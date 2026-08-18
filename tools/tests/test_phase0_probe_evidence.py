"""Verifying assertions for the Phase 0 probe records.

`ops/` holds the only evidence Phase 0 produces. Four of the records this file
closes are about that evidence being destroyed, contradicted, unscrubbed, or
asserted without ever having been produced — and none of them had a runnable
assertion, which is how `D-00-005` was able to delete a full `P1`..`P11`
transcript and leave every lane green.

The probe script takes a `-RepoRoot` parameter, so every test that runs it runs
it against a temporary tree. **Nothing here writes to the repository's own
`ops/`.** That is not politeness: the destroyed-evidence defect this file exists
to guard against was caused by a tool that wrote to `ops/` before checking
whether it could do anything useful.
"""

from __future__ import annotations

import filecmp
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.scrub import scrub_text

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PS1 = REPO_ROOT / "ops" / "probes" / "phase0-probe.ps1"
PROBE_README = REPO_ROOT / "ops" / "probes" / "README.md"
BEDROCK_PROBE = REPO_ROOT / "ops" / "bedrock-probe.txt"
LEDGER = REPO_ROOT / "ops" / "PROBE_LEDGER.md"
STALE_LEDGER = REPO_ROOT / "ops" / "probes" / "PROBE_LEDGER.md"

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

requires_powershell = pytest.mark.skipif(
    POWERSHELL is None,
    reason=(
        "no PowerShell host on PATH. `ops/probes/phase0-probe.ps1` is the only "
        "probe implementation in the tree (D-00-030), so on a host without "
        "PowerShell these assertions are unproven, not satisfied."
    ),
)

# The six files `ops/probes/phase0-probe.ps1` treats as committed evidence.
EVIDENCE = (
    "ops/cluster-probe.txt",
    "ops/grant-probe.txt",
    "ops/bedrock-probe.txt",
    "ops/restore-probe.txt",
    "ops/decisions/VECTOR_INDEX_VARIANT.md",
    "ops/PROBE_LEDGER.md",
)


# ---------------------------------------------------------------------------
# D-00-003 — PB-5 actually invoked something
# ---------------------------------------------------------------------------


def test_pb5_records_a_real_invocation_for_both_tiers() -> None:
    """D-00-003: PB-5 had two green sub-results and one absent one, and the
    ledger recorded PASS anyway.

    Listing a model proves it exists in the region; it does not prove this
    account can invoke it, and model-access grants are per-account. The
    transcript must therefore name the id that answered for each tier, not the
    ids that were listed.

    Neuter to prove it: truncate `ops/bedrock-probe.txt`, or delete the verdict
    block, and this test goes red. That is the same neutering `make probe`
    performed by accident in D-00-005, which is why this assertion is worth
    having at all.
    """
    text = BEDROCK_PROBE.read_text(encoding="utf-8")
    assert text.strip(), f"{BEDROCK_PROBE} is empty; PB-5 has no transcript at all"

    tier_e = re.search(r"(?m)^\s*Tier E\s+(?P<id>\S+)\s+invocable", text)
    tier_r = re.search(r"(?m)^\s*Tier R\s+(?P<id>\S+)\s+invocable", text)
    assert tier_e is not None, (
        "no Tier E line recording an invocable id. Expected a verdict naming the "
        f"id form that actually answered. Transcript:\n{text}"
    )
    assert tier_r is not None, "no Tier R line recording an invocable id"

    for tier, match in (("E", tier_e), ("R", tier_r)):
        assert match.group("id").startswith("us.anthropic."), (
            f"Tier {tier} records {match.group('id')!r}. Anthropic chat models "
            "are invocable only through a `us.`/`global.` inference profile "
            "(D-00-002); a bare id in the verdict means the transcript is "
            "recording something that cannot have been called."
        )

    assert re.search(r"(?m)^\s*Embeddings\s+amazon\.titan-embed-text-v2:0", text), (
        "PB-5 must also record the embedding model, which is the one id that is "
        "correctly bare (D-00-040)."
    )


# ---------------------------------------------------------------------------
# D-00-020 — the probe targets the ids that can be invoked
# ---------------------------------------------------------------------------

# An id, not the wildcard `anthropic.claude-*` the prose uses when it says the
# bare form does not work: the character after the dash must begin a name.
BARE_ANTHROPIC_ID = re.compile(r"(?<!us\.)(?<!global\.)\banthropic\.claude-[a-z0-9]")


@pytest.mark.parametrize("path", [PROBE_PS1, PROBE_README], ids=lambda p: p.name)
def test_the_probe_names_no_superseded_bare_anthropic_id(path: Path) -> None:
    """D-00-020: PB-5 probed `anthropic.claude-haiku-4-5` and
    `anthropic.claude-opus-5` — seven hits across the script and its README.

    A `make probe` run would have re-failed PB-5 for the wrong reason and
    written the superseded ids back into the transcript, producing evidence that
    says the models do not work when what does not work is the id form.

    Neuter to prove it: put a bare id back in either file and this test goes red.
    """
    offenders = [
        f"{path.name}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if BARE_ANTHROPIC_ID.search(line)
    ]
    assert not offenders, "superseded bare Anthropic chat ids appear at:\n" + "\n".join(offenders)


def test_pb5_probes_all_three_anthropic_tiers_through_inference_profiles() -> None:
    """The positive half of D-00-020: the ids the probe actually calls.

    Asserting only the absence of the bare form would stay green if PB-5 stopped
    probing Anthropic altogether, which is the `L-VAC` question asked of this
    very test.
    """
    source = PROBE_PS1.read_text(encoding="utf-8")
    targets = dict(re.findall(r'\("(E|R|R-TARGET)",\s*"([^"]+)"\)', source))
    assert set(targets) == {"E", "R", "R-TARGET"}, (
        f"PB-5 probes {sorted(targets)}; expected Tier E, the Tier R model in "
        "force, and the Tier R target whose denial dates the grant when it lands."
    )
    for tier, model_id in targets.items():
        assert model_id.startswith("us.anthropic."), f"tier {tier} probes {model_id!r}"


# ---------------------------------------------------------------------------
# D-00-022 — one probe ledger
# ---------------------------------------------------------------------------


def test_there_is_exactly_one_probe_ledger() -> None:
    """D-00-022: two ledgers under the same name with contradictory verdicts.

    `ops/PROBE_LEDGER.md` read `PB-1..PB-4 = PASS` and `VARIANT: A`; the
    script-generated `ops/probes/PROBE_LEDGER.md` read `NOT RUN` and
    `VARIANT: none -- BRUTE_FORCE_PARTITION`. A reviewer who finds either has no
    reason to look for the other.

    Neuter to prove it: point `$LedgerFile` back at `ops/probes/PROBE_LEDGER.md`,
    or write PB rows into that file, and this test goes red.
    """
    assert LEDGER.exists(), f"{LEDGER} is the committed ledger path and is missing"
    if STALE_LEDGER.exists():
        stale = STALE_LEDGER.read_text(encoding="utf-8")
        rows = [line for line in stale.splitlines() if re.match(r"^\s*\|\s*`?PB-\d", line)]
        assert not rows, (
            f"{STALE_LEDGER} carries PB result rows again: {rows}. It is kept only "
            "as a redirect so that a habit pointing there lands on the reason."
        )
        assert (
            "ops/PROBE_LEDGER.md" in stale
        ), f"{STALE_LEDGER} exists but does not redirect to the committed ledger"

    source = PROBE_PS1.read_text(encoding="utf-8")
    ledger_assignment = re.search(r"(?m)^\$LedgerFile\s*=\s*(?P<rhs>.+)$", source)
    assert ledger_assignment is not None, "the probe script no longer sets $LedgerFile"
    rhs = ledger_assignment.group("rhs")
    assert "$OpsDir" in rhs and "PROBE_LEDGER.md" in rhs, (
        f"$LedgerFile is {rhs.strip()!r}; the generated ledger must be written to "
        "ops/PROBE_LEDGER.md, the path .gitleaks.toml enumerates and the reviewer reads."
    )
    assert "$ProbesDir" not in rhs, rhs


# ---------------------------------------------------------------------------
# D-00-021 — the two scrubbers agree
# ---------------------------------------------------------------------------

# The CockroachDB Cloud shape this rule exists for: the token is in the USER
# field and the password field is empty. No real value appears here.
TOKEN_IN_USER_DSN = "postgresql://pv_kernel_writer_TOKEN:@h.example.com:26257/provenance"


def protect_text_fragment() -> str:
    """Lift `Protect-Text` and the state it closes over out of the probe script.

    Dot-sourcing the whole script would run it. The function and its
    `$script:Secrets` list are self-contained, so the fragment is exactly the
    unit under test.
    """
    lines = PROBE_PS1.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("$script:Secrets"))
    opened = next(i for i, line in enumerate(lines) if line.startswith("function Protect-Text"))
    close = next(i for i in range(opened, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : close + 1])


@requires_powershell
def test_the_probe_scrubber_redacts_both_halves_of_the_userinfo(tmp_path: Path) -> None:
    """D-00-021: `Protect-Text` replaced everything after the colon and left the
    user half verbatim.

    This scrubber writes the committed `ops/*.txt` transcripts and
    `tools/scrub.py` writes the committed gate logs, so the repository would
    have shipped two scrubbers with different coverage on the one DSN shape that
    matters — the CockroachDB Cloud form that carries the token in the user
    field.

    Neuter to prove it: change the URL rule back to `://[^:/\\s@]+:[^@\\s]*@` ->
    `://$1:[REDACTED]@`, keeping the user half, and this test goes red.
    """
    assert POWERSHELL is not None
    script = tmp_path / "protect.ps1"
    script.write_text(
        protect_text_fragment() + f"\nWrite-Output (Protect-Text '{TOKEN_IN_USER_DSN}')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    powershell_out = result.stdout.strip()
    python_out = scrub_text(TOKEN_IN_USER_DSN).strip()

    assert "pv_kernel_writer_TOKEN" not in powershell_out, (
        "the probe scrubber preserved the userinfo USER. Some CockroachDB Cloud "
        "connection strings carry the token there, so redacting only the "
        f"password leaks on exactly the shape that matters. Got: {powershell_out!r}"
    )
    assert (
        "pv_kernel_writer_TOKEN" not in python_out
    ), f"tools/scrub.py regressed on the same shape: {python_out!r}"
    for out in (powershell_out, python_out):
        assert (
            "h.example.com:26257/provenance" in out
        ), f"redaction must cost the credential and not the host: {out!r}"


# ---------------------------------------------------------------------------
# D-00-005 — a probe run never destroys evidence it cannot replace
# ---------------------------------------------------------------------------


@requires_powershell
def test_the_probe_refuses_to_overwrite_existing_evidence(tmp_path: Path) -> None:
    """D-00-005: the script opened all four transcripts with
    `File::WriteAllText($f, '')` in the preflight — before the CA-certificate
    check, before the connection string is read, before `$SqlReady` exists.

    So the failure was not "a bad run wrote a bad transcript"; it was "any
    invocation at all, including one that cannot connect, destroys the evidence
    of the run that could". `ops/` was untracked, so there was no
    `git checkout` recovery.

    This is the record's own second close condition, run: a second invocation
    with no database URL must exit non-zero having left all six evidence files
    byte-identical.

    Neuter to prove it: delete the `$ExistingEvidence` refusal block from the
    preflight and this test goes red — the four transcripts come back empty.
    """
    assert POWERSHELL is not None
    for rel in EVIDENCE:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"SENTINEL {rel}\nVARIANT: A\n", encoding="utf-8")
    before = tmp_path / "_before"
    shutil.copytree(tmp_path / "ops", before / "ops")

    env = {
        k: v
        for k, v in __import__("os").environ.items()
        if k not in {"PV_PROBE_DB_URL", "PV_PROBE_MIGRATOR_URL"}
    }
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROBE_PS1),
            "-RepoRoot",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
    )

    assert result.returncode != 0, (
        "a probe run that reached nothing exited 0. It has learned nothing and "
        f"must not be readable as a result.\n{result.stdout}"
    )
    assert "REFUSING TO RUN" in result.stdout, result.stdout

    changed = [
        rel for rel in EVIDENCE if not filecmp.cmp(tmp_path / rel, before / rel, shallow=False)
    ]
    assert not changed, (
        f"the run modified committed evidence it could not replace: {changed}. "
        "A probe run never opens an output file it cannot fill."
    )


# ---------------------------------------------------------------------------
# D-00-006 — the transcripts the ledger cites are actually there
# ---------------------------------------------------------------------------


def test_the_g0_6_evidence_is_present_and_complete() -> None:
    """D-00-006: `ops/PROBE_LEDGER.md` read `PB-1..PB-4 = PASS` while
    `ops/cluster-probe.txt` held zero `-- P` headers and
    `ops/decisions/VECTOR_INDEX_VARIANT.md` read `NO VARIANT SELECTED`.

    A ledger that reads PASS while the transcript it cites is empty is the
    failure `23_PHASE_GATES.md` section 3 exists to prevent — an assertion
    reported complete without its pasted output — and it is the same shape as
    rule `B4` one level up: a claim rendered from a stored constant rather than
    from the thing it claims to describe.

    This is `G0.6`'s own pair of counts, asserted in the unit lane so that the
    loss is caught by `make test-fast` rather than at the gate. `D-00-005` guards
    the script; this guards the artifact.

    Neuter to prove it: truncate `ops/cluster-probe.txt`, or replace the
    `VARIANT:` line with `## NO VARIANT SELECTED`, and this test goes red.
    """
    cluster = (REPO_ROOT / "ops" / "cluster-probe.txt").read_text(encoding="utf-8")
    headers = [line for line in cluster.splitlines() if line.startswith("-- P")]
    assert len(headers) == 11, (
        f"ops/cluster-probe.txt carries {len(headers)} `-- P` headers, not 11. "
        "G0.6 asserts eleven capability probes; the cleanup step is written "
        "`-- CLEANUP` precisely so that it is not one of them."
    )

    variant = (REPO_ROOT / "ops" / "decisions" / "VECTOR_INDEX_VARIANT.md").read_text(
        encoding="utf-8"
    )
    chosen = [line for line in variant.splitlines() if re.fullmatch(r"VARIANT: [ABC]", line)]
    assert len(chosen) == 1, (
        f"ops/decisions/VECTOR_INDEX_VARIANT.md carries {len(chosen)} `VARIANT:` "
        "lines, not exactly one. A run that reached nothing has not disproved a "
        "frozen decision, and `NO VARIANT SELECTED` would drive PV_RETRIEVAL_MODE "
        "into the brute-force fallback over a missing binary."
    )

    for cited in ("ops/cluster-probe.txt", "ops/grant-probe.txt", "ops/bedrock-probe.txt"):
        path = REPO_ROOT / cited
        assert (
            path.exists() and path.stat().st_size > 0
        ), f"{cited} is cited as evidence by ops/PROBE_LEDGER.md and is empty."
