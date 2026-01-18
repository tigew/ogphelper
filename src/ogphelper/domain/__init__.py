"""Domain models and business rules for scheduling."""

from ogphelper.domain.demand import (
    DemandCurve,
    DemandMetrics,
    DemandPoint,
    DemandPriority,
    DemandProfile,
    WeeklyDemand,
)
from ogphelper.domain.models import (
    Associate,
    Availability,
    DaySchedule,
    DaysOffPattern,
    FairnessConfig,
    FairnessMetrics,
    JobAssignment,
    JobRole,
    Preference,
    ScheduleBlock,
    ScheduleRequest,
    ShiftAssignment,
    TimeSlot,
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

__all__ = [
    # Models
    "Associate",
    "Availability",
    "DaySchedule",
    "DaysOffPattern",
    "FairnessConfig",
    "FairnessMetrics",
    "JobAssignment",
    "JobRole",
    "Preference",
    "ScheduleBlock",
    "ScheduleRequest",
    "ShiftAssignment",
    "TimeSlot",
    "WeeklySchedule",
    "WeeklyScheduleRequest",
    # Demand
    "DemandCurve",
    "DemandMetrics",
    "DemandPoint",
    "DemandPriority",
    "DemandProfile",
    "WeeklyDemand",
    # Policies
    "BreakPolicy",
    "DefaultBreakPolicy",
    "DefaultLunchPolicy",
    "DefaultShiftPolicy",
    "LunchPolicy",
    "ShiftPolicy",
]
