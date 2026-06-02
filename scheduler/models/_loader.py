"""
YAML scenario loader.

Responsible for reading scenario files from disk, normalising YAML quirks
(the 'from' key that clashes with Python's reserved word), and delegating
to Pydantic for full validation.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ._input import Scenario


def _normalise_segments(raw: dict) -> dict:
    """
    Translate YAML 'from' keys to 'from_' before Pydantic validation.

    'from' is a reserved word in Python, so Pydantic uses the alias 'from_'.
    This pre-processing step keeps YAML files natural while keeping the model
    code clean.  Returns a shallow copy — the original dict is not mutated.
    """
    if "route" not in raw or "segments" not in raw.get("route", {}):
        return raw
    raw = dict(raw)
    raw["route"] = dict(raw["route"])
    raw["route"]["segments"] = [
        {("from_" if k == "from" else k): v for k, v in seg.items()}
        for seg in raw["route"]["segments"]
    ]
    return raw


def load_scenario(path: Path | str) -> Scenario:
    """Load and fully validate a single scenario YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Scenario.model_validate(_normalise_segments(raw))


def load_all_scenarios(directory: Path | str) -> list[Scenario]:
    """Load all .yaml scenario files from *directory*, sorted by filename."""
    directory = Path(directory)
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise FileNotFoundError(f"No YAML scenario files found in {directory}")
    return [load_scenario(f) for f in files]
