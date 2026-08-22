"""Run the sabotage matrix: neuter each symbol, require its tests to go red.

Why this exists
---------------
``tests/sabotage_matrix.yaml`` holds a claim per entry -- "neuter this symbol
and that selection fails". ``tools/sabotage_guard.py`` checks the file is
append-only and counts it. Until this module, **nothing executed those claims.**
The matrix's own header states the principle it was itself exempt from:

    A green test suite proves that the tests pass. It does not prove that they
    would fail if the thing they test were broken.

How a sabotage is delivered
---------------------------
``PV_SABOTAGE=<dotted.symbol>`` is read once at import by the owning module,
which replaces the named attribute **on the module object** with an identity
function (``install_sabotage``). Callers must reach the symbol through its
module, never through a ``from``-import -- a ``from``-import copies the
reference before the rebind and the sabotage silently never arrives. Each entry
has an AST test asserting that wiring; this runner assumes it.

Reading pytest's exit code honestly
-----------------------------------
The one thing this module must not do is treat "did not pass" as "was caught".
Three non-zero exits mean nothing of the kind, and one of them -- **exit 5, no
tests collected** -- is the trap. A matrix entry whose ``tests:`` path is a typo
or points at a moved directory collects zero tests and exits non-zero. Read as
"red", that reports a passing sabotage for a selection that executed nothing.

That is ``D-00-005`` exactly: ``CANNOT RUN`` recorded as a result. Here it would
be worse than the original, because the result recorded is a *pass*.

Usage
-----
    python -m tools.sabotage_run                 # every entry
    python -m tools.sabotage_run --symbol money.outstanding
    python -m tools.sabotage_run --list

Exit ``0`` every sabotage was caught; ``1`` at least one survived or could not
be run.
"""

from __future__ import annotations

import argparse
import enum
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

MATRIX = _REPO_ROOT / "tests" / "sabotage_matrix.yaml"

#: How long one selection may run before it is abandoned. A hung selection is
#: CANNOT RUN, never CAUGHT -- a timeout kills the process with a non-zero
#: status and would otherwise be indistinguishable from a caught sabotage.
TIMEOUT_SECONDS = 900


class Outcome(enum.Enum):
    """What one sabotage run proved.

    ``ok`` is deliberately true for exactly one member. Any future member
    defaults to not-ok, so adding one cannot quietly widen what counts as
    success.
    """

    CAUGHT = ("CAUGHT", True)
    SURVIVED = ("SURVIVED", False)
    CANNOT_RUN = ("CANNOT RUN", False)

    def __init__(self, label: str, ok: bool) -> None:
        self.label = label
        self.ok = ok


#: pytest's terminal summary, e.g. "2 passed, 4 skipped in 5.12s".
_SKIPPED = re.compile(r"(\d+)\s+skipped")
_SUMMARY = re.compile(r"\d+\s+(passed|failed|error|skipped|deselected|xfailed|xpassed)")


def skipped_count(output: str) -> int | None:
    """How many tests pytest reported skipping, or ``None`` if unknown.

    ``None`` rather than ``0`` when no summary line is present. A run that
    crashed before printing one tells us nothing about skips, and "unknown" is
    not "none" -- defaulting to zero would re-open the exact hole this closes.
    """
    if not _SUMMARY.search(output):
        return None
    match = _SKIPPED.search(output)
    return int(match.group(1)) if match else 0


def classify(exit_code: int, *, skipped: int | None) -> Outcome:
    """Map a pytest exit code to what it actually proved.

    ``0`` -- everything passed with the symbol neutered. The tests do not
    depend on the behaviour they claim to test. This is the gate failure the
    matrix exists to find.

    ``1`` -- tests failed. The neutering was caught. The only pass.

    Everything else proved nothing:

    * ``2`` collection or internal error -- a full disk produced this here once,
      via a ``MemoryError`` during collection, and it reads exactly like a
      failing test;
    * ``3`` internal error, ``4`` usage error -- the selection is malformed;
    * ``5`` **no tests collected** -- an empty selection fails nothing, and
      calling that "red" would report a proof for a run that executed nothing.

    An unrecognised code is CANNOT RUN rather than CAUGHT, so a future pytest
    exit status cannot default into counting as success.
    """
    if exit_code == 1:
        # Something failed, so something discriminated. Skips do not weaken it.
        return Outcome.CAUGHT
    if exit_code == 0:
        # Exit 0 means one of two OPPOSITE things, and only the skip count
        # separates them: every test ran and missed the sabotage (SURVIVED), or
        # the discriminating tests never executed (CANNOT RUN). This runner
        # reported SURVIVED for `retraction_filter` on its first run and was
        # wrong -- the selection was `2 passed, 4 skipped` and the 4 skipped
        # were the ones that touch the filter.
        if skipped is None or skipped > 0:
            return Outcome.CANNOT_RUN
        return Outcome.SURVIVED
    return Outcome.CANNOT_RUN


@dataclass(frozen=True)
class Entry:
    symbol: str
    tests: str


@dataclass(frozen=True)
class Result:
    entry: Entry
    outcome: Outcome
    exit_code: int
    seconds: float
    skipped: int | None


def load_matrix(path: Path = MATRIX) -> tuple[Entry, ...]:
    """Read the matrix without taking a YAML dependency.

    The file is a flat list of ``- symbol:`` / ``tests:`` pairs and the parser
    stays deliberately small; ``sabotage_guard.py`` is the authority on the
    file's integrity, and duplicating a full YAML reader here would be a second
    opinion about what the matrix says.
    """
    entries: list[Entry] = []
    symbol: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- symbol:"):
            symbol = line.split(":", 1)[1].strip()
        elif line.startswith("tests:") and symbol is not None:
            entries.append(Entry(symbol=symbol, tests=line.split(":", 1)[1].strip()))
            symbol = None
    return tuple(entries)


def run_one(entry: Entry, *, verbose: bool = False) -> Result:
    """Neuter *entry.symbol* and run its selection in a fresh interpreter.

    A subprocess is required, not a convenience: the sabotage is installed at
    **import time** from the environment, so a symbol already imported into this
    process could never be neutered.
    """
    env = dict(os.environ)
    env["PV_SABOTAGE"] = entry.symbol
    # A sabotage run is expected to fail; -p no:randomly and -x keep it quick
    # and deterministic without changing what is proved.
    command = [sys.executable, "-m", "pytest", entry.tests, "-x", "-q", "--no-header"]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        code = completed.returncode
        output = completed.stdout + completed.stderr
    except subprocess.TimeoutExpired:
        # Not CAUGHT. A hung selection exits non-zero and would otherwise be
        # indistinguishable from a sabotage that was detected.
        return Result(entry, Outcome.CANNOT_RUN, -1, time.monotonic() - started, None)

    if verbose:
        print(output[-2000:])
    skipped = skipped_count(output)
    return Result(entry, classify(code, skipped=skipped), code, time.monotonic() - started, skipped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="run only the entry for this symbol")
    parser.add_argument("--list", action="store_true", help="print the matrix and exit")
    parser.add_argument("--verbose", action="store_true", help="show pytest output")
    args = parser.parse_args(argv)

    entries = load_matrix()
    if not entries:
        print("CANNOT RUN: the matrix parsed to zero entries.", file=sys.stderr)
        return 1

    if args.list:
        for entry in entries:
            print(f"  {entry.symbol}\n      {entry.tests}")
        print(f"\n{len(entries)} entries")
        return 0

    if args.symbol:
        entries = tuple(e for e in entries if args.symbol in e.symbol)
        if not entries:
            print(f"CANNOT RUN: no matrix entry matches {args.symbol!r}.", file=sys.stderr)
            return 1

    print(f"running {len(entries)} sabotages (a GREEN selection is a gate FAILURE)\n")
    results: list[Result] = []
    for index, entry in enumerate(entries, start=1):
        print(f"  [{index}/{len(entries)}] {entry.symbol} ... ", end="", flush=True)
        result = run_one(entry, verbose=args.verbose)
        results.append(result)
        detail = f"{result.seconds:.0f}s, pytest exit {result.exit_code}"
        if result.skipped:
            detail += f", {result.skipped} skipped"
        print(f"{result.outcome.label}  ({detail})")

    caught = [r for r in results if r.outcome is Outcome.CAUGHT]
    survived = [r for r in results if r.outcome is Outcome.SURVIVED]
    unrunnable = [r for r in results if r.outcome is Outcome.CANNOT_RUN]

    print(f"\ncaught {len(caught)}  survived {len(survived)}  cannot-run {len(unrunnable)}")

    if survived:
        print("\nSURVIVED -- these tests pass with the symbol neutered, so they do")
        print("not depend on the behaviour they claim to test:")
        for result in survived:
            print(f"    {result.entry.symbol}\n        {result.entry.tests}")

    if unrunnable:
        print("\nCANNOT RUN -- nothing was proved either way. This is NOT a pass")
        print("and must not be recorded as one (exit 5 means the selection")
        print("collected no tests at all):")
        for result in unrunnable:
            why = f"pytest exit {result.exit_code}"
            if result.skipped:
                why += f"; {result.skipped} tests SKIPPED, so the discriminating ones may never have run"
            print(f"    {result.entry.symbol}  ({why})")
            print(f"        {result.entry.tests}")

    return 0 if all(r.outcome.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
