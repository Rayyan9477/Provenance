#!/usr/bin/env python3
"""The close-proof: prove a defect's test would have caught it.

Authority: `EXECUTION/72_DEFECT_PROTOCOL.md` section 7.4 -

    > **A defect may be closed only by a test that would have caught it.**

    Not "a test that passes now". A test that passes now is compatible with a fix
    that changed nothing and a test that asserts nothing. The distinguishing
    question is counterfactual, and it is mechanically answerable.

and section 11.2: "The section 7.4 counterfactual: revert the non-test hunks of
the fix, run the named test, require a non-zero exit."

This is AG-1 detector 1 (section 10.1), and section 2 explains why it exists in
this shape: in a solo agent-driven build the hunter, the fixer and the reviewer
collapse onto one operator, and the protocol survives that collapse "only because
none of the three conditions for closing a defect is a judgement". This tool
produces one of the three. The exit code is the reviewer.

USAGE
-----
    python -m tools.close_proof D-04-002        # the section 7.4 counterfactual
    python -m tools.close_proof --all           # every CLOSED row names a real assertion
    python -m tools.close_proof D-04-002 --check-only   # skip the revert, check the name

Output is the line that goes verbatim into the record's `Close-proof` field:

    close-proof D-04-002: test FAILED with fix reverted (exit=1) - PASS
    close-proof D-04-002: FAIL: the named test passes without the fix; it does not catch this defect

TWO IMPLEMENTATION NOTES, BOTH DELIBERATE
-----------------------------------------
**`git revert` takes no pathspec.** Section 7.4 step 2 writes
`git revert -n <sha> -- <non-test paths>`, and git has no such form: `revert`
reverts whole commits. The operation section 7.4 *describes* - restore the
pre-fix content of the non-test files, keep the new test - is exactly
`git checkout <sha>^ -- <non-test paths>` inside a scratch worktree, and that is
what runs here. Same effect, real command. This divergence is a MAJOR-M3
documentation defect against section 7.4 by the letter of the severity rule and
should be filed as one.

**`hooks.py` counts as non-test.** Section R4: a fix that changes a shipped test
seam has hunks that are neither cleanly test nor cleanly production, and
`provenance_db.hooks` is shipped code existing solely so tests can force an
interleaving. The decision recorded there is that it is classified **non-test**,
so reverting it is part of reverting the fix - the conservative direction, which
makes the close-proof harder to pass rather than easier. `NON_TEST_OVERRIDES`
below is that classification list, and section R4 notes that any addition to it
is an AG-1 surface needing the same trailer treatment.

**Nothing here touches the working tree.** All work happens in a detached
worktree under a temporary directory, which is removed afterwards. A tool that
reverted hunks in place could leave a repository looking fixed when it is not.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from tools.defect_lint import (
    DEFECTS_MD,
    FULL_SHA,
    GATE_ID,
    REPO_ROOT,
    Record,
    _clean,
    _split_severity,
    load_records,
)

# Section R4. Paths that look like test infrastructure but are shipped code, and
# are therefore reverted along with the rest of the fix.
NON_TEST_OVERRIDES: tuple[str, ...] = ("packages/python/provenance_db/src/provenance_db/hooks.py",)

_TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|[^/]*_test\.py$|\.spec\.[tj]sx?$")
_PYTEST_NODE = re.compile(r"^(?P<file>[^\s:]+\.py)::(?P<rest>[^\s]+)$")
_GREP_ASSERTION = re.compile(r"^\s*(rg|grep)\b")


def is_test_path(path: str) -> bool:
    """Section 7.4 step 2's split, with the section R4 override applied first."""
    normalised = path.replace("\\", "/")
    if normalised in NON_TEST_OVERRIDES:
        return False
    return bool(_TEST_PATH.search(normalised))


def _git(args: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def _assertion_parts(record: Record) -> list[str]:
    return [part.strip() for part in record.get("verifying assertion").split("+") if part.strip()]


def assertion_exists(part: str) -> tuple[bool, str]:
    """Can the thing the record names actually be run?

    Three shapes are accepted, and each is checked rather than assumed:

    * a gate id - it must appear in `quality/23_PHASE_GATES.md`, so a record
      cannot close against an assertion nobody wrote;
    * a pytest node id - the file must exist and pytest must collect the node;
    * a `grep`/`rg` command - the section R8 provisional path for a defect whose
      owning file is a document, where there is no test to revert a fix against.
    """
    text = _clean(part)
    if GATE_ID.match(text):
        gates = REPO_ROOT / "docs" / "quality" / "23_PHASE_GATES.md"
        if not gates.exists():
            return False, f"{text}: cannot verify, {gates} is missing"
        if re.search(rf"\b{re.escape(text)}\b", gates.read_text(encoding="utf-8")):
            return True, f"{text}: gate id found in 23_PHASE_GATES.md"
        return False, f"{text}: no such gate id in 23_PHASE_GATES.md"

    if _GREP_ASSERTION.match(text):
        return True, f"{text}: section R8 documentation-defect form (grep returning zero hits)"

    node = _PYTEST_NODE.match(text)
    if node:
        path = REPO_ROOT / node.group("file")
        if not path.exists():
            return False, f"{text}: {node.group('file')} does not exist"
        collected = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", text],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if collected.returncode == 0:
            return True, f"{text}: collected by pytest"
        return False, f"{text}: pytest could not collect it ({collected.returncode})"

    return False, (
        f"{text!r} is not a runnable assertion. Section 5.2: a gate id (G4.2), a pytest "
        "node id (path::test_name), or both joined by ' + '. Never 'manual check', never "
        "'reviewed'."
    )


def check_all(records: Sequence[Record], out) -> int:
    """Every CLOSED row must name a verifying assertion that exists."""
    closed = [r for r in records if r.status == "CLOSED"]
    if not closed:
        out.write("0 CLOSED records in ops/defects/DEFECTS.md - nothing to prove\n")
        return 0

    failures = 0
    out.write(f"{len(closed)} CLOSED record(s)\n\n")
    for record in closed:
        parts = _assertion_parts(record)
        if not parts:
            out.write(
                f"  {record.defect_id}: FAIL - CLOSED with no Verifying assertion. "
                "Section 7.4: no close-proof, no CLOSED.\n"
            )
            failures += 1
            continue
        for part in parts:
            ok, detail = assertion_exists(part)
            out.write(f"  {record.defect_id}: {'ok  ' if ok else 'FAIL'} {detail}\n")
            if not ok:
                failures += 1
        proof = record.get("close-proof")
        if not proof:
            out.write(
                f"  {record.defect_id}: FAIL - no Close-proof line in the detail block. "
                f"Run `python -m tools.close_proof {record.defect_id}`.\n"
            )
            failures += 1
        elif "PASS" not in proof.upper():
            out.write(f"  {record.defect_id}: FAIL - close-proof does not read PASS: {proof}\n")
            failures += 1
    out.write(f"\nclose_proof --all: {failures} failure(s)\n")
    return 1 if failures else 0


def counterfactual(record: Record, out) -> int:
    """Section 7.4 steps 1-5."""
    did = record.defect_id

    sha = _clean(record.get("fix commit"))
    if not FULL_SHA.match(sha):
        out.write(
            f"close-proof {did}: FAIL: no 40-character fix commit on the record. "
            "There is nothing to revert.\n"
        )
        return 2
    if _git(["cat-file", "-e", f"{sha}^{{commit}}"]).returncode != 0:
        out.write(f"close-proof {did}: FAIL: commit {sha} is not in this repository.\n")
        return 2

    nodes = [p for p in _assertion_parts(record) if _PYTEST_NODE.match(_clean(p))]
    if not nodes:
        assertion = record.get("verifying assertion") or "<empty>"
        out.write(
            f"close-proof {did}: NOT APPLICABLE: the Verifying assertion is {assertion!r} and "
            "names no pytest node id, so there is no test to run with the fix reverted.\n"
            "Section R8: a defect whose owning file is a document closes on the change-control "
            "rule in README.md instead - the decision register, the owning specification, the "
            "dependent contracts and examples, and the migration note updated in one change - "
            "and its Verifying assertion carries the rg/grep command that returns zero hits for "
            "the old spelling. That is weaker than a close-proof and it is the strongest thing "
            "available for prose. Say so in the record rather than claiming a proof.\n"
        )
        return 2

    changed = [
        p
        for p in _git(["show", "--pretty=format:", "--name-only", sha]).stdout.splitlines()
        if p.strip()
    ]
    non_test = [p for p in changed if not is_test_path(p)]
    test_only = [p for p in changed if is_test_path(p)]

    out.write(f"close-proof {did}\n")
    out.write(f"  fix commit      {sha}\n")
    out.write(f"  test paths      {len(test_only)}: {', '.join(test_only) or '<none>'}\n")
    out.write(f"  non-test paths  {len(non_test)}: {', '.join(non_test) or '<none>'}\n")
    out.write(f"  assertion       {', '.join(nodes)}\n\n")

    if not non_test:
        rule = _split_severity(record.get("sev"))[1]
        if rule != "M2":
            out.write(
                f"close-proof {did}: FAIL: the fix touches only test files and this record's "
                f"rule id is {rule or '<none>'}, not M2. Section 7.2: a defect closed by a "
                "change confined to test files is either a MAJOR-M2 vacuity defect, where the "
                "test IS the owning file, or an instance of AG-1. There is no third case, and "
                "there is nothing to revert here.\n"
            )
            return 1
        out.write(
            f"close-proof {did}: NOT APPLICABLE: an M2 vacuity defect's fix is the test itself, "
            "so reverting the non-test hunks reverts nothing. Prove this one the other way: "
            "show the sabotage that used to stay green now goes red (the G6.7-shaped assertion "
            "in section 4.5 M-ex-2), and record that transcript in the Close-proof field.\n"
        )
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="pv-close-proof-"))
    worktree = scratch / "wt"
    try:
        added = _git(["worktree", "add", "--detach", str(worktree), sha])
        if added.returncode != 0:
            out.write(f"close-proof {did}: FAIL: could not create a worktree: {added.stderr}\n")
            return 2

        # Section 7.4 step 2. `git revert` takes no pathspec; restoring the
        # parent's blobs for the non-test paths is the same operation, spelled in
        # a command that exists. See the module docstring.
        restored = _git(["checkout", f"{sha}^", "--", *non_test], cwd=worktree)
        if restored.returncode != 0:
            out.write(
                f"close-proof {did}: FAIL: could not revert the non-test hunks: "
                f"{restored.stderr}\n"
            )
            return 2
        out.write(f"  reverted        git checkout {sha[:12]}^ -- {' '.join(non_test)}\n")

        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *nodes],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
        )
        out.write("\n--- pytest, fix reverted, new test in place -----------------------------\n")
        out.write(run.stdout)
        if run.stderr.strip():
            out.write(run.stderr)
        out.write("------------------------------------------------------------------------\n\n")

        # Step 4: the run must exit non-zero. A green run means the named test
        # passes without the fix, so it does not distinguish the broken system
        # from the working one.
        if run.returncode != 0:
            out.write(
                f"close-proof {did}: test FAILED with fix reverted (exit={run.returncode}) - PASS\n"
            )
            out.write(
                "\nPaste that line verbatim into the record's Close-proof field. It is one of "
                "the three machine conditions section 7 requires, and none of them is a "
                "judgement.\n"
            )
            return 0

        out.write(
            f"close-proof {did}: FAIL: the named test passes without the fix; it does not "
            "catch this defect\n"
        )
        out.write(
            "\nSection 10.1: this is what a weakened assertion looks like from the outside - "
            "an exact reason_code become `is not None`, or an id membership check become "
            "`len(results) > 0`. The status stays AWAITING_REVERIFY, which blocks the gate "
            "exactly as OPEN does.\n"
        )
        return 1
    finally:
        _git(["worktree", "remove", "--force", str(worktree)])
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.close_proof",
        description=(
            "The EXECUTION/72_DEFECT_PROTOCOL.md section 7.4 counterfactual: revert the "
            "non-test hunks of a fix, run the named test, require a non-zero exit."
        ),
    )
    parser.add_argument("defect_id", nargs="?", help="e.g. D-04-002")
    parser.add_argument(
        "--all",
        action="store_true",
        help="check that every CLOSED row names a verifying assertion that exists",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="check that the named assertion exists; do not revert or run anything",
    )
    parser.add_argument("--defects-file", type=Path, default=DEFECTS_MD)
    args = parser.parse_args(argv)

    out = sys.stdout
    records, load_violations = load_records(args.defects_file)
    for violation in load_violations:
        out.write(f"{violation}\n")

    if args.all:
        return check_all(records, out)

    if not args.defect_id:
        parser.print_usage(out)
        out.write("\ngive a defect id, or --all\n")
        return 2

    record = next((r for r in records if r.defect_id == args.defect_id), None)
    if record is None:
        out.write(f"{args.defect_id} is not in {args.defects_file}\n")
        return 2

    if args.check_only:
        parts = _assertion_parts(record)
        if not parts:
            out.write(f"close-proof {record.defect_id}: FAIL: no Verifying assertion\n")
            return 1
        failures = 0
        for part in parts:
            ok, detail = assertion_exists(part)
            out.write(f"  {'ok  ' if ok else 'FAIL'} {detail}\n")
            failures += 0 if ok else 1
        return 1 if failures else 0

    if record.status != "CLOSED":
        out.write(
            f"note: {record.defect_id} is {record.get('status')!r}, not CLOSED. Running the "
            "counterfactual anyway - section 7.4 is what MOVES a record to CLOSED, so running "
            "it before the status changes is the normal order.\n\n"
        )
    return counterfactual(record, out)


if __name__ == "__main__":
    raise SystemExit(main())
