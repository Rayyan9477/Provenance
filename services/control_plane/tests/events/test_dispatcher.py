"""The outbox dispatcher: claim, publish, and re-schedule what failed.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` §13 (the dispatcher state machine).
- ``docs/quality/23_PHASE_GATES.md`` ``G10.4``: "under a forced dispatcher error
  the attempts occur at 1s, 5s, 30s, 2m and 10m on a compressed clock, the row
  reaches ``status=DEAD`` after the schedule is exhausted, an alarm metric is
  emitted, and a manual replay succeeds while the consumer still produces
  exactly one effect."
- ``db/migrations/versions/0008_events_infrastructure.py`` — the real columns,
  including ``ck_outbox_events_attempts`` (``0 <= attempt_count <= 5``) and
  ``ck_outbox_events_dispatched`` (``status = 'DISPATCHED'`` iff
  ``dispatched_at IS NOT NULL``).
- ``docs/EXECUTION/70_TASK_PLAN.md`` T10.1.

The non-negotiable
------------------
**Never treat delivery as exactly-once.** The dispatcher guarantees
at-least-once and the consumer dedupes. That split is not negotiable, and every
test here is written from the dispatcher's side of it: a row may be published
twice — after a lease expiry, after a crash between publish and mark, after a
manual replay — and none of those is a bug. What would be a bug is a row that is
published *never*, or a dispatcher that claims a row another sweeper already
holds.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from services.control_plane.app.events.dispatcher import (
    DEAD_LETTER_METRIC,
    LEASE_SECONDS,
    MAX_ATTEMPT_COUNT,
    RETRY_SCHEDULE,
    OutboxDispatcher,
    OutboxStatus,
    next_attempt_delay,
)
from services.control_plane.tests.events._support.fakes import FailingTransport, RecordingTransport
from services.control_plane.tests.events._support.memory_outbox import (
    MemoryOutboxStore,
    outbox_row,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)


def _dispatcher(store: MemoryOutboxStore, transport: object) -> OutboxDispatcher:
    return OutboxDispatcher(store=store, transport=transport)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The happy path.
# ---------------------------------------------------------------------------


async def test_a_pending_row_is_claimed_published_and_marked() -> None:
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    transport = RecordingTransport()
    swept = await _dispatcher(store, transport).sweep(now=T0)

    assert swept.published == 1
    assert transport.event_types() == ["trigger.fired.v1"]
    row = store.rows[0]
    assert row["status"] == OutboxStatus.DISPATCHED
    assert row["dispatched_at"] == T0


def test_dispatched_and_dispatched_at_move_together() -> None:
    """``ck_outbox_events_dispatched`` is a biconditional, not an implication.

    A row marked DISPATCHED without a timestamp, or timestamped without the
    status, is a row whose history cannot be reconstructed — and the database
    refuses both.
    """
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    store.mark_dispatched_sync([store.rows[0]["id"]], T0)
    row = store.rows[0]
    assert (row["status"] == OutboxStatus.DISPATCHED) == (row["dispatched_at"] is not None)


async def test_a_row_not_yet_due_is_not_claimed() -> None:
    """``next_attempt_at <= now()`` lives in the claim, not in the caller.

    Without it a backoff is advisory: the dispatcher reads back the row it just
    failed on, immediately, and the exponential schedule recorded in the column
    never happens.
    """
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0 + timedelta(seconds=30))])
    swept = await _dispatcher(store, RecordingTransport()).sweep(now=T0)
    assert swept.claimed == 0
    assert store.rows[0]["status"] == OutboxStatus.PENDING


async def test_rows_are_published_oldest_first_and_in_version_order() -> None:
    """``occurred_at`` alone is not a total order.

    Several events from one Kernel transaction share it, and publishing
    ``case.state_changed.v1`` for revision 9 before revision 8 is a consumer bug
    that presents as a producer bug.
    """
    aggregate = uuid.uuid4()
    store = MemoryOutboxStore(
        rows=[
            outbox_row(
                next_attempt_at=T0,
                occurred_at=T0,
                aggregate_id=aggregate,
                aggregate_version=9,
                event_type="case.state_changed.v1",
                aggregate_type="CASE",
            ),
            outbox_row(
                next_attempt_at=T0,
                occurred_at=T0,
                aggregate_id=aggregate,
                aggregate_version=8,
                event_type="case.state_changed.v1",
                aggregate_type="CASE",
            ),
        ]
    )
    transport = RecordingTransport()
    await _dispatcher(store, transport).sweep(now=T0)
    assert [event.aggregate_version for event in transport.published] == [8, 9]


async def test_an_empty_sweep_is_not_an_error() -> None:
    swept = await _dispatcher(MemoryOutboxStore(rows=[]), RecordingTransport()).sweep(now=T0)
    assert (swept.claimed, swept.published, swept.failed) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Leases — two sweepers must not dispatch one row.
# ---------------------------------------------------------------------------


async def test_a_claimed_row_is_invisible_to_a_second_sweeper() -> None:
    """ "The lease is a database fact with an expiry, not an in-process lock."

    ``outbox_events`` has no lease column, so the lease is expressed with the
    two columns that exist: ``status = 'DISPATCHING'`` marks the row held, and
    ``next_attempt_at`` carries when the hold expires. A second sweeper's claim
    filters on ``status IN ('PENDING','FAILED_RETRYABLE')`` and therefore cannot
    see it.
    """
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    claimed = await store.claim(limit=10, now=T0, lease_seconds=LEASE_SECONDS)
    assert len(claimed) == 1
    assert store.rows[0]["status"] == OutboxStatus.DISPATCHING

    second = await store.claim(limit=10, now=T0, lease_seconds=LEASE_SECONDS)
    assert second == []


async def test_a_lease_expires_so_a_crashed_sweeper_does_not_strand_a_row() -> None:
    """The gap this closes, named rather than hidden.

    A sweeper that dies between claim and mark leaves the row ``DISPATCHING``,
    and the ordinary claim query deliberately excludes that status — so without
    a reclaim the event is stranded forever, which is a silently forgotten
    obligation. The reclaim is a separate, explicit operation over expired
    leases, and it is the reason "at-least-once" is true rather than aspirational.
    """
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    await store.claim(limit=10, now=T0, lease_seconds=LEASE_SECONDS)

    still_held = await store.reclaim_expired(now=T0 + timedelta(seconds=LEASE_SECONDS - 1))
    assert still_held == 0

    reclaimed = await store.reclaim_expired(now=T0 + timedelta(seconds=LEASE_SECONDS + 1))
    assert reclaimed == 1
    assert store.rows[0]["status"] == OutboxStatus.FAILED_RETRYABLE
    assert store.rows[0]["last_error"] is not None


async def test_a_reclaimed_row_is_published_again_and_that_is_correct() -> None:
    """At-least-once, demonstrated. The duplicate is the consumer's problem."""
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    transport = RecordingTransport()
    dispatcher = _dispatcher(store, transport)

    await store.claim(limit=10, now=T0, lease_seconds=LEASE_SECONDS)
    later = T0 + timedelta(seconds=LEASE_SECONDS + 1)
    await store.reclaim_expired(now=later)
    await dispatcher.sweep(now=later)

    assert len(transport.published) == 1
    assert store.rows[0]["status"] == OutboxStatus.DISPATCHED


# ---------------------------------------------------------------------------
# The retry schedule — G10.4.
# ---------------------------------------------------------------------------


def test_the_retry_schedule_is_the_one_the_gate_names() -> None:
    assert (
        timedelta(seconds=1),
        timedelta(seconds=5),
        timedelta(seconds=30),
        timedelta(minutes=2),
        timedelta(minutes=10),
    ) == RETRY_SCHEDULE
    assert MAX_ATTEMPT_COUNT == 5


@pytest.mark.parametrize(
    ("failures", "expected"),
    [
        (1, timedelta(seconds=1)),
        (2, timedelta(seconds=5)),
        (3, timedelta(seconds=30)),
        (4, timedelta(minutes=2)),
        (5, timedelta(minutes=10)),
    ],
)
def test_each_failure_schedules_the_next_attempt(failures: int, expected: timedelta) -> None:
    assert next_attempt_delay(failures) == expected


def test_past_the_schedule_there_is_no_next_attempt() -> None:
    assert next_attempt_delay(MAX_ATTEMPT_COUNT + 1) is None


async def test_a_forced_error_walks_the_whole_schedule_then_deads() -> None:
    """G10.4 on a compressed clock: the delays come from configuration.

    The clock is driven by the test rather than slept through, which is the only
    reason this gate does not take fifteen minutes — and the schedule under test
    is the real one, not a shortened copy.
    """
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    transport = FailingTransport(failures=99)
    dispatcher = _dispatcher(store, transport)

    now = T0
    observed: list[timedelta] = []
    for _ in range(len(RETRY_SCHEDULE)):
        before = now
        await dispatcher.sweep(now=now)
        row = store.rows[0]
        assert row["status"] == OutboxStatus.FAILED_RETRYABLE
        observed.append(row["next_attempt_at"] - before)
        now = row["next_attempt_at"]

    assert observed == list(RETRY_SCHEDULE)

    # The schedule is exhausted; the next failure is terminal.
    await dispatcher.sweep(now=now)
    row = store.rows[0]
    assert row["status"] == OutboxStatus.DEAD
    assert row["attempt_count"] == MAX_ATTEMPT_COUNT
    assert row["dispatched_at"] is None


async def test_attempt_count_never_exceeds_the_column_check() -> None:
    """``ck_outbox_events_attempts`` caps it at 5, so the counter saturates.

    A dispatcher that incremented past the cap would fail its own bookkeeping
    UPDATE at the exact moment it was recording a dead letter — losing the
    record of the failure to the failure.
    """
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    dispatcher = _dispatcher(store, FailingTransport(failures=99))
    now = T0
    for _ in range(len(RETRY_SCHEDULE) + 3):
        await dispatcher.sweep(now=now)
        now = (store.rows[0]["next_attempt_at"] or now) + timedelta(seconds=1)
        assert 0 <= store.rows[0]["attempt_count"] <= MAX_ATTEMPT_COUNT


async def test_a_failed_row_records_why() -> None:
    """``ck_outbox_events_dead_has_error``: FAILED_RETRYABLE and DEAD need a reason.

    A dead letter with no error is an operator staring at a row that stopped,
    with nothing to act on.
    """
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    transport = FailingTransport(failures=1, error=RuntimeError("bus refused entry"))
    await _dispatcher(store, transport).sweep(now=T0)
    assert store.rows[0]["last_error"] is not None
    assert "bus refused" in store.rows[0]["last_error"]


async def test_a_transient_failure_recovers_without_operator_action() -> None:
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    transport = FailingTransport(failures=1)
    dispatcher = _dispatcher(store, transport)

    await dispatcher.sweep(now=T0)
    assert store.rows[0]["status"] == OutboxStatus.FAILED_RETRYABLE

    await dispatcher.sweep(now=store.rows[0]["next_attempt_at"])
    assert store.rows[0]["status"] == OutboxStatus.DISPATCHED
    assert len(transport.published) == 1


# ---------------------------------------------------------------------------
# Dead letters and replay.
# ---------------------------------------------------------------------------


async def test_a_dead_letter_emits_an_alarm_metric() -> None:
    """ "A silently dropped event is a silently forgotten obligation."."""
    metrics: list[tuple[str, dict[str, str]]] = []
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    dispatcher = OutboxDispatcher(
        store=store,  # type: ignore[arg-type]
        transport=FailingTransport(failures=99),
        on_metric=lambda name, tags: metrics.append((name, tags)),
    )
    now = T0
    for _ in range(len(RETRY_SCHEDULE) + 1):
        await dispatcher.sweep(now=now)
        now = (store.rows[0]["next_attempt_at"] or now) + timedelta(seconds=1)

    assert DEAD_LETTER_METRIC in [name for name, _ in metrics]
    tags = next(tags for name, tags in metrics if name == DEAD_LETTER_METRIC)
    assert tags["event_type"] == "trigger.fired.v1"


async def test_a_dead_row_is_not_claimed_again() -> None:
    """``DEAD`` means an operator's problem, not a retry's."""
    store = MemoryOutboxStore(
        rows=[outbox_row(next_attempt_at=T0, status=OutboxStatus.DEAD, last_error="exhausted")]
    )
    swept = await _dispatcher(store, RecordingTransport()).sweep(now=T0)
    assert swept.claimed == 0


async def test_a_manual_replay_re_arms_a_dead_row() -> None:
    """G10.4's tail: the replay succeeds and the event is delivered.

    The replayed row keeps its ``event_id``, which is what makes the consumer's
    dedupe still correct — the replay is a *re-delivery of the same fact*, not a
    new one, and the two must not be distinguishable downstream.
    """
    original = outbox_row(next_attempt_at=T0, status=OutboxStatus.DEAD, last_error="exhausted")
    event_id = original["id"]
    store = MemoryOutboxStore(rows=[original])
    transport = RecordingTransport()

    replayed = await store.replay(event_id=event_id, now=T0)
    assert replayed is True
    assert store.rows[0]["status"] == OutboxStatus.PENDING
    assert store.rows[0]["attempt_count"] == 0

    await _dispatcher(store, transport).sweep(now=T0)
    assert len(transport.published) == 1
    assert transport.published[0].event_id == event_id


async def test_replaying_a_row_that_is_not_dead_is_refused() -> None:
    """Replay is an operator action on a stopped row, not a re-publish button.

    Re-arming a row that is merely ``FAILED_RETRYABLE`` would reset a backoff
    that is doing its job, and re-arming a ``DISPATCHED`` row would publish a
    fact twice for no reason at all.
    """
    store = MemoryOutboxStore(rows=[outbox_row(next_attempt_at=T0)])
    assert await store.replay(event_id=store.rows[0]["id"], now=T0) is False
    assert store.rows[0]["status"] == OutboxStatus.PENDING


# ---------------------------------------------------------------------------
# Idempotence of the sweeper itself — T10.1.
# ---------------------------------------------------------------------------


async def test_re_enabling_a_paused_sweeper_drains_without_duplication() -> None:
    """Rows accumulate in PENDING while the sweeper is off; one pass drains them."""
    rows = [
        outbox_row(next_attempt_at=T0, occurred_at=T0 + timedelta(seconds=index))
        for index in range(5)
    ]
    store = MemoryOutboxStore(rows=rows)
    transport = RecordingTransport()
    dispatcher = _dispatcher(store, transport)

    first = await dispatcher.sweep(now=T0 + timedelta(minutes=1))
    second = await dispatcher.sweep(now=T0 + timedelta(minutes=1))

    assert first.published == 5
    assert second.claimed == 0
    assert len(transport.published) == 5
    assert len({event.event_id for event in transport.published}) == 5


async def test_the_batch_limit_is_honoured() -> None:
    rows = [
        outbox_row(next_attempt_at=T0, occurred_at=T0 + timedelta(seconds=index))
        for index in range(10)
    ]
    store = MemoryOutboxStore(rows=rows)
    dispatcher = OutboxDispatcher(
        store=store,  # type: ignore[arg-type]
        transport=RecordingTransport(),
        batch_size=3,
    )
    swept = await dispatcher.sweep(now=T0)
    assert swept.claimed == 3


async def test_one_failure_does_not_hold_up_the_rest_of_the_batch() -> None:
    """Each row is marked on its own, so a poison row is isolated to itself."""

    class OneBadEvent:
        def __init__(self) -> None:
            self.published: list[object] = []

        async def publish(self, events: object) -> None:
            for event in events:  # type: ignore[union-attr]
                if event.event_type == "trigger.noop.v1":
                    raise RuntimeError("poison")
                self.published.append(event)

    store = MemoryOutboxStore(
        rows=[
            outbox_row(next_attempt_at=T0, occurred_at=T0, event_type="trigger.fired.v1"),
            outbox_row(
                next_attempt_at=T0,
                occurred_at=T0 + timedelta(seconds=1),
                event_type="trigger.noop.v1",
            ),
            outbox_row(
                next_attempt_at=T0,
                occurred_at=T0 + timedelta(seconds=2),
                event_type="case.state_changed.v1",
                aggregate_type="CASE",
            ),
        ]
    )
    transport = OneBadEvent()
    swept = await _dispatcher(store, transport).sweep(now=T0)

    assert swept.published == 2
    assert swept.failed == 1
    statuses = [row["status"] for row in store.rows]
    assert statuses.count(OutboxStatus.DISPATCHED) == 2
    assert statuses.count(OutboxStatus.FAILED_RETRYABLE) == 1


# ---------------------------------------------------------------------------
# The one permitted canonical write.
# ---------------------------------------------------------------------------


def test_the_dispatcher_only_ever_updates_status_bookkeeping() -> None:
    """The enumerated exception, and its exact boundary.

    ``repositories/__init__.py`` permits "the dispatcher's ``UPDATE
    outbox_events SET status = ...``, which is status bookkeeping about a row
    the Kernel wrote and carries no domain meaning". It may never author an
    event: an event is written in the same transaction as the state it
    describes, or it is a claim about state that was never committed.
    """
    import inspect
    import re

    from services.control_plane.app.events import dispatcher as dispatcher_mod

    source = inspect.getsource(dispatcher_mod)
    statements = re.findall(
        r"\b(INSERT\s+INTO|UPSERT\s+INTO|DELETE\s+FROM|UPDATE)\s+(\w+)", source, re.IGNORECASE
    )
    for operation, table in statements:
        if table.lower() != "outbox_events":
            continue
        assert operation.upper() == "UPDATE", f"{operation} on outbox_events is not permitted"
    assert any(
        table.lower() == "outbox_events" for _, table in statements
    ), "the module must contain the enumerated UPDATE, or this test is vacuous"
