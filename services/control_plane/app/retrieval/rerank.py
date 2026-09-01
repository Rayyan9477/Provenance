"""Stage G — tier assignment, the scoring function, and abstention (``T6.4``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 11.2, 11.3 and 11.4.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T6.4``, fourth sub-task.

Tiers first, score second, and the order is a guard
----------------------------------------------------
Weights alone cannot guarantee that an exact account-number match outranks a
semantically gorgeous but structurally unrelated document: with enough weak
signals, any linear score can be beaten. So ordering is **lexicographic on tier
first**, and only then on score. The score orders *within* a tier; it never
crosses one.

That is the difference between this and a RAG reranker. A reranker with these
same weights would still let a beautifully-worded irrelevant document win on a
day when the numbers lined up. Here it cannot, and
``test_no_score_can_lift_a_lower_tier_above_a_higher_one`` asserts it at the
extreme rather than at a plausible midpoint.

Honest statement about the nine constants
------------------------------------------
Section 11.3 says it, ``config.py`` says it, and section 20 risk R5 says it a
third time because the failure is a reader's inference rather than a code
defect: **these weights are declared engineering priors, not fitted
parameters.** They were chosen to produce the correct ordering on the hero
corpus and to encode the product's stated priorities.

Abstention is an output, not a failure
---------------------------------------
A confident wrong binding writes a claim onto the wrong case, mis-grounds a
belief and can reopen an unrelated dispute. An abstention costs the user one
disambiguation tap. The asymmetry is the whole argument, and it is why
:func:`decide_identity` returns ``UNRESOLVED`` with reasons rather than
returning its best guess with a low score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum

from services.control_plane.app.retrieval.config import (
    B_CORROBORATION,
    B_CORROBORATION_CAP,
    CASE_STATE_SALIENCE,
    P_SUPERSEDED,
    RECENCY_HALF_LIFE_DAYS,
    TAU_ABSTAIN,
    TAU_ABSTAIN_DEGRADED,
    TAU_IDENTITY_ACCEPT,
    TAU_IDENTITY_MARGIN,
    TEMPORAL_HALF_LIFE_DAYS,
    W_AUTHORITY,
    W_GROUNDING,
    W_IDENTITY,
    W_RECENCY,
    W_STATE,
    W_TEMPORAL,
    W_VECTOR,
)

__all__ = ["Candidate", "Tier", "assign_tier", "decide_identity", "order_candidates", "score"]


class Tier(IntEnum):
    """Ordered, and the ordering is the point. Lower value wins.

    ``IntEnum`` rather than ``StrEnum``: the sort key is ``(tier, -score)`` and
    a string tier would sort alphabetically, which puts ``T0`` before ``T1``
    today and breaks the moment a tier is renamed.
    """

    T0_EXACT_IDENTIFIER = 0
    T1_DOMAIN_TEMPORAL = 1
    T2_GROUNDING_EXPANSION = 2
    T3_VECTOR_ONLY = 3


@dataclass(frozen=True)
class Candidate:
    """One Stage E survivor, with every input the score needs and nothing else.

    Deliberately a flat record of already-computed facts. The score must be
    reproducible from what the Memory Trace displays, so a term that needed a
    database round trip to explain would be a term nobody could check.
    """

    match_strength: float
    cosine_similarity: float | None
    source_authority: float | None
    case_status: str | None
    flag_ref_match: bool = False
    flag_thread_match: bool = False
    flag_domain_match: bool = False
    flag_temporal_overlap: bool = False
    grounds_belief_version_ids: tuple[str, ...] = ()
    contradicts_belief_version_ids: tuple[str, ...] = ()
    observed_at: datetime | None = None
    feature_count: int = 0
    superseded_grounding: bool = False
    temporal_gap_days: float = 0.0
    evidence_id: str | None = None
    notes: tuple[str, ...] = field(default=())


def assign_tier(candidate: Candidate) -> Tier:
    """Section 11.2's ladder, in its order.

    ``T0`` is an identifier the counterparty itself printed on the document, or
    a thread Provenance already holds; nothing semantic outranks it. ``T1`` is
    "the right counterparty, in the right period" -- a conjunction, because the
    domain alone promotes every message that counterparty ever sent. ``T2`` is
    "this is what the current canonical belief rests on". ``T3`` is pure
    semantics, which is where an ordinary RAG system starts and where this one
    ends.
    """
    if candidate.flag_ref_match or candidate.flag_thread_match:
        return Tier.T0_EXACT_IDENTIFIER
    if candidate.flag_domain_match and candidate.flag_temporal_overlap:
        return Tier.T1_DOMAIN_TEMPORAL
    if candidate.grounds_belief_version_ids or candidate.contradicts_belief_version_ids:
        return Tier.T2_GROUNDING_EXPANSION
    return Tier.T3_VECTOR_ONLY


def score(candidate: Candidate, *, now: datetime) -> tuple[float, dict[str, float]]:
    """Seven positive terms, one subtractive penalty, one capped bonus.

    Returns the total and the per-term breakdown. The breakdown is not
    diagnostic decoration: it is carried on every ``EvidenceSnippet`` and
    rendered in the Memory Trace, because a ranking that cannot be explained
    term by term is a ranking nobody will trust under scrutiny.
    """
    identity = candidate.match_strength
    vector = max(0.0, min(1.0, candidate.cosine_similarity or 0.0))
    authority = float(candidate.source_authority or 0.0)
    state = CASE_STATE_SALIENCE.get(candidate.case_status, 0.50)

    if candidate.flag_temporal_overlap:
        temporal = 1.0
    else:
        temporal = 0.5 ** (max(0.0, candidate.temporal_gap_days) / TEMPORAL_HALF_LIFE_DAYS)

    # A contradicting item is exactly the thing the product exists to surface,
    # so its edge counts double.
    edge_weight = len(candidate.grounds_belief_version_ids) + 2 * len(
        candidate.contradicts_belief_version_ids
    )
    grounding = min(1.0, edge_weight / 3.0)

    if candidate.observed_at is None:
        recency = 0.0
    else:
        age_days = max(0.0, (now - candidate.observed_at).total_seconds() / 86400.0)
        recency = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)

    corroboration = min(B_CORROBORATION_CAP, B_CORROBORATION * max(0, candidate.feature_count - 1))
    superseded = 1.0 if candidate.superseded_grounding else 0.0

    parts = {
        "identity": W_IDENTITY * identity,
        "vector": W_VECTOR * vector,
        "authority": W_AUTHORITY * authority,
        "state": W_STATE * state,
        "temporal": W_TEMPORAL * temporal,
        "grounding": W_GROUNDING * grounding,
        "recency": W_RECENCY * recency,
        "corroboration": corroboration,
        "superseded_penalty": -P_SUPERSEDED * superseded,
    }
    return max(0.0, sum(parts.values())), parts


def order_candidates(candidates: Sequence[Candidate], *, now: datetime) -> list[Candidate]:
    """Lexicographic on ``(tier, -score)``.

    The tier component is what stops a linear score adjudicating identity. If
    this ever becomes a single sorted-by-score call, the guard is gone and the
    system has quietly become a reranker.
    """
    return sorted(candidates, key=lambda c: (assign_tier(c).value, -score(c, now=now)[0]))


def decide_identity(
    scores: Sequence[float], *, degraded: bool = False
) -> tuple[str, float, float, tuple[str, ...]]:
    """``(identity_status, top, margin, reasons)``. Section 11.4.

    The three statuses map onto downstream behaviour with no further
    interpretation: ``RESOLVED`` binds the case, ``AMBIGUOUS`` invokes the Tier
    R resolver, ``UNRESOLVED`` builds a proposal with no case binding and the
    kernel routes it to ``PENDING_IDENTITY``.

    *degraded* raises the abstention floor from 0.42 to 0.62. With Stage D
    skipped the system has lost its only recall backstop for evidence that
    shares no identifier, and keeping the floor would produce confident
    resolutions built on identifier matches alone -- whose failure rate would
    then look like a model problem rather than an infrastructure one.
    """
    if not scores:
        return (
            "UNRESOLVED",
            0.0,
            0.0,
            ("no candidate case matched any deterministic feature",),
        )

    ordered = sorted(scores, reverse=True)
    top = ordered[0]
    margin = top - (ordered[1] if len(ordered) > 1 else 0.0)
    floor = TAU_ABSTAIN_DEGRADED if degraded else TAU_ABSTAIN

    if top < floor:
        return (
            "UNRESOLVED",
            top,
            margin,
            (f"best case candidate scored {top:.2f}, below abstention floor {floor:.2f}",),
        )
    if top >= TAU_IDENTITY_ACCEPT and margin >= TAU_IDENTITY_MARGIN:
        return ("RESOLVED", top, margin, ())
    return (
        "AMBIGUOUS",
        top,
        margin,
        (
            f"top two case candidates within {margin:.2f}; "
            "strong resolution required before a proposal binds identity",
        ),
    )
