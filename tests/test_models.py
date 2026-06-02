"""Tests for Pydantic models, loading, and validation helpers."""

from __future__ import annotations

import pytest
from pathlib import Path

from scheduler.models import (
    Bus,
    Scenario,
    load_all_scenarios,
    load_scenario,
    minutes_to_hhmm,
    format_duration,
)

DATA_DIR = Path(__file__).parent.parent / "data" / "scenarios"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_load_all_scenarios_count():
    scenarios = load_all_scenarios(DATA_DIR)
    assert len(scenarios) == 5


def test_scenario_01_basics():
    scenarios = load_all_scenarios(DATA_DIR)
    s = next(s for s in scenarios if s.metadata.id == "scenario_01")
    assert len(s.buses) == 20
    assert s.weights.individual == 1.0
    assert s.weights.operator == 1.0


def test_scenario_03_asymmetric():
    """Scenario 3 has only 14 buses — the engine accepts any count."""
    scenarios = load_all_scenarios(DATA_DIR)
    s = next(s for s in scenarios if s.metadata.id == "scenario_03")
    assert len(s.buses) == 14


def test_scenario_04_operator_weight():
    scenarios = load_all_scenarios(DATA_DIR)
    s = next(s for s in scenarios if s.metadata.id == "scenario_04")
    assert s.weights.operator == 2.0


# ---------------------------------------------------------------------------
# Cross-field validation
# ---------------------------------------------------------------------------

def test_duplicate_bus_ids_rejected():
    scenarios = load_all_scenarios(DATA_DIR)
    s = scenarios[0]
    raw = s.model_dump()
    raw["buses"].append(raw["buses"][0].copy())  # duplicate first bus
    with pytest.raises(Exception, match="Duplicate bus IDs"):
        Scenario.model_validate(raw)


def test_unknown_operator_rejected():
    scenarios = load_all_scenarios(DATA_DIR)
    s = scenarios[0]
    raw = s.model_dump()
    raw["buses"][0]["operator"] = "ghost_operator"
    with pytest.raises(Exception, match="unknown operator"):
        Scenario.model_validate(raw)


def test_invalid_station_id_rejected():
    scenarios = load_all_scenarios(DATA_DIR)
    s = scenarios[0]
    raw = s.model_dump()
    raw["stations"][0]["id"] = "Z"  # not in route
    with pytest.raises(Exception, match="not found in route"):
        Scenario.model_validate(raw)


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------

def test_route_nodes_bk():
    s = load_all_scenarios(DATA_DIR)[0]
    bus = next(b for b in s.buses if b.origin == "bengaluru")
    nodes = s.route_nodes_for_bus(bus)
    assert nodes[0] == "bengaluru"
    assert nodes[-1] == "kochi"
    assert "A" in nodes and "B" in nodes


def test_route_nodes_kb():
    s = load_all_scenarios(DATA_DIR)[0]
    bus = next(b for b in s.buses if b.origin == "kochi")
    nodes = s.route_nodes_for_bus(bus)
    assert nodes[0] == "kochi"
    assert nodes[-1] == "bengaluru"
    assert nodes.index("D") < nodes.index("C")


def test_intermediate_stations_bk():
    s = load_all_scenarios(DATA_DIR)[0]
    bus = next(b for b in s.buses if b.origin == "bengaluru")
    stations = s.intermediate_stations_for_bus(bus)
    assert stations == ["A", "B", "C", "D"]


def test_intermediate_stations_kb():
    s = load_all_scenarios(DATA_DIR)[0]
    bus = next(b for b in s.buses if b.origin == "kochi")
    stations = s.intermediate_stations_for_bus(bus)
    assert stations == ["D", "C", "B", "A"]


def test_travel_time_100km_60kmh():
    s = load_all_scenarios(DATA_DIR)[0]
    assert s.travel_time_between("bengaluru", "A") == 100  # 100km / 60kmh = 100 min


def test_travel_time_120km():
    s = load_all_scenarios(DATA_DIR)[0]
    assert s.travel_time_between("A", "B") == 120  # 120km / 60kmh = 120 min


# ---------------------------------------------------------------------------
# Bus departure parsing
# ---------------------------------------------------------------------------

def test_departure_minutes():
    s = load_all_scenarios(DATA_DIR)[0]
    bus = next(b for b in s.buses if b.departure == "19:00")
    assert bus.departure_minutes() == 19 * 60


def test_departure_invalid_format():
    with pytest.raises(Exception):
        Bus(id="x", operator="kpn", origin="bengaluru", destination="kochi", departure="25:00")


# ---------------------------------------------------------------------------
# Time formatting helpers
# ---------------------------------------------------------------------------

def test_minutes_to_hhmm_simple():
    assert minutes_to_hhmm(19 * 60) == "19:00"
    assert minutes_to_hhmm(19 * 60 + 15) == "19:15"


def test_minutes_to_hhmm_next_day():
    assert "(+1d)" in minutes_to_hhmm(24 * 60 + 30)


def test_format_duration():
    assert format_duration(25) == "25m"
    assert format_duration(90) == "1h 30m"
    assert format_duration(120) == "2h"
