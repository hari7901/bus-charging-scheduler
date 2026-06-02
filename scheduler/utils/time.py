"""
Time formatting utilities — display layer only.

These helpers convert integer minutes-from-midnight (the engine's internal
representation) into human-readable strings for the UI.  They carry no
scheduling logic and have no dependencies on any other scheduler module,
making them safe to import anywhere without risk of circular imports.

Usage:
    from scheduler.utils.time import minutes_to_hhmm, format_duration
"""

from __future__ import annotations


def minutes_to_hhmm(minutes: int) -> str:
    """
    Convert absolute minutes-from-midnight to an 'HH:MM' display string.
    Values >= 1440 (past midnight) are shown as 'HH:MM (+Nd)'.

    Examples:
        minutes_to_hhmm(1140)  → '19:00'
        minutes_to_hhmm(1500)  → '01:00 (+1d)'
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
    """
    Format a duration in minutes as a human-readable string.

    Examples:
        format_duration(25)  → '25m'
        format_duration(90)  → '1h 30m'
        format_duration(120) → '2h'
    """
    if minutes < 60:
        return f"{minutes}m"
    h = minutes // 60
    m = minutes % 60
    if m:
        return f"{h}h {m}m"
    return f"{h}h"
