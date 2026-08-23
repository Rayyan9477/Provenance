"""T4.1 - the Kernel boundary, asserted against the import graph.

`quality/20_TDD_STRATEGY.md` section 2.3 mechanism E1 makes "testable without
Bedrock" a property of the import graph rather than a matter of discipline. The
`.importlinter` contract enforces it in CI for `provenance_domain.kernel`; the
decision modules that `EXECUTION/70_TASK_PLAN.md` section 7 places in
`services/control_plane/app/memory_kernel/` need the same guarantee, and these
tests are it until the Integrator lands the contract block reported with this
task.

The check is an AST scan of the source rather than an inspection of
`sys.modules`, because a transitive import through a module that happened not
to be loaded yet would pass the runtime check and fail the real one.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE = "services.control_plane.app.memory_kernel"

#: The modules T4.1-T4.5 deliver. Every one of them must be importable and
#: usable with no database, no credentials and no model.
PURE_MODULES = (
    "config",
    "families",
    "propositions",
    "contradiction",
    "disposition",
    "money_ops",
    "preflight",
)

#: `.importlinter` contract `kernel-purity`, verbatim. `asyncio` is on the list
#: deliberately: a pure function that needs an event loop is a pure function
#: that is about to make a call.
FORBIDDEN_ROOTS = (
    "provenance_db",
    "boto3",
    "botocore",
    "anthropic",
    "httpx",
    "requests",
    "psycopg",
    "asyncio",
)


def _module_path(name: str) -> Path:
    module = importlib.import_module(f"{PACKAGE}.{name}")
    assert module.__file__ is not None
    return Path(module.__file__)


def _imported_roots(path: Path) -> set[str]:
    """Every top-level module name imported by *path*, aliases resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("name", PURE_MODULES)
def test_the_decision_modules_import_no_io_library(name: str) -> None:
    """E1, applied to where the code actually lives.

    A kernel that imports `provenance_db` can still be unit tested today and
    cannot be unit tested the moment somebody adds a connection argument. The
    import graph is where that becomes impossible rather than merely unusual.
    """
    roots = _imported_roots(_module_path(name))
    assert roots.isdisjoint(FORBIDDEN_ROOTS), (
        f"{PACKAGE}.{name} imports {sorted(roots & set(FORBIDDEN_ROOTS))}; "
        "the Memory Kernel's decisions must be reachable with no database, "
        "no credentials and no model (20_TDD_STRATEGY.md section 2.1)"
    )


@pytest.mark.parametrize("name", PURE_MODULES)
def test_every_decision_module_declares_its_public_surface(name: str) -> None:
    """An explicit `__all__` per T4.1. Without one, "what is the Kernel's API"
    is answered by whatever a caller happened to reach for."""
    module = importlib.import_module(f"{PACKAGE}.{name}")
    assert getattr(module, "__all__", None), f"{name} has no __all__"
    for symbol in module.__all__:
        assert hasattr(module, symbol), f"{name}.__all__ names missing {symbol}"


def test_the_package_docstring_states_the_single_writer_rule_first() -> None:
    """T4.1: "a docstring stating the single-writer rule in its first
    sentence". Anyone opening this package must read the constraint before the
    convenience."""
    package = importlib.import_module(PACKAGE)
    doc = (package.__doc__ or "").strip()
    first_sentence = doc.split("\n", 1)[0]
    assert "only canonical write path" in first_sentence.lower(), first_sentence


def test_importing_the_package_does_not_pull_in_a_database_driver() -> None:
    """The package's own `__init__` stays cheap and pure, so that when
    `transaction.py` lands at T4.10 and legitimately needs `provenance_db`,
    importing `memory_kernel.propositions` still does not."""
    package = importlib.import_module(PACKAGE)
    assert package.__file__ is not None
    roots = _imported_roots(Path(package.__file__))
    assert roots.isdisjoint(FORBIDDEN_ROOTS)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """`id()` of every string constant that is a module/class/function doc."""
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    docs.add(id(value))
    return docs


@pytest.mark.parametrize("name", PURE_MODULES)
def test_no_decision_module_issues_sql(name: str) -> None:
    """`specs/10_DATABASE_DDL.md` section 12: every canonical statement lives
    in the Kernel's write path and nowhere else. A decision module that formats
    its own statement is a second write path wearing a pure function's clothes,
    and `tools/write_path_lint.py` counts it as one."""
    shapes = ("INSERT INTO", "UPDATE SET", "DELETE FROM", "SELECT ")
    tree = ast.parse(_module_path(name).read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    offenders = [
        node.value[:60]
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docs
        and any(shape in node.value.upper() for shape in shapes)
    ]
    assert offenders == [], offenders
