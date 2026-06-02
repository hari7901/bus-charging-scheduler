"""
Feasible charging-plan generation.

OOP pillars demonstrated here
------------------------------
Abstraction:   PlanGenerator is a formal ABC. The solver depends on this
               interface, not on any specific algorithm. You can swap
               SubsetEnumerationGenerator for a DP-based generator for large
               routes without touching solver.py.

Inheritance:   SubsetEnumerationGenerator inherits PlanGenerator and provides
               the 2^n subset-enumeration implementation.

Polymorphism:  CpSatBackend holds a PlanGenerator reference. At runtime it
               calls self.plan_generator.get_plans(...), dispatching to
               whatever concrete implementation was injected. A different
               generator (e.g. DPPlanGenerator for 20+ stations) can be
               swapped in at construction time.

Encapsulation: _is_valid_plan and _earliest_arrival_time are private helpers
               — they are implementation details of SubsetEnumerationGenerator,
               not part of the public contract.

A charging plan is a tuple of station IDs (in traversal order) that a bus
will stop at to recharge.  A plan is valid iff every consecutive pair of
checkpoints (origin → stop₁ → stop₂ → … → destination) is within the
battery range.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from itertools import combinations

from .models import Bus, Scenario


# ---------------------------------------------------------------------------
# Abstract interface (Abstraction)
# ---------------------------------------------------------------------------

class PlanGenerator(ABC):
    """
    Abstract interface for generating feasible charging plans.

    A 'plan' is a tuple of station IDs in the order a bus visits them.
    Concrete subclasses implement different enumeration strategies.
    """

    @abstractmethod
    def get_plans(self, scenario: Scenario, bus: Bus) -> list[tuple[str, ...]]:
        """
        Return every valid charging-stop sequence for *bus* in *scenario*.

        A plan is valid iff no consecutive pair of checkpoints (including
        the origin and destination as implicit stops) requires the bus to
        travel more than battery_range_km.

        Raises ValueError if no feasible plan exists for this bus.
        """

    def min_stop_plan(self, scenario: Scenario, bus: Bus) -> tuple[str, ...]:
        """
        Return the plan with the fewest stops (earliest-arrival heuristic).
        Ties broken by preferring earlier stations along the route.
        Default implementation: pick the minimum-length plan from get_plans().
        Subclasses may override for a faster direct path.
        """
        plans = self.get_plans(scenario, bus)
        return min(plans, key=lambda p: (len(p), p))


# ---------------------------------------------------------------------------
# Concrete implementation — subset enumeration (Inheritance + Encapsulation)
# ---------------------------------------------------------------------------

class SubsetEnumerationGenerator(PlanGenerator):
    """
    Generates feasible charging plans by exhaustively testing all 2ⁿ subsets
    of intermediate stations.

    Time complexity: O(2ⁿ × k) where n = intermediate stations, k = stops/plan.
    Suitable for up to ~12 intermediate stations. For larger routes, replace
    with DPPlanGenerator (dynamic programming over the route graph).
    """

    def get_plans(self, scenario: Scenario, bus: Bus) -> list[tuple[str, ...]]:
        intermediate = scenario.intermediate_stations_for_bus(bus)
        path = scenario.route_nodes_for_bus(bus)
        battery = scenario.parameters.battery_range_km

        valid_plans: list[tuple[str, ...]] = [
            combo
            for r in range(len(intermediate) + 1)
            for combo in combinations(intermediate, r)
            if self._is_valid(combo, path, battery, scenario)
        ]

        if not valid_plans:
            raise ValueError(
                f"No feasible charging plan for bus '{bus.id}'. "
                f"Check battery range vs. route distances."
            )
        return valid_plans

    # ------------------------------------------------------------------
    # Private helpers (Encapsulation — not part of PlanGenerator contract)
    # ------------------------------------------------------------------

    def _is_valid(
        self,
        charge_stops: tuple[str, ...],
        full_path: list[str],
        battery: float,
        scenario: Scenario,
    ) -> bool:
        """
        True iff every consecutive gap between checkpoints fits in battery range.
        Checkpoints = [origin] + selected charge stops + [destination].
        """
        charge_set = set(charge_stops)
        checkpoints = [full_path[0]]
        for node in full_path[1:-1]:
            if node in charge_set:
                checkpoints.append(node)
        checkpoints.append(full_path[-1])

        return all(
            scenario.route.distance_between(checkpoints[i], checkpoints[i + 1]) <= battery
            for i in range(len(checkpoints) - 1)
        )


# ---------------------------------------------------------------------------
# Module-level helpers (backward-compatible convenience wrappers)
# ---------------------------------------------------------------------------

# Shared instance — avoids re-instantiating for every call site.
_default_generator = SubsetEnumerationGenerator()


def feasible_plans(scenario: Scenario, bus: Bus) -> list[tuple[str, ...]]:
    """Backward-compatible wrapper around SubsetEnumerationGenerator.get_plans()."""
    return _default_generator.get_plans(scenario, bus)


def earliest_arrival_time(
    scenario: Scenario,
    bus: Bus,
    plan: tuple[str, ...],
    charge_start_times: dict[str, int],
) -> int:
    """
    Compute the arrival time (minutes from midnight) at the destination,
    given a chosen plan and charger availability times per station.
    """
    full_path = scenario.route_nodes_for_bus(bus)
    current_time = bus.departure_minutes()
    current_node = full_path[0]

    for station_id in plan:
        travel = scenario.travel_time_between(current_node, station_id)
        arrival = current_time + travel
        charge_start = max(arrival, charge_start_times.get(station_id, arrival))
        current_time = charge_start + scenario.effective_charge_duration(station_id)
        current_node = station_id

    return current_time + scenario.travel_time_between(current_node, full_path[-1])
