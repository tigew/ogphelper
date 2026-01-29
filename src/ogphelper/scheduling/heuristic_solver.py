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
    ShiftBlockConfig,
    ShiftBlockType,
    ShiftStartConfig,
    SlotRangeCaps,
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
    lunch_start_count: int = 0  # How many lunches START at this exact slot
    role_counts: dict[JobRole, int] = field(
        default_factory=lambda: {role: 0 for role in JobRole}
    )

    @property
    def total_scheduled(self) -> int:
        """Total associates scheduled for this slot."""
        return self.on_floor_count + self.on_lunch_count + self.on_break_count


@dataclass
class ShiftBlockState:
    """Tracks how many associates have been assigned to start in each shift block."""

    counts: dict[ShiftBlockType, int] = field(
        default_factory=lambda: {block_type: 0 for block_type in ShiftBlockType}
    )

    def get_count(self, block_type: ShiftBlockType) -> int:
        """Get current count for a block type."""
        return self.counts.get(block_type, 0)

    def increment(self, block_type: ShiftBlockType) -> None:
        """Increment count for a block type."""
        self.counts[block_type] = self.counts.get(block_type, 0) + 1


@dataclass
class ShiftStartState:
    """Tracks how many associates have been assigned to start at each specific time."""

    counts: dict[int, int] = field(default_factory=dict)  # slot -> count

    def get_count(self, slot: int) -> int:
        """Get current count for a start slot."""
        return self.counts.get(slot, 0)

    def increment(self, slot: int) -> None:
        """Increment count for a start slot."""
        self.counts[slot] = self.counts.get(slot, 0) + 1


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

        # Track shift block assignments for capacity limits
        block_state = ShiftBlockState()

        # Track shift start assignments for granular time-based limits
        start_state = ShiftStartState()

        # Step 1: Select shifts (with shift block and start time enforcement)
        selected_shifts = self._select_shifts(
            candidates,
            slot_states,
            request.total_slots,
            request.shift_block_configs,
            block_state,
            request.shift_start_configs,
            start_state,
        )

        # Re-sort selected shifts by start time for role assignment
        # This ensures earlier starters (5AM) get specialized roles first,
        # enabling proper ramping (1 new per hour) for GMD/SM, Exception/SM, etc.
        selected_shifts.sort(key=lambda c: c.start_slot)

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
                    candidate, slot_states, request.is_busy_day, request.day_start_minutes
                )
                assignment.lunch_block = lunch_block
                # Update slot states for lunch
                for slot in range(lunch_block.start_slot, lunch_block.end_slot):
                    slot_states[slot].on_lunch_count += 1
                    slot_states[slot].on_floor_count -= 1
                # Track lunch START position specifically for staggering
                slot_states[lunch_block.start_slot].lunch_start_count += 1

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
                candidate, assignment, associate, slot_states, request.job_caps,
                request.slot_range_caps
            )
            assignment.job_assignments = job_assignments

            schedule.assignments[candidate.associate_id] = assignment

        return schedule

    def _select_shifts(
        self,
        candidates: dict[str, list[ShiftCandidate]],
        slot_states: list[SlotState],
        total_slots: int,
        shift_block_configs: Optional[list[ShiftBlockConfig]] = None,
        block_state: Optional[ShiftBlockState] = None,
        shift_start_configs: Optional[list[ShiftStartConfig]] = None,
        start_state: Optional[ShiftStartState] = None,
    ) -> list[ShiftCandidate]:
        """Select one shift per associate to maximize coverage.

        Uses a greedy approach: for each associate, pick the shift that
        contributes most to overall coverage (especially in low-coverage slots).
        Enforces shift block and start time capacity limits if configured.
        """
        selected = []

        # Build a quick lookup for shift blocks by slot
        block_by_slot: dict[int, ShiftBlockConfig] = {}
        if shift_block_configs:
            for block in shift_block_configs:
                for slot in range(block.start_slot, block.end_slot):
                    block_by_slot[slot] = block

        # Build a quick lookup for shift start configs by slot
        start_config_by_slot: dict[int, ShiftStartConfig] = {}
        if shift_start_configs:
            for cfg in shift_start_configs:
                start_config_by_slot[cfg.start_slot] = cfg

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
                # Check shift block capacity limit
                if shift_block_configs and block_state:
                    block = block_by_slot.get(candidate.start_slot)
                    if block:
                        current_count = block_state.get_count(block.block_type)
                        if current_count >= block.max_associates:
                            # This block is at capacity, skip this candidate
                            continue

                # Check shift start time capacity limit
                if shift_start_configs and start_state:
                    start_cfg = start_config_by_slot.get(candidate.start_slot)
                    if start_cfg and start_cfg.max_count is not None:
                        current_count = start_state.get_count(candidate.start_slot)
                        if current_count >= start_cfg.max_count:
                            # This start time is at capacity, skip this candidate
                            continue

                score = self._score_shift(candidate, slot_states)

                # Add bonus/penalty based on shift block targets
                if shift_block_configs and block_state:
                    block = block_by_slot.get(candidate.start_slot)
                    if block and block.target_associates is not None:
                        current_count = block_state.get_count(block.block_type)
                        if current_count < block.target_associates:
                            # Bonus for filling under-target blocks
                            score += 5.0 * (block.target_associates - current_count)

                # Add bonus/penalty based on shift start time targets
                if shift_start_configs and start_state:
                    start_cfg = start_config_by_slot.get(candidate.start_slot)
                    if start_cfg:
                        current_count = start_state.get_count(candidate.start_slot)
                        if current_count < start_cfg.target_count:
                            # Strong bonus for filling under-target start times
                            score += 10.0 * (start_cfg.target_count - current_count)

                if score > best_score:
                    best_score = score
                    best_candidate = candidate

            if best_candidate:
                selected.append(best_candidate)
                # Update slot states (initially all on floor)
                for slot in range(best_candidate.start_slot, best_candidate.end_slot):
                    slot_states[slot].on_floor_count += 1

                # Update shift block state
                if shift_block_configs and block_state:
                    block = block_by_slot.get(best_candidate.start_slot)
                    if block:
                        block_state.increment(block.block_type)

                # Update shift start state
                if shift_start_configs and start_state:
                    start_state.increment(best_candidate.start_slot)

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
        day_start_minutes: int = 300,  # Default 5AM
    ) -> ScheduleBlock:
        """Place lunch with consistent 15-minute staggering across all associates.

        Uses lunch_start_count to track exactly where lunches BEGIN, ensuring
        even distribution across 15-minute intervals. This prevents clustering
        where many associates have lunches starting at the same time.
        """
        lunch_slots = candidate.lunch_slots
        slot_minutes = candidate.slot_minutes

        # Calculate early-starter cutoff: before 8AM (480 minutes from midnight)
        early_cutoff_minutes = 480  # 8AM
        early_cutoff_slot = (early_cutoff_minutes - day_start_minutes) // slot_minutes

        # Get allowed window from policy
        earliest, latest = self.lunch_policy.get_lunch_window(
            candidate.start_slot,
            candidate.end_slot,
            lunch_slots,
            is_busy_day,
            slot_minutes,
        )

        # Calculate target (roughly 4 hours into shift for 8-hour shifts)
        shift_length = candidate.end_slot - candidate.start_slot
        mid_point = candidate.start_slot + shift_length // 2
        target = mid_point - lunch_slots // 2

        # For early starters (before 8AM), don't allow lunches before target
        # This prevents 8AM lunches for 5AM starters - they should start at 9AM
        if candidate.start_slot < early_cutoff_slot:
            loop_start = target
        else:
            loop_start = earliest

        # Find best position using 15-minute (1-slot) staggering
        # Primary criterion: fewest lunches starting at this exact slot
        # Secondary criterion: lower overlap with existing lunches
        # Tertiary criterion: closer to target time
        best_start = loop_start
        best_score = float("-inf")

        for start in range(loop_start, latest + 1):
            end = start + lunch_slots
            if end > candidate.end_slot:
                break

            # Primary: strongly prefer slots with fewer lunches STARTING here
            # This ensures true 15-minute staggering
            lunches_starting_here = slot_states[start].lunch_start_count
            start_score = -lunches_starting_here * 100.0

            # Secondary: slight preference for lower overlap (coverage consideration)
            overlap_count = sum(
                slot_states[slot].on_lunch_count for slot in range(start, end)
            )
            overlap_score = -overlap_count * 1.0

            # Tertiary: slight preference for being closer to target
            distance_from_target = abs(start - target)
            distance_score = -distance_from_target * 0.5

            score = start_score + overlap_score + distance_score

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
        - Fewer others are already on lunch (stagger lunches for coverage)
        """
        score = 0.0

        for slot in range(start, end):
            coverage = slot_states[slot].on_floor_count
            lunch_count = slot_states[slot].on_lunch_count

            # Prefer high coverage slots
            score += coverage * 0.5

            # Penalize slots where many are already on lunch
            # This ensures lunches are staggered across associates
            score -= lunch_count * 3.0

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

        # Get max variance from policy (default 2 slots = 30 min)
        max_variance = self.break_policy.get_max_break_variance_slots()

        # Add lunch slots to used set
        if lunch_block:
            for slot in range(lunch_block.start_slot, lunch_block.end_slot):
                used_slots.add(slot)

        for target in targets:
            # Find best position near target, limited to max variance
            best_start = self._find_best_break_position(
                target,
                break_slots,
                candidate.start_slot,
                candidate.end_slot,
                used_slots,
                slot_states,
                search_radius=max_variance,
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
            # Balance distance from target with break distribution
            score = 0.0
            for slot in range(start, end):
                # Prefer high coverage (less impact when taking break)
                score += slot_states[slot].on_floor_count * 0.1
                # Strong penalty for slots where others are already on break
                # This ensures breaks are staggered across associates
                score -= slot_states[slot].on_break_count * 5.0
            # Moderate penalty for distance from target (allows spreading)
            score -= abs(offset) * 2.0

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
        slot_range_caps: Optional[list[SlotRangeCaps]] = None,
    ) -> list[JobAssignment]:
        """Assign job roles for each work period in the shift.

        Strategy:
        1. Fill constrained roles first (GMD, Exception, Staging, Backroom)
        2. Assign Picking as overflow
        3. Respect caps and eligibility
        4. Use slot-specific caps when available (e.g., 5AM staffing)
        5. For specialized roles (GMD/SM, Exception/SM, S/R, Backroom),
           preserve the role throughout the entire shift once assigned.
           These roles require area-specific knowledge and switching
           between them and picking is disruptive.
        """
        eligible_roles = associate.eligible_roles()
        if not eligible_roles:
            return []

        # Build list of work periods (excluding lunch and breaks)
        work_periods = self._get_work_periods(candidate, assignment)

        assignments = []

        # Roles that should persist throughout the shift once assigned
        # (switching between these and picking is disruptive)
        persistent_roles = {
            JobRole.GMD_SM,
            JobRole.EXCEPTION_SM,
            JobRole.SR,
            JobRole.BACKROOM,
        }

        # 5AM starters (slot < 4) are "extra special" - they keep their initial
        # role ALL DAY, even if it's Picking. This is because 5AM has very limited
        # specialized role slots, so those who start as pickers must stay pickers.
        is_5am_starter = candidate.start_slot < 4

        initial_role: Optional[JobRole] = None

        for period in work_periods:
            role: Optional[JobRole] = None

            # If we have an initial role that should persist, try to preserve it
            # - 5AM starters: preserve ANY role (including Picking)
            # - Other starters: only preserve specialized roles
            if initial_role is not None:
                should_persist = is_5am_starter or initial_role in persistent_roles
                if should_persist:
                    role = self._try_preserve_role(
                        initial_role, period, eligible_roles, slot_states,
                        job_caps, slot_range_caps
                    )

            # If not preserving (or couldn't preserve), select normally
            if role is None:
                role = self._select_role_for_period(
                    period, eligible_roles, associate, slot_states, job_caps,
                    slot_range_caps
                )

            if role:
                assignments.append(JobAssignment(role=role, block=period))
                # Update slot states
                for slot in range(period.start_slot, period.end_slot):
                    slot_states[slot].role_counts[role] += 1

                # Track initial role for persistence
                if initial_role is None:
                    initial_role = role

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

    def _get_cap_for_slot(
        self,
        slot: int,
        role: JobRole,
        job_caps: dict[JobRole, int],
        slot_range_caps: Optional[list[SlotRangeCaps]] = None,
    ) -> int:
        """Get the job cap for a role at a specific slot.

        Uses slot-specific caps if available, otherwise falls back to global caps.
        """
        if slot_range_caps:
            for caps in slot_range_caps:
                if caps.contains_slot(slot):
                    return caps.get_cap(role)
        return job_caps.get(role, 999)

    def _try_preserve_role(
        self,
        role: JobRole,
        period: ScheduleBlock,
        eligible_roles: set[JobRole],
        slot_states: list[SlotState],
        job_caps: dict[JobRole, int],
        slot_range_caps: Optional[list[SlotRangeCaps]] = None,
    ) -> Optional[JobRole]:
        """Try to preserve a specific role for a work period.

        Used for 5AM starters to maintain their initial job assignment
        throughout the shift. Returns the role if it can be assigned,
        or None if capacity constraints prevent it.
        """
        if role not in eligible_roles:
            return None

        # Check if we can assign this role (under cap for all slots)
        for slot in range(period.start_slot, period.end_slot):
            cap = self._get_cap_for_slot(slot, role, job_caps, slot_range_caps)
            if slot_states[slot].role_counts[role] >= cap:
                return None

        return role

    def _select_role_for_period(
        self,
        period: ScheduleBlock,
        eligible_roles: set[JobRole],
        associate: Associate,
        slot_states: list[SlotState],
        job_caps: dict[JobRole, int],
        slot_range_caps: Optional[list[SlotRangeCaps]] = None,
    ) -> Optional[JobRole]:
        """Select best role for a work period.

        Priority:
        1. Constrained roles that need staffing (under cap)
        2. Preferred roles
        3. Neutral roles
        4. Avoid roles only if necessary

        Uses slot-specific caps when available (e.g., 5AM has different staffing).
        """
        # Priority order for constrained roles
        constrained_priority = [
            JobRole.GMD_SM,
            JobRole.EXCEPTION_SM,
            JobRole.STAGING,
            JobRole.BACKROOM,
            JobRole.SR,
        ]

        # Check if any constrained role needs staffing
        for role in constrained_priority:
            if role not in eligible_roles:
                continue

            # Check if we can assign this role (under cap for all slots)
            can_assign = True
            for slot in range(period.start_slot, period.end_slot):
                cap = self._get_cap_for_slot(slot, role, job_caps, slot_range_caps)
                if slot_states[slot].role_counts[role] >= cap:
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
                cap = self._get_cap_for_slot(slot, role, job_caps, slot_range_caps)
                if slot_states[slot].role_counts[role] >= cap:
                    can_assign = False
                    break
            if can_assign:
                return role

        return None
