# Provenance — Canonical Decisions Register

Status: frozen planning baseline v1.1  
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared  
Decision date: 2026-08-17

This register resolves cross-document ambiguities. These decisions are binding for implementation. External capability checks are assigned to Phase 0; they are not invitations to redesign the product.

## Product and vocabulary

| Concern | Canonical decision |
|---|---|
| Product name | **Provenance**. NeverReset is a deprecated former name and appears only in naming history. |
| Promise | A system of record for the institutions that already have one of you. |
| Wedge | Unresolved obligations and the events that contradict, fulfill, withdraw, or expire them. |
| Grounding | `belief_support` edges that support, contradict, or qualify a belief version. |
| Lineage | The ordered `belief_versions` supersession chain and reason for each change. |
| Initial audience | Individual consumer in v1. Professional advocate workflows are a post-v1 market hypothesis, not v1 scope. |

## Names and counts

| Concern | Canonical decision |
|---|---|
| Python namespace | `provenance_domain`, `provenance_contracts`, `provenance_db`, `provenance_telemetry`. |
| SQL roles | `pv_migrator`, `pv_app_reader_writer`, `pv_kernel_writer`, `pv_agent_reader`, optional `pv_ops_reader`. |
| Canonical tables | 26 tables. Operational tables are included in this total. |
| Agent-safe views | `agent_case_context_v1`, `agent_active_beliefs_v1`, `agent_belief_lineage_v1`, `agent_evidence_retrieval_v1`, `agent_open_obligations_v1`. |
| ANN index | `evidence_embedding_ann_idx`; optional active-prefix variant `evidence_embedding_ann_active_idx`. |
| ANN repository entry point | `provenance_db.repositories.evidence.ann_search()`. |
| Case attention levels | `NONE`, `INFO`, `ATTENTION`, `URGENT`. No aliases are accepted. |
| Advocate attention classes | Separate model output: `NONE`, `FYI`, `ACTION_SUGGESTED`, `ACTION_REQUIRED`, `HUMAN_DECISION`; mapped deterministically to case attention and action policy, never stored directly in `cases.attention_level`. |
| Trigger types | `COMMITMENT_DEADLINE`, `RESPONSE_DEADLINE`, `CONFLICT_TIMEOUT`, `WARRANTY_WINDOW`. |
| Trigger results | `FIRED`, `NO_OP`, `DISARMED`, `EXPIRED`, `ERROR` plus one closed-set reason code. |
| Closed domain vocabularies | `specs/11_CONTRACTS.md` owns enum membership. DDL checks, generated prompt schemas, APIs, fixtures, and UI filters mirror those values exactly; no layer-local aliases. |
| Evaluation corpus | 51 labelled scenarios: identity 9, temporal 8, contradiction 10, commitments 9, prospective 7, safety 8. |

## Evidence and retrieval

| Concern | Canonical decision |
|---|---|
| Evidence lifecycle | `retraction_status` is `ACTIVE`, `RETRACTED`, `SUPERSEDED`, or `QUARANTINED`. |
| Retrieval eligibility | Stored/generated `is_retrieval_eligible = (retraction_status = 'ACTIVE')`. Only `ACTIVE` evidence may enter new retrieval or ground a new belief. |
| Historical visibility | Retracted and superseded evidence retains bytes, metadata, embeddings, and historical support edges. State Proof may show it with status badges; retrieval excludes it. |
| Vector filtering | The canonical query over-fetches the user-prefixed ANN partition, then filters `tenant_id`, `retraction_status = 'ACTIVE'`, and `embedding_version`. The optional active-prefix index is allowed only after the Phase 0 probe and recall evaluation. |
| Identity order | Exact identifiers and deterministic identity signals precede vector similarity. Vector output is advisory and never canonical truth. |
| Multi-case artifacts | Ingestion splits one artifact into independently traceable, one-case `MemoryProposal` objects sharing the artifact/evidence references. The kernel never opens a cross-case transaction for a single proposal. |
| Superseded evidence | Excluded from active retrieval; visible through historical State Proof and lineage queries. No down-weighted active path exists in v1. |

## Models and prompts

| Concern | Canonical decision |
|---|---|
| Tier E | `anthropic.claude-haiku-4-5` for extraction and classification. |
| Tier R | `anthropic.claude-opus-5` for semantic resolution, contradiction characterization, attention assessment, and advocacy drafting. |
| Tier E fallback | One schema-repair attempt; on invocation failure, one Opus 5 fallback at low effort. Exhaustion becomes pending review. |
| Tier R fallback | No downgrade to a weaker model. Failure persists a pending-human-review result. |
| Embeddings | `amazon.titan-embed-text-v2:0`, 1024 dimensions, frozen embedding version `v1`. |
| Prompt authority | `specs/14_PROMPTS.md` owns byte-exact prompts and model parameters. |

## Memory, action, and time

| Concern | Canonical decision |
|---|---|
| Canonical writer | Only the deterministic Memory Kernel using `pv_kernel_writer`. Agents never receive canonical write credentials. |
| Transaction isolation | CockroachDB `SERIALIZABLE`, bounded retry for SQLSTATE `40001`, no model or network call inside the callback. |
| External action | Draft, validate grounding, create intent, human approve, bind approval to case revision and draft SHA-256, revalidate, execute idempotently. |
| Retention effect | If lawful deletion removes all grounding, the belief version becomes `RETRACTED` with a tombstoned support record; it never silently disappears. |
| Trigger arithmetic | No general arithmetic nodes in the trigger DSL. Add reviewed deterministic derived fields to the projection registry. |
| Trigger demonstration | Use the same manual-wake entry point for a false-predicate no-op and the landlord fire. Do not mutate and secretly revert canonical state for presentation. |
| Business days | v1 means Monday through Friday with no holiday calendar; extraction must surface `BUSINESS_DAY_CALENDAR_ASSUMED`. |

## Demo and disclosure

| Concern | Canonical decision |
|---|---|
| Counterfactual | Memory OFF and ON use the same artifact, model, prompt, and graph. OFF receives empty retrieval and State Proof. The request diff is kept for live Q&A, not the three-minute video. |
| Fixture mode | Permitted for local deterministic graph tests and emergency demonstration only with a permanent visible banner and `fixture_mode: true`. The recorded demonstration must use live mode. |
| Seed disclosure | 32 hero evidence rows are curated; 18,000 decoys are synthetic; state changes, conflict detection, retrieval, trigger evaluation, and drafting are computed at demo time. |
| Judge Mode | Built from persisted runtime rows and spans. Scripted trace animation and hard-coded object identifiers are forbidden. |

## Hero commit canon

Frozen 2026-08-17 by the consistency and integrity review recorded in `quality/24_CONSISTENCY_REVIEW.md`. These values were previously contradicted across four to six documents each; they are now single-valued and every one of them is machine-checkable.

| Concern | Canonical decision |
|---|---|
| Case-reopen reason code | `CONTRADICTORY_EVIDENCE`. It is a member of `CASE_REOPEN_REASON_CODES` in `specs/11_CONTRACTS.md` and a **guard** on the `RESOLVED → REOPENED` transition, so `CONTRADICTORY_EVIDENCE_ADMITTED` and `RC_CONTRADICTORY_EVIDENCE` would raise `IllegalTransition`, not merely read oddly. |
| Hero conflict | `conflict_type = 'VALUE_CONFLICT'`, family `BALANCE`, `status = 'NEEDS_HUMAN'`, `severity = 'HIGH'`, `requires_human = true`. Produced by gate H5 of `specs/12_KERNEL_ALGORITHMS.md` §3.3: monetary family, `monetary_exposure = 186.00 ≥ 100.00`, which short-circuits before the authority-margin test. Disposition `RETAIN_INCUMBENT_DISPUTED` — value unchanged, `epistemic_status` `CONFIRMED → DISPUTED`. `status = 'OPEN'` is a legal column value but no disposition rule emits it, so it is not the hero's value. The `AUTO_RESOLVED` outcome in `specs/12_KERNEL_ALGORITHMS.md` §1.6 is a **different** conflict (`SERVICE_STATUS` via entailment EN-1) in a different worked dataset; both are correct and they are not the same row. |
| Evidence admitted from the June invoice | Exactly 3: `DATE_ASSERTION`, `AMOUNT_ASSERTION`, `IDENTIFIER_ASSERTION` (`00_PRODUCT.md` §2.3). |
| Corpus counts | **18,035 total** (16,000 hero decoys + 1,000 `iso-a` + 1,000 `iso-b` + 32 curated + 3 retraction fixtures); **16,035 user-scoped** for the hero. No surface may render a cross-tenant total as a user-scoped figure. Any surface rendering `corpus_size_user_scoped` or `corpus_size_visible` renders the value counted at query time, never a constant. |
| Counterfactual prompt | MEMORY OFF uses `pv-draft-1.0.0` — the **same** prompt asset, `effort`, `max_tokens` and output schema as MEMORY ON — with an empty TRUSTED STRUCTURED CONTEXT block. There is no `pv-draft-nomemory-*` asset. This is what makes the `parity` block in `frontend/32_JUDGE_MODE.md` §7.2 provable rather than decorative. |
| Operating-mode disclosure | `GET /v1/version` is the single authoritative channel and carries `fixture_mode`, `agent_mode`, `otlp_export`, `schema_revision`, `db_ok`, `git_sha`. It is unauthenticated so a judge can `curl` it. `/v1/healthz` stays a bare liveness probe and never carries `fixture_mode`. `GET /v1/me.feature_flags.fixture_mode` is the UI-binding mirror. The field is `git_sha`; `build_sha` is not a field name. |
| Kernel retry exhaustion | The Kernel performs **no** side effect after the retry cap. It returns `RETRYABLE_CONCURRENCY` with `RETRY_EXHAUSTED_NOT_ENQUEUED`; the caller re-drives over `503` + `Retry-After`. The control plane holds no `sqs:*` permission and no kernel retry queue exists. |
| `pv_ops_reader` | **Created**, in migration `0008`, because `tools/trace_verify.py` has a real consumer. Strictly read-only: `SELECT` on the five `_v1` views and eleven operational tables, no `INSERT`/`UPDATE`/`DELETE`. Operator and CI credential only; it is not an App Runner pool. `provenance/db` carries five keys, including `ops_reader_url`. |
| MCP tool-call naming | Column `agent_runs.tool_calls`; HTTP field `mcp_tool_calls[]`. `agent_runs.mcp_tool_calls` is not a column name. |
| Trace node types | Seventeen, closed, listed in `specs/15_API_SPEC.md` §8.28, including `CANONICAL_CHANGE` as a child of `DB_TRANSACTION`. `state_transitions` is both the spine that orders those children and the source of the flat `memory_operations` array — one DAG shape, two renderings. |

## Phase 0 verification decisions

These questions cannot be answered truthfully in prose. The architecture already defines what happens for every result:

| Probe | Success path | Predetermined fallback |
|---|---|---|
| CockroachDB vector-index syntax/opclass | Use the first supported cosine variant in DDL order. | L2-normalized vector index; if no vector index works, disclose brute-force user-partition scan and fail the vector-index gate. |
| Computed stored column support | Use generated `is_retrieval_eligible`. | Plain boolean plus consistency check, written only by the kernel. |
| View/grant behavior | MCP role reads views and is denied base tables. | Stop Phase 11; do not weaken grants. Use a controlled read API until the database boundary is proven. |
| Bedrock model access | Use canonical Tier E and Tier R IDs. | Fixture mode for development only; live release remains blocked until model access works. |
| Seed database clone/restore | Per-scenario logical databases from template dump. | Sequential scenarios with transaction rollback; isolate live-model writes explicitly. |

## Hero dataset canon (frozen 2026-08-17)

Four documents previously carried four incompatible date sets for the same commitment, and three carried a different persona and different counterparty names from the ones the seed actually creates. Both classes of drift are now closed. `specs/10_DATABASE_DDL.md` §17 is the naming and seeding authority; `00_PRODUCT.md` §2.3 is the date authority.

| Concern | Canonical decision |
|---|---|
| Hero user | **Alex Rivera**, timezone `America/New_York`, `judge_mode_enabled = true`. Not "Dana Whitfield" — that persona is retired and must not reappear in any example. |
| ISP | **Northline Fiber**, two relationships on one counterparty (old account `NF-4471-8802`, new address `NF-9913-2250`). The pair is the sharpest decoy in the corpus. |
| Landlord | **Harborview Property Management** (`HPM-LEASE-2024-3B`). |
| Moving company | **Beltline Movers** (`BM-88214`). |
| Employer | **Kestrel Analytics** (`KA-EMP-3308`). Kestrel is the **employer**, never the mover — an earlier draft made "Kestrel Moving Co." the mover, which would have attributed the USD 420 damage claim to the user's employer. |
| Decoy utility | **Cascade Power** (`CP-770194`). |
| Final inspection | `2026-05-16`. |
| Deposit promise | Written "within 30 days of inspection", made `2026-05-16`. |
| Deposit `due_at` | `2026-06-15T00:00:00Z`. Every "days overdue" figure derives from this against the `2026-09-18` demo clock — 95 days. |
| Trigger wake | `due_at` + `WAKE_MARGIN_SECONDS`, i.e. `2026-06-15T00:01:00Z`. |
| Example names | Must be drawn from §17.3, never invented. A new counterparty in an example is a documentation defect, not a stylistic choice. |

## Counterfactual parity canon (frozen 2026-08-17)

| Concern | Canonical decision |
|---|---|
| Parity block | `GET /v1/judge-mode/counterfactual/{id}` returns a normative `parity` object comparing `artifact_id`, `artifact_sha256`, `model_id`, `prompt_version`, `graph_version`, `decode_params_sha256`. Owner: `specs/15_API_SPEC.md` §8.31. |
| Render gate | `parity.all_equal = false` means the two output columns are **not rendered**; a failure banner replaces them. Binding on `frontend/30_UX_SPEC.md` §14.4 item 9 and `frontend/32_JUDGE_MODE.md` §7.2 alike. |
| Header copy | Strategy-dependent, selected from `memory_on.strategy`, never a client constant. Under `REPLAY_COMMITTED` the MEMORY_ON column is the already-committed production run and the UI must not claim it "ran just now". |
| Permitted differences | Exactly four: `retrieval_enabled`, `canonical_memory_enabled`, `corpus_size_visible`, and the resulting `output`. Anything else differing is a parity failure. |

## Bedrock model id canon (frozen 2026-08-17, supersedes the Tier E/R rows above)

Phase 0 probing against the live account disproved the model ids frozen earlier. Two separate facts, both established by invocation rather than by listing — `list-foundation-models` returns ids that are **not invocable**, which is the trap.

| Concern | Canonical decision |
|---|---|
| Identifier form — Anthropic | **Anthropic chat models are invoked by inference-profile id, never by bare model id.** A bare id returns `ValidationException: Invocation ... with on-demand throughput isn't supported. Retry your request with the ID or ARN of an inference profile`. The invocable form carries a region-group prefix: `us.` or `global.`. |
| Identifier form — every other provider | **Third-party serverless models are invoked by bare id, and reject the profile form.** `us.zai.glm-5`, `us.moonshotai.kimi-k2.5`, `us.google.gemma-3-27b-it` and `us.deepseek.v3.2` all return `ValidationException`, while the same four ids without the prefix invoke successfully. The rule is the **mirror image** of the Anthropic rule, not an extension of it. A client that applies one rule uniformly cannot call both families. (`D-00-040`.) |
| Tier E | **`us.anthropic.claude-haiku-4-5-20251001-v1:0`** — verified invocable. Note it also carries the dated suffix; the undated `anthropic.claude-haiku-4-5` does not exist in any form on Bedrock. |
| Tier R — in force | **`us.anthropic.claude-opus-4-6-v1`** — verified invocable, and the most capable reasoning model this account can call. This is the shipped configuration, not a temporary substitution. |
| Tier R — second reachable option | **`us.anthropic.claude-sonnet-4-6`** — verified invocable. Held as the fallback if Opus 4.6 throttles under eval load; a weaker choice for contradiction characterisation, so it is not the default. |
| Denied on this account | `us.anthropic.claude-opus-5`, `us.anthropic.claude-sonnet-5`, `us.anthropic.claude-opus-4-8`, `us.anthropic.claude-opus-4-7`. A grant would have to be requested in the Bedrock console, us-east-1. **Nothing blocks on it** — see the row above. |
| Embeddings | **`amazon.titan-embed-text-v2:0`**, unchanged, invoked by **bare id**. Verified: 1024 dims, L2 norm 1.0000000 with `"normalize": true`. |
| Reachable but unadopted | `zai.glm-5`, `zai.glm-4.7`, `moonshotai.kimi-k2.5`, `google.gemma-3-27b-it`, `deepseek.v3.2`, `qwen.qwen3-next-80b-a3b`, `openai.gpt-oss-120b-1:0` are all invocable. None is adopted. A second model family in the reasoning path would mean two prompt calibrations, two refusal shapes and two JSON-mode behaviours to hold the extraction contract against, and `14_PROMPTS.md` is calibrated once. They are recorded because a **capacity** failure — not a capability one — is the scenario in which reaching for one is the right move, and that decision should not have to be re-probed under time pressure. |
| Router obligation | Both tier ids are read from configuration (`BEDROCK_REASONING_MODEL_ID`, `BEDROCK_EXTRACTION_MODEL_ID`). Swapping either is a one-line environment change, never a code change. Because the two identifier forms differ by provider, the router **must not** synthesise a profile prefix; it passes the configured string through unmodified. `agent_runs.model_route` records the id actually used, so every run is attributable to the model that served it. |
| Disclosure | The build ships on **Opus 4.6**. The README states that, and does not claim Opus 5. Claiming a model you did not run is the kind of small, checkable dishonesty the pack exists to prevent, and `agent_runs.model_route` makes it checkable against persisted state. |

Every occurrence of a bare `anthropic.claude-*` id elsewhere in the pack is superseded by this section.

Evidence: `ops/bedrock-probe.txt`, run 2026-08-17T22:14:20Z. Every row above is a live `Converse` result, not a `list-foundation-models` listing — the listing returns ids that are not invocable, which is the trap the earlier run fell into.

## Gemini model id canon (frozen 2026-08-24, supersedes the Bedrock canon above)

On 2026-08-24 the model and hosting platform moved off AWS Bedrock and onto
Google: **Gemini 3.5 or newer**, a Google agent framework, and Google Cloud for
the runtime. The CockroachDB cluster stays, because the eight migrations,
twenty-six tables, five agent views, the seed and roughly 390 database and
retrieval tests are the largest block of verified work in the repository, and
`CREATE VECTOR INDEX` has no exact pgvector/ScaNN equivalent.

Model access is the **Gemini Developer API** via an AI Studio API key. No GCP
service account, no ADC, no IAM. The Google Cloud footprint is therefore
**Cloud Run**, not the model API.

| Concern | Canonical decision |
|---|---|
| Reasoning tier — **there is no Pro option** | `gemini-3.1-pro-preview` is the only Pro model on the API and it is version **3.1**, *below* the 3.5 floor this build holds to; it also has no free tier. Gemini 3.5 Pro was announced but has not rolled out. **Both tiers are therefore Flash-class**, and any document implying a Pro reasoning tier is superseded. |
| Tier E | `gemini-3.5-flash-lite` — extraction, classification, bulk structured output. |
| Tier R | `gemini-3.7-flash` — semantic resolution, contradiction characterisation, attention assessment, advocacy drafting. |
| Tier R fallback | `gemini-3.6-flash` (GA), held for capacity failure, not capability failure. |
| Embeddings | **`gemini-embedding-2`** at `output_dimensionality=1536`, `embedding_version = 'v2'`. |
| Why `gemini-embedding-2` and not `gemini-embedding-001` | `gemini-embedding-2` **auto-normalizes truncated dimensions** (768, 1536); `001` requires manual normalization for any width other than 3072. This stack ranks by **cosine**, and a missed normalization is silent — the distances stay numbers, stay ordered, and stop meaning anything. The model that makes the failure structurally impossible wins over the one that costs $0.05/1M less. It also raises the input ceiling from 2,048 to 8,192 tokens and is multimodal. |
| Embedding width | **1536**. On Google's recommended list (768 / 1536 / 3072), halves storage and index-build cost against the 3072 default, and MRL truncation costs little quality. |
| Agent framework | **`google-genai`** SDK (installed, 1.60.0). ADK remains an option for how the agent layer *reads*, never a requirement. |
| Router obligation | Every id is read from configuration — `GEMINI_REASONING_MODEL_ID`, `GEMINI_EXTRACTION_MODEL_ID`, `GEMINI_REASONING_FALLBACK_MODEL_ID`, `GEMINI_EMBEDDING_MODEL_ID`. Swapping one is an environment change, never a code change. `agent_runs.model_route` records the id actually used, so every run is attributable to the model that served it. |
| Database | CockroachDB Cloud, **unchanged**, on AWS `us-east-1`. Cloud Run should therefore sit in GCP `us-east4` — same physical metro, so the cross-cloud hop stays in single-digit milliseconds instead of 70+. |

### These ids are PROBED, and the transcript is the evidence

**Settled 2026-08-24 by live invocation.** `python ops/probes/gemini_probe.py`
exited **0** with `PASS 11 | FAIL 0 | CANNOT RUN 0`; the transcript is
`ops/gemini-probe.txt`. Every id in the table above was *invoked*, not listed —
`client.models.list()` is recorded in the transcript under an explicit
`REFERENCE ONLY, NOT PROOF` heading, because listing is the trap the Bedrock
canon fell into and enumeration is not invocation.

| Id | Verdict | Measured |
|---|---|---|
| `gemini-3.7-flash` (Tier R) | PASS | `reply='ok'`, 125 tokens, **116 of them thinking** |
| `gemini-3.5-flash-lite` (Tier E) | PASS | `reply='ok'`, 9 tokens, `thoughts=None` — **does not think** |
| `gemini-3.6-flash` (fallback) | PASS | `reply='ok'`, 116 tokens, 107 thinking |
| `gemini-embedding-2` | PASS | **1536 dims, L2 norm 1.0000003 — unit-normalised**, drift `0.000e+00` across two calls |
| `gemini-embedding-001` | PASS | 1536 dims, **L2 norm 0.6935943 — NOT normalised** |
| structured output (`response_schema`) | PASS | `Extracted(amount='186.00', currency='USD')` |
| native multimodal | PASS | described a 64×64 red-over-blue PNG as `'Red and blue.'` |

**The embedding choice is now measured rather than argued.** The row above that
justified `gemini-embedding-2` over `001` on auto-normalization was a claim read
from documentation; it is now a number. At the same width, in the same minute,
`2` returns 1.0000003 and `001` returns 0.6935943. The `gemini-001-v3` profile's
`caller_must_normalize=True` is therefore correct and load-bearing, not defensive
— cosine over unnormalised vectors still returns ordered numbers, which is why
nothing downstream would ever have noticed.

**Unknown 1 (the contested spelling) is closed.** `gemini-embedding-2` and
`gemini-embedding-2-preview` both invoke and return byte-identical results
(1536, 1.0000003). Migration `0009`'s CHECK may keep admitting both; there is now
evidence for that latitude rather than an absence of evidence.

**Two findings that the probe only produced because it was corrected first.**
Both were defects in the probe, and both had already been written down as PASS or
FAIL in the first transcript:

1. **`max_output_tokens` is one allowance shared with thinking.** It is not a cap
   on the reply. Every Flash tier above Lite thinks by default, so at
   `max_output_tokens=16` the budget was spent before the first visible token:
   `candidates_token_count=None`, `finish_reason=MAX_TOKENS`, `response.text=''`.
   An empty string is not an exception, so the first run recorded **PASS for three
   ids that answered nothing**. The router is unaffected — it budgets 8192/16000
   and `router.py:482` treats truncation as a schema failure — but the probe's
   verdict was vacuous, and a vacuous verdict in a transcript whose purpose is to
   be believed is worse than no transcript.
2. **PB-G6's FAIL was the fixture, not the capability.** The probe uploaded a 1×1
   *transparent* PNG and the API answered `400 INVALID_ARGUMENT: Unable to process
   input image`. An 8×8 solid red PNG — **the same 75 bytes** — succeeds. Acting on
   that FAIL would have kept an external OCR dependency and forfeited native
   multimodal ingestion on the evidence of one transparent pixel. This is `D-00-005`
   in its purest form: the probe could not perform the action and reported that the
   capability had failed.

**Unknown 2 (rate limits) still stands.** The published limits are per-tier and
only visible in the AI Studio dashboard. Re-embedding 18,035 texts is the longest
unattended job in the plan, and a free-tier limit could turn a fifty-minute job
into an overnight one. The Batch API offers higher throughput at half price but
appears to require Tier 1 (billing enabled). Nothing in this probe measured
throughput, and nothing here should be read as if it did.

Every occurrence of a `us.anthropic.*`, `anthropic.claude-*` or
`amazon.titan-embed-*` id elsewhere in the pack is superseded by this section for
new work. The Titan constants remain reachable in code because the 18,035 vectors
currently in `evidence_items` were rendered by Titan at 1024 dimensions and stay
uninterpretable without them until the re-embed lands.

## Repository layout canon (frozen 2026-08-17)

Four documents specified four different repository trees, and `ARCHITECTURE.md` §25 contradicted the implementation map outright by specifying a microservice decomposition that §4.2 explicitly rejects. Building the wrong one would have put the Memory Kernel in its own service and broken the single-canonical-writer boundary.

| Concern | Canonical decision |
|---|---|
| Layout authority | `implementation/00_IMPLEMENTATION_MAP.md` §5, as reconciled. All other trees defer to it. |
| `ARCHITECTURE.md` §25 | **Superseded.** Marked in place; retained only as a record of a rejected alternative. Must not be built from. |
| Deployment units | Four: `web`, `control-plane`, `agent-runtime`, `workers`. Not five services, not three agent services. |
| Hero artifact bytes | One location: **`demo/artifacts/`**. Replaces `demo_data/the_move/` and `db/demo/`, both retired. |
| Seed scripts | `scripts/seed/` — `ids.py` (the `sid()` helper), `decoys.py`, `embeddings.py`. |
| Execution evidence | `ops/` — probes, decisions, gate ledgers, logs, and `ops/defects/DEFECTS.md`. Committed and gitleaks-scanned. |
| Test placement | Per-package tests live beside their package. Top-level `tests/` holds only genuinely cross-package suites (`retrieval/`, `e2e/`, `support/`). A test importing from exactly one package belongs next to that package. |

## Test and corpus counts (frozen 2026-08-17)

Three totals had been carried forward without re-adding the per-file column. The enumerated per-file counts are authoritative.

| Concern | Canonical decision |
|---|---|
| `provenance_domain` tests | **230** (93 domain and state machines + 137 kernel algorithms) |
| Layer 1 total | **392** (230 + 44 contracts + 14 `provenance_db` unit + 104 control-plane unit) |
| Full suite total | **626** across the eight layers |
| Eval corpus | **51** scenarios. The two `20_TDD_STRATEGY.md` occurrences of 62 are corrected; this register, `22_EVAL_DATASETS.md`, `G14.1` and the README were already right. |

A gate that asserts a test count against a wrong figure fails on arrival, so these are contract values, not documentation trivia.

## Closed former questions

- Superseded evidence is excluded from active retrieval, not merely down-weighted.
- Beliefs that lose all grounding become retracted rather than being deleted.
- The consumer wedge is the v1 product; professional workflows are future discovery.
- The counterfactual payload diff is a Q&A artifact.
- Trigger derived comparisons use named projection fields, not general arithmetic AST nodes.
- Multi-case artifacts become multiple single-case proposals.
- The trigger no-op demonstration uses a real false predicate and does not perform a hidden state revert.

No unresolved planning question may change a v1 contract. Remaining risks are verification, calibration, or market-learning risks and are tracked by phase gates.
