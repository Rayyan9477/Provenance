"""Stage C — the valid-time window (``T6.3``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 8, rules C-1 to C-4.

Stage C issues no query. It computes one half-open ``[window_from, window_to)``
used as a **recall-oriented** filter in Stage D and as a **precision** signal in
Stage G, and that split is the whole design: candidate generation optimises
recall, ranking optimises precision, and reversing the order is unrecoverable
because a candidate never generated cannot be reranked back in.

C-1 is enforced by absence
---------------------------
``observed_at``, ``recorded_at`` and ``created_at`` are never relevance
filters -- bitemporal rule T1. A cancellation confirmation from 15 May imported
in September is exactly as relevant as one imported in May; filtering by
ingestion time would drop it and the hero demo's contradiction would never be
detected. :func:`overlaps` therefore has no parameter for record time at all.
The absence is the enforcement, and a test asserts the signature to keep it
that way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from services.control_plane.app.retrieval.config import (
    DEFAULT_LOOKBACK_DAYS,
    FUTURE_HORIZON_DAYS,
    TEMPORAL_SLACK_DAYS,
)

__all__ = ["TEMPORAL_SLACK", "TemporalWindow", "build_window", "overlaps"]

#: 45 days. A candidate-generation parameter, not a relevance one: its job is to
#: stop an off-by-a-billing-cycle dropping the one document that matters. The
#: 15 May termination confirmation sits 17 days before the 1 June invoice
#: period, and a tight window anchored on the invoice would miss it. Precision
#: is recovered in Stage G, where temporal credit decays with the gap.
TEMPORAL_SLACK = timedelta(days=TEMPORAL_SLACK_DAYS)

_DEFAULT_LOOKBACK = timedelta(days=DEFAULT_LOOKBACK_DAYS)
_FUTURE_HORIZON = timedelta(days=FUTURE_HORIZON_DAYS)


@dataclass(frozen=True)
class TemporalWindow:
    """A half-open ``[window_from, window_to)`` interval in valid time."""

    window_from: datetime
    window_to: datetime

    def __post_init__(self) -> None:
        if self.window_to <= self.window_from:
            raise ValueError(
                "a retrieval window must be non-empty; an inverted or zero-width "
                "window admits nothing and looks exactly like a corpus with no "
                "matching evidence"
            )


def build_window(
    *,
    dates: Sequence[datetime] = (),
    case_spans: Sequence[tuple[datetime, datetime]] = (),
    now: datetime,
) -> TemporalWindow:
    """Union of extracted dates, candidate case lifespans and a default lookback.

    Widened by :data:`TEMPORAL_SLACK` on both sides, then clamped forward to
    ``now + FUTURE_HORIZON`` so a mis-parsed year widens the window by at most
    the horizon rather than by a century.

    C-4 needs no branch: when a numeric date is ambiguous both interpretations
    are already in *dates*, so ``min``/``max`` spans them. The cost is a wider
    window; the alternative is a confidently wrong one.
    """
    lows = [now - _DEFAULT_LOOKBACK]
    highs = [now]

    if dates:
        lows.append(min(dates))
        highs.append(max(dates))

    for opened_at, last_activity_at in case_spans:
        lows.append(opened_at)
        highs.append(last_activity_at)

    low = min(lows) - TEMPORAL_SLACK
    high = max(highs) + TEMPORAL_SLACK
    return TemporalWindow(low, min(high, now + _FUTURE_HORIZON))


def overlaps(
    window: TemporalWindow, valid_from: datetime | None, valid_to: datetime | None
) -> bool:
    """Half-open overlap, with ``None`` meaning unbounded.

    Rule C-2, and the reason it is written as two independent ``None`` checks
    rather than as a coalesce to some sentinel date: ``valid_from IS NULL``
    means *we do not know when this was true*, and the correct handling of
    unknown is to keep the row and let authority and grounding decide.
    Excluding NULLs turns "we do not know" into "it did not happen", and it does
    so by omission -- the row is simply absent from the result, with nothing to
    notice.
    """
    starts_before_end = valid_from is None or valid_from < window.window_to
    ends_after_start = valid_to is None or valid_to > window.window_from
    return starts_before_end and ends_after_start
