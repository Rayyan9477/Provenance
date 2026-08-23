"""The decision pipeline, as a declarative write plan — T4.9.

Authority
---------
``specs/12_KERNEL_ALGORITHMS.md`` section 1 (the thirty steps and the PHASE A /
PHASE B split), section 6.2 (what counts as a canonical change), and
``CANONICAL_DECISIONS.md`` -> *Hero commit canon*.

Why a plan and not writes
-------------------------
Every function here is a pure function of (rows already read, proposal payload,
frozen config). It produces a :class:`WritePlan` - a declarative description of
the rows to be written - which ``transaction.py`` executes in DDL section 13
order. Two things follow, and both are the point:

* the whole decision is reachable from a unit test with no network access, no
  credentials and no model call, which is the falsifiable form of the product
  claim (``quality/20_TDD_STRATEGY.md`` section 2.1); and
* the transaction body stays small enough to review, because it contains no
  decisions - only an ordered sequence of INSERTs and UPDATEs.

Scope: the six v1 capabilities
------------------------------
New counterparty claim, new commitment, fulfillment, contradiction with an
existing belief, case reopen, prospective trigger arm/disarm. A capability
outside those six is refused with a named reason code rather than partially
handled; ``implementation/06_CODING_AGENT_HANDOFF.md`` section 6 is explicit
that scope creep here is the most expensive kind in the build.

No model call. Semantic input arrived as a typed proposal and its
interpretation is already finished.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from provenance_contracts.predicates import PredicateNode
from provenance_contracts.proposal import (
    MemoryProposal,
    ProposedClaim,
    ProposedCommitment,
    ProposedTrigger,
)
from provenance_domain.enums import (
    ActorType,
    AttentionLevel,
    CaseStatus,
    ClaimKind,
    CommitmentStatus,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    EventType,
    FulfillmentAdmissionStatus,
    KernelDecision,
    KernelReasonCode,
    SourceClass,
    SubjectType,
    TransitionType,
    TriggerMutationKind,
    TriggerReasonCode,
    TriggerState,
    TriggerType,
)
from services.control_plane.app.memory_kernel import (
    case_ops,
    contradiction,
    disposition,
    families,
    money_ops,
    preflight,
)
from services.control_plane.app.memory_kernel import propositions as prop
from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG, KernelConfig
from services.control_plane.app.memory_kernel.families import Family
from services.control_plane.app.triggers import ast as trigger_ast
from services.control_plane.app.triggers import config as trigger_config
from services.control_plane.app.triggers import outcomes as trigger_outcomes
from services.control_plane.app.triggers import registry as trigger_registry

__all__ = [
    "OBLIGOR_TYPE_FOR_ACTOR",
    "AggregateSnapshot",
    "BeliefVersionWrite",
    "BeliefWrite",
    "CaseUpdateWrite",
    "ClaimWrite",
    "CommitmentRowWrite",
    "CommitmentUpdateWrite",
    "ConflictRowWrite",
    "FulfillmentRowWrite",
    "IncumbentBelief",
    "OutboxWrite",
    "PipelineOutcome",
    "StateTransitionWrite",
    "SupersedeWrite",
    "SupportEdgeWrite",
    "TriggerArmWrite",
    "TriggerDisarmWrite",
    "WritePlan",
    "build_write_plan",
]

#: ``outbox_events.payload_version`` must match ``^[0-9]+\\.[0-9]+$``.
PAYLOAD_VERSION: Final[str] = "1.0"

#: The ``source_class`` handed to the normaliser when reconstructing an admitted
#: fulfillment as a proposition. It is a **placeholder**: the authority is
#: overwritten from the ledger row's grounding claim immediately afterwards, and
#: a member of the closed enum is used so the normaliser does not emit
#: ``AUTHORITY_UNMAPPED_SOURCE_CLASS`` about a class nobody supplied.
_LEDGER_AUTHORITY_PLACEHOLDER: Final[SourceClass] = SourceClass.PROVIDER_SYSTEM_NOTICE

#: ``ck_outbox_events_aggregate_type``.
AGGREGATE_CASE: Final[str] = "CASE"

#: ``16_TRIGGER_DSL.md`` section 9.1 precondition 5 and its INSERT: a fresh arm
#: is generation **1**, and section 9.3's ``schedule_name`` is stamped with it.
#: The column's ``DEFAULT 0`` is a column default, not the arm's value.
FRESH_ARM_EVALUATION_VERSION: Final[int] = 1

#: ``ck_outbox_events_aggregate_type`` again. ``EVENT_AGGREGATE_TYPE`` maps
#: ``trigger.armed.v1`` onto the TRIGGER aggregate, so an armed trigger's event
#: is keyed by the trigger rather than by the case - which is also what keeps
#: ``uq_outbox_events_aggregate_event`` satisfied when one commit arms two.
AGGREGATE_TRIGGER: Final[str] = "TRIGGER"

#: ``ck_commitments_obligor`` and ``ck_commitments_beneficiary`` accept three
#: values; :class:`~provenance_domain.enums.ActorType` has five. ``SYSTEM`` and
#: ``UNKNOWN`` name an actor that is neither the user nor the counterparty of
#: this relationship, which is exactly what ``THIRD_PARTY`` means, so the map is
#: total and no proposal can produce a row the CHECK refuses.
OBLIGOR_TYPE_FOR_ACTOR: Final[Mapping[ActorType, str]] = {
    ActorType.USER: "USER",
    ActorType.COUNTERPARTY: "COUNTERPARTY",
    ActorType.THIRD_PARTY: "THIRD_PARTY",
    ActorType.SYSTEM: "THIRD_PARTY",
    ActorType.UNKNOWN: "THIRD_PARTY",
}


# ---------------------------------------------------------------------------
# Row specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimWrite:
    """One ``claims`` row.

    ``predicate`` is the **surface** form the source used. Only ``beliefs``
    normalises to the family's canonical predicate (rule N1); a claim that
    silently changed its own wording would stop being a record of what was said.
    """

    claim_id: uuid.UUID
    case_id: uuid.UUID | None
    relationship_id: uuid.UUID | None
    subject_type: SubjectType
    subject_id: uuid.UUID
    predicate: str
    object_type: str
    object_json: Mapping[str, Any]
    actor_type: str
    actor_id: str | None
    evidence_id: uuid.UUID
    claim_kind: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    authority_score: Decimal | None = None
    extraction_confidence: Decimal = Decimal("1.0000")


@dataclass(frozen=True, slots=True)
class BeliefWrite:
    """One ``beliefs`` row, created only when ``exists`` is false.

    ``predicate`` here is the family's canonical predicate.
    ``uq_beliefs_proposition`` makes that Rule N1's enforcement: two surface
    forms of one family cannot become two rows that never contradict.
    """

    belief_id: uuid.UUID
    case_id: uuid.UUID | None
    subject_type: SubjectType
    subject_id: uuid.UUID
    predicate: str
    exists: bool = False


@dataclass(frozen=True, slots=True)
class SupportEdgeWrite:
    """One ``belief_support`` row: what this version rests on, or argues with."""

    edge_id: uuid.UUID
    belief_version_id: uuid.UUID
    source_kind: str
    source_id: uuid.UUID
    relation: str
    weight: Decimal | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class BeliefVersionWrite:
    """One ``belief_versions`` row and the grounding edges it is written with.

    ``support_edge_count`` is derived from the edges rather than supplied, so
    ``ck_belief_versions_grounded`` cannot be satisfied by a truthful-looking
    count with no edges behind it - the failure verification query V3 exists to
    catch.
    """

    version_id: uuid.UUID
    belief_id: uuid.UUID
    version_no: int
    value_type: str
    value_json: Mapping[str, Any]
    epistemic_status: EpistemicStatus
    belief_confidence: Decimal
    derivation_kind: str = "EVIDENCE_GROUNDED"
    supersedes_version_id: uuid.UUID | None = None
    supersession_reason_code: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    support: tuple[SupportEdgeWrite, ...] = ()

    @property
    def support_edge_count(self) -> int:
        return len(self.support)


@dataclass(frozen=True, slots=True)
class SupersedeWrite:
    """Mark the predecessor ``SUPERSEDED``, in the same transaction."""

    version_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ConflictRowWrite:
    """One ``conflicts`` row, already ordered for ``ck_conflicts_side_order``."""

    conflict_id: uuid.UUID
    case_id: uuid.UUID
    subject_type: SubjectType
    subject_id: uuid.UUID
    predicate: str
    left_source_kind: str
    left_source_id: uuid.UUID
    right_source_kind: str
    right_source_id: uuid.UUID
    conflict_type: ConflictType
    status: ConflictStatus
    severity: ConflictSeverity
    requires_human: bool
    canonical_belief_version_id: uuid.UUID | None = None
    resolution_reason_code: str | None = None
    resolution_notes: str | None = None


@dataclass(frozen=True, slots=True)
class FulfillmentRowWrite:
    """One ``fulfillments`` row. Evidence-linked and immutable once written."""

    fulfillment_id: uuid.UUID
    commitment_id: uuid.UUID
    evidence_id: uuid.UUID
    admission_status: str
    currency: str | None = None
    amount: Decimal | None = None
    quantity: Decimal | None = None
    confidence: Decimal = Decimal("1.0000")
    fulfilled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CommitmentRowWrite:
    """One ``commitments`` INSERT — the obligation this commit recorded.

    The three amounts travel together or not at all, which is M2 of
    ``0004_obligation_ledger``: with any one of them NULL, M3
    (``fulfilled <= committed``) and M4 (``outstanding = committed - fulfilled``)
    are vacuously true and the whole set stops meaning anything. A non-monetary
    obligation - the ISP termination promise - therefore carries all three as
    ``None`` and no currency, rather than a zero that would erase the difference
    between "nothing owed" and "not a money obligation".

    ``source_claim_id`` is NOT NULL and foreign-keyed: an obligation the system
    cannot trace back to something somebody said is not an obligation this
    product is willing to store.
    """

    commitment_id: uuid.UUID
    case_id: uuid.UUID
    obligor_type: str
    beneficiary_type: str
    commitment_type: str
    description: str
    source_claim_id: uuid.UUID
    status: CommitmentStatus
    #: The proposal-scoped name this obligation was authored under, taken from
    #: ``ProposedCommitment.local_id`` with its ``cm_`` prefix removed. Never a
    #: column: it exists so a trigger armed in the same commit can bind
    #: ``commitments.<name>`` to this row by name rather than by counting.
    binding_name: str = ""
    obligor_id: str | None = None
    beneficiary_id: str | None = None
    currency: str | None = None
    committed_amount: Decimal | None = None
    fulfilled_amount: Decimal | None = None
    outstanding_amount: Decimal | None = None
    due_at: datetime | None = None
    condition_ast: Mapping[str, Any] | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    revision: int = 0


@dataclass(frozen=True, slots=True)
class TriggerArmWrite:
    """One ``prospective_triggers`` INSERT, in state ``ARMED``.

    ``predicate_ast`` is the stored **spec envelope** -
    ``{ast_version, bindings, predicate}`` - built by
    ``app.triggers.ast.build_spec_document``, not a bare ``PredicateNode`` dump.
    The envelope is where ``bindings`` lives, and ``commitments.<name>.<field>``
    resolves through it: a bare node arms cleanly and then fails months later
    with ``UNBOUND_COMMITMENT``, on a row nobody remembers writing.

    ``evaluation_version`` is ``1`` on a fresh arm, not the column's ``DEFAULT
    0``: ``16_TRIGGER_DSL.md`` section 9.1's INSERT states it explicitly and
    section 9.3 derives ``schedule_name`` from it. The name *is* the wake
    identity and *is* the idempotency key, so a row at 0 with a ``-v1`` schedule
    no-ops every wake with ``STALE_SCHEDULE_GENERATION``.

    ``basis_case_revision`` is the revision this commit produced, so rule I8 -
    the evaluator compares the case's current revision against the one the
    trigger was armed at - has something true to compare against.
    """

    trigger_id: uuid.UUID
    case_id: uuid.UUID
    trigger_type: TriggerType
    predicate_ast: Mapping[str, Any]
    basis_case_revision: int
    evaluation_version: int = FRESH_ARM_EVALUATION_VERSION
    not_before: datetime | None = None
    expires_at: datetime | None = None
    schedule_name: str | None = None


@dataclass(frozen=True, slots=True)
class TriggerDisarmWrite:
    """One ``prospective_triggers`` UPDATE that stands a trigger down.

    ``last_reason_code`` is a member of the six ``DISARMED`` codes
    ``ck_prospective_triggers_last_reason`` accepts, derived from what this
    commit actually did rather than taken from the proposal: a free-text
    rationale is not a closed vocabulary, and the database refuses anything
    outside it.
    """

    trigger_id: uuid.UUID
    last_reason_code: str


@dataclass(frozen=True, slots=True)
class CommitmentUpdateWrite:
    """One ``commitments`` UPDATE, with the totals recomputed from the ledger."""

    commitment_id: uuid.UUID
    status_before: str
    status_after: str
    fulfilled_after: Decimal | None
    outstanding_after: Decimal | None
    revision_before: int
    revision_after: int
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class StateTransitionWrite:
    """One ``state_transitions`` row, at the revision that produced it."""

    transition_id: uuid.UUID
    case_id: uuid.UUID
    case_revision: int
    transition_type: TransitionType
    subject_kind: str
    subject_id: uuid.UUID | None
    from_state: str | None
    to_state: str | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class OutboxWrite:
    """One ``outbox_events`` row, written inside the same transaction.

    An outbox written after commit is not a transactional outbox and
    reintroduces the dual-write problem the design exists to avoid.
    """

    event_id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    event_type: EventType
    payload: Mapping[str, Any]
    payload_version: str = PAYLOAD_VERSION


CaseUpdateWrite = case_ops.CaseUpdate


# ---------------------------------------------------------------------------
# What the transaction read
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncumbentBelief:
    """One current belief version, as the matcher and the planner see it.

    ``proposition`` is the normalised comparable form; ``value_json`` is the
    persisted value, kept separately because a ``RETAIN_INCUMBENT_DISPUTED``
    disposition writes the *incumbent's* value into the new version and a
    round-trip through the normaliser is not the identity for every family.
    """

    belief_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    subject_type: SubjectType
    subject_id: uuid.UUID
    predicate: str
    proposition: prop.Proposition
    value_type: str = "MONEY"
    value_json: Mapping[str, Any] | None = None
    belief_confidence: Decimal = Decimal("0.9000")


@dataclass(frozen=True, slots=True)
class AggregateSnapshot:
    """Everything PHASE B read inside the transaction, and nothing else.

    Rule 2 of section 7.3: a retry re-reads all of this. Nothing here may be
    computed before ``BEGIN`` and carried in.
    """

    case: case_ops.CaseRow
    relationship_id: uuid.UUID | None = None
    case_snapshot: case_ops.CaseSnapshot = field(default_factory=case_ops.CaseSnapshot)
    incumbents: tuple[IncumbentBelief, ...] = ()
    decided_proposal_ids: frozenset[uuid.UUID] = frozenset()
    commitments: tuple[money_ops.CommitmentRow, ...] = ()
    fulfillment_ledger: Mapping[uuid.UUID, tuple[money_ops.FulfillmentRow, ...]] = field(
        default_factory=dict
    )

    def incumbent_for(
        self, subject_type: SubjectType, subject_id: uuid.UUID, predicate: str
    ) -> IncumbentBelief | None:
        for candidate in self.incumbents:
            if (
                candidate.subject_type is subject_type
                and candidate.subject_id == subject_id
                and candidate.predicate == predicate
            ):
                return candidate
        return None

    def commitment(self, commitment_id: uuid.UUID) -> money_ops.CommitmentRow | None:
        for row in self.commitments:
            if row.commitment_id == commitment_id:
                return row
        return None


@dataclass(frozen=True, slots=True)
class WritePlan:
    """The rows this commit will write, in no particular order.

    Ordering is DDL section 13's business and belongs to ``transaction.py``;
    putting it here would make the plan and the executor two places to get the
    order wrong.
    """

    claims: tuple[ClaimWrite, ...] = ()
    beliefs: tuple[BeliefWrite, ...] = ()
    belief_versions: tuple[BeliefVersionWrite, ...] = ()
    supersedes: tuple[SupersedeWrite, ...] = ()
    conflicts: tuple[ConflictRowWrite, ...] = ()
    commitments: tuple[CommitmentRowWrite, ...] = ()
    fulfillments: tuple[FulfillmentRowWrite, ...] = ()
    commitment_updates: tuple[CommitmentUpdateWrite, ...] = ()
    case_update: case_ops.CaseUpdate | None = None
    transitions: tuple[StateTransitionWrite, ...] = ()
    trigger_arms: tuple[TriggerArmWrite, ...] = ()
    trigger_disarms: tuple[TriggerDisarmWrite, ...] = ()
    outbox: tuple[OutboxWrite, ...] = ()

    def is_canonical_noop(self) -> bool:
        """Rule R2's question, answered from section 6.2's table.

        ``state_transitions`` and ``outbox_events`` are consequences, never
        reasons: a plan holding only those would be a plan that emitted an
        event about nothing. ``commitments`` and ``prospective_triggers`` are
        in section 6.2's table as canonical, so a commit that only recorded an
        obligation or only armed prospective memory still moves the revision.
        """
        return not (
            self.claims
            or self.belief_versions
            or self.conflicts
            or self.commitments
            or self.fulfillments
            or self.commitment_updates
            or self.trigger_arms
            or self.trigger_disarms
        )


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """The decision, its reasons, and the rows that follow from it."""

    decision: KernelDecision
    reason_codes: tuple[KernelReasonCode, ...] = ()
    plan: WritePlan = field(default_factory=WritePlan)
    attention_required: bool = False
    attention_level: AttentionLevel = AttentionLevel.NONE


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def build_write_plan(
    proposal: MemoryProposal,
    *,
    snapshot: AggregateSnapshot,
    tx_now: datetime,
    trace_id: uuid.UUID,
    decision_id: uuid.UUID,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> PipelineOutcome:
    """Run the PHASE B steps against *snapshot* and return the plan.

    Fresh ids are minted on every call (rule 4 of section 7.3): idempotency
    comes from ``proposal_id`` and the unique constraints, never from stable
    primary keys, so a retry that re-ran this function cannot half-collide with
    its own previous attempt.
    """
    _ = (trace_id, decision_id)

    # Step 6 / rule R6 - replay is a lookup, not a re-execution.
    if proposal.proposal_id in snapshot.decided_proposal_ids:
        return PipelineOutcome(
            decision=KernelDecision.NOOP_DUPLICATE,
            reason_codes=(KernelReasonCode.PROPOSAL_ALREADY_DECIDED,),
        )

    # Step 7 / rule R7 - one case per proposal, and it must be this one.
    case_id = proposal.identity.case_id
    if case_id is None or case_id != snapshot.case.case_id:
        return _rejected(KernelReasonCode.INVARIANT_MULTI_CASE_PROPOSAL)

    # Step 8 - a terminal case accepts nothing.
    if snapshot.case.status is CaseStatus.SUPERSEDED:
        return _rejected(KernelReasonCode.CASE_TERMINAL_SUPERSEDED)

    # Step 9 - every claim must cite evidence the proposal declared. Preflight
    # checked provenance of the declared ids; this checks the join between them.
    declared = set(proposal.evidence_ids)
    if any(claim.evidence_id not in declared for claim in proposal.claims):
        return _rejected(KernelReasonCode.CLAIM_EVIDENCE_UNLINKED)

    # Step 15 - prospective memory, refused before anything is planned when the
    # proposal asks for a capability v1 does not have. The six v1 capabilities
    # name trigger *arm* and *disarm*; REARM and EXTEND belong to the trigger
    # evaluator's own re-arm budget (`app/triggers/config.REARM_POLICY`), not to
    # a proposal, and DDL section 13 step 9 prints exactly two statements. A
    # partially handled proposal is worse than a refused one, so this is
    # fail-closed and escalated rather than silently dropped.
    if any(
        mutation.mutation_kind in (TriggerMutationKind.REARM, TriggerMutationKind.EXTEND)
        for mutation in proposal.trigger_mutations
    ):
        return PipelineOutcome(
            decision=KernelDecision.PENDING_HUMAN_REVIEW,
            reason_codes=(KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE,),
        )

    # Step 16's grounding sweep, re-run inside PHASE B. PHASE A is advisory;
    # this is the one that runs with the rows the transaction actually read.
    # Reached through the module so `PV_SABOTAGE` rebinding is visible here.
    preflight.assert_grounded(proposal)

    reasons: list[KernelReasonCode] = []
    claim_writes: list[ClaimWrite] = []
    #: local_id -> the id this plan will write, so a commitment can cite the
    #: claim it came from. ``fk_commitments_source_claim`` is NOT NULL.
    claim_ids_by_local: dict[str, uuid.UUID] = {}
    belief_writes: list[BeliefWrite] = []
    version_writes: list[BeliefVersionWrite] = []
    supersedes: list[SupersedeWrite] = []
    conflict_writes: list[ConflictRowWrite] = []
    fulfillment_writes: list[FulfillmentRowWrite] = []
    commitment_writes: list[CommitmentUpdateWrite] = []
    conflict_signals: list[case_ops.ConflictSignal] = []
    commitment_signals: list[case_ops.CommitmentSignal] = []
    claim_signals: list[case_ops.ClaimSignal] = []
    attention = AttentionLevel.NONE
    requires_human = False

    # Steps 10-16, one proposed claim at a time.
    for proposed in proposal.claims:
        claim_id = uuid.uuid4()
        claim_ids_by_local[proposed.local_id] = claim_id
        # Step 7, per claim. `ProposedClaim` takes exactly one of `subject_id`
        # and `subject_local_ref`, and the ingestion graph can only produce the
        # second, so a local ref is the common case rather than the exotic one.
        # Resolving it here means the claim row and the proposition get the SAME
        # answer; they used to get two different wrong ones.
        subject_id = _resolve_subject(proposed, snapshot)
        if subject_id is None:
            return PipelineOutcome(
                decision=KernelDecision.PENDING_IDENTITY,
                reason_codes=(KernelReasonCode.IDENTITY_UNRESOLVED,),
            )
        normalised = _normalise(proposed, subject_id, tx_now=tx_now, cfg=cfg)
        reasons.extend(normalised.reason_codes)
        proposition = normalised.proposition
        authority = None if proposition is None else proposition.authority

        claim_writes.append(
            ClaimWrite(
                claim_id=claim_id,
                case_id=case_id,
                relationship_id=snapshot.relationship_id,
                subject_type=proposed.subject_type,
                subject_id=subject_id,
                predicate=proposed.predicate,
                object_type=str(proposed.object_type),
                object_json=_as_json(proposed.object_value),
                actor_type=str(proposed.actor_type),
                actor_id=proposed.actor_ref,
                evidence_id=proposed.evidence_id,
                claim_kind=str(proposed.claim_kind),
                valid_from=proposed.valid_from,
                valid_to=proposed.valid_to,
                authority_score=authority,
                extraction_confidence=proposed.extraction_confidence,
            )
        )
        claim_signals.append(
            case_ops.ClaimSignal(
                claim_kind=str(proposed.claim_kind),
                disputes_case_belief=proposed.claim_kind
                in (ClaimKind.USER_CLAIM, ClaimKind.CORRECTION),
            )
        )

        if proposition is None:
            # An unmapped surface predicate is admitted as a claim and never
            # reaches the matcher (section 2.1). It is still canonical - section
            # 6.2: "admitting a claim is a memory change even if no belief
            # moves" - and it needs a reason code, because every other code in
            # this function is downstream of a mapped family and
            # `decisions.build_decision_row` refuses an accepted row with none.
            # Observed live as `ValueError: ACCEPTED was built with no reason
            # code` on the `movers-scheduling` fixture, whose two scheduling
            # claims are the only curated ones outside the registry.
            #
            # The code is chosen from the closed catalogue rather than invented:
            # section 9.3 has no CLAIM_ADMITTED member and CANONICAL_DECISIONS
            # -> *Closed domain vocabularies* forbids a layer-local alias, so
            # the code that already means "a predicate outside the v1 registry"
            # carries it. Section 2.9 reaches it through the same family lookup
            # on the conflict-hint path.
            _note_inert(reasons)
            continue

        # Step 15 - money. PAYMENT claims against a known commitment drive the
        # ledger and produce no belief (section 2.7).
        if proposition.family is Family.PAYMENT:
            # Polarity FIRST. `payment_not_received` normalises to
            # `PaymentValue(asserted=False)` and denies that a payment arrived;
            # handing it to `apply_fulfillment` credits the ledger with the sum
            # the counterparty says was never sent. `M5` cannot catch that,
            # because this branch never reaches `contradiction.match`.
            if _is_denial(proposition):
                denial = _apply_denial(proposed, proposition, snapshot, tx_now=tx_now, cfg=cfg)
                reasons.extend(denial.reasons)
                requires_human = requires_human or denial.requires_human
                attention = _max_attention(attention, denial.attention)
                conflict_writes.extend(denial.conflicts)
                conflict_signals.extend(denial.signals)
                if denial.commitment_update is not None:
                    commitment_writes.append(denial.commitment_update)
                    commitment_signals.append(
                        case_ops.CommitmentSignal(
                            status_before=denial.commitment_update.status_before,
                            status_after=denial.commitment_update.status_after,
                        )
                    )
                if not denial.conflicts and denial.commitment_update is None:
                    _note_inert(reasons)
                continue
            delta = _apply_payment(proposed, proposition, snapshot, tx_now=tx_now, cfg=cfg)
            if delta is None:
                # Section 2.7: a payment naming no commitment this transaction
                # read is still admitted as a claim and simply moves no ledger.
                # It is inert, and an inert claim has to say so.
                _note_inert(reasons)
            if delta is not None:
                reasons.extend(delta.reasons)
                if delta.fulfillment is not None:
                    fulfillment_writes.append(
                        FulfillmentRowWrite(
                            fulfillment_id=uuid.uuid4(),
                            commitment_id=delta.commitment_id,
                            evidence_id=delta.fulfillment.evidence_id,
                            admission_status=str(delta.fulfillment.admission_status),
                            currency=delta.fulfillment.currency,
                            amount=delta.fulfillment.amount,
                            quantity=delta.fulfillment.quantity,
                            confidence=delta.fulfillment.confidence,
                            fulfilled_at=delta.fulfillment.fulfilled_at or tx_now,
                        )
                    )
                if delta.status_after is not delta.status_before or delta.fulfillment is not None:
                    # The UPDATE follows the STATUS as well as the money. A
                    # currency rejection moves the commitment to DISPUTED and
                    # writes no admitted fulfillment; emitting only the signal
                    # left the recorded status disagreeing with the decision.
                    commitment_writes.append(
                        CommitmentUpdateWrite(
                            commitment_id=delta.commitment_id,
                            status_before=str(delta.status_before),
                            status_after=str(delta.status_after),
                            fulfilled_after=delta.fulfilled_after,
                            outstanding_after=delta.outstanding_after,
                            revision_before=delta.revision_after - 1,
                            revision_after=delta.revision_after,
                            currency=delta.currency,
                        )
                    )
                    commitment_signals.append(
                        case_ops.CommitmentSignal(
                            status_before=str(delta.status_before),
                            status_after=str(delta.status_after),
                        )
                    )
                for written in delta.conflicts:
                    requires_human = requires_human or written.requires_human
                    conflict_writes.append(
                        _money_conflict(written, case_id, proposition, delta.commitment_id)
                    )
                    conflict_signals.append(
                        case_ops.ConflictSignal(case_id=case_id, severity=written.severity)
                    )
            continue

        if not families.produces_belief(proposition.family):
            _note_inert(reasons)
            continue

        # Steps 12-14 and 16 - contradiction, disposition, the new version.
        canonical = families.canonical_predicate(proposition.family)
        incumbent = snapshot.incumbent_for(
            proposition.subject_type, proposition.subject_id, canonical
        )
        version_id = uuid.uuid4()

        if incumbent is None:
            verdict = disposition.decide_no_incumbent(proposition.authority, cfg)
            belief_id = uuid.uuid4()
            belief_writes.append(
                BeliefWrite(
                    belief_id=belief_id,
                    case_id=case_id,
                    subject_type=proposition.subject_type,
                    subject_id=proposition.subject_id,
                    predicate=canonical,
                    exists=False,
                )
            )
            version_writes.append(
                BeliefVersionWrite(
                    version_id=version_id,
                    belief_id=belief_id,
                    version_no=1,
                    value_type=str(proposed.object_type),
                    value_json=_as_json(proposed.object_value),
                    epistemic_status=verdict.epistemic_status_after or EpistemicStatus.PROBABLE,
                    belief_confidence=proposition.authority,
                    valid_from=proposition.valid_from,
                    valid_to=proposition.valid_to,
                    support=(
                        SupportEdgeWrite(
                            edge_id=uuid.uuid4(),
                            belief_version_id=version_id,
                            source_kind="CLAIM",
                            source_id=claim_id,
                            relation="SUPPORTS",
                            weight=proposition.authority,
                        ),
                    ),
                )
            )
            reasons.append(verdict.reason_code)
            attention = _max_attention(attention, verdict.case_attention)
            continue

        finding = contradiction.match(
            incumbent.proposition, proposition, contradiction.MatchContext(), cfg
        )
        belief_writes.append(
            BeliefWrite(
                belief_id=incumbent.belief_id,
                case_id=case_id,
                subject_type=incumbent.subject_type,
                subject_id=incumbent.subject_id,
                predicate=canonical,
                exists=True,
            )
        )

        if finding is None:
            # No contradiction: the challenger simply becomes the new version.
            verdict = disposition.decide_no_incumbent(proposition.authority, cfg)
            version_writes.append(
                _successor(
                    version_id=version_id,
                    incumbent=incumbent,
                    proposed=proposed,
                    proposition=proposition,
                    verdict=verdict,
                    claim_id=claim_id,
                    contradicts=False,
                    cfg=cfg,
                )
            )
            supersedes.append(SupersedeWrite(version_id=incumbent.version_id))
            reasons.append(verdict.reason_code)
            continue

        verdict = disposition.decide(finding, cfg)
        reasons.extend(finding.reason_codes)
        reasons.append(verdict.reason_code)
        requires_human = requires_human or verdict.requires_human
        attention = _max_attention(attention, verdict.case_attention)

        version_writes.append(
            _successor(
                version_id=version_id,
                incumbent=incumbent,
                proposed=proposed,
                proposition=proposition,
                verdict=verdict,
                claim_id=claim_id,
                contradicts=True,
                cfg=cfg,
            )
        )
        supersedes.append(SupersedeWrite(version_id=incumbent.version_id))
        conflict_writes.append(
            _conflict_row(finding, verdict, case_id=case_id, canonical_version_id=version_id)
        )
        conflict_signals.append(case_ops.ConflictSignal(case_id=case_id, severity=finding.severity))

    # Step 15 - the obligations this proposal records. A commitment is planned
    # from `proposal.commitments`, which `build_write_plan` never read until
    # now: the four commitments `scripts/seed/proposals.py` authors validated,
    # were persisted inside the `memory_proposals` payload, and then had nowhere
    # to go.
    commitment_rows = [
        _commitment_row(proposed, case_id, claim_ids_by_local, tx_now=tx_now, cfg=cfg)
        for proposed in proposal.commitments
    ]
    for row in commitment_rows:
        if row.status is CommitmentStatus.EXPIRED:
            reasons.append(KernelReasonCode.COMMITMENT_EXPIRED)
        elif row.status is CommitmentStatus.FULFILLED:
            reasons.append(KernelReasonCode.COMMITMENT_FULFILLED)

    disarming = [
        mutation
        for mutation in proposal.trigger_mutations
        if mutation.mutation_kind is TriggerMutationKind.DISARM
    ]
    arming = [
        mutation
        for mutation in proposal.trigger_mutations
        if mutation.mutation_kind is TriggerMutationKind.ARM
    ]
    if arming:
        reasons.append(KernelReasonCode.TRIGGER_ARMED)
    if disarming:
        reasons.append(KernelReasonCode.TRIGGER_DISARMED_RESOLVED)

    changed = bool(
        claim_writes
        or version_writes
        or conflict_writes
        or commitment_rows
        or fulfillment_writes
        or commitment_writes
        or arming
        or disarming
    )
    if not changed:
        return PipelineOutcome(
            decision=KernelDecision.NOOP_DUPLICATE,
            reason_codes=(KernelReasonCode.NO_CANONICAL_CHANGE,),
        )

    # Steps 18-19 - the case aggregate.
    basis = case_ops.ReopenBasis(
        evidence_ids=tuple(proposal.evidence_ids),
        artifact_hashes=(),
        conflicts=tuple(conflict_signals),
        commitment_deltas=tuple(commitment_signals),
        claims=tuple(claim_signals),
    )
    requested = (
        [proposal.requested_case_transition]
        if proposal.requested_case_transition is not None
        else []
    )
    try:
        case_update = case_ops.plan_case_update(
            snapshot.case,
            requested=requested,
            reason_code=proposal.requested_transition_reason_code,
            basis=basis,
            snapshot=snapshot.case_snapshot,
            changed=True,
            attention=_max_attention(snapshot.case.attention_level, attention),
            cfg=cfg,
        )
    except case_ops.MultipleTransitionsError:
        return _rejected(KernelReasonCode.CASE_TRANSITION_MULTIPLE_IN_COMMIT)
    except case_ops.IllegalCaseTransitionError:
        return _rejected(KernelReasonCode.CASE_TRANSITION_ILLEGAL)

    reasons.extend(case_update.reason_codes)

    # Steps 20-21 - the audit ledger and the outbox, both at the new revision.
    revision = case_update.revision_after
    transitions: list[StateTransitionWrite] = []
    outbox: list[OutboxWrite] = []
    # Armed here rather than above because ``basis_case_revision`` is the
    # revision this commit *produces*: rule R3, and the number the evaluator
    # compares against when the scheduler wakes it months later.
    # Built here rather than beside `arming` because the precedence reads the
    # status this commit is MOVING the case to, and that is `case_update`'s
    # answer. The trigger evaluator passes the case's *current* status to the
    # same function; both are correct for their context, so neither should later
    # be "fixed" to match the other.
    disarm_writes = [
        TriggerDisarmWrite(
            trigger_id=_required_trigger_id(mutation),
            last_reason_code=_disarm_reason(case_update, commitment_writes),
        )
        for mutation in disarming
    ]

    arm_writes: list[TriggerArmWrite] = []
    for mutation in arming:
        armed = _trigger_arm(
            mutation,
            case_id=case_id,
            revision=revision,
            created=commitment_rows,
            snapshot=snapshot,
        )
        if armed is None:
            return PipelineOutcome(
                decision=KernelDecision.PENDING_HUMAN_REVIEW,
                reason_codes=(KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE,),
            )
        arm_writes.append(armed)
    if case_update.status_moves:
        transitions.append(
            StateTransitionWrite(
                transition_id=uuid.uuid4(),
                case_id=case_id,
                case_revision=revision,
                transition_type=TransitionType.CASE_STATUS,
                subject_kind="CASE",
                subject_id=case_id,
                from_state=str(case_update.status_before),
                to_state=str(case_update.status_after),
                reason_code=case_update.reason_code,
            )
        )
        outbox.append(
            OutboxWrite(
                event_id=uuid.uuid4(),
                aggregate_type=AGGREGATE_CASE,
                aggregate_id=case_id,
                aggregate_version=revision,
                event_type=(
                    EventType.CASE_REOPENED
                    if case_update.status_after is CaseStatus.REOPENED
                    else EventType.CASE_STATE_CHANGED
                ),
                payload={
                    "case_id": str(case_id),
                    "from": str(case_update.status_before),
                    "to": str(case_update.status_after),
                    "reason_code": case_update.reason_code,
                    "revision": revision,
                },
            )
        )
    for row in commitment_rows:
        transitions.append(
            StateTransitionWrite(
                transition_id=uuid.uuid4(),
                case_id=case_id,
                case_revision=revision,
                transition_type=TransitionType.COMMITMENT_STATUS,
                subject_kind="COMMITMENT",
                subject_id=row.commitment_id,
                # A commitment that did not exist has no prior state, and
                # ``ck_state_transitions_moves`` is satisfied because NULL is
                # distinct from the status it was opened at.
                from_state=None,
                to_state=str(row.status),
                reason_code=str(KernelReasonCode.COMMITMENT_PARTIAL_RECOMPUTED),
            )
        )
    if commitment_rows:
        # ONE event per commit, not one per commitment.
        # ``uq_outbox_events_aggregate_event`` is
        # (aggregate_type, aggregate_id, aggregate_version, event_type) and
        # ``EVENT_AGGREGATE_TYPE`` maps commitment.created.v1 onto the CASE, so
        # a proposal carrying two commitments would collide with itself.
        outbox.append(
            OutboxWrite(
                event_id=uuid.uuid4(),
                aggregate_type=AGGREGATE_CASE,
                aggregate_id=case_id,
                aggregate_version=revision,
                event_type=EventType.COMMITMENT_CREATED,
                payload={
                    "case_id": str(case_id),
                    "revision": revision,
                    "commitments": [
                        {
                            "commitment_id": str(row.commitment_id),
                            "commitment_type": row.commitment_type,
                            "status": str(row.status),
                            "currency": row.currency,
                            "outstanding": (
                                None
                                if row.outstanding_amount is None
                                else str(row.outstanding_amount)
                            ),
                            "due_at": None if row.due_at is None else row.due_at.isoformat(),
                        }
                        for row in commitment_rows
                    ],
                },
            )
        )
    for arm in arm_writes:
        transitions.append(
            StateTransitionWrite(
                transition_id=uuid.uuid4(),
                case_id=case_id,
                case_revision=revision,
                transition_type=TransitionType.TRIGGER_STATE,
                subject_kind="TRIGGER",
                subject_id=arm.trigger_id,
                from_state=None,
                to_state=str(TriggerState.ARMED),
                reason_code=str(KernelReasonCode.TRIGGER_ARMED),
            )
        )
        outbox.append(
            OutboxWrite(
                event_id=uuid.uuid4(),
                aggregate_type=AGGREGATE_TRIGGER,
                aggregate_id=arm.trigger_id,
                aggregate_version=revision,
                event_type=EventType.TRIGGER_ARMED,
                payload={
                    "trigger_id": str(arm.trigger_id),
                    "case_id": str(case_id),
                    "trigger_type": str(arm.trigger_type),
                    "not_before": (None if arm.not_before is None else arm.not_before.isoformat()),
                    "expires_at": (None if arm.expires_at is None else arm.expires_at.isoformat()),
                    "basis_case_revision": arm.basis_case_revision,
                },
            )
        )
    for disarm in disarm_writes:
        # No trigger-disarmed event exists in the closed vocabulary of
        # ``ck_outbox_events_event_type``; the ledger row is the whole record.
        transitions.append(
            StateTransitionWrite(
                transition_id=uuid.uuid4(),
                case_id=case_id,
                case_revision=revision,
                transition_type=TransitionType.TRIGGER_STATE,
                subject_kind="TRIGGER",
                subject_id=disarm.trigger_id,
                from_state=str(TriggerState.ARMED),
                to_state=str(TriggerState.DISARMED),
                reason_code=str(KernelReasonCode.TRIGGER_DISARMED_RESOLVED),
            )
        )
    for update in commitment_writes:
        transitions.append(
            StateTransitionWrite(
                transition_id=uuid.uuid4(),
                case_id=case_id,
                case_revision=revision,
                transition_type=TransitionType.COMMITMENT_STATUS,
                subject_kind="COMMITMENT",
                subject_id=update.commitment_id,
                from_state=update.status_before,
                to_state=update.status_after,
                reason_code=str(KernelReasonCode.COMMITMENT_PARTIAL_RECOMPUTED),
            )
        )
        if update.status_before != update.status_after:
            outbox.append(
                OutboxWrite(
                    event_id=uuid.uuid4(),
                    aggregate_type=AGGREGATE_CASE,
                    aggregate_id=case_id,
                    aggregate_version=revision,
                    event_type=(
                        EventType.COMMITMENT_FULFILLED
                        if update.status_after == "FULFILLED"
                        else EventType.COMMITMENT_PARTIALLY_FULFILLED
                    ),
                    payload={
                        "commitment_id": str(update.commitment_id),
                        "outstanding": str(update.outstanding_after),
                        "status": update.status_after,
                        "revision": revision,
                    },
                )
            )

    plan = WritePlan(
        claims=tuple(claim_writes),
        beliefs=tuple(belief_writes),
        belief_versions=tuple(version_writes),
        supersedes=tuple(supersedes),
        conflicts=tuple(conflict_writes),
        commitments=tuple(commitment_rows),
        fulfillments=tuple(fulfillment_writes),
        commitment_updates=tuple(commitment_writes),
        case_update=case_update,
        transitions=tuple(transitions),
        trigger_arms=tuple(arm_writes),
        trigger_disarms=tuple(disarm_writes),
        outbox=tuple(outbox),
    )
    decision = KernelDecision.ACCEPTED_WITH_CONFLICT if conflict_writes else KernelDecision.ACCEPTED
    return PipelineOutcome(
        decision=decision,
        reason_codes=_dedupe(reasons),
        plan=plan,
        attention_required=bool(conflict_writes) or requires_human,
        attention_level=case_update.attention_after,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _rejected(code: KernelReasonCode) -> PipelineOutcome:
    return PipelineOutcome(decision=KernelDecision.REJECTED_INVARIANT, reason_codes=(code,))


def _dedupe(codes: Sequence[KernelReasonCode]) -> tuple[KernelReasonCode, ...]:
    """First occurrence wins, so a golden-file test can assert the order."""
    return tuple(dict.fromkeys(codes))


def _max_attention(left: AttentionLevel, right: AttentionLevel) -> AttentionLevel:
    order = (
        AttentionLevel.NONE,
        AttentionLevel.INFO,
        AttentionLevel.ATTENTION,
        AttentionLevel.URGENT,
    )
    return left if order.index(left) >= order.index(right) else right


def _as_json(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {"value": value}


def _money_ready(raw: object) -> object:
    """Turn JSON money into ``Decimal`` exactly once, at the boundary.

    ``MemoryProposal`` is JSON over the wire, and money crosses it as a
    **string** because a float would already have lost the cent by the time it
    arrived. ``families.normalize_money`` refuses anything that is not a
    ``Decimal``, deliberately, so the conversion has to happen somewhere
    explicit. Here is that somewhere: one place, on the way in, never in a
    comparison and never in arithmetic.
    """
    if not isinstance(raw, Mapping):
        return raw
    converted: dict[str, Any] = dict(raw)
    for key in ("amount", "quantity"):
        value = converted.get(key)
        if isinstance(value, str | int) and not isinstance(value, bool):
            try:
                converted[key] = Decimal(str(value))
            except ArithmeticError:  # pragma: no cover - refused downstream
                return raw
    return converted


def _normalise(
    proposed: ProposedClaim,
    subject_id: uuid.UUID,
    *,
    tx_now: datetime,
    cfg: KernelConfig,
) -> prop.NormalizationResult:
    """Step 10. Validity basis is read off the claim, never invented (rule T2).

    *subject_id* is the **resolved** subject, passed in rather than read off the
    claim. It used to fall back to ``uuid.UUID(int=0)`` whenever the claim
    carried a local ref instead of an id, which is every claim the ingestion
    graph produces: the proposition's subject was then the nil UUID,
    ``AggregateSnapshot.incumbent_for`` never matched, and every belief was
    created fresh at ``version_no = 1`` with no supersession and no conflict.
    """
    basis = prop.ValidityBasis.UNKNOWN
    if proposed.valid_from is not None:
        basis = (
            prop.ValidityBasis.EXPLICIT
            if proposed.valid_to is not None
            else prop.ValidityBasis.EXPLICIT_OPEN
        )
    return prop.normalize_claim(
        prop_id=uuid.uuid4(),
        subject_type=proposed.subject_type,
        subject_id=subject_id,
        predicate=proposed.predicate,
        raw_value=_money_ready(proposed.object_value),
        source_class=str(proposed.source_class),
        claim_kind=proposed.claim_kind,
        valid_from=proposed.valid_from,
        valid_to=proposed.valid_to,
        validity_basis=basis,
        recorded_at=tx_now,
        actor_ref=proposed.actor_ref,
        cfg=cfg,
    )


def _resolve_subject(proposed: ProposedClaim, snapshot: AggregateSnapshot) -> uuid.UUID | None:
    """The subject this claim is about, or ``None`` when nothing can say.

    ``subject_local_ref`` is a **proposal-scoped name** -- a hint the extractor
    wrote, like ``"northline-old-account"`` -- and the Kernel is where a
    proposal becomes rows, so this is where it has to become an id. Two rules,
    both deterministic, both consequences of rule ``R7`` (one proposal is one
    case):

    * ``CASE`` -> the proposal's case. There is exactly one.
    * ``RELATIONSHIP`` -> the case's relationship. There is exactly one, and the
      transaction read it.

    Everything else returns ``None`` and the commit becomes ``PENDING_IDENTITY``
    with ``IDENTITY_UNRESOLVED``. That is the point. The previous behaviour was
    ``proposed.subject_id or snapshot.case.case_id``, which wrote the **case
    id** into a row declaring ``subject_type = 'RELATIONSHIP'`` -- a confident
    write to the wrong subject, and one that ``uq_beliefs_proposition``
    (``(tenant_id, user_id, subject_type, subject_id, predicate)``, no
    ``case_id``) then collapsed across cases.
    """
    if proposed.subject_id is not None:
        return proposed.subject_id
    if proposed.subject_type is SubjectType.CASE:
        return snapshot.case.case_id
    if proposed.subject_type is SubjectType.RELATIONSHIP:
        return snapshot.relationship_id
    return None


def _successor(
    *,
    version_id: uuid.UUID,
    incumbent: IncumbentBelief,
    proposed: ProposedClaim,
    proposition: prop.Proposition,
    verdict: disposition.Disposition,
    claim_id: uuid.UUID,
    contradicts: bool,
    cfg: KernelConfig,
) -> BeliefVersionWrite:
    """The new ``belief_versions`` row a disposition implies.

    ``value_changes`` is the whole of it: when the disposition retains the
    incumbent, the successor carries the **incumbent's** value and only its
    epistemic status moves. That is the hero - the balance stays USD 0 while the
    belief becomes ``DISPUTED`` - and writing the challenger's value here would
    silently promote a contested number to canonical.
    """
    if verdict.value_changes:
        value_type = str(proposed.object_type)
        value_json = _as_json(proposed.object_value)
        valid_from, valid_to = proposition.valid_from, proposition.valid_to
        confidence = proposition.authority
    else:
        value_type = incumbent.value_type
        value_json = (
            incumbent.value_json
            if incumbent.value_json is not None
            else _value_to_json(incumbent.proposition.value)
        )
        valid_from = incumbent.proposition.valid_from
        valid_to = incumbent.proposition.valid_to
        confidence = (
            disposition.disputed_confidence(incumbent.belief_confidence, proposition.authority, cfg)
            if verdict.epistemic_status_after is EpistemicStatus.DISPUTED
            else incumbent.belief_confidence
        )

    support = [
        SupportEdgeWrite(
            edge_id=uuid.uuid4(),
            belief_version_id=version_id,
            source_kind="CLAIM",
            source_id=claim_id,
            relation="SUPPORTS" if verdict.value_changes else "QUALIFIES",
            weight=proposition.authority,
        )
    ]
    if contradicts:
        support.append(
            SupportEdgeWrite(
                edge_id=uuid.uuid4(),
                belief_version_id=version_id,
                source_kind="BELIEF_VERSION",
                source_id=incumbent.version_id,
                relation="CONTRADICTS",
                reason_code=str(verdict.reason_code),
            )
        )
    return BeliefVersionWrite(
        version_id=version_id,
        belief_id=incumbent.belief_id,
        version_no=incumbent.version_no + 1,
        value_type=value_type,
        value_json=value_json,
        epistemic_status=verdict.epistemic_status_after or EpistemicStatus.PROBABLE,
        belief_confidence=confidence,
        supersedes_version_id=incumbent.version_id,
        supersession_reason_code=str(verdict.reason_code),
        valid_from=valid_from,
        valid_to=valid_to,
        support=tuple(support),
    )


def _value_to_json(value: families.FamilyValue) -> Mapping[str, Any]:
    """Serialise a normalised family value. Money is a string, never a float."""
    if isinstance(value, families.ServiceStatusValue):
        return {"state": str(value.state)}
    if isinstance(value, families.BalanceValue):
        return {"currency": value.currency, "amount": str(value.amount)}
    if isinstance(value, families.PaymentValue):
        return {
            "currency": value.currency,
            "amount": str(value.amount),
            "paid_at": value.paid_at.isoformat(),
            "asserted": value.asserted,
        }
    if isinstance(value, families.OutstandingValue):
        return {
            "currency": value.currency,
            "amount": str(value.amount),
            "commitment_id": str(value.commitment_id),
        }
    return {"withdrawn": value.withdrawn, "commitment_id": str(value.commitment_id)}


def _conflict_row(
    finding: contradiction.ConflictFinding,
    verdict: disposition.Disposition,
    *,
    case_id: uuid.UUID,
    canonical_version_id: uuid.UUID | None,
) -> ConflictRowWrite:
    """Map a finding plus its disposition onto one ``conflicts`` row.

    ``ck_conflicts_requires_human_consistent`` forbids ``requires_human`` on an
    ``AUTO_RESOLVED`` row and ``ck_conflicts_terminal_needs_resolution`` demands
    a reason code on a settled one, so both are decided here rather than left
    for the database to discover.
    """
    status = verdict.conflict_status or ConflictStatus.OPEN
    settled = status in (ConflictStatus.AUTO_RESOLVED, ConflictStatus.RESOLVED)
    return ConflictRowWrite(
        conflict_id=uuid.uuid4(),
        case_id=case_id,
        subject_type=finding.subject_type,
        subject_id=finding.subject_id,
        predicate=finding.predicate,
        left_source_kind=finding.left_source_kind,
        left_source_id=finding.left_source_id,
        right_source_kind=finding.right_source_kind,
        right_source_id=finding.right_source_id,
        conflict_type=finding.conflict_type,
        status=status,
        severity=finding.severity,
        requires_human=verdict.requires_human and not settled,
        canonical_belief_version_id=canonical_version_id,
        resolution_reason_code=str(verdict.reason_code) if settled else None,
        resolution_notes=f"matcher {finding.matcher_rule}" if settled else None,
    )


def _money_conflict(
    written: money_ops.ConflictWrite,
    case_id: uuid.UUID,
    proposition: prop.Proposition,
    commitment_id: uuid.UUID,
) -> ConflictRowWrite:
    """A conflict the monetary algorithm raised, given its two sides.

    Both sides are the commitment: over-fulfilment and a currency rejection are
    statements about one obligation, and ``ck_conflicts_distinct_sides`` is
    satisfied by differing kinds rather than by inventing a second id.
    """
    settled = written.status in (ConflictStatus.AUTO_RESOLVED, ConflictStatus.RESOLVED)
    # `ck_conflicts_side_order` is `left_source_id <= right_source_id`, and it is
    # what makes `uq_conflicts_live_identity` real: an index on an unordered pair
    # is defeated by swapping the arguments. The kinds travel with their own ids
    # rather than being fixed to a position.
    sides = sorted(
        (("COMMITMENT", commitment_id), ("EVIDENCE", proposition.prop_id)),
        key=lambda side: side[1],
    )
    (left_kind, left_id), (right_kind, right_id) = sides
    return ConflictRowWrite(
        conflict_id=uuid.uuid4(),
        case_id=case_id,
        subject_type=SubjectType.COMMITMENT,
        subject_id=commitment_id,
        predicate=families.canonical_predicate(Family.OUTSTANDING),
        left_source_kind=left_kind,
        left_source_id=left_id,
        right_source_kind=right_kind,
        right_source_id=right_id,
        conflict_type=written.conflict_type,
        status=written.status,
        severity=written.severity,
        requires_human=written.requires_human and not settled,
        resolution_reason_code=str(written.reason_code) if settled else None,
        resolution_notes=written.notes if settled else None,
    )


def _commitment_row(
    proposed: ProposedCommitment,
    case_id: uuid.UUID,
    claim_ids_by_local: Mapping[str, uuid.UUID],
    *,
    tx_now: datetime,
    cfg: KernelConfig,
) -> CommitmentRowWrite:
    """One ``commitments`` row from one :class:`ProposedCommitment`.

    The opening projection is :func:`money_ops.open_commitment` rather than
    three assignments here, so ``outstanding`` is the money identity applied to
    an empty ledger and the status is section 4.4's function - the same two
    rules a fulfillment would run later. Copying ``committed`` into
    ``outstanding`` would agree with every fixture and be the one place the
    identity was never enforced.
    """
    opening = money_ops.open_commitment(
        committed_amount=None if proposed.committed is None else proposed.committed.amount,
        currency=None if proposed.committed is None else proposed.committed.currency,
        tx_now=tx_now,
        # `due_condition` is an ACTIVATION condition, not a fulfillment test
        # (section 4.4). Its three-valued result belongs to the trigger DSL and
        # is not evaluated inside this transaction, so an admitted conditional
        # promise opens PROPOSED and is activated by a later commit.
        has_condition=proposed.due_condition is not None,
        condition_result=None,
        valid_to=proposed.valid_to,
        cfg=cfg,
    )
    return CommitmentRowWrite(
        commitment_id=uuid.uuid4(),
        case_id=case_id,
        binding_name=proposed.local_id.removeprefix("cm_"),
        obligor_type=OBLIGOR_TYPE_FOR_ACTOR[proposed.obligor_type],
        obligor_id=proposed.obligor_ref,
        beneficiary_type=OBLIGOR_TYPE_FOR_ACTOR[proposed.beneficiary_type],
        beneficiary_id=proposed.beneficiary_ref,
        commitment_type=str(proposed.commitment_type),
        description=proposed.description,
        source_claim_id=_source_claim_id(proposed, claim_ids_by_local),
        status=opening.status,
        currency=opening.currency,
        committed_amount=opening.committed_amount,
        fulfilled_amount=opening.fulfilled_amount,
        outstanding_amount=opening.outstanding_amount,
        due_at=proposed.due_at,
        condition_ast=(
            None
            if proposed.due_condition is None
            else proposed.due_condition.model_dump(mode="json")
        ),
        valid_from=proposed.valid_from,
        valid_to=proposed.valid_to,
    )


def _source_claim_id(
    proposed: ProposedCommitment, claim_ids_by_local: Mapping[str, uuid.UUID]
) -> uuid.UUID:
    """The claim this obligation came from. Exactly one of the two references.

    ``ProposedCommitment`` already refuses both and neither, and
    ``MemoryProposal._local_references_resolve`` already refuses a local id no
    claim in the proposal carries, so the lookup below cannot miss for a
    validated proposal. It is written as an explicit failure anyway, because a
    ``KeyError`` inside a serializable transaction is a 500 with no reason code
    and this is the one place the two validators could be bypassed - by a
    caller that built the plan from a hand-made proposal in a test.
    """
    if proposed.source_claim_id is not None:
        return proposed.source_claim_id
    local = proposed.source_claim_local_id
    resolved = None if local is None else claim_ids_by_local.get(local)
    if resolved is None:
        raise UnresolvedCommitmentSourceError(
            f"commitment {proposed.local_id} cites claim {local!r}, which this "
            "plan does not write; fk_commitments_source_claim is NOT NULL"
        )
    return resolved


class UnresolvedCommitmentSourceError(RuntimeError):
    """A commitment cited a local claim the plan does not contain."""

    code: Final[KernelReasonCode] = KernelReasonCode.CLAIM_EVIDENCE_UNLINKED


def _trigger_arm(
    mutation: ProposedTrigger,
    *,
    case_id: uuid.UUID,
    revision: int,
    created: Sequence[CommitmentRowWrite],
    snapshot: AggregateSnapshot,
) -> TriggerArmWrite | None:
    """One armed trigger, or ``None`` when it cannot be armed truthfully.

    ``16_TRIGGER_DSL.md`` section 9.1 lists the preconditions the Kernel checks
    before the insert. Three of them are enforced here:

    * **1** - ``parse_spec()`` succeeds. A predicate that does not parse arms
      cleanly and then fails at wake time with ``PROJECTION_FAILED``, months
      later, which is the whole failure mode prospective memory cannot afford.
    * **2** - every ``commitments.<name>`` binding resolves to a commitment on
      **this** case, either one this commit just wrote or one already there.
    * **5 / section 9.3** - generation ``1``, and the schedule name stamped with
      it, computed by ``triggers.config.schedule_name_for`` rather than spelled
      again here.

    Precondition 3 (``not_before`` far in the past) is a warning metric and not
    a refusal: the hero deposit deadline genuinely elapsed in June, and section
    9.1 says to arm it and let the first wake handle it.
    """
    predicate = mutation.predicate
    if predicate is None:  # pragma: no cover - refused by ProposedTrigger
        raise UnresolvedCommitmentSourceError(
            f"trigger {mutation.local_id} arms with no predicate; "
            "prospective_triggers.predicate_ast is NOT NULL"
        )
    bindings = _trigger_bindings(predicate, created, snapshot)
    if bindings is None:
        return None
    document = trigger_ast.build_spec_document(
        predicate=predicate.model_dump(mode="json"), bindings=bindings
    )
    try:
        trigger_ast.parse_spec(document, trigger_registry.resolve_field)
    except trigger_ast.TriggerSpecError:
        return None
    trigger_id = uuid.uuid4()
    return TriggerArmWrite(
        trigger_id=trigger_id,
        case_id=case_id,
        trigger_type=mutation.trigger_type,
        predicate_ast=document,
        basis_case_revision=revision,
        evaluation_version=FRESH_ARM_EVALUATION_VERSION,
        not_before=mutation.not_before,
        expires_at=mutation.expires_at,
        schedule_name=trigger_config.schedule_name_for(
            trigger_id.hex, FRESH_ARM_EVALUATION_VERSION
        ),
    )


def _trigger_bindings(
    predicate: PredicateNode,
    created: Sequence[CommitmentRowWrite],
    snapshot: AggregateSnapshot,
) -> dict[str, uuid.UUID] | None:
    """``commitments.<name>`` -> the obligation it names, or ``None``.

    The Kernel is the only component that knows the resolved commitment ids,
    because it just wrote them - but ``ProposedTrigger`` has no field that can
    carry a binding, so the *name* has to be recovered from the predicate's own
    field paths and matched against what this case holds.

    Two rules, tried in order, and the fallback is the one that fails closed.

    **By name, when the commit wrote the obligation under that name.**
    ``ProposedCommitment.local_id`` is ``cm_<name>`` and the binding is
    ``commitments.<name>``, so when a producer adopts that convention the two
    can be compared instead of guessed, and a predicate saying
    ``commitments.deposit.*`` beside a ``cm_damage`` obligation binds to the
    damage claim only if elimination independently justifies it.

    **By elimination otherwise: one name, one candidate obligation.** No table
    stores a binding name, and today no producer sets ``local_id`` to one -
    every curated commitment is ``cm_001`` - so requiring the name match would
    refuse **every** arm in the repository, the hero landlord deposit included,
    and leave ``prospective_triggers`` permanently empty. A rule that closes a
    conditional hazard by removing the feature is not a safer rule.

    Anything else - two binding names, or one name with two candidate
    obligations - is refused. Those are the shapes elimination cannot justify at
    all, and binding a trigger to the wrong obligation is a silent error that
    surfaces months later as a fire against the wrong money.

    **The residual is real and named.** While producers use ``cm_001``, a 1:1
    elimination match cannot distinguish "the predicate means this obligation"
    from "the predicate names something else and there happens to be only one".
    The fix is not a better heuristic: it is ``bindings: Mapping[str, LocalId]``
    on ``ProposedTrigger``, which makes the wrong bind *unrepresentable* rather
    than detected. ``ProposedCommitment.source_claim_local_id`` already models
    exactly that shape, and ``16_TRIGGER_DSL.md`` section 4.6 justifies bindings
    with "the Kernel verifies each bound commitment exists, belongs to the
    trigger's ``case_id``, and belongs to the trigger's tenant" - which presumes
    the Kernel is *given* the binding and checks it. Inference is a different
    operation the spec never contemplated. That field lives in
    ``provenance_contracts`` and is not the Kernel's to add.
    """
    names = sorted(
        {path.split(".")[1] for path in predicate.field_paths() if path.startswith("commitments.")}
    )
    if not names:
        return {}
    by_name = {row.binding_name: row.commitment_id for row in created}
    if all(name in by_name for name in names):
        return {name: by_name[name] for name in names}
    candidates = [row.commitment_id for row in created] + [
        row.commitment_id for row in snapshot.commitments
    ]
    if len(names) != 1 or len(candidates) != 1:
        return None
    return {names[0]: candidates[0]}


def _required_trigger_id(mutation: ProposedTrigger) -> uuid.UUID:
    """The trigger a DISARM names. ``ProposedTrigger`` already requires it."""
    trigger_id = mutation.trigger_id
    if trigger_id is None:  # pragma: no cover - refused by ProposedTrigger
        raise UnresolvedCommitmentSourceError(
            f"trigger mutation {mutation.local_id} names no trigger to disarm"
        )
    return trigger_id


def _disarm_reason(
    case_update: case_ops.CaseUpdate, commitment_writes: Sequence[CommitmentUpdateWrite]
) -> str:
    """Which of the six ``DISARMED`` codes this commit earned.

    ``ck_prospective_triggers_last_reason`` pairs ``last_result = 'DISARMED'``
    with exactly six codes, so ``DISARMED`` + ``PREDICATE_FALSE`` - a
    combination that reads plausibly and means nothing - is refused by the
    database. ``ProposedTrigger`` carries a free-text ``rationale`` and no code,
    so the Kernel derives one from what the commit actually did rather than
    letting a model choose a member of a closed vocabulary.

    The **precedence itself is not decided here.** Two components answer "why
    did this trigger stop?" - the trigger evaluator when a predicate comes out
    FALSE, and the Kernel when a committed proposal disarms one outright - and
    if they answered differently about the same event the audit record would
    contradict itself. ``triggers.outcomes.disarm_reason`` is the single
    implementation and this delegates to it. It ranks the case above its
    commitments (reporting ``COMMITMENT_SATISFIED`` for a case that resolved
    while money was still owed is a false statement in an audit record), and it
    requires **every** bound obligation to be discharged rather than any one.

    ``USER_DISMISSED`` is the residual: nothing canonical in this commit stood
    the trigger down, so the disarm is the request itself - an agent acting on
    the user's artifact, on the user's behalf.
    """
    code = trigger_outcomes.disarm_reason(
        case_status=str(case_update.status_after) if case_update.status_moves else None,
        commitment_statuses=[update.status_after for update in commitment_writes],
    )
    return str(code) if code is not None else str(TriggerReasonCode.USER_DISMISSED)


@dataclass(frozen=True, slots=True)
class _DenialOutcome:
    """What a payment denial produced. Never a ``fulfillments`` row."""

    reasons: tuple[KernelReasonCode, ...] = ()
    conflicts: tuple[ConflictRowWrite, ...] = ()
    signals: tuple[case_ops.ConflictSignal, ...] = ()
    commitment_update: CommitmentUpdateWrite | None = None
    requires_human: bool = False
    attention: AttentionLevel = AttentionLevel.NONE


def _note_inert(reasons: list[KernelReasonCode]) -> None:
    """Record that a claim was admitted and the v1 registry did nothing with it.

    Section 6.2: *"admitting a claim is a memory change even if no belief
    moves"*, so this commit is canonical and takes a revision. But every other
    code :func:`build_write_plan` can emit is downstream of a claim reaching the
    matcher, the disposition or the ledger, and ``decisions.build_decision_row``
    refuses an accepted row with no code at all - observed live as
    ``ValueError: ACCEPTED was built with no reason code``. Three claims reach
    here: a predicate outside the closed family registry, a payment naming no
    commitment this transaction read, and a denial with nothing admitted to
    contradict.

    The code is taken from the closed catalogue rather than invented.
    ``12_KERNEL_ALGORITHMS.md`` section 9.3 has no ``CLAIM_ADMITTED`` member and
    ``CANONICAL_DECISIONS.md`` -> *Closed domain vocabularies* forbids a
    layer-local alias, so the one member whose meaning is "the v1 registry had
    nothing to apply this to" carries it. Section 2.9 reaches the same code
    through the same registry lookup on the conflict-hint path. This is a
    recorded widening of that code's stated scope, not a new vocabulary.
    """
    reasons.append(KernelReasonCode.CONFLICT_HINT_UNMAPPED_FAMILY)


def _is_denial(proposition: prop.Proposition) -> bool:
    """``payment_not_received`` -> ``PaymentValue(asserted=False)``.

    ``families.coerce_value`` calls ``asserted`` "the denial flag rule M5
    needs"; this is the only place the write path reads it.
    """
    value = proposition.value
    return isinstance(value, families.PaymentValue) and not value.asserted


def _apply_denial(
    proposed: ProposedClaim,
    proposition: prop.Proposition,
    snapshot: AggregateSnapshot,
    *,
    tx_now: datetime,
    cfg: KernelConfig,
) -> _DenialOutcome:
    """Match a payment denial against the ledger. Never move the money.

    ``22_EVAL_DATASETS.md`` CX-04: *"The denial claim is preserved; the ledger is
    unchanged."* The denial is a counterparty assertion, and the whole product
    thesis is that a counterparty assertion is not a fact - so it raises a
    ``FULFILLMENT_CONFLICT`` against the admitted payment it contradicts and
    ``12_KERNEL_ALGORITHMS.md`` section 3.3 decides whether a person must look.

    The ledger is never mutated here, in either direction. When the disposition
    would promote the denial, that means reversing an admitted, evidence-linked
    fulfillment, and v1 has no statement that does it: the outcome is escalated
    with ``HUMAN_REQUIRED_UNRESOLVABLE_TYPE`` rather than recorded as an
    auto-resolution whose consequence never happened.
    """
    if proposed.subject_type is not SubjectType.COMMITMENT or proposed.subject_id is None:
        return _DenialOutcome()
    commitment = snapshot.commitment(proposed.subject_id)
    if commitment is None:
        return _DenialOutcome()

    ledger = snapshot.fulfillment_ledger.get(commitment.commitment_id, ())
    finding = _denial_finding(proposition, commitment, ledger, cfg=cfg)
    if finding is None:
        if _has_unreconstructable_payment(ledger, commitment, cfg=cfg):
            # There IS an admitted payment here, and the Kernel could not
            # establish its standing well enough to write a conflict whose sides
            # name real rows. Escalating with no conflict row is the honest
            # outcome; staying silent would be the silent-drop this function
            # exists to remove.
            return _DenialOutcome(
                reasons=(KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE,),
                requires_human=True,
                attention=AttentionLevel.ATTENTION,
            )
        return _DenialOutcome()

    verdict = disposition.decide(finding, cfg)
    if verdict.value_changes:
        verdict = disposition.Disposition(
            kind=disposition.DispositionKind.RETAIN_INCUMBENT_DISPUTED,
            conflict_status=ConflictStatus.NEEDS_HUMAN,
            requires_human=True,
            reason_code=KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE,
            gate="H8",
            epistemic_status_after=EpistemicStatus.DISPUTED,
            value_changes=False,
            case_attention=AttentionLevel.ATTENTION,
        )

    conflict = _conflict_row(
        finding, verdict, case_id=commitment.case_id, canonical_version_id=None
    )
    update = _denial_commitment_update(commitment, ledger, conflict, tx_now=tx_now, cfg=cfg)
    return _DenialOutcome(
        reasons=(*finding.reason_codes, verdict.reason_code),
        conflicts=(conflict,),
        signals=(case_ops.ConflictSignal(case_id=commitment.case_id, severity=finding.severity),),
        commitment_update=update,
        requires_human=verdict.requires_human,
        attention=verdict.case_attention,
    )


def _denial_finding(
    denial: prop.Proposition,
    commitment: money_ops.CommitmentRow,
    ledger: Sequence[money_ops.FulfillmentRow],
    *,
    cfg: KernelConfig,
) -> contradiction.ConflictFinding | None:
    """The first admitted ledger row ``M5`` says the denial contradicts.

    A denial of a payment nothing ever admitted contradicts nothing: the claim
    is recorded and no conflict is raised. Guessing which unrecorded payment a
    denial refers to is the inference the Kernel is not allowed to make, in the
    mirror image of section 2.7's rule for undirected payments.
    """
    for row in ledger:
        if row.admission_status is not FulfillmentAdmissionStatus.ADMITTED:
            continue
        incumbent = _ledger_proposition(row, commitment, cfg=cfg)
        if incumbent is None:
            continue
        finding = contradiction.match(incumbent, denial, contradiction.MatchContext(), cfg)
        if finding is not None:
            return finding
    return None


def _has_unreconstructable_payment(
    ledger: Sequence[money_ops.FulfillmentRow],
    commitment: money_ops.CommitmentRow,
    *,
    cfg: KernelConfig,
) -> bool:
    """True when the ledger holds an admitted payment this Kernel cannot read.

    Separating this from "no admitted payment at all" is the whole point: a
    denial of something nobody recorded contradicts nothing, while a denial of
    something recorded but unreadable is a question for a person.
    """
    return any(
        row.admission_status is FulfillmentAdmissionStatus.ADMITTED
        and _ledger_proposition(row, commitment, cfg=cfg) is None
        for row in ledger
    )


def _ledger_proposition(
    row: money_ops.FulfillmentRow,
    commitment: money_ops.CommitmentRow,
    *,
    cfg: KernelConfig,
) -> prop.Proposition | None:
    """One admitted fulfillment, as the asserted payment it records.

    ``source_class`` is a placeholder and the authority is then overwritten from
    ``FulfillmentRow.authority`` - exactly what ``transaction._incumbent`` does
    for a belief incumbent, and for the same reason: no table stores the source
    class, so the grid key is not recoverable, while the score the Kernel wrote
    at admission time is. A known placeholder is used so the normaliser does not
    emit ``AUTHORITY_UNMAPPED_SOURCE_CLASS`` about a class nobody supplied.

    Returns ``None`` when the row carries no amount, no timestamp or no
    grounding claim. The claim id is the conflict's side id, and recording a
    side that names nothing would make ``uq_conflicts_live_identity`` a
    coincidence.
    """
    if row.amount is None or row.fulfilled_at is None or row.source_claim_id is None:
        return None
    normalised = prop.normalize_claim(
        prop_id=row.source_claim_id,
        subject_type=SubjectType.COMMITMENT,
        subject_id=commitment.commitment_id,
        predicate=families.canonical_predicate(Family.PAYMENT),
        raw_value={
            "currency": row.currency,
            "amount": row.amount,
            "paid_at": row.fulfilled_at,
        },
        source_class=str(_LEDGER_AUTHORITY_PLACEHOLDER),
        claim_kind=ClaimKind.FULFILLMENT_CLAIM,
        valid_from=row.fulfilled_at,
        valid_to=None,
        validity_basis=prop.ValidityBasis.EXPLICIT_OPEN,
        recorded_at=row.fulfilled_at,
        source_kind=prop.PropositionSourceKind.CLAIM,
        is_incumbent=True,
        cfg=cfg,
    )
    proposition = normalised.proposition
    if proposition is None:  # pragma: no cover - PAYMENT is a mapped family
        return None
    # An unresolvable authority stays at the unknown floor. It cannot clear
    # `auto_resolve_floor`, so the denial cannot auto-resolve against a payment
    # whose standing the Kernel could not recover - it goes to a person.
    authority = row.authority if row.authority is not None else cfg.unknown_source_class_authority
    return replace(proposition, base_authority=authority)


def _denial_commitment_update(
    commitment: money_ops.CommitmentRow,
    ledger: Sequence[money_ops.FulfillmentRow],
    conflict: ConflictRowWrite,
    *,
    tx_now: datetime,
    cfg: KernelConfig,
) -> CommitmentUpdateWrite | None:
    """The status a live denial forces, with the amounts untouched.

    Section 4.4: dispute dominates. A ``NEEDS_HUMAN`` ``FULFILLMENT_CONFLICT``
    makes the commitment ``DISPUTED``; an ``AUTO_RESOLVED`` one does not, which
    is exactly the difference between CX-05 and CX-04. The amounts are the
    recomputed ledger projection and are unchanged by construction, because a
    denial writes no ``fulfillments`` row.
    """
    admitted = money_ops.admitted_total(ledger, commitment.currency)
    committed = commitment.committed_amount
    outstanding = commitment.outstanding_amount or Decimal("0.0000")
    excess = Decimal("0.0000") if committed is None else admitted - committed
    status_after = money_ops.commitment_status(
        commitment,
        admitted_sum=admitted,
        committed=committed,
        outstanding=outstanding,
        excess=excess,
        conflicts=(
            money_ops.ConflictWrite(
                conflict_type=conflict.conflict_type,
                status=conflict.status,
                severity=conflict.severity,
                requires_human=conflict.requires_human,
                reason_code=KernelReasonCode.CONFLICT_PAYMENT_DENIAL,
                detected_at=tx_now,
            ),
        ),
        tx_now=tx_now,
        cfg=cfg,
    )
    if status_after is commitment.status:
        return None
    return CommitmentUpdateWrite(
        commitment_id=commitment.commitment_id,
        status_before=str(commitment.status),
        status_after=str(status_after),
        fulfilled_after=commitment.fulfilled_amount,
        outstanding_after=commitment.outstanding_amount,
        revision_before=commitment.revision,
        revision_after=commitment.revision + 1,
        currency=commitment.currency,
    )


def _apply_payment(
    proposed: ProposedClaim,
    proposition: prop.Proposition,
    snapshot: AggregateSnapshot,
    *,
    tx_now: datetime,
    cfg: KernelConfig,
) -> money_ops.CommitmentDelta | None:
    """Admit one payment against the commitment it names, or do nothing.

    A payment claim whose subject is not a commitment this transaction read is
    still admitted as a claim; it simply moves no ledger. Guessing which
    obligation an undirected payment satisfies is exactly the kind of inference
    the Kernel is not allowed to make.
    """
    if proposed.subject_type is not SubjectType.COMMITMENT or proposed.subject_id is None:
        return None
    commitment = snapshot.commitment(proposed.subject_id)
    if commitment is None:
        return None
    value = proposition.value
    amount = value.amount if isinstance(value, families.PaymentValue) else None
    currency = value.currency if isinstance(value, families.PaymentValue) else None
    ledger = snapshot.fulfillment_ledger.get(commitment.commitment_id, ())
    return money_ops.apply_fulfillment(
        commitment,
        ledger,
        money_ops.ProposedFulfillment(
            evidence_id=proposed.evidence_id,
            amount=amount,
            currency=currency,
            fulfilled_at=proposed.valid_from or tx_now,
            confidence=proposed.extraction_confidence,
        ),
        tx_now=tx_now,
        cfg=cfg,
    )


#: Kept so a future edit that wants to rebuild a plan with different ids has a
#: supported route rather than reaching into the frozen dataclass.
_replace = replace
