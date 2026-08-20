#!/usr/bin/env python
"""G4.3 — the Memory Kernel is the only canonical writer, checked structurally.

Authority
---------
- ``specs/10_DATABASE_DDL.md`` section 12, the write-path ownership table. It is
  the source of truth for :data:`CANONICAL_TABLES` and for the three places
  where a role other than ``pv_kernel_writer`` legitimately writes.
- ``quality/23_PHASE_GATES.md`` ``G4.3``::

      python -m tools.write_path_lint
      #  -> "canonical write statements found in 1 module: .../memory_kernel"
      #  -> "agents/: 0    workers/: 0    apps/web/: 0    packages/: 0"

- ``CANONICAL_DECISIONS.md`` -> *The Memory Kernel is the sole canonical
  writer*.

Why a grant is not enough
-------------------------
``pv_kernel_writer`` is the runtime enforcement and it is the stronger of the
two: a statement issued under the app role simply fails. But it fails **in
production, at the moment the row was needed**, and it says nothing at review
time about where the statement was written. This linter answers the review-time
question — *which modules contain a canonical write at all* — so that a second
writer is caught when it is typed rather than when it is deployed.

Why the counts are printed
--------------------------
``0 violations`` over ``0`` scanned statements is a vacuous pass. It is the
exact failure ``D-00-014`` recorded (``tools/`` was missing from ``testpaths``
and 28 tests ran nowhere for a week), so every run prints four numbers that
would move if the linter stopped seeing anything: the rule count, the violation
count, the number of modules walked, and the number of canonical write
statements found **inside** the Kernel. The last one is the load-bearing one. A
linter that cannot see the single legitimate writer cannot see an illegitimate
one either.

What is deliberately not scanned, and why
-----------------------------------------
:data:`NOT_SCANNED` names every tree left out and the reason. An exclusion
nobody can see is an exclusion that grows, so the list is printed on every run
rather than kept here as a comment. Test modules are excluded for the same
reason and counted separately: fixtures write rows directly, on purpose, under
the migrator role.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = [
    "CANONICAL_TABLES",
    "DEFAULT_ROOTS",
    "KERNEL_MODULE",
    "NOT_SCANNED",
    "RULES",
    "ScanResult",
    "Violation",
    "main",
    "scan_paths",
    "scan_source",
    "summary_lines",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The tables ``specs/10_DATABASE_DDL.md`` section 12 grants ``pv_kernel_writer``
#: an INSERT or an UPDATE on. A table nobody but the app writes -- ``users``,
#: ``tenants``, ``ingest_aliases``, ``action_executions``, ``processed_events``,
#: ``idempotency_records`` -- is absent, because flagging a control-plane INSERT
#: into one would be a false positive and a linter that cries wolf gets a
#: blanket ``noqa``. ``source_artifacts``, ``action_intents`` and ``agent_runs``
#: are absent for the mirror-image reason: the Kernel holds only SELECT on them,
#: so they have no Kernel-only write to protect.
CANONICAL_TABLES: Final[frozenset[str]] = frozenset(
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

#: The one module allowed to hold a canonical write. Repo-relative and POSIX, so
#: the comparison is the same on every platform.
KERNEL_MODULE: Final[str] = "services/control_plane/app/memory_kernel"

#: Section 12's three documented exceptions, split by operation because that is
#: how the grant is split. The parser admits evidence before any proposal
#: exists, and the API accepts a proposal before the Kernel decides it; but
#: ``UPDATE`` on both stays Kernel-only, so only the Kernel can retract evidence
#: or settle a proposal.
APP_INSERT_PERMITTED: Final[frozenset[str]] = frozenset({"evidence_items", "memory_proposals"})

#: The outbox dispatcher marks an event sent. It may never author one: an event
#: is written in the same transaction as the state it describes, or it is a
#: claim about state that was never committed.
DISPATCHER_UPDATE_PERMITTED: Final[frozenset[str]] = frozenset({"outbox_events"})

#: Every rule this linter knows, named so a violation cites one rather than a
#: line number and a shrug. W4 and W5 are listed even though they can never
#: produce a violation: an exemption that is not counted as a rule is an
#: exemption nobody reviews.
RULES: Final[tuple[str, ...]] = (
    "W1-canonical-INSERT-is-kernel-only",
    "W2-canonical-UPDATE-is-kernel-only",
    "W3-canonical-DELETE-is-forbidden-everywhere",
    "W4-evidence-and-proposal-INSERT-is-app-permitted",
    "W5-outbox-UPDATE-is-dispatcher-permitted",
)

#: The trees walked by a bare ``python -m tools.write_path_lint``. ``services``
#: is here because the Kernel lives in it: a run that did not walk the Kernel
#: could not report a non-zero ``kernel_statements`` and every clean result
#: would be vacuous.
DEFAULT_ROOTS: Final[tuple[str, ...]] = (
    "services",
    "packages",
    "workers",
    "agents",
    "apps/web",
)

#: The roots left out, each with the reason. Printed on every run.
NOT_SCANNED: Final[tuple[tuple[str, str], ...]] = (
    ("scripts/", "CI fixture loader; runs as pv_migrator, never in the request path"),
    ("db/", "migrations and seeds; owned by pv_migrator by construction"),
    ("infra/", "CDK; provisions the grants rather than issuing statements"),
    ("tools/", "lints and gates; opens no pool"),
    ("**/tests/**", "fixtures write rows directly, on purpose - counted below"),
)

#: The four roots ``G4.3`` prints by name. Printed whether or not they exist on
#: disk: a root that has not been built yet must still report its zero.
REPORTED_ROOTS: Final[tuple[str, ...]] = ("agents", "workers", "apps/web", "packages")

#: Non-Python source is scanned as text. ``apps/web`` holds no Python at all, so
#: without this its ``0`` would mean "nothing was looked at".
TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".sql"}
)

#: Directories never descended into.
SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".venv",
        ".next",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".ruff_cache",
    }
)

#: ``INSERT``/``UPSERT``/``UPDATE``/``DELETE`` and the table each names.
#:
#: ``\s+`` rather than a literal space: ``"insert   into\n  conflicts"`` is the
#: same statement and a linter that only reads tidy SQL is a linter a careless
#: writer walks past. The trailing ``\b`` on the operation keyword is what keeps
#: ``updated_at = now()`` from reading as an ``UPDATE`` of a table called
#: ``d_at``. The table group captures the whole identifier, so ``cases_archive``
#: is not ``cases``.
_STATEMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<op>INSERT\s+INTO|UPSERT\s+INTO|DELETE\s+FROM|(?<!FOR\s)UPDATE)\b\s+"
    r"(?:(?P<schema>[A-Za-z_][A-Za-z0-9_$]*)\s*\.\s*)?"
    r"[\"`]?(?P<table>[A-Za-z_][A-Za-z0-9_$]*)[\"`]?",
    re.IGNORECASE | re.DOTALL,
)

#: An ``UPSERT`` is an ``INSERT`` for ownership purposes; the reported operation
#: is normalised so the exemption table has one key per grant rather than two.
_OPERATIONS: Final[dict[str, str]] = {
    "INSERTINTO": "INSERT",
    "UPSERTINTO": "INSERT",
    "DELETEFROM": "DELETE",
    "UPDATE": "UPDATE",
}

_LINE_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"--[^\n]*")


@dataclass(frozen=True, slots=True)
class Violation:
    """One canonical write in a module that may not hold one."""

    path: str
    line: int
    rule: str
    table: str
    operation: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.operation} on canonical table {self.table}"


@dataclass
class ScanResult:
    """What one scan saw. Every field exists so a zero can be interrogated."""

    violations: list[Violation] = field(default_factory=list)
    scanned_modules: int = 0
    scanned_text_files: int = 0
    excluded_test_modules: int = 0
    canonical_statements: int = 0
    kernel_statements: int = 0
    per_root: dict[str, int] = field(default_factory=dict)
    #: Modules whose canonical writes this linter could not see. Never empty
    #: silently: an unparsable module is a hole, not a pass.
    unparsable: list[str] = field(default_factory=list)
    #: Repo-relative directories that hold at least one canonical write.
    writing_modules: set[str] = field(default_factory=set)

    def merge(self, other: ScanResult) -> None:
        self.violations.extend(other.violations)
        self.scanned_modules += other.scanned_modules
        self.scanned_text_files += other.scanned_text_files
        self.excluded_test_modules += other.excluded_test_modules
        self.canonical_statements += other.canonical_statements
        self.kernel_statements += other.kernel_statements
        self.unparsable.extend(other.unparsable)
        self.writing_modules |= other.writing_modules
        for key, value in other.per_root.items():
            self.per_root[key] = self.per_root.get(key, 0) + value


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _normalise(path: str) -> str:
    return path.replace("\\", "/")


def _in_kernel(path: str) -> bool:
    normalised = _normalise(path)
    return normalised == KERNEL_MODULE or f"{KERNEL_MODULE}/" in f"{normalised}/"


def _owning_module(path: str) -> str:
    """The directory a write belongs to, for the "found in N modules" line."""
    normalised = _normalise(path)
    if _in_kernel(normalised):
        return KERNEL_MODULE
    parent = normalised.rsplit("/", 1)[0]
    return parent if parent != normalised else "."


def _classify(operation: str, table: str, *, in_kernel: bool) -> str | None:
    """The rule *operation* on *table* breaks, or ``None`` when it is permitted.

    ``DELETE`` is checked before the Kernel exemption on purpose. Nothing
    deletes a canonical row, the Kernel included: retraction is an UPDATE
    (``10_DATABASE_DDL.md`` section 5.4), because the embedding and the lineage
    that cite the row have to keep resolving after it is withdrawn.
    """
    if operation == "DELETE":
        return RULES[2]
    if in_kernel:
        return None
    if operation == "INSERT":
        return None if table in APP_INSERT_PERMITTED else RULES[0]
    if operation == "UPDATE":
        return None if table in DISPATCHER_UPDATE_PERMITTED else RULES[1]
    return None


def _scan_text(text: str, path: str, line_offset: int, result: ScanResult) -> None:
    """Record every canonical write statement in *text* against *result*."""
    in_kernel = _in_kernel(path)
    for match in _STATEMENT_RE.finditer(text):
        table = match.group("table").lower()
        if table not in CANONICAL_TABLES:
            continue
        operation = _OPERATIONS[re.sub(r"\s+", "", match.group("op")).upper()]
        result.canonical_statements += 1
        if in_kernel:
            result.kernel_statements += 1
        result.writing_modules.add(_owning_module(path))
        rule = _classify(operation, table, in_kernel=in_kernel)
        if rule is None:
            continue
        line = line_offset + text.count("\n", 0, match.start())
        result.violations.append(
            Violation(path=_normalise(path), line=line, rule=rule, table=table, operation=operation)
        )


def _literal_strings(tree: ast.Module) -> list[ast.Constant]:
    """Every string constant that is not a bare expression statement.

    A module, class or function docstring is an :class:`ast.Expr` wrapping a
    constant and nothing else, and a module that documents this rule must not
    trip it. Everything else counts — assigned, annotated, returned, nested in a
    dict, or handed straight to ``cursor.execute`` — because the linter's job is
    to find the SQL, not to guess how it was plumbed.
    """
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def scan_source(source: str, path: str) -> ScanResult:
    """Scan one Python module's source. Pure: no filesystem, no imports."""
    result = ScanResult(scanned_modules=1)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        result.unparsable.append(_normalise(path))
        return result
    for node in _literal_strings(tree):
        assert isinstance(node.value, str)
        _scan_text(node.value, path, node.lineno, result)
    root = _root_label(Path(path))
    result.per_root[root] = len(result.violations)
    return result


def scan_file(path: Path, *, display: str) -> ScanResult:
    """Scan one file, Python by AST and anything else as commentless text."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        result = ScanResult()
        result.unparsable.append(display)
        return result
    if path.suffix == ".py":
        return scan_source(text, display)
    stripped = _SQL_COMMENT_RE.sub("", _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text)))
    result = ScanResult(scanned_text_files=1)
    _scan_text(stripped, display, 1, result)
    result.per_root[_root_label(Path(display))] = len(result.violations)
    return result


# ---------------------------------------------------------------------------
# Walking the tree
# ---------------------------------------------------------------------------


def _root_label(path: Path) -> str:
    """The reported root a path belongs to.

    Matched against :data:`DEFAULT_ROOTS` by suffix so that a temporary
    directory laid out like the repository reports the same labels the real
    tree does; the counterfactual test plants its violation in exactly such a
    directory and must see it land under ``agents``.
    """
    posix = path.as_posix()
    for root in sorted(DEFAULT_ROOTS, key=len, reverse=True):
        if posix == root or posix.endswith(f"/{root}") or f"/{root}/" in posix:
            return root
    return path.parts[0] if path.parts else "."


def _is_test_module(path: Path) -> bool:
    parts = path.parts
    return (
        "tests" in parts
        or "test" in parts
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or path.name == "conftest.py"
    )


def _walk(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix == ".py" or path.suffix in TEXT_SUFFIXES:
            yield path


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def scan_paths(paths: Sequence[Path]) -> ScanResult:
    """Scan every file under *paths*. Missing paths contribute nothing.

    A path that does not exist is not an error here — ``agents/`` is a declared
    root that Phase 6 creates — but it *is* an error when a caller names one
    explicitly, and :func:`main` makes that distinction.
    """
    total = ScanResult()
    for root in paths:
        label = _root_label(root)
        total.per_root.setdefault(label, 0)
        if not root.exists():
            continue
        for path in _walk(root):
            display = _display_path(path)
            if path.suffix == ".py" and _is_test_module(path):
                total.excluded_test_modules += 1
                continue
            total.merge(scan_file(path, display=display))
    for label in REPORTED_ROOTS:
        total.per_root.setdefault(label, 0)
    return total


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summary_lines(result: ScanResult) -> tuple[str, ...]:
    """The report ``G4.3`` reads, counts first.

    ``len(RULES)`` and ``len(violations)`` are both printed because either one
    alone can be read as good news: zero violations from zero rules is the
    vacuous pass this whole module exists to make impossible to claim.
    """
    modules = sorted(result.writing_modules)
    lines = [
        f"write_path_lint: {len(RULES)} rules, {len(result.violations)} violations",
        (
            f"scanned {result.scanned_modules} python modules and "
            f"{result.scanned_text_files} non-python source files; "
            f"{result.canonical_statements} canonical write statements, "
            f"{result.kernel_statements} of them in the Kernel"
        ),
        (
            f"canonical write statements found in {len(modules)} module"
            f"{'' if len(modules) == 1 else 's'}: " + (", ".join(modules) or "(none)")
        ),
        "    " + "    ".join(f"{root}/: {result.per_root.get(root, 0)}" for root in REPORTED_ROOTS),
        f"excluded {result.excluded_test_modules} test modules, and these trees:",
    ]
    lines.extend(f"    {tree:<14} {reason}" for tree, reason in NOT_SCANNED)
    for rule in RULES:
        lines.append(f"    rule {rule}")
    if result.unparsable:
        lines.append(f"UNPARSABLE ({len(result.unparsable)}): these modules were not checked")
        lines.extend(f"    {path}" for path in result.unparsable)
    return tuple(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 clean, 1 on a violation, 2 on a usage error or an unreadable file."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        roots = [Path(arg) for arg in args]
        missing = [str(root) for root in roots if not root.exists()]
        if missing:
            print(f"write_path_lint: no such path: {', '.join(missing)}", file=sys.stderr)
            return 2
    else:
        roots = [REPO_ROOT / root for root in DEFAULT_ROOTS]

    result = scan_paths(roots)
    for violation in result.violations:
        print(violation)
    for line in summary_lines(result):
        print(line)
    if result.unparsable:
        return 2
    return 1 if result.violations else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
