"""Reads over the action plane: intents and their executions.

Authority: ``specs/10_DATABASE_DDL.md`` section 12 (write-path ownership) and
``specs/13_RETRIEVAL_SPEC.md`` section 19 (module layout). Split by domain,
never by table: a repository spanning two aggregates hides a transaction
boundary.

``action_intents`` and ``action_executions`` are written by the control
plane and never by the Kernel — the Kernel can neither send anything nor
mint an approval (``10_DATABASE_DDL.md`` section 15). Those writes arrive
in Phase 9; this module reads.

The two revisions, and why both are returned
---------------------------------------------
``CANONICAL_DECISIONS.md`` -> *External action*: draft, validate grounding,
create intent, human approve, **bind approval to case revision and draft
SHA-256**, revalidate, execute idempotently. The revalidation step compares two
numbers: the revision the approval was bound to (``basis_case_revision``, on
the intent) and the revision the case is at now. A read that returned only the
first would leave the second to a separate query at every call site, and a
stale-approval check assembled from two independent reads is a stale-approval
check that one caller will forget to assemble.

So the statement joins ``cases`` and returns ``case_revision_now`` beside
``basis_case_revision``. The comparison is the caller's — this is a read — but
it can no longer be made against a number nobody fetched.
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
    "ACTION_INTENT_SQL",
    "CASE_ACTION_INTENTS_SQL",
    "LATEST_ACTION_INTENT_FOR_CASE_SQL",
    "PENDING_ACTION_COUNT_SQL",
    "USER_ACTION_INTENTS_SQL",
    "count_pending_action_intents",
    "get_action_intent",
    "get_action_intent_detail",
    "get_latest_action_intent_for_case",
    "list_action_intents_for_case",
    "list_action_intents_for_user",
]

#: ``draft_sha256`` and ``approval_draft_sha256`` are both returned. They are
#: the same 32 bytes on a healthy intent and their divergence is the entire
#: point: an approval is bound to an exact draft, so an intent whose draft
#: changed after approval must be executable by nobody. Returning only one of
#: them would make that comparison impossible at the layer that has to make it.
ACTION_INTENT_SQL = """
    SELECT ai.id, ai.case_id, ai.action_type, ai.recipient,
           ai.draft_payload, ai.draft_sha256, ai.rationale,
           ai.supporting_belief_versions, ai.basis_case_revision,
           ai.status, ai.risk_tier, ai.created_by_agent_run_id,
           ai.approved_by_user_id, ai.approved_at, ai.approval_draft_sha256,
           ai.rejected_at, ai.rejection_reason, ai.idempotency_key,
           ai.created_at, ai.updated_at,
           c.revision AS case_revision_now,
           c.status   AS case_status_now
    FROM action_intents ai
    JOIN cases c
      ON c.tenant_id = ai.tenant_id
     AND c.user_id = ai.user_id
     AND c.id = ai.case_id
    WHERE ai.tenant_id = %(tenant_id)s
      AND ai.user_id = %(user_id)s
      AND ai.id = %(intent_id)s
"""


async def get_action_intent(
    conn: AsyncConnection[Any],
    principal: Principal,
    intent_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> dict[str, Any] | None:
    """One action intent, with the case revision its approval was bound to.

    Returns ``None`` when no such intent exists **or** when it belongs to
    another user. The two are not distinguished, for the same reason the case
    reads do not distinguish them: an approval id is guessable enough that a
    404/403 split is an enumeration oracle.
    """
    return await _fetch_one(
        conn,
        ACTION_INTENT_SQL,
        {**_scope(principal), "intent_id": intent_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


# ===========================================================================
# The API read models (T8.9)
# ===========================================================================

#: Section 8.23. ``current_case_revision`` travels beside
#: ``basis_case_revision`` for the reason stated in the module docstring: a
#: stale-approval check assembled from two independent reads is one a caller
#: will forget to assemble. ``is_stale`` is the comparison of the two and is
#: made by the adapter, from these two columns, on every row.
#:
#: ``draft_payload`` is **not** projected here. The index renders a subject
#: preview and a masked recipient; shipping the whole drafted letter for
#: twenty-five rows would put an unsent dispute into a list response.
USER_ACTION_INTENTS_SQL = """
    SELECT ai.id AS action_intent_id, ai.case_id, ai.action_type, ai.status,
           ai.recipient, ai.basis_case_revision, ai.risk_tier,
           ai.created_by_agent_run_id, ai.created_at, ai.updated_at,
           ai.approved_at, ai.rejected_at, ai.rejection_reason,
           ai.draft_payload->>'subject' AS subject_preview,
           k.revision AS current_case_revision, k.title AS case_title,
           cp.display_name AS counterparty_display_name
    FROM action_intents ai
    JOIN cases k
      ON k.tenant_id = ai.tenant_id AND k.user_id = ai.user_id AND k.id = ai.case_id
    JOIN relationships r
      ON r.tenant_id = k.tenant_id AND r.user_id = k.user_id AND r.id = k.relationship_id
    JOIN counterparties cp
      ON cp.tenant_id = k.tenant_id AND cp.id = r.counterparty_id
    WHERE ai.tenant_id = %(tenant_id)s
      AND ai.user_id = %(user_id)s
      AND (%(case_id)s::UUID IS NULL OR ai.case_id = %(case_id)s::UUID)
      AND (cardinality(%(statuses)s::STRING[]) = 0
           OR ai.status = ANY(%(statuses)s::STRING[]))
      AND (cardinality(%(action_types)s::STRING[]) = 0
           OR ai.action_type = ANY(%(action_types)s::STRING[]))
      AND (%(after_created_at)s::TIMESTAMPTZ IS NULL
           OR (ai.created_at, ai.id) < (%(after_created_at)s::TIMESTAMPTZ, %(after_id)s::UUID))
    ORDER BY ai.created_at DESC, ai.id DESC
    LIMIT %(limit)s
"""

#: Section 8.9's ``latest_action_intent`` block: one row, the newest.
LATEST_ACTION_INTENT_FOR_CASE_SQL = """
    SELECT ai.id AS action_intent_id, ai.action_type, ai.status,
           ai.basis_case_revision, ai.created_at
    FROM action_intents ai
    WHERE ai.tenant_id = %(tenant_id)s
      AND ai.user_id = %(user_id)s
      AND ai.case_id = %(case_id)s
    ORDER BY ai.created_at DESC, ai.id DESC
    LIMIT 1
"""

#: Section 8.11's ``actions_relying_on_this_state``: every intent bound to
#: this case, with the revision it was bound to. ``still_current`` is the
#: comparison, made by the adapter.
CASE_ACTION_INTENTS_SQL = """
    SELECT ai.id AS action_intent_id, ai.action_type, ai.status,
           ai.basis_case_revision, ai.supporting_belief_versions, ai.created_at,
           k.revision AS current_case_revision
    FROM action_intents ai
    JOIN cases k
      ON k.tenant_id = ai.tenant_id AND k.user_id = ai.user_id AND k.id = ai.case_id
    WHERE ai.tenant_id = %(tenant_id)s
      AND ai.user_id = %(user_id)s
      AND ai.case_id = %(case_id)s
    ORDER BY ai.created_at DESC, ai.id DESC
"""

#: The dashboard's ``action_intents_pending`` tile. ``PROPOSED`` and
#: ``NEEDS_REVIEW`` are the two states waiting on a human; an ``APPROVED``
#: intent is waiting on the executor, which is a different queue.
PENDING_ACTION_COUNT_SQL = """
    SELECT count(*) AS action_intents_pending
    FROM action_intents ai
    WHERE ai.tenant_id = %(tenant_id)s
      AND ai.user_id = %(user_id)s
      AND ai.status IN ('PROPOSED', 'NEEDS_REVIEW')
"""


async def list_action_intents_for_user(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    case_id: uuid.UUID | None = None,
    statuses: tuple[str, ...] = (),
    action_types: tuple[str, ...] = (),
    after_created_at: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One page of action intents, newest first."""
    return await _fetch_all(
        conn,
        USER_ACTION_INTENTS_SQL,
        {
            **_owner(tenant_id, user_id),
            "limit": limit,
            "case_id": case_id,
            "statuses": list(statuses),
            "action_types": list(action_types),
            "after_created_at": after_created_at,
            "after_id": after_id,
        },
    )


async def get_action_intent_detail(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    intent_id: uuid.UUID,
) -> dict[str, Any] | None:
    """:func:`get_action_intent`, for a caller holding an owner pair.

    The same statement, so the two digests an execution compares --
    ``draft_sha256`` and ``approval_draft_sha256`` -- are projected by one
    definition. An intent whose draft changed after approval must be
    executable by nobody, and that check is only possible if both digests come
    back from the same read.
    """
    return await _fetch_one(
        conn, ACTION_INTENT_SQL, {**_owner(tenant_id, user_id), "intent_id": intent_id}
    )


async def get_latest_action_intent_for_case(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
) -> dict[str, Any] | None:
    """The newest action intent on one case, or ``None``."""
    return await _fetch_one(
        conn,
        LATEST_ACTION_INTENT_FOR_CASE_SQL,
        {**_owner(tenant_id, user_id), "case_id": case_id},
    )


async def list_action_intents_for_case(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Every action intent resting on one case's state."""
    return await _fetch_all(
        conn, CASE_ACTION_INTENTS_SQL, {**_owner(tenant_id, user_id), "case_id": case_id}
    )


async def count_pending_action_intents(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    """How many drafts are waiting on this user's decision."""
    row = await _fetch_one(conn, PENDING_ACTION_COUNT_SQL, _owner(tenant_id, user_id))
    return int(row["action_intents_pending"]) if row else 0
