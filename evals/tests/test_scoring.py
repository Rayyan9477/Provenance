"""Ranking metrics, and the three places an undefined metric becomes a zero."""

from __future__ import annotations

import pytest

from evals.runner.scoring import exclude, first_gold_rank, mean, recall_at_k, reciprocal_rank

pytestmark = pytest.mark.unit

RANKED = ("q", "a", "d1", "b", "d2", "d3", "c")
GOLD = frozenset({"a", "b", "c"})


def test_recall_at_k_counts_only_gold_inside_the_cutoff() -> None:
    # ranks:      1    2    3     4    5     6     7
    # ranked:     q    a    d1    b    d2    d3    c
    assert recall_at_k(RANKED, GOLD, 1) == pytest.approx(0.0)
    assert recall_at_k(RANKED, GOLD, 2) == pytest.approx(1 / 3)
    assert recall_at_k(RANKED, GOLD, 4) == pytest.approx(2 / 3)
    assert recall_at_k(RANKED, GOLD, 7) == pytest.approx(1.0)


def test_recall_at_k_ignores_gold_that_is_not_in_the_ranking_at_all() -> None:
    assert recall_at_k(("a",), frozenset({"a", "zz"}), 10) == pytest.approx(0.5)


def test_recall_at_k_is_undefined_rather_than_zero_for_an_empty_gold_set() -> None:
    assert recall_at_k(RANKED, frozenset(), 10) is None, (
        "recall over an empty gold set returned 0.0. That reads as 'retrieval "
        "found none of them' when the truth is 'there was nothing to find'."
    )


def test_recall_at_k_refuses_a_non_positive_cutoff() -> None:
    with pytest.raises(ValueError, match="k"):
        recall_at_k(RANKED, GOLD, 0)


def test_first_gold_rank_is_one_based_and_finds_the_earliest_hit() -> None:
    assert first_gold_rank(RANKED, GOLD) == 2


def test_first_gold_rank_is_none_when_no_gold_appears() -> None:
    assert first_gold_rank(("q", "d1", "d2"), GOLD) is None


def test_reciprocal_rank_is_one_over_the_first_gold_rank() -> None:
    assert reciprocal_rank(RANKED, GOLD, cap=7) == pytest.approx(0.5)


def test_reciprocal_rank_respects_the_cap_it_was_given() -> None:
    # "c" is at rank 7; with a cap of 6 the only gold inside the cap is at 2.
    assert reciprocal_rank(("q", "d1", "d2", "d3", "d4", "d5", "c"), GOLD, cap=6) == pytest.approx(
        0.0
    )
    assert reciprocal_rank(("q", "d1", "d2", "d3", "d4", "d5", "c"), GOLD, cap=7) == pytest.approx(
        1 / 7
    )


def test_reciprocal_rank_is_a_measured_zero_when_gold_exists_but_did_not_rank() -> None:
    value = reciprocal_rank(("d1", "d2"), GOLD, cap=2)
    assert value == pytest.approx(0.0)
    assert value is not None, (
        "a gold set that exists and did not rank within the cap is a "
        "measurement, not an absence. Returning None here would hide a real miss."
    )


def test_reciprocal_rank_is_undefined_rather_than_zero_for_an_empty_gold_set() -> None:
    assert reciprocal_rank(RANKED, frozenset(), cap=10) is None


def test_exclude_drops_the_named_members_and_keeps_the_order() -> None:
    assert exclude(RANKED, {"q", "d2"}) == ["a", "d1", "b", "d3", "c"]


def test_exclude_of_nothing_returns_the_ranking_unchanged() -> None:
    assert exclude(RANKED, ()) == list(RANKED)


def test_mean_of_measured_values_is_their_average() -> None:
    assert mean([1.0, 0.0, 0.5]) == pytest.approx(0.5)


def test_mean_ignores_undefined_entries_rather_than_scoring_them_zero() -> None:
    assert mean([1.0, None, 0.0]) == pytest.approx(0.5), (
        "an undefined per-query metric was averaged in as a zero, which drags "
        "the reported score down for queries that were never scoreable."
    )


def test_mean_of_nothing_measured_is_none_not_zero() -> None:
    assert mean([]) is None
    assert mean([None, None]) is None, (
        "averaging a sequence of undefined values produced 0.0. The aggregate "
        "then reports a failing score for a suite that measured nothing."
    )
