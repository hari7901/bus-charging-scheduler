# Bus Charging Scheduler

A Streamlit app that schedules electric bus charging along the Bengaluru–Kochi corridor using constraint-based optimization.

## Live App

Hosted on Streamlit Community Cloud: *(URL after deployment)*

## Local Setup

```bash
git clone <repo-url>
cd assignment1
pip install -r requirements.txt
streamlit run app.py
```

The app opens at `http://localhost:8501`. Select any of the 5 scenarios from the dropdown.

## Running Tests

```bash
pytest tests/ -v
```

All 57 tests should pass (takes ~3 minutes due to solver calls for high-contention scenarios).

## How to Change a Weight

Weights live in the scenario YAML file under the `weights` key. Open any scenario file in `data/scenarios/` and edit:

```yaml
weights:
  individual: 1.0   # penalise maximum single-bus wait
  operator:   2.0   # penalise worst operator fleet wait (double weight example)
  overall:    1.0   # penalise total delay across all buses
```

Changing `operator` from `1.0` to `2.0` makes the scheduler prioritise fleet-level fairness more heavily. No code changes needed.

## How to Add a New Scenario

1. Create a new YAML file in `data/scenarios/`, following the schema of any existing scenario.
2. Define `metadata`, `parameters`, `route`, `stations`, `operators`, `weights`, and `buses`.
3. The app discovers all `.yaml` files automatically — no code change needed.

## How to Add a New Rule

Open `scheduler/rules.py`. Define a new class inheriting from `BaseRule`:

```python
class TimeOfDayCostRule(BaseRule):
    """Penalise charging during peak-cost hours (07:00–22:00)."""

    name = "time_of_day_cost"

    def contribute_objective(self, ctx: RuleContext):
        w = int(ctx.scenario.weights.get("time_of_day_cost", 0) * ctx.scale)
        if w == 0:
            return None
        # Add an IntVar for each (bus, station) charge event that falls in the
        # peak window, and include it in the objective.
        # ... implementation ...
        return w * peak_cost_var
```

Then add it to the registry:

```python
REGISTERED_RULES = [
    IndividualWaitRule,
    OperatorFairnessRule,
    OverallWaitRule,
    TimeOfDayCostRule,   # <-- add here
]
```

The solver picks up all registered rules automatically. No changes to `solver.py`.

## Project Structure

```
assignment1/
├── app.py                       # Streamlit UI
├── requirements.txt
├── README.md
├── ARCHITECTURE.md
├── data/
│   └── scenarios/
│       ├── scenario_01_even_spacing.yaml
│       ├── scenario_02_bunched_start.yaml
│       ├── scenario_03_asymmetric_load.yaml
│       ├── scenario_04_operator_heavy.yaml
│       └── scenario_05_worst_case.yaml
├── scheduler/
│   ├── __init__.py
│   ├── constants/     # Solver constants such as WEIGHT_SCALE
│   ├── models/        # Pydantic input/output models and YAML loaders
│   ├── types/         # Shared solver type aliases
│   ├── utils/         # Small dependency-free utilities (time formatting)
│   ├── plans.py       # Explicit plan enumeration utilities for tests/analysis
│   ├── rules.py       # Extensible rule/objective plugin system
│   └── solver.py      # CP-SAT model builder with scalable range constraints
└── tests/
    ├── test_models.py
    ├── test_plans.py
    └── test_solver.py
```

## Deploying to Streamlit Community Cloud

1. Push this repository to GitHub (public).
2. Go to [share.streamlit.io](https://share.streamlit.io).
3. Click "New app", select the repo, set `app.py` as the entry point.
4. Click "Deploy". Streamlit reads `requirements.txt` automatically.
