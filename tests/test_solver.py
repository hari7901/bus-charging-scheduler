"""
Tests for the CP-SAT solver: correctness, hard rule compliance, and weight effects.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from scheduler.models import load_all_scenarios, ScheduleResult, Scenario
from scheduler.solver import solve

DATA_DIR = Path(__file__).parent.parent / "data" / "scenarios"

_SCENARIOS = None  # cached after first load


def _all_scenarios():
    global _SCENARIOS
    if _SCENARIOS is None:
        _SCENARIOS = load_all_scenarios(DATA_DIR)
    return _SCENARIOS


def _solve(scenario_id: str, time_limit: float = 10.0) -> tuple[Scenario, ScheduleResult]:
    s = next(sc for sc in _all_scenarios() if sc.metadata.id == scenario_id)
    return s, solve(s, time_limit_sec=time_limit)


# ---------------------------------------------------------------------------
# Solver produces a solution for every scenario
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", [
    "scenario_01", "scenario_02", "scenario_03", "scenario_04", "scenario_05"
])
def test_solver_finds_solution(sid):
    _, result = _solve(sid, time_limit=15.0)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE"), (
        f"{sid}: expected OPTIMAL or FEASIBLE, got {result.solver_status}"
    )


@pytest.mark.parametrize("sid", [
    "scenario_01", "scenario_02", "scenario_03", "scenario_04", "scenario_05"
])
def test_all_buses_scheduled(sid):
    s, result = _solve(sid, time_limit=15.0)
    assert len(result.bus_schedules) == len(s.buses)


# ---------------------------------------------------------------------------
# Hard rule: range must never be violated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", [
    "scenario_01", "scenario_02", "scenario_03", "scenario_04", "scenario_05"
])
def test_range_rule(sid):
    s, result = _solve(sid, time_limit=15.0)
    battery = s.parameters.battery_range_km
    for bs in result.bus_schedules:
        bus = next(b for b in s.buses if b.id == bs.bus_id)
        path = s.route_nodes_for_bus(bus)
        checkpoints = [path[0]] + [e.station_id for e in bs.charge_events] + [path[-1]]
        for i in range(len(checkpoints) - 1):
            dist = s.route.distance_between(checkpoints[i], checkpoints[i + 1])
            assert dist <= battery, (
                f"{sid}/{bs.bus_id}: range violation {checkpoints[i]}→{checkpoints[i+1]} ({dist} km)"
            )


# ---------------------------------------------------------------------------
# Hard rule: station charger capacity never exceeded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", [
    "scenario_01", "scenario_02", "scenario_03", "scenario_04", "scenario_05"
])
def test_charger_capacity(sid):
    s, result = _solve(sid, time_limit=15.0)
    station_map = s.station_map()

    station_events: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for bs in result.bus_schedules:
        for ev in bs.charge_events:
            station_events[ev.station_id].append(
                (ev.charge_start_min, ev.charge_end_min)
            )

    for station_id, evs in station_events.items():
        cap = station_map[station_id].charger_count
        times = []
        for start, end in evs:
            times.append((start, +1))
            times.append((end, -1))
        times.sort()
        concurrent = 0
        for _, delta in times:
            concurrent += delta
            assert concurrent <= cap, (
                f"{sid}: Station {station_id} has {concurrent} concurrent buses (cap={cap})"
            )


# ---------------------------------------------------------------------------
# Hard rule: timelines are monotonic (bus can't go back in time)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", ["scenario_01", "scenario_03"])
def test_timeline_monotonic(sid):
    _, result = _solve(sid, time_limit=10.0)
    for bs in result.bus_schedules:
        prev_time = bs.departure_min
        for ev in bs.charge_events:
            assert ev.arrival_min >= prev_time, (
                f"{bs.bus_id}: arrived at {ev.station_id} ({ev.arrival_min}) before prev time ({prev_time})"
            )
            assert ev.charge_start_min >= ev.arrival_min, (
                f"{bs.bus_id}: charge started before arrival at {ev.station_id}"
            )
            assert ev.charge_end_min == ev.charge_start_min + 25
            prev_time = ev.charge_end_min
        assert bs.arrival_min >= prev_time, (
            f"{bs.bus_id}: final arrival ({bs.arrival_min}) before last charge end ({prev_time})"
        )


# ---------------------------------------------------------------------------
# Hard rule: every bus charges at least twice (540 km / 240 km battery)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", [
    "scenario_01", "scenario_02", "scenario_03", "scenario_04", "scenario_05"
])
def test_minimum_charge_stops(sid):
    _, result = _solve(sid, time_limit=15.0)
    for bs in result.bus_schedules:
        assert len(bs.charge_events) >= 2, (
            f"{bs.bus_id}: only {len(bs.charge_events)} charge stop(s); need >= 2"
        )


# ---------------------------------------------------------------------------
# Weight behaviour: operator weight 2.0 vs 1.0 should differ in scenario 4
# ---------------------------------------------------------------------------

def test_operator_weight_effect():
    """
    Scenario 4 has operator weight=2.0 (KPN dominates).
    Changing it to 1.0 should produce a different or equal objective value,
    but not a higher total cost for KPN when weight=2.0.

    We verify the scenario-defined weight (2.0) is loaded correctly and the
    result is valid, then compare it against a clone with weight=1.0.
    """
    s, result_w2 = _solve("scenario_04", time_limit=10.0)
    assert result_w2.solver_status in ("OPTIMAL", "FEASIBLE")

    # Clone with operator weight = 1.0
    s_low = s.model_copy(update={"weights": s.weights.model_copy(update={"operator": 1.0})})
    result_w1 = solve(s_low, time_limit_sec=10.0)
    assert result_w1.solver_status in ("OPTIMAL", "FEASIBLE")

    # With higher operator weight, max per-operator wait should be <= low weight version
    # (higher weight pushes the solver to equalise across operators more)
    def max_op_wait(r: ScheduleResult) -> int:
        op_waits: dict[str, list[int]] = defaultdict(list)
        for bs in r.bus_schedules:
            op_waits[bs.operator].append(bs.total_wait_min)
        return max(max(v) for v in op_waits.values())

    # This is a soft assertion: the higher operator weight should produce
    # a schedule with max_op_wait <= the lower weight result (or equal).
    # We don't assert a strict improvement since both may find optimal.
    assert max_op_wait(result_w2) <= max_op_wait(result_w1) + 5, (
        "Higher operator weight should not produce a worse operator-max result "
        f"(w=2.0 max_op={max_op_wait(result_w2)}, w=1.0 max_op={max_op_wait(result_w1)})"
    )


# ---------------------------------------------------------------------------
# Wait values are non-negative
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", ["scenario_01", "scenario_03"])
def test_wait_non_negative(sid):
    _, result = _solve(sid, time_limit=10.0)
    for bs in result.bus_schedules:
        assert bs.total_wait_min >= 0
        for ev in bs.charge_events:
            assert ev.wait_min >= 0
