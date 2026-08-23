"""Idempotent consumption — the half of at-least-once that lives downstream.

Authority
---------
- ``specs/15_API_SPEC.md`` §12 (the consumer dedupe transaction).
- ``db/migrations/versions/0008_events_infrastructure.py`` —
  ``pk_processed_events PRIMARY KEY (consumer_name, event_id)``,
  ``ck_processed_events_consumer_shape``, ``ck_processed_events_result_hash``.
- ``EXECUTION/70_TASK_PLAN.md`` T10.2.

The split, restated from this side
-----------------------------------
The dispatcher guarantees at-least-once. That guarantee is only useful because
of what happens here: the same ``event_id`` delivered to the same
``consumer_name`` twice produces **one** business effect, and the second
delivery is a ``NOOP`` rather than an error. It is not an error because the
caller did nothing wrong — a duplicate is the expected shape of the contract,
and returning a failure would push benign redeliveries into a dead-letter queue.

Why the ledger row goes in first, inside the same transaction
--------------------------------------------------------------
Write it *after* the effect and a crash in between leaves an effect nobody has a
record of, so the redelivery applies it again. Write it *before*, in its own
transaction, and a crash leaves a record of an effect that never happened, so
the redelivery skips it and the work is lost silently and permanently. Both
orderings lose. The insert and the effect commit or roll back together, and the
insert goes first within that transaction so the uniqueness conflict is taken
before any work is done.

The bus is also unordered
-------------------------
At-least-once says nothing about order. A consumer that sees ``aggregate_version``
8 after 9 must **drop**, not apply — otherwise it overwrites newer state with
older, and the outbox delivered both faithfully so nothing upstream looks wrong.
:func:`is_out_of_order` is that check, offered rather than assumed, because it is
a decision each handler has to make about its own projection.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol, TypeVar

from services.control_plane.app.events.transport import PublishedEvent

__all__ = [
    "CONSUMER_NAME_PATTERN",
    "ConsumerUnitOfWork",
    "ConsumptionOutcome",
    "ConsumptionResult",
    "DuplicateEventDeliveryError",
    "IdempotentConsumer",
    "ProcessedEventLedger",
    "is_out_of_order",
    "result_hash",
]

#: ``ck_processed_events_consumer_shape``. Checked at construction rather than
#: at insert time, because the insert failing takes the effect down with it.
CONSUMER_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")

T = TypeVar("T")


class DuplicateEventDeliveryError(Exception):
    """``pk_processed_events`` rejected the insert. This event was already handled.

    An exception rather than a boolean return, because it is raised by the
    database's own uniqueness constraint under concurrency, and modelling the
    race any other way would mean re-implementing the constraint in Python where
    two coroutines can both read "absent" before either writes.
    """

    def __init__(self, *, consumer_name: str, event_id: uuid.UUID) -> None:
        super().__init__(f"{consumer_name} already processed {event_id}")
        self.consumer_name = consumer_name
        self.event_id = event_id


class ConsumptionOutcome(str, Enum):
    PROCESSED = "PROCESSED"
    NOOP = "NOOP"


@dataclass(frozen=True, slots=True)
class ConsumptionResult:
    outcome: ConsumptionOutcome
    event_id: uuid.UUID
    consumer_name: str
    result_hash: bytes | None = None


class ProcessedEventLedger(Protocol):
    """One row in ``processed_events``, written in two steps.

    Two steps because the row must exist *before* the handler runs — that is
    what takes the uniqueness conflict early — while the result hash is only
    knowable *after* it. Both statements are inside the one transaction, so the
    row is never visible in its half-written form.

    Raises:
        DuplicateEventDeliveryError: ``(consumer_name, event_id)`` already exists.
    """

    async def record(
        self,
        *,
        consumer_name: str,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        result_hash: bytes | None = None,
    ) -> None: ...

    async def stamp_result(
        self, *, consumer_name: str, event_id: uuid.UUID, result_hash: bytes
    ) -> None: ...


class ConsumerUnitOfWork(Protocol):
    """One transaction containing both the dedupe row and the effect."""

    def transaction(self) -> AbstractAsyncContextManager[ProcessedEventLedger]: ...


def result_hash(value: object) -> bytes | None:
    """SHA-256 of a handler's result, or ``None`` when there was none.

    ``ck_processed_events_result_hash`` requires exactly 32 bytes when present.
    ``None`` in, ``None`` out, deliberately: hashing "no result" would write a
    fixed, meaningless 32 bytes into every row and make "these two runs produced
    the same result" true of runs that produced nothing at all.
    """
    if value is None:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).digest()


def is_out_of_order(event: PublishedEvent, last_applied_version: int | None) -> bool:
    """Whether *event* describes a version this consumer has already passed.

    ``>=`` rather than ``>``: a redelivery of the version already applied has
    nothing new to say, and applying it again is the duplicate the ledger exists
    to prevent.
    """
    if last_applied_version is None:
        return False
    return event.aggregate_version <= last_applied_version


@dataclass
class IdempotentConsumer:
    """Runs one handler at most once per ``(consumer_name, event_id)``."""

    consumer_name: str
    unit_of_work: ConsumerUnitOfWork

    def __post_init__(self) -> None:
        if not CONSUMER_NAME_PATTERN.match(self.consumer_name):
            raise ValueError(
                f"consumer name {self.consumer_name!r} does not match "
                f"{CONSUMER_NAME_PATTERN.pattern}; ck_processed_events_consumer_shape "
                "would refuse the dedupe insert, and that insert failing rolls the "
                "effect back with it"
            )

    async def consume(
        self,
        event: PublishedEvent,
        handler: Callable[[PublishedEvent], Awaitable[T]],
    ) -> ConsumptionResult:
        """Record the delivery and run *handler*, or report a benign duplicate.

        The handler's exceptions are **not** swallowed. A failed handler must
        roll the ledger row back with it and let the redelivery try again; a
        consumer that caught the error and reported success would commit a
        record of an effect that never happened.
        """
        async with self.unit_of_work.transaction() as ledger:
            try:
                await ledger.record(
                    consumer_name=self.consumer_name,
                    event_id=event.event_id,
                    tenant_id=event.tenant_id,
                    user_id=event.user_id,
                )
            except DuplicateEventDeliveryError:
                return ConsumptionResult(
                    outcome=ConsumptionOutcome.NOOP,
                    event_id=event.event_id,
                    consumer_name=self.consumer_name,
                )
            outcome = await handler(event)
            digest = result_hash(outcome)
            if digest is not None:
                await ledger.stamp_result(
                    consumer_name=self.consumer_name,
                    event_id=event.event_id,
                    result_hash=digest,
                )

        return ConsumptionResult(
            outcome=ConsumptionOutcome.PROCESSED,
            event_id=event.event_id,
            consumer_name=self.consumer_name,
            result_hash=digest,
        )


#: ``UPDATE processed_events SET result_hash = ...`` — the ledger's own row,
#: inside the transaction that created it. ``processed_events`` is not a
#: canonical table (``pv_app_reader_writer`` owns it outright), so this is an
#: ordinary write and not an exception to anything.
STAMP_RESULT_SQL: Final[str] = """
    UPDATE processed_events
       SET result_hash = %(result_hash)s
     WHERE consumer_name = %(consumer_name)s
       AND event_id = %(event_id)s
"""
