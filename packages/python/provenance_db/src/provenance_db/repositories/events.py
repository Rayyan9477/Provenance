"""Reads over the outbox and the processed-event ledger.

Authority: ``specs/10_DATABASE_DDL.md`` section 12 (write-path ownership) and
``specs/13_RETRIEVAL_SPEC.md`` section 19 (module layout). Split by domain,
never by table: a repository spanning two aggregates hides a transaction
boundary.

The outbox row is written by the Kernel in the same transaction as the
state it describes. The dispatcher's ``UPDATE outbox_events SET status``
is the one non-canonical write against a canonical table
(``10_DATABASE_DDL.md`` section 12); it arrives with the dispatcher in
Phase 10, and this module is reads only.

Why ``FAILED_RETRYABLE`` is in the claim set and ``DISPATCHING`` is not
-----------------------------------------------------------------------
``outbox_events.status`` is one of ``PENDING``, ``DISPATCHING``,
``DISPATCHED``, ``FAILED_RETRYABLE`` and ``DEAD``. Undispatched work is the
first and the fourth: a row that failed retryably is still owed and its
``next_attempt_at`` is when it is owed again. ``DISPATCHING`` is deliberately
excluded — it means another dispatcher has it in flight, and a claim query that
returned it would produce exactly the duplicate publish the outbox pattern
exists to avoid. ``DEAD`` is excluded because ``attempt_count`` hit its cap of
five; that is an operator's problem, not a retry's.

``next_attempt_at <= now()`` is in the SQL rather than in the caller. Without
it a backoff is advisory: the dispatcher reads the row it just failed on,
immediately, and the exponential backoff recorded in the column never happens.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection

from provenance_contracts.identity import Principal
from provenance_db import retry
from provenance_db.repositories._execute import _fetch_all, _scope

__all__ = ["UNDISPATCHED_OUTBOX_SQL", "get_undispatched_outbox_events"]

UNDISPATCHED_OUTBOX_SQL = """
    SELECT id, aggregate_type, aggregate_id, aggregate_version, event_type,
           payload_version, payload, trace_id, causation_id, correlation_id,
           status, attempt_count, next_attempt_at, last_error,
           occurred_at, created_at
    FROM outbox_events
    WHERE tenant_id = %(tenant_id)s
      AND user_id = %(user_id)s
      AND status IN ('PENDING', 'FAILED_RETRYABLE')
      AND next_attempt_at <= now()
    ORDER BY occurred_at ASC, aggregate_version ASC
    LIMIT %(limit)s
"""


async def get_undispatched_outbox_events(
    conn: AsyncConnection[Any],
    principal: Principal,
    limit: int = 100,
    *,
    policy: retry.RetryPolicy = retry.DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: retry.Jitter | None = None,
) -> list[dict[str, Any]]:
    """Outbox rows the dispatcher has not yet published, oldest first.

    Ordered by ``occurred_at`` and then ``aggregate_version``. The timestamp
    alone is not a total order — several events from one Kernel transaction
    share it — and publishing ``case.state_changed.v1`` for revision 9 before
    revision 8 is a consumer bug that looks like a producer bug.

    This read is user-scoped like every other read in this package, which means
    a dispatcher sweeping the whole table drives it per principal rather than
    globally. That is the intended shape: an unscoped sweep is one missing
    ``WHERE`` away from publishing one tenant's events into another's stream.
    """
    return await _fetch_all(
        conn,
        UNDISPATCHED_OUTBOX_SQL,
        {**_scope(principal), "limit": limit},
        policy=policy,
        sleep=sleep,
        rng=rng,
    )
