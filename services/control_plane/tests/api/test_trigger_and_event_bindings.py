"""The four bindings that give prospective memory and the event plane a door.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` §9.10 (evaluate), §9.12 (sweep), §9.13
  (deliveries) and ``docs/specs/16_TRIGGER_DSL.md`` §13.2 (the manual wake).
- ``services/control_plane/app/api/adapters/unbound.py`` — the register these
  four entries were deleted from.

What "bound" has to mean here
------------------------------
Not "the method returns a dict". ``UNBOUND``'s whole argument is that a method
with no backing must refuse rather than answer, because an answer is
indistinguishable from a real one. So each test below drives the **real**
adapter and asserts that the answer came from the machinery underneath it: the
evaluator that re-reads canonical state, the dispatcher state machine, the
dedupe ledger. A binding that returned a plausible constant would pass a
shape test and fail every one of these.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from provenance_contracts.identity import CapabilityBinding
from services.control_plane.app.api.adapters import UNBOUND, KernelInternalPort, KernelWritePort
from services.control_plane.app.api.ports import OwnerScope
from services.control_plane.app.events.transport import PublishedEvent
from services.control_plane.app.triggers.projection import ProjectionSnapshot
from services.control_plane.app.triggers.service import (
    CommitReceipt,
    CommitRequest,
    TriggerSnapshot,
)

pytestmark = pytest.mark.unit

TENANT = uuid.UUID("0f6c1e88-2a94-4b31-8d5c-77e1a0b93f42")
USER = uuid.UUID("b1d47a03-8e26-4c9f-a0b3-5f2c9d8e1470")
CASE = uuid.UUID("4d2b8e10-6c3a-4f77-9a51-b8e0d3c7a291")
TRIGGER = uuid.UUID("a7e3d901-5b48-4c26-9f13-8d0a2e6b4c77")
COMMITMENT = uuid.UUID("9c1f4b2e-7a55-4d31-b0c7-2f8e6a91d044")

#: ``CANONICAL_DECISIONS.md`` -> *Hero dataset canon*.
DUE_AT = datetime(2026, 6, 15, tzinfo=UTC)
DEMO_CLOCK = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
SCOPE = OwnerScope(tenant_id=TENANT, user_id=USER)


# ==========================================================================
# The hero rows, and the boundaries the evaluator depends on
# ==========================================================================


def _predicate(*, must_be_paid: bool = False) -> dict[str, Any]:
    """The landlord predicate, or a genuinely false one over the same rows.

    ``CANONICAL_DECISIONS.md`` -> *Trigger demonstration*: the no-op
    demonstration uses a real false predicate and performs no hidden state
    revert. ``must_be_paid`` asks whether the deposit already carries an
    admitted fulfillment, which on the hero rows it does not.
    """
    deposit = "commitments.deposit"
    conjunct: dict[str, Any] = (
        {
            "op": "EQ",
            "left": {"op": "FIELD", "path": f"{deposit}.has_admitted_fulfillment"},
            "right": {"op": "CONST", "type": "BOOL", "value": True},
        }
        if must_be_paid
        else {
            "op": "GT",
            "left": {"op": "FIELD", "path": f"{deposit}.outstanding_amount"},
            "right": {"op": "CONST", "type": "DECIMAL", "value": "0"},
        }
    )
    return {
        "ast_version": "1.0",
        "bindings": {"deposit": {"kind": "COMMITMENT", "id": str(COMMITMENT)}},
        "predicate": {
            "op": "AND",
            "args": [
                {
                    "op": "GTE",
                    "left": {"op": "FIELD", "path": "clock.now"},
                    "right": {"op": "FIELD", "path": f"{deposit}.due_at"},
                },
                conjunct,
            ],
        },
    }


def _trigger_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": TRIGGER,
        "tenant_id": TENANT,
        "user_id": USER,
        "case_id": CASE,
        "trigger_type": "COMMITMENT_DEADLINE",
        "predicate_ast": _predicate(),
        "not_before": datetime(2026, 6, 15, 0, 1, tzinfo=UTC),
        "expires_at": datetime(2027, 6, 15, tzinfo=UTC),
        "state": "ARMED",
        "evaluation_version": 1,
        "basis_case_revision": 11,
        "schedule_name": f"pv-trg-{TRIGGER.hex}-v1",
        "last_evaluated_at": None,
        "last_result": None,
        "last_reason_code": None,
        "fired_at": None,
    }
    row.update(overrides)
    return row


def _case_row() -> dict[str, Any]:
    return {
        "case_id": CASE,
        "tenant_id": TENANT,
        "user_id": USER,
        "case_status": "WAITING",
        "case_revision": 11,
        "attention_level": "ATTENTION",
        "reopened_count": 0,
        "opened_at": datetime(2026, 5, 16, 14, 22, tzinfo=UTC),
        "resolved_at": None,
        "last_activity_at": datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        "db_now": DEMO_CLOCK,
        "open_conflict_count": 1,
        "needs_human_conflict_count": 1,
        "active_commitment_count": 2,
        "total_outstanding_amount": Decimal("2020.0000"),
        "outstanding_currency": "USD",
    }


def _commitment_row() -> dict[str, Any]:
    return {
        "id": COMMITMENT,
        "status": "ACTIVE",
        "commitment_type": "MONETARY_RETURN",
        "revision": 4,
        "currency": "USD",
        "committed_amount": Decimal("1800.0000"),
        "fulfilled_amount": Decimal("0.0000"),
        "outstanding_amount": Decimal("1800.0000"),
        "due_at": DUE_AT,
        "valid_from": datetime(2026, 5, 16, 14, 22, tzinfo=UTC),
        "valid_to": None,
        "has_admitted_fulfillment": False,
    }


@dataclass
class _Store:
    row: dict[str, Any]
    reads: int = 0

    async def load(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, trigger_id: uuid.UUID
    ) -> TriggerSnapshot | None:
        self.reads += 1
        if (tenant_id, user_id, trigger_id) != (
            self.row["tenant_id"],
            self.row["user_id"],
            self.row["id"],
        ):
            return None
        return TriggerSnapshot(row=dict(self.row), db_now=DEMO_CLOCK)


@dataclass
class _Reader:
    reads: int = 0

    async def read(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        case_id: uuid.UUID,
        commitment_ids: tuple[uuid.UUID, ...],
    ) -> ProjectionSnapshot:
        del tenant_id, user_id, case_id
        self.reads += 1
        rows = {cid: _commitment_row() for cid in commitment_ids if cid == COMMITMENT}
        return ProjectionSnapshot(case_row=_case_row(), commitment_rows=rows)


@dataclass
class _Kernel:
    """The canonical writer, at its transaction boundary."""

    case_revision: int = 11
    commits: list[CommitRequest] = field(default_factory=list)

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        self.commits.append(request)
        if request.increments_case_revision:
            self.case_revision += 1
        return CommitReceipt(
            committed=True,
            revision_moved=False,
            case_revision_after=self.case_revision,
            proposal_id=uuid.uuid4(),
            outbox_event_ids=tuple(uuid.uuid4() for _ in request.outbox_event_types),
        )


@dataclass
class _Transport:
    published: list[PublishedEvent] = field(default_factory=list)
    fail_with: Exception | None = None

    async def publish(self, events: Sequence[PublishedEvent]) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.published.extend(events)


@dataclass
class _OutboxStore:
    rows: list[dict[str, Any]] = field(default_factory=list)
    dispatched: list[uuid.UUID] = field(default_factory=list)
    failed: list[uuid.UUID] = field(default_factory=list)
    dead: list[uuid.UUID] = field(default_factory=list)
    reclaimed: int = 0
    oldest_age: float | None = 3.0

    async def claim(self, *, limit: int, now: datetime, lease_seconds: int) -> list[dict[str, Any]]:
        del now, lease_seconds
        taken, self.rows = self.rows[:limit], self.rows[limit:]
        return taken

    async def mark_dispatched(self, event_ids: list[uuid.UUID], now: datetime) -> None:
        del now
        self.dispatched.extend(event_ids)

    async def mark_failed(self, **kwargs: Any) -> None:
        self.failed.append(kwargs["event_id"])

    async def mark_dead(self, **kwargs: Any) -> None:
        self.dead.append(kwargs["event_id"])

    async def reclaim_expired(self, *, now: datetime) -> int:
        del now
        return self.reclaimed

    async def replay(self, *, event_id: uuid.UUID, now: datetime) -> bool:
        del event_id, now
        return False

    async def oldest_pending_age_seconds(self, *, now: datetime) -> float | None:
        del now
        return self.oldest_age


@dataclass
class _Ledger:
    seen: set[tuple[str, uuid.UUID]] = field(default_factory=set)
    stamped: list[tuple[str, uuid.UUID]] = field(default_factory=list)

    async def record(
        self,
        *,
        consumer_name: str,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        result_hash: bytes | None = None,
    ) -> None:
        from services.control_plane.app.events.consumer import DuplicateEventDeliveryError

        del tenant_id, user_id, result_hash
        key = (consumer_name, event_id)
        if key in self.seen:
            raise DuplicateEventDeliveryError(consumer_name=consumer_name, event_id=event_id)
        self.seen.add(key)

    async def stamp_result(
        self, *, consumer_name: str, event_id: uuid.UUID, result_hash: bytes
    ) -> None:
        del result_hash
        self.stamped.append((consumer_name, event_id))


@dataclass
class _UnitOfWork:
    ledger: _Ledger = field(default_factory=_Ledger)

    def transaction(self) -> Any:
        return _Held(self.ledger)


class _Held:
    def __init__(self, ledger: _Ledger) -> None:
        self.ledger = ledger

    async def __aenter__(self) -> _Ledger:
        return self.ledger

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Source:
    """A connection source nothing in these tests is allowed to reach."""

    def connection(self) -> Any:  # pragma: no cover - reaching it is the failure
        raise AssertionError("these bindings must not open a connection of their own")


def _binding(**overrides: Any) -> CapabilityBinding:
    fields: dict[str, Any] = {
        "binding_id": TRIGGER,
        "binding_kind": "TRIGGER_EVALUATION",
        "tenant_id": TENANT,
        "user_id": USER,
        "case_id": CASE,
        "expires_at": DEMO_CLOCK + timedelta(hours=1),
        "status": "ACTIVE",
    }
    fields.update(overrides)
    return CapabilityBinding(**fields)


def _internal(
    *,
    store: _Store | None = None,
    reader: _Reader | None = None,
    kernel: _Kernel | None = None,
    outbox: _OutboxStore | None = None,
    transport: _Transport | None = None,
    unit_of_work: _UnitOfWork | None = None,
) -> KernelInternalPort:
    return KernelInternalPort(
        _Source(),
        kernel_pool=None,
        read=SimpleNamespace(),
        policy=SimpleNamespace(),
        sink=SimpleNamespace(),
        clock=lambda: DEMO_CLOCK,
        trigger_store=store or _Store(_trigger_row()),
        projection_reader=reader or _Reader(),
        trigger_kernel=kernel or _Kernel(),
        outbox_store=outbox or _OutboxStore(),
        transport=transport or _Transport(),
        consumer_unit_of_work=unit_of_work or _UnitOfWork(),
    )


def _write(
    *,
    store: _Store | None = None,
    reader: _Reader | None = None,
    kernel: _Kernel | None = None,
) -> KernelWritePort:
    return KernelWritePort(
        _Source(),
        kernel_pool=None,
        read=SimpleNamespace(),
        policy=SimpleNamespace(),
        clock=lambda: DEMO_CLOCK,
        trigger_store=store or _Store(_trigger_row()),
        projection_reader=reader or _Reader(),
        trigger_kernel=kernel or _Kernel(),
    )


# ==========================================================================
# 0. The register
# ==========================================================================


def test_the_four_methods_are_no_longer_declared_unbound() -> None:
    """Wiring a method means deleting a line from the register, which is a
    visible act in a diff."""
    for method in (
        "internal.evaluate_trigger",
        "internal.sweep_outbox",
        "internal.deliver_event",
        "write.wake_trigger",
    ):
        assert method not in UNBOUND, f"{method} is bound but still declared unbound"


# ==========================================================================
# 1. §9.10 — the scheduled wake
# ==========================================================================


@pytest.mark.asyncio
async def test_9_10_fires_the_hero_trigger_through_the_real_evaluator() -> None:
    """The second reveal, end to end through the adapter.

    ``outstanding_amount`` is still 1,800 and the deadline elapsed on 15 June,
    so the predicate is TRUE at the demo clock and the outcome is a fire with
    the reason code the trigger *type* names -- not a restatement of "the
    predicate was true".
    """
    kernel = _Kernel()
    port = _internal(kernel=kernel)
    row = await port.evaluate_trigger(
        _binding(),
        SimpleNamespace(scheduled_for=DEMO_CLOCK, evaluation_version=1, schedule_name=None),
    )

    assert row["result"] == "FIRED"
    assert row["reason_code"] == "COMMITMENT_OVERDUE_UNPAID"
    assert row["state"] == "FIRED"
    assert row["case_revision_before"] == 11
    assert row["case_revision_after"] == 12
    assert row["basis_case_revision"] == 11
    assert row["outbox_event_ids"]
    # §9.10's `field_values`: what the predicate actually saw, so a judge checks
    # the conclusion rather than takes it.
    assert row["field_values"]["commitments.deposit.outstanding_amount"] == "1800.0000"
    assert kernel.commits and kernel.commits[0].result.value == "FIRED"


@pytest.mark.asyncio
async def test_9_10_a_false_predicate_no_ops_against_the_same_unmodified_rows() -> None:
    """The demonstration ``CANONICAL_DECISIONS.md`` -> *Trigger demonstration*
    requires: a real false predicate over exactly the rows the fire reads, and
    no hidden state revert. Nothing about the world changed between this test
    and the one above except the stored predicate."""
    kernel = _Kernel()
    port = _internal(
        store=_Store(_trigger_row(predicate_ast=_predicate(must_be_paid=True))), kernel=kernel
    )
    row = await port.evaluate_trigger(
        _binding(),
        SimpleNamespace(scheduled_for=DEMO_CLOCK, evaluation_version=1, schedule_name=None),
    )

    assert row["result"] == "NO_OP"
    assert row["reason_code"] == "PREDICATE_FALSE"
    assert row["state"] == "ARMED"
    assert row["case_revision_after"] == 11
    assert kernel.case_revision == 11


@pytest.mark.asyncio
async def test_9_10_a_stale_generation_never_fires() -> None:
    """Guard G3. A delivery from the schedule a re-arm replaced cannot act on
    the trigger that replaced it."""
    kernel = _Kernel()
    port = _internal(kernel=kernel)
    row = await port.evaluate_trigger(
        _binding(),
        SimpleNamespace(scheduled_for=DEMO_CLOCK, evaluation_version=9, schedule_name=None),
    )

    assert row["result"] == "NO_OP"
    assert row["reason_code"] == "STALE_SCHEDULE_GENERATION"
    assert kernel.commits == []


@pytest.mark.asyncio
async def test_9_10_authority_comes_from_the_row_and_not_from_the_capability() -> None:
    """§9.5. The binding names a trigger; whether it is *yours* is settled by
    the row, and "not yours" is reported exactly like "no such trigger"."""
    port = _internal(store=_Store(_trigger_row(user_id=uuid.uuid4())))
    row = await port.evaluate_trigger(
        _binding(),
        SimpleNamespace(scheduled_for=DEMO_CLOCK, evaluation_version=1, schedule_name=None),
    )
    assert row["result"] == "ERROR"
    assert row["reason_code"] == "PROJECTION_FAILED"
    assert row["http_status"] == 404


# ==========================================================================
# 2. §13.2 — the manual wake, which must prove more rather than less
# ==========================================================================


@pytest.mark.asyncio
async def test_13_2_the_manual_wake_reaches_the_same_evaluator() -> None:
    """ "It is **not** a shortcut, a mock, a fixture, or a forced fire."

    The manual path differs from the scheduled one in exactly two fields --
    ``wake_source`` and ``wake_id`` -- and this asserts the outcome is
    identical, which is only possible if both land in one function.
    """
    kernel = _Kernel()
    port = _write(kernel=kernel)
    row = await port.wake_trigger(
        SCOPE, TRIGGER, SimpleNamespace(dry_run=False, idempotency_key="judge-1")
    )

    assert row is not None
    assert row["result"] == "FIRED"
    assert row["state"] == "FIRED"
    assert kernel.commits[0].idempotency_key.startswith("manual:")
    assert kernel.commits[0].idempotency_key.endswith("judge-1")


@pytest.mark.asyncio
async def test_13_2_pressing_the_button_twice_is_safe_and_says_so() -> None:
    """Guard G2, and Judge Mode displays the second result as a feature."""
    port = _write(store=_Store(_trigger_row(state="FIRED", fired_at=DEMO_CLOCK)))
    row = await port.wake_trigger(
        SCOPE, TRIGGER, SimpleNamespace(dry_run=False, idempotency_key="judge-2")
    )
    assert row is not None
    assert row["result"] == "NO_OP"
    assert row["reason_code"] == "TRIGGER_NOT_ARMED"
    assert row["state"] == "FIRED"


@pytest.mark.asyncio
async def test_13_4_a_dry_run_writes_nothing_and_says_it_wrote_nothing() -> None:
    """ "PREVIEW — no state was changed". Dry run must never be the demo path:
    it proves nothing about the transaction, the revision guard or the
    outbox."""
    kernel = _Kernel()
    port = _write(kernel=kernel)
    row = await port.wake_trigger(
        SCOPE, TRIGGER, SimpleNamespace(dry_run=True, idempotency_key="judge-3")
    )

    assert row is not None
    assert row["dry_run"] is True
    assert row["preview_label"] == "PREVIEW — no state was changed"
    assert kernel.commits == []
    assert row["outbox_event_ids"] == []


@pytest.mark.asyncio
async def test_13_2_a_trigger_that_is_not_yours_is_a_typed_absence() -> None:
    """``None`` from the write port means "no such row **for this scope**" and
    the route maps it to a 404 -- never a 403 (§1.7)."""
    port = _write(store=_Store(_trigger_row(user_id=uuid.uuid4())))
    row = await port.wake_trigger(
        SCOPE, TRIGGER, SimpleNamespace(dry_run=False, idempotency_key="judge-4")
    )
    assert row is None


# ==========================================================================
# 3. §9.12 — the outbox sweep
# ==========================================================================


def _outbox_row(event_type: str = "trigger.fired.v1") -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "user_id": USER,
        "aggregate_type": "TRIGGER",
        "aggregate_id": TRIGGER,
        "aggregate_version": 1,
        "event_type": event_type,
        "payload_version": "1.0",
        "payload": {"trigger_id": str(TRIGGER)},
        "trace_id": uuid.uuid4(),
        "causation_id": None,
        "correlation_id": None,
        "attempt_count": 0,
        "occurred_at": DEMO_CLOCK,
    }


@pytest.mark.asyncio
async def test_9_12_returns_counts_and_publishes_what_it_claimed() -> None:
    store = _OutboxStore(rows=[_outbox_row(), _outbox_row("trigger.noop.v1")])
    transport = _Transport()
    port = _internal(outbox=store, transport=transport)

    row = await port.sweep_outbox(
        SimpleNamespace(batch_size=100, max_batches=1, worker_id="outbox-dispatch-1a2b3c")
    )

    assert set(row) == {
        "claimed",
        "dispatched",
        "failed_retryable",
        "dead",
        "reaped_stale_claims",
        "oldest_pending_age_seconds",
        "duration_ms",
        "worker_id",
    }
    assert row["claimed"] == 2
    assert row["dispatched"] == 2
    assert row["worker_id"] == "outbox-dispatch-1a2b3c"
    assert [event.event_type for event in transport.published] == [
        "trigger.fired.v1",
        "trigger.noop.v1",
    ]


@pytest.mark.asyncio
async def test_9_12_a_refusing_transport_is_re_scheduled_and_not_lost() -> None:
    """The backoff state machine, reached through the binding. A failed publish
    is a ``FAILED_RETRYABLE`` row with a ``next_attempt_at``, never a drop."""
    store = _OutboxStore(rows=[_outbox_row()])
    port = _internal(outbox=store, transport=_Transport(fail_with=RuntimeError("bus refused")))

    row = await port.sweep_outbox(SimpleNamespace(batch_size=100, max_batches=1, worker_id="w1"))

    assert row["claimed"] == 1
    assert row["dispatched"] == 0
    assert row["failed_retryable"] == 1
    assert store.failed


@pytest.mark.asyncio
async def test_9_12_sweeps_up_to_max_batches_and_stops_when_the_outbox_empties() -> None:
    """``max_batches`` is a bound, not a target. A sweeper that always ran the
    full count would issue empty claims against the cluster forever."""
    store = _OutboxStore(rows=[_outbox_row() for _ in range(3)])
    port = _internal(outbox=store, transport=_Transport())

    row = await port.sweep_outbox(SimpleNamespace(batch_size=1, max_batches=20, worker_id="w2"))
    assert row["claimed"] == 3
    assert row["dispatched"] == 3


@pytest.mark.asyncio
async def test_9_12_reports_an_absent_oldest_pending_as_absent() -> None:
    """``None`` and ``0`` are different facts: nothing is waiting, versus
    something is waiting and it is fresh. Reporting the second for the first is
    the founding rule of this codebase."""
    store = _OutboxStore(rows=[], oldest_age=None)
    port = _internal(outbox=store, transport=_Transport())
    row = await port.sweep_outbox(SimpleNamespace(batch_size=10, max_batches=1, worker_id="w3"))
    assert row["oldest_pending_age_seconds"] is None


# ==========================================================================
# 4. §9.13 — consumer intake
# ==========================================================================


def _delivery(event_id: uuid.UUID | None = None, **overrides: Any) -> SimpleNamespace:
    event: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "trigger.fired.v1",
        "aggregate_type": "TRIGGER",
        "aggregate_id": str(TRIGGER),
        "aggregate_version": 1,
        "tenant_id": str(TENANT),
        "user_id": str(USER),
        "trace_id": str(uuid.uuid4()),
        "occurred_at": DEMO_CLOCK.isoformat(),
        "payload": {"trigger_id": str(TRIGGER)},
    }
    event.update(overrides)
    return SimpleNamespace(consumer_name="advocate_dispatch", event=event)


@pytest.mark.asyncio
async def test_9_13_records_the_delivery_and_reports_what_it_did() -> None:
    work = _UnitOfWork()
    port = _internal(unit_of_work=work)
    payload = _delivery()

    row = await port.deliver_event(payload)

    assert row["result"] == "PROCESSED"
    assert row["consumer_name"] == "advocate_dispatch"
    assert row["event_id"] == payload.event["event_id"]
    assert work.ledger.seen


@pytest.mark.asyncio
async def test_9_13_a_redelivery_is_a_duplicate_noop_and_not_an_error() -> None:
    """ "Duplicate delivery is normal in an at-least-once system; treating it as
    a failure would make the DLQ meaningless." """
    work = _UnitOfWork()
    port = _internal(unit_of_work=work)
    payload = _delivery()

    first = await port.deliver_event(payload)
    second = await port.deliver_event(payload)

    assert first["result"] == "PROCESSED"
    assert second["result"] == "DUPLICATE_NOOP"
    assert second["effect"] is None


@pytest.mark.asyncio
async def test_9_13_does_not_claim_a_local_effect_it_did_not_perform() -> None:
    """The honest half of this binding.

    §9.13's example ``effect`` is ``AGENT_RUN_STARTED``, produced by the
    ``advocate_dispatch`` **Lambda** the pivot discarded. No consumer's local
    effect exists in this deployment, so the endpoint reports the one thing it
    did do -- write the dedupe row -- under a kind that says so. Reporting
    ``AGENT_RUN_STARTED`` with a null id, or a null effect beside
    ``PROCESSED``, would both read as work that happened.
    """
    port = _internal()
    row = await port.deliver_event(_delivery())
    assert row["effect"] == {"kind": "DEDUPE_RECORDED", "consumer_name": "advocate_dispatch"}


@pytest.mark.asyncio
async def test_9_13_refuses_an_event_type_outside_the_closed_catalogue() -> None:
    """ "Do not invent event names ad hoc in consumers." An unknown type cannot
    have been written by the Kernel, so a dedupe row for it would be a record of
    a delivery nothing can have sent."""
    from services.control_plane.app.api.errors import ApiError

    port = _internal()
    with pytest.raises(ApiError):
        await port.deliver_event(_delivery(event_type="trigger.exploded.v1"))
