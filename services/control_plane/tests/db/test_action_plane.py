"""DDL section 19 test 7, against the built schema -- ``T9.4``-``T9.6``.

Authority
---------
- ``db/migrations/versions/0007_action_plane.py`` -- the constraints asserted
  here are the ones that file declares, by name.
- ``docs/specs/10_DATABASE_DDL.md`` sections 10, 13 and 19 test 7.
- ``docs/quality/23_PHASE_GATES.md`` ``G9.1``, ``G9.2``, ``G9.4``, ``G9.6``.

Why this file exists beside ``tests/actions/``
-----------------------------------------------
``tests/actions/`` decides the action sequence in the hermetic lane, against
``InMemoryActionStore``. That proves the *logic*. It cannot prove the two
things that make the logic load-bearing:

1. **The refusals are also schema refusals.** A Python guard is one refactor
   away from being untrue; ``ck_action_intents_execution_needs_approval`` is
   not. ``0007``'s docstring puts it exactly right -- an unapproved execution is
   not *prevented* by the schema, it is *unrepresentable* in it -- and a
   constraint nobody ever hit is a constraint nobody knows still exists.
2. **The SQL is valid against the real tables.** Every statement in
   ``store_postgres.py`` names columns from ``0007``. A typo in one of them is
   invisible to the unit lane and fatal in the demo.

``G9.1``'s "zero rows" is a database claim
-------------------------------------------
``T9.4``'s first sub-task requires staleness to be "expressed as zero rows
rather than as a Python branch that can be skipped".
:data:`~services.control_plane.app.actions.store_postgres.EXECUTOR_CLAIM_SQL`
is that expression and it is run here twice against a real cluster: once at the
revision the approval was bound to, and once after an unrelated commit moved
it. One row, then none.

Credential hygiene
------------------
Nothing here prints a DSN. The ``test_dsn`` fixture returns
``MaskedDsn``, whose ``repr`` is what pytest writes into a failure header.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from datetime import datetime

import psycopg
import pytest
from psycopg.types.json import Json

from services.control_plane.app.actions import drafts, executor
from services.control_plane.app.actions.store_postgres import (
    EXECUTOR_CLAIM_SQL,
    GROUNDING_SNAPSHOT_SQL,
    INSERT_EXECUTION_SQL,
    INSERT_INTENT_SQL,
    RECORD_APPROVAL_SQL,
)
from services.control_plane.app.actions.support_validation import COMMITTED_DECISIONS

pytestmark = pytest.mark.db

#: SQLSTATE 23514, ``check_violation``, and 23505, ``unique_violation``.
CHECK_VIOLATION_SQLSTATE = "23514"
UNIQUE_VIOLATION_SQLSTATE = "23505"

#: The hero draft, reduced to what a digest needs. The bytes do not matter;
#: that both sides hash the *same* bytes is the whole assertion.
DRAFT_PAYLOAD = {
    "subject": "Disputed invoice 88431 - service terminated 31 May 2026",
    "body": "Hello,\n\nPlease cancel invoice 88431.\n\nAlex Rivera",
}


class _Spine:
    """The identity rows one ``action_intents`` row's foreign keys demand."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.counterparty_id = uuid.uuid4()
        self.relationship_id = uuid.uuid4()
        self.case_id = uuid.uuid4()
        self.belief_id = uuid.uuid4()
        self.belief_version_id = uuid.uuid4()
        self.decision_id = uuid.uuid4()


def _seed_spine(cursor: psycopg.Cursor, *, now: datetime, revision: int = 13) -> _Spine:
    """Tenant, user, counterparty, relationship, case, and a committed decision.

    Written out rather than imported from ``test_kernel_required.py``:
    ``--import-mode=importlib`` gives test modules no shared package, and the
    alternative -- a ``_support`` package on ``sys.path`` -- would collide with
    the one ``tests/api/`` already installs under that name.
    """
    s = _Spine()
    cursor.execute(
        "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
        (s.tenant_id, "G9 fixture tenant", f"t-{s.tenant_id.hex[:16]}"),
    )
    cursor.execute(
        "INSERT INTO users (id, tenant_id, cognito_sub) VALUES (%s, %s, %s)",
        (s.user_id, s.tenant_id, f"sub-{s.user_id.hex}"),
    )
    cursor.execute(
        "INSERT INTO counterparties (id, tenant_id, normalized_name, display_name, kind) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            s.counterparty_id,
            s.tenant_id,
            f"northline fiber {s.counterparty_id.hex[:8]}",
            "Northline Fiber",
            "UTILITY",
        ),
    )
    cursor.execute(
        "INSERT INTO relationships (id, tenant_id, user_id, counterparty_id, relationship_type,"
        " status) VALUES (%s, %s, %s, %s, %s, %s)",
        (s.relationship_id, s.tenant_id, s.user_id, s.counterparty_id, "SERVICE_ACCOUNT", "ACTIVE"),
    )
    cursor.execute(
        "INSERT INTO cases (id, tenant_id, user_id, relationship_id, case_type, title, status,"
        " attention_level, revision, opened_at, last_activity_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            s.case_id,
            s.tenant_id,
            s.user_id,
            s.relationship_id,
            "BILLING_DISPUTE",
            "G9 fixture case",
            "DISPUTED",
            "ATTENTION",
            revision,
            now,
            now,
        ),
    )
    _seed_committed_decision(cursor, s, now=now)
    _seed_belief_version(cursor, s, now=now)
    return s


def _seed_committed_decision(cursor: psycopg.Cursor, s: _Spine, *, now: datetime) -> None:
    """An ``ACCEPTED`` decision with ``committed_at`` set: the basis exists.

    Both halves matter. A ``kernel_decisions`` row exists for every outcome
    including the rejections, so the decision value is checked; and
    ``committed_at`` is what separates a settled transaction from one that was
    opened and abandoned.
    """
    proposal_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO memory_proposals (id, tenant_id, user_id, trace_id, schema_version,"
        " proposal_type, source_artifact_ids, evidence_ids, payload, payload_sha256,"
        " model_id, prompt_version, status, decided_at) VALUES (%s, %s, %s, %s, '1.0',"
        " 'SEED_FIXTURE', %s, %s, %s, %s, 'deterministic.kernel', 'pv-seed-1.0.0',"
        " 'ACCEPTED', %s)",
        (
            proposal_id,
            s.tenant_id,
            s.user_id,
            uuid.uuid4(),
            Json([]),
            Json([]),
            Json({"fixture": "test_action_plane"}),
            hashlib.sha256(proposal_id.bytes).digest(),
            now,
        ),
    )
    cursor.execute(
        "INSERT INTO kernel_decisions (id, tenant_id, user_id, proposal_id, case_id, decision,"
        " reason_codes, retry_count, transaction_opened, trace_id, committed_at)"
        " VALUES (%s, %s, %s, %s, %s, 'ACCEPTED', %s, 0, true, %s, %s)",
        (
            s.decision_id,
            s.tenant_id,
            s.user_id,
            proposal_id,
            s.case_id,
            Json(["BELIEF_CREATED"]),
            uuid.uuid4(),
            now,
        ),
    )


def _seed_belief_version(cursor: psycopg.Cursor, s: _Spine, *, now: datetime) -> None:
    """One current belief version, so a supporting citation can be current.

    ``DETERMINISTIC_DERIVATION`` with ``support_edge_count = 0`` satisfies
    ``ck_belief_versions_grounded`` without an evidence spine. That is not a
    shortcut around invariant 5: a derived value's justification *is* its
    derivation, which is exactly what the constraint says.
    """
    cursor.execute(
        "INSERT INTO beliefs (id, tenant_id, user_id, subject_type, subject_id, predicate)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (s.belief_id, s.tenant_id, s.user_id, "RELATIONSHIP", s.relationship_id, "service_ended"),
    )
    cursor.execute(
        "INSERT INTO belief_versions (id, tenant_id, user_id, belief_id, version_no, value_type,"
        " value_json, epistemic_status, belief_confidence, derivation_kind, support_edge_count,"
        " recorded_at, kernel_decision_id)"
        " VALUES (%s, %s, %s, %s, 1, 'DATE', %s, 'CONFIRMED', '0.9000',"
        " 'DETERMINISTIC_DERIVATION', 0, %s, %s)",
        (
            s.belief_version_id,
            s.tenant_id,
            s.user_id,
            s.belief_id,
            Json("2026-05-31"),
            now,
            s.decision_id,
        ),
    )


def _insert_intent(
    cursor: psycopg.Cursor,
    s: _Spine,
    *,
    now: datetime,
    status: str = "PROPOSED",
    payload: dict[str, object] | None = None,
    approval_sha256: bytes | None = None,
    basis_case_revision: int = 13,
) -> uuid.UUID:
    """One ``action_intents`` row through the production INSERT statement."""
    body = payload if payload is not None else DRAFT_PAYLOAD
    intent_id = uuid.uuid4()
    digest = drafts.draft_digest(body)
    cursor.execute(
        INSERT_INTENT_SQL,
        {
            "id": intent_id,
            "tenant_id": s.tenant_id,
            "user_id": s.user_id,
            "case_id": s.case_id,
            "action_type": "OUTBOUND_EMAIL_DISPUTE",
            "recipient": "billing@northlinefiber.example",
            "draft_payload": Json(body),
            "draft_sha256": digest,
            "rationale": "A counterparty claim asserts billable service in a terminated period.",
            "supporting_belief_versions": Json([str(s.belief_version_id)]),
            "basis_case_revision": basis_case_revision,
            "status": status,
            "risk_tier": 3,
            "created_by_agent_run_id": None,
            "idempotency_key": drafts.mint_idempotency_key(
                tenant_id=s.tenant_id,
                user_id=s.user_id,
                case_id=s.case_id,
                action_type="OUTBOUND_EMAIL_DISPUTE",
                draft_sha256=digest,
            ),
            "now": now,
        },
    )
    if approval_sha256 is not None:
        cursor.execute(
            RECORD_APPROVAL_SQL,
            {
                "tenant_id": s.tenant_id,
                "user_id": s.user_id,
                "intent_id": intent_id,
                "draft_payload": Json(body),
                "draft_sha256": approval_sha256,
                "approved_by_user_id": s.user_id,
                "approved_at": now,
                "basis_case_revision": basis_case_revision,
            },
        )
    return intent_id


@pytest.fixture
def spine(db_connection: psycopg.Connection, frozen_clock) -> Iterator[_Spine]:
    """A seeded spine that is rolled back when the test ends.

    ``provenance_ci`` is the only database this lane may touch and the
    ``db_connection`` fixture is not autocommit, so every row written here
    disappears on rollback. The demo corpus in ``provenance`` is never opened.
    """
    with db_connection.cursor() as cursor:
        yield _seed_spine(cursor, now=frozen_clock.now())


# ==========================================================================
# The schema's own refusals
# ==========================================================================


def test_an_execution_state_without_an_approval_hash_is_unrepresentable(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``ck_action_intents_execution_needs_approval``.

    Invariant 4 as a CHECK rather than as a code path. The distinction is the
    whole difference between a guarantee and a convention: a convention is one
    refactor away from being untrue, and nothing about a refactor makes a CHECK
    stop firing.
    """
    with db_connection.cursor() as cursor, pytest.raises(psycopg.errors.CheckViolation) as raised:
        _insert_intent(cursor, spine, now=frozen_clock.now(), status="EXECUTED")

    assert raised.value.sqlstate == CHECK_VIOLATION_SQLSTATE
    assert raised.value.diag.constraint_name == "ck_action_intents_execution_needs_approval"


def test_an_approval_missing_any_of_its_three_facts_is_refused(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``ck_action_intents_approval_complete``: who, when, and exactly what.

    Two out of three is the shape a half-finished approval flow leaves behind,
    and it is the shape that lets an execution claim consent nobody gave.
    """
    now = frozen_clock.now()
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now)
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            cursor.execute(
                "UPDATE action_intents SET approved_at = %s, approved_by_user_id = %s"
                " WHERE id = %s",
                (now, spine.user_id, intent_id),
            )

    assert raised.value.diag.constraint_name == "ck_action_intents_approval_complete"


def test_a_second_successful_send_is_refused_by_the_index(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``uq_action_executions_single_success`` -- ``G9.4``'s hard stop.

    The application checks for a prior success before calling the provider, and
    that check is the cheap one. This is the one that holds when two executor
    instances race: attempts are free, success is once.
    """
    now = frozen_clock.now()
    digest = drafts.draft_digest(DRAFT_PAYLOAD)
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=digest)
        for attempt in (1, 2):
            params = {
                "id": uuid.uuid4(),
                "tenant_id": spine.tenant_id,
                "user_id": spine.user_id,
                "action_intent_id": intent_id,
                "attempt_no": attempt,
                "provider": "SAFE_SINK",
                "provider_correlation_id": f"demo-sink-{attempt}",
                "request_sha256": digest,
                "revalidated_case_revision": 13,
                "status": "SUCCEEDED",
                "error_code": None,
                "started_at": now,
                "finished_at": now,
            }
            if attempt == 1:
                cursor.execute(INSERT_EXECUTION_SQL, params)
                continue
            with pytest.raises(psycopg.errors.UniqueViolation) as raised:
                cursor.execute(INSERT_EXECUTION_SQL, params)

    assert raised.value.sqlstate == UNIQUE_VIOLATION_SQLSTATE


def test_a_retryable_failure_and_a_success_coexist_on_one_intent(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """The partial index is ``WHERE status = 'SUCCEEDED'``, and that matters.

    ``UNIQUE (action_intent_id)`` would refuse a retry after a genuinely
    retryable failure, so a transient provider error would become permanent.
    Both attempts land; only one of them sent anything.
    """
    now = frozen_clock.now()
    digest = drafts.draft_digest(DRAFT_PAYLOAD)
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=digest)
        for attempt, status, correlation, error in (
            (1, "FAILED_RETRYABLE", None, "PROVIDER_TRANSIENT"),
            (2, "SUCCEEDED", "demo-sink-0002", None),
        ):
            cursor.execute(
                INSERT_EXECUTION_SQL,
                {
                    "id": uuid.uuid4(),
                    "tenant_id": spine.tenant_id,
                    "user_id": spine.user_id,
                    "action_intent_id": intent_id,
                    "attempt_no": attempt,
                    "provider": "SAFE_SINK",
                    "provider_correlation_id": correlation,
                    "request_sha256": digest,
                    "revalidated_case_revision": 13,
                    "status": status,
                    "error_code": error,
                    "started_at": now,
                    "finished_at": now,
                },
            )
        cursor.execute(
            "SELECT attempt_no, status FROM action_executions"
            " WHERE action_intent_id = %s ORDER BY attempt_no",
            (intent_id,),
        )
        assert cursor.fetchall() == [(1, "FAILED_RETRYABLE"), (2, "SUCCEEDED")]


def test_risk_tier_four_cannot_be_stored(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``ck_action_intents_tier4_blocked``.

    A schema that could hold a tier-4 intent invites a config flag that enables
    one. It cannot, so there is nothing for the flag to turn on.
    """
    now = frozen_clock.now()
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now)
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            cursor.execute("UPDATE action_intents SET risk_tier = 4 WHERE id = %s", (intent_id,))

    assert raised.value.diag.constraint_name == "ck_action_intents_tier4_blocked"


# ==========================================================================
# DDL section 19 test 7 -- the executor query, run for real
# ==========================================================================


def test_the_executor_query_claims_a_fresh_approval(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """One row, at the revision the approval was bound to."""
    now = frozen_clock.now()
    digest = drafts.draft_digest(DRAFT_PAYLOAD)
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=digest)
        cursor.execute(
            EXECUTOR_CLAIM_SQL,
            {
                "tenant_id": spine.tenant_id,
                "user_id": spine.user_id,
                "intent_id": intent_id,
                "expected_draft_sha256": digest,
                "expected_case_revision": 13,
            },
        )
        assert len(cursor.fetchall()) == 1


def test_a_stale_approval_claims_zero_rows(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``G9.1`` / DDL section 19 test 7, on the **revision** axis alone.

    Approve at ``basis_case_revision = 13``, commit an unrelated change moving
    the case to 14, run the executor query: **zero rows**. The draft is
    untouched, so the only thing that could have refused it is the revision
    binding.
    """
    now = frozen_clock.now()
    digest = drafts.draft_digest(DRAFT_PAYLOAD)
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=digest)
        cursor.execute("UPDATE cases SET revision = 14 WHERE id = %s", (spine.case_id,))
        cursor.execute(
            EXECUTOR_CLAIM_SQL,
            {
                "tenant_id": spine.tenant_id,
                "user_id": spine.user_id,
                "intent_id": intent_id,
                "expected_draft_sha256": digest,
                "expected_case_revision": 14,
            },
        )
        assert cursor.fetchall() == []


def test_an_edited_draft_claims_zero_rows(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``G9.2`` on the **draft-hash** axis alone.

    The case revision never moves. The stored payload gains one sentence, its
    digest stops matching ``approval_draft_sha256``, and the query that decides
    whether anything may be sent returns nothing.
    """
    now = frozen_clock.now()
    approved_digest = drafts.draft_digest(DRAFT_PAYLOAD)
    edited = {**DRAFT_PAYLOAD, "body": DRAFT_PAYLOAD["body"] + " P.S. I expect compensation."}
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=approved_digest)
        cursor.execute(
            "UPDATE action_intents SET draft_payload = %s WHERE id = %s",
            (Json(edited), intent_id),
        )
        cursor.execute(
            EXECUTOR_CLAIM_SQL,
            {
                "tenant_id": spine.tenant_id,
                "user_id": spine.user_id,
                "intent_id": intent_id,
                # What the caller believes it is sending -- the edited draft.
                "expected_draft_sha256": drafts.draft_digest(edited),
                "expected_case_revision": 13,
            },
        )
        assert cursor.fetchall() == []


def test_a_successful_execution_makes_the_claim_return_nothing(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """ "No ``action_executions`` row exists with ``status = 'SUCCEEDED'``".

    Idempotency expressed where the executor reads it rather than only where
    the index enforces it: the second attempt never becomes a claim at all.
    """
    now = frozen_clock.now()
    digest = drafts.draft_digest(DRAFT_PAYLOAD)
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=digest)
        cursor.execute(
            INSERT_EXECUTION_SQL,
            {
                "id": uuid.uuid4(),
                "tenant_id": spine.tenant_id,
                "user_id": spine.user_id,
                "action_intent_id": intent_id,
                "attempt_no": 1,
                "provider": "SAFE_SINK",
                "provider_correlation_id": "demo-sink-0001",
                "request_sha256": digest,
                "revalidated_case_revision": 13,
                "status": "SUCCEEDED",
                "error_code": None,
                "started_at": now,
                "finished_at": now,
            },
        )
        cursor.execute(
            EXECUTOR_CLAIM_SQL,
            {
                "tenant_id": spine.tenant_id,
                "user_id": spine.user_id,
                "intent_id": intent_id,
                "expected_draft_sha256": digest,
                "expected_case_revision": 13,
            },
        )
        assert cursor.fetchall() == []


def test_another_users_approval_is_not_claimable(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """The scoping predicate is in the SQL, not in the caller's discipline."""
    now = frozen_clock.now()
    digest = drafts.draft_digest(DRAFT_PAYLOAD)
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=digest)
        cursor.execute(
            EXECUTOR_CLAIM_SQL,
            {
                "tenant_id": spine.tenant_id,
                "user_id": uuid.uuid4(),
                "intent_id": intent_id,
                "expected_draft_sha256": digest,
                "expected_case_revision": 13,
            },
        )
        assert cursor.fetchall() == []


# ==========================================================================
# The snapshot statement -- invariant 4's committed-basis question
# ==========================================================================


def test_the_snapshot_reports_a_committed_basis_and_the_current_versions(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``GROUNDING_SNAPSHOT_SQL`` answers the revision and the basis at once.

    One statement, so the two describe one instant. Two statements would
    describe two, and the window between them is the one an irreversible
    operation must not have.
    """
    with db_connection.cursor() as cursor:
        cursor.execute(
            GROUNDING_SNAPSHOT_SQL,
            {
                "tenant_id": spine.tenant_id,
                "user_id": spine.user_id,
                "case_id": spine.case_id,
                "committed_decisions": sorted(COMMITTED_DECISIONS),
            },
        )
        row = cursor.fetchone()

    assert row is not None
    case_id, revision, has_basis, current_versions = row
    assert case_id == spine.case_id
    assert revision == 13
    assert has_basis is True
    assert spine.belief_version_id in current_versions


def test_a_case_whose_only_decision_was_rejected_has_no_committed_basis(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``G9.6``'s second clause, at the database.

    A REJECTED proposal committed nothing. The snapshot says so, and
    ``ActionIntentService.create`` refuses on the strength of it -- which is
    why "a REJECTED proposal cannot produce an ``ActionIntent`` at all" is a
    property of the data rather than of a special case in a handler.
    """
    with db_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE kernel_decisions SET decision = 'REJECTED_INVARIANT' WHERE id = %s",
            (spine.decision_id,),
        )
        cursor.execute(
            GROUNDING_SNAPSHOT_SQL,
            {
                "tenant_id": spine.tenant_id,
                "user_id": spine.user_id,
                "case_id": spine.case_id,
                "committed_decisions": sorted(COMMITTED_DECISIONS),
            },
        )
        row = cursor.fetchone()

    assert row is not None
    assert row[2] is False


# ==========================================================================
# The digest crosses the boundary unchanged
# ==========================================================================


def test_the_stored_digest_round_trips_as_thirty_two_bytes(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``ck_action_intents_draft_sha`` is ``length(draft_sha256) = 32``.

    The digest computed in Python is the digest the executor re-reads. A hex
    string stored in a ``BYTES`` column would be 64 bytes, would fail the
    CHECK, and would fail it in whichever phase first tried -- which is this
    one, on purpose.
    """
    now = frozen_clock.now()
    expected = drafts.draft_digest(DRAFT_PAYLOAD)
    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=expected)
        cursor.execute(
            "SELECT draft_sha256, approval_draft_sha256, draft_payload"
            " FROM action_intents WHERE id = %s",
            (intent_id,),
        )
        row = cursor.fetchone()

    assert row is not None
    stored_draft, stored_approval, payload = row
    assert bytes(stored_draft) == expected
    assert bytes(stored_approval) == expected
    assert len(bytes(stored_draft)) == 32
    assert drafts.draft_digest(payload) == expected


# ==========================================================================
# Every status the executor writes is a status the table admits
# ==========================================================================


def test_every_ledger_status_the_executor_writes_is_accepted_by_the_table(
    db_connection: psycopg.Connection, spine: _Spine, frozen_clock
) -> None:
    """``ck_action_executions_status`` admits five values and no more.

    This exists because of an asymmetry that looks like a defect and is not.
    A refusal for ``NO_COMMITTED_BASIS`` is recorded with
    ``status = 'ABORTED_STALE'`` even though nothing about the intent was
    stale -- because ``ABORTED_STALE`` is the *only* terminal-refusal value
    ``0007`` admits. ``error_code`` is the field that carries which of the
    seven blocking reasons fired; ``status`` answers the coarser question the
    schema has room for.

    The obvious "fix" is a sixth status naming the reason. It would be
    rejected by the database at runtime -- a ``23514`` during a demo, on the
    one operation that cannot be undone -- and migrations past ``0008`` are
    not this phase's to write. So the executor publishes the set it writes,
    and this test drives every member through the real table. Adding a status
    without adding it to `WRITTEN_EXECUTION_STATUSES` leaves it untested;
    adding one the schema does not admit fails here instead of in the demo.
    """
    now = frozen_clock.now()
    digest = drafts.draft_digest(DRAFT_PAYLOAD)
    assert set(executor.WRITTEN_EXECUTION_STATUSES) == {
        "STARTED",
        "SUCCEEDED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "ABORTED_STALE",
    }

    with db_connection.cursor() as cursor:
        intent_id = _insert_intent(cursor, spine, now=now, approval_sha256=digest)
        for attempt, status in enumerate(executor.WRITTEN_EXECUTION_STATUSES, start=1):
            terminal = status != "STARTED"
            cursor.execute(
                INSERT_EXECUTION_SQL,
                {
                    "id": uuid.uuid4(),
                    "tenant_id": spine.tenant_id,
                    "user_id": spine.user_id,
                    "action_intent_id": intent_id,
                    "attempt_no": attempt,
                    "provider": "SAFE_SINK",
                    # ck_action_executions_success_has_correlation
                    "provider_correlation_id": (
                        f"demo-sink-{attempt}" if status == "SUCCEEDED" else None
                    ),
                    "request_sha256": digest,
                    "revalidated_case_revision": 13,
                    "status": status,
                    # ck_action_executions_error
                    "error_code": (
                        executor.NO_COMMITTED_BASIS
                        if status in ("FAILED_RETRYABLE", "FAILED_FINAL", "ABORTED_STALE")
                        else None
                    ),
                    "started_at": now,
                    # ck_action_executions_terminal
                    "finished_at": now if terminal else None,
                },
            )
        cursor.execute(
            "SELECT count(*) FROM action_executions WHERE action_intent_id = %s", (intent_id,)
        )
        assert cursor.fetchone() == (len(executor.WRITTEN_EXECUTION_STATUSES),)
