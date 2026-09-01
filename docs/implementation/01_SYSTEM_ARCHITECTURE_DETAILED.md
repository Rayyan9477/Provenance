# Provenance — Detailed System Architecture

Status: planning-complete baseline v1.1
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared

## 1. System context

Provenance maintains a user's side of relationships with counterparties. The outside world provides **untrusted evidence**; the application converts that evidence into **versioned canonical state** and then uses committed state to recommend or execute approved actions.

The system has five planes:

```text
                 ┌──────────────────────────────────────┐
                 │  1. EXPERIENCE PLANE                │
                 │  Next.js + Cognito                   │
                 │  dashboard / cases / proof / action │
                 └────────────────┬─────────────────────┘
                                  │ HTTPS + JWT
                                  ▼
                 ┌──────────────────────────────────────┐
                 │  2. CONTROL PLANE                   │
                 │  FastAPI on App Runner              │
                 │  auth / retrieval / Memory Kernel   │
                 │  proof / action policy              │
                 └─────────────┬───────────────┬────────┘
                               │               │
                  typed tools  │               │ SQL/TLS
                               ▼               ▼
                 ┌──────────────────────┐   ╔════════════════════════╗
                 │ 3. COGNITION PLANE  │   ║ 4. MEMORY PLANE        ║
                 │ AgentCore Runtime    │   ║ CockroachDB Cloud      ║
                 │ LangGraph            │   ║ evidence/beliefs/state ║
                 │ Interpreter/Advocate │   ║ conflicts/outbox       ║
                 └──────────┬───────────┘   ╚═══════════╤════════════╝
                            │                           │
                            └──────────┬────────────────┘
                                       ▼
                         ┌─────────────────────────────┐
                         │ 5. REACTION / EVIDENCE     │
                         │ SES/S3/Textract/EventBridge│
                         │ Scheduler/Lambda/SQS       │
                         └─────────────────────────────┘
```

## 2. Trust boundaries

### Boundary A — browser to application

Browser is untrusted.

Requirements:

- Cognito JWT validation server-side.
- Never trust `user_id` or `tenant_id` supplied by body/query string.
- Derive principal from token.
- Object authorization check after lookup.

### Boundary B — external artifact to semantic evidence

Email/PDF/image content is hostile input.

Requirements:

- artifacts cannot carry instructions that grant capabilities;
- parser output is data, never system instructions;
- Interpreter can only produce typed proposals;
- Interpreter has no canonical write tool and no outbound email credential.

### Boundary C — agent to Memory Kernel

Agent reasoning is nondeterministic.

Requirements:

- Pydantic/JSON Schema validation;
- all referenced evidence IDs must belong to principal/tenant;
- model-supplied confidence is advisory;
- Kernel recomputes deterministic derivations;
- proposal is not canonical until transaction commits.

### Boundary D — committed state to external side effect

An action may have real-world consequences.

Requirements:

- action derived only from committed state;
- human approval required for all external sends;
- approval bound to exact draft hash + case revision;
- executor revalidates immediately before send;
- idempotency key prevents duplicate external effects.

## 3. Deployment topology

### 3.1 Primary AWS region

Default: `us-east-1`.

Reasoning:

- broad Bedrock/AgentCore availability;
- broad AWS service coverage;
- simple single-region application topology.

Before deployment, verify the selected Bedrock model IDs and SES receiving availability in the region. If inbound SES requires another supported region, either place only SES/S3 ingestion there or use `.eml` upload for the live demo; do not move the entire architecture unnecessarily.

### 3.2 Runtime resources

```text
Internet
  |
  +--> Amplify Hosting / CloudFront
  |       |
  |       +--> Next.js web
  |
  +--> App Runner HTTPS endpoint
  |       |
  |       +--> control-plane FastAPI container
  |               |
  |               +--> CockroachDB Cloud over TLS
  |               +--> AgentCore Runtime invoke
  |               +--> S3 signed upload APIs
  |               +--> EventBridge PutEvents
  |               +--> SES outbound for approved demo action
  |
  +--> SES inbound MX
          |
          +--> S3 raw MIME bucket
          +--> Lambda ses_ingest
                   |
                   +--> control-plane internal artifact registration

EventBridge Scheduler --> Lambda trigger_wakeup --> control-plane trigger evaluation
SQS DLQs             <-- failed async invocations
CloudWatch/OTEL       <-- App Runner + Lambda + AgentCore telemetry
```

### 3.3 Why App Runner for control-plane

The control-plane needs:

- a normal HTTP API;
- long-lived connection pooling to CockroachDB;
- deterministic Memory Kernel code;
- easy container deployment;
- no need for Kubernetes.

App Runner can deploy from ECR/source and gives a managed web-service deployment. It is a better deployment boundary than creating separate ECS services for every module.

### 3.4 Why AgentCore Runtime is separate

Agents have different operational characteristics from deterministic APIs:

- model/network waits;
- LangGraph workflow execution;
- agent-specific tracing;
- tool invocations;
- framework-specific runtime.

Keep LangGraph there, but keep canonical persistence in CockroachDB.

## 4. Control-plane internal modules

The FastAPI container is one deployment unit but these modules must remain code-isolated.

### 4.1 `auth`

Input: HTTP bearer token.  
Output: immutable `Principal`.

```text
Principal
- subject: Cognito sub
- user_id: internal UUID
- tenant_id: internal UUID
- scopes: set[str]
- roles: set[str]
- trace_id
```

The rest of the application accepts `Principal`, not raw JWT claims.

### 4.2 `artifact_registry`

Responsibilities:

- create upload intent;
- finalize upload;
- register SES/S3 object;
- calculate/verify SHA-256 content hash;
- deduplicate;
- create `source_artifacts` row;
- emit ingestion job.

### 4.3 `retrieval`

Responsibilities:

- exact identity candidate retrieval;
- vector ANN retrieval;
- temporal filtering;
- graph/provenance expansion;
- authority/state reranking;
- compact `RetrievalContext` construction.

No write authority.

### 4.4 `memory_kernel`

Only application component that writes canonical semantic state.

Responsibilities:

- proposal validation;
- duplicate detection;
- identity gate;
- temporal policy;
- conflict creation/update;
- commitment/state transition calculation;
- belief version creation;
- aggregate revision increments;
- trigger mutation;
- outbox write;
- retry serialization failures.

### 4.5 `state_proof`

Pure read model builder.

Given a case/belief, returns:

- current canonical value;
- supporting evidence;
- opposing evidence;
- belief lineage;
- source authority;
- temporal applicability;
- conflict status;
- deterministic derivation explanation;
- actions that relied on this state.

It never calls an LLM. An LLM may later summarize its output.

### 4.6 `action_policy`

Validates whether an action can transition:

`PROPOSED -> NEEDS_REVIEW -> APPROVED -> EXECUTING`.

It checks:

- supported action type;
- basis case revision;
- exact draft hash;
- support belief versions still current;
- human approval;
- recipient allowlist/safety rules for demo;
- idempotency key.

### 4.7 `action_executor`

Deterministic side-effect adapter.

Current adapter:

- SES outbound to a safe demo counterparty mailbox or simulator.

Production interface can later support Gmail/Outlook OAuth, but that must not contaminate the core.

## 5. Ingestion architecture

Provenance supports three ingress modes with one downstream contract.

### 5.1 Forwarded email

Preferred live flow:

```text
User forwards mail
  -> unique ingest alias
  -> SES receipt rule
  -> raw MIME in S3
  -> ses_ingest Lambda
  -> ArtifactRegistered
```

#### User-to-alias mapping

Each user gets an opaque ingest alias such as:


7K4Q...@in.provenance.app`

Do not encode user UUID/email directly.

Database table stores alias hash -> user mapping. Rotate/disable aliases if abused.

### 5.2 File upload

```text
Browser -> POST upload-intent
        <- pre-signed S3 URL + artifact_id
Browser -> PUT S3
Browser -> POST complete
```

The backend verifies S3 object metadata/hash before admitting the artifact.

### 5.3 Demo `.eml` import

If SES domain setup is delayed, the exact same raw MIME parser consumes an uploaded `.eml`. This keeps the demo independent of DNS/MX readiness.

## 6. Content extraction pipeline

### 6.1 MIME/email

Do not send the entire raw MIME to an LLM.

Pipeline:

1. parse headers;
2. normalize sender/recipient/message-id/in-reply-to;
3. extract text/plain and sanitized text/html;
4. separate quoted history/signatures where feasible;
5. register attachments separately;
6. run attachment extractors;
7. produce normalized artifact text blocks with source offsets.

### 6.2 PDFs/images

- text-based PDF: use deterministic text extraction first;
- scanned/image-heavy/form/table document: Textract;
- store Textract output as parser metadata, not canonical truth;
- every extracted evidence item retains page/bounding region or text-span references where available.

## 7. End-to-end new-artifact sequence

```text
User/SES        Control Plane       S3       AgentCore       CockroachDB
   |                 |               |           |                |
   |---artifact----->|               |           |                |
   |                 |---store/ref-->|           |                |
   |                 |---register artifact----------------------->|
   |                 |               |           |                |
   |                 |---interpret request----->|                |
   |                 |<--evidence candidates----|                |
   |                 |---admit immutable evidence--------------->|
   |                 |---retrieval query------------------------>|
   |                 |<--candidate context-----------------------|
   |                 |---reason/reconcile------>|                |
   |                 |<--MemoryProposal---------|                |
   |                 |                                                
   |                 |---Kernel SERIALIZABLE transaction---------->|
   |                 |    claims/beliefs/conflict/state/outbox      |
   |                 |<--KernelCommitResult------------------------|
   |                 |---advocate if needed---->|                |
   |                 |<--ActionIntent draft-----|                |
   |<--updated UI-----|               |           |                |
```

Important: immutable evidence may be admitted before semantic belief commit. If reasoning fails, evidence remains available for retry without being treated as canonical belief.

## 8. Relationship/case architecture

A user may have many relationships with the same counterparty.

Example:

```text
User
  |
  +-- ISP account A (old apartment)
  |      |
  |      +-- cancellation case
  |      +-- billing dispute case
  |
  +-- ISP account B (new apartment)
```

Never identify a relationship from counterparty name alone.

Identity resolver uses evidence such as:

- account/reference numbers;
- email sender domain/address;
- service address;
- booking/order IDs;
- phone number suffix;
- dates;
- currency/amount;
- user-confirmed mapping.

## 9. Canonical aggregate boundary

The primary transaction aggregate is the **Case**.

A case transaction may include:

- case row/revision;
- beliefs for that case;
- commitments;
- fulfillment application;
- conflicts;
- triggers;
- state transition;
- outbox events.

Relationships are broader containers and should not be locked/updated for every case event unless relationship-level state truly changes.

## 10. Case revision

Every successful canonical state-changing Kernel transaction increments:

`cases.revision`.

Revision is used for:

- optimistic stale-read detection at API/UI boundaries;
- action approval binding;
- event aggregate ordering;
- trigger staleness checks;
- debugging.

CockroachDB serializability still provides correctness; revision is a domain version, not a replacement for DB isolation.

## 11. Reaction architecture

### 11.1 Domain event delivery

Kernel commits an `outbox_events` row in the same transaction as state.

After commit:

- control-plane makes a best-effort immediate dispatch signal;
- scheduled/worker sweeper guarantees recovery of pending outbox rows;
- dispatcher publishes normalized events to EventBridge;
- consumers deduplicate by `event_id`.

Never assume exactly-once delivery.

### 11.2 Prospective triggers

For a deadline `T`:

1. Kernel stores durable trigger predicate and version.
2. Scheduler gets a one-time wakeup at/after `T`.
3. Scheduler invocation triggers worker.
4. Worker calls deterministic trigger-evaluation API.
5. API reloads trigger + current case revision/state.
6. If predicate false, record NOOP/disarm.
7. If predicate true, atomically mark fired and emit event.
8. Advocate may create new ActionIntent.

The scheduled message is never treated as proof that the condition is still true.

## 12. LangGraph persistence boundary

LangGraph has its own workflow/checkpoint persistence capability, but Provenance uses it only for:

- transient graph recovery;
- node retry state;
- optional human-in-the-loop workflow continuation;
- short-term execution context.

It must **never** be queried as the authoritative answer to:

- what does the user currently believe?
- what is still owed?
- what evidence supports the state?
- what action has been approved?

Those answers always come from CockroachDB.

If all LangGraph checkpoints are deleted, canonical product memory remains correct.

## 13. Judge Mode architecture

Judge Mode is not fake debug text. It is constructed from real trace records.

Each vertical slice has one `trace_id` propagated through:

- API request;
- artifact;
- agent invocation;
- retrieval log;
- proposal;
- kernel decision;
- DB transaction result/retry count;
- outbox event;
- advocate run;
- action intent;
- approval;
- execution.

The UI renders a simplified DAG from those records.

## 14. Scaling model

Current scale is small; the architecture should still have clean scaling dimensions.

### Stateless/scalable horizontally

- web frontend;
- App Runner instances;
- AgentCore runtime executions;
- Lambda workers.

### Stateful

- CockroachDB canonical state;
- S3 immutable artifacts.

### Hot-key risk

Contention is localized mainly to one active case. This is acceptable and desirable: conflicting updates to the same case should serialize.

Avoid one tenant-wide mutable “memory row” that would create unnecessary contention.

## 15. Production multi-region evolution

Application compute may remain single-region for now.

Production CockroachDB evolution:

- home user rows via locality strategy where appropriate;
- evaluate `REGIONAL BY ROW` for user-owned state;
- choose zone- vs region-failure survival intentionally;
- keep globally shared, read-mostly reference metadata separate from private user state.

Do not claim multi-region application active/active if it is not deployed.

## 16. Non-negotiable anti-patterns

Do not implement:

- direct LLM -> SQL canonical updates;
- one generic `memories(jsonb, embedding)` table;
- a second “truth” copy in DynamoDB/AgentCore Memory/LangGraph Store;
- async event handlers that mutate several invariant-linked rows without a DB transaction;
- a scheduler that directly sends an action without re-reading current state;
- approval that is not invalidated when its basis state changes;
- raw prompt traces containing entire private artifacts by default.

## 17. Official references

- AgentCore Runtime and LangGraph: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html
- AgentCore gateway/runtime security: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html
- App Runner: https://docs.aws.amazon.com/apprunner/latest/dg/what-is-apprunner.html
- SES receiving: https://docs.aws.amazon.com/ses/latest/dg/receiving-email-concepts.html
- Textract document analysis: https://docs.aws.amazon.com/textract/latest/dg/how-it-works-analyzing.html
- CockroachDB multi-region overview: https://www.cockroachlabs.com/docs/stable/multiregion-overview/
