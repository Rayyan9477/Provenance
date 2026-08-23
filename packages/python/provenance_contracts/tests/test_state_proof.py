"""Grounding, lineage, and the Judge Mode counterfactual.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 14 (``proof.py``) and section 20.8, which
  prints the first eight tests below.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, third sub-task: ``StateProof`` carries
  ``grounding`` (relations ``SUPPORTS`` / ``CONTRADICTS`` / ``QUALIFIES``) and
  ``lineage`` (the version chain with ``superseded_by_version_no``) "as two
  distinct fields under those two names".
- ``EXECUTION/70_TASK_PLAN.md`` section 2.3, the three-term vocabulary:
  grounding is the edge set and is never the version chain.

Why the vocabulary is asserted structurally
--------------------------------------------
``test_grounding_and_lineage_are_two_distinct_fields`` reads the model's own
field table rather than an instance. A rename or a merge of the two fields is
the failure mode the three-term vocabulary exists to prevent, and it would
otherwise be invisible to every behavioural test in this file: a proof that put
the version chain under ``grounding`` would still validate, still hash, and
still render -- and would still be wrong.

Recorded deviation from section 20.8
------------------------------------
Section 20.8's fixtures are untyped (``def _evidence():``) because the spec
prints them as illustrations. They are annotated here: this repository runs
``ruff`` over the test tree, and an un-annotated helper is not a
recorded-deviation-worthy difference in behaviour.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from provenance_contracts.ingestion import SourceLocator
from provenance_contracts.proof import (
    BeliefProof,
    BeliefVersionProof,
    EvidenceProof,
    GroundingEdgeProof,
    LineageEntry,
    StateProof,
)
from provenance_domain.enums import (
    EpistemicStatus,
    EvidenceType,
    MemoryMode,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    ValueType,
)

NOW = datetime(2026, 6, 5, tzinfo=UTC)
EVIDENCE_ID = uuid.uuid4()


def _evidence() -> EvidenceProof:
    return EvidenceProof(
        evidence_id=EVIDENCE_ID,
        artifact_id=uuid.uuid4(),
        evidence_type=EvidenceType.CANCELLATION_NOTICE,
        normalized_text="Your cancellation is confirmed. Service ends 31 May 2026.",
        source_locator=SourceLocator(
            kind="TEXT_SPAN", block_id="blk_body1", char_start=0, char_end=56
        ),
        observed_at=datetime(2026, 5, 15, tzinfo=UTC),
        artifact_received_at=datetime(2026, 5, 15, tzinfo=UTC),
    )


def _version(no: int = 1) -> BeliefVersionProof:
    return BeliefVersionProof(
        belief_version_id=uuid.uuid4(),
        version_no=no,
        value_type=ValueType.BOOLEAN,
        value_json=True,
        epistemic_status=EpistemicStatus.CONFIRMED,
        belief_confidence=Decimal("0.95"),
        recorded_at=NOW,
        kernel_decision_id=uuid.uuid4(),
    )


def _belief(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "belief_id": uuid.uuid4(),
        "subject_type": SubjectType.RELATIONSHIP,
        "subject_id": uuid.uuid4(),
        "subject_label": "Old ISP",
        "predicate": "service_terminated",
        "current_version": _version(),
        "grounding": (
            GroundingEdgeProof(
                support_id=uuid.uuid4(),
                source_kind=SupportSourceKind.EVIDENCE,
                source_id=EVIDENCE_ID,
                relation=SupportRelation.SUPPORTS,
                evidence=_evidence(),
            ),
        ),
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# specs/11_CONTRACTS.md section 20.8
# ---------------------------------------------------------------------------


def test_a_canonical_belief_must_be_grounded_at_render_time() -> None:
    with pytest.raises(ValidationError) as excinfo:
        BeliefProof(**_belief(grounding=()))
    assert "UNGROUNDED" in str(excinfo.value)


def test_an_evidence_edge_must_render_its_evidence() -> None:
    with pytest.raises(ValidationError) as excinfo:
        GroundingEdgeProof(
            support_id=uuid.uuid4(),
            source_kind=SupportSourceKind.EVIDENCE,
            source_id=EVIDENCE_ID,
            relation=SupportRelation.SUPPORTS,
        )
    assert "must render its evidence" in str(excinfo.value)


def test_lineage_supersession_requires_a_reason() -> None:
    with pytest.raises(ValidationError) as excinfo:
        LineageEntry(
            belief_version_id=uuid.uuid4(),
            version_no=1,
            value_json=True,
            epistemic_status=EpistemicStatus.SUPERSEDED,
            recorded_at=NOW,
            superseded_at=NOW,
            superseded_by_version_id=uuid.uuid4(),
            kernel_decision_id=uuid.uuid4(),
        )
    assert "without a reason code" in str(excinfo.value)


def test_lineage_must_end_at_the_current_version() -> None:
    v2 = _version(2)
    stale_lineage = (
        LineageEntry(
            belief_version_id=uuid.uuid4(),
            version_no=1,
            value_json=True,
            epistemic_status=EpistemicStatus.SUPERSEDED,
            recorded_at=NOW,
            superseded_at=NOW,
            superseded_by_version_id=v2.belief_version_id,
            supersession_reason_code="CONTRADICTORY_EVIDENCE",
            kernel_decision_id=uuid.uuid4(),
        ),
    )
    with pytest.raises(ValidationError) as excinfo:
        BeliefProof(**_belief(current_version=v2, lineage=stale_lineage))
    assert "exactly one lineage entry may be un-superseded" in str(excinfo.value)


def test_memory_off_proof_must_be_empty() -> None:
    """Addition A. The counterfactual cannot be contaminated."""
    with pytest.raises(ValidationError) as excinfo:
        StateProof(
            proof_id=uuid.uuid4(),
            generated_at=NOW,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            memory_mode=MemoryMode.OFF,
            memory_disabled_reason="Judge Mode: retrieval and canonical memory disabled.",
            beliefs=(BeliefProof(**_belief()),),
        )
    assert "must be empty" in str(excinfo.value)


def test_memory_off_proof_requires_a_stated_reason() -> None:
    with pytest.raises(ValidationError) as excinfo:
        StateProof(
            proof_id=uuid.uuid4(),
            generated_at=NOW,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            memory_mode=MemoryMode.OFF,
        )
    assert "memory_disabled_reason" in str(excinfo.value)


def test_memory_off_proof_is_valid_when_genuinely_empty() -> None:
    proof = StateProof(
        proof_id=uuid.uuid4(),
        generated_at=NOW,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        memory_mode=MemoryMode.OFF,
        memory_disabled_reason="Judge Mode: retrieval and canonical memory disabled.",
    )
    assert proof.support_ids() == frozenset()


def test_proof_hash_is_stable_across_renderings() -> None:
    common: dict[str, Any] = {
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "memory_mode": MemoryMode.OFF,
        "memory_disabled_reason": "Judge Mode: retrieval and canonical memory disabled.",
    }
    first = StateProof(proof_id=uuid.uuid4(), generated_at=NOW, **common)
    second = StateProof(
        proof_id=uuid.uuid4(),
        generated_at=datetime(2027, 1, 1, tzinfo=UTC),
        **common,
    )
    assert first.compute_hash() == second.compute_hash()
    assert first.with_hash().proof_hash == first.compute_hash()


# ---------------------------------------------------------------------------
# T1.6 sub-task 3 — grounding and lineage are never conflated
# ---------------------------------------------------------------------------


def test_grounding_and_lineage_are_two_distinct_fields() -> None:
    """Two names, two types, two questions. Never merged into one field."""
    fields = BeliefProof.model_fields
    assert {"grounding", "lineage"} <= set(fields)

    grounded = BeliefProof(**_belief())
    assert all(isinstance(edge, GroundingEdgeProof) for edge in grounded.grounding)
    assert grounded.lineage == ()

    # The edge set answers "why", and its vocabulary is exactly the three
    # relations. The version chain has no relation at all.
    assert {r.value for r in SupportRelation} == {"SUPPORTS", "CONTRADICTS", "QUALIFIES"}
    assert "relation" not in LineageEntry.model_fields
    assert "superseded_by_version_no" not in GroundingEdgeProof.model_fields


def test_a_contradicting_edge_is_grounding_and_is_rendered_not_hidden() -> None:
    """The hero proof shows the counterparty's contradiction next to the belief.

    A proof that dropped CONTRADICTS edges would read as agreement, which is
    the opposite of what the case is about.
    """
    contradiction = GroundingEdgeProof(
        support_id=uuid.uuid4(),
        source_kind=SupportSourceKind.CLAIM,
        source_id=uuid.uuid4(),
        relation=SupportRelation.CONTRADICTS,
        claim_summary="The invoice asserts service was delivered 1-30 June 2026.",
    )
    belief = BeliefProof(
        **_belief(grounding=(*_belief()["grounding"], contradiction)),
    )
    assert belief.contradicting_edges == (contradiction,)
    assert len(belief.grounding) == 2


def test_the_version_chain_carries_superseded_by_version_no() -> None:
    """T1.6 names the field; the API, the UX spec and the G8 query all read it."""
    v2 = _version(2)
    chain = (
        LineageEntry(
            belief_version_id=uuid.uuid4(),
            version_no=1,
            value_json=False,
            epistemic_status=EpistemicStatus.SUPERSEDED,
            recorded_at=NOW,
            superseded_at=NOW,
            superseded_by_version_id=v2.belief_version_id,
            superseded_by_version_no=2,
            supersession_reason_code="CONTRADICTORY_EVIDENCE",
            kernel_decision_id=uuid.uuid4(),
        ),
        LineageEntry(
            belief_version_id=v2.belief_version_id,
            version_no=2,
            value_json=True,
            epistemic_status=EpistemicStatus.CONFIRMED,
            recorded_at=NOW,
            kernel_decision_id=uuid.uuid4(),
        ),
    )
    belief = BeliefProof(**_belief(current_version=v2, lineage=chain))
    superseded = [e.superseded_by_version_no for e in belief.lineage if e.superseded_by_version_no]
    assert superseded == [2]
    assert [e.is_current for e in belief.lineage] == [False, True]


def test_a_successor_pointer_that_disagrees_with_its_id_is_refused() -> None:
    """Two spellings of one pointer must tell the same story.

    The chain here is otherwise well-formed -- both versions present, exactly
    one open end -- so the only thing wrong with it is the disagreement, and
    the assertion cannot be satisfied by an unrelated structural complaint.
    """
    v2 = _version(2)
    with pytest.raises(ValidationError) as excinfo:
        BeliefProof(
            **_belief(
                current_version=v2,
                lineage=(
                    LineageEntry(
                        belief_version_id=uuid.uuid4(),
                        version_no=1,
                        value_json=False,
                        epistemic_status=EpistemicStatus.SUPERSEDED,
                        recorded_at=NOW,
                        superseded_at=NOW,
                        superseded_by_version_id=v2.belief_version_id,
                        superseded_by_version_no=7,
                        supersession_reason_code="CONTRADICTORY_EVIDENCE",
                        kernel_decision_id=uuid.uuid4(),
                    ),
                    LineageEntry(
                        belief_version_id=v2.belief_version_id,
                        version_no=2,
                        value_json=True,
                        epistemic_status=EpistemicStatus.CONFIRMED,
                        recorded_at=NOW,
                        kernel_decision_id=uuid.uuid4(),
                    ),
                ),
            )
        )
    assert "but the id it carries is version 2" in str(excinfo.value)


def test_current_version_must_still_be_the_newest_entry() -> None:
    """The other half of the reordered pair, kept reachable on purpose.

    Reordering two checks is exactly how one of them quietly becomes dead, so
    the check that no longer speaks first is asserted directly: a chain with a
    single open end that nonetheless stops below the current version.
    """
    with pytest.raises(ValidationError) as excinfo:
        BeliefProof(
            **_belief(
                current_version=_version(2),
                lineage=(
                    LineageEntry(
                        belief_version_id=uuid.uuid4(),
                        version_no=1,
                        value_json=True,
                        epistemic_status=EpistemicStatus.CONFIRMED,
                        recorded_at=NOW,
                        kernel_decision_id=uuid.uuid4(),
                    ),
                ),
            )
        )
    assert "must be the newest entry in the lineage chain" in str(excinfo.value)


def test_the_chain_only_moves_forward() -> None:
    with pytest.raises(ValidationError) as excinfo:
        LineageEntry(
            belief_version_id=uuid.uuid4(),
            version_no=3,
            value_json=True,
            epistemic_status=EpistemicStatus.SUPERSEDED,
            recorded_at=NOW,
            superseded_at=NOW,
            superseded_by_version_id=uuid.uuid4(),
            superseded_by_version_no=2,
            supersession_reason_code="CONTRADICTORY_EVIDENCE",
            kernel_decision_id=uuid.uuid4(),
        )
    assert "the chain only moves forward" in str(excinfo.value)


def test_a_memory_on_proof_must_name_its_case() -> None:
    with pytest.raises(ValidationError) as excinfo:
        StateProof(
            proof_id=uuid.uuid4(),
            generated_at=NOW,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        )
    assert "must name its case" in str(excinfo.value)
