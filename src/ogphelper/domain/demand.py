"""Demand models for staffing demand curves and optimization.

This module provides data structures for defining staffing demand at each
time slot, supporting demand-aware scheduling optimization.
"""

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from typing import Optional

from ogphelper.domain.models import JobRole


class DemandPriority(Enum):
    """Priority level for demand periods."""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(frozen=True)
class DemandPoint:
    """A single demand point at a specific time.

    Attributes:
        slot: Time slot index (0-67 for 15-min slots, 5AM-10PM).
        min_staff: Minimum required staff (hard constraint).
        target_staff: Ideal staffing level (optimization target).
        max_staff: Maximum useful staff (soft cap for diminishing returns).
        priority: Priority level for this slot.
    """

    slot: int
    min_staff: int = 0
    target_staff: int = 1
    max_staff: int = 99
    priority: DemandPriority = DemandPriority.NORMAL

    def __post_init__(self) -> None:
        if self.min_staff < 0:
            object.__setattr__(self, "min_staff", 0)
        if self.target_staff < self.min_staff:
            object.__setattr__(self, "target_staff", self.min_staff)
        if self.max_staff < self.target_staff:
            object.__setattr__(self, "max_staff", self.target_staff)


@dataclass
class DemandCurve:
    """Staffing demand curve for a single day.

    Defines the required/target staffing levels at each time slot,
    optionally broken down by role.

    Attributes:
        schedule_date: Date this demand curve applies to.
        total_demand: Dict mapping slot index to DemandPoint for total staffing.
        role_demand: Optional per-role demand at each slot.
        priority_periods: List of (start_slot, end_slot, priority) for high-priority windows.
        slot_minutes: Duration of each slot in minutes.
        day_start_minutes: Minutes from midnight when day starts.
    """

    schedule_date: date
    total_demand: dict[int, DemandPoint] = field(default_factory=dict)
    role_demand: dict[int, dict[JobRole, DemandPoint]] = field(default_factory=dict)
    priority_periods: list[tuple[int, int, DemandPriority]] = field(default_factory=list)
    slot_minutes: int = 15
    day_start_minutes: int = 300  # 5 AM

    @property
    def total_slots(self) -> int:
        """Total number of slots in the day (68 for 5AM-10PM, 15-min slots)."""
        return (1320 - self.day_start_minutes) // self.slot_minutes

    def get_demand_at_slot(self, slot: int) -> DemandPoint:
        """Get total demand at a specific slot."""
        if slot in self.total_demand:
            return self.total_demand[slot]
        # Default demand point
        return DemandPoint(slot=slot, min_staff=0, target_staff=1, max_staff=99)

    def get_role_demand_at_slot(self, slot: int, role: JobRole) -> Optional[DemandPoint]:
        """Get demand for a specific role at a slot."""
        if slot in self.role_demand and role in self.role_demand[slot]:
            return self.role_demand[slot][role]
        return None

    def get_priority_at_slot(self, slot: int) -> DemandPriority:
        """Get the priority level at a specific slot."""
        for start, end, priority in self.priority_periods:
            if start <= slot < end:
                return priority
        if slot in self.total_demand:
            return self.total_demand[slot].priority
        return DemandPriority.NORMAL

    def get_min_staff_at_slot(self, slot: int) -> int:
        """Get minimum required staff at a slot."""
        return self.get_demand_at_slot(slot).min_staff

    def get_target_staff_at_slot(self, slot: int) -> int:
        """Get target staff at a slot."""
        return self.get_demand_at_slot(slot).target_staff

    def get_max_staff_at_slot(self, slot: int) -> int:
        """Get maximum useful staff at a slot."""
        return self.get_demand_at_slot(slot).max_staff

    def set_demand(
        self,
        slot: int,
        min_staff: int = 0,
        target_staff: int = 1,
        max_staff: int = 99,
        priority: DemandPriority = DemandPriority.NORMAL,
    ) -> None:
        """Set demand for a specific slot."""
        self.total_demand[slot] = DemandPoint(
            slot=slot,
            min_staff=min_staff,
            target_staff=target_staff,
            max_staff=max_staff,
            priority=priority,
        )

    def set_demand_range(
        self,
        start_slot: int,
        end_slot: int,
        min_staff: int = 0,
        target_staff: int = 1,
        max_staff: int = 99,
        priority: DemandPriority = DemandPriority.NORMAL,
    ) -> None:
        """Set demand for a range of slots."""
        for slot in range(start_slot, end_slot):
            self.set_demand(slot, min_staff, target_staff, max_staff, priority)

    def set_role_demand(
        self,
        slot: int,
        role: JobRole,
        min_staff: int = 0,
        target_staff: int = 1,
        max_staff: int = 99,
    ) -> None:
        """Set demand for a specific role at a slot."""
        if slot not in self.role_demand:
            self.role_demand[slot] = {}
        self.role_demand[slot][role] = DemandPoint(
            slot=slot,
            min_staff=min_staff,
            target_staff=target_staff,
            max_staff=max_staff,
        )

    def add_priority_period(
        self,
        start_slot: int,
        end_slot: int,
        priority: DemandPriority,
    ) -> None:
        """Add a priority period."""
        self.priority_periods.append((start_slot, end_slot, priority))

    @classmethod
    def from_hourly_pattern(
        cls,
        schedule_date: date,
        hourly_targets: dict[int, int],
        slot_minutes: int = 15,
        day_start_minutes: int = 300,
    ) -> "DemandCurve":
        """Create a demand curve from hourly target staffing levels.

        Args:
            schedule_date: Date for the demand curve.
            hourly_targets: Dict mapping hour (0-23) to target staff count.
            slot_minutes: Duration of each slot.
            day_start_minutes: Minutes from midnight when day starts.

        Returns:
            DemandCurve with interpolated demand per slot.
        """
        curve = cls(
            schedule_date=schedule_date,
            slot_minutes=slot_minutes,
            day_start_minutes=day_start_minutes,
        )

        slots_per_hour = 60 // slot_minutes
        start_hour = day_start_minutes // 60  # 5 for 5AM

        for slot in range(curve.total_slots):
            current_hour = start_hour + (slot * slot_minutes) // 60
            target = hourly_targets.get(current_hour, 1)

            # Set min as 60% of target, max as 150% of target
            min_staff = max(0, int(target * 0.6))
            max_staff = int(target * 1.5) + 1

            curve.set_demand(
                slot=slot,
                min_staff=min_staff,
                target_staff=target,
                max_staff=max_staff,
            )

        return curve

    @classmethod
    def create_default(
        cls,
        schedule_date: date,
        base_demand: int = 5,
        peak_demand: int = 10,
        peak_hours: tuple[int, int] = (10, 14),
        slot_minutes: int = 15,
    ) -> "DemandCurve":
        """Create a default demand curve with peak hours.

        Args:
            schedule_date: Date for the demand curve.
            base_demand: Base staffing level during non-peak hours.
            peak_demand: Staffing level during peak hours.
            peak_hours: Tuple of (start_hour, end_hour) for peak period.
            slot_minutes: Duration of each slot.

        Returns:
            DemandCurve with base and peak demand periods.
        """
        curve = cls(schedule_date=schedule_date, slot_minutes=slot_minutes)

        slots_per_hour = 60 // slot_minutes
        day_start_hour = 5  # 5 AM

        for slot in range(curve.total_slots):
            current_hour = day_start_hour + (slot * slot_minutes) // 60

            if peak_hours[0] <= current_hour < peak_hours[1]:
                target = peak_demand
                priority = DemandPriority.HIGH
            else:
                target = base_demand
                priority = DemandPriority.NORMAL

            min_staff = max(0, int(target * 0.6))
            max_staff = int(target * 1.5) + 1

            curve.set_demand(
                slot=slot,
                min_staff=min_staff,
                target_staff=target,
                max_staff=max_staff,
                priority=priority,
            )

        # Add priority period for peak hours
        peak_start_slot = (peak_hours[0] - day_start_hour) * slots_per_hour
        peak_end_slot = (peak_hours[1] - day_start_hour) * slots_per_hour
        curve.add_priority_period(peak_start_slot, peak_end_slot, DemandPriority.HIGH)

        return curve


@dataclass
class DemandProfile:
    """Named demand profile that can be applied to multiple days.

    Useful for defining reusable patterns like "weekday", "weekend",
    "holiday", etc.

    Attributes:
        name: Profile name (e.g., "weekday", "weekend", "black_friday").
        description: Human-readable description.
        hourly_pattern: Dict mapping hour (5-22) to target staff count.
        role_patterns: Optional per-role hourly patterns.
        priority_windows: List of (start_hour, end_hour, priority) tuples.
    """

    name: str
    description: str = ""
    hourly_pattern: dict[int, int] = field(default_factory=dict)
    role_patterns: dict[JobRole, dict[int, int]] = field(default_factory=dict)
    priority_windows: list[tuple[int, int, DemandPriority]] = field(default_factory=list)

    def to_demand_curve(
        self,
        schedule_date: date,
        slot_minutes: int = 15,
        day_start_minutes: int = 300,
    ) -> DemandCurve:
        """Convert this profile to a DemandCurve for a specific date."""
        curve = DemandCurve.from_hourly_pattern(
            schedule_date=schedule_date,
            hourly_targets=self.hourly_pattern,
            slot_minutes=slot_minutes,
            day_start_minutes=day_start_minutes,
        )

        # Add role-specific demand
        slots_per_hour = 60 // slot_minutes
        start_hour = day_start_minutes // 60

        for role, pattern in self.role_patterns.items():
            for hour, target in pattern.items():
                hour_offset = hour - start_hour
                if hour_offset < 0:
                    continue
                for i in range(slots_per_hour):
                    slot = hour_offset * slots_per_hour + i
                    if slot < curve.total_slots:
                        curve.set_role_demand(
                            slot=slot,
                            role=role,
                            min_staff=max(0, int(target * 0.6)),
                            target_staff=target,
                            max_staff=int(target * 1.5) + 1,
                        )

        # Add priority windows
        day_start_hour = day_start_minutes // 60
        for window_start_hour, window_end_hour, priority in self.priority_windows:
            start_slot = (window_start_hour - day_start_hour) * slots_per_hour
            end_slot = (window_end_hour - day_start_hour) * slots_per_hour
            curve.add_priority_period(start_slot, end_slot, priority)

        return curve

    @classmethod
    def create_weekday_profile(cls) -> "DemandProfile":
        """Create a typical weekday demand profile.

        Role staffing eases into needs:
        - 5AM: 1 GMD, 1 Exception, rest picking (no staging/backroom)
        - Gradually increase support roles as day progresses
        """
        return cls(
            name="weekday",
            description="Standard weekday demand pattern with gradual role ramp-up",
            hourly_pattern={
                5: 2,   # Early morning - light staff (1 GMD/SR, 1 Exception, rest picking)
                6: 3,
                7: 5,   # Morning ramp-up
                8: 7,
                9: 9,
                10: 10,  # Mid-morning peak
                11: 10,
                12: 8,   # Lunch dip
                13: 9,
                14: 10,  # Afternoon peak
                15: 9,
                16: 8,
                17: 7,   # Evening wind-down
                18: 6,
                19: 5,
                20: 4,
                21: 3,   # Late evening
            },
            role_patterns={
                # GMD staffing: 1 person from open, stays at 1
                JobRole.GMD_SM: {
                    5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 1,
                    12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1, 21: 1,
                },
                # Exception staffing: 1 person from open, stays at 1
                JobRole.EXCEPTION_SM: {
                    5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 1,
                    12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1, 21: 1,
                },
                # SR staffing: 1 person from open, stays at 1
                JobRole.SR: {
                    5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 1,
                    12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1, 21: 1,
                },
                # No staging in morning - ease into needs
                JobRole.STAGING: {
                    5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0,
                    12: 0, 13: 0, 14: 0, 15: 0, 16: 0, 17: 0, 18: 0, 19: 0, 20: 0, 21: 0,
                },
                # No backroom - ease into needs
                JobRole.BACKROOM: {
                    5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0,
                    12: 0, 13: 0, 14: 0, 15: 0, 16: 0, 17: 0, 18: 0, 19: 0, 20: 0, 21: 0,
                },
            },
            priority_windows=[
                (10, 12, DemandPriority.HIGH),
                (14, 16, DemandPriority.HIGH),
            ],
        )

    @classmethod
    def create_weekend_profile(cls) -> "DemandProfile":
        """Create a typical weekend demand profile.

        Role staffing eases into needs:
        - 5AM: 1 GMD, 1 Exception, rest picking (no staging/backroom)
        - Gradually increase support roles as day progresses
        """
        return cls(
            name="weekend",
            description="Weekend demand pattern with later peak and gradual role ramp-up",
            hourly_pattern={
                5: 1,
                6: 2,
                7: 3,
                8: 5,
                9: 7,
                10: 9,
                11: 11,  # Late morning peak
                12: 12,  # Midday peak
                13: 12,
                14: 11,
                15: 10,
                16: 9,
                17: 8,
                18: 7,
                19: 6,
                20: 4,
                21: 2,
            },
            role_patterns={
                # GMD staffing: 1 person from open, stays at 1
                JobRole.GMD_SM: {
                    5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 1,
                    12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1, 21: 1,
                },
                # Exception staffing: 1 person from open, stays at 1
                JobRole.EXCEPTION_SM: {
                    5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 1,
                    12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1, 21: 1,
                },
                # SR staffing: 1 person from open, stays at 1
                JobRole.SR: {
                    5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 1,
                    12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1, 21: 1,
                },
                # No staging - ease into needs
                JobRole.STAGING: {
                    5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0,
                    12: 0, 13: 0, 14: 0, 15: 0, 16: 0, 17: 0, 18: 0, 19: 0, 20: 0, 21: 0,
                },
                # No backroom - ease into needs
                JobRole.BACKROOM: {
                    5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0,
                    12: 0, 13: 0, 14: 0, 15: 0, 16: 0, 17: 0, 18: 0, 19: 0, 20: 0, 21: 0,
                },
            },
            priority_windows=[
                (11, 15, DemandPriority.HIGH),
            ],
        )

    @classmethod
    def create_high_volume_profile(cls) -> "DemandProfile":
        """Create a high-volume day profile (e.g., holiday rush)."""
        return cls(
            name="high_volume",
            description="High volume day with extended peak hours",
            hourly_pattern={
                5: 4,
                6: 6,
                7: 8,
                8: 12,
                9: 15,
                10: 18,
                11: 20,
                12: 18,
                13: 20,
                14: 20,
                15: 18,
                16: 16,
                17: 14,
                18: 12,
                19: 10,
                20: 8,
                21: 5,
            },
            priority_windows=[
                (9, 16, DemandPriority.CRITICAL),
            ],
        )


@dataclass
class WeeklyDemand:
    """Demand configuration for an entire week.

    Attributes:
        demand_curves: Dict mapping dates to DemandCurve objects.
        default_profile: Default profile to use for days without specific curves.
    """

    demand_curves: dict[date, DemandCurve] = field(default_factory=dict)
    default_profile: Optional[DemandProfile] = None

    def get_demand_for_date(
        self,
        schedule_date: date,
        slot_minutes: int = 15,
    ) -> DemandCurve:
        """Get demand curve for a specific date.

        Returns the specific curve if defined, otherwise generates from
        default profile, or creates a minimal default.
        """
        if schedule_date in self.demand_curves:
            return self.demand_curves[schedule_date]

        if self.default_profile:
            return self.default_profile.to_demand_curve(
                schedule_date=schedule_date,
                slot_minutes=slot_minutes,
            )

        # Minimal default
        return DemandCurve.create_default(schedule_date=schedule_date)

    def set_demand_curve(self, demand_curve: DemandCurve) -> None:
        """Set demand curve for a specific date."""
        self.demand_curves[demand_curve.schedule_date] = demand_curve

    def apply_profile(
        self,
        schedule_date: date,
        profile: DemandProfile,
        slot_minutes: int = 15,
    ) -> None:
        """Apply a profile to create a demand curve for a date."""
        curve = profile.to_demand_curve(schedule_date, slot_minutes)
        self.demand_curves[schedule_date] = curve

    @classmethod
    def create_standard_week(
        cls,
        start_date: date,
        weekday_profile: Optional[DemandProfile] = None,
        weekend_profile: Optional[DemandProfile] = None,
    ) -> "WeeklyDemand":
        """Create a standard week with weekday/weekend patterns.

        Args:
            start_date: First date of the week.
            weekday_profile: Profile for Mon-Fri (uses default if None).
            weekend_profile: Profile for Sat-Sun (uses default if None).

        Returns:
            WeeklyDemand with appropriate profiles applied.
        """
        from datetime import timedelta

        weekday = weekday_profile or DemandProfile.create_weekday_profile()
        weekend = weekend_profile or DemandProfile.create_weekend_profile()

        weekly = cls()

        for i in range(7):
            d = start_date + timedelta(days=i)
            if d.weekday() < 5:  # Mon-Fri
                weekly.apply_profile(d, weekday)
            else:  # Sat-Sun
                weekly.apply_profile(d, weekend)

        return weekly


@dataclass
class DemandMetrics:
    """Metrics for evaluating how well a schedule matches demand.

    Attributes:
        total_demand_minutes: Sum of target demand across all slots.
        total_coverage_minutes: Sum of actual coverage across all slots.
        undercoverage_minutes: Minutes where coverage < min demand.
        overcoverage_minutes: Minutes where coverage > max useful.
        match_score: Percentage of demand satisfied (0-100).
        priority_match_scores: Dict of priority level to match score.
    """

    total_demand_minutes: float = 0.0
    total_coverage_minutes: float = 0.0
    undercoverage_minutes: float = 0.0
    overcoverage_minutes: float = 0.0
    match_score: float = 0.0
    priority_match_scores: dict[DemandPriority, float] = field(default_factory=dict)
    slot_deficits: list[int] = field(default_factory=list)
    slot_surpluses: list[int] = field(default_factory=list)

    @classmethod
    def calculate(
        cls,
        demand_curve: DemandCurve,
        coverage_timeline: list[int],
        slot_minutes: int = 15,
    ) -> "DemandMetrics":
        """Calculate demand metrics from a coverage timeline.

        Args:
            demand_curve: The demand curve to compare against.
            coverage_timeline: List of coverage counts per slot.
            slot_minutes: Duration of each slot in minutes.

        Returns:
            DemandMetrics with calculated values.
        """
        total_demand = 0.0
        total_coverage = 0.0
        undercoverage = 0.0
        overcoverage = 0.0
        slot_deficits = []
        slot_surpluses = []

        priority_demand: dict[DemandPriority, float] = {}
        priority_coverage: dict[DemandPriority, float] = {}

        for slot, coverage in enumerate(coverage_timeline):
            demand_point = demand_curve.get_demand_at_slot(slot)
            priority = demand_curve.get_priority_at_slot(slot)

            target = demand_point.target_staff
            min_staff = demand_point.min_staff
            max_staff = demand_point.max_staff

            # Track by priority
            priority_demand[priority] = priority_demand.get(priority, 0) + target
            priority_coverage[priority] = priority_coverage.get(priority, 0) + min(
                coverage, target
            )

            total_demand += target
            total_coverage += min(coverage, max_staff)

            if coverage < min_staff:
                undercoverage += (min_staff - coverage) * slot_minutes
                slot_deficits.append(slot)
            elif coverage > max_staff:
                overcoverage += (coverage - max_staff) * slot_minutes
                slot_surpluses.append(slot)

        # Calculate match scores
        match_score = (total_coverage / total_demand * 100) if total_demand > 0 else 100.0

        priority_scores = {}
        for priority in DemandPriority:
            if priority in priority_demand and priority_demand[priority] > 0:
                priority_scores[priority] = (
                    priority_coverage[priority] / priority_demand[priority] * 100
                )

        return cls(
            total_demand_minutes=total_demand * slot_minutes,
            total_coverage_minutes=total_coverage * slot_minutes,
            undercoverage_minutes=undercoverage,
            overcoverage_minutes=overcoverage,
            match_score=match_score,
            priority_match_scores=priority_scores,
            slot_deficits=slot_deficits,
            slot_surpluses=slot_surpluses,
        )
