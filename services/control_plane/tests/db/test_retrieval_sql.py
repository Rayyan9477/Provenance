"""Shape of the one sanctioned ANN retrieval predicate (``T2.2``).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 5.1 (the vector index) and section
  5.5 (the exact retrieval predicate, with the four rules that make it correct).
- ``ops/decisions/VECTOR_INDEX_VARIANT.md`` - **VARIANT: A**, decided by probe
  PB-2 against the live cluster. Not re-probed here.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5, ``T2.2``.

Why these tests exist now, with no data
---------------------------------------
Two of the failure modes in this area are *silent*. A predicate that moves
``user_id`` out of the CTE still returns correct rows - it just scans every
user's partition to do it. And ``D-06-001``, proven on this cluster, is worse:
an ANN query vector supplied as a **subquery** rather than a literal or a bound
parameter produces a full scan with correct results, no error and no warning.
Neither shows up in a result-set assertion, at any corpus size. Both show up in
``EXPLAIN``, which needs no rows.

``G6.2`` asserts that the plan names the index. It only catches ``D-06-001`` if
the assertion runs against the production query shape, parameter binding
included - which is what ``test_canonical_predicate_is_executable`` and
``test_query_vector_as_a_subquery_silently_loses_the_index`` pin down here, four
phases early.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable

import psycopg
import pytest

pytestmark = pytest.mark.db

ANN_INDEX = "evidence_embedding_ann_idx"
EMBEDDING_DIMENSIONS = 1024

#: A well-formed but arbitrary query vector. Its *values* are irrelevant: every
#: assertion below is about the plan, never about which rows come back.
QUERY_VECTOR = "[" + ",".join(["0.0125"] * EMBEDDING_DIMENSIONS) + "]"

HERO_USER = uuid.UUID("00000000-0000-4000-8000-000000000001")
HERO_TENANT = uuid.UUID("00000000-0000-4000-8000-0000000000ff")

_PARAM = re.compile(r"\$(\d)")


def _sql_blocks(markdown: str) -> list[str]:
    return re.findall(r"```sql\n(.*?)```", markdown, re.S)


def _canonical_predicate(markdown: str) -> str:
    """The single section 5.5 block. Exactly one block may match."""
    blocks = [block for block in _sql_blocks(markdown) if "WITH ann AS (" in block]
    assert (
        len(blocks) == 1
    ), f"expected exactly one section 5.5 predicate block, found {len(blocks)}"
    return blocks[0]


def _split_cte(predicate: str) -> tuple[str, str]:
    """``(inside the ann CTE, the outer query)``.

    Split on the closing parenthesis of the CTE, which is the only line in the
    block that is a bare ``)``. Doing it structurally rather than by line number
    means an edit to the spec cannot silently move the boundary.
    """
    lines = predicate.splitlines()
    closers = [index for index, line in enumerate(lines) if line.strip() == ")"]
    assert closers, f"cannot find the end of the ann CTE in:\n{predicate}"
    boundary = closers[0]
    return "\n".join(lines[: boundary + 1]), "\n".join(lines[boundary + 1 :])


def _strip_sql_comments(sql: str) -> str:
    """Drop ``--`` line comments. Used before counting bind placeholders."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _explain(connection: psycopg.Connection, sql: str) -> str:
    with connection.cursor() as cur:
        cur.execute("EXPLAIN (VERBOSE) " + sql)
        return "\n".join(str(row[0]) for row in cur.fetchall())


@pytest.fixture
def explain(db_connection) -> Callable[[str], str]:
    def _run(sql: str) -> str:
        return _explain(db_connection, sql)

    return _run


# ==========================================================================
# The decision and the index it selected
# ==========================================================================


def test_variant_a_is_the_decided_variant(variant_decision_text: str) -> None:
    """``T0.6`` decided Variant A against the live cluster. ``T2.2`` does not re-litigate."""
    assert re.search(
        r"(?m)^VARIANT:\s*A\s*$", variant_decision_text
    ), "ops/decisions/VECTOR_INDEX_VARIANT.md no longer records VARIANT: A"


def test_spec_ann_index_ddl_prefixes_user_id(ddl_spec_text: str) -> None:
    """Section 5.1: ``(user_id, embedding vector_cosine_ops)`` - prefix first.

    The prefix is not a performance hint. It is the mechanism by which
    approximate nearest-neighbour search physically cannot return another user's
    evidence, so invariant I7 does not rest on a WHERE clause a refactor can drop.
    """
    candidates = [
        block
        for block in _sql_blocks(ddl_spec_text)
        if f"CREATE VECTOR INDEX {ANN_INDEX}" in block and "vector_cosine_ops" in block
    ]
    assert len(candidates) == 1, f"expected one Variant A block, found {len(candidates)}"
    variant_a = candidates[0]
    match = re.search(r"ON\s+evidence_items\s*\(\s*(\w+)", variant_a)
    assert match is not None, f"cannot parse the Variant A index DDL:\n{variant_a}"
    assert match.group(1) == "user_id"


def test_optional_active_prefix_index_is_not_adopted(variant_decision_text: str) -> None:
    """Index variant R stays unadopted until a recall evaluation justifies it."""
    assert "unadopted" in variant_decision_text.lower()


# ==========================================================================
# Shape of the section 5.5 predicate
# ==========================================================================


def test_canonical_predicate_block_exists(ddl_spec_text: str) -> None:
    """One sanctioned ANN query shape, in one place."""
    predicate = _canonical_predicate(ddl_spec_text)
    assert "FROM evidence_items" in predicate
    assert "LIMIT" in predicate


def test_user_id_equality_is_inside_the_cte(ddl_spec_text: str) -> None:
    """Section 5.5 rule 1. Inside, this matches the index prefix."""
    inside, _ = _split_cte(_canonical_predicate(ddl_spec_text))
    assert re.search(
        r"WHERE\s+user_id\s*=\s*\$1", inside
    ), f"user_id equality is not inside the ann CTE:\n{inside}"


def test_user_id_equality_is_not_in_the_outer_query(ddl_spec_text: str) -> None:
    """Section 5.5 rule 1, the other half.

    Moving it out breaks the index-prefix match and turns an ANN lookup into a
    full scan across every user - with identical results, which is why this is
    asserted structurally rather than behaviourally.
    """
    _, outer = _split_cte(_canonical_predicate(ddl_spec_text))
    assert not re.search(r"\buser_id\s*=", outer), f"user_id equality leaked outside:\n{outer}"


def test_non_prefix_filters_are_outside_the_cte(ddl_spec_text: str) -> None:
    """Section 5.5 rule 2: ``tenant_id``, ``retraction_status``, ``embedding_version``.

    Adding non-prefix predicates to the ANN block can prevent the vector index
    from being chosen at all.
    """
    inside, outer = _split_cte(_canonical_predicate(ddl_spec_text))
    assert re.search(r"tenant_id\s*=\s*\$2", outer)
    assert re.search(r"retraction_status\s*=\s*'ACTIVE'", outer)
    assert re.search(r"embedding_version\s*=\s*\$6", outer)
    assert "retraction_status = 'ACTIVE'" not in inside


def test_retraction_filter_is_present_at_all(ddl_spec_text: str) -> None:
    """Canon item C: a retracted item's vector stays in the ANN index.

    Filtering is a hard requirement of the query, not a refinement. Without it,
    corrected evidence resurfaces and the Interpreter re-derives the mistake the
    user already fixed.
    """
    assert "retraction_status = 'ACTIVE'" in _canonical_predicate(ddl_spec_text)


def test_predicate_has_no_embedding_is_not_null(ddl_spec_text: str) -> None:
    """Section 5.5 rule 4. Rows without a vector are not in the index anyway.

    Adding the predicate risks disqualifying the index without changing the result.
    """
    predicate = _canonical_predicate(ddl_spec_text)
    assert not re.search(r"(?i)embedding\s+IS\s+NOT\s+NULL", predicate)


def test_cte_orders_by_the_cosine_distance_operator(ddl_spec_text: str) -> None:
    """``<=>`` against the bound query vector, inside the CTE."""
    inside, _ = _split_cte(_canonical_predicate(ddl_spec_text))
    assert re.search(r"ORDER BY\s+embedding\s*<=>\s*\$3", inside)


def test_over_fetch_uses_two_limits(ddl_spec_text: str) -> None:
    """Section 5.5 rule 3. ANN returns ``k_raw`` candidates *before* filtering.

    If ``k_raw == k_final`` a run of retracted near-neighbours silently shrinks
    the result set.
    """
    inside, outer = _split_cte(_canonical_predicate(ddl_spec_text))
    assert re.search(r"LIMIT\s+\$4", inside), "the CTE does not over-fetch"
    assert re.search(r"LIMIT\s+\$5", outer), "the outer query does not apply k_final"


def test_k_raw_formula_is_documented(ddl_spec_text: str) -> None:
    """``k_raw = greatest(40, 4 * k_final)``, ``k_final = 20`` for the demo corpus."""
    predicate = _canonical_predicate(ddl_spec_text)
    assert "greatest(40, 4 * k_final)" in predicate
    assert "k_final" in predicate


# ==========================================================================
# The live planner - no rows required
# ==========================================================================


def test_ann_query_with_a_literal_vector_uses_the_vector_index(
    explain: Callable[[str], str],
) -> None:
    """``T2.2`` acceptance, and the shape ``G6.2`` will assert in Phase 6."""
    plan = explain(
        "SELECT id FROM evidence_items "
        f"WHERE user_id = '{HERO_USER}' "
        f"ORDER BY embedding <=> '{QUERY_VECTOR}'::VECTOR({EMBEDDING_DIMENSIONS}) "
        "LIMIT 40"
    )
    assert ANN_INDEX in plan, f"the plan does not name {ANN_INDEX}:\n{plan}"
    assert "vector search" in plan, f"the plan is not a vector search:\n{plan}"


def test_ann_query_with_a_literal_vector_is_not_a_full_scan(
    explain: Callable[[str], str],
) -> None:
    """A ``FULL SCAN`` node here means the prefix or the opclass does not match."""
    plan = explain(
        "SELECT id FROM evidence_items "
        f"WHERE user_id = '{HERO_USER}' "
        f"ORDER BY embedding <=> '{QUERY_VECTOR}'::VECTOR({EMBEDDING_DIMENSIONS}) "
        "LIMIT 40"
    )
    assert "FULL SCAN" not in plan.upper(), f"the ANN query degraded to a full scan:\n{plan}"


def test_user_id_prefix_constrains_the_ann_partition(explain: Callable[[str], str]) -> None:
    """The prefix span is the physical isolation boundary, visible in the plan."""
    plan = explain(
        "SELECT id FROM evidence_items "
        f"WHERE user_id = '{HERO_USER}' "
        f"ORDER BY embedding <=> '{QUERY_VECTOR}'::VECTOR({EMBEDDING_DIMENSIONS}) "
        "LIMIT 40"
    )
    assert "prefix spans" in plan, f"the ANN scan is not prefix-constrained:\n{plan}"


def test_query_vector_as_a_subquery_silently_loses_the_index(
    explain: Callable[[str], str],
) -> None:
    """``D-06-001``, reproduced against the schema this migration creates.

    Correct results, no error, no warning - only latency, which is invisible at
    demo scale. Phase 6 must embed first and pass the vector as a bound
    parameter, never computing it inside the ranking statement.
    """
    plan = explain(
        "SELECT id FROM evidence_items "
        f"WHERE user_id = '{HERO_USER}' "
        "ORDER BY embedding <=> "
        f"(SELECT embedding FROM evidence_items WHERE id = '{HERO_TENANT}') "
        "LIMIT 40"
    )
    assert "vector search" not in plan, (
        "D-06-001 no longer reproduces: a subquery query vector now uses the vector "
        f"index. Re-verify the defect before relaxing any Phase 6 rule.\n{plan}"
    )


def test_canonical_predicate_is_executable(db_connection, ddl_spec_text: str) -> None:
    """Run the section 5.5 predicate verbatim, with bound parameters, on empty tables.

    ``$3`` is cast explicitly because CockroachDB needs the target width to
    resolve ``<=>``; everything else binds as-is. Zero rows is the correct
    answer here - the point is that the statement the repository will emit is
    the statement the schema accepts.

    The block's leading ``-- Parameters`` header names ``$1`` through ``$6`` in
    comments. :func:`_strip_sql_comments` runs first because psycopg counts
    placeholders by scanning the string and does not know a comment from a
    predicate - leaving the header in binds thirteen values into a statement
    with seven placeholders, which the server reports as
    ``could not determine data type of placeholder $3``.
    """
    predicate = _strip_sql_comments(_canonical_predicate(ddl_spec_text))
    order = [int(token) for token in _PARAM.findall(predicate)]
    assert order, "no bind parameters survived comment stripping"
    values = {
        1: HERO_USER,
        2: HERO_TENANT,
        3: QUERY_VECTOR,
        4: 40,
        5: 20,
        6: "v1",
    }
    sql = _PARAM.sub(
        lambda match: (f"%s::VECTOR({EMBEDDING_DIMENSIONS})" if match.group(1) == "3" else "%s"),
        predicate,
    )
    with db_connection.cursor() as cur:
        cur.execute(sql, [values[index] for index in order])
        assert cur.fetchall() == []
