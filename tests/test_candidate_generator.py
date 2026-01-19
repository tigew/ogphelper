"""Tests for candidate generation."""

from datetime import date

import pytest

from ogphelper.domain.models import (
    Associate,
    Availability,
    JobRole,
    ScheduleRequest,
)
from ogphelper.scheduling.candidate_generator import CandidateGenerator, ShiftCandidate


class TestCandidateGenerator:
    """Tests for CandidateGenerator."""

    @pytest.fixture
    def generator(self):
        """Create a generator with default policies."""
        return CandidateGenerator()

    @pytest.fixture
    def full_day_associate(self):
        """Create an associate available all day."""
        return Associate(
            id="A001",
            name="Test Associate",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
            },
            supervisor_allowed_roles=set(JobRole),
        )

    @pytest.fixture
    def morning_associate(self):
        """Create an associate available only in morning."""
        return Associate(
            id="A002",
            name="Morning Associate",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=24),  # 5 AM - 11 AM
            },
            supervisor_allowed_roles=set(JobRole),
        )

    @pytest.fixture
    def schedule_request(self, full_day_associate):
        """Create a schedule request."""
        return ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=[full_day_associate],
        )

    def test_generates_candidates_for_available_associate(
        self, generator, full_day_associate, schedule_request
    ):
        """Should generate candidates when associate is available."""
        candidates = generator.generate_candidates(full_day_associate, schedule_request)
        assert len(candidates) > 0

    def test_no_candidates_on_day_off(self, generator, schedule_request):
        """Should generate no candidates when associate is off."""
        off_associate = Associate(
            id="A003",
            name="Off Associate",
            availability={
                date(2024, 1, 15): Availability.off_day(),
            },
            supervisor_allowed_roles=set(JobRole),
        )
        candidates = generator.generate_candidates(off_associate, schedule_request)
        assert len(candidates) == 0

    def test_no_candidates_when_not_in_availability_dict(self, generator, schedule_request):
        """Should generate no candidates when date not in availability."""
        no_avail_associate = Associate(
            id="A004",
            name="No Availability",
            availability={},  # Empty availability
            supervisor_allowed_roles=set(JobRole),
        )
        candidates = generator.generate_candidates(no_avail_associate, schedule_request)
        assert len(candidates) == 0

    def test_candidates_respect_minimum_shift(
        self, generator, full_day_associate, schedule_request
    ):
        """All candidates should have at least 4 hours work time."""
        candidates = generator.generate_candidates(full_day_associate, schedule_request)
        for candidate in candidates:
            assert candidate.work_minutes >= 240

    def test_candidates_respect_maximum_shift(
        self, generator, full_day_associate, schedule_request
    ):
        """All candidates should have at most 8 hours work time."""
        candidates = generator.generate_candidates(full_day_associate, schedule_request)
        for candidate in candidates:
            assert candidate.work_minutes <= 480

    def test_candidates_respect_availability_window(
        self, generator, morning_associate
    ):
        """Candidates should fit within availability window."""
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=[morning_associate],
        )
        candidates = generator.generate_candidates(morning_associate, request)

        for candidate in candidates:
            assert candidate.start_slot >= 0
            assert candidate.end_slot <= 24

    def test_candidates_have_correct_lunch_slots(
        self, generator, full_day_associate, schedule_request
    ):
        """Candidates should have correct lunch slots for work duration."""
        candidates = generator.generate_candidates(full_day_associate, schedule_request)

        for candidate in candidates:
            if candidate.work_minutes < 360:
                assert candidate.lunch_slots == 0
            elif candidate.work_minutes < 390:
                assert candidate.lunch_slots == 2  # 30 min = 2 slots
            else:
                assert candidate.lunch_slots == 4  # 60 min = 4 slots

    def test_candidates_have_correct_break_count(
        self, generator, full_day_associate, schedule_request
    ):
        """Candidates should have correct break count for work duration."""
        candidates = generator.generate_candidates(full_day_associate, schedule_request)

        for candidate in candidates:
            if candidate.work_minutes < 300:
                assert candidate.break_count == 0
            elif candidate.work_minutes < 420:
                assert candidate.break_count == 1
            else:
                assert candidate.break_count == 2

    def test_candidates_do_not_exceed_daily_max(self, generator, schedule_request):
        """Candidates should not exceed associate's daily max hours."""
        limited_associate = Associate(
            id="A005",
            name="Limited Hours",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
            },
            max_minutes_per_day=360,  # 6 hours max
            supervisor_allowed_roles=set(JobRole),
        )
        candidates = generator.generate_candidates(limited_associate, schedule_request)

        for candidate in candidates:
            assert candidate.work_minutes <= 360

    def test_generate_all_candidates(self, generator):
        """Should generate candidates for multiple associates."""
        associates = [
            Associate(
                id=f"A{i:03d}",
                name=f"Associate {i}",
                availability={
                    date(2024, 1, 15): Availability(start_slot=0, end_slot=68),
                },
                supervisor_allowed_roles=set(JobRole),
            )
            for i in range(3)
        ]
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=associates,
        )
        all_candidates = generator.generate_all_candidates(request)

        assert len(all_candidates) == 3
        for assoc_id in ["A000", "A001", "A002"]:
            assert assoc_id in all_candidates
            assert len(all_candidates[assoc_id]) > 0

    def test_step_slots_affects_granularity(self, generator, full_day_associate, schedule_request):
        """Larger step_slots should generate fewer candidates."""
        candidates_step1 = generator.generate_candidates(
            full_day_associate, schedule_request, step_slots=1
        )
        candidates_step4 = generator.generate_candidates(
            full_day_associate, schedule_request, step_slots=4
        )

        assert len(candidates_step1) > len(candidates_step4)

    def test_filter_by_work_duration(self, generator, full_day_associate, schedule_request):
        """Should filter candidates by work duration."""
        candidates = generator.generate_candidates(full_day_associate, schedule_request)

        # Filter for 6-7 hour shifts
        filtered = generator.filter_by_work_duration(candidates, min_minutes=360, max_minutes=420)

        for candidate in filtered:
            assert 360 <= candidate.work_minutes <= 420

    def test_filter_by_start_time(self, generator, full_day_associate, schedule_request):
        """Should filter candidates by start time."""
        candidates = generator.generate_candidates(full_day_associate, schedule_request)

        # Filter for starts between 8 AM and 10 AM (slots 12-20)
        filtered = generator.filter_by_start_time(candidates, earliest_slot=12, latest_slot=20)

        for candidate in filtered:
            assert 12 <= candidate.start_slot <= 20

    def test_short_availability_limits_candidates(self, generator):
        """Short availability should limit possible candidates."""
        # Only 5 hours available - can only do 4-hour shift
        short_avail_associate = Associate(
            id="A006",
            name="Short Availability",
            availability={
                date(2024, 1, 15): Availability(start_slot=0, end_slot=20),  # 5 hours
            },
            supervisor_allowed_roles=set(JobRole),
        )
        request = ScheduleRequest(
            schedule_date=date(2024, 1, 15),
            associates=[short_avail_associate],
        )
        candidates = generator.generate_candidates(short_avail_associate, request)

        # Should have some 4-hour candidates but no 6+ hour candidates
        work_durations = {c.work_minutes for c in candidates}
        assert 240 in work_durations  # 4 hours should be possible
        assert 360 not in work_durations  # 6 hours should not be possible

    def test_total_shift_includes_lunch(self, generator, full_day_associate, schedule_request):
        """Total shift time should include work time plus lunch."""
        candidates = generator.generate_candidates(full_day_associate, schedule_request)

        for candidate in candidates:
            expected_total = candidate.work_minutes + (candidate.lunch_slots * 15)
            assert candidate.total_shift_minutes == expected_total
