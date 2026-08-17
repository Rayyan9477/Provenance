# Provenance — Judge Mode Specification

Purpose: define the four-panel technical-credibility surface — consumer state, State Proof, Memory Trace, systems status — such that every pixel on screen is traceable to a persisted CockroachDB row or an OpenTelemetry span, and no element can be rendered without one.

Status: planning complete v1.1
Implementation status: not started

Audience: frontend engineers building `apps/web/src/app/(judge)`, backend engineers implementing the `/v1/judge-mode/*` and `/v1/traces/*` handlers, the demo operator who runs the walkthrough live, the gate reviewer signing `G-11` and `G-12`, and hackathon judges auditing whether the memory system is real.

---

## 0. Authority, scope, and the one rule

### 0.1 What this document owns

Judge Mode's screen composition, per-element data lineage, the Memory Trace DAG rendering contract, the counterfactual presentation, the three safe probes, the redaction allowlist, the anti-requirements, and the judge click path.

### 0.2 What this document does not own

| Concern | Owner |
|---|---|
| HTTP paths, status codes, error envelope, idempotency | `specs/15_API_SPEC.md` |
| Tables, columns, enums, views, grants, seed contents | `specs/10_DATABASE_DDL.md` |
| Enum membership | `specs/11_CONTRACTS.md` |
| Retrieval stages, scoring, beam size, ANN SQL | `specs/13_RETRIEVAL_SPEC.md` |
| Kernel decision pipeline | `specs/12_KERNEL_ALGORITHMS.md` |
| Span names and metric names | `implementation/05_RELIABILITY_EVAL_DEMO.md` §6, §7 |
| Product vocabulary, invariants, hero scenario | `00_PRODUCT.md` |
| Cross-cutting frozen names and counts | `CANONICAL_DECISIONS.md` |

Where this document shows an identifier, an enum value, a table name, a view name, a role name, or a model id, it is quoting one of the above. Where it needs a surface that does not yet exist in `specs/15_API_SPEC.md`, it says so explicitly in §8.1 and §9.1 and routes the change through `README.md` → *Change control*. It never assumes a surface into existence.

### 0.3 The one rule

> **Judge Mode renders persisted rows and spans. If a value cannot be traced to a row id, a column, or a span attribute, it does not appear on screen.**

This is the operational form of `CANONICAL_DECISIONS.md` → *Demo and disclosure* → *Judge Mode*: "Built from persisted runtime rows and spans. Scripted trace animation and hard-coded object identifiers are forbidden." Everything in §11 (anti-requirements) is a restatement of this rule with a specific failure mode attached.

### 0.4 Vocabulary reminder

Three terms are load-bearing and are never collapsed (`00_PRODUCT.md` §0.2):

- **Provenance** — the product name. Never a common noun.
- **grounding** — `belief_support` edges (`SUPPORTS` / `CONTRADICTS` / `QUALIFIES`) linking a belief version to evidence and claims.
- **lineage** — the `belief_versions` supersession chain and the reason for each supersession.

Panel B renders both, labelled separately. A Judge Mode string that would read the same with the two words swapped is a bug.

---

## 1. Access, gating, and route layout

### 1.1 Gate

Judge Mode is reachable only when `GET /v1/me` returns `judge_mode_enabled: true`. That flag is `true` when the Cognito principal is in group `provenance-judges` **or** `users.id` is in the seeded demo allowlist (`specs/15_API_SPEC.md` §2.5). It grants **no** cross-user visibility: a judge requesting a non-demo-tenant trace receives `404 TRACE_NOT_FOUND`, not `403`.

Two additional flags from `GET /v1/me.feature_flags` gate sub-surfaces:

| Flag | Gates | Behaviour when `false` |
|---|---|---|
| `counterfactual_enabled` | §7 counterfactual panel | Panel renders a disabled card reading "Counterfactual disabled for this deployment." No fabricated content. |
| `mcp_trace_visible` | §6 MCP nodes and the MCP strip in Panel D | MCP nodes are omitted from the DAG entirely; the strip reads "MCP trace hidden by configuration." Never replaced with a placeholder call. |

Clients must treat an absent flag as `false` (`specs/15_API_SPEC.md` §8.3).

### 1.2 Routes

```text
apps/web/src/app/(judge)/
  judge/
    layout.tsx                     # 4-panel shell, fixture banner, trace selector
    page.tsx                       # default: latest materially-changing trace for the hero case
    [traceId]/page.tsx             # deep link: /judge/018f9c2e-...-8a90
  _components/
    PanelA_ConsumerState.tsx
    PanelB_StateProof.tsx
    PanelC_MemoryTrace.tsx
    PanelD_SystemsStatus.tsx
    CounterfactualPanel.tsx
    ProbePanel.tsx
    IsolationPanel.tsx
    IdChip.tsx                     # the correlation primitive (§4.6)
    FixtureBanner.tsx
  _lib/
    api.ts                         # generated from openapi.json (openapi-typescript)
    trace.ts                       # DAG layout, no data synthesis
    redact.ts                      # client-side assertion of §10, not the enforcement point
```

Every panel is a server component that fetches on the server with the human access token and streams; only the DAG canvas and the probe controls are client components. There is no client-side data store holding trace data between navigations — a reload must re-fetch, so a stale render is impossible.

### 1.3 Layout

Desktop (≥ 1440 px) is the target; the video is recorded at 1920×1080.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│  FIXTURE BANNER (only when GET /v1/version.fixture_mode === true)             │
├────────────────────────────┬─────────────────────────────────────────────────┤
│  A  CONSUMER STATE         │  B  STATE PROOF                                 │
│  what the user sees        │  grounding + lineage, deterministic SQL          │
├────────────────────────────┴─────────────────────────────────────────────────┤
│  C  MEMORY TRACE  — DAG, left→right, deterministic vs model lanes            │
├──────────────────────────────────────────────────────────────────────────────┤
│  D  SYSTEMS STATUS  — six live indicators + sponsor-tool strip               │
└──────────────────────────────────────────────────────────────────────────────┘
```

Below the fold, in order: counterfactual (§7), probes (§8), isolation (§9). `05_RELIABILITY_EVAL_DEMO.md` §8 is explicit — "Do not overwhelm the main demo with infrastructure; reveal it after the product 'aha'." Panels A and B are therefore above C and D, and the counterfactual sits below all four.

---

## 2. Panel A — consumer state

The human-legible view. It must be indistinguishable from what a non-judge user sees; Judge Mode adds *correlation chips*, never different data.

### 2.1 Elements and exact data source

Single source call: `GET /v1/dashboard?context_id={the-move}` plus `GET /v1/cases/{case_id}` for the focused case.

| Element on screen | API field | Backing table.column |
|---|---|---|
| Context title ("The Move — …") | `contexts[0].title` | `contexts.title` |
| Relationship count | `contexts[0].relationship_count` | `count(relationships)` scoped by `context_id` |
| Total outstanding, per currency | `contexts[0].total_outstanding[]` | `sum(commitments.outstanding_amount)` grouped by `commitments.currency` |
| Relationship row: counterparty name | `relationships_summary[].counterparty.display_name` | `counterparties.display_name` |
| Relationship row: account reference | `relationships_summary[].external_account_ref_masked` *(detail view)* | `relationships.external_account_ref`, masked to last four (§10.3) |
| Relationship row: attention badge | `relationships_summary[].attention_level` | `cases.attention_level` rolled up — `NONE \| INFO \| ATTENTION \| URGENT` |
| Case card: status | `cases_attention[].status` | `cases.status` |
| Case card: revision | `cases_attention[].revision` | `cases.revision` |
| Case card: headline | `cases_attention[].headline` | Rendered by `provenance_domain` from `cases_attention[].attention_reason_codes`; **never model-generated** (`specs/15_API_SPEC.md` §8.4) |
| Case card: reopened count | `GET /v1/cases/{id}.reopened_count` | `cases.reopened_count` |
| Commitment line: committed / fulfilled / outstanding | `GET /v1/cases/{id}.commitments[]` | `commitments.committed_amount`, `.fulfilled_amount`, `.outstanding_amount` |
| Commitment status chip | same | `commitments.status` — `PARTIAL` is enforced by `ck_commitments_outstanding_identity` |
| Trigger chip ("woke itself") | `GET /v1/triggers` item | `prospective_triggers.state`, `.last_result`, `.fired_at` |

### 2.2 Judge Mode additions to Panel A

Exactly three, all correlation-only:

1. **Revision chip** beside the case status, rendering `cases.revision` with a copy button and the SQL `SELECT revision FROM cases WHERE id = '<case_id>';`.
2. **Attention provenance popover** — hovering the attention badge lists `attention_reason_codes` verbatim (`CONFLICT_OPEN`, `ACTION_AWAITING_APPROVAL`, `TRIGGER_FIRED`, `COMMITMENT_OVERDUE`, …) with the note "these codes select the headline template; the headline is not written by a model."
3. **Derivation chip** beside `outstanding_amount`, rendering the expression from `GET /v1/cases/{id}/state-proof.derivations[]`: `committed_amount − fulfilled_amount = 220.0000 USD`, marked `deterministic_derivation: true`.

Panel A must never show a number that Panel B cannot justify. If `GET /v1/dashboard` and `GET /v1/cases/{id}/state-proof` disagree on a monetary value, Panel A renders the value with an error chip reading `STATE_PROOF_DISAGREEMENT` and the console logs the two values. Silently preferring one is forbidden — a disagreement is a data-integrity bug, and hiding it is exactly the failure Judge Mode exists to make impossible.

### 2.3 Money rendering

Money is never a JavaScript `number`. `GET` responses deliver `{ "currency": "USD", "amount": "1800.0000" }` (`specs/15_API_SPEC.md` §1.3). The frontend types money as a branded string and formats with `Intl.NumberFormat` from the decimal string. A lint rule forbids `parseFloat` / `Number(` on any field named `amount`.

---

## 3. Panel B — State Proof

Why Provenance holds the current position. Assembled by SQL, not by a model (`00_PRODUCT.md` §3, *State Proof*).

Single source call: `GET /v1/cases/{case_id}/state-proof`, with an `include_retracted` toggle.

### 3.1 Three stacked sections, in this order

**B1 — Canonical position.** One row per belief: `predicate`, `value_json`, `epistemic_status`, `belief_confidence`, `valid_from`/`valid_to`, `grounded`. Source: `beliefs` joined to `belief_versions` at `beliefs.current_version_id`.

For a belief whose status changed while its value did not — the hero's `balance_owed` v1 → v2, `$0.0000` `CONFIRMED` → `$0.0000` `DISPUTED` — **status is the visual primary and value is secondary**, with the fixed caption:

> The amount did not change. Our confidence in it did.

This is `00_PRODUCT.md` R3's mitigation, rendered. It is a static string chosen by the `epistemic_status` transition, not generated text.

**B2 — Grounding.** One row per `belief_support` edge, from `state-proof.beliefs[].grounding[]`.

| Column | API field | Backing |
|---|---|---|
| Relation | `relation` | `belief_support.relation` ∈ `SUPPORTS \| CONTRADICTS \| QUALIFIES` |
| Source kind | `source_kind` | `belief_support.source_kind` ∈ `EVIDENCE \| CLAIM \| BELIEF_VERSION \| DERIVATION` |
| Source id (chip) | `source_id` | `evidence_items.id` or `claims.id` |
| Weight | `weight` | `belief_support.weight` |
| Reason code | `reason_code` | `belief_support.reason_code` |
| Excerpt | `source.exact_text` / `source.normalized_text` | `evidence_items.exact_text` (the user's own document; see §10.4) |
| Source authority | `source.source_authority` | `evidence_items.source_authority` |
| Artifact origin | `source.artifact.*` | `source_artifacts.source_type`, `.sender`, `.subject`, `.received_at` |

`CONTRADICTS` edges render in the same table, styled distinctly, never in a separate "objections" box. The point of grounding is that supporting and contradicting evidence live in one ordered structure.

**B3 — Lineage.** One row per `belief_versions` entry from `state-proof.beliefs[].lineage[]`, oldest first: `version_no`, `value_json`, `epistemic_status`, `recorded_at`, `superseded_at`, `superseded_by_version_no`, `supersession_reason_codes`, `kernel_decision_id`, `grounding_count`.

`supersession_reason_codes` is read from the `kernel_decisions.reason_codes` of the decision that created version *n+1* (`specs/15_API_SPEC.md` §8.11.2) — there is no supersession-reason column on `belief_versions`, and the UI must not invent one.

### 3.2 Conflicts, derivations, transitions, dependent actions

Rendered from the corresponding `state-proof` blocks, each a real row:

| Block | Table |
|---|---|
| `conflicts[]` | `conflicts` — `conflict_type`, `status`, `severity`, `requires_human`, `left`, `right`, `canonical_belief_version_id` |
| `derivations[]` | computed by `provenance_domain.DETERMINISTIC_DERIVATIONS`, flagged `grounding_exempt: true` |
| `state_transitions[]` | `state_transitions` — `case_revision`, `transition_type`, `from_state`, `to_state`, `reason_code`, `kernel_decision_id`, `trace_id` |
| `actions_relying_on_this_state[]` | `action_intents` — `basis_case_revision`, `supporting_belief_versions[]`, `still_current` |

### 3.3 Retraction visibility

The `include_retracted` toggle maps to `?include_retracted=true`. Default off.

- Off: `excluded.retraction_filter_applied` renders as a chip reading **"retraction filter applied — 2 rows excluded"** using `excluded.retracted_evidence_count`. The count comes from the API; it is never computed client-side.
- On: retracted sources appear with a red `RETRACTED` / `SUPERSEDED` / `QUARANTINED` badge, `retracted_at`, `retraction_reason_code`, and `retracted_by_evidence_id`. They remain excluded from `grounded` and from `belief_confidence`, and the UI states that.

This is the visible face of `CANONICAL_DECISIONS.md` → *Historical visibility* and of `00_PRODUCT.md` R4. The seed carries three retraction fixtures on purpose, one of which (`sid('evidence','isp-wrong-term-date')`) embeds *closer* to the June invoice than the correct evidence does — so a judge toggling this switch is looking at the exact row that would have broken the demo if the filter were missing.

---

## 4. Panel C — Memory Trace

The end-to-end record of how one artifact changed the system. Source: `GET /v1/traces/{trace_id}` (`specs/15_API_SPEC.md` §8.28), with `GET /v1/cases/{case_id}/memory-trace` (§8.29) supplying the trace list for the selector.

### 4.1 Required nodes and their mapping to the closed node-type vocabulary

`specs/15_API_SPEC.md` §8.28 fixes the node `type` vocabulary: `API_REQUEST`, `ARTIFACT_PARSE`, `EMBEDDING`, `AGENT_RUN`, `MODEL_CALL`, `MCP_TOOL_CALL`, `RETRIEVAL`, `PROPOSAL`, `KERNEL_DECISION`, `DB_TRANSACTION`, `OUTBOX_EVENT`, `EVENT_CONSUMER`, `TRIGGER_EVALUATION`, `ACTION_INTENT`, `ACTION_APPROVAL`, `ACTION_EXECUTION`. `status` ∈ `OK | FAILED | RETRIED | SKIPPED | PENDING`.

Judge Mode requires thirteen things to be visible. Twelve map onto the existing vocabulary. One — *canonical changes* — requires **one additive enum member**, `CANONICAL_CHANGE`. Adding an enum member is explicitly non-breaking under `specs/15_API_SPEC.md` §16.2 ("New enum members are **not** breaking"), and §16.1's `spec_lint` requires the member to be added to §8.28's list in the same change. No other new member is introduced.

| # | Required visibility | Node `type` | Backing rows (authoritative) |
|---|---|---|---|
| 1 | Artifact registered | `API_REQUEST` | `source_artifacts` (`id`, `source_type`, `content_sha256`, `mime_type`, `size_bytes`, `received_at`) |
| 2a | Parse result | `ARTIFACT_PARSE` | `source_artifacts.parser_status`, `.parser_version`, `.parser_metadata` |
| 2b | Extraction result | `MODEL_CALL` (Tier E) | `agent_runs.model_calls[]` entry + the `evidence_items` ids it produced |
| 2c | Embeddings | `EMBEDDING` | `evidence_items.embedding_version`, row count |
| 3 | Agent interpretation | `AGENT_RUN` (graph `ingestion`) | `agent_runs` (`id`, `graph_name`, `graph_version`, `model_route`, `memory_mode`, `status`) |
| 4a | MCP reads | `MCP_TOOL_CALL` | `agent_runs.tool_calls[]` (see §6.4) |
| 4b | Retrieval candidates | `RETRIEVAL` | `agent_runs.retrieval_candidate_count` + the `RetrievalContext` telemetry of `specs/13_RETRIEVAL_SPEC.md` §11.6 |
| 5 | Resolver decision, if invoked | `AGENT_RUN` (graph `resolver`) + `MODEL_CALL` (Tier R) | `agent_runs`; rendered with `status: "SKIPPED"` and `attributes.skip_reason` when the router did not escalate |
| 6 | Memory proposal | `PROPOSAL` | `memory_proposals` (`id`, `proposal_type`, `payload_sha256`, `model_id`, `prompt_version`, `status`) |
| 7 | Kernel validation result | `KERNEL_DECISION` | `kernel_decisions` (`id`, `decision`, `reason_codes`, `case_revision_before/after`, `retry_count`, `committed_at`) |
| 8 | Transaction id and retry count | `DB_TRANSACTION` | `kernel_decisions.id` as transaction identity, `.retry_count`, `.committed_at` (see §4.5) |
| 9 | Canonical changes | `CANONICAL_CHANGE` *(additive)* | `state_transitions` (one node per row) plus the created-row ids from `claims`, `belief_versions`, `belief_support`, `conflicts`, `commitments`, `fulfillments` |
| 10 | Outbox event | `OUTBOX_EVENT` | `outbox_events` (`id`, `event_type`, `aggregate_version`, `status`, `attempt_count`, `dispatched_at`) |
| 10b | Event consumption | `EVENT_CONSUMER` | `processed_events` (`consumer_name`, `event_id`, `processed_at`) |
| 11 | Advocate action intent | `ACTION_INTENT` | `action_intents` (`id`, `action_type`, `status`, `basis_case_revision`, `draft_sha256`) |
| 12 | Approval | `ACTION_APPROVAL` | `action_intents.approved_at`, `.approval_draft_sha256`, `.basis_case_revision` |
| 13 | Execution outcome | `ACTION_EXECUTION` | `action_executions` (`id`, `attempt_no`, `provider`, `provider_correlation_id`, `revalidated_case_revision`, `status`) |
| — | Prospective wake | `TRIGGER_EVALUATION` | `prospective_triggers` (`id`, `trigger_type`, `state`, `last_result`, `last_reason_code`, `basis_case_revision`) |

`prospective_triggers.trigger_type` values are the four frozen in `CANONICAL_DECISIONS.md`: `COMMITMENT_DEADLINE`, `RESPONSE_DEADLINE`, `CONFLICT_TIMEOUT`, `WARRANTY_WINDOW`. `last_result` values are `FIRED`, `NO_OP`, `DISARMED`, `EXPIRED`, `ERROR` plus one closed-set reason code. The UI renders those strings verbatim; it has no aliases.

### 4.2 Edges

Edges are causal, not chronological. The rendering is a left-to-right layered DAG; the layer index is derived from topological depth, never from a hard-coded x-coordinate.

```text
API_REQUEST ──▶ ARTIFACT_PARSE ──▶ AGENT_RUN(ingestion)
                                        ├──▶ MODEL_CALL(Tier E, extract)
                                        │        └──▶ EMBEDDING
                                        ├──▶ MCP_TOOL_CALL × n
                                        ├──▶ RETRIEVAL
                                        └──▶ AGENT_RUN(resolver)?  ──▶ MODEL_CALL(Tier R)
                                                                          │
                                                          PROPOSAL ◀──────┘
                                                              │
                                                     KERNEL_DECISION
                                                              │
                                                      DB_TRANSACTION
                                                        │        │
                            CANONICAL_CHANGE × n ◀──────┘        └──▶ OUTBOX_EVENT × n
                                                                          │
                                                                   EVENT_CONSUMER
                                                                          │
                                                              AGENT_RUN(advocate)
                                                                          │
                                                                  ACTION_INTENT
                                                                          │
                                                                 ACTION_APPROVAL
                                                                          │
                                                                ACTION_EXECUTION
```

Edge rules:

1. `edges[]` in the API response is the authority. The client performs layout only. A DOM edge with no corresponding `{from, to}` pair is a rendering bug and is caught by `G12.2`.
2. `parent_id` on a node expresses containment (a `MODEL_CALL` inside an `AGENT_RUN`); `edges[]` expresses causation. They are different and both are rendered — containment as a lane, causation as an arrow.
3. A node with `status: "SKIPPED"` still appears, greyed, with its `skip_reason`. Absence is never used to communicate "did not happen", because absence is indistinguishable from "was not recorded".
4. A node with `status: "FAILED"` or a `MCP_TOOL_CALL` with `denied: true` renders in red and is **never** filtered out (`quality/23_PHASE_GATES.md` `G11.5`).

### 4.3 The `boundary` block

`specs/15_API_SPEC.md` §8.28 returns:

```json
"boundary": {
  "deterministic_node_ids": ["n1","n2","n5","n6","n7","n9","n10","n11","n12","n14","n15","n16"],
  "model_node_ids": ["n4","n8","n13"],
  "note": "Model nodes propose. Deterministic nodes decide, commit, and act."
}
```

The DAG is drawn in two horizontal lanes: **model lane** (top, tinted) and **deterministic lane** (bottom). The lane assignment comes from `boundary`, not from the node type — so if the server ever classifies a node differently, the picture changes rather than the picture lying. The lane divider carries the fixed caption: *"LLM agents emit typed MemoryProposals. The deterministic Memory Kernel is the only canonical writer."* (`00_PRODUCT.md` §0, Kernel rule.)

This single visual is the primary evidence for the Agentic Memory Design criterion. Every arrow that crosses from the model lane to the deterministic lane terminates at `PROPOSAL` — there is no other crossing, and if one appears, the architecture has been violated.

### 4.4 Correlation: `node.refs`

Every node must expose a real identifier a judge can correlate against the database. The trace response therefore carries, on each node, an additive optional array `refs` (additive optional response fields are non-breaking, `specs/15_API_SPEC.md` §16.2):

```ts
// apps/web/src/app/(judge)/_lib/trace.ts
export type TraceRef = {
  /** The canonical table the id lives in. Must be one of the 26 tables. */
  table:
    | "source_artifacts" | "evidence_items" | "claims" | "beliefs" | "belief_versions"
    | "belief_support" | "conflicts" | "commitments" | "fulfillments" | "cases"
    | "state_transitions" | "memory_proposals" | "kernel_decisions"
    | "prospective_triggers" | "action_intents" | "action_executions"
    | "outbox_events" | "processed_events" | "agent_runs";
  /** The column the value matches. Almost always "id"; "event_id" for processed_events. */
  column: string;
  /** The literal value, lowercase hyphenated UUID or a stable string key. */
  value: string;
  /** Row count this ref resolves to. Always 1 for an id ref; n for a set ref. */
  cardinality: number;
};
```

Rendering: each ref becomes an `IdChip` — monospace, truncated to first 8 and last 4 characters, click-to-copy full value, and a "verify" affordance that reveals the exact read-only SQL:

```sql
-- rendered verbatim by IdChip for table=kernel_decisions column=id
SELECT id, decision, reason_codes, case_revision_before, case_revision_after,
       retry_count, committed_at, trace_id
FROM kernel_decisions
WHERE id = '018f8b90-0000-7000-8000-000000000002';
```

The SQL is a **display string** produced from `{table, column, value}` by a pure function with a table allowlist. Judge Mode never executes it and the browser never holds a database credential. A judge with `ccloud`/`cockroach sql` access — or the CockroachDB Cloud Managed MCP Server in read-only mode — runs it themselves. That is the whole point: the correlation is checkable by someone who does not trust us.

Minimum `refs` per node type (a node lacking its minimum is a `G12.2` failure):

| Node type | Minimum refs |
|---|---|
| `API_REQUEST` | `source_artifacts.id` |
| `ARTIFACT_PARSE` | `source_artifacts.id` |
| `MODEL_CALL` | `agent_runs.id` (+ `evidence_items.id` set for the extraction node) |
| `EMBEDDING` | `evidence_items.id` set |
| `AGENT_RUN` | `agent_runs.id` |
| `MCP_TOOL_CALL` | `agent_runs.id` |
| `RETRIEVAL` | `agent_runs.id` |
| `PROPOSAL` | `memory_proposals.id` |
| `KERNEL_DECISION` | `kernel_decisions.id` |
| `DB_TRANSACTION` | `kernel_decisions.id` |
| `CANONICAL_CHANGE` | `state_transitions.id` + the created row id (`claims.id`, `belief_versions.id`, `conflicts.id`, …) |
| `OUTBOX_EVENT` | `outbox_events.id` |
| `EVENT_CONSUMER` | `processed_events.event_id` |
| `TRIGGER_EVALUATION` | `prospective_triggers.id` |
| `ACTION_INTENT` | `action_intents.id` |
| `ACTION_APPROVAL` | `action_intents.id` |
| `ACTION_EXECUTION` | `action_executions.id` |

### 4.5 Transaction identity, stated honestly

`DB_TRANSACTION.attributes.transaction_id` is **`kernel_decisions.id`**, not CockroachDB's internal transaction UUID.

The reasoning, which the UI displays in the node's info popover: one `kernel_decisions` row corresponds to exactly one `SERIALIZABLE` transaction attempt sequence, `retry_count` records how many `SQLSTATE 40001` retries that sequence consumed (bounded at 5 by `ck_kernel_decisions_retry`), and `committed_at` is non-null only for `ACCEPTED` and `ACCEPTED_WITH_CONFLICT` (`ck_kernel_decisions_commit_ts`). Capturing CockroachDB's internal transaction id would require `crdb_internal` reads, which `pv_kernel_writer` is not granted and which this build does not add. The label on screen reads **"transaction identity (kernel decision)"** — not "CockroachDB transaction id" — because those are different things and conflating them would be the kind of small lie this panel exists to prevent.

### 4.6 The full trace response, hero flow

Illustrative values; every id below is produced at runtime by the seed's `uuid5` minting or by UUIDv7 at request time. No literal below appears in frontend source (`G12.3`).

```json
{
  "trace_id": "018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90",
  "started_at": "2026-09-18T14:05:11.001Z",
  "finished_at": "2026-09-18T14:12:04.900Z",
  "duration_ms": 413899,
  "status": "COMPLETED",
  "case_ids": ["018f8a10-4c22-7f31-9b7d-2ac1e5f09b41"],
  "fixture_mode": false,
  "nodes": [
    { "id": "n1", "type": "API_REQUEST", "status": "OK",
      "started_at": "2026-09-18T14:05:11.001Z", "duration_ms": 61,
      "summary": "POST /v1/artifacts/{artifact_id}/complete",
      "attributes": { "artifact_id": "018f9e80-0000-7000-8000-000000000001",
                      "source_type": "UPLOAD_EML", "mime_type": "message/rfc822",
                      "size_bytes": 18422, "content_sha256_prefix": "7d2fc19a",
                      "http_status": 202, "idempotency_replayed": false },
      "refs": [{ "table": "source_artifacts", "column": "id",
                 "value": "018f9e80-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n2", "type": "ARTIFACT_PARSE", "status": "OK", "parent_id": null,
      "started_at": "2026-09-18T14:05:11.140Z", "duration_ms": 812,
      "summary": "message/rfc822 parsed, 1 text part, 0 attachments",
      "attributes": { "parser_version": "eml-text-1", "parser_status": "PARSED",
                      "used_textract": false, "quoted_history_blocks": 1 },
      "refs": [{ "table": "source_artifacts", "column": "id",
                 "value": "018f9e80-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n3", "type": "AGENT_RUN", "status": "OK",
      "started_at": "2026-09-18T14:05:12.010Z", "duration_ms": 9420,
      "summary": "ingestion v1.3.0 — memory ON",
      "attributes": { "agent_run_id": "018f9e90-0000-7000-8000-000000000001",
                      "graph_name": "ingestion", "graph_version": "1.3.0",
                      "memory_mode": "ON", "is_counterfactual": false },
      "refs": [{ "table": "agent_runs", "column": "id",
                 "value": "018f9e90-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n4", "type": "MODEL_CALL", "status": "OK", "parent_id": "n3",
      "started_at": "2026-09-18T14:05:12.220Z", "duration_ms": 2110,
      "summary": "extract_structured_evidence (Tier E) — 3 evidence items",
      "attributes": { "model_id": "anthropic.claude-haiku-4-5", "prompt_version": "pv-extract-1.1.0",
                      "input_tokens": 3184, "output_tokens": 742, "repair_attempts": 0,
                      "evidence_count": 3, "fixture_mode": false },
      "refs": [
        { "table": "agent_runs", "column": "id",
          "value": "018f9e90-0000-7000-8000-000000000001", "cardinality": 1 },
        { "table": "evidence_items", "column": "id",
          "value": "018f8aa0-0000-7000-8000-000000000021", "cardinality": 1 }] },

    { "id": "n5", "type": "EMBEDDING", "status": "OK", "parent_id": "n3",
      "started_at": "2026-09-18T14:05:14.400Z", "duration_ms": 310,
      "summary": "3 evidence embeddings generated, 0 reused from cache",
      "attributes": { "model_id": "amazon.titan-embed-text-v2:0", "dimensions": 1024,
                      "distance": "cosine", "embedding_version": "v1" },
      "refs": [{ "table": "evidence_items", "column": "id",
                 "value": "018f8aa0-0000-7000-8000-000000000021", "cardinality": 3 }] },

    { "id": "n6", "type": "MCP_TOOL_CALL", "status": "OK", "parent_id": "n3",
      "started_at": "2026-09-18T14:05:14.900Z", "duration_ms": 44,
      "summary": "CockroachDB Cloud Managed MCP → agent_case_context_v1",
      "attributes": { "sequence": 1, "mcp_server": "cockroachdb-cloud-managed-mcp",
                      "tool_name": "query_agent_case_context",
                      "view_name": "agent_case_context_v1",
                      "sql_role": "pv_agent_reader", "access_mode": "READ_ONLY",
                      "filter_summary": "tenant_id = <run tenant>; user_id = <run user>; case_id = <candidate>",
                      "rows_returned": 1, "denied": false },
      "refs": [{ "table": "agent_runs", "column": "id",
                 "value": "018f9e90-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n7", "type": "MCP_TOOL_CALL", "status": "OK", "parent_id": "n3",
      "started_at": "2026-09-18T14:05:14.960Z", "duration_ms": 128,
      "summary": "CockroachDB Cloud Managed MCP → agent_evidence_retrieval_v1 (vector)",
      "attributes": { "sequence": 2, "mcp_server": "cockroachdb-cloud-managed-mcp",
                      "tool_name": "query_agent_evidence_search",
                      "view_name": "agent_evidence_retrieval_v1",
                      "sql_role": "pv_agent_reader", "access_mode": "READ_ONLY",
                      "filter_summary": "tenant_id = <run tenant>; user_id = <run user>; retraction_status = 'ACTIVE'; top_k = 20",
                      "vector_index": "evidence_embedding_ann_idx",
                      "beam_size": 8, "rows_returned": 20,
                      "retraction_filter_applied": true, "retracted_rows_excluded": 2,
                      "denied": false },
      "refs": [{ "table": "agent_runs", "column": "id",
                 "value": "018f9e90-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n8", "type": "MCP_TOOL_CALL", "status": "OK", "parent_id": "n3",
      "started_at": "2026-09-18T14:05:15.100Z", "duration_ms": 31,
      "summary": "CockroachDB Cloud Managed MCP → agent_active_beliefs_v1",
      "attributes": { "sequence": 3, "mcp_server": "cockroachdb-cloud-managed-mcp",
                      "tool_name": "query_agent_active_beliefs",
                      "view_name": "agent_active_beliefs_v1",
                      "sql_role": "pv_agent_reader", "access_mode": "READ_ONLY",
                      "filter_summary": "tenant_id = <run tenant>; user_id = <run user>; case_id = <resolved case>",
                      "rows_returned": 6, "denied": false },
      "refs": [{ "table": "agent_runs", "column": "id",
                 "value": "018f9e90-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n9", "type": "RETRIEVAL", "status": "OK", "parent_id": "n3",
      "started_at": "2026-09-18T14:05:15.180Z", "duration_ms": 190,
      "summary": "16,035 user-scoped vectors → 20 ANN candidates → 7 after rerank → 1 exact identifier match",
      "attributes": { "corpus_size_user_scoped": 16035, "vector_candidates": 20,
                      "after_rerank": 7, "exact_identifier_hits": 1,
                      "retraction_filtered": 2, "cross_user_results": 0,
                      "identity_status": "RESOLVED", "beam_size": 8,
                      "embedding_version": "v1" },
      "refs": [{ "table": "agent_runs", "column": "id",
                 "value": "018f9e90-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n10", "type": "AGENT_RUN", "status": "OK",
      "started_at": "2026-09-18T14:05:15.400Z", "duration_ms": 5900,
      "summary": "resolver v1.1.0 — contradiction characterisation",
      "attributes": { "agent_run_id": "018f9e95-0000-7000-8000-000000000001",
                      "graph_name": "resolver", "graph_version": "1.1.0",
                      "memory_mode": "ON", "escalation_reason": "MUTUAL_EXCLUSION_SUSPECTED" },
      "refs": [{ "table": "agent_runs", "column": "id",
                 "value": "018f9e95-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n11", "type": "MODEL_CALL", "status": "OK", "parent_id": "n10",
      "started_at": "2026-09-18T14:05:15.420Z", "duration_ms": 5820,
      "summary": "strong_resolution (Tier R)",
      "attributes": { "model_id": "anthropic.claude-opus-5", "prompt_version": "pv-resolve-1.1.0",
                      "input_tokens": 5210, "output_tokens": 1104,
                      "requires_human_review": true, "fixture_mode": false },
      "refs": [{ "table": "agent_runs", "column": "id",
                 "value": "018f9e95-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n12", "type": "PROPOSAL", "status": "OK",
      "started_at": "2026-09-18T14:05:21.400Z", "duration_ms": 40,
      "summary": "MemoryProposal submitted — 1 claim, 1 conflict hint, 0 belief mutations",
      "attributes": { "proposal_id": "018f9fa0-0000-7000-8000-000000000001",
                      "proposal_type": "INGESTION_INTERPRETATION",
                      "payload_sha256_prefix": "3c81ba09",
                      "model_id": "anthropic.claude-opus-5", "prompt_version": "pv-resolve-1.1.0",
                      "status": "ACCEPTED_WITH_CONFLICT" },
      "refs": [{ "table": "memory_proposals", "column": "id",
                 "value": "018f9fa0-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n13", "type": "KERNEL_DECISION", "status": "OK",
      "started_at": "2026-09-18T14:05:21.460Z", "duration_ms": 178,
      "summary": "ACCEPTED_WITH_CONFLICT — case revision 12 → 13",
      "attributes": { "kernel_decision_id": "018f8b90-0000-7000-8000-000000000002",
                      "decision": "ACCEPTED_WITH_CONFLICT",
                      "case_revision_before": 12, "case_revision_after": 13,
                      "retry_count": 0,
                      "reason_codes": ["MUTUAL_EXCLUSION_DETECTED", "CASE_REOPEN_QUALIFIED",
                                       "COUNTERPARTY_CLAIM_NOT_ADMITTED_AS_FACT"] },
      "refs": [{ "table": "kernel_decisions", "column": "id",
                 "value": "018f8b90-0000-7000-8000-000000000002", "cardinality": 1 }] },

    { "id": "n14", "type": "DB_TRANSACTION", "status": "OK", "parent_id": "n13",
      "started_at": "2026-09-18T14:05:21.470Z", "duration_ms": 141,
      "summary": "SERIALIZABLE commit — 8 rows written, 0 retries",
      "attributes": { "isolation": "SERIALIZABLE",
                      "transaction_identity_kind": "KERNEL_DECISION",
                      "transaction_id": "018f8b90-0000-7000-8000-000000000002",
                      "retry_count": 0, "sqlstate_40001_retries": 0, "rows_written": 8,
                      "committed_at": "2026-09-18T14:05:21.611Z" },
      "refs": [{ "table": "kernel_decisions", "column": "id",
                 "value": "018f8b90-0000-7000-8000-000000000002", "cardinality": 1 }] },

    { "id": "n15", "type": "CANONICAL_CHANGE", "status": "OK", "parent_id": "n14",
      "started_at": "2026-09-18T14:05:21.611Z", "duration_ms": 0,
      "summary": "claims +1 (COUNTERPARTY_CLAIM)",
      "attributes": { "change_kind": "CLAIM_RECORDED", "claim_kind": "COUNTERPARTY_CLAIM",
                      "predicate": "service_active_during", "count": 1 },
      "refs": [{ "table": "claims", "column": "id",
                 "value": "018f8ab0-0000-7000-8000-000000000011", "cardinality": 1 }] },

    { "id": "n16", "type": "CANONICAL_CHANGE", "status": "OK", "parent_id": "n14",
      "started_at": "2026-09-18T14:05:21.611Z", "duration_ms": 0,
      "summary": "belief_versions +1 — balance_owed v2, DISPUTED (value unchanged)",
      "attributes": { "change_kind": "BELIEF_VERSIONED", "predicate": "balance_owed",
                      "version_no": 2, "epistemic_status": "DISPUTED",
                      "value_changed": false, "support_edges": 3 },
      "refs": [
        { "table": "belief_versions", "column": "id",
          "value": "018f8b22-0000-7000-8000-000000000002", "cardinality": 1 },
        { "table": "belief_support", "column": "belief_version_id",
          "value": "018f8b22-0000-7000-8000-000000000002", "cardinality": 3 }] },

    { "id": "n17", "type": "CANONICAL_CHANGE", "status": "OK", "parent_id": "n14",
      "started_at": "2026-09-18T14:05:21.611Z", "duration_ms": 0,
      "summary": "conflicts +1 — VALUE_CONFLICT, NEEDS_HUMAN, HIGH, requires_human",
      "attributes": { "change_kind": "CONFLICT_OPENED", "conflict_type": "VALUE_CONFLICT",
                      "severity": "HIGH", "requires_human": true },
      "refs": [{ "table": "conflicts", "column": "id",
                 "value": "018f8d40-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n18", "type": "CANONICAL_CHANGE", "status": "OK", "parent_id": "n14",
      "started_at": "2026-09-18T14:05:21.611Z", "duration_ms": 0,
      "summary": "cases RESOLVED → REOPENED, revision 12 → 13, attention NONE → URGENT",
      "attributes": { "change_kind": "CASE_STATUS", "from_state": "RESOLVED",
                      "to_state": "REOPENED", "case_revision": 13,
                      "reason_code": "COUNTERPARTY_CLAIM_CONTRADICTS_CANONICAL",
                      "attention_level": "URGENT" },
      "refs": [
        { "table": "state_transitions", "column": "id",
          "value": "018f8e10-0000-7000-8000-000000000005", "cardinality": 1 },
        { "table": "cases", "column": "id",
          "value": "018f8a10-4c22-7f31-9b7d-2ac1e5f09b41", "cardinality": 1 }] },

    { "id": "n19", "type": "OUTBOX_EVENT", "status": "OK", "parent_id": "n14",
      "started_at": "2026-09-18T14:05:22.900Z", "duration_ms": 62,
      "summary": "case.reopened.v1 dispatched to EventBridge",
      "attributes": { "event_id": "018f9fb0-0000-7000-8000-000000000001",
                      "event_type": "case.reopened.v1", "aggregate_type": "CASE",
                      "aggregate_version": 13, "attempt_count": 1, "status": "DISPATCHED" },
      "refs": [{ "table": "outbox_events", "column": "id",
                 "value": "018f9fb0-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n20", "type": "EVENT_CONSUMER", "status": "OK", "parent_id": "n19",
      "started_at": "2026-09-18T14:05:23.020Z", "duration_ms": 18,
      "summary": "advocate_dispatch — PROCESSED",
      "attributes": { "consumer_name": "advocate_dispatch", "result": "PROCESSED",
                      "event_id": "018f9fb0-0000-7000-8000-000000000001" },
      "refs": [{ "table": "processed_events", "column": "event_id",
                 "value": "018f9fb0-0000-7000-8000-000000000001", "cardinality": 1 }] },

    { "id": "n21", "type": "AGENT_RUN", "status": "OK",
      "started_at": "2026-09-18T14:05:24.200Z", "duration_ms": 7300,
      "summary": "advocate v1.2.0 — grounded dispute draft",
      "attributes": { "agent_run_id": "018f9ec0-0000-7000-8000-000000000002",
                      "graph_name": "advocate", "graph_version": "1.2.0",
                      "memory_mode": "ON", "claims_validated": 2, "claims_unsupported": 0 },
      "refs": [{ "table": "agent_runs", "column": "id",
                 "value": "018f9ec0-0000-7000-8000-000000000002", "cardinality": 1 }] },

    { "id": "n22", "type": "ACTION_INTENT", "status": "OK",
      "started_at": "2026-09-18T14:08:41.900Z", "duration_ms": 30,
      "summary": "OUTBOUND_EMAIL_DISPUTE proposed, basis revision 13",
      "attributes": { "action_intent_id": "018f9c2f-1111-7abc-8def-000000000001",
                      "action_type": "OUTBOUND_EMAIL_DISPUTE", "status": "NEEDS_REVIEW",
                      "basis_case_revision": 13, "draft_sha256_prefix": "9a1f2b3c" },
      "refs": [{ "table": "action_intents", "column": "id",
                 "value": "018f9c2f-1111-7abc-8def-000000000001", "cardinality": 1 }] },

    { "id": "n23", "type": "ACTION_APPROVAL", "status": "OK", "parent_id": "n22",
      "started_at": "2026-09-18T14:12:03.771Z", "duration_ms": 88,
      "summary": "Human approved; draft hash frozen; case revision 13 → 14",
      "attributes": { "approved_case_revision": 13, "case_revision_after": 14,
                      "approval_draft_sha256_prefix": "9a1f2b3c" },
      "refs": [{ "table": "action_intents", "column": "id",
                 "value": "018f9c2f-1111-7abc-8def-000000000001", "cardinality": 1 }] },

    { "id": "n24", "type": "ACTION_EXECUTION", "status": "OK", "parent_id": "n23",
      "started_at": "2026-09-18T14:12:04.500Z", "duration_ms": 400,
      "summary": "Revalidated revision 14 and draft hash; sent",
      "attributes": { "attempt_no": 1, "provider": "SES",
                      "provider_correlation_id": "0100018f9f2a…",
                      "revalidated_case_revision": 14, "status": "SUCCEEDED",
                      "revalidation": "PASSED" },
      "refs": [{ "table": "action_executions", "column": "id",
                 "value": "018fa0c0-0000-7000-8000-000000000001", "cardinality": 1 }] }
  ],
  "edges": [
    {"from":"n1","to":"n2"}, {"from":"n2","to":"n3"},
    {"from":"n3","to":"n4"}, {"from":"n4","to":"n5"},
    {"from":"n5","to":"n6"}, {"from":"n6","to":"n7"}, {"from":"n7","to":"n8"},
    {"from":"n8","to":"n9"}, {"from":"n9","to":"n10"}, {"from":"n10","to":"n11"},
    {"from":"n11","to":"n12"}, {"from":"n12","to":"n13"}, {"from":"n13","to":"n14"},
    {"from":"n14","to":"n15"}, {"from":"n14","to":"n16"}, {"from":"n14","to":"n17"},
    {"from":"n14","to":"n18"}, {"from":"n14","to":"n19"}, {"from":"n19","to":"n20"},
    {"from":"n20","to":"n21"}, {"from":"n21","to":"n22"}, {"from":"n22","to":"n23"},
    {"from":"n23","to":"n24"}
  ],
  "boundary": {
    "deterministic_node_ids": ["n1","n2","n5","n6","n7","n8","n9","n12","n13","n14",
                               "n15","n16","n17","n18","n19","n20","n22","n23","n24"],
    "model_node_ids": ["n4","n11","n21"],
    "note": "Model nodes propose. Deterministic nodes decide, commit, and act."
  }
}
```

### 4.7 Assembly on the server

The handler builds nodes with one query per source table, all filtered on `trace_id`, all under `pv_app_reader_writer`. The DDL provides the indexes for exactly this:

```sql
-- idx_agent_runs_trace
SELECT id, graph_name, graph_version, model_route, memory_mode, is_counterfactual,
       status, started_at, finished_at, retrieval_candidate_count, error_code
FROM agent_runs WHERE trace_id = $1 ORDER BY started_at;

-- idx_state_transitions_trace  ("show every canonical change this one artifact caused")
SELECT id, case_id, case_revision, transition_type, subject_kind, subject_id,
       from_state, to_state, reason_code, kernel_decision_id, recorded_at
FROM state_transitions WHERE trace_id = $1 ORDER BY recorded_at;

-- idx_outbox_events_trace
SELECT id, event_type, aggregate_type, aggregate_id, aggregate_version,
       status, attempt_count, occurred_at, dispatched_at
FROM outbox_events WHERE trace_id = $1 ORDER BY created_at;

SELECT id, decision, reason_codes, case_revision_before, case_revision_after,
       retry_count, committed_at, proposal_id, case_id
FROM kernel_decisions WHERE trace_id = $1 ORDER BY created_at;

SELECT id, proposal_type, payload_sha256, model_id, prompt_version, status,
       agent_run_id, created_at, decided_at, kernel_decision_id
FROM memory_proposals WHERE trace_id = $1 ORDER BY created_at;
```

If a query returns zero rows for a stage, the corresponding node is **absent**, not synthesised. An absent stage is visible as a gap in the DAG and is correct: the stage did not happen, or it did not record itself, and both are worth seeing.

### 4.8 Trace selection

The trace selector in the Panel C header is fed by `GET /v1/cases/{case_id}/memory-trace?limit=10&include_mcp=true`, which returns the traces that *materially changed this case*, newest first, each with `case_revision_before` / `case_revision_after`, the `kernel_decision` summary, `memory_operations[]`, `retrieval`, `mcp_tool_calls[]`, and `model_calls[]`. Selecting an item navigates to `/judge/{traceId}`; the deep link is shareable and is what a judge pastes into a follow-up question.

---

## 5. Panel D — systems status

Six live indicators, exactly as required by `05_RELIABILITY_EVAL_DEMO.md` §8, plus a clearly separated sponsor-tool strip. Everything is a value read from the currently selected trace or from the case; nothing is a static badge.

### 5.1 The six mandated indicators

| # | Indicator | Rendered value | Source |
|---|---|---|---|
| 1 | CockroachDB canonical commit | ✓ / ✗ + `committed_at` | `kernel_decisions.decision` ∈ `ACCEPTED \| ACCEPTED_WITH_CONFLICT` **and** `kernel_decisions.committed_at IS NOT NULL` |
| 2 | Vector retrieval candidate count | `20 candidates from 16,035 user-scoped vectors` | `RETRIEVAL` node `attributes.vector_candidates` and `.corpus_size_user_scoped`; the corpus figure is `agent_runs.retrieval_candidate_count`'s companion count computed at retrieval time, never a constant |
| 3 | Transaction revision / retry count | `revision 12 → 13 · 0 retries (SERIALIZABLE)` | `kernel_decisions.case_revision_before`, `.case_revision_after`, `.retry_count` |
| 4 | Outbox delivered | ✓ + `case.reopened.v1 · attempt 1` | `outbox_events.status = 'DISPATCHED'`, `.event_type`, `.attempt_count`, `.dispatched_at` |
| 5 | Model route used | `Tier E anthropic.claude-haiku-4-5 · Tier R anthropic.claude-opus-5` | `agent_runs.model_route` (JSONB) for every run in the trace |
| 6 | Action approval required | `Yes — human approval bound to revision 13 + draft sha256` | `action_intents.status`, `.basis_case_revision`, `.approval_draft_sha256` |

Indicator 1 renders ✗ in red when the decision is `NOOP_DUPLICATE`, `PENDING_IDENTITY`, `PENDING_HUMAN_REVIEW`, or any `REJECTED_*` value, with the decision string shown. Judge Mode does not hide unsuccessful commits; a rejected proposal that left canonical state untouched is a *feature* and is labelled as one.

### 5.2 Sponsor-tool strip

Separated by a rule and labelled "CockroachDB and AWS surface". Each row states what the tool did on *this* trace, so it cannot be read as a logo wall.

| Row | Rendered value | Source |
|---|---|---|
| Distributed vector index | `evidence_embedding_ann_idx · cosine · 1024 dims · beam_size 8 · user_id prefix` | `MCP_TOOL_CALL`/`RETRIEVAL` node attributes; index name from `CANONICAL_DECISIONS.md` |
| Embedding model | `amazon.titan-embed-text-v2:0 · embedding_version v1` | `EMBEDDING` node attributes |
| MCP server | `cockroachdb-cloud-managed-mcp · 3 calls · pv_agent_reader · READ_ONLY · 0 denied` | `MCP_TOOL_CALL` nodes (§6) |
| Agent-safe views reachable | the five `agent_*_v1` names | `MCP_TOOL_CALL.attributes.view_name` observed, plus the constant list mirrored from `CANONICAL_DECISIONS.md` (§6.5) |
| AWS services on this trace | `Bedrock · AgentCore Runtime · S3 · EventBridge · SES · Cognito · CloudWatch` | Presence of the corresponding node types and `action_executions.provider` |
| Retraction filter | `applied · 2 rows excluded` | `RETRIEVAL.attributes.retraction_filtered`, `state-proof.excluded` |

### 5.3 Health strip

A thin footer row with three counters, refreshed on each panel load:

- `outbox pending age` — from the CloudWatch metric backing the `provenance-outbox-pending-age` alarm; rendered as "n/a" if the metric is unavailable, never as `0`.
- `kernel 40001 retry rate` — `provenance` kernel-retry metric.
- `auth tenant mismatch` — `provenance.auth.tenant_mismatch`. In a correct system this is `0` and a non-zero value is either a bug or an attack (`specs/15_API_SPEC.md` §17.11). It is displayed precisely because it should never move.

An unavailable metric renders `n/a` with a tooltip naming the metric. Substituting a plausible number for a missing metric is a §11 violation.

---

## 6. MCP visibility — load-bearing, not decorative

### 6.1 What must be visible

`quality/23_PHASE_GATES.md` §17 (`G-11`) is explicit: "MCP is visible and load-bearing (canon item B): the Memory Trace renders those calls as first-class nodes, including denied ones in red. It is not hidden plumbing and it is not decorative — if MCP is disabled, the Interpreter loses its case-context read and the trace shows the degradation."

Judge Mode renders, for every tool call the agent made through the **CockroachDB Cloud Managed MCP Server**:

| Field | Meaning | Source |
|---|---|---|
| `sequence` | Call ordinal within the run | `agent_runs.tool_calls[].sequence` |
| `mcp_server` | `cockroachdb-cloud-managed-mcp` | `agent_runs.tool_calls[].mcp_server` |
| `tool_name` | The MCP tool invoked | `agent_runs.tool_calls[].tool_name` |
| `view_name` | Which agent-safe view it hit | `agent_runs.tool_calls[].view_name` |
| `sql_role` | `pv_agent_reader` | `agent_runs.tool_calls[].sql_role` |
| `access_mode` | `READ_ONLY` | `agent_runs.tool_calls[].access_mode` |
| `filter_summary` | Rendered predicate template, values elided | `agent_runs.tool_calls[].filter_summary` |
| `rows_returned` | Row count | `agent_runs.tool_calls[].rows_returned` |
| `duration_ms` | Latency | `agent_runs.tool_calls[].duration_ms` |
| `denied` | Whether the call was refused | `agent_runs.tool_calls[].denied` |
| `vector_index`, `beam_size` | Only on the retrieval view call | same |

### 6.2 Where it surfaces — three places, not one

1. **Inside the DAG.** Each call is a `MCP_TOOL_CALL` node parented to its `AGENT_RUN`, on the deterministic lane (the read is deterministic; the model chose *whether* to read, not *what scope* to read). This is the primary surface.
2. **In the Panel D sponsor-tool strip.** Aggregate: call count, distinct views touched, role, access mode, denied count.
3. **In `GET /v1/cases/{case_id}/memory-trace`.** The `mcp_tool_calls[]` array per trace item, which is what a judge sees when they scan the case's history rather than one trace.

`include_mcp=false` on §8.29 hides them; Judge Mode never sets it.

### 6.3 The five views, and the grant boundary

The MCP server's entire reachable surface is the five agent-safe views (`CANONICAL_DECISIONS.md`; `specs/10_DATABASE_DDL.md` §14):

`agent_case_context_v1`, `agent_active_beliefs_v1`, `agent_belief_lineage_v1`, `agent_evidence_retrieval_v1`, `agent_open_obligations_v1`

The panel displays, beside the view list, the fixed sentence:

> The permission boundary is the SQL grant, not the prompt. `pv_agent_reader` holds `SELECT` on these five views and on nothing else.

Clicking the sentence expands the two commands from `G11.2`, with their real recorded output pasted from the gate report:

```bash
cockroach sql --url "$PV_DB_AGENT" -e "SELECT id FROM evidence_items LIMIT 1;"
# ERROR: user pv_agent_reader has no SELECT privilege on relation evidence_items

cockroach sql --url "$PV_DB_AGENT" -e "SELECT * FROM agent_active_beliefs_v1 LIMIT 1;"
# 1 row
```

These strings are read from the gate artifact `ops/gate-reports/G-11.txt`, checked into the repository, and are labelled **"recorded at gate G-11"** with the commit sha. They are not presented as live. The live equivalent is the isolation probe in §9, which produces a fresh denial on demand.

### 6.4 Provenance of the tool-call record — the honest caveat

`agent_runs.tool_calls` is populated by the AgentCore tool wrapper via `POST /internal/v1/agent-runs/{agent_run_id}/complete` (`specs/15_API_SPEC.md` §9.9). The server rejects any entry with a key outside the allowlist (`422 VALIDATION_FAILED`), so returned rows and SQL text cannot be smuggled into the trace — but the record is still **caller-reported** (`specs/15_API_SPEC.md` §17.13).

Judge Mode states this on screen, in the MCP section's info popover, verbatim:

> These tool calls are reported by the agent runtime. They are an observability record, not tamper-proof provenance. What the agent *could* read is proven by the SQL grant on `pv_agent_reader`; proving what it *did* read would require CockroachDB audit logging, which this build does not enable.

Volunteering this is not a weakness. A judge who discovers it unaided concludes the trace may be theatre; a judge who reads it in our own UI concludes we know exactly where our evidence stops.

### 6.5 Degradation is visible

When `PV_MCP_ENABLED=false` (the `G11.7` condition), the Interpreter falls back to the control-plane retrieval endpoint and the trace renders a node with `status: "FAILED"` and `summary: "MCP UNAVAILABLE — degraded read path"`. Judge Mode shows it in red and Panel D's MCP row reads `unavailable — degraded`. Silent success under MCP failure would make MCP decorative by definition, and is a gate failure, not a UI preference.

### 6.6 Qualifying-tool disclosure

The panel footer names the three CockroachDB tools this build uses, which is the submission's tool-usage disclosure rendered in-product:

1. **CockroachDB Cloud** — canonical state, transactional outbox, and the distributed vector index. Remove it and there is no memory.
2. **CockroachDB Cloud Managed MCP Server** — the agent's governed read path over the five `agent_*_v1` views as `pv_agent_reader`, read-only. Remove it and the Interpreter degrades to the control-plane read path (`G11.7` proves the degradation is visible).
3. **`ccloud` CLI** — cluster provisioning and inspection during Phase 0 and Phase 2.

The distinction between the Cloud Managed MCP Server and the self-hosted `cockroachdb-mcp-server` is stated explicitly, because they are different products and only one of them is the qualifying tool.

---

## 7. The memory OFF / ON counterfactual

The single most persuasive twenty-five seconds in the video (`00_PRODUCT.md` §5, segment D) and the easiest thing in the build to accuse of being rigged (`00_PRODUCT.md` R2). The design answers the accusation before it is made.

### 7.1 Architecture

Both runs execute the **same LangGraph graph, same model, same prompt version, same graph version, same artifact**. One request flag removes memory:

```text
POST /v1/judge-mode/counterfactual        (Idempotency-Key required, judge_mode_enabled required)
  { "artifact_id": "…", "modes": ["MEMORY_OFF","MEMORY_ON"],
    "memory_on_strategy": "REPLAY_COMMITTED" }
```

**MEMORY_OFF path.** The control plane creates an `agent_runs` row with `memory_mode = 'OFF'`, `is_counterfactual = true` (the pair is enforced by `ck_agent_runs_counterfactual_consistent`), `graph_name = 'counterfactual'`, and `allowed_case_ids = []`. The graph then runs with:

1. structured identity retrieval skipped — no exact-identifier match against `relationships.external_account_ref`;
2. `agent_evidence_retrieval_v1` not consulted — no MCP retrieval call, no ANN query;
3. an empty `RetrievalContext` (`corpus_size_visible: 0`);
4. an empty State Proof;
5. **no `submit_memory_proposal` tool bound at all.**

Point 5 is a capability property, not a prompt instruction. Even a bug that reached `POST /internal/v1/memory/proposals` fails with `403 CAPABILITY_SCOPE_MISMATCH` because `allowed_case_ids` is empty, and the Kernel additionally rejects any proposal whose `memory_proposals.agent_run_id` resolves to a counterfactual run (`specs/10_DATABASE_DDL.md` §0, note 6).

**MEMORY_ON path.** Default `REPLAY_COMMITTED` reads the already-committed Kernel decision and Advocate draft for this artifact — the real production run, `graph_name = 'ingestion'`, `memory_mode = 'ON'`. `RERUN_SANDBOXED` re-executes the graph read-only and is judge-only, rate-limited to 10 requests per 60 minutes (`specs/15_API_SPEC.md` §14.1, bucket `counterfactual`), and never cached.

### 7.2 What must be identical, and how it is proved on screen

The panel renders a **parity block** above the two columns. It is computed server-side by comparing the two runs' recorded metadata and is an additive optional response field on `GET /v1/judge-mode/counterfactual/{counterfactual_id}`:

```json
"parity": {
  "artifact_id":        { "off": "018f9e80-…-0001", "on": "018f9e80-…-0001", "equal": true },
  "artifact_sha256":    { "off": "7d2f…c19a",       "on": "7d2f…c19a",       "equal": true },
  "model_id":           { "off": "anthropic.claude-opus-5",
                          "on":  "anthropic.claude-opus-5", "equal": true },
  "prompt_version":     { "off": "pv-draft-1.0.0",  "on": "pv-draft-1.0.0",  "equal": true },
  "graph_version":      { "off": "1.3.0",           "on": "1.3.0",           "equal": true },
  "decode_params_sha256": { "off": "b41c…", "on": "b41c…", "equal": true },
  "all_equal": true
}
```

`prompt_version` is equal because `specs/14_PROMPTS.md` §6.4 gives MEMORY OFF the **same** prompt asset, `pv-draft-1.0.0`, with the same `effort="high"` and the same `DraftAction` output schema, and strips memory by supplying an *empty* TRUSTED STRUCTURED CONTEXT block rather than a different prompt. `decode_params_sha256` is equal by the same construction. This is the frozen posture in `CANONICAL_DECISIONS.md`, *Counterfactual* — "the same artifact, model, prompt, and graph" — and it is what makes the block provable rather than decorative.

If any pair is unequal, `all_equal` is `false`, the panel renders a red banner reading **"PARITY FAILED — this comparison is not valid"**, and the two output columns are **not** displayed. A counterfactual that cannot prove parity is worse than no counterfactual, because it invites exactly the accusation it was built to defeat.

The only permitted differences are the four that constitute the experiment:

| Property | MEMORY_OFF | MEMORY_ON |
|---|---|---|
| `retrieval_enabled` | `false` | `true` |
| `canonical_memory_enabled` | `false` | `true` |
| `corpus_size_visible` | `0` | real user-scoped count |
| State Proof supplied to the graph | empty | full |

### 7.3 Presentation

Two columns, equal width, same typography, same font size. Deliberately symmetric: any visual asymmetry reads as a thumb on the scale.

```text
┌──────────────── PARITY: artifact ✓ model ✓ prompt ✓ graph ✓ decode ✓ ────────────────┐
├──────────────────────────────┬───────────────────────────────────────────────────────┤
│ MEMORY OFF                   │ MEMORY ON                                             │
│ retrieval disabled           │ retrieval enabled                                     │
│ canonical memory disabled    │ canonical memory enabled                              │
│ corpus visible: 0            │ corpus visible: 16,035                                │
│ anthropic.claude-opus-5      │ anthropic.claude-opus-5                               │
├──────────────────────────────┼───────────────────────────────────────────────────────┤
│ "Invoice for $186 due        │ "Contradicts your 15 May termination confirmation —   │
│  30 June."                   │  case reopened, dispute drafted."                     │
│                              │                                                       │
│ classification               │ classification                                        │
│   ROUTINE_INVOICE            │   COUNTERPARTY_CLAIM_CONTRADICTING_CANONICAL          │
│ case linked        —         │ case linked   Old ISP cancellation                    │
│ conflicts          0         │ conflicts     1  (VALUE_CONFLICT, NEEDS_HUMAN, HIGH)  │
│ action             NONE      │ action        OUTBOUND_EMAIL_DISPUTE                  │
│ evidence recalled  0 days    │ evidence recalled  118 days                           │
└──────────────────────────────┴───────────────────────────────────────────────────────┘
      agent_run_id 018fa0…0031        agent_run_id 018f9e90…0001   trace ↗
```

Beneath: the `delta` block verbatim (`conflicts_detected`, `cases_reopened`, `actions_recommended`, `evidence_recalled_days`, `verdict`), and the `safety` block rendered as four green checks:

- `memory_off_wrote_canonical_state: false`
- `memory_off_admitted_evidence: false`
- `memory_off_had_proposal_tool: false`
- `case_revision_changed_by_counterfactual: false`

Both columns carry an `IdChip` for their `agent_runs.id`, so a judge can verify both rows exist and inspect `memory_mode` themselves:

```sql
SELECT id, graph_name, graph_version, memory_mode, is_counterfactual,
       model_route, status, started_at, finished_at
FROM agent_runs
WHERE id IN ('<off_agent_run_id>', '<on_agent_run_id>');
```

### 7.4 The honesty rule

> **The MEMORY_OFF run is always a live execution against `anthropic.claude-opus-5`. It is never a stored string, never a cached response, and never a hand-written sentence.**

Enforcement, in four layers:

1. Every rendered MEMORY_OFF output must be accompanied by a resolvable `agent_runs.id` whose `started_at` is later than the counterfactual request's `created_at`. The frontend refuses to render an output column without one.
2. `MODEL_CALL` nodes carry `input_tokens`, `output_tokens`, and `duration_ms` from the actual Bedrock invocation. A zero-token MEMORY_OFF run is rendered as `FAILED`, never as an output.
3. `specs/15_API_SPEC.md` §17.9 forbids caching the counterfactual: "model output is not deterministic enough for a cache to represent a fresh counterfactual honestly."
4. Under `PV_AGENT_MODE=FIXTURE`, `MODEL_CALL.attributes.fixture_mode` is `true` on every node and the fixture banner is on. A fixture-mode counterfactual is *labelled* as replayed, in-place, per node — it is never silently presented as live.

`00_PRODUCT.md` R2's decision stands: the request-payload diff between the two runs is a **live Q&A artifact**, available in Judge Mode behind a "show request diff" disclosure, and is deliberately **not** part of the three-minute video.

---

## 8. Safe failure-injection probes

Two required probes, both genuinely executed, neither on the video's critical path (`05_RELIABILITY_EVAL_DEMO.md` §16: "Do not make the primary 30-second demo depend on failure injection").

### 8.1 Required API surface — declared, not assumed

`specs/15_API_SPEC.md` §8.0 does not currently define a probe endpoint. Judge Mode requires the following pair, which must be added to §8.0 and to `provenance_contracts` under `README.md` → *Change control* **before** Phase 12 begins. They are specified here in full so the change is mechanical.

| # | Method | Path | Auth | Idempotency-Key |
|---|---|---|---|---|
| 8.32 | POST | `/v1/judge-mode/probes` | human + `judge_mode_enabled` | required, scope `judge.probe` |
| 8.33 | GET | `/v1/judge-mode/probes/{probe_id}` | human + `judge_mode_enabled` | — |

Rate-limit bucket `judge_probe`, keyed on `user_id`, 10 requests per 60 minutes, `429 RATE_LIMITED` on exceed. Errors: `400 MISSING_IDEMPOTENCY_KEY`, `403 JUDGE_MODE_DISABLED`, `404 PROBE_NOT_FOUND`, `409 IDEMPOTENCY_CONFLICT`, `409 PROBE_TARGET_BUSY`, `503 UPSTREAM_UNAVAILABLE`.

```json
POST /v1/judge-mode/probes
{ "probe_type": "DUPLICATE_EVENT_DELIVERY" }
```

`probe_type` ∈ `DUPLICATE_EVENT_DELIVERY | CONCURRENT_PROPOSALS | CROSS_TENANT_RETRIEVAL`.

```json
202
{ "probe_id": "018fa300-0000-7000-8000-000000000001",
  "probe_type": "DUPLICATE_EVENT_DELIVERY",
  "status": "RUNNING",
  "poll_url": "/v1/judge-mode/probes/018fa300-0000-7000-8000-000000000001",
  "suggested_interval_ms": 750,
  "trace_id": "018fa2f0-9a41-7a13-b0e2-6d2b1c4f8a90" }
```

Every probe result carries `writes_canonical_state: boolean` and, when `true`, `restore_command`. The UI displays both **before** the judge can press the button.

### 8.2 Probe 1 — duplicate event delivery

**What it does.** Re-delivers an already-dispatched `case.reopened.v1` `DomainEvent` — byte-identical envelope, same `event_id` — to `POST /internal/v1/events/deliveries` with `consumer_name: "advocate_dispatch"`, exactly as EventBridge would on an at-least-once redelivery. There is no `Idempotency-Key` on that endpoint because `event_id` **is** the key and `processed_events` is the ledger (`specs/15_API_SPEC.md` §9.13, §12).

**What is real.** The second `INSERT INTO processed_events (consumer_name, event_id, …)` collides with `pk_processed_events` and the consumer returns `DUPLICATE_NOOP` with HTTP `200` — a duplicate is normal in an at-least-once system, not an error.

**`writes_canonical_state: false`.** One `processed_events` row already existed; no second one is created; no `agent_runs`, `kernel_decisions`, `cases`, or `outbox_events` row changes.

**Result shape:**

```json
{
  "probe_id": "018fa300-0000-7000-8000-000000000001",
  "probe_type": "DUPLICATE_EVENT_DELIVERY",
  "status": "COMPLETED",
  "writes_canonical_state": false,
  "event_id": "018f9fb0-0000-7000-8000-000000000001",
  "event_type": "case.reopened.v1",
  "consumer_name": "advocate_dispatch",
  "deliveries": [
    { "attempt": 1, "result": "PROCESSED",
      "processed_at": "2026-09-18T14:05:23.020Z",
      "effect": { "kind": "AGENT_RUN_STARTED",
                  "agent_run_id": "018f9ec0-0000-7000-8000-000000000002" } },
    { "attempt": 2, "result": "DUPLICATE_NOOP",
      "first_processed_at": "2026-09-18T14:05:23.020Z",
      "effect": null,
      "reason": "processed_events primary key (consumer_name, event_id) already present" }
  ],
  "invariant_checks": {
    "processed_events_rows_for_event": 1,
    "advocate_agent_runs_for_event": 1,
    "action_intents_for_case_delta": 0,
    "case_revision_before": 14,
    "case_revision_after": 14
  },
  "verify_sql": "SELECT consumer_name, event_id, processed_at FROM processed_events WHERE event_id = '018f9fb0-0000-7000-8000-000000000001';",
  "trace_id": "018fa2f0-9a41-7a13-b0e2-6d2b1c4f8a90"
}
```

**Rendering.** Two stacked delivery rows — first green `PROCESSED`, second amber `DUPLICATE_NOOP` — with the `invariant_checks` beneath as a four-line table. The `EVENT_CONSUMER` node in the DAG gains a sibling with `status: "SKIPPED"` and `attributes.result: "DUPLICATE_NOOP"`, so the DAG itself shows the second delivery arriving and doing nothing. The `verify_sql` is displayed and returns exactly one row.

### 8.3 Probe 2 — two concurrent proposals against one case

**What it does.** Submits two `MemoryProposal` objects concurrently against **one** case, from two independent agent runs, so the Kernel's `SERIALIZABLE` transaction encounters real contention and at least one attempt retries on `SQLSTATE 40001`.

**Target.** The movers damage case — the seed's case 5, commitment `sid('commitment','damage')`: committed `420.0000`, fulfilled `200.0000`, outstanding `220.0000`, status `PARTIAL`. Configurable via `PV_JUDGE_PROBE_CASE_ID`; the default is chosen because it already carries a partially-fulfilled monetary commitment, which is what makes an impossible intermediate state expressible.

- **Proposal A** admits a `FULFILLMENT_CLAIM` of `USD 50.0000` from a seeded bank-credit artifact.
- **Proposal B** admits a `COUNTERPARTY_CLAIM` asserting "reimbursement issued in full".

**What must be true afterwards** (the assertions of `specs/10_DATABASE_DDL.md` §19 test 10, executed live rather than in CI):

1. No state resembling `status = 'FULFILLED'` with `outstanding_amount > 0` exists — `ck_commitments_outstanding_blocks_fulfilled` forbids it at the storage layer.
2. `outstanding_amount = committed_amount − fulfilled_amount` holds — `ck_commitments_outstanding_identity`.
3. Both claims survive; neither is discarded.
4. At least one `kernel_decisions` row has `retry_count >= 1`.
5. `cases.revision` advanced by exactly the number of accepted commits — no more, no fewer.

**`writes_canonical_state: true`.** This probe permanently changes memory: `fulfilled_amount 200.0000 → 250.0000`, `outstanding_amount 220.0000 → 170.0000`, status stays `PARTIAL`, one new `conflicts` row for the counterparty's "in full" assertion, and the dashboard's context total falls from `2020.0000` to `1970.0000`. That is not a bug; it is what admitting real evidence does. The UI states it plainly before the judge presses the button, and displays the restore command:

```bash
python -m scripts.seed --profile hero --reset
```

**Sequencing rule.** The probe is disabled while a recording session flag is set and is never run during video segments A–G. It runs after the hero flow, in live Q&A. `05_RELIABILITY_EVAL_DEMO.md` §16 is unambiguous on this point.

**Result shape:**

```json
{
  "probe_id": "018fa310-0000-7000-8000-000000000001",
  "probe_type": "CONCURRENT_PROPOSALS",
  "status": "COMPLETED",
  "writes_canonical_state": true,
  "restore_command": "python -m scripts.seed --profile hero --reset",
  "case_id": "018f8a14-4c22-7f31-9b7d-2ac1e5f09b45",
  "case_revision_before": 9,
  "case_revision_after": 11,
  "proposals": [
    { "proposal_id": "018fa320-…-0001", "agent_run_id": "018fa330-…-0001",
      "kernel_decision_id": "018fa340-…-0001", "decision": "ACCEPTED",
      "retry_count": 0, "case_revision_before": 9,  "case_revision_after": 10,
      "reason_codes": ["FULFILLMENT_ADMITTED"] },
    { "proposal_id": "018fa320-…-0002", "agent_run_id": "018fa330-…-0002",
      "kernel_decision_id": "018fa340-…-0002", "decision": "ACCEPTED_WITH_CONFLICT",
      "retry_count": 2, "case_revision_before": 10, "case_revision_after": 11,
      "reason_codes": ["SERIALIZATION_RETRY", "MUTUAL_EXCLUSION_DETECTED"] }
  ],
  "final_state": {
    "commitment_id": "018f8c30-…-0002",
    "committed_amount":   { "currency": "USD", "amount": "420.0000" },
    "fulfilled_amount":   { "currency": "USD", "amount": "250.0000" },
    "outstanding_amount": { "currency": "USD", "amount": "170.0000" },
    "status": "PARTIAL",
    "open_conflicts": 1
  },
  "invariant_checks": {
    "no_fulfilled_with_outstanding": true,
    "outstanding_identity_holds": true,
    "both_claims_persisted": true,
    "at_least_one_retry": true,
    "revision_delta_equals_accepted_commits": true
  },
  "verify_sql": "SELECT id, decision, retry_count, case_revision_before, case_revision_after FROM kernel_decisions WHERE case_id = '018f8a14-4c22-7f31-9b7d-2ac1e5f09b45' ORDER BY created_at DESC LIMIT 2;",
  "trace_id": "018fa2f8-9a41-7a13-b0e2-6d2b1c4f8a90"
}
```

**Rendering.** A two-lane swimlane on a shared time axis, one lane per proposal, with the retried attempt drawn as a repeated segment labelled `SQLSTATE 40001 · attempt 2`. Beneath it, the commitment arithmetic before and after, and the five `invariant_checks` as pass/fail rows. A failed check renders red and the probe reports `status: "FAILED"` — Judge Mode surfaces an invariant violation rather than hiding one, because an invariant that can fail silently is not an invariant.

**If no retry occurs.** Contention is probabilistic. When `at_least_one_retry` is `false`, the panel says so — *"no serialization retry occurred on this attempt; the commits did not overlap"* — and offers "run again". It never claims a retry that the `kernel_decisions.retry_count` column does not show. `ck_kernel_decisions_retry` bounds the value at 5, so the number on screen is always a real, bounded count.

---

## 9. Cross-tenant isolation demonstration

`00_PRODUCT.md` R8 states the gap honestly: the build seeds three tenants but the UI only ever shows one, so "a judge cannot see isolation working." This panel closes that gap with a live denial rather than a claim.

### 9.1 The probe

`probe_type: "CROSS_TENANT_RETRIEVAL"`, `writes_canonical_state: false`.

The seed provides the material: tenants `sid('tenant','iso-a')` and `sid('tenant','iso-b')` each hold 1,000 evidence rows whose text is *deliberately near-identical* to the hero's — same ISP name, same amounts, same dates (`specs/10_DATABASE_DDL.md` §17.2, §17.7). If the vector index prefix or the tenant foreign key were ever wrong, those rows would leak.

The probe executes four independent attempts and records what each returns. **All four are live; none is a stored string.**

| # | Attempt | Layer | Expected real result |
|---|---|---|---|
| 1 | Agent tool wrapper attempts an MCP read scoped to `sid('user','iso-a')` from a hero-bound `agent_runs` capability | Application capability | `403 CAPABILITY_SCOPE_MISMATCH`, `details.field = "user_id"`, `details.reason = "PAYLOAD_USER_MISMATCH"`; a `tool_calls` entry with `denied: true` |
| 2 | Connect as `pv_agent_reader`, `SELECT id FROM evidence_items LIMIT 1` | SQL grant | `ERROR: user pv_agent_reader has no SELECT privilege on relation evidence_items` |
| 3 | ANN search executed with the hero `user_id` prefix over the full corpus | Vector index prefix | `cross_user_results: 0` out of `vector_candidates`, while `foreign_tenant_corpus_size: 2000` proves the near-identical rows exist |
| 4 | Submit a proposal citing an `iso-a` `evidence_id` | Kernel + composite FK | `422 PROPOSAL_FOREIGN_PROVENANCE`, `unresolved_evidence_ids[]` non-empty; the row lookup returns nothing, so the id is "not found", never "found but denied" |

Attempt 1 also increments the CloudWatch metric `provenance.auth.tenant_mismatch`, and the panel shows the counter moving from `0` to `1` — the metric that should never fire, firing exactly once because a judge asked it to.

### 9.2 Result shape

```json
{
  "probe_id": "018fa350-0000-7000-8000-000000000001",
  "probe_type": "CROSS_TENANT_RETRIEVAL",
  "status": "COMPLETED",
  "writes_canonical_state": false,
  "attempts": [
    { "layer": "CAPABILITY", "attempt": "MCP read scoped to a foreign user_id",
      "outcome": "REFUSED", "http_status": 403,
      "error_code": "CAPABILITY_SCOPE_MISMATCH",
      "error_details": { "capability_kind": "AGENT_RUN", "field": "user_id",
                         "reason": "PAYLOAD_USER_MISMATCH" },
      "agent_run_id": "018fa360-…-0001",
      "tool_call": { "view_name": "agent_evidence_retrieval_v1",
                     "sql_role": "pv_agent_reader", "access_mode": "READ_ONLY",
                     "rows_returned": 0, "denied": true },
      "trace_id": "018fa358-9a41-7a13-b0e2-6d2b1c4f8a90" },

    { "layer": "SQL_GRANT", "attempt": "SELECT id FROM evidence_items LIMIT 1 as pv_agent_reader",
      "outcome": "REFUSED",
      "sqlstate": "42501",
      "error_text": "user pv_agent_reader has no SELECT privilege on relation evidence_items" },

    { "layer": "VECTOR_INDEX_PREFIX",
      "attempt": "ANN search, user_id prefix = hero, over the full corpus",
      "outcome": "ISOLATED",
      "vector_index": "evidence_embedding_ann_idx",
      "beam_size": 8,
      "vector_candidates": 20,
      "cross_user_results": 0,
      "corpus_size_user_scoped": 16035,
      "foreign_tenant_corpus_size": 2000,
      "note": "The 2,000 foreign rows are seeded with deliberately near-identical text." },

    { "layer": "KERNEL_PROVENANCE",
      "attempt": "MemoryProposal citing a foreign evidence_id",
      "outcome": "REFUSED", "http_status": 422,
      "error_code": "PROPOSAL_FOREIGN_PROVENANCE",
      "proposal_status": "REJECTED_INVALID_PROVENANCE",
      "memory_proposal_id": "018fa370-…-0001" }
  ],
  "metrics": { "provenance.auth.tenant_mismatch": { "before": 0, "after": 1 } },
  "verify_sql": "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee = 'pv_agent_reader' AND table_name NOT LIKE 'agent\\_%\\_v1';",
  "trace_id": "018fa358-9a41-7a13-b0e2-6d2b1c4f8a90"
}
```

### 9.3 Rendering

Four stacked cards, each headed by its layer name and each showing the *verbatim* refusal — the HTTP error envelope for layers 1 and 4, the raw SQL error text for layer 2, the counts for layer 3. Nothing is paraphrased. The panel's fixed caption:

> Four independent layers refuse the same request. Any one of them alone would be a policy. Four of them is a boundary.

The `verify_sql` at the bottom is `G11.1`'s V9 query, which must return zero data rows: `pv_agent_reader` has no reach outside the five `agent_*_v1` views.

Layer 3's counts carry the important nuance, displayed inline: the headline figure of **18,000 seeded vectors is the cross-tenant total** (16,000 hero + 1,000 `iso-a` + 1,000 `iso-b`). The hero's *user-scoped* partition is approximately 16,035 rows including the 32 curated items and the 3 retraction fixtures. Judge Mode always renders the real counted value from the probe; it never displays 18,000 as a user-scoped number, because that would be a small, checkable, entirely avoidable lie.

---

## 10. Redaction inside Judge Mode

### 10.1 The absolute rule

> **Raw model chain-of-thought is never exposed. Not in a tooltip, not in a collapsed panel, not behind a judge flag, not in a network response body, not in a console log.**

`05_RELIABILITY_EVAL_DEMO.md` §19 makes "no raw chain-of-thought is exposed" a Definition-of-Done item. `G12.6` enforces it mechanically: an end-to-end test scans every network response body for `thinking`, `reasoning_trace`, `scratchpad`, `chain_of_thought`, `raw_completion`, `system_prompt`, and `prompt_text` keys and asserts zero hits. Judge Mode adds a client-side assertion in `_lib/redact.ts` that throws in development if any of those keys appear in a parsed payload — a second detector, because the first one only runs in CI.

What a judge sees about model reasoning instead: `model_id`, `prompt_version`, `input_tokens`, `output_tokens`, `repair_attempts`, `duration_ms`, `requires_human_review`, and the model's **typed output** — the `MemoryProposal` field counts, the `conflict_hints[].rationale` (a bounded, contract-typed field, not a transcript), and the final draft the human is asked to approve. That is more auditable than a transcript, not less: it is schema-checked, size-bounded, and durably stored.

### 10.2 Trace attribute allowlist

`specs/15_API_SPEC.md` §8.28: "Redaction is enforced at construction… The serializer runs an allowlist over attribute keys per node type; an unknown key is dropped, not passed through." Judge Mode depends on that server-side allowlist and adds no client-side unwrapping.

Permitted in `attributes`: ids, counts, durations, model ids, prompt versions, graph versions, reason codes, revisions, enum values, boolean flags, hash prefixes, view names, role names, index names, token counts.

Forbidden in `attributes`, at any node type: prompt text, system prompts, artifact bodies, evidence `exact_text`, draft bodies, credentials, bearer tokens, `cognito_sub`, `alias_hash`, full `s3_key`, raw SQL text, returned rows.

### 10.3 Field-level masking

| Field | Rule | Where the unmasked value is legitimately visible |
|---|---|---|
| `relationships.external_account_ref` | Masked to last four (`••••4417`) in every list and detail response | Inside State Proof evidence excerpts, where the user is reading their own source document |
| `content_sha256`, `draft_sha256`, `payload_sha256` | First 8 hex characters plus an ellipsis; full value on click-to-copy | Full value is safe — it is a hash of the user's own content |
| `s3_bucket` / `s3_key` | Never rendered | Nowhere in Judge Mode |
| `filter_summary` in MCP calls | Template with values elided (`user_id = <run user>`) | Nowhere; the server never stores the values |
| `bound_params` in retrieval telemetry | Hashed (`sha256:9f3c…`, `vec:1024:sha256:c17a…`) per `specs/13_RETRIEVAL_SPEC.md` §11.6 | Nowhere |
| Counterparty email addresses | Rendered as-is for seeded demo counterparties | The demo tenant's data is fictional seed data |

### 10.4 Why evidence text is shown at all

State Proof deliberately renders `evidence_items.exact_text` with its `source_locator` span. This is the user reading their own document, and it is the difference between a citation and an assertion. Two guards apply: the agent-safe view `agent_evidence_retrieval_v1` withholds `exact_text` from the MCP surface entirely (the agent gets `normalized_text` only), and every Judge Mode session runs against the seeded demo tenant, whose artifacts are fictional. A judge is never shown a real person's document.

### 10.5 Error envelopes are shown verbatim

The isolation panel renders full error envelopes. This is safe by construction: `error.message` is capped at 300 characters and "never contains SQL, stack traces, table names, internal hostnames, model prompts, or artifact content" (`specs/15_API_SPEC.md` §4.1), and `error.details` "never contains user content from another tenant." The envelope was designed to be showable; Judge Mode is where that design pays.

---

## 11. Anti-requirements

Stated bluntly, each with the detector that catches it.

**No hard-coded animation.** The DAG has no timeline scrubber that plays a pre-authored sequence. Node positions are computed by topological layout from `edges[]`; node timing text comes from `started_at` and `duration_ms`. If the API returns eight nodes, eight nodes render. *Detector:* `G12.2` — `e2e/trace_is_real.spec.ts` intercepts `GET /v1/traces/{id}`, collects every `[data-node-id]` in the DOM, and asserts the DOM set is a subset of the payload set with at least 8 members.

**No faked commits.** No screen ever displays a commit, a revision increment, a conflict, or an outbox event that is not backed by a row. `05_RELIABILITY_EVAL_DEMO.md` §17: "do not fake DB commits/trace data." *Detector:* `G12.4` — the mutation probe commits a real correction through `POST /v1/cases/{case_id}/corrections`, moving revision 13 → 14, reloads, and asserts the UI moved. A UI still reading 13 is rendering a snapshot, not the system.

**No invented trace data.** A missing stage is a gap, never a synthesised node. No client-side backfill, no "probably happened" inference, no default values standing in for absent columns. *Detector:* code review plus the `refs` minimum table in §4.4 — a node without its minimum `refs` fails `G12.2`.

**No identifiers that do not resolve in the database.** Every id chip resolves to exactly one row (or, for set refs, to `cardinality` rows). *Detector:* `G12.3` — `grep -rnE "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}" apps/web/src --include='*.ts*'` excluding tests and fixtures must return `0`. A hard-coded UUID in frontend source is a rendered lie.

**No decorative MCP.** MCP calls are not a logo strip. If MCP is disabled the trace shows degradation (`G11.7`); if a call is denied it renders in red (`G11.5`); the caller-reported caveat is stated in-product (§6.4).

**No plausible substitutes for missing telemetry.** An unavailable metric renders `n/a` with the metric name. A count that could not be computed renders `—`. Zero is never used as a stand-in for unknown, because zero is a meaningful value in every counter on this screen.

**No number without a source.** Particularly: the corpus size is counted, not constant (§9.3); the retry count comes from `kernel_decisions.retry_count`; the candidate counts come from the retrieval telemetry; the outstanding amount comes from the `commitments` row that a `CHECK` constraint validates.

### 11.1 Fixture mode

Fixture mode is permitted only for local deterministic graph tests and emergency demonstration (`CANONICAL_DECISIONS.md` → *Fixture mode*). The recorded submission must use live mode.

When `PV_AGENT_MODE=FIXTURE`:

1. `GET /v1/version` returns `fixture_mode: true`. This is the authoritative source and it is **unauthenticated**, so a judge can `curl` it independently of the UI.
2. Every `MODEL_CALL` node carries `attributes.fixture_mode: true` and renders a per-node "replayed" tag. Fixture mode is disclosed where the substitution actually happened, not only globally.
3. A permanent, non-dismissible banner sits above every Judge Mode panel:

   > **DEMO FIXTURE MODE — model outputs are replayed. The Kernel, database, transactions, and event path are live.**

4. The banner has no close control, no `aria-hidden`, no CSS path that removes it, and no query parameter that suppresses it. *Detector:* `G12.7` — `PV_AGENT_MODE=FIXTURE npx playwright test e2e/fixture_banner.spec.ts` asserts the banner is present and non-dismissible.

Even in fixture mode, the real Kernel, the real CockroachDB transaction, and the real event path execute (`05_RELIABILITY_EVAL_DEMO.md` §17). Only stored extraction fixtures replace live model calls. A fixture-mode run still produces genuine `kernel_decisions`, `state_transitions`, and `outbox_events` rows, and Judge Mode still renders them from those rows.

---

## 12. The judge walkthrough

The exact click path, and what a judge should conclude at each step. Total: about four minutes unassisted, or the seven video segments A–G at 2:55.

### Step 1 — Dashboard, before anything happens

**Click:** log in with judge credentials → land on "The Move".

**Sees:** four relationships, `USD 2,020.0000` outstanding, one case resolved four months ago, one deposit overdue, one reimbursement partial.

**Concludes:** this is a real consumer product with money at stake, not a technology demonstration wearing a UI. (Rubric: Real-World Impact.)

### Step 2 — Upload the June invoice

**Click:** *Upload artifact* → `demo/artifacts/northline-june-invoice.eml` → *Complete*.

**Sees:** the case card flips `RESOLVED → REOPENED`, revision text moves `12 → 13`, attention badge moves `NONE → URGENT`, and a conflict appears.

**Concludes:** the system did not summarise the invoice; it changed its mind about the world and recorded that it did. This is the "wait, it reopened the case" moment, and it happens before any database concept is named.

### Step 3 — Open State Proof

**Click:** *Why?* on the case card → Panel B.

**Sees:** grounding — two provider-authored documents `SUPPORTS`, one counterparty claim `CONTRADICTS`, each with weight, reason code, source authority, and the exact quoted sentence. Lineage — `balance_owed` v1 `CONFIRMED` superseded by v2 `DISPUTED`, same `$0.0000` value, with the caption "the amount did not change; our confidence in it did."

**Concludes:** grounding and lineage are different things, both are stored, and a belief's *status* can change without its *value* changing — which no prose summary can express and no chunk-based retrieval system can represent. (Rubric: Agentic Memory Design.)

### Step 4 — Toggle `include_retracted`

**Click:** the retraction toggle in Panel B.

**Sees:** three previously hidden rows appear with `SUPERSEDED` / `RETRACTED` / `QUARANTINED` badges — including an extraction error reading "Service termination effective 31 July" whose embedding sits *closer* to the June invoice than the correct evidence does.

**Concludes:** retraction filtering is a correctness mechanism, not a cosmetic filter. Without it, that row wins the retrieval and the system reaches the wrong answer confidently.

### Step 5 — Open the Memory Trace

**Click:** *Judge Mode* → Panel C.

**Sees:** the DAG in two lanes. Three model nodes on top. Nineteen deterministic nodes below. Exactly one arrow crosses from the model lane to the deterministic lane, and it terminates at `PROPOSAL`.

**Concludes:** the LLM proposes; a deterministic kernel decides, commits, and acts. The permission boundary is architectural, not prompt-based. (Rubric: Agentic Memory Design, Technological Implementation.)

### Step 6 — Click any id chip

**Click:** the `kernel_decisions.id` chip on the `KERNEL_DECISION` node → *verify*.

**Sees:** the exact read-only SQL, copyable, naming a real table and a real primary key.

**Concludes:** these identifiers resolve. The trace can be checked by someone who does not trust the people who built it — which is the only kind of trace worth showing a judge.

### Step 7 — Read the MCP nodes and Panel D

**Click:** expand `MCP_TOOL_CALL` sequence 2.

**Sees:** `agent_evidence_retrieval_v1`, role `pv_agent_reader`, `access_mode: READ_ONLY`, `evidence_embedding_ann_idx`, `beam_size 8`, 20 rows returned, `retraction_filter_applied: true`, 2 excluded. Panel D shows the commit ✓, `revision 12 → 13 · 0 retries (SERIALIZABLE)`, outbox `case.reopened.v1` dispatched, the model route, and approval required. Below it, the caveat that tool calls are caller-reported.

**Concludes:** the CockroachDB Cloud Managed MCP Server is doing real, scoped, read-only work through views whose grants are the actual permission boundary — and the team knows exactly where the evidence for that claim stops. (Rubric: Technological Implementation, Product Readiness.)

### Step 8 — Run the counterfactual

**Click:** *Memory OFF / ON* → *Run*.

**Sees:** the parity block (artifact ✓ model ✓ prompt ✓ graph ✓ decode ✓), then two symmetric columns: "Invoice for $186 due 30 June" against "Contradicts your 15 May termination confirmation — case reopened, dispute drafted." Both columns carry a resolvable `agent_runs.id`. The safety block shows four green checks. Optionally: *show request diff*.

**Concludes:** the memory system is doing the work, not the model. The same model with the same prompt on the same bytes produces an ordinary answer when memory is removed. (Rubric: Creativity and Originality.)

### Step 9 — Approve and send

**Click:** the pending action intent → read the draft with per-sentence support ids → *Approve & Send*.

**Sees:** approval binds to `basis_case_revision = 13` and `approval_draft_sha256`; the executor revalidates both and only then sends; `action_executions.revalidated_case_revision` appears in the trace.

**Concludes:** no uncommitted proposal and no agent scratchpad can produce an external side effect. An approval goes stale the instant memory moves under it. (Rubric: Product Readiness.)

### Step 10 — The second reveal

**Click:** the landlord deposit card.

**Sees:** a `TRIGGER_EVALUATION` node with `trigger_type: COMMITMENT_DEADLINE`, `last_result: FIRED`, and the exact field values the predicate saw at wakeup — `outstanding_amount = 1800.0000`, `clock.now >= due_at`.

**Concludes:** the system woke itself on elapsed time evaluated against current state, and produced an alert the user never scheduled. Memory that only reacts to inbound mail is not prospective memory.

### Step 11 — Probes and isolation, in live Q&A only

**Click:** *Inject duplicate event* → *Cross-tenant retrieval* → (optionally, and with the write warning acknowledged) *Concurrent proposals*.

**Sees:** first delivery `PROCESSED`, duplicate `DUPLICATE_NOOP` keyed on `event_id`, one `processed_events` row. Four independent layers refusing the same cross-tenant request, including a raw `pv_agent_reader` permission error and `cross_user_results: 0` against 2,000 near-identical foreign rows. A real `SQLSTATE 40001` retry with a final state that no `CHECK` constraint can call impossible.

**Concludes:** the reliability claims are executable, not narrated — and the team is willing to run them live, in front of a judge, on a system that writes to its own memory when it does.

---

## 13. Acceptance criteria

Judge Mode is done when every assertion below passes. These are the `G-11` and `G-12` batteries from `quality/23_PHASE_GATES.md`, restated with the elements this document adds.

| ID | Assertion | Command |
|---|---|---|
| `G12.1` | Hero flow end to end in a browser, zero console errors | `npx playwright test e2e/hero_flow.spec.ts` |
| `G12.2` | Every rendered node id exists in the API payload; ≥ 8 nodes | `npx playwright test e2e/trace_is_real.spec.ts` |
| `G12.3` | Zero UUID literals in frontend source | `grep -rnE "[0-9a-f]{8}-…" apps/web/src --include='*.ts*'` |
| `G12.4` | Mutation probe: a real correction moves the UI from revision 13 to 14 | `npx playwright test e2e/trace_mutation_probe.spec.ts` |
| `G12.5` | Counterfactual differs and changes nothing | `POST /v1/judge-mode/counterfactual` + `SELECT revision FROM cases` before/after |
| `G12.6` | No chain-of-thought key in any response body | `npx playwright test e2e/no_cot_leak.spec.ts` |
| `G12.7` | Fixture banner cannot be suppressed | `PV_AGENT_MODE=FIXTURE npx playwright test e2e/fixture_banner.spec.ts` |
| `G11.4` | Trace MCP calls come from rows, not a template; deleting the row empties the panel | `curl …/memory-trace \| jq` + `SELECT count(*) FROM agent_runs WHERE id=…` |
| `G11.5` | A denied MCP call is rendered, not swallowed | `pytest tests/mcp/test_denied_call_is_visible.py` |
| `G11.7` | MCP disabled degrades visibly | `PV_MCP_ENABLED=false pytest tests/mcp/test_degradation.py` |
| **JM-1** | Every node in `GET /v1/traces/{id}` carries its minimum `refs` (§4.4) | `pytest tests/api/test_trace_refs_minimum.py` |
| **JM-2** | Every `refs` entry resolves to exactly `cardinality` rows | `pytest tests/api/test_trace_refs_resolve.py` |
| **JM-3** | Counterfactual `parity.all_equal` is `true`; both columns hidden when `false` | `pytest tests/api/test_counterfactual_parity.py` |
| **JM-4** | Isolation probe returns four refusals, all live | `pytest tests/api/test_isolation_probe.py` |
| **JM-5** | Panel D renders `n/a` for an unavailable metric and never `0` | `npx playwright test e2e/panel_d_missing_metric.spec.ts` |
| **JM-6** | Corpus size on screen equals `SELECT count(*)`, never a constant | `pytest tests/api/test_corpus_count_is_counted.py` |

---

## 14. Risks and open questions

**R1 — Resolved.** `specs/10_DATABASE_DDL.md` §11.3 now declares `tool_calls JSONB NULL`, `model_calls JSONB NULL` and `capability_status JSONB NULL` on `agent_runs`, with `jsonb_typeof` CHECK constraints and `ck_agent_runs_counterfactual_toolless`; §11.4 now declares `idempotency_records.trace_id UUID NULL` with `idx_idempotency_trace`. All four land inline in migration `0008_events_infrastructure`, which is where both tables are created, so no extra Alembic revision is required. `G11.4` was corrected. The naming rule is fixed: the **column** is `agent_runs.tool_calls`; the **JSON field** carried over HTTP is `mcp_tool_calls[]`. Do not re-litigate that pairing.

**R2 — Resolved.** `POST /v1/judge-mode/probes`, `GET /v1/judge-mode/probes/{probe_id}` and `GET /v1/judge-mode/agent-views` are now rows 8.33, 8.34 and 8.35 of `specs/15_API_SPEC.md` §8.0, with scopes (`judge.probe`, `provenance.memory/read`), rate-limit buckets and the `PROBE_NOT_FOUND` / `PROBE_TARGET_BUSY` / `JUDGE_MODE_DISABLED` error codes. `POST /v1/judge/triggers/{trigger_id}/wake` was added in the same edit as row 8.32, since `submission/51_VIDEO_SCRIPT.md` beat 6 depends on it and it was equally absent. `provenance_contracts` request/response models remain to be written, but the surface these sections describe can now be called.

**R3 — Landed, except one.** The `CANONICAL_CHANGE` node type and the `node.refs` array are now in `specs/15_API_SPEC.md` §8.28's closed seventeen-value enum, with the `change_kind` set enumerated and the explicit statement that `state_transitions` is both the spine and the source of these child nodes — reconciling this document with `quality/21_OBSERVABILITY_ANALYTICS.md` §6.2, which previously specified an incompatible shape. `GET /v1/version` now returns `fixture_mode`, `agent_mode`, `otlp_export`, `schema_revision` and `db_ok` (§8.2), and is the pack's single authoritative disclosure channel; `/v1/healthz` stays a bare liveness probe. **Now closed (2026-08-17).** The counterfactual `parity` block is a documented, normative field on `specs/15_API_SPEC.md` §8.31's response, with the render gate stated there and mirrored as mandatory item 9 in `frontend/30_UX_SPEC.md` §14.4. All three documents now specify one render rule: six parity checks above the columns, and no columns at all when `all_equal` is `false`. §14.4 item 1 was also corrected — its previously fixed header copy ("Both columns ran just now") was false under the default `REPLAY_COMMITTED` strategy, where the MEMORY_ON column is the already-committed production run rather than a fresh execution; the copy is now selected from `memory_on.strategy` and is truthful under both.

**R4 — The concurrency probe writes real memory to the demo tenant.** §8.3 permanently changes `sid('commitment','damage')` from `220.0000` to `170.0000` outstanding and drops the dashboard total from `2,020.0000` to `1,970.0000`. That is honest but irreversible without a reseed, and a judge who runs it before watching the video sees numbers that do not match the recording. **Mitigation:** the write warning and restore command are shown before the button is enabled, and the probe is disabled while the recording-session flag is set. **Residual risk:** operator error during live Q&A. A dedicated probe case seeded outside "The Move" context would remove the risk entirely and would cost one addition to `scripts/seed/obligations.py`; that change was not made here because the seed is owned by `specs/10_DATABASE_DDL.md` §17.

**R5 — Seed names and API example names disagree. RESOLVED 2026-08-17.** `specs/15_API_SPEC.md` §8, `frontend/30_UX_SPEC.md`, and `frontend/31_DESIGN_BRIEF_FOR_OPUS5.md` previously used a different persona ("Dana Whitfield") and different counterparty names ("Northline Broadband", "Harrow Street Properties") from the ones `specs/10_DATABASE_DDL.md` §17 actually seeds. All documents now use the seeded set: **Alex Rivera** (hero user, `America/New_York`), **Northline Fiber** (ISP, two relationships), **Harborview Property Management** (landlord), **Beltline Movers** (moving company), **Kestrel Analytics** (employer), **Cascade Power** (decoy utility). `specs/10_DATABASE_DDL.md` §17.3 is the naming authority; any future example must be drawn from it rather than invented. The dangerous case was the design brief calling the mover "Kestrel Moving Co." while the seed's Kestrel Analytics is the **employer** — a design returned against that brief would have attributed the $420 damage claim to the user's employer.

**R6 — The 18,000-vector headline is a cross-tenant total.** `00_PRODUCT.md` §5 and `05_RELIABILITY_EVAL_DEMO.md` §14 say "18,000+ seeded memory vectors scoped to user"; the seed plan is 16,000 hero + 1,000 `iso-a` + 1,000 `iso-b`. The hero's user-scoped partition is approximately 16,035. §9.3 requires Judge Mode to display the real counted value and to label 18,000 as the corpus total. The video narration must be corrected to match, or a judge who runs the count catches a discrepancy in the one number the demo repeats most often.

**R7 — Resolved.** The embedding version is `v1` everywhere (`CANONICAL_DECISIONS.md`, *Embeddings*). The descriptor spellings formerly in `specs/13_RETRIEVAL_SPEC.md`, `specs/15_API_SPEC.md`, `specs/11_CONTRACTS.md` and `quality/22_EVAL_DATASETS.md` were corrected in `quality/24_CONSISTENCY_REVIEW.md` finding B1. This matters because `embedding_version` is a *filter predicate* in the canonical ANN query: a mismatch is not cosmetic, it silently returns zero rows.

**R8 — `graph_name` values disagree between the DDL and the API examples.** `ck_agent_runs_graph` permits exactly `ingestion`, `advocate`, `resolver`, `counterfactual`; `specs/15_API_SPEC.md` §8.28 shows `ingestion_graph` and §8.30 shows `counterfactual_graph`. This document uses the DDL values because the CHECK constraint will reject the others at insert time. The API examples need one edit.

**R9 — `ck_agent_runs_counterfactual_consistent` constrains the counterfactual design.** The constraint `is_counterfactual = (memory_mode = 'OFF')` means a MEMORY_ON *sandboxed rerun* cannot be marked `is_counterfactual = true`. Under the default `REPLAY_COMMITTED` this is fine — the ON side is the real production run. Under `RERUN_SANDBOXED` the ON re-run is distinguishable only by `graph_name = 'counterfactual'`, and any query that counts "real" runs by `is_counterfactual = false` will over-count. Judge Mode therefore identifies sandboxed runs by `graph_name`, and any future analytics must do the same.

**R10 — The trace is caller-reported at exactly one point, and that point is the sponsor tool.** §6.4 states this in-product. It remains a genuine limitation: a compromised or buggy agent runtime could under-report MCP calls, and nothing in v1 would detect it. CockroachDB audit logging or MCP server-side logging would close the gap; neither is wired up. The defence is disclosure, and disclosure is not the same as a control.

**R11 — Panel D's health metrics depend on CloudWatch being reachable from the browser's request path.** The control plane must proxy them, since the browser holds no AWS credentials. That proxy is not specified in `specs/15_API_SPEC.md` and is not specified here either; §5.3's `n/a` rendering is the honest fallback and may well be what ships. A judge seeing three `n/a` values in the health strip is a worse outcome than not showing the strip, so if the proxy is not built, drop the strip rather than render an empty one.

**R12 — The 3-minute video does not have room for §12's eleven steps.** The walkthrough is the unassisted-judge path and the live-Q&A path. The video is segments A–G at 2:55 (`00_PRODUCT.md` §5), which covers steps 1, 2, 3, 8, 9, 10, and a compressed 7. Steps 4, 5, 6, and 11 are Q&A only. Conflating the two paths would either overrun the submission limit or gut the walkthrough; both are worse than keeping them separate and saying so.

**R13 — Judge group membership is a Cognito group, and group membership is not tested from a clean profile in this document.** `quality/23_PHASE_GATES.md` §21 does test judge login from a clean browser profile. Judge Mode adds a failure mode that test does not cover: a judge who is authenticated but *not* in `provenance-judges` sees `judge_mode_enabled: false` and lands on a disabled surface with no explanation of how to get access. The surface must render an explicit "Judge Mode is not enabled for this account" state naming the group — not a blank panel, and not a `403` envelope dumped on screen.
