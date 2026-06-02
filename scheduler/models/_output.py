"""
Output data models produced by the solver.

These models are plain, validated containers for the scheduler's results.
They carry no business logic — the solver writes them, the UI reads them.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel


class ChargeEvent(BaseModel):
    """A single charging event for one bus at one station."""
    station_id: str
    arrival_min: int
    wait_min: int
    charge_start_min: int
    charge_end_min: int

    model_config = {"extra": "forbid"}


class BusSchedule(BaseModel):
    """Complete timeline for a single bus."""
    bus_id: str
    operator: str
    origin: str
    destination: str
    departure_min: int
    charge_events: list[ChargeEvent]
    arrival_min: int
    total_wait_min: int

    model_config = {"extra": "forbid"}


class ScheduleResult(BaseModel):
    """Full solver output for one scenario."""
    scenario_id: str
    solver_status: str
    solve_time_sec: float
    bus_schedules: list[BusSchedule]

    model_config = {"extra": "forbid"}

    def station_timeline(self) -> dict[str, list[tuple]]:
        """
        Return {station_id: [(ChargeEvent, bus_id, operator), ...]} sorted by
        charge_start_min.  Useful for rendering per-station charging order.
        """
        result: dict[str, list] = defaultdict(list)
        for bs in self.bus_schedules:
            for ev in bs.charge_events:
                result[ev.station_id].append((ev, bs.bus_id, bs.operator))
        for st_id in result:
            result[st_id].sort(key=lambda t: t[0].charge_start_min)
        return dict(result)
