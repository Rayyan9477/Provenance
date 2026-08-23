"""A payment denial must not pay off the obligation it denies — CX-04 / CX-05.

Authority
---------
- ``docs/quality/22_EVAL_DATASETS.md`` ``CX-04`` and ``CX-05``, which are the
  canonical scenarios for this behaviour and supply every expected value here.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.6 matcher ``M5``
  (``asserted`` differs -> ``FULFILLMENT_CONFLICT`` / ``CONFLICT_PAYMENT_DENIAL``)
  and section 3.3 gates ``H5`` and the authority margin.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.1: ``payment_not_received`` is a
  surface form of the ``PAYMENT`` family and ``families.coerce_value`` turns it
  into ``PaymentValue(asserted=False)``.

The defect this module was written against
------------------------------------------
``pipeline._apply_payment`` destructured the ``PaymentValue`` into ``amount``
and ``currency`` and **never read** ``asserted``. A counterparty asserting that
a payment was *not* received was handed to ``money_ops.apply_fulfillment`` as a
positive payment: ``payment_received`` and ``payment_not_received`` produced
byte-identical output - ``ACCEPTED``, ``FULFILLMENT_ADMITTED``, an ``ADMITTED``
``fulfillments`` row, and the outstanding amount reduced by the denied sum.

``M5`` could not catch it. The ``Family.PAYMENT`` branch calls ``_apply_payment``
and then ``continue``s, so the only ``contradiction.match`` call in the write
path - the one for belief-bearing families - is structurally unreachable for
payments. Every DDL guard passes, because the invariants hold for the *wrong*
numbers, and ``asserted`` is not a ``fulfillments`` column, so the denial was
not recoverable after the fact either.

The entire product thesis is that a counterparty claim is not a fact. This was
a counterparty denial silently discharging a debt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from provenance_contracts.proposal import MemoryProposal, ProposalIdentity, ProposedClaim
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import (
    ActorType,
    AttentionLevel,
    CaseStatus,
    ClaimKind,
    CommitmentStatus,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    FulfillmentAdmissionStatus,
    KernelDecision,
    KernelReasonCode,
    Modality,
    ModelTier,
    ProposalType,
    SourceClass,
    SubjectType,
    ValueType,
)
from services.control_plane.app.memory_kernel import case_ops, money_ops, pipeline

pytestmark = pytest.mark.unit

TENANT = uuid.UUID(int=0x8001)
USER = uuid.UUID(int=0x8002)
REL = uuid.UUID(int=0x1001)
CASE = uuid.UUID(int=0x2001)
COMMITMENT = uuid.UUID(int=0x3002)
#: The bank statement that grounded the admitted payment, and the claim the
#: Kernel wrote from it. ``fulfillments`` carries the evidence; the authority
#: lives on the claim, exactly as it does for a belief incumbent.
EV_BANK = uuid.UUID(int=0x6009)
CLAIM_BANK = uuid.UUID(int=0x4009)
#: The support-chat transcript that denies it.
EV_CHAT = uuid.UUID(int=0x6001)
ART = uuid.UUID(int=0x7001)
PROPOSAL = uuid.UUID(int=0x9001)
TRACE = uuid.UUID(int=0x9002)
DECISION_ID = uuid.UUID(int=0x9003)

TX_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
PAID_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _model() -> ModelAttribution:
    return ModelAttribution(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tier=ModelTier.E,
        prompt_version="pv-extract-1.0.0",
        graph_name="ingestion",
        graph_version="1.0.0",
    )


def _payment_claim(
    *,
    predicate: str,
    amount: str,
    source_class: SourceClass = SourceClass.PROVIDER_AGENT_CHAT,
    claim_kind: ClaimKind = ClaimKind.COUNTERPARTY_CLAIM,
    subject_id: uuid.UUID = COMMITMENT,
    currency: str = "USD",
) -> ProposedClaim:
    return ProposedClaim(
        local_id="cl_001",
        claim_kind=claim_kind,
        subject_type=SubjectType.COMMITMENT,
        subject_id=subject_id,
        predicate=predicate,
        object_type=ValueType.MONEY,
        object_value={
            "currency": currency,
            "amount": amount,
            "paid_at": PAID_AT.isoformat(),
        },
        actor_type=ActorType.COUNTERPARTY,
        actor_ref="beltline-movers",
        evidence_id=EV_CHAT,
        source_class=source_class,
        modality=Modality.ASSERTED_PAST,
        valid_from=PAID_AT,
        valid_to=None,
        extraction_confidence=Decimal("0.9100"),
    )


def _proposal(claim: ProposedClaim) -> MemoryProposal:
    return MemoryProposal(
        proposal_id=PROPOSAL,
        proposal_type=ProposalType.INGESTION_INTERPRETATION,
        trace_id=TRACE,
        agent_run_id=uuid.UUID(int=0x9004),
        user_id=USER,
        source_artifact_ids=(ART,),
        evidence_ids=(EV_CHAT,),
        identity=ProposalIdentity(relationship_id=REL, case_id=CASE, confidence=Decimal("0.9700")),
        claims=(claim,),
        model=_model(),
        idempotency_key="cx04-denial-0001",
        created_at=PAID_AT,
    )


def _snapshot(
    *,
    committed: str = "420.0000",
    fulfilled: str = "40.0000",
    admitted: str = "40.0000",
    status: CommitmentStatus = CommitmentStatus.PARTIAL,
    ledger_authority: Decimal | None = Decimal("0.9700"),
    ledger_claim_id: uuid.UUID | None = CLAIM_BANK,
    commitment_id: uuid.UUID = COMMITMENT,
) -> pipeline.AggregateSnapshot:
    """CX-04's prior canonical state: a commitment with an admitted payment.

    ``authority`` on the ledger row is the **bank statement's** grid score,
    ``(PAYMENT, BANK_OR_CARD_STATEMENT) = 0.9700``, read back from the claim
    that grounded the fulfillment. ``belief_versions`` has no ``source_class``
    column and neither does ``fulfillments``, so the grid key is not recoverable
    from persisted state; the score is.
    """
    outstanding = Decimal(committed) - Decimal(fulfilled)
    return pipeline.AggregateSnapshot(
        case=case_ops.CaseRow(
            case_id=CASE,
            tenant_id=TENANT,
            user_id=USER,
            status=CaseStatus.WAITING,
            revision=12,
        ),
        relationship_id=REL,
        case_snapshot=case_ops.CaseSnapshot(
            evidence={EV_CHAT: case_ops.EvidenceRecord(evidence_id=EV_CHAT, created_at=PAID_AT)}
        ),
        commitments=(
            money_ops.CommitmentRow(
                commitment_id=commitment_id,
                case_id=CASE,
                status=status,
                currency="USD",
                committed_amount=Decimal(committed),
                fulfilled_amount=Decimal(fulfilled),
                outstanding_amount=outstanding,
                revision=3,
            ),
        ),
        fulfillment_ledger={
            commitment_id: (
                money_ops.FulfillmentRow(
                    amount=Decimal(admitted),
                    currency="USD",
                    admission_status=FulfillmentAdmissionStatus.ADMITTED,
                    evidence_id=EV_BANK,
                    fulfilled_at=PAID_AT,
                    authority=ledger_authority,
                    source_claim_id=ledger_claim_id,
                ),
            )
        },
    )


def _run(claim: ProposedClaim, snapshot: pipeline.AggregateSnapshot) -> pipeline.PipelineOutcome:
    return pipeline.build_write_plan(
        _proposal(claim),
        snapshot=snapshot,
        tx_now=TX_NOW,
        trace_id=TRACE,
        decision_id=DECISION_ID,
    )


# ---------------------------------------------------------------------------
# CX-04 — below the money gate
# ---------------------------------------------------------------------------


def _cx04() -> pipeline.PipelineOutcome:
    return _run(_payment_claim(predicate="payment_not_received", amount="40.0000"), _snapshot())


def test_cx04_the_denial_does_not_reach_the_ledger() -> None:
    """*"The denial claim is preserved; the ledger is unchanged."*

    The bug in one assertion: before the fix this planned an ``ADMITTED``
    ``fulfillments`` row of USD 40.00 and took the admitted total from 40 to 80.
    """
    plan = _cx04().plan
    assert plan.fulfillments == ()


def test_cx04_the_denial_is_preserved_as_a_claim() -> None:
    """The evidence is admitted whatever the Kernel decides about it. Invariant
    1: a claim is recorded, never suppressed."""
    plan = _cx04().plan
    assert len(plan.claims) == 1
    assert plan.claims[0].predicate == "payment_not_received"
    assert plan.claims[0].object_json["amount"] == "40.0000"


def test_cx04_raises_a_fulfillment_conflict_naming_the_denial() -> None:
    """``M5``: ``asserted`` differs, so the two payment propositions cannot both
    be right."""
    outcome = _cx04()
    assert outcome.decision is KernelDecision.ACCEPTED_WITH_CONFLICT
    assert len(outcome.plan.conflicts) == 1
    conflict = outcome.plan.conflicts[0]
    assert conflict.conflict_type is ConflictType.FULFILLMENT_CONFLICT
    assert conflict.subject_type is SubjectType.COMMITMENT
    assert conflict.subject_id == COMMITMENT
    assert conflict.predicate == "payment_received"
    assert KernelReasonCode.CONFLICT_PAYMENT_DENIAL in outcome.reason_codes


def test_cx04_auto_resolves_on_the_authority_margin() -> None:
    """Δ = 0.97 - 0.45 = 0.52 ≥ 0.25 and the winner 0.97 ≥ 0.80, and the
    monetary exposure 40.00 is below the 100.00 gate so ``H5`` never fires."""
    outcome = _cx04()
    assert KernelReasonCode.AUTO_RESOLVED_AUTHORITY_MARGIN in outcome.reason_codes
    assert KernelReasonCode.HUMAN_REQUIRED_MONETARY_THRESHOLD not in outcome.reason_codes
    conflict = outcome.plan.conflicts[0]
    assert conflict.status is ConflictStatus.AUTO_RESOLVED
    assert not conflict.requires_human


def test_cx04_leaves_the_commitment_status_and_amounts_alone() -> None:
    """*"commitment:damage status unchanged (an AUTO_RESOLVED conflict does not
    trip the dispute-dominates rule)."*"""
    plan = _cx04().plan
    assert plan.commitment_updates == ()


def test_cx04_conflict_sides_are_ordered_for_the_check_constraint() -> None:
    """``ck_conflicts_side_order`` is ``left_source_id <= right_source_id``, and
    it is what makes ``uq_conflicts_live_identity`` real: an index on the pair
    is trivially defeated by swapping the arguments."""
    conflict = _cx04().plan.conflicts[0]
    assert conflict.left_source_id <= conflict.right_source_id
    assert conflict.left_source_id != conflict.right_source_id


def test_an_asserted_payment_and_a_denial_no_longer_agree() -> None:
    """The measurement that found the defect: the two surface predicates
    produced byte-identical plans."""
    received = _run(_payment_claim(predicate="payment_received", amount="40.0000"), _snapshot())
    denied = _cx04()
    assert received.decision is not denied.decision
    assert len(received.plan.fulfillments) == 1
    assert denied.plan.fulfillments == ()
    assert received.plan.conflicts == ()
    assert len(denied.plan.conflicts) == 1


# ---------------------------------------------------------------------------
# CX-05 — above the money gate
# ---------------------------------------------------------------------------


def _cx05() -> pipeline.PipelineOutcome:
    """The same wording for USD 200.00 against the 420/200/220 commitment."""
    return _run(
        _payment_claim(predicate="payment_not_received", amount="200.0000"),
        _snapshot(committed="420.0000", fulfilled="200.0000", admitted="200.0000"),
    )


def test_cx05_the_money_gate_fires_before_the_authority_margin() -> None:
    """*"monetary_exposure = 200.00 ≥ 100.00 → H5 fires before the authority
    margin is even computed."*

    The exposure of a denial is the **denied amount**: one side says 200.00
    arrived and the other says nothing did, so ``abs(200 - 0) = 200``. Read as
    ``abs(200 - 200) = 0`` the gate could never fire for any denial, and CX-05
    would auto-resolve exactly like CX-04 - which is the pair that exists to
    straddle the threshold.
    """
    outcome = _cx05()
    assert KernelReasonCode.HUMAN_REQUIRED_MONETARY_THRESHOLD in outcome.reason_codes
    assert KernelReasonCode.AUTO_RESOLVED_AUTHORITY_MARGIN not in outcome.reason_codes
    conflict = outcome.plan.conflicts[0]
    assert conflict.status is ConflictStatus.NEEDS_HUMAN
    assert conflict.requires_human
    assert conflict.severity is ConflictSeverity.HIGH
    assert outcome.attention_required


def test_cx05_disputes_the_commitment_without_moving_the_money() -> None:
    """*"commitment:damage → DISPUTED (dispute dominates). Amounts unchanged:
    420/200/220."*"""
    plan = _cx05().plan
    assert plan.fulfillments == ()
    assert len(plan.commitment_updates) == 1
    update = plan.commitment_updates[0]
    assert update.status_before == str(CommitmentStatus.PARTIAL)
    assert update.status_after == str(CommitmentStatus.DISPUTED)
    assert update.fulfilled_after == Decimal("200.0000")
    assert update.outstanding_after == Decimal("220.0000")
    assert update.revision_after == update.revision_before + 1


def test_cx04_and_cx05_straddle_the_human_review_threshold() -> None:
    """The pair is the regression test for ``human_review_amount_threshold``;
    asserting them apart lets one drift across the boundary unnoticed.

    ``attention_required`` is **not** the discriminator - the pipeline raises it
    for any conflict, which is the hero's shape too. The discriminators are the
    conflict status, whether a person is required, and the attention level the
    disposition assigned.
    """
    below, above = _cx04(), _cx05()
    assert below.plan.conflicts[0].status is ConflictStatus.AUTO_RESOLVED
    assert above.plan.conflicts[0].status is ConflictStatus.NEEDS_HUMAN
    assert not below.plan.conflicts[0].requires_human
    assert above.plan.conflicts[0].requires_human
    assert below.attention_level is AttentionLevel.INFO
    assert above.attention_level is AttentionLevel.ATTENTION


# ---------------------------------------------------------------------------
# The paths a denial must not take
# ---------------------------------------------------------------------------


def test_a_denial_the_ledger_cannot_speak_to_moves_nothing() -> None:
    """A denial of a payment nothing ever admitted contradicts nothing.

    The claim is still recorded - that is invariant 1 - and it still has to
    carry a reason code, because an accepted commit with none cannot be built.
    """
    outcome = _run(
        _payment_claim(predicate="payment_not_received", amount="40.0000"),
        pipeline.AggregateSnapshot(
            case=case_ops.CaseRow(
                case_id=CASE,
                tenant_id=TENANT,
                user_id=USER,
                status=CaseStatus.WAITING,
                revision=12,
            ),
            relationship_id=REL,
            case_snapshot=case_ops.CaseSnapshot(
                evidence={EV_CHAT: case_ops.EvidenceRecord(evidence_id=EV_CHAT, created_at=PAID_AT)}
            ),
            commitments=(
                money_ops.CommitmentRow(
                    commitment_id=COMMITMENT,
                    case_id=CASE,
                    status=CommitmentStatus.ACTIVE,
                    currency="USD",
                    committed_amount=Decimal("420.0000"),
                    fulfilled_amount=Decimal("0.0000"),
                    outstanding_amount=Decimal("420.0000"),
                    revision=3,
                ),
            ),
        ),
    )
    assert outcome.plan.fulfillments == ()
    assert outcome.plan.conflicts == ()
    assert outcome.plan.commitment_updates == ()
    assert len(outcome.plan.claims) == 1
    assert outcome.reason_codes


def test_a_payment_claim_naming_no_known_commitment_still_carries_a_code() -> None:
    """Measured, not predicted: this produced ``reason_codes = ()`` and an
    ``ACCEPTED`` decision, so ``decisions.build_decision_row`` raised inside the
    transaction - the same crash as the unmapped-predicate case, reached through
    a mapped predicate whose subject the snapshot did not contain.
    """
    outcome = _run(
        _payment_claim(
            predicate="payment_received",
            amount="200.0000",
            subject_id=uuid.UUID(int=0xBEEF),
        ),
        _snapshot(),
    )
    assert len(outcome.plan.claims) == 1
    assert outcome.plan.fulfillments == ()
    assert outcome.reason_codes, "an accepted commit with no reason code cannot be audited"


def test_a_denial_that_would_win_is_escalated_rather_than_reversing_the_ledger() -> None:
    """A promoted denial means reversing an admitted fulfillment, and v1 has no
    statement that does that. Fail closed to a person rather than record a
    conflict that says the denial won while the ledger says it did not.
    """
    outcome = _run(
        _payment_claim(
            predicate="payment_not_received",
            amount="40.0000",
            source_class=SourceClass.BANK_OR_CARD_STATEMENT,
        ),
        _snapshot(ledger_authority=Decimal("0.4500")),
    )
    conflict = outcome.plan.conflicts[0]
    assert conflict.status is ConflictStatus.NEEDS_HUMAN
    assert conflict.requires_human
    assert outcome.plan.fulfillments == ()
    assert KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE in outcome.reason_codes


def test_a_denial_against_an_unreadable_ledger_row_escalates() -> None:
    """The ledger row's grounding claim carries both the conflict's side id and
    the authority the margin is measured on. Without it the Kernel can write no
    well-formed conflict, so it escalates instead of falling silent - the
    difference between "nothing to contradict" and "something to contradict that
    I cannot read" is the difference between a no-op and a question.
    """
    outcome = _run(
        _payment_claim(predicate="payment_not_received", amount="40.0000"),
        _snapshot(ledger_authority=None, ledger_claim_id=None),
    )
    assert outcome.plan.fulfillments == ()
    assert outcome.plan.commitment_updates == ()
    assert KernelReasonCode.HUMAN_REQUIRED_UNRESOLVABLE_TYPE in outcome.reason_codes
    assert outcome.attention_required


def test_a_denial_with_no_authority_cannot_auto_resolve() -> None:
    """The claim id resolves but the score does not: there is a conflict to
    write, and it may not auto-resolve against a payment whose standing the
    Kernel could not recover."""
    outcome = _run(
        _payment_claim(predicate="payment_not_received", amount="40.0000"),
        _snapshot(ledger_authority=None),
    )
    assert outcome.plan.fulfillments == ()
    assert len(outcome.plan.conflicts) == 1
    assert outcome.plan.conflicts[0].requires_human
    assert outcome.plan.conflicts[0].status is ConflictStatus.NEEDS_HUMAN


# ---------------------------------------------------------------------------
# The two places the fix is only as good as its plumbing
# ---------------------------------------------------------------------------


def test_the_ledger_read_carries_the_grounding_claim_and_its_authority() -> None:
    """``_READ_LEDGER_SQL`` -> :class:`money_ops.FulfillmentRow`, mapped.

    Every other test here builds the ledger row by hand, so none of them can
    see the SQL projection drift. Both columns are load-bearing: the claim id is
    the conflict's side id and the score is the margin ``H5`` is measured
    against, and with either missing a denial escalates instead of resolving.
    """
    from services.control_plane.app.memory_kernel import transaction

    assert "cl.id, cl.authority_score" in transaction._READ_LEDGER_SQL
    row = transaction._ledger_row(
        (
            COMMITMENT,
            Decimal("40.0000"),
            "USD",
            "ADMITTED",
            EV_BANK,
            PAID_AT,
            CLAIM_BANK,
            Decimal("0.9700"),
        )
    )
    assert row.source_claim_id == CLAIM_BANK
    assert row.authority == Decimal("0.9700")
    assert row.evidence_id == EV_BANK
    assert row.admission_status is FulfillmentAdmissionStatus.ADMITTED


#: The two ends of the UUID range, so the ordering test covers both sides of the
#: comparison rather than whichever one the fixture happened to land on. Every
#: other commitment id in this module is a small integer, which sorts **below**
#: every ``uuid4`` the normaliser mints -- so a test that used one would have
#: passed against an unordered pair forever.
_LOW_COMMITMENT = uuid.UUID(int=0x3002)
_HIGH_COMMITMENT = uuid.UUID(int=(1 << 128) - 1)


@pytest.mark.parametrize("commitment_id", [_LOW_COMMITMENT, _HIGH_COMMITMENT])
def test_a_currency_rejection_orders_its_conflict_sides(commitment_id: uuid.UUID) -> None:
    """``ck_conflicts_side_order`` is ``left_source_id <= right_source_id``.

    The money-conflict path paired ``COMMITMENT`` with the *left* position and
    ``EVIDENCE`` with the right, unconditionally. The other side is a freshly
    minted ``uuid4``, so which way the pair sorts is a property of the
    commitment id: a small one always sorts below and the CHECK is satisfied by
    luck, while a large one always sorts above and the write is refused.
    """
    for _ in range(8):
        outcome = _run(
            _payment_claim(
                predicate="payment_received",
                amount="40.0000",
                currency="EUR",
                subject_id=commitment_id,
            ),
            _snapshot(commitment_id=commitment_id),
        )
        assert len(outcome.plan.conflicts) == 1
        conflict = outcome.plan.conflicts[0]
        assert (
            conflict.left_source_id <= conflict.right_source_id
        ), "ck_conflicts_side_order refuses an unordered pair"
        assert conflict.left_source_kind != conflict.right_source_kind


def test_a_currency_rejection_still_records_the_status_it_decided() -> None:
    """Section 4.2 step 1: the fulfillment is written ``REJECTED_CURRENCY`` and
    the commitment becomes ``DISPUTED``. The UPDATE that records the status was
    only emitted when a fulfillment row existed, so a status move with no
    admitted money was decided and then not written."""
    outcome = _run(
        _payment_claim(predicate="payment_received", amount="40.0000", currency="EUR"),
        _snapshot(),
    )
    assert len(outcome.plan.commitment_updates) == 1
    update = outcome.plan.commitment_updates[0]
    assert update.status_after == str(CommitmentStatus.DISPUTED)
    assert update.fulfilled_after == Decimal("40.0000")
    assert update.outstanding_after == Decimal("380.0000")


def test_the_ledger_read_will_not_bind_a_denial_as_its_own_grounding() -> None:
    """A denial shares the evidence and the subject of the payment it denies.

    ``fulfillments`` has no column naming the claim that produced it, so the
    ledger read resolves the grounding claim by ``(evidence_id, subject)``. Left
    unnarrowed, a denial can be selected as that fulfillment's own grounding and
    the authority margin is then measured between a claim and itself - which
    auto-resolves every denial against itself, silently, in exactly the shape
    this module exists to prevent.
    """
    from services.control_plane.app.memory_kernel import families, transaction

    assert set(families.PAYMENT_DENIAL_PREDICATES) == {"payment_not_received"}
    assert families.ASSERTED_PAYMENT_PREDICATES == ("payment_received", "payment_sent")
    assert (
        transaction._READ_LEDGER_SQL.count("predicate = ANY(%(asserted_payment_predicates)s)") == 1
    )
    #: Every PAYMENT surface predicate is on exactly one of the two lists.
    payment_predicates = {
        p for p, f in families.SURFACE_PREDICATES.items() if f is families.Family.PAYMENT
    }
    assert payment_predicates == set(families.ASSERTED_PAYMENT_PREDICATES) | (
        families.PAYMENT_DENIAL_PREDICATES
    )


def test_the_polarity_rule_has_one_definition() -> None:
    """``coerce_value`` reads the same constant the SQL does, so a second denial
    predicate cannot be recognised by one and missed by the other.

    The source check is the load-bearing half. The two spellings - the constant
    and the inlined ``"payment_not_received"`` literal it replaced - are the
    same value today, so no behavioural assertion can tell them apart; what a
    behavioural assertion cannot see is a *second* denial predicate added to the
    constant and missed by an inlined literal, which is the drift the constant
    exists to prevent.
    """
    import inspect

    from services.control_plane.app.memory_kernel import families

    source = inspect.getsource(families.coerce_value)
    assert "PAYMENT_DENIAL_PREDICATES" in source
    assert '"payment_not_received"' not in source

    for predicate in families.ASSERTED_PAYMENT_PREDICATES:
        value = families.coerce_value(
            predicate,
            {"currency": "USD", "amount": Decimal("40.0000"), "paid_at": PAID_AT},
        )
        assert isinstance(value, families.PaymentValue)
        assert value.asserted is True
    for predicate in sorted(families.PAYMENT_DENIAL_PREDICATES):
        value = families.coerce_value(
            predicate,
            {"currency": "USD", "amount": Decimal("40.0000"), "paid_at": PAID_AT},
        )
        assert isinstance(value, families.PaymentValue)
        assert value.asserted is False
