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
import ast
import enum
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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
    """Run *args*, returning ``(-1, reason)`` when it could not run at all.

    ``args[0]`` is resolved through :func:`shutil.which` rather than passed
    bare. On Windows the Google Cloud SDK installs ``gcloud`` as ``gcloud.cmd``,
    and ``subprocess.run(["gcloud", ...])`` without a shell does not consult
    ``PATHEXT`` -- so it raises ``FileNotFoundError`` for a tool that is plainly
    on ``PATH`` and answers on the command line.

    That is worth a helper rather than a shrug: this function's ``-1`` becomes
    ``CANNOT RUN``, and a ``CANNOT RUN`` that really means "wrong extension
    lookup" is a probe measuring the probe. It reported "gcloud is not on PATH"
    on a machine with gcloud 565.0.0 installed, which would have sent someone to
    debug their SDK install.
    """
    resolved = shutil.which(args[0])
    if resolved is None:
        return -1, f"{args[0]} is not on PATH"
    try:
        done = subprocess.run(
            [resolved, *args[1:]],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
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


# -- Mandatory ---------------------------------------------------------------
#
# The three hard requirements of the All Things Agentic hackathon, plus the
# three required submission artifacts.
#
# These did not exist in this file until 2026-08-27, and their absence was the
# defect. Section 19's Definition of Done predates the pivot, so every AWS item
# in it is carried as SUPERSEDED -- but nothing was ever added to check the
# rules that REPLACED them. The result was a checklist that could report
# "PASS 8, FAIL 0" while the entry was ineligible, because not one assertion
# asked whether a Google Cloud service existed.
#
# A gate that screens the previous contest is worse than no gate: it produces a
# green log for a question nobody asked.

#: The version floor the rules state: "Gemini 3.5 or newer".
_GEMINI_FLOOR: tuple[int, int] = (3, 5)


def _gemini_version_floor() -> tuple[Status, str]:
    """Every Gemini id this build invokes must be >= 3.5, and be PROVED invoked.

    Read from the probe transcript rather than from configuration, because
    configuration is a claim and the transcript is a measurement. The previous
    model canon was frozen from documentation and all four of its ids turned out
    to be un-invocable, which is the whole reason this project probes at all.
    """
    import re

    transcript = _REPO_ROOT / "ops" / "gemini-probe.txt"
    if not transcript.is_file():
        return (
            Status.CANNOT_RUN,
            "ops/gemini-probe.txt does not exist; run ops/probes/gemini_probe.py",
        )

    text = transcript.read_text(encoding="utf-8", errors="replace")
    if "CANNOT RUN" in text and "GOOGLE_API_KEY is not set" in text:
        return Status.CANNOT_RUN, "the probe recorded CANNOT RUN -- no API key when it last ran"

    # Only ids on a PASS line count. An id that appears in a FAIL line is
    # evidence against, and an id in prose is evidence of nothing.
    invoked: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        if "PASS" not in line:
            continue
        for name, major, minor in re.findall(r"(gemini-((?:\d+))\.(\d+)[a-z0-9.-]*)", line):
            invoked[name] = (int(major), int(minor))

    if not invoked:
        return Status.CANNOT_RUN, "no gemini-N.N id appears on a PASS line in the transcript"

    below = sorted(n for n, v in invoked.items() if v < _GEMINI_FLOOR)
    if below:
        return Status.FAIL, f"below the 3.5 floor and invoked: {', '.join(below)}"

    best = max(invoked.values())
    return (
        Status.PASS,
        f"{len(invoked)} id(s) invoked, all >= 3.5 (highest {best[0]}.{best[1]}): "
        + ", ".join(sorted(invoked)),
    )


def _google_agent_framework() -> tuple[Status, str]:
    """One of ADK / GenAI SDK / Antigravity SDK / GenKit, and actually used.

    Declared AND imported. A dependency in a manifest that no module imports is
    a claim about a file, not about the system.

    Parsed, not grepped. This matched the bare substring ``google.genai``
    anywhere in a file and reported "imported by 4 shipped modules". Two of
    those four were PROSE, and one of the two was a docstring saying the module
    does *not* import it -- ``counterfactual_graph.py``: "so the control plane
    can pass the real router **without this module importing** google.genai
    transitively". The gate counted a denial as an affirmation, which is an
    L-VAC defect inside the tool written to catch L-VAC defects.

    The requirement it screens is mandatory, so the number had to become true
    rather than merely smaller. An AST walk cannot read a comment.

    Module-level and lazy imports are counted separately and both reported: a
    lazy import is a real import and a weaker claim about what the shipped path
    reaches, and collapsing them would hide that distinction.
    """
    manifest = _REPO_ROOT / "requirements-runtime.txt"
    declared = manifest.is_file() and "google-genai" in manifest.read_text(encoding="utf-8")

    top_level: list[str] = []
    lazy: list[str] = []
    for path in sorted((_REPO_ROOT / "agents").rglob("*.py")):
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                hit = module.startswith("google.genai") or (
                    module == "google" and any(a.name == "genai" for a in node.names)
                )
            elif isinstance(node, ast.Import):
                hit = any(a.name.startswith("google.genai") for a in node.names)
            else:
                continue
            if not hit:
                continue
            rel = path.relative_to(_REPO_ROOT).as_posix()
            (top_level if any(node is stmt for stmt in tree.body) else lazy).append(rel)
            break

    if not declared:
        return Status.FAIL, "google-genai is not declared in requirements-runtime.txt"
    every = sorted(set(top_level) | set(lazy))
    if not every:
        return (
            Status.FAIL,
            "google-genai is declared but no shipped module under agents/ imports it",
        )
    return (
        Status.PASS,
        f"google-genai declared; imported by {len(every)} shipped module(s) "
        f"({len(top_level)} at module level, {len(lazy)} lazily): " + ", ".join(every),
    )


def _google_cloud_service() -> tuple[Status, str]:
    """A Cloud Run service must exist. This is the requirement, not a proxy for it.

    CANNOT RUN and FAIL are different answers here and the difference matters:
    no gcloud, or no configured project, means the question was not asked.
    A configured project with no service means it was asked and the answer was
    no -- which is a FAIL, and is the correct verdict until something is
    deployed.
    """
    code, out = _cmd(["gcloud", "config", "get-value", "project"], timeout=60)
    if code == -1:
        return Status.CANNOT_RUN, "gcloud is not on PATH; cannot ask whether anything is deployed"
    project = out.strip().splitlines()[0].strip() if out.strip() else ""
    if not project or project in {"(unset)", ""}:
        return (
            Status.CANNOT_RUN,
            "no gcloud project configured; run: gcloud config set project <id>",
        )

    code, out = _cmd(
        ["gcloud", "run", "services", "list", "--format=value(metadata.name,status.url)"],
        timeout=180,
    )
    if code == -1:
        return Status.CANNOT_RUN, f"gcloud run services list could not execute: {out[:80]}"
    if code != 0:
        return Status.CANNOT_RUN, f"gcloud refused the query in {project}: {out.strip()[:100]}"

    services = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not services:
        return Status.FAIL, f"project {project} has no Cloud Run service. deploy/cloudrun.sh up"
    return Status.PASS, f"{len(services)} Cloud Run service(s) in {project}: " + "; ".join(
        s.split("\t")[0] for s in services[:4]
    )


#: Phrases that assert the deployment does NOT exist. Checked against the live
#: answer rather than trusted, because both artifacts carried them for two days
#: after the deployment went up and the structural checks below passed anyway.
_DENIES_DEPLOYMENT: Final[tuple[str, ...]] = (
    "not yet deployed",
    "nothing is deployed",
    "not deployed",
    "not created",
    "no container has been built",
    "no service has been created",
)


def _artifact_agrees_with_reality(path: str) -> tuple[Status, str] | None:
    """Does *path* claim the deployment is absent while it demonstrably exists?

    This is the assertion whose absence let the worst finding of the whole
    submission audit survive: README.md's mandatory-requirements table said the
    Google Cloud requirement was "Not yet deployed" while two Cloud Run services
    were serving, and `docs/diagrams/architecture.md` agreed with it in five
    places. Both files passed their own structural checks -- a heading count and
    a mermaid-fence count -- because neither asked whether the CONTENT agreed
    with the machine.

    Stage One is binary. An artifact that tells a judge the entry is ineligible
    is worse than a missing artifact, and no count of headings can see it.

    Returns ``None`` when there is nothing to contradict: if the deployment
    check itself cannot run, this cannot fail on its behalf.
    """
    status, _ = _google_cloud_service()
    if status is not Status.PASS:
        return None
    raw = (_REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")

    # Two exclusions, both learned by this check firing wrongly on its first run.
    #
    # Strikethrough is a SUPERSEDED claim deliberately kept so the record of what
    # was believed survives its correction -- "~~Nothing is deployed.~~
    # **Deployed and serving.**" is the honest form, not a violation, and a guard
    # that punishes it would push the docs toward quietly deleting their own
    # history instead.
    #
    # And the phrase has to be ABOUT the Cloud Run deployment. "workers/ ... not
    # deployed separately" is a true statement about a directory that holds no
    # code, on a line that has nothing to do with the mandatory requirement.
    without_struck = re.sub(r"~~.*?~~", "", raw, flags=re.S)
    hits: set[str] = set()
    for line in without_struck.lower().splitlines():
        if "cloud run" not in line and "mandatory" not in line:
            continue
        hits |= {phrase for phrase in _DENIES_DEPLOYMENT if phrase in line}
    if not hits:
        return None
    hits_list = sorted(hits)
    return (
        Status.FAIL,
        f"{path} says {hits_list} while Cloud Run is serving. A required artifact that "
        "denies a met mandatory requirement argues for the entry's own disqualification.",
    )


def _architecture_diagram() -> tuple[Status, str]:
    """A required artifact: "a clear visual representation of your system"."""
    diagram = _REPO_ROOT / "docs" / "diagrams" / "architecture.md"
    if not diagram.is_file():
        return Status.FAIL, "docs/diagrams/architecture.md does not exist"
    contradiction = _artifact_agrees_with_reality("docs/diagrams/architecture.md")
    if contradiction is not None:
        return contradiction
    text = diagram.read_text(encoding="utf-8", errors="replace")
    blocks = text.count("```mermaid")
    if blocks == 0:
        return Status.FAIL, "docs/diagrams/architecture.md contains no mermaid diagram"
    return Status.PASS, f"docs/diagrams/architecture.md, {blocks} mermaid diagram(s)"


def _readme_spinup() -> tuple[Status, str]:
    """A required artifact: step-by-step setup, locally OR deployed to the cloud.

    Checked for the four things a reader has to be able to find, because a
    README that has a "Spin-up" heading and no install step satisfies a grep and
    not a human.
    """
    readme = _REPO_ROOT / "README.md"
    if not readme.is_file():
        return Status.FAIL, "README.md does not exist"
    contradiction = _artifact_agrees_with_reality("README.md")
    if contradiction is not None:
        return contradiction
    text = readme.read_text(encoding="utf-8", errors="replace")
    needed = {
        "a spin-up section": "## Spin-up",
        "an install step": "make bootstrap",
        "a run step": "make run-api",
        "a cloud deploy step": "cloudrun.sh",
    }
    missing = sorted(name for name, needle in needed.items() if needle not in text)
    if missing:
        return Status.FAIL, "README.md is missing " + ", ".join(missing)
    return Status.PASS, "README.md carries spin-up, install, run and cloud-deploy steps"


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


def _deployed_urls() -> tuple[str, str] | None:
    """The two Cloud Run URLs, or ``None`` if there is no deployment to ask.

    Read from the platform rather than from a constant. A URL written into this
    file would keep answering after the service was deleted.
    """
    region = os.environ.get("PV_GCP_REGION", "us-east4")
    out: list[str] = []
    for service in ("provenance-web", "provenance-control-plane"):
        code, text = _cmd(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                service,
                "--region",
                region,
                "--format=value(status.url)",
            ],
            120,
        )
        url = text.strip().splitlines()[0].strip() if code == 0 and text.strip() else ""
        if not url.startswith("https://"):
            return None
        out.append(url)
    return out[0], out[1]


def _every_live_route_renders() -> tuple[Status, str]:
    """Sweep every route, preferring the DEPLOYED stack over localhost.

    The sweep used to target localhost only, so this assertion reported CANNOT
    RUN on any machine without ``make run-api`` and ``make run-web`` already up
    -- including CI, and including the machine that had just deployed. That is a
    CANNOT RUN caused by where the checker looked rather than by the state of
    the system, and it blocks the exit code exactly as a failure does.

    A deployed revision is also the stronger claim: localhost proves the code
    renders, and the ``.run.app`` host proves the thing a judge will open
    renders. So the deployment is preferred when there is one, and localhost is
    the fallback rather than the assumption.

    ``PV_SWEEP_WEB`` / ``PV_SWEEP_API`` override both, for a target that is
    neither.
    """
    web = os.environ.get("PV_SWEEP_WEB", "")
    api = os.environ.get("PV_SWEEP_API", "")
    target = "explicit"
    if not (web and api):
        found = _deployed_urls()
        if found is not None:
            web, api, target = found[0], found[1], "the deployed Cloud Run revision"
    argv = [sys.executable, "-m", "tools.route_sweep"]
    if web and api:
        argv += ["--web", web, "--api", api, "--warm"]
    else:
        target = "localhost"
    code, out = _cmd(argv, 1200)
    line = next((ln for ln in out.splitlines() if ln.startswith("swept ")), "")
    if code == 2:
        return (
            Status.CANNOT_RUN,
            f"the sweep could not run against {target}"
            + ("" if web else " (is `make run-api` and `make run-web` up?)"),
        )
    if code == -1:
        return Status.CANNOT_RUN, out.strip()[:110]
    detail = f"{line.strip() or 'no summary line'} — against {target}"
    return (Status.PASS if code == 0 else Status.FAIL), detail


CHECKS: tuple[Check, ...] = (
    # -- the three hard requirements, first, because nothing below matters if
    #    one of them is unmet: the entry is not judged at all.
    Check("Mandatory", "Gemini 3.5 or newer, proved by invocation", _gemini_version_floor),
    Check("Mandatory", "at least one Google agent framework, and used", _google_agent_framework),
    Check("Mandatory", "at least one Google Cloud service, deployed", _google_cloud_service),
    # -- the three required submission artifacts
    Check("Submission", "architecture diagram exists", _architecture_diagram),
    Check("Submission", "README carries step-by-step spin-up", _readme_spinup),
    Check(
        "Submission",
        "demo video, live and unedited, no longer than 4 minutes",
        _manual(
            "a human records and times it. The rules ask for unedited live execution and "
            "visual proof of Google Cloud deployment; `deploy/cloudrun.sh proof` prints what "
            "to film. ffprobe the file before uploading -- 4:00.0 is the limit, not a target."
        ),
    ),
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
    Check(
        "Sponsor",
        "CockroachDB remains canonical persistent memory",
        _superseded(
            "this hackathon has no CockroachDB requirement, and the check behind it was "
            "_kernel_is_sole_writer -- a claim about WHERE writes live, which would pass "
            "identically against SQLite or a dict. The database is still CockroachDB and "
            "db_ok on GET /v1/version is the live evidence; a Sponsor section screening the "
            "previous contest is the defect this file's header names."
        ),
    ),
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
