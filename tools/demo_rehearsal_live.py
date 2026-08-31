"""Drive the demo's beats against a DEPLOYED Provenance, and compute each verdict.

Why this exists as a tool rather than a shell one-off
-----------------------------------------------------
The first version of this rehearsal was a shell script that printed a ``PASS``
label beside each check. One of those labels sat next to a line reading
``git_sha equals HEAD: NO`` -- a hardcoded verdict beside a computed fact, in a
transcript whose entire purpose is to be believed. That is the vacuous-assertion
failure (``L-VAC``) this repository files defects about, committed inside the
artifact meant to demonstrate the opposite.

So no verdict here is written down. Every one is the return value of a
comparison, and :class:`Check` has no constructor that takes a bare status.

Three verdicts, not two
-----------------------
``PASS``, ``FAIL`` and ``CANNOT RUN``, and the third is load-bearing here for a
reason peculiar to a rehearsal: **two of the demo's beats must not be run.**
Firing the landlord trigger and ingesting the June invoice are the demo's two
reveals. Pressing them during a rehearsal would leave the recorded demo showing
a ``NO_OP`` and an already-ingested artifact -- the rehearsal would consume the
thing it was rehearsing. They are therefore ``CANNOT RUN`` with a stated reason,
which is not a failure and is never tallied as one (``D-00-005``).

What a PASS here does and does not mean
----------------------------------------
It means a deployed Cloud Run revision answered, over the public internet, from
the seeded CockroachDB cluster. It does not mean the beat will be *persuasive*;
no assertion can judge a narrative, and ``tools/demo_readiness`` already says so
about the same steps.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PASS: Final = "   PASS   "
FAIL: Final = "   FAIL   "
CANNOT: Final = "CANNOT RUN"


@dataclass(frozen=True, slots=True)
class Check:
    """One measured claim. ``status`` is never supplied by a caller."""

    label: str
    status: str
    detail: str

    @classmethod
    def measured(cls, label: str, *, held: bool, detail: str) -> Check:
        """PASS or FAIL, decided by *held*. There is no third argument."""
        return cls(label, PASS if held else FAIL, detail)

    @classmethod
    def cannot_run(cls, label: str, *, because: str) -> Check:
        """Nothing was measured. Must say what it waits on."""
        return cls(label, CANNOT, because)


def _get(url: str, token: str | None = None, timeout: int = 60) -> tuple[int, Any]:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, None
    except Exception as exc:
        return -1, {"transport_error": f"{type(exc).__name__}: {exc}"}


def _post(url: str, token: str | None, body: dict[str, Any], timeout: int = 60) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, None
    except Exception as exc:
        return -1, {"transport_error": f"{type(exc).__name__}: {exc}"}


def _fetch_text(url: str, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def _head_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(_REPO_ROOT), capture_output=True, text=True
        )
        return out.stdout.strip()
    except OSError:
        return ""


def rehearse(api: str, web: str, token: str | None) -> list[Check]:
    checks: list[Check] = []
    api = api.rstrip("/")
    web = web.rstrip("/")

    # -- disclosure ---------------------------------------------------------
    code, version = _get(f"{api}/v1/version")
    if code != 200 or not isinstance(version, dict):
        checks.append(
            Check.cannot_run(
                "GET /v1/version",
                because=f"the deployment did not answer ({code}); nothing below could be measured",
            )
        )
        return checks

    checks.append(
        Check.measured(
            "GET /v1/version is unauthenticated and discloses the mode",
            held=version.get("fixture_mode") is False and version.get("db_ok") is True,
            detail=(
                f"fixture_mode={version.get('fixture_mode')} db_ok={version.get('db_ok')} "
                f"agent_mode={version.get('agent_mode')} otlp={version.get('otlp_export')}"
            ),
        )
    )

    head = _head_sha()
    deployed = str(version.get("git_sha") or "")
    checks.append(
        Check.measured(
            "the deployed revision is this commit",
            held=bool(head) and deployed == head,
            detail=(
                f"deployed={deployed[:12] or '<none>'} head={head[:12] or '<unknown>'}"
                + ("" if deployed == head else "  -- redeploy, or the sha names a different tree")
            ),
        )
    )

    code, _ = _get(f"{api}/v1/cases")
    checks.append(
        Check.measured(
            "an unauthenticated read is refused",
            held=code == 401,
            detail=f"GET /v1/cases -> {code} (401 expected; a 200 would be a tenancy failure)",
        )
    )

    if not token:
        checks.append(
            Check.cannot_run(
                "every authenticated beat",
                because="no token. Mint one: python scripts/mint_local_token.py --quiet",
            )
        )
        return checks

    # -- beat A -------------------------------------------------------------
    code, dash = _get(f"{api}/v1/dashboard", token)
    if code == 200 and isinstance(dash, dict) and dash.get("contexts"):
        ctx = dash["contexts"][0]
        counts = dash.get("counts", {})
        money = (ctx.get("total_outstanding") or [{}])[0]
        checks.append(
            Check.measured(
                "beat A -- the dashboard is summed from Kernel-written rows",
                held=bool(money.get("amount")) and int(ctx.get("relationship_count") or 0) > 0,
                detail=(
                    f"{money.get('currency')} {money.get('amount')} across "
                    f"{ctx.get('relationship_count')} relationships, "
                    f"{ctx.get('open_case_count')} open of {ctx.get('case_count')} cases; "
                    f"triggers_armed={counts.get('triggers_armed')} "
                    f"active_conflicts={counts.get('active_conflicts')}"
                ),
            )
        )
        # `counts.get(...) or -1` is WRONG here and was, on the first run: zero
        # is falsy, so a legitimate `active_conflicts: 0` fell through to the
        # sentinel and reported FAIL for the exact state the demo requires. A
        # guard against a MISSING key must not also fire on a real zero -- the
        # same "absence is not emptiness" confusion this repository files
        # defects about, inverted. `is None` distinguishes them; `or` cannot.
        seeded_conflicts = counts.get("active_conflicts")
        checks.append(
            Check.measured(
                "beat A -- no conflict is seeded",
                held=seeded_conflicts == 0,
                detail=(
                    f"active_conflicts={seeded_conflicts if seeded_conflicts is not None else 'ABSENT'}"
                    " -- 0 is required. A seeded conflict would mean the reveal had already "
                    "happened before anyone pressed anything, and an ABSENT count is not a zero."
                ),
            )
        )
    else:
        checks.append(
            Check.cannot_run("beat A -- the dashboard", because=f"GET /v1/dashboard -> {code}")
        )

    # -- beat C -------------------------------------------------------------
    code, cases = _get(f"{api}/v1/cases", token)
    case_id = None
    if code == 200 and isinstance(cases, dict) and cases.get("items"):
        case_id = cases["items"][0]["case_id"]
    if case_id:
        code, proof = _get(f"{api}/v1/cases/{case_id}/state-proof", token)
        if code == 200 and isinstance(proof, dict):
            checks.append(
                Check.measured(
                    "beat C -- the State Proof is assembled by SQL, not by a model",
                    held=proof.get("deterministic") is True and proof.get("model_used") is None,
                    detail=(
                        f"deterministic={proof.get('deterministic')} "
                        f"model_used={proof.get('model_used')} "
                        f"revision={proof.get('case_revision')} "
                        f"beliefs={len(proof.get('beliefs') or [])} "
                        f"commitments={len(proof.get('commitments') or [])}"
                    ),
                )
            )
            grounded = [b for b in (proof.get("beliefs") or []) if b.get("grounding")]
            checks.append(
                Check.measured(
                    "beat C -- every rendered belief carries grounding",
                    held=len(grounded) == len(proof.get("beliefs") or []),
                    detail=f"{len(grounded)} of {len(proof.get('beliefs') or [])} beliefs grounded",
                )
            )
        else:
            checks.append(
                Check.cannot_run("beat C -- the State Proof", because=f"state-proof -> {code}")
            )
    else:
        checks.append(Check.cannot_run("beat C -- the State Proof", because="no case to read"))

    # -- beat F -------------------------------------------------------------
    code, triggers = _get(f"{api}/v1/triggers", token)
    armed = (
        [t for t in triggers.get("items", []) if t.get("state") == "ARMED"]
        if code == 200 and isinstance(triggers, dict)
        else []
    )
    checks.append(
        Check.measured(
            "beat F -- prospective memory is armed",
            held=len(armed) > 0,
            detail="; ".join(f"{t['trigger_type']} not_before={t['not_before']}" for t in armed)
            or f"GET /v1/triggers -> {code}",
        )
    )

    # The HANDLER must run, not merely the router. An unknown id proves that:
    # a router miss cannot produce this code.
    code, body = _post(f"{api}/v1/triggers/00000000-0000-7000-8000-00000000dead/wake", token, {})
    err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
    checks.append(
        Check.measured(
            "beat F -- the wake route reaches its handler",
            held=code == 404
            and err.get("code") == "TRIGGER_NOT_FOUND"
            and bool(err.get("trace_id")),
            detail=(
                f"POST .../wake with an unknown id -> {code} {err.get('code')}; "
                "a router miss would carry no trace_id and a different code"
            ),
        )
    )
    checks.append(
        Check.cannot_run(
            "beat F -- firing the landlord trigger",
            because=(
                "deliberate. It is the demo's second reveal, and the first press disarms "
                "it -- pressing it here would leave the recorded demo showing a NO_OP."
            ),
        )
    )

    # -- the web app, rendered ---------------------------------------------
    #
    # This checks the WEB APP, not the API, and the distinction is the whole
    # reason it exists.
    #
    # Every check above authenticates with a token this process just minted. The
    # deployed web app authenticates with the token baked into its own revision
    # at deploy time, and those are different tokens with different expiries. On
    # 2026-08-30 the deployed one had been expired for fifty hours: the API was
    # healthy, every check above passed, and the site a judge would open
    # returned HTTP 200 while rendering 401 and no money.
    #
    # That is the failure the README warns about -- PV_API_BASE_URL set and
    # PV_API_TOKEN bad gives a wall of error states rather than the fixture
    # banner -- and the rehearsal could not see it, because it was testing with
    # its own token instead of the deployed one. A fixture and a type written by
    # the same hand agree with each other and prove nothing; so do a rehearsal
    # and an API that share a credential the real client does not use.
    #
    # So this asserts on the RENDERED BYTES of the page, fetched anonymously the
    # way a judge fetches it.
    if web:
        code, html = _fetch_text(f"{web}/dashboard")
        money = re.findall(r"USD\s[\d,]+\.\d{2}", html)
        banner = "no Provenance API is connected" in html
        # Strip the build stamp before scanning for error text: the git sha is
        # 40 hex characters and reliably contains "401" by coincidence.
        scrubbed = re.sub(r"[0-9a-f]{40}", "", html)
        errored = bool(re.search(r"401|UNAUTHENTICATED", scrubbed))
        checks.append(
            Check.measured(
                "the deployed WEB APP renders live data with the token it actually holds",
                held=code == 200 and bool(money) and not banner and not errored,
                detail=(
                    f"GET {web}/dashboard -> {code}; "
                    f"money rendered: {sorted(set(money))[:3] or 'NONE'}; "
                    f"fixture banner: {banner}; unauthenticated-render: {errored}"
                    + (
                        ""
                        if money and not errored
                        else "  -- re-mint PV_API_TOKEN and update the web service"
                    )
                ),
            )
        )
    else:
        checks.append(Check.cannot_run("the deployed web app", because="no --web URL was given"))

    # -- beats B/D, E, G ----------------------------------------------------
    #
    # The reason string below is itself a claim about another file, and it used
    # to be a false one. It read "the path is proved in
    # ops/ingestion-live-run.txt, which now passes every step" while that
    # transcript ended on a FAIL (the app-side memory_proposals INSERT, refused
    # by ck_memory_proposals_model) and a CANNOT RUN (commit_proposal, with no
    # proposal row to decide). Because the sentence was hardcoded, every
    # re-recorded rehearsal re-emitted it, and re-recording was exactly what
    # made it look freshly measured. Nothing here opens that transcript, so
    # nothing here can notice when the two drift apart: the only defence is to
    # say no more than the transcript actually shows.
    #
    # The ingest was re-run on 2026-08-31 against the migrated cluster and the
    # text below was rewritten from the new transcript, which is what the
    # previous version of this comment asked whoever re-ran it to do. Do the
    # same next time: read the file, do not describe what the re-run was
    # expected to produce.
    checks.append(
        Check.cannot_run(
            "beats B and D -- ingest the June invoice, then the counterfactual on it",
            because=(
                "deliberate. northline-june-invoice.eml is absent from source_artifacts "
                "because the demo ingests it live to create the conflict. "
                "ops/ingestion-live-run.txt measures that path against the cluster and "
                "was re-recorded on 2026-08-31, after migration 0009a: PASS 13, FAIL 0, "
                "CANNOT RUN 1. Every step now passes through the app-side "
                "memory_proposals INSERT, which the earlier transcript recorded as a "
                "FAIL because ck_memory_proposals_model did not yet admit the Gemini "
                "model ids. The single CANNOT RUN is commit_proposal, and its reason is "
                "structural rather than a blocker: the Kernel commits its own "
                "transaction, this runner rolls back, so a Kernel decision would "
                "outlive the proposal row it decided. commit_proposal has therefore "
                "still never been measured on a live path."
            ),
        )
    )
    checks.append(
        Check.cannot_run(
            "beat E -- approve and send",
            because=(
                "no action_intents row exists to approve. internal.create_action_intent is "
                "the only unbound method in the action plane; approve, reject, "
                "execute_action and both reads are bound. Not attempted, so not a FAIL."
            ),
        )
    )
    checks.append(
        Check.cannot_run(
            "beat G -- the Memory Trace",
            because=(
                "the trace assembler is unbuilt. read.get_trace and read.memory_trace "
                "answer 501 NOT_IMPLEMENTED naming the subsystem, never 500."
            ),
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--web", default="")
    parser.add_argument("--token", default="")
    args = parser.parse_args(argv)

    checks = rehearse(args.api, args.web or args.api, args.token or None)

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("=" * 78)
    print("Provenance -- demo rehearsal against a DEPLOYED Cloud Run revision")
    print(f"  run    : {stamp}")
    print(f"  commit : {_head_sha()}")
    print(f"  api    : {args.api}")
    if args.web:
        print(f"  web    : {args.web}")
    print("  Every verdict below is computed. There is no code path that writes")
    print("  a PASS without a comparison returning True.")
    print("=" * 78)
    print()
    for check in checks:
        print(f"  [{check.status}] {check.label}")
        print(f"               {check.detail}")
    passed = sum(1 for c in checks if c.status == PASS)
    failed = sum(1 for c in checks if c.status == FAIL)
    cannot = sum(1 for c in checks if c.status == CANNOT)
    print()
    print("=" * 78)
    print(f"  PASS {passed}   FAIL {failed}   CANNOT RUN {cannot}")
    print("  CANNOT RUN is neither. Four of them here are deliberate: they are the")
    print("  demo's own reveals, and rehearsing them would spend them.")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
