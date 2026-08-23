"""Money, and the one derived identity the product is built on.

Authority
---------
- ``implementation/00_IMPLEMENTATION_MAP.md`` section 8: amount is
  ``DECIMAL(20,4)``, currency is an ISO-style 3-character code, the Kernel
  refuses arithmetic across currencies unless an explicit conversion event
  exists, and the derived outstanding amount is
  ``committed_amount - admitted_fulfillment_amount``.
- ``specs/11_CONTRACTS.md`` section 5.1: the money scale rules, the currency
  refusal, and "never clamps" - over-fulfilment yields a negative outstanding
  which the Kernel turns into a ``FULFILLMENT_CONFLICT`` instead of silently
  absorbing.
- ``specs/12_KERNEL_ALGORITHMS.md`` sections 4.1-4.3: recompute from the
  ledger, never increment; the projection is capped at ``committed_amount``
  by the Kernel *with* a conflict row, a ``DISPUTED`` status and an outbox
  event. That capping is a decision with an audit trail. This module's job is
  to hand the Kernel the true number so there is something to decide about.

Stdlib only. No ``pydantic``, no ``provenance_db``, no ``boto3``, no
``httpx``, no ``asyncio``. Safe to call inside a serializable transaction
callback and safe to call again on a 40001 retry.

Why ``float`` is refused rather than coerced
--------------------------------------------
``float("0.1") + float("0.2") != float("0.3")``. An obligation that is wrong
in the seventeenth decimal place is wrong, and it is wrong invisibly. Every
boundary in this system takes money as a decimal string or a ``Decimal``;
accepting a ``float`` here would mean the one place the error could still be
caught chose not to catch it. ``tools/contract_lint.py``'s ``no-float-money``
rule enforces the same ban statically.

Relationship to ``invariants.py``
---------------------------------
``specs/11_CONTRACTS.md`` section 5.1 prints ``derive_outstanding`` inside
``invariants.py``. That function is the *invariant* form - it returns a
``CommitmentAmounts`` record and is owned by T1.4. :func:`outstanding` here is
the pure arithmetic identity it is built from, and is the symbol the
``PV_SABOTAGE`` matrix neuters at ``G1.7``. Keeping the arithmetic in one
place is what makes that sabotage entry meaningful: neutering one function
must be able to turn the whole money surface red.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

__all__ = [
    "CurrencyMismatchError",
    "DERIVATION_NAME",
    "DERIVATION_VERSION",
    "FloatAmountError",
    "InvalidAmountError",
    "InvalidCurrencyError",
    "MONEY_EXPONENT",
    "MONEY_PRECISION",
    "MONEY_SCALE",
    "Money",
    "MoneyError",
    "MoneyPrecisionError",
    "NegativeCommitmentError",
    "NegativeFulfilmentError",
    "SABOTAGED_SYMBOLS",
    "SABOTAGE_ENV_VAR",
    "SABOTAGE_HOOKS",
    "install_sabotage",
    "outstanding",
    "outstanding_money",
    "sabotage_targets",
]

# --- DECIMAL(20,4) -----------------------------------------------------------

#: Total significant digits of the storage type.
MONEY_PRECISION: Final[int] = 20
#: Fractional digits of the storage type.
MONEY_SCALE: Final[int] = 4
#: The quantisation target. Matches ``DECIMAL(20,4)`` exactly.
MONEY_EXPONENT: Final[Decimal] = Decimal("0.0001")
#: One more than the largest representable integral part.
_INTEGRAL_LIMIT: Final[Decimal] = Decimal(10) ** (MONEY_PRECISION - MONEY_SCALE)

#: The registry entry in :mod:`provenance_domain.derivations` that names this
#: module's identity. Declared as a plain string rather than imported so that
#: ``money`` and ``derivations`` stay independent modules; the pair is asserted
#: in ``tests/test_derivations.py``.
DERIVATION_NAME: Final[str] = "outstanding_from_committed_minus_fulfilled"
DERIVATION_VERSION: Final[str] = "1.0.0"


# --- refusals ----------------------------------------------------------------


class MoneyError(ValueError):
    """A monetary rule was broken.

    Carries a stable ``code`` so ``invariants.py`` can map it onto an
    ``InvariantViolation`` and the Kernel onto a ``KernelReasonCode`` without
    parsing an English message. The codes are the ones printed in
    ``specs/11_CONTRACTS.md`` section 5.1.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class InvalidAmountError(MoneyError):
    """The amount was not an exact numeric type."""


class FloatAmountError(InvalidAmountError):
    """The amount was a ``float`` (or a ``bool``, which is an ``int``)."""


class InvalidCurrencyError(MoneyError):
    """The currency was not an ISO-style 3-character uppercase code."""


class MoneyPrecisionError(MoneyError):
    """The amount does not fit ``DECIMAL(20,4)``, or is not finite."""


class CurrencyMismatchError(MoneyError):
    """Arithmetic was attempted across two currencies."""

    def __init__(self, left: str, right: str) -> None:
        super().__init__(
            "CURRENCY_MISMATCH",
            f"refusing arithmetic across {left} and {right} "
            "without an explicit conversion event",
        )


class NegativeCommitmentError(MoneyError):
    """A commitment was for a negative amount. That is not an obligation."""

    def __init__(self, amount: Decimal) -> None:
        super().__init__("NEGATIVE_COMMITMENT", f"committed={amount}")


class NegativeFulfilmentError(MoneyError):
    """A fulfilment was for a negative amount. A refund is its own event."""

    def __init__(self, amount: Decimal) -> None:
        super().__init__("NEGATIVE_FULFILMENT", f"fulfilled={amount}")


# --- validation --------------------------------------------------------------


def _validated_currency(currency: object) -> str:
    """An ISO-style 3-character uppercase code, or a refusal.

    Lowercase is rejected rather than upper-cased. ``provenance_contracts``
    constrains ``CurrencyCode`` to ``^[A-Z]{3}$`` at every boundary, so a
    lowercase code has already escaped a check that should have caught it, and
    silently repairing it here would hide that.
    """
    if not isinstance(currency, str):
        raise InvalidCurrencyError(
            "CURRENCY_INVALID", f"{currency!r} is not a string currency code"
        )
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise InvalidCurrencyError(
            "CURRENCY_INVALID",
            f"{currency!r} is not an ISO-style 3-character currency code",
        )
    if currency != currency.upper():
        raise InvalidCurrencyError(
            "CURRENCY_INVALID", f"{currency!r} must be uppercase, for example 'USD'"
        )
    return currency


def _validated_amount(amount: object) -> Decimal:
    """An exact ``DECIMAL(20,4)``-shaped value, or a refusal."""
    if isinstance(amount, bool):
        raise FloatAmountError(
            "MONEY_NOT_EXACT",
            "bool is not a monetary amount; True is not one dollar",
        )
    if isinstance(amount, float):
        raise FloatAmountError(
            "MONEY_NOT_EXACT",
            f"{amount!r} is a float; binary floating point cannot represent an "
            "obligation exactly. Pass a Decimal, or a decimal string such as "
            '"186.00" at the boundary',
        )
    if isinstance(amount, int):
        amount = Decimal(amount)
    if not isinstance(amount, Decimal):
        raise InvalidAmountError("MONEY_NOT_EXACT", f"{amount!r} is not a Decimal monetary amount")
    if not amount.is_finite():
        raise MoneyPrecisionError("MONEY_NOT_FINITE", f"{amount!r} is not a finite decimal")
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int):
        raise MoneyPrecisionError("MONEY_NOT_FINITE", f"{amount!r} is not a finite decimal")
    if -exponent > MONEY_SCALE:
        raise MoneyPrecisionError(
            "MONEY_SCALE",
            f"{amount} exceeds DECIMAL({MONEY_PRECISION},{MONEY_SCALE}); "
            "round at the source, never here",
        )
    if abs(amount) >= _INTEGRAL_LIMIT:
        raise MoneyPrecisionError(
            "MONEY_RANGE",
            f"{amount} does not fit DECIMAL({MONEY_PRECISION},{MONEY_SCALE})",
        )
    return amount.quantize(MONEY_EXPONENT)


# --- the value type ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in one currency.

    Frozen, because an obligation is not edited in place: a changed amount is
    a new belief version with its own grounding, never a mutated field.

    Negative amounts are *representable*. Section 5.1's ``derive_outstanding``
    is explicit that over-fulfilment yields a negative outstanding, and
    section 4.3 makes that visibility the whole point of "do not silently
    clamp". The non-negativity rule belongs to the *committed* amount
    specifically and is enforced by :func:`outstanding`, not by every value
    that happens to be money.
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", _validated_currency(self.currency))
        object.__setattr__(self, "amount", _validated_amount(self.amount))

    @classmethod
    def zero(cls, currency: str) -> Money:
        """``0.0000`` in *currency*."""
        return cls(Decimal(0), currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


# --- the derived identity ----------------------------------------------------


def outstanding(committed_amount: Decimal, admitted_fulfilment_amount: Decimal) -> Decimal:
    """``outstanding = committed_amount - admitted_fulfilment_amount``.

    Pure, deterministic, and the canonical ``source_kind = 'DERIVATION'`` case:
    a belief version derived this way carries a derivation edge instead of an
    evidence edge and is still grounded. See
    :func:`provenance_domain.derivations.grounding_for` and registry entry
    :data:`DERIVATION_NAME`.

    Never clamps. A result below zero means more was admitted than was ever
    committed, which is an anomaly the Kernel raises as a
    ``FULFILLMENT_CONFLICT`` (``specs/12_KERNEL_ALGORITHMS.md`` section 4.3).
    Returning ``0`` here would destroy the only evidence that anything was
    wrong.

    Raises:
        MoneyPrecisionError: either amount is outside ``DECIMAL(20,4)``.
        NegativeCommitmentError: *committed_amount* is below zero.
        NegativeFulfilmentError: *admitted_fulfilment_amount* is below zero.
    """
    committed = _validated_amount(committed_amount)
    fulfilled = _validated_amount(admitted_fulfilment_amount)
    if committed < 0:
        raise NegativeCommitmentError(committed)
    if fulfilled < 0:
        raise NegativeFulfilmentError(fulfilled)
    return (committed - fulfilled).quantize(MONEY_EXPONENT)


def outstanding_money(committed: Money, admitted_fulfilment: Money) -> Money:
    """:func:`outstanding`, with the currency gate of section 4.2 step 1.

    Delegates to the module-level :func:`outstanding` by name so that the
    ``PV_SABOTAGE`` replacement reaches this path too. A sabotage hook that
    only neutered the function nobody calls would prove nothing.
    """
    if committed.currency != admitted_fulfilment.currency:
        raise CurrencyMismatchError(committed.currency, admitted_fulfilment.currency)
    return Money(outstanding(committed.amount, admitted_fulfilment.amount), committed.currency)


# --- the PV_SABOTAGE hook ----------------------------------------------------
#
# `quality/23_PHASE_GATES.md` G1.7 runs:
#
#     PV_SABOTAGE=provenance_domain.money.outstanding \
#         pytest packages/python/provenance_domain/tests -q; echo "exit=$?"
#
# and requires at least one FAILED and exit=1. A green run there means the
# tests do not actually depend on the identity they claim to prove, which is
# a gate failure rather than a relief. `make sabotage` drives the same hook
# across `tests/sabotage_matrix.yaml` at G14.6.

#: The environment variable the harness sets. Read once, at import.
SABOTAGE_ENV_VAR: Final[str] = "PV_SABOTAGE"

#: The symbols in *this module* that the matrix is allowed to neuter.
SABOTAGE_HOOKS: Final[tuple[str, ...]] = ("outstanding",)


def _identity(*args: Any, **kwargs: Any) -> Any:
    """Return the first argument unchanged.

    The neutering shape used by every ``PV_SABOTAGE`` hook: it type-checks at
    the call site, returns something plausible, and is wrong. Keyword calls
    are handled too, so a caller's calling convention cannot accidentally
    protect a symbol from the sabotage.
    """
    if args:
        return args[0]
    for value in kwargs.values():
        return value
    return None


def sabotage_targets(raw: str | None) -> frozenset[str]:
    """Parse ``PV_SABOTAGE`` into fully qualified symbol names.

    Accepts whitespace- or comma-separated names so one run can neuter several
    symbols. Unset, empty and whitespace-only all mean "no sabotage".
    """
    if not raw:
        return frozenset()
    return frozenset(token for token in raw.replace(",", " ").split() if token)


def install_sabotage(
    namespace: MutableMapping[str, Any],
    module: str,
    symbols: Iterable[str],
    raw: str | None,
) -> tuple[str, ...]:
    """Replace each requested symbol of *module* in *namespace* with the identity.

    Written as a pure function over an explicit namespace rather than as
    import-time magic so that the mechanism itself is testable without
    re-importing a module or mutating the process environment.

    Returns:
        The symbol names actually replaced, in declaration order.

    Raises:
        KeyError: a symbol was requested but is not present in *namespace*. A
            stale matrix entry must surface loudly; silently skipping it would
            report a green sabotage run for a symbol nobody neutered.
    """
    requested = sabotage_targets(raw)
    if not requested:
        return ()
    replaced: list[str] = []
    for symbol in symbols:
        if f"{module}.{symbol}" not in requested:
            continue
        if symbol not in namespace:
            raise KeyError(f"{module}.{symbol} is not defined in {module}")
        namespace[symbol] = _identity
        replaced.append(symbol)
    return tuple(replaced)


#: The symbols this import actually neutered. ``()`` on every normal run.
SABOTAGED_SYMBOLS: Final[tuple[str, ...]] = install_sabotage(
    globals(), __name__, SABOTAGE_HOOKS, os.environ.get(SABOTAGE_ENV_VAR)
)
