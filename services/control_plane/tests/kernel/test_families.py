"""T4.3 - the closed predicate-family registry.

`specs/12_KERNEL_ALGORITHMS.md` section 2.1. Five families, a canonical
predicate each, a closed surface-predicate table, one legal belief subject
shape, and one value schema. Anything outside the table produces zero
conflicts, deliberately: risk R1 accepts honest silence over invented
contradictions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from provenance_domain import authority as domain_authority
from provenance_domain.enums import SourceClass, SubjectType
from services.control_plane.app.memory_kernel import families as fam

pytestmark = pytest.mark.unit


def test_exactly_five_families_plus_the_unmapped_escape() -> None:
    """Section 2.1: "v1 recognizes exactly five predicate families." A sixth
    family is a table row plus matcher rows M14+, never a quiet addition."""
    assert set(fam.Family) - {fam.Family.UNMAPPED} == {
        fam.Family.SERVICE_STATUS,
        fam.Family.BALANCE,
        fam.Family.PAYMENT,
        fam.Family.OUTSTANDING,
        fam.Family.COMMITMENT_STATUS,
    }


@pytest.mark.parametrize(
    ("family", "canonical"),
    [
        (fam.Family.SERVICE_STATUS, "service_active"),
        (fam.Family.BALANCE, "balance_owed"),
        (fam.Family.PAYMENT, "payment_received"),
        (fam.Family.OUTSTANDING, "deposit_outstanding"),
        (fam.Family.COMMITMENT_STATUS, "commitment_withdrawn"),
    ],
)
def test_each_family_stores_one_canonical_predicate(family: fam.Family, canonical: str) -> None:
    """Rule N1. `beliefs.predicate` always stores the family's canonical
    predicate, so `UNIQUE (tenant, user, subject, predicate)` cannot let two
    mutually exclusive beliefs coexist as separate rows and silently no-op the
    whole contradiction model."""
    assert fam.canonical_predicate(family) == canonical


@pytest.mark.parametrize(
    ("predicate", "family"),
    [
        ("service_active", fam.Family.SERVICE_STATUS),
        ("service_terminated", fam.Family.SERVICE_STATUS),
        ("service_cancelled", fam.Family.SERVICE_STATUS),
        ("service_suspended", fam.Family.SERVICE_STATUS),
        ("balance_owed", fam.Family.BALANCE),
        ("amount_due", fam.Family.BALANCE),
        ("invoice_total", fam.Family.BALANCE),
        ("payment_received", fam.Family.PAYMENT),
        ("payment_sent", fam.Family.PAYMENT),
        ("payment_not_received", fam.Family.PAYMENT),
        ("deposit_outstanding", fam.Family.OUTSTANDING),
        ("refund_outstanding", fam.Family.OUTSTANDING),
        ("reimbursement_outstanding", fam.Family.OUTSTANDING),
        ("commitment_withdrawn", fam.Family.COMMITMENT_STATUS),
        ("commitment_revoked", fam.Family.COMMITMENT_STATUS),
        ("promise_retracted", fam.Family.COMMITMENT_STATUS),
    ],
)
def test_section_2_1_surface_predicates_map_to_their_family(
    predicate: str, family: fam.Family
) -> None:
    """The whole "Surface predicates accepted from proposals" column."""
    assert fam.family_of(predicate) is family


def test_family_assignment_is_total_and_names_the_unmapped_case() -> None:
    """Section 2.2 step 1: map to a family, or `UNMAPPED` and stop. An
    unrecognised predicate is admitted as a claim and produces no conflict."""
    assert fam.family_of("warranty_window_open") is fam.Family.UNMAPPED
    assert fam.family_of("") is fam.Family.UNMAPPED


def test_the_registry_agrees_with_the_domain_authority_table() -> None:
    """`provenance_domain.authority.PREDICATE_FAMILIES` is the grid's key set.
    Where both know a predicate they must agree, or the same claim gets one
    family for matching and another for scoring."""
    for predicate, family_name in domain_authority.PREDICATE_FAMILIES.items():
        assert fam.family_of(predicate).value == family_name, predicate


def test_the_balance_and_service_status_families_are_distinct() -> None:
    """T4.3 acceptance. The hero conflict is family `BALANCE`; the worked
    `AUTO_RESOLVED` example in section 1.6 is `SERVICE_STATUS` in a different
    dataset. Both are correct and they are not the same row."""
    assert fam.family_of("balance_owed") is not fam.family_of("service_active")


@pytest.mark.parametrize(
    ("family", "monetary"),
    [
        (fam.Family.BALANCE, True),
        (fam.Family.PAYMENT, True),
        (fam.Family.OUTSTANDING, True),
        (fam.Family.SERVICE_STATUS, False),
        (fam.Family.COMMITMENT_STATUS, False),
        (fam.Family.UNMAPPED, False),
    ],
)
def test_monetary_families_are_exactly_the_three(family: fam.Family, monetary: bool) -> None:
    """Section 3.3 `MONETARY_FAMILIES`. Gate H5 applies to these and to no
    others; widening the set would apply a money threshold to a service-status
    disagreement."""
    assert fam.is_monetary(family) is monetary
    assert (family in fam.MONETARY_FAMILIES) is monetary


def test_payment_never_produces_a_belief_row() -> None:
    """Section 2.7. Rule N1 would collapse every payment for a subject into one
    belief, so payments are matched against `fulfillments` instead."""
    assert fam.produces_belief(fam.Family.PAYMENT) is False
    assert fam.produces_belief(fam.Family.BALANCE) is True
    assert fam.produces_belief(fam.Family.UNMAPPED) is False


@pytest.mark.parametrize(
    ("family", "subject_type", "allowed"),
    [
        (fam.Family.SERVICE_STATUS, SubjectType.RELATIONSHIP, True),
        (fam.Family.SERVICE_STATUS, SubjectType.CASE, False),
        (fam.Family.BALANCE, SubjectType.RELATIONSHIP, True),
        (fam.Family.BALANCE, SubjectType.CASE, True),
        (fam.Family.OUTSTANDING, SubjectType.COMMITMENT, True),
        (fam.Family.OUTSTANDING, SubjectType.RELATIONSHIP, False),
        (fam.Family.COMMITMENT_STATUS, SubjectType.COMMITMENT, True),
    ],
)
def test_belief_subject_types_follow_the_table(
    family: fam.Family, subject_type: SubjectType, allowed: bool
) -> None:
    """Section 2.1's "Belief subject" column."""
    assert fam.valid_subject_type(family, subject_type) is allowed


def test_the_authority_grid_is_read_from_the_domain_package() -> None:
    """Section 3.2 is the semantic owner and `provenance_domain.authority`
    holds the values. A second copy of sixty numbers is sixty chances to
    disagree with the document the gate reads."""
    assert fam.authority_for(fam.Family.PAYMENT, SourceClass.BANK_OR_CARD_STATEMENT) == Decimal(
        "0.9700"
    )
    assert fam.authority_for(
        fam.Family.SERVICE_STATUS, SourceClass.BANK_OR_CARD_STATEMENT
    ) == Decimal("0.1000")


def test_authority_is_keyed_by_family_not_by_surface_predicate() -> None:
    """A first-class `BALANCE` predicate the domain table does not list must
    still score 0.90 for a provider notice. Routing through
    `authority.authority_for('amount_due', ...)` would silently return the 0.10
    unmapped floor and quietly demote every invoice."""
    family = fam.family_of("amount_due")
    assert fam.authority_for(family, SourceClass.PROVIDER_SYSTEM_NOTICE) == Decimal("0.9000")


def test_an_unknown_source_class_falls_to_the_configured_floor() -> None:
    """Rule N2. A model-proposed `source_class` outside the closed enum maps to
    `unknown_source_class_authority`. Failing low is deliberate: an
    unrecognised source must not be able to displace canonical state."""
    assert fam.is_known_source_class("TOTALLY_TRUSTWORTHY_SOURCE") is False
    assert fam.authority_for(fam.Family.BALANCE, "TOTALLY_TRUSTWORTHY_SOURCE") == Decimal("0.1000")


def test_an_unmapped_family_scores_the_floor_for_every_source() -> None:
    """No predicate outside the registry can borrow another family's score."""
    for source_class in SourceClass:
        assert fam.authority_for(fam.Family.UNMAPPED, source_class) == Decimal("0.1000")


def test_model_inference_is_never_authoritative_about_anything() -> None:
    """Section 3.2: `MODEL_INFERENCE` is 0.05 everywhere. That row is the
    machine-readable form of the kernel rule."""
    for family in fam.MONETARY_FAMILIES | {fam.Family.SERVICE_STATUS}:
        assert fam.authority_for(family, SourceClass.MODEL_INFERENCE) == Decimal("0.0500")


# --- section 2.2 steps 2 and 3, coercion -------------------------------------


@pytest.mark.parametrize(
    ("predicate", "raw", "state"),
    [
        ("service_terminated", True, fam.ServiceState.TERMINATED),
        ("service_terminated", False, fam.ServiceState.ACTIVE),
        ("service_active", True, fam.ServiceState.ACTIVE),
        ("service_active", False, fam.ServiceState.TERMINATED),
        ("service_cancelled", True, fam.ServiceState.TERMINATED),
        ("service_suspended", True, fam.ServiceState.TERMINATED),
        ("service_active", {"state": "TERMINATED"}, fam.ServiceState.TERMINATED),
    ],
)
def test_service_status_polarity_collapses_onto_one_schema(
    predicate: str, raw: object, state: fam.ServiceState
) -> None:
    """Section 2.2 step 2, verbatim: `service_terminated: true` becomes
    `{"state":"TERMINATED"}` and `service_active: false` becomes the same
    thing. Polarity belongs to the surface predicate, which is exactly why
    Rule N1 can collapse all of them onto one belief."""
    value = fam.coerce_value(predicate, raw)
    assert isinstance(value, fam.ServiceStatusValue)
    assert value.state is state


def test_payment_not_received_becomes_an_unasserted_payment() -> None:
    """Section 2.2 step 2. `asserted=False` is what makes rule M5 - one side
    denies a payment the other asserts - expressible at all."""
    paid_at = datetime(2026, 6, 11, tzinfo=UTC)
    value = fam.coerce_value(
        "payment_not_received",
        {"currency": "USD", "amount": Decimal("200.00"), "paid_at": paid_at},
    )
    assert isinstance(value, fam.PaymentValue)
    assert value.asserted is False
    assert value.amount == Decimal("200.0000")


def test_money_is_quantised_to_four_places_and_currency_upper_cased_never_guessed() -> None:
    """Section 2.2 step 3: `Decimal`, quantised to 4 dp, ISO-4217 3-char
    currency, uppercase."""
    value = fam.coerce_value("balance_owed", {"currency": "USD", "amount": Decimal("186.0")})
    assert isinstance(value, fam.BalanceValue)
    assert value.amount == Decimal("186.0000")
    assert value.currency == "USD"


def test_a_float_amount_is_refused_rather_than_coerced() -> None:
    """`float("0.1") + float("0.2") != float("0.3")`. An obligation wrong in
    the seventeenth decimal place is wrong invisibly, and this is the last
    place it can be caught."""
    with pytest.raises(fam.ValueCoercionError):
        fam.coerce_value("balance_owed", {"currency": "USD", "amount": 186.0})


def test_a_lowercase_currency_is_refused_rather_than_repaired() -> None:
    """`provenance_contracts` constrains `CurrencyCode` to `^[A-Z]{3}$` at
    every boundary, so a lowercase code has already escaped a check that should
    have caught it. Silently repairing it here would hide that."""
    with pytest.raises(fam.ValueCoercionError):
        fam.coerce_value("balance_owed", {"currency": "usd", "amount": Decimal("1.00")})


def test_outstanding_and_commitment_status_carry_their_commitment_id() -> None:
    """Sections 2.1 and 2.6: rules M8-M12 all key on `commitment_id`. A value
    schema without it cannot express any of them."""
    cm = uuid.UUID(int=0x3001)
    outstanding = fam.coerce_value(
        "deposit_outstanding",
        {"currency": "USD", "amount": Decimal("1800.00"), "commitment_id": cm},
    )
    withdrawn = fam.coerce_value("commitment_withdrawn", {"withdrawn": True, "commitment_id": cm})
    assert isinstance(outstanding, fam.OutstandingValue)
    assert isinstance(withdrawn, fam.CommitmentStatusValue)
    assert outstanding.commitment_id == cm
    assert withdrawn.withdrawn is True


def test_coercing_an_unmapped_predicate_is_refused() -> None:
    """There is no family, so there is no schema to coerce into. Guessing one
    would invent a contradiction surface the registry deliberately withholds."""
    with pytest.raises(fam.ValueCoercionError):
        fam.coerce_value("warranty_window_open", {"open": True})


# ---------------------------------------------------------------------------
# `paid_at` survives the round trip through JSONB — T4.13
#
# Found by the database lane, not by this one. `claims.object_json` and
# `belief_versions.value_json` are JSONB columns, so every PAYMENT value the
# Kernel reads back from persisted state carries `paid_at` as text; and
# `ProposedClaim.object_value` is typed `JsonValue`, so an inbound proposal
# cannot carry a `datetime` object even in principle. A coercion that accepted
# only `datetime` made the PAYMENT family unreachable from both directions
# while every unit test stayed green, because every unit fixture built
# `PaymentValue` directly instead of coercing one.
# ---------------------------------------------------------------------------


def test_paid_at_is_accepted_as_iso_8601_text() -> None:
    """The shape `object_value` actually arrives in. `_require_uuid` already
    accepts text for the same reason; `paid_at` not doing so was an asymmetry,
    and it surfaced as `SCHEMA_FIELD_MISSING: paid_at is required for this
    family` on a proposal that supplied `paid_at`."""
    moment = datetime(2026, 9, 5, 13, 12, tzinfo=UTC)
    value = fam.coerce_value(
        "payment_received",
        {"currency": "USD", "amount": Decimal("300.0000"), "paid_at": moment.isoformat()},
    )
    assert isinstance(value, fam.PaymentValue)
    assert value.paid_at == moment


def test_a_datetime_paid_at_is_still_accepted() -> None:
    """The in-process path is unchanged; text is an addition, not a swap."""
    moment = datetime(2026, 9, 5, 13, 12, tzinfo=UTC)
    value = fam.coerce_value(
        "payment_received",
        {"currency": "USD", "amount": Decimal("300.0000"), "paid_at": moment},
    )
    assert isinstance(value, fam.PaymentValue)
    assert value.paid_at == moment


def test_a_naive_paid_at_is_refused_rather_than_assumed_utc() -> None:
    """Section 8.1: the Kernel never invents a timezone. A New York payment
    read as UTC lands four hours into the wrong day, and section 2.7 buckets
    payments by `(paid_at - EPOCH) // 3 days` -- so an assumed timezone can
    move a payment into a different identity bucket and stop it matching the
    commitment it settles."""
    with pytest.raises(fam.ValueCoercionError):
        fam.coerce_value(
            "payment_received",
            {"currency": "USD", "amount": Decimal("300.0000"), "paid_at": "2026-09-05T13:12:00"},
        )


def test_unparsable_paid_at_text_is_refused() -> None:
    with pytest.raises(fam.ValueCoercionError):
        fam.coerce_value(
            "payment_received",
            {"currency": "USD", "amount": Decimal("300.0000"), "paid_at": "last Tuesday"},
        )


def test_a_missing_paid_at_is_still_missing() -> None:
    """The original refusal must survive: accepting text may not turn a field
    that was never supplied into one that was."""
    with pytest.raises(fam.ValueCoercionError):
        fam.coerce_value("payment_received", {"currency": "USD", "amount": Decimal("300.0000")})
