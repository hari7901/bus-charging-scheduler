"""
scheduler.models — public API for all data models.

Import from this package, not from the sub-modules directly:

    from scheduler.models import Scenario, ScheduleResult, load_all_scenarios

Sub-module layout
-----------------
_input.py              — scenario input models  (Metadata → Scenario)
_output.py             — solver output models   (ChargeEvent → ScheduleResult)
_loader.py             — YAML loading functions (load_scenario, load_all_scenarios)
scheduler/utils/time.py — display-only helpers  (minutes_to_hhmm, format_duration)
"""

from ._input import (
    Bus,
    Metadata,
    Operator,
    Parameters,
    Route,
    RouteNode,
    Scenario,
    Segment,
    Station,
    Weights,
)
from ._loader import load_all_scenarios, load_scenario
from ._output import BusSchedule, ChargeEvent, ScheduleResult
from ..utils.time import format_duration, minutes_to_hhmm

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
    # Time utilities
    "minutes_to_hhmm",
    "format_duration",
]
