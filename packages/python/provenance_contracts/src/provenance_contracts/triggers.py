"""Prospective memory delivery and evaluation.

A wakeup is an invitation to re-evaluate, never an instruction to act. The
evaluator reloads the case, runs the predicate against a fresh projection, and
most of the time correctly does nothing.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 17, whose code this module implements.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, sixth sub-task.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Contract,
    IdempotencyKey,
    Revision,
    UtcDatetime,
)
from provenance_contracts.predicates import FieldPath
from provenance_domain.enums import (
    CaseStatus,
    PredicateOp,
    TriggerReasonCode,
    TriggerResult,
    TriggerState,
    TriggerType,
    WakeupSource,
)

__all__ = ["PredicateEvalStep", "TriggerEvaluationResult", "TriggerWakeup"]


class TriggerWakeup(BoundaryContract):
    """One delivery from EventBridge Scheduler to the trigger evaluator.

    Carries ``basis_case_revision`` as it was when the trigger was armed. The
    evaluator compares it against the live revision: a large gap is not an
    error, it just means the world moved and the predicate must be judged
    against the world as it is now.
    """

    wakeup_id: uuid.UUID
    trigger_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID
    trigger_type: TriggerType
    source: WakeupSource
    schedule_name: Annotated[str, StringConstraints(max_length=64)] | None = None
    scheduled_for: UtcDatetime
    delivered_at: UtcDatetime
    basis_case_revision: Revision
    evaluation_version: Annotated[int, Field(ge=0)]
    idempotency_key: IdempotencyKey
    trace_id: uuid.UUID

    @model_validator(mode="after")
    def _scheduler_wakeups_name_their_schedule(self) -> TriggerWakeup:
        if self.source is WakeupSource.EVENTBRIDGE_SCHEDULER and self.schedule_name is None:
            raise ValueError(
                "a scheduler-sourced wakeup must name its schedule so the "
                "one-time schedule can be removed after it fires"
            )
        return self


class PredicateEvalStep(Contract):
    """One node of the predicate AST, with the value it saw.

    This is what makes the second reveal legible: the trace shows the
    outstanding amount and the clock comparison both resolving true, so the
    user can see the reminder was derived, not guessed.
    """

    op: PredicateOp
    path: FieldPath | None = None
    observed_value: JsonValue | None = None
    result: bool | None = None
    depth: Annotated[int, Field(ge=0, le=8)] = 0


class TriggerEvaluationResult(BoundaryContract):
    """What the evaluator decided, and why.

    A no-op is a first-class, expected outcome and is recorded as such: a
    trigger that fires after its case resolved is a bug, and
    ``DISARMED / CASE_RESOLVED`` is how the system proves it did not.
    """

    trigger_id: uuid.UUID
    wakeup_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID
    trace_id: uuid.UUID

    evaluated_at: UtcDatetime
    current_case_revision: Revision
    current_case_status: CaseStatus
    result: TriggerResult
    state_before: TriggerState
    state_after: TriggerState
    predicate_trace: tuple[PredicateEvalStep, ...] = Field(default=(), max_length=40)
    reason_code: TriggerReasonCode
    proposal_id: uuid.UUID | None = None
    outbox_event_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def _noop_means_no_side_effects(self) -> TriggerEvaluationResult:
        if self.result is TriggerResult.NO_OP:
            if self.proposal_id is not None:
                raise ValueError(
                    f"{self.result} must not submit a memory proposal; a no-op "
                    "wakeup changes nothing"
                )
            if self.state_after is TriggerState.FIRED:
                raise ValueError(f"{self.result} cannot leave the trigger FIRED")
        if self.result is TriggerResult.FIRED and self.state_after is not TriggerState.FIRED:
            raise ValueError("a FIRED result must leave the trigger in FIRED")
        if (
            self.result is TriggerResult.DISARMED
            and self.reason_code is TriggerReasonCode.CASE_RESOLVED
            and self.current_case_status is not CaseStatus.RESOLVED
        ):
            raise ValueError(
                "DISARMED/CASE_RESOLVED claims the case is resolved but the observed "
                f"status is {self.current_case_status}"
            )
        return self
