"""
Bus Charging Scheduler — Streamlit App

Layout:
  1. Scenario dropdown (top)
  2. Scenario input view (route, stations, buses table, weights)
  3. Solver status & summary
  4. Per-bus timetable
  5. Per-station charging order
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import streamlit as st

from scheduler.models import (
    Scenario,
    ScheduleResult,
    format_duration,
    load_all_scenarios,
    minutes_to_hhmm,
)
from scheduler.solver import CpSatBackend, SchedulerBackend

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Bus Charging Scheduler",
    page_icon="🚌",
    layout="wide",
)

DATA_DIR = Path(__file__).parent / "data" / "scenarios"

# ---------------------------------------------------------------------------
# Load scenarios (cached so YAML files are parsed once)
# ---------------------------------------------------------------------------

@st.cache_data
def _load_scenarios() -> dict[str, Scenario]:
    scenarios = load_all_scenarios(DATA_DIR)
    return {s.metadata.name: s for s in scenarios}


def _make_backend() -> SchedulerBackend:
    """
    Factory for the scheduling backend.

    Returns a CpSatBackend by default.  To swap in a different backend
    (e.g. a greedy fallback during testing), change only this function —
    the rest of the UI is written against the SchedulerBackend interface.
    """
    return CpSatBackend()


@st.cache_data
def _run_solver(scenario_name: str) -> ScheduleResult:
    """Run the solver, cached per scenario name so re-renders don't re-solve."""
    scenarios = _load_scenarios()
    backend = _make_backend()
    return backend.solve(scenarios[scenario_name])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _direction_label(origin: str, dest: str) -> str:
    return f"{origin.capitalize()} → {dest.capitalize()}"


def _build_bus_table(result: ScheduleResult, scenario: Scenario) -> pd.DataFrame:
    rows = []
    for bs in result.bus_schedules:
        stops = " → ".join(e.station_id for e in bs.charge_events) or "—"
        waits = (
            ", ".join(f"{e.station_id}: {e.wait_min}m" for e in bs.charge_events)
            or "none"
        )
        rows.append(
            {
                "Bus": bs.bus_id,
                "Operator": bs.operator,
                "Direction": _direction_label(bs.origin, bs.destination),
                "Departs": minutes_to_hhmm(bs.departure_min),
                "Charges at": stops,
                "Wait details": waits,
                "Total wait": format_duration(bs.total_wait_min),
                "Arrives": minutes_to_hhmm(bs.arrival_min),
            }
        )
    return pd.DataFrame(rows)


def _build_station_tables(
    result: ScheduleResult, scenario: Scenario
) -> dict[str, pd.DataFrame]:
    station_timeline = result.station_timeline()
    tables: dict[str, pd.DataFrame] = {}
    for station in scenario.stations:
        sid = station.id
        entries = station_timeline.get(sid, [])
        rows = []
        for ev, bus_id, operator in entries:
            rows.append(
                {
                    "Bus": bus_id,
                    "Operator": operator,
                    "Arrives": minutes_to_hhmm(ev.arrival_min),
                    "Wait": format_duration(ev.wait_min),
                    "Charge start": minutes_to_hhmm(ev.charge_start_min),
                    "Charge end": minutes_to_hhmm(ev.charge_end_min),
                }
            )
        tables[sid] = pd.DataFrame(rows) if rows else pd.DataFrame()
    return tables


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("Bus Charging Scheduler")
    st.markdown(
        "Select a scenario to see the charging plan computed by the scheduler."
    )

    try:
        scenarios = _load_scenarios()
    except Exception as exc:
        st.error(f"Failed to load scenarios: {exc}")
        return

    scenario_names = list(scenarios.keys())
    selected_name = st.selectbox("Scenario", scenario_names, index=0)
    scenario = scenarios[selected_name]

    # -----------------------------------------------------------------------
    # Section 1: Scenario input view
    # -----------------------------------------------------------------------
    st.header("Scenario Input")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Route")
        node_ids = scenario.route.node_ids()
        route_rows = []
        for i in range(len(node_ids) - 1):
            seg_dist = scenario.route.distance_between(node_ids[i], node_ids[i + 1])
            route_rows.append(
                {
                    "From": node_ids[i],
                    "To": node_ids[i + 1],
                    "Distance (km)": seg_dist,
                    "Travel time (min)": scenario.parameters.travel_time_min(seg_dist),
                }
            )
        st.dataframe(pd.DataFrame(route_rows), hide_index=True, use_container_width=True)

        st.subheader("Parameters")
        params = scenario.parameters
        st.markdown(
            f"- **Battery range**: {params.battery_range_km} km  \n"
            f"- **Charge duration**: {params.charge_duration_min} min (always to full)  \n"
            f"- **Speed**: {params.speed_kmh} km/h  \n"
            f"- **Time rounding**: {params.time_rounding}"
        )

    with col2:
        st.subheader("Stations")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ID": s.id,
                        "Name": s.name,
                        "Chargers": s.charger_count,
                        "Charge time (min)": scenario.effective_charge_duration(s.id),
                    }
                    for s in scenario.stations
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )

        st.subheader("Objective Weights")
        w = scenario.weights
        st.markdown(
            f"| individual | operator | overall |\n"
            f"|:---:|:---:|:---:|\n"
            f"| {w.individual} | {w.operator} | {w.overall} |"
        )

    st.subheader("Buses")
    bus_input_rows = [
        {
            "Bus ID": b.id,
            "Operator": b.operator,
            "Direction": _direction_label(b.origin, b.destination),
            "Departure": b.departure,
        }
        for b in scenario.buses
    ]
    st.dataframe(
        pd.DataFrame(bus_input_rows), hide_index=True, use_container_width=True
    )

    with st.expander("Raw YAML input"):
        yaml_path = DATA_DIR / f"{scenario.metadata.id}_*.yaml"
        # Find the actual file
        matches = list(DATA_DIR.glob(f"{scenario.metadata.id}*.yaml"))
        if matches:
            st.code(matches[0].read_text(), language="yaml")

    st.divider()

    # -----------------------------------------------------------------------
    # Section 2: Run solver and show results
    # -----------------------------------------------------------------------
    st.header("Scheduler Output")

    with st.spinner("Running scheduler…"):
        try:
            result = _run_solver(selected_name)
        except Exception as exc:
            st.error(f"Solver error: {exc}")
            return

    # Status banner
    status_color = {
        "OPTIMAL": "green",
        "FEASIBLE": "blue",
        "INFEASIBLE": "red",
        "UNKNOWN": "orange",
        "MODEL_INVALID": "red",
    }.get(result.solver_status, "orange")

    st.markdown(
        f"**Solver status**: :{status_color}[{result.solver_status}]  "
        f"&nbsp;&nbsp; **Solve time**: {result.solve_time_sec:.2f}s"
    )

    if result.solver_status in ("INFEASIBLE", "MODEL_INVALID", "UNKNOWN"):
        st.error(
            "The scheduler could not find a valid schedule. "
            "Check that the scenario constraints are satisfiable."
        )
        return

    if not result.bus_schedules:
        st.warning("Solver returned a solution with no bus schedules.")
        return

    # Summary metrics
    total_waits = [bs.total_wait_min for bs in result.bus_schedules]
    st.markdown(
        f"**Buses scheduled**: {len(result.bus_schedules)}  "
        f"&nbsp;&nbsp; **Max single-bus wait**: {max(total_waits)}m  "
        f"&nbsp;&nbsp; **Total wait (all buses)**: {sum(total_waits)}m"
    )

    # -----------------------------------------------------------------------
    # Section 3: Per-bus timetable
    # -----------------------------------------------------------------------
    st.subheader("Per-Bus Timetable")
    st.markdown(
        "Each row shows the full timeline: departure, charging stops, "
        "wait at each stop, and final arrival."
    )

    bus_df = _build_bus_table(result, scenario)

    # Colour-code wait: highlight long waits
    def _style_wait(val: str) -> str:
        if val == "0m" or val == "none":
            return ""
        mins = 0
        for part in val.split():
            if part.endswith("h"):
                mins += int(part[:-1]) * 60
            elif part.endswith("m"):
                mins += int(part[:-1])
        if mins > 30:
            return "background-color: #ffe0e0"
        if mins > 10:
            return "background-color: #fff3cd"
        return ""

    styled = bus_df.style.map(_style_wait, subset=["Total wait"])
    st.dataframe(styled, hide_index=True, use_container_width=True)

    # Operator summary
    st.subheader("Operator Summary")
    op_rows = []
    op_bus_map: dict[str, list] = {}
    for bs in result.bus_schedules:
        op_bus_map.setdefault(bs.operator, []).append(bs)
    for op_id, bus_list in sorted(op_bus_map.items()):
        waits = [b.total_wait_min for b in bus_list]
        op_rows.append(
            {
                "Operator": op_id,
                "Buses": len(bus_list),
                "Total wait (sum)": f"{sum(waits)}m",
                "Max wait (any bus)": f"{max(waits)}m",
                "Avg wait": f"{sum(waits) / len(waits):.1f}m",
            }
        )
    st.dataframe(pd.DataFrame(op_rows), hide_index=True, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------------
    # Section 4: Per-station charging order
    # -----------------------------------------------------------------------
    st.subheader("Per-Station Charging Order")
    st.markdown(
        "Buses at each station sorted by charge-start time. "
        "Shows the order the scheduler decided, and any waiting."
    )

    station_tables = _build_station_tables(result, scenario)

    st.markdown(
        "_Hard rule check:_ "
        + _validate_hard_rules(result, scenario)
    )

    cols = st.columns(len(scenario.stations))
    for col, station in zip(cols, scenario.stations):
        with col:
            st.markdown(f"**{station.name}** ({station.charger_count} charger{'s' if station.charger_count > 1 else ''})")
            df = station_tables[station.id]
            if df.empty:
                st.write("_No buses charged here_")
            else:
                st.dataframe(df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Hard-rule validation (shown inline)
# ---------------------------------------------------------------------------

def _validate_hard_rules(result: ScheduleResult, scenario: Scenario) -> str:
    """Return a short validation summary string."""
    errors = []
    station_map = scenario.station_map()

    # Check range rule
    for bs in result.bus_schedules:
        nodes = scenario.route_nodes_for_bus(
            next(b for b in scenario.buses if b.id == bs.bus_id)
        )
        checkpoints = [nodes[0]] + [e.station_id for e in bs.charge_events] + [nodes[-1]]
        for i in range(len(checkpoints) - 1):
            dist = scenario.route.distance_between(checkpoints[i], checkpoints[i + 1])
            if dist > scenario.parameters.battery_range_km:
                errors.append(
                    f"{bs.bus_id}: range violation {checkpoints[i]}→{checkpoints[i+1]} ({dist} km)"
                )

    # Check station non-overlap
    station_timeline = result.station_timeline()
    for sid, entries in station_timeline.items():
        charger_count = station_map[sid].charger_count
        events_sorted = [(ev.charge_start_min, ev.charge_end_min) for ev, _, _ in entries]
        # Check with a sweep: at any point, at most charger_count buses are charging
        events_flat = []
        for start, end in events_sorted:
            events_flat.append((start, +1))
            events_flat.append((end, -1))
        events_flat.sort()
        concurrent = 0
        for _, delta in events_flat:
            concurrent += delta
            if concurrent > charger_count:
                errors.append(
                    f"Station {sid}: charger capacity exceeded (>{charger_count} concurrent)"
                )
                break

    if errors:
        return ":red[FAILED] — " + "; ".join(errors)
    return ":green[ALL HARD RULES SATISFIED] (range ≤ 240 km, no charger overlap)"


if __name__ == "__main__":
    main()
