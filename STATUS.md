# Provenance — build status

Written 2026-08-28. Every number was measured from this tree or this cluster
today. None is carried forward from a plan.

**Re-measure rather than quote.** These figures have moved several times within
a single session; a number written down is a timestamp. The command is beside
each claim so a reader can check rather than trust.

---

## 1. Where the build is

| | |
|---|---|
| Python suite collected | **3,830** |
| Unit lane | **2,951 passed, 0 failed, 4 skipped** (`pytest -q -m unit`) |
| Frontend | **95 passed**, exit 0 (`npm --prefix apps/web run verify`) |
| Adversarial lane | **139 passed, 1 skipped** (`pytest -q -m adversarial`) |
| Isolation lane | **84 passed, 5 skipped** — cross-tenant and cross-user leakage proofs |
| `ruff check` / `ruff format --check` | clean, 476 files |
| `mypy --strict` | clean, 23 source files |
| import-linter | **5 contracts kept, 0 broken** |
| `write_path_lint` (now in `make lint` and CI) | **5 rules, 0 violations** · 27 canonical writes, 19 in the Kernel |
| `txn_purity_lint` | 8 transaction callbacks, **0 network constructs** |
| `invariant_map_check` | **5 invariants, 5 mapped, 0 UNPROVEN**, exit 0 |
| `defect_lint` | **0 violations** |
| `contract_lint` (now in `make lint` and CI) | **3 rules, 0 violations** |
| Sabotage matrix | **13 entries** |
| Defect ledger | **75 records** · 33 CLOSED · **0 BLOCKER open** |
| Gemini probe | **PASS 11, FAIL 0, CANNOT RUN 0**, exit 0 (`ops/gemini-probe.txt`) |
| Live agent graph | **PASS 34, FAIL 0, CANNOT RUN 8**, exit 0 · both tiers invoked (`gemini-3.6-flash`, `gemini-3.5-flash-lite`), **37 `agent_runs` rows**, `strong_resolution` PASS on both resolving artifacts (`ops/agent-graph-live-run.txt`) |
| **Deployed** | **two Cloud Run services, serving** — see §2 |
| Live route sweep | **50 discovered, 0 broken**, against Cloud Run (`ops/route-sweep-live-cloudrun.txt`) |
| Live hero ingest | **PASS 13, FAIL 0, CANNOT RUN 1**, exit 0 (`ops/ingestion-live-run.txt`), re-recorded after `0009a` |
| Live demo rehearsal | **PASS 10, FAIL 0, CANNOT RUN 4**, exit 0 (`ops/demo-rehearsal-live-cloudrun.txt`) |
| Database invariants | **`VERDICT PASS`** — `V1 0 … V10 0  V11 3` on the live cluster (`PV_VERIFY_URL="$PV_DB_MIGRATOR" make db-verify`; the bare target points at `provenance_ci`, which the migration lane rebuilds from base) |
| Unbound port methods | **6 of 47** |

Per-area collection: kernel 631 · api 456 · db 410 · events 299 · auth 148 ·
mcp 135 · actions 76 · agents 310 · packages ~503 · tools ~250 · web 83.

---

## 2. Platform and deployment

Four facts decide what the rest of this file is describing, so they are asserted
first — `python -m tools.release_check` runs them ahead of everything else,
because nothing below matters if one of them is false.

| | State | Evidence |
|---|---|---|
| Models | Four Gemini ids **invoked**, all ≥ 3.5, highest 3.7 | `ops/gemini-probe.txt`, PASS 11 / FAIL 0. Read from the transcript rather than from configuration: configuration is a claim, a transcript is a measurement. |
| Model SDK | `google-genai` | Declared in `requirements-runtime.txt` **and** imported by two shipped modules, one at module level and one lazily. Declared-and-imported, because a dependency no module imports is a claim about a file. |
| Compute | **Cloud Run** — two services in `provenance-agentic-2026`, region `us-east4` | the URLs below |
| Database | **CockroachDB Cloud** on AWS `us-east-1` | Northern Virginia either way, so the cross-cloud hop stays in single-digit milliseconds rather than seventy-plus |

```
web            https://provenance-web-vaq74wztva-uk.a.run.app
control plane  https://provenance-control-plane-vaq74wztva-uk.a.run.app
```

`GET /v1/version` is unauthenticated so anyone can `curl` it, and reports
`fixture_mode: false`, `db_ok: true`, `agent_mode: LIVE`, and the `git_sha` of
the revision actually serving.

**Whether that sha equals `HEAD` is a measurement, not a standing fact.** Every
commit made after a deploy moves `HEAD` ahead of the deployed revision, and this
file said "equals HEAD" while it did not. `python -m tools.demo_rehearsal_live`
compares the two and reports it; re-deploy before recording.

**Read `db_ok`, not the status code** — startup deliberately survives a refused
pool and says so rather than crash-looping.

The web app renders `USD 2,020.00` summed by the API from Kernel-written rows,
with no fixture banner.

---

## 3. Phase status

| Phase | State | Note |
|---|---|---|
| 0 — scaffold, licence, probes | built, gate REJECTED | §6 |
| 1 — contracts and domain | built | |
| 2 — schema, migrations, seed | built | 26 tables, 5 views, seed idempotent |
| 3 — database runtime and retry | built | `40001` proved by real two-connection interleaving |
| 4 — Memory Kernel | built | |
| 5 — read models | partial | `state_proof` built; `app/observability` is 140 lines and settles runs only |
| 6 — embeddings and retrieval | **partial** | every stage built; **no executor composes them**, so `internal.retrieve` is unbound. See §4 |
| 7 — agent graphs | built | `google-genai`, 310 tests, 12-artifact injection suite |
| 8 — API and auth | built | 41 of 47 ports bound |
| 9 — actions, approval, executor | **partial** | `approve`, `reject`, `execute_action` and both reads bound; **`create_action_intent` is not**. See §4 |
| 10 — events, outbox, triggers | built | manual wake route added 2026-08-28 |
| 11 — MCP, SQL roles, agent views | built | base-table denial proved to come from the database |
| 12 — frontend | built and LIVE | 50 live routes swept clean against Cloud Run |
| 13 — deploy | **built** | two Cloud Run services, `deploy/` is the runbook |
| 14 — evals | built | `make evals` exits 0: PASS 9 / FAIL 0 / CANNOT RUN 6 |
| 15 — release artifacts | partial | README, diagram and gate rewritten; **walkthrough not recorded** |

---

## 4. What is not built, precisely

**The retrieval pipeline has stages but no pipeline.** `app/retrieval/` holds all
eight stages and they are good — `ann.py` parses the canonical predicate out of
the spec markdown so the SQL cannot drift from the document. But
`pipeline.py` is a `STAGES` tuple, a `call_order()` function and one boolean
constant; **no module runs A through G end to end.** That is why
`internal.retrieve` is unbound, and it is why the Judge Mode counterfactual
passes an empty `evidence` array to its MEMORY ON side. The State Proof it does
pass is rich — beliefs with grounding edges validated to render their evidence,
commitments with fulfillments, conflicts, derivations — so the memory-on side is
not evidence-starved. But retrieval is not on the live path.

**`internal.create_action_intent` needs a typed State Proof that nothing builds.**
The unbound register names two gaps and says both have seams. One does:
`prompt_version` is handled by `app/proposals/submission.py`, which accepts it as
a caller claim and records it explicitly as an assertion rather than a
cross-check. The other is deeper than the register says. `GroundingSnapshot.
from_state_proof` needs a `provenance_contracts.proof.StateProof`, and
**`build_state_proof` has no production call site** — it and `from_state_proof`
are reachable only from tests. The live State Proof is a dict payload assembled
independently in `adapters/read.py`. So binding this method means making the
read path produce a typed proof, which is a refactor of a live tested path
rather than a wiring job.

**The trace assembler does not exist.** `app/observability` is 140 lines and
settles agent runs. The seventeen closed trace node types would be assembled
from `agent_runs`, `state_transitions`, `kernel_decisions`, `outbox_events` and
`tool_calls`, all of which are persisted — it is assembly over existing rows,
but it is real work. `read.get_trace` and `read.memory_trace` answer **501
NOT_IMPLEMENTED** naming the subsystem, never 500.

**The 51-scenario eval corpus does not exist.** `CANONICAL_DECISIONS.md` freezes
it; `evals/datasets/schema/` and `evals/datasets/the_move/` are empty
directories. The harness records this as `MEM-03 CANNOT RUN` rather than scoring
around it.

**Gate batteries 1 and 3–15 are not implemented.** `make gate-0` and `gate-2`
run; the rest are deliberate non-zero stubs naming the phase that owns them.
`ops/gates/PHASE_01.md` … `PHASE_15.md` therefore still carry the unfilled
template. That is honest and it is also a gap: the gate system is one of this
build's strongest arguments and it has one recorded verdict, which is a
rejection.

**The walkthrough recording does not exist.**

---

## 5. Walkthrough, step by step

The end-to-end path a reader is expected to follow, measured against the
deployed revision by `python -m tools.demo_rehearsal_live`. Every verdict that
tool prints is computed; it has no constructor that accepts a bare PASS. The
State column below is not: it is written by hand from those runs and from the
transcripts it names, so it carries a risk a computed verdict does not, which is
that prose can go stale while the artifact it describes stays put. Step B is
where that happened, and the note under the table says how.

| Step | What it does | State |
|---|---|---|
| A | Dashboard: "The Move", 4 relationships, USD 2,020 outstanding | **works live** |
| B | Forward the June invoice; case flips RESOLVED → REOPENED | `ops/ingestion-live-run.txt`, re-recorded 2026-08-31: **PASS 13, FAIL 0, CANNOT RUN 1**. Passes to the Kernel's door; `commit_proposal` cannot run in a rolled-back transaction. Not run here: it is one-shot against the live database |
| C | State Proof: grounding and lineage, deterministic | **works live**, `model_used: null`, every belief grounded |
| D | Counterfactual: memory off vs on | two live runs recorded, `parity.all_equal` true. Wants the June invoice, so it follows B |
| E | Approve and send | **blocked** on `create_action_intent` — §4 |
| F | The landlord trigger fires on its own | **armed and reachable.** `COMMITMENT_DEADLINE` ARMED at `2026-06-15T00:01:00Z`; the wake route reaches its handler. Not fired — the first press disarms it |
| G | Memory Trace | **unbuilt** — §4 |

Four steps are `CANNOT RUN` in the rehearsal and two of those are deliberate:
B and F are each one-shot against the live database — the first ingest creates
the conflict, the first press disarms the trigger — so rehearsing either
spends it.

**Step B was stale, and has been re-measured.** `ops/ingestion-live-run.txt`
was recorded before migration `0009a` reached the cluster, and ended on a FAIL —
the app-side `memory_proposals` INSERT, refused by a `CheckViolation` on
`ck_memory_proposals_model`, because that constraint did not yet admit the
Gemini model ids — followed by a `CANNOT RUN` on `commit_proposal`, which had no
proposal row to decide. `0009a` widens exactly that constraint and was applied
to the live cluster on 2026-08-28. The transcript was not regenerated for three
days, which left those two steps unmeasured since the fix: neither passing nor
failing.

It was re-run on 2026-08-31 against the migrated cluster, in the same
rolled-back transaction, and now reads **PASS 13, FAIL 0, CANNOT RUN 1**. The
INSERT passes — "1 row written" — so the constraint accepts the Gemini id and
the whole path from `write.upload_intent` through the typed `MemoryProposal` to
the app-side write is measured against a real CockroachDB, every CHECK, foreign
key and generated column evaluated.

**`commit_proposal` still has not run on a live path, and the reason is
structural rather than a blocker.** The Kernel commits its own transaction; this
runner rolls back. A Kernel decision taken here would outlive the proposal row
it decided, so the runner refuses rather than producing a decision pointing at
nothing. `--persist` would settle it and would also spend step B, which is
one-shot and is deliberately not being spent. So: the path is proved to the
Kernel's door, and the Kernel's own commit on a live path is the one thing this
tree still does not show.

---

## 6. Why gate G-0 is still REJECTED

Unchanged in substance. `G0.4` cannot run until something is pushed, `G0.5`'s
substance was proved through `psql` and recorded as a deviation, and open MAJOR
defects reject on their own under §4.3.

**No gate has been signed.** `ops/gates/PHASE_00.md` carries a real REJECTED
verdict; `PHASE_01` through `PHASE_15` hold the unfilled template. Built is not
the same claim as gate-passed.

---

## 7. Defects

75 records: **33 CLOSED**, 18 OPEN, 15 AWAITING_REVERIFY, 9 TRIAGED. 42 still
block a gate — `AWAITING_REVERIFY` blocks exactly as `OPEN` does — but **no
BLOCKER remains open.** All five were closed on 2026-08-28 with full fix SHAs
found by `git log -S` on the symbol each fix introduced, and with counterfactuals
that were run rather than predicted.

Three of those counterfactuals changed the record, which is the whole reason the
protocol demands them:

- **`D-12-003`'s named verifying assertion was wrong.** The frontend
  contract-conformance suite cannot catch it — TypeScript types are erased at
  runtime, so reverting the declaration from `Money` to a decimal string cannot
  move a vitest result. Measured: 8 passed with the fix reverted. `tsc --noEmit`
  is what reads a declaration, and neutered it reports `TS2345` at three call
  sites in the case detail page — the page whose 500s are the defect.
- **`D-08-003`'s named assertion survived its own neutering.** It does not
  discriminate; two others do, and one of them is now the named assertion.
- **`D-04-001`'s close-proof carried a stale count** from an earlier run. 13
  failed / 8 passed neutered, 21 passed restored — not the 19 recorded.

Two close-proofs were nearly written as predictions — "neuter X and Y goes red" —
without being run. That is recorded in the ledger rather than quietly corrected.

---

## 8. What needs a human

1. **Record the walkthrough.** Live and unedited, following §5.
   `deploy/cloudrun.sh proof` prints the deployment evidence it should show.
2. **Rotate four credentials** before the repository is shared: the CockroachDB
   password, the Google AI Studio key, the Cloud credit coupon, and
   **`PV_API_TOKEN`**. The first three arrived through a chat transcript. The
   fourth was found in plaintext in a Cloud Run revision spec — which
   `gcloud run services describe` prints in full to anyone holding `viewer` —
   guarding an API deployed `--allow-unauthenticated`, so it was the entire
   authentication boundary. It is a `secretKeyRef` now and was rotated, but
   rotate again after the repository is shared.

   **The cluster hostname is a separate decision and rotation does not fix it.**
   `<cluster-host>.cockroachlabs.cloud` and the SQL usernames appear in **18
   files already on `origin/main`**. Either rewrite that history before pushing
   (`git filter-repo --replace-text`, safe here: solo repository, no forks), or
   accept it deliberately on a rotated password plus `sslmode=verify-full`.
   Leaving it undecided while the defect ledger records it as undecided is the
   one option that is not defensible.
3. **Decide on `db/seeds/vectors.parquet`** — 69.9 MB, tracked, and about to be
   invalidated by the `0009` re-embed. It is excluded from the *build* context by
   `.gcloudignore` and `.dockerignore`, so it never reaches an image; the cost is
   borne by anyone cloning the repository. Keeping it is defensible — it is what
   makes `make seed` reproducible without a Titan credential — but it should be a
   decision rather than an accident.
4. **`deploy/cloudrun.sh down`** once the walkthrough is recorded, unless the
   deployment is meant to stay up. A live environment costs money every day it
   runs; the transcripts under `ops/` are the record of what it did, and `up`
   rebuilds it.

Migration `0009` — the destructive Gemini embedding plane — remains deliberately
unapplied. The cluster is at `0009b`. `upgrade head` would drop all 18,035 Titan
vectors; upgrade **by name**.

---

## 9. Ground rules that cost something to learn

**A declared fallback is only worth having if swapping to it is one line.**
On 2026-08-31 `gemini-3.7-flash` answered 0 of 3 two-word prompts -- `503
UNAVAILABLE  This model is currently experiencing high demand`, and one `504
DEADLINE_EXCEEDED` after 119 seconds -- while `gemini-3.6-flash` answered 3 of
3. Tier R moved to 3.6 and 3.7 became the declared fallback. The live agent
graph went from **PASS 31 / CANNOT RUN 12** to **PASS 34 / CANNOT RUN 8**, and
`strong_resolution` -- the Tier R node, the one that had never once succeeded
against a live model -- now PASSES on both resolving artifacts.

Two things made that a configuration change rather than a redesign: both ids are
above the 3.5 floor this project holds itself to, and both were PROBED in the
same transcript, so nothing depended on which one was Tier R. The four tests
that broke had pinned the id rather than the behaviour, and now derive it.

- **`CANNOT RUN` is not `FAIL`**, and **absence is not emptiness**. Both were
  violated again this session, the second by a `counts.get(...) or -1` that
  reported FAIL for a legitimate zero, inside the tool written to demonstrate
  the opposite.
- **A green run on a sabotage assertion is a gate failure**, not a pass.
- **A hardcoded verdict beside a computed fact is worse than no verdict.** The
  first rehearsal printed `PASS` next to `git_sha equals HEAD: NO`.
- **A close-proof must be run, not predicted.**
- **Assert which, not how many.** A bare `404` check passes when the route does
  not exist at all.
- **A guard placed inside the thing it guards cannot fire.**
- **An unobserved mapping is a guess with a comment on it** — including in a
  readiness tool that asserted "there is no wake route" as a constant and kept
  saying so after the route existed.
- **`git check-ignore -v` output can be misread as a match.** `git add -An`
  reports what would be staged, which is the question.
- **A fixture and a type written by the same hand agree with each other and
  prove nothing.**
- **Verification against a database another process is writing is meaningless in
  both directions.**
