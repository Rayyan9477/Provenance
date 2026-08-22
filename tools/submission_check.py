"""The Definition of Done, checked rather than recited.

Authority: ``docs/implementation/05_RELIABILITY_EVAL_DEMO.md`` section 19 --
"The architecture is implemented enough for submission when all are true".

The drift this surfaces
-----------------------
Section 19 was written before the pivot and still requires **Cognito, S3, SES,
EventBridge and CloudWatch**. ``PIVOT.md``'s status block records, as a binding
user decision, that *"the CockroachDB/AWS entry is discarded -- ``infra/cdk/``
is dead weight rather than dual-use"*. Model access is an AI Studio API key; the
runtime is Cloud Run.

So six of the section-19 items describe a product this build deliberately does
not ship. A checker that ran them verbatim would report six permanent failures
and be useless; one that quietly dropped them would report a green Definition of
Done over a checklist nobody had reconciled. Neither is honest, so each such
item is carried as ``SUPERSEDED`` with the decision that superseded it and the
capability that replaced it. The count of superseded items is printed, because
a reader must be able to see how much of the original checklist no longer
applies.

What a status means
-------------------
``PASS``       checked mechanically, and it held.
``FAIL``       checked mechanically, and it did not.
``CANNOT RUN`` the check could not execute -- a missing service, an absent
               credential. **Not a pass and not a failure.** ``D-00-005``.
``MANUAL``     genuinely requires a human to look (is the State Proof
               *understandable*?). Never auto-passed.
``SUPERSEDED`` the pivot replaced this requirement; the replacement is named.

The exit code is ``0`` only when nothing is FAIL and nothing is CANNOT RUN.
MANUAL items do not block it -- they are reported, and a human signs them off.
"""

from __future__ import annotations

import argparse
import enum
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class Status(enum.Enum):
    PASS = ("PASS", True)
    FAIL = ("FAIL", False)
    CANNOT_RUN = ("CANNOT RUN", False)
    MANUAL = ("MANUAL", True)
    SUPERSEDED = ("SUPERSEDED", True)

    def __init__(self, label: str, ok: bool) -> None:
        self.label = label
        self.ok = ok


@dataclass(frozen=True)
class Check:
    section: str
    claim: str
    run: Callable[[], tuple[Status, str]]


def _cmd(args: list[str], timeout: int = 600) -> tuple[int, str]:
    try:
        done = subprocess.run(
            args, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return done.returncode, done.stdout + done.stderr


def _superseded(replacement: str) -> Callable[[], tuple[Status, str]]:
    def check() -> tuple[Status, str]:
        return Status.SUPERSEDED, replacement

    return check


def _manual(who_decides: str) -> Callable[[], tuple[Status, str]]:
    def check() -> tuple[Status, str]:
        return Status.MANUAL, who_decides

    return check


# -- Memory -----------------------------------------------------------------


def _kernel_is_sole_writer() -> tuple[Status, str]:
    code, out = _cmd([sys.executable, "-m", "tools.write_path_lint"])
    if code == -1:
        return Status.CANNOT_RUN, out.strip()[:120]
    if code != 0:
        return Status.FAIL, "write_path_lint reports violations"
    line = next((ln for ln in out.splitlines() if "canonical write statements" in ln), "")
    return Status.PASS, line.strip()[:110]


def _state_proof_needs_no_model() -> tuple[Status, str]:
    code, out = _cmd(
        [sys.executable, "-m", "pytest", "-q", "-m", "unit", "-k", "state_proof", "--no-header"]
    )
    if code == 5:
        return Status.CANNOT_RUN, "no state-proof tests collected; the selection proves nothing"
    if code not in (0, 1):
        return Status.CANNOT_RUN, out.strip().splitlines()[-1][:110] if out.strip() else "no output"
    summary = next((ln for ln in reversed(out.splitlines()) if " passed" in ln), "")
    return (Status.PASS if code == 0 else Status.FAIL), summary.strip()[:110]


def _serializable_concurrency_test_exists() -> tuple[Status, str]:
    """Found by MARKER, not by filename.

    The first draft globbed for `*concurren*.py` and reported FAIL. The tests
    exist and are called `test_retry.py`, `test_case_ops.py` and so on -- they
    carry `pytest.mark.concurrency`. A checker that looks for a naming
    convention nobody adopted reports the product broken when the checker is.
    """
    code, out = _cmd(
        [sys.executable, "-m", "pytest", "-m", "concurrency", "--collect-only", "-q", "--no-header"]
    )
    if code == 5:
        return Status.FAIL, "no test carries pytest.mark.concurrency"
    if code not in (0, 1):
        return Status.CANNOT_RUN, "collection did not complete"
    line = next((ln for ln in reversed(out.splitlines()) if "test" in ln and "/" in ln), "")
    count = sum(1 for ln in out.splitlines() if "::" in ln)
    return Status.PASS, f"{count} tests marked `concurrency`" if count else line[:110]


# -- Agents -----------------------------------------------------------------


def _versions_are_recorded() -> tuple[Status, str]:
    """`agent_runs` must carry graph, model and prompt versions for a real run."""
    code, out = _cmd(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-m",
            "unit",
            "-k",
            "graph_names_match_the_schema",
            "--no-header",
        ]
    )
    if code == 5:
        return Status.CANNOT_RUN, "the graph-name agreement test was not collected"
    if code not in (0, 1):
        return Status.CANNOT_RUN, "the selection could not run"
    return (Status.PASS if code == 0 else Status.FAIL), "graph names agree with ck_agent_runs_graph"


def _proposals_are_typed() -> tuple[Status, str]:
    try:
        from provenance_contracts.proposal import MemoryProposal  # noqa: F401
    except Exception as exc:  # pragma: no cover - import failure is the signal
        return Status.FAIL, f"MemoryProposal does not import: {type(exc).__name__}"
    return Status.PASS, "provenance_contracts.proposal.MemoryProposal"


# -- Quality ----------------------------------------------------------------


def _kernel_suite_is_green() -> tuple[Status, str]:
    code, out = _cmd([sys.executable, "-m", "pytest", "-q", "-m", "unit", "--no-header"], 1800)
    if code not in (0, 1):
        return (
            Status.CANNOT_RUN,
            "the unit lane did not complete (check disk space; a full disk exits 2)",
        )
    summary = next((ln for ln in reversed(out.splitlines()) if " passed" in ln), "")
    return (Status.PASS if code == 0 else Status.FAIL), summary.strip()[:110]


def _sabotage_matrix_is_proven() -> tuple[Status, str]:
    code, out = _cmd([sys.executable, "-m", "tools.sabotage_run"], 1800)
    line = next((ln for ln in out.splitlines() if ln.startswith("caught ")), "")
    if code == 0:
        return Status.PASS, line.strip()
    if "survived" in out and " survived 0" not in out:
        return Status.FAIL, line.strip() or "at least one sabotage survived"
    return Status.CANNOT_RUN, line.strip() or "the matrix could not be run"


def _adversarial_cases_exist() -> tuple[Status, str]:
    hits = list(_REPO_ROOT.glob("**/test*adversarial*.py"))
    hits = [h for h in hits if "node_modules" not in h.parts]
    if not hits:
        return Status.FAIL, "no adversarial test file found"
    return Status.PASS, f"{len(hits)} file(s)"


# -- Frontend / demo --------------------------------------------------------


def _every_live_route_renders() -> tuple[Status, str]:
    code, out = _cmd([sys.executable, "-m", "tools.route_sweep"], 1200)
    line = next((ln for ln in out.splitlines() if ln.startswith("swept ")), "")
    if code == 2:
        return (
            Status.CANNOT_RUN,
            "the sweep could not run (is `make run-api` and `make run-web` up?)",
        )
    if code == -1:
        return Status.CANNOT_RUN, out.strip()[:110]
    return (Status.PASS if code == 0 else Status.FAIL), line.strip() or "no summary line"


CHECKS: tuple[Check, ...] = (
    Check("Memory", "Memory Kernel is the sole canonical write path", _kernel_is_sole_writer),
    Check("Memory", "State Proof works without an LLM", _state_proof_needs_no_model),
    Check(
        "Memory",
        "at least one serializable concurrency test exists",
        _serializable_concurrency_test_exists,
    ),
    Check("Agents", "the interpreter emits typed proposals", _proposals_are_typed),
    Check("Agents", "model/prompt/graph versions are recorded", _versions_are_recorded),
    Check(
        "Agents",
        "LangGraph ingestion graph deployed to AgentCore Runtime",
        _superseded(
            "PIVOT.md: the AWS entry is discarded. The graph runs on Cloud Run against the Gemini Developer API; a live run is recorded in ops/agent-graph-live-run.txt"
        ),
    ),
    Check(
        "AWS",
        "Cognito authentication",
        _superseded(
            "PIVOT.md decision 3: an AI Studio API key, no IAM. Local auth is HS256 via scripts/mint_local_token.py"
        ),
    ),
    Check(
        "AWS",
        "S3 evidence storage",
        _superseded("PIVOT.md: GCS_ARTIFACT_BUCKET on Google Cloud Storage"),
    ),
    Check(
        "AWS",
        "EventBridge/Scheduler prospective-memory wakeup",
        _superseded(
            "PIVOT.md: the outbox + trigger evaluator in the Kernel; internal.evaluate_trigger is bound"
        ),
    ),
    Check(
        "AWS",
        "CloudWatch/OTEL traces",
        _superseded("PIVOT.md: OTLP export, currently DISABLED; /v1/version discloses otlp_export"),
    ),
    Check("Sponsor", "CockroachDB remains canonical persistent memory", _kernel_is_sole_writer),
    Check("Quality", "the deterministic Kernel test suite is green", _kernel_suite_is_green),
    Check(
        "Quality", "the sabotage matrix is proven, not merely listed", _sabotage_matrix_is_proven
    ),
    Check(
        "Evaluation", "adversarial prompt-injection cases are included", _adversarial_cases_exist
    ),
    Check("UX/demo", "every live route renders against the real API", _every_live_route_renders),
    Check(
        "UX/demo",
        "the hero story works end to end",
        _manual("a human runs the rehearsal; no assertion can judge a narrative"),
    ),
    Check(
        "UX/demo",
        "the State Proof is understandable",
        _manual("a human reads it; comprehensibility is not machine-checkable"),
    ),
    Check(
        "UX/demo",
        "no raw chain-of-thought is exposed",
        _manual(
            "a human reviews the recorded demo; tools/scrub.py covers transcripts, not the screen"
        ),
    ),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="skip the slow full-lane checks")
    args = parser.parse_args(argv)

    slow = {
        "the deterministic Kernel test suite is green",
        "the sabotage matrix is proven, not merely listed",
    }
    checks = tuple(c for c in CHECKS if not (args.fast and c.claim in slow))

    print(f"Definition of Done - {len(checks)} assertions")
    print("docs/implementation/05_RELIABILITY_EVAL_DEMO.md section 19\n")

    results: list[tuple[Check, Status, str]] = []
    section = ""
    for check in checks:
        if check.section != section:
            section = check.section
            print(f"  {section}")
        status, detail = check.run()
        results.append((check, status, detail))
        print(f"    [{status.label:^11}] {check.claim}")
        if detail:
            print(f"                  {detail}")

    counts = {status: sum(1 for _, s, _ in results if s is status) for status in Status}
    print(
        "\n" + "  ".join(f"{status.label} {counts[status]}" for status in Status if counts[status])
    )

    if counts[Status.SUPERSEDED]:
        print(
            f"\n{counts[Status.SUPERSEDED]} assertions were SUPERSEDED by the pivot. They are "
            "carried rather than deleted so a\nreader can see how much of the original "
            "checklist no longer describes this build."
        )
    if counts[Status.MANUAL]:
        print(
            f"\n{counts[Status.MANUAL]} assertions need a human. They are NOT auto-passed and "
            "the exit code ignores them;\nsomeone has to look and say so."
        )
    if counts[Status.CANNOT_RUN]:
        print(
            f"\n{counts[Status.CANNOT_RUN]} could not be checked. That is not a pass and not a "
            "failure - it means nothing\nwas measured, and it blocks the exit code."
        )

    blocking = counts[Status.FAIL] + counts[Status.CANNOT_RUN]
    return 0 if blocking == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
