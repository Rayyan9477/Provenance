# Provenance — Canonical Decisions Register

Status: frozen planning baseline v1.1  
Implementation status: not started  
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
| Initial audience | Individual consumer in the hackathon build. Professional advocate workflows are a post-hackathon market hypothesis, not v1 scope. |

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
| Fixture mode | Permitted for local deterministic graph tests and emergency demonstration only with a permanent visible banner and `fixture_mode: true`. The recorded submission must use live mode. |
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
| CockroachDB vector-index syntax/opclass | Use the first supported cosine variant in DDL order. | L2-normalized vector index; if no vector index works, disclose brute-force user-partition scan and fail the sponsor vector-index submission gate. |
| Computed stored column support | Use generated `is_retrieval_eligible`. | Plain boolean plus consistency check, written only by the kernel. |
| View/grant behavior | MCP role reads views and is denied base tables. | Stop Phase 11; do not weaken grants. Use a controlled read API until the database boundary is proven. |
| Bedrock model access | Use canonical Tier E and Tier R IDs. | Fixture mode for development only; live submission remains blocked until model access works. |
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

## Closed former questions

- Superseded evidence is excluded from active retrieval, not merely down-weighted.
- Beliefs that lose all grounding become retracted rather than being deleted.
- The consumer wedge is the v1 product; professional workflows are future discovery.
- The counterfactual payload diff is a Q&A artifact.
- Trigger derived comparisons use named projection fields, not general arithmetic AST nodes.
- Multi-case artifacts become multiple single-case proposals.
- The trigger no-op demonstration uses a real false predicate and does not perform a hidden state revert.

No unresolved planning question may change a v1 contract. Remaining risks are verification, calibration, or market-learning risks and are tracked by phase gates.
