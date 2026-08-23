"""Reads over relationships and the counterparties behind them.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.6, 8.7 and the ``relationships_summary``
  block of 8.4.
- ``specs/10_DATABASE_DDL.md`` section 12 — both tables are canonical and
  Kernel-written; this module reads.

``counterparties`` is tenant-scoped, ``relationships`` is user-scoped
---------------------------------------------------------------------
``counterparties`` has a ``tenant_id`` and **no** ``user_id`` — two users in
one tenant can hold a relationship with the same Northline Fiber. Every join
below therefore binds ``tenant_id`` on the counterparty side and both ids on
the relationship side. Writing ``cp.user_id`` would not fail loudly; it would
fail at ``CREATE TABLE`` time, which is the good case, and writing the join
without the tenant predicate would not fail at all — it would silently widen
to the whole cluster. The tenant predicate is what survives a bad join.

``external_account_ref`` is masked in the SQL, not in the renderer
-------------------------------------------------------------------
``NF-4471-8802`` is an account identifier a support agent will read out loud.
Masking it at the projection means no downstream surface can render the full
value by forgetting to call a helper, and the two Northline relationships —
canon's "sharpest decoy in the corpus" — stay distinguishable by their last
four characters.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from provenance_db.repositories._execute import _fetch_all, _fetch_one, _owner

__all__ = [
    "RELATIONSHIPS_SQL",
    "RELATIONSHIP_CASES_SQL",
    "RELATIONSHIP_OUTSTANDING_SQL",
    "RELATIONSHIP_SQL",
    "get_relationship",
    "list_relationship_cases",
    "list_relationship_outstanding",
    "list_relationships",
]

#: The projection shared by 8.4's ``relationships_summary`` and 8.6's list.
#:
#: ``attention_level`` is the **maximum** over the relationship's live cases
#: rather than a stored column: relationships have no attention column, and
#: deriving it as "the loudest thing still open under this counterparty" is
#: what the dashboard row means. The rank is spelled out as a ``CASE`` rather
#: than relying on the strings, because ``URGENT`` < ``NONE`` alphabetically
#: and a lexical ``max()`` would rank a quiet relationship above a burning one.
RELATIONSHIPS_SQL = """
    SELECT r.id AS relationship_id, r.relationship_type, r.label, r.status,
           r.valid_from, r.valid_to, r.revision, r.updated_at,
           r.normalized_identifiers,
           CASE WHEN r.external_account_ref IS NULL THEN NULL
                ELSE '••••' || right(r.external_account_ref, 4)
           END AS external_account_ref_masked,
           cp.id AS counterparty_id, cp.display_name AS counterparty_display_name,
           cp.kind AS counterparty_kind, cp.canonical_domain,
           (SELECT count(*) FROM cases k
             WHERE k.tenant_id = r.tenant_id AND k.user_id = r.user_id
               AND k.relationship_id = r.id
               AND k.status NOT IN ('RESOLVED', 'SUPERSEDED')) AS open_case_count,
           (SELECT count(*) FROM cases k
             WHERE k.tenant_id = r.tenant_id AND k.user_id = r.user_id
               AND k.relationship_id = r.id) AS total_case_count,
           (SELECT max(k.last_activity_at) FROM cases k
             WHERE k.tenant_id = r.tenant_id AND k.user_id = r.user_id
               AND k.relationship_id = r.id) AS last_activity_at,
           (SELECT coalesce(max(CASE k.attention_level WHEN 'URGENT' THEN 3
                                                       WHEN 'ATTENTION' THEN 2
                                                       WHEN 'INFO' THEN 1 ELSE 0 END), 0)
              FROM cases k
             WHERE k.tenant_id = r.tenant_id AND k.user_id = r.user_id
               AND k.relationship_id = r.id
               AND k.status NOT IN ('RESOLVED', 'SUPERSEDED')) AS attention_rank
    FROM relationships r
    JOIN counterparties cp
      ON cp.tenant_id = r.tenant_id
     AND cp.id = r.counterparty_id
    WHERE r.tenant_id = %(tenant_id)s
      AND r.user_id = %(user_id)s
      AND (%(counterparty_id)s::UUID IS NULL OR r.counterparty_id = %(counterparty_id)s::UUID)
      AND (cardinality(%(statuses)s::STRING[]) = 0 OR r.status = ANY(%(statuses)s::STRING[]))
      AND (%(context_id)s::UUID IS NULL
           OR EXISTS (SELECT 1 FROM cases k
                       WHERE k.tenant_id = r.tenant_id AND k.user_id = r.user_id
                         AND k.relationship_id = r.id
                         AND k.context_id = %(context_id)s::UUID))
      AND (%(after_updated_at)s::TIMESTAMPTZ IS NULL
           OR (r.updated_at, r.id) < (%(after_updated_at)s::TIMESTAMPTZ, %(after_id)s::UUID))
    ORDER BY r.updated_at DESC, r.id DESC
    LIMIT %(limit)s
"""

#: 8.7, by id. Same projection, no pagination, one row or none.
RELATIONSHIP_SQL = """
    SELECT r.id AS relationship_id, r.relationship_type, r.label, r.status,
           r.valid_from, r.valid_to, r.revision, r.created_at, r.updated_at,
           r.normalized_identifiers,
           CASE WHEN r.external_account_ref IS NULL THEN NULL
                ELSE '••••' || right(r.external_account_ref, 4)
           END AS external_account_ref_masked,
           cp.id AS counterparty_id, cp.display_name AS counterparty_display_name,
           cp.kind AS counterparty_kind, cp.canonical_domain
    FROM relationships r
    JOIN counterparties cp
      ON cp.tenant_id = r.tenant_id
     AND cp.id = r.counterparty_id
    WHERE r.tenant_id = %(tenant_id)s
      AND r.user_id = %(user_id)s
      AND r.id = %(relationship_id)s
"""

#: The cases under one relationship, for 8.7's ``cases`` block and its
#: ``summary`` counts.
RELATIONSHIP_CASES_SQL = """
    SELECT k.id AS case_id, k.title, k.status, k.revision, k.attention_level,
           k.case_type, k.context_id, k.opened_at, k.resolved_at,
           k.last_activity_at, k.reopened_count
    FROM cases k
    WHERE k.tenant_id = %(tenant_id)s
      AND k.user_id = %(user_id)s
      AND k.relationship_id = %(relationship_id)s
    ORDER BY k.last_activity_at DESC, k.id DESC
    LIMIT %(limit)s
"""

#: Outstanding per ``(relationship_id, currency)``. Never summed across
#: currencies, for the reason ``contexts.py`` states at length.
RELATIONSHIP_OUTSTANDING_SQL = """
    SELECT k.relationship_id, cm.currency, sum(cm.outstanding_amount) AS outstanding
    FROM commitments cm
    JOIN cases k
      ON k.tenant_id = cm.tenant_id
     AND k.user_id = cm.user_id
     AND k.id = cm.case_id
    WHERE cm.tenant_id = %(tenant_id)s
      AND cm.user_id = %(user_id)s
      AND cm.currency IS NOT NULL
      AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
    GROUP BY k.relationship_id, cm.currency
    ORDER BY k.relationship_id, cm.currency
"""


async def list_relationships(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    statuses: tuple[str, ...] = (),
    counterparty_id: uuid.UUID | None = None,
    context_id: uuid.UUID | None = None,
    after_updated_at: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One page of relationships with their counterparty and case counts.

    Every filter is a **bound parameter**, never a concatenated fragment. A
    filter narrows inside the caller's own scope, so it can never widen: the
    two owner predicates are unconditional and sit above every optional one.
    """
    return await _fetch_all(
        conn,
        RELATIONSHIPS_SQL,
        {
            **_owner(tenant_id, user_id),
            "limit": limit,
            "statuses": list(statuses),
            "counterparty_id": counterparty_id,
            "context_id": context_id,
            "after_updated_at": after_updated_at,
            "after_id": after_id,
        },
    )


async def get_relationship(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    relationship_id: uuid.UUID,
) -> dict[str, Any] | None:
    """One relationship, or ``None`` when it does not exist for this owner."""
    return await _fetch_one(
        conn,
        RELATIONSHIP_SQL,
        {**_owner(tenant_id, user_id), "relationship_id": relationship_id},
    )


async def list_relationship_cases(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    relationship_id: uuid.UUID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The cases under one relationship, most recently active first."""
    return await _fetch_all(
        conn,
        RELATIONSHIP_CASES_SQL,
        {**_owner(tenant_id, user_id), "relationship_id": relationship_id, "limit": limit},
    )


async def list_relationship_outstanding(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Outstanding obligations per relationship and currency."""
    return await _fetch_all(conn, RELATIONSHIP_OUTSTANDING_SQL, _owner(tenant_id, user_id))
