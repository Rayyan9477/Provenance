"""Flatten canonical rows into the exact path->value map the evaluator reads — §7.3.

The output dict is keyed by whitelisted registry paths. It is both the input to
evaluation and, verbatim, the audit record of what was observed.

Where the arithmetic lives
--------------------------
``CANONICAL_DECISIONS.md`` -> *Memory, action, and time*: "No general arithmetic
nodes in the trigger DSL. Add reviewed deterministic derived fields to the
projection registry." :func:`_days_between` is the entirety of that arithmetic.
It happens once, here, in code with tests, rather than being re-derived —
differently — by every stored predicate that ever needed "days overdue".

One clock, and it is the database's
-----------------------------------
``now`` comes out of ``case_row["db_now"]`` and there is no other way to obtain
a time in this module. §11.5 is explicit about why: it is the *same* clock that
timestamped ``commitments.due_at`` and ``cases.updated_at``, so comparisons are
internally consistent. A worker's clock compared against a database-written
deadline is two unsynchronised clocks and can produce ``days_overdue = -1`` on a
row the database considers overdue.

One snapshot
------------
Both queries in §7.2 run inside one ``READ ONLY`` transaction so that
``cases.revision``, the conflict counts, the commitment rows and ``clock.now``
all come from a single serializable snapshot. Reading them in separate
autocommit statements would let the fire transaction's revision guard compare
against a revision that never coexisted with the values that were evaluated.
This module is handed the rows; :class:`ProjectionReader` is the shape of the
thing that must have read them together.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from services.control_plane.app.triggers.ast import PredicateSpec
from services.control_plane.app.triggers.config import SECONDS_PER_DAY
from services.control_plane.app.triggers.registry import COMMITMENT_FIELDS

__all__ = [
    "BindingUnresolved",
    "Projection",
    "ProjectionReader",
    "ProjectionSnapshot",
    "ProjectionUnavailable",
    "build_projection",
]


class ProjectionUnavailable(RuntimeError):  # noqa: N818 - it names a state, not an error kind
    """The snapshot could not be read at all.

    Distinct from :class:`BindingUnresolved`, which means the rows were read and
    one of them was not there. This means nothing was read: the case is not
    visible, or the reader could not obtain a snapshot. Both are ``ERROR`` and
    both leave the trigger ``ARMED`` -- silently forgetting an obligation
    because of an internal fault is the worst failure this product has -- but a
    reader that returned an empty projection instead would evaluate the
    predicate against zeroes and could disarm on the strength of them.
    """


class BindingUnresolved(RuntimeError):  # noqa: N818 - the name is 16_TRIGGER_DSL.md section 7.3's
    """A bound commitment is not on the case.

    §10.4: this is an ``ERROR`` and not a no-op, because it indicates a Kernel
    bug or a hand-edited row — a state the system should never reach silently.
    The trigger is left ``ARMED`` for operator inspection and is deliberately
    **not** auto-disarmed: silently forgetting an obligation because of an
    internal error is the worst failure mode this product has.
    """

    def __init__(self, binding: str, commitment_id: UUID) -> None:
        super().__init__(f"binding {binding!r} -> commitment {commitment_id} not found on case")
        self.binding = binding
        self.commitment_id = commitment_id


@dataclass(frozen=True, slots=True)
class Projection:
    """One coherent point-in-time observation of a case and its bound obligations."""

    case_id: UUID
    tenant_id: UUID
    user_id: UUID
    case_status: str
    case_revision: int
    db_now: datetime
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    """The rows §7.2's two queries return, carried together.

    Together, because the guarantee is that they were read together. A reader
    returning three unrelated values could satisfy the type while issuing three
    autocommit statements, and the revision guard downstream would then be
    comparing against a revision that never coexisted with the amounts.
    """

    case_row: Mapping[str, Any]
    commitment_rows: Mapping[UUID, Mapping[str, Any]]


class ProjectionReader(Protocol):
    """Reads one case and its bound commitments in a single read-only snapshot.

    Declared as a Protocol here, and satisfied by the repository layer, so this
    package can be exercised in full against a fake without a database and
    without reaching into a module another lane owns.
    """

    async def read(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        case_id: UUID,
        commitment_ids: tuple[UUID, ...],
    ) -> ProjectionSnapshot: ...


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _days_between(later: datetime, earlier: datetime) -> int:
    """Whole days from *earlier* to *later*, floored, and never clamped.

    Floored rather than rounded: 23 hours past a deadline is 0 days overdue, and
    rounding it to 1 would put a number in a letter the user signs that the
    calendar does not support. Negative results are preserved because
    ``days_overdue`` is meaningful before the deadline too, and clamping would
    make "0" mean both "due today" and "due next year".
    """
    return math.floor((_as_utc(later) - _as_utc(earlier)).total_seconds() / SECONDS_PER_DAY)


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def build_projection(
    *,
    case_row: Mapping[str, Any],
    commitment_rows: Mapping[UUID, Mapping[str, Any]],
    trigger_row: Mapping[str, Any],
    spec: PredicateSpec,
) -> Projection:
    """Materialise every whitelisted path the *spec*'s bindings make legal.

    Every path, not only the referenced ones: §8 documents the evaluator's
    ``values[node.path]`` as infallible, and that is only true if this function
    populates the whole legal surface rather than guessing which subset the
    predicate happens to read today.

    Raises:
        BindingUnresolved: a declared binding names a commitment that is not on
            the case. See §10.4 for why that is an error and not a quiet skip.
    """
    now = _as_utc(case_row["db_now"])

    values: dict[str, Any] = {
        "clock.now": now,
        "case.status": case_row["case_status"],
        "case.revision": int(case_row["case_revision"]),
        "case.attention_level": case_row["attention_level"],
        "case.reopened_count": int(case_row["reopened_count"]),
        "case.opened_at": _as_utc(case_row["opened_at"]),
        "case.resolved_at": (
            None if case_row["resolved_at"] is None else _as_utc(case_row["resolved_at"])
        ),
        "case.last_activity_at": _as_utc(case_row["last_activity_at"]),
        "case.days_since_last_activity": _days_between(now, case_row["last_activity_at"]),
        "case.open_conflict_count": int(case_row["open_conflict_count"]),
        "case.needs_human_conflict_count": int(case_row["needs_human_conflict_count"]),
        "case.active_commitment_count": int(case_row["active_commitment_count"]),
        "case.total_outstanding_amount": _decimal(case_row["total_outstanding_amount"]),
        "case.outstanding_currency": case_row["outstanding_currency"],
        "trigger.not_before": (
            None if trigger_row["not_before"] is None else _as_utc(trigger_row["not_before"])
        ),
        "trigger.expires_at": (
            None if trigger_row["expires_at"] is None else _as_utc(trigger_row["expires_at"])
        ),
        "trigger.evaluation_version": int(trigger_row["evaluation_version"]),
        "trigger.basis_case_revision": int(trigger_row["basis_case_revision"]),
    }

    for binding in spec.bindings:
        row = commitment_rows.get(binding.commitment_id)
        if row is None:
            raise BindingUnresolved(binding.name, binding.commitment_id)
        prefix = f"commitments.{binding.name}"
        for leaf in COMMITMENT_FIELDS:
            if leaf == "days_overdue":
                due_at = row["due_at"]
                values[f"{prefix}.days_overdue"] = (
                    None if due_at is None else _days_between(now, due_at)
                )
            elif leaf == "has_admitted_fulfillment":
                values[f"{prefix}.{leaf}"] = bool(row["has_admitted_fulfillment"])
            elif leaf in {"committed_amount", "fulfilled_amount", "outstanding_amount"}:
                raw = row[leaf]
                values[f"{prefix}.{leaf}"] = None if raw is None else _decimal(raw)
            elif leaf in {"due_at", "valid_from", "valid_to"}:
                raw = row[leaf]
                values[f"{prefix}.{leaf}"] = None if raw is None else _as_utc(raw)
            else:
                values[f"{prefix}.{leaf}"] = row[leaf]

    return Projection(
        case_id=case_row["case_id"],
        tenant_id=case_row["tenant_id"],
        user_id=case_row["user_id"],
        case_status=str(case_row["case_status"]),
        case_revision=int(case_row["case_revision"]),
        db_now=now,
        values=values,
    )
