"""Validation module for verifying schedule correctness.

This module provides a single source of truth for all schedule constraints.
Every generated schedule should pass validation before being output.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ogphelper.domain.models import (
    Associate,
    DaySchedule,
    JobRole,
    ScheduleRequest,
    ShiftAssignment,
)
from ogphelper.domain.policies import (
    BreakPolicy,
    DefaultBreakPolicy,
    DefaultLunchPolicy,
    DefaultShiftPolicy,
    LunchPolicy,
    ShiftPolicy,
)


class ValidationErrorType(Enum):
    """Types of validation errors."""

    SHIFT_OUTSIDE_DAY = "shift_outside_day"
    SHIFT_OUTSIDE_AVAILABILITY = "shift_outside_availability"
    WORK_TIME_TOO_SHORT = "work_time_too_short"
    WORK_TIME_TOO_LONG = "work_time_too_long"
    INVALID_LUNCH_DURATION = "invalid_lunch_duration"
    INVALID_BREAK_COUNT = "invalid_break_count"
    INVALID_BREAK_DURATION = "invalid_break_duration"
    ROLE_NOT_ALLOWED_BY_SUPERVISOR = "role_not_allowed_by_supervisor"
    ROLE_CANNOT_DO = "role_cannot_do"
    ROLE_CAP_EXCEEDED = "role_cap_exceeded"
    MAX_DAILY_HOURS_EXCEEDED = "max_daily_hours_exceeded"
    MAX_WEEKLY_HOURS_EXCEEDED = "max_weekly_hours_exceeded"
    LUNCH_OUTSIDE_SHIFT = "lunch_outside_shift"
    BREAK_OUTSIDE_SHIFT = "break_outside_shift"
    BREAK_OVERLAPS_LUNCH = "break_overlaps_lunch"
    BREAKS_OVERLAP = "breaks_overlap"
    NO_JOB_ASSIGNMENT = "no_job_assignment"


@dataclass
class ValidationError:
    """A single validation error."""

    error_type: ValidationErrorType
    message: str
    associate_id: Optional[str] = None
    slot: Optional[int] = None
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        parts = [f"[{self.error_type.value}]"]
        if self.associate_id:
            parts.append(f"Associate {self.associate_id}:")
        parts.append(self.message)
        if self.slot is not None:
            parts.append(f"(slot {self.slot})")
        return " ".join(parts)


@dataclass
class ValidationResult:
    """Result of validating a schedule."""

    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, error: ValidationError) -> None:
        """Add an error and mark as invalid."""
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        """Add a warning (doesn't affect validity)."""
        self.warnings.append(warning)


class ScheduleValidator:
    """Validates schedules against all constraints.

    This is the single source of truth for constraint checking.
    All schedules should be validated before output.

    Example:
        >>> validator = ScheduleValidator()
        >>> result = validator.validate(schedule, request, associates_map)
        >>> if not result.is_valid:
        ...     for error in result.errors:
        ...         print(error)
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

    def validate(
        self,
        schedule: DaySchedule,
        request: ScheduleRequest,
        associates_map: dict[str, Associate],
    ) -> ValidationResult:
        """Validate a complete schedule.

        Args:
            schedule: The schedule to validate.
            request: Original request with constraints.
            associates_map: Dict mapping associate IDs to Associate objects.

        Returns:
            ValidationResult with is_valid flag and any errors.
        """
        result = ValidationResult(is_valid=True)

        # Validate each assignment
        for assoc_id, assignment in schedule.assignments.items():
            associate = associates_map.get(assoc_id)
            if associate is None:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.SHIFT_OUTSIDE_AVAILABILITY,
                        message=f"Unknown associate ID: {assoc_id}",
                        associate_id=assoc_id,
                    )
                )
                continue

            self._validate_assignment(assignment, associate, request, result)

        # Validate role caps across all slots
        self._validate_role_caps(schedule, request, result)

        return result

    def _validate_assignment(
        self,
        assignment: ShiftAssignment,
        associate: Associate,
        request: ScheduleRequest,
        result: ValidationResult,
    ) -> None:
        """Validate a single shift assignment."""
        assoc_id = assignment.associate_id

        # Check shift is within day bounds
        if assignment.shift_start_slot < 0:
            result.add_error(
                ValidationError(
                    error_type=ValidationErrorType.SHIFT_OUTSIDE_DAY,
                    message="Shift starts before day begins",
                    associate_id=assoc_id,
                    slot=assignment.shift_start_slot,
                )
            )

        if assignment.shift_end_slot > request.total_slots:
            result.add_error(
                ValidationError(
                    error_type=ValidationErrorType.SHIFT_OUTSIDE_DAY,
                    message="Shift ends after day ends",
                    associate_id=assoc_id,
                    slot=assignment.shift_end_slot,
                )
            )

        # Check shift is within availability
        availability = associate.get_availability(request.schedule_date)
        if availability.is_off:
            result.add_error(
                ValidationError(
                    error_type=ValidationErrorType.SHIFT_OUTSIDE_AVAILABILITY,
                    message="Associate is off this day",
                    associate_id=assoc_id,
                )
            )
        else:
            if assignment.shift_start_slot < availability.start_slot:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.SHIFT_OUTSIDE_AVAILABILITY,
                        message=(
                            f"Shift starts before availability "
                            f"(slot {assignment.shift_start_slot} < {availability.start_slot})"
                        ),
                        associate_id=assoc_id,
                    )
                )
            if assignment.shift_end_slot > availability.end_slot:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.SHIFT_OUTSIDE_AVAILABILITY,
                        message=(
                            f"Shift ends after availability "
                            f"(slot {assignment.shift_end_slot} > {availability.end_slot})"
                        ),
                        associate_id=assoc_id,
                    )
                )

        # Check work time bounds
        work_minutes = assignment.work_minutes
        if work_minutes < self.shift_policy.min_work_minutes():
            result.add_error(
                ValidationError(
                    error_type=ValidationErrorType.WORK_TIME_TOO_SHORT,
                    message=(
                        f"Work time {work_minutes} min is below minimum "
                        f"{self.shift_policy.min_work_minutes()} min"
                    ),
                    associate_id=assoc_id,
                    details={"work_minutes": work_minutes},
                )
            )

        if work_minutes > self.shift_policy.max_work_minutes():
            result.add_error(
                ValidationError(
                    error_type=ValidationErrorType.WORK_TIME_TOO_LONG,
                    message=(
                        f"Work time {work_minutes} min exceeds maximum "
                        f"{self.shift_policy.max_work_minutes()} min"
                    ),
                    associate_id=assoc_id,
                    details={"work_minutes": work_minutes},
                )
            )

        # Check lunch duration
        expected_lunch = self.lunch_policy.get_lunch_duration(work_minutes)
        actual_lunch = assignment.lunch_minutes
        if actual_lunch != expected_lunch:
            result.add_error(
                ValidationError(
                    error_type=ValidationErrorType.INVALID_LUNCH_DURATION,
                    message=(
                        f"Lunch duration {actual_lunch} min doesn't match "
                        f"required {expected_lunch} min for {work_minutes} min work"
                    ),
                    associate_id=assoc_id,
                    details={
                        "actual_lunch": actual_lunch,
                        "expected_lunch": expected_lunch,
                    },
                )
            )

        # Check lunch is within shift
        if assignment.lunch_block:
            if assignment.lunch_block.start_slot < assignment.shift_start_slot:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.LUNCH_OUTSIDE_SHIFT,
                        message="Lunch starts before shift",
                        associate_id=assoc_id,
                    )
                )
            if assignment.lunch_block.end_slot > assignment.shift_end_slot:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.LUNCH_OUTSIDE_SHIFT,
                        message="Lunch ends after shift",
                        associate_id=assoc_id,
                    )
                )

        # Check break count
        expected_breaks = self.break_policy.get_break_count(work_minutes)
        actual_breaks = len(assignment.break_blocks)
        if actual_breaks != expected_breaks:
            result.add_error(
                ValidationError(
                    error_type=ValidationErrorType.INVALID_BREAK_COUNT,
                    message=(
                        f"Break count {actual_breaks} doesn't match "
                        f"required {expected_breaks} for {work_minutes} min work"
                    ),
                    associate_id=assoc_id,
                    details={
                        "actual_breaks": actual_breaks,
                        "expected_breaks": expected_breaks,
                    },
                )
            )

        # Check break durations
        expected_break_duration = self.break_policy.get_break_duration()
        for i, break_block in enumerate(assignment.break_blocks):
            if break_block.duration_minutes != expected_break_duration:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.INVALID_BREAK_DURATION,
                        message=(
                            f"Break {i + 1} duration {break_block.duration_minutes} min "
                            f"doesn't match required {expected_break_duration} min"
                        ),
                        associate_id=assoc_id,
                    )
                )

            # Check break is within shift
            if break_block.start_slot < assignment.shift_start_slot:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.BREAK_OUTSIDE_SHIFT,
                        message=f"Break {i + 1} starts before shift",
                        associate_id=assoc_id,
                    )
                )
            if break_block.end_slot > assignment.shift_end_slot:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.BREAK_OUTSIDE_SHIFT,
                        message=f"Break {i + 1} ends after shift",
                        associate_id=assoc_id,
                    )
                )

            # Check break doesn't overlap lunch
            if assignment.lunch_block and break_block.overlaps(assignment.lunch_block):
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.BREAK_OVERLAPS_LUNCH,
                        message=f"Break {i + 1} overlaps with lunch",
                        associate_id=assoc_id,
                    )
                )

        # Check breaks don't overlap each other
        for i, break1 in enumerate(assignment.break_blocks):
            for j, break2 in enumerate(assignment.break_blocks[i + 1 :], i + 1):
                if break1.overlaps(break2):
                    result.add_error(
                        ValidationError(
                            error_type=ValidationErrorType.BREAKS_OVERLAP,
                            message=f"Break {i + 1} overlaps with break {j + 1}",
                            associate_id=assoc_id,
                        )
                    )

        # Check job role eligibility
        for job_assignment in assignment.job_assignments:
            role = job_assignment.role

            if role not in associate.supervisor_allowed_roles:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.ROLE_NOT_ALLOWED_BY_SUPERVISOR,
                        message=f"Role {role.value} not allowed by supervisor",
                        associate_id=assoc_id,
                        details={"role": role.value},
                    )
                )

            if role in associate.cannot_do_roles:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.ROLE_CANNOT_DO,
                        message=f"Associate cannot perform role {role.value}",
                        associate_id=assoc_id,
                        details={"role": role.value},
                    )
                )

        # Check daily max hours
        if assignment.work_minutes > associate.max_minutes_per_day:
            result.add_error(
                ValidationError(
                    error_type=ValidationErrorType.MAX_DAILY_HOURS_EXCEEDED,
                    message=(
                        f"Work time {assignment.work_minutes} min exceeds "
                        f"daily max {associate.max_minutes_per_day} min"
                    ),
                    associate_id=assoc_id,
                )
            )

        # Check job assignments cover all work periods
        self._validate_job_coverage(assignment, result)

    def _validate_role_caps(
        self,
        schedule: DaySchedule,
        request: ScheduleRequest,
        result: ValidationResult,
    ) -> None:
        """Validate that role caps are not exceeded at any slot."""
        for slot in range(schedule.total_slots):
            for role in JobRole:
                count = schedule.get_role_coverage_at_slot(slot, role)
                cap = request.job_caps.get(role, 999)

                if count > cap:
                    result.add_error(
                        ValidationError(
                            error_type=ValidationErrorType.ROLE_CAP_EXCEEDED,
                            message=(
                                f"Role {role.value} has {count} assigned "
                                f"but cap is {cap}"
                            ),
                            slot=slot,
                            details={"role": role.value, "count": count, "cap": cap},
                        )
                    )

    def _validate_job_coverage(
        self,
        assignment: ShiftAssignment,
        result: ValidationResult,
    ) -> None:
        """Check that job assignments cover all work slots."""
        # Get all work slots (on-floor slots)
        for slot in range(assignment.shift_start_slot, assignment.shift_end_slot):
            if assignment.is_on_floor(slot):
                role = assignment.get_role_at_slot(slot)
                if role is None:
                    result.add_error(
                        ValidationError(
                            error_type=ValidationErrorType.NO_JOB_ASSIGNMENT,
                            message=f"No job assignment for work slot {slot}",
                            associate_id=assignment.associate_id,
                            slot=slot,
                        )
                    )

    def validate_weekly_hours(
        self,
        schedules: list[DaySchedule],
        associates_map: dict[str, Associate],
    ) -> ValidationResult:
        """Validate weekly hour limits across multiple days.

        Args:
            schedules: List of daily schedules for the week.
            associates_map: Dict mapping associate IDs to Associate objects.

        Returns:
            ValidationResult for weekly validation.
        """
        result = ValidationResult(is_valid=True)

        # Sum up hours per associate
        weekly_minutes: dict[str, int] = {}

        for schedule in schedules:
            for assoc_id, assignment in schedule.assignments.items():
                weekly_minutes[assoc_id] = weekly_minutes.get(assoc_id, 0)
                weekly_minutes[assoc_id] += assignment.work_minutes

        # Check against weekly limits
        for assoc_id, total_minutes in weekly_minutes.items():
            associate = associates_map.get(assoc_id)
            if associate and total_minutes > associate.max_minutes_per_week:
                result.add_error(
                    ValidationError(
                        error_type=ValidationErrorType.MAX_WEEKLY_HOURS_EXCEEDED,
                        message=(
                            f"Weekly work time {total_minutes} min exceeds "
                            f"max {associate.max_minutes_per_week} min"
                        ),
                        associate_id=assoc_id,
                        details={
                            "total_minutes": total_minutes,
                            "max_minutes": associate.max_minutes_per_week,
                        },
                    )
                )

        return result
