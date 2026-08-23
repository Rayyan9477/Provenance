"""The seeded corpus, asserted against the live cluster (``T2.8``).

Authority
---------
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5 ``T2.8`` acceptance, and section
  23 -- the drop-index / bulk-load / rebuild-index ordering.
- ``docs/specs/10_DATABASE_DDL.md`` sections 17.7, 17.8, 18.
- ``docs/quality/23_PHASE_GATES.md`` section 8 -- ``G2.4``, ``G2.5``, ``G2.6``.
- ``docs/CANONICAL_DECISIONS.md`` -> Hero commit canon: **18,035 total**,
  **16,035 user-scoped** for the hero.

The three failure modes this file is written against
----------------------------------------------------
1. **Index left dropped.** The demo still works -- a brute-force scan over
   16,035 rows is survivable -- and ``G6.2``'s ``EXPLAIN`` then finds no index,
   failing the sponsor vector-index claim.
   ``test_the_ann_index_is_back_after_the_seed`` and
   ``test_the_index_build_job_actually_finished`` catch it.
2. **Rows loaded without vectors.** ``count(*)`` passes and retrieval returns
   nothing. ``test_every_evidence_row_carries_a_1024_dim_titan_vector``.
3. **Seeded state that is not idempotent.** A second ``make seed`` that adds
   rows makes every later row-count assertion a function of how many times
   someone ran the seed. ``test_seeding_twice_changes_no_row_count``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parents[4]

#: ``provenance_ci`` is shared. Other phases' database tests create their own
#: fixture tenants, users, cases and belief versions in it, and an unscoped
#: ``count(*)`` here would measure their work as well as this seed's -- turning
#: "the seed is idempotent" into "no other agent wrote anything in the last
#: minute", which is a claim about scheduling rather than about the loader.
#: Every count below that asserts an exact number is therefore restricted to the
#: three seeded tenants. Assertions that are about the *database* rather than
#: about the seed (V8's cross-tenant stitching, the generated
#: ``is_retrieval_eligible`` column) stay unscoped, because scoping them would
#: weaken them.

ANN_INDEX = "evidence_embedding_ann_idx"
TOTAL_EVIDENCE = 18_035
HERO_SCOPED_EVIDENCE = 16_035
CURATED_EVIDENCE = 32
RETRACTION_FIXTURES = 3


def _run_seed(dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PROVENANCE_SEED_DATABASE"] = "provenance_ci"
    env["APP_ENV"] = "local"
    return subprocess.run(
        [sys.executable, "-m", "scripts.seed", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def seeded(migrated: str) -> str:
    """The DSN of a database that holds the seeded corpus.

    Runs the seed when the corpus is absent. It is not re-run when it is
    present: this lane asserts *properties of a seeded database*, and paying
    several minutes per module to re-prove the loader would make the suite
    unusable. ``test_seeding_twice_changes_no_row_count`` is the one test that
    deliberately re-runs it.
    """
    with psycopg.connect(migrated) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM evidence_items")
        row = cur.fetchone()
        present = int(row[0]) if row else 0
    if present == 0:
        result = _run_seed(migrated, "--profile", "all")
        if result.returncode != 0:
            pytest.fail(
                f"seed failed\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
            )
    return migrated


@pytest.fixture(scope="module")
def seed_tenants() -> list[object]:
    from scripts.seed.manifest import seed_tenant_ids

    return list(seed_tenant_ids())


@pytest.fixture
def query(seeded: str) -> Iterator[Callable[..., list[tuple[object, ...]]]]:
    conn = psycopg.connect(seeded)
    try:

        def _q(sql: str, params: tuple[object, ...] | None = None) -> list[tuple[object, ...]]:
            with conn.cursor() as cur:
                cur.execute(sql, params)  # type: ignore[arg-type]
                return list(cur.fetchall())

        yield _q
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Row counts -- the corpus canon
# ---------------------------------------------------------------------------


def test_evidence_items_total_is_18035(
    query: Callable[..., list[tuple[object, ...]]], seed_tenants: list[object]
) -> None:
    rows = query("SELECT count(*) FROM evidence_items WHERE tenant_id = ANY(%s)", (seed_tenants,))
    assert rows[0][0] == TOTAL_EVIDENCE


def test_hero_partition_holds_16035(query: Callable[..., list[tuple[object, ...]]]) -> None:
    from scripts.seed.tenants import HERO_USER

    rows = query("SELECT count(*) FROM evidence_items WHERE user_id = %s", (HERO_USER.id,))
    assert rows[0][0] == HERO_SCOPED_EVIDENCE


def test_isolation_tenants_hold_a_thousand_rows_each(
    query: Callable[..., list[tuple[object, ...]]],
) -> None:
    from scripts.seed.tenants import USERS

    for user in USERS:
        if user.slug in {"iso-a", "iso-b"}:
            rows = query("SELECT count(*) FROM evidence_items WHERE user_id = %s", (user.id,))
            assert rows[0][0] == 1_000, user.slug


def test_small_planes_have_their_canon_counts(
    query: Callable[..., list[tuple[object, ...]]], seed_tenants: list[object]
) -> None:
    from scripts.seed.manifest import scoped_count_sql

    counts = {
        "tenants": 3,
        "users": 3,
        "counterparties": 5,
        "relationships": 6,
        "contexts": 1,
        "cases": 10,
    }
    for table, expected in counts.items():
        assert query(scoped_count_sql(table), (seed_tenants,))[0][0] == expected, table


def test_no_evidence_row_is_stitched_across_tenants(
    query: Callable[..., list[tuple[object, ...]]],
) -> None:
    """Verification query V8, first half."""
    rows = query(
        "SELECT count(*) FROM evidence_items e JOIN source_artifacts a ON a.id = e.artifact_id "
        "WHERE a.tenant_id <> e.tenant_id OR a.user_id <> e.user_id"
    )
    assert rows[0][0] == 0


# ---------------------------------------------------------------------------
# The vector index -- section 23's first failure mode
# ---------------------------------------------------------------------------


def test_the_ann_index_is_back_after_the_seed(
    query: Callable[..., list[tuple[object, ...]]],
) -> None:
    rows = query(
        "SELECT index_name, column_name, seq_in_index FROM [SHOW INDEXES FROM evidence_items] "
        "WHERE index_name = %s ORDER BY seq_in_index",
        (ANN_INDEX,),
    )
    assert rows, f"{ANN_INDEX} is missing: the seed dropped it and never rebuilt it"
    assert rows[0][1] == "user_id", "the user_id prefix is what scopes the ANN partition"


def test_the_index_build_job_actually_finished(
    query: Callable[..., list[tuple[object, ...]]],
) -> None:
    """A ``CREATE INDEX`` that has returned is not one that has finished."""
    rows = query(
        "SELECT count(*) FROM [SHOW JOBS] "
        "WHERE description ILIKE %s AND status NOT IN ('succeeded','failed','canceled')",
        (f"%{ANN_INDEX}%",),
    )
    assert rows[0][0] == 0


def test_every_evidence_row_carries_a_1024_dim_titan_vector(
    query: Callable[..., list[tuple[object, ...]]], seed_tenants: list[object]
) -> None:
    missing = query(
        "SELECT count(*) FROM evidence_items WHERE tenant_id = ANY(%s) AND embedding IS NULL",
        (seed_tenants,),
    )
    assert missing[0][0] == 0
    provenance = query(
        "SELECT count(*) FROM evidence_items WHERE tenant_id = ANY(%s) AND "
        "(embedding_model <> 'amazon.titan-embed-text-v2:0' OR embedding_version <> 'v1')",
        (seed_tenants,),
    )
    assert provenance[0][0] == 0
    dims = query(
        "SELECT DISTINCT vector_dims(embedding) FROM evidence_items WHERE tenant_id = ANY(%s)",
        (seed_tenants,),
    )
    assert [row[0] for row in dims] == [1024]


def test_no_two_evidence_rows_share_one_vector(
    query: Callable[..., list[tuple[object, ...]]], seed_tenants: list[object]
) -> None:
    """``22_EVAL_DATASETS.md`` section 7.2 rule 4: no reused vectors.

    Compared over the text hash rather than the vector itself, because two rows
    with identical embedding input legitimately share a cache entry -- and a
    corpus with duplicate *text* is the defect that would produce them.
    """
    rows = query(
        "SELECT count(*) FROM (SELECT normalized_text_sha256 FROM evidence_items "
        "WHERE tenant_id = ANY(%s) GROUP BY normalized_text_sha256 HAVING count(*) > 1)",
        (seed_tenants,),
    )
    assert rows[0][0] == 0


# ---------------------------------------------------------------------------
# Retraction fixtures -- canon item C
# ---------------------------------------------------------------------------


def test_v11_returns_at_least_three_retracted_rows_that_kept_their_vectors(
    query: Callable[..., list[tuple[object, ...]]],
) -> None:
    rows = query(
        "SELECT id, retraction_status, (embedding IS NOT NULL) "
        "FROM evidence_items WHERE retraction_status <> 'ACTIVE'"
    )
    assert len(rows) >= RETRACTION_FIXTURES
    assert all(row[2] is True for row in rows), "a retraction deleted an embedding"


def test_the_three_fixtures_carry_their_canon_statuses(
    query: Callable[..., list[tuple[object, ...]]],
) -> None:
    from scripts.seed.retractions import RETRACTION_FIXTURES as FIXTURES

    for fixture in FIXTURES:
        rows = query(
            "SELECT retraction_status, retraction_reason_code, retracted_at IS NOT NULL "
            "FROM evidence_items WHERE id = %s",
            (fixture.id,),
        )
        assert rows, fixture.slug
        assert rows[0][0] == fixture.retraction_status, fixture.slug
        assert rows[0][1] == fixture.retraction_reason_code, fixture.slug
        assert rows[0][2] is True, fixture.slug


def test_retracted_rows_are_not_retrieval_eligible(
    query: Callable[..., list[tuple[object, ...]]],
) -> None:
    rows = query(
        "SELECT count(*) FROM evidence_items "
        "WHERE retraction_status <> 'ACTIVE' AND is_retrieval_eligible"
    )
    assert rows[0][0] == 0


def test_the_superseded_item_points_at_the_correct_31_may_evidence(
    query: Callable[..., list[tuple[object, ...]]],
) -> None:
    from scripts.seed.ids import sid

    wrong = sid("evidence", "isp-wrong-term-date")
    correct = sid("evidence", "isp-termination-effective-31-may")
    rows = query("SELECT retracted_by_evidence_id FROM evidence_items WHERE id = %s", (wrong,))
    assert rows[0][0] == correct


# ---------------------------------------------------------------------------
# Idempotence -- the acceptance assertion
# ---------------------------------------------------------------------------

ROW_COUNT_SQL = """
SELECT table_name, count FROM (
  SELECT 'evidence_items' AS table_name, count(*) AS count
    FROM evidence_items WHERE tenant_id = ANY(%(t)s)
  UNION ALL SELECT 'source_artifacts', count(*)
    FROM source_artifacts WHERE tenant_id = ANY(%(t)s)
  UNION ALL SELECT 'cases', count(*) FROM cases WHERE tenant_id = ANY(%(t)s)
  UNION ALL SELECT 'relationships', count(*) FROM relationships WHERE tenant_id = ANY(%(t)s)
  UNION ALL SELECT 'counterparties', count(*) FROM counterparties WHERE tenant_id = ANY(%(t)s)
  UNION ALL SELECT 'contexts', count(*) FROM contexts WHERE tenant_id = ANY(%(t)s)
  UNION ALL SELECT 'users', count(*) FROM users WHERE tenant_id = ANY(%(t)s)
  UNION ALL SELECT 'tenants', count(*) FROM tenants WHERE id = ANY(%(t)s)
  UNION ALL SELECT 'belief_versions', count(*) FROM belief_versions WHERE tenant_id = ANY(%(t)s)
) ORDER BY table_name
"""


@pytest.mark.slow
def test_seeding_twice_changes_no_row_count(
    seeded: str, query: Callable[..., list[tuple[object, ...]]], seed_tenants: list[object]
) -> None:
    """The ``T2.8`` acceptance assertion, run rather than hoped for."""
    before = query(ROW_COUNT_SQL, {"t": seed_tenants})
    result = _run_seed(seeded, "--profile", "all")
    after = query(ROW_COUNT_SQL, {"t": seed_tenants})
    assert before == after
    assert "rows_pending=0" in result.stdout, result.stdout
    assert "bedrock_live=0" in result.stdout, result.stdout


@pytest.mark.slow
def test_a_reseed_over_a_complete_corpus_does_not_drop_the_index(seeded: str) -> None:
    """Section 23's ordering is mandatory *for a load*, and only for a load.

    Rebuilding this index over 18,035 rows was measured at 52 minutes 56 seconds
    on this cluster. A seed that dropped it unconditionally would make the G2.6
    idempotence assertion -- run the seed twice, diff the row counts -- cost
    nearly two hours, and would leave the ANN index absent for most of that
    window. That is section 23's first failure mode reached by way of the fix
    for its second.
    """
    result = _run_seed(seeded, "--profile", "all")
    assert "ANN index left in place" in result.stdout, result.stdout
    assert "index_dropped=False" in result.stdout, result.stdout
    assert "bedrock_live=0" in result.stdout, result.stdout


@pytest.mark.slow
def test_seeding_twice_changes_no_row_identity(
    seeded: str, query: Callable[..., list[tuple[object, ...]]], seed_tenants: list[object]
) -> None:
    """Counts can match while contents drift; the bytes must match too."""
    digest_sql = (
        "SELECT count(*), sum(length(normalized_text)) FROM evidence_items "
        "WHERE tenant_id = ANY(%s) AND retraction_status = 'ACTIVE'"
    )
    before = query(digest_sql, (seed_tenants,))
    result = _run_seed(seeded, "--profile", "all")
    # Not `returncode == 0`: step 11 runs V1-V11 over the WHOLE database, so on a
    # shared provenance_ci another lane's in-flight fixture row turns that verdict
    # red for reasons that have nothing to do with this seed. What is being
    # asserted here is the seed's own effect, and that is visible in its report
    # line and in the rows it owns.
    assert "rows_pending=0" in result.stdout, result.stdout
    assert query(digest_sql, (seed_tenants,)) == before


def test_the_seed_refuses_to_run_outside_local_or_demo(seeded: str) -> None:
    """Sub-task 1: the guard, exercised rather than described."""
    env = dict(os.environ)
    env["APP_ENV"] = "production"
    env["PROVENANCE_SEED_DATABASE"] = "provenance_ci"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.seed", "--profile", "all"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "APP_ENV" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# Verification queries and the manifest
# ---------------------------------------------------------------------------


def test_all_eleven_verification_queries_hold(seeded: str) -> None:
    """DDL section 18, V1-V11. ``G2.5``.

    These are **global** invariants -- they range over every row in the
    database, not over the seed's tenants -- because that is what they are for:
    V1 asks whether *any* canonical belief version lacks grounding. On a shared
    ``provenance_ci`` that means another lane's half-built fixture can turn this
    red while the seed is entirely correct. Scoping them to the seeded tenants
    would remove the noise and also remove the point, so they stay global and a
    failure here is read before it is believed.
    """
    from scripts.seed.verify import run_verification

    report = run_verification(seeded)
    assert report.summary_line().startswith("V1 0"), report.summary_line()
    assert report.ok, report.summary_line()
    assert report.results["V11"] >= 3


def test_manifest_matches_the_seeded_row_counts(seeded: str) -> None:
    """``G2.6``: ``26 tables checked, 26 match``.

    Scoped to the seeded tenants. ``python -m tools.manifest_check`` runs the
    unscoped comparison and is the gate's own command; it reports 26/26 only on
    a database that holds nothing but the seed, which is what the demo database
    and a freshly reset CI database are and what ``provenance_ci`` is not while
    four other phases are testing against it.
    """
    from scripts.seed.manifest import compare

    comparison = compare(seeded, scoped=True)
    assert comparison.summary_line() == "26 tables checked, 26 match", comparison.mismatches
    assert comparison.ok


# ---------------------------------------------------------------------------
# seed-perturb -- the detector for a demo that passes on seeded state
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_seed_perturb_removes_the_outcome_bearing_rows(seeded: str) -> None:
    """``70_TASK_PLAN.md`` section 23.1 -- the detector for a demo that passes on
    seeded state rather than on logic.

    A suite unaffected by this is testing the seed file. The perturbation is
    applied in place and reversed exactly, because a truncate-and-reload round
    trip would cost two fifty-three-minute ANN index rebuilds, and a detector
    nobody can afford to run is not a detector.
    """
    from scripts.seed.cases import case_of
    from scripts.seed.manifest import seed_tenant_ids
    from scripts.seed.verify import run_verification

    tenants = seed_tenant_ids()

    def read() -> tuple[str, str, int, int]:
        with psycopg.connect(seeded) as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM cases WHERE id = %s", (case_of("landlord-deposit").id,))
            deposit = cur.fetchone()
            cur.execute("SELECT status FROM cases WHERE id = %s", (case_of("movers-damage").id,))
            damage = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM evidence_items WHERE tenant_id = ANY(%s) "
                "AND retraction_status <> 'ACTIVE'",
                (tenants,),
            )
            retracted = cur.fetchone()
            cur.execute("SELECT count(*) FROM evidence_items WHERE tenant_id = ANY(%s)", (tenants,))
            total = cur.fetchone()
        assert deposit and damage and retracted and total
        return str(deposit[0]), str(damage[0]), int(retracted[0]), int(total[0])

    baseline = read()
    assert baseline == ("WAITING", "WAITING", RETRACTION_FIXTURES, TOTAL_EVIDENCE)

    perturbed = _run_seed(seeded, "--profile", "all", "--perturb")
    assert "[perturb]" in perturbed.stdout, perturbed.stdout
    assert perturbed.returncode != 0, "a perturbed seed must fail its own verification"
    try:
        after = read()
        assert after[0] == "RESOLVED", "the deposit case must be left RESOLVED"
        assert after[1] == "RESOLVED", "the damage case must be left RESOLVED"
        assert after[2] == 0, "the retraction fixtures must be gone, so V11 drops to zero"
        assert after[3] == TOTAL_EVIDENCE - RETRACTION_FIXTURES

        # The point of the whole exercise: V11 now fails, loudly.
        report = run_verification(seeded)
        assert not report.ok
        assert any("V11" in failure for failure in report.failures), report.failures
    finally:
        restored = _run_seed(seeded, "--profile", "all", "--restore")
        assert "[restore]" in restored.stdout, restored.stdout

    # The restore is asserted by reading the rows back, not by the exit code:
    # step 11's verdict is global and another lane's fixture row can redden it.
    assert read() == baseline, "the restore did not put the seed back"


# ---------------------------------------------------------------------------
# USD 2,020.00 -- proved against the real schema, committed nowhere
# ---------------------------------------------------------------------------


def test_the_outstanding_total_is_2020_against_the_real_schema(
    seeded: str, role_dsn: Callable[[str], str]
) -> None:
    """The landing-screen figure, run as SQL, inside a transaction that rolls back.

    ``commitments`` is Kernel-written and step 9 waits for Phase 4, so the
    seeded table is legitimately empty and
    ``python -m scripts.seed --outstanding-total`` prints ``USD 0 over 0
    commitment rows``. That leaves two things unproved that this test proves
    without creating a second canonical writer:

    1. **The fixtures survive the schema.** ``ck_commitments_type`` is a closed
       vocabulary and ``REFUND`` / ``REIMBURSEMENT`` -- both entirely plausible,
       neither a member -- were the first values these fixtures carried.
       ``ck_commitments_outstanding_identity``, ``ck_commitments_partial_status``
       and ``ck_commitments_fulfilled_needs_payment`` all bind here too:
       ``10_DATABASE_DDL.md`` section 17.6 says "if the seed gets the arithmetic
       wrong the insert fails, which is the point".
    2. **The query shape is right.** ``sum(outstanding_amount)`` over the hero's
       commitments with no status filter is what the read model will run.

    Nothing is committed. The connection rolls back, the row count before equals
    the row count after, and the assertion is made over a second read on the same
    open transaction rather than over Python arithmetic.
    """
    from decimal import Decimal

    from scripts.seed.cases import case_of
    from scripts.seed.evidence import evidence_of
    from scripts.seed.ids import DEMO_ANCHOR_UTC, sid
    from scripts.seed.obligations import COMMITMENTS, outstanding_total
    from scripts.seed.tenants import HERO_TENANT, HERO_USER
    from scripts.seed.verify import OUTSTANDING_TOTAL_SQL

    # As pv_kernel_writer, because commitments and claims are Kernel-written
    # tables (10_DATABASE_DDL.md section 12) and running the probe as the
    # migrator would prove the arithmetic while proving nothing about the grant.
    conn = psycopg.connect(role_dsn("pv_kernel_writer"))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM commitments")
            row = cur.fetchone()
            assert row is not None
            before = int(row[0])

            for commitment in COMMITMENTS:
                evidence = evidence_of(commitment.source_claim_slug)
                claim_id = sid("claim", commitment.slug)
                cur.execute(
                    "INSERT INTO claims (id, tenant_id, user_id, case_id, relationship_id, "
                    "subject_type, subject_id, predicate, object_type, object_json, actor_type, "
                    "actor_id, evidence_id, claim_kind, authority_score, extraction_confidence, "
                    "recorded_at) VALUES (%s,%s,%s,%s,%s,'CASE',%s,%s,'STRING',%s,"
                    "'COUNTERPARTY',%s,%s,'COMMITMENT_CLAIM',%s,%s,%s)",
                    (
                        claim_id,
                        HERO_TENANT.id,
                        HERO_USER.id,
                        case_of(commitment.case_slug).id,
                        None,
                        case_of(commitment.case_slug).id,
                        evidence.predicate,
                        '{"seed": "rollback-probe"}',
                        commitment.obligor_id,
                        evidence.id,
                        Decimal("0.90"),
                        Decimal("0.97"),
                        DEMO_ANCHOR_UTC,
                    ),
                )
                cur.execute(
                    "INSERT INTO commitments (id, tenant_id, user_id, case_id, obligor_type, "
                    "obligor_id, beneficiary_type, beneficiary_id, commitment_type, description, "
                    "currency, committed_amount, fulfilled_amount, outstanding_amount, due_at, "
                    "source_claim_id, status, revision) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        commitment.id,
                        commitment.tenant_id,
                        commitment.user_id,
                        case_of(commitment.case_slug).id,
                        commitment.obligor_type,
                        commitment.obligor_id,
                        commitment.beneficiary_type,
                        commitment.beneficiary_id,
                        commitment.commitment_type,
                        commitment.description,
                        commitment.currency,
                        commitment.committed_amount,
                        commitment.fulfilled_amount,
                        commitment.outstanding_amount,
                        commitment.due_at,
                        claim_id,
                        commitment.status,
                        commitment.revision,
                    ),
                )

            cur.execute(OUTSTANDING_TOTAL_SQL, (HERO_TENANT.id, HERO_USER.id))
            result = cur.fetchone()
            assert result is not None
            total, rows = Decimal(str(result[0])), int(result[1])
            assert rows == 4
            assert total == Decimal("2020.00"), f"the landing screen would render {total}"
            assert total == outstanding_total()

            cur.execute(
                "SELECT c.commitment_type, c.status, c.outstanding_amount FROM commitments c "
                "WHERE c.commitment_type = 'SERVICE_TERMINATION'"
            )
            northline = cur.fetchall()
            assert len(northline) == 1
            assert northline[0][2] is None, (
                "Northline's obligation is non-monetary and contributes nothing to the "
                "total; a disputed balance changes status, never amount"
            )
    finally:
        conn.rollback()
        conn.close()

    with psycopg.connect(seeded) as check, check.cursor() as cur:
        cur.execute("SELECT count(*) FROM commitments")
        row = cur.fetchone()
        assert row is not None
        assert int(row[0]) == before, "the rollback probe left rows behind"
