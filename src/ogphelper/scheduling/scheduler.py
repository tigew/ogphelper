"""Main scheduler interface.

This module provides the high-level Scheduler class that orchestrates
candidate generation, solving, and validation.
"""

from typing import Optional

from ogphelper.domain.models import (
    Associate,
    DaySchedule,
    ScheduleRequest,
)
from ogphelper.domain.policies import (
    BreakPolicy,
    DefaultBreakPolicy,
    DefaultLunchPolicy,
    DefaultShiftPolicy,
    LunchPolicy,
    ShiftPolicy,
)
from ogphelper.scheduling.candidate_generator import CandidateGenerator
from ogphelper.scheduling.heuristic_solver import HeuristicSolver


class Scheduler:
    """High-level scheduler for generating daily schedules.

    The Scheduler coordinates candidate generation and solving to
    produce optimized schedules that respect all constraints.

    Example:
        >>> scheduler = Scheduler()
        >>> request = ScheduleRequest(
        ...     schedule_date=date(2024, 1, 15),
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
        """Initialize scheduler with policies.

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
        request: ScheduleRequest,
        step_slots: int = 2,
    ) -> DaySchedule:
        """Generate a complete schedule for the request.

        Args:
            request: Schedule request with date, associates, and constraints.
            step_slots: Granularity for shift start/end times (in slots).
                       Smaller values = more candidates = better optimization
                       but slower generation.

        Returns:
            Complete DaySchedule with all assignments.
        """
        # Build associate lookup
        associates_map = {a.id: a for a in request.associates}

        # Generate candidates for all associates
        candidates = self.candidate_generator.generate_all_candidates(
            request, step_slots=step_slots
        )

        # Solve using heuristic
        schedule = self.solver.solve(request, candidates, associates_map)

        return schedule

    def generate_schedule_with_stats(
        self,
        request: ScheduleRequest,
        step_slots: int = 2,
    ) -> tuple[DaySchedule, dict]:
        """Generate schedule and return statistics.

        Args:
            request: Schedule request.
            step_slots: Candidate generation granularity.

        Returns:
            Tuple of (schedule, stats_dict).
        """
        schedule = self.generate_schedule(request, step_slots)

        # Calculate statistics
        stats = self._calculate_stats(schedule, request)

        return schedule, stats

    def _calculate_stats(
        self,
        schedule: DaySchedule,
        request: ScheduleRequest,
    ) -> dict:
        """Calculate schedule statistics."""
        coverage = schedule.get_coverage_timeline()

        total_associates = len(request.associates)
        scheduled_associates = len(schedule.assignments)
        unscheduled = total_associates - scheduled_associates

        total_work_minutes = sum(
            a.work_minutes for a in schedule.assignments.values()
        )
        total_lunch_minutes = sum(
            a.lunch_minutes for a in schedule.assignments.values()
        )
        total_break_minutes = sum(
            a.break_minutes for a in schedule.assignments.values()
        )

        return {
            "total_associates": total_associates,
            "scheduled_associates": scheduled_associates,
            "unscheduled_associates": unscheduled,
            "total_work_minutes": total_work_minutes,
            "total_lunch_minutes": total_lunch_minutes,
            "total_break_minutes": total_break_minutes,
            "min_coverage": min(coverage) if coverage else 0,
            "max_coverage": max(coverage) if coverage else 0,
            "avg_coverage": sum(coverage) / len(coverage) if coverage else 0,
            "coverage_timeline": coverage,
        }
