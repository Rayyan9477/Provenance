"""T4.8 - the bitemporal rules T1-T4 and the half-open interval convention.

Authority: ``specs/12_KERNEL_ALGORITHMS.md`` section 8.

Two clocks. Valid time ``[valid_from, valid_to)`` is when a statement is true
in the world; record time is when Provenance learned it. Sorting by record time
alone produces confidently wrong answers, which is the failure mode of every
RAG-over-your-inbox product, so the two are never allowed to substitute for one
another here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from provenance_domain.enums import KernelReasonCode
from services.control_plane.app.memory_kernel import temporal
from services.control_plane.app.memory_kernel.config import DEFAULT_KERNEL_CONFIG, KernelConfig

pytestmark = pytest.mark.unit

TX_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
JUN_1 = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
JUL_1 = datetime(2026, 7, 1, 4, 0, tzinfo=UTC)
MAY_1 = datetime(2026, 5, 1, 4, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# T1 - record time is sourced from the transaction, never from a payload
# ---------------------------------------------------------------------------


def test_tx_now_is_transaction_timestamp() -> None:
    """Section 1.4: every row written in one transaction shares one record time,
    and it comes from the server, not from Python and not from the model."""
    sql = " ".join(temporal.TX_NOW_SQL.split()).lower()
    assert sql == "select transaction_timestamp()"
    assert "now()" not in sql
    assert "statement_timestamp" not in sql


def test_a_naive_datetime_is_refused_rather_than_assumed_utc() -> None:
    with pytest.raises(temporal.IntervalError):
        temporal.Interval(valid_from=datetime(2026, 6, 1, 4, 0), valid_to=None)


# ---------------------------------------------------------------------------
# The half-open convention
# ---------------------------------------------------------------------------


def test_the_interval_is_half_open() -> None:
    interval = temporal.Interval(valid_from=JUN_1, valid_to=JUL_1)
    assert interval.contains(JUN_1)
    assert interval.contains(JUL_1 - timedelta(microseconds=1))
    assert not interval.contains(JUL_1)


def test_an_open_ended_interval_has_no_upper_bound() -> None:
    interval = temporal.Interval(valid_from=JUN_1, valid_to=None)
    assert interval.contains(datetime(2099, 1, 1, tzinfo=UTC))


def test_an_inverted_interval_is_rejected_not_coerced() -> None:
    codes = temporal.validate_interval(
        temporal.Interval(valid_from=JUL_1, valid_to=JUN_1), tx_now=TX_NOW
    )
    assert KernelReasonCode.VALIDITY_INVERTED in codes


def test_an_empty_interval_is_rejected() -> None:
    """``valid_to == valid_from`` contains nothing under a half-open reading, so
    it is inverted for every purpose that matters."""
    codes = temporal.validate_interval(
        temporal.Interval(valid_from=JUN_1, valid_to=JUN_1), tx_now=TX_NOW
    )
    assert KernelReasonCode.VALIDITY_INVERTED in codes


def test_validity_beyond_the_horizon_is_flagged() -> None:
    cfg = KernelConfig()
    far = TX_NOW + timedelta(days=cfg.future_validity_horizon_days + 1)
    codes = temporal.validate_interval(
        temporal.Interval(valid_from=far, valid_to=None), tx_now=TX_NOW, cfg=cfg
    )
    assert KernelReasonCode.VALIDITY_FUTURE_BEYOND_HORIZON in codes


def test_validity_inside_the_horizon_is_not_flagged() -> None:
    near = TX_NOW + timedelta(days=30)
    codes = temporal.validate_interval(
        temporal.Interval(valid_from=near, valid_to=None), tx_now=TX_NOW
    )
    assert codes == ()


# ---------------------------------------------------------------------------
# T2 - unknown validity stays unknown
# ---------------------------------------------------------------------------


def test_unknown_validity_stays_unknown() -> None:
    """The kernel never invents a date and never falls back to received_at."""
    assert temporal.validity_basis(temporal.Interval()) is temporal.ValidityBasis.UNKNOWN


def test_an_unknown_interval_does_not_participate_in_overlap() -> None:
    assert not temporal.overlaps(
        temporal.Interval(), temporal.Interval(valid_from=JUN_1, valid_to=JUL_1)
    )


def test_a_bounded_interval_is_explicit() -> None:
    assert (
        temporal.validity_basis(temporal.Interval(valid_from=JUN_1, valid_to=JUL_1))
        is temporal.ValidityBasis.EXPLICIT
    )


def test_an_open_ended_interval_is_explicit_open() -> None:
    assert (
        temporal.validity_basis(temporal.Interval(valid_from=JUN_1))
        is temporal.ValidityBasis.EXPLICIT_OPEN
    )


# ---------------------------------------------------------------------------
# Overlap, on the half-open reading
# ---------------------------------------------------------------------------


def test_touching_intervals_do_not_overlap() -> None:
    """``[May, Jun)`` and ``[Jun, Jul)`` share no instant. A closed reading would
    say they collide on 1 June and manufacture a contradiction."""
    assert not temporal.overlaps(
        temporal.Interval(valid_from=MAY_1, valid_to=JUN_1),
        temporal.Interval(valid_from=JUN_1, valid_to=JUL_1),
    )


def test_genuinely_overlapping_intervals_overlap() -> None:
    assert temporal.overlaps(
        temporal.Interval(valid_from=MAY_1, valid_to=JUL_1),
        temporal.Interval(valid_from=JUN_1, valid_to=None),
    )


# ---------------------------------------------------------------------------
# T3 / supersession - close the predecessor at the successor's valid_from
# ---------------------------------------------------------------------------


def test_supersession_closes_the_predecessor_with_no_gap_and_no_overlap() -> None:
    predecessor = temporal.Interval(valid_from=MAY_1, valid_to=None)
    successor = temporal.Interval(valid_from=JUN_1, valid_to=None)
    closed = temporal.close_predecessor(predecessor, successor)
    assert closed.valid_from == MAY_1
    assert closed.valid_to == JUN_1
    temporal.assert_chain_has_no_gap_or_overlap([closed, successor])


def test_closing_an_already_closed_predecessor_keeps_the_earlier_bound() -> None:
    predecessor = temporal.Interval(valid_from=MAY_1, valid_to=JUN_1)
    successor = temporal.Interval(valid_from=JUL_1, valid_to=None)
    assert temporal.close_predecessor(predecessor, successor).valid_to == JUN_1


def test_a_successor_that_starts_before_its_predecessor_is_refused() -> None:
    with pytest.raises(temporal.IntervalError):
        temporal.close_predecessor(
            temporal.Interval(valid_from=JUN_1), temporal.Interval(valid_from=MAY_1)
        )


def test_a_successor_with_unknown_validity_cannot_close_a_predecessor() -> None:
    """T2 again: an unknown start is not a closing instant."""
    with pytest.raises(temporal.IntervalError):
        temporal.close_predecessor(temporal.Interval(valid_from=MAY_1), temporal.Interval())


def test_a_chain_with_a_gap_is_refused() -> None:
    with pytest.raises(temporal.IntervalError):
        temporal.assert_chain_has_no_gap_or_overlap(
            [
                temporal.Interval(valid_from=MAY_1, valid_to=JUN_1),
                temporal.Interval(valid_from=JUL_1, valid_to=None),
            ]
        )


def test_a_chain_that_overlaps_is_refused() -> None:
    with pytest.raises(temporal.IntervalError):
        temporal.assert_chain_has_no_gap_or_overlap(
            [
                temporal.Interval(valid_from=MAY_1, valid_to=JUL_1),
                temporal.Interval(valid_from=JUN_1, valid_to=None),
            ]
        )


@given(offsets=st.lists(st.integers(min_value=1, max_value=500), min_size=2, max_size=8))
def test_closure_over_a_generated_chain_never_gaps_or_overlaps(offsets: list[int]) -> None:
    """The property the section 8.6 structural invariants state, generated."""
    starts: list[datetime] = []
    cursor = MAY_1
    for offset in offsets:
        cursor = cursor + timedelta(days=offset)
        starts.append(cursor)

    chain: list[temporal.Interval] = []
    for index, start in enumerate(starts):
        successor = temporal.Interval(valid_from=start, valid_to=None)
        if chain:
            chain[index - 1] = temporal.close_predecessor(chain[index - 1], successor)
        chain.append(successor)
    temporal.assert_chain_has_no_gap_or_overlap(chain)


# ---------------------------------------------------------------------------
# T4 - late-arriving evidence makes history without changing the present
# ---------------------------------------------------------------------------


def test_late_arriving_evidence_is_recognised() -> None:
    """A May letter learned in September is history, not a new present."""
    assert temporal.is_late_arriving(
        temporal.Interval(valid_from=MAY_1, valid_to=JUN_1),
        temporal.Interval(valid_from=JUN_1, valid_to=None),
    )


def test_a_successor_after_the_current_version_is_not_late_arriving() -> None:
    assert not temporal.is_late_arriving(
        temporal.Interval(valid_from=JUL_1, valid_to=None),
        temporal.Interval(valid_from=JUN_1, valid_to=None),
    )


def test_late_arrival_with_unknown_validity_is_not_claimed() -> None:
    assert not temporal.is_late_arriving(
        temporal.Interval(), temporal.Interval(valid_from=JUN_1, valid_to=None)
    )


def test_the_default_config_is_the_shipped_one() -> None:
    assert DEFAULT_KERNEL_CONFIG.future_validity_horizon_days == 3650
