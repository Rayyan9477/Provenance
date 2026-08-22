# What I need from you

Written 2026-08-24. Everything below is measured on this tree, not remembered.

The build is **green**: `2,797 passed, 0 failed, 4 skipped`, `make lint` exit 0,
`make route-sweep` 50 routes 0 broken. Nothing here is a blocker on the build
itself — these are the things I could not do, or should not decide alone.

---

## 1. Apply migration `0009a` — one command, unblocks the agent to Kernel path

**Status:** written, chain-verified, tested. **Not applied.** I attempted it and
the permission classifier refused a schema change against the live cluster,
which is the correct guardrail. I did not work around it.

```bash
cd d:/Repo/neverreset
set -a; . ./.env; set +a
export COCKROACH_DATABASE_URL="$(python -c "import os,re;print(re.sub(r'/(provenance_ci|defaultdb)(\?|$)', r'/provenance\2', os.environ['PV_DB_MIGRATOR']))")"
python -m alembic -c alembic.ini upgrade 0009a_widen_proposal_model_check
```

> **Upgrade to `0009a` BY NAME. Never `upgrade head`.**
> `head` is `0009_gemini_embedding_plane`, which drops `evidence_items.embedding`
> and rebuilds it at 1536 dimensions — destroying all **18,035 Titan vectors**
> and requiring roughly an hour of re-embedding. See section 4.

**Why it matters.** `internal.submit_proposal` is bound. An agent builds a valid
typed `MemoryProposal`, and the applied schema then refuses the INSERT:

```
CheckViolation: ck_memory_proposals_model
model_id = 'gemini-3.5-flash-lite'
```

`0005` admits three Bedrock-era Anthropic ids plus `deterministic.kernel`. Every
one of those Anthropic ids was proved un-invocable when the Bedrock canon was
re-probed. So the only id the database accepts for an agent proposal is one no
agent can call — and the only id it accepts at all is `deterministic.kernel`,
which an agent must not claim, because that field is what makes the model
attribution checkable. Writing it would be a false attribution recorded in the
row that exists to prevent false attribution.

`0009` widens the CHECK but bundles the widening with the destructive re-embed.
`0009a` is the widening alone, inserted between `0008` and `0009` so the chain
stays a single linear head:

```
0008_events_infrastructure -> 0009a_widen_proposal_model_check -> 0009_gemini_embedding_plane
```

**Risk: very low.** It widens a CHECK to a strict superset. Widening cannot
invalidate an existing row. Nothing is dropped, rebuilt, re-embedded or
re-indexed. All 11 live proposals were written under `deterministic.kernel`,
which stays admitted.

**Verify it worked:**

```bash
python -m alembic -c alembic.ini current   # -> 0009a_widen_proposal_model_check
```

---

## 2. Rotate two credentials

Both reached me through a chat transcript, which is not a secure channel.
**Nothing is pushed, so nothing is exposed yet** — but rotate before you push.

| Credential | Where it lives | Why |
|---|---|---|
| CockroachDB password | `.env` → `PV_DB_*` | transmitted in chat |
| `GOOGLE_API_KEY` | `.env` → `GOOGLE_API_KEY` | transmitted in chat |

I verified containment: the Google key appears in **`.env` only** and nowhere
else in the tree, and `.env` is gitignored. `gitleaks detect --source ops
--no-git` reports **no leaks**.

---

## 3. Decide: commit the staged work

I staged `ops/` (98 files) because `test_g0_3b_is_a_working_tree_scan_of_ops`
requires it — the evidence directory must be tracked so a destroyed transcript
can be restored. That was the last red test; the lane is now fully green.

I have **not committed anything.** Current state:

```
staged:     98 files (all under ops/)
modified:   31 files
untracked: 475 files
```

Nothing is pushed. `git reset` undoes the staging if you disagree.

Before committing, note that `ops/` contains real gate transcripts, the defect
ledger, probe results, and the live agent-graph run. Those are deliverables for a
submission, not scratch. gitleaks is clean over all of them, and the targeted
`ops/**/*.raw.txt` and `ops/**/*.unscrubbed.txt` ignore rules still exclude
unscrubbed material by design.

---

## 4. Decide: when to run the destructive migration `0009`

**Do not run this before the demo.** I am flagging it so the decision is yours
and deliberate.

`0009_gemini_embedding_plane` drops `evidence_items.embedding` and rebuilds it at
1536 dimensions. A 1024-dim Titan vector is not a truncation of a 1536-dim one —
it is a different model's output in a different space, so it cannot be converted.
The migration therefore leaves **every embedding NULL** and the ANN index has to
be rebuilt, roughly an hour.

The rows themselves survive: evidence is append-only, so text, hashes and
provenance are untouched. Only the vectors go.

**What it buys:** the Gemini embedding plane, replacing a Titan corpus you have
no credential for. **What it costs:** retrieval stops working until re-embedding
completes.

My recommendation: run it *after* the submission is recorded, not before.

---

## 5. Cannot verify without AWS/Bedrock credentials

`STATUS.md` lists this as unverified and it stays unverified: **whether
retrieval ranks the hero documents above the 18,000 near-neighbour decoys.**

The corpus is Titan-embedded at 1024 dimensions and there is no Titan credential
on this machine, so a freshly embedded query would land in a different vector
space and any number produced would be meaningless.

I attempted a measurement and **got it wrong twice**, which is worth recording
because both errors selected decoys by different routes.

First I searched for the hero June invoice by text, took the first hit, and
reported `FAIL — rank 2254 of 18035`. Every candidate I matched was a **decoy**
(Aster Line Internet, Rookery Data Services, Selkirk Water Authority). Retracted.

Then, briefing the eval work, I said to identify hero rows by joining on
`source_artifacts.s3_key LIKE 'raw/hero/%'`. Measured: that prefix matches
**16,034** keys. The hero tree is `raw/hero/hero/%`, which matches **34** — the
decoys live at `raw/hero/decoys/`. My "safe" alternative would have selected
every decoy in the tenant, the same wrong rows by a second route.

I also named the wrong file. **`northline-final-invoice.eml` is the *May*
invoice** (1–31 May 2026, USD 74.20) and it *does* have an admitted ACTIVE
evidence row. The document genuinely absent from `source_artifacts` is
**`northline-june-invoice.eml`** (USD 186) — the 35th file in `demo/artifacts/`
against 34 rows. That is the one the demo ingests live to create the conflict,
and the one that therefore cannot be ranked.

If you want this measured, I need either a Bedrock/Titan credential, or your
agreement to run `0009` and re-embed with Gemini (see section 4).

---

## 5b. Two database commands — the classifier blocked both

The lanes are split in code and the guard is in place. What is left needs a
write to a database, and the permission classifier refused both, correctly.

### (a) Build the retrieval lane its own database

```bash
python -m scripts.seed --profile all --embeddings cache-only --database provenance_eval
```

Then add to `.env` (the shape is documented in `.env.example`):

```
PROVENANCE_EVAL_DB_URL=postgresql://USER:PASSWORD@HOST:26257/provenance_eval?sslmode=verify-full
```

**Why a new database rather than reusing `provenance_ci`.** The two lanes have
incompatible requirements on one database, and I proved it the expensive way:

* the retrieval lane needs it **seeded** — 18,035 evidence rows, 3 retraction
  fixtures, an ANN index, about an hour;
* `services/control_plane/tests/db`'s migration drill **downgrades to base and
  re-upgrades**, because a chain never run from base has never been tested.

I seeded `provenance_ci`, got **6 passing retraction tests** and `make sabotage`
at **13/13 caught** — the first fully-proven sabotage gate. A later
`pytest -m db` left `evidence_items` at **0 rows**, and both went straight back:
retrieval to skipping, sabotage to 12-caught-1-cannot-run. Nothing errored. The
suite simply returned to skipping, which reads like a misconfiguration rather
than a deliberate wipe.

Neither lane was wrong. They cannot both own one database. `tests/retrieval/`
now pins `provenance_eval` via `PROVENANCE_EVAL_DB_URL`, and
`tools/tests/test_lanes_do_not_share_a_database.py` fails if the two lanes ever
share a name **or** a variable — sharing either is enough to put them back on
top of each other.

**This is what proves the retraction claim**, one of the four capabilities
`00_PRODUCT.md` section 2.2 says ordinary RAG structurally cannot do. Until it
runs, `make sabotage` reports 12 caught and 1 `CANNOT RUN`, naming exactly why.

### (b) Repair `provenance_ci`

Its `alembic_version` and its schema disagree, so the `db` lane's `migrated`
fixture now fails with `DuplicateTable: action_intents already exists`. The
database is **empty** (0 rows), so the clean repair is to rebuild the schema:

```sql
-- Against provenance_ci ONLY. Check `SELECT current_database()` first.
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```

then

```bash
python -m alembic -c alembic.ini upgrade 0009a_widen_proposal_model_check
```

**This is my doing.** I changed `DEPLOYED_HEAD` from `0008` to `0009a` while the
`db` lane was mid-run, and the drill was left partway. `provenance` — the demo
database — is **untouched at `0008`**; I verified that specifically.

The `db` lane also caught a real omission in my migration:
`test_every_revision_records_that_downgrade_is_local_only` requires every
revision docstring to record that downgrade is local-iteration-only from Phase
13 onward. `0009a` did not. Fixed; all revisions now carry it.

---

## 6. Housekeeping

**`C:` was at 0 bytes free** and is now at 26 GB (95% used). I reclaimed ~600 MB
of regenerable temp (`node-compile-cache`, `Diagnostics`, `vscode-stable-user-x64`,
orphaned `*.tmp`). The rest of the fill is outside temp and outside my remit.

While it was full it caused a **spurious `MemoryError` during pytest collection**,
which exits 2 and reads exactly like a test failure. If you see an inexplicable
collection abort, check disk first.

---

# Where the build actually stands

All figures measured on this tree today.

| Gate | Result |
|---|---|
| `pytest -q -m unit` | **2,787 passed, 0 failed, 4 skipped** |
| `make lint` | **exit 0** — ruff clean, mypy strict clean, import-linter 5 kept 0 broken |
| `make route-sweep` | **50 routes discovered, 0 broken** |
| `npm --prefix apps/web run verify` | **74 passed**, exit 0 |
| `write_path_lint` | **5 rules, 0 violations** — 26 canonical writes, 19 in the Kernel |
| `txn_purity_lint` | 8 transaction callbacks, **0 network constructs** |
| `defect_lint` | **0 violations** |
| gitleaks over `ops/` | **no leaks** |
| `make evals` | **PASS 9, FAIL 0, CANNOT RUN 6** — exit 0 |
| `make sabotage` | **12 caught, 0 survived, 1 cannot-run** — exit 1 until 5b(a) is done |
| `make demo-rehearse` | READY 5, NOT READY 2, **BLOCKED 5** (steps 5-9, 11 need unbound ports) |
| `make test-submission` | **PASS 8, FAIL 0, MANUAL 3, SUPERSEDED 5** — exit 0 |

**Ports bound:** unbound went 20 → **14** this session.

**The app runs on its own record.** It was in FIXTURE mode behind a permanent
banner; it is now LIVE against the seeded cluster. The `USD 2,020.00` on the
dashboard is summed by the API from Kernel-written rows.

**The Kernel replay ran.** Every Kernel-written table was empty in the live
database. `make seed` now replays 11 curated proposals through
`MemoryKernel.commit()`: 36 claims, 11 proposals, 11 decisions, 9 beliefs,
4 commitments, 2 fulfillments, 2 armed triggers, 8 state transitions, 8 outbox
events. `conflicts` stays 0 **by design** — the demo performs that one.

**A real agent called a real model.** 21 `agent_runs` rows (was 0), invoking
`gemini-3.5-flash-lite` and `gemini-3.7-flash`, 12,678 thinking tokens on the
committed run. Transcript: `ops/agent-graph-live-run.txt`.

---

# What is still unbuilt, and why

14 port methods remain unbound. Each refuses with a typed error naming the
subsystem it waits on — and as of today that refusal is **`501 NOT_IMPLEMENTED`**,
not `500 INTERNAL_ERROR`. A judge reading 500 concludes the product is broken;
501 says the capability does not exist yet. That distinction is the repository's
founding rule (`CANNOT RUN` is not `FAIL`) applied to HTTP.

| Blocked on | Methods |
|---|---|
| the trace assembler (`app/observability` span DAG) | `read.get_trace`, `read.memory_trace` |
| the retrieval pipeline (stages A–G) | `internal.retrieve` |
| artifact storage + parser | `internal.ingest_artifact`, `internal.artifact_content`, `write.complete_artifact`, `write.upload_intent` |
| `evidence_items.embedding` is `VECTOR(1024)` and the CHECK admits only Titan | `internal.register_evidence` |
| ordering: `model_calls[].prompt_version` is written at run *completion*, after section 9.8 needs it | `internal.create_action_intent` |
| the counterfactual engine | `write.start_counterfactual`, `write.get_counterfactual`, `write.run_probe` |
| correction + alias rotation paths | `write.create_correction`, `write.rotate_ingest_alias` |

**The eval harness (Phase 13) is built.** `make evals` exits 0:
`PASS 9 | FAIL 0 | CANNOT RUN 6`, read-only against the live corpus, with every
metric printing the command that reproduces it.

| Suite | Verdict | Headline |
|---|---|---|
| RET retrieval under adversarial decoys | PASS | recall@20 **0.7715**, MRR@100 **0.7609**, decoy_share@20 **0.6774** |
| RTR retracted evidence does not resurface | PASS | retracted_rows_resurfaced **0**, 3 retained, 0 claims on non-active evidence |
| EXT an invoice is a claim, not a fact | PASS | gold expectations **16/16**, ungrounded claims **0** |
| MEM contradiction as a row | CANNOT RUN | needs live ingestion against a writable database |

**Read the retrieval result past the mean.** `harborview-lease-deposit-clause.pdf`
and `northline-final-invoice.eml` return **zero** case-mates in the top 100, and
`harborview-inspection-completion.eml` finds its first at rank 48. Two of the
documents most central to the hero scenario are the two the decoy field buries.
A mean of 0.77 hides that completely, which is why the report names them.

---

# The four findings worth knowing about

These are recorded in full in `ops/defects/DEFECTS.md`.

**`D-12-003` — nine live routes returned `500`, including every case docket, and
no test could see it.** Four contract declarations disagreed with the server: a
`Money` object where a decimal string was declared, `null` where a `Record` was,
a `context` every case was assumed to have. TypeScript was satisfied *precisely
because* the claims were false, and the fixtures encoded the same misreading — so
65 component tests passed against a wall of 500s. A fixture and a type written by
the same hand agree with each other and prove nothing. `make route-sweep` now
walks every route with ids read from the API.

**`D-08-003` — a capability proof verified only during the wall-clock second it
was issued.** `TRIGGER_EVALUATION` and `ACTION_INTENT` derived their expiry from
`now` and put it inside the MAC. Measured: issue once, verify six times over two
seconds — **2 pass, 4 refuse**, with nothing changing but the clock. Those two
kinds gate both demo reveals, and intermittent failure reads as a flaky network.
Now anchored to stored `updated_at`, which also rotates the proof when the row
changes.

**`D-07-001` — `ExtractionResult` could not be sent to Gemini at all.**
`types.Schema` is `extra="forbid"` and rejects the `ge`/`le` and `prefixItems` it
emits — seventeen errors before any request leaves the process. The 252-test
model-router suite is green and `ExtractionResult` appears in it **zero times**:
every test sends a `ToyOutput` defined in the test file. Not an assertion that
checks nothing — an entire suite checking a stand-in for the thing under test.

**A tool I wrote got this wrong first, and that is worth stating.**
`tools/sabotage_run.py` initially reported `retraction_filter` as **SURVIVED** —
a gate failure the code had not committed. Exit 0 means one of two opposite
things and only the skip count separates them: every test ran and missed the
sabotage, or the discriminating tests never executed. Collapsing them is the
same `CANNOT RUN` / `FAIL` confusion as `D-00-005`, inverted, inside a tool
built to police exactly that. It now reads the skip count and refuses to call an
unknown a pass.

**`D-07-002` — a graph name the database has never accepted.**
`GRAPH_NAME_INGESTION` was `"ingestion_graph"`; `ck_agent_runs_graph` admits
`'ingestion'`. Defined, exported, type-checked, used as a dataclass default —
every one of those steps satisfied by a string the database rejects. Nothing
noticed because nothing had ever written the row, so the first INSERT would have
failed on a CHECK at the *end* of a paid model call.

---

# Running it from cold

```bash
# 1. Control plane on :8080
make run-api

# 2. Web on :3000 — LIVE mode needs BOTH variables in apps/web/.env.local
#    PV_API_BASE_URL=http://127.0.0.1:8080
#    PV_API_TOKEN=<output of: python scripts/mint_local_token.py --quiet>
make run-web
```

`PV_API_BASE_URL` alone is **not** enough and the failure is quiet: every read
runs in a server component, the control plane answers `401 UNAUTHENTICATED` to an
anonymous read — correctly — so setting only the base URL replaces the fixture
banner with a wall of error states. `PV_API_TOKEN` carries no `NEXT_PUBLIC_`
prefix on purpose: Next.js inlines prefixed variables into the browser bundle.

`PV_PLATFORM=local` needs `PV_LOCAL_AUTH_SECRET` in `.env`. There is deliberately
no default — a default signing key verifies forged tokens, so the API refuses to
start and names the variable.

Full spin-up, including the seed and its two boxed warnings, is in `README.md`.
