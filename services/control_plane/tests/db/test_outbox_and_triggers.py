"""The Phase 10 statements, run against the real schema.

Authority
---------
- ``db/migrations/versions/0006_prospective_memory.py`` and
  ``0008_events_infrastructure.py`` — the tables these statements touch.
- ``docs/specs/16_TRIGGER_DSL.md`` §7.2 (the projection queries), §10.2 (the
  fire transaction).
- ``docs/quality/23_PHASE_GATES.md`` ``G10.1`` (``D9``) and ``G10.4``.

What this file adds that the unit lane cannot
----------------------------------------------
The unit lane exercises the dispatcher and the evaluator against in-memory
doubles that *transcribe* the CHECK constraints. Transcriptions drift. This lane
runs the actual statements against the actual cluster, so three specific claims
stop being transcriptions:

1. The dispatcher's five statements parse and execute — a claim query that
   referenced a column ``0008`` does not have would pass every unit test and
   fail on first deployment.
2. ``pk_processed_events`` really does reject the second insert, which is the
   mechanism ``D9`` rests on. A Python-side dedupe would pass the unit test and
   lose the race under concurrency.
3. The result/reason partition in ``outcomes.py`` really is what
   ``ck_prospective_triggers_last_reason`` accepts, checked by writing a row
   rather than by parsing the migration's source.

Nothing here is committed. Every test rolls back, because ``provenance_ci`` is
shared with the rest of the ``db`` lane and a leftover row is a failure someone
else has to debug. Nothing here prints a DSN: :class:`MaskedDsn` from
``conftest.py`` is what the fixtures return, and no test renders one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Json

from provenance_domain.enums import TriggerReasonCode, TriggerResult
from services.control_plane.app.events.dispatcher import (
    CLAIM_SQL,
    LEASE_SECONDS,
    MARK_DEAD_SQL,
    MARK_DISPATCHED_SQL,
    MARK_FAILED_SQL,
    MAX_ATTEMPT_COUNT,
    RECLAIM_EXPIRED_SQL,
    REPLAY_SQL,
)
from services.control_plane.app.triggers.ast import parse_spec
from services.control_plane.app.triggers.outcomes import RESULT_REASONS
from services.control_plane.app.triggers.registry import resolve_field

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)
LEASE_UNTIL = NOW + timedelta(seconds=LEASE_SECONDS)


# ---------------------------------------------------------------------------
# Fixtures — a tenant, a user and a case, all rolled back.
# ---------------------------------------------------------------------------


@pytest.fixture
def owner(db_connection: psycopg.Connection) -> dict[str, Any]:
    """A minimal owner graph, created inside the test's own transaction.

    These are ``users`` and ``tenants`` rows, which the write-path table assigns
    to the app role rather than to the Kernel, so creating them from a test
    fixture is ordinary rather than an exception. They exist because
    ``fk_outbox_events_user`` and ``fk_prospective_triggers_case`` are real.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
            (tenant_id, "Phase 10 fixture tenant", f"t-{tenant_id.hex[:16]}"),
        )
        cur.execute(
            "INSERT INTO users (id, tenant_id, cognito_sub, timezone, judge_mode_enabled) "
            "VALUES (%s, %s, %s, 'America/New_York', true)",
            (user_id, tenant_id, f"sub-{user_id.hex}"),
        )
    return {"tenant_id": tenant_id, "user_id": user_id}


def _insert_outbox(
    db_connection: psycopg.Connection,
    owner: dict[str, Any],
    *,
    event_type: str = "trigger.fired.v1",
    aggregate_type: str = "TRIGGER",
    aggregate_version: int = 12,
    next_attempt_at: datetime = NOW,
    status: str = "PENDING",
    attempt_count: int = 0,
    last_error: str | None = None,
) -> uuid.UUID:
    """Seed one outbox row.

    A test fixture writing ``outbox_events`` directly is the documented
    exception the write-path linter records for test modules: the lane needs a
    row to dispatch, and routing it through the Kernel would make this a Kernel
    test rather than a dispatcher one.
    """
    event_id = uuid.uuid4()
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO outbox_events (
                id, tenant_id, user_id, aggregate_type, aggregate_id, aggregate_version,
                event_type, payload_version, payload, trace_id, status, attempt_count,
                next_attempt_at, last_error, occurred_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, '1.0', %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_id,
                owner["tenant_id"],
                owner["user_id"],
                aggregate_type,
                uuid.uuid4(),
                aggregate_version,
                event_type,
                Json({"trigger_id": str(uuid.uuid4())}),
                uuid.uuid4(),
                status,
                attempt_count,
                next_attempt_at,
                last_error,
                NOW,
                NOW,
            ),
        )
    return event_id


def _status_of(db_connection: psycopg.Connection, event_id: uuid.UUID) -> dict[str, Any]:
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT status, attempt_count, next_attempt_at, last_error, dispatched_at "
            "FROM outbox_events WHERE id = %s",
            (event_id,),
        )
        row = cur.fetchone()
    assert row is not None
    return {
        "status": row[0],
        "attempt_count": row[1],
        "next_attempt_at": row[2],
        "last_error": row[3],
        "dispatched_at": row[4],
    }


# ---------------------------------------------------------------------------
# The dispatcher's five statements, against the real table.
# ---------------------------------------------------------------------------


def test_the_claim_statement_executes_and_returns_the_columns_it_promises(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """A claim query naming a column ``0008`` lacks would pass every unit test."""
    event_id = _insert_outbox(db_connection, owner)
    with db_connection.cursor() as cur:
        cur.execute(CLAIM_SQL, {"now": NOW, "lease_until": LEASE_UNTIL, "limit": 10})
        rows = cur.fetchall()
        columns = [description.name for description in cur.description or ()]

    assert [row[0] for row in rows] == [event_id]
    assert set(columns) >= {
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
        "attempt_count",
        "occurred_at",
    }
    assert _status_of(db_connection, event_id)["status"] == "DISPATCHING"


def test_a_claimed_row_is_invisible_to_a_second_claim(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """The lease is a database fact. Two sweepers cannot dispatch one row."""
    _insert_outbox(db_connection, owner)
    with db_connection.cursor() as cur:
        cur.execute(CLAIM_SQL, {"now": NOW, "lease_until": LEASE_UNTIL, "limit": 10})
        assert len(cur.fetchall()) == 1
        cur.execute(CLAIM_SQL, {"now": NOW, "lease_until": LEASE_UNTIL, "limit": 10})
        assert cur.fetchall() == []


def test_a_row_not_yet_due_is_not_claimed(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    _insert_outbox(db_connection, owner, next_attempt_at=NOW + timedelta(minutes=5))
    with db_connection.cursor() as cur:
        cur.execute(CLAIM_SQL, {"now": NOW, "lease_until": LEASE_UNTIL, "limit": 10})
        assert cur.fetchall() == []


def test_a_dead_row_is_never_claimed(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    _insert_outbox(db_connection, owner, status="DEAD", last_error="exhausted")
    with db_connection.cursor() as cur:
        cur.execute(CLAIM_SQL, {"now": NOW, "lease_until": LEASE_UNTIL, "limit": 10})
        assert cur.fetchall() == []


def test_marking_dispatched_satisfies_the_biconditional_check(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """``ck_outbox_events_dispatched``: status and timestamp move together."""
    event_id = _insert_outbox(db_connection, owner)
    with db_connection.cursor() as cur:
        cur.execute(MARK_DISPATCHED_SQL, {"now": NOW, "event_ids": [event_id]})
    state = _status_of(db_connection, event_id)
    assert state["status"] == "DISPATCHED"
    assert state["dispatched_at"] is not None


def test_marking_dispatched_without_a_timestamp_is_refused(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """The CHECK, demonstrated rather than quoted."""
    event_id = _insert_outbox(db_connection, owner)
    with pytest.raises(psycopg.errors.CheckViolation), db_connection.cursor() as cur:
        cur.execute("UPDATE outbox_events SET status = 'DISPATCHED' WHERE id = %s", (event_id,))
    db_connection.rollback()


def test_marking_failed_records_the_backoff_and_the_reason(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    event_id = _insert_outbox(db_connection, owner)
    with db_connection.cursor() as cur:
        cur.execute(
            MARK_FAILED_SQL,
            {
                "event_id": event_id,
                "attempt_count": 1,
                "next_attempt_at": NOW + timedelta(seconds=1),
                "last_error": "TransportError: bus refused",
            },
        )
    state = _status_of(db_connection, event_id)
    assert state["status"] == "FAILED_RETRYABLE"
    assert state["attempt_count"] == 1
    assert state["last_error"] is not None


def test_a_retryable_failure_without_an_error_is_refused(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """``ck_outbox_events_dead_has_error``. An operator needs something to act on."""
    event_id = _insert_outbox(db_connection, owner)
    with pytest.raises(psycopg.errors.CheckViolation), db_connection.cursor() as cur:
        cur.execute(
            "UPDATE outbox_events SET status = 'FAILED_RETRYABLE' WHERE id = %s", (event_id,)
        )
    db_connection.rollback()


def test_the_attempt_count_cap_is_real(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """``ck_outbox_events_attempts`` caps at 5, which is why the counter saturates.

    A dispatcher that incremented past the cap would fail this UPDATE at the
    exact moment it was recording a dead letter — losing the record of the
    failure to the failure.
    """
    event_id = _insert_outbox(db_connection, owner)
    with db_connection.cursor() as cur:
        cur.execute(
            MARK_DEAD_SQL,
            {"event_id": event_id, "attempt_count": MAX_ATTEMPT_COUNT, "last_error": "exhausted"},
        )
    assert _status_of(db_connection, event_id)["attempt_count"] == MAX_ATTEMPT_COUNT

    with pytest.raises(psycopg.errors.CheckViolation), db_connection.cursor() as cur:
        cur.execute(
            MARK_DEAD_SQL,
            {"event_id": event_id, "attempt_count": MAX_ATTEMPT_COUNT + 1, "last_error": "x"},
        )
    db_connection.rollback()


def test_reclaim_returns_an_expired_lease_and_leaves_a_live_one_alone(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """Without this, a sweeper that dies mid-publish strands the row forever.

    The claim query deliberately excludes ``DISPATCHING``, so nothing else can
    ever pick it up — and a stranded ``trigger.fired.v1`` is a silently
    forgotten obligation.
    """
    event_id = _insert_outbox(db_connection, owner)
    with db_connection.cursor() as cur:
        cur.execute(CLAIM_SQL, {"now": NOW, "lease_until": LEASE_UNTIL, "limit": 10})

        cur.execute(
            RECLAIM_EXPIRED_SQL,
            {"now": NOW + timedelta(seconds=LEASE_SECONDS - 1), "last_error": "lease expired"},
        )
        assert cur.rowcount == 0

        cur.execute(
            RECLAIM_EXPIRED_SQL,
            {"now": NOW + timedelta(seconds=LEASE_SECONDS + 1), "last_error": "lease expired"},
        )
        assert cur.rowcount == 1
    assert _status_of(db_connection, event_id)["status"] == "FAILED_RETRYABLE"


def test_replay_re_arms_only_a_dead_row(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """Scoped to ``DEAD`` so it cannot reset a backoff that is doing its job."""
    dead = _insert_outbox(db_connection, owner, status="DEAD", last_error="exhausted")
    pending = _insert_outbox(db_connection, owner)
    with db_connection.cursor() as cur:
        cur.execute(REPLAY_SQL, {"event_id": dead, "now": NOW})
        assert cur.rowcount == 1
        cur.execute(REPLAY_SQL, {"event_id": pending, "now": NOW})
        assert cur.rowcount == 0

    replayed = _status_of(db_connection, dead)
    assert replayed["status"] == "PENDING"
    assert replayed["attempt_count"] == 0
    assert replayed["last_error"] is None


def test_an_invented_event_type_cannot_be_written(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """ "Do not invent event names ad hoc in consumers", enforced by the database."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_outbox(db_connection, owner, event_type="trigger.almost_fired.v1")
    db_connection.rollback()


# ---------------------------------------------------------------------------
# D9 — the dedupe is the database's, not Python's.
# ---------------------------------------------------------------------------


def test_the_second_delivery_raises_a_duplicate_key(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """``D9``'s mechanism. A Python-side check would lose this race."""
    event_id = uuid.uuid4()
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO processed_events (consumer_name, event_id, tenant_id, user_id) "
            "VALUES (%s, %s, %s, %s)",
            ("advocate.attention", event_id, owner["tenant_id"], owner["user_id"]),
        )
    with pytest.raises(psycopg.errors.UniqueViolation), db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO processed_events (consumer_name, event_id, tenant_id, user_id) "
            "VALUES (%s, %s, %s, %s)",
            ("advocate.attention", event_id, owner["tenant_id"], owner["user_id"]),
        )
    db_connection.rollback()


def test_two_consumers_may_each_process_one_event(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """The key is ``(consumer_name, event_id)``.

    Deduping on ``event_id`` alone would let whichever consumer saw an event
    first silently suppress every other subscriber.
    """
    event_id = uuid.uuid4()
    with db_connection.cursor() as cur:
        for consumer in ("advocate.attention", "projection.rebuild"):
            cur.execute(
                "INSERT INTO processed_events (consumer_name, event_id, tenant_id, user_id) "
                "VALUES (%s, %s, %s, %s)",
                (consumer, event_id, owner["tenant_id"], owner["user_id"]),
            )
        cur.execute("SELECT count(*) FROM processed_events WHERE event_id = %s", (event_id,))
        row = cur.fetchone()
    assert row is not None and row[0] == 2


def test_a_consumer_name_outside_the_shape_is_refused(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    """``ck_processed_events_consumer_shape``, which is why the name is validated
    at construction rather than at insert: this failure would roll the effect
    back with it."""
    with pytest.raises(psycopg.errors.CheckViolation), db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO processed_events (consumer_name, event_id, tenant_id, user_id) "
            "VALUES (%s, %s, %s, %s)",
            ("Advocate Attention", uuid.uuid4(), owner["tenant_id"], owner["user_id"]),
        )
    db_connection.rollback()


def test_a_result_hash_must_be_thirty_two_bytes(
    db_connection: psycopg.Connection, owner: dict[str, Any]
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO processed_events (consumer_name, event_id, result_hash) "
            "VALUES (%s, %s, %s)",
            ("advocate.attention", uuid.uuid4(), b"too short"),
        )
    db_connection.rollback()


# ---------------------------------------------------------------------------
# The trigger row: the reason partition, against the CHECK that enforces it.
# ---------------------------------------------------------------------------


@pytest.fixture
def armed_trigger(db_connection: psycopg.Connection, owner: dict[str, Any]) -> dict[str, Any]:
    """One case and one ``ARMED`` trigger carrying the hero predicate.

    The predicate is stored verbatim and then re-parsed by
    :func:`test_the_stored_predicate_round_trips_through_the_parser`, which is
    the check that matters: a predicate the database accepts but the parser
    rejects is a trigger that will ``ERROR`` at wake time, months from now, on a
    row nobody remembers writing.
    """
    from services.control_plane.tests.events._support import canon

    counterparty_id = uuid.uuid4()
    relationship_id = uuid.uuid4()
    case_id = uuid.uuid4()
    trigger_id = uuid.uuid4()
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO counterparties (id, tenant_id, normalized_name, display_name, kind) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                counterparty_id,
                owner["tenant_id"],
                f"harborview property management {counterparty_id.hex[:8]}",
                "Harborview Property Management",
                "LANDLORD",
            ),
        )
        cur.execute(
            "INSERT INTO relationships (id, tenant_id, user_id, counterparty_id, "
            "relationship_type, status) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                relationship_id,
                owner["tenant_id"],
                owner["user_id"],
                counterparty_id,
                "TENANCY",
                "ACTIVE",
            ),
        )
        cur.execute(
            """
            INSERT INTO cases (id, tenant_id, user_id, relationship_id, case_type, title,
                               status, attention_level, revision, opened_at, last_activity_at)
            VALUES (%s, %s, %s, %s, 'DEPOSIT_RETURN', 'Harborview deposit',
                    'WAITING', 'ATTENTION', 11, %s, %s)
            """,
            (case_id, owner["tenant_id"], owner["user_id"], relationship_id, NOW, NOW),
        )
        cur.execute(
            """
            INSERT INTO prospective_triggers (
                id, tenant_id, user_id, case_id, trigger_type, predicate_ast,
                not_before, expires_at, state, evaluation_version, basis_case_revision,
                schedule_name, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'COMMITMENT_DEADLINE', %s, %s, %s,
                    'ARMED', 1, 11, %s, now(), now())
            """,
            (
                trigger_id,
                owner["tenant_id"],
                owner["user_id"],
                case_id,
                Json(canon.hero_predicate_document()),
                canon.TRIGGER_WAKE_AT,
                canon.TRIGGER_WAKE_AT + timedelta(days=365),
                f"pv-trg-{trigger_id.hex}-v1",
            ),
        )
    return {**owner, "case_id": case_id, "trigger_id": trigger_id}


def test_the_stored_predicate_round_trips_through_the_parser(
    db_connection: psycopg.Connection, armed_trigger: dict[str, Any]
) -> None:
    """JSONB in, the same fully typed tree out.

    ``predicate_ast`` is JSONB rather than STRING precisely so this is possible:
    a string column would make "the model wrote a little expression and we eval
    it" a one-line change six months from now.
    """
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT predicate_ast FROM prospective_triggers WHERE id = %s",
            (armed_trigger["trigger_id"],),
        )
        row = cur.fetchone()
    assert row is not None
    spec = parse_spec(row[0], resolve_field)
    assert spec.referenced_paths == (
        "case.status",
        "clock.now",
        "commitments.deposit.due_at",
        "commitments.deposit.outstanding_amount",
        "commitments.deposit.status",
    )


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (result.value, reason.value)
        for result, reasons in RESULT_REASONS.items()
        for reason in sorted(reasons, key=lambda member: member.value)
    ],
)
def test_every_legal_pairing_is_accepted_by_the_check(
    db_connection: psycopg.Connection,
    armed_trigger: dict[str, Any],
    result: str,
    reason: str,
) -> None:
    """``outcomes.RESULT_REASONS`` against ``ck_prospective_triggers_last_reason``.

    Written as a row rather than parsed from the migration's source: a pairing
    this module thinks legal and the database refuses would fail inside the fire
    transaction, at the moment the row was needed.
    """
    fired_clause = ", fired_at = now()" if result == TriggerResult.FIRED.value else ""
    state = {"FIRED": "FIRED", "DISARMED": "DISARMED", "EXPIRED": "EXPIRED"}.get(result, "ARMED")
    with db_connection.cursor() as cur:
        cur.execute(
            f"""
            UPDATE prospective_triggers
               SET last_result = %s, last_reason_code = %s, last_evaluated_at = now(),
                   state = %s{fired_clause}
             WHERE id = %s
            """,
            (result, reason, state, armed_trigger["trigger_id"]),
        )
        assert cur.rowcount == 1
    db_connection.rollback()


def test_a_plausible_but_illegal_pairing_is_refused(
    db_connection: psycopg.Connection, armed_trigger: dict[str, Any]
) -> None:
    """``DISARMED`` with ``PREDICATE_FALSE`` reads perfectly and means nothing."""
    with pytest.raises(psycopg.errors.CheckViolation), db_connection.cursor() as cur:
        cur.execute(
            "UPDATE prospective_triggers SET last_result = %s, last_reason_code = %s, "
            "last_evaluated_at = now(), state = 'DISARMED' WHERE id = %s",
            (
                TriggerResult.DISARMED.value,
                TriggerReasonCode.PREDICATE_FALSE.value,
                armed_trigger["trigger_id"],
            ),
        )
    db_connection.rollback()


def test_a_disarmed_trigger_cannot_carry_a_fired_at(
    db_connection: psycopg.Connection, armed_trigger: dict[str, Any]
) -> None:
    """``ck_prospective_triggers_fired`` is a biconditional, and D8 needs it.

    Without it, ``fired_at IS NULL`` on a disarmed trigger passes on a bug: a
    trigger that fired and then failed to stamp the time is indistinguishable
    from one that never fired.
    """
    with pytest.raises(psycopg.errors.CheckViolation), db_connection.cursor() as cur:
        cur.execute(
            "UPDATE prospective_triggers SET state = 'DISARMED', fired_at = now(), "
            "last_result = 'DISARMED', last_reason_code = 'CASE_RESOLVED', "
            "last_evaluated_at = now() WHERE id = %s",
            (armed_trigger["trigger_id"],),
        )
    db_connection.rollback()


def test_an_evaluation_without_a_result_is_refused(
    db_connection: psycopg.Connection, armed_trigger: dict[str, Any]
) -> None:
    """``ck_prospective_triggers_evaluated``: an unexplained evaluation is a bug.

    §23.8 in database form — "an unexplained NOOP is a gate failure even though
    nothing broke".
    """
    with pytest.raises(psycopg.errors.CheckViolation), db_connection.cursor() as cur:
        cur.execute(
            "UPDATE prospective_triggers SET last_evaluated_at = now() WHERE id = %s",
            (armed_trigger["trigger_id"],),
        )
    db_connection.rollback()


def test_the_due_sweep_index_query_finds_the_armed_trigger(
    db_connection: psycopg.Connection, armed_trigger: dict[str, Any]
) -> None:
    """§11.6: the database, not a scheduler, is the source of truth about what is due.

    This is the query ``idx_prospective_triggers_due`` exists to serve, and it is
    the backstop that makes a lost schedule survivable.
    """
    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM prospective_triggers
             WHERE state = 'ARMED'
               AND (not_before IS NULL OR not_before <= %s)
             ORDER BY not_before
             LIMIT 100
            """,
            (NOW,),
        )
        ids = [row[0] for row in cur.fetchall()]
    assert armed_trigger["trigger_id"] in ids


def test_money_survives_the_round_trip_as_decimal(
    db_connection: psycopg.Connection, armed_trigger: dict[str, Any]
) -> None:
    """``NUMERIC(20,4)`` in, ``Decimal`` out. Never a float, at any boundary."""
    with db_connection.cursor() as cur:
        cur.execute("SELECT %s::DECIMAL(20,4)", (Decimal("1800.0000"),))
        row = cur.fetchone()
    assert row is not None
    assert isinstance(row[0], Decimal)
    assert row[0] == Decimal("1800.0000")
