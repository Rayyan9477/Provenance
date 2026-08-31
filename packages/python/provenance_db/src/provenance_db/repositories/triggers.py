"""Reads over prospective memory: armed triggers and their predicates.

Authority: ``specs/10_DATABASE_DDL.md`` section 12 (write-path ownership) and
``specs/13_RETRIEVAL_SPEC.md`` section 19 (module layout). Split by domain,
never by table: a repository spanning two aggregates hides a transaction
boundary.

A scheduler event is never truth. The wake path re-evaluates the predicate
against current canonical state, which is why this module reads triggers
and does not fire them. ``basis_case_revision`` is returned for the same
reason: a trigger armed against revision 7 that wakes at revision 9 has to
know it is looking at a case that moved, and the reason code for that is
``STALE_SCHEDULE_GENERATION`` rather than a fire.
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
    "ARMED_TRIGGERS_FOR_CASE_SQL",
    "NEXT_TRIGGER_FOR_CASE_SQL",
    "TRIGGER_COUNTS_SQL",
    "USER_TRIGGERS_SQL",
    "get_armed_triggers_for_case",
    "get_next_trigger_for_case",
    "get_trigger_counts",
    "list_triggers_for_user",
]

#: ``state = 'ARMED'`` and not ``state <> 'FIRED'``: ``DISARMED`` and
#: ``EXPIRED`` are also not armed, and a negated predicate over a four-member
#: vocabulary lets two of them through. The same shape of mistake as
#: ``retraction_status <> 'RETRACTED'`` (``13_RETRIEVAL_SPEC.md`` section 13.1),
#: and it is worth naming twice because it reads correct both times.
ARMED_TRIGGERS_FOR_CASE_SQL = """
    SELECT id, case_id, trigger_type, predicate_ast, not_before, expires_at,
           state, evaluation_version, basis_case_revision, schedule_name,
           last_evaluated_at, last_result, last_reason_code, fired_at,
           created_at, updated_at
    FROM prospective_triggers
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND case_id = %(case_id)s
      AND state = 'ARMED'
    ORDER BY not_before ASC NULLS LAST
"""


async def get_armed_triggers_for_case(
    conn: AsyncConnection[Any],
    principal: Principal,
    case_id: uuid.UUID,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """Every armed trigger on one case, with its wake time.

    ``expires_at`` is returned alongside ``not_before``: a trigger past its
    expiry is still ``ARMED`` in the column until something evaluates it, and a
    caller that read only the wake time would treat an expired trigger as
    pending.
    """
    return await _fetch_all(
        conn,
        ARMED_TRIGGERS_FOR_CASE_SQL,
        {**_scope(principal), "case_id": case_id},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )


# ===========================================================================
# The API read models (T8.9)
# ===========================================================================

#: Section 8.16 — "prospective memory, visible", and the second reveal of the
#: hero demo. Every state, not just ``ARMED``: the point of the screen is that
#: a trigger **fired on its own**, and a list that showed only what is still
#: armed would hide the one row the demo exists to show.
#:
#: ``predicate_ast`` is returned verbatim. Section 8.16: the grammar is a
#: closed whitelist over whitelisted projection fields, evaluated by
#: deterministic Python, and it contains no executable code and no PII.
#: ``predicate_summary`` is rendered by a deterministic template in the
#: adapter, never by a model.
USER_TRIGGERS_SQL = """
    SELECT t.id AS trigger_id, t.case_id, t.trigger_type, t.predicate_ast,
           t.not_before, t.expires_at, t.state, t.evaluation_version,
           t.basis_case_revision, t.schedule_name, t.last_evaluated_at,
           t.last_result, t.last_reason_code, t.fired_at,
           t.created_at, t.updated_at,
           k.title AS case_title, k.revision AS case_revision_now
    FROM prospective_triggers t
    JOIN cases k
      ON k.tenant_id = t.tenant_id AND k.user_id = t.user_id AND k.id = t.case_id
    WHERE t.tenant_id = %(tenant_id)s
      AND t.user_id = %(user_id)s
      AND (%(case_id)s::UUID IS NULL OR t.case_id = %(case_id)s::UUID)
      AND (cardinality(%(states)s::STRING[]) = 0 OR t.state = ANY(%(states)s::STRING[]))
      AND (cardinality(%(trigger_types)s::STRING[]) = 0
           OR t.trigger_type = ANY(%(trigger_types)s::STRING[]))
      -- Keyset over `ASC NULLS LAST`, which the naive form gets wrong twice.
      --
      -- It was:
      --     AND (%(after_not_before)s IS NULL
      --          OR (t.not_before, t.id) > (%(after_not_before)s, %(after_id)s))
      --
      -- With a cursor in the non-null section, a row whose not_before IS NULL makes
      -- the row comparison evaluate to NULL rather than true, so the entire
      -- NULLS-LAST tail was unreachable -- silently, with has_more already
      -- false by then. And a cursor minted FROM that tail carries a null sort
      -- value, which made the first branch true and returned the whole list
      -- again from the top.
      --
      -- `after_id` is the discriminator: it is NULL only when there is no
      -- cursor at all, whereas the sort value is legitimately NULL inside the
      -- tail. Past the non-null section every remaining row is in the tail, so
      -- the ordering is: rest of the non-null section, then all nulls by id.
      AND (%(after_id)s::UUID IS NULL
           OR (CASE WHEN %(after_not_before)s::TIMESTAMPTZ IS NOT NULL
                    THEN t.not_before IS NULL
                         OR (t.not_before, t.id)
                            > (%(after_not_before)s::TIMESTAMPTZ, %(after_id)s::UUID)
                    ELSE t.not_before IS NULL AND t.id > %(after_id)s::UUID
               END))
    ORDER BY t.not_before ASC NULLS LAST, t.id ASC
    LIMIT %(limit)s
"""

#: Section 8.9's ``next_trigger``: the soonest armed wake on one case.
#:
#: ``state = 'ARMED'`` and not ``state <> 'FIRED'``, for the reason
#: :data:`ARMED_TRIGGERS_FOR_CASE_SQL` gives at length: a negated predicate
#: over a four-member vocabulary lets ``DISARMED`` and ``EXPIRED`` through, and
#: the case header would then promise a wake that will never happen.
NEXT_TRIGGER_FOR_CASE_SQL = """
    SELECT t.id AS trigger_id, t.trigger_type, t.state, t.not_before,
           t.expires_at, t.basis_case_revision, t.evaluation_version
    FROM prospective_triggers t
    WHERE t.tenant_id = %(tenant_id)s
      AND t.user_id = %(user_id)s
      AND t.case_id = %(case_id)s
      AND t.state = 'ARMED'
    ORDER BY t.not_before ASC NULLS LAST, t.id ASC
    LIMIT 1
"""

#: The dashboard's two trigger tiles. ``triggers_fired_unhandled`` counts
#: triggers that fired and whose case has not moved since: a fired trigger on
#: a case that has already advanced past ``basis_case_revision`` has been
#: responded to, and counting it again would keep a resolved alarm on the home
#: screen forever.
TRIGGER_COUNTS_SQL = """
    SELECT
      (SELECT count(*) FROM prospective_triggers t
        WHERE t.tenant_id = %(tenant_id)s AND t.user_id = %(user_id)s
          AND t.state = 'ARMED') AS triggers_armed,
      (SELECT count(*) FROM prospective_triggers t
        JOIN cases k
          ON k.tenant_id = t.tenant_id AND k.user_id = t.user_id AND k.id = t.case_id
        WHERE t.tenant_id = %(tenant_id)s AND t.user_id = %(user_id)s
          AND t.state = 'FIRED'
          AND k.status NOT IN ('RESOLVED', 'SUPERSEDED')) AS triggers_fired_unhandled
"""


async def list_triggers_for_user(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    case_id: uuid.UUID | None = None,
    states: tuple[str, ...] = (),
    trigger_types: tuple[str, ...] = (),
    after_not_before: Any = None,
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """One page of prospective triggers, soonest wake first."""
    return await _fetch_all(
        conn,
        USER_TRIGGERS_SQL,
        {
            **_owner(tenant_id, user_id),
            "limit": limit,
            "case_id": case_id,
            "states": list(states),
            "trigger_types": list(trigger_types),
            "after_not_before": after_not_before,
            "after_id": after_id,
        },
    )


async def get_next_trigger_for_case(
    conn: AsyncConnection[Any],
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
) -> dict[str, Any] | None:
    """The soonest armed wake on one case, or ``None``."""
    return await _fetch_one(
        conn, NEXT_TRIGGER_FOR_CASE_SQL, {**_owner(tenant_id, user_id), "case_id": case_id}
    )


async def get_trigger_counts(
    conn: AsyncConnection[Any], *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    """The dashboard's ``triggers_armed`` and ``triggers_fired_unhandled``."""
    row = await _fetch_one(conn, TRIGGER_COUNTS_SQL, _owner(tenant_id, user_id))
    return row or {"triggers_armed": 0, "triggers_fired_unhandled": 0}
