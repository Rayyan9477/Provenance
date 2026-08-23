"""Tier R advisory output.

Everything here is advisory. ``advisory: Literal[True]`` is a field, not a
docstring, so a downstream consumer that treats an assessment as canonical has
to visibly strip a flag that says it is not.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 10, whose code this module implements.
- ``CANONICAL_DECISIONS.md`` -> *Bedrock model id canon (frozen 2026-08-17)*,
  which **supersedes** section 10's model-id map. See the recorded deviation.
- ``EXECUTION/70_TASK_PLAN.md`` T1.5, fifth sub-task.

Recorded deviation 1 — ``ModelAttribution`` may not pin bare model ids
-----------------------------------------------------------------------
Section 10 pins ``ModelTier.E`` to the literal string
``anthropic.claude-haiku-4-5`` and ``ModelTier.R`` to
``anthropic.claude-opus-5``. ``CANONICAL_DECISIONS.md`` -> *Bedrock model id
canon* disproved both by live invocation and says so explicitly: "Every
occurrence of a bare ``anthropic.claude-*`` id elsewhere in the pack is
superseded by this section." Bare Anthropic ids are **not invocable** on
Bedrock; the invocable form carries a region-group prefix (``us.`` or
``global.``). ``us.anthropic.claude-opus-5`` is additionally *denied* to this
account, and the shipped Tier R is ``us.anthropic.claude-opus-4-6-v1``.

Implementing section 10 literally would therefore reject every attribution the
running system can produce, and would break the canon's *Router obligation*
("Swapping either is a one-line environment change, never a code change"). The
validator here checks the **shape** the canon establishes instead of a frozen
string — the same posture ``settings.py`` took for defect ``D-00-002``:

* ``EMBEDDING`` — frozen by contract to the bare id
  ``amazon.titan-embed-text-v2:0``, which the canon confirms is invoked bare.
* ``E`` and ``R`` — must be an Anthropic **inference-profile** id. A bare
  ``anthropic.*`` id is refused with the message Bedrock itself returns.

The canon outranks the spec, so this is applied rather than merely reported;
it is reported as well, because ``11_CONTRACTS.md`` section 10 still prints the
superseded map and should be corrected at its source.

Recorded deviation 2 — resolved in T1.6: ``HUMAN_REVIEW_CONFIDENCE_FLOOR``
----------------------------------------------------------------------------
T1.5 defined this constant locally, because section 10 imports it from
``provenance_domain.invariants``, which is built by **T1.4** and did not exist
while T1.5 ran. T1.4 has since landed. The local definition is deleted and the
name is imported from the domain, as section 10 says: one value, one home. Two
definitions of a review threshold is exactly the drift this package exists to
prevent.

The two values were compared before the local one was removed, rather than
assumed equal: ``provenance_domain.invariants.HUMAN_REVIEW_CONFIDENCE_FLOOR``
is ``Decimal("0.70")``, which is what T1.5 had and what
``specs/11_CONTRACTS.md`` section 5.1 freezes. It is re-exported from here
under the same name so every existing importer keeps working, and
``tests/test_scalars.py`` still sees the symbol it asserts against.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Confidence,
    Contract,
    LocalId,
    ReasonCode,
    UtcDatetime,
    validate_half_open,
)
from provenance_domain.enums import (
    DateGranularity,
    ModelTier,
    SemanticRelation,
    SourceClass,
    SupportSourceKind,
)
from provenance_domain.invariants import HUMAN_REVIEW_CONFIDENCE_FLOOR

__all__ = [
    "HUMAN_REVIEW_CONFIDENCE_FLOOR",
    "ModelAttribution",
    "ProposedSupersession",
    "ResolutionAssessment",
    "ResolvedIdentity",
    "SemanticRelationAssertion",
    "TemporalInterpretation",
]

#: Frozen by contract and invoked by **bare** id: the inference-profile
#: requirement applies to the Anthropic chat models only.
FROZEN_EMBEDDING_MODEL_ID: Final[str] = "amazon.titan-embed-text-v2:0"

#: Region-group prefixes that make a Bedrock id an inference-profile id.
INFERENCE_PROFILE_PREFIXES: Final[tuple[str, ...]] = ("us.", "global.")

Reason = Annotated[str, StringConstraints(min_length=1, max_length=400)]


class ModelAttribution(BoundaryContract):
    """Which model produced an artifact of reasoning, under which prompt.

    Stored on every agent run so a demo or evaluation regression is
    explainable: graph name, graph version, prompt version, model id,
    extraction schema version.
    """

    # Defaults to ``bedrock`` rather than to the shipping provider, and the
    # reason is that it does not matter for safety: the validator dispatches on
    # provider, so an id that disagrees with its provider raises either way.
    # Given that, the default is chosen to leave fixtures that are *about*
    # Bedrock unedited by a Gemini change. New Gemini call sites say so.
    provider: Literal["bedrock", "gemini"] = "bedrock"
    model_id: Annotated[str, StringConstraints(max_length=128)]
    tier: ModelTier
    prompt_version: Annotated[str, StringConstraints(max_length=32)]
    graph_name: Annotated[str, StringConstraints(max_length=64)]
    graph_version: Annotated[str, StringConstraints(max_length=32)]
    extraction_schema_version: Annotated[str, StringConstraints(max_length=16)] | None = None

    @model_validator(mode="after")
    def _tier_matches_model(self) -> ModelAttribution:
        """Model id shape, dispatched on provider -- never applied uniformly.

        The uniform version of this check is the bug ``D-00-040`` records. On
        Bedrock, Anthropic chat models are invocable **only** through an
        inference-profile id carrying a ``us.``/``global.`` prefix, while every
        other provider on Bedrock is invocable **only** by bare id. Those rules
        are mirror images, so a client applying one of them uniformly cannot
        call both families. Gemini adds a third rule -- bare ids, no
        ``anthropic.`` anywhere -- and folding it into either of the first two
        would reintroduce exactly that defect.

        See recorded deviation 1 at the top of this module for why the
        non-embedding branches are shape checks rather than the equality check
        ``specs/11_CONTRACTS.md`` section 10 prints.
        """
        if self.tier is ModelTier.EMBEDDING:
            return self._embedding_id_names_a_declared_space()
        if self.provider == "bedrock":
            return self._bedrock_chat_id_is_an_inference_profile()
        return self._gemini_chat_id_is_bare()

    def _embedding_id_names_a_declared_space(self) -> ModelAttribution:
        """Accept exactly the ids some ``EmbeddingProfile`` declares.

        Derived from the profile registry rather than restated, because a
        hand-maintained second list is one that drifts: adding a profile would
        silently fail to make its model recordable here, and the symptom would
        be a run that cannot be attributed rather than an error at the point of
        the mistake.
        """
        from provenance_contracts.embedding_profile import EMBEDDING_PROFILES

        declared = {p.model_id: p.provider for p in EMBEDDING_PROFILES.values()}
        if self.model_id not in declared:
            known = ", ".join(sorted(declared))
            raise ValueError(
                f"{self.model_id!r} names no embedding space this build declares. "
                f"Known embedding models: {known}. Guessing a width for an "
                "unrecognised model is how a short vector reaches an index with "
                "nothing erroring."
            )
        expected_provider = declared[self.model_id]
        if self.provider != expected_provider:
            raise ValueError(
                f"{self.model_id!r} is served by provider {expected_provider!r}, "
                f"not {self.provider!r}"
            )
        return self

    def _bedrock_chat_id_is_an_inference_profile(self) -> ModelAttribution:
        if "anthropic." not in self.model_id:
            raise ValueError(
                f"tier {self.tier} on Bedrock is served by an Anthropic model; "
                f"{self.model_id!r} is not one. Model routing is deterministic from "
                "task class, and there is no meta-agent that chooses models."
            )
        if not self.model_id.startswith(INFERENCE_PROFILE_PREFIXES):
            prefixes = " or ".join(repr(prefix) for prefix in INFERENCE_PROFILE_PREFIXES)
            raise ValueError(
                f"{self.model_id!r} is a bare Bedrock model id. Anthropic chat models "
                "on Bedrock are invocable only through an inference profile id, which "
                f"carries a region-group prefix - {prefixes}. A bare id returns "
                "ValidationException about on-demand throughput not being supported. "
                "See CANONICAL_DECISIONS.md -> Bedrock model id canon (frozen "
                "2026-08-17), which supersedes the frozen id map in "
                "specs/11_CONTRACTS.md section 10."
            )
        return self

    def _gemini_chat_id_is_bare(self) -> ModelAttribution:
        """Gemini takes bare ids. There is no inference-profile concept here.

        The prefix check is not paranoia: it is the precise error a developer
        carrying the Bedrock rule across would make, and ``us.gemini-3.7-flash``
        does not exist.
        """
        if "anthropic" in self.model_id or "claude" in self.model_id:
            raise ValueError(
                f"{self.model_id!r} is an Anthropic id and cannot be served by the "
                "Gemini provider. See CANONICAL_DECISIONS.md -> Gemini model id canon."
            )
        if self.model_id.startswith(INFERENCE_PROFILE_PREFIXES):
            prefixes = " or ".join(repr(prefix) for prefix in INFERENCE_PROFILE_PREFIXES)
            raise ValueError(
                f"{self.model_id!r} carries a Bedrock inference-profile prefix "
                f"({prefixes}). The Gemini API has no such concept and takes bare "
                "ids; this is the mirror-image mistake D-00-040 records."
            )
        if not self.model_id.startswith("gemini-"):
            raise ValueError(
                f"tier {self.tier} on Gemini expects a 'gemini-' model id, got "
                f"{self.model_id!r}."
            )
        # An embedding model is a `gemini-` id and passes every check above, so
        # without this an embedding model could be recorded as having served a
        # reasoning tier. The prefix test is necessary and not sufficient, and
        # the id set that settles it is the profile registry rather than a
        # second hand-maintained list.
        from provenance_contracts.embedding_profile import EMBEDDING_PROFILES

        embedding_ids = {profile.model_id for profile in EMBEDDING_PROFILES.values()}
        if self.model_id in embedding_ids:
            raise ValueError(
                f"{self.model_id!r} is an embedding model and cannot have served "
                f"tier {self.tier}. Embedding attributions use ModelTier.EMBEDDING."
            )
        return self


class ResolvedIdentity(Contract):
    relationship_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    confidence: Confidence
    reasons: tuple[Reason, ...] = Field(default=(), max_length=8)


class SemanticRelationAssertion(Contract):
    """ "Does this evidence support, contradict, or qualify that belief?"

    ``UNRELATED`` is expressible so the resolver can say "no" explicitly; the
    proposal builder drops UNRELATED rows rather than persisting them, since
    ``belief_support.relation`` has no such value.
    """

    source_kind: SupportSourceKind
    source_id: uuid.UUID | None = None
    source_local_id: LocalId | None = None
    target_kind: Literal["BELIEF_VERSION", "CLAIM", "COMMITMENT"]
    target_id: uuid.UUID
    relation: SemanticRelation
    confidence: Confidence
    rationale: Reason

    @model_validator(mode="after")
    def _exactly_one_source_ref(self) -> SemanticRelationAssertion:
        if (self.source_id is None) == (self.source_local_id is None):
            raise ValueError(
                "provide exactly one of source_id (already persisted) or "
                "source_local_id (proposed in this run)"
            )
        return self


class TemporalInterpretation(Contract):
    """A reading of when something was true, separate from when we learned it."""

    target_kind: Literal["CLAIM", "COMMITMENT", "BELIEF"]
    target_id: uuid.UUID | None = None
    target_local_id: LocalId | None = None
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    granularity: DateGranularity
    basis: Reason
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> TemporalInterpretation:
        if (self.target_id is None) == (self.target_local_id is None):
            raise ValueError("provide exactly one of target_id or target_local_id")
        validate_half_open(self.valid_from, self.valid_to)
        return self


class ProposedSupersession(Contract):
    """Bitemporal rule T3.

    A prior version's ``valid_to`` may close at T only if source authority
    supports supersession. The resolver names the authority basis; the Kernel
    decides whether it is sufficient.
    """

    target_kind: Literal["BELIEF", "COMMITMENT", "POLICY_TERM"]
    target_id: uuid.UUID
    superseding_source_kind: SupportSourceKind
    superseding_source_local_id: LocalId | None = None
    superseding_source_id: uuid.UUID | None = None
    effective_at: UtcDatetime
    authority_basis: SourceClass
    reason_code: ReasonCode
    confidence: Confidence


class ResolutionAssessment(BoundaryContract):
    """Advisory output of the ``strong_resolution`` node.

    Contains no database mutations, no SQL, and no entitlement conclusions. It
    says what relates to what and when things were true; the Kernel decides what
    that means for canonical state.
    """

    advisory: Literal[True] = True
    trace_id: uuid.UUID
    agent_run_id: uuid.UUID

    identity: ResolvedIdentity
    semantic_relations: tuple[SemanticRelationAssertion, ...] = Field(default=(), max_length=40)
    proposed_temporal_interpretations: tuple[TemporalInterpretation, ...] = Field(
        default=(), max_length=20
    )
    proposed_supersessions: tuple[ProposedSupersession, ...] = Field(default=(), max_length=10)
    unresolved_questions: tuple[Reason, ...] = Field(default=(), max_length=10)
    requires_human_review: bool
    rationale_summary: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    model: ModelAttribution

    @model_validator(mode="after")
    def _low_confidence_must_escalate(self) -> ResolutionAssessment:
        """Abstain rather than commit wrongly on ambiguous cases.

        If the resolver bound an identity below the human-review floor, or left
        an unresolved question open, or proposed closing out a prior version, it
        must have set ``requires_human_review``. This turns the identity
        evaluation gate ("abstain/pending rather than wrong commit") into a
        validation error instead of a metric someone reads later.

        Section 23 risk 6 records the cost, which is accepted deliberately: a
        Tier R response that violates this is *unconstructable*, so the node
        consumes its single repair attempt and then routes to
        ``PENDING_HUMAN_REVIEW``. That path must be reachable from a validation
        failure, not only from a well-formed low-confidence assessment — a
        graph-wiring obligation this module specifies but cannot enforce.
        """
        if self.model.tier is not ModelTier.R:
            raise ValueError("ResolutionAssessment must come from Tier R")
        must_escalate = (
            (
                self.identity.case_id is not None
                and self.identity.confidence < HUMAN_REVIEW_CONFIDENCE_FLOOR
            )
            or bool(self.unresolved_questions)
            or any(
                s.confidence < HUMAN_REVIEW_CONFIDENCE_FLOOR for s in self.proposed_supersessions
            )
        )
        if must_escalate and not self.requires_human_review:
            raise ValueError(
                "assessment binds an identity or supersession below the "
                f"{HUMAN_REVIEW_CONFIDENCE_FLOOR} human-review floor, or leaves a "
                "question open, but did not set requires_human_review"
            )
        return self

    def support_edges(self) -> tuple[SemanticRelationAssertion, ...]:
        """Only the relations that can become persisted grounding edges."""
        return tuple(
            r for r in self.semantic_relations if r.relation is not SemanticRelation.UNRELATED
        )
