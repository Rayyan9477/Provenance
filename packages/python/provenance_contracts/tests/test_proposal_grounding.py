"""The grounding invariant, and "one proposal is one case".

Written before ``provenance_contracts/proposal.py`` exists (T1.6).

Authority
---------
- ``specs/11_CONTRACTS.md`` section 12 (``proposal.py``) and section 20.4,
  which prints the first seven tests below.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, first sub-task: "One proposal is one
  case -- a multi-case artifact becomes several single-case proposals sharing
  artifact and evidence references, and the type must make a cross-case
  proposal unconstructable."
- ``EXECUTION/70_TASK_PLAN.md`` section 2.3, the three-term vocabulary:
  **grounding** is the ``SUPPORTS`` / ``CONTRADICTS`` / ``QUALIFIES`` edge set,
  never the version chain.

Why the "one case" rule is tested structurally
----------------------------------------------
A validator that rejects a second case id can only fire if a second case id is
*representable*. :func:`test_one_proposal_is_one_case` therefore walks the
whole ``MemoryProposal`` model graph and asserts that exactly one field named
``case_id`` exists anywhere in it, on ``ProposalIdentity``. Combined with
``extra="forbid"`` on every contract, a cross-case proposal has no field to
put the second case in, which is what "unconstructable" means. The Kernel
keeps ``KernelReasonCode.INVARIANT_MULTI_CASE_PROPOSAL`` for the shape that
arrives as two ids the type could not see -- belt and braces, not a substitute.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from provenance_contracts.base import utc_now
from provenance_contracts.proposal import (
    ConflictHint,
    DeterministicDerivation,
    MemoryProposal,
    ProposalIdentity,
    ProposedBeliefMutation,
    ProposedClaim,
    ProposedCommitment,
    ProposedSupportEdge,
)
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    BeliefMutationKind,
    CaseStatus,
    ClaimKind,
    CommitmentType,
    ConflictSeverity,
    ConflictType,
    EpistemicStatus,
    Modality,
    ModelTier,
    ProposalType,
    SourceClass,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    ValueType,
)

# The hero scenario's ids, allocated once so the fixtures below cross-reference.
RELATIONSHIP_ID = uuid.uuid4()
CASE_ID = uuid.uuid4()
ARTIFACT_ID = uuid.uuid4()
BILLING_EVIDENCE_ID = uuid.uuid4()
INVOICE_EVIDENCE_ID = uuid.uuid4()
SERVICE_TERMINATED_V1_ID = uuid.uuid4()

TIER_E = ModelAttribution(
    model_id="us.anthropic.claude-haiku-4-5",
    tier=ModelTier.E,
    prompt_version="interpreter-1.3",
    graph_name="ingestion_graph",
    graph_version="1.2.0",
    extraction_schema_version="1.0",
)


def _base(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "local_id": "bm_1",
        "mutation_kind": BeliefMutationKind.CREATE,
        "subject_type": SubjectType.RELATIONSHIP,
        "subject_id": uuid.uuid4(),
        "predicate": "service_terminated",
        "value_type": ValueType.BOOLEAN,
        "value_json": True,
        "epistemic_status": EpistemicStatus.CONFIRMED,
        "belief_confidence": Decimal("0.95"),
    }
    payload.update(overrides)
    return payload


def _billing_claim(**overrides: Any) -> ProposedClaim:
    payload: dict[str, Any] = {
        "local_id": "cl_billing",
        "claim_kind": ClaimKind.COUNTERPARTY_CLAIM,
        "subject_type": SubjectType.RELATIONSHIP,
        "subject_id": RELATIONSHIP_ID,
        "predicate": "billing_period_covered",
        "object_type": ValueType.INTERVAL,
        "object_value": {"from": "2026-06-01T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        "actor_type": ActorType.COUNTERPARTY,
        "actor_ref": "billing@example-isp.test",
        "evidence_id": BILLING_EVIDENCE_ID,
        "source_class": SourceClass.PROVIDER_SYSTEM_NOTICE,
        "modality": Modality.ASSERTED_PRESENT,
        "valid_from": datetime(2026, 6, 1, tzinfo=UTC),
        "valid_to": datetime(2026, 7, 1, tzinfo=UTC),
        "extraction_confidence": Decimal("0.97"),
    }
    payload.update(overrides)
    return ProposedClaim(**payload)


def _amount_claim() -> ProposedClaim:
    return ProposedClaim(
        local_id="cl_amount",
        claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=RELATIONSHIP_ID,
        predicate="amount_outstanding",
        object_type=ValueType.MONEY,
        object_value={"amount": "186.00", "currency": "USD"},
        actor_type=ActorType.COUNTERPARTY,
        actor_ref="billing@example-isp.test",
        evidence_id=INVOICE_EVIDENCE_ID,
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
        modality=Modality.ASSERTED_PRESENT,
        extraction_confidence=Decimal("0.99"),
    )


def _service_conflict_hint() -> ConflictHint:
    return ConflictHint(
        local_id="cf_service",
        conflict_type=ConflictType.VALUE_CONFLICT,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=RELATIONSHIP_ID,
        predicate="service_terminated",
        left_source_kind=SupportSourceKind.BELIEF_VERSION,
        left_source_id=SERVICE_TERMINATED_V1_ID,
        right_source_kind=SupportSourceKind.CLAIM,
        right_source_local_id="cl_billing",
        severity=ConflictSeverity.HIGH,
        requires_human_hint=False,
        rationale=(
            "Canonical belief holds service terminated 31 May 2026, grounded in the "
            "15 May cancellation confirmation. The invoice asserts service was "
            "delivered 1-30 June. The intervals overlap and the values are mutually "
            "exclusive."
        ),
        confidence=Decimal("0.94"),
    )


def _proposal(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "proposal_id": uuid.uuid4(),
        "proposal_type": ProposalType.INGESTION_INTERPRETATION,
        "trace_id": uuid.uuid4(),
        "agent_run_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "source_artifact_ids": (ARTIFACT_ID,),
        "evidence_ids": (BILLING_EVIDENCE_ID, INVOICE_EVIDENCE_ID),
        "identity": ProposalIdentity(
            relationship_id=RELATIONSHIP_ID,
            case_id=CASE_ID,
            confidence=Decimal("0.96"),
            resolved_by="DETERMINISTIC",
        ),
        "claims": (_billing_claim(), _amount_claim()),
        "conflict_hints": (_service_conflict_hint(),),
        "requested_case_transition": CaseStatus.REOPENED,
        "requested_transition_reason_code": "COUNTERPARTY_CLAIM_AFTER_CLOSE",
        "model": TIER_E,
        "idempotency_key": f"proposal:{ARTIFACT_ID}:interpreter-1.3",
        "created_at": utc_now(),
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# specs/11_CONTRACTS.md section 20.4 — the grounding invariant
# ---------------------------------------------------------------------------


def test_belief_without_grounding_or_derivation_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ProposedBeliefMutation(**_base())
    assert "UNGROUNDED" in str(excinfo.value)


def test_contradicting_edge_alone_does_not_ground_a_belief() -> None:
    """A belief supported only by something that contradicts it is not grounded.

    This is the subtle version of the bug and the one most likely to slip
    through review.
    """
    with pytest.raises(ValidationError) as excinfo:
        ProposedBeliefMutation(
            **_base(
                grounding=(
                    ProposedSupportEdge(
                        source_kind=SupportSourceKind.EVIDENCE,
                        source_id=uuid.uuid4(),
                        relation=SupportRelation.CONTRADICTS,
                    ),
                )
            )
        )
    assert "UNGROUNDED" in str(excinfo.value)


def test_one_supports_edge_is_enough() -> None:
    mutation = ProposedBeliefMutation(
        **_base(
            grounding=(
                ProposedSupportEdge(
                    source_kind=SupportSourceKind.EVIDENCE,
                    source_id=uuid.uuid4(),
                    relation=SupportRelation.SUPPORTS,
                    weight=Decimal("0.9"),
                ),
            )
        )
    )
    assert len(mutation.grounding) == 1


def test_registered_derivation_is_the_only_ungrounded_path() -> None:
    mutation = ProposedBeliefMutation(
        **_base(
            derivation=DeterministicDerivation(
                name="outstanding_from_committed_minus_fulfilled",
                function_version="1.0.0",
                input_refs=(uuid.uuid4(),),
            )
        )
    )
    assert mutation.grounding == ()


def test_unregistered_derivation_cannot_bypass_grounding() -> None:
    with pytest.raises(ValidationError) as excinfo:
        DeterministicDerivation(
            name="trust_me_bro",
            function_version="1.0.0",
            input_refs=(uuid.uuid4(),),
        )
    assert "not a registered" in str(excinfo.value)


def test_revision_must_name_the_version_it_replaces() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ProposedBeliefMutation(
            **_base(
                local_id="bm_2",
                mutation_kind=BeliefMutationKind.REVISE,
                belief_id=uuid.uuid4(),
                subject_id=None,
                subject_type=None,
                predicate=None,
                grounding=(
                    ProposedSupportEdge(
                        source_kind=SupportSourceKind.EVIDENCE,
                        source_id=uuid.uuid4(),
                        relation=SupportRelation.SUPPORTS,
                    ),
                ),
            )
        )
    assert "must name the version it replaces" in str(excinfo.value)


def test_retraction_is_exempt_but_must_be_explained() -> None:
    retraction = ProposedBeliefMutation(
        local_id="bm_3",
        mutation_kind=BeliefMutationKind.RETRACT,
        belief_id=uuid.uuid4(),
        epistemic_status=EpistemicStatus.RETRACTED,
        belief_confidence=Decimal("1.0"),
        reason_code="USER_CORRECTED_RECORD",
    )
    assert retraction.grounding == ()

    with pytest.raises(ValidationError) as excinfo:
        ProposedBeliefMutation(
            local_id="bm_4",
            mutation_kind=BeliefMutationKind.RETRACT,
            belief_id=uuid.uuid4(),
            epistemic_status=EpistemicStatus.RETRACTED,
            belief_confidence=Decimal("1.0"),
        )
    assert "unauditable" in str(excinfo.value)


# ---------------------------------------------------------------------------
# T1.6 sub-task 1 — one proposal is one case
# ---------------------------------------------------------------------------


def _case_id_fields(model: type[BaseModel], seen: set[type[BaseModel]]) -> list[str]:
    """Every ``case_id`` field reachable from *model*, as ``Owner.field``."""
    if model in seen:
        return []
    seen.add(model)
    found = [f"{model.__name__}.{name}" for name in model.model_fields if name == "case_id"]
    for field in model.model_fields.values():
        annotation = field.annotation
        candidates = (annotation, *getattr(annotation, "__args__", ()))
        for candidate in candidates:
            for item in (candidate, *getattr(candidate, "__args__", ())):
                if isinstance(item, type) and issubclass(item, BaseModel):
                    found += _case_id_fields(item, seen)
    return found


def test_one_proposal_is_one_case() -> None:
    """A cross-case proposal is unconstructable: there is nowhere to put it.

    Two halves of one rule. The census proves a second case id has no field to
    live in; ``extra="forbid"`` proves one cannot be added at call time. Either
    alone would be a hole.
    """
    owners = sorted(set(_case_id_fields(MemoryProposal, set())))
    assert owners == ["ProposalIdentity.case_id"], owners

    proposal = MemoryProposal(**_proposal())
    assert proposal.identity.case_id == CASE_ID

    with pytest.raises(ValidationError) as excinfo:
        MemoryProposal(**_proposal(), case_ids=[str(uuid.uuid4()), str(uuid.uuid4())])
    assert "case_ids" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        ProposalIdentity(
            relationship_id=RELATIONSHIP_ID,
            case_id=CASE_ID,
            confidence=Decimal("0.96"),
            also_case_id=str(uuid.uuid4()),
        )
    assert "also_case_id" in str(excinfo.value)


def test_two_single_case_proposals_may_share_artifact_and_evidence() -> None:
    """The prescribed shape for a multi-case artifact."""
    first = MemoryProposal(**_proposal())
    second = MemoryProposal(
        **_proposal(
            proposal_id=uuid.uuid4(),
            identity=ProposalIdentity(
                relationship_id=RELATIONSHIP_ID,
                case_id=uuid.uuid4(),
                confidence=Decimal("0.91"),
            ),
            requested_case_transition=None,
            requested_transition_reason_code=None,
            conflict_hints=(),
            idempotency_key=f"proposal:{ARTIFACT_ID}:interpreter-1.3:2",
        )
    )
    assert first.source_artifact_ids == second.source_artifact_ids
    assert first.evidence_ids == second.evidence_ids
    assert first.identity.case_id != second.identity.case_id


# ---------------------------------------------------------------------------
# MemoryProposal structural validation
# ---------------------------------------------------------------------------


def test_an_empty_proposal_is_not_a_proposal() -> None:
    with pytest.raises(ValidationError) as excinfo:
        MemoryProposal(
            **_proposal(
                claims=(),
                conflict_hints=(),
                requested_case_transition=None,
                requested_transition_reason_code=None,
            )
        )
    assert "an empty proposal is not a proposal" in str(excinfo.value)


def test_local_ids_must_be_unique_and_must_resolve() -> None:
    """A within-proposal reference is only useful if it points at something."""
    with pytest.raises(ValidationError) as excinfo:
        MemoryProposal(**_proposal(claims=(_billing_claim(), _billing_claim())))
    assert "duplicate local_id" in str(excinfo.value)

    orphan = ProposedCommitment(
        local_id="cm_1",
        # `CommitmentType.REFUND` is not a member of the closed vocabulary;
        # the catalogue spells a refund `MONETARY_REFUND` (T1.1 enums, and
        # specs/11_CONTRACTS.md section 3). Corrected in T1.6.
        commitment_type=CommitmentType.MONETARY_REFUND,
        description="Refund the June charge.",
        obligor_type=ActorType.COUNTERPARTY,
        beneficiary_type=ActorType.USER,
        source_claim_local_id="cl_nonexistent",
        confidence=Decimal("0.8"),
    )
    with pytest.raises(ValidationError) as excinfo:
        MemoryProposal(**_proposal(commitments=(orphan,)))
    assert "cites unknown claim" in str(excinfo.value)


def test_cited_evidence_must_be_declared_up_front() -> None:
    """Pipeline step 4 loads and ownership-checks the declared set in one read.

    Undeclared evidence is how foreign provenance would reach step 5 unchecked,
    so the declaration is a validation error rather than a convention.
    """
    with pytest.raises(ValidationError) as excinfo:
        MemoryProposal(**_proposal(evidence_ids=(INVOICE_EVIDENCE_ID,)))
    assert "referenced but not declared" in str(excinfo.value)


def test_a_state_blocked_proposal_may_record_claims_but_not_mutate() -> None:
    blocked = MemoryProposal(
        **_proposal(
            blocks_state_change=True,
            requested_case_transition=None,
            requested_transition_reason_code=None,
        )
    )
    assert blocked.claims
    assert not blocked.is_state_changing()

    with pytest.raises(ValidationError) as excinfo:
        MemoryProposal(**_proposal(blocks_state_change=True))
    assert "blocks_state_change" in str(excinfo.value)


def test_the_hero_invoice_proposal_records_a_claim_and_decides_nothing() -> None:
    """Section 12.3. The agent admits what was said; the Kernel decides.

    The absence of ``belief_mutations`` is the assertion: an LLM never decides
    that service was or was not terminated. The negative branch is the same
    rule seen from the other side -- the reopen it *does* request is
    unconstructable without a reason code the Kernel can check against the
    frozen guard table.
    """
    proposal = MemoryProposal(**_proposal())
    assert proposal.belief_mutations == ()
    assert proposal.ungrounded_mutations() == ()
    assert proposal.is_state_changing()
    assert proposal.requested_transition_reason_code == "COUNTERPARTY_CLAIM_AFTER_CLOSE"
    assert {claim.predicate for claim in proposal.claims} == {
        "billing_period_covered",
        "amount_outstanding",
    }

    with pytest.raises(ValidationError) as excinfo:
        MemoryProposal(**_proposal(requested_transition_reason_code=None))
    assert "requires a reason code" in str(excinfo.value)
