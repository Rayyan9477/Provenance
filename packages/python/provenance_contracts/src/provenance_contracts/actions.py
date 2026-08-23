"""Advocate output and the human-facing action record.

Every factual sentence in an outbound message must cite State Proof support
ids, and each cited span must literally appear in the body at the offsets it
claims. Both are checked here, deterministically, before a human ever sees the
draft.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 16, whose code this module implements, and
  section 20.6.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, fifth sub-task: ``approval_draft_sha256``
  and ``basis_case_revision`` are **required** on the approved form.

The approved form binds three things at once
--------------------------------------------
``basis_case_revision`` is required on every intent and must agree with the
draft it carries. ``approval_draft_sha256`` is required the moment the record
reaches any post-approval status, because a status past APPROVED with no
recorded hash means an action could execute content no human ever saw.
:meth:`ActionIntentView.executability` re-checks the revision, the hash, and
the supporting belief versions immediately before dispatch, and fails closed.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Contract,
    IdempotencyKey,
    ReasonCode,
    Revision,
    Sha256Hex,
    UtcDatetime,
    content_hash,
)
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import ActionState, ActionType, ExecutionStatus, ModelTier

__all__ = [
    "DRAFT_HASH_EXCLUDE",
    "FORBIDDEN_OUTBOUND_TERMS",
    "POST_APPROVAL_STATES",
    "ActionExecutionView",
    "ActionIntentView",
    "DraftAction",
    "DraftClaim",
    "ExecutabilityVerdict",
]

#: Excluded from the draft hash: generation metadata is not what the human
#: approved. Editing the body changes the hash; regenerating the identical body
#: with a newer prompt version does not.
DRAFT_HASH_EXCLUDE: frozenset[str] = frozenset({"draft_id", "generated_at", "generated_by"})

#: The Advocate must not mention internal scores or architecture in outbound
#: user communication. Kept deliberately narrow so it catches leaked internals
#: without censoring ordinary words: each entry is a term that has no innocent
#: reading in a letter to a landlord or a billing department. Section 23 risk 3
#: records that this is a backstop behind the Advocate prompt, not a control,
#: and that the correct response to a false positive is to narrow the list.
FORBIDDEN_OUTBOUND_TERMS: Final[tuple[str, ...]] = (
    "belief_version",
    "belief version id",
    "memory kernel",
    "state proof",
    "confidence score",
    "epistemic",
    "embedding",
    "vector index",
    "cockroachdb",
    "langgraph",
    "bedrock",
    "prompt",
    "system prompt",
    "large language model",
)

#: Every state at or past approval. Reaching one of these without a recorded
#: approval hash is the defect :meth:`ActionIntentView._approval_is_complete_or_absent`
#: exists to make unconstructable.
POST_APPROVAL_STATES: Final[frozenset[ActionState]] = frozenset(
    {
        ActionState.APPROVED,
        ActionState.EXECUTING,
        ActionState.EXECUTED,
        ActionState.FAILED_RETRYABLE,
        ActionState.FAILED_FINAL,
        ActionState.CANCELLED_STALE,
    }
)

Body = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
Sentence = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class DraftClaim(Contract):
    """One factual assertion inside a draft, with its grounding.

    ``support_ids`` must be non-empty and must resolve inside
    ``StateProof.support_ids()``. The span offsets make the check mechanical
    rather than a matter of interpretation.
    """

    claim_id: Annotated[str, StringConstraints(pattern=r"^dc_[0-9a-z]{1,16}$")]
    sentence_or_span: Sentence
    char_start: Annotated[int, Field(ge=0)]
    char_end: Annotated[int, Field(ge=1)]
    support_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10)
    support_kind: Literal["BELIEF_VERSION", "EVIDENCE", "COMMITMENT", "CONFLICT"]

    @model_validator(mode="after")
    def _span_is_sane(self) -> DraftClaim:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.char_end - self.char_start != len(self.sentence_or_span):
            raise ValueError(
                "span length does not match the quoted sentence; the citation "
                "must be checkable against the body by offset"
            )
        return self


class DraftAction(BoundaryContract):
    """A proposed outbound communication. Never sent from this object.

    The Advocate's read tools are ``get_state_proof`` and
    ``get_action_policy``; its only write tool is ``create_action_intent``.
    There is no send tool anywhere in the agent's surface, so an injected
    instruction saying "send this now" has nothing to call.
    """

    draft_id: uuid.UUID
    case_id: uuid.UUID
    basis_case_revision: Revision
    basis_proof_hash: Sha256Hex
    action_type: ActionType
    channel: Literal["EMAIL"] = "EMAIL"
    recipient: Annotated[str, StringConstraints(max_length=320)]
    subject: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    body: Body
    claims: tuple[DraftClaim, ...] = Field(default=(), max_length=30)
    requested_outcome: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    tone: Literal["NEUTRAL", "FIRM", "CONCILIATORY"] = "NEUTRAL"
    unresolved_risks: tuple[Annotated[str, StringConstraints(max_length=400)], ...] = Field(
        default=(), max_length=8
    )
    generated_by: ModelAttribution
    generated_at: UtcDatetime

    @model_validator(mode="after")
    def _require_support_and_spans(self) -> DraftAction:
        """L11. Every claim cites support, and every span is real.

        This is the draft-grounding gate: 100% of factual outbound assertions
        must have at least one State Proof support id. ``support_ids`` is
        declared ``min_length=1``, so the only remaining question is whether
        the span is honest, which is what the offset check answers.
        """
        for claim in self.claims:
            if claim.char_end > len(self.body):
                raise ValueError(f"claim {claim.claim_id} cites offsets past the end of the body")
            actual = self.body[claim.char_start : claim.char_end]
            if actual != claim.sentence_or_span:
                raise ValueError(
                    f"claim {claim.claim_id} quotes text that is not at the offsets "
                    "it names; a citation that cannot be located is not a citation"
                )
        ids = [c.claim_id for c in self.claims]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate claim_id in draft")
        return self

    @model_validator(mode="after")
    def _no_internal_vocabulary(self) -> DraftAction:
        haystack = f"{self.subject}\n{self.body}".lower()
        leaked = sorted({t for t in FORBIDDEN_OUTBOUND_TERMS if t in haystack})
        if leaked:
            raise ValueError(
                f"outbound draft leaks internal vocabulary {leaked}; the recipient "
                "is a landlord or a billing department, not an engineer"
            )
        return self

    @model_validator(mode="after")
    def _drafted_by_tier_r(self) -> DraftAction:
        if self.generated_by.tier is not ModelTier.R:
            raise ValueError("advocacy drafting is a Tier R task")
        return self

    def sha256(self) -> str:
        """The exact hash bound at approval and re-checked at execution."""
        return content_hash(self, exclude=DRAFT_HASH_EXCLUDE)

    def validate_against_proof(self, support_ids: frozenset[uuid.UUID]) -> tuple[str, ...]:
        """Return the ids of claims whose support is not in the current proof.

        An empty result means the draft is fully grounded. A non-empty result
        means one repair attempt, then ``ActionState.NEEDS_REVIEW`` with the
        warning attached -- never a silent send.
        """
        return tuple(
            claim.claim_id
            for claim in self.claims
            if not set(claim.support_ids).issubset(support_ids)
        )


class ActionExecutionView(Contract):
    """One dispatch attempt, with the exact request it sent."""

    execution_id: uuid.UUID
    attempt_no: Annotated[int, Field(ge=1, le=5)]
    provider: Annotated[str, StringConstraints(max_length=64)]
    provider_correlation_id: Annotated[str, StringConstraints(max_length=255)] | None = None
    request_sha256: Sha256Hex
    status: ExecutionStatus
    error_code: ReasonCode | None = None
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None


class ExecutabilityVerdict(Contract):
    """The answer to "may this be sent right now". Fails closed."""

    allowed: bool
    blocking_reasons: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def _allowed_means_unblocked(self) -> ExecutabilityVerdict:
        if self.allowed and self.blocking_reasons:
            raise ValueError("an allowed verdict cannot carry blocking reasons")
        if not self.allowed and not self.blocking_reasons:
            raise ValueError("a refusal must say why")
        return self


class ActionIntentView(BoundaryContract):
    """The permissioned action record, as the UI and the executor see it.

    Approval binds three things at once: the case revision, the exact draft
    hash, and the supporting belief versions. All three are re-checked at
    execution time by :meth:`executability`.
    """

    action_intent_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    case_id: uuid.UUID
    action_type: ActionType
    status: ActionState
    recipient: Annotated[str, StringConstraints(max_length=320)]

    draft: DraftAction
    draft_sha256: Sha256Hex
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    supporting_belief_versions: tuple[uuid.UUID, ...] = Field(default=(), max_length=40)
    basis_case_revision: Revision

    created_by_agent_run_id: uuid.UUID | None = None
    approved_by_user_id: uuid.UUID | None = None
    approved_at: UtcDatetime | None = None
    approval_draft_sha256: Sha256Hex | None = None

    idempotency_key: IdempotencyKey
    executions: tuple[ActionExecutionView, ...] = Field(default=(), max_length=5)
    warnings: tuple[Annotated[str, StringConstraints(max_length=400)], ...] = Field(
        default=(), max_length=8
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def _hash_matches_draft(self) -> ActionIntentView:
        if self.draft.sha256() != self.draft_sha256:
            raise ValueError(
                "draft_sha256 does not match the rendered draft; the record and "
                "the content have diverged"
            )
        if self.draft.case_id != self.case_id:
            raise ValueError("draft belongs to a different case")
        if self.draft.basis_case_revision != self.basis_case_revision:
            raise ValueError("draft and intent disagree about the basis revision")
        return self

    @model_validator(mode="after")
    def _approval_is_complete_or_absent(self) -> ActionIntentView:
        approval_fields = (
            self.approved_by_user_id,
            self.approved_at,
            self.approval_draft_sha256,
        )
        if any(f is not None for f in approval_fields) and not all(
            f is not None for f in approval_fields
        ):
            raise ValueError("an approval must record who, when, and exactly what was approved")
        if self.status in POST_APPROVAL_STATES and self.approval_draft_sha256 is None:
            raise ValueError(
                f"status {self.status} without a recorded approval hash means an "
                "action could execute content no human ever saw"
            )
        return self

    def executability(
        self,
        *,
        current_case_revision: int,
        current_belief_version_ids: frozenset[uuid.UUID],
        has_successful_execution: bool,
    ) -> ExecutabilityVerdict:
        """The five staleness checks, evaluated together.

        The executor calls this immediately before dispatch, inside the same
        read as the case load. Any failure means NEEDS_REVIEW or
        CANCELLED_STALE, never an automatic send. ``SUPPORT_BELIEF_SUPERSEDED``
        is returned rather than silently continuing when a cited belief has
        been revised: that is the difference between "we sent a letter citing a
        fact" and "we sent a letter citing a fact we no longer hold".
        """
        reasons: list[str] = []
        if self.status is not ActionState.APPROVED:
            reasons.append("NOT_APPROVED")
        if current_case_revision != self.basis_case_revision:
            reasons.append("CASE_REVISION_CHANGED")
        if self.approval_draft_sha256 is None or self.approval_draft_sha256 != self.draft.sha256():
            reasons.append("DRAFT_HASH_CHANGED")
        if not set(self.supporting_belief_versions).issubset(current_belief_version_ids):
            reasons.append("SUPPORT_BELIEF_SUPERSEDED")
        if has_successful_execution:
            reasons.append("ALREADY_EXECUTED")
        return ExecutabilityVerdict(allowed=not reasons, blocking_reasons=tuple(reasons))
