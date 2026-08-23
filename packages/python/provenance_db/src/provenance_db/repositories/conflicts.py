"""Reads over the durable ``conflicts`` ledger.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.12 (``GET /v1/cases/{id}/conflicts``)
  and the ``conflicts[]`` block of 8.11.
- ``specs/10_DATABASE_DDL.md`` section 12 — ``conflicts`` is canonical and
  Kernel-written; this module reads.
- ``CANONICAL_DECISIONS.md`` -> *Hero conflict*: ``VALUE_CONFLICT``, family
  ``BALANCE``, ``status = 'NEEDS_HUMAN'``, ``severity = 'HIGH'``,
  ``requires_human = true``.

"Open" is two statuses, and naming them is not pedantry
--------------------------------------------------------
``conflicts.status`` is one of ``OPEN``, ``AUTO_RESOLVED``, ``NEEDS_HUMAN``,
``RESOLVED`` and ``SUPERSEDED``. The hero conflict's status is
``NEEDS_HUMAN``, so a predicate written as ``status = 'OPEN'`` — the obvious
first draft — renders the demo's central artifact as *absent* from the case
detail's ``active_conflicts`` block. No error, no empty-state banner, just a
case that looks settled. :data:`UNRESOLVED_STATUSES` is the pair, and it is
the same pair ``app/retrieval/grounding.py`` binds for stage F.4a, so the
prompt context and the screen cannot disagree about what is still contested.

Severity ordering is a ``CASE``, not the string
------------------------------------------------
``HIGH`` < ``LOW`` < ``MEDIUM`` alphabetically. Sorting on the column would
put the medium-severity conflicts last and the high-severity ones first by
accident, and would silently reorder the moment a fourth level appeared.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from provenance_db.repositories._execute import _fetch_all, _owner

__all__ = [
    "CASE_CONFLICTS_SQL",
    "OPEN_CONFLICT_COUNT_SQL",
    "UNRESOLVED_STATUSES",
    "count_open_conflicts",
    "list_conflicts_for_case",
]

#: The two statuses in which a contradiction is still live.
UNRESOLVED_STATUSES: tuple[str, ...] = ("OPEN", "NEEDS_HUMAN")

CASE_CONFLICTS_SQL = """
    SELECT cf.id AS conflict_id, cf.case_id, cf.subject_type, cf.subject_id,
           cf.predicate, cf.conflict_type, cf.status, cf.severity,
           cf.requires_human, cf.left_source_kind, cf.left_source_id,
           cf.right_source_kind, cf.right_source_id,
           cf.canonical_belief_version_id, cf.resolution_reason_code,
           cf.resolution_notes, cf.detected_at, cf.resolved_at
    FROM conflicts cf
    WHERE cf.tenant_id = %(tenant_id)s
      AND cf.user_id = %(user_id)s
      AND cf.case_id = %(case_id)s
      AND (cardinality(%(statuses)s::STRING[]) = 0
           OR cf.status = ANY(%(statuses)s::STRING[]))
      AND (%(severity)s::STRING IS NULL OR cf.severity = %(severity)s::STRING)
      AND (%(requires_human)s::BOOL IS NULL OR cf.requires_human = %(requires_human)s::BOOL)
      AND (%(after_detected_at)s::TIMESTAMPTZ IS NULL
           OR (cf.detected_at, cf.id) < (%(after_detected_at)s::TIMESTAMPTZ, %(after_id)s::UUID))
    ORDER BY cf.detected_at DESC, cf.id DESC
    LIMIT %(limit)s
"""

#: The dashboard's ``active_conflicts`` figure, counted at query time.
#: ``CANONICAL_DECISIONS.md`` -> *Corpus counts* forbids rendering a constant
#: where a counted value belongs, and the same reasoning applies to every tile
#: on the home screen: a number nobody counted is a number nobody can defend.
OPEN_CONFLICT_COUNT_SQL = """
    SELECT count(*) AS active_conflicts
    FROM conflicts cf
    WHERE cf.tenant_id = %(tenant_id)s
      AND cf.user_id = %(user_id)s
      AND cf.status IN ('OPEN', 'NEEDS_HUMAN')
"""


async def list_conflicts_for_case(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    limit: int,
    statuses: tuple[str, ...] = (),
    severity: str | None = None,
    requires_human: bool | None = None,
    after_detected_at: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Conflicts on one case, most recently detected first."""
    return await _fetch_all(
        conn,
        CASE_CONFLICTS_SQL,
        {
            **_owner(tenant_id, user_id),
            "case_id": case_id,
            "limit": limit,
            "statuses": list(statuses),
            "severity": severity,
            "requires_human": requires_human,
            "after_detected_at": after_detected_at,
            "after_id": after_id,
        },
    )


async def count_open_conflicts(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    """How many contradictions are still live for one user."""
    rows = await _fetch_all(conn, OPEN_CONFLICT_COUNT_SQL, _owner(tenant_id, user_id))
    return int(rows[0]["active_conflicts"]) if rows else 0
