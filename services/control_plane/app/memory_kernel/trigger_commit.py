"""``commit_trigger_evaluation`` — prospective memory's canonical write.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` §10.1 (*the evaluator is a proposer, not a
  writer*), §10.2 (the atomic fire transaction), §9.9 (the idempotency key),
  §9.10 (the outcome taxonomy) and §9.11 (re-arm).
- ``docs/specs/15_API_SPEC.md`` §9.10.
- ``db/migrations/versions/0005_kernel_control.py``,
  ``0006_prospective_memory.py``, ``0008_events_infrastructure.py`` — every
  column name and every CHECK this module satisfies is read from those files.

Why this lives in the Kernel and not in ``app/triggers``
--------------------------------------------------------
Firing a trigger writes ``cases``, ``state_transitions``, ``prospective_
triggers``, ``memory_proposals``, ``kernel_decisions`` and ``outbox_events``.
Every one of those is a canonical table, and the Memory Kernel is the sole
canonical writer — ``tools/write_path_lint`` checks that against the AST rather
than against a review. An earlier attempt put this function in
``app/triggers/`` and produced five W1/W2 violations, which is the linter doing
exactly its job.

The direction of the import is therefore deliberate: the **proposer** owns the
shape of what it proposes (``CommitRequest``, in ``app/triggers/service.py``,
where the evaluator that fills it lives) and the **Kernel** consumes it. That is
the same relationship ``commit_proposal`` has with ``MemoryProposal``.
``app/triggers`` imports nothing from here, so the graph stays acyclic and the
whole evaluator stays runnable in the hermetic ``unit`` lane.

What a fake Kernel could not have caught
-----------------------------------------
The evaluator has been complete and green since Phase 10 against
``FakeKernel``. A fake cannot be refused by a CHECK constraint, and three of
them would have refused every wake the moment this ran against the cluster:

1. ``ck_memory_proposals_model`` admits four ids and ``deterministic:trigger-
   eval`` — the one §10.1 prints — is not among them. ``deterministic.kernel``
   is, and migration ``0009``'s comment says so in as many words.
2. ``ck_idempotency_scope_shape`` is ``^[a-z][a-z0-9_.]{2,63}$``, so the scope
   string ``TRIGGER_EVALUATION`` was unwritable. The claim is the transaction's
   first INSERT, so nothing at all would have committed.
3. ``uq_outbox_events_aggregate_event`` is ``UNIQUE (aggregate_type,
   aggregate_id, aggregate_version, event_type)``. A no-op does not move
   ``cases.revision``; two successive no-ops on one trigger would therefore have
   collided had the trigger aggregate been versioned by the case revision. It is
   versioned by ``evaluation_version`` — the generation counter, which advances
   on every re-arm — and the case aggregate keeps the case revision.

Statements
----------
:data:`STATEMENT_ORDER` declares the only order these may appear in and
:func:`commit_trigger_evaluation` returns nothing else; the executed labels are
compared against it by test. Three of the statements are reused from
``transaction.py`` rather than copied: a second definition of the outbox INSERT
is a second place a column can be forgotten.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from psycopg.types.json import Json

from provenance_db.retry import (
    RetryExhausted,
    TelemetrySink,
    TxPool,
    in_transaction,
    run_in_serializable_tx,
)
from provenance_domain import money
from provenance_domain.enums import (
    AttentionLevel,
    CaseStatus,
    KernelDecision,
    KernelReasonCode,
    TransitionType,
    TriggerResult,
    TriggerState,
)
from services.control_plane.app.memory_kernel import case_ops, decisions, transaction
from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG, KernelConfig
from services.control_plane.app.triggers.config import (
    IDEMPOTENCY_RETENTION_DAYS,
    IDEMPOTENCY_SCOPE,
)
from services.control_plane.app.triggers.service import (
    CommitReceipt,
    CommitRequest,
    KernelUnavailableError,
    TriggerKernel,
)

__all__ = [
    "CASE_AGGREGATE_EVENTS",
    "IDEMPOTENCY_SCOPE",
    "SABOTAGE_HOOKS",
    "SABOTAGE_MODULE",
    "SABOTAGED_SYMBOLS",
    "STATEMENT_ORDER",
    "TRIGGER_CANONICAL_WRITE_STATEMENTS",
    "WRITE_LABELS",
    "IdempotencyConflictError",
    "KernelTriggerWriter",
    "commit_trigger_evaluation",
    "revision_is_current",
    "trigger_is_actionable",
]

#: The statements one trigger-evaluation commit may issue, in the only order
#: they may appear in. Reads first, then the idempotency claim, then the audit
#: chain, then the canonical change, then the outbox — DDL section 13's order,
#: restricted to the tables prospective memory touches.
#:
#: ``read_trigger`` and ``read_case`` are ``SELECT ... FOR UPDATE``: §10.2 (a)
#: and (b) re-assert the generation and revision guards **under lock**, because
#: the evaluator checked them before the projection read and something else can
#: commit in between.
STATEMENT_ORDER: Final[tuple[str, ...]] = (
    "read_trigger",
    "read_idempotency",
    "read_case",
    "idempotency_records",
    "memory_proposals",
    "kernel_decisions",
    "cases",
    "state_transitions",
    "prospective_triggers",
    "outbox_events",
)

#: The subset of :data:`STATEMENT_ORDER` that writes. Used by the test that
#: asserts the idempotency claim precedes every write it guards.
WRITE_LABELS: Final[tuple[str, ...]] = (
    "idempotency_records",
    "memory_proposals",
    "kernel_decisions",
    "cases",
    "state_transitions",
    "prospective_triggers",
    "outbox_events",
)

#: The two canonical write statements this module adds to the Kernel's
#: register, named the way ``transaction.CANONICAL_WRITE_STATEMENTS`` names
#: its own. The other four canonical writes a fire performs are that module's
#: statements, reused rather than re-declared.
TRIGGER_CANONICAL_WRITE_STATEMENTS: Final[tuple[str, ...]] = (
    "memory_proposals INSERT     (_TRIGGER_PROPOSAL_SQL)",
    "prospective_triggers UPDATE (_TRIGGER_SETTLE_SQL)",
)

#: Which aggregate each event type belongs to. ``ck_outbox_events_aggregate_
#: type`` is a closed list, and the choice decides which counter
#: ``aggregate_version`` reads — see the module docstring's point 3.
CASE_AGGREGATE_EVENTS: Final[frozenset[str]] = frozenset(
    {"case.state_changed.v1", "commitment.overdue.v1"}
)

#: The status a settled ``idempotency_records`` row carries.
#: ``ck_idempotency_status`` admits ``IN_PROGRESS``, ``COMPLETED`` and
#: ``FAILED``. §10.2 prints ``'COMMITTED'``, which the column has never
#: accepted; the migration is the higher authority and the row is written
#: settled because it is written inside the transaction that settles it.
_IDEMPOTENCY_STATUS: Final[str] = "COMPLETED"

#: §9.10's fire path: the case becomes actionable and the person is told.
_FIRE_CASE_STATUS: Final[CaseStatus] = CaseStatus.ACTIONABLE
_FIRE_ATTENTION: Final[AttentionLevel] = AttentionLevel.URGENT

#: One Kernel reason code per trigger outcome, for the ``kernel_decisions``
#: ledger. The trigger's own closed reason code is a different vocabulary and
#: is recorded on ``prospective_triggers.last_reason_code`` and on the
#: transition, so both are readable and neither is guessed from the other.
_DECISION_REASON: Final[Mapping[TriggerResult, KernelReasonCode]] = {
    TriggerResult.FIRED: KernelReasonCode.TRIGGER_FIRED_PREDICATE_TRUE,
    TriggerResult.NO_OP: KernelReasonCode.TRIGGER_NOOP_PREDICATE_FALSE,
    TriggerResult.DISARMED: KernelReasonCode.TRIGGER_DISARMED_RESOLVED,
    TriggerResult.EXPIRED: KernelReasonCode.TRIGGER_EXPIRED,
}


class IdempotencyConflictError(RuntimeError):
    """One key, two different bodies — §9.9.

    Not a replay. The key identifies the *intent*, and a different request under
    the same key is never a legitimate continuation of it. The API maps this to
    ``409 IDEMPOTENCY_CONFLICT`` rather than to a retry, because retrying will
    produce the same answer forever.
    """

    def __init__(self, *, scope: str, key: str) -> None:
        super().__init__(
            f"{scope}/{key} was already claimed by a request with a different body; "
            "the key identifies the intent, so this is a conflict and not a replay"
        )
        self.scope = scope
        self.key = key


# ---------------------------------------------------------------------------
# The statements. The two new ones; the rest are transaction.py's.
# ---------------------------------------------------------------------------

#: §10.2 (a). ``FOR UPDATE`` so the generation guard is settled by the database
#: rather than by the gap between the evaluator's read and this write.
_TRIGGER_LOCK_SQL: Final[str] = """
SELECT state, evaluation_version, basis_case_revision, expires_at
  FROM prospective_triggers
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(trigger_id)s
   FOR UPDATE
"""

#: §10.2 (b). THE staleness guard: the case must not have moved since the
#: projection read. Redundant with the optimistic predicate on the UPDATE, and
#: deliberately so — under serializable isolation either alone suffices, and
#: both together mean a refactor that drops one cannot silently reintroduce a
#: lost update.
_CASE_LOCK_SQL: Final[str] = """
SELECT id, status, revision, attention_level, reopened_count, resolved_at
  FROM cases
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(case_id)s
   FOR UPDATE
"""

#: The stored result a duplicate wake gets back. ``response_body`` is read as
#: well as ``request_hash`` because §11.1 requires the *stored* result: a
#: replay that invented a fresh proposal id would point the Memory Trace at
#: nothing.
_IDEMPOTENCY_READ_SQL: Final[str] = """
SELECT request_hash, response_body
  FROM idempotency_records
 WHERE scope = %(scope)s AND key = %(key)s
"""

#: §9.9. ``UNIQUE(scope, key)``, inside the same transaction as the effect.
#: ``ck_idempotency_completed`` requires ``completed_at`` and ``response_code``
#: together on any non-``IN_PROGRESS`` row, so all three are written at once.
_IDEMPOTENCY_CLAIM_SQL: Final[str] = """
INSERT INTO idempotency_records (
    scope, key, tenant_id, user_id, request_hash, trace_id, status,
    response_code, response_body, created_at, completed_at, expires_at
) VALUES (
    %(scope)s, %(key)s, %(tenant_id)s, %(user_id)s, %(request_hash)s, %(trace_id)s,
    %(status)s, %(response_code)s, %(response_body)s, %(tx_now)s, %(tx_now)s,
    %(expires_at)s
)
"""

#: §10.1's honestly-labelled proposal. ``source_artifact_ids`` and
#: ``evidence_ids`` are empty arrays because no artifact caused this and no new
#: evidence was admitted; ``model_id`` is the deterministic marker, so a judge
#: can select every canonical change no language model participated in.
_TRIGGER_PROPOSAL_SQL: Final[str] = """
INSERT INTO memory_proposals (
    id, tenant_id, user_id, trace_id, schema_version, proposal_type,
    source_artifact_ids, evidence_ids, candidate_relationship_id,
    candidate_case_id, payload, payload_sha256, model_id, prompt_version,
    status, created_at, decided_at, kernel_decision_id
) VALUES (
    %(id)s, %(tenant_id)s, %(user_id)s, %(trace_id)s, %(schema_version)s,
    %(proposal_type)s, %(source_artifact_ids)s, %(evidence_ids)s, NULL,
    %(case_id)s, %(payload)s, %(payload_sha256)s, %(model_id)s,
    %(prompt_version)s, %(status)s, %(tx_now)s, %(tx_now)s, %(kernel_decision_id)s
)
"""

#: §10.2 (f), generalised over the five outcomes.
#:
#: One statement rather than one per outcome, because the CHECKs are
#: cross-column: ``ck_prospective_triggers_fired`` is a biconditional between
#: ``state`` and ``fired_at``, and ``ck_prospective_triggers_last_reason``
#: partitions ``last_reason_code`` by ``last_result``. A shape that wrote them
#: in separate statements could satisfy each half alone.
#:
#: The ``WHERE`` re-states the generation guard a third time. It is what makes
#: a lost update impossible rather than merely unlikely: zero rows affected
#: means the trigger moved under this transaction, and the caller re-evaluates.
_TRIGGER_SETTLE_SQL: Final[str] = """
UPDATE prospective_triggers
   SET state               = %(state_after)s,
       last_result         = %(last_result)s,
       last_reason_code    = %(last_reason_code)s,
       last_evaluated_at   = %(tx_now)s,
       fired_at            = %(fired_at)s,
       basis_case_revision = %(basis_case_revision)s,
       evaluation_version  = %(evaluation_version)s,
       not_before          = %(not_before)s,
       schedule_name       = %(schedule_name)s,
       updated_at          = %(tx_now)s
 WHERE tenant_id = %(tenant_id)s AND user_id = %(user_id)s AND id = %(trigger_id)s
   AND state = 'ARMED'
   AND evaluation_version = %(evaluation_version_observed)s
"""


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


async def commit_trigger_evaluation(
    pool: TxPool,
    request: CommitRequest,
    *,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
    telemetry: TelemetrySink | None = None,
) -> CommitReceipt:
    """Commit one trigger evaluation, or report why it could not be committed.

    One ``SERIALIZABLE`` transaction, no network call inside the callback, and
    the ``40001`` retry loop is ``provenance_db.retry.run_in_serializable_tx``
    — the only one in the repository. There is no second loop here and no
    backoff arithmetic.

    Args:
        pool: the ``pv_kernel_writer`` pool. Holding it is what makes this the
            canonical writer; nothing else in the request path has it.
        request: what the evaluator decided. Assembled there, executed here,
            and never re-decided: this function does not re-run the predicate
            and has no way to change the outcome.
        cfg: the retry policy. The Kernel's own, so a trigger commit and an
            ingestion commit contend under identical rules.
        telemetry: optional sink for the retry counters.

    Returns:
        :class:`~services.control_plane.app.triggers.service.CommitReceipt`.
        ``revision_moved`` is not an error — it reports that the world moved
        between the projection read and the write, and the caller's correct
        response is to rebuild and re-evaluate rather than to retry the write
        with a stale decision.

    Raises:
        IdempotencyConflictError: the key was claimed by a different body.
        KernelUnavailableError: the retry cap was reached. **No side effect is
            performed after the cap** and nothing is enqueued; the trigger stays
            ``ARMED`` and the wake is re-driven. Reported as unavailability
            rather than as a refusal because the difference matters: a refusal
            is information, and this is the absence of it.
    """
    observed_version = int(request.evaluation_payload["evaluation_version"])
    scope = {"tenant_id": request.tenant_id, "user_id": request.user_id}

    @in_transaction
    async def _callback(conn: Any, tx_now: datetime) -> CommitReceipt:
        """One attempt. Fresh reads, fresh ids, no network.

        Every id is minted inside the callback rather than above it: rule 4 of
        ``12_KERNEL_ALGORITHMS.md`` §7.3 forbids deterministic UUIDs across
        attempts, because idempotency comes from ``(scope, key)`` and the unique
        constraints, never from a stable primary key.
        """
        executed: list[str] = []

        # (a) The trigger row, locked. §10.2's ABORT_STATE_CHANGED.
        executed.append("read_trigger")
        cursor = await conn.execute(_TRIGGER_LOCK_SQL, {**scope, "trigger_id": request.trigger_id})
        trigger_row = await cursor.fetchone()
        if trigger_row is None:
            return _moved(request.case_revision_observed)
        state, current_version, current_basis, expires_at = (
            str(trigger_row[0]),
            int(trigger_row[1]),
            int(trigger_row[2]),
            trigger_row[3],
        )
        # Reached as a BARE NAME in this module so `PV_SABOTAGE` can neuter it:
        # `install_sabotage` rebinds the name in these globals, and a call
        # through any other path would resolve to a reference captured before
        # the rebind, making the sabotage silently never arrive.
        if not trigger_is_actionable(state, current_version, observed_version):
            return _moved(request.case_revision_observed)

        # (b) The idempotency claim, read before anything is built. §11.1.
        executed.append("read_idempotency")
        cursor = await conn.execute(
            _IDEMPOTENCY_READ_SQL,
            {"scope": request.idempotency_scope, "key": request.idempotency_key},
        )
        stored = await cursor.fetchone()
        if stored is not None:
            return _replayed(request, stored)

        # (c) The case row, locked. THE staleness guard.
        executed.append("read_case")
        cursor = await conn.execute(_CASE_LOCK_SQL, {**scope, "case_id": request.case_id})
        case_row = await cursor.fetchone()
        if case_row is None:
            raise transaction.CaseNotFoundError(
                f"case {request.case_id} is not visible to this principal"
            )
        case_status_before = str(case_row[1])
        revision_before = int(case_row[2])
        if not revision_is_current(revision_before, request.case_revision_observed):
            return _moved(revision_before)

        if request.result not in _DECISION_REASON:
            # `ERROR` is the only member without an entry, and the evaluator
            # never reaches the Kernel with one: an ERROR is a terminal outcome
            # that emits nothing and leaves the trigger ARMED. Refusing loudly
            # here is the difference between "this cannot happen" written in a
            # comment and written in code -- a bare KeyError would surface as an
            # opaque 500 with no statement of what was wrong.
            raise ValueError(
                f"{request.result.value} is not a committable trigger outcome; "
                "an ERROR establishes nothing about the world and has no "
                "canonical change to record"
            )

        fires = request.result is TriggerResult.FIRED
        revision_after = revision_before + 1 if fires else revision_before
        proposal_id = uuid.uuid4()
        decision_id = uuid.uuid4()
        event_ids = tuple(uuid.uuid4() for _ in request.outbox_event_types)

        # (d) The claim, first of the writes, in the same transaction as the
        #     effect. There is no window in which the effect is committed and
        #     the key is not.
        executed.append("idempotency_records")
        await conn.execute(
            _IDEMPOTENCY_CLAIM_SQL,
            {
                **scope,
                "scope": request.idempotency_scope,
                "key": request.idempotency_key,
                "request_hash": bytes.fromhex(request.request_sha256),
                "trace_id": request.trace_id,
                "status": _IDEMPOTENCY_STATUS,
                "response_code": 200,
                "response_body": Json(
                    {
                        "proposal_id": str(proposal_id),
                        "outbox_event_ids": [str(value) for value in event_ids],
                        "case_revision_after": revision_after,
                    }
                ),
                "tx_now": tx_now,
                "expires_at": tx_now + timedelta(days=IDEMPOTENCY_RETENTION_DAYS),
            },
        )

        # (e) The audit chain: proposal, then decision. The order is a foreign
        #     key -- `fk_kernel_decisions_proposal` is validated at statement
        #     time -- not a preference.
        payload = dict(request.evaluation_payload)
        executed.append("memory_proposals")
        await conn.execute(
            _TRIGGER_PROPOSAL_SQL,
            {
                **scope,
                "id": proposal_id,
                "trace_id": request.trace_id,
                "schema_version": "1.0",
                "proposal_type": request.proposal_type,
                "source_artifact_ids": Json([str(v) for v in request.source_artifact_ids]),
                "evidence_ids": Json([str(v) for v in request.evidence_ids]),
                "case_id": request.case_id,
                "payload": Json(payload),
                "payload_sha256": _payload_digest(payload),
                "model_id": request.model_id,
                "prompt_version": request.prompt_version,
                "status": decisions.proposal_status_for(KernelDecision.ACCEPTED),
                "kernel_decision_id": decision_id,
                "tx_now": tx_now,
            },
        )

        row = decisions.build_decision_row(
            decision_id=decision_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            proposal_id=proposal_id,
            trace_id=request.trace_id,
            decision=KernelDecision.ACCEPTED,
            reason_codes=(_DECISION_REASON[request.result],),
            case_id=request.case_id,
            # A trigger commit that does not fire is still a canonical commit --
            # it writes `prospective_triggers` and one outbox row -- but it does
            # not touch the case aggregate, and rule R1 is about that aggregate.
            # NULL on both columns says exactly that. The observed revision is
            # not lost: §10.3's payload carries `case_revision_observed`.
            case_revision_before=revision_before if fires else None,
            case_revision_after=revision_after if fires else None,
            tx_now=tx_now,
        )
        executed.append("kernel_decisions")
        await conn.execute(decisions.DECISION_INSERT_SQL, _decision_params(row))

        # (f) The canonical state change. Only a fire touches the case.
        if fires:
            executed.append("cases")
            cursor = await conn.execute(
                case_ops.CASE_UPDATE_SQL,
                {
                    **scope,
                    "case_id": request.case_id,
                    "status_after": str(_FIRE_CASE_STATUS),
                    "reopen_delta": 0,
                    "attention_after": str(_FIRE_ATTENTION),
                    "resolved_at": case_row[5],
                    "revision_before": revision_before,
                    "tx_now": tx_now,
                },
            )
            if getattr(cursor, "rowcount", 1) == 0:
                # Unreachable under SERIALIZABLE with the row locked, which is
                # exactly why it is checked: if it ever fires, the isolation
                # guarantee has regressed and the alternative to refusing here
                # is a silently lost update.
                return _moved(revision_before)

            for label, params in _fire_transitions(
                request=request,
                case_status_before=case_status_before,
                revision_after=revision_after,
                proposal_id=proposal_id,
                decision_id=decision_id,
                tx_now=tx_now,
            ):
                executed.append(label)
                await conn.execute(transaction.STATE_TRANSITION_INSERT_SQL, {**scope, **params})

        # (g) Close out the trigger.
        executed.append("prospective_triggers")
        cursor = await conn.execute(
            _TRIGGER_SETTLE_SQL,
            {
                **scope,
                "trigger_id": request.trigger_id,
                "state_after": str(request.state_after),
                "last_result": str(request.result),
                "last_reason_code": str(request.reason_code),
                "fired_at": tx_now if request.state_after is TriggerState.FIRED else None,
                "basis_case_revision": revision_after if fires else current_basis,
                "evaluation_version": request.rearm_evaluation_version or observed_version,
                "not_before": _rearm_not_before(request, expires_at),
                "schedule_name": request.rearm_schedule_name,
                "evaluation_version_observed": observed_version,
                "tx_now": tx_now,
            },
        )
        if getattr(cursor, "rowcount", 1) == 0:
            return _moved(revision_before)

        # (h) The reactions, in the same transaction as the state they describe.
        for event_id, event_type in zip(event_ids, request.outbox_event_types, strict=True):
            executed.append("outbox_events")
            await conn.execute(
                transaction.OUTBOX_INSERT_SQL,
                {
                    **scope,
                    "id": event_id,
                    **_outbox_aggregate(
                        request=request,
                        event_type=event_type,
                        revision_after=revision_after,
                        evaluation_version=observed_version,
                    ),
                    "event_type": event_type,
                    "payload_version": "1.0",
                    "payload": Json(
                        _event_payload(
                            request=request,
                            event_type=event_type,
                            revision_before=revision_before,
                            revision_after=revision_after,
                            case_status_before=case_status_before,
                        )
                    ),
                    "trace_id": request.trace_id,
                    "causation_id": proposal_id,
                    "tx_now": tx_now,
                },
            )

        undeclared = set(executed) - set(STATEMENT_ORDER)
        if undeclared:  # pragma: no cover - the test asserts the same set statically
            raise AssertionError(f"undeclared statement labels: {sorted(undeclared)}")

        return CommitReceipt(
            committed=True,
            revision_moved=False,
            case_revision_after=revision_after,
            proposal_id=proposal_id,
            outbox_event_ids=event_ids,
        )

    try:
        result = await run_in_serializable_tx(pool, _callback, config=cfg, telemetry=telemetry)
    except RetryExhausted as exhausted:
        # `CANONICAL_DECISIONS.md` -> *Kernel retry exhaustion*: no side effect
        # after the cap and nothing enqueued. The evaluator turns this into
        # `ERROR / KERNEL_UNAVAILABLE` and leaves the trigger ARMED, which is
        # the only safe response to an absence of information.
        raise KernelUnavailableError(
            f"the Kernel could not commit the evaluation in {exhausted.attempts} attempts; "
            "nothing was written and nothing was enqueued"
        ) from exhausted
    return result.value


# ---------------------------------------------------------------------------
# The adapter the evaluator is handed
# ---------------------------------------------------------------------------


class KernelTriggerWriter:
    """:class:`TriggerKernel` over the ``pv_kernel_writer`` pool.

    The evaluator depends on the Protocol rather than on this class, which is
    what lets the whole subsystem run in the hermetic ``unit`` lane against a
    fake. This is the one implementation that holds a credential.
    """

    __slots__ = ("_cfg", "_pool", "_telemetry")

    def __init__(
        self,
        pool: TxPool,
        *,
        cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self._pool = pool
        self._cfg = cfg
        self._telemetry = telemetry

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        return await commit_trigger_evaluation(
            self._pool, request, cfg=self._cfg, telemetry=self._telemetry
        )


def _assert_satisfies_protocol(writer: KernelTriggerWriter) -> TriggerKernel:
    """Structural check, at import time of the type checker rather than at runtime.

    A ``TriggerKernel`` whose ``commit`` drifted from the Protocol would be
    caught here rather than at the first wake.
    """
    return writer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def trigger_is_actionable(state: str, current_version: int, observed_version: int) -> bool:
    """Section 10.2 (a), as a predicate: may this wake act on this row?

    A predicate rather than an inline comparison for one reason: it is the
    thing ``PV_SABOTAGE`` has to be able to neuter. Neutered to the identity it
    returns ``state`` -- a non-empty string, therefore truthy -- so an already
    ``FIRED`` trigger fires a second time and a wake from a superseded
    generation acts on the trigger that replaced it. That is the UNSAFE
    direction, which is what makes the sabotage entry worth having: a guard
    written to return an error object would be neutered into something falsy
    and fail closed, and the sabotage would pass while proving nothing.
    """
    return state == str(TriggerState.ARMED) and current_version == observed_version


def revision_is_current(revision_before: int, observed: int) -> bool:
    """Section 10.2 (b): THE staleness guard, as a predicate.

    The evaluation was computed against ``observed``. If the case has moved
    since, the decision describes a world that no longer exists and the only
    correct response is to rebuild and re-evaluate. Neutered to the identity
    this returns ``revision_before``, a truthy int, so a stale decision commits
    -- which is a trigger firing a demand for money that may already have been
    paid, months after the fact.
    """
    return revision_before == observed


def _moved(case_revision_after: int) -> CommitReceipt:
    """Nothing was committed and the caller should rebuild.

    One shape for four causes -- the trigger row vanished, it is no longer
    ``ARMED``, its generation moved, or the case revision moved -- because the
    caller's correct response is identical in all four: re-read, re-evaluate,
    and never fire on a decision computed against state that has changed.
    """
    return CommitReceipt(
        committed=False, revision_moved=True, case_revision_after=case_revision_after
    )


def _replayed(request: CommitRequest, stored: tuple[Any, ...]) -> CommitReceipt:
    """§11.1's stored result, or §9.9's conflict.

    The hash check precedes everything: a mismatched body under a claimed key
    is a conflict whatever the stored row's state, because the key identifies
    the intent.
    """
    stored_hash = bytes(stored[0]) if stored[0] is not None else b""
    if stored_hash != bytes.fromhex(request.request_sha256):
        raise IdempotencyConflictError(scope=request.idempotency_scope, key=request.idempotency_key)
    body = stored[1] if isinstance(stored[1], Mapping) else {}
    raw_proposal = body.get("proposal_id")
    raw_events = body.get("outbox_event_ids") or []
    return CommitReceipt(
        committed=False,
        revision_moved=False,
        case_revision_after=int(body.get("case_revision_after", request.case_revision_observed)),
        proposal_id=None if raw_proposal is None else uuid.UUID(str(raw_proposal)),
        outbox_event_ids=tuple(uuid.UUID(str(value)) for value in raw_events),
        idempotent_replay=True,
    )


def _payload_digest(payload: Mapping[str, Any]) -> bytes:
    """``ck_memory_proposals_payload_sha CHECK (length(payload_sha256) = 32)``.

    Over the canonical JSON rather than over ``repr``: two runs that produced
    the same evaluation must produce the same digest, and dict ordering is not
    part of the evaluation.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def _decision_params(row: decisions.DecisionRow) -> dict[str, Any]:
    """``DecisionRow.as_params`` with ``reason_codes`` wrapped for ``jsonb``.

    Delegated to ``transaction`` so there is one place that knows a plain list
    handed to ``psycopg`` renders a Postgres *array* literal, which a ``jsonb``
    column rejects.
    """
    return transaction.decision_params(row)


def _rearm_not_before(request: CommitRequest, expires_at: datetime | None) -> datetime | None:
    """The next wake time, or ``None``.

    ``ck_prospective_triggers_window`` requires ``expires_at > not_before``. A
    thirty-day backoff on a trigger three days from expiry would be refused by
    the database at the exact moment the obligation was still open, which is the
    worst possible time to lose a row. ``None`` is what the CHECK admits and it
    means *due now*: the next wake reaches guard G4 and records
    ``EXPIRED / TRIGGER_EXPIRED``, so the obligation is closed out rather than
    dropped.

    ``None`` is also the right answer for a fire, a disarm and an expiry: the
    trigger is no longer ``ARMED`` and there is no next wake to schedule.
    """
    proposed = request.rearm_not_before
    if proposed is None:
        return None
    if expires_at is not None and proposed >= expires_at:
        return None
    return proposed


def _fire_transitions(
    *,
    request: CommitRequest,
    case_status_before: str,
    revision_after: int,
    proposal_id: uuid.UUID,
    decision_id: uuid.UUID,
    tx_now: datetime,
) -> list[tuple[str, dict[str, Any]]]:
    """The ledger rows one fire appends, at the revision that produced them.

    Two rows and not one. ``ck_state_transitions_moves`` refuses a row whose
    ``from_state`` equals its ``to_state``, so the trigger's ``ARMED -> FIRED``
    and the case's ``WAITING -> ACTIONABLE`` are genuinely two moves of two
    subjects. Collapsing them into one row would have to pick a subject, and
    whichever it picked the other change would be unattributable.

    A no-op appends nothing: its state does not move, and a row asserting
    ``ARMED -> ARMED`` is what the CHECK exists to refuse.
    """
    rows: list[tuple[str, dict[str, Any]]] = [
        (
            "state_transitions",
            {
                "id": uuid.uuid4(),
                "case_id": request.case_id,
                "case_revision": revision_after,
                "transition_type": str(TransitionType.TRIGGER_STATE),
                "subject_kind": "TRIGGER",
                "subject_id": request.trigger_id,
                "from_state": str(TriggerState.ARMED),
                "to_state": str(TriggerState.FIRED),
                "reason_code": str(request.reason_code),
                "proposal_id": proposal_id,
                "kernel_decision_id": decision_id,
                "trace_id": request.trace_id,
                "recorded_at": tx_now,
            },
        )
    ]
    if case_status_before != str(_FIRE_CASE_STATUS):
        rows.append(
            (
                "state_transitions",
                {
                    "id": uuid.uuid4(),
                    "case_id": request.case_id,
                    "case_revision": revision_after,
                    "transition_type": str(TransitionType.CASE_STATUS),
                    "subject_kind": "CASE",
                    "subject_id": request.case_id,
                    "from_state": case_status_before,
                    "to_state": str(_FIRE_CASE_STATUS),
                    "reason_code": str(request.reason_code),
                    "proposal_id": proposal_id,
                    "kernel_decision_id": decision_id,
                    "trace_id": request.trace_id,
                    "recorded_at": tx_now,
                },
            )
        )
    return rows


def _outbox_aggregate(
    *,
    request: CommitRequest,
    event_type: str,
    revision_after: int,
    evaluation_version: int,
) -> dict[str, Any]:
    """Which aggregate an event belongs to, and which counter versions it.

    See the module docstring's point 3: ``uq_outbox_events_aggregate_event``
    makes this a correctness question rather than a labelling one.
    """
    if event_type in CASE_AGGREGATE_EVENTS:
        return {
            "aggregate_type": "CASE",
            "aggregate_id": request.case_id,
            "aggregate_version": revision_after,
        }
    return {
        "aggregate_type": "TRIGGER",
        "aggregate_id": request.trigger_id,
        "aggregate_version": evaluation_version,
    }


def _event_payload(
    *,
    request: CommitRequest,
    event_type: str,
    revision_before: int,
    revision_after: int,
    case_status_before: str,
) -> dict[str, Any]:
    """Ids and outcomes, never document text.

    An event is a pointer to committed state, not a copy of it: a consumer that
    needs the evaluation reads ``memory_proposals.payload`` through an
    authorised API using the ids below. ``PublishedEvent`` re-checks that on the
    way out, and this is the side that must not put anything there to find.
    """
    payload: dict[str, Any] = {
        "trigger_id": str(request.trigger_id),
        "case_id": str(request.case_id),
        "result": str(request.result),
        "reason_code": str(request.reason_code),
        "trigger_type": str(request.evaluation_payload.get("trigger_type", "")),
        "evaluation_version": int(request.evaluation_payload["evaluation_version"]),
        "case_revision_before": revision_before,
        "case_revision_after": revision_after,
    }
    if event_type == "trigger.noop.v1":
        # §9.10's "with disarmed: true / expired: true". A no-op that stopped
        # watching and a no-op that will wake again are different facts, and a
        # consumer that could not tell them apart would keep a resolved
        # obligation on a dashboard forever.
        payload["disarmed"] = request.result is TriggerResult.DISARMED
        payload["expired"] = request.result is TriggerResult.EXPIRED
        payload["rearmed_as_version"] = request.rearm_evaluation_version
    if event_type == "case.state_changed.v1":
        payload["from_status"] = case_status_before
        payload["to_status"] = str(_FIRE_CASE_STATUS)
        payload["attention_level"] = str(_FIRE_ATTENTION)
    return payload


# ---------------------------------------------------------------------------
# The PV_SABOTAGE hooks
#
# `quality/23_PHASE_GATES.md` section 23.5 fixes the semantics and
# `tests/sabotage_matrix.yaml` carries the entries. The mechanism is
# `provenance_domain.money.install_sabotage`, reused rather than reimplemented:
# a second copy of the neutering logic is a second thing that can quietly stop
# neutering.
#
# Both symbols are called as BARE NAMES inside `_callback`, in this module, so
# the lookup happens in these globals at call time and the rebind is visible.
# A `from`-import at a call site in another module would copy the reference
# before the rebind and the sabotage would report green while neutering
# nothing -- which section 23 counts as a failure, not a relief.
# ---------------------------------------------------------------------------

#: `23_PHASE_GATES.md` addresses kernel symbols as
#: `memory_kernel.<module>.<name>` rather than by their full dotted import
#: path, so the label is explicit.
SABOTAGE_MODULE: Final[str] = "memory_kernel.trigger_commit"

#: The symbols in this module the matrix may neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = (
    "trigger_is_actionable",
    "revision_is_current",
)

#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = money.install_sabotage(
    globals(), SABOTAGE_MODULE, SABOTAGE_HOOKS, os.environ.get(money.SABOTAGE_ENV_VAR)
)
