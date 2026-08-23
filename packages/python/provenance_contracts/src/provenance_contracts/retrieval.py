"""Deterministic retrieval output. Bounded by construction.

"Never send all history to the model" is enforced with ``Field(max_length=)``
rather than a comment: three relationship candidates, three case candidates,
ten evidence snippets. If a caller wants more, it must change this file and
justify it in review.

Authority
---------
- ``specs/11_CONTRACTS.md`` sections 9 and 9.1, whose code this module
  implements. Caps come from ``02_DATA_MEMORY_TRANSACTIONS.md`` section 15.5.
- ``CANONICAL_DECISIONS.md`` -> *Evidence and retrieval*: only ``ACTIVE``
  evidence may enter new retrieval or ground a new belief, and superseded
  evidence is **excluded** rather than down-weighted.
- ``EXECUTION/70_TASK_PLAN.md`` T1.5, fourth sub-task.

Canonical Addition C — the retraction guard, in four legs
----------------------------------------------------------
Retracted and superseded evidence keeps its row **and its embedding**, because
that is what makes lineage auditable. ANN search will therefore happily return
a correction the user already made, forever. Four defences are stacked:

1. :attr:`EvidenceSnippet.retraction_status` is ``Literal[ACTIVE]`` — the
   mistake is unrepresentable at the leaf.
2. :meth:`RetrievalContext._reject_retracted_evidence` re-checks each snippet,
   so an item assembled by a path that skipped field validation
   (``model_construct``, a repository row mapped straight onto the model,
   a payload deserialised by a future build) still cannot be placed in a
   context object. This is the leg ``EXECUTION/70_TASK_PLAN.md`` T1.5 asks for
   by name — "``RetrievalContext`` refuses to hold an evidence item whose
   ``retraction_status`` is not ``ACTIVE``" — and it is not redundant with leg
   1, because pydantic does not re-validate an already-constructed model when
   it is assigned to a field of another model.
3. :attr:`VectorSearchParams.retraction_filter_applied` is ``Literal[True]`` —
   a result cannot claim an unfiltered search was filtered.
4. :attr:`RetrievalDebug.candidates_filtered_by_retraction` records how many
   were dropped, so the number can be asserted in an evaluation.

The first three make the mistake unrepresentable; the fourth makes it
observable when someone changes the query and the count silently goes to zero.

Recorded deviation from ``specs/11_CONTRACTS.md`` section 9
-------------------------------------------------------------
``EvidenceSnippet``, ``CanonicalBeliefSummary`` and ``TemporalFact`` carry the
``[valid_from, valid_to)`` pair; section 9 checks the ordering on none of them.
All three call :func:`provenance_contracts.base.validate_half_open` here, per
``EXECUTION/70_TASK_PLAN.md`` T1.5's first sub-task, which puts the rule in the
base scalar rather than in whichever models happened to get a validator.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Confidence,
    Contract,
    Money,
    SafeIdentifier,
    UtcDatetime,
    validate_half_open,
)
from provenance_contracts.ingestion import SourceLocator
from provenance_domain.enums import (
    AgentSafeView,
    CaseStatus,
    CommitmentStatus,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    EvidenceType,
    IdentityCandidateKind,
    RelationshipStatus,
    RetractionStatus,
    SubjectType,
)

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_MODEL_ID",
    "EMBEDDING_VERSION",
    "ActiveCommitmentSummary",
    "ActiveConflictSummary",
    "CanonicalBeliefSummary",
    "EvidenceSnippet",
    "IdentityCandidate",
    "MatchSignal",
    "McpToolCall",
    "RetrievalContext",
    "RetrievalDebug",
    "TemporalFact",
    "VectorSearchParams",
]

#: Frozen. One embedding version is active for the primary vector index.
#:
#: Declared ``Final`` rather than ``: str`` / ``: int`` as section 9 prints them,
#: for one mechanical reason: ``VectorSearchParams.model_id`` and ``.dimensions``
#: are ``Literal`` types that take these as their defaults, and a ``str``-typed
#: constant is not assignable to a ``Literal`` under ``mypy --strict``. ``Final``
#: narrows the inferred type to the literal, which is what the constant already
#: meant. Runtime behaviour is identical.
EMBEDDING_MODEL_ID: Final = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS: Final = 1024
EMBEDDING_VERSION: Final = "v1"

Snippet = Annotated[str, StringConstraints(min_length=1, max_length=1200)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class MatchSignal(Contract):
    """One deterministic reason a candidate scored the way it did.

    Signals are computed by relational validation
    (``02_DATA_MEMORY_TRANSACTIONS.md`` section 15.3), never by a model. They
    are what makes an identity score explainable in the Memory Trace instead of
    being an unexplained number.
    """

    signal: Literal[
        "EXACT_EXTERNAL_REFERENCE",
        "SENDER_DOMAIN_MATCH",
        "THREAD_ID_MATCH",
        "SERVICE_ADDRESS_MATCH",
        "AMOUNT_CONSISTENT",
        "TEMPORAL_OVERLAP",
        "RELATIONSHIP_ACTIVE",
        "CASE_RECENTLY_ACTIVE",
        "USER_CONFIRMED_MAPPING",
        "VECTOR_SIMILARITY",
        "COUNTERPARTY_NAME_SIMILARITY",
    ]
    matched: bool
    weight: Confidence
    detail: Annotated[str, StringConstraints(max_length=300)] | None = None


class IdentityCandidate(BoundaryContract):
    """A relationship or case this artifact might belong to.

    ``score`` is deterministic: it is a weighted sum of ``signals``, not a model
    opinion. ``route_resolution_need`` compares it against the configured
    thresholds in ``provenance_domain.invariants`` (0.90 top-1, 0.15 margin) to
    decide whether the Tier R resolver runs at all.
    """

    candidate_kind: IdentityCandidateKind
    candidate_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    label: Label
    counterparty_name: Label | None = None
    relationship_status: RelationshipStatus | None = None
    case_status: CaseStatus | None = None
    last_activity_at: UtcDatetime | None = None
    score: Confidence
    signals: tuple[MatchSignal, ...] = Field(default=(), max_length=16)
    reasons: tuple[Annotated[str, StringConstraints(max_length=300)], ...] = Field(
        default=(), max_length=8
    )

    @model_validator(mode="after")
    def _kind_matches_status(self) -> IdentityCandidate:
        if self.candidate_kind is IdentityCandidateKind.CASE and self.case_status is None:
            raise ValueError("a CASE candidate must carry case_status")
        if (
            self.candidate_kind is IdentityCandidateKind.RELATIONSHIP
            and self.relationship_status is None
        ):
            raise ValueError("a RELATIONSHIP candidate must carry relationship_status")
        return self


class EvidenceSnippet(Contract):
    """A retrieved evidence item, already filtered for retraction.

    Addition C, leg 1. A retracted or superseded evidence item keeps its row and
    its embedding in the vector index — that is what makes lineage auditable —
    so ANN search will happily return it. ``retraction_status`` is therefore
    pinned to ACTIVE by the type: if a retrieval path forgets its
    ``retraction_status = 'ACTIVE'`` predicate, the resulting context fails
    validation before it ever reaches a prompt, rather than quietly resurfacing
    a correction the user already made.
    """

    evidence_id: uuid.UUID
    artifact_id: uuid.UUID
    evidence_type: EvidenceType
    normalized_text: Snippet
    source_locator: SourceLocator
    observed_at: UtcDatetime
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    source_authority: Confidence | None = None
    retraction_status: Literal[RetractionStatus.ACTIVE] = RetractionStatus.ACTIVE
    similarity: Confidence | None = None
    retrieved_by: tuple[Literal["VECTOR", "EXACT_MATCH", "GRAPH_EXPANSION"], ...] = ()

    @model_validator(mode="after")
    def _validate_validity_window(self) -> EvidenceSnippet:
        validate_half_open(self.valid_from, self.valid_to)
        return self


class CanonicalBeliefSummary(Contract):
    """What Provenance currently holds. Trusted context, not evidence."""

    belief_id: uuid.UUID
    belief_version_id: uuid.UUID
    version_no: Annotated[int, Field(ge=1)]
    subject_type: SubjectType
    subject_id: uuid.UUID
    predicate: SafeIdentifier
    value_summary: Snippet
    epistemic_status: EpistemicStatus
    belief_confidence: Confidence
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    support_edge_count: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _validate_validity_window(self) -> CanonicalBeliefSummary:
        validate_half_open(self.valid_from, self.valid_to)
        return self


class ActiveConflictSummary(Contract):
    conflict_id: uuid.UUID
    case_id: uuid.UUID
    conflict_type: ConflictType
    status: ConflictStatus
    predicate: SafeIdentifier
    summary: Snippet
    requires_human: bool
    detected_at: UtcDatetime


class ActiveCommitmentSummary(Contract):
    commitment_id: uuid.UUID
    case_id: uuid.UUID
    description: Snippet
    status: CommitmentStatus
    committed: Money | None = None
    fulfilled: Money | None = None
    outstanding: Money | None = None
    due_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _currencies_agree(self) -> ActiveCommitmentSummary:
        present = [m for m in (self.committed, self.fulfilled, self.outstanding) if m]
        if present and len({m.currency for m in present}) > 1:
            raise ValueError(
                "committed/fulfilled/outstanding must share one currency; "
                "cross-currency aggregation requires an explicit conversion event"
            )
        return self


class TemporalFact(Contract):
    """A dated anchor the resolver needs in order to reason about overlap."""

    label: Label
    predicate: SafeIdentifier
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    recorded_at: UtcDatetime
    source_evidence_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _validate_validity_window(self) -> TemporalFact:
        validate_half_open(self.valid_from, self.valid_to)
        return self


class McpToolCall(Contract):
    """Addition B. One CockroachDB MCP read, surfaced rather than hidden.

    The MCP server is read-only and SQL grants are the real permission
    boundary: the connection authenticates as ``pv_agent_reader``, which holds
    SELECT on the agent-safe views only. Recording the view name, the row count
    and the role in the contract means the Memory Trace can show a judge
    exactly which governed surface the agent touched, and the ``db_role``
    literal means a trace claiming a write role fails validation.

    ``arguments_digest`` rather than raw arguments: query parameters can echo
    document text, and raw document contents never enter logs or traces.
    """

    server: Literal["cockroachdb-mcp"] = "cockroachdb-mcp"
    tool_name: Annotated[str, StringConstraints(max_length=64)]
    view: AgentSafeView
    db_role: Literal["pv_agent_reader"] = "pv_agent_reader"
    arguments_digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    row_count: Annotated[int, Field(ge=0, le=10_000)]
    latency_ms: Annotated[int, Field(ge=0, le=600_000)]
    started_at: UtcDatetime
    truncated: bool = False


class VectorSearchParams(Contract):
    """The exact ANN parameters used, frozen into the trace.

    ``user_prefix_applied`` is a ``Literal[True]``: the vector index is defined
    as ``(user_id, embedding vector_cosine_ops)`` so ANN search cannot cross
    users, and a retrieval result that claims otherwise cannot be constructed.
    """

    model_id: Literal["amazon.titan-embed-text-v2:0"] = EMBEDDING_MODEL_ID
    dimensions: Literal[1024] = EMBEDDING_DIMENSIONS
    embedding_version: Annotated[str, StringConstraints(max_length=64)] = EMBEDDING_VERSION
    distance: Literal["cosine"] = "cosine"
    top_k: Annotated[int, Field(ge=1, le=200)] = 20
    rerank_to: Annotated[int, Field(ge=1, le=50)] = 10
    beam_size: Annotated[int, Field(ge=1, le=512)] | None = None
    user_prefix_applied: Literal[True] = True
    retraction_filter_applied: Literal[True] = True


class RetrievalDebug(Contract):
    """Everything a judge needs to believe the retrieval step."""

    deterministic_hints: tuple[Annotated[str, StringConstraints(max_length=200)], ...] = Field(
        default=(), max_length=30
    )
    vector_search: VectorSearchParams | None = None
    mcp_tool_calls: tuple[McpToolCall, ...] = Field(default=(), max_length=20)
    candidates_considered: Annotated[int, Field(ge=0)] = 0
    candidates_filtered_by_retraction: Annotated[int, Field(ge=0)] = 0
    elapsed_ms: Annotated[int, Field(ge=0)] = 0


class RetrievalContext(BoundaryContract):
    """The bounded memory package handed to the Interpreter and the resolver.

    Caps come straight from ``02_DATA_MEMORY_TRANSACTIONS.md`` section 15.5 and
    are enforced here so no node can widen them at call time.
    """

    trace_id: uuid.UUID
    agent_run_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    artifact_id: uuid.UUID | None = None

    relationship_candidates: tuple[IdentityCandidate, ...] = Field(default=(), max_length=3)
    case_candidates: tuple[IdentityCandidate, ...] = Field(default=(), max_length=3)
    canonical_beliefs: tuple[CanonicalBeliefSummary, ...] = Field(default=(), max_length=25)
    evidence_snippets: tuple[EvidenceSnippet, ...] = Field(default=(), max_length=10)
    active_conflicts: tuple[ActiveConflictSummary, ...] = Field(default=(), max_length=10)
    active_commitments: tuple[ActiveCommitmentSummary, ...] = Field(default=(), max_length=15)
    temporal_facts: tuple[TemporalFact, ...] = Field(default=(), max_length=20)
    unresolved_identity_questions: tuple[Annotated[str, StringConstraints(max_length=300)], ...] = (
        Field(default=(), max_length=8)
    )

    debug: RetrievalDebug = RetrievalDebug()
    retrieved_at: UtcDatetime

    @model_validator(mode="after")
    def _reject_retracted_evidence(self) -> RetrievalContext:
        """Addition C, leg 2 — the filter as a validator, not as a query comment.

        ``EvidenceSnippet.retraction_status`` is already ``Literal[ACTIVE]``, so
        a snippet built through normal validation cannot be anything else. This
        exists because that is not the only way a snippet reaches this field:
        ``model_construct`` skips field validation outright, and pydantic does
        not re-validate an already-constructed model assigned to a field of
        another model. A repository that maps a row onto ``EvidenceSnippet``
        without validating — the exact shortcut a performance-minded change
        would make — would otherwise put a retracted item into a prompt.
        """
        offenders = [
            (str(snippet.evidence_id), str(snippet.retraction_status))
            for snippet in self.evidence_snippets
            if snippet.retraction_status is not RetractionStatus.ACTIVE
        ]
        if offenders:
            raise ValueError(
                f"evidence {sorted(offenders)} is not ACTIVE and may not enter a retrieval "
                "context; retracted and superseded evidence keeps its embedding, so ANN "
                "search returns it, and only the retraction filter keeps a correction the "
                "user already made from being resurfaced in a prompt"
            )
        return self

    @model_validator(mode="after")
    def _validate_scope_and_kinds(self) -> RetrievalContext:
        for cand in self.relationship_candidates:
            if cand.candidate_kind is not IdentityCandidateKind.RELATIONSHIP:
                raise ValueError("relationship_candidates must hold RELATIONSHIP kinds")
        for cand in self.case_candidates:
            if cand.candidate_kind is not IdentityCandidateKind.CASE:
                raise ValueError("case_candidates must hold CASE kinds")
        foreign = [
            str(c.candidate_id)
            for c in (*self.relationship_candidates, *self.case_candidates)
            if c.user_id != self.user_id or c.tenant_id != self.tenant_id
        ]
        if foreign:
            raise ValueError(
                f"candidates {foreign} belong to another user; "
                "retrieval is scoped by user prefix and must never cross that line"
            )
        return self

    def top_case_candidate(self) -> IdentityCandidate | None:
        return self.case_candidates[0] if self.case_candidates else None

    def identity_margin(self) -> Confidence | None:
        """Difference between the top two case candidates.

        ``route_resolution_need`` invokes the Tier R resolver when this is below
        the configured 0.15 margin, or when the top score is below 0.90. Both
        thresholds are configuration, never prompt text.
        """
        if len(self.case_candidates) < 2:
            return None
        return self.case_candidates[0].score - self.case_candidates[1].score
