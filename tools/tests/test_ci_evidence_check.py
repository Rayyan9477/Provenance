"""The verifying assertion for `D-00-011`.

`.github/workflows/ci.yml` carries an *evidence-before-assertion* step
(`quality/23_PHASE_GATES.md` section 3): a gate ledger row that reads `PASS`
must have a captured `tools/gate.sh` log behind it. The step was, until
`62e3f1c`, a filename-existence check wearing an evidence check's name. Two
holes, both reproduced below as failing cases:

  1. it never opened a log, so a `PASS` row whose only evidence recorded
     `exit=7` was accepted, and a log captured at some older commit proved a row
     at HEAD;
  2. its row regex was `^\\|\\s*(G\\d+\\.\\d+[a-z]?)\\s*\\|`, and
     `ops/gates/PHASE_15.md` states outright that there is no `G15.x` — so the
     submission gate, the one this step exists to protect, was matched by
     nothing at all.

Rather than re-implement the check, these tests **extract the step's own script
out of `ci.yml`** and run it against forged trees. A re-implementation would
test the re-implementation, and the re-implementation is not what runs in the
merge lane.

Neuter to prove it: restore either hole in `ci.yml` — drop the `exit_code != "0"`
branch, or narrow `ident` back to `G\\d+\\.\\d+[a-z]?` — and the corresponding
case below goes red.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"


def evidence_check_source() -> str:
    """Lift the heredoc the evidence step runs, dedented to column zero."""
    text = CI_YML.read_text(encoding="utf-8")
    start = text.index("evidence-before-assertion")
    body = text[start:]
    open_marker = re.search(r"(?m)^(?P<indent>\s*)python - <<'PY'\n", body)
    assert open_marker is not None, "the evidence step no longer runs a `python - <<'PY'` heredoc"
    indent = open_marker.group("indent")
    rest = body[open_marker.end() :]
    close = re.search(rf"(?m)^{indent}PY\s*$", rest)
    assert close is not None, "the evidence heredoc is not terminated"
    lines = rest[: close.start()].splitlines()
    return "\n".join(line[len(indent) :] if line.startswith(indent) else line for line in lines)


def run_check(tree: Path, head: str = HEAD_SHA) -> subprocess.CompletedProcess[str]:
    script = tree / "_evidence_check.py"
    script.write_text(evidence_check_source(), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=str(tree),
        capture_output=True,
        text=True,
        check=False,
        env={"GITHUB_SHA": head, "PATH": "", "SYSTEMROOT": ""},
    )


def make_log(tree: Path, assertion: str, sha: str, exit_code: str) -> None:
    logs = tree / "ops" / "gates" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{assertion}.{sha[:8]}.log").write_text(
        f"# gate={assertion} sha={sha} at=2026-08-18T00:00:00Z cmd=true exit={exit_code}\n"
        "captured output\n",
        encoding="utf-8",
    )


def make_ledger(tree: Path, name: str, row: str) -> None:
    gates = tree / "ops" / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / name).write_text(
        "| Assertion | Result | Evidence |\n|---|---|---|\n" + row + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# The positive control comes first: without it, every case below could be
# passing because the script errors out on something unrelated.
# ---------------------------------------------------------------------------


def test_a_pass_row_with_a_matching_log_is_accepted(tmp_path: Path) -> None:
    make_ledger(tmp_path, "PHASE_00.md", "| G0.1 | PASS | ops/gates/logs/G0.1.01234567.log |")
    make_log(tmp_path, "G0.1", HEAD_SHA, "0")
    result = run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_pass_row_whose_log_records_a_failure_is_rejected(tmp_path: Path) -> None:
    """Hole 1: the step tested that a filename existed, never that the run
    inside it had succeeded."""
    make_ledger(tmp_path, "PHASE_00.md", "| G0.1 | PASS | ops/gates/logs/G0.1.01234567.log |")
    make_log(tmp_path, "G0.1", HEAD_SHA, "7")
    result = run_check(tmp_path)
    assert result.returncode != 0, (
        "a PASS row whose captured log records exit=7 was accepted:\n" + result.stdout
    )
    assert "exit=7" in result.stderr, result.stderr


def test_a_pass_row_proved_at_another_commit_is_rejected(tmp_path: Path) -> None:
    """The sha check is what stops a stale green from surviving a red re-run:
    `tools/gate.sh` names logs `<ID>.<sha8>.log`, so a re-run at the same commit
    overwrites in place and a log from an older commit is a different claim."""
    make_ledger(tmp_path, "PHASE_00.md", "| G0.1 | PASS | ops/gates/logs/G0.1.fedcba98.log |")
    make_log(tmp_path, "G0.1", OTHER_SHA, "0")
    result = run_check(tmp_path)
    assert result.returncode != 0, (
        "a PASS row proved by a log captured at a different commit was accepted:\n" + result.stdout
    )
    assert "not at the commit under test" in result.stderr, result.stderr


def test_a_forged_release_row_is_rejected(tmp_path: Path) -> None:
    """Hole 2: the `S1`..`S10` family was matched by nothing, so the
    release-readiness battery — the assertions T0.7 sub-task 4 exists to
    protect — was entirely unguarded."""
    make_ledger(tmp_path, "PHASE_15.md", "| S3 | PASS | (no log) |")
    result = run_check(tmp_path)
    assert result.returncode != 0, (
        "a forged `| S3 | PASS |` row was not detected. ops/gates/PHASE_15.md "
        "states there is no G15.x, so a G-only row regex leaves the G-15 "
        "gate unguarded:\n" + result.stdout
    )
    assert "S3" in result.stderr, result.stderr


def test_a_forged_s_row_in_any_phase_ledger_is_rejected(tmp_path: Path) -> None:
    """The `S<n>` family is scanned in every `PHASE_*.md` ledger; before the
    fix it was matched by nothing at all."""
    make_ledger(tmp_path, "PHASE_14.md", "| S8 | PASS | (no log) |")
    result = run_check(tmp_path)
    assert result.returncode != 0, (
        "a forged S-row in ops/gates/PHASE_14.md was not detected:\n" + result.stdout
    )
    assert "S8" in result.stderr, result.stderr
