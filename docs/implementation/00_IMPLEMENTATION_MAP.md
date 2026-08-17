# Provenance — Implementation Map

Status: planning-complete implementation baseline v1.1  
Implementation status: not started  
Purpose: remove architectural ambiguity before code generation  
Audience: coding agents, backend/frontend engineers, judges reviewing technical depth

## 1. Frozen decisions

These decisions are considered stable unless a concrete blocker appears.

| Concern | Decision |
|---|---|
| Product | Provenance: personal system of record for user–institution relationships |
| Hero story | “The Move That Never Really Ended” — ISP post-cancellation bill reopens an old relationship, while other move obligations remain unresolved |
| Agent framework | LangGraph |
| Agent hosting | Amazon Bedrock AgentCore Runtime |
| Canonical memory | CockroachDB Cloud |
| Agent access to DB | CockroachDB MCP for governed reads; no direct canonical writes from reasoning agents |
| Backend | Python + FastAPI |
| Frontend | Next.js + TypeScript |
| Authentication | Amazon Cognito user pool; real multi-user architecture + pre-seeded judge account |
| Raw artifacts | Amazon S3 |
| Email ingestion | Amazon SES inbound → S3 → ingestion worker; upload `.eml`/PDF/image remains fallback |
| Document extraction | MIME/parser first; Textract for image/PDF/form/table workloads |
| Event/wakeup | EventBridge + EventBridge Scheduler; SQS DLQ |
| External action | Draft → human approval → deterministic executor; demo outbound email via SES/safe sink |
| Reasoning model | `anthropic.claude-opus-5` for Tier R semantic resolution and advocacy |
| Bulk extraction model | `anthropic.claude-haiku-4-5` for Tier E extraction/classification |
| Fallback policy | One Tier E repair and one Opus 5 low-effort invocation fallback; Tier R failure becomes `PENDING_HUMAN_REVIEW` |
| Embeddings | Titan Text Embeddings V2, 1024 dimensions, one frozen embedding version |
| Database isolation | CockroachDB `SERIALIZABLE`; kernel retries SQLSTATE `40001` |
| Deployment DB | CockroachDB Cloud Basic/trial first; multi-region if easy under credits |
| AWS primary region | `us-east-1` default; change only if required model/SES/AgentCore availability dictates |

## 2. The architecture in one sentence

> Untrusted evidence is interpreted by LangGraph agents into typed proposals; a deterministic Memory Kernel validates those proposals and atomically commits versioned beliefs, obligations, state transitions, and outbox records in CockroachDB; asynchronous AWS services then react to committed state under explicit human authorization.

## 3. Four invariants that every implementation must preserve

1. **Evidence is append-only.** Original source/evidence is never silently rewritten.
2. **Beliefs are revisable.** New evidence creates new belief versions and preserves old lineage.
3. **State is transactional.** A case/commitment must never be left in an impossible partial state.
4. **Actions are permissioned.** No agent scratchpad or uncommitted proposal may cause an external side effect.

If an implementation decision breaks any invariant, reject the decision.

## 4. Logical architecture vs deployment architecture

Provenance has many logical modules, but the hackathon build should **not** deploy one microservice per box.

### 4.1 Logical modules

- API/BFF
- authentication/tenant context
- ingestion coordinator
- retrieval engine
- Memory Kernel
- State Proof builder
- action policy + action executor
- Interpreter LangGraph
- Advocate LangGraph
- optional Resolver node/subgraph
- event dispatcher
- trigger evaluator
- observability/evaluation

### 4.2 Hackathon deployment units

Use only these deployment units initially:

1. **`web`** — Next.js UI on Amplify Hosting.
2. **`control-plane`** — one FastAPI container on AWS App Runner containing API, retrieval, Memory Kernel, State Proof, action-policy logic, and internal tool endpoints.
3. **`agent-runtime`** — LangGraph agent package on AgentCore Runtime.
4. **`workers`** — small Lambda functions for SES ingestion notification, trigger wakeups, outbox sweeping/dispatch, and optional document-analysis completion callbacks.
5. **CockroachDB Cloud** — canonical memory plane.
6. **S3/SES/EventBridge/SQS/Cognito/CloudWatch** — managed supporting services.

This is intentionally a **modular monolith + managed async workers**, not a microservice zoo. It minimizes deployment/network failure modes while preserving clear module boundaries in code.

## 5. Repository layout

Recommended monorepo:

```text
provenance/
├── apps/
│   └── web/                         # Next.js + TypeScript
│
├── services/
│   └── control_plane/               # FastAPI container
│       ├── app/
│       │   ├── api/                 # public REST endpoints
│       │   ├── auth/                # Cognito JWT -> Principal
│       │   ├── ingestion/           # artifact registration
│       │   ├── retrieval/           # structured + vector retrieval
│       │   ├── memory_kernel/       # only canonical write path
│       │   ├── state_proof/         # deterministic explanation read model
│       │   ├── actions/             # intents/approval/revalidation/execution
│       │   ├── events/              # outbox domain event helpers
│       │   └── observability/       # trace/correlation utilities
│       └── tests/
│
├── agents/
│   └── runtime/
│       ├── graphs/
│       │   ├── ingestion_graph.py
│       │   └── advocate_graph.py
│       ├── nodes/
│       ├── prompts/
│       ├── schemas/
│       ├── tools/
│       ├── model_router/
│       └── tests/
│
├── workers/
│   ├── ses_ingest/
│   ├── textract_complete/
│   ├── outbox_dispatch/
│   └── trigger_wakeup/
│
├── packages/
│   └── python/
│       ├── provenance_contracts/     # Pydantic schemas shared by backend/agents/workers
│       ├── provenance_domain/        # enums, state machines, invariant functions
│       ├── provenance_db/            # DB pool, repositories, transaction retry
│       └── provenance_telemetry/     # trace IDs / OTEL helpers
│
├── db/
│   ├── migrations/                   # Alembic migrations
│   ├── seeds/
│   └── demo/
│
├── infra/
│   ├── cdk/                          # Cognito, S3, SES, EventBridge, SQS, Lambda, App Runner/ECR
│   └── agentcore/                    # AgentCore CLI/SDK config
│
├── evals/
│   ├── datasets/
│   ├── memory/
│   ├── retrieval/
│   ├── extraction/
│   └── adversarial/
│
├── demo_data/
│   └── the_move/
│
└── docs/
    └── implementation/
```

## 6. Shared contracts are mandatory

Coding agents must not re-declare JSON shapes independently in multiple services.

Create one Python package, `provenance_contracts`, containing versioned Pydantic models for:

- `Principal`
- `ArtifactRegistered`
- `ExtractedEvidenceCandidate`
- `MemoryProposal`
- `ResolutionAssessment`
- `KernelCommitResult`
- `DomainEvent`
- `RetrievalContext`
- `StateProof`
- `ActionIntentView`
- `TriggerWakeup`

Every boundary payload includes `schema_version`.

## 7. IDs and timestamps

### IDs

Use UUIDv7 if the chosen library is stable; otherwise UUIDv4. Do not use incremental IDs across tenant-owned data.

All externally visible objects use opaque UUIDs.

### Time

- Store all timestamps as `TIMESTAMPTZ` in UTC.
- UI renders in user timezone.
- Bitemporal intervals use half-open semantics: `[valid_from, valid_to)`.
- `valid_to = NULL` means open-ended.
- `recorded_at` is assigned by backend/database at commit time, not trusted from model output.

## 8. Money and quantities

Never use floating point for obligations.

- amount: `DECIMAL(20,4)`
- currency: ISO-style 3-character code, e.g. `USD`
- Kernel refuses arithmetic across different currencies unless an explicit conversion event exists.
- Derived outstanding amount is deterministic: `committed_amount - admitted_fulfillment_amount`.

## 9. Model roles

### Tier E — extraction

Allowed tasks:

- classify artifact
- extract actor/counterparty hints
- extract dates/amounts/reference numbers
- identify candidate claims/commitments
- identify quoted evidence spans

Frozen: `anthropic.claude-haiku-4-5`.

### Tier R — reasoning

Allowed tasks:

- ambiguous identity resolution
- temporal interpretation
- contradiction characterization
- determine whether evidence semantically supports/qualifies/opposes a proposition
- generate advocacy draft from State Proof

Frozen: `anthropic.claude-opus-5`.

### Rule

No model receives database write credentials. No model emits SQL as a state-changing contract.

## 10. Build dependency graph

The implementation has a strict dependency structure:

```text
contracts/domain enums
       |
       +--> database schema/repositories
       |          |
       |          +--> Memory Kernel
       |          |       |
       |          |       +--> public API / State Proof
       |          |       +--> trigger evaluator
       |          |       +--> action approval/executor
       |          |
       |          +--> retrieval engine
       |                   |
       +-------------------+--> LangGraph agents
                                  |
                                  +--> ingestion end-to-end
                                  +--> advocate flow

AWS event plumbing can be connected after local end-to-end behavior works.
```

A coding agent should not start from the UI or prompt design before contracts + schema + Kernel invariants exist.

## 11. Definition of the first coherent vertical slice

The first system slice is complete only when this works end-to-end:

1. seed user + old ISP relationship + resolved cancellation case;
2. seed May 15 cancellation confirmation evidence;
3. ingest June invoice artifact;
4. Interpreter extracts account, billing period, amount, new counterparty claim;
5. retrieval resolves the old ISP case;
6. Kernel detects contradiction with service-terminated belief;
7. one serializable transaction:
   - admits new claim/evidence;
   - creates conflict;
   - reopens case;
   - increments case revision;
   - writes state transition;
   - writes outbox event;
8. Advocate builds action draft from State Proof;
9. user approves draft;
10. executor revalidates case revision and sends to demo sink;
11. execution outcome is stored and visible in the timeline;
12. Judge Mode shows one trace spanning the complete lifecycle.

Everything beyond this slice is enhancement.

## 12. Implementation rules for coding agents

- Prefer explicit domain functions over prompt-based logic for arithmetic/status transitions.
- Prefer one database transaction over choreography when objects share a consistency invariant.
- Do not add a cache until profiling proves a need.
- Do not introduce DynamoDB as a second canonical memory store.
- Do not use AgentCore Memory for product memory.
- Do not use LangGraph Store as product memory.
- LangGraph checkpointers are allowed only for workflow durability/HITL recovery.
- Do not expose arbitrary SQL tools to agents.
- Do not put raw document contents into logs.
- Do not silently resolve two high-authority conflicting sources.
- Every action approval binds to a specific case revision and exact draft hash.
- Every state-changing request has an idempotency key.

## 13. Document map

Read in this order:

1. `00_IMPLEMENTATION_MAP.md` — frozen decisions and repo structure.
2. `01_SYSTEM_ARCHITECTURE_DETAILED.md` — runtime topology and flow ownership.
3. `02_DATA_MEMORY_TRANSACTIONS.md` — schema, invariants, indexes, transaction boundaries.
4. `03_AGENTS_LANGGRAPH_CONTRACTS.md` — graph state, nodes, tools, model routing.
5. `04_API_EVENTS_SECURITY.md` — public/internal APIs, events, auth, idempotency, MCP.
6. `05_RELIABILITY_EVAL_DEMO.md` — failure handling, observability, evaluation, judge scenario.

## 14. Official references

- CockroachDB transaction retry errors: https://www.cockroachlabs.com/docs/stable/transaction-retry-error-reference
- CockroachDB vector indexes: https://www.cockroachlabs.com/docs/stable/vector-indexes
- CockroachDB MCP server: https://www.cockroachlabs.com/docs/v26.2/cockroachdb-mcp-server
- AgentCore Runtime: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html
- AgentCore Runtime deployment: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/create-deploy-agent.html
- Amazon SES email receiving: https://docs.aws.amazon.com/ses/latest/dg/receiving-email.html
- EventBridge Scheduler: https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html
- Titan Text Embeddings V2: https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html
