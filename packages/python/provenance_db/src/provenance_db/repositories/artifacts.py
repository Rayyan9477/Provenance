"""Reads over ``source_artifacts`` — the bytes memory was built from.

Authority
---------
- ``specs/15_API_SPEC.md`` sections 8.17 (``GET /v1/artifacts``) and 8.20
  (``GET /v1/artifacts/{id}``).
- ``specs/10_DATABASE_DDL.md`` section 12 — ``source_artifacts`` belongs to
  ``pv_app_reader_writer``; the Kernel holds only ``SELECT`` on it. This
  module reads, and the ingestion write arrives with Phase 7.

``s3_bucket`` and ``s3_key`` are never projected
-------------------------------------------------
The API hands out a **pre-signed URL** (section 8.20) or nothing. Returning
the key would put an object path into a browser payload, where the only thing
standing between it and a direct fetch is the bucket policy — and a bucket
policy is not the layer that should be carrying that decision. The download
URL is minted by the write path, from the row, on request.

``content_sha256`` is projected as hex, in the SQL
---------------------------------------------------
The column is ``BYTES``. Every caller wants the 64-character hex digest the
rest of the pack prints, and ``encode(..., 'hex')`` at the projection means no
renderer can produce a different spelling of the same digest — which is what
makes ``duplicate_of`` in section 8.19 checkable by eye against a curl.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from provenance_db.repositories._execute import _fetch_all, _fetch_one, _owner

__all__ = [
    "ARTIFACTS_SQL",
    "ARTIFACT_CASES_SQL",
    "ARTIFACT_SQL",
    "get_artifact",
    "list_artifact_cases",
    "list_artifacts",
]

#: Section 8.17, sorted ``received_at DESC, id DESC``.
#:
#: The projection is repeated verbatim in :data:`ARTIFACT_SQL` rather than
#: shared through an f-string or a ``+`` chain, and that is deliberate. Both
#: guards that police this package --
#: ``tests/db/test_repository_read_only.py`` and, for the retrieval tree,
#: ``tests/retrieval/test_no_unscoped_sql.py`` -- identify SQL by finding
#: string **constants** in the AST. A statement assembled from fragments is
#: not a constant, so it is not scanned, so its missing ``user_id`` predicate
#: is not reported. Twelve duplicated column names are a much smaller cost
#: than a statement that has quietly stopped being checked.
#:
#: The ``case_id`` filter reaches through ``evidence_items`` and ``claims``,
#: because an artifact has no case: evidence reaches a case through a claim,
#: and one artifact can feed several cases. ``EXISTS`` rather than a join, so
#: an artifact that produced nine claims on one case is still one row.
ARTIFACTS_SQL = """
    SELECT sa.id AS artifact_id, sa.source_type, sa.mime_type, sa.size_bytes,
           encode(sa.content_sha256, 'hex') AS content_sha256,
           sa.sender AS sender_display, sa.recipient AS recipient_display,
           sa.subject, sa.source_message_id, sa.received_at, sa.event_time,
           sa.parser_status, sa.parser_version, sa.parser_metadata,
           (SELECT count(*) FROM evidence_items e
             WHERE e.tenant_id = sa.tenant_id
               AND e.user_id = sa.user_id
               AND e.artifact_id = sa.id) AS evidence_item_count
    FROM source_artifacts sa
    WHERE sa.tenant_id = %(tenant_id)s
      AND sa.user_id = %(user_id)s
      AND (cardinality(%(source_types)s::STRING[]) = 0
           OR sa.source_type = ANY(%(source_types)s::STRING[]))
      AND (cardinality(%(parser_statuses)s::STRING[]) = 0
           OR sa.parser_status = ANY(%(parser_statuses)s::STRING[]))
      AND (%(case_id)s::UUID IS NULL
           OR EXISTS (SELECT 1
                        FROM evidence_items e
                        JOIN claims c
                          ON c.tenant_id = e.tenant_id
                         AND c.user_id = e.user_id
                         AND c.evidence_id = e.id
                       WHERE e.tenant_id = sa.tenant_id
                         AND e.user_id = sa.user_id
                         AND e.artifact_id = sa.id
                         AND c.case_id = %(case_id)s::UUID))
      AND (%(after_received_at)s::TIMESTAMPTZ IS NULL
           OR (sa.received_at, sa.id) < (%(after_received_at)s::TIMESTAMPTZ, %(after_id)s::UUID))
    ORDER BY sa.received_at DESC, sa.id DESC
    LIMIT %(limit)s
"""

ARTIFACT_SQL = """
    SELECT sa.id AS artifact_id, sa.source_type, sa.mime_type, sa.size_bytes,
           encode(sa.content_sha256, 'hex') AS content_sha256,
           sa.sender AS sender_display, sa.recipient AS recipient_display,
           sa.subject, sa.source_message_id, sa.received_at, sa.event_time,
           sa.parser_status, sa.parser_version, sa.parser_metadata,
           (SELECT count(*) FROM evidence_items e
             WHERE e.tenant_id = sa.tenant_id
               AND e.user_id = sa.user_id
               AND e.artifact_id = sa.id) AS evidence_item_count,
           sa.thread_ref, sa.sender_domain, sa.ses_verdicts, sa.created_at
    FROM source_artifacts sa
    WHERE sa.tenant_id = %(tenant_id)s
      AND sa.user_id = %(user_id)s
      AND sa.id = %(artifact_id)s
"""

#: 8.20's ``linked_cases``: which cases this artifact's evidence reached, and
#: how many claims it produced on each.
ARTIFACT_CASES_SQL = """
    SELECT c.case_id, k.title AS case_title, count(*) AS claim_count
    FROM claims c
    JOIN evidence_items e
      ON e.tenant_id = c.tenant_id
     AND e.user_id = c.user_id
     AND e.id = c.evidence_id
    JOIN cases k
      ON k.tenant_id = c.tenant_id
     AND k.user_id = c.user_id
     AND k.id = c.case_id
    WHERE c.tenant_id = %(tenant_id)s
      AND c.user_id = %(user_id)s
      AND e.artifact_id = %(artifact_id)s
      AND c.case_id IS NOT NULL
    GROUP BY c.case_id, k.title
    ORDER BY count(*) DESC, c.case_id
"""


async def list_artifacts(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    source_types: tuple[str, ...] = (),
    parser_statuses: tuple[str, ...] = (),
    case_id: uuid.UUID | None = None,
    after_received_at: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One page of artifacts, most recently received first."""
    return await _fetch_all(
        conn,
        ARTIFACTS_SQL,
        {
            **_owner(tenant_id, user_id),
            "limit": limit,
            "source_types": list(source_types),
            "parser_statuses": list(parser_statuses),
            "case_id": case_id,
            "after_received_at": after_received_at,
            "after_id": after_id,
        },
    )


async def get_artifact(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> dict[str, Any] | None:
    """One artifact's metadata, or ``None`` for this owner."""
    return await _fetch_one(
        conn, ARTIFACT_SQL, {**_owner(tenant_id, user_id), "artifact_id": artifact_id}
    )


async def list_artifact_cases(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """The cases this artifact's evidence reached, busiest first."""
    return await _fetch_all(
        conn, ARTIFACT_CASES_SQL, {**_owner(tenant_id, user_id), "artifact_id": artifact_id}
    )
