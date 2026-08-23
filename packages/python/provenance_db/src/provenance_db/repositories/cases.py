"""Reads over the case aggregate. No canonical write lives here.

Authority: ``specs/10_DATABASE_DDL.md`` section 12 (write-path ownership) and
section 13 (statement order inside the Kernel transaction).

The case aggregate is written by ``services/control_plane/app/memory_kernel/``
and by nothing else. Everything here is a read, and every read is user-scoped
by construction: the predicate is in the SQL constant, not in the caller's
discipline.

``CaseStatus`` has no ``CLOSED``
--------------------------------
The ten legal values are ``OPEN``, ``WAITING``, ``ACTIONABLE``,
``IN_PROGRESS``, ``DISPUTED``, ``BLOCKED``, ``AWAITING_USER``, ``RESOLVED``,
``REOPENED`` and ``SUPERSEDED`` — enforced by ``ck_cases_status`` in migration
``0001`` and by ``provenance_domain.enums.CaseStatus``. A "live cases"
predicate written as ``status <> 'CLOSED'`` matches **every** legal value and
returns the whole table: no error, no empty result, just a plausible-looking
list with every resolved case in it. The terminal pair is named explicitly in
:data:`OPEN_CASES_SQL` for that reason.
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
    "ALL_CASES_SQL",
    "CASE_COUNTS_SQL",
    "CASE_DETAIL_SQL",
    "CASE_INDEX_SQL",
    "CASE_REVISION_SQL",
    "CASE_SNAPSHOT_SQL",
    "CASE_TRANSITIONS_SQL",
    "OPEN_CASES_SQL",
    "TERMINAL_STATUSES",
    "get_case_counts",
    "get_case_detail",
    "get_case_revision",
    "get_case_revision_for",
    "get_case_snapshot",
    "list_case_index",
    "list_case_transitions",
    "list_cases",
    "list_open_cases",
]

#: The two statuses a case does not come back from on its own.
#: ``REOPENED`` is reachable from ``RESOLVED``, but only through the Kernel's
#: guarded transition, so a "live work" list excludes both.
TERMINAL_STATUSES: tuple[str, ...] = ("RESOLVED", "SUPERSEDED")

#: The aggregate re-read of ``10_DATABASE_DDL.md`` section 13, statement 1.
#: The Kernel issues it on its own connection inside its own transaction — a
#: retry must start from fresh reads — but the scoping predicate has one home
#: and this is it.
CASE_SNAPSHOT_SQL = """
    SELECT id, case_type, title, status, revision, reopened_count,
           attention_level, relationship_id, context_id,
           opened_at, resolved_at, last_activity_at
    FROM cases
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND id = %(case_id)s
"""

#: The revision alone. ``CANONICAL_DECISIONS.md`` -> *External action* binds an
#: approval to a case revision and a draft SHA-256, and revalidates before
#: executing; that revalidation wants one integer, not an aggregate.
CASE_REVISION_SQL = """
    SELECT revision
    FROM cases
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND id = %(case_id)s
"""

OPEN_CASES_SQL = """
    SELECT id, case_type, title, status, revision, attention_level,
           reopened_count, opened_at, last_activity_at
    FROM cases
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND status NOT IN ('RESOLVED', 'SUPERSEDED')
    ORDER BY last_activity_at DESC
    LIMIT %(limit)s
"""

ALL_CASES_SQL = """
    SELECT id, case_type, title, status, revision, attention_level,
           reopened_count, opened_at, resolved_at, last_activity_at
    FROM cases
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
    ORDER BY last_activity_at DESC
    LIMIT %(limit)s
"""


async def get_case_snapshot(
    conn: AsyncConnection[Any],
    principal: Principal,
    case_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> dict[str, Any] | None:
    """One case, scoped to *principal*'s tenant and user.

    ``None`` covers both "no such case" and "that case belongs to somebody
    else". Distinguishing them would turn this into an existence oracle over
    the whole tenant space, which is a worse leak than the one it would fix.
    """
    return await _fetch_one(
        conn,
        CASE_SNAPSHOT_SQL,
        {**_scope(principal), "case_id": case_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


async def get_case_revision(
    conn: AsyncConnection[Any],
    principal: Principal,
    case_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> int | None:
    """The current revision of one case, or ``None``.

    The revision is what an approval is bound to and what execution
    revalidates against, so it gets its own entry point rather than being
    picked out of a snapshot at three call sites that could each drift.
    """
    row = await _fetch_one(
        conn,
        CASE_REVISION_SQL,
        {**_scope(principal), "case_id": case_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )
    return None if row is None else int(row["revision"])


async def list_open_cases(
    conn: AsyncConnection[Any],
    principal: Principal,
    limit: int = 50,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """Every non-terminal case for one user, most recently active first."""
    return await _fetch_all(
        conn,
        OPEN_CASES_SQL,
        {**_scope(principal), "limit": limit},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


async def list_cases(
    conn: AsyncConnection[Any],
    principal: Principal,
    limit: int = 100,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """Every case for one user, live and resolved alike.

    A separate entry point from :func:`list_open_cases` rather than a boolean
    flag on it. The case index and the work queue are different questions with
    different default orderings, and a flag is how one of them quietly acquires
    the other's semantics.
    """
    return await _fetch_all(
        conn,
        ALL_CASES_SQL,
        {**_scope(principal), "limit": limit},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


# ===========================================================================
# The API read models (T8.9)
# ===========================================================================
#
# The statements below take an explicit ``(tenant_id, user_id)`` pair rather
# than a ``Principal``, because the control plane's read adapters hold an
# ``OwnerScope`` -- see ``_execute._owner`` for why fabricating a Principal
# there would be the worse shape. Everything else is unchanged: the scoping
# predicate is in the SQL, and there is no argument through which a caller can
# name a different owner than the one the route resolved.

#: Section 8.8's index. ``attention_reason_codes`` is **derived**, not stored:
#: ``cases`` has no such column, and section 8.4 defines the field as what the
#: deterministic headline template keys on. Each flag below is an ``EXISTS``
#: over the ledger that would make the case demand attention, so the chips a
#: user sees are the current state of those ledgers rather than a denormalised
#: copy that goes stale between writes.
#:
#: ``counterparty_display_name`` comes through ``relationships`` and is joined
#: on ``tenant_id`` alone on the counterparty side, because ``counterparties``
#: is tenant-scoped and has no ``user_id``: two users in one tenant share the
#: row for Northline Fiber.
CASE_INDEX_SQL = """
    SELECT k.id AS case_id, k.title, k.status, k.revision, k.attention_level,
           k.case_type, k.relationship_id, k.context_id, k.opened_at,
           k.resolved_at, k.last_activity_at, k.reopened_count,
           cp.display_name AS counterparty_display_name,
           cp.kind AS counterparty_kind,
           EXISTS (SELECT 1 FROM conflicts cf
                    WHERE cf.tenant_id = k.tenant_id AND cf.user_id = k.user_id
                      AND cf.case_id = k.id
                      AND cf.status IN ('OPEN', 'NEEDS_HUMAN')) AS has_open_conflict,
           EXISTS (SELECT 1 FROM commitments cm
                    WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
                      AND cm.case_id = k.id
                      AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
                      AND cm.due_at IS NOT NULL
                      AND cm.due_at < %(now)s::TIMESTAMPTZ) AS has_overdue_commitment,
           EXISTS (SELECT 1 FROM commitments cm
                    WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
                      AND cm.case_id = k.id
                      AND cm.status = 'PARTIAL') AS has_partial_commitment,
           EXISTS (SELECT 1 FROM prospective_triggers t
                    WHERE t.tenant_id = k.tenant_id AND t.user_id = k.user_id
                      AND t.case_id = k.id
                      AND t.state = 'FIRED') AS has_fired_trigger,
           EXISTS (SELECT 1 FROM action_intents ai
                    WHERE ai.tenant_id = k.tenant_id AND ai.user_id = k.user_id
                      AND ai.case_id = k.id
                      AND ai.status IN ('PROPOSED', 'NEEDS_REVIEW')) AS has_pending_action
    FROM cases k
    JOIN relationships r
      ON r.tenant_id = k.tenant_id AND r.user_id = k.user_id AND r.id = k.relationship_id
    JOIN counterparties cp
      ON cp.tenant_id = k.tenant_id AND cp.id = r.counterparty_id
    WHERE k.tenant_id = %(tenant_id)s
      AND k.user_id = %(user_id)s
      AND (cardinality(%(statuses)s::STRING[]) = 0
           OR k.status = ANY(%(statuses)s::STRING[]))
      AND (%(relationship_id)s::UUID IS NULL OR k.relationship_id = %(relationship_id)s::UUID)
      AND (%(context_id)s::UUID IS NULL OR k.context_id = %(context_id)s::UUID)
      AND (NOT %(attention_only)s::BOOL OR k.attention_level <> 'NONE')
      AND (cardinality(%(case_types)s::STRING[]) = 0
           OR k.case_type = ANY(%(case_types)s::STRING[]))
      AND (%(after_last_activity_at)s::TIMESTAMPTZ IS NULL
           OR (k.last_activity_at, k.id)
               < (%(after_last_activity_at)s::TIMESTAMPTZ, %(after_id)s::UUID))
    ORDER BY k.last_activity_at DESC, k.id DESC
    LIMIT %(limit)s
"""

#: Section 8.9. The same derived attention flags, plus the relationship,
#: counterparty and context blocks the detail screen renders as a header.
CASE_DETAIL_SQL = """
    SELECT k.id AS case_id, k.title, k.status, k.revision, k.attention_level,
           k.case_type, k.relationship_id, k.context_id, k.opened_at,
           k.resolved_at, k.last_activity_at, k.reopened_count,
           r.label AS relationship_label, r.status AS relationship_status,
           r.relationship_type,
           cp.id AS counterparty_id, cp.display_name AS counterparty_display_name,
           cp.kind AS counterparty_kind, cp.canonical_domain,
           x.title AS context_title, x.context_type,
           EXISTS (SELECT 1 FROM conflicts cf
                    WHERE cf.tenant_id = k.tenant_id AND cf.user_id = k.user_id
                      AND cf.case_id = k.id
                      AND cf.status IN ('OPEN', 'NEEDS_HUMAN')) AS has_open_conflict,
           EXISTS (SELECT 1 FROM commitments cm
                    WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
                      AND cm.case_id = k.id
                      AND cm.status IN ('PROPOSED', 'ACTIVE', 'PARTIAL', 'DISPUTED')
                      AND cm.due_at IS NOT NULL
                      AND cm.due_at < %(now)s::TIMESTAMPTZ) AS has_overdue_commitment,
           EXISTS (SELECT 1 FROM commitments cm
                    WHERE cm.tenant_id = k.tenant_id AND cm.user_id = k.user_id
                      AND cm.case_id = k.id
                      AND cm.status = 'PARTIAL') AS has_partial_commitment,
           EXISTS (SELECT 1 FROM prospective_triggers t
                    WHERE t.tenant_id = k.tenant_id AND t.user_id = k.user_id
                      AND t.case_id = k.id
                      AND t.state = 'FIRED') AS has_fired_trigger,
           EXISTS (SELECT 1 FROM action_intents ai
                    WHERE ai.tenant_id = k.tenant_id AND ai.user_id = k.user_id
                      AND ai.case_id = k.id
                      AND ai.status IN ('PROPOSED', 'NEEDS_REVIEW')) AS has_pending_action
    FROM cases k
    JOIN relationships r
      ON r.tenant_id = k.tenant_id AND r.user_id = k.user_id AND r.id = k.relationship_id
    JOIN counterparties cp
      ON cp.tenant_id = k.tenant_id AND cp.id = r.counterparty_id
    LEFT JOIN contexts x
      ON x.tenant_id = k.tenant_id AND x.user_id = k.user_id AND x.id = k.context_id
    WHERE k.tenant_id = %(tenant_id)s
      AND k.user_id = %(user_id)s
      AND k.id = %(case_id)s
"""

#: Section 8.9's ``counts`` block. Four scalars in one round trip; four
#: separate queries would be four chances to forget an owner predicate.
#: ``evidence_items`` reaches the case through ``claims`` -- it carries no
#: ``case_id`` of its own -- and the ``DISTINCT`` is what stops one invoice
#: that produced three claims from being counted three times.
CASE_COUNTS_SQL = """
    SELECT
      (SELECT count(DISTINCT c.evidence_id) FROM claims c
        WHERE c.tenant_id = %(tenant_id)s AND c.user_id = %(user_id)s
          AND c.case_id = %(case_id)s) AS evidence_items,
      (SELECT count(*) FROM claims c
        WHERE c.tenant_id = %(tenant_id)s AND c.user_id = %(user_id)s
          AND c.case_id = %(case_id)s) AS claims,
      (SELECT count(*) FROM beliefs b
        WHERE b.tenant_id = %(tenant_id)s AND b.user_id = %(user_id)s
          AND b.case_id = %(case_id)s) AS beliefs,
      (SELECT count(*) FROM state_transitions st
        WHERE st.tenant_id = %(tenant_id)s AND st.user_id = %(user_id)s
          AND st.case_id = %(case_id)s) AS state_transitions
"""

#: Section 8.11's ``state_transitions`` block, newest revision first.
CASE_TRANSITIONS_SQL = """
    SELECT st.id, st.case_revision, st.transition_type, st.subject_kind,
           st.subject_id, st.from_state, st.to_state, st.reason_code,
           st.kernel_decision_id, st.trace_id, st.recorded_at
    FROM state_transitions st
    WHERE st.tenant_id = %(tenant_id)s
      AND st.user_id = %(user_id)s
      AND st.case_id = %(case_id)s
    ORDER BY st.case_revision DESC, st.recorded_at DESC
    LIMIT %(limit)s
"""


async def list_case_index(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    statuses: tuple[str, ...] = (),
    case_types: tuple[str, ...] = (),
    relationship_id: uuid.UUID | None = None,
    context_id: uuid.UUID | None = None,
    attention_only: bool = False,
    now: Any = None,
    after_last_activity_at: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One page of the case index, most recently active first.

    Every filter is a bound parameter and every one of them *narrows* inside
    the owner predicate, which is unconditional and sits above all of them. A
    filter can therefore never widen the result set beyond one user, which is
    what makes it safe for the route to pass request data straight through.
    """
    return await _fetch_all(
        conn,
        CASE_INDEX_SQL,
        {
            **_owner(tenant_id, user_id),
            "limit": limit,
            "statuses": list(statuses),
            "case_types": list(case_types),
            "relationship_id": relationship_id,
            "context_id": context_id,
            "attention_only": attention_only,
            "now": now,
            "after_last_activity_at": after_last_activity_at,
            "after_id": after_id,
        },
    )


async def get_case_detail(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    now: Any = None,
) -> dict[str, Any] | None:
    """One case with its relationship, counterparty and context header.

    *now* is the request clock, bound as a parameter rather than read as
    ``now()`` inside the statement. "Overdue" is a comparison against a clock,
    and a demo pinned to ``2026-09-18`` must get the same answer from the case
    header, the commitment list and the trigger predicate -- three surfaces
    reading three different clocks is how "95 days overdue" becomes three
    numbers.
    """
    return await _fetch_one(
        conn, CASE_DETAIL_SQL, {**_owner(tenant_id, user_id), "case_id": case_id, "now": now}
    )


async def get_case_revision_for(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
) -> int | None:
    """:func:`get_case_revision`, for a caller holding an owner pair.

    The same statement -- :data:`CASE_REVISION_SQL` -- so there is exactly one
    definition of "the revision of this case, for this owner". The revision is
    what an approval is bound to and what execution revalidates against, and
    two entry points reading two statements is how those two numbers come to
    disagree.
    """
    row = await _fetch_one(
        conn, CASE_REVISION_SQL, {**_owner(tenant_id, user_id), "case_id": case_id}
    )
    return None if row is None else int(row["revision"])


async def get_case_counts(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
) -> dict[str, Any]:
    """Section 8.9's four counts, computed rather than stored."""
    row = await _fetch_one(
        conn, CASE_COUNTS_SQL, {**_owner(tenant_id, user_id), "case_id": case_id}
    )
    return row or {"evidence_items": 0, "claims": 0, "beliefs": 0, "state_transitions": 0}


async def list_case_transitions(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The canonical transition ledger for one case, newest revision first."""
    return await _fetch_all(
        conn,
        CASE_TRANSITIONS_SQL,
        {**_owner(tenant_id, user_id), "case_id": case_id, "limit": limit},
    )
