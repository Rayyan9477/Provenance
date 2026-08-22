# Provenance — Implementation Map

Status: planning-complete implementation baseline v1.1  
Implementation status: not started  
Purpose: remove architectural ambiguity before code generation  
Audience: coding agents, backend/frontend engineers, judges reviewing technical depth

## 1. Frozen decisions

**Rewritten 2026-08-24 for the pivot** from the CockroachDB × AWS hackathon to the
All Things Agentic Hackathon. `CANONICAL_DECISIONS.md` → *Gemini model id canon* is
the binding record; this table is the summary and defers to it on any conflict.

| Concern | Decision | Changed by the pivot |
|---|---|---|
| Product | Provenance: personal system of record for user–institution relationships | no |
| Hero story | "The Move That Never Really Ended" | no |
| Agent framework | **`google-genai` SDK** | yes — was LangGraph |
| Agent hosting | **Cloud Run**, in-process with the control plane | yes — was Bedrock AgentCore Runtime |
| Canonical memory | CockroachDB Cloud | no — and deliberately still on AWS `us-east-1` |
| Agent access to DB | Five `agent_*_v1` views via `pv_agent_reader`; no canonical writes from reasoning agents | no |
| Backend | Python + FastAPI | no |
| Frontend | Next.js + TypeScript | no |
| Authentication | **Identity provider selected by `PV_PLATFORM`**: Google Identity Platform, Cognito, or a disclosed local issuer | yes — was Cognito only |
| Raw artifacts | **Cloud Storage** | yes — was S3 |
| Email ingestion | **Deferred.** Upload `.eml`/PDF/image is the ingestion path for v1 | yes — was SES inbound |
| Document extraction | MIME/parser first; **Gemini native multimodal** for image/PDF | yes — was Textract |
| Event/wakeup | **In-process dispatcher behind a transport Protocol**; Pub/Sub is a later wiring decision | yes — was EventBridge + SQS |
| External action | Draft → human approval → deterministic executor; demo sink only | no |
| Reasoning model (Tier R) | **`gemini-3.7-flash`** | yes |
| Extraction model (Tier E) | **`gemini-3.5-flash-lite`** | yes |
| Tier R fallback | **`gemini-3.6-flash`** — held for *capacity* failure, not capability | yes |
| Embeddings | **`gemini-embedding-2`, 1536 dimensions, version `v2`** | yes — was Titan, 1024, `v1` |
| Database isolation | CockroachDB `SERIALIZABLE`; Kernel retries SQLSTATE `40001` | no |
| Cloud region | Cloud Run in **`us-east4`** | yes |

**There is no Pro reasoning tier, and that is the rules rather than a preference.**
`gemini-3.1-pro-preview` is the only Pro model on the Gemini API and it is version
3.1 — *below* the mandated "3.5 or newer" floor. Both tiers are therefore
Flash-class. Any statement elsewhere in this pack implying a Pro tier is superseded.

**Why the database did not move.** Keeping CockroachDB preserves eight migrations,
twenty-six tables, five agent views, the seed and roughly 390 database and retrieval
tests — the largest block of verified work in the repository. `CREATE VECTOR INDEX`
has no exact pgvector or ScaNN equivalent, so moving would mean re-deriving the
vector strategy under deadline. The hackathon requires *at least one* Google Cloud
infrastructure service, and Cloud Run satisfies that on its own. `us-east4` and AWS
`us-east-1` are both Northern Virginia, so the cross-cloud hop stays in single-digit
milliseconds.

**Every Gemini id above is UNPROBED.** All are transcribed from documentation and
none has been invoked. The previous canon was frozen the same way and *every one of
its four ids turned out to be un-invocable* — `list-foundation-models` returns ids
that cannot be called, which is the trap `D-00-002` fell into.
`ops/probes/gemini_probe.py` is what settles them; until its transcript exists these
are candidates, not decisions.

## 2. The architecture in one sentence

> Untrusted evidence is interpreted by Gemini agents into typed proposals; a deterministic Memory Kernel validates those proposals and atomically commits versioned beliefs, obligations, state transitions, and outbox records in CockroachDB; asynchronous workers then react to committed state under explicit human authorization.

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
- Interpreter graph (`google-genai`)
- Advocate graph (`google-genai`)
- optional Resolver node/subgraph
- event dispatcher
- trigger evaluator
- observability/evaluation

### 4.2 Hackathon deployment units

Use only these deployment units initially:

Rewritten 2026-08-24. The pivot **reduced** this list, which is the main reason
the deploy phase got cheaper rather than more expensive.

1. **`web`** — Next.js UI on **Cloud Run**.
2. **`control-plane`** — one FastAPI container on **Cloud Run**, containing API,
   retrieval, Memory Kernel, State Proof, action-policy logic, the agent runtime and
   internal tool endpoints.
3. **`workers`** — trigger wakeups and outbox sweeping/dispatch. In-process behind a
   transport Protocol for the hackathon build; a separate deployment unit only if the
   demo shows it needs one.
4. **CockroachDB Cloud** — canonical memory plane, on AWS `us-east-1`.
5. **Cloud Storage** — raw artifact bytes.
6. **Gemini Developer API** — model inference. Not a Cloud infrastructure service, so
   it does **not** satisfy the hackathon's infrastructure requirement; Cloud Run does.

`agent-runtime` is no longer a separate deployment unit. It was one because AgentCore
Runtime was a distinct hosting product; with the `google-genai` SDK the agent layer is
a library the control plane imports, and it still holds no canonical write credential
because that boundary is enforced by SQL grants and `tools/write_path_lint.py`, not by
process separation.

This is intentionally a **modular monolith + async workers**, not a microservice zoo.
It minimizes deployment and network failure modes while preserving clear module
boundaries in code — and the boundary that matters most, the single canonical writer,
was never a process boundary in the first place.

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
├── workers/                          # ses_ingest/ and textract_complete/ are
│   ├── outbox_dispatch/              # retired with the pivot: SES ingestion is
│   └── trigger_wakeup/               # deferred and Gemini reads images natively
│
├── packages/
│   └── python/
│       ├── provenance_contracts/     # Pydantic schemas shared by backend/agents/workers
│       ├── provenance_domain/        # enums, state machines, invariant functions
│       ├── provenance_db/            # DB pool, repositories, transaction retry
│       └── provenance_telemetry/     # trace IDs / OTEL helpers
│
├── db/
│   ├── migrations/                   # Alembic 0001..0008
│   ├── seeds/                        # MANIFEST.json
│   └── verify.sql                    # V1..V11 post-migration verification queries
│
├── scripts/
│   └── seed/                         # ids.py (sid), decoys.py, embeddings.py
│
├── infra/
│   ├── cdk/                          # AWS CDK -- DISCARDED with the pivot; retained only as a record
│   └── agentcore/                    # RETIRED with the pivot
│
├── tests/                            # cross-package suites; per-package tests live beside their package
│   ├── retrieval/                    # L5
│   ├── e2e/                          # L6
│   └── support/                      # shared helpers only, contains NO tests
│
├── evals/
│   ├── datasets/                     # memory_cases.jsonl (51), injection_corpus.jsonl, schema/
│   ├── decoys/
│   ├── fixtures/model/               # recorded cassettes
│   ├── runner/                       # __main__.py, modes.py, assertions.py, scoring.py, report.py
│   ├── reports/                      # generated *.json
│   ├── memory/  retrieval/  extraction/  adversarial/
│
├── tools/                            # gate.sh, scrub.py, write_path_lint, txn_purity_lint,
│                                     # fixture_guard, invariant_map_check
│
├── ops/                              # execution evidence — committed, gitleaks-scanned
│   ├── gate-env.sh
│   ├── cluster-provision.txt
│   ├── probes/                       # phase0-probe.sh / .ps1
│   ├── cluster-probe.txt  grant-probe.txt  bedrock-probe.txt  restore-probe.txt
│   ├── decisions/                    # VECTOR_INDEX_VARIANT.md
│   ├── gates/                        # PHASE_00.md .. PHASE_15.md, SUBMISSION.md, logs/
│   └── defects/                      # DEFECTS.md
│
├── demo/
│   └── artifacts/                    # the hero .eml and PDF files, real bytes, real hashes
│
├── pyproject.toml   .importlinter   Makefile   .coveragerc
├── LICENSE   NOTICE                  # Apache-2.0
│
└── docs/
```

### 5.1 Layout authority and the four trees it reconciles

**This tree is authoritative.** Four documents previously specified four different layouts, and one of them contradicted this one outright. Resolved 2026-08-17, recorded in `CANONICAL_DECISIONS.md` → *Repository layout canon*:

| Source | Status |
|---|---|
| `implementation/00_IMPLEMENTATION_MAP.md` §5 (this tree) | **Authoritative.** |
| `ARCHITECTURE.md` §25 | **Superseded.** Specified a microservice tree — five `services/*`, three `agents/*`, no `workers/` — that §4.2 of this document explicitly rejects. Marked superseded in place. |
| `submission/50_README_DRAFT.md` | Merged in: `ops/`, `tools/`, `tests/`, `db/verify.sql`. |
| `quality/20_TDD_STRATEGY.md` §3.3 | Merged in: `pyproject.toml`, `.importlinter`, `Makefile`, `.coveragerc`, `tests/{retrieval,e2e,support}`, `evals/fixtures/model/`. |
| `quality/22_EVAL_DATASETS.md` §1.4 | Merged in: `evals/{decoys,runner,reports}`, `datasets/schema/`. |

Two directories were referenced by working documents but appeared in no tree, and are now placed: **`scripts/seed/`** (`41_RUNBOOK.md` calls `scripts/seed/embeddings.py`; `20_TDD_STRATEGY.md` imports `from scripts.seed.ids import sid`) and **`demo/artifacts/`** (referenced three times in `20_TDD_STRATEGY.md` for the real hero `.eml` and PDF bytes). `demo/artifacts/` **replaces** the earlier `demo_data/the_move/` and `db/demo/`; there is now one location for hero artifact bytes, not three.

Test placement rule: per-package tests live beside their package (`packages/python/*/tests/`, `services/control_plane/tests/`, `agents/runtime/tests/`). Only genuinely cross-package suites live in the top-level `tests/`. A test that imports from exactly one package belongs next to that package.

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
       +-------------------+--> Gemini agents (google-genai)
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
- **Do not use any agent SDK's session or memory abstraction as product memory.**
  This previously named AgentCore Memory and the LangGraph Store; after the pivot it
  binds the `google-genai` SDK's and ADK's session and memory abstractions equally.
  The ban is restated for the new stack rather than assumed to carry over, because a
  rule phrased against a library nobody uses any more is a rule nobody applies.
- Session state is workflow durability and HITL recovery only. If canonical truth is
  ever read from an SDK session rather than from the database, the
  single-canonical-writer guarantee is gone and nothing reports it.
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
- Gemini API models: https://ai.google.dev/gemini-api/docs/models
- Gemini API embeddings: https://ai.google.dev/gemini-api/docs/embeddings
- Gemini API rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Cloud Run: https://cloud.google.com/run/docs

Retired with the pivot, retained so a reader can find what a superseded decision
pointed at: AgentCore Runtime, Amazon SES email receiving, EventBridge Scheduler,
and Titan Text Embeddings V2.
