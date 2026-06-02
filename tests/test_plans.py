"""Tests for feasible charging plan generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scheduler.models import load_all_scenarios
from scheduler.plans import feasible_plans

DATA_DIR = Path(__file__).parent.parent / "data" / "scenarios"


def _scenario_01():
    return next(s for s in load_all_scenarios(DATA_DIR) if s.metadata.id == "scenario_01")


# ---------------------------------------------------------------------------
# Plan validity: range constraint must hold for all returned plans
# ---------------------------------------------------------------------------

def test_all_plans_respect_range_bk():
    s = _scenario_01()
    battery = s.parameters.battery_range_km
    bus = next(b for b in s.buses if b.origin == "bengaluru")
    for plan in feasible_plans(s, bus):
        path = s.route_nodes_for_bus(bus)
        checkpoints = [path[0]] + list(plan) + [path[-1]]
        for i in range(len(checkpoints) - 1):
            dist = s.route.distance_between(checkpoints[i], checkpoints[i + 1])
            assert dist <= battery, (
                f"Range violated in plan {plan}: {checkpoints[i]}→{checkpoints[i+1]} = {dist} km"
            )


def test_all_plans_respect_range_kb():
    s = _scenario_01()
    battery = s.parameters.battery_range_km
    bus = next(b for b in s.buses if b.origin == "kochi")
    for plan in feasible_plans(s, bus):
        path = s.route_nodes_for_bus(bus)
        checkpoints = [path[0]] + list(plan) + [path[-1]]
        for i in range(len(checkpoints) - 1):
            dist = s.route.distance_between(checkpoints[i], checkpoints[i + 1])
            assert dist <= battery, (
                f"Range violated in plan {plan}: {checkpoints[i]}→{checkpoints[i+1]} = {dist} km"
            )


# ---------------------------------------------------------------------------
# Plan count and content
# ---------------------------------------------------------------------------

def test_bk_plan_count():
    """With the given route and 240 km range, a BK bus has exactly 8 valid plans."""
    s = _scenario_01()
    bus = next(b for b in s.buses if b.origin == "bengaluru")
    plans = feasible_plans(s, bus)
    assert len(plans) == 8


def test_kb_plan_count():
    """By symmetry, a KB bus also has exactly 8 valid plans."""
    s = _scenario_01()
    bus = next(b for b in s.buses if b.origin == "kochi")
    plans = feasible_plans(s, bus)
    assert len(plans) == 8


def test_no_empty_plan_bk():
    """A BK bus can never do the 540 km trip without charging (240 km battery)."""
    s = _scenario_01()
    bus = next(b for b in s.buses if b.origin == "bengaluru")
    plans = feasible_plans(s, bus)
    assert () not in plans


def test_no_single_stop_ad_plan():
    """A→D gap (340 km) exceeds battery, so (A,D) is not a valid BK plan."""
    s = _scenario_01()
    bus = next(b for b in s.buses if b.origin == "bengaluru")
    plans = feasible_plans(s, bus)
    assert ("A", "D") not in plans


def test_plans_in_route_order_bk():
    """All BK plans must list stations in ascending route order."""
    s = _scenario_01()
    bus = next(b for b in s.buses if b.origin == "bengaluru")
    route_order = {n: i for i, n in enumerate(s.route.node_ids())}
    for plan in feasible_plans(s, bus):
        indices = [route_order[st] for st in plan]
        assert indices == sorted(indices), f"Plan {plan} not in route order"


def test_plans_in_route_order_kb():
    """All KB plans must list stations in descending (KB direction) route order."""
    s = _scenario_01()
    bus = next(b for b in s.buses if b.origin == "kochi")
    route_order = {n: i for i, n in enumerate(s.route.node_ids())}
    for plan in feasible_plans(s, bus):
        indices = [route_order[st] for st in plan]
        assert indices == sorted(indices, reverse=True), f"Plan {plan} not in KB route order"


def test_minimum_two_stops_required():
    """Every valid plan for this route has at least 2 stops (540/240 = 2.25)."""
    s = _scenario_01()
    for bus in s.buses:
        for plan in feasible_plans(s, bus):
            assert len(plan) >= 2
