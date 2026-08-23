"""No unscoped SQL can reach the vector path (``T6.2``, ``G6.4``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 14.4, which prints this file by
  name, and section 1's rules R-1 and R-2.
- ``docs/EXECUTION/70_TASK_PLAN.md`` ``T6.2`` acceptance: "every retrieval
  statement in the module carries a ``user_id`` predicate, **asserted by AST
  scan rather than by grep**".
- ``docs/quality/23_PHASE_GATES.md`` ``G6.4``.

Why an AST scan and not the grep section 14.4 prints
-----------------------------------------------------
Section 14.4's version reads every ``.py`` file as text and regex-matches
``SELECT ... ;``. That version cannot tell a SQL string from a docstring
quoting one, so the moment a module documents the predicate it is guarding --
which every module here does at length -- the scan starts matching prose. It
also cannot see string concatenation or an f-string, which is exactly how an
unscoped query gets written in a hurry.

``T6.2`` overrides it: the scan below walks the AST, collects only genuine
string **constants and their concatenations**, and identifies SQL by structure
(a ``SELECT`` with a ``FROM``) rather than by the presence of the word. A
docstring is still a string constant, so docstrings are excluded by position
(``ast.get_docstring``) rather than by content -- the one distinction the text
scan cannot make and the whole reason for the change.

What "the module" means
-----------------------
Every module under ``services/control_plane/app/retrieval/``. The rule is
deliberately wider than the ANN entry point: R-1 binds ``user_id`` on every
statement touching ``evidence_items.embedding``, and R-2 binds
``retraction_status = 'ACTIVE'`` on every statement reading ``evidence_items``
for active retrieval. A second, plausible-looking, unscoped statement in a
neighbouring module is the failure this guards, and it would sit outside a
scan pointed at one file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.retrieval]

REPO_ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL_PKG = REPO_ROOT / "services" / "control_plane" / "app" / "retrieval"
STATE_PROOF_PKG = REPO_ROOT / "services" / "control_plane" / "app" / "state_proof"

#: The cosine-distance operator. ``13_RETRIEVAL_SPEC.md`` section 19 wants
#: exactly one module to contain it.
VECTOR_OP = "<=>"

#: The one module permitted to carry the vector operator in a SQL constant.
#: ``CANONICAL_DECISIONS.md`` names ``provenance_db.repositories.evidence.
#: ann_search()`` as *the* ANN entry point; that repository module is owned by
#: another task and currently raises ``NotImplementedError``, so the statement
#: it will execute lives here and the entry point delegates to it. Recorded as
#: a discrepancy, not resolved silently.
ALLOWED_VECTOR_MODULE = RETRIEVAL_PKG / "ann.py"

_TABLES = ("evidence_items", "agent_evidence_retrieval_v1")

_SELECT_WITH_FROM = re.compile(r"(?is)\bSELECT\b.*?\bFROM\b")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """``id()`` of every string constant that is a docstring, by position.

    Position, not content: a module that documents its own predicate would
    otherwise be indistinguishable from one that emits it.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(id(value))
    return found


def _fold(node: ast.AST) -> str | None:
    """The literal text of *node* when it is a string constant or a ``+`` chain.

    Returns ``None`` for anything that is not statically a string, which is the
    honest answer: an f-string interpolating a table name is reported by the
    separate dynamic-SQL test rather than silently folded into something
    scannable.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold(node.left)
        right = _fold(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _sql_constants(
    path: Path, *, exempt_assignments: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """Every non-docstring string constant in *path* that looks like a statement.

    A folded ``+`` chain consumes its own operands. Without that, a statement
    written as ``"WITH ann AS (... user_id = $1 ...)" + " SELECT ... $2 ..."``
    would be scanned three times -- once whole and once per half -- and each
    half would be reported as missing the predicate the other half carries.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set(_docstring_nodes(tree))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            target = first.id if isinstance(first, ast.Name) else None
        if target in exempt_assignments and node.value is not None:
            skip.update(id(child) for child in ast.walk(node.value))
    folded: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and id(node) not in skip:
            text = _fold(node)
            if text is None:
                continue
            folded.append(text)
            skip.update(id(child) for child in ast.walk(node) if child is not node)
    out = [text for text in folded if _SELECT_WITH_FROM.search(text)]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in skip:
            continue
        text = _fold(node)
        if text is not None and _SELECT_WITH_FROM.search(text):
            out.append(text)
    return out


def _modules() -> list[Path]:
    return sorted(p for p in RETRIEVAL_PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _binds(statement: str, column: str) -> bool:
    """``column = $n`` or ``column = %(name)s`` or ``column = %s``.

    All three binding syntaxes count. Only an inlined *value* does not, which
    is the point: a UUID pasted into the string is not a bound parameter and is
    how a caller-supplied user id gets in.
    """
    return bool(re.search(rf"\b{column}\s*=\s*(\$\d+|%\(\w+\)s|%s)", statement))


# ==========================================================================


def test_the_scan_finds_statements_at_all() -> None:
    """The vacuity guard, first.

    ``0 offenders`` over ``0`` scanned statements is a lint that stopped
    working, and it reports success forever. Every assertion below is worthless
    without this one, so it runs first and names the number.
    """
    statements = [s for path in _modules() for s in _sql_constants(path)]
    assert statements, (
        "the AST scan found no SQL statements under services/control_plane/app/"
        "retrieval/. Either the retrieval modules emit no SQL (in which case "
        "G6.4 is vacuous) or _sql_constants no longer recognises them."
    )
    touching = [s for s in statements if any(t in s for t in _TABLES)]
    assert touching, "no scanned statement reads evidence_items; the scan is vacuous"


def test_every_evidence_statement_binds_user_id() -> None:
    """R-1. A statement that reads evidence without a ``user_id`` equality can
    cross users, and ANN can do it silently because the results still look
    right."""
    offenders = [
        (path.name, statement[:200])
        for path in _modules()
        for statement in _sql_constants(path)
        if any(table in statement for table in _TABLES) and not _binds(statement, "user_id")
    ]
    assert not offenders, f"retrieval SQL unscoped by user_id: {offenders}"


def test_every_evidence_statement_binds_tenant_id() -> None:
    """Defence in depth. ``user_id`` already implies the tenant through the FK;
    the tenant predicate is what survives a bad join."""
    offenders = [
        (path.name, statement[:200])
        for path in _modules()
        for statement in _sql_constants(path)
        if any(table in statement for table in _TABLES) and not _binds(statement, "tenant_id")
    ]
    assert not offenders, f"retrieval SQL unscoped by tenant_id: {offenders}"


def test_every_evidence_statement_filters_retraction_status() -> None:
    """R-2, and canon item C.

    A retracted item keeps its embedding, so ANN returns it. Only this
    predicate keeps a correction the user already made from being resurfaced.

    ``ann._ANN_SQL_BEFORE_LIFECYCLE_FILTER`` is exempt **by name**, and the
    exemption is the point rather than a hole: that constant is the statement
    *before* ``predicates.retraction_filter`` inserts the predicate, and
    assembling it that way is what makes the ``G6.7`` sabotage falsifiable --
    neuter the filter to the identity and the executed statement really does
    lose its lifecycle predicate. The executed statement is checked instead, by
    :func:`test_the_executed_ann_statement_filters_retraction_status`, which is
    a stronger check than scanning a literal: it is the string the database
    receives.
    """
    exempt = "_ANN_SQL_BEFORE_LIFECYCLE_FILTER"
    offenders = [
        (path.name, statement[:200])
        for path in _modules()
        for statement in _sql_constants(path, exempt_assignments={exempt})
        if any(table in statement for table in _TABLES)
        and "retraction_status = 'ACTIVE'" not in statement
    ]
    assert not offenders, f"retrieval SQL missing the retraction filter: {offenders}"


def test_the_executed_ann_statement_filters_retraction_status() -> None:
    """The string the database actually receives, not a literal in the source.

    A source scan can be satisfied by a constant nobody executes. This asserts
    on :func:`ann.render_ann_sql`, which is what the repository hands to
    psycopg -- so an assembly step that dropped the predicate fails here even
    though every literal in the file still contains it.
    """
    from services.control_plane.app.retrieval import ann

    executed = ann.render_ann_sql()
    assert "retraction_status = 'ACTIVE'" in executed
    assert _binds(executed, "user_id")
    assert _binds(executed, "tenant_id")


def test_only_one_module_carries_the_vector_operator() -> None:
    """Section 19: one module contains ``<=>``.

    One entry point is what makes ``EXPLAIN``-by-name assertable, and it is
    what stops three call sites growing independently until one of them drops
    the prefix.
    """
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _modules()
        if path != ALLOWED_VECTOR_MODULE
        and VECTOR_OP in path.read_text(encoding="utf-8")
        and any(VECTOR_OP in statement for statement in _sql_constants(path))
    ]
    assert not offenders, f"vector SQL outside {ALLOWED_VECTOR_MODULE.name}: {offenders}"


def test_no_retrieval_module_builds_sql_by_interpolation() -> None:
    """An f-string is how a bound parameter becomes an inlined value.

    It is also how ``D-06-001`` gets reintroduced: interpolating a query vector
    into the statement text is one keystroke away from computing it there.
    """
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr) or id(node) in skip:
                continue
            literal = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if _SELECT_WITH_FROM.search(literal) or VECTOR_OP in literal:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"SQL built by f-string interpolation: {offenders}"


def test_the_state_proof_builder_reaches_no_model() -> None:
    """``T5.3``: the builder imports nothing from ``agents/`` and nothing from a
    Bedrock client module.

    ``G5.1`` proves the same thing dynamically, by constructing an
    ``ExplodingClient``. This proves it structurally, which is the half that
    keeps holding when nobody remembers to set the environment variable.
    """
    banned = {"boto3", "botocore", "anthropic", "agents", "httpx", "requests"}
    offenders: list[str] = []
    for path in sorted(STATE_PROOF_PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.split(".", 1)[0] in banned:
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, f"a read model reached a model client: {offenders}"
