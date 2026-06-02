"""
Time formatting utilities — display layer only.

These helpers convert integer minutes-from-midnight (the engine's internal
representation) into human-readable strings.  They are intentionally kept
separate from the data models so the engine never depends on display logic.
"""

from __future__ import annotations


def minutes_to_hhmm(minutes: int) -> str:
    """
    Convert absolute minutes-from-midnight to an 'HH:MM' display string.
    Values >= 1440 (past midnight) are shown as 'HH:MM (+Nd)'.
    """
    if minutes < 0:
        raise ValueError(f"Negative time value: {minutes}")
    days_over = minutes // 1440
    mins_in_day = minutes % 1440
    hh = mins_in_day // 60
    mm = mins_in_day % 60
    base = f"{hh:02d}:{mm:02d}"
    if days_over:
        return f"{base} (+{days_over}d)"
    return base


def format_duration(minutes: int) -> str:
    """Format a duration in minutes as 'Xh Ym' or 'Ym'."""
    if minutes < 60:
        return f"{minutes}m"
    h = minutes // 60
    m = minutes % 60
    if m:
        return f"{h}h {m}m"
    return f"{h}h"
