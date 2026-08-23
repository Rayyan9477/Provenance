"""The monetary commitment algorithm: recompute, never increment.

Authority
---------
- ``specs/12_KERNEL_ALGORITHMS.md`` section 4.1: the projection is recomputed
  from the fulfillment ledger by aggregation inside the transaction. Implement
  ``new_fulfilled = old_fulfilled + F`` instead and the first duplicate
  delivery double-counts a payment.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 4.2: :func:`apply_fulfillment`,
  step for step.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 4.3: over-fulfilment is flagged,
  never silently clamped.
- ``specs/12_KERNEL_ALGORITHMS.md`` section 4.4: :func:`commitment_status`,
  evaluated in that exact order, first match wins.
- ``specs/11_CONTRACTS.md`` section 5.1 and
  ``provenance_domain.money.outstanding``: the identity never clamps.
- ``specs/10_DATABASE_DDL.md`` section 7.2 checks M1-M8, which this module must
  never be able to breach.

Why the arithmetic is reached through the module global
--------------------------------------------------------
``provenance_domain.money.outstanding`` is the symbol the ``PV_SABOTAGE``
matrix neuters. It is called as ``money.outstanding(...)`` through the module
object, never imported by name: a ``from``-import copies the reference at
import time, so the sabotage would rebind a symbol nothing reads and the probe
would report green while the identity was gone.

Two outstanding numbers, both true
----------------------------------
:attr:`CommitmentDelta.signed_outstanding` is the raw identity and goes
**negative** on over-fulfilment. :attr:`CommitmentDelta.outstanding_after` is
the projection the schema can store, capped at zero, and it is only ever
written alongside a ``FULFILLMENT_CONFLICT``, a ``DISPUTED`` status, a state
transition and an outbox event. That is the difference between capping and
silently clamping: nothing here is silent, and ``excess`` stays recomputable
from the ledger forever.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from provenance_domain import money
from provenance_domain.enums import (
    CommitmentStatus,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    FulfillmentAdmissionStatus,
    KernelReasonCode,
)
from services.control_plane.app.memory_kernel.config import (
    DEFAULT_KERNEL_CONFIG,
    KernelConfig,
)

__all__ = [
    "BLOCKING_CONFLICT_TYPES",
    "LIVE_CONFLICT_STATUSES",
    "CommitmentDelta",
    "CommitmentRow",
    "ConflictWrite",
    "Fulfillment",
    "FulfillmentRow",
    "FulfillmentWrite",
    "NewCommitment",
    "ProposedFulfillment",
    "admitted_total",
    "apply_fulfillment",
    "commitment_status",
    "open_commitment",
    "recompute",
]

#: Section 4.4: a live conflict of one of these types makes the commitment
#: ``DISPUTED`` regardless of the arithmetic. Dispute dominates - if the
#: counterparty says "paid in full" and the ledger says otherwise, the
#: commitment is disputed, not fulfilled.
BLOCKING_CONFLICT_TYPES: Final[frozenset[ConflictType]] = frozenset(
    {ConflictType.FULFILLMENT_CONFLICT, ConflictType.COMMITMENT_WITHDRAWAL_CONFLICT}
)

#: Section 4.4: the conflict statuses that count as live. A settled dispute
#: that kept blocking would leave the commitment ``DISPUTED`` forever.
LIVE_CONFLICT_STATUSES: Final[frozenset[ConflictStatus]] = frozenset(
    {ConflictStatus.OPEN, ConflictStatus.NEEDS_HUMAN}
)

_ZERO: Final[Decimal] = Decimal("0.0000")


@dataclass(frozen=True, slots=True)
class CommitmentRow:
    """The commitment columns the algorithm reads."""

    commitment_id: uuid.UUID
    case_id: uuid.UUID
    status: CommitmentStatus
    currency: str | None = None
    committed_amount: Decimal | None = None
    fulfilled_amount: Decimal | None = None
    outstanding_amount: Decimal | None = None
    revision: int = 0
    due_at: datetime | None = None
    valid_to: datetime | None = None
    #: ``condition_ast`` is an **activation** condition, not a fulfillment test.
    #: The trigger DSL owns its evaluation; the Kernel consumes the three-valued
    #: result, where ``None`` means UNKNOWN.
    has_condition: bool = False
    condition_result: bool | None = None


@dataclass(frozen=True, slots=True)
class FulfillmentRow:
    """One row of the ledger.

    Defaults exist so ``quality/20_TDD_STRATEGY.md`` section 5.2's property
    test can write ``Fulfillment(amount=p, currency="USD")`` without inventing
    an evidence id for every generated payment.

    ``source_claim_id`` and ``authority`` are what let a later **denial** be
    matched against this row by ``M5``. Neither is a ``fulfillments`` column:
    the row is grounded on evidence, the claim written from that evidence in the
    same commit carries the frozen grid score, and ``transaction._READ_LEDGER_SQL``
    reads it back - the same read-back ``_READ_INCUMBENTS_SQL`` performs for a
    belief incumbent, and for the same reason (no ``source_class`` is persisted
    anywhere the grid key could be recovered from).

    Both are ``None`` when the grounding claim cannot be resolved, and that is
    load-bearing: a denial with no authority to measure against has no margin to
    compute, and the Kernel routes it to a person rather than guessing.
    """

    amount: Decimal | None = None
    currency: str | None = None
    admission_status: FulfillmentAdmissionStatus = FulfillmentAdmissionStatus.ADMITTED
    evidence_id: uuid.UUID | None = None
    fulfillment_id: uuid.UUID | None = None
    fulfilled_at: datetime | None = None
    source_claim_id: uuid.UUID | None = None
    authority: Decimal | None = None


#: The name ``quality/20_TDD_STRATEGY.md`` section 5.2 uses.
Fulfillment = FulfillmentRow


@dataclass(frozen=True, slots=True)
class ProposedFulfillment:
    """A payment this proposal wants applied to a commitment."""

    evidence_id: uuid.UUID
    amount: Decimal | None = None
    currency: str | None = None
    fulfilled_at: datetime | None = None
    confidence: Decimal = Decimal("1.0000")
    fulfillment_id: uuid.UUID | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FulfillmentWrite:
    """The ``fulfillments`` row this delta wants inserted.

    The full observed amount is kept even when the projection is capped: the
    row is evidence-linked and immutable, and ``excess`` stays recomputable
    from the ledger (section 4.3).
    """

    evidence_id: uuid.UUID
    admission_status: FulfillmentAdmissionStatus
    amount: Decimal | None = None
    currency: str | None = None
    fulfilled_at: datetime | None = None
    confidence: Decimal = Decimal("1.0000")
    quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ConflictWrite:
    """A ``conflicts`` row this delta wants written.

    ``specs/10_DATABASE_DDL.md`` section 7.1
    ``ck_conflicts_requires_human_consistent`` forbids ``requires_human`` on an
    ``AUTO_RESOLVED`` row, and ``ck_conflicts_open_has_no_resolution`` forbids a
    ``resolved_at`` on ``OPEN``/``NEEDS_HUMAN``. Both are properties of this
    object, asserted in ``test_money_ops.py``.
    """

    conflict_type: ConflictType
    status: ConflictStatus
    severity: ConflictSeverity
    requires_human: bool
    reason_code: KernelReasonCode
    detected_at: datetime | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CommitmentDelta:
    """The whole outcome of one fulfillment decision."""

    commitment_id: uuid.UUID
    status_before: CommitmentStatus
    status_after: CommitmentStatus
    reasons: tuple[KernelReasonCode, ...] = ()
    fulfillment: FulfillmentWrite | None = None
    conflicts: tuple[ConflictWrite, ...] = ()
    admitted_sum: Decimal | None = None
    fulfilled_after: Decimal | None = None
    outstanding_after: Decimal | None = None
    signed_outstanding: Decimal | None = None
    excess: Decimal | None = None
    revision_after: int = 0
    currency: str | None = None

    @classmethod
    def noop(cls, c: CommitmentRow, reasons: Sequence[KernelReasonCode] = ()) -> CommitmentDelta:
        """An exact no-op that is still a valid, auditable decision.

        `kernel_decisions` is written for every outcome including NOOPs, so the
        reasons travel even though nothing moved. The revision does not
        advance: rule R2.
        """
        return cls(
            commitment_id=c.commitment_id,
            status_before=c.status,
            status_after=c.status,
            reasons=tuple(reasons),
            admitted_sum=c.fulfilled_amount,
            fulfilled_after=c.fulfilled_amount,
            outstanding_after=c.outstanding_amount,
            revision_after=c.revision,
            currency=c.currency,
        )


@dataclass(frozen=True, slots=True)
class NewCommitment:
    """The opening projection of an obligation with an empty ledger.

    Three amounts or none, which is M2 of ``0004_obligation_ledger``: with any
    one of them NULL, M3 (``fulfilled <= committed``) and M4
    (``outstanding = committed - fulfilled``) are vacuously true. A non-monetary
    obligation - "terminate the service" - therefore carries all three as
    ``None`` rather than a zero, because ``0.0000 USD`` would assert that
    nothing is owed on something that was never a sum of money.
    """

    status: CommitmentStatus
    currency: str | None = None
    committed_amount: Decimal | None = None
    fulfilled_amount: Decimal | None = None
    outstanding_amount: Decimal | None = None


def open_commitment(
    *,
    committed_amount: Decimal | None,
    currency: str | None,
    tx_now: datetime,
    has_condition: bool = False,
    condition_result: bool | None = None,
    valid_to: datetime | None = None,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> NewCommitment:
    """The row a newly recorded obligation opens at. Nothing is paid yet.

    The status comes from :func:`commitment_status` - section 4.4, in that exact
    order, first match wins - rather than from a second rule written here, so a
    commitment that arrives already expired at ``valid_to``, or one whose
    activation condition has not evaluated TRUE, opens in the same state a
    fulfillment would have computed for it.

    The amount is put through :func:`provenance_domain.money.outstanding`
    against an empty ledger, reached through the module object so the
    ``PV_SABOTAGE`` rebinding of the identity reaches this path too. That call
    is not decoration: it is what refuses a negative commitment and what
    quantizes ``1800.00`` to ``DECIMAL(20,4)`` before the row is built.

    ``outstanding_amount`` then **equals** ``committed_amount`` by construction,
    because the ledger is empty and ``committed - 0 = committed``. It is
    assigned rather than recomputed, and this is deliberate: a second call whose
    result is provably identical to the first cannot be distinguished from a
    copy by any test, so keeping one would be a check that looks like a check.
    The identity has teeth the moment a fulfillment arrives, where
    :func:`recompute` derives it from the ledger sum.
    """
    zero = _ZERO
    if committed_amount is None:
        status = commitment_status(
            CommitmentRow(
                commitment_id=uuid.UUID(int=0),
                case_id=uuid.UUID(int=0),
                status=CommitmentStatus.PROPOSED if has_condition else CommitmentStatus.ACTIVE,
                currency=currency,
                committed_amount=None,
                valid_to=valid_to,
                has_condition=has_condition,
                condition_result=condition_result,
            ),
            admitted_sum=zero,
            committed=None,
            outstanding=zero,
            excess=zero,
            conflicts=(),
            tx_now=tx_now,
            cfg=cfg,
        )
        return NewCommitment(status=status, currency=currency)

    committed = money.outstanding(committed_amount, zero)
    outstanding_amount = committed
    status = commitment_status(
        CommitmentRow(
            commitment_id=uuid.UUID(int=0),
            case_id=uuid.UUID(int=0),
            status=CommitmentStatus.PROPOSED if has_condition else CommitmentStatus.ACTIVE,
            currency=currency,
            committed_amount=committed,
            fulfilled_amount=zero,
            outstanding_amount=outstanding_amount,
            valid_to=valid_to,
            has_condition=has_condition,
            condition_result=condition_result,
        ),
        admitted_sum=zero,
        committed=committed,
        outstanding=outstanding_amount,
        excess=zero - committed,
        conflicts=(),
        tx_now=tx_now,
        cfg=cfg,
    )
    return NewCommitment(
        status=status,
        currency=currency,
        committed_amount=committed,
        fulfilled_amount=zero,
        outstanding_amount=outstanding_amount,
    )


def admitted_total(ledger: Sequence[FulfillmentRow], currency: str | None) -> Decimal:
    """Sum of ``ADMITTED`` rows in *currency*. Never a running total.

    A ``REJECTED_CURRENCY`` row is kept as evidence and must never enter the
    arithmetic, or the currency gate would be a formality.
    """
    total = _ZERO
    for row in ledger:
        if row.admission_status is not FulfillmentAdmissionStatus.ADMITTED:
            continue
        if row.currency != currency:
            continue
        total += row.amount or _ZERO
    return total.quantize(money.MONEY_EXPONENT)


def _has_live_blocking_conflict(
    open_conflicts: Sequence[tuple[ConflictType, ConflictStatus]],
    conflicts: Sequence[ConflictWrite],
) -> bool:
    for conflict_type, status in open_conflicts:
        if status in LIVE_CONFLICT_STATUSES and conflict_type in BLOCKING_CONFLICT_TYPES:
            return True
    return any(
        c.status in LIVE_CONFLICT_STATUSES and c.conflict_type in BLOCKING_CONFLICT_TYPES
        for c in conflicts
    )


def commitment_status(
    c: CommitmentRow,
    *,
    admitted_sum: Decimal,
    committed: Decimal | None,
    outstanding: Decimal,
    excess: Decimal,
    conflicts: Sequence[ConflictWrite],
    open_conflict_types: Sequence[tuple[ConflictType, ConflictStatus]] = (),
    tx_now: datetime,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> CommitmentStatus:
    """Section 4.4, in that exact order, first match wins.

    Two consequences tests assert directly: ``outstanding > 0`` can never
    coexist with ``FULFILLED``, and a past ``due_at`` does **not** expire a
    commitment. A missed deadline makes the case actionable through a trigger;
    it does not extinguish the obligation. Only ``valid_to`` expires it - the
    Harborview deposit is 95 days overdue and still owed, which is the whole
    second reveal.
    """
    if c.status is CommitmentStatus.SUPERSEDED:
        return CommitmentStatus.SUPERSEDED
    # FALSE or UNKNOWN keeps a conditional promise PROPOSED; only TRUE permits
    # activation, and UNKNOWN never arms an overdue trigger.
    if c.status is CommitmentStatus.PROPOSED and c.has_condition and c.condition_result is not True:
        return CommitmentStatus.PROPOSED
    if _has_live_blocking_conflict(open_conflict_types, conflicts):
        return CommitmentStatus.DISPUTED
    if excess > cfg.overpay_tolerance:
        return CommitmentStatus.DISPUTED
    if committed is None:
        return _non_monetary_status(c, admitted_sum, tx_now)
    if outstanding == 0 and admitted_sum >= committed:
        return CommitmentStatus.FULFILLED
    if admitted_sum > 0 and outstanding > 0:
        return CommitmentStatus.PARTIAL
    if c.valid_to is not None and tx_now >= c.valid_to:
        return CommitmentStatus.EXPIRED
    return CommitmentStatus.ACTIVE


def _non_monetary_status(
    c: CommitmentRow, admitted_count: Decimal, tx_now: datetime
) -> CommitmentStatus:
    """A commitment with no ``committed_amount``.

    Section 4.4: after activation it becomes ``FULFILLED`` when at least one
    qualifying fulfillment is admitted; otherwise it stays ``ACTIVE`` or expires
    at ``valid_to``.
    """
    if admitted_count > 0:
        return CommitmentStatus.FULFILLED
    if c.valid_to is not None and tx_now >= c.valid_to:
        return CommitmentStatus.EXPIRED
    return CommitmentStatus.ACTIVE


def recompute(
    *,
    committed_amount: Decimal | None,
    currency: str | None,
    admitted: Sequence[FulfillmentRow],
    open_conflicts: Sequence[tuple[ConflictType, ConflictStatus]] = (),
    tx_now: datetime,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
    commitment_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
    status_before: CommitmentStatus = CommitmentStatus.ACTIVE,
    revision_before: int = 0,
    valid_to: datetime | None = None,
    due_at: datetime | None = None,
    has_condition: bool = False,
    condition_result: bool | None = None,
    new_fulfillment: FulfillmentWrite | None = None,
    extra_conflicts: Sequence[ConflictWrite] = (),
) -> CommitmentDelta:
    """Recompute the projection from the whole ledger. No increment anywhere.

    Because ``fulfillments`` carries ``UNIQUE (commitment_id, evidence_id)``,
    replaying the same evidence is a no-op at the constraint level *and* at the
    arithmetic level. Idempotency stops being a discipline and becomes a
    property.
    """
    row = CommitmentRow(
        commitment_id=commitment_id or uuid.UUID(int=0),
        case_id=case_id or uuid.UUID(int=0),
        status=status_before,
        currency=currency,
        committed_amount=committed_amount,
        revision=revision_before,
        due_at=due_at,
        valid_to=valid_to,
        has_condition=has_condition,
        condition_result=condition_result,
    )
    reasons: list[KernelReasonCode] = []
    conflicts: list[ConflictWrite] = list(extra_conflicts)

    admitted_sum = admitted_total(admitted, currency)
    if new_fulfillment is not None:
        reasons.append(KernelReasonCode.FULFILLMENT_ADMITTED)

    if committed_amount is None:
        # Non-monetary: there is no projection to compute, only a count.
        status_after = commitment_status(
            row,
            admitted_sum=Decimal(len(admitted)),
            committed=None,
            outstanding=_ZERO,
            excess=_ZERO,
            conflicts=conflicts,
            open_conflict_types=open_conflicts,
            tx_now=tx_now,
            cfg=cfg,
        )
        return CommitmentDelta(
            commitment_id=row.commitment_id,
            status_before=status_before,
            status_after=status_after,
            reasons=tuple(reasons),
            fulfillment=new_fulfillment,
            conflicts=tuple(conflicts),
            revision_after=revision_before + 1,
            currency=currency,
        )

    committed = committed_amount
    # The identity, reached through the module global so `PV_SABOTAGE` bites.
    # It never clamps: a negative result means more was admitted than was ever
    # committed, and returning zero here would destroy the only evidence that
    # anything was wrong.
    signed_outstanding = money.outstanding(committed, admitted_sum)
    excess = -signed_outstanding
    fulfilled = min(admitted_sum, committed)
    outstanding = money.outstanding(committed, fulfilled)

    if excess > cfg.overpay_tolerance:
        conflicts.append(
            ConflictWrite(
                conflict_type=ConflictType.FULFILLMENT_CONFLICT,
                status=ConflictStatus.NEEDS_HUMAN,
                severity=ConflictSeverity.HIGH,
                requires_human=True,
                reason_code=KernelReasonCode.CONFLICT_OVER_FULFILMENT,
                detected_at=tx_now,
                notes=(
                    f"admitted {admitted_sum} {currency} against committed "
                    f"{committed} {currency}; excess {excess}"
                ),
            )
        )
        reasons.append(KernelReasonCode.COMMITMENT_DISPUTED_EXCESS)

    status_after = commitment_status(
        row,
        admitted_sum=admitted_sum,
        committed=committed,
        outstanding=outstanding,
        excess=excess,
        conflicts=conflicts,
        open_conflict_types=open_conflicts,
        tx_now=tx_now,
        cfg=cfg,
    )
    if status_after is CommitmentStatus.PARTIAL:
        reasons.append(KernelReasonCode.COMMITMENT_PARTIAL_RECOMPUTED)
    elif status_after is CommitmentStatus.FULFILLED:
        reasons.append(KernelReasonCode.COMMITMENT_FULFILLED)
    elif status_after is CommitmentStatus.EXPIRED:
        reasons.append(KernelReasonCode.COMMITMENT_EXPIRED)

    return CommitmentDelta(
        commitment_id=row.commitment_id,
        status_before=status_before,
        status_after=status_after,
        reasons=tuple(reasons),
        fulfillment=new_fulfillment,
        conflicts=tuple(conflicts),
        admitted_sum=admitted_sum,
        fulfilled_after=fulfilled,
        outstanding_after=outstanding,
        signed_outstanding=signed_outstanding,
        excess=excess,
        revision_after=revision_before + 1,
        currency=currency,
    )


def apply_fulfillment(
    c: CommitmentRow,
    ledger: Sequence[FulfillmentRow],
    new: ProposedFulfillment,
    open_conflicts: Sequence[tuple[ConflictType, ConflictStatus]] = (),
    *,
    tx_now: datetime,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> CommitmentDelta:
    """Section 4.2's four branches: currency gate, duplicate, recompute, excess.

    The currency gate never converts and never guesses. A mismatched currency
    is written as ``REJECTED_CURRENCY`` with the observed amount intact, the
    outstanding untouched, a ``FULFILLMENT_CONFLICT`` opened for a human, and
    the commitment moved to ``DISPUTED``.
    """
    # ---- 1. currency gate: never convert, never guess ----
    if new.currency is not None and c.currency is not None and new.currency != c.currency:
        return CommitmentDelta(
            commitment_id=c.commitment_id,
            status_before=c.status,
            status_after=CommitmentStatus.DISPUTED,
            reasons=(
                KernelReasonCode.FULFILLMENT_CURRENCY_REJECTED,
                KernelReasonCode.CONFLICT_CURRENCY_MISMATCH,
            ),
            fulfillment=FulfillmentWrite(
                evidence_id=new.evidence_id,
                admission_status=FulfillmentAdmissionStatus.REJECTED_CURRENCY,
                amount=new.amount,
                currency=new.currency,
                fulfilled_at=new.fulfilled_at or tx_now,
                confidence=new.confidence,
                quantity=new.quantity,
            ),
            conflicts=(
                ConflictWrite(
                    conflict_type=ConflictType.FULFILLMENT_CONFLICT,
                    status=ConflictStatus.NEEDS_HUMAN,
                    severity=ConflictSeverity.HIGH,
                    requires_human=True,
                    reason_code=KernelReasonCode.CONFLICT_CURRENCY_MISMATCH,
                    detected_at=tx_now,
                    notes=(
                        f"fulfillment offered in {new.currency} against a "
                        f"{c.currency} commitment; the Kernel never converts"
                    ),
                ),
            ),
            admitted_sum=admitted_total(ledger, c.currency),
            fulfilled_after=c.fulfilled_amount,
            outstanding_after=c.outstanding_amount,
            revision_after=c.revision + 1,
            currency=c.currency,
        )

    # ---- 2. duplicate evidence: exact no-op, still a valid decision ----
    if any(f.evidence_id is not None and f.evidence_id == new.evidence_id for f in ledger):
        return CommitmentDelta.noop(c, reasons=[KernelReasonCode.FULFILLMENT_EVIDENCE_DUPLICATE])

    # ---- 3. recompute from the ledger, with the new row admitted ----
    admitted_row = FulfillmentRow(
        amount=None if new.amount is None else money._validated_amount(new.amount),
        currency=new.currency if new.currency is not None else c.currency,
        admission_status=FulfillmentAdmissionStatus.ADMITTED,
        evidence_id=new.evidence_id,
        fulfillment_id=new.fulfillment_id,
        fulfilled_at=new.fulfilled_at or tx_now,
    )
    write = FulfillmentWrite(
        evidence_id=new.evidence_id,
        admission_status=FulfillmentAdmissionStatus.ADMITTED,
        amount=admitted_row.amount,
        currency=admitted_row.currency,
        fulfilled_at=admitted_row.fulfilled_at,
        confidence=new.confidence,
        quantity=new.quantity,
    )
    return recompute(
        committed_amount=c.committed_amount,
        currency=c.currency,
        admitted=[*ledger, admitted_row],
        open_conflicts=open_conflicts,
        tx_now=tx_now,
        cfg=cfg,
        commitment_id=c.commitment_id,
        case_id=c.case_id,
        status_before=c.status,
        revision_before=c.revision,
        valid_to=c.valid_to,
        due_at=c.due_at,
        has_condition=c.has_condition,
        condition_result=c.condition_result,
        new_fulfillment=write,
    )
