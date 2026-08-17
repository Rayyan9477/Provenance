# Provenance — Coding Agent Handoff

Status: planning-complete execution contract v1.1
Implementation status: not started

This file is designed to be pasted into or referenced by a coding agent. It intentionally translates the architecture into bounded implementation work packages without changing the architecture.

## 1. Mission

Build Provenance as a production-minded hackathon system where:

- CockroachDB is the only canonical memory system;
- LangGraph agents interpret evidence but cannot directly commit truth;
- a deterministic Memory Kernel commits versioned beliefs/state under CockroachDB serializable transactions;
- user-approved actions are revalidated against current state before execution;
- the hero demo “The Move That Never Really Ended” runs end-to-end.

## 2. Do not redesign these decisions

Do not replace:

- LangGraph;
- AgentCore Runtime;
- FastAPI/Python backend;
- Next.js frontend;
- Cognito;
- CockroachDB canonical memory/vector store;
- S3 raw artifact store;
- EventBridge/Scheduler prospective memory;
- human-approved action execution.

Do not introduce a second source of truth.

## 3. Work package A — contracts/domain package

Create `packages/python/provenance_contracts` and `provenance_domain`.

Required types:

- `Principal`, `InternalPrincipal`
- all domain status enums
- `ArtifactMetadata`
- `ContentBlock`
- `ExtractionResult`
- `IdentityCandidate`
- `RetrievalContext`
- `ResolutionAssessment`
- `MemoryProposal`
- `KernelCommitResult`
- `StateProof`
- `DomainEvent`
- `ActionIntentView`
- `TriggerWakeup`

Acceptance:

- Pydantic serialization round-trips;
- schema versions present;
- invalid money/date/confidence rejected;
- no contract contains raw SQL.

## 4. Work package B — database/migrations

Implement the tables/indexes in `02_DATA_MEMORY_TRANSACTIONS.md` using Alembic.

Prioritize tables in this order:

1. tenants/users/counterparties/relationships/cases;
2. source_artifacts/evidence_items;
3. claims/beliefs/belief_versions/belief_support;
4. conflicts/commitments/fulfillments/state_transitions;
5. memory_proposals/kernel_decisions;
6. prospective_triggers;
7. action_intents/action_executions;
8. outbox_events/processed_events/agent_runs/idempotency_records.

Acceptance:

- fresh database migrates from zero;
- seed script creates hero user/cases;
- vector column/index exists;
- tenant/user indexes exist;
- schema constraints catch impossible monetary state where practical.

## 5. Work package C — database runtime

Create `provenance_db`:

- CockroachDB connection pool;
- transaction wrapper;
- retry on SQLSTATE `40001` with bounded jitter/backoff;
- repositories separated by domain;
- no network/model calls inside transaction callback.

Acceptance:

- forced/injected retry test proves transaction callback can rerun;
- retry count is observable;
- rollback leaves no partial writes.

## 6. Work package D — Memory Kernel

Implement the decision pipeline exactly from `02_DATA_MEMORY_TRANSACTIONS.md`.

Start with these proposal capabilities only:

- new counterparty claim;
- new commitment;
- fulfillment;
- contradiction with existing belief;
- case reopen;
- prospective trigger arm/disarm.

Do not attempt a universal ontology in v1.

Acceptance:

- Kernel never accepts foreign evidence;
- every canonical belief has provenance;
- ISP hero contradiction reopens case atomically;
- case revision increments once per canonical commit;
- outbox written in same transaction;
- duplicate proposal can resolve to NOOP.

## 7. Work package E — deterministic read models

Implement:

- dashboard read model;
- case projection;
- timeline;
- State Proof;
- conflict view;
- Memory Trace storage/query.

Acceptance:

- all work without model calls;
- State Proof returns source IDs + belief lineage;
- hero case explanation is derivable purely from DB.

## 8. Work package F — ingestion

### Upload first

Implement pre-signed S3 upload + `.eml` parsing before SES DNS.

### Then SES

Add SES receipt rule -> S3 -> Lambda.

Normalized parser must produce `ContentBlock`s and preserve quoted-history tagging.

Acceptance:

- exact same `.eml` produces same artifact hash/content blocks via upload and SES path;
- duplicate email does not create duplicate business state;
- unsupported attachments fail safely.

## 9. Work package G — embeddings/retrieval

Implement:

- Titan v2 embeddings, 1024 dimensions;
- embedding cache keyed by normalized-text hash + embedding version;
- user-prefixed Cockroach vector index;
- exact identifier candidate lookup;
- vector top-K;
- relational/temporal rerank;
- bounded `RetrievalContext`.

Acceptance:

- no cross-user vector results;
- hero invoice maps to correct old ISP case despite decoy evidence;
- retrieval metrics runnable from eval dataset.

## 10. Work package H — LangGraph ingestion graph

Implement exact nodes from `03_AGENTS_LANGGRAPH_CONTRACTS.md`.

Initially use fixtures to test graph topology, then enable Bedrock calls.

Acceptance:

- extraction schema validated;
- one repair max;
- resolver called only when route condition says so;
- graph output is typed MemoryProposal/Kernel result;
- graph cannot mutate DB except proposal endpoint;
- graph version/model/prompt version recorded.

## 11. Work package I — Advocate graph + approval

Implement:

- State Proof input;
- attention classification;
- grounded draft;
- support-ID validation;
- ActionIntent creation;
- user approve/reject endpoints;
- stale revision detection.

Acceptance:

- unsupported factual draft cannot silently pass;
- edit changes draft hash;
- approval freezes exact hash;
- post-approval case mutation invalidates execution.

## 12. Work package J — action executor

Hackathon implementation:

- SES/safe sink email adapter;
- deterministic recipient allowlist;
- execution idempotency;
- provider correlation ID;
- outcome recorded.

Acceptance:

- retry cannot send duplicate message under same idempotency key in test/simulator;
- stale action never sends;
- execution outcome appears in case timeline.

## 13. Work package K — events and prospective memory

Implement:

- transactional outbox;
- dispatcher;
- EventBridge rules;
- EventBridge Scheduler one-time trigger;
- trigger evaluator;
- SQS DLQ.

Acceptance:

- duplicate event -> consumer NOOP;
- overdue landlord deposit trigger creates attention event;
- trigger after fulfillment -> NOOP;
- failed outbox can be replayed.

## 14. Work package L — authentication/security

Implement:

- Cognito human app client;
- Cognito M2M app clients + custom scopes;
- FastAPI JWT verification middleware/dependencies;
- `cognito_sub -> Principal` mapping;
- `agent_run_id` binding for service requests;
- SQL roles logical separation;
- MCP read-only role.

Acceptance:

- cross-user object access returns 404/403 according to policy;
- agent workload cannot submit arbitrary user UUID without bound run;
- prompt-injected artifact cannot call action/commit capability.

## 15. Work package M — frontend

Required screens only:

1. Login.
2. Dashboard/context “The Move”.
3. Case detail/timeline.
4. State Proof.
5. Action approval.
6. Judge Mode Memory Trace.
7. Upload/forward instructions.

Do not build chat as the main UX.

Acceptance:

- hero flow requires no developer console;
- product value visible before technical view;
- technical view exposes real IDs/revisions/events, not hard-coded animation.

## 16. Work package N — evals/tests

Implement fixture and live-model modes.

Test layers:

- unit: domain/state machines;
- DB integration: Kernel/transactions;
- agent contract: fixture structured outputs;
- live model eval: extraction/reasoning;
- retrieval eval;
- end-to-end hero flow;
- adversarial prompt injection;
- concurrency/idempotency.

Acceptance metrics are in `03_AGENTS_LANGGRAPH_CONTRACTS.md` and `05_RELIABILITY_EVAL_DEMO.md`.

## 17. Environment configuration

Use explicit env/config names; no secrets committed.

Suggested:

```text
APP_ENV
APP_BASE_URL
AWS_REGION
COGNITO_USER_POOL_ID
COGNITO_WEB_CLIENT_ID
COGNITO_ISSUER
COGNITO_AGENT_CLIENT_ID
COGNITO_AGENT_CLIENT_SECRET_ARN
COGNITO_WORKER_CLIENT_ID
COGNITO_WORKER_CLIENT_SECRET_ARN
COCKROACH_DATABASE_URL
S3_ARTIFACT_BUCKET
SES_INGEST_DOMAIN
SES_FROM_ADDRESS
EVENTBRIDGE_BUS_NAME
EVENTBRIDGE_SCHEDULER_GROUP
SQS_DLQ_URL
BEDROCK_REASONING_MODEL_ID
BEDROCK_EXTRACTION_MODEL_ID
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0
EMBEDDING_DIMENSIONS=1024
AGENTCORE_RUNTIME_ARN
MCP_SERVER_URL
MCP_AUTH_SECRET_ARN
OTEL_SERVICE_NAME
```

Create a central typed settings object. Do not call `os.environ` throughout domain code.

## 18. Local development mode

Local mode must preserve semantics while substituting infrastructure:

- local FastAPI;
- real CockroachDB Cloud dev cluster preferred;
- local filesystem/minio optional only for artifacts, but S3 integration should be tested before demo;
- stored model fixtures can replace Bedrock for deterministic tests;
- EventBridge/Scheduler can be simulated by direct worker invocation in tests;
- never replace Memory Kernel with mocks in end-to-end correctness tests.

## 19. Pull-request guardrails

Reject code that:

- writes canonical tables from agent package;
- makes a model call inside DB transaction;
- bypasses tenant scoping;
- stores float money;
- creates external action from uncommitted proposal;
- uses scheduler event as truth without predicate reevaluation;
- relies on exactly-once delivery;
- logs raw sensitive artifacts;
- uses LangGraph memory/store as product state;
- creates an alternate canonical copy in another database.

## 20. Handoff completion checklist

A coding agent can claim the build is architecturally compliant only if:

- [ ] contracts are shared and versioned;
- [ ] DB schema/migrations match memory model;
- [ ] Memory Kernel is sole canonical writer;
- [ ] 40001 retry wrapper tested;
- [ ] State Proof deterministic;
- [ ] vector search user-scoped;
- [ ] Interpreter produces typed proposals;
- [ ] Resolver is conditional;
- [ ] Advocate uses committed State Proof;
- [ ] approval bound to revision + draft hash;
- [ ] outbox + idempotent consumer works;
- [ ] prospective trigger reevaluates current state;
- [ ] Cognito user + M2M scopes work;
- [ ] MCP is read-only/least privilege;
- [ ] hero demo passes;
- [ ] concurrency test passes;
- [ ] adversarial evals pass;
- [ ] Judge Mode uses real traces.
