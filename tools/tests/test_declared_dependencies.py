"""Every third-party import must be declared, or a clean clone cannot run.

The defect this closes
-----------------------
`services/control_plane/app/mcp/server.py` imported `mcp` (SDK 1.21.2). It was
installed on the build machine and **declared nowhere**. `pyproject.toml`'s
`control-plane` extra listed FastAPI, uvicorn and the four workspace packages,
and not the SDK the MCP server cannot start without.

Nothing caught it, because nothing on this machine ever performed the failing
action: importing that module in an environment built from `pyproject.toml`
alone. Every test run, every lint pass and every mypy pass used an environment
that happened to have it.

That is the exact shape of the README's spin-up promise -- a step-by-step
guide someone follows on a machine that is not ours. A missing declaration is
invisible here and fatal there, which is the worst combination available.

Why an import scan rather than a build
---------------------------------------
Actually proving it would mean building a fresh virtualenv from
`pyproject.toml` and importing every module, which takes minutes and a network.
This is the cheap approximation: every top-level module `services/` and
`agents/` import that is neither stdlib nor first-party must appear in some
declared dependency. It catches the realistic regression -- adding an import
and forgetting the extra -- rather than a subtly wrong version pin.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: Trees whose imports must be satisfiable from a declared extra. `tools/` and
#: `scripts/` are excluded deliberately: they are developer and CI utilities
#: that run from the `dev` extra, and `write_path_lint` excludes them for the
#: same reason -- they are not in the request path.
SCANNED = ("services", "agents", "packages")

#: First-party. Distributed as workspace packages, already declared by name.
FIRST_PARTY = {
    "provenance_contracts",
    "provenance_db",
    "provenance_domain",
    "provenance_telemetry",
    "services",
    "agents",
    "workers",
    "scripts",
    "tools",
    "db",
}

#: Distribution name -> the module it installs, where they differ.
DISTRIBUTION_MODULES = {
    "google-genai": "google",
    "psycopg[binary,pool]": "psycopg",
    "uvicorn[standard]": "uvicorn",
    "coverage[toml]": "coverage",
    "pyyaml": "yaml",
    "import-linter": "importlinter",
    "pytest-asyncio": "pytest_asyncio",
    "aws-cdk-lib": "aws_cdk",
    "pydantic-settings": "pydantic_settings",
    "psycopg[pool]": "psycopg_pool",
}

#: A distribution that installs more than one importable module.
EXTRA_MODULES = {
    "psycopg[binary,pool]": ("psycopg", "psycopg_pool"),
    "fastapi": ("fastapi", "starlette"),
}


def _pyproject_files() -> list[Path]:
    """The root manifest AND each workspace package's own.

    Reading only the root was the first version of this check, and it reported
    `pydantic` as undeclared -- it is declared by
    `packages/python/provenance_contracts/pyproject.toml`, which is the correct
    place for it. A check that fires on correctly-declared dependencies gets
    disabled, so it has to read every manifest a clean install would.
    """
    return [PYPROJECT, *sorted((REPO_ROOT / "packages" / "python").glob("*/pyproject.toml"))]


def _declared_modules() -> set[str]:
    specs: list[str] = []
    for manifest in _pyproject_files():
        project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
        specs.extend(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            specs.extend(extra)

    modules: set[str] = set()
    for spec in specs:
        name = spec.split(">=")[0].split("==")[0].split("<")[0].split("~=")[0].strip()
        modules.add(DISTRIBUTION_MODULES.get(name, name.replace("-", "_")))
        modules.update(EXTRA_MODULES.get(name, ()))
        # A distribution may also be importable under its own dashed-to-nothing
        # form; adding both keeps the check from failing on a naming convention.
        modules.add(name.replace("-", ""))
    return modules


def _guarded_import_nodes(tree: ast.AST) -> set[int]:
    """``id()`` of every import inside a ``try`` that catches ``ImportError``.

    A guarded import is a *soft* dependency: the module works without it. The
    UUIDv7 chain in `provenance_contracts.base` is the reason this exists --
    stdlib ``uuid.uuid7``, then the ``uuid6`` backport, then ``uuid_utils``,
    each in its own ``try``, falling back to ``uuid4``. Requiring all three to
    be declared would demand a manifest list alternatives it deliberately does
    not depend on, and requiring none of them would miss the real ones.
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import_error = any(
            handler.type is not None
            and (
                (isinstance(handler.type, ast.Name) and handler.type.id == "ImportError")
                or (
                    isinstance(handler.type, ast.Tuple)
                    and any(
                        isinstance(e, ast.Name) and e.id == "ImportError" for e in handler.type.elts
                    )
                )
            )
            for handler in node.handlers
        )
        if not catches_import_error:
            continue
        for statement in node.body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Import | ast.ImportFrom):
                    guarded.add(id(inner))
    return guarded


def _imported_roots() -> dict[str, str]:
    """Top-level module -> the first file that imports it, guarded ones excluded."""
    roots: dict[str, str] = {}
    for tree in SCANNED:
        for path in (REPO_ROOT / tree).rglob("*.py"):
            parts = path.parts
            if any(p in {"__pycache__", "node_modules", "cdk.out", ".venv"} for p in parts):
                continue
            try:
                parsed = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            guarded = _guarded_import_nodes(parsed)
            for node in ast.walk(parsed):
                if id(node) in guarded:
                    continue
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # `from . import x` has no module; relative is first-party.
                    names = [node.module] if node.module and node.level == 0 else []
                else:
                    continue
                for name in names:
                    root = name.split(".", 1)[0]
                    roots.setdefault(root, path.relative_to(REPO_ROOT).as_posix())
    return roots


def test_every_third_party_import_is_declared() -> None:
    declared = _declared_modules()
    stdlib = sys.stdlib_module_names
    undeclared: list[str] = []

    for root, first_use in sorted(_imported_roots().items()):
        if root in stdlib or root in FIRST_PARTY or root in declared:
            continue
        if root.startswith("_"):
            continue
        undeclared.append(f"{root}  (first imported by {first_use})")

    assert not undeclared, (
        "these third-party modules are imported but declared in no extra, so a "
        "clean clone built from pyproject.toml alone cannot import them:\n  "
        + "\n  ".join(undeclared)
    )


def test_the_mcp_sdk_is_declared() -> None:
    """The specific regression, pinned.

    `app/mcp/server.py` is the only module importing the SDK -- the tool
    registry, the statement composer and the scope binding are SDK-free, so the
    boundary does not depend on the SDK behaving. That is good design and it is
    also why the missing declaration was easy to miss: nothing else broke.
    """
    assert "mcp" in _declared_modules()


def test_the_scan_is_armed() -> None:
    """A scan that finds nothing because it looks nowhere proves nothing."""
    roots = _imported_roots()
    assert len(roots) > 30, f"only {len(roots)} import roots found; the walk is not reaching files"
    assert "pydantic" in roots, "a known third-party import was not seen by the scan"
    assert "pydantic" in _declared_modules(), "the declaration reader is not reading"
