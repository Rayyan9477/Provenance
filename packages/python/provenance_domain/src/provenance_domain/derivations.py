"""The closed registry of deterministic derivations, and the business-day rule.

Authority
---------
- ``specs/11_CONTRACTS.md`` section 5.3. The grounding invariant says a
  canonical belief version must carry at least one ``SUPPORTS`` edge **unless
  it is an explicitly defined deterministic derivation**. This module is that
  definition. A ``ProposedBeliefMutation`` may skip grounding only by naming a
  derivation that appears here.
- ``CANONICAL_DECISIONS.md``, *Business days*: v1 means Monday through Friday
  with no holiday calendar, and extraction must surface
  ``BUSINESS_DAY_CALENDAR_ASSUMED``.
- ``quality/22_EVAL_DATASETS.md`` ``TM-04`` and its risk note R4: v1 computes
  Monday-Friday only, marks the result ``BUSINESS_DAY_CALENDAR_ASSUMED``,
  keeps the scenario non-blocking, and never presents the instant as
  jurisdictionally authoritative.

Stdlib only. No ``pydantic``, no ``provenance_db``, no ``boto3``, no
``httpx``, no ``asyncio``.

Why the registry is closed
--------------------------
"Grounded because it was derived" is the one route by which a canonical belief
may exist with no evidence behind it. If that route accepted an arbitrary
string, any agent could ground any claim by inventing a derivation name, and
the grounding invariant would be decoration. The registry is a
``MappingProxyType`` built at import: adding a derivation is a code change with
a test diff, which is the point.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Final

from provenance_domain.enums import SupportSourceKind

__all__ = [
    "BUSINESS_DAY_CALENDAR_ASSUMED",
    "BUSINESS_WEEKDAYS",
    "BusinessDayComputation",
    "DERIVATION_REGISTRY",
    "DerivationGrounding",
    "DerivationSpec",
    "MAX_BUSINESS_DAYS",
    "UnregisteredDerivationError",
    "add_business_days",
    "grounding_for",
    "is_business_day",
    "is_registered_derivation",
]


class UnregisteredDerivationError(ValueError):
    """A name was offered as a grounding exemption and is not in the registry."""

    def __init__(self, name: str, function_version: str) -> None:
        self.name = name
        self.function_version = function_version
        super().__init__(
            f"UNREGISTERED_DERIVATION: {name!r} at version {function_version!r} is not a "
            "registered deterministic derivation, so it cannot stand in for grounding"
        )


@dataclass(frozen=True, slots=True)
class DerivationSpec:
    """One deterministic derivation, versioned.

    ``function_version`` is part of the identity: a derivation whose rule
    changed is a different derivation, and belief versions grounded by the old
    one must remain explicable in the terms that produced them.
    """

    name: str
    function_version: str
    input_kinds: tuple[str, ...]
    description: str


DERIVATION_REGISTRY: Final[Mapping[str, DerivationSpec]] = MappingProxyType(
    {
        spec.name: spec
        for spec in (
            DerivationSpec(
                name="outstanding_from_committed_minus_fulfilled",
                function_version="1.0.0",
                input_kinds=("COMMITMENT", "FULFILLMENT"),
                description="outstanding = committed - admitted fulfilment total",
            ),
            DerivationSpec(
                name="commitment_overdue_from_due_at",
                function_version="1.0.0",
                input_kinds=("COMMITMENT", "CLOCK"),
                description="overdue = clock.now >= due_at AND outstanding > 0",
            ),
            DerivationSpec(
                name="case_attention_from_open_conflicts",
                function_version="1.0.0",
                input_kinds=("CASE", "CONFLICT"),
                description="attention rises to URGENT while an OPEN conflict exists",
            ),
            DerivationSpec(
                name="service_period_overlap",
                function_version="1.0.0",
                input_kinds=("BELIEF_VERSION", "CLAIM"),
                description=(
                    "a billed period overlapping a terminated period is mutually exclusive"
                ),
            ),
        )
    }
)


@dataclass(frozen=True, slots=True)
class DerivationGrounding:
    """What a derived belief version carries *instead of* an evidence edge.

    ``specs/12_KERNEL_ALGORITHMS.md`` section 3.2 lists ``DERIVATION`` beside
    ``CLAIM`` and ``BELIEF_VERSION`` as a ``source_kind``; the edge is real and
    persisted, it simply points at a function rather than at a document. The
    belief is grounded, and State Proof can render the arithmetic that
    produced it.
    """

    spec: DerivationSpec
    source_kind: SupportSourceKind
    carries_evidence_edge: bool
    is_grounded: bool


def is_registered_derivation(name: str, function_version: str) -> bool:
    """True when *name* at *function_version* may stand in for grounding."""
    spec = DERIVATION_REGISTRY.get(name)
    return spec is not None and spec.function_version == function_version


def grounding_for(name: str, function_version: str) -> DerivationGrounding:
    """The grounding a belief version derived by *name* carries.

    Raises:
        UnregisteredDerivationError: *name* at *function_version* is not
            registered. Failing closed here is the whole value of the module:
            an unknown derivation must not be able to buy grounding.
    """
    if not is_registered_derivation(name, function_version):
        raise UnregisteredDerivationError(name, function_version)
    return DerivationGrounding(
        spec=DERIVATION_REGISTRY[name],
        source_kind=SupportSourceKind.DERIVATION,
        carries_evidence_edge=False,
        is_grounded=True,
    )


# --- business days -----------------------------------------------------------

#: The uncertainty flag every business-day computation must surface. Not a
#: member of a closed enum: ``specs/11_CONTRACTS.md`` section 3 declares no
#: uncertainty vocabulary, and ``ClaimCandidate.must_flag_uncertainty`` carries
#: free strings. Declared once here so callers cannot misspell it.
BUSINESS_DAY_CALENDAR_ASSUMED: Final[str] = "BUSINESS_DAY_CALENDAR_ASSUMED"

#: Monday through Friday, as ``date.weekday()`` values. No holiday calendar.
BUSINESS_WEEKDAYS: Final[frozenset[int]] = frozenset({0, 1, 2, 3, 4})

#: A sanity bound. Ten years of business days is already far past
#: ``KernelConfig.future_validity_horizon_days``; anything larger is a bug in
#: the caller, not a deadline.
MAX_BUSINESS_DAYS: Final[int] = 3650


@dataclass(frozen=True, slots=True)
class BusinessDayComputation:
    """A due date, and the assumption that produced it.

    The flag travels with the value rather than beside it. A caller that wants
    ``due_on`` cannot obtain it without also holding the reason it might be
    wrong, which is the only reliable way to make the disclosure survive the
    trip to the trigger and the user interface.
    """

    start_on: date
    business_days: int
    due_on: date
    uncertainty_flags: tuple[str, ...]


def is_business_day(day: date) -> bool:
    """True on Monday through Friday.

    Deliberately holiday-blind. v1 has no authoritative holiday source, and
    inventing one would produce a deadline that looks jurisdictional and is
    not. The honest failure mode is a flagged assumption, not a confident
    wrong answer.
    """
    return day.weekday() in BUSINESS_WEEKDAYS


def add_business_days(start_on: date, business_days: int) -> BusinessDayComputation:
    """*business_days* business days after *start_on*, counting Monday-Friday.

    The start date is not counted, matching the reading of "within 10 business
    days" in eval ``TM-04``: Friday 12 June 2026 plus ten business days is
    Friday 26 June 2026. Weekend days are skipped and never consume a count.
    ``business_days=0`` returns *start_on* unchanged.

    The result always carries :data:`BUSINESS_DAY_CALENDAR_ASSUMED`,
    unconditionally. A zero-day computation relied on the same assumption as a
    ten-day one, and a flag that appears only sometimes is a flag consumers
    learn to ignore.

    Raises:
        ValueError: *business_days* is negative or beyond
            :data:`MAX_BUSINESS_DAYS`.
    """
    if business_days < 0:
        raise ValueError(
            f"business_days must not be negative, got {business_days}; "
            "a deadline before its own start is a caller bug, not a date"
        )
    if business_days > MAX_BUSINESS_DAYS:
        raise ValueError(f"business_days must not exceed {MAX_BUSINESS_DAYS}, got {business_days}")
    due_on = start_on
    remaining = business_days
    while remaining > 0:
        due_on = due_on + timedelta(days=1)
        if is_business_day(due_on):
            remaining -= 1
    return BusinessDayComputation(
        start_on=start_on,
        business_days=business_days,
        due_on=due_on,
        uncertainty_flags=(BUSINESS_DAY_CALENDAR_ASSUMED,),
    )
