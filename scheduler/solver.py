"""
CP-SAT scheduling backend for the bus charging scheduler.

OOP pillars demonstrated here
------------------------------
Abstraction:   SchedulerBackend is a formal ABC. app.py depends on this
               interface, not on CP-SAT specifically. A greedy or LP-based
               backend can be dropped in without any UI changes.

Inheritance:   CpSatBackend inherits SchedulerBackend and implements solve().

Polymorphism:  Any SchedulerBackend subclass can be passed to the Streamlit
               app. CpSatBackend itself receives a PlanGenerator at construction
               time and calls self._plan_generator.get_plans() — dispatch goes
               to whatever concrete generator was injected.

Encapsulation: All CP-SAT model-building logic lives in private _methods.
               External callers see only solve(scenario, time_limit_sec).
               Internal variable dictionaries (depart, active, charge_start)
               are confined to _build_model and passed explicitly between
               private helpers — they never leak into the public interface.

CP-SAT model overview
---------------------
For each bus b traversing nodes [n0, n1, ..., nk]:

  depart[b][i]       — IntVar: minute the bus leaves node i.
                       i=0 is fixed (= departure time).
                       i=last is the final arrival.
  active[b][s]       — BoolVar: 1 if bus b charges at station s.
  charge_start[b][s] — IntVar: minute charging begins.

Hard constraints
  1. Plan validity: active flags match one of the bus's feasible plans,
     encoding the 240 km range rule generically via PlanGenerator.
  2. Timing: depart[i] = arrival or charge_end depending on active[s].
  3. Capacity: optional charging intervals at each station obey
     AddNoOverlap (charger_count=1) or AddCumulative (>1).

Soft objective
  Each registered BaseRule contributes a weighted LinearExpr term.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from ortools.sat.python import cp_model

from .models import (
    Bus,
    BusSchedule,
    ChargeEvent,
    Scenario,
    ScheduleResult,
)
from .plans import PlanGenerator, SubsetEnumerationGenerator
from .rules import REGISTERED_RULES, BaseRule, RuleContext

_WEIGHT_SCALE = 100

# Internal type aliases
_IntVarByIdx = dict[str, dict[int, Any]]   # bus_id -> node_idx -> IntVar
_IntVarByStation = dict[str, dict[str, Any]]  # bus_id -> station_id -> IntVar


# ---------------------------------------------------------------------------
# Abstract backend interface (Abstraction)
# ---------------------------------------------------------------------------

class SchedulerBackend(ABC):
    """
    Abstract interface for all scheduling backends.

    Decouples the UI and tests from the concrete solver implementation.
    Any backend — CP-SAT, greedy, LP — must implement solve().
    """

    @abstractmethod
    def solve(
        self,
        scenario: Scenario,
        time_limit_sec: float = 15.0,
    ) -> ScheduleResult:
        """
        Compute a valid charging schedule for *scenario* and return the result.

        Must respect all hard rules (range, charger capacity, route order).
        Should optimise the scenario's weighted objective within time_limit_sec.
        """


# ---------------------------------------------------------------------------
# Concrete CP-SAT backend (Inheritance + Encapsulation + Polymorphism)
# ---------------------------------------------------------------------------

class CpSatBackend(SchedulerBackend):
    """
    Scheduling backend that uses OR-Tools CP-SAT.

    Constructor arguments let callers inject different rule sets and plan
    generators — demonstrating Dependency Inversion and enabling easy testing
    with minimal mock setups.

    Example — use a custom rule set:
        backend = CpSatBackend(rules=[IndividualWaitRule, OverallWaitRule])

    Example — plug in a future DP plan generator:
        backend = CpSatBackend(plan_generator=DPPlanGenerator())
    """

    def __init__(
        self,
        rules: list[type[BaseRule]] | None = None,
        plan_generator: PlanGenerator | None = None,
    ) -> None:
        # Dependency injection: accept any BaseRule subclass list
        self._rules: list[type[BaseRule]] = rules if rules is not None else REGISTERED_RULES
        # Dependency injection: accept any PlanGenerator subclass
        self._plan_generator: PlanGenerator = (
            plan_generator if plan_generator is not None else SubsetEnumerationGenerator()
        )

    # ------------------------------------------------------------------
    # Public interface (SchedulerBackend contract)
    # ------------------------------------------------------------------

    def solve(
        self,
        scenario: Scenario,
        time_limit_sec: float = 15.0,
    ) -> ScheduleResult:
        """Build the CP-SAT model, solve it, and return structured results."""
        time_ub = scenario.absolute_time_upper_bound()
        n_buses = len(scenario.buses)
        max_charge_dur = max(
            scenario.effective_charge_duration(s.id) for s in scenario.stations
        )
        single_wait_ub = n_buses * max_charge_dur
        total_wait_ub = n_buses * len(scenario.stations) * max_charge_dur

        model, depart, active, charge_start = self._build_model(scenario, time_ub)
        wait_vars, total_wait = self._build_wait_vars(
            model, scenario, depart, active, charge_start,
            single_wait_ub, total_wait_ub,
        )
        self._apply_rules(
            model, scenario, depart, active, charge_start,
            wait_vars, total_wait, total_wait_ub,
        )

        status_str, solver, elapsed = self._run_solver(model, time_limit_sec)

        if status_str not in ("OPTIMAL", "FEASIBLE"):
            return ScheduleResult(
                scenario_id=scenario.metadata.id,
                solver_status=status_str,
                solve_time_sec=round(elapsed, 3),
                bus_schedules=[],
            )

        return self._extract_results(
            solver, scenario, depart, active, charge_start, status_str, elapsed
        )

    # ------------------------------------------------------------------
    # Private helpers — encapsulated implementation details
    # ------------------------------------------------------------------

    def _build_model(
        self,
        scenario: Scenario,
        time_ub: int,
    ) -> tuple[cp_model.CpModel, _IntVarByIdx, _IntVarByStation, _IntVarByStation]:
        """Create all CP-SAT variables and hard constraints."""
        model = cp_model.CpModel()
        station_id_set = {s.id for s in scenario.stations}

        # Per-bus data — computed once via the injected PlanGenerator
        bus_nodes: dict[str, list[str]] = {
            bus.id: scenario.route_nodes_for_bus(bus)
            for bus in scenario.buses
        }
        bus_plans: dict[str, list[tuple[str, ...]]] = {
            bus.id: self._plan_generator.get_plans(scenario, bus)
            for bus in scenario.buses
        }

        depart: _IntVarByIdx = {}
        active: _IntVarByStation = {}
        charge_start: _IntVarByStation = {}

        for bus in scenario.buses:
            bid = bus.id
            nodes = bus_nodes[bid]
            dep_min = bus.departure_minutes()
            plans = bus_plans[bid]

            depart[bid] = {0: model.NewConstant(dep_min)}
            for i in range(1, len(nodes)):
                depart[bid][i] = model.NewIntVar(dep_min, time_ub, f"dep_{bid}_{i}")

            plan_vars = [
                model.NewBoolVar(f"plan_{bid}_{p}") for p in range(len(plans))
            ]
            model.AddExactlyOne(plan_vars)

            active[bid] = {}
            charge_start[bid] = {}
            for node in nodes[1:-1]:
                if node not in station_id_set:
                    continue
                active[bid][node] = model.NewBoolVar(f"act_{bid}_{node}")
                charge_start[bid][node] = model.NewIntVar(
                    dep_min, time_ub, f"cs_{bid}_{node}"
                )
                plans_with_node = [
                    i for i, plan in enumerate(plans) if node in plan
                ]
                model.Add(
                    sum(plan_vars[i] for i in plans_with_node) == active[bid][node]
                )

            self._add_timing(model, bus, nodes, depart, active, charge_start, scenario)

        self._add_capacity(model, scenario, active, charge_start, time_ub)
        return model, depart, active, charge_start

    def _add_timing(
        self,
        model: cp_model.CpModel,
        bus: Bus,
        nodes: list[str],
        depart: _IntVarByIdx,
        active: _IntVarByStation,
        charge_start: _IntVarByStation,
        scenario: Scenario,
    ) -> None:
        """Propagate departure times through every node on this bus's path."""
        bid = bus.id
        for node_idx in range(1, len(nodes)):
            node = nodes[node_idx]
            prev = nodes[node_idx - 1]
            travel = scenario.travel_time_between(prev, node)
            arrival = depart[bid][node_idx - 1] + travel

            if node in active[bid]:
                dur = scenario.effective_charge_duration(node)
                model.Add(depart[bid][node_idx] == arrival).OnlyEnforceIf(
                    active[bid][node].Not()
                )
                model.Add(charge_start[bid][node] >= arrival).OnlyEnforceIf(
                    active[bid][node]
                )
                model.Add(
                    depart[bid][node_idx] == charge_start[bid][node] + dur
                ).OnlyEnforceIf(active[bid][node])
                model.Add(depart[bid][node_idx] >= arrival)
            else:
                model.Add(depart[bid][node_idx] == arrival)

    def _add_capacity(
        self,
        model: cp_model.CpModel,
        scenario: Scenario,
        active: _IntVarByStation,
        charge_start: _IntVarByStation,
        time_ub: int,
    ) -> None:
        """Enforce charger capacity at each station via optional intervals."""
        station_intervals: dict[str, list[Any]] = {s.id: [] for s in scenario.stations}

        for bus in scenario.buses:
            bid = bus.id
            for station_id, act_var in active[bid].items():
                if station_id not in station_intervals:
                    continue
                dur = scenario.effective_charge_duration(station_id)
                end_var = model.NewIntVar(0, time_ub, f"ce_{bid}_{station_id}")
                model.Add(end_var == charge_start[bid][station_id] + dur)
                iv = model.NewOptionalIntervalVar(
                    charge_start[bid][station_id], dur, end_var,
                    act_var, f"iv_{bid}_{station_id}",
                )
                station_intervals[station_id].append(iv)

        station_map = scenario.station_map()
        for station_id, intervals in station_intervals.items():
            if not intervals:
                continue
            cap = station_map[station_id].charger_count
            if cap == 1:
                model.AddNoOverlap(intervals)
            else:
                model.AddCumulative(intervals, [1] * len(intervals), cap)

    def _build_wait_vars(
        self,
        model: cp_model.CpModel,
        scenario: Scenario,
        depart: _IntVarByIdx,
        active: _IntVarByStation,
        charge_start: _IntVarByStation,
        single_wait_ub: int,
        total_wait_ub: int,
    ) -> tuple[dict[str, list[Any]], dict[str, Any]]:
        """Create wait IntVars and total-wait IntVars shared with rules."""
        wait_vars: dict[str, list[Any]] = {}
        total_wait: dict[str, Any] = {}

        for bus in scenario.buses:
            bid = bus.id
            nodes = scenario.route_nodes_for_bus(bus)
            parts: list[Any] = []

            for node_idx, node in enumerate(nodes[1:-1], start=1):
                if node not in active[bid]:
                    continue
                prev = nodes[node_idx - 1]
                travel = scenario.travel_time_between(prev, node)
                arrival = depart[bid][node_idx - 1] + travel

                w = model.NewIntVar(0, single_wait_ub, f"wait_{bid}_{node}")
                model.Add(w == 0).OnlyEnforceIf(active[bid][node].Not())
                model.Add(
                    w == charge_start[bid][node] - arrival
                ).OnlyEnforceIf(active[bid][node])
                parts.append(w)

            wait_vars[bid] = parts
            tw = model.NewIntVar(0, total_wait_ub, f"tw_{bid}")
            model.Add(tw == sum(parts)) if parts else model.Add(tw == 0)
            total_wait[bid] = tw

        return wait_vars, total_wait

    def _apply_rules(
        self,
        model: cp_model.CpModel,
        scenario: Scenario,
        depart: _IntVarByIdx,
        active: _IntVarByStation,
        charge_start: _IntVarByStation,
        wait_vars: dict[str, list[Any]],
        total_wait: dict[str, Any],
        wait_ub: int,
    ) -> None:
        """Instantiate each registered rule and collect its objective terms."""
        ctx = RuleContext(
            model=model,
            scenario=scenario,
            depart=depart,
            active=active,
            charge_start=charge_start,
            wait_vars=wait_vars,
            total_wait=total_wait,
            wait_ub=wait_ub,
            scale=_WEIGHT_SCALE,
        )
        terms: list[Any] = []
        for rule_cls in self._rules:
            rule = rule_cls()
            rule.enforce_hard(ctx)
            term = rule.contribute_objective(ctx)
            if term is not None:
                terms.append(term)

        if terms:
            model.Minimize(sum(terms))

    def _run_solver(
        self,
        model: cp_model.CpModel,
        time_limit_sec: float,
    ) -> tuple[str, cp_model.CpSolver, float]:
        """Execute the solver and return (status_string, solver, elapsed_sec)."""
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_sec
        solver.parameters.log_search_progress = False
        solver.parameters.num_workers = 1

        t0 = time.perf_counter()
        status = solver.Solve(model)
        elapsed = time.perf_counter() - t0

        status_map = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.UNKNOWN: "UNKNOWN",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
        }
        return status_map.get(status, "UNKNOWN"), solver, elapsed

    def _extract_results(
        self,
        solver: cp_model.CpSolver,
        scenario: Scenario,
        depart: _IntVarByIdx,
        active: _IntVarByStation,
        charge_start: _IntVarByStation,
        status_str: str,
        elapsed: float,
    ) -> ScheduleResult:
        """Read solved variable values into structured output models."""
        bus_schedules: list[BusSchedule] = []

        for bus in scenario.buses:
            bid = bus.id
            nodes = scenario.route_nodes_for_bus(bus)
            events: list[ChargeEvent] = []

            for node_idx, node in enumerate(nodes[1:-1], start=1):
                if node not in active[bid] or solver.Value(active[bid][node]) == 0:
                    continue
                prev = nodes[node_idx - 1]
                travel = scenario.travel_time_between(prev, node)
                arr = solver.Value(depart[bid][node_idx - 1]) + travel
                cs = solver.Value(charge_start[bid][node])
                dur = scenario.effective_charge_duration(node)
                events.append(ChargeEvent(
                    station_id=node,
                    arrival_min=arr,
                    wait_min=cs - arr,
                    charge_start_min=cs,
                    charge_end_min=cs + dur,
                ))

            bus_schedules.append(BusSchedule(
                bus_id=bid,
                operator=bus.operator,
                origin=bus.origin,
                destination=bus.destination,
                departure_min=bus.departure_minutes(),
                charge_events=events,
                arrival_min=solver.Value(depart[bid][len(nodes) - 1]),
                total_wait_min=sum(e.wait_min for e in events),
            ))

        return ScheduleResult(
            scenario_id=scenario.metadata.id,
            solver_status=status_str,
            solve_time_sec=round(elapsed, 3),
            bus_schedules=bus_schedules,
        )


# ---------------------------------------------------------------------------
# Convenience wrapper — backward compatible with existing call sites
# ---------------------------------------------------------------------------

def solve(
    scenario: Scenario,
    time_limit_sec: float = 15.0,
    rules: list[type[BaseRule]] | None = None,
) -> ScheduleResult:
    """
    Convenience wrapper: creates a default CpSatBackend and calls solve().

    Prefer instantiating CpSatBackend directly when you need custom rules
    or a different plan generator.
    """
    return CpSatBackend(rules=rules).solve(scenario, time_limit_sec)
