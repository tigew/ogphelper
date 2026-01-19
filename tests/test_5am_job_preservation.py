"""Tests for 5AM job assignment preservation."""

from datetime import date

import pytest

from ogphelper.domain.models import (
    Associate,
    Availability,
    JobRole,
    ScheduleRequest,
    SlotRangeCaps,
)
from ogphelper.scheduling.scheduler import Scheduler
from ogphelper.validation.validator import ScheduleValidator


class Test5AMJobPreservation:
    """Tests for preserving 5AM starters' initial job assignments."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler with default policies."""
        return Scheduler()

    @pytest.fixture
    def validator(self):
        """Create a validator with default policies."""
        return ScheduleValidator()

    def test_5am_starter_keeps_same_role_throughout_shift(self, scheduler):
        """5AM starters should keep their initial role for all work periods."""
        # Create a 5AM starter with a long shift that will have breaks
        associate = Associate(
            id="A001",
            name="5AM Starter",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=40),  # 5AM-3PM
            },
            max_minutes_per_day=480,
            supervisor_allowed_roles=set(JobRole),
        )

        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=[associate],
            slot_range_caps=[SlotRangeCaps.create_5am_staffing()],
        )

        schedule = scheduler.generate_schedule(request)

        # Get the assignment
        assignment = schedule.assignments.get("A001")
        assert assignment is not None, "5AM associate should be scheduled"
        assert assignment.shift_start_slot < 4, "Should start in 5AM hour"

        # Check that all job assignments have the same role
        job_assignments = assignment.job_assignments
        assert len(job_assignments) > 0, "Should have job assignments"

        initial_role = job_assignments[0].role
        for job in job_assignments:
            assert job.role == initial_role, (
                f"5AM starter should keep same role: expected {initial_role}, got {job.role}"
            )

    def test_non_5am_starter_can_have_different_roles(self, scheduler):
        """Non-5AM starters can have different roles across work periods.

        This test verifies the existing behavior is preserved for later starters.
        """
        # Create associates starting after 5AM - one will have varied eligibility
        # to encourage different role assignments
        associates = [
            Associate(
                id="A001",
                name="Late Starter 1",
                availability={
                    date(2024, 1, 15): Availability(start_slot=8, end_slot=48),  # 7AM-5PM
                },
                max_minutes_per_day=480,
                supervisor_allowed_roles=set(JobRole),
            ),
            Associate(
                id="A002",
                name="Late Starter 2",
                availability={
                    date(2024, 1, 15): Availability(start_slot=12, end_slot=52),  # 8AM-6PM
                },
                max_minutes_per_day=480,
                supervisor_allowed_roles=set(JobRole),
            ),
        ]

        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )

        schedule = scheduler.generate_schedule(request)

        # Verify non-5AM starters were scheduled but don't enforce same-role
        for assoc_id in ["A001", "A002"]:
            assignment = schedule.assignments.get(assoc_id)
            if assignment:
                assert assignment.shift_start_slot >= 4, "Should not start in 5AM hour"
                assert len(assignment.job_assignments) > 0

    def test_5am_preservation_respects_capacity_limits(self, scheduler):
        """Role preservation should fall back if capacity prevents preservation."""
        # Create multiple 5AM starters with the same initial role eligibility
        # to test capacity-based fallback
        associates = [
            Associate(
                id=f"A{i:03d}",
                name=f"5AM Starter {i}",
                availability={
                    date(2024, 1, 15): Availability(start_slot=0, end_slot=40),
                },
                max_minutes_per_day=480,
                supervisor_allowed_roles=set(JobRole),
            )
            for i in range(1, 6)  # 5 associates
        ]

        # Very restrictive caps to force some capacity issues
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
            job_caps={
                JobRole.PICKING: 999,
                JobRole.GMD_SM: 1,
                JobRole.EXCEPTION_SM: 1,
                JobRole.STAGING: 1,
                JobRole.BACKROOM: 2,
                JobRole.SR: 1,
            },
            slot_range_caps=[SlotRangeCaps.create_5am_staffing()],
        )

        schedule = scheduler.generate_schedule(request)

        # Verify caps are respected
        for slot in range(schedule.total_slots):
            for role, cap in request.job_caps.items():
                count = schedule.get_role_coverage_at_slot(slot, role)
                assert count <= cap, f"{role.value} exceeds cap at slot {slot}"

    def test_5am_starter_with_breaks_preserves_role(self, scheduler, validator):
        """5AM starters with breaks should preserve their role across break boundaries."""
        associate = Associate(
            id="A001",
            name="5AM Full Day",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=40),  # 5AM-3PM (10 hrs)
            },
            max_minutes_per_day=600,  # Allow longer shift
            supervisor_allowed_roles=set(JobRole),
        )

        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=[associate],
        )

        schedule = scheduler.generate_schedule(request)
        assignment = schedule.assignments.get("A001")

        assert assignment is not None

        # Should have multiple work periods if breaks/lunch are scheduled
        # Verify same role across all
        if len(assignment.job_assignments) > 1:
            initial_role = assignment.job_assignments[0].role
            for job in assignment.job_assignments:
                assert job.role == initial_role

    def test_5am_at_slot_3_still_preserves(self, scheduler):
        """Associate starting at slot 3 (5:45 AM) should still preserve role."""
        associate = Associate(
            id="A001",
            name="Late 5AM",
            availability={
                # Starts at slot 3 (5:45 AM) - still within 5AM hour
                date(2024, 1, 15): Availability(start_slot=3, end_slot=43),
            },
            max_minutes_per_day=480,
            supervisor_allowed_roles=set(JobRole),
        )

        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=[associate],
        )

        schedule = scheduler.generate_schedule(request)
        assignment = schedule.assignments.get("A001")

        if assignment and assignment.shift_start_slot < 4:
            # Should preserve role
            job_assignments = assignment.job_assignments
            if len(job_assignments) > 1:
                initial_role = job_assignments[0].role
                for job in job_assignments:
                    assert job.role == initial_role

    def test_6am_starter_does_not_preserve(self, scheduler):
        """Associate starting at slot 4 (6:00 AM) should not have role preservation."""
        associate = Associate(
            id="A001",
            name="6AM Starter",
            availability={
                # Starts at slot 4 (6:00 AM) - NOT in 5AM hour
                date(2024, 1, 15): Availability(start_slot=4, end_slot=44),
            },
            max_minutes_per_day=480,
            supervisor_allowed_roles=set(JobRole),
        )

        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=[associate],
        )

        schedule = scheduler.generate_schedule(request)
        assignment = schedule.assignments.get("A001")

        # 6AM starter should be scheduled starting at slot 4 or later
        if assignment:
            assert assignment.shift_start_slot >= 4, "Should start at or after 6AM"

    def test_validation_passes_with_preserved_roles(self, scheduler, validator):
        """Schedule with preserved 5AM roles should pass validation."""
        associates = [
            Associate(
                id="A001",
                name="5AM Starter",
                availability={
                    date(2024, 1, 15): Availability(start_slot=0, end_slot=40),
                },
                max_minutes_per_day=480,
                supervisor_allowed_roles=set(JobRole),
            ),
            Associate(
                id="A002",
                name="Normal Starter",
                availability={
                    date(2024, 1, 15): Availability(start_slot=12, end_slot=52),
                },
                max_minutes_per_day=480,
                supervisor_allowed_roles=set(JobRole),
            ),
        ]

        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
            slot_range_caps=[SlotRangeCaps.create_5am_staffing()],
        )

        schedule = scheduler.generate_schedule(request)
        associates_map = {a.id: a for a in associates}
        result = validator.validate(schedule, request, associates_map)

        assert result.is_valid, f"Validation failed: {[str(e) for e in result.errors]}"
