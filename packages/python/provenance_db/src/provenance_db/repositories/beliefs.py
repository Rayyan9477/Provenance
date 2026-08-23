"""Reads over the epistemic plane: beliefs, versions and their grounding.

Authority: ``specs/10_DATABASE_DDL.md`` section 12 (write-path ownership) and
``specs/13_RETRIEVAL_SPEC.md`` section 19 (module layout). Split by domain,
never by table: a repository spanning two aggregates hides a transaction
boundary.

**grounding** is the ``belief_support`` edge set; **lineage** is the
``belief_versions`` supersession chain. State Proof renders both, and a
module that conflates them is rejected at review (task plan section 2.3).
They are three separate functions here for exactly that reason — one read that
returned "the belief with its history and its evidence" would make the two
indistinguishable at every call site downstream.

``CANONICAL_DECISIONS.md`` -> *Retention effect*: a belief version that loses
all its grounding becomes ``RETRACTED`` with a tombstoned support record; it
never silently disappears. So :func:`get_active_beliefs_for_case` excludes
``RETRACTED`` and :func:`get_belief_lineage` does not — the chain is the
history, and a history with the retraction edited out is not one.
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
    "ACTIVE_BELIEFS_FOR_CASE_SQL",
    "BELIEF_HEAD_SQL",
    "BELIEF_LINEAGE_SQL",
    "BELIEF_SUPPORT_SQL",
    "CASE_BELIEF_SUPPORT_SQL",
    "CASE_BELIEF_VERSIONS_SQL",
    "CASE_BELIEFS_SQL",
    "get_active_beliefs_for_case",
    "get_belief_head",
    "get_belief_lineage",
    "get_belief_lineage_for",
    "get_belief_support",
    "get_belief_support_for",
    "list_case_belief_support",
    "list_case_belief_versions",
    "list_case_beliefs",
]

#: The live version of every belief on one case.
#:
#: The join is on ``beliefs.current_version_id``, not on ``max(version_no)``.
#: The pointer is what the Kernel updates inside the transaction that writes the
#: new version, so it is the only definition of "current" that cannot disagree
#: with the write path — an aggregate over ``version_no`` would silently pick a
#: row the Kernel had not yet promoted.
ACTIVE_BELIEFS_FOR_CASE_SQL = """
    SELECT b.id AS belief_id, b.case_id, b.subject_type, b.subject_id,
           b.predicate,
           bv.id AS belief_version_id, bv.version_no, bv.value_type,
           bv.value_json, bv.epistemic_status, bv.belief_confidence,
           bv.derivation_kind, bv.support_edge_count,
           bv.supersedes_version_id, bv.supersession_reason_code,
           bv.valid_from, bv.valid_to, bv.recorded_at, bv.kernel_decision_id
    FROM beliefs b
    JOIN belief_versions bv
      ON bv.tenant_id = b.tenant_id
     AND bv.user_id = b.user_id
     AND bv.id = b.current_version_id
    WHERE b.tenant_id = %(tenant_id)s
      AND b.user_id = %(user_id)s
      AND b.case_id = %(case_id)s
      AND bv.epistemic_status <> 'RETRACTED'
    ORDER BY b.predicate, bv.version_no DESC
"""

#: The ordered supersession chain of one belief, oldest first, with the reason
#: for each change. ``ORDER BY version_no`` and not ``recorded_at``: two
#: versions written inside one Kernel transaction share a timestamp, and the
#: chain is what ``uq_belief_versions_chain`` makes total.
BELIEF_LINEAGE_SQL = """
    SELECT bv.id AS belief_version_id, bv.belief_id, bv.version_no,
           bv.value_type, bv.value_json, bv.epistemic_status,
           bv.belief_confidence, bv.derivation_kind, bv.support_edge_count,
           bv.supersedes_version_id, bv.supersession_reason_code,
           bv.valid_from, bv.valid_to, bv.recorded_at, bv.superseded_at,
           bv.kernel_decision_id
    FROM belief_versions bv
    WHERE bv.tenant_id = %(tenant_id)s
      AND bv.user_id = %(user_id)s
      AND bv.belief_id = %(belief_id)s
    ORDER BY bv.version_no
"""

#: The grounding edges of one belief version.
#:
#: ``CONTRADICTS`` and ``QUALIFIES`` edges are returned alongside ``SUPPORTS``.
#: A State Proof that showed only what agreed with the conclusion would be the
#: exact opposite of the thing this product sells, and the retracted evidence
#: those edges point at is frequently *why* a version was superseded.
BELIEF_SUPPORT_SQL = """
    SELECT bs.id, bs.belief_version_id, bs.source_kind, bs.source_id,
           bs.relation, bs.weight, bs.reason_code, bs.created_at
    FROM belief_support bs
    WHERE bs.tenant_id = %(tenant_id)s
      AND bs.user_id = %(user_id)s
      AND bs.belief_version_id = %(belief_version_id)s
    ORDER BY bs.relation, bs.created_at
"""


async def get_active_beliefs_for_case(
    conn: AsyncConnection[Any],
    principal: Principal,
    case_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """The live belief version of every belief attached to one case."""
    return await _fetch_all(
        conn,
        ACTIVE_BELIEFS_FOR_CASE_SQL,
        {**_scope(principal), "case_id": case_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


async def get_belief_lineage(
    conn: AsyncConnection[Any],
    principal: Principal,
    belief_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """The ordered supersession chain of one belief, with its reasons."""
    return await _fetch_all(
        conn,
        BELIEF_LINEAGE_SQL,
        {**_scope(principal), "belief_id": belief_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


async def get_belief_support(
    conn: AsyncConnection[Any],
    principal: Principal,
    belief_version_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """The grounding edges of one belief version: supports, contradicts, qualifies."""
    return await _fetch_all(
        conn,
        BELIEF_SUPPORT_SQL,
        {**_scope(principal), "belief_version_id": belief_version_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


# ===========================================================================
# The API read models (T8.9)
# ===========================================================================

#: Section 8.13's header: one belief, its case, and the pointer to its live
#: version. ``subject_label`` is resolved through ``relationships`` and
#: ``counterparties`` when the subject is a relationship, because "the belief
#: about ``018f7c00-…``" is not a sentence a person can check.
BELIEF_HEAD_SQL = """
    SELECT b.id AS belief_id, b.case_id, b.subject_type, b.subject_id,
           b.predicate, b.current_version_id,
           k.relationship_id, k.title AS case_title,
           cp.display_name AS subject_label
    FROM beliefs b
    JOIN cases k
      ON k.tenant_id = b.tenant_id AND k.user_id = b.user_id AND k.id = b.case_id
    JOIN relationships r
      ON r.tenant_id = k.tenant_id AND r.user_id = k.user_id AND r.id = k.relationship_id
    JOIN counterparties cp
      ON cp.tenant_id = k.tenant_id AND cp.id = r.counterparty_id
    WHERE b.tenant_id = %(tenant_id)s
      AND b.user_id = %(user_id)s
      AND b.id = %(belief_id)s
"""

#: Section 8.11's ``beliefs[]`` header rows for a whole case.
#:
#: ``grounded`` is computed here rather than read from a column. Section
#: 8.11.1 defines it as "this version has at least one ``SUPPORTS`` edge", and
#: ``belief_versions.support_edge_count`` counts **every** relation --
#: including ``CONTRADICTS``. A version grounded only by the thing that argues
#: against it would read as grounded off the stored counter, which is the
#: precise condition section 8.11.1 raises as a P1 data-integrity alarm.
CASE_BELIEFS_SQL = """
    SELECT b.id AS belief_id, b.case_id, b.subject_type, b.subject_id,
           b.predicate,
           bv.id AS belief_version_id, bv.version_no, bv.value_type,
           bv.value_json, bv.epistemic_status, bv.belief_confidence,
           bv.derivation_kind, bv.support_edge_count,
           bv.supersedes_version_id, bv.supersession_reason_code,
           bv.valid_from, bv.valid_to, bv.recorded_at, bv.superseded_at,
           bv.kernel_decision_id,
           (SELECT count(*) FROM belief_support bs
             WHERE bs.tenant_id = bv.tenant_id AND bs.user_id = bv.user_id
               AND bs.belief_version_id = bv.id
               AND bs.relation = 'SUPPORTS') AS supports_count
    FROM beliefs b
    JOIN belief_versions bv
      ON bv.tenant_id = b.tenant_id AND bv.user_id = b.user_id
     AND bv.id = b.current_version_id
    WHERE b.tenant_id = %(tenant_id)s
      AND b.user_id = %(user_id)s
      AND b.case_id = %(case_id)s
      AND (cardinality(%(belief_ids)s::UUID[]) = 0
           OR b.id = ANY(%(belief_ids)s::UUID[]))
    ORDER BY b.predicate, b.id
"""

#: Every version of every belief on one case, with the reason each one was
#: superseded.
#:
#: Section 8.11.2: ``belief_versions`` has no supersession-reason column, so
#: the codes for version *n* are read from the ``kernel_decisions`` row of the
#: decision that created version *n + 1* -- the row that actually made the
#: decision. The self-join on ``version_no + 1`` is that statement, and it
#: keeps the reasons single-sourced instead of duplicated onto the version.
CASE_BELIEF_VERSIONS_SQL = """
    SELECT bv.belief_id, bv.id AS belief_version_id, bv.version_no,
           bv.value_type, bv.value_json, bv.epistemic_status,
           bv.belief_confidence, bv.derivation_kind, bv.support_edge_count,
           bv.valid_from, bv.valid_to, bv.recorded_at, bv.superseded_at,
           bv.kernel_decision_id,
           nxt.id AS superseded_by_version_id,
           nxt.version_no AS superseded_by_version_no,
           COALESCE(kd.reason_codes, '[]'::JSONB) AS supersession_reason_codes,
           (SELECT count(*) FROM belief_support bs
             WHERE bs.tenant_id = bv.tenant_id AND bs.user_id = bv.user_id
               AND bs.belief_version_id = bv.id) AS grounding_count
    FROM belief_versions bv
    JOIN beliefs b
      ON b.tenant_id = bv.tenant_id AND b.user_id = bv.user_id AND b.id = bv.belief_id
    LEFT JOIN belief_versions nxt
      ON nxt.tenant_id = bv.tenant_id AND nxt.user_id = bv.user_id
     AND nxt.belief_id = bv.belief_id AND nxt.version_no = bv.version_no + 1
    LEFT JOIN kernel_decisions kd
      ON kd.tenant_id = bv.tenant_id AND kd.user_id = bv.user_id
     AND kd.id = nxt.kernel_decision_id
    WHERE bv.tenant_id = %(tenant_id)s
      AND bv.user_id = %(user_id)s
      AND b.case_id = %(case_id)s
      AND (cardinality(%(belief_ids)s::UUID[]) = 0
           OR bv.belief_id = ANY(%(belief_ids)s::UUID[]))
    ORDER BY bv.belief_id, bv.version_no
"""

#: Every grounding edge on one case, with the evidence or claim it points at.
#:
#: ``include_retracted`` (section 8.11.3) selects between showing the retracted
#: source and hiding it, and it is a **bound parameter** rather than two
#: statements: the default excludes it, and the same query with the flag true
#: is what an audit reads. Retracted evidence keeps its row, its bytes and its
#: embedding; what changes is whether this projection renders it.
#:
#: ``LEFT JOIN`` on both sources, because ``source_kind`` may be ``EVIDENCE``,
#: ``CLAIM``, ``BELIEF_VERSION`` or ``DERIVATION`` and only the first two have
#: a row to render here. A ``JOIN`` would silently drop every derivation edge,
#: which is exactly how a deterministically-derived belief comes to look
#: ungrounded.
CASE_BELIEF_SUPPORT_SQL = """
    SELECT bs.id AS support_id, bs.belief_version_id, bs.source_kind,
           bs.source_id, bs.relation, bs.weight, bs.reason_code, bs.created_at,
           e.id AS evidence_id, e.artifact_id, e.evidence_type,
           e.exact_text, e.normalized_text, e.source_locator, e.observed_at,
           e.source_authority, e.extraction_confidence, e.retraction_status,
           e.retracted_at, e.retracted_by_evidence_id, e.retraction_reason_code,
           sa.source_type AS artifact_source_type, sa.sender AS artifact_sender,
           sa.subject AS artifact_subject, sa.received_at AS artifact_received_at,
           c.id AS claim_id, c.claim_kind, c.predicate AS claim_predicate,
           c.object_json, c.actor_type AS claim_actor_type,
           c.authority_score, c.recorded_at AS claim_recorded_at,
           c.evidence_id AS claim_evidence_id
    FROM belief_support bs
    JOIN belief_versions bv
      ON bv.tenant_id = bs.tenant_id AND bv.user_id = bs.user_id
     AND bv.id = bs.belief_version_id
    JOIN beliefs b
      ON b.tenant_id = bv.tenant_id AND b.user_id = bv.user_id AND b.id = bv.belief_id
    LEFT JOIN evidence_items e
      ON bs.source_kind = 'EVIDENCE'
     AND e.tenant_id = bs.tenant_id AND e.user_id = bs.user_id AND e.id = bs.source_id
    LEFT JOIN source_artifacts sa
      ON sa.tenant_id = e.tenant_id AND sa.user_id = e.user_id AND sa.id = e.artifact_id
    LEFT JOIN claims c
      ON bs.source_kind = 'CLAIM'
     AND c.tenant_id = bs.tenant_id AND c.user_id = bs.user_id AND c.id = bs.source_id
    WHERE bs.tenant_id = %(tenant_id)s
      AND bs.user_id = %(user_id)s
      AND b.case_id = %(case_id)s
      AND (cardinality(%(belief_ids)s::UUID[]) = 0
           OR bv.belief_id = ANY(%(belief_ids)s::UUID[]))
      AND (%(include_retracted)s::BOOL
           OR bs.source_kind <> 'EVIDENCE'
           OR e.retraction_status = 'ACTIVE')
    ORDER BY bs.belief_version_id,
             (bs.relation = 'CONTRADICTS') DESC,
             bs.weight DESC NULLS LAST,
             bs.id
"""


async def get_belief_head(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    belief_id: uuid.UUID,
) -> dict[str, Any] | None:
    """One belief's identity row, or ``None`` for this owner."""
    return await _fetch_one(
        conn, BELIEF_HEAD_SQL, {**_owner(tenant_id, user_id), "belief_id": belief_id}
    )


async def get_belief_lineage_for(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    belief_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """:func:`get_belief_lineage`, for a caller holding an owner pair."""
    return await _fetch_all(
        conn, BELIEF_LINEAGE_SQL, {**_owner(tenant_id, user_id), "belief_id": belief_id}
    )


async def get_belief_support_for(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    belief_version_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """:func:`get_belief_support`, for a caller holding an owner pair."""
    return await _fetch_all(
        conn,
        BELIEF_SUPPORT_SQL,
        {**_owner(tenant_id, user_id), "belief_version_id": belief_version_id},
    )


async def list_case_beliefs(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    belief_ids: tuple[uuid.UUID, ...] = (),
) -> list[dict[str, Any]]:
    """The live version of every belief on one case, with its supports count."""
    return await _fetch_all(
        conn,
        CASE_BELIEFS_SQL,
        {**_owner(tenant_id, user_id), "case_id": case_id, "belief_ids": list(belief_ids)},
    )


async def list_case_belief_versions(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    belief_ids: tuple[uuid.UUID, ...] = (),
) -> list[dict[str, Any]]:
    """Every version of every belief on one case, oldest first, with reasons.

    Lineage, not grounding. A history with the retraction edited out is not a
    history, so ``RETRACTED`` and ``SUPERSEDED`` versions are all here; the
    grounding query is the one that filters.
    """
    return await _fetch_all(
        conn,
        CASE_BELIEF_VERSIONS_SQL,
        {**_owner(tenant_id, user_id), "case_id": case_id, "belief_ids": list(belief_ids)},
    )


async def list_case_belief_support(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    belief_ids: tuple[uuid.UUID, ...] = (),
    include_retracted: bool = False,
) -> list[dict[str, Any]]:
    """Grounding, not lineage: the edges and the observations behind them."""
    return await _fetch_all(
        conn,
        CASE_BELIEF_SUPPORT_SQL,
        {
            **_owner(tenant_id, user_id),
            "case_id": case_id,
            "belief_ids": list(belief_ids),
            "include_retracted": include_retracted,
        },
    )
