# Provenance — Reliability, Observability, Evaluation, and Demo

Status: planning-complete baseline v1.1
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared

## 1. Why this document exists

A demo can appear correct while the system underneath is brittle. Provenance should make its reliability properties visible and testable, so that production readiness is a property of the system rather than of the walkthrough.

## 2. Reliability philosophy

> Preserve evidence first. Commit truth carefully. React asynchronously. Revalidate before action.

Failure of an LLM, worker, event delivery, or UI should delay progress, not corrupt memory.

## 3. Failure matrix

| Failure | Required system behavior | User impact |
|---|---|---|
| extraction model timeout | artifact/evidence stays pending; bounded retry | delayed processing |
| invalid structured model output | one repair attempt then pending/review | no wrong commit |
| resolver unavailable | preserve evidence; mark pending review | no forced conclusion |
| model hallucinated evidence ID | schema/provenance reject | no canonical mutation |
| duplicate email/upload | dedupe by source/message/hash | one logical artifact |
| wrong vector candidate | relational/identity gate rejects | possible pending identity |
| vector miss | exact identifier + broader structured fallback | reduced recall, no corruption |
| two updates same case | serializable retry | slightly higher latency |
| duplicate domain event | consumer dedupe | no duplicate effect |
| outbox publish failure | durable retry/backoff | delayed async reaction |
| scheduler fires after fulfillment | current predicate false -> NOOP | no false alert |
| user approves then new evidence arrives | case revision mismatch -> approval stale | user re-reviews |
| outbound send timeout | use idempotency/provider correlation; reconcile | no blind resend |
| malicious prompt in PDF/email | no capability path from Interpreter to truth/action | contained |
| AgentCore outage | canonical memory/read APIs remain intact | reasoning delayed |
| Bedrock model outage | evidence remains durable; state unchanged | reasoning delayed |
| App Runner restart | stateless process recovers; DB/outbox remain | transient retry |
| S3 unavailable | artifact ingestion pauses | existing memory unaffected |
| Cockroach transaction contention | retry 40001 | increased commit latency |
| CockroachDB regional/node fault | rely on configured cluster resilience | depends on chosen plan/topology |

## 4. Timeouts and retry budgets

Use explicit budgets; do not let retries stack infinitely.

Recommended starting values:

### Public API

- normal reads: 5-10s hard timeout target;
- artifact completion returns `QUEUED` rather than waiting for full agent workflow;
- action approval should complete DB transition quickly, then execute async.

### Model calls

- bounded client timeout;
- network/throttle retries: 2-3;
- schema repair: maximum 1;
- no nested model retry loops.

### CockroachDB transaction

- serialization retry: up to 5 attempts in synchronous Kernel call;
- exponential backoff + jitter;
- never call external services during retryable transaction.

### Outbox

Example retry schedule:

- 1s, 5s, 30s, 2m, 10m;
- then DEAD + alert/manual replay.

At current traffic levels, exact values matter less than bounded behavior and visibility.

## 5. Observability data model

Every end-to-end flow has:

- `trace_id` — cross-service flow;
- `agent_run_id` — LangGraph run;
- `proposal_id` — semantic write proposal;
- `kernel_decision_id` — memory admission decision;
- `event_id` — async domain event;
- `action_intent_id` — user-facing proposed side effect.

These IDs should be clickable in Judge Mode.

## 6. Trace span map

Recommended spans:

```text
artifact.register
artifact.parse
embedding.generate
agent.interpreter.run
retrieval.identity
retrieval.vector
retrieval.expand
agent.resolver.run
memory.proposal.build
memory.kernel.preflight
memory.kernel.transaction
memory.kernel.retry
outbox.dispatch
agent.advocate.run
action.intent.create
action.approve
action.execute
trigger.evaluate
```

Attach IDs, durations, model names, candidate counts, and reason codes. Avoid raw private content.

## 7. Metrics

### Product/memory

- active cases;
- reopened cases;
- unresolved commitments;
- active conflicts;
- prospective triggers armed/fired/no-op;
- action intents proposed/approved/rejected;
- time from artifact -> canonical commit.

### Agent quality

- extraction schema invalid rate;
- resolver escalation rate;
- proposal acceptance rate;
- pending identity rate;
- unsupported draft claim rate;
- model fallback rate.

### Retrieval

- exact-identifier hit rate;
- top-1/top-3 correct case rate on eval set;
- vector latency;
- candidate count before/after rerank.

### Database

- transaction latency;
- serialization retry count;
- outbox pending age;
- connection pool utilization;
- query latency for dashboard/State Proof/vector retrieval.

### Cost

- model invocations per artifact;
- input/output tokens by model role;
- embeddings generated/reused;
- estimated model cost per processed artifact;
- Textract pages processed.

## 8. Judge Mode UI

Judge Mode should have four panels.

### Panel A — consumer state

Human-friendly case view.

### Panel B — State Proof

Why Provenance believes the current state.

### Panel C — Memory Trace

Actual processing path and deterministic/LLM boundary.

### Panel D — Systems status

Small live indicators:

- CockroachDB canonical commit ✓
- vector retrieval candidate count
- transaction revision/retry count
- outbox delivered ✓
- model route used
- action approval required

Do not overwhelm the main demo with infrastructure; reveal it after the product “aha”.

## 9. Evaluation strategy

Provenance needs an explicit evaluation corpus so the demo is not the only evidence of correctness.

Create `evals/datasets/memory_cases.jsonl` with synthetic but realistic cases.

Each case should include:

- previous canonical state;
- source artifacts;
- expected evidence extraction;
- expected relationship/case match;
- expected conflict outcome;
- expected state transition;
- expected action gate;
- adversarial variants.

## 10. Dataset categories

Exactly 51 canonical scenarios across:

### Identity

- same company, two accounts;
- same amount, different cases;
- changed sender address;
- missing account number;
- forwarded old thread;
- wrong user/cross-tenant attempt.

### Temporal

- late-imported old evidence;
- policy changed after commitment;
- deadline stated as business days;
- ambiguous relative date;
- email quotes earlier date.

### Contradiction

- written confirmation vs later denial;
- two high-authority sources conflict;
- low-authority statement vs verified transaction;
- partial fulfillment vs “fully paid” claim;
- commitment withdrawal.

### Commitments

- monetary full/partial fulfillment;
- non-monetary repair deadline;
- conditional commitment;
- commitment without due date;
- vague “we may” that must not become commitment.

### Prospective memory

- due date passes unresolved;
- due date passes after fulfillment -> no-op;
- trigger state changed before schedule;
- duplicate schedule invocation.

### Safety

- prompt injection;
- malicious attachment text;
- model invents evidence ID;
- action draft includes unsupported claim;
- stale approval.

## 11. Eval metrics

### Extraction

- entity/reference exact match;
- date normalization accuracy;
- amount/currency accuracy;
- claim/commitment type F1;
- provenance span validity.

### Retrieval

- relationship Recall@1 / Recall@3;
- case Recall@1 / Recall@3;
- MRR;
- ambiguous-case abstention correctness.

### Memory admission

- expected Kernel decision accuracy;
- conflict detection precision/recall;
- expected state transition accuracy;
- invariant violations: target **zero**.

### End-to-end

- final case state exact match;
- correct outstanding amount;
- correct action gate;
- correct State Proof support set.

## 12. Golden deterministic tests

These should run without Bedrock by using stored model fixtures.

1. MemoryProposal fixture -> expected Kernel DB state.
2. State Proof fixture -> exact support/conflict structure.
3. Trigger predicate fixture -> true/false/no-op.
4. Action approval fixture -> stale/current cases.
5. Outbox consumer duplicate -> one business effect.
6. Serialization/retry test -> invariant-preserving final state.

This separates model quality from system correctness.

## 13. Concurrent update test

Seed:

```text
commitment = 1200 USD
fulfilled = 0
outstanding = 1200
case revision = 8
```

Run concurrently:

A. admit $300 bank fulfillment.
B. admit provider claim “refund fully issued”.

Expected:

- no impossible combination like `FULFILLED + outstanding=900`;
- one transaction may retry;
- both evidence/claims preserved;
- canonical commitment derived from admitted fulfillment + conflict rules;
- final case revision reflects serialized commits.

Display this test result or a miniature concurrency visualization in a technical appendix or review discussion if useful.

## 14. Hero demo — exact narrative

### Setup visible before demo

Dashboard:

```text
THE MOVE — 4 relationships

✓ Employer relocation reimbursement — resolved
⚠ Moving company damage reimbursement — $420 overdue
⚠ Landlord deposit — $1,800 overdue
✓ Old ISP cancellation — resolved four months ago
```

This instantly establishes long-lived cross-relationship memory.

### Event

Forward/upload a new ISP invoice:

```text
Amount: $186
Billing period: June 1–June 30
Account: same old ISP account
```

Old canonical memory contains written provider confirmation:

```text
Cancellation confirmed May 15
Service termination effective May 31
```

### Product moment 1 — state resurrection

UI changes:

```text
OLD ISP CANCELLATION
RESOLVED -> REOPENED

⚠ New claim contradicts the existing record
```

Message:

> “This relationship was closed. New evidence reopened it.”

### Product moment 2 — State Proof

Show:

- termination confirmation;
- invoice service period;
- contradiction relationship;
- current canonical position;
- why new invoice is recorded as a claim rather than overwriting history.

### Product moment 3 — agency with control

Provenance drafts a grounded response.

UI shows:

- evidence attached/referenced;
- basis case revision;
- `Approve & Send`.

The user approves.

Executor revalidates revision and sends to safe demo inbox.

### Product moment 4 — prospective memory

Reveal:

> “This isn't the only part of the move that never ended.”

Landlord deposit trigger is overdue because promised 30-day period passed and outstanding remains $1,800.

This proves memory can wake itself based on future state, not only react to a new email.

### Technical reveal

Switch to Memory Trace:

```text
artifact received
  -> evidence extracted
  -> 18,000+ seeded memory vectors scoped to user
  -> 7 semantic candidates
  -> 1 exact account/case match
  -> contradiction detected
  -> MemoryProposal submitted
  -> Kernel transaction COMMITTED (case rev 12 -> 13)
       + claim
       + conflict
       + reopened case
       + state transition
       + outbox event
  -> Advocate action proposed
  -> human approval
  -> execution revalidated rev 13
```

Use a large enough synthetic evidence corpus to make vector retrieval visibly nontrivial, while keeping canonical business state small and explainable.

## 15. Seed-data strategy

The demo dataset should contain:

- 1 hero user;
- 4 “move” counterparties;
- 8-12 cases;
- 25-40 important evidence items;
- thousands of additional semantically plausible historical evidence chunks for retrieval scale;
- deliberately similar decoy ISP/utility memories;
- at least 2 other users/tenants to prove search isolation in tests, not UI.

Do not create 18,000 manually curated facts. Generate synthetic decoy evidence and keep hero evidence hand-curated.

## 16. Failure demonstration option

For reviewers who want technical depth, have a safe toggle:

### “Inject duplicate event”

Send the same `case.reopened` event twice.

Judge Mode displays:

```text
consumer first delivery: PROCESSED
consumer duplicate: NOOP (event_id already recorded)
```

or:

### “Inject concurrent update”

Two memory proposals touch the same commitment; show one serialization retry and a correct final state.

Do not make the primary 30-second demo depend on failure injection.

## 17. Demo reliability requirements

Before any live walkthrough:

- all hero artifacts preloaded locally/in S3;
- demo can run via uploaded `.eml` even if SES inbound DNS misbehaves;
- outbound send can target a controlled mailbox/simulator;
- model responses for hero path should be stable under temperature/config;
- maintain a “demo fixture mode” for emergency fallback that still executes the real Kernel/database/event path using stored extraction fixture, clearly disclosed if used;
- do not fake DB commits/trace data.

## 18. Production-readiness story

When asked “what makes this more than a demo?” answer with concrete guarantees:

- LLM cannot write truth directly;
- every belief is provenance-backed;
- contradictions persist as first-class state;
- case aggregate transitions are serializable;
- event processing is idempotent;
- long-lived triggers revalidate current state;
- approval becomes stale if underlying memory changes;
- agent DB access is least privilege;
- raw evidence is isolated from derived memory;
- system has measurable evals beyond one scripted flow.

## 19. Definition of Done

The architecture is implemented enough to ship when all are true:

### Memory

- evidence/claim/belief/commitment/conflict/case tables live in CockroachDB;
- vectors are stored/indexed in CockroachDB;
- Memory Kernel is sole canonical write path;
- State Proof works without LLM;
- at least one serializable concurrency test passes.

### Agents

- LangGraph ingestion graph deployed to AgentCore Runtime;
- Interpreter emits typed proposals;
- strong resolver is conditional;
- Advocate drafts only from State Proof;
- model/prompt/graph versions recorded.

### AWS

- Cognito authentication;
- S3 evidence storage;
- one real ingestion path (SES or upload `.eml`);
- EventBridge/Scheduler prospective-memory wakeup;
- CloudWatch/OTEL traces;
- approved outbound demo action.

### CockroachDB integration

- Distributed Vector Indexing used in actual retrieval;
- CockroachDB MCP used in a meaningful read path/agent inspection path;
- CockroachDB remains canonical persistent memory.

### UX/demo

- hero story works end-to-end;
- State Proof is understandable;
- Judge Mode shows real trace;
- no raw chain-of-thought is exposed;
- user sees clear human approval before action.

### Evaluation

- >= 40 scenario evaluation corpus;
- extraction/retrieval/memory admission metrics generated;
- adversarial prompt-injection cases included;
- deterministic Kernel test suite green.

## 20. Official references

- AgentCore Observability: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html
- EventBridge Scheduler retry/DLQ: https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule.html
- EventBridge Scheduler overview: https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html
- CockroachDB transaction retry: https://www.cockroachlabs.com/docs/stable/transaction-retry-error-reference
- CockroachDB vector index tuning: https://www.cockroachlabs.com/docs/stable/vector-indexes
