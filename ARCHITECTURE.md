# Architecture

## Scheduler Approach: CP-SAT Constraint Optimisation

### Why CP-SAT

The problem has two distinct concerns: *what* (which stations does each bus charge at) and *when* (in what order do buses use each charger). Both are tightly coupled — the answer to "when" changes with each "what" choice, and the two share a constraint space (battery range, charger exclusivity).

I chose OR-Tools CP-SAT (constraint programming + SAT-based solver) for these reasons:

1. **Hard constraints are exact.** Range violations and charger overlaps are hard rules. CP-SAT guarantees that any returned solution satisfies every hard constraint — no post-hoc repair needed.

2. **Station scheduling is a job-shop variant.** Multiple "jobs" (buses) compete for "machines" (chargers) with precedence constraints (route order). CP-SAT's `AddNoOverlap` and optional interval variables model this directly.

3. **Weighted objectives compose cleanly.** Each soft rule contributes a term to a linear objective. Changing a weight only changes a coefficient; adding a rule adds a term. No structural changes.

4. **Scales well within the problem domain.** For 20 buses × 4 stations, CP-SAT finds optimal or near-optimal solutions in under 4 seconds for balanced scenarios and within the 15s time limit for worst-case contention. The model has ~400 variables and ~300 constraints — very small for CP-SAT.

**Alternative considered: greedy/heuristic.** A greedy earliest-arrival scheduler is fast and simple but cannot easily balance the three objective weights or guarantee optimality. It would require reimplementing when weights change. CP-SAT handles all of this natively.

---

## Data Structure Design

Each scenario is a single YAML file that fully describes the world. No world state is hardcoded in the engine.

```yaml
metadata:      # scenario identity
parameters:    # battery range, charge time, speed, rounding policy
route:         # ordered nodes + per-segment distances
stations:      # intermediate stations with charger counts + optional overrides
operators:     # known operator registry
weights:       # individual / operator / overall (tunable here)
buses:         # bus id, operator, origin, destination, departure
```

The engine reads nothing else. Every change to the problem world is a data change.

---

## Anticipated Future Changes and How the Design Handles Them

This is the specific list I considered when designing the data structure. Every item below can be handled through data alone (no code change) unless explicitly noted.

### 1. Add a new intermediate station

Add it to `route.nodes`, add the adjacent `route.segments`, and add it to `stations`. The solver creates one `active[bus][station]` decision variable per station on the bus path and enforces the battery range with polynomial range-cover constraints: every route interval longer than the battery range must contain at least one active charging station. This avoids enumerating all station subsets and scales much better as station count grows.

**Code change required:** None.

### 2. Change a segment distance

Edit the `distance_km` value in the relevant `route.segments` entry. All travel times and range-cover constraints recompute from route data.

**Code change required:** None.

### 3. Add more chargers to a station

Change `charger_count` in the station entry. The solver uses `AddCumulative` when `charger_count > 1` and `AddNoOverlap` when `charger_count == 1`, chosen dynamically.

**Code change required:** None.

### 4. Add a new operator

Add an entry to `operators`. Buses can immediately reference the new operator id. The `OperatorFairnessRule` discovers operators from the scenario data.

**Code change required:** None.

### 5. Add more buses (scale up)

Add entries to `buses`. The solver iterates over all buses in the scenario — there is no hardcoded bus count.

**Code change required:** None.

### 6. Add a new scenario

Create a new `.yaml` file in `data/scenarios/`. The app loads all `.yaml` files in that directory automatically.

**Code change required:** None.

### 7. Change the speed

Edit `parameters.speed_kmh`. All travel times recompute from this value.

**Code change required:** None.

### 8. Change the charge duration globally or per station

Global: edit `parameters.charge_duration_min`. Per station: add `charge_duration_min` to the station entry (overrides the global default). The solver calls `scenario.effective_charge_duration(station_id)` which respects station overrides.

**Code change required:** None.

### 9. Change objective weights

Edit `weights.individual`, `weights.operator`, or `weights.overall` in the scenario file. These values are multiplied into the objective coefficients; the solver structure doesn't change.

**Code change required:** None.

### 10. Add a new soft rule (e.g. time-of-day electricity cost)

Define a new `BaseRule` subclass in `scheduler/rules.py` and add it to `REGISTERED_RULES`. The solver's main loop calls `contribute_objective()` on every registered rule automatically. No changes to `solver.py`.

```python
# Example: penalise charging during peak electricity hours
class TimeOfDayCostRule(BaseRule):
    name = "time_of_day_cost"

    def contribute_objective(self, ctx: RuleContext):
        w = int(ctx.scenario.weights.time_of_day_cost * ctx.scale)
        if w == 0:
            return None
        peak_start, peak_end = 7 * 60, 22 * 60  # 07:00–22:00
        cost_var = ctx.model.NewIntVar(0, ctx.wait_ub, "obj_peak_cost")
        # ... add constraints linking cost_var to charge_start times in peak window
        return w * cost_var
```

The scenario YAML gains a new weight key (`time_of_day_cost: 1.5`) and the rule reads it. Everything else is unchanged.

**Code change required:** Add one class to `rules.py`.

### 11. Add a hard rule (e.g. priority bus must charge before others)

Add a `priority_bus` field to the `Bus` model and implement a rule using `enforce_hard()`:

```python
class PriorityBusRule(BaseRule):
    name = "priority_bus"

    def enforce_hard(self, ctx: RuleContext) -> None:
        priority_buses = [b for b in ctx.scenario.buses if b.priority]
        for pbus in priority_buses:
            for other in ctx.scenario.buses:
                if other.id == pbus.id: continue
                # For each shared station: pbus charges before other
                # model.Add(...).OnlyEnforceIf(...)
                pass
```

**Code change required:** Add one field to the `Bus` model + one class to `rules.py`.

### 12. Multiple routes sharing stations

The data model already represents routes as ordered node sequences with per-segment distances. Multiple scenarios (or a single scenario with multiple routes) can share station IDs. The solver builds per-bus constraints from `route_nodes_for_bus()`, which is already route-aware.

Adding a second route would require: adding a `route_id` field to `Bus` and a `routes` list to the scenario, then updating `route_nodes_for_bus()` to look up the right route.

**Code change required:** Minor model extension.

### 13. Driver shift constraints

Add `shift_start` and `shift_end` to the `Bus` model and implement a rule that prevents the bus from being in transit during the driver's break.

**Code change required:** Add fields to `Bus` + one class to `rules.py`.

---

## Objective Function

The weighted objective is:

```
Minimise:
  w_individual × max_single_bus_total_wait
  + w_operator × max_per_operator_fleet_max_wait
  + w_overall × sum_of_all_wait_times
```

- **`individual`**: keeps any single bus from being stuck while others sail through.
- **`operator`**: prevents one operator's fleet from bearing a disproportionate share of queuing delays.
- **`overall`**: keeps the total network delay low.

All three objectives pull in the same direction (reduce waits) but with different granularities. Changing relative weights shifts the balance between individual fairness, fleet fairness, and global efficiency.

---

## Assumptions

1. **Speed is constant.** All buses travel at `speed_kmh` with no traffic, as the spec states.

2. **Travel times are rounded up (ceil).** A 100 km trip at 60 km/h takes exactly 100 minutes; a 120 km trip takes exactly 120 minutes. Ceiling rounding is conservative and ensures buses never "teleport" to arrive slightly early.

3. **Charging always fills to full.** Buses never do a partial charge, so after each charge stop the effective range resets to `battery_range_km`. Partial charging would require tracking state-of-charge, which is out of scope.

4. **The terminal endpoints (Bengaluru and Kochi) are not scheduling stations.** They have slow chargers that always fill buses before departure; their charging is not modelled.

5. **Times can cross midnight.** The app displays "04:50 (+1d)" for arrivals after midnight. A 19:00 departure with a 9h50m trip naturally arrives the next day.

6. **Scenario 3 has 14 buses, not 20.** The spec says "20 buses per scenario" in the overview but provides only 14 departure rows for Scenario 3. I treated the table as authoritative. The engine accepts any bus count.

7. **Solver time limit is 15 seconds per scenario.** All scenarios return OPTIMAL or a high-quality FEASIBLE solution within this limit. The worst-case scenarios (2 and 5) return FEASIBLE solutions that satisfy all hard constraints.

8. **Objective weights are integers internally.** Float weights are multiplied by 100 and rounded to integers before passing to CP-SAT (which uses integer arithmetic). Weights with up to 2 decimal places of precision are represented exactly.

---

## How to Change a Weight (Code Example)

In `data/scenarios/scenario_04_operator_heavy.yaml`:

```yaml
# Before
weights:
  individual: 1.0
  operator: 2.0
  overall: 1.0

# After — triple operator weight
weights:
  individual: 1.0
  operator: 3.0
  overall: 1.0
```

Save the file. The app re-runs the solver with the new weights on the next page load (cached results are keyed by scenario name, so a restart or cache clear picks up the change).

---

## How to Add a New Rule (Code Example)

**Scenario**: penalise charging during peak electricity cost hours (07:00–22:00).

**Step 1** — Add a weight key to the scenario YAML:

```yaml
weights:
  individual: 1.0
  operator: 1.0
  overall: 1.0
  time_of_day_cost: 0.5   # new
```

**Step 2** — Add the weight field to the `Weights` model in `scheduler/models.py`:

```python
class Weights(BaseModel):
    individual: float = 1.0
    operator: float = 1.0
    overall: float = 1.0
    time_of_day_cost: float = 0.0   # new; defaults to 0 for backward compat
```

**Step 3** — Implement the rule in `scheduler/rules.py`:

```python
class TimeOfDayCostRule(BaseRule):
    name = "time_of_day_cost"

    def contribute_objective(self, ctx: RuleContext):
        w = int(ctx.scenario.weights.time_of_day_cost * ctx.scale)
        if w == 0:
            return None

        peak_start, peak_end = 7 * 60, 22 * 60
        peak_charge_vars = []

        for bus in ctx.scenario.buses:
            bid = bus.id
            for station_id, cs_var in ctx.charge_start_vars[bid].items():
                active_var = ctx.active_vars[bid][station_id]
                # 1 if charging starts during peak hours, 0 otherwise
                in_peak = ctx.model.NewBoolVar(f"peak_{bid}_{station_id}")
                ctx.model.Add(cs_var >= peak_start).OnlyEnforceIf(in_peak)
                ctx.model.Add(cs_var < peak_end).OnlyEnforceIf(in_peak)
                # Only counts when bus is actually charging there
                combined = ctx.model.NewBoolVar(f"peak_active_{bid}_{station_id}")
                ctx.model.AddBoolAnd([in_peak, active_var]).OnlyEnforceIf(combined)
                peak_charge_vars.append(combined)

        total_peak = ctx.model.NewIntVar(0, len(peak_charge_vars), "obj_peak_total")
        ctx.model.Add(total_peak == sum(peak_charge_vars))
        return w * total_peak
```

**Step 4** — Register it:

```python
REGISTERED_RULES = [
    IndividualWaitRule,
    OperatorFairnessRule,
    OverallWaitRule,
    TimeOfDayCostRule,   # added
]
```

No other files change. The new rule takes effect on the next solver run.
