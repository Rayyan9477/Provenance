# Provenance

**A system of record for the institutions that already have one of you.**

[![Licence](https://img.shields.io/badge/licence-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-3776AB.svg)](pyproject.toml)
[![Next.js](https://img.shields.io/badge/web-Next.js%2015-000000.svg)](apps/web)
[![CockroachDB](https://img.shields.io/badge/database-CockroachDB-6933FF.svg)](db/migrations)
[![Cloud Run](https://img.shields.io/badge/deployed-Cloud%20Run%20·%20us--east4-4285F4.svg)](https://provenance-web-vaq74wztva-uk.a.run.app)

[**Live app**](https://provenance-web-vaq74wztva-uk.a.run.app) ·
[**API**](https://provenance-control-plane-vaq74wztva-uk.a.run.app/v1/version) ·
[**How it works**](docs/HOW_IT_WORKS.md) ·
[**Architecture**](docs/diagrams/architecture.md) ·
[**Spin-up**](#spin-up)

---

## The asymmetry

Institutions keep durable, structured, adversarially-useful records about
people. People keep nothing comparable about institutions.

Your internet provider knows your account number, your service address history,
every billing period, the policy version in force on the day you called, the
ticket id of that call, and the retention schedule governing all of it. That
record survives staff turnover, system migrations, and the four months during
which you thought about none of it. Your side of the same relationship is a mail
archive you cannot search by obligation, a screenshot you took because you had a
feeling, and a memory that degrades on a predictable curve. When the two records
disagree, only one of them is written down in a form that can be cited.

The price is paid in tail obligations: the $1,800 deposit promised "within 30
days of inspection" that quietly was not returned, the $420 reimbursement that
arrived at $200, the cancellation confirmed in writing on 15 May and billed again
for June. Each is worth a few hundred dollars and roughly four hours of
reconstruction — which is exactly why they go unresolved. The problem is
evidentiary, not motivational.

**Provenance is the other side of that ledger.** It maintains one thing: your
open obligations with counterparties, held as versioned beliefs grounded in
immutable evidence, projected into transactional state, with prospective
triggers so that a deadline passing is itself an event.

When a forwarded invoice arrives four months after a confirmed termination,
Provenance does not summarise it. It admits it as immutable evidence, types it
as a *counterparty claim* rather than a fact, detects that it is mutually
exclusive with the canonical `service_terminated` belief, reopens the closed case
in one serializable transaction, and drafts a reply whose every factual sentence
carries a support id.

**The record that makes it cheap to be right.**

---

## See it running

- **Web app** — [https://provenance-web-vaq74wztva-uk.a.run.app](https://provenance-web-vaq74wztva-uk.a.run.app)
- **API** — [https://provenance-control-plane-vaq74wztva-uk.a.run.app](https://provenance-control-plane-vaq74wztva-uk.a.run.app)

Two Cloud Run services in `provenance-agentic-2026` / `us-east4`, reaching the
seeded CockroachDB cluster. **There is nothing to sign into.** The web app holds
its API token server-side — `PV_API_TOKEN` carries no `NEXT_PUBLIC_` prefix and
every read runs in a server component — so the URL opens straight onto the
dashboard with real data behind it. Click it and you are in.

The API can be checked without a credential, because `GET /v1/version` is
unauthenticated on purpose:

```bash
curl -s https://provenance-control-plane-vaq74wztva-uk.a.run.app/v1/version
# fixture_mode: false   db_ok: true   agent_mode: LIVE   + the git_sha of the running revision
```

**Read `db_ok`, not the status code.** Startup deliberately survives a refused
database pool and reports the refusal rather than crash-looping, so a `200`
alone proves only that the process is up.

### What to look at, in order

1. **The dashboard, "The Move"** — the landing screen, and the one number to
   carry into everything else: **`USD 2,020.00` outstanding across 4
   relationships**. What matters is where that figure comes from.
   `GET /v1/dashboard` sums it from rows the Memory Kernel wrote, and the screen
   performs no arithmetic of its own. There is no fixture banner, and its
   absence is the claim: the web app runs in FIXTURE mode behind a permanent,
   non-dismissible banner whenever `PV_API_BASE_URL` is unset, and there is no
   third mode in which a fixture is served without saying so.

2. **The hero case — the Harborview deposit.** The largest line on that
   dashboard is a security deposit of **`USD 1,800.00`** that Harborview
   Property Management promised "within 30 days" of the 16 May 2026 final
   inspection, due 15 June 2026, and did not return. Open it from the attention
   list. The case carries a revision number, an `ACTIVE` commitment with
   `USD 0.00` fulfilled against it, and a `COMMITMENT_DEADLINE` trigger **armed**
   at `2026-06-15T00:01:00Z` — in this system a deadline passing is itself an
   event, not a report somebody has to remember to ask for.

3. **The State Proof on that case** — why Provenance believes what it says.
   Beliefs with their grounding edges, the commitment, the lineage, the
   revision the proof was taken at. The field to look at is `model_used: null`.
   The proof is assembled by SQL over persisted rows, so it is deterministic and
   reproducible; nothing in it was produced by asking a model to explain itself.

4. **Judge Mode** — and specifically the panel that refuses to render. The
   Memory Trace answers `501 NOT_IMPLEMENTED` and names the trace assembler it
   is waiting on. It is worth thirty seconds, because it is what every unbuilt
   capability looks like here. A read method with no backing that returned `[]`
   would render as "memory did nothing on this case" — indistinguishable from a
   real empty result, and believable enough that nobody investigates.

5. **The counterfactual screen** — the same rule from the other side. Live, it
   says *"No counterfactual has been run"* and stops, because creating one is a
   mutation this build's read path does not perform and a specimen comparison
   would be indistinguishable from a real one. The two runs that did happen ran
   against live models and are recorded in
   [`ops/counterfactual-live-run.txt`](ops/counterfactual-live-run.txt) and
   [`ops/counterfactual-live-run-2.txt`](ops/counterfactual-live-run-2.txt),
   with `parity.all_equal` true — same artifact, model, prompt and graph on both
   sides.

### What works, and what does not

The full seven-step walkthrough is written out in [`STATUS.md`](STATUS.md).

The **Live?** column below is written by hand. It is a reading of the evidence,
not output, and it carries the risk any hand-written verdict carries — which is
worth saying here rather than claiming a computation that did not happen. What
*is* computed is the transcript it summarises: `python -m tools.demo_rehearsal_live`
runs against the deployed revision and has no constructor that accepts a bare
`PASS`. Check the column against the transcript rather than trusting it. It is
[`ops/demo-rehearsal-live-cloudrun.txt`](ops/demo-rehearsal-live-cloudrun.txt) —
`PASS 10 | FAIL 0 | CANNOT RUN 4`.

| Step | What you see | Live? |
|---|---|---|
| A | The dashboard: "The Move", 4 relationships, `USD 2,020.00` outstanding | **yes** |
| B | Forward the June invoice; the closed case flips `RESOLVED` → `REOPENED` | **not pre-run, deliberately.** It is the reveal, and rehearsing it spends it |
| C | The State Proof: grounding and lineage, `model_used: null` | **yes** |
| D | Counterfactual: memory off against memory on | **not on screen.** It wants step B's invoice, so it follows B; two live runs are recorded in `ops/` |
| E | Approve and send the drafted reply | **no — blocked.** `internal.create_action_intent` is unbound, so no `action_intents` row exists to approve |
| F | The landlord trigger fires on its own | **armed and reachable, not fired.** The first press disarms it |
| G | The Memory Trace | **no — unbuilt.** The trace assembler does not exist |

Five of the seven are not shown live, and they are not the same kind of gap.
**D and F work and are deliberately being held back** — the first press of the
landlord trigger disarms it, and rehearsing a reveal spends it. **B is held back
too, but its last live run is stale rather than green**, which is a weaker claim
and the paragraph below makes it. **E and G do not work**: one has an unbound
method under it, the other has no subsystem under it at all. Reporting those
three kinds of absence with the same word is the failure this README is
organised to avoid.

Step B's path is exercised **as far as the Kernel's door** in
[`ops/ingestion-live-run.txt`](ops/ingestion-live-run.txt), a rollback run that
kept nothing: **PASS 13, FAIL 0, CANNOT RUN 1**, re-recorded on 2026-08-31.
Every step from `write.upload_intent` through the typed `MemoryProposal` to the
app-side `memory_proposals` INSERT was accepted by a real CockroachDB — every
`CHECK`, every foreign key, every generated column — and rolled back.

That file used to end on a `FAIL`. The INSERT was refused by
`ck_memory_proposals_model`, a pre-pivot constraint that did not admit the
Gemini model ids; migration `0009a` widened it on 2026-08-28 and the transcript
simply was not regenerated for three days, so this README claimed a schema
change was "waiting on a human's authorisation" that had already happened. It
has been re-run, and the INSERT passes.

**`commit_proposal` — the Kernel's own call, the one the whole thesis rests on —
has still never run on a live path.** It is `CANNOT RUN` here for a structural
reason rather than a blocked one: the Kernel commits its own transaction and
this runner rolls back, so a Kernel decision would outlive the proposal row it
decided. `--persist` would settle it and would also spend step B, which is the
reveal. That is the honest edge of what is proved: the path is measured to the
Kernel's door, and no further.

For the shape of the surface rather than the story, `make route-sweep` reports
**50 routes discovered, 0 broken**, run against this deployed revision rather
than against localhost
([`ops/route-sweep-live-cloudrun.txt`](ops/route-sweep-live-cloudrun.txt)).

---

## Platform and stack

| Layer | Choice |
|---|---|
| **Reasoning models** | **`gemini-3.6-flash`** (Tier R — semantic resolution, contradiction characterisation, attention assessment, advocacy drafting) and **`gemini-3.5-flash-lite`** (Tier E — extraction, classification, bulk structured output), via the **Gemini Developer API**. Capacity fallback `gemini-3.7-flash`. |
| **Agent SDK** | **`google-genai`** — the GenAI SDK, pinned and verified at **1.60.0**. |
| **Compute** | **Cloud Run**, deployed and serving. Two services in `us-east4`. `agent-runtime` runs in-process inside the control plane, so there are two containers rather than three. |
| **Canonical database** | **CockroachDB Cloud** — 26 tables, 5 agent-safe views, 5 SQL roles, migrated and seeded with 18,035 evidence rows. |
| **Web** | Next.js 15 App Router. Every data read runs in a server component; the API token is never exposed to the browser. |
| **Architecture diagram** | [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) — five diagrams. |

### Notes on the model choices

Tier R was `gemini-3.7-flash` until 2026-08-31, when that id began answering
`503 UNAVAILABLE — this model is currently experiencing high demand` to 3 of 3
two-word prompts while `gemini-3.6-flash` answered 3 of 3. Both are PROBED in
the same transcript, so the swap was the one-line environment change the
fallback was declared to make possible.

Both tiers are Flash-class deliberately. `gemini-3.1-pro-preview` is the only
Pro model on the Developer API, and at version 3.1 it sits below the 3.5 floor
set in [`docs/CANONICAL_DECISIONS.md`](docs/CANONICAL_DECISIONS.md) → *Gemini
model id canon*.

`gemini-embedding-2` is **probed and canonical but not in use**: the 18,035
corpus vectors are Titan at `VECTOR(1024)`, and migration `0009`, which widens
the column for it, is deliberately unapplied.

**Every one of these ids is PROBED**, by invocation rather than by listing:
`python ops/probes/gemini_probe.py` exits 0 at `PASS 11 | FAIL 0 | CANNOT RUN
0`, transcript at [`ops/gemini-probe.txt`](ops/gemini-probe.txt).
`gemini-embedding-2` returns 1536 dimensions at L2 norm **1.0000003**;
`gemini-embedding-001` returns **0.6935943** in the same run.

---

## The architecture in one paragraph

Untrusted evidence is interpreted by Gemini agents into typed `MemoryProposal`
objects; a **deterministic Memory Kernel** validates those proposals and
atomically commits versioned beliefs, obligations, state transitions and outbox
records in one serializable CockroachDB transaction; the outbox then drives
asynchronous reaction under explicit human authorisation.

The load-bearing claim is the second clause. **The Memory Kernel is the only
thing in this system that writes canonical state.** No agent holds a SQL write
credential — not a restricted one, not a scoped one, none. That is enforced
twice:

- **At runtime, by grant.** `pv_agent_reader` holds `SELECT` on five
  `agent_*_v1` views and, explicitly, `REVOKE ALL ON TABLE` for all 26 canonical
  tables.
- **At review time, by lint.** `python -m tools.write_path_lint` walks the tree
  and reports where canonical write statements live. Measured on this tree
  2026-08-27: *5 rules, 0 violations; 27 canonical write statements, 19 of them
  in the Kernel; `agents/` 0, `workers/` 0, `apps/web/` 0, `packages/` 0.* The
  Kernel's nineteen are **named** in
  `memory_kernel/transaction.CANONICAL_WRITE_STATEMENTS`, so the number stays a
  claim about specific statements rather than a constant nobody re-measures.
  The other eight are all app-role writes that rule `W4` permits by name — the
  outbox dispatcher's `UPDATE outbox_events SET status = ...` (which
  `10_DATABASE_DDL.md` §12 assigns to the dispatcher rather than to
  `pv_kernel_writer`, rule `W5-outbox-UPDATE-is-dispatcher-permitted`), and the
  evidence and proposal `INSERT`s in `app/ingestion` and `app/proposals` that
  `W4-evidence-and-proposal-INSERT-is-app-permitted` allows.

  **Re-run it rather than quoting this.** The count moves as the ingestion path
  grows, and a number in prose that nobody re-measures is exactly the failure
  this lint exists to prevent.

Six levels of representation are kept apart, each a distinct table with a
distinct lifecycle and a distinct authority to change things:

```
Artifact → Evidence → Claim → Belief → Commitment → State
```

Ordinary RAG has one level — the chunk — and resolves a contradiction by cosine
similarity. Level 3 is the one it cannot represent at all: an invoice arriving
does not make $186 *owed*, it makes $186 **claimed**, by a party with a financial
interest, about a period beginning after a termination that same party confirmed
in writing. Every one of those qualifiers is a column.

Full diagrams, with per-component build status:
**[`docs/diagrams/architecture.md`](docs/diagrams/architecture.md)**

---

## Current state — read this before you run anything

This section exists because a spin-up guide that implies working capability is
worse than none. Several things do not work yet, and they are named here rather
than discovered at a terminal.

**Built and demonstrable**

- The schema: migrations `0001`–`0008`, 26 tables, 5 agent-safe views, 5 SQL
  roles with the grants above. Migrated and seeded on a live cluster.
  Migration `0009` — the Gemini embedding plane, `VECTOR(1536)` — is authored and
  in the chain; see the warning in step 4 before running it against a seeded
  database.
- The **Memory Kernel** — the decision core, the disposition ladder, the
  serializable transaction with bounded `40001` retry.
- **Retrieval — the eight stages, not the pipeline.** `A_SCOPE`, `B_IDENTITY`,
  `C_TEMPORAL`, `D_VECTOR`, `E_RELATIONAL`, `F_GROUNDING`, `G_RERANK`,
  `H_CONTEXT` all exist and the ANN statement is proved against the corpus —
  `ann.py` parses the canonical predicate out of the spec markdown so the SQL
  cannot drift from the document. Vector ANN is stage **D**, deliberately after
  the two stages that can produce certainty. **No module composes them end to
  end**, which is why `internal.retrieve` is unbound; `STATUS.md` §4 has the
  detail. Listing this under "built" without that sentence would be the
  overstatement this section exists to prevent.
- **API surface and auth** — capability-typed principals that cannot be
  constructed from request data, plus an adversarial cross-tenant test lane.
- **Frontend** — 14 screens with a mechanical render-honesty checker.
- The gate tooling: `write_path_lint`, `txn_purity_lint`, `invariant_map_check`,
  `manifest_check`, `sabotage_guard`, `defect_lint`, `scrub`, `gate.sh`.

- The **agent layer**, built natively against `google-genai` — 23 modules, 310
  tests. The ingestion graph's eleven nodes, the advocate graph's six, the
  conditional resolver, the counterfactual graph, and a twelve-artifact
  prompt-injection suite. Phase 7 was never started under the pre-pivot plan,
  which is why there was no LangGraph code to port and the layer got written once.
  **Both tiers have been invoked against live Gemini endpoints** — three
  artifacts, 31 `agent_runs` rows where there were zero, each carrying
  `model_calls[]` attribution ([`ops/agent-graph-live-run.txt`](ops/agent-graph-live-run.txt)).

- **Deployed to Cloud Run and serving since 2026-08-28.** Two services reaching
  the seeded CockroachDB cluster: `GET /v1/version` reports `fixture_mode: false`
  and `db_ok: true`, an unauthenticated read is refused with `401`, and the
  dashboard renders `USD 2,020.00` summed by the API from Kernel-written rows.
  The platform claims above are backed by a running deployment rather than by
  a Dockerfile. The URLs are at the top of this file.
- **Every Gemini model id is settled by invocation, not by listing.**
  `python ops/probes/gemini_probe.py` exits 0 at
  `PASS 11 | FAIL 0 | CANNOT RUN 0`, settled 2026-08-24; the transcript is
  [`ops/gemini-probe.txt`](ops/gemini-probe.txt). `gemini-embedding-2` returns
  1536 dimensions at L2 norm **1.0000003** while `gemini-embedding-001` returns
  **0.6935943** in the same run, which turns the argument for choosing the
  former into a measurement. Native multimodal works, which is why there is no
  OCR service in this pipeline.

**Landed last, and measured**

These were the final subsystems to arrive. Each is green, measured with a real
exit code rather than a piped one:

- **Actions, approval and the executor — the approve, reject and execute half.**
  `ActionIntentService.create` is implemented and tested, and `approve`,
  `reject`, `execute_action` and both action reads are bound to the API.
  `basis_case_revision`, `draft_sha256`, revalidation and idempotency are all
  implemented. 76 tests. **Intent creation is not bound**: the agent-facing
  adapter `internal.create_action_intent` is the one unbound method on this
  plane, so nothing can write an `action_intents` row and there is consequently
  nothing for the approve half to approve. That is why `STATUS.md` marks demo
  step E blocked, and it is not a wiring job — binding it means making the live
  read path produce a typed `StateProof` where today it assembles a dict
  payload, which is a refactor of a tested path. `STATUS.md` §4 has the detail.
- **Events, outbox dispatch and the trigger evaluator** — the prospective-memory
  reveal, including the trigger predicate DSL. 299 tests.
- The **MCP server** over the five agent views. 135 tests.
- **Binding the API's ports to the database.** `build_dependencies()` no longer
  raises; `make run-api` starts a real server. 456 tests.

- The **eval harness**. `make evals` exits 0 at `PASS 9 | FAIL 0 | CANNOT RUN 6`,
  read-only against the live corpus, every metric printing the command that
  reproduces it. Read it past the mean: retrieval recall@20 is 0.7715, and the
  report **names the two hero documents the decoy field buries** rather than
  letting an average hide them.
- **Container images for Cloud Run**, both built and run locally —
  `make deploy-images`. The control plane answers `200` on `/v1/healthz`, `401`
  on an unauthenticated read, and reports its `git_sha` and `db_ok` on
  `/v1/version`. [`deploy/README.md`](deploy/README.md) is the runbook.

**Not built**

- The trace assembler (`app/observability`), which is why `read.get_trace` and
  `read.memory_trace` are unbound — two of **six unbound methods across the
  API's 47**. All six are named in one place, together with the subsystem each
  waits on, by
  [`services/control_plane/app/api/adapters/unbound.py`](services/control_plane/app/api/adapters/unbound.py):
  `read.get_trace` and `read.memory_trace` (the trace assembler);
  `internal.retrieve` (all eight retrieval stages exist, no module composes them
  end to end); `internal.create_action_intent` (a typed State Proof the live
  read path does not build, which is what blocks demo step E);
  `write.create_correction` (`app/ingestion`, plus a `RETRACT_EVIDENCE` writer
  that exists nowhere at all); and `write.rotate_ingest_alias` (the ingest-alias
  minting path). Each answers `501 NOT_IMPLEMENTED` naming that subsystem —
  never `500`, and never an empty list, because a read method that returned `[]`
  would render as "no conflicts on this case" and be believed. Wiring one means
  deleting a line from that register, which is a visible act in a diff.
- The 51-scenario labelled eval corpus that `CANONICAL_DECISIONS.md` freezes.
  `evals/datasets/` holds two empty directories; the harness records this as
  `MEM-03 CANNOT RUN` rather than scoring around it.
- The demo video.

**Unverified, and it matters**

- **Model throughput is unmeasured.** Which ids answer is settled, by the probe
  and by the agent graphs above. How fast they answer under load is not: the AI
  Studio rate limits are per-tier and visible only in the dashboard, and
  re-embedding 18,035 texts is the longest unattended job left in this build.

  Worth keeping in view, because it is why the probe exists at all: the
  *previous* model canon was frozen from documentation rather than from
  invocation, and **all four of its ids turned out to be un-invocable**. Two of
  this probe's own first-run verdicts were also wrong, and both were defects in
  the probe rather than in the model — `D-00-046` reported PASS for three ids
  that answered nothing, and `D-00-047` reported a capability FAIL that was a
  1×1 transparent pixel the API rejects. A probe measures the probe until proven
  otherwise.

- **The corpus is still Titan-embedded.** The 18,035 vectors in `evidence_items`
  are 1024-dimensional AWS Titan output. `PV_EMBEDDING_PROFILE` defaults to
  `titan-v1` so retrieval queries the space the corpus was actually written in.
  The re-embed to `gemini-embedding-2` at 1536 has not run.

**Things that will look broken and are not**

- **A `200` from the control plane does not mean the database is reachable.**
  `make run-api` now starts a real server, and startup deliberately survives a
  refused pool: it answers `GET /v1/healthz` and reports `db_ok: false` on
  `GET /v1/version` rather than crash-looping before it can say anything. Read
  `db_ok`, not the status code. On Windows, `--loop asyncio` is not optional —
  psycopg async refuses uvicorn's default proactor loop, and the `make` target
  already passes it.
- **`make db-migrate`, `make db-reset`, `make embeddings-warm`, `make sabotage`,
  `make demo-rehearse` and `make test-release` exit non-zero by design.**
  Each prints the phase and task that owns it. A target that succeeds without
  doing its work produces a green log for work that never happened. A real
  substitute for `db-migrate` is given in step 4.
- **The web app runs in FIXTURE mode with a permanent, non-dismissible banner**
  unless `PV_API_BASE_URL` is set. There is no third mode and no mode in which a
  fixture is served without saying so.
- **`CANNOT RUN` is recorded distinctly from `FAIL` throughout**, because the
  two lead to opposite decisions. A probe that could not connect once reported
  that a *capability* had failed, which would have forced a working capability
  into a fallback. Expect to see the third verdict in transcripts, in
  `make evals`, in `make sabotage` and over HTTP, where an unbuilt capability
  answers `501 NOT_IMPLEMENTED` naming the subsystem it waits on rather than
  `500 INTERNAL_ERROR`.
- **The 18,035 vectors currently in the database are 1024-dimension Titan
  vectors**, not 1536-dimension Gemini vectors. Migration `0009` widens the
  column and nulls them; the Gemini re-embed that refills them is in flight. In
  between, retrieval returns nothing — by construction, not by accident.
- **Deployed to Cloud Run and serving**, at the two URLs above.
  [`deploy/README.md`](deploy/README.md) is the runbook; `deploy/cloudrun.sh up`
  reproduces it from a clean machine once `deploy/.env.deploy` holds the project
  id. Read `db_ok` on `GET /v1/version` rather than the status code — startup
  deliberately survives a refused pool and says so rather than crash-looping.

### Two operational facts that will cost you an afternoon if you miss them

> **The ANN index build takes about 55 minutes, not one to two.**
> Measured three times on this cluster: **52:56, 55:12, 54:32**. Older runbook
> text predicting "one to two minutes" was wrong by roughly 30×. Budget an hour
> for any `make demo-reset` + `make seed` sequence, and do not start one near a
> deadline.

> **Never run two seeds concurrently. The failure is silent and destructive.**
> CockroachDB serialises schema changes, so a second seed's `DROP INDEX` does not
> fail — it **queues** behind the first seed's 55-minute `CREATE` and fires the
> instant that succeeds. This destroyed one complete build. If you are unsure
> whether a seed is running, check before starting another:
> `SELECT count(*) FROM [SHOW SESSIONS] WHERE application_name NOT LIKE '%psql%';`

---

## Spin-up

### 0. Prerequisites

| | Why |
|---|---|
| **Python 3.12.x** — not 3.13 | `pyproject.toml` pins `>=3.12,<3.13`; `make bootstrap` refuses anything else. |
| **Node.js 20+** and npm | Next.js 15 / React 19. |
| **GNU Make ≥ 3.82** | The `Makefile` relies on `.SHELLFLAGS`, which 3.81 *silently ignores* — every recipe would then lose `-e`, `-u` and `pipefail` and a failing step would scroll past with exit 0. `bootstrap` and every gate battery refuse to run on 3.81. |
| **`bash`** | The `Makefile` sets `SHELL := /bin/bash`. On Windows, use Git Bash; put Git for Windows' (or ezwinports') `make` ahead of any GnuWin32 `make` on `PATH`. |
| **`gitleaks` ≥ 8.30.0** on `PATH` | `make bootstrap` refuses without it. Below 8.30.0 the custom allowlists are silently inert. |
| **`psql`** | `make db-verify` runs `db/verify.sql` through it. CockroachDB is Postgres wire-compatible; the `cockroach` CLI is not required. |
| **Docker** (optional) | `make run-crdb` starts a local single-node CockroachDB; `make run-sink` starts a local mail sink. |
| **A CockroachDB cluster** | CockroachDB Cloud, or the local single-node container above. |
| **A Gemini API key** | From Google AI Studio. Needed only for the model probe and for re-embedding — not for the migrate/seed/test path. |

### 1. Clone and install

```bash
git clone https://github.com/Rayyan9477/Provenance.git provenance
cd provenance
make bootstrap
```

`make bootstrap` checks the Make and Python versions, installs the four
`provenance_*` packages editable plus `requirements-dev.txt`, runs
`npm --prefix apps/web ci`, and verifies `gitleaks` is present. It skips the
pre-commit hook install with a printed message, because `.pre-commit-config.yaml`
does not exist in this tree yet.

### 2. Configure the environment

**Environment variable *names* only appear below. Never commit a value.** This
repository is public; `.env` and `.env.*` are gitignored.

Copy [`.env.example`](.env.example) to `.env` at the repository root and fill in
the values you need. It is annotated by section and says which variables each
platform actually requires. `make db-verify` and `make demo-reset` read `.env`
directly.

For a Cloud Run deployment the configuration is separate and smaller —
[`deploy/.env.deploy.example`](deploy/.env.deploy.example), four values. See
[`deploy/README.md`](deploy/README.md).

**Database — the migrate / seed / test path needs only these:**

| Name | Used by |
|---|---|
| `COCKROACH_DATABASE_URL` | Alembic reads this and only this. Set it to the **migrator** DSN when running migrations. Also the app-role DSN elsewhere. |
| `COCKROACH_MIGRATOR_URL` *or* `PV_DB_MIGRATOR` | Migrator role. `make demo-reset` reads `PV_DB_MIGRATOR`. |
| `PV_DB_MIGRATOR_CI` | Migrator role against the CI database; the seed prefers it. |
| `PV_DB_APP` | App role, preferred by the seed. |
| `COCKROACH_KERNEL_URL` *or* `PV_DB_KERNEL` | `pv_kernel_writer`. |
| `PV_DB_AGENT_READER` | `pv_agent_reader` — for the grant-boundary tests. |
| `PV_DB_OPS_READER` | `pv_ops_reader`. |
| `PROVENANCE_TEST_DB_URL` | The `db` test lane. |
| `PV_VERIFY_URL` | Optional override for `make db-verify`. |
| `PV_APP_DATABASE` | Database name for `make demo-reset`. |

> Every role must resolve to the **same database**. A seed that split itself
> across two databases while a third was being validated once reported
> "26 tables checked, 26 match" over a database holding zero evidence rows. The
> seed now refuses unless every role agrees.

**Models — post-pivot names, from `docs/CANONICAL_DECISIONS.md`:**

| Name | Notes |
|---|---|
| `GOOGLE_API_KEY` *(or `GEMINI_API_KEY`)* | AI Studio key. Read by `ops/probes/gemini_probe.py`; never printed or echoed. |
| `GEMINI_REASONING_MODEL_ID` | Tier R. |
| `GEMINI_EXTRACTION_MODEL_ID` | Tier E. |
| `GEMINI_REASONING_FALLBACK_MODEL_ID` | Capacity fallback only. |
| `GEMINI_EMBEDDING_MODEL_ID` | |
| `EMBEDDING_DIMENSIONS`, `EMBEDDING_VERSION`, `EMBEDDING_NORMALIZATION` | |

Every model id is read from configuration. Swapping one is an environment
change, never a code change, and `agent_runs.model_route` records the id that
actually served each run.

**Still present, pre-pivot, being replaced:**
`provenance_contracts.settings.Settings` presently also requires the AWS-era
names (`BEDROCK_*`, `COGNITO_*`, `S3_*`, `SES_*`, `EVENTBRIDGE_*`, `SQS_DLQ_URL`)
and raises on any missing required value — it defaults no credential, ever. You
do **not** need them for the migrate, seed or test path below, because nothing on
that path constructs `Settings`.

**Application and gate switches:** `APP_ENV`, `APP_BASE_URL`, `WEB_BASE_URL`,
`LOG_LEVEL`, `OTEL_SERVICE_NAME`, `PV_AGENT_MODE`, `PV_MCP_ENABLED`,
`PV_ACTION_EXECUTION_MODE`, `PV_ACTION_ALLOWLIST`, `PV_SABOTAGE`,
`PV_FORBID_MOCKS`, `PROVENANCE_SEED_EMBED_WORKERS`,
`PROVENANCE_CONFIRM_DESTRUCTIVE`.

**Secrets, by name only:** `PROVENANCE_CAPABILITY_HMAC_KEY`,
`PROVENANCE_CAPABILITY_HMAC_KID`, `CURSOR_HMAC_KEY`, `INGEST_ALIAS_HMAC_KEY`.

**Web:** `PV_API_BASE_URL` (or `NEXT_PUBLIC_PV_API_BASE_URL`). Unset ⇒ FIXTURE
mode with a permanent banner.

### 3. Optional — a local cluster instead of CockroachDB Cloud

```bash
make run-crdb      # single-node CockroachDB; SQL on :26257, console on :8081
make stop-local    # tears down run-crdb and run-sink
```

The console is on 8081 because the control plane owns 8080.

### 4. Migrate

`make db-migrate` is one of the deliberate non-zero stubs. Run Alembic directly,
and migrate to a **named revision, not `head`**:

```bash
COCKROACH_DATABASE_URL="<your pv_migrator DSN>" python -m alembic -c alembic.ini upgrade 0009b_kernel_idempotency_grant
```

**Do not run `upgrade head` here.** The chain head is
`0009_gemini_embedding_plane`, and its `upgrade()` refuses to run unless
`PV_EMBEDDING_REWRITE_ACK` is set to the exact number of embeddings the run
would destroy — *including when that number is zero*, so it aborts on a fresh,
empty database too. That refusal is the guard working as designed, but it means
`upgrade head` is not a command that succeeds on any database this code should
run against. This README told you to run it until 2026-08-31, and `make
demo-reset` did the same after dropping the database, which left it recreated
and unmigrated. Both now name the revision.

`0009b_kernel_idempotency_grant` is what the live cluster is at, which you can
check yourself against `schema_revision` on `GET /v1/version`, and what
`TARGET_REVISION` in the `Makefile` pins.

`alembic.ini` declares `sqlalchemy.url` empty on purpose so a DSN cannot be
committed into it; the URL comes from the environment at run time and from
nowhere else.

Migrations `0001`–`0008` create 26 tables, 5 `agent_*_v1` views, the SQL grants
and the ANN vector index; `0009a` widens the proposal model `CHECK` to admit the
Gemini ids and `0009b` adds the Kernel's idempotency grant. On a fresh database
that is all you need, and that whole chain is safe.

> **`0009` is destructive to an already-seeded database, which is why the
> command above stops short of it.** `0009_gemini_embedding_plane` is the schema half of the pivot:
> it widens `evidence_items.embedding` from `VECTOR(1024)` to `VECTOR(1536)` and
> repoints the two model `CHECK` constraints from the Bedrock ids to the Gemini
> ids. A 1024-dimension vector is not a truncation of a 1536-dimension one — it
> is a different model's output in a different space — so the revision **leaves
> every `embedding` NULL** and drops `embedding_model`, `embedding_version` and
> `embedding_generated_at` with it, rather than leaving a vector's provenance
> attached to a vector that no longer exists. The `evidence_items` rows, their
> text, their hashes and their grounding edges are untouched; evidence is
> append-only. But **retrieval returns nothing until the corpus is re-embedded**,
> because the canonical query filters `embedding_version = 'v2'` and no row
> carries it yet. If you are migrating a database that already holds the Titan
> corpus, read the revision's docstring first —
> `db/migrations/versions/0009_gemini_embedding_plane.py` — it is written for
> exactly this decision.
>
> `0009` rebuilds the ANN index while the column is empty, so *that* build is
> free. The expensive one is the seed's, after re-embedded rows land.
>
> `ck_evidence_embedding_model` deliberately admits **both**
> `gemini-embedding-2` and `gemini-embedding-2-preview`: Google's models page
> spells it one way and its embeddings page the other, and no live invocation has
> settled it. A `CHECK` that admits two candidates is honest about what is known;
> one that picks a spelling is a guess wearing a constraint's clothes.

### 5. Verify the schema

```bash
PV_VERIFY_URL="<dsn>" PV_VERIFY_ALLOW_EMPTY=1 make db-verify
```

`db/verify.sql` is one statement that computes its own verdict over invariants
`V1`–`V11`. `make db-verify` parses the verdict and maps it to an exit status:

| Verdict | Exit | Meaning |
|---|---|---|
| `PASS` / `PASS_PARTIAL` | 0 | `PASS_PARTIAL` names the checks that examined no rows. |
| `FAIL_*` | 1 | An invariant returned rows. |
| `VACUOUS_EMPTY_CORPUS` | 2 | Nothing was examined. `V1`–`V10` returning zero over an empty database proves nothing. `PV_VERIFY_ALLOW_EMPTY=1` turns this into exit 0 for a deliberately pre-seed database — it changes the exit status only, never the verdict text. |
| no verdict line | 3 | The file or the connection is broken. |

Drop `PV_VERIFY_ALLOW_EMPTY` after seeding. A healthy seeded database reports
`V1 0 … V10 0  V11 3` — `V11` is a positive control, not a failure count.

**Quiesce the database first.** `V1`–`V11` are whole-corpus invariants, so a
concurrent writer makes the result meaningless in *both* directions: a failure
that is really someone else's half-built fixture, or a pass that read the moment
before the breaking row landed.

### 6. Seed

```bash
make seed
```

That runs `python -m scripts.seed --profile schema-only` followed by
`python -m tools.manifest_check db/seeds/MANIFEST.json`.

It loads 18,035 evidence rows — 32 curated hero rows, 3 retraction fixtures, and
18,000 synthetic decoys — plus the identity and artifact tables, and builds the
`evidence_embedding_ann_idx` vector index.

**Re-read the two boxed warnings above before you start.** The index build is
~55 minutes and two concurrent seeds destroy the database.

**The Kernel replay now runs, so those tables are populated.** Eleven curated
`MemoryProposal` fixtures go through `MemoryKernel.commit()` — never a raw
`INSERT`, because a second canonical writer is the one thing this architecture
forbids. Measured against the live cluster after `make seed`:

```
[9] replayed 11 proposals through MemoryKernel.commit():
    committed=11 already-decided=0 rejected=0 cases_positioned=9;
    obligations carried: commitments=4, fulfillments=2, trigger_mutations=2
```

and the row counts that leaves: `claims` 36, `memory_proposals` 11,
`kernel_decisions` 11, `beliefs` / `belief_versions` / `belief_support` 9,
`commitments` 4, `fulfillments` 2, `prospective_triggers` 2,
`state_transitions` 8, `outbox_events` 8.

`conflicts` is **0 on purpose** and is the only table `MANIFEST.json` still
expects empty: the June invoice, the reopen, and the `12 → 13` revision
increment are the *demo's* to perform. A seeded conflict would mean the reveal
had already happened before anyone pressed anything.

> **The roles disagree about which database they point at.** `pv_migrator`
> resolves to `provenance_ci` while every other role resolves to `provenance`.
> The seed refuses rather than writing half the corpus to each — pass
> `--database provenance` explicitly:
>
> ```bash
> python -m scripts.seed --profile all --embeddings cache-only --database provenance
> ```

`db/seeds/vectors.parquet` (67 MB) caches the corpus vectors, so reseeding does
not re-invoke an embedding API for 18,000 texts. `MANIFEST.json` pins it by
content hash.

### 7. Run

**Control plane** on `:8080`:

```bash
make run-api
```

Served as a **factory**, not a module-level `app`: a module-level app resolves
`Settings` at *import*, so any tool that merely imports `main.py` — a linter
walking the tree, a stray test collection — fails on an unset environment
variable instead of doing its job.

Startup survives a refused database pool on purpose. Check
`GET /v1/version` and read `db_ok`; a `200` alone proves only that the process
is up. `/v1/healthz` is a bare liveness probe and never carries `fixture_mode`.

`make run-api` exports `.env` into the environment before starting, because
`Settings` deliberately does **not** parse a dotenv (`settings.py:331`): a
repository-root `.env` holding a live credential must not be read by every test
that happens to run from the repo root. The shell exports; the object only
reads. It also derives `GIT_SHA` from `git rev-parse HEAD` rather than from a
written-down value, so the stamp on every screen cannot drift out of step.

On Windows the recipe runs `scripts/run_api.py` rather than `uvicorn` directly.
`--loop asyncio` *selects the proactor loop* there — uvicorn 0.40 resolves that
flag to `ProactorEventLoop` on win32 — and psycopg refuses it, so the server
started, answered `200`, and reported `db_ok=false` against a perfectly healthy
cluster. The runner supplies a `SelectorEventLoop` factory instead; a policy
cannot, because uvicorn hands a factory to `asyncio.run` and never consults the
policy.

**Web** on `:3000`:

```bash
make run-web                      # or: npm --prefix apps/web run dev
```

LIVE mode needs **two** variables, not one. Put them in `apps/web/.env.local`
(gitignored):

```bash
PV_API_BASE_URL=http://127.0.0.1:8080
PV_API_TOKEN=$(python scripts/mint_local_token.py --quiet)
```

`PV_API_BASE_URL` alone is not enough and the failure is quiet: every read runs
in a server component, the control plane answers `401 UNAUTHENTICATED` to an
anonymous read — correctly — so setting only the base URL replaces the fixture
banner with a wall of error states. `PV_API_TOKEN` carries **no**
`NEXT_PUBLIC_` prefix on purpose: Next.js inlines prefixed variables into the
browser bundle, and this one must stay on the server.

`PV_PLATFORM=local` is the reviewer's mode — no cloud account — and it needs
`PV_LOCAL_AUTH_SECRET` in `.env`. There is deliberately no default: a default
signing key verifies forged tokens, so the API refuses to start and names the
variable rather than booting with a key an attacker could guess.

```bash
python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

With both set, the fixture banner disappears because the data is real, and the
dashboard's `USD 2,020.00` is summed by the API from Kernel-written rows rather
than read from a file.

Other real targets:

```bash
make run-sink       # local mail sink: SMTP :1025, UI http://localhost:8025
make stop-local     # remove the local CockroachDB and mail-sink containers
```

### 8. Test

```bash
make test-fast                    # hermetic: no database, no network, no credentials
make test                         # the commit lane
make test-db                      # needs PROVENANCE_TEST_DB_URL
npm --prefix apps/web test        # frontend component tests
npm --prefix apps/web run verify  # honesty + counterfactual + typecheck + lint + format + test
make route-sweep                  # load EVERY live route; needs run-api and run-web up
```

`make route-sweep` exists because the frontend suite cannot see the failures
that matter most. It runs against fixtures, and the fixtures were written from
the same reading of the spec as the TypeScript types — so they agreed with each
other, and **nine live routes still returned `500`** the first time anything
loaded them against the real API, including every case docket. Each failure was
a shape the contract declared and the server does not send: a decimal string
where a `Money` object arrives, a `Record` where `null` arrives, a `context`
every case was assumed to have. TypeScript was satisfied precisely because the
claims were false.

The sweep reads its ids from the API, so it visits every case, relationship and
artifact the corpus actually contains rather than the two somebody wrote down —
which is what caught a route that rendered for one id and died on another. Exit
`2` means the sweep could not run, which is not the same claim as a broken route
and is never recorded as one.

Measured on this tree on 2026-08-30: the unit lane runs **2,912 passed, 4
skipped, 0 failed**; the frontend suite is **74 passing**; `make route-sweep`
reports **50 routes discovered, 0 broken** — run against the deployed Cloud Run
revision rather than localhost, which is the stronger claim
([`ops/route-sweep-live-cloudrun.txt`](ops/route-sweep-live-cloudrun.txt)).
The single failure this paragraph used to report
(`test_g0_3b_is_a_working_tree_scan_of_ops`, `D-00-043`) cleared when `ops/` was
committed, exactly as it predicted it would.

**Re-measure rather than quoting these.** This tree moves by the hour, and the
numbers above have already gone stale twice and been corrected.
`pytest --collect-only -q` prints the total in about five seconds, and
`pytest -m unit -q` runs the hermetic lane in about a minute. Markers: `unit`,
`db`, `contract`, `live_model`,
`retrieval`, `e2e`, `adversarial`, `concurrency`, `isolation`, `slow`, `golden`.
`--strict-markers` is on, so an unregistered marker fails the build rather than
scrolling past.

### 9. Deploy to Google Cloud Run

Two services: `control-plane` and `web`. The runbook, including why this
deployment runs `PV_PLATFORM=local` and what that costs, is
[`deploy/README.md`](deploy/README.md).

**Docker is not required.** `deploy/cloudrun.sh up` builds on **Cloud Build**
from [`deploy/cloudbuild.yaml`](deploy/cloudbuild.yaml), so `gcloud` alone is
enough to reproduce both images. A local `docker build` is kept only as a
fallback for when Cloud Build refuses.

That is not a preference. The deploy used to build locally, and the local
daemon died twice mid-deploy — `Docker Desktop is unable to start`, then a 500
from the engine pipe — taking the only route to production with it. A deploy
that needs a working Docker Desktop on one particular machine is that machine's
deploy, not the project's.

**Optional, if you do have Docker:** building and running the image locally is
still the fastest way to find a configuration error, and needs no cloud account:

```bash
make deploy-images

docker run --rm -p 8080:8080 --env-file .env provenance-control-plane:local
curl -s localhost:8080/v1/version | python -m json.tool
```

`Settings` refuses to start on a missing required variable and **names every
one it is missing** rather than failing on the first — so one run tells you the
whole list. It defaults no credential, ever.

**Then deploy:**

```bash
cp deploy/.env.deploy.example deploy/.env.deploy   # four values
deploy/cloudrun.sh up                              # build, push, deploy, print proof
```

`up` is idempotent: re-running rolls the revision forward, and it does **not**
rotate the four signing keys. Rotating them would invalidate every capability
proof and pagination cursor the previous revision issued, which presents as
intermittent `403`s and reads as a flaky network (`D-08-003`).

Credential-shaped values go to Secret Manager and are mounted by reference.
`--set-env-vars` would put them in the revision spec, which `gcloud run services
describe` prints in full to anyone holding `viewer`.

**When the demo is recorded, switch it off:**

```bash
deploy/cloudrun.sh down
```

Both services deploy with `--min-instances 0`, so an idle deployment already
bills essentially nothing; `down` pins `--max-instances 0` so nothing can start
an instance at all. The images stay in Artifact Registry, so coming back up
needs no rebuild.

> **A `200` from a deployed control plane does not mean the database is
> reachable.** Startup deliberately survives a refused pool and reports
> `db_ok: false` on `GET /v1/version` rather than crash-looping before it can
> say anything — which is the right behaviour on a platform that would
> otherwise show you a retry count and no reason. Read `db_ok`.

---

## How to verify the claims

Nothing in this README asks to be believed. Each claim has a command.

**The Memory Kernel is the only canonical writer**

```bash
python -m tools.write_path_lint
# 5 rules, 0 violations
# 27 canonical write statements, 19 of them in the Kernel
# canonical write statements found in 4 modules:
#     services/control_plane/app/events, services/control_plane/app/ingestion,
#     services/control_plane/app/memory_kernel, services/control_plane/app/proposals
#     agents/: 0    workers/: 0    apps/web/: 0    packages/: 0
```

It prints four numbers that would move if it stopped seeing anything, because
`0 violations` over `0` scanned statements is a vacuous pass.

**Every invariant names a test, and that test actually runs**

```bash
python -m tools.invariant_map_check packages/python/provenance_domain/INVARIANTS.md
# 5 invariants, 5 mapped, 0 UNPROVEN
```

A mapped test that is skipped, `skipif`-ed or `xfail`-ed reports `UNPROVEN` as
loudly as a missing one. The count of tests is not reportable evidence; the map
is.

**No model or network call inside a serializable transaction**

```bash
python -m tools.txn_purity_lint services packages workers
```

A transaction callback runs once per retry, so a model call inside it is charged
again on every attempt while the transaction holds its locks — and an external
effect inside it cannot be rolled back when the transaction is.

**Lint, types and module boundaries**

```bash
make lint
```

Runs `ruff check`, `ruff format --check`, `mypy --strict` on the two typed
packages, the import-linter contracts, and `txn_purity_lint`. The import-linter
step *checks its own output for a contract summary line*, because the obvious
module-form invocation evaluates zero contracts and exits 0 — a green log for a
check that never ran.

**Seed integrity**

```bash
python -m tools.manifest_check db/seeds/MANIFEST.json
```

**The defect ledger**

```bash
make defects            # grouped by status; PHASE=<N> filters to one phase
make debt               # open carried debt, with escalation checks
make close-proof ID=D-04-002
```

`ops/defects/DEFECTS.md` is the ledger. Defects are closed with counterfactual
proofs, not assertions.

**The gate batteries and their committed evidence**

```bash
make gate-0 ... make gate-15
```

Each assertion runs through `tools/gate.sh <ID> -- <command>`, which tees output
to `ops/gates/logs/<ID>.<sha8>.log`, scrubs it through `tools/scrub.py`, and
records the **child's** exit code — not the scrubber's, and not the pipeline's.
Recording the wrong one would make every gate log read `exit=0`. Per-phase
verdicts are in `ops/gates/PHASE_00.md` … `PHASE_15.md`, and `PHASE_15.md` holds
the release-readiness checks.

**Gate G-0 is currently REJECTED**, and the reasons are written down rather than
smoothed over: four of seven assertions pass, two cannot run, and there are open
MAJOR defects against phase 0. `STATUS.md` §4 has the detail. `CANNOT RUN` is
recorded distinctly from `FAIL` throughout, because a probe that could not
connect once reported that a *capability* had failed — which would have forced a
working capability into a fallback.

---

## Repository layout

```
apps/web/                 Next.js 15 UI — deployment unit 1
services/control_plane/   FastAPI: API, auth, retrieval, Memory Kernel,
                          State Proof, actions — deployment unit 2
agents/runtime/           Gemini agent layer — deployment unit 3
                          ingestion, advocate and counterfactual graphs, the
                          resolver, the model router. ~11,600 lines, 310 tests
workers/                  EMPTY — four `__init__.py` files, 14 lines total.
                          The outbox dispatcher it was meant to hold actually
                          lives in services/control_plane/app/events/, and is
                          not deployed separately
deploy/                   Cloud Run: two Dockerfiles, cloudrun.sh, the runbook
packages/python/          provenance_contracts, provenance_domain,
                          provenance_db, provenance_telemetry
db/migrations/            Alembic 0001..0008, then 0009a, 0009b, 0009
db/seeds/                 MANIFEST.json, vectors.parquet
db/verify.sql             the V1..V11 verification statement
scripts/seed/             seed corpus generation
tools/                    gate.sh, scrub, write_path_lint, txn_purity_lint,
                          invariant_map_check, manifest_check, defect_lint
ops/                      execution evidence: probes, gate ledgers, defect ledger
demo/artifacts/           the hero .eml and PDF bytes, real hashes
docs/                     product, architecture, specs, diagrams
docs/diagrams/            the architecture diagrams
```

The design names four deployment units — `web`, `control-plane`,
`agent-runtime`, `workers` — a modular monolith plus managed async workers,
not a microservice zoo.

**Three of the four exist. `workers/` does not**, and saying "four" without that
qualifier would be exactly the kind of small, checkable overstatement this
repository's gates exist to catch. The async work it was to hold — outbox
dispatch, trigger wake — is implemented in
`services/control_plane/app/events/` and `app/triggers/` and runs in the control
plane. Two units are containerised and deployable today
([`deploy/`](deploy/README.md)); the agent runtime runs in-process inside the
control plane rather than as its own service.

`docs/ARCHITECTURE.md` §25 specifies a five-service decomposition and is
**superseded**; it is retained only as a record of a rejected alternative, and
building from it would put the Memory Kernel in its own service and break the
single-canonical-writer boundary.

---

## Ground rules this build paid to learn

- **`CANNOT RUN` is not `FAIL`.** They lead to opposite decisions.
- **A green run on a sabotage assertion is a gate failure**, not a pass.
- **`make` reports its own exit code, not the recipe's** — a recipe exiting 1
  makes `make` exit 2, so a gate asserting `$? == 2` accepts three different
  outcomes.
- **Piping through `tail` discards the exit code.** This produced a false green
  three separate times, including inside a sabotage transcript.
- **An unobserved mapping is a guess with a comment on it.**
- **Verifying against a database another process is writing is meaningless in
  both directions.**

---

## Documentation map

| Document | What it settles |
|---|---|
| [`docs/CANONICAL_DECISIONS.md`](docs/CANONICAL_DECISIONS.md) | The frozen register. **It outranks every other document**, including this one. |
| [`docs/00_PRODUCT.md`](docs/00_PRODUCT.md) | Thesis, the six-way separation worked end to end, glossary. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Service boundaries, request flows, demo story. Pre-pivot in its AWS specifics. |
| [`docs/implementation/00_IMPLEMENTATION_MAP.md`](docs/implementation/00_IMPLEMENTATION_MAP.md) | Deployment units and the authoritative repository layout. |
| [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) | The architecture diagram, with per-component build status. |
| [`STATUS.md`](STATUS.md) | Measured build state, and what is not built. |
| [`PIVOT.md`](PIVOT.md) | The platform migration off Bedrock and AWS onto Gemini and Cloud Run. Written 2026-08-19; read its status block first, since several recommendations have since been decided or superseded. |
| [`SECURITY.md`](SECURITY.md) | Reporting. |

---

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
