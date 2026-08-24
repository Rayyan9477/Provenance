"""Every graph name the runtime uses must be one the database accepts.

The defect this closes
-----------------------
``GRAPH_NAME_INGESTION`` is ``"ingestion_graph"``. ``ck_agent_runs_graph``
(migration ``0008``) admits ``'ingestion' | 'advocate' | 'resolver' |
'counterfactual'``. They have **never** agreed, and ``GRAPH_NAME_ADVOCATE``
(``"advocate_graph"``) has the same problem.

Nothing caught it because nothing had ever written an ``agent_runs`` row. The
constant was defined, exported, type-checked, and used as a dataclass default,
and every one of those steps is satisfied by a string the database will reject.
The first insert would have failed on a CHECK constraint -- at the end of a live
model call, after the tokens were spent.

This is the shape ``D-00-002`` had: a value transcribed once, agreeing with
nothing, and invisible until the moment it reached the system that actually
validates it. Here the validator is a CHECK constraint rather than an inference
endpoint, and the fix is the same -- compare the constant against the authority
rather than against a second copy of itself.

Why parse the migration rather than restate the list
-----------------------------------------------------
Writing ``{"ingestion", "advocate", ...}`` into this file would create the
second registry that caused the problem. The migration is the authority
because it is what the deployed database was built from, so the test reads it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents.runtime import state

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _REPO_ROOT / "db" / "migrations" / "versions" / "0008_events_infrastructure.py"


def _admitted_graph_names() -> frozenset[str]:
    """The literal set `ck_agent_runs_graph` allows, read from the migration."""
    text = _MIGRATION.read_text(encoding="utf-8")
    match = re.search(
        r"ck_agent_runs_graph\s+CHECK\s*\(\s*graph_name\s+IN\s*\((?P<body>.*?)\)\)",
        text,
        re.S,
    )
    assert match is not None, "ck_agent_runs_graph not found; the migration moved"
    return frozenset(re.findall(r"'([a-z_]+)'", match.group("body")))


def _declared_graph_names() -> dict[str, str]:
    return {
        name: getattr(state, name)
        for name in dir(state)
        if name.startswith("GRAPH_NAME_") and isinstance(getattr(state, name), str)
    }


def test_the_check_constraint_was_actually_found() -> None:
    """A regex that matched nothing would make every test below vacuous."""
    admitted = _admitted_graph_names()
    assert len(admitted) >= 2, f"parsed only {admitted} from the CHECK; the pattern is wrong"
    assert "ingestion" in admitted


def test_there_is_at_least_one_graph_name_constant() -> None:
    """Likewise: an empty mapping passes the agreement test trivially."""
    assert _declared_graph_names(), "no GRAPH_NAME_* constants found in agents.runtime.state"


def test_every_declared_graph_name_is_admitted_by_the_database() -> None:
    admitted = _admitted_graph_names()
    rejected = {
        name: value for name, value in _declared_graph_names().items() if value not in admitted
    }
    assert not rejected, (
        f"these graph names would be refused by ck_agent_runs_graph, which admits "
        f"{sorted(admitted)}: {rejected}. An agent_runs INSERT using one fails on a "
        "CHECK constraint at the end of a live model call, after the tokens are spent."
    )
