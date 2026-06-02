"""
scheduler.utils — shared utility functions.

Sub-module layout
-----------------
time.py — display helpers: minutes_to_hhmm, format_duration

Usage:
    from scheduler.utils.time import minutes_to_hhmm, format_duration
    # or for convenience:
    from scheduler.utils import minutes_to_hhmm, format_duration
"""

from .time import format_duration, minutes_to_hhmm

__all__ = ["minutes_to_hhmm", "format_duration"]
