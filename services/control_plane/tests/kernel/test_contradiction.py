"""T4.4 - the overlap test and the M1-M13 matcher decision table.

`specs/12_KERNEL_ALGORITHMS.md` sections 2.4 to 2.8 and 9.5. Detection produces
candidates; it never resolves. A test here that asserted a disposition would be
asserting T4.5's job and would let a monetary threshold leak into a
non-monetary conflict, which is the specific confusion T4.4's sub-tasks name.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from provenance_domain.enums import (
    ClaimKind,
    CommitmentStatus,
    ConflictSeverity,
    ConflictType,
    EpistemicStatus,
    SubjectType,
)
from services.control_plane.app.memory_kernel import contradiction as cx
from services.control_plane.app.memory_kernel import families as fam
from services.control_plane.app.memory_kernel import propositions as prop
from services.control_plane.app.memory_kernel.config import KernelConfig

pytestmark = pytest.mark.unit

Make = Callable[..., prop.Proposition]


def _ctx(**kwargs: Any) -> cx.MatchContext:
    return cx.MatchContext(**kwargs)


# --- section 2.4, bounds and the overlap test --------------------------------


def test_the_hero_periods_overlap_by_thirty_days(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """The June invoice period and the open-ended TERMINATED interval overlap
    by 30 days. If the matcher calls this "not comparable" there is no hero
    conflict and no product."""
    assert cx.material_overlap(incumbent_terminated, entailed_active) == timedelta(days=30)


def test_abutting_intervals_do_not_overlap(hero: Any, make_proposition: Make) -> None:
    """Section 8.7 L-3. `[May 1 04:00Z, Jun 1 04:00Z)` and `[Jun 1 04:00Z, inf)`
    abut exactly, and `[a, b)` half-open semantics mean abutting is not
    overlapping. Treat the interval as closed and a letter that agrees with
    everything reopens the case."""
    may = make_proposition(valid_from=hero.may_1, valid_to=hero.jun_1)
    june_on = make_proposition(valid_from=hero.jun_1, valid_to=None)
    assert cx.material_overlap(may, june_on) is None


def test_brushing_overlap_below_twenty_four_hours_is_not_material(
    hero: Any, make_proposition: Make
) -> None:
    """Two service periods that touch for four hours across a timezone
    inference error are a parsing artifact, not a contradiction."""
    a = make_proposition(valid_from=hero.may_1, valid_to=hero.jun_1 + timedelta(hours=4))
    b = make_proposition(valid_from=hero.jun_1, valid_to=hero.jul_1)
    assert cx.material_overlap(a, b) is None


def test_an_overlap_of_exactly_twenty_four_hours_is_material(
    hero: Any, make_proposition: Make
) -> None:
    """`>=`, not `>`. The threshold is inclusive, which is the difference
    between dismissing a whole day and keeping it."""
    a = make_proposition(valid_from=hero.may_1, valid_to=hero.jun_1 + timedelta(hours=24))
    b = make_proposition(valid_from=hero.jun_1, valid_to=hero.jul_1)
    assert cx.material_overlap(a, b) == timedelta(hours=24)


def test_full_containment_always_counts_regardless_of_duration(
    hero: Any, make_proposition: Make
) -> None:
    """A one-hour period wholly inside a one-year period is never dismissed. A
    naive `>= 86400` check would throw it away."""
    outer = make_proposition(valid_from=hero.may_1, valid_to=hero.may_1 + timedelta(days=365))
    inner = make_proposition(valid_from=hero.jun_1, valid_to=hero.jun_1 + timedelta(hours=1))
    assert cx.material_overlap(outer, inner) == timedelta(hours=1)


def test_unknown_validity_is_never_comparable(hero: Any, make_proposition: Make) -> None:
    """Rule T2, the highest-value rule in section 8. "We processed your refund"
    with no date is not evidence about any particular window, and treating it
    as `[-inf, +inf)` would make it conflict with everything."""
    dated = make_proposition()
    dateless = make_proposition(
        valid_from=None, valid_to=None, validity_basis=prop.ValidityBasis.UNKNOWN
    )
    assert cx.material_overlap(dated, dateless) is None
    assert cx.material_overlap(dateless, dated) is None


def test_an_open_lower_bound_widens_to_negative_infinity(hero: Any, make_proposition: Make) -> None:
    """`valid_from = NULL` means "since forever as far as we know", not "now"."""
    lo, hi = cx.bounds(make_proposition(valid_from=None, valid_to=hero.jun_1))
    assert lo == cx.NEG_INF
    assert hi == hero.jun_1


def test_a_payment_instant_widens_by_the_payment_match_window(
    hero: Any, make_proposition: Make
) -> None:
    """Section 2.4: a `PAYMENT` point event widens to `[t - 3d, t + 3d)` for
    identity matching. Bank settlement dates and remittance dates disagree by
    days, routinely."""
    paid_at = datetime(2026, 6, 11, tzinfo=UTC)
    payment = make_proposition(
        family=fam.Family.PAYMENT,
        predicate="payment_received",
        value=fam.PaymentValue("USD", Decimal("200.0000"), paid_at),
        valid_from=paid_at,
        valid_to=paid_at,
    )
    lo, hi = cx.bounds(payment)
    assert (hi - lo) == timedelta(days=6)


def test_a_non_payment_instant_widens_by_one_day(hero: Any, make_proposition: Make) -> None:
    """Every other family widens by `instant_widen_days`, not by the payment
    window. Using the payment window everywhere would merge a month of
    unrelated service assertions."""
    lo, hi = cx.bounds(make_proposition(valid_from=hero.jun_1, valid_to=hero.jun_1))
    assert (hi - lo) == timedelta(days=1)


# --- section 2.5, amount comparison ------------------------------------------


@pytest.mark.parametrize(
    ("x", "y", "differ"),
    [
        (Decimal("186.00"), Decimal("186.00"), False),
        (Decimal("1800.00"), Decimal("1791.00"), False),  # delta 9.00, tol 9.00
        (Decimal("1800.00"), Decimal("1780.00"), True),  # delta 20.00 > 9.00
        (Decimal("1.00"), Decimal("1.01"), False),  # absolute floor
        (Decimal("1.00"), Decimal("1.02"), True),
    ],
)
def test_amount_tolerance_is_the_max_of_absolute_and_relative(
    x: Decimal, y: Decimal, differ: bool
) -> None:
    """Section 2.5, worked example for worked example. Tolerance exists because
    rounding and fee lines produce sub-percent noise that is not a dispute, and
    calling `1800.00` against `1791.00` a dispute trains users to click
    through."""
    assert cx.amounts_differ(x, y) is differ


def test_amount_comparison_is_symmetric() -> None:
    """The relative tolerance is measured against the larger operand, so a
    naive implementation is asymmetric and the verdict depends on which side
    the matcher happened to call `left`."""
    assert cx.amounts_differ(Decimal("1800.00"), Decimal("1791.00")) is cx.amounts_differ(
        Decimal("1791.00"), Decimal("1800.00")
    )


# --- section 2.6, the matcher table ------------------------------------------


def test_m1_opposed_service_states_over_an_overlap_are_a_value_conflict(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """M1, and the load-bearing half of section 8.7's reversed hero."""
    finding = cx.match(incumbent_terminated, entailed_active, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.VALUE_CONFLICT
    assert finding.family is fam.Family.SERVICE_STATUS
    assert finding.matcher_rule == "M1"
    assert "CONFLICT_VALUE_MUTUAL_EXCLUSION" in [c.value for c in finding.reason_codes]


def test_m2_same_state_with_starts_more_than_a_day_apart_is_temporal(
    hero: Any, make_proposition: Make
) -> None:
    """M2. Two sources agree the service ended and disagree about when. That is
    a date dispute, not a value dispute, and typing it as `VALUE_CONFLICT`
    would make the State Proof narrate the wrong disagreement."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        valid_from=hero.jun_1,
        valid_to=None,
        validity_basis=prop.ValidityBasis.EXPLICIT_OPEN,
        base_authority=Decimal("0.8800"),
        is_incumbent=True,
    )
    right = make_proposition(
        prop_id=hero.cl_terminated,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        valid_from=hero.jun_1 + timedelta(days=10),
        valid_to=None,
        validity_basis=prop.ValidityBasis.EXPLICIT_OPEN,
        base_authority=Decimal("0.5500"),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.TEMPORAL_CONFLICT
    assert finding.matcher_rule == "M2"


def test_m2_does_not_fire_when_the_starts_are_within_a_day(
    hero: Any, make_proposition: Make
) -> None:
    """A twelve-hour difference between two termination dates is a timezone
    artifact, not a disagreement."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        valid_from=hero.jun_1,
        valid_to=None,
        validity_basis=prop.ValidityBasis.EXPLICIT_OPEN,
        is_incumbent=True,
    )
    right = make_proposition(
        prop_id=hero.cl_terminated,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        valid_from=hero.jun_1 + timedelta(hours=12),
        valid_to=None,
        validity_basis=prop.ValidityBasis.EXPLICIT_OPEN,
        base_authority=Decimal("0.5500"),
    )
    assert cx.match(left, right, _ctx()) is None


def test_m3_differing_balances_in_one_currency_are_a_value_conflict(
    hero: Any, make_proposition: Make
) -> None:
    """M3. The hero's own family: `BALANCE`."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("0.0000")),
        is_incumbent=True,
    )
    right = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", hero.invoice_amount),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.VALUE_CONFLICT
    assert finding.family is fam.Family.BALANCE
    assert finding.matcher_rule == "M3"


def test_m4_a_currency_mismatch_is_a_conflict_never_a_conversion(
    hero: Any, make_proposition: Make
) -> None:
    """M4 and section 2.10 item 4: the Kernel never converts. Inventing an FX
    rate inside a memory system is how a records product starts being wrong
    about money."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", hero.invoice_amount),
        is_incumbent=True,
    )
    right = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("EUR", hero.invoice_amount),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.matcher_rule == "M4"
    assert "CONFLICT_CURRENCY_MISMATCH" in [c.value for c in finding.reason_codes]


def test_m5_one_side_denying_a_payment_the_other_asserts_is_a_fulfillment_conflict(
    hero: Any, make_proposition: Make
) -> None:
    """M5. Both sides carry the same external reference, so the identity is not
    in doubt: only whether the money moved."""
    paid_at = datetime(2026, 6, 11, tzinfo=UTC)
    asserted = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.PAYMENT,
        predicate="payment_received",
        value=fam.PaymentValue("USD", Decimal("200.0000"), paid_at, "TRN-9931", True),
        valid_from=paid_at,
        valid_to=paid_at,
        is_incumbent=True,
    )
    denied = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.PAYMENT,
        predicate="payment_not_received",
        value=fam.PaymentValue("USD", Decimal("200.0000"), paid_at, "TRN-9931", False),
        valid_from=paid_at,
        valid_to=paid_at,
    )
    finding = cx.match(asserted, denied, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.FULFILLMENT_CONFLICT
    assert finding.matcher_rule == "M5"
    assert "CONFLICT_PAYMENT_DENIAL" in [c.value for c in finding.reason_codes]


def test_payment_identity_prefers_the_external_reference(hero: Any, make_proposition: Make) -> None:
    """Section 2.7. The amount-and-window fallback deliberately over-merges,
    so an available reference must always win."""
    paid_at = datetime(2026, 6, 11, tzinfo=UTC)
    referenced = make_proposition(
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("200.0000"), paid_at, " trn-9931 ", True),
    )
    other = make_proposition(
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("999.0000"), paid_at, "TRN-9931", True),
    )
    assert cx.payment_key(referenced) == cx.payment_key(other)


def test_payments_without_a_reference_fall_back_to_amount_and_window(
    hero: Any, make_proposition: Make
) -> None:
    """The fallback merges two genuinely distinct same-amount payments three
    days apart. That produces a false `FULFILLMENT_CONFLICT` routed to a human,
    which is the safe failure direction: a false conflict costs one click; a
    missed conflict costs money."""
    a = make_proposition(
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("200.0000"), datetime(2026, 6, 10, tzinfo=UTC)),
    )
    b = make_proposition(
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("200.0000"), datetime(2026, 6, 11, tzinfo=UTC)),
    )
    far = make_proposition(
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("200.0000"), datetime(2026, 9, 12, tzinfo=UTC)),
    )
    assert cx.payment_key(a) == cx.payment_key(b)
    assert cx.payment_key(a) != cx.payment_key(far)


def test_the_payment_window_is_a_fixed_grid_not_a_sliding_window(
    hero: Any, make_proposition: Make
) -> None:
    """Section 2.7 buckets by `(paid_at - EPOCH) // 3 days`, so the window is a
    fixed grid: two payments one day apart can land either side of a boundary
    and fail to merge, while two payments three days apart inside one bucket
    do merge. Asserted rather than papered over, because the failure direction
    is a false conflict routed to a human - noisy, but safe - and a reader of
    this suite should know the mechanism is coarse by design (risk R5)."""
    same_bucket = make_proposition(
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("200.0000"), datetime(2026, 6, 11, tzinfo=UTC)),
    )
    next_bucket = make_proposition(
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("200.0000"), datetime(2026, 6, 12, tzinfo=UTC)),
    )
    assert cx.payment_key(same_bucket) != cx.payment_key(next_bucket)


def test_m6_the_same_payment_at_two_amounts_is_a_fulfillment_conflict(
    hero: Any, make_proposition: Make
) -> None:
    """M6. Same reference, same currency, materially different amounts."""
    paid_at = datetime(2026, 6, 11, tzinfo=UTC)
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("200.0000"), paid_at, "TRN-9931", True),
        is_incumbent=True,
    )
    right = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("120.0000"), paid_at, "TRN-9931", True),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.FULFILLMENT_CONFLICT
    assert finding.matcher_rule == "M6"


def test_m7_the_same_reference_in_two_currencies_is_a_currency_mismatch(
    hero: Any, make_proposition: Make
) -> None:
    """M7. One transaction cannot have been made in two currencies, and the
    Kernel will not pick one."""
    paid_at = datetime(2026, 6, 11, tzinfo=UTC)
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("USD", Decimal("200.0000"), paid_at, "TRN-9931", True),
        is_incumbent=True,
    )
    right = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.PAYMENT,
        value=fam.PaymentValue("EUR", Decimal("200.0000"), paid_at, "TRN-9931", True),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.matcher_rule == "M7"
    assert "CONFLICT_CURRENCY_MISMATCH" in [c.value for c in finding.reason_codes]


def test_m8_two_outstanding_amounts_for_one_commitment_conflict(
    hero: Any, make_proposition: Make
) -> None:
    """M8. The ledger says one thing about the deposit; the counterparty says
    another."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.OUTSTANDING,
        predicate="deposit_outstanding",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("USD", hero.deposit_amount, hero.cm_deposit),
        is_incumbent=True,
    )
    right = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.OUTSTANDING,
        predicate="deposit_outstanding",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("USD", Decimal("900.0000"), hero.cm_deposit),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.VALUE_CONFLICT
    assert finding.matcher_rule == "M8"


def test_m9_settled_against_a_live_ledger_is_a_fulfillment_conflict(
    hero: Any, make_proposition: Make
) -> None:
    """M9. "Your deposit was fully returned" against a ledger that says
    otherwise. The typing matters: this is a fulfillment dispute, and routing
    it as a plain value conflict would let it auto-resolve on authority."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.OUTSTANDING,
        predicate="deposit_outstanding",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("USD", hero.deposit_amount, hero.cm_deposit),
        is_incumbent=True,
    )
    right = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.OUTSTANDING,
        predicate="deposit_outstanding",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("USD", Decimal("0.0000"), hero.cm_deposit),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.FULFILLMENT_CONFLICT
    assert finding.matcher_rule == "M9"


def test_m10_outstanding_in_two_currencies_is_a_currency_mismatch(
    hero: Any, make_proposition: Make
) -> None:
    """M10, completing the trio with M4 and M7. Currency is never converted in
    any family."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.OUTSTANDING,
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("USD", hero.deposit_amount, hero.cm_deposit),
        is_incumbent=True,
    )
    right = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.OUTSTANDING,
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("GBP", hero.deposit_amount, hero.cm_deposit),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.matcher_rule == "M10"
    assert "CONFLICT_CURRENCY_MISMATCH" in [c.value for c in finding.reason_codes]


def test_m11_withdrawing_a_live_commitment_is_its_own_conflict_type(
    hero: Any, make_proposition: Make
) -> None:
    """M11. The obligor is retracting their own promise. Authority is symmetric
    by definition, so no margin test can decide it and the type exists to make
    that unmistakable."""
    withdrawal = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.COMMITMENT_STATUS,
        predicate="commitment_withdrawn",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.CommitmentStatusValue(True, hero.cm_deposit),
    )
    incumbent = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.COMMITMENT_STATUS,
        predicate="commitment_withdrawn",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.CommitmentStatusValue(False, hero.cm_deposit),
        is_incumbent=True,
    )
    finding = cx.match(
        incumbent,
        withdrawal,
        _ctx(
            commitment_statuses={hero.cm_deposit: CommitmentStatus.ACTIVE},
            commitment_validity={hero.cm_deposit: (hero.may_1, None)},
        ),
    )
    assert finding is not None
    assert finding.conflict_type is ConflictType.COMMITMENT_WITHDRAWAL_CONFLICT
    assert finding.matcher_rule == "M11"
    assert "CONFLICT_COMMITMENT_WITHDRAWAL" in [c.value for c in finding.reason_codes]


def test_m11_does_not_fire_against_an_already_fulfilled_commitment(
    hero: Any, make_proposition: Make
) -> None:
    """M11 requires the commitment to be `ACTIVE` or `PARTIAL`. Withdrawing a
    promise that was already kept changes nothing about the ledger."""
    withdrawal = make_proposition(
        base_authority=Decimal("0.5500"),
        family=fam.Family.COMMITMENT_STATUS,
        predicate="commitment_withdrawn",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.CommitmentStatusValue(True, hero.cm_deposit),
    )
    incumbent = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.COMMITMENT_STATUS,
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.CommitmentStatusValue(False, hero.cm_deposit),
        is_incumbent=True,
    )
    finding = cx.match(
        incumbent,
        withdrawal,
        _ctx(
            commitment_statuses={hero.cm_deposit: CommitmentStatus.FULFILLED},
            commitment_validity={hero.cm_deposit: (hero.may_1, None)},
        ),
    )
    assert finding is None or finding.matcher_rule != "M11"


def test_m13_upgrades_two_credible_sources_to_an_authority_conflict(
    hero: Any, make_proposition: Make
) -> None:
    """M13, and the only producer of `AUTHORITY_CONFLICT`. Two
    `PROVIDER_SYSTEM_NOTICE` direct claims at 0.88 disagree with no
    deterministic winner, so the type says so rather than a margin quietly
    picking one."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        base_authority=Decimal("0.8800"),
        is_incumbent=True,
    )
    right = make_proposition(
        prop_id=hero.cl_terminated,
        value=fam.ServiceStatusValue(fam.ServiceState.ACTIVE),
        base_authority=Decimal("0.8800"),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.AUTHORITY_CONFLICT
    assert "CONFLICT_AUTHORITY_TIE" in [c.value for c in finding.reason_codes]


def test_m13_does_not_upgrade_when_the_entailment_penalty_separates_them(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """0.88 against 0.58 is a 0.30 margin, comfortably outside 0.25, so the
    hero's service-status collision stays a `VALUE_CONFLICT`. If M13 upgraded
    it, section 1.6's worked `AUTO_RESOLVED` outcome would become a human
    review and the demo would stall."""
    finding = cx.match(incumbent_terminated, entailed_active, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.VALUE_CONFLICT


def test_m13_does_not_upgrade_two_weak_sources(hero: Any, make_proposition: Make) -> None:
    """Both sides must be at or above the high-authority floor. Two 0.35
    marketing pages disagreeing is not a credible tie; it is noise."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        base_authority=Decimal("0.3500"),
        is_incumbent=True,
    )
    right = make_proposition(
        prop_id=hero.cl_terminated,
        value=fam.ServiceStatusValue(fam.ServiceState.ACTIVE),
        base_authority=Decimal("0.3500"),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.conflict_type is ConflictType.VALUE_CONFLICT


# --- the guards every rule shares --------------------------------------------


def test_a_proposition_compared_with_itself_yields_no_candidate(
    incumbent_terminated: prop.Proposition,
) -> None:
    """T4.4 acceptance. Section 2.10 item 10: supersession is lineage, not
    contradiction, and a belief cannot contradict itself."""
    assert cx.match(incumbent_terminated, incumbent_terminated, _ctx()) is None


def test_two_families_never_produce_a_candidate(hero: Any, make_proposition: Make) -> None:
    """T4.3 acceptance, asserted where it bites. The source-class ordering
    inverts between families, so a cross-family comparison is a category error
    rather than a close call."""
    balance = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", hero.invoice_amount),
        is_incumbent=True,
    )
    service = make_proposition(prop_id=hero.cl_terminated)
    assert cx.match(balance, service, _ctx()) is None


def test_different_subjects_never_produce_a_candidate(hero: Any, make_proposition: Make) -> None:
    """Section 2.10 item 8: the matcher scope is one case aggregate, and two
    relationships are two subjects even on one counterparty. Northline Fiber's
    two accounts are the sharpest decoy in the corpus for exactly this."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        subject_id=hero.rel_isp,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        is_incumbent=True,
    )
    right = make_proposition(prop_id=hero.cl_terminated, subject_id=hero.rel_other)
    assert cx.match(left, right, _ctx()) is None


def test_retracted_evidence_is_excluded_from_matching(
    hero: Any, incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """Section 2.8. Retracted and superseded evidence keeps its embedding, so a
    matcher that ignores `retraction_status` re-litigates settled facts every
    time retrieval surfaces the corrected document."""
    ctx = _ctx(retracted_prop_ids=frozenset({entailed_active.prop_id}))
    assert cx.match(incumbent_terminated, entailed_active, ctx) is None


def test_unknown_validity_produces_no_conflict_row_in_any_family(
    hero: Any, make_proposition: Make
) -> None:
    """Rule T2 again, this time through the matcher rather than through
    `material_overlap`, because a rule enforced in only one of the two places
    is a rule with a bypass."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("0.0000")),
        is_incumbent=True,
    )
    right = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", hero.invoice_amount),
        valid_from=None,
        valid_to=None,
        validity_basis=prop.ValidityBasis.UNKNOWN,
    )
    assert cx.match(left, right, _ctx()) is None


def test_an_unmapped_family_never_produces_a_conflict(hero: Any, make_proposition: Make) -> None:
    """Risk R1, accepted deliberately: the honest failure mode is silence, not
    invented conflicts."""
    left = make_proposition(family=fam.Family.UNMAPPED, is_incumbent=True)
    right = make_proposition(prop_id=hero.cl_terminated, family=fam.Family.UNMAPPED)
    assert cx.match(left, right, _ctx()) is None


# --- the row the finding becomes ---------------------------------------------


def test_the_finding_orders_its_sides_by_uuid_for_the_check_constraint(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """`specs/10_DATABASE_DDL.md` section 7.1 `ck_conflicts_side_order`
    requires `left_source_id <= right_source_id`, and the partial unique index
    on live identity depends on it. Normalising here is what stops the same
    contradiction being raised twice under two argument orders."""
    finding = cx.match(incumbent_terminated, entailed_active, _ctx())
    assert finding is not None
    assert finding.left_source_id <= finding.right_source_id
    assert finding.left_source_kind in cx.CONFLICT_SIDE_KINDS
    assert finding.right_source_kind in cx.CONFLICT_SIDE_KINDS


def test_side_ordering_does_not_disturb_the_epistemic_roles(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """The incumbent stays the incumbent whichever way the UUIDs sort. Losing
    that would silently invert every authority margin."""
    finding = cx.match(incumbent_terminated, entailed_active, _ctx())
    assert finding is not None
    assert finding.incumbent is incumbent_terminated
    assert finding.challenger is entailed_active


def test_monetary_exposure_is_the_difference_between_the_two_amounts(
    hero: Any, make_proposition: Make
) -> None:
    """Section 3.3's definition, on the hero's own numbers: nothing recorded
    against 186.00 is an exposure of 186.00."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("0.0000")),
        is_incumbent=True,
    )
    right = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", hero.invoice_amount),
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.monetary_exposure == Decimal("186.0000")


def test_a_non_monetary_conflict_borrows_the_commits_largest_amount(
    hero: Any, make_proposition: Make
) -> None:
    """Section 3.3: for a non-monetary family the exposure is the largest
    absolute amount among monetary claims admitted in the same commit against
    the same case. A service-status disagreement arriving with a 186.00 invoice
    is not a free decision."""
    ctx = _ctx(monetary_amounts_in_commit=(Decimal("12.0000"), hero.invoice_amount))
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        is_incumbent=True,
    )
    right = make_proposition(prop_id=hero.cl_terminated)
    finding = cx.match(left, right, ctx)
    assert finding is not None
    assert finding.monetary_exposure == Decimal("186.0000")


@pytest.mark.parametrize(
    ("exposure", "authority", "expected"),
    [
        (Decimal("1000.00"), Decimal("0.7000"), ConflictSeverity.CRITICAL),
        (Decimal("186.00"), Decimal("0.7000"), ConflictSeverity.HIGH),
        (Decimal("50.00"), Decimal("0.7000"), ConflictSeverity.MEDIUM),
        (Decimal("5.00"), Decimal("0.4500"), ConflictSeverity.LOW),
    ],
)
def test_severity_follows_the_first_matching_rung(
    hero: Any,
    make_proposition: Make,
    exposure: Decimal,
    authority: Decimal,
    expected: ConflictSeverity,
) -> None:
    """Section 9.5, first match wins, all four rungs. Severity gates the reopen
    test's Q3a and the UI sort order; it never gates auto-resolution, and
    keeping the two independent is what stops a severity heuristic from
    quietly becoming the resolution policy."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("0.0000")),
        base_authority=authority,
        is_incumbent=True,
    )
    right = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", exposure),
        base_authority=authority,
    )
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.severity is expected


def test_a_confirmed_incumbent_makes_the_conflict_at_least_high(
    hero: Any, make_proposition: Make
) -> None:
    """Section 9.5. Contradicting something the system called CONFIRMED is
    never a low-severity event, whatever the amount."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        base_authority=Decimal("0.4500"),
        epistemic_status=EpistemicStatus.CONFIRMED,
        is_incumbent=True,
    )
    right = make_proposition(prop_id=hero.cl_terminated, base_authority=Decimal("0.4500"))
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert finding.severity is ConflictSeverity.HIGH


def test_a_conflict_blocking_an_approved_action_is_critical(
    hero: Any, make_proposition: Make
) -> None:
    """Invariant 4. A pending external side effect must not have its basis
    rewritten while it waits, so the severity says CRITICAL before the
    disposition says human."""
    ctx = _ctx(action_blocked_subject_ids=frozenset({hero.rel_isp}))
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        is_incumbent=True,
    )
    right = make_proposition(prop_id=hero.cl_terminated)
    finding = cx.match(left, right, ctx)
    assert finding is not None
    assert finding.severity is ConflictSeverity.CRITICAL
    assert finding.blocks_approved_action is True


def test_detect_runs_the_full_cross_product_without_self_pairs(
    hero: Any,
    incumbent_terminated: prop.Proposition,
    entailed_active: prop.Proposition,
    make_proposition: Make,
) -> None:
    """Section 2.6: incumbents x challengers, no pruning. Snapshots are bounded
    to one case, so the cross-product is single-digit by single-digit."""
    harmless = make_proposition(
        prop_id=hero.cl_other,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
    )
    findings = cx.detect([incumbent_terminated], [entailed_active, harmless], _ctx())
    assert len(findings) == 1
    assert findings[0].challenger is entailed_active


def test_detect_returns_an_empty_tuple_when_nothing_contradicts(
    incumbent_terminated: prop.Proposition,
) -> None:
    """The commonest outcome by far, and it must be an empty tuple rather than
    `None`, so callers cannot forget to handle it."""
    assert cx.detect([incumbent_terminated], [], _ctx()) == ()


def test_a_looser_material_overlap_threshold_changes_the_verdict(
    hero: Any, make_proposition: Make
) -> None:
    """The threshold is configuration, not a literal. If a bespoke config does
    not change the answer, the number in `KernelConfig` is decoration."""
    a = make_proposition(valid_from=hero.may_1, valid_to=hero.jun_1 + timedelta(hours=4))
    b = make_proposition(valid_from=hero.jun_1, valid_to=hero.jul_1)
    loose = KernelConfig(material_overlap_min_seconds=3600)
    assert cx.material_overlap(a, b) is None
    assert cx.material_overlap(a, b, loose) == timedelta(hours=4)


def test_the_matcher_table_has_exactly_thirteen_rules() -> None:
    """Section 2.6: "This is the complete v1 rule set." A fourteenth rule is a
    documented table row, never an inline special case."""
    assert tuple(f"M{n}" for n in range(1, 14)) == cx.MATCHER_RULES


def test_conflict_side_kinds_are_the_ddl_set_not_the_support_edge_set() -> None:
    """`ck_conflicts_source_kinds` admits `COMMITMENT` and refuses
    `DERIVATION`; `SupportSourceKind` is the mirror image. Reusing one for the
    other writes a row the database rejects at the last possible moment."""
    assert {"EVIDENCE", "CLAIM", "BELIEF_VERSION", "COMMITMENT"} == cx.CONFLICT_SIDE_KINDS


def test_a_user_correction_is_still_only_a_candidate_here(
    hero: Any, make_proposition: Make
) -> None:
    """Detection never resolves. Gate H4 lives in `disposition`, and this
    module must not anticipate it - if it did, a monetary threshold would
    eventually be applied to a non-monetary conflict by the same shortcut."""
    left = make_proposition(
        prop_id=hero.bv_service_v1,
        value=fam.ServiceStatusValue(fam.ServiceState.TERMINATED),
        is_incumbent=True,
    )
    right = make_proposition(prop_id=hero.cl_terminated, source_claim_kind=ClaimKind.CORRECTION)
    finding = cx.match(left, right, _ctx())
    assert finding is not None
    assert not hasattr(finding, "requires_human")


def test_findings_carry_the_canonical_predicate_of_their_family(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """`conflicts.predicate` must be the belief's predicate, and Rule N1 says
    that is the family's canonical one. Writing a surface form there would make
    the conflict unjoinable to the belief it is about."""
    finding = cx.match(incumbent_terminated, entailed_active, _ctx())
    assert finding is not None
    assert finding.predicate == fam.canonical_predicate(fam.Family.SERVICE_STATUS)


def test_a_uuid_is_never_stringified_on_the_way_into_a_finding(
    incumbent_terminated: prop.Proposition, entailed_active: prop.Proposition
) -> None:
    """The write path binds these straight into a `UUID` column."""
    finding = cx.match(incumbent_terminated, entailed_active, _ctx())
    assert finding is not None
    assert isinstance(finding.left_source_id, uuid.UUID)
    assert isinstance(finding.subject_id, uuid.UUID)
