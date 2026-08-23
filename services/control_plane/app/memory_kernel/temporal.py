"""The bitemporal rules T1-T4, as functions with no clock of their own — T4.8.

Authority
---------
``specs/12_KERNEL_ALGORITHMS.md`` section 8 in full: T1 record time never
substitutes for valid time, T2 unknown validity stays unknown, T3 supersession
requires authority, T4 late-arriving evidence may create history without
changing the present, and section 8.6's two structural invariants.

The one rule this module exists to make enforceable
---------------------------------------------------
**The Kernel never invents a date.** When evidence gives no trustworthy
effective date, ``valid_from`` and ``valid_to`` stay ``NULL`` and the
proposition's basis is ``UNKNOWN``; it then does not participate in overlap
matching at all. "We processed your refund" with no date is not evidence that
the refund happened during any particular window, and treating it as
``[-inf, +inf)`` would make it conflict with everything. That single rule
eliminates the largest source of false-positive conflicts, so it is a property
of the type rather than a convention in a call site.

Record time comes from :data:`TX_NOW_SQL` and from nowhere else. It is
``transaction_timestamp()`` rather than ``now()`` or ``statement_timestamp()``
because every row written in one transaction must share one record time; with
``statement_timestamp()`` the claim and the belief version it grounds would
disagree about when Provenance learned them, by however long the transaction
took.

Stdlib only. No ``provenance_db``, no ``asyncio``: the SQL is a string here and
is executed by ``transaction.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from provenance_domain.enums import KernelReasonCode
from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG, KernelConfig
from services.control_plane.app.memory_kernel.propositions import ValidityBasis

__all__ = [
    "TX_NOW_SQL",
    "Interval",
    "IntervalError",
    "ValidityBasis",
    "assert_chain_has_no_gap_or_overlap",
    "close_predecessor",
    "is_late_arriving",
    "overlaps",
    "validate_interval",
    "validity_basis",
]

#: Rule T1's only clock. ``transaction_timestamp()`` is constant for the whole
#: transaction, which is what makes "every row of this commit shares one
#: ``recorded_at``" true rather than approximately true.
TX_NOW_SQL: Final[str] = "SELECT transaction_timestamp()"


class IntervalError(ValueError):
    """A validity interval was inverted, empty, naive, or impossible to close.

    Carries a reason code so the refusal is reportable through the closed
    catalogue rather than as prose.
    """

    def __init__(
        self, message: str, code: KernelReasonCode = KernelReasonCode.VALIDITY_INVERTED
    ) -> None:
        self.code: Final[KernelReasonCode] = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Interval:
    """A half-open validity interval ``[valid_from, valid_to)``.

    ``None`` on either side means unbounded on that side. Both bounds must be
    timezone-aware: a naive datetime is refused rather than assumed to be UTC,
    because "assume UTC" is how a New York cancellation confirmation lands four
    hours into the wrong day and the whole day-boundary convention in section
    2.4 stops meaning anything.
    """

    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        for label, moment in (("valid_from", self.valid_from), ("valid_to", self.valid_to)):
            if moment is not None and moment.tzinfo is None:
                raise IntervalError(
                    f"{label} is naive; the Kernel never assumes a timezone "
                    "(section 8.1: valid time comes only from evidence content)"
                )

    def contains(self, moment: datetime) -> bool:
        """Half-open: the lower bound is in, the upper bound is out."""
        if self.valid_from is not None and moment < self.valid_from:
            return False
        return not (self.valid_to is not None and moment >= self.valid_to)

    @property
    def is_known(self) -> bool:
        return self.valid_from is not None


def validity_basis(interval: Interval) -> ValidityBasis:
    """Section 8.2's three values, read off the interval.

    Shared with ``propositions.ValidityBasis`` rather than declared a second
    time: two vocabularies for one concept is how a proposition ends up
    comparable in one module and incomparable in the next.
    """
    if interval.valid_from is None:
        return ValidityBasis.UNKNOWN
    if interval.valid_to is None:
        return ValidityBasis.EXPLICIT_OPEN
    return ValidityBasis.EXPLICIT


def validate_interval(
    interval: Interval,
    *,
    tx_now: datetime,
    cfg: KernelConfig = DEFAULT_KERNEL_CONFIG,
) -> tuple[KernelReasonCode, ...]:
    """Reason codes for *interval*, empty when it is usable.

    Rejection rather than coercion: an inverted pair is a statement about the
    world that cannot be true, and silently swapping the bounds would admit a
    fabricated fact with a plausible shape.
    """
    codes: list[KernelReasonCode] = []
    lo, hi = interval.valid_from, interval.valid_to
    if lo is not None and hi is not None and hi <= lo:
        # `hi == lo` is empty under the half-open reading, so it is inverted for
        # every purpose that matters, and `ck_claims_validity` refuses it too.
        codes.append(KernelReasonCode.VALIDITY_INVERTED)
    horizon = tx_now + timedelta(days=cfg.future_validity_horizon_days)
    if (lo is not None and lo > horizon) or (hi is not None and hi > horizon):
        codes.append(KernelReasonCode.VALIDITY_FUTURE_BEYOND_HORIZON)
    return tuple(codes)


def overlaps(left: Interval, right: Interval) -> bool:
    """True when the two intervals share at least one instant.

    Rule T2 is enforced first: an interval with no stated start does not
    participate in overlap at all, so the answer is ``False`` rather than an
    optimistic ``True`` computed against an invented bound.
    """
    if not (left.is_known and right.is_known):
        return False
    lo = max(_lower(left), _lower(right))
    hi = min(_upper(left), _upper(right))
    return hi > lo


def close_predecessor(predecessor: Interval, successor: Interval) -> Interval:
    """Close *predecessor* at *successor*'s ``valid_from``. No gap, no overlap.

    An already-closed predecessor keeps its earlier bound: closing it again at a
    later instant would extend a fact's stated lifetime after the evidence for
    it stopped, which is a rewrite of history rather than a supersession.
    """
    if successor.valid_from is None:
        raise IntervalError(
            "a successor with unknown validity cannot close a predecessor; "
            "rule T2 forbids inventing the closing instant",
            KernelReasonCode.VALIDITY_UNKNOWN_NOT_COMPARABLE,
        )
    if predecessor.valid_from is not None and successor.valid_from < predecessor.valid_from:
        raise IntervalError(
            f"successor starts at {successor.valid_from.isoformat()}, before its "
            f"predecessor's {predecessor.valid_from.isoformat()}"
        )
    if predecessor.valid_to is not None and predecessor.valid_to <= successor.valid_from:
        return predecessor
    return Interval(valid_from=predecessor.valid_from, valid_to=successor.valid_from)


def is_late_arriving(successor: Interval, current: Interval) -> bool:
    """Rule T4: the new statement is about a period that has already passed.

    Such a version is written into the chain as history; it does not become the
    present. ``UNKNOWN`` on either side answers ``False`` rather than guessing.
    """
    if successor.valid_from is None or current.valid_from is None:
        return False
    return successor.valid_from < current.valid_from


def assert_chain_has_no_gap_or_overlap(chain: Sequence[Interval]) -> None:
    """Section 8.6's two structural invariants over one belief's lineage.

    Consecutive versions must abut exactly: ``chain[i].valid_to ==
    chain[i + 1].valid_from``. A gap is a period the belief silently had no
    value for; an overlap is a period it had two.
    """
    for index in range(len(chain) - 1):
        earlier, later = chain[index], chain[index + 1]
        if earlier.valid_to is None:
            raise IntervalError(
                f"version {index} is still open while version {index + 1} exists; "
                "supersession must close the predecessor"
            )
        if later.valid_from is None:
            raise IntervalError(f"version {index + 1} has no stated start")
        if earlier.valid_to < later.valid_from:
            raise IntervalError(
                f"gap between {earlier.valid_to.isoformat()} and "
                f"{later.valid_from.isoformat()}: the belief had no value there"
            )
        if earlier.valid_to > later.valid_from:
            raise IntervalError(
                f"overlap between {later.valid_from.isoformat()} and "
                f"{earlier.valid_to.isoformat()}: the belief had two values there"
            )


def _lower(interval: Interval) -> datetime:
    assert interval.valid_from is not None  # guarded by `is_known`
    return interval.valid_from


def _upper(interval: Interval) -> datetime:
    return (
        interval.valid_to
        if interval.valid_to is not None
        else datetime.max.replace(
            tzinfo=interval.valid_from.tzinfo if interval.valid_from else None
        )
    )
