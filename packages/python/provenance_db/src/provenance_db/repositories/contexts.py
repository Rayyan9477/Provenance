"""Reads over life contexts — "THE MOVE" and its siblings.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.5 (``GET /v1/contexts``) and 8.4, whose
  ``contexts[]`` element carries the same counts plus the outstanding totals.
- ``specs/10_DATABASE_DDL.md`` section 12 — ``contexts`` is canonical and
  Kernel-written; this module reads.

Why the counts are subqueries and not a join
---------------------------------------------
A context has many cases and a case has many commitments. One ``LEFT JOIN``
chain would multiply every context row by the product of the two, and
``count(DISTINCT ...)`` over that product is both slower and easy to get
subtly wrong the first time a third child table is added. Correlated
subqueries keep one row per context and make each number independently
readable.

``total_outstanding`` is an **array**, one entry per currency
-------------------------------------------------------------
Section 8.4: "the Kernel refuses arithmetic across currencies without an
explicit conversion event, so the API refuses to sum them either." Summing
USD and EUR into one figure is the single most plausible mistake on this
screen and it produces a number that looks entirely reasonable. The group-by
is therefore in the SQL, and the caller receives rows it cannot accidentally
add together.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from provenance_db.repositories._execute import _fetch_all, _owner

__all__ = [
    "CONTEXTS_SQL",
    "CONTEXT_OUTSTANDING_SQL",
    "list_context_outstanding",
    "list_contexts",
]

#: Section 8.5, sorted ``created_at DESC, id DESC`` — the keyset section 5.5
#: assigns this collection. ``LIMIT %(limit)s`` is bound by the caller as
#: ``page.limit + 1``; the extra row is what ``has_more`` is read from.
CONTEXTS_SQL = """
    SELECT c.id AS context_id, c.title, c.context_type, c.status,
           c.started_at, c.ended_at, c.created_at,
           (SELECT count(*) FROM cases k
             WHERE k.tenant_id = c.tenant_id
               AND k.user_id = c.user_id
               AND k.context_id = c.id) AS case_count,
           (SELECT count(*) FROM cases k
             WHERE k.tenant_id = c.tenant_id
               AND k.user_id = c.user_id
               AND k.context_id = c.id
               AND k.status NOT IN ('RESOLVED', 'SUPERSEDED')) AS open_case_count,
           (SELECT count(DISTINCT k.relationship_id) FROM cases k
             WHERE k.tenant_id = c.tenant_id
               AND k.user_id = c.user_id
               AND k.context_id = c.id) AS relationship_count
    FROM contexts c
    WHERE c.tenant_id = %(tenant_id)s
      AND c.user_id = %(user_id)s
      AND (%(after_created_at)s::TIMESTAMPTZ IS NULL
           OR (c.created_at, c.id) < (%(after_created_at)s::TIMESTAMPTZ, %(after_id)s::UUID))
    ORDER BY c.created_at DESC, c.id DESC
    LIMIT %(limit)s
"""

#: One row per ``(context_id, currency)``. ``PROPOSED``, ``ACTIVE``,
#: ``PARTIAL`` and ``DISPUTED`` are the four states in which something is still
#: owed — the same four ``commitments.OPEN_STATUSES`` names and
#: ``agent_open_obligations_v1`` selects, so three surfaces cannot disagree
#: about what "outstanding" means.
CONTEXT_OUTSTANDING_SQL = """
    SELECT k.context_id, cm.currency, sum(cm.outstanding_amount) AS outstanding
    FROM commitments cm
    JOIN cases k
      ON k.tenant_id = cm.tenant_id
     AND k.user_id = cm.user_id
     AND k.id = cm.case_id
    WHERE cm.tenant_id = %(tenant_id)s
      AND cm.user_id = %(user_id)s
      AND k.context_id IS NOT NULL
      AND cm.currency IS NOT NULL
      AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
    GROUP BY k.context_id, cm.currency
    ORDER BY k.context_id, cm.currency
"""


async def list_contexts(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    after_created_at: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One page of contexts, newest first, with their case counts."""
    return await _fetch_all(
        conn,
        CONTEXTS_SQL,
        {
            **_owner(tenant_id, user_id),
            "limit": limit,
            "after_created_at": after_created_at,
            "after_id": after_id,
        },
    )


async def list_context_outstanding(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Outstanding obligations per context and currency, never summed across."""
    return await _fetch_all(conn, CONTEXT_OUTSTANDING_SQL, _owner(tenant_id, user_id))
