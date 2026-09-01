# Provenance — Data Model, Memory Kernel, and Transactions

Status: planning-complete baseline v1.1
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared

## 1. Purpose

This document is the authoritative implementation contract for durable memory. If code and prompts disagree with this document, the memory model wins.

## 2. Storage split

### S3 owns bytes

S3 contains immutable/raw external artifacts:

- raw MIME email;
- PDFs;
- screenshots/images;
- uploaded attachments;
- optional normalized parser output snapshots.

### CockroachDB owns meaning and state

CockroachDB contains:

- artifact metadata/hash;
- evidence spans;
- claims;
- beliefs and belief versions;
- support/conflict edges;
- commitments and fulfillments;
- current case/relationship projections;
- triggers;
- actions/approvals/executions;
- trace/audit metadata;
- vectors;
- transactional outbox.

## 3. Database conventions

### 3.1 Primary keys

Use UUIDs generated application-side.

### 3.2 Tenant ownership

Every user-owned aggregate contains `tenant_id` and usually `user_id` directly, even when derivable through joins. This makes authorization predicates explicit and vector-prefix isolation possible.

### 3.3 Timestamps

Use `TIMESTAMPTZ`.

- `created_at`, `recorded_at`, `updated_at`: system/database time.
- `valid_from`, `valid_to`: real-world validity supplied from evidence/resolution.
- validity interval is `[valid_from, valid_to)`.

### 3.4 JSONB

Use JSONB only for variable payloads whose semantics are still typed at the application boundary:

- normalized external identifiers;
- condition AST;
- model metadata;
- event payload;
- artifact parser metadata.

Do not hide core domain state in JSONB.

### 3.5 Status values

Use `STRING` plus application enums and DB `CHECK` constraints for critical states. Avoid migration-heavy custom DB enum churn.

## 4. Core tables

The definitions below are deliberately close to DDL so a coding agent can translate them directly into Alembic migrations.

### 4.1 `tenants`

```text
id UUID PK
name STRING NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

For v1: one tenant per user is acceptable, but keep the tenant abstraction.

### 4.2 `users`

```text
id UUID PK
tenant_id UUID NOT NULL
cognito_sub STRING NOT NULL UNIQUE
email STRING NULL
display_name STRING NULL
timezone STRING NOT NULL DEFAULT 'UTC'
home_region STRING NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL

UNIQUE (tenant_id, id)
```

Index: `(tenant_id, cognito_sub)`.

### 4.3 `ingest_aliases`

Maps opaque forwarded-email alias to user.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
alias_hash BYTES NOT NULL UNIQUE
status STRING NOT NULL CHECK status IN ('ACTIVE','DISABLED')
created_at TIMESTAMPTZ NOT NULL
rotated_at TIMESTAMPTZ NULL
```

Store a hash/HMAC of the alias token, not necessarily the plaintext token.

### 4.4 `counterparties`

```text
id UUID PK
normalized_name STRING NOT NULL
kind STRING NOT NULL
canonical_domain STRING NULL
metadata JSONB NULL
created_at TIMESTAMPTZ NOT NULL
```

Counterparty itself may be shared reference metadata. User-specific facts belong in relationships.

### 4.5 `relationships`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
counterparty_id UUID NOT NULL
relationship_type STRING NOT NULL
label STRING NULL
external_account_ref STRING NULL
normalized_identifiers JSONB NULL
status STRING NOT NULL CHECK status IN ('ACTIVE','INACTIVE','CLOSED')
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
revision INT8 NOT NULL DEFAULT 0
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Indexes:

- `(tenant_id, user_id, counterparty_id, status)`
- `(tenant_id, user_id, external_account_ref)` where non-null

### 4.6 `contexts`

Optional cross-relationship grouping such as “The Move”.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
title STRING NOT NULL
context_type STRING NOT NULL
status STRING NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

`cases.context_id` may point here.

### 4.7 `cases`

Primary consistency aggregate.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
relationship_id UUID NOT NULL
context_id UUID NULL
case_type STRING NOT NULL
title STRING NOT NULL
status STRING NOT NULL
revision INT8 NOT NULL DEFAULT 0
opened_at TIMESTAMPTZ NOT NULL
resolved_at TIMESTAMPTZ NULL
last_activity_at TIMESTAMPTZ NOT NULL
reopened_count INT8 NOT NULL DEFAULT 0
attention_level STRING NOT NULL DEFAULT 'NONE'
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Case status values:

- `OPEN`
- `WAITING`
- `ACTIONABLE`
- `IN_PROGRESS`
- `DISPUTED`
- `BLOCKED`
- `AWAITING_USER`
- `RESOLVED`
- `REOPENED`
- `SUPERSEDED`

Indexes:

- `(tenant_id, user_id, status, last_activity_at DESC)`
- `(relationship_id, status)`
- `(context_id, status)`

### 4.8 `source_artifacts`

Immutable artifact identity/metadata.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
source_type STRING NOT NULL
s3_bucket STRING NOT NULL
s3_key STRING NOT NULL
content_sha256 BYTES NOT NULL
mime_type STRING NOT NULL
source_message_id STRING NULL
sender STRING NULL
recipient STRING NULL
subject STRING NULL
received_at TIMESTAMPTZ NOT NULL
event_time TIMESTAMPTZ NULL
parser_status STRING NOT NULL
parser_version STRING NULL
parser_metadata JSONB NULL
created_at TIMESTAMPTZ NOT NULL
```

Deduplication indexes:

- `UNIQUE (tenant_id, user_id, content_sha256, source_type)` as default dedupe key;
- optional `UNIQUE (tenant_id, user_id, source_message_id)` for email where present.

If identical bytes legitimately belong to different contexts, create an explicit artifact-link record rather than duplicate raw storage.

### 4.9 `evidence_items`

Atomic immutable semantic observation from an artifact.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
artifact_id UUID NOT NULL
evidence_type STRING NOT NULL
normalized_text STRING NOT NULL
exact_text STRING NULL
source_locator JSONB NULL
actor_ref STRING NULL
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
observed_at TIMESTAMPTZ NOT NULL
extraction_confidence DECIMAL(5,4) NOT NULL
source_authority DECIMAL(5,4) NULL
embedding VECTOR(1024) NULL
embedding_model STRING NULL
embedding_version STRING NULL
created_at TIMESTAMPTZ NOT NULL
```

`source_locator` examples:

- email text span/part;
- PDF page + bounding box;
- attachment name + page;
- MIME part identifier.

Vector index:

```text
VECTOR INDEX evidence_embedding_ann_idx
  (user_id, embedding vector_cosine_ops)
```

Exact syntax should follow the cluster version. User prefix is mandatory for the first index so ANN search cannot cross users.

Additional indexes:

- `(tenant_id, user_id, artifact_id)`
- `(tenant_id, user_id, evidence_type, observed_at DESC)`

### 4.10 `claims`

A source actor's assertion; never automatically canonical.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
case_id UUID NULL
relationship_id UUID NULL
subject_type STRING NOT NULL
subject_id UUID NOT NULL
predicate STRING NOT NULL
object_type STRING NOT NULL
object_json JSONB NOT NULL
actor_type STRING NOT NULL
actor_id STRING NULL
evidence_id UUID NOT NULL
claim_kind STRING NOT NULL
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
authority_score DECIMAL(5,4) NULL
extraction_confidence DECIMAL(5,4) NOT NULL
recorded_at TIMESTAMPTZ NOT NULL
```

Claim kinds:

- `OBSERVATION`
- `COUNTERPARTY_CLAIM`
- `USER_CLAIM`
- `COMMITMENT_CLAIM`
- `POLICY_TERM`
- `FULFILLMENT_CLAIM`
- `CORRECTION`
- `INFERENCE`

Index `(tenant_id, user_id, subject_type, subject_id, predicate, recorded_at DESC)`.

### 4.11 `beliefs`

Stable proposition identity.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
case_id UUID NULL
subject_type STRING NOT NULL
subject_id UUID NOT NULL
predicate STRING NOT NULL
current_version_id UUID NULL
created_at TIMESTAMPTZ NOT NULL

UNIQUE (tenant_id, user_id, subject_type, subject_id, predicate)
```

### 4.12 `belief_versions`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
belief_id UUID NOT NULL
version_no INT8 NOT NULL
value_type STRING NOT NULL
value_json JSONB NOT NULL
epistemic_status STRING NOT NULL
belief_confidence DECIMAL(5,4) NOT NULL
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
recorded_at TIMESTAMPTZ NOT NULL
superseded_at TIMESTAMPTZ NULL
kernel_decision_id UUID NOT NULL

UNIQUE (belief_id, version_no)
```

Epistemic status:

- `CONFIRMED`
- `PROBABLE`
- `UNCERTAIN`
- `DISPUTED`
- `SUPERSEDED`
- `RETRACTED`

Index `(belief_id, version_no DESC)`.

### 4.13 `belief_support`

Many-to-many provenance edges.

```text
id UUID PK
tenant_id UUID NOT NULL
belief_version_id UUID NOT NULL
source_kind STRING NOT NULL  # EVIDENCE | CLAIM | BELIEF_VERSION | DERIVATION
source_id UUID NOT NULL
relation STRING NOT NULL     # SUPPORTS | CONTRADICTS | QUALIFIES
weight DECIMAL(5,4) NULL
reason_code STRING NULL
created_at TIMESTAMPTZ NOT NULL

UNIQUE (belief_version_id, source_kind, source_id, relation)
```

Invariant: every non-deterministically-derived canonical belief version has at least one support edge.

### 4.14 `conflicts`

Durable contradiction object.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
case_id UUID NOT NULL
subject_type STRING NOT NULL
subject_id UUID NOT NULL
predicate STRING NOT NULL
left_source_kind STRING NOT NULL
left_source_id UUID NOT NULL
right_source_kind STRING NOT NULL
right_source_id UUID NOT NULL
conflict_type STRING NOT NULL
status STRING NOT NULL
severity STRING NOT NULL
requires_human BOOL NOT NULL
canonical_belief_version_id UUID NULL
resolution_reason_code STRING NULL
resolution_notes STRING NULL
detected_at TIMESTAMPTZ NOT NULL
resolved_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
```

Status:

- `OPEN`
- `AUTO_RESOLVED`
- `NEEDS_HUMAN`
- `RESOLVED`
- `SUPERSEDED`

Deduplicate conflict identity by semantic subject + two sources where feasible.

### 4.15 `commitments`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
case_id UUID NOT NULL
obligor_type STRING NOT NULL
obligor_id STRING NULL
beneficiary_type STRING NOT NULL
beneficiary_id STRING NULL
commitment_type STRING NOT NULL
description STRING NOT NULL
currency STRING NULL
committed_amount DECIMAL(20,4) NULL
fulfilled_amount DECIMAL(20,4) NULL
outstanding_amount DECIMAL(20,4) NULL
due_at TIMESTAMPTZ NULL
condition_ast JSONB NULL
source_claim_id UUID NOT NULL
status STRING NOT NULL
revision INT8 NOT NULL DEFAULT 0
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Status:

- `PROPOSED`
- `ACTIVE`
- `PARTIAL`
- `DISPUTED`
- `FULFILLED`
- `EXPIRED`
- `SUPERSEDED`

Critical checks:

- amounts >= 0;
- fulfilled <= committed when both present;
- outstanding = committed - fulfilled when monetary;
- outstanding > 0 implies status != `FULFILLED`;
- outstanding = 0 and admitted fulfillment complete may transition to `FULFILLED`.

### 4.16 `fulfillments`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
commitment_id UUID NOT NULL
evidence_id UUID NOT NULL
currency STRING NULL
amount DECIMAL(20,4) NULL
quantity DECIMAL(20,4) NULL
fulfilled_at TIMESTAMPTZ NOT NULL
admission_status STRING NOT NULL
confidence DECIMAL(5,4) NOT NULL
created_at TIMESTAMPTZ NOT NULL

UNIQUE (commitment_id, evidence_id)
```

### 4.17 `state_transitions`

Append-only audit of canonical aggregate changes.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
case_id UUID NOT NULL
case_revision INT8 NOT NULL
transition_type STRING NOT NULL
from_state STRING NULL
to_state STRING NULL
reason_code STRING NOT NULL
proposal_id UUID NULL
kernel_decision_id UUID NOT NULL
trace_id UUID NOT NULL
recorded_at TIMESTAMPTZ NOT NULL

UNIQUE (case_id, case_revision, transition_type, id)
```

### 4.18 `memory_proposals`

Agents write only proposals, never canonical state.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
trace_id UUID NOT NULL
schema_version STRING NOT NULL
proposal_type STRING NOT NULL
source_artifact_ids JSONB NOT NULL
evidence_ids JSONB NOT NULL
candidate_relationship_id UUID NULL
candidate_case_id UUID NULL
payload JSONB NOT NULL
model_id STRING NOT NULL
prompt_version STRING NOT NULL
status STRING NOT NULL
created_at TIMESTAMPTZ NOT NULL
decided_at TIMESTAMPTZ NULL
kernel_decision_id UUID NULL
```

Status:

- `SUBMITTED`
- `ACCEPTED`
- `ACCEPTED_WITH_CONFLICT`
- `NOOP_DUPLICATE`
- `PENDING_IDENTITY`
- `PENDING_HUMAN_REVIEW`
- `REJECTED_INVALID_PROVENANCE`
- `REJECTED_INVARIANT`
- `REJECTED_SCHEMA`

### 4.19 `kernel_decisions`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
proposal_id UUID NOT NULL
decision STRING NOT NULL
reason_codes JSONB NOT NULL
case_revision_before INT8 NULL
case_revision_after INT8 NULL
retry_count INT8 NOT NULL DEFAULT 0
trace_id UUID NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

### 4.20 `prospective_triggers`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
case_id UUID NOT NULL
trigger_type STRING NOT NULL
predicate_ast JSONB NOT NULL
not_before TIMESTAMPTZ NULL
expires_at TIMESTAMPTZ NULL
state STRING NOT NULL
evaluation_version INT8 NOT NULL DEFAULT 0
basis_case_revision INT8 NOT NULL
schedule_name STRING NULL
last_evaluated_at TIMESTAMPTZ NULL
last_result STRING NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

State: `ARMED | FIRED | DISARMED | EXPIRED`.

### 4.21 `action_intents`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
case_id UUID NOT NULL
action_type STRING NOT NULL
recipient STRING NULL
draft_payload JSONB NOT NULL
draft_sha256 BYTES NOT NULL
rationale STRING NOT NULL
supporting_belief_versions JSONB NOT NULL
basis_case_revision INT8 NOT NULL
status STRING NOT NULL
created_by_agent_run_id UUID NULL
approved_by_user_id UUID NULL
approved_at TIMESTAMPTZ NULL
approval_draft_sha256 BYTES NULL
idempotency_key STRING NOT NULL UNIQUE
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

### 4.22 `action_executions`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
action_intent_id UUID NOT NULL
attempt_no INT8 NOT NULL
provider STRING NOT NULL
provider_correlation_id STRING NULL
request_sha256 BYTES NOT NULL
status STRING NOT NULL
error_code STRING NULL
started_at TIMESTAMPTZ NOT NULL
finished_at TIMESTAMPTZ NULL

UNIQUE (action_intent_id, attempt_no)
```

### 4.23 `outbox_events`

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
aggregate_type STRING NOT NULL
aggregate_id UUID NOT NULL
aggregate_version INT8 NOT NULL
event_type STRING NOT NULL
payload_version STRING NOT NULL
payload JSONB NOT NULL
trace_id UUID NOT NULL
status STRING NOT NULL
attempt_count INT8 NOT NULL DEFAULT 0
next_attempt_at TIMESTAMPTZ NOT NULL
created_at TIMESTAMPTZ NOT NULL
dispatched_at TIMESTAMPTZ NULL
```

Status: `PENDING | DISPATCHING | DISPATCHED | FAILED_RETRYABLE | DEAD`.

Index `(status, next_attempt_at, created_at)`.

### 4.24 `processed_events`

```text
consumer_name STRING NOT NULL
event_id UUID NOT NULL
processed_at TIMESTAMPTZ NOT NULL
result_hash BYTES NULL
PRIMARY KEY (consumer_name, event_id)
```

### 4.25 `agent_runs`

Metadata only; not product memory.

```text
id UUID PK
tenant_id UUID NOT NULL
user_id UUID NOT NULL
trace_id UUID NOT NULL
graph_name STRING NOT NULL
graph_version STRING NOT NULL
model_route JSONB NOT NULL
status STRING NOT NULL
started_at TIMESTAMPTZ NOT NULL
finished_at TIMESTAMPTZ NULL
input_artifact_id UUID NULL
error_code STRING NULL
```

## 5. Bitemporal rules

### 5.1 Rule T1

`recorded_at` is when Provenance learned/committed the fact; it never substitutes for outside-world validity.

### 5.2 Rule T2

When evidence has no trustworthy effective date, set validity unknown rather than inventing one.

### 5.3 Rule T3

If a new claim explicitly supersedes an earlier policy/commitment effective from time `T`, the new version starts at `T`; the prior version's `valid_to` may close at `T` only if source authority supports supersession.

### 5.4 Rule T4

Late-arriving evidence may create a historical belief version without necessarily changing current state.

Example: July import proves a policy that was valid only in March. It may explain a March commitment but should not become today's policy.

## 6. Source authority model

Do not maintain one global “trustworthiness” score.

Authority is predicate-aware.

Represent the first version as code/config:

```text
predicate family           source kind                    authority band
payment_received           bank/payment record           0.95-1.00
contract_term              signed agreement               0.95-1.00
support_commitment         official written support       0.80-0.95
policy_term                official policy document       0.85-0.98
service_status             provider confirmation          0.80-0.95
formal_entitlement         marketing page                 0.30-0.60
historical_conversation    user recollection              0.45-0.75
external_fact              model inference                0.00-0.20
```

The model may recommend a source class; the Kernel maps class + predicate to configured authority.

## 7. MemoryProposal typed contract

A proposal should resemble:

```text
MemoryProposal
  schema_version: "1.0"
  proposal_id: UUID
  trace_id: UUID
  user_id: UUID
  source_artifact_ids: [UUID]
  evidence_ids: [UUID]
  identity:
    relationship_id?: UUID
    case_id?: UUID
    confidence: decimal
    unresolved_candidates: [UUID]
  claims: [ProposedClaim]
  commitments: [ProposedCommitment]
  belief_mutations: [ProposedBeliefMutation]
  conflict_hints: [ConflictHint]
  trigger_mutations: [ProposedTrigger]
  requested_case_transition?: string
  unresolved_questions: [string]
  model:
    provider
    model_id
    prompt_version
```

The proposal **must not** include raw SQL, table names as commands, or permissions.

## 8. Kernel decision pipeline

```text
0. receive proposal
1. validate schema/version
2. derive tenant from authenticated internal principal
3. validate proposal user == principal user
4. load referenced artifact/evidence rows
5. reject any foreign/missing provenance
6. check artifact/proposal dedupe
7. resolve/validate relationship and case identity
8. load current case revision + relevant canonical state
9. validate temporal applicability
10. materialize proposed claims
11. compare against current beliefs/commitments
12. create conflict plan if mutually exclusive
13. compute deterministic fulfillment/amount/status changes
14. evaluate state-machine transition legality
15. evaluate trigger changes
16. evaluate hard invariants
17. begin/continue serializable transaction
18. write claims/evidence links
19. write new belief versions + support edges
20. update conflicts
21. update commitments/fulfillments
22. update case state/revision
23. append state transitions
24. write trigger mutations
25. write kernel_decision
26. update proposal status
27. write outbox event(s)
28. COMMIT
29. on SQLSTATE 40001: rollback/backoff/retry entire operation from fresh reads
30. return KernelCommitResult
```

No network/model call is allowed inside the database transaction.

## 9. Serialization retry contract

CockroachDB may surface retryable `40001` serialization errors.

Persistence rule:

- transaction callback must be side-effect-free outside DB;
- retry starts from fresh reads;
- exponential backoff + jitter;
- cap attempts (recommended 5 for synchronous API path);
- after cap, return `RETRYABLE_CONCURRENCY` and enqueue retry if appropriate;
- metrics record retry count;
- agent is never asked to reason about retry semantics.

Do not reuse computed derived state from a failed transaction without reloading the aggregate.

## 10. Aggregate revision invariant

For every canonical write affecting a case:

```text
new_revision = old_revision + 1
```

All state transitions/outbox events created by that commit use `new_revision` as aggregate version.

If one proposal produces no canonical change, do not increment revision.

## 11. Contradiction algorithm

### 11.1 Candidate detection

Two propositions are conflict candidates when:

- same semantic subject;
- same predicate family;
- validity intervals overlap materially;
- values are mutually exclusive or violate domain rule.

### 11.2 Conflict classes

- `VALUE_CONFLICT`
- `TEMPORAL_CONFLICT`
- `AUTHORITY_CONFLICT`
- `IDENTITY_CONFLICT`
- `COMMITMENT_WITHDRAWAL_CONFLICT`
- `FULFILLMENT_CONFLICT`
- `POLICY_VERSION_CONFLICT`

### 11.3 Auto-resolution

Allowed only if configured rule is deterministic enough.

Example:

- canonical `payment_received=300` backed by bank transaction;
- low-authority support email says `payment_received=0`;
- preserve email claim;
- create conflict;
- retain bank-backed canonical state;
- conflict may be `AUTO_RESOLVED`.

### 11.4 Human review

Required when:

- two high-authority sources conflict;
- identity is ambiguous among multiple active cases;
- legal interpretation would determine an entitlement;
- user directly disputes canonical state;
- a consequential action requires a belief below configured confidence.

## 12. Monetary commitment algorithm

When a new admitted fulfillment amount `F` is applied:

```text
new_fulfilled = old_fulfilled + F
new_outstanding = committed_amount - new_fulfilled
```

Rules:

- reject or flag if currencies mismatch;
- if `new_fulfilled > committed_amount`, do not silently clamp; create anomaly/conflict;
- `new_outstanding > 0` -> `PARTIAL` (unless dispute dominates);
- `new_outstanding == 0` -> `FULFILLED` when no blocking conflict exists;
- any recalculation + fulfillment row + status update + case revision + outbox occurs in one transaction.

## 13. Case transition legality

Allowed core transitions:

```text
OPEN -> WAITING | ACTIONABLE | DISPUTED | BLOCKED | RESOLVED
WAITING -> ACTIONABLE | DISPUTED | BLOCKED | RESOLVED
ACTIONABLE -> IN_PROGRESS | AWAITING_USER | DISPUTED | RESOLVED
IN_PROGRESS -> WAITING | ACTIONABLE | DISPUTED | RESOLVED
DISPUTED -> WAITING | ACTIONABLE | AWAITING_USER | RESOLVED
BLOCKED -> WAITING | ACTIONABLE | RESOLVED
AWAITING_USER -> ACTIONABLE | IN_PROGRESS | RESOLVED
RESOLVED -> REOPENED only on qualifying new evidence/trigger
REOPENED -> WAITING | ACTIONABLE | DISPUTED | RESOLVED
```

`SUPERSEDED` is terminal for a case replaced by another case.

## 14. State Proof query model

State Proof is assembled deterministically.

For a case:

1. load case current state/revision;
2. load active beliefs and current versions;
3. load support edges;
4. load evidence metadata/source locator;
5. load open/resolved conflicts;
6. load commitments/fulfillments;
7. load relevant state transitions;
8. compute deterministic derivations;
9. package into stable response schema.

LLM-generated prose is optional presentation and must not replace the raw proof structure.

## 15. Retrieval data path

### 15.1 Step 1 — deterministic hints

From artifact:

- sender domain/email;
- account/order/booking/case IDs;
- names;
- service address;
- dates;
- currencies/amounts;
- subject/message thread IDs.

Use exact indexed lookups to create candidate relationships/cases.

### 15.2 Step 2 — vector candidate search

Search `evidence_items` scoped by `user_id`.

Use cosine distance with Titan v2 embeddings.

Initial defaults for demo dataset:

- retrieve top 20 vector candidates;
- then rerank/filter to top 6-10 evidence items;
- tune `vector_search_beam_size` only after retrieval evaluation.

Do not hard-code assumptions about production optimum.

### 15.3 Step 3 — relational validation

Each semantic candidate gets a deterministic score/flags using:

- matching external reference;
- same sender/domain;
- relationship status;
- case open/resolved state;
- temporal overlap;
- amount/currency consistency;
- thread/message IDs;
- user-confirmed mapping.

### 15.4 Step 4 — graph expansion

For selected cases, load:

- active beliefs;
- commitments;
- conflicts;
- supporting evidence;
- relevant policy terms;
- recent state transitions.

### 15.5 Step 5 — compact context

Never send all history to the model.

`RetrievalContext` should include:

- max 3 relationship candidates;
- max 3 case candidates;
- current canonical summaries;
- max 10 evidence snippets;
- explicit evidence IDs;
- active conflicts;
- temporal facts;
- unresolved identity questions.

## 16. Embedding lifecycle

### Frozen properties

- model: Titan Text Embeddings V2;
- dimensions: 1024;
- distance: cosine;
- one embedding version active for primary index.

### Text to embed

Use a normalized semantic string, e.g. conceptually:

```text
[type=COUNTERPARTY_CLAIM]
[counterparty=Example ISP]
[date=2026-06-05]
Invoice for service June 1 through June 30. Amount due USD 186.
```

Do not include irrelevant parser JSON or secrets.

### Re-embedding

Store `embedding_model` and `embedding_version` so a future migration can create a parallel index/version rather than mixing incompatible vector spaces.

## 17. Trigger predicate AST

Do not store arbitrary executable code.

Support a small safe predicate grammar:

```text
AND | OR | NOT
EQ | NE | GT | GTE | LT | LTE
IS_NULL | NOT_NULL
FIELD(path)
CONST(value)
```

Whitelisted fields are read from a CaseProjection/CommitmentProjection.

Example:

```text
AND(
  GT(FIELD("commitments.deposit.outstanding_amount"), CONST(0)),
  GTE(FIELD("clock.now"), FIELD("commitments.deposit.due_at"))
)
```

The evaluator is deterministic Python code.

## 18. Approval staleness

An approval is valid only if all are true at execution:

```text
action.status == APPROVED
current_case.revision == action.basis_case_revision
sha256(current_draft) == action.approval_draft_sha256
supporting belief versions are still current or explicitly allowed
idempotency key has no successful execution
```

If any fails:

- transition ActionIntent to `NEEDS_REVIEW` or `CANCELLED_STALE` (add state if desired);
- never send automatically;
- regenerate State Proof/action draft if needed.

## 19. Deletion and retention

v1 implementation:

- raw artifacts retained for demo;
- canonical evidence/belief lineage retained;
- agent scratch/checkpoints can expire;
- parser temporary blobs can expire.

Architecture must reserve a future deletion workflow:

- mark artifact deletion requested;
- remove S3 object;
- tombstone artifact/evidence according to policy;
- recompute beliefs that no longer have support;
- preserve minimal audit where legally permissible.

Do not build full compliance in v1, but do not make it impossible.

## 20. Required database tests

A coding agent must implement tests for at least:

1. duplicate artifact registration is idempotent;
2. belief cannot be canonical without support;
3. contradictory claims create conflict and preserve both;
4. $300 fulfillment against $1,200 commitment yields $900 outstanding atomically;
5. no state can be FULFILLED with outstanding > 0;
6. resolved case reopens on qualifying contradictory evidence;
7. stale ActionIntent approval cannot execute after case revision changes;
8. trigger wakeup after case resolution no-ops;
9. duplicate outbox event processing no-ops;
10. two concurrent Kernel updates on same case serialize/retry without impossible state;
11. cross-user evidence reference in proposal is rejected;
12. vector retrieval query always scopes by user prefix.

## 21. Official references

- Serializable retry behavior: https://www.cockroachlabs.com/docs/stable/transaction-retry-error-reference
- Retry example: https://www.cockroachlabs.com/docs/stable/transaction-retry-error-example
- Vector indexes and prefix columns: https://www.cockroachlabs.com/docs/stable/vector-indexes
- CockroachDB changefeed delivery semantics: https://www.cockroachlabs.com/docs/stable/changefeed-messages
