"""The second reveal: the landlord deposit wakes itself, and it is honest.

Authority
---------
- ``docs/ARCHITECTURE.md`` §22 — "the dashboard expands the parent Move context
  and shows the old landlord deposit is still overdue because its promised
  deadline passed", which is what this file proves happens.
- ``docs/CANONICAL_DECISIONS.md`` -> *Hero dataset canon* and
  -> *Trigger demonstration*.
- ``docs/specs/16_TRIGGER_DSL.md`` §12 (the hero trigger), §13 (the manual
  path), §15 items 9, 10 and 13.

The claim the demo makes, and what would make it a lie
-------------------------------------------------------
*"Nobody set this reminder; the memory of an unmet obligation woke itself."*

Three things would make that false, and each has a test here. If the predicate
were not really true — if the demo mutated state to make it fire — the reveal
would be theatre. If the no-op demonstration reverted canonical state
afterwards, the audit story would be worse than no demonstration at all.
``CANONICAL_DECISIONS.md`` is explicit on both: "Use the same manual-wake entry
point for a false-predicate no-op and the landlord fire. Do not mutate and
secretly revert canonical state for presentation." And if the manual button
took a different code path from the scheduler, the thing being demonstrated
would not be the thing that ships.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from provenance_domain.enums import TriggerReasonCode, TriggerResult, TriggerState
from services.control_plane.app.triggers.ast import (
    TriggerSpecError,
    build_spec_document,
    parse_spec,
)
from services.control_plane.app.triggers.config import WAKE_MARGIN_SECONDS
from services.control_plane.app.triggers.evaluator import Tri, evaluate_predicate
from services.control_plane.app.triggers.projection import build_projection
from services.control_plane.app.triggers.registry import resolve_field
from services.control_plane.app.triggers.service import (
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

#: A second trigger on the same case, carrying a genuinely false predicate over
#: the *unmodified* deposit. Not a mutated copy of the hero trigger.
FALSE_TRIGGER_ID = uuid.UUID("c5a10b73-2f64-4e18-9d05-7a3b1c8e6f40")


def _world(*, predicate_ast: object | None = None, trigger_id: uuid.UUID | None = None):
    """The hero state exactly as the canon fixes it. Nothing is adjusted."""
    row = canon.trigger_row(
        trigger_id=trigger_id or canon.HERO_TRIGGER_ID,
        predicate_ast=predicate_ast,  # type: ignore[arg-type]
    )
    store = FakeTriggerStore(rows={row["id"]: row}, db_now=canon.DEMO_CLOCK_UTC)
    reader = FakeProjectionReader(
        case_row=canon.case_row(),
        commitment_rows={canon.HERO_COMMITMENT_ID: canon.commitment_row()},
    )
    return store, reader, FakeKernel()


# ---------------------------------------------------------------------------
# The canon itself.
# ---------------------------------------------------------------------------


def test_the_wake_is_the_deadline_plus_the_margin() -> None:
    """``2026-06-15T00:00:00Z`` + 60s = ``2026-06-15T00:01:00Z``.

    Both instants and the margin are canon, and this is the arithmetic that
    connects them. If ``WAKE_MARGIN_SECONDS`` moved, a date four documents agree
    on would slide silently; this fails instead.
    """
    assert canon.DEPOSIT_DUE_AT + timedelta(seconds=WAKE_MARGIN_SECONDS) == canon.TRIGGER_WAKE_AT
    assert canon.DEPOSIT_DUE_AT.isoformat() == "2026-06-15T00:00:00+00:00"
    assert canon.TRIGGER_WAKE_AT.isoformat() == "2026-06-15T00:01:00+00:00"


def test_the_deposit_is_ninety_five_days_overdue_at_the_demo_clock() -> None:
    """The figure the dashboard renders, derived rather than typed in."""
    elapsed = canon.DEMO_CLOCK_UTC - canon.DEPOSIT_DUE_AT
    assert elapsed.days == canon.DAYS_OVERDUE_AT_DEMO == 95


# ---------------------------------------------------------------------------
# The fire — matrix item 9, G10.3.
# ---------------------------------------------------------------------------


async def test_the_landlord_deposit_fires_on_the_seeded_facts() -> None:
    """G10.3. The predicate is genuinely true; nothing is arranged for the demo.

    Deadline 2026-06-15, already past. Paid: nothing — ``outstanding_amount`` is
    still 1800.0000. Case: WAITING, not resolved. The predicate is therefore true
    at any moment after 15 June 2026, and the manual button fires it live in
    front of judges through the same code the scheduler would have used.
    """
    store, reader, kernel = _world()
    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=manual_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
            client_idempotency_key="judge-demo-fire",
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )

    assert outcome.result is TriggerResult.FIRED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID
    assert outcome.state_after is TriggerState.FIRED
    assert outcome.predicate_result == "TRUE"


async def test_the_fire_prints_the_three_field_values_the_gate_asks_for() -> None:
    """G10.3 names them: ``{outstanding_amount: 1800.0000, due_at: <past>,
    status: ACTIVE}``. They come out of the evaluation's ``observed`` map, which
    is the same map written into the stored proposal payload."""
    store, reader, kernel = _world()
    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )
    assert outcome.observed["commitments.deposit.outstanding_amount"] == "1800.0000"
    assert outcome.observed["commitments.deposit.due_at"] == "2026-06-15T00:00:00Z"
    assert outcome.observed["commitments.deposit.status"] == "ACTIVE"
    assert (
        datetime.fromisoformat(
            outcome.observed["commitments.deposit.due_at"].replace("Z", "+00:00")
        )
        < canon.DEMO_CLOCK_UTC
    )


async def test_the_fire_increments_the_case_revision_exactly_once() -> None:
    """G10.3: "cases.revision incremented exactly once"."""
    store, reader, kernel = _world()
    before = kernel.case_revision
    await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )
    assert kernel.case_revision == before + 1
    assert kernel.transitions == ["TRIGGER_FIRED"]


async def test_the_fire_emits_trigger_fired_v1() -> None:
    store, reader, kernel = _world()
    await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )
    assert "trigger.fired.v1" in [event for event, _, _ in kernel.outbox]
    assert "commitment.overdue.v1" in [event for event, _, _ in kernel.outbox]


async def test_every_conjunct_of_the_hero_predicate_is_true_and_shown() -> None:
    """§10.3's ``node_trace``, which is what the Memory Trace panel renders.

    Seven conjuncts plus the AND. A judge can read each line and check the
    conclusion rather than take it — which is the difference between a demo and
    an assertion.
    """
    store, reader, kernel = _world()
    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )
    assert outcome.evaluation is not None
    trace = outcome.evaluation.node_trace
    assert len(trace) == 8
    assert all(step.result == "TRUE" for step in trace)
    ops = [step.op for step in trace]
    assert ops.count("NE") == 4
    assert "NOT_NULL" in ops and "GTE" in ops and "GT" in ops and "AND" in ops


# ---------------------------------------------------------------------------
# The honest no-op — CANONICAL_DECISIONS.md -> "Trigger demonstration".
# ---------------------------------------------------------------------------


async def test_the_no_op_demonstration_uses_a_real_false_predicate() -> None:
    """The false-predicate no-op, with no state mutated and nothing reverted.

    The second demo trigger asks whether the deposit already carries an admitted
    fulfillment. On the hero deposit it does not — nothing was ever paid — so
    the term is genuinely ``FALSE`` against exactly the rows the landlord trigger
    reads a moment later. The deposit is not touched before, during or after.
    """
    store, reader, kernel = _world(
        predicate_ast=canon.false_predicate_document(), trigger_id=FALSE_TRIGGER_ID
    )
    case_before = dict(reader.case_row)
    commitment_before = dict(reader.commitment_rows[canon.HERO_COMMITMENT_ID])

    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=manual_wake(
            trigger_id=FALSE_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
            client_idempotency_key="judge-demo-noop",
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )

    assert outcome.result is TriggerResult.NO_OP
    assert outcome.reason_code is TriggerReasonCode.PREDICATE_FALSE
    assert outcome.evaluation is not None
    assert outcome.evaluation.result is Tri.FALSE
    # Nothing was arranged and nothing was put back.
    assert dict(reader.case_row) == case_before
    assert dict(reader.commitment_rows[canon.HERO_COMMITMENT_ID]) == commitment_before
    assert kernel.case_revision == 11
    assert kernel.transitions == []


async def test_the_false_conjunct_is_visible_in_the_trace() -> None:
    """ "This is the artifact that shows a judge precisely which conjunct was
    false — a more convincing demonstration of determinism than a fire."."""
    spec = parse_spec(canon.false_predicate_document(), resolve_field)
    store, reader, kernel = _world(
        predicate_ast=canon.false_predicate_document(), trigger_id=FALSE_TRIGGER_ID
    )
    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=manual_wake(
            trigger_id=FALSE_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
            client_idempotency_key="judge-demo-noop-2",
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )
    assert outcome.evaluation is not None
    assert outcome.evaluation.predicate_sha256 == spec.sha256
    false_steps = [step for step in outcome.evaluation.node_trace if step.result == "FALSE"]
    # Ordered by node id, so the root AND (index 0) precedes the conjunct that
    # made it false. Both are recorded: the verdict and the reason for it.
    assert [step.op for step in false_steps] == ["AND", "EQ"]
    assert false_steps[1].detail == "false EQ true"
    # The other conjunct is true, which is what makes this a *specific* answer
    # rather than "something was wrong".
    assert [step.result for step in outcome.evaluation.node_trace] == ["FALSE", "TRUE", "FALSE"]


async def test_both_demonstrations_take_the_same_entry_point() -> None:
    """One function, two envelopes that differ in their client key alone.

    ``CANONICAL_DECISIONS.md`` requires the same manual-wake entry point for
    both. Asserting that both calls are literally ``evaluate_trigger`` is the
    only version of that claim a reader can check.
    """
    fire_store, fire_reader, fire_kernel = _world()
    noop_store, noop_reader, noop_kernel = _world(
        predicate_ast=canon.false_predicate_document(), trigger_id=FALSE_TRIGGER_ID
    )

    noop = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=manual_wake(
            trigger_id=FALSE_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
            client_idempotency_key="demo-step-1",
        ),
        store=noop_store,
        reader=noop_reader,
        kernel=noop_kernel,
    )
    fire = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=manual_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
            client_idempotency_key="demo-step-2",
        ),
        store=fire_store,
        reader=fire_reader,
        kernel=fire_kernel,
    )

    assert noop.result is TriggerResult.NO_OP
    assert fire.result is TriggerResult.FIRED
    # Same evaluator, same code version, same rendering of the same rows.
    assert noop.evaluation is not None and fire.evaluation is not None
    assert noop.evaluation.evaluator_code_version == fire.evaluation.evaluator_code_version
    assert (
        noop.evaluation.observed["commitments.deposit.due_at"]
        == fire.evaluation.observed["commitments.deposit.due_at"]
    )


async def test_pressing_the_button_twice_is_a_feature() -> None:
    """§13.2. The second press hits G2 and reports ``TRIGGER_NOT_ARMED``.

    Judge Mode displays that second result as a feature, because it is one: the
    trigger fired, and a fired trigger does not fire again.
    """
    store, reader, kernel = _world()

    async def press(key: str):
        return await evaluate_trigger(
            tenant_id=canon.HERO_TENANT_ID,
            user_id=canon.HERO_USER_ID,
            wake=manual_wake(
                trigger_id=canon.HERO_TRIGGER_ID,
                evaluation_version=1,
                scheduled_for=canon.TRIGGER_WAKE_AT,
                trace_id=TRACE_ID,
                client_idempotency_key=key,
            ),
            store=store,
            reader=reader,
            kernel=kernel,
        )

    first = await press("press-1")
    assert first.result is TriggerResult.FIRED
    # The Kernel committed; the row is now FIRED.
    store.rows[canon.HERO_TRIGGER_ID]["state"] = "FIRED"
    second = await press("press-2")
    assert second.result is TriggerResult.NO_OP
    assert second.reason_code is TriggerReasonCode.TRIGGER_NOT_ARMED
    assert second.state_after is TriggerState.FIRED
    assert kernel.transitions == ["TRIGGER_FIRED"]


# ---------------------------------------------------------------------------
# Clock determinism — G10.6 and §23.13.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frozen_at",
    [
        datetime(2026, 8, 17, 9, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC),
        datetime(2027, 2, 1, 9, 0, 0, tzinfo=UTC),
    ],
)
async def test_the_reveal_does_not_depend_on_wall_clock_luck(frozen_at: datetime) -> None:
    """G10.6: identical results at both gate clocks, and at the demo clock.

    The honest asymmetry, stated where it belongs: EventBridge Scheduler runs on
    AWS wall time and cannot be frozen, so a deployed trigger is exercised by
    setting ``not_before`` into the past. That tests the evaluator and not the
    scheduler's own timing, and the gap is disclosed rather than papered over.
    """
    store, reader, kernel = _world()
    store.db_now = frozen_at
    reader.case_row["db_now"] = frozen_at
    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )
    assert outcome.result is TriggerResult.FIRED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID


async def test_the_days_overdue_figure_grows_with_the_clock_and_never_lies() -> None:
    """A later wake is *more* overdue, not differently overdue.

    "Scheduler fires late: harmless — the predicate is more true, and
    ``days_overdue`` grows."
    """
    store, reader, kernel = _world()
    later = datetime(2027, 2, 1, 9, 0, 0, tzinfo=UTC)
    store.db_now = later
    reader.case_row["db_now"] = later
    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )
    assert outcome.result is TriggerResult.FIRED
    assert (later - canon.DEPOSIT_DUE_AT).days > canon.DAYS_OVERDUE_AT_DEMO


# ---------------------------------------------------------------------------
# The Kernel's own encoding, end to end.
# ---------------------------------------------------------------------------


def _kernel_encoded_hero_predicate() -> dict[str, Any]:
    """The hero predicate built the way the Memory Kernel actually builds it.

    A real ``PredicateNode`` tree, dumped by Pydantic, wrapped in the stored
    envelope by ``build_spec_document``. Nothing here is hand-written JSON, so
    the test cannot drift from what the Kernel will store: if the contract's
    serialization changes, this changes with it and the assertions below either
    still hold or fail loudly.
    """
    from provenance_contracts.predicates import PredicateNode
    from provenance_domain.enums import PredicateOp

    deposit = "commitments.deposit"

    def field(path: str) -> PredicateNode:
        return PredicateNode(op=PredicateOp.FIELD, path=path)

    def const(value: object) -> PredicateNode:
        return PredicateNode(op=PredicateOp.CONST, value=value)  # type: ignore[arg-type]

    def ne(path: str, value: str) -> PredicateNode:
        return PredicateNode(op=PredicateOp.NE, args=(field(path), const(value)))

    node = PredicateNode(
        op=PredicateOp.AND,
        args=(
            PredicateNode(op=PredicateOp.NOT_NULL, args=(field(f"{deposit}.due_at"),)),
            PredicateNode(
                op=PredicateOp.GTE, args=(field("clock.now"), field(f"{deposit}.due_at"))
            ),
            # "0" as a string, because the money rule applies to the Kernel's
            # encoding exactly as it applies to section 12.1's.
            PredicateNode(
                op=PredicateOp.GT, args=(field(f"{deposit}.outstanding_amount"), const("0"))
            ),
            ne(f"{deposit}.status", "FULFILLED"),
            ne(f"{deposit}.status", "SUPERSEDED"),
            ne(f"{deposit}.status", "EXPIRED"),
            ne("case.status", "RESOLVED"),
        ),
    )
    return build_spec_document(
        predicate=node.model_dump(mode="json"),
        bindings={"deposit": canon.HERO_COMMITMENT_ID},
    )


async def test_a_kernel_armed_trigger_fires_on_the_same_facts() -> None:
    """The second reveal survives the Kernel's serialization, not just the spec's.

    ``16_TRIGGER_DSL.md`` section 12.1 and
    ``provenance_contracts.predicates.PredicateNode`` encode a predicate
    differently -- ``left``/``right`` versus ``args``, and a ``CONST`` with or
    without a ``type``. A trigger armed by the Kernel therefore reaches the
    evaluator in the second spelling, and if only the first parsed, prospective
    memory would work in every test and in nothing that was ever armed.
    """
    store, reader, kernel = _world(predicate_ast=_kernel_encoded_hero_predicate())
    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )

    assert outcome.result is TriggerResult.FIRED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID
    assert outcome.observed["commitments.deposit.outstanding_amount"] == "1800.0000"
    assert outcome.observed["commitments.deposit.due_at"] == "2026-06-15T00:00:00Z"
    assert outcome.evaluation is not None
    assert all(step.result == "TRUE" for step in outcome.evaluation.node_trace)


async def test_both_encodings_of_the_hero_predicate_reach_the_same_verdict() -> None:
    """Same facts, same conclusion, different bytes.

    The hashes differ on purpose: ``predicate_sha256`` identifies the bytes that
    were stored, which is what a judge is checking against the row. What must
    not differ is the verdict, or the observed values a follow-up letter quotes.
    """
    spec_store, spec_reader, spec_kernel = _world()
    kernel_store, kernel_reader, kernel_kernel = _world(
        predicate_ast=_kernel_encoded_hero_predicate()
    )

    async def run(store: Any, reader: Any, kernel: Any) -> Any:
        return await evaluate_trigger(
            tenant_id=canon.HERO_TENANT_ID,
            user_id=canon.HERO_USER_ID,
            wake=scheduler_wake(
                trigger_id=canon.HERO_TRIGGER_ID,
                evaluation_version=1,
                scheduled_for=canon.TRIGGER_WAKE_AT,
                trace_id=TRACE_ID,
            ),
            store=store,
            reader=reader,
            kernel=kernel,
        )

    from_spec = await run(spec_store, spec_reader, spec_kernel)
    from_kernel = await run(kernel_store, kernel_reader, kernel_kernel)

    assert from_spec.result is from_kernel.result
    assert from_spec.reason_code is from_kernel.reason_code
    assert from_spec.observed == from_kernel.observed
    assert from_spec.evaluation is not None
    assert from_kernel.evaluation is not None
    assert (
        from_spec.evaluation.predicate_sha256 != from_kernel.evaluation.predicate_sha256
    ), "different stored bytes must hash differently"


async def test_a_kernel_armed_trigger_also_disarms_when_the_deposit_is_paid() -> None:
    """The re-check is encoding-independent, which is the whole claim.

    A trigger that fired correctly under one spelling and fired *wrongly* under
    the other would be worse than one that never worked.
    """
    store, _, kernel = _world(predicate_ast=_kernel_encoded_hero_predicate())
    reader = FakeProjectionReader(
        case_row=canon.case_row(),
        commitment_rows={
            canon.HERO_COMMITMENT_ID: canon.commitment_row(
                status="FULFILLED", outstanding="0.0000", has_admitted_fulfillment=True
            )
        },
    )
    outcome = await evaluate_trigger(
        tenant_id=canon.HERO_TENANT_ID,
        user_id=canon.HERO_USER_ID,
        wake=scheduler_wake(
            trigger_id=canon.HERO_TRIGGER_ID,
            evaluation_version=1,
            scheduled_for=canon.TRIGGER_WAKE_AT,
            trace_id=TRACE_ID,
        ),
        store=store,
        reader=reader,
        kernel=kernel,
    )
    assert outcome.result is TriggerResult.DISARMED
    assert outcome.reason_code is TriggerReasonCode.COMMITMENT_SATISFIED
    assert kernel.case_revision == 11


# ---------------------------------------------------------------------------
# The seeded rows, against the real parser and the real evaluator.
# ---------------------------------------------------------------------------
#
# Everything above this line uses `_support.canon`, which transcribes
# `16_TRIGGER_DSL.md` section 12.1 by hand. That is the right fixture for
# testing the evaluator, and it is the wrong one for answering "will the thing
# we actually seeded fire?" -- a transcription agrees with the document it was
# copied from, not with the row the demo will read.
#
# `scripts/seed/obligations.py` authors the **stored** dialect, which is what
# lands in `prospective_triggers.predicate_ast` and what the evaluator receives
# at wake time. The Kernel lane guards the *proposal* path (contract dialect,
# bindings resolve, both ARMs commit). Nothing guarded the stored dialect until
# now, and it is the half my evaluator consumes.


def _seeded_triggers() -> list[Any]:
    from scripts.seed.obligations import TRIGGERS

    return list(TRIGGERS)


def test_every_seeded_predicate_parses_against_the_real_registry() -> None:
    """A seeded predicate that does not parse is a trigger that can never fire.

    It would arm cleanly, sit in the table for months, and then return
    ``ERROR / PROJECTION_FAILED`` at the one moment it was supposed to work.
    """
    triggers = _seeded_triggers()
    assert len(triggers) == 2, "the seed declares the deposit and damage triggers"
    for trigger in triggers:
        spec = parse_spec(trigger.predicate_ast, resolve_field)
        assert spec.ast_version == "1.0"
        assert len(spec.bindings) == 1
        assert len(spec.referenced_paths) == 5


def test_the_seed_guard_has_teeth() -> None:
    """The negative control: prove the assertion above can fail.

    A guard that passes on a corrupted input is a guard that would have passed
    on the real defect too.
    """
    import copy

    corrupted = copy.deepcopy(_seeded_triggers()[0].predicate_ast)
    corrupted["predicate"]["args"][0]["arg"]["path"] = "users.email"
    with pytest.raises(TriggerSpecError) as excinfo:
        parse_spec(corrupted, resolve_field)
    assert excinfo.value.code == "UNKNOWN_FIELD"


def _seeded_deposit_spec() -> Any:
    seeded = next(t for t in _seeded_triggers() if "deposit" in t.slug)
    return parse_spec(seeded.predicate_ast, resolve_field)


def _projection_for(spec: Any, **commitment_overrides: Any) -> Any:
    """The hero projection, keyed by whatever commitment the SEED bound.

    The binding id comes from the seeded document rather than from
    ``_support.canon``, so this exercises the row the demo will actually read.
    """
    commitment_id = spec.binding_ids()["deposit"]
    return build_projection(
        case_row=canon.case_row(),
        commitment_rows={
            commitment_id: canon.commitment_row(commitment_id=commitment_id, **commitment_overrides)
        },
        trigger_row=canon.trigger_row(),
        spec=spec,
    )


def test_the_seeded_deposit_predicate_fires_on_the_hero_facts() -> None:
    """The loop closed: the seeded row, not a transcription of it, is TRUE.

    Every other assertion in this file proves the evaluator does the right thing
    with section 12.1's document. This one proves the seed and the evaluator
    agree -- which is the claim the demo actually rests on, and the one that no
    amount of testing against a hand-copied fixture can establish.
    """
    spec = _seeded_deposit_spec()
    evaluation = evaluate_predicate(spec, _projection_for(spec).values)
    assert evaluation.result is Tri.TRUE
    assert all(step.result == "TRUE" for step in evaluation.node_trace)
    assert evaluation.observed["commitments.deposit.outstanding_amount"] == "1800.0000"
    assert evaluation.observed["commitments.deposit.due_at"] == "2026-06-15T00:00:00Z"


def test_the_seeded_deposit_predicate_is_false_once_the_deposit_is_paid() -> None:
    """The same row declines to fire when the money arrived. Same rule, both ways."""
    spec = _seeded_deposit_spec()
    projection = _projection_for(
        spec, status="FULFILLED", outstanding="0.0000", has_admitted_fulfillment=True
    )
    assert evaluate_predicate(spec, projection.values).result is Tri.FALSE


def test_the_seeded_wake_time_is_the_canon_wake_time() -> None:
    """``due_at`` + ``WAKE_MARGIN_SECONDS``, checked against the seeded row.

    ``_support.canon`` transcribes this; the seed computes it. If the two ever
    disagree the demo's "95 days overdue" stops matching the row it describes.
    """
    seeded = next(t for t in _seeded_triggers() if "deposit" in t.slug)
    assert seeded.not_before == canon.TRIGGER_WAKE_AT
    assert seeded.not_before == canon.DEPOSIT_DUE_AT + timedelta(seconds=WAKE_MARGIN_SECONDS)
