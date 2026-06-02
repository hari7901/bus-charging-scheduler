"""
scheduler — Bus Charging Scheduler package.

Public API
----------
The most commonly used names are re-exported here so callers can write:

    from scheduler import Scenario, CpSatBackend, solve
    from scheduler import load_scenario, load_all_scenarios

For specialised usage (custom rules, plan generators, type aliases) import
from the sub-packages directly:

    from scheduler.rules import BaseRule, REGISTERED_RULES
    from scheduler.plans import PlanGenerator
    from scheduler.constants import WEIGHT_SCALE, DEFAULT_TIME_LIMIT_SEC
    from scheduler.types import IntVarByIdx, IntVarByStation
    from scheduler.utils.time import minutes_to_hhmm, format_duration
"""

# Core data models
from .models import (
    Bus,
    BusSchedule,
    ChargeEvent,
    Metadata,
    Operator,
    Parameters,
    Route,
    RouteNode,
    Scenario,
    ScheduleResult,
    Segment,
    Station,
    Weights,
    format_duration,
    load_all_scenarios,
    load_scenario,
    minutes_to_hhmm,
)

# Solver backends
from .solver import CpSatBackend, SchedulerBackend, solve

__all__ = [
    # Input models
    "Metadata",
    "Parameters",
    "RouteNode",
    "Segment",
    "Route",
    "Station",
    "Operator",
    "Weights",
    "Bus",
    "Scenario",
    # Output models
    "ChargeEvent",
    "BusSchedule",
    "ScheduleResult",
    # Loader
    "load_scenario",
    "load_all_scenarios",
    # Solver
    "SchedulerBackend",
    "CpSatBackend",
    "solve",
    # Display utilities
    "minutes_to_hhmm",
    "format_duration",
]
