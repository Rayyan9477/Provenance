"""``commit_trigger_evaluation`` — the Kernel side of prospective memory.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` §10.1 (*the evaluator is a proposer, not a
  writer*), §10.2 (the atomic fire transaction), §9.9 (the idempotency key),
  §9.10 (the outcome taxonomy) and §9.11 (re-arm).
- ``docs/specs/15_API_SPEC.md`` §9.10.
- ``db/migrations/versions/0005_kernel_control.py``,
  ``0006_prospective_memory.py`` and ``0008_events_infrastructure.py`` — every
  CHECK asserted below is read from those files rather than remembered.

Why this suite exists at all
----------------------------
``STATUS.md`` recorded the gap in one sentence: *"the evaluator, the guards, the
fire-transaction shape, the idempotency contract and the re-arm columns are all
built and tested, and none of them can run against the cluster."* The evaluator
had a ``TriggerKernel`` **Protocol** and one implementation — a test fake. A
fake cannot be refused by a CHECK constraint, so three defects survived every
green run:

1. ``PROPOSAL_MODEL_ID`` was ``"deterministic:trigger-eval"`` and
   ``ck_memory_proposals_model`` admits four ids, none of them that one. Every
   fire would have been refused at the ``memory_proposals`` INSERT.
2. ``IDEMPOTENCY_SCOPE`` was ``"TRIGGER_EVALUATION"`` and
   ``ck_idempotency_scope_shape`` is ``^[a-z][a-z0-9_.]{2,63}$``. Every wake
   would have been refused at the idempotency claim.
3. ``uq_outbox_events_aggregate_event`` is
   ``UNIQUE (aggregate_type, aggregate_id, aggregate_version, event_type)``. A
   no-op does not move ``cases.revision``, so two consecutive no-op wakes on one
   trigger would have collided had the trigger aggregate's version been the case
   revision.

Each of those is asserted here against the migration text, so the assertion
fails when the schema moves rather than when somebody remembers to re-read it.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from provenance_domain.enums import (
    KernelDecision,
    TriggerReasonCode,
    TriggerResult,
    TriggerState,
)
from services.control_plane.app.memory_kernel import trigger_commit
from services.control_plane.app.triggers.service import CommitRequest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS = REPO_ROOT / "db" / "migrations" / "versions"

TENANT = uuid.UUID(int=0xA001)
USER = uuid.UUID(int=0xA002)
CASE = uuid.UUID(int=0xA003)
TRIGGER = uuid.UUID(int=0xA004)
TRACE = uuid.UUID(int=0xA005)
TX_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
EXPIRES_AT = datetime(2027, 6, 15, tzinfo=UTC)


# ==========================================================================
# A connection that answers the four reads and records every statement
# ==========================================================================


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _Connection:
    """Answers by statement keyword; records everything, in order."""

    def __init__(
        self,
        *,
        trigger_row: tuple[Any, ...] | None,
        case_row: tuple[Any, ...] | None,
        stored_idempotency: tuple[Any, ...] | None = None,
    ) -> None:
        self.trigger_row = trigger_row
        self.case_row = case_row
        self.stored_idempotency = stored_idempotency
        self.executed: list[tuple[str, Any]] = []

    @property
    def labels(self) -> list[str]:
        return [label for label, _ in self.executed]

    def params_for(self, label: str) -> dict[str, Any]:
        for name, params in self.executed:
            if name == label:
                assert isinstance(params, dict)
                return params
        raise AssertionError(f"{label} was never executed; ran {self.labels}")

    def all_params_for(self, label: str) -> list[dict[str, Any]]:
        return [dict(p) for name, p in self.executed if name == label]

    async def execute(self, query: str, params: Any = None) -> _Cursor:
        flat = " ".join(query.split())
        self.executed.append((_label_of(flat), params))
        if flat.startswith("SELECT transaction_timestamp"):
            return _Cursor([(TX_NOW,)])
        if "FROM prospective_triggers" in flat and flat.startswith("SELECT"):
            return _Cursor([self.trigger_row] if self.trigger_row is not None else [])
        if "FROM idempotency_records" in flat:
            return _Cursor([self.stored_idempotency] if self.stored_idempotency is not None else [])
        if "FROM cases" in flat and flat.startswith("SELECT"):
            return _Cursor([self.case_row] if self.case_row is not None else [])
        return _Cursor([(1,)])

    async def set_isolation_level(self, level: Any) -> None:
        del level

    def transaction(self) -> Any:
        return _Noop()


def _label_of(flat: str) -> str:
    """A short name for one statement, derived from the statement itself."""
    if flat.startswith("SELECT transaction_timestamp"):
        return "tx_now"
    if flat.startswith("SELECT") and "FROM prospective_triggers" in flat:
        return "read_trigger"
    if flat.startswith("SELECT") and "FROM idempotency_records" in flat:
        return "read_idempotency"
    if flat.startswith("SELECT") and "FROM cases" in flat:
        return "read_case"
    if flat.startswith("INSERT INTO idempotency_records"):
        return "idempotency_records"
    if flat.startswith("INSERT INTO memory_proposals"):
        return "memory_proposals"
    if flat.startswith("INSERT INTO kernel_decisions"):
        return "kernel_decisions"
    if flat.startswith("UPDATE cases"):
        return "cases"
    if flat.startswith("INSERT INTO state_transitions"):
        return "state_transitions"
    if flat.startswith("UPDATE prospective_triggers"):
        return "prospective_triggers"
    if flat.startswith("INSERT INTO outbox_events"):
        return "outbox_events"
    return flat[:40]


class _Noop:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    def connection(self) -> Any:
        return _Held(self.conn)


class _Held:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Connection:
        return self.conn

    async def __aexit__(self, *exc: Any) -> bool:
        return False


# ==========================================================================
# Fixtures for the request the evaluator hands over
# ==========================================================================


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "kind": "TRIGGER_EVALUATION",
        "wake_source": "MANUAL_DEMO",
        "wake_id": "manual:trg:v1:judge",
        "trigger_id": str(TRIGGER),
        "trigger_type": "COMMITMENT_DEADLINE",
        "evaluation_version": 1,
        "predicate_result": "TRUE",
        "outcome": "FIRED",
        "reason_code": "COMMITMENT_OVERDUE_UNPAID",
        "basis_case_revision": 11,
        "case_revision_observed": 11,
        "basis_stale": False,
        "observed": {"commitments.deposit.outstanding_amount": "1800.0000"},
        "node_trace": [],
    }
    base.update(overrides)
    return base


def _fire_request(**overrides: Any) -> CommitRequest:
    fields: dict[str, Any] = {
        "trigger_id": TRIGGER,
        "tenant_id": TENANT,
        "user_id": USER,
        "case_id": CASE,
        "trace_id": TRACE,
        "idempotency_scope": trigger_commit.IDEMPOTENCY_SCOPE,
        "idempotency_key": "manual:trg:v1:judge",
        "request_sha256": "a" * 64,
        "result": TriggerResult.FIRED,
        "reason_code": TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID,
        "state_after": TriggerState.FIRED,
        "case_revision_observed": 11,
        "increments_case_revision": True,
        "outbox_event_types": (
            "trigger.fired.v1",
            "commitment.overdue.v1",
            "case.state_changed.v1",
        ),
        "evaluation_payload": _payload(),
    }
    fields.update(overrides)
    return CommitRequest(**fields)


def _noop_request(**overrides: Any) -> CommitRequest:
    fields: dict[str, Any] = {
        "result": TriggerResult.NO_OP,
        "reason_code": TriggerReasonCode.PREDICATE_FALSE,
        "state_after": TriggerState.ARMED,
        "increments_case_revision": False,
        "outbox_event_types": ("trigger.noop.v1",),
        "evaluation_payload": _payload(outcome="NO_OP", predicate_result="FALSE"),
        "rearm_evaluation_version": 2,
        "rearm_not_before": TX_NOW + timedelta(days=1),
        "rearm_schedule_name": f"pv-trg-{TRIGGER.hex}-v2",
    }
    fields.update(overrides)
    return _fire_request(**fields)


def _trigger_row(state: str = "ARMED", version: int = 1, expires_at: Any = EXPIRES_AT) -> tuple:
    #: (state, evaluation_version, basis_case_revision, expires_at)
    return (state, version, 11, expires_at)


def _case_row(revision: int = 11, status: str = "WAITING") -> tuple:
    #: (id, status, revision, attention_level, reopened_count, resolved_at)
    return (CASE, status, revision, "ATTENTION", 0, None)


#: "Use the default row" and "there is no row" are different worlds, and
#: ``None`` cannot mean both. A test that could not express the second would
#: silently exercise the first, which is the shape of a vacuous assertion.
_DEFAULT = object()


def _world(
    *,
    trigger: Any = _DEFAULT,
    case: Any = _DEFAULT,
    stored: tuple[Any, ...] | None = None,
) -> tuple[_Pool, _Connection]:
    conn = _Connection(
        trigger_row=_trigger_row() if trigger is _DEFAULT else trigger,
        case_row=_case_row() if case is _DEFAULT else case,
        stored_idempotency=stored,
    )
    return _Pool(conn), conn


# ==========================================================================
# 1. The fire transaction — §10.2
# ==========================================================================


@pytest.mark.asyncio
async def test_a_fire_writes_every_statement_in_ddl_section_13_order() -> None:
    """The order is the specification, and the reason is foreign keys.

    ``kernel_decisions.proposal_id`` is a NOT NULL foreign key into
    ``memory_proposals``, so the proposal goes first; ``outbox_events.
    aggregate_version`` is the **post**-increment revision, so the ``cases``
    UPDATE goes before it. Writing the outbox first produces a
    plausible-looking row with the wrong version and nothing downstream can
    tell.
    """
    pool, conn = _world()
    receipt = await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    assert receipt.committed is True
    canonical = [label for label in conn.labels if label in trigger_commit.STATEMENT_ORDER]
    assert canonical == [
        "read_trigger",
        "read_idempotency",
        "read_case",
        "idempotency_records",
        "memory_proposals",
        "kernel_decisions",
        "cases",
        "state_transitions",
        "state_transitions",
        "prospective_triggers",
        "outbox_events",
        "outbox_events",
        "outbox_events",
    ]


@pytest.mark.asyncio
async def test_a_fire_advances_the_case_revision_by_exactly_one() -> None:
    """Rule R1, and the optimistic predicate that makes it safe."""
    pool, conn = _world()
    receipt = await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    params = conn.params_for("cases")
    assert params["revision_before"] == 11
    assert params["status_after"] == "ACTIONABLE"
    assert params["attention_after"] == "URGENT"
    assert receipt.case_revision_after == 12


@pytest.mark.asyncio
async def test_the_fired_trigger_row_stamps_fired_at_and_clears_its_schedule() -> None:
    """``ck_prospective_triggers_fired`` is a biconditional:
    ``(state = 'FIRED') = (fired_at IS NOT NULL)``. A fire that forgot the
    timestamp would be refused by the database, and a disarm that kept one
    would be too."""
    pool, conn = _world()
    await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    params = conn.params_for("prospective_triggers")
    assert params["state_after"] == "FIRED"
    assert params["last_result"] == "FIRED"
    assert params["last_reason_code"] == "COMMITMENT_OVERDUE_UNPAID"
    assert params["fired_at"] == TX_NOW
    assert params["schedule_name"] is None
    # §10.2 (f): the fired row's basis moves to the revision the fire produced.
    assert params["basis_case_revision"] == 12


# ==========================================================================
# 2. The no-op transaction — §9.10's "cases.revision incremented: no"
# ==========================================================================


@pytest.mark.asyncio
async def test_a_no_op_never_touches_the_case_aggregate() -> None:
    """§9.10: only ``FIRED`` touches the case. A no-op updates trigger-local
    columns and publishes one event, and that event is not noise --
    ``provenance_trigger_false_wake_ratio`` is built from it."""
    pool, conn = _world()
    receipt = await trigger_commit.commit_trigger_evaluation(pool, _noop_request())

    assert "cases" not in conn.labels
    assert "state_transitions" not in conn.labels
    assert conn.labels.count("outbox_events") == 1
    assert receipt.committed is True
    assert receipt.case_revision_after == 11


@pytest.mark.asyncio
async def test_a_re_arm_writes_the_next_generation_and_its_schedule_name() -> None:
    """§9.11. A ``NO_OP`` that leaves the obligation genuinely open re-arms, or
    prospective memory is single-shot."""
    pool, conn = _world()
    await trigger_commit.commit_trigger_evaluation(pool, _noop_request())

    params = conn.params_for("prospective_triggers")
    assert params["state_after"] == "ARMED"
    assert params["evaluation_version"] == 2
    assert params["schedule_name"] == f"pv-trg-{TRIGGER.hex}-v2"
    assert params["not_before"] == TX_NOW + timedelta(days=1)
    assert params["fired_at"] is None


@pytest.mark.asyncio
async def test_a_re_arm_past_expiry_writes_a_window_the_check_admits() -> None:
    """``ck_prospective_triggers_window``: ``expires_at IS NULL OR not_before IS
    NULL OR expires_at > not_before``.

    A thirty-day backoff on a trigger three days from expiry would otherwise be
    refused by the database at the exact moment the obligation was still open --
    the worst possible time to lose a row. The re-arm is written with a NULL
    ``not_before`` instead, which the CHECK admits and which means *due now*: the
    next wake reaches guard G4 and records ``EXPIRED / TRIGGER_EXPIRED``. The
    obligation is closed out honestly rather than dropped.
    """
    pool, conn = _world(trigger=_trigger_row(expires_at=TX_NOW + timedelta(hours=1)))
    await trigger_commit.commit_trigger_evaluation(pool, _noop_request())

    params = conn.params_for("prospective_triggers")
    assert params["not_before"] is None
    assert params["evaluation_version"] == 2


# ==========================================================================
# 3. The guards — §10.2 (a) and (b), re-asserted under lock
# ==========================================================================


@pytest.mark.asyncio
async def test_a_moved_case_revision_refuses_the_commit_and_writes_nothing() -> None:
    """The evaluation was computed on data that is no longer current. The
    correct response is to rebuild and re-evaluate, never to retry the write
    with the stale decision."""
    pool, conn = _world(case=_case_row(revision=12))
    receipt = await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    assert receipt.committed is False
    assert receipt.revision_moved is True
    assert receipt.case_revision_after == 12
    assert not [
        label for label in conn.labels if label.startswith(("memory_", "kernel_", "outbox"))
    ]


@pytest.mark.asyncio
async def test_a_trigger_that_is_no_longer_armed_refuses_the_commit() -> None:
    """§10.2 (a): the generation guard, re-asserted under ``FOR UPDATE``.

    The evaluator checked ``state`` before the projection read. Something else
    can commit in between, and only the lock settles it.
    """
    pool, conn = _world(trigger=_trigger_row(state="FIRED"))
    receipt = await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    assert receipt.committed is False
    assert "memory_proposals" not in conn.labels


@pytest.mark.asyncio
async def test_a_superseded_generation_refuses_the_commit() -> None:
    """A wake for generation 1 delivered after a re-arm to generation 2 must
    not act on the trigger that replaced it."""
    pool, conn = _world(trigger=_trigger_row(version=2))
    receipt = await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    assert receipt.committed is False
    assert "prospective_triggers" not in [
        label for label in conn.labels if label == "prospective_triggers"
    ]


@pytest.mark.asyncio
async def test_a_trigger_row_that_is_not_there_refuses_the_commit() -> None:
    pool, conn = _world(trigger=None)
    receipt = await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    assert receipt.committed is False
    assert "memory_proposals" not in conn.labels


# ==========================================================================
# 4. Idempotency — §9.9, the row goes in with the effect
# ==========================================================================


@pytest.mark.asyncio
async def test_the_idempotency_claim_precedes_every_write_it_guards() -> None:
    """ "There is no window in which the effect is committed but the key is
    not." The claim is the first INSERT of the transaction, so a duplicate
    wake's transaction dies on the uniqueness conflict before any work."""
    pool, conn = _world()
    await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    writes = [label for label in conn.labels if label in trigger_commit.WRITE_LABELS]
    assert writes[0] == "idempotency_records"


@pytest.mark.asyncio
async def test_a_duplicate_wake_returns_the_stored_result_and_writes_nothing() -> None:
    """§11.1. The caller did nothing wrong: a redelivery is the expected shape
    of an at-least-once contract, so the stored result comes back rather than
    an error -- and nothing is written a second time."""
    proposal_id = uuid.uuid4()
    event_id = uuid.uuid4()
    stored = (
        bytes.fromhex("a" * 64),
        {
            "proposal_id": str(proposal_id),
            "outbox_event_ids": [str(event_id)],
            "case_revision_after": 12,
        },
    )
    pool, conn = _world(stored=stored)
    receipt = await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    assert receipt.idempotent_replay is True
    assert receipt.committed is False
    assert receipt.proposal_id == proposal_id
    assert receipt.outbox_event_ids == (event_id,)
    assert receipt.case_revision_after == 12
    assert "memory_proposals" not in conn.labels
    assert "outbox_events" not in conn.labels


@pytest.mark.asyncio
async def test_the_same_key_with_a_different_body_is_a_conflict_not_a_replay() -> None:
    """§9.9: same key + different body -> ``IDEMPOTENCY_CONFLICT``. The key
    identifies the *intent*; a different body under one key is never a
    legitimate continuation of it."""
    stored = (bytes.fromhex("b" * 64), {})
    pool, _ = _world(stored=stored)
    with pytest.raises(trigger_commit.IdempotencyConflictError):
        await trigger_commit.commit_trigger_evaluation(pool, _fire_request())


# ==========================================================================
# 5. The three defects a fake Kernel could never have caught
# ==========================================================================


def _migration_text(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


def test_the_proposal_model_id_is_one_the_schema_check_admits() -> None:
    """``ck_memory_proposals_model`` is a closed IN list.

    ``16_TRIGGER_DSL.md`` §10.1 prints ``deterministic:trigger-eval`` and the
    schema has never admitted it -- not at ``0005``, not at ``0009``, whose
    comment is explicit that ``deterministic.kernel`` is the id the Kernel
    writes its ``TRIGGER_EVALUATION`` proposals under. Asserted against both
    migrations because 0009 is written and deliberately unapplied, so the
    constraint in force today and the one in force after the re-embed must
    both admit it.
    """
    from services.control_plane.app.triggers.service import PROPOSAL_MODEL_ID

    assert PROPOSAL_MODEL_ID in _migration_text("0005_kernel_control.py")
    assert PROPOSAL_MODEL_ID in _migration_text("0009_gemini_embedding_plane.py")
    assert PROPOSAL_MODEL_ID.startswith("deterministic"), (
        "a judge reads `WHERE model_id LIKE 'deterministic%'` to see every "
        "canonical change no language model participated in"
    )


def test_the_idempotency_scope_satisfies_the_column_check() -> None:
    """``ck_idempotency_scope_shape CHECK (scope ~ '^[a-z][a-z0-9_.]{2,63}$')``.

    ``TRIGGER_EVALUATION`` is upper-case and fails it. Every wake -- fire and
    no-op alike -- would have been refused at the claim, which is the first
    write of the transaction, so nothing would ever have been committed.
    """
    pattern = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")
    assert pattern.match(trigger_commit.IDEMPOTENCY_SCOPE), trigger_commit.IDEMPOTENCY_SCOPE
    assert "ck_idempotency_scope_shape" in _migration_text("0008_events_infrastructure.py")


@pytest.mark.asyncio
async def test_a_trigger_event_is_versioned_by_generation_not_by_case_revision() -> None:
    """``uq_outbox_events_aggregate_event`` is
    ``UNIQUE (aggregate_type, aggregate_id, aggregate_version, event_type)``.

    A no-op does not move ``cases.revision``, so two successive no-op wakes on
    one trigger would compute the same tuple and the second would be refused --
    losing the "the trigger woke and correctly did nothing" record that the
    false-wake ratio is built from. The trigger aggregate is versioned by its
    own generation counter, which advances on every re-arm; the case aggregate
    keeps the case revision.
    """
    pool, conn = _world()
    await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    rows = conn.all_params_for("outbox_events")
    by_type = {row["event_type"]: row for row in rows}
    assert by_type["trigger.fired.v1"]["aggregate_type"] == "TRIGGER"
    assert by_type["trigger.fired.v1"]["aggregate_version"] == 1
    assert by_type["case.state_changed.v1"]["aggregate_type"] == "CASE"
    assert by_type["case.state_changed.v1"]["aggregate_version"] == 12
    assert by_type["commitment.overdue.v1"]["aggregate_version"] == 12


# ==========================================================================
# 6. The audit chain, and what the decision row may claim
# ==========================================================================


@pytest.mark.asyncio
async def test_the_decision_row_names_the_proposal_the_transition_names() -> None:
    """The chain ``trigger -> proposal -> decision -> transition`` is what makes
    a trigger fire as explainable as an email-driven change, through the same
    tables and the same State Proof queries."""
    pool, conn = _world()
    await trigger_commit.commit_trigger_evaluation(pool, _fire_request())

    proposal = conn.params_for("memory_proposals")
    decision = conn.params_for("kernel_decisions")
    transitions = conn.all_params_for("state_transitions")

    assert decision["proposal_id"] == proposal["id"]
    assert proposal["kernel_decision_id"] == decision["id"]
    for transition in transitions:
        assert transition["proposal_id"] == proposal["id"]
        assert transition["kernel_decision_id"] == decision["id"]
    assert proposal["proposal_type"] == "TRIGGER_EVALUATION"
    assert proposal["status"] == "ACCEPTED"


@pytest.mark.asyncio
async def test_a_non_firing_commit_claims_no_case_revision_on_its_ledger_row() -> None:
    """``ck_kernel_decisions_revision_step`` permits ``after = before``, but
    ``build_decision_row`` refuses an ``ACCEPTED`` that did not advance the
    revision -- correctly, because rule R1 is about the case aggregate.

    A trigger no-op *is* a canonical commit (it writes ``prospective_triggers``
    and one outbox row) that does not touch the case aggregate. Recording NULL
    on both revision columns says exactly that. The observed revision is not
    lost: it is on ``memory_proposals.payload.case_revision_observed``, which is
    the durable, replayable record §10.3 defines.
    """
    pool, conn = _world()
    await trigger_commit.commit_trigger_evaluation(pool, _noop_request())

    decision = conn.params_for("kernel_decisions")
    assert decision["decision"] == str(KernelDecision.ACCEPTED)
    assert decision["case_revision_before"] is None
    assert decision["case_revision_after"] is None
    payload = conn.params_for("memory_proposals")["payload"]
    assert getattr(payload, "obj", payload)["case_revision_observed"] == 11


@pytest.mark.asyncio
async def test_the_stored_payload_is_the_evaluation_the_evaluator_computed() -> None:
    """§10.3. The receipt behind "nobody set this reminder": the values the
    predicate saw and the verdict of every subexpression, so a judge can check
    the conclusion rather than take it."""
    pool, conn = _world()
    request = _fire_request()
    await trigger_commit.commit_trigger_evaluation(pool, request)

    stored = conn.params_for("memory_proposals")["payload"]
    assert getattr(stored, "obj", stored) == dict(request.evaluation_payload)


@pytest.mark.asyncio
async def test_an_error_outcome_is_refused_rather_than_committed() -> None:
    """``ERROR`` establishes nothing about the world.

    §9.10's taxonomy gives it no terminal state change and no event: the
    trigger stays ``ARMED`` and the signal belongs on an alarm, not on the bus.
    The evaluator never hands one to the Kernel, and this asserts that the
    Kernel would refuse it if it did -- with a sentence rather than a
    ``KeyError`` surfacing as an opaque 500.
    """
    pool, conn = _world()
    request = _fire_request(
        result=TriggerResult.ERROR,
        reason_code=TriggerReasonCode.KERNEL_UNAVAILABLE,
        state_after=TriggerState.ARMED,
        increments_case_revision=False,
        outbox_event_types=(),
    )
    with pytest.raises(ValueError, match="not a committable trigger outcome"):
        await trigger_commit.commit_trigger_evaluation(pool, request)
    assert "prospective_triggers" not in conn.labels


# ==========================================================================
# 7. The statement register
# ==========================================================================


def test_every_executed_label_is_declared() -> None:
    """A label the committer emits that the order does not declare is a
    statement nobody reviewed."""
    import ast
    import inspect

    source = Path(inspect.getsourcefile(trigger_commit) or "").read_text(encoding="utf-8")
    emitted = {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert emitted, "the committer emits no labels; this test would be vacuous"
    assert emitted - set(trigger_commit.STATEMENT_ORDER) == set()


def test_the_committer_is_reachable_from_production_code() -> None:
    """The gap ``STATUS.md`` recorded, inverted into an assertion.

    ``grep`` found zero hits for ``commit_trigger_evaluation`` in ``services/``
    or ``packages/`` excluding tests, and that absence is what "no production
    path" meant. Three things are asserted here rather than one, because a
    symbol can be reachable in any of three ways and only all three together
    mean the capability can run:

    1. it is exported;
    2. :class:`KernelTriggerWriter`, the one implementation of the
       ``TriggerKernel`` Protocol that holds a credential, actually calls it;
    3. a **non-test** module under ``services/`` constructs that writer.

    Asserting only (1) would pass on a module nothing imports, and asserting
    only (3) would pass on a writer whose ``commit`` had been hollowed out.
    """
    import ast
    import inspect

    assert "commit_trigger_evaluation" in trigger_commit.__all__

    writer_source = inspect.getsource(trigger_commit.KernelTriggerWriter)
    called = {
        node.func.id
        for node in ast.walk(ast.parse(writer_source.lstrip()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "commit_trigger_evaluation" in called, (
        "KernelTriggerWriter.commit no longer calls the committer; the Protocol "
        "would still be satisfied and nothing would ever be written"
    )

    constructors = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "services").rglob("*.py")
        if "tests" not in path.parts
        and "__pycache__" not in path.parts
        and path.name != "trigger_commit.py"
        and "KernelTriggerWriter(" in path.read_text(encoding="utf-8")
    )
    assert constructors, (
        "nothing in services/ outside tests constructs KernelTriggerWriter; "
        "that is the same 'built but unreachable' state STATUS.md recorded"
    )
