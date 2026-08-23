"""The transactional outbox dispatcher — claim, publish, re-schedule, give up.

Authority
---------
- ``specs/15_API_SPEC.md`` §13 (the dispatcher state machine).
- ``db/migrations/versions/0008_events_infrastructure.py`` — the real columns
  and the three CHECKs this module has to satisfy.
- ``EXECUTION/70_TASK_PLAN.md`` T10.1.
- ``packages/python/provenance_db/.../repositories/__init__.py`` — the
  enumeration that permits exactly one write from here.

Delivery is at-least-once, and that is a design decision
---------------------------------------------------------
**Never treat delivery as exactly-once.** The dispatcher guarantees
at-least-once; the consumer dedupes on ``event_id`` through ``processed_events``.
That split is not negotiable. Three ordinary situations publish a row twice — a
lease that expired while a sweeper was slow, a crash between the publish and the
status write, and an operator replay — and none of them is a bug. Building
"exactly once" here would mean a distributed transaction between a database and
a bus, which is the thing the outbox pattern exists to avoid.

The one write, and its exact boundary
--------------------------------------
This module issues ``UPDATE outbox_events SET status = ...`` and nothing else
against a canonical table. That is the single enumerated exception to the Kernel
being the sole canonical writer: it is status bookkeeping about a row the Kernel
already wrote and it carries no domain meaning. It may never *author* an event.
An event is written in the same transaction as the state it describes, or it is
a claim about state that was never committed —
``test_the_dispatcher_only_ever_updates_status_bookkeeping`` holds that line
against the source.

The lease has no column, so it rides on two that exist
-------------------------------------------------------
``0008`` gives ``outbox_events`` no lease column and this phase adds no
migration. The hold is therefore expressed with ``status = 'DISPATCHING'`` —
which the claim query excludes, so a second sweeper cannot take the row — plus
``next_attempt_at`` as the expiry. That combination is what makes
:meth:`OutboxStore.reclaim_expired` necessary and not optional: without it a
sweeper that dies between claim and mark strands the row in ``DISPATCHING``
forever, and a stranded ``trigger.fired.v1`` is a silently forgotten obligation,
which is the exact failure this product exists to prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from services.control_plane.app.events.transport import EventTransport, PublishedEvent

__all__ = [
    "CLAIM_SQL",
    "DEAD_LETTER_METRIC",
    "LEASE_SECONDS",
    "MARK_DEAD_SQL",
    "MARK_DISPATCHED_SQL",
    "MARK_FAILED_SQL",
    "MAX_ATTEMPT_COUNT",
    "RECLAIM_EXPIRED_SQL",
    "REPLAY_SQL",
    "RETRY_SCHEDULE",
    "DispatchMetric",
    "OutboxDispatcher",
    "OutboxStatus",
    "OutboxStore",
    "SweepResult",
    "next_attempt_delay",
]


class OutboxStatus:
    """``ck_outbox_events_status``. A namespace of literals, not an enum.

    The values go straight into SQL parameters and come straight back out of a
    ``STRING`` column, and an enum member would have to be unwrapped at every
    one of those crossings. ``provenance_domain.enums.OutboxStatus`` is the
    typed vocabulary for code that reasons about them.
    """

    PENDING: Final[str] = "PENDING"
    DISPATCHING: Final[str] = "DISPATCHING"
    DISPATCHED: Final[str] = "DISPATCHED"
    FAILED_RETRYABLE: Final[str] = "FAILED_RETRYABLE"
    DEAD: Final[str] = "DEAD"


#: ``G10.4``: "the attempts occur at 1s, 5s, 30s, 2m and 10m on a compressed
#: clock". Configuration rather than literals in the code path, so the gate can
#: drive the clock instead of sleeping through fifteen minutes of real time —
#: and so the schedule under test is this one rather than a shortened copy.
RETRY_SCHEDULE: Final[tuple[timedelta, ...]] = (
    timedelta(seconds=1),
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
)

#: ``ck_outbox_events_attempts CHECK (attempt_count >= 0 AND attempt_count <= 5)``.
#: The counter saturates here rather than growing: a dispatcher that incremented
#: past the cap would fail its own bookkeeping UPDATE at the exact moment it was
#: recording a dead letter, losing the record of the failure to the failure.
MAX_ATTEMPT_COUNT: Final[int] = 5

#: How long a claim holds a row. Long enough for a slow publish, short enough
#: that a crashed sweeper's rows come back within a sweep interval or two.
LEASE_SECONDS: Final[int] = 300

#: The alarm. AWS's default for a failed delivery is a silent drop; this is the
#: opposite of that, and it is why ``DEAD`` is a state rather than a deletion.
DEAD_LETTER_METRIC: Final[str] = "provenance_outbox_dead_letter_total"


def next_attempt_delay(failures: int) -> timedelta | None:
    """The backoff after *failures* failed attempts, or ``None`` when exhausted.

    ``failures`` is 1-based: the first failure schedules the first delay. Past
    the end of the schedule there is no next attempt and the row is dead.
    """
    if failures < 1 or failures > len(RETRY_SCHEDULE):
        return None
    return RETRY_SCHEDULE[failures - 1]


# ---------------------------------------------------------------------------
# The statements. Every canonical write this module performs is here.
# ---------------------------------------------------------------------------

#: The claim. ``FOR UPDATE`` plus a status flip is what makes the lease a
#: database fact rather than an in-process lock, so two sweepers in two
#: containers cannot dispatch one row.
#:
#: ``DISPATCHING`` is excluded from the claim on purpose — it means another
#: sweeper holds the row — and ``DEAD`` is excluded because a row that exhausted
#: its attempts is an operator's problem, not a retry's.
#:
#: ``next_attempt_at <= now()`` is in the SQL and not in the caller. Without it
#: the backoff is advisory: the dispatcher reads back the row it just failed on,
#: immediately, and the schedule recorded in the column never happens.
#:
#: ``lease_until`` is computed in Python and bound, rather than added in SQL.
#: CockroachDB rejects ``make_interval(secs => $n)`` outright -- verified against
#: the cluster, not assumed -- and the arithmetic form would put a second clock
#: in the statement anyway. Deriving it from the same ``now`` the claim filters
#: on keeps the whole operation on one instant.
CLAIM_SQL: Final[str] = """
    UPDATE outbox_events
       SET status = 'DISPATCHING',
           next_attempt_at = %(lease_until)s::TIMESTAMPTZ
     WHERE id IN (
             SELECT id FROM outbox_events
              WHERE status IN ('PENDING', 'FAILED_RETRYABLE')
                AND next_attempt_at <= %(now)s::TIMESTAMPTZ
              ORDER BY next_attempt_at ASC, occurred_at ASC, aggregate_version ASC
              LIMIT %(limit)s
            )
 RETURNING id, tenant_id, user_id, aggregate_type, aggregate_id, aggregate_version,
           event_type, payload_version, payload, trace_id, causation_id,
           correlation_id, attempt_count, occurred_at
"""

#: ``ck_outbox_events_dispatched`` is a biconditional, so the status and the
#: timestamp move in one statement or the row becomes unwritable.
MARK_DISPATCHED_SQL: Final[str] = """
    UPDATE outbox_events
       SET status = 'DISPATCHED',
           dispatched_at = %(now)s::TIMESTAMPTZ,
           last_error = NULL
     WHERE id = ANY(%(event_ids)s::UUID[])
"""

#: ``ck_outbox_events_dead_has_error``: a retryable failure carries its reason,
#: or an operator is left staring at a row that stopped with nothing to act on.
MARK_FAILED_SQL: Final[str] = """
    UPDATE outbox_events
       SET status = 'FAILED_RETRYABLE',
           attempt_count = %(attempt_count)s,
           next_attempt_at = %(next_attempt_at)s::TIMESTAMPTZ,
           last_error = %(last_error)s
     WHERE id = %(event_id)s
"""

MARK_DEAD_SQL: Final[str] = """
    UPDATE outbox_events
       SET status = 'DEAD',
           attempt_count = %(attempt_count)s,
           last_error = %(last_error)s
     WHERE id = %(event_id)s
"""

#: The reclaim. Without this a sweeper that died between claim and mark strands
#: its rows in ``DISPATCHING`` forever, because the claim query deliberately
#: cannot see them.
RECLAIM_EXPIRED_SQL: Final[str] = """
    UPDATE outbox_events
       SET status = 'FAILED_RETRYABLE',
           last_error = %(last_error)s
     WHERE status = 'DISPATCHING'
       AND next_attempt_at <= %(now)s::TIMESTAMPTZ
"""

#: Operator replay of a dead letter. Scoped to ``status = 'DEAD'`` so it cannot
#: reset a backoff that is doing its job, or republish an already-delivered fact.
#: ``event_id`` is deliberately unchanged: the replay is a re-delivery of the
#: same fact, and the consumer's dedupe has to keep recognising it as such.
REPLAY_SQL: Final[str] = """
    UPDATE outbox_events
       SET status = 'PENDING',
           attempt_count = 0,
           next_attempt_at = %(now)s::TIMESTAMPTZ,
           last_error = NULL
     WHERE id = %(event_id)s
       AND status = 'DEAD'
"""


class OutboxStore(Protocol):
    """The five statements above, behind a Protocol.

    Narrow on purpose: this package does not reach into the repository layer
    another lane owns, and the whole dispatcher runs in the hermetic ``unit``
    lane against an in-memory implementation that enforces the same CHECKs the
    cluster does.
    """

    async def claim(
        self, *, limit: int, now: datetime, lease_seconds: int
    ) -> list[dict[str, Any]]: ...

    async def mark_dispatched(self, event_ids: list[uuid.UUID], now: datetime) -> None: ...

    async def mark_failed(
        self,
        *,
        event_id: uuid.UUID,
        attempt_count: int,
        next_attempt_at: datetime,
        last_error: str,
    ) -> None: ...

    async def mark_dead(
        self, *, event_id: uuid.UUID, attempt_count: int, last_error: str
    ) -> None: ...

    async def reclaim_expired(self, *, now: datetime) -> int: ...

    async def replay(self, *, event_id: uuid.UUID, now: datetime) -> bool: ...


DispatchMetric = Callable[[str, dict[str, str]], None]


@dataclass(frozen=True, slots=True)
class SweepResult:
    """What one pass did. Every number exists so a zero can be interrogated."""

    claimed: int = 0
    published: int = 0
    failed: int = 0
    dead: int = 0


def _row_to_event(row: dict[str, Any]) -> PublishedEvent:
    return PublishedEvent(
        event_id=row["id"],
        event_type=row["event_type"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        aggregate_version=int(row["aggregate_version"]),
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        trace_id=row["trace_id"],
        occurred_at=row["occurred_at"],
        payload_version=row["payload_version"],
        payload=row["payload"],
        causation_id=row.get("causation_id"),
        correlation_id=row.get("correlation_id"),
    )


@dataclass
class OutboxDispatcher:
    """Sweeps the outbox and publishes what it claims.

    The sweeper is idempotent: re-enabling it after a pause drains accumulated
    ``PENDING`` rows without duplication, because the claim is what excludes a
    row rather than any in-process memory of having seen it.
    """

    store: OutboxStore
    transport: EventTransport
    batch_size: int = 50
    lease_seconds: int = LEASE_SECONDS
    on_metric: DispatchMetric | None = None

    def _metric(self, name: str, tags: dict[str, str]) -> None:
        if self.on_metric is not None:
            self.on_metric(name, tags)

    async def sweep(self, *, now: datetime) -> SweepResult:
        """Claim a batch, publish each row, and record what happened to it.

        Rows are published and marked **individually**. Batching the publish
        would make one poison row hold up everything claimed with it, and the
        whole point of ``DEAD`` is that a single undeliverable event is isolated
        rather than blocking the queue behind it.
        """
        rows = await self.store.claim(
            limit=self.batch_size, now=now, lease_seconds=self.lease_seconds
        )
        if not rows:
            return SweepResult()

        dispatched: list[uuid.UUID] = []
        failed = 0
        dead = 0

        for row in rows:
            event = _row_to_event(row)
            try:
                await self.transport.publish([event])
            except Exception as error:
                outcome = await self._record_failure(row=row, error=error, now=now)
                if outcome == "DEAD":
                    dead += 1
                else:
                    failed += 1
                continue
            dispatched.append(event.event_id)

        if dispatched:
            await self.store.mark_dispatched(dispatched, now)

        return SweepResult(claimed=len(rows), published=len(dispatched), failed=failed, dead=dead)

    async def _record_failure(self, *, row: dict[str, Any], error: Exception, now: datetime) -> str:
        failures = int(row["attempt_count"]) + 1
        message = f"{type(error).__name__}: {error}"[:1000]
        delay = next_attempt_delay(failures)
        if delay is None:
            # The schedule is exhausted. The counter is clamped so the UPDATE
            # stays inside ck_outbox_events_attempts.
            await self.store.mark_dead(
                event_id=row["id"],
                attempt_count=min(failures, MAX_ATTEMPT_COUNT),
                last_error=message,
            )
            self._metric(
                DEAD_LETTER_METRIC,
                {
                    "event_type": str(row["event_type"]),
                    "aggregate_type": str(row["aggregate_type"]),
                },
            )
            return "DEAD"

        await self.store.mark_failed(
            event_id=row["id"],
            attempt_count=min(failures, MAX_ATTEMPT_COUNT),
            next_attempt_at=now + delay,
            last_error=message,
        )
        return "FAILED_RETRYABLE"

    async def reclaim(self, *, now: datetime) -> int:
        """Return rows whose lease expired to the retry queue.

        Run on the same schedule as :meth:`sweep`. It is the half of
        at-least-once that a claim-only dispatcher silently omits.
        """
        reclaimed = await self.store.reclaim_expired(now=now)
        if reclaimed:
            self._metric("provenance_outbox_lease_reclaimed_total", {"count": str(reclaimed)})
        return reclaimed

    async def replay_dead_letter(self, *, event_id: uuid.UUID, now: datetime) -> bool:
        """Re-arm one ``DEAD`` row. An operator action, and it keeps the id."""
        return await self.store.replay(event_id=event_id, now=now)

    async def publish_now(self, events: Sequence[PublishedEvent]) -> None:  # pragma: no cover
        """Escape hatch for tooling that already holds built events."""
        await self.transport.publish(events)
