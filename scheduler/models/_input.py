"""
Input data models for the bus charging scheduler.

These Pydantic models represent the scenario file — the single source of truth
for all world state fed into the solver.  Every field that the scheduler or UI
touches is declared here; extra fields in YAML are rejected immediately so
typos surface as clear validation errors rather than silent bugs.

Design notes
------------
- All time values are stored as integer minutes-from-midnight internally;
  conversion to display strings happens only at the output layer (_time.py).
- Hot-path helpers (_station_map, _cum_distances) are @cached_property so the
  solver loop never recomputes them across calls within the same Scenario object.
- Cross-field validators run after all fields are populated, making constraint
  errors point to the scenario file rather than deep inside the engine.
"""

from __future__ import annotations

import math
from collections import Counter
from functools import cached_property
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Primitive sub-models
# ---------------------------------------------------------------------------

class Metadata(BaseModel):
    """Scenario identity — used for display and caching keys."""
    id: str
    name: str
    description: str = ""

    model_config = {"extra": "forbid"}


class Parameters(BaseModel):
    """Physical constants that govern every bus on this scenario."""
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
    """A named stop on the route (terminal or charging station)."""
    id: str
    name: str

    model_config = {"extra": "forbid"}


class Segment(BaseModel):
    """A directed road segment between two adjacent route nodes."""
    from_: str
    to: str
    distance_km: float

    # Allow the YAML key 'from' to populate 'from_' transparently.
    model_config = {"extra": "forbid", "populate_by_name": True}


class Route(BaseModel):
    """The full ordered route: nodes in traversal order plus segment distances."""
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
        """Distance for a direct adjacent segment (O(n); prefer distance_between)."""
        for seg in self.segments:
            if seg.from_ == from_id and seg.to == to_id:
                return seg.distance_km
        raise ValueError(f"No segment {from_id} → {to_id}")

    @cached_property
    def _cum_distances(self) -> dict[str, float]:
        """
        Cumulative distance from the first node to every other node (forward).
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
        O(1) after the first call thanks to the cached cumulative-distance map.
        """
        cum = self._cum_distances
        if from_id not in cum or to_id not in cum:
            raise ValueError(f"Unknown node(s): {from_id}, {to_id}")
        return abs(cum[to_id] - cum[from_id])


class Station(BaseModel):
    """A charging station along the route."""
    id: str
    name: str
    charger_count: int = 1
    charge_duration_min: Optional[int] = None  # None → inherit from Parameters

    model_config = {"extra": "forbid"}


class Operator(BaseModel):
    """A bus operating company."""
    id: str
    name: str

    model_config = {"extra": "forbid"}


class Weights(BaseModel):
    """
    Objective weights for the scheduler.

    extra='allow' means new weight keys can be added to the YAML and consumed
    by new rules without touching this class.  Existing rules read their named
    fields; new rules can read extra keys via the get() helper.
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
    """A single bus with its operator, direction, and departure time."""
    id: str
    operator: str
    origin: str
    destination: str
    departure: str  # stored as "HH:MM"; converted lazily via departure_minutes()

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
# Top-level scenario model
# ---------------------------------------------------------------------------

class Scenario(BaseModel):
    """
    Complete description of one scheduling scenario.

    This is the single entry-point for all world state consumed by the solver
    and the UI.  Every cross-field constraint is validated here so downstream
    code can assume a consistent, fully-checked object.
    """
    metadata: Metadata
    parameters: Parameters
    route: Route
    stations: list[Station]
    operators: list[Operator]
    weights: Weights
    buses: list[Bus]

    model_config = {"extra": "forbid"}

    # ------------------------------------------------------------------
    # Cross-field validation
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def check_station_ids_in_route(self) -> "Scenario":
        route_node_ids = set(self.route.node_ids())
        for st in self.stations:
            if st.id not in route_node_ids:
                raise ValueError(f"Station '{st.id}' not found in route nodes")
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
                raise ValueError(f"Bus '{bus.id}' origin '{bus.origin}' not in route")
            if bus.destination not in node_ids:
                raise ValueError(
                    f"Bus '{bus.id}' destination '{bus.destination}' not in route"
                )
            if bus.origin == bus.destination:
                raise ValueError(f"Bus '{bus.id}' origin and destination are the same")
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
                raise ValueError(f"Station '{st.id}' charger_count must be >= 1")
        return self

    # ------------------------------------------------------------------
    # Cached helpers (hot-path safe)
    # ------------------------------------------------------------------

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
            raise ValueError(f"Bus '{bus.id}' endpoint not in route: {e}") from e
        if start_idx < end_idx:
            return all_nodes[start_idx : end_idx + 1]
        return list(reversed(all_nodes[end_idx : start_idx + 1]))

    def intermediate_stations_for_bus(self, bus: Bus) -> list[str]:
        """Return station IDs that lie along a bus's path (excluding endpoints)."""
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
        Accounts for the latest departure, full-trip travel time, and a
        worst-case queue at every station.
        """
        latest_dep = self.max_departure_minutes()
        total_travel = self.parameters.travel_time_min(
            sum(seg.distance_km for seg in self.route.segments)
        )
        n_buses = len(self.buses)
        max_charge = max(self.effective_charge_duration(s.id) for s in self.stations)
        max_queue = n_buses * len(self.stations) * max_charge
        return latest_dep + total_travel + max_queue
