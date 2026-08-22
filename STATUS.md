# Provenance — build status

Written 2026-08-24. Every number here was measured from the tree or the cluster
on that date, not carried forward from a plan.

**Re-measure rather than quote.** These figures moved several times within a
single session; any number written down is a timestamp. The command is given
beside each claim so a reader can check rather than trust.

---

## 1. Where the build is

| | |
|---|---|
| Python suite collected | **3,434** |
| Unit lane | **2,707 passed, 1 failed, 4 skipped** (`pytest -q -m unit`) |
| Live web app | **50 routes swept, 0 broken** (`make route-sweep`) — LIVE, not fixtures |
| Frontend | **65 passed** (`cd apps/web && npm run verify`) |
| `ruff check` / `ruff format --check` | clean, 388 files |
| `mypy --strict` | clean |
| import-linter | **5 contracts kept, 0 broken** |
| `write_path_lint` | **5 rules, 0 violations** · 23 canonical writes, 17 in the Kernel |
| `txn_purity_lint` | 7 transaction callbacks, 0 network constructs |
| `invariant_map_check` | **5 invariants, 5 mapped, 0 UNPROVEN** |
| Sabotage matrix | **11 entries**, each verified firing |
| Defect ledger | `defect_lint: 0 violations` · **33 blocking, 1 of them BLOCKER** (see §5) |
| Gemini probe | **PASS 11, FAIL 0, CANNOT RUN 0**, exit 0 (`ops/gemini-probe.txt`) |
| Live agent graph | **PASS 34, FAIL 2, CANNOT RUN 8** over 3 artifacts (`ops/agent-graph-live-run.txt`) — both tiers invoked, **21 `agent_runs` rows** where there were 0 |
| Control plane | **starts, `db_ok:true`** against the live cluster; 45 routes; 401 on an unauthenticated read |
| Frontend | **74 tests**; 50 live routes swept clean; zero console messages |

Per-area collection:

| Area | Tests |
|---|---|
| `services/control_plane/tests/kernel` | 597 |
| `packages/` (4 workspace packages) | 503 |
| `services/control_plane/tests/db` | 407 |
| `infra/` (CDK — discarded, retained as a record) | 304 |
| `services/control_plane/tests/api` | 290 |
| `services/control_plane/tests/events` | 282 |
| `agents/runtime/tests` | 252 |
| `tools/` | 189 |
| `tests/retrieval` | 180 |
| `services/control_plane/tests/auth` | 143 |
| `services/control_plane/tests/mcp` | 135 |
| `services/control_plane/tests/actions` | 76 |
| `apps/web` | 65 |

### The one failing test

`tools/tests/test_build_lane_guards.py::test_g0_3b_is_a_working_tree_scan_of_ops`
fails because `git ls-files ops/` is empty: **`ops/` is untracked.** No git-mode
secret scan can see the evidence directory, and a destroyed transcript has no
`git checkout` recovery — which already happened once. It clears when `ops/` is
committed. Read §6.3 before committing it.

---

## 2. Phase status

| Phase | State | Note |
|---|---|---|
| 0 — scaffold, licence, probes | built, gate REJECTED | see §4 |
| 1 — contracts and domain | built | |
| 2 — schema, migrations, seed | built | 26 tables, 5 views, seed idempotent |
| 3 — database runtime and retry | built | `40001` proved by real two-connection interleaving |
| 4 — Memory Kernel | built | writes commitments and triggers since 2026-08-24 |
| 5 — read models | partial | `state_proof` built; `app/observability` empty |
| 6 — embeddings and retrieval | built | `ann_search()` now binds and delegates |
| 7 — agent graphs | **built** | `google-genai`, 252 tests, 12-artifact injection suite |
| 8 — API and auth | **built** | `build_dependencies()` returns; 27 of 47 ports bound |
| 9 — actions, approval, executor | **built** | five refusal gates, each proved separately |
| 10 — events, outbox, triggers | **built and wired** | `commit_trigger_evaluation` now lives in the Kernel; `evaluate_trigger`, `deliver_event`, `sweep_outbox`, `wake_trigger` bound. Two triggers armed in the live cluster |
| 11 — MCP, SQL roles, agent views | **built** | five tools; base-table denial proved to come from the database |
| 12 — frontend | **built and LIVE** | runs against the real API, no fixture banner. 50 routes swept clean; four contract drifts and two layout defects found and fixed |
| 13 — deploy | **not started** | AWS CDK discarded with the pivot; nothing deployed to Google Cloud |
| 14 — evals | not started | `evals/` is `.gitkeep` only |
| 15 — submission artifacts | partial | README and architecture diagram exist; video and eval report do not |

---

## 3. What is not built, and what is unverified

**Not built:** Phase 13 (any deployment), Phase 14 (evals), most of Phase 15,
and `app/observability` — which is why `get_trace` and `memory_trace` are the
two `ReadPort` methods still raising.

**Fifteen of forty-seven port methods still raise `NotImplementedError`**, each
naming the subsystem it needs, from one register (`app/api/adapters/unbound.py`).
The register held nineteen entries before `internal.evaluate_trigger`,
`internal.sweep_outbox`, `internal.deliver_event` and `write.wake_trigger` were
bound — an earlier revision of this file said "twenty", which was a quoted
number rather than a measured one. None returns
`None` or `[]`: in the UI an empty list is indistinguishable from a real empty
result. Re-measure with `python -c "from services.control_plane.app.api.adapters
import UNBOUND; print(len(UNBOUND))"` rather than trusting this number — other
lanes are binding methods too.

**Verified 2026-08-24, and it had never been run before:**

- **The product runs on its own record, end to end.** The web app was in
  FIXTURE mode — a banner on every screen reading *"no Provenance API is
  connected"* — and is now LIVE against the seeded cluster. `USD 2,020.00` on
  the dashboard is summed by the API from Kernel-written rows, not read from a
  file. Getting there needed three things that did not exist: a way to obtain a
  token under `PV_PLATFORM=local` (`scripts/mint_local_token.py`), a way for the
  web app to present one (`PV_API_TOKEN`, server-side only), and the Kernel
  replay actually run against the cluster.

- **The Kernel replay ran.** Every Kernel-written table was **empty in the live
  database** — 18,035 evidence rows and zero claims, zero beliefs, zero
  commitments, zero triggers. `make seed` now replays 11 curated proposals
  through `MemoryKernel.commit()`: claims 36, proposals 11, decisions 11,
  beliefs 9, commitments 4, fulfillments 2, prospective triggers 2, state
  transitions 8, outbox events 8. `conflicts` stays 0 by design — the demo
  performs that one. The seed refused first, correctly: `pv_migrator` resolves
  to `provenance_ci` while every other role resolves to `provenance`, and it
  would rather stop than write half a corpus to each.

- **Nine live routes were returning `500`,** including every case docket, and no
  test in the repository could see it. Four contract declarations disagreed with
  the server — a `Money` object where a decimal string was declared, `null`
  where a `Record` was, a `context` every case was assumed to have. TypeScript
  was satisfied *because* the claims were false, and the fixtures encoded the
  same misreading, so 65 component tests passed against a wall of 500s.
  `make route-sweep` now walks every route with ids read from the API;
  `D-12-003`.

- **A capability proof verified only during the second it was issued.**
  `TRIGGER_EVALUATION` and `ACTION_INTENT` derived their expiry from `now` and
  put it inside the MAC, so the number changed once a second. Measured: issue
  once, verify six times over two seconds — 2 pass, 4 refuse, with nothing
  changing but the clock. Those two kinds gate both demo reveals, and the
  failure is *intermittent*, so it reads as a flaky network. `D-08-003`.

- **The control plane starts and reaches the database.** `make run-api` serves
  `GET /v1/version` with `"db_ok":true`, and `GET /v1/cases` without a token
  returns `401 UNAUTHENTICATED` with a `trace_id`. Neither had been observed.
  Getting there took three fixes, and the middle one is the interesting one:
  the recipe passed `--loop asyncio` under a comment claiming that flag was what
  made Windows work, and on uvicorn 0.40 **that flag selects the proactor loop**
  — the one psycopg refuses. The server started, answered 200, and reported
  `db_ok=false` against a cluster that was entirely healthy. Under a selector
  loop the same DSN connects as `pv_kernel_writer` and counts **18,035** rows in
  `evidence_items`, the corpus size canon records. `D-08-001`, `D-08-002`,
  `D-00-049`.

**Unverified, and it matters more than anything above:**

- ~~No Gemini model id has been invoked.~~ **Settled 2026-08-24.** Every canon
  id was invoked, not listed: `PASS 11, FAIL 0, CANNOT RUN 0`, exit 0,
  transcript at `ops/gemini-probe.txt`. `gemini-embedding-2` returns 1536
  dimensions at **L2 norm 1.0000003** with zero drift across two calls;
  `gemini-embedding-001` returns **0.6935943** in the same run, which is the
  measurement the profile's `caller_must_normalize=True` was asserting on the
  strength of a documentation quote. Native multimodal works, so there is no OCR
  service in this pipeline.

  Two of that transcript's first-run verdicts were **wrong, and both were
  defects in the probe** — `D-00-046` reported PASS for three ids that answered
  nothing, and `D-00-047` reported a capability FAIL that was a 1×1 transparent
  pixel the API rejects. An 8×8 PNG of the same 75 bytes succeeds. The probe now
  decides PB-G2 through `chat_verdict()`, a pure function with its own tests.

  **What is still not measured: throughput.** The AI Studio rate limits are
  per-tier and visible only in the dashboard. Re-embedding 18,035 texts is the
  longest unattended job remaining and nothing here touched it.
- ~~**No agent graph has touched a live model.**~~ **Settled 2026-08-24.**
  `python scripts/run_ingestion_graph.py` walks
  `agents/runtime/graphs/ingestion_graph.run_ingestion` over a seeded artifact
  with a real `google-genai` client, the byte-exact `pv-extract-1.1.0` and
  `pv-resolve-1.1.0` assets, and the real `ExtractionResult` contract.
  Transcript at `ops/agent-graph-live-run.txt`: three artifacts,
  **PASS 34 / FAIL 2 / CANNOT RUN 8**, both tiers invoked —
  `gemini-3.5-flash-lite` (E) and `gemini-3.7-flash` (R) — 37,050 input,
  7,922 output and 12,678 thinking tokens, and **21 `agent_runs` rows** where
  there were 0, each carrying `model_calls[]` attribution. Those verdict counts
  are **one run**, not a constant: the model is nondeterministic, and across
  seven runs the same three artifacts produced between one and three FAILs as
  Tier R exhausted or did not exhaust its single repair. The rows are the
  durable part — `agent_runs.model_calls[]` records every call that was
  actually made.

  Three things only a live call could have found. **(1) The shipped
  `GeminiClient` cannot send `ExtractionResult` at all**: `google.genai.types.
  Schema` is `extra="forbid"` and refuses the `ge`/`le` that `Confidence`
  generates and the `prefixItems` that `bbox` generates, and the API returns
  `400 INVALID_ARGUMENT` for a `$ref` document and for `maxItems` above an
  object item. The whole router suite is green because every test sends
  `ToyOutput`. `agents/runtime/model_router/wire_schema.py` derives the wire form
  from the contract; the four incompatibilities have their own unit tests.
  **(2) `validate_extraction` requires character-exact spans a language model
  cannot produce** — the first live run ended `SCHEMA_REPAIR_EXHAUSTED /
  SPAN_TEXT_MISMATCH` on every candidate, twice. Anchoring `exact_text` inside
  the block it cites keeps the defence and drops the arithmetic.
  **(3) `build_memory_proposal` raises past the graph boundary**, contradicting
  `run_ingestion`'s "the loop never raises", whenever evidence registration
  returns a partial map: a commitment whose source claim was filtered out
  reaches `MemoryProposal` and fails its cross-reference validator. Reproduced
  on `northline-final-invoice`.

  Two things the run did **not** do, recorded as `CANNOT RUN` rather than
  skipped: it never submitted to the Memory Kernel, and it registered no new
  evidence. The second is a gap — `evidence_items.embedding` is `VECTOR(1024)`
  and `ck_evidence_embedding_model` admits only Titan, so the only legal INSERT
  carries `embedding = NULL` in an append-only table. The first is a **decision**
  as of 2026-08-24: `app/proposals/submission.py` now provides the app-side
  `memory_proposals` INSERT and `internal.submit_proposal` has left the unbound
  register, so the door opens — but a commit writes claims, beliefs, commitments
  and `kernel_decisions` into the corpus `db/seeds/MANIFEST.json` asserts exact
  row counts over, while other lanes are verifying against it.
- **No action intent has executed against a real sink.**
- ~~**Prospective memory has no production path.**~~ **Closed 2026-08-24.**
  `commit_trigger_evaluation` now exists in the Memory Kernel
  (`app/memory_kernel/trigger_commit.py`), `SqlTriggerStore` and
  `SqlProjectionReader` satisfy the other two Protocols, and
  `internal.evaluate_trigger` and `write.wake_trigger` are bound and deleted
  from `UNBOUND`. **Still unverified against the cluster** — every test below
  is hermetic, and "the statements are shaped to the CHECKs" is a weaker claim
  than "the cluster accepted them".

  Writing the commit against the real DDL found **three defects a fake Kernel
  could not have caught**, each of which would have refused every wake:

  1. `PROPOSAL_MODEL_ID` was `deterministic:trigger-eval`, which
     `ck_memory_proposals_model` has never admitted — not at `0005`, not at
     `0009`. It is `deterministic.kernel` now, which `0009`'s own comment names
     as the id the Kernel writes `TRIGGER_EVALUATION` proposals under.
  2. `IDEMPOTENCY_SCOPE` was `TRIGGER_EVALUATION` and
     `ck_idempotency_scope_shape` is `^[a-z][a-z0-9_.]{2,63}$`. The claim is the
     fire transaction's **first** insert, so nothing would ever have committed.
  3. `uq_outbox_events_aggregate_event` is
     `UNIQUE (aggregate_type, aggregate_id, aggregate_version, event_type)`, and
     a no-op does not move `cases.revision` — so two consecutive no-op wakes on
     one trigger would have collided had the trigger aggregate been versioned by
     the case revision. It is versioned by `evaluation_version`.

  All three are the same lesson: a Protocol satisfied only by a test double is
  tested against a thing with no constraints. Prospective memory is one of the
  four things `00_PRODUCT.md` §2.2 claims ordinary RAG structurally cannot do,
  and it is the demo's second reveal, so the distance between "the evaluator is
  built" and "the capability runs" was the widest gap in the build.

- **The curated proposals give every commitment `local_id="cm_001"`**
  (`scripts/seed/proposals.py:924`). Any binding-recovery rule keyed on
  `local_id == "cm_<binding>"` therefore refuses **both** curated triggers and
  leaves `prospective_triggers` permanently empty. The underlying gap is that
  `ProposedTrigger` carries no `bindings` map, so the binding a predicate needs
  has to be recovered by heuristic rather than read.
- **The corpus is still Titan-embedded.** The 18,035 vectors in `evidence_items`
  are 1024-dimensional AWS Titan output. `PV_EMBEDDING_PROFILE` defaults to
  `titan-v1` so retrieval queries the space the corpus was actually written in.
  Migration `0009` is written, tested, and **deliberately unapplied** — it drops
  the embedding quartet and refuses without an exact-count acknowledgement.

---

## 4. Why gate G-0 is still REJECTED

Unchanged in substance: the repository is not public (deliberate, timed to
submission), `G0.4` cannot run until something is pushed, `G0.5`'s substance was
proved through `psql` and recorded as a deviation, and open MAJOR defects reject
on their own under §4.3.

`CANNOT RUN` is recorded distinctly from `FAIL` throughout. That distinction is
the whole lesson of `D-00-005`, and it recurred this session as the sharpest
defect found: `PostgresActionStore.grounding_snapshot` returned `frozenset()` —
a *real answer* meaning "supports nothing" — where it meant "never loaded".

**No gate has been signed.** `ops/gates/PHASE_00.md` carries a real REJECTED
verdict; `PHASE_01` through `PHASE_15` still hold the unfilled template. Built
is not the same claim as gate-passed.

---

## 5. Defects worth knowing about

Found by running things, not by reading them.

**On the count.** 61 records: 28 CLOSED, and **33 that block a gate** — 15
`OPEN`, 9 `AWAITING_REVERIFY`, 9 `TRIAGED`. Earlier revisions of this file said
"15 open, 0 BLOCKER", which counted only `status == OPEN`. §7.4 of the defect
protocol is explicit that `AWAITING_REVERIFY` "blocks the gate exactly as OPEN
does", and **`D-04-001` — the payment denial — is a BLOCKER sitting at
`AWAITING_REVERIFY`**. Its fix is applied and close-proved; it cannot reach
CLOSED because that needs a 40-character fix SHA and nothing is committed. So
the honest statement is one blocking BLOCKER, discharged by a commit and not by
any further code.

**A payment *denial* extinguished the debt.** `payment_not_received` coerces to
`PaymentValue(asserted=False)` — the denial flag — and `_apply_payment` read
`amount` and `currency` and never `asserted`. An assertion and its denial
produced **byte-identical** plans: `ACCEPTED`, `FULFILLMENT_ADMITTED`,
outstanding `0.0000`, zero conflicts. Every DDL guard passed on the wrong
numbers. This inverted the product's central claim — a counterparty's denial did
not merely become a fact, it discharged the obligation. Fixed; `D-04-001`.

**The execution idempotency key was minted, not read.** §9.11 requires it to
*equal* `action_intents.idempotency_key`. Every execution of a legitimately
approved intent would have failed `409` the moment a human edited one word of
the draft — the ordinary path, not an edge case.

**`ann_search()` returned zero rows for every query.** A lifecycle filter applied
to a statement whose outer `SELECT` does not project the column. No exception,
no warning; a second entry point canon says should not exist. Deleted.

**Kernel-armed triggers would have failed at wake.** The stored predicate had no
`{ast_version, bindings, predicate}` envelope, and `bindings` is where commitment
paths resolve — so every hero trigger would have raised `UNBOUND_COMMITMENT`,
months after arming.

**Settings required 27 environment variables, 15 of them AWS-specific**, so a
judge could not construct settings at all. Now 11 core plus 2 for Google, with
requirements *conditional on the platform* rather than deleted.

**Two registries for one fact, twice.** `Settings` read `COCKROACH_KERNEL_URL`
while the seed read `PV_DB_KERNEL`; the kernel pool silently failed to open
while the app pool worked and the process started anyway. The earlier instance
split the seed across two databases and reported `26 tables checked, 26 match`
against a database holding zero evidence rows.

**A conftest ImportError aborts the entire pytest session**, so one broken
directory silences every other test and exits **2** — which a gate checking for
`1` misreads. `D-00-044`, third of its kind after `D-00-005` and `D-00-014`.

**Nine vacuous or inverted assertions**, including a length compared against
itself, a `DROP COLUMN` check that matched the docstring explaining the rule,
and a guard carrying `pytest.mark.unit` that therefore could not run during the
collection abort it existed to detect.

---

## 6. What needs a human

0. **Apply `0009a`.** One command, and it is the last thing standing between a
   working agent and the Kernel:

   ```bash
   set -a; . ./.env; set +a
   export COCKROACH_DATABASE_URL="$(python -c "import os,re;print(re.sub(r'/(provenance_ci|defaultdb)(\?|$)', r'/provenance\2', os.environ['PV_DB_MIGRATOR']))")"
   python -m alembic -c alembic.ini upgrade 0009a_widen_proposal_model_check
   ```

   **Upgrade to `0009a` by name, never to `head`.** `head` is
   `0009_gemini_embedding_plane`, which drops `evidence_items.embedding` and
   destroys all 18,035 Titan vectors.

   `internal.submit_proposal` is bound and builds a valid typed
   `MemoryProposal`; the applied schema then refuses the INSERT with
   `CheckViolation: ck_memory_proposals_model`, because `0005` admits only three
   Bedrock-era Anthropic ids — every one of which was proved un-invocable — plus
   `deterministic.kernel`, which an agent must not claim, since that field is
   what makes the model attribution checkable. `0009` widens the CHECK, but it
   bundles the widening with the destructive re-embed. `0009a` is the widening
   alone, inserted between `0008` and `0009` so the chain stays a single linear
   head. Widening a CHECK cannot invalidate an existing row.

   I wrote and verified it but did not apply it: a schema change against the
   live cluster is yours to authorise.

1. **Rotate the database password.** It was transmitted in a chat transcript.
   Nothing is pushed, so nothing is exposed yet. **Rotate the Google API key
   too** — it arrived the same way.
2. ~~Supply an AI Studio API key.~~ **Done 2026-08-24** — in `.env`, probe run,
   transcript at `ops/gemini-probe.txt`. Two things follow from it:
   **rotate that key before the repository goes public** (it was transmitted in
   a chat transcript, same as the database password), and **say whether billing
   is enabled** — the re-embed of 18,035 texts is still unscheduled and a
   free-tier limit decides whether it is a fifty-minute job or an overnight one.
3. **Decide about `ops/`.** `D-00-037` measures it: the password appears in
   **zero** files, but the cluster hostname is in 12, the cluster id in 9 and the
   SQL username in 42. `gitleaks` reports clean because a hostname is not
   credential-shaped, which is exactly why that record exists. Three options are
   written out there; committing as-is is one of them.
4. **A GCP project with Cloud Run**, ideally `us-east4` — same metro as the
   CockroachDB cluster on AWS `us-east-1`, so the cross-cloud hop stays in
   single-digit milliseconds.
5. **Commit.** 462 untracked files. A commit turns the one failing test green
   and lets `D-04-001` move from `AWAITING_REVERIFY` to `CLOSED`, which needs a
   fix SHA the protocol will not let anyone fabricate.
6. **Decide on `db/seeds/vectors.parquet`** — 67 MB, not gitignored.
7. **Make the repository public** and push, timed to submission.

---

## 7. Ground rules that cost something to learn

- **`CANNOT RUN` is not `FAIL`**, and **absence is not emptiness**. Both lead to
  opposite decisions, and both were violated this session.
- **A green run on a sabotage assertion is a gate failure**, not a pass.
- **`make` reports its own exit code, not the recipe's.**
- **Piping through `tail` discards the exit code.** It produced a false green
  three times before this session and once during it.
- **A sentence predicting which way something fails is a claim to be executed,
  not written.** It is read at the moment of failure, by someone who cannot yet
  see the mechanism.
- **A guard placed inside the thing it guards cannot fire.** No in-session pytest
  test can detect a session-wide collection abort.
- **Assert which, not how many.** A count admits any substitute; naming the
  members fails on a third *and* on the right one being swapped for the wrong one.
- **An unobserved mapping is a guess with a comment on it.**
- **A test pinned to a state fails when the state legitimately changes**, and the
  pressure then is to delete it — removing the guard exactly when there is
  finally something to guard. Five such tests flipped the day the probe ran.
  Assert the *property* (claim agrees with evidence) rather than the *state*
  (claim is currently False), and both directions stay checked.
- **A probe measures the probe until proven otherwise.** Two of PB-G2 and PB-G6's
  first verdicts described the fixture and the token budget, not the model.
- **A fixture and a type written by the same hand agree with each other and
  prove nothing.** Both were derived from one reading of the spec, so the suite
  was green while every live route was 500. Only a captured response can
  disagree.
- **`CANNOT RUN` is not `FAIL` applies to HTTP too.** An unbuilt capability
  answered `500 INTERNAL_ERROR` — "something went wrong on our side" — when
  nothing had gone wrong and nothing had been attempted. It now answers `501`
  and names the subsystem it waits on.
- **Measure the page you think you are measuring.** Three readings this session
  were taken against a transiently-404ing route and a viewport that had not
  resized; each looked like a clean pass.
- **Verification against a database another agent is writing is meaningless in
  both directions.**
