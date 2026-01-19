"""OR-Tools CP-SAT solver for optimal schedule generation.

This module provides a constraint programming approach to scheduling using
Google OR-Tools CP-SAT solver. It can optimize schedules to match demand
curves while respecting all constraints.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from ortools.sat.python import cp_model

from ogphelper.domain.demand import DemandCurve, DemandPriority
from ogphelper.domain.models import (
    Associate,
    DaySchedule,
    JobAssignment,
    JobRole,
    Preference,
    ScheduleBlock,
    ScheduleRequest,
    ShiftAssignment,
)
from ogphelper.domain.policies import (
    BreakPolicy,
    DefaultBreakPolicy,
    DefaultLunchPolicy,
    LunchPolicy,
)
from ogphelper.scheduling.candidate_generator import CandidateGenerator, ShiftCandidate


class OptimizationMode(Enum):
    """Optimization objective modes."""

    MAXIMIZE_COVERAGE = "maximize_coverage"  # Maximize total on-floor time
    MATCH_DEMAND = "match_demand"  # Match demand curve as closely as possible
    MINIMIZE_UNDERCOVERAGE = "minimize_undercoverage"  # Avoid understaffing
    BALANCED = "balanced"  # Balance coverage and demand matching


@dataclass
class SolverConfig:
    """Configuration for the CP-SAT solver.

    Attributes:
        time_limit_seconds: Maximum solver runtime.
        num_workers: Number of parallel workers (0 = auto).
        optimization_mode: What to optimize for.
        demand_weight: Weight for demand matching (0-100).
        coverage_weight: Weight for coverage maximization (0-100).
        fairness_weight: Weight for hours fairness (0-100).
        preference_weight: Weight for role preferences (0-100).
        undercoverage_penalty: Penalty multiplier for being under min demand.
        overcoverage_penalty: Penalty multiplier for being over max demand.
        priority_multipliers: Multipliers for different priority levels.
        enforce_min_demand: If True, min_demand is a hard constraint.
    """

    time_limit_seconds: float = 30.0
    num_workers: int = 0
    optimization_mode: OptimizationMode = OptimizationMode.BALANCED
    demand_weight: int = 40
    coverage_weight: int = 30
    fairness_weight: int = 20
    preference_weight: int = 10
    undercoverage_penalty: int = 100
    overcoverage_penalty: int = 10
    priority_multipliers: dict[DemandPriority, int] = field(
        default_factory=lambda: {
            DemandPriority.LOW: 1,
            DemandPriority.NORMAL: 2,
            DemandPriority.HIGH: 5,
            DemandPriority.CRITICAL: 10,
        }
    )
    enforce_min_demand: bool = False


@dataclass
class SolverResult:
    """Result from the CP-SAT solver.

    Attributes:
        schedule: The generated DaySchedule.
        status: Solver status (OPTIMAL, FEASIBLE, etc.).
        objective_value: Final objective value.
        solve_time_seconds: Time taken to solve.
        num_branches: Number of branches explored.
        num_conflicts: Number of conflicts encountered.
    """

    schedule: Optional[DaySchedule]
    status: str
    objective_value: int = 0
    solve_time_seconds: float = 0.0
    num_branches: int = 0
    num_conflicts: int = 0

    @property
    def is_optimal(self) -> bool:
        return self.status == "OPTIMAL"

    @property
    def is_feasible(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")


class CPSATSolver:
    """Constraint Programming solver using OR-Tools CP-SAT.

    This solver formulates the scheduling problem as a constraint satisfaction
    and optimization problem, finding globally optimal solutions.
    """

    def __init__(
        self,
        lunch_policy: Optional[LunchPolicy] = None,
        break_policy: Optional[BreakPolicy] = None,
        config: Optional[SolverConfig] = None,
    ):
        self.lunch_policy = lunch_policy or DefaultLunchPolicy()
        self.break_policy = break_policy or DefaultBreakPolicy()
        self.config = config or SolverConfig()

    def solve(
        self,
        request: ScheduleRequest,
        candidates: dict[str, list[ShiftCandidate]],
        associates_map: dict[str, Associate],
        demand_curve: Optional[DemandCurve] = None,
    ) -> SolverResult:
        """Solve the scheduling problem using CP-SAT.

        Args:
            request: Schedule request with constraints.
            candidates: Pre-generated candidates per associate.
            associates_map: Dict mapping associate IDs to Associate objects.
            demand_curve: Optional demand curve to optimize against.

        Returns:
            SolverResult with schedule and solver statistics.
        """
        model = cp_model.CpModel()
        total_slots = request.total_slots

        # Decision variables: x[a][c] = 1 if associate a is assigned candidate c
        x: dict[str, dict[int, cp_model.IntVar]] = {}
        for assoc_id, assoc_candidates in candidates.items():
            x[assoc_id] = {}
            for c_idx, candidate in enumerate(assoc_candidates):
                x[assoc_id][c_idx] = model.NewBoolVar(f"x_{assoc_id}_{c_idx}")

        # Constraint 1: Each associate gets at most one shift
        for assoc_id in candidates:
            model.AddAtMostOne(x[assoc_id].values())

        # Variables for lunch positions (we'll optimize these)
        lunch_vars: dict[str, dict[int, dict[int, cp_model.IntVar]]] = {}
        for assoc_id, assoc_candidates in candidates.items():
            lunch_vars[assoc_id] = {}
            for c_idx, candidate in enumerate(assoc_candidates):
                if candidate.lunch_slots > 0:
                    lunch_vars[assoc_id][c_idx] = {}
                    # Get lunch window
                    earliest, latest = self.lunch_policy.get_lunch_window(
                        candidate.start_slot,
                        candidate.end_slot,
                        candidate.lunch_slots,
                        request.is_busy_day,
                        candidate.slot_minutes,
                    )
                    for lunch_start in range(earliest, latest + 1):
                        lunch_vars[assoc_id][c_idx][lunch_start] = model.NewBoolVar(
                            f"lunch_{assoc_id}_{c_idx}_{lunch_start}"
                        )

        # Constraint: If candidate selected and needs lunch, exactly one lunch position
        for assoc_id, assoc_candidates in candidates.items():
            for c_idx, candidate in enumerate(assoc_candidates):
                if candidate.lunch_slots > 0 and c_idx in lunch_vars.get(assoc_id, {}):
                    model.Add(
                        sum(lunch_vars[assoc_id][c_idx].values()) == 1
                    ).OnlyEnforceIf(x[assoc_id][c_idx])
                    model.Add(
                        sum(lunch_vars[assoc_id][c_idx].values()) == 0
                    ).OnlyEnforceIf(x[assoc_id][c_idx].Not())

        # Calculate coverage at each slot
        coverage = []
        for slot in range(total_slots):
            slot_coverage = []
            for assoc_id, assoc_candidates in candidates.items():
                for c_idx, candidate in enumerate(assoc_candidates):
                    if candidate.start_slot <= slot < candidate.end_slot:
                        # Check if on lunch at this slot
                        if candidate.lunch_slots > 0 and c_idx in lunch_vars.get(assoc_id, {}):
                            # Create a variable for "on floor at this slot"
                            on_floor = model.NewBoolVar(f"floor_{assoc_id}_{c_idx}_{slot}")

                            # Associate is on floor if:
                            # - Candidate is selected AND
                            # - Not on lunch at this slot
                            lunch_at_slot = []
                            for lunch_start, lunch_var in lunch_vars[assoc_id][c_idx].items():
                                if lunch_start <= slot < lunch_start + candidate.lunch_slots:
                                    lunch_at_slot.append(lunch_var)

                            if lunch_at_slot:
                                # on_floor = x[assoc_id][c_idx] AND NOT any(lunch_at_slot)
                                # not_on_lunch is true iff NONE of the lunch_at_slot vars are true
                                not_on_lunch = model.NewBoolVar(f"not_lunch_{assoc_id}_{c_idx}_{slot}")
                                # If not_on_lunch, all lunch vars for this slot must be false
                                model.AddBoolAnd([v.Not() for v in lunch_at_slot]).OnlyEnforceIf(not_on_lunch)
                                # If NOT not_on_lunch (i.e., on lunch), at least one must be true
                                model.AddBoolOr(lunch_at_slot).OnlyEnforceIf(not_on_lunch.Not())

                                model.AddBoolAnd([x[assoc_id][c_idx], not_on_lunch]).OnlyEnforceIf(on_floor)
                                model.AddBoolOr([x[assoc_id][c_idx].Not(), not_on_lunch.Not()]).OnlyEnforceIf(on_floor.Not())
                                slot_coverage.append(on_floor)
                            else:
                                slot_coverage.append(x[assoc_id][c_idx])
                        else:
                            slot_coverage.append(x[assoc_id][c_idx])

            coverage.append(sum(slot_coverage) if slot_coverage else 0)

        # Role cap constraints (supports time-based caps)
        for role in JobRole:
            for slot in range(total_slots):
                cap = request.get_job_cap(slot, role)
                if cap < 999:
                    role_assignments = []
                    for assoc_id, assoc_candidates in candidates.items():
                        associate = associates_map.get(assoc_id)
                        if associate and associate.can_do_role(role):
                            for c_idx, candidate in enumerate(assoc_candidates):
                                if candidate.start_slot <= slot < candidate.end_slot:
                                    role_assignments.append(x[assoc_id][c_idx])
                    if role_assignments:
                        model.Add(sum(role_assignments) <= cap)

        # Build objective function
        objective_terms = []

        # Coverage component
        if self.config.coverage_weight > 0:
            for slot_cov in coverage:
                if isinstance(slot_cov, int):
                    objective_terms.append(slot_cov * self.config.coverage_weight)
                else:
                    objective_terms.append(slot_cov * self.config.coverage_weight)

        # Demand matching component
        if demand_curve and self.config.demand_weight > 0:
            for slot in range(total_slots):
                demand_point = demand_curve.get_demand_at_slot(slot)
                priority = demand_curve.get_priority_at_slot(slot)
                priority_mult = self.config.priority_multipliers.get(priority, 1)

                target = demand_point.target_staff
                min_staff = demand_point.min_staff
                max_staff = demand_point.max_staff

                if isinstance(coverage[slot], int):
                    # Static coverage
                    diff = coverage[slot] - target
                    if coverage[slot] < min_staff:
                        objective_terms.append(
                            -self.config.undercoverage_penalty * priority_mult * (min_staff - coverage[slot])
                        )
                else:
                    # Create auxiliary variables for under/over coverage
                    under = model.NewIntVar(0, target, f"under_{slot}")
                    over = model.NewIntVar(0, total_slots, f"over_{slot}")

                    # coverage[slot] + under - over = target
                    model.Add(coverage[slot] + under - over == target)

                    # Penalize undercoverage heavily, overcoverage lightly
                    objective_terms.append(-under * self.config.undercoverage_penalty * priority_mult)
                    objective_terms.append(-over * self.config.overcoverage_penalty)

                    # Hard constraint for minimum if configured
                    if self.config.enforce_min_demand and min_staff > 0:
                        model.Add(coverage[slot] >= min_staff)

        # Preference component
        if self.config.preference_weight > 0:
            for assoc_id, assoc_candidates in candidates.items():
                associate = associates_map.get(assoc_id)
                if not associate:
                    continue
                for c_idx, candidate in enumerate(assoc_candidates):
                    # Calculate preference score for this candidate
                    pref_score = 0
                    eligible_roles = associate.eligible_roles()
                    for role in eligible_roles:
                        pref = associate.get_preference(role)
                        if pref == Preference.PREFER:
                            pref_score += 1
                        elif pref == Preference.AVOID:
                            pref_score -= 1
                    objective_terms.append(x[assoc_id][c_idx] * pref_score * self.config.preference_weight)

        # Add shift length bonus (prefer longer shifts for better coverage)
        for assoc_id, assoc_candidates in candidates.items():
            for c_idx, candidate in enumerate(assoc_candidates):
                # Small bonus for work minutes (scaled down)
                objective_terms.append(x[assoc_id][c_idx] * (candidate.work_minutes // 60))

        # Maximize objective
        model.Maximize(sum(objective_terms))

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.config.time_limit_seconds
        if self.config.num_workers > 0:
            solver.parameters.num_workers = self.config.num_workers

        status = solver.Solve(model)

        # Map status to string
        status_map = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
            cp_model.UNKNOWN: "UNKNOWN",
        }
        status_str = status_map.get(status, "UNKNOWN")

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return SolverResult(
                schedule=None,
                status=status_str,
                solve_time_seconds=solver.WallTime(),
            )

        # Extract solution
        schedule = self._extract_solution(
            solver,
            x,
            lunch_vars,
            candidates,
            associates_map,
            request,
        )

        return SolverResult(
            schedule=schedule,
            status=status_str,
            objective_value=int(solver.ObjectiveValue()),
            solve_time_seconds=solver.WallTime(),
            num_branches=solver.NumBranches(),
            num_conflicts=solver.NumConflicts(),
        )

    def _extract_solution(
        self,
        solver: cp_model.CpSolver,
        x: dict[str, dict[int, cp_model.IntVar]],
        lunch_vars: dict[str, dict[int, dict[int, cp_model.IntVar]]],
        candidates: dict[str, list[ShiftCandidate]],
        associates_map: dict[str, Associate],
        request: ScheduleRequest,
    ) -> DaySchedule:
        """Extract the schedule from the solved model."""
        schedule = DaySchedule(
            schedule_date=request.schedule_date,
            slot_minutes=request.slot_minutes,
            day_start_minutes=request.day_start_minutes,
            day_end_minutes=request.day_end_minutes,
        )

        for assoc_id, assoc_candidates in candidates.items():
            for c_idx, candidate in enumerate(assoc_candidates):
                if solver.Value(x[assoc_id][c_idx]) == 1:
                    # This candidate was selected
                    assignment = ShiftAssignment(
                        associate_id=assoc_id,
                        schedule_date=request.schedule_date,
                        shift_start_slot=candidate.start_slot,
                        shift_end_slot=candidate.end_slot,
                        slot_minutes=candidate.slot_minutes,
                    )

                    # Extract lunch position
                    if candidate.lunch_slots > 0 and c_idx in lunch_vars.get(assoc_id, {}):
                        for lunch_start, lunch_var in lunch_vars[assoc_id][c_idx].items():
                            if solver.Value(lunch_var) == 1:
                                assignment.lunch_block = ScheduleBlock(
                                    lunch_start,
                                    lunch_start + candidate.lunch_slots,
                                    candidate.slot_minutes,
                                )
                                break

                    # Place breaks using heuristic (simpler than optimizing)
                    if candidate.break_count > 0:
                        assignment.break_blocks = self._place_breaks(
                            candidate, assignment.lunch_block
                        )

                    # Assign roles
                    associate = associates_map.get(assoc_id)
                    if associate:
                        assignment.job_assignments = self._assign_roles(
                            candidate, assignment, associate, request.job_caps
                        )

                    schedule.assignments[assoc_id] = assignment
                    break  # Only one candidate per associate

        return schedule

    def _place_breaks(
        self,
        candidate: ShiftCandidate,
        lunch_block: Optional[ScheduleBlock],
    ) -> list[ScheduleBlock]:
        """Place breaks using simple heuristic."""
        break_count = candidate.break_count
        break_duration = self.break_policy.get_break_duration()
        break_slots = break_duration // candidate.slot_minutes

        lunch_start = lunch_block.start_slot if lunch_block else None
        lunch_end = lunch_block.end_slot if lunch_block else None

        targets = self.break_policy.get_break_target_positions(
            candidate.start_slot,
            candidate.end_slot,
            break_count,
            lunch_start,
            lunch_end,
            candidate.slot_minutes,
        )

        breaks = []
        used_slots = set()

        if lunch_block:
            for slot in range(lunch_block.start_slot, lunch_block.end_slot):
                used_slots.add(slot)

        for target in targets:
            # Find valid position near target
            best_start = target
            for offset in range(9):
                for direction in [0, 1, -1]:
                    if direction == 0 and offset > 0:
                        continue
                    start = target + offset * direction
                    end = start + break_slots

                    if start < candidate.start_slot or end > candidate.end_slot:
                        continue

                    conflict = any(slot in used_slots for slot in range(start, end))
                    if not conflict:
                        best_start = start
                        break
                else:
                    continue
                break

            breaks.append(
                ScheduleBlock(best_start, best_start + break_slots, candidate.slot_minutes)
            )
            for slot in range(best_start, best_start + break_slots):
                used_slots.add(slot)

        return breaks

    def _assign_roles(
        self,
        candidate: ShiftCandidate,
        assignment: ShiftAssignment,
        associate: Associate,
        job_caps: dict[JobRole, int],
    ) -> list[JobAssignment]:
        """Assign roles to work periods."""
        eligible_roles = associate.eligible_roles()
        if not eligible_roles:
            return []

        work_periods = self._get_work_periods(candidate, assignment)
        assignments = []

        for period in work_periods:
            # Simple role assignment: prefer constrained roles, fall back to Picking
            role = None
            for r in [JobRole.GMD_SM, JobRole.EXCEPTION_SM, JobRole.STAGING, JobRole.BACKROOM]:
                if r in eligible_roles and associate.get_preference(r) != Preference.AVOID:
                    role = r
                    break

            if role is None and JobRole.PICKING in eligible_roles:
                role = JobRole.PICKING

            if role is None:
                role = next(iter(eligible_roles), None)

            if role:
                assignments.append(JobAssignment(role=role, block=period))

        return assignments

    def _get_work_periods(
        self,
        candidate: ShiftCandidate,
        assignment: ShiftAssignment,
    ) -> list[ScheduleBlock]:
        """Get contiguous work periods excluding lunch and breaks."""
        off_blocks = []
        if assignment.lunch_block:
            off_blocks.append(
                (assignment.lunch_block.start_slot, assignment.lunch_block.end_slot)
            )
        for break_block in assignment.break_blocks:
            off_blocks.append((break_block.start_slot, break_block.end_slot))

        off_blocks.sort()

        periods = []
        current_start = candidate.start_slot

        for off_start, off_end in off_blocks:
            if current_start < off_start:
                periods.append(
                    ScheduleBlock(current_start, off_start, candidate.slot_minutes)
                )
            current_start = off_end

        if current_start < candidate.end_slot:
            periods.append(
                ScheduleBlock(current_start, candidate.end_slot, candidate.slot_minutes)
            )

        return periods


class DemandAwareSolver:
    """High-level solver that combines candidate generation with CP-SAT optimization.

    This is the main entry point for demand-aware scheduling.
    """

    def __init__(
        self,
        lunch_policy: Optional[LunchPolicy] = None,
        break_policy: Optional[BreakPolicy] = None,
        solver_config: Optional[SolverConfig] = None,
    ):
        from ogphelper.domain.policies import DefaultShiftPolicy, ShiftPolicy

        self.lunch_policy = lunch_policy or DefaultLunchPolicy()
        self.break_policy = break_policy or DefaultBreakPolicy()
        self.solver_config = solver_config or SolverConfig()

        self.candidate_generator = CandidateGenerator(
            shift_policy=DefaultShiftPolicy(),
            lunch_policy=self.lunch_policy,
            break_policy=self.break_policy,
        )

        self.cpsat_solver = CPSATSolver(
            lunch_policy=self.lunch_policy,
            break_policy=self.break_policy,
            config=self.solver_config,
        )

    def solve(
        self,
        request: ScheduleRequest,
        demand_curve: Optional[DemandCurve] = None,
        step_slots: int = 2,
    ) -> SolverResult:
        """Generate an optimized schedule.

        Args:
            request: Schedule request with constraints.
            demand_curve: Optional demand curve to optimize against.
            step_slots: Granularity for candidate generation.

        Returns:
            SolverResult with the optimized schedule.
        """
        # Generate candidates
        candidates = self.candidate_generator.generate_all_candidates(request, step_slots)
        associates_map = {a.id: a for a in request.associates}

        # Solve with CP-SAT
        return self.cpsat_solver.solve(request, candidates, associates_map, demand_curve)

    def solve_with_fallback(
        self,
        request: ScheduleRequest,
        demand_curve: Optional[DemandCurve] = None,
        step_slots: int = 2,
    ) -> DaySchedule:
        """Solve with fallback to heuristic solver if CP-SAT fails.

        Args:
            request: Schedule request.
            demand_curve: Optional demand curve.
            step_slots: Candidate generation granularity.

        Returns:
            DaySchedule (from CP-SAT if successful, otherwise from heuristic).
        """
        from ogphelper.scheduling.heuristic_solver import HeuristicSolver

        result = self.solve(request, demand_curve, step_slots)

        if result.is_feasible and result.schedule:
            return result.schedule

        # Fallback to heuristic
        heuristic = HeuristicSolver(
            lunch_policy=self.lunch_policy,
            break_policy=self.break_policy,
        )
        candidates = self.candidate_generator.generate_all_candidates(request, step_slots)
        associates_map = {a.id: a for a in request.associates}

        return heuristic.solve(request, candidates, associates_map)
