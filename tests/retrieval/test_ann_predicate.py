"""Stage D — the index is chosen, by name, with a bound parameter (``T6.3``).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 5.5 -- the only sanctioned ANN query
  shape, and the four rules that make it correct.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.2``: "a line naming the index. A
  ``full scan`` line here is a FAILURE even if results are correct."
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T6.3``, and defect ``D-06-001``.

``D-06-001``, and why ``G6.2`` only catches it here
----------------------------------------------------
An ANN query vector supplied as a **correlated subquery** silently produces a
full scan. Correct results, no error, no warning; it survives ``ANALYZE`` and
reproduces at 3 and at 1024 dimensions. Nothing in a result-set assertion can
see it, at any corpus size -- only latency changes, and latency is invisible at
demo scale.

``G6.2`` asserts that the plan names the index, and it only catches this if the
assertion runs against the **production query shape, parameter binding
included**. So this file EXPLAINs the statement
``services.control_plane.app.retrieval.ann`` actually emits, with psycopg
binding the vector as a parameter -- and then EXPLAINs the subquery form beside
it and asserts the index is *not* chosen. The second half is what makes the
first half mean something: if the defect ever stops reproducing, the guard
fires and the Phase 6 rule gets re-derived rather than quietly relaxed.

The regression guard at
``services/control_plane/tests/db/test_retrieval_sql.py::test_query_vector_as_a_
subquery_silently_loses_the_index`` pins the same defect against the *spec* text
four phases earlier. This one pins it against the shipped module.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable

import psycopg
import pytest

from services.control_plane.app.retrieval import ann
from services.control_plane.app.retrieval.config import EMBEDDING_VERSION, K_FINAL, K_RAW

pytestmark = [pytest.mark.db, pytest.mark.retrieval]

#: ``ops/decisions/VECTOR_INDEX_VARIANT.md`` -- VARIANT A, prefix ``user_id``.
#: Named here rather than imported from ``conftest``: pytest loads conftest
#: files, it does not import them by module path, and ``--import-mode=importlib``
#: makes ``import tests.retrieval.conftest`` unreliable.
ANN_INDEX = "evidence_embedding_ann_idx"


def _hero_evidence_id(conn: psycopg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM evidence_items "
            "WHERE user_id = %s AND embedding IS NOT NULL AND retraction_status = 'ACTIVE' "
            "ORDER BY id LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip("the corpus is not seeded with embedded evidence for the hero user")
    return uuid.UUID(str(row[0]))


# ==========================================================================
# Preconditions. Reported separately so a red run is diagnosable.
# ==========================================================================


def test_the_vector_index_exists(rconn: psycopg.Connection) -> None:
    """The precondition for every EXPLAIN below, asserted on its own.

    ``70_TASK_PLAN.md`` section 23 sequences the index *after* the bulk load, so
    a corpus can legitimately exist without it for a window. When that is the
    state, this test says so in one line instead of leaving four EXPLAIN
    failures to be misread as a defect in the query.

    Two states look identical from the table alone: the index was never built,
    and the index is building right now. ``SHOW INDEXES`` does not list a vector
    index until its job succeeds, and the build takes **52 minutes 56 seconds**
    over 18,035 rows on this cluster -- measured by ``T2.8``, against
    ``ops/41_RUNBOOK.md`` section 4.2's estimate of "one to two minutes", which
    is wrong by a factor of thirty at this corpus size. ``SHOW JOBS``
    distinguishes them, and the distinction is the difference between "this is
    broken" and "come back in forty minutes".
    """
    with rconn.cursor() as cur:
        cur.execute("SHOW CREATE TABLE evidence_items")
        row = cur.fetchone()
    assert row is not None
    if ANN_INDEX in str(row[1]):
        return

    with rconn.cursor() as cur:
        cur.execute(
            "SELECT status FROM [SHOW JOBS] WHERE description ILIKE %s ORDER BY created DESC",
            (f"%{ANN_INDEX}%",),
        )
        statuses = [str(job[0]) for job in cur.fetchall()]
    pytest.fail(
        f"{ANN_INDEX} is not present on evidence_items. Recent job statuses for "
        f"it: {statuses or 'none'}. A 'running' status means the ~53-minute "
        "build is in flight; no job at all means it was never created. Either "
        "way G6.2 and G6.3(b) cannot pass and the sponsor vector-index claim "
        "must not be made until it lands."
    )


def test_the_corpus_carries_exactly_one_embedding_version(rconn: psycopg.Connection) -> None:
    """``G6.1``. Two versions in one index is the drift this assertion exists for.

    Mixing embedding spaces in one ranking is a worse failure than a migration:
    the distances are still numbers, they are still ordered, and they are
    meaningless.
    """
    with rconn.cursor() as cur:
        cur.execute(
            "SELECT embedding_version, count(*) FROM evidence_items "
            "WHERE embedding IS NOT NULL GROUP BY 1"
        )
        rows = cur.fetchall()
    if not rows:
        # An unseeded database does not *violate* this property, it makes it
        # untestable -- "zero embedded rows" is not "two embedding versions".
        # Its sibling tests skip through `_user_by_email`; this one queries the
        # whole table and so missed that guard, and failed misleadingly instead.
        pytest.skip("the corpus is not seeded: no rows carry an embedding")
    assert len(rows) == 1, f"expected one embedding_version, found {rows}"
    assert rows[0][0] == EMBEDDING_VERSION


# ==========================================================================
# The statement itself
# ==========================================================================


def test_the_shipped_statement_is_the_spec_statement(ddl_spec_text: str) -> None:
    """The module's SQL is the section 5.5 block, not a transcription of it.

    Byte comparison after comment stripping and whitespace folding. A spec edit
    or a module edit that moves one predicate out of the CTE fails here rather
    than three phases later in a latency graph nobody is watching.
    """
    spec = ann.canonical_predicate_from_spec(ddl_spec_text)
    assert ann.normalise_sql(ann.CANONICAL_ANN_SQL) == ann.normalise_sql(spec)


def test_the_statement_binds_six_parameters_in_order() -> None:
    """Section 5.5's parameter list, and the reason it is fixed.

    ``$3`` is the query vector. It is a *bind*, which is the whole of
    ``D-06-001``'s remedy, and its position is asserted so a reordering cannot
    quietly turn the vector into the limit.
    """
    assert ann.PARAMETER_ORDER == (
        "user_id",
        "tenant_id",
        "query_embedding",
        "k_raw",
        "k_final",
        "embedding_version",
    )
    placeholders = sorted({int(n) for n in re.findall(r"\$(\d+)", ann.CANONICAL_ANN_SQL)})
    assert placeholders == [1, 2, 3, 4, 5, 6]


def test_the_over_fetch_is_four_times_the_final_limit() -> None:
    """Section 5.5 rule 3. ``k_raw == k_final`` is the silent-shrink bug.

    ANN returns ``k_raw`` candidates *before* filtering, so a run of retracted
    near-neighbours would otherwise reduce a 20-row answer to 12 with no signal
    that anything happened.
    """
    assert K_RAW == max(40, 4 * K_FINAL) == 80
    assert K_RAW > K_FINAL


# ==========================================================================
# G6.2 -- the live planner
# ==========================================================================


def test_explain_of_the_production_query_names_the_index(
    explain: Callable[..., str],
    hero_user: tuple[uuid.UUID, uuid.UUID],
    rconn: psycopg.Connection,
    stored_vector: Callable[[uuid.UUID], str],
) -> None:
    """``G6.2``, against the shipped statement with parameters bound.

    Not a hand-written EXPLAIN of a similar query: ``ann.render_ann_sql()`` is
    what the repository executes, and the vector arrives through psycopg's bind
    path exactly as it does in production.
    """
    tenant_id, user_id = hero_user
    vector = stored_vector(_hero_evidence_id(rconn, user_id))
    plan = explain(
        ann.render_ann_sql(),
        ann.bind(
            user_id=user_id,
            tenant_id=tenant_id,
            query_embedding=vector,
            embedding_version=EMBEDDING_VERSION,
        ),
    )
    assert ANN_INDEX in plan, f"the plan does not name {ANN_INDEX}:\n{plan}"
    assert "FULL SCAN" not in plan.upper(), f"the ANN query degraded to a full scan:\n{plan}"


def test_the_plan_is_constrained_on_the_user_prefix(
    explain: Callable[..., str],
    hero_user: tuple[uuid.UUID, uuid.UUID],
    rconn: psycopg.Connection,
    stored_vector: Callable[[uuid.UUID], str],
) -> None:
    """The prefix span is the physical isolation boundary, and it is visible.

    A schema regression that drops ``user_id`` from the index prefix fails here
    even when the ``WHERE`` clause survives -- which is the case where results
    stay correct and isolation stops being structural.
    """
    tenant_id, user_id = hero_user
    vector = stored_vector(_hero_evidence_id(rconn, user_id))
    plan = explain(
        ann.render_ann_sql(),
        ann.bind(
            user_id=user_id,
            tenant_id=tenant_id,
            query_embedding=vector,
            embedding_version=EMBEDDING_VERSION,
        ),
    )
    assert "prefix spans" in plan, f"the ANN scan is not prefix-constrained:\n{plan}"


def test_a_subquery_query_vector_still_loses_the_index(
    explain: Callable[..., str],
    hero_user: tuple[uuid.UUID, uuid.UUID],
    rconn: psycopg.Connection,
) -> None:
    """``D-06-001``, reproduced against the seeded corpus.

    This is the contrast that gives the assertion above its meaning. If the
    defect ever stops reproducing, this test fails and the Phase 6 rule is
    re-derived from a fresh measurement rather than relaxed on a hunch.
    """
    _tenant_id, user_id = hero_user
    neighbour = _hero_evidence_id(rconn, user_id)
    plan = explain(
        "SELECT id FROM evidence_items WHERE user_id = %s "
        "ORDER BY embedding <=> (SELECT embedding FROM evidence_items WHERE id = %s) "
        "LIMIT 40",
        (user_id, neighbour),
    )
    assert "vector search" not in plan, (
        "D-06-001 no longer reproduces: a subquery query vector now uses the "
        f"vector index. Re-verify the defect before relaxing any Phase 6 rule.\n{plan}"
    )


def test_the_module_refuses_to_build_a_subquery_query_vector() -> None:
    """The defect, made unrepresentable rather than merely documented.

    A comment saying "never compute the vector inside the statement" is advice.
    A refusal is a boundary, and it is the one that survives the next engineer
    in a hurry.
    """
    with pytest.raises(ann.QueryVectorNotBoundError):
        ann.bind(
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            query_embedding="(SELECT embedding FROM evidence_items LIMIT 1)",
            embedding_version=EMBEDDING_VERSION,
        )


def test_there_is_exactly_one_ann_entry_point() -> None:
    """``CANONICAL_DECISIONS.md`` names one door; there must be one door.

    ``app/retrieval/ann.py`` briefly carried its own ``ann_search()``. Its
    docstring was explicit that it existed only because
    ``provenance_db.repositories.evidence.ann_search()`` still raised
    ``NotImplementedError`` -- *"the divergence is reported rather than papered
    over by opening a second door"*. Once the repository was implemented that
    condition stopped holding, and what remained was a second door that
    happened to be broken.

    It was broken in the way this repository fears most: it applied
    ``predicates.active_rows`` to the section 5.5 statement, whose outer
    ``SELECT`` does not project ``retraction_status``. ``active_rows`` drops a
    row with no lifecycle key, so **every ANN query returned zero rows** -- no
    exception, no warning, correct-looking empty results. ``active_rows``'s own
    docstring names that exact mistake: *"a filter fed rows that cannot carry
    the column would drop all of them, which is a different bug wearing the same
    clothes."*

    The projection could not simply be widened: ``test_the_shipped_sql_matches
    _the_spec`` pins the statement to ``10_DATABASE_DDL.md`` after
    normalisation, and that pin is what catches a dropped filter.
    """
    import services.control_plane.app.retrieval.ann as ann_module

    assert not hasattr(ann_module, "ann_search"), (
        "app/retrieval/ann.py has re-grown an ann_search(); the canonical entry "
        "point is provenance_db.repositories.evidence.ann_search()"
    )


def test_the_retrieval_module_still_owns_the_statement_and_the_binding() -> None:
    """Deleting the second door must not delete the shared machinery.

    The repository delegates to ``render_ann_sql()`` and ``bind()``; if those
    disappeared with it, there would be no module containing ``<=>`` and the
    single-statement property would be lost rather than preserved.
    """
    import services.control_plane.app.retrieval.ann as ann_module

    assert callable(ann_module.render_ann_sql)
    assert callable(ann_module.bind)
    assert "<=>" in ann_module.CANONICAL_ANN_SQL
