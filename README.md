# Provenance

**A system of record for the institutions that already have one of you.**

Submission for the **All Things Agentic Hackathon** — deadline 2026-08-31,
17:00 PDT.

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

## Mandatory hackathon requirements

| Requirement | This project |
|---|---|
| **Gemini 3.5 or newer** | **`gemini-3.7-flash`** (Tier R — semantic resolution, contradiction characterisation, attention assessment, advocacy drafting) and **`gemini-3.5-flash-lite`** (Tier E — extraction, classification, bulk structured output), via the **Gemini Developer API**. Capacity fallback `gemini-3.6-flash`. Embeddings `gemini-embedding-2` at `output_dimensionality=1536`. |
| **A Google agent framework** | **`google-genai`** — the GenAI SDK, one of the four accepted frameworks. Installed and verified at **1.60.0**. |
| **A Google Cloud infrastructure service** | **Cloud Run** — the deployment target for all three runtime units (`control-plane`, `web`, `agent-runtime`). **Not yet deployed.** See *Current state*, below. |
| Canonical database | **CockroachDB Cloud** — 26 tables, 5 agent-safe views, 5 SQL roles, migrated and seeded with 18,035 evidence rows. |
| Architecture diagram | [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) |
| Spin-up instructions | This file, [below](#spin-up). |

There is **no Gemini Pro option** on the Developer API that satisfies the
version floor: `gemini-3.1-pro-preview` is the only Pro model available and it is
version 3.1, *below* the mandated 3.5. Both tiers are therefore Flash-class. The
frozen decision and its reasoning are in
[`docs/CANONICAL_DECISIONS.md`](docs/CANONICAL_DECISIONS.md) → *Gemini model id
canon*.

**These model ids are not yet probed.** See *Current state*.

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
  and reports where canonical write statements live. On this tree: *5 rules, 0
  violations; 23 canonical write statements, 17 of them in the Kernel;
  `agents/` 0, `workers/` 0, `apps/web/` 0, `packages/` 0.* The Kernel's
  seventeen are **named** in
  `memory_kernel/transaction.CANONICAL_WRITE_STATEMENTS`, so the number stays a
  claim about specific statements; the other six are the outbox dispatcher's
  `UPDATE outbox_events SET status = ...`, which `10_DATABASE_DDL.md` §12
  assigns to the dispatcher rather than to `pv_kernel_writer` (rule
  `W5-outbox-UPDATE-is-dispatcher-permitted`).

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
- **Retrieval** — an eight-stage pipeline (`A_SCOPE`, `B_IDENTITY`, `C_TEMPORAL`,
  `D_VECTOR`, `E_RELATIONAL`, `F_GROUNDING`, `G_RERANK`, `H_CONTEXT`). Vector ANN
  is stage **D**, deliberately after the two stages that can produce certainty.
- **API surface and auth** — capability-typed principals that cannot be
  constructed from request data, plus an adversarial cross-tenant test lane.
- **Frontend** — 14 screens with a mechanical render-honesty checker.
- The gate tooling: `write_path_lint`, `txn_purity_lint`, `invariant_map_check`,
  `manifest_check`, `sabotage_guard`, `defect_lint`, `scrub`, `gate.sh`.

- The **agent layer**, built natively against `google-genai` — 19 modules, 252
  tests. The ingestion graph's eleven nodes, the advocate graph's six, the
  conditional resolver, and a twelve-artifact prompt-injection suite. Phase 7 was
  never started under the pre-pivot plan, which is why there was no LangGraph code
  to port and the layer got written once.
  **No model has been invoked** — see *Unverified* below.

**Landed while this README was being written**

All four were listed here as in-progress hours ago. Each is now green, measured
with a real exit code rather than a piped one:

- **Actions, approval and the executor** — the approve-and-send half of the demo.
  `basis_case_revision`, `draft_sha256`, revalidation and idempotency are all
  implemented. 74 tests.
- **Events, outbox dispatch and the trigger evaluator** — the prospective-memory
  reveal, including the trigger predicate DSL. 262 tests.
- The **MCP server** over the five agent views. 131 tests.
- **Binding the API's ports to the database.** `build_dependencies()` no longer
  raises; `make run-api` starts a real server. 286 tests.

**Not built**

- Evals, and the submission artifacts other than this file and the diagram.
- Any deployment. Nothing has been deployed to Google Cloud.

**Unverified, and it matters**

- **No Gemini model id in this repository has been invoked.** Every one —
  `gemini-3.7-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`,
  `gemini-embedding-2` — is transcribed from documentation. The previous model
  canon was frozen the same way and *all four of its ids turned out to be
  un-invocable*. `python ops/probes/gemini_probe.py` is what settles this; it
  currently reports `CANNOT RUN — GOOGLE_API_KEY is not set`, which is recorded
  as distinct from a failure.
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
  `make demo-rehearse` and `make test-submission` exit non-zero by design.**
  Each prints the phase and task that owns it. A target that succeeds without
  doing its work produces a green log for work that never happened. A real
  substitute for `db-migrate` is given in step 4.
- **The web app runs in FIXTURE mode with a permanent, non-dismissible banner**
  unless `PV_API_BASE_URL` is set. There is no third mode and no mode in which a
  fixture is served without saying so.
- **The Gemini model ids are UNPROBED.** `ops/probes/gemini_probe.py` exists;
  `ops/gemini-probe.txt` currently records `CANNOT RUN — GOOGLE_API_KEY is not
  set`. Every id in this README is transcribed from documentation. The last time
  this project froze model ids from documentation, *all of them were wrong*.
  `CANNOT RUN` is recorded distinctly from `FAIL` throughout, because the two
  lead to opposite decisions.
- **The 18,035 vectors currently in the database are 1024-dimension Titan
  vectors**, not 1536-dimension Gemini vectors. Migration `0009` widens the
  column and nulls them; the Gemini re-embed that refills them is in flight. In
  between, retrieval returns nothing — by construction, not by accident.
- **Nothing is deployed to Cloud Run.** No container has been built or pushed.

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
repository becomes public at submission; `.env` and `.env.*` are gitignored.

There is no `.env.example` in the tree yet — create `.env` at the repository
root yourself. `make db-verify` and `make demo-reset` read it directly.

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

`make db-migrate` is one of the deliberate non-zero stubs. Run Alembic directly:

```bash
COCKROACH_DATABASE_URL="<your pv_migrator DSN>" python -m alembic -c alembic.ini upgrade head
```

`alembic.ini` declares `sqlalchemy.url` empty on purpose so a DSN cannot be
committed into it; the URL comes from the environment at run time and from
nowhere else.

Migrations `0001`–`0008` create 26 tables, 5 `agent_*_v1` views, the SQL grants
and the ANN vector index. On a fresh database that is all you need, and the
whole chain is safe.

> **`0009` is destructive to an already-seeded database, and `upgrade head`
> includes it.** `0009_gemini_embedding_plane` is the schema half of the pivot:
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

Measured on this tree on 2026-08-24: the unit lane runs **2,621 passed, 4
skipped, 1 failed**; the frontend suite is **73 passing**; `make route-sweep`
reports **50 routes discovered, 0 broken**. The one failure is
`tools/tests/test_build_lane_guards.py::test_g0_3b_is_a_working_tree_scan_of_ops`,
a build-lane guard tracked as `D-00-043` — it fails because `ops/` is untracked,
so a git-mode secret scan cannot see it, and it clears when `ops/` is committed.
Not a product defect.

**Re-measure rather than quoting these.** This tree moves by the hour;
the numbers above went stale twice while this README was being written.
`pytest --collect-only -q` prints the total in about five seconds, and
`pytest -m unit -q` runs the hermetic lane in about a minute. Markers: `unit`,
`db`, `contract`, `live_model`,
`retrieval`, `e2e`, `adversarial`, `concurrency`, `isolation`, `slow`, `golden`.
`--strict-markers` is on, so an unregistered marker fails the build rather than
scrolling past.

---

## How to verify the claims

Nothing in this README asks to be believed. Each claim has a command.

**The Memory Kernel is the only canonical writer**

```bash
python -m tools.write_path_lint
# 5 rules, 0 violations
# 25 canonical write statements, 19 of them in the Kernel
# canonical write statements found in 2 modules:
#     services/control_plane/app/events, services/control_plane/app/memory_kernel
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
verdicts are in `ops/gates/PHASE_00.md` … `PHASE_15.md`; `ops/gates/SUBMISSION.md`
holds the submission checks.

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
agents/runtime/           Gemini agent layer — deployment unit 3 (scaffolding only)
workers/                  async workers — deployment unit 4 (scaffolding only)
packages/python/          provenance_contracts, provenance_domain,
                          provenance_db, provenance_telemetry
db/migrations/            Alembic 0001..0008
db/seeds/                 MANIFEST.json, vectors.parquet
db/verify.sql             the V1..V11 verification statement
scripts/seed/             seed corpus generation
tools/                    gate.sh, scrub, write_path_lint, txn_purity_lint,
                          invariant_map_check, manifest_check, defect_lint
ops/                      execution evidence: probes, gate ledgers, defect ledger
demo/artifacts/           the hero .eml and PDF bytes, real hashes
docs/                     product, architecture, specs, diagrams
docs/diagrams/            the architecture diagram (submission artifact)
```

Four deployment units, deliberately: `web`, `control-plane`, `agent-runtime`,
`workers`. A modular monolith plus managed async workers — not a microservice
zoo. `docs/ARCHITECTURE.md` §25 specifies a five-service decomposition and is
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
| [`PIVOT.md`](PIVOT.md) | The pivot assessment. Written 2026-08-19; its "twelve days" figure is stale. |
| [`SECURITY.md`](SECURITY.md) | Reporting. |

---

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
