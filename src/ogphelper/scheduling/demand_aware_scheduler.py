"""Demand-aware weekly scheduler for optimized schedule generation.

This module provides the DemandAwareWeeklyScheduler that combines:
- Weekly scheduling coordination
- Demand curve matching
- OR-Tools CP-SAT optimization
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Optional

from ogphelper.domain.demand import (
    DemandCurve,
    DemandMetrics,
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
    JobRole,
    ScheduleRequest,
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
from ogphelper.scheduling.candidate_generator import CandidateGenerator
from ogphelper.scheduling.cpsat_solver import (
    CPSATSolver,
    DemandAwareSolver,
    OptimizationMode,
    SolverConfig,
    SolverResult,
)
from ogphelper.scheduling.heuristic_solver import HeuristicSolver
from ogphelper.scheduling.weekly_scheduler import (
    AssociateWeeklyState,
    DaysOffPatternEnforcer,
    FairnessBalancer,
)


class SolverType(Enum):
    """Type of solver to use."""

    HEURISTIC = "heuristic"  # Fast greedy heuristic
    CPSAT = "cpsat"  # OR-Tools CP-SAT (optimal but slower)
    HYBRID = "hybrid"  # Try CP-SAT, fall back to heuristic


@dataclass
class DemandAwareConfig:
    """Configuration for demand-aware scheduling.

    Attributes:
        solver_type: Which solver to use.
        solver_config: Configuration for CP-SAT solver.
        weekly_demand: Demand curves for the week.
        auto_generate_demand: If True and no demand provided, generate default.
        default_weekday_profile: Profile for weekdays if auto-generating.
        default_weekend_profile: Profile for weekends if auto-generating.
        balance_across_days: Whether to balance demand matching across days.
        track_demand_metrics: Whether to calculate demand metrics.
    """

    solver_type: SolverType = SolverType.HYBRID
    solver_config: SolverConfig = field(default_factory=SolverConfig)
    weekly_demand: Optional[WeeklyDemand] = None
    auto_generate_demand: bool = True
    default_weekday_profile: Optional[DemandProfile] = None
    default_weekend_profile: Optional[DemandProfile] = None
    balance_across_days: bool = True
    track_demand_metrics: bool = True


@dataclass
class DemandAwareWeeklyResult:
    """Result from demand-aware weekly scheduling.

    Attributes:
        schedule: The generated WeeklySchedule.
        demand_metrics: Dict of date to DemandMetrics.
        solver_stats: Dict of date to solver statistics.
        overall_match_score: Overall demand match score (0-100).
    """

    schedule: WeeklySchedule
    demand_metrics: dict[date, DemandMetrics] = field(default_factory=dict)
    solver_stats: dict[date, dict] = field(default_factory=dict)
    overall_match_score: float = 0.0

    def get_summary(self) -> dict:
        """Get a summary of the scheduling results."""
        return {
            "num_days": len(self.schedule.day_schedules),
            "total_shifts": sum(
                len(ds.assignments) for ds in self.schedule.day_schedules.values()
            ),
            "overall_match_score": self.overall_match_score,
            "fairness_score": (
                self.schedule.fairness_metrics.fairness_score
                if self.schedule.fairness_metrics
                else None
            ),
            "demand_metrics_by_day": {
                d.isoformat(): {
                    "match_score": m.match_score,
                    "undercoverage_minutes": m.undercoverage_minutes,
                }
                for d, m in self.demand_metrics.items()
            },
        }


class DemandAwareWeeklyScheduler:
    """Weekly scheduler with demand-aware optimization.

    This scheduler extends the basic weekly scheduler with:
    - Demand curve matching
    - OR-Tools CP-SAT solver option
    - Demand metrics tracking
    """

    def __init__(
        self,
        shift_policy: Optional[ShiftPolicy] = None,
        lunch_policy: Optional[LunchPolicy] = None,
        break_policy: Optional[BreakPolicy] = None,
        config: Optional[DemandAwareConfig] = None,
    ):
        self.shift_policy = shift_policy or DefaultShiftPolicy()
        self.lunch_policy = lunch_policy or DefaultLunchPolicy()
        self.break_policy = break_policy or DefaultBreakPolicy()
        self.config = config or DemandAwareConfig()

        self.candidate_generator = CandidateGenerator(
            shift_policy=self.shift_policy,
            lunch_policy=self.lunch_policy,
            break_policy=self.break_policy,
        )

        self.heuristic_solver = HeuristicSolver(
            lunch_policy=self.lunch_policy,
            break_policy=self.break_policy,
        )

        self.cpsat_solver = CPSATSolver(
            lunch_policy=self.lunch_policy,
            break_policy=self.break_policy,
            config=self.config.solver_config,
        )

    def generate_schedule(
        self,
        request: WeeklyScheduleRequest,
        weekly_demand: Optional[WeeklyDemand] = None,
        step_slots: int = 2,
    ) -> DemandAwareWeeklyResult:
        """Generate a demand-aware weekly schedule.

        Args:
            request: Weekly schedule request.
            weekly_demand: Optional demand configuration (uses config default if None).
            step_slots: Candidate generation granularity.

        Returns:
            DemandAwareWeeklyResult with schedule and metrics.
        """
        # Resolve demand configuration
        demand = weekly_demand or self.config.weekly_demand
        if demand is None and self.config.auto_generate_demand:
            demand = self._generate_default_demand(request)

        # Initialize weekly state tracking
        associates_map = {a.id: a for a in request.associates}
        weekly_states = self._init_weekly_states(request.associates)

        # Initialize pattern enforcer and fairness balancer
        pattern_enforcer = DaysOffPatternEnforcer(
            request.days_off_pattern,
            request.required_days_off,
        )
        fairness_balancer = FairnessBalancer(request.fairness_config)

        # Create result containers
        weekly_schedule = WeeklySchedule(
            start_date=request.start_date,
            end_date=request.end_date,
        )
        demand_metrics: dict[date, DemandMetrics] = {}
        solver_stats: dict[date, dict] = {}

        all_dates = request.schedule_dates

        # Schedule each day
        for i, schedule_date in enumerate(all_dates):
            remaining_dates = all_dates[i:]

            # Get demand curve for this day
            day_demand = None
            if demand:
                day_demand = demand.get_demand_for_date(
                    schedule_date, request.slot_minutes
                )

            # Determine which associates work today
            working_associates = self._get_working_associates(
                request.associates,
                schedule_date,
                weekly_states,
                remaining_dates,
                all_dates,
                pattern_enforcer,
                fairness_balancer,
            )

            if not working_associates:
                day_schedule = DaySchedule(
                    schedule_date=schedule_date,
                    slot_minutes=request.slot_minutes,
                    day_start_minutes=request.day_start_minutes,
                    day_end_minutes=request.day_end_minutes,
                )
                weekly_schedule.day_schedules[schedule_date] = day_schedule
                continue

            # Adjust associates for weekly limits
            adjusted_associates = self._adjust_associates_for_weekly_limits(
                working_associates,
                schedule_date,
                weekly_states,
            )

            # Create day request
            day_request = ScheduleRequest(
                schedule_date=schedule_date,
                associates=adjusted_associates,
                day_start_minutes=request.day_start_minutes,
                day_end_minutes=request.day_end_minutes,
                slot_minutes=request.slot_minutes,
                job_caps=request.job_caps,
                is_busy_day=request.is_busy_day(schedule_date),
            )

            # Generate candidates
            candidates = self._generate_fairness_aware_candidates(
                day_request,
                weekly_states,
                fairness_balancer,
                step_slots,
            )

            # Solve using configured solver
            day_schedule, stats = self._solve_day(
                day_request,
                candidates,
                associates_map,
                day_demand,
            )

            solver_stats[schedule_date] = stats

            # Calculate demand metrics
            if day_demand and self.config.track_demand_metrics:
                coverage_timeline = day_schedule.get_coverage_timeline()
                metrics = DemandMetrics.calculate(
                    day_demand, coverage_timeline, request.slot_minutes
                )
                demand_metrics[schedule_date] = metrics

            # Update weekly states
            self._update_weekly_states(
                day_schedule,
                schedule_date,
                weekly_states,
                working_associates,
            )

            weekly_schedule.day_schedules[schedule_date] = day_schedule

        # Calculate fairness metrics
        weekly_schedule.fairness_metrics = self._calculate_fairness_metrics(weekly_states)

        # Calculate overall match score
        overall_match = 0.0
        if demand_metrics:
            overall_match = sum(m.match_score for m in demand_metrics.values()) / len(
                demand_metrics
            )

        return DemandAwareWeeklyResult(
            schedule=weekly_schedule,
            demand_metrics=demand_metrics,
            solver_stats=solver_stats,
            overall_match_score=overall_match,
        )

    def _generate_default_demand(self, request: WeeklyScheduleRequest) -> WeeklyDemand:
        """Generate default demand based on associate count and profiles."""
        weekday_profile = (
            self.config.default_weekday_profile
            or DemandProfile.create_weekday_profile()
        )
        weekend_profile = (
            self.config.default_weekend_profile
            or DemandProfile.create_weekend_profile()
        )

        # Scale profiles based on associate count
        num_associates = len(request.associates)
        scale_factor = max(0.5, min(2.0, num_associates / 10.0))

        # Create scaled profiles
        scaled_weekday = DemandProfile(
            name=weekday_profile.name,
            description=weekday_profile.description,
            hourly_pattern={
                h: max(1, int(v * scale_factor))
                for h, v in weekday_profile.hourly_pattern.items()
            },
            priority_windows=weekday_profile.priority_windows,
        )
        scaled_weekend = DemandProfile(
            name=weekend_profile.name,
            description=weekend_profile.description,
            hourly_pattern={
                h: max(1, int(v * scale_factor))
                for h, v in weekend_profile.hourly_pattern.items()
            },
            priority_windows=weekend_profile.priority_windows,
        )

        return WeeklyDemand.create_standard_week(
            request.start_date,
            weekday_profile=scaled_weekday,
            weekend_profile=scaled_weekend,
        )

    def _solve_day(
        self,
        request: ScheduleRequest,
        candidates: dict[str, list],
        associates_map: dict[str, Associate],
        demand_curve: Optional[DemandCurve],
    ) -> tuple[DaySchedule, dict]:
        """Solve a single day using the configured solver."""
        stats: dict = {"solver_type": self.config.solver_type.value}

        if self.config.solver_type == SolverType.HEURISTIC:
            schedule = self.heuristic_solver.solve(request, candidates, associates_map)
            stats["method"] = "heuristic"

        elif self.config.solver_type == SolverType.CPSAT:
            result = self.cpsat_solver.solve(
                request, candidates, associates_map, demand_curve
            )
            stats.update({
                "method": "cpsat",
                "status": result.status,
                "objective_value": result.objective_value,
                "solve_time": result.solve_time_seconds,
            })

            if result.is_feasible and result.schedule:
                schedule = result.schedule
            else:
                # Fall back to heuristic
                schedule = self.heuristic_solver.solve(
                    request, candidates, associates_map
                )
                stats["fallback"] = True

        else:  # HYBRID
            result = self.cpsat_solver.solve(
                request, candidates, associates_map, demand_curve
            )
            stats.update({
                "method": "hybrid",
                "cpsat_status": result.status,
                "cpsat_time": result.solve_time_seconds,
            })

            if result.is_feasible and result.schedule:
                schedule = result.schedule
                stats["used"] = "cpsat"
            else:
                schedule = self.heuristic_solver.solve(
                    request, candidates, associates_map
                )
                stats["used"] = "heuristic"

        return schedule, stats

    def _init_weekly_states(
        self,
        associates: list[Associate],
    ) -> dict[str, AssociateWeeklyState]:
        """Initialize weekly state tracking for all associates."""
        return {
            a.id: AssociateWeeklyState(
                associate_id=a.id,
                max_weekly_minutes=a.max_minutes_per_week,
            )
            for a in associates
        }

    def _get_working_associates(
        self,
        associates: list[Associate],
        schedule_date: date,
        weekly_states: dict[str, AssociateWeeklyState],
        remaining_dates: list[date],
        all_dates: list[date],
        pattern_enforcer: DaysOffPatternEnforcer,
        fairness_balancer: FairnessBalancer,
    ) -> list[Associate]:
        """Determine which associates should work on a given day."""
        working = []
        remaining_days = len(remaining_dates)

        for associate in associates:
            state = weekly_states[associate.id]

            # Check availability
            availability = associate.get_availability(schedule_date)
            if availability.is_off or availability.slot_count() == 0:
                state.add_day_off(schedule_date)
                continue

            # Check weekly limit
            if state.remaining_minutes < self.shift_policy.min_work_minutes():
                state.add_day_off(schedule_date)
                continue

            # Check days-off pattern
            if pattern_enforcer.should_be_day_off(
                state, schedule_date, remaining_dates, all_dates
            ):
                state.add_day_off(schedule_date)
                continue

            # Check fairness
            if fairness_balancer.should_skip_associate(
                state, weekly_states, remaining_days
            ):
                continue

            working.append(associate)

        return working

    def _adjust_associates_for_weekly_limits(
        self,
        associates: list[Associate],
        schedule_date: date,
        weekly_states: dict[str, AssociateWeeklyState],
    ) -> list[Associate]:
        """Adjust daily limits based on remaining weekly minutes."""
        adjusted = []

        for associate in associates:
            state = weekly_states[associate.id]

            adjusted_daily_max = min(
                associate.max_minutes_per_day,
                state.remaining_minutes,
            )

            if adjusted_daily_max < self.shift_policy.min_work_minutes():
                continue

            modified = Associate(
                id=associate.id,
                name=associate.name,
                availability=associate.availability,
                max_minutes_per_day=adjusted_daily_max,
                max_minutes_per_week=associate.max_minutes_per_week,
                supervisor_allowed_roles=associate.supervisor_allowed_roles,
                cannot_do_roles=associate.cannot_do_roles,
                role_preferences=associate.role_preferences,
            )
            adjusted.append(modified)

        return adjusted

    def _generate_fairness_aware_candidates(
        self,
        request: ScheduleRequest,
        weekly_states: dict[str, AssociateWeeklyState],
        fairness_balancer: FairnessBalancer,
        step_slots: int,
    ) -> dict[str, list]:
        """Generate candidates with fairness-aware ordering."""
        candidates = self.candidate_generator.generate_all_candidates(request, step_slots)

        for assoc_id, assoc_candidates in candidates.items():
            if assoc_id not in weekly_states:
                continue

            state = weekly_states[assoc_id]
            all_minutes = [s.minutes_scheduled for s in weekly_states.values()]
            avg_minutes = sum(all_minutes) / len(all_minutes) if all_minutes else 0

            if state.minutes_scheduled < avg_minutes:
                assoc_candidates.sort(key=lambda c: -c.work_minutes)
            elif state.minutes_scheduled > avg_minutes * 1.1:
                assoc_candidates.sort(key=lambda c: c.work_minutes)

        return candidates

    def _update_weekly_states(
        self,
        day_schedule: DaySchedule,
        schedule_date: date,
        weekly_states: dict[str, AssociateWeeklyState],
        working_associates: list[Associate],
    ) -> None:
        """Update weekly states after scheduling a day."""
        for assoc_id, assignment in day_schedule.assignments.items():
            if assoc_id in weekly_states:
                weekly_states[assoc_id].add_shift(
                    schedule_date,
                    assignment.work_minutes,
                )

        scheduled_ids = set(day_schedule.assignments.keys())
        for associate in working_associates:
            if associate.id not in scheduled_ids:
                weekly_states[associate.id].add_day_off(schedule_date)

    def _calculate_fairness_metrics(
        self,
        weekly_states: dict[str, AssociateWeeklyState],
    ) -> FairnessMetrics:
        """Calculate fairness metrics from weekly states."""
        weekly_minutes = {
            aid: state.minutes_scheduled for aid, state in weekly_states.items()
        }
        weekly_days = {
            aid: len(state.days_worked) for aid, state in weekly_states.items()
        }

        return FairnessMetrics.calculate(weekly_minutes, weekly_days)


def create_demand_aware_scheduler(
    solver_type: str = "hybrid",
    time_limit: float = 30.0,
    optimization_mode: str = "balanced",
) -> DemandAwareWeeklyScheduler:
    """Factory function to create a demand-aware scheduler.

    Args:
        solver_type: "heuristic", "cpsat", or "hybrid".
        time_limit: CP-SAT solver time limit in seconds.
        optimization_mode: "maximize_coverage", "match_demand", "minimize_undercoverage", or "balanced".

    Returns:
        Configured DemandAwareWeeklyScheduler.
    """
    solver_type_enum = SolverType(solver_type.lower())
    opt_mode_enum = OptimizationMode(optimization_mode.lower())

    solver_config = SolverConfig(
        time_limit_seconds=time_limit,
        optimization_mode=opt_mode_enum,
    )

    config = DemandAwareConfig(
        solver_type=solver_type_enum,
        solver_config=solver_config,
    )

    return DemandAwareWeeklyScheduler(config=config)
