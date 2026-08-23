"""Stage G — tiers, weights, and abstention as a first-class output (``T6.4``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 11.2, 11.3 and 11.4.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T6.4``, fourth sub-task: "Treat
  abstention as a legitimate output. A retrieval returning nothing because
  nothing qualifies is correct; one that returns its best guess regardless is
  how RAG resolves contradiction by cosine similarity, which is the failure
  this product exists to avoid."

What the tier test is really asserting
---------------------------------------
Ordering is lexicographic on **tier first**, then on score. That is not a
refinement of the weighted sum -- it is a guard against it. With enough weak
signals any linear score can be beaten, so a semantically gorgeous but
structurally unrelated document could outscore an exact account-number match if
the weights were the only mechanism. :func:`test_no_score_can_lift_a_lower_tier_
above_a_higher_one` asserts that at the extreme: a ``T3`` candidate at the
maximum possible score still loses to a ``T0`` candidate at the minimum.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.control_plane.app.retrieval import rerank
from services.control_plane.app.retrieval.config import (
    B_CORROBORATION_CAP,
    CASE_STATE_SALIENCE,
    P_SUPERSEDED,
    POSITIVE_WEIGHTS,
    TAU_ABSTAIN,
    TAU_ABSTAIN_DEGRADED,
    TAU_IDENTITY_ACCEPT,
    TAU_IDENTITY_MARGIN,
    W_AUTHORITY,
)

pytestmark = [pytest.mark.unit, pytest.mark.retrieval]

NOW = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)


def candidate(**overrides: object) -> rerank.Candidate:
    base: dict[str, object] = {
        "match_strength": 0.0,
        "cosine_similarity": 0.0,
        "source_authority": 0.0,
        "case_status": None,
        "flag_ref_match": False,
        "flag_thread_match": False,
        "flag_domain_match": False,
        "flag_temporal_overlap": False,
        "grounds_belief_version_ids": (),
        "contradicts_belief_version_ids": (),
        "observed_at": NOW,
        "feature_count": 0,
        "superseded_grounding": False,
        "temporal_gap_days": 0.0,
    }
    base.update(overrides)
    return rerank.Candidate(**base)  # type: ignore[arg-type]


# ==========================================================================
# 11.3 -- the weights
# ==========================================================================


def test_the_seven_positive_weights_sum_to_exactly_one() -> None:
    """Section 11.3's own ``ASSERT_SUM``.

    Not decoration: the seven weights are the whole of the positive score, so a
    sum below 1.00 silently compresses every score toward the abstention floor
    and a sum above it pushes every score past ``TAU_IDENTITY_ACCEPT``.
    """
    assert sum(POSITIVE_WEIGHTS) == pytest.approx(1.00, abs=1e-9)


def test_the_superseded_penalty_outweighs_authority_alone() -> None:
    """Section 11.3: "A penalty larger than ``W_AUTHORITY`` guarantees that a
    superseded-grounding item cannot beat an otherwise-equal current one on
    authority alone." """
    assert P_SUPERSEDED > W_AUTHORITY


def test_resolved_cases_are_deprioritised_and_never_excluded() -> None:
    """Section 11.3. ``RESOLVED = 0.35`` rather than 0.0.

    The hero scenario turns on retrieving a case resolved four months ago.
    Zeroing resolved cases would make the product's central claim
    undemonstrable, so the salience floor is a product decision, not a tuning
    knob.
    """
    assert CASE_STATE_SALIENCE["RESOLVED"] == 0.35
    assert CASE_STATE_SALIENCE["SUPERSEDED"] > 0.0
    assert CASE_STATE_SALIENCE[None] == 0.50


def test_contradicting_edges_count_double_in_the_grounding_term() -> None:
    """Section 11.3. A contradicting item is exactly the thing the product
    exists to surface, so one ``CONTRADICTS`` edge is worth two ``SUPPORTS``."""
    supports = candidate(grounds_belief_version_ids=("a", "b"))
    contradicts = candidate(contradicts_belief_version_ids=("a",))
    _, supports_parts = rerank.score(supports, now=NOW)
    _, contradicts_parts = rerank.score(contradicts, now=NOW)
    assert contradicts_parts["grounding"] == supports_parts["grounding"]


def test_the_corroboration_bonus_is_capped() -> None:
    """Section 11.3. ``match_strength`` is a max precisely to stop weak-signal
    stacking; an uncapped bonus would reintroduce it through the back door."""
    _, parts = rerank.score(candidate(feature_count=9), now=NOW)
    assert parts["corroboration"] == pytest.approx(B_CORROBORATION_CAP)


def test_negative_cosine_similarity_clamps_to_zero_and_is_not_rescaled() -> None:
    """Section 9.4.

    Rescaling ``(s + 1) / 2`` would award 0.5 to an unrelated document.
    Semantic opposition is not evidence of relevance.
    """
    _, parts = rerank.score(candidate(cosine_similarity=-0.8), now=NOW)
    assert parts["vector"] == 0.0


def test_temporal_credit_halves_over_one_half_life() -> None:
    """Section 11.3. 90 days is one billing quarter: long enough for the
    May-confirmation-versus-June-invoice pairing, short enough that last year's
    correspondence does not compete with this month's."""
    _, near = rerank.score(candidate(temporal_gap_days=0.0), now=NOW)
    _, far = rerank.score(candidate(temporal_gap_days=90.0), now=NOW)
    assert far["temporal"] == pytest.approx(near["temporal"] / 2.0)


def test_a_score_never_goes_negative() -> None:
    """The penalty is subtractive, and a negative rank has no meaning."""
    total, _ = rerank.score(candidate(superseded_grounding=True), now=NOW)
    assert total >= 0.0


# ==========================================================================
# 11.2 -- tiers
# ==========================================================================


def test_tier_assignment_follows_the_spec_ladder() -> None:
    assert rerank.assign_tier(candidate(flag_ref_match=True)) is rerank.Tier.T0_EXACT_IDENTIFIER
    assert rerank.assign_tier(candidate(flag_thread_match=True)) is rerank.Tier.T0_EXACT_IDENTIFIER
    assert (
        rerank.assign_tier(candidate(flag_domain_match=True, flag_temporal_overlap=True))
        is rerank.Tier.T1_DOMAIN_TEMPORAL
    )
    assert (
        rerank.assign_tier(candidate(grounds_belief_version_ids=("a",)))
        is rerank.Tier.T2_GROUNDING_EXPANSION
    )
    assert rerank.assign_tier(candidate()) is rerank.Tier.T3_VECTOR_ONLY


def test_a_domain_match_without_temporal_overlap_is_not_tier_one() -> None:
    """ "The right counterparty, in the right period" is a conjunction.

    Dropping the temporal half would promote every message a counterparty ever
    sent to the tier reserved for the ones that could plausibly be about this.
    """
    assert rerank.assign_tier(candidate(flag_domain_match=True)) is rerank.Tier.T3_VECTOR_ONLY


def test_no_score_can_lift_a_lower_tier_above_a_higher_one() -> None:
    """The point of tiering, at the extreme.

    A ``T3`` candidate with every signal maxed out still loses to a ``T0``
    candidate with nothing but its exact identifier. If this ever passes only
    because of the weights, the tier ordering has been removed.
    """
    best_t3 = candidate(
        match_strength=1.0,
        cosine_similarity=1.0,
        source_authority=1.0,
        case_status="REOPENED",
        flag_temporal_overlap=True,
        feature_count=9,
        observed_at=NOW,
    )
    worst_t0 = candidate(flag_ref_match=True, observed_at=NOW - timedelta(days=3650))
    ordered = rerank.order_candidates([best_t3, worst_t0], now=NOW)
    assert ordered[0] is worst_t0
    assert rerank.score(best_t3, now=NOW)[0] > rerank.score(worst_t0, now=NOW)[0]


# ==========================================================================
# 11.4 -- abstention
# ==========================================================================


def test_no_candidate_at_all_is_unresolved_not_a_guess() -> None:
    status, top, margin, reasons = rerank.decide_identity([])
    assert status == "UNRESOLVED"
    assert (top, margin) == (0.0, 0.0)
    assert reasons


def test_a_top_score_below_the_floor_abstains() -> None:
    """Abstaining is a first-class success.

    A confident wrong binding writes a claim onto the wrong case, mis-grounds a
    belief, and can reopen an unrelated dispute. An abstention costs the user
    one disambiguation tap.
    """
    status, _, _, _ = rerank.decide_identity([TAU_ABSTAIN - 0.01])
    assert status == "UNRESOLVED"


def test_a_clear_winner_resolves() -> None:
    status, top, margin, reasons = rerank.decide_identity(
        [TAU_IDENTITY_ACCEPT + 0.05, TAU_IDENTITY_ACCEPT - TAU_IDENTITY_MARGIN]
    )
    assert status == "RESOLVED"
    assert margin >= TAU_IDENTITY_MARGIN
    assert reasons == ()


def test_two_close_candidates_are_ambiguous_and_say_so() -> None:
    """The Northline Fiber shape: one counterparty, two accounts.

    ``AMBIGUOUS`` routes to the Tier R resolver rather than binding identity,
    which is the whole reason the margin threshold exists separately from the
    acceptance threshold.
    """
    status, _, margin, reasons = rerank.decide_identity([0.95, 0.93])
    assert status == "AMBIGUOUS"
    assert margin < TAU_IDENTITY_MARGIN
    assert reasons


def test_the_abstention_floor_rises_when_the_embedding_is_unavailable() -> None:
    """Section 9.5.

    With Stage D skipped the system has lost its only recall backstop for
    evidence that shares no identifier. Keeping the floor would produce
    confident identity resolutions built on identifier matches alone, and the
    resulting ``PENDING_IDENTITY`` rate would look like a model problem rather
    than an infrastructure one.
    """
    borderline = (TAU_ABSTAIN + TAU_ABSTAIN_DEGRADED) / 2
    assert rerank.decide_identity([borderline], degraded=False)[0] != "UNRESOLVED"
    assert rerank.decide_identity([borderline], degraded=True)[0] == "UNRESOLVED"
