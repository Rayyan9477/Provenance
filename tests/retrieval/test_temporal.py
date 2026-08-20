"""Stage C — the valid-time window and its four rules (``T6.3``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 8, rules C-1 to C-4.
- ``docs/quality/20_TDD_STRATEGY.md`` section 4.2 rule 3 -- no wall-clock reads
  in tests. ``now`` is passed in, never taken.

Why C-2 gets the sharpest test
-------------------------------
``valid_from IS NULL`` means "we do not know when this was true". Excluding
NULLs turns *we do not know* into *it did not happen*, and it does so silently:
the row simply is not in the result. Every other rule here fails visibly when
broken; this one fails by omission, so it is asserted from both sides.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.control_plane.app.retrieval import temporal
from services.control_plane.app.retrieval.config import (
    DEFAULT_LOOKBACK_DAYS,
    FUTURE_HORIZON_DAYS,
    TEMPORAL_SLACK_DAYS,
)

pytestmark = [pytest.mark.unit, pytest.mark.retrieval]

#: The demo clock, ``2026-09-18T09:00:00-04:00`` as UTC. Matches the root
#: ``conftest.py`` anchor; passed in explicitly because rule 3 bans reads.
NOW = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)

SLACK = timedelta(days=TEMPORAL_SLACK_DAYS)


def test_the_default_window_is_the_lookback_widened_by_slack() -> None:
    """With no extracted date and no candidate case, the window is the default."""
    window = temporal.build_window(now=NOW)
    assert window.window_from == NOW - timedelta(days=DEFAULT_LOOKBACK_DAYS) - SLACK
    assert window.window_to == NOW + SLACK


def test_extracted_dates_widen_the_window_on_both_sides() -> None:
    """Rule C-3. The 45-day slack prevents an off-by-a-billing-cycle drop.

    The 15 May termination confirmation sits 17 days before the 1 June invoice
    period. A window anchored tightly on the invoice would miss it, and a
    candidate never generated cannot be reranked back in.
    """
    invoice_period = [datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC)]
    window = temporal.build_window(dates=invoice_period, now=NOW)
    assert window.window_from <= datetime(2026, 5, 15, tzinfo=UTC)


def test_the_window_never_reaches_beyond_the_future_horizon() -> None:
    """Rule from section 8's ``min(hi, now + FUTURE_HORIZON)``.

    A mis-parsed year -- ``2126`` for ``2026`` -- must widen the window by at
    most the horizon rather than by a century.
    """
    window = temporal.build_window(dates=[datetime(2126, 1, 1, tzinfo=UTC)], now=NOW)
    assert window.window_to == NOW + timedelta(days=FUTURE_HORIZON_DAYS)


def test_an_ambiguous_numeric_date_widens_rather_than_picking_a_side() -> None:
    """Rule C-4. ``06/01/2026`` is 1 June or 6 January; both are in ``dates``.

    The cost is a wider window. The alternative is a confidently wrong one.
    """
    both = [datetime(2026, 1, 6, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)]
    window = temporal.build_window(dates=both, now=NOW)
    assert window.window_from <= datetime(2026, 1, 6, tzinfo=UTC) - SLACK
    assert window.window_to >= datetime(2026, 6, 1, tzinfo=UTC) + SLACK


def test_candidate_case_lifespans_widen_the_window() -> None:
    """A case opened before the default lookback still contributes its span."""
    opened = datetime(2024, 1, 1, tzinfo=UTC)
    window = temporal.build_window(case_spans=[(opened, datetime(2024, 6, 1, tzinfo=UTC))], now=NOW)
    assert window.window_from == opened - SLACK


def test_overlap_is_half_open() -> None:
    """``[valid_from, valid_to)``. A row ending exactly at ``window_from`` is out;
    a row starting exactly at ``window_to`` is out."""
    window = temporal.TemporalWindow(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC)
    )
    assert not temporal.overlaps(window, None, datetime(2026, 6, 1, tzinfo=UTC))
    assert not temporal.overlaps(window, datetime(2026, 7, 1, tzinfo=UTC), None)
    assert temporal.overlaps(window, datetime(2026, 6, 30, tzinfo=UTC), None)


def test_unknown_validity_is_included_never_excluded() -> None:
    """Rule C-2, from both sides.

    ``valid_from IS NULL`` means "we do not know when this was true", and the
    correct handling of unknown is to keep the row and let authority and
    grounding decide.
    """
    window = temporal.TemporalWindow(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC)
    )
    assert temporal.overlaps(window, None, None)
    assert temporal.overlaps(window, None, datetime(2026, 6, 15, tzinfo=UTC))
    assert temporal.overlaps(window, datetime(2026, 6, 15, tzinfo=UTC), None)


def test_record_time_is_never_a_relevance_filter() -> None:
    """Rule C-1, bitemporal rule T1.

    A 15 May cancellation confirmation imported in September is exactly as
    relevant as one imported in May. Filtering on ingestion time would drop it,
    and the hero demo's contradiction would never be detected -- so
    :func:`overlaps` takes valid time and has no parameter for record time at
    all. The absence is the enforcement.
    """
    import inspect

    parameters = set(inspect.signature(temporal.overlaps).parameters)
    assert parameters == {"window", "valid_from", "valid_to"}
    assert not (parameters & {"observed_at", "recorded_at", "created_at", "received_at"})
