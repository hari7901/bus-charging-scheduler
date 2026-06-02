"""
Scheduler type aliases.

These aliases give meaningful names to the nested dict structures that carry
CP-SAT variables through the solver's private methods.  Importing them from
here (rather than defining them inline in solver.py) keeps solver.py focused
on logic and makes it easier to swap in new variable shapes if the model
structure ever changes.

Usage:
    from scheduler.types import IntVarByIdx, IntVarByStation
"""

from __future__ import annotations

from typing import Any

# Maps bus_id → node_index → OR-Tools IntVar (or BoolVar).
# Used for the departure-time variables: depart[bus_id][node_idx].
IntVarByIdx = dict[str, dict[int, Any]]

# Maps bus_id → station_id → OR-Tools IntVar (or BoolVar).
# Used for active[bus_id][station_id] and charge_start[bus_id][station_id].
IntVarByStation = dict[str, dict[str, Any]]
