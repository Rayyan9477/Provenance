"""Inputs to the Ingestion/Interpretation graph.

Nothing in this module is trusted. ``ContentBlock.trust_class`` is a
``Literal[TrustClass.UNTRUSTED]`` so that a prompt builder which forgets to put
a block in the UNTRUSTED EVIDENCE section is a type error rather than a
prompt-injection incident.

Authority
---------
- ``specs/11_CONTRACTS.md`` sections 8, 8.1 and 8.2, whose code this module
  implements.
- ``specs/03_AGENTS_LANGGRAPH_CONTRACTS.md`` section 5.4, whose
  ``validate_extraction_schema`` is expressed here as types rather than as a
  node function: every candidate cites a block that was actually supplied,
  every claim cites an evidence candidate that exists, every commitment cites a
  claim, and every local id is unique.
- ``EXECUTION/70_TASK_PLAN.md`` T1.5, third sub-task.

Quoted history is a tag, not a heuristic
----------------------------------------
``ContentBlockKind.QUOTED_HISTORY`` is the mechanism behind the Interpreter
rule "distinguish quoted history from new message content". A promise found
only inside a quoted block is not a new promise: ``ClaimCandidate.modality``
must then be ``QUOTED_HISTORICAL``, and ``CommitmentCandidate`` refuses that
modality outright. The parser tags the block once, at ingestion, and every
later stage reads the tag instead of re-deciding — which is what stops a
four-month-old forwarded promise from being admitted twice.

Recorded deviation from ``specs/11_CONTRACTS.md`` section 8
-------------------------------------------------------------
``EvidenceCandidate`` and ``ClaimCandidate`` both carry the
``[valid_from, valid_to)`` pair. Section 8 checks the ordering on
``EvidenceCandidate`` only. Both call
:func:`provenance_contracts.base.validate_half_open` here: an inverted interval
on a claim is the same modelling error, and ``EXECUTION/70_TASK_PLAN.md`` T1.5
asks for the rule to be enforced by the base scalar rather than per model.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BlockId,
    BoundaryContract,
    Confidence,
    Contract,
    LocalId,
    Money,
    ReasonCode,
    SafeIdentifier,
    Sha256Hex,
    UtcDatetime,
    validate_half_open,
)
from provenance_domain.enums import (
    ActorType,
    AmountRole,
    ArtifactSourceType,
    ClaimKind,
    CommitmentType,
    ContentBlockKind,
    DateGranularity,
    DateRole,
    EvidenceType,
    ExternalIdentifierKind,
    Modality,
    ModelTier,
    ParserStatus,
    SourceClass,
    SubjectType,
    TriggerType,
    TrustClass,
    ValueType,
)

__all__ = [
    "EXTRACTION_SCHEMA_VERSION",
    "AmountMention",
    "ArtifactMetadata",
    "ClaimCandidate",
    "CommitmentCandidate",
    "ContentBlock",
    "ContentLocator",
    "CounterpartyHint",
    "DateMention",
    "EvidenceCandidate",
    "ExternalIdentifier",
    "ExtractionResult",
    "InjectionObservation",
    "NormalizedContent",
    "ProspectiveCue",
    "SourceLocator",
    "Uncertainty",
]

#: Versioned independently of ``SCHEMA_VERSION`` (section 22): prompt and
#: extraction-schema churn is expected to be far faster than boundary-contract
#: churn. Recorded on every ``agent_runs`` row so an evaluation regression can
#: be attributed to a schema change rather than a model change.
EXTRACTION_SCHEMA_VERSION: str = "1.0"

FreeText = Annotated[str, StringConstraints(min_length=1, max_length=8000)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ContentLocator(Contract):
    """Where the raw bytes live. Control-plane internal only."""

    scheme: Literal["s3"] = "s3"
    bucket: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]{2,62}$")]
    key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    version_id: Annotated[str, StringConstraints(max_length=1024)] | None = None


class SourceLocator(Contract):
    """Exactly where inside an artifact an observation came from.

    Every admitted evidence item carries one. "No candidate without provenance
    may be admitted" is enforced by making this field required on
    :class:`EvidenceCandidate`, not by asking the model nicely.
    """

    kind: Literal["EMAIL_PART", "PDF_PAGE", "IMAGE_REGION", "TABLE_CELL", "TEXT_SPAN"]
    block_id: BlockId
    mime_part: Annotated[str, StringConstraints(max_length=128)] | None = None
    page: Annotated[int, Field(ge=1, le=10_000)] | None = None
    bbox: tuple[float, float, float, float] | None = None
    char_start: Annotated[int, Field(ge=0)] | None = None
    char_end: Annotated[int, Field(ge=0)] | None = None
    row: Annotated[int, Field(ge=0)] | None = None
    column: Annotated[int, Field(ge=0)] | None = None
    attachment_name: Annotated[str, StringConstraints(max_length=255)] | None = None

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> SourceLocator:
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end <= self.char_start
        ):
            raise ValueError("char_end must be greater than char_start")
        required: dict[str, tuple[str, ...]] = {
            "PDF_PAGE": ("page",),
            "IMAGE_REGION": ("bbox",),
            "TABLE_CELL": ("row", "column"),
            "TEXT_SPAN": ("char_start", "char_end"),
        }
        for field_name in required.get(self.kind, ()):
            if getattr(self, field_name) is None:
                raise ValueError(f"{self.kind} locator requires {field_name}")
        return self


class ArtifactMetadata(BoundaryContract):
    """Safe metadata for one immutable source artifact.

    Carries no document text. The Interpreter's ``load_artifact_metadata`` node
    receives this; ``load_normalized_content`` separately returns blocks.
    Splitting them keeps a metadata-only path (dedupe checks, timeline
    rendering) that never materialises document content.
    """

    artifact_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    source_type: ArtifactSourceType
    mime_type: Annotated[str, StringConstraints(pattern=r"^[\w.+-]+/[\w.+-]+$", max_length=255)]
    content_sha256: Sha256Hex
    size_bytes: Annotated[int, Field(gt=0, le=20 * 1024 * 1024)]
    content_locator: ContentLocator | None = None
    source_message_id: Annotated[str, StringConstraints(max_length=998)] | None = None
    sender: Annotated[str, StringConstraints(max_length=320)] | None = None
    recipient: Annotated[str, StringConstraints(max_length=320)] | None = None
    subject: Annotated[str, StringConstraints(max_length=2000)] | None = None
    received_at: UtcDatetime
    event_time: UtcDatetime | None = None
    parser_status: ParserStatus
    parser_version: Annotated[str, StringConstraints(max_length=64)] | None = None
    block_count: Annotated[int, Field(ge=0, le=10_000)] = 0
    is_duplicate_of: uuid.UUID | None = None

    def redacted_for_agent(self) -> ArtifactMetadata:
        """The agent-facing projection: no bucket, no key, no version id.

        The agent fetches content through ``get_artifact_content(artifact_id)``,
        which re-authorises against the run binding. Handing it an S3 locator
        would create a second, unaudited read path.
        """
        return self.model_copy(update={"content_locator": None})


class ContentBlock(Contract):
    """One parser-produced unit of artifact text.

    ``kind=QUOTED_HISTORY`` is the mechanism behind the Interpreter rule
    "distinguish quoted history from new message content". A promise found only
    inside a QUOTED_HISTORY block is not a new promise, and
    ``ClaimCandidate.modality`` must then be ``QUOTED_HISTORICAL``.
    """

    block_id: BlockId
    artifact_id: uuid.UUID
    ordinal: Annotated[int, Field(ge=0, le=10_000)]
    kind: ContentBlockKind
    text: Annotated[str, StringConstraints(max_length=100_000)]
    content_sha256: Sha256Hex
    source_locator: SourceLocator
    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] | None = None
    trust_class: Literal[TrustClass.UNTRUSTED] = TrustClass.UNTRUSTED

    @model_validator(mode="after")
    def _locator_matches_block(self) -> ContentBlock:
        if self.source_locator.block_id != self.block_id:
            raise ValueError(
                f"source_locator.block_id {self.source_locator.block_id!r} "
                f"does not match block_id {self.block_id!r}"
            )
        return self

    @property
    def is_quoted_history(self) -> bool:
        return self.kind is ContentBlockKind.QUOTED_HISTORY


class NormalizedContent(BoundaryContract):
    """The output of ``load_normalized_content``. Bounded on purpose."""

    artifact_id: uuid.UUID
    parser_version: Annotated[str, StringConstraints(max_length=64)]
    blocks: tuple[ContentBlock, ...] = Field(max_length=500)
    truncated: bool = False

    @model_validator(mode="after")
    def _unique_ordered_blocks(self) -> NormalizedContent:
        ids = [b.block_id for b in self.blocks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate block_id in normalized content")
        ordinals = [b.ordinal for b in self.blocks]
        if ordinals != sorted(ordinals):
            raise ValueError("blocks must be supplied in ordinal order")
        for block in self.blocks:
            if block.artifact_id != self.artifact_id:
                raise ValueError(f"block {block.block_id} belongs to a different artifact")
        return self


# ---------------------------------------------------------------------------
# 8.2 — ExtractionResult, the Tier E output contract
# ---------------------------------------------------------------------------


class CounterpartyHint(Contract):
    raw_name: ShortText
    normalized_name: ShortText
    domain: Annotated[str, StringConstraints(max_length=253)] | None = None
    block_id: BlockId
    confidence: Confidence


class ExternalIdentifier(Contract):
    kind: ExternalIdentifierKind
    value: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    block_id: BlockId
    confidence: Confidence


class DateMention(Contract):
    raw_text: ShortText
    normalized: UtcDatetime | None = None
    granularity: DateGranularity
    role: DateRole
    block_id: BlockId
    confidence: Confidence

    @model_validator(mode="after")
    def _unknown_granularity_has_no_value(self) -> DateMention:
        # Bitemporal rule T2: when evidence has no trustworthy effective date,
        # say unknown rather than inventing one.
        if self.granularity is DateGranularity.UNKNOWN and self.normalized is not None:
            raise ValueError(
                "granularity=UNKNOWN must not carry a normalized timestamp; "
                "an invented date is worse than an absent one"
            )
        return self


class AmountMention(Contract):
    raw_text: ShortText
    money: Money
    role: AmountRole
    block_id: BlockId
    confidence: Confidence


class EvidenceCandidate(Contract):
    """A proposed immutable observation.

    Admission means "this text was present", never "this statement is true".
    """

    local_id: LocalId
    evidence_type: EvidenceType
    exact_text: FreeText
    normalized_text: FreeText
    block_id: BlockId
    source_locator: SourceLocator
    source_class: SourceClass
    quoted: bool = False
    modality: Modality
    observed_at: UtcDatetime
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    extraction_confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> EvidenceCandidate:
        if not self.local_id.startswith("ev_"):
            raise ValueError("EvidenceCandidate.local_id must use the ev_ prefix")
        if self.source_locator.block_id != self.block_id:
            raise ValueError("source_locator must point at the cited block")
        validate_half_open(self.valid_from, self.valid_to)
        return self


class ClaimCandidate(Contract):
    """A source actor's assertion. Never canonical by itself.

    There is deliberately no ``authority_score`` field, and ``extra="forbid"``
    means one cannot be added by a model at run time. The extractor may
    recommend a ``source_class``; ``provenance_domain.authority.authority_for()``
    turns that into a number. That is the difference between "the model told us
    how much to trust it" and "we decided how much to trust that kind of
    source".
    """

    local_id: LocalId
    claim_kind: ClaimKind
    subject_type: SubjectType
    subject_hint: ShortText
    predicate: SafeIdentifier
    object_type: ValueType
    object_value: JsonValue
    actor_type: ActorType
    actor_hint: ShortText | None = None
    evidence_local_id: LocalId
    quoted: bool = False
    modality: Modality
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    extraction_confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> ClaimCandidate:
        if not self.local_id.startswith("cl_"):
            raise ValueError("ClaimCandidate.local_id must use the cl_ prefix")
        if not self.evidence_local_id.startswith("ev_"):
            raise ValueError("evidence_local_id must reference an ev_ candidate")
        validate_half_open(self.valid_from, self.valid_to)
        return self


class CommitmentCandidate(Contract):
    """A proposed obligation.

    Modality is load-bearing: an obligation may not be inferred from user
    desire, from a hypothetical, or from quoted history alone.
    """

    local_id: LocalId
    commitment_type: CommitmentType
    description: FreeText
    obligor_type: ActorType
    obligor_hint: ShortText | None = None
    beneficiary_type: ActorType
    beneficiary_hint: ShortText | None = None
    money: Money | None = None
    due_at: UtcDatetime | None = None
    due_condition_text: ShortText | None = None
    source_claim_local_id: LocalId
    quoted: bool = False
    modality: Modality
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> CommitmentCandidate:
        if not self.local_id.startswith("cm_"):
            raise ValueError("CommitmentCandidate.local_id must use the cm_ prefix")
        if self.quoted or self.modality in (
            Modality.HYPOTHETICAL,
            Modality.QUOTED_HISTORICAL,
        ):
            raise ValueError(
                f"quoted={self.quoted}, modality={self.modality} cannot create a commitment; "
                "extract it as a claim and let the Kernel decide"
            )
        if self.money is not None and self.money.amount < 0:
            raise ValueError("a committed amount may not be negative")
        return self


class ProspectiveCue(Contract):
    """Text implying a future check: "within 30 days of inspection".

    In the hero scenario this is what eventually arms the landlord deposit
    trigger that fires on its own four months later.
    """

    local_id: LocalId
    cue_text: ShortText
    suggested_trigger_type: TriggerType
    block_id: BlockId
    not_before: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    relative_to_claim_local_id: LocalId | None = None
    quoted: bool = False
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> ProspectiveCue:
        if not self.local_id.startswith("pc_"):
            raise ValueError("ProspectiveCue.local_id must use the pc_ prefix")
        return self


class InjectionObservation(Contract):
    """An instruction-like span observed in untrusted content, never obeyed."""

    local_id: LocalId
    block_id: BlockId
    classification: Literal[
        "INSTRUCTION_OVERRIDE",
        "TOOL_CALL_IMITATION",
        "SYSTEM_IMPERSONATION",
        "FENCE_BREAKOUT",
        "AUTHORITY_SPOOF",
        "IDENTIFIER_INJECTION",
        "OTHER",
    ]
    excerpt: ShortText
    action_taken: Literal["TREATED_AS_DATA"] = "TREATED_AS_DATA"

    @model_validator(mode="after")
    def _validate(self) -> InjectionObservation:
        if not self.local_id.startswith("ij_"):
            raise ValueError("InjectionObservation.local_id must use the ij_ prefix")
        return self


class Uncertainty(Contract):
    """An explicit statement of ambiguity.

    The Interpreter is instructed to state ambiguity rather than force a value;
    this is where that goes.
    """

    local_id: LocalId
    code: ReasonCode
    description: FreeText
    affects_local_ids: tuple[LocalId, ...] = ()
    blocks_state_change: bool = False

    @model_validator(mode="after")
    def _validate(self) -> Uncertainty:
        if not self.local_id.startswith("un_"):
            raise ValueError("Uncertainty.local_id must use the un_ prefix")
        return self


class ExtractionResult(BoundaryContract):
    """Complete Tier E output for one artifact.

    Cross-reference validation happens here rather than in a node function, so
    a schema-valid-but-referentially-broken extraction cannot reach the
    proposal builder at all. A failure raises ``ValidationError``, the graph
    takes its single repair attempt, and a second failure routes to FAIL_SAFE
    with the evidence left pending.
    """

    extraction_schema_version: str = EXTRACTION_SCHEMA_VERSION
    artifact_id: uuid.UUID
    agent_run_id: uuid.UUID
    trace_id: uuid.UUID
    source_block_ids: tuple[BlockId, ...] = Field(min_length=1, max_length=500)

    artifact_summary: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    counterparty_hints: tuple[CounterpartyHint, ...] = Field(default=(), max_length=10)
    external_identifiers: tuple[ExternalIdentifier, ...] = Field(default=(), max_length=40)
    dates: tuple[DateMention, ...] = Field(default=(), max_length=60)
    amounts: tuple[AmountMention, ...] = Field(default=(), max_length=60)
    evidence_candidates: tuple[EvidenceCandidate, ...] = Field(default=(), max_length=60)
    claim_candidates: tuple[ClaimCandidate, ...] = Field(default=(), max_length=60)
    commitment_candidates: tuple[CommitmentCandidate, ...] = Field(default=(), max_length=20)
    prospective_cues: tuple[ProspectiveCue, ...] = Field(default=(), max_length=20)
    injection_observations: tuple[InjectionObservation, ...] = Field(default=(), max_length=20)
    uncertainties: tuple[Uncertainty, ...] = Field(default=(), max_length=30)
    needs_visual_reasoning: bool = False

    model_id: Annotated[str, StringConstraints(max_length=128)]
    model_tier: Literal[ModelTier.E, ModelTier.R]
    prompt_version: Annotated[str, StringConstraints(max_length=32)]
    repaired: bool = False

    @model_validator(mode="after")
    def _validate_cross_references(self) -> ExtractionResult:
        known_blocks = set(self.source_block_ids)

        # 1. every cited block was actually supplied to the model
        cited: list[tuple[str, str]] = []
        for group in (
            self.counterparty_hints,
            self.external_identifiers,
            self.dates,
            self.amounts,
            self.evidence_candidates,
            self.prospective_cues,
            self.injection_observations,
        ):
            for item in group:
                cited.append((type(item).__name__, item.block_id))
        unknown = {(k, b) for k, b in cited if b not in known_blocks}
        if unknown:
            raise ValueError(
                "candidates cite block ids that were not supplied: "
                f"{sorted(unknown)}; a hallucinated locator is unadmittable provenance"
            )

        # 2. local ids are globally unique inside the result
        all_local_ids = [
            item.local_id
            for group in (
                self.evidence_candidates,
                self.claim_candidates,
                self.commitment_candidates,
                self.prospective_cues,
                self.injection_observations,
                self.uncertainties,
            )
            for item in group
        ]
        if len(set(all_local_ids)) != len(all_local_ids):
            raise ValueError("duplicate local_id in extraction result")

        # 3. claims resolve to a declared evidence candidate
        evidence_ids = {e.local_id for e in self.evidence_candidates}
        dangling_claims = [
            c.local_id for c in self.claim_candidates if c.evidence_local_id not in evidence_ids
        ]
        if dangling_claims:
            raise ValueError(
                f"claim candidates {dangling_claims} cite unknown evidence; "
                "no candidate without provenance may be admitted"
            )

        # 4. commitments resolve to a declared claim candidate
        claim_ids = {c.local_id for c in self.claim_candidates}
        dangling_commitments = [
            m.local_id
            for m in self.commitment_candidates
            if m.source_claim_local_id not in claim_ids
        ]
        if dangling_commitments:
            raise ValueError(f"commitment candidates {dangling_commitments} cite an unknown claim")

        # 5. uncertainties and cues reference real local ids
        universe = set(all_local_ids)
        for unc in self.uncertainties:
            missing = set(unc.affects_local_ids) - universe
            if missing:
                raise ValueError(
                    f"uncertainty {unc.local_id} references unknown ids {sorted(missing)}"
                )
        for cue in self.prospective_cues:
            if (
                cue.relative_to_claim_local_id is not None
                and cue.relative_to_claim_local_id not in claim_ids
            ):
                raise ValueError(f"cue {cue.local_id} anchors to an unknown claim candidate")
        return self

    @property
    def blocks_state_change(self) -> bool:
        """True when at least one uncertainty is severe enough that the Kernel
        must not mutate canonical state from this extraction.
        """
        return any(u.blocks_state_change for u in self.uncertainties)
