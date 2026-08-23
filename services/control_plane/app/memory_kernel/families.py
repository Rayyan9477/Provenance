"""The closed predicate-family registry and its value schemas.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.1 owns the table: five families,
  their canonical predicates, the surface predicates accepted from proposals,
  the legal belief subject, and the value schema of each.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.2 steps 1-3 own coercion.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 3.2 owns the authority grid, which
  is **not** re-typed here: it is read from
  :data:`provenance_domain.authority.AUTHORITY_SCORES`, so the grid has one
  definition.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.7: ``PAYMENT`` propositions never
  create a belief row.

Why the registry is closed
--------------------------
"Values are mutually exclusive" is not implementable. A generic semantic
contradiction detector cannot be tested and cannot be explained in State Proof,
so v1 recognises exactly five families and a predicate outside them is admitted
as a claim, may ground a belief, and never produces a ``conflicts`` row
(section 2.1, and risk R1). Honest silence beats invented contradictions.

Recorded discrepancy
--------------------
``provenance_domain.authority.PREDICATE_FAMILIES`` knows eight surface
predicates; section 2.1's table names sixteen, and the two sets are not nested
in either direction. Three names appear only in ``authority``
(``billing_period_covered``, ``amount_outstanding``, ``commitment_status``) and
eleven appear only in section 2.1. :data:`SURFACE_PREDICATES` is their union,
so no predicate either document recognises is silently dropped, and
:func:`family_of` and ``authority.predicate_family`` agree on every name they
both know. That is asserted, not assumed, in ``test_families.py``. The
underlying gap is reported as a defect against ``provenance_domain.authority``
rather than repaired here: this package does not own that module.

Consequence for authority lookup: :func:`authority_for` is keyed by **family**,
not by surface predicate. Calling ``authority.authority_for('amount_due', ...)``
would silently return the 0.10 unmapped floor for a first-class ``BALANCE``
predicate, quietly demoting every invoice that used that spelling.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from provenance_domain import authority as domain_authority
from provenance_domain import money
from provenance_domain.enums import KernelReasonCode, SourceClass, SubjectType
from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    KernelConfig,
)

__all__ = [
    "BELIEF_SUBJECT_TYPES",
    "CANONICAL_PREDICATE",
    "ASSERTED_PAYMENT_PREDICATES",
    "MONETARY_FAMILIES",
    "PAYMENT_DENIAL_PREDICATES",
    "SURFACE_PREDICATES",
    "BalanceValue",
    "CommitmentStatusValue",
    "Family",
    "FamilyValue",
    "OutstandingValue",
    "PaymentValue",
    "ServiceState",
    "ServiceStatusValue",
    "ValueCoercionError",
    "authority_for",
    "canonical_predicate",
    "coerce_value",
    "family_of",
    "is_known_source_class",
    "is_monetary",
    "normalize_currency",
    "normalize_money",
    "produces_belief",
    "valid_subject_type",
]


class Family(StrEnum):
    """The five v1 predicate families, plus the total function's escape hatch.

    ``UNMAPPED`` is a member rather than a ``None`` return so that family
    assignment is total and every caller has to say what it does with the
    unmapped case. ``EXECUTION/70_TASK_PLAN.md`` T4.3 calls this outcome
    ``UNCLASSIFIED``; ``specs/12_KERNEL_ALGORITHMS.md`` section 2.2 step 1 and
    ``provenance_domain.authority.UNMAPPED_FAMILY`` both spell it ``UNMAPPED``,
    and the spec outranks the plan.
    """

    SERVICE_STATUS = "SERVICE_STATUS"
    BALANCE = "BALANCE"
    PAYMENT = "PAYMENT"
    OUTSTANDING = "OUTSTANDING"
    COMMITMENT_STATUS = "COMMITMENT_STATUS"
    UNMAPPED = "UNMAPPED"


class ServiceState(StrEnum):
    """The two-valued service state of section 2.1's ``SERVICE_STATUS`` schema."""

    ACTIVE = "ACTIVE"
    TERMINATED = "TERMINATED"


#: Families whose conflicts carry a monetary exposure and therefore reach gate
#: ``H5``. ``specs/12_KERNEL_ALGORITHMS.md`` section 3.3 ``MONETARY_FAMILIES``.
MONETARY_FAMILIES: Final[frozenset[Family]] = frozenset(
    {Family.BALANCE, Family.PAYMENT, Family.OUTSTANDING}
)

#: ``beliefs.predicate`` always stores the family's canonical predicate
#: (Rule N1). ``service_terminated`` and ``service_active`` are two surface
#: forms of one belief; without this rule the
#: ``UNIQUE (tenant_id, user_id, subject_type, subject_id, predicate)``
#: constraint would let two mutually exclusive beliefs coexist as separate rows
#: and the whole contradiction model would silently no-op.
CANONICAL_PREDICATE: Final[Mapping[Family, str]] = MappingProxyType(
    {
        Family.SERVICE_STATUS: "service_active",
        Family.BALANCE: "balance_owed",
        Family.PAYMENT: "payment_received",
        Family.OUTSTANDING: "deposit_outstanding",
        Family.COMMITMENT_STATUS: "commitment_withdrawn",
    }
)

#: Surface predicate -> family: section 2.1's table, unioned with the three
#: names ``provenance_domain.authority.PREDICATE_FAMILIES`` carries that the
#: table omits. See the module docstring for why the union rather than either
#: set alone.
SURFACE_PREDICATES: Final[Mapping[str, Family]] = MappingProxyType(
    {
        # SERVICE_STATUS
        "service_active": Family.SERVICE_STATUS,
        "service_terminated": Family.SERVICE_STATUS,
        "service_cancelled": Family.SERVICE_STATUS,
        "service_suspended": Family.SERVICE_STATUS,
        "billing_period_covered": Family.SERVICE_STATUS,  # authority.py only
        # BALANCE
        "balance_owed": Family.BALANCE,
        "amount_due": Family.BALANCE,
        "invoice_total": Family.BALANCE,
        # PAYMENT
        "payment_received": Family.PAYMENT,
        "payment_sent": Family.PAYMENT,
        "payment_not_received": Family.PAYMENT,
        # OUTSTANDING
        "deposit_outstanding": Family.OUTSTANDING,
        "refund_outstanding": Family.OUTSTANDING,
        "reimbursement_outstanding": Family.OUTSTANDING,
        "amount_outstanding": Family.OUTSTANDING,  # authority.py only
        # COMMITMENT_STATUS
        "commitment_withdrawn": Family.COMMITMENT_STATUS,
        "commitment_revoked": Family.COMMITMENT_STATUS,
        "promise_retracted": Family.COMMITMENT_STATUS,
        "commitment_status": Family.COMMITMENT_STATUS,  # authority.py only
    }
)

#: The subject types a family's belief may hang off. ``PAYMENT`` has no belief
#: row at all (section 2.7), so it maps to the empty tuple: Rule N1 would
#: otherwise collapse every payment for a subject into one belief.
BELIEF_SUBJECT_TYPES: Final[Mapping[Family, tuple[SubjectType, ...]]] = MappingProxyType(
    {
        Family.SERVICE_STATUS: (SubjectType.RELATIONSHIP,),
        Family.BALANCE: (SubjectType.RELATIONSHIP, SubjectType.CASE),
        Family.PAYMENT: (),
        Family.OUTSTANDING: (SubjectType.COMMITMENT,),
        Family.COMMITMENT_STATUS: (SubjectType.COMMITMENT,),
        Family.UNMAPPED: (),
    }
)

#: Surface predicates whose truthy reading is ``ACTIVE``. Every other
#: ``SERVICE_STATUS`` surface form reads ``TERMINATED`` when true.
_ACTIVE_WHEN_TRUE: Final[frozenset[str]] = frozenset({"service_active", "billing_period_covered"})


class ValueCoercionError(ValueError):
    """A proposed value did not fit its family's schema.

    Carries a :class:`~provenance_domain.enums.KernelReasonCode` so the caller
    maps it onto a decision without parsing an English message.
    """

    def __init__(self, code: KernelReasonCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


@dataclass(frozen=True, slots=True)
class ServiceStatusValue:
    """The ``SERVICE_STATUS`` value schema: one two-valued state."""

    state: ServiceState


@dataclass(frozen=True, slots=True)
class BalanceValue:
    """The ``BALANCE`` value schema: an exact amount in one currency."""

    currency: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class PaymentValue:
    """The ``PAYMENT`` value schema, including the denial flag rule M5 needs."""

    currency: str
    amount: Decimal
    paid_at: datetime
    external_ref: str | None = None
    asserted: bool = True


@dataclass(frozen=True, slots=True)
class OutstandingValue:
    """The ``OUTSTANDING`` value schema, keyed to its commitment."""

    currency: str
    amount: Decimal
    commitment_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class CommitmentStatusValue:
    """The ``COMMITMENT_STATUS`` value schema: withdrawn, or not."""

    withdrawn: bool
    commitment_id: uuid.UUID


FamilyValue = (
    ServiceStatusValue | BalanceValue | PaymentValue | OutstandingValue | CommitmentStatusValue
)


#: The ``PAYMENT`` surface predicates whose polarity is negative. One member
#: today, named rather than spelled inline because two places read it: the
#: coercion below, and ``transaction._READ_LEDGER_SQL``, which must not bind a
#: denial as the *grounding* of the very fulfillment it denies.
PAYMENT_DENIAL_PREDICATES: Final[frozenset[str]] = frozenset({"payment_not_received"})

#: The ``PAYMENT`` surface predicates that assert a payment happened.
#:
#: A ``fulfillments`` row is grounded on one of these and never on a denial. No
#: column links a fulfillment to the claim that produced it, so this tuple is
#: what lets the ledger read resolve the grounding claim unambiguously: without
#: it, a denial sharing the evidence and subject of the payment it denies can be
#: selected as that payment's own grounding, and the authority margin is then
#: measured between a claim and itself.
ASSERTED_PAYMENT_PREDICATES: Final[tuple[str, ...]] = tuple(
    sorted(
        predicate
        for predicate, family in SURFACE_PREDICATES.items()
        if family is Family.PAYMENT and predicate not in PAYMENT_DENIAL_PREDICATES
    )
)


def family_of(predicate: str) -> Family:
    """The family of *predicate*, or :attr:`Family.UNMAPPED`. Total."""
    return SURFACE_PREDICATES.get(predicate, Family.UNMAPPED)


def canonical_predicate(family: Family) -> str:
    """The predicate stored in ``beliefs.predicate`` for *family* (Rule N1)."""
    return CANONICAL_PREDICATE.get(family, "")


def is_monetary(family: Family) -> bool:
    """True when a conflict in *family* carries a monetary exposure."""
    return family in MONETARY_FAMILIES


def produces_belief(family: Family) -> bool:
    """False for ``PAYMENT`` and ``UNMAPPED``; True for the other four."""
    return bool(BELIEF_SUBJECT_TYPES.get(family, ()))


def valid_subject_type(family: Family, subject_type: SubjectType) -> bool:
    """Whether *family* may hang a belief off *subject_type*."""
    return subject_type in BELIEF_SUBJECT_TYPES.get(family, ())


def is_known_source_class(source_class: SourceClass | str) -> bool:
    """Whether *source_class* is a member of the closed enum."""
    if isinstance(source_class, SourceClass):
        return True
    return source_class in {member.value for member in SourceClass}


def authority_for(
    family: Family,
    source_class: SourceClass | str,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> Decimal:
    """The frozen ``(family, source_class)`` score from section 3.2's grid.

    Keyed by family rather than by surface predicate, for the reason given in
    the module docstring. An unmapped family or an unknown source class falls
    to ``cfg.unknown_source_class_authority``; failing low is deliberate, since
    an unrecognised source must not be able to displace canonical state.
    """
    floor = cfg.unknown_source_class_authority.quantize(domain_authority.AUTHORITY_EXPONENT)
    if family is Family.UNMAPPED:
        return floor
    try:
        resolved = SourceClass(source_class)
    except ValueError:
        return floor
    row = _GRID.get(resolved)
    if row is None:
        return floor
    return row.get(family.value, floor).quantize(domain_authority.AUTHORITY_EXPONENT)


def normalize_currency(currency: object) -> str:
    """An ISO-style 3-character uppercase code, or a refusal.

    Lowercase is rejected rather than upper-cased: ``provenance_contracts``
    constrains ``CurrencyCode`` to ``^[A-Z]{3}$`` at every boundary, so a
    lowercase code has already escaped a check that should have caught it, and
    silently repairing it here would hide that. Delegates to
    ``provenance_domain.money`` so the rule has one implementation.
    """
    try:
        return money._validated_currency(currency)
    except money.MoneyError as exc:
        raise ValueCoercionError(KernelReasonCode.SCHEMA_TYPE_INVALID, exc.detail) from exc


def normalize_money(amount: object) -> Decimal:
    """An exact ``DECIMAL(20,4)`` amount, or a refusal. Never a ``float``."""
    try:
        return money._validated_amount(amount)
    except money.MoneyError as exc:
        raise ValueCoercionError(KernelReasonCode.SCHEMA_TYPE_INVALID, exc.detail) from exc


def _field(raw: object, name: str) -> object:
    """Read *name* off a mapping or an object, without guessing at either."""
    if isinstance(raw, Mapping):
        return raw.get(name)
    return getattr(raw, name, None)


def _require_uuid(raw: object, name: str) -> uuid.UUID:
    value = _field(raw, name)
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise ValueCoercionError(
                KernelReasonCode.SCHEMA_TYPE_INVALID, f"{name}={value!r} is not a UUID"
            ) from exc
    raise ValueCoercionError(
        KernelReasonCode.SCHEMA_FIELD_MISSING, f"{name} is required for this family"
    )


def _require_datetime(raw: object, name: str) -> datetime:
    """*name* as a timezone-aware instant, from a ``datetime`` or ISO-8601 text.

    Text is accepted for the same reason :func:`_require_uuid` accepts it, and
    it is not a convenience. ``claims.object_json`` and
    ``belief_versions.value_json`` are JSONB columns, so every ``PAYMENT`` value
    the Kernel reads back from persisted state carries ``paid_at`` as a string;
    and ``ProposedClaim.object_value`` is typed ``JsonValue``, so an inbound
    proposal cannot carry a ``datetime`` object even in principle. Accepting
    only ``datetime`` therefore made the whole family unreachable from both
    directions -- observed against ``provenance_ci`` as ``SCHEMA_FIELD_MISSING:
    paid_at is required for this family`` on a proposal that supplied
    ``paid_at``. Every unit fixture had built :class:`PaymentValue` directly
    instead of coercing one, so nothing in the hermetic lane could see it.

    A naive value is refused rather than assumed to be UTC: section 8.1, the
    Kernel never invents a timezone. Section 2.7 buckets payments by
    ``(paid_at - EPOCH) // payment_match_window_days``, so an assumed timezone
    can move a payment into a different identity bucket and stop it matching
    the commitment it settles.

    ``SCHEMA_TYPE_INVALID`` separates "you gave me the wrong thing" from
    ``SCHEMA_FIELD_MISSING``'s "you gave me nothing"; collapsing the two would
    report a malformed timestamp as an absent one.
    """
    value = _field(raw, name)
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueCoercionError(
                KernelReasonCode.SCHEMA_TYPE_INVALID,
                f"{name}={value!r} is not an ISO-8601 instant",
            ) from exc
    else:
        raise ValueCoercionError(
            KernelReasonCode.SCHEMA_FIELD_MISSING, f"{name} is required for this family"
        )
    if moment.tzinfo is None:
        raise ValueCoercionError(
            KernelReasonCode.SCHEMA_TYPE_INVALID,
            f"{name} carries no timezone; the Kernel never assumes one (section 8.1)",
        )
    return moment


def _coerce_service_status(predicate: str, raw: object) -> ServiceStatusValue:
    stated = _field(raw, "state")
    if stated is not None:
        try:
            return ServiceStatusValue(ServiceState(str(stated).upper()))
        except ValueError as exc:
            raise ValueCoercionError(
                KernelReasonCode.SCHEMA_TYPE_INVALID,
                f"{stated!r} is not ACTIVE or TERMINATED",
            ) from exc
    if not isinstance(raw, bool):
        raise ValueCoercionError(
            KernelReasonCode.SCHEMA_TYPE_INVALID,
            f"{raw!r} is neither a service state nor a boolean assertion",
        )
    active = raw if predicate in _ACTIVE_WHEN_TRUE else not raw
    return ServiceStatusValue(ServiceState.ACTIVE if active else ServiceState.TERMINATED)


def _coerce_money_pair(raw: object) -> tuple[str, Decimal]:
    return normalize_currency(_field(raw, "currency")), normalize_money(_field(raw, "amount"))


def coerce_value(predicate: str, raw: object) -> FamilyValue:
    """Coerce *raw* into the value schema of *predicate*'s family.

    Section 2.2 step 2 in code: ``service_terminated: true`` becomes
    ``{"state":"TERMINATED"}``, ``service_active: false`` becomes the same
    thing, and ``payment_not_received`` becomes ``PaymentValue(asserted=False)``.
    Polarity is a property of the surface predicate, which is exactly why
    Rule N1 can collapse all of them onto one belief.
    """
    family = family_of(predicate)
    if family is Family.UNMAPPED:
        raise ValueCoercionError(
            KernelReasonCode.SCHEMA_TYPE_INVALID,
            f"{predicate!r} belongs to no v1 predicate family, so it has no value schema",
        )
    if family is Family.SERVICE_STATUS:
        return _coerce_service_status(predicate, raw)
    if family is Family.BALANCE:
        currency, amount = _coerce_money_pair(raw)
        return BalanceValue(currency, amount)
    if family is Family.PAYMENT:
        currency, amount = _coerce_money_pair(raw)
        asserted_raw = _field(raw, "asserted")
        asserted = (
            predicate not in PAYMENT_DENIAL_PREDICATES
            if asserted_raw is None
            else bool(asserted_raw)
        )
        external = _field(raw, "external_ref")
        return PaymentValue(
            currency=currency,
            amount=amount,
            paid_at=_require_datetime(raw, "paid_at"),
            external_ref=str(external) if external is not None else None,
            asserted=asserted,
        )
    if family is Family.OUTSTANDING:
        currency, amount = _coerce_money_pair(raw)
        return OutstandingValue(currency, amount, _require_uuid(raw, "commitment_id"))
    withdrawn_raw = _field(raw, "withdrawn")
    withdrawn = True if withdrawn_raw is None else bool(withdrawn_raw)
    return CommitmentStatusValue(withdrawn, _require_uuid(raw, "commitment_id"))


# The grid this module reads from, bound once so `authority_for` resolves
# through the domain package rather than through a second copy of sixty numbers.
_GRID: Final[Mapping[SourceClass, Mapping[str, Decimal]]] = domain_authority.AUTHORITY_SCORES
