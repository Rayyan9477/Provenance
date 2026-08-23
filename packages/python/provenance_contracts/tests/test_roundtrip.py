"""Every boundary contract survives a dump and a reload, unchanged.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 20.9, which prints the two registry tests
  and the ``roundtrip`` helper.
- ``specs/11_CONTRACTS.md`` section 18: ``CONTRACT_REGISTRY`` is the single
  place a new boundary contract is registered, and "two tests iterate it, so a
  contract added to a module and forgotten there is untested by construction".
- ``EXECUTION/70_TASK_PLAN.md`` T1.6 acceptance: "the Hypothesis round-trip
  test builds every boundary model, dumps, reloads, and asserts equality".

Why Hypothesis rather than one fixture per contract
----------------------------------------------------
Section 20.9 prints a ``roundtrip`` helper and says each contract "has a fixture
in ``tests/fixtures/``". A single hand-written fixture per contract proves the
contract round-trips *that one payload*. The failures this test exists to catch
are field-shaped, not payload-shaped: a ``Decimal`` that serialises to a float,
a ``datetime`` that loses its offset, a ``frozenset`` that reloads as a list, a
tuple field that comes back as a list and compares unequal. Those appear only
when the values vary. Each builder below is a strategy over the *valid* shape of
one contract -- the cross-field validators mean a naive
``st.builds(Model)`` would generate almost nothing constructible -- and
Hypothesis varies every scalar inside it.

The two registry tests from section 20.9 are kept exactly as printed, plus one
more that closes the registry against the class hierarchy in both directions.
"""

from __future__ import annotations

import ast
import json
import pathlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

from provenance_contracts import CONTRACT_REGISTRY
from provenance_contracts.actions import (
    FORBIDDEN_OUTBOUND_TERMS,
    ActionIntentView,
    DraftAction,
    DraftClaim,
)
from provenance_contracts.base import SCHEMA_VERSION, BoundaryContract, Money
from provenance_contracts.events import DomainEvent
from provenance_contracts.identity import CapabilityBinding, InternalPrincipal, Principal
from provenance_contracts.ingestion import (
    ArtifactMetadata,
    ContentBlock,
    ExtractionResult,
    NormalizedContent,
    SourceLocator,
)
from provenance_contracts.kernel import ConflictRef, KernelCommitResult
from provenance_contracts.proof import (
    BeliefProof,
    BeliefVersionProof,
    CaseSnapshot,
    EvidenceProof,
    GroundingEdgeProof,
    StateProof,
)
from provenance_contracts.proposal import MemoryProposal, ProposalIdentity, ProposedClaim
from provenance_contracts.resolution import (
    ModelAttribution,
    ResolutionAssessment,
    ResolvedIdentity,
)
from provenance_contracts.retrieval import IdentityCandidate, RetrievalContext
from provenance_contracts.triggers import (
    PredicateEvalStep,
    TriggerEvaluationResult,
    TriggerWakeup,
)
from provenance_domain.enums import (
    EVENT_AGGREGATE_TYPE,
    ActionState,
    ActionType,
    ActorType,
    AggregateType,
    ArtifactSourceType,
    AttentionLevel,
    CaseStatus,
    CaseType,
    ClaimKind,
    ConflictStatus,
    ConflictType,
    ContentBlockKind,
    EpistemicStatus,
    EventType,
    EvidenceType,
    IdentityCandidateKind,
    KernelDecision,
    MemoryMode,
    Modality,
    ModelTier,
    OAuthScope,
    ParserStatus,
    PredicateOp,
    ProposalStatus,
    ProposalType,
    RelationshipStatus,
    SourceClass,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    TriggerReasonCode,
    TriggerResult,
    TriggerState,
    TriggerType,
    ValueType,
    WakeupSource,
    WorkloadKind,
)
from tools import contract_lint as lint

# ---------------------------------------------------------------------------
# Primitive strategies
# ---------------------------------------------------------------------------

UUIDS = st.uuids(version=4)
TIMES = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 1, 1),
    timezones=st.just(UTC),
)
CONFIDENCES = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("1"), places=4, allow_nan=False, allow_infinity=False
)
AMOUNTS = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("100000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
SHA256 = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
SHORT_TEXT = (
    st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=60)
    .map(str.strip)
    .filter(bool)
)
REVISIONS = st.integers(min_value=0, max_value=10_000)

# Text bound for a recipient outside the system. DraftAction refuses any subject
# or body containing a FORBIDDEN_OUTBOUND_TERMS entry, so a generator for those
# fields must respect the same rule -- otherwise the suite intermittently
# generates a model the contract forbids and reports the correct refusal as a
# failure. Filtered against the live tuple rather than a copied list, so adding a
# term to the contract cannot leave this strategy behind.
OUTBOUND_TEXT = SHORT_TEXT.filter(
    lambda text: not any(term in text.lower() for term in FORBIDDEN_OUTBOUND_TERMS)
)
BLOCK_SUFFIX = st.text(alphabet="0123456789abcdef", min_size=1, max_size=12)


def _at_least(first: datetime, delta_seconds: int) -> datetime:
    return first + timedelta(seconds=delta_seconds)


#: Every event type whose aggregate is the case, derived from the frozen map
#: rather than copied, so a new case event is exercised without an edit here.
_CASE_EVENTS: list[EventType] = sorted(
    event for event, aggregate in EVENT_AGGREGATE_TYPE.items() if aggregate is AggregateType.CASE
)


# ---------------------------------------------------------------------------
# One builder per registered contract
# ---------------------------------------------------------------------------


@st.composite
def _model_attribution(draw: st.DrawFn, tier: ModelTier = ModelTier.E) -> ModelAttribution:
    return ModelAttribution(
        model_id=draw(st.sampled_from(["us.anthropic.claude-haiku-4-5", "global.anthropic.x"])),
        tier=tier,
        prompt_version=draw(st.sampled_from(["interpreter-1.3", "advocate-1.1"])),
        graph_name="ingestion_graph",
        graph_version="1.2.0",
    )


@st.composite
def _principal(draw: st.DrawFn) -> Principal:
    issued = draw(TIMES)
    return Principal(
        tenant_id=draw(UUIDS),
        user_id=draw(UUIDS),
        cognito_sub=str(draw(UUIDS)),
        email=draw(st.none() | st.just("hero@example.test")),
        display_name=draw(st.none() | SHORT_TEXT),
        scopes=frozenset(draw(st.sets(st.sampled_from(sorted(OAuthScope)), max_size=3))),
        token_issued_at=issued,
        token_expires_at=_at_least(issued, draw(st.integers(min_value=1, max_value=3600))),
        request_id=draw(UUIDS),
        trace_id=draw(UUIDS),
    )


@st.composite
def _internal_principal(draw: st.DrawFn) -> InternalPrincipal:
    issued = draw(TIMES)
    with_binding = draw(st.booleans())
    binding = (
        CapabilityBinding(
            binding_id=draw(UUIDS),
            binding_kind="AGENT_RUN",
            tenant_id=draw(UUIDS),
            user_id=draw(UUIDS),
            expires_at=_at_least(issued, 600),
            status="ACTIVE",
        )
        if with_binding
        else None
    )
    return InternalPrincipal(
        app_client="provenance-agent-runtime",
        workload=WorkloadKind.AGENT_RUNTIME,
        scopes=frozenset({OAuthScope.MEMORY_PROPOSE}),
        binding=binding,
        agent_run_id=draw(st.none() | UUIDS),
        token_issued_at=issued,
        token_expires_at=_at_least(issued, draw(st.integers(min_value=1, max_value=3600))),
        request_id=draw(UUIDS),
        trace_id=draw(UUIDS),
    )


@st.composite
def _artifact_metadata(draw: st.DrawFn) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=draw(UUIDS),
        tenant_id=draw(UUIDS),
        user_id=draw(UUIDS),
        source_type=draw(st.sampled_from(sorted(ArtifactSourceType))),
        mime_type=draw(st.sampled_from(["message/rfc822", "application/pdf", "text/plain"])),
        content_sha256=draw(SHA256),
        size_bytes=draw(st.integers(min_value=1, max_value=20 * 1024 * 1024)),
        sender=draw(st.none() | st.just("billing@example-isp.test")),
        subject=draw(st.none() | SHORT_TEXT),
        received_at=draw(TIMES),
        parser_status=draw(st.sampled_from(sorted(ParserStatus))),
        block_count=draw(st.integers(min_value=0, max_value=10)),
    )


@st.composite
def _content_block(draw: st.DrawFn, artifact_id: uuid.UUID, ordinal: int) -> ContentBlock:
    block_id = f"blk_{draw(BLOCK_SUFFIX)}{ordinal}"
    text = draw(SHORT_TEXT)
    return ContentBlock(
        block_id=block_id,
        artifact_id=artifact_id,
        ordinal=ordinal,
        kind=draw(st.sampled_from(sorted(ContentBlockKind))),
        text=text,
        content_sha256=draw(SHA256),
        source_locator=SourceLocator(
            kind="TEXT_SPAN", block_id=block_id, char_start=0, char_end=len(text)
        ),
    )


@st.composite
def _normalized_content(draw: st.DrawFn) -> NormalizedContent:
    artifact_id = draw(UUIDS)
    count = draw(st.integers(min_value=0, max_value=3))
    blocks = tuple(draw(_content_block(artifact_id, ordinal)) for ordinal in range(count))
    return NormalizedContent(
        artifact_id=artifact_id,
        parser_version="1.0.0",
        blocks=blocks,
        truncated=draw(st.booleans()),
    )


@st.composite
def _extraction_result(draw: st.DrawFn) -> ExtractionResult:
    return ExtractionResult(
        artifact_id=draw(UUIDS),
        agent_run_id=draw(UUIDS),
        trace_id=draw(UUIDS),
        source_block_ids=(f"blk_{draw(BLOCK_SUFFIX)}",),
        artifact_summary=draw(SHORT_TEXT),
        model_id="us.anthropic.claude-haiku-4-5",
        model_tier=ModelTier.E,
        prompt_version="interpreter-1.3",
        needs_visual_reasoning=draw(st.booleans()),
        repaired=draw(st.booleans()),
    )


@st.composite
def _identity_candidate(
    draw: st.DrawFn,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> IdentityCandidate:
    kind = draw(st.sampled_from(sorted(IdentityCandidateKind)))
    return IdentityCandidate(
        candidate_kind=kind,
        candidate_id=draw(UUIDS),
        tenant_id=tenant_id if tenant_id is not None else draw(UUIDS),
        user_id=user_id if user_id is not None else draw(UUIDS),
        label=draw(SHORT_TEXT),
        relationship_status=(
            draw(st.sampled_from(sorted(RelationshipStatus)))
            if kind is IdentityCandidateKind.RELATIONSHIP
            else None
        ),
        case_status=(
            draw(st.sampled_from(sorted(CaseStatus)))
            if kind is IdentityCandidateKind.CASE
            else None
        ),
        last_activity_at=draw(st.none() | TIMES),
        score=draw(CONFIDENCES),
    )


@st.composite
def _retrieval_context(draw: st.DrawFn) -> RetrievalContext:
    tenant_id = draw(UUIDS)
    user_id = draw(UUIDS)
    cases = tuple(
        draw(_identity_candidate(tenant_id, user_id)).model_copy(
            update={
                "candidate_kind": IdentityCandidateKind.CASE,
                "case_status": CaseStatus.OPEN,
                "relationship_status": None,
            }
        )
        for _ in range(draw(st.integers(min_value=0, max_value=2)))
    )
    return RetrievalContext(
        trace_id=draw(UUIDS),
        agent_run_id=draw(UUIDS),
        tenant_id=tenant_id,
        user_id=user_id,
        artifact_id=draw(st.none() | UUIDS),
        case_candidates=cases,
        unresolved_identity_questions=tuple(draw(st.lists(SHORT_TEXT, max_size=2))),
        retrieved_at=draw(TIMES),
    )


@st.composite
def _resolution_assessment(draw: st.DrawFn) -> ResolutionAssessment:
    return ResolutionAssessment(
        trace_id=draw(UUIDS),
        agent_run_id=draw(UUIDS),
        identity=ResolvedIdentity(
            relationship_id=draw(st.none() | UUIDS),
            case_id=None,
            confidence=draw(CONFIDENCES),
        ),
        requires_human_review=True,
        rationale_summary=draw(SHORT_TEXT),
        model=draw(_model_attribution(ModelTier.R)),
    )


@st.composite
def _memory_proposal(draw: st.DrawFn) -> MemoryProposal:
    evidence_id = draw(UUIDS)
    relationship_id = draw(UUIDS)
    artifact_id = draw(UUIDS)
    return MemoryProposal(
        proposal_id=draw(UUIDS),
        proposal_type=draw(st.sampled_from(sorted(ProposalType))),
        trace_id=draw(UUIDS),
        agent_run_id=draw(UUIDS),
        user_id=draw(UUIDS),
        source_artifact_ids=(artifact_id,),
        evidence_ids=(evidence_id,),
        identity=ProposalIdentity(
            relationship_id=relationship_id,
            case_id=draw(st.none() | UUIDS),
            confidence=draw(CONFIDENCES),
        ),
        claims=(
            ProposedClaim(
                local_id="cl_1",
                claim_kind=draw(st.sampled_from(sorted(ClaimKind))),
                subject_type=SubjectType.RELATIONSHIP,
                subject_id=relationship_id,
                predicate="billing_period_covered",
                object_type=ValueType.MONEY,
                object_value={"amount": "186.00", "currency": "USD"},
                actor_type=ActorType.COUNTERPARTY,
                actor_ref=draw(st.none() | st.just("billing@example-isp.test")),
                evidence_id=evidence_id,
                source_class=draw(st.sampled_from(sorted(SourceClass))),
                modality=draw(st.sampled_from(sorted(Modality))),
                extraction_confidence=draw(CONFIDENCES),
            ),
        ),
        unresolved_questions=tuple(draw(st.lists(SHORT_TEXT, max_size=2))),
        model=draw(_model_attribution()),
        idempotency_key=f"proposal:{artifact_id}:interpreter-1.3",
        created_at=draw(TIMES),
    )


@st.composite
def _kernel_commit_result(draw: st.DrawFn) -> KernelCommitResult:
    before = draw(REVISIONS)
    return KernelCommitResult(
        decision=KernelDecision.ACCEPTED_WITH_CONFLICT,
        proposal_id=draw(UUIDS),
        kernel_decision_id=draw(UUIDS),
        proposal_status=ProposalStatus.ACCEPTED_WITH_CONFLICT,
        trace_id=draw(UUIDS),
        tenant_id=draw(UUIDS),
        user_id=draw(UUIDS),
        case_id=draw(UUIDS),
        case_status_after=draw(st.none() | st.sampled_from(sorted(CaseStatus))),
        case_revision_before=before,
        case_revision_after=before + 1,
        attention_level_after=draw(st.none() | st.sampled_from(sorted(AttentionLevel))),
        created_claim_ids=tuple(draw(st.lists(UUIDS, max_size=3))),
        created_or_updated_conflicts=(
            ConflictRef(
                conflict_id=draw(UUIDS),
                conflict_type=draw(st.sampled_from(sorted(ConflictType))),
                status=ConflictStatus.OPEN,
                predicate="service_terminated",
                requires_human=draw(st.booleans()),
                created=True,
            ),
        ),
        outbox_event_ids=tuple(draw(st.lists(UUIDS, max_size=3))),
        attention_required=True,
        retry_count=draw(st.integers(min_value=0, max_value=10)),
        committed_at=draw(TIMES),
    )


@st.composite
def _state_proof(draw: st.DrawFn) -> StateProof:
    if draw(st.booleans()):
        return StateProof(
            proof_id=draw(UUIDS),
            generated_at=draw(TIMES),
            tenant_id=draw(UUIDS),
            user_id=draw(UUIDS),
            memory_mode=MemoryMode.OFF,
            memory_disabled_reason=draw(SHORT_TEXT),
        )
    evidence_id = draw(UUIDS)
    observed = draw(TIMES)
    text = draw(SHORT_TEXT)
    return StateProof(
        proof_id=draw(UUIDS),
        generated_at=draw(TIMES),
        tenant_id=draw(UUIDS),
        user_id=draw(UUIDS),
        case=CaseSnapshot(
            case_id=draw(UUIDS),
            case_type=draw(st.sampled_from(sorted(CaseType))),
            title=draw(SHORT_TEXT),
            status=draw(st.sampled_from(sorted(CaseStatus))),
            revision=draw(REVISIONS),
            attention_level=draw(st.sampled_from(sorted(AttentionLevel))),
            counterparty_name=draw(SHORT_TEXT),
            relationship_id=draw(UUIDS),
            opened_at=observed,
            reopened_count=draw(st.integers(min_value=0, max_value=5)),
            last_activity_at=observed,
        ),
        beliefs=(
            BeliefProof(
                belief_id=draw(UUIDS),
                subject_type=SubjectType.RELATIONSHIP,
                subject_id=draw(UUIDS),
                subject_label=draw(SHORT_TEXT),
                predicate="service_terminated",
                current_version=BeliefVersionProof(
                    belief_version_id=draw(UUIDS),
                    version_no=1,
                    value_type=ValueType.BOOLEAN,
                    value_json=draw(st.booleans()),
                    epistemic_status=EpistemicStatus.CONFIRMED,
                    belief_confidence=draw(CONFIDENCES),
                    recorded_at=observed,
                    kernel_decision_id=draw(UUIDS),
                ),
                grounding=(
                    GroundingEdgeProof(
                        support_id=draw(UUIDS),
                        source_kind=SupportSourceKind.EVIDENCE,
                        source_id=evidence_id,
                        relation=SupportRelation.SUPPORTS,
                        weight=draw(st.none() | CONFIDENCES),
                        evidence=EvidenceProof(
                            evidence_id=evidence_id,
                            artifact_id=draw(UUIDS),
                            evidence_type=draw(st.sampled_from(sorted(EvidenceType))),
                            normalized_text=text,
                            source_locator=SourceLocator(
                                kind="TEXT_SPAN",
                                block_id="blk_body1",
                                char_start=0,
                                char_end=len(text),
                            ),
                            observed_at=observed,
                            artifact_received_at=observed,
                        ),
                    ),
                ),
            ),
        ),
    )


@st.composite
def _domain_event(draw: st.DrawFn) -> DomainEvent:
    event_type = draw(st.sampled_from(sorted(_CASE_EVENTS)))
    return DomainEvent(
        event_id=draw(UUIDS),
        event_type=event_type,
        aggregate_type=AggregateType.CASE,
        aggregate_id=draw(UUIDS),
        aggregate_version=draw(REVISIONS),
        tenant_id=draw(UUIDS),
        user_id=draw(UUIDS),
        trace_id=draw(UUIDS),
        causation_id=draw(st.none() | UUIDS),
        correlation_id=draw(st.none() | UUIDS),
        occurred_at=draw(TIMES),
        payload={
            "reason_code": "COUNTERPARTY_CLAIM_AFTER_CLOSE",
            "conflict_id": str(draw(UUIDS)),
            "attempt": draw(st.integers(min_value=0, max_value=5)),
            "nested": {"previous_status": "RESOLVED"},
        },
    )


_DRAFT_BODY = (
    "Your invoice covers 1-30 June 2026. "
    "Service was confirmed cancelled on 15 May 2026 and terminated on 31 May 2026. "
    "Please withdraw the charge of USD 186.00."
)
_DRAFT_SPAN = "Service was confirmed cancelled on 15 May 2026 and terminated on 31 May 2026."
_DRAFT_START = _DRAFT_BODY.index(_DRAFT_SPAN)


@st.composite
def _draft_action(draw: st.DrawFn, case_id: uuid.UUID | None = None) -> DraftAction:
    support_id = draw(UUIDS)
    return DraftAction(
        draft_id=draw(UUIDS),
        case_id=case_id if case_id is not None else draw(UUIDS),
        basis_case_revision=draw(REVISIONS),
        basis_proof_hash=draw(SHA256),
        action_type=draw(st.sampled_from(sorted(ActionType))),
        recipient="billing@example-isp.test",
        # The subject is drawn from OUTBOUND_TEXT, not SHORT_TEXT.
        #
        # DraftAction._no_internal_vocabulary scans subject + body and refuses a
        # draft that leaks a term from FORBIDDEN_OUTBOUND_TERMS. Free ASCII text
        # can and does land on one -- Hypothesis produced a subject containing
        # "cockroachdb" and this test went red on a run that had passed minutes
        # earlier, which is the property-based suite behaving correctly rather
        # than flaking.
        #
        # The fix belongs in the STRATEGY, not the validator. A round-trip test
        # asks "does a valid model survive dump and reload"; generating an
        # INVALID model and calling the refusal a failure tests the wrong thing.
        # The refusal is asserted directly by test_draft_grounding.py, where a
        # leaked term is the point rather than an accident.
        subject=draw(OUTBOUND_TEXT),
        body=_DRAFT_BODY,
        claims=(
            DraftClaim(
                claim_id="dc_1",
                sentence_or_span=_DRAFT_SPAN,
                char_start=_DRAFT_START,
                char_end=_DRAFT_START + len(_DRAFT_SPAN),
                support_ids=(support_id,),
                support_kind="BELIEF_VERSION",
            ),
        ),
        requested_outcome=draw(SHORT_TEXT),
        tone=draw(st.sampled_from(["NEUTRAL", "FIRM", "CONCILIATORY"])),
        unresolved_risks=tuple(draw(st.lists(SHORT_TEXT, max_size=2))),
        generated_by=draw(_model_attribution(ModelTier.R)),
        generated_at=draw(TIMES),
    )


@st.composite
def _action_intent_view(draw: st.DrawFn) -> ActionIntentView:
    case_id = draw(UUIDS)
    draft = draw(_draft_action(case_id))
    approved = draw(st.booleans())
    created = draw(TIMES)
    return ActionIntentView(
        action_intent_id=draw(UUIDS),
        tenant_id=draw(UUIDS),
        user_id=draw(UUIDS),
        case_id=case_id,
        action_type=draft.action_type,
        status=ActionState.APPROVED if approved else ActionState.PROPOSED,
        recipient=draft.recipient,
        draft=draft,
        draft_sha256=draft.sha256(),
        rationale=draw(SHORT_TEXT),
        supporting_belief_versions=tuple(draw(st.lists(UUIDS, max_size=3))),
        basis_case_revision=draft.basis_case_revision,
        approved_by_user_id=draw(UUIDS) if approved else None,
        approved_at=created if approved else None,
        approval_draft_sha256=draft.sha256() if approved else None,
        idempotency_key=f"intent:{draft.draft_id}",
        warnings=tuple(draw(st.lists(SHORT_TEXT, max_size=2))),
        created_at=created,
        updated_at=created,
    )


@st.composite
def _trigger_wakeup(draw: st.DrawFn) -> TriggerWakeup:
    scheduled = draw(TIMES)
    return TriggerWakeup(
        wakeup_id=draw(UUIDS),
        trigger_id=draw(UUIDS),
        tenant_id=draw(UUIDS),
        user_id=draw(UUIDS),
        case_id=draw(UUIDS),
        trigger_type=draw(st.sampled_from(sorted(TriggerType))),
        source=WakeupSource.EVENTBRIDGE_SCHEDULER,
        schedule_name="pv-trigger-deposit-2026-08-17",
        scheduled_for=scheduled,
        delivered_at=scheduled,
        basis_case_revision=draw(REVISIONS),
        evaluation_version=draw(st.integers(min_value=0, max_value=9)),
        idempotency_key="wakeup:deposit:2026-08-17",
        trace_id=draw(UUIDS),
    )


@st.composite
def _trigger_evaluation_result(draw: st.DrawFn) -> TriggerEvaluationResult:
    return TriggerEvaluationResult(
        trigger_id=draw(UUIDS),
        wakeup_id=draw(UUIDS),
        tenant_id=draw(UUIDS),
        user_id=draw(UUIDS),
        case_id=draw(UUIDS),
        trace_id=draw(UUIDS),
        evaluated_at=draw(TIMES),
        current_case_revision=draw(REVISIONS),
        current_case_status=CaseStatus.WAITING,
        result=TriggerResult.NO_OP,
        state_before=TriggerState.ARMED,
        state_after=TriggerState.ARMED,
        predicate_trace=(
            PredicateEvalStep(op=PredicateOp.AND, result=draw(st.booleans()), depth=0),
            PredicateEvalStep(
                op=PredicateOp.GT,
                path="commitments.deposit.outstanding_amount",
                observed_value=str(draw(AMOUNTS)),
                result=draw(st.booleans()),
                depth=1,
            ),
        ),
        reason_code=TriggerReasonCode.PREDICATE_FALSE,
        proposal_id=None,
        outbox_event_ids=tuple(draw(st.lists(UUIDS, max_size=2))),
    )


#: One builder per name in ``CONTRACT_REGISTRY``. The mapping is asserted
#: complete below, so a contract added to the registry without a builder fails
#: rather than being skipped.
BUILDERS: dict[str, Callable[[], st.SearchStrategy[BaseModel]]] = {
    "Principal": _principal,
    "InternalPrincipal": _internal_principal,
    "ArtifactMetadata": _artifact_metadata,
    "NormalizedContent": _normalized_content,
    "ExtractionResult": _extraction_result,
    "IdentityCandidate": _identity_candidate,
    "RetrievalContext": _retrieval_context,
    "ResolutionAssessment": _resolution_assessment,
    "MemoryProposal": _memory_proposal,
    "KernelCommitResult": _kernel_commit_result,
    "StateProof": _state_proof,
    "DomainEvent": _domain_event,
    "DraftAction": _draft_action,
    "ActionIntentView": _action_intent_view,
    "TriggerWakeup": _trigger_wakeup,
    "TriggerEvaluationResult": _trigger_evaluation_result,
}


# ---------------------------------------------------------------------------
# specs/11_CONTRACTS.md section 20.9
# ---------------------------------------------------------------------------


def test_every_boundary_contract_declares_schema_version() -> None:
    for name, model in CONTRACT_REGISTRY.items():
        assert "schema_version" in model.model_fields, f"{name} is not a BoundaryContract"
        assert model.model_fields["schema_version"].default == SCHEMA_VERSION


def test_every_contract_emits_a_json_schema() -> None:
    for name, model in CONTRACT_REGISTRY.items():
        schema = model.model_json_schema()
        assert schema["type"] == "object", name
        assert json.dumps(schema)  # serialisable, no dangling refs


def roundtrip(instance: BaseModel) -> BaseModel:
    """Dump to JSON, re-validate, and require structural equality."""
    restored = type(instance).model_validate_json(instance.model_dump_json())
    assert restored == instance
    return restored


# ---------------------------------------------------------------------------
# T1.6 acceptance — build every boundary model, dump, reload, assert equality
# ---------------------------------------------------------------------------


def test_every_registered_contract_has_a_builder() -> None:
    """Without this, adding a contract and forgetting a builder is a silent skip."""
    assert set(BUILDERS) == set(CONTRACT_REGISTRY)


@pytest.mark.parametrize("name", sorted(CONTRACT_REGISTRY))
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_every_boundary_contract_round_trips(name: str, data: st.DataObject) -> None:
    """Build it, dump it, reload it, and require the two to be equal.

    The equality is structural, not textual: a tuple that reloads as a list, a
    ``Decimal`` that arrives as a float, or a datetime that loses its offset all
    fail here, and each of those is a real serialisation bug that a single
    hand-written fixture could pass over.
    """
    model = CONTRACT_REGISTRY[name]
    instance = data.draw(BUILDERS[name]())
    assert isinstance(instance, model)
    roundtrip(instance)


@pytest.mark.parametrize("name", sorted(CONTRACT_REGISTRY))
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_every_boundary_contract_round_trips_through_python(name: str, data: st.DataObject) -> None:
    """The same claim over ``model_dump()`` rather than ``model_dump_json()``.

    The JSON path and the Python path have different coercion rules, and a
    contract that survives one is not thereby proven to survive the other --
    ``Money`` is a string on the wire and a ``Decimal`` in memory, which is
    exactly where the two diverge.
    """
    model = CONTRACT_REGISTRY[name]
    instance = data.draw(BUILDERS[name]())
    restored = model.model_validate(instance.model_dump())
    assert restored == instance


def test_the_registry_covers_every_boundary_contract() -> None:
    """The registry and the class hierarchy may not drift apart.

    Section 18's registry is reproduced verbatim in ``__init__.py``. This test
    closes it in the other direction: a ``BoundaryContract`` that exists in the
    package and is not registered is not round-tripped, and is therefore
    untested by construction. ``ModelAttribution`` is the one documented
    exception -- it never travels alone.
    """
    import provenance_contracts

    defined: set[str] = set()
    for value in vars(provenance_contracts).values():
        if isinstance(value, type) and issubclass(value, BoundaryContract):
            if value is BoundaryContract:
                continue
            defined.add(value.__name__)

    unregistered = defined - set(CONTRACT_REGISTRY)
    assert unregistered == {"ModelAttribution"}, unregistered


def test_money_survives_the_wire_as_a_string() -> None:
    """Section 6.1, pinned here because the registry contracts embed Money.

    If this ever reloads as a float the whole round-trip suite above is
    reporting on a type that quietly changed underneath it.
    """
    money = Money(amount=Decimal("186.00"), currency="USD")
    payload = json.loads(money.model_dump_json())
    assert payload == {"amount": "186.00", "currency": "USD"}
    assert Money.model_validate_json(money.model_dump_json()) == money


# ---------------------------------------------------------------------------
# The two contract_lint rules that guard this file's subject matter
#
# `python -m tools.contract_lint` prints `3 rules, 0 violations` against the
# shipped package. Zero is only meaningful if the rules can reach a non-zero,
# and `no-sql-in-contracts` is the only one whose falsifiability
# `test_no_sql_in_contracts.py` proves. The other two guard exactly what this
# file is about -- `schema_version` on every registered contract, and Money
# never becoming a float -- so they are falsified here rather than left as two
# green lines nobody has ever seen fail.
# ---------------------------------------------------------------------------


def _synthetic(name: str, source: str) -> lint.SourceFile:
    return lint.SourceFile(
        path=pathlib.Path("/synthetic") / name,
        source=source,
        tree=ast.parse(source, name),
    )


_BOUNDARY_BASE = """
class Contract:
    pass


class BoundaryContract(Contract):
    schema_version: str = SCHEMA_VERSION
"""


def test_no_float_money_fires_on_a_float_amount() -> None:
    planted = _synthetic(
        "proposal.py",
        "class ProposedCommitment(Contract):\n    committed_amount: float\n",
    )
    violations = lint.check_no_float_money([planted])
    assert [v.message for v in violations], "the float-money rule cannot fire"
    assert "annotated with float" in violations[0].message


def test_no_float_money_leaves_non_monetary_floats_alone() -> None:
    """``SourceLocator.bbox`` is four floats and is not money."""
    planted = _synthetic(
        "ingestion.py",
        "class SourceLocator(Contract):\n    bbox: tuple[float, float, float, float] | None\n",
    )
    assert lint.check_no_float_money([planted]) == []


def test_schema_version_present_fires_when_a_value_object_redeclares_it() -> None:
    """Finding pinned by T1.5: the field belongs to BoundaryContract alone."""
    files = [
        _synthetic(
            "base.py",
            _BOUNDARY_BASE + "\n\nclass Money(Contract):\n"
            "    schema_version: str = SCHEMA_VERSION\n",
        ),
        _synthetic("__init__.py", "CONTRACT_REGISTRY = {}\n"),
    ]
    messages = [v.message for v in lint.check_schema_version_present(files)]
    assert any("redeclares schema_version" in m for m in messages), messages


def test_schema_version_present_fires_on_an_unregistered_boundary_contract() -> None:
    files = [
        _synthetic(
            "proof.py", _BOUNDARY_BASE + "\n\nclass StateProof(BoundaryContract):\n    pass\n"
        ),
        _synthetic("__init__.py", "CONTRACT_REGISTRY = {}\n"),
    ]
    messages = [v.message for v in lint.check_schema_version_present(files)]
    assert any("missing from CONTRACT_REGISTRY" in m for m in messages), messages


def test_schema_version_present_fires_when_a_value_object_is_registered() -> None:
    """A registry holding a value object is finding 1 from the T1.6 brief."""
    files = [
        _synthetic("base.py", _BOUNDARY_BASE + "\n\nclass Money(Contract):\n    pass\n"),
        _synthetic("__init__.py", 'CONTRACT_REGISTRY = {"Money": Money}\n'),
    ]
    messages = [v.message for v in lint.check_schema_version_present(files)]
    assert any("is not a BoundaryContract" in m for m in messages), messages


def test_the_shipped_package_passes_all_three_rules() -> None:
    assert lint.run(sorted(lint.RULES)) == []
    assert len(lint.RULES) == 3
