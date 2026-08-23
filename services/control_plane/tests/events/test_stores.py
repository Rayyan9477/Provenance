"""The SQL behind prospective memory and the event plane — T10.5.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` §7.2 (the projection queries) and §9.5 (the
  trigger row and the database clock, read together).
- ``docs/specs/15_API_SPEC.md`` §9.12 and §12.
- ``db/migrations/versions/0006_prospective_memory.py`` and
  ``0008_events_infrastructure.py``.

What a hermetic suite can prove about SQL
------------------------------------------
Not that the cluster accepts it — that is what
``services/control_plane/tests/db`` is for. What it can prove, and what fails
silently without a test, is the shape of the calls around it: that every
statement binds an owner, that the two projection queries run inside **one**
transaction, that an absent case refuses rather than returning an empty
projection, and that ``None`` and ``0`` stay distinguishable in the sweep's
report.

The last two are the same rule twice. ``D-00-005``: absence is not emptiness.
A projection reader that answered ``{}`` for a case it could not see would hand
the evaluator zeroes, and zeroes evaluate — a predicate reading
``outstanding_amount`` would come out FALSE and the trigger would DISARM. The
obligation would be forgotten because of a permission error.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from services.control_plane.app.events.store import (
    SqlConsumerUnitOfWork,
    SqlOutboxStore,
    SqlProcessedEventLedger,
)
from services.control_plane.app.triggers.projection import ProjectionUnavailable
from services.control_plane.app.triggers.store import (
    CASE_PROJECTION_SQL,
    COMMITMENT_PROJECTION_SQL,
    TRIGGER_ROW_SQL,
    SqlProjectionReader,
    SqlTriggerStore,
)

pytestmark = pytest.mark.unit

TENANT = uuid.UUID(int=0xB001)
USER = uuid.UUID(int=0xB002)
CASE = uuid.UUID(int=0xB003)
TRIGGER = uuid.UUID(int=0xB004)
COMMITMENT = uuid.UUID(int=0xB005)
NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)


# ==========================================================================
# A connection that answers by statement and records the parameters
# ==========================================================================


class _Cursor:
    def __init__(self, columns: tuple[str, ...], rows: list[tuple[Any, ...]]) -> None:
        self.description = [(name,) for name in columns]
        self._rows = rows
        self.rowcount = len(rows)

    async def execute(self, sql: str, params: Any = None) -> None:  # pragma: no cover
        raise AssertionError("the cursor is pre-loaded; execute goes through the connection")

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Connection:
    """Answers a fixed set of statements; records every call, in order."""

    def __init__(self, answers: dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, Any]] = []
        self.transactions = 0
        self.open_transaction = False

    def _answer(self, sql: str) -> _Cursor:
        flat = " ".join(sql.split())
        self.calls.append((flat, None))
        for marker, (columns, rows) in self.answers.items():
            if marker in flat:
                return _Cursor(columns, rows)
        return _Cursor((), [])

    def cursor(self) -> _CursorHandle:
        return _CursorHandle(self)

    async def execute(self, sql: str, params: Any = None) -> _Cursor:
        cursor = self._answer(sql)
        self.calls[-1] = (self.calls[-1][0], params)
        return cursor

    def transaction(self) -> _Txn:
        return _Txn(self)


class _CursorHandle:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn
        self.cursor: _Cursor | None = None

    async def __aenter__(self) -> _CursorHandle:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, sql: str, params: Any = None) -> None:
        self.cursor = self.conn._answer(sql)
        self.conn.calls[-1] = (self.conn.calls[-1][0], params)

    @property
    def description(self) -> Any:
        assert self.cursor is not None
        return self.cursor.description

    async def fetchall(self) -> list[tuple[Any, ...]]:
        assert self.cursor is not None
        return await self.cursor.fetchall()

    async def fetchone(self) -> tuple[Any, ...] | None:
        assert self.cursor is not None
        return await self.cursor.fetchone()


class _Txn:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self) -> None:
        self.conn.transactions += 1
        self.conn.open_transaction = True

    async def __aexit__(self, *exc: Any) -> bool:
        self.conn.open_transaction = False
        return False


class _Source:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    def connection(self) -> _Held:
        return _Held(self.conn)


class _Held:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Connection:
        return self.conn

    async def __aexit__(self, *exc: Any) -> bool:
        return False


_TRIGGER_COLUMNS = (
    "id",
    "tenant_id",
    "user_id",
    "case_id",
    "trigger_type",
    "predicate_ast",
    "not_before",
    "expires_at",
    "state",
    "evaluation_version",
    "basis_case_revision",
    "schedule_name",
    "last_evaluated_at",
    "last_result",
    "last_reason_code",
    "fired_at",
    "db_now",
)

_TRIGGER_ROW = (
    TRIGGER,
    TENANT,
    USER,
    CASE,
    "COMMITMENT_DEADLINE",
    {"ast_version": "1.0"},
    NOW - timedelta(days=95),
    NOW + timedelta(days=270),
    "ARMED",
    1,
    11,
    f"pv-trg-{TRIGGER.hex}-v1",
    None,
    None,
    None,
    None,
    NOW,
)

_CASE_COLUMNS = ("case_id", "tenant_id", "user_id", "case_status", "case_revision", "db_now")
_CASE_ROW = (CASE, TENANT, USER, "WAITING", 11, NOW)

_COMMITMENT_COLUMNS = ("id", "status", "outstanding_amount")
_COMMITMENT_ROW = (COMMITMENT, "ACTIVE", Decimal("1800.0000"))


# ==========================================================================
# 1. The trigger row and the clock, read together — §9.5
# ==========================================================================


@pytest.mark.asyncio
async def test_the_trigger_row_arrives_with_the_database_clock() -> None:
    """The guards judge ``expires_at`` and ``not_before`` against the database's
    clock and never the caller's. A store that returned the row alone would
    force the guard to invent a clock, and inventing a clock is how a trigger
    fires a minute before its deadline."""
    conn = _Connection({"FROM prospective_triggers": (_TRIGGER_COLUMNS, [_TRIGGER_ROW])})
    snapshot = await SqlTriggerStore(_Source(conn)).load(
        tenant_id=TENANT, user_id=USER, trigger_id=TRIGGER
    )

    assert snapshot is not None
    assert snapshot.db_now == NOW
    assert snapshot.row["state"] == "ARMED"
    assert snapshot.row["evaluation_version"] == 1
    # Every statement binds the owner. A read that could be issued without one
    # is the shape a cross-user leak takes.
    _, params = conn.calls[0]
    assert params == {"tenant_id": TENANT, "user_id": USER, "trigger_id": TRIGGER}


@pytest.mark.asyncio
async def test_a_trigger_that_is_not_there_is_none_and_so_is_one_that_is_not_yours() -> None:
    """Both answers are ``None``, and the SQL is why: the scoping predicate is
    in the ``WHERE``, so a cross-tenant read returns no row rather than a row
    this code then has to refuse. Anything else is an existence oracle."""
    conn = _Connection({"FROM prospective_triggers": (_TRIGGER_COLUMNS, [])})
    snapshot = await SqlTriggerStore(_Source(conn)).load(
        tenant_id=TENANT, user_id=USER, trigger_id=TRIGGER
    )
    assert snapshot is None
    assert "tenant_id = %(tenant_id)s" in TRIGGER_ROW_SQL
    assert "user_id = %(user_id)s" in TRIGGER_ROW_SQL


# ==========================================================================
# 2. One snapshot, and an absent case refuses — §7.2, D-00-005
# ==========================================================================


@pytest.mark.asyncio
async def test_both_projection_queries_run_inside_one_transaction() -> None:
    """§7.2: "Both reads happen inside **one read-only transaction**."

    Read in separate autocommit statements, ``cases.revision`` and the
    commitment amounts could come from two different instants, and the fire
    transaction's revision guard would then be comparing against a revision
    that never coexisted with the values that were evaluated.
    """
    conn = _Connection(
        {
            "FROM cases c": (_CASE_COLUMNS, [_CASE_ROW]),
            "FROM commitments m": (_COMMITMENT_COLUMNS, [_COMMITMENT_ROW]),
        }
    )
    snapshot = await SqlProjectionReader(_Source(conn)).read(
        tenant_id=TENANT, user_id=USER, case_id=CASE, commitment_ids=(COMMITMENT,)
    )

    assert conn.transactions == 1
    assert len(conn.calls) == 2
    assert snapshot.case_row["case_revision"] == 11
    assert snapshot.commitment_rows[COMMITMENT]["outstanding_amount"] == Decimal("1800.0000")


@pytest.mark.asyncio
async def test_a_case_that_cannot_be_read_refuses_rather_than_projecting_zeroes() -> None:
    """``D-00-005``, with an obligation attached.

    An empty projection is not "the case is empty" -- it is "the case was never
    loaded", and the two lead to opposite decisions. Zeroes evaluate: a
    predicate reading ``outstanding_amount`` would come out FALSE, and
    ``classify_false`` would DISARM the trigger. The obligation would be
    silently forgotten because of a permission error.
    """
    conn = _Connection({"FROM cases c": (_CASE_COLUMNS, [])})
    with pytest.raises(ProjectionUnavailable):
        await SqlProjectionReader(_Source(conn)).read(
            tenant_id=TENANT, user_id=USER, case_id=CASE, commitment_ids=(COMMITMENT,)
        )


def test_the_commitment_query_is_scoped_to_the_case_as_well_as_the_owner() -> None:
    """§7.2: ``m.case_id = $1`` is a **security control, not an optimisation**.

    A binding that names a commitment belonging to a different case returns no
    row, which surfaces as ``BINDING_UNRESOLVED`` (§10.4) rather than as a
    cross-case read of someone else's obligation.
    """
    assert "m.case_id = %(case_id)s" in COMMITMENT_PROJECTION_SQL
    assert "m.tenant_id = %(tenant_id)s" in COMMITMENT_PROJECTION_SQL
    assert "m.user_id = %(user_id)s" in COMMITMENT_PROJECTION_SQL
    assert "c.tenant_id = %(tenant_id)s" in CASE_PROJECTION_SQL
    assert "c.user_id = %(user_id)s" in CASE_PROJECTION_SQL


# ==========================================================================
# 3. The outbox store — §9.12
# ==========================================================================


@pytest.mark.asyncio
async def test_the_claim_binds_a_lease_computed_from_the_same_instant_it_filters_on() -> None:
    """CockroachDB rejects ``make_interval(secs => $n)`` -- verified against the
    cluster, not assumed -- so the expiry is computed in Python and bound.
    Deriving it from the same ``now`` the claim filters on keeps the whole
    operation on one instant."""
    conn = _Connection({"UPDATE outbox_events": (("id",), [])})
    await SqlOutboxStore(_Source(conn)).claim(limit=50, now=NOW, lease_seconds=300)

    _, params = conn.calls[0]
    assert params["now"] == NOW
    assert params["lease_until"] == NOW + timedelta(seconds=300)
    assert params["limit"] == 50


@pytest.mark.asyncio
async def test_marking_nothing_dispatched_issues_no_statement() -> None:
    """An empty ``IN`` list is a statement that can only do nothing, and issuing
    it once per sweep against a quiet outbox is a round trip per interval
    forever."""
    conn = _Connection({})
    await SqlOutboxStore(_Source(conn)).mark_dispatched([], NOW)
    assert conn.calls == []


@pytest.mark.asyncio
async def test_an_empty_outbox_reports_no_oldest_pending_rather_than_zero() -> None:
    """``None`` and ``0`` are different facts and an operator reads them as
    opposite ones: nothing is waiting, versus something is waiting and it is
    fresh."""
    conn = _Connection({"FROM outbox_events": (("age",), [(None,)])})
    store = SqlOutboxStore(_Source(conn))
    assert await store.oldest_pending_age_seconds(now=NOW) is None

    conn = _Connection({"FROM outbox_events": (("age",), [(41.5,)])})
    store = SqlOutboxStore(_Source(conn))
    assert await store.oldest_pending_age_seconds(now=NOW) == 41.5


@pytest.mark.asyncio
async def test_replaying_a_row_that_was_not_dead_reports_that_it_did_nothing() -> None:
    """Scoped to ``status = 'DEAD'`` in the SQL, so a replay cannot reset a
    backoff that is doing its job or republish an already-delivered fact. Zero
    rows means the operator's replay found nothing, and a method returning
    ``None`` would make that indistinguishable from success."""
    conn = _Connection({"UPDATE outbox_events": (("id",), [])})
    assert await SqlOutboxStore(_Source(conn)).replay(event_id=uuid.uuid4(), now=NOW) is False


# ==========================================================================
# 4. The dedupe ledger — §12
# ==========================================================================


@pytest.mark.asyncio
async def test_a_duplicate_delivery_is_raised_from_the_database_constraint() -> None:
    """Modelling the race in Python would mean re-implementing
    ``pk_processed_events`` where two coroutines can both read "absent" before
    either writes."""
    from psycopg import errors as pgerr

    from services.control_plane.app.events.consumer import DuplicateEventDeliveryError

    class _Refusing(_Connection):
        async def execute(self, sql: str, params: Any = None) -> _Cursor:
            raise pgerr.UniqueViolation("duplicate key")

    ledger = SqlProcessedEventLedger(_Refusing({}))
    with pytest.raises(DuplicateEventDeliveryError):
        await ledger.record(
            consumer_name="advocate_dispatch",
            event_id=uuid.uuid4(),
            tenant_id=TENANT,
            user_id=USER,
        )


@pytest.mark.asyncio
async def test_the_ledger_and_the_effect_share_one_transaction() -> None:
    """§12. Write the row after the effect and a crash between them leaves an
    effect nobody has a record of; write it before, in its own transaction, and
    a crash leaves a record of an effect that never happened. Both orderings
    lose, so they commit or roll back together."""
    conn = _Connection({})
    work = SqlConsumerUnitOfWork(_Source(conn))
    async with work.transaction() as ledger:
        assert conn.open_transaction is True
        await ledger.record(
            consumer_name="telemetry", event_id=uuid.uuid4(), tenant_id=TENANT, user_id=USER
        )
    assert conn.transactions == 1
    assert conn.open_transaction is False
