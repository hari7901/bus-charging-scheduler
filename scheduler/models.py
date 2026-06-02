"""
Pydantic data models for the bus charging scheduler.

Design principles:
- The scenario file is the single source of truth for all world state.
- Models are strict (extra fields are forbidden) so typos in YAML surface immediately.
- All time values inside the engine are stored as minutes from midnight (int),
  converted to display strings only at output.
- Hot-path helpers (station_map, cumulative_distances) are cached so the solver
  loop never recomputes them across calls within the same scenario object.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from functools import cached_property
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class Metadata(BaseModel):
    id: str
    name: str
    description: str = ""

    model_config = {"extra": "forbid"}


class Parameters(BaseModel):
    battery_range_km: float
    charge_duration_min: int
    speed_kmh: float
    time_rounding: Literal["ceil", "floor", "round"] = "ceil"

    model_config = {"extra": "forbid"}

    def travel_time_min(self, distance_km: float) -> int:
        """Return travel time in whole minutes, applying configured rounding."""
        raw = (distance_km / self.speed_kmh) * 60.0
        if self.time_rounding == "ceil":
            return math.ceil(raw)
        if self.time_rounding == "floor":
            return math.floor(raw)
        return round(raw)


class RouteNode(BaseModel):
    id: str
    name: str

    model_config = {"extra": "forbid"}


class Segment(BaseModel):
    from_: str
    to: str
    distance_km: float

    # Allow the YAML key 'from' to populate 'from_' transparently.
    # populate_by_name lets code pass either 'from_' or the alias.
    model_config = {"extra": "forbid", "populate_by_name": True}


class Route(BaseModel):
    nodes: list[RouteNode]
    segments: list[Segment]

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def check_segments_cover_adjacent_nodes(self) -> "Route":
        node_ids = [n.id for n in self.nodes]
        segment_map = {(s.from_, s.to): s for s in self.segments}
        for i in range(len(node_ids) - 1):
            pair = (node_ids[i], node_ids[i + 1])
            if pair not in segment_map:
                raise ValueError(
                    f"Missing segment between adjacent nodes {pair[0]} → {pair[1]}"
                )
        return self

    def node_ids(self) -> list[str]:
        return [n.id for n in self.nodes]

    def segment_distance(self, from_id: str, to_id: str) -> float:
        """Distance for a direct adjacent segment (O(n) scan; use cached_cum_distances for repeated calls)."""
        for seg in self.segments:
            if seg.from_ == from_id and seg.to == to_id:
                return seg.distance_km
        raise ValueError(f"No segment {from_id} → {to_id}")

    @cached_property
    def _cum_distances(self) -> dict[str, float]:
        """
        Cumulative distance from the first node to each node (forward direction).
        Cached after first computation — the route is immutable once loaded.
        """
        node_ids = self.node_ids()
        cum: dict[str, float] = {node_ids[0]: 0.0}
        for i in range(len(node_ids) - 1):
            cum[node_ids[i + 1]] = cum[node_ids[i]] + self.segment_distance(
                node_ids[i], node_ids[i + 1]
            )
        return cum

    def distance_between(self, from_id: str, to_id: str) -> float:
        """
        Distance along the route between any two nodes (forward or backward).
        Uses the cached cumulative-distance map — O(1) after first call.
        """
        cum = self._cum_distances
        if from_id not in cum or to_id not in cum:
            raise ValueError(f"Unknown node(s): {from_id}, {to_id}")
        return abs(cum[to_id] - cum[from_id])


class Station(BaseModel):
    id: str
    name: str
    charger_count: int = 1
    # Per-station override; inherits from parameters if None
    charge_duration_min: Optional[int] = None

    model_config = {"extra": "forbid"}


class Operator(BaseModel):
    id: str
    name: str

    model_config = {"extra": "forbid"}


class Weights(BaseModel):
    """
    Objective weights for the scheduler.

    extra='allow' means new weight keys can be added to the YAML and read by
    new rules without modifying this class.  Existing rules read their own
    named fields; new rules can read extras via scenario.weights.model_extra.
    """
    individual: float = 1.0
    operator: float = 1.0
    overall: float = 1.0

    model_config = {"extra": "allow"}

    def get(self, key: str, default: float = 0.0) -> float:
        """Read any weight by name, including extra keys from YAML."""
        if hasattr(self, key):
            return float(getattr(self, key))
        return float((self.model_extra or {}).get(key, default))


class Bus(BaseModel):
    id: str
    operator: str
    origin: str
    destination: str
    departure: str  # stored as "HH:MM" string; converted lazily

    model_config = {"extra": "forbid"}

    @field_validator("departure")
    @classmethod
    def departure_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"Departure must be HH:MM, got '{v}'")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"Departure time out of range: '{v}'")
        return v

    def departure_minutes(self) -> int:
        """Departure as minutes from midnight."""
        h, m = self.departure.split(":")
        return int(h) * 60 + int(m)


# ---------------------------------------------------------------------------
# Top-level scenario
# ---------------------------------------------------------------------------

class Scenario(BaseModel):
    metadata: Metadata
    parameters: Parameters
    route: Route
    stations: list[Station]
    operators: list[Operator]
    weights: Weights
    buses: list[Bus]

    model_config = {"extra": "forbid"}

    # --- Cross-field validation ---

    @model_validator(mode="after")
    def check_station_ids_in_route(self) -> "Scenario":
        route_node_ids = set(self.route.node_ids())
        for st in self.stations:
            if st.id not in route_node_ids:
                raise ValueError(
                    f"Station '{st.id}' not found in route nodes"
                )
        return self

    @model_validator(mode="after")
    def check_bus_operators_known(self) -> "Scenario":
        known = {op.id for op in self.operators}
        for bus in self.buses:
            if bus.operator not in known:
                raise ValueError(
                    f"Bus '{bus.id}' references unknown operator '{bus.operator}'"
                )
        return self

    @model_validator(mode="after")
    def check_bus_endpoints_in_route(self) -> "Scenario":
        node_ids = set(self.route.node_ids())
        for bus in self.buses:
            if bus.origin not in node_ids:
                raise ValueError(
                    f"Bus '{bus.id}' origin '{bus.origin}' not in route"
                )
            if bus.destination not in node_ids:
                raise ValueError(
                    f"Bus '{bus.id}' destination '{bus.destination}' not in route"
                )
            if bus.origin == bus.destination:
                raise ValueError(
                    f"Bus '{bus.id}' origin and destination are the same"
                )
        return self

    @model_validator(mode="after")
    def check_no_duplicate_bus_ids(self) -> "Scenario":
        counts = Counter(b.id for b in self.buses)
        dupes = {bid for bid, cnt in counts.items() if cnt > 1}
        if dupes:
            raise ValueError(f"Duplicate bus IDs: {dupes}")
        return self

    @model_validator(mode="after")
    def check_charger_counts_positive(self) -> "Scenario":
        for st in self.stations:
            if st.charger_count < 1:
                raise ValueError(
                    f"Station '{st.id}' charger_count must be >= 1"
                )
        return self

    # --- Cached helpers (hot-path safe) ---

    @cached_property
    def _station_map(self) -> dict[str, Station]:
        return {s.id: s for s in self.stations}

    def station_ids(self) -> list[str]:
        return [s.id for s in self.stations]

    def station_map(self) -> dict[str, Station]:
        """Return {station_id: Station}. Cached after first call."""
        return self._station_map

    def effective_charge_duration(self, station_id: str) -> int:
        """Charge duration at a station (station override or global default)."""
        st = self._station_map.get(station_id)
        if st and st.charge_duration_min is not None:
            return st.charge_duration_min
        return self.parameters.charge_duration_min

    def route_nodes_for_bus(self, bus: Bus) -> list[str]:
        """
        Return the ordered list of route node IDs a bus will traverse,
        based on its origin and destination (handles both directions).
        """
        all_nodes = self.route.node_ids()
        try:
            start_idx = all_nodes.index(bus.origin)
            end_idx = all_nodes.index(bus.destination)
        except ValueError as e:
            raise ValueError(
                f"Bus '{bus.id}' endpoint not in route: {e}"
            ) from e
        if start_idx < end_idx:
            return all_nodes[start_idx : end_idx + 1]
        return list(reversed(all_nodes[end_idx : start_idx + 1]))

    def intermediate_stations_for_bus(self, bus: Bus) -> list[str]:
        """Return station IDs that are along a bus's path (excluding endpoints)."""
        path = self.route_nodes_for_bus(bus)
        st_ids = set(self.station_ids())
        return [n for n in path[1:-1] if n in st_ids]

    def travel_time_between(self, from_id: str, to_id: str) -> int:
        """Travel time in minutes between any two route nodes."""
        dist = self.route.distance_between(from_id, to_id)
        return self.parameters.travel_time_min(dist)

    def max_departure_minutes(self) -> int:
        """Latest departure time among all buses in this scenario."""
        return max(b.departure_minutes() for b in self.buses)

    def absolute_time_upper_bound(self) -> int:
        """
        Conservative upper bound (minutes from midnight) for any time variable.
        Accounts for the latest departure, full trip travel time, and a
        worst-case queue at every station.
        """
        latest_dep = self.max_departure_minutes()
        total_travel = self.parameters.travel_time_min(
            sum(seg.distance_km for seg in self.route.segments)
        )
        n_buses = len(self.buses)
        max_charge = max(
            self.effective_charge_duration(s.id) for s in self.stations
        )
        max_queue = n_buses * len(self.stations) * max_charge
        return latest_dep + total_travel + max_queue


# ---------------------------------------------------------------------------
# Output models (produced by solver)
# ---------------------------------------------------------------------------

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
    """Full solver output for a scenario."""
    scenario_id: str
    solver_status: str
    solve_time_sec: float
    bus_schedules: list[BusSchedule]

    model_config = {"extra": "forbid"}

    def station_timeline(self) -> dict[str, list[tuple]]:
        """
        Returns {station_id: [(ChargeEvent, bus_id, operator), ...]}
        sorted by charge_start_min.
        """
        result: dict[str, list] = defaultdict(list)
        for bs in self.bus_schedules:
            for ev in bs.charge_events:
                result[ev.station_id].append((ev, bs.bus_id, bs.operator))
        for st_id in result:
            result[st_id].sort(key=lambda t: t[0].charge_start_min)
        return dict(result)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _normalise_segments(raw: dict) -> dict:
    """
    Translate YAML 'from' keys to 'from_' before Pydantic validation.
    Mutates a copy and returns it; original dict is not touched.
    """
    if "route" not in raw or "segments" not in raw.get("route", {}):
        return raw
    raw = dict(raw)
    raw["route"] = dict(raw["route"])
    raw["route"]["segments"] = [
        {**{("from_" if k == "from" else k): v for k, v in seg.items()}}
        for seg in raw["route"]["segments"]
    ]
    return raw


def load_scenario(path: Path | str) -> Scenario:
    """Load and validate a scenario YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Scenario.model_validate(_normalise_segments(raw))


def load_all_scenarios(directory: Path | str) -> list[Scenario]:
    """Load all .yaml scenario files from a directory, sorted by filename."""
    directory = Path(directory)
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise FileNotFoundError(f"No YAML scenario files found in {directory}")
    return [load_scenario(f) for f in files]


# ---------------------------------------------------------------------------
# Time formatting helpers (display layer only — not used by the engine)
# ---------------------------------------------------------------------------

def minutes_to_hhmm(minutes: int) -> str:
    """
    Convert absolute minutes-from-midnight to 'HH:MM' display string.
    Values >= 1440 (past midnight) are shown as 'HH:MM (+Nd)'.
    """
    if minutes < 0:
        raise ValueError(f"Negative time: {minutes}")
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
