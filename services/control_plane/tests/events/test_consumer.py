"""Idempotent consumption: the other half of at-least-once.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` §12 (the consumer dedupe transaction).
- ``docs/quality/23_PHASE_GATES.md`` ``G10.1`` and DDL §19 test 9 (``D9``):
  "delivering the same ``event_id`` to the same ``consumer_name`` twice makes
  the second INSERT raise a duplicate key on ``pk_processed_events``, the
  consumer returns NOOP, and the downstream side-effect count stays 1".
- ``db/migrations/versions/0008_events_infrastructure.py`` —
  ``pk_processed_events PRIMARY KEY (consumer_name, event_id)`` and
  ``ck_processed_events_consumer_shape``.

Why the dedupe insert is inside the handler's transaction
----------------------------------------------------------
If the ledger row were written *after* the effect, a crash in between would
leave an effect nobody has a record of, and the redelivery would apply it again.
If it were written *before*, in its own transaction, a crash would leave a
record of an effect that never happened, and the redelivery would skip it. Both
orderings lose. The insert and the effect commit or roll back **together**, and
:func:`test_a_failed_handler_rolls_the_ledger_row_back_with_it` is the assertion
that keeps that true.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from services.control_plane.app.events.consumer import (
    ConsumptionOutcome,
    DuplicateEventDeliveryError,
    IdempotentConsumer,
    is_out_of_order,
    result_hash,
)
from services.control_plane.app.events.transport import PublishedEvent

pytestmark = pytest.mark.unit

T0 = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)
AGGREGATE = uuid.UUID("a7e3d901-5b48-4c26-9f13-8d0a2e6b4c77")


def _event(*, event_id: uuid.UUID | None = None, aggregate_version: int = 12) -> PublishedEvent:
    return PublishedEvent(
        event_id=event_id or uuid.uuid4(),
        event_type="trigger.fired.v1",
        aggregate_type="TRIGGER",
        aggregate_id=AGGREGATE,
        aggregate_version=aggregate_version,
        tenant_id=uuid.UUID("0f6c1e88-2a94-4b31-8d5c-77e1a0b93f42"),
        user_id=uuid.UUID("b1d47a03-8e26-4c9f-a0b3-5f2c9d8e1470"),
        trace_id=uuid.UUID("2b6f8c14-9d33-4a02-8e77-13c5a90b6d21"),
        occurred_at=T0,
        payload_version="1.0",
        payload={"trigger_id": str(AGGREGATE)},
    )


@dataclass
class MemoryLedger:
    """``processed_events``, with its primary key and its transaction semantics.

    ``rows`` is the committed state and ``staged`` is what one in-flight
    transaction has written. A rollback discards ``staged``, which is what makes
    the "commit or roll back together" claim testable rather than assumed.
    """

    rows: dict[tuple[str, uuid.UUID], dict[str, Any]] = field(default_factory=dict)
    staged: dict[tuple[str, uuid.UUID], dict[str, Any]] = field(default_factory=dict)
    commits: int = 0
    rollbacks: int = 0

    async def record(
        self,
        *,
        consumer_name: str,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        result_hash: bytes | None = None,
    ) -> None:
        key = (consumer_name, event_id)
        if key in self.rows or key in self.staged:
            raise DuplicateEventDeliveryError(consumer_name=consumer_name, event_id=event_id)
        assert result_hash is None or len(result_hash) == 32, "ck_processed_events_result_hash"
        self.staged[key] = {
            "consumer_name": consumer_name,
            "event_id": event_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "result_hash": result_hash,
        }

    async def stamp_result(
        self, *, consumer_name: str, event_id: uuid.UUID, result_hash: bytes
    ) -> None:
        assert len(result_hash) == 32, "ck_processed_events_result_hash"
        row = self.staged.get((consumer_name, event_id))
        assert row is not None, "stamp_result must run inside the transaction that recorded"
        row["result_hash"] = result_hash

    async def commit(self) -> None:
        self.rows.update(self.staged)
        self.staged.clear()
        self.commits += 1

    async def rollback(self) -> None:
        self.staged.clear()
        self.rollbacks += 1


@dataclass
class MemoryUnitOfWork:
    ledger: MemoryLedger = field(default_factory=MemoryLedger)

    def transaction(self) -> Any:
        ledger = self.ledger

        class _Txn:
            async def __aenter__(self) -> MemoryLedger:
                return ledger

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
                if exc_type is None:
                    await ledger.commit()
                else:
                    await ledger.rollback()
                return False

        return _Txn()


@dataclass
class SideEffect:
    """The downstream thing whose count the gate asserts stays at 1."""

    applied: list[uuid.UUID] = field(default_factory=list)
    fail_with: Exception | None = None

    async def __call__(self, event: PublishedEvent) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.applied.append(event.event_id)
        return f"handled:{event.event_id}"


def _consumer(uow: MemoryUnitOfWork, name: str = "advocate.attention") -> IdempotentConsumer:
    return IdempotentConsumer(consumer_name=name, unit_of_work=uow)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# D9 — the duplicate delivery.
# ---------------------------------------------------------------------------


async def test_duplicate_event_noop() -> None:
    """``D9``. Second delivery: duplicate key, NOOP, side-effect count stays 1."""
    uow = MemoryUnitOfWork()
    consumer = _consumer(uow)
    effect = SideEffect()
    event = _event()

    first = await consumer.consume(event, effect)
    second = await consumer.consume(event, effect)

    assert first.outcome is ConsumptionOutcome.PROCESSED
    assert second.outcome is ConsumptionOutcome.NOOP
    assert len(effect.applied) == 1
    assert len(uow.ledger.rows) == 1


async def test_the_dedupe_key_is_consumer_plus_event_and_nothing_else() -> None:
    """Two consumers of one event each get their own effect.

    ``pk_processed_events`` is ``(consumer_name, event_id)``: the ledger records
    that *this consumer* handled it, not that the event was handled by someone.
    A dedupe on ``event_id`` alone would let the first consumer to see an event
    silently suppress every other subscriber.
    """
    uow = MemoryUnitOfWork()
    event = _event()
    advocate_effect = SideEffect()
    projection_effect = SideEffect()

    await _consumer(uow, "advocate.attention").consume(event, advocate_effect)
    await _consumer(uow, "projection.rebuild").consume(event, projection_effect)

    assert len(advocate_effect.applied) == 1
    assert len(projection_effect.applied) == 1
    assert len(uow.ledger.rows) == 2


async def test_a_replayed_event_keeps_its_id_so_the_dedupe_still_holds() -> None:
    """An operator replay is a re-delivery of one fact, not a second fact."""
    uow = MemoryUnitOfWork()
    consumer = _consumer(uow)
    effect = SideEffect()
    event = _event()

    await consumer.consume(event, effect)
    # The dispatcher re-arms the DEAD row; `event_id` is unchanged.
    replayed = _event(event_id=event.event_id)
    outcome = await consumer.consume(replayed, effect)

    assert outcome.outcome is ConsumptionOutcome.NOOP
    assert len(effect.applied) == 1


async def test_concurrent_deliveries_produce_one_effect() -> None:
    """Both deliveries race for the same key; the loser is a benign NOOP."""
    import asyncio

    uow = MemoryUnitOfWork()
    consumer = _consumer(uow)
    effect = SideEffect()
    event = _event()

    results = await asyncio.gather(consumer.consume(event, effect), consumer.consume(event, effect))
    outcomes = sorted(result.outcome.value for result in results)
    assert outcomes == ["NOOP", "PROCESSED"]
    assert len(effect.applied) == 1


# ---------------------------------------------------------------------------
# The transaction boundary.
# ---------------------------------------------------------------------------


async def test_a_failed_handler_rolls_the_ledger_row_back_with_it() -> None:
    """The whole reason the insert is inside the handler's transaction.

    A committed ledger row for an effect that never happened would make the
    redelivery skip the work, and the obligation would be lost — silently, and
    permanently, because nothing would ever try again.
    """
    uow = MemoryUnitOfWork()
    consumer = _consumer(uow)
    effect = SideEffect(fail_with=RuntimeError("handler blew up"))
    event = _event()

    with pytest.raises(RuntimeError):
        await consumer.consume(event, effect)

    assert uow.ledger.rows == {}
    assert uow.ledger.rollbacks == 1

    # The redelivery therefore does the work, which is the correct outcome.
    effect.fail_with = None
    result = await consumer.consume(event, effect)
    assert result.outcome is ConsumptionOutcome.PROCESSED
    assert len(effect.applied) == 1


async def test_the_ledger_row_is_written_before_the_effect_runs() -> None:
    """The insert takes the uniqueness conflict early, before any work is done.

    Ordering it after the effect would mean two concurrent deliveries both do
    the work and only then discover one of them was a duplicate.
    """
    uow = MemoryUnitOfWork()
    consumer = _consumer(uow)
    observed: list[int] = []

    async def handler(event: PublishedEvent) -> str:
        observed.append(len(uow.ledger.staged))
        return "ok"

    await consumer.consume(_event(), handler)
    assert observed == [1]


async def test_the_result_hash_is_recorded_so_a_replay_can_be_compared() -> None:
    uow = MemoryUnitOfWork()
    consumer = _consumer(uow)
    event = _event()
    await consumer.consume(event, SideEffect())
    row = next(iter(uow.ledger.rows.values()))
    assert row["result_hash"] is not None
    assert len(row["result_hash"]) == 32


def test_the_result_hash_is_stable_and_thirty_two_bytes() -> None:
    """``ck_processed_events_result_hash`` requires exactly 32 bytes."""
    first = result_hash("handled:abc")
    assert first == result_hash("handled:abc")
    assert len(first) == 32
    assert first != result_hash("handled:abd")


def test_a_none_result_records_no_hash_rather_than_a_hash_of_nothing() -> None:
    """A handler with no result is honest about it.

    Hashing ``None`` would put a fixed, meaningless 32 bytes in every row and
    make "these two runs produced the same result" true of runs that produced
    nothing.
    """
    assert result_hash(None) is None


# ---------------------------------------------------------------------------
# Ordering — the bus is unordered, and consumers must cope.
# ---------------------------------------------------------------------------


def test_an_older_aggregate_version_is_dropped_not_applied() -> None:
    """ "A consumer that sees version 8 after version 9 must drop, not apply."

    At-least-once delivery is also *unordered*. A consumer that applied the
    older event would overwrite newer state with older state — and the outbox
    would have delivered both faithfully, so nothing downstream would look wrong.
    """
    assert is_out_of_order(_event(aggregate_version=8), last_applied_version=9) is True
    assert is_out_of_order(_event(aggregate_version=10), last_applied_version=9) is False


def test_the_same_version_twice_is_out_of_order_too() -> None:
    """A redelivery of the version already applied has nothing new to say."""
    assert is_out_of_order(_event(aggregate_version=9), last_applied_version=9) is True


def test_nothing_applied_yet_means_nothing_is_out_of_order() -> None:
    assert is_out_of_order(_event(aggregate_version=1), last_applied_version=None) is False


# ---------------------------------------------------------------------------
# The consumer name is a database-constrained value.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Advocate", "ab", "x" * 65, "advocate attention", ""])
def test_a_consumer_name_the_ledger_would_refuse_is_refused_here(name: str) -> None:
    """``ck_processed_events_consumer_shape`` — ``^[a-z][a-z0-9_.-]{2,63}$``.

    Catching it at construction beats catching it when the dedupe insert fails,
    because that insert failing rolls the effect back with it.
    """
    with pytest.raises(ValueError, match="consumer name"):
        IdempotentConsumer(consumer_name=name, unit_of_work=MemoryUnitOfWork())  # type: ignore[arg-type]


def test_a_valid_consumer_name_is_accepted() -> None:
    consumer = IdempotentConsumer(
        consumer_name="advocate.attention",
        unit_of_work=MemoryUnitOfWork(),  # type: ignore[arg-type]
    )
    assert consumer.consumer_name == "advocate.attention"


def test_the_consumer_module_writes_no_canonical_table() -> None:
    """``processed_events`` is not canonical; nothing else may be touched here."""
    import inspect
    import re

    from services.control_plane.app.events import consumer as consumer_mod

    source = inspect.getsource(consumer_mod)
    statements = re.findall(
        r"\b(INSERT\s+INTO|UPSERT\s+INTO|DELETE\s+FROM|UPDATE)\s+(\w+)", source, re.IGNORECASE
    )
    assert {table.lower() for _, table in statements} <= {"processed_events"}
