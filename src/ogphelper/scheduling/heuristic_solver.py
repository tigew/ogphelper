"""Heuristic solver for schedule optimization.

This module implements a greedy heuristic approach to:
1. Select shifts to maximize coverage
2. Place lunches to minimize coverage gaps
3. Place breaks to reduce operational impact
4. Assign job roles respecting caps and constraints
"""

from dataclasses import dataclass, field
from typing import Optional

from ogphelper.domain.models import (
    Associate,
    DaySchedule,
    JobAssignment,
    JobRole,
    Preference,
    ScheduleBlock,
    ScheduleRequest,
    ShiftAssignment,
)
from ogphelper.domain.policies import (
    BreakPolicy,
    DefaultBreakPolicy,
    DefaultLunchPolicy,
    LunchPolicy,
)
from ogphelper.scheduling.candidate_generator import CandidateGenerator, ShiftCandidate


@dataclass
class SlotState:
    """Tracks state of a single time slot during scheduling."""

    on_floor_count: int = 0
    on_lunch_count: int = 0
    on_break_count: int = 0
    role_counts: dict[JobRole, int] = field(
        default_factory=lambda: {role: 0 for role in JobRole}
    )

    @property
    def total_scheduled(self) -> int:
        """Total associates scheduled for this slot."""
        return self.on_floor_count + self.on_lunch_count + self.on_break_count


class HeuristicSolver:
    """Greedy heuristic solver for schedule generation.

    The solver follows this approach:
    1. Generate candidates for all associates
    2. Select shifts greedily to maximize coverage
    3. Place lunches to minimize simultaneous lunches
    4. Place breaks to avoid clustering
    5. Assign roles respecting caps and eligibility
    """

    def __init__(
        self,
        lunch_policy: Optional[LunchPolicy] = None,
        break_policy: Optional[BreakPolicy] = None,
    ):
        self.lunch_policy = lunch_policy or DefaultLunchPolicy()
        self.break_policy = break_policy or DefaultBreakPolicy()

    def solve(
        self,
        request: ScheduleRequest,
        candidates: dict[str, list[ShiftCandidate]],
        associates_map: dict[str, Associate],
    ) -> DaySchedule:
        """Generate a complete schedule using heuristic approach.

        Args:
            request: Schedule request with constraints.
            candidates: Pre-generated candidates per associate.
            associates_map: Dict mapping associate IDs to Associate objects.

        Returns:
            Complete DaySchedule with all assignments.
        """
        schedule = DaySchedule(
            schedule_date=request.schedule_date,
            slot_minutes=request.slot_minutes,
            day_start_minutes=request.day_start_minutes,
            day_end_minutes=request.day_end_minutes,
        )

        # Track slot states for optimization
        slot_states = [SlotState() for _ in range(request.total_slots)]

        # Step 1: Select shifts
        selected_shifts = self._select_shifts(
            candidates, slot_states, request.total_slots
        )

        # Step 2-4: For each selected shift, place lunch, breaks, and assign roles
        for candidate in selected_shifts:
            associate = associates_map[candidate.associate_id]

            # Create base assignment
            assignment = ShiftAssignment(
                associate_id=candidate.associate_id,
                schedule_date=request.schedule_date,
                shift_start_slot=candidate.start_slot,
                shift_end_slot=candidate.end_slot,
                slot_minutes=candidate.slot_minutes,
            )

            # Place lunch if needed
            if candidate.lunch_slots > 0:
                lunch_block = self._place_lunch(
                    candidate, slot_states, request.is_busy_day
                )
                assignment.lunch_block = lunch_block
                # Update slot states for lunch
                for slot in range(lunch_block.start_slot, lunch_block.end_slot):
                    slot_states[slot].on_lunch_count += 1
                    slot_states[slot].on_floor_count -= 1

            # Place breaks if needed
            if candidate.break_count > 0:
                break_blocks = self._place_breaks(
                    candidate, assignment.lunch_block, slot_states
                )
                assignment.break_blocks = break_blocks
                # Update slot states for breaks
                for break_block in break_blocks:
                    for slot in range(break_block.start_slot, break_block.end_slot):
                        slot_states[slot].on_break_count += 1
                        slot_states[slot].on_floor_count -= 1

            # Assign job roles
            job_assignments = self._assign_roles(
                candidate, assignment, associate, slot_states, request.job_caps
            )
            assignment.job_assignments = job_assignments

            schedule.assignments[candidate.associate_id] = assignment

        return schedule

    def _select_shifts(
        self,
        candidates: dict[str, list[ShiftCandidate]],
        slot_states: list[SlotState],
        total_slots: int,
    ) -> list[ShiftCandidate]:
        """Select one shift per associate to maximize coverage.

        Uses a greedy approach: for each associate, pick the shift that
        contributes most to overall coverage (especially in low-coverage slots).
        """
        selected = []

        # Sort associates by number of candidates (fewer first - more constrained)
        sorted_associates = sorted(candidates.keys(), key=lambda a: len(candidates[a]))

        for assoc_id in sorted_associates:
            assoc_candidates = candidates[assoc_id]
            if not assoc_candidates:
                continue

            # Score each candidate based on coverage contribution
            best_candidate = None
            best_score = float("-inf")

            for candidate in assoc_candidates:
                score = self._score_shift(candidate, slot_states)
                if score > best_score:
                    best_score = score
                    best_candidate = candidate

            if best_candidate:
                selected.append(best_candidate)
                # Update slot states (initially all on floor)
                for slot in range(best_candidate.start_slot, best_candidate.end_slot):
                    slot_states[slot].on_floor_count += 1

        return selected

    def _score_shift(
        self,
        candidate: ShiftCandidate,
        slot_states: list[SlotState],
    ) -> float:
        """Score a shift candidate based on coverage contribution.

        Higher scores are better. Shifts that cover low-coverage slots
        get bonus points.
        """
        score = 0.0

        for slot in range(candidate.start_slot, candidate.end_slot):
            current_coverage = slot_states[slot].on_floor_count

            # Bonus for low-coverage slots (diminishing returns for high coverage)
            if current_coverage == 0:
                score += 10.0  # High value for uncovered slots
            elif current_coverage < 3:
                score += 5.0
            elif current_coverage < 5:
                score += 2.0
            else:
                score += 1.0

        # Slight preference for longer shifts (more flexibility for lunch/breaks)
        score += candidate.work_minutes / 100.0

        return score

    def _place_lunch(
        self,
        candidate: ShiftCandidate,
        slot_states: list[SlotState],
        is_busy_day: bool,
    ) -> ScheduleBlock:
        """Place lunch to minimize coverage impact.

        Tries to place lunch when other associates are also on lunch
        or when coverage is highest.
        """
        lunch_slots = candidate.lunch_slots
        slot_minutes = candidate.slot_minutes

        # Get allowed window from policy
        earliest, latest = self.lunch_policy.get_lunch_window(
            candidate.start_slot,
            candidate.end_slot,
            lunch_slots,
            is_busy_day,
            slot_minutes,
        )

        # Find best position within window
        best_start = earliest
        best_score = float("-inf")

        for start in range(earliest, latest + 1):
            end = start + lunch_slots
            if end > candidate.end_slot:
                break

            score = self._score_lunch_position(start, end, slot_states)
            if score > best_score:
                best_score = score
                best_start = start

        return ScheduleBlock(best_start, best_start + lunch_slots, slot_minutes)

    def _score_lunch_position(
        self,
        start: int,
        end: int,
        slot_states: list[SlotState],
    ) -> float:
        """Score a lunch position. Higher is better.

        Prefers positions where:
        - Coverage is high (less impact when one person leaves)
        - Others are already on lunch (coordinated lunches)
        """
        score = 0.0

        for slot in range(start, end):
            coverage = slot_states[slot].on_floor_count
            lunch_count = slot_states[slot].on_lunch_count

            # Prefer high coverage slots
            score += coverage * 0.5

            # Small bonus for coordinating with existing lunches
            score += lunch_count * 0.2

        return score

    def _place_breaks(
        self,
        candidate: ShiftCandidate,
        lunch_block: Optional[ScheduleBlock],
        slot_states: list[SlotState],
    ) -> list[ScheduleBlock]:
        """Place breaks to minimize operational impact.

        Uses policy targets (1/3 and 2/3 points) as starting points,
        then adjusts based on current coverage.
        """
        break_count = candidate.break_count
        break_duration = self.break_policy.get_break_duration()
        break_slots = break_duration // candidate.slot_minutes

        lunch_start = lunch_block.start_slot if lunch_block else None
        lunch_end = lunch_block.end_slot if lunch_block else None

        # Get target positions from policy
        targets = self.break_policy.get_break_target_positions(
            candidate.start_slot,
            candidate.end_slot,
            break_count,
            lunch_start,
            lunch_end,
            candidate.slot_minutes,
        )

        breaks = []
        used_slots = set()

        # Add lunch slots to used set
        if lunch_block:
            for slot in range(lunch_block.start_slot, lunch_block.end_slot):
                used_slots.add(slot)

        for target in targets:
            # Find best position near target
            best_start = self._find_best_break_position(
                target,
                break_slots,
                candidate.start_slot,
                candidate.end_slot,
                used_slots,
                slot_states,
            )

            break_block = ScheduleBlock(
                best_start, best_start + break_slots, candidate.slot_minutes
            )
            breaks.append(break_block)

            # Mark slots as used
            for slot in range(best_start, best_start + break_slots):
                used_slots.add(slot)

        return breaks

    def _find_best_break_position(
        self,
        target: int,
        break_slots: int,
        shift_start: int,
        shift_end: int,
        used_slots: set[int],
        slot_states: list[SlotState],
        search_radius: int = 8,
    ) -> int:
        """Find best break position near target slot."""
        best_start = target
        best_score = float("-inf")

        for offset in range(-search_radius, search_radius + 1):
            start = target + offset
            end = start + break_slots

            # Check bounds
            if start < shift_start or end > shift_end:
                continue

            # Check for conflicts
            conflict = False
            for slot in range(start, end):
                if slot in used_slots:
                    conflict = True
                    break
            if conflict:
                continue

            # Score this position
            score = 0.0
            for slot in range(start, end):
                # Prefer high coverage (less impact)
                score += slot_states[slot].on_floor_count
                # Small penalty for distance from target
                score -= abs(offset) * 0.1

            if score > best_score:
                best_score = score
                best_start = start

        return best_start

    def _assign_roles(
        self,
        candidate: ShiftCandidate,
        assignment: ShiftAssignment,
        associate: Associate,
        slot_states: list[SlotState],
        job_caps: dict[JobRole, int],
    ) -> list[JobAssignment]:
        """Assign job roles for each work period in the shift.

        Strategy:
        1. Fill constrained roles first (GMD, Exception, Staging, Backroom)
        2. Assign Picking as overflow
        3. Respect caps and eligibility
        """
        eligible_roles = associate.eligible_roles()
        if not eligible_roles:
            return []

        # Build list of work periods (excluding lunch and breaks)
        work_periods = self._get_work_periods(candidate, assignment)

        assignments = []

        for period in work_periods:
            # For simplicity, assign one role per work period
            role = self._select_role_for_period(
                period, eligible_roles, associate, slot_states, job_caps
            )
            if role:
                assignments.append(JobAssignment(role=role, block=period))
                # Update slot states
                for slot in range(period.start_slot, period.end_slot):
                    slot_states[slot].role_counts[role] += 1

        return assignments

    def _get_work_periods(
        self,
        candidate: ShiftCandidate,
        assignment: ShiftAssignment,
    ) -> list[ScheduleBlock]:
        """Get contiguous work periods (excluding lunch and breaks)."""
        # Collect all off-floor blocks
        off_blocks = []
        if assignment.lunch_block:
            off_blocks.append(
                (assignment.lunch_block.start_slot, assignment.lunch_block.end_slot)
            )
        for break_block in assignment.break_blocks:
            off_blocks.append((break_block.start_slot, break_block.end_slot))

        # Sort by start time
        off_blocks.sort()

        # Build work periods
        periods = []
        current_start = candidate.start_slot

        for off_start, off_end in off_blocks:
            if current_start < off_start:
                periods.append(
                    ScheduleBlock(current_start, off_start, candidate.slot_minutes)
                )
            current_start = off_end

        # Final period after last break/lunch
        if current_start < candidate.end_slot:
            periods.append(
                ScheduleBlock(current_start, candidate.end_slot, candidate.slot_minutes)
            )

        return periods

    def _select_role_for_period(
        self,
        period: ScheduleBlock,
        eligible_roles: set[JobRole],
        associate: Associate,
        slot_states: list[SlotState],
        job_caps: dict[JobRole, int],
    ) -> Optional[JobRole]:
        """Select best role for a work period.

        Priority:
        1. Constrained roles that need staffing (under cap)
        2. Preferred roles
        3. Neutral roles
        4. Avoid roles only if necessary
        """
        # Priority order for constrained roles
        constrained_priority = [
            JobRole.GMD_SM,
            JobRole.EXCEPTION_SM,
            JobRole.STAGING,
            JobRole.BACKROOM,
        ]

        # Check if any constrained role needs staffing
        for role in constrained_priority:
            if role not in eligible_roles:
                continue

            # Check if we can assign this role (under cap for all slots)
            can_assign = True
            for slot in range(period.start_slot, period.end_slot):
                if slot_states[slot].role_counts[role] >= job_caps.get(role, 999):
                    can_assign = False
                    break

            if can_assign:
                # Check preference - don't force avoid roles for constrained
                pref = associate.get_preference(role)
                if pref != Preference.AVOID:
                    return role

        # Fall back to Picking or preferred roles
        if JobRole.PICKING in eligible_roles:
            return JobRole.PICKING

        # Last resort: any eligible role
        for role in eligible_roles:
            can_assign = True
            for slot in range(period.start_slot, period.end_slot):
                if slot_states[slot].role_counts[role] >= job_caps.get(role, 999):
                    can_assign = False
                    break
            if can_assign:
                return role

        return None
