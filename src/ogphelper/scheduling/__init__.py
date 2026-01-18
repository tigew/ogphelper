"""Scheduling engine for generating associate schedules."""

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
from ogphelper.scheduling.heuristic_solver import HeuristicSolver
from ogphelper.scheduling.scheduler import Scheduler
from ogphelper.scheduling.weekly_scheduler import WeeklyScheduler

__all__ = [
    # Core schedulers
    "Scheduler",
    "WeeklyScheduler",
    # Demand-aware scheduling (Phase 3)
    "DemandAwareWeeklyScheduler",
    "DemandAwareConfig",
    "DemandAwareWeeklyResult",
    "create_demand_aware_scheduler",
    # Solvers
    "CandidateGenerator",
    "HeuristicSolver",
    "CPSATSolver",
    "DemandAwareSolver",
    # Solver configuration
    "SolverConfig",
    "SolverResult",
    "SolverType",
    "OptimizationMode",
]
