"""``db/verify.sql`` - the V1-V11 verification queries, and their positive control.

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 18 - the eleven queries, transcribed
  verbatim into ``db/verify.sql``. "Every one of these must return zero rows"
  for V1-V10; V11 "must return AT LEAST 3 rows after seeding".
- ``docs/quality/23_PHASE_GATES.md`` section 23.7 - *the assertion that passes on
  an empty set*: "no negative assertion ships without a positive control. V10
  (no retracted row reachable) is paired with V11 (retracted rows exist and
  still carry embeddings, >= 3)."
- ``docs/quality/23_PHASE_GATES.md`` ``G2.5`` - ``make db-verify`` prints
  ``V1 0  V2 0 ... V11 3`` and exits 0 on a seeded database.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5, ``T2.7``.

What this module is defending against
-------------------------------------
``V1``-``V10`` return zero rows on an empty database, and they return zero rows
on a correct one. Those two facts are indistinguishable from the count alone,
and a suite that cannot tell them apart reports success for a database that has
never been looked at. So the contract asserted here is not "the query returned
zero" but:

1. every check reports **how many rows it examined** alongside what it returned,
   and a status of ``HOLDS`` (zero out of a non-empty population), ``VACUOUS``
   (zero out of nothing) or ``VIOLATED``;
2. ``V11`` - the positive control for ``V10`` - is 0 before the seed and >= 3
   after it, and 0 before the seed is reported as ``VACUOUS``, never as a pass;
3. removing the retraction filter that ``V10`` depends on makes ``V10``
   non-zero. Without that mutation ``V10`` passes whether or not it filters
   anything, which is exactly the failure section 23.7 names.

Why fixtures instead of the seed
--------------------------------
``T2.8`` owns the seed and had not landed when this was written. Skipping the
"after seeding" half would leave the positive control unproven, so this module
builds its own three retraction fixtures - one ``SUPERSEDED``, one
``RETRACTED``, one ``QUARANTINED``, mirroring DDL section 17.8 - inside a
transaction that is rolled back. Every assertion below is therefore real today
and stays real once the seed exists.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parents[4]
VERIFY_SQL = REPO_ROOT / "db" / "verify.sql"
DDL_SPEC = REPO_ROOT / "docs" / "specs" / "10_DATABASE_DDL.md"

#: The eleven check ids, in the order the summary line must print them.
CHECK_IDS: tuple[str, ...] = tuple(f"V{n}" for n in range(1, 12))

#: DDL section 18: V1-V10 return zero rows, V11 returns at least three.
ZERO_CHECKS: tuple[str, ...] = CHECK_IDS[:10]
POSITIVE_CONTROL = "V11"

#: DDL section 17.8. A ``V11`` below three means these were deleted rather than
#: retracted, and canon item C - retraction filtering - is untested.
RETRACTION_FIXTURES: tuple[tuple[str, str, str], ...] = (
    ("isp-wrong-term-date", "SUPERSEDED", "EXTRACTION_ERROR"),
    ("movers-350-claim", "RETRACTED", "USER_CORRECTION"),
    ("injected-instruction", "QUARANTINED", "ADVERSARIAL_CONTENT"),
)

#: Statuses a check may report. ``VACUOUS`` is the one that matters: it is how
#: "zero because the invariant holds" is told apart from "zero because there was
#: nothing to check".
CHECK_STATUSES = frozenset({"HOLDS", "VACUOUS", "VIOLATED"})

#: Verdict codes ``db/verify.sql`` may emit. ``make db-verify`` maps these to
#: exit codes; the mapping is stated in the Makefile target, the meaning here.
VERDICT_CODES = frozenset(
    {
        "PASS",
        "PASS_PARTIAL",
        "FAIL_INVARIANT",
        "FAIL_V11_UNDERSEEDED",
        "VACUOUS_EMPTY_CORPUS",
    }
)

#: ``amazon.titan-embed-text-v2:0`` is the only value ``ck_evidence_embedding_model``
#: admits, and section 17.8 requires the retracted rows to keep their vectors.
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024


# ---------------------------------------------------------------------------
# Parsing the output of db/verify.sql
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One ``CHECK`` line from ``db/verify.sql``."""

    check_id: str
    returned: int
    examined: int
    expect: str
    status: str
    population: str


@dataclass(frozen=True)
class VerifyOutput:
    """The whole result of running ``db/verify.sql`` once."""

    checks: dict[str, Check]
    summary: str
    verdict_code: str
    verdict_message: str
    lines: tuple[str, ...]

    def returned(self, check_id: str) -> int:
        return self.checks[check_id].returned

    def examined(self, check_id: str) -> int:
        return self.checks[check_id].examined


def _parse_verify(lines: list[str]) -> VerifyOutput:
    checks: dict[str, Check] = {}
    summary = ""
    verdict_code = ""
    verdict_message = ""
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "CHECK":
            fields = dict(token.split("=", 1) for token in parts[2:] if "=" in token)
            checks[parts[1]] = Check(
                check_id=parts[1],
                returned=int(fields["returned"]),
                examined=int(fields["examined"]),
                expect=fields["expect"],
                status=fields["status"],
                population=fields["population"],
            )
        elif parts[0] == "SUMMARY":
            summary = line[len("SUMMARY ") :]
        elif parts[0] == "VERDICT":
            verdict_code = parts[1]
            verdict_message = line[len(f"VERDICT {verdict_code} ") :]
    return VerifyOutput(
        checks=checks,
        summary=summary,
        verdict_code=verdict_code,
        verdict_message=verdict_message,
        lines=tuple(lines),
    )


def _run_verify(cursor: psycopg.Cursor) -> VerifyOutput:
    """Execute ``db/verify.sql`` as written and parse its output.

    The file is one statement on purpose: psycopg can run it whole, so this test
    exercises the same bytes ``make db-verify`` feeds to ``psql``, not a
    re-implementation of them.
    """
    cursor.execute(VERIFY_SQL.read_text(encoding="utf-8"))
    rows = cursor.fetchall()
    return _parse_verify([str(row[0]) for row in rows])


# ---------------------------------------------------------------------------
# Extracting the queries from the spec, so "verbatim" is checked and not claimed
# ---------------------------------------------------------------------------


def _spec_statements() -> list[tuple[str, str]]:
    """Return ``(check_id, normalized_sql)`` for every statement in DDL section 18."""
    text = DDL_SPEC.read_text(encoding="utf-8")
    start = text.index("## 18. Post-migration verification queries")
    block = text[start:]
    fence_open = block.index("```sql") + len("```sql")
    fence_close = block.index("```", fence_open)
    body = block[fence_open:fence_close]

    statements: list[tuple[str, str]] = []
    current_id = ""
    buffer: list[str] = []
    for raw in body.splitlines():
        marker = re.match(r"\s*--\s*(V\d+)\.", raw)
        if marker:
            current_id = marker.group(1)
        stripped = raw.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buffer.append(stripped)
        if stripped.endswith(";"):
            statements.append((current_id, _normalize_sql(" ".join(buffer))))
            buffer = []
    return statements


def _normalize_sql(sql: str) -> str:
    """Collapse whitespace and drop the trailing semicolon. Nothing else."""
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").strip()


# ---------------------------------------------------------------------------
# Fixture rows: one tenant, one user, one artifact, five evidence items
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetractionFixture:
    """Ids of the rows inserted by :func:`retraction_fixture`."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    artifact_id: uuid.UUID
    active_ids: tuple[uuid.UUID, ...]
    retracted_ids: tuple[uuid.UUID, ...]


def _embedding_literal(seed: int) -> str:
    """A deterministic 1024-dimension vector literal.

    Not a real Titan embedding and not pretending to be one: V11 asserts that a
    retracted row still *has* a vector, never what the vector means.
    """
    return (
        "[" + ",".join(f"{((seed + i) % 97) / 97:.6f}" for i in range(EMBEDDING_DIMENSIONS)) + "]"
    )


def _insert_identity(cursor: psycopg.Cursor) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
        (tenant_id, "T2.7 verify fixture tenant", f"t-{tenant_id.hex[:16]}"),
    )
    cursor.execute(
        "INSERT INTO users (id, tenant_id, cognito_sub) VALUES (%s, %s, %s)",
        (user_id, tenant_id, f"sub-{user_id.hex}"),
    )
    return tenant_id, user_id


def _insert_artifact(
    cursor: psycopg.Cursor,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    artifact_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO source_artifacts (id, tenant_id, user_id, source_type, s3_bucket, s3_key,"
        " content_sha256, size_bytes, mime_type, received_at, parser_status, parser_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            artifact_id,
            tenant_id,
            user_id,
            "SEED_FIXTURE",
            "pv-fixtures",
            f"raw/t27/{artifact_id.hex}.eml",
            artifact_id.bytes + artifact_id.bytes,
            2048,
            "message/rfc822",
            now,
            "PARSED",
            "fixture-1.0.0",
        ),
    )
    return artifact_id


def _insert_evidence(
    cursor: psycopg.Cursor,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    text: str,
    now: datetime,
    seed: int,
    retraction_status: str = "ACTIVE",
    reason_code: str | None = None,
    retracted_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert one evidence item, with an embedding, retracted or not.

    ``ck_evidence_retraction_consistent`` requires ``retracted_at`` and
    ``retraction_reason_code`` together with a non-``ACTIVE`` status, and
    ``ck_evidence_embedding_provenance`` requires the model, version and
    timestamp beside any vector. Both are the schema refusing an incoherent row,
    so the fixture supplies them rather than working around them.
    """
    evidence_id = uuid.uuid4()
    retracted_at = None if retraction_status == "ACTIVE" else now
    cursor.execute(
        "INSERT INTO evidence_items (id, tenant_id, user_id, artifact_id, evidence_type,"
        " normalized_text, normalized_text_sha256, observed_at, extraction_confidence,"
        " retraction_status, retracted_at, retraction_reason_code, retracted_by_evidence_id,"
        " embedding, embedding_model, embedding_version, embedding_generated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s::VECTOR(1024), %s, %s, %s)",
        (
            evidence_id,
            tenant_id,
            user_id,
            artifact_id,
            "STATEMENT",
            text,
            evidence_id.bytes + evidence_id.bytes,
            now,
            "0.9500",
            retraction_status,
            retracted_at,
            reason_code,
            retracted_by,
            _embedding_literal(seed),
            EMBEDDING_MODEL,
            "v1",
            now,
        ),
    )
    return evidence_id


@pytest.fixture
def retraction_fixture(
    db_connection: psycopg.Connection, frozen_clock
) -> Iterator[RetractionFixture]:
    """Two active evidence items and the three retraction fixtures of DDL 17.8.

    Written inside the test transaction and rolled back by ``db_connection``, so
    this leaves nothing behind for ``T2.8``'s seed to collide with.
    """
    now = frozen_clock.now_utc()
    with db_connection.cursor() as cur:
        tenant_id, user_id = _insert_identity(cur)
        artifact_id = _insert_artifact(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        correct = _insert_evidence(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            artifact_id=artifact_id,
            text="service termination effective 31 May",
            now=now,
            seed=1,
        )
        other_active = _insert_evidence(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            artifact_id=artifact_id,
            text="final invoice issued 20 May",
            now=now,
            seed=2,
        )
        retracted: list[uuid.UUID] = []
        for index, (slug, status, reason) in enumerate(RETRACTION_FIXTURES, start=3):
            retracted.append(
                _insert_evidence(
                    cur,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    artifact_id=artifact_id,
                    text=f"retraction fixture {slug}",
                    now=now,
                    seed=index,
                    retraction_status=status,
                    reason_code=reason,
                    retracted_by=correct if status == "SUPERSEDED" else None,
                )
            )
    yield RetractionFixture(
        tenant_id=tenant_id,
        user_id=user_id,
        artifact_id=artifact_id,
        active_ids=(correct, other_active),
        retracted_ids=tuple(retracted),
    )


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_verify_sql_exists_and_is_one_statement() -> None:
    """One statement, so psql and psycopg run identical bytes.

    ``make db-verify`` reads the verdict out of this file's own output. If the
    file were a script of eleven separate statements, the target would have to
    reconstruct the verdict itself and the two would drift.
    """
    assert VERIFY_SQL.is_file(), f"{VERIFY_SQL} does not exist"
    body = re.sub(r"--[^\n]*", "", VERIFY_SQL.read_text(encoding="utf-8"))
    body = re.sub(r"'(?:[^']|'')*'", "''", body)  # a ';' inside prose is not a statement end
    assert body.count(";") == 1, "db/verify.sql must be exactly one SQL statement"


def test_verify_sql_transcribes_every_spec_query_verbatim() -> None:
    """Every statement in DDL section 18 appears in ``db/verify.sql`` unchanged.

    ``T2.7`` says "transcribe V1-V11 from section 18 verbatim". A hand-rewritten
    predicate is how a verification query stops verifying what the spec says and
    starts verifying what the implementer remembered.
    """
    file_sql = _normalize_sql(VERIFY_SQL.read_text(encoding="utf-8"))
    spec = _spec_statements()
    assert {check_id for check_id, _ in spec} == set(CHECK_IDS)
    missing = [(check_id, sql) for check_id, sql in spec if sql not in file_sql]
    assert not missing, "not transcribed verbatim: " + ", ".join(
        f"{check_id}: {sql[:70]}..." for check_id, sql in missing
    )


def test_verify_sql_names_v11_as_the_positive_control_for_v10() -> None:
    """Section 23.7: the pair must be stated in writing, next to the query.

    "Any new 'expect zero' query must arrive with its pair, named
    ``*_positive_control``, and the reviewer checks for the pair before reading
    the result." A reviewer reading ``db/verify.sql`` must find the pairing
    without holding the gate document open beside it.
    """
    text = VERIFY_SQL.read_text(encoding="utf-8")
    assert "positive control" in text.lower()
    assert "23.7" in text
    assert re.search(r"V10.*V11|V11.*V10", text, flags=re.DOTALL)


def test_verify_sql_failure_message_names_the_three_retraction_fixtures() -> None:
    """``V11 < 3`` must fail with the fixture names, not with a bare number.

    ``T2.7`` acceptance: "V11 < 3 after a seed exits non-zero with a message
    naming the retraction fixtures." The names are what tell the reader which
    three rows to go and look for.
    """
    text = VERIFY_SQL.read_text(encoding="utf-8")
    for slug, _status, _reason in RETRACTION_FIXTURES:
        assert slug in text, f"the V11 failure message does not name {slug}"


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def test_every_check_reports_what_it_examined(db_connection: psycopg.Connection) -> None:
    """The vacuity discrimination, asserted as a property of the output.

    A check that prints only ``0`` cannot be told apart from a check that ran
    against nothing. Each line therefore carries ``examined=`` and a status of
    ``HOLDS``, ``VACUOUS`` or ``VIOLATED``.
    """
    with db_connection.cursor() as cur:
        out = _run_verify(cur)
    assert set(out.checks) == set(CHECK_IDS), f"expected {CHECK_IDS}, got {sorted(out.checks)}"
    for check_id in CHECK_IDS:
        check = out.checks[check_id]
        assert check.status in CHECK_STATUSES, f"{check_id} status {check.status!r}"
        assert check.examined >= 0
        assert check.population, f"{check_id} does not name the population it examined"
        if check.expect == "ZERO" and check.returned == 0:
            expected = "VACUOUS" if check.examined == 0 else "HOLDS"
            assert check.status == expected, (
                f"{check_id} returned 0 over {check.examined} examined rows "
                f"but reported {check.status}"
            )
    assert out.verdict_code in VERDICT_CODES, f"unknown verdict {out.verdict_code!r}"


def test_summary_line_is_the_exact_string_g2_5_greps_for(
    db_connection: psycopg.Connection,
) -> None:
    """``G2.5`` reads ``V1 0  V2 0 ... V11 3`` - two spaces, V1 through V11."""
    with db_connection.cursor() as cur:
        out = _run_verify(cur)
    fields = out.summary.split("  ")
    assert len(fields) == 11, f"summary is not eleven fields: {out.summary!r}"
    for check_id, field in zip(CHECK_IDS, fields, strict=True):
        name, _, count = field.partition(" ")
        assert name == check_id
        assert int(count) == out.returned(check_id)


def test_v1_through_v10_return_zero_rows(db_connection: psycopg.Connection) -> None:
    """DDL section 18: "Every one of these must return zero rows."

    On an empty database this is trivially true, which is why the assertion below
    is paired with a status check: a zero over an empty population is recorded as
    ``VACUOUS``, and the run's verdict says so.
    """
    with db_connection.cursor() as cur:
        out = _run_verify(cur)
    violated = {cid: out.returned(cid) for cid in ZERO_CHECKS if out.returned(cid) != 0}
    assert not violated, f"verification queries returned rows: {violated}"


def test_v11_is_zero_and_vacuous_before_the_seed_and_at_least_three_after(
    db_connection: psycopg.Connection,
) -> None:
    """``V11`` is 0 on an empty database - and that is not a pass.

    ``T2.7`` acceptance: "on an empty database V11 prints 0 and that is correct".
    Correct, and unproven: the run must report ``VACUOUS_EMPTY_CORPUS`` rather
    than success. Once ``T2.8`` has seeded, the other branch binds: ``V11 >= 3``
    and the verdict is a real pass. Both branches assert; neither is a no-op.
    """
    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM evidence_items")
        row = cur.fetchone()
        assert row is not None
        corpus = int(row[0])
        out = _run_verify(cur)

    if corpus == 0:
        assert out.returned(POSITIVE_CONTROL) == 0
        assert out.checks[POSITIVE_CONTROL].status == "VACUOUS"
        assert out.verdict_code == "VACUOUS_EMPTY_CORPUS", (
            "an empty corpus must not be reported as a pass; "
            f"verdict was {out.verdict_code} - {out.verdict_message}"
        )
    else:
        assert out.returned(POSITIVE_CONTROL) >= 3, (
            f"V11 returned {out.returned(POSITIVE_CONTROL)} against a corpus of {corpus} rows; "
            "DDL section 17.8 requires three retraction fixtures"
        )
        assert out.checks[POSITIVE_CONTROL].status == "HOLDS"


def test_v11_counts_retraction_fixtures_and_they_keep_their_embeddings(
    db_connection: psycopg.Connection,
    retraction_fixture: RetractionFixture,
) -> None:
    """After seeding, ``V11`` returns at least three - with vectors intact.

    DDL section 17.8: "All three keep their embeddings." A fixture deleted rather
    than retracted, or stripped of its vector, would make ``V10`` pass for the
    wrong reason forever.
    """
    with db_connection.cursor() as cur:
        out = _run_verify(cur)
        cur.execute(
            "SELECT id, retraction_status, (embedding IS NOT NULL) AS still_embedded "
            "FROM evidence_items WHERE retraction_status <> 'ACTIVE' AND id = ANY(%s)",
            (list(retraction_fixture.retracted_ids),),
        )
        fixture_rows = cur.fetchall()

    assert out.returned(POSITIVE_CONTROL) >= 3, out.verdict_message
    assert out.checks[POSITIVE_CONTROL].status == "HOLDS"
    assert out.verdict_code != "VACUOUS_EMPTY_CORPUS"
    assert len(fixture_rows) == 3
    assert {str(row[1]) for row in fixture_rows} == {"SUPERSEDED", "RETRACTED", "QUARANTINED"}
    assert all(bool(row[2]) for row in fixture_rows), "a retraction fixture lost its embedding"


def test_v10_returns_zero_while_retracted_rows_exist(
    db_connection: psycopg.Connection,
    retraction_fixture: RetractionFixture,
) -> None:
    """The negative half of the pair, over a population that is not empty."""
    with db_connection.cursor() as cur:
        out = _run_verify(cur)

    assert out.returned("V10") == 0
    assert out.examined("V10") > 0, "V10 examined nothing; its zero would prove nothing"
    assert out.checks["V10"].status == "HOLDS"


def test_v10_retraction_filter_positive_control(
    db_connection: psycopg.Connection,
    retraction_fixture: RetractionFixture,
) -> None:
    """Section 23.7: removing the retraction predicate must make V10 non-zero.

    ``V10`` reads ``agent_evidence_retrieval_v1``, and the filter that makes it
    return zero lives *inside that view* (``WHERE e.retraction_status =
    'ACTIVE'``). Two mutations are applied, both of which must fire:

    ``M1`` the view is replaced by the same SELECT with its retraction filter
    deleted - the mutant a careless migration would ship. V10 must then return
    the three retracted rows.

    ``M2`` V10's own selection predicate ``e.retraction_status <> 'ACTIVE'`` is
    deleted, so the query returns whatever the view exposes. Non-zero here proves
    the query reaches rows at all, i.e. that the zero in M0 is not an artefact of
    an empty view.

    Without M1 this test would pass against a view that filters nothing.
    """
    unmutated = """
        SELECT v.evidence_id
        FROM agent_evidence_retrieval_v1 v
        JOIN evidence_items e ON e.id = v.evidence_id
        WHERE e.retraction_status <> 'ACTIVE'
    """
    mutant_view_filter_removed = """
        WITH agent_evidence_retrieval_v1 AS (
            SELECT e.tenant_id, e.user_id, e.id AS evidence_id
            FROM evidence_items e
            JOIN source_artifacts a
              ON (a.tenant_id = e.tenant_id AND a.user_id = e.user_id) AND a.id = e.artifact_id
            -- WHERE e.retraction_status = 'ACTIVE'   <-- the mutation
        )
        SELECT v.evidence_id
        FROM agent_evidence_retrieval_v1 v
        JOIN evidence_items e ON e.id = v.evidence_id
        WHERE e.retraction_status <> 'ACTIVE'
    """
    mutant_selection_removed = """
        SELECT v.evidence_id
        FROM agent_evidence_retrieval_v1 v
        JOIN evidence_items e ON e.id = v.evidence_id
    """

    with db_connection.cursor() as cur:
        cur.execute(unmutated)
        baseline = [row[0] for row in cur.fetchall()]
        cur.execute(mutant_view_filter_removed)
        m1 = {str(row[0]) for row in cur.fetchall()}
        cur.execute(mutant_selection_removed)
        m2 = cur.fetchall()

    assert baseline == [], f"V10 is not zero before mutation: {baseline}"
    assert len(m1) >= 3, (
        "removing the view's retraction filter did NOT make V10 non-zero. "
        "V10 is passing vacuously - see 23_PHASE_GATES.md section 23.7."
    )
    assert {str(eid) for eid in retraction_fixture.retracted_ids} <= m1
    assert len(m2) > 0, "V10 reaches no rows at all; its zero is an empty-set artefact"


def test_v11_under_three_on_a_populated_corpus_is_a_failure(
    db_connection: psycopg.Connection,
    frozen_clock,
) -> None:
    """A corpus with evidence but no retractions must fail, not pass.

    This is the ``V11 < 3`` arm of the acceptance criterion. It can only be
    observed while the database holds no retracted rows of its own; once
    ``T2.8``'s seed has landed the three fixtures are always present, and the
    assertion then flips to the seeded contract rather than being skipped.
    """
    now = frozen_clock.now_utc()
    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM evidence_items WHERE retraction_status <> 'ACTIVE'")
        row = cur.fetchone()
        assert row is not None
        already_retracted = int(row[0])

        tenant_id, user_id = _insert_identity(cur)
        artifact_id = _insert_artifact(cur, tenant_id=tenant_id, user_id=user_id, now=now)
        _insert_evidence(
            cur,
            tenant_id=tenant_id,
            user_id=user_id,
            artifact_id=artifact_id,
            text="an active evidence item and no retraction anywhere",
            now=now,
            seed=11,
        )
        out = _run_verify(cur)

    assert out.examined(POSITIVE_CONTROL) > 0, "the corpus is not populated; nothing was tested"
    if already_retracted == 0:
        assert out.returned(POSITIVE_CONTROL) < 3
        assert out.verdict_code == "FAIL_V11_UNDERSEEDED"
        assert out.checks[POSITIVE_CONTROL].status == "VIOLATED"
        for slug, _status, _reason in RETRACTION_FIXTURES:
            assert slug in out.verdict_message, f"the failure message does not name {slug}"
    else:
        assert out.returned(POSITIVE_CONTROL) >= 3
        assert out.verdict_code in {"PASS", "PASS_PARTIAL"}
