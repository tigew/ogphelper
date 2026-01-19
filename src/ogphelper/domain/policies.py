"""Policy definitions for scheduling rules.

This module contains configurable policies that define business rules
for shifts, lunches, and breaks. Policies are kept separate from
the scheduling engine to allow independent testing and easy modification.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class ShiftPolicy(ABC):
    """Abstract base class for shift length policies."""

    @abstractmethod
    def min_work_minutes(self) -> int:
        """Minimum work time in minutes (excluding lunch)."""
        pass

    @abstractmethod
    def max_work_minutes(self) -> int:
        """Maximum work time in minutes (excluding lunch)."""
        pass

    @abstractmethod
    def is_valid_work_duration(self, work_minutes: int) -> bool:
        """Check if a work duration is valid."""
        pass


class LunchPolicy(ABC):
    """Abstract base class for lunch break policies."""

    @abstractmethod
    def get_lunch_duration(self, work_minutes: int) -> int:
        """Get required lunch duration in minutes based on work time.

        Args:
            work_minutes: Total work time in minutes (excluding lunch).

        Returns:
            Required lunch duration in minutes (0 if no lunch needed).
        """
        pass

    @abstractmethod
    def get_lunch_window(
        self,
        shift_start_slot: int,
        shift_end_slot: int,
        lunch_slots: int,
        is_busy_day: bool,
    ) -> tuple[int, int]:
        """Get the allowable window for lunch placement.

        Args:
            shift_start_slot: First slot of the shift.
            shift_end_slot: Last slot of the shift (exclusive).
            lunch_slots: Number of slots for lunch.
            is_busy_day: If True, allow wider lunch window.

        Returns:
            Tuple of (earliest_start_slot, latest_start_slot) for lunch.
        """
        pass


class BreakPolicy(ABC):
    """Abstract base class for rest break policies."""

    @abstractmethod
    def get_break_count(self, work_minutes: int) -> int:
        """Get number of breaks required based on work time.

        Args:
            work_minutes: Total work time in minutes.

        Returns:
            Number of breaks required.
        """
        pass

    @abstractmethod
    def get_break_duration(self) -> int:
        """Get duration of each break in minutes."""
        pass

    @abstractmethod
    def get_break_target_positions(
        self,
        work_start_slot: int,
        work_end_slot: int,
        break_count: int,
        lunch_start_slot: Optional[int],
        lunch_end_slot: Optional[int],
    ) -> list[int]:
        """Get target slot positions for breaks.

        Breaks should be positioned to minimize operational impact,
        typically around 1/3 and 2/3 points of the work period.

        Args:
            work_start_slot: First slot of work.
            work_end_slot: Last slot of work (exclusive).
            break_count: Number of breaks to place.
            lunch_start_slot: Start of lunch block (if any).
            lunch_end_slot: End of lunch block (if any).

        Returns:
            List of target start slots for each break.
        """
        pass

    @abstractmethod
    def get_max_break_variance_slots(self) -> int:
        """Get maximum allowed variance from ideal break position in slots.

        Returns:
            Maximum number of slots a break can deviate from the ideal midpoint.
        """
        pass


@dataclass
class DefaultShiftPolicy(ShiftPolicy):
    """Default shift policy implementation.

    Work time bounds:
    - Minimum: 4 hours (240 minutes)
    - Maximum: 8 hours (480 minutes)

    Note: Lunch time is NOT counted toward the 8-hour max.
    """

    min_work: int = 240  # 4 hours
    max_work: int = 480  # 8 hours

    def min_work_minutes(self) -> int:
        return self.min_work

    def max_work_minutes(self) -> int:
        return self.max_work

    def is_valid_work_duration(self, work_minutes: int) -> bool:
        return self.min_work <= work_minutes <= self.max_work


@dataclass
class DefaultLunchPolicy(LunchPolicy):
    """Default lunch policy implementation.

    Lunch requirements based on work duration:
    - work < 6 hours (360 min): No lunch
    - work >= 6 hours and < 6.5 hours (390 min): 30-minute lunch
    - work >= 6.5 hours: 60-minute lunch

    Lunch timing:
    - Normal days: ±30 minutes around mid-shift target
    - Busy days: ±60 minutes for peak coverage protection
    """

    # Thresholds in minutes
    no_lunch_threshold: int = 360  # 6 hours
    short_lunch_threshold: int = 390  # 6.5 hours

    # Lunch durations in minutes
    short_lunch_duration: int = 30
    long_lunch_duration: int = 60

    # Window flexibility in minutes
    normal_day_window: int = 30
    busy_day_window: int = 60

    def get_lunch_duration(self, work_minutes: int) -> int:
        if work_minutes < self.no_lunch_threshold:
            return 0
        elif work_minutes < self.short_lunch_threshold:
            return self.short_lunch_duration
        else:
            return self.long_lunch_duration

    def get_lunch_window(
        self,
        shift_start_slot: int,
        shift_end_slot: int,
        lunch_slots: int,
        is_busy_day: bool,
        slot_minutes: int = 15,
    ) -> tuple[int, int]:
        """Calculate allowable lunch placement window.

        The window is centered around the mid-point of the shift,
        with flexibility based on whether it's a busy day.
        """
        if lunch_slots == 0:
            return (0, 0)

        shift_length = shift_end_slot - shift_start_slot
        mid_point = shift_start_slot + shift_length // 2

        # Target: lunch should be placed so its middle aligns with shift middle
        target_start = mid_point - lunch_slots // 2

        # Calculate window based on day type
        window_minutes = self.busy_day_window if is_busy_day else self.normal_day_window
        window_slots = window_minutes // slot_minutes

        earliest_start = max(shift_start_slot + 4, target_start - window_slots)  # At least 1 hour into shift
        latest_start = min(
            shift_end_slot - lunch_slots - 4,  # At least 1 hour before end
            target_start + window_slots,
        )

        # Ensure valid window
        earliest_start = max(shift_start_slot, earliest_start)
        latest_start = max(earliest_start, latest_start)

        return (earliest_start, latest_start)


@dataclass
class DefaultBreakPolicy(BreakPolicy):
    """Default break policy implementation.

    Break requirements based on work duration:
    - 8 hours work: 2 breaks
    - 5+ hours work: 1 break
    - < 5 hours work: 0 breaks

    Break duration: 15 minutes each (configurable)

    Break placement: Target midpoint of each work segment (before lunch
    and after lunch), with maximum variance of 30 minutes from ideal.
    """

    # Thresholds in minutes
    one_break_threshold: int = 300  # 5 hours
    two_break_threshold: int = 420  # 7 hours

    # Break duration in minutes
    break_duration: int = 15

    # Minimum gap from lunch (in slots)
    min_gap_from_lunch_slots: int = 2

    # Maximum variance from ideal midpoint position (in slots)
    # 2 slots = 30 minutes max variance from the exact midpoint
    max_break_variance_slots: int = 2

    def get_break_count(self, work_minutes: int) -> int:
        if work_minutes >= self.two_break_threshold:
            return 2
        elif work_minutes >= self.one_break_threshold:
            return 1
        else:
            return 0

    def get_max_break_variance_slots(self) -> int:
        """Return maximum allowed variance from ideal break position in slots."""
        return self.max_break_variance_slots

    def get_break_duration(self) -> int:
        return self.break_duration

    def get_break_target_positions(
        self,
        work_start_slot: int,
        work_end_slot: int,
        break_count: int,
        lunch_start_slot: Optional[int],
        lunch_end_slot: Optional[int],
        slot_minutes: int = 15,
    ) -> list[int]:
        """Calculate target break positions at 1/3 and 2/3 points."""
        if break_count == 0:
            return []

        break_slots = self.break_duration // slot_minutes
        work_length = work_end_slot - work_start_slot

        # Calculate effective work periods (excluding lunch)
        if lunch_start_slot is not None and lunch_end_slot is not None:
            # Work is split by lunch into two segments
            segment1_length = lunch_start_slot - work_start_slot
            segment2_length = work_end_slot - lunch_end_slot
            total_work_length = segment1_length + segment2_length
        else:
            total_work_length = work_length

        targets = []

        if break_count == 1:
            # Single break at midpoint
            if lunch_start_slot is not None:
                # Place in longer segment
                segment1_length = lunch_start_slot - work_start_slot
                segment2_length = work_end_slot - lunch_end_slot
                if segment1_length >= segment2_length:
                    target = work_start_slot + segment1_length // 2
                else:
                    target = lunch_end_slot + segment2_length // 2
            else:
                target = work_start_slot + work_length // 2
            targets.append(target)

        elif break_count == 2:
            # Two breaks at 1/3 and 2/3 points
            if lunch_start_slot is not None:
                # First break in first segment, second in second segment
                segment1_length = lunch_start_slot - work_start_slot
                segment2_length = work_end_slot - lunch_end_slot

                # First break around middle of first segment
                target1 = work_start_slot + segment1_length // 2
                # Second break around middle of second segment
                target2 = lunch_end_slot + segment2_length // 2

                targets = [target1, target2]
            else:
                # No lunch - place at 1/3 and 2/3 points
                one_third = work_start_slot + work_length // 3
                two_thirds = work_start_slot + (2 * work_length) // 3
                targets = [one_third, two_thirds]

        # Ensure breaks don't overlap with lunch and stay within bounds
        adjusted_targets = []
        for target in targets:
            # Adjust if too close to lunch
            if lunch_start_slot is not None:
                if lunch_start_slot <= target < lunch_end_slot:
                    # Break falls during lunch - move it
                    if target - work_start_slot < work_end_slot - target:
                        target = lunch_start_slot - break_slots - self.min_gap_from_lunch_slots
                    else:
                        target = lunch_end_slot + self.min_gap_from_lunch_slots

            # Ensure within bounds
            target = max(work_start_slot, target)
            target = min(work_end_slot - break_slots, target)
            adjusted_targets.append(target)

        return adjusted_targets


def minutes_to_slots(minutes: int, slot_minutes: int = 15) -> int:
    """Convert minutes to number of slots."""
    return minutes // slot_minutes


def slots_to_minutes(slots: int, slot_minutes: int = 15) -> int:
    """Convert number of slots to minutes."""
    return slots * slot_minutes
