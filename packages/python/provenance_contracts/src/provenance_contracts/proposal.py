"""The typed MemoryProposal and its parts.

Kernel rule: LLM agents propose typed MemoryProposals; the deterministic
Memory Kernel is the only canonical writer. No agent gets SQL write access,
ever. This module is where that boundary is given a shape.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 12, whose code this module implements
  rather than paraphrases, plus the hero payload in section 12.3.
- ``EXECUTION/70_TASK_PLAN.md`` T1.6, first sub-task: "One proposal is one
  case -- a multi-case artifact becomes several single-case proposals sharing
  artifact and evidence references, and the type must make a cross-case
  proposal unconstructable."

One proposal is one case, structurally
--------------------------------------
There is exactly one ``case_id`` field reachable from :class:`MemoryProposal`,
on :class:`ProposalIdentity`. Nothing below the identity carries a case
reference, so a second case has nowhere to live, and ``extra="forbid"`` on
:class:`~provenance_contracts.base.Contract` means one cannot be added at call
time either. A multi-case artifact becomes several single-case proposals that
share ``source_artifact_ids`` and ``evidence_ids``.
``KernelReasonCode.INVARIANT_MULTI_CASE_PROPOSAL`` remains for the shape that
arrives as two ids the type could not see: belt and braces, not a substitute.

Recorded deviation from section 12
----------------------------------
One error message is reworded. Section 12's ``_blocked_proposals_do_not_mutate``
ends "record the claims and escalate". That literal contains a token which is
also one of the 26 canonical table names, and the no-SQL rule of
``tools/contract_lint.py`` tokenises every non-docstring literal. The rule is
correct and the message is incidental, so the message moved rather than the
rule: it now ends "record what was asserted and escalate". Nothing else about
the check changed, and no carve-out was spent on it.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from provenance_contracts.base import (
    BoundaryContract,
    Confidence,
    Contract,
    IdempotencyKey,
    LocalId,
    Money,
    ReasonCode,
    SafeIdentifier,
    UtcDatetime,
    Weight,
    validate_half_open,
)
from provenance_contracts.predicates import PredicateNode
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.derivations import is_registered_derivation
from provenance_domain.enums import (
    ActorType,
    BeliefMutationKind,
    CaseStatus,
    ClaimKind,
    CommitmentType,
    ConflictSeverity,
    ConflictType,
    EpistemicStatus,
    Modality,
    ProposalType,
    SourceClass,
    SubjectType,
    SupportRelation,
    SupportSourceKind,
    TriggerMutationKind,
    TriggerType,
    ValueType,
)

__all__ = [
    "ConflictHint",
    "DeterministicDerivation",
    "MemoryProposal",
    "ProposalIdentity",
    "ProposedBeliefMutation",
    "ProposedClaim",
    "ProposedCommitment",
    "ProposedSupportEdge",
    "ProposedTrigger",
]

Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ProposalIdentity(Contract):
    """Which relationship and case the agent believes this belongs to.

    A proposal may legitimately resolve to nothing: ``PENDING_IDENTITY`` is a
    better outcome than a confident write to the wrong case. This is also the
    single home of ``case_id`` in the whole proposal graph.
    """

    relationship_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    confidence: Confidence
    unresolved_candidates: tuple[uuid.UUID, ...] = Field(default=(), max_length=6)
    resolved_by: Literal["DETERMINISTIC", "TIER_R_RESOLVER", "USER_CONFIRMED"] = "DETERMINISTIC"


class ProposedClaim(Contract):
    """An assertion to be recorded as a claim. Never as a fact.

    In the hero scenario the June invoice becomes exactly this: a
    ``COUNTERPARTY_CLAIM`` with ``predicate="billing_period_covered"``. It is
    admitted, it is preserved, and it does not overwrite anything.
    """

    local_id: LocalId
    claim_kind: ClaimKind
    subject_type: SubjectType
    subject_id: uuid.UUID | None = None
    subject_local_ref: ShortText | None = None
    predicate: SafeIdentifier
    object_type: ValueType
    object_value: JsonValue
    actor_type: ActorType
    actor_ref: ShortText | None = None
    evidence_id: uuid.UUID
    source_class: SourceClass
    modality: Modality
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    extraction_confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> ProposedClaim:
        if not self.local_id.startswith("cl_"):
            raise ValueError("ProposedClaim.local_id must use the cl_ prefix")
        if (self.subject_id is None) == (self.subject_local_ref is None):
            raise ValueError("provide exactly one of subject_id or subject_local_ref")
        validate_half_open(self.valid_from, self.valid_to)
        return self


class ProposedCommitment(Contract):
    """An obligation to record. Amounts are Money; there is no float path."""

    local_id: LocalId
    commitment_type: CommitmentType
    description: Text
    obligor_type: ActorType
    obligor_ref: ShortText | None = None
    beneficiary_type: ActorType
    beneficiary_ref: ShortText | None = None
    committed: Money | None = None
    due_at: UtcDatetime | None = None
    due_condition: PredicateNode | None = None
    source_claim_local_id: LocalId | None = None
    source_claim_id: uuid.UUID | None = None
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> ProposedCommitment:
        if not self.local_id.startswith("cm_"):
            raise ValueError("ProposedCommitment.local_id must use the cm_ prefix")
        if (self.source_claim_local_id is None) == (self.source_claim_id is None):
            raise ValueError(
                "a commitment must originate from exactly one claim "
                "(local to this proposal, or already persisted)"
            )
        if self.committed is not None and self.committed.amount < 0:
            raise ValueError("a committed amount may not be negative")
        # `due_at is None and due_condition is None` is legal: an open-ended
        # obligation. Recorded here explicitly so it reads as a decision rather
        # than an omission.
        validate_half_open(self.valid_from, self.valid_to)
        return self


class DeterministicDerivation(Contract):
    """The only lawful excuse for a belief version with no grounding edge.

    The name and version must appear in ``provenance_domain.derivations``. An
    agent cannot invent a derivation to bypass grounding, because the registry
    is closed and lives outside the contract.
    """

    name: SafeIdentifier
    function_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    input_refs: tuple[uuid.UUID, ...] = Field(default=(), max_length=20)
    input_local_refs: tuple[LocalId, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def _must_be_registered(self) -> DeterministicDerivation:
        if not is_registered_derivation(self.name, self.function_version):
            raise ValueError(
                f"{self.name}@{self.function_version} is not a registered "
                "deterministic derivation; ungrounded belief versions are only "
                "permitted for derivations declared in provenance_domain"
            )
        if not self.input_refs and not self.input_local_refs:
            raise ValueError("a derivation must name at least one input")
        return self


class ProposedSupportEdge(Contract):
    """One grounding edge: belief version <- evidence / claim / belief version.

    The vocabulary word for the edge is "grounding"; it is never the version
    chain, which is "lineage" and lives in :mod:`provenance_contracts.proof`.
    """

    source_kind: SupportSourceKind
    source_id: uuid.UUID | None = None
    source_local_id: LocalId | None = None
    relation: SupportRelation
    weight: Weight | None = None
    reason_code: ReasonCode | None = None

    @model_validator(mode="after")
    def _validate(self) -> ProposedSupportEdge:
        if (self.source_id is None) == (self.source_local_id is None):
            raise ValueError("provide exactly one of source_id or source_local_id")
        if self.source_kind is SupportSourceKind.DERIVATION:
            raise ValueError(
                "a DERIVATION is expressed through ProposedBeliefMutation.derivation, "
                "not as a support edge"
            )
        return self


class ProposedBeliefMutation(Contract):
    """Create, revise, supersede, or retract a belief.

    The grounding invariant is enforced in :meth:`_require_grounding`: a
    canonical belief version must have at least one SUPPORTS edge unless it
    names a registered deterministic derivation. There is no third option.
    """

    local_id: LocalId
    mutation_kind: BeliefMutationKind

    belief_id: uuid.UUID | None = None
    subject_type: SubjectType | None = None
    subject_id: uuid.UUID | None = None
    subject_local_ref: ShortText | None = None
    predicate: SafeIdentifier | None = None

    value_type: ValueType | None = None
    value_json: JsonValue | None = None
    epistemic_status: EpistemicStatus
    belief_confidence: Confidence
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None

    supersedes_version_id: uuid.UUID | None = None
    reason_code: ReasonCode | None = None

    grounding: tuple[ProposedSupportEdge, ...] = Field(default=(), max_length=20)
    derivation: DeterministicDerivation | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> ProposedBeliefMutation:
        if not self.local_id.startswith("bm_"):
            raise ValueError("ProposedBeliefMutation.local_id must use the bm_ prefix")

        if self.mutation_kind is BeliefMutationKind.CREATE:
            missing = [
                name
                for name in ("subject_type", "predicate", "value_type")
                if getattr(self, name) is None
            ]
            if missing or (self.subject_id is None and self.subject_local_ref is None):
                raise ValueError(
                    "CREATE requires subject_type, a subject reference, predicate "
                    f"and value_type (missing: {missing})"
                )
            if self.belief_id is not None:
                raise ValueError("CREATE must not name an existing belief_id")
        elif self.belief_id is None:
            raise ValueError(f"{self.mutation_kind} requires belief_id")

        if self.mutation_kind in (
            BeliefMutationKind.REVISE,
            BeliefMutationKind.SUPERSEDE,
        ):
            if self.supersedes_version_id is None:
                raise ValueError(
                    f"{self.mutation_kind} must name the version it replaces so the "
                    "lineage chain stays unbroken"
                )
            if self.value_type is None:
                raise ValueError(f"{self.mutation_kind} requires value_type")

        if self.mutation_kind is BeliefMutationKind.RETRACT:
            if self.epistemic_status is not EpistemicStatus.RETRACTED:
                raise ValueError("RETRACT must record epistemic_status=RETRACTED")
            if self.reason_code is None:
                raise ValueError("a retraction without a reason code is unauditable")

        if (
            self.epistemic_status in (EpistemicStatus.SUPERSEDED, EpistemicStatus.RETRACTED)
            and self.reason_code is None
        ):
            raise ValueError(f"epistemic_status={self.epistemic_status} requires a reason_code")

        validate_half_open(self.valid_from, self.valid_to)
        return self

    @model_validator(mode="after")
    def _require_grounding(self) -> ProposedBeliefMutation:
        """THE grounding invariant.

        A retraction is exempt: it removes a belief from canonical standing
        rather than asserting one, and it carries a reason code instead.
        Everything else must either cite evidence or be a registered
        deterministic derivation.
        """
        if self.mutation_kind is BeliefMutationKind.RETRACT:
            return self
        if self.derivation is not None:
            # Belt and braces is fine, but a derivation plus contradicting
            # edges is incoherent: pick one story.
            if self.grounding and any(
                e.relation is SupportRelation.CONTRADICTS for e in self.grounding
            ):
                raise ValueError(
                    "a deterministic derivation cannot simultaneously be "
                    "contradicted by its own grounding"
                )
            return self
        supports = [e for e in self.grounding if e.relation is SupportRelation.SUPPORTS]
        if not supports:
            raise ValueError(
                f"belief mutation {self.local_id} is UNGROUNDED: a canonical belief "
                "version needs at least one SUPPORTS edge, or a registered "
                "deterministic derivation. A belief is revisable, but it is "
                "never free-floating."
            )
        return self


class ConflictHint(Contract):
    """An advisory "these two cannot both be true".

    The Kernel runs its own deterministic contradiction detection and does not
    depend on this. The hint improves reason codes and ordering; it does not
    create a conflict by itself.
    """

    local_id: LocalId
    advisory: Literal[True] = True
    conflict_type: ConflictType
    subject_type: SubjectType
    subject_id: uuid.UUID | None = None
    subject_local_ref: ShortText | None = None
    predicate: SafeIdentifier
    left_source_kind: SupportSourceKind
    left_source_id: uuid.UUID | None = None
    left_source_local_id: LocalId | None = None
    right_source_kind: SupportSourceKind
    right_source_id: uuid.UUID | None = None
    right_source_local_id: LocalId | None = None
    severity: ConflictSeverity
    requires_human_hint: bool = False
    rationale: Text
    confidence: Confidence

    @model_validator(mode="after")
    def _validate(self) -> ConflictHint:
        if not self.local_id.startswith("cf_"):
            raise ValueError("ConflictHint.local_id must use the cf_ prefix")
        for side in ("left", "right"):
            has_id = getattr(self, f"{side}_source_id") is not None
            has_local = getattr(self, f"{side}_source_local_id") is not None
            if has_id == has_local:
                raise ValueError(f"{side} side needs exactly one of source_id or source_local_id")
        if self.left_source_id is not None and self.left_source_id == self.right_source_id:
            raise ValueError("a source cannot conflict with itself")
        return self


class ProposedTrigger(Contract):
    """Arm, re-arm, disarm, or extend prospective memory."""

    local_id: LocalId
    mutation_kind: TriggerMutationKind
    trigger_id: uuid.UUID | None = None
    trigger_type: TriggerType
    predicate: PredicateNode | None = None
    not_before: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    rationale: Text

    @model_validator(mode="after")
    def _validate(self) -> ProposedTrigger:
        if not self.local_id.startswith("tg_"):
            raise ValueError("ProposedTrigger.local_id must use the tg_ prefix")
        if self.mutation_kind is TriggerMutationKind.ARM:
            if self.predicate is None:
                raise ValueError("ARM requires a predicate")
            if self.trigger_id is not None:
                raise ValueError("ARM creates a trigger and must not name one")
        elif self.trigger_id is None:
            raise ValueError(f"{self.mutation_kind} requires trigger_id")
        if (
            self.not_before is not None
            and self.expires_at is not None
            and self.expires_at <= self.not_before
        ):
            raise ValueError("expires_at must be after not_before")
        return self


class MemoryProposal(BoundaryContract):
    """The complete typed proposal an agent submits to the Memory Kernel.

    Absent by design:
      * ``tenant_id`` -- the Kernel derives tenancy from the authenticated
        internal principal's capability binding (pipeline step 2). A field the
        agent could fill in is a field an attacker could fill in.
      * any authority score -- see ``provenance_domain.authority``.
      * any SQL, table name, or permission grant.

    ``user_id`` IS present, and is a cross-check rather than a grant: pipeline
    step 3 rejects the proposal when it disagrees with the binding. A machine
    client asserting a user id it was not issued is a security event, and we
    want it to be loud rather than absent.
    """

    proposal_id: uuid.UUID
    proposal_type: ProposalType
    trace_id: uuid.UUID
    agent_run_id: uuid.UUID
    user_id: uuid.UUID

    source_artifact_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=10)
    evidence_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=60)

    identity: ProposalIdentity
    claims: tuple[ProposedClaim, ...] = Field(default=(), max_length=60)
    commitments: tuple[ProposedCommitment, ...] = Field(default=(), max_length=20)
    belief_mutations: tuple[ProposedBeliefMutation, ...] = Field(default=(), max_length=40)
    conflict_hints: tuple[ConflictHint, ...] = Field(default=(), max_length=20)
    trigger_mutations: tuple[ProposedTrigger, ...] = Field(default=(), max_length=10)

    requested_case_transition: CaseStatus | None = None
    requested_transition_reason_code: ReasonCode | None = None
    unresolved_questions: tuple[Text, ...] = Field(default=(), max_length=10)
    blocks_state_change: bool = False

    model: ModelAttribution
    idempotency_key: IdempotencyKey
    created_at: UtcDatetime

    # -- structural validation -------------------------------------------

    @model_validator(mode="after")
    def _must_propose_something(self) -> MemoryProposal:
        if not any(
            (
                self.claims,
                self.commitments,
                self.belief_mutations,
                self.conflict_hints,
                self.trigger_mutations,
            )
        ):
            raise ValueError(
                "an empty proposal is not a proposal; if nothing was learned, "
                "do not submit and let the run end with a visible NOOP status"
            )
        return self

    @model_validator(mode="after")
    def _local_ids_unique(self) -> MemoryProposal:
        ids = [
            item.local_id
            for group in (
                self.claims,
                self.commitments,
                self.belief_mutations,
                self.conflict_hints,
                self.trigger_mutations,
            )
            for item in group
        ]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate local_id in proposal: {duplicates}")
        return self

    @model_validator(mode="after")
    def _evidence_references_are_declared(self) -> MemoryProposal:
        """Every persisted evidence id used anywhere must appear in
        ``evidence_ids``, so the Kernel can load and ownership-check the whole
        set in one read at pipeline step 4 and refuse foreign provenance at
        step 5 before any write is planned.
        """
        declared = set(self.evidence_ids)

        undeclared: set[uuid.UUID] = set()
        for claim in self.claims:
            if claim.evidence_id not in declared:
                undeclared.add(claim.evidence_id)
        for mutation in self.belief_mutations:
            for edge in mutation.grounding:
                if (
                    edge.source_kind is SupportSourceKind.EVIDENCE
                    and edge.source_id is not None
                    and edge.source_id not in declared
                ):
                    undeclared.add(edge.source_id)
        if undeclared:
            raise ValueError(
                "these evidence ids are referenced but not declared in "
                f"evidence_ids: {sorted(str(u) for u in undeclared)}"
            )
        return self

    @model_validator(mode="after")
    def _local_references_resolve(self) -> MemoryProposal:
        claim_ids = {c.local_id for c in self.claims}
        known = (
            claim_ids
            | {c.local_id for c in self.commitments}
            | {b.local_id for b in self.belief_mutations}
        )

        for commitment in self.commitments:
            ref = commitment.source_claim_local_id
            if ref is not None and ref not in claim_ids:
                raise ValueError(f"commitment {commitment.local_id} cites unknown claim {ref}")
        for mutation in self.belief_mutations:
            for edge in mutation.grounding:
                if edge.source_local_id is not None and edge.source_local_id not in known:
                    raise ValueError(
                        f"belief mutation {mutation.local_id} grounds on unknown "
                        f"local reference {edge.source_local_id}"
                    )
            if mutation.derivation is not None:
                missing = set(mutation.derivation.input_local_refs) - known
                if missing:
                    raise ValueError(
                        f"derivation on {mutation.local_id} names unknown local "
                        f"inputs {sorted(missing)}"
                    )
        for hint in self.conflict_hints:
            for ref in (hint.left_source_local_id, hint.right_source_local_id):
                if ref is not None and ref not in known:
                    raise ValueError(f"conflict hint {hint.local_id} cites unknown local ref {ref}")
        return self

    @model_validator(mode="after")
    def _transition_requires_reason(self) -> MemoryProposal:
        if self.requested_case_transition is not None:
            if self.identity.case_id is None:
                raise ValueError(
                    "a case transition may only be requested when identity resolves to a case"
                )
            if self.requested_transition_reason_code is None:
                raise ValueError(
                    "requested_case_transition requires a reason code; the Kernel "
                    "checks it against the frozen guard table"
                )
        return self

    @model_validator(mode="after")
    def _blocked_proposals_do_not_mutate(self) -> MemoryProposal:
        """When extraction flagged a state-blocking uncertainty, the proposal
        may still record what was asserted -- evidence is append-only and
        always admissible -- but it may not ask to change canonical belief,
        obligations, or case state.
        """
        if self.blocks_state_change and (
            self.belief_mutations or self.commitments or self.requested_case_transition
        ):
            raise ValueError(
                "proposal declares blocks_state_change but requests belief, "
                "commitment or case mutations; record what was asserted and escalate"
            )
        return self

    # -- convenience ------------------------------------------------------

    def is_state_changing(self) -> bool:
        return bool(
            self.belief_mutations
            or self.commitments
            or self.trigger_mutations
            or self.requested_case_transition
        )

    def ungrounded_mutations(self) -> tuple[ProposedBeliefMutation, ...]:
        """Always empty for a validated proposal. Kept as an explicit
        assertion point for the Kernel's step 16 invariant sweep, so the check
        exists in the Kernel's own code path and does not rely solely on
        validation having run upstream.
        """
        return tuple(
            m
            for m in self.belief_mutations
            if m.derivation is None
            and m.mutation_kind is not BeliefMutationKind.RETRACT
            and not any(e.relation is SupportRelation.SUPPORTS for e in m.grounding)
        )
