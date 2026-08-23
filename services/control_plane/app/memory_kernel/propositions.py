"""Propositions: the normal form the matcher compares, and the two entailments.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.2 owns :class:`Proposition` and
  the six normalisation steps.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.3 owns entailment. There are
  exactly two rules, ``EN-1`` and ``EN-2``, they fire once, and an entailed
  proposition never feeds another rule (section 2.10 item 5: no fixpoint loop,
  no transitive closure).
- ``specs/12_KERNEL_ALGORITHMS.md`` section 2.4 owns the day-boundary
  convention and validity-interval normalisation.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 8.2 owns ``validity_basis`` and
  rule ``T2``.

The rule the demo rests on
--------------------------
An invoice for June does not literally say "service was active in June".
``EN-1`` entails it, and the entailed proposition pays a fixed 0.30 penalty.
That penalty is larger than ``auto_resolve_margin`` (0.25), which is precisely
what makes a *direct* statement about service status beat an *implied* one
deterministically, in both ingestion orders. Without ``EN-1`` the hero
scenario produces no contradiction at all.

Why an entailed proposition is never persisted as a claim
----------------------------------------------------------
Invariant 1: ``claims`` records what actors actually asserted. The ISP asserted
an amount and a service period; it did not assert "the service was active". The
entailment's durable trace is the ``belief_support`` edge whose ``source_id`` is
the parent claim and whose ``reason_code`` is ``EN-1``.
:func:`is_persistable_claim` is the predicate the write path filters on.

Recorded discrepancy
--------------------
Section 2.3 writes that grounding edge's reason code as the literal ``'EN-1'``,
which does not satisfy ``provenance_contracts.base.ReasonCode``'s
``^[A-Z][A-Z0-9_]{2,63}$``. The hyphen is the problem. The spelling is kept
here because it is the spec's and because the edge is kernel-authored rather
than agent-supplied, so it never crosses that boundary validator; the clash is
reported rather than resolved by inventing a code the spec does not name.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from provenance_domain.authority import AUTHORITY_EXPONENT, ENTAILMENT_PENALTY
from provenance_domain.enums import ClaimKind, EpistemicStatus, KernelReasonCode, SubjectType
from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    KernelConfig,
)
from services.control_plane.app.memory_kernel.families import (
    CommitmentStatusValue,
    Family,
    FamilyValue,
    OutstandingValue,
    PaymentValue,
    ServiceState,
    ServiceStatusValue,
    ValueCoercionError,
    authority_for,
    coerce_value,
    family_of,
    is_known_source_class,
)

__all__ = [
    "EN_1",
    "EN_2",
    "ENTAILED_FULL_SETTLEMENT",
    "ENTAILMENT_RULES",
    "UTC_TZ",
    "CommitmentFacts",
    "NormalizationResult",
    "Proposition",
    "PropositionSourceKind",
    "ValidityBasis",
    # Re-exported so a caller of `normalize_claim` can catch the coercion
    # failure without also importing `families`.
    "ValueCoercionError",
    "day_start_utc",
    "entail",
    "exclusive_day_end_utc",
    "is_persistable_claim",
    "normalize_claim",
    "normalize_effective_from",
    "normalize_service_period",
    "normalize_terminated_on",
    "validate_validity",
]

#: ``specs/12_KERNEL_ALGORITHMS.md`` section 2.3. The literal spellings are the
#: ones written into ``belief_support.reason_code``.
EN_1: Final[str] = "EN-1"
EN_2: Final[str] = "EN-2"
ENTAILMENT_RULES: Final[tuple[str, str]] = (EN_1, EN_2)

#: The identity key section 2.3 gives the synthetic ``EN-2`` payment.
ENTAILED_FULL_SETTLEMENT: Final[str] = "ENTAILED_FULL_SETTLEMENT"

#: Every instant this module produces is UTC. ``zoneinfo`` rather than
#: ``datetime.UTC`` so the returned ``tzinfo`` compares equal to the one the
#: database driver hands back.
UTC_TZ: Final[tzinfo] = ZoneInfo("UTC")

_ONE_DAY: Final[timedelta] = timedelta(days=1)


class ValidityBasis(StrEnum):
    """Section 8.2's three values, and the whole of rule ``T2``.

    ``UNKNOWN`` does not participate in interval-overlap contradiction
    detection at all. "We processed your refund" with no date is not evidence
    that the refund happened during any particular window, and treating it as
    ``[-inf, +inf)`` would make it conflict with everything. This single rule
    eliminates the largest source of false-positive conflicts.
    """

    EXPLICIT = "EXPLICIT"
    EXPLICIT_OPEN = "EXPLICIT_OPEN"
    UNKNOWN = "UNKNOWN"


class PropositionSourceKind(StrEnum):
    """Where a proposition was read from. Mirrors section 2.2's ``source_kind``."""

    CLAIM = "CLAIM"
    BELIEF_VERSION = "BELIEF_VERSION"
    DERIVATION = "DERIVATION"


@dataclass(frozen=True, slots=True)
class CommitmentFacts:
    """The commitment columns ``EN-2`` needs, and nothing else."""

    commitment_id: uuid.UUID
    currency: str
    committed_amount: Decimal


@dataclass(frozen=True, slots=True)
class Proposition:
    """One comparable statement, normalised.

    ``authority`` is a property rather than a stored column because the
    entailment penalty is part of the arithmetic, not part of the source: the
    same claim read directly and read through ``EN-1`` must not be able to
    disagree about what it is worth.
    """

    prop_id: uuid.UUID
    source_kind: PropositionSourceKind
    subject_type: SubjectType
    subject_id: uuid.UUID
    family: Family
    predicate: str
    value: FamilyValue
    valid_from: datetime | None
    valid_to: datetime | None
    validity_basis: ValidityBasis
    base_authority: Decimal
    recorded_at: datetime
    source_class: str | None = None
    source_claim_kind: ClaimKind | None = None
    entailed_from: uuid.UUID | None = None
    entailment_rule: str | None = None
    actor_ref: str | None = None
    is_incumbent: bool = False
    epistemic_status: EpistemicStatus | None = None
    belief_confidence: Decimal | None = None
    service_period: tuple[datetime, datetime] | None = None
    entailment_penalty: Decimal = field(default=ENTAILMENT_PENALTY)

    @property
    def authority(self) -> Decimal:
        """Section 2.2: the grid score, less the penalty, floored at zero."""
        if self.entailed_from is None:
            return self.base_authority.quantize(AUTHORITY_EXPONENT)
        penalised = self.base_authority - self.entailment_penalty
        return max(Decimal("0"), penalised).quantize(AUTHORITY_EXPONENT)


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """What step 10 produced for one proposed claim.

    ``proposition`` is ``None`` when the surface predicate maps to no v1 family:
    the claim is still admitted and may still ground a belief, it simply never
    reaches the matcher (section 2.1).
    """

    family: Family
    proposition: Proposition | None
    reason_codes: tuple[KernelReasonCode, ...] = ()


# --- section 2.4, the day-boundary convention --------------------------------


def day_start_utc(day: date, tz: tzinfo) -> datetime:
    """``day 00:00:00`` in *tz*, as UTC. The inclusive lower bound.

    The offset is resolved per date rather than assumed, because New York is
    UTC-5 in January and UTC-4 in June and a hard-coded offset is wrong for
    half the year in a way that reads as a one-hour parsing artifact.
    """
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC_TZ)


def exclusive_day_end_utc(day: date, tz: tzinfo) -> datetime:
    """``(day + 1) 00:00:00`` in *tz*, as UTC. The exclusive upper bound."""
    return day_start_utc(day + _ONE_DAY, tz)


def normalize_effective_from(day: date, tz: tzinfo) -> datetime:
    """ "effective / starting / from *day*" -> inclusive lower bound."""
    return day_start_utc(day, tz)


def normalize_terminated_on(day: date, tz: tzinfo) -> datetime:
    """ "terminated / ends / through / until *day*" -> exclusive upper bound.

    "service terminated 31 May 2026" in ``America/New_York`` therefore begins
    the ``TERMINATED`` state at ``2026-06-01T04:00:00Z``. Get this wrong by one
    day and the hero scenario produces a ``TEMPORAL_CONFLICT`` where it should
    produce a ``VALUE_CONFLICT``.
    """
    return exclusive_day_end_utc(day, tz)


def normalize_service_period(start: date, end: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """ "service period *start* to *end*" -> ``[start 00:00, (end+1) 00:00)``."""
    return day_start_utc(start, tz), exclusive_day_end_utc(end, tz)


def validate_validity(
    valid_from: datetime | None,
    valid_to: datetime | None,
    now: datetime,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> tuple[KernelReasonCode, ...]:
    """Pipeline step 9. Empty tuple means the interval is applicable.

    ``VALIDITY_INVERTED`` when ``valid_to <= valid_from`` - a half-open
    ``[a, a)`` is empty and states nothing - and
    ``VALIDITY_FUTURE_BEYOND_HORIZON`` when a bound is more than
    ``future_validity_horizon_days`` ahead, which is almost always a parse
    error rather than a ten-year promise.
    """
    codes: list[KernelReasonCode] = []
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        codes.append(KernelReasonCode.VALIDITY_INVERTED)
    horizon = now + timedelta(days=cfg.future_validity_horizon_days)
    if any(bound is not None and bound > horizon for bound in (valid_from, valid_to)):
        codes.append(KernelReasonCode.VALIDITY_FUTURE_BEYOND_HORIZON)
    return tuple(codes)


# --- section 2.2, normalisation ----------------------------------------------


def normalize_claim(
    *,
    prop_id: uuid.UUID,
    subject_type: SubjectType,
    subject_id: uuid.UUID,
    predicate: str,
    raw_value: object,
    source_class: str,
    claim_kind: ClaimKind | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    validity_basis: ValidityBasis = ValidityBasis.UNKNOWN,
    recorded_at: datetime,
    actor_ref: str | None = None,
    service_period: tuple[datetime, datetime] | None = None,
    source_kind: PropositionSourceKind = PropositionSourceKind.CLAIM,
    is_incumbent: bool = False,
    epistemic_status: EpistemicStatus | None = None,
    belief_confidence: Decimal | None = None,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> NormalizationResult:
    """The six steps of section 2.2, in order.

    Rule N2 is enforced structurally: there is no ``authority_score``
    parameter. A model-proposed score has nowhere to enter, and
    ``source_class`` is used only as a key into the frozen grid.

    Raises:
        ValueCoercionError: the value does not fit the family's schema. That is
            a ``REJECTED_SCHEMA`` outcome upstream, not a coercion to something
            plausible.
    """
    # Step 1 - map the surface predicate to a family, or stop.
    family = family_of(predicate)
    if family is Family.UNMAPPED:
        return NormalizationResult(family=family, proposition=None)

    # Steps 2 and 3 - coerce the value, normalising money exactly.
    value = coerce_value(predicate, raw_value)

    # Steps 4 and 5 - the interval and its basis arrive already normalised by
    # the day-boundary helpers above; the Kernel never invents either (rule T2).
    reasons: list[KernelReasonCode] = []
    if validity_basis is ValidityBasis.UNKNOWN:
        valid_from = None
        valid_to = None
        reasons.append(KernelReasonCode.VALIDITY_UNKNOWN_NOT_COMPARABLE)

    # Step 6 - look up the base authority from the frozen grid.
    if not is_known_source_class(source_class):
        reasons.append(KernelReasonCode.AUTHORITY_UNMAPPED_SOURCE_CLASS)
    base_authority = authority_for(family, source_class, cfg)

    proposition = Proposition(
        prop_id=prop_id,
        source_kind=source_kind,
        subject_type=subject_type,
        subject_id=subject_id,
        family=family,
        predicate=predicate,
        value=value,
        valid_from=valid_from,
        valid_to=valid_to,
        validity_basis=validity_basis,
        base_authority=base_authority,
        recorded_at=recorded_at,
        source_class=source_class,
        source_claim_kind=claim_kind,
        actor_ref=actor_ref,
        is_incumbent=is_incumbent,
        epistemic_status=epistemic_status,
        belief_confidence=belief_confidence,
        service_period=service_period,
        entailment_penalty=cfg.entailment_penalty,
    )
    return NormalizationResult(family=family, proposition=proposition, reason_codes=tuple(reasons))


def is_persistable_claim(p: Proposition) -> bool:
    """False for an entailed proposition. Invariant 1, as a filter."""
    return p.entailed_from is None


# --- section 2.3, entailment -------------------------------------------------


def _entailed(
    parent: Proposition,
    *,
    rule: str,
    family: Family,
    predicate: str,
    value: FamilyValue,
    valid_from: datetime | None,
    valid_to: datetime | None,
    validity_basis: ValidityBasis,
    subject_type: SubjectType | None = None,
    subject_id: uuid.UUID | None = None,
    cfg: KernelConfig,
) -> Proposition:
    """One entailed proposition, carrying its parent's identity and penalty.

    ``prop_id`` is the **parent's** claim id, because the entailment's only
    durable trace is a ``belief_support`` edge pointing at that claim. Minting a
    fresh id here would produce an edge referencing a row that does not exist.

    The base authority is re-looked-up for the *entailed* family rather than
    inherited numerically: section 1.6 reads 0.88 for
    ``(SERVICE_STATUS, PROVIDER_SYSTEM_NOTICE)`` even though the parent
    ``BALANCE`` claim scored 0.90, and the grid is the reason the two differ.
    """
    return Proposition(
        prop_id=parent.prop_id,
        source_kind=parent.source_kind,
        subject_type=parent.subject_type if subject_type is None else subject_type,
        subject_id=parent.subject_id if subject_id is None else subject_id,
        family=family,
        predicate=predicate,
        value=value,
        valid_from=valid_from,
        valid_to=valid_to,
        validity_basis=validity_basis,
        base_authority=authority_for(family, parent.source_class or "", cfg),
        recorded_at=parent.recorded_at,
        source_class=parent.source_class,
        source_claim_kind=parent.source_claim_kind,
        entailed_from=parent.prop_id,
        entailment_rule=rule,
        actor_ref=parent.actor_ref,
        is_incumbent=parent.is_incumbent,
        entailment_penalty=cfg.entailment_penalty,
    )


def entail(
    parent: Proposition,
    *,
    commitments: Mapping[uuid.UUID, CommitmentFacts] | None = None,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> tuple[Proposition, ...]:
    """Apply ``EN-1`` and ``EN-2`` to *parent*. Never recursive.

    An already-entailed proposition entails nothing: section 2.10 item 5 puts
    multi-hop entailment out of scope, and a rule set with no termination bound
    is a rule set that cannot be tested.
    """
    if parent.entailed_from is not None:
        return ()

    if parent.family is Family.BALANCE:
        return _entail_en1(parent, cfg)
    if parent.family is Family.OUTSTANDING:
        return _entail_en2(parent, commitments or {}, cfg)
    return ()


def _entail_en1(parent: Proposition, cfg: KernelConfig) -> tuple[Proposition, ...]:
    """A billed service period entails that the service was supplied.

    The obligor is billing for a window; billing entails supply. Without this
    rule the ISP invoice never contradicts the termination belief.
    """
    period = parent.service_period
    if period is None:
        return ()
    lo, hi = period
    if hi <= lo:
        return ()
    return (
        _entailed(
            parent,
            rule=EN_1,
            family=Family.SERVICE_STATUS,
            predicate="service_active",
            value=ServiceStatusValue(ServiceState.ACTIVE),
            valid_from=lo,
            valid_to=hi,
            validity_basis=ValidityBasis.EXPLICIT,
            cfg=cfg,
        ),
    )


def _entail_en2(
    parent: Proposition,
    commitments: Mapping[uuid.UUID, CommitmentFacts],
    cfg: KernelConfig,
) -> tuple[Proposition, ...]:
    """ "Your deposit was fully returned" must be able to hit the ledger.

    An ``OUTSTANDING`` proposition asserting zero entails both that the
    commitment stands and that a full settlement was paid, so the claim
    collides with ``fulfillments`` instead of quietly agreeing with nothing.
    """
    value = parent.value
    if not isinstance(value, OutstandingValue) or value.amount != Decimal(0):
        return ()
    facts = commitments.get(value.commitment_id)
    if facts is None:
        # The synthetic payment's amount is the *committed* amount. With no
        # commitment row there is no amount, and inventing one would be a
        # monetary guess.
        return ()
    paid_at = parent.valid_from or parent.recorded_at
    return (
        _entailed(
            parent,
            rule=EN_2,
            family=Family.COMMITMENT_STATUS,
            predicate="commitment_withdrawn",
            value=CommitmentStatusValue(False, facts.commitment_id),
            valid_from=parent.valid_from,
            valid_to=parent.valid_to,
            validity_basis=parent.validity_basis,
            subject_type=SubjectType.COMMITMENT,
            subject_id=facts.commitment_id,
            cfg=cfg,
        ),
        _entailed(
            parent,
            rule=EN_2,
            family=Family.PAYMENT,
            predicate="payment_received",
            value=PaymentValue(
                currency=facts.currency,
                amount=facts.committed_amount,
                paid_at=paid_at,
                external_ref=ENTAILED_FULL_SETTLEMENT,
                asserted=True,
            ),
            valid_from=paid_at,
            valid_to=paid_at,
            validity_basis=parent.validity_basis,
            subject_type=SubjectType.COMMITMENT,
            subject_id=facts.commitment_id,
            cfg=cfg,
        ),
    )
