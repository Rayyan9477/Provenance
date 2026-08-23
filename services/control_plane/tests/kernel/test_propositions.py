"""T4.3 - the proposition normal form, the day boundary, and entailment.

`specs/12_KERNEL_ALGORITHMS.md` sections 2.2, 2.3, 2.4 and 8.2. The
day-boundary convention has its own tests because getting it wrong by one day
turns the hero's `VALUE_CONFLICT` into a `TEMPORAL_CONFLICT` and turns the
late-arriving May letter (section 8.7 L-3) into a spurious conflict that
reopens a case for a document that agrees with everything.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from provenance_domain.enums import ClaimKind, SourceClass, SubjectType
from services.control_plane.app.memory_kernel import families as fam
from services.control_plane.app.memory_kernel import propositions as prop
from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG, KernelConfig

pytestmark = pytest.mark.unit


# --- section 2.4, the day-boundary convention --------------------------------


def test_day_boundary_terminated_31_may_is_june_1_0400z(hero: Any) -> None:
    """Section 2.4, worked. "service terminated 31 May 2026" in
    `America/New_York` (UTC-4 in May) means the TERMINATED state begins at
    `2026-06-01T04:00:00Z`, because "through D" is an *exclusive* upper bound
    at `(D+1) 00:00` local. Off by one day here and the hero produces a
    `TEMPORAL_CONFLICT` instead of a `VALUE_CONFLICT`."""
    assert prop.normalize_terminated_on(date(2026, 5, 31), hero.tz) == hero.jun_1


def test_day_boundary_effective_from_is_the_inclusive_local_midnight(hero: Any) -> None:
    """ "effective / starting / from D" is `D 00:00` local, converted to UTC."""
    assert prop.normalize_effective_from(date(2026, 6, 1), hero.tz) == hero.jun_1


def test_invoice_period_june_1_to_30_is_a_thirty_day_half_open_interval(hero: Any) -> None:
    """Section 2.4: "Invoice period June 1-30" -> `[2026-06-01T04:00:00Z,
    2026-07-01T04:00:00Z)`. Overlap with the open-ended TERMINATED interval is
    30 days, not zero."""
    lo, hi = prop.normalize_service_period(date(2026, 6, 1), date(2026, 6, 30), hero.tz)
    assert (lo, hi) == (hero.jun_1, hero.jul_1)
    assert hi - lo == timedelta(days=30)


def test_the_convention_respects_the_daylight_saving_offset_change(hero: Any) -> None:
    """January is UTC-5 in New York and June is UTC-4. A convention that hard
    coded one offset would be wrong for half the year, and the error would look
    like a one-hour parsing artifact rather than a bug."""
    assert prop.day_start_utc(date(2026, 1, 15), hero.tz) == datetime(2026, 1, 15, 5, 0, tzinfo=UTC)
    assert prop.day_start_utc(date(2026, 6, 15), hero.tz) == datetime(2026, 6, 15, 4, 0, tzinfo=UTC)


def test_every_normalised_instant_is_timezone_aware_utc(hero: Any) -> None:
    """A naive datetime compared against an aware one raises at the worst
    possible moment - inside the transaction, on one row in a thousand."""
    moment = prop.normalize_effective_from(date(2026, 6, 1), hero.tz)
    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)


# --- pipeline step 9, temporal applicability ---------------------------------


def test_an_inverted_interval_is_rejected_rather_than_coerced(hero: Any) -> None:
    """Step 9 -> `VALIDITY_INVERTED` -> `REJECTED_SCHEMA`. Swapping the bounds
    to "fix" an extractor bug would hide the extractor bug forever."""
    codes = prop.validate_validity(hero.jul_1, hero.jun_1, hero.tx_now)
    assert "VALIDITY_INVERTED" in [c.value for c in codes]


def test_a_zero_length_interval_is_inverted_for_the_purposes_of_step_9(hero: Any) -> None:
    """`[a, a)` is half-open and therefore empty. It states nothing."""
    codes = prop.validate_validity(hero.jun_1, hero.jun_1, hero.tx_now)
    assert "VALIDITY_INVERTED" in [c.value for c in codes]


def test_a_date_beyond_the_ten_year_horizon_is_rejected(hero: Any) -> None:
    """Almost always a parse error, never a ten-year promise."""
    far = hero.tx_now + timedelta(days=DEFAULT_KERNEL_CONFIG.future_validity_horizon_days + 1)
    codes = prop.validate_validity(hero.tx_now, far, hero.tx_now)
    assert "VALIDITY_FUTURE_BEYOND_HORIZON" in [c.value for c in codes]


def test_the_hero_interval_passes_step_9_cleanly(hero: Any) -> None:
    """Section 1.6 step 9: `valid_from < valid_to`, `valid_to` within horizon."""
    assert prop.validate_validity(hero.jun_1, hero.jul_1, hero.tx_now) == ()


def test_an_open_ended_interval_passes_step_9(hero: Any) -> None:
    """`valid_to = NULL` means open-ended, which is the commonest shape a
    cancellation confirmation has."""
    assert prop.validate_validity(hero.jun_1, None, hero.tx_now) == ()


# --- section 2.2, normalisation ----------------------------------------------


def test_normalisation_assigns_authority_from_the_grid_not_from_the_model(hero: Any) -> None:
    """Rule N2: the Kernel assigns authority. There is no `authority_score`
    parameter to pass, so a model-proposed score has nowhere to enter."""
    result = prop.normalize_claim(
        prop_id=hero.cl_invoice,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=hero.rel_isp,
        predicate="balance_owed",
        raw_value={"currency": "USD", "amount": Decimal("186.00")},
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE.value,
        claim_kind=ClaimKind.COUNTERPARTY_CLAIM,
        valid_from=hero.jun_1,
        valid_to=hero.jul_1,
        validity_basis=prop.ValidityBasis.EXPLICIT,
        recorded_at=hero.tx_now,
    )
    assert result.family is fam.Family.BALANCE
    assert result.proposition is not None
    assert result.proposition.base_authority == Decimal("0.9000")
    assert result.proposition.authority == Decimal("0.9000")


def test_an_unmapped_predicate_normalises_to_no_proposition(hero: Any) -> None:
    """Section 2.1: admitted as a claim, may ground a belief, never produces a
    `conflicts` row. The claim is not lost; it simply never reaches the
    matcher."""
    result = prop.normalize_claim(
        prop_id=hero.cl_invoice,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=hero.rel_isp,
        predicate="warranty_window_open",
        raw_value={"open": True},
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE.value,
        recorded_at=hero.tx_now,
    )
    assert result.family is fam.Family.UNMAPPED
    assert result.proposition is None


def test_an_unknown_source_class_is_reported_not_silently_floored(hero: Any) -> None:
    """The 0.10 floor is right; applying it silently is not. Reason code
    `AUTHORITY_UNMAPPED_SOURCE_CLASS` is how the telemetry notices an agent
    runtime inventing vocabulary."""
    result = prop.normalize_claim(
        prop_id=hero.cl_invoice,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=hero.rel_isp,
        predicate="balance_owed",
        raw_value={"currency": "USD", "amount": Decimal("186.00")},
        source_class="A_VERY_RELIABLE_SOURCE",
        recorded_at=hero.tx_now,
    )
    assert result.proposition is not None
    assert result.proposition.base_authority == Decimal("0.1000")
    assert "AUTHORITY_UNMAPPED_SOURCE_CLASS" in [c.value for c in result.reason_codes]


def test_validity_defaults_to_unknown_and_the_kernel_never_invents_a_date(hero: Any) -> None:
    """Rule T2. The Kernel never falls back to `received_at`: "we processed
    your refund" with no date is not evidence about any particular window."""
    result = prop.normalize_claim(
        prop_id=hero.cl_invoice,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=hero.rel_isp,
        predicate="balance_owed",
        raw_value={"currency": "USD", "amount": Decimal("186.00")},
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE.value,
        recorded_at=hero.tx_now,
    )
    assert result.proposition is not None
    assert result.proposition.validity_basis is prop.ValidityBasis.UNKNOWN
    assert result.proposition.valid_from is None
    assert result.proposition.valid_to is None


def test_the_entailment_penalty_is_applied_by_the_authority_property(hero: Any) -> None:
    """Section 2.2: `authority = base - penalty` for an entailed proposition,
    floored at zero, and `base` for a direct one."""
    direct = prop.Proposition(
        prop_id=hero.cl_invoice,
        source_kind=prop.PropositionSourceKind.CLAIM,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=hero.rel_isp,
        family=fam.Family.SERVICE_STATUS,
        predicate="service_active",
        value=fam.ServiceStatusValue(fam.ServiceState.ACTIVE),
        valid_from=hero.jun_1,
        valid_to=hero.jul_1,
        validity_basis=prop.ValidityBasis.EXPLICIT,
        base_authority=Decimal("0.8800"),
        recorded_at=hero.tx_now,
    )
    entailed = dataclasses.replace(direct, entailed_from=hero.cl_invoice)
    assert direct.authority == Decimal("0.8800")
    assert entailed.authority == Decimal("0.5800")


def test_the_penalty_floors_at_zero_and_never_goes_negative(hero: Any) -> None:
    """A 0.05 `MODEL_INFERENCE` claim entailed into another family cannot carry
    a negative weight into a comparison."""
    weak = prop.Proposition(
        prop_id=hero.cl_invoice,
        source_kind=prop.PropositionSourceKind.CLAIM,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=hero.rel_isp,
        family=fam.Family.SERVICE_STATUS,
        predicate="service_active",
        value=fam.ServiceStatusValue(fam.ServiceState.ACTIVE),
        valid_from=hero.jun_1,
        valid_to=hero.jul_1,
        validity_basis=prop.ValidityBasis.EXPLICIT,
        base_authority=Decimal("0.0500"),
        recorded_at=hero.tx_now,
        entailed_from=hero.cl_invoice,
    )
    assert weak.authority == Decimal("0.0000")


# --- section 2.3, entailment -------------------------------------------------


def test_en1_entails_service_active_at_authority_minus_penalty(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """The rule the entire hero demo rests on. An invoice for June does not
    literally say "service was active in June"; EN-1 entails it, at 0.88 - 0.30
    = 0.58. Without this rule the ISP invoice never contradicts the termination
    belief and the demo does not exist."""
    invoice = make_proposition(
        prop_id=hero.cl_invoice,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("186.0000")),
        valid_from=None,
        valid_to=None,
        validity_basis=prop.ValidityBasis.UNKNOWN,
        base_authority=Decimal("0.9000"),
        service_period=(hero.jun_1, hero.jul_1),
    )
    (entailed,) = prop.entail(invoice)
    assert entailed.family is fam.Family.SERVICE_STATUS
    assert entailed.value == fam.ServiceStatusValue(fam.ServiceState.ACTIVE)
    assert (entailed.valid_from, entailed.valid_to) == (hero.jun_1, hero.jul_1)
    assert entailed.validity_basis is prop.ValidityBasis.EXPLICIT
    assert entailed.base_authority == Decimal("0.8800")
    assert entailed.authority == Decimal("0.5800")
    assert entailed.entailment_rule == "EN-1"


def test_en1_does_not_fire_without_a_service_period(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """A balance with no billed period entails nothing about service. The
    antecedent is a non-null, non-inverted interval, and inventing one would be
    exactly the date-guessing rule T2 forbids."""
    invoice = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("186.0000")),
        service_period=None,
    )
    assert prop.entail(invoice) == ()


def test_en1_does_not_fire_on_an_inverted_service_period(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """An inverted antecedent is a parse error, not an implication."""
    invoice = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("186.0000")),
        service_period=(hero.jul_1, hero.jun_1),
    )
    assert prop.entail(invoice) == ()


def test_entailed_propositions_are_never_persisted_as_claims(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """Invariant 1: `claims` records what actors actually asserted. The ISP
    asserted an amount and a period; it did not assert "the service was
    active". The entailment's durable trace is the `belief_support` edge whose
    `source_id` is the parent claim and whose `reason_code` is `EN-1`."""
    invoice = make_proposition(
        prop_id=hero.cl_invoice,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("186.0000")),
        service_period=(hero.jun_1, hero.jul_1),
    )
    (entailed,) = prop.entail(invoice)
    assert prop.is_persistable_claim(invoice) is True
    assert prop.is_persistable_claim(entailed) is False
    assert entailed.entailed_from == hero.cl_invoice
    assert entailed.prop_id == hero.cl_invoice


def test_entailment_does_not_chain(
    hero: Any, make_proposition: Callable[..., prop.Proposition]
) -> None:
    """Section 2.10 item 5: EN-1 and EN-2 fire once. No transitive closure, no
    fixpoint loop - a rule set with no termination bound cannot be tested."""
    invoice = make_proposition(
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("186.0000")),
        service_period=(hero.jun_1, hero.jul_1),
    )
    (entailed,) = prop.entail(invoice)
    assert prop.entail(entailed) == ()


def test_en2_turns_a_zero_outstanding_into_a_settlement_claim(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """Section 2.3 EN-2: "your deposit was fully returned" must be able to
    collide with the fulfillment ledger. It entails both a non-withdrawn
    commitment and a synthetic full-settlement payment."""
    settled = make_proposition(
        family=fam.Family.OUTSTANDING,
        predicate="deposit_outstanding",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("USD", Decimal("0.0000"), hero.cm_deposit),
        base_authority=Decimal("0.7200"),
    )
    entailed = prop.entail(
        settled,
        commitments={
            hero.cm_deposit: prop.CommitmentFacts(hero.cm_deposit, "USD", Decimal("1800.0000"))
        },
    )
    families = {p.family for p in entailed}
    assert families == {fam.Family.COMMITMENT_STATUS, fam.Family.PAYMENT}
    payment = next(p for p in entailed if p.family is fam.Family.PAYMENT)
    assert isinstance(payment.value, fam.PaymentValue)
    assert payment.value.asserted is True
    assert payment.value.amount == Decimal("1800.0000")
    assert payment.value.external_ref == "ENTAILED_FULL_SETTLEMENT"
    assert all(p.entailment_rule == "EN-2" for p in entailed)


def test_en2_does_not_fire_on_a_non_zero_outstanding(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """ "You still owe 900" is not a claim that anything was settled."""
    partial = make_proposition(
        family=fam.Family.OUTSTANDING,
        predicate="deposit_outstanding",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("USD", Decimal("900.0000"), hero.cm_deposit),
    )
    assert (
        prop.entail(
            partial,
            commitments={
                hero.cm_deposit: prop.CommitmentFacts(hero.cm_deposit, "USD", Decimal("1800.0000"))
            },
        )
        == ()
    )


def test_en2_does_not_fire_without_the_commitment_row(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """The synthetic payment's amount is the *committed* amount. With no
    commitment row there is no amount, and inventing one would be a monetary
    guess."""
    settled = make_proposition(
        family=fam.Family.OUTSTANDING,
        predicate="deposit_outstanding",
        subject_type=SubjectType.COMMITMENT,
        subject_id=hero.cm_deposit,
        value=fam.OutstandingValue("USD", Decimal("0.0000"), hero.cm_deposit),
    )
    assert prop.entail(settled, commitments={}) == ()


def test_only_two_entailment_rules_exist(hero: Any) -> None:
    """Section 2.3 defines two rules "and no more". A third would need its own
    penalty calibration and its own termination argument."""
    assert prop.ENTAILMENT_RULES == ("EN-1", "EN-2")


def test_a_service_status_proposition_entails_nothing(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """Only BALANCE (EN-1) and OUTSTANDING (EN-2) are antecedents."""
    assert prop.entail(make_proposition()) == ()


def test_the_penalty_comes_from_the_config_not_from_a_literal(hero: Any) -> None:
    """A test that loosens the penalty must change the arithmetic, or the
    threshold is not really configuration."""
    generous = KernelConfig(entailment_penalty=Decimal("0.10"))
    invoice = prop.Proposition(
        prop_id=hero.cl_invoice,
        source_kind=prop.PropositionSourceKind.CLAIM,
        subject_type=SubjectType.RELATIONSHIP,
        subject_id=hero.rel_isp,
        family=fam.Family.BALANCE,
        predicate="balance_owed",
        value=fam.BalanceValue("USD", Decimal("186.0000")),
        valid_from=None,
        valid_to=None,
        validity_basis=prop.ValidityBasis.UNKNOWN,
        base_authority=Decimal("0.9000"),
        recorded_at=hero.tx_now,
        source_class=SourceClass.PROVIDER_SYSTEM_NOTICE.value,
        service_period=(hero.jun_1, hero.jul_1),
    )
    (entailed,) = prop.entail(invoice, cfg=generous)
    assert entailed.authority == Decimal("0.7800")


def test_the_late_arriving_may_letter_normalises_to_an_abutting_interval(hero: Any) -> None:
    """Section 8.7 L-3: the May letter's interval is
    `[2026-05-01T04:00Z, 2026-06-01T04:00Z)`, which *abuts* the June-onward
    TERMINATED interval and therefore does not overlap it. The day-boundary
    convention is what makes abutting exact rather than approximate."""
    lo, hi = prop.normalize_service_period(date(2026, 5, 1), date(2026, 5, 31), hero.tz)
    assert (lo, hi) == (hero.may_1, hero.jun_1)


def test_proposition_identity_survives_a_uuid_round_trip(
    hero: Any,
    make_proposition: Callable[..., prop.Proposition],
) -> None:
    """The matcher compares by `(subject_type, subject_id)`; a proposition that
    silently stringified its ids would compare unequal to itself."""
    p = make_proposition(subject_id=uuid.UUID(int=0x1001))
    assert p.subject_id == hero.rel_isp
    assert isinstance(p.subject_id, uuid.UUID)
