"""Tests for Phase 3: Demand-Aware Optimization.

This module tests:
- Demand models (DemandCurve, DemandProfile, WeeklyDemand)
- OR-Tools CP-SAT solver
- Demand matching optimizer
- Demand-aware weekly scheduler
"""

from datetime import date, timedelta

import pytest

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
    DaysOffPattern,
    FairnessConfig,
    JobRole,
    ScheduleRequest,
    WeeklyScheduleRequest,
)
from ogphelper.scheduling.candidate_generator import CandidateGenerator
from ogphelper.scheduling.cpsat_solver import (
    CPSATSolver,
    DemandAwareSolver,
    OptimizationMode,
    SolverConfig,
    SolverResult,
)
from ogphelper.scheduling.demand_aware_scheduler import (
    DemandAwareConfig,
    DemandAwareWeeklyResult,
    DemandAwareWeeklyScheduler,
    SolverType,
    create_demand_aware_scheduler,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_date() -> date:
    """Return a sample date for testing."""
    return date(2024, 6, 10)  # A Monday


@pytest.fixture
def sample_associates(sample_date: date) -> list[Associate]:
    """Create sample associates for testing."""
    associates = []
    for i in range(5):
        associates.append(
            Associate(
                id=f"A{i+1:03d}",
                name=f"Associate {i+1}",
                availability={sample_date: Availability(start_slot=0, end_slot=68)},
                max_minutes_per_day=480,
                max_minutes_per_week=2400,
                supervisor_allowed_roles=set(JobRole),
                cannot_do_roles=set(),
            )
        )
    return associates


@pytest.fixture
def weekly_associates(sample_date: date) -> list[Associate]:
    """Create associates with weekly availability."""
    associates = []
    dates = [sample_date + timedelta(days=i) for i in range(7)]

    for i in range(5):
        availability = {}
        for d in dates:
            if i % 5 == 0 and d.weekday() == 0:
                availability[d] = Availability.off_day()
            else:
                availability[d] = Availability(start_slot=0, end_slot=68)

        associates.append(
            Associate(
                id=f"A{i+1:03d}",
                name=f"Associate {i+1}",
                availability=availability,
                max_minutes_per_day=480,
                max_minutes_per_week=2400,
                supervisor_allowed_roles=set(JobRole),
            )
        )
    return associates


# ============================================================================
# Demand Models Tests
# ============================================================================


class TestDemandPoint:
    """Tests for DemandPoint."""

    def test_basic_creation(self) -> None:
        """Test basic demand point creation."""
        point = DemandPoint(slot=10, min_staff=2, target_staff=5, max_staff=8)
        assert point.slot == 10
        assert point.min_staff == 2
        assert point.target_staff == 5
        assert point.max_staff == 8
        assert point.priority == DemandPriority.NORMAL

    def test_auto_correction_min(self) -> None:
        """Test that min_staff is auto-corrected to >= 0."""
        point = DemandPoint(slot=0, min_staff=-5, target_staff=3)
        assert point.min_staff == 0

    def test_auto_correction_target(self) -> None:
        """Test that target is auto-corrected to >= min."""
        point = DemandPoint(slot=0, min_staff=5, target_staff=3)
        assert point.target_staff == 5  # Should be raised to min

    def test_auto_correction_max(self) -> None:
        """Test that max is auto-corrected to >= target."""
        point = DemandPoint(slot=0, min_staff=2, target_staff=8, max_staff=5)
        assert point.max_staff == 8  # Should be raised to target


class TestDemandCurve:
    """Tests for DemandCurve."""

    def test_basic_creation(self, sample_date: date) -> None:
        """Test basic demand curve creation."""
        curve = DemandCurve(schedule_date=sample_date)
        assert curve.schedule_date == sample_date
        assert curve.total_slots == 68

    def test_set_demand(self, sample_date: date) -> None:
        """Test setting demand for a slot."""
        curve = DemandCurve(schedule_date=sample_date)
        curve.set_demand(slot=10, min_staff=2, target_staff=5, max_staff=8)

        point = curve.get_demand_at_slot(10)
        assert point.min_staff == 2
        assert point.target_staff == 5
        assert point.max_staff == 8

    def test_set_demand_range(self, sample_date: date) -> None:
        """Test setting demand for a range of slots."""
        curve = DemandCurve(schedule_date=sample_date)
        curve.set_demand_range(
            start_slot=20,
            end_slot=30,
            min_staff=3,
            target_staff=6,
            priority=DemandPriority.HIGH,
        )

        for slot in range(20, 30):
            point = curve.get_demand_at_slot(slot)
            assert point.target_staff == 6
            assert curve.get_priority_at_slot(slot) == DemandPriority.HIGH

    def test_from_hourly_pattern(self, sample_date: date) -> None:
        """Test creating curve from hourly pattern."""
        hourly = {
            5: 2,
            6: 3,
            7: 5,
            8: 7,
            9: 10,
            10: 12,
        }
        curve = DemandCurve.from_hourly_pattern(sample_date, hourly)

        # Check slot 0 (5:00 AM) - should have target 2
        assert curve.get_target_staff_at_slot(0) == 2

        # Check slot 20 (10:00 AM) - should have target 12
        assert curve.get_target_staff_at_slot(20) == 12

    def test_create_default(self, sample_date: date) -> None:
        """Test creating default demand curve."""
        curve = DemandCurve.create_default(
            sample_date,
            base_demand=5,
            peak_demand=10,
            peak_hours=(10, 14),
        )

        # Morning (8 AM, slot 12) - base demand
        assert curve.get_target_staff_at_slot(12) == 5

        # Peak (11 AM, slot 24) - peak demand
        assert curve.get_target_staff_at_slot(24) == 10


class TestDemandProfile:
    """Tests for DemandProfile."""

    def test_weekday_profile(self) -> None:
        """Test weekday demand profile."""
        profile = DemandProfile.create_weekday_profile()
        assert profile.name == "weekday"
        assert len(profile.hourly_pattern) > 0
        assert 10 in profile.hourly_pattern  # Has 10 AM demand

    def test_weekend_profile(self) -> None:
        """Test weekend demand profile."""
        profile = DemandProfile.create_weekend_profile()
        assert profile.name == "weekend"
        assert profile.hourly_pattern.get(12, 0) >= profile.hourly_pattern.get(8, 0)

    def test_high_volume_profile(self) -> None:
        """Test high volume demand profile."""
        profile = DemandProfile.create_high_volume_profile()
        assert profile.name == "high_volume"
        # High volume should have higher demands
        weekday = DemandProfile.create_weekday_profile()
        assert profile.hourly_pattern.get(10, 0) > weekday.hourly_pattern.get(10, 0)

    def test_to_demand_curve(self, sample_date: date) -> None:
        """Test converting profile to curve."""
        profile = DemandProfile.create_weekday_profile()
        curve = profile.to_demand_curve(sample_date)

        assert curve.schedule_date == sample_date
        assert curve.total_slots == 68


class TestDemandProfilePriorityWindows:
    """Tests for priority window slot offset calculation."""

    def test_priority_window_correct_offset(self, sample_date: date) -> None:
        """Test that priority windows are placed at correct slot offsets."""
        profile = DemandProfile(
            name="test",
            hourly_pattern={h: 5 for h in range(5, 22)},
            priority_windows=[
                (10, 12, DemandPriority.HIGH),  # 10 AM - 12 PM
                (14, 16, DemandPriority.CRITICAL),  # 2 PM - 4 PM
            ],
        )

        curve = profile.to_demand_curve(sample_date)

        # Slot 0 = 5:00 AM, so 10 AM = slot 20 (5 hours * 4 slots/hour)
        # 12 PM = slot 28, 2 PM = slot 36, 4 PM = slot 44

        # Before peak (9 AM = slot 16) should be NORMAL
        assert curve.get_priority_at_slot(16) == DemandPriority.NORMAL

        # During first peak (10:30 AM = slot 22) should be HIGH
        assert curve.get_priority_at_slot(22) == DemandPriority.HIGH

        # Between peaks (1 PM = slot 32) should be NORMAL
        assert curve.get_priority_at_slot(32) == DemandPriority.NORMAL

        # During second peak (3 PM = slot 40) should be CRITICAL
        assert curve.get_priority_at_slot(40) == DemandPriority.CRITICAL

        # After peaks (5 PM = slot 48) should be NORMAL
        assert curve.get_priority_at_slot(48) == DemandPriority.NORMAL

    def test_weekday_profile_priority_windows(self, sample_date: date) -> None:
        """Test that weekday profile has correctly placed priority windows."""
        profile = DemandProfile.create_weekday_profile()
        curve = profile.to_demand_curve(sample_date)

        # Weekday profile has priority windows at (10, 12) and (14, 16)
        # 10 AM = slot 20, 12 PM = slot 28, 2 PM = slot 36, 4 PM = slot 44

        # Slot 20 (10 AM) should be HIGH
        assert curve.get_priority_at_slot(20) == DemandPriority.HIGH

        # Slot 36 (2 PM) should be HIGH
        assert curve.get_priority_at_slot(36) == DemandPriority.HIGH


class TestWeeklyDemand:
    """Tests for WeeklyDemand."""

    def test_create_standard_week(self, sample_date: date) -> None:
        """Test creating a standard week demand."""
        weekly = WeeklyDemand.create_standard_week(sample_date)

        assert len(weekly.demand_curves) == 7

        # Monday should have weekday pattern
        monday = sample_date
        monday_curve = weekly.get_demand_for_date(monday)
        assert monday_curve is not None

        # Saturday should have weekend pattern
        saturday = sample_date + timedelta(days=5)
        saturday_curve = weekly.get_demand_for_date(saturday)
        assert saturday_curve is not None

    def test_apply_profile(self, sample_date: date) -> None:
        """Test applying a custom profile."""
        weekly = WeeklyDemand()
        profile = DemandProfile.create_high_volume_profile()
        weekly.apply_profile(sample_date, profile)

        curve = weekly.get_demand_for_date(sample_date)
        # Should have high volume targets
        assert curve.get_target_staff_at_slot(20) >= 10


class TestDemandMetrics:
    """Tests for DemandMetrics."""

    def test_perfect_match(self, sample_date: date) -> None:
        """Test metrics for perfect demand match."""
        curve = DemandCurve.create_default(
            sample_date,
            base_demand=5,
            peak_demand=5,
        )
        coverage = [5] * 68  # Perfect coverage

        metrics = DemandMetrics.calculate(curve, coverage)
        assert metrics.match_score == 100.0
        assert metrics.undercoverage_minutes == 0

    def test_undercoverage(self, sample_date: date) -> None:
        """Test metrics with undercoverage."""
        curve = DemandCurve(schedule_date=sample_date)
        curve.set_demand_range(0, 68, min_staff=5, target_staff=10)

        coverage = [3] * 68  # Under min

        metrics = DemandMetrics.calculate(curve, coverage)
        assert metrics.undercoverage_minutes > 0
        assert metrics.match_score < 100.0

    def test_overcoverage(self, sample_date: date) -> None:
        """Test metrics with overcoverage."""
        curve = DemandCurve(schedule_date=sample_date)
        curve.set_demand_range(0, 68, min_staff=2, target_staff=5, max_staff=8)

        coverage = [15] * 68  # Over max

        metrics = DemandMetrics.calculate(curve, coverage)
        assert metrics.overcoverage_minutes > 0


# ============================================================================
# CP-SAT Solver Tests
# ============================================================================


class TestSolverConfig:
    """Tests for SolverConfig."""

    def test_default_config(self) -> None:
        """Test default solver configuration."""
        config = SolverConfig()
        assert config.time_limit_seconds == 30.0
        assert config.optimization_mode == OptimizationMode.BALANCED
        assert config.demand_weight == 40
        assert config.coverage_weight == 30

    def test_custom_config(self) -> None:
        """Test custom solver configuration."""
        config = SolverConfig(
            time_limit_seconds=60.0,
            optimization_mode=OptimizationMode.MATCH_DEMAND,
            demand_weight=80,
        )
        assert config.time_limit_seconds == 60.0
        assert config.optimization_mode == OptimizationMode.MATCH_DEMAND
        assert config.demand_weight == 80


class TestCPSATSolver:
    """Tests for CPSATSolver."""

    def test_basic_solve(
        self, sample_date: date, sample_associates: list[Associate]
    ) -> None:
        """Test basic schedule solving with CP-SAT."""
        request = ScheduleRequest(
            schedule_date=sample_date,
            associates=sample_associates,
        )

        generator = CandidateGenerator()
        candidates = generator.generate_all_candidates(request)
        associates_map = {a.id: a for a in sample_associates}

        solver = CPSATSolver()
        result = solver.solve(request, candidates, associates_map)

        assert result.is_feasible
        assert result.schedule is not None
        assert len(result.schedule.assignments) > 0

    def test_solve_with_demand(
        self, sample_date: date, sample_associates: list[Associate]
    ) -> None:
        """Test solving with a demand curve."""
        request = ScheduleRequest(
            schedule_date=sample_date,
            associates=sample_associates,
        )

        demand_curve = DemandCurve.create_default(
            sample_date,
            base_demand=2,
            peak_demand=4,
        )

        generator = CandidateGenerator()
        candidates = generator.generate_all_candidates(request)
        associates_map = {a.id: a for a in sample_associates}

        config = SolverConfig(
            optimization_mode=OptimizationMode.MATCH_DEMAND,
            time_limit_seconds=10.0,
        )
        solver = CPSATSolver(config=config)
        result = solver.solve(request, candidates, associates_map, demand_curve)

        assert result.is_feasible
        assert result.schedule is not None

    def test_solver_result_properties(self) -> None:
        """Test SolverResult properties."""
        result = SolverResult(
            schedule=None,
            status="OPTIMAL",
            objective_value=100,
            solve_time_seconds=1.5,
        )
        assert result.is_optimal
        assert result.is_feasible

        result2 = SolverResult(schedule=None, status="INFEASIBLE")
        assert not result2.is_optimal
        assert not result2.is_feasible


class TestDemandAwareSolver:
    """Tests for DemandAwareSolver."""

    def test_solve(
        self, sample_date: date, sample_associates: list[Associate]
    ) -> None:
        """Test demand-aware solving."""
        request = ScheduleRequest(
            schedule_date=sample_date,
            associates=sample_associates,
        )

        demand_curve = DemandCurve.create_default(sample_date, base_demand=3)

        solver = DemandAwareSolver()
        result = solver.solve(request, demand_curve)

        assert result.is_feasible
        assert result.schedule is not None

    def test_solve_with_fallback(
        self, sample_date: date, sample_associates: list[Associate]
    ) -> None:
        """Test solve_with_fallback method."""
        request = ScheduleRequest(
            schedule_date=sample_date,
            associates=sample_associates,
        )

        solver = DemandAwareSolver()
        schedule = solver.solve_with_fallback(request)

        assert schedule is not None
        assert len(schedule.assignments) > 0


# ============================================================================
# Demand-Aware Weekly Scheduler Tests
# ============================================================================


class TestDemandAwareConfig:
    """Tests for DemandAwareConfig."""

    def test_default_config(self) -> None:
        """Test default configuration."""
        config = DemandAwareConfig()
        assert config.solver_type == SolverType.HYBRID
        assert config.auto_generate_demand is True
        assert config.track_demand_metrics is True

    def test_custom_config(self) -> None:
        """Test custom configuration."""
        config = DemandAwareConfig(
            solver_type=SolverType.CPSAT,
            auto_generate_demand=False,
        )
        assert config.solver_type == SolverType.CPSAT
        assert config.auto_generate_demand is False


class TestDemandAwareWeeklyScheduler:
    """Tests for DemandAwareWeeklyScheduler."""

    def test_generate_schedule(
        self, sample_date: date, weekly_associates: list[Associate]
    ) -> None:
        """Test generating a demand-aware weekly schedule."""
        end_date = sample_date + timedelta(days=6)

        request = WeeklyScheduleRequest(
            start_date=sample_date,
            end_date=end_date,
            associates=weekly_associates,
            days_off_pattern=DaysOffPattern.NONE,
        )

        config = DemandAwareConfig(
            solver_type=SolverType.HEURISTIC,  # Use heuristic for faster tests
            auto_generate_demand=True,
        )
        scheduler = DemandAwareWeeklyScheduler(config=config)
        result = scheduler.generate_schedule(request)

        assert result.schedule is not None
        assert len(result.schedule.day_schedules) == 7
        assert result.overall_match_score > 0

    def test_generate_with_custom_demand(
        self, sample_date: date, weekly_associates: list[Associate]
    ) -> None:
        """Test generating with custom demand curves."""
        end_date = sample_date + timedelta(days=6)

        request = WeeklyScheduleRequest(
            start_date=sample_date,
            end_date=end_date,
            associates=weekly_associates,
            days_off_pattern=DaysOffPattern.NONE,
        )

        weekly_demand = WeeklyDemand.create_standard_week(sample_date)

        config = DemandAwareConfig(
            solver_type=SolverType.HEURISTIC,
            track_demand_metrics=True,
        )
        scheduler = DemandAwareWeeklyScheduler(config=config)
        result = scheduler.generate_schedule(request, weekly_demand)

        assert result.schedule is not None
        # Metrics calculated for days with shifts (may be less than 7 if associates run out of hours)
        assert len(result.demand_metrics) >= 5

    def test_hybrid_solver_fallback(
        self, sample_date: date, weekly_associates: list[Associate]
    ) -> None:
        """Test that hybrid solver falls back when needed."""
        end_date = sample_date + timedelta(days=6)

        request = WeeklyScheduleRequest(
            start_date=sample_date,
            end_date=end_date,
            associates=weekly_associates,
        )

        config = DemandAwareConfig(
            solver_type=SolverType.HYBRID,
            solver_config=SolverConfig(time_limit_seconds=5.0),
        )
        scheduler = DemandAwareWeeklyScheduler(config=config)
        result = scheduler.generate_schedule(request)

        assert result.schedule is not None
        # Check that solver stats are tracked
        assert len(result.solver_stats) > 0

    def test_fairness_maintained(
        self, sample_date: date, weekly_associates: list[Associate]
    ) -> None:
        """Test that fairness is maintained in demand-aware scheduling."""
        end_date = sample_date + timedelta(days=6)

        request = WeeklyScheduleRequest(
            start_date=sample_date,
            end_date=end_date,
            associates=weekly_associates,
            days_off_pattern=DaysOffPattern.TWO_CONSECUTIVE,
            fairness_config=FairnessConfig(
                weight_hours_balance=0.7,
                weight_days_balance=0.3,
            ),
        )

        config = DemandAwareConfig(solver_type=SolverType.HEURISTIC)
        scheduler = DemandAwareWeeklyScheduler(config=config)
        result = scheduler.generate_schedule(request)

        assert result.schedule.fairness_metrics is not None
        # Fairness score should be reasonable
        assert result.schedule.fairness_metrics.fairness_score >= 0


class TestDemandAwareWeeklyResult:
    """Tests for DemandAwareWeeklyResult."""

    def test_get_summary(self, sample_date: date, weekly_associates: list[Associate]) -> None:
        """Test result summary generation."""
        end_date = sample_date + timedelta(days=6)

        request = WeeklyScheduleRequest(
            start_date=sample_date,
            end_date=end_date,
            associates=weekly_associates,
        )

        config = DemandAwareConfig(solver_type=SolverType.HEURISTIC)
        scheduler = DemandAwareWeeklyScheduler(config=config)
        result = scheduler.generate_schedule(request)

        summary = result.get_summary()
        assert "num_days" in summary
        assert "total_shifts" in summary
        assert "overall_match_score" in summary
        assert summary["num_days"] == 7


class TestFactoryFunction:
    """Tests for factory function."""

    def test_create_demand_aware_scheduler(self) -> None:
        """Test factory function for creating schedulers."""
        scheduler = create_demand_aware_scheduler(
            solver_type="cpsat",
            time_limit=60.0,
            optimization_mode="match_demand",
        )

        assert isinstance(scheduler, DemandAwareWeeklyScheduler)
        assert scheduler.config.solver_type == SolverType.CPSAT

    def test_create_with_defaults(self) -> None:
        """Test factory function with defaults."""
        scheduler = create_demand_aware_scheduler()

        assert scheduler.config.solver_type == SolverType.HYBRID
        assert scheduler.config.solver_config.time_limit_seconds == 30.0


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for the full demand-aware scheduling pipeline."""

    def test_full_pipeline(
        self, sample_date: date, weekly_associates: list[Associate]
    ) -> None:
        """Test the full scheduling pipeline."""
        end_date = sample_date + timedelta(days=6)

        # Create request
        request = WeeklyScheduleRequest(
            start_date=sample_date,
            end_date=end_date,
            associates=weekly_associates,
            days_off_pattern=DaysOffPattern.TWO_CONSECUTIVE,
            required_days_off=2,
        )

        # Create demand
        weekly_demand = WeeklyDemand.create_standard_week(sample_date)

        # Configure scheduler
        config = DemandAwareConfig(
            solver_type=SolverType.HEURISTIC,
            weekly_demand=weekly_demand,
            track_demand_metrics=True,
        )

        # Generate schedule
        scheduler = DemandAwareWeeklyScheduler(config=config)
        result = scheduler.generate_schedule(request, weekly_demand)

        # Verify result
        assert result.schedule is not None
        assert len(result.schedule.day_schedules) == 7
        assert len(result.demand_metrics) == 7
        assert result.overall_match_score >= 0

        # Verify each day has assignments
        for d in result.schedule.schedule_dates:
            day_schedule = result.schedule.day_schedules.get(d)
            assert day_schedule is not None

        # Verify fairness
        assert result.schedule.fairness_metrics is not None

    def test_high_volume_day(
        self, sample_date: date, weekly_associates: list[Associate]
    ) -> None:
        """Test scheduling with a high-volume day."""
        end_date = sample_date + timedelta(days=6)

        request = WeeklyScheduleRequest(
            start_date=sample_date,
            end_date=end_date,
            associates=weekly_associates,
        )

        # Create weekly demand with high-volume mid-week
        weekly_demand = WeeklyDemand.create_standard_week(sample_date)
        high_volume = DemandProfile.create_high_volume_profile()
        wednesday = sample_date + timedelta(days=2)
        weekly_demand.apply_profile(wednesday, high_volume)

        config = DemandAwareConfig(solver_type=SolverType.HEURISTIC)
        scheduler = DemandAwareWeeklyScheduler(config=config)
        result = scheduler.generate_schedule(request, weekly_demand)

        # Verify high-volume day metrics
        assert wednesday in result.demand_metrics
