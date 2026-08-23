"""``OutboxStore`` and ``ProcessedEventLedger`` over a live connection — T10.5.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` §9.12 (the sweep), §9.13 (consumer intake) and
  §13 (the dispatcher state machine).
- ``db/migrations/versions/0008_events_infrastructure.py`` — ``outbox_events``,
  ``processed_events`` and every CHECK this module has to satisfy.
- ``services/control_plane/app/events/dispatcher.py`` — the five statements the
  outbox store issues. They are **imported**, not copied.

Why the statements are imported rather than restated
-----------------------------------------------------
``dispatcher.py`` declares ``CLAIM_SQL``, ``MARK_DISPATCHED_SQL``,
``MARK_FAILED_SQL``, ``MARK_DEAD_SQL``, ``RECLAIM_EXPIRED_SQL`` and
``REPLAY_SQL`` beside the state machine that decides when each one runs, and
``test_the_dispatcher_only_ever_updates_status_bookkeeping`` holds them against
that source. A second copy here would be a second place a status value could
drift, and ``tools/write_path_lint`` would count each copy as another canonical
write statement — which is exactly the signal that must stay meaningful.

The one canonical write, and its exact boundary
------------------------------------------------
``UPDATE outbox_events SET status = ...`` is the single enumerated exception to
the Kernel being the sole canonical writer (rule ``W5``): it is status
bookkeeping about a row the Kernel already wrote and it carries no domain
meaning. This module may never **author** an event. ``processed_events`` is not
canonical at all — ``pv_app_reader_writer`` owns it outright — so the dedupe
insert is an ordinary write and an exception to nothing.

Why the ledger takes a transaction and the outbox does not
------------------------------------------------------------
§12's dedupe row and the effect it guards commit or roll back **together**, so
:class:`SqlConsumerUnitOfWork` opens one transaction and hands the ledger out
inside it. The dispatcher is the opposite case: each claim, publish and mark is
its own committed step, because a publish that succeeded and a mark that was
never committed must leave the row reclaimable rather than lost.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Final

from psycopg import errors as pgerr

from services.control_plane.app.events.consumer import DuplicateEventDeliveryError
from services.control_plane.app.events.dispatcher import (
    CLAIM_SQL,
    MARK_DEAD_SQL,
    MARK_DISPATCHED_SQL,
    MARK_FAILED_SQL,
    RECLAIM_EXPIRED_SQL,
    REPLAY_SQL,
    OutboxStore,
)
from services.control_plane.app.triggers.store import ConnectionSource

__all__ = [
    "OLDEST_PENDING_SQL",
    "RECORD_PROCESSED_SQL",
    "SqlConsumerUnitOfWork",
    "SqlOutboxStore",
    "SqlProcessedEventLedger",
]

#: §12. The dedupe row goes in **before** the handler runs, so the uniqueness
#: conflict is taken before any work is done. ``pk_processed_events`` is
#: ``(consumer_name, event_id)``; the ``23505`` it raises under concurrency is
#: the mechanism, not an accident, which is why this is an INSERT and not a
#: read-then-write.
#:
#: ``processed_events`` is not in ``write_path_lint.CANONICAL_TABLES``: DDL
#: section 12 gives it to ``pv_app_reader_writer`` outright, because it is a
#: consumer's own bookkeeping about deliveries rather than a statement about the
#: user's record.
RECORD_PROCESSED_SQL: Final[str] = """
INSERT INTO processed_events (consumer_name, event_id, tenant_id, user_id, result_hash)
VALUES (%(consumer_name)s, %(event_id)s, %(tenant_id)s, %(user_id)s, %(result_hash)s)
"""

#: Section 9.12's ``oldest_pending_age_seconds``, measured against the same
#: instant the sweep used rather than against a second ``now()``. The filter
#: mirrors the claim's: undispatched work is ``PENDING`` and
#: ``FAILED_RETRYABLE``, and a row whose backoff has not elapsed is not late --
#: it is scheduled.
OLDEST_PENDING_SQL: Final[str] = """
SELECT extract(epoch FROM %(now)s::TIMESTAMPTZ - min(occurred_at))
  FROM outbox_events
 WHERE status IN ('PENDING', 'FAILED_RETRYABLE')
   AND next_attempt_at <= %(now)s::TIMESTAMPTZ
"""

#: How long a claimed row stays claimed. Imported semantics, restated here only
#: because the SQL binds it as a computed timestamp rather than as an interval:
#: CockroachDB rejects ``make_interval(secs => $n)``, verified against the
#: cluster.
_CLAIM_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "tenant_id",
    "user_id",
    "aggregate_type",
    "aggregate_id",
    "aggregate_version",
    "event_type",
    "payload_version",
    "payload",
    "trace_id",
    "causation_id",
    "correlation_id",
    "attempt_count",
    "occurred_at",
)


class SqlOutboxStore:
    """:class:`OutboxStore` over the app pool.

    Each method takes its own connection and commits on its own: pooled
    connections are opened ``autocommit=True`` (``provenance_db.pools``), which
    is what §9.12 needs. A claim that were rolled back with the publish would
    make the lease meaningless, and a mark that waited for a batch would strand
    every row behind one poison event.
    """

    __slots__ = ("_source",)

    def __init__(self, source: ConnectionSource) -> None:
        self._source = source

    async def claim(self, *, limit: int, now: datetime, lease_seconds: int) -> list[dict[str, Any]]:
        """Take up to *limit* due rows, flipping them to ``DISPATCHING``.

        The status flip **is** the lease: the claim query excludes
        ``DISPATCHING``, so a second sweeper in a second container cannot take
        the same row. ``next_attempt_at`` carries the expiry, which is what
        makes :meth:`reclaim_expired` necessary rather than optional.
        """
        async with self._source.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                CLAIM_SQL,
                {
                    "limit": limit,
                    "now": now,
                    "lease_until": now + timedelta(seconds=lease_seconds),
                },
            )
            rows = await cur.fetchall()
        return [dict(zip(_CLAIM_COLUMNS, row, strict=True)) for row in rows]

    async def mark_dispatched(self, event_ids: list[uuid.UUID], now: datetime) -> None:
        if not event_ids:
            return
        async with self._source.connection() as conn:
            await conn.execute(MARK_DISPATCHED_SQL, {"event_ids": event_ids, "now": now})

    async def mark_failed(
        self,
        *,
        event_id: uuid.UUID,
        attempt_count: int,
        next_attempt_at: datetime,
        last_error: str,
    ) -> None:
        async with self._source.connection() as conn:
            await conn.execute(
                MARK_FAILED_SQL,
                {
                    "event_id": event_id,
                    "attempt_count": attempt_count,
                    "next_attempt_at": next_attempt_at,
                    "last_error": last_error,
                },
            )

    async def mark_dead(self, *, event_id: uuid.UUID, attempt_count: int, last_error: str) -> None:
        async with self._source.connection() as conn:
            await conn.execute(
                MARK_DEAD_SQL,
                {
                    "event_id": event_id,
                    "attempt_count": attempt_count,
                    "last_error": last_error,
                },
            )

    async def reclaim_expired(self, *, now: datetime) -> int:
        """Return rows whose lease expired to the retry queue.

        Without this a sweeper that died between claim and mark strands its
        rows in ``DISPATCHING`` forever, because the claim query deliberately
        cannot see them — and a stranded ``trigger.fired.v1`` is a silently
        forgotten obligation, which is the exact failure this product exists to
        prevent.
        """
        async with self._source.connection() as conn:
            cursor = await conn.execute(
                RECLAIM_EXPIRED_SQL,
                {"now": now, "last_error": "lease expired; reclaimed by the sweeper"},
            )
            return int(getattr(cursor, "rowcount", 0) or 0)

    async def oldest_pending_age_seconds(self, *, now: datetime) -> float | None:
        """How long the oldest undispatched row has been waiting, or ``None``.

        ``None`` when the outbox holds nothing due, and that is not the same
        number as ``0``: an operator watching this reads ``0`` as "something is
        waiting and it is fresh" and ``None`` as "nothing is waiting". Reporting
        the first for the second is the founding rule of this codebase in its
        smallest form.

        Not part of the ``OutboxStore`` Protocol: it reports on the queue rather
        than driving the state machine, and widening the Protocol for it would
        make every implementation carry a method the dispatcher never calls.
        """
        async with self._source.connection() as conn:
            cursor = await conn.execute(OLDEST_PENDING_SQL, {"now": now})
            row = await cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    async def replay(self, *, event_id: uuid.UUID, now: datetime) -> bool:
        """Re-arm one ``DEAD`` row. Returns whether a row was actually re-armed.

        A boolean and not ``None``: "there was no such dead letter" and "it is
        queued again" are opposite answers for an operator, and a method that
        returned nothing would make them indistinguishable.
        """
        async with self._source.connection() as conn:
            cursor = await conn.execute(REPLAY_SQL, {"event_id": event_id, "now": now})
            return int(getattr(cursor, "rowcount", 0) or 0) > 0


def _assert_satisfies_protocol(store: SqlOutboxStore) -> OutboxStore:
    """Structural check by the type checker rather than at the first sweep."""
    return store


class SqlProcessedEventLedger:
    """§12's dedupe row, over the connection the unit of work opened.

    The connection is supplied and the transaction is owned by
    :class:`SqlConsumerUnitOfWork`, because the whole point of §12 is that this
    insert and the handler's effect share one transaction.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def record(
        self,
        *,
        consumer_name: str,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        result_hash: bytes | None = None,
    ) -> None:
        """Claim ``(consumer_name, event_id)``.

        Raises:
            DuplicateEventDeliveryError: the pair already exists. Raised from
                the database's own uniqueness constraint rather than from a
                prior read, because two coroutines can both read "absent"
                before either writes and only the constraint settles it.
        """
        try:
            await self._conn.execute(
                RECORD_PROCESSED_SQL,
                {
                    "consumer_name": consumer_name,
                    "event_id": event_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "result_hash": result_hash,
                },
            )
        except pgerr.UniqueViolation as violation:
            raise DuplicateEventDeliveryError(
                consumer_name=consumer_name, event_id=event_id
            ) from violation

    async def stamp_result(
        self, *, consumer_name: str, event_id: uuid.UUID, result_hash: bytes
    ) -> None:
        """Write the handler's result digest onto the row that guarded it.

        A separate statement because the hash is only knowable *after* the
        handler ran, while the row had to exist *before* it. Both are inside the
        one transaction, so the row is never visible half-written.
        """
        from services.control_plane.app.events.consumer import STAMP_RESULT_SQL

        await self._conn.execute(
            STAMP_RESULT_SQL,
            {
                "consumer_name": consumer_name,
                "event_id": event_id,
                "result_hash": result_hash,
            },
        )


class SqlConsumerUnitOfWork:
    """One transaction containing both the dedupe row and the effect."""

    __slots__ = ("_source",)

    def __init__(self, source: ConnectionSource) -> None:
        self._source = source

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SqlProcessedEventLedger]:
        async with self._source.connection() as conn, conn.transaction():
            yield SqlProcessedEventLedger(conn)
