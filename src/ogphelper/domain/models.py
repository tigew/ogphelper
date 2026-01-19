"""Domain models for the scheduling system.

This module contains all core data structures used throughout the scheduling
system, including associates, time slots, shifts, and schedule outputs.
"""

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from enum import Enum
from typing import Optional


class DaysOffPattern(Enum):
    """Pattern for days off within a week.

    These patterns define how rest days should be distributed
    to ensure fair and healthy work schedules.
    """

    NONE = "none"  # No pattern enforced
    TWO_CONSECUTIVE = "two_consecutive"  # 2 consecutive days off
    ONE_WEEKEND_DAY = "one_weekend_day"  # At least 1 weekend day off
    EVERY_OTHER_DAY = "every_other_day"  # Work every other day max
    CUSTOM = "custom"  # Custom pattern defined elsewhere


class ShiftBlockType(Enum):
    """Types of shift blocks for capacity planning.

    These represent different time periods of the day with
    potentially different staffing requirements.
    """

    MORNING = "morning"  # Early shifts (e.g., 5 AM - 11 AM)
    DAY = "day"  # Mid-day shifts (e.g., 10 AM - 4 PM)
    CLOSING = "closing"  # Late shifts (e.g., 3 PM - 10 PM)


@dataclass
class ShiftStartConfig:
    """Configuration for a specific shift start time with target count.

    Attributes:
        start_slot: The slot index when the shift starts.
        target_count: Target number of associates to start at this time.
        min_count: Minimum associates who should start at this time.
        max_count: Maximum associates who can start at this time.
        label: Human-readable label (e.g., "5:00 AM").
    """

    start_slot: int
    target_count: int
    min_count: int = 0
    max_count: Optional[int] = None  # None means use target_count as max
    label: str = ""

    def __post_init__(self):
        if self.max_count is None:
            self.max_count = self.target_count
        if not self.label:
            # Generate label from slot
            minutes = 300 + self.start_slot * 15  # Assuming 5 AM start, 15 min slots
            hours, mins = divmod(minutes, 60)
            self.label = f"{hours}:{mins:02d}"

    @classmethod
    def from_time(
        cls,
        hour: int,
        minute: int,
        target_count: int,
        min_count: int = 0,
        max_count: Optional[int] = None,
        slot_minutes: int = 15,
        day_start_minutes: int = 300,  # 5 AM
    ) -> "ShiftStartConfig":
        """Create a shift start config from a time.

        Args:
            hour: Hour (24-hour format).
            minute: Minute.
            target_count: Target number of associates.
            min_count: Minimum associates.
            max_count: Maximum associates (defaults to target_count).
            slot_minutes: Duration of each slot in minutes.
            day_start_minutes: Minutes from midnight when schedule starts.
        """
        time_minutes = hour * 60 + minute
        start_slot = (time_minutes - day_start_minutes) // slot_minutes
        label = f"{hour}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
        if hour > 12:
            label = f"{hour - 12}:{minute:02d} PM"
        elif hour == 12:
            label = f"12:{minute:02d} PM"

        return cls(
            start_slot=start_slot,
            target_count=target_count,
            min_count=min_count,
            max_count=max_count,
            label=label,
        )

    @classmethod
    def create_standard_distribution(cls) -> list["ShiftStartConfig"]:
        """Create standard shift start distribution based on typical retail schedule.

        Returns a list representing:
        - 9 at 5:00 AM, 7 at 6:00 AM, 5 at 7:00 AM
        - 2 at 8:00 AM, 1 at 8:30 AM
        - 5 at 9:00 AM, 1 at 9:30 AM
        - 3 at 10:00 AM, 6 at 11:00 AM
        - 3 at 1:00 PM, 5 at 2:00 PM
        """
        return [
            cls.from_time(5, 0, 9),    # 9 at 5:00 AM
            cls.from_time(6, 0, 7),    # 7 at 6:00 AM
            cls.from_time(7, 0, 5),    # 5 at 7:00 AM
            cls.from_time(8, 0, 2),    # 2 at 8:00 AM
            cls.from_time(8, 30, 1),   # 1 at 8:30 AM
            cls.from_time(9, 0, 5),    # 5 at 9:00 AM
            cls.from_time(9, 30, 1),   # 1 at 9:30 AM
            cls.from_time(10, 0, 3),   # 3 at 10:00 AM
            cls.from_time(11, 0, 6),   # 6 at 11:00 AM
            cls.from_time(13, 0, 3),   # 3 at 1:00 PM
            cls.from_time(14, 0, 5),   # 5 at 2:00 PM
        ]

    @classmethod
    def scale_distribution(
        cls,
        base: list["ShiftStartConfig"],
        target_total: int,
    ) -> list["ShiftStartConfig"]:
        """Scale a distribution to a target total while maintaining proportions.

        Args:
            base: Base distribution to scale.
            target_total: Target total number of associates.

        Returns:
            Scaled distribution with configs that have 0 target_count filtered out.
        """
        current_total = sum(cfg.target_count for cfg in base)
        if current_total == 0:
            return base

        scale_factor = target_total / current_total
        scaled = []
        running_total = 0

        # First pass: calculate proportional counts (allowing 0)
        proportional_counts = []
        for cfg in base:
            new_count = round(cfg.target_count * scale_factor)
            proportional_counts.append((cfg, new_count))

        # Distribute remainder to match target_total exactly
        total_so_far = sum(c for _, c in proportional_counts)
        remainder = target_total - total_so_far

        # Add remainder to configs with highest original proportions
        if remainder != 0:
            # Sort by original target_count (descending) to prioritize high-volume times
            sorted_indices = sorted(
                range(len(proportional_counts)),
                key=lambda i: base[i].target_count,
                reverse=True
            )
            for i in range(abs(remainder)):
                idx = sorted_indices[i % len(sorted_indices)]
                cfg, count = proportional_counts[idx]
                if remainder > 0:
                    proportional_counts[idx] = (cfg, count + 1)
                elif count > 0:  # Only decrease if > 0
                    proportional_counts[idx] = (cfg, count - 1)

        # Build final list, filtering out 0-count configs
        for cfg, new_count in proportional_counts:
            if new_count > 0:
                scaled.append(cls(
                    start_slot=cfg.start_slot,
                    target_count=new_count,
                    min_count=max(0, new_count - 1),
                    max_count=new_count + 2,  # Allow some flexibility
                    label=cfg.label,
                ))

        return scaled


@dataclass
class ShiftBlockConfig:
    """Configuration for a shift block with capacity limits.

    Attributes:
        block_type: The type of shift block.
        start_slot: First slot of this block (inclusive).
        end_slot: Last slot of this block (exclusive).
        min_associates: Minimum associates who should start in this block.
        max_associates: Maximum associates who can start in this block.
        target_associates: Target number of associates for this block.
    """

    block_type: ShiftBlockType
    start_slot: int
    end_slot: int
    min_associates: int = 0
    max_associates: int = 999
    target_associates: Optional[int] = None

    @classmethod
    def create_default_blocks(
        cls,
        slot_minutes: int = 15,
        day_start_minutes: int = 300,  # 5 AM
    ) -> list["ShiftBlockConfig"]:
        """Create default shift block configurations.

        Default blocks:
        - Morning: 5 AM - 11 AM (associates starting in this window)
        - Day: 10 AM - 4 PM (associates starting in this window)
        - Closing: 3 PM - 10 PM (associates starting in this window)
        """
        # Calculate slot indices
        morning_start = 0  # 5 AM
        morning_end = (11 * 60 - day_start_minutes) // slot_minutes  # 11 AM
        day_start = (10 * 60 - day_start_minutes) // slot_minutes  # 10 AM
        day_end = (16 * 60 - day_start_minutes) // slot_minutes  # 4 PM
        closing_start = (15 * 60 - day_start_minutes) // slot_minutes  # 3 PM
        closing_end = (22 * 60 - day_start_minutes) // slot_minutes  # 10 PM

        return [
            cls(
                block_type=ShiftBlockType.MORNING,
                start_slot=morning_start,
                end_slot=morning_end,
            ),
            cls(
                block_type=ShiftBlockType.DAY,
                start_slot=day_start,
                end_slot=day_end,
            ),
            cls(
                block_type=ShiftBlockType.CLOSING,
                start_slot=closing_start,
                end_slot=closing_end,
            ),
        ]

    def contains_slot(self, slot: int) -> bool:
        """Check if a slot falls within this block."""
        return self.start_slot <= slot < self.end_slot


class JobRole(Enum):
    """Available job roles for associates."""

    PICKING = "picking"
    GMD_SM = "gmd_sm"
    EXCEPTION_SM = "exception_sm"
    STAGING = "staging"
    BACKROOM = "backroom"
    SR = "sr"  # Seasonal and Regulated


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
        job_caps: Default maximum associates per role per slot.
        time_based_job_caps: Optional time-based caps (slot -> role -> cap).
            Overrides job_caps for specific slots. Use to specify when roles
            should start (e.g., staging starts at 8 AM with cap > 0).
        is_busy_day: If True, allow more aggressive lunch shifting.
        shift_block_configs: Optional shift block configurations with capacity limits.
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
            JobRole.SR: 2,
        }
    )
    time_based_job_caps: Optional[dict[int, dict[JobRole, int]]] = None
    is_busy_day: bool = False
    shift_block_configs: Optional[list[ShiftBlockConfig]] = None
    shift_start_configs: Optional[list[ShiftStartConfig]] = None

    def get_job_cap(self, slot: int, role: JobRole) -> int:
        """Get the job cap for a role at a specific slot.

        Checks time_based_job_caps first, then falls back to job_caps.

        Args:
            slot: The time slot index.
            role: The job role.

        Returns:
            Maximum associates allowed for this role at this slot.
        """
        if self.time_based_job_caps and slot in self.time_based_job_caps:
            slot_caps = self.time_based_job_caps[slot]
            if role in slot_caps:
                return slot_caps[role]
        return self.job_caps.get(role, 999)

    def get_shift_block_for_slot(self, slot: int) -> Optional[ShiftBlockConfig]:
        """Get the shift block configuration for a given start slot."""
        if not self.shift_block_configs:
            return None
        for block in self.shift_block_configs:
            if block.contains_slot(slot):
                return block
        return None

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


@dataclass
class FairnessConfig:
    """Configuration for fairness balancing in weekly scheduling.

    Attributes:
        target_weekly_minutes: Target work minutes per week per associate (None = use max).
        min_weekly_minutes: Minimum work minutes per week (default 0).
        max_hours_variance: Maximum allowed variance in hours between associates.
        balance_daily_starts: Whether to vary shift start times day-to-day.
        prefer_consistent_shifts: Whether to prefer similar shift patterns.
        weight_hours_balance: Weight for hours balancing in scoring (0-1).
        weight_days_balance: Weight for days worked balancing in scoring (0-1).
    """

    target_weekly_minutes: Optional[int] = None
    min_weekly_minutes: int = 0
    max_hours_variance: float = 120.0  # 2 hours variance allowed
    balance_daily_starts: bool = True
    prefer_consistent_shifts: bool = False
    weight_hours_balance: float = 0.7
    weight_days_balance: float = 0.3


@dataclass
class WeeklyScheduleRequest:
    """Request parameters for generating a weekly schedule.

    Attributes:
        start_date: First date of the scheduling week.
        end_date: Last date of the scheduling week (inclusive).
        associates: List of associates to schedule.
        day_start_minutes: Minutes from midnight when schedule day starts.
        day_end_minutes: Minutes from midnight when schedule day ends.
        slot_minutes: Duration of each time slot in minutes.
        job_caps: Default maximum associates per role per slot.
        time_based_job_caps: Optional time-based caps (slot -> role -> cap).
        busy_days: Set of dates that are considered busy days.
        days_off_pattern: Pattern for distributing days off.
        required_days_off: Minimum number of days off per associate per week.
        fairness_config: Configuration for fairness balancing.
        shift_block_configs: Optional shift block configurations with capacity limits.
    """

    start_date: date
    end_date: date
    associates: list[Associate]
    day_start_minutes: int = 300  # 5:00 AM
    day_end_minutes: int = 1320  # 10:00 PM
    slot_minutes: int = 15
    job_caps: dict[JobRole, int] = field(
        default_factory=lambda: {
            JobRole.PICKING: 999,
            JobRole.GMD_SM: 2,
            JobRole.EXCEPTION_SM: 2,
            JobRole.STAGING: 2,
            JobRole.BACKROOM: 8,
            JobRole.SR: 2,
        }
    )
    time_based_job_caps: Optional[dict[int, dict[JobRole, int]]] = None
    busy_days: set[date] = field(default_factory=set)
    days_off_pattern: DaysOffPattern = DaysOffPattern.TWO_CONSECUTIVE
    required_days_off: int = 2
    fairness_config: FairnessConfig = field(default_factory=FairnessConfig)
    shift_block_configs: Optional[list[ShiftBlockConfig]] = None
    shift_start_configs: Optional[list[ShiftStartConfig]] = None

    @property
    def total_slots_per_day(self) -> int:
        """Total number of slots in each schedule day."""
        return (self.day_end_minutes - self.day_start_minutes) // self.slot_minutes

    @property
    def schedule_dates(self) -> list[date]:
        """List of all dates in the scheduling period."""
        dates = []
        current = self.start_date
        while current <= self.end_date:
            dates.append(current)
            current += timedelta(days=1)
        return dates

    @property
    def num_days(self) -> int:
        """Number of days in the scheduling period."""
        return (self.end_date - self.start_date).days + 1

    def is_busy_day(self, d: date) -> bool:
        """Check if a specific date is a busy day."""
        return d in self.busy_days

    def create_day_request(self, schedule_date: date) -> ScheduleRequest:
        """Create a ScheduleRequest for a specific day."""
        return ScheduleRequest(
            schedule_date=schedule_date,
            associates=self.associates,
            day_start_minutes=self.day_start_minutes,
            day_end_minutes=self.day_end_minutes,
            slot_minutes=self.slot_minutes,
            job_caps=self.job_caps,
            time_based_job_caps=self.time_based_job_caps,
            is_busy_day=self.is_busy_day(schedule_date),
            shift_block_configs=self.shift_block_configs,
            shift_start_configs=self.shift_start_configs,
        )


@dataclass
class FairnessMetrics:
    """Metrics for evaluating schedule fairness.

    Attributes:
        hours_per_associate: Dict mapping associate ID to total work hours.
        days_per_associate: Dict mapping associate ID to days worked.
        avg_hours: Average hours across all associates.
        hours_std_dev: Standard deviation of hours.
        hours_variance: Variance in hours between associates.
        min_hours: Minimum hours assigned to any associate.
        max_hours: Maximum hours assigned to any associate.
        fairness_score: Overall fairness score (0-100, higher is fairer).
    """

    hours_per_associate: dict[str, float] = field(default_factory=dict)
    days_per_associate: dict[str, int] = field(default_factory=dict)
    avg_hours: float = 0.0
    hours_std_dev: float = 0.0
    hours_variance: float = 0.0
    min_hours: float = 0.0
    max_hours: float = 0.0
    fairness_score: float = 100.0

    @classmethod
    def calculate(
        cls,
        weekly_minutes: dict[str, int],
        weekly_days: dict[str, int],
    ) -> "FairnessMetrics":
        """Calculate fairness metrics from weekly data."""
        if not weekly_minutes:
            return cls()

        hours = {aid: mins / 60.0 for aid, mins in weekly_minutes.items()}
        values = list(hours.values())

        avg = sum(values) / len(values)
        variance = sum((h - avg) ** 2 for h in values) / len(values)
        std_dev = variance ** 0.5

        # Fairness score: penalize high variance
        # 100 = perfect (no variance), decreases as variance increases
        max_acceptable_variance = 4.0  # 2 hours std dev
        score = max(0.0, 100.0 - (std_dev / max_acceptable_variance) * 100.0)

        return cls(
            hours_per_associate=hours,
            days_per_associate=weekly_days,
            avg_hours=avg,
            hours_std_dev=std_dev,
            hours_variance=variance,
            min_hours=min(values) if values else 0.0,
            max_hours=max(values) if values else 0.0,
            fairness_score=score,
        )


@dataclass
class WeeklySchedule:
    """Complete schedule output for a week.

    Attributes:
        start_date: First date of the schedule week.
        end_date: Last date of the schedule week.
        day_schedules: Dict mapping dates to DaySchedule objects.
        fairness_metrics: Metrics about schedule fairness.
    """

    start_date: date
    end_date: date
    day_schedules: dict[date, DaySchedule] = field(default_factory=dict)
    fairness_metrics: Optional[FairnessMetrics] = None

    @property
    def schedule_dates(self) -> list[date]:
        """List of all dates in the schedule."""
        dates = []
        current = self.start_date
        while current <= self.end_date:
            dates.append(current)
            current += timedelta(days=1)
        return dates

    @property
    def num_days(self) -> int:
        """Number of days in the schedule period."""
        return (self.end_date - self.start_date).days + 1

    def get_associate_weekly_minutes(self, associate_id: str) -> int:
        """Get total work minutes for an associate across the week."""
        total = 0
        for day_schedule in self.day_schedules.values():
            if associate_id in day_schedule.assignments:
                total += day_schedule.assignments[associate_id].work_minutes
        return total

    def get_associate_days_worked(self, associate_id: str) -> int:
        """Get number of days an associate is scheduled to work."""
        count = 0
        for day_schedule in self.day_schedules.values():
            if associate_id in day_schedule.assignments:
                count += 1
        return count

    def get_associate_days_off(self, associate_id: str) -> list[date]:
        """Get list of dates when an associate is not scheduled."""
        days_off = []
        for d in self.schedule_dates:
            if d in self.day_schedules:
                if associate_id not in self.day_schedules[d].assignments:
                    days_off.append(d)
            else:
                days_off.append(d)
        return days_off

    def get_total_coverage_by_day(self) -> dict[date, list[int]]:
        """Get coverage timeline for each day."""
        return {
            d: schedule.get_coverage_timeline()
            for d, schedule in self.day_schedules.items()
        }

    def get_weekly_summary(self) -> dict:
        """Get summary statistics for the weekly schedule."""
        total_work_minutes = 0
        scheduled_shifts = 0
        coverage_by_day = {}

        for d, schedule in self.day_schedules.items():
            day_work = sum(a.work_minutes for a in schedule.assignments.values())
            total_work_minutes += day_work
            scheduled_shifts += len(schedule.assignments)
            timeline = schedule.get_coverage_timeline()
            coverage_by_day[d] = {
                "min": min(timeline) if timeline else 0,
                "max": max(timeline) if timeline else 0,
                "avg": sum(timeline) / len(timeline) if timeline else 0,
            }

        return {
            "total_work_hours": total_work_minutes / 60.0,
            "total_shifts": scheduled_shifts,
            "days_scheduled": len(self.day_schedules),
            "coverage_by_day": coverage_by_day,
            "fairness_metrics": self.fairness_metrics,
        }
