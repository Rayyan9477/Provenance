"""Deterministic three-valued evaluator for trigger predicates — §8.

Properties this module guarantees, and that its unit tests assert:

* **pure** — no I/O, no clock, no randomness, no network, no database;
* **total** — every stored spec evaluates to ``TRUE``, ``FALSE`` or ``UNKNOWN``;
* **eager** — no short-circuit, so the trace records every subexpression;
* **reproducible** — the same ``(spec, values)`` always yields the same result
  and the same trace.

``EVALUATOR_CODE_VERSION`` must be bumped whenever any of those semantics
change, so an old evaluation's replay is never silently reinterpreted by new
code.

Why ``UNKNOWN`` exists
----------------------
The naive rule — "a comparison against NULL is false" — is unsafe here, because
``NOT(EQ(x, 'FULFILLED'))`` would then be **true** when ``x`` is unknown, and
the trigger would fire on missing data: a demand for money that may already
have been paid, sent because a column was empty. So a comparison with a NULL
operand is ``UNKNOWN``, and a trigger fires **only** when the root is exactly
``TRUE``. ``UNKNOWN`` is not a failure — it is memory correctly declining to
assert something it does not know.

Why there is no wall clock in this file
---------------------------------------
``clock.now`` arrives in the value map, taken from ``now()`` in the same
read-only transaction as the rest of the projection (§11.5). Reading a local
clock here would compare a worker's time against a database-written deadline —
two unsynchronised clocks — and can produce ``days_overdue = -1`` on a row the
database considers overdue. ``test_the_evaluator_module_reads_no_wall_clock``
scans this source so a future edit fails there rather than in production.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Final

from provenance_domain import money
from services.control_plane.app.triggers.ast import (
    NUMERIC_TYPES,
    BoolNode,
    CompareNode,
    ConstNode,
    FieldNode,
    Node,
    NotNode,
    NullCheckNode,
    PredicateSpec,
    ValueType,
)
from services.control_plane.app.triggers.config import EVALUATOR_CODE_VERSION

__all__ = [
    "EVALUATOR_CODE_VERSION",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "SABOTAGED_SYMBOLS",
    "Evaluation",
    "NodeTrace",
    "Tri",
    "evaluate_predicate",
    "reevaluate_predicate",
    "render_value",
    "tri_and",
    "tri_not",
    "tri_or",
]


class Tri(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


def tri_not(value: Tri) -> Tri:
    if value is Tri.TRUE:
        return Tri.FALSE
    if value is Tri.FALSE:
        return Tri.TRUE
    return Tri.UNKNOWN


def tri_and(values: list[Tri]) -> Tri:
    # FALSE is checked before UNKNOWN: `FALSE AND UNKNOWN` is FALSE. Reversing
    # the two lines is the subtle form of the "null compares false" bug.
    if any(value is Tri.FALSE for value in values):
        return Tri.FALSE
    if any(value is Tri.UNKNOWN for value in values):
        return Tri.UNKNOWN
    return Tri.TRUE


def tri_or(values: list[Tri]) -> Tri:
    if any(value is Tri.TRUE for value in values):
        return Tri.TRUE
    if any(value is Tri.UNKNOWN for value in values):
        return Tri.UNKNOWN
    return Tri.FALSE


@dataclass(frozen=True, slots=True)
class NodeTrace:
    """One node's verdict, keyed by the parser's deterministic pre-order index."""

    nid: int
    op: str
    result: str
    detail: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    """What the evaluator saw and what it concluded.

    ``observed`` is the single most important artifact this subsystem produces
    for a judge: the durable answer to *"what did Provenance actually see at the
    moment it decided to act?"*. It is written verbatim into the proposal
    payload, and it carries exactly the paths the predicate read — no more,
    because a record that included amounts the predicate never consulted would
    misrepresent what the decision rested on.
    """

    result: Tri
    evaluator_code_version: str
    predicate_sha256: str
    observed: Mapping[str, str]
    node_trace: tuple[NodeTrace, ...]


def render_value(value: Any) -> str:
    """The one rendering used by ``observed``, the node trace and the payload.

    One function rather than three so a judge comparing the trace against the
    observed map is comparing the same string, not two formattings of one value.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _operand(node: Node, values: Mapping[str, Any]) -> tuple[Any, ValueType]:
    if isinstance(node, ConstNode):
        return node.value, node.value_type
    if isinstance(node, FieldNode):
        # A KeyError here is deliberate and must not become UNKNOWN. UNKNOWN
        # means "the database does not know"; a missing key means "the
        # projection forgot to load it", and recording the second as the first
        # would file a build defect as an honest absence.
        return values[node.path], node.value_type
    raise TypeError(f"{node.op} is not an operand")  # pragma: no cover - parser prevents it


def _normalise(value: Any, vtype: ValueType, other: ValueType) -> Any:
    if value is None:
        return None
    if vtype in NUMERIC_TYPES and other in NUMERIC_TYPES:
        # Decimal, never float. `money.QUANTUM` is the same 4-place quantum the
        # obligation ledger stores, so a comparison here means what the ledger
        # means.
        return value if isinstance(value, Decimal) else Decimal(value)
    if vtype is ValueType.TIMESTAMP and isinstance(value, datetime):
        return value.astimezone(UTC)
    return value


def _compare(op: str, left: Any, right: Any) -> Tri:
    if left is None or right is None:
        return Tri.UNKNOWN  # the safety default: never assert on absence
    if op == "EQ":
        outcome = left == right
    elif op == "NE":
        outcome = left != right
    elif op == "GT":
        outcome = left > right
    elif op == "GTE":
        outcome = left >= right
    elif op == "LT":
        outcome = left < right
    else:  # LTE
        outcome = left <= right
    return Tri.TRUE if outcome else Tri.FALSE


def _eval(node: Node, values: Mapping[str, Any], trace: list[NodeTrace]) -> Tri:
    if isinstance(node, BoolNode):
        # Eager: every child is evaluated so the trace is complete. Operand
        # reads are dictionary lookups against a projection already in memory,
        # so there is nothing to save by short-circuiting and a complete audit
        # record to lose.
        children = [_eval(arg, values, trace) for arg in node.args]
        result = tri_and(children) if node.op == "AND" else tri_or(children)
        trace.append(
            NodeTrace(node.nid, node.op, result.value, " ".join(c.value for c in children))
        )
        return result

    if isinstance(node, NotNode):
        result = tri_not(_eval(node.arg, values, trace))
        trace.append(NodeTrace(node.nid, "NOT", result.value, ""))
        return result

    if isinstance(node, NullCheckNode):
        value, _ = _operand(node.arg, values)
        is_null = value is None
        result = Tri.TRUE if (is_null == (node.op == "IS_NULL")) else Tri.FALSE
        trace.append(NodeTrace(node.nid, node.op, result.value, render_value(value)))
        return result

    if isinstance(node, CompareNode):
        left, left_type = _operand(node.left, values)
        right, right_type = _operand(node.right, values)
        result = _compare(
            node.op,
            _normalise(left, left_type, right_type),
            _normalise(right, right_type, left_type),
        )
        trace.append(
            NodeTrace(
                node.nid,
                node.op,
                result.value,
                f"{render_value(left)} {node.op} {render_value(right)}",
            )
        )
        return result

    raise TypeError(f"unevaluable node {node.op}")  # pragma: no cover - parser prevents it


def evaluate_predicate(spec: PredicateSpec, values: Mapping[str, Any]) -> Evaluation:
    """Evaluate *spec* against *values*. Pure, total, eager, reproducible."""
    trace: list[NodeTrace] = []
    result = _eval(spec.root, values, trace)
    return Evaluation(
        result=result,
        evaluator_code_version=EVALUATOR_CODE_VERSION,
        predicate_sha256=spec.sha256,
        observed={path: render_value(values[path]) for path in spec.referenced_paths},
        node_trace=tuple(sorted(trace, key=lambda step: step.nid)),
    )


def reevaluate_predicate(spec: PredicateSpec, values: Mapping[str, Any]) -> Evaluation:
    """The wake-time entry point, and the whole point of the subsystem.

    A scheduler event is never truth. When a wake arrives — from EventBridge, a
    sweeper, or the judge's manual button — the predicate is evaluated **again**
    against a projection rebuilt from current canonical state, and *this* is the
    function that does it. It is deliberately a named symbol rather than a
    second call to :func:`evaluate_predicate`, because
    ``tests/sabotage_matrix.yaml`` needs something to neuter: with
    ``PV_SABOTAGE=triggers.evaluator.reevaluate_predicate`` the re-check becomes
    the identity function, a trigger whose case has since resolved no longer
    sees the resolution, and the D8 assertion goes red.

    A green sabotage run here means the re-evaluation is not actually reached,
    which is the bug the gate exists to find.
    """
    return evaluate_predicate(spec, values)


# --- the PV_SABOTAGE hook ----------------------------------------------------
#
# `70_TASK_PLAN.md` T10.4 and `23_PHASE_GATES.md` G10.7 address this symbol as
# `triggers.evaluator.reevaluate_predicate`, not by its dotted import path, so
# the module label is explicit rather than `__name__`. The mechanism lives in
# `provenance_domain.money` and is reused rather than re-implemented.
#
# Callers must reach the symbol THROUGH THIS MODULE (`evaluator.reevaluate_
# predicate(...)`), never through a `from`-import: a `from`-import copies the
# reference before the rebind is visible and the sabotage silently never
# arrives. `test_sabotage.py` asserts that wiring against the AST of
# `service.py`, which is the reason the matrix entry can be trusted.

#: The label ``tests/sabotage_matrix.yaml`` and ``G10.7`` use for this module.
SABOTAGE_MODULE: Final[str] = "triggers.evaluator"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("reevaluate_predicate",)

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
