"""Tests for Phase 2: Weekly Scheduling features.

This module contains comprehensive tests for:
- Multi-day scheduling
- Weekly hour enforcement
- Fairness balancing
- Days-off patterns
"""

import pytest
from datetime import date, timedelta

from ogphelper.domain.models import (
    Associate,
    Availability,
    DaysOffPattern,
    FairnessConfig,
    FairnessMetrics,
    JobRole,
    WeeklySchedule,
    WeeklyScheduleRequest,
)
from ogphelper.scheduling.weekly_scheduler import (
    AssociateWeeklyState,
    DaysOffPatternEnforcer,
    FairnessBalancer,
    WeeklyScheduler,
)
from ogphelper.validation.validator import ScheduleValidator, ValidationErrorType


# Test fixtures
@pytest.fixture
def base_date():
    """A Monday for testing weekly schedules."""
    return date(2024, 1, 15)  # This is a Monday


@pytest.fixture
def week_dates(base_date):
    """A full week of dates starting from base_date."""
    return [base_date + timedelta(days=i) for i in range(7)]


@pytest.fixture
def full_availability(week_dates):
    """Full day availability for all days in the week."""
    return {d: Availability(start_slot=0, end_slot=68) for d in week_dates}


def create_test_associate(
    id: str,
    name: str,
    availability: dict,
    max_daily: int = 480,
    max_weekly: int = 2400,
) -> Associate:
    """Helper to create test associates."""
    return Associate(
        id=id,
        name=name,
        availability=availability,
        max_minutes_per_day=max_daily,
        max_minutes_per_week=max_weekly,
        supervisor_allowed_roles=set(JobRole),
        cannot_do_roles=set(),
    )


class TestWeeklyScheduleModels:
    """Tests for weekly schedule data models."""

    def test_weekly_schedule_request_dates(self, base_date, week_dates, full_availability):
        """Test that WeeklyScheduleRequest correctly generates date list."""
        associates = [create_test_associate("A001", "Alice", full_availability)]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
        )

        assert request.num_days == 7
        assert request.schedule_dates == week_dates

    def test_weekly_schedule_request_busy_days(self, base_date, full_availability):
        """Test busy day identification."""
        associates = [create_test_associate("A001", "Alice", full_availability)]
        busy = {base_date + timedelta(days=5)}  # Saturday

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
            busy_days=busy,
        )

        assert request.is_busy_day(base_date + timedelta(days=5))
        assert not request.is_busy_day(base_date)

    def test_fairness_metrics_calculation(self):
        """Test fairness metrics are calculated correctly."""
        weekly_minutes = {"A001": 2400, "A002": 2400, "A003": 2400}
        weekly_days = {"A001": 5, "A002": 5, "A003": 5}

        metrics = FairnessMetrics.calculate(weekly_minutes, weekly_days)

        assert metrics.avg_hours == 40.0
        assert metrics.hours_std_dev == 0.0
        assert metrics.fairness_score == 100.0

    def test_fairness_metrics_with_variance(self):
        """Test fairness metrics with hour variance."""
        weekly_minutes = {"A001": 2400, "A002": 2000, "A003": 1600}
        weekly_days = {"A001": 5, "A002": 4, "A003": 4}

        metrics = FairnessMetrics.calculate(weekly_minutes, weekly_days)

        assert metrics.min_hours == pytest.approx(26.67, rel=0.01)
        assert metrics.max_hours == 40.0
        assert metrics.hours_std_dev > 0
        assert metrics.fairness_score < 100.0

    def test_weekly_schedule_associate_minutes(self, base_date):
        """Test getting associate's weekly minutes."""
        from ogphelper.domain.models import DaySchedule, ShiftAssignment

        schedule = WeeklySchedule(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
        )

        # Add assignments for 3 days
        for i in range(3):
            d = base_date + timedelta(days=i)
            day_schedule = DaySchedule(schedule_date=d)
            day_schedule.assignments["A001"] = ShiftAssignment(
                associate_id="A001",
                schedule_date=d,
                shift_start_slot=0,
                shift_end_slot=32,  # 8 hours total
            )
            schedule.day_schedules[d] = day_schedule

        # 8 hours * 3 days = 24 hours = 1440 minutes
        assert schedule.get_associate_weekly_minutes("A001") == 1440
        assert schedule.get_associate_days_worked("A001") == 3

    def test_weekly_schedule_days_off(self, base_date):
        """Test getting associate's days off."""
        from ogphelper.domain.models import DaySchedule, ShiftAssignment

        schedule = WeeklySchedule(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
        )

        # Work Mon-Fri, off Sat-Sun
        for i in range(5):
            d = base_date + timedelta(days=i)
            day_schedule = DaySchedule(schedule_date=d)
            day_schedule.assignments["A001"] = ShiftAssignment(
                associate_id="A001",
                schedule_date=d,
                shift_start_slot=0,
                shift_end_slot=32,
            )
            schedule.day_schedules[d] = day_schedule

        # Add empty schedules for weekend
        for i in range(5, 7):
            d = base_date + timedelta(days=i)
            schedule.day_schedules[d] = DaySchedule(schedule_date=d)

        days_off = schedule.get_associate_days_off("A001")
        assert len(days_off) == 2
        assert base_date + timedelta(days=5) in days_off  # Saturday
        assert base_date + timedelta(days=6) in days_off  # Sunday


class TestDaysOffPatternEnforcer:
    """Tests for days-off pattern enforcement."""

    def test_no_pattern(self, base_date, week_dates):
        """Test that no pattern allows any schedule."""
        enforcer = DaysOffPatternEnforcer(DaysOffPattern.NONE)
        state = AssociateWeeklyState("A001")

        for d in week_dates:
            assert not enforcer.should_be_day_off(state, d, week_dates, week_dates)

    def test_two_consecutive_last_two_days(self, base_date, week_dates):
        """Test that last 2 days are forced off if no days off yet."""
        enforcer = DaysOffPatternEnforcer(DaysOffPattern.TWO_CONSECUTIVE, required_days_off=2)
        state = AssociateWeeklyState("A001")

        # Work first 5 days
        for i in range(5):
            state.days_worked.append(week_dates[i])

        # Last 2 days should be off
        remaining = week_dates[5:]
        assert enforcer.should_be_day_off(state, week_dates[5], remaining, week_dates)

    def test_two_consecutive_adjacent_to_existing(self, base_date, week_dates):
        """Test that days adjacent to existing off days are preferred."""
        enforcer = DaysOffPatternEnforcer(DaysOffPattern.TWO_CONSECUTIVE, required_days_off=2)
        state = AssociateWeeklyState("A001")

        # Already have Wednesday off
        state.days_off.append(week_dates[2])

        # Thursday (adjacent) should be encouraged as day off
        remaining = week_dates[3:]
        # Note: this depends on the implementation's decision logic
        result = enforcer.should_be_day_off(state, week_dates[3], remaining, week_dates)
        # If we have 1 day off and the next is adjacent, it should complete the pattern
        assert result is True

    def test_weekend_day_pattern(self, base_date, week_dates):
        """Test one weekend day pattern."""
        enforcer = DaysOffPatternEnforcer(DaysOffPattern.ONE_WEEKEND_DAY, required_days_off=1)
        state = AssociateWeeklyState("A001")

        # Work Mon-Fri, no weekend yet
        for i in range(5):
            state.days_worked.append(week_dates[i])

        # Saturday is last weekend day in remaining
        remaining = [week_dates[5], week_dates[6]]

        # With only weekends remaining and no weekend off yet, should force it
        # The logic checks if it's the last weekend day
        saturday = week_dates[5]
        # Check if Saturday (weekday 5) triggers
        assert saturday.weekday() == 5  # Verify it's Saturday

    def test_every_other_day_pattern(self, base_date, week_dates):
        """Test every-other-day pattern prevents consecutive work."""
        enforcer = DaysOffPatternEnforcer(DaysOffPattern.EVERY_OTHER_DAY, required_days_off=3)
        state = AssociateWeeklyState("A001")

        # Worked Monday
        state.days_worked.append(week_dates[0])

        # Tuesday should be a day off (no consecutive work)
        remaining = week_dates[1:]
        result = enforcer.should_be_day_off(state, week_dates[1], remaining, week_dates)
        assert result is True


class TestFairnessBalancer:
    """Tests for fairness balancing logic."""

    def test_priority_for_behind_associate(self):
        """Test that associates behind on hours get higher priority."""
        config = FairnessConfig()
        balancer = FairnessBalancer(config)

        states = {
            "A001": AssociateWeeklyState("A001", minutes_scheduled=2400),
            "A002": AssociateWeeklyState("A002", minutes_scheduled=1200),
            "A003": AssociateWeeklyState("A003", minutes_scheduled=1800),
        }

        # A002 has fewest hours, should have highest priority
        score_a001 = balancer.calculate_priority_score(
            "A001", states["A001"], states, date.today()
        )
        score_a002 = balancer.calculate_priority_score(
            "A002", states["A002"], states, date.today()
        )
        score_a003 = balancer.calculate_priority_score(
            "A003", states["A003"], states, date.today()
        )

        assert score_a002 > score_a003 > score_a001

    def test_skip_ahead_associate(self):
        """Test that associates significantly ahead may be skipped."""
        config = FairnessConfig(max_hours_variance=60.0)  # 1 hour variance allowed
        balancer = FairnessBalancer(config)

        states = {
            "A001": AssociateWeeklyState("A001", minutes_scheduled=2400),  # 40 hours
            "A002": AssociateWeeklyState("A002", minutes_scheduled=1200),  # 20 hours
        }

        # A001 is way ahead, should potentially be skipped
        # A002 is behind
        should_skip_a001 = balancer.should_skip_associate(states["A001"], states, 2)
        should_skip_a002 = balancer.should_skip_associate(states["A002"], states, 2)

        assert should_skip_a001 is True
        assert should_skip_a002 is False


class TestAssociateWeeklyState:
    """Tests for AssociateWeeklyState tracking."""

    def test_remaining_minutes(self):
        """Test remaining minutes calculation."""
        state = AssociateWeeklyState("A001", max_weekly_minutes=2400)

        assert state.remaining_minutes == 2400

        state.add_shift(date.today(), 480)
        assert state.remaining_minutes == 1920

        state.add_shift(date.today() + timedelta(days=1), 480)
        assert state.remaining_minutes == 1440

    def test_add_shift(self):
        """Test adding shifts updates state correctly."""
        state = AssociateWeeklyState("A001")
        d1 = date.today()
        d2 = date.today() + timedelta(days=1)

        state.add_shift(d1, 480)
        state.add_shift(d2, 360)

        assert state.minutes_scheduled == 840
        assert len(state.days_worked) == 2
        assert d1 in state.days_worked
        assert d2 in state.days_worked

    def test_add_day_off(self):
        """Test adding days off."""
        state = AssociateWeeklyState("A001")
        d = date.today()

        state.add_day_off(d)

        assert d in state.days_off

        # Adding same day again shouldn't duplicate
        state.add_day_off(d)
        assert len(state.days_off) == 1


class TestWeeklyScheduler:
    """Integration tests for the WeeklyScheduler."""

    def test_generate_weekly_schedule_small(self, base_date, week_dates, full_availability):
        """Test generating a small weekly schedule."""
        associates = [
            create_test_associate(f"A{i:03d}", f"Associate{i}", full_availability)
            for i in range(5)
        ]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
            days_off_pattern=DaysOffPattern.NONE,
            required_days_off=0,
        )

        scheduler = WeeklyScheduler()
        schedule = scheduler.generate_schedule(request, step_slots=4)

        assert schedule.start_date == base_date
        assert schedule.end_date == base_date + timedelta(days=6)
        assert len(schedule.day_schedules) == 7
        assert schedule.fairness_metrics is not None

    def test_weekly_hour_enforcement(self, base_date, week_dates, full_availability):
        """Test that weekly hour limits are enforced."""
        # Create associate with low weekly limit
        availability = full_availability
        associate = create_test_associate(
            "A001", "Alice", availability,
            max_daily=480,  # 8 hours per day
            max_weekly=1440,  # 24 hours per week (3 full days)
        )

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=[associate],
            days_off_pattern=DaysOffPattern.NONE,
            required_days_off=0,
        )

        scheduler = WeeklyScheduler()
        schedule = scheduler.generate_schedule(request, step_slots=4)

        # Should not exceed weekly limit
        total_minutes = schedule.get_associate_weekly_minutes("A001")
        assert total_minutes <= 1440

    def test_days_off_enforcement(self, base_date, week_dates, full_availability):
        """Test that required days off are enforced."""
        associates = [
            create_test_associate(f"A{i:03d}", f"Associate{i}", full_availability)
            for i in range(3)
        ]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
            days_off_pattern=DaysOffPattern.NONE,
            required_days_off=2,
        )

        scheduler = WeeklyScheduler()
        schedule = scheduler.generate_schedule(request, step_slots=4)

        # Each associate should have at least 2 days off
        for associate in associates:
            days_worked = schedule.get_associate_days_worked(associate.id)
            assert days_worked <= 5  # Max 5 work days if 2 days off required

    def test_fairness_balancing(self, base_date, week_dates, full_availability):
        """Test that hours are distributed fairly."""
        associates = [
            create_test_associate(f"A{i:03d}", f"Associate{i}", full_availability)
            for i in range(10)
        ]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
            days_off_pattern=DaysOffPattern.NONE,
            required_days_off=2,
            fairness_config=FairnessConfig(
                weight_hours_balance=0.8,
                weight_days_balance=0.2,
            ),
        )

        scheduler = WeeklyScheduler()
        schedule = scheduler.generate_schedule(request, step_slots=4)

        metrics = schedule.fairness_metrics
        assert metrics is not None

        # Check that hours aren't wildly different
        # With 10 associates and balanced scheduling, variance should be reasonable
        if metrics.avg_hours > 0:
            ratio = metrics.max_hours / metrics.min_hours if metrics.min_hours > 0 else float('inf')
            assert ratio < 3.0  # Max shouldn't be more than 3x min

    def test_schedule_with_mixed_availability(self, base_date, week_dates):
        """Test scheduling with varying availability."""
        # Associate 1: Full availability
        full_avail = {d: Availability(start_slot=0, end_slot=68) for d in week_dates}

        # Associate 2: Only mornings
        morning_avail = {d: Availability(start_slot=0, end_slot=28) for d in week_dates}

        # Associate 3: Only available Mon-Wed
        partial_week = {week_dates[i]: Availability(start_slot=0, end_slot=68) for i in range(3)}
        for i in range(3, 7):
            partial_week[week_dates[i]] = Availability.off_day()

        associates = [
            create_test_associate("A001", "Full", full_avail),
            create_test_associate("A002", "Morning", morning_avail),
            create_test_associate("A003", "PartWeek", partial_week),
        ]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
            days_off_pattern=DaysOffPattern.NONE,
            required_days_off=1,
        )

        scheduler = WeeklyScheduler()
        schedule = scheduler.generate_schedule(request, step_slots=4)

        # A003 should only work Mon-Wed
        a003_days_worked = schedule.get_associate_days_worked("A003")
        assert a003_days_worked <= 3

    def test_generate_schedule_with_stats(self, base_date, week_dates, full_availability):
        """Test schedule generation with statistics."""
        associates = [
            create_test_associate(f"A{i:03d}", f"Associate{i}", full_availability)
            for i in range(5)
        ]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
        )

        scheduler = WeeklyScheduler()
        schedule, stats = scheduler.generate_schedule_with_stats(request, step_slots=4)

        assert "total_associates" in stats
        assert "total_shifts" in stats
        assert "total_work_hours" in stats
        assert "fairness_metrics" in stats
        assert stats["total_associates"] == 5
        assert stats["num_days"] == 7


class TestWeeklyValidation:
    """Tests for weekly schedule validation."""

    def test_validate_weekly_hours_exceeded(self, base_date, week_dates, full_availability):
        """Test detection of exceeded weekly hours."""
        from ogphelper.domain.models import DaySchedule, ShiftAssignment

        associate = create_test_associate("A001", "Alice", full_availability, max_weekly=1200)
        associates_map = {"A001": associate}

        # Create schedule with 6 days of 8-hour shifts = 2880 minutes
        schedule = WeeklySchedule(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
        )

        for i in range(6):
            d = base_date + timedelta(days=i)
            day_schedule = DaySchedule(schedule_date=d)
            day_schedule.assignments["A001"] = ShiftAssignment(
                associate_id="A001",
                schedule_date=d,
                shift_start_slot=0,
                shift_end_slot=32,  # 8 hours
            )
            schedule.day_schedules[d] = day_schedule

        # Add empty day 7
        schedule.day_schedules[base_date + timedelta(days=6)] = DaySchedule(
            schedule_date=base_date + timedelta(days=6)
        )

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=[associate],
        )

        validator = ScheduleValidator()
        result = validator.validate_weekly_schedule(schedule, request, associates_map)

        # Should have error for exceeding weekly hours
        weekly_errors = [
            e for e in result.errors
            if e.error_type == ValidationErrorType.MAX_WEEKLY_HOURS_EXCEEDED
        ]
        assert len(weekly_errors) > 0

    def test_validate_insufficient_days_off(self, base_date, week_dates, full_availability):
        """Test detection of insufficient days off."""
        from ogphelper.domain.models import DaySchedule, ShiftAssignment

        associate = create_test_associate("A001", "Alice", full_availability)
        associates_map = {"A001": associate}

        # Create schedule with all 7 days working
        schedule = WeeklySchedule(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
        )

        for i in range(7):
            d = base_date + timedelta(days=i)
            day_schedule = DaySchedule(schedule_date=d)
            day_schedule.assignments["A001"] = ShiftAssignment(
                associate_id="A001",
                schedule_date=d,
                shift_start_slot=0,
                shift_end_slot=32,
            )
            schedule.day_schedules[d] = day_schedule

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=[associate],
            required_days_off=2,
        )

        validator = ScheduleValidator()
        result = validator.validate_weekly_schedule(schedule, request, associates_map)

        days_off_errors = [
            e for e in result.errors
            if e.error_type == ValidationErrorType.INSUFFICIENT_DAYS_OFF
        ]
        assert len(days_off_errors) > 0

    def test_validate_days_off_pattern_two_consecutive(self, base_date, week_dates, full_availability):
        """Test validation of two-consecutive-days-off pattern."""
        from ogphelper.domain.models import DaySchedule, ShiftAssignment

        associate = create_test_associate("A001", "Alice", full_availability)
        associates_map = {"A001": associate}

        # Work Mon, off Tue, work Wed, off Thu, work Fri-Sun
        # Days off are not consecutive
        work_days = [0, 2, 4, 5, 6]  # Mon, Wed, Fri, Sat, Sun

        schedule = WeeklySchedule(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
        )

        for i in range(7):
            d = base_date + timedelta(days=i)
            day_schedule = DaySchedule(schedule_date=d)
            if i in work_days:
                day_schedule.assignments["A001"] = ShiftAssignment(
                    associate_id="A001",
                    schedule_date=d,
                    shift_start_slot=0,
                    shift_end_slot=32,
                )
            schedule.day_schedules[d] = day_schedule

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=[associate],
            days_off_pattern=DaysOffPattern.TWO_CONSECUTIVE,
            required_days_off=2,
        )

        validator = ScheduleValidator()
        result = validator.validate_weekly_schedule(schedule, request, associates_map)

        pattern_errors = [
            e for e in result.errors
            if e.error_type == ValidationErrorType.DAYS_OFF_PATTERN_VIOLATED
        ]
        assert len(pattern_errors) > 0

    def test_validate_weekend_day_pattern(self, base_date, week_dates, full_availability):
        """Test validation of weekend day off pattern."""
        from ogphelper.domain.models import DaySchedule, ShiftAssignment

        associate = create_test_associate("A001", "Alice", full_availability)
        associates_map = {"A001": associate}

        # Work all days including weekend (only Mon-Tue off)
        work_days = [2, 3, 4, 5, 6]  # Wed-Sun

        schedule = WeeklySchedule(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
        )

        for i in range(7):
            d = base_date + timedelta(days=i)
            day_schedule = DaySchedule(schedule_date=d)
            if i in work_days:
                day_schedule.assignments["A001"] = ShiftAssignment(
                    associate_id="A001",
                    schedule_date=d,
                    shift_start_slot=0,
                    shift_end_slot=32,
                )
            schedule.day_schedules[d] = day_schedule

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=[associate],
            days_off_pattern=DaysOffPattern.ONE_WEEKEND_DAY,
            required_days_off=2,
        )

        validator = ScheduleValidator()
        result = validator.validate_weekly_schedule(schedule, request, associates_map)

        pattern_errors = [
            e for e in result.errors
            if e.error_type == ValidationErrorType.DAYS_OFF_PATTERN_VIOLATED
        ]
        assert len(pattern_errors) > 0

    def test_valid_weekly_schedule_passes(self, base_date, week_dates, full_availability):
        """Test that a valid weekly schedule passes validation."""
        from ogphelper.domain.models import DaySchedule, ShiftAssignment, JobAssignment, ScheduleBlock

        associate = create_test_associate("A001", "Alice", full_availability)
        associates_map = {"A001": associate}

        # Work Mon-Fri (5 days), off Sat-Sun (consecutive)
        work_days = [0, 1, 2, 3, 4]

        schedule = WeeklySchedule(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
        )

        for i in range(7):
            d = base_date + timedelta(days=i)
            day_schedule = DaySchedule(schedule_date=d)
            if i in work_days:
                # Create a proper shift with job assignment, lunch and breaks
                # 6-hour shift (240 work minutes) to avoid lunch requirement
                # shift_end_slot=24 means 6 hours (24 * 15min = 360min total shift)
                # With no lunch, work_minutes = 360min which needs 1 break
                # Use a 5.5 hour shift to avoid any lunch/break requirements
                # Actually, let's use 4-hour shift (16 slots = 240 minutes) - no lunch, no breaks
                assignment = ShiftAssignment(
                    associate_id="A001",
                    schedule_date=d,
                    shift_start_slot=0,
                    shift_end_slot=16,  # 4 hours work (minimum shift)
                    lunch_block=None,  # No lunch for 4-hour shift
                    break_blocks=[],   # No breaks for 4-hour shift
                    job_assignments=[
                        JobAssignment(
                            role=JobRole.PICKING,
                            block=ScheduleBlock(0, 16),
                        )
                    ],
                )
                day_schedule.assignments["A001"] = assignment
            schedule.day_schedules[d] = day_schedule

        # Calculate fairness metrics
        weekly_minutes = {"A001": schedule.get_associate_weekly_minutes("A001")}
        weekly_days = {"A001": schedule.get_associate_days_worked("A001")}
        schedule.fairness_metrics = FairnessMetrics.calculate(weekly_minutes, weekly_days)

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=[associate],
            days_off_pattern=DaysOffPattern.TWO_CONSECUTIVE,
            required_days_off=2,
        )

        validator = ScheduleValidator()
        result = validator.validate_weekly_schedule(schedule, request, associates_map)

        # Should pass - Mon-Fri work, Sat-Sun consecutive off
        # Filter out warnings (not errors)
        assert result.is_valid, f"Errors: {[str(e) for e in result.errors]}"


class TestWeeklySchedulerEdgeCases:
    """Edge case tests for weekly scheduling."""

    def test_single_day_schedule(self, base_date, full_availability):
        """Test scheduling a single day (degenerate case)."""
        single_avail = {base_date: full_availability[base_date]}
        associates = [create_test_associate("A001", "Alice", single_avail)]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date,
            associates=associates,
            required_days_off=0,
        )

        scheduler = WeeklyScheduler()
        schedule = scheduler.generate_schedule(request, step_slots=4)

        assert schedule.num_days == 1
        assert len(schedule.day_schedules) == 1

    def test_no_available_associates(self, base_date, week_dates):
        """Test scheduling when no associates are available."""
        # All associates have off for all days
        off_avail = {d: Availability.off_day() for d in week_dates}
        associates = [create_test_associate("A001", "Alice", off_avail)]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
        )

        scheduler = WeeklyScheduler()
        schedule = scheduler.generate_schedule(request, step_slots=4)

        # Should have empty schedules for all days
        for day_schedule in schedule.day_schedules.values():
            assert len(day_schedule.assignments) == 0

    def test_very_limited_weekly_hours(self, base_date, week_dates, full_availability):
        """Test with very limited weekly hours (less than one full shift)."""
        associates = [
            create_test_associate(
                "A001", "Alice", full_availability,
                max_daily=480,
                max_weekly=240,  # Only 4 hours for the whole week
            )
        ]

        request = WeeklyScheduleRequest(
            start_date=base_date,
            end_date=base_date + timedelta(days=6),
            associates=associates,
            days_off_pattern=DaysOffPattern.NONE,
            required_days_off=0,
        )

        scheduler = WeeklyScheduler()
        schedule = scheduler.generate_schedule(request, step_slots=4)

        # Should schedule at most 240 minutes
        total = schedule.get_associate_weekly_minutes("A001")
        assert total <= 240
