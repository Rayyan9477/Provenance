"""ANN cannot cross users (``T6.5``, ``G6.3(a)``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 14.2 and 14.3 -- "the test a
  judge should be shown", and why assertion 2 is what makes assertion 1
  meaningful.
- ``docs/quality/23_PHASE_GATES.md`` ``G6.3(a)``: "0 of 200 returned ids belong
  to iso-a or iso-b, over the full 18,035-row corpus (the hero user's own
  partition is 16,035 of those rows)".
- ``docs/specs/10_DATABASE_DDL.md`` section 5.1 -- ``user_id`` is the mandatory
  index prefix, and it is not a performance hint.

How the honeypot is built without a write and without Bedrock
--------------------------------------------------------------
Section 14.3 plants a row in user B's corpus whose embedding is a byte-copy of
user A's query vector, so its cosine distance is exactly 0.0 and it is rank 1
in any unfiltered search over the combined corpus. This lane gets the identical
property for free and without touching the database: it takes an **existing**
iso-b row's stored embedding and uses *that* as the query vector. The row is
then at distance 0.0 from the query by construction, it is already indexed, and
no insert, no rollback and no Bedrock call is involved.

That matters for more than tidiness. The corpus is being seeded concurrently by
another task; a lane that inserted rows would contend with it, and a lane that
inserted-then-rolled-back would still take write intents on a table under bulk
load.

Assertion 2 is the one that carries the proof
----------------------------------------------
A test that only asserts absence passes trivially if the fixture failed. Proving
the same vector *is* rank 1 for its rightful owner establishes that the
honeypot exists, is indexed and is findable -- and therefore that its absence
for the hero user is isolation rather than an accident.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg
import pytest

from services.control_plane.app.retrieval import ann
from services.control_plane.app.retrieval.config import EMBEDDING_VERSION

pytestmark = [pytest.mark.db, pytest.mark.retrieval, pytest.mark.isolation]

#: ``G6.3(a)`` says 200 ids. ``k_final`` is 20 for the demo corpus, so the
#: assertion is run at a deliberately widened limit: a leak that only appears
#: past rank 20 is still a leak, and asking for ten times the production limit
#: is the cheapest way to look for it.
ISOLATION_PROBE_LIMIT = 200


def _first_embedded_id(conn: psycopg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM evidence_items "
            "WHERE user_id = %s AND embedding IS NOT NULL AND retraction_status = 'ACTIVE' "
            "ORDER BY id LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip(f"no embedded evidence seeded for user {user_id}")
    return uuid.UUID(str(row[0]))


def _ann_ids(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    vector: str,
    limit: int,
) -> list[uuid.UUID]:
    params = ann.bind(
        user_id=user_id,
        tenant_id=tenant_id,
        query_embedding=vector,
        embedding_version=EMBEDDING_VERSION,
        k_raw=limit * 4,
        k_final=limit,
    )
    with conn.cursor() as cur:
        cur.execute(ann.render_ann_sql(), params)
        return [uuid.UUID(str(row[0])) for row in cur.fetchall()]


def test_the_corpus_is_the_one_the_gate_describes(rconn: psycopg.Connection) -> None:
    """Vacuity guard. 18,035 seeded rows across three users, 16,035 the hero's.

    An isolation proof over an empty or single-user corpus is not a proof, and
    this is the assertion that says so out loud instead of letting four green
    tests imply it.

    The count is taken over the **three seeded users**, not over the whole
    table. ``provenance_ci`` is shared: other suites in this repository write
    their own fixture users into ``evidence_items`` and do not always clean up,
    so a ``count(*)`` over the table measures those suites' hygiene rather than
    this corpus's integrity, and it goes red for a reason that has nothing to do
    with retrieval. Scoping it to the three users keeps the assertion sharp --
    a missing seed partition still fails -- and moves the foreign rows into the
    separate report below, where they are diagnosable.
    """
    with rconn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM users WHERE email IN "
            "('alex.rivera@example.invalid','iso-a@example.invalid','iso-b@example.invalid')"
        )
        if int(str(cur.fetchone()[0])) == 0:
            # None of the three seed users exists, so there is no corpus to make
            # a claim about. Its siblings skip through `_user_by_email`; this one
            # queries by email inline and so missed that guard, and reported a
            # seeding state as an isolation failure. An unseeded database does
            # not violate the vacuity guard -- it is the case the guard cannot
            # speak to.
            pytest.skip("the corpus is not seeded: none of the three seed users exists")
        cur.execute(
            "SELECT count(*) FROM evidence_items WHERE user_id IN "
            "(SELECT id FROM users WHERE email IN "
            "('alex.rivera@example.invalid','iso-a@example.invalid','iso-b@example.invalid'))"
        )
        seeded = int(str(cur.fetchone()[0]))
        cur.execute(
            "SELECT count(DISTINCT user_id) FROM evidence_items WHERE user_id IN "
            "(SELECT id FROM users WHERE email IN "
            "('alex.rivera@example.invalid','iso-a@example.invalid','iso-b@example.invalid'))"
        )
        users = int(str(cur.fetchone()[0]))
        cur.execute("SELECT count(*) FROM evidence_items")
        total = int(str(cur.fetchone()[0]))

    assert seeded == 18035, f"expected the 18,035-row seeded corpus, found {seeded}"
    assert users == 3, f"expected three seeded partitions, found {users}"
    assert total >= seeded

    if total != seeded:
        # Reported, not asserted. Foreign rows do not weaken the isolation
        # proofs below -- each one queries a named user's partition and asserts
        # on named ids -- but they are evidence of a concurrent suite leaving
        # state in a shared database, and that belongs in a transcript rather
        # than in silence.
        print(  # this line IS the report; -s surfaces it
            f"NOTE: {total - seeded} evidence rows outside the three seeded users "
            "are present in provenance_ci. Another suite wrote fixture rows into "
            "the shared database and did not remove them."
        )


def test_the_honeypot_is_rank_one_for_its_rightful_owner(
    rconn: psycopg.Connection,
    iso_b_user: tuple[uuid.UUID, uuid.UUID],
    stored_vector: Callable[[uuid.UUID], str],
) -> None:
    """Section 14.3, assertion 2 -- run first, because it licenses assertion 1.

    The query vector is this row's own embedding, so cosine distance is exactly
    0.0. If this is not rank 1 the fixture is broken, the index is missing, or
    the statement is not ordering by distance -- and any of those would make the
    absence assertions below vacuous.
    """
    tenant_b, user_b = iso_b_user
    honeypot = _first_embedded_id(rconn, user_b)
    ids = _ann_ids(
        rconn,
        tenant_id=tenant_b,
        user_id=user_b,
        vector=stored_vector(honeypot),
        limit=20,
    )
    assert ids, "the honeypot's own owner got no rows at all"
    assert ids[0] == honeypot, f"the exact-duplicate vector is not rank 1; got {ids[:3]}"


def test_ann_never_returns_another_users_evidence(
    rconn: psycopg.Connection,
    hero_user: tuple[uuid.UUID, uuid.UUID],
    iso_a_user: tuple[uuid.UUID, uuid.UUID],
    iso_b_user: tuple[uuid.UUID, uuid.UUID],
    stored_vector: Callable[[uuid.UUID], str],
) -> None:
    """``G6.3(a)``. Zero of 200 returned ids belong to iso-a or iso-b.

    The query vector is iso-b's honeypot, i.e. the single worst case: the one
    vector in the corpus that would be rank 1 for the hero user if the prefix
    were not doing its job.
    """
    tenant_hero, user_hero = hero_user
    _tenant_a, user_a = iso_a_user
    _tenant_b, user_b = iso_b_user
    honeypot = _first_embedded_id(rconn, user_b)

    ids = _ann_ids(
        rconn,
        tenant_id=tenant_hero,
        user_id=user_hero,
        vector=stored_vector(honeypot),
        limit=ISOLATION_PROBE_LIMIT,
    )
    assert ids, "the hero user's own partition returned nothing; the probe is vacuous"
    assert honeypot not in ids, "ANN crossed the user boundary"

    with rconn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM evidence_items WHERE id = ANY(%s) AND user_id = ANY(%s)",
            (list(ids), [user_a, user_b]),
        )
        foreign = int(str(cur.fetchone()[0]))
    assert foreign == 0, f"{foreign} of {len(ids)} returned ids belong to iso-a or iso-b"


def test_the_tenant_predicate_holds_independently_of_the_prefix(
    rconn: psycopg.Connection,
    hero_user: tuple[uuid.UUID, uuid.UUID],
    iso_b_user: tuple[uuid.UUID, uuid.UUID],
    stored_vector: Callable[[uuid.UUID], str],
) -> None:
    """Defence in depth, exercised rather than assumed.

    ``user_id`` already determines the tenant through the FK, so the outer
    ``tenant_id`` predicate normally changes nothing -- which is exactly why it
    can rot unnoticed. Binding the hero's ``user_id`` with another tenant's
    ``tenant_id`` must return nothing; if it returns rows, the predicate is
    decorative and the second layer of isolation does not exist.
    """
    _tenant_hero, user_hero = hero_user
    tenant_b, user_b = iso_b_user
    vector = stored_vector(_first_embedded_id(rconn, user_b))
    ids = _ann_ids(rconn, tenant_id=tenant_b, user_id=user_hero, vector=vector, limit=20)
    assert ids == [], "the tenant_id predicate did not filter a mismatched pair"
