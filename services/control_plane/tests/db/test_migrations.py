"""Migration-chain tests for ``T2.1``-``T2.6`` (revisions 0001-0008).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` sections 2, 3 (0001), 4, 5 (0002), 6 (0003),
  7, 8 (0004, 0005), 9, 10 (0006, 0007), 11, 14, 15 (0008) and 16 (Alembic
  ordering and the CockroachDB-specific rules).
- ``docs/ops/41_RUNBOOK.md`` section 4.1 - the reversibility drill.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 5, ``T2.1``-``T2.6`` acceptance.
- ``docs/quality/23_PHASE_GATES.md`` section 6 - ``G2.1``-``G2.3``, ``G2.7``.

Two halves, deliberately
------------------------
The **static** half reads the revision files as text. It is what catches the
class of defect that a migrated database cannot: a revision that mixes DDL and
DML runs perfectly against an empty database and fails only when CockroachDB
sees a schema change after a write in the same transaction. The **live** half
introspects the migrated ``provenance_ci`` database, because a constraint that
exists only in a Python string is not a constraint.

Everything here carries the ``db`` marker from ``conftest.py``; the static half
does not open a connection but belongs to this lane, not to the hermetic one.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from provenance_contracts.settings import (
    DEFAULT_EXTRACTION_MODEL_ID,
    DEFAULT_REASONING_MODEL_ID,
)
from provenance_domain.enums import (
    ActionState,
    ActionType,
    AgentRunStatus,
    AgentSafeView,
    AggregateType,
    AttentionLevel,
    CaseStatus,
    CommitmentStatus,
    CommitmentType,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    EpistemicStatus,
    EventType,
    EvidenceType,
    ExecutionStatus,
    FulfillmentAdmissionStatus,
    KernelDecision,
    MemoryMode,
    OutboxStatus,
    ProposalStatus,
    ProposalType,
    RetractionStatus,
    SupportRelation,
    SupportSourceKind,
    TransitionType,
    TriggerReasonCode,
    TriggerResult,
    TriggerState,
    TriggerType,
)
from services.control_plane.tests.db.conftest import DEPLOYED_HEAD

pytestmark = pytest.mark.db

# --------------------------------------------------------------------------
# The tables each revision owns (DDL sections 3, 4, 6; section 16's table)
# --------------------------------------------------------------------------

TABLES_0001: tuple[str, ...] = (
    "tenants",
    "users",
    "ingest_aliases",
    "counterparties",
    "relationships",
    "contexts",
    "cases",
)
TABLES_0002: tuple[str, ...] = ("source_artifacts", "evidence_items")
TABLES_0003: tuple[str, ...] = ("claims", "beliefs", "belief_versions", "belief_support")
TABLES_0004: tuple[str, ...] = (
    "conflicts",
    "commitments",
    "fulfillments",
    "state_transitions",
)
TABLES_0005: tuple[str, ...] = ("memory_proposals", "kernel_decisions")
TABLES_0006: tuple[str, ...] = ("prospective_triggers",)
TABLES_0007: tuple[str, ...] = ("action_intents", "action_executions")
TABLES_0008: tuple[str, ...] = (
    "outbox_events",
    "processed_events",
    "agent_runs",
    "idempotency_records",
)

ALL_TABLES: tuple[str, ...] = (
    TABLES_0001
    + TABLES_0002
    + TABLES_0003
    + TABLES_0004
    + TABLES_0005
    + TABLES_0006
    + TABLES_0007
    + TABLES_0008
)

#: ``CANONICAL_DECISIONS.md``: "26 tables. Operational tables are included in
#: this total." ``G2.2`` counts them, ``db/expected_tables.txt`` names them, and
#: DDL section 20 risk 2 exists because 24 was once written down instead.
CANONICAL_TABLE_COUNT = 26

#: The five agent-safe views of DDL section 14, canon names, from the domain
#: enum rather than retyped here: ``G2.3`` asserts that the database names and
#: the names the API renders are the same strings.
AGENT_VIEWS: tuple[str, ...] = tuple(sorted(member.value for member in AgentSafeView))

#: ``db/expected_tables.txt`` - the hand-written manifest ``G2.2`` diffs against.
#: Written by hand from the enumeration above; generating it from the database
#: would make the gate a diff of the database against itself.
EXPECTED_TABLES_MANIFEST = Path(__file__).resolve().parents[4] / "db" / "expected_tables.txt"

#: Two tables key on a natural composite and carry no surrogate ``id``:
#: ``processed_events`` is ``(consumer_name, event_id)`` because the event id is
#: supplied by the producer, and ``idempotency_records`` is ``(scope, key)``
#: because the key is supplied by the client. Inventing an ``id`` for either
#: would add a second identity to a row whose whole purpose is one.
TABLES_WITHOUT_SURROGATE_ID: frozenset[str] = frozenset({"processed_events", "idempotency_records"})

#: Tables that carry both ``tenant_id`` and ``user_id`` (the tenancy spine).
#: ``counterparties`` is tenant-scoped only, by DDL deviation 1; ``tenants`` and
#: ``users`` are the roots of the spine and are excluded by construction.
#: ``processed_events`` and ``idempotency_records`` carry *nullable* copies of
#: the pair, taken from an event envelope for observability filtering; DDL
#: section 11.2 is explicit that they "exist for observability filtering, never
#: for authorization", so they are not part of the spine.
USER_OWNED_TABLES: tuple[str, ...] = (
    "ingest_aliases",
    "relationships",
    "contexts",
    "cases",
    "source_artifacts",
    "evidence_items",
    "claims",
    "beliefs",
    "belief_versions",
    "belief_support",
    "conflicts",
    "commitments",
    "fulfillments",
    "state_transitions",
    "memory_proposals",
    "kernel_decisions",
    "prospective_triggers",
    "action_intents",
    "action_executions",
    "outbox_events",
    "agent_runs",
)

#: Tables declaring ``UNIQUE (tenant_id, user_id, id)`` so children may point at
#: them composite-ly. ``ingest_aliases`` and ``belief_support`` are leaves and
#: the DDL deliberately gives them no such key.
COMPOSITE_UNIQUE_TABLES: dict[str, str] = {
    "relationships": "uq_relationships_tenant_user_id",
    "contexts": "uq_contexts_tenant_user_id",
    "cases": "uq_cases_tenant_user_id",
    "source_artifacts": "uq_source_artifacts_tenant_user_id",
    "evidence_items": "uq_evidence_tenant_user_id",
    "claims": "uq_claims_tenant_user_id",
    "beliefs": "uq_beliefs_tenant_user_id",
    "belief_versions": "uq_belief_versions_tenant_user_id",
    "conflicts": "uq_conflicts_tenant_user_id",
    "commitments": "uq_commitments_tenant_user_id",
    "memory_proposals": "uq_memory_proposals_tenant_user_id",
    "kernel_decisions": "uq_kernel_decisions_tenant_user_id",
    "prospective_triggers": "uq_prospective_triggers_tenant_user_id",
    "action_intents": "uq_action_intents_tenant_user_id",
    "agent_runs": "uq_agent_runs_tenant_user_id",
}

#: ``action_intents.approved_by_user_id`` names a *second* user - the approver -
#: and its FK is ``(tenant_id, approved_by_user_id) -> users (tenant_id, id)``.
#: Tenancy is still part of the key, so an approval cannot be stitched across
#: tenants; what it deliberately does not do is force the approver to be the
#: owning user. Named here so the composite-FK sweep stays a sweep rather than
#: acquiring a silent "unless the column is called something else" clause.
COMPOSITE_FK_EXEMPTIONS: frozenset[str] = frozenset({"fk_action_intents_approver"})

#: Indexes DDL sections 3, 4 and 6 name, each with the query it serves.
EXPECTED_INDEXES: dict[str, tuple[str, ...]] = {
    "users": ("idx_users_cognito_lookup",),
    "ingest_aliases": ("idx_ingest_aliases_active_lookup", "idx_ingest_aliases_by_user"),
    "counterparties": ("idx_counterparties_domain",),
    "relationships": (
        "idx_relationships_user_counterparty_status",
        "idx_relationships_external_ref",
    ),
    "contexts": ("idx_contexts_user_status",),
    "cases": (
        "idx_cases_user_status_activity",
        "idx_cases_relationship_status",
        "idx_cases_context_status",
        "idx_cases_attention",
    ),
    "source_artifacts": (
        "uq_source_artifacts_message_id",
        "idx_source_artifacts_sender_domain",
        "idx_source_artifacts_thread",
        "idx_source_artifacts_parse_queue",
    ),
    "evidence_items": (
        "evidence_embedding_ann_idx",
        "idx_evidence_artifact",
        "idx_evidence_type_observed",
        "idx_evidence_embedding_backlog",
        "idx_evidence_text_hash",
        "idx_evidence_retracted",
        "idx_evidence_valid_time",
    ),
    "claims": ("idx_claims_proposition", "idx_claims_case_recorded", "idx_claims_evidence"),
    "beliefs": ("idx_beliefs_case", "idx_beliefs_current_version"),
    "belief_versions": (
        "idx_belief_versions_chain",
        "idx_belief_versions_decision",
        "idx_belief_versions_valid_time",
    ),
    "belief_support": ("idx_belief_support_version", "idx_belief_support_source"),
    "conflicts": (
        "uq_conflicts_live_identity",
        "idx_conflicts_case_status",
        "idx_conflicts_needs_human",
    ),
    "commitments": (
        "idx_commitments_case_status",
        "idx_commitments_overdue",
        "idx_commitments_source_claim",
    ),
    "fulfillments": ("idx_fulfillments_commitment_admitted", "idx_fulfillments_evidence"),
    "state_transitions": (
        "idx_state_transitions_case_revision",
        "idx_state_transitions_trace",
        "idx_state_transitions_decision",
    ),
    "memory_proposals": (
        "idx_memory_proposals_trace",
        "idx_memory_proposals_pending",
        "idx_memory_proposals_run",
    ),
    "kernel_decisions": (
        "idx_kernel_decisions_trace",
        "idx_kernel_decisions_case",
        "uq_kernel_decisions_terminal_per_proposal",
    ),
    "prospective_triggers": (
        "idx_prospective_triggers_due",
        "idx_prospective_triggers_case_state",
        "idx_prospective_triggers_expiry",
    ),
    "action_intents": (
        "idx_action_intents_user_status",
        "idx_action_intents_case_status",
        "idx_action_intents_approved_queue",
    ),
    "action_executions": (
        "uq_action_executions_single_success",
        "idx_action_executions_correlation",
        "idx_action_executions_user_time",
    ),
    "outbox_events": (
        "idx_outbox_events_dispatch_queue",
        "idx_outbox_events_dead",
        "idx_outbox_events_trace",
        "idx_outbox_events_pending_age",
    ),
    "processed_events": ("idx_processed_events_event", "idx_processed_events_recent"),
    "agent_runs": (
        "idx_agent_runs_active",
        "idx_agent_runs_trace",
        "idx_agent_runs_counterfactual",
        "idx_agent_runs_user_active",
    ),
    "idempotency_records": ("idx_idempotency_expiry", "idx_idempotency_trace"),
}

#: A revision that writes rows cannot also change the schema in the same
#: CockroachDB transaction. Docstrings and SQL comments are removed before the
#: scan (see :func:`_executed_sql`) so the *prohibition* may be written down
#: next to the code it governs, while the SQL that will actually run is read.
DML_KEYWORDS: tuple[str, ...] = ("INSERT", "UPDATE", "DELETE", "UPSERT", "TRUNCATE")

_FK_PATTERN = re.compile(
    r"CONSTRAINT\s+(?P<name>\w+)\s+FOREIGN\s+KEY\s*\((?P<cols>[^)]*)\)\s*"
    r"REFERENCES\s+(?P<ref>[\w.\"]+)\s*\((?P<refcols>[^)]*)\)",
    re.IGNORECASE,
)
#: A closed-vocabulary member inside a CHECK. The colon is in the class for
#: ``ck_memory_proposals_model``: a Bedrock inference-profile id ends in a
#: version suffix (``...-v1:0``), and without it that member is invisible to
#: the vocabulary comparison - which would pass the test while the CHECK
#: rejected every proposal the shipped configuration produces.
_STRING_LITERAL = re.compile(r"'([A-Za-z0-9_.:/+-]+)'")

#: A real DSN, as opposed to prose naming the scheme.
#:
#: ``G0.3`` bans a committed credential, not the word ``postgresql://`` - and
#: ``db/migrations/env.py`` has to explain *why* it rewrites a plain
#: ``postgresql://`` URL to ``postgresql+psycopg``. A bare substring check makes
#: that explanation unwritable, which is how a scanner trains people to delete
#: the comment instead of the credential. A DSN carries an authority section, so
#: this requires the scheme to be followed by host or userinfo characters.
_DSN_LITERAL = re.compile(r"postgres(?:ql)?(?:\+\w+)?://[A-Za-z0-9_.%\-]+[:@/]")

#: Scratch tables. ``docs/specs/10_DATABASE_DDL.md`` section 1 uses the ``_pv_``
#: prefix for its probe tables, and ``provenance_ci`` is shared with whatever
#: other Phase 2 work is running. Tolerated by name so a neighbour's scratch
#: space cannot fail an assertion about *this* migration chain - and tolerated
#: only under that prefix, so a real table appearing unannounced still fails.
_SCRATCH_PREFIX = "_pv_"


def _managed_tables(connection: psycopg.Connection) -> frozenset[str]:
    """Live tables, minus Alembic's bookkeeping and minus probe scratch."""
    return frozenset(
        name
        for name in _live_tables(connection)
        if name != "alembic_version" and not name.startswith(_SCRATCH_PREFIX)
    )


# --------------------------------------------------------------------------
# Small parsers. Deliberately not clever: a wrong parse must fail loudly.
# --------------------------------------------------------------------------


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The ``id()`` of every docstring Constant node in *tree*.

    Docstrings are excluded **by identity**, never by delimiter. Stripping
    triple-quoted blocks wholesale would strip the SQL itself, since the
    revisions write their DDL in triple-quoted strings.

    Identity also beats "skip the first statement of the function": that form
    misses nested definitions, and the obvious variant -- dropping every
    ``ast.Expr`` from the body -- silently drops every bare
    ``op.execute("...")`` too, because a call used as a statement *is* an
    ``ast.Expr``. On ``0002`` that discards
    ``op.execute("DROP INDEX IF EXISTS ...")`` while the test still passes on a
    different DROP, so the loss is invisible. That is the same shape as the bug
    this helper exists to prevent.
    """
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }


def _executed_sql(source: str) -> str:
    """Every string literal in *source* that is not a docstring, SQL-uncommented.

    A revision executes what is in its string literals. Stripping triple-quoted
    blocks wholesale - the obvious shortcut - would strip the SQL itself and let
    an ``op.execute("INSERT ...")`` sail through the DDL/DML check below, which
    is the one thing that check exists to stop. So the docstrings are removed by
    identity (``ast.get_docstring``'s nodes) rather than by delimiter, and
    ``--`` / ``/* */`` comments are then removed from what remains: a *comment*
    naming a forbidden keyword is documentation, and must stay legal.
    """
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
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


def _constraint_body(create_sql: str, constraint: str) -> str:
    """The parenthesised body of ``CONSTRAINT <constraint> CHECK (...)``."""
    anchor = re.search(rf"CONSTRAINT\s+{re.escape(constraint)}\s+CHECK\s*\(", create_sql)
    assert anchor is not None, f"{constraint} not found in:\n{create_sql}"
    depth = 0
    start = anchor.end() - 1
    for offset, char in enumerate(create_sql[start:]):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return create_sql[start : start + offset + 1]
    raise AssertionError(f"unbalanced parentheses after {constraint}")


def _check_members(create_sql: str, constraint: str) -> frozenset[str]:
    """The string literals inside a named CHECK - i.e. its closed vocabulary."""
    return frozenset(_STRING_LITERAL.findall(_constraint_body(create_sql, constraint)))


def _foreign_keys(create_sql: str) -> dict[str, tuple[tuple[str, ...], str]]:
    """``{constraint_name: ((local columns...), referenced_table)}``."""
    found: dict[str, tuple[tuple[str, ...], str]] = {}
    for match in _FK_PATTERN.finditer(create_sql):
        columns = tuple(part.strip().strip('"') for part in match.group("cols").split(","))
        referenced = match.group("ref").split(".")[-1].strip('"')
        found[match.group("name")] = (columns, referenced)
    return found


def _live_tables(connection: psycopg.Connection) -> frozenset[str]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        return frozenset(str(row[0]) for row in cur.fetchall())


def _columns(connection: psycopg.Connection, table: str) -> dict[str, dict[str, object]]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "       numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
        rows = cur.fetchall()
    return {
        str(row[0]): {
            "data_type": str(row[1]),
            "is_nullable": str(row[2]),
            "column_default": row[3],
            "numeric_precision": row[4],
            "numeric_scale": row[5],
        }
        for row in rows
    }


def _index_names(connection: psycopg.Connection, table: str) -> frozenset[str]:
    with connection.cursor() as cur:
        cur.execute(f"SHOW INDEXES FROM {table}")
        columns = [str(description.name) for description in cur.description or []]
        rows = cur.fetchall()
    position = columns.index("index_name")
    return frozenset(str(row[position]) for row in rows)


# ==========================================================================
# Static half - the scaffold and the revision files as text
# ==========================================================================


def test_alembic_ini_exists(repo_paths) -> None:
    """``T2.1`` creates ``alembic.ini`` at the repository root."""
    assert repo_paths.alembic_ini.is_file(), f"missing {repo_paths.alembic_ini}"


def test_alembic_ini_carries_no_database_url(repo_paths) -> None:
    """The URL never appears in ``alembic.ini`` (``T2.1`` sub-task; ``G0.3``).

    ``sqlalchemy.url`` must be present and empty: present so the key is not
    silently misspelled somewhere else, empty so a committed file can never
    carry a credential.
    """
    text = repo_paths.alembic_ini.read_text(encoding="utf-8")
    url_lines = [line for line in text.splitlines() if line.strip().startswith("sqlalchemy.url")]
    assert url_lines, "alembic.ini declares no sqlalchemy.url key"
    for line in url_lines:
        value = line.split("=", 1)[1].split(";", 1)[0].strip()
        assert value == "", f"alembic.ini carries a URL value: {line.split('=', 1)[0]}"
    assert _DSN_LITERAL.search(text) is None, "alembic.ini contains a DSN literal"


def test_alembic_ini_sets_transaction_per_migration(repo_paths) -> None:
    """DDL section 16: ``transaction_per_migration = true``."""
    text = repo_paths.alembic_ini.read_text(encoding="utf-8")
    assert re.search(
        r"(?m)^\s*transaction_per_migration\s*=\s*true\s*$", text
    ), "alembic.ini must set transaction_per_migration = true"


def test_env_py_reads_url_from_the_environment(repo_paths) -> None:
    """``env.py`` resolves ``COCKROACH_DATABASE_URL`` at runtime, never from the ini."""
    assert repo_paths.env_py.is_file(), f"missing {repo_paths.env_py}"
    source = repo_paths.env_py.read_text(encoding="utf-8")
    assert "COCKROACH_DATABASE_URL" in source
    assert "os.environ" in source or "environ.get" in source


def test_env_py_disables_autogenerate(repo_paths) -> None:
    """DDL section 16's ``env.py`` block, asserted setting by setting.

    ``target_metadata = None`` is the one that matters: these migrations are
    hand-written because SQLAlchemy's CockroachDB dialect emits neither
    ``VECTOR`` nor ``FAMILY`` nor ``STORING``, so an autogenerate run would
    quietly propose dropping half the schema.
    """
    source = repo_paths.env_py.read_text(encoding="utf-8")
    assert re.search(
        r"target_metadata\s*=\s*None", source
    ), "env.py must pin target_metadata to None; autogenerate is forbidden"
    for setting in ("transaction_per_migration=", "transactional_ddl=True", "compare_type=False"):
        assert setting in source, f"env.py does not configure {setting}"


def test_env_py_contains_no_embedded_credential(repo_paths) -> None:
    """No DSN literal anywhere in the scaffold. ``G0.3`` is green and stays green."""
    source = repo_paths.env_py.read_text(encoding="utf-8")
    match = _DSN_LITERAL.search(source)
    assert (
        match is None
    ), f"env.py contains a DSN literal at offset {match.start() if match else -1}"


def test_eight_revision_files_exist(repo_paths) -> None:
    """The revision files, under these names.

    The filename stem is the revision id, and both come from DDL section 16's
    ordering table - ``ops/41_RUNBOOK.md`` section 4.1 quotes the last line of a
    clean upgrade verbatim, so the ids are contract values.

    Nine since 2026-08-24: ``T2.1``-``T2.6`` created eight, and the pivot added
    ``0009_gemini_embedding_plane``. The name is kept because ``G2.x`` addresses
    this test by name.

    The assertion compares the declared tuple against the **directory**, not
    against its own length. ``repo_paths.revision_paths()`` is built *from*
    ``REVISION_FILENAMES``, so ``len(paths) == len(REVISION_FILENAMES)`` is
    tautologically true and would pass with the directory empty -- the vacuity
    shape ``D-00-013`` was filed for. The directory is the only independent
    witness available here.
    """
    on_disk = sorted(
        path.name for path in repo_paths.versions_dir.glob("[0-9][0-9][0-9][0-9]_*.py")
    )
    assert on_disk == sorted(repo_paths.revision_filenames), (
        "the declared revision list and the versions/ directory disagree; " f"on disk: {on_disk}"
    )
    for path in repo_paths.revision_paths():
        assert path.is_file(), f"missing revision file {path}"


def test_revision_chain_is_linear_and_unbranched(repo_paths) -> None:
    """One chain, ``0001 -> ... -> 0008``, no branch labels, no merge points."""
    revisions: dict[str, str | None] = {}
    for path in repo_paths.revision_paths():
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"(?m)^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']", source)
        down = re.search(
            r"(?m)^down_revision(?::\s*[^=]+)?\s*=\s*(?:[\"']([^\"']+)[\"']|None)", source
        )
        assert revision is not None, f"{path.name} declares no revision id"
        assert down is not None, f"{path.name} declares no down_revision"
        revisions[revision.group(1)] = down.group(1)
        assert (
            "branch_labels" not in source or "branch_labels = None" in source
        ), f"{path.name} declares a branch label; the chain must stay linear"

    assert revisions == {
        "0001_identity_aggregates": None,
        "0002_evidence_plane": "0001_identity_aggregates",
        "0003_epistemic_plane": "0002_evidence_plane",
        "0004_obligation_ledger": "0003_epistemic_plane",
        "0005_kernel_control": "0004_obligation_ledger",
        "0006_prospective_memory": "0005_kernel_control",
        "0007_action_plane": "0006_prospective_memory",
        "0008_events_infrastructure": "0007_action_plane",
        # The pivot. Drops the Titan embedding quartet and rebuilds it at
        # VECTOR(1536); refuses to run without PV_EMBEDDING_REWRITE_ACK.
        "0009_gemini_embedding_plane": "0008_events_infrastructure",
    }


def test_no_revision_mixes_ddl_and_dml(repo_paths) -> None:
    """CockroachDB rejects a schema change that follows a write in one transaction.

    ``T2.1`` sub-task, DDL section 16 rule 1, runbook section 4.1. The seed is a
    separate program precisely because of this; a revision that quietly inserts a
    lookup row fails at the *next* schema statement, far from its cause.
    """
    offenders: list[str] = []
    for path in repo_paths.revision_paths():
        code = _executed_sql(path.read_text(encoding="utf-8"))
        for keyword in DML_KEYWORDS:
            if re.search(rf"(?i)\b{keyword}\s+(INTO|FROM|\w+\s+SET|TABLE)\b", code):
                offenders.append(f"{path.name}: {keyword}")
    assert offenders == [], f"revision(s) mix DDL and DML: {offenders}"


def _module_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """Every module-level name bound to an expression, by name."""
    bound: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            bound[node.target.id] = node.value
    return bound


def _strings_reachable_from(
    node: ast.AST,
    assignments: dict[str, ast.expr],
    seen: set[str],
    docstrings: set[int],
) -> list[str]:
    """Every string literal *node* reaches, following module names transitively.

    One hop is not enough: ``0009``'s ``DOWNGRADE_DDL`` splats
    ``*DROP_COLUMN_DDL``, so a single level finds the tuple and none of the DDL
    inside it.

    ``assignments`` is a parameter rather than a closure over the caller's loop
    variable -- the closure form binds late, so a deferred call would resolve
    names against whichever revision the loop had reached last.
    """
    out = [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    for name_node in ast.walk(node):
        if not isinstance(name_node, ast.Name):
            continue
        name = name_node.id
        if name in seen or name not in assignments:
            continue
        seen.add(name)
        out.extend(_strings_reachable_from(assignments[name], assignments, seen, docstrings))
    return out


def test_every_revision_implements_downgrade(repo_paths) -> None:
    """DDL section 16 rule 3: every ``downgrade()`` is implemented, in reverse order.

    The check follows the *names the body references*, not the text after
    ``def downgrade()``. Substring-scanning the sliced body was the original
    shape and it silently stopped working at ``0009``, whose body is::

        for statement in DOWNGRADE_DDL:
            op.execute(statement)

    -- so every ``DROP`` lives in a module constant declared above the
    function, invisible to the slice. A revision could pass by dropping
    nothing, or fail while dropping everything, purely on where its author put
    the SQL. Resolving the referenced constants is what makes the assertion
    about behaviour rather than about layout, and it is the same reason
    ``23_PHASE_GATES.md`` prefers the AST to a substring guard.

    ``0009`` is also the chain's first column-level revision: it creates no
    table, so its downgrade drops columns. Requiring ``DROP TABLE`` of it would
    demand it destroy something it never created.
    """
    for path in repo_paths.revision_paths():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        downgrade = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
            ),
            None,
        )
        assert downgrade is not None, f"{path.name} has no downgrade()"

        statements = [n for n in downgrade.body if not isinstance(n, ast.Expr | ast.Pass)]
        assert statements, f"{path.name} downgrade is a stub"

        sql_parts = _strings_reachable_from(
            downgrade, _module_assignments(tree), set(), _docstring_ids(tree)
        )

        sql = " ".join(sql_parts).upper()
        assert "DROP TABLE" in sql or "DROP COLUMN" in sql, f"{path.name} downgrade drops nothing"


def test_the_downgrade_check_ignores_docstrings(repo_paths) -> None:
    """Positive control. Without it the check above is a guess, not a guard.

    The check reads every string literal ``downgrade()`` reaches. A *docstring*
    is one of those, so a revision whose downgrade drops nothing would still
    pass if its docstring happened to contain the phrase ``DROP COLUMN``. That
    is not hypothetical -- it was live in this file until 2026-08-24, and it was
    found by mutation rather than by reading.

    The mutation below is the exact one that exposed it: replace every
    ``DROP COLUMN`` in ``0009`` with ``RENAME COLUMN`` so the migration reverses
    nothing, and put the phrase into the docstring instead.

    **If ``_docstring_ids`` stops being applied, this test FAILS**, and its
    assertion message says why. Green is the healthy state. Measured by
    neutering ``_docstring_ids`` to return ``set()`` and re-running, not
    reasoned about -- the previous version of this sentence claimed the
    opposite, which is the sixth instance in this codebase of a description
    drifting from the mechanism it describes, and the second inside a fix for
    the same class of error. A sentence predicting which way something fails is
    read *at the moment it fails*, by someone who cannot yet see the mechanism;
    pointing them the wrong way costs more than saying nothing.

    Nothing is written to ``db/migrations/versions/``. The mutation exists only
    as a string in this process -- a mutated migration on disk, even briefly, is
    a hazard while other work is running against the same tree.
    """
    source = (repo_paths.versions_dir / "0009_gemini_embedding_plane.py").read_text(
        encoding="utf-8"
    )

    def drops_something(text: str) -> bool:
        tree = ast.parse(text)
        downgrade = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        parts = _strings_reachable_from(
            downgrade, _module_assignments(tree), set(), _docstring_ids(tree)
        )
        joined = " ".join(parts).upper()
        return "DROP TABLE" in joined or "DROP COLUMN" in joined

    assert drops_something(source), "0009 genuinely drops columns; the fixture is stale"

    neutered = source.replace("DROP COLUMN IF EXISTS", "RENAME COLUMN")
    split = neutered.index("def downgrade()")
    head, tail = neutered[:split], neutered[split:]
    tail = tail.replace(
        '"""Restore the 1024-wide Titan shape. Local iteration only.',
        '"""Restore the shape by issuing a DROP COLUMN for each.',
        1,
    )
    assert not drops_something(head + tail), (
        "a downgrade that drops nothing passed because its DOCSTRING said "
        "DROP COLUMN; _docstring_ids is no longer being applied"
    )


def test_every_revision_records_that_downgrade_is_local_only(repo_paths) -> None:
    """The forward-only rule is written where an operator will read it.

    ``T2.1`` sub-task: from Phase 13 onward schema rolls forward and code rolls
    back. Nobody should discover that during an incident, so each revision
    docstring says it.
    """
    for path in repo_paths.revision_paths():
        source = path.read_text(encoding="utf-8").lower()
        assert (
            "local iteration" in source
        ), f"{path.name} does not record that downgrade is for local iteration only"
        assert "phase 13" in source, f"{path.name} does not name the forward-only phase"


def test_0002_docstring_records_the_import_into_limitation(repo_paths) -> None:
    """``IMPORT INTO`` is unsupported once the vector index exists (``T2.2``).

    The seed must drop and rebuild the index (``T2.8``), and the person reading
    this migration during a seed failure needs that sentence here.
    """
    source = (repo_paths.versions_dir / "0002_evidence_plane.py").read_text(encoding="utf-8")
    assert "IMPORT INTO" in source, "0002 does not record the IMPORT INTO limitation"
    assert "T2.8" in source, "0002 does not point at T2.8 for the drop-and-rebuild"


def test_0002_optional_active_ann_index_is_commented_out_only(repo_paths) -> None:
    """``evidence_embedding_ann_active_idx`` is a commented block, never executed.

    Creating it now pre-empts a recall measurement nobody has taken
    (DDL section 5.3 "index variant R"; ``T2.2`` sub-task).
    """
    source = (repo_paths.versions_dir / "0002_evidence_plane.py").read_text(encoding="utf-8")
    assert (
        "evidence_embedding_ann_active_idx" in source
    ), "0002 should carry the optional index as a documented, commented-out block"
    live = _executed_sql(source)
    assert (
        "evidence_embedding_ann_active_idx" not in live
    ), "the optional active-prefix ANN index must not be created in Phase 2"
    assert (
        "recall" in source.lower()
    ), "0002 must record that the optional index is permitted only after a recall evaluation"


# ==========================================================================
# Live half - the migrated provenance_ci database
# ==========================================================================


def test_0001_creates_exactly_the_identity_tables(db_connection) -> None:
    """DDL section 3: seven identity and aggregate tables."""
    live = _live_tables(db_connection)
    assert set(TABLES_0001) <= live, f"missing: {sorted(set(TABLES_0001) - live)}"


def test_0002_creates_the_evidence_plane_tables(db_connection) -> None:
    """DDL section 4: ``source_artifacts`` and ``evidence_items``."""
    live = _live_tables(db_connection)
    assert set(TABLES_0002) <= live, f"missing: {sorted(set(TABLES_0002) - live)}"


def test_0003_creates_the_epistemic_plane_tables(db_connection) -> None:
    """DDL section 6: ``claims``, ``beliefs``, ``belief_versions``, ``belief_support``."""
    live = _live_tables(db_connection)
    assert set(TABLES_0003) <= live, f"missing: {sorted(set(TABLES_0003) - live)}"


def test_no_table_outside_the_migration_chain_exists(db_connection) -> None:
    """``T2.1`` acceptance: these tables "and nothing else".

    ``alembic_version`` is Alembic's own bookkeeping and is excluded by name.
    """
    live = _managed_tables(db_connection)
    assert live == set(ALL_TABLES), f"unexpected: {sorted(live - set(ALL_TABLES))}"


def test_primary_keys_are_uuid_without_a_database_default(
    db_connection, show_create: Callable[[str], str]
) -> None:
    """DDL conventions: ``id UUID NOT NULL PRIMARY KEY``, **no** ``gen_random_uuid()``.

    The Kernel must know every id before the transaction opens so it can build
    the whole write plan without a round trip.
    """
    for table in ALL_TABLES:
        assert "gen_random_uuid" not in show_create(
            table
        ), f"{table} defaults an id server-side; ids are generated application-side"
        if table in TABLES_WITHOUT_SURROGATE_ID:
            continue
        columns = _columns(db_connection, table)
        assert "id" in columns, f"{table} has no id column"
        assert columns["id"]["data_type"] == "uuid", f"{table}.id is not UUID"
        assert columns["id"]["is_nullable"] == "NO", f"{table}.id is nullable"


def test_timestamps_are_timestamptz_everywhere(db_connection) -> None:
    """DDL conventions: ``TIMESTAMPTZ`` always. Never ``TIMESTAMP``, never ``DATE``."""
    offenders: list[str] = []
    for table in ALL_TABLES:
        for name, spec in _columns(db_connection, table).items():
            if spec["data_type"] in {"timestamp without time zone", "date"}:
                offenders.append(f"{table}.{name} is {spec['data_type']}")
    assert offenders == [], f"non-timestamptz temporal columns: {offenders}"


def test_confidence_columns_are_decimal_five_four(db_connection) -> None:
    """DDL conventions: confidence and weight are ``DECIMAL(5,4)``, never float."""
    expected = {
        ("evidence_items", "extraction_confidence"),
        ("evidence_items", "source_authority"),
        ("claims", "extraction_confidence"),
        ("claims", "authority_score"),
        ("belief_versions", "belief_confidence"),
        ("belief_support", "weight"),
        ("fulfillments", "confidence"),
    }
    for table, column in sorted(expected):
        spec = _columns(db_connection, table)[column]
        assert spec["data_type"] == "numeric", f"{table}.{column} is {spec['data_type']}"
        assert (spec["numeric_precision"], spec["numeric_scale"]) == (5, 4), (
            f"{table}.{column} is DECIMAL"
            f"({spec['numeric_precision']},{spec['numeric_scale']}), expected (5,4)"
        )


def test_every_user_owned_table_carries_tenant_and_user_id(db_connection) -> None:
    """The tenancy spine: ``tenant_id`` **and** ``user_id`` as real columns."""
    for table in USER_OWNED_TABLES:
        columns = _columns(db_connection, table)
        for required in ("tenant_id", "user_id"):
            assert required in columns, f"{table} lacks {required}"
            assert columns[required]["is_nullable"] == "NO", f"{table}.{required} is nullable"
    counterparties = _columns(db_connection, "counterparties")
    assert "tenant_id" in counterparties, "counterparties must be tenant-scoped (deviation 1)"


def test_user_owned_tables_declare_the_composite_unique_key(
    show_create: Callable[[str], str],
) -> None:
    """``UNIQUE (tenant_id, user_id, id)`` - the target of every composite child FK."""
    for table, constraint in sorted(COMPOSITE_UNIQUE_TABLES.items()):
        create_sql = show_create(table)
        assert constraint in create_sql, f"{table} lacks {constraint}"


def test_every_foreign_key_into_user_owned_data_is_composite(
    show_create: Callable[[str], str],
) -> None:
    """``T2.1`` acceptance, and DDL section 19 test 11's non-Python half.

    A child row must be unable to stitch itself to a parent owned by a different
    user, even through a buggy repository. That guarantee is the ``tenant_id``
    and ``user_id`` columns being *part of the key*, not a WHERE clause.
    """
    violations: list[str] = []
    for table in ALL_TABLES:
        for name, (columns, referenced) in sorted(_foreign_keys(show_create(table)).items()):
            if referenced not in USER_OWNED_TABLES and referenced != "users":
                continue
            if name in COMPOSITE_FK_EXEMPTIONS:
                assert "tenant_id" in columns, f"{table}.{name} is not even tenant-scoped"
                continue
            if "tenant_id" not in columns or "user_id" not in columns:
                violations.append(f"{table}.{name} -> {referenced} on {columns}")
    assert violations == [], f"non-composite foreign keys into user-owned data: {violations}"


def test_cases_carries_revision_reopened_count_and_attention_level(db_connection) -> None:
    """``T2.1`` sub-task: the optimistic-concurrency and attention columns."""
    columns = _columns(db_connection, "cases")
    for required in ("revision", "reopened_count", "attention_level", "status"):
        assert required in columns, f"cases lacks {required}"
        assert columns[required]["is_nullable"] == "NO", f"cases.{required} is nullable"


def test_cases_vocabularies_match_the_domain_enums(show_create: Callable[[str], str]) -> None:
    """The DDL CHECKs mirror ``provenance_domain.enums`` exactly. No layer-local aliases.

    ``CANONICAL_DECISIONS.md``: "DDL checks ... mirror those values exactly".
    """
    create_sql = show_create("cases")
    assert _check_members(create_sql, "ck_cases_attention") == frozenset(
        member.value for member in AttentionLevel
    )
    assert _check_members(create_sql, "ck_cases_status") == frozenset(
        member.value for member in CaseStatus
    )


def test_cases_resolved_at_consistency_check_exists(show_create: Callable[[str], str]) -> None:
    """``ck_cases_resolved_at_consistent`` - named by DDL section 19 test 6."""
    assert "ck_cases_resolved_at_consistent" in show_create("cases")
    assert "ck_cases_reopen_implies_history" in show_create("cases")


def test_named_indexes_are_present(db_connection) -> None:
    """Every index DDL sections 3, 4 and 6 name, by name.

    Each one carries a comment there naming the query it serves; an index that
    silently went missing is a query that silently became a scan.
    """
    missing: list[str] = []
    for table, expected in sorted(EXPECTED_INDEXES.items()):
        live = _index_names(db_connection, table)
        missing.extend(f"{table}.{name}" for name in expected if name not in live)
    assert missing == [], f"missing indexes: {missing}"


def test_source_artifacts_has_both_dedupe_constraints(
    db_connection, show_create: Callable[[str], str]
) -> None:
    """DDL section 19 test 1 needs **both**.

    Dedupe keyed only on ``source_message_id`` is the specific bug that test
    exists to catch: the column is NULL for every uploaded ``.eml``, and a
    partial unique index over NULLs deduplicates nothing.
    """
    create_sql = show_create("source_artifacts")
    assert "uq_source_artifacts_content" in create_sql
    assert "uq_source_artifacts_message_id" in _index_names(db_connection, "source_artifacts")


def test_evidence_embedding_is_a_1024_dimension_vector(
    show_create: Callable[[str], str],
) -> None:
    """Frozen embedding contract: Titan v2, 1024 dimensions, one version."""
    create_sql = show_create("evidence_items")
    assert re.search(
        r"(?i)embedding\s+VECTOR\s*\(\s*1024\s*\)", create_sql
    ), f"evidence_items.embedding is not VECTOR(1024):\n{create_sql}"


def test_evidence_vocabularies_match_the_domain_enums(
    show_create: Callable[[str], str],
) -> None:
    """``retraction_status`` accepts exactly the four canon values, and no more."""
    create_sql = show_create("evidence_items")
    assert _check_members(create_sql, "ck_evidence_retraction_status") == frozenset(
        member.value for member in RetractionStatus
    )
    assert _check_members(create_sql, "ck_evidence_type") == frozenset(
        member.value for member in EvidenceType
    )


def test_evidence_is_retrieval_eligible_is_generated_and_stored(
    db_connection, show_create: Callable[[str], str]
) -> None:
    """Canon: ``is_retrieval_eligible = (retraction_status = 'ACTIVE')``.

    PB-3 passed on this cluster, so it is a generated STORED column rather than
    a kernel-written boolean plus a consistency check.
    """
    create_sql = show_create("evidence_items")
    assert re.search(
        r"(?is)is_retrieval_eligible\s+BOOL\s+NOT\s+NULL\s+AS\s*\(.*?retraction_status.*?"
        r"'ACTIVE'.*?\)\s+STORED",
        create_sql,
    ), f"is_retrieval_eligible is not a generated STORED column:\n{create_sql}"
    assert (
        _columns(db_connection, "evidence_items")["is_retrieval_eligible"]["data_type"] == "boolean"
    )


def test_evidence_ann_index_exists_with_user_id_as_prefix(db_connection) -> None:
    """``T2.2`` acceptance and ``G2.4``: the prefix column must be ``user_id``.

    Filter acceleration works only through prefix columns. A non-prefixed index
    passes ``SHOW INDEXES`` for existence and fails ``EXPLAIN`` at ``G6.2``, so
    this asserts the *position*, not merely the presence.
    """
    with db_connection.cursor() as cur:
        cur.execute("SHOW INDEXES FROM evidence_items")
        columns = [str(description.name) for description in cur.description or []]
        rows = cur.fetchall()
    name_at = columns.index("index_name")
    seq_at = columns.index("seq_in_index")
    column_at = columns.index("column_name")

    entries = sorted(
        (int(row[seq_at]), str(row[column_at]))
        for row in rows
        if str(row[name_at]) == "evidence_embedding_ann_idx"
    )
    assert entries, "evidence_embedding_ann_idx does not exist"
    assert (
        entries[0][1] == "user_id"
    ), f"ANN index prefix column is {entries[0][1]!r}, must be 'user_id'; columns={entries}"
    assert any(
        column == "embedding" for _, column in entries
    ), f"ANN index does not index the embedding column; columns={entries}"


def test_belief_versions_epistemic_status_includes_confirmed_and_disputed(
    show_create: Callable[[str], str],
) -> None:
    """The hero commit moves the ISP balance belief ``CONFIRMED -> DISPUTED``.

    The value does not change; only the epistemic status does. A column that
    cannot express that makes the hero commit unrepresentable.
    """
    members = _check_members(show_create("belief_versions"), "ck_belief_versions_status")
    assert {"CONFIRMED", "DISPUTED"} <= members
    assert members == frozenset(member.value for member in EpistemicStatus)


def test_belief_versions_grounding_check_exists(show_create: Callable[[str], str]) -> None:
    """``ck_belief_versions_grounded`` is a database CHECK, not Kernel discipline.

    The behavioural half of this - the exact error class CockroachDB raises - is
    ``test_kernel_required.py::test_belief_cannot_be_canonical_without_grounding``.
    """
    create_sql = show_create("belief_versions")
    assert "ck_belief_versions_grounded" in create_sql
    body = _constraint_body(create_sql, "ck_belief_versions_grounded")
    assert "DETERMINISTIC_DERIVATION" in body
    assert "support_edge_count" in body
    assert "derivation_kind" in create_sql
    assert _check_members(create_sql, "ck_belief_versions_derivation") == {
        "EVIDENCE_GROUNDED",
        "DETERMINISTIC_DERIVATION",
    }


def test_claims_unique_evidence_proposition_exists(show_create: Callable[[str], str]) -> None:
    """``uq_claims_evidence_proposition``: re-processing an artifact cannot double-count."""
    assert "uq_claims_evidence_proposition" in show_create("claims")


def test_belief_support_vocabularies_match_the_domain_enums(
    show_create: Callable[[str], str],
) -> None:
    """``relation`` is SUPPORTS|CONTRADICTS|QUALIFIES; ``source_kind`` allows DERIVATION."""
    create_sql = show_create("belief_support")
    assert _check_members(create_sql, "ck_belief_support_relation") == frozenset(
        member.value for member in SupportRelation
    )
    assert _check_members(create_sql, "ck_belief_support_source_kind") == frozenset(
        member.value for member in SupportSourceKind
    )
    assert "uq_belief_support_edge" in create_sql


# --------------------------------------------------------------------------
# 0004 - conflict, obligation and audit ledger (DDL section 7)
# --------------------------------------------------------------------------


def test_0004_creates_the_obligation_ledger_tables(db_connection) -> None:
    """DDL section 7: ``conflicts``, ``commitments``, ``fulfillments``, ``state_transitions``."""
    live = _live_tables(db_connection)
    assert set(TABLES_0004) <= live, f"missing: {sorted(set(TABLES_0004) - live)}"


def test_conflicts_vocabularies_match_the_domain_enums(show_create: Callable[[str], str]) -> None:
    """``status``, ``conflict_type`` and ``severity`` mirror ``provenance_domain``."""
    create_sql = show_create("conflicts")
    assert _check_members(create_sql, "ck_conflicts_status") == frozenset(
        member.value for member in ConflictStatus
    )
    assert _check_members(create_sql, "ck_conflicts_type") == frozenset(
        member.value for member in ConflictType
    )
    assert _check_members(create_sql, "ck_conflicts_severity") == frozenset(
        member.value for member in ConflictSeverity
    )


def test_conflicts_status_permits_open_and_needs_human(show_create: Callable[[str], str]) -> None:
    """``T2.4`` sub-task, and ``CANONICAL_DECISIONS.md`` -> *Hero commit canon*.

    The hero conflict is ``NEEDS_HUMAN``. ``OPEN`` is a legal column value that
    no disposition rule emits. Both belong in the enum; only one belongs in the
    hero row. A schema that dropped ``OPEN`` as "unused" would make the
    human-review queue's own partial index unwritable.
    """
    members = _check_members(show_create("conflicts"), "ck_conflicts_status")
    assert {"OPEN", "NEEDS_HUMAN"} <= members


def test_conflicts_dedupe_and_side_order_constraints_exist(
    db_connection, show_create: Callable[[str], str]
) -> None:
    """``uq_conflicts_live_identity`` and ``ck_conflicts_side_order`` (section 19 test 3).

    Side ordering is what stops the dedupe index being defeated by argument
    order: the Kernel normalises ``left`` to the lexicographically smaller UUID,
    and the CHECK is what makes that normalisation non-optional.
    """
    create_sql = show_create("conflicts")
    assert "ck_conflicts_side_order" in create_sql
    body = _constraint_body(create_sql, "ck_conflicts_side_order")
    assert "left_source_id" in body and "right_source_id" in body
    assert "uq_conflicts_live_identity" in _index_names(db_connection, "conflicts")
    assert "ck_conflicts_distinct_sides" in create_sql


def test_commitments_money_checks_are_all_present(show_create: Callable[[str], str]) -> None:
    """M1-M8 from DDL section 7.2, by name.

    These are the constraints that make "$420 promised, $200 paid, $220
    outstanding" impossible to get wrong *even with a Kernel bug*, which is why
    they are named individually rather than asserted as a count.
    """
    create_sql = show_create("commitments")
    for constraint in (
        "ck_commitments_amounts_nonneg",
        "ck_commitments_monetary_triple",
        "ck_commitments_fulfilled_le_committed",
        "ck_commitments_outstanding_identity",
        "ck_commitments_outstanding_blocks_fulfilled",
        "ck_commitments_money_needs_currency",
        "ck_commitments_partial_status",
        "ck_commitments_fulfilled_needs_payment",
    ):
        assert constraint in create_sql, f"commitments lacks {constraint}"

    identity = _constraint_body(create_sql, "ck_commitments_outstanding_identity")
    assert "committed_amount" in identity and "fulfilled_amount" in identity


def test_commitments_vocabularies_match_the_domain_enums(
    show_create: Callable[[str], str],
) -> None:
    create_sql = show_create("commitments")
    assert _check_members(create_sql, "ck_commitments_status") == frozenset(
        member.value for member in CommitmentStatus
    )
    assert _check_members(create_sql, "ck_commitments_type") == frozenset(
        member.value for member in CommitmentType
    )


def test_fulfillments_cannot_admit_one_evidence_twice(show_create: Callable[[str], str]) -> None:
    """``uq_fulfillments_commitment_evidence``: replaying a bank-transfer email is a no-op."""
    create_sql = show_create("fulfillments")
    assert "uq_fulfillments_commitment_evidence" in create_sql
    assert _check_members(create_sql, "ck_fulfillments_admission") == frozenset(
        member.value for member in FulfillmentAdmissionStatus
    )


def test_state_transitions_vocabulary_matches_the_domain_enum(
    show_create: Callable[[str], str],
) -> None:
    """``transition_type`` is the closed ``TransitionType`` set - the trace's spine."""
    assert _check_members(show_create("state_transitions"), "ck_state_transitions_type") == (
        frozenset(member.value for member in TransitionType)
    )


def test_state_transitions_reason_code_is_a_shaped_closed_code(
    show_create: Callable[[str], str],
) -> None:
    """``reason_code`` is ``NOT NULL`` and shape-checked.

    ``CASE_REOPEN_REASON_CODES`` membership is a Python guard on the transition;
    what the schema can enforce is that the column is never NULL and never free
    prose, so a lower-case sentence cannot reach the timeline.
    """
    create_sql = show_create("state_transitions")
    assert "ck_state_transitions_reason_shape" in create_sql
    assert "reason_code" in _constraint_body(create_sql, "ck_state_transitions_reason_shape")


# --------------------------------------------------------------------------
# 0005 - Kernel control plane (DDL section 8)
# --------------------------------------------------------------------------


def test_0005_creates_the_kernel_control_plane_tables(db_connection) -> None:
    """DDL section 8: ``memory_proposals`` and ``kernel_decisions``."""
    live = _live_tables(db_connection)
    assert set(TABLES_0005) <= live, f"missing: {sorted(set(TABLES_0005) - live)}"


def test_kernel_decisions_records_every_outcome(show_create: Callable[[str], str]) -> None:
    """``T2.4`` sub-task: writable for **every** outcome, rejections and NOOPs included.

    A decision table that can only hold acceptances turns every rejection into
    an unaudited event, and ``G4.4`` - which asserts a *rejection* row - becomes
    unassertable.
    """
    members = _check_members(show_create("kernel_decisions"), "ck_kernel_decisions_decision")
    assert members == frozenset(member.value for member in KernelDecision)
    assert {
        "NOOP_DUPLICATE",
        "REJECTED_INVALID_PROVENANCE",
        "REJECTED_INVARIANT",
        "REJECTED_SCHEMA",
        "RETRYABLE_CONCURRENCY",
    } <= members


def test_kernel_decisions_carries_transaction_opened(
    db_connection, show_create: Callable[[str], str]
) -> None:
    """``G4.4``: "foreign evidence is refused BEFORE a transaction opens".

    The gate asserts ``kernel_decisions.transaction_opened = false`` on a
    preflight rejection, and ``23_PHASE_GATES.md`` section 12 lists the column
    among the three a decision row must carry. DDL section 8.2 does not print
    it - a spec gap reported with ``T2.4`` - so the column is created from the
    gate, which outranks the task plan and is the only document
    that states what a decision row must contain.

    ``DEFAULT false`` is deliberate: a row written by a path that never thought
    about transactions claims the *weaker* fact, not the stronger one.
    """
    columns = _columns(db_connection, "kernel_decisions")
    assert "transaction_opened" in columns, "kernel_decisions lacks transaction_opened"
    assert columns["transaction_opened"]["data_type"] == "boolean"
    assert columns["transaction_opened"]["is_nullable"] == "NO"
    create_sql = show_create("kernel_decisions")
    assert "ck_kernel_decisions_commit_needs_transaction" in create_sql, (
        "an ACCEPTED decision that never opened a transaction is incoherent and "
        "must be unrepresentable"
    )


def test_kernel_decisions_noop_cannot_bump_the_revision(show_create: Callable[[str], str]) -> None:
    """The aggregate-revision invariant (02 section 10) as row-local CHECKs."""
    create_sql = show_create("kernel_decisions")
    assert "ck_kernel_decisions_revision_step" in create_sql
    assert "ck_kernel_decisions_noop_no_bump" in create_sql
    assert "ck_kernel_decisions_commit_ts" in create_sql


def test_memory_proposals_vocabularies_match_the_domain_enums(
    show_create: Callable[[str], str],
) -> None:
    create_sql = show_create("memory_proposals")
    assert _check_members(create_sql, "ck_memory_proposals_status") == frozenset(
        member.value for member in ProposalStatus
    )
    assert _check_members(create_sql, "ck_memory_proposals_type") == frozenset(
        member.value for member in ProposalType
    )
    assert "uq_memory_proposals_payload" in create_sql, (
        "the same payload from the same run must be the same proposal, so a retry "
        "is a NOOP_DUPLICATE rather than a second commit"
    )


def test_memory_proposals_model_ids_are_the_canon_bedrock_ids(
    show_create: Callable[[str], str],
) -> None:
    """``ck_memory_proposals_model`` rejects a stale model id at the boundary.

    DDL section 8.1 prints the bare ``anthropic.claude-opus-5`` /
    ``anthropic.claude-haiku-4-5`` pair. ``CANONICAL_DECISIONS.md`` -> *Bedrock
    model id canon* supersedes **every** bare id in the pack: the undated
    ``anthropic.claude-haiku-4-5`` does not exist on Bedrock in any form, and
    ``us.anthropic.claude-opus-5`` is denied to this account. Writing the
    superseded strings into a CHECK would make the shipped configuration
    unwritable, so the members are the ids the account can actually invoke.
    """
    members = _check_members(show_create("memory_proposals"), "ck_memory_proposals_model")
    assert DEFAULT_EXTRACTION_MODEL_ID in members
    assert DEFAULT_REASONING_MODEL_ID in members
    assert "deterministic.kernel" in members, "the Kernel's own derivations need an id"
    stale = {member for member in members if member.startswith("anthropic.claude")}
    assert stale == frozenset(), f"superseded bare Bedrock ids in the CHECK: {sorted(stale)}"


def test_the_deferred_foreign_keys_from_0003_are_closed(
    show_create: Callable[[str], str],
) -> None:
    """DDL section 8.3: the two FKs 0003 could not create because the target did not exist.

    ``belief_versions.kernel_decision_id`` is ``NOT NULL`` from 0003 onward, but
    ``kernel_decisions`` needs ``memory_proposals``, which references nothing in
    0003. Creating the column there and its constraint here is what breaks the
    knot without a nullable column and without a circular revision graph.
    """
    assert "fk_belief_versions_kernel_decision" in show_create("belief_versions")
    assert "fk_state_transitions_kernel_decision" in show_create("state_transitions")


# --------------------------------------------------------------------------
# 0006 - prospective memory (DDL section 9)
# --------------------------------------------------------------------------


def test_0006_creates_prospective_triggers(db_connection) -> None:
    """DDL section 9: the one table prospective memory needs."""
    live = _live_tables(db_connection)
    assert set(TABLES_0006) <= live, f"missing: {sorted(set(TABLES_0006) - live)}"


def test_prospective_triggers_predicate_ast_is_serialized_jsonb(db_connection) -> None:
    """``T2.5`` sub-task and ``16_TRIGGER_DSL.md`` section 3.

    The predicate is stored as the serialized safe AST from
    ``provenance_contracts.predicates`` - data, never free text and never
    executable code. The column type is where that decision becomes
    irreversible, so it is asserted as a type, not as a convention.
    """
    columns = _columns(db_connection, "prospective_triggers")
    assert "predicate_ast" in columns, "prospective_triggers lacks predicate_ast"
    assert columns["predicate_ast"]["data_type"] == "jsonb", (
        f"predicate_ast is {columns['predicate_ast']['data_type']}; a STRING column "
        "would make free text and executable code equally storable"
    )
    assert columns["predicate_ast"]["is_nullable"] == "NO"


def test_prospective_triggers_fired_check_is_biconditional(
    show_create: Callable[[str], str],
) -> None:
    """``ck_prospective_triggers_fired``: ``fired_at`` set exactly when the trigger fired.

    Section 19 test 8 (``D8``) asserts ``fired_at IS NULL`` on a *disarmed*
    trigger. Without a biconditional CHECK that assertion passes on a bug - a
    trigger that fired and then forgot to stamp the time reads identically.
    """
    create_sql = show_create("prospective_triggers")
    assert "ck_prospective_triggers_fired" in create_sql
    body = _constraint_body(create_sql, "ck_prospective_triggers_fired")
    assert "fired_at" in body
    assert "FIRED" in body
    assert "=" in body, "the check must be an equivalence, not a one-way implication"


def test_prospective_triggers_vocabularies_match_the_domain_enums(
    show_create: Callable[[str], str],
) -> None:
    create_sql = show_create("prospective_triggers")
    assert _check_members(create_sql, "ck_prospective_triggers_state") == frozenset(
        member.value for member in TriggerState
    )
    assert _check_members(create_sql, "ck_prospective_triggers_type") == frozenset(
        member.value for member in TriggerType
    )
    assert _check_members(create_sql, "ck_prospective_triggers_last_result") == frozenset(
        member.value for member in TriggerResult
    )


def test_prospective_triggers_reason_codes_are_the_closed_catalogue(
    show_create: Callable[[str], str],
) -> None:
    """``last_reason_code`` is legal only in combination with its ``last_result``.

    ``CANONICAL_DECISIONS.md``: "Trigger results ... plus one closed-set reason
    code". The CHECK pairs them, so ``DISARMED`` + ``PREDICATE_FALSE`` - a
    plausible-looking, meaningless combination - is refused by the database.
    """
    create_sql = show_create("prospective_triggers")
    members = _check_members(create_sql, "ck_prospective_triggers_last_reason")
    catalogue = frozenset(member.value for member in TriggerReasonCode)
    assert catalogue <= members, f"missing reason codes: {sorted(catalogue - members)}"
    invented = members - catalogue - frozenset(member.value for member in TriggerResult)
    assert invented == frozenset(), f"reason codes outside the catalogue: {sorted(invented)}"


# --------------------------------------------------------------------------
# 0007 - action plane (DDL section 10)
# --------------------------------------------------------------------------


def test_0007_creates_the_action_plane_tables(db_connection) -> None:
    """DDL section 10: ``action_intents`` and ``action_executions``."""
    live = _live_tables(db_connection)
    assert set(TABLES_0007) <= live, f"missing: {sorted(set(TABLES_0007) - live)}"


def test_action_intents_execution_needs_approval(
    db_connection, show_create: Callable[[str], str]
) -> None:
    """``ck_action_intents_execution_needs_approval`` - invariant 4, expressed in DDL.

    An unapproved execution is not "prevented"; it is unrepresentable. The four
    execution states each require ``approval_draft_sha256``, which is the hash
    frozen at approval time and re-checked by the executor.
    """
    columns = _columns(db_connection, "action_intents")
    for required in ("approval_draft_sha256", "basis_case_revision", "draft_sha256"):
        assert required in columns, f"action_intents lacks {required}"

    create_sql = show_create("action_intents")
    assert "ck_action_intents_execution_needs_approval" in create_sql
    body = _constraint_body(create_sql, "ck_action_intents_execution_needs_approval")
    assert "approval_draft_sha256" in body
    for state in ("EXECUTING", "EXECUTED", "FAILED_RETRYABLE", "FAILED_FINAL"):
        assert state in body, f"{state} is not covered by the approval gate"


def test_action_intents_approval_is_all_three_columns_or_none(
    show_create: Callable[[str], str],
) -> None:
    """Approval freezes a hash, an approver and a timestamp - all three or none."""
    create_sql = show_create("action_intents")
    body = _constraint_body(create_sql, "ck_action_intents_approval_complete")
    for column in ("approved_at", "approved_by_user_id", "approval_draft_sha256"):
        assert column in body, f"{column} is not part of the approval triple"
    assert (
        "ck_action_intents_grounded" in create_sql
    ), "an outbound action must cite at least one canonical belief version"
    assert "ck_action_intents_tier4_blocked" in create_sql


def test_action_intents_vocabularies_match_the_domain_enums(
    show_create: Callable[[str], str],
) -> None:
    create_sql = show_create("action_intents")
    assert _check_members(create_sql, "ck_action_intents_status") == frozenset(
        member.value for member in ActionState
    )
    assert _check_members(create_sql, "ck_action_intents_type") == frozenset(
        member.value for member in ActionType
    )


def test_action_executions_permit_many_attempts_and_one_success(
    db_connection, show_create: Callable[[str], str]
) -> None:
    """``uq_action_executions_single_success``: idempotency as a schema guarantee.

    A plain unique on ``action_intent_id`` would forbid a retry after a
    retryable failure; no unique at all would let two executor instances both
    send. The partial unique is the only shape that permits many attempts and at
    most one success.
    """
    create_sql = show_create("action_executions")
    assert "uq_action_executions_attempt" in create_sql, "attempts must still be distinct"
    assert "uq_action_executions_single_success" in _index_names(db_connection, "action_executions")
    assert "ck_action_executions_success_has_correlation" in create_sql


def test_action_executions_vocabulary_matches_the_domain_enum(
    show_create: Callable[[str], str],
) -> None:
    assert _check_members(show_create("action_executions"), "ck_action_executions_status") == (
        frozenset(member.value for member in ExecutionStatus)
    )


# --------------------------------------------------------------------------
# 0008 - events and infrastructure (DDL section 11)
# --------------------------------------------------------------------------


def test_0008_creates_the_event_and_infrastructure_tables(db_connection) -> None:
    """DDL section 11: the last four tables of the canonical set."""
    live = _live_tables(db_connection)
    assert set(TABLES_0008) <= live, f"missing: {sorted(set(TABLES_0008) - live)}"


def test_outbox_events_dedupes_on_aggregate_version_and_event_type(
    show_create: Callable[[str], str],
) -> None:
    """``uq_outbox_events_aggregate_event`` - section 19 test 9's second half.

    A Kernel transaction retried after a 40001 recomputes the same
    ``aggregate_version`` and collides here, so one domain event cannot be
    emitted twice by one logical commit.
    """
    create_sql = show_create("outbox_events")
    assert "uq_outbox_events_aggregate_event" in create_sql
    assert "ck_outbox_events_dispatched" in create_sql


def test_outbox_events_vocabularies_match_the_domain_enums(
    show_create: Callable[[str], str],
) -> None:
    """The closed event vocabulary lives in the database, not only in review."""
    create_sql = show_create("outbox_events")
    assert _check_members(create_sql, "ck_outbox_events_status") == frozenset(
        member.value for member in OutboxStatus
    )
    assert _check_members(create_sql, "ck_outbox_events_aggregate_type") == frozenset(
        member.value for member in AggregateType
    )
    assert _check_members(create_sql, "ck_outbox_events_event_type") == frozenset(
        member.value for member in EventType
    )


def test_processed_events_primary_key_is_consumer_and_event(
    show_create: Callable[[str], str],
) -> None:
    """``pk_processed_events`` - consumer-side dedupe by primary key, not by SELECT.

    04 section 17 step 2: insert first and let the key decide. A ``SELECT`` then
    ``INSERT`` is a race; a primary key is not.
    """
    create_sql = show_create("processed_events")
    assert "pk_processed_events" in create_sql
    assert re.search(
        r"(?i)PRIMARY\s+KEY\s*\(\s*consumer_name\s*(?:ASC\s*)?,\s*event_id\s*(?:ASC\s*)?\)",
        create_sql,
    ), f"processed_events primary key is not (consumer_name, event_id): {create_sql}"


def test_agent_runs_column_is_tool_calls_not_mcp_tool_calls(db_connection) -> None:
    """``CANONICAL_DECISIONS.md``: column ``tool_calls``, HTTP field ``mcp_tool_calls[]``.

    That pairing is frozen. ``agent_runs.mcp_tool_calls`` is not a column name,
    and ``G11.4`` queries the column, so a schema that used the field name would
    fail the gate while looking right in the API.
    """
    columns = _columns(db_connection, "agent_runs")
    assert "tool_calls" in columns, "agent_runs lacks tool_calls"
    assert "mcp_tool_calls" not in columns, "agent_runs.mcp_tool_calls is not a column name"
    for jsonb_column in ("tool_calls", "model_calls", "capability_status", "model_route"):
        assert columns[jsonb_column]["data_type"] == "jsonb", f"{jsonb_column} is not JSONB"


def test_agent_runs_counterfactual_is_toolless_and_memory_off(
    show_create: Callable[[str], str],
) -> None:
    """Canon item A: memory ON/OFF is an auditable property of a run, not a UI trick."""
    create_sql = show_create("agent_runs")
    assert _check_members(create_sql, "ck_agent_runs_memory_mode") == frozenset(
        member.value for member in MemoryMode
    )
    assert _check_members(create_sql, "ck_agent_runs_status") == frozenset(
        member.value for member in AgentRunStatus
    )
    assert "ck_agent_runs_counterfactual_consistent" in create_sql
    assert "ck_agent_runs_counterfactual_toolless" in create_sql


def test_idempotency_records_carry_a_trace_id(db_connection) -> None:
    """Memory Trace joins eleven sources on ``trace_id``; this is the eleventh.

    Nullable on purpose - the record may be created before a trace context
    exists - and indexed partially so the null rows cost nothing.
    """
    columns = _columns(db_connection, "idempotency_records")
    assert "trace_id" in columns
    assert columns["trace_id"]["is_nullable"] == "YES"
    assert "idx_idempotency_trace" in _index_names(db_connection, "idempotency_records")


# --------------------------------------------------------------------------
# The canonical set - G2.2 and G2.3
# --------------------------------------------------------------------------


def test_the_canonical_table_count_is_twenty_six(db_connection) -> None:
    """``G2.2``: the canonical set is complete and has nothing extra.

    26 includes the operational ``agent_runs`` and ``idempotency_records``.
    DDL section 20 risk 2 exists because 24 was once written down instead.
    """
    live = _managed_tables(db_connection)
    assert len(live) == CANONICAL_TABLE_COUNT, (
        f"{len(live)} base tables, expected {CANONICAL_TABLE_COUNT}: "
        f"unexpected={sorted(live - set(ALL_TABLES))} missing={sorted(set(ALL_TABLES) - live)}"
    )


def test_expected_tables_manifest_is_hand_written_sorted_and_complete() -> None:
    """``db/expected_tables.txt`` is the diff target, transcribed from the enumeration.

    It is written by hand and checked against the *test's* enumeration, never
    generated from the database: a generated manifest would make ``G2.2`` a diff
    of the database against itself, which passes for any schema at all.
    """
    assert EXPECTED_TABLES_MANIFEST.is_file(), f"missing {EXPECTED_TABLES_MANIFEST}"
    names = EXPECTED_TABLES_MANIFEST.read_text(encoding="utf-8").splitlines()
    assert names == sorted(names), "the manifest is not sorted; G2.2 diffs against ORDER BY 1"
    assert len(names) == CANONICAL_TABLE_COUNT, f"{len(names)} names, expected 26"
    assert set(names) == set(ALL_TABLES), (
        f"manifest/enumeration mismatch: only-in-manifest={sorted(set(names) - set(ALL_TABLES))} "
        f"only-in-tests={sorted(set(ALL_TABLES) - set(names))}"
    )


def test_live_tables_match_the_hand_written_manifest(db_connection) -> None:
    """``G2.2``'s ``diff`` produces no output - asserted here as list equality.

    The gate pipes ``ORDER BY 1`` into ``diff``, so this compares the ordered
    server-side list, not two sets: a manifest sorted under a different collation
    would pass a set comparison and fail the gate.
    """
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
        )
        live = [
            str(row[0])
            for row in cur.fetchall()
            if str(row[0]) != "alembic_version" and not str(row[0]).startswith(_SCRATCH_PREFIX)
        ]
    expected = EXPECTED_TABLES_MANIFEST.read_text(encoding="utf-8").splitlines()
    assert live == expected, (
        f"live-only={sorted(set(live) - set(expected))} "
        f"manifest-only={sorted(set(expected) - set(live))}"
    )


def test_exactly_the_five_agent_views_exist(db_connection) -> None:
    """``G2.3``: five views, canon names, nothing else.

    The names appear verbatim in Memory Trace nodes, so a sixth view - or a
    renamed one - is a trace a judge cannot follow back to DDL section 14.
    """
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema = 'public' ORDER BY 1"
        )
        live = [str(row[0]) for row in cur.fetchall()]
    assert live == list(AGENT_VIEWS), f"views are {live}, expected {list(AGENT_VIEWS)}"


def test_up_down_up_cycle_is_clean(
    migrated: str,
    alembic: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Runbook section 4.1: ``downgrade base`` then ``upgrade head``, twice, exit 0.

    Run last, and it restores head before returning, so no other test in this
    lane can observe a half-migrated database.
    """
    transcript: list[str] = []
    for command in ("downgrade", "upgrade", "downgrade", "upgrade"):
        # NOT ``head``. With ``0009`` on the chain, ``upgrade head`` hits its
        # acknowledgement guard, and the ``downgrade base`` half of this
        # cycle destroys all 26 tables and 5 views on a SHARED
        # ``provenance_ci``. Pinning to the deployed head keeps the cycle
        # meaningful and keeps it off the revision that is destructive by
        # design.
        target = "base" if command == "downgrade" else DEPLOYED_HEAD
        result = alembic(command, target, dsn=migrated)
        transcript.append(f"alembic {command} {target} -> exit {result.returncode}")
        assert result.returncode == 0, (
            f"alembic {command} {target} failed\n{result.stdout}\n{result.stderr}\n"
            + "\n".join(transcript)
        )

    with psycopg.connect(migrated) as conn:
        assert _managed_tables(conn) == set(ALL_TABLES)


def test_revision_files_are_not_generated_by_autogenerate(repo_paths) -> None:
    """DDL section 16 rule 4: literal SQL through ``op.execute()``.

    SQLAlchemy's CockroachDB dialect emits none of ``VECTOR``, ``FAMILY``,
    ``STORING``, or partial indexes, so ``op.create_table()`` cannot express
    this schema. Hand-written SQL is what keeps the spec and the migrations
    byte-identical.
    """
    for path in repo_paths.revision_paths():
        source = path.read_text(encoding="utf-8")
        assert "op.create_table(" not in source, f"{path.name} uses op.create_table()"
        assert "### commands auto generated" not in source, f"{path.name} is autogenerated"
        assert "op.execute(" in source, f"{path.name} does not use op.execute()"


def test_migrations_directory_has_no_stray_revisions(repo_paths) -> None:
    """The chain is exactly three files. A fourth would branch it silently."""
    present = sorted(
        path.name for path in Path(repo_paths.versions_dir).glob("*.py") if path.is_file()
    )
    assert present == sorted(repo_paths.revision_filenames), f"unexpected revisions: {present}"
