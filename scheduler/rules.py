"""
Extensible rule system for the scheduler.

OOP pillars demonstrated here
------------------------------
Abstraction:   BaseRule is a formal ABC (Abstract Base Class). Calling
               contribute_objective on BaseRule directly raises TypeError at
               import time, not at runtime. The contract is machine-enforced.

Inheritance:   IndividualWaitRule, OperatorFairnessRule, OverallWaitRule each
               inherit BaseRule and specialise its behaviour.

Polymorphism:  The solver calls rule.contribute_objective(ctx) without knowing
               which concrete subclass it holds. Each returns a different
               LinearExpr term. Classic runtime polymorphism.

Encapsulation: RuleContext is a frozen dataclass — its fields are set at
               construction and cannot be reassigned afterwards.
               Rules interact with the CP-SAT model through ctx.model; they
               cannot replace ctx.model with a different object.
               Internal variables (depart, active, charge_start) are exposed
               as read-intended mappings; rules read them but do not reassign
               the references.

Adding a new rule: define a subclass, register it in REGISTERED_RULES.
No changes to the solver engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ortools.sat.python import cp_model as _cp_model
    from .models import Scenario


# ---------------------------------------------------------------------------
# Context object — frozen so rules cannot accidentally corrupt solver state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleContext:
    """
    Read-only snapshot of all solver variables passed to each rule.

    frozen=True enforces encapsulation:
      - ctx.model = x    →  raises FrozenInstanceError
      - ctx.model.Add(c) →  works fine (the model object is mutable)

    Variable naming mirrors solver.py so rules are self-documenting:
      depart[bus_id][node_index]       — when the bus leaves route node i
      active[bus_id][station_id]       — BoolVar: 1 if bus charges here
      charge_start[bus_id][station_id] — IntVar: when charging begins
      wait_vars[bus_id]                — list of per-station wait IntVars
      total_wait[bus_id]               — sum of wait_vars for that bus
    """
    model: "_cp_model.CpModel"
    scenario: "Scenario"

    depart: dict[str, dict[int, Any]]
    active: dict[str, dict[str, Any]]
    charge_start: dict[str, dict[str, Any]]

    wait_vars: dict[str, list[Any]]
    total_wait: dict[str, Any]

    # Solver passes a tight upper bound so rules never call .Proto()
    wait_ub: int = 11520
    scale: int = 100


# ---------------------------------------------------------------------------
# Abstract base class (Abstraction + enforced polymorphism contract)
# ---------------------------------------------------------------------------

class BaseRule(ABC):
    """
    Abstract base class for all scheduler rules.

    Subclasses MUST implement contribute_objective.
    Subclasses MAY override enforce_hard for hard constraints.

    Using ABC + @abstractmethod means:
      - Python raises TypeError if you try to instantiate BaseRule directly.
      - The contract is declared at the class level, not hidden inside a body.
    """

    # Each rule carries a stable name for logging and debugging.
    # Protected by convention (_name); subclasses set it via the class-level name attr.
    name: str = "base"

    def enforce_hard(self, ctx: RuleContext) -> None:
        """
        Add hard (mandatory) constraints to ctx.model.
        Default is a no-op — override only when a rule has hard requirements.
        Template-Method pattern: the solver calls this before the objective.
        """

    @abstractmethod
    def contribute_objective(self, ctx: RuleContext) -> Any:
        """
        Return a weighted LinearExpr that will be included in model.Minimize(),
        or None to skip this rule's contribution entirely.

        Must be implemented by every concrete rule class.
        The returned expression is expected to be non-negative.
        """


# ---------------------------------------------------------------------------
# Concrete rules (Inheritance + Polymorphism)
# ---------------------------------------------------------------------------

class IndividualWaitRule(BaseRule):
    """
    Minimise the maximum total wait experienced by any single bus.
    Weight key: scenario.weights.individual
    """

    name = "individual"

    def contribute_objective(self, ctx: RuleContext) -> Any:
        w = int(ctx.scenario.weights.get("individual") * ctx.scale)
        if w == 0:
            return None

        max_wait = ctx.model.NewIntVar(0, ctx.wait_ub, "obj_max_individual_wait")
        for tv in ctx.total_wait.values():
            ctx.model.Add(max_wait >= tv)

        return w * max_wait


class OperatorFairnessRule(BaseRule):
    """
    Minimise the worst per-operator fleet wait.

    For each operator, compute the maximum total-wait across its buses.
    Then minimise the maximum of those per-operator maxima.

    Weight key: scenario.weights.operator
    """

    name = "operator"

    def contribute_objective(self, ctx: RuleContext) -> Any:
        w = int(ctx.scenario.weights.get("operator") * ctx.scale)
        if w == 0:
            return None

        op_bus_map: dict[str, list[str]] = {}
        for bus in ctx.scenario.buses:
            op_bus_map.setdefault(bus.operator, []).append(bus.id)

        global_op_max = ctx.model.NewIntVar(0, ctx.wait_ub, "obj_max_operator_wait")

        for op_id, bus_ids in op_bus_map.items():
            op_max = ctx.model.NewIntVar(0, ctx.wait_ub, f"obj_op_max_{op_id}")
            for bid in bus_ids:
                ctx.model.Add(op_max >= ctx.total_wait[bid])
            ctx.model.Add(global_op_max >= op_max)

        return w * global_op_max


class OverallWaitRule(BaseRule):
    """
    Minimise the total sum of waits across all buses.
    Weight key: scenario.weights.overall
    """

    name = "overall"

    def contribute_objective(self, ctx: RuleContext) -> Any:
        w = int(ctx.scenario.weights.get("overall") * ctx.scale)
        if w == 0:
            return None

        all_wait_vars: list[Any] = [
            v for wait_list in ctx.wait_vars.values() for v in wait_list
        ]
        if not all_wait_vars:
            return None

        total_ub = ctx.wait_ub * len(all_wait_vars)
        total_all = ctx.model.NewIntVar(0, total_ub, "obj_total_all_wait")
        ctx.model.Add(total_all == sum(all_wait_vars))

        return w * total_all


# ---------------------------------------------------------------------------
# Registry — add new rules here; solver picks them up automatically
# ---------------------------------------------------------------------------

REGISTERED_RULES: list[type[BaseRule]] = [
    IndividualWaitRule,
    OperatorFairnessRule,
    OverallWaitRule,
]
