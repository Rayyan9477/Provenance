#!/usr/bin/env python3
"""Keep the sabotage matrix append-only, and make its size a number to compare.

Authority: `EXECUTION/72_DEFECT_PROTOCOL.md` section 10.2, AG-2 -

    **The failure mode.** `make sabotage` reports `sabotages: 18 | detected: 17 |
    UNDETECTED: 1`. The honest fix is to write the missing assertion -
    `23_PHASE_GATES.md` section 23.5 says so in four words: "Fix the test, not the
    matrix." The cheap fix is to delete the entry from `tests/sabotage_matrix.yaml`,
    after which `make sabotage` prints `sabotages: 17 | detected: 17 | UNDETECTED: 0`
    and `G14.6` **passes**. The gate assertion as written cannot tell the
    difference, because it asserts a ratio.

and section 11.2: "The AG-2 detectors: append-only matrix diff,
`--assert-symbol-gone`, `--min-count`."

USAGE
-----
    python -m tools.sabotage_guard --count
    python -m tools.sabotage_guard --min-count 18
    python -m tools.sabotage_guard --base "$(git merge-base HEAD main)"
    python -m tools.sabotage_guard --assert-symbol-gone provenance_domain.money.outstanding

WHY TWO DETECTORS
-----------------
Detector 1 (`--base`) catches **deletion**. Detector 2 (`--min-count`) catches the
subtler version: never adding the entry in the first place, so there is nothing to
delete. `23_PHASE_GATES.md` section 25 risk 10 states plainly that `make sabotage`
"reports on what is listed"; the count comparison is the only thing that notices
what is not.

A legitimate removal exists - the symbol was deleted from the codebase - and it is
handled mechanically rather than by permission: the removal is accepted only when
the named symbol is **also absent from the tree** at that commit, and the commit
carries a `Sabotage-Entry-Removal:` trailer naming the reason.

THE MATRIX FILE
---------------
`tests/sabotage_matrix.yaml` is created and completed by `T14.5`, which reconciles
it to 18 entries; `T1.4` and `T4.1` register the first entries. It does not exist
in Phase 0, and this tool reports that as a fact rather than as a violation - a
guard that failed on a file its own phase has not created yet would be noise from
the first commit. `--min-count` still fails against a missing file, because
"the file is gone" and "the entries are gone" are the same event.

Three shapes are read, because the pack does not fix one:

    - symbol: provenance_domain.money.outstanding      # list of mappings
      tests: packages/python/provenance_domain/tests

    sabotages:                                          # under a top-level key
      - symbol: ...
        tests: ...

    provenance_domain.money.outstanding: packages/...   # plain mapping

The selection key may be `tests`, `selection`, `expect_fail` or `expected_failures`.
`23_PHASE_GATES.md` section 23.5 fixes only the semantics - "maps symbol -> test
selection that must fail when that symbol is neutered" - so the reader is liberal
and the guard is strict.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - pyyaml is a declared dev dependency
    sys.stderr.write(
        "tools/sabotage_guard.py needs pyyaml (declared in pyproject.toml [dev]). "
        "Run `make bootstrap`.\n"
    )
    raise SystemExit(70) from None

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX = REPO_ROOT / "tests" / "sabotage_matrix.yaml"

SELECTION_KEYS = ("tests", "selection", "expect_fail", "expected_failures", "must_fail")
REMOVAL_TRAILER = "Sabotage-Entry-Removal:"

# Where a sabotage symbol could plausibly live. Used by --assert-symbol-gone.
SEARCH_ROOTS = ("packages", "services", "agents", "workers", "scripts", "tools", "evals")


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, check=False
    )


def parse_matrix(text: str) -> dict[str, str]:
    """Return {symbol: test selection}. Raises ValueError on an unreadable shape."""
    data = yaml.safe_load(text) if text.strip() else None
    if data is None:
        return {}

    entries: object = data
    if isinstance(data, dict):
        for key in ("sabotages", "entries", "matrix"):
            if key in data:
                entries = data[key]
                break
        else:
            # Plain mapping form: symbol -> selection.
            if all(isinstance(v, str) for v in data.values()):
                return {str(k): str(v) for k, v in data.items()}
            entries = list(data.values())

    if not isinstance(entries, list):
        raise ValueError(
            "tests/sabotage_matrix.yaml is neither a list of entries nor a "
            "symbol -> selection mapping"
        )

    result: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError(f"matrix entry is not a mapping: {item!r}")
        symbol = item.get("symbol") or item.get("target") or item.get("name")
        if not symbol:
            raise ValueError(f"matrix entry has no `symbol:` key: {item!r}")
        selection = ""
        for key in SELECTION_KEYS:
            if item.get(key):
                selection = str(item[key])
                break
        result[str(symbol)] = selection
    return result


def load_matrix(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_matrix(path.read_text(encoding="utf-8"))


def load_matrix_at(ref: str) -> dict[str, str] | None:
    rel = MATRIX.relative_to(REPO_ROOT).as_posix()
    shown = _git(["show", f"{ref}:{rel}"])
    if shown.returncode != 0:
        return None
    return parse_matrix(shown.stdout)


def symbol_is_gone(symbol: str) -> tuple[bool, str]:
    """Is the neutered symbol actually absent from the tree?

    A sabotage symbol is a dotted path to a function - `PV_SABOTAGE` names it and
    the hook neuters it - so the last segment is the function name and the rest
    is the module. Absence is decided on the **whole dotted path** appearing
    nowhere and on the final identifier not being defined anywhere, because a
    function that was merely moved is not gone, and moving an invariant-bearing
    function out from under its matrix entry is precisely the shape AG-2 exists
    to catch.
    """
    leaf = symbol.rsplit(".", 1)[-1]
    roots = [str(REPO_ROOT / root) for root in SEARCH_ROOTS if (REPO_ROOT / root).exists()]
    if not roots:
        return False, "no source roots exist yet; cannot establish absence"

    dotted = _git(["grep", "-l", "--fixed-strings", symbol, "--", *SEARCH_ROOTS])
    if dotted.returncode == 0 and dotted.stdout.strip():
        hits = ", ".join(dotted.stdout.split())
        return False, f"the dotted path still appears in: {hits}"

    defined = _git(
        ["grep", "-lE", rf"^\s*(def|async def|class)\s+{re.escape(leaf)}\b", "--", *SEARCH_ROOTS]
    )
    if defined.returncode == 0 and defined.stdout.strip():
        hits = ", ".join(defined.stdout.split())
        return False, (
            f"`{leaf}` is still defined in: {hits}. A symbol that moved is not a symbol "
            "that is gone; move the matrix entry with it."
        )
    return (
        True,
        f"neither `{symbol}` nor a definition of `{leaf}` appears under {', '.join(SEARCH_ROOTS)}",
    )


def check_append_only(base: str, out) -> int:
    before = load_matrix_at(base)
    after = load_matrix(MATRIX)

    if before is None:
        out.write(
            f"tests/sabotage_matrix.yaml does not exist at {base}; nothing to diff. "
            f"Current entries: {len(after)}\n"
        )
        return 0

    removed = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    out.write(f"sabotage matrix: {len(before)} at {base[:12]} -> {len(after)} now ")
    out.write(f"(+{len(added)} -{len(removed)})\n")
    for symbol in added:
        out.write(f"  added    {symbol}\n")
    if not removed:
        out.write("append-only: PASS\n")
        return 0

    message = _git(["log", "-1", "--format=%B", "HEAD"]).stdout
    has_trailer = REMOVAL_TRAILER in message
    failures = 0
    for symbol in removed:
        gone, detail = symbol_is_gone(symbol)
        out.write(f"  removed  {symbol}\n")
        out.write(f"           symbol absent from the tree: {'yes' if gone else 'NO'} - {detail}\n")
        out.write(
            f"           `{REMOVAL_TRAILER}` trailer on HEAD: {'yes' if has_trailer else 'NO'}\n"
        )
        if not (gone and has_trailer):
            failures += 1

    if failures:
        out.write(
            f"\nappend-only: FAIL ({failures} unjustified removal(s)).\n"
            "Section 10.2 AG-2: an entry may not be deleted to make the matrix pass. "
            "`make sabotage` asserts a ratio, so deleting the entry that reports UNDETECTED "
            "turns G14.6 green while the untested code path stays untested. "
            "quality/23_PHASE_GATES.md section 23.5, in four words: fix the test, not the "
            "matrix. A removal is legitimate only when the symbol is also gone from the tree "
            f"and the commit carries a `{REMOVAL_TRAILER} <reason>` trailer.\n"
        )
        return 1
    out.write("\nappend-only: PASS (every removal names a symbol that is gone, with a trailer)\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.sabotage_guard",
        description=(
            "AG-2 (EXECUTION/72_DEFECT_PROTOCOL.md section 10.2): the sabotage matrix is "
            "append-only, and its entry count only goes up."
        ),
    )
    parser.add_argument("--count", action="store_true", help="print the entry count and exit")
    parser.add_argument(
        "--min-count",
        type=int,
        default=None,
        help="fail if the matrix has fewer entries than this (the previous gate's number)",
    )
    parser.add_argument(
        "--base",
        default=None,
        help='git ref to diff against, e.g. "$(git merge-base HEAD main)"',
    )
    parser.add_argument(
        "--assert-symbol-gone",
        default=None,
        metavar="SYMBOL",
        help="check that a removed entry's symbol really is absent from the tree",
    )
    parser.add_argument("--list", action="store_true", help="print every entry")
    args = parser.parse_args(argv)

    out = sys.stdout

    if not MATRIX.exists() and not args.assert_symbol_gone:
        out.write(f"{MATRIX.relative_to(REPO_ROOT).as_posix()} does not exist yet.\n")
        out.write(
            "It is started by T1.4 (`provenance_domain.money.outstanding`), extended by T4.1 "
            "and reconciled to 18 entries by T14.5, so its absence in an early phase is the "
            "expected state and not a violation. What IS a violation is a later phase that "
            "added invariant-bearing modules and zero entries: 23_PHASE_GATES.md section 25 "
            "risk 10 - a code path with no entry is a path whose tests were never checked for "
            "vacuousness, and nothing in `make sabotage` will tell you the entry is missing.\n"
        )
        if args.min_count is not None:
            out.write(f"\nmin-count: FAIL (0 entries < required {args.min_count})\n")
            return 1
        if args.count:
            out.write("\nsabotage matrix entries: 0\n")
        return 0

    if args.assert_symbol_gone:
        gone, detail = symbol_is_gone(args.assert_symbol_gone)
        out.write(f"assert-symbol-gone {args.assert_symbol_gone}: {'PASS' if gone else 'FAIL'}\n")
        out.write(f"  {detail}\n")
        return 0 if gone else 1

    try:
        entries = load_matrix(MATRIX)
    except (ValueError, yaml.YAMLError) as exc:
        out.write(f"tests/sabotage_matrix.yaml could not be read: {exc}\n")
        return 2

    if args.list:
        for symbol, selection in sorted(entries.items()):
            out.write(f"  {symbol}\n      -> {selection or '<no selection recorded>'}\n")

    unselected = [symbol for symbol, selection in entries.items() if not selection.strip()]
    if unselected:
        out.write(
            f"\n{len(unselected)} entry(ies) name a symbol but no test selection:\n"
            + "".join(f"  {symbol}\n" for symbol in unselected)
            + "Section 23.5: an entry maps a symbol to the test selection that must FAIL when "
            "the symbol is neutered. An entry with no selection cannot detect anything and "
            "inflates the count that AG-2 detector 2 compares.\n"
        )

    status = 0
    if args.count or args.min_count is not None or not (args.base or args.list):
        out.write(f"sabotage matrix entries: {len(entries)}\n")

    if args.min_count is not None:
        if len(entries) < args.min_count:
            out.write(
                f"min-count: FAIL ({len(entries)} < required {args.min_count}).\n"
                "Section 10.2 detector 2: the reviewer's check at gate N is "
                "count(N) >= count(N-1). A matrix that shrank means an entry was removed; a "
                "matrix that stood still across a phase that added invariant-bearing modules "
                "is suspicious on its face.\n"
            )
            status = 1
        else:
            out.write(f"min-count: PASS ({len(entries)} >= {args.min_count})\n")

    if unselected:
        status = 1

    if args.base:
        status = max(status, check_append_only(args.base, out))

    return status


if __name__ == "__main__":
    raise SystemExit(main())
