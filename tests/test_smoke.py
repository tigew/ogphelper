"""Smoke tests for end-to-end scheduling flow."""

from datetime import date

import pytest

from ogphelper.domain.models import (
    Associate,
    Availability,
    JobRole,
    Preference,
    ScheduleRequest,
)
from ogphelper.scheduling.scheduler import Scheduler
from ogphelper.validation.validator import ScheduleValidator


class TestSmoke:
    """End-to-end smoke tests for the scheduling system."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler with default policies."""
        return Scheduler()

    @pytest.fixture
    def validator(self):
        """Create a validator with default policies."""
        return ScheduleValidator()

    def _create_test_associates(self, count: int) -> list[Associate]:
        """Create test associates with varied availability."""
        associates = []

        for i in range(count):
            # Vary availability
            if i % 5 == 0:
                # Early shift: 5 AM - 3 PM
                avail = Availability(start_slot=0, end_slot=40)
            elif i % 5 == 1:
                # Mid shift: 8 AM - 6 PM
                avail = Availability(start_slot=12, end_slot=52)
            elif i % 5 == 2:
                # Late shift: 12 PM - 10 PM
                avail = Availability(start_slot=28, end_slot=68)
            else:
                # Full day: 5 AM - 10 PM
                avail = Availability(start_slot=0, end_slot=68)

            # All roles allowed
            allowed_roles = set(JobRole)

            # Some restrictions
            cannot_do = set()
            if i % 7 == 0:
                cannot_do.add(JobRole.BACKROOM)
            if i % 11 == 0:
                cannot_do.add(JobRole.GMD_SM)

            # Some preferences
            preferences = {}
            if i % 3 == 0:
                preferences[JobRole.PICKING] = Preference.PREFER
            if i % 4 == 0:
                preferences[JobRole.BACKROOM] = Preference.AVOID

            associate = Associate(
                id=f"A{i + 1:03d}",
                name=f"Associate {i + 1}",
                availability={date(2024, 1, 15): avail},
                max_minutes_per_day=480,
                max_minutes_per_week=2400,
                supervisor_allowed_roles=allowed_roles,
                cannot_do_roles=cannot_do,
                role_preferences=preferences,
            )
            associates.append(associate)

        return associates

    def test_smoke_small_schedule(self, scheduler, validator):
        """Smoke test: Generate and validate schedule for 10 associates."""
        associates = self._create_test_associates(10)
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        # Generate schedule
        schedule = scheduler.generate_schedule(request)

        # Validate
        associates_map = {a.id: a for a in associates}
        result = validator.validate(schedule, request, associates_map)

        # Should pass validation
        assert result.is_valid, f"Validation failed with errors: {[str(e) for e in result.errors]}"

        # Should have scheduled at least some associates
        assert len(schedule.assignments) > 0

    def test_smoke_medium_schedule(self, scheduler, validator):
        """Smoke test: Generate and validate schedule for 30 associates."""
        associates = self._create_test_associates(30)
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        schedule = scheduler.generate_schedule(request)

        associates_map = {a.id: a for a in associates}
        result = validator.validate(schedule, request, associates_map)

        assert result.is_valid, f"Validation failed with errors: {[str(e) for e in result.errors]}"
        assert len(schedule.assignments) > 0

    def test_smoke_large_schedule(self, scheduler, validator):
        """Smoke test: Generate and validate schedule for 80 associates."""
        associates = self._create_test_associates(80)
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        schedule = scheduler.generate_schedule(request, step_slots=4)  # Coarser for speed

        associates_map = {a.id: a for a in associates}
        result = validator.validate(schedule, request, associates_map)

        assert result.is_valid, f"Validation failed with errors: {[str(e) for e in result.errors]}"
        assert len(schedule.assignments) > 0

    def test_smoke_busy_day(self, scheduler, validator):
        """Smoke test: Schedule on a busy day should allow wider lunch windows."""
        associates = self._create_test_associates(20)
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
            is_busy_day=True,
        )

        schedule = scheduler.generate_schedule(request)

        associates_map = {a.id: a for a in associates}
        result = validator.validate(schedule, request, associates_map)

        assert result.is_valid, f"Validation failed: {[str(e) for e in result.errors]}"

    def test_smoke_all_shifts_have_coverage(self, scheduler, validator):
        """Verify all scheduled associates have job assignments."""
        associates = self._create_test_associates(15)
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        schedule = scheduler.generate_schedule(request)

        for assoc_id, assignment in schedule.assignments.items():
            # Every scheduled associate should have job assignments
            assert len(assignment.job_assignments) > 0, f"No jobs for {assoc_id}"

            # Work minutes should be positive
            assert assignment.work_minutes >= 240, f"Work too short for {assoc_id}"

    def test_smoke_coverage_is_continuous(self, scheduler):
        """Verify there's coverage throughout the day."""
        associates = self._create_test_associates(20)
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        schedule = scheduler.generate_schedule(request)
        coverage = schedule.get_coverage_timeline()

        # Should have some coverage most of the day
        # (Early and late slots may have less coverage)
        mid_day_coverage = coverage[16:52]  # 9 AM - 6 PM
        assert all(c > 0 for c in mid_day_coverage), "Gap in mid-day coverage"

    def test_smoke_role_caps_respected(self, scheduler, validator):
        """Verify role caps are not exceeded."""
        associates = self._create_test_associates(30)
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
            job_caps={
                JobRole.PICKING: 999,
                JobRole.GMD_SM: 2,
                JobRole.EXCEPTION_SM: 2,
                JobRole.STAGING: 2,
                JobRole.BACKROOM: 8,
            },
        )

        schedule = scheduler.generate_schedule(request)

        # Check caps at each slot
        for slot in range(schedule.total_slots):
            for role, cap in request.job_caps.items():
                count = schedule.get_role_coverage_at_slot(slot, role)
                assert count <= cap, f"{role.value} exceeds cap at slot {slot}: {count} > {cap}"

    def test_smoke_stats_are_reasonable(self, scheduler):
        """Verify schedule statistics are reasonable."""
        associates = self._create_test_associates(20)
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        schedule, stats = scheduler.generate_schedule_with_stats(request)

        # Check stats are populated
        assert stats["total_associates"] == 20
        assert stats["scheduled_associates"] > 0
        assert stats["total_work_minutes"] > 0

        # Coverage should be non-negative
        assert stats["min_coverage"] >= 0
        assert stats["max_coverage"] >= stats["min_coverage"]
        assert stats["avg_coverage"] >= 0

    def test_smoke_limited_availability_associates(self, scheduler, validator):
        """Test scheduling with associates who have very limited availability."""
        associates = [
            # Very short availability - may not be schedulable
            Associate(
                id="A001",
                name="Short Avail",
                availability={
                    date(2024, 1, 15): Availability(start_slot=0, end_slot=16),  # Only 4 hours
                },
                supervisor_allowed_roles=set(JobRole),
            ),
            # Normal availability
            Associate(
                id="A002",
                name="Normal",
                availability={
                    date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
                },
                supervisor_allowed_roles=set(JobRole),
            ),
        ]

        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        schedule = scheduler.generate_schedule(request)

        associates_map = {a.id: a for a in associates}
        result = validator.validate(schedule, request, associates_map)

        # Should still be valid even if not all are scheduled
        assert result.is_valid, f"Validation failed: {[str(e) for e in result.errors]}"

    def test_smoke_mixed_role_eligibility(self, scheduler, validator):
        """Test with associates having different role eligibilities."""
        associates = [
            # Picking only
            Associate(
                id="A001",
                name="Picker Only",
                availability={date(2024, 1, 15): Availability(start_slot=0, end_slot=68)},
                supervisor_allowed_roles={JobRole.PICKING},
            ),
            # Everything except backroom
            Associate(
                id="A002",
                name="No Backroom",
                availability={date(2024, 1, 15): Availability(start_slot=0, end_slot=68)},
                supervisor_allowed_roles=set(JobRole),
                cannot_do_roles={JobRole.BACKROOM},
            ),
            # All roles
            Associate(
                id="A003",
                name="All Roles",
                availability={date(2024, 1, 15): Availability(start_slot=0, end_slot=68)},
                supervisor_allowed_roles=set(JobRole),
            ),
        ]

        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        schedule = scheduler.generate_schedule(request)

        associates_map = {a.id: a for a in associates}
        result = validator.validate(schedule, request, associates_map)

        assert result.is_valid, f"Validation failed: {[str(e) for e in result.errors]}"

        # Verify role assignments respect eligibility
        for assoc_id, assignment in schedule.assignments.items():
            assoc = associates_map[assoc_id]
            for job in assignment.job_assignments:
                assert job.role in assoc.supervisor_allowed_roles
                assert job.role not in assoc.cannot_do_roles
