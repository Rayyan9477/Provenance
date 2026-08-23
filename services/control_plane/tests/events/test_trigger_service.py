"""``evaluate_trigger()`` — the one path every wake takes, and its guards.

Authority
---------
- ``docs/specs/16_TRIGGER_DSL.md`` §9 (lifecycle and guards G1-G7), §10 (the
  atomic fire transaction), §11 (failure cases), §13 (the manual-invoke path),
  §15 items 11-18.
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time*, "Trigger
  demonstration".
- ``docs/EXECUTION/70_TASK_PLAN.md`` T10.4: "**Re-evaluate the predicate against
  current state on wake.** The wakeup is a hint, never a truth. A scheduler
  event trusted without re-evaluation is a listed PR-rejection condition."

The claim under test, stated once
---------------------------------
Nothing in the wake envelope is believed. It carries identity — a trigger id, a
generation, a scheduled instant — and no amount, status, deadline or decision.
Everything the outcome depends on is read again, from canonical state, at wake
time. :func:`test_the_wake_envelope_carries_no_decidable_fact` asserts that
about the message, and
:func:`test_a_stale_envelope_cannot_make_a_paid_deposit_fire` asserts it about
the behaviour: an envelope frozen in June, delivered against a deposit that has
since been paid, no-ops.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from provenance_domain.enums import (
    TriggerReasonCode,
    TriggerResult,
    TriggerState,
    WakeupSource,
)
from services.control_plane.app.triggers import evaluator as evaluator_mod
from services.control_plane.app.triggers.evaluator import Tri
from services.control_plane.app.triggers.service import (
    TriggerWake,
    evaluate_trigger,
    manual_wake,
    scheduler_wake,
)
from services.control_plane.tests.events._support import canon
from services.control_plane.tests.events._support.fakes import (
    FakeKernel,
    FakeProjectionReader,
    FakeTriggerStore,
)

pytestmark = pytest.mark.unit

TRACE_ID = uuid.UUID("2b6f8c14-9d33-4a02-8e77-13c5a90b6d21")


def _store(**overrides: object) -> FakeTriggerStore:
    row = canon.trigger_row(**overrides)  # type: ignore[arg-type]
    return FakeTriggerStore(rows={row["id"]: row}, db_now=canon.DEMO_CLOCK_UTC)


def _reader(**case_overrides: object) -> FakeProjectionReader:
    return FakeProjectionReader(
        case_row=canon.case_row(**case_overrides),  # type: ignore[arg-type]
        commitment_rows={canon.HERO_COMMITMENT_ID: canon.commitment_row()},
    )


async def _evaluate(
    *,
    store: FakeTriggerStore | None = None,
    reader: FakeProjectionReader | None = None,
    kernel: FakeKernel | None = None,
    wake: TriggerWake | None = None,
    dry_run: bool = False,
):
    store = store if store is not None else _store()
    reader = reader if reader is not None else _reader()
    kernel = kernel if kernel is not None else FakeKernel()
    wake = (
        wake
        if wake is not None
        else scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        )
    )
    return await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=wake,
        store=store,
        reader=reader,
        kernel=kernel,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# The envelope carries identity and nothing else — §9.5.
# ---------------------------------------------------------------------------


def test_the_wake_envelope_carries_no_decidable_fact() -> None:
    """ "There is no amount, no status, no due date, no predicate, and no
    decision in this message."

    A reviewer can verify by inspection that nothing here could be acted upon.
    This test is that inspection, mechanised: the envelope's field names are
    compared against a closed list, so a future field carrying an amount fails
    here rather than being noticed in review.
    """
    wake = scheduler_wake(
        trigger_id=canon.HERO_TRIGGER_ID,
        evaluation_version=1,
        scheduled_for=canon.TRIGGER_WAKE_AT,
        trace_id=TRACE_ID,
    )
    assert set(wake.identity_fields()) == {
        "wake_id",
        "trigger_id",
        "evaluation_version",
        "source",
        "scheduled_for",
        "trace_id",
    }
    rendered = repr(wake)
    for forbidden in ("1800", "ACTIVE", "outstanding", "predicate", "due_at"):
        assert forbidden not in rendered


def test_the_wake_id_is_the_schedule_name_so_duplicates_share_a_key() -> None:
    wake = scheduler_wake(
        trigger_id=canon.HERO_TRIGGER_ID,
        evaluation_version=1,
        scheduled_for=canon.TRIGGER_WAKE_AT,
        trace_id=TRACE_ID,
    )
    assert wake.wake_id == f"pv-trg-{canon.HERO_TRIGGER_ID.hex}-v1"


def test_generations_get_distinct_wake_ids() -> None:
    """A re-arm produces ``-v2``, so a late ``-v1`` delivery is recognisable."""
    first = scheduler_wake(
        trigger_id=canon.HERO_TRIGGER_ID,
        evaluation_version=1,
        scheduled_for=canon.TRIGGER_WAKE_AT,
        trace_id=TRACE_ID,
    )
    second = scheduler_wake(
        trigger_id=canon.HERO_TRIGGER_ID,
        evaluation_version=2,
        scheduled_for=canon.TRIGGER_WAKE_AT,
        trace_id=TRACE_ID,
    )
    assert first.wake_id != second.wake_id


# ---------------------------------------------------------------------------
# The guards, in order — §9.6.
# ---------------------------------------------------------------------------


async def test_a_projection_that_could_not_be_read_is_an_error_and_stays_armed() -> None:
    """``D-00-005``, with an obligation attached — §10.4's rule applied one step
    earlier.

    ``BindingUnresolved`` means the rows were read and one of them was missing.
    ``ProjectionUnavailable`` means nothing was read at all: the case is not
    visible, or the reader could not obtain a snapshot. A reader that returned
    an empty projection instead would hand the evaluator zeroes, and zeroes
    evaluate — a predicate over ``outstanding_amount`` comes out FALSE and
    ``classify_false`` DISARMS. The obligation would be forgotten because of a
    permission error, which is the worst failure this product has.

    So it is an ``ERROR``, the trigger is left ``ARMED`` for operator
    inspection, and nothing is committed.
    """
    from services.control_plane.app.triggers.projection import ProjectionUnavailable

    class _Refusing:
        async def read(self, **kwargs: object) -> object:
            raise ProjectionUnavailable("the case is not visible to this owner")

    kernel = FakeKernel()
    outcome = await _evaluate(reader=_Refusing(), kernel=kernel)  # type: ignore[arg-type]

    assert outcome.result is TriggerResult.ERROR
    assert outcome.reason_code is TriggerReasonCode.PROJECTION_FAILED
    assert outcome.state_after is TriggerState.ARMED
    assert outcome.http_status == 500
    assert kernel.commits == []


async def test_g1_a_missing_trigger_is_not_found() -> None:
    outcome = await _evaluate(store=FakeTriggerStore(rows={}, db_now=canon.DEMO_CLOCK_UTC))
    assert outcome.result is TriggerResult.ERROR
    assert outcome.reason_code is TriggerReasonCode.PROJECTION_FAILED
    assert outcome.not_found is True


async def test_g1_a_cross_tenant_read_is_indistinguishable_from_a_miss() -> None:
    """Authority comes from the row, never from the payload's copies.

    §9.5: ``case_id``, ``tenant_id`` and ``user_id`` are in the envelope for log
    correlation only. Answering "wrong tenant" differently from "no such row"
    would turn this endpoint into an existence oracle for other tenants' ids.
    """
    store = _store()
    outcome = await evaluate_trigger(
        tenant_id=uuid.uuid4(),
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=_reader(),
        kernel=FakeKernel(),
    )
    assert outcome.not_found is True


async def test_g2_a_fired_trigger_no_ops() -> None:
    """Pressing the demo button twice is safe, and the second press is a feature."""
    outcome = await _evaluate(store=_store(state="FIRED"))
    assert outcome.result is TriggerResult.NO_OP
    assert outcome.reason_code is TriggerReasonCode.TRIGGER_NOT_ARMED
    assert outcome.state_after is TriggerState.FIRED


async def test_g3_a_superseded_generation_no_ops() -> None:
    """Matrix item 15. Only the current generation may act.

    This is what makes re-arming safe: a delivery from the ``-v1`` schedule that
    a re-arm replaced cannot act on the ``-v2`` trigger.
    """
    outcome = await _evaluate(
        store=_store(evaluation_version=2),
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
    )
    assert outcome.result is TriggerResult.NO_OP
    assert outcome.reason_code is TriggerReasonCode.STALE_SCHEDULE_GENERATION
    assert outcome.http_status == 409


async def test_g4_an_expired_trigger_never_evaluates_the_predicate() -> None:
    """Matrix item 12, with a spy. Expiry outranks the predicate, in code.

    An expired trigger must not fire even if its condition is spectacularly
    true. Ordering the guard before the projection read is what makes that
    non-negotiable rather than conventional — so the assertion is that the
    projection was never read at all.
    """
    reader = _reader()
    outcome = await _evaluate(
        store=_store(expires_at=canon.DEPOSIT_DUE_AT + timedelta(days=1)),
        reader=reader,
    )
    assert outcome.result is TriggerResult.EXPIRED
    assert outcome.reason_code is TriggerReasonCode.TRIGGER_EXPIRED
    assert outcome.state_after is TriggerState.EXPIRED
    assert reader.reads == 0, "the predicate must not be evaluated on an expired trigger"
    assert outcome.evaluation is None


async def test_g5_woke_too_early_does_not_fire() -> None:
    """Matrix item 13. The database clock decides whether the moment arrived.

    ``not_before`` is in the future by the database's reckoning, so the wake is
    early however confident the scheduler was. The trigger re-arms and
    ``cases.revision`` does not move.
    """
    store = _store(not_before=canon.DEMO_CLOCK_UTC + timedelta(hours=1))
    kernel = FakeKernel()
    outcome = await _evaluate(store=store, kernel=kernel)
    assert outcome.result is TriggerResult.NO_OP
    assert outcome.reason_code is TriggerReasonCode.WOKE_TOO_EARLY
    assert outcome.rearm_evaluation_version == 2
    assert kernel.case_revision == 11


async def test_g6_the_rearm_budget_is_finite() -> None:
    """After 64 generations a trigger that never resolved is a bug, not an
    obligation. It stops and alarms rather than re-arming forever."""
    outcome = await _evaluate(
        store=_store(evaluation_version=65),
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=65,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
    )
    assert outcome.result is TriggerResult.EXPIRED
    assert outcome.reason_code is TriggerReasonCode.REARM_BUDGET_EXHAUSTED


async def test_the_guards_run_in_the_documented_order() -> None:
    """An expired **and** superseded generation reports the generation first.

    §9.6 prints the guards in order and each terminates the evaluation. If the
    order drifted, the reason code a judge reads would describe the second thing
    that was wrong rather than the first.
    """
    outcome = await _evaluate(
        store=_store(evaluation_version=2, expires_at=canon.DEPOSIT_DUE_AT + timedelta(days=1)),
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
    )
    assert outcome.reason_code is TriggerReasonCode.STALE_SCHEDULE_GENERATION


# ---------------------------------------------------------------------------
# The predicate is re-evaluated — T10.4's headline requirement.
# ---------------------------------------------------------------------------


async def test_the_predicate_is_re_evaluated_against_current_state() -> None:
    """The projection is read at wake time, not carried in the envelope."""
    reader = _reader()
    outcome = await _evaluate(reader=reader)
    assert reader.reads == 1
    assert outcome.result is TriggerResult.FIRED
    assert outcome.evaluation is not None
    assert outcome.evaluation.result is Tri.TRUE


async def test_a_stale_envelope_cannot_make_a_paid_deposit_fire() -> None:
    """§11.2. The landlord paid on 13 June; the 15 June schedule still fires.

    Nothing cancelled the schedule and nothing needed to. The envelope is
    identical to the one that would have fired, and the outcome is a disarm,
    because the *state* is what was consulted. A timer-based system would have
    emailed the landlord demanding money that had already been paid.
    """
    reader = FakeProjectionReader(
        case_row=canon.case_row(),
        commitment_rows={
            canon.HERO_COMMITMENT_ID: canon.commitment_row(
                status="FULFILLED", outstanding="0.0000", has_admitted_fulfillment=True
            )
        },
    )
    kernel = FakeKernel()
    outcome = await _evaluate(reader=reader, kernel=kernel)
    assert outcome.result is TriggerResult.DISARMED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_SATISFIED
    assert outcome.state_after is TriggerState.DISARMED
    assert kernel.case_revision == 11
    assert [event for event, _, _ in kernel.outbox] == ["trigger.noop.v1"]


async def test_wake_after_case_resolved_disarms_with_a_reason() -> None:
    """Matrix item 11 and DDL §19 test 8 (``D8``).

    ``23_PHASE_GATES.md`` §23.8: an unexplained NOOP is a gate failure even
    though nothing broke. The reason code is asserted, not merely the absence
    of a fire.
    """
    kernel = FakeKernel()
    outcome = await _evaluate(reader=_reader(status="RESOLVED"), kernel=kernel)
    assert outcome.result is TriggerResult.DISARMED
    assert outcome.reason_code is TriggerReasonCode.CASE_RESOLVED
    assert outcome.state_after is TriggerState.DISARMED
    assert outcome.fired_at is None
    assert kernel.case_revision == 11
    assert kernel.transitions == []
    assert [event for event, _, _ in kernel.outbox] == ["trigger.noop.v1"]


async def test_an_unknown_predicate_is_a_no_op_with_its_own_reason() -> None:
    """UNKNOWN is not FALSE, and recording it as FALSE would hide a data bug.

    A commitment admitted with NULL amounts produces UNKNOWN forever; that is
    worth an operator alarm, and it is only visible if the reason code
    distinguishes it.
    """
    reader = FakeProjectionReader(
        case_row=canon.case_row(),
        commitment_rows={canon.HERO_COMMITMENT_ID: canon.commitment_row(outstanding=None)},
    )
    outcome = await _evaluate(reader=reader)
    assert outcome.result is TriggerResult.NO_OP
    assert outcome.reason_code is TriggerReasonCode.PREDICATE_UNKNOWN
    assert outcome.evaluation is not None
    assert outcome.evaluation.result is Tri.UNKNOWN


async def test_an_unresolved_binding_is_an_error_and_leaves_the_trigger_armed() -> None:
    """§10.4. Never fire, never disarm, and make it loud.

    "Silently forgetting an obligation because of an internal error is the worst
    possible failure mode for this product."
    """
    reader = FakeProjectionReader(case_row=canon.case_row(), commitment_rows={})
    outcome = await _evaluate(reader=reader)
    assert outcome.result is TriggerResult.ERROR
    assert outcome.reason_code is TriggerReasonCode.BINDING_UNRESOLVED
    assert outcome.state_after is TriggerState.ARMED
    assert outcome.http_status == 500


async def test_an_unparseable_stored_predicate_is_an_error_not_a_fire() -> None:
    """§11.6. An ``ast_version`` bump fails closed: alarm, stay armed."""
    broken = canon.hero_predicate_document()
    broken["ast_version"] = "9.9"
    outcome = await _evaluate(store=_store(predicate_ast=broken))
    assert outcome.result is TriggerResult.ERROR
    assert outcome.reason_code is TriggerReasonCode.PROJECTION_FAILED
    assert outcome.state_after is TriggerState.ARMED


async def test_a_kernel_that_cannot_be_reached_never_fires_optimistically() -> None:
    outcome = await _evaluate(kernel=FakeKernel(unavailable=True))
    assert outcome.result is TriggerResult.ERROR
    assert outcome.reason_code is TriggerReasonCode.KERNEL_UNAVAILABLE
    assert outcome.state_after is TriggerState.ARMED


# ---------------------------------------------------------------------------
# basis_case_revision — §9.7. It labels, it never authorises.
# ---------------------------------------------------------------------------


async def test_a_stale_basis_still_evaluates_and_may_still_fire() -> None:
    """ "A trigger armed at revision 11 and woken at revision 40 is still
    evaluated, against revision 40's data. That is the point."

    ``basis_stale = true`` with ``result = FIRED`` is the interesting and
    correct case: the world moved, and firing is still right.
    """
    kernel = FakeKernel(case_revision=40)
    outcome = await _evaluate(reader=_reader(revision=40), kernel=kernel)
    assert outcome.basis_stale is True
    assert outcome.result is TriggerResult.FIRED
    assert outcome.case_revision_observed == 40


async def test_basis_staleness_is_recorded_not_acted_on() -> None:
    outcome = await _evaluate()
    assert outcome.basis_case_revision == 11
    assert outcome.case_revision_observed == 11
    assert outcome.basis_stale is False


# ---------------------------------------------------------------------------
# Concurrency — §9.7 and matrix item 16.
# ---------------------------------------------------------------------------


async def test_a_moved_revision_rebuilds_the_projection_and_re_evaluates() -> None:
    """Re-evaluating from fresh reads is mandatory, not an optimisation.

    "Do not reuse computed derived state from a failed transaction without
    reloading the aggregate." Retrying the *write* with the stale result would
    reintroduce exactly the stale-action bug this subsystem exists to prevent,
    so the assertion is on the number of projection reads.
    """
    reader = _reader()
    kernel = FakeKernel(revision_moves_before_commit=1)

    def advance(state: FakeProjectionReader) -> None:
        state.case_row["case_revision"] = kernel.case_revision

    reader.mutate_before_read = advance
    outcome = await _evaluate(reader=reader, kernel=kernel)
    assert reader.reads == 2
    assert outcome.result is TriggerResult.FIRED
    assert outcome.attempts == 2


async def test_exhausted_attempts_no_op_rather_than_firing_optimistically() -> None:
    """Matrix item 16's tail. Never fire on a snapshot that has moved."""
    reader = _reader()
    kernel = FakeKernel(revision_moves_before_commit=99)

    def advance(state: FakeProjectionReader) -> None:
        state.case_row["case_revision"] = kernel.case_revision

    reader.mutate_before_read = advance
    outcome = await _evaluate(reader=reader, kernel=kernel)
    assert outcome.result is TriggerResult.NO_OP
    assert outcome.reason_code is TriggerReasonCode.CONCURRENT_CASE_MUTATION
    assert outcome.attempts == 3


# ---------------------------------------------------------------------------
# Idempotency — §9.9, §11.1, matrix item 14.
# ---------------------------------------------------------------------------


async def test_duplicate_wake_produces_one_effect() -> None:
    """Matrix item 14. At-least-once delivery, exactly one business effect.

    The second delivery computes the same ``wake_id``, the idempotency claim is
    inside the same transaction as the effect, and the stored result is
    replayed. One transition, one revision increment, three outbox rows — not
    six.
    """
    store = _store()
    reader = _reader()
    kernel = FakeKernel()
    wake = scheduler_wake(
        trigger_id=canon.HERO_TRIGGER_ID,
        evaluation_version=1,
        scheduled_for=canon.TRIGGER_WAKE_AT,
        trace_id=TRACE_ID,
    )
    first = await _evaluate(store=store, reader=reader, kernel=kernel, wake=wake)
    second = await _evaluate(store=store, reader=reader, kernel=kernel, wake=wake)

    assert first.result is TriggerResult.FIRED
    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert kernel.transitions == ["TRIGGER_FIRED"]
    assert kernel.case_revision == 12
    assert len(kernel.outbox) == 3


async def test_a_replay_is_not_an_error() -> None:
    """ "The caller did nothing wrong, and an error would push a benign
    duplicate into the DLQ."."""
    store = _store()
    kernel = FakeKernel()
    wake = scheduler_wake(
        trigger_id=canon.HERO_TRIGGER_ID,
        evaluation_version=1,
        scheduled_for=canon.TRIGGER_WAKE_AT,
        trace_id=TRACE_ID,
    )
    await _evaluate(store=store, kernel=kernel, wake=wake)
    replay = await _evaluate(store=store, kernel=kernel, wake=wake)
    assert replay.http_status == 200


# ---------------------------------------------------------------------------
# One entry point — §13.1, matrix item 17.
# ---------------------------------------------------------------------------


async def test_manual_and_scheduler_wakes_are_identical() -> None:
    """Matrix item 17. The manual path is not a shortcut, a mock or a fixture.

    ``CANONICAL_DECISIONS.md`` -> "Trigger demonstration" requires the same
    entry point for the false-predicate no-op and the landlord fire. The two
    envelopes differ in exactly two fields, and the evaluations they produce are
    equal field for field.
    """
    scheduled = await _evaluate(
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        )
    )
    manual = await _evaluate(
        wake=manual_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
            client_idempotency_key="judge-button-001",
        )
    )
    assert scheduled.evaluation == manual.evaluation
    assert scheduled.result is manual.result
    assert scheduled.reason_code is manual.reason_code


def test_the_two_envelopes_differ_in_exactly_two_fields() -> None:
    scheduled = scheduler_wake(
        trigger_id=canon.HERO_TRIGGER_ID,
        evaluation_version=1,
        scheduled_for=canon.TRIGGER_WAKE_AT,
        trace_id=TRACE_ID,
    )
    manual = manual_wake(
        trigger_id=canon.HERO_TRIGGER_ID,
        evaluation_version=1,
        scheduled_for=canon.TRIGGER_WAKE_AT,
        trace_id=TRACE_ID,
        client_idempotency_key="judge-button-001",
    )
    differing = {
        name
        for name in scheduled.identity_fields()
        if getattr(scheduled, name) != getattr(manual, name)
    }
    assert differing == {"source", "wake_id"}
    assert manual.source is WakeupSource.MANUAL_DEMO


async def test_the_manual_path_does_not_skip_the_not_before_gate() -> None:
    """ "A manual wake before the deadline correctly no-ops with WOKE_TOO_EARLY."

    There is no ``force`` parameter, and adding one is prohibited. If the
    manual path could skip a guard, the demo would be proving something other
    than what the product does.
    """
    outcome = await _evaluate(
        store=_store(not_before=canon.DEMO_CLOCK_UTC + timedelta(days=1)),
        wake=manual_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
            client_idempotency_key="judge-button-002",
        ),
    )
    assert outcome.reason_code is TriggerReasonCode.WOKE_TOO_EARLY


def test_there_is_no_force_parameter() -> None:
    import inspect

    signature = inspect.signature(evaluate_trigger)
    assert "force" not in signature.parameters
    assert "fire" not in signature.parameters


def test_no_branch_reads_the_wake_source_to_decide_behaviour() -> None:
    """§13.1: ``wake_source`` is a label for metrics and the trace, never a branch.

    Scanned against the source, because "we promise not to branch on it" is the
    kind of promise a later contributor breaks with one convenient ``if``.
    """
    import inspect

    from services.control_plane.app.triggers import service as service_mod

    source = inspect.getsource(service_mod)
    body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for pattern in ("if wake.source", "if source ==", "== WakeupSource.MANUAL_DEMO"):
        assert pattern not in body, f"{pattern!r} branches on the wake source"


# ---------------------------------------------------------------------------
# Dry run — §13.4, matrix item 18.
# ---------------------------------------------------------------------------


async def test_dry_run_writes_nothing() -> None:
    """Matrix item 18. Guards, projection and evaluator run; nothing commits."""
    kernel = FakeKernel()
    outcome = await _evaluate(kernel=kernel, dry_run=True)
    assert outcome.dry_run is True
    assert outcome.result is TriggerResult.FIRED
    assert outcome.evaluation is not None
    assert kernel.commits == []
    assert kernel.outbox == []
    assert kernel.case_revision == 11
    assert outcome.case_revision_after == 11


async def test_dry_run_is_not_the_demo_path_and_says_so() -> None:
    """It is read-only and therefore proves nothing about the transaction, the
    revision guard or the outbox — which is most of what is interesting."""
    outcome = await _evaluate(dry_run=True)
    assert outcome.preview_label == "PREVIEW — no state was changed"


# ---------------------------------------------------------------------------
# What a fire emits — §9.10 and §10.2 (g).
# ---------------------------------------------------------------------------


async def test_a_commitment_deadline_fire_emits_three_events() -> None:
    kernel = FakeKernel()
    outcome = await _evaluate(kernel=kernel)
    assert outcome.result is TriggerResult.FIRED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID
    assert [event for event, _, _ in kernel.outbox] == [
        "trigger.fired.v1",
        "commitment.overdue.v1",
        "case.state_changed.v1",
    ]


async def test_a_fire_increments_the_case_revision_exactly_once() -> None:
    kernel = FakeKernel()
    outcome = await _evaluate(kernel=kernel)
    assert kernel.case_revision == 12
    assert outcome.case_revision_after == 12
    assert kernel.transitions == ["TRIGGER_FIRED"]


async def test_the_stored_payload_is_the_replayable_record() -> None:
    """§10.3. This is the receipt for "nobody set this reminder"."""
    kernel = FakeKernel()
    await _evaluate(kernel=kernel)
    payload = kernel.commits[0].evaluation_payload
    assert payload["kind"] == "TRIGGER_EVALUATION"
    assert payload["outcome"] == "FIRED"
    assert payload["reason_code"] == "COMMITMENT_OVERDUE_UNPAID"
    assert payload["predicate_result"] == "TRUE"
    assert payload["evaluator_code_version"] == evaluator_mod.EVALUATOR_CODE_VERSION
    assert payload["observed"]["commitments.deposit.outstanding_amount"] == "1800.0000"
    assert payload["observed"]["commitments.deposit.due_at"] == "2026-06-15T00:00:00Z"
    assert len(payload["node_trace"]) == 8
    assert payload["basis_stale"] is False


async def test_the_proposal_is_labelled_machine_free() -> None:
    """The ``model_id`` marker, asserted as a **property** rather than a literal.

    A judge runs ``WHERE model_id LIKE 'deterministic%'`` and sees every
    canonical change no language model participated in. What makes that work is
    two things at once, and both are checked here:

    * the id is a deterministic marker, and
    * ``ck_memory_proposals_model`` admits it.

    This test previously pinned the literal ``deterministic:trigger-eval``,
    which ``16_TRIGGER_DSL.md`` §10.1 prints and which the schema has **never**
    accepted -- the migration's closed ``IN`` list carries ``deterministic.kernel``
    and three model ids. Because the only ``TriggerKernel`` was a fake, the
    literal assertion passed for as long as nothing tried to write the row. A
    state assertion agrees with whatever the code says; a property assertion
    fails when code and schema disagree, which is the failure worth catching.
    """
    from pathlib import Path

    kernel = FakeKernel()
    await _evaluate(kernel=kernel)
    request = kernel.commits[0]
    assert request.model_id.startswith("deterministic")
    migrations = Path(__file__).resolve().parents[4] / "db" / "migrations" / "versions"
    for migration in ("0005_kernel_control.py", "0009_gemini_embedding_plane.py"):
        text = (migrations / migration).read_text(encoding="utf-8")
        assert f"'{request.model_id}'" in text or f'"{request.model_id}"' in text, (
            f"{migration} does not admit model_id {request.model_id!r}; "
            "ck_memory_proposals_model would refuse every trigger proposal"
        )
    assert request.proposal_type == "TRIGGER_EVALUATION"
    assert request.source_artifact_ids == ()
    assert request.evidence_ids == ()


async def test_the_payload_carries_no_raw_text_or_credential_key() -> None:
    kernel = FakeKernel()
    await _evaluate(kernel=kernel)
    rendered = repr(kernel.commits[0].evaluation_payload)
    for forbidden in ("raw_text", "body", "token", "password", "secret"):
        assert forbidden not in rendered


# ---------------------------------------------------------------------------
# Determinism across clocks — G10.6.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frozen_at",
    [
        datetime(2026, 8, 17, 9, 0, 0, tzinfo=UTC),
        datetime(2027, 2, 1, 9, 0, 0, tzinfo=UTC),
    ],
)
async def test_the_outcome_is_identical_at_both_gate_clocks(frozen_at: datetime) -> None:
    """``G10.6``: identical pass/fail at ``2026-08-17`` and ``2027-02-01``.

    Both instants are after the June deadline, and nothing was paid, so the
    predicate is true at both. The honest asymmetry, recorded here as it is in
    the gate report: this exercises the evaluator, not EventBridge Scheduler's
    own timing, which runs on AWS wall time and cannot be frozen.
    """
    store = _store()
    store.db_now = frozen_at
    reader = _reader(db_now=frozen_at)
    outcome = await _evaluate(store=store, reader=reader)
    assert outcome.result is TriggerResult.FIRED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID


async def test_money_never_becomes_a_float_on_the_way_to_the_payload() -> None:
    kernel = FakeKernel()
    await _evaluate(kernel=kernel)
    observed = kernel.commits[0].evaluation_payload["observed"]
    assert isinstance(observed["commitments.deposit.outstanding_amount"], str)
    assert Decimal(observed["commitments.deposit.outstanding_amount"]) == Decimal("1800.0000")
