"""
Shared pytest fixtures for the bus charging scheduler test suite.

Using session scope means the 5 YAML scenario files are parsed exactly once
per pytest run, rather than once per test function that calls load_all_scenarios.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scheduler.models import Scenario, load_all_scenarios

_DATA_DIR = Path(__file__).parent.parent / "data" / "scenarios"


@pytest.fixture(scope="session")
def all_scenarios() -> list[Scenario]:
    """All 5 loaded scenarios, parsed once for the entire test session."""
    return load_all_scenarios(_DATA_DIR)


@pytest.fixture(scope="session")
def scenario_01(all_scenarios: list[Scenario]) -> Scenario:
    return next(s for s in all_scenarios if s.metadata.id == "scenario_01")


@pytest.fixture(scope="session")
def scenario_03(all_scenarios: list[Scenario]) -> Scenario:
    return next(s for s in all_scenarios if s.metadata.id == "scenario_03")


@pytest.fixture(scope="session")
def scenario_04(all_scenarios: list[Scenario]) -> Scenario:
    return next(s for s in all_scenarios if s.metadata.id == "scenario_04")
