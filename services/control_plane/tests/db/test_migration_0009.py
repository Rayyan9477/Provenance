"""Migration-chain tests for ``0009_gemini_embedding_plane``.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` — *Bedrock model id canon* (the trap this
  revision must not repeat: a documented id is not an invocable id) and
  *Embeddings*.
- ``PIVOT.md`` sections 5 and 6 — the embedding width decision (1536) and the
  measured cost of the ANN rebuild.
- ``docs/specs/10_DATABASE_DDL.md`` sections 4, 5 and 16.
- ``docs/ops/41_RUNBOOK.md`` section 4.2 — the mandatory seed order, and the
  52-55 minute ANN index build.

Why this module never runs the migration
----------------------------------------
``0009`` is **not applied to any database yet**. It invalidates all 18,035
Titan vectors, and the re-embed that repairs them needs a Gemini API key this
machine does not have. So this module deliberately does **not** use the
``migrated`` / ``db_connection`` fixtures from ``conftest.py``: those run
``alembic upgrade head``, and with ``0009`` on the chain ``head`` is ``0009``.

The live half instead replays the revision's own SQL constants against a
``_pv_``-prefixed **scratch** table shaped like the embedding half of
``evidence_items``. ``conftest.py`` already sanctions that prefix, and
``evidence_items`` itself is never touched. A migration whose DDL has only been
read is not a migration that runs, and this cluster is the only place the
answer lives: CockroachDB rejects an in-place ``VECTOR`` width change two
different ways, and neither one is discoverable from the Alembic API.
"""

from __future__ import annotations

import ast
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parents[4]
REVISION_PATH = REPO_ROOT / "db" / "migrations" / "versions" / "0009_gemini_embedding_plane.py"

#: The August 2026 Gemini canon this revision moves the database to.
GEMINI_EMBEDDING_SPELLINGS: frozenset[str] = frozenset(
    {"gemini-embedding-2", "gemini-embedding-2-preview"}
)
GEMINI_DIMENSIONS = 1536
GEMINI_EMBEDDING_VERSION = "v2"
GEMINI_TIER_R = "gemini-3.7-flash"
GEMINI_TIER_R_FALLBACK = "gemini-3.6-flash"
GEMINI_TIER_E = "gemini-3.5-flash-lite"

#: What must no longer be reachable through the database boundary.
TITAN_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
TITAN_DIMENSIONS = 1024
SUPERSEDED_PROPOSAL_MODELS: frozenset[str] = frozenset(
    {
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "us.anthropic.claude-opus-4-6-v1",
        "us.anthropic.claude-sonnet-4-6",
    }
)

#: The probe transcript that will settle the two-spelling question.
PROBE_LEDGER = "ops/gemini-probe.txt"

DML_KEYWORDS: tuple[str, ...] = ("INSERT", "UPDATE", "DELETE", "UPSERT", "TRUNCATE")

_STRING_LITERAL = re.compile(r"'([A-Za-z0-9_.:/+-]+)'")


# --------------------------------------------------------------------------
# Parsers, borrowed verbatim in behaviour from test_migrations.py
# --------------------------------------------------------------------------


def _executed_sql(source: str) -> str:
    """Every string literal in *source* that is not a docstring, SQL-uncommented.

    Same contract as ``test_migrations.py::_executed_sql``: a docstring or a
    ``--`` comment naming a forbidden keyword is documentation and must stay
    legal, while the SQL that will actually run is read.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    joined = "\n".join(literals)
    without_block_comments = re.sub(r"/\*.*?\*/", " ", joined, flags=re.S)
    return "\n".join(line.split("--", 1)[0] for line in without_block_comments.splitlines())


def _check_members(statement: str) -> frozenset[str]:
    """The string literals inside a statement — i.e. the closed vocabulary."""
    return frozenset(_STRING_LITERAL.findall(statement))


@pytest.fixture(scope="module")
def source() -> str:
    assert REVISION_PATH.is_file(), f"missing revision file {REVISION_PATH}"
    return REVISION_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def module() -> Any:
    """The revision, imported. Alembic revisions are plain modules."""
    import importlib.util

    assert REVISION_PATH.is_file(), f"missing revision file {REVISION_PATH}"
    spec = importlib.util.spec_from_file_location("pv_migration_0009", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


# ==========================================================================
# Static half — the revision file as text and as an importable module
# ==========================================================================


def test_the_revision_extends_the_chain_linearly(module: Any) -> None:
    """0009 follows 0008 and branches nothing."""
    assert module.revision == "0009_gemini_embedding_plane"
    assert module.down_revision == "0008_events_infrastructure"
    assert module.branch_labels is None
    assert module.depends_on is None


def test_the_revision_mixes_no_ddl_and_dml(source: str) -> None:
    """DDL section 16 rule 1, and the reason the embeddings are *dropped*.

    Nulling 18,035 embeddings with ``UPDATE ... SET embedding = NULL`` would be
    a write, and CockroachDB rejects a schema change that follows a write in the
    same transaction. Dropping and re-adding the column reaches the same state
    through DDL alone, which is why this revision can null the corpus at all.
    """
    code = _executed_sql(source)
    offenders = [
        keyword
        for keyword in DML_KEYWORDS
        if re.search(rf"(?i)\b{keyword}\s+(INTO|FROM|\w+\s+SET|TABLE)\b", code)
    ]
    assert offenders == [], f"0009 mixes DDL and DML: {offenders}"


def test_the_revision_is_hand_written_sql(source: str) -> None:
    """DDL section 16 rule 4: ``op.execute()`` with literal SQL, no autogenerate."""
    assert "op.create_table(" not in source
    assert "### commands auto generated" not in source
    assert "op.execute(" in source


def test_downgrade_is_implemented_and_recorded_as_local_only(source: str, module: Any) -> None:
    """DDL section 16 rule 3, and the chain's own convention for saying so."""
    assert callable(module.downgrade)
    assert module.DOWNGRADE_DDL, "downgrade executes nothing"
    lowered = source.lower()
    assert "local iteration" in lowered
    assert "phase 13" in lowered


# ---- fact 1: the column width -------------------------------------------


def test_the_embedding_column_becomes_1536_wide(module: Any) -> None:
    """`PIVOT.md` section 5: 1536, on Google's recommended list."""
    assert module.EMBEDDING_DIMENSIONS == GEMINI_DIMENSIONS
    upgrade_sql = "\n".join(module.UPGRADE_DDL)
    assert re.search(r"(?i)embedding\s+VECTOR\s*\(\s*1536\s*\)", upgrade_sql)
    assert not re.search(r"(?i)VECTOR\s*\(\s*1024\s*\)", upgrade_sql)


def test_the_width_change_is_a_drop_and_recreate_not_an_alter(module: Any) -> None:
    """Measured on this cluster, twice, and both refusals are hard.

    ``ALTER TABLE evidence_items ALTER COLUMN embedding SET DATA TYPE
    VECTOR(1536)`` fails two independent ways here:

    - with the ANN index present: ``unimplemented: ALTER COLUMN TYPE requiring
      rewrite of on-disk data is currently not supported for columns that are
      part of an index``;
    - with the index gone but rows present: ``expected 1536 dimensions, not
      1024`` — raised in the *post-commit backfill*, i.e. after the statement
      has already been accepted.

    So the revision must not contain that statement at all. A migration that
    fails in a post-commit phase is the worst kind: it has already reported
    success to the operator.
    """
    upgrade_sql = "\n".join(module.UPGRADE_DDL)
    assert not re.search(
        r"(?i)ALTER\s+COLUMN\s+embedding\s+(SET\s+DATA\s+)?TYPE", upgrade_sql
    ), "in-place ALTER COLUMN TYPE is rejected by this cluster; drop and recreate instead"
    assert re.search(r"(?i)DROP\s+COLUMN\s+(IF\s+EXISTS\s+)?embedding\b", upgrade_sql)
    assert re.search(r"(?i)ADD\s+COLUMN\s+embedding\b", upgrade_sql)


def test_the_ann_index_is_recreated_last(module: Any) -> None:
    """Runbook section 4.2: bulk-load first, build the ANN index after.

    Order is the whole point. The index build was measured at 52m56s and 55m12s
    over the full corpus; anything that lands rows *after* it has to pay that
    again, and ``IMPORT INTO`` is refused outright while a vector index exists.
    """
    statements = list(module.UPGRADE_DDL)
    creates = [i for i, s in enumerate(statements) if re.search(r"(?i)CREATE\s+VECTOR\s+INDEX", s)]
    assert len(creates) == 1, f"expected exactly one ANN index build, found {len(creates)}"
    assert creates[0] == len(statements) - 1, (
        "the ANN index must be the LAST statement of upgrade(); "
        f"it is at position {creates[0]} of {len(statements)}"
    )
    assert re.search(
        r"(?i)CREATE\s+VECTOR\s+INDEX\s+evidence_embedding_ann_idx\s+ON\s+evidence_items\s*\(\s*user_id",
        statements[-1],
    ), "the ANN index keeps its canon name and its user_id prefix column"


def test_the_docstring_records_the_cost_and_the_destruction(source: str) -> None:
    """An operator reading this file must not have to discover any of it live."""
    assert "IMPORT INTO" in source, "the IMPORT INTO limitation is not recorded"
    assert re.search(r"52", source) and re.search(
        r"55", source
    ), "the measured build time is absent"
    assert "one to two minutes" in source, "the wrong runbook figure is not corrected in place"
    assert "18,035" in source, "the size of the corpus this destroys is not stated"
    assert re.search(
        r"(?i)\bNULL\b", source
    ), "the revision does not say the embeddings become NULL"


# ---- fact 2: the embedding-model CHECK -----------------------------------


def test_the_embedding_check_admits_both_documented_spellings(module: Any) -> None:
    """Two spellings, because we have not probed which one invokes.

    The models page spells it ``gemini-embedding-2-preview``; the embeddings
    page spells it ``gemini-embedding-2``. On Bedrock every documented-but-
    unprobed id turned out to be wrong (``CANONICAL_DECISIONS.md`` → *Bedrock
    model id canon*: ``list-foundation-models`` returns ids that are not
    invocable). A CHECK admitting both candidates is honest; one that picks is a
    guess wearing a constraint's clothes.
    """
    assert frozenset(module.EMBEDDING_MODEL_IDS) == GEMINI_EMBEDDING_SPELLINGS
    statement = module.EMBEDDING_MODEL_CHECK_DDL
    assert "ck_evidence_embedding_model" in statement
    assert _check_members(statement) >= GEMINI_EMBEDDING_SPELLINGS
    assert TITAN_EMBEDDING_MODEL not in _check_members(statement)


def test_the_two_spellings_are_marked_for_reduction_to_one(source: str) -> None:
    """The comment that stops two candidates from becoming permanent."""
    assert PROBE_LEDGER in source, (
        f"the revision must name {PROBE_LEDGER} as the transcript that removes "
        "the losing spelling"
    )


def test_titan_is_gone_from_every_upgrade_statement(module: Any) -> None:
    """The whole point of the revision: a Titan id is refused at the boundary.

    Scoped to ``UPGRADE_DDL`` rather than to the file, because ``DOWNGRADE_DDL``
    must still name Titan — restoring the 0008 shape is what a downgrade is.
    """
    upgrade_sql = "\n".join(module.UPGRADE_DDL)
    assert TITAN_EMBEDDING_MODEL not in upgrade_sql
    assert "titan" not in upgrade_sql.lower()
    assert TITAN_EMBEDDING_MODEL in "\n".join(module.DOWNGRADE_DDL)


def test_the_embedding_version_canon_is_v2(module: Any) -> None:
    """Canon: ``embedding_version = 'v2'`` for the Gemini space.

    A 1536-dim Gemini vector and a 1024-dim Titan vector must never be ranked in
    one query, and ``embedding_version`` is the column the retrieval SQL filters
    on to guarantee that (DDL section 5.5).
    """
    assert module.EMBEDDING_VERSION == GEMINI_EMBEDDING_VERSION
    assert module.SUPERSEDED_EMBEDDING_VERSION == "v1"


# ---- fact 3: the proposal-model CHECK ------------------------------------


def test_the_proposal_check_carries_the_three_gemini_tiers_and_the_kernel(module: Any) -> None:
    """Tier R, Tier R fallback, Tier E, and the Kernel's own derivations."""
    members = _check_members(module.PROPOSAL_MODEL_CHECK_DDL)
    assert "ck_memory_proposals_model" in module.PROPOSAL_MODEL_CHECK_DDL
    assert GEMINI_TIER_R in members
    assert GEMINI_TIER_R_FALLBACK in members
    assert GEMINI_TIER_E in members
    assert "deterministic.kernel" in members, "the Kernel writes its own proposals"


def test_the_proposal_check_admits_no_bedrock_id(module: Any) -> None:
    """A model the submission no longer runs must not be writable.

    ``agent_runs.model_route`` records the id that actually served a run, and
    ``CANONICAL_DECISIONS.md`` → *Disclosure* makes that checkable against
    persisted state. A CHECK still admitting Opus 4.6 would let the database
    accept a row the disclosure says cannot exist.
    """
    members = _check_members(module.PROPOSAL_MODEL_CHECK_DDL)
    assert members.isdisjoint(SUPERSEDED_PROPOSAL_MODELS), (
        f"superseded Bedrock ids remain in the CHECK: "
        f"{sorted(members & SUPERSEDED_PROPOSAL_MODELS)}"
    )
    assert not [m for m in members if m.startswith(("anthropic.", "us.anthropic.", "amazon."))]


# ---- the destruction guard -----------------------------------------------


def test_the_guard_refuses_an_unacknowledged_run(module: Any) -> None:
    """18,035 vectors do not go quietly."""
    with pytest.raises(RuntimeError) as excinfo:
        module.require_acknowledgement(
            embeddings_destroyed=18035, acknowledged=None, database="provenance"
        )
    message = str(excinfo.value)
    assert "18035" in message, "the operator is not told how much is destroyed"
    assert module.ACK_ENV_VAR in message, "the message does not say how to proceed"
    assert "provenance" in message, "the message does not name the database at risk"


def test_the_guard_refuses_a_stale_acknowledgement(module: Any) -> None:
    """An ack carried over from a smaller corpus is not an ack for this one."""
    with pytest.raises(RuntimeError):
        module.require_acknowledgement(
            embeddings_destroyed=18035, acknowledged="32", database="provenance"
        )


def test_the_guard_refuses_a_non_numeric_acknowledgement(module: Any) -> None:
    with pytest.raises(RuntimeError):
        module.require_acknowledgement(
            embeddings_destroyed=0, acknowledged="yes", database="provenance_ci"
        )


def test_the_guard_accepts_an_exact_acknowledgement(module: Any) -> None:
    """Exact, so it cannot be set without having read the number first."""
    module.require_acknowledgement(
        embeddings_destroyed=18035, acknowledged="18035", database="provenance"
    )
    module.require_acknowledgement(
        embeddings_destroyed=0, acknowledged="0", database="provenance_ci"
    )


def test_stranded_proposals_are_refused_before_any_schema_change(module: Any) -> None:
    """A pre-flight count, so CockroachDB never has to raise the CheckViolation.

    Observed while probing this cluster: a failing ``ADD CONSTRAINT`` reports the
    offending row **in full** — every column, including the entire 1024-float
    vector. That transcript then goes into CI logs and gate evidence. Counting
    first turns a row dump into a number.
    """
    with pytest.raises(RuntimeError) as excinfo:
        module.require_no_stranded_proposals(stranded=7, database="provenance")
    message = str(excinfo.value)
    assert "7" in message
    assert "memory_proposals" in message


def test_no_stranded_proposals_is_the_normal_case(module: Any) -> None:
    module.require_no_stranded_proposals(stranded=0, database="provenance_ci")


def test_an_empty_database_still_needs_an_acknowledgement(module: Any) -> None:
    """``alembic upgrade head`` must not carry 0009 in by accident.

    The db lane's ``migrated`` fixture runs ``upgrade head``, and with 0009 on
    the chain ``head`` is 0009. Requiring the ack even at zero embeddings is
    what stops a routine test run from silently moving ``provenance_ci`` to a
    schema no code on this branch is ready for.
    """
    with pytest.raises(RuntimeError):
        module.require_acknowledgement(
            embeddings_destroyed=0, acknowledged=None, database="provenance_ci"
        )


# ==========================================================================
# Live half — the revision's own SQL, replayed on a _pv_ scratch table
# ==========================================================================
#
# Every test below is marked ``slow`` and means it: a schema change on this
# CockroachDB Cloud cluster costs two to three seconds, and one replay of
# ``UPGRADE_DDL`` is twenty statements. The upgraded table is therefore built
# **once** per module and shared, rather than rebuilt per test — 90 seconds
# against 450. The tests that share it assert on a row they seeded by id, not
# on a global count, so they do not depend on each other's order; the one test
# that does consume the shared table checks its precondition out loud first.

SCRATCH_EVIDENCE = "_pv_m0009_evidence"
SCRATCH_PROPOSALS = "_pv_m0009_proposals"

#: Seeded before the upgrade and asserted afterwards. A fixed id, so the claim
#: is "this row survived and its vector did not" rather than a row count that a
#: neighbouring test could move.
TITAN_ROW_ID = "00000000-0000-4000-8000-000000000009"

#: The shape 0009 starts from: the embedding quartet of ``evidence_items`` as
#: ``0002_evidence_plane`` leaves it, including both families, both CHECKs that
#: name an embedding column, and the two partial indexes whose predicates read
#: ``embedding``. Written out rather than derived, so a drift between this and
#: 0002 is visible here instead of being inherited silently.
SCRATCH_EVIDENCE_DDL = f"""
CREATE TABLE {SCRATCH_EVIDENCE} (
    id                     UUID          NOT NULL PRIMARY KEY,
    user_id                UUID          NOT NULL,
    normalized_text_sha256 BYTES         NOT NULL,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    embedding              VECTOR(1024)  NULL,
    embedding_model        STRING        NULL,
    embedding_version      STRING        NULL,
    embedding_generated_at TIMESTAMPTZ   NULL,
    CONSTRAINT ck_evidence_embedding_provenance CHECK (
        embedding IS NULL
        OR (embedding_model IS NOT NULL
            AND embedding_version IS NOT NULL
            AND embedding_generated_at IS NOT NULL)
    ),
    CONSTRAINT ck_evidence_embedding_model CHECK (
        embedding_model IS NULL OR embedding_model = 'amazon.titan-embed-text-v2:0'
    ),
    FAMILY f_meta (id, user_id, normalized_text_sha256, created_at,
                   embedding_model, embedding_version, embedding_generated_at),
    FAMILY f_vec  (embedding)
)
"""

SCRATCH_EVIDENCE_INDEX_DDL: tuple[str, ...] = (
    "CREATE VECTOR INDEX evidence_embedding_ann_idx "
    f"ON {SCRATCH_EVIDENCE} (user_id, embedding vector_cosine_ops)",
    f"CREATE INDEX idx_evidence_embedding_backlog ON {SCRATCH_EVIDENCE} (created_at) "
    "WHERE embedding IS NULL",
    "CREATE INDEX idx_evidence_text_hash "
    f"ON {SCRATCH_EVIDENCE} (normalized_text_sha256, embedding_version) "
    "WHERE embedding IS NOT NULL",
)

SCRATCH_PROPOSALS_DDL = f"""
CREATE TABLE {SCRATCH_PROPOSALS} (
    id       UUID   NOT NULL PRIMARY KEY,
    model_id STRING NOT NULL,
    CONSTRAINT ck_memory_proposals_model CHECK (model_id IN (
        'us.anthropic.claude-haiku-4-5-20251001-v1:0',
        'us.anthropic.claude-opus-4-6-v1',
        'us.anthropic.claude-sonnet-4-6',
        'deterministic.kernel'
    ))
)
"""

_REWRITE_TABLES = {
    "evidence_items": SCRATCH_EVIDENCE,
    "memory_proposals": SCRATCH_PROPOSALS,
}
#: Index names are table-scoped in CockroachDB, so a scratch index could legally
#: carry a canon name — but it would then be indistinguishable from the real one
#: in ``SHOW JOBS`` while another agent is working the same cluster, which is
#: exactly the confusion that destroyed a 55-minute index build here once.
#: Prefixed, and asserted below to have been prefixed.
_REWRITE_OBJECTS = re.compile(r"\b(evidence_embedding_ann_idx|idx_evidence_\w+)\b")


def _rewrite(statement: str) -> str:
    """Point one of the revision's statements at the scratch tables.

    Loud on purpose: if a table or object name in 0009 stops matching, the
    assertion below fails rather than the test quietly exercising nothing.
    """
    out = statement
    for canon, scratch in _REWRITE_TABLES.items():
        out = re.sub(rf"\b{canon}\b", scratch, out)
    out = _REWRITE_OBJECTS.sub(r"_pv_m0009_\1", out)
    for canon in _REWRITE_TABLES:
        assert not re.search(rf"\b{canon}\b", out), f"{canon} survived the rewrite: {out}"
    return out


def _evidence_statements(statements: tuple[str, ...]) -> list[str]:
    """The half of a DDL tuple that targets ``evidence_items``."""
    selected = [s for s in statements if "memory_proposals" not in s]
    assert selected, "no evidence_items statements found"
    return selected


def _proposal_statements(statements: tuple[str, ...]) -> list[str]:
    selected = [s for s in statements if "memory_proposals" in s]
    assert len(selected) == 2, f"expected a DROP and an ADD, found {len(selected)}"
    return selected


def _vector(dimensions: int) -> str:
    return "[" + ",".join("0.001" for _ in range(dimensions)) + "]"


@pytest.fixture(scope="module")
def upgraded(test_dsn: Any, module: Any) -> Iterator[psycopg.Connection]:
    """A scratch table at the pre-0009 shape, seeded, then upgraded by 0009's own SQL.

    Deliberately does **not** use ``migrated`` / ``db_connection`` from
    ``conftest.py``: those run ``alembic upgrade head``, and with 0009 on the
    chain ``head`` is 0009. Nothing here touches ``evidence_items``.
    """
    conn = psycopg.connect(str(test_dsn), autocommit=True)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {SCRATCH_EVIDENCE} CASCADE")
        conn.execute(SCRATCH_EVIDENCE_DDL)
        for statement in SCRATCH_EVIDENCE_INDEX_DDL:
            conn.execute(_rewrite(statement))
        conn.execute(
            f"INSERT INTO {SCRATCH_EVIDENCE} "
            "(id, user_id, normalized_text_sha256, embedding, embedding_model, "
            " embedding_version, embedding_generated_at) "
            "VALUES (%s, %s, %s, %s::VECTOR(1024), %s, 'v1', now())",
            (
                TITAN_ROW_ID,
                str(uuid.uuid4()),
                b"\x00" * 32,
                _vector(TITAN_DIMENSIONS),
                TITAN_EMBEDDING_MODEL,
            ),
        )
        for statement in _evidence_statements(module.UPGRADE_DDL):
            conn.execute(_rewrite(statement))
        yield conn
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {SCRATCH_EVIDENCE} CASCADE")
        conn.close()


@pytest.mark.slow
def test_upgrade_leaves_the_row_and_nulls_its_vector(upgraded: psycopg.Connection) -> None:
    """The honest outcome, observed: the row survives, the vector does not.

    This is the assertion that stops 0009 from pretending the corpus came
    through. Evidence is append-only (``0002``'s docstring), so the row, its
    text hash and its grounding edges must all still be there — but a
    1024-dimension Titan vector cannot be a 1536-dimension Gemini one, and
    nothing in this revision claims otherwise.
    """
    row = upgraded.execute(
        "SELECT embedding IS NULL, embedding_model IS NULL, embedding_version IS NULL, "
        f"embedding_generated_at IS NULL FROM {SCRATCH_EVIDENCE} WHERE id = %s",
        (TITAN_ROW_ID,),
    ).fetchone()
    assert row is not None, "the evidence row itself must survive; evidence is append-only"
    assert row[0], "0009 must not pretend a 1024-dim vector became a 1536-dim one"
    assert row[1], "a vector's provenance without the vector is a lie"
    assert row[2] and row[3]

    create_sql = str(upgraded.execute(f"SHOW CREATE TABLE {SCRATCH_EVIDENCE}").fetchone()[1])
    assert re.search(r"(?i)embedding\s+VECTOR\s*\(\s*1536\s*\)", create_sql), create_sql
    assert "FAMILY f_vec (embedding)" in create_sql, (
        "the vector must stay in its own column family, or every metadata read "
        f"drags 6KB of float with it:\n{create_sql}"
    )


@pytest.mark.slow
def test_after_upgrade_gemini_writes_and_titan_is_refused(
    upgraded: psycopg.Connection,
) -> None:
    """The boundary, observed from the far side rather than read off a catalogue."""
    insert = (
        f"INSERT INTO {SCRATCH_EVIDENCE} "
        "(id, user_id, normalized_text_sha256, embedding, embedding_model, "
        " embedding_version, embedding_generated_at) "
        "VALUES (%s, %s, %s, %s::VECTOR({dims}), %s, %s, now())"
    )

    for spelling in sorted(GEMINI_EMBEDDING_SPELLINGS):
        upgraded.execute(
            insert.format(dims=GEMINI_DIMENSIONS),
            (
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                b"\x01" * 32,
                _vector(GEMINI_DIMENSIONS),
                spelling,
                GEMINI_EMBEDDING_VERSION,
            ),
        )

    with pytest.raises(psycopg.errors.CheckViolation):
        upgraded.execute(
            insert.format(dims=GEMINI_DIMENSIONS),
            (
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                b"\x02" * 32,
                _vector(GEMINI_DIMENSIONS),
                TITAN_EMBEDDING_MODEL,
                "v1",
            ),
        )

    with pytest.raises(psycopg.errors.DataException):
        upgraded.execute(
            insert.format(dims=TITAN_DIMENSIONS),
            (
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                b"\x03" * 32,
                _vector(TITAN_DIMENSIONS),
                "gemini-embedding-2",
                GEMINI_EMBEDDING_VERSION,
            ),
        )


@pytest.mark.slow
def test_after_upgrade_the_ann_index_keeps_its_user_id_prefix(
    upgraded: psycopg.Connection,
) -> None:
    """Invariant I7 rests on the prefix column, not on a ``WHERE`` clause.

    Filter acceleration in CockroachDB works only through prefix columns, so a
    rebuilt index that lost ``user_id`` would pass ``SHOW INDEXES`` for existence
    and fail ``EXPLAIN`` at ``G6.2``. The position is asserted, not the presence.
    """
    cursor = upgraded.execute(f"SHOW INDEXES FROM {SCRATCH_EVIDENCE}")
    columns = [str(d.name) for d in cursor.description or []]
    rows = cursor.fetchall()
    name_at = columns.index("index_name")
    seq_at = columns.index("seq_in_index")
    column_at = columns.index("column_name")

    entries = sorted(
        (int(r[seq_at]), str(r[column_at]))
        for r in rows
        if str(r[name_at]) == "_pv_m0009_evidence_embedding_ann_idx"
    )
    assert entries, "the ANN index was not rebuilt"
    assert entries[0][1] == "user_id", f"ANN prefix column is {entries[0][1]!r}; columns={entries}"
    assert any(column == "embedding" for _, column in entries)

    live = {str(r[name_at]) for r in rows}
    assert "_pv_m0009_idx_evidence_embedding_backlog" in live, "the backlog index was not rebuilt"
    assert "_pv_m0009_idx_evidence_text_hash" in live, "the text-hash index was not rebuilt"


@pytest.mark.slow
def test_the_proposal_check_swap_executes_on_this_cluster(test_dsn: Any, module: Any) -> None:
    """The third pinned fact, replayed: Gemini in, Bedrock out.

    Its own table because it is cheap — two statements — and because coupling it
    to the evidence fixture would make a ``memory_proposals`` failure look like
    an ``evidence_items`` one.
    """
    conn = psycopg.connect(str(test_dsn), autocommit=True)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {SCRATCH_PROPOSALS} CASCADE")
        conn.execute(SCRATCH_PROPOSALS_DDL)
        for statement in _proposal_statements(module.UPGRADE_DDL):
            conn.execute(_rewrite(statement))

        for model_id in (
            GEMINI_TIER_R,
            GEMINI_TIER_R_FALLBACK,
            GEMINI_TIER_E,
            "deterministic.kernel",
        ):
            conn.execute(
                f"INSERT INTO {SCRATCH_PROPOSALS} (id, model_id) VALUES (%s, %s)",
                (str(uuid.uuid4()), model_id),
            )

        for superseded in sorted(SUPERSEDED_PROPOSAL_MODELS):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    f"INSERT INTO {SCRATCH_PROPOSALS} (id, model_id) VALUES (%s, %s)",
                    (str(uuid.uuid4()), superseded),
                )
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {SCRATCH_PROPOSALS} CASCADE")
        conn.close()


@pytest.mark.slow
def test_downgrade_restores_the_titan_shape(upgraded: psycopg.Connection, module: Any) -> None:
    """A working ``downgrade()``, and it is symmetrically destructive.

    Rolling back does not resurrect the Titan vectors — they were gone the moment
    the column was dropped. What it restores is the *shape*, so a branch still on
    0008 can run.

    **This test consumes the shared table**, which is why it is last and why it
    states its precondition out loud: if a future edit reorders the module, the
    assertion below fails instead of the downgrade quietly running against an
    already-downgraded table and proving nothing.
    """
    before = str(upgraded.execute(f"SHOW CREATE TABLE {SCRATCH_EVIDENCE}").fetchone()[1])
    assert re.search(
        r"(?i)embedding\s+VECTOR\s*\(\s*1536\s*\)", before
    ), f"precondition failed: the shared table is not at the upgraded width:\n{before}"

    for statement in _evidence_statements(module.DOWNGRADE_DDL):
        upgraded.execute(_rewrite(statement))

    after = str(upgraded.execute(f"SHOW CREATE TABLE {SCRATCH_EVIDENCE}").fetchone()[1])
    assert re.search(r"(?i)embedding\s+VECTOR\s*\(\s*1024\s*\)", after), after
    assert TITAN_EMBEDDING_MODEL in after, "the 0008 embedding-model CHECK was not restored"
    assert "FAMILY f_vec (embedding)" in after, after
