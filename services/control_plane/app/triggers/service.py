"""``evaluate_trigger()`` — the ONE trigger evaluation path.

Scheduler wakes, sweeper wakes, replays and manual demo wakes all land here.
``wake.source`` is a label used for metrics and the Memory Trace; **no branch in
this function or anything it calls reads it to decide behaviour**, and
``test_no_branch_reads_the_wake_source_to_decide_behaviour`` scans this source to
keep that true. That single shared entry point is what makes the demo's manual
button prove more rather than less: it is not a shortcut, a mock, a fixture or a
forced fire, there is no ``force`` parameter, and adding one is prohibited.

The rule
--------
A wakeup is an invitation to re-evaluate, never an instruction to act. The
envelope was frozen at arm time — possibly months ago — and carries identity
only: a trigger id, a generation, a scheduled instant. Everything the outcome
depends on is read again, from canonical state, in one read-only snapshot, at
wake time. A trigger that fires because a timer said so, without re-checking, is
a false claim about the world.

The evaluator is a proposer, not a writer
------------------------------------------
The KERNEL RULE has no exception for deterministic components. Nothing here
holds ``pv_kernel_writer`` and nothing here writes a canonical row. This module
synthesises a deterministic ``TRIGGER_EVALUATION`` proposal and hands it to the
Memory Kernel through :class:`TriggerKernel`, which keeps the audit chain
trigger -> proposal -> decision -> transition unbroken and reuses the Kernel's
serialization-retry machinery rather than reimplementing it.

Boundaries are Protocols
------------------------
:class:`TriggerStore`, ``projection.ProjectionReader`` and :class:`TriggerKernel`
are narrow Protocols declared here and satisfied elsewhere. That is what lets
the whole subsystem run in the hermetic ``unit`` lane — no database, no
credentials, no socket — and it means this package does not reach into modules
another lane owns.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from provenance_domain.enums import (
    TriggerReasonCode,
    TriggerResult,
    TriggerState,
    WakeupSource,
)
from services.control_plane.app.triggers import evaluator as evaluator_mod
from services.control_plane.app.triggers.ast import (
    PredicateSpec,
    TriggerSpecError,
    parse_spec,
)
from services.control_plane.app.triggers.config import (
    CONCURRENT_MUTATION_REARM_MINUTES,
    IDEMPOTENCY_SCOPE,
    MAX_REARM_GENERATIONS,
    TRIGGER_EVAL_MAX_ATTEMPTS,
    schedule_name_for,
)
from services.control_plane.app.triggers.evaluator import Evaluation, Tri
from services.control_plane.app.triggers.outcomes import (
    Outcome,
    classify_false,
    rearm_delay,
)
from services.control_plane.app.triggers.projection import (
    BindingUnresolved,
    Projection,
    ProjectionSnapshot,
    ProjectionUnavailable,
    build_projection,
)
from services.control_plane.app.triggers.registry import resolve_field

__all__ = [
    "FIRE_REASON_BY_TYPE",
    "NOOP_EVENT_TYPES",
    "PREVIEW_LABEL",
    "PROPOSAL_MODEL_ID",
    "CommitReceipt",
    "CommitRequest",
    "KernelUnavailableError",
    "TriggerEvaluationOutcome",
    "TriggerKernel",
    "TriggerSnapshot",
    "TriggerStore",
    "TriggerWake",
    "evaluate_trigger",
    "manual_wake",
    "outbox_event_types_for",
    "scheduler_wake",
    "sweeper_wake",
]

#: A deliberate marker: a judge can run ``WHERE model_id LIKE 'deterministic%'``
#: and see every canonical change that no language model participated in.
#:
#: The value is ``deterministic.kernel`` and not ``deterministic:trigger-eval``,
#: which §10.1 prints. ``ck_memory_proposals_model`` is a closed ``IN`` list
#: (``0005_kernel_control.py``, repointed by ``0009``) and has never admitted the
#: colon form, so every fire would have been refused at the ``memory_proposals``
#: INSERT. ``0009``'s own comment settles which id is meant: "the deterministic
#: Memory Kernel writes its own proposals (SYSTEM_DERIVATION, TRIGGER_EVALUATION)
#: and is the only canonical writer. Dropping it would make the Kernel unable to
#: record its own derivations."
#:
#: Nothing is lost by the merge: ``memory_proposals.proposal_type`` is
#: ``TRIGGER_EVALUATION`` on exactly these rows, so "which deterministic
#: derivation was this" is still answerable from the row, by a column whose
#: vocabulary is closed by its own CHECK.
PROPOSAL_MODEL_ID = "deterministic.kernel"

PREVIEW_LABEL = "PREVIEW — no state was changed"

#: Every non-fire emits exactly this. Publishing the no-op is not noise: "the
#: trigger woke and correctly did nothing" is the observation
#: ``provenance_trigger_false_wake_ratio`` is built from, and a metric emitted
#: only on success measures nothing.
NOOP_EVENT_TYPES: tuple[str, ...] = ("trigger.noop.v1",)

#: §9.10. One fire reason per trigger type, so the reason a judge reads names
#: the deadline that elapsed rather than restating "the predicate was true".
FIRE_REASON_BY_TYPE: Mapping[str, TriggerReasonCode] = {
    "COMMITMENT_DEADLINE": TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID,
    "RESPONSE_DEADLINE": TriggerReasonCode.RESPONSE_DEADLINE_MISSED,
    "CONFLICT_TIMEOUT": TriggerReasonCode.CONFLICT_UNRESOLVED_TIMEOUT,
    "WARRANTY_WINDOW": TriggerReasonCode.WARRANTY_WINDOW_CLOSING,
}


class KernelUnavailableError(RuntimeError):
    """The Memory Kernel could not be reached.

    Distinct from a refused commit: a refusal is information (the case moved),
    while this is an absence of information, and the only safe response to an
    absence of information is to do nothing and stay armed.
    """


# ---------------------------------------------------------------------------
# The wake envelope — §9.5.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TriggerWake:
    """One invitation to re-evaluate. Identity only.

    There is no amount, no status, no due date, no predicate and no decision in
    this object, and ``test_the_wake_envelope_carries_no_decidable_fact``
    asserts that field by field. The payload is frozen at arm time — months
    before delivery — which makes it structurally impossible for it to carry
    current truth. ``scheduled_for`` is what the schedule *intended*, kept for
    the trace, never compared against anything.
    """

    wake_id: str
    trigger_id: uuid.UUID
    evaluation_version: int
    source: WakeupSource
    scheduled_for: datetime
    trace_id: uuid.UUID

    @staticmethod
    def identity_fields() -> tuple[str, ...]:
        return (
            "wake_id",
            "trigger_id",
            "evaluation_version",
            "source",
            "scheduled_for",
            "trace_id",
        )

    @property
    def idempotency_key(self) -> str:
        """§9.9. ``(scope, key)`` is unique, and the row goes in with the effect."""
        return self.wake_id


def scheduler_wake(
    *,
    trigger_id: uuid.UUID,
    evaluation_version: int,
    scheduled_for: datetime,
    trace_id: uuid.UUID,
) -> TriggerWake:
    """A wake whose id **is** the schedule name, so duplicates share a key.

    Every redelivery of one generation computes the same ``wake_id``; distinct
    generations compute distinct ones. Had the id counted evaluations, a
    duplicate arriving after the first evaluation completed would compute a
    different key and fire twice. That bug is designed out here.
    """
    return TriggerWake(
        wake_id=schedule_name_for(trigger_id.hex, evaluation_version),
        trigger_id=trigger_id,
        evaluation_version=evaluation_version,
        source=WakeupSource.EVENTBRIDGE_SCHEDULER,
        scheduled_for=scheduled_for,
        trace_id=trace_id,
    )


def manual_wake(
    *,
    trigger_id: uuid.UUID,
    evaluation_version: int,
    scheduled_for: datetime,
    trace_id: uuid.UUID,
    client_idempotency_key: str,
) -> TriggerWake:
    """The judge's button. Differs from a scheduler wake in exactly two fields.

    ``CANONICAL_DECISIONS.md`` -> *Trigger demonstration*: the same manual-wake
    entry point serves the false-predicate no-op and the landlord fire, and no
    canonical state is mutated and reverted for presentation. Because this
    builds an ordinary envelope and calls the ordinary function, that is
    structurally true rather than promised.
    """
    return TriggerWake(
        wake_id=f"manual:{trigger_id.hex}:v{evaluation_version}:{client_idempotency_key}",
        trigger_id=trigger_id,
        evaluation_version=evaluation_version,
        source=WakeupSource.MANUAL_DEMO,
        scheduled_for=scheduled_for,
        trace_id=trace_id,
    )


def sweeper_wake(
    *,
    trigger_id: uuid.UUID,
    evaluation_version: int,
    scheduled_for: datetime,
    trace_id: uuid.UUID,
    hour_bucket: str,
) -> TriggerWake:
    """§11.6's safety net. The database, not a scheduler, is what is due.

    Bucketed by hour so a sweeper that runs every fifteen minutes cannot
    produce four distinct keys for one logical wake.
    """
    return TriggerWake(
        wake_id=f"sweeper:{trigger_id.hex}:v{evaluation_version}:{hour_bucket}",
        trigger_id=trigger_id,
        evaluation_version=evaluation_version,
        source=WakeupSource.EVENTBRIDGE_RULE,
        scheduled_for=scheduled_for,
        trace_id=trace_id,
    )


# ---------------------------------------------------------------------------
# The boundaries.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TriggerSnapshot:
    """One ``prospective_triggers`` row and the database clock, read together.

    Together, because the guards judge ``expires_at`` and ``not_before`` against
    the database's clock and never the caller's (§11.5). A store that returned
    the row alone would force the guard to invent a clock, and inventing a clock
    is how a trigger fires a minute before its deadline.
    """

    row: Mapping[str, Any]
    db_now: datetime


class TriggerStore(Protocol):
    """Loads one trigger row, scoped to its owner.

    Returns ``None`` for both "no such trigger" and "not yours". Distinguishing
    them would make this an existence oracle for other tenants' identifiers, and
    §9.5 is explicit that authority is resolved from the row rather than from the
    envelope's copies of ``tenant_id`` and ``user_id``.
    """

    async def load(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, trigger_id: uuid.UUID
    ) -> TriggerSnapshot | None: ...


@dataclass(frozen=True, slots=True)
class CommitRequest:
    """What the Kernel is asked to commit. Assembled, never executed, here."""

    trigger_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID
    trace_id: uuid.UUID
    idempotency_scope: str
    idempotency_key: str
    request_sha256: str
    result: TriggerResult
    reason_code: TriggerReasonCode
    state_after: TriggerState
    case_revision_observed: int
    increments_case_revision: bool
    outbox_event_types: tuple[str, ...]
    evaluation_payload: Mapping[str, Any]
    proposal_type: str = "TRIGGER_EVALUATION"
    model_id: str = PROPOSAL_MODEL_ID
    prompt_version: str = "n/a"
    source_artifact_ids: tuple[uuid.UUID, ...] = ()
    evidence_ids: tuple[uuid.UUID, ...] = ()
    #: Set when the outcome re-arms: the next generation and when it wakes.
    rearm_evaluation_version: int | None = None
    rearm_not_before: datetime | None = None
    rearm_schedule_name: str | None = None


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    """What the Kernel did.

    ``revision_moved`` is not an error. It is the Kernel reporting that another
    commit landed between the projection read and the write, which means the
    evaluation was computed on data that is no longer current — and the correct
    response is to rebuild and re-evaluate, never to retry the write with the
    stale decision.
    """

    committed: bool
    revision_moved: bool
    case_revision_after: int
    proposal_id: uuid.UUID | None = None
    outbox_event_ids: tuple[uuid.UUID, ...] = ()
    idempotent_replay: bool = False


class TriggerKernel(Protocol):
    """The only canonical writer, at its transaction boundary.

    Raises:
        KernelUnavailableError: the Kernel could not be reached at all.
    """

    async def commit(self, request: CommitRequest) -> CommitReceipt: ...


# ---------------------------------------------------------------------------
# The result.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TriggerEvaluationOutcome:
    """What the evaluator decided, and everything a judge needs to check it."""

    trigger_id: uuid.UUID
    wake_id: str
    result: TriggerResult
    reason_code: TriggerReasonCode
    state_before: TriggerState
    state_after: TriggerState
    trace_id: uuid.UUID
    #: The case this evaluation was about, and ``None`` when no projection was
    #: reached. A guard outcome (not armed, stale generation, expired) terminates
    #: before the projection read, so there is no case it observed -- and
    #: reporting the trigger row's ``case_id`` there would name a case this
    #: evaluation never looked at, beside a ``case_revision_before`` that is
    #: correctly absent.
    case_id: uuid.UUID | None = None
    evaluation: Evaluation | None = None
    basis_case_revision: int | None = None
    case_revision_observed: int | None = None
    case_revision_after: int | None = None
    basis_stale: bool = False
    idempotent_replay: bool = False
    proposal_id: uuid.UUID | None = None
    outbox_event_ids: tuple[uuid.UUID, ...] = ()
    outbox_event_types: tuple[str, ...] = ()
    attempts: int = 0
    dry_run: bool = False
    not_found: bool = False
    fired_at: datetime | None = None
    rearm_evaluation_version: int | None = None
    rearm_not_before: datetime | None = None
    http_status: int = 200
    preview_label: str | None = None
    observed: Mapping[str, str] = field(default_factory=dict)

    @property
    def predicate_result(self) -> str | None:
        return None if self.evaluation is None else self.evaluation.result.value


def outbox_event_types_for(result: TriggerResult, trigger_type: str) -> tuple[str, ...]:
    """§9.10's "Emits" column.

    A fire fans out to three events; everything else emits exactly one
    ``trigger.noop.v1``. Publishing the no-op is not noise: "the trigger woke and
    correctly did nothing" is the observation ``provenance_trigger_false_wake_
    ratio`` is built from, and a healthy system has that ratio well above zero.
    """
    if result is TriggerResult.ERROR:
        # An ERROR emits nothing at all. The trigger is still ARMED and nothing
        # about the world was established, so there is no fact to publish; the
        # signal belongs on an alarm, not on the bus.
        return ()
    if result is not TriggerResult.FIRED:
        return NOOP_EVENT_TYPES
    events = ["trigger.fired.v1"]
    if trigger_type == "COMMITMENT_DEADLINE":
        events.append("commitment.overdue.v1")
    events.append("case.state_changed.v1")
    return tuple(events)


# ---------------------------------------------------------------------------
# The evaluation itself.
# ---------------------------------------------------------------------------


def _terminal(
    *,
    wake: TriggerWake,
    state_before: TriggerState,
    outcome: Outcome,
    http_status: int = 200,
    state_after: TriggerState | None = None,
    **extra: Any,
) -> TriggerEvaluationOutcome:
    """A guard or failure outcome, with the events it still emits.

    ``state_after`` is overridable for exactly one case: guard G2 reports
    ``NO_OP / TRIGGER_NOT_ARMED`` on a trigger that is already ``FIRED`` or
    ``DISARMED``, and a no-op must leave that state alone rather than resetting
    it to ``ARMED``. Everywhere else the outcome's own mapping is authoritative.
    """
    return TriggerEvaluationOutcome(
        trigger_id=wake.trigger_id,
        wake_id=wake.wake_id,
        result=outcome.result,
        reason_code=outcome.reason_code,
        state_before=state_before,
        state_after=state_after if state_after is not None else outcome.state_after,
        trace_id=wake.trace_id,
        http_status=http_status,
        # A guard outcome is never a fire, so the trigger type cannot change
        # what it emits: every non-fire emits exactly one `trigger.noop.v1`.
        # Passing a trigger type here would read as though it mattered.
        outbox_event_types=NOOP_EVENT_TYPES,
        **extra,
    )


def _request_hash(wake: TriggerWake) -> str:
    import hashlib
    import json

    canonical = json.dumps(
        {
            "wake_id": wake.wake_id,
            "trigger_id": str(wake.trigger_id),
            "evaluation_version": wake.evaluation_version,
            "source": wake.source.value,
            "scheduled_for": wake.scheduled_for.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evaluation_payload(
    *,
    wake: TriggerWake,
    row: Mapping[str, Any],
    spec: PredicateSpec,
    evaluation: Evaluation,
    outcome: Outcome,
    projection: Projection,
) -> dict[str, Any]:
    """§10.3 — the durable, replayable record.

    This is the receipt behind "nobody set this reminder". It carries the values
    the predicate saw and the verdict of every subexpression, so a judge can
    check the conclusion rather than take it. It carries no document text: the
    predicate never read any, and the observed map is keyed by registry paths.
    """
    return {
        "kind": "TRIGGER_EVALUATION",
        "wake_source": wake.source.value,
        "wake_id": wake.wake_id,
        "trigger_id": str(wake.trigger_id),
        "trigger_type": str(row["trigger_type"]),
        "evaluation_version": int(row["evaluation_version"]),
        "evaluator_code_version": evaluation.evaluator_code_version,
        "ast_version": spec.ast_version,
        "predicate_sha256": evaluation.predicate_sha256,
        "predicate_result": evaluation.result.value,
        "outcome": outcome.result.value,
        "reason_code": outcome.reason_code.value,
        "basis_case_revision": int(row["basis_case_revision"]),
        "case_revision_observed": projection.case_revision,
        "basis_stale": int(row["basis_case_revision"]) != projection.case_revision,
        "observed": dict(evaluation.observed),
        "node_trace": [
            {
                "nid": step.nid,
                "op": step.op,
                "result": step.result,
                "detail": step.detail,
            }
            for step in evaluation.node_trace
        ],
    }


def _classify(
    *,
    evaluation: Evaluation,
    projection: Projection,
    spec: PredicateSpec,
    trigger_type: str,
) -> Outcome:
    """Turn a three-valued verdict into one closed-set outcome.

    ``TRUE`` fires. ``FALSE`` is handed to :func:`classify_false`, which decides
    between disarming and continuing to watch. ``UNKNOWN`` is its own no-op with
    its own reason: it is memory correctly declining to assert something it does
    not know, and recording it as ``PREDICATE_FALSE`` would hide the data-quality
    bug that usually causes it.
    """
    if evaluation.result is Tri.TRUE:
        return Outcome(result=TriggerResult.FIRED, reason_code=FIRE_REASON_BY_TYPE[trigger_type])
    if evaluation.result is Tri.FALSE:
        return classify_false(projection, spec, trigger_type)
    return Outcome(
        result=TriggerResult.NO_OP,
        reason_code=TriggerReasonCode.PREDICATE_UNKNOWN,
        rearm=True,
    )


async def evaluate_trigger(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    wake: TriggerWake,
    store: TriggerStore,
    reader: Any,
    kernel: TriggerKernel,
    dry_run: bool = False,
) -> TriggerEvaluationOutcome:
    """The only trigger evaluation path.

    There is no ``force`` parameter. A caller cannot request a fire; it can only
    ask for the predicate to be judged against current state, and be told what
    the judgement was.

    Args:
        tenant_id, user_id: the owner. Authority comes from these and the row,
            never from the envelope.
        wake: the invitation. Identity only; nothing in it is believed.
        store: loads the trigger row and the database clock together.
        reader: a ``projection.ProjectionReader`` — one read-only snapshot of
            the case and its bound commitments.
        kernel: the only canonical writer.
        dry_run: run guards, projection and evaluator, then write nothing.
            Never the demo path: it proves nothing about the transaction, the
            revision guard or the outbox, which is most of what is interesting.
    """
    snapshot = await store.load(tenant_id=tenant_id, user_id=user_id, trigger_id=wake.trigger_id)
    if snapshot is None:
        # G1. "Not yours" and "no such row" are one answer on purpose.
        return TriggerEvaluationOutcome(
            trigger_id=wake.trigger_id,
            wake_id=wake.wake_id,
            result=TriggerResult.ERROR,
            reason_code=TriggerReasonCode.PROJECTION_FAILED,
            state_before=TriggerState.ARMED,
            state_after=TriggerState.ARMED,
            trace_id=wake.trace_id,
            not_found=True,
            http_status=404,
        )

    row = snapshot.row
    db_now = snapshot.db_now
    state_before = TriggerState(str(row["state"]))
    trigger_type = str(row["trigger_type"])
    guard = _run_guards(wake=wake, row=row, db_now=db_now, state_before=state_before)
    if guard is not None:
        return guard

    return await _evaluate_and_commit(
        tenant_id=tenant_id,
        user_id=user_id,
        wake=wake,
        row=row,
        state_before=state_before,
        trigger_type=trigger_type,
        reader=reader,
        kernel=kernel,
        dry_run=dry_run,
    )


def _run_guards(
    *,
    wake: TriggerWake,
    row: Mapping[str, Any],
    db_now: datetime,
    state_before: TriggerState,
) -> TriggerEvaluationOutcome | None:
    """G2-G6 of §9.6, in the order that section prints them.

    The order is load-bearing, not stylistic. Each guard terminates the
    evaluation, so the reason code a judge reads is the *first* thing that was
    wrong. G4 in particular precedes the projection read: an expired trigger
    must not be able to fire even if its condition is spectacularly true,
    because expiry is a policy decision that outranks the predicate, and putting
    the check first makes that non-negotiable in code rather than by convention.
    """
    if state_before is not TriggerState.ARMED:
        # G2. Pressing the demo button twice lands here, and that is a feature.
        return _terminal(
            wake=wake,
            state_before=state_before,
            outcome=Outcome(
                result=TriggerResult.NO_OP, reason_code=TriggerReasonCode.TRIGGER_NOT_ARMED
            ),
            state_after=state_before,
        )

    if wake.evaluation_version != int(row["evaluation_version"]):
        # G3. Only the current generation may act; this is what makes re-arming
        # safe, because a delivery from the schedule a re-arm replaced cannot
        # act on the trigger that replaced it.
        return _terminal(
            wake=wake,
            state_before=state_before,
            outcome=Outcome(
                result=TriggerResult.NO_OP,
                reason_code=TriggerReasonCode.STALE_SCHEDULE_GENERATION,
            ),
            http_status=409,
        )

    expires_at = row["expires_at"]
    if expires_at is not None and db_now >= expires_at:
        # G4, and the predicate is deliberately not evaluated at all.
        return _terminal(
            wake=wake,
            state_before=state_before,
            outcome=Outcome(
                result=TriggerResult.EXPIRED, reason_code=TriggerReasonCode.TRIGGER_EXPIRED
            ),
        )

    if int(row["evaluation_version"]) > MAX_REARM_GENERATIONS:
        # G6.
        return _terminal(
            wake=wake,
            state_before=state_before,
            outcome=Outcome(
                result=TriggerResult.EXPIRED,
                reason_code=TriggerReasonCode.REARM_BUDGET_EXHAUSTED,
            ),
        )

    not_before = row["not_before"]
    if not_before is not None and db_now < not_before:
        # G5. The database clock, not the scheduler, decides whether the moment
        # has arrived — which is what makes cluster clock skew harmless.
        next_version = int(row["evaluation_version"]) + 1
        return _terminal(
            wake=wake,
            state_before=state_before,
            outcome=Outcome(
                result=TriggerResult.NO_OP,
                reason_code=TriggerReasonCode.WOKE_TOO_EARLY,
                rearm=True,
            ),
            rearm_evaluation_version=next_version,
            rearm_not_before=not_before,
        )

    return None


async def _evaluate_and_commit(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    wake: TriggerWake,
    row: Mapping[str, Any],
    state_before: TriggerState,
    trigger_type: str,
    reader: Any,
    kernel: TriggerKernel,
    dry_run: bool,
) -> TriggerEvaluationOutcome:
    """Steps 6-9 of §9: read, re-evaluate, propose, commit — with the retry loop.

    The loop rebuilds the projection from fresh reads on every attempt. Reusing a
    computed decision across a rebuilt read would reintroduce exactly the
    stale-action bug this subsystem exists to prevent, and is the same rule as
    "do not reuse computed derived state from a failed transaction without
    reloading the aggregate".
    """
    try:
        spec = parse_spec(dict(row["predicate_ast"]), resolve_field)
    except TriggerSpecError:
        # §11.6: an ast_version bump fails closed. Never fire, never disarm.
        return _terminal(
            wake=wake,
            state_before=state_before,
            outcome=Outcome(
                result=TriggerResult.ERROR, reason_code=TriggerReasonCode.PROJECTION_FAILED
            ),
            http_status=500,
        )

    commitment_ids = tuple(binding.commitment_id for binding in spec.bindings)
    last: tuple[Evaluation, Projection, Outcome] | None = None

    for attempt in range(1, TRIGGER_EVAL_MAX_ATTEMPTS + 1):
        try:
            snapshot: ProjectionSnapshot = await reader.read(
                tenant_id=tenant_id,
                user_id=user_id,
                case_id=row["case_id"],
                commitment_ids=commitment_ids,
            )
        except ProjectionUnavailable:
            # Nothing was read. An empty projection would be evaluated against
            # zeroes and could DISARM on the strength of them, which is
            # `D-00-005` with an obligation attached: "not loaded" rendered as
            # "nothing is owed". The trigger stays ARMED for operator inspection.
            return _terminal(
                wake=wake,
                state_before=state_before,
                outcome=Outcome(
                    result=TriggerResult.ERROR,
                    reason_code=TriggerReasonCode.PROJECTION_FAILED,
                ),
                http_status=500,
                attempts=attempt,
            )
        try:
            projection = build_projection(
                case_row=snapshot.case_row,
                commitment_rows=snapshot.commitment_rows,
                trigger_row=row,
                spec=spec,
            )
        except BindingUnresolved:
            # §10.4. An ERROR, not a no-op, and the trigger stays ARMED for
            # operator inspection: silently forgetting an obligation because of
            # an internal error is the worst failure this product has.
            return _terminal(
                wake=wake,
                state_before=state_before,
                outcome=Outcome(
                    result=TriggerResult.ERROR,
                    reason_code=TriggerReasonCode.BINDING_UNRESOLVED,
                ),
                http_status=500,
                attempts=attempt,
            )

        # THE re-check. Reached through the module global so PV_SABOTAGE can
        # neuter it; a `from`-import here would copy the reference before the
        # rebind and the sabotage would silently never arrive.
        evaluation = evaluator_mod.reevaluate_predicate(spec, projection.values)
        outcome = _classify(
            evaluation=evaluation,
            projection=projection,
            spec=spec,
            trigger_type=trigger_type,
        )
        last = (evaluation, projection, outcome)

        if dry_run:
            return _completed(
                wake=wake,
                row=row,
                state_before=state_before,
                evaluation=evaluation,
                projection=projection,
                outcome=outcome,
                trigger_type=trigger_type,
                receipt=CommitReceipt(
                    committed=False,
                    revision_moved=False,
                    case_revision_after=projection.case_revision,
                ),
                attempts=attempt,
                dry_run=True,
            )

        request = _build_request(
            wake=wake,
            row=row,
            spec=spec,
            evaluation=evaluation,
            projection=projection,
            outcome=outcome,
            trigger_type=trigger_type,
        )
        try:
            receipt = await kernel.commit(request)
        except KernelUnavailableError:
            return _terminal(
                wake=wake,
                state_before=state_before,
                outcome=Outcome(
                    result=TriggerResult.ERROR,
                    reason_code=TriggerReasonCode.KERNEL_UNAVAILABLE,
                ),
                http_status=503,
                attempts=attempt,
            )

        if receipt.revision_moved:
            continue

        return _completed(
            wake=wake,
            row=row,
            state_before=state_before,
            evaluation=evaluation,
            projection=projection,
            outcome=outcome,
            trigger_type=trigger_type,
            receipt=receipt,
            attempts=attempt,
        )

    # Attempts exhausted. Never fire optimistically; re-arm in five minutes and
    # let the world settle.
    assert last is not None
    _, projection, _ = last
    return _terminal(
        wake=wake,
        state_before=state_before,
        outcome=Outcome(
            result=TriggerResult.NO_OP,
            reason_code=TriggerReasonCode.CONCURRENT_CASE_MUTATION,
            rearm=True,
        ),
        attempts=TRIGGER_EVAL_MAX_ATTEMPTS,
        case_revision_observed=projection.case_revision,
        rearm_evaluation_version=int(row["evaluation_version"]) + 1,
        rearm_not_before=projection.db_now + timedelta(minutes=CONCURRENT_MUTATION_REARM_MINUTES),
    )


def _build_request(
    *,
    wake: TriggerWake,
    row: Mapping[str, Any],
    spec: PredicateSpec,
    evaluation: Evaluation,
    projection: Projection,
    outcome: Outcome,
    trigger_type: str,
) -> CommitRequest:
    event_types = outbox_event_types_for(outcome.result, trigger_type)
    rearm_version = int(row["evaluation_version"]) + 1 if outcome.rearm else None
    rearm_not_before = (
        projection.db_now + rearm_delay(trigger_type, int(row["evaluation_version"]))
        if outcome.rearm
        else None
    )
    return CommitRequest(
        trigger_id=wake.trigger_id,
        tenant_id=projection.tenant_id,
        user_id=projection.user_id,
        case_id=projection.case_id,
        trace_id=wake.trace_id,
        idempotency_scope=IDEMPOTENCY_SCOPE,
        idempotency_key=wake.idempotency_key,
        request_sha256=_request_hash(wake),
        result=outcome.result,
        reason_code=outcome.reason_code,
        state_after=outcome.state_after,
        case_revision_observed=projection.case_revision,
        increments_case_revision=outcome.increments_case_revision,
        outbox_event_types=event_types,
        evaluation_payload=_evaluation_payload(
            wake=wake,
            row=row,
            spec=spec,
            evaluation=evaluation,
            outcome=outcome,
            projection=projection,
        ),
        rearm_evaluation_version=rearm_version,
        rearm_not_before=rearm_not_before,
        rearm_schedule_name=(
            schedule_name_for(wake.trigger_id.hex, rearm_version)
            if rearm_version is not None
            else None
        ),
    )


def _completed(
    *,
    wake: TriggerWake,
    row: Mapping[str, Any],
    state_before: TriggerState,
    evaluation: Evaluation,
    projection: Projection,
    outcome: Outcome,
    trigger_type: str,
    receipt: CommitReceipt,
    attempts: int,
    dry_run: bool = False,
) -> TriggerEvaluationOutcome:
    basis = int(row["basis_case_revision"])
    rearm_version = int(row["evaluation_version"]) + 1 if outcome.rearm else None
    return TriggerEvaluationOutcome(
        trigger_id=wake.trigger_id,
        wake_id=wake.wake_id,
        result=outcome.result,
        reason_code=outcome.reason_code,
        state_before=state_before,
        state_after=outcome.state_after,
        trace_id=wake.trace_id,
        case_id=projection.case_id,
        evaluation=evaluation,
        basis_case_revision=basis,
        case_revision_observed=projection.case_revision,
        case_revision_after=receipt.case_revision_after,
        # §9.7: this labels the evaluation. It never authorises a fire and never
        # suppresses one. `basis_stale = true` with `result = FIRED` is the
        # interesting and correct case — the world moved, and firing is still
        # right — and Judge Mode shows it.
        basis_stale=basis != projection.case_revision,
        idempotent_replay=receipt.idempotent_replay,
        proposal_id=receipt.proposal_id,
        outbox_event_ids=receipt.outbox_event_ids,
        outbox_event_types=outbox_event_types_for(outcome.result, trigger_type),
        attempts=attempts,
        dry_run=dry_run,
        fired_at=(
            projection.db_now if outcome.result is TriggerResult.FIRED and not dry_run else None
        ),
        rearm_evaluation_version=rearm_version,
        rearm_not_before=(
            projection.db_now + rearm_delay(trigger_type, int(row["evaluation_version"]))
            if outcome.rearm
            else None
        ),
        preview_label=PREVIEW_LABEL if dry_run else None,
        observed=dict(evaluation.observed),
    )
