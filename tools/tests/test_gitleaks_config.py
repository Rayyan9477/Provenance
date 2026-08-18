"""Positive controls for `.gitleaks.toml`.

An allowlist with no positive control is indistinguishable from `disabledRules`.
Every test here therefore asserts BOTH directions:

  * negative — the documented fake IS excused;
  * positive — a realistic credential of the SAME shape, in the SAME file, is
    STILL reported.

The positive half is the point. Three defects in this configuration shipped
because only the negative half was ever checked:

  D-00-010  an allowlist excused any gate-log line carrying a redaction marker,
            so a line holding a marker *and* a live secret was excused too.
  D-00-041  on gitleaks 8.21.2 the top-level ``[[allowlists]]`` array is applied
            to the extended default ruleset only and is silently ignored for the
            custom ``pv-*`` rules — the rules covering the six shapes this
            project can actually leak. Nothing was noisy; the allowlists simply
            did not exist for those rules.
  D-00-042  ``gitleaks detect --source ops/gates`` without ``--no-git`` scans the
            whole repository and ignores the path scoping, and ``ops/`` is
            untracked so a git-mode scan cannot reach it at all. A planted DSN
            with a real-shaped password produced "no leaks found".

Two mechanical traps these tests are also built to catch, because both fail
*silently* in the direction of scanning less:

  1. ``--source`` must be RELATIVE and the process cwd must be the scan root.
     With an absolute ``--source``, gitleaks reports absolute ``File`` paths and
     every repo-relative ``paths`` regex in the configuration stops matching.
  2. The binary must be >= 8.30.0 (see ``MIN_VERSION``). A downgrade does not
     announce itself — it just quietly stops applying allowlists to custom
     rules.

These tests shell out to the real gitleaks binary on purpose. Re-implementing
its allowlist semantics in Python would test the re-implementation, and the
re-implementation is exactly what was wrong.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".gitleaks.toml"

# Raised from 8.21.0 by D-00-041. Below this floor the custom-rule allowlists
# are inert and this whole module would pass while protecting nothing.
MIN_VERSION = (8, 30, 0)


def _binary() -> str | None:
    found = shutil.which("gitleaks")
    if found:
        return found
    fallback = Path.home() / "bin" / "gitleaks.exe"
    return str(fallback) if fallback.exists() else None


GITLEAKS = _binary()

# A silent skip is the vacuity failure this project files defects about, so the
# reason names the binary and the floor rather than saying "not available".
requires_gitleaks = pytest.mark.skipif(
    GITLEAKS is None,
    reason=(
        "the `gitleaks` binary is not on PATH and is not at ~/bin/gitleaks.exe. "
        f"G0.3 and S8 both run it and >= {'.'.join(map(str, MIN_VERSION))} is "
        "required (.gitleaks.toml header). Install it; do not skip this module."
    ),
)


def _version() -> tuple[int, ...]:
    assert GITLEAKS is not None
    out = subprocess.run([GITLEAKS, "version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    assert m, f"could not parse a version out of {out!r}"
    return tuple(int(g) for g in m.groups())


def scan(root: Path, *, no_git: bool = True, source: str = ".") -> list[dict[str, object]]:
    """Run gitleaks from `root` over `source`, and return its findings.

    cwd is ALWAYS the scan root and `source` is ALWAYS relative to it: see trap
    1 in the module docstring. To scan a subdirectory, pass the repository root
    as `root` and the subdirectory as `source` — never cd into the subdirectory,
    which strips its own name from the reported ``File`` and makes every
    repo-relative expression stop matching.

    The report is written OUTSIDE the scanned tree, because a report file inside
    it is itself scanned on a later run and its quoted secrets become fresh
    findings.
    """
    assert GITLEAKS is not None
    report = root.parent / f"{root.name}-report.json"
    if report.exists():
        report.unlink()
    cmd = [
        GITLEAKS,
        "detect",
        "--source",
        source,
        "--config",
        str(CONFIG),
        "--no-banner",
        "--report-format",
        "json",
        "--report-path",
        str(report),
    ]
    if no_git:
        cmd.append("--no-git")
    subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    if not report.exists():
        return []
    findings = json.loads(report.read_text(encoding="utf-8"))
    return [
        {
            "rule": f["RuleID"],
            "file": str(f["File"]).replace(os.sep, "/"),
            "line": f["StartLine"],
            "secret": f["Secret"],
        }
        for f in findings
    ]


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def reported(findings: list[dict[str, object]], rel: str, line: int | None = None) -> bool:
    return any(f["file"] == rel and (line is None or f["line"] == line) for f in findings)


# ---------------------------------------------------------------------------
# The configuration itself
# ---------------------------------------------------------------------------


@requires_gitleaks
def test_binary_meets_the_version_floor() -> None:
    """Below 8.30.0 the custom-rule allowlists are inert (D-00-041).

    This is asserted at runtime rather than trusted from the CI pin, because the
    failure mode of a downgrade is silent: allowlists stop applying and nothing
    in the output says so.
    """
    got = _version()
    assert got >= MIN_VERSION, (
        f"gitleaks {'.'.join(map(str, got))} is below the "
        f"{'.'.join(map(str, MIN_VERSION))} floor. On 8.21.x the top-level "
        "[[allowlists]] array is ignored for every custom pv-* rule, so this "
        "module would pass while protecting nothing."
    )


def test_config_exists_and_forbids_the_blunt_escape_hatch() -> None:
    """`disabledRules` turns a rule off everywhere; the policy forbids it."""
    text = CONFIG.read_text(encoding="utf-8")
    assert "useDefault = true" in text, "the upstream ruleset must stay enabled"
    live = [
        ln for ln in text.splitlines() if "disabledRules" in ln and not ln.lstrip().startswith("#")
    ]
    assert not live, f"disabledRules is forbidden by the allowlist policy: {live}"


# ---------------------------------------------------------------------------
# Allowlist A — AWS's published example key
# ---------------------------------------------------------------------------


@requires_gitleaks
def test_aws_example_key_excused_but_a_real_shaped_key_is_not(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(root, "docs/notes.md", "key = AKIAIOSFODNN7EXAMPLE\n")
    write(root, "docs/other.md", "key = AKIA2E0RZ4QIXMPL7T3B\n")
    found = scan(root)
    assert not reported(
        found, "docs/notes.md"
    ), "allowlist A should excuse AWS's own documentation example key"
    assert reported(found, "docs/other.md"), (
        "a real-shaped AKIA key must still be reported — allowlist A is "
        "value-scoped, not path-scoped"
    )


# ---------------------------------------------------------------------------
# Allowlist B — documented fake passwords in a DSN
# ---------------------------------------------------------------------------


@requires_gitleaks
def test_fake_dsn_password_excused_but_a_real_one_is_not(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(root, "docs/a.md", "postgresql://pv_migrator:hunter2@h.example.com:26257/provenance\n")
    write(
        root,
        "docs/b.md",
        "postgresql://pv_migrator:Kj8sQ2mNvR4tL9xW@h.example.com:26257/provenance\n",
    )
    found = scan(root)
    assert not reported(found, "docs/a.md"), "the documented fake `hunter2` should be excused"
    assert reported(found, "docs/b.md"), (
        "a real-shaped DSN password must still be reported. This is the single "
        "most likely real leak in this project — every Secrets Manager role URL "
        "has exactly this shape."
    )


@requires_gitleaks
def test_sql_password_literal_placeholder_excused_but_a_real_one_is_not(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(
        root,
        "ops/a.txt",
        "CREATE USER pv_kernel_writer WITH PASSWORD '<generated, 24 chars, never committed>';\n",
    )
    write(root, "ops/b.txt", "CREATE USER pv_kernel_writer WITH PASSWORD 'Kj8sQ2mNvR4tL9xW';\n")
    found = scan(root)
    assert not reported(found, "ops/a.txt"), "the angle-bracketed placeholder must not match"
    assert reported(found, "ops/b.txt"), "a real SQL password literal must still be reported"


# ---------------------------------------------------------------------------
# Allowlist C — scrubbed transcripts, and the line-scoping hazard it carries
# ---------------------------------------------------------------------------


@requires_gitleaks
def test_no_allowlist_uses_paths(tmp_path: Path) -> None:
    """The single standing rule of this configuration, asserted structurally.

    `allowlist.paths` is a file-level skip evaluated before the file is read, so
    `condition = "AND"` cannot restrain it. One `paths` key silently exempts
    whole files. Checking the text is enough and is the only check that cannot
    itself be fooled.
    """
    live = [
        (n, ln)
        for n, ln in enumerate(CONFIG.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"\s*paths\s*=", ln)
    ]
    assert not live, (
        "`paths` appeared in .gitleaks.toml at "
        + ", ".join(f"line {n}" for n, _ in live)
        + ". It excludes whole files from scanning regardless of `condition`. "
        "Use value-scoped `regexes` instead."
    )


@requires_gitleaks
def test_transcript_paths_are_scanned_not_skipped(tmp_path: Path) -> None:
    """The regression test for the deleted allowlist C.

    `ops/cluster-probe.txt` is the file the probe scripts write live credentials
    through the scrubber into. It was, for a time, excluded from scanning
    entirely by a path allowlist. Both lines here must be reported: a redaction
    marker elsewhere on the line does not excuse a bare credential, and the
    filename buys nothing.
    """
    root = tmp_path / "repo"
    write(
        root,
        "ops/cluster-probe.txt",
        "context [REDACTED] and then postgresql://u:realpw123456@h.example.com:26257/db\n"
        "bare postgresql://u:sneaky7654321@h.example.com:26257/db\n",
    )
    found = scan(root)
    for line in (1, 2):
        assert reported(found, "ops/cluster-probe.txt", line=line), (
            f"line {line} of ops/cluster-probe.txt was not reported. If this "
            "regresses, a path allowlist has been reintroduced and the probe "
            "transcripts are unscanned."
        )


# ---------------------------------------------------------------------------
# Allowlist E/F/G — documentation and fixture values
# ---------------------------------------------------------------------------


@requires_gitleaks
def test_idempotency_key_excused_but_a_non_uuid_key_is_not(tmp_path: Path) -> None:
    """Allowlist E is value-scoped, so the shape is the whole of the protection.

    This test also pins the one real trade-off in the configuration, so that it
    stays a decision rather than becoming a surprise: because `paths` cannot be
    used safely, a UUIDv7 is excused *everywhere*, not only in the API
    specification. That is acceptable only while this project issues no
    UUID-shaped credential — capability tokens and cursors are HMAC, session
    tokens are JWTs, database credentials are DSNs. If that ever changes, delete
    allowlist E and rewrite the seven documentation examples instead.
    """
    uuid7 = "018f9d5f-4a2b-7c11-9000-a1b2c3d4e5f6"
    uuid4 = "6f1a2b3c-4d5e-4f60-8a9b-0c1d2e3f4a5b"
    opaque = "sk_live_51H8xQ2mNvR4tL9xWbYcZ7dEf"
    root = tmp_path / "repo"
    write(root, "docs/specs/15_API_SPEC.md", f'  -H "Idempotency-Key: {uuid7}" \\\n')
    write(root, "docs/specs/16_TRIGGER_DSL.md", f'  -H "Idempotency-Key: {uuid7}" \\\n')
    write(root, "docs/specs/17_OTHER.md", f'  KEY="{opaque}"\n  KEY2="{uuid4}"\n')
    found = scan(root)

    assert not reported(
        found, "docs/specs/15_API_SPEC.md"
    ), "the documented idempotency-key examples must be excused"
    assert not reported(found, "docs/specs/16_TRIGGER_DSL.md"), (
        "value scoping is deliberate and documented: the same UUIDv7 shape is "
        "excused wherever it appears, because a path-scoped entry would exclude "
        "the whole file from scanning"
    )
    assert reported(found, "docs/specs/17_OTHER.md", line=1), (
        "an opaque non-UUID key must still be reported — allowlist E excuses a "
        "shape, not a delimiter"
    )
    assert reported(found, "docs/specs/17_OTHER.md", line=2), (
        "a UUIDv4 must still be reported. Allowlist E pins the v7 version nibble "
        "and variant precisely so that it does not become a blanket exemption "
        "for every UUID."
    )


@requires_gitleaks
def test_hmac_fixture_excused_but_a_new_key_in_the_same_file_is_not(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    rel = "packages/python/provenance_contracts/tests/test_settings.py"
    write(
        root,
        rel,
        '    "PROVENANCE_CAPABILITY_HMAC_KEY": "Y2FwYWJpbGl0eS1obWFjLWtleS0zMi1ieXRlcy0wMDA=",\n'
        '    "SOME_NEW_HMAC_KEY": "9uQ3xZpL7vK2mR8tW5yB4nC6dF1gH0jS3kA7eT9wX2M=",\n',
    )
    found = scan(root)
    lines = {f["line"] for f in found if f["file"] == rel}
    assert 1 not in lines, "the three self-describing base64 fixtures are excused"
    assert 2 in lines, (
        "a FOURTH key added to that fixture must be reported. Allowlist G names "
        "three exact values so that adding key material to a test is a "
        "deliberate decision here, not an exemption inherited from neighbours."
    )


# ---------------------------------------------------------------------------
# D-00-042 — the scan must be able to see the evidence directory
# ---------------------------------------------------------------------------


@requires_gitleaks
def test_working_tree_scan_of_ops_detects_a_planted_credential(tmp_path: Path) -> None:
    """The regression test for G0.3b.

    `ops/` is untracked, so a git-mode scan cannot reach it. G0.3b must be a
    working-tree scan or the evidence directory is unscanned — which was true,
    silently, and was caught only by planting this exact canary.
    """
    root = tmp_path / "repo"
    write(
        root,
        "ops/gates/PHASE_00.md",
        'psql "postgresql://pv_migrator:R3alPassw0rdHere@rayyandb-32190.j77.aws-us-east-1.cockroachlabs.cloud:26257/provenance"\n',
    )
    found = scan(root, source="ops", no_git=True)
    assert reported(found, "ops/gates/PHASE_00.md"), (
        "a working-tree scan of ops/ must report a planted DSN. If this fails, "
        "G0.3b has stopped being able to see the evidence directory."
    )


@requires_gitleaks
def test_repo_ops_directory_is_clean() -> None:
    """G0.3b itself, run against the real `ops/` rather than a fixture.

    Scanned from the repository root with a relative `--source ops`, which is
    exactly what `make gate-0` runs. Running it from inside `ops/` instead would
    strip the `ops/` prefix from every reported path and quietly change which
    expressions match.
    """
    found = scan(REPO_ROOT, source="ops", no_git=True)
    assert not found, f"ops/ carries {len(found)} finding(s): {found}"


# ---------------------------------------------------------------------------
# .gitleaksignore — the fingerprint file must stay fingerprints
# ---------------------------------------------------------------------------


def test_gitleaksignore_entries_are_full_fingerprints() -> None:
    """Every entry must be `commit:file:rule:line`, with a full 40-char SHA.

    gitleaks also accepts shorter forms, and a bare `file:rule` would silence
    that rule across the whole file forever — the same whole-file exemption
    that `allowlist.paths` turned out to be, arriving by a different door. The
    four-part form pins one finding, of one rule, at one line, in one commit,
    and anything else in that file is still reported.
    """
    path = REPO_ROOT / ".gitleaksignore"
    if not path.exists():
        return
    bad: list[str] = []
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        count += 1
        parts = line.split(":")
        if len(parts) != 4 or not re.fullmatch(r"[0-9a-f]{40}", parts[0]) or not parts[3].isdigit():
            bad.append(line)
    assert not bad, (
        "these .gitleaksignore entries are not full commit:file:rule:line "
        f"fingerprints and would silence more than one finding: {bad}"
    )
    assert count > 0, ".gitleaksignore exists but holds no entries; delete it instead"


def test_gitleaksignore_only_covers_the_scanner_s_own_tests() -> None:
    """Nothing outside `tools/tests/` may be ignored by fingerprint.

    The bootstrap problem — a test proving the scanner detects a credential must
    contain one — applies only to the scanner's own tests. A fingerprint
    anywhere else is a real finding someone silenced, and it should be a defect
    record and a fix instead.
    """
    path = REPO_ROOT / ".gitleaksignore"
    if not path.exists():
        return
    outside = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and not line.strip().startswith("#")
        and not line.strip().split(":")[1].startswith("tools/tests/")
    ]
    assert not outside, (
        "fingerprints outside tools/tests/ are not the bootstrap problem and "
        f"must not be silenced here: {outside}"
    )
