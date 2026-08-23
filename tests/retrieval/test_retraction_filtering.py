"""Evidence lifecycle filtering, with its positive control (``T6.5``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 13, and its section 13.4 golden
  test.
- ``docs/CANONICAL_DECISIONS.md`` -> *Evidence and retrieval*: superseded
  evidence is **excluded** from active retrieval, not down-weighted. No
  down-weighted active path exists in v1.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.3(c)`` and ``G6.3(d)``, and ``G6.7``.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T6.5``: "Name part (d)
  ``*_positive_control`` and require its presence before part (c)'s result is
  read."

Read part (d) first
-------------------
Part (c) asserts that none of the three retraction fixtures appear in any
retrieval result. That assertion passes trivially if the fixtures rank
nowhere near the top, if the corpus is empty, or if the query vector is
unrelated to them -- and it would keep passing after someone deleted the
filter. Part (d) removes the retraction predicate and requires the superseded
fixture to appear inside the top 20. **Part (d) failing means part (c) was
passing vacuously**, and the reviewer reads (d) first.

Why the fixtures keep their embeddings
---------------------------------------
Deleting the row is forbidden by invariant I1 and by the fact that
``belief_support`` edges point at it -- a retracted item is frequently the
``CONTRADICTS`` edge justifying why a belief version was superseded. So the
vector stays in the index, still competing on cosine distance, and still capable
of being the single closest neighbour to a query -- because a correction is by
construction *about the same subject* as the thing it corrects. That adjacency
is not a coincidence to be tuned around; it is the mechanism, and the filter is
the only thing standing in front of it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg
import pytest

from services.control_plane.app.retrieval import ann, predicates
from services.control_plane.app.retrieval.config import EMBEDDING_VERSION

pytestmark = [pytest.mark.db, pytest.mark.retrieval]

PROVENANCE_SEED_NS = uuid.UUID("6f2b1c40-0000-4000-8000-70726f76656e")


def sid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(PROVENANCE_SEED_NS, ":".join(parts))


#: ``scripts/seed/retractions.py``. Each falsifies a different way the filter
#: can be wrong: a SUPERSEDED extraction error, a RETRACTED user correction, and
#: a QUARANTINED adversarial excerpt.
SUPERSEDED_FIXTURE = sid("evidence", "isp-wrong-term-date")


def _retraction_fixture_ids(conn: psycopg.Connection, user_id: uuid.UUID) -> list[uuid.UUID]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM evidence_items "
            "WHERE user_id = %s AND retraction_status <> 'ACTIVE' ORDER BY id",
            (user_id,),
        )
        return [uuid.UUID(str(row[0])) for row in cur.fetchall()]


def _ann_ids(
    conn: psycopg.Connection,
    sql: str,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    vector: str,
    k_final: int = 20,
) -> list[uuid.UUID]:
    params = ann.bind(
        user_id=user_id,
        tenant_id=tenant_id,
        query_embedding=vector,
        embedding_version=EMBEDDING_VERSION,
        k_raw=max(40, 4 * k_final),
        k_final=k_final,
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [uuid.UUID(str(row[0])) for row in cur.fetchall()]


# ==========================================================================
# (d) THE POSITIVE CONTROL. Read this one first.
# ==========================================================================


def test_retracted_evidence_is_reachable_without_the_filter_positive_control(
    rconn: psycopg.Connection,
    hero_user: tuple[uuid.UUID, uuid.UUID],
    stored_vector: Callable[[uuid.UUID], str],
) -> None:
    """``G6.3(d)``. With the retraction predicate removed, the superseded
    fixture appears within the top 20.

    The query vector is the fixture's own stored embedding, so it sits at
    cosine distance exactly 0.0 and is rank 1 in an unfiltered search. That is
    the strongest available form of this control: if it does *not* appear, the
    corpus, the index or the statement is broken, and part (c)'s green is
    meaningless.
    """
    tenant_id, user_id = hero_user
    vector = stored_vector(SUPERSEDED_FIXTURE)
    ids = _ann_ids(
        rconn,
        ann.render_ann_sql(retraction_filter=False),
        tenant_id=tenant_id,
        user_id=user_id,
        vector=vector,
    )
    assert SUPERSEDED_FIXTURE in ids[:20], (
        "the positive control did not fire: sid('evidence','isp-wrong-term-date') "
        "is not in the unfiltered top 20, so the (c) assertion below would be "
        f"passing vacuously. Got {ids[:5]}"
    )


# ==========================================================================
# (c) The assertion the positive control licenses.
# ==========================================================================


def test_no_retraction_fixture_survives_the_production_statement(
    rconn: psycopg.Connection,
    hero_user: tuple[uuid.UUID, uuid.UUID],
    stored_vector: Callable[[uuid.UUID], str],
) -> None:
    """``G6.3(c)``. None of the three fixtures appear in any retrieval result.

    Run once per fixture, each time with that fixture's own embedding as the
    query vector -- so each is rank 1 in the unfiltered ordering and its absence
    can only be the filter.
    """
    tenant_id, user_id = hero_user
    fixtures = _retraction_fixture_ids(rconn, user_id)
    assert len(fixtures) == 3, f"expected three retraction fixtures, found {len(fixtures)}"

    for fixture in fixtures:
        ids = _ann_ids(
            rconn,
            ann.render_ann_sql(),
            tenant_id=tenant_id,
            user_id=user_id,
            vector=stored_vector(fixture),
        )
        assert ids, "the filtered statement returned nothing at all; the probe is vacuous"
        assert fixture not in ids, (
            f"retracted evidence {fixture} was returned by active retrieval even "
            "though the query vector was its own embedding"
        )


def test_layer_three_excludes_what_a_lost_sql_predicate_would_admit(
    rconn: psycopg.Connection,
    hero_user: tuple[uuid.UUID, uuid.UUID],
    stored_vector: Callable[[uuid.UUID], str],
) -> None:
    """Layer 3, against the exact candidate set a lost SQL predicate produces.

    The test above proves layer 1: the predicate in the statement keeps the
    fixtures out. Layer 1 is one line, and a single missed predicate is a
    *silent* correctness failure -- which is the whole argument for section
    13.3's defence in depth.

    So this constructs the scenario layer 3 exists for. It runs the **positive
    control's** unfiltered statement to get precisely the rows a statement
    without the predicate would return, confirms the superseded fixture is
    among them, and hands them to :func:`predicates.active_rows`, which must
    remove it.

    Note what this does *not* claim. ``G6.7``'s sabotage neuters
    ``predicates.retraction_filter``, the function that assembles the predicate
    into the statement, and it is part (c) above that goes red -- verified, not
    assumed. This test covers the other half: the in-process filter that stands
    where layer 1 was, on rows layer 1 never saw.
    """
    tenant_id, user_id = hero_user
    fixtures = _retraction_fixture_ids(rconn, user_id)
    assert len(fixtures) == 3, f"expected three retraction fixtures, found {len(fixtures)}"

    ids = _ann_ids(
        rconn,
        ann.render_ann_sql(retraction_filter=False),
        tenant_id=tenant_id,
        user_id=user_id,
        vector=stored_vector(SUPERSEDED_FIXTURE),
    )
    assert SUPERSEDED_FIXTURE in ids, "the unfiltered candidate set is vacuous"

    with rconn.cursor() as cur:
        cur.execute(
            "SELECT id, retraction_status FROM evidence_items WHERE id = ANY(%s::UUID[])",
            (ids,),
        )
        status = {uuid.UUID(str(row[0])): str(row[1]) for row in cur.fetchall()}

    candidates = [{"evidence_id": one, "retraction_status": status[one]} for one in ids]
    kept = predicates.active_rows(candidates)

    survivors = {row["evidence_id"] for row in kept}
    assert SUPERSEDED_FIXTURE not in survivors, (
        "the in-process lifecycle filter admitted a SUPERSEDED row. Layer 1 is "
        "one SQL predicate; layer 3 is what stands there when layer 1 is edited "
        "away, and a correction the user already made is one line from being "
        "returned first."
    )
    assert survivors, "layer 3 dropped every candidate; the assertion above is vacuous"
    assert not any(row["retraction_status"] != "ACTIVE" for row in kept)


def test_all_three_lifecycle_states_are_excluded_not_just_retracted(
    rconn: psycopg.Connection,
    hero_user: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """Section 13.1's table has four states and three of them are excluded.

    A filter written as ``retraction_status <> 'RETRACTED'`` passes a test that
    only checks the RETRACTED fixture and lets SUPERSEDED and QUARANTINED rows
    straight through. The corpus carries one of each precisely so that shortcut
    fails.
    """
    _tenant_id, user_id = hero_user
    with rconn.cursor() as cur:
        cur.execute(
            "SELECT retraction_status, count(*) FROM evidence_items "
            "WHERE user_id = %s AND retraction_status <> 'ACTIVE' GROUP BY 1 ORDER BY 1",
            (user_id,),
        )
        states = {str(row[0]) for row in cur.fetchall()}
    assert states == {"QUARANTINED", "RETRACTED", "SUPERSEDED"}
    assert frozenset(states) == predicates.EXCLUDED_STATUSES


def test_superseded_evidence_is_excluded_and_never_down_weighted() -> None:
    """``CANONICAL_DECISIONS.md`` -> *Evidence and retrieval*, stated as code.

    There is no v1 code path that admits a superseded row with a reduced
    weight. Down-weighting is the plausible-sounding alternative that keeps a
    disowned correction in the prompt at 40% volume, and the refusal is what
    stops someone adding it as a "softer" fix.
    """
    assert predicates.retraction_mode() == "EXCLUDE"
    with pytest.raises(ValueError, match="(?i)down-?weight"):
        predicates.retraction_mode("DOWN_WEIGHT")


def test_the_agent_safe_view_bakes_the_filter_in(rconn: psycopg.Connection) -> None:
    """Layer 2 of section 13.3, and the reason V10 returns zero while V11
    returns three.

    ``agent_evidence_retrieval_v1`` carries ``WHERE e.retraction_status =
    'ACTIVE'`` inside the view definition, so the MCP path cannot express a
    query that returns retracted rows. ``pv_agent_reader`` has no grant on the
    base table, so there is no way around it.
    """
    with rconn.cursor() as cur:
        cur.execute("SHOW CREATE VIEW agent_evidence_retrieval_v1")
        row = cur.fetchone()
    assert row is not None, "agent_evidence_retrieval_v1 does not exist"
    definition = str(row[1])
    assert "retraction_status = 'ACTIVE'" in definition.replace("'ACTIVE':::STRING", "'ACTIVE'")
