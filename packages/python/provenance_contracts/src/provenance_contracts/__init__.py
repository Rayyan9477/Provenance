"""Shared Provenance contracts. Import from here, never from a submodule of
another service.

Single responsibility
---------------------
Own every JSON shape that crosses a process boundary, once, as versioned
Pydantic v2 models: ``Principal``, the ingestion payloads, the retrieval and
resolution payloads, ``MemoryProposal``, ``KernelCommitResult``,
``StateProof``, ``DomainEvent``, the action-intent models, and the trigger
models. Also the typed settings object (``settings.py``, T0.4), which is
deliberately **not** imported here: it reads the environment, and importing
this package must never do that.

A coding agent that re-declares one of these shapes inside a service has
created a second copy of a contract, which is how the field name in the API and
the field name in the worker drift apart. :data:`CONTRACT_REGISTRY` is the
single place a new boundary contract is registered; ``test_roundtrip.py``
iterates it, so a contract added to a module and forgotten there is untested by
construction.

Authority: ``specs/11_CONTRACTS.md`` section 18. Enum membership mirrors that
document exactly -- no layer-local aliases. Every export below is named
explicitly: there is no ``import *`` anywhere in this package, so the public
surface is a decision rather than a side effect of what happened to be defined.

Forbidden dependencies
----------------------
``boto3``, ``psycopg``, ``httpx``, ``provenance_db``. This package performs no
I/O: it validates and serialises, and it never reaches anything. Enforced by
``.importlinter`` contract ``contracts-have-no-io``, and separately by
``tests/test_no_sql_in_contracts.py`` and ``tools/contract_lint.py``, which
fail on SQL text appearing anywhere in the package.

Permitted dependencies: ``pydantic>=2.9``, ``provenance_domain``, ``uuid6``.

What ``CONTRACT_REGISTRY`` holds, and what it does not
-------------------------------------------------------
**Boundary contracts only.** ``schema_version`` lives on ``BoundaryContract``,
not on ``Contract``: putting it on ``Contract`` would break section 20.3's
assertion that ``Money.model_dump(mode="json")`` has exactly two keys. The 31
value objects are the documented carve-out, pinned by
``tests/test_scalars.py::test_value_objects_are_the_documented_carve_out``.

One boundary contract is deliberately outside the registry: ``ModelAttribution``.
Section 18's registry does not list it, and it never travels alone -- it is a
component of ``ResolutionAssessment``, ``MemoryProposal`` and ``DraftAction``,
each of which is registered. That omission is pinned by
``tests/test_roundtrip.py::test_the_registry_covers_every_boundary_contract``
so it cannot quietly grow a second member.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from pydantic import BaseModel

from provenance_contracts.actions import (
    DRAFT_HASH_EXCLUDE,
    FORBIDDEN_OUTBOUND_TERMS,
    POST_APPROVAL_STATES,
    ActionExecutionView,
    ActionIntentView,
    DraftAction,
    DraftClaim,
    ExecutabilityVerdict,
)
from provenance_contracts.base import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_MAJORS,
    BoundaryContract,
    Confidence,
    Contract,
    HalfOpenInterval,
    Money,
    ReasonCode,
    Revision,
    Sha256Hex,
    UtcDatetime,
    Weight,
    canonical_json,
    content_hash,
    new_id,
    utc_now,
    validate_half_open,
)
from provenance_contracts.events import (
    FORBIDDEN_PAYLOAD_KEYS,
    MAX_EVENT_PAYLOAD_BYTES,
    DomainEvent,
)
from provenance_contracts.identity import (
    AuthorizationError,
    CapabilityBinding,
    InternalPrincipal,
    Principal,
)
from provenance_contracts.ingestion import (
    ArtifactMetadata,
    ContentBlock,
    ContentLocator,
    EvidenceCandidate,
    ExtractionResult,
    NormalizedContent,
    SourceLocator,
)
from provenance_contracts.kernel import (
    PREFLIGHT_DECISIONS,
    BeliefVersionRef,
    CommitmentChange,
    ConflictRef,
    KernelCommitResult,
    StateTransitionRef,
    TriggerChange,
)
from provenance_contracts.predicates import (
    MAX_PREDICATE_DEPTH,
    WHITELISTED_FIELD_ROOTS,
    PredicateNode,
)
from provenance_contracts.proof import (
    PROOF_HASH_EXCLUDE,
    BeliefProof,
    BeliefVersionProof,
    CaseSnapshot,
    CommitmentProof,
    ConflictProof,
    DerivationTrace,
    EvidenceProof,
    FulfillmentProof,
    GroundingEdgeProof,
    LineageEntry,
    StateProof,
    StateTransitionProof,
    TriggerProof,
)
from provenance_contracts.proposal import (
    ConflictHint,
    DeterministicDerivation,
    MemoryProposal,
    ProposalIdentity,
    ProposedBeliefMutation,
    ProposedClaim,
    ProposedCommitment,
    ProposedSupportEdge,
    ProposedTrigger,
)
from provenance_contracts.resolution import (
    HUMAN_REVIEW_CONFIDENCE_FLOOR,
    ModelAttribution,
    ResolutionAssessment,
    ResolvedIdentity,
)
from provenance_contracts.retrieval import (
    EvidenceSnippet,
    IdentityCandidate,
    McpToolCall,
    RetrievalContext,
    VectorSearchParams,
)
from provenance_contracts.triggers import (
    PredicateEvalStep,
    TriggerEvaluationResult,
    TriggerWakeup,
)

__version__ = "1.0.0"

__all__ = [
    "CONTRACT_REGISTRY",
    "DRAFT_HASH_EXCLUDE",
    "FORBIDDEN_OUTBOUND_TERMS",
    "FORBIDDEN_PAYLOAD_KEYS",
    "HUMAN_REVIEW_CONFIDENCE_FLOOR",
    "MAX_EVENT_PAYLOAD_BYTES",
    "MAX_PREDICATE_DEPTH",
    "POST_APPROVAL_STATES",
    "PREFLIGHT_DECISIONS",
    "PROOF_HASH_EXCLUDE",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_MAJORS",
    "WHITELISTED_FIELD_ROOTS",
    "ActionExecutionView",
    "ActionIntentView",
    "ArtifactMetadata",
    "AuthorizationError",
    "BeliefProof",
    "BeliefVersionProof",
    "BeliefVersionRef",
    "BoundaryContract",
    "CapabilityBinding",
    "CaseSnapshot",
    "CommitmentChange",
    "CommitmentProof",
    "Confidence",
    "ConflictHint",
    "ConflictProof",
    "ConflictRef",
    "ContentBlock",
    "ContentLocator",
    "Contract",
    "DerivationTrace",
    "DeterministicDerivation",
    "DomainEvent",
    "DraftAction",
    "DraftClaim",
    "EvidenceCandidate",
    "EvidenceProof",
    "EvidenceSnippet",
    "ExecutabilityVerdict",
    "ExtractionResult",
    "FulfillmentProof",
    "GroundingEdgeProof",
    "HalfOpenInterval",
    "IdentityCandidate",
    "InternalPrincipal",
    "KernelCommitResult",
    "LineageEntry",
    "McpToolCall",
    "MemoryProposal",
    "ModelAttribution",
    "Money",
    "NormalizedContent",
    "PredicateEvalStep",
    "PredicateNode",
    "Principal",
    "ProposalIdentity",
    "ProposedBeliefMutation",
    "ProposedClaim",
    "ProposedCommitment",
    "ProposedSupportEdge",
    "ProposedTrigger",
    "ReasonCode",
    "ResolutionAssessment",
    "ResolvedIdentity",
    "RetrievalContext",
    "Revision",
    "Sha256Hex",
    "SourceLocator",
    "StateProof",
    "StateTransitionProof",
    "StateTransitionRef",
    "TriggerChange",
    "TriggerEvaluationResult",
    "TriggerProof",
    "TriggerWakeup",
    "UtcDatetime",
    "VectorSearchParams",
    "Weight",
    "__version__",
    "canonical_json",
    "content_hash",
    "new_id",
    "utc_now",
    "validate_half_open",
]

#: Every model that crosses a boundary on its own, by name. The API layer
#: iterates this to emit JSON Schemas, and ``test_roundtrip.py`` iterates it to
#: prove every contract serialises and re-validates.
#:
#: Authority: ``specs/11_CONTRACTS.md`` section 18, reproduced exactly. Adding a
#: name here without a round-trip fixture fails CI immediately, which is the
#: intended pressure.
CONTRACT_REGISTRY: Final[MappingProxyType[str, type[BaseModel]]] = MappingProxyType(
    {
        "Principal": Principal,
        "InternalPrincipal": InternalPrincipal,
        "ArtifactMetadata": ArtifactMetadata,
        "NormalizedContent": NormalizedContent,
        "ExtractionResult": ExtractionResult,
        "IdentityCandidate": IdentityCandidate,
        "RetrievalContext": RetrievalContext,
        "ResolutionAssessment": ResolutionAssessment,
        "MemoryProposal": MemoryProposal,
        "KernelCommitResult": KernelCommitResult,
        "StateProof": StateProof,
        "DomainEvent": DomainEvent,
        "DraftAction": DraftAction,
        "ActionIntentView": ActionIntentView,
        "TriggerWakeup": TriggerWakeup,
        "TriggerEvaluationResult": TriggerEvaluationResult,
    }
)
