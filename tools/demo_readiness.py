"""Can the dress rehearsal run right now, and if not, where does it stop?

Authority: ``docs/ops/41_RUNBOOK.md`` section 8.1, which lists twelve steps.

Why this is a readiness check and not the rehearsal
---------------------------------------------------
Section 8.1's step 1 is ``make demo-reset && make seed && make db-verify``. That
destroys the demo database and rebuilds it, and the ANN index build alone is
roughly **55 minutes**. A tool you run to find out whether you are ready must
not cost an hour and must not consume the demo it is checking — so nothing here
writes, resets or ingests. It reads the current state and reports, per step,
whether that step could run.

The distinction that matters
----------------------------
``BLOCKED`` names a capability that does not exist yet. ``NOT READY`` means the
capability exists but the world is not in the right state (a server is down, the
corpus has already been consumed). They call for opposite responses: one is
someone else's build, the other is a thing you can fix in a minute. Collapsing
them into "failed" would send a reader to the wrong place.

Steps 5 through 9 and 11 depend on port methods that are still unbound. Rather
than assert that from memory, each is checked against the live ``UNBOUND``
register, so this file cannot claim a step is blocked after the blocker is
cleared.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

API = "http://127.0.0.1:8080"
WEB = "http://localhost:3000"

READY = "READY"
NOT_READY = "NOT READY"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Step:
    number: int
    what: str
    status: str
    detail: str


def _unbound() -> dict[str, str]:
    from services.control_plane.app.api.adapters.unbound import UNBOUND

    return dict(UNBOUND)


def _token() -> str | None:
    done = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "mint_local_token.py"), "--quiet"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    return done.stdout.strip() or None if done.returncode == 0 else None


def _get(url: str, token: str | None = None, timeout: int = 20) -> tuple[int, object]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _needs(register: dict[str, str], *keys: str) -> tuple[str, str] | None:
    """``(status, detail)`` if any of *keys* is still unbound, else ``None``."""
    missing = [key for key in keys if key in register]
    if not missing:
        return None
    key = missing[0]
    reason = register[key].split(".")[0]
    return BLOCKED, f"needs {', '.join(missing)} - {reason[:96]}"


def assess() -> list[Step]:
    register = _unbound()
    token = _token()
    steps: list[Step] = []

    # 1 - reset, seed, verify. Never run here; report whether it could be.
    seed_ok = (_REPO_ROOT / "db" / "seeds" / "MANIFEST.json").exists()
    steps.append(
        Step(
            1,
            "demo-reset, seed, db-verify",
            READY if seed_ok else NOT_READY,
            "the seed manifest is present; NOT run here (it destroys the demo corpus, ~55min)"
            if seed_ok
            else "db/seeds/MANIFEST.json is missing",
        )
    )

    # 2 - warm-up.
    code, _ = _get(f"{API}/v1/healthz")
    steps.append(
        Step(
            2,
            "warm-up: healthz, version, me",
            READY if code == 200 else NOT_READY,
            f"GET /v1/healthz -> {code}" + ("" if code == 200 else "; start `make run-api`"),
        )
    )

    # 3 - live mode.
    code, version = _get(f"{API}/v1/version")
    if code != 200 or not isinstance(version, dict):
        steps.append(Step(3, "assert live mode", NOT_READY, f"GET /v1/version -> {code}"))
    else:
        fixture = version.get("fixture_mode")
        db_ok = version.get("db_ok")
        good = fixture is False and db_ok is True
        steps.append(
            Step(
                3,
                "assert live mode",
                READY if good else NOT_READY,
                f"fixture_mode={fixture} db_ok={db_ok} agent_mode={version.get('agent_mode')}",
            )
        )

    # 4 - dashboard state before anything is uploaded.
    if token is None:
        steps.append(Step(4, "dashboard opening state", NOT_READY, "could not mint a token"))
    else:
        code, dash = _get(f"{API}/v1/dashboard", token)
        if code != 200 or not isinstance(dash, dict):
            steps.append(
                Step(4, "dashboard opening state", NOT_READY, f"GET /v1/dashboard -> {code}")
            )
        else:
            contexts = dash.get("contexts") or [{}]
            total = (contexts[0].get("total_outstanding") or [{}])[0]
            amount = f"{total.get('currency', '?')} {total.get('amount', '?')}"
            rels = contexts[0].get("relationship_count")
            steps.append(
                Step(
                    4,
                    "dashboard opening state",
                    READY,
                    f"{rels} relationships in scope, {amount} outstanding",
                )
            )

    # 5-9, 11 - the capability-gated steps.
    gated = (
        (5, "upload the ISP invoice through the real UI path", ("internal.ingest_artifact",)),
        (6, "assert REOPENED, revision 12 -> 13, conflict row", ("internal.ingest_artifact",)),
        (7, "State Proof shows SUPPORTS and CONTRADICTS", ()),
        (
            8,
            "counterfactual: memory_off vs memory_on",
            ("write.start_counterfactual", "write.get_counterfactual"),
        ),
        (
            9,
            "approve and send; executor revalidates revision 13",
            ("internal.create_action_intent",),
        ),
        (11, "Memory Trace: >=3 MCP calls, all READ_ONLY", ("read.memory_trace",)),
    )
    for number, what, keys in gated:
        blocked = _needs(register, *keys) if keys else None
        if blocked:
            steps.append(Step(number, what, blocked[0], blocked[1]))
        elif number == 7:
            # State Proof is bound; it needs step 6 to have produced the conflict.
            steps.append(
                Step(
                    7,
                    what,
                    NOT_READY,
                    "the CONTRADICTS edge only exists after step 5 ingests the invoice",
                )
            )
        else:
            steps.append(Step(number, what, READY, "the ports it needs are bound"))

    # 10 - trigger wake. The port is bound; the route is a separate decision.
    blocked = _needs(register, "internal.evaluate_trigger")
    if blocked:
        steps.append(Step(10, "wake the landlord trigger", blocked[0], blocked[1]))
    else:
        steps.append(
            Step(
                10,
                "wake the landlord trigger (NO_OP then FIRED)",
                NOT_READY,
                "internal.evaluate_trigger is bound, but section 8.0's 31-route index has no "
                "public wake route, so there is no manual-wake entry point to drive it from",
            )
        )

    # 12 - reset.
    steps.append(Step(12, "reset so the demo is not left half-consumed", READY, "make demo-reset"))
    return sorted(steps, key=lambda s: s.number)


def main() -> int:
    print("Dress-rehearsal readiness - ops/41_RUNBOOK.md section 8.1\n")
    print("  Nothing here writes, resets or ingests: step 1 alone would destroy the demo")
    print("  corpus and take about 55 minutes, and a readiness check must not cost that.\n")

    steps = assess()
    width = max(len(s.status) for s in steps)
    for step in steps:
        print(f"  {step.number:>2}. [{step.status:^{width}}] {step.what}")
        print(f"       {step.detail}")

    ready = sum(1 for s in steps if s.status == READY)
    not_ready = [s for s in steps if s.status == NOT_READY]
    blocked = [s for s in steps if s.status == BLOCKED]

    print(f"\n  READY {ready}   NOT READY {len(not_ready)}   BLOCKED {len(blocked)}")
    if blocked:
        print(
            f"\n  {len(blocked)} steps need a capability that does not exist yet. That is a build\n"
            "  task, not a setup task, and no amount of restarting will change it."
        )
    if not_ready:
        print(
            f"\n  {len(not_ready)} steps could run once the world is in the right state - a server to\n"
            "  start, a corpus to reset. These are minutes, not builds."
        )
    return 0 if not blocked and not not_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
