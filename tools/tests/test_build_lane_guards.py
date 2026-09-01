"""Regression tests for the Phase 0 build-lane fixes.

Every test here exists because a defect record needs a *runnable* verifying
assertion (`EXECUTION/72_DEFECT_PROTOCOL.md` section 5.2) and had none. The
records these close were fixed in commit `62e3f1c`, which is the commit that
introduced the whole build lane — there is no pre-fix parent to revert, so
`tools/close_proof.py`'s mechanical counterfactual cannot run against them
(section 7.4 assumes a fix commit with a parent that holds the broken state).

The counterfactual is therefore performed by hand and recorded in each record's
`Close-proof` field: the mechanism is neutered in a scratch copy of the tree,
the named test is run there, and a non-zero exit is required. Each test below
names the record it proves and the exact neutering that turns it red, so the
next reader can repeat the proof rather than trust this docstring.

Several of these are assertions about a *configuration file* rather than about
running code. That is the same epistemic strength as section R8's provisional
path for a documentation defect — "the `rg`/`grep` command that returns zero
hits for the old spelling" — promoted into pytest so that it runs in the unit
lane instead of being remembered. Where a functional assertion was available
(`tools/gate.sh`, `python -m tools.defect_lint --debt`, the real `gitleaks`
binary) the functional one is used instead, because a text assertion cannot
tell a working mechanism from a well-spelled one.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tools.tests.test_gitleaks_config import (
    reported,
    requires_gitleaks,
    scan,
    write,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
GITLEAKS_TOML = REPO_ROOT / ".gitleaks.toml"
RUNBOOK = REPO_ROOT / "docs" / "ops" / "41_RUNBOOK.md"

BASH = shutil.which("bash")

# A silent skip is the vacuity failure this project files defects about, so the
# reason names what is missing and why the assertion matters.
requires_bash = pytest.mark.skipif(
    BASH is None,
    reason=(
        "`bash` is not on PATH. tools/gate.sh is a bash script and every gate "
        "assertion is captured through it; without bash this assertion is "
        "unproven, not satisfied."
    ),
)


def makefile_recipe(target: str) -> str:
    """Return the recipe body of one Makefile target, comments included.

    Comment lines between recipe lines belong to the recipe for our purposes:
    the reasons a Makefile records beside a fix are part of what must not be
    deleted silently.
    """
    text = MAKEFILE.read_text(encoding="utf-8")
    start = re.search(rf"(?m)^{re.escape(target)}:", text)
    assert start is not None, f"no target {target!r} in {MAKEFILE}"
    rest = text[start.end() :]
    end = re.search(r"(?m)^[a-zA-Z0-9_.-]+:", rest)
    return rest[: end.start()] if end else rest


# ---------------------------------------------------------------------------
# D-00-008 — the formatter pin
# ---------------------------------------------------------------------------


def test_ruff_is_pinned_to_an_exact_version() -> None:
    """`ruff format --check .` must return the same verdict on every machine.

    D-00-008: with `ruff>=0.6,<1` the merge-blocking format check returned
    `rc=0` on 0.6.9 and `rc=1` on 0.16.3, so the lane's verdict depended on the
    day the runner resolved its dependencies.

    Neuter to prove it: restore the range spec in `pyproject.toml` and this test
    goes red.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dev = data["project"]["optional-dependencies"]["dev"]
    specs = [s for s in dev if s.replace("_", "-").startswith("ruff")]
    assert specs, f"ruff is not in the dev extra at all: {dev}"
    pinned = [s for s in specs if re.fullmatch(r"ruff==\d+\.\d+(\.\d+)?", s.strip())]
    assert pinned == specs, (
        f"ruff must be pinned exactly, not ranged: {specs}. A formatter's output "
        "moves between minor versions, and `ruff format --check .` is a step in "
        "`make lint` and inside G0.4's clean-clone proof."
    )

    want = pinned[0].split("==", 1)[1]
    got = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert want in got, (
        f"pyproject.toml pins ruff=={want} but the installed ruff reports {got.strip()!r}. "
        "The pin is only a guarantee while the environment honours it."
    )


# ---------------------------------------------------------------------------
# D-00-014 — the tools tree is collected
# ---------------------------------------------------------------------------


def test_testpaths_collects_the_tools_tree() -> None:
    """D-00-014: `testpaths` omitted `tools`, so 28 declared tests never ran.

    Neuter to prove it: drop `"tools"` from `testpaths` and this test goes red.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    testpaths = data["tool"]["pytest"]["ini_options"]["testpaths"]
    assert "tools" in testpaths, (
        f"testpaths is {testpaths}; `tools` is missing, so tools/tests/ is "
        "collected by no lane — not `make test`, not `make test-fast`, not CI. "
        "Twenty-eight tests that never run look identical to no tests."
    )
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tools/tests/test_scrub.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr


# ---------------------------------------------------------------------------
# D-00-007 — no silent no-op fallback in `make lint`
# ---------------------------------------------------------------------------


def test_make_lint_refuses_the_import_linter_module_fallback() -> None:
    """D-00-007: `python -m importlinter.cli` exits 0 having run no contracts.

    `importlinter/cli.py` declares its click commands at module scope, has no
    `__main__` guard and ships no `__main__.py`, so running it as a module
    evaluates zero contracts and returns 0. That was the branch taken on every
    machine without the console script, and E1 kernel purity was decoration on
    it.

    Neuter to prove it: add `$(PY) -m importlinter.cli lint-imports` back to the
    `lint` recipe as a fallback and this test goes red.
    """
    recipe = makefile_recipe("lint")
    # `printf` lines are allowed to *name* the fallback: the recipe explains why
    # it refuses to use one. What must not exist is an invocation.
    live = [
        line
        for line in recipe.splitlines()
        if "-m importlinter.cli" in line
        and not line.lstrip().startswith("#")
        and "printf" not in line
    ]
    assert not live, (
        "`python -m importlinter.cli` appears as a live command in the lint "
        f"recipe: {live}. It exits 0 having evaluated zero contracts."
    )
    assert "command -v lint-imports" in recipe, (
        "`make lint` must probe for the lint-imports console script. A missing "
        "tool that produces a green log is worse than no tool."
    )
    assert (
        "lint-imports" in recipe
    ), "the lint recipe must actually invoke the `lint-imports` console script"

    # The assertion that actually matters, and it is stronger than any check on
    # the invocation's shape.
    #
    # This originally required `lint-imports` on a bare recipe line. That shape
    # broke for a reason worth keeping: on this build machine the console script
    # is PRESENT but NOT EXECUTABLE from Git Bash -- the anaconda Scripts shim
    # gives "Permission denied", exit 126 -- so `command -v` found it, the bare
    # line ran it, and `make lint` died at exit 126. The recipe now tries the
    # console script, falls back to importing the click command and calling it
    # (which does evaluate every contract, unlike the `-m` form), and then GREPS
    # THE OUTPUT for the summary line.
    #
    # That grep is what makes a vacuous run fail. Any invocation may be swapped
    # in; none may report success without producing "Contracts: N kept".
    # Checked against LIVE lines only. An earlier version of this assertion read
    # `"Contracts:" in recipe` and matched the explanatory comment above the
    # check rather than the check itself -- so deleting the grep left the test
    # green. That is the same prose-versus-code confusion as the substring guard
    # in test_invariants.py, reintroduced within the hour, which is a fair
    # measure of how easy it is: a comment that describes a mechanism reads
    # exactly like the mechanism to a substring search.
    live_lines = [ln for ln in recipe.splitlines() if not ln.lstrip().startswith("#")]
    verifies = [ln for ln in live_lines if "Contracts:" in ln]
    assert verifies, (
        "the lint recipe must verify that import-linter actually EVALUATED "
        "contracts, not merely that it exited 0 -- and the check must be a live "
        "command, not a comment describing one. A run that evaluates zero "
        "contracts and returns 0 is the whole of D-00-007, and no assertion "
        "about how the invocation is spelled can catch it."
    )


# ---------------------------------------------------------------------------
# D-00-016 — G0.7's exit-code assertion is not a dead statement
# ---------------------------------------------------------------------------


def test_g0_7_exit_code_assertion_is_not_a_dead_statement() -> None:
    """D-00-016: under `set -uo pipefail` a bare `test ...;` discards its status.

    G0.7 claims two things — that `Settings()` exits non-zero on a missing
    required variable, and that it names `COCKROACH_DATABASE_URL` — and without
    `|| exit 1` it could only ever fail on the second. `-e` is not the fix:
    these flags omit it on purpose so that `rc=$?` survives the deliberately
    failing command.

    Neuter to prove it: delete `|| exit 1` from the `test "$$rc" -ne 0` line in
    the `gate-0` recipe and this test goes red.
    """
    recipe = makefile_recipe("gate-0")
    assertion = [
        line for line in recipe.splitlines() if re.search(r'test\s+"\$\$rc"\s+-ne\s+0', line)
    ]
    assert assertion, "G0.7's exit-code assertion is missing from the gate-0 recipe"
    for line in assertion:
        assert "|| exit 1" in line, (
            f"{line.strip()!r} discards its own status. The recipe runs under "
            "`set -uo pipefail` without `-e`, so a bare `test` is a statement "
            "whose result nothing reads."
        )


# ---------------------------------------------------------------------------
# D-00-025 / D-00-043 — G0.3 is two scans, and the second can see ops/
# ---------------------------------------------------------------------------


def test_gate_0_runs_both_gitleaks_scans_with_the_project_config() -> None:
    """D-00-025: one `$(GATE) G0.3` line against the two scans G0.3 is defined
    to be, and neither named `--config`, so a local run could silently use a
    different ruleset from CI.

    Neuter to prove it: delete the `$(GATE) G0.3b` line, or drop `--config
    .gitleaks.toml` from either scan, and this test goes red.
    """
    recipe = makefile_recipe("gate-0")
    scans = [
        line
        for line in recipe.splitlines()
        if "gitleaks detect" in line and not line.lstrip().startswith("#")
    ]
    assert len(scans) == 2, (
        f".gitleaks.toml declares G0.3 to be two scans; the gate-0 recipe runs "
        f"{len(scans)}: {scans}"
    )
    ids = {re.search(r"\$\(GATE\)\s+(\S+)", line).group(1) for line in scans if "$(GATE)" in line}
    assert ids == {"G0.3", "G0.3b"}, f"expected G0.3 and G0.3b, found {sorted(ids)}"
    for line in scans:
        assert "--config .gitleaks.toml" in line, (
            f"{line.strip()!r} does not name --config, so it can fall back to the "
            "default ruleset and disagree with CI without saying so."
        )


def test_g0_3b_is_a_working_tree_scan_of_ops() -> None:
    """D-00-043: G0.3b was `--source ops/gates` in git mode — the same scan as
    G0.3, run twice.

    In git mode gitleaks scans the repository containing `--source` and ignores
    the path, so the byte count was identical to the scan on the line above it.
    `ops/` was also untracked, so no git-mode scan could have reached it anyway.

    Neuter to prove it: change G0.3b back to `--source ops/gates` without
    `--no-git`, in the Makefile or in ci.yml, and this test goes red.
    """
    make_line = [
        line
        for line in makefile_recipe("gate-0").splitlines()
        if "$(GATE) G0.3b" in line and not line.lstrip().startswith("#")
    ]
    assert len(make_line) == 1, f"expected exactly one G0.3b line, found {make_line}"
    for where, line in (("Makefile", make_line[0]), ("ci.yml", _ci_g0_3b_line())):
        assert (
            "--source ops " in f"{line} "
        ), f"{where}: G0.3b must scan ops/, not a subdirectory: {line.strip()!r}"
        assert "--no-git" in line, (
            f"{where}: G0.3b must be a WORKING-TREE scan. Without --no-git "
            "gitleaks scans the repository containing --source and ignores the "
            f"path entirely: {line.strip()!r}"
        )

    tracked = subprocess.run(
        ["git", "ls-files", "ops/"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.stdout.strip(), (
        "`git ls-files ops/` is empty. The evidence directory is untracked, so "
        "nothing restores a destroyed transcript and no git-mode scan can see it."
    )


def _ci_g0_3b_line() -> str:
    lines = [
        line
        for line in CI_YML.read_text(encoding="utf-8").splitlines()
        if "gitleaks detect" in line
        and "--source ops" in line
        and not line.lstrip().startswith("#")
    ]
    assert lines, "ci.yml runs no `gitleaks detect --source ops` scan"
    return lines[0]


# ---------------------------------------------------------------------------
# D-00-041 / D-00-024 — the scanner version floor, and the binary
# ---------------------------------------------------------------------------


def test_the_gitleaks_version_floor_is_declared_consistently() -> None:
    """D-00-041: on 8.21.x the top-level `[[allowlists]]` array is applied to the
    extended default ruleset only and is silently ignored for every custom
    `pv-*` rule — the rules covering the six shapes this project can leak.

    The floor is the fix, and it has to hold in all three places that state it,
    because a runner that installs from ci.yml and a developer who reads the
    runbook must land on the same binary.

    Neuter to prove it: put `8.21.0` back in any one of the three files and this
    test goes red.
    """
    floor = (8, 30, 0)
    for path in (GITLEAKS_TOML, CI_YML, RUNBOOK):
        text = path.read_text(encoding="utf-8")
        versions = {
            tuple(int(p) for p in m)
            for m in re.findall(r"\b(\d+)\.(\d+)\.(\d+)\b", text)
            if int(m[0]) == 8
        }
        declared = {v for v in versions if v >= floor}
        assert declared, (
            f"{path.name} names no gitleaks version at or above "
            f"{'.'.join(map(str, floor))}; the versions it does name are "
            f"{sorted(versions)}. Below the floor the allowlists are inert for "
            "every custom rule and nothing says so."
        )


def test_the_gitleaks_binary_is_installed_and_meets_the_floor() -> None:
    """D-00-024: without the binary, G0.3 cannot run and `tools/scrub.py` is the
    only filter between a credential and a public repository.

    This deliberately does NOT carry the `requires_gitleaks` skip that the rest
    of `test_gitleaks_config.py` uses. Every assertion behind that marker turns
    into a skip when the binary is absent, so the state this record describes —
    no scanner on the build machine — is a green run everywhere else in the
    suite. One test has to go red instead, or the record has no verifying
    assertion at all.

    Neuter to prove it: run this module with `gitleaks` off PATH and with
    `HOME` pointing somewhere without `bin/gitleaks.exe`, and it goes red.
    """
    from tools.tests.test_gitleaks_config import GITLEAKS, MIN_VERSION, _version

    assert GITLEAKS is not None, (
        "the gitleaks binary is not on PATH and is not at ~/bin/gitleaks.exe. "
        "G0.3, G0.3b and S8 all run it; without it the scan half of the "
        "secret-leak defence does not exist on this machine."
    )
    got = _version()
    assert got >= MIN_VERSION, (
        f"gitleaks {'.'.join(map(str, got))} is below the "
        f"{'.'.join(map(str, MIN_VERSION))} floor (D-00-041)."
    )


# ---------------------------------------------------------------------------
# D-00-038 — allowlist C's `never committed` regex
# ---------------------------------------------------------------------------


@requires_gitleaks
def test_a_secret_on_a_never_committed_line_is_still_reported(tmp_path: Path) -> None:
    """D-00-038: the bare phrase `never committed` was an allowlist regex.

    Under `condition = "AND"` with `regexTarget = "line"` it excused *any*
    finding on any line containing those two words inside the eleven enumerated
    `ops/` files — the only allowlist regex in the file that did not require an
    actual redaction marker. The line below is the shape that made it dangerous:
    the phrase and a live credential on one line.

    Neuter to prove it: add an allowlist to `.gitleaks.toml` whose `regexes`
    contain `'''never committed'''` with `regexTarget = "line"` and this test
    goes red.
    """
    root = tmp_path / "repo"
    write(
        root,
        "ops/cluster-probe.txt",
        "-- password is never committed; run was "
        "postgresql://pv_migrator:Kj8sQ2mNvR4tL9xW@h.example.com:26257/provenance\n",
    )
    found = scan(root)
    assert reported(found, "ops/cluster-probe.txt", line=1), (
        "a live DSN password on a line reading `never committed` was not "
        "reported. The phrase is prose, not a redaction marker, and a value "
        "beside it is a value."
    )


# ---------------------------------------------------------------------------
# D-00-033 / D-00-034 — the repository hygiene fixes
# ---------------------------------------------------------------------------

# The ten credential shapes D-00-033 uncovered, plus the eval-report rule.
IGNORED_SHAPES = [
    ".envrc",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "client.p12",
    "client.pfx",
    "keystore.jks",
    "id_rsa",
    "secrets.json",
    "prod.tfvars",
    "ops/gate-env.local.sh",
    "evals/reports/_probe_run.json",
]


@pytest.mark.parametrize("candidate", IGNORED_SHAPES)
def test_gitignore_covers_the_credential_shapes(candidate: str) -> None:
    """D-00-033: ten credential shapes and `evals/reports/*.json` were missed.

    `.envrc` is the most likely next credential file in this tree — the runbook
    and `settings.py` both say the shell exports the environment, which is the
    direnv use case exactly — and `ops/gate-env.sh` advertises itself as
    committed and secret-free, which invites a `*.local.sh` sibling that is not.

    Neuter to prove it: remove the matching line from `.gitignore` and the
    corresponding case goes red.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-q", candidate],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{candidate} is not ignored by .gitignore"


def test_the_named_eval_baseline_is_not_ignored() -> None:
    """The other half of D-00-033: `evals/reports/*.json` must not eat T0.2's
    named baseline (`22_EVAL_DATASETS.md` section 1.4)."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "evals/reports/baseline.json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, (
        "evals/reports/baseline.json is ignored. The negation `!evals/reports/"
        "baseline.json` is what keeps the eval baseline a committed artifact."
    )


def test_notice_attributes_langgraph_to_graphs_that_exist() -> None:
    """D-00-034: `NOTICE` credited LangGraph for "the Librarian, Registrar and
    Advocate graphs"; neither of the first two appears anywhere in the design
    pack. `NOTICE` is a public release artifact read at S2 and S7, and two
    invented component names in it read as a project describing a system it did
    not build.

    Neuter to prove it: put either name back in `NOTICE` and this test goes red.
    """
    invented = re.compile(r"Librarian|Registrar")
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{n}"
        for path in [REPO_ROOT / "NOTICE", *(REPO_ROOT / "docs").rglob("*.md")]
        if path.exists()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if invented.search(line)
    ]
    assert not offenders, (
        f"invented component names appear at {offenders}. "
        "00_IMPLEMENTATION_MAP.md section 5 names ingestion_graph.py and "
        "advocate_graph.py; section 4.1 names the Interpreter and Advocate graphs."
    )


# ---------------------------------------------------------------------------
# D-00-015 — `make debt` distinguishes an absent ledger from an empty one
# ---------------------------------------------------------------------------


def test_make_debt_distinguishes_a_missing_ledger_from_an_empty_one() -> None:
    """D-00-015: `make debt` printed `0 carried items` and exited 0 with no
    ledger file to read, so a reviewer pasted "no carried debt" into a gate
    report from a tool that had opened no file.

    Section 9.3 makes that line part of every gate report, so "the ledger is
    empty" and "there is no ledger" must not render as the same sentence.

    Neuter to prove it: make `print_debt` ignore `ledger_exists`, or move the
    `--debt` branch back above the "does not exist yet" notes in
    `tools/defect_lint.py`, and this test goes red.
    """
    absent = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.defect_lint",
            "--debt",
            "--debt-file",
            "ops/defects/NO_SUCH_FILE.md",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert "NO_SUCH_FILE.md" in absent.stdout, (
        "`--debt` against a missing ledger does not name the file it could not "
        f"read. Output was:\n{absent.stdout}"
    )
    assert "no CARRIED_DEBT.md ledger exists" in absent.stdout, (
        "the absent case must state the absence. `0 carried items` from a tool "
        f"that opened nothing is the whole defect. Output was:\n{absent.stdout}"
    )


# ---------------------------------------------------------------------------
# D-00-017 — the ledger passes its own linter
# ---------------------------------------------------------------------------


def test_the_defect_ledger_passes_its_own_linter() -> None:
    """D-00-017: `make defects` returned non-zero on the ledger itself, and
    section 11.3 makes a clean run a binding precondition of *every* gate
    verdict — so G-0 could not be signed at all while the ledger was malformed.

    Neuter to prove it: break any row in `ops/defects/DEFECTS.md` — drop a
    severity rule id, or set a `CLOSED` row's fix commit to a short SHA — and
    this test goes red naming the rule that fired.
    """
    result = subprocess.run(
        [sys.executable, "-m", "tools.defect_lint"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "ops/defects/DEFECTS.md does not satisfy tools/defect_lint.py:\n"
        + result.stdout
        + result.stderr
    )


# ---------------------------------------------------------------------------
# D-00-026 — tools/gate.sh checks the scrubber on the command line
# ---------------------------------------------------------------------------


@requires_bash
def test_gate_sh_takes_the_capture_failed_path_when_the_command_scrub_fails(
    tmp_path: Path,
) -> None:
    """D-00-026: the body pipeline's scrubber status was checked; the command
    line's was not.

    A scrubber that failed on the `# cmd=` line and succeeded in the body wrote
    a log with an empty command line and a normal `exit=0` header — a gate log
    that does not say what was run, with nothing flagging it.

    `PV_PYTHON=false` is the shim: `false <scrub.py>` is an interpreter that
    always fails, which is exactly the fault. `PV_GATE_LOG` keeps the log out of
    the committed `ops/gates/logs/`.

    Neuter to prove it: drop the `CMD_SCRUB_STATUS` check from `tools/gate.sh`
    and this test goes red — the log comes back headed `exit=0`.
    """
    assert BASH is not None
    logs = tmp_path / "logs"
    result = subprocess.run(
        [
            BASH,
            "tools/gate.sh",
            "PROBE-CAPFAIL",
            "--",
            "bash",
            "-c",
            "echo hello; exit 0",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={
            **_clean_env(),
            "PV_GATE_LOG": str(logs),
            "PV_PYTHON": "false",
        },
    )
    assert result.returncode == 70, (
        "a capture fault must exit EX_SOFTWARE (70), which cannot be read as a "
        f"gate result. Got {result.returncode}.\n{result.stdout}\n{result.stderr}"
    )
    written = list(logs.glob("PROBE-CAPFAIL.*.log"))
    assert written, f"no log was written to {logs}"
    header = written[0].read_text(encoding="utf-8")
    assert "exit=CAPTURE-FAILED" in header, header
    assert "# cmd=<UNAVAILABLE>" in header, header


def _clean_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.pop("PV_PYTHON", None)
    env.pop("PV_GATE_LOG", None)
    return env


# ---------------------------------------------------------------------------
# D-00-023 — a gate battery refuses to run on a make where .SHELLFLAGS is inert
# ---------------------------------------------------------------------------


def test_a_gate_battery_refuses_a_make_older_than_3_82() -> None:
    """D-00-023: `.SHELLFLAGS` was introduced in GNU Make 3.82 and is silently
    ignored on 3.81, so on the build machine's GnuWin32 `make` every
    multi-command recipe lost `-e`, `-u` and `pipefail` — including the gate
    recipes, where a step that scrolls past with exit 0 is a green log for an
    assertion that failed.

    Two halves, because either alone is vacuous. The guard's comparison is
    exercised as running code against both versions, and the gate battery is
    asserted to actually call it: a correct predicate nothing invokes protects
    nothing, and an invoked predicate that compares wrongly protects nothing
    either.

    Neuter to prove it: lower the `(3, 82)` tuple in `define
    require_make_version`, or delete the `$(call require_make_version)` line
    from the `gate-0` recipe, and this test goes red.
    """
    text = MAKEFILE.read_text(encoding="utf-8")
    guard = re.search(r"(?s)define require_make_version\n(.*?)\nendef", text)
    assert guard is not None, (
        "`define require_make_version` is gone from the Makefile. It is the only "
        "thing standing between a gate battery and a make on which every recipe "
        "reports exit 0 whatever happened inside it."
    )

    assert re.search(r"(?m)^\t\$\(call require_make_version\)\s*$", makefile_recipe("gate-0")), (
        "the gate-0 recipe does not call require_make_version. `bootstrap` has "
        "carried this check since T0.3, which protected the install and not the "
        "thing being certified."
    )

    # The guard's own predicate, extracted and run against both versions. The
    # recipe body is a `$(PY) -c '...'` one-liner with `$(MAKE_VERSION)`
    # expanded by make; expanding it here is what make would do.
    body = re.search(r"(?s)\$\(PY\) -c '(.*?)'", guard.group(1))
    assert body is not None, "the version guard is no longer a runnable predicate"
    predicate = body.group(1).replace("\\n", "").replace("\t", " ")

    for version, want_zero in (("3.81", False), ("3.79", False), ("3.82", True), ("4.4.1", True)):
        run = subprocess.run(
            [sys.executable, "-c", predicate.replace("$(MAKE_VERSION)", version)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert (run.returncode == 0) is want_zero, (
            f"the version guard returned {run.returncode} for GNU Make {version}; "
            f"expected {'0' if want_zero else 'non-zero'}. Below 3.82 `.SHELLFLAGS` "
            "is ignored and the battery would report a pass it did not measure."
        )


def test_the_unit_lane_actually_collects() -> None:
    """A conftest ImportError aborts the whole session, so nothing runs.

    **This test cannot detect that abort, and the real guard is in the
    Makefile.** It carries ``pytest.mark.unit`` like every test in this module,
    so it is inside the lane it describes -- and a collection abort ends the
    session before any test executes, including this one. Measured: under
    ``pytest -q -m unit`` the string ``test_the_unit_lane_actually_collects``
    appears **zero** times in the output. The first version of this test claimed
    to guard the abort and could only ever report when invoked in a scope narrow
    enough to dodge the broken conftests. (``D-00-044``, finding 2 -- the
    seventh description-versus-mechanism drift in this codebase, and the third
    inside a remediation for one of the others.)

    No in-session pytest test can guard against session-wide collection abort:
    the abort is precisely what stops it running. ``require_collection`` in the
    ``Makefile`` runs ``--collect-only`` in its own session *before* each lane,
    which is the check that actually fires. ``test_the_makefile_prechecks_
    collection`` below is what keeps it from being deleted.

    What this test still buys, in the cases where collection does NOT abort: the
    marker expression silently matching nothing, and the count falling through
    the floor.

    This is the third appearance of one shape in this repository. ``D-00-014``:
    ``testpaths`` omitted ``tools/``, so 28 scrubber tests were never collected
    -- not in ``make test``, not in ``make test-fast``, not in CI. ``D-00-005``:
    the ``infra/`` omission hid 304 CDK tests the same way. Both were *suites
    that existed and nothing ran*, and both read as a clean pass to anyone who
    checked only the summary line.

    A collection error is worse than either, because it is not scoped. pytest
    aborts the **entire** session on a single ``conftest.py`` ImportError, so
    one broken directory silences every other test in the run. The output says
    ``N deselected, 2 errors`` and exits **2** -- and ``make`` reports its own
    exit code on top of that, so a gate checking for ``1`` misreads it as
    something else entirely.

    The floor is deliberately far below the real count. Its job is to catch
    "collection aborted" and "the marker expression now matches nothing", not
    to track the suite's size -- a tight floor would fail on every legitimate
    deletion and get raised until it meant nothing.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "unit"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    rendered = "\n".join(completed.stdout.strip().splitlines()[-6:])

    errors = re.search(r"(\d+)\s+errors?\b", completed.stdout)
    error_count = int(errors.group(1)) if errors else 0
    assert error_count == 0, (
        f"{error_count} collection error(s): the unit lane ABORTED, so nothing in "
        "the run executed. pytest ends the whole session on one conftest "
        "ImportError, however healthy every other directory is.\n" + rendered
    )
    assert completed.returncode == 0, (
        f"pytest --collect-only -m unit exited {completed.returncode}, not 0. "
        "Exit 2 means collection was interrupted, which is not a failing test "
        "and must not be read as one.\n" + rendered
    )

    # `N/M tests collected` when some are deselected, `N tests collected` when
    # none are. Matching only the second spelling would read zero on the lane
    # this guard exists for.
    match = re.search(r"(\d+)(?:/\d+)?\s+tests? collected", completed.stdout)
    assert match is not None, f"could not read a collected count from:\n{rendered}"
    collected = int(match.group(1))
    assert collected >= 500, (
        f"only {collected} unit tests collected. Either collection is partially "
        "aborting or the `unit` marker expression has stopped matching."
    )


def test_the_makefile_prechecks_collection() -> None:
    """The guard above cannot fire during an abort; this is what does.

    Asserting the recipe's *text* rather than its behaviour is a compromise and
    worth naming as one: running ``make`` from a test would need a make binary
    of the right version on PATH, which ``test_a_gate_battery_refuses_a_make_
    older_than_3_82`` exists because we cannot assume. What this catches is the
    realistic regression -- somebody deleting the precheck because a lane was
    slow -- not a subtly broken one.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "define require_collection" in makefile, (
        "the collection precheck is gone; nothing now detects a lane that "
        "aborts before running (D-00-044)"
    )
    assert "--collect-only" in makefile, "require_collection no longer collects"

    live = [
        line
        for line in makefile.splitlines()
        if "require_collection" in line and not line.lstrip().startswith("#")
    ]
    calls = [line for line in live if "$(call require_collection" in line]
    assert len(calls) >= 4, (
        f"only {len(calls)} lane(s) precheck collection; test, test-fast, test-db "
        "and test-all each need it, or the lane they guard can abort silently"
    )

    for lane in ("test:", "test-fast:", "test-db:", "test-all:"):
        recipe = makefile_recipe(lane[:-1])
        assert "require_collection" in recipe, (
            f"`make {lane[:-1]}` does not precheck collection, so it can report "
            "a lane that never executed"
        )
