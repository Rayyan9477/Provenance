"""The merged case timeline — ``UNION ALL`` over everything that happened.

Authority
---------
- ``specs/15_API_SPEC.md`` section 8.10, which specifies the shape exactly:
  "assembled by a ``UNION ALL`` over the contributing tables with a common
  ``(occurred_at, id)`` projection, then keyset-paginated on that tuple. Each
  branch carries ``tenant_id``, ``user_id``, and ``case_id`` predicates."
- ``specs/10_DATABASE_DDL.md`` section 12 — every table below is read-only
  here.

Why the ``UNION ALL`` and not one query per kind
-------------------------------------------------
The timeline is keyset-paginated on ``(occurred_at, id)`` across *all* kinds.
Ten separate queries merged in Python cannot be paginated on that tuple
without over-fetching every branch and discarding most of it, and the first
page would be correct while page four quietly skipped rows. One statement,
one ordering, one cursor.

Each branch repeats the three owner predicates
------------------------------------------------
Section 8.10 requires it and the repetition is the point: a ``UNION ALL``
branch is a whole query, and a branch that lost its ``user_id`` predicate
would contribute another user's rows into a result that is otherwise
correctly scoped. The leak would appear as a handful of unexplained entries in
one person's history — the least likely thing anyone would report as a
security bug.

``case_revision`` is on the row where the row has one
------------------------------------------------------
Only ``state_transitions`` carries a revision. For every other branch the
outer ``SELECT`` derives "the revision this case was at when that happened"
from the transition ledger, which is the only table that records it.
``COALESCE`` rather than always deriving: two transitions committed in one
Kernel transaction share a timestamp, so the derived value would collapse
them onto the later revision, and the row that *has* the answer should give
it.

``detail`` is built by ``jsonb_build_object``, never by string concatenation
-----------------------------------------------------------------------------
It is a projection of columns, so it is bound by the server and cannot carry
an injected fragment. Section 8.10 also bounds what may appear: metadata
only, and for ``USER_CORRECTION`` a 200-character excerpt of the user's own
statement. The excerpt limit is in the SQL rather than in the renderer, so a
surface that forgets to truncate cannot.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from provenance_db.repositories._execute import _fetch_all, _owner

__all__ = [
    "CASE_TIMELINE_SQL",
    "TIMELINE_KINDS",
    "list_case_timeline",
]

#: Every ``kind`` a branch below can emit. Section 8.10's table is wider --
#: ``BELIEF_CHANGED``, ``TRIGGER_NOOP``, ``ACTION_EXECUTED`` and
#: ``ACTION_FAILED`` need ``action_executions`` and a belief-change ledger that
#: Phase 9 and Phase 10 own -- and the difference is reported here rather than
#: silently rendered as an empty timeline. A ``kind`` filter naming one of the
#: absent values matches nothing, which is the truthful answer: nothing of that
#: kind is recorded yet.
TIMELINE_KINDS: tuple[str, ...] = (
    "ARTIFACT_RECEIVED",
    "EVIDENCE_ADMITTED",
    "CLAIM_RECORDED",
    "STATE_TRANSITION",
    "CONFLICT_OPENED",
    "CONFLICT_RESOLVED",
    "COMMITMENT_CREATED",
    "FULFILLMENT_ADMITTED",
    "TRIGGER_ARMED",
    "TRIGGER_FIRED",
    "ACTION_PROPOSED",
    "ACTION_APPROVED",
    "ACTION_REJECTED",
    "USER_CORRECTION",
)

CASE_TIMELINE_SQL = """
    WITH entries AS (
        SELECT st.id AS id,
               'STATE_TRANSITION' AS kind,
               st.recorded_at AS occurred_at,
               st.case_revision AS case_revision,
               st.trace_id AS trace_id,
               'KERNEL' AS actor_type,
               'Memory Kernel' AS actor_label,
               jsonb_build_object(
                   'transition_type', st.transition_type,
                   'subject_kind', st.subject_kind,
                   'subject_id', st.subject_id,
                   'from_state', st.from_state,
                   'to_state', st.to_state,
                   'reason_code', st.reason_code,
                   'kernel_decision_id', st.kernel_decision_id
               ) AS detail
        FROM state_transitions st
        WHERE st.tenant_id = %(tenant_id)s
          AND st.user_id = %(user_id)s
          AND st.case_id = %(case_id)s

        UNION ALL

        SELECT DISTINCT sa.id, 'ARTIFACT_RECEIVED', sa.received_at,
               NULL::INT8, NULL::UUID,
               CASE WHEN sa.source_type IN ('EMAIL_INBOUND') THEN 'COUNTERPARTY'
                    WHEN sa.source_type = 'USER_CORRECTION' THEN 'USER'
                    ELSE 'SYSTEM' END,
               coalesce(sa.sender, sa.source_type),
               jsonb_build_object(
                   'artifact_id', sa.id,
                   'source_type', sa.source_type,
                   'mime_type', sa.mime_type,
                   'sender_display', sa.sender,
                   'subject', sa.subject,
                   'received_at', sa.received_at,
                   'parser_status', sa.parser_status
               )
        FROM source_artifacts sa
        JOIN evidence_items e
          ON e.tenant_id = sa.tenant_id AND e.user_id = sa.user_id AND e.artifact_id = sa.id
        JOIN claims c
          ON c.tenant_id = e.tenant_id AND c.user_id = e.user_id AND c.evidence_id = e.id
        WHERE sa.tenant_id = %(tenant_id)s
          AND sa.user_id = %(user_id)s
          AND c.case_id = %(case_id)s

        UNION ALL

        SELECT DISTINCT e.id, 'EVIDENCE_ADMITTED', e.observed_at,
               NULL::INT8, NULL::UUID, 'SYSTEM', 'Extraction',
               jsonb_build_object(
                   'evidence_id', e.id,
                   'artifact_id', e.artifact_id,
                   'evidence_type', e.evidence_type,
                   'retraction_status', e.retraction_status,
                   'extraction_confidence', e.extraction_confidence
               )
        FROM evidence_items e
        JOIN claims c
          ON c.tenant_id = e.tenant_id AND c.user_id = e.user_id AND c.evidence_id = e.id
        WHERE e.tenant_id = %(tenant_id)s
          AND e.user_id = %(user_id)s
          AND c.case_id = %(case_id)s

        UNION ALL

        SELECT c.id, 'CLAIM_RECORDED', c.recorded_at,
               NULL::INT8, NULL::UUID, c.actor_type, coalesce(c.actor_id, c.actor_type),
               jsonb_build_object(
                   'claim_id', c.id,
                   'claim_kind', c.claim_kind,
                   'predicate', c.predicate,
                   'actor_type', c.actor_type,
                   'object_summary', c.object_json,
                   'evidence_id', c.evidence_id
               )
        FROM claims c
        WHERE c.tenant_id = %(tenant_id)s
          AND c.user_id = %(user_id)s
          AND c.case_id = %(case_id)s

        UNION ALL

        SELECT cf.id, 'CONFLICT_OPENED', cf.detected_at,
               NULL::INT8, NULL::UUID, 'KERNEL', 'Memory Kernel',
               jsonb_build_object(
                   'conflict_id', cf.id,
                   'conflict_type', cf.conflict_type,
                   'severity', cf.severity,
                   'status', cf.status,
                   'requires_human', cf.requires_human,
                   'predicate', cf.predicate
               )
        FROM conflicts cf
        WHERE cf.tenant_id = %(tenant_id)s
          AND cf.user_id = %(user_id)s
          AND cf.case_id = %(case_id)s

        UNION ALL

        SELECT cf.id, 'CONFLICT_RESOLVED', cf.resolved_at,
               NULL::INT8, NULL::UUID, 'KERNEL', 'Memory Kernel',
               jsonb_build_object(
                   'conflict_id', cf.id,
                   'conflict_type', cf.conflict_type,
                   'severity', cf.severity,
                   'status', cf.status,
                   'resolution_reason_code', cf.resolution_reason_code
               )
        FROM conflicts cf
        WHERE cf.tenant_id = %(tenant_id)s
          AND cf.user_id = %(user_id)s
          AND cf.case_id = %(case_id)s
          AND cf.resolved_at IS NOT NULL

        UNION ALL

        SELECT cm.id, 'COMMITMENT_CREATED', cm.created_at,
               NULL::INT8, NULL::UUID, 'KERNEL', 'Memory Kernel',
               jsonb_build_object(
                   'commitment_id', cm.id,
                   'status', cm.status,
                   'currency', cm.currency,
                   'committed_amount', cm.committed_amount,
                   'fulfilled_amount', cm.fulfilled_amount,
                   'outstanding_amount', cm.outstanding_amount,
                   'due_at', cm.due_at
               )
        FROM commitments cm
        WHERE cm.tenant_id = %(tenant_id)s
          AND cm.user_id = %(user_id)s
          AND cm.case_id = %(case_id)s

        UNION ALL

        SELECT f.id, 'FULFILLMENT_ADMITTED', f.fulfilled_at,
               NULL::INT8, NULL::UUID, 'COUNTERPARTY', 'Counterparty',
               jsonb_build_object(
                   'fulfillment_id', f.id,
                   'commitment_id', f.commitment_id,
                   'currency', f.currency,
                   'amount', f.amount,
                   'admission_status', f.admission_status,
                   'evidence_id', f.evidence_id
               )
        FROM fulfillments f
        JOIN commitments cm
          ON cm.tenant_id = f.tenant_id AND cm.user_id = f.user_id AND cm.id = f.commitment_id
        WHERE f.tenant_id = %(tenant_id)s
          AND f.user_id = %(user_id)s
          AND cm.case_id = %(case_id)s

        UNION ALL

        SELECT t.id, 'TRIGGER_ARMED', t.created_at,
               t.basis_case_revision, NULL::UUID, 'SCHEDULER', 'Prospective memory',
               jsonb_build_object(
                   'trigger_id', t.id,
                   'trigger_type', t.trigger_type,
                   'state', t.state,
                   'evaluation_version', t.evaluation_version,
                   'not_before', t.not_before,
                   'last_result', t.last_result
               )
        FROM prospective_triggers t
        WHERE t.tenant_id = %(tenant_id)s
          AND t.user_id = %(user_id)s
          AND t.case_id = %(case_id)s

        UNION ALL

        SELECT t.id, 'TRIGGER_FIRED', t.fired_at,
               t.basis_case_revision, NULL::UUID, 'SCHEDULER', 'Prospective memory',
               jsonb_build_object(
                   'trigger_id', t.id,
                   'trigger_type', t.trigger_type,
                   'state', t.state,
                   'evaluation_version', t.evaluation_version,
                   'last_result', t.last_result,
                   'last_reason_code', t.last_reason_code
               )
        FROM prospective_triggers t
        WHERE t.tenant_id = %(tenant_id)s
          AND t.user_id = %(user_id)s
          AND t.case_id = %(case_id)s
          AND t.fired_at IS NOT NULL

        UNION ALL

        SELECT ai.id, 'ACTION_PROPOSED', ai.created_at,
               ai.basis_case_revision, NULL::UUID, 'AGENT', 'Advocate',
               jsonb_build_object(
                   'action_intent_id', ai.id,
                   'action_type', ai.action_type,
                   'status', ai.status,
                   'recipient_masked', left(coalesce(ai.recipient, ''), 1) || '•••••'
               )
        FROM action_intents ai
        WHERE ai.tenant_id = %(tenant_id)s
          AND ai.user_id = %(user_id)s
          AND ai.case_id = %(case_id)s

        UNION ALL

        SELECT ai.id, 'ACTION_APPROVED', ai.approved_at,
               ai.basis_case_revision, NULL::UUID, 'USER', 'You',
               jsonb_build_object(
                   'action_intent_id', ai.id,
                   'action_type', ai.action_type,
                   'status', ai.status
               )
        FROM action_intents ai
        WHERE ai.tenant_id = %(tenant_id)s
          AND ai.user_id = %(user_id)s
          AND ai.case_id = %(case_id)s
          AND ai.approved_at IS NOT NULL

        UNION ALL

        SELECT ai.id, 'ACTION_REJECTED', ai.rejected_at,
               ai.basis_case_revision, NULL::UUID, 'USER', 'You',
               jsonb_build_object(
                   'action_intent_id', ai.id,
                   'action_type', ai.action_type,
                   'status', ai.status,
                   'rejection_reason', ai.rejection_reason
               )
        FROM action_intents ai
        WHERE ai.tenant_id = %(tenant_id)s
          AND ai.user_id = %(user_id)s
          AND ai.case_id = %(case_id)s
          AND ai.rejected_at IS NOT NULL

        UNION ALL

        SELECT DISTINCT e.id, 'USER_CORRECTION', e.observed_at,
               NULL::INT8, NULL::UUID, 'USER', 'You',
               jsonb_build_object(
                   'evidence_id', e.id,
                   'correction_type', e.evidence_type,
                   'statement_excerpt', left(e.normalized_text, 200)
               )
        FROM evidence_items e
        JOIN source_artifacts sa
          ON sa.tenant_id = e.tenant_id AND sa.user_id = e.user_id AND sa.id = e.artifact_id
        JOIN claims c
          ON c.tenant_id = e.tenant_id AND c.user_id = e.user_id AND c.evidence_id = e.id
        WHERE e.tenant_id = %(tenant_id)s
          AND e.user_id = %(user_id)s
          AND c.case_id = %(case_id)s
          AND sa.source_type = 'USER_CORRECTION'
    )
    SELECT e.id, e.kind, e.occurred_at, e.trace_id, e.actor_type, e.actor_label, e.detail,
           COALESCE(
               e.case_revision,
               (SELECT max(st2.case_revision)
                  FROM state_transitions st2
                 WHERE st2.tenant_id = %(tenant_id)s
                   AND st2.user_id = %(user_id)s
                   AND st2.case_id = %(case_id)s
                   AND st2.recorded_at <= e.occurred_at)
           ) AS case_revision
    FROM entries e
    WHERE e.occurred_at IS NOT NULL
      AND (cardinality(%(kinds)s::STRING[]) = 0 OR e.kind = ANY(%(kinds)s::STRING[]))
      AND (%(after_occurred_at)s::TIMESTAMPTZ IS NULL
           OR (e.occurred_at, e.id) < (%(after_occurred_at)s::TIMESTAMPTZ, %(after_id)s::UUID))
    ORDER BY e.occurred_at DESC, e.id DESC
    LIMIT %(limit)s
"""


async def list_case_timeline(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    limit: int,
    kinds: tuple[str, ...] = (),
    since_revision: int | None = None,
    after_occurred_at: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One page of the merged timeline, newest first.

    *since_revision* is applied by the caller against the projected
    ``case_revision``, because for every branch but ``state_transitions`` the
    revision is derived in the outer ``SELECT`` and is therefore not available
    to the ``WHERE`` of that same ``SELECT`` without repeating the subquery.
    Repeating it would be a second definition of "what revision was this case
    at", which is exactly the kind of duplication this package exists to
    avoid.
    """
    rows = await _fetch_all(
        conn,
        CASE_TIMELINE_SQL,
        {
            **_owner(tenant_id, user_id),
            "case_id": case_id,
            "limit": limit,
            "kinds": list(kinds),
            "after_occurred_at": after_occurred_at,
            "after_id": after_id,
        },
    )
    if since_revision is None:
        return rows
    return [row for row in rows if (row.get("case_revision") or 0) > since_revision]
