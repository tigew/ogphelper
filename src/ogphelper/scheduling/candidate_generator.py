"""Candidate generator for creating feasible shift options.

This module generates all valid shift candidates for each associate,
respecting availability, work hour limits, and policy constraints.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from ogphelper.domain.models import (
    Associate,
    Availability,
    ScheduleBlock,
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


@dataclass
class ShiftCandidate:
    """A candidate shift option for an associate.

    Attributes:
        associate_id: ID of the associate.
        start_slot: First slot of the shift.
        end_slot: Last slot of the shift (exclusive).
        work_minutes: Work time in minutes (excluding lunch).
        lunch_slots: Number of slots for lunch.
        break_count: Number of breaks required.
        slot_minutes: Duration of each slot.
    """

    associate_id: str
    start_slot: int
    end_slot: int
    work_minutes: int
    lunch_slots: int
    break_count: int
    slot_minutes: int = 15

    @property
    def total_shift_slots(self) -> int:
        """Total slots in the shift."""
        return self.end_slot - self.start_slot

    @property
    def total_shift_minutes(self) -> int:
        """Total shift duration in minutes."""
        return self.total_shift_slots * self.slot_minutes

    def __repr__(self) -> str:
        from datetime import time

        def slot_to_time(slot: int, day_start: int = 300) -> time:
            mins = day_start + slot * self.slot_minutes
            h, m = divmod(mins, 60)
            return time(hour=h, minute=m)

        start = slot_to_time(self.start_slot)
        end = slot_to_time(self.end_slot)
        return (
            f"ShiftCandidate({self.associate_id}: "
            f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}, "
            f"work={self.work_minutes}min, lunch={self.lunch_slots * self.slot_minutes}min)"
        )


class CandidateGenerator:
    """Generates feasible shift candidates for associates.

    The generator creates all valid shift options based on:
    - Associate availability
    - Shift policy (min/max work hours)
    - Lunch policy (required duration based on work time)
    - Break policy (number of breaks based on work time)
    - Daily hour limits
    """

    def __init__(
        self,
        shift_policy: Optional[ShiftPolicy] = None,
        lunch_policy: Optional[LunchPolicy] = None,
        break_policy: Optional[BreakPolicy] = None,
    ):
        self.shift_policy = shift_policy or DefaultShiftPolicy()
        self.lunch_policy = lunch_policy or DefaultLunchPolicy()
        self.break_policy = break_policy or DefaultBreakPolicy()

    def generate_candidates(
        self,
        associate: Associate,
        request: ScheduleRequest,
        step_slots: int = 1,
    ) -> list[ShiftCandidate]:
        """Generate all feasible shift candidates for an associate.

        Args:
            associate: The associate to generate candidates for.
            request: Schedule request with date and constraints.
            step_slots: Granularity for start/end times (default 1 = every slot).

        Returns:
            List of valid ShiftCandidate objects.
        """
        availability = associate.get_availability(request.schedule_date)
        if availability.is_off:
            return []

        candidates = []
        slot_minutes = request.slot_minutes
        min_work_slots = self.shift_policy.min_work_minutes() // slot_minutes
        max_work_slots = self.shift_policy.max_work_minutes() // slot_minutes

        # Determine available window
        day_slots = request.total_slots
        avail_start = max(0, availability.start_slot)
        avail_end = min(day_slots, availability.end_slot)

        if avail_end - avail_start < min_work_slots:
            # Not enough availability for minimum shift
            return []

        # Generate all valid start/end combinations
        for start_slot in range(avail_start, avail_end, step_slots):
            for work_slots in range(min_work_slots, max_work_slots + 1, step_slots):
                work_minutes = work_slots * slot_minutes

                # Check daily hour limit
                if work_minutes > associate.max_minutes_per_day:
                    continue

                # Calculate lunch requirement
                lunch_minutes = self.lunch_policy.get_lunch_duration(work_minutes)
                lunch_slots = lunch_minutes // slot_minutes

                # Total shift includes work + lunch
                total_slots = work_slots + lunch_slots
                end_slot = start_slot + total_slots

                # Check if shift fits within availability
                if end_slot > avail_end:
                    continue

                # Check if shift fits within day bounds
                if end_slot > day_slots:
                    continue

                # Calculate break count
                break_count = self.break_policy.get_break_count(work_minutes)

                candidate = ShiftCandidate(
                    associate_id=associate.id,
                    start_slot=start_slot,
                    end_slot=end_slot,
                    work_minutes=work_minutes,
                    lunch_slots=lunch_slots,
                    break_count=break_count,
                    slot_minutes=slot_minutes,
                )
                candidates.append(candidate)

        return candidates

    def generate_all_candidates(
        self,
        request: ScheduleRequest,
        step_slots: int = 2,
    ) -> dict[str, list[ShiftCandidate]]:
        """Generate candidates for all associates in the request.

        Args:
            request: Schedule request with associates and constraints.
            step_slots: Granularity for start/end times.

        Returns:
            Dict mapping associate IDs to their candidate lists.
        """
        all_candidates = {}
        for associate in request.associates:
            candidates = self.generate_candidates(
                associate, request, step_slots=step_slots
            )
            if candidates:
                all_candidates[associate.id] = candidates
        return all_candidates

    def filter_by_work_duration(
        self,
        candidates: list[ShiftCandidate],
        min_minutes: Optional[int] = None,
        max_minutes: Optional[int] = None,
    ) -> list[ShiftCandidate]:
        """Filter candidates by work duration range."""
        result = candidates
        if min_minutes is not None:
            result = [c for c in result if c.work_minutes >= min_minutes]
        if max_minutes is not None:
            result = [c for c in result if c.work_minutes <= max_minutes]
        return result

    def filter_by_start_time(
        self,
        candidates: list[ShiftCandidate],
        earliest_slot: Optional[int] = None,
        latest_slot: Optional[int] = None,
    ) -> list[ShiftCandidate]:
        """Filter candidates by start time range."""
        result = candidates
        if earliest_slot is not None:
            result = [c for c in result if c.start_slot >= earliest_slot]
        if latest_slot is not None:
            result = [c for c in result if c.start_slot <= latest_slot]
        return result
