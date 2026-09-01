# Provenance — Consistency and Integrity Review

*Documents 50 and 51 — `submission/50_README_DRAFT.md` and `submission/51_VIDEO_SCRIPT.md` — were retired after this review was written. The references to them below are left exactly as they stood, for the record.*

Purpose: merge two adversarial reviews of the design pack into one ranked remediation list, record which contradictions were resolved in place and by what authority, and state plainly whether the pack can be built from.

Status: planning complete v1.1
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared
Audience: the implementation team and any coding agent about to start Phase 1; the documentation owners of `specs/`, `frontend/`, `ops/`, `quality/` and `submission/`.

---

## 0. Verdict

**No. The pack was not implementation-ready when reviewed. It is now buildable from the database, contracts, kernel and API planes, and it is still not buildable end-to-end.**

Fifteen blocking contradictions were found. All fifteen have been fixed in place, and the decisions behind them are recorded in `CANONICAL_DECISIONS.md` under a new *Hero commit canon* section, per the change-control rule in `README.md`. Nineteen major defects remain open, along with ten minor ones. They are listed below with the exact edit and the owning file for each.

What that means concretely:

| Plane | Can Phase 1 start? | What blocks it |
|---|---|---|
| Database (`specs/10_DATABASE_DDL.md`) | **Yes** | Nothing. Four missing columns were added; the seed no longer violates its own CHECK constraints. |
| Contracts (`specs/11_CONTRACTS.md`) | **Yes** | Nothing. |
| Kernel (`specs/12_KERNEL_ALGORITHMS.md`) | **Yes** | Nothing blocking. `M23` (a belief version whose `epistemic_status` no disposition rule produces) must be settled at `G-4` but does not block earlier work. |
| Retrieval (`specs/13_RETRIEVAL_SPEC.md`) | **Qualified yes** | `M1` — §16 is built on the wrong CockroachDB default for `vector_search_beam_size` and ships a `SET LOCAL` that silently changes it. Fix before Phase 6. |
| API (`specs/15_API_SPEC.md`) | **Yes** | Nothing blocking. Nine routes were added to §8.0; the `parity` block (`M15`) is still an undocumented response field on §8.31. |
| Prospective memory (`specs/16_TRIGGER_DSL.md`) | **No** | `M9` and `M10`. Four incompatible dates for the landlord deposit clock, and `due_at` is the field the hero trigger predicate compares against. The counterfactual panel in §13.5 also shows a trigger that has already fired, which the seed and the video both forbid. |
| Frontend (`frontend/30`, `31`, `32`) | **No** | `M5` (two required screens are undesigned), `M14`/`M15` (three documents specify three different render rules for the counterfactual), `M4` (the design brief names five counterparties that do not exist in the seed and attributes the damage claim to the employer). |
| Observability (`quality/21`) | **Qualified yes** | `M11` — `retrieval.*` spans are parented under the AgentCore span while `G13.5` queries for them in the control-plane log group. |
| Submission (`submission/50`, `51`) | **No** | `M13` (three documents describe three shot lists), `M17` (the badge certifying liveness is the only thing on screen the product did not render), `M12` (a pre-flight check a correct database fails). |

The honest one-line summary: **the pack's reasoning is sound and its self-disclosure is unusually good; its failures were concentrated almost entirely in fields, endpoints, roles and queues that newer documents assumed into existence and the owning specifications never gained.** Ten of the fifteen blockers were that single defect. `README.md` → *Change control* already prescribed the remedy; it simply had not been executed.

### Counts

| Severity | Found (deduplicated) | Fixed in place | Remaining |
|---|---|---|---|
| **BLOCKER** | 15 | **15** | 0 |
| **MAJOR** | 23 | 4 | 19 |
| **MINOR** | 12 | 2 | 10 |

The two reviews overlapped on nine findings. `B4`/`M16`, `B8`, `B13`/`m7`, `B2`/`M18`, `B9`, `B15`, `M4`, `M15` and `m11` each appeared in both and are merged here into one entry.

---

## 1. Blockers — all fixed in place

Each entry names the contradiction, the authority that decided it, and the edit that was applied.

### B1 — Three literal values for `evidence_items.embedding_version` · **FIXED**

`titan-v2-1024-cos-tmpl1` (`13_RETRIEVAL_SPEC`, `22_EVAL_DATASETS`), `titan-v2-1024-cosine-1` (`15_API_SPEC`, `11_CONTRACTS`), `v1` (everywhere else). `embedding_version` is an equality predicate in the canonical ANN query (`10_DATABASE_DDL.md` §5.5), so a mismatch returns **zero rows silently** — no error, no empty-result warning, just a retrieval pipeline that finds nothing and a demo that dies on camera.

**Authority:** `CANONICAL_DECISIONS.md`, *Embeddings* — "frozen embedding version `v1`". This outranks every specification.

**Applied:** all descriptor spellings replaced with `v1` in `specs/13_RETRIEVAL_SPEC.md` (5 occurrences including §16's `EMBEDDING_VERSION` constant and R10's prose), `specs/15_API_SPEC.md` (3), `specs/11_CONTRACTS.md` §22's `EMBEDDING_VERSION` constant, and `quality/22_EVAL_DATASETS.md` (2). `11_CONTRACTS.md` was carrying a third spelling that neither reviewer caught. `41_RUNBOOK.md` R7 and `32_JUDGE_MODE.md` R7 rewritten as resolved. `v1` now denotes the whole embedding contract — model, dimensionality, distance function **and** normalisation template — so a template change is a new version, not a silent reuse; that was the legitimate engineering concern behind the descriptor spelling and it is preserved.

### B2 — Three spellings of the hero case-reopen reason code · **FIXED**

`CONTRADICTORY_EVIDENCE` (`11_CONTRACTS`), `CONTRADICTORY_EVIDENCE_ADMITTED` (`23_PHASE_GATES` `G4.1`, `10_DATABASE_DDL` §18 test 6), `RC_CONTRADICTORY_EVIDENCE` (`00_PRODUCT` §2.3). This is not cosmetic: `CASE_REOPEN_REASON_CODES` is a **guard** on the `RESOLVED → REOPENED` transition (`11_CONTRACTS.md` §4.1), so `G4.1` as written asserted a value that would raise `IllegalTransition`.

**Authority:** `CANONICAL_DECISIONS.md`, *Closed domain vocabularies* — "`specs/11_CONTRACTS.md` owns enum membership." `CONTRADICTORY_EVIDENCE` wins.

**Applied:** corrected in `quality/23_PHASE_GATES.md` `G4.1`, `specs/10_DATABASE_DDL.md` §18 test 6, `00_PRODUCT.md` §2.3. `50_README_DRAFT.md` R5 rewritten as resolved. Recorded in `CANONICAL_DECISIONS.md` → *Hero commit canon*.

### B3 — The seed emits trigger types outside the frozen enum · **FIXED**

`10_DATABASE_DDL.md` §17.6 wrote `DEADLINE_ELAPSED` and `NO_RESPONSE_BY`. `ck_prospective_triggers_type` in the *same file* at §9 permits only `COMMITMENT_DEADLINE`, `RESPONSE_DEADLINE`, `CONFLICT_TIMEOUT`, `WARRANTY_WINDOW`. The seed insert would fail its own CHECK constraint. Both `51_VIDEO_SCRIPT.md` R5 and `41_RUNBOOK.md` R5 flagged it and neither fixed it at source.

**Authority:** `CANONICAL_DECISIONS.md`, *Trigger types*.

**Applied:** `10_DATABASE_DDL.md` §17.6 now seeds `COMMITMENT_DEADLINE` (deposit-overdue) and `RESPONSE_DEADLINE` (damage-followup). Both R5 entries rewritten as resolved.

### B4 — Four different values for the corpus size · **FIXED**

`18,412` (`15_API_SPEC` ×4, `30_UX_SPEC` ×2, `31_DESIGN_BRIEF` ×3, `21_OBSERVABILITY` ×1), `16,035` (`32_JUDGE_MODE` ×5), `18,032` (`EXECUTION_PLAN`, `PLANNING_READINESS`), `18,035` (`41_RUNBOOK`). This is the number the demo repeats most often and renders in Judge Mode.

**Authority:** `22_EVAL_DATASETS.md` §7.2 `DECOY_PLAN` is the arithmetic source: hero 16,000 + `iso-a` 1,000 + `iso-b` 1,000 = 18,000 decoys, plus 32 curated and 3 retraction fixtures. **Total 18,035. User-scoped 16,035.** `18,412` is derivable from nothing.

**Applied:** every `18,412` replaced with `16,035` in `15_API_SPEC.md`, `30_UX_SPEC.md`, `31_DESIGN_BRIEF_FOR_OPUS5.md`, `21_OBSERVABILITY_ANALYTICS.md`. `EXECUTION_PLAN.md` and `PLANNING_READINESS.md` corrected from 18,032 to 18,035 total / 16,035 user-scoped. `23_PHASE_GATES.md` `G6.3(a)` and `10_DATABASE_DDL.md` §18 test 12(a) corrected from "the full 18,000-row corpus". `30_UX_SPEC.md` §14.4 item 4 now carries `32_JUDGE_MODE.md` §9.3's disclosure sentence and binds the figure to a counted value rather than a constant. `41_RUNBOOK.md` R6 rewritten as resolved. One incidental `18412` in a `21_OBSERVABILITY_ANALYTICS.md` redaction unit test — an arbitrary integer asserting that numbers are not mangled by the account mask — was changed to `47219` so no reader mistakes it for a corpus figure.

### B5 — The counterfactual `parity` block can never be true · **FIXED**

`32_JUDGE_MODE.md` §7.2 requires `prompt_version` **and** `decode_params_sha256` to be equal or the panel renders "PARITY FAILED" and **suppresses both output columns**. `14_PROMPTS.md` §6.4 gave MEMORY OFF a different prompt (`pv-draft-nomemory-1.0.0`), a different effort (`low` against `high`) and a different output schema. The single highest-value demo asset was specified to refuse to render.

**Authority:** `CANONICAL_DECISIONS.md`, *Counterfactual* — "Memory OFF and ON use the same artifact, model, **prompt**, and graph. OFF receives empty retrieval and State Proof." This is binding and it decides the question without argument: the stripped variant violated a frozen decision.

**Applied:** `specs/14_PROMPTS.md` §6.4 rewritten. MEMORY OFF now uses `pv-draft-1.0.0` — same asset, same `thinking`, same `effort="high"`, same `max_tokens`, same `DraftAction` schema — and strips memory by rendering the TRUSTED STRUCTURED CONTEXT block **empty** (`state_proof: null`, `retrieval: {corpus_size_visible: 0, …}`) rather than by swapping the prompt. §1's auxiliary table and §11's asset layout updated; there is no counterfactual prompt directory. `32_JUDGE_MODE.md` §7.2's parity example corrected to `pv-draft-1.0.0` with the reason stated inline.

This also removes the objection the panel exists to defeat. Under the old design a sceptic could say "you gave the OFF side a worse prompt and less thinking budget." Under this one, the only difference is the contents of one block.

### B6 — Stored counterfactual outputs versus live-only execution · **FIXED**

`14_PROMPTS.md` §6.4: "Both outputs are stored on the `agent_runs` row for the demo so the panel is reproducible without re-invoking the model live." `32_JUDGE_MODE.md` §7.4: "never a stored string, never a cached response," with four enforcement layers including a frontend refusal. `15_API_SPEC.md` §17.9 forbids caching outright.

**Authority:** `CANONICAL_DECISIONS.md`, *Fixture mode* and *Judge Mode*. Live execution is the frozen posture.

**Applied:** the sentence is gone, replaced in the §6.4 rewrite with an explicit statement that neither output is stored for replay-as-if-live and that `32_JUDGE_MODE.md` §7.4 and `15_API_SPEC.md` §17.9 own the rule. The one genuine nuance — that the MEMORY ON side under the default `REPLAY_COMMITTED` strategy *is* a replay of a real committed run — is stated rather than elided.

### B7 — `21_OBSERVABILITY_ANALYTICS.md` §2.4 claimed eleven endpoints exist; six did not · **FIXED**

§2.4 ended "Every one of those endpoints exists in `15_API_SPEC.md` §8. This document adds no route." Six were absent from the §8.0 index. `30_UX_SPEC.md` §15.1 independently documented the `agent-views` gap and refused to call it.

**Authority:** `README.md` → *Change control*: a route absent from the owning specification does not exist and may not be implemented from.

**Applied:** rather than weaken the click path, the six routes were added to `15_API_SPEC.md` §8.0 as rows 8.35–8.40 (`/v1/judge-mode/agent-views`, `/v1/agent-runs/{id}`, `/v1/memory/proposals/{id}`, `/v1/kernel-decisions/{id}`, `/v1/events/{id}`, `/v1/triggers/{trigger_id}`) with scopes, rate-limit buckets and 404 codes. §2.4's closing paragraph now states honestly that the claim was false until that edit landed. `30_UX_SPEC.md` §15.1 updated: the system-status panel now calls `agent-views` and cross-checks it against `mcp_tool_calls[].view_name`, and a mismatch is rendered rather than hidden.

### B8 — Four columns the trace is built on did not exist in the DDL · **FIXED**

`idempotency_records.trace_id`, `agent_runs.tool_calls`, `agent_runs.model_calls`, `agent_runs.capability_status`. `15_API_SPEC.md` §4 states "If an example differs from the DDL, the DDL wins," so the trace contract was resting on nothing. `G11.4` additionally spelled the column `agent_runs.mcp_tool_calls`, a third spelling. Without these, `G11.4` cannot pass and MCP visibility is unprovable.

**Authority:** `10_DATABASE_DDL.md` owns tables; `15_API_SPEC.md` owns the trace contract and its field names. Both are satisfied by adding the columns rather than removing the contract.

**Applied:** all four added to `specs/10_DATABASE_DDL.md` §11.3 and §11.4 with `jsonb_typeof` CHECK constraints, documented element shapes, `idx_idempotency_trace`, and a new `ck_agent_runs_counterfactual_toolless` asserting that a counterfactual run was never bound the proposal tool. Both tables are created in migration `0008_events_infrastructure`, so the columns are inline and no extra Alembic revision is needed. The naming rule is now fixed and stated in the DDL comment: **column `agent_runs.tool_calls`, HTTP field `mcp_tool_calls[]`**. `G11.4` corrected; `40_INFRA_IAC.md` §17 and `41_RUNBOOK.md` §8.2 corrected. `32_JUDGE_MODE.md` R1 and `21_OBSERVABILITY_ANALYTICS.md` R1 rewritten as resolved.

### B9 — Two documents specified incompatible DAG shapes for the same panel · **FIXED**

`32_JUDGE_MODE.md` §4.1 requires a `CANONICAL_CHANGE` trace node type and says `spec_lint` requires it be added to `15_API_SPEC.md` §8.28 "in the same change" — it was not. `21_OBSERVABILITY_ANALYTICS.md` §6.2 made the *opposite* decision for the same panel: `state_transitions` is "(spine, not a node)".

**Authority:** `15_API_SPEC.md` §8.28 owns the trace node enum. It had no opinion, so one was required.

**Applied:** `CANONICAL_CHANGE` added to §8.28's now-seventeen-value closed enum, as a **child of `DB_TRANSACTION`**, with its `change_kind` closed set enumerated and `refs[]` specified. The reconciliation is stated in both files: `state_transitions` is the spine that orders the `CANONICAL_CHANGE` children *and* the source of the flat `memory_operations` array — one DAG shape, two renderings, not two competing shapes. `21_OBSERVABILITY_ANALYTICS.md` §6.2's node table gained the row. `32_JUDGE_MODE.md` R3 updated.

### B10 — The fixture-mode disclosure had no defined home · **FIXED**

The pack's only anti-fraud signal was read from three different endpoints by three different documents: `/healthz` (`41_RUNBOOK`, `23_PHASE_GATES` §24 `S3`, `51_VIDEO_SCRIPT` pre-flight, `50_README`), `/v1/version` (`32_JUDGE_MODE`, which calls it "the authoritative source"), and `/v1/me.feature_flags` (`30_UX_SPEC`, which drives the banner). `15_API_SPEC.md` §8.1 specifies `/v1/healthz` as returning exactly `{"status":"ok"}` with no auth and no database access; §8.2 returns seven fields, none of them `fixture_mode`. **`S3` — the gate item that invalidates the video — grepped a field no endpoint was specified to return.** The field names `build_sha` / `schema_revision` / `db_ok` also did not match §8.2's `git_sha` / `built_at`.

**Authority:** `15_API_SPEC.md` owns the HTTP surface.

**Applied:** `15_API_SPEC.md` §8.2 `GET /v1/version` is now the single authoritative disclosure channel and returns `fixture_mode`, `agent_mode`, `otlp_export`, `schema_revision` and `db_ok` alongside `git_sha`, each with a documented type and meaning, unauthenticated by design so a reviewer can `curl` it with nothing but the URL. `/v1/healthz` stays a bare liveness probe and explicitly does not carry `fixture_mode`. `/v1/me.feature_flags.fixture_mode` remains the UI-binding mirror. Every `/healthz` probe in `41_RUNBOOK.md` (§5.2, §7.6, §8.1, §8.3, §9, §11), `23_PHASE_GATES.md` (§13, `G13.2`, §24, the fixture-mode detector), `51_VIDEO_SCRIPT.md` §7.2 P6, `50_README_DRAFT.md`, `40_INFRA_IAC.md` §17 and `21_OBSERVABILITY_ANALYTICS.md` §3.2 was rewritten to `GET /v1/version`, and `build_sha` to `git_sha`. This also closes minor finding `m11` (`$PV_API/healthz` was an unversioned path that does not exist).

### B11 — The hero conflict had two mutually exclusive canonical values · **FIXED**

`00_PRODUCT.md` §2.3 shows `status = 'OPEN'`, `requires_human = true`; `12_KERNEL_ALGORITHMS.md` §1.6 step 12 resolves its ISP conflict as `RETAIN_INCUMBENT_AUTO` → `AUTO_RESOLVED`, `requires_human = false`. `32_JUDGE_MODE.md`, `41_RUNBOOK.md` §8.1 step 6 and `50_README_DRAFT.md` all assert `OPEN` / `requires_human`. `51_VIDEO_SCRIPT.md` R4 shows the authors knew and routed around it in narration.

**Authority:** `12_KERNEL_ALGORITHMS.md` §3.1/§3.3 owns disposition — it is the algorithm that will actually run. `00_PRODUCT.md` owns the hero narrative.

**Decision, and it reconciles both:** the two documents describe **two different conflicts**, and the reviewers' framing as a straight contradiction is not quite right. `12` §1.6 is a separate worked dataset with no `balance_owed` incumbent; its conflict is on the `SERVICE_STATUS` family, produced by entailment EN-1, and `AUTO_RESOLVED` is correct there. The **hero seed** has a `balance_owed = $0.0000 CONFIRMED` incumbent, so its conflict is on the `BALANCE` family — a **monetary** family with `monetary_exposure = |0 − 186| = 186.00 ≥ human_review_amount_threshold = 100.00`. Gate **H5** fires and short-circuits before the authority-margin test is ever reached. The disposition is `RETAIN_INCUMBENT_DISPUTED`: `conflicts.status = 'NEEDS_HUMAN'`, `requires_human = true`, severity `HIGH`, and a new belief version with the **value unchanged** and `epistemic_status` `CONFIRMED → DISPUTED`.

That is producible by `12` §3.3 exactly as written, matches `00_PRODUCT.md` §2.3 in every field, preserves `requires_human = true` for every downstream document, and preserves R3's "the amount did not change, our confidence in it did" caption. The **only** wrong token was `'OPEN'` — a legal column value that no disposition rule emits.

**Applied:** `'OPEN'` → `'NEEDS_HUMAN'` in `00_PRODUCT.md` §2.3 (with the H5 rationale inline), §2.3's transaction block, §2.4's comparison table, `32_JUDGE_MODE.md` §4.6 and §7.3, `41_RUNBOOK.md` §8.1 step 6, `50_README_DRAFT.md`, `31_DESIGN_BRIEF_FOR_OPUS5.md` §2.2. `G4.1`'s `conflict_type` corrected from `TEMPORAL_CONFLICT` to `VALUE_CONFLICT` in the same edit, closing `M18`. `51_VIDEO_SCRIPT.md` R4 rewritten: the narration may now name the status. Recorded in `CANONICAL_DECISIONS.md` → *Hero commit canon*.

### B12 — The Kernel spec shipped a view DDL that silently defeats retraction filtering · **FIXED**

`12_KERNEL_ALGORITHMS.md` §2.8 asserted "Evidence rows are immutable and have no retraction column" and defined `agent_evidence_retrieval_v1` with a `NOT EXISTS (… claims.claim_kind = 'CORRECTION' …)` predicate. The canonical DDL has a stored `retraction_status` with a four-value CHECK, a generated `is_retrieval_eligible`, and a view filtering `WHERE e.retraction_status = 'ACTIVE'`. The §2.8 predicate excludes **only** CORRECTION-retracted rows — `SUPERSEDED` and `QUARANTINED` evidence flows straight back into retrieval and grounding. It also projected `e.embedding` and `e.source_locator`, which the canonical view withholds from `pv_agent_reader`. It was presented as executable migration SQL, so a coding agent implementing it verbatim builds exactly the silent failure `00_PRODUCT.md` R4 exists to prevent.

**Authority:** `CANONICAL_DECISIONS.md`, *Evidence lifecycle* and *Retrieval eligibility*; `10_DATABASE_DDL.md` §5.4 and §14 own the column and the view.

**Applied:** the view DDL is deleted from `12` §2.8 and replaced with an explicit statement that `retraction_status` is a stored column, that `10_DATABASE_DDL.md` §14 owns all five agent-safe views, and that the withdrawn predicate was wrong in both of the ways above and must not be implemented. The `MatchContext.is_retrieval_ineligible(p)` kernel rule and the grant posture are kept as cross-references.

### B13 — The server-side anti-fabrication proof could not run: its credential did not exist · **FIXED**

`21_OBSERVABILITY_ANALYTICS.md` §7.3 and §9 run `tools/trace_verify.py` — the tool `50_README_DRAFT.md` hands a sceptical reviewer, and the only mechanism that falsifies "the DAG is a hand-authored fixture" — as `pv_ops_reader` via `provenance/db:ops_reader_url`. `40_INFRA_IAC.md` §11.5 stated flatly "It is **not** created in this deployment," and both `40` §8.7 and `41` §2.3 enumerate exactly four keys. Even the hypothetical role sketched there (five views + `outbox_events` + `kernel_decisions`) could not read the eight further tables §7.2's row census requires.

**Authority:** `CANONICAL_DECISIONS.md`, *SQL roles*, permits "optional `pv_ops_reader`". `40_INFRA_IAC.md` owns deployment. The tie-breaker is that a real consumer now exists, which is precisely the condition under which "optional" becomes "create it."

**Applied:** `40_INFRA_IAC.md` §11.5 now creates `pv_ops_reader` in migration `0008` with `SELECT` on the five `_v1` views and the eleven operational tables the census names (`source_artifacts`, `agent_runs`, `memory_proposals`, `kernel_decisions`, `state_transitions`, `outbox_events`, `processed_events`, `prospective_triggers`, `action_intents`, `action_executions`, `idempotency_records`), an explicit `REVOKE INSERT, UPDATE, DELETE`, and a `G12.8` verification pair demonstrating the refusal. It is stated to be an operator and CI credential only, not one of the §8.6 pools. `provenance/db` gains `ops_reader_url` as a fifth key in `40` §8.7 and `41` §2.3.

The point is worth stating plainly, because it is the reason the role earns its existence: a verifier that runs as `pv_app_reader_writer` proves less, because that role can write the rows it claims to verify. This also closes minor finding `m7` — `50_README_DRAFT.md` and `00_PRODUCT.md` §5 now say five roles.

### B14 — The Kernel's retry-exhaustion re-drive was unbuildable · **FIXED**

`12_KERNEL_ALGORITHMS.md` §7.4 called `sqs.send_message(QueueUrl=cfg.kernel_retry_queue_url, …)` after the retry cap. The control-plane task role has **no** `sqs:*` — `40_INFRA_IAC.md` §8.3 names that absence as a deliberate property — and no `provenance-kernel-retry-queue` exists among the four queues in §6.2. Under contention the enqueue fails with `AccessDenied`, `memory_proposals.status` stays `SUBMITTED` forever, and the UI shows "queued" for something that is not queued: a case whose evidence was admitted and whose commit will never be retried.

**Authority:** `40_INFRA_IAC.md` owns IAM and queues, and its "there is no `sqs:*`" is a stated security property rather than an oversight. Removing the call is therefore preferable to weakening the role.

**Applied:** `12` §7.4 rewritten. The Kernel now performs **no** side effect after the cap; it returns `RETRYABLE_CONCURRENCY` with reason `RETRY_EXHAUSTED_NOT_ENQUEUED` (renamed from `RETRY_EXHAUSTED_ENQUEUED` in the §11 reason-code table, code 29), and the HTTP layer maps it to `503` + `Retry-After: 1`. That contract already existed in `15_API_SPEC.md` §4.3 — "the client should retry the identical request with the identical `Idempotency-Key`" — so no new HTTP semantics were invented. The re-drive owner is named for each caller: the ingestion graph's submit step for artifact proposals, and SQS redelivery of the wake message for `TRIGGER_EVALUATION` proposals, using the queues that do exist.

### B15 — The demo's only trigger entry point was not in the HTTP authority · **FIXED**

`POST /v1/judge/triggers/{trigger_id}/wake` carries a human token and an `Idempotency-Key` and mutates canonical state (`ARMED → FIRED`, revision increment, outbox event), yet appeared in no route table, no route-class list, no error catalogue and no rate-limit bucket — defined only in `16_TRIGGER_DSL.md` §13.2. The same applied to `POST /v1/judge-mode/probes` and `GET /v1/judge-mode/probes/{id}`, which `32_JUDGE_MODE.md` §8.1 honestly flagged as needing to be added "before Phase 12 begins." `51_VIDEO_SCRIPT.md` beat 6 and `41_RUNBOOK.md` §8.1 step 10 both depend on the wake endpoint.

**Authority:** `15_API_SPEC.md` owns the HTTP surface; `README.md` → *Change control* forbids implementing from a document requiring an unapplied change.

**Applied:** rows 8.32 (`judge/triggers/{id}/wake`, scope `judge.trigger_wake`, bucket `judge_wake`, errors `403 JUDGE_MODE_DISABLED` / `404 TRIGGER_NOT_FOUND` / `409 TRIGGER_NOT_ARMED` / `409 IDEMPOTENCY_CONFLICT` / `503 RETRYABLE_CONCURRENCY`), 8.33 and 8.34 (probes, scope `judge.probe`, errors `PROBE_NOT_FOUND` / `PROBE_TARGET_BUSY`) added to `15_API_SPEC.md` §8.0 with a scope-and-bucket table. The behavioural contract stays owned by `16_TRIGGER_DSL.md` §13.2 and is cross-referenced rather than duplicated; §8.0 owns existence, auth, scope, bucket and errors. The false-predicate wake is stated as a `200` typed no-op rather than an error, matching `CANONICAL_DECISIONS.md`, *Trigger demonstration*. `spec_lint`'s obligation — this table against the generated OpenAPI, failing on any asymmetry — is stated in the index itself so the next document cannot repeat the mistake. `32_JUDGE_MODE.md` R2 rewritten as resolved.

---

## 2. Major — open, with the exact edit and its owner

Ranked by how much they cost if left. `M6`, `M7`, `M16` and `M18` were fixed opportunistically alongside blockers and are listed at the end for the record.

### M1 — `vector_search_beam_size` default is 32, not 8 · owner `specs/13_RETRIEVAL_SPEC.md`

§16.1 states "Default **8**" and §16.2 ships `SET LOCAL vector_search_beam_size = 8` while claiming to ship the default. The CockroachDB default is **32**. `41_RUNBOOK.md`, `40_INFRA_IAC.md`, `10_DATABASE_DDL.md` and `21_OBSERVABILITY_ANALYTICS.md` all correctly say 32, and `21`'s R8 cites `13` §16.2 as authority for "frozen at the CockroachDB default of 32" — mutually contradictory. `32_JUDGE_MODE.md` renders `beam_size 8` in the third-party tool strip Judge Mode shows.

**Edit:** correct `13_RETRIEVAL_SPEC.md` §16.1 to 32, and either change the `SET LOCAL` to `32` or delete it and ship the true default (deleting is better — a `SET` that restates the default is noise that will drift again). Then update `32_JUDGE_MODE.md`'s four rendered examples. Recall at beam 32 is strictly better than at 8, so no eval number regresses.

### M2 — `00_PRODUCT.md` lists three agent-safe views; there are five · owner `00_PRODUCT.md`

§3's glossary entry and §5's rubric row name only `agent_case_context_v1`, `agent_active_beliefs_v1`, `agent_evidence_retrieval_v1`. `CANONICAL_DECISIONS.md` freezes five, and every downstream document uses five.

**Edit:** add `agent_belief_lineage_v1` and `agent_open_obligations_v1` to both places in `00_PRODUCT.md`. `CANONICAL_DECISIONS.md` outranks `00_PRODUCT.md` on names, so this is a correction, not a negotiation.

### M3 — `20_TDD_STRATEGY.md` sizes the L4 lane against 62 scenarios · owner `quality/20_TDD_STRATEGY.md`

Two occurrences (§8 and the fixture layout comment) say 62. `CANONICAL_DECISIONS.md`, `22_EVAL_DATASETS.md`, `23_PHASE_GATES.md` `G14.1` and `50_README_DRAFT.md` all say 51.

**Edit:** change both occurrences to **51** in `20_TDD_STRATEGY.md`. Nothing else in that document depends on the count.

### M4 — Three incompatible sets of demo display names, and one is actively wrong · owner `frontend/31_DESIGN_BRIEF_FOR_OPUS5.md` (plus a sweep)

Seed, video and README: *Alex Rivera, Northline Fiber, Harborview Property Management, Beltline Movers, Kestrel Analytics, Cascade Power*. UX / Judge Mode / API examples: *Dana Whitfield, Northline Broadband, Harrow Street Properties*. The design brief adds *Kestrel Moving Co.* and *Halloran Group*, and §1.6 falsely asserts its set is "canonical in our specifications."

The dangerous one is **Kestrel**: the brief makes it the mover while the seed's *Kestrel Analytics* is the **employer**. A returned design will attribute the USD 420 damage reimbursement to the employer, and a designer has no way to catch it.

**Edit:** adopt the seed's set as canonical and record it in `CANONICAL_DECISIONS.md` under *Demo and disclosure*. Replace all five display names in `31`'s pasteable prompt, delete the "canonical in our specifications" claim in §1.6 and R1, and sweep the examples in `30_UX_SPEC.md`, `32_JUDGE_MODE.md` and `15_API_SPEC.md` §8 in the same change. `10_DATABASE_DDL.md` §17.2 is the authority.

### M5 — Two required screens will ship undesigned · owner `frontend/31_DESIGN_BRIEF_FOR_OPUS5.md`

The brief commissions seven screens: Relationship Dashboard, Case Timeline, State Proof, Contradiction Panel, Action Approval Inbox, Memory Trace Inspector, Judge Mode. `23_PHASE_GATES.md` `G-12` and `30_UX_SPEC.md`'s S1–S7 are a *different* seven: **login** and **upload/forward** are required and absent, while Contradiction Panel and Memory Trace Inspector are sub-regions of S3 and S6 rather than routes.

**Edit:** add Login (S1) and Upload/Forward (S7) to §4 of the pasteable prompt, and demote Contradiction Panel and Memory Trace Inspector to named components inside Case Detail and Judge Mode. Upload is the screen the video's beat 2 opens on; shipping it undesigned is the single most expensive omission in this list.

### M8 — Two metric families for one signal · owner `specs/16_TRIGGER_DSL.md`

`16` §14.2 defines Prometheus-style `provenance_trigger_armed_total{trigger_type}`, `provenance_trigger_wake_total{wake_source,outcome,reason_code}`. `21_OBSERVABILITY_ANALYTICS.md` §4.1 mandates dotted `provenance.<area>.<leaf>` and defines a different set (`provenance.trigger.armed`, `.evaluated{result,trigger_type}`, `.wake_lateness_ms`). `21` explicitly reconciles `13` and `15`'s metric names and never mentions `16`.

**Edit:** `21` owns telemetry naming (it is the document whose whole subject it is). Rewrite `16` §14.2 to the dotted names. If any `16` metric has no dotted equivalent, add it to `21` §4.1 in the same change rather than leaving an orphan.

### M9 — Four incompatible dates for the landlord deposit clock · owner `00_PRODUCT.md`

`00_PRODUCT.md` §2.3: inspection 16 May, `due_at = 2026-06-15`. `10_DATABASE_DDL.md` §17.5: inspection completion 2 June, promise 3 June. `16_TRIGGER_DSL.md` §12.1: inspection `2026-05-28`, deadline `2026-06-27`. `30_UX_SPEC.md` and `31`: "promised on 16 May, elapsed on 15 June."

`due_at` is the field the hero trigger predicate compares against and the date spoken in `51_VIDEO_SCRIPT.md` beat 6. The seed, the predicate and the UI copy cannot all be right, and this is the second reveal — the moment the demo is built to land.

**Edit:** fix **one** date in `00_PRODUCT.md` §2.3 (product authority) and propagate to `10_DATABASE_DDL.md` §17.5/§17.6, `16_TRIGGER_DSL.md` §12, and the copy tables in `30_UX_SPEC.md` and `31`. Recommended: keep `00_PRODUCT.md`'s 16 May inspection / `due_at 2026-06-15`, because it is the value the narrative already uses and it yields a clean "95 days past due" against the `2026-09-18` demo clock. Then recompute every day count from it.

### M10 — The trigger DSL shows the landlord trigger already fired, and hard-codes UUIDs · owner `specs/16_TRIGGER_DSL.md`

§13.5's counterfactual panel states the trigger fired at `2026-06-27T00:01:03Z`. The seed arms it and never fires it; `51_VIDEO_SCRIPT.md` P3 asserts `COMMITMENT_DEADLINE, ARMED` before recording, fires it on camera, and §8 says a pre-fired trigger means "stop, reseed, restart the session." §13.5 also hard-codes trigger id `a7e3d901…` and commitment id `9c1f4b2e…`, which the seed mints via `uuid5`/`sid()` — and `CANONICAL_DECISIONS.md`, *Judge Mode*, forbids hard-coded object identifiers. The "51 days past" figure matches neither candidate `due_at`.

**Edit:** rewrite `16` §13.5 so the ON column shows an `ARMED` trigger whose predicate evaluates true at demo time; replace the literal UUIDs with `sid('trigger','deposit-overdue')` and `sid('commitment','deposit')`; recompute the day count from whichever `due_at` `M9` settles on.

### M11 — `retrieval.*` spans are parented where the code does not run · owner `quality/21_OBSERVABILITY_ANALYTICS.md`

§3.3 nests `retrieval.identity/vector/expand/rerank` under `agent.interpreter.run`, whose AgentCore-side server span emits into `/provenance/agents`. But `13_RETRIEVAL_SPEC.md` §2 places the trusted retrieval pipeline in the **control plane** (`pv_app_reader_writer`, App Runner), and `G13.5` asserts `retrieval.vector` appears in a Log Insights query against `/provenance/control-plane`. Under `21`'s own span tree that query returns nothing.

**Edit:** re-parent `retrieval.*` as siblings of `agent.interpreter.run` under `http.server.request` in `21` §3.3, and state explicitly which log group each span family lands in. `13_RETRIEVAL_SPEC.md` §2 is the authority on where the code runs.

### M12 — A pre-flight check a correct database fails · owner `submission/51_VIDEO_SCRIPT.md`

`P5` runs `SELECT … FROM commitments ORDER BY committed_amount` and states three expected rows. The seed has **four** commitments (`deposit`, `damage`, `relocation`, `termination`); the non-monetary `termination` row appears. The query is also unscoped by `tenant_id` and `user_id`.

**Edit:** add `WHERE tenant_id = … AND user_id = … AND currency IS NOT NULL` (or list the fourth row) so a correct database passes its own pre-flight. A pre-flight that fails on correctness trains the operator to ignore it, which is worse than not having it.

### M13 — Three documents describe three shot lists · owner `00_PRODUCT.md` §5

`32_JUDGE_MODE.md` §7 still calls the counterfactual "the single most persuasive twenty-five seconds in the video (`00_PRODUCT.md` §5, segment D)." `51_VIDEO_SCRIPT.md` §2 deliberately reduces it to a **5-second** closing frame in beat 7 and reassigns the 25 seconds to the prospective-memory reveal. `00_PRODUCT.md` §5's shot list and rubric row still describe the old cut.

**Edit:** land `51_VIDEO_SCRIPT.md` §2's stated edit — update `00_PRODUCT.md` §5's shot-list table and rubric column, and remove the "twenty-five seconds" claim from `32_JUDGE_MODE.md` §7. `51` is the owner of the cut; `00_PRODUCT.md` §5 must follow it.

### M14 — The counterfactual's mandatory on-screen sentence is false in the default mode · owner `frontend/30_UX_SPEC.md`

§14.4 item 1 fixes the copy as "Both columns ran just now." The default and recommended `memory_on_strategy` is `REPLAY_COMMITTED`, which the same pack defines as showing "the Kernel decision and draft that this artifact **actually produced when it arrived**." The ON column is a replay of a run that happened minutes to months earlier. This is the exact screen `00_PRODUCT.md` R2 calls "the easiest to accuse of being rigged," and the reassurance printed on it is the one demonstrably untrue statement in the pack.

**Edit:** make the header strategy-dependent, rendered from `memory_on.strategy` rather than fixed copy.
`REPLAY_COMMITTED` → *"The left column ran just now. The right column is the committed result this artifact actually produced on {occurred_at}, replayed from stored rows. Same document, same model, same prompt."*
`RERUN_SANDBOXED` → the current sentence.
Update `51_VIDEO_SCRIPT.md` beat 7's narration to match. The replay is a *stronger* claim honestly stated than the rerun is dishonestly stated, so nothing is lost.

### M15 — The two frontend documents disagree on whether an unfair comparison may be displayed · owner `specs/15_API_SPEC.md` §8.31, then both frontend files

`32_JUDGE_MODE.md` §7.2 makes a server-computed `parity` block mandatory and suppresses both output columns on `all_equal: false`. `30_UX_SPEC.md` §14.3 never mentions `parity` (zero occurrences), renders both columns unconditionally, and checks only `model_id` client-side. A build following `30_UX_SPEC.md` ships the screen `32` was written to prevent. Separately, both documents and `00_PRODUCT.md` R2 assert "the identical graph" while `32` §7.1 gives the OFF run `graph_name = 'counterfactual'` against the ON run's `'ingestion'` — and `parity` does not compare `graph_name`, so the one field that genuinely differs is the one field not checked.

**Edit, one change across three files:** add the `parity` block to `15_API_SPEC.md` §8.31's response as a documented field; add `graph_name` to it with an explicit `expected_difference: true` annotation; rewrite `30_UX_SPEC.md` §14.3/§14.4 to gate column display on `all_equal`. Change the "identical graph" wording in `30_UX_SPEC.md` §14.4 and `00_PRODUCT.md` R2 to "identical graph *definition and version*; the OFF run is recorded under `graph_name = 'counterfactual'` so it is excluded from case timelines." `B5` made the block *satisfiable*; this makes it *specified*.

### M17 — The badge that certifies liveness is the only thing on screen the product did not render · owner `submission/51_VIDEO_SCRIPT.md`

§9.1 says the values "are read … in the pre-flight and **typed into the overlay once**." §9.4 says "If a number is on screen, the application rendered it. The editor **may not** set type over the product." The `LIVE · fixture_mode: false · build <sha>` overlay is editor-typeset, persists for all 170 seconds, and is precisely the claim an editor could type while the system ran in fixture mode. The §12 checklist item "`fixture_mode: false` appears on screen at least once at full legibility" is satisfiable by the typeset overlay alone.

**Edit:** make the badge a product surface. Render `LIVE · fixture_mode · git_sha` from `GET /v1/version` in the app's own persistent status chip, add it to `30_UX_SPEC.md` §4 as a global element, capture it as B-roll, and change §9.1 to "the badge is rendered by the application; the editor may not typeset it." Change the §12 checklist item to require a full-frame hold on the app-rendered chip. `B10` gave `/v1/version` the fields this needs.

### M19 — The outbox dispatcher is specified to live in two processes with two principals · owner `specs/15_API_SPEC.md`

`15` §13.4 puts the publish/settle state machine in `workers/outbox_dispatch/handler.py` and shows it calling `PutEvents`. `40_INFRA_IAC.md` §7.4 puts the entire state machine in the control plane — "Putting them in the Lambda would require giving a worker a SQL credential" — and grants the Lambda **no** `events:PutEvents`, calling that absence "the check that the state machine has not leaked out of its owner." As written the Lambda has neither a database credential nor `PutEvents` and cannot execute the code `15` assigns it.

**Edit:** amend `15` §13.3–§13.4 to place the claim/publish/settle code at `services/control_plane/app/events/outbox_dispatch.py` behind `POST /internal/v1/events/outbox/sweep`, and retitle the code block. Keep the Lambda as the clock, exactly as `40` §7.4 already shows. `40` is the authority here because its version is the one with a stated security rationale.

### M20 — Invariant 1 has a comment, not a boundary · owner `ops/40_INFRA_IAC.md` §11.5 and `specs/10_DATABASE_DDL.md`

`GRANT INSERT, UPDATE ON TABLE … evidence_items TO pv_kernel_writer` is unrestricted, so `pv_kernel_writer` may rewrite `normalized_text`, `exact_text`, `embedding`, `valid_from`/`valid_to` and `observed_at`. The DDL comment says those are never overwritten; nothing enforces it. §11.5's own preamble says "These are the actual permission boundary. Application-layer checks are defence in depth on top of them, never a substitute." Invariants 2, 3 and 4 all have mechanisms — CHECK constraints, `SERIALIZABLE`, revalidation. **Invariant 1, the one the product is named for, does not.**

**Edit:** keep the grant (the `retraction_status` flip genuinely needs `UPDATE`) and add the cheapest real guard: either a `BEFORE UPDATE` trigger rejecting any change to the immutable columns, or a repeatable verification query `V12` in `db/verify.sql` comparing `normalized_text_sha256` against `sha256(normalized_text)` for every row, wired into `G2` and the post-seed check. Add one honest sentence to `40` §11.5 and to `50_README_DRAFT.md`'s *Known limitations* saying the append-only guarantee for evidence *content* is enforced by constraint and verification, not by grant. The trigger is stronger; the verification query is cheaper and can land today.

### M21 — The dress rehearsal gates on substrings of live model output · owner `ops/41_RUNBOOK.md`

§8.1 step 8 asserts `memory_off` contains `"$186"` and **not** `"15 May"`/`"terminat"`/`"reopen"`, and `memory_on` contains `"15 May"` AND `"reopened"`. These are two non-deterministic Opus 5 completions. A correct system fails this check whenever the model phrases the same conclusion differently, and the only reliable ways to make it pass are to constrain the prompt to canned phrasing or to switch on fixture mode — the precise pressure `32_JUDGE_MODE.md` §7.4 and `30_UX_SPEC.md` §14.6 exist to remove.

**Edit:** replace the substring assertions with structural ones the contract guarantees — `memory_off.output.case_linked IS NULL`, `.conflicts_detected == 0`, `.recommended_action == "NONE"`, `corpus_size_visible == 0`; `memory_on.output.conflicts_detected >= 1`, `.grounding[]` non-empty, `.kernel_decision_id` non-null. Keep `safety.case_revision_changed_by_counterfactual == false` and the before/after `cases.revision` equality.

### M22 — The isolation probe permanently falsifies a Panel D indicator · owner `quality/21_OBSERVABILITY_ANALYTICS.md` and `frontend/32_JUDGE_MODE.md`

`32` §5.3 renders `provenance.auth.tenant_mismatch` with the stated meaning "In a correct system this is `0` and a non-zero value is either a bug or an attack. It is displayed precisely because it should never move." §9.1's isolation probe deliberately drives it `0 → 1`. After anyone runs the probe, Panel D shows a non-zero value with that tooltip for the whole CloudWatch period, and nothing distinguishes probe-induced from genuine.

**Edit:** emit the probe's attempt with a distinguishing dimension — `provenance.auth.tenant_mismatch{source="JUDGE_PROBE"}` versus `{source="RUNTIME"}`. Add the dimension to `21` §4 and to the §8.3 alarm's metric filter so the alarm watches only `RUNTIME`. Have Panel D render the `RUNTIME` series, with the probe count shown separately and labelled.

### M23 — A belief version no disposition rule can produce, and a UI panel built on it · owner `specs/12_KERNEL_ALGORITHMS.md`, settle at `G-4`

`12` §1.6 step 19 writes `bv_isp_balance_v1` as `DISPUTED` with confidence `0.53`, but in that walkthrough `balance_owed` has no incumbent, so §3.1's disposition is `NO_INCUMBENT` → no `conflicts` row, status `CONFIRMED` (authority 0.90 ≥ 0.90) — never `DISPUTED`. §3.1 also gives no confidence formula for the `NO_INCUMBENT` case. Meanwhile `32_JUDGE_MODE.md` §3.1 hard-specifies Panel B's headline row as "the hero's `balance_owed` v1 → v2, `$0.0000 CONFIRMED` → `$0.0000 DISPUTED`" with the caption "The amount did not change. Our confidence in it did."

`B11` resolves the *hero* case: the seed **does** have a `balance_owed` incumbent, so the v1 → v2 `DISPUTED` pair and Panel B's caption are correct there. What remains open is `12` §1.6's own worked walkthrough, which describes a different dataset and is internally inconsistent with §3.1.

**Edit, small and local:** correct `12` §1.6 step 19's `bv_isp_balance_v1` to `CONFIRMED`, and add a confidence formula for `NO_INCUMBENT` to §3.1's table (recommended: `belief_confidence = challenger.authority × extraction_confidence`, quantized to four places, which for the walkthrough gives `0.90 × 0.996 = 0.8964`). Additionally, key `32` §3.1's caption off *any* `epistemic_status`-changed-value-unchanged transition rather than naming `balance_owed`, so the panel stays correct if the seed ever changes. Then `51_VIDEO_SCRIPT.md` beat 4's alternate narration becomes the primary.

### Fixed opportunistically alongside blockers

- **M6 — evidence count 3 versus 6.** `00_PRODUCT.md` §2.3 is product authority: **3** (`DATE_ASSERTION`, `AMOUNT_ASSERTION`, `IDENTIFIER_ASSERTION`). Corrected in `31_DESIGN_BRIEF_FOR_OPUS5.md` §2.2, `30_UX_SPEC.md` §17's live-region copy (now interpolated from the API rather than hard-coded), and `15_API_SPEC.md` §8.28's `EMBEDDING` node and §12's `evidence.admitted.v1` example.
- **M7 — prompt versions outside the registry.** `14_PROMPTS.md` owns them (`CANONICAL_DECISIONS.md`, *Prompt authority*). `extract-v4` → `pv-extract-1.1.0` and `resolve-v3` → `pv-resolve-1.1.0` throughout `15_API_SPEC.md`, `32_JUDGE_MODE.md` and `21_OBSERVABILITY_ANALYTICS.md` §3.4. Prompt assets are hash-verified at process start, so a value outside the registry could never have verified.
- **M16 — `30_UX_SPEC.md`'s 18,412.** Fixed under `B4`.
- **M18 — `G4.1` asserts `TEMPORAL_CONFLICT`.** Fixed under `B11` to `VALUE_CONFLICT`, which is what `12` §1.6 step 20 and §9.5's vocabulary reconciliation produce for a same-predicate value disagreement, and what beat 3 holds on screen for six seconds.

---

## 3. Minor — open

| # | File | Defect | Fix |
|---|---|---|---|
| m1 | `00_PRODUCT.md` §2.3, `21_OBSERVABILITY_ANALYTICS.md` redaction examples, `31` §2.2 | Three account references: `88-114-2039`, `NF-4471-8802`, "ending 4417". This is the value the exact-identifier gate matches on and it appears on camera. | `NF-4471-8802` is the seeded `external_account_ref` (`10_DATABASE_DDL.md` §17.2). Update the other three. |
| m2 | `21_OBSERVABILITY_ANALYTICS.md` §6.3 | "Nine sources. Six are joined on `trace_id` directly; three are reachable only through a join." The table lists **eleven** — 6 direct, 5 join-only. | "Eleven sources. Six … five …". |
| m3 | `21_OBSERVABILITY_ANALYTICS.md` §3.2 | `HMAC-SHA256(key, id)[:8]` labelled "string, 16 hex". `[:8]` on a hexdigest yields 8 characters; `subject_hash()` uses `.hexdigest()[:16]` and the worked record shows 16. | Write the §3.2 formula as `HMAC-SHA256(PV_LOG_HASH_KEY, tenant_id).hexdigest()[:16]`. |
| m4 | `51_VIDEO_SCRIPT.md` §11 | The 30-second cut-down states "12 + 13 + 16 + 9 = 50 words"; the four lines contain 9, 13, 15 and 8 = **45**, so the stated 1.67 w/s is wrong. | Restate as 45 words / 30 s = 1.50 w/s, or lengthen the lines. |
| m5 | `32_JUDGE_MODE.md` §7.3 | "evidence recalled 118 days" against `31`'s "126 days ago". `2026-09-18` minus `2026-05-15` is **126**. | Use 126, or better, derive it in the API and stop hard-coding it in either. |
| m6 | `README.md` | The read order lists documents 1–17 and stops at `implementation/`. Eight newly written documents have no entry: `quality/21`, `frontend/30`, `31`, `32`, `ops/40`, `41`, `submission/50`, `51`. "Within a technical concern, the numbered specification that owns it is authoritative" — with no index entry, `21`'s claim to own telemetry and `32`'s to own Judge Mode are unbacked. | Add the eight to the read order with their owned concerns, and state the frontend/ops/submission authority tier explicitly. **This one is cheap and it underwrites several decisions above.** |
| m8 | `21_OBSERVABILITY_ANALYTICS.md` §4.2 | `provenance.api.requests` cardinality stated as "the route count (31)". That counted public `/v1` routes only, and §8.0 now has 40; the `/internal/v1` routes also emit `http.server.request` spans with `provenance.route_class = INTERNAL`. | State the number as public + internal, or drop the parenthetical count. It will drift again either way; dropping it is better. |
| m9 | `50_README_DRAFT.md` | "One container, four separate database connection pools — one per SQL role." `40_INFRA_IAC.md` §8.6 says "Three pools"; `pv_migrator` is "DDL only, never used by runtime" and `pv_agent_reader` reaches the control plane only via MCP. | "three separate database connection pools — app, kernel, and the agent-read pool — created at startup and never mixed; `pv_migrator` is used only by migrations." Note that `pv_ops_reader` (added under `B13`) is also **not** a pool. |
| m10 | `32_JUDGE_MODE.md` §6.3 | Reads recorded refusal output from `ops/gate-reports/G-11.txt`; every other document places signed gate output at `ops/gates/PHASE_00.md … PHASE_15.md`. | Change to `ops/gates/PHASE_11.md` and specify the fenced-block label the panel greps for. |
| m12 | `21_OBSERVABILITY_ANALYTICS.md` §7.5 | "Step 3 is deliberately destructive against the demo tenant and is reversible with `make seed`." It is not: `make seed` recreates seeded rows, not a live run's `tool_calls`. | "Step 3 destroys the MCP record of *this* trace only. It is not undone by `make seed`; re-run step 1 to produce a fresh trace. The seeded corpus is unaffected." |

Minor findings `m7` (role count) and `m11` (`$PV_API/healthz` unversioned) were closed under `B13` and `B10` respectively.

---

## 4. What both reviews found clean, and why it matters

Recording this deliberately, because a remediation list read alone gives a false impression of the pack.

- **26 canonical table names.** Every SQL identifier in the eight newer documents maps to a table created in `specs/10_DATABASE_DDL.md`. No invented tables.
- **Five agent-safe `_v1` view names.** Spelled identically in all eighteen files that mention them. The only defect was the *count* in `00_PRODUCT.md` (`M2`).
- **Enums.** Case status (10), attention levels (4), advocate attention classes (5), trigger types (4), trigger results (5), retraction status (4), action intent status (10), action types (5), conflict types (7) and severities (4), claim kinds (8), epistemic statuses (6), support relations (3), kernel decisions (9), event types (25) — every list in every target document matches `specs/11_CONTRACTS.md` and `specs/10_DATABASE_DDL.md` exactly. `31_DESIGN_BRIEF_FOR_OPUS5.md` §1.4's vocabulary block is enum-perfect.
- **Model IDs.** `anthropic.claude-haiku-4-5`, `anthropic.claude-opus-5`, `amazon.titan-embed-text-v2:0` are byte-identical everywhere, `anthropic.` prefix included. Tier E/R routing and the no-downgrade rule are stated consistently.
- **1024 dimensions, cosine.** Consistent everywhere.
- **Sixteen gates `G-0` … `G-15`.** Every gate id cited by the newer documents resolves to a real assertion. The only additions are `G12.8` and `G13.10`, disclosed in `21`'s R12.
- **51 scenarios, split 9-8-10-9-7-8.** Identical in `CANONICAL_DECISIONS.md`, `22_EVAL_DATASETS.md`, `50_README_DRAFT.md` and `23_PHASE_GATES.md`, and internally consistent with `22`'s 18-conflict / 33-no-conflict and 43-of-51 extraction subsets. Only `20_TDD_STRATEGY.md` dissents (`M3`).
- **External vendor facts.** `40_INFRA_IAC.md` §10 correctly states `feature.vector_index.enabled`, the three index-syntax variants, the 32/16/128 partition and beam defaults, the `IMPORT INTO` restriction on vector-indexed tables, and the load-then-index seed ordering that follows from it. The Managed-versus-self-hosted MCP distinction is drawn correctly. Release constraints — Apache-2.0, a demo video under 180 seconds, at least two CockroachDB tools, at least one AWS service, and a written tool-usage disclosure — are all present and correctly sourced.
- **Money.** USD 1,800 + USD 220 = USD 2,020 holds in all eight documents; 420 − 200 = 220 with status `PARTIAL` matches `ck_commitments_outstanding_identity`.
- **No fabricated claims.** Neither reviewer found a claim that code, cloud resources, tests, or benchmarks exist, nor a fabricated metric presented as a measurement. `21`'s R11, `32`'s §6.4 and §7.4, `50`'s *Build status* and *Known limitations*, and `51`'s R11 all volunteer weaknesses rather than papering over them. No place was found where an agent is handed a write credential, or where SES is reachable from uncommitted state.

That last point is the reason the remediation above was worth doing rather than starting over. The pack's *architecture* survived adversarial review intact. What failed was bookkeeping between documents written at different times — and bookkeeping is fixable in an afternoon, which is what this was.

---

## 5. Recommended order of remaining work

1. **Before Phase 1 (one sitting):** `M2`, `M3`, `m6`, `m9`, `m10`, `m12`. All are single-line corrections and `m6` underwrites the authority claims several other fixes rely on.
2. **Before Phase 2 (the seed):** `M9` — the landlord date. Everything downstream of the seed encodes it, and re-seeding to change a date is cheap now and expensive after `G-6` embeds 18,035 rows against it.
3. **Before Phase 6 (retrieval):** `M1` — the beam-size default.
4. **Before Phase 10 (prospective memory):** `M10`.
5. **Before Phase 12 (frontend and Judge Mode):** `M4`, `M5`, `M14`, `M15`, `M22`. `M5` is the long-pole item: two screens have to be designed and neither is commissioned.
6. **Before Phase 13:** `M11`, `M19`, `M20`.
7. **Before Phase 15 (recording and submission):** `M12`, `M13`, `M17`, `M21`, `m1`, `m4`, `m5`.
8. **At `G-4`, as a design decision rather than a correction:** `M23`.

`M20` deserves one more sentence. It is the only item on this list where the pack's *stated posture* and its *actual mechanism* diverge on an invariant, and the invariant in question is the one the product is named for. It is not urgent, but it should not be quietly dropped, and the honest disclosure it needs in `50_README_DRAFT.md` costs a sentence.

---

## 6. Risks and open questions

**R1 — Fifteen blockers were fixed by editing documentation, and documentation cannot be executed.** Every fix above is a claim about what the code will do, verified only by reading. `B8`'s columns have never been created; `B13`'s role has never been granted; `B15`'s routes have never returned a response. The first execution of `alembic upgrade head` and the first `spec_lint` run against a generated OpenAPI document are the real tests, and they will find things this review could not. *Posture:* treat the phase gates as the verification and this document as the input to them, never as evidence of correctness.

**R2 — The `B11` resolution rests on a reading of two documents as describing different datasets, and that reading is mine.** `12_KERNEL_ALGORITHMS.md` §1.6 and `00_PRODUCT.md` §2.3 use different case ids, different revisions (7 → 8 against 12 → 13) and different dates (`2026-09-05` against `2026-09-18`), which is strong evidence they are separate worked examples rather than one contradictory one. If the intent was in fact that §1.6 *is* the hero commit, then the hero conflict is `AUTO_RESOLVED` and six documents are now wrong in a new way. **This must be confirmed by whoever wrote §1.6 before `G-4`.** The confirming test is cheap: run the hero seed, submit the hero proposal, and read `conflicts.status`. If it is `AUTO_RESOLVED`, revert `B11` and take the integrity reviewer's original recommendation instead — which is also a good demo, just a different one.

**R3 — Nineteen major defects remain, and three of them are on the video path.** `M13`, `M17` and `M21` all touch the recorded walkthrough, which is the artifact most people will actually watch. They are individually small and collectively the difference between a recording that survives scrutiny and one that does not. The schedule pressure at Phase 15 is exactly when they will be tempting to skip.

**R4 — Fixing a contradiction by picking a winner can silently discard the losing document's reasoning.** `B5` withdrew `pv-draft-nomemory-1.0.0` because `CANONICAL_DECISIONS.md` is binding, but that variant existed for a reason: a prompt written to *use* memory, run with no memory, may produce a degenerate output that flatters the comparison rather than a fair one. The mitigation is that `pv-draft-1.0.0`'s grounding rule already handles empty supports gracefully — it produces `omitted_because_unsupported` entries rather than hallucinating. That is an argument, not a measurement. **Verify it at `G-7` with a real invocation before the counterfactual is filmed**, and if the OFF output is degenerate rather than merely memoryless, reopen the decision through change control rather than quietly reintroducing a second prompt.

**R5 — This review found what two reviewers looked for.** Both were pointed at consistency and integrity. Neither was asked to check whether the *design* is good, whether the retrieval pipeline will actually rank the June invoice against 16,035 near-neighbours, whether 51 scenarios are enough, or whether the authority grid's sixty hand-set numbers produce the right answers. `12_KERNEL_ALGORITHMS.md` R4 says plainly that they are "expert priors, not measurements." A pack can be perfectly self-consistent and still wrong about the world, and nothing in this document addresses that.

**R6 — The `CANONICAL_DECISIONS.md` register grew by eleven rows in one edit.** That is the correct mechanism and it was applied per `README.md` → *Change control*, but a register that grows every time two documents disagree eventually becomes a second place to look rather than the single place. If it exceeds roughly forty rows, the right response is to push decisions down into the owning specifications and leave pointers, not to keep appending.

---

## 8. Post-review remediation (2026-08-17)

The three defects this review ranked as most important were fixed after it was written. Recorded here so the review's own verdict is not read as current.

### M9 — Four incompatible dates for the landlord deposit clock — **CLOSED**

`00_PRODUCT.md` §2.3 said inspection 16 May with `due_at 2026-06-15`; `10_DATABASE_DDL.md` §17.5 said inspection 2 June, promise 3 June; `16_TRIGGER_DSL.md` §12 said inspection `2026-05-28`, deadline `2026-06-27`; `22_EVAL_DATASETS.md` sourced the commitment from "a 3 June written promise". This mattered because `due_at` is both the field the hero trigger predicate compares against and a date spoken aloud in the recorded video.

`00_PRODUCT.md` won on authority. Inspection is `2026-05-16`, the promise is made the same day, `due_at` is `2026-06-15T00:00:00Z`, and the trigger wakes at `due_at + WAKE_MARGIN_SECONDS`. Seventeen timestamps in `16_TRIGGER_DSL.md`, the §17.5 evidence table in `10_DATABASE_DDL.md`, and two scenario preambles in `22_EVAL_DATASETS.md` were rewritten to match. The no-op worked example's payment date moved from 25 June to 13 June so that it still falls before the deadline it is demonstrating. Frozen in `CANONICAL_DECISIONS.md` → *Hero dataset canon*.

### M4 + M5 — The design brief commissioned the wrong product — **CLOSED**

Two independent defects in the one document that gets handed to an outside session, where an error becomes a returned design rather than a diff.

**Names.** The brief invented four counterparties that the seed does not create — and made "Kestrel Moving Co." the moving company when `10_DATABASE_DDL.md` §17.3 seeds **Kestrel Analytics** as the *employer*. A design returned against that brief would have attributed the USD 420 damage claim to the user's employer. The same drift was present in `15_API_SPEC.md` (18 occurrences) and `30_UX_SPEC.md`, and the hero user was "Dana Whitfield" in three documents against **Alex Rivera** in the seed. All four names and the persona were corrected across every document except this one; `10_DATABASE_DDL.md` §17.3 is now named in `README.md` as the naming authority, and `32_JUDGE_MODE.md` R5 is marked resolved.

**Screens.** The brief commissioned *Relationship Dashboard, Case Timeline, State Proof, Contradiction Panel, Action Approval Inbox, Memory Trace Inspector, Judge Mode* — which is not the canonical seven. It omitted **Login** (the first surface any judge sees) and **Upload and forward** (the product's only real input, and the surface the video's second beat opens on), promoted the contradiction panel to a screen, and split Judge Mode across two. §4 is now S1–S7 matching `30_UX_SPEC.md`: Login and Upload/forward written from scratch, the contradiction panel demoted to §4.4.1 as a component of both hosts, and the two Judge Mode sections merged with Memory Trace as §4.6.1.

### M14 + M15 — Three documents, three render rules for the counterfactual — **CLOSED**

`32_JUDGE_MODE.md` §7.2 required a `parity` block that gates rendering; `15_API_SPEC.md` §8.31 never defined the field; `30_UX_SPEC.md` §14.4 rendered both columns unconditionally. As this document's own R3 noted, §14.4's rule is the one a build would have followed — so the gate would simply not have existed.

`parity` is now a normative field on §8.31 with a per-field table and an explicit client obligation, mirrored as mandatory item 9 in `30_UX_SPEC.md` §14.4. Separately, §14.4 item 1 mandated fixed header copy reading *"Both columns ran just now"* — false under the default `REPLAY_COMMITTED` strategy, where the MEMORY_ON column is the already-committed production run. The copy is now selected from `memory_on.strategy` and is truthful under both. Frozen in `CANONICAL_DECISIONS.md` → *Counterfactual parity canon*.

### What remains

Sixteen MAJOR and ten MINOR findings from §§3–4 are still open and are still accurate. None of them blocks Phase 1. R5 above remains the most important caveat in this document: **the pack is now substantially self-consistent, and self-consistency is not correctness.** Whether the retrieval pipeline actually ranks the June invoice above 16,035 near-neighbours, and whether the authority grid's hand-set numbers produce the right answers, are measurements that Phase 6 and Phase 14 make — not claims this review can settle.
