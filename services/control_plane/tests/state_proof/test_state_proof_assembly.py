"""State Proof: grounding and lineage, two fields, never one (``T5.3``).

Authority
---------
- ``docs/specs/11_CONTRACTS.md`` section 14, and
  ``packages/python/provenance_contracts/src/provenance_contracts/proof.py``,
  which implements it.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 2.3 and ``T5.3``: "Two fields, two
  names, never merged."
- ``docs/quality/23_PHASE_GATES.md`` ``G5.1``-``G5.5``.

The distinction this file defends
----------------------------------
**grounding** is the ``belief_support`` edge set -- ``SUPPORTS`` /
``CONTRADICTS`` / ``QUALIFIES`` -- and it answers *why do you believe this*.
**lineage** is the ``belief_versions`` supersession chain and its reason codes,
and it answers *what did you believe before, and what changed your mind*. They
come from different tables, they have different cardinalities, and they are
rendered under exactly those two names.

Merging them is not a cosmetic error. A changelog is not an argument: a user
disputing a charge needs to show the counterparty *the evidence*, and a system
that hands them a version history instead has answered a different question
confidently. :func:`test_a_merged_render_is_rejected` is the test that would
fail if the two were ever unified, and it fails at construction time rather
than at review time.

No model, structurally
----------------------
Every assertion here runs on committed row shapes. ``G5.1`` proves the absence
of a model dynamically by constructing an ``ExplodingClient``;
``tests/retrieval/test_no_unscoped_sql.py::test_the_state_proof_builder_
reaches_no_model`` proves it structurally by walking the import graph. This
file needs neither, because it never leaves the process.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from provenance_contracts.proof import BeliefProof, StateProof
from provenance_domain.enums import (
    EpistemicStatus,
    MemoryMode,
    RetractionStatus,
    SupportRelation,
    SupportSourceKind,
)
from services.control_plane.app.state_proof import builder

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)

BELIEF_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
CASE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
TENANT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
USER_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
DECISION_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")

V1_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
V2_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000002")

TERMINATION_EVIDENCE = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001")
INVOICE_EVIDENCE = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
ARTIFACT_ID = uuid.UUID("cccccccc-0000-4000-8000-000000000001")
SUBJECT_ID = uuid.UUID("cccccccc-0000-4000-8000-000000000002")
RELATIONSHIP_ID = uuid.UUID("cccccccc-0000-4000-8000-000000000003")
SUPPORTS_EDGE_ID = uuid.UUID("dddddddd-0000-4000-8000-000000000001")
CONTRADICTS_EDGE_ID = uuid.UUID("dddddddd-0000-4000-8000-000000000002")

#: Every id here is a literal. A ``uuid4()`` in a fixture would make
#: ``test_the_proof_hash_is_stable_across_two_renderings`` fail for a reason
#: that has nothing to do with the property it asserts -- and, worse, would
#: make it *pass* for the wrong reason if the hash ever stopped covering ids.


def _evidence_row(evidence_id: uuid.UUID, text: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "evidence_id": evidence_id,
        "artifact_id": ARTIFACT_ID,
        "evidence_type": "CANCELLATION_NOTICE",
        "exact_text": None,
        "normalized_text": text,
        "source_locator": {
            "kind": "EMAIL_PART",
            "block_id": "blk_0001",
            "char_start": 0,
            "char_end": 40,
        },
        "observed_at": NOW,
        "valid_from": None,
        "valid_to": None,
        "source_authority": 0.9,
        "retraction_status": RetractionStatus.ACTIVE,
        "artifact_received_at": NOW,
        "artifact_sender": "billing@northlinefiber.example",
    }
    row.update(overrides)
    return row


def _support_row(
    source_id: uuid.UUID,
    relation: SupportRelation,
    support_id: uuid.UUID = SUPPORTS_EDGE_ID,
    **overrides: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "support_id": support_id,
        "belief_version_id": V2_ID,
        "source_kind": SupportSourceKind.EVIDENCE,
        "source_id": source_id,
        "relation": relation,
        "weight": 0.9,
        "reason_code": "DIRECT_OBSERVATION",
    }
    row.update(overrides)
    return row


def _version_row(
    version_id: uuid.UUID, version_no: int, value: Any, **overrides: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "belief_version_id": version_id,
        "belief_id": BELIEF_ID,
        "version_no": version_no,
        "value_type": "DATE",
        "value_json": value,
        "epistemic_status": EpistemicStatus.CONFIRMED,
        "belief_confidence": 0.92,
        "valid_from": None,
        "valid_to": None,
        "recorded_at": NOW,
        "superseded_at": None,
        "superseded_by_version_id": None,
        "superseded_by_version_no": None,
        "supersession_reason_code": None,
        "kernel_decision_id": DECISION_ID,
    }
    row.update(overrides)
    return row


#: The hero shape: v1 said 31 July, v2 says 31 May, and v2 is grounded by a
#: cancellation confirmation that CONTRADICTS the later invoice.
HERO_VERSIONS = [
    _version_row(
        V1_ID,
        1,
        "2026-07-31",
        epistemic_status=EpistemicStatus.SUPERSEDED,
        superseded_at=NOW,
        superseded_by_version_id=V2_ID,
        superseded_by_version_no=2,
        supersession_reason_code="USER_CORRECTION",
    ),
    _version_row(V2_ID, 2, "2026-05-31"),
]

HERO_SUPPORT = [
    _support_row(TERMINATION_EVIDENCE, SupportRelation.SUPPORTS),
    _support_row(INVOICE_EVIDENCE, SupportRelation.CONTRADICTS, CONTRADICTS_EDGE_ID, weight=0.8),
]

HERO_EVIDENCE = {
    TERMINATION_EVIDENCE: _evidence_row(
        TERMINATION_EVIDENCE, "Service terminates 31 May 2026 at 214 Ridgeway Apt 3B."
    ),
    INVOICE_EVIDENCE: _evidence_row(
        INVOICE_EVIDENCE,
        "Invoice for internet service covering 1 June through 30 June 2026.",
        evidence_type="INVOICE_LINE",
    ),
}

HERO_BELIEF = {
    "belief_id": BELIEF_ID,
    "subject_type": "RELATIONSHIP",
    "subject_id": SUBJECT_ID,
    "subject_label": "Northline Fiber - NF-4471-8802",
    "predicate": "service_terminated_on",
}


def hero_belief_proof(**overrides: Any) -> BeliefProof:
    kwargs: dict[str, Any] = {
        "belief_row": HERO_BELIEF,
        "version_rows": HERO_VERSIONS,
        "support_rows": HERO_SUPPORT,
        "evidence_rows": HERO_EVIDENCE,
    }
    kwargs.update(overrides)
    return builder.build_belief_proof(**kwargs)


# ==========================================================================
# The vocabulary
# ==========================================================================


def test_grounding_carries_the_support_edges_under_that_name() -> None:
    """grounding = ``belief_support``. Three relations, and only those three."""
    proof = hero_belief_proof()
    assert {edge.relation for edge in proof.grounding} == {
        SupportRelation.SUPPORTS,
        SupportRelation.CONTRADICTS,
    }
    assert all(
        edge.relation
        in (SupportRelation.SUPPORTS, SupportRelation.CONTRADICTS, SupportRelation.QUALIFIES)
        for edge in proof.grounding
    )


def test_lineage_carries_the_version_chain_under_that_name() -> None:
    """lineage = ``belief_versions``, oldest first, each with its reason code."""
    proof = hero_belief_proof()
    assert [entry.version_no for entry in proof.lineage] == [1, 2]
    assert proof.lineage[0].supersession_reason_code == "USER_CORRECTION"
    assert proof.lineage[0].superseded_by_version_no == 2
    assert proof.lineage[1].is_current


def test_the_gate_query_shape_is_what_g5_2_expects() -> None:
    """``G5.2``'s ``jq`` expression, evaluated in Python against the same object.

    ``{"grounding": ["CONTRADICTS","SUPPORTS"], "lineage_depth": 2,
    "superseded": 1}``. Asserting the *shape* of the gate command here means a
    field rename fails in the unit lane rather than in a ``curl`` at gate time.
    """
    proof = hero_belief_proof()
    payload = proof.model_dump(mode="json")
    assert sorted({edge["relation"] for edge in payload["grounding"]}) == [
        "CONTRADICTS",
        "SUPPORTS",
    ]
    assert len(payload["lineage"]) == 2
    superseded = [
        entry for entry in payload["lineage"] if entry["superseded_by_version_no"] is not None
    ]
    assert len(superseded) == 1


def test_a_merged_render_is_rejected() -> None:
    """The central distinction, enforced rather than reviewed.

    A "simplification" that folded the version chain into the edge set would
    produce something that still serialises, still renders, and still looks like
    a proof -- while answering *what changed* to a user who asked *why*. The
    builder refuses the merge at the point someone would make it.
    """
    with pytest.raises(builder.GroundingLineageMergeError):
        builder.build_belief_proof(
            belief_row=HERO_BELIEF,
            version_rows=HERO_VERSIONS,
            support_rows=HERO_SUPPORT,
            evidence_rows=HERO_EVIDENCE,
            merge_grounding_and_lineage=True,
        )


def test_grounding_and_lineage_are_disjoint_id_spaces() -> None:
    """A support edge id is never a belief-version id, and the builder proves it.

    If a future reader ever finds these two lists sharing an identifier, the
    tables have been conflated upstream -- so the check lives here rather than
    in a comment about how it cannot happen.
    """
    proof = hero_belief_proof()
    grounding_ids = {edge.support_id for edge in proof.grounding}
    lineage_ids = {entry.belief_version_id for entry in proof.lineage}
    assert grounding_ids.isdisjoint(lineage_ids)


# ==========================================================================
# What the proof must refuse
# ==========================================================================


def test_a_canonical_belief_with_no_supports_edge_is_refused() -> None:
    """Invariant 5, checked at render time.

    A canonical belief the system cannot justify is a data-integrity incident,
    not a rendering bug. Failing loudly is the point: the alternative is a
    State Proof that displays a confident value with an empty "why".
    """
    with pytest.raises(ValueError, match="UNGROUNDED"):
        hero_belief_proof(
            support_rows=[_support_row(INVOICE_EVIDENCE, SupportRelation.CONTRADICTS)]
        )


def test_an_evidence_edge_must_render_its_evidence() -> None:
    """A proof that says "trust me" is not a proof.

    An ``EVIDENCE`` edge whose evidence row is missing means the reader is asked
    to accept an identifier in place of the observation.
    """
    with pytest.raises(builder.MissingEvidenceError):
        hero_belief_proof(evidence_rows={INVOICE_EVIDENCE: HERO_EVIDENCE[INVOICE_EVIDENCE]})


def test_a_supersession_without_a_reason_code_is_refused() -> None:
    """Lineage without reasons is a changelog. Lineage with reason codes is an
    argument, which is what the user needs when a counterparty disputes it."""
    broken = [dict(HERO_VERSIONS[0]) | {"supersession_reason_code": None}, HERO_VERSIONS[1]]
    with pytest.raises(ValueError, match="(?i)reason code"):
        hero_belief_proof(version_rows=broken)


def test_the_chain_only_moves_forward() -> None:
    """A successor pointer naming an earlier version is a corrupt chain."""
    broken = [
        dict(HERO_VERSIONS[0]) | {"superseded_by_version_no": 1},
        HERO_VERSIONS[1],
    ]
    with pytest.raises(ValueError, match="(?i)forward|supersed"):
        hero_belief_proof(version_rows=broken)


# ==========================================================================
# Retracted evidence: visible here, excluded from retrieval
# ==========================================================================


def test_retracted_evidence_is_rendered_with_its_status_badge() -> None:
    """Historical visibility and retrieval eligibility are different questions.

    Retrieval excludes a retracted item; State Proof must *show* it, because
    the retracted invoice is frequently the ``CONTRADICTS`` edge that explains
    why the belief moved. Hiding it would defeat the purpose of keeping
    lineage.
    """
    evidence = dict(HERO_EVIDENCE)
    evidence[INVOICE_EVIDENCE] = _evidence_row(
        INVOICE_EVIDENCE,
        "Invoice for internet service covering 1 June through 30 June 2026.",
        retraction_status=RetractionStatus.RETRACTED,
    )
    proof = hero_belief_proof(evidence_rows=evidence)
    rendered = {
        edge.source_id: edge.evidence.retraction_status
        for edge in proof.grounding
        if edge.evidence is not None
    }
    assert rendered[INVOICE_EVIDENCE] is RetractionStatus.RETRACTED
    assert rendered[TERMINATION_EVIDENCE] is RetractionStatus.ACTIVE


# ==========================================================================
# The whole proof
# ==========================================================================


def _state_proof(**overrides: Any) -> StateProof:
    kwargs: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "user_id": USER_ID,
        "generated_at": NOW,
        "case_row": {
            "case_id": CASE_ID,
            "case_type": "BILLING_DISPUTE",
            "title": "Old ISP final bill reconciliation",
            "status": "RESOLVED",
            "revision": 7,
            "attention_level": "NONE",
            "counterparty_name": "Northline Fiber",
            "relationship_id": RELATIONSHIP_ID,
            "opened_at": NOW,
            "resolved_at": NOW,
            "reopened_count": 0,
            "last_activity_at": NOW,
        },
        "belief_proofs": (hero_belief_proof(),),
        "transition_rows": (),
    }
    kwargs.update(overrides)
    return builder.build_state_proof(**kwargs)


def test_the_proof_hash_is_stable_across_two_renderings() -> None:
    """Two renderings of one committed revision must agree.

    The Advocate binds a draft to this value and the executor re-computes it
    before sending; a hash that moved with the wall clock would invalidate
    every approved action the moment it was read twice.
    """
    first = _state_proof().with_hash()
    second = _state_proof(generated_at=NOW.replace(hour=23)).with_hash()
    assert first.proof_hash == second.proof_hash


def test_a_memory_off_proof_is_empty_and_says_why() -> None:
    """Judge Mode runs the identical artifact twice.

    With memory OFF there is no retrieval and no canonical state, so the honest
    rendering is an empty proof with a stated reason -- which is exactly why the
    OFF reply can only restate the invoice while the ON reply can say it
    contradicts the recorded termination.
    """
    proof = builder.build_state_proof(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        generated_at=NOW,
        case_row=None,
        belief_proofs=(),
        transition_rows=(),
        memory_mode=MemoryMode.OFF,
        memory_disabled_reason="Judge Mode counterfactual: memory disabled",
    )
    assert proof.memory_mode is MemoryMode.OFF
    assert proof.beliefs == ()
    assert proof.memory_disabled_reason is not None


def test_a_memory_off_proof_cannot_be_contaminated_with_canonical_state() -> None:
    """The counterfactual is only a counterfactual if it is genuinely empty.

    Leaking one belief into the OFF rendering would make the ON/OFF comparison
    a demonstration of nothing while looking exactly like a demonstration of
    something.
    """
    with pytest.raises(ValueError, match="(?i)OFF proof must be empty"):
        builder.build_state_proof(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            generated_at=NOW,
            case_row=None,
            belief_proofs=(hero_belief_proof(),),
            transition_rows=(),
            memory_mode=MemoryMode.OFF,
            memory_disabled_reason="Judge Mode counterfactual: memory disabled",
        )


def test_no_chain_of_thought_key_appears_anywhere_in_the_proof() -> None:
    """``G5.4``, run as a scan over every path in the serialised object.

    The gate greps the HTTP response; this greps the object that becomes it, so
    the failure is caught in the unit lane by the task that introduces the field
    rather than by a ``curl`` at gate time.
    """
    banned = ("thinking", "reasoning_trace", "scratchpad", "raw_completion")
    paths = builder.scalar_paths(_state_proof().model_dump(mode="json"))
    offenders = [p for p in paths if any(token in p.lower() for token in banned)]
    assert not offenders, f"chain-of-thought keys in a read model: {offenders}"
    assert paths, "the path scan found nothing; the assertion above is vacuous"


def test_transitions_never_exceed_the_revision_they_describe() -> None:
    """A proof is bound to one case revision.

    A transition newer than the revision means the read was not point-in-time
    consistent, and an action approved against it would be approved against a
    state that no longer exists.
    """
    future = {
        "state_transition_id": uuid.UUID("eeeeeeee-0000-4000-8000-000000000001"),
        "transition_type": "CASE_STATUS",
        "case_revision": 99,
        "from_state": "RESOLVED",
        "to_state": "REOPENED",
        "reason_code": "NEW_EVIDENCE",
        "recorded_at": NOW,
    }
    with pytest.raises(ValueError, match="(?i)newer than the case revision"):
        _state_proof(transition_rows=(future,))


# ==========================================================================
# The PV_SABOTAGE hook -- G5.5
# ==========================================================================


def test_the_sabotage_hook_is_declared_and_reachable() -> None:
    """``G5.5`` neuters ``load_grounding`` and requires this suite to go red.

    The hook is only meaningful if the production path reaches the symbol
    through its **module global**. A ``from``-import copies the reference before
    the rebind is visible and the sabotage silently never arrives, so the
    declaration is asserted here and the call convention is asserted by
    :func:`test_build_belief_proof_calls_load_grounding_through_the_module_global`.
    """
    assert "load_grounding" in builder.SABOTAGE_HOOKS
    assert builder.SABOTAGE_MODULE == "state_proof.builder"
    assert builder.SABOTAGED_SYMBOLS == () or "load_grounding" in builder.SABOTAGED_SYMBOLS


def test_build_belief_proof_calls_load_grounding_through_the_module_global() -> None:
    """The wiring that makes the sabotage entry trustworthy, asserted on the AST.

    ``PV_SABOTAGE`` replaces the attribute on the module object. A caller that
    bound the function to a local name at import time would keep calling the
    original, and ``make sabotage`` would report green for a symbol nobody
    neutered.
    """
    import ast
    import inspect

    source = inspect.getsource(builder.build_belief_proof)
    tree = ast.parse(source.lstrip())
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "load_grounding" in called
