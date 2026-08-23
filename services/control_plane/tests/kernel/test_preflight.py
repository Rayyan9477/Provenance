"""T4.2 - PHASE A, and the proof that no transaction was opened.

`specs/12_KERNEL_ALGORITHMS.md` section 1.2 steps 1-9, and `G4.4`:

    decision.status=REJECTED reason_code=REJECTED_INVALID_PROVENANCE
    kernel_decisions.transaction_opened = false

The database refuses a cross-user evidence reference on its own, through the
composite foreign keys, even when the Kernel is bypassed entirely - so the
guarantee does not rest on this module. What this module adds is that the
refusal is cheap, named, and auditable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from provenance_contracts.base import Money
from provenance_contracts.proposal import (
    MemoryProposal,
    ProposalIdentity,
    ProposedClaim,
    ProposedCommitment,
)
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    ClaimKind,
    CommitmentType,
    KernelDecision,
    KernelReasonCode,
    Modality,
    ModelTier,
    ProposalType,
    RetractionStatus,
    SourceClass,
    SubjectType,
    ValueType,
)
from services.control_plane.app.memory_kernel import preflight as pf

pytestmark = pytest.mark.unit


def _model() -> ModelAttribution:
    return ModelAttribution(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tier=ModelTier.E,
        prompt_version="pv-extract-1.0.0",
        graph_name="ingestion",
        graph_version="1.0.0",
    )


def _proposal(
    hero: Any,
    *,
    user_id: uuid.UUID | None = None,
    evidence_ids: tuple[uuid.UUID, ...] | None = None,
    claim_evidence_id: uuid.UUID | None = None,
    commitments: tuple[ProposedCommitment, ...] = (),
    transition_reason: str | None = None,
) -> MemoryProposal:
    """A minimal, valid `MemoryProposal` shaped like the hero's invoice."""
    evidence_id = hero.ev_one if claim_evidence_id is None else claim_evidence_id
    claim = ProposedClaim(
        local_id="cl_001",
        claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=hero.rel_isp,
        predicate="balance_owed",
        object_type=ValueType.MONEY,
        object_value={"currency": "USD", "amount": "186.0000"},
        actor_type=ActorType.COUNTERPARTY,
        actor_ref="northline-fiber",
        evidence_id=evidence_id,
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
        modality=Modality.ASSERTED_PRESENT,
        extraction_confidence=Decimal("0.9100"),
    )
    return MemoryProposal(
        proposal_id=uuid.UUID(int=0x9001),
        proposal_type=ProposalType.INGESTION_INTERPRETATION,
        trace_id=uuid.UUID(int=0x9002),
        agent_run_id=uuid.UUID(int=0x9003),
        user_id=hero.user if user_id is None else user_id,
        source_artifact_ids=(hero.art_one,),
        evidence_ids=(evidence_id,) if evidence_ids is None else evidence_ids,
        identity=ProposalIdentity(
            relationship_id=hero.rel_isp, case_id=hero.case_isp, confidence=Decimal("0.9700")
        ),
        claims=(claim,),
        commitments=commitments,
        requested_transition_reason_code=transition_reason,
        model=_model(),
        idempotency_key="hero-invoice-0001",
        created_at=datetime(2026, 9, 5, 13, 12, tzinfo=UTC),
    )


def _snapshot(hero: Any, **overrides: Any) -> pf.PreflightSnapshot:
    evidence = {
        hero.ev_one: pf.EvidenceRow(
            evidence_id=hero.ev_one,
            tenant_id=hero.tenant,
            user_id=hero.user,
            artifact_id=hero.art_one,
            created_at=hero.invoice_recorded_at,
        )
    }
    artifacts = {
        hero.art_one: pf.ArtifactRow(
            artifact_id=hero.art_one, tenant_id=hero.tenant, user_id=hero.user
        )
    }
    return pf.PreflightSnapshot(
        evidence=overrides.pop("evidence", evidence),
        artifacts=overrides.pop("artifacts", artifacts),
        **overrides,
    )


def _principal(hero: Any) -> pf.Principal:
    return pf.Principal(tenant_id=hero.tenant, user_id=hero.user)


# --- G4.4 ---------------------------------------------------------------------


def test_cross_user_evidence_reference_is_rejected_before_a_transaction_opens(
    hero: Any,
) -> None:
    """`G4.4` and required test D11. A hero-user proposal citing evidence that
    belongs to another user is `REJECTED_INVALID_PROVENANCE`, and the receipt
    says `transaction_opened = false` - which is the machine-readable statement
    that no aggregate was touched, not merely that none appeared to be."""
    foreign = pf.EvidenceRow(
        evidence_id=hero.ev_foreign,
        tenant_id=hero.tenant,
        user_id=hero.other_user,
        artifact_id=hero.art_one,
    )
    outcome = pf.preflight(
        _proposal(hero, claim_evidence_id=hero.ev_foreign),
        principal=_principal(hero),
        snapshot=_snapshot(hero, evidence={hero.ev_foreign: foreign}),
        preflight_now=hero.tx_now,
    )
    assert outcome.rejected
    assert outcome.decision is KernelDecision.REJECTED_INVALID_PROVENANCE
    assert KernelReasonCode.EVIDENCE_FOREIGN_USER in outcome.reason_codes
    assert outcome.transaction_opened is False


def test_a_preflight_outcome_can_never_claim_a_transaction(hero: Any) -> None:
    """`transaction_opened` is a property, not a parameter. If it were
    settable, `G4.4` could pass vacuously on a Kernel that simply never
    assigned the field."""
    outcome = pf.PreflightOutcome(ok=True)
    assert outcome.transaction_opened is False
    with pytest.raises((AttributeError, TypeError)):
        outcome.transaction_opened = True  # type: ignore[misc]


def test_a_foreign_tenant_is_rejected_as_a_security_event(hero: Any) -> None:
    """Tenancy is derived from the authenticated principal, and evidence that
    disagrees with it is a cross-tenant reach, not a mistake."""
    foreign = pf.EvidenceRow(
        evidence_id=hero.ev_one,
        tenant_id=uuid.UUID(int=0x8009),
        user_id=hero.user,
        artifact_id=hero.art_one,
    )
    outcome = pf.preflight(
        _proposal(hero),
        principal=_principal(hero),
        snapshot=_snapshot(hero, evidence={hero.ev_one: foreign}),
        preflight_now=hero.tx_now,
    )
    assert outcome.decision is KernelDecision.REJECTED_INVALID_PROVENANCE
    assert KernelReasonCode.TENANT_MISMATCH in outcome.reason_codes


def test_a_proposal_asserting_someone_elses_user_id_is_rejected(hero: Any) -> None:
    """Step 3. `user_id` is on the proposal as a cross-check, never as a grant:
    a machine client asserting a user id it was not issued is a security event
    and must be loud rather than absent."""
    outcome = pf.preflight(
        _proposal(hero, user_id=hero.other_user),
        principal=_principal(hero),
        snapshot=_snapshot(hero),
        preflight_now=hero.tx_now,
    )
    assert outcome.decision is KernelDecision.REJECTED_INVALID_PROVENANCE
    assert KernelReasonCode.PRINCIPAL_USER_MISMATCH in outcome.reason_codes


def test_an_evidence_id_the_agent_invented_is_rejected(hero: Any) -> None:
    """Step 4. The commonest hallucination shape, and the cheapest to refuse."""
    outcome = pf.preflight(
        _proposal(hero, claim_evidence_id=uuid.UUID(int=0xDEAD)),
        principal=_principal(hero),
        snapshot=_snapshot(hero),
        preflight_now=hero.tx_now,
    )
    assert outcome.decision is KernelDecision.REJECTED_INVALID_PROVENANCE
    assert KernelReasonCode.EVIDENCE_NOT_FOUND in outcome.reason_codes


def test_evidence_pointing_at_an_uncited_artifact_is_rejected(hero: Any) -> None:
    """Step 5 `EVIDENCE_ARTIFACT_MISMATCH`. Evidence that does not belong to a
    cited artifact is provenance the proposal cannot account for."""
    stray = pf.EvidenceRow(
        evidence_id=hero.ev_one,
        tenant_id=hero.tenant,
        user_id=hero.user,
        artifact_id=hero.art_foreign,
    )
    outcome = pf.preflight(
        _proposal(hero),
        principal=_principal(hero),
        snapshot=_snapshot(hero, evidence={hero.ev_one: stray}),
        preflight_now=hero.tx_now,
    )
    assert outcome.decision is KernelDecision.REJECTED_INVALID_PROVENANCE
    assert KernelReasonCode.EVIDENCE_ARTIFACT_MISMATCH in outcome.reason_codes


@pytest.mark.parametrize(
    "status",
    [RetractionStatus.RETRACTED, RetractionStatus.SUPERSEDED, RetractionStatus.QUARANTINED],
)
def test_retracted_evidence_cannot_ground_a_new_proposal(
    hero: Any, status: RetractionStatus
) -> None:
    """Section 2.8. Retracted and superseded evidence keeps its embedding, so a
    path that ignores `retraction_status` lets corrected evidence resurface and
    re-litigate settled facts - the silent failure `00_PRODUCT.md` R4 names."""
    retracted = pf.EvidenceRow(
        evidence_id=hero.ev_one,
        tenant_id=hero.tenant,
        user_id=hero.user,
        artifact_id=hero.art_one,
        retraction_status=status,
    )
    outcome = pf.preflight(
        _proposal(hero),
        principal=_principal(hero),
        snapshot=_snapshot(hero, evidence={hero.ev_one: retracted}),
        preflight_now=hero.tx_now,
    )
    assert KernelReasonCode.SOURCE_RETRACTED_EXCLUDED in outcome.reason_codes


# --- step 1, schema -----------------------------------------------------------


def test_an_unsupported_schema_version_is_rejected(hero: Any) -> None:
    """Step 1. An agent runtime on a stale contract is redeployed, not
    accommodated by a compatibility branch nobody can test."""
    codes = pf.validate_schema_version("0.9")
    assert KernelReasonCode.SCHEMA_VERSION_UNSUPPORTED in codes


def test_the_shipped_schema_version_passes(hero: Any) -> None:
    """The complement, without which the check could be "always reject"."""
    assert pf.validate_schema_version("1.0") == ()


def test_a_valid_proposal_passes_every_preflight_step(hero: Any) -> None:
    """The happy path, asserted so that none of the refusals above can be
    passing because preflight rejects everything."""
    outcome = pf.preflight(
        _proposal(hero),
        principal=_principal(hero),
        snapshot=_snapshot(hero),
        preflight_now=hero.tx_now,
    )
    assert outcome.ok
    assert outcome.decision is None
    assert outcome.transaction_opened is False


# --- step 6, replay -----------------------------------------------------------


def test_an_already_decided_proposal_is_a_noop_not_a_second_commit(hero: Any) -> None:
    """Rule R6: replay is a lookup. A second submission of the same
    `proposal_id` does not re-execute, and the revision does not move."""
    proposal = _proposal(hero)
    outcome = pf.preflight(
        proposal,
        principal=_principal(hero),
        snapshot=_snapshot(hero, decided_proposal_ids=frozenset({proposal.proposal_id})),
        preflight_now=hero.tx_now,
    )
    assert outcome.decision is KernelDecision.NOOP_DUPLICATE
    assert KernelReasonCode.PROPOSAL_ALREADY_DECIDED in outcome.reason_codes
    assert outcome.transaction_opened is False


# --- the closed reason-code set -----------------------------------------------


def test_an_invented_reason_code_is_rejected_never_passed_through(hero: Any) -> None:
    """T4.2: "An unknown reason code is a rejection, never a pass-through
    string." A stringly-typed message is how a closed set leaks."""
    codes = pf.validate_reason_codes(_proposal(hero, transition_reason="LOOKS_FINE_TO_ME"))
    assert KernelReasonCode.SCHEMA_TYPE_INVALID in codes


def test_the_hero_reopen_reason_code_is_accepted(hero: Any) -> None:
    """`CONTRADICTORY_EVIDENCE` is a `CaseReopenReasonCode`, not a
    `KernelReasonCode`, so a validator that checked only the latter would
    reject the hero's own transition."""
    assert "CONTRADICTORY_EVIDENCE" in pf.KNOWN_REASON_CODES
    assert pf.validate_reason_codes(_proposal(hero, transition_reason=None)) == ()


def test_the_known_reason_codes_are_the_union_of_the_two_closed_enums() -> None:
    """Both enums are closed and neither is a superset of the other."""
    assert "EVIDENCE_FOREIGN_USER" in pf.KNOWN_REASON_CODES
    assert "USER_DISPUTE" in pf.KNOWN_REASON_CODES
    assert "DEFINITELY_NOT_A_REASON_CODE" not in pf.KNOWN_REASON_CODES


# --- currency coherence --------------------------------------------------------


def test_a_currency_mismatch_is_reported_as_a_conflict_not_a_rejection(
    hero: Any,
) -> None:
    """Section 1.2 step 13: `CONFLICT_CURRENCY_MISMATCH` is "a conflict, not a
    rejection". The evidence is still admitted and a person still gets to see
    the disagreement; refusing here would discard the artifact."""
    commitment = ProposedCommitment(
        local_id="cm_001",
        commitment_type=CommitmentType.MONETARY_REFUND,
        description="Refund the June invoice",
        obligor_type=ActorType.COUNTERPARTY,
        beneficiary_type=ActorType.USER,
        committed=Money(currency="EUR", amount=Decimal("186.0000")),
        source_claim_local_id="cl_001",
        confidence=Decimal("0.9000"),
    )
    proposal = _proposal(hero, commitments=(commitment,))
    codes = pf.validate_currency_coherence(
        proposal, _snapshot(hero, commitment_currencies={hero.cm_moving: "USD"})
    )
    outcome = pf.preflight(
        proposal,
        principal=_principal(hero),
        snapshot=_snapshot(hero, commitment_currencies={hero.cm_moving: "USD"}),
        preflight_now=hero.tx_now,
    )
    assert KernelReasonCode.CONFLICT_CURRENCY_MISMATCH in codes
    assert outcome.ok is True


# --- the grounding check and its sabotage hook ---------------------------------


class _Ungrounded:
    """A belief mutation with no support edge and no derivation.

    `MemoryProposal` refuses to construct one, which is correct and which is
    also why the Kernel's own step-16 sweep needs its own assertion point:
    `proposal.ungrounded_mutations()` exists precisely so the rule lives in the
    Kernel's code path and not only in upstream validation.
    """

    def __init__(self, count: int) -> None:
        self._count = count

    def ungrounded_mutations(self) -> tuple[Any, ...]:
        return tuple(object() for _ in range(self._count))


def test_a_grounded_proposal_passes_the_grounding_check() -> None:
    """The happy path returns an empty tuple - "nothing ungrounded"."""
    assert pf.assert_grounded(_Ungrounded(0)) == ()


def test_an_ungrounded_belief_version_is_refused() -> None:
    """THE grounding invariant, and required test 2. A belief is revisable, but
    it is never free-floating: a canonical version carries at least one
    `SUPPORTS` edge or names a registered deterministic derivation."""
    with pytest.raises(pf.UngroundedBeliefError):
        pf.assert_grounded(_Ungrounded(1))


def test_the_grounding_check_is_the_symbol_g4_9_sabotages() -> None:
    """`G4.9` addresses `memory_kernel.preflight.assert_grounded` by that exact
    name. If the module label or the hook list drifts, the sabotage probe
    silently neuters nothing and reports green - which the gate reads as a
    pass."""
    assert pf.SABOTAGE_MODULE == "memory_kernel.preflight"
    assert "assert_grounded" in pf.SABOTAGE_HOOKS


def test_the_sabotage_mechanism_can_actually_neuter_the_check() -> None:
    """Proof that the hook is wired, without mutating the process environment:
    the same `install_sabotage` the module calls at import is called here
    against a throwaway namespace."""
    from provenance_domain import money

    namespace: dict[str, Any] = {"assert_grounded": pf.assert_grounded}
    replaced = money.install_sabotage(
        namespace,
        pf.SABOTAGE_MODULE,
        pf.SABOTAGE_HOOKS,
        "memory_kernel.preflight.assert_grounded",
    )
    assert replaced == ("assert_grounded",)
    assert namespace["assert_grounded"](_Ungrounded(1)) is not None


def test_no_sabotage_is_installed_on_a_normal_run() -> None:
    """A suite that ran permanently sabotaged would report failures nobody
    could explain, and a green sabotage probe would then mean nothing."""
    assert pf.SABOTAGED_SYMBOLS == ()
