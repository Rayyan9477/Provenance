# Provenance — Observability and Analytics Contract

Purpose: fix the correlation-id model, the OpenTelemetry span map, the metric catalogue, the log and redaction contract, the Memory Trace persistence model, its tamper check, the CloudWatch dashboard and alarm set, and the analytics questions this system must be able to answer once it has real users.

Status: planning complete v1.1
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared

Audience: backend engineers instrumenting `services/control_plane` and `workers/`, agent engineers instrumenting the LangGraph graphs on AgentCore Runtime, the engineer who builds `packages/python/provenance_telemetry`, and the reviewer running the `G-13` battery. Product readiness is the property this document exists to make checkable rather than assertable.

Product: **Provenance** — a system of record for the institutions that already have one of you.

> Three terms are load-bearing and never collapsed. **Provenance** is the product name and never a common noun. **grounding** is the `belief_support` edges with relation `SUPPORTS | CONTRADICTS | QUALIFIES`. **lineage** is the `belief_versions` supersession chain and the reason code for each step. Telemetry may count both; it may not rename either.

---

## 0. Contents

1. [Scope, and the one rule everything else serves](#1-scope-and-the-one-rule-everything-else-serves)
2. [The correlation-id model](#2-the-correlation-id-model)
3. [The OpenTelemetry span map](#3-the-opentelemetry-span-map)
4. [The metric catalogue](#4-the-metric-catalogue)
5. [Structured logs and the redaction contract](#5-structured-logs-and-the-redaction-contract)
6. [The Memory Trace persistence model](#6-the-memory-trace-persistence-model)
7. [Trace integrity](#7-trace-integrity)
8. [CloudWatch dashboard and alarms](#8-cloudwatch-dashboard-and-alarms)
9. [Analytics for product learning](#9-analytics-for-product-learning)
10. [Where this contract is verified](#10-where-this-contract-is-verified)
11. [Risks and open questions](#11-risks-and-open-questions)

---

## 1. Scope, and the one rule everything else serves

This document owns telemetry: correlation ids, spans, metrics, logs, the persisted trace, and the dashboards over them. It does not own product semantics. Where a name, enum, threshold, or table appears here it is quoted from its owning specification, never re-decided.

### 1.1 The rule

> **No element of a rendered Memory Trace may be synthesised at render time.**

Every node in `GET /v1/traces/{trace_id}` and `GET /v1/cases/{case_id}/memory-trace` is a projection of one persisted row. If the row does not exist, the node does not exist. If the row is deleted, the panel goes empty. There is no fallback narration, no template that fills a gap, no "the model probably did X" node, and no client-side animation that advances on a timer.

This is not a stylistic preference. `CANONICAL_DECISIONS.md` freezes it: *"Judge Mode is built from persisted runtime rows and spans. Scripted trace animation and hard-coded object identifiers are forbidden."* A reviewer who catches one fabricated node has correctly concluded that none of the others can be trusted either, and the credibility of the whole surface collapses with it. §6 defines exactly which rows constitute a trace and §7 defines how a reviewer proves the correspondence.

### 1.2 The telemetry invariant, stated as the four product invariants see it

| Product invariant (`00_PRODUCT.md` §0.1) | What telemetry must be able to show |
|---|---|
| Evidence is append-only | `evidence.admitted.v1` counts rise; `evidence_items` rows are never observed to decrease; a retraction appears as a status transition plus `evidence.retracted.v1`, never as a missing row. |
| Beliefs are revisable | Every `belief_versions` insert carries a `kernel_decision_id` that appears in exactly one trace; lineage depth is queryable, not narrated. |
| State is transactional | One `memory.kernel.transaction` span per commit, carrying `retry_count` and the aggregate revision before and after; no span exists for a partial write, because no partial write exists. |
| Actions are permissioned | `action.approve` and `action.execute` are separate spans with a human decision between them, and `action.revalidate` records the revision and hash comparison that gates the send. |

### 1.3 Export architecture, and why it is boring on purpose

Three transports, chosen so that no part of the observability story depends on a service that might not be reachable on demo day.

| Signal | Primary transport | Why | Fallback |
|---|---|---|---|
| Metrics | CloudWatch **Embedded Metric Format** (EMF) JSON written to stdout | App Runner and Lambda both ship stdout to CloudWatch Logs with no agent, no sidecar, and no collector to crash. CloudWatch extracts the metrics from the log line automatically. | None needed. If EMF extraction is disabled the values are still in the log line and recoverable with Log Insights. |
| Spans | One structured JSON log line per span end, on stdout, **plus** OTLP export when `PV_OTLP_TRACES_ENDPOINT` is set | `23_PHASE_GATES.md` `G13.5` asserts spans by querying the `/provenance/control-plane` **log group** for `span_name`. The log line is therefore the record of account; OTLP is an enhancement. | If OTLP export fails, spans remain complete in Logs and the deployment discloses `otlp_export: false` on `/v1/version`. |
| Logs | stdout, JSON, one object per line | Same reason. | None. |

App Runner does not support sidecar containers, so an ADOT Collector would have to run in-process or not at all. Writing EMF and span records to stdout removes the question. The cost is that metric extraction is asynchronous and Log Insights is the query surface for spans rather than a trace UI; at demo scale that is an acceptable trade and it is stated here so nobody discovers it during the `G-13` round.

**W3C trace ids.** The OpenTelemetry trace id is set to the 16 bytes of the Provenance `trace_id` UUIDv7 by a custom `IdGenerator`, so a span and a database row share one identifier with no mapping table. Two consequences are recorded honestly:

- UUIDv7 begins with a 48-bit millisecond timestamp, which is **not** the epoch-seconds prefix AWS X-Ray requires. Provenance therefore does not export to X-Ray natively. If X-Ray is ever wanted, the id generator reverts to the default and the join moves to the `provenance.trace_id` span attribute, which is why that attribute is mandatory on **every** span (§3.2) even though it is redundant today.
- A caller-supplied `X-Provenance-Trace-Id` that is not a valid UUID is ignored and replaced (`15_API_SPEC.md` §1.4). A caller-supplied trace id is joined, never trusted: it never selects a row and never widens authority.

---

## 2. The correlation-id model

### 2.1 The seven identifiers

Every one of these is a UUID except where stated. Each is minted exactly once, by exactly one component, and is thereafter read-only.

| Id | Type | Minted by | Minted when | Persisted on | Lifetime |
|---|---|---|---|---|---|
| `trace_id` | UUIDv7 | Control plane request middleware, or the worker that starts a flow with no inbound trace | First entry into the system for one causal flow: an artifact arriving, a scheduled trigger waking, a human approving | `memory_proposals`, `kernel_decisions`, `state_transitions`, `outbox_events`, `agent_runs`, `idempotency_records` (§6.6) | The whole flow, across many HTTP requests, agent runs, and Lambda invocations |
| `request_id` | UUIDv7 | Control plane request middleware | Every single HTTP request, including retries and replays | Not persisted; log field and `X-Provenance-Request-Id` response header only | One HTTP request |
| `agent_run_id` | UUIDv7 | Control plane, immediately **before** `InvokeAgentRuntime` (`15_API_SPEC.md` §3.3) | One LangGraph graph execution | `agent_runs.id` | Until `POST /internal/v1/agent-runs/{id}/complete` burns the capability |
| `proposal_id` | UUIDv7 | The agent, as the id of the `MemoryProposal` it submits | On proposal build, before submission | `memory_proposals.id` | Permanent |
| `kernel_decision_id` | UUIDv4 | Memory Kernel, **before** the transaction opens (`10_DATABASE_DDL.md` §1 item 7) | Every kernel outcome, including rejections and no-ops | `kernel_decisions.id`, referenced by `belief_versions.kernel_decision_id` and `state_transitions.kernel_decision_id` | Permanent |
| `event_id` | UUIDv7 | Memory Kernel, at outbox insert, inside the same transaction | Every domain event | `outbox_events.id`, `processed_events.event_id` | Permanent |
| `action_intent_id` | UUIDv7 | Advocate, via `POST /internal/v1/advocacy/action-intents` | One proposed external side effect | `action_intents.id`, referenced by `action_executions.action_intent_id` | Permanent |
| `trigger_evaluation_id` | UUIDv5, **derived** | Computed, not stored (§2.2) | Every trigger wake | Derivable from `prospective_triggers.id` + `evaluation_version` | Permanent, because it is a pure function of two persisted columns |

`trigger_evaluation_id` is the only derived member of the set, and it is derived rather than stored because `prospective_triggers` records `evaluation_version` and nothing else per wake. Deriving it costs one function and keeps the DDL unchanged:

```python
# packages/python/provenance_telemetry/ids.py
from __future__ import annotations

import uuid
from typing import Final

# "trigge" in ASCII hex, mirroring the seed namespace convention in
# 10_DATABASE_DDL.md §17.1 (PROVENANCE_SEED_NS ends 70726f76656e == "proven").
TRIGGER_EVAL_NS: Final = uuid.UUID("6f2b1c40-0000-4000-8000-747269676765")


def trigger_evaluation_id(trigger_id: uuid.UUID, evaluation_version: int) -> uuid.UUID:
    """Stable id for one wake of one trigger.

    Deterministic on purpose. The scheduler may deliver the same wake twice;
    both deliveries derive the same id, which is also the tail of the
    idempotency key `trg-{trigger_id}-{evaluation_version}` required by
    15_API_SPEC.md §9.10. One wake, one id, whatever the delivery count.
    """
    if evaluation_version < 0:
        raise ValueError("evaluation_version must be >= 0")
    return uuid.uuid5(TRIGGER_EVAL_NS, f"{trigger_id}:{evaluation_version}")
```

### 2.2 Where each one is minted, in code

```python
# services/control_plane/app/api/middleware/correlation.py
from __future__ import annotations

import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from provenance_telemetry import context, ids

_TRACE_HEADER = "X-Provenance-Trace-Id"
_REQUEST_HEADER = "X-Provenance-Request-Id"


def _parse_or_mint(raw: str | None) -> tuple[uuid.UUID, bool]:
    """A caller may join an existing flow. A caller may not choose an identity."""
    if raw:
        try:
            return uuid.UUID(raw), True
        except ValueError:
            pass  # malformed is silently replaced, per 15_API_SPEC.md §1.4
    return ids.uuid7(), False


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id, joined = _parse_or_mint(request.headers.get(_TRACE_HEADER))
        request_id = ids.uuid7()

        request.state.trace_id = trace_id
        request.state.request_id = request_id

        # One contextvar binding, inherited by every span, log record, metric
        # dimension set, and outbound client in this task. Nothing downstream
        # ever passes a trace id as a function argument.
        with context.bind(trace_id=trace_id, request_id=request_id,
                          trace_joined=joined):
            response = await call_next(request)

        # Present on success AND on every error. 15_API_SPEC.md §1.5.
        response.headers[_TRACE_HEADER] = str(trace_id)
        response.headers[_REQUEST_HEADER] = str(request_id)
        return response
```

The error handlers in `15_API_SPEC.md` §4.1 already read `request.state.trace_id`, so the trace id is present in the body of a 500 as well as its headers. That is deliberate: the trace id matters most on the request that failed.

### 2.3 Propagation across the four boundaries

| Boundary | Carrier | Field | Notes |
|---|---|---|---|
| Browser → control plane | HTTP header | `X-Provenance-Trace-Id` (optional in), always out | The UI stores the trace id returned by `POST /v1/artifacts/{id}/complete` and uses it to poll and to deep-link Judge Mode. |
| Control plane → AgentCore Runtime | `InvokeAgentRuntime` payload | `{"agent_run_id", "capability_proof", "artifact_id", "trace_id"}` | Exactly the payload in `15_API_SPEC.md` §3.7. The agent does not mint a trace id; it is given one. If the payload's `trace_id` disagrees with `agent_runs.trace_id`, the row wins and `provenance.trace.binding_mismatch` is incremented. |
| Agent → control plane (`/internal/v1`) | HTTP header | `X-Provenance-Trace-Id`, plus `agent_run_id` in the path | The path capability is authoritative for identity; the header is authoritative for nothing. The server re-reads `agent_runs.trace_id` and uses that. |
| Kernel → EventBridge → Lambda consumer | `DomainEvent` envelope | `trace_id`, `causation_id`, `correlation_id` (`15_API_SPEC.md` §10.1) | The envelope is written **inside** the kernel transaction, so a delivered event cannot carry a trace id for a commit that did not happen. |
| EventBridge Scheduler → `trigger_wakeup` Lambda | Scheduler target input | `{"trigger_id", "capability_proof", "scheduled_for", "schedule_name", "evaluation_version"}` | Deliberately **no** `trace_id`. A trigger wake starts a new causal flow: the schedule was created by an old commit, but the evaluation is a fresh decision against current state. The worker mints a fresh `trace_id` and sets `causation_id = trigger_evaluation_id(...)`. |
| SQS DLQ | SQS message attributes | `provenance-trace-id`, `provenance-event-id` | So a poisoned message is traceable without parsing its body, which is exactly the body that may be malformed. |
| Any process → any log/span/metric | `contextvars` | bound once per task in `provenance_telemetry.context` | No component accepts a trace id as a function parameter. A parameter can be forgotten; a contextvar cannot be. |

Two propagation rules that are easy to get wrong and expensive to fix later:

1. **A retry does not mint a new `trace_id`.** It mints a new `request_id`. The retried request carries the same `Idempotency-Key`, so the flow is one flow; the transport attempt is what differs. `idempotency_records.trace_id` therefore holds the trace of the **first** attempt, and the replay response carries the original trace id in the body while the header carries the new request's trace id (`15_API_SPEC.md` §6.5). Both are recoverable, which is the point.
2. **A causally-downstream flow does not inherit the parent's `trace_id` when the two are separated by a human decision or by wall-clock time.** Approval and execution stay on the trace of the case activity that produced them, because the reviewer needs to see one story. A trigger wake four months later does not, because pretending it is the same flow would make trace duration meaningless and would let one `trace_id` accumulate an unbounded number of nodes. The link between them is `causation_id`, which is what it is for.

### 2.4 The Judge Mode click path

Judge Mode's job is to make every one of these ids a link, so that a skeptical reader can walk the whole chain without leaving the browser. The path below is the one the demo follows and the one the video's segment G shows.

| Start from | Click target | Endpoint that resolves it | What the reviewer sees next |
|---|---|---|---|
| Case card on the dashboard | "Why?" | `GET /v1/cases/{case_id}/state-proof` | grounding (`SUPPORTS`/`CONTRADICTS`/`QUALIFIES` edges with source authority) and lineage (v1 → v2 and the supersession reason) |
| State Proof | "What changed this?" | `GET /v1/cases/{case_id}/memory-trace` | The list of traces that materially changed this case, newest first, each with `case_revision_before` → `case_revision_after` |
| Memory Trace list item | `trace_id` | `GET /v1/traces/{trace_id}` | The full DAG: API request → parse → agent run → model calls → MCP tool calls → retrieval → proposal → kernel decision → transaction → outbox → advocate → intent → approval → execution |
| Trace node `AGENT_RUN` | `agent_run_id` | `GET /v1/agent-runs/{agent_run_id}` | Graph name and version, model route, memory mode, MCP tool calls with `view_name` and `sql_role`, token counts |
| Trace node `MCP_TOOL_CALL` | `view_name` | `GET /v1/judge-mode/agent-views` | The five agent-safe view names as the **database** reports them, so the rendered name can be diffed against `information_schema.views` (`G11.6`) |
| Trace node `PROPOSAL` | `proposal_id` | `GET /v1/memory/proposals/{proposal_id}` | The typed proposal, its status, and the decision that consumed it |
| Trace node `KERNEL_DECISION` | `kernel_decision_id` | `GET /v1/kernel-decisions/{kernel_decision_id}` | `decision`, `reason_codes`, `case_revision_before`/`after`, `retry_count`, `committed_at`, and every `belief_versions` and `state_transitions` row carrying that id |
| Trace node `OUTBOX_EVENT` | `event_id` | `GET /v1/events/{event_id}` | Envelope, dispatch attempts, and the `processed_events` rows proving which consumers saw it and which deduplicated it |
| Trace node `ACTION_INTENT` | `action_intent_id` | `GET /v1/action-intents/{action_intent_id}` | Draft, `draft_sha256`, `supporting_belief_versions`, `basis_case_revision`, approval record, execution attempts |
| Trace node `TRIGGER_EVALUATION` | `trigger_id` | `GET /v1/triggers/{trigger_id}` | Predicate AST, `not_before`, `basis_case_revision`, `last_result`, `last_reason_code`, and the `field_values` the predicate actually read |
| Any node | "Verify" | `GET /v1/traces/{trace_id}?include=integrity` | The row census and digests of §7, plus the copy-pasteable verifier command |

Every one of those endpoints now exists in `15_API_SPEC.md` §8.0. Six of them (`/v1/judge-mode/agent-views`, `/v1/agent-runs/{id}`, `/v1/memory/proposals/{id}`, `/v1/kernel-decisions/{id}`, `/v1/events/{id}`, `/v1/triggers/{trigger_id}`) were added there as rows 8.35–8.40 in response to this table; before that edit the claim in this paragraph was false and the click path could not be built. This document still adds no route on its own authority — it specifies which id opens which one, and `15_API_SPEC.md` owns their existence, auth, scopes and error sets.

---

## 3. The OpenTelemetry span map

### 3.1 Resource attributes

Set once per process, on every span, metric, and log record emitted by that process.

| Attribute | Value | Source |
|---|---|---|
| `service.name` | `provenance-control-plane` \| `provenance-agent-runtime` \| `provenance-worker-ses-ingest` \| `provenance-worker-trigger-wakeup` \| `provenance-worker-outbox-dispatch` \| `provenance-worker-action-execute` \| `provenance-worker-advocate-dispatch` | Closed set. A new value requires editing this table. |
| `service.version` | The full git SHA, identical to `GET /v1/version`'s `git_sha` | `PV_GIT_SHA` baked at image build |
| `service.namespace` | `provenance` | constant |
| `deployment.environment` | `dev` \| `ci` \| `demo` \| `prod` | `PV_STAGE` |
| `cloud.region` | `us-east-1` | constant for v1 |
| `provenance.schema_revision` | Alembic head, e.g. `0008` | read at startup |
| `provenance.agent_mode` | `LIVE` \| `FIXTURE` | `PV_AGENT_MODE`. Fixture mode must be visible in telemetry as well as in the UI banner (`23_PHASE_GATES.md` §23.12). |

### 3.2 Universal span attributes

Present on **every** span without exception. Missing any of them is a lint failure (§10).

| Attribute | Type | Notes |
|---|---|---|
| `provenance.trace_id` | string (UUID) | Redundant with the OTel trace id today, mandatory anyway, so the join survives an id-generator change (§1.3). |
| `provenance.request_id` | string (UUID) or absent | Absent on spans that begin outside an HTTP request (scheduler wake, outbox sweep). |
| `provenance.tenant_hash` | string, 16 hex | `HMAC-SHA256(PV_LOG_HASH_KEY, tenant_id)[:8]`. Never the raw tenant id. |
| `provenance.user_hash` | string, 16 hex | Same construction. `ARCHITECTURE.md` §21.2 requires a hashed user id, not the id. |
| `provenance.stage` | string | Mirrors `deployment.environment`; kept as a span attribute so a single Log Insights query can filter without a resource join. |
| `provenance.memory_mode` | `ON` \| `OFF` | On every span in an agent flow, so the counterfactual's two runs are separable in one query. Defaults to `ON`. |

Span names are the closed set in §3.3. A span name is never interpolated with an id, a user value, or a case title. Ids go in attributes; names stay low-cardinality so they can be aggregated.

### 3.3 The tree

The eighteen span names in `05_RELIABILITY_EVAL_DEMO.md` §6 are all present and none is renamed. Nine names are added, marked `+`, for boundaries that already exist in the architecture and were previously folded into a parent.

```text
http.server.request                       SERVER   + control plane, one per HTTP request
├── artifact.register                     INTERNAL
├── artifact.parse                        INTERNAL
│   └── artifact.textract                 CLIENT   + only when parser falls back to Textract
├── embedding.generate                    CLIENT     one span per Bedrock batch, not per item
├── agent.interpreter.run                 CLIENT     from the control plane's side
│   │                                                (SERVER on the AgentCore side, same trace)
│   ├── model.invoke                      CLIENT   + node=extract_structured_evidence, tier=E
│   ├── retrieval.identity                INTERNAL   stages A + B + C of 13_RETRIEVAL_SPEC.md §5
│   ├── retrieval.vector                  INTERNAL   stage D, the only stage using embeddings
│   ├── retrieval.expand                  INTERNAL   stages E + F
│   ├── retrieval.rerank                  INTERNAL + stages G + H
│   ├── mcp.tool_call                     CLIENT   + one span per CockroachDB MCP read
│   ├── agent.resolver.run                INTERNAL   conditional; absent on the identity-certain path
│   │   └── model.invoke                  CLIENT     node=strong_resolution, tier=R
│   └── memory.proposal.build             INTERNAL
├── memory.kernel.preflight               INTERNAL   before any transaction is opened
├── memory.kernel.transaction             INTERNAL   the serializable commit
│   └── memory.kernel.retry               INTERNAL   one child span per attempt with index >= 1
├── outbox.dispatch                       PRODUCER   one per sweep batch
│   └── outbox.publish                    CLIENT   + one per EventBridge PutEvents call
├── event.consume                         CONSUMER + one per consumer per delivery
├── agent.advocate.run                    CLIENT
│   ├── model.invoke                      CLIENT     node=draft_action, tier=R
│   └── model.invoke                      CLIENT     node=classify_attention_need, tier=R
├── action.intent.create                  INTERNAL
├── action.approve                        INTERNAL
├── action.execute                        INTERNAL
│   ├── action.revalidate                 INTERNAL + the §9.11 revalidation gate
│   └── action.send                       CLIENT   + the SES call, outside the transaction
└── trigger.evaluate                      INTERNAL   worker-rooted; new trace, causation_id set
    └── memory.kernel.transaction         INTERNAL   present only when the predicate is true
```

Parenting rules, because "nesting" is where instrumentation usually goes wrong:

1. `agent.interpreter.run` on the control-plane side is a **client** span whose context is injected into the AgentCore payload. The AgentCore-side root is a **server** span with the same name and the same trace id, and it is the parent of everything the graph does. Two spans, one name, one trace, opposite kinds — this is the standard remote-call shape, and it is what makes the agent's work show up in the same trace as the commit it caused.
2. `memory.kernel.transaction` is **never** a child of `agent.interpreter.run`. The agent proposes; the Kernel decides. The two are siblings under `http.server.request` precisely so that the deterministic/model boundary is visible as a tree shape and not only as an annotation. `15_API_SPEC.md` §8.28's `boundary` block renders the same distinction in the trace DAG.
3. `memory.kernel.retry` spans exist only for attempts after the first. A commit that succeeds on attempt 0 has zero retry children, and `retry_count = 0`. A reader must be able to tell "no retries happened" from "retries were not instrumented", so `memory.kernel.transaction` always carries `provenance.kernel.retry_count` even when it is zero.
4. `event.consume` is a **consumer** span linked to the producing `outbox.publish` span via a span link, not a parent-child edge. Delivery is at-least-once and asynchronous; making it a child would imply a synchronous causal edge that does not exist and would keep the parent open across the queue.
5. `trigger.evaluate` roots its own trace (§2.3). The link back is `causation_id`, carried as the span attribute `provenance.causation_id` and as a span link to the `outbox.publish` span of the `trigger.armed.v1` event when that span is still within retention.

### 3.4 Attribute sets, span by span

Every table below is the **complete** attribute set for that span, in addition to §3.2's universal attributes. An attribute not listed here may not be added without editing this document; an attribute listed here is required unless marked optional.

#### `http.server.request`

| Attribute | Type | Value |
|---|---|---|
| `http.request.method` | string | `GET`, `POST`, `PUT` |
| `http.route` | string | The **template**, e.g. `/v1/cases/{case_id}`, never the filled path. Filled paths are unbounded cardinality and leak ids into span names. |
| `http.response.status_code` | int | |
| `provenance.route_class` | enum | `PUBLIC` \| `INTERNAL` \| `UNAUTHENTICATED` |
| `provenance.principal_kind` | enum | `HUMAN` \| `INTERNAL` \| `NONE` |
| `provenance.client_id` | enum | `provenance-web` \| `provenance-agent-runtime` \| `provenance-workers` \| absent |
| `provenance.capability_kind` | enum, optional | `AGENT_RUN` \| `TRIGGER` \| `ACTION_INTENT` \| `ARTIFACT` \| `INGEST_ALIAS` |
| `provenance.idempotency.scope` | string, optional | The scope string from `15_API_SPEC.md` §6.2 |
| `provenance.idempotency.replayed` | bool, optional | |
| `provenance.error_code` | string, optional | The `error.code` from the §4.3 catalogue. Set on every non-2xx. |
| `provenance.case_revision` | int, optional | Value of the `X-Provenance-Case-Revision` response header |
| `provenance.judge_mode` | bool | `true` when the principal has `judge_mode_enabled` |

#### `artifact.register`

| Attribute | Type | Value |
|---|---|---|
| `provenance.artifact_id` | string (UUID) | |
| `provenance.artifact.source_type` | enum | `EMAIL_INBOUND` \| `UPLOAD_EML` \| `UPLOAD_PDF` \| `UPLOAD_IMAGE` \| `UPLOAD_TEXT` \| `USER_CORRECTION` \| `SEED_FIXTURE` |
| `provenance.artifact.mime_type` | enum | The five values in `ck_source_artifacts_mime` |
| `provenance.artifact.size_bytes` | int | |
| `provenance.artifact.sender_domain` | string, optional | Domain only. The local part is PII and is redacted (§5). |
| `provenance.artifact.duplicate_of` | string (UUID), optional | Set when `uq_source_artifacts_content` deduplicated the upload |
| `provenance.artifact.ses_verdict_pass` | bool, optional | Collapsed from `ses_verdicts`; the raw verdict object stays in the row |

#### `artifact.parse`

| Attribute | Type | Value |
|---|---|---|
| `provenance.artifact_id` | string (UUID) | |
| `provenance.parser.version` | string | e.g. `pdf-text-1` |
| `provenance.parser.status` | enum | `PARSED` \| `PARTIAL` \| `FAILED` \| `UNSUPPORTED_MIME` |
| `provenance.parser.content_blocks` | int | |
| `provenance.parser.attachments` | int | |
| `provenance.parser.quoted_history_blocks` | int | Forwarded-thread quoting is the commonest extraction hazard; counting it is how we find out. |
| `provenance.parser.used_textract` | bool | |

`artifact.textract` adds `provenance.textract.pages` (int) and `provenance.textract.api` (`DetectDocumentText` \| `AnalyzeDocument`). It carries no text.

#### `embedding.generate`

| Attribute | Type | Value |
|---|---|---|
| `provenance.model_id` | const | `amazon.titan-embed-text-v2:0` |
| `provenance.embedding.dimensions` | const int | `1024` |
| `provenance.embedding.version` | string | The frozen version, `v1` (`CANONICAL_DECISIONS.md`) |
| `provenance.embedding.distance` | const | `cosine` |
| `provenance.embedding.requested` | int | Items in the batch |
| `provenance.embedding.generated` | int | Items that required a Bedrock call |
| `provenance.embedding.cache_hits` | int | Served from the `(normalized_text_sha256, embedding_version)` cache |
| `provenance.embedding.body_truncated` | int | Items whose normalised text hit the template's length ceiling |
| `provenance.embedding.input_tokens` | int | For §4.5 cost |

#### `agent.interpreter.run`, `agent.resolver.run`, `agent.advocate.run`

| Attribute | Type | Value |
|---|---|---|
| `provenance.agent_run_id` | string (UUID) | |
| `provenance.graph.name` | enum | `ingestion` \| `advocate` \| `resolver` \| `counterfactual` (the `ck_agent_runs_graph` set) |
| `provenance.graph.version` | string | e.g. `1.3.0` |
| `provenance.graph.node_path` | string | Ordered, comma-joined node names actually visited, e.g. `parse,extract,retrieve,resolve,build_proposal`. Bounded by the graph's node count, so cardinality is bounded. |
| `provenance.agent.status` | enum | `SUCCEEDED` \| `FAILED` \| `ABANDONED` |
| `provenance.agent.error_code` | string, optional | Closed set from `11_CONTRACTS.md` |
| `provenance.agent.model_calls` | int | Against the budget of 8 per artifact |
| `provenance.agent.tool_calls` | int | Against the budget of 50 per run |
| `provenance.agent.repair_attempts` | int | Against the budget of 1 per node |
| `provenance.agent.escalated_to_resolver` | bool | On `agent.interpreter.run` only |
| `provenance.capability.expires_at` | string (RFC 3339) | So a run that ran long enough to lose its capability is visible |

`agent.advocate.run` additionally carries `provenance.draft.claims_validated` (int) and `provenance.draft.claims_unsupported` (int). A non-zero `claims_unsupported` means the grounding gate refused a draft, which is a correct outcome and a quality signal, not an error.

#### `model.invoke`

| Attribute | Type | Value |
|---|---|---|
| `provenance.model_id` | enum | `anthropic.claude-haiku-4-5` \| `anthropic.claude-opus-5`. The `ck_memory_proposals_model` set minus `deterministic.kernel`. |
| `provenance.model.tier` | enum | `E` \| `R` |
| `provenance.model.node` | enum | `extract_structured_evidence` \| `strong_resolution` \| `classify_attention_need` \| `draft_action` \| `counterfactual_summary` |
| `provenance.model.prompt_version` | string | e.g. `pv-extract-1.1.0`, `pv-resolve-1.1.0` |
| `provenance.model.effort` | enum, optional | `low` \| `medium` \| `high` (Opus 5 nodes only; `xhigh` and `max` are not used) |
| `provenance.model.max_tokens` | int | |
| `provenance.model.input_tokens` | int | |
| `provenance.model.output_tokens` | int | |
| `provenance.model.cache_read_input_tokens` | int | Zero on every Tier E call by design (`14_PROMPTS.md` §9.5) |
| `provenance.model.cache_creation_input_tokens` | int | |
| `provenance.model.stop_reason` | enum | `end_turn` \| `max_tokens` \| `tool_use` \| `refusal` |
| `provenance.model.schema_valid_first_pass` | bool | |
| `provenance.model.repair_attempted` | bool | |
| `provenance.model.fallback_from_tier` | enum, optional | Set only on a Tier E → Opus 5 fallback invocation |
| `provenance.model.fence_scrub_hits` | int | Count of `FENCE_BREAKOUT` substitutions (`14_PROMPTS.md` §2). Non-zero is an adversarial-input signal. |

**No prompt text, no completion text, no thinking blocks, no tool arguments.** `thinking.display` stays at its default `omitted` in every deployed configuration, and the span carries counts and versions only.

#### `mcp.tool_call`

| Attribute | Type | Value |
|---|---|---|
| `provenance.mcp.server` | const | `cockroachdb-mcp` |
| `provenance.mcp.tool_name` | string | e.g. `query_agent_evidence_search` |
| `provenance.mcp.view` | enum | One of the five: `agent_case_context_v1`, `agent_active_beliefs_v1`, `agent_belief_lineage_v1`, `agent_evidence_retrieval_v1`, `agent_open_obligations_v1` |
| `provenance.mcp.db_role` | const | `pv_agent_reader` |
| `provenance.mcp.access_mode` | const | `READ_ONLY` |
| `provenance.mcp.arguments_digest` | string, 64 hex | SHA-256 of the canonicalised arguments. Never the arguments, which can echo document text. |
| `provenance.mcp.row_count` | int | |
| `provenance.mcp.truncated` | bool | |
| `provenance.mcp.denied` | bool | A denied call is instrumented and rendered, never swallowed (`G11.5`) |
| `provenance.mcp.error_class` | string, optional | SQL error class only, e.g. `42501`, never the message |

#### `retrieval.identity`, `retrieval.vector`, `retrieval.expand`, `retrieval.rerank`

Common to all four:

| Attribute | Type | Value |
|---|---|---|
| `provenance.retrieval.stages` | string | The `13_RETRIEVAL_SPEC.md` §5 letters this span covers: `A,B,C` \| `D` \| `E,F` \| `G,H` |
| `provenance.retrieval.mode` | enum | `FULL` \| `DISABLED` \| `DEGRADED` |
| `provenance.retrieval.candidates_in` | int | |
| `provenance.retrieval.candidates_out` | int | |
| `provenance.retrieval.degraded_reasons` | string, optional | Comma-joined closed reason codes |

`retrieval.identity` adds:

| Attribute | Type | Value |
|---|---|---|
| `provenance.retrieval.identity_status` | enum | `RESOLVED` \| `AMBIGUOUS` \| `UNRESOLVED` |
| `provenance.retrieval.identity_confidence` | double | |
| `provenance.retrieval.identity_margin` | double | The gap to the runner-up. A high confidence with a small margin is the shape that produces confidently wrong bindings. |
| `provenance.retrieval.exact_identifier_hits` | int | |
| `provenance.retrieval.relationship_candidates` | int | ≤ 12 from stage B |
| `provenance.retrieval.case_candidates` | int | ≤ 12 from stage B |

`retrieval.vector` adds, and this is the span that carries the vector-index evidence:

| Attribute | Type | Value |
|---|---|---|
| `provenance.vector.index_name` | const | `evidence_embedding_ann_idx` |
| `provenance.vector.prefix_column` | const | `user_id`. Filter acceleration on a CockroachDB vector index works only through prefix columns, which is why user isolation is a schema property here and not a query-authoring discipline. |
| `provenance.vector.opclass` | enum | `vector_cosine_ops` \| `vector_l2_ops` — whichever the Phase 0 probe selected and `ops/decisions/VECTOR_INDEX_VARIANT.md` recorded |
| `provenance.vector.beam_size` | int | The session value of `vector_search_beam_size`. The CockroachDB default is 32; Provenance ships the default until the eval harness exists (`13_RETRIEVAL_SPEC.md` §16.2), and recording it means a change is visible in telemetry rather than only in a config diff. |
| `provenance.vector.corpus_size_user_scoped` | int | Rows in the user's partition |
| `provenance.vector.overfetch_limit` | int | The raw `LIMIT` before post-filtering |
| `provenance.vector.raw_candidates` | int | Rows the ANN scan returned |
| `provenance.vector.postfilter_survivors` | int | After `tenant_id`, `retraction_status = 'ACTIVE'`, `embedding_version` |
| `provenance.vector.retracted_excluded` | int | The retraction filter's actual bite. Zero forever on a corpus that contains retraction fixtures is a bug, not a success. |
| `provenance.vector.cross_user_rows` | int | Must be `0`. Post-hoc audited per row (`13_RETRIEVAL_SPEC.md` §15.5). |
| `provenance.vector.overfetch_exhausted` | bool | The over-fetch multiplier was too small and the result set is short |
| `provenance.vector.index_used` | bool | From the plan; `false` means a full scan and is an alarm, not a curiosity |

`retrieval.expand` adds `provenance.retrieval.grounding_edges_walked` (int), `provenance.retrieval.evidence_pulled_via_grounding` (int) — evidence reachable **only** through `belief_support` edges, which is the number that shows grounding-graph expansion is doing work — plus `provenance.retrieval.beliefs`, `.conflicts`, `.commitments` (ints).

`retrieval.rerank` adds `provenance.retrieval.abstained` (bool), `provenance.retrieval.dropped_for_budget` (int), `provenance.retrieval.tier_counts` (string, e.g. `T1=2,T2=4,T3=4`), and `provenance.retrieval.final_evidence` (int, ≤ 10).

Read across the four spans, `candidates_in`/`candidates_out` give the full funnel: user corpus → stage B identity candidates → stage D raw → post-filter survivors → stage E validated → stage F expanded → stage G ranked → stage H bounded context. That funnel is what `15_API_SPEC.md` §8.28's `RETRIEVAL` node summarises as `"16,035 user-scoped vectors → 20 ANN candidates → 7 semantic → 1 exact match"`, and it is assembled from these attributes rather than written by hand.

#### `memory.proposal.build` and `memory.kernel.preflight`

| Attribute | Type | Value |
|---|---|---|
| `provenance.proposal_id` | string (UUID) | |
| `provenance.proposal.schema_version` | string | |
| `provenance.proposal.type` | enum | `INGESTION_INTERPRETATION` \| `USER_CORRECTION` \| `FULFILLMENT_ADMISSION` \| `TRIGGER_EVALUATION` \| `SYSTEM_DERIVATION` \| `SEED_FIXTURE` |
| `provenance.proposal.claims` | int | |
| `provenance.proposal.belief_mutations` | int | |
| `provenance.proposal.commitment_mutations` | int | |
| `provenance.proposal.conflict_hints` | int | |
| `provenance.proposal.trigger_mutations` | int | |
| `provenance.proposal.evidence_ids` | int | Count, not the ids |
| `provenance.proposal.payload_sha256` | string, 64 hex | The dedupe key behind `uq_memory_proposals_payload` |

`memory.kernel.preflight` adds `provenance.kernel.preflight_result` (`PASSED` \| `REJECTED`), `provenance.kernel.reason_codes` (comma-joined closed set), and `provenance.kernel.transaction_opened` (bool). The last one is load-bearing: `G4.4` asserts that foreign evidence is refused **before** a transaction opens, and this attribute is how that is observable in production rather than only in a test.

#### `memory.kernel.transaction` — the span the whole architecture is about

| Attribute | Type | Value |
|---|---|---|
| `provenance.kernel_decision_id` | string (UUID) | Allocated before the transaction opens |
| `provenance.proposal_id` | string (UUID) | |
| `provenance.case_id` | string (UUID), optional | Absent on a proposal that resolves to no case |
| `provenance.kernel.decision` | enum | The nine values of `ck_kernel_decisions_decision` |
| `provenance.kernel.reason_codes` | string | Comma-joined, closed set |
| `provenance.kernel.isolation` | const | `SERIALIZABLE` |
| **`provenance.kernel.retry_count`** | int | 0–5. Always present, including when zero. |
| `provenance.kernel.attempts` | int | `retry_count + 1` |
| `provenance.kernel.sqlstate_40001_count` | int | Retries attributable to serialization failures specifically, as opposed to other retryable classes |
| **`provenance.kernel.case_revision_before`** | int, optional | Null only when the decision touches no case |
| **`provenance.kernel.case_revision_after`** | int, optional | Equals `before` on a no-op or rejection, `before + 1` on a commit. The `ck_kernel_decisions_revision_step` constraint enforces it; the span makes it observable. |
| `provenance.kernel.aggregate_advanced` | bool | `case_revision_after > case_revision_before` |
| `provenance.kernel.rows_written` | int | Attributable rows (§6.5) |
| `provenance.kernel.belief_versions_written` | int | |
| `provenance.kernel.support_edges_written` | int | grounding edges |
| `provenance.kernel.conflicts_opened` | int | |
| `provenance.kernel.state_transitions_written` | int | |
| `provenance.kernel.outbox_events_written` | int | |
| `provenance.kernel.commit_duration_ms` | int | Time inside the final successful attempt only, excluding backoff sleeps |
| `provenance.kernel.total_duration_ms` | int | Including every failed attempt and every backoff |

`memory.kernel.retry` (one child per attempt after the first): `provenance.kernel.attempt_index` (int, ≥ 1), `provenance.kernel.sqlstate` (string, e.g. `40001`), `provenance.kernel.backoff_ms` (int), `provenance.kernel.retryable` (bool).

The reason `commit_duration_ms` and `total_duration_ms` are separate: under contention on one hot case, wall-clock latency is dominated by backoff, and conflating the two makes a retry storm look like a slow database. `05_RELIABILITY_EVAL_DEMO.md` §4 fixes the retry budget at five attempts with exponential backoff and jitter; these two numbers are how you see whether the budget is the right size.

No network client may be constructed or called inside the transaction callback (`tools/txn_purity_lint`, `G3.5`), so this span never has an HTTP or Bedrock child. If one ever appears, that is the lint failing in production and it is worth an alarm on its own.

#### `outbox.dispatch`, `outbox.publish`, `event.consume`

| Span | Attribute | Type | Value |
|---|---|---|---|
| `outbox.dispatch` | `provenance.outbox.worker_id` | string | e.g. `outbox-dispatch-1a2b3c` |
| | `provenance.outbox.batch_size` | int | Requested |
| | `provenance.outbox.claimed` | int | |
| | `provenance.outbox.dispatched` | int | |
| | `provenance.outbox.failed_retryable` | int | |
| | `provenance.outbox.dead` | int | |
| | `provenance.outbox.reaped_stale_claims` | int | |
| | `provenance.outbox.oldest_pending_age_seconds` | int | |
| | `provenance.outbox.trigger` | enum | `IMMEDIATE` \| `SCHEDULED` \| `MANUAL` (the three paths in `15_API_SPEC.md` §13.6) |
| `outbox.publish` | `provenance.event_id` | string (UUID) | |
| | `provenance.event.type` | enum | One of the 25 in `ck_outbox_events_event_type` |
| | `provenance.event.aggregate_type` | enum | `CASE` \| `RELATIONSHIP` \| `ACTION` \| `TRIGGER` \| `ARTIFACT` |
| | `provenance.event.aggregate_version` | int | |
| | `provenance.event.attempt_count` | int | |
| | `provenance.event.bus` | string | EventBridge bus name |
| `event.consume` | `provenance.consumer_name` | enum | `advocate_dispatch` \| `notification_dispatch` \| `action_execute` |
| | `provenance.event_id` | string (UUID) | |
| | `provenance.event.type` | enum | |
| | `provenance.consume.result` | enum | `PROCESSED` \| `DUPLICATE_NOOP` \| `SKIPPED_STALE` \| `FAILED` |
| | `provenance.consume.effect_kind` | string, optional | e.g. `AGENT_RUN_STARTED` |
| | `provenance.consume.lag_ms` | int | `now() - event.occurred_at`. The honest end-to-end async latency. |

#### `action.intent.create`, `action.approve`, `action.execute`, `action.revalidate`, `action.send`

| Span | Attribute | Type | Value |
|---|---|---|---|
| all five | `provenance.action_intent_id` | string (UUID) | |
| | `provenance.action.type` | enum | The five values of `ck_action_intents_type` |
| | `provenance.case_id` | string (UUID) | |
| `action.intent.create` | `provenance.action.risk_tier` | int | 0–3; tier 4 is refused by `ck_action_intents_tier4_blocked` |
| | `provenance.action.supporting_belief_versions` | int | |
| | `provenance.action.draft_sha256` | string, 64 hex | The hash, never the draft |
| | `provenance.action.recipient_domain` | string | Domain only |
| | `provenance.action.recipient_allowlisted` | bool | |
| `action.approve` | `provenance.action.basis_case_revision` | int | |
| | `provenance.action.case_revision_after` | int | Approval is itself a canonical change and advances the revision (`15_API_SPEC.md` §17.3) |
| | `provenance.action.approval_draft_sha256` | string, 64 hex | |
| | `provenance.action.time_to_approval_ms` | int | From intent creation. The human-in-the-loop cost, measured. |
| `action.revalidate` | `provenance.revalidate.case_revision_expected` | int | |
| | `provenance.revalidate.case_revision_actual` | int | |
| | `provenance.revalidate.draft_hash_match` | bool | |
| | `provenance.revalidate.support_still_current` | bool | |
| | `provenance.revalidate.recipient_allowlisted` | bool | |
| | `provenance.revalidate.result` | enum | `PASSED` \| `STALE` |
| | `provenance.revalidate.stale_reason` | string, optional | Closed set |
| `action.execute` | `provenance.action_execution_id` | string (UUID) | |
| | `provenance.action.attempt_no` | int | 1–5 |
| | `provenance.action.status` | enum | `STARTED` \| `SUCCEEDED` \| `FAILED_RETRYABLE` \| `FAILED_FINAL` \| `ABORTED_STALE` |
| `action.send` | `provenance.action.provider` | enum | `SES` \| `SAFE_SINK` \| `SIMULATOR` |
| | `provenance.action.provider_correlation_id` | string, optional | SES message id |
| | `provenance.action.execution_mode` | enum | `ENABLED` \| `DISABLED` — the `PV_ACTION_EXECUTION_MODE` kill switch, visible in telemetry so a demo cannot silently run with sends disabled |

`action.send` is the only span in the system whose failure is irreversible in the other direction: a message already sent. It is a child of `action.execute` and a sibling of `action.revalidate`, and it is started **after** the revalidation transaction commits, never inside it.

#### `trigger.evaluate`

| Attribute | Type | Value |
|---|---|---|
| `provenance.trigger_id` | string (UUID) | |
| `provenance.trigger_evaluation_id` | string (UUID) | Derived per §2.1 |
| `provenance.trigger.type` | enum | `COMMITMENT_DEADLINE` \| `RESPONSE_DEADLINE` \| `CONFLICT_TIMEOUT` \| `WARRANTY_WINDOW` |
| `provenance.trigger.evaluation_version` | int | |
| `provenance.trigger.scheduled_for` | string (RFC 3339) | What the scheduler thought |
| `provenance.trigger.evaluated_at` | string (RFC 3339) | When the predicate actually ran |
| `provenance.trigger.wake_lateness_ms` | int | The gap. Prospective memory that fires late is a product defect, not an infrastructure detail. |
| `provenance.trigger.result` | enum | `FIRED` \| `NO_OP` \| `DISARMED` \| `EXPIRED` \| `ERROR` |
| `provenance.trigger.reason_code` | enum | Exactly one member of the result-specific closed registry in `ck_prospective_triggers_last_reason` |
| `provenance.trigger.state_after` | enum | `ARMED` \| `FIRED` \| `DISARMED` \| `EXPIRED` |
| `provenance.trigger.basis_case_revision` | int | |
| `provenance.trigger.case_revision_at_evaluation` | int | The predicate is evaluated against **current** state, so these two differing is normal and expected |
| `provenance.trigger.predicate_fields_read` | int | Count of projection fields the AST touched |
| `provenance.trigger.predicate_true` | bool | |

The wake is never treated as proof that the condition holds. `provenance.trigger.result = NO_OP` with `reason_code = PREDICATE_FALSE` is the second-most-important observable in the demo: it is what proves the deposit trigger and the false-predicate no-op both run the same code path, with no hidden state revert (`CANONICAL_DECISIONS.md`, trigger demonstration).

### 3.5 Status, errors, and events

- Span status is `ERROR` **only** when the operation failed to complete. A `409 ACTION_STALE`, a `NO_OP` trigger, a `DUPLICATE_NOOP` consume, and a `REJECTED_INVARIANT` kernel decision are all successful operations with negative outcomes. Marking them `ERROR` would make the error rate meaningless within a day.
- Exceptions are recorded with `span.record_exception(exc)` **after** redaction (§5.3). The exception type and the redacted message are recorded; the traceback is recorded only when `deployment.environment != "prod"`.
- Span events (as opposed to child spans) are used for four things and nothing else: `kernel.retry_scheduled`, `model.schema_repair_attempted`, `mcp.call_denied`, `redaction.value_masked`. Each carries only the attributes already defined above.

### 3.6 Sampling

Head-based, deterministic on `trace_id`, configured by `PV_TRACE_SAMPLE_RATIO`.

| Flow | Ratio | Why |
|---|---|---|
| Any flow that reaches `memory.kernel.transaction` | 1.0 | Every canonical commit is traced. Sampling the thing the product exists to do would be indefensible. |
| Any flow that reaches `action.execute` | 1.0 | External side effects are always traced. |
| Any flow whose span status is `ERROR` | 1.0 | Retained by a tail rule in the exporter: an errored span forces its whole trace to be kept. |
| Judge Mode requests (`provenance.judge_mode = true`) | 1.0 | |
| `GET /v1/healthz`, `GET /v1/version` | 0.0 | Never traced. App Runner health checks would otherwise dominate the log group. |
| All other read traffic | 0.1 default, 1.0 in `demo` | Read traffic is the only volume worth sampling at this scale, and on demo day there is no volume. |

Because `demo` runs at 1.0 across the board, nothing in a recorded walkthrough depends on a sampling decision. That is the point.

---

## 4. The metric catalogue

### 4.1 Naming, namespaces, and the cardinality rule

**Instrument name.** Every OpenTelemetry instrument is named `provenance.<area>.<leaf>`. The area segment is a closed set: `api`, `auth`, `artifact`, `memory`, `kernel`, `agent`, `model`, `embedding`, `retrieval`, `db`, `outbox`, `events`, `trigger`, `action`, `state_proof`, `cost`, `judge`, `trace`, `eval`.

**CloudWatch mapping.** The EMF writer maps `provenance.<area>.<leaf>` to namespace `Provenance/<Area>` and metric name `<leaf>`. So `provenance.retrieval.latency_ms` is namespace `Provenance/Retrieval`, metric `latency_ms` — which is exactly the namespace `13_RETRIEVAL_SPEC.md` §15.5 specifies. That document writes the instrument names without the `provenance.` prefix; the prefix is implied. **There is one metric, not two.** The same reconciliation applies to `15_API_SPEC.md` §13.8's `provenance.outbox.*` and §8.11's `provenance.state_proof.ungrounded_belief`, which are already fully qualified and are used verbatim.

**The cardinality rule, which is not negotiable:**

> A value may be a metric dimension only if its value space is a closed enum declared in `provenance_contracts` or `provenance_domain`. Everything else is a log field or a span attribute.

That rule forbids `user_id`, `tenant_id`, `case_id`, `trace_id`, `artifact_id`, `sender_domain`, `recipient`, `error message text`, and `graph node path` as dimensions. It permits `stage`, `mode`, `tier`, `model_id`, `node`, `graph`, `decision`, `reason_code`, `result`, `status`, `event_type`, `consumer_name`, `view`, `role`, `bucket`, `sqlstate`, `capability_kind`, `error_code`. At most four dimensions per metric.

The reason is not cost, though CloudWatch charges per unique dimension combination and a `user_id` dimension on a metric emitted per request would be an unbounded bill. The reason is that a metric with an unbounded dimension is unqueryable: you cannot alarm on it, you cannot graph it, and the value you wanted was always available in the logs anyway. §9 shows how per-user and per-counterparty questions are answered from Log Insights, which is the correct surface for them.

**Instrument types.** `Counter` is monotonic. `UpDownCounter` moves both ways. `Histogram` records a distribution. `ObservableGauge` is sampled by a callback on a fixed interval — used for every metric whose value is a `SELECT count(*)`, because those are properties of the world rather than events, and re-deriving them from event deltas would drift the first time a seed or a migration ran.

The gauge callbacks run in one background task in the control plane every 60 seconds, as a single batched read against `pv_app_reader_writer`:

```python
# packages/python/provenance_telemetry/gauges.py
GAUGE_SQL: Final = """
SELECT
  (SELECT count(*) FROM cases
     WHERE status IN ('OPEN','WAITING','ACTIONABLE','IN_PROGRESS',
                      'DISPUTED','BLOCKED','AWAITING_USER','REOPENED'))     AS active_cases,
  (SELECT count(*) FROM cases WHERE status = 'REOPENED')                    AS reopened_cases,
  (SELECT count(*) FROM cases WHERE attention_level = 'URGENT')             AS urgent_cases,
  (SELECT count(*) FROM commitments
     WHERE status IN ('ACTIVE','PARTIAL','DISPUTED'))                       AS unresolved_commitments,
  (SELECT coalesce(sum(outstanding_amount), 0) FROM commitments
     WHERE status IN ('ACTIVE','PARTIAL','DISPUTED'))                       AS outstanding_total,
  (SELECT count(*) FROM commitments
     WHERE status IN ('ACTIVE','PARTIAL') AND due_at < now())               AS overdue_commitments,
  (SELECT count(*) FROM conflicts WHERE status IN ('OPEN','NEEDS_HUMAN'))   AS active_conflicts,
  (SELECT count(*) FROM conflicts
     WHERE status IN ('OPEN','NEEDS_HUMAN') AND requires_human)             AS conflicts_awaiting_human,
  (SELECT count(*) FROM prospective_triggers WHERE state = 'ARMED')         AS triggers_armed,
  (SELECT count(*) FROM action_intents WHERE status = 'PROPOSED')           AS intents_awaiting_approval,
  (SELECT count(*) FROM outbox_events
     WHERE status IN ('PENDING','FAILED_RETRYABLE'))                        AS outbox_pending,
  (SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
     FROM outbox_events WHERE status IN ('PENDING','FAILED_RETRYABLE'))     AS outbox_oldest_age_s,
  (SELECT count(*) FROM outbox_events WHERE status = 'DEAD')                AS outbox_dead,
  (SELECT count(*) FROM memory_proposals
     WHERE status IN ('PENDING_IDENTITY','PENDING_HUMAN_REVIEW'))           AS proposals_pending
"""
```

One statement, fourteen scalars, once a minute. At demo scale this is free; the query is listed here rather than described so that nobody implements fourteen separate round trips.

### 4.2 Product and memory metrics — namespace `Provenance/Memory`, `Provenance/Trigger`, `Provenance/Action`

| Metric | Instrument | Labels | The decision it informs | Alarm |
|---|---|---|---|---|
| `provenance.memory.active_cases` | ObservableGauge | — | Is there anything to demo? Is the seed loaded? A zero here on the demo stack means the dashboard will be empty on camera. | `< 1` for 5 min on `demo` |
| `provenance.memory.reopened_cases` | ObservableGauge | — | The hero outcome, as a number. Rising means the contradiction detector is finding things. | none |
| `provenance.memory.urgent_cases` | ObservableGauge | — | Whether attention classification is producing anything actionable, or classifying everything as `NONE` | none |
| `provenance.memory.unresolved_commitments` | ObservableGauge | — | The size of the wedge. If this trends to zero the product has nothing to do. | none |
| `provenance.memory.overdue_commitments` | ObservableGauge | — | Whether prospective triggers are keeping up with deadlines that have actually passed | `> 0` and `provenance.trigger.armed == 0` for 15 min: deadlines exist and nothing is armed to catch them |
| `provenance.memory.outstanding_total` | ObservableGauge | `currency` | The money at stake, which is segment A of the video and the real-world-impact claim | none |
| `provenance.memory.active_conflicts` | ObservableGauge | — | Contradiction load. A conflict count that only rises means resolution is never happening. | `> 20` for 1 h on `demo` (seed is ~1) |
| `provenance.memory.conflicts_awaiting_human` | ObservableGauge | — | The human queue depth | none |
| `provenance.memory.evidence_admitted` | Counter | `evidence_type` | Append-only evidence growth; the denominator for extraction quality | none |
| `provenance.memory.claims_recorded` | Counter | `claim_kind` | Whether counterparty statements are being typed as `COUNTERPARTY_CLAIM` rather than as fact. A rising `OBSERVATION` share on inbound counterparty mail is a classification regression. | none |
| `provenance.memory.belief_versions_written` | Counter | `epistemic_status` | Lineage growth, and how often a status changes without a value change | none |
| `provenance.memory.grounding_edges_written` | Counter | `relation` (`SUPPORTS`/`CONTRADICTS`/`QUALIFIES`) | Whether grounding is real or whether every belief has exactly one support edge added mechanically | none |
| `provenance.memory.artifact_to_commit_ms` | Histogram | `source_type`, `decision` | **Artifact-to-commit latency.** `kernel_decisions.committed_at − source_artifacts.received_at`, emitted by the Kernel at commit. The single number that describes "how long until the record is right". | p95 `> 45000` for 10 min |
| `provenance.trigger.armed` | ObservableGauge | — | Prospective memory inventory | `== 0` for 1 h while `overdue_commitments > 0` |
| `provenance.trigger.evaluated` | Counter | `result`, `trigger_type` | The fire/no-op ratio. Both values must be non-zero across a demo: a system that only fires is not evaluating, and a system that only no-ops is not useful. | `result=ERROR` `> 0` in 15 min |
| `provenance.trigger.wake_lateness_ms` | Histogram | `trigger_type` | Scheduler health. A trigger that fires 40 minutes late still fires, but the demo depends on it firing when the script says it will. | p95 `> 300000` |
| `provenance.action.intent_transition` | Counter | `to_status` (`PROPOSED`/`APPROVED`/`REJECTED`/`EXECUTED`/`CANCELLED_STALE`/`FAILED_FINAL`) | **Action intents proposed / approved / rejected**, as one counter with a closed label rather than three counters that can drift apart. Approval rate = `APPROVED / PROPOSED`; rejection rate = `REJECTED / PROPOSED`. | `to_status=FAILED_FINAL` `≥ 1` in 5 min |
| `provenance.action.rejection_reason` | Counter | `reason` (`WRONG_TONE`/`WRONG_FACTS`/`NOT_NOW`/`OTHER`) | Whether rejections are stylistic or factual. `WRONG_FACTS` is the one that means the **memory** is wrong, not the draft. | `reason=WRONG_FACTS` share `> 0.25` over 24 h |
| `provenance.action.aborted_stale` | Counter | `stale_reason` | The revalidation gate doing its job. Non-zero is correct behaviour; a sustained high rate means approvals are racing commits. | rate `> 0.2` of executions over 1 h → `provenance-action-abort-rate` |
| `provenance.action.time_to_approval_ms` | Histogram | `action_type` | How long a human takes. Product learning, not an SLO. | none |
| `provenance.state_proof.ungrounded_belief` | Counter | — | A canonical belief version rendered with zero grounding edges. The Kernel refuses to create one (`422 PROPOSAL_UNGROUNDED_BELIEF`), so this is a data-integrity alarm, not a rendering concern (`15_API_SPEC.md` §8.11). | **`≥ 1` ever — P1** |

### 4.3 Agent quality — namespace `Provenance/Agent`, `Provenance/Model`

Rates are computed as CloudWatch metric-math expressions over these counters rather than emitted as pre-divided gauges, because a pre-divided rate cannot be re-aggregated across a time window without lying.

| Metric | Instrument | Labels | The decision it informs | Alarm |
|---|---|---|---|---|
| `provenance.agent.runs` | Counter | `graph`, `status` | Denominator for every agent rate | none |
| `provenance.agent.extraction_schema_invalid` | Counter | `node`, `repaired` (bool) | **Extraction schema-invalid rate** = `extraction_schema_invalid{node=extract_structured_evidence} / model.invocations{node=extract_structured_evidence}`. Rising means the prompt, the schema, or the model changed under us. | rate `> 0.10` over 30 min |
| `provenance.agent.resolver_escalation` | Counter | `reason` (`AMBIGUOUS_IDENTITY`/`CONTRADICTION_SUSPECTED`/`LOW_CONFIDENCE`) | **Resolver escalation rate** = `resolver_escalation / agent.runs{graph=ingestion}`. Tier R is the expensive tier; this is the cost dial and the ambiguity signal at once. | rate `> 0.60` over 1 h (cost), or `== 0` over 1 h with the ambiguous eval fixture present (routing broken) |
| `provenance.agent.proposal_decision` | Counter | `decision` (the nine `ck_kernel_decisions_decision` values) | **Proposal acceptance rate** = `(ACCEPTED + ACCEPTED_WITH_CONFLICT) / Σ`. **Pending identity rate** = `PENDING_IDENTITY / Σ`. One counter, two rates, no drift. | `decision=REJECTED_INVALID_PROVENANCE` `≥ 1` — see below |
| `provenance.agent.pending_human_review` | Counter | `origin` (`TIER_E_EXHAUSTED`/`TIER_R_FAILED`/`MODEL_REFUSAL`/`BUDGET_EXCEEDED`) | Where the human queue comes from. Fallback exhaustion and refusals are different problems with different fixes. | `origin=MODEL_REFUSAL` `≥ 1` in 1 h |
| `provenance.agent.unsupported_draft_claim` | Counter | `node` | **Unsupported draft claim rate** = `unsupported_draft_claim / action.intent_transition{to_status=PROPOSED}`. Every factual sentence must carry a support id; this counts the sentences that did not. | rate `> 0.15` over 1 h |
| `provenance.agent.tool_budget_exceeded` | Counter | `budget` (`model_calls`/`tool_calls`/`repair_attempts`/`embedding_calls`) | Which budget is actually binding | `> 0` sustained on `model_calls` |
| `provenance.agent.fence_scrub_hits` | Counter | `classification` (`FENCE_BREAKOUT`) | Adversarial input reaching the prompt boundary. The artifact is still admitted as evidence; the injection is contained architecturally, and this counts the attempts. | `> 0` — investigate, do not page |
| `provenance.model.invocations` | Counter | `model_id`, `tier`, `node` | Denominator for everything above, and the numerator for §4.5 cost | none |
| `provenance.model.fallback` | Counter | `from_tier`, `to_model_id`, `reason` | **Model fallback rate** = `fallback / model.invocations`. The Tier E policy is one schema-repair attempt then one Opus 5 fallback at low effort; Tier R never downgrades. A Tier R fallback event would mean the router violated `CANONICAL_DECISIONS.md`. | any `from_tier=R` — P1, contract violation. `from_tier=E` rate `> 0.20` over 1 h — cost |
| `provenance.model.refusal` | Counter | `model_id`, `node` | `stop_reason == "refusal"` arrives as HTTP 200 and must never be mistaken for content | `≥ 1` in 1 h |
| `provenance.model.latency_ms` | Histogram | `model_id`, `node` | Whether the effort settings in `14_PROMPTS.md` §9.4 are still paying for themselves | p95 `> 20000` on any `tier=R` node |
| `provenance.model.cache_read_input_tokens` | Counter | `model_id`, `node` | Prompt caching is verified, not assumed. Sustained zero on an Opus 5 node means something is silently invalidating the stable prefix. | `== 0` for 1 h on any `model_id=anthropic.claude-opus-5` node while `model.invocations > 0` |
| `provenance.model.cache_creation_input_tokens` | Counter | `model_id`, `node` | Distinguishes "cache never warmed" from "cache warmed then invalidated" | none |
| `provenance.model.schema_repair` | Counter | `node`, `succeeded` | Whether the single repair attempt is worth its latency | none |

`provenance.agent.proposal_decision{decision=REJECTED_INVALID_PROVENANCE}` deserves its own sentence. `15_API_SPEC.md` §3.6 states that a `MemoryProposal.user_id` mismatch is a tripwire, because a correct system can never produce one. The same is true of foreign evidence references. Any non-zero value is either a bug of the exact class the capability model exists to prevent, or an attack. It pages.

### 4.4 Retrieval — namespace `Provenance/Retrieval`

The runtime metrics below extend `13_RETRIEVAL_SPEC.md` §15.5 and keep its names and thresholds unchanged.

| Metric | Instrument | Labels | The decision it informs | Alarm |
|---|---|---|---|---|
| `provenance.retrieval.latency_ms` | Histogram | `stage` (`A`…`H`, `TOTAL`), `mode` | Which stage is slow. **ANN latency is `stage=D`**, whose p95 budget is 60 ms against a 210 ms total. | p95 `stage=TOTAL` `> 400` for 5 min; p95 `stage=D` `> 250` for 5 min |
| `provenance.retrieval.candidates` | Histogram | `stage`, `direction` (`in`/`out`) | **Candidate counts before and after rerank** are `stage=G,direction=in` and `stage=G,direction=out`. The whole funnel is one query across stages. | none (diagnostic) |
| `provenance.retrieval.identity.status` | Counter | `status` (`RESOLVED`/`AMBIGUOUS`/`UNRESOLVED`) | Abstention behaviour in production, where there are no gold labels | `UNRESOLVED` share `> 0.30` over 1 h |
| `provenance.retrieval.identity.exact_match_count` | Counter | — | **Exact-identifier hit rate** in production = `exact_match_count / retrieval invocations whose artifact contained a reference identifier`. The eval target is 1.00 because it measures determinism, not quality. A sustained drop is the earliest warning that a counterparty changed its invoice format. | `== 0` over 1 h with traffic present |
| `provenance.retrieval.vector.postfilter_survival_ratio` | Histogram | — | Whether the over-fetch multiplier is large enough that post-filtering does not starve the result set | p5 `< 0.33` |
| `provenance.retrieval.vector.overfetch_exhausted` | Counter | — | The over-fetch ran out and the result is short | any occurrence |
| `provenance.retrieval.vector.index_used` | Counter | `used` (bool) | `EXPLAIN` says the ANN index was used. `used=false` means a full scan, which may still return correct results and is still a failure (`G6.2`). | `used=false` `> 0` |
| `provenance.retrieval.retracted_leakage` | Counter | — | Retracted, superseded, or quarantined evidence reached a caller. Only acceptable value is zero. | **`> 0` ever — page** |
| `provenance.retrieval.cross_user_rows` | Counter | — | ANN or rerank returned another user's row. Only acceptable value is zero, and the agent run fails closed. | **`> 0` ever — page** |
| `provenance.retrieval.context.dropped_for_budget` | Counter | `reason` | What the bounded `RetrievalContext` had to discard | sustained drops of `T3_VECTOR_ONLY` |
| `provenance.retrieval.embedding.body_truncated` | Counter | — | Extraction is producing non-atomic evidence that does not fit the embedding template | `> 0.10` of items |
| `provenance.retrieval.degraded` | Counter | `reason` | Which degradation path ran | any `EMBEDDING_UNAVAILABLE` |
| `provenance.retrieval.mcp.tool_calls` | Counter | `view`, `denied` | Which agent-safe views are load-bearing, and whether any call was refused | `denied=true` `> 0` |
| `provenance.embedding.generated` | Counter | `model_id` | **Embeddings generated** | none |
| `provenance.embedding.cache_hit` | Counter | `model_id` | **Embeddings served from cache.** Cache hit ratio = `cache_hit / (cache_hit + generated)`. The cache is a cost optimisation, never a correctness dependency: `G6.6` asserts that clearing it recomputes an identical vector. | none |

**Ranking quality is measured, not monitored.** Recall@1, Recall@3, Recall@10, MRR, gold-set completeness, `must_not_include` violations, abstention precision/recall, and `harmful_confidence_rate` all require hand-labelled gold answers. They are emitted by the eval harness, not by the runtime, into namespace `Provenance/Eval`:

| Metric | Instrument | Labels | Decision | Threshold |
|---|---|---|---|---|
| `provenance.eval.retrieval.recall_at_1` | ObservableGauge (one value per harness run) | `dataset_version`, `git_sha` | Is the rerank ordering correctly? | `< 0.85` fails `G6.5`; the spec floor in `13_RETRIEVAL_SPEC.md` §15.2 is `0.75` and the gate strengthens it |
| `provenance.eval.retrieval.recall_at_3` | ObservableGauge | same | Is candidate generation finding the document at all? | `< 0.95` fails `G6.5`; spec floor `0.90` |
| `provenance.eval.retrieval.recall_at_10` | ObservableGauge | same | Recall problem versus ranking problem | `< 0.95` |
| `provenance.eval.retrieval.mrr` | ObservableGauge | same | Gold item present but buried = weighting problem | `< 0.80` |
| `provenance.eval.retrieval.must_not_include_violations` | ObservableGauge | same | Retraction or scoping failure | **`> 0` fails the gate** |
| `provenance.eval.retrieval.harmful_confidence_rate` | ObservableGauge | same | A confidently wrong binding is a memory-integrity failure | **`> 0.00` blocks release** |

Emitting these from CI to CloudWatch, dimensioned by `git_sha`, means the quality trend across the build is a graph rather than a memory. The honest caveat from `13_RETRIEVAL_SPEC.md` §15.1 travels with them and must be repeated wherever they are shown: at n = 40 and a rate near 0.90 the 95% confidence interval is roughly ±9 points, so any measured difference under 10 points is noise.

### 4.5 Database — namespace `Provenance/Db`, `Provenance/Outbox`, `Provenance/Events`

| Metric | Instrument | Labels | The decision it informs | Alarm |
|---|---|---|---|---|
| `provenance.db.transaction_duration_ms` | Histogram | `txn_name`, `role`, `outcome` (`COMMITTED`/`ROLLED_BACK`/`RETRY_EXHAUSTED`) | **Transaction latency.** `txn_name` is a closed set of named transactions (`kernel_commit`, `trigger_fire`, `action_approve`, `action_revalidate`, `outbox_claim`, `consumer_dedupe`), so the label is bounded. | p95 `txn_name=kernel_commit` `> 2000` for 10 min |
| `provenance.db.serialization_retry` | Counter | `txn_name`, `sqlstate` | **SQLSTATE 40001 retry count.** Retries are normal under contention on one hot case; a rate change is the signal, not the presence. | rate `> 0.5` retries per commit over 15 min → `provenance-kernel-retry-rate` |
| `provenance.db.retry_exhausted` | Counter | `txn_name` | The five-attempt budget ran out and the caller got `503 RETRYABLE_CONCURRENCY` | `≥ 1` in 5 min |
| `provenance.db.query_duration_ms` | Histogram | `query_name`, `role` | Named read queries: `dashboard`, `state_proof`, `case_timeline`, `memory_trace`, `ann_search`, `gauge_sweep` | p95 `query_name=state_proof` `> 800` for 10 min |
| `provenance.db.pool_in_use` | ObservableGauge | `role` | **Pool utilisation** numerator. One pool per SQL role, so the role boundary is a runtime fact (`G3.1`). | none |
| `provenance.db.pool_size` | ObservableGauge | `role` | Denominator. Utilisation = `pool_in_use / pool_size`. | utilisation `> 0.85` for 5 min on any role |
| `provenance.db.pool_wait_ms` | Histogram | `role` | Whether the pool is the bottleneck rather than the query | p95 `> 250` for 5 min |
| `provenance.db.connection_errors` | Counter | `role`, `sqlstate` | Cluster reachability | `≥ 3` in 5 min |
| `provenance.outbox.pending` | ObservableGauge | — | Backlog size | none |
| `provenance.outbox.oldest_pending_age_seconds` | ObservableGauge | — | **Outbox pending age.** Dispatcher stalled or EventBridge degraded. | `> 120` → `provenance-outbox-pending-age` |
| `provenance.outbox.dead_count` | ObservableGauge | — | An event will never be delivered | **`≥ 1` in 5 min — P1** |
| `provenance.outbox.dispatched` | Counter | `event_type` | Throughput, and which event types actually flow | none |
| `provenance.outbox.attempt_count_p99` | ObservableGauge | — | Publish failures becoming routine | `> 2` |
| `provenance.outbox.reaped_stale_claims` | Counter | — | Dispatchers crashing mid-publish | `> 0` sustained |
| `provenance.events.consumed` | Counter | `consumer_name`, `result` | Consumer health and the `SKIPPED_STALE` rate | `result=FAILED` `≥ 3` in 5 min |
| `provenance.events.duplicate` | Counter | `consumer_name` | Healthy at low rates; it proves the dedupe ledger is exercised rather than dormant | informational; alarm only if `== 0` across a full demo run, which would mean at-least-once was never tested |
| `provenance.events.consume_lag_ms` | Histogram | `consumer_name` | End-to-end async latency from commit to effect | p95 `> 30000` |
| `provenance.events.dlq_depth` | (AWS-native) | `QueueName` | **DLQ depth.** Sourced from `AWS/SQS` `ApproximateNumberOfMessagesVisible` on `provenance-events-dlq` rather than re-published by us: republishing an AWS metric adds a failure mode and subtracts nothing. | `≥ 1` → `provenance-dlq-depth`, P1 |
| `provenance.auth.tenant_mismatch` | Counter | `route_class` | A cross-tenant access attempt, logged at WARN with `reason: "TENANT_MISMATCH"`. In a correct system this never fires, so one occurrence is a bug or an attack. | **`≥ 1` ever** |
| `provenance.api.requests` | Counter | `route`, `status_class`, `route_class` | Baseline traffic and error rate. `route` is the **template**, so cardinality is the route count (31). | 5xx rate `> 0.02` over 10 min |
| `provenance.api.rate_limited` | Counter | `bucket` | Which quota is binding | none |

### 4.6 Cost — namespace `Provenance/Cost`, `Provenance/Model`

| Metric | Instrument | Labels | The decision it informs | Alarm |
|---|---|---|---|---|
| `provenance.model.invocations` | Counter | `model_id`, `tier`, `node` | **Model invocations per artifact** = `model.invocations / memory.artifact_to_commit_ms{count}`, against the budget of 8 | mean `> 8` over 1 h (budget breach) |
| `provenance.model.input_tokens` | Counter | `model_id`, `tier`, `node` | **Input tokens by tier** | none |
| `provenance.model.output_tokens` | Counter | `model_id`, `tier`, `node` | **Output tokens by tier** | none |
| `provenance.embedding.generated` / `.cache_hit` | Counter | `model_id` | **Embeddings generated versus served from cache** | cache hit ratio `< 0.30` over 1 h on a warm corpus |
| `provenance.embedding.input_tokens` | Counter | `model_id` | Titan token spend | none |
| `provenance.cost.textract_pages` | Counter | `api` | Textract is the surprise line item, because one scanned PDF can be 20 pages | `> 200` in 24 h |
| `provenance.cost.artifact_usd_micros` | Histogram | `source_type`, `pricing_snapshot_date` | **Estimated cost per processed artifact.** The number that decides whether the wedge is economically viable at more than demo scale. | p95 `> 250000` (USD 0.25) over 1 h |
| `provenance.cost.pricing_unavailable` | Counter | `reason` (`SNAPSHOT_MISSING`/`SNAPSHOT_STALE`/`MODEL_NOT_PRICED`) | Emitted **instead of** a cost estimate when prices are not known. A dashboard never shows a fabricated cost. | `> 0` sustained |

Cost is computed from a committed price snapshot, never from a number typed into code:

```python
# packages/python/provenance_telemetry/pricing.py
"""Cost estimation from a refreshed AWS price snapshot.

There is no hard-coded price anywhere in Provenance. If the snapshot is
missing, stale, or does not price a model we invoked, the cost metric is
NOT emitted and `provenance.cost.pricing_unavailable` is incremented
instead. A dashboard that shows a wrong cost is worse than one that shows
no cost, because the wrong one gets quoted.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

SNAPSHOT_PATH: Final = Path("ops/pricing/bedrock-us-east-1.json")
MAX_SNAPSHOT_AGE_DAYS: Final = 30


class PricingUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per 1,000 tokens, as Decimal. Never float: money is never float."""
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


def load_snapshot(path: Path = SNAPSHOT_PATH) -> tuple[dict[str, ModelPrice], dt.date]:
    if not path.exists():
        raise PricingUnavailable("SNAPSHOT_MISSING")
    doc = json.loads(path.read_text(encoding="utf-8"))
    snapshot_date = dt.date.fromisoformat(doc["snapshot_date"])
    if (dt.date.today() - snapshot_date).days > MAX_SNAPSHOT_AGE_DAYS:
        raise PricingUnavailable("SNAPSHOT_STALE")
    prices = {
        model_id: ModelPrice(
            input=Decimal(str(p["input"])),
            output=Decimal(str(p["output"])),
            cache_read=Decimal(str(p["cache_read"])),
            cache_write=Decimal(str(p["cache_write"])),
        )
        for model_id, p in doc["models"].items()
    }
    return prices, snapshot_date


def artifact_cost_usd_micros(
    model_calls: list[dict],
    embedding_tokens: int,
    textract_pages: int,
    prices: dict[str, ModelPrice],
    embedding_model_id: str = "amazon.titan-embed-text-v2:0",
    textract_price_per_page: Decimal | None = None,
) -> int:
    total = Decimal(0)
    for call in model_calls:
        price = prices.get(call["model_id"])
        if price is None:
            raise PricingUnavailable("MODEL_NOT_PRICED")
        total += (
            Decimal(call["input_tokens"]) * price.input
            + Decimal(call["output_tokens"]) * price.output
            + Decimal(call.get("cache_read_input_tokens", 0)) * price.cache_read
            + Decimal(call.get("cache_creation_input_tokens", 0)) * price.cache_write
        ) / Decimal(1000)

    embed_price = prices.get(embedding_model_id)
    if embed_price is None:
        raise PricingUnavailable("MODEL_NOT_PRICED")
    total += Decimal(embedding_tokens) * embed_price.input / Decimal(1000)

    if textract_pages:
        if textract_price_per_page is None:
            raise PricingUnavailable("MODEL_NOT_PRICED")
        total += Decimal(textract_pages) * textract_price_per_page

    return int(total * Decimal(1_000_000))
```

The snapshot is generated, never hand-written:

```bash
# ops/pricing/refresh.sh — run at G-0 and again at G-13; the output file is committed.
set -euo pipefail
REGION=us-east-1

# The Pricing API endpoint lives in us-east-1; regionCode selects the priced region.
aws pricing get-products \
  --region us-east-1 \
  --service-code AmazonBedrock \
  --filters "Type=TERM_MATCH,Field=regionCode,Value=${REGION}" \
  --format-version aws_v1 \
  --output json > ops/pricing/.raw-bedrock.json

# First run prints the distinct attribute set so the mapping below is written
# from observed data rather than guessed. Re-run after any Bedrock price change.
jq -r '.PriceList[] | fromjson | .product.attributes | keys[]' \
  ops/pricing/.raw-bedrock.json | sort -u

python -m tools.pricing_snapshot \
  --raw ops/pricing/.raw-bedrock.json \
  --region "${REGION}" \
  --model anthropic.claude-opus-5 \
  --model anthropic.claude-haiku-4-5 \
  --model amazon.titan-embed-text-v2:0 \
  --out ops/pricing/bedrock-us-east-1.json

python -c "from provenance_telemetry.pricing import load_snapshot; load_snapshot(); print('pricing snapshot OK')"
```

`tools/pricing_snapshot.py` fails loudly if any requested model is absent from the price list, which is the failure that matters: silently pricing Opus 5 at Haiku rates would understate cost by an order of magnitude and would be exactly the sort of number a reviewer asks about.

---

## 5. Structured logs and the redaction contract

### 5.1 The record

One JSON object per line, on stdout, no multi-line records, no ANSI colour, no human-oriented prefix. A worked example: the hero commit.

```json
{
  "ts": "2026-09-18T14:05:19.412Z",
  "level": "INFO",
  "event": "kernel_decision_committed",
  "logger": "provenance.memory_kernel.commit",
  "service": "provenance-control-plane",
  "service_version": "9f3c1b7e5a2d48c1b0e6f7a9d2c4e8b1a3f5d7c9",
  "stage": "demo",
  "schema_revision": "0008",
  "agent_mode": "LIVE",
  "trace_id": "018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90",
  "span_id": "7a13b0e26d2b1c4f",
  "request_id": "018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a91",
  "tenant_hash": "3f9a1c7d20b48e56",
  "user_hash": "b1e4c07a9d3f2856",
  "case_id": "018f8a10-4c22-7f31-9b7d-2ac1e5f09b41",
  "agent_run_id": "018f9e90-0000-7000-8000-000000000001",
  "proposal_id": "018f9fa0-0000-7000-8000-000000000001",
  "kernel_decision_id": "018f8b90-0000-7000-8000-000000000002",
  "event_id": null,
  "action_intent_id": null,
  "trigger_evaluation_id": null,
  "decision": "ACCEPTED_WITH_CONFLICT",
  "reason_codes": ["MUTUAL_EXCLUSION_DETECTED", "CASE_REOPEN_QUALIFIED"],
  "case_revision_before": 12,
  "case_revision_after": 13,
  "retry_count": 0,
  "rows_written": 7,
  "duration_ms": 141,
  "redaction": { "applied": true, "dropped_keys": [], "masked_keys": [], "denied_keys": [] }
}
```

Rules that make the schema usable rather than merely present:

1. **`event` is a stable, low-cardinality identifier, not a sentence.** `kernel_decision_committed`, not `"Kernel committed decision 018f8b90 for case 018f8a10 after 0 retries"`. Human-readable sentences are assembled by the log viewer from the fields; a sentence in the record is a field you cannot query.
2. **Every correlation id from §2.1 is a top-level key**, present as `null` when it does not apply. A key that is sometimes absent and sometimes present forces every Log Insights query to handle both shapes.
3. **`user_hash` and `tenant_hash`, never `user_id` and `tenant_id`.** `HMAC-SHA256(PV_LOG_HASH_KEY, str(uuid))[:8].hex()`. This satisfies `ARCHITECTURE.md` §21.2 and means a log export is not a user list. `case_id`, `artifact_id`, and the other object ids are **not** hashed: they are opaque UUIDs scoped to a user who cannot be identified from the record, and hashing them would break the one thing logs are for, which is joining to a row.
4. **The `redaction` block is always present.** `applied: false` is a legitimate value and means the record passed through clean. An absent block means the record bypassed the filter, which is a bug the log-schema test catches.
5. **Levels.** `DEBUG` is never enabled in `demo` or `prod`. `INFO` for state changes and boundary crossings. `WARN` for correct-but-notable outcomes: `ACTION_STALE`, `SKIPPED_STALE`, `TENANT_MISMATCH`, a denied MCP call, a schema repair. `ERROR` for a failed operation. `CRITICAL` for the three P1 conditions in §8.2.
6. **Log groups.** `/provenance/control-plane`, `/provenance/agents`, `/provenance/workers/<name>`. Retention 30 days on `demo` and `prod`, 3 days on `ci`. `G13.5` queries `/provenance/control-plane` by name, so the name is part of the contract.

### 5.2 The allow-list and the deny-list

`04_API_EVENTS_SECURITY.md` §23 fixes the shape of this decision. This section makes it mechanical.

**Allow-list (keys that may appear in a log record).** Anything else is dropped. New telemetry must be declared here before it can be logged, which is the only way an allow-list survives six months of feature work.

| Group | Keys |
|---|---|
| Envelope | `ts`, `level`, `event`, `logger`, `service`, `service_version`, `stage`, `schema_revision`, `agent_mode`, `redaction` |
| Correlation | `trace_id`, `span_id`, `request_id`, `agent_run_id`, `proposal_id`, `kernel_decision_id`, `event_id`, `action_intent_id`, `action_execution_id`, `trigger_evaluation_id`, `causation_id`, `correlation_id`, `idempotency_scope` |
| Subject (opaque ids) | `tenant_hash`, `user_hash`, `case_id`, `relationship_id`, `context_id`, `artifact_id`, `evidence_id`, `belief_id`, `belief_version_id`, `conflict_id`, `commitment_id`, `trigger_id`, `counterparty_id` |
| Decision | `decision`, `reason_code`, `reason_codes`, `result`, `status`, `from_state`, `to_state`, `transition_type`, `error_code`, `stale_reason`, `identity_status`, `epistemic_status`, `claim_kind`, `conflict_type`, `severity`, `attention_level`, `requires_human` |
| Versioning | `case_revision`, `case_revision_before`, `case_revision_after`, `basis_case_revision`, `aggregate_version`, `evaluation_version`, `schema_version`, `payload_version`, `graph_name`, `graph_version`, `prompt_version`, `parser_version`, `embedding_version`, `model_id`, `tier`, `node`, `effort` |
| Measurement | `duration_ms`, `retry_count`, `attempt_no`, `attempt_count`, `rows_written`, `row_count`, `count`, `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `candidates_in`, `candidates_out`, `size_bytes`, `page_count`, `lag_ms`, `cost_usd_micros` |
| Hashes | `content_sha256`, `payload_sha256`, `draft_sha256`, `approval_draft_sha256`, `request_sha256`, `arguments_digest`, `normalized_text_sha256`, `node_digest`, `trace_digest` |
| Security-relevant | `route`, `route_class`, `http_status`, `client_id`, `capability_kind`, `capability_status`, `principal_kind`, `scope_required`, `sql_role`, `access_mode`, `view_name`, `mcp_server`, `tool_name`, `denied`, `recipient_domain`, `sender_domain`, `provider`, `provider_correlation_id`, `ses_verdict_pass` |
| Diagnostic free text | `error_detail`, `filter_summary`, `message` |

The last group is the dangerous one and it is deliberately tiny. Those three keys are allowed because operating without them is genuinely harder, and they are the reason value-level scanning exists.

**Deny-list (keys that must never appear, at any nesting depth).** Hitting one is not just a drop; it increments `provenance.trace.redaction_denied` and logs a `WARN` naming the key path but not the value, because an attempt to log a denied key is itself a signal.

| Category | Keys |
|---|---|
| Artifact and evidence content | `body`, `text`, `raw_text`, `exact_text`, `normalized_text`, `content`, `html`, `plain`, `attachment`, `attachments`, `page_text`, `ocr_text`, `subject`, `snippet`, `excerpt`, `draft_text`, `draft_payload`, `rationale`, `resolution_notes`, `description` |
| Model internals | `prompt`, `system_prompt`, `messages`, `completion`, `raw_completion`, `thinking`, `reasoning_trace`, `reasoning`, `scratchpad`, `tool_input`, `tool_arguments`, `arguments` |
| Credentials | `authorization`, `access_token`, `refresh_token`, `id_token`, `jwt`, `bearer`, `password`, `passwd`, `secret`, `client_secret`, `api_key`, `apikey`, `private_key`, `session_token`, `aws_secret_access_key`, `aws_session_token`, `capability_proof`, `alias_secret`, `hmac_key`, `cursor_hmac_key`, `smtp_password`, `ses_credentials`, `database_url`, `dsn`, `conn_str`, `connection_string`, `cookie`, `set-cookie` |
| Direct identity | `email`, `email_address`, `sender`, `recipient`, `to`, `cc`, `bcc`, `phone`, `address`, `postal_address`, `account_number`, `external_account_ref`, `iban`, `card_number`, `ssn`, `cognito_sub`, `alias_display`, `user_id`, `tenant_id` |

`user_id` and `tenant_id` are on the deny-list, not the allow-list, because §5.1 rule 3 requires the hashed forms. A record that carries `user_id` is a record written by code that did not go through `emit()`, and the deny-list turns that from an invisible leak into a counted event.

`sender` is denied while `sender_domain` is allowed. The domain is the operationally useful part (it is how you find out that one counterparty changed its invoice format) and the local part is the identifying part.

**Value-level deny patterns**, applied to every string value that survives the key check, including inside `error_detail`:

| Pattern | Replacement | Why it exists |
|---|---|---|
| JWT (`eyJ…​.…​.…`) | `[REDACTED:JWT]` | A Cognito access token pasted into an error message |
| DSN with credentials (`postgresql://user:pass@host/db`) | `[REDACTED:DB_DSN]` | The single most common real-world leak; see §5.4 |
| `Bearer <token>` | `[REDACTED:BEARER]` | Header echoed into an exception |
| AWS access key id (`AKIA…`, `ASIA…`) | `[REDACTED:AWS_KEY]` | |
| PEM private key header | `[REDACTED:PRIVATE_KEY]` | |
| Email address | `[REDACTED:EMAIL]` | PII in free text |
| Account-number-shaped digit run (≥ 8 digits, optional `-`/space separators) | `••••<last 4>` | `88-114-2039` → `••••2039`. Partial retention is deliberate: the last four is what makes a log entry actionable and it is not a full account number. |

### 5.3 The redaction helper

```python
# packages/python/provenance_telemetry/redaction.py
"""Field-level redaction for every structured record Provenance emits.

Two rules, applied in this order.

1.  ALLOW-LIST WINS ON KEYS.  A key not in ``ALLOWED_KEYS`` is dropped.  A key
    in ``DENIED_KEYS`` is dropped *and counted*, because a caller trying to log
    a denied key is a signal, not an accident to be swallowed.

2.  DENY-LIST WINS ON VALUES.  Even an allowed key is scanned, because the
    dangerous case is not ``{"password": "hunter2"}`` -- nobody writes that --
    it is ``{"error_detail": "... postgresql://<user>:hunter2@..."}``,
    where the leak arrives inside a string a driver produced.

The function is pure, total, and idempotent: ``redact(redact(x)) == redact(x)``.
It never raises on unexpected input, because a redactor that raises inside an
exception handler turns a logged error into a lost error.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

REDACTED_MARK: Final = "[REDACTED:{kind}]"
MAX_STRING_LEN: Final = 512
MAX_DEPTH: Final = 6
MAX_SEQUENCE_ITEMS: Final = 50
TRUNCATED_SUFFIX: Final = "…[TRUNCATED]"

ALLOWED_KEYS: Final[frozenset[str]] = frozenset({
    # envelope
    "ts", "level", "event", "logger", "service", "service_version", "stage",
    "schema_revision", "agent_mode", "redaction",
    # correlation
    "trace_id", "span_id", "request_id", "agent_run_id", "proposal_id",
    "kernel_decision_id", "event_id", "action_intent_id", "action_execution_id",
    "trigger_evaluation_id", "causation_id", "correlation_id", "idempotency_scope",
    # subject
    "tenant_hash", "user_hash", "case_id", "relationship_id", "context_id",
    "artifact_id", "evidence_id", "belief_id", "belief_version_id", "conflict_id",
    "commitment_id", "trigger_id", "counterparty_id",
    # decision
    "decision", "reason_code", "reason_codes", "result", "status", "from_state",
    "to_state", "transition_type", "error_code", "stale_reason", "identity_status",
    "epistemic_status", "claim_kind", "conflict_type", "severity",
    "attention_level", "requires_human",
    # versioning
    "case_revision", "case_revision_before", "case_revision_after",
    "basis_case_revision", "aggregate_version", "evaluation_version",
    "schema_version", "payload_version", "graph_name", "graph_version",
    "prompt_version", "parser_version", "embedding_version", "model_id", "tier",
    "node", "effort",
    # measurement
    "duration_ms", "retry_count", "attempt_no", "attempt_count", "rows_written",
    "row_count", "count", "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens", "candidates_in",
    "candidates_out", "size_bytes", "page_count", "lag_ms", "cost_usd_micros",
    # hashes
    "content_sha256", "payload_sha256", "draft_sha256", "approval_draft_sha256",
    "request_sha256", "arguments_digest", "normalized_text_sha256", "node_digest",
    "trace_digest",
    # security-relevant
    "route", "route_class", "http_status", "client_id", "capability_kind",
    "capability_status", "principal_kind", "scope_required", "sql_role",
    "access_mode", "view_name", "mcp_server", "tool_name", "denied",
    "recipient_domain", "sender_domain", "provider", "provider_correlation_id",
    "ses_verdict_pass",
    # diagnostic free text -- small on purpose, scanned hard
    "error_detail", "filter_summary", "message",
})

DENIED_KEYS: Final[frozenset[str]] = frozenset({
    # artifact / evidence content
    "body", "text", "raw_text", "exact_text", "normalized_text", "content",
    "html", "plain", "attachment", "attachments", "page_text", "ocr_text",
    "subject", "snippet", "excerpt", "draft_text", "draft_payload", "rationale",
    "resolution_notes", "description",
    # model internals
    "prompt", "system_prompt", "messages", "completion", "raw_completion",
    "thinking", "reasoning_trace", "reasoning", "scratchpad", "tool_input",
    "tool_arguments", "arguments",
    # credentials
    "authorization", "access_token", "refresh_token", "id_token", "jwt",
    "bearer", "password", "passwd", "secret", "client_secret", "api_key",
    "apikey", "private_key", "session_token", "aws_secret_access_key",
    "aws_session_token", "capability_proof", "alias_secret", "hmac_key",
    "cursor_hmac_key", "smtp_password", "ses_credentials", "database_url",
    "dsn", "conn_str", "connection_string", "cookie", "set-cookie",
    # direct identity
    "email", "email_address", "sender", "recipient", "to", "cc", "bcc", "phone",
    "address", "postal_address", "account_number", "external_account_ref",
    "iban", "card_number", "ssn", "cognito_sub", "alias_display",
    "user_id", "tenant_id",
})

_VALUE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}")),
    ("DB_DSN", re.compile(
        r"\b(?:postgres|postgresql|cockroachdb|mysql|redis|amqp|mongodb)(?:\+\w+)?"
        r"://[^\s:@/]+:[^\s@/]+@\S+")),
    ("BEARER", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA|ABIA|ACCA)[A-Z0-9]{16}\b")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?"
                               r"(?:-----END (?:[A-Z ]+ )?PRIVATE KEY-----|$)")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)

# 8+ digits, optionally grouped by a single space or hyphen. Applied to strings
# only, so integer byte counts and epoch values are untouched.
_ACCOUNT_PATTERN: Final = re.compile(r"(?<![\w.])(?:\d[ -]?){7,}\d(?![\w.])")

_ALREADY_REDACTED: Final = re.compile(r"\[REDACTED:[A-Z_]+\]")


def _mask_account(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "••••" + digits[-4:]


def scan_text(value: str) -> tuple[str, list[str]]:
    """Return the cleaned string and the list of pattern names that fired."""
    hits: list[str] = []
    cleaned = value
    for kind, pattern in _VALUE_PATTERNS:
        cleaned, n = pattern.subn(REDACTED_MARK.format(kind=kind), cleaned)
        if n:
            hits.append(kind)
    cleaned, n = _ACCOUNT_PATTERN.subn(_mask_account, cleaned)
    if n:
        hits.append("ACCOUNT_NUMBER")
    if len(cleaned) > MAX_STRING_LEN:
        cleaned = cleaned[:MAX_STRING_LEN] + TRUNCATED_SUFFIX
        hits.append("TRUNCATED")
    return cleaned, hits


class _Report:
    __slots__ = ("dropped", "masked", "denied")

    def __init__(self) -> None:
        self.dropped: list[str] = []
        self.masked: list[str] = []
        self.denied: list[str] = []


def _walk(value: Any, path: str, depth: int, report: _Report) -> Any:
    if depth > MAX_DEPTH:
        report.dropped.append(path + ".<depth>")
        return "[DROPPED:MAX_DEPTH]"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        cleaned, hits = scan_text(value)
        if hits:
            report.masked.append(path)
        return cleaned

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            child = f"{path}.{key}" if path else key
            lowered = key.lower()
            if lowered in DENIED_KEYS:
                report.denied.append(child)
                continue
            if depth == 0 and lowered not in ALLOWED_KEYS:
                report.dropped.append(child)
                continue
            out[key] = _walk(raw_value, child, depth + 1, report)
        return out

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)[:MAX_SEQUENCE_ITEMS]
        if len(list(value)) > MAX_SEQUENCE_ITEMS:
            report.dropped.append(path + ".<overflow>")
        return [_walk(item, f"{path}[{i}]", depth + 1, report)
                for i, item in enumerate(items)]

    if isinstance(value, (bytes, bytearray)):
        return f"[BYTES:{len(value)}]"

    cleaned, hits = scan_text(str(value))
    if hits:
        report.masked.append(path)
    return cleaned


def redact_record(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the key allow-list, the key deny-list, and value scanning.

    Idempotent: values already carrying a ``[REDACTED:...]`` marker are left
    alone by every pattern, and the account mask cannot re-fire on ``••••2039``.
    """
    report = _Report()
    out = _walk(dict(fields), path="", depth=0, report=report)
    assert isinstance(out, dict)
    out["redaction"] = {
        "applied": bool(report.dropped or report.masked or report.denied),
        "dropped_keys": sorted(set(report.dropped)),
        "masked_keys": sorted(set(report.masked)),
        "denied_keys": sorted(set(report.denied)),
    }
    return out


def subject_hash(value: object, key: bytes) -> str:
    """Stable, non-reversible id for a user or tenant. 16 hex characters."""
    return hmac.new(key, str(value).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
```

Installation, so that no code path can bypass it:

```python
# packages/python/provenance_telemetry/logging_setup.py
import json
import logging
import sys
from typing import Any

from provenance_telemetry import context, metrics
from provenance_telemetry.redaction import redact_record

_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


class ProvenanceJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%03dZ"),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
            **context.current_fields(),          # service, stage, trace_id, hashes …
            **{k: v for k, v in vars(record).items() if k not in _RESERVED},
        }
        if record.exc_info and record.exc_info[1] is not None:
            fields["error_code"] = type(record.exc_info[1]).__name__
            fields["error_detail"] = str(record.exc_info[1])

        safe = redact_record(fields)
        if safe["redaction"]["denied_keys"]:
            metrics.redaction_denied.add(len(safe["redaction"]["denied_keys"]))
        return json.dumps(safe, separators=(",", ":"), default=str)


def install() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ProvenanceJsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]          # replace, never append: no second sink
    root.setLevel(logging.INFO)
```

Replacing rather than appending the root handler matters. A second handler installed by a library — uvicorn's default, boto3's, LangGraph's — would emit the same record unredacted, and the leak would be in a log line nobody was looking at.

### 5.4 Unit tests, including the case that leaks without this

```python
# services/control_plane/tests/unit/test_redaction.py
import json

import pytest

from provenance_telemetry.redaction import redact_record, scan_text, subject_hash

HASH_KEY = b"test-key-not-a-real-secret"


def _dump(record: dict) -> str:
    return json.dumps(record, default=str)


# ---------------------------------------------------------------- the leak case

def test_asyncpg_connection_error_leaks_the_database_password_without_redaction():
    """The single most likely real leak in this system.

    asyncpg and psycopg both put the connection string into the exception
    message. That exception is caught by the Kernel's retry wrapper and logged
    with `logger.exception(...)`. Without value-level scanning, the role
    password for `pv_kernel_writer` is written to CloudWatch Logs in plain
    text -- from correct-looking, well-intentioned error handling.
    """
    raw = (
        'connection to server at "<cluster-host>.cockroachlabs.cloud" (10.0.4.11), '
        "port 26257 failed: FATAL: password authentication failed for user "
        '"pv_kernel_writer"; url was '
        "postgresql://<user>:S3cr3t-Rot4te-Me@"
        "<cluster-host>.cockroachlabs.cloud:26257/provenance?sslmode=verify-full"
    )
    assert "S3cr3t-Rot4te-Me" in raw                      # the leak, if nothing intervenes

    out = redact_record({
        "event": "kernel_transaction_failed",
        "trace_id": "018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90",
        "error_code": "ConnectionDoesNotExistError",
        "error_detail": raw,
        "retry_count": 5,
    })

    body = _dump(out)
    assert "S3cr3t-Rot4te-Me" not in body
    assert "pv_kernel_writer:" not in body
    assert "[REDACTED:DB_DSN]" in out["error_detail"]
    assert out["redaction"]["applied"] is True
    assert out["redaction"]["masked_keys"] == ["error_detail"]
    # The operationally useful half survives: we still know what failed.
    assert "password authentication failed" in out["error_detail"]
    assert out["retry_count"] == 5


# ---------------------------------------------------------------- key deny-list

def test_full_email_body_is_dropped_not_truncated():
    out = redact_record({
        "event": "artifact_parsed",
        "artifact_id": "018f9e80-0000-7000-8000-000000000001",
        "normalized_text": "Dear customer, your invoice for account 88-114-2039 ...",
        "body": "<html><body>full message</body></html>",
    })
    assert "normalized_text" not in out
    assert "body" not in out
    assert "88-114-2039" not in _dump(out)
    assert out["redaction"]["denied_keys"] == ["body", "normalized_text"]


def test_cognito_tokens_are_denied_by_key_and_by_value():
    jwt = ("eyJraWQiOiJhYmMiLCJhbGciOiJSUzI1NiJ9"
           ".eyJzdWIiOiIxMjMiLCJ0b2tlbl91c2UiOiJhY2Nlc3MifQ.c2lnbmF0dXJlLXZhbHVl")
    out = redact_record({
        "event": "token_verified",
        "access_token": jwt,                       # denied by key
        "refresh_token": "eyJjdHkiOiJKV1QifQ..x.y",
        "message": f"presented Authorization: Bearer {jwt}",   # caught by value
    })
    assert "access_token" not in out and "refresh_token" not in out
    assert jwt not in _dump(out)
    assert "[REDACTED:BEARER]" in out["message"] or "[REDACTED:JWT]" in out["message"]


def test_raw_user_id_is_denied_because_the_hashed_form_is_required():
    out = redact_record({"event": "case_read", "user_id": "018f7a01-0000-7000-8000-00000000abcd"})
    assert "user_id" not in out
    assert out["redaction"]["denied_keys"] == ["user_id"]


def test_chain_of_thought_can_never_be_logged():
    out = redact_record({
        "event": "resolution_complete",
        "thinking": "The user probably wants ...",
        "raw_completion": "{...}",
        "scratchpad": "step 1 ...",
        "model_id": "anthropic.claude-opus-5",
    })
    assert set(out["redaction"]["denied_keys"]) == {"raw_completion", "scratchpad", "thinking"}
    assert out["model_id"] == "anthropic.claude-opus-5"


# ---------------------------------------------------------------- value masking

def test_account_number_in_an_allowed_field_keeps_only_the_last_four():
    out = redact_record({
        "event": "mcp_tool_call",
        "tool_name": "query_agent_evidence_search",
        "filter_summary": "external_account_ref_norm = 88-114-2039; top_k = 20",
    })
    assert "88-114-2039" not in out["filter_summary"]
    assert "••••2039" in out["filter_summary"]
    assert "top_k = 20" in out["filter_summary"]        # small integers untouched


def test_numeric_fields_are_not_mangled_by_the_account_mask():
    out = redact_record({"event": "artifact_registered", "size_bytes": 184213,
                         "duration_ms": 81200000, "count": 47219})
    assert out["size_bytes"] == 184213
    assert out["duration_ms"] == 81200000
    assert out["count"] == 47219


def test_email_address_in_free_text_is_removed_but_domain_field_survives():
    out = redact_record({
        "event": "artifact_received",
        "sender_domain": "example-isp.com",
        "message": "forwarded from dana.okoro@example-isp.com",
    })
    assert out["sender_domain"] == "example-isp.com"
    assert "dana.okoro@" not in out["message"]
    assert "[REDACTED:EMAIL]" in out["message"]


# ---------------------------------------------------------------- positive control

def test_a_clean_record_passes_through_completely_unchanged():
    """Without this, a redactor that deletes everything would pass every test above."""
    clean = {
        "event": "kernel_decision_committed",
        "trace_id": "018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90",
        "kernel_decision_id": "018f8b90-0000-7000-8000-000000000002",
        "case_id": "018f8a10-4c22-7f31-9b7d-2ac1e5f09b41",
        "decision": "ACCEPTED_WITH_CONFLICT",
        "reason_codes": ["MUTUAL_EXCLUSION_DETECTED", "CASE_REOPEN_QUALIFIED"],
        "case_revision_before": 12,
        "case_revision_after": 13,
        "retry_count": 0,
        "duration_ms": 141,
    }
    out = redact_record(clean)
    assert {k: v for k, v in out.items() if k != "redaction"} == clean
    assert out["redaction"] == {"applied": False, "dropped_keys": [],
                                "masked_keys": [], "denied_keys": []}


# ---------------------------------------------------------------- structural

def test_unknown_top_level_key_is_dropped_and_named():
    out = redact_record({"event": "x", "invoice_total_text": "USD 186.00 due"})
    assert "invoice_total_text" not in out
    assert out["redaction"]["dropped_keys"] == ["invoice_total_text"]


def test_redaction_is_idempotent():
    once = redact_record({"event": "e", "error_detail":
                          "postgresql://u:p@h:26257/d and account 88-114-2039"})
    twice = redact_record({k: v for k, v in once.items() if k != "redaction"})
    assert once["error_detail"] == twice["error_detail"]


def test_redactor_never_raises_on_hostile_input():
    class Exploding:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    # str() failure must not escape: a redactor that raises inside an exception
    # handler loses the error it was called to record.
    out = redact_record({"event": "e", "message": "ok", "weird": Exploding()})
    assert out["event"] == "e"


def test_subject_hash_is_stable_and_not_reversible():
    h1 = subject_hash("018f7a01-0000-7000-8000-00000000abcd", HASH_KEY)
    h2 = subject_hash("018f7a01-0000-7000-8000-00000000abcd", HASH_KEY)
    assert h1 == h2 and len(h1) == 16
    assert "018f7a01" not in h1


@pytest.mark.parametrize("payload,kind", [
    ("AKIAIOSFODNN7EXAMPLE", "AWS_KEY"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----", "PRIVATE_KEY"),
])
def test_each_value_pattern_fires(payload, kind):
    cleaned, hits = scan_text(f"prefix {payload} suffix")
    assert kind in hits and payload not in cleaned
```

`20_TDD_STRATEGY.md` allocates six tests to `test_redaction.py`. The suite above is larger; the count in that document is a floor, and a quality document may strengthen an acceptance criterion. The sabotage entry for this module is `provenance_telemetry.redaction.scan_text`: neuter it and `test_asyncpg_connection_error_leaks_the_database_password_without_redaction` must go red. If it does not, the test is decoration.

### 5.5 The lint that keeps the allow-list honest

```bash
# tools/log_schema_lint.py — runs in CI, and as part of `make lint`.
python -m tools.log_schema_lint services packages agents workers
#   → "log call sites: 214 | keys used: 96 | undeclared keys: 0 | denied keys used: 0"
```

The lint is AST-based. It finds every `logger.*(...)` call and every `emit(...)` call, collects the literal keyword and `extra={...}` keys, and fails on any key that is neither in `ALLOWED_KEYS` nor in `DENIED_KEYS` — because a key in neither set is a key nobody has decided about. It also fails on any `logging.getLogger().addHandler(...)` outside `logging_setup.install`, which is the second sink problem from §5.3.

---

## 6. The Memory Trace persistence model

### 6.1 The rule, stated where it cannot be missed

> **A trace is a projection of rows. No trace element may be synthesised at render time.**

Concretely, and these are all failures of the same rule:

- No node is created from a span, a log line, or an in-memory object. Spans are for operators; rows are for auditors. A span expires with the log group's retention; a row does not.
- No node is created because the renderer expected one. If the Advocate never ran, there is no `AGENT_RUN` node for it, and the trace ends at `OUTBOX_EVENT`. The gap is the information.
- No node's `summary` is written by a model. Summaries are formatted from the row's own columns by a pure function in `provenance_db/read_models/memory_trace.py`.
- No node id, case id, revision number, or count is a literal in frontend source. `G12.3` greps `apps/web/src` for UUID literals and requires zero.
- No animation advances the DAG on a timer. `G12.2` collects `[data-node-id]` from the DOM and asserts the set is a subset of the API payload's node ids.
- If a row is deleted, its node disappears. `G11.4` states this as a test: deleting the `agent_runs` row must empty the MCP panel.

### 6.2 Which rows constitute a trace

Nine sources. Six are joined on `trace_id` directly; three are reachable only through a join, and that is stated rather than hidden.

| Trace node type | Source table | How it is reached from `trace_id` | Notes |
|---|---|---|---|
| `API_REQUEST` | `idempotency_records` | `trace_id` (additive column, §6.6) | Exists only for the endpoints in `15_API_SPEC.md` §6.2. A `GET` produces no node, correctly: it changed nothing. |
| `ARTIFACT_PARSE` | `outbox_events` where `event_type = 'artifact.parsed.v1'`, joined to `source_artifacts` on `aggregate_id` | `trace_id` | `source_artifacts` carries no `trace_id`; the event does, and it carries the parse outcome in its payload. |
| `EMBEDDING` | `outbox_events` where `event_type = 'evidence.admitted.v1'` | `trace_id` | Payload carries `created_count`, `deduplicated_count`, `embedding_version`. |
| `AGENT_RUN` | `agent_runs` | `trace_id` | `idx_agent_runs_trace` exists for exactly this query. |
| `MODEL_CALL` | `agent_runs.model_calls` (JSONB, additive, §6.6) | via `agent_runs.trace_id` | One node per array element. Caller-reported; see §7.4. |
| `MCP_TOOL_CALL` | `agent_runs.tool_calls` (JSONB, additive, §6.6) | via `agent_runs.trace_id` | One node per array element, capped at 50. |
| `RETRIEVAL` | `agent_runs.retrieval_candidate_count` plus the retrieval fields of `tool_calls` | via `agent_runs.trace_id` | One node per agent run that retrieved. |
| `PROPOSAL` | `memory_proposals` | `trace_id` | `idx_memory_proposals_trace`. |
| `KERNEL_DECISION` | `kernel_decisions` | `trace_id` | `idx_kernel_decisions_trace`. |
| `DB_TRANSACTION` | `kernel_decisions` (same row, different projection) | `trace_id` | Carries `retry_count`, `committed_at − created_at`, and the attributable row count of §6.5. |
| `OUTBOX_EVENT` | `outbox_events` | `trace_id` | |
| `EVENT_CONSUMER` | `processed_events` | join `processed_events.event_id = outbox_events.id` | `processed_events` carries no `trace_id` by design: it is keyed by `(consumer_name, event_id)` and the event id is the join. |
| `TRIGGER_EVALUATION` | `outbox_events` where `event_type IN ('trigger.fired.v1','trigger.noop.v1')`, joined to `prospective_triggers` on `aggregate_id` | `trace_id` | A `NO_OP` writes no `state_transitions` row, so the outbox event is the only durable record of the wake — which is why `trigger.noop.v1` exists. |
| `ACTION_INTENT` | `action_intents` | join `action_intents.created_by_agent_run_id = agent_runs.id` | |
| `ACTION_APPROVAL` | `state_transitions` where `transition_type = 'ACTION_STATE'` and `to_state = 'APPROVED'` | `trace_id` | Approval is a canonical state change and therefore has a transition row. |
| `ACTION_EXECUTION` | `action_executions` | join `action_executions.action_intent_id → action_intents.created_by_agent_run_id → agent_runs.trace_id` | |
| `CANONICAL_CHANGE` | `state_transitions` | `trace_id` | One node per row effect the commit produced, **child of `DB_TRANSACTION`**, ordered by `case_revision` then `created_at`. `attributes.change_kind` comes from the closed set in `specs/15_API_SPEC.md` §8.28; `refs[]` points at the rows it describes. |
| (spine, not a node) | `state_transitions` | `trace_id` | The *same* rows, in their second role: they supply the ordering and the `case_revision` for the `CANONICAL_CHANGE` children, and they are rendered flat as the `memory_operations` array of `GET /v1/cases/{id}/memory-trace` and as the `rows_written` breakdown on `DB_TRANSACTION`. Spine and node are two renderings of one table, not two competing DAG shapes. |

The `belief_versions`, `belief_support`, `claims`, `conflicts`, and `commitments` rows written by the commit are **not** trace nodes. They are canonical memory, rendered by State Proof, and the trace links to them rather than duplicating them. A trace that re-rendered grounding would be a second copy of canonical data, which `00_IMPLEMENTATION_MAP.md` §12 forbids for good reason.

### 6.3 The assembly SQL

One statement, run as `pv_app_reader_writer`, `$1 = trace_id`, `$2 = tenant_id`, `$3 = user_id`. It returns a flat node table; edges are derived in §6.4 from `parent_key` and ordering, not from a stored graph.

```sql
-- packages/python/provenance_db/queries/memory_trace_nodes.sql
-- Assembles the Judge Mode trace DAG from persisted rows only.
-- Every branch is a projection of a table. There is no VALUES list, no
-- generate_series, and no CASE expression that invents a node.
WITH
api_request AS (
    SELECT 'API_REQUEST'                         AS node_type,
           'idempotency_records'                 AS source_table,
           ir.scope || '|' || ir.key             AS source_pk,
           ir.created_at                         AS started_at,
           (EXTRACT(EPOCH FROM (COALESCE(ir.completed_at, ir.created_at)
                                - ir.created_at)) * 1000)::INT8 AS duration_ms,
           CASE ir.status WHEN 'COMPLETED' THEN 'OK'
                          WHEN 'FAILED'    THEN 'FAILED'
                          ELSE 'PENDING' END     AS status,
           NULL::STRING                          AS parent_key,
           jsonb_build_object(
               'idempotency_scope', ir.scope,
               'http_status',       ir.response_code)  AS attributes
    FROM idempotency_records AS ir
    WHERE ir.trace_id = $1 AND ir.tenant_id = $2 AND ir.user_id = $3
),
artifact_parse AS (
    SELECT 'ARTIFACT_PARSE', 'outbox_events', oe.id::STRING,
           oe.occurred_at,
           (oe.payload ->> 'duration_ms')::INT8,
           CASE WHEN oe.payload ->> 'parser_status' = 'PARSED' THEN 'OK'
                WHEN oe.payload ->> 'parser_status' = 'PARTIAL' THEN 'RETRIED'
                ELSE 'FAILED' END,
           NULL::STRING,
           jsonb_build_object(
               'artifact_id',        oe.aggregate_id,
               'parser_version',     oe.payload ->> 'parser_version',
               'parser_status',      oe.payload ->> 'parser_status',
               'content_block_count', oe.payload -> 'content_block_count',
               'used_textract',      oe.payload -> 'used_textract',
               'mime_type',          sa.mime_type,
               'size_bytes',         sa.size_bytes,
               'sender_domain',      sa.sender_domain)
    FROM outbox_events AS oe
    JOIN source_artifacts AS sa
      ON sa.tenant_id = oe.tenant_id AND sa.user_id = oe.user_id
     AND sa.id = oe.aggregate_id
    WHERE oe.trace_id = $1 AND oe.tenant_id = $2 AND oe.user_id = $3
      AND oe.event_type = 'artifact.parsed.v1'
),
embedding AS (
    SELECT 'EMBEDDING', 'outbox_events', oe.id::STRING,
           oe.occurred_at, NULL::INT8, 'OK', NULL::STRING,
           jsonb_build_object(
               'model_id',          'amazon.titan-embed-text-v2:0',
               'dimensions',        1024,
               'embedding_version', oe.payload ->> 'embedding_version',
               'created_count',     oe.payload -> 'created_count',
               'deduplicated_count', oe.payload -> 'deduplicated_count')
    FROM outbox_events AS oe
    WHERE oe.trace_id = $1 AND oe.tenant_id = $2 AND oe.user_id = $3
      AND oe.event_type = 'evidence.admitted.v1'
),
agent_run AS (
    SELECT 'AGENT_RUN', 'agent_runs', ar.id::STRING,
           ar.started_at,
           (EXTRACT(EPOCH FROM (COALESCE(ar.finished_at, ar.started_at)
                                - ar.started_at)) * 1000)::INT8,
           CASE ar.status WHEN 'SUCCEEDED' THEN 'OK'
                          WHEN 'RUNNING'   THEN 'PENDING'
                          ELSE 'FAILED' END,
           NULL::STRING,
           jsonb_build_object(
               'agent_run_id',  ar.id,
               'graph_name',    ar.graph_name,
               'graph_version', ar.graph_version,
               'memory_mode',   ar.memory_mode,
               'model_route',   ar.model_route,
               'error_code',    ar.error_code)
    FROM agent_runs AS ar
    WHERE ar.trace_id = $1 AND ar.tenant_id = $2 AND ar.user_id = $3
      AND ar.is_counterfactual = false        -- counterfactual runs are excluded
),
model_call AS (                                -- one node per array element
    SELECT 'MODEL_CALL', 'agent_runs.model_calls',
           ar.id::STRING || '#mc' || (mc.ord - 1)::STRING,
           ar.started_at + ((mc.ord - 1) * INTERVAL '1 microsecond'),
           (mc.value ->> 'duration_ms')::INT8,
           CASE WHEN mc.value ->> 'stop_reason' = 'refusal' THEN 'FAILED' ELSE 'OK' END,
           ar.id::STRING,
           jsonb_build_object(
               'model_id',       mc.value ->> 'model_id',
               'tier',           mc.value ->> 'tier',
               'node',           mc.value ->> 'node',
               'prompt_version', mc.value ->> 'prompt_version',
               'input_tokens',   mc.value -> 'input_tokens',
               'output_tokens',  mc.value -> 'output_tokens',
               'repair_attempts', mc.value -> 'repair_attempts')
    FROM agent_runs AS ar,
         jsonb_array_elements(COALESCE(ar.model_calls, '[]'::JSONB))
              WITH ORDINALITY AS mc(value, ord)
    WHERE ar.trace_id = $1 AND ar.tenant_id = $2 AND ar.user_id = $3
),
mcp_tool_call AS (
    SELECT 'MCP_TOOL_CALL', 'agent_runs.tool_calls',
           ar.id::STRING || '#tc' || (tc.ord - 1)::STRING,
           ar.started_at + ((tc.ord - 1) * INTERVAL '1 microsecond'),
           (tc.value ->> 'duration_ms')::INT8,
           CASE WHEN (tc.value ->> 'denied')::BOOL THEN 'FAILED' ELSE 'OK' END,
           ar.id::STRING,
           jsonb_build_object(
               'mcp_server',     tc.value ->> 'mcp_server',
               'tool_name',      tc.value ->> 'tool_name',
               'view_name',      tc.value ->> 'view_name',
               'sql_role',       tc.value ->> 'sql_role',
               'access_mode',    tc.value ->> 'access_mode',
               'filter_summary', tc.value ->> 'filter_summary',
               'rows_returned',  tc.value -> 'rows_returned',
               'denied',         tc.value -> 'denied')
    FROM agent_runs AS ar,
         jsonb_array_elements(COALESCE(ar.tool_calls, '[]'::JSONB))
              WITH ORDINALITY AS tc(value, ord)
    WHERE ar.trace_id = $1 AND ar.tenant_id = $2 AND ar.user_id = $3
),
retrieval AS (
    SELECT 'RETRIEVAL', 'agent_runs', ar.id::STRING || '#retrieval',
           ar.started_at, NULL::INT8, 'OK', ar.id::STRING,
           jsonb_build_object(
               'vector_candidates',   ar.retrieval_candidate_count,
               'vector_index',        'evidence_embedding_ann_idx',
               'embedding_model',     'amazon.titan-embed-text-v2:0',
               'distance',            'cosine',
               'mcp_views_touched',   (SELECT jsonb_agg(DISTINCT t.value ->> 'view_name')
                                       FROM jsonb_array_elements(
                                                COALESCE(ar.tool_calls, '[]'::JSONB)) AS t(value)))
    FROM agent_runs AS ar
    WHERE ar.trace_id = $1 AND ar.tenant_id = $2 AND ar.user_id = $3
      AND ar.retrieval_candidate_count IS NOT NULL
),
proposal AS (
    SELECT 'PROPOSAL', 'memory_proposals', mp.id::STRING,
           mp.created_at, NULL::INT8,
           CASE WHEN mp.status LIKE 'REJECTED%' THEN 'FAILED'
                WHEN mp.status = 'SUBMITTED'    THEN 'PENDING'
                ELSE 'OK' END,
           mp.agent_run_id::STRING,
           jsonb_build_object(
               'proposal_id',      mp.id,
               'proposal_type',    mp.proposal_type,
               'status',           mp.status,
               'schema_version',   mp.schema_version,
               'model_id',         mp.model_id,
               'prompt_version',   mp.prompt_version,
               'evidence_count',   jsonb_array_length(mp.evidence_ids),
               'artifact_count',   jsonb_array_length(mp.source_artifact_ids),
               'payload_sha256',   encode(mp.payload_sha256, 'hex'))
    FROM memory_proposals AS mp
    WHERE mp.trace_id = $1 AND mp.tenant_id = $2 AND mp.user_id = $3
),
kernel_decision AS (
    SELECT 'KERNEL_DECISION', 'kernel_decisions', kd.id::STRING,
           kd.created_at,
           (EXTRACT(EPOCH FROM (COALESCE(kd.committed_at, kd.created_at)
                                - kd.created_at)) * 1000)::INT8,
           CASE WHEN kd.decision LIKE 'REJECTED%'          THEN 'FAILED'
                WHEN kd.decision = 'RETRYABLE_CONCURRENCY' THEN 'RETRIED'
                WHEN kd.decision = 'NOOP_DUPLICATE'        THEN 'SKIPPED'
                ELSE 'OK' END,
           kd.proposal_id::STRING,
           jsonb_build_object(
               'kernel_decision_id',   kd.id,
               'decision',             kd.decision,
               'reason_codes',         kd.reason_codes,
               'case_revision_before', kd.case_revision_before,
               'case_revision_after',  kd.case_revision_after,
               'retry_count',          kd.retry_count)
    FROM kernel_decisions AS kd
    WHERE kd.trace_id = $1 AND kd.tenant_id = $2 AND kd.user_id = $3
),
db_transaction AS (
    SELECT 'DB_TRANSACTION', 'kernel_decisions', kd.id::STRING || '#txn',
           COALESCE(kd.committed_at, kd.created_at),
           (EXTRACT(EPOCH FROM (COALESCE(kd.committed_at, kd.created_at)
                                - kd.created_at)) * 1000)::INT8,
           CASE WHEN kd.committed_at IS NULL THEN 'SKIPPED' ELSE 'OK' END,
           kd.id::STRING,
           jsonb_build_object(
               'isolation',   'SERIALIZABLE',
               'retry_count', kd.retry_count,
               'case_revision_before', kd.case_revision_before,
               'case_revision_after',  kd.case_revision_after,
               'belief_versions_written',
                   (SELECT count(*) FROM belief_versions bv
                     WHERE bv.kernel_decision_id = kd.id),
               'support_edges_written',
                   (SELECT COALESCE(sum(bv.support_edge_count), 0) FROM belief_versions bv
                     WHERE bv.kernel_decision_id = kd.id),
               'state_transitions_written',
                   (SELECT count(*) FROM state_transitions st
                     WHERE st.kernel_decision_id = kd.id),
               'outbox_events_written',
                   (SELECT count(*) FROM outbox_events oe
                     WHERE oe.trace_id = kd.trace_id
                       AND oe.aggregate_id = kd.case_id
                       AND oe.aggregate_version = kd.case_revision_after),
               'rows_written_attributable',
                   (SELECT count(*) FROM belief_versions bv
                     WHERE bv.kernel_decision_id = kd.id)
                 + (SELECT COALESCE(sum(bv.support_edge_count), 0) FROM belief_versions bv
                     WHERE bv.kernel_decision_id = kd.id)
                 + (SELECT count(*) FROM state_transitions st
                     WHERE st.kernel_decision_id = kd.id)
                 + (SELECT count(*) FROM outbox_events oe
                     WHERE oe.trace_id = kd.trace_id
                       AND oe.aggregate_id = kd.case_id
                       AND oe.aggregate_version = kd.case_revision_after)
                 + CASE WHEN kd.case_revision_after > kd.case_revision_before THEN 1 ELSE 0 END
                 + 1)
    FROM kernel_decisions AS kd
    WHERE kd.trace_id = $1 AND kd.tenant_id = $2 AND kd.user_id = $3
),
outbox_event AS (
    SELECT 'OUTBOX_EVENT', 'outbox_events', oe.id::STRING,
           oe.occurred_at,
           (EXTRACT(EPOCH FROM (COALESCE(oe.dispatched_at, oe.occurred_at)
                                - oe.occurred_at)) * 1000)::INT8,
           CASE oe.status WHEN 'DISPATCHED'       THEN 'OK'
                          WHEN 'DEAD'             THEN 'FAILED'
                          WHEN 'FAILED_RETRYABLE' THEN 'RETRIED'
                          ELSE 'PENDING' END,
           NULL::STRING,
           jsonb_build_object(
               'event_id',          oe.id,
               'event_type',        oe.event_type,
               'aggregate_type',    oe.aggregate_type,
               'aggregate_version', oe.aggregate_version,
               'attempt_count',     oe.attempt_count,
               'status',            oe.status)
    FROM outbox_events AS oe
    WHERE oe.trace_id = $1 AND oe.tenant_id = $2 AND oe.user_id = $3
      AND oe.event_type NOT IN ('artifact.parsed.v1', 'evidence.admitted.v1',
                                'trigger.fired.v1', 'trigger.noop.v1')
),
event_consumer AS (
    SELECT 'EVENT_CONSUMER', 'processed_events',
           pe.consumer_name || '|' || pe.event_id::STRING,
           pe.processed_at, NULL::INT8, 'OK', oe.id::STRING,
           jsonb_build_object(
               'consumer_name', pe.consumer_name,
               'event_id',      pe.event_id,
               'event_type',    oe.event_type)
    FROM processed_events AS pe
    JOIN outbox_events AS oe ON oe.id = pe.event_id
    WHERE oe.trace_id = $1 AND oe.tenant_id = $2 AND oe.user_id = $3
),
trigger_evaluation AS (
    SELECT 'TRIGGER_EVALUATION', 'outbox_events', oe.id::STRING,
           oe.occurred_at, NULL::INT8,
           CASE WHEN oe.event_type = 'trigger.fired.v1' THEN 'OK' ELSE 'SKIPPED' END,
           NULL::STRING,
           jsonb_build_object(
               'trigger_id',            oe.aggregate_id,
               'trigger_type',          pt.trigger_type,
               'result',                pt.last_result,
               'reason_code',           pt.last_reason_code,
               'state',                 pt.state,
               'evaluation_version',    pt.evaluation_version,
               'basis_case_revision',   pt.basis_case_revision,
               'field_values',          oe.payload -> 'field_values')
    FROM outbox_events AS oe
    JOIN prospective_triggers AS pt
      ON pt.tenant_id = oe.tenant_id AND pt.user_id = oe.user_id
     AND pt.id = oe.aggregate_id
    WHERE oe.trace_id = $1 AND oe.tenant_id = $2 AND oe.user_id = $3
      AND oe.event_type IN ('trigger.fired.v1', 'trigger.noop.v1')
),
action_intent AS (
    SELECT 'ACTION_INTENT', 'action_intents', ai.id::STRING,
           ai.created_at, NULL::INT8,
           CASE WHEN ai.status IN ('REJECTED','CANCELLED','CANCELLED_STALE') THEN 'SKIPPED'
                WHEN ai.status IN ('FAILED_FINAL')                           THEN 'FAILED'
                WHEN ai.status = 'PROPOSED'                                  THEN 'PENDING'
                ELSE 'OK' END,
           ai.created_by_agent_run_id::STRING,
           jsonb_build_object(
               'action_intent_id',     ai.id,
               'action_type',          ai.action_type,
               'status',               ai.status,
               'risk_tier',            ai.risk_tier,
               'basis_case_revision',  ai.basis_case_revision,
               'draft_sha256',         encode(ai.draft_sha256, 'hex'),
               'supporting_belief_versions',
                    jsonb_array_length(ai.supporting_belief_versions))
    FROM action_intents AS ai
    JOIN agent_runs AS ar
      ON ar.tenant_id = ai.tenant_id AND ar.user_id = ai.user_id
     AND ar.id = ai.created_by_agent_run_id
    WHERE ar.trace_id = $1 AND ar.tenant_id = $2 AND ar.user_id = $3
),
action_approval AS (
    SELECT 'ACTION_APPROVAL', 'state_transitions', st.id::STRING,
           st.recorded_at, NULL::INT8, 'OK', st.subject_id::STRING,
           jsonb_build_object(
               'action_intent_id',      st.subject_id,
               'from_state',            st.from_state,
               'to_state',              st.to_state,
               'reason_code',           st.reason_code,
               'approved_case_revision', st.case_revision)
    FROM state_transitions AS st
    WHERE st.trace_id = $1 AND st.tenant_id = $2 AND st.user_id = $3
      AND st.transition_type = 'ACTION_STATE'
      AND st.to_state IN ('APPROVED', 'REJECTED')
),
action_execution AS (
    SELECT 'ACTION_EXECUTION', 'action_executions', ax.id::STRING,
           ax.started_at,
           (EXTRACT(EPOCH FROM (COALESCE(ax.finished_at, ax.started_at)
                                - ax.started_at)) * 1000)::INT8,
           CASE ax.status WHEN 'SUCCEEDED' THEN 'OK'
                          WHEN 'STARTED'   THEN 'PENDING'
                          ELSE 'FAILED' END,
           ax.action_intent_id::STRING,
           jsonb_build_object(
               'action_execution_id',       ax.id,
               'attempt_no',                ax.attempt_no,
               'provider',                  ax.provider,
               'provider_correlation_id',   ax.provider_correlation_id,
               'revalidated_case_revision', ax.revalidated_case_revision,
               'status',                    ax.status,
               'error_code',                ax.error_code)
    FROM action_executions AS ax
    JOIN action_intents AS ai
      ON ai.tenant_id = ax.tenant_id AND ai.user_id = ax.user_id
     AND ai.id = ax.action_intent_id
    JOIN agent_runs AS ar
      ON ar.tenant_id = ai.tenant_id AND ar.user_id = ai.user_id
     AND ar.id = ai.created_by_agent_run_id
    WHERE ar.trace_id = $1 AND ar.tenant_id = $2 AND ar.user_id = $3
)
SELECT * FROM api_request
UNION ALL SELECT * FROM artifact_parse
UNION ALL SELECT * FROM embedding
UNION ALL SELECT * FROM agent_run
UNION ALL SELECT * FROM model_call
UNION ALL SELECT * FROM mcp_tool_call
UNION ALL SELECT * FROM retrieval
UNION ALL SELECT * FROM proposal
UNION ALL SELECT * FROM kernel_decision
UNION ALL SELECT * FROM db_transaction
UNION ALL SELECT * FROM outbox_event
UNION ALL SELECT * FROM event_consumer
UNION ALL SELECT * FROM trigger_evaluation
UNION ALL SELECT * FROM action_intent
UNION ALL SELECT * FROM action_approval
UNION ALL SELECT * FROM action_execution
ORDER BY started_at, source_table, source_pk;
```

Notes an implementer needs:

- **Every branch is tenant- and user-scoped.** `trace_id` alone is not an authorisation key. Two of the branches reach their scope through a join to `agent_runs` or `outbox_events`; those joins carry `tenant_id` and `user_id` in the `ON` clause, so a foreign row is not found rather than found-and-denied (`15_API_SPEC.md` §1.7).
- **`ORDER BY started_at, source_table, source_pk`** is a total order, so the ordinal node ids (`n1`, `n2`, …) assigned at serialisation are stable across calls. An unstable ordering would make the integrity digest of §7 unstable and the whole check worthless.
- **The `RETRIEVAL` node's `vector_candidates` comes from `agent_runs.retrieval_candidate_count`**, a column that already exists. The richer funnel (`candidates_in`/`candidates_out` per stage) lives in spans, not rows, and is therefore shown in the systems-status panel rather than in the trace DAG. This is a deliberate limit: the trace shows what is durable.
- **Counterfactual runs are excluded** by `ar.is_counterfactual = false`, matching `15_API_SPEC.md` §8.30's requirement that counterfactual runs stay out of case timelines. They are rendered by the counterfactual endpoint, on their own.
- **`prospective_triggers` is joined for display fields only.** The row is mutable — `last_result` reflects the most recent evaluation — so a trace rendered long after a re-arm could show a later result than the one the event recorded. The event payload's `field_values` is the immutable half and is what the UI renders as primary. This is called out again in §11.

### 6.4 Edges

Edges are derived, deterministically, by two rules applied in order. There is no stored edge table and no hand-authored graph.

1. **Explicit parentage.** A node with a non-null `parent_key` gets one edge from the node whose `source_pk` equals that key. This covers `MODEL_CALL`/`MCP_TOOL_CALL`/`RETRIEVAL` → `AGENT_RUN`, `PROPOSAL` → `AGENT_RUN`, `KERNEL_DECISION` → `PROPOSAL`, `DB_TRANSACTION` → `KERNEL_DECISION`, `EVENT_CONSUMER` → `OUTBOX_EVENT`, `ACTION_INTENT` → `AGENT_RUN`, `ACTION_APPROVAL` → `ACTION_INTENT`, `ACTION_EXECUTION` → `ACTION_INTENT`.
2. **Temporal spine for the roots.** Nodes with a null `parent_key` are chained in `started_at` order: `API_REQUEST` → `ARTIFACT_PARSE` → `EMBEDDING` → `AGENT_RUN` → `OUTBOX_EVENT` → … This is the flow's backbone and it is honest: these stages genuinely happen in sequence within one trace, and the ordering comes from persisted timestamps rather than from an assumed script.

```python
# packages/python/provenance_db/read_models/memory_trace.py  (edge derivation)
def derive_edges(nodes: list[TraceNode]) -> list[tuple[str, str]]:
    by_pk = {n.source_pk: n.id for n in nodes}
    edges: list[tuple[str, str]] = []

    for node in nodes:
        if node.parent_key and node.parent_key in by_pk:
            edges.append((by_pk[node.parent_key], node.id))

    spine = [n for n in nodes if not n.parent_key]      # already ordered by started_at
    edges.extend((a.id, b.id) for a, b in zip(spine, spine[1:]))
    return edges
```

A `parent_key` that does not resolve produces **no** edge and leaves the node parentless. It does not produce a synthetic parent. A dangling parent is a data problem and the correct rendering of a data problem is a visible orphan, not a repaired graph.

### 6.5 `rows_written` is a lower bound, and says so

`claims`, `conflicts`, `commitments`, and `fulfillments` do not carry `kernel_decision_id`. `belief_versions` and `state_transitions` do. Therefore the number the `DB_TRANSACTION` node reports is named `rows_written_attributable` and is the exact count of rows the database can **prove** belong to that decision:

```text
rows_written_attributable
  = count(belief_versions   WHERE kernel_decision_id = kd.id)
  + sum(support_edge_count  WHERE kernel_decision_id = kd.id)     -- grounding edges
  + count(state_transitions WHERE kernel_decision_id = kd.id)
  + count(outbox_events     WHERE trace_id = kd.trace_id
                              AND aggregate_id = kd.case_id
                              AND aggregate_version = kd.case_revision_after)
  + 1 if the case revision advanced                              -- the cases UPDATE
  + 1                                                            -- the kernel_decisions row
```

Claims and fulfillments are counted only where the same commit also wrote a `state_transitions` row naming them. Where it did not, they are not counted. Reporting seven when the database can prove six would be a small lie in the one panel whose entire value is that it does not contain any. §11 records the additive fix.

### 6.6 Additive DDL dependencies

This contract needs four columns that `15_API_SPEC.md` already assumes and `10_DATABASE_DDL.md` does not yet declare. They are listed as dependencies, exactly as `15_API_SPEC.md` §15 lists its own, and they must be reconciled into the DDL before Phase 2 rather than discovered in Phase 12.

| Column | Type | Required by | Consequence if absent |
|---|---|---|---|
| `idempotency_records.trace_id` | `UUID NOT NULL` | `15_API_SPEC.md` §6.3 | The `api_request` CTE returns zero rows; traces lose their `API_REQUEST` node and start at `ARTIFACT_PARSE`. |
| `agent_runs.tool_calls` | `JSONB NULL` | `15_API_SPEC.md` §8.29, §9.9 | No `MCP_TOOL_CALL` nodes. `G11.4` cannot pass, and MCP visibility is unprovable. |
| `agent_runs.model_calls` | `JSONB NULL` | `15_API_SPEC.md` §9.9 | No `MODEL_CALL` nodes; the deterministic/model boundary block in `15_API_SPEC.md` §8.28 has nothing to classify. |
| `agent_runs.capability_status` | `STRING NOT NULL DEFAULT 'ACTIVE'` | `15_API_SPEC.md` §3.3, §3.7 | Capability lifecycle is unobservable; `CAPABILITY_CONSUMED` cannot be distinguished from a crashed run. |

Without those four the assembly SQL still runs and still returns a coherent, truthful trace — it is simply a smaller one. Nothing degrades into invention, which is the property that matters.

---

## 7. Trace integrity

### 7.1 The problem a reviewer actually has

A reviewer looking at Judge Mode sees a beautiful DAG and has no way to distinguish four cases:

1. The DAG is a projection of rows written by a live run. (What we claim.)
2. The DAG is a projection of rows written by the seed script and replayed. (Plausible, and a much easier build.)
3. The DAG is a projection of *some* rows, with the inconvenient ones filtered out at render time.
4. The DAG is a hand-authored fixture served by the API.

Asserting (1) does not distinguish it from (2), (3), or (4). `CANONICAL_DECISIONS.md` forbids (4) and `23_PHASE_GATES.md` `G12.2`/`G12.3` test for it from the browser side. This section adds the server-side proof, and it is the difference between "we say the trace is real" and "here is a command that fails if it is not".

### 7.2 Digests

Every node carries a `source` block and a `node_digest` computed **from the row**, not from the rendered node:

```python
# packages/python/provenance_db/read_models/trace_integrity.py
"""Digest a trace so a third party can recompute it from the database.

The digest covers the row identity and the projected attribute values. It does
NOT cover presentation: renaming a summary string or reordering the JSON keys
must not change the digest, or the check becomes a formatting test.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

DIGEST_ALGORITHM = "PV-TRACE-DIGEST-1"


def _jcs(value: Any) -> bytes:
    """RFC 8785-style canonical JSON: sorted keys, no insignificant whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def node_digest(*, node_type: str, source_table: str, source_pk: str,
                attributes: dict[str, Any]) -> str:
    material = _jcs({
        "a": DIGEST_ALGORITHM,
        "t": node_type,
        "s": source_table,
        "p": source_pk,
        "v": attributes,
    })
    return hashlib.sha256(material).hexdigest()


def trace_digest(node_digests: list[str], census: dict[str, int]) -> str:
    material = _jcs({
        "a": DIGEST_ALGORITHM,
        "n": sorted(node_digests),          # order-independent by construction
        "c": {k: census[k] for k in sorted(census)},
    })
    return "sha256:" + hashlib.sha256(material).hexdigest()
```

`GET /v1/traces/{trace_id}?include=integrity` adds one block. Additive response fields are not breaking (`15_API_SPEC.md` §16.2), so this extends §8.28 rather than changing it:

```json
{
  "integrity": {
    "algorithm": "PV-TRACE-DIGEST-1",
    "assembled_at": "2026-09-18T14:31:02.418Z",
    "assembled_by_query_sha256": "4c1e…9b02",
    "row_census": [
      { "source": "idempotency_records", "rows": 1 },
      { "source": "outbox_events",       "rows": 4 },
      { "source": "agent_runs",          "rows": 2 },
      { "source": "agent_runs.tool_calls",  "rows": 3 },
      { "source": "agent_runs.model_calls", "rows": 3 },
      { "source": "memory_proposals",    "rows": 1 },
      { "source": "kernel_decisions",    "rows": 1 },
      { "source": "state_transitions",   "rows": 2 },
      { "source": "processed_events",    "rows": 2 },
      { "source": "action_intents",      "rows": 1 },
      { "source": "action_executions",   "rows": 1 }
    ],
    "trace_digest": "sha256:9f2c…41ab",
    "liveness": {
      "seeded_source_pks": 0,
      "earliest_node_at": "2026-09-18T14:05:02.001Z",
      "deployment_started_at": "2026-09-18T09:14:33.000Z",
      "all_nodes_after_deployment": true
    },
    "verify_command": "python -m tools.trace_verify --trace-id 018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90 --api https://api.provenance.app --assert-live"
  }
}
```

Each node additionally carries:

```json
{ "id": "n10", "type": "KERNEL_DECISION",
  "source": { "table": "kernel_decisions",
              "pk": "018f8b90-0000-7000-8000-000000000002",
              "digest": "b71e…2f04" } }
```

### 7.3 The verifier

`tools/trace_verify.py` is the whole point. It reads the rendered trace over HTTP and the rows over SQL, using **different credentials and a different code path**, and compares them. It runs as `pv_ops_reader` — the optional fifth role in `CANONICAL_DECISIONS.md` — which holds `SELECT` on the operational tables and nothing else, so running the verifier cannot itself change what it is verifying.

```bash
asm-exec --env PV_DB_OPS='{{resolve:secretsmanager:provenance/db:SecretString:ops_reader_url}}' -- \
python -m tools.trace_verify \
  --trace-id 018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90 \
  --api "$PV_API" \
  --token "$PV_JUDGE_TOKEN" \
  --assert-live
```

```text
PV-TRACE-DIGEST-1   trace 018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90

  rendered nodes ............................ 16
  rows found in database .................... 16
  digest matches ............................ 16
  rendered but not in database .............. 0      <- fabrication
  in database but not rendered .............. 0      <- suppression
  digest mismatches ......................... 0      <- alteration
  trace_digest (recomputed) ................. sha256:9f2c…41ab
  trace_digest (as served) .................. sha256:9f2c…41ab   MATCH

  liveness
    source pks minted by the seed (uuid5) ... 0 of 16
    nodes before deployment start ........... 0 of 16
    agent_runs.agent_mode ................... LIVE
    kernel_decisions.committed_at range ..... 2026-09-18T14:05:19Z .. 14:25:04Z

  VERDICT: PASS
```

Its four checks, and what each one falsifies:

| Check | Falsifies |
|---|---|
| Every rendered node's digest recomputes from a row | **Fabrication.** A node with no row, or a node whose values do not match its row, fails here. |
| Every row the census SQL finds has a rendered node | **Suppression.** A trace that quietly drops the failed model call, the denied MCP call, or the rejected proposal fails here. |
| `trace_digest` served equals `trace_digest` recomputed | **Tampering in transit or in a cache.** Also catches an unstable node ordering, which would otherwise make the other checks flaky. |
| `--assert-live`: no node's `source_pk` appears in `db/seeds/SEEDED_IDS.txt`, and every node timestamp is after the deployment's start | **Replay of seeded data.** Every seeded id is minted with `uuid5` from `PROVENANCE_SEED_NS` (`10_DATABASE_DDL.md` §17.1) and the seed writes the sorted list of ids it minted. A "live" trace containing a seeded `kernel_decision_id` is a replay, and the verifier says so. |

The seeded-id manifest is produced by the seed itself, so it cannot drift:

```python
# scripts/seed/__main__.py (excerpt)
def write_id_manifest(minted: set[uuid.UUID], path: Path) -> None:
    """Every id the seed minted, sorted. The liveness check reads this file."""
    path.write_text("\n".join(sorted(str(i) for i in minted)) + "\n", encoding="utf-8")
```

`db/seeds/SEEDED_IDS.txt` is committed and is checked by `python -m tools.manifest_check` at `G2.6`, alongside the row-count manifest.

### 7.4 What this does not prove, stated plainly

Three honest limits. A reviewer who asks about any of them deserves the real answer, and the real answer is better than a defensive one.

1. **`agent_runs.tool_calls` and `model_calls` are caller-reported.** The AgentCore tool wrapper writes them at `POST /internal/v1/agent-runs/{id}/complete`. A compromised or buggy agent could under-report. `15_API_SPEC.md` §17.13 says this in the same words. The verifier proves the rendered MCP panel matches the stored array; it does not prove the stored array matches reality. The authoritative record of what the agent *could* read is the SQL grant on `pv_agent_reader`, which `G11.1` and `G11.2` demonstrate by refusal, and which is a stronger guarantee than a log anyway: an audit log tells you what happened, a grant tells you what can happen. Server-side proof of what was actually read would require CockroachDB audit logging or MCP server-side logging, neither of which is wired up in v1.
2. **A digest match proves correspondence, not correctness.** If the Kernel wrote the wrong `case_revision_after`, the trace faithfully renders the wrong number and the verifier passes. Correctness of the write is the Kernel's problem and is gated separately at `G-4`; this section is about the rendering being faithful to the write.
3. **The verifier trusts the database.** Somebody with `pv_migrator` could write consistent lies into both the rows and the seeded-id manifest. That is a compromise-of-the-operator scenario, not a demo-credibility scenario, and defending against it would need external attestation this build has no way to provide. It is out of scope and is said so rather than implied away.

### 7.5 The two-minute verification protocol

Everything above is only useful if a skeptical reader can run it fast. This is that sequence:

```bash
# 1. Do the thing live. Forward the invoice, or POST it.
TRACE=$(curl -sS -X POST "$PV_API/v1/artifacts/$ARTIFACT/complete" \
          -H "Authorization: Bearer $PV_JUDGE_TOKEN" \
          -H "Idempotency-Key: $(uuidgen)" -d @body.json \
          -D- -o /dev/null | grep -i '^x-provenance-trace-id' | tr -d '\r' | awk '{print $2}')

# 2. Verify the trace it produced corresponds to rows, and that none of them are seeded.
python -m tools.trace_verify --trace-id "$TRACE" --api "$PV_API" \
       --token "$PV_JUDGE_TOKEN" --assert-live
#    → VERDICT: PASS

# 3. Break it on purpose. Delete the agent_runs row and reload Judge Mode.
#    The MCP panel must go EMPTY, not fall back to a template.
asm-exec --env U='{{resolve:secretsmanager:provenance/db:SecretString:app_url}}' -- \
  cockroach sql --url "$U" -e \
  "UPDATE agent_runs SET tool_calls = NULL WHERE trace_id = '$TRACE';"
curl -sS "$PV_API/v1/traces/$TRACE" -H "Authorization: Bearer $PV_JUDGE_TOKEN" \
  | jq '[.nodes[] | select(.type=="MCP_TOOL_CALL")] | length'
#    → 0     (a non-zero answer here means the panel is rendering something else)

# 4. Change the truth and watch the trace move.
curl -sS -X POST "$PV_API/v1/cases/$CASE/corrections" -H "Authorization: Bearer $PV_JUDGE_TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" -d @correction.json | jq -r '.case_revision_after'
#    → 14     (and GET /v1/cases/$CASE/memory-trace gains a new item at the top)
```

Step 3 is deliberately destructive against the demo tenant and is reversible with `make seed`. A team that will not let a reviewer run step 3 does not believe its own trace.

---

## 8. CloudWatch dashboard and alarms

### 8.1 Two dashboards, because they answer different questions

| Dashboard | Audience | Question it answers | Refresh |
|---|---|---|---|
| `provenance-demo` | The presenter, during the video and live Q&A | "Is the system healthy enough to demo right now, and did the thing I just did actually commit?" | 10 s |
| `provenance-ops` | The builder and the gate reviewer | "What is degrading, and which of the five quality families is it in?" | 1 min |

Both are committed as JSON and applied by the CDK stack, so a dashboard change is a reviewable diff rather than a click in the console.

### 8.2 `ops/observability/dashboard-demo.json`

```json
{
  "start": "-PT1H",
  "periodOverride": "inherit",
  "widgets": [
    { "type": "text", "x": 0, "y": 0, "width": 24, "height": 2,
      "properties": { "markdown": "# Provenance — demo health\nGreen means the hero flow can run. Every number below is read from CockroachDB or from the request path; none is seeded at render time." } },

    { "type": "metric", "x": 0, "y": 2, "width": 6, "height": 5,
      "properties": {
        "title": "Money outstanding (USD)", "view": "singleValue", "region": "us-east-1",
        "stat": "Maximum", "period": 60,
        "metrics": [["Provenance/Memory", "outstanding_total", "currency", "USD"]] } },

    { "type": "metric", "x": 6, "y": 2, "width": 6, "height": 5,
      "properties": {
        "title": "Cases: active / reopened / urgent", "view": "singleValue",
        "region": "us-east-1", "stat": "Maximum", "period": 60,
        "metrics": [
          ["Provenance/Memory", "active_cases"],
          [".", "reopened_cases"],
          [".", "urgent_cases"]] } },

    { "type": "metric", "x": 12, "y": 2, "width": 6, "height": 5,
      "properties": {
        "title": "Obligations: unresolved / overdue / conflicts", "view": "singleValue",
        "region": "us-east-1", "stat": "Maximum", "period": 60,
        "metrics": [
          ["Provenance/Memory", "unresolved_commitments"],
          [".", "overdue_commitments"],
          [".", "active_conflicts"]] } },

    { "type": "metric", "x": 18, "y": 2, "width": 6, "height": 5,
      "properties": {
        "title": "Prospective memory armed", "view": "singleValue",
        "region": "us-east-1", "stat": "Maximum", "period": 60,
        "metrics": [["Provenance/Trigger", "armed"]] } },

    { "type": "metric", "x": 0, "y": 7, "width": 8, "height": 6,
      "properties": {
        "title": "Kernel commits and retries", "view": "timeSeries", "stacked": false,
        "region": "us-east-1", "period": 60,
        "metrics": [
          ["Provenance/Agent", "proposal_decision", "decision", "ACCEPTED", { "stat": "Sum" }],
          ["...", "ACCEPTED_WITH_CONFLICT", { "stat": "Sum" }],
          ["Provenance/Db", "serialization_retry", "txn_name", "kernel_commit",
            { "stat": "Sum", "yAxis": "right" }]],
        "yAxis": { "left": { "label": "commits" }, "right": { "label": "40001 retries" } } } },

    { "type": "metric", "x": 8, "y": 7, "width": 8, "height": 6,
      "properties": {
        "title": "Artifact → canonical commit (ms)", "view": "timeSeries",
        "region": "us-east-1", "period": 60,
        "metrics": [
          ["Provenance/Memory", "artifact_to_commit_ms", { "stat": "p50" }],
          ["...", { "stat": "p95" }]],
        "annotations": { "horizontal": [
          { "label": "p95 alarm", "value": 45000, "color": "#d13212" }] } } },

    { "type": "metric", "x": 16, "y": 7, "width": 8, "height": 6,
      "properties": {
        "title": "Retrieval funnel", "view": "timeSeries", "region": "us-east-1",
        "period": 60,
        "metrics": [
          ["Provenance/Retrieval", "candidates", "stage", "D", "direction", "out", { "stat": "Average" }],
          ["...", "G", ".", "in",  { "stat": "Average" }],
          ["...", "G", ".", "out", { "stat": "Average" }],
          ["Provenance/Retrieval", "identity.exact_match_count", { "stat": "Sum" }]] } },

    { "type": "metric", "x": 0, "y": 13, "width": 8, "height": 6,
      "properties": {
        "title": "Outbox: pending age and dead", "view": "timeSeries",
        "region": "us-east-1", "period": 60,
        "metrics": [
          ["Provenance/Outbox", "oldest_pending_age_seconds", { "stat": "Maximum" }],
          ["Provenance/Outbox", "dead_count", { "stat": "Maximum", "yAxis": "right" }],
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible",
            "QueueName", "provenance-events-dlq", { "stat": "Maximum", "yAxis": "right" }]],
        "annotations": { "horizontal": [
          { "label": "stalled", "value": 120, "color": "#d13212" }] } } },

    { "type": "metric", "x": 8, "y": 13, "width": 8, "height": 6,
      "properties": {
        "title": "Actions: proposed → approved → executed", "view": "timeSeries",
        "stacked": true, "region": "us-east-1", "period": 300,
        "metrics": [
          ["Provenance/Action", "intent_transition", "to_status", "PROPOSED", { "stat": "Sum" }],
          ["...", "APPROVED",        { "stat": "Sum" }],
          ["...", "EXECUTED",        { "stat": "Sum" }],
          ["...", "REJECTED",        { "stat": "Sum" }],
          ["...", "CANCELLED_STALE", { "stat": "Sum" }]] } },

    { "type": "metric", "x": 16, "y": 13, "width": 8, "height": 6,
      "properties": {
        "title": "Model route and spend", "view": "timeSeries", "region": "us-east-1",
        "period": 300,
        "metrics": [
          ["Provenance/Model", "invocations", "tier", "E", { "stat": "Sum" }],
          ["...", "R", { "stat": "Sum" }],
          ["Provenance/Cost", "artifact_usd_micros",
            { "stat": "p95", "yAxis": "right", "label": "p95 cost / artifact (µUSD)" }]] } },

    { "type": "log", "x": 0, "y": 19, "width": 24, "height": 6,
      "properties": {
        "title": "Last 20 canonical commits", "region": "us-east-1",
        "query": "SOURCE '/provenance/control-plane' | fields @timestamp, case_id, decision, case_revision_before, case_revision_after, retry_count, duration_ms, trace_id\n| filter event = 'kernel_decision_committed'\n| sort @timestamp desc\n| limit 20" } }
  ]
}
```

Applied by the stack, and by hand when iterating:

```bash
aws cloudwatch put-dashboard \
  --dashboard-name provenance-demo \
  --dashboard-body file://ops/observability/dashboard-demo.json
```

`provenance-ops` follows the same shape with five sections matching §4.2 to §4.6: memory, agent quality, retrieval, database, cost. It is not reproduced here because its widget list is mechanically derivable from the metric catalogue and adding a second 200-line JSON blob would not add information.

### 8.3 The alarm set

Alarms are declared in one committed file so the set is reviewable as a list rather than as scattered constructor calls.

```yaml
# ops/observability/alarms.yaml
# Every alarm: name, the metric it watches, the threshold, the reason the
# threshold is where it is, and what an operator does when it fires.
# treat_missing_data is explicit on every alarm. G13.7 requires every alarm to
# be in OK, not INSUFFICIENT_DATA, so a metric that is legitimately absent at
# rest must declare notBreaching rather than be left to default.

defaults:
  namespace_prefix: Provenance
  alarm_actions: [ "arn:aws:sns:us-east-1:${ACCOUNT_ID}:provenance-alerts" ]
  ok_actions:    [ "arn:aws:sns:us-east-1:${ACCOUNT_ID}:provenance-alerts" ]

alarms:

  # ---- P1: correctness. These mean the record may be wrong. ----------------
  - name: provenance-retrieval-retracted-leakage
    severity: P1
    namespace: Provenance/Retrieval
    metric: retracted_leakage
    statistic: Sum
    period: 60
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      Retracted, superseded, or quarantined evidence reached a caller. Corrected
      evidence resurfacing and re-grounding a belief is a silent, plausible-looking
      failure and is the exact defect canon item C exists to prevent.
    runbook: >
      Stop ingestion. Check the retraction predicate in agent_evidence_retrieval_v1
      and in the repository query. Re-run tests/db/test_12 part (c) and its positive
      control (d). Do not restart ingestion until both pass.

  - name: provenance-retrieval-cross-user-rows
    severity: P1
    namespace: Provenance/Retrieval
    metric: cross_user_rows
    statistic: Sum
    period: 60
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      ANN or rerank returned another user's row. The user_id vector prefix makes
      this a schema property, so a non-zero value means the prefix was bypassed.
    runbook: >
      The agent run fails closed automatically. Revoke pv_agent_reader, run
      tests/retrieval/test_isolation.py, and check EXPLAIN for the prefix column.

  - name: provenance-state-proof-ungrounded-belief
    severity: P1
    namespace: Provenance/StateProof
    metric: ungrounded_belief
    statistic: Sum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      A canonical belief version rendered with zero grounding edges. The Kernel
      refuses to create one and the CHECK constraint refuses to store one, so this
      is unreachable in a correct system.
    runbook: >
      Data-integrity incident. Run make db-verify; V2 identifies the row. Do not
      "fix" by adding a support edge -- find out how it was written.

  - name: provenance-auth-tenant-mismatch
    severity: P1
    namespace: Provenance/Auth
    metric: tenant_mismatch
    statistic: Sum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      A cross-tenant access attempt. In a correct system this never fires, so a
      single occurrence is a bug or an attack. It is logged at WARN with
      reason TENANT_MISMATCH and is invisible to the caller, who sees only a 404.
    runbook: >
      Log Insights on client_id and route. If client_id is provenance-agent-runtime,
      treat as a capability-binding defect and revoke the agent_runs row.

  - name: provenance-proposal-foreign-provenance
    severity: P1
    namespace: Provenance/Agent
    metric: proposal_decision
    dimensions: { decision: REJECTED_INVALID_PROVENANCE }
    statistic: Sum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      A proposal named a user or referenced evidence outside its capability. The
      tripwire is intentional: a correct system cannot produce one.
    runbook: >
      Pull the memory_proposals row. Compare MemoryProposal.user_id to the
      agent_runs binding. This is the prompt-injection escalation path failing safe.

  - name: provenance-model-tier-r-fallback
    severity: P1
    namespace: Provenance/Model
    metric: fallback
    dimensions: { from_tier: R }
    statistic: Sum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      Tier R never downgrades to a weaker model. A Tier R fallback is a router
      violation of a frozen decision, not a degradation.
    runbook: Roll back the router change. Failure must produce PENDING_HUMAN_REVIEW.

  # ---- P2: delivery and durability ---------------------------------------
  - name: provenance-outbox-dead
    severity: P1
    namespace: Provenance/Outbox
    metric: dead_count
    statistic: Maximum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: An event exhausted 1s/5s/30s/2m/10m and will never be delivered.
    runbook: >
      Inspect last_error. Fix the cause, then replay: the consumer dedupe in
      processed_events makes replay safe by construction.

  - name: provenance-outbox-pending-age
    severity: P2
    namespace: Provenance/Outbox
    metric: oldest_pending_age_seconds
    statistic: Maximum
    period: 60
    evaluation_periods: 2
    threshold: 120
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      The scheduled sweep runs every 30 s, so 120 s is four missed sweeps -- past
      any plausible jitter and short of a demo-ruining backlog.
    runbook: >
      POST /internal/v1/events/outbox/sweep manually. If that drains it, the
      EventBridge Scheduler rule is disabled or throttled.

  - name: provenance-dlq-depth
    severity: P1
    namespace: AWS/SQS
    metric: ApproximateNumberOfMessagesVisible
    dimensions: { QueueName: provenance-events-dlq }
    statistic: Maximum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: A poisoned event stopped retrying. Someone must look at it.
    runbook: >
      Read the message attributes provenance-trace-id and provenance-event-id;
      open the trace; the payload itself may be malformed, so do not parse it first.

  - name: provenance-outbox-attempt-p99
    severity: P3
    namespace: Provenance/Outbox
    metric: attempt_count_p99
    statistic: Maximum
    period: 300
    evaluation_periods: 3
    threshold: 2
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: Publish failures becoming routine rather than exceptional.
    runbook: Check EventBridge PutEvents throttling and the bus name.

  # ---- P2: transactional health ------------------------------------------
  - name: provenance-kernel-retry-rate
    severity: P2
    namespace: Provenance/Db
    metric_math:
      expression: "IF(commits > 0, retries / commits, 0)"
      variables:
        retries: { namespace: Provenance/Db, metric: serialization_retry,
                   dimensions: { txn_name: kernel_commit }, statistic: Sum, period: 300 }
        commits: { namespace: Provenance/Agent, metric: proposal_decision,
                   dimensions: { decision: ACCEPTED }, statistic: Sum, period: 300 }
    evaluation_periods: 3
    threshold: 0.5
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      SQLSTATE 40001 retries are normal under contention on one hot case; the
      budget is five attempts. More than one retry per two commits means the
      contention set has grown, most likely because the idempotency record joined
      the kernel transaction (15_API_SPEC.md §17.10).
    runbook: >
      Check retry_count distribution per case. If concentrated on one case, that
      case is hot; if spread, the transaction is doing too much.

  - name: provenance-kernel-retry-exhausted
    severity: P2
    namespace: Provenance/Db
    metric: retry_exhausted
    statistic: Sum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: The five-attempt budget ran out and the caller received 503 RETRYABLE_CONCURRENCY.
    runbook: Expected under deliberate contention tests; unexpected otherwise.

  - name: provenance-db-pool-saturation
    severity: P2
    namespace: Provenance/Db
    metric_math:
      expression: "MAX(in_use / size)"
      variables:
        in_use: { namespace: Provenance/Db, metric: pool_in_use, statistic: Maximum, period: 60 }
        size:   { namespace: Provenance/Db, metric: pool_size,   statistic: Maximum, period: 60 }
    evaluation_periods: 5
    threshold: 0.85
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: One pool per SQL role; saturation on any role stalls that role's callers.
    runbook: Identify the role dimension. Kernel saturation is the one that blocks commits.

  # ---- P2: actions --------------------------------------------------------
  - name: provenance-action-abort-rate
    severity: P2
    namespace: Provenance/Action
    metric_math:
      expression: "IF(exec > 0, aborted / exec, 0)"
      variables:
        aborted: { namespace: Provenance/Action, metric: aborted_stale,
                   statistic: Sum, period: 3600 }
        exec:    { namespace: Provenance/Action, metric: intent_transition,
                   dimensions: { to_status: EXECUTED }, statistic: Sum, period: 3600 }
    evaluation_periods: 1
    threshold: 0.2
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      A stale approval aborting is correct behaviour, not a failure. A sustained
      high rate means approvals are racing commits, which usually means
      basis_case_revision is not being advanced at approval (15_API_SPEC.md §17.3).
    runbook: >
      Check stale_reason. CASE_REVISION_MOVED on every execution is the
      self-invalidating-approval bug and looks like a concurrency problem.

  - name: provenance-action-failed-final
    severity: P1
    namespace: Provenance/Action
    metric: intent_transition
    dimensions: { to_status: FAILED_FINAL }
    statistic: Sum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: A human-approved action did not reach the counterparty and will not retry.
    runbook: Check provider and error_code. SES suppression and identity issues look alike.

  # ---- P3: quality drift --------------------------------------------------
  - name: provenance-extraction-schema-invalid-rate
    severity: P3
    namespace: Provenance/Agent
    metric_math:
      expression: "IF(calls > 0, invalid / calls, 0)"
      variables:
        invalid: { namespace: Provenance/Agent, metric: extraction_schema_invalid,
                   statistic: Sum, period: 1800 }
        calls:   { namespace: Provenance/Model, metric: invocations,
                   dimensions: { node: extract_structured_evidence }, statistic: Sum, period: 1800 }
    evaluation_periods: 1
    threshold: 0.10
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      One repair attempt is budgeted per node. Above 10% the repair budget is
      absorbing a systematic problem rather than an occasional one.
    runbook: Diff prompt_version and model_id against the last known-good run.

  - name: provenance-retrieval-latency
    severity: P3
    namespace: Provenance/Retrieval
    metric: latency_ms
    dimensions: { stage: TOTAL }
    statistic: p95
    period: 300
    evaluation_periods: 1
    threshold: 400
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: The eight-stage p95 budget is 210 ms; 400 ms is roughly double and visible on camera.
    runbook: Break down by stage. Stage D is ANN; stage F is grounding expansion.

  - name: provenance-retrieval-index-not-used
    severity: P2
    namespace: Provenance/Retrieval
    metric: vector.index_used
    dimensions: { used: "false" }
    statistic: Sum
    period: 300
    evaluation_periods: 1
    threshold: 0
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      A full scan may return correct results and is still a failure: the claim
      being made is distributed vector indexing, and G6.2 treats a full-scan plan
      as a gate failure even when the answer is right.
    runbook: EXPLAIN the retrieval query. Confirm evidence_embedding_ann_idx by name.

  - name: provenance-identity-unresolved-share
    severity: P3
    namespace: Provenance/Retrieval
    metric_math:
      expression: "IF(total > 0, unresolved / total, 0)"
      variables:
        unresolved: { namespace: Provenance/Retrieval, metric: identity.status,
                      dimensions: { status: UNRESOLVED }, statistic: Sum, period: 3600 }
        total:      { namespace: Provenance/Retrieval, metric: identity.status,
                      statistic: Sum, period: 3600 }
    evaluation_periods: 1
    threshold: 0.30
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      Abstention is cheap and over-abstaining is tolerable, but a third of traffic
      unresolvable means the identity extractors stopped matching real documents.
    runbook: Check retrieval.identity.exact_match_count first; a joint drop is an extractor problem.

  - name: provenance-exact-identifier-hits-zero
    severity: P2
    namespace: Provenance/Retrieval
    metric: identity.exact_match_count
    statistic: Sum
    period: 3600
    evaluation_periods: 1
    threshold: 1
    comparison: LessThanThreshold
    treat_missing_data: notBreaching
    reason: >
      The earliest possible warning that a counterparty changed its invoice format.
      Target is 1.00 on the eval set because it measures determinism, not quality.
    runbook: >
      Run the four-cause table in 13_RETRIEVAL_SPEC.md §15.3: regex miss,
      normalisation mismatch, stopword over-rejection, or an unpopulated ref.

  - name: provenance-prompt-cache-cold
    severity: P3
    namespace: Provenance/Model
    metric: cache_read_input_tokens
    dimensions: { model_id: anthropic.claude-opus-5 }
    statistic: Sum
    period: 3600
    evaluation_periods: 1
    threshold: 1
    comparison: LessThanThreshold
    treat_missing_data: notBreaching
    reason: >
      Verification is a metric, not an assumption. A sustained zero on an Opus 5
      node means something is silently invalidating the stable system prefix --
      usually a timestamp or a user value that crossed the §2.1 prompt boundary.
    runbook: Diff the rendered system block across two invocations; it must be byte-identical.

  - name: provenance-cost-per-artifact
    severity: P3
    namespace: Provenance/Cost
    metric: artifact_usd_micros
    statistic: p95
    period: 3600
    evaluation_periods: 1
    threshold: 250000
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: >
      USD 0.25 per artifact. Not a hard economic limit -- it is the point at which
      the model route deserves a second look, because the budget is 8 model calls
      and one Tier R escalation per artifact.
    runbook: Break down by tier and node. A rising resolver escalation rate is the usual cause.

  # ---- P2: availability ---------------------------------------------------
  - name: provenance-api-5xx-rate
    severity: P2
    namespace: Provenance/Api
    metric_math:
      expression: "IF(total > 0, errors / total, 0)"
      variables:
        errors: { namespace: Provenance/Api, metric: requests,
                  dimensions: { status_class: "5xx" }, statistic: Sum, period: 300 }
        total:  { namespace: Provenance/Api, metric: requests, statistic: Sum, period: 300 }
    evaluation_periods: 2
    threshold: 0.02
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: 2% of requests failing is visible to a user and certain to happen on camera.
    runbook: Group by route. A single route means a handler; all routes means a dependency.

  - name: provenance-db-connection-errors
    severity: P2
    namespace: Provenance/Db
    metric: connection_errors
    statistic: Sum
    period: 300
    evaluation_periods: 1
    threshold: 2
    comparison: GreaterThanThreshold
    treat_missing_data: notBreaching
    reason: CockroachDB Cloud reachability. Three failures in five minutes is not transient.
    runbook: ccloud cluster list; check the IP allowlist and the certificate.

composites:
  - name: provenance-demo-not-ready
    severity: P1
    rule: >
      ALARM(provenance-api-5xx-rate) OR ALARM(provenance-db-connection-errors)
      OR ALARM(provenance-outbox-pending-age) OR ALARM(provenance-dlq-depth)
      OR ALARM(provenance-retrieval-index-not-used)
    reason: >
      One red light for the presenter. Everything in this rule breaks the hero
      flow visibly; nothing in it is a slow-burning quality signal.
```

The loader is short enough to read in full, which is the point of keeping the alarms in data:

```typescript
// infra/cdk/lib/observability-stack.ts  (excerpt)
// If infra/cdk is authored in Python instead, this is the same eight lines
// against aws_cloudwatch.CfnAlarm; the YAML is the contract, not the language.
const spec = yaml.load(fs.readFileSync('ops/observability/alarms.yaml', 'utf8')) as AlarmSpec;

for (const a of spec.alarms) {
  new cloudwatch.CfnAlarm(this, a.name, {
    alarmName: a.name,
    alarmDescription: `${a.severity} - ${a.reason.trim()}`,   // hyphen, not em dash
    namespace: a.metric_math ? undefined : a.namespace,
    metricName: a.metric_math ? undefined : a.metric,
    dimensions: toDimensions(a.dimensions),
    statistic: a.statistic,
    period: a.period,
    evaluationPeriods: a.evaluation_periods,
    threshold: a.threshold,
    comparisonOperator: a.comparison,
    treatMissingData: a.treat_missing_data,
    metrics: a.metric_math ? toMetricDataQueries(a.metric_math) : undefined,
    alarmActions: spec.defaults.alarm_actions,
    okActions: spec.defaults.ok_actions,
  });
}
```

`G13.7` asserts that every alarm exists and is in `OK` rather than `INSUFFICIENT_DATA`, and names four of them explicitly: `provenance-outbox-pending-age`, `provenance-dlq-depth`, `provenance-kernel-retry-rate`, `provenance-action-abort-rate`. All four are above under exactly those names. `treat_missing_data: notBreaching` is set on every alarm precisely so a quiet system reports `OK` rather than `INSUFFICIENT_DATA`, which would otherwise fail that gate on a system that is behaving perfectly.

### 8.4 Saved Log Insights queries

Committed as `ops/observability/queries/*.txt` and installed as CloudWatch saved queries, because the query you need during a demo is the one you did not write down.

```text
# hero-flow-by-trace.txt — the whole story of one artifact
SOURCE '/provenance/control-plane', '/provenance/agents', '/provenance/workers/outbox-dispatch'
| fields @timestamp, service, event, decision, result, case_revision_before,
         case_revision_after, retry_count, duration_ms
| filter trace_id = 'TRACE_ID_HERE'
| sort @timestamp asc
```

```text
# spans-by-trace.txt — G13.5 runs exactly this shape
SOURCE '/provenance/control-plane'
| fields @timestamp, span_name, duration_ms, status
| filter event = 'span' and trace_id = 'TRACE_ID_HERE'
| sort @timestamp asc
```

```text
# commits-without-outbox.txt — the outbox invariant, checked from logs
SOURCE '/provenance/control-plane'
| filter event in ['kernel_decision_committed', 'outbox_event_written']
| stats count(*) as n by trace_id, event
| sort trace_id
# A trace_id with kernel_decision_committed and no outbox_event_written is an
# invariant violation. It should be impossible: both writes are in one
# transaction. If this query ever returns such a row, that is the finding.
```

```text
# redaction-denied.txt — who tried to log something they should not have
SOURCE '/provenance/control-plane', '/provenance/agents'
| fields @timestamp, service, logger, redaction.denied_keys
| filter ispresent(redaction.denied_keys) and redaction.denied_keys != []
| stats count(*) as attempts by logger, redaction.denied_keys
| sort attempts desc
```

```text
# extraction-failures-by-counterparty.txt — which format broke
SOURCE '/provenance/agents'
| fields @timestamp, sender_domain, node, error_code
| filter event = 'extraction_schema_invalid'
| stats count(*) as failures by sender_domain
| sort failures desc
| limit 20
```

```text
# trigger-wakes.txt — fired versus no-op, with the reason
SOURCE '/provenance/workers/trigger-wakeup'
| fields @timestamp, trigger_id, result, reason_code, case_revision_before, case_revision_after
| filter event = 'trigger_evaluated'
| sort @timestamp desc
| limit 50
```

---

## 9. Analytics for product learning

### 9.1 The distinction this section rests on

Operational telemetry answers *is it working*. Product analytics answers *is it worth working*. They use the same events and require different aggregation windows, different cardinality tolerance, and different honesty about sample size.

The current build has one real user's worth of seeded data plus whatever the demo generates. **No number in this section is a finding.** Each row below names the question, the event or metric that will eventually answer it, and the query that produces it, so that the instrumentation exists on day one and the answers accumulate from the first real user rather than from a retrofit six weeks later. That is the whole value of writing this down now.

Per-user and per-counterparty questions are answered from **CloudWatch Logs**, not from metrics, because §4.1 forbids unbounded metric dimensions. Logs carry `user_hash` and `sender_domain`; that is exactly enough to segment without holding a user list.

### 9.2 The questions

| # | Product question | Signal | Query / derivation | What each answer would mean |
|---|---|---|---|---|
| 1 | **Is the wedge frequent enough to be a product?** `00_PRODUCT.md` R6 states the risk plainly: a handful of qualifying artifacts a year is excellent for correctness and poor for engagement. | `evidence.admitted.v1`, `memory.artifact_to_commit_ms{count}` | `stats count(*) by user_hash, bin(7d)` over `event = 'evidence_admitted'` | Under ~1 qualifying artifact per user per month, the consumer wedge needs a bundling mechanism (the "Move" context) or a different initial audience. Over ~4, the retention story writes itself. |
| 2 | **Does memory change the outcome, or does the model?** The entire premise of the architecture rests on this. | `POST /v1/judge-mode/counterfactual` results; `agent_runs.memory_mode` | `delta.conflicts_detected`, `delta.cases_reopened`, `delta.actions_recommended` per artifact, aggregated | A `delta.conflicts_detected` of zero on artifacts that genuinely contradict canonical state would mean retrieval, not memory architecture, is doing the work. |
| 3 | **Do users approve what the Advocate drafts?** | `provenance.action.intent_transition{to_status}` | `APPROVED / PROPOSED` over 30 days | Below ~0.5, drafting is not yet worth the human's attention and the product should propose fewer, better actions. |
| 4 | **When users reject, is it the draft or the memory?** This is the single most valuable question in the list. | `provenance.action.rejection_reason` | share of `WRONG_FACTS` among rejections | `WRONG_TONE` is a prompt problem. `WRONG_FACTS` is a **memory** problem and should route the user to `POST /v1/cases/{id}/corrections`, which is why the API already does that. A rising `WRONG_FACTS` share invalidates the system-of-record claim faster than any latency number. |
| 5 | **Is conflict detection precise enough to be trusted?** | `conflict.detected.v1`, `conflict.resolved.v1` with `resolution_reason_code` | conflicts resolved with a reason code indicating the conflict was spurious, over conflicts opened | Authority scores are hand-set engineering judgement, not calibration (`00_PRODUCT.md` R7). This ratio is the first empirical evidence about whether the bands are roughly right. |
| 6 | **Does prospective memory earn its complexity?** It is a whole subsystem — a DSL, a scheduler, an evaluator — for one behaviour. | `provenance.trigger.evaluated{result}` joined to `action.approved.v1` | fraction of `FIRED` evaluations that led within 7 days to an approved action | If fired triggers rarely lead to action, the deadline model is wrong, not the scheduler. If no-ops dominate and fires are rare, the predicates are too strict. |
| 7 | **Which counterparty format breaks extraction?** | `provenance.agent.extraction_schema_invalid` + `sender_domain` log field | `extraction-failures-by-counterparty.txt` (§8.4) | A single dominant domain means one parser fixture, not a model change. Broad failure means the prompt or the model moved. |
| 8 | **Where does identity resolution actually come from?** The architecture claims deterministic identity precedes vector similarity. | `provenance.retrieval.identity.exact_match_count` versus `identity.status{RESOLVED}` | ratio over 30 days | If exact identifiers resolve most bindings, the ANN index is a recall backstop rather than the primary mechanism — which is the honest architecture and should be described that way, not oversold. |
| 9 | **What does one artifact cost, end to end?** | `provenance.cost.artifact_usd_micros`, `provenance.model.invocations` | p50/p95 by `source_type` | Sets the ceiling on any consumer pricing and decides whether Tier R escalation stays conditional or becomes rare. |
| 10 | **How long until the record is right?** | `provenance.memory.artifact_to_commit_ms` | p50/p95 | The product promise is a citable record, not a fast one — but a user watching a spinner for 40 seconds does not experience it that way. |
| 11 | **How long does a human take to decide?** | `provenance.action.time_to_approval_ms` | p50/p95 by `action_type` | If approval routinely takes days, the notification path matters more than the drafting quality. |
| 12 | **Is the "Move" bundling actually what creates density?** | `contexts` membership on cases that received activity | `stats count_distinct(case_id) by context_id` over traces with a commit | Validates or kills the R6 mitigation. If activity is spread evenly across unbundled relationships, the context abstraction is decoration. |
| 13 | **Does retraction ever actually happen?** Retraction filtering is a correctness requirement built for a case we assume exists. | `evidence.retracted.v1` with `retraction_reason_code` | count by reason over 90 days | If users never retract, the filter is still correct and the *justification* changes from "users correct themselves" to "corrections arrive from counterparties". |
| 14 | **Is the human queue growing?** | `provenance.agent.pending_human_review{origin}`, `provenance.memory.conflicts_awaiting_human` | trend | A queue that only grows means the automation boundary is drawn in the wrong place. |
| 15 | **Which agent-safe views are load-bearing?** | `provenance.retrieval.mcp.tool_calls{view}` | share per view over 30 days | A view with near-zero calls is either dead surface to remove or a capability the graphs forgot to use. Both are worth knowing before it becomes API. |

### 9.3 The two aggregations worth building first

Everything above is a query. Two of them are worth a scheduled job that writes a small table, because they need a join across a time window that Log Insights makes awkward:

```sql
-- ops/analytics/weekly_wedge.sql — run weekly, output committed to ops/analytics/
-- Question 1 and question 12 in one statement. Read-only, pv_ops_reader.
SELECT
    date_trunc('week', kd.committed_at)                       AS week,
    count(*) FILTER (WHERE kd.decision IN ('ACCEPTED',
                                           'ACCEPTED_WITH_CONFLICT'))  AS commits,
    count(*) FILTER (WHERE kd.decision = 'ACCEPTED_WITH_CONFLICT')     AS commits_with_conflict,
    count(DISTINCT kd.case_id)                                AS cases_touched,
    count(DISTINCT c.context_id)                              AS contexts_touched,
    count(DISTINCT kd.user_id)                                AS users_active,
    round(count(*)::DECIMAL / NULLIF(count(DISTINCT kd.user_id), 0), 2)
                                                              AS commits_per_active_user
FROM kernel_decisions AS kd
JOIN cases AS c
  ON c.tenant_id = kd.tenant_id AND c.user_id = kd.user_id AND c.id = kd.case_id
WHERE kd.committed_at IS NOT NULL
  AND kd.committed_at >= now() - INTERVAL '90 days'
GROUP BY 1
ORDER BY 1 DESC;
```

```sql
-- ops/analytics/action_trust.sql — questions 3 and 4, which together decide
-- whether the Advocate is an asset or a liability.
SELECT
    ai.action_type,
    count(*)                                                          AS proposed,
    count(*) FILTER (WHERE ai.status IN ('APPROVED','EXECUTING','EXECUTED'))  AS approved,
    count(*) FILTER (WHERE ai.status = 'REJECTED')                    AS rejected,
    count(*) FILTER (WHERE ai.status = 'REJECTED'
                       AND ai.rejection_reason = 'WRONG_FACTS')       AS rejected_wrong_facts,
    count(*) FILTER (WHERE ai.status = 'CANCELLED_STALE')             AS cancelled_stale,
    round(count(*) FILTER (WHERE ai.status IN ('APPROVED','EXECUTING','EXECUTED'))::DECIMAL
          / NULLIF(count(*), 0), 3)                                   AS approval_rate,
    round(count(*) FILTER (WHERE ai.rejection_reason = 'WRONG_FACTS')::DECIMAL
          / NULLIF(count(*) FILTER (WHERE ai.status = 'REJECTED'), 0), 3)
                                                                      AS wrong_facts_share
FROM action_intents AS ai
WHERE ai.created_at >= now() - INTERVAL '90 days'
GROUP BY 1
ORDER BY proposed DESC;
```

Both run as `pv_ops_reader`, both are read-only, and neither is on any request path. They write CSV into `ops/analytics/` where the numbers are versioned alongside the code that produced them — which is the only way a metric definition and its history stay attached to each other.

### 9.4 What is deliberately not instrumented

- **No session recording, no click tracking, no third-party analytics SDK.** A product whose thesis is that institutions hold better records about people than people hold about institutions cannot ship a page that quietly builds a behavioural record about its user. This is a positioning decision as much as a privacy one, and it is cheap to keep.
- **No content-derived analytics.** Nothing counts words, topics, sentiment, or amounts extracted from artifact text into a metric. Artifact content lives in S3 and in `evidence_items` and is read by the user's own retrieval path; it is not a corpus we mine.
- **No cross-tenant aggregate on any dashboard the user can reach.** The gauges in §4.2 are global counts across the demo deployment, which has one real tenant plus the two isolation tenants. If a second real tenant is ever added, those gauges gain a `tenant_hash` dimension and move to the ops dashboard only.

---

## 10. Where this contract is verified

Every claim in this document has a gate that fails if the claim is false. This table is the map, and it is the answer to standing question Q3 ("which invariant is currently unproven?") for the observability surface.

| Claim | Gate | Assertion |
|---|---|---|
| Redaction exists and is not vacuous | `G-8` | `pytest services/control_plane/tests/unit/test_redaction.py` — including the positive control and `PV_SABOTAGE=provenance_telemetry.redaction.scan_text` going red |
| Every logged key is declared | `G-8` | `python -m tools.log_schema_lint services packages agents workers` → `undeclared keys: 0` |
| Spans exist in CloudWatch with the canonical names | `G-13` | `G13.5` — the Log Insights query returns `artifact.register`, `agent.interpreter.run`, `retrieval.vector`, `memory.kernel.transaction`, `outbox.dispatch`, `action.approve`, `action.execute` |
| Alarms exist and are healthy | `G-13` | `G13.7` — every `provenance-` alarm in `OK`, with `provenance-outbox-pending-age`, `provenance-dlq-depth`, `provenance-kernel-retry-rate`, `provenance-action-abort-rate` present |
| The trace is not an animation | `G-12` | `G12.2` — DOM `[data-node-id]` set ⊆ API payload node set, `|DOM| ≥ 8` |
| No hard-coded ids in the frontend | `G-12` | `G12.3` — zero UUID literals in `apps/web/src` |
| Changing the truth moves the UI | `G-12` | `G12.4` — commit a correction, revision text moves 13 → 14 |
| No chain-of-thought reaches a client | `G-5`, `G-12` | `G5.4` (jq over State Proof paths), `G12.6` (Playwright over every response body) |
| MCP calls in the trace come from rows | `G-11` | `G11.4` — every `sql_role == pv_agent_reader`, every `access_mode == READ_ONLY`, backed by the `agent_runs` row |
| A denied MCP call is rendered, not swallowed | `G-11` | `G11.5` |
| Rendered view names equal database view names | `G-11` | `G11.6` — `diff` of `information_schema.views` against `GET /v1/judge-mode/agent-views` |
| `retry_count` is real | `G-3`, `G-4` | `G3.2` prints `retry_count=2` from two genuinely overlapping transactions, not a monkeypatch; `G4.7` requires at least one run with `retry_count >= 1` |
| Retrieval metrics are not vacuous | `G-6` | `G6.3(d)` — the positive control: with the retraction predicate removed, the retracted fixture appears in the top 20 |
| The trace corresponds to rows | new, at `G-12` | `python -m tools.trace_verify --assert-live` → `VERDICT: PASS`, added to the `G-12` battery as `G12.8` |
| Cost is never fabricated | `G-13` | `python -c "from provenance_telemetry.pricing import load_snapshot; load_snapshot()"` exits 0, and `provenance.cost.pricing_unavailable` is zero |

Two additions this document makes to existing batteries, both small and both worth the minute they cost:

```bash
# G12.8 — the rendered trace corresponds to real, live rows.
TRACE=$(...)   # from the G12.1 hero-flow run
python -m tools.trace_verify --trace-id "$TRACE" --api "$PV_API" \
       --token "$PV_TOKEN" --assert-live
#   → "VERDICT: PASS" with 0 fabricated, 0 suppressed, 0 mismatched, 0 seeded

# G13.10 — every span name in the map appears at least once in the deployed stack,
#          and no span name outside the map appears at all.
aws logs start-query --log-group-name /provenance/control-plane \
  --query-string 'fields span_name | filter event="span" | stats count(*) by span_name' \
  --start-time $(($(date +%s) - 3600)) --end-time $(date +%s)
python -m tools.span_map_check --expected docs/quality/21_OBSERVABILITY_ANALYTICS.md
#   → "27 span names declared, 27 observed, 0 undeclared"
```

`G13.10` catches the failure mode nobody plans for: a span quietly renamed during a refactor, which breaks `G13.5` and every saved query at once, silently, weeks before anyone looks.

---

## 11. Risks and open questions

**R1 — Resolved.** All four columns now exist in `specs/10_DATABASE_DDL.md`: `agent_runs.tool_calls`, `agent_runs.model_calls`, `agent_runs.capability_status` (§11.3) and `idempotency_records.trace_id` (§11.4, with `idx_idempotency_trace`). They are inline in migration `0008_events_infrastructure`, where both tables are already created. `G11.4` was corrected to query the `tool_calls` column. The fixed naming rule: **column** `agent_runs.tool_calls`, **JSON field** `mcp_tool_calls[]`.

**R2 — `rows_written` is a lower bound because `claims`, `conflicts`, `commitments`, and `fulfillments` carry no `kernel_decision_id`.** §6.5 handles this by naming the field `rows_written_attributable` and counting only what the database can prove. That is honest and it is also slightly weaker than the story the demo wants to tell — "one transaction wrote seven rows" becomes "one transaction wrote at least six rows we can attribute". **Recommended fix, additive and cheap:** add `kernel_decision_id UUID NULL` to those four tables, written by the Kernel in the same statement that already writes them, with an index on it. This costs one column and makes attribution exact. It belongs in the same DDL change as R1. Until then, the number rendered is a lower bound and the UI must not label it "rows written" without qualification.

**R3 — `agent_runs.tool_calls` and `model_calls` are self-reported by the agent runtime.** Already conceded in `15_API_SPEC.md` §17.13 and restated in §7.4. A compromised or buggy agent can under-report, so the MCP panel is an accurate observability artifact and not a tamper-proof audit record. **Decided posture:** say so, in Judge Mode, in one line of UI text next to the panel, rather than letting a reviewer discover it. The authoritative statement about what the agent could access remains the SQL grant on `pv_agent_reader`, demonstrated by refusal at `G11.2`, which is a stronger guarantee than any log. Server-side proof would need CockroachDB audit logging or MCP server-side logging; neither is in v1 scope and pretending otherwise would be the exact kind of overclaim this document exists to prevent.

**R4 — Binding the OpenTelemetry trace id to the UUIDv7 rules out AWS X-Ray.** §1.3. UUIDv7's timestamp prefix is milliseconds-since-epoch in the high 48 bits, which is not X-Ray's epoch-seconds prefix, so X-Ray would reject the id. **Decision:** accept it. Spans go to CloudWatch Logs as structured records, which is what `G13.5` actually asserts, plus OTLP where available. The mandatory `provenance.trace_id` span attribute exists so that reverting to the default id generator is a one-line change that costs nothing but a join. **Residual risk: low.** The cost is that there is no flame-graph UI in v1, which at eighteen spans per flow is not the bottleneck to understanding anything.

**R5 — Metrics arrive asynchronously through EMF log extraction, so a dashboard can lag a live demo by tens of seconds.** This is the price of having no collector to crash. On stage, the presenter reads the product UI, not the dashboard; the dashboard is for the Q&A and for the ops story. **Mitigation:** the demo dashboard's singleValue widgets use a 60-second period and the gauge sweep runs every 60 seconds, so worst-case staleness is roughly two minutes. **If that proves too slow during rehearsal**, the fix is to shorten the gauge interval to 15 seconds, not to add a collector.

**R6 — The gauge sweep is a `SELECT count(*)` fleet against a live cluster.** Fourteen scalars, one statement, once a minute, unindexed on most predicates. At 18,000 evidence rows and low tens of cases this is free. At a hundred thousand cases it is a recurring full scan. **Decision:** ship it, because measuring the wrong thing at demo scale in order to be ready for a scale we do not have would be premature optimisation of the exact kind `15_API_SPEC.md` §17.5 rejects for the timeline query. **The honest fix when it matters** is Kernel-maintained counters in the same transaction as the state change, which is a second copy of canonical data and should not be added without a profile in hand.

**R7 — `prospective_triggers` is mutable, so a `TRIGGER_EVALUATION` node can drift from the event it renders.** §6.3 joins the trigger row for display fields, and `last_result`/`last_reason_code` reflect the *most recent* evaluation. A trace rendered after a re-arm and a second evaluation would show the later result beside the earlier event. **Mitigation:** the event payload's `field_values` is immutable and is rendered as primary; the joined columns are secondary and labelled "current". **Better fix, deferred:** carry `result`, `reason_code`, and `evaluation_version` inside the `trigger.fired.v1` / `trigger.noop.v1` payloads, which are append-only, and drop the join entirely. That is an additive payload change and is permitted within `.v1` (`15_API_SPEC.md` §10.2 rule 3). It should be done before Phase 10 signs.

**R8 — The eval metrics in §4.4 will be read as calibrated measurements and they are not.** `13_RETRIEVAL_SPEC.md` §15.1 is explicit: at n = 40 with a rate near 0.90 the 95% confidence interval is roughly ±9 points, so any difference under 10 points is noise. Putting Recall@1 on a CloudWatch graph dimensioned by `git_sha` makes it look like a tuned production metric. **Decided posture:** the `Provenance/Eval` widgets carry the sample size and the interval in the widget title, and no tuning decision is justified by a movement inside the interval. `16_TRIGGER_DSL.md`-style tuning of `vector_search_beam_size` stays frozen at the CockroachDB default of 32 until the harness is large enough to support a decision, per `13_RETRIEVAL_SPEC.md` §16.2.

**R9 — The redaction allow-list will rot.** It is the correct design and it has a well-known failure mode: a developer adds a field, the lint fails, and the fastest way to make the lint pass is to add the key to `ALLOWED_KEYS` without thinking about what can end up in it. **Mitigation:** `error_detail`, `filter_summary`, and `message` are the only free-text keys and they are scanned at the value level, so the damage from a careless addition of a *structured* key is bounded. **Standing rule for reviewers:** an addition to `ALLOWED_KEYS` in a diff requires the reviewer to name, in the review, what the worst possible value of that key is. If the answer is "arbitrary text from an artifact", the key belongs on the deny-list instead.

**R10 — Value-level scanning has false positives, and one of them is annoying.** The account-number mask fires on any string containing eight or more digits with optional separators. A CockroachDB job id, a long numeric correlation id from a provider, or an epoch in milliseconds rendered as a string will be masked to `••••1234`. **Assessment:** acceptable. The failure is a partially unreadable log line; the alternative failure is a leaked account number, and the asymmetry is not close. **Mitigation:** integers stay integers — the mask applies only to strings — so every numeric field in §5.2's measurement group is untouched, which covers the cases that actually matter operationally.

**R11 — Alarm thresholds are engineering judgement at demo scale, not SLOs derived from measurement.** Nothing in §8.3 was derived from observed production behaviour, because there is none. Each threshold carries its reasoning in the `reason:` field precisely so that the first person to see a false page can evaluate the reasoning rather than guess at intent. **Expect to move:** `provenance-kernel-retry-rate` (0.5 retries per commit is a guess about contention on one hot case), `provenance-cost-per-artifact` (USD 0.25 is a round number, not a budget), and `provenance-extraction-schema-invalid-rate` (10% assumes the repair budget is absorbing occasional failures). **Do not move:** the six P1 correctness alarms whose threshold is zero. Those are not tuned; they are invariants, and a non-zero value means something the architecture says cannot happen has happened.

**R12 — This document adds two gate assertions (`G12.8`, `G13.10`) and one new tool (`tools/trace_verify.py`) to a schedule that is already tight.** `23_PHASE_GATES.md` §5 marks phase 12 and phase 13 as not cuttable, and this adds work to both. **Honest assessment:** `G12.8` and `trace_verify` are worth the cost, because Judge Mode credibility is the mechanism by which the memory-design and product-readiness claims are actually earned, and an unverifiable trace earns neither. `G13.10` and `tools/span_map_check` are the cuttable item in this document; if time runs out, drop `G13.10` and rely on `G13.5`'s seven named spans, and record the drop as carried debt rather than deleting the assertion from this table.






