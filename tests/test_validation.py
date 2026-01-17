"""Tests for schedule validation."""

from datetime import date

import pytest

from ogphelper.domain.models import (
    Associate,
    Availability,
    DaySchedule,
    JobAssignment,
    JobRole,
    ScheduleBlock,
    ScheduleRequest,
    ShiftAssignment,
)
from ogphelper.validation.validator import (
    ScheduleValidator,
    ValidationErrorType,
)


class TestScheduleValidator:
    """Tests for ScheduleValidator."""

    @pytest.fixture
    def validator(self):
        """Create a validator with default policies."""
        return ScheduleValidator()

    @pytest.fixture
    def valid_associate(self):
        """Create a valid associate."""
        return Associate(
            id="A001",
            name="Valid Associate",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
            },
            max_minutes_per_day=480,
            supervisor_allowed_roles=set(JobRole),
        )

    @pytest.fixture
    def schedule_request(self, valid_associate):
        """Create a schedule request."""
        return ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=[valid_associate],
        )

    @pytest.fixture
    def valid_8hr_assignment(self, valid_associate):
        """Create a valid 8-hour shift assignment."""
        # 8 hours work + 1 hour lunch = 9 hours total = 36 slots
        # Slots 0-36 (5 AM - 2 PM)
        return ShiftAssignment(
            associate_id=valid_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=36,
            lunch_block=ScheduleBlock(14, 18, 15),  # 60-min lunch
            break_blocks=[
                ScheduleBlock(6, 7, 15),   # First break
                ScheduleBlock(26, 27, 15),  # Second break
            ],
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 6, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(7, 14, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(18, 26, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(27, 36, 15)),
            ],
        )

    @pytest.fixture
    def valid_4hr_assignment(self, valid_associate):
        """Create a valid 4-hour shift assignment (no lunch, no breaks)."""
        return ShiftAssignment(
            associate_id=valid_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=16,  # 4 hours
            lunch_block=None,
            break_blocks=[],
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 16, 15)),
            ],
        )

    def test_valid_schedule_passes(
        self, validator, valid_associate, schedule_request, valid_8hr_assignment
    ):
        """A valid schedule should pass validation."""
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={valid_associate.id: valid_8hr_assignment},
        )
        result = validator.validate(schedule, schedule_request, {valid_associate.id: valid_associate})
        assert result.is_valid, f"Errors: {[str(e) for e in result.errors]}"

    def test_valid_4hr_schedule_passes(
        self, validator, valid_associate, schedule_request, valid_4hr_assignment
    ):
        """A valid 4-hour schedule should pass."""
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={valid_associate.id: valid_4hr_assignment},
        )
        result = validator.validate(schedule, schedule_request, {valid_associate.id: valid_associate})
        assert result.is_valid, f"Errors: {[str(e) for e in result.errors]}"

    def test_shift_outside_day_fails(self, validator, valid_associate, schedule_request):
        """Shift ending after day end should fail."""
        assignment = ShiftAssignment(
            associate_id=valid_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=60,
            shift_end_slot=80,  # Beyond 68 slots
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(60, 80, 15)),
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={valid_associate.id: assignment},
        )
        result = validator.validate(schedule, schedule_request, {valid_associate.id: valid_associate})
        assert not result.is_valid
        assert any(e.error_type == ValidationErrorType.SHIFT_OUTSIDE_DAY for e in result.errors)

    def test_shift_outside_availability_fails(self, validator, schedule_request):
        """Shift outside availability window should fail."""
        limited_associate = Associate(
            id="A002",
            name="Limited",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=24),  # Until 11 AM only
            },
            supervisor_allowed_roles=set(JobRole),
        )
        assignment = ShiftAssignment(
            associate_id=limited_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=32,  # Goes past availability
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 32, 15)),
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={limited_associate.id: assignment},
        )
        result = validator.validate(schedule, schedule_request, {limited_associate.id: limited_associate})
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.SHIFT_OUTSIDE_AVAILABILITY
            for e in result.errors
        )

    def test_work_time_too_short_fails(self, validator, valid_associate, schedule_request):
        """Work time under 4 hours should fail."""
        assignment = ShiftAssignment(
            associate_id=valid_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=12,  # 3 hours - too short
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 12, 15)),
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={valid_associate.id: assignment},
        )
        result = validator.validate(schedule, schedule_request, {valid_associate.id: valid_associate})
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.WORK_TIME_TOO_SHORT
            for e in result.errors
        )

    def test_work_time_too_long_fails(self, validator, valid_associate, schedule_request):
        """Work time over 8 hours should fail."""
        assignment = ShiftAssignment(
            associate_id=valid_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=40,  # 10 hours - would be 9 hours work with lunch
            lunch_block=ScheduleBlock(16, 20, 15),  # 1 hour lunch
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 16, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(20, 40, 15)),
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={valid_associate.id: assignment},
        )
        result = validator.validate(schedule, schedule_request, {valid_associate.id: valid_associate})
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.WORK_TIME_TOO_LONG
            for e in result.errors
        )

    def test_invalid_lunch_duration_fails(self, validator, valid_associate, schedule_request):
        """Wrong lunch duration for work time should fail."""
        # 8-hour work should have 60-min lunch, but we give 30-min
        assignment = ShiftAssignment(
            associate_id=valid_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=34,  # 8 hours work + 30 min lunch
            lunch_block=ScheduleBlock(14, 16, 15),  # 30-min lunch (wrong!)
            break_blocks=[
                ScheduleBlock(6, 7, 15),
                ScheduleBlock(26, 27, 15),
            ],
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 6, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(7, 14, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(16, 26, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(27, 34, 15)),
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={valid_associate.id: assignment},
        )
        result = validator.validate(schedule, schedule_request, {valid_associate.id: valid_associate})
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.INVALID_LUNCH_DURATION
            for e in result.errors
        )

    def test_invalid_break_count_fails(self, validator, valid_associate, schedule_request):
        """Wrong break count for work time should fail."""
        # 8-hour work should have 2 breaks, but we give 1
        assignment = ShiftAssignment(
            associate_id=valid_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=36,
            lunch_block=ScheduleBlock(14, 18, 15),
            break_blocks=[
                ScheduleBlock(6, 7, 15),  # Only 1 break (wrong!)
            ],
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 6, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(7, 14, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(18, 36, 15)),
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={valid_associate.id: assignment},
        )
        result = validator.validate(schedule, schedule_request, {valid_associate.id: valid_associate})
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.INVALID_BREAK_COUNT
            for e in result.errors
        )

    def test_unauthorized_role_fails(self, validator, schedule_request):
        """Assigning role not allowed by supervisor should fail."""
        restricted_associate = Associate(
            id="A003",
            name="Restricted",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
            },
            supervisor_allowed_roles={JobRole.PICKING},  # Only picking allowed
        )
        assignment = ShiftAssignment(
            associate_id=restricted_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=16,
            job_assignments=[
                JobAssignment(JobRole.GMD_SM, ScheduleBlock(0, 16, 15)),  # Not allowed!
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={restricted_associate.id: assignment},
        )
        result = validator.validate(
            schedule, schedule_request, {restricted_associate.id: restricted_associate}
        )
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.ROLE_NOT_ALLOWED_BY_SUPERVISOR
            for e in result.errors
        )

    def test_cannot_do_role_fails(self, validator, schedule_request):
        """Assigning role associate cannot do should fail."""
        limited_associate = Associate(
            id="A004",
            name="Limited",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
            },
            supervisor_allowed_roles=set(JobRole),
            cannot_do_roles={JobRole.BACKROOM},  # Cannot do backroom
        )
        assignment = ShiftAssignment(
            associate_id=limited_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=16,
            job_assignments=[
                JobAssignment(JobRole.BACKROOM, ScheduleBlock(0, 16, 15)),  # Cannot do!
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={limited_associate.id: assignment},
        )
        result = validator.validate(
            schedule, schedule_request, {limited_associate.id: limited_associate}
        )
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.ROLE_CANNOT_DO
            for e in result.errors
        )

    def test_role_cap_exceeded_fails(self, validator, valid_associate, schedule_request):
        """Exceeding role cap should fail."""
        # Create multiple associates all assigned to GMD_SM
        associates = [
            Associate(
                id=f"A{i:03d}",
                name=f"Associate {i}",
                availability={
                    date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
                },
                supervisor_allowed_roles=set(JobRole),
            )
            for i in range(5)
        ]

        # Cap is 2 for GMD_SM, but we assign 3
        assignments = {}
        for assoc in associates[:3]:
            assignments[assoc.id] = ShiftAssignment(
                associate_id=assoc.id,
                schedule_date=date(2024, 1, 15),
                shift_start_slot=0,
                shift_end_slot=16,
                job_assignments=[
                    JobAssignment(JobRole.GMD_SM, ScheduleBlock(0, 16, 15)),
                ],
            )

        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments=assignments,
        )
        assoc_map = {a.id: a for a in associates}
        result = validator.validate(schedule, schedule_request, assoc_map)
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.ROLE_CAP_EXCEEDED
            for e in result.errors
        )

    def test_daily_max_hours_exceeded_fails(self, validator, schedule_request):
        """Exceeding daily max hours should fail."""
        limited_associate = Associate(
            id="A005",
            name="Limited Hours",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
            },
            max_minutes_per_day=300,  # 5 hours max
            supervisor_allowed_roles=set(JobRole),
        )
        # Assign 6 hours of work
        assignment = ShiftAssignment(
            associate_id=limited_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=26,  # 6 hours + 30 min lunch
            lunch_block=ScheduleBlock(12, 14, 15),
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 12, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(14, 26, 15)),
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={limited_associate.id: assignment},
        )
        result = validator.validate(
            schedule, schedule_request, {limited_associate.id: limited_associate}
        )
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.MAX_DAILY_HOURS_EXCEEDED
            for e in result.errors
        )

    def test_break_overlaps_lunch_fails(self, validator, valid_associate, schedule_request):
        """Break overlapping lunch should fail."""
        assignment = ShiftAssignment(
            associate_id=valid_associate.id,
            schedule_date=date(2024, 1, 15),
            shift_start_slot=0,
            shift_end_slot=36,
            lunch_block=ScheduleBlock(14, 18, 15),
            break_blocks=[
                ScheduleBlock(16, 17, 15),  # Overlaps lunch!
                ScheduleBlock(26, 27, 15),
            ],
            job_assignments=[
                JobAssignment(JobRole.PICKING, ScheduleBlock(0, 14, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(18, 26, 15)),
                JobAssignment(JobRole.PICKING, ScheduleBlock(27, 36, 15)),
            ],
        )
        schedule = DaySchedule(
            schedule_date=date(2024, 1, 15),
            assignments={valid_associate.id: assignment},
        )
        result = validator.validate(schedule, schedule_request, {valid_associate.id: valid_associate})
        assert not result.is_valid
        assert any(
            e.error_type == ValidationErrorType.BREAK_OVERLAPS_LUNCH
            for e in result.errors
        )
