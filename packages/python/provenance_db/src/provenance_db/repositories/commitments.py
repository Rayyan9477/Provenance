"""Reads over the obligation ledger: commitments and their fulfillments.

Authority: ``specs/10_DATABASE_DDL.md`` section 12 (write-path ownership) and
``specs/13_RETRIEVAL_SPEC.md`` section 19 (module layout). Split by domain,
never by table: a repository spanning two aggregates hides a transaction
boundary.

Money is ``Decimal`` at every boundary. ``outstanding`` is derived as
``committed_amount - admitted_fulfillment_amount`` and is recomputed from
scratch, never incremented (``12_KERNEL_ALGORITHMS.md`` section 4).

Why this read does not compute the outstanding amount
------------------------------------------------------
``commitments.outstanding_amount`` is a stored column, and constraint ``M4``
(``ck_commitments_outstanding_identity``) makes the database refuse any row
where it is not ``committed_amount - fulfilled_amount``. Recomputing it here
from ``fulfillments`` would be a **second** implementation of the same
arithmetic, reachable by a different path, and the two would agree right up
until a partial payment landed in a currency the ledger rejected — at which
point the read would report a number the Kernel never wrote and no constraint
had ever checked. The column is read, not recalculated.

The wedge, and why the status set is what it is
------------------------------------------------
``CANONICAL_DECISIONS.md`` -> *Wedge*: unresolved obligations are the product.
``PROPOSED``, ``ACTIVE``, ``PARTIAL`` and ``DISPUTED`` are the four states in
which something is still owed — the same four ``agent_open_obligations_v1``
selects, so the agent-safe view and the repository cannot disagree about what
"open" means. ``FULFILLED``, ``EXPIRED`` and ``SUPERSEDED`` are settled.
``DISPUTED`` is deliberately open: a disputed obligation is the case the user
most needs to see, not the one to hide.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection

from provenance_contracts.identity import Principal
from provenance_db import retry
from provenance_db.repositories._execute import _fetch_all, _fetch_one, _owner, _scope

__all__ = [
    "CASE_COMMITMENTS_SQL",
    "CASE_FULFILLMENTS_SQL",
    "OPEN_COMMITMENTS_SQL",
    "OPEN_COMMITMENT_COUNT_SQL",
    "OPEN_STATUSES",
    "USER_COMMITMENTS_SQL",
    "count_open_commitments",
    "get_open_commitments",
    "list_commitments_for_case",
    "list_commitments_for_user",
    "list_fulfillments_for_case",
]

#: The four states in which something is still owed. Mirrors the ``WHERE`` of
#: ``agent_open_obligations_v1`` in migration ``0008``.
OPEN_STATUSES: tuple[str, ...] = ("PROPOSED", "ACTIVE", "PARTIAL", "DISPUTED")

#: ``DECIMAL(20,4)`` on the way out, because it is ``DECIMAL(20,4)`` in the
#: column. psycopg returns :class:`~decimal.Decimal` for a numeric and this
#: statement does nothing to it — no cast, no ``::FLOAT``, no arithmetic. A
#: repository that coerced here would reintroduce the representation error the
#: column type exists to prevent, and would do it *below* every test that
#: checks the arithmetic above.
#:
#: ``NULLS LAST`` on ``due_at``: a commitment with no deadline is not the most
#: urgent thing on the list, which is what ascending order would otherwise
#: claim.
OPEN_COMMITMENTS_SQL = """
    SELECT id, case_id, commitment_type, description,
           obligor_type, obligor_id, beneficiary_type, beneficiary_id,
           currency, committed_amount, fulfilled_amount, outstanding_amount,
           due_at, condition_ast, source_claim_id, status, revision,
           valid_from, valid_to, created_at
    FROM commitments
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND case_id = %(case_id)s
      AND status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
    ORDER BY due_at ASC NULLS LAST, created_at ASC
"""


async def get_open_commitments(
    conn: AsyncConnection[Any],
    principal: Principal,
    case_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """Commitments on one case that are neither fulfilled nor expired.

    Monetary rows carry ``committed_amount``, ``fulfilled_amount`` and
    ``outstanding_amount`` as :class:`~decimal.Decimal`, or all three as
    ``None`` — constraint ``M2`` forbids the mixed case, which is what stops
    ``M3`` and ``M4`` from being vacuously true whenever one column was left
    unset. Non-monetary commitments (``SERVICE_TERMINATION``, ``RESPONSE``,
    ``DOCUMENT_DELIVERY``) carry all three as ``None``, and a caller that
    treats ``None`` as zero has invented a settled obligation.
    """
    return await _fetch_all(
        conn,
        OPEN_COMMITMENTS_SQL,
        {**_scope(principal), "case_id": case_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


# ===========================================================================
# The API read models (T8.9)
# ===========================================================================

#: Section 8.15, sorted ``due_at ASC NULLS LAST, id ASC`` so the next
#: obligation is first. The keyset therefore runs **forwards** -- ``>`` rather
#: than the ``<`` every other collection uses -- because this is the one list
#: the spec orders ascending.
#:
#: ``overdue`` and ``days_overdue`` are **not** projected: section 8.15 is
#: explicit that they are computed at read time from ``due_at``,
#: ``outstanding_amount`` and the request clock, and that a stored flag would
#: go stale between writes. The adapter derives them from the three values
#: this statement does return, against the injected clock, so the demo's "95
#: days overdue" is one arithmetic in one place.
#:
#: No cast, no ``::FLOAT``, no arithmetic on the money columns. They are
#: ``DECIMAL(20,4)`` in the column and :class:`~decimal.Decimal` on the way
#: out, and a coercion here would reintroduce the representation error the
#: column type exists to prevent -- below every test that checks the
#: arithmetic above.
USER_COMMITMENTS_SQL = """
    SELECT cm.id AS commitment_id, cm.case_id, cm.commitment_type, cm.description,
           cm.obligor_type, cm.obligor_id, cm.beneficiary_type, cm.beneficiary_id,
           cm.currency, cm.committed_amount, cm.fulfilled_amount,
           cm.outstanding_amount, cm.due_at, cm.source_claim_id, cm.status,
           cm.revision, cm.valid_from, cm.valid_to, cm.created_at,
           k.relationship_id, k.title AS case_title,
           cp.display_name AS counterparty_display_name
    FROM commitments cm
    JOIN cases k
      ON k.tenant_id = cm.tenant_id AND k.user_id = cm.user_id AND k.id = cm.case_id
    JOIN relationships r
      ON r.tenant_id = k.tenant_id AND r.user_id = k.user_id AND r.id = k.relationship_id
    JOIN counterparties cp
      ON cp.tenant_id = k.tenant_id AND cp.id = r.counterparty_id
    WHERE cm.tenant_id = %(tenant_id)s
      AND cm.user_id = %(user_id)s
      AND (%(case_id)s::UUID IS NULL OR cm.case_id = %(case_id)s::UUID)
      AND (%(relationship_id)s::UUID IS NULL OR k.relationship_id = %(relationship_id)s::UUID)
      AND (%(context_id)s::UUID IS NULL OR k.context_id = %(context_id)s::UUID)
      AND (cardinality(%(statuses)s::STRING[]) = 0
           OR cm.status = ANY(%(statuses)s::STRING[]))
      AND (NOT %(overdue_only)s::BOOL
           OR (cm.due_at IS NOT NULL
               AND cm.due_at < %(now)s::TIMESTAMPTZ
               AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')))
      AND (NOT %(outstanding_only)s::BOOL
           OR cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED'))
      AND (%(after_due_at)s::TIMESTAMPTZ IS NULL
           OR (cm.due_at, cm.id) > (%(after_due_at)s::TIMESTAMPTZ, %(after_id)s::UUID))
    ORDER BY cm.due_at ASC NULLS LAST, cm.id ASC
    LIMIT %(limit)s
"""

#: Section 8.9's ``commitments`` block and section 8.11's, which is the same
#: set: every commitment on the case whatever its status. ``SUPERSEDED`` is
#: included because a superseded obligation is part of the case's history and
#: State Proof renders history.
CASE_COMMITMENTS_SQL = """
    SELECT cm.id AS commitment_id, cm.case_id, cm.commitment_type, cm.description,
           cm.obligor_type, cm.obligor_id, cm.beneficiary_type, cm.beneficiary_id,
           cm.currency, cm.committed_amount, cm.fulfilled_amount,
           cm.outstanding_amount, cm.due_at, cm.source_claim_id, cm.status,
           cm.revision, cm.valid_from, cm.valid_to, cm.created_at
    FROM commitments cm
    WHERE cm.tenant_id = %(tenant_id)s
      AND cm.user_id = %(user_id)s
      AND cm.case_id = %(case_id)s
    ORDER BY cm.due_at ASC NULLS LAST, cm.created_at ASC
"""

#: Section 8.11's ``commitments[].fulfillments`` block, joined through
#: ``commitments`` because ``fulfillments`` carries no ``case_id``.
CASE_FULFILLMENTS_SQL = """
    SELECT f.id AS fulfillment_id, f.commitment_id, f.currency, f.amount,
           f.quantity, f.fulfilled_at, f.admission_status, f.evidence_id
    FROM fulfillments f
    JOIN commitments cm
      ON cm.tenant_id = f.tenant_id AND cm.user_id = f.user_id AND cm.id = f.commitment_id
    WHERE f.tenant_id = %(tenant_id)s
      AND f.user_id = %(user_id)s
      AND cm.case_id = %(case_id)s
    ORDER BY f.fulfilled_at ASC, f.id ASC
"""

#: The dashboard's ``unresolved_commitments`` tile, counted at query time.
OPEN_COMMITMENT_COUNT_SQL = """
    SELECT count(*) AS unresolved_commitments
    FROM commitments cm
    WHERE cm.tenant_id = %(tenant_id)s
      AND cm.user_id = %(user_id)s
      AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
"""


async def list_commitments_for_user(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    now: Any = None,
    case_id: uuid.UUID | None = None,
    relationship_id: uuid.UUID | None = None,
    context_id: uuid.UUID | None = None,
    statuses: tuple[str, ...] = (),
    overdue_only: bool = False,
    outstanding_only: bool = False,
    after_due_at: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One page of obligations across every case, next one first."""
    return await _fetch_all(
        conn,
        USER_COMMITMENTS_SQL,
        {
            **_owner(tenant_id, user_id),
            "limit": limit,
            "now": now,
            "case_id": case_id,
            "relationship_id": relationship_id,
            "context_id": context_id,
            "statuses": list(statuses),
            "overdue_only": overdue_only,
            "outstanding_only": outstanding_only,
            "after_due_at": after_due_at,
            "after_id": after_id,
        },
    )


async def list_commitments_for_case(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Every commitment on one case, settled and unsettled alike."""
    return await _fetch_all(
        conn, CASE_COMMITMENTS_SQL, {**_owner(tenant_id, user_id), "case_id": case_id}
    )


async def list_fulfillments_for_case(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """The fulfillment ledger behind one case's commitments, oldest first."""
    return await _fetch_all(
        conn, CASE_FULFILLMENTS_SQL, {**_owner(tenant_id, user_id), "case_id": case_id}
    )


async def count_open_commitments(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    """How many obligations are still owed to or by this user."""
    row = await _fetch_one(conn, OPEN_COMMITMENT_COUNT_SQL, _owner(tenant_id, user_id))
    return int(row["unresolved_commitments"]) if row else 0
