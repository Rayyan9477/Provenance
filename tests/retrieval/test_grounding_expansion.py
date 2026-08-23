"""Stage F — grounding-graph expansion, and the vocabulary it may not blur.

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 11.1 (F.1 to F.4) and section 0's
  vocabulary guard.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 18, test 18.13.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 2.3: grounding and lineage are two
  fields under two names, and "any task whose output conflates the two is
  rejected at review".

Why Stage F exists at all
--------------------------
Vector similarity between a June invoice and a 15 May termination confirmation
is mediocre: different vocabulary, different genre, four months apart. The
confirmation reaches the model because it **grounds the canonical
``service_terminated`` belief on the case the invoice matched**, which is a
graph walk and not a similarity computation. Delete Stage F and the hero demo
fails while every ranking test stays green -- the failure is a missing row, and
a missing row has no error message.

grounding is not lineage
-------------------------
Stage F issues both kinds of query and they are the two the vocabulary guard
separates. **grounding** is ``belief_support`` -- ``SUPPORTS`` /
``CONTRADICTS`` / ``QUALIFIES`` -- and answers *why do you believe this*.
**lineage** is the ``belief_versions`` chain with supersession reasons and
answers *what did you believe before*. They are a different query over a
different table answering a different question, and the tests below fail if
one is rendered as the other.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import pytest

from services.control_plane.app.retrieval import grounding, rerank

pytestmark = [pytest.mark.unit, pytest.mark.retrieval]

NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)

BV_1 = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
BV_2 = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000002")
BELIEF = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001")
EV_TERMINATION = uuid.UUID("cccccccc-0000-4000-8000-00000000000f")
TENANT = uuid.UUID("dddddddd-0000-4000-8000-000000000001")
USER = uuid.UUID("eeeeeeee-0000-4000-8000-000000000001")
CASE_A = uuid.UUID("ffffffff-0000-4000-8000-00000000000a")
CASE_B = uuid.UUID("ffffffff-0000-4000-8000-00000000000b")
CASE_C = uuid.UUID("ffffffff-0000-4000-8000-00000000000c")
CASE_D = uuid.UUID("ffffffff-0000-4000-8000-00000000000d")


def _edge(relation: str, weight: float, source: uuid.UUID | None = None) -> dict[str, object]:
    return {
        "belief_version_id": BV_2,
        "source_kind": "EVIDENCE",
        "source_id": source or uuid.uuid4(),
        "relation": relation,
        "weight": weight,
        "reason_code": None,
    }


# ==========================================================================
# The four statements: scope, and the schema they name
# ==========================================================================


def test_stage_f_issues_the_four_statements_section_11_1_names() -> None:
    """F.1, F.2, F.3 and F.4's three queries. Six statements, one place.

    Named rather than counted so that deleting one is a test failure with the
    missing name in it, rather than an off-by-one in an integer nobody reads.
    """
    assert set(grounding.STATEMENTS) == {
        "F1_CANONICAL_BELIEFS",
        "F2_GROUNDED_EVIDENCE",
        "F3_LINEAGE",
        "F4_CONFLICTS",
        "F4_COMMITMENTS",
        "F4_TRANSITIONS",
    }


def test_every_stage_f_statement_is_scoped_to_tenant_and_user() -> None:
    """Rule R-1 does not stop at the ANN statement.

    Stage F walks a graph, and a graph walk that loses the scope predicate at
    the second hop returns another user's beliefs with results that still look
    entirely plausible. Every one of the six binds both.
    """
    assert len(grounding.STATEMENTS) == 6, (
        "the vacuity guard: `0 offenders` over 0 statements is a lint that "
        "stopped working, and it reports success forever"
    )
    offenders = [
        name
        for name, sql in grounding.STATEMENTS.items()
        if not re.search(r"\btenant_id\s*=\s*\$\d", sql)
        or not re.search(r"\buser_id\s*=\s*\$\d", sql)
    ]
    assert not offenders, f"Stage F statements unscoped by tenant or user: {offenders}"


def test_the_grounding_evidence_statement_filters_retracted_rows() -> None:
    """R-2 on the backstop query, which is the easiest place to forget it.

    F.2 is reached *through an edge* rather than through a similarity search,
    so it feels like a lookup by id rather than like retrieval -- and a lookup
    by id is exactly the shape someone writes without a lifecycle predicate.
    The edge stays in the database as historical grounding for State Proof; it
    does not re-enter retrieval.
    """
    assert "retraction_status = 'ACTIVE'" in grounding.STATEMENTS["F2_GROUNDED_EVIDENCE"]


def test_the_spec_limits_are_the_shipped_limits() -> None:
    """Section 11.1's four numbers, and one reason each.

    12 grounded evidence rows, 3 lineage versions per belief, 6 conflicts, 6
    commitments, 9 transitions (3 cases x 3). They bound a graph walk, which is
    the one stage that can otherwise return an unbounded amount of context.
    """
    assert grounding.GROUNDED_EVIDENCE_LIMIT == 12
    assert grounding.LINEAGE_DEPTH == 3
    assert grounding.CONFLICT_LIMIT == 6
    assert grounding.COMMITMENT_LIMIT == 6
    assert grounding.TRANSITION_LIMIT == 9


def test_days_past_due_is_computed_in_sql_and_never_by_the_model() -> None:
    """Section 11.1's closing note, asserted where it can be broken.

    "A model that 'reasons' its way to 64 days past due and a ``date_trunc``
    subtraction are not the same engineering artifact." The commitments
    statement carries the subtraction, so the number in the context window is a
    computed value the Memory Trace can re-derive.
    """
    sql = grounding.STATEMENTS["F4_COMMITMENTS"]
    assert "days_past_due" in sql
    assert "date_trunc" in sql


# ==========================================================================
# grounding is not lineage
# ==========================================================================


def test_grounding_and_lineage_are_two_fields_under_two_names() -> None:
    """The vocabulary guard, as a shape assertion.

    ``StageFExpansion`` carries both, under exactly the two names ``StateProof``
    uses, loaded by two functions from two tables.
    """
    expansion = grounding.StageFExpansion(
        grounding=(grounding.GroundingEdge.from_row(_edge("SUPPORTS", 0.9)),),
        lineage=(
            grounding.LineageStep.from_row(
                {
                    "belief_id": BELIEF,
                    "belief_version_id": BV_1,
                    "version_no": 1,
                    "value_json": {"state": "ACTIVE"},
                    "epistemic_status": "SUPERSEDED",
                    "recorded_at": NOW,
                    "superseded_at": NOW,
                    "supersession_reason_codes": ["CORRECTION"],
                }
            ),
        ),
    )
    assert [type(item).__name__ for item in expansion.grounding] == ["GroundingEdge"]
    assert [type(item).__name__ for item in expansion.lineage] == ["LineageStep"]
    assert expansion.grounding[0].relation == "SUPPORTS"
    assert expansion.lineage[0].version_no == 1


def test_a_merged_grounding_and_lineage_render_is_refused() -> None:
    """The merge is what a well-meaning simplification looks like in a diff.

    Both are "the history of this belief", both render as a list under a
    heading, and a reviewer skimming would not stop it. What it destroys is the
    product: a user disputing a charge needs the evidence, and a system that
    hands them a version history has answered a different question with total
    confidence.
    """
    expansion = grounding.StageFExpansion(
        grounding=(grounding.GroundingEdge.from_row(_edge("CONTRADICTS", 0.95)),),
        lineage=(),
    )
    with pytest.raises(
        grounding.GroundingLineageMergeError, match="(?i)changelog is not an argument"
    ):
        expansion.merged()


def test_a_lineage_row_cannot_be_loaded_as_a_grounding_edge() -> None:
    """Type-level separation, not naming discipline.

    A ``belief_versions`` row has no ``relation``, so feeding one to the
    grounding loader fails loudly rather than producing an edge with a
    plausible-looking ``None`` relation that sorts last and disappears.
    """
    lineage_row = {
        "belief_id": BELIEF,
        "belief_version_id": BV_1,
        "version_no": 1,
        "epistemic_status": "SUPERSEDED",
        "recorded_at": NOW,
    }
    with pytest.raises(KeyError, match="relation"):
        grounding.GroundingEdge.from_row(lineage_row)


# ==========================================================================
# Ordering and truncation: the contradiction is never what gets eaten
# ==========================================================================


def test_contradicts_edges_sort_before_supports_and_qualifies() -> None:
    """F.1's ``ORDER BY``, reproduced in process so a client-side re-sort keeps it.

    A contradiction is the highest-value thing retrieval can surface. Sorting
    it below four supporting documents technically discloses it and practically
    buries it.
    """
    edges = [
        grounding.GroundingEdge.from_row(_edge("QUALIFIES", 0.99)),
        grounding.GroundingEdge.from_row(_edge("SUPPORTS", 0.98)),
        grounding.GroundingEdge.from_row(_edge("CONTRADICTS", 0.10)),
    ]
    assert [edge.relation for edge in grounding.order_edges(edges)] == [
        "CONTRADICTS",
        "SUPPORTS",
        "QUALIFIES",
    ]


def test_within_one_relation_the_heavier_edge_comes_first() -> None:
    """Weight orders within a relation and never across one."""
    edges = [
        grounding.GroundingEdge.from_row(_edge("SUPPORTS", 0.20)),
        grounding.GroundingEdge.from_row(_edge("SUPPORTS", 0.80)),
    ]
    assert [edge.weight for edge in grounding.order_edges(edges)] == [0.80, 0.20]


def test_a_null_weight_sorts_last_within_its_relation() -> None:
    """``NULLS LAST``, reproduced. An unweighted edge is not a zero-weight one,
    but it is less informative than a measured one and must not displace it."""
    edges = [
        grounding.GroundingEdge.from_row(_edge("SUPPORTS", None)),  # type: ignore[arg-type]
        grounding.GroundingEdge.from_row(_edge("SUPPORTS", 0.10)),
    ]
    assert [edge.weight for edge in grounding.order_edges(edges)] == [0.10, None]


def test_truncation_eats_qualifies_then_supports_and_never_contradicts() -> None:
    """Section 11.1: "the truncation must eat ``QUALIFIES`` and then
    ``SUPPORTS`` -- never the edge that says the system disagrees with itself".

    Asserted at the extreme rather than at a plausible midpoint: a budget of
    one, with two contradictions present, keeps a contradiction.
    """
    edges = [
        grounding.GroundingEdge.from_row(_edge("QUALIFIES", 0.99)),
        grounding.GroundingEdge.from_row(_edge("SUPPORTS", 0.99)),
        grounding.GroundingEdge.from_row(_edge("CONTRADICTS", 0.01)),
    ]
    kept, dropped = grounding.truncate_edges(edges, limit=1)
    assert [edge.relation for edge in kept] == ["CONTRADICTS"]
    assert sorted(edge.relation for edge in dropped) == ["QUALIFIES", "SUPPORTS"]


def test_truncating_to_zero_is_refused_rather_than_silently_emptying() -> None:
    """An empty grounding list and "this belief has no grounding" are the same
    JSON and different facts. The first is a budget event and the second is an
    invariant violation, so the budget path may not produce it."""
    with pytest.raises(ValueError, match="(?i)at least one"):
        grounding.truncate_edges(
            [grounding.GroundingEdge.from_row(_edge("SUPPORTS", 0.5))], limit=0
        )


# ==========================================================================
# The backstop candidates themselves — test 18.13's two properties
# ==========================================================================


def test_expansion_candidates_are_tier_two_with_no_vector_contribution() -> None:
    """Test 18.13, in the unit lane.

    Evidence pulled in by F.2 "enters the candidate pool with ``tier =
    T2_GROUNDING_EXPANSION``, ``cosine_similarity`` recorded as ``None``, and a
    ``vector_feature`` of 0.0". Recording 0.0 as a *similarity* would be a lie
    -- the item was never scored against the query vector at all -- and it would
    be indistinguishable from a genuinely orthogonal document.
    """
    candidate = grounding.expansion_candidate(
        {
            "evidence_id": str(EV_TERMINATION),
            "belief_version_id": BV_2,
            "relation": "SUPPORTS",
            "weight": 0.9,
            "source_authority": 0.95,
            "observed_at": NOW,
        },
        case_status="RESOLVED",
    )
    assert candidate.cosine_similarity is None
    assert rerank.assign_tier(candidate) is rerank.Tier.T2_GROUNDING_EXPANSION
    _total, parts = rerank.score(candidate, now=NOW)
    assert parts["vector"] == 0.0


def test_a_contradicting_expansion_candidate_is_still_tier_two() -> None:
    """A ``CONTRADICTS`` edge is grounding, so it lands in the same tier and
    earns its priority from the doubled grounding term rather than from a
    special tier that would let it outrank an exact identifier match."""
    candidate = grounding.expansion_candidate(
        {
            "evidence_id": str(EV_TERMINATION),
            "belief_version_id": BV_2,
            "relation": "CONTRADICTS",
            "weight": 0.9,
            "source_authority": 0.95,
            "observed_at": NOW,
        },
        case_status="RESOLVED",
    )
    assert candidate.contradicts_belief_version_ids == (str(BV_2),)
    assert candidate.grounds_belief_version_ids == ()
    assert rerank.assign_tier(candidate) is rerank.Tier.T2_GROUNDING_EXPANSION


def test_a_qualifying_edge_is_grounding_too_and_lands_in_tier_two() -> None:
    """``QUALIFIES`` is a ``belief_support`` row like the other two.

    Reading section 11.2's ladder literally -- ``grounds_belief_version_ids or
    contradicts_belief_version_ids`` -- would drop a qualifying edge to
    ``T3_VECTOR_ONLY``, where it competes for the two slots reserved for
    genuinely novel evidence against evidence that genuinely is novel. It
    arrived through the grounding graph; it belongs in the grounding tier.
    """
    candidate = grounding.expansion_candidate(
        {
            "evidence_id": str(EV_TERMINATION),
            "belief_version_id": BV_2,
            "relation": "QUALIFIES",
            "weight": 0.4,
            "source_authority": 0.7,
            "observed_at": NOW,
        },
        case_status="OPEN",
    )
    assert rerank.assign_tier(candidate) is rerank.Tier.T2_GROUNDING_EXPANSION


def test_a_fourth_relation_is_refused_rather_than_scored() -> None:
    """The three relations are closed.

    A fourth would be scored as grounding without anybody having decided what
    it means, and it would sort somewhere arbitrary while reading as ranked.
    """
    with pytest.raises(KeyError, match="(?i)relation"):
        grounding.expansion_candidate(
            {
                "evidence_id": str(EV_TERMINATION),
                "belief_version_id": BV_2,
                "relation": "MENTIONS",
                "observed_at": NOW,
            },
            case_status="OPEN",
        )


def test_an_expansion_candidate_never_claims_an_identity_match() -> None:
    """``match_strength`` is 0.0 and the flags are false.

    The item arrived through the graph, not through an identifier. Awarding it
    identity credit here would let Stage F promote a candidate into ``T0`` and
    quietly reintroduce adjudication-by-accumulation through a side door.
    """
    candidate = grounding.expansion_candidate(
        {
            "evidence_id": str(EV_TERMINATION),
            "belief_version_id": BV_2,
            "relation": "SUPPORTS",
            "weight": 0.9,
            "source_authority": 0.95,
            "observed_at": NOW,
        },
        case_status="OPEN",
    )
    assert candidate.match_strength == 0.0
    assert not candidate.flag_ref_match
    assert not candidate.flag_thread_match
    assert not candidate.flag_domain_match


# ==========================================================================
# Parameter binding
# ==========================================================================


def test_at_most_three_cases_are_expanded() -> None:
    """Section 11.1: ``$3`` is "at most 3 after Stage G's provisional ordering".

    The walk fans out per case, so a fourth case is not a slightly larger
    context -- it is four more statements' worth of rows competing for ten
    slots, and the reserve that protects novel evidence loses first.
    """
    assert grounding.expansion_cases([CASE_A, CASE_B, CASE_C, CASE_D]) == (CASE_A, CASE_B, CASE_C)


def test_evidence_already_returned_by_stage_d_is_not_refetched() -> None:
    """``$5`` is the exclusion list, and it is a correctness device as well as a
    performance one: the same evidence arriving twice would be counted twice by
    the corroboration bonus and would occupy two of ten slots."""
    params = grounding.bind_grounded_evidence(
        tenant_id=TENANT,
        user_id=USER,
        belief_version_ids=[BV_1, BV_2],
        already_present=[EV_TERMINATION],
    )
    assert params == {
        "tenant_id": TENANT,
        "user_id": USER,
        "belief_version_ids": [BV_1, BV_2],
        "already_present": [EV_TERMINATION],
    }


def test_an_empty_exclusion_list_is_an_empty_array_and_never_null() -> None:
    """``NOT (ev.id = ANY(NULL))`` is ``NULL``, which is not true, which drops
    every row. The backstop would return nothing and the hero demo would fail
    with no error -- so the empty case is an empty array, asserted."""
    params = grounding.bind_grounded_evidence(
        tenant_id=TENANT, user_id=USER, belief_version_ids=[BV_1], already_present=[]
    )
    assert params["already_present"] == []


def test_the_case_scoped_statements_share_one_parameter_shape() -> None:
    """F.1 and F.4's three queries all take ``($1 tenant, $2 user, $3 case ids)``.

    One binder for four statements is what stops the fourth acquiring a
    different parameter order during a later edit. Parameters are bound **by
    name**: the failure this removes is a ``user_id`` bound where a
    ``tenant_id`` belongs, which is valid SQL, returns zero rows, and reads as
    an empty case rather than as a bug.
    """
    params = grounding.bind_case_scoped(tenant_id=TENANT, user_id=USER, case_ids=[CASE_A, CASE_B])
    assert params == {"tenant_id": TENANT, "user_id": USER, "case_ids": [CASE_A, CASE_B]}
    for name in ("F1_CANONICAL_BELIEFS", "F4_CONFLICTS", "F4_COMMITMENTS", "F4_TRANSITIONS"):
        assert grounding.parameter_names(name) == ("tenant_id", "user_id", "case_ids"), name


def test_the_lineage_binder_takes_belief_ids_not_version_ids() -> None:
    """F.3 is keyed on ``belief_id``; F.2 is keyed on ``belief_version_id``.

    Positionally these are indistinguishable -- both are UUID arrays, and both
    statements are valid SQL with the other one's array bound into them,
    returning zero rows. Binding by name is what turns that silent emptiness
    into a ``KeyError``, and these two assertions are what keep the names apart.
    """
    params = grounding.bind_lineage(tenant_id=TENANT, user_id=USER, belief_ids=[BELIEF])
    assert params == {"tenant_id": TENANT, "user_id": USER, "belief_ids": [BELIEF]}
    assert grounding.parameter_names("F3_LINEAGE") == ("tenant_id", "user_id", "belief_ids")
    assert grounding.parameter_names("F2_GROUNDED_EVIDENCE") == (
        "tenant_id",
        "user_id",
        "belief_version_ids",
        "already_present",
    )
