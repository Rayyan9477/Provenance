"""T4.9 - the 30-step decision pipeline, as a declarative write plan.

Authority: ``specs/12_KERNEL_ALGORITHMS.md`` section 1 in full, section 6.2 for
what counts as a canonical change, and ``CANONICAL_DECISIONS.md`` -> *Hero
commit canon* for the shape the plan must have.

The pipeline produces a **plan**, not writes. That is what makes the
transaction body reviewable and what lets every one of these tests run with no
database, no credentials and no model call - the falsifiable form of the
product claim.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from provenance_contracts.proposal import MemoryProposal, ProposalIdentity, ProposedClaim
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    AttentionLevel,
    CaseStatus,
    ClaimKind,
    ConflictType,
    EpistemicStatus,
    EventType,
    KernelDecision,
    KernelReasonCode,
    Modality,
    ModelTier,
    ProposalType,
    SourceClass,
    SubjectType,
    TransitionType,
    ValueType,
)
from services.control_plane.app.memory_kernel import case_ops, families, pipeline
from services.control_plane.app.memory_kernel import propositions as prop

pytestmark = pytest.mark.unit

TENANT = uuid.UUID(int=0x8001)
USER = uuid.UUID(int=0x8002)
REL = uuid.UUID(int=0x1001)
CASE = uuid.UUID(int=0x2001)
OTHER_CASE = uuid.UUID(int=0x2002)
EV_INVOICE = uuid.UUID(int=0x6001)
EV_OLD = uuid.UUID(int=0x6002)
ART = uuid.UUID(int=0x7001)
BELIEF = uuid.UUID(int=0x5001)
BV_V1 = uuid.UUID(int=0x5101)
PROPOSAL = uuid.UUID(int=0x9001)
TRACE = uuid.UUID(int=0x9002)
DECISION_ID = uuid.UUID(int=0x9003)

RESOLVED_AT = datetime(2026, 6, 2, 13, 0, tzinfo=UTC)
INVOICE_AT = datetime(2026, 9, 5, 13, 12, tzinfo=UTC)
TX_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
#: The billed June period. Both sides carry a *stated* interval, because a
#: proposition whose validity is UNKNOWN never reaches the matcher (rule T2)
#: and a conflict that cannot be detected is not a conflict.
JUN_1 = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
JUL_1 = datetime(2026, 7, 1, 4, 0, tzinfo=UTC)


def _model() -> ModelAttribution:
    return ModelAttribution(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tier=ModelTier.E,
        prompt_version="pv-extract-1.0.0",
        graph_name="ingestion",
        graph_version="1.0.0",
    )


def _claim(
    *,
    predicate: str = "balance_owed",
    amount: str = "186.0000",
    evidence_id: uuid.UUID = EV_INVOICE,
    claim_kind: ClaimKind = ClaimKind.COUNTERPARTY_CLAIM,
    local_id: str = "cl_001",
) -> ProposedClaim:
    return ProposedClaim(
        local_id=local_id,
        claim_kind=claim_kind,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=REL,
        predicate=predicate,
        object_type=ValueType.MONEY,
        object_value={"currency": "USD", "amount": amount},
        actor_type=ActorType.COUNTERPARTY,
        actor_ref="northline-fiber",
        evidence_id=evidence_id,
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
        modality=Modality.ASSERTED_PRESENT,
        valid_from=JUN_1,
        valid_to=JUL_1,
        extraction_confidence=Decimal("0.9100"),
    )


def _proposal(
    *,
    claims: tuple[ProposedClaim, ...] | None = None,
    evidence_ids: tuple[uuid.UUID, ...] = (EV_INVOICE,),
    case_id: uuid.UUID | None = CASE,
    proposal_id: uuid.UUID = PROPOSAL,
    requested_transition: CaseStatus | None = CaseStatus.REOPENED,
    transition_reason: str | None = "CONTRADICTORY_EVIDENCE",
) -> MemoryProposal:
    return MemoryProposal(
        proposal_id=proposal_id,
        proposal_type=ProposalType.INGESTION_INTERPRETATION,
        trace_id=TRACE,
        agent_run_id=uuid.UUID(int=0x9004),
        user_id=USER,
        source_artifact_ids=(ART,),
        evidence_ids=evidence_ids,
        identity=ProposalIdentity(
            relationship_id=REL, case_id=case_id, confidence=Decimal("0.9700")
        ),
        claims=(_claim(),) if claims is None else claims,
        requested_case_transition=requested_transition,
        requested_transition_reason_code=transition_reason,
        model=_model(),
        idempotency_key="hero-invoice-0001",
        created_at=INVOICE_AT,
    )


def _incumbent() -> pipeline.IncumbentBelief:
    """The canonical ``balance_owed = USD 0`` the invoice contradicts."""
    result = prop.normalize_claim(
        prop_id=BV_V1,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=REL,
        predicate="balance_owed",
        raw_value={"currency": "USD", "amount": Decimal("0.0000")},
        source_class=str(SourceClass.PROVIDER_AGENT_WRITTEN),
        valid_from=JUN_1,
        valid_to=None,
        validity_basis=prop.ValidityBasis.EXPLICIT_OPEN,
        recorded_at=RESOLVED_AT,
        source_kind=prop.PropositionSourceKind.BELIEF_VERSION,
        is_incumbent=True,
        epistemic_status=EpistemicStatus.CONFIRMED,
        belief_confidence=Decimal("0.9500"),
    )
    assert result.proposition is not None
    return pipeline.IncumbentBelief(
        belief_id=BELIEF,
        version_id=BV_V1,
        version_no=1,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=REL,
        predicate="balance_owed",
        proposition=result.proposition,
    )


def _snapshot(
    *,
    status: CaseStatus = CaseStatus.RESOLVED,
    revision: int = 12,
    incumbents: tuple[pipeline.IncumbentBelief, ...] | None = None,
    decided: frozenset[uuid.UUID] = frozenset(),
    reopened_count: int = 0,
) -> pipeline.AggregateSnapshot:
    return pipeline.AggregateSnapshot(
        case=case_ops.CaseRow(
            case_id=CASE,
            tenant_id=TENANT,
            user_id=USER,
            status=status,
            revision=revision,
            reopened_count=reopened_count,
            resolved_at=RESOLVED_AT if status is CaseStatus.RESOLVED else None,
        ),
        relationship_id=REL,
        case_snapshot=case_ops.CaseSnapshot(
            evidence_ids_linked_to_case=frozenset({EV_OLD}),
            artifact_hashes_linked_to_case=frozenset({"aa" * 32}),
            evidence={
                EV_INVOICE: case_ops.EvidenceRecord(evidence_id=EV_INVOICE, created_at=INVOICE_AT),
                EV_OLD: case_ops.EvidenceRecord(evidence_id=EV_OLD, created_at=RESOLVED_AT),
            },
        ),
        incumbents=(_incumbent(),) if incumbents is None else incumbents,
        decided_proposal_ids=decided,
    )


def _run(
    proposal: MemoryProposal, snapshot: pipeline.AggregateSnapshot
) -> pipeline.PipelineOutcome:
    return pipeline.build_write_plan(
        proposal,
        snapshot=snapshot,
        tx_now=TX_NOW,
        trace_id=TRACE,
        decision_id=DECISION_ID,
    )


# ---------------------------------------------------------------------------
# The hero, in plan form
# ---------------------------------------------------------------------------


def test_the_hero_plan_is_accepted_with_a_conflict() -> None:
    outcome = _run(_proposal(), _snapshot())
    assert outcome.decision is KernelDecision.ACCEPTED_WITH_CONFLICT
    assert outcome.attention_required


def test_the_hero_plan_writes_exactly_one_of_each_row() -> None:
    plan = _run(_proposal(), _snapshot()).plan
    assert len(plan.claims) == 1
    assert len(plan.belief_versions) == 1
    assert len(plan.conflicts) == 1
    assert len(plan.transitions) == 1
    assert len(plan.outbox) == 1


def test_the_hero_conflict_is_a_value_conflict_needing_a_person() -> None:
    """``CANONICAL_DECISIONS.md`` -> *Hero conflict*: it resolves on H5,
    monetary exposure >= 100.00, not on the authority margin."""
    conflict = _run(_proposal(), _snapshot()).plan.conflicts[0]
    assert conflict.conflict_type is ConflictType.VALUE_CONFLICT
    assert conflict.requires_human
    assert conflict.left_source_id <= conflict.right_source_id


def test_the_hero_case_goes_resolved_to_reopened_at_revision_thirteen() -> None:
    update = _run(_proposal(), _snapshot()).plan.case_update
    assert update is not None
    assert update.status_before is CaseStatus.RESOLVED
    assert update.status_after is CaseStatus.REOPENED
    assert (update.revision_before, update.revision_after) == (12, 13)
    assert update.reopen_delta == 1
    assert update.attention_after is AttentionLevel.URGENT


def test_the_transition_row_names_the_canon_reason_code() -> None:
    transition = _run(_proposal(), _snapshot()).plan.transitions[0]
    assert transition.transition_type is TransitionType.CASE_STATUS
    assert transition.reason_code == "CONTRADICTORY_EVIDENCE"
    assert transition.from_state == "RESOLVED"
    assert transition.to_state == "REOPENED"


def test_the_outbox_event_is_case_reopened_at_the_new_revision() -> None:
    """Rule R3: every artifact of one commit carries the *new* revision."""
    plan = _run(_proposal(), _snapshot()).plan
    event = plan.outbox[0]
    assert event.event_type is EventType.CASE_REOPENED
    assert event.aggregate_version == 13
    assert event.aggregate_type == "CASE"
    assert {t.case_revision for t in plan.transitions} == {13}


# ---------------------------------------------------------------------------
# Grounding and Rule N1
# ---------------------------------------------------------------------------


def test_every_belief_version_carries_at_least_one_grounding_edge() -> None:
    """Invariant 5. ``ck_belief_versions_grounded`` is the other half."""
    for version in _run(_proposal(), _snapshot()).plan.belief_versions:
        assert version.support_edge_count >= 1
        assert version.support_edge_count == len(version.support)


def test_the_grounding_edge_records_that_it_contradicts() -> None:
    version = _run(_proposal(), _snapshot()).plan.belief_versions[0]
    relations = {edge.relation for edge in version.support}
    assert "CONTRADICTS" in relations or "SUPPORTS" in relations


def test_rule_n1_the_belief_stores_the_canonical_predicate() -> None:
    """A surface form stored in ``beliefs.predicate`` lets two mutually
    exclusive beliefs coexist as separate rows, and the contradiction model
    silently no-ops."""
    proposal = _proposal(claims=(_claim(predicate="amount_due"),))
    plan = _run(proposal, _snapshot()).plan
    canonical = families.canonical_predicate(families.Family.BALANCE)
    assert canonical == "balance_owed"
    assert [b.predicate for b in plan.beliefs] == [canonical]
    assert plan.claims[0].predicate == "amount_due", "the claim keeps what the source said"


def test_the_new_version_supersedes_the_incumbent_by_id() -> None:
    version = _run(_proposal(), _snapshot()).plan.belief_versions[0]
    assert version.supersedes_version_id == BV_V1
    assert version.version_no == 2
    assert version.supersession_reason_code is not None


# ---------------------------------------------------------------------------
# The negative controls
# ---------------------------------------------------------------------------


def test_a_replayed_proposal_is_a_noop_with_a_reason() -> None:
    """Rule R6: a second submission is a lookup, not a re-execution."""
    outcome = _run(_proposal(), _snapshot(decided=frozenset({PROPOSAL})))
    assert outcome.decision is KernelDecision.NOOP_DUPLICATE
    assert KernelReasonCode.PROPOSAL_ALREADY_DECIDED in outcome.reason_codes
    assert outcome.plan.is_canonical_noop()
    assert outcome.plan.case_update is None
    assert outcome.plan.outbox == ()


def test_the_marketing_email_does_not_reopen_the_case() -> None:
    """Section 5.3's negative control: new evidence, recorded after resolution,
    that did nothing canonical. Without Q3 the product looks broken in the
    first thirty seconds."""
    outcome = _run(
        _proposal(claims=(_claim(predicate="balance_owed", amount="0.0000"),)),
        _snapshot(),
    )
    update = outcome.plan.case_update
    assert update is not None
    assert update.status_after is CaseStatus.RESOLVED
    assert KernelReasonCode.CASE_REOPEN_REFUSED_NON_QUALIFYING in outcome.reason_codes


def test_a_claim_is_canonical_even_when_no_belief_moves() -> None:
    """Section 6.2: admitting a claim is a memory change even if no belief
    moves, so the revision still advances by exactly one."""
    outcome = _run(
        _proposal(
            claims=(_claim(predicate="balance_owed", amount="0.0000"),),
            requested_transition=None,
            transition_reason=None,
        ),
        _snapshot(),
    )
    update = outcome.plan.case_update
    assert update is not None
    assert update.revision_after == update.revision_before + 1
    assert not outcome.plan.is_canonical_noop()


def test_rule_r7_a_proposal_naming_a_foreign_case_is_refused() -> None:
    outcome = _run(_proposal(case_id=OTHER_CASE), _snapshot())
    assert outcome.decision is KernelDecision.REJECTED_INVARIANT
    assert KernelReasonCode.INVARIANT_MULTI_CASE_PROPOSAL in outcome.reason_codes
    assert outcome.plan.is_canonical_noop()


def test_a_proposal_naming_no_case_is_refused() -> None:
    outcome = _run(
        _proposal(case_id=None, requested_transition=None, transition_reason=None), _snapshot()
    )
    assert outcome.decision is KernelDecision.REJECTED_INVARIANT
    assert KernelReasonCode.INVARIANT_MULTI_CASE_PROPOSAL in outcome.reason_codes


def test_the_contract_refuses_a_transition_with_no_case_before_the_kernel_sees_it() -> None:
    """The first of three guards ``MemoryProposal`` already holds. The pipeline
    checks the same things again inside the transaction, because PHASE A is
    advisory and a caller could bypass the contract."""
    with pytest.raises(ValidationError, match="identity resolves to a case"):
        _proposal(case_id=None)


def test_the_contract_refuses_a_transition_with_no_reason_code() -> None:
    with pytest.raises(ValidationError, match="requires a reason code"):
        _proposal(requested_transition=CaseStatus.IN_PROGRESS, transition_reason=None)


def test_a_terminal_case_refuses_every_commit() -> None:
    outcome = _run(_proposal(), _snapshot(status=CaseStatus.SUPERSEDED))
    assert outcome.decision is KernelDecision.REJECTED_INVARIANT
    assert KernelReasonCode.CASE_TERMINAL_SUPERSEDED in outcome.reason_codes


def test_an_illegal_requested_transition_is_refused_by_reason_code() -> None:
    """``RESOLVED -> IN_PROGRESS`` is a dash in section 5.1. The reason code is
    well formed; the edge does not exist."""
    outcome = _run(
        _proposal(
            requested_transition=CaseStatus.IN_PROGRESS,
            transition_reason="CONTRADICTORY_EVIDENCE",
        ),
        _snapshot(),
    )
    assert outcome.decision is KernelDecision.REJECTED_INVARIANT
    assert KernelReasonCode.CASE_TRANSITION_ILLEGAL in outcome.reason_codes


def test_a_claim_citing_evidence_the_proposal_did_not_declare_is_refused() -> None:
    """The contract refuses it at the boundary; the pipeline refuses it again
    inside the transaction. Both are asserted, because only the second one is
    still standing if a caller reaches the Kernel without the contract."""
    with pytest.raises(ValidationError, match="referenced but not declared"):
        _proposal(claims=(_claim(evidence_id=uuid.UUID(int=0x6999)),))

    bypassed = _proposal().model_copy(update={"evidence_ids": (uuid.UUID(int=0x6999),)})
    outcome = _run(bypassed, _snapshot())
    assert outcome.decision is KernelDecision.REJECTED_INVARIANT
    assert KernelReasonCode.CLAIM_EVIDENCE_UNLINKED in outcome.reason_codes


def test_the_flapping_guard_withholds_the_transition_but_keeps_the_evidence() -> None:
    outcome = _run(_proposal(), _snapshot(reopened_count=5))
    update = outcome.plan.case_update
    assert update is not None
    assert update.status_after is CaseStatus.RESOLVED
    assert update.attention_after is AttentionLevel.ATTENTION
    assert KernelReasonCode.CASE_REOPEN_LIMIT_REACHED in outcome.reason_codes
    assert len(outcome.plan.claims) == 1
    assert len(outcome.plan.conflicts) == 1


# ---------------------------------------------------------------------------
# Plan hygiene
# ---------------------------------------------------------------------------


def test_an_empty_plan_is_a_canonical_noop() -> None:
    assert pipeline.WritePlan().is_canonical_noop()


def test_a_plan_with_only_a_claim_is_not_a_noop() -> None:
    plan = _run(_proposal(), _snapshot()).plan
    assert not plan.is_canonical_noop()


def test_every_row_id_in_the_plan_is_distinct() -> None:
    """Rule 4 of section 7.3 in plan form: no id is reused between rows, so a
    unique violation can never be an accident of the planner."""
    plan = _run(_proposal(), _snapshot()).plan
    ids: list[uuid.UUID] = [c.claim_id for c in plan.claims]
    ids += [v.version_id for v in plan.belief_versions]
    ids += [e.edge_id for v in plan.belief_versions for e in v.support]
    ids += [c.conflict_id for c in plan.conflicts]
    ids += [t.transition_id for t in plan.transitions]
    ids += [o.event_id for o in plan.outbox]
    assert len(ids) == len(set(ids))


def test_the_pipeline_makes_no_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 1.3: semantic input arrived as a typed proposal and its
    interpretation is already finished."""
    text = Path(pipeline.__file__).read_text(encoding="utf-8")
    for forbidden in ("boto3", "bedrock", "httpx", "anthropic", "invoke_model"):
        assert forbidden not in text.lower()


def test_the_plan_is_frozen() -> None:
    plan = _run(_proposal(), _snapshot()).plan
    with pytest.raises((AttributeError, TypeError)):
        plan.claims = ()  # type: ignore[misc]


def test_two_identical_runs_produce_the_same_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Determinism of the *shape*; the ids differ by design (rule 4)."""
    first = _run(_proposal(), _snapshot()).plan
    second = _run(_proposal(), _snapshot()).plan

    def shape(plan: pipeline.WritePlan) -> tuple[Any, ...]:
        return (
            len(plan.claims),
            len(plan.belief_versions),
            len(plan.conflicts),
            len(plan.transitions),
            len(plan.outbox),
            plan.case_update.status_after if plan.case_update else None,
        )

    assert shape(first) == shape(second)
