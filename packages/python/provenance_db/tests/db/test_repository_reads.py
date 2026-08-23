"""The read layer against the seeded corpus — T3.3 / T5.x / T6.x, ``db`` lane.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts* (the ANN entry point),
  -> *Retrieval eligibility* (only ``ACTIVE`` evidence enters retrieval), and
  -> *Hero dataset canon* (Alex Rivera, and the corpus counts).
- ``docs/specs/10_DATABASE_DDL.md`` section 5.5 and section 12.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` sections 13.3 and 13.4.
- Defect ``D-06-001``.

What this lane adds over ``tests/unit/test_repositories.py``
-------------------------------------------------------------
The unit lane proves the *shape* of every statement without a cluster. Three
things it structurally cannot prove, and all three are here:

1. **The column names are real.** A statement can be perfectly scoped, perfectly
   parameterised and still name ``evidence_items.case_id``, which does not
   exist. Only the server can say so, and it says so by refusing the statement.
2. **The vector index is actually chosen.** ``D-06-001`` has no result-set
   symptom at any corpus size — the query returns the right rows either way and
   only latency moves, which is invisible at demo scale. ``EXPLAIN`` is the one
   observation that separates a vector search from a full scan.
3. **The lifecycle filter removes something.** The corpus carries one
   ``RETRACTED``, one ``SUPERSEDED`` and one ``QUARANTINED`` row among 18,035.
   A filter asserted against a corpus with nothing to filter is a filter nobody
   has tested.

Read-only, and structurally so
-------------------------------
The ``provenance`` database is the seeded demo corpus; a stray write there is
not recoverable from a test run. Every connection this module opens is put in
``READ ONLY`` mode before it issues a statement, so a write is refused by the
server rather than avoided by convention. ``provenance_ci`` is deliberately not
used: it is empty, and an empty corpus turns every assertion below into a
vacuous pass.

Credentials
-----------
The DSN arrives as a :class:`~pydantic.SecretStr` from the session fixture in
``conftest.py`` and is unwrapped inside a function body, never in a fixture
return value — pytest renders every test-function argument in its failure
header, and this lane's whole job is to produce failures.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from pydantic import SecretStr

from provenance_contracts.identity import Principal
from provenance_db.repositories import (
    actions,
    agent_runs,
    beliefs,
    cases,
    commitments,
    events,
    evidence,
    triggers,
)

pytestmark = [pytest.mark.db, pytest.mark.isolation]

#: The seeded demo database. ``CANONICAL_DECISIONS.md`` -> *Hero commit canon*:
#: 18,035 rows total, 16,035 of them the hero's.
DEMO_DATABASE = "provenance"

#: ``CANONICAL_DECISIONS.md`` -> *Hero dataset canon*. Looked up by e-mail
#: rather than pinned as a UUID: the id is a ``uuid5`` of a seed namespace that
#: lives in ``scripts/seed/ids.py``, and a copy of it here is a second source of
#: truth that nothing would keep in step.
HERO_EMAIL = "alex.rivera@example.invalid"
ISOLATION_EMAIL = "iso-a@example.invalid"


@pytest.fixture(scope="session")
def demo_dsn(role_dsns: dict[str, SecretStr]) -> SecretStr:
    """``pv_app_reader_writer`` on the seeded ``provenance`` database.

    A DSN naming any other database is a **skip**, not a silent redirect: this
    module asserts on seeded counts, and pointing it at ``provenance_ci`` —
    which is empty and two migrations behind — would turn every assertion here
    into a vacuous pass.
    """
    dsn = role_dsns.get("pv_app_reader_writer")
    if dsn is None:
        pytest.skip("COCKROACH_DATABASE_URL is not set; the seeded corpus is unreachable")
    name = dsn.get_secret_value().rsplit("/", 1)[-1].split("?", 1)[0]
    if name != DEMO_DATABASE:
        pytest.skip(f"COCKROACH_DATABASE_URL names {name!r}, not the seeded {DEMO_DATABASE!r}")
    return dsn


@pytest.fixture
async def conn(demo_dsn: SecretStr) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """A ``READ ONLY`` async connection to the seeded corpus.

    ``set_read_only`` is what makes "this module does not write" a property of
    the session rather than of the statements someone might add later.
    """
    connection = await psycopg.AsyncConnection.connect(demo_dsn.get_secret_value())
    try:
        await connection.set_read_only(True)
        yield connection
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def identities(demo_dsn: SecretStr) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    """``email -> (tenant_id, user_id)`` for the two personas this lane needs."""
    found: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
    with psycopg.connect(demo_dsn.get_secret_value()) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email, tenant_id, id FROM users WHERE email = ANY(%s)",
                ([HERO_EMAIL, ISOLATION_EMAIL],),
            )
            for email, tenant_id, user_id in cursor.fetchall():
                found[email] = (tenant_id, user_id)
    return found


def _principal(pair: tuple[uuid.UUID, uuid.UUID], sub: str) -> Principal:
    import datetime as dt

    now = dt.datetime.now(tz=dt.UTC)
    return Principal(
        tenant_id=pair[0],
        user_id=pair[1],
        cognito_sub=sub,
        token_issued_at=now - dt.timedelta(minutes=1),
        token_expires_at=now + dt.timedelta(minutes=59),
        request_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )


@pytest.fixture
def hero(identities: dict[str, tuple[uuid.UUID, uuid.UUID]]) -> Principal:
    if HERO_EMAIL not in identities:
        pytest.skip(f"{HERO_EMAIL} is not seeded in {DEMO_DATABASE}")
    return _principal(identities[HERO_EMAIL], "sub-alex-rivera")


@pytest.fixture
def stranger(identities: dict[str, tuple[uuid.UUID, uuid.UUID]]) -> Principal:
    if ISOLATION_EMAIL not in identities:
        pytest.skip(f"{ISOLATION_EMAIL} is not seeded in {DEMO_DATABASE}")
    return _principal(identities[ISOLATION_EMAIL], "sub-iso-a")


@pytest.fixture
def query_vector(demo_dsn: SecretStr, identities: dict[str, tuple[uuid.UUID, uuid.UUID]]) -> str:
    """A stored embedding, read rather than computed.

    ``tests/retrieval/conftest.py`` states the rule for the whole retrieval
    lane: every vector this corpus needs already exists, so a query vector is
    *read* and never costs a Bedrock call. The same applies here, and it also
    makes the ANN result deterministic — the nearest neighbour of a stored
    vector is that vector.
    """
    if HERO_EMAIL not in identities:
        pytest.skip(f"{HERO_EMAIL} is not seeded in {DEMO_DATABASE}")
    _, user_id = identities[HERO_EMAIL]
    with psycopg.connect(demo_dsn.get_secret_value()) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT embedding FROM evidence_items "
                "WHERE user_id = %s AND embedding IS NOT NULL "
                "ORDER BY observed_at DESC LIMIT 1",
                (user_id,),
            )
            row = cursor.fetchone()
    if row is None:
        pytest.skip("no embedded evidence for the hero user")
    return str(row[0])


@pytest.fixture
def hero_case_id(
    demo_dsn: SecretStr, identities: dict[str, tuple[uuid.UUID, uuid.UUID]]
) -> uuid.UUID:
    if HERO_EMAIL not in identities:
        pytest.skip(f"{HERO_EMAIL} is not seeded in {DEMO_DATABASE}")
    tenant_id, user_id = identities[HERO_EMAIL]
    with psycopg.connect(demo_dsn.get_secret_value()) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM cases WHERE tenant_id = %s AND user_id = %s "
                "ORDER BY last_activity_at DESC LIMIT 1",
                (tenant_id, user_id),
            )
            row = cursor.fetchone()
    if row is None:
        pytest.skip("no seeded case for the hero user")
    return uuid.UUID(str(row[0]))


@pytest.fixture(scope="session")
def lifecycle_census(demo_dsn: SecretStr) -> dict[str, int]:
    with psycopg.connect(demo_dsn.get_secret_value()) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT retraction_status, count(*) FROM evidence_items GROUP BY 1")
            return {str(status): int(count) for status, count in cursor.fetchall()}


# ==========================================================================
# 0. The vacuity guard, first. Every assertion below is worthless without it.
# ==========================================================================


def test_the_corpus_is_actually_seeded(lifecycle_census: dict[str, int]) -> None:
    """``0 offenders`` over ``0`` rows is a lint that stopped working.

    ``CANONICAL_DECISIONS.md`` -> *Hero commit canon* fixes the shape of this
    corpus: 18,035 rows, of which exactly three are the retraction fixtures.
    Those three are what give the lifecycle filter something to remove.
    """
    assert sum(lifecycle_census.values()) > 10_000, f"corpus looks empty: {lifecycle_census}"
    non_active = {k: v for k, v in lifecycle_census.items() if k != "ACTIVE"}
    assert non_active, (
        "every evidence row is ACTIVE, so the retraction filter has nothing to "
        "remove and every lifecycle assertion in this module passes vacuously"
    )


# ==========================================================================
# 1. Cases.
# ==========================================================================


async def test_get_case_snapshot_returns_the_seeded_case(
    conn: psycopg.AsyncConnection[Any], hero: Principal, hero_case_id: uuid.UUID
) -> None:
    """The aggregate re-read of ``10_DATABASE_DDL.md`` section 13, statement 1.

    Column names come from the migrations, and the server is the only thing
    that can confirm it: a snapshot naming a column that does not exist fails
    here and nowhere earlier.
    """
    row = await cases.get_case_snapshot(conn, hero, hero_case_id)
    assert row is not None
    assert row["id"] == hero_case_id
    assert row["revision"] >= 0
    assert row["attention_level"] in {"NONE", "INFO", "ATTENTION", "URGENT"}
    assert row["status"] in {
        "OPEN",
        "WAITING",
        "ACTIONABLE",
        "IN_PROGRESS",
        "DISPUTED",
        "BLOCKED",
        "AWAITING_USER",
        "RESOLVED",
        "REOPENED",
        "SUPERSEDED",
    }


async def test_get_case_snapshot_is_scoped_to_the_principal(
    conn: psycopg.AsyncConnection[Any], stranger: Principal, hero_case_id: uuid.UUID
) -> None:
    """The isolation proof, and the reason the predicate lives in the SQL.

    A second user asking for the hero's case by its exact id gets nothing. Not
    a filtered-down row, not a permission error the caller might swallow —
    nothing, because the row was never in the result set.
    """
    assert await cases.get_case_snapshot(conn, stranger, hero_case_id) is None


async def test_get_case_revision_matches_the_snapshot(
    conn: psycopg.AsyncConnection[Any], hero: Principal, hero_case_id: uuid.UUID
) -> None:
    """The revision is what an approval is bound to (``CANONICAL_DECISIONS.md``
    -> *External action*), so it gets its own entry point and the two must not
    be able to disagree."""
    snapshot = await cases.get_case_snapshot(conn, hero, hero_case_id)
    assert snapshot is not None
    assert await cases.get_case_revision(conn, hero, hero_case_id) == snapshot["revision"]


async def test_get_case_revision_is_scoped_to_the_principal(
    conn: psycopg.AsyncConnection[Any], stranger: Principal, hero_case_id: uuid.UUID
) -> None:
    assert await cases.get_case_revision(conn, stranger, hero_case_id) is None


async def test_list_open_cases_excludes_terminal_cases(
    conn: psycopg.AsyncConnection[Any], hero: Principal
) -> None:
    """``RESOLVED`` and ``SUPERSEDED`` are the terminal statuses in
    ``CaseStatus``; there is no ``CLOSED``.

    A predicate written as ``status <> 'CLOSED'`` matches every legal value and
    returns the whole table — silently, with a plausible-looking result — which
    is why this asserts on membership rather than on a count.
    """
    rows = await cases.list_open_cases(conn, hero)
    assert rows, "the hero has no live cases at all; the corpus or the predicate is wrong"
    statuses = {row["status"] for row in rows}
    assert not statuses & {"RESOLVED", "SUPERSEDED"}, f"terminal cases leaked in: {statuses}"


async def test_list_cases_returns_more_than_the_open_ones(
    conn: psycopg.AsyncConnection[Any], hero: Principal
) -> None:
    """The two entry points are different questions. The seeded hero carries
    both live and resolved cases, so a repository that answered them the same
    way fails here."""
    live = await cases.list_open_cases(conn, hero)
    everything = await cases.list_cases(conn, hero)
    assert len(everything) > len(live)
    assert {row["id"] for row in live} <= {row["id"] for row in everything}


async def test_list_cases_is_scoped_to_the_principal(
    conn: psycopg.AsyncConnection[Any], hero: Principal, stranger: Principal
) -> None:
    hero_ids = {row["id"] for row in await cases.list_cases(conn, hero)}
    stranger_ids = {row["id"] for row in await cases.list_cases(conn, stranger)}
    assert hero_ids, "the hero has no cases; this assertion would pass vacuously"
    assert not hero_ids & stranger_ids


# ==========================================================================
# 2. Evidence, and the ANN entry point.
# ==========================================================================


async def test_ann_search_returns_rows(
    conn: psycopg.AsyncConnection[Any], hero: Principal, query_vector: str
) -> None:
    """The regression that matters most: the entry point returns *something*.

    An ANN path that returns zero rows for every query is not a degraded
    retrieval, it is no retrieval — and it looks exactly like a corpus with no
    matches. The query vector here is a stored embedding, so at least one row
    (its own) is at distance 0 and a correct implementation cannot return
    nothing.
    """
    rows = await evidence.ann_search(conn, hero, query_vector, limit=20, embedding_version="v1")
    assert rows, "ann_search returned no rows for a vector that is itself in the corpus"
    assert len(rows) <= 20
    assert "distance" in rows[0]
    assert rows == sorted(rows, key=lambda row: row["distance"])


async def test_ann_search_never_returns_non_active_evidence(
    conn: psycopg.AsyncConnection[Any],
    hero: Principal,
    query_vector: str,
    demo_dsn: SecretStr,
) -> None:
    """``13_RETRIEVAL_SPEC.md`` section 13.4, against the real fixtures.

    Retracted and superseded evidence keeps its embedding — it has to, because
    ``belief_support`` edges point at it with ``ON DELETE RESTRICT`` — so it is
    still in the vector index and still competing on cosine distance. A
    correction is by construction *about the same subject* as the thing it
    corrects, which is exactly what makes it semantically adjacent and exactly
    why a missing predicate resurfaces it first.
    """
    with psycopg.connect(demo_dsn.get_secret_value()) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM evidence_items WHERE retraction_status <> 'ACTIVE'")
            excluded = {uuid.UUID(str(row[0])) for row in cursor.fetchall()}
    assert excluded, "no non-ACTIVE evidence exists; this test would pass vacuously"

    rows = await evidence.ann_search(conn, hero, query_vector, limit=60, embedding_version="v1")
    assert not {row["id"] for row in rows} & excluded


async def test_ann_search_cannot_cross_a_user_boundary(
    conn: psycopg.AsyncConnection[Any],
    hero: Principal,
    stranger: Principal,
    query_vector: str,
    demo_dsn: SecretStr,
) -> None:
    """The hero's own query vector, run as somebody else, returns only that
    somebody's rows.

    ``user_id`` is the ANN index prefix, so this is also the assertion that the
    prefix stayed inside the CTE: moving it out returns identical-looking rows
    from every user in the cluster.
    """
    with psycopg.connect(demo_dsn.get_secret_value()) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM evidence_items WHERE user_id = %s", (stranger.user_id,))
            stranger_evidence = {uuid.UUID(str(row[0])) for row in cursor.fetchall()}
    assert stranger_evidence, "the isolation persona has no evidence; nothing to prove"

    rows = await evidence.ann_search(conn, stranger, query_vector, limit=20, embedding_version="v1")
    assert rows, "the isolation persona got no rows at all"
    assert {row["id"] for row in rows} <= stranger_evidence


async def test_ann_search_filters_the_embedding_version(
    conn: psycopg.AsyncConnection[Any], hero: Principal, query_vector: str
) -> None:
    """``CANONICAL_DECISIONS.md`` -> *Embeddings*: the frozen version is ``v1``.

    Mixing vectors from two embedding versions in one cosine ranking produces
    distances that are arithmetically fine and semantically meaningless.
    """
    assert (
        await evidence.ann_search(
            conn, hero, query_vector, limit=20, embedding_version="v2-does-not-exist"
        )
        == []
    )


async def test_the_ann_statement_uses_the_vector_index(
    conn: psycopg.AsyncConnection[Any], hero: Principal, query_vector: str
) -> None:
    """``D-06-001``, observed the only way it can be observed.

    A query vector passed as a correlated subquery returns the same rows in the
    same order and defeats index selection silently. The plan is the one place
    the difference is visible, so the test asserts on the plan: the ANN index
    is named, and no full scan of ``evidence_items`` appears.
    """
    plan = await evidence.explain_ann_search(
        conn, hero, query_vector, limit=20, embedding_version="v1"
    )
    text = "\n".join(plan)
    assert "evidence_embedding_ann_idx" in text, f"the ANN index was not chosen:\n{text}"
    assert "vector search" in text, f"the plan is not a vector search:\n{text}"
    assert "FULL SCAN" not in text.upper(), f"D-06-001 shape detected:\n{text}"


async def test_get_evidence_item_reads_a_real_row(
    conn: psycopg.AsyncConnection[Any], hero: Principal, query_vector: str
) -> None:
    """Column names, confirmed by the server. ``evidence_items`` has
    ``artifact_id`` and ``created_at``; it has neither ``source_artifact_id``
    nor ``recorded_at`` nor ``case_id``."""
    rows = await evidence.ann_search(conn, hero, query_vector, limit=1, embedding_version="v1")
    assert rows
    item = await evidence.get_evidence_item(conn, hero, rows[0]["id"])
    assert item is not None
    assert item["id"] == rows[0]["id"]
    assert item["retraction_status"] == "ACTIVE"
    assert "artifact_id" in item


async def test_get_evidence_item_is_scoped_to_the_principal(
    conn: psycopg.AsyncConnection[Any],
    hero: Principal,
    stranger: Principal,
    query_vector: str,
) -> None:
    rows = await evidence.ann_search(conn, hero, query_vector, limit=1, embedding_version="v1")
    assert rows
    assert await evidence.get_evidence_item(conn, stranger, rows[0]["id"]) is None


async def test_list_active_evidence_for_case_executes_against_the_real_schema(
    conn: psycopg.AsyncConnection[Any], hero: Principal, hero_case_id: uuid.UUID
) -> None:
    """``evidence_items`` carries no ``case_id``. Evidence reaches a case
    through ``claims``, and a statement that assumes otherwise is rejected by
    the server — which is the whole point of running it here."""
    rows = await evidence.list_active_evidence_for_case(conn, hero, hero_case_id, limit=25)
    assert isinstance(rows, list)
    assert all(row["retraction_status"] == "ACTIVE" for row in rows)


# ==========================================================================
# 3. Beliefs, commitments, triggers, actions, events, agent runs.
#
# The epistemic and obligation planes are empty in the seeded corpus: the
# Kernel that writes them is Phase 4. An empty result is therefore the correct
# answer, and it is still worth asserting — a statement that names a column
# that does not exist raises `UndefinedColumn` before it can return anything,
# so these are column-name proofs rather than row-count proofs, and they are
# labelled as such rather than dressed up as more.
# ==========================================================================


async def test_get_active_beliefs_for_case_executes(
    conn: psycopg.AsyncConnection[Any], hero: Principal, hero_case_id: uuid.UUID
) -> None:
    assert await beliefs.get_active_beliefs_for_case(conn, hero, hero_case_id) == []


async def test_get_belief_lineage_executes(
    conn: psycopg.AsyncConnection[Any], hero: Principal
) -> None:
    assert await beliefs.get_belief_lineage(conn, hero, uuid.uuid4()) == []


async def test_get_belief_support_executes(
    conn: psycopg.AsyncConnection[Any], hero: Principal
) -> None:
    assert await beliefs.get_belief_support(conn, hero, uuid.uuid4()) == []


async def test_get_open_commitments_executes_and_keeps_money_decimal(
    conn: psycopg.AsyncConnection[Any], hero: Principal, hero_case_id: uuid.UUID
) -> None:
    """Money is ``DECIMAL(20,4)`` and arrives as :class:`~decimal.Decimal`.

    The assertion is conditional on there being a row because the ledger is
    written in Phase 4; what is unconditional is that the statement runs, which
    is what proves the eight money columns are named correctly.
    """
    rows = await commitments.get_open_commitments(conn, hero, hero_case_id)
    assert isinstance(rows, list)
    for row in rows:
        if row["outstanding_amount"] is not None:
            assert isinstance(row["outstanding_amount"], Decimal)


async def test_get_armed_triggers_for_case_executes(
    conn: psycopg.AsyncConnection[Any], hero: Principal, hero_case_id: uuid.UUID
) -> None:
    assert await triggers.get_armed_triggers_for_case(conn, hero, hero_case_id) == []


async def test_get_action_intent_executes(
    conn: psycopg.AsyncConnection[Any], hero: Principal
) -> None:
    assert await actions.get_action_intent(conn, hero, uuid.uuid4()) is None


async def test_get_agent_run_executes(conn: psycopg.AsyncConnection[Any], hero: Principal) -> None:
    assert await agent_runs.get_agent_run(conn, hero, uuid.uuid4()) is None


async def test_get_undispatched_outbox_events_executes(
    conn: psycopg.AsyncConnection[Any], hero: Principal
) -> None:
    assert await events.get_undispatched_outbox_events(conn, hero, limit=10) == []


# ==========================================================================
# 4. The write boundary, at the only place it can be proven: the server.
# ==========================================================================


async def test_the_connection_this_module_uses_refuses_a_write(
    conn: psycopg.AsyncConnection[Any],
) -> None:
    """ "Read-only" is enforced by the server, not by convention.

    Two independent mechanisms refuse the statement below, and the test accepts
    either because both are correct:

    * ``InsufficientPrivilege`` — ``10_DATABASE_DDL.md`` section 12 grants
      ``pv_app_reader_writer`` **SELECT only** on ``cases``. This is the one
      that actually fires, and it is the stronger of the two: it is a property
      of the role rather than of the session, so it holds for every connection
      anyone opens with this credential, including one that forgot to set
      read-only mode.
    * ``ReadOnlySqlTransaction`` — the session mode this module's fixture sets,
      which is what would catch a write against a table the role *can* write.

    Without this test, "no test in this file writes to the demo corpus" would
    be a claim about every statement anyone ever adds to it.
    """
    with pytest.raises(
        (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)
    ):
        await conn.execute("UPDATE cases SET revision = revision + 1 WHERE false")


async def test_the_role_cannot_write_a_table_it_is_granted_on_either(
    conn: psycopg.AsyncConnection[Any],
) -> None:
    """The session-mode half, proven where the grant does not mask it.

    ``pv_app_reader_writer`` legitimately holds ``INSERT``/``UPDATE`` on
    ``agent_runs`` (section 12), so a refusal here can only come from the
    read-only session. Without this case the assertion above would prove the
    grant and nothing else, and the fixture's ``set_read_only`` would be
    untested — which is the half that protects the tables this role *can*
    write.
    """
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        await conn.execute("UPDATE agent_runs SET status = status WHERE false")
