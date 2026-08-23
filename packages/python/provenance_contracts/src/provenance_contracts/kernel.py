"""What the deterministic Memory Kernel returns after one decision.

The result is a receipt, not a summary: every id it names is a row that now
exists, and the revision arithmetic is checked here so a caller cannot be
handed a receipt that describes an impossible commit.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 13, whose code this module implements,
  and section 20.7, which prints the nine tests.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, second sub-task: ``status``,
  ``reason_code`` from the closed catalogue, ``retry_count``,
  ``transaction_opened``.
- ``CANONICAL_DECISIONS.md`` -> *Kernel retry exhaustion*: the Kernel performs
  **no** side effect after the retry cap; it returns ``RETRYABLE_CONCURRENCY``
  with ``RETRY_EXHAUSTED_NOT_ENQUEUED`` and the caller re-drives over ``503`` +
  ``Retry-After``. No kernel retry queue exists.
- ``quality/23_PHASE_GATES.md`` ``G4.4``: foreign evidence is refused **before**
  a transaction opens, observable as ``transaction_opened = false``.

Recorded deviations from section 13
-----------------------------------
1. **``reason_codes`` is ``tuple[KernelReasonCode, ...]``**, not a free
   ``ReasonCode`` string. ``provenance_domain.enums.KernelReasonCode`` states
   the rule directly -- "``KernelCommitResult.reason_codes`` is a list of these
   and nothing else; a stringly-typed message here is how a closed set leaks"
   -- and T1.6 asks for "``reason_code`` from the closed catalogue". Section
   20.7's own fixture uses ``"CROSS_USER_EVIDENCE"``, which is not a member of
   that catalogue; the catalogue's spelling is ``EVIDENCE_FOREIGN_USER``. The
   discrepancy is reported rather than accommodated by relaxing the type.

2. **``transaction_opened`` is added**, because T1.6 and ``G4.4`` both require
   it and section 13 does not declare it. It **defaults to ``True``** on
   purpose. The dangerous shape is a preflight rejection that silently reads as
   "no transaction" because nobody set the flag: with a ``False`` default,
   ``G4.4`` would pass vacuously on a Kernel that never assigned the field at
   all. Defaulting to ``True`` makes ``transaction_opened=False`` an explicit
   claim about work that was **not** done, which is the claim the gate is
   auditing.

3. **``status``** is spelled ``decision`` / ``proposal_status`` here, as
   section 13 declares. The ``kernel_decisions`` **column** is ``status``
   (T2.4); the mapping is one persistence hop away and is that migration's
   business, not this contract's.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Contract,
    Money,
    ReasonCode,
    Revision,
    SafeIdentifier,
    UtcDatetime,
)
from provenance_domain.enums import (
    ACCEPTING_KERNEL_DECISIONS,
    DECISION_TO_PROPOSAL_STATUS,
    AttentionLevel,
    CaseStatus,
    CommitmentStatus,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    KernelDecision,
    KernelReasonCode,
    ProposalStatus,
    TransitionType,
    TriggerState,
)

__all__ = [
    "PREFLIGHT_DECISIONS",
    "BeliefVersionRef",
    "CommitmentChange",
    "ConflictRef",
    "KernelCommitResult",
    "StateTransitionRef",
    "TriggerChange",
]

#: Decisions reached in PHASE A, before any write transaction exists
#: (``specs/12_KERNEL_ALGORITHMS.md`` section 1.2: PHASE A is preflight, PHASE B
#: is the one SERIALIZABLE transaction). A receipt carrying one of these may not
#: claim a transaction was opened.
#:
#: ``G4.4`` names ``REJECTED_INVALID_PROVENANCE`` only. ``REJECTED_SCHEMA`` is
#: included because boundary validation is step 1 of PHASE A and therefore
#: strictly earlier still; that extension is recorded rather than assumed.
#: ``REJECTED_INVARIANT`` is deliberately **not** here: the invariant sweep is
#: re-executed inside PHASE B, so an invariant rejection may legitimately have
#: opened and rolled back a transaction.
PREFLIGHT_DECISIONS: frozenset[KernelDecision] = frozenset(
    {
        KernelDecision.REJECTED_SCHEMA,
        KernelDecision.REJECTED_INVALID_PROVENANCE,
    }
)


class BeliefVersionRef(Contract):
    """One belief version this commit created, as a receipt line."""

    belief_id: uuid.UUID
    belief_version_id: uuid.UUID
    version_no: Annotated[int, Field(ge=1)]
    predicate: SafeIdentifier
    epistemic_status: EpistemicStatus
    supersedes_version_id: uuid.UUID | None = None
    grounding_edge_count: Annotated[int, Field(ge=0)]
    is_derived: bool = False

    @model_validator(mode="after")
    def _committed_versions_are_grounded(self) -> BeliefVersionRef:
        """The read-side half of the grounding invariant.

        A committed version that is neither derived nor retracted must have
        left at least one support edge behind, or the commit that produced it
        was wrong.
        """
        if (
            not self.is_derived
            and self.epistemic_status is not EpistemicStatus.RETRACTED
            and self.grounding_edge_count < 1
        ):
            raise ValueError(
                f"belief version {self.belief_version_id} was committed with no "
                "grounding edge and no derivation"
            )
        if self.version_no > 1 and self.supersedes_version_id is None:
            raise ValueError(
                f"version {self.version_no} must name the version it supersedes; "
                "lineage may not have a gap"
            )
        return self


class ConflictRef(Contract):
    """One contradiction this commit opened, updated, or settled."""

    conflict_id: uuid.UUID
    conflict_type: ConflictType
    status: ConflictStatus
    predicate: SafeIdentifier
    requires_human: bool
    created: bool
    canonical_belief_version_id: uuid.UUID | None = None
    resolution_reason_code: ReasonCode | None = None

    @model_validator(mode="after")
    def _resolved_conflicts_explain_themselves(self) -> ConflictRef:
        if (
            self.status in (ConflictStatus.AUTO_RESOLVED, ConflictStatus.RESOLVED)
            and self.resolution_reason_code is None
        ):
            raise ValueError(
                f"conflict {self.conflict_id} is {self.status} without a reason code; "
                "two high-authority sources are never silently reconciled"
            )
        return self


class CommitmentChange(Contract):
    """One obligation whose recorded position moved."""

    commitment_id: uuid.UUID
    status_before: CommitmentStatus | None = None
    status_after: CommitmentStatus
    committed: Money | None = None
    fulfilled_before: Money | None = None
    fulfilled_after: Money | None = None
    outstanding_after: Money | None = None
    fulfillment_ids: tuple[uuid.UUID, ...] = ()
    created: bool = False

    @model_validator(mode="after")
    def _arithmetic_holds(self) -> CommitmentChange:
        """``outstanding = committed - fulfilled``, verified on the receipt.

        This is the $420 / $200 / $220 case from the hero scenario checked one
        more time on the way out of the Kernel.
        """
        if self.committed is None or self.outstanding_after is None:
            return self
        fulfilled = self.fulfilled_after
        if fulfilled is None:
            return self
        self.committed.require_same_currency(fulfilled)
        self.committed.require_same_currency(self.outstanding_after)
        expected = self.committed.amount - fulfilled.amount
        if self.outstanding_after.amount != expected:
            raise ValueError(
                f"outstanding {self.outstanding_after.amount} != "
                f"{self.committed.amount} - {fulfilled.amount}"
            )
        if self.status_after is CommitmentStatus.FULFILLED and self.outstanding_after.amount > 0:
            raise ValueError("FULFILLED with a positive outstanding amount is impossible")
        return self


class TriggerChange(Contract):
    """One prospective-memory trigger this commit armed, moved, or retired."""

    trigger_id: uuid.UUID
    state_before: TriggerState | None = None
    state_after: TriggerState
    not_before: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    schedule_name: Annotated[str, StringConstraints(max_length=64)] | None = None
    basis_case_revision: Revision
    created: bool = False


class StateTransitionRef(Contract):
    """One recorded state transition, at the revision that produced it."""

    state_transition_id: uuid.UUID
    transition_type: TransitionType
    case_revision: Revision
    from_state: Annotated[str, StringConstraints(max_length=64)] | None = None
    to_state: Annotated[str, StringConstraints(max_length=64)] | None = None
    reason_code: ReasonCode
    recorded_at: UtcDatetime


class KernelCommitResult(BoundaryContract):
    """The outcome of one Memory Kernel decision.

    Invariant 3 in receipt form. :meth:`_validate_revision_arithmetic` refuses
    to describe a commit where canonical objects changed but the case revision
    did not move, or where nothing changed but it did.
    """

    decision: KernelDecision
    proposal_id: uuid.UUID
    kernel_decision_id: uuid.UUID
    proposal_status: ProposalStatus
    trace_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID

    case_id: uuid.UUID | None = None
    case_status_after: CaseStatus | None = None
    case_revision_before: Revision | None = None
    case_revision_after: Revision | None = None
    attention_level_after: AttentionLevel | None = None

    created_claim_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=60)
    created_belief_versions: tuple[BeliefVersionRef, ...] = Field(default=(), max_length=40)
    created_or_updated_conflicts: tuple[ConflictRef, ...] = Field(default=(), max_length=20)
    commitment_changes: tuple[CommitmentChange, ...] = Field(default=(), max_length=20)
    trigger_changes: tuple[TriggerChange, ...] = Field(default=(), max_length=10)
    state_transitions: tuple[StateTransitionRef, ...] = Field(default=(), max_length=30)
    outbox_event_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=30)

    attention_required: bool = False
    reason_codes: tuple[KernelReasonCode, ...] = Field(default=(), max_length=20)
    retry_count: Annotated[int, Field(ge=0, le=10)] = 0
    transaction_opened: bool = True
    committed_at: UtcDatetime | None = None

    # -- validation -------------------------------------------------------

    @model_validator(mode="after")
    def _status_matches_decision(self) -> KernelCommitResult:
        expected = DECISION_TO_PROPOSAL_STATUS[self.decision]
        if self.proposal_status is not expected:
            raise ValueError(
                f"decision {self.decision} implies proposal status {expected}, "
                f"got {self.proposal_status}"
            )
        return self

    @model_validator(mode="after")
    def _rejections_are_empty_and_explained(self) -> KernelCommitResult:
        rejected = self.decision.value.startswith("REJECTED")
        if rejected:
            if not self.reason_codes:
                raise ValueError(
                    f"{self.decision} must carry at least one reason code; "
                    "an unexplained rejection is not auditable"
                )
            if self._wrote_anything() or self.committed_at is not None:
                raise ValueError(
                    f"{self.decision} reports having written canonical rows; "
                    "a rejected proposal writes nothing but its own decision row"
                )
        return self

    @model_validator(mode="after")
    def _transaction_opened_is_honest(self) -> KernelCommitResult:
        """``G4.4``, as a property of the receipt rather than of a log line.

        A preflight refusal happens before any write intent exists, so it may
        not claim a transaction; an accepted commit necessarily opened one, so
        it may not deny it.
        """
        if self.decision in PREFLIGHT_DECISIONS and self.transaction_opened:
            raise ValueError(
                f"{self.decision} is decided in preflight, before a transaction opens; "
                "a receipt that says otherwise hides where the refusal happened"
            )
        if self.decision in ACCEPTING_KERNEL_DECISIONS and not self.transaction_opened:
            raise ValueError(
                f"{self.decision} wrote canonical rows, so transaction_opened cannot "
                "be false; every canonical write happens inside one serializable "
                "transaction"
            )
        return self

    @model_validator(mode="after")
    def _validate_revision_arithmetic(self) -> KernelCommitResult:
        changed = self._changed_canonical_state()
        if self.decision not in ACCEPTING_KERNEL_DECISIONS:
            return self
        if self.case_id is None:
            raise ValueError("an accepted decision affecting state must name a case")
        if self.case_revision_before is None or self.case_revision_after is None:
            raise ValueError("an accepted decision must report both case revisions")
        expected = self.case_revision_before + 1 if changed else self.case_revision_before
        if self.case_revision_after != expected:
            raise ValueError(
                f"case revision must go {self.case_revision_before} -> {expected} "
                f"(changed={changed}), got {self.case_revision_after}"
            )
        for transition in self.state_transitions:
            if transition.case_revision != self.case_revision_after:
                raise ValueError(
                    "every state transition written by a commit carries that "
                    f"commit's new revision {self.case_revision_after}, got "
                    f"{transition.case_revision}"
                )
        if self.committed_at is None:
            raise ValueError("an accepted decision must report committed_at")
        return self

    @model_validator(mode="after")
    def _conflicting_accept_has_a_conflict(self) -> KernelCommitResult:
        if self.decision is KernelDecision.ACCEPTED_WITH_CONFLICT:
            if not self.created_or_updated_conflicts:
                raise ValueError(
                    "ACCEPTED_WITH_CONFLICT without a conflict row is a lie about "
                    "what the user will see"
                )
            if not self.attention_required:
                raise ValueError(
                    "a committed contradiction always raises attention; silent "
                    "contradictions are the failure this product exists to prevent"
                )
        return self

    @model_validator(mode="after")
    def _retryable_is_not_a_commit(self) -> KernelCommitResult:
        """Retry exhaustion performs no side effect and enqueues nothing.

        ``CANONICAL_DECISIONS.md`` -> *Kernel retry exhaustion*. There is no
        kernel retry queue, the control plane holds no queue-publish
        permission, and the re-drive belongs to the caller over ``503`` +
        ``Retry-After``. A receipt naming an outbox row would describe a side
        effect that the architecture makes impossible.
        """
        if self.decision is not KernelDecision.RETRYABLE_CONCURRENCY:
            return self
        if self.committed_at is not None:
            raise ValueError(
                "RETRYABLE_CONCURRENCY means the transaction rolled back; it cannot "
                "report a commit time"
            )
        if self.outbox_event_ids:
            raise ValueError(
                "RETRYABLE_CONCURRENCY performs no side effect and enqueues nothing; "
                "RETRY_EXHAUSTED_NOT_ENQUEUED means the caller re-drives the identical "
                "request, because no kernel retry queue exists"
            )
        if self.retry_count < 1:
            raise ValueError("RETRYABLE_CONCURRENCY implies at least one retry")
        return self

    # -- convenience ------------------------------------------------------

    def _wrote_anything(self) -> bool:
        return bool(self._changed_canonical_state() or self.outbox_event_ids)

    def _changed_canonical_state(self) -> bool:
        return bool(
            self.created_claim_ids
            or self.created_belief_versions
            or self.created_or_updated_conflicts
            or self.commitment_changes
            or self.trigger_changes
            or self.state_transitions
        )

    @property
    def is_accepted(self) -> bool:
        return self.decision in ACCEPTING_KERNEL_DECISIONS

    @property
    def should_wake_advocate(self) -> bool:
        """Route to the Advocate graph only for user-impacting commits."""
        return self.is_accepted and self.attention_required

    @property
    def retry_exhausted(self) -> bool:
        """True for the terminal no-side-effect outcome of the retry loop."""
        return (
            self.decision is KernelDecision.RETRYABLE_CONCURRENCY
            and KernelReasonCode.RETRY_EXHAUSTED_NOT_ENQUEUED in self.reason_codes
        )
