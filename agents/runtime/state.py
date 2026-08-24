"""Typed graph state, and the narrow protocols the graphs reach the world through.

Authority
---------
- ``docs/implementation/03_AGENTS_LANGGRAPH_CONTRACTS.md`` sections 1, 3, 5, 16
  and 17. Section 3 fixes the state shape; section 17 fixes the tool surface.
- ``docs/CANONICAL_DECISIONS.md`` -> *Gemini model id canon (frozen 2026-08-24)*,
  *Multi-case artifacts*, *Canonical writer*.
- ``docs/EXECUTION/70_TASK_PLAN.md`` T7.3, T7.4, T7.5.

Agents propose. They never write canonical state.
----------------------------------------------------
A graph may inspect an artifact, retrieve context, reason over ambiguity,
produce a typed :class:`~provenance_contracts.proposal.MemoryProposal`, and
draft an action. A graph may **not** mutate canonical beliefs, commitments or
case state, call arbitrary SQL for writes, execute an external action without
deterministic authorization, or treat its own orchestrator storage as
Provenance memory.

That last prohibition is restated here because this stack is new and the
prohibition is load-bearing. ``03_AGENTS_LANGGRAPH_CONTRACTS.md`` section 16
forbids "LangGraph store as product state". **The Google GenAI SDK and the ADK
ship their own session, chat-history and memory abstractions, and those are
equally forbidden as product state.** Session storage here is workflow
durability only: a place to note that a node ran so a crashed run can be
resumed or debugged. If canonical truth is ever read back out of an SDK
session rather than out of the database, the single-canonical-writer guarantee
is gone and nothing in the system will report it — a graph reading a stale
belief from a chat transcript looks exactly like a graph reading a fresh one.

The prohibition is therefore expressed structurally rather than as a rule:

* :class:`SessionRecorder` has exactly one method, ``record``, and it returns
  ``None``. There is no reader on the protocol, so "read product state from the
  session" is not a call that type-checks.
* Every business input arrives through a protocol that names its real source —
  :class:`ArtifactReader`, :class:`RetrievalService`, :class:`StateProofReader`,
  :class:`MemoryKernelClient`.
* ``agents/runtime/tests/test_no_write_tools.py`` walks the AST of every graph
  and node module and fails on any attribute access against the session object
  other than ``record``, and on any import of an SDK session/memory symbol.

Why protocols rather than concrete clients
------------------------------------------
Every dependency is a :class:`typing.Protocol` defined here and satisfied
structurally. Three consequences, in descending order of importance:

1. The tool surface is readable in one file. ``17.`` of the contracts document
   says there is no agent tool called ``update_belief``, ``resolve_case`` or
   ``send_email``; here that is checkable by reading forty lines rather than by
   auditing a client library.
2. The graphs are testable with no API key, no socket and no database.
3. The concrete Gemini router, the control-plane clients and the prompt
   renderer are owned by other modules and can change shape without touching a
   graph.

Recorded deviation from section 3
---------------------------------
Section 3 prints ``principal_ref: InternalPrincipalRef`` and
``memory_proposal: MemoryProposal | null``. Two changes, both forced by
documents that outrank it:

* ``InternalPrincipalRef`` is not a type that exists.
  :class:`~provenance_contracts.identity.CapabilityBinding` is the
  server-resolved ownership record and is what the run actually holds. It
  carries ``tenant_id`` and ``user_id`` and no token, which is what section 3's
  own rule -- "do not put database secrets/raw auth tokens into graph state" --
  is asking for.
* ``memory_proposal`` is singular, which contradicts
  ``CANONICAL_DECISIONS.md`` -> *Multi-case artifacts*: "Ingestion splits one
  artifact into independently traceable, one-case ``MemoryProposal`` objects".
  The register outranks the implementation note, so the field is
  ``memory_proposals`` and is a tuple. No node was added to accommodate it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, Generic, Literal, Protocol, TypeVar, runtime_checkable

from agents.runtime.schemas.advocacy import AdvocacyContext, AttentionAssessment
from agents.runtime.schemas.validation import ValidationFailure
from provenance_contracts.actions import DraftAction
from provenance_contracts.identity import CapabilityBinding
from provenance_contracts.ingestion import (
    ArtifactMetadata,
    ContentBlock,
    EvidenceCandidate,
    ExtractionResult,
    NormalizedContent,
)
from provenance_contracts.kernel import KernelCommitResult
from provenance_contracts.proof import StateProof
from provenance_contracts.proposal import MemoryProposal
from provenance_contracts.resolution import ModelAttribution, ResolutionAssessment
from provenance_contracts.retrieval import RetrievalContext
from provenance_domain.enums import ActionState, ActionType, ModelTier

__all__ = [
    "ADVOCATE_NODES",
    "GRAPH_NAME_ADVOCATE",
    "GRAPH_NAME_INGESTION",
    "GRAPH_VERSION_ADVOCATE",
    "GRAPH_VERSION_INGESTION",
    "INGESTION_NODES",
    "ActionIntentReceipt",
    "ActionIntentWriter",
    "AdvocateGraphState",
    "AdvocateOutcome",
    "ArtifactReader",
    "EvidenceRegistrar",
    "FenceScrubEntry",
    "GraphError",
    "IngestionGraphState",
    "IngestionOutcome",
    "MemoryKernelClient",
    "ModelPending",
    "ModelRoute",
    "ModelRouter",
    "ModelSuccess",
    "PromptRenderer",
    "RenderedPrompt",
    "ResolutionSignals",
    "RetrievalResult",
    "RetrievalService",
    "RetrievalSpec",
    "SessionRecorder",
    "StateProofReader",
    "initial_advocate_state",
    "initial_ingestion_state",
]

T = TypeVar("T")

# These are the values `ck_agent_runs_graph` (migration 0008) admits:
# 'ingestion' | 'advocate' | 'resolver' | 'counterfactual'. They read
# "ingestion_graph" and "advocate_graph" until 2026-08-24 and had never
# agreed with the schema -- the constants were defined, exported,
# type-checked and used as dataclass defaults, and every one of those steps
# is satisfied by a string the database rejects. Nothing noticed because
# nothing had ever written an agent_runs row; the first INSERT would have
# failed on the CHECK at the END of a live model call, after the tokens
# were spent. Pinned by tests/test_graph_names_match_the_schema.py, which
# parses the migration rather than restating the list.
GRAPH_NAME_INGESTION: Final[str] = "ingestion"
GRAPH_VERSION_INGESTION: Final[str] = "1.0.0"
GRAPH_NAME_ADVOCATE: Final[str] = "advocate"
GRAPH_VERSION_ADVOCATE: Final[str] = "1.0.0"

#: ``03_AGENTS_LANGGRAPH_CONTRACTS.md`` section 4, in order. This tuple is the
#: topology: the graph walks it, the tests assert on it, and adding an entry is
#: a visible change to a constant rather than a quiet change to control flow.
INGESTION_NODES: Final[tuple[str, ...]] = (
    "load_artifact_metadata",
    "load_normalized_content",
    "extract_structured_evidence",
    "validate_extraction_schema",
    "register_or_lookup_evidence",
    "retrieve_candidate_context",
    "route_resolution_need",
    "strong_resolution",
    "build_memory_proposal",
    "submit_to_memory_kernel",
    "route_commit_result",
)

#: ``03_AGENTS_LANGGRAPH_CONTRACTS.md`` section 6, in order.
ADVOCATE_NODES: Final[tuple[str, ...]] = (
    "load_state_proof",
    "classify_attention_need",
    "select_action_template",
    "draft_action",
    "validate_draft_claims",
    "create_action_intent",
)


# ---------------------------------------------------------------------------
# Small value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphError:
    """One recorded failure. Never raised past the graph boundary.

    A graph that raises loses the visit order, the partial state and the reason
    code, which are the three things a pending-review row needs in order to be
    actionable. ``PENDING_REVIEW`` is a state, not an exception that unwinds.
    """

    node: str
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class FenceScrubEntry:
    """One untrusted-evidence fence the renderer had to scrub.

    ``14_PROMPTS.md`` section 2.2: a document that contains the fence pattern is
    trying to close the fence early. The substitution is deterministic and the
    attempt becomes a first-class Memory Trace row rather than a silent fix.
    """

    block_id: str
    classification: str
    substitutions: int


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """The user-message half of one invocation. Never the ``system`` half.

    ``rendered_blocks`` is the blocks **as fenced**, which is not always the
    blocks as parsed: the scrubber in ``14_PROMPTS.md`` section 2.2 rewrites any
    text matching the fence pattern before render, and the replacement is a
    different length. Span citations must therefore be checked against these
    blocks rather than against the parser's, or a scrubbed artifact fails
    ``SPAN_TEXT_MISMATCH`` for offsets the model never saw. The parser's blocks
    and the artifact bytes are untouched; only what the model was shown is
    recorded here.
    """

    user_text: str
    nonce: str
    fence_scrub_log: tuple[FenceScrubEntry, ...] = ()
    rendered_blocks: tuple[ContentBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """Which model served a node, under which prompt version.

    ``CANONICAL_DECISIONS.md`` -> *Router obligation*: ids are read from
    configuration and the id **actually used** is recorded, so every run is
    attributable to the model that served it. This object is that record on its
    way to :class:`~provenance_contracts.resolution.ModelAttribution`.
    """

    #: Narrowed to the same ``Literal`` as ``ModelAttribution.provider``, and
    #: deliberately **not** widened to ``str``.
    #:
    #: ``ModelAttribution.provider`` defaults to ``"bedrock"``, not to the
    #: shipping provider, and its id-shape check dispatches on that field. A
    #: Gemini id under the Bedrock branch raises at construction -- loud, but at
    #: run time, and only if that code path is exercised. Typing this field as
    #: ``str`` and silencing the assignment with ``# type: ignore[arg-type]``
    #: (which is what this used to do) moved the guarantee from "mypy checks it"
    #: to "someone audited the call sites once". Narrowing it puts every
    #: construction site back under the type checker, so a typo like
    #: ``"google-genai"`` or ``"Gemini"`` fails ``mypy --strict`` rather than
    #: waiting for a pydantic validator at run time.
    provider: Literal["bedrock", "gemini"]
    model_id: str
    tier: ModelTier
    prompt_version: str

    def attribution(
        self,
        *,
        graph_name: str,
        graph_version: str,
        extraction_schema_version: str | None = None,
    ) -> ModelAttribution:
        return ModelAttribution(
            provider=self.provider,
            model_id=self.model_id,
            tier=self.tier,
            prompt_version=self.prompt_version,
            graph_name=graph_name,
            graph_version=graph_version,
            extraction_schema_version=extraction_schema_version,
        )


@dataclass(frozen=True, slots=True)
class ModelSuccess(Generic[T]):
    """A validated structured output, plus the route that produced it.

    Declared with the classic ``Generic[T]`` syntax rather than PEP 695's
    ``class ModelSuccess[T]``: the pinned mypy refuses the new spelling without
    an experimental flag, and a type-checker flag is a worse thing to add to a
    shared config than four extra characters here.
    """

    value: T
    route: ModelRoute
    repaired: bool = False


@dataclass(frozen=True, slots=True)
class ModelPending:
    """The router gave up inside its budget. A state, not an exception.

    ``14_PROMPTS.md`` section 7.2: after the single repair attempt fails, the
    artifact is marked ``PENDING_HUMAN_REVIEW`` and no canonical state is
    mutated. Failing is always cheaper than committing a guess.
    """

    reason_code: str
    node: str


@dataclass(frozen=True, slots=True)
class ResolutionSignals:
    """Everything ``route_resolution_need`` is allowed to read.

    Assembled deterministically from the retrieval result and the extraction,
    so ``should_resolve`` is a pure function of a value object and is testable
    without running the graph (``70_TASK_PLAN.md`` T7.4, first sub-task).
    """

    top_case_score: Decimal | None
    identity_margin: Decimal | None
    contradicts_canonical_belief: bool
    validity_interval_ambiguous: bool
    commitment_supersession_possible: bool
    blocking_uncertainty: bool
    kernel_preflight_requests_resolution: bool


@dataclass(frozen=True, slots=True)
class RetrievalSpec:
    """The deterministic retrieval request. Carries hints, never raw content."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    artifact_id: uuid.UUID
    external_identifiers: tuple[str, ...] = ()
    counterparty_hints: tuple[str, ...] = ()
    evidence_ids: tuple[uuid.UUID, ...] = ()
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """What the deterministic retrieval service returns.

    ``context`` is the bounded package handed to a model.  The remaining fields
    are deterministic findings that ``route_resolution_need`` consumes; they are
    computed by relational validation, never by a model, which is what keeps the
    resolver decision explainable.

    ``case_by_block`` is how a multi-case artifact becomes several one-case
    proposals: exact-identifier matching assigns each content block to at most
    one case, and the proposal builder partitions on that map. An artifact whose
    blocks all map to one case (or to none) produces exactly one proposal.
    """

    context: RetrievalContext
    case_by_block: Mapping[str, uuid.UUID] = field(default_factory=dict)
    contradicts_canonical_belief: bool = False
    validity_interval_ambiguous: bool = False
    commitment_supersession_possible: bool = False
    kernel_preflight_requests_resolution: bool = False


@dataclass(frozen=True, slots=True)
class ActionIntentReceipt:
    """What the control plane returns when an intent row is created.

    The agent never approves and never sends. ``status`` is ``PROPOSED`` or
    ``NEEDS_REVIEW``; the approval transition is a database transition
    authenticated as the user, not a graph resume.
    """

    action_intent_id: uuid.UUID
    status: ActionState
    warnings: tuple[str, ...] = ()


class IngestionOutcome(StrEnum):
    """Terminal status of one ingestion run, always visible."""

    IN_PROGRESS = "IN_PROGRESS"
    COMMITTED = "COMMITTED"
    NOOP = "NOOP"
    PENDING_IDENTITY = "PENDING_IDENTITY"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    REJECTED = "REJECTED"
    FAIL_SAFE = "FAIL_SAFE"


class AdvocateOutcome(StrEnum):
    """Terminal status of one advocate run."""

    IN_PROGRESS = "IN_PROGRESS"
    NO_ATTENTION = "NO_ATTENTION"
    INTENT_CREATED = "INTENT_CREATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    FAIL_SAFE = "FAIL_SAFE"


# ---------------------------------------------------------------------------
# The tool surface — every protocol the graphs are allowed to reach
# ---------------------------------------------------------------------------


@runtime_checkable
class PromptRenderer(Protocol):
    """Deterministic prompt assembly.

    ``render_system`` takes no artifact argument, by design: that signature is
    the mechanism behind "never concatenate external document text into system
    instructions" (``14_PROMPTS.md`` section 2.1). A reviewer verifies
    containment by reading one signature rather than by auditing a call site.
    """

    def render_system(self, prompt_version: str) -> str: ...

    def render_user(
        self,
        *,
        trusted_context: Mapping[str, Any],
        blocks: Sequence[ContentBlock],
    ) -> RenderedPrompt: ...


@runtime_checkable
class ModelRouter(Protocol):
    """The only way a graph reaches a model.

    The repair and fallback budgets live behind this call, not in the nodes:
    ``70_TASK_PLAN.md`` T7.6 -- "count calls per node but enforce the budget in
    the router, not in each node. A per-node budget is a budget that three
    places can disagree about."

    ``validate`` carries the semantic checks the JSON Schema layer cannot
    express (span-substring equality, referential integrity, sentence copying).
    Passing it here rather than running it after the call is what lets the
    router spend its single repair attempt on a semantic failure.
    """

    def invoke(
        self,
        node: str,
        *,
        system: str,
        user_text: str,
        schema: type[T],
        validate: Callable[[T], Sequence[ValidationFailure]] | None = None,
    ) -> ModelSuccess[T] | ModelPending: ...


@runtime_checkable
class ArtifactReader(Protocol):
    """``get_artifact_content(artifact_id)``, split in two.

    Metadata and content are separate calls so a metadata-only path (dedupe,
    timeline rendering) never materialises document text.
    """

    def get_artifact_metadata(self, artifact_id: uuid.UUID) -> ArtifactMetadata: ...

    def get_normalized_content(self, artifact_id: uuid.UUID) -> NormalizedContent: ...


@runtime_checkable
class EvidenceRegistrar(Protocol):
    """Creates immutable evidence rows, or returns existing deduplicated ids.

    Admitting evidence means "this text was present in the artifact", never
    "the claim is true". This is a typed internal API, not a database handle:
    the agent has no SQL surface at all.
    """

    def register_or_lookup_evidence(
        self, *, artifact_id: uuid.UUID, candidates: Sequence[EvidenceCandidate]
    ) -> Mapping[str, uuid.UUID]: ...


@runtime_checkable
class RetrievalService(Protocol):
    """``search_memory_candidates(query_spec)``. Deterministic, bounded, read-only."""

    def retrieve_candidate_context(self, spec: RetrievalSpec) -> RetrievalResult: ...


@runtime_checkable
class MemoryKernelClient(Protocol):
    """``submit_memory_proposal(proposal)`` -- the interpreter's only write tool.

    One proposal, one case, one call. The Kernel never opens a cross-case
    transaction for a single proposal, and the graph never batches two cases
    into one submission.
    """

    def submit_memory_proposal(self, proposal: MemoryProposal) -> KernelCommitResult: ...


@runtime_checkable
class StateProofReader(Protocol):
    """The Advocate's two read tools, and its whole view of the world.

    There is deliberately no method here that could return an uncommitted
    proposal. An advocate that reads uncommitted proposals is invariant 4
    waiting to happen, so the capability is absent from the type rather than
    discouraged in a docstring.
    """

    def get_state_proof(self, case_id: uuid.UUID) -> StateProof: ...

    def get_action_policy(self, case_id: uuid.UUID) -> AdvocacyContext: ...


@runtime_checkable
class ActionIntentWriter(Protocol):
    """``create_action_intent(draft)`` -- the Advocate's only write tool.

    There is no ``send_email``. An injected instruction saying "send this now"
    has nothing to call, which is a structural containment rather than a
    prompt-level one.
    """

    def create_action_intent(
        self,
        draft: DraftAction,
        *,
        rationale: str,
        warnings: Sequence[str],
        needs_review: bool,
    ) -> ActionIntentReceipt: ...


@runtime_checkable
class SessionRecorder(Protocol):
    """Workflow durability. **Not** product state. See the module docstring.

    One method, returning ``None``. There is no reader, so a graph cannot
    satisfy a business question from session storage without first adding a
    method here -- a visible change to the prohibition rather than a quiet one.
    """

    def record(self, *, run_id: uuid.UUID, node: str, note: str) -> None: ...


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestionGraphState:
    """``03_AGENTS_LANGGRAPH_CONTRACTS.md`` section 3, typed.

    Frozen: every node returns a new state through ``dataclasses.replace``, so
    the visit order is a record of what ran rather than a mutable log that a
    later node could rewrite.
    """

    trace_id: uuid.UUID
    agent_run_id: uuid.UUID
    principal_ref: CapabilityBinding
    artifact_id: uuid.UUID

    graph_name: str = GRAPH_NAME_INGESTION
    graph_version: str = GRAPH_VERSION_INGESTION

    artifact_metadata: ArtifactMetadata | None = None
    normalized_content: NormalizedContent | None = None
    rendered_blocks: tuple[ContentBlock, ...] = ()
    fence_nonce: str | None = None
    extraction_result: ExtractionResult | None = None
    extraction_failures: tuple[ValidationFailure, ...] = ()
    evidence_ids_by_local_id: Mapping[str, uuid.UUID] = field(default_factory=dict)
    retrieval: RetrievalResult | None = None
    resolution_signals: ResolutionSignals | None = None
    resolution_assessment: ResolutionAssessment | None = None
    memory_proposals: tuple[MemoryProposal, ...] = ()
    kernel_results: tuple[KernelCommitResult, ...] = ()
    advocate_signals: tuple[uuid.UUID, ...] = ()

    fence_scrub_log: tuple[FenceScrubEntry, ...] = ()
    model_routes: tuple[ModelRoute, ...] = ()
    visits: tuple[str, ...] = ()
    errors: tuple[GraphError, ...] = ()
    route_flags: frozenset[str] = frozenset()
    outcome: IngestionOutcome = IngestionOutcome.IN_PROGRESS

    @property
    def halted(self) -> bool:
        return self.outcome is not IngestionOutcome.IN_PROGRESS

    @property
    def idempotency_keys(self) -> tuple[str, ...]:
        return tuple(p.idempotency_key for p in self.memory_proposals)

    @property
    def retrieval_context(self) -> RetrievalContext | None:
        return None if self.retrieval is None else self.retrieval.context


@dataclass(frozen=True, slots=True)
class AdvocateGraphState:
    """Section 6, typed. Its only business input is a committed State Proof."""

    trace_id: uuid.UUID
    agent_run_id: uuid.UUID
    principal_ref: CapabilityBinding
    case_id: uuid.UUID

    graph_name: str = GRAPH_NAME_ADVOCATE
    graph_version: str = GRAPH_VERSION_ADVOCATE

    state_proof: StateProof | None = None
    advocacy_context: AdvocacyContext | None = None
    attention: AttentionAssessment | None = None
    action_type: ActionType | None = None
    draft: DraftAction | None = None
    draft_failures: tuple[ValidationFailure, ...] = ()
    action_intent: ActionIntentReceipt | None = None

    model_routes: tuple[ModelRoute, ...] = ()
    visits: tuple[str, ...] = ()
    errors: tuple[GraphError, ...] = ()
    route_flags: frozenset[str] = frozenset()
    outcome: AdvocateOutcome = AdvocateOutcome.IN_PROGRESS

    @property
    def halted(self) -> bool:
        return self.outcome is not AdvocateOutcome.IN_PROGRESS


def initial_ingestion_state(
    *,
    trace_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    principal_ref: CapabilityBinding,
    artifact_id: uuid.UUID,
) -> IngestionGraphState:
    return IngestionGraphState(
        trace_id=trace_id,
        agent_run_id=agent_run_id,
        principal_ref=principal_ref,
        artifact_id=artifact_id,
    )


def initial_advocate_state(
    *,
    trace_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    principal_ref: CapabilityBinding,
    case_id: uuid.UUID,
) -> AdvocateGraphState:
    return AdvocateGraphState(
        trace_id=trace_id,
        agent_run_id=agent_run_id,
        principal_ref=principal_ref,
        case_id=case_id,
    )
