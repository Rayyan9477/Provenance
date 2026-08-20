#!/usr/bin/env python3
"""Every invariant names a test, and that test is one that actually runs.

Authority: `quality/23_PHASE_GATES.md` section 23.15 -

    **Smell:** "we have 240 tests" appears in the report; "invariant 3 is proven
    by test X" does not.

    **Detector:** `tools/invariant_map_check` (`G1.6`) requires every invariant to
    name a test. It is re-run at every gate, and any invariant whose mapped test is
    currently skipped or deferred reports as UNPROVEN. The count of tests is not
    reportable evidence; the map is.

USAGE
-----
    python -m tools.invariant_map_check provenance_domain/INVARIANTS.md
    python -m tools.invariant_map_check --list
    python -m tools.invariant_map_check --strict-locations

The final line is the gate assertion and its wording is contract:

    5 invariants, 5 mapped, 0 UNPROVEN

WHY SKIPPED COUNTS AS UNPROVEN
------------------------------
A map row that names `test_invariant_3` proves nothing if `test_invariant_3` is
decorated `@pytest.mark.skip(reason="deferred to phase 6")`. The row still reads
as coverage in the phase report, `pytest -q` still prints a green summary, and
the invariant is not proven by anything. Deferral is a legitimate engineering
decision; reporting a deferred invariant as covered is not. So a skipped,
skipif-ed or xfailed test reports `UNPROVEN` exactly as loudly as a missing one.

HOW COLLECTION IS DECIDED
-------------------------
Two `pytest --collect-only` passes over the test files the map names:

    pass A   --collect-only -q                       -> every node id
    pass B   --collect-only -q -m "not skip and
             not skipif and not xfail"               -> the ones that would run

`skip`, `skipif` and `xfail` are pytest's own builtin markers, so `-m` deselects
them without this tool needing to import a test module or parse a decorator.
A node in A but not in B is skipped or xfailed. A node in neither is missing.

WHY THE FUNCTION COLUMN IS IMPORTED, NOT JUST READ
--------------------------------------------------
A map that names `provenance_domain.invariants.derive_outstanding` after that
function has been renamed is a map that documents a function nobody can call.
Importing each named symbol is the cheapest way to make the left half of the row
as checkable as the right half.

LOCATION DRIFT
--------------
The `file:line` columns are verified against the real definition and a
disagreement is printed as a `note:`. It does **not** fail by default: line
numbers move whenever a docstring grows, and a tool that turns every gate red
for a stale line number is a tool people learn to skip. `--strict-locations`
makes drift fatal for the reviewer who wants it.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The path `23_PHASE_GATES.md` G1.6 and `70_TASK_PLAN.md` T1.4 both print. The
#: file really lives under `packages/python/`, so the documented command would
#: fail on a literal reading. Resolving the shorthand is cheaper than having two
#: spellings of the gate command in circulation.
DEFAULT_MAP = REPO_ROOT / "packages" / "python" / "provenance_domain" / "INVARIANTS.md"

#: Where a bare `<package>/INVARIANTS.md` argument is looked for.
PACKAGE_ROOTS: tuple[str, ...] = ("packages/python", "packages", "services", "agents")

#: pytest's own builtin markers for "this will not run".
DEFERRAL_MARKERS = "not skip and not skipif and not xfail"

_CODE = re.compile(r"`([^`]+)`")
_LOCATION = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+)$")


class MapError(RuntimeError):
    """The map file itself is unreadable. Distinct from an unproven invariant."""


@dataclass(frozen=True, slots=True)
class Location:
    """A `file:line` cell, repository-relative and POSIX-spelled."""

    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True, slots=True)
class MapRow:
    """One row of `INVARIANTS.md`: an invariant, its functions, its tests."""

    name: str
    functions: tuple[str, ...]
    function_locations: tuple[Location, ...]
    tests: tuple[str, ...]
    test_locations: tuple[Location, ...]
    source_line: int = 0

    def node_ids(self) -> tuple[str, ...]:
        return tuple(
            f"{location.path}::{test}"
            for test, location in zip(self.tests, self.test_locations, strict=False)
        )


@dataclass(frozen=True, slots=True)
class RowVerdict:
    """Whether one row is mapped, and every reason it is not."""

    row: MapRow
    mapped: bool
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(set(cell) <= set("-: ") and "-" in cell for cell in cells)


def _code_items(cell: str) -> tuple[str, ...]:
    """Every backticked item in a cell, in order.

    Backticks are required rather than optional: a bare cell would let prose
    ("the money identity") sit where a dotted symbol belongs, and prose does not
    import.
    """
    return tuple(item.strip() for item in _CODE.findall(cell) if item.strip())


def _locations(cell: str, *, row: str, column: str) -> tuple[Location, ...]:
    found: list[Location] = []
    for item in _code_items(cell):
        match = _LOCATION.match(item)
        if match is None:
            raise MapError(f"row {row!r}: {column} cell {item!r} is not `path:line`")
        found.append(
            Location(path=match.group("path").replace("\\", "/"), line=int(match.group("line")))
        )
    return tuple(found)


def parse_table(text: str) -> list[MapRow]:
    """Parse the five-column map table out of `INVARIANTS.md`.

    Columns, in order: invariant name, enforcing function(s), function
    `file:line`, proving test(s), test `file:line`. Rows outside a five-column
    table are ignored, so the file may carry as much prose as it needs.
    """
    rows: list[MapRow] = []
    seen_header = False
    for number, line in enumerate(text.splitlines(), start=1):
        cells = _cells(line)
        if len(cells) != 5:
            continue
        if _is_separator(cells):
            continue
        if not seen_header:
            # The first five-column row is the header. It is not data.
            seen_header = True
            continue
        name, functions, function_locations, tests, test_locations = cells
        if not name:
            raise MapError(f"line {number}: a map row with no invariant name")
        row_functions = _code_items(functions)
        row_tests = _code_items(tests)
        if not row_functions:
            raise MapError(f"row {name!r}: names no enforcing function in backticks")
        if not row_tests:
            raise MapError(f"row {name!r}: names no proving test in backticks")
        rows.append(
            MapRow(
                name=name,
                functions=row_functions,
                function_locations=_locations(function_locations, row=name, column="function"),
                tests=row_tests,
                test_locations=_locations(test_locations, row=name, column="test"),
                source_line=number,
            )
        )
    if not seen_header:
        raise MapError("no five-column map table found; INVARIANTS.md is not a map")
    return rows


def resolve_map_path(argument: str | None) -> Path:
    """Accept the documented shorthand as well as a real path."""
    if argument is None:
        return DEFAULT_MAP
    candidate = Path(argument)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()
    direct = (REPO_ROOT / candidate).resolve()
    if direct.exists():
        return direct
    for root in PACKAGE_ROOTS:
        nested = (REPO_ROOT / root / candidate).resolve()
        if nested.exists():
            return nested
    if candidate.exists():
        return candidate.resolve()
    raise MapError(f"{argument}: no such map file (looked in {', '.join(PACKAGE_ROOTS)})")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_function(dotted: str) -> object:
    """Import `pkg.module.symbol` and return the symbol.

    Raises:
        LookupError: the module does not import or the symbol is not in it.
    """
    if "." not in dotted:
        raise LookupError(f"{dotted!r} is not a dotted path to a symbol")
    module_name, _, symbol = dotted.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise LookupError(f"{dotted!r}: {module_name} does not import ({exc})") from exc
    try:
        return getattr(module, symbol)
    except AttributeError as exc:
        raise LookupError(f"{dotted!r}: {module_name} defines no {symbol!r}") from exc


def _definition_location(target: object) -> Location | None:
    try:
        source_file = inspect.getsourcefile(target)  # type: ignore[arg-type]
        _, line = inspect.getsourcelines(target)  # type: ignore[arg-type]
    except (TypeError, OSError):  # pragma: no cover - builtins have no source
        return None
    if source_file is None:  # pragma: no cover - defensive
        return None
    path = Path(source_file).resolve()
    try:
        relative = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:  # pragma: no cover - a symbol outside the repository
        relative = path.as_posix()
    return Location(path=relative, line=line)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _collect(paths: Sequence[str], marker_expression: str | None) -> frozenset[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        *paths,
    ]
    if marker_expression:
        command += ["-m", marker_expression]
    completed = subprocess.run(
        command, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False
    )
    node_ids: set[str] = set()
    for line in completed.stdout.splitlines():
        candidate = line.strip()
        if "::" not in candidate or candidate.startswith(("<", "=")):
            continue
        node_ids.add(candidate.replace("\\", "/"))
    return frozenset(node_ids)


def collect_tests(rows: Iterable[MapRow]) -> tuple[frozenset[str], frozenset[str]]:
    """Return `(collected, runnable)` node ids for every file the map names."""
    paths = sorted({location.path for row in rows for location in row.test_locations})
    if not paths:
        return frozenset(), frozenset()
    existing = [path for path in paths if (REPO_ROOT / path).exists()]
    if not existing:
        return frozenset(), frozenset()
    return _collect(existing, None), _collect(existing, DEFERRAL_MARKERS)


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def verdict_for(
    row: MapRow,
    *,
    collected: Iterable[str],
    runnable: Iterable[str],
    check_locations: bool = False,
) -> RowVerdict:
    """Is this invariant mapped to a function that exists and a test that runs?"""
    collected_set = frozenset(collected)
    runnable_set = frozenset(runnable)
    problems: list[str] = []
    notes: list[str] = []

    for index, dotted in enumerate(row.functions):
        try:
            target = resolve_function(dotted)
        except LookupError as exc:
            problems.append(f"enforcing function {exc}")
            continue
        if not callable(target):
            problems.append(f"enforcing function `{dotted}` is not callable")
            continue
        if index < len(row.function_locations):
            declared = row.function_locations[index]
            actual = _definition_location(target)
            if actual is not None and actual != declared:
                notes.append(f"`{dotted}` is declared at {declared} but defined at {actual}")

    for index, test in enumerate(row.tests):
        if index >= len(row.test_locations):
            problems.append(f"proving test `{test}` has no `file:line`")
            continue
        location = row.test_locations[index]
        node_id = f"{location.path}::{test}"
        if not (REPO_ROOT / location.path).exists():
            problems.append(f"proving test file `{location.path}` does not exist")
            continue
        if node_id not in collected_set:
            problems.append(f"proving test `{node_id}` was not collected (missing or renamed)")
            continue
        if node_id not in runnable_set:
            problems.append(
                f"proving test `{node_id}` is collected but skipped, skipif-ed or xfailed; "
                "a deferred test proves nothing"
            )

    if check_locations:
        problems.extend(notes)
        notes = []

    return RowVerdict(row=row, mapped=not problems, problems=tuple(problems), notes=tuple(notes))


def summary_line(verdicts: Sequence[RowVerdict]) -> str:
    """The gate assertion. Its exact wording is contract."""
    total = len(verdicts)
    mapped = sum(1 for verdict in verdicts if verdict.mapped)
    return f"{total} invariants, {mapped} mapped, {total - mapped} UNPROVEN"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.invariant_map_check",
        description=(
            "quality/23_PHASE_GATES.md section 23.15: every invariant names a test, and a "
            "skipped test counts as UNPROVEN."
        ),
    )
    parser.add_argument(
        "map",
        nargs="?",
        default=None,
        help="path to INVARIANTS.md (default: packages/python/provenance_domain/INVARIANTS.md)",
    )
    parser.add_argument("--list", action="store_true", help="print every row and its mapping")
    parser.add_argument(
        "--strict-locations",
        action="store_true",
        help="treat a stale `file:line` as UNPROVEN rather than as a note",
    )
    args = parser.parse_args(argv)

    out = sys.stdout
    try:
        map_path = resolve_map_path(args.map)
        rows = parse_table(map_path.read_text(encoding="utf-8"))
    except (MapError, OSError) as exc:
        out.write(f"invariant_map_check: {exc}\n")
        out.write("0 invariants, 0 mapped, 0 UNPROVEN\n")
        return 2

    collected, runnable = collect_tests(rows)
    verdicts = [
        verdict_for(
            row,
            collected=collected,
            runnable=runnable,
            check_locations=args.strict_locations,
        )
        for row in rows
    ]

    try:
        shown = map_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:  # pragma: no cover - a map outside the repository
        shown = str(map_path)
    out.write(f"invariant_map_check: {shown}\n")

    width = max((len(v.row.name) for v in verdicts), default=0)
    for verdict in verdicts:
        status = "MAPPED" if verdict.mapped else "UNPROVEN"
        out.write(f"  {verdict.row.name.ljust(width)}  {status}\n")
        if args.list or not verdict.mapped:
            for dotted in verdict.row.functions:
                out.write(f"      enforced by  {dotted}\n")
            for node_id in verdict.row.node_ids():
                out.write(f"      proven by    {node_id}\n")
        for problem in verdict.problems:
            out.write(f"      ! {problem}\n")
        for note in verdict.notes:
            out.write(f"      note: {note}\n")

    unproven = [verdict for verdict in verdicts if not verdict.mapped]
    if unproven:
        out.write(
            "\nAn UNPROVEN invariant is not a formatting problem. Section 23.15: the count of "
            "tests is not reportable evidence; the map is. Fix the test, or say plainly in the "
            "phase report that the invariant is unproven.\n"
        )

    out.write(f"{summary_line(verdicts)}\n")
    return 1 if unproven else 0


if __name__ == "__main__":
    raise SystemExit(main())
