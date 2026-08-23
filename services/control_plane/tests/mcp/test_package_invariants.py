"""Properties of the package source itself, read through the AST.

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 12 - the write-path ownership table,
  and ``tools/write_path_lint.py``, which enforces it across the tree.
- ``docs/implementation/00_IMPLEMENTATION_MAP.md`` section 12 - no arbitrary SQL
  tool.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T11.2``.

Why a source scan in addition to the behavioural tests
------------------------------------------------------
:mod:`test_statements` proves the *composed* statement is a ``SELECT`` whose
text does not vary with any parameter. That is the property that matters and it
is checked by execution. This file adds the cheaper structural claim that no
string literal anywhere in the package - including one nothing currently
composes - carries a write verb or the name of a base table. The behavioural
test can only see the statements something composes; this one sees the ones
nothing composes yet, which is where the next mistake will be typed.

Docstrings are excluded, and deliberately: this file's own subject matter is
SQL, and a module that documents "``pv_agent_reader`` cannot ``INSERT INTO
claims``" must not trip the rule that sentence describes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from provenance_domain.enums import AgentSafeView
from services.control_plane.app import mcp as mcp_package
from services.control_plane.app.mcp import views

pytestmark = pytest.mark.unit

PACKAGE_DIR = Path(mcp_package.__file__).resolve().parent

#: The five agent-safe views, transcribed from ``CANONICAL_DECISIONS.md``.
CANON_VIEWS: frozenset[str] = frozenset(
    {
        "agent_case_context_v1",
        "agent_active_beliefs_v1",
        "agent_belief_lineage_v1",
        "agent_evidence_retrieval_v1",
        "agent_open_obligations_v1",
    }
)

#: The 26 canonical tables. ``pv_agent_reader`` can reach none of them, so the
#: package has no reason to name one.
CANONICAL_TABLES: frozenset[str] = frozenset(
    {
        "tenants",
        "users",
        "ingest_aliases",
        "counterparties",
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
        "processed_events",
        "agent_runs",
        "idempotency_records",
    }
)

# No trailing ``\b`` on the group, and the omission is deliberate. With one, the
# ``UPDATE\s+\w+`` alternative cannot match ``UPDATE agent_runs``: the group ends
# mid-identifier and the boundary assertion fails against the next word
# character. The rule read correctly and caught nothing. Found by planting
# ``"UPDATE agent_runs SET tool_calls = NULL"`` in the package and watching this
# file stay green - which is the only way that class of mistake is ever found.
_WRITE_VERB = re.compile(
    r"\b(INSERT\s+INTO\b|UPSERT\s+INTO\b|UPDATE\s+\w+|DELETE\s+FROM\b|GRANT\s+|REVOKE\s+|"
    r"CREATE\s+(TABLE|VIEW|INDEX)\b|DROP\s+(TABLE|VIEW)\b|ALTER\s+TABLE\b|TRUNCATE\b)",
    re.IGNORECASE,
)

#: A bare relation name: lowercase, underscores, ending ``_v1``.
_RELATION_LIKE = re.compile(r"^[a-z][a-z0-9_]*_v1$")


def _sources() -> list[Path]:
    return sorted(PACKAGE_DIR.glob("*.py"))


def _string_constants(source: Path) -> list[str]:
    """Every string literal in *source* that is not a docstring."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_package_has_modules_to_scan() -> None:
    """Without this, every assertion below passes over an empty list."""
    assert len(_sources()) >= 5, [p.name for p in _sources()]


@pytest.mark.parametrize("source", _sources(), ids=lambda p: p.name)
def test_no_string_literal_carries_a_write_verb(source: Path) -> None:
    found = [text for text in _string_constants(source) if _WRITE_VERB.search(text)]
    assert found == [], f"{source.name} carries a write statement: {found}"


@pytest.mark.parametrize("source", _sources(), ids=lambda p: p.name)
def test_no_string_literal_names_a_canonical_base_table(source: Path) -> None:
    leaked = {text for text in _string_constants(source) if text.lower() in CANONICAL_TABLES}
    assert leaked == set(), f"{source.name} names a base table: {sorted(leaked)}"


def test_the_only_relation_names_in_the_package_are_the_five_canon_views() -> None:
    """Two halves, because either alone would be weak.

    The registry names its relations through :class:`AgentSafeView`, so the
    package holds no relation-name literal at all and the source scan below
    legitimately finds nothing. That makes the scan a guard against a *new*
    literal appearing - ``"users_v1"``, a scratch view, a typo - and nothing
    more, which is why the positive half is asserted against the registry the
    server actually reads from rather than left implied by an empty set.
    """
    named: set[str] = set()
    for source in _sources():
        named |= {text for text in _string_constants(source) if _RELATION_LIKE.match(text)}
    assert named <= CANON_VIEWS, f"a relation outside the canon: {sorted(named - CANON_VIEWS)}"

    registered = {spec.view_name for spec in views.AGENT_VIEW_TOOLS.values()}
    assert registered == CANON_VIEWS
    assert registered == {member.value for member in AgentSafeView}
