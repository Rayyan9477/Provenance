"""T4.5 - the monetary algorithm: recompute, currency, over-fulfilment.

`specs/12_KERNEL_ALGORITHMS.md` section 4, and the CHECK constraints of
`specs/10_DATABASE_DDL.md` section 7.2 that the algorithm must never be able to
breach. Money is `Decimal` here and in the assertions; `pytest.approx` on money
is rejected at review.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import EllipsisType
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from provenance_domain import money as domain_money
from provenance_domain.enums import (
    CommitmentStatus,
    ConflictStatus,
    ConflictType,
    FulfillmentAdmissionStatus,
    KernelReasonCode,
)
from services.control_plane.app.memory_kernel import money_ops as mo
from services.control_plane.app.memory_kernel.config import KernelConfig

pytestmark = pytest.mark.unit


def _commitment(
    hero: Any,
    *,
    committed: Decimal | None | EllipsisType = ...,
    currency: str | None = "USD",
    status: CommitmentStatus = CommitmentStatus.ACTIVE,
    fulfilled: Decimal | None = None,
    outstanding: Decimal | None = None,
    revision: int = 2,
    valid_to: Any = None,
    has_condition: bool = False,
    condition_result: bool | None = None,
) -> mo.CommitmentRow:
    # `...` means "the Beltline Movers default"; an explicit None means the
    # non-monetary shape, which is a different commitment, not a missing one.
    committed = hero.moving_committed if committed is ... else committed
    return mo.CommitmentRow(
        commitment_id=hero.cm_moving,
        case_id=hero.case_isp,
        status=status,
        currency=currency,
        committed_amount=committed,
        fulfilled_amount=Decimal("0.0000") if fulfilled is None else fulfilled,
        outstanding_amount=committed if outstanding is None else outstanding,
        revision=revision,
        valid_to=valid_to,
        has_condition=has_condition,
        condition_result=condition_result,
    )


# --- section 4.6, the worked example -----------------------------------------


def test_partial_payment_of_200_against_420_leaves_220_outstanding(hero: Any) -> None:
    """Section 4.6, field for field. USD 420 committed, USD 200 paid, USD 220
    outstanding, status ACTIVE -> PARTIAL, commitment revision 2 -> 3."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one,
            amount=hero.moving_paid,
            currency="USD",
            fulfilled_at=hero.tx_now,
        ),
        tx_now=hero.tx_now,
    )
    assert delta.admitted_sum == Decimal("200.0000")
    assert delta.fulfilled_after == Decimal("200.0000")
    assert delta.outstanding_after == Decimal("220.0000")
    assert delta.status_before is CommitmentStatus.ACTIVE
    assert delta.status_after is CommitmentStatus.PARTIAL
    assert delta.revision_after == 3
    assert KernelReasonCode.FULFILLMENT_ADMITTED in delta.reasons
    assert KernelReasonCode.COMMITMENT_PARTIAL_RECOMPUTED in delta.reasons
    assert delta.conflicts == ()


def test_the_admitted_fulfillment_row_keeps_the_full_observed_amount(hero: Any) -> None:
    """The `fulfillments` row is evidence-linked and immutable. Whatever the
    projection ends up capped at, the ledger keeps what was actually seen."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("200.0000"), currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.fulfillment is not None
    assert delta.fulfillment.amount == Decimal("200.0000")
    assert delta.fulfillment.admission_status is FulfillmentAdmissionStatus.ADMITTED


def test_recompute_never_increments(hero: Any) -> None:
    """Section 4.1, the central design decision. `new_fulfilled = old_fulfilled
    + F` double-counts the first duplicate delivery; recomputing from the whole
    ledger cannot. Here the stored `fulfilled_amount` is deliberately *wrong* -
    the recompute must ignore it and get 300 from the rows."""
    commitment = _commitment(hero, fulfilled=Decimal("999.0000"), outstanding=Decimal("0.0000"))
    delta = mo.apply_fulfillment(
        commitment,
        ledger=[
            mo.Fulfillment(amount=Decimal("100.0000"), currency="USD", evidence_id=hero.ev_one)
        ],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_two, amount=Decimal("200.0000"), currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.admitted_sum == Decimal("300.0000")
    assert delta.outstanding_after == Decimal("120.0000")


def test_only_admitted_rows_of_the_right_currency_are_summed(hero: Any) -> None:
    """A `REJECTED_CURRENCY` row is kept as evidence and must never enter the
    arithmetic, or the currency gate would be a formality."""
    ledger = [
        mo.Fulfillment(amount=Decimal("100.0000"), currency="USD", evidence_id=hero.ev_one),
        mo.Fulfillment(
            amount=Decimal("50.0000"),
            currency="EUR",
            admission_status=FulfillmentAdmissionStatus.REJECTED_CURRENCY,
            evidence_id=hero.ev_two,
        ),
        mo.Fulfillment(
            amount=Decimal("25.0000"),
            currency="USD",
            admission_status=FulfillmentAdmissionStatus.CLAIMED_ONLY,
            evidence_id=hero.ev_foreign,
        ),
    ]
    assert mo.admitted_total(ledger, "USD") == Decimal("100.0000")


# --- section 4.2 branch 2, duplicate evidence ---------------------------------


def test_a_duplicate_evidence_id_is_an_exact_no_op(hero: Any) -> None:
    """Section 4.2 branch 2, and the failure the demo audience tries first.
    Three independent defences exist; this is the arithmetic one."""
    commitment = _commitment(
        hero,
        fulfilled=Decimal("200.0000"),
        outstanding=Decimal("220.0000"),
        status=CommitmentStatus.PARTIAL,
    )
    delta = mo.apply_fulfillment(
        commitment,
        ledger=[
            mo.Fulfillment(amount=Decimal("200.0000"), currency="USD", evidence_id=hero.ev_one)
        ],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("200.0000"), currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.fulfillment is None
    assert delta.status_after is CommitmentStatus.PARTIAL
    assert delta.revision_after == commitment.revision
    assert KernelReasonCode.FULFILLMENT_EVIDENCE_DUPLICATE in delta.reasons


def test_a_no_op_is_still_a_decision_with_a_reason(hero: Any) -> None:
    """`kernel_decisions` is written for every outcome including NOOPs, and
    `G4.5` reads `reason_code` from that row. A NULL there is a gate failure."""
    delta = mo.CommitmentDelta.noop(
        _commitment(hero), reasons=[KernelReasonCode.FULFILLMENT_EVIDENCE_DUPLICATE]
    )
    assert delta.reasons != ()
    assert delta.status_before is delta.status_after


# --- section 4.2 branch 1, the currency gate ----------------------------------


def test_currency_mismatch_is_rejected_never_converted(hero: Any) -> None:
    """Section 2.10 item 4 and section 4.6's variant. EUR 200 against a USD
    commitment: the row is written `REJECTED_CURRENCY`, outstanding stays 420,
    the status becomes DISPUTED, and a `FULFILLMENT_CONFLICT` opens for a
    human. The Kernel never invents an exchange rate."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("200.0000"), currency="EUR"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.fulfillment is not None
    assert delta.fulfillment.admission_status is FulfillmentAdmissionStatus.REJECTED_CURRENCY
    assert delta.outstanding_after == hero.moving_committed
    assert delta.status_after is CommitmentStatus.DISPUTED
    assert KernelReasonCode.FULFILLMENT_CURRENCY_REJECTED in delta.reasons
    assert KernelReasonCode.CONFLICT_CURRENCY_MISMATCH in delta.reasons
    (conflict,) = delta.conflicts
    assert conflict.conflict_type is ConflictType.FULFILLMENT_CONFLICT
    assert conflict.status is ConflictStatus.NEEDS_HUMAN
    assert conflict.requires_human is True


def test_a_currency_rejection_still_moves_the_case(hero: Any) -> None:
    """Section 4.6: "the case revision still increments - a conflict row is a
    canonical change". A rejection that left the aggregate untouched would make
    the dispute invisible to every read model."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("200.0000"), currency="EUR"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.conflicts != ()
    assert delta.status_before is not delta.status_after


# --- section 4.3, over-fulfilment ---------------------------------------------


def test_over_fulfilment_disputes_and_preserves_the_full_observed_amount(
    hero: Any,
) -> None:
    """Section 4.3, and required test 11. USD 500 against USD 420: the ledger
    row keeps 500, the projection caps at 420 with outstanding 0, the excess of
    80 is recorded in the conflict's notes, and the status is DISPUTED, **not**
    FULFILLED. That is the difference between capping and silently clamping."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("500.0000"), currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.fulfillment is not None
    assert delta.fulfillment.amount == Decimal("500.0000")
    assert delta.fulfilled_after == Decimal("420.0000")
    assert delta.outstanding_after == Decimal("0.0000")
    assert delta.excess == Decimal("80.0000")
    assert delta.status_after is CommitmentStatus.DISPUTED
    assert KernelReasonCode.COMMITMENT_DISPUTED_EXCESS in delta.reasons
    (conflict,) = delta.conflicts
    assert conflict.reason_code is KernelReasonCode.CONFLICT_OVER_FULFILMENT
    assert conflict.notes is not None
    assert "80" in conflict.notes


def test_the_signed_outstanding_goes_negative_and_is_not_clamped(hero: Any) -> None:
    """`specs/11_CONTRACTS.md` section 5.1 and section 4.3: the identity never
    clamps. Returning zero here would destroy the only evidence that anything
    was wrong; the *projection* is capped separately, and only ever alongside a
    conflict row, a DISPUTED status and an outbox event."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("500.0000"), currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.signed_outstanding == Decimal("-80.0000")
    assert delta.outstanding_after == Decimal("0.0000")


def test_the_identity_is_reached_through_the_domain_module_global(hero: Any) -> None:
    """`provenance_domain.money.outstanding` is the symbol `PV_SABOTAGE`
    neuters. A `from`-import would copy the reference at import time, the
    rebind would land on a symbol nothing reads, and the sabotage probe would
    report green while the identity was gone. This test proves the call site
    resolves through the module object."""
    calls: list[tuple[Decimal, Decimal]] = []
    real = domain_money.outstanding

    def _spy(committed: Decimal, fulfilled: Decimal) -> Decimal:
        calls.append((committed, fulfilled))
        return real(committed, fulfilled)

    domain_money.outstanding = _spy  # type: ignore[assignment]
    try:
        mo.apply_fulfillment(
            _commitment(hero),
            ledger=[],
            new=mo.ProposedFulfillment(
                evidence_id=hero.ev_one, amount=Decimal("200.0000"), currency="USD"
            ),
            tx_now=hero.tx_now,
        )
    finally:
        domain_money.outstanding = real  # type: ignore[assignment]
    assert calls, "money_ops did not reach provenance_domain.money.outstanding"


def test_the_overpay_tolerance_is_configuration(hero: Any) -> None:
    """`overpay_tolerance` defaults to 0.00, so any excess at all is an
    anomaly. A deployment that wanted to absorb rounding would change the
    number, never the branch."""
    tolerant = KernelConfig(overpay_tolerance=Decimal("100.00"))
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("500.0000"), currency="USD"
        ),
        tx_now=hero.tx_now,
        cfg=tolerant,
    )
    assert delta.conflicts == ()
    assert delta.status_after is CommitmentStatus.FULFILLED


# --- section 4.4, the status decision function --------------------------------


def test_exact_settlement_is_fulfilled(hero: Any) -> None:
    """`outstanding == 0 and admitted_sum >= committed`."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=hero.moving_committed, currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.status_after is CommitmentStatus.FULFILLED
    assert delta.outstanding_after == Decimal("0.0000")
    assert KernelReasonCode.COMMITMENT_FULFILLED in delta.reasons


def test_dispute_outranks_fulfilled(hero: Any) -> None:
    """Section 4.4: if the counterparty says "paid in full" and a live
    fulfillment conflict says otherwise, the commitment is disputed, not
    fulfilled."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=hero.moving_committed, currency="USD"
        ),
        open_conflicts=[(ConflictType.FULFILLMENT_CONFLICT, ConflictStatus.NEEDS_HUMAN)],
        tx_now=hero.tx_now,
    )
    assert delta.status_after is CommitmentStatus.DISPUTED


def test_a_resolved_conflict_no_longer_blocks_fulfilment(hero: Any) -> None:
    """Only `OPEN` and `NEEDS_HUMAN` are live. A settled dispute that kept
    blocking would leave the commitment DISPUTED forever."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=hero.moving_committed, currency="USD"
        ),
        open_conflicts=[(ConflictType.FULFILLMENT_CONFLICT, ConflictStatus.RESOLVED)],
        tx_now=hero.tx_now,
    )
    assert delta.status_after is CommitmentStatus.FULFILLED


def test_a_past_due_at_does_not_expire_a_commitment(hero: Any) -> None:
    """Section 4.4, stated because a test asserts it. A missed deadline makes
    the case *actionable* through a trigger; it does not extinguish the
    obligation. Only `valid_to` expires it. The Harborview deposit is 95 days
    overdue and still owed - that is the whole second reveal."""
    overdue = mo.CommitmentRow(
        commitment_id=hero.cm_deposit,
        case_id=hero.case_isp,
        status=CommitmentStatus.ACTIVE,
        currency="USD",
        committed_amount=hero.deposit_amount,
        fulfilled_amount=Decimal("0.0000"),
        outstanding_amount=hero.deposit_amount,
        due_at=hero.jun_1,
    )
    delta = mo.recompute(
        committed_amount=overdue.committed_amount,
        currency="USD",
        admitted=[],
        tx_now=hero.tx_now,
        commitment_id=overdue.commitment_id,
        due_at=overdue.due_at,
    )
    assert delta.status_after is CommitmentStatus.ACTIVE
    assert delta.outstanding_after == hero.deposit_amount


def test_a_passed_valid_to_expires_a_commitment(hero: Any) -> None:
    """The complement. `valid_to` is the only thing that extinguishes an
    obligation, so it must actually do so."""
    delta = mo.recompute(
        committed_amount=hero.deposit_amount,
        currency="USD",
        admitted=[],
        tx_now=hero.tx_now,
        commitment_id=hero.cm_deposit,
        valid_to=hero.jun_1,
    )
    assert delta.status_after is CommitmentStatus.EXPIRED


def test_a_conditional_promise_stays_proposed_until_the_condition_is_true(
    hero: Any,
) -> None:
    """Section 4.4: `condition_ast` is an activation condition, not a
    fulfillment test. FALSE or UNKNOWN keeps the commitment PROPOSED; UNKNOWN
    never arms an overdue trigger."""
    for result in (False, None):
        delta = mo.apply_fulfillment(
            _commitment(
                hero,
                status=CommitmentStatus.PROPOSED,
                has_condition=True,
                condition_result=result,
            ),
            ledger=[],
            new=mo.ProposedFulfillment(
                evidence_id=hero.ev_one, amount=Decimal("10.0000"), currency="USD"
            ),
            tx_now=hero.tx_now,
        )
        assert delta.status_after is CommitmentStatus.PROPOSED, result


def test_a_superseded_commitment_is_terminal(hero: Any) -> None:
    """First branch of section 4.4. Nothing reopens a superseded obligation;
    changing it requires superseding evidence and a new commitment version."""
    delta = mo.apply_fulfillment(
        _commitment(hero, status=CommitmentStatus.SUPERSEDED),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("10.0000"), currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.status_after is CommitmentStatus.SUPERSEDED


def test_a_non_monetary_commitment_is_fulfilled_by_one_admitted_row(hero: Any) -> None:
    """`committed_amount IS NULL` is the non-monetary shape. Section 4.4: it
    becomes FULFILLED when at least one qualifying fulfillment is admitted."""
    delta = mo.apply_fulfillment(
        _commitment(hero, committed=None, currency=None),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, quantity=Decimal("1.0000"), fulfilled_at=hero.tx_now
        ),
        tx_now=hero.tx_now,
    )
    assert delta.status_after is CommitmentStatus.FULFILLED
    assert delta.outstanding_after is None


# --- the CHECK constraints, duplicated in Python on purpose --------------------


def test_fulfilled_never_exceeds_committed_in_the_projection(hero: Any) -> None:
    """`ck_commitments_fulfilled_le_committed` (M3). Asserting it here as well
    is intentional: the CHECK cannot tell you *which* code path was wrong."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=Decimal("10000.0000"), currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.fulfilled_after is not None
    assert delta.fulfilled_after <= hero.moving_committed


def test_the_outstanding_identity_holds_on_the_projection(hero: Any) -> None:
    """`ck_commitments_outstanding_identity` (M4): `outstanding = committed -
    fulfilled`. This is what makes "$420 promised, $200 paid, $220
    outstanding" impossible to get wrong."""
    delta = mo.apply_fulfillment(
        _commitment(hero),
        ledger=[],
        new=mo.ProposedFulfillment(
            evidence_id=hero.ev_one, amount=hero.moving_paid, currency="USD"
        ),
        tx_now=hero.tx_now,
    )
    assert delta.outstanding_after == hero.moving_committed - delta.fulfilled_after


money_amount = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("100000"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)


#: Hypothesis and the repository's autouse `unit` guard are both function
#: scoped, which is exactly the shape `function_scoped_fixture` warns about.
#: The guard is idempotent per example, so the health check is suppressed
#: rather than the guard removed.
PROPERTY = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

TX_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)


@PROPERTY
@given(committed=money_amount, payments=st.lists(money_amount, max_size=8))
def test_no_ledger_ever_yields_fulfilled_with_outstanding(
    committed: Decimal, payments: list[Decimal]
) -> None:
    """Required test 5, as a property rather than an example
    (`quality/20_TDD_STRATEGY.md` section 5.2). The database `CHECK` enforces
    the same thing; asserting it in both places is deliberate, because the
    CHECK cannot say which code path was wrong."""
    delta = mo.recompute(
        committed_amount=committed,
        currency="USD",
        admitted=[mo.Fulfillment(amount=p, currency="USD") for p in payments],
        open_conflicts=[],
        tx_now=TX_NOW,
        commitment_id=uuid.UUID(int=0x3002),
    )
    assert delta.outstanding_after is not None
    assert delta.outstanding_after >= Decimal("0")
    paid = sum(payments, Decimal(0))
    assert delta.outstanding_after == max(Decimal("0"), committed - min(paid, committed))
    if delta.status_after is CommitmentStatus.FULFILLED:
        assert delta.outstanding_after == Decimal("0")


@PROPERTY
@given(committed=money_amount, payments=st.lists(money_amount, max_size=8))
def test_the_signed_identity_is_exactly_committed_minus_admitted(
    committed: Decimal, payments: list[Decimal]
) -> None:
    """The unclamped half of the same property. `signed_outstanding` is the
    honest number and may be negative; `outstanding_after` is what the schema
    can hold."""
    delta = mo.recompute(
        committed_amount=committed,
        currency="USD",
        admitted=[mo.Fulfillment(amount=p, currency="USD") for p in payments],
        tx_now=TX_NOW,
        commitment_id=uuid.UUID(int=0x3002),
    )
    assert delta.signed_outstanding == committed - sum(payments, Decimal(0))


def test_money_never_arrives_as_a_float(hero: Any) -> None:
    """A float amount must be refused rather than quantised into plausibility.
    `tools/contract_lint.py`'s no-float-money rule bans it statically; this is
    the runtime half."""
    with pytest.raises((TypeError, ValueError, domain_money.MoneyError)):
        mo.apply_fulfillment(
            _commitment(hero),
            ledger=[],
            new=mo.ProposedFulfillment(
                evidence_id=hero.ev_one,
                amount=200.0,  # type: ignore[arg-type]
                currency="USD",
            ),
            tx_now=hero.tx_now,
        )


def test_a_conflict_write_never_claims_auto_resolution_while_requiring_a_human(
    hero: Any,
) -> None:
    """`ck_conflicts_requires_human_consistent`. Both conflicts this module can
    emit are `NEEDS_HUMAN`, so both must satisfy it."""
    for new in (
        mo.ProposedFulfillment(evidence_id=hero.ev_one, amount=Decimal("500.0000"), currency="USD"),
        mo.ProposedFulfillment(evidence_id=hero.ev_two, amount=Decimal("200.0000"), currency="EUR"),
    ):
        delta = mo.apply_fulfillment(_commitment(hero), ledger=[], new=new, tx_now=hero.tx_now)
        for conflict in delta.conflicts:
            assert conflict.status is ConflictStatus.NEEDS_HUMAN
            assert conflict.requires_human is True
