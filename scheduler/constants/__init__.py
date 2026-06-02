"""
Scheduler-wide constants.

All magic numbers used by the engine live here so they are easy to find,
change in one place, and document with a comment explaining why the value
was chosen.

Usage:
    from scheduler.constants import WEIGHT_SCALE, DEFAULT_TIME_LIMIT_SEC
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Solver constants
# ---------------------------------------------------------------------------

# CP-SAT works with integer coefficients only.
# Objective weights come from the YAML as floats (e.g. 1.5), so they are
# multiplied by this scale factor before being passed to the model.
# 100 gives two decimal places of precision (e.g. 1.5 → 150).
WEIGHT_SCALE: int = 100

# Default wall-clock budget given to the CP-SAT solver per scenario.
# The solver returns the best feasible solution found within this limit.
# Can be overridden per solve() call without touching this constant.
DEFAULT_TIME_LIMIT_SEC: float = 15.0
