"""Domain models for the scheduling system.

This module contains all core data structures used throughout the scheduling
system, including associates, time slots, shifts, and schedule outputs.
"""

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from enum import Enum
from typing import Optional


class JobRole(Enum):
    """Available job roles for associates."""

    PICKING = "picking"
    GMD_SM = "gmd_sm"
    EXCEPTION_SM = "exception_sm"
    STAGING = "staging"
    BACKROOM = "backroom"


class Preference(Enum):
    """Associate preference level for a job role."""

    AVOID = -1  # Prefer not to do this role
    NEUTRAL = 0  # No preference
    PREFER = 1  # Would like to do this role


@dataclass(frozen=True)
class TimeSlot:
    """Represents a discrete time slot in the schedule.

    Time slots are the fundamental unit of scheduling. By default, slots
    are 15 minutes each, allowing for fine-grained control over scheduling.

    Attributes:
        index: Zero-based index of the slot within the day.
        slot_minutes: Duration of each slot in minutes (default 15).
    """

    index: int
    slot_minutes: int = 15

    @property
    def start_minutes(self) -> int:
        """Minutes from midnight when this slot starts."""
        return self.index * self.slot_minutes

    @property
    def end_minutes(self) -> int:
        """Minutes from midnight when this slot ends."""
        return (self.index + 1) * self.slot_minutes

    @property
    def start_time(self) -> time:
        """Time object for when this slot starts."""
        hours, mins = divmod(self.start_minutes, 60)
        return time(hour=hours, minute=mins)

    @property
    def end_time(self) -> time:
        """Time object for when this slot ends."""
        hours, mins = divmod(self.end_minutes, 60)
        return time(hour=hours, minute=mins)

    def __repr__(self) -> str:
        return f"TimeSlot({self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')})"


@dataclass
class Availability:
    """Defines when an associate is available to work.

    Attributes:
        start_slot: First slot the associate can start working.
        end_slot: Last slot the associate can work (exclusive).
        is_off: If True, associate is completely unavailable this day.
    """

    start_slot: int
    end_slot: int
    is_off: bool = False

    @classmethod
    def off_day(cls) -> "Availability":
        """Create availability representing a day off."""
        return cls(start_slot=0, end_slot=0, is_off=True)

    @classmethod
    def from_times(
        cls,
        start_time: time,
        end_time: time,
        slot_minutes: int = 15,
        day_start_minutes: int = 300,  # 5 AM
    ) -> "Availability":
        """Create availability from time objects.

        Args:
            start_time: Earliest time associate can start.
            end_time: Latest time associate can work until.
            slot_minutes: Duration of each slot in minutes.
            day_start_minutes: Minutes from midnight when the schedule day starts.
        """
        start_mins = start_time.hour * 60 + start_time.minute
        end_mins = end_time.hour * 60 + end_time.minute

        start_slot = (start_mins - day_start_minutes) // slot_minutes
        end_slot = (end_mins - day_start_minutes) // slot_minutes

        return cls(start_slot=max(0, start_slot), end_slot=end_slot)

    def slot_count(self) -> int:
        """Number of available slots."""
        if self.is_off:
            return 0
        return self.end_slot - self.start_slot


@dataclass
class Associate:
    """Represents an associate who can be scheduled.

    Attributes:
        id: Unique identifier for the associate.
        name: Display name for the associate.
        availability: Dict mapping dates to availability windows.
        max_minutes_per_day: Maximum work minutes allowed per day.
        max_minutes_per_week: Maximum work minutes allowed per week.
        supervisor_allowed_roles: Roles the supervisor has approved (hard constraint).
        cannot_do_roles: Roles the associate physically cannot do (hard constraint).
        role_preferences: Soft preferences for each role.
    """

    id: str
    name: str
    availability: dict[date, Availability] = field(default_factory=dict)
    max_minutes_per_day: int = 480  # 8 hours default
    max_minutes_per_week: int = 2400  # 40 hours default
    supervisor_allowed_roles: set[JobRole] = field(
        default_factory=lambda: set(JobRole)
    )
    cannot_do_roles: set[JobRole] = field(default_factory=set)
    role_preferences: dict[JobRole, Preference] = field(default_factory=dict)

    def get_availability(self, schedule_date: date) -> Availability:
        """Get availability for a specific date."""
        return self.availability.get(schedule_date, Availability.off_day())

    def can_do_role(self, role: JobRole) -> bool:
        """Check if associate can be assigned to a role (hard constraints only)."""
        if role in self.cannot_do_roles:
            return False
        if role not in self.supervisor_allowed_roles:
            return False
        return True

    def get_preference(self, role: JobRole) -> Preference:
        """Get preference level for a role."""
        return self.role_preferences.get(role, Preference.NEUTRAL)

    def eligible_roles(self) -> set[JobRole]:
        """Get all roles this associate can be assigned to."""
        return self.supervisor_allowed_roles - self.cannot_do_roles


@dataclass(frozen=True)
class ScheduleBlock:
    """A contiguous block of time in a schedule.

    Used for representing lunch breaks, rest breaks, and work periods.

    Attributes:
        start_slot: First slot of the block (inclusive).
        end_slot: Last slot of the block (exclusive).
        slot_minutes: Duration of each slot in minutes.
    """

    start_slot: int
    end_slot: int
    slot_minutes: int = 15

    @property
    def duration_minutes(self) -> int:
        """Total duration of the block in minutes."""
        return (self.end_slot - self.start_slot) * self.slot_minutes

    @property
    def slot_count(self) -> int:
        """Number of slots in the block."""
        return self.end_slot - self.start_slot

    def contains_slot(self, slot: int) -> bool:
        """Check if a slot index falls within this block."""
        return self.start_slot <= slot < self.end_slot

    def overlaps(self, other: "ScheduleBlock") -> bool:
        """Check if this block overlaps with another."""
        return self.start_slot < other.end_slot and other.start_slot < self.end_slot

    def __repr__(self) -> str:
        start = TimeSlot(self.start_slot, self.slot_minutes)
        end = TimeSlot(self.end_slot - 1, self.slot_minutes)
        return (
            f"ScheduleBlock({start.start_time.strftime('%H:%M')}-"
            f"{end.end_time.strftime('%H:%M')})"
        )


@dataclass
class JobAssignment:
    """Assignment of a role to a specific time block.

    Attributes:
        role: The job role assigned.
        block: The time block for this assignment.
    """

    role: JobRole
    block: ScheduleBlock


@dataclass
class ShiftAssignment:
    """Complete shift assignment for an associate on a given day.

    Attributes:
        associate_id: ID of the associate.
        schedule_date: Date of the shift.
        shift_start_slot: First slot of the shift.
        shift_end_slot: Last slot of the shift (exclusive).
        lunch_block: Lunch break block, if applicable.
        break_blocks: List of rest break blocks.
        job_assignments: List of role assignments during the shift.
        slot_minutes: Duration of each slot.
    """

    associate_id: str
    schedule_date: date
    shift_start_slot: int
    shift_end_slot: int
    lunch_block: Optional[ScheduleBlock] = None
    break_blocks: list[ScheduleBlock] = field(default_factory=list)
    job_assignments: list[JobAssignment] = field(default_factory=list)
    slot_minutes: int = 15

    @property
    def total_shift_minutes(self) -> int:
        """Total shift duration including lunch."""
        return (self.shift_end_slot - self.shift_start_slot) * self.slot_minutes

    @property
    def lunch_minutes(self) -> int:
        """Duration of lunch break in minutes."""
        if self.lunch_block is None:
            return 0
        return self.lunch_block.duration_minutes

    @property
    def break_minutes(self) -> int:
        """Total duration of all rest breaks in minutes."""
        return sum(b.duration_minutes for b in self.break_blocks)

    @property
    def work_minutes(self) -> int:
        """Time spent on work duties (excluding lunch, including breaks)."""
        return self.total_shift_minutes - self.lunch_minutes

    @property
    def shift_block(self) -> ScheduleBlock:
        """The entire shift as a ScheduleBlock."""
        return ScheduleBlock(
            self.shift_start_slot, self.shift_end_slot, self.slot_minutes
        )

    def is_on_floor(self, slot: int) -> bool:
        """Check if associate is on floor (working) at a given slot."""
        if not self.shift_block.contains_slot(slot):
            return False
        if self.lunch_block and self.lunch_block.contains_slot(slot):
            return False
        for break_block in self.break_blocks:
            if break_block.contains_slot(slot):
                return False
        return True

    def get_role_at_slot(self, slot: int) -> Optional[JobRole]:
        """Get the assigned role at a specific slot, if any."""
        for assignment in self.job_assignments:
            if assignment.block.contains_slot(slot):
                return assignment.role
        return None


@dataclass
class ScheduleRequest:
    """Request parameters for generating a schedule.

    Attributes:
        schedule_date: Date to generate the schedule for.
        associates: List of associates to schedule.
        day_start_minutes: Minutes from midnight when schedule day starts (default 5 AM).
        day_end_minutes: Minutes from midnight when schedule day ends (default 10 PM).
        slot_minutes: Duration of each time slot in minutes.
        job_caps: Maximum associates per role per slot.
        is_busy_day: If True, allow more aggressive lunch shifting.
    """

    schedule_date: date
    associates: list[Associate]
    day_start_minutes: int = 300  # 5:00 AM
    day_end_minutes: int = 1320  # 10:00 PM
    slot_minutes: int = 15
    job_caps: dict[JobRole, int] = field(
        default_factory=lambda: {
            JobRole.PICKING: 999,  # Effectively unlimited
            JobRole.GMD_SM: 2,
            JobRole.EXCEPTION_SM: 2,
            JobRole.STAGING: 2,
            JobRole.BACKROOM: 8,
        }
    )
    is_busy_day: bool = False

    @property
    def total_slots(self) -> int:
        """Total number of slots in the schedule day."""
        return (self.day_end_minutes - self.day_start_minutes) // self.slot_minutes

    def slot_to_time(self, slot: int) -> time:
        """Convert a slot index to a time object."""
        minutes = self.day_start_minutes + (slot * self.slot_minutes)
        hours, mins = divmod(minutes, 60)
        return time(hour=hours, minute=mins)

    def time_to_slot(self, t: time) -> int:
        """Convert a time object to a slot index."""
        minutes = t.hour * 60 + t.minute
        return (minutes - self.day_start_minutes) // self.slot_minutes


@dataclass
class DaySchedule:
    """Complete schedule output for a single day.

    Attributes:
        schedule_date: Date of the schedule.
        assignments: Dict mapping associate IDs to their shift assignments.
        slot_minutes: Duration of each slot.
        day_start_minutes: Minutes from midnight when day starts.
        day_end_minutes: Minutes from midnight when day ends.
    """

    schedule_date: date
    assignments: dict[str, ShiftAssignment] = field(default_factory=dict)
    slot_minutes: int = 15
    day_start_minutes: int = 300
    day_end_minutes: int = 1320

    @property
    def total_slots(self) -> int:
        """Total slots in the day."""
        return (self.day_end_minutes - self.day_start_minutes) // self.slot_minutes

    def get_coverage_at_slot(self, slot: int) -> int:
        """Count associates on floor at a given slot."""
        return sum(1 for a in self.assignments.values() if a.is_on_floor(slot))

    def get_role_coverage_at_slot(self, slot: int, role: JobRole) -> int:
        """Count associates assigned to a role at a given slot."""
        count = 0
        for assignment in self.assignments.values():
            if assignment.is_on_floor(slot) and assignment.get_role_at_slot(slot) == role:
                count += 1
        return count

    def get_coverage_timeline(self) -> list[int]:
        """Get coverage count for each slot in the day."""
        return [self.get_coverage_at_slot(slot) for slot in range(self.total_slots)]

    def get_on_lunch_at_slot(self, slot: int) -> list[str]:
        """Get list of associate IDs on lunch at a given slot."""
        result = []
        for assoc_id, assignment in self.assignments.items():
            if assignment.lunch_block and assignment.lunch_block.contains_slot(slot):
                result.append(assoc_id)
        return result

    def get_on_break_at_slot(self, slot: int) -> list[str]:
        """Get list of associate IDs on break at a given slot."""
        result = []
        for assoc_id, assignment in self.assignments.items():
            for break_block in assignment.break_blocks:
                if break_block.contains_slot(slot):
                    result.append(assoc_id)
                    break
        return result

    def slot_to_time(self, slot: int) -> time:
        """Convert slot index to time object."""
        minutes = self.day_start_minutes + (slot * self.slot_minutes)
        hours, mins = divmod(minutes, 60)
        return time(hour=hours, minute=mins)
