"""No repository writes a canonical table, and no read is unscoped — T3.3.

Authority
---------
- ``EXECUTION/70_TASK_PLAN.md`` T3.3, which names this file and its two
  claims: "asserts that no repository module contains an
  ``INSERT``/``UPDATE``/``DELETE`` against a canonical table, by AST
  inspection, and that every read carries a tenant and user predicate".
- ``specs/10_DATABASE_DDL.md`` section 12 — write-path ownership. The
  canonical set below is exactly the set of tables ``pv_kernel_writer`` may
  ``INSERT`` or ``UPDATE``; the permitted set is what
  ``pv_app_reader_writer`` owns outright.
- ``CANONICAL_DECISIONS.md`` -> *Names and counts*:
  ``provenance_db.repositories.evidence.ann_search()`` is the ANN entry point.
- ``implementation/06_CODING_AGENT_HANDOFF.md`` section 19, guardrails 1 and 3.

Why this test is marked ``unit`` although it lives in ``tests/db/``
------------------------------------------------------------------
The path is the one ``EXECUTION/70_TASK_PLAN.md`` T3.3 names and the plan is
authoritative on paths. The marker describes what the test *needs*, and this
one needs no cluster, no credentials and no socket: it parses source. Marking
it ``db`` would make a hermetic check unrunnable on a laptop without a
cluster, which is how static guards quietly stop running.
"""

from __future__ import annotations

import ast
import pkgutil
import re
from pathlib import Path

import pytest

from provenance_db import repositories

pytestmark = pytest.mark.unit

#: Tables ``pv_kernel_writer`` may INSERT or UPDATE — ``10_DATABASE_DDL.md``
#: section 12. Only ``services/control_plane/app/memory_kernel/`` may write
#: these, and it does not live in this package.
CANONICAL_TABLES: frozenset[str] = frozenset(
    {
        "counterparties",
        "relationships",
        "contexts",
        "cases",
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
        "outbox_events",
    }
)

#: The non-canonical writes this package is allowed to carry, enumerated so the
#: boundary is legible rather than implied (T3.3 sub-task 4).
PERMITTED_WRITE_TABLES: frozenset[str] = frozenset(
    {
        "idempotency_records",
        "agent_runs",
        "processed_events",
        "action_intents",
        "action_executions",
        "source_artifacts",
        "tenants",
        "users",
        "ingest_aliases",
    }
)

WRITE_VERBS = re.compile(r"\b(insert\s+into|update|delete\s+from)\s+([a-z_][a-z0-9_]*)", re.I)
FROM_OR_JOIN = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", re.I)


def repository_modules() -> list[Path]:
    root = Path(repositories.__file__).parent
    return sorted(root.glob("*.py"))


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The ``id()`` of every docstring constant in *tree*.

    Docstrings are excluded from the SQL scan deliberately. Every module in
    this package documents the write boundary in prose — "the dispatcher's
    ``UPDATE outbox_events SET status``" is the clearest way to say which write
    is permitted — and a scanner that reads prose as SQL reports the
    documentation of a rule as a breach of it.
    """
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = node.body
        if not body or not isinstance(body[0], ast.Expr):
            continue
        first = body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(id(first))
    return found


def sql_literals(path: Path) -> list[str]:
    """Every non-docstring string constant in *path* that looks like SQL."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_ids(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if re.search(r"\b(select|insert|update|delete)\b", text, re.I) and "\n" in text:
            found.append(text)
    return found


def test_the_package_declares_every_module_the_task_plan_names() -> None:
    """Split by domain, not by table: a repository spanning two aggregates
    hides a transaction boundary."""
    expected = {
        "cases",
        "evidence",
        "beliefs",
        "commitments",
        "triggers",
        "actions",
        "events",
        "agent_runs",
    }
    present = {module.name for module in pkgutil.iter_modules(repositories.__path__)}
    assert expected <= present, f"missing repository modules: {sorted(expected - present)}"


def test_no_repository_writes_a_canonical_table() -> None:
    """Invariant 1 of ``00_IMPLEMENTATION_MAP.md`` section 3, as a property of
    the source rather than of a code review."""
    offences: list[str] = []
    scanned = 0
    for path in repository_modules():
        for statement in sql_literals(path):
            scanned += 1
            for verb, table in WRITE_VERBS.findall(statement):
                if table.lower() in CANONICAL_TABLES:
                    offences.append(f"{path.name}: {verb.upper()} {table}")
                elif table.lower() not in PERMITTED_WRITE_TABLES:
                    offences.append(f"{path.name}: {verb.upper()} {table} (not enumerated)")
    assert offences == [], "\n".join(offences)
    assert scanned > 0, (
        "no SQL statement was found in the package at all, so this check passed "
        "vacuously — the same failure mode tools/txn_purity_lint.py prints its "
        "scanned count to prevent"
    )


def test_every_canonical_read_carries_a_tenant_and_a_user_predicate() -> None:
    """``tests/retrieval/test_no_unscoped_sql.py`` (``G6.4``) will scan for this
    later; making it structurally impossible now is cheaper than fixing it then."""
    offences: list[str] = []
    for path in repository_modules():
        for statement in sql_literals(path):
            tables = {name.lower() for name in FROM_OR_JOIN.findall(statement)}
            if not tables & (CANONICAL_TABLES | PERMITTED_WRITE_TABLES):
                continue
            if "tenant_id" not in statement or "user_id" not in statement:
                offences.append(f"{path.name}: unscoped read of {sorted(tables)}")
    assert offences == [], "\n".join(offences)


def test_no_read_signature_omits_both_a_principal_and_an_explicit_pair() -> None:
    """Every read method requires a ``Principal`` or an explicit
    ``(tenant_id, user_id)`` pair, and there is no signature that omits both."""
    offences: list[str] = []
    for path in repository_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            args = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
            scoped = "principal" in args or {"tenant_id", "user_id"} <= args
            if not scoped:
                offences.append(f"{path.name}:{node.name}({', '.join(sorted(args))})")
    assert offences == [], "\n".join(offences)


def test_ann_search_is_the_single_ann_entry_point() -> None:
    """Phase 6 gets one canonical entry point rather than three call sites that
    grew independently."""
    from provenance_db.repositories import evidence

    assert callable(evidence.ann_search)
    holders = [path.name for path in repository_modules() if _defines(path, "ann_search")]
    assert holders == ["evidence.py"], holders


def _defines(path: Path, name: str) -> bool:
    """Whether *path* contains a function *definition* called *name*.

    A text search would also match the package docstring, which names
    ``ann_search()`` precisely because it is the one entry point — mentioning
    a rule is not breaking it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name
        for node in ast.walk(tree)
    )
