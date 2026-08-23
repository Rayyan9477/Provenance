"""``T9.1`` / ``G9.3`` -- an ungrounded claim in a draft cannot reach an intent.

The assertion, quoted from ``ops/gates/PHASE_09.md``:

    an ungrounded claim in a draft cannot ship: ``DRAFT_CLAIM_UNSUPPORTED``,
    no ``ActionIntent`` created.

Two ways a draft can be ungrounded, and both are checked here because only one
of them is obvious:

1. It cites a support id the State Proof does not carry. Caught by set
   membership, never by string similarity.
2. It asserts something factual and cites **nothing at all**. This is the one a
   naive implementation misses: a sentence with no ``DraftClaim`` entry passes
   every membership test vacuously, because there is no id to test. ``T9.1``'s
   third sub-task names it -- "a draft sentence that paraphrases a real evidence
   item and cites nothing is unsupported".

The snapshot, not a live re-query
---------------------------------
``T9.1``: "Load the State Proof by case and revision, and validate against
*that* snapshot rather than against a live re-query, so validation and approval
agree about the world." ``GroundingSnapshot`` is that snapshot, and it carries
the revision it was taken at, so a draft built against a different one is
refused rather than silently validated against newer facts.
"""

from __future__ import annotations

import uuid

import pytest

from provenance_domain.enums import (
    EpistemicStatus,
    RetractionStatus,
    SupportRelation,
    SupportSourceKind,
)
from services.control_plane.app.actions import support_validation as sv
from services.control_plane.app.state_proof import builder

pytestmark = pytest.mark.unit


# ==========================================================================
# The accepted draft
# ==========================================================================


def test_a_draft_citing_only_ids_the_proof_carries_is_grounded(make_draft, snapshot) -> None:
    """Every factual assertion cites an id present in the State Proof."""
    verdict = sv.validate_draft_claims(make_draft(), snapshot)

    assert verdict.grounded is True
    assert verdict.reason_code is None
    assert verdict.unsupported == ()
    assert verdict.validated_claim_ids == ("dc_1",)


def test_membership_is_by_id_and_not_by_string_similarity(make_draft, snapshot, hero) -> None:
    """The sentence is word-for-word real; the id it cites is not in the proof.

    This is the whole difference between "the model said something plausible"
    and "the record supports it". A validator matching on prose would pass this
    draft, because the prose is the same prose.
    """
    draft = make_draft(support_ids=(hero.phantom_support_id,))

    verdict = sv.validate_draft_claims(draft, snapshot)

    assert verdict.grounded is False
    assert verdict.reason_code == sv.DRAFT_CLAIM_UNSUPPORTED
    assert [claim.claim_id for claim in verdict.unsupported] == ["dc_1"]
    assert verdict.unsupported[0].uncited_support_ids == (hero.phantom_support_id,)


# ==========================================================================
# The refused draft -- T9.1's acceptance, both shapes
# ==========================================================================


def test_a_claim_citing_one_real_and_one_phantom_id_is_refused(make_draft, snapshot, hero) -> None:
    """``support_ids`` must be a **subset** of the proof, not intersect it.

    A claim that cites one real id and one invented one is a claim the record
    half-supports, and half is not a citation.
    """
    draft = make_draft(support_ids=(hero.belief_version_id, hero.phantom_support_id))

    verdict = sv.validate_draft_claims(draft, snapshot)

    assert verdict.grounded is False
    assert verdict.reason_code == sv.DRAFT_CLAIM_UNSUPPORTED
    assert verdict.unsupported[0].uncited_support_ids == (hero.phantom_support_id,)


def test_an_uncited_factual_sentence_is_unsupported(make_draft, snapshot, hero) -> None:
    """``T9.1``'s acceptance sentence, with no claim entry behind it.

    "you confirmed cancellation on 20 May" is added to the body and cited by
    nothing. Every ``DraftClaim`` in the draft is still perfectly grounded, so
    a subset check alone returns green. The sentence carries a date, which is a
    factual assertion, and it is covered by no claim span -- so it is refused.
    """
    body = hero.grounded_body + "\n\n" + hero.ungrounded_sentence

    verdict = sv.validate_draft_claims(make_draft(body=body), snapshot)

    assert verdict.grounded is False
    assert verdict.reason_code == sv.DRAFT_CLAIM_UNSUPPORTED
    uncited = [claim for claim in verdict.unsupported if claim.claim_id is None]
    assert [claim.sentence_or_span for claim in uncited] == [hero.ungrounded_sentence]


def test_an_uncited_sentence_with_no_factual_marker_is_not_refused(make_draft, snapshot) -> None:
    """Provenance does not censor the user's own words. It grounds facts.

    "Please confirm the account is closed." is a request, not an assertion
    about the world, and it is in the default body. A validator that refused
    every uncited sentence would refuse every greeting and every signature, and
    would be turned off within a day.
    """
    verdict = sv.validate_draft_claims(make_draft(), snapshot)

    assert verdict.grounded is True


def test_a_refusal_is_never_a_downgrade(make_draft, snapshot, hero) -> None:
    """``T9.1``: reject rather than soften.

    The verdict carries no "hedged" variant of the draft and no repaired body:
    the only two outcomes are grounded and refused. A softened draft would put
    an unsupported assertion in front of a human wearing the system's
    confidence.
    """
    verdict = sv.validate_draft_claims(make_draft(support_ids=(hero.phantom_support_id,)), snapshot)

    assert verdict.grounded is False
    assert not hasattr(verdict, "repaired_draft")
    assert not hasattr(verdict, "hedged_body")


# ==========================================================================
# The snapshot is the snapshot
# ==========================================================================


def test_a_draft_built_against_another_revision_is_refused(make_draft, snapshot) -> None:
    """Validation and approval must agree about the world.

    A draft carrying ``basis_case_revision = 14`` validated against a snapshot
    taken at 13 is not "close enough": the two disagree about which facts were
    committed, and the approval would bind a revision the grounding was never
    checked at.
    """
    verdict = sv.validate_draft_claims(make_draft(basis_case_revision=14), snapshot)

    assert verdict.grounded is False
    assert verdict.reason_code == sv.BASIS_REVISION_MISMATCH


def test_a_draft_for_another_case_is_refused(make_draft, snapshot, hero) -> None:
    """A snapshot of case A cannot ground a draft about case B."""
    verdict = sv.validate_draft_claims(make_draft(case_id=hero.other_case_id), snapshot)

    assert verdict.grounded is False
    assert verdict.reason_code == sv.BASIS_CASE_MISMATCH


def test_a_case_with_no_committed_kernel_decision_has_no_basis(make_draft, snapshot) -> None:
    """Invariant 4, at the grounding step rather than at the executor.

    An action intent references committed rows only. A case whose state has
    never been settled by the Kernel has no committed basis at all, so there is
    nothing for a draft to be grounded in -- and no revision the approval could
    honestly bind to.
    """
    uncommitted = sv.GroundingSnapshot(
        case_id=snapshot.case_id,
        case_revision=snapshot.case_revision,
        support_ids=snapshot.support_ids,
        current_belief_version_ids=snapshot.current_belief_version_ids,
        has_committed_kernel_decision=False,
    )

    verdict = sv.validate_draft_claims(make_draft(), uncommitted)

    assert verdict.grounded is False
    assert verdict.reason_code == sv.NO_COMMITTED_BASIS


# ==========================================================================
# The snapshot is derived from a real State Proof, not hand-assembled
# ==========================================================================


def test_the_snapshot_is_built_from_the_state_proof_support_ids(hero) -> None:
    """``GroundingSnapshot.from_state_proof`` reads ``StateProof.support_ids()``.

    The permitted citation set has exactly one definition and it lives in the
    contract. A second definition here is how the validator and the proof drift
    into disagreeing about what "supported" means.
    """
    belief = builder.build_belief_proof(
        belief_row={
            "belief_id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
            "subject_type": "RELATIONSHIP",
            "subject_id": uuid.UUID("cccccccc-0000-4000-8000-000000000002"),
            "subject_label": "Northline Fiber - NF-4471-8802",
            "predicate": "service_terminated_on",
        },
        version_rows=[
            {
                "belief_version_id": hero.belief_version_id,
                "belief_id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
                "version_no": 1,
                "value_type": "DATE",
                "value_json": "2026-05-31",
                "epistemic_status": EpistemicStatus.CONFIRMED,
                "belief_confidence": 0.92,
                "valid_from": None,
                "valid_to": None,
                "recorded_at": hero.now,
                "superseded_at": None,
                "superseded_by_version_id": None,
                "superseded_by_version_no": None,
                "supersession_reason_code": None,
                "kernel_decision_id": uuid.UUID("55555555-5555-4555-8555-555555555555"),
            }
        ],
        support_rows=[
            {
                "support_id": uuid.UUID("dddddddd-0000-4000-8000-000000000001"),
                "belief_version_id": hero.belief_version_id,
                "source_kind": SupportSourceKind.EVIDENCE,
                "source_id": hero.termination_evidence_id,
                "relation": SupportRelation.SUPPORTS,
                "weight": 0.9,
                "reason_code": "DIRECT_OBSERVATION",
            }
        ],
        evidence_rows={
            hero.termination_evidence_id: {
                "evidence_id": hero.termination_evidence_id,
                "artifact_id": uuid.UUID("cccccccc-0000-4000-8000-000000000001"),
                "evidence_type": "CANCELLATION_NOTICE",
                "exact_text": None,
                "normalized_text": "Service terminates 31 May 2026.",
                "source_locator": {
                    "kind": "EMAIL_PART",
                    "block_id": "blk_0001",
                    "char_start": 0,
                    "char_end": 30,
                },
                "observed_at": hero.now,
                "valid_from": None,
                "valid_to": None,
                "source_authority": 0.9,
                "retraction_status": RetractionStatus.ACTIVE,
                "artifact_received_at": hero.now,
                "artifact_sender": "billing@northlinefiber.example",
            }
        },
    )
    proof = builder.build_state_proof(
        tenant_id=hero.tenant_id,
        user_id=hero.user_id,
        generated_at=hero.now,
        case_row={
            "case_id": hero.case_id,
            "case_type": "BILLING_DISPUTE",
            "title": "Old ISP cancellation",
            "status": "DISPUTED",
            "revision": hero.basis_case_revision,
            "attention_level": "ATTENTION",
            "counterparty_name": "Northline Fiber",
            "relationship_id": uuid.UUID("cccccccc-0000-4000-8000-000000000003"),
            "opened_at": hero.now,
            "reopened_count": 0,
            "last_activity_at": hero.now,
        },
        belief_proofs=[belief],
    )
    assert hero.belief_version_id in proof.support_ids()

    snapshot = sv.GroundingSnapshot.from_state_proof(
        proof,
        case_id=hero.case_id,
        case_revision=hero.basis_case_revision,
        current_belief_version_ids=frozenset(),
        has_committed_kernel_decision=True,
    )

    assert snapshot.support_ids == proof.support_ids()
    assert snapshot.case_revision == hero.basis_case_revision


# ==========================================================================
# "Not loaded" is not "empty"
# ==========================================================================


def test_a_snapshot_whose_support_set_was_never_loaded_refuses_loudly(make_draft, snapshot) -> None:
    """An unknown citation set must not read as an empty one.

    ``frozenset()`` is a real answer: it says the committed record supports
    nothing, so every citation is invented. ``None`` is the absence of an
    answer, and the two must not collapse -- a store that has not loaded the
    set would otherwise declare every claim in every draft unsupported, which
    looks exactly like a correctly refused draft and is a different fact
    entirely.

    So the validator refuses with its own code rather than reporting
    ``DRAFT_CLAIM_UNSUPPORTED`` over a question it never asked.
    """
    unloaded = sv.GroundingSnapshot(
        case_id=snapshot.case_id,
        case_revision=snapshot.case_revision,
        support_ids=None,
        current_belief_version_ids=snapshot.current_belief_version_ids,
        has_committed_kernel_decision=True,
    )

    verdict = sv.validate_draft_claims(make_draft(), unloaded)

    assert verdict.grounded is False
    assert verdict.reason_code == sv.SUPPORT_SET_UNAVAILABLE
    assert verdict.reason_code != sv.DRAFT_CLAIM_UNSUPPORTED
    assert verdict.unsupported == ()


def test_a_genuinely_empty_support_set_still_refuses_as_unsupported(make_draft, snapshot) -> None:
    """The other side of the distinction, so neither code absorbs the other.

    A case whose committed record carries nothing citable is not a loading
    failure. The draft cites an id, the record does not have it, and that is
    ``DRAFT_CLAIM_UNSUPPORTED`` in its ordinary meaning.
    """
    empty = sv.GroundingSnapshot(
        case_id=snapshot.case_id,
        case_revision=snapshot.case_revision,
        support_ids=frozenset(),
        current_belief_version_ids=snapshot.current_belief_version_ids,
        has_committed_kernel_decision=True,
    )

    verdict = sv.validate_draft_claims(make_draft(), empty)

    assert verdict.grounded is False
    assert verdict.reason_code == sv.DRAFT_CLAIM_UNSUPPORTED
    assert [claim.claim_id for claim in verdict.unsupported] == ["dc_1"]
