"""Canonical Addition C — a retracted item cannot reach a prompt.

Written before ``retrieval.py`` exists (T1.5).

Authority
---------
- ``specs/11_CONTRACTS.md`` section 9 and section 9.1, and section 20.5, which
  prints the first version of this file.
- ``CANONICAL_DECISIONS.md`` -> *Evidence and retrieval*: "Stored/generated
  ``is_retrieval_eligible = (retraction_status = 'ACTIVE')``. Only ``ACTIVE``
  evidence may enter new retrieval or ground a new belief", and "Superseded
  evidence [is] excluded from active retrieval ... No down-weighted active path
  exists in v1."
- ``EXECUTION/70_TASK_PLAN.md`` T1.5: "``RetrievalContext`` refuses to hold an
  evidence item whose ``retraction_status`` is not ``ACTIVE``".

Why this is a type and not a query comment
------------------------------------------
Retracted and superseded evidence keeps its row **and its embedding**, because
that is what makes lineage auditable. ANN search will therefore happily return
a correction the user already made. Three defences are stacked, and this file
tests all three plus the containing context:

1. ``EvidenceSnippet.retraction_status`` is ``Literal[ACTIVE]`` — the mistake is
   unrepresentable at the leaf.
2. ``RetrievalContext`` re-checks each snippet in a model validator — so an item
   assembled by a path that skipped field validation (``model_construct``, a
   repository row mapped straight onto the model) still cannot be placed in a
   context object.
3. ``VectorSearchParams.retraction_filter_applied`` is ``Literal[True]`` — a
   result cannot claim an unfiltered search was filtered.

``RetrievalDebug.candidates_filtered_by_retraction`` is the fourth, observable
leg: it makes a silently-changed query visible when the count goes to zero.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from provenance_contracts.ingestion import SourceLocator
from provenance_contracts.retrieval import (
    EvidenceSnippet,
    IdentityCandidate,
    McpToolCall,
    RetrievalContext,
    RetrievalDebug,
    VectorSearchParams,
)
from provenance_domain.enums import (
    AgentSafeView,
    CaseStatus,
    EvidenceType,
    IdentityCandidateKind,
    RelationshipStatus,
    RetractionStatus,
)

pytestmark = pytest.mark.unit

JUNE_5 = datetime(2026, 6, 5, tzinfo=UTC)
SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _snippet(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_id": uuid.uuid4(),
        "artifact_id": uuid.uuid4(),
        "evidence_type": EvidenceType.INVOICE_LINE,
        "normalized_text": "Invoice for service June 1 through June 30. Amount due USD 186.",
        "source_locator": SourceLocator(
            kind="TEXT_SPAN", block_id="blk_body1", char_start=0, char_end=64
        ),
        "observed_at": JUNE_5,
    }
    payload.update(overrides)
    return payload


def _snippet_model(**overrides: Any) -> EvidenceSnippet:
    return EvidenceSnippet(**_snippet(**overrides))


def _context(**overrides: Any) -> RetrievalContext:
    payload: dict[str, Any] = {
        "trace_id": uuid.uuid4(),
        "agent_run_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "retrieved_at": JUNE_5,
    }
    payload.update(overrides)
    return RetrievalContext(**payload)


# ---------------------------------------------------------------------------
# Leg 1 — the leaf type
# ---------------------------------------------------------------------------


def test_active_evidence_is_accepted() -> None:
    assert _snippet_model().retraction_status is RetractionStatus.ACTIVE


@pytest.mark.parametrize(
    "status",
    [RetractionStatus.RETRACTED, RetractionStatus.SUPERSEDED, RetractionStatus.QUARANTINED],
    ids=["retracted", "superseded", "quarantined"],
)
def test_non_active_evidence_cannot_be_constructed(status: RetractionStatus) -> None:
    """Retracted evidence keeps its embedding, so ANN search will return it.

    The type refuses to carry it into a prompt. All three non-ACTIVE statuses
    are refused identically: there is no down-weighted active path in v1.
    """
    with pytest.raises(ValidationError):
        EvidenceSnippet(**_snippet(retraction_status=status))


# ---------------------------------------------------------------------------
# Leg 2 — the containing context re-checks, so a bypass is still refused
# ---------------------------------------------------------------------------


def test_context_refuses_an_evidence_item_that_is_not_active() -> None:
    """T1.5 acceptance, proved against a snippet that skipped field validation.

    ``model_construct`` is how a repository row, a cache, or a partially
    migrated payload can produce a model instance without running field
    validators — and pydantic does not re-validate an already-constructed model
    when it is assigned to a field of another model. Without the validator on
    ``RetrievalContext`` this object would be assembled and handed to a prompt.
    """
    smuggled = EvidenceSnippet.model_construct(
        **_snippet(retraction_status=RetractionStatus.RETRACTED)
    )
    assert smuggled.retraction_status is RetractionStatus.RETRACTED  # the bypass worked

    with pytest.raises(ValidationError) as excinfo:
        _context(evidence_snippets=(smuggled,))
    assert "retraction" in str(excinfo.value).lower()


def test_context_accepts_active_evidence() -> None:
    context = _context(evidence_snippets=(_snippet_model(), _snippet_model()))
    assert len(context.evidence_snippets) == 2
    assert all(s.retraction_status is RetractionStatus.ACTIVE for s in context.evidence_snippets)


# ---------------------------------------------------------------------------
# Leg 3 — a result cannot claim an unfiltered search was filtered
# ---------------------------------------------------------------------------


def test_vector_params_cannot_claim_an_unfiltered_search() -> None:
    with pytest.raises(ValidationError):
        VectorSearchParams(retraction_filter_applied=False)
    with pytest.raises(ValidationError):
        VectorSearchParams(user_prefix_applied=False)


def test_vector_params_pin_the_frozen_embedding_contract() -> None:
    params = VectorSearchParams()
    assert params.model_id == "amazon.titan-embed-text-v2:0"
    assert params.dimensions == 1024
    assert params.embedding_version == "v1"
    assert params.distance == "cosine"
    with pytest.raises(ValidationError):
        VectorSearchParams(dimensions=1536)
    with pytest.raises(ValidationError):
        VectorSearchParams(distance="l2")


# ---------------------------------------------------------------------------
# Leg 4 — the filter is observable, not merely unrepresentable
# ---------------------------------------------------------------------------


def test_debug_records_how_many_candidates_the_retraction_filter_dropped() -> None:
    debug = RetrievalDebug(candidates_considered=14, candidates_filtered_by_retraction=4)
    assert debug.candidates_filtered_by_retraction == 4
    with pytest.raises(ValidationError):
        RetrievalDebug(candidates_filtered_by_retraction=-1)


def test_mcp_tool_calls_are_pinned_to_the_read_only_role_and_the_safe_views() -> None:
    """Addition B. A trace claiming a write role fails validation."""
    call = McpToolCall(
        tool_name="query",
        view=AgentSafeView.EVIDENCE_RETRIEVAL,
        arguments_digest=SHA,
        row_count=3,
        latency_ms=42,
        started_at=JUNE_5,
    )
    assert call.db_role == "pv_agent_reader"
    assert call.server == "cockroachdb-mcp"
    with pytest.raises(ValidationError):
        McpToolCall(
            tool_name="query",
            view=AgentSafeView.EVIDENCE_RETRIEVAL,
            db_role="pv_kernel_writer",
            arguments_digest=SHA,
            row_count=3,
            latency_ms=42,
            started_at=JUNE_5,
        )


def test_mcp_tool_call_carries_a_digest_rather_than_raw_arguments() -> None:
    """Query parameters can echo document text; document text never enters a trace."""
    assert "arguments" not in McpToolCall.model_fields
    with pytest.raises(ValidationError):
        McpToolCall(
            tool_name="query",
            view=AgentSafeView.EVIDENCE_RETRIEVAL,
            arguments_digest="SELECT * FROM evidence_items",
            row_count=1,
            latency_ms=1,
            started_at=JUNE_5,
        )


# ---------------------------------------------------------------------------
# The rest of the bounded context — scope and caps
# ---------------------------------------------------------------------------


def _candidate(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: IdentityCandidateKind = IdentityCandidateKind.CASE,
    score: str = "0.99",
    **overrides: Any,
) -> IdentityCandidate:
    payload: dict[str, Any] = {
        "candidate_kind": kind,
        "candidate_id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "label": "Old ISP cancellation",
        "score": score,
    }
    if kind is IdentityCandidateKind.CASE:
        payload["case_status"] = CaseStatus.RESOLVED
    else:
        payload["relationship_status"] = RelationshipStatus.ACTIVE
    payload.update(overrides)
    return IdentityCandidate(**payload)


def test_context_refuses_candidates_belonging_to_another_user() -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    foreign = _candidate(tenant_id=tenant_id, user_id=uuid.uuid4())
    with pytest.raises(ValidationError) as excinfo:
        _context(tenant_id=tenant_id, user_id=user_id, case_candidates=(foreign,))
    assert "belong to another user" in str(excinfo.value)


def test_context_refuses_candidates_belonging_to_another_tenant() -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    foreign = _candidate(tenant_id=uuid.uuid4(), user_id=user_id)
    with pytest.raises(ValidationError) as excinfo:
        _context(tenant_id=tenant_id, user_id=user_id, case_candidates=(foreign,))
    assert "belong to another user" in str(excinfo.value)


def test_context_refuses_a_candidate_of_the_wrong_kind() -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    relationship = _candidate(
        tenant_id=tenant_id, user_id=user_id, kind=IdentityCandidateKind.RELATIONSHIP
    )
    with pytest.raises(ValidationError) as excinfo:
        _context(tenant_id=tenant_id, user_id=user_id, case_candidates=(relationship,))
    assert "case_candidates must hold CASE kinds" in str(excinfo.value)


def test_identity_candidate_requires_the_status_matching_its_kind() -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ValidationError) as excinfo:
        IdentityCandidate(
            candidate_kind=IdentityCandidateKind.CASE,
            candidate_id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            label="Old ISP cancellation",
            score="0.99",
        )
    assert "CASE candidate must carry case_status" in str(excinfo.value)


def test_evidence_snippet_cap_is_ten() -> None:
    with pytest.raises(ValidationError):
        _context(evidence_snippets=tuple(_snippet_model() for _ in range(11)))


def test_candidate_caps_are_three() -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    four = tuple(_candidate(tenant_id=tenant_id, user_id=user_id) for _ in range(4))
    with pytest.raises(ValidationError):
        _context(tenant_id=tenant_id, user_id=user_id, case_candidates=four)


def test_identity_margin_drives_the_tier_r_resolver() -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    top = _candidate(tenant_id=tenant_id, user_id=user_id, score="0.94")
    second = _candidate(tenant_id=tenant_id, user_id=user_id, score="0.86")
    context = _context(tenant_id=tenant_id, user_id=user_id, case_candidates=(top, second))
    assert context.top_case_candidate() is top
    assert context.identity_margin() == top.score - second.score
    assert _context().identity_margin() is None
    assert _context().top_case_candidate() is None
