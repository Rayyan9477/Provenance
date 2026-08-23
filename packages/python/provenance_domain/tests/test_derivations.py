"""T1.3 — money, the outstanding identity, derivations, and business days.

Sources, transcribed by hand rather than imported:

* `docs/specs/11_CONTRACTS.md` §5.1 (money scale, currency strictness, the
  `outstanding = committed - fulfilled` identity, "never clamps") and §5.3
  (the derivation registry as the *closed* exemption from the grounding
  invariant).
* `docs/implementation/00_IMPLEMENTATION_MAP.md` §8 — `DECIMAL(20,4)`, a
  3-character ISO currency, no arithmetic across currencies without an
  explicit conversion event, never floating point.
* `docs/specs/12_KERNEL_ALGORITHMS.md` §4.3 and §4.6 — over-fulfilment is
  flagged, not silently clamped; the moving-company worked example
  (`USD 420` committed, `USD 200` paid, `USD 220` outstanding).
* `docs/CANONICAL_DECISIONS.md` *Business days* — Monday through Friday with
  no holiday calendar, and extraction must surface
  `BUSINESS_DAY_CALENDAR_ASSUMED`.
* `docs/quality/22_EVAL_DATASETS.md` `TM-04` — Friday 12 June 2026 plus
  "within 10 business days" is Friday 26 June 2026.

Every literal amount in this file is a `Decimal` or an `int`. A `float`
anywhere in a money test would be the defect the `no-float-money` lint exists
to catch, written into the proof itself.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from provenance_domain import derivations, money
from provenance_domain.enums import SupportSourceKind

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# The derived identity
# ---------------------------------------------------------------------------


def test_outstanding_is_committed_minus_admitted_fulfilment() -> None:
    """`outstanding = committed_amount - admitted_fulfilment_amount`, exactly.

    The scale assertion is not decoration: `Decimal("900.00") ==
    Decimal("900.0000")` is true numerically, so equality alone would pass on a
    value that had lost its `DECIMAL(20,4)` shape.
    """
    result = money.outstanding(Decimal("1200.0000"), Decimal("300.0000"))
    assert result == Decimal("900.0000")
    assert str(result) == "900.0000"

    # 12_KERNEL_ALGORITHMS.md §4.6 — the moving-company worked example.
    assert money.outstanding(Decimal("420.00"), Decimal("200.00")) == Decimal("220.0000")
    assert str(money.outstanding(Decimal("420.00"), Decimal("200.00"))) == "220.0000"

    # Nothing owed and nothing paid are both ordinary, not special cases.
    assert money.outstanding(Decimal("186.00"), Decimal("0")) == Decimal("186.0000")
    assert money.outstanding(Decimal("1200"), Decimal("1200")) == Decimal("0.0000")

    # Pure: same inputs, same answer, no state carried between calls.
    assert money.outstanding(Decimal("1200.0000"), Decimal("300.0000")) == result

    # An out-of-scale input is rejected at the source rather than rounded here.
    with pytest.raises(money.MoneyPrecisionError):
        money.outstanding(Decimal("1200.00001"), Decimal("300.0000"))


# ---------------------------------------------------------------------------
# `Money`
# ---------------------------------------------------------------------------


def test_money_rejects_float_bad_currency_and_excess_scale() -> None:
    """The constructor is the boundary. Nothing downstream re-checks these.

    The `186.00` below is the **only** `float` in T1.3, in source or in tests,
    and it is deliberate: T1.3's acceptance criterion is literally
    "constructing `Money(186.00, 'USD')` from a `float` raises", which cannot
    be proved without passing one. `tools/contract_lint.py`'s `no-float-money`
    rule (T1.6) must therefore permit a float literal inside a
    `pytest.raises(...FloatAmountError)` block, or it will flag the test that
    enforces the very rule it implements.
    """
    # 00_IMPLEMENTATION_MAP.md §8: never floating point for an obligation.
    with pytest.raises(money.FloatAmountError):
        money.Money(186.00, "USD")  # the one intentional float in T1.3
    # `bool` is an `int` subclass and would otherwise sail through as 0 or 1.
    with pytest.raises(money.FloatAmountError):
        money.Money(True, "USD")
    # A string amount is a parsing decision this type refuses to make.
    with pytest.raises(money.InvalidAmountError):
        money.Money("186.00", "USD")
    assert issubclass(money.FloatAmountError, money.InvalidAmountError)

    # ISO-style 3-character code, uppercase (11_CONTRACTS.md §6 `CurrencyCode`).
    for bad in ("USDD", "US", "usd", "U5D", "", "USD "):
        with pytest.raises(money.InvalidCurrencyError):
            money.Money(Decimal("186.00"), bad)

    # DECIMAL(20,4). Round at the source, never here.
    with pytest.raises(money.MoneyPrecisionError):
        money.Money(Decimal("1.00001"), "USD")
    with pytest.raises(money.MoneyPrecisionError):
        money.Money(Decimal("NaN"), "USD")
    with pytest.raises(money.MoneyPrecisionError):
        money.Money(Decimal("Infinity"), "USD")

    ok = money.Money(Decimal("186.00"), "USD")
    assert ok.amount == Decimal("186.0000")
    assert str(ok.amount) == "186.0000"
    assert ok.currency == "USD"
    assert money.Money(0, "USD") == money.Money.zero("USD")

    # Frozen: an obligation is not edited in place.
    with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError is a subclass
        ok.amount = Decimal("1.0000")  # type: ignore[misc]

    # Every refusal is one catchable family carrying a stable code.
    for error in (
        money.FloatAmountError,
        money.InvalidAmountError,
        money.InvalidCurrencyError,
        money.MoneyPrecisionError,
        money.CurrencyMismatchError,
        money.NegativeCommitmentError,
        money.NegativeFulfilmentError,
    ):
        assert issubclass(error, money.MoneyError)
        assert issubclass(error, ValueError)


def test_money_arithmetic_refuses_to_cross_currencies() -> None:
    """USD plus EUR is not a number. It is a missing conversion event."""
    usd = money.Money(Decimal("186.00"), "USD")
    eur = money.Money(Decimal("186.00"), "EUR")

    with pytest.raises(money.CurrencyMismatchError) as excinfo:
        _ = usd + eur
    assert "explicit conversion" in str(excinfo.value)
    assert excinfo.value.code == "CURRENCY_MISMATCH"

    with pytest.raises(money.CurrencyMismatchError):
        _ = usd - eur

    # Same currency is ordinary arithmetic and keeps the scale.
    assert usd + money.Money(Decimal("14.00"), "USD") == money.Money(Decimal("200.00"), "USD")
    assert usd - money.Money(Decimal("86.00"), "USD") == money.Money(Decimal("100.00"), "USD")
    assert -usd == money.Money(Decimal("-186.00"), "USD")

    # Equal amounts in different currencies are not equal values.
    assert usd != eur


def test_outstanding_money_is_currency_strict_and_never_clamps() -> None:
    """11_CONTRACTS.md §5.1: over-fulfilment yields a negative outstanding.

    The projection stored in the database is clamped at zero and the excess is
    raised as a `FULFILLMENT_CONFLICT` (12_KERNEL_ALGORITHMS.md §4.3). That
    clamping is the Kernel's job and it is not silent. This pure function must
    hand the Kernel the real number so there is something to notice.
    """
    committed = money.Money(Decimal("420.00"), "USD")
    paid = money.Money(Decimal("200.00"), "USD")
    assert money.outstanding_money(committed, paid) == money.Money(Decimal("220.00"), "USD")

    with pytest.raises(money.CurrencyMismatchError):
        money.outstanding_money(committed, money.Money(Decimal("200.00"), "EUR"))

    over = money.outstanding_money(committed, money.Money(Decimal("500.00"), "USD"))
    assert over.amount == Decimal("-80.0000")
    assert over.currency == "USD"

    # A commitment cannot be for a negative amount; a fulfilment cannot be
    # negative either. Both are invariant violations, not clamping candidates.
    with pytest.raises(money.NegativeCommitmentError) as neg:
        money.outstanding(Decimal("-1.0000"), Decimal("0.0000"))
    assert neg.value.code == "NEGATIVE_COMMITMENT"
    with pytest.raises(money.NegativeFulfilmentError):
        money.outstanding(Decimal("100.0000"), Decimal("-1.0000"))
    with pytest.raises(money.NegativeCommitmentError):
        money.outstanding_money(
            money.Money(Decimal("-1.00"), "USD"), money.Money(Decimal("0"), "USD")
        )


# ---------------------------------------------------------------------------
# The derivation registry
# ---------------------------------------------------------------------------


def test_outstanding_is_the_canonical_derivation_and_grounds_without_evidence() -> None:
    """11_CONTRACTS.md §5.3 — the closed exemption from the grounding invariant.

    A belief version derived this way carries a `DERIVATION` support edge
    instead of an evidence edge and is still grounded.
    """
    assert money.DERIVATION_NAME == "outstanding_from_committed_minus_fulfilled"
    assert money.DERIVATION_VERSION == "1.0.0"

    assert derivations.is_registered_derivation(money.DERIVATION_NAME, money.DERIVATION_VERSION)
    assert not derivations.is_registered_derivation(money.DERIVATION_NAME, "9.9.9")
    assert not derivations.is_registered_derivation("outstanding_but_vibes", "1.0.0")

    assert set(derivations.DERIVATION_REGISTRY) == {
        "outstanding_from_committed_minus_fulfilled",
        "commitment_overdue_from_due_at",
        "case_attention_from_open_conflicts",
        "service_period_overlap",
    }
    spec = derivations.DERIVATION_REGISTRY[money.DERIVATION_NAME]
    assert spec.input_kinds == ("COMMITMENT", "FULFILLMENT")
    assert spec.function_version == "1.0.0"

    grounding = derivations.grounding_for(money.DERIVATION_NAME, money.DERIVATION_VERSION)
    assert grounding.source_kind is SupportSourceKind.DERIVATION
    assert grounding.carries_evidence_edge is False
    assert grounding.is_grounded is True
    assert grounding.spec is spec

    # An unregistered name is not a grounding exemption, it is a mistake.
    with pytest.raises(derivations.UnregisteredDerivationError):
        derivations.grounding_for("outstanding_but_vibes", "1.0.0")
    with pytest.raises(derivations.UnregisteredDerivationError):
        derivations.grounding_for(money.DERIVATION_NAME, "9.9.9")

    # The registry is closed at import; nothing may add an exemption at runtime.
    with pytest.raises(TypeError):
        derivations.DERIVATION_REGISTRY["outstanding_but_vibes"] = spec  # type: ignore[index]


# ---------------------------------------------------------------------------
# Business days
# ---------------------------------------------------------------------------


def test_business_days_are_monday_to_friday_and_flag_the_assumed_calendar() -> None:
    """CANONICAL_DECISIONS.md *Business days*, and eval `TM-04`.

    The flag is the point. Arithmetic that is right for the wrong reason - a
    jurisdiction whose holidays this build has never heard of - must announce
    the assumption rather than present the instant as authoritative.
    """
    friday = date(2026, 6, 12)
    assert friday.weekday() == 4

    result = derivations.add_business_days(friday, 10)
    assert result.due_on == date(2026, 6, 26)
    assert result.start_on == friday
    assert result.business_days == 10

    # Test the flag, not just the arithmetic.
    assert derivations.BUSINESS_DAY_CALENDAR_ASSUMED == "BUSINESS_DAY_CALENDAR_ASSUMED"
    assert result.uncertainty_flags == (derivations.BUSINESS_DAY_CALENDAR_ASSUMED,)
    # Unconditional: a zero-day computation relied on the same assumption.
    zero = derivations.add_business_days(friday, 0)
    assert zero.uncertainty_flags == (derivations.BUSINESS_DAY_CALENDAR_ASSUMED,)
    assert zero.due_on == friday

    # Saturday and Sunday are never business days and are never counted.
    assert derivations.is_business_day(date(2026, 6, 13)) is False  # Saturday
    assert derivations.is_business_day(date(2026, 6, 14)) is False  # Sunday
    assert derivations.is_business_day(date(2026, 6, 15)) is True  # Monday
    assert derivations.add_business_days(friday, 1).due_on == date(2026, 6, 15)

    # No holiday calendar: 2026-07-04 falls on a Saturday, so Friday 2026-07-03
    # is the observed US Independence Day. v1 counts it as an ordinary
    # business day, and says so through the flag rather than through silence.
    assert derivations.add_business_days(date(2026, 7, 2), 1).due_on == date(2026, 7, 3)
    assert derivations.is_business_day(date(2026, 7, 3)) is True

    with pytest.raises(ValueError):
        derivations.add_business_days(friday, -1)


# ---------------------------------------------------------------------------
# The `PV_SABOTAGE` hook
# ---------------------------------------------------------------------------


def test_pv_sabotage_hook_can_neuter_the_outstanding_identity() -> None:
    """`G1.7` neuters `provenance_domain.money.outstanding` and requires red.

    The hook is exercised as a pure function over an explicit namespace, so the
    mechanism is stated without re-importing the module or mutating the process
    environment. The one assertion that *is* environment-sensitive is
    `SABOTAGED_SYMBOLS == ()`: on an ordinary run it proves the hook is off by
    default, and under the `G1.7` probe it correctly goes red alongside the
    identity tests above rather than quietly agreeing with the sabotage.
    """
    assert money.SABOTAGE_ENV_VAR == "PV_SABOTAGE"
    assert money.SABOTAGE_HOOKS == ("outstanding",)
    assert money.SABOTAGED_SYMBOLS == ()

    assert money.sabotage_targets(None) == frozenset()
    assert money.sabotage_targets("   ") == frozenset()
    assert money.sabotage_targets("provenance_domain.money.outstanding") == frozenset(
        {"provenance_domain.money.outstanding"}
    )
    assert money.sabotage_targets("a.b, c.d") == frozenset({"a.b", "c.d"})

    namespace: dict[str, object] = {"outstanding": money.outstanding}
    replaced = money.install_sabotage(
        namespace,
        "provenance_domain.money",
        ("outstanding",),
        "provenance_domain.money.outstanding",
    )
    assert replaced == ("outstanding",)
    neutered = namespace["outstanding"]
    assert callable(neutered)
    # An identity function: it hands back the committed amount untouched, so
    # every assertion about the derived value must go red.
    assert neutered(Decimal("1200.0000"), Decimal("300.0000")) == Decimal("1200.0000")

    untouched: dict[str, object] = {"outstanding": money.outstanding}
    assert (
        money.install_sabotage(untouched, "provenance_domain.money", ("outstanding",), None) == ()
    )
    assert untouched["outstanding"] is money.outstanding

    # A symbol the namespace does not hold is not silently skipped; a stale
    # matrix entry must surface rather than report a green sabotage run.
    with pytest.raises(KeyError):
        money.install_sabotage(
            {},
            "provenance_domain.money",
            ("outstanding",),
            "provenance_domain.money.outstanding",
        )

    # The public identity takes exactly the two amounts and nothing else.
    assert list(inspect.signature(money.outstanding).parameters) == [
        "committed_amount",
        "admitted_fulfilment_amount",
    ]
