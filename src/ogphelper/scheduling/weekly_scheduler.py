"""Weekly scheduler for multi-day schedule generation.

This module provides the WeeklyScheduler class that orchestrates scheduling
across multiple days with support for:
- Weekly hour tracking and enforcement
- Fairness balancing between associates
- Days-off pattern enforcement
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from ogphelper.domain.models import (
    Associate,
    Availability,
    DaySchedule,
    DaysOffPattern,
    FairnessConfig,
    FairnessMetrics,
    JobRole,
    ScheduleRequest,
    ShiftAssignment,
    WeeklySchedule,
    WeeklyScheduleRequest,
)
from ogphelper.domain.policies import (
    BreakPolicy,
    DefaultBreakPolicy,
    DefaultLunchPolicy,
    DefaultShiftPolicy,
    LunchPolicy,
    ShiftPolicy,
)
from ogphelper.scheduling.candidate_generator import CandidateGenerator, ShiftCandidate
from ogphelper.scheduling.heuristic_solver import HeuristicSolver


@dataclass
class AssociateWeeklyState:
    """Tracks an associate's state throughout the week for scheduling decisions.

    Attributes:
        associate_id: ID of the associate.
        minutes_scheduled: Total work minutes scheduled so far this week.
        days_worked: List of dates the associate is scheduled to work.
        days_off: List of dates the associate has off.
        max_weekly_minutes: Maximum allowed weekly minutes.
        remaining_minutes: Minutes still available for scheduling.
    """

    associate_id: str
    minutes_scheduled: int = 0
    days_worked: list[date] = field(default_factory=list)
    days_off: list[date] = field(default_factory=list)
    max_weekly_minutes: int = 2400

    @property
    def remaining_minutes(self) -> int:
        """Minutes still available for scheduling this week."""
        return max(0, self.max_weekly_minutes - self.minutes_scheduled)

    def add_shift(self, schedule_date: date, work_minutes: int) -> None:
        """Record a shift being added."""
        self.minutes_scheduled += work_minutes
        if schedule_date not in self.days_worked:
            self.days_worked.append(schedule_date)

    def add_day_off(self, schedule_date: date) -> None:
        """Record a day off."""
        if schedule_date not in self.days_off:
            self.days_off.append(schedule_date)


class DaysOffPatternEnforcer:
    """Enforces days-off patterns for weekly scheduling.

    This class determines which days associates should have off based on
    the configured pattern and their current schedule state.
    """

    def __init__(
        self,
        pattern: DaysOffPattern,
        required_days_off: int = 2,
    ):
        self.pattern = pattern
        self.required_days_off = required_days_off

    def should_be_day_off(
        self,
        associate_state: AssociateWeeklyState,
        schedule_date: date,
        remaining_dates: list[date],
        all_dates: list[date],
    ) -> bool:
        """Determine if a date should be a day off for an associate.

        Args:
            associate_state: Current weekly state of the associate.
            schedule_date: Date being considered.
            remaining_dates: Dates not yet scheduled (including current).
            all_dates: All dates in the scheduling period.

        Returns:
            True if this should be a day off, False otherwise.
        """
        if self.pattern == DaysOffPattern.NONE:
            return False

        days_worked = len(associate_state.days_worked)
        days_off = len(associate_state.days_off)
        total_days = len(all_dates)
        remaining_count = len(remaining_dates)

        # Check if we need to guarantee remaining days off
        days_off_needed = self.required_days_off - days_off
        if days_off_needed > 0 and remaining_count <= days_off_needed:
            # Must take this day off to meet minimum
            return True

        if self.pattern == DaysOffPattern.TWO_CONSECUTIVE:
            return self._check_two_consecutive(
                associate_state, schedule_date, remaining_dates, all_dates
            )
        elif self.pattern == DaysOffPattern.ONE_WEEKEND_DAY:
            return self._check_weekend_day(
                associate_state, schedule_date, remaining_dates, all_dates
            )
        elif self.pattern == DaysOffPattern.EVERY_OTHER_DAY:
            return self._check_every_other(
                associate_state, schedule_date, remaining_dates, all_dates
            )

        return False

    def _check_two_consecutive(
        self,
        state: AssociateWeeklyState,
        schedule_date: date,
        remaining_dates: list[date],
        all_dates: list[date],
    ) -> bool:
        """Check if date should be off to satisfy two consecutive days pattern."""
        days_off = state.days_off
        days_off_count = len(days_off)

        # If we already have 2+ days off, check if they're consecutive
        if days_off_count >= 2:
            # Check if any two are consecutive
            sorted_off = sorted(days_off)
            for i in range(len(sorted_off) - 1):
                if (sorted_off[i + 1] - sorted_off[i]).days == 1:
                    return False  # Already have consecutive days off
            # Need to add more consecutive days
            # Check if this date is adjacent to an existing day off
            for off_day in days_off:
                if abs((schedule_date - off_day).days) == 1:
                    return True  # This would create consecutive days

        # If we have 1 day off, check if this makes it consecutive
        if days_off_count == 1:
            existing_off = days_off[0]
            if abs((schedule_date - existing_off).days) == 1:
                return True  # This makes consecutive days off

        # If no days off yet, need to plan ahead
        if days_off_count == 0:
            remaining_count = len(remaining_dates)
            # If only 2 remaining days, both should be off
            if remaining_count <= 2:
                return True

        return False

    def _check_weekend_day(
        self,
        state: AssociateWeeklyState,
        schedule_date: date,
        remaining_dates: list[date],
        all_dates: list[date],
    ) -> bool:
        """Check if date should be off to satisfy weekend day pattern."""
        # Check if any existing day off is a weekend
        has_weekend_off = any(d.weekday() >= 5 for d in state.days_off)
        if has_weekend_off:
            return False

        # If this is a weekend day and we don't have one yet
        if schedule_date.weekday() >= 5:
            # Check if there are more weekends coming
            remaining_weekends = [d for d in remaining_dates if d.weekday() >= 5]
            if len(remaining_weekends) == 1:
                return True  # Last weekend day, must take off

        return False

    def _check_every_other(
        self,
        state: AssociateWeeklyState,
        schedule_date: date,
        remaining_dates: list[date],
        all_dates: list[date],
    ) -> bool:
        """Check if date should be off to avoid working consecutive days."""
        # Check if worked yesterday
        yesterday = schedule_date - timedelta(days=1)
        if yesterday in state.days_worked:
            return True  # Can't work two consecutive days

        return False

    def get_planned_days_off(
        self,
        associate_state: AssociateWeeklyState,
        remaining_dates: list[date],
        all_dates: list[date],
    ) -> list[date]:
        """Get a list of dates that should be days off.

        This is used for planning ahead when scheduling.
        """
        planned_off = list(associate_state.days_off)

        for d in remaining_dates:
            if self.should_be_day_off(associate_state, d, remaining_dates, all_dates):
                if d not in planned_off:
                    planned_off.append(d)
                    # Update state temporarily to plan correctly
                    temp_days_off = planned_off.copy()
                    continue

        return planned_off


class FairnessBalancer:
    """Balances work hours fairly among associates.

    This class adjusts scheduling decisions to ensure equitable distribution
    of hours while respecting individual constraints.
    """

    def __init__(self, config: FairnessConfig):
        self.config = config

    def calculate_priority_score(
        self,
        associate_id: str,
        state: AssociateWeeklyState,
        all_states: dict[str, AssociateWeeklyState],
        schedule_date: date,
    ) -> float:
        """Calculate scheduling priority for an associate.

        Higher scores indicate the associate should be prioritized for
        scheduling to maintain fairness.

        Args:
            associate_id: ID of the associate.
            state: Current weekly state of the associate.
            all_states: States of all associates.
            schedule_date: Date being scheduled.

        Returns:
            Priority score (higher = higher priority for scheduling).
        """
        if not all_states:
            return 0.0

        # Calculate average hours scheduled so far
        all_minutes = [s.minutes_scheduled for s in all_states.values()]
        avg_minutes = sum(all_minutes) / len(all_minutes) if all_minutes else 0

        # Calculate average days worked
        all_days = [len(s.days_worked) for s in all_states.values()]
        avg_days = sum(all_days) / len(all_days) if all_days else 0

        score = 0.0

        # Hours component: prioritize those with fewer hours
        hours_deficit = avg_minutes - state.minutes_scheduled
        score += hours_deficit * self.config.weight_hours_balance

        # Days component: prioritize those with fewer days
        days_deficit = avg_days - len(state.days_worked)
        score += days_deficit * 60 * self.config.weight_days_balance  # Convert to minutes scale

        # Bonus for associates far below average (catch-up priority)
        if state.minutes_scheduled < avg_minutes * 0.8:
            score += 100.0

        return score

    def adjust_candidate_scores(
        self,
        candidates: list[ShiftCandidate],
        state: AssociateWeeklyState,
        all_states: dict[str, AssociateWeeklyState],
        base_scores: list[float],
    ) -> list[float]:
        """Adjust candidate scores based on fairness considerations.

        Args:
            candidates: List of shift candidates.
            state: Current state of the associate.
            all_states: States of all associates.
            base_scores: Base scores from coverage optimization.

        Returns:
            Adjusted scores incorporating fairness.
        """
        if not candidates or not all_states:
            return base_scores

        all_minutes = [s.minutes_scheduled for s in all_states.values()]
        avg_minutes = sum(all_minutes) / len(all_minutes) if all_minutes else 0

        adjusted = []
        for candidate, base_score in zip(candidates, base_scores):
            adjustment = 0.0

            # If associate is behind on hours, prefer longer shifts
            if state.minutes_scheduled < avg_minutes:
                deficit_ratio = (avg_minutes - state.minutes_scheduled) / max(avg_minutes, 1)
                adjustment += candidate.work_minutes * deficit_ratio * 0.1

            # If associate is ahead on hours, prefer shorter shifts
            elif state.minutes_scheduled > avg_minutes * 1.2:
                excess_ratio = (state.minutes_scheduled - avg_minutes) / max(avg_minutes, 1)
                adjustment -= candidate.work_minutes * excess_ratio * 0.05

            adjusted.append(base_score + adjustment)

        return adjusted

    def should_skip_associate(
        self,
        state: AssociateWeeklyState,
        all_states: dict[str, AssociateWeeklyState],
        remaining_days: int,
    ) -> bool:
        """Determine if an associate should be skipped for fairness.

        Args:
            state: Current state of the associate.
            all_states: States of all associates.
            remaining_days: Number of days remaining in the week.

        Returns:
            True if associate should be skipped, False otherwise.
        """
        if not all_states or remaining_days <= 0:
            return False

        all_minutes = [s.minutes_scheduled for s in all_states.values()]
        avg_minutes = sum(all_minutes) / len(all_minutes) if all_minutes else 0

        # Skip if significantly ahead on hours and others need catch-up time
        if state.minutes_scheduled > avg_minutes + self.config.max_hours_variance:
            # Check if any associate is significantly behind
            min_minutes = min(all_minutes)
            if avg_minutes - min_minutes > self.config.max_hours_variance / 2:
                return True

        return False


class WeeklyScheduler:
    """High-level scheduler for generating weekly schedules.

    The WeeklyScheduler coordinates daily scheduling across a week while
    enforcing weekly hour limits, fairness balancing, and days-off patterns.

    Example:
        >>> scheduler = WeeklyScheduler()
        >>> request = WeeklyScheduleRequest(
        ...     start_date=date(2024, 1, 15),
        ...     end_date=date(2024, 1, 21),
        ...     associates=[associate1, associate2, ...]
        ... )
        >>> schedule = scheduler.generate_schedule(request)
    """

    def __init__(
        self,
        shift_policy: Optional[ShiftPolicy] = None,
        lunch_policy: Optional[LunchPolicy] = None,
        break_policy: Optional[BreakPolicy] = None,
    ):
        """Initialize weekly scheduler with policies.

        Args:
            shift_policy: Policy for shift length rules.
            lunch_policy: Policy for lunch break rules.
            break_policy: Policy for rest break rules.
        """
        self.shift_policy = shift_policy or DefaultShiftPolicy()
        self.lunch_policy = lunch_policy or DefaultLunchPolicy()
        self.break_policy = break_policy or DefaultBreakPolicy()

        self.candidate_generator = CandidateGenerator(
            shift_policy=self.shift_policy,
            lunch_policy=self.lunch_policy,
            break_policy=self.break_policy,
        )

        self.solver = HeuristicSolver(
            lunch_policy=self.lunch_policy,
            break_policy=self.break_policy,
        )

    def generate_schedule(
        self,
        request: WeeklyScheduleRequest,
        step_slots: int = 2,
    ) -> WeeklySchedule:
        """Generate a complete weekly schedule.

        Args:
            request: Weekly schedule request with dates, associates, and constraints.
            step_slots: Granularity for shift start/end times (in slots).

        Returns:
            Complete WeeklySchedule with all daily assignments.
        """
        # Initialize weekly state tracking
        associates_map = {a.id: a for a in request.associates}
        weekly_states = self._init_weekly_states(request.associates)

        # Initialize pattern enforcer and fairness balancer
        pattern_enforcer = DaysOffPatternEnforcer(
            request.days_off_pattern,
            request.required_days_off,
        )
        fairness_balancer = FairnessBalancer(request.fairness_config)

        # Create weekly schedule container
        weekly_schedule = WeeklySchedule(
            start_date=request.start_date,
            end_date=request.end_date,
        )

        all_dates = request.schedule_dates

        # Schedule each day
        for i, schedule_date in enumerate(all_dates):
            remaining_dates = all_dates[i:]

            # Determine which associates work today
            working_associates = self._get_working_associates(
                request.associates,
                schedule_date,
                weekly_states,
                remaining_dates,
                all_dates,
                pattern_enforcer,
                fairness_balancer,
            )

            if not working_associates:
                # No one available to work
                day_schedule = DaySchedule(
                    schedule_date=schedule_date,
                    slot_minutes=request.slot_minutes,
                    day_start_minutes=request.day_start_minutes,
                    day_end_minutes=request.day_end_minutes,
                )
                weekly_schedule.day_schedules[schedule_date] = day_schedule
                continue

            # Create modified associates with adjusted max daily hours
            adjusted_associates = self._adjust_associates_for_weekly_limits(
                working_associates,
                schedule_date,
                weekly_states,
            )

            # Create day request
            day_request = ScheduleRequest(
                schedule_date=schedule_date,
                associates=adjusted_associates,
                day_start_minutes=request.day_start_minutes,
                day_end_minutes=request.day_end_minutes,
                slot_minutes=request.slot_minutes,
                job_caps=request.job_caps,
                is_busy_day=request.is_busy_day(schedule_date),
                shift_block_configs=request.shift_block_configs,
                shift_start_configs=request.shift_start_configs,
            )

            # Generate candidates with fairness-aware scoring
            candidates = self._generate_fairness_aware_candidates(
                day_request,
                weekly_states,
                fairness_balancer,
                step_slots,
            )

            # Solve the day
            day_schedule = self.solver.solve(
                day_request,
                candidates,
                associates_map,
            )

            # Update weekly states
            self._update_weekly_states(
                day_schedule,
                schedule_date,
                weekly_states,
                working_associates,
            )

            weekly_schedule.day_schedules[schedule_date] = day_schedule

        # Calculate fairness metrics
        weekly_schedule.fairness_metrics = self._calculate_fairness_metrics(weekly_states)

        return weekly_schedule

    def _init_weekly_states(
        self,
        associates: list[Associate],
    ) -> dict[str, AssociateWeeklyState]:
        """Initialize weekly state tracking for all associates."""
        return {
            a.id: AssociateWeeklyState(
                associate_id=a.id,
                max_weekly_minutes=a.max_minutes_per_week,
            )
            for a in associates
        }

    def _get_working_associates(
        self,
        associates: list[Associate],
        schedule_date: date,
        weekly_states: dict[str, AssociateWeeklyState],
        remaining_dates: list[date],
        all_dates: list[date],
        pattern_enforcer: DaysOffPatternEnforcer,
        fairness_balancer: FairnessBalancer,
    ) -> list[Associate]:
        """Determine which associates should work on a given day."""
        working = []
        remaining_days = len(remaining_dates)

        for associate in associates:
            state = weekly_states[associate.id]

            # Check availability first
            availability = associate.get_availability(schedule_date)
            if availability.is_off or availability.slot_count() == 0:
                state.add_day_off(schedule_date)
                continue

            # Check if reached weekly limit
            if state.remaining_minutes < self.shift_policy.min_work_minutes():
                state.add_day_off(schedule_date)
                continue

            # Check days-off pattern
            if pattern_enforcer.should_be_day_off(
                state, schedule_date, remaining_dates, all_dates
            ):
                state.add_day_off(schedule_date)
                continue

            # Check fairness balancing
            if fairness_balancer.should_skip_associate(
                state, weekly_states, remaining_days
            ):
                # Don't mark as permanent day off, just skip this day
                continue

            working.append(associate)

        return working

    def _adjust_associates_for_weekly_limits(
        self,
        associates: list[Associate],
        schedule_date: date,
        weekly_states: dict[str, AssociateWeeklyState],
    ) -> list[Associate]:
        """Create modified associates with adjusted daily limits based on weekly remaining."""
        adjusted = []

        for associate in associates:
            state = weekly_states[associate.id]

            # Cap daily max at remaining weekly minutes
            adjusted_daily_max = min(
                associate.max_minutes_per_day,
                state.remaining_minutes,
            )

            # Ensure at least minimum shift is possible
            if adjusted_daily_max < self.shift_policy.min_work_minutes():
                continue

            # Create a modified associate with adjusted limit
            modified = Associate(
                id=associate.id,
                name=associate.name,
                availability=associate.availability,
                max_minutes_per_day=adjusted_daily_max,
                max_minutes_per_week=associate.max_minutes_per_week,
                supervisor_allowed_roles=associate.supervisor_allowed_roles,
                cannot_do_roles=associate.cannot_do_roles,
                role_preferences=associate.role_preferences,
            )
            adjusted.append(modified)

        return adjusted

    def _generate_fairness_aware_candidates(
        self,
        request: ScheduleRequest,
        weekly_states: dict[str, AssociateWeeklyState],
        fairness_balancer: FairnessBalancer,
        step_slots: int,
    ) -> dict[str, list[ShiftCandidate]]:
        """Generate candidates with fairness considerations.

        Associates with fewer hours get priority ordering.
        """
        # Generate base candidates
        candidates = self.candidate_generator.generate_all_candidates(
            request, step_slots
        )

        # Sort candidates for each associate by fairness-adjusted preference
        # (shorter shifts for those ahead on hours, longer for those behind)
        for assoc_id, assoc_candidates in candidates.items():
            if assoc_id not in weekly_states:
                continue

            state = weekly_states[assoc_id]
            all_minutes = [s.minutes_scheduled for s in weekly_states.values()]
            avg_minutes = sum(all_minutes) / len(all_minutes) if all_minutes else 0

            # Sort candidates: if behind on hours, prefer longer shifts
            if state.minutes_scheduled < avg_minutes:
                assoc_candidates.sort(key=lambda c: -c.work_minutes)
            # If ahead on hours, prefer shorter shifts
            elif state.minutes_scheduled > avg_minutes * 1.1:
                assoc_candidates.sort(key=lambda c: c.work_minutes)

        return candidates

    def _update_weekly_states(
        self,
        day_schedule: DaySchedule,
        schedule_date: date,
        weekly_states: dict[str, AssociateWeeklyState],
        working_associates: list[Associate],
    ) -> None:
        """Update weekly states after a day is scheduled."""
        # Update states for those who got shifts
        for assoc_id, assignment in day_schedule.assignments.items():
            if assoc_id in weekly_states:
                weekly_states[assoc_id].add_shift(
                    schedule_date,
                    assignment.work_minutes,
                )

        # Record day off for those who were working but didn't get scheduled
        scheduled_ids = set(day_schedule.assignments.keys())
        for associate in working_associates:
            if associate.id not in scheduled_ids:
                weekly_states[associate.id].add_day_off(schedule_date)

    def _calculate_fairness_metrics(
        self,
        weekly_states: dict[str, AssociateWeeklyState],
    ) -> FairnessMetrics:
        """Calculate fairness metrics from final weekly states."""
        weekly_minutes = {
            aid: state.minutes_scheduled
            for aid, state in weekly_states.items()
        }
        weekly_days = {
            aid: len(state.days_worked)
            for aid, state in weekly_states.items()
        }

        return FairnessMetrics.calculate(weekly_minutes, weekly_days)

    def generate_schedule_with_stats(
        self,
        request: WeeklyScheduleRequest,
        step_slots: int = 2,
    ) -> tuple[WeeklySchedule, dict]:
        """Generate weekly schedule and return statistics.

        Args:
            request: Weekly schedule request.
            step_slots: Candidate generation granularity.

        Returns:
            Tuple of (schedule, stats_dict).
        """
        schedule = self.generate_schedule(request, step_slots)

        # Calculate comprehensive statistics
        stats = self._calculate_stats(schedule, request)

        return schedule, stats

    def _calculate_stats(
        self,
        schedule: WeeklySchedule,
        request: WeeklyScheduleRequest,
    ) -> dict:
        """Calculate weekly schedule statistics."""
        total_associates = len(request.associates)
        total_work_minutes = 0
        total_shifts = 0
        coverage_by_day = {}
        hours_by_associate = {}
        days_by_associate = {}

        for d, day_schedule in schedule.day_schedules.items():
            total_shifts += len(day_schedule.assignments)

            for assoc_id, assignment in day_schedule.assignments.items():
                total_work_minutes += assignment.work_minutes
                hours_by_associate[assoc_id] = (
                    hours_by_associate.get(assoc_id, 0) + assignment.work_minutes / 60.0
                )
                days_by_associate[assoc_id] = days_by_associate.get(assoc_id, 0) + 1

            timeline = day_schedule.get_coverage_timeline()
            if timeline:
                coverage_by_day[d] = {
                    "min": min(timeline),
                    "max": max(timeline),
                    "avg": sum(timeline) / len(timeline),
                }

        # Calculate averages
        avg_hours = (
            sum(hours_by_associate.values()) / len(hours_by_associate)
            if hours_by_associate
            else 0
        )
        avg_days = (
            sum(days_by_associate.values()) / len(days_by_associate)
            if days_by_associate
            else 0
        )

        return {
            "total_associates": total_associates,
            "total_shifts": total_shifts,
            "total_work_hours": total_work_minutes / 60.0,
            "avg_hours_per_associate": avg_hours,
            "avg_days_per_associate": avg_days,
            "hours_by_associate": hours_by_associate,
            "days_by_associate": days_by_associate,
            "coverage_by_day": coverage_by_day,
            "fairness_metrics": schedule.fairness_metrics,
            "num_days": len(schedule.day_schedules),
        }
