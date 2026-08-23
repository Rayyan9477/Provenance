"""The deterministic receipts: kernel commit, domain event, trigger evaluation.

Written before ``provenance_contracts/{kernel,events,triggers}.py`` exist (T1.6).

Authority
---------
- ``specs/11_CONTRACTS.md`` section 13 (``kernel.py``), section 15
  (``events.py``), section 17 (``triggers.py``) and section 20.7, which prints
  the nine kernel tests below.
- ``specs/15_API_SPEC.md`` section 10 — the ``DomainEvent`` envelope, keyed on
  ``(aggregate_id, aggregate_version, event_type)``.
- ``CANONICAL_DECISIONS.md`` -> *Kernel retry exhaustion*: "The Kernel performs
  **no** side effect after the retry cap. It returns ``RETRYABLE_CONCURRENCY``
  with ``RETRY_EXHAUSTED_NOT_ENQUEUED``; the caller re-drives over ``503`` +
  ``Retry-After``. The control plane holds no ``sqs:*`` permission and no
  kernel retry queue exists."
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, sub-tasks 2, 4 and 6.

Why events and triggers are tested here
---------------------------------------
T1.6 names exactly six test files, and none of them is ``test_events.py`` or
``test_triggers.py``. All three contracts in this module are *machine receipts*
— what a deterministic component recorded about one decision — so they share
the file rather than being left uncovered or given a seventh file the plan does
not authorise.

Recorded deviations from section 20.7
-------------------------------------
1. ``reason_codes`` is typed ``tuple[KernelReasonCode, ...]``, not a free
   ``ReasonCode`` string. ``provenance_domain.enums.KernelReasonCode`` states
   the rule directly — "``KernelCommitResult.reason_codes`` is a list of these
   and nothing else; a stringly-typed message here is how a closed set leaks" —
   and ``EXECUTION/70_TASK_PLAN.md`` T1.6 asks for "``reason_code`` from the
   closed catalogue". Section 20.7's own fixture uses ``"CROSS_USER_EVIDENCE"``,
   which is **not** a member; the catalogue's spelling is
   ``EVIDENCE_FOREIGN_USER``, and that is what is used below. The discrepancy is
   reported rather than silently accommodated by relaxing the type.
2. ``transaction_opened`` is added. Section 13 does not declare it;
   ``EXECUTION/70_TASK_PLAN.md`` T1.6 and gate ``G4.4`` both require it, and
   ``quality/23_PHASE_GATES.md`` asserts ``transaction_opened = false`` on a
   preflight rejection. It is validated here rather than merely carried.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from provenance_contracts.events import (
    FORBIDDEN_PAYLOAD_KEYS,
    MAX_EVENT_PAYLOAD_BYTES,
    DomainEvent,
)
from provenance_contracts.kernel import ConflictRef, KernelCommitResult, StateTransitionRef
from provenance_contracts.triggers import (
    PredicateEvalStep,
    TriggerEvaluationResult,
    TriggerWakeup,
)
from provenance_domain.enums import (
    AggregateType,
    CaseStatus,
    ConflictStatus,
    ConflictType,
    EventType,
    KernelDecision,
    KernelReasonCode,
    PredicateOp,
    ProposalStatus,
    TransitionType,
    TriggerReasonCode,
    TriggerResult,
    TriggerState,
    TriggerType,
    WakeupSource,
)

NOW = datetime(2026, 6, 5, 9, 30, tzinfo=UTC)


def _conflict() -> ConflictRef:
    return ConflictRef(
        conflict_id=uuid.uuid4(),
        conflict_type=ConflictType.VALUE_CONFLICT,
        status=ConflictStatus.OPEN,
        predicate="service_terminated",
        requires_human=False,
        created=True,
    )


def _transition(revision: int) -> StateTransitionRef:
    return StateTransitionRef(
        state_transition_id=uuid.uuid4(),
        transition_type=TransitionType.CASE_STATUS,
        case_revision=revision,
        from_state="RESOLVED",
        to_state="REOPENED",
        reason_code="COUNTERPARTY_CLAIM_AFTER_CLOSE",
        recorded_at=NOW,
    )


def _result(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision": KernelDecision.ACCEPTED_WITH_CONFLICT,
        "proposal_id": uuid.uuid4(),
        "kernel_decision_id": uuid.uuid4(),
        "proposal_status": ProposalStatus.ACCEPTED_WITH_CONFLICT,
        "trace_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "case_revision_before": 7,
        "case_revision_after": 8,
        "created_claim_ids": (uuid.uuid4(), uuid.uuid4()),
        "created_or_updated_conflicts": (_conflict(),),
        "state_transitions": (_transition(8),),
        "outbox_event_ids": (uuid.uuid4(),),
        "attention_required": True,
        "committed_at": NOW,
    }
    payload.update(overrides)
    return payload


def _rejection(**overrides: Any) -> dict[str, Any]:
    payload = _result(
        created_claim_ids=(),
        created_or_updated_conflicts=(),
        state_transitions=(),
        outbox_event_ids=(),
        committed_at=None,
        attention_required=False,
        case_revision_before=None,
        case_revision_after=None,
        transaction_opened=False,
    )
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# specs/11_CONTRACTS.md section 20.7 — revision arithmetic and honest receipts
# ---------------------------------------------------------------------------


def test_the_hero_commit_validates() -> None:
    result = KernelCommitResult(**_result())
    assert result.is_accepted
    assert result.should_wake_advocate


def test_revision_must_advance_by_exactly_one() -> None:
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(case_revision_after=9))
    assert "case revision must go 7 -> 8" in str(excinfo.value)


def test_a_noop_must_not_advance_the_revision() -> None:
    with pytest.raises(ValidationError):
        KernelCommitResult(
            **_result(
                created_claim_ids=(),
                created_or_updated_conflicts=(),
                state_transitions=(),
                decision=KernelDecision.ACCEPTED,
                proposal_status=ProposalStatus.ACCEPTED,
                attention_required=False,
            )
        )


def test_state_transitions_carry_the_new_revision() -> None:
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(state_transitions=(_transition(7),)))
    assert "carries that commit's new revision 8" in str(excinfo.value)


def test_decision_and_proposal_status_cannot_disagree() -> None:
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(proposal_status=ProposalStatus.ACCEPTED))
    assert "implies proposal status" in str(excinfo.value)


def test_a_rejection_writes_nothing() -> None:
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(
            **_result(
                decision=KernelDecision.REJECTED_INVALID_PROVENANCE,
                proposal_status=ProposalStatus.REJECTED_INVALID_PROVENANCE,
                reason_codes=(KernelReasonCode.EVIDENCE_FOREIGN_USER,),
            )
        )
    assert "writes nothing but its own decision row" in str(excinfo.value)


def test_a_rejection_must_be_explained() -> None:
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(
            **_rejection(
                decision=KernelDecision.REJECTED_INVARIANT,
                proposal_status=ProposalStatus.REJECTED_INVARIANT,
                transaction_opened=True,
            )
        )
    assert "at least one reason code" in str(excinfo.value)


def test_a_committed_conflict_always_raises_attention() -> None:
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(attention_required=False))
    assert "silent contradictions" in str(excinfo.value)


def test_retryable_concurrency_is_not_a_commit() -> None:
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(
            **_result(
                decision=KernelDecision.RETRYABLE_CONCURRENCY,
                proposal_status=ProposalStatus.SUBMITTED,
                retry_count=5,
            )
        )
    assert "rolled back" in str(excinfo.value)


# ---------------------------------------------------------------------------
# T1.6 sub-task 2 — transaction_opened and RETRY_EXHAUSTED_NOT_ENQUEUED
# ---------------------------------------------------------------------------


def test_a_preflight_rejection_never_opened_a_transaction() -> None:
    """G4.4. Foreign evidence is refused before any write intent exists."""
    refused = KernelCommitResult(
        **_rejection(
            decision=KernelDecision.REJECTED_INVALID_PROVENANCE,
            proposal_status=ProposalStatus.REJECTED_INVALID_PROVENANCE,
            reason_codes=(KernelReasonCode.EVIDENCE_FOREIGN_USER,),
        )
    )
    assert refused.transaction_opened is False

    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(
            **_rejection(
                decision=KernelDecision.REJECTED_INVALID_PROVENANCE,
                proposal_status=ProposalStatus.REJECTED_INVALID_PROVENANCE,
                reason_codes=(KernelReasonCode.EVIDENCE_FOREIGN_USER,),
                transaction_opened=True,
            )
        )
    assert "before a transaction opens" in str(excinfo.value)


def test_an_accepted_commit_must_admit_it_opened_a_transaction() -> None:
    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(**_result(transaction_opened=False))
    assert "transaction_opened" in str(excinfo.value)


def test_retry_exhaustion_enqueues_nothing_and_performs_no_side_effect() -> None:
    exhausted = KernelCommitResult(
        **_rejection(
            decision=KernelDecision.RETRYABLE_CONCURRENCY,
            proposal_status=ProposalStatus.SUBMITTED,
            reason_codes=(
                KernelReasonCode.RETRYABLE_CONCURRENCY,
                KernelReasonCode.RETRY_EXHAUSTED_NOT_ENQUEUED,
            ),
            retry_count=5,
            transaction_opened=True,
        )
    )
    assert exhausted.outbox_event_ids == ()
    assert exhausted.committed_at is None
    assert exhausted.is_accepted is False

    with pytest.raises(ValidationError) as excinfo:
        KernelCommitResult(
            **_rejection(
                decision=KernelDecision.RETRYABLE_CONCURRENCY,
                proposal_status=ProposalStatus.SUBMITTED,
                reason_codes=(KernelReasonCode.RETRY_EXHAUSTED_NOT_ENQUEUED,),
                retry_count=5,
                transaction_opened=True,
                outbox_event_ids=(uuid.uuid4(),),
            )
        )
    assert "enqueues nothing" in str(excinfo.value)


def test_a_reason_code_outside_the_closed_catalogue_is_refused() -> None:
    """See recorded deviation 1 in this module's docstring."""
    with pytest.raises(ValidationError):
        KernelCommitResult(
            **_rejection(
                decision=KernelDecision.REJECTED_INVALID_PROVENANCE,
                proposal_status=ProposalStatus.REJECTED_INVALID_PROVENANCE,
                reason_codes=("CROSS_USER_EVIDENCE",),
            )
        )


# ---------------------------------------------------------------------------
# specs/15_API_SPEC.md section 10 — the DomainEvent envelope
# ---------------------------------------------------------------------------


def _event(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_type": EventType.CASE_REOPENED,
        "aggregate_type": AggregateType.CASE,
        "aggregate_id": uuid.uuid4(),
        "aggregate_version": 8,
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "payload": {
            "reason_code": "COUNTERPARTY_CLAIM_AFTER_CLOSE",
            "previous_status": "RESOLVED",
            "new_status": "REOPENED",
            "attention_level": "URGENT",
        },
    }
    payload.update(overrides)
    return payload


def test_a_domain_event_is_keyed_on_aggregate_version_and_type() -> None:
    event = DomainEvent(**_event())
    assert (event.aggregate_id, event.aggregate_version, event.event_type) == (
        event.aggregate_id,
        8,
        EventType.CASE_REOPENED,
    )
    assert event.dedupe_key("advocate_waker") == ("advocate_waker", event.event_id)


def test_an_event_may_not_lie_about_its_aggregate() -> None:
    with pytest.raises(ValidationError) as excinfo:
        DomainEvent(**_event(aggregate_type=AggregateType.TRIGGER))
    assert "is an CASE event" in str(excinfo.value) or "CASE" in str(excinfo.value)


def test_an_event_payload_carries_ids_not_documents() -> None:
    assert "exact_text" in FORBIDDEN_PAYLOAD_KEYS
    with pytest.raises(ValidationError) as excinfo:
        DomainEvent(
            **_event(payload={"conflict": {"exact_text": "Invoice for $186.00 due 30 June"}})
        )
    assert "publish ids, not contents" in str(excinfo.value)


def test_an_event_payload_is_capped_far_below_the_bus_limit() -> None:
    assert MAX_EVENT_PAYLOAD_BYTES == 16 * 1024
    with pytest.raises(ValidationError) as excinfo:
        DomainEvent(**_event(payload={"blob": "x" * (MAX_EVENT_PAYLOAD_BYTES + 1)}))
    assert "reference the aggregate" in str(excinfo.value)


# ---------------------------------------------------------------------------
# specs/11_CONTRACTS.md section 17 — prospective memory
# ---------------------------------------------------------------------------


def _wakeup(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "wakeup_id": uuid.uuid4(),
        "trigger_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "trigger_type": TriggerType.COMMITMENT_DEADLINE,
        "source": WakeupSource.EVENTBRIDGE_SCHEDULER,
        "schedule_name": "pv-trigger-deposit-2026-08-17",
        "scheduled_for": NOW,
        "delivered_at": NOW,
        "basis_case_revision": 3,
        "evaluation_version": 1,
        "idempotency_key": "wakeup:deposit:2026-08-17",
        "trace_id": uuid.uuid4(),
    }
    payload.update(overrides)
    return payload


def test_a_scheduler_wakeup_must_name_the_schedule_it_came_from() -> None:
    assert TriggerWakeup(**_wakeup()).schedule_name is not None
    with pytest.raises(ValidationError) as excinfo:
        TriggerWakeup(**_wakeup(schedule_name=None))
    assert "must name its schedule" in str(excinfo.value)


def test_a_no_op_wakeup_changes_nothing() -> None:
    """A trigger that wakes and correctly does nothing is the expected case."""
    quiet = TriggerEvaluationResult(
        trigger_id=uuid.uuid4(),
        wakeup_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        evaluated_at=NOW,
        current_case_revision=3,
        current_case_status=CaseStatus.WAITING,
        result=TriggerResult.NO_OP,
        state_before=TriggerState.ARMED,
        state_after=TriggerState.ARMED,
        predicate_trace=(PredicateEvalStep(op=PredicateOp.AND, result=False, depth=0),),
        reason_code=TriggerReasonCode.PREDICATE_FALSE,
    )
    assert quiet.proposal_id is None

    with pytest.raises(ValidationError) as excinfo:
        TriggerEvaluationResult(
            trigger_id=uuid.uuid4(),
            wakeup_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            case_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            evaluated_at=NOW,
            current_case_revision=3,
            current_case_status=CaseStatus.WAITING,
            result=TriggerResult.NO_OP,
            state_before=TriggerState.ARMED,
            state_after=TriggerState.ARMED,
            reason_code=TriggerReasonCode.PREDICATE_FALSE,
            proposal_id=uuid.uuid4(),
        )
    assert "must not submit a memory proposal" in str(excinfo.value)


def test_the_landlord_deposit_trigger_fires_from_a_predicate_not_a_reminder() -> None:
    fired = TriggerEvaluationResult(
        trigger_id=uuid.uuid4(),
        wakeup_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        evaluated_at=NOW,
        current_case_revision=3,
        current_case_status=CaseStatus.WAITING,
        result=TriggerResult.FIRED,
        state_before=TriggerState.ARMED,
        state_after=TriggerState.FIRED,
        predicate_trace=(
            PredicateEvalStep(op=PredicateOp.AND, result=True, depth=0),
            PredicateEvalStep(
                op=PredicateOp.GT,
                path="commitments.deposit.outstanding_amount",
                observed_value="1800.00",
                result=True,
                depth=1,
            ),
            PredicateEvalStep(
                op=PredicateOp.GTE,
                path="clock.now",
                observed_value="2026-08-17T09:00:00Z",
                result=True,
                depth=1,
            ),
        ),
        reason_code=TriggerReasonCode.COMMITMENT_OVERDUE_UNPAID,
        proposal_id=uuid.uuid4(),
    )
    assert fired.state_after is TriggerState.FIRED
    assert [step.observed_value for step in fired.predicate_trace if step.path] == [
        "1800.00",
        "2026-08-17T09:00:00Z",
    ]


def test_a_disarm_claiming_the_case_resolved_must_have_observed_that() -> None:
    with pytest.raises(ValidationError) as excinfo:
        TriggerEvaluationResult(
            trigger_id=uuid.uuid4(),
            wakeup_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            case_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            evaluated_at=NOW,
            current_case_revision=3,
            current_case_status=CaseStatus.WAITING,
            result=TriggerResult.DISARMED,
            state_before=TriggerState.ARMED,
            state_after=TriggerState.DISARMED,
            reason_code=TriggerReasonCode.CASE_RESOLVED,
        )
    assert "claims the case is resolved" in str(excinfo.value)
