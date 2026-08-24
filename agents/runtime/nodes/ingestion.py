"""The eleven ingestion nodes of ``03_AGENTS_LANGGRAPH_CONTRACTS.md`` section 4.

Authority
---------
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` sections 4 and 5.
- ``docs/specs/14_PROMPTS.md`` sections 2, 3 and 7.
- ``docs/CANONICAL_DECISIONS.md`` -> *Multi-case artifacts*, *Identity order*.
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.3.

Every function here has the same shape: ``(state, deps) -> state``. Nodes never
raise past their own boundary and never mutate the state they were given; each
returns a new frozen :class:`~agents.runtime.state.IngestionGraphState` with one
more entry in ``visits``. Two consequences the tests depend on:

* a failure is a recorded :class:`~agents.runtime.state.GraphError` plus a
  terminal outcome, so the visit order up to the failure survives;
* the visit order is the honest record of what ran, because no node can rewrite
  an earlier node's entry.

Nine of the eleven nodes contain no model call at all. That is the design, not
an accident of this file: ``14_PROMPTS.md`` section 1 says Provenance has
exactly four model nodes and "everything else in both graphs is deterministic
Python". Two of those four are here.
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

from agents.runtime.graphs.resolver import guard_assessment
from agents.runtime.schemas.validation import ValidationFailure, validate_extraction
from agents.runtime.state import (
    GraphError,
    IngestionGraphState,
    IngestionOutcome,
    ModelPending,
    ModelSuccess,
    ResolutionSignals,
    RetrievalSpec,
)
from provenance_contracts.ingestion import (
    ClaimCandidate,
    CommitmentCandidate,
    ContentBlock,
    ExtractionResult,
)
from provenance_contracts.kernel import KernelCommitResult
from provenance_contracts.proposal import (
    MemoryProposal,
    ProposalIdentity,
    ProposedClaim,
    ProposedCommitment,
)
from provenance_contracts.resolution import ResolutionAssessment
from provenance_domain.enums import (
    KernelDecision,
    ProposalType,
)
from provenance_domain.invariants import HUMAN_REVIEW_CONFIDENCE_FLOOR

__all__ = [
    "PROPOSAL_NAMESPACE",
    "build_memory_proposal",
    "extract_structured_evidence",
    "load_artifact_metadata",
    "load_normalized_content",
    "register_or_lookup_evidence",
    "resolution_signals_from",
    "retrieve_candidate_context",
    "route_commit_result",
    "route_resolution_need",
    "strong_resolution",
    "submit_to_memory_kernel",
    "validate_extraction_schema",
]

#: Proposal and evidence ids are derived, never random. Two runs over the same
#: artifact under the same graph version therefore produce the same
#: ``idempotency_key``, which is what makes a re-drive a no-op instead of a
#: duplicate. ``uuid5`` over a stable namespace is the whole mechanism.
PROPOSAL_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _visit(state: IngestionGraphState, node: str, **updates: Any) -> IngestionGraphState:
    return dataclasses.replace(state, visits=(*state.visits, node), **updates)


def _fail(
    state: IngestionGraphState,
    node: str,
    *,
    code: str,
    detail: str,
    outcome: IngestionOutcome,
    **updates: Any,
) -> IngestionGraphState:
    return _visit(
        state,
        node,
        errors=(*state.errors, GraphError(node=node, code=code, detail=detail)),
        outcome=outcome,
        **updates,
    )


# ---------------------------------------------------------------------------
# 5.1 / 5.2 — the artifact
# ---------------------------------------------------------------------------


def load_artifact_metadata(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Deterministic. Safe metadata plus a content locator, authorized by binding.

    The ownership check is here rather than downstream because every later node
    reads content: an artifact belonging to another user must be refused before
    a single byte of it is fetched, not after.
    """
    node = "load_artifact_metadata"
    metadata = deps.artifacts.get_artifact_metadata(state.artifact_id)
    binding = state.principal_ref
    if metadata.user_id != binding.user_id or metadata.tenant_id != binding.tenant_id:
        return _fail(
            state,
            node,
            code="ARTIFACT_FOREIGN_USER",
            detail="the artifact does not belong to the user this run is bound to",
            outcome=IngestionOutcome.REJECTED,
        )
    if binding.artifact_id is not None and binding.artifact_id != metadata.artifact_id:
        return _fail(
            state,
            node,
            code="BINDING_ARTIFACT_MISMATCH",
            detail="the capability binding names a different artifact",
            outcome=IngestionOutcome.REJECTED,
        )
    deps.session.record(run_id=state.agent_run_id, node=node, note="metadata loaded")
    # The agent-facing projection: no bucket, no key, no version id. Content
    # arrives through get_normalized_content, which re-authorises.
    return _visit(state, node, artifact_metadata=metadata.redacted_for_agent())


def load_normalized_content(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Deterministic. Parser-produced blocks, not arbitrary database history.

    ``QUOTED_HISTORY`` arrives as a block *kind*, decided once by the parser.
    Every later stage reads the tag instead of re-deciding, which is what stops
    a four-month-old forwarded promise from being admitted twice.
    """
    node = "load_normalized_content"
    content = deps.artifacts.get_normalized_content(state.artifact_id)
    if not content.blocks:
        return _fail(
            state,
            node,
            code="ARTIFACT_HAS_NO_CONTENT",
            detail="the parser produced no content blocks; nothing can be extracted",
            outcome=IngestionOutcome.FAIL_SAFE,
            normalized_content=content,
        )
    deps.session.record(run_id=state.agent_run_id, node=node, note="content loaded")
    return _visit(state, node, normalized_content=content)


# ---------------------------------------------------------------------------
# 5.3 / 5.4 — Tier E extraction and its deterministic validation
# ---------------------------------------------------------------------------


def _trusted_context(state: IngestionGraphState) -> dict[str, Any]:
    """Provenance's own metadata. Never artifact text.

    ``14_PROMPTS.md`` section 2.1: the trusted block carries ids, enum values,
    decimals and timestamps. Everything here is generated by Provenance, so
    there is no path by which a sender controls a byte of it.
    """
    metadata = state.artifact_metadata
    return {
        "artifact_id": str(state.artifact_id),
        "trace_id": str(state.trace_id),
        "agent_run_id": str(state.agent_run_id),
        "source_type": str(metadata.source_type) if metadata else None,
        "received_at": metadata.received_at.isoformat() if metadata else None,
        "block_kinds": (
            [b.kind.value for b in state.normalized_content.blocks]
            if state.normalized_content
            else []
        ),
    }


def extract_structured_evidence(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Tier E. The only model call on the unambiguous path.

    The semantic validator is handed to the router rather than run afterwards,
    so the router's single repair attempt can be spent on a span mismatch --
    the most common and most repairable Tier E failure -- instead of only on a
    JSON-shape failure.
    """
    node = "extract_structured_evidence"
    content = state.normalized_content
    if content is None:  # pragma: no cover - unreachable through the graph
        return _fail(
            state,
            node,
            code="NO_NORMALIZED_CONTENT",
            detail="extraction reached without content",
            outcome=IngestionOutcome.FAIL_SAFE,
        )

    route = deps.extraction_route
    system = deps.renderer.render_system(route.prompt_version)
    rendered = deps.renderer.render_user(
        trusted_context=_trusted_context(state), blocks=content.blocks
    )
    # Citations are checked against what the model was SHOWN, not against what
    # the parser produced. The two differ whenever the fence scrubber rewrote a
    # block, and the replacement is a different length, so validating against
    # the parser's offsets would fail a scrubbed artifact for spans the model
    # never saw. `rendered_blocks` is empty for a renderer that scrubbed
    # nothing, which is the ordinary case.
    blocks: Sequence[ContentBlock] = rendered.rendered_blocks or content.blocks

    def _validate(result: ExtractionResult) -> Sequence[ValidationFailure]:
        return validate_extraction(result, blocks=blocks, nonce=rendered.nonce)

    outcome = deps.router.invoke(
        node,
        system=system,
        user_text=rendered.user_text,
        schema=ExtractionResult,
        validate=_validate,
    )
    scrub = (*state.fence_scrub_log, *rendered.fence_scrub_log)
    if isinstance(outcome, ModelPending):
        return _fail(
            state,
            node,
            code=outcome.reason_code,
            detail="the router exhausted its budget; the artifact stays pending review",
            outcome=IngestionOutcome.PENDING_HUMAN_REVIEW,
            fence_scrub_log=scrub,
        )
    assert isinstance(outcome, ModelSuccess)
    deps.session.record(run_id=state.agent_run_id, node=node, note="extraction returned")
    return _visit(
        state,
        node,
        extraction_result=outcome.value,
        fence_scrub_log=scrub,
        rendered_blocks=tuple(blocks),
        fence_nonce=rendered.nonce,
        model_routes=(*state.model_routes, outcome.route),
    )


def validate_extraction_schema(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Deterministic. The second opinion the router's own check cannot be.

    The router already ran this check list, so on a well-behaved run this node
    finds nothing. It exists anyway because the router is a dependency the
    graph does not own: a router that silently stopped calling ``validate``
    would otherwise put an unverified span into ``evidence_items``, and nothing
    would say so. Re-running a pure function over data already in hand costs
    microseconds and removes that trust assumption entirely.
    """
    node = "validate_extraction_schema"
    result = state.extraction_result
    content = state.normalized_content
    if result is None or content is None:  # pragma: no cover - unreachable
        return _fail(
            state,
            node,
            code="NO_EXTRACTION_RESULT",
            detail="validation reached without an extraction",
            outcome=IngestionOutcome.FAIL_SAFE,
        )
    # The same blocks and the same live nonce the router was given, so the
    # second opinion is as strong as the first rather than a weaker echo of it.
    failures = validate_extraction(
        result,
        blocks=state.rendered_blocks or content.blocks,
        nonce=state.fence_nonce,
    )
    if failures:
        return _fail(
            state,
            node,
            code=failures[0].code,
            detail="; ".join(f"{f.path}: {f.detail}" for f in failures),
            outcome=IngestionOutcome.FAIL_SAFE,
            extraction_failures=failures,
        )
    deps.session.record(run_id=state.agent_run_id, node=node, note="extraction re-validated")
    return _visit(state, node)


# ---------------------------------------------------------------------------
# 5.5 / 5.6 — evidence registration and retrieval
# ---------------------------------------------------------------------------


def register_or_lookup_evidence(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Deterministic API. Admission means "this text was present", never "this is true".

    Evidence is append-only, so this runs before identity is settled and before
    anything is proposed. An artifact whose interpretation later fails still
    leaves its evidence admitted -- which is the property the injection suite's
    positive control asserts.
    """
    node = "register_or_lookup_evidence"
    result = state.extraction_result
    if result is None:  # pragma: no cover - unreachable
        return _fail(
            state,
            node,
            code="NO_EXTRACTION_RESULT",
            detail="registration reached without an extraction",
            outcome=IngestionOutcome.FAIL_SAFE,
        )
    ids = deps.registrar.register_or_lookup_evidence(
        artifact_id=state.artifact_id, candidates=result.evidence_candidates
    )
    deps.session.record(run_id=state.agent_run_id, node=node, note=f"{len(ids)} evidence ids")
    return _visit(state, node, evidence_ids_by_local_id=dict(ids))


def retrieve_candidate_context(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Deterministic retrieval. Exact identifiers first, vectors advisory.

    ``CANONICAL_DECISIONS.md`` -> *Identity order*: deterministic identity
    signals precede vector similarity and vector output is never canonical
    truth. The spec handed over carries identifier hints for that reason.
    """
    node = "retrieve_candidate_context"
    result = state.extraction_result
    if result is None:  # pragma: no cover - unreachable
        return _fail(
            state,
            node,
            code="NO_EXTRACTION_RESULT",
            detail="retrieval reached without an extraction",
            outcome=IngestionOutcome.FAIL_SAFE,
        )
    spec = RetrievalSpec(
        tenant_id=state.principal_ref.tenant_id,
        user_id=state.principal_ref.user_id,
        artifact_id=state.artifact_id,
        external_identifiers=tuple(i.value for i in result.external_identifiers),
        counterparty_hints=tuple(h.normalized_name for h in result.counterparty_hints),
        evidence_ids=tuple(state.evidence_ids_by_local_id.values()),
        observed_at=state.artifact_metadata.received_at if state.artifact_metadata else None,
    )
    retrieval = deps.retrieval.retrieve_candidate_context(spec)
    context = retrieval.context
    if context.user_id != state.principal_ref.user_id:
        return _fail(
            state,
            node,
            code="RETRIEVAL_FOREIGN_USER",
            detail="retrieval returned a context scoped to another user",
            outcome=IngestionOutcome.REJECTED,
        )
    return _visit(state, node, retrieval=retrieval)


# ---------------------------------------------------------------------------
# 5.7 — the routing predicate's inputs
# ---------------------------------------------------------------------------


def resolution_signals_from(
    *, retrieval: Any, extraction: ExtractionResult | None
) -> ResolutionSignals:
    """Assemble section 5.7's six triggers into one value object.

    Deliberately a free function taking two values rather than a method on the
    state: ``should_resolve`` must be testable without constructing a graph,
    and that is only true if the thing it consumes is constructible on its own.
    """
    context = retrieval.context
    candidates = context.case_candidates
    return ResolutionSignals(
        top_case_score=candidates[0].score if candidates else None,
        identity_margin=context.identity_margin(),
        contradicts_canonical_belief=retrieval.contradicts_canonical_belief,
        validity_interval_ambiguous=retrieval.validity_interval_ambiguous,
        commitment_supersession_possible=retrieval.commitment_supersession_possible,
        blocking_uncertainty=bool(extraction is not None and extraction.blocks_state_change),
        kernel_preflight_requests_resolution=retrieval.kernel_preflight_requests_resolution,
    )


def route_resolution_need(
    state: IngestionGraphState, deps: Any, *, resolve: bool
) -> IngestionGraphState:
    """Record the routing decision. The predicate itself lives in the graph module.

    ``resolve`` is passed in rather than computed here because
    ``70_TASK_PLAN.md`` T7.4 registers ``ingestion_graph.should_resolve`` as a
    ``PV_SABOTAGE`` symbol, and a sabotage only arrives if the call goes through
    that module's global. Computing it here would silently protect the symbol
    from its own gate.
    """
    node = "route_resolution_need"
    flags = set(state.route_flags)
    flags.add("RESOLVER_INVOKED" if resolve else "RESOLVER_SKIPPED")
    deps.session.record(run_id=state.agent_run_id, node=node, note=f"resolve={resolve}")
    return _visit(state, node, route_flags=frozenset(flags))


# ---------------------------------------------------------------------------
# 5.8 — the conditional Tier R resolver
# ---------------------------------------------------------------------------


def strong_resolution(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Tier R, conditional. Advisory output; read-only tools; no mutations.

    ``14_PROMPTS.md`` section 4.1 rule 9: the resolver proposes readings, not
    mutations, and never declares a legal entitlement. That is a property of
    :class:`~provenance_contracts.resolution.ResolutionAssessment`, which
    contains no mutation, no SQL and no entitlement field -- so the guarantee
    survives a model that ignores its prompt.

    Tier R has no weaker-model fallback. A failure here persists pending review
    rather than forcing a low-confidence result onto canonical state.
    """
    node = "strong_resolution"
    route = deps.resolution_route
    system = deps.renderer.render_system(route.prompt_version)
    content = state.normalized_content
    rendered = deps.renderer.render_user(
        trusted_context=_resolver_context(state),
        blocks=content.blocks if content is not None else (),
    )
    outcome = deps.router.invoke(
        node,
        system=system,
        user_text=rendered.user_text,
        schema=ResolutionAssessment,
        validate=None,
    )
    if isinstance(outcome, ModelPending):
        return _fail(
            state,
            node,
            code=outcome.reason_code,
            detail="Tier R does not downgrade; the artifact stays pending human review",
            outcome=IngestionOutcome.PENDING_HUMAN_REVIEW,
        )
    assert isinstance(outcome, ModelSuccess)
    assessment: ResolutionAssessment = outcome.value
    routes = (*state.model_routes, outcome.route)

    # The assessment is well-formed by now; these are the checks its own schema
    # cannot make, because they compare it against the context it was given.
    # An assessment reaching outside that context is never repaired: citing a
    # row the resolver was never shown is not a formatting problem.
    if state.retrieval is not None:
        breaches = guard_assessment(assessment, context=state.retrieval.context)
        if breaches:
            return _fail(
                state,
                node,
                code=breaches[0].code,
                detail="; ".join(f"{f.path}: {f.detail}" for f in breaches),
                outcome=IngestionOutcome.PENDING_HUMAN_REVIEW,
                resolution_assessment=assessment,
                model_routes=routes,
            )

    if assessment.requires_human_review:
        return _fail(
            state,
            node,
            code="RESOLVER_REQUIRES_HUMAN_REVIEW",
            detail=assessment.rationale_summary,
            outcome=IngestionOutcome.PENDING_HUMAN_REVIEW,
            resolution_assessment=assessment,
            model_routes=routes,
        )
    return _visit(state, node, resolution_assessment=assessment, model_routes=routes)


def _resolver_context(state: IngestionGraphState) -> dict[str, Any]:
    """Compact context, never the whole account history (section 5.8)."""
    retrieval = state.retrieval
    context = retrieval.context if retrieval is not None else None
    return {
        "trace_id": str(state.trace_id),
        "agent_run_id": str(state.agent_run_id),
        "case_candidates": (
            [
                {"candidate_id": str(c.candidate_id), "label": c.label, "score": str(c.score)}
                for c in context.case_candidates
            ]
            if context
            else []
        ),
        "relationship_candidates": (
            [
                {"candidate_id": str(c.candidate_id), "label": c.label, "score": str(c.score)}
                for c in context.relationship_candidates
            ]
            if context
            else []
        ),
        "derived_from_untrusted": True,
    }


# ---------------------------------------------------------------------------
# 5.9 — deterministic proposal assembly
# ---------------------------------------------------------------------------


def _identity_for(state: IngestionGraphState, case_id: uuid.UUID | None) -> ProposalIdentity:
    assessment = state.resolution_assessment
    retrieval = state.retrieval
    context = retrieval.context if retrieval is not None else None
    if assessment is not None and assessment.identity.case_id == case_id:
        return ProposalIdentity(
            relationship_id=assessment.identity.relationship_id,
            case_id=case_id,
            confidence=assessment.identity.confidence,
            resolved_by="TIER_R_RESOLVER",
        )
    score = None
    if context is not None:
        for candidate in context.case_candidates:
            if candidate.candidate_id == case_id:
                score = candidate.score
                break
    relationship_id = None
    if context is not None and context.relationship_candidates:
        relationship_id = context.relationship_candidates[0].candidate_id
    return ProposalIdentity(
        relationship_id=relationship_id,
        case_id=case_id,
        confidence=score if score is not None else HUMAN_REVIEW_CONFIDENCE_FLOOR,
        unresolved_candidates=(
            tuple(c.candidate_id for c in context.case_candidates)
            if case_id is None and context is not None
            else ()
        ),
        resolved_by="DETERMINISTIC",
    )


def _case_of_candidate(
    block_id: str, case_by_block: Mapping[str, uuid.UUID], default: uuid.UUID | None
) -> uuid.UUID | None:
    return case_by_block.get(block_id, default)


def _partition(
    state: IngestionGraphState,
) -> dict[uuid.UUID | None, tuple[tuple[ClaimCandidate, ...], tuple[CommitmentCandidate, ...]]]:
    """Group claims and commitments by the case their cited block belongs to.

    ``CANONICAL_DECISIONS.md`` -> *Multi-case artifacts*. One proposal is one
    case, structurally: :class:`~provenance_contracts.proposal.MemoryProposal`
    has exactly one reachable ``case_id``, so a second case has nowhere to live
    even if this function were wrong. This is the deterministic split that keeps
    the Kernel from ever being asked to open a cross-case transaction.
    """
    result = state.extraction_result
    retrieval = state.retrieval
    assert result is not None and retrieval is not None
    case_by_block = dict(retrieval.case_by_block)
    context = retrieval.context
    default_case: uuid.UUID | None = None
    if state.resolution_assessment is not None:
        default_case = state.resolution_assessment.identity.case_id
    elif context.case_candidates:
        default_case = context.case_candidates[0].candidate_id

    evidence_block = {e.local_id: e.block_id for e in result.evidence_candidates}
    claims_by_case: dict[uuid.UUID | None, list[ClaimCandidate]] = {}
    for claim in result.claim_candidates:
        block_id = evidence_block.get(claim.evidence_local_id, "")
        case_id = _case_of_candidate(block_id, case_by_block, default_case)
        claims_by_case.setdefault(case_id, []).append(claim)

    claim_case = {
        claim.local_id: case_id for case_id, claims in claims_by_case.items() for claim in claims
    }
    commitments_by_case: dict[uuid.UUID | None, list[CommitmentCandidate]] = {}
    for commitment in result.commitment_candidates:
        case_id = claim_case.get(commitment.source_claim_local_id, default_case)
        commitments_by_case.setdefault(case_id, []).append(commitment)

    cases: list[uuid.UUID | None] = []
    for case_id in (*claims_by_case, *commitments_by_case):
        if case_id not in cases:
            cases.append(case_id)
    if not cases:
        cases = [default_case]
    return {
        case_id: (
            tuple(claims_by_case.get(case_id, ())),
            tuple(commitments_by_case.get(case_id, ())),
        )
        for case_id in cases
    }


def _proposed_commitment(commitment: CommitmentCandidate) -> ProposedCommitment:
    return ProposedCommitment(
        local_id=commitment.local_id,
        commitment_type=commitment.commitment_type,
        description=commitment.description,
        obligor_type=commitment.obligor_type,
        obligor_ref=commitment.obligor_hint,
        beneficiary_type=commitment.beneficiary_type,
        beneficiary_ref=commitment.beneficiary_hint,
        committed=commitment.money,
        due_at=commitment.due_at,
        source_claim_local_id=commitment.source_claim_local_id,
        confidence=commitment.confidence,
    )


def _idempotency_key(state: IngestionGraphState, case_id: uuid.UUID | None) -> str:
    metadata = state.artifact_metadata
    digest = hashlib.sha256(
        "|".join(
            [
                metadata.content_sha256 if metadata else str(state.artifact_id),
                state.graph_name,
                state.graph_version,
                str(case_id),
            ]
        ).encode()
    ).hexdigest()
    return f"pv-ingest-{digest[:40]}"


def build_memory_proposal(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Deterministic assembly. The model never constructs a persistence command.

    Every field here comes from a validated extraction, a deterministic
    retrieval result, or an advisory assessment whose advisory flag is a typed
    field. Nothing is copied out of free text.
    """
    node = "build_memory_proposal"
    result = state.extraction_result
    retrieval = state.retrieval
    if result is None or retrieval is None:  # pragma: no cover - unreachable
        return _fail(
            state,
            node,
            code="NO_EXTRACTION_RESULT",
            detail="proposal assembly reached without an extraction",
            outcome=IngestionOutcome.FAIL_SAFE,
        )

    evidence_ids = state.evidence_ids_by_local_id
    source_class_by_local_id = {e.local_id: e.source_class for e in result.evidence_candidates}
    declared = tuple(dict.fromkeys(evidence_ids.values()))
    attribution = deps.extraction_route.attribution(
        graph_name=state.graph_name,
        graph_version=state.graph_version,
        extraction_schema_version=result.extraction_schema_version,
    )
    now = deps.clock()

    proposals: list[MemoryProposal] = []
    for case_id, (claims, commitments) in _partition(state).items():
        proposed_claims = tuple(
            ProposedClaim(
                local_id=claim.local_id,
                claim_kind=claim.claim_kind,
                subject_type=claim.subject_type,
                subject_local_ref=claim.subject_hint,
                predicate=claim.predicate,
                object_type=claim.object_type,
                object_value=claim.object_value,
                actor_type=claim.actor_type,
                actor_ref=claim.actor_hint,
                evidence_id=evidence_ids[claim.evidence_local_id],
                source_class=source_class_by_local_id[claim.evidence_local_id],
                modality=claim.modality,
                valid_from=claim.valid_from,
                valid_to=claim.valid_to,
                extraction_confidence=claim.extraction_confidence,
            )
            for claim in claims
            if claim.evidence_local_id in evidence_ids
        )
        proposed_commitments = tuple(_proposed_commitment(c) for c in commitments)
        if not proposed_claims and not proposed_commitments:
            continue
        proposals.append(
            MemoryProposal(
                proposal_id=uuid.uuid5(PROPOSAL_NAMESPACE, f"{state.agent_run_id}:{case_id}"),
                proposal_type=ProposalType.INGESTION_INTERPRETATION,
                trace_id=state.trace_id,
                agent_run_id=state.agent_run_id,
                user_id=state.principal_ref.user_id,
                source_artifact_ids=(state.artifact_id,),
                evidence_ids=declared,
                identity=_identity_for(state, case_id),
                claims=proposed_claims,
                commitments=proposed_commitments,
                unresolved_questions=(
                    state.resolution_assessment.unresolved_questions
                    if state.resolution_assessment is not None
                    else ()
                ),
                blocks_state_change=result.blocks_state_change,
                model=attribution,
                idempotency_key=_idempotency_key(state, case_id),
                created_at=now,
            )
        )

    if not proposals:
        return _visit(state, node, outcome=IngestionOutcome.NOOP)
    deps.session.record(run_id=state.agent_run_id, node=node, note=f"{len(proposals)} proposal(s)")
    return _visit(state, node, memory_proposals=tuple(proposals))


# ---------------------------------------------------------------------------
# 5.10 — submission and commit routing
# ---------------------------------------------------------------------------


def submit_to_memory_kernel(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """One typed proposal per call. The Kernel is the only canonical writer.

    A multi-case artifact submits several times; it never submits once with two
    cases in it. The loop is the whole reason the Kernel never has to open a
    cross-case transaction for one proposal.
    """
    node = "submit_to_memory_kernel"
    results: list[KernelCommitResult] = []
    for proposal in state.memory_proposals:
        results.append(deps.kernel.submit_memory_proposal(proposal))
    deps.session.record(run_id=state.agent_run_id, node=node, note=f"{len(results)} receipt(s)")
    return _visit(state, node, kernel_results=tuple(results))


def route_commit_result(state: IngestionGraphState, deps: Any) -> IngestionGraphState:
    """Terminal routing. Every path ends with a status the user can see.

    The advocate is signalled only for a commit that is both accepted and
    user-impacting. A commit that changed nothing a person would want to know
    about is a successful run that produces no notification, which is the
    behaviour the attention prompt's "NONE is a normal answer" rule assumes.
    """
    node = "route_commit_result"
    results = state.kernel_results
    if not results:
        return _visit(state, node, outcome=IngestionOutcome.NOOP)

    signals = tuple(r.case_id for r in results if r.should_wake_advocate and r.case_id is not None)
    if any(r.decision is KernelDecision.PENDING_IDENTITY for r in results):
        outcome = IngestionOutcome.PENDING_IDENTITY
    elif any(r.decision is KernelDecision.PENDING_HUMAN_REVIEW for r in results):
        outcome = IngestionOutcome.PENDING_HUMAN_REVIEW
    elif any(r.decision.value.startswith("REJECTED") for r in results):
        outcome = IngestionOutcome.REJECTED
    elif all(r.decision is KernelDecision.NOOP_DUPLICATE for r in results):
        outcome = IngestionOutcome.NOOP
    elif any(r.is_accepted for r in results):
        outcome = IngestionOutcome.COMMITTED
    else:
        outcome = IngestionOutcome.PENDING_HUMAN_REVIEW

    deps.session.record(run_id=state.agent_run_id, node=node, note=str(outcome))
    return _visit(state, node, advocate_signals=signals, outcome=outcome)
