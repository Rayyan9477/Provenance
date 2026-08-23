"""The closed whitelist of paths a trigger predicate may read — §5 and §7.1.

Adding a path here is a deliberate act with a security review attached: it
widens what a predicate proposed by a language model, from untrusted text, can
observe months after the text arrived.

What is deliberately absent, and why it matters
-----------------------------------------------
No path reaches ``users``, ``tenants``, ``ingest_aliases``,
``source_artifacts``, ``evidence_items``, ``claims``, ``beliefs``,
``belief_versions``, ``belief_support``, ``action_intents``,
``action_executions``, ``memory_proposals``, ``kernel_decisions`` or
``outbox_events``. Prospective memory asks *"is this obligation still open and
overdue?"* — a question about the canonical state plane and the obligation
plane. It never asks a question about the evidence or epistemic planes, because
those are exactly where an attacker who forwarded a hostile PDF has influence.
There is also no path to raw text of any kind: a predicate cannot match on a
subject line, a sender or a body. It compares scalars.

Where the arithmetic went
-------------------------
``days_overdue`` and ``outstanding_amount`` are named fields in this registry
rather than expressions in the AST, which is ``CANONICAL_DECISIONS.md`` ->
"Trigger arithmetic" made structural. The subtraction happens once, in
``projection.py``, in code that has tests; the predicate only compares. A
reviewer reading a stored term can tell what it means without also auditing how
every trigger that ever referenced "outstanding" chose to compute it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from services.control_plane.app.triggers.ast import TriggerSpecError, ValueType

__all__ = [
    "COMMITMENT_FIELDS",
    "STATIC_FIELDS",
    "FieldSource",
    "FieldSpec",
    "all_paths_for",
    "resolve_field",
]

_D, _I, _S, _B, _T = (
    ValueType.DECIMAL,
    ValueType.INT,
    ValueType.STRING,
    ValueType.BOOL,
    ValueType.TIMESTAMP,
)

#: Which of the four projection sources a path is read from. A string rather
#: than an enum because it is a label in an audit record, never a branch.
FieldSource = str


@dataclass(frozen=True, slots=True)
class FieldSpec:
    path: str
    value_type: ValueType
    nullable: bool
    source: FieldSource  # CLOCK | CASE | COMMITMENT | TRIGGER


def _spec(path: str, value_type: ValueType, nullable: bool, source: str) -> FieldSpec:
    return FieldSpec(path=path, value_type=value_type, nullable=nullable, source=source)


#: §5.1, §5.2 and §5.4. ``clock.now`` is the CockroachDB transaction timestamp
#: of the projection read and **never** a worker's wall clock (§11.5): it is the
#: same clock that timestamped ``commitments.due_at``, so the comparison is
#: internally consistent rather than a race between two unsynchronised clocks.
STATIC_FIELDS: Final[dict[str, FieldSpec]] = {
    field.path: field
    for field in (
        _spec("clock.now", _T, False, "CLOCK"),
        _spec("case.status", _S, False, "CASE"),
        _spec("case.revision", _I, False, "CASE"),
        _spec("case.attention_level", _S, False, "CASE"),
        _spec("case.reopened_count", _I, False, "CASE"),
        _spec("case.opened_at", _T, False, "CASE"),
        _spec("case.resolved_at", _T, True, "CASE"),
        _spec("case.last_activity_at", _T, False, "CASE"),
        _spec("case.days_since_last_activity", _I, False, "CASE"),
        _spec("case.open_conflict_count", _I, False, "CASE"),
        _spec("case.needs_human_conflict_count", _I, False, "CASE"),
        _spec("case.active_commitment_count", _I, False, "CASE"),
        _spec("case.total_outstanding_amount", _D, False, "CASE"),
        # NULL when zero or more than one distinct currency is active, so a
        # mixed-currency case yields UNKNOWN rather than a wrong sum.
        _spec("case.outstanding_currency", _S, True, "CASE"),
        _spec("trigger.not_before", _T, True, "TRIGGER"),
        _spec("trigger.expires_at", _T, True, "TRIGGER"),
        _spec("trigger.evaluation_version", _I, False, "TRIGGER"),
        _spec("trigger.basis_case_revision", _I, False, "TRIGGER"),
    )
}

#: §5.3 — ``commitments.<binding>.<field>``. ``days_overdue`` is the reviewed
#: derived field that replaces an arithmetic node; ``has_admitted_fulfillment``
#: is the reviewed existence check that replaces a subquery.
COMMITMENT_FIELDS: Final[dict[str, tuple[ValueType, bool]]] = {
    "status": (_S, False),
    "commitment_type": (_S, False),
    "revision": (_I, False),
    "currency": (_S, True),
    "committed_amount": (_D, True),
    "fulfilled_amount": (_D, True),
    "outstanding_amount": (_D, True),
    "due_at": (_T, True),
    "valid_from": (_T, True),
    "valid_to": (_T, True),
    "days_overdue": (_I, True),
    "has_admitted_fulfillment": (_B, False),
}


def resolve_field(path: str, bindings: Mapping[str, UUID]) -> FieldSpec:
    """Resolve a ``FIELD`` path against the whitelist. Raises on anything unknown.

    Raises:
        TriggerSpecError: ``UNBOUND_COMMITMENT`` when a ``commitments.<name>``
            path names a binding the spec never declared, ``UNKNOWN_FIELD``
            for everything else outside the registry.
    """
    spec = STATIC_FIELDS.get(path)
    if spec is not None:
        return spec

    parts = path.split(".")
    if len(parts) == 3 and parts[0] == "commitments":
        _, binding, leaf = parts
        if binding not in bindings:
            raise TriggerSpecError(
                "UNBOUND_COMMITMENT",
                f"binding {binding!r} is not declared in spec.bindings",
                path,
            )
        entry = COMMITMENT_FIELDS.get(leaf)
        if entry is None:
            raise TriggerSpecError(
                "UNKNOWN_FIELD",
                f"{leaf!r} is not a readable commitment field; "
                f"allowed: {sorted(COMMITMENT_FIELDS)}",
                path,
            )
        value_type, nullable = entry
        return FieldSpec(path=path, value_type=value_type, nullable=nullable, source="COMMITMENT")

    raise TriggerSpecError("UNKNOWN_FIELD", f"{path!r} is not a whitelisted field path", path)


def all_paths_for(bindings: Mapping[str, UUID]) -> list[str]:
    """Every legal path given these bindings — for docs, tests and the builder."""
    paths = list(STATIC_FIELDS)
    for name in bindings:
        paths.extend(f"commitments.{name}.{leaf}" for leaf in COMMITMENT_FIELDS)
    return sorted(paths)
