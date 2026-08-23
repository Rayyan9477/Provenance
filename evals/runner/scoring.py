"""Ranking metrics. Pure functions; no database, no configuration, no clock.

Every function here answers one question about one ranked list, and every one
of them can return ``None``. That is the point: ``recall@k`` over an empty gold
set is **undefined**, not zero, and a harness that returns ``0.0`` there
reports that retrieval missed everything when in fact there was nothing to
find. ``D-00-005``, one layer down from the verdict types.

The distinction this module holds:

===============  =========================================================
result           claim
===============  =========================================================
``None``         the metric is undefined for this query
``0.0``          the metric is defined, and it is zero
===============  =========================================================

:func:`reciprocal_rank` returns ``0.0`` -- a real zero -- when the gold set is
non-empty and no gold item appears within the stated cap. That is a
measurement: we looked as far as the cap and it was not there. It is not the
same claim as "the item is absent from the corpus", so the caller reports the
cap alongside the number and a reader can tell which one they have.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Sequence
from typing import TypeVar

__all__ = [
    "exclude",
    "first_gold_rank",
    "mean",
    "recall_at_k",
    "reciprocal_rank",
]

T = TypeVar("T", bound=Hashable)


def exclude(ranked: Sequence[T], removed: Iterable[T]) -> list[T]:
    """*ranked* with every member of *removed* dropped, order preserved."""
    drop = set(removed)
    return [item for item in ranked if item not in drop]


def first_gold_rank(ranked: Sequence[T], gold: frozenset[T]) -> int | None:
    """The 1-based rank of the first member of *gold* in *ranked*, else ``None``."""
    for position, item in enumerate(ranked, start=1):
        if item in gold:
            return position
    return None


def recall_at_k(ranked: Sequence[T], gold: frozenset[T], k: int) -> float | None:
    """Fraction of *gold* appearing in the first *k* of *ranked*.

    ``None`` when *gold* is empty: the fraction has no denominator, and
    reporting ``0.0`` would claim a miss where there was nothing to hit.
    """
    if k <= 0:
        raise ValueError(f"k must be positive; got {k}. A cutoff of zero scores nothing.")
    if not gold:
        return None
    found = sum(1 for item in ranked[:k] if item in gold)
    return found / len(gold)


def reciprocal_rank(ranked: Sequence[T], gold: frozenset[T], cap: int) -> float | None:
    """``1 / rank`` of the first gold hit within *cap*.

    ``None`` when *gold* is empty (undefined). ``0.0`` when *gold* is non-empty
    and nothing hit within *cap* -- a measurement, reported with the cap so a
    reader can tell it from "absent from the corpus".
    """
    if cap <= 0:
        raise ValueError(f"cap must be positive; got {cap}.")
    if not gold:
        return None
    rank = first_gold_rank(ranked[:cap], gold)
    return 0.0 if rank is None else 1.0 / rank


def mean(values: Iterable[float | None]) -> float | None:
    """Arithmetic mean of the measured values, ignoring ``None``.

    ``None`` when nothing was measured -- averaging an empty sequence to
    ``0.0`` is the same substitution one aggregation level up, and it reports a
    failing score for a suite that scored nothing.
    """
    measured = [value for value in values if value is not None]
    if not measured:
        return None
    return sum(measured) / len(measured)
