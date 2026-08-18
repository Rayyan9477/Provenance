"""Every `provenance_*` package must ship a PEP 561 `py.typed` marker.

This is a vacuity guard, not a tidiness rule, and the distinction is the whole
point of the module.

Without the marker, mypy treats an installed `provenance_*` package as untyped
and silently degrades every symbol imported from it to `Any`. A downstream
`mypy --strict` run then **passes while checking nothing**: `services/` could
pass a `str` where a `Money` is required, or drop a required field from a
`MemoryProposal`, and the type checker would have no opinion. That is worse than
a type error, because a type error is visible.

How it was found, and why the obvious check missed it: `make lint` runs

    mypy --strict packages/python/provenance_domain packages/python/provenance_contracts

Naming both packages in one invocation makes mypy analyse `provenance_domain`
**from source**, so the missing marker is irrelevant and `make lint` is green.
Checking `provenance_contracts` alone — which is what a consumer package does —
produced 10 errors, beginning:

    error: Skipping analyzing "provenance_domain.enums": module is installed,
           but missing library stubs or py.typed marker  [import-untyped]
    error: Parameter 1 of Literal[...] cannot be of type "Any"  [valid-type]

So the project-wide command hid a defect that a per-package command exposes, and
Phase 8 is the first place a per-package command runs. The tests here assert the
marker directly rather than relying on either command.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages" / "python"


def _packages() -> list[Path]:
    if not PACKAGES_DIR.is_dir():
        return []
    return sorted(p for p in PACKAGES_DIR.iterdir() if (p / "pyproject.toml").is_file())


@pytest.mark.parametrize("package", _packages(), ids=lambda p: p.name)
def test_package_ships_a_py_typed_marker(package: Path) -> None:
    """PEP 561: the marker must sit beside `__init__.py`, inside the import package."""
    importable = package / "src" / package.name
    assert importable.is_dir(), f"{package.name} has no src/{package.name} directory"
    marker = importable / "py.typed"
    assert marker.is_file(), (
        f"{package.name} ships no py.typed. Consumers will silently treat every "
        f"symbol it exports as Any, and their mypy --strict runs will pass while "
        f"checking nothing. Create an empty {marker.relative_to(REPO_ROOT)}."
    )


@pytest.mark.parametrize("package", _packages(), ids=lambda p: p.name)
def test_marker_is_inside_the_wheel_package_root(package: Path) -> None:
    """A marker outside the packaged directory is not installed, so it does nothing.

    hatchling's ``[tool.hatch.build.targets.wheel] packages = ["src/<name>"]``
    includes everything under that directory, so a marker placed beside it — in
    ``src/`` or at the package root — is built into nothing and the defect
    reappears at install time only.
    """
    text = (package / "pyproject.toml").read_text(encoding="utf-8")
    expected = f'packages = ["src/{package.name}"]'
    if expected not in text:
        pytest.skip(f"{package.name} does not use the standard hatchling wheel layout")
    stray = [p for p in ((package / "py.typed"), (package / "src" / "py.typed")) if p.is_file()]
    assert not stray, (
        f"py.typed found outside the packaged directory: {stray}. It will not be "
        f"installed. It belongs at src/{package.name}/py.typed."
    )


def test_each_package_typechecks_in_isolation() -> None:
    """The regression test for the real defect.

    `make lint` names both typed packages in one command, which makes mypy read
    the dependency from source and hides a missing marker. This runs each package
    **alone**, the way a consumer will, so the marker is actually load-bearing
    here. If this ever fails with `import-untyped`, a marker has gone missing or
    a new package needs one.
    """
    typed = [
        PACKAGES_DIR / "provenance_domain",
        PACKAGES_DIR / "provenance_contracts",
    ]
    failures: list[str] = []
    for package in typed:
        if not package.is_dir():
            continue
        proc = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", str(package)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if proc.returncode != 0:
            failures.append(f"--- {package.name} ---\n{proc.stdout}{proc.stderr}")
    assert not failures, "mypy --strict fails on a package in isolation:\n" + "\n".join(failures)
