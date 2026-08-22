# Pre-submission gate — Stage One pass/fail

> **Status: NOT RUN — phase not started.** Instantiated by task `T0.3` from
> `docs/quality/23_PHASE_GATES.md` §24. This file is the signed `S1`–`S10`
> battery; `ops/gates/PHASE_15.md` is the phase report that points at it.

The CockroachDB x AWS Hackathon — Build with Agentic Memory — screens on Stage One items
**before** any judging happens on the five equally weighted criteria (Agentic Memory
Design, Technological Implementation, Real-World Impact, Product Readiness, Creativity &
Originality). Stage One is binary. Every item below is PASS or FAIL **with pasted
output**.

> **Run this battery twice: once 24 hours before the deadline, and once within 2 hours
> of submitting.** The first run finds the problems; the second run proves nothing
> rotted. Both runs are recorded below, side by side, because "it passed yesterday" is
> not a claim about the submitted artifact.

Submitted commit: `<full sha>`
Demo URL: `<url>`   Repository URL: `<url>`   Video URL: `<url>`
Run 1 (T-24h): `<ISO8601>`   Run 2 (T-2h): `<ISO8601>`
Signed by: `<name>`   Verdict: `PASS | FAIL`

## Results

| ID | What it screens | Run 1 (T-24h) | Run 2 (T-2h) | Log |
|---|---|---|---|---|
| S1 | public repository, verified anonymously | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S2 | Apache-2.0 licence, SPDX-detected, `NOTICE` present | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S3 | functional demo URL from outside the build network; `fixture_mode: false` | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S4 | video strictly under 180.0 seconds and publicly viewable | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S5 | >= 2 CockroachDB tools, genuinely used, each with a stated degradation | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S6 | >= 1 AWS service, evidenced by a real trace on the demo path | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S7 | tool-usage disclosure and the seeded-vs-computed table | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S8 | no secret has ever been public — repository and gate logs | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S9 | a stranger can run it; setup time recorded honestly | NOT RUN — phase not started | NOT RUN — phase not started | — |
| S10 | the demo survives a full reset; re-run S3 afterwards | NOT RUN — phase not started | NOT RUN — phase not started | — |

## S1 — public repository

```
<gh repo view --json visibility,url,pushedAt -q '.visibility + " " + .url'>
<curl -sS -o /dev/null -w '%{http_code}\n' "https://github.com/<org>/provenance">
```

Verified as an **anonymous** client, not as the authenticated owner. `T0.2` records
whether the repository is intentionally private until submission; if it was, this is the
gate that closes that debt.

## S2 — Apache-2.0 licence

```
<gh api "repos/<org>/provenance/license" -q .license.spdx_id>
<head -3 LICENSE>
<grep -rn "Copyright" NOTICE | head -1>
```

## S3 — functional demo URL

```
<curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' "$PV_WEB">
<curl -sS "$PV_API/v1/version" | jq '{git_sha, fixture_mode, agent_mode, schema_revision}'>
<npx playwright test e2e/judge_login.spec.ts --project=clean-profile --reporter=line>
<PV_WEB=https://<public> npx playwright test e2e/hero_flow.spec.ts --reporter=line>
```

`fixture_mode == false`. A `true` here **invalidates the submission** (§23.12). `GET
/v1/version` is unauthenticated so a judge can `curl` it, and it is the single
authoritative disclosure channel; `/v1/healthz` stays a bare liveness probe. Record the
`trace_id` from the hero-flow run:

Trace id: `<trace_id>`

## S4 — demo video

```
<ffprobe -v error -show_entries format=duration -of csv=p=0 demo/provenance-demo.mp4>
<curl -sS -o /dev/null -w '%{http_code}\n' "<public video url>">
```

Duration, in seconds, recorded exactly: `<n>`. Under 180.0 or it is a FAIL.
Watched end to end in a private window by someone who did not edit it: `yes | no` — `<who>`.

## S5 — at least two CockroachDB tools, genuinely used

```
Tool 1 — Distributed Vector Indexing
<cockroach sql --url "$PV_DB_APP" -e "EXPLAIN <the live retrieval query>;" | grep evidence_embedding_ann_idx>

Tool 2 — CockroachDB Cloud Managed MCP Server
<curl … /memory-trace | jq '[.mcp_tool_calls[] | {view, sql_role}] | length'>

Tool 3 — ccloud CLI, used for provisioning
<head -5 ops/cluster-provision.txt>
```

**The genuineness test.** For each tool, state what breaks when it is removed:

| Tool | What breaks without it |
|---|---|
| Vector index | Retrieval degrades to a brute-force partition scan; the hero invoice still resolves but latency rises and the approach does not scale. |
| MCP | The Interpreter loses its governed case-context read and falls back to the control-plane endpoint; the Memory Trace shows the degradation (`G11.7`). |
| `ccloud` | The cluster cannot be reprovisioned from scratch. |

`S5`'s genuineness test also requires the cluster to be reprovisionable from
`ops/cluster-provision.txt`. If that file records a `describe` rather than a `create`,
**say so in the file and say so here** (`T0.5`).

## S6 — at least one AWS service

```
<aws logs start-query … | stats count() by span_name>
<cdk list>
```

Services on the critical demo path: Bedrock (AgentCore Runtime + Titan embeddings),
Cognito, S3, App Runner, EventBridge + Scheduler, SQS, SES, CloudWatch, Amplify Hosting.
Evidence, not a list.

## S7 — tool-usage disclosure

```
<grep -n "## Tool usage disclosure" -A 40 SUBMISSION.md>
<grep -n "## What is seeded vs what is computed" -A 30 README.md>
```

The disclosure names every AI tool used to **build** Provenance and, separately, the
**runtime** models. Record the runtime ids actually in force at submission — per
`CANONICAL_DECISIONS.md` → *Bedrock model id canon*, Anthropic chat models are invoked by
inference-profile id, and if the build ships on `us.anthropic.claude-opus-4-6-v1` because
the Opus 5 grant never landed (`D-00-004`), then `SUBMISSION.md` and the README say so.
**Claiming Opus 5 while running 4.6 is the exact dishonesty this pack exists to prevent.**

The seeded-vs-computed table is not optional and not a formality: 18,000 decoy evidence
rows are synthetic and seeded; the 32 hero evidence items are hand-curated and seeded; the
conflict, the reopen, the revision increment, the trigger evaluation and the draft are
**computed at demo time**. Stating this plainly is worth more than hoping nobody asks.

## S8 — no secret has ever been public

```
<gitleaks detect --source . --redact --no-banner --exit-code 1>
<gitleaks detect --source ops/gates --redact --no-banner --exit-code 1>
```

Two scans, not one. `ops/gates/` is scanned separately because it is the directory this
build deliberately fills with command transcripts; `tools/scrub.py` is the first filter
and this is the second.

## S9 — a stranger can run it

Clone the public repository, follow `README` § Setup, reach a running local stack. Timed,
by someone who did not build it.

- Who: `<name>`
- Wall-clock time: `<minutes>` (target < 30)
- Where they got stuck: `<verbatim>`

Record the real number even if it is 90. An honest number is useful and an aspirational
one is not.

## S10 — the demo survives a full reset

Run this **last**, then re-run S3.

```
<make demo-reset && make seed && make db-verify>
```

Expected: `V1 0 … V10 0  V11 3`. Then re-run the S3 hero flow. If the demo only works on
a database that has already been demoed on, it does not work.

Re-run of S3 after reset: `<result>`

## §24.1 — submission checklist, condensed

- [ ] Repository public and anonymously reachable (S1)
- [ ] Apache-2.0 `LICENSE` present, SPDX-detected (S2)
- [ ] Demo URL returns 200 from an outside network; judge login works from a clean profile (S3)
- [ ] `fixture_mode: false` in the recorded demo (S3)
- [ ] Video strictly under 180.0 seconds and publicly viewable (S4)
- [ ] >= 2 CockroachDB tools with evidence **and** a stated degradation if removed (S5)
- [ ] >= 1 AWS service, evidenced by a real trace on the demo path (S6)
- [ ] Tool-usage disclosure and the seeded-vs-computed table (S7)
- [ ] Clean secret scan on the repository and on the gate logs (S8)
- [ ] Third-party setup timed and recorded honestly (S9)
- [ ] Full demo reset, reseed, re-verify, re-run the hero flow (S10)
- [ ] `ops/gates/PHASE_00.md` … `PHASE_15.md` all present, all SIGNED, carried debt listed
- [ ] `G14.2` `INVARIANT VIOLATIONS: 0`, output pasted
- [ ] `make debt --assert-empty` passes (`72_DEFECT_PROTOCOL.md` §9.4)
- [ ] `python -m tools.defect_lint` exits 0 and reports `OPEN BLOCKER: 0  OPEN MAJOR: 0`
