"""The Kernel writes obligations and prospective memory — T4.9 / T4.10 gap.

Authority
---------
- ``specs/10_DATABASE_DDL.md`` section 13, the statement order, which prints
  step 6 as *"Commitments and fulfillments"* and step 9 as *"Trigger
  mutations"*. Both steps existed as headings with nothing under them.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 4 (the monetary algorithm) and
  section 6.2, whose table names ``commitments`` and ``prospective_triggers``
  canonical changes.
- ``CANONICAL_DECISIONS.md`` -> *Canonical writer*: these rows belong to the
  Kernel and to nothing else.

Two defects, both found by executing the Kernel against curated fixtures
-------------------------------------------------------------------------
1. ``pipeline.build_write_plan`` read ``proposal.claims`` and never
   ``proposal.commitments`` or ``proposal.trigger_mutations``. The four
   commitments ``scripts/seed/proposals.py`` authors validated, were persisted
   inside the ``memory_proposals`` payload, and then had nowhere to go: no
   ``INSERT INTO commitments`` and no ``INSERT INTO prospective_triggers``
   existed anywhere in the repository.
2. A commit that admitted **only** claims whose predicates map to no v1
   family produced no reason code at all, and
   ``decisions.build_decision_row`` refuses such a row -
   ``ValueError: ACCEPTED was built with no reason code``. Section 6.2 is
   explicit that *"admitting a claim is a memory change even if no belief
   moves"*, so the acceptance is legal and the Kernel had no code to carry it.

Every test here is hermetic: no database, no credentials, no model call. The
same rows are asserted against a live cluster in
``services/control_plane/tests/db/test_kernel_obligations.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from provenance_contracts.base import Money
from provenance_contracts.predicates import PredicateNode
from provenance_contracts.proposal import (
    MemoryProposal,
    ProposalIdentity,
    ProposedClaim,
    ProposedCommitment,
    ProposedTrigger,
)
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    CaseStatus,
    ClaimKind,
    CommitmentStatus,
    CommitmentType,
    EventType,
    KernelDecision,
    KernelReasonCode,
    Modality,
    ModelTier,
    PredicateOp,
    ProposalType,
    SourceClass,
    SubjectType,
    TransitionType,
    TriggerMutationKind,
    TriggerType,
    ValueType,
)
from services.control_plane.app.memory_kernel import case_ops, decisions, money_ops, pipeline

pytestmark = pytest.mark.unit

TENANT = uuid.UUID(int=0x8001)
USER = uuid.UUID(int=0x8002)
REL = uuid.UUID(int=0x1001)
CASE = uuid.UUID(int=0x2001)
EV_PROMISE = uuid.UUID(int=0x6001)
ART = uuid.UUID(int=0x7001)
PROPOSAL = uuid.UUID(int=0x9001)
TRACE = uuid.UUID(int=0x9002)
DECISION_ID = uuid.UUID(int=0x9003)
EXISTING_TRIGGER = uuid.UUID(int=0xC001)
#: An obligation already on the case, for a trigger predicate to bind to.
EXISTING_COMMITMENT = uuid.UUID(int=0x3001)
#: A claim written by an earlier commit, cited by id rather than by local ref.
PERSISTED_CLAIM = uuid.UUID(int=0x4001)

OBSERVED_AT = datetime(2026, 5, 16, 15, 0, tzinfo=UTC)
TX_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
#: ``CANONICAL_DECISIONS.md`` -> *Hero dataset canon*: the deposit falls due
#: `2026-06-15T00:00:00Z` and the wake is that instant plus the margin.
DEPOSIT_DUE_AT = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
TRIGGER_WAKE_AT = datetime(2026, 6, 15, 0, 1, tzinfo=UTC)


def _money(amount: str) -> Money:
    """Money crosses the proposal boundary as an exact decimal, never a float."""
    return Money(amount=Decimal(amount), currency="USD")


def _model() -> ModelAttribution:
    return ModelAttribution(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tier=ModelTier.E,
        prompt_version="pv-extract-1.0.0",
        graph_name="ingestion",
        graph_version="1.0.0",
    )


def _claim(
    *,
    predicate: str,
    object_type: ValueType,
    object_value: object,
    local_id: str = "cl_001",
    subject_type: SubjectType = SubjectType.CASE,
    subject_id: uuid.UUID = CASE,
    dated: bool = True,
) -> ProposedClaim:
    return ProposedClaim(
        local_id=local_id,
        claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
        subject_type=subject_type,
        subject_id=subject_id,
        predicate=predicate,
        object_type=object_type,
        object_value=object_value,  # type: ignore[arg-type]
        actor_type=ActorType.COUNTERPARTY,
        actor_ref="harborview-property-management",
        evidence_id=EV_PROMISE,
        source_class=SourceClass.PROVIDER_AGENT_WRITTEN,
        modality=Modality.ASSERTED_PAST,
        valid_from=OBSERVED_AT if dated else None,
        valid_to=None,
        extraction_confidence=Decimal("0.9800"),
    )


def _promise_claim() -> ProposedClaim:
    """The unmapped surface predicate the deposit commitment cites."""
    return _claim(
        predicate="deposit_return_promise",
        object_type=ValueType.BOOLEAN,
        object_value=True,
    )


def _deposit_commitment(
    *,
    amount: str | None = "1800.00",
    due_at: datetime | None = DEPOSIT_DUE_AT,
    commitment_type: CommitmentType = CommitmentType.DEPOSIT_RETURN,
    local_id: str = "cm_001",
) -> ProposedCommitment:
    return ProposedCommitment(
        local_id=local_id,
        commitment_type=commitment_type,
        description=(
            "Return of the USD 1,800.00 security deposit within 30 days of the final inspection."
        ),
        obligor_type=ActorType.COUNTERPARTY,
        obligor_ref="harborview-property-management",
        beneficiary_type=ActorType.USER,
        beneficiary_ref=str(USER),
        committed=None if amount is None else _money(amount),
        due_at=due_at,
        source_claim_local_id="cl_001",
        confidence=Decimal("0.9800"),
    )


def _deposit_predicate() -> PredicateNode:
    """``10_DATABASE_DDL.md`` section 17.6, as the closed algebraic term."""
    return PredicateNode(
        op=PredicateOp.AND,
        args=(
            PredicateNode(
                op=PredicateOp.GT,
                args=(
                    PredicateNode(
                        op=PredicateOp.FIELD,
                        path="commitments.deposit.outstanding_amount",
                    ),
                    # A DECIMAL comparison takes its constant as a JSON
                    # **string**. `parse_spec` refuses a bare `0` with
                    # `DECIMAL_MUST_BE_STRING`, which is what stops a float
                    # from ever reaching a money comparison.
                    PredicateNode(op=PredicateOp.CONST, value="0"),
                ),
            ),
            PredicateNode(
                op=PredicateOp.GTE,
                args=(
                    PredicateNode(op=PredicateOp.FIELD, path="clock.now"),
                    PredicateNode(op=PredicateOp.FIELD, path="commitments.deposit.due_at"),
                ),
            ),
        ),
    )


def _arm_trigger() -> ProposedTrigger:
    return ProposedTrigger(
        local_id="tg_001",
        mutation_kind=TriggerMutationKind.ARM,
        trigger_type=TriggerType.COMMITMENT_DEADLINE,
        predicate=_deposit_predicate(),
        not_before=TRIGGER_WAKE_AT,
        expires_at=DEPOSIT_DUE_AT.replace(year=2027),
        rationale="The deposit is promised within 30 days of the final inspection.",
    )


def _disarm_trigger() -> ProposedTrigger:
    return ProposedTrigger(
        local_id="tg_002",
        mutation_kind=TriggerMutationKind.DISARM,
        trigger_id=EXISTING_TRIGGER,
        trigger_type=TriggerType.COMMITMENT_DEADLINE,
        rationale="The deposit arrived in full.",
    )


def _proposal(
    *,
    claims: tuple[ProposedClaim, ...] | None = None,
    commitments: tuple[ProposedCommitment, ...] = (),
    triggers: tuple[ProposedTrigger, ...] = (),
) -> MemoryProposal:
    return MemoryProposal(
        proposal_id=PROPOSAL,
        proposal_type=ProposalType.INGESTION_INTERPRETATION,
        trace_id=TRACE,
        agent_run_id=uuid.UUID(int=0x9004),
        user_id=USER,
        source_artifact_ids=(ART,),
        evidence_ids=(EV_PROMISE,),
        identity=ProposalIdentity(relationship_id=REL, case_id=CASE, confidence=Decimal("0.9900")),
        claims=(_promise_claim(),) if claims is None else claims,
        commitments=commitments,
        trigger_mutations=triggers,
        model=_model(),
        idempotency_key="harborview-deposit-0001",
        created_at=OBSERVED_AT,
    )


def _snapshot(
    *, revision: int = 3, commitments: tuple[money_ops.CommitmentRow, ...] = ()
) -> pipeline.AggregateSnapshot:
    return pipeline.AggregateSnapshot(
        case=case_ops.CaseRow(
            case_id=CASE,
            tenant_id=TENANT,
            user_id=USER,
            status=CaseStatus.OPEN,
            revision=revision,
        ),
        relationship_id=REL,
        case_snapshot=case_ops.CaseSnapshot(
            evidence={
                EV_PROMISE: case_ops.EvidenceRecord(evidence_id=EV_PROMISE, created_at=OBSERVED_AT)
            }
        ),
        commitments=commitments,
    )


#: The obligation a ``COMMITMENT_DEADLINE`` predicate binds to. A trigger that
#: reads ``commitments.deposit.outstanding_amount`` needs one to bind, and the
#: Kernel refuses to arm when the binding is ambiguous.
def _deposit_on_the_case() -> tuple[money_ops.CommitmentRow, ...]:
    return (
        money_ops.CommitmentRow(
            commitment_id=EXISTING_COMMITMENT,
            case_id=CASE,
            status=CommitmentStatus.ACTIVE,
            currency="USD",
            committed_amount=Decimal("1800.0000"),
            fulfilled_amount=Decimal("0.0000"),
            outstanding_amount=Decimal("1800.0000"),
            revision=0,
            due_at=DEPOSIT_DUE_AT,
        ),
    )


def _run(
    proposal: MemoryProposal, snapshot: pipeline.AggregateSnapshot
) -> pipeline.PipelineOutcome:
    return pipeline.build_write_plan(
        proposal,
        snapshot=snapshot,
        tx_now=TX_NOW,
        trace_id=TRACE,
        decision_id=DECISION_ID,
    )


# ---------------------------------------------------------------------------
# Defect 2 — a claim-only acceptance must carry a reason code
# ---------------------------------------------------------------------------


def test_a_claim_only_acceptance_is_accepted() -> None:
    """Section 6.2: admitting a claim is a canonical change on its own."""
    outcome = _run(_proposal(), _snapshot())
    assert outcome.decision is KernelDecision.ACCEPTED
    assert len(outcome.plan.claims) == 1
    assert outcome.plan.belief_versions == ()


def test_a_claim_only_acceptance_carries_at_least_one_reason_code() -> None:
    """The defect, stated as the property that was false.

    ``build_write_plan`` collected reason codes from normalisation, from the
    disposition verdict and from the case update - all three downstream of a
    mapped predicate family. A commit admitting only unmapped claims produced
    none of the three.
    """
    outcome = _run(_proposal(), _snapshot())
    assert outcome.reason_codes, "an accepted commit with no reason code cannot be audited"


def test_the_ledger_row_for_a_claim_only_acceptance_can_be_built() -> None:
    """The crash itself, executed rather than described.

    ``decisions.build_decision_row`` refuses a row with no reason code, so this
    raised ``ValueError: ACCEPTED was built with no reason code`` against a live
    cluster on the first replay of the ``movers-scheduling`` fixture.
    """
    outcome = _run(_proposal(), _snapshot())
    row = decisions.build_decision_row(
        decision_id=DECISION_ID,
        tenant_id=TENANT,
        user_id=USER,
        proposal_id=PROPOSAL,
        trace_id=TRACE,
        decision=outcome.decision,
        reason_codes=outcome.reason_codes,
        case_id=CASE,
        case_revision_before=3,
        case_revision_after=4,
        tx_now=TX_NOW,
    )
    assert row.reason_codes


def test_the_unmapped_claim_reason_code_names_the_registry_miss() -> None:
    """The code is chosen from the closed catalogue, never invented.

    ``12_KERNEL_ALGORITHMS.md`` section 9.3 has no ``CLAIM_ADMITTED`` member and
    ``CANONICAL_DECISIONS.md`` -> *Closed domain vocabularies* forbids a
    layer-local alias, so the code that already means *"a predicate outside the
    v1 registry"* is the one used.
    """
    outcome = _run(_proposal(), _snapshot())
    assert KernelReasonCode.CONFLICT_HINT_UNMAPPED_FAMILY in outcome.reason_codes


def test_a_mapped_claim_does_not_emit_the_unmapped_code() -> None:
    """The guard is not a blanket append: a claim the registry recognises
    reports the disposition verdict instead."""
    balance = _claim(
        predicate="balance_owed",
        object_type=ValueType.MONEY,
        object_value={"currency": "USD", "amount": "186.0000"},
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=REL,
    )
    outcome = _run(_proposal(claims=(balance,)), _snapshot())
    assert KernelReasonCode.CONFLICT_HINT_UNMAPPED_FAMILY not in outcome.reason_codes
    assert KernelReasonCode.BELIEF_CREATED in outcome.reason_codes


# ---------------------------------------------------------------------------
# Defect 1a — commitments
# ---------------------------------------------------------------------------


def test_a_proposed_commitment_becomes_a_commitment_row() -> None:
    plan = _run(_proposal(commitments=(_deposit_commitment(),)), _snapshot()).plan
    assert len(plan.commitments) == 1
    row = plan.commitments[0]
    assert row.case_id == CASE
    assert row.commitment_type == str(CommitmentType.DEPOSIT_RETURN)
    assert row.due_at == DEPOSIT_DUE_AT


def test_the_deposit_row_carries_decimal_money_and_the_outstanding_identity() -> None:
    """``outstanding = committed - admitted_fulfillment``, in ``Decimal``.

    M2 of ``0004_obligation_ledger`` requires all three amounts or none, so a
    new monetary commitment carries ``0.0000`` fulfilled rather than NULL -
    without which M3 and M4 are vacuously true.
    """
    row = _run(_proposal(commitments=(_deposit_commitment(),)), _snapshot()).plan.commitments[0]
    assert row.currency == "USD"
    assert isinstance(row.committed_amount, Decimal)
    assert isinstance(row.fulfilled_amount, Decimal)
    assert isinstance(row.outstanding_amount, Decimal)
    assert row.committed_amount == Decimal("1800.0000")
    assert row.fulfilled_amount == Decimal("0.0000")
    assert row.outstanding_amount == Decimal("1800.0000")
    assert row.outstanding_amount == row.committed_amount - row.fulfilled_amount
    assert row.status is CommitmentStatus.ACTIVE


def test_a_non_monetary_commitment_carries_no_amount_and_no_currency() -> None:
    """M2 and M6: the ISP termination promise is an obligation with no money.

    A NULL that silently became ``0.00`` would erase the difference between
    "nothing owed" and "not a money obligation".
    """
    termination = _deposit_commitment(
        amount=None, due_at=None, commitment_type=CommitmentType.SERVICE_TERMINATION
    )
    row = _run(_proposal(commitments=(termination,)), _snapshot()).plan.commitments[0]
    assert row.committed_amount is None
    assert row.fulfilled_amount is None
    assert row.outstanding_amount is None
    assert row.currency is None
    assert row.status is CommitmentStatus.ACTIVE


def test_the_commitment_cites_the_claim_written_in_the_same_plan() -> None:
    """``fk_commitments_source_claim`` is NOT NULL, so the local reference has
    to be resolved to the id this plan is about to write."""
    plan = _run(_proposal(commitments=(_deposit_commitment(),)), _snapshot()).plan
    assert plan.commitments[0].source_claim_id == plan.claims[0].claim_id


def test_a_created_commitment_is_recorded_in_the_audit_ledger() -> None:
    plan = _run(_proposal(commitments=(_deposit_commitment(),)), _snapshot()).plan
    rows = [t for t in plan.transitions if t.transition_type is TransitionType.COMMITMENT_STATUS]
    assert len(rows) == 1
    assert rows[0].from_state is None
    assert rows[0].to_state == str(CommitmentStatus.ACTIVE)
    assert rows[0].subject_id == plan.commitments[0].commitment_id


def test_one_commitment_created_event_is_emitted_per_commit() -> None:
    """``uq_outbox_events_aggregate_event`` is
    ``(aggregate_type, aggregate_id, aggregate_version, event_type)``, and the
    aggregate of ``commitment.created.v1`` is the CASE. One event per commit is
    therefore the only collision-free shape when a proposal carries two.
    """
    second = ProposedCommitment(
        local_id="cm_002",
        commitment_type=CommitmentType.MONETARY_REIMBURSEMENT,
        description="Reimbursement of USD 420.00 for damage caused during the move.",
        obligor_type=ActorType.COUNTERPARTY,
        obligor_ref="beltline-movers",
        beneficiary_type=ActorType.USER,
        beneficiary_ref=str(USER),
        committed=_money("420.00"),
        source_claim_local_id="cl_001",
        confidence=Decimal("0.9600"),
    )
    plan = _run(_proposal(commitments=(_deposit_commitment(), second)), _snapshot()).plan
    assert len(plan.commitments) == 2
    created = [e for e in plan.outbox if e.event_type is EventType.COMMITMENT_CREATED]
    assert len(created) == 1
    assert created[0].aggregate_version == 4


def test_a_commitment_alone_is_not_a_canonical_noop() -> None:
    """Claim-free on purpose: with a claim in the plan the assertion would pass
    whether or not the commitment was ever read."""
    detached = ProposedCommitment(
        local_id="cm_001",
        commitment_type=CommitmentType.DEPOSIT_RETURN,
        description="Return of the USD 1,800.00 security deposit.",
        obligor_type=ActorType.COUNTERPARTY,
        obligor_ref="harborview-property-management",
        beneficiary_type=ActorType.USER,
        beneficiary_ref=str(USER),
        committed=_money("1800.00"),
        due_at=DEPOSIT_DUE_AT,
        source_claim_id=PERSISTED_CLAIM,
        confidence=Decimal("0.9800"),
    )
    plan = _run(_proposal(claims=(), commitments=(detached,)), _snapshot()).plan
    assert plan.claims == ()
    assert len(plan.commitments) == 1
    assert plan.commitments[0].source_claim_id == PERSISTED_CLAIM
    assert not plan.is_canonical_noop()


# ---------------------------------------------------------------------------
# Defect 1b — prospective triggers
# ---------------------------------------------------------------------------


def test_an_armed_trigger_becomes_a_prospective_triggers_row() -> None:
    plan = _run(
        _proposal(triggers=(_arm_trigger(),)),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan
    assert len(plan.trigger_arms) == 1
    arm = plan.trigger_arms[0]
    assert arm.case_id == CASE
    assert arm.trigger_type is TriggerType.COMMITMENT_DEADLINE
    assert arm.not_before == TRIGGER_WAKE_AT


def test_the_armed_predicate_is_stored_in_the_spec_envelope() -> None:
    """``0006_prospective_memory``: ``predicate_ast`` is JSONB holding the
    closed algebraic term, never a string a later evaluator would parse - and
    ``16_TRIGGER_DSL.md`` section 6 wraps that term in
    ``{ast_version, bindings, predicate}``.

    The envelope is not decoration. ``commitments.<name>.<field>`` resolves
    through ``bindings``, and a bare node dump arms cleanly and then fails at
    wake time with ``UNBOUND_COMMITMENT``, months later, on a row nobody
    remembers writing.
    """
    arm = _run(
        _proposal(triggers=(_arm_trigger(),)),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan.trigger_arms[0]
    assert arm.predicate_ast["ast_version"] == "1.0"
    assert arm.predicate_ast["bindings"] == {
        "deposit": {"kind": "COMMITMENT", "id": str(EXISTING_COMMITMENT)}
    }
    predicate = arm.predicate_ast["predicate"]
    assert predicate["op"] == "AND"
    paths = {
        leaf.get("path")
        for branch in predicate["args"]
        for leaf in branch["args"]
        if leaf.get("path")
    }
    assert "commitments.deposit.outstanding_amount" in paths


def test_the_armed_predicate_parses_with_the_evaluator_that_will_read_it() -> None:
    """Section 9.1 precondition 1, as a property of the row rather than a hope.

    ``parse_spec`` is the same function the wake path runs. Arming a predicate
    it cannot read is the failure prospective memory cannot afford: nothing goes
    wrong until the deadline passes.
    """
    from services.control_plane.app.triggers import ast as trigger_ast
    from services.control_plane.app.triggers import registry as trigger_registry

    arm = _run(
        _proposal(triggers=(_arm_trigger(),)),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan.trigger_arms[0]
    spec = trigger_ast.parse_spec(arm.predicate_ast, trigger_registry.resolve_field)
    assert sorted(spec.referenced_paths) == [
        "clock.now",
        "commitments.deposit.due_at",
        "commitments.deposit.outstanding_amount",
    ]


def test_a_predicate_the_evaluator_cannot_read_is_refused_at_arm_time() -> None:
    """A bare integer against a DECIMAL field. ``parse_spec`` raises
    ``DECIMAL_MUST_BE_STRING``, which is what keeps a float out of a money
    comparison - and the Kernel refuses rather than arming it."""
    numeric = ProposedTrigger(
        local_id="tg_001",
        mutation_kind=TriggerMutationKind.ARM,
        trigger_type=TriggerType.COMMITMENT_DEADLINE,
        predicate=PredicateNode(
            op=PredicateOp.GT,
            args=(
                PredicateNode(op=PredicateOp.FIELD, path="commitments.deposit.outstanding_amount"),
                PredicateNode(op=PredicateOp.CONST, value=0),
            ),
        ),
        not_before=TRIGGER_WAKE_AT,
        rationale="A money comparison against a bare number.",
    )
    outcome = _run(_proposal(triggers=(numeric,)), _snapshot(commitments=_deposit_on_the_case()))
    assert outcome.decision is KernelDecision.PENDING_HUMAN_REVIEW
    assert KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE in outcome.reason_codes
    assert outcome.plan.trigger_arms == ()


def test_an_unbindable_predicate_is_refused_rather_than_armed_unresolved() -> None:
    """No obligation on the case for ``commitments.deposit`` to name.

    ``ProposedTrigger`` carries no ``bindings`` field, so the Kernel recovers
    the name from the predicate and matches it against what the case holds.
    When that is ambiguous - or empty - it refuses, because a trigger bound to
    the wrong obligation fires against the wrong money.
    """
    outcome = _run(_proposal(triggers=(_arm_trigger(),)), _snapshot())
    assert outcome.decision is KernelDecision.PENDING_HUMAN_REVIEW
    assert KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE in outcome.reason_codes


def test_a_trigger_binds_to_a_commitment_written_by_the_same_commit() -> None:
    """The demo shape: record the deposit promise and arm its deadline in one
    proposal. The binding names an id that did not exist when the transaction
    began."""
    plan = _run(
        _proposal(commitments=(_deposit_commitment(),), triggers=(_arm_trigger(),)),
        _snapshot(),
    ).plan
    assert len(plan.commitments) == 1
    assert len(plan.trigger_arms) == 1
    binding = plan.trigger_arms[0].predicate_ast["bindings"]["deposit"]
    assert binding["id"] == str(plan.commitments[0].commitment_id)


def test_the_disarm_precedence_is_not_a_second_implementation() -> None:
    """Two components answer "why did this trigger stop?" - the evaluator on a
    FALSE predicate and the Kernel on a committed disarm. If they answered
    differently the audit record would contradict itself, so the precedence has
    one implementation and this asserts the Kernel reaches it."""
    import ast
    import inspect

    from services.control_plane.app.memory_kernel import pipeline as pipeline_mod
    from services.control_plane.app.triggers import outcomes as trigger_outcomes

    #: The docstring explains the precedence and therefore names its codes; the
    #: **body** is what must not re-decide it, so the docstring is stripped
    #: before looking. Checking the whole source would have passed on a comment.
    function = ast.parse(inspect.getsource(pipeline_mod._disarm_reason).lstrip()).body[0]
    assert isinstance(function, ast.FunctionDef)
    body = ast.unparse(ast.Module(body=function.body[1:], type_ignores=[]))
    assert "trigger_outcomes.disarm_reason(" in body
    for code in ("COMMITMENT_SATISFIED", "COMMITMENT_SUPERSEDED", "CASE_RESOLVED"):
        assert code not in body, f"{code} is decided by triggers.outcomes, not here"
    #: The residual is the Kernel's own, and it is the only code the body names.
    assert "USER_DISMISSED" in body
    assert trigger_outcomes.disarm_reason(case_status=None, commitment_statuses=[]) is None


def test_the_disarm_reason_follows_what_the_commit_did() -> None:
    """A commit that discharges the obligation reports it; one that only
    resolves the case reports the case; one that does neither is the user's."""
    from provenance_domain.enums import TriggerReasonCode

    disarm = _run(_proposal(triggers=(_disarm_trigger(),)), _snapshot()).plan.trigger_disarms[0]
    assert disarm.last_reason_code == str(TriggerReasonCode.USER_DISMISSED)


def test_a_fresh_arm_is_generation_one_and_stamps_its_schedule_name() -> None:
    """``16_TRIGGER_DSL.md`` section 9.1 precondition 5 and section 9.3.

    The column's ``DEFAULT 0`` is not the arm's value. ``schedule_name`` is the
    wake identity and the idempotency key, so a row whose generation disagrees
    with its own schedule name no-ops every wake with
    ``STALE_SCHEDULE_GENERATION``.
    """
    from services.control_plane.app.triggers import config as trigger_config

    arm = _run(
        _proposal(triggers=(_arm_trigger(),)),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan.trigger_arms[0]
    assert arm.evaluation_version == 1
    assert arm.schedule_name == trigger_config.schedule_name_for(arm.trigger_id.hex, 1)
    assert arm.schedule_name is not None
    assert arm.schedule_name.endswith("-v1")
    assert len(arm.schedule_name) <= 64


def test_the_armed_trigger_records_the_revision_this_commit_produced() -> None:
    """``basis_case_revision`` is what makes rule I8 real: the evaluator
    compares the case's *current* revision against the one it was armed at."""
    arm = _run(
        _proposal(triggers=(_arm_trigger(),)),
        _snapshot(revision=3, commitments=_deposit_on_the_case()),
    ).plan.trigger_arms[0]
    assert arm.basis_case_revision == 4


def test_a_disarm_names_a_reason_code_the_column_accepts() -> None:
    """``ck_prospective_triggers_last_reason`` pairs ``DISARMED`` with exactly
    six codes; anything else is refused by the database."""
    from provenance_domain.enums import TriggerReasonCode

    plan = _run(_proposal(triggers=(_disarm_trigger(),)), _snapshot()).plan
    assert len(plan.trigger_disarms) == 1
    disarm = plan.trigger_disarms[0]
    assert disarm.trigger_id == EXISTING_TRIGGER
    assert disarm.last_reason_code in {
        str(TriggerReasonCode.COMMITMENT_SATISFIED),
        str(TriggerReasonCode.COMMITMENT_SUPERSEDED),
        str(TriggerReasonCode.BINDING_SUPERSEDED),
        str(TriggerReasonCode.CASE_RESOLVED),
        str(TriggerReasonCode.CASE_SUPERSEDED),
        str(TriggerReasonCode.USER_DISMISSED),
    }


def test_a_trigger_mutation_is_recorded_in_the_audit_ledger() -> None:
    plan = _run(
        _proposal(triggers=(_arm_trigger(), _disarm_trigger())),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan
    rows = [t for t in plan.transitions if t.transition_type is TransitionType.TRIGGER_STATE]
    assert len(rows) == 2
    assert (rows[0].from_state, rows[0].to_state) == (None, "ARMED")
    assert (rows[1].from_state, rows[1].to_state) == ("ARMED", "DISARMED")


def test_rearm_and_extend_are_refused_rather_than_silently_dropped() -> None:
    """The six v1 capabilities name *arm/disarm*. A capability outside them is
    refused with a named reason code rather than partially handled - which is
    the failure this whole module exists to close."""
    extend = ProposedTrigger(
        local_id="tg_003",
        mutation_kind=TriggerMutationKind.EXTEND,
        trigger_id=EXISTING_TRIGGER,
        trigger_type=TriggerType.COMMITMENT_DEADLINE,
        expires_at=DEPOSIT_DUE_AT.replace(year=2028),
        rationale="Give the landlord another year.",
    )
    outcome = _run(_proposal(triggers=(extend,)), _snapshot())
    assert outcome.decision is KernelDecision.PENDING_HUMAN_REVIEW
    assert KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE in outcome.reason_codes
    assert outcome.plan.is_canonical_noop()


def test_an_armed_trigger_alone_is_not_a_canonical_noop() -> None:
    """Claim-free on purpose, for the same reason."""
    plan = _run(
        _proposal(claims=(), triggers=(_arm_trigger(),)),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan
    assert plan.claims == ()
    assert len(plan.trigger_arms) == 1
    assert not plan.is_canonical_noop()


def test_the_armed_trigger_emits_one_event_against_the_trigger_aggregate() -> None:
    plan = _run(
        _proposal(triggers=(_arm_trigger(),)),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan
    armed = [e for e in plan.outbox if e.event_type is EventType.TRIGGER_ARMED]
    assert len(armed) == 1
    assert armed[0].aggregate_type == "TRIGGER"
    assert armed[0].aggregate_id == plan.trigger_arms[0].trigger_id


# ---------------------------------------------------------------------------
# The count stays a claim about specific statements
# ---------------------------------------------------------------------------


def test_the_kernel_write_statements_are_named_and_counted() -> None:
    """``python -m tools.write_path_lint`` counts; this tuple says which.

    The linter reported *14 canonical write statements, 14 of them in the
    Kernel* before ``commitments``, ``prospective_triggers`` INSERT and
    ``prospective_triggers`` DISARM landed, and 17 before ``trigger_commit.py``
    gave prospective memory a production path -- the ``memory_proposals``
    INSERT the Kernel needs to author its own deterministic proposal, and the
    ``prospective_triggers`` UPDATE that settles one wake. Running the linter
    here rather than quoting its output is what keeps the number from becoming
    a magic constant nobody re-measures.
    """
    from pathlib import Path

    from services.control_plane.app.memory_kernel import transaction
    from tools import write_path_lint

    repo_root = Path(__file__).resolve().parents[4]
    result = write_path_lint.scan_paths(
        [repo_root / root for root in write_path_lint.DEFAULT_ROOTS]
    )
    assert result.violations == []
    assert result.kernel_statements == len(transaction.CANONICAL_WRITE_STATEMENTS)
    assert result.kernel_statements == 19


def test_every_named_write_statement_exists_in_the_kernel() -> None:
    """A name in the tuple that names nothing would make the enumeration
    decorative, which is the failure mode the tuple exists to prevent."""
    import inspect

    from services.control_plane.app.memory_kernel import (
        case_ops,
        decisions,
        transaction,
        trigger_commit,
    )

    sources = "\n".join(
        inspect.getsource(module) for module in (transaction, case_ops, decisions, trigger_commit)
    )
    for entry in transaction.CANONICAL_WRITE_STATEMENTS:
        symbol = entry.rsplit("(", 1)[1].rstrip(")").rsplit(".", 1)[-1]
        assert f"{symbol} = " in sources or f"{symbol}: Final[str] = " in sources, entry


# ---------------------------------------------------------------------------
# The money identity is on the path, and it has teeth
# ---------------------------------------------------------------------------


def test_the_opening_projection_refuses_a_negative_commitment() -> None:
    """``money.outstanding`` is what validates the amount, so removing it from
    the opening projection has to be visible. It is: a negative committed
    amount raises rather than reaching a row the CHECK would have to catch."""
    from provenance_domain.money import NegativeCommitmentError
    from services.control_plane.app.memory_kernel import money_ops

    with pytest.raises(NegativeCommitmentError):
        money_ops.open_commitment(
            committed_amount=Decimal("-1800.00"), currency="USD", tx_now=TX_NOW
        )


def test_the_opening_projection_quantizes_to_the_column_scale() -> None:
    """``DECIMAL(20,4)``. ``1800.00`` as proposed is ``1800.0000`` as stored,
    and the quantization happens in the identity rather than in the driver."""
    from services.control_plane.app.memory_kernel import money_ops

    opening = money_ops.open_commitment(
        committed_amount=Decimal("1800.5"), currency="USD", tx_now=TX_NOW
    )
    assert opening.committed_amount is not None
    assert opening.committed_amount.as_tuple().exponent == -4
    assert str(opening.committed_amount) == "1800.5000"
    assert str(opening.outstanding_amount) == "1800.5000"


def test_a_payment_against_a_kernel_created_commitment_leaves_two_twenty() -> None:
    """USD 420.00 committed, USD 200.00 admitted, USD 220.00 outstanding.

    The first commit creates the obligation the way a proposal does; the second
    admits a payment against the id the first minted. This is the only
    hermetic shape in which ``outstanding = committed - fulfilled`` can differ
    from ``outstanding = committed``, so it is the one that can fail if the
    identity is ever replaced by a copy.
    """
    from services.control_plane.app.memory_kernel import money_ops

    damage = ProposedCommitment(
        local_id="cm_001",
        commitment_type=CommitmentType.MONETARY_REIMBURSEMENT,
        description="Reimbursement of USD 420.00 for damage caused during the move.",
        obligor_type=ActorType.COUNTERPARTY,
        obligor_ref="beltline-movers",
        beneficiary_type=ActorType.USER,
        beneficiary_ref=str(USER),
        committed=_money("420.00"),
        source_claim_local_id="cl_001",
        confidence=Decimal("0.9600"),
    )
    opened = _run(_proposal(commitments=(damage,)), _snapshot()).plan.commitments[0]
    assert opened.committed_amount == Decimal("420.0000")
    assert opened.outstanding_amount == Decimal("420.0000")

    payment = _claim(
        predicate="payment_received",
        object_type=ValueType.MONEY,
        object_value={
            "currency": "USD",
            "amount": "200.00",
            "paid_at": "2026-05-25T12:00:00+00:00",
        },
        subject_type=SubjectType.COMMITMENT,
        subject_id=opened.commitment_id,
    )
    after = pipeline.build_write_plan(
        MemoryProposal(
            proposal_id=uuid.UUID(int=0x9101),
            proposal_type=ProposalType.INGESTION_INTERPRETATION,
            trace_id=TRACE,
            agent_run_id=uuid.UUID(int=0x9004),
            user_id=USER,
            source_artifact_ids=(ART,),
            evidence_ids=(EV_PROMISE,),
            identity=ProposalIdentity(
                relationship_id=REL, case_id=CASE, confidence=Decimal("0.9900")
            ),
            claims=(payment,),
            model=_model(),
            idempotency_key="beltline-damage-payment",
            created_at=OBSERVED_AT,
        ),
        snapshot=pipeline.AggregateSnapshot(
            case=case_ops.CaseRow(
                case_id=CASE,
                tenant_id=TENANT,
                user_id=USER,
                status=CaseStatus.OPEN,
                revision=4,
            ),
            relationship_id=REL,
            case_snapshot=case_ops.CaseSnapshot(
                evidence={
                    EV_PROMISE: case_ops.EvidenceRecord(
                        evidence_id=EV_PROMISE, created_at=OBSERVED_AT
                    )
                }
            ),
            commitments=(
                money_ops.CommitmentRow(
                    commitment_id=opened.commitment_id,
                    case_id=CASE,
                    status=opened.status,
                    currency=opened.currency,
                    committed_amount=opened.committed_amount,
                    fulfilled_amount=opened.fulfilled_amount,
                    outstanding_amount=opened.outstanding_amount,
                    revision=opened.revision,
                ),
            ),
        ),
        tx_now=TX_NOW,
        trace_id=TRACE,
        decision_id=DECISION_ID,
    ).plan
    assert len(after.commitment_updates) == 1
    update = after.commitment_updates[0]
    assert update.fulfilled_after == Decimal("200.0000")
    assert update.outstanding_after == Decimal("220.0000")
    assert update.status_after == str(CommitmentStatus.PARTIAL)


# ---------------------------------------------------------------------------
# The executor issues the statements, in DDL section 13 order
# ---------------------------------------------------------------------------


class _Recording:
    """Records every statement in the order it was executed."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[object] = []

    async def execute(self, query: str, params: object = None) -> _Recording:
        self.statements.append(" ".join(query.split()))
        self.params.append(params)
        return self

    async def fetchone(self) -> tuple[int, ...]:
        return (1,)

    rowcount = 1


def _executed(plan: pipeline.WritePlan) -> tuple[tuple[str, ...], _Recording]:
    import asyncio

    from services.control_plane.app.memory_kernel import transaction

    conn = _Recording()
    row = decisions.build_decision_row(
        decision_id=DECISION_ID,
        tenant_id=TENANT,
        user_id=USER,
        proposal_id=PROPOSAL,
        trace_id=TRACE,
        decision=KernelDecision.ACCEPTED,
        reason_codes=(KernelReasonCode.TRIGGER_ARMED,),
        case_id=CASE,
        case_revision_before=3,
        case_revision_after=4,
        tx_now=TX_NOW,
    )
    labels = asyncio.run(
        transaction.apply_write_plan(
            conn,
            plan,
            row=row,
            context=transaction.CommitContext(
                tenant_id=TENANT, user_id=USER, proposal_id=PROPOSAL, trace_id=TRACE
            ),
            tx_now=TX_NOW,
        )
    )
    return labels, conn


def test_a_planned_commitment_issues_an_insert_into_commitments() -> None:
    """Before this, ``INSERT INTO commitments`` existed nowhere in the tree."""
    plan = _run(_proposal(commitments=(_deposit_commitment(),)), _snapshot()).plan
    labels, conn = _executed(plan)
    assert "commitments_insert" in labels
    inserts = [s for s in conn.statements if s.startswith("INSERT INTO commitments")]
    assert len(inserts) == 1


def test_the_commitment_insert_precedes_the_fulfillment_insert() -> None:
    """``fk_fulfillments_commitment`` is validated at statement time, so an
    obligation opened and settled in one commit has to be inserted first."""
    from services.control_plane.app.memory_kernel import money_ops

    plan = _run(_proposal(commitments=(_deposit_commitment(),)), _snapshot()).plan
    settled = pipeline.WritePlan(
        claims=plan.claims,
        commitments=plan.commitments,
        fulfillments=(
            pipeline.FulfillmentRowWrite(
                fulfillment_id=uuid.UUID(int=0xD001),
                commitment_id=plan.commitments[0].commitment_id,
                evidence_id=EV_PROMISE,
                admission_status=str(money_ops.FulfillmentAdmissionStatus.ADMITTED),
                currency="USD",
                amount=Decimal("1800.0000"),
                fulfilled_at=TX_NOW,
            ),
        ),
        case_update=plan.case_update,
        transitions=plan.transitions,
        outbox=plan.outbox,
    )
    labels, _ = _executed(settled)
    assert labels.index("commitments_insert") < labels.index("fulfillments")


def test_a_planned_trigger_issues_both_prospective_trigger_statements() -> None:
    plan = _run(
        _proposal(triggers=(_arm_trigger(), _disarm_trigger())),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan
    labels, conn = _executed(plan)
    assert labels.count("prospective_triggers") == 2
    assert any(s.startswith("INSERT INTO prospective_triggers") for s in conn.statements)
    assert any(s.startswith("UPDATE prospective_triggers") for s in conn.statements)


def test_the_trigger_statements_sit_between_the_ledger_and_the_proposal() -> None:
    """DDL section 13 step 9 is after step 8's ``state_transitions`` and before
    step 10's ``memory_proposals``."""
    plan = _run(
        _proposal(triggers=(_arm_trigger(),)),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan
    labels, _ = _executed(plan)
    assert labels.index("state_transitions") < labels.index("prospective_triggers")
    assert labels.index("prospective_triggers") < labels.index("memory_proposals")


def test_the_disarm_update_clears_fired_at() -> None:
    """``ck_prospective_triggers_fired`` is a biconditional on ``state``, so a
    trigger that had fired cannot be disarmed while it still carries a
    ``fired_at``."""
    plan = _run(_proposal(triggers=(_disarm_trigger(),)), _snapshot()).plan
    _, conn = _executed(plan)
    update = next(s for s in conn.statements if s.startswith("UPDATE prospective_triggers"))
    assert "fired_at = NULL" in update
    assert "state = 'DISARMED'" in update
    assert "last_result = 'DISARMED'" in update


# ---------------------------------------------------------------------------
# `subject_local_ref` — a proposal-scoped name the Kernel has to resolve
# ---------------------------------------------------------------------------
#
# `ProposedClaim` requires exactly one of `subject_id` and `subject_local_ref`,
# and `agents/runtime/nodes/ingestion.py` can only produce the second:
# `ClaimCandidate` has no `subject_id` field. Nothing resolved it. The claim was
# written with `subject_id = case_id` whatever `subject_type` said, and
# `_normalise` was handed the nil UUID, so:
#
#   * `snapshot.incumbent_for(...)` never matched, every belief came out at
#     `version_no = 1` with no supersession and no conflict row; and
#   * `uq_beliefs_proposition` is UNIQUE on
#     `(tenant_id, user_id, subject_type, subject_id, predicate)` with no
#     `case_id`, so every RELATIONSHIP-scoped belief of one predicate collapsed
#     onto the nil-subject row -- and the Northline old/new account pair, which
#     `CANONICAL_DECISIONS.md` calls the sharpest decoy in the corpus, became
#     one belief with the second case raising 23505.
#
# All of it silent. Rule R7 is what makes the resolution deterministic: one
# proposal is one case, and that case has exactly one relationship.


def _local_ref_claim(
    *, subject_type: SubjectType, hint: str = "northline-old-account"
) -> ProposedClaim:
    return ProposedClaim(
        local_id="cl_001",
        claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
        subject_type=subject_type,
        subject_local_ref=hint,
        predicate="balance_owed",
        object_type=ValueType.MONEY,
        object_value={"currency": "USD", "amount": "186.0000"},
        actor_type=ActorType.COUNTERPARTY,
        actor_ref="northline-fiber",
        evidence_id=EV_PROMISE,
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
        modality=Modality.ASSERTED_PRESENT,
        valid_from=OBSERVED_AT,
        valid_to=None,
        extraction_confidence=Decimal("0.9100"),
    )


def test_a_relationship_local_ref_resolves_to_the_case_relationship() -> None:
    """Rule R7: one proposal is one case, and the case names one relationship,
    so a relationship-scoped subject in that proposal can only be that one."""
    claim = _local_ref_claim(subject_type=SubjectType.RELATIONSHIP)
    plan = _run(_proposal(claims=(claim,)), _snapshot()).plan
    assert plan.claims[0].subject_type is SubjectType.RELATIONSHIP
    assert plan.claims[0].subject_id == REL
    assert plan.claims[0].subject_id != CASE


def test_a_case_local_ref_resolves_to_the_proposals_case() -> None:
    claim = _local_ref_claim(subject_type=SubjectType.CASE, hint="this-case")
    plan = _run(_proposal(claims=(claim,)), _snapshot()).plan
    assert plan.claims[0].subject_id == CASE


def test_a_resolved_local_ref_reaches_the_matcher_as_the_same_subject() -> None:
    """The belief the claim grounds has to hang off the resolved subject.

    ``_normalise`` used ``uuid.UUID(int=0)`` when ``subject_id`` was absent, so
    the proposition's subject was the nil UUID while the claim row's was the
    case id: two different answers to one question, and the matcher was reading
    the wrong one.
    """
    claim = _local_ref_claim(subject_type=SubjectType.RELATIONSHIP)
    plan = _run(_proposal(claims=(claim,)), _snapshot()).plan
    assert len(plan.beliefs) == 1
    assert plan.beliefs[0].subject_type is SubjectType.RELATIONSHIP
    assert plan.beliefs[0].subject_id == REL
    assert plan.beliefs[0].subject_id != uuid.UUID(int=0)


def test_an_unresolvable_local_ref_is_pending_identity_not_a_guess() -> None:
    """A relationship-scoped claim on a case with no relationship has nothing to
    resolve to. ``PENDING_IDENTITY`` is a better outcome than a confident write
    to the wrong subject, which is what the case-id fallback was."""
    claim = _local_ref_claim(subject_type=SubjectType.RELATIONSHIP)
    snapshot = pipeline.AggregateSnapshot(
        case=case_ops.CaseRow(
            case_id=CASE,
            tenant_id=TENANT,
            user_id=USER,
            status=CaseStatus.OPEN,
            revision=3,
        ),
        relationship_id=None,
        case_snapshot=case_ops.CaseSnapshot(
            evidence={
                EV_PROMISE: case_ops.EvidenceRecord(evidence_id=EV_PROMISE, created_at=OBSERVED_AT)
            }
        ),
    )
    outcome = _run(_proposal(claims=(claim,)), snapshot)
    assert outcome.decision is KernelDecision.PENDING_IDENTITY
    assert KernelReasonCode.IDENTITY_UNRESOLVED in outcome.reason_codes
    assert outcome.plan.is_canonical_noop()


def test_a_local_ref_the_kernel_has_no_rule_for_is_pending_identity() -> None:
    """``COUNTERPARTY`` is a legal ``claims.subject_type`` and the transaction
    reads no counterparty rows, so there is nothing deterministic to resolve a
    hint against. Fail closed rather than invent a subject."""
    claim = _local_ref_claim(subject_type=SubjectType.COUNTERPARTY)
    outcome = _run(_proposal(claims=(claim,)), _snapshot())
    assert outcome.decision is KernelDecision.PENDING_IDENTITY
    assert KernelReasonCode.IDENTITY_UNRESOLVED in outcome.reason_codes


def test_an_explicit_subject_id_is_never_overridden_by_the_case() -> None:
    """The mirror of the bug: a claim that supplied its own subject keeps it."""
    other = uuid.UUID(int=0x1777)
    claim = ProposedClaim(
        local_id="cl_001",
        claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=other,
        predicate="balance_owed",
        object_type=ValueType.MONEY,
        object_value={"currency": "USD", "amount": "186.0000"},
        actor_type=ActorType.COUNTERPARTY,
        actor_ref="northline-fiber",
        evidence_id=EV_PROMISE,
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE,
        modality=Modality.ASSERTED_PRESENT,
        valid_from=OBSERVED_AT,
        valid_to=None,
        extraction_confidence=Decimal("0.9100"),
    )
    plan = _run(_proposal(claims=(claim,)), _snapshot()).plan
    assert plan.claims[0].subject_id == other


def test_a_binding_matches_by_name_when_the_producer_names_it() -> None:
    """``local_id = "cm_deposit"`` against ``commitments.deposit.*``.

    Name matching is what makes the bind *checkable* rather than inferred, and
    it is tried before elimination so that a producer adopting the convention
    gets the stronger rule without anything else changing.
    """
    plan = _run(
        _proposal(
            commitments=(_deposit_commitment(local_id="cm_deposit"),),
            triggers=(_arm_trigger(),),
        ),
        _snapshot(),
    ).plan
    assert plan.trigger_arms[0].predicate_ast["bindings"]["deposit"]["id"] == str(
        plan.commitments[0].commitment_id
    )


def test_a_name_mismatch_still_binds_while_elimination_justifies_it() -> None:
    """Every commitment in the repository is ``cm_001`` today.

    Requiring the name match would refuse **every** arm, the hero landlord
    deposit included, and leave ``prospective_triggers`` permanently empty -
    closing a conditional hazard by removing the feature. Measured against
    ``scripts/seed/proposals.py``: both curated triggers create their obligation
    in the same commit as ``cm_001`` and name bindings ``deposit`` and
    ``damage``, so a strict rule refuses 2 of 2.
    """
    plan = _run(
        _proposal(
            commitments=(_deposit_commitment(local_id="cm_001"),), triggers=(_arm_trigger(),)
        ),
        _snapshot(),
    ).plan
    assert len(plan.trigger_arms) == 1
    assert plan.trigger_arms[0].predicate_ast["bindings"]["deposit"]["id"] == str(
        plan.commitments[0].commitment_id
    )


def test_two_candidate_obligations_refuse_rather_than_pick_one() -> None:
    """The shape elimination cannot justify at all. One name, two obligations -
    there is no reading of the predicate that says which."""
    second = ProposedCommitment(
        local_id="cm_002",
        commitment_type=CommitmentType.MONETARY_REIMBURSEMENT,
        description="Reimbursement of USD 420.00 for damage caused during the move.",
        obligor_type=ActorType.COUNTERPARTY,
        obligor_ref="beltline-movers",
        beneficiary_type=ActorType.USER,
        beneficiary_ref=str(USER),
        committed=_money("420.00"),
        source_claim_local_id="cl_001",
        confidence=Decimal("0.9600"),
    )
    outcome = _run(
        _proposal(commitments=(_deposit_commitment(), second), triggers=(_arm_trigger(),)),
        _snapshot(),
    )
    assert outcome.decision is KernelDecision.PENDING_HUMAN_REVIEW
    assert KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE in outcome.reason_codes
    assert outcome.plan.trigger_arms == ()


def test_the_curated_triggers_all_arm() -> None:
    """The regression that matters: a binding rule that refuses the fixtures
    takes the second reveal down with it.

    ``scripts/seed/proposals.py`` is read rather than transcribed, so a change
    there that breaks arming fails here rather than in a twelve-minute database
    run.
    """
    from scripts.seed.proposals import CURATED_PROPOSALS

    armed = 0
    for seeded in CURATED_PROPOSALS:
        for mutation in seeded.proposal.trigger_mutations:
            if mutation.mutation_kind is not TriggerMutationKind.ARM:
                continue
            assert mutation.predicate is not None
            names = {
                path.split(".")[1]
                for path in mutation.predicate.field_paths()
                if path.startswith("commitments.")
            }
            created = tuple(
                pipeline.CommitmentRowWrite(
                    commitment_id=uuid.uuid4(),
                    case_id=CASE,
                    obligor_type="COUNTERPARTY",
                    beneficiary_type="USER",
                    commitment_type="DEPOSIT_RETURN",
                    description="d",
                    source_claim_id=uuid.uuid4(),
                    status=CommitmentStatus.ACTIVE,
                    binding_name=c.local_id.removeprefix("cm_"),
                )
                for c in seeded.proposal.commitments
            )
            bindings = pipeline._trigger_bindings(mutation.predicate, created, _snapshot())
            assert bindings is not None, (
                f"{seeded.case_slug}: bindings {sorted(names)} against "
                f"{[c.local_id for c in seeded.proposal.commitments]} would refuse"
            )
            assert set(bindings) == names
            armed += 1
    assert armed == 2, "the curated seed arms the deposit and the damage follow-up"


def test_naming_the_obligations_binds_where_elimination_would_refuse() -> None:
    """The name-match branch, in the only shape that can distinguish it.

    With one obligation on the case, elimination reaches the same answer, so a
    single-commitment fixture cannot tell the two branches apart - it passed
    with the name lookup deleted. Two **named** obligations and one binding is
    the shape where they disagree: the name resolves it and elimination refuses
    it, so deleting the lookup turns this test red.
    """
    damage = ProposedCommitment(
        local_id="cm_damage",
        commitment_type=CommitmentType.MONETARY_REIMBURSEMENT,
        description="Reimbursement of USD 420.00 for damage caused during the move.",
        obligor_type=ActorType.COUNTERPARTY,
        obligor_ref="beltline-movers",
        beneficiary_type=ActorType.USER,
        beneficiary_ref=str(USER),
        committed=_money("420.00"),
        source_claim_local_id="cl_001",
        confidence=Decimal("0.9600"),
    )
    plan = _run(
        _proposal(
            commitments=(_deposit_commitment(local_id="cm_deposit"), damage),
            triggers=(_arm_trigger(),),
        ),
        _snapshot(),
    ).plan
    assert len(plan.commitments) == 2
    assert len(plan.trigger_arms) == 1
    deposit = next(row for row in plan.commitments if row.binding_name == "deposit")
    bound = plan.trigger_arms[0].predicate_ast["bindings"]["deposit"]["id"]
    assert bound == str(deposit.commitment_id)
    assert bound != str(
        next(row for row in plan.commitments if row.binding_name == "damage").commitment_id
    )


def test_the_arm_statement_carries_the_generation_and_the_schedule_name() -> None:
    """The executor has to pass them, not just the plan hold them.

    ``evaluation_version`` was previously a literal ``0`` inside the INSERT, and
    the plan's value went nowhere. Reading the bound parameters is what catches
    a statement that silently stops carrying a field the row depends on.
    """
    from services.control_plane.app.triggers import config as trigger_config

    plan = _run(
        _proposal(triggers=(_arm_trigger(),)),
        _snapshot(commitments=_deposit_on_the_case()),
    ).plan
    labels, conn = _executed(plan)
    index = labels.index("prospective_triggers")
    statement = conn.statements[index]
    params = conn.params[index]
    assert statement.startswith("INSERT INTO prospective_triggers")
    assert isinstance(params, dict)
    assert params["evaluation_version"] == 1
    assert params["schedule_name"] == trigger_config.schedule_name_for(
        plan.trigger_arms[0].trigger_id.hex, 1
    )
    assert params["basis_case_revision"] == plan.trigger_arms[0].basis_case_revision
