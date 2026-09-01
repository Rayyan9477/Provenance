# Provenance — architecture diagrams

Mermaid source. GitHub renders it inline; `docs/diagrams/README.md` covers other
renderers and the conventions these diagrams follow.

**Read this first.** These diagrams distinguish three states and never blur them:

| Badge | Meaning |
|---|---|
| `BUILT` | Exists in this tree, has tests, and the tests run. |
| `IN FLIGHT` | Partly in the tree; the named gaps are listed under the diagram. |
| `NOT BUILT` | Does not exist. Drawn only where omitting it would misrepresent the shape of the system. |

Nothing carries `BUILT` that this repository cannot demonstrate. A diagram that
claims unbuilt capability is exactly the dishonesty this project's gate system
exists to prevent, so the badges are part of the diagram, not decoration around
it.

Authorities, in precedence order: `docs/CANONICAL_DECISIONS.md` (frozen; it
outranks every other document), `docs/implementation/00_IMPLEMENTATION_MAP.md`
§2/§4/§5, `docs/00_PRODUCT.md` §0–§2, `docs/ARCHITECTURE.md` §7/§9/§11/§17/§22,
`STATUS.md`, `PIVOT.md`.

---

## 1. The system, and the one door into canonical memory

The single most important claim in this architecture: **the deterministic Memory
Kernel is the only thing that writes canonical state.** Agents propose typed
`MemoryProposal` objects. No agent holds a SQL write credential — not a
restricted one, not a scoped one, none. That is enforced twice: by the SQL grants
in migration `0008` at runtime, and by `tools/write_path_lint.py` at review time.

In the diagram below there is exactly **one** thick arrow into the database.
Every other path into it is a dotted read. The two crossed arrows are refusals,
not omissions.

```mermaid
flowchart TB

  classDef built     fill:#e8f5e9,stroke:#2e7d32,color:#14371a
  classDef inflight  fill:#e3f2fd,stroke:#1565c0,color:#0b2f57
  classDef planned   fill:#fff8e1,stroke:#f9a825,color:#5c4400,stroke-dasharray: 6 4
  classDef kernel    fill:#ffebee,stroke:#c62828,stroke-width:4px,color:#6d0000
  classDef store     fill:#ede7f6,stroke:#5e35b1,stroke-width:2px,color:#2c1465
  classDef untrusted fill:#fbe9e7,stroke:#d84315,color:#5c1a06

  IN["Untrusted inbound<br/>forwarded email, uploaded PDF<br/>bytes are never edited; identity is content_sha256"]

  subgraph WEB["web - Next.js 15 - target Cloud Run"]
    UI["14 screens: State Proof, approvals, Judge Mode<br/>BUILT - 74 tests, render-honesty checker<br/>runs in FIXTURE mode with a permanent banner<br/>until PV_API_BASE_URL is set"]
  end

  subgraph AGENTS["agent-runtime - google-genai SDK - target Cloud Run"]
    INTERP["Interpreter - ingestion_graph<br/>Tier E gemini-3.5-flash-lite<br/>classify, extract, emit MemoryProposal<br/>BUILT - no write tool exists to call"]
    RESOLVE["Resolver - invoked only on ambiguity<br/>Tier R gemini-3.7-flash<br/>BUILT"]
    ADVOCATE["Advocate - advocate_graph<br/>Tier R gemini-3.7-flash<br/>reads State Proof, drafts, cites<br/>BUILT"]
  end

  subgraph CP["control-plane - one FastAPI container - target Cloud Run"]
    API["API and auth<br/>capability-typed Principal, never built from request data<br/>BUILT - ports bound to the database, one pool per SQL role<br/>starts even when the pool is refused, reporting db_ok false"]
    RETR["Retrieval - eight stages A to H<br/>A scope, B identity, C temporal, D vector ANN,<br/>E relational, F grounding, G rerank, H context<br/>IN FLIGHT - every stage is built and the ANN<br/>statement is proved; NO EXECUTOR composes them,<br/>so internal.retrieve is unbound"]
    KERNEL["MEMORY KERNEL<br/>deterministic: no model call, no network call<br/>validate, reconcile, enforce invariants<br/>THE ONLY CANONICAL WRITER<br/>BUILT"]
    SPROOF["State Proof builder<br/>grounding edges plus version lineage<br/>BUILT"]
    ACTION["Action policy, intents and executor<br/>bind to case revision plus draft sha256, revalidate, execute once<br/>BUILT - see diagram 4"]
  end

  subgraph GEM["Gemini Developer API - AI Studio key"]
    MODELS["gemini-3.7-flash - Tier R<br/>gemini-3.5-flash-lite - Tier E<br/>gemini-3.6-flash - capacity fallback<br/>gemini-embedding-2 at 1536 dims<br/>BUILT - every id PROBED by invocation<br/>ops/gemini-probe.txt PASS 11 FAIL 0 CANNOT RUN 0"]
  end

  DB[("CockroachDB Cloud<br/>canonical memory plane<br/>26 tables, 5 agent views, 5 SQL roles<br/>18035 evidence rows seeded<br/>BUILT")]

  OUTBOX["Outbox dispatcher and trigger scheduler<br/>outbox_events is written inside the same transaction<br/>BUILT - dispatcher, consumer and trigger predicate DSL<br/>262 tests passing"]

  IN --> API
  API --> INTERP
  RETR -. "advisory context - read only" .-> INTERP
  INTERP --> RESOLVE
  INTERP ==> |"typed MemoryProposal - the ONLY way in"| KERNEL
  RESOLVE ==> |"typed MemoryProposal"| KERNEL

  INTERP -. "google-genai" .-> MODELS
  RESOLVE -. "google-genai" .-> MODELS
  ADVOCATE -. "google-genai" .-> MODELS
  RETR -. "embed BEFORE the transaction opens" .-> MODELS

  KERNEL ==> |"ONE SERIALIZABLE transaction as pv_kernel_writer - retried on SQLSTATE 40001"| DB

  DB -. "SELECT as pv_app_reader_writer" .-> RETR
  DB -. "SELECT as pv_app_reader_writer" .-> SPROOF
  DB -. "SELECT as pv_app_reader_writer" .-> API
  DB -. "SELECT on 5 views only - pv_agent_reader" .-> INTERP
  DB -. "SELECT on 5 views only - pv_agent_reader" .-> ADVOCATE

  SPROOF -. "State Proof" .-> ADVOCATE
  API -. "HTTPS - PV_API_BASE_URL" .-> UI
  ADVOCATE --> ACTION
  DB --> OUTBOX
  OUTBOX -. "re-drive through the API - never a direct write" .-> API

  INTERP -. "no SQL write credential exists to hold" .-x DB
  ADVOCATE -. "no SQL write credential exists to hold" .-x DB
  UI -. "the browser never reaches the database" .-x DB

  class UI,SPROOF,INTERP,RESOLVE,ADVOCATE,ACTION built
  class OUTBOX,MODELS,API built
  class RETR inflight
  class KERNEL kernel
  class DB store
  class IN untrusted
```

### What that diagram claims, and what backs each claim

| Claim | How it is checked |
|---|---|
| The Kernel is the only canonical writer | `python -m tools.write_path_lint` — measured 2026-08-27: *5 rules, 0 violations; 27 canonical write statements, 19 of them in the Kernel; `agents/` 0, `workers/` 0, `apps/web/` 0, `packages/` 0*. The Kernel's nineteen are **named** in `memory_kernel/transaction.CANONICAL_WRITE_STATEMENTS`, so the count is a claim about specific statements rather than a constant nobody re-measures. The other eight are app-role writes that a rule permits **by name**: six `UPDATE outbox_events` in `app/events/dispatcher.py` — claim, mark-dispatched, mark-failed, mark-dead, reclaim-lease, replay — under `W5-outbox-UPDATE-is-dispatcher-permitted`, because they move a dispatch status field on an already-committed event rather than canonical epistemic state; and the evidence and proposal `INSERT`s in `app/ingestion` and `app/proposals` under `W4-evidence-and-proposal-INSERT-is-app-permitted`. (`processed_events` is **not** in the linter's `CANONICAL_TABLES`; it belongs to `pv_app_reader_writer` outright, so `consumer.py`'s update is not counted at all.) Re-run it rather than quoting the number: it moves as the ingestion path grows. |
| The agent layer holds no write capability | `agents/runtime/tests/test_no_write_tools.py` — enumerates the tool protocols, walks the AST of every shipped agent module for canonical write vocabulary and for the two writer SQL roles, and runs 12 injected adversarial artifacts end to end asserting *kernel commits caused: 0, action intents created: 0, scopes escalated: 0* — with a positive control that every injected artifact is still admitted as evidence, because silently dropping the text would break the append-only invariant while looking like a pass. |
| Agents cannot write, by grant rather than by prompt | Migration `0008` issues `GRANT SELECT` on the five `agent_*_v1` views to `pv_agent_reader`, then `REVOKE ALL ON TABLE <all 26> FROM pv_agent_reader`. |
| The commit is one transaction | `services/control_plane/app/memory_kernel/transaction.py` — `SERIALIZABLE`, bounded retry on SQLSTATE `40001`. |
| No model or network call inside that transaction | `python -m tools.txn_purity_lint services packages workers`, wired into `make lint`. |
| Vector search is stage D, not stage A | `STAGES` in `services/control_plane/app/retrieval/pipeline.py`, asserted by a test rather than left as a comment. |
| Every invariant names a test that actually runs | `python -m tools.invariant_map_check packages/python/provenance_domain/INVARIANTS.md` — *5 invariants, 5 mapped, 0 UNPROVEN* — provided the workspace packages are installed (`make bootstrap`); an editable install pointing at a stale path makes every invariant read UNPROVEN, which is a broken environment and not a regression. |

### The honest gaps in diagram 1

- ~~**The agent layer's model calls are unexercised.**~~ **Closed 2026-08-24.**
  `agents/runtime/` is ~11,600 lines across ingestion, advocate and
  counterfactual graphs, the resolver, the Gemini model router and typed
  schemas, with **310 tests**. Three artifacts have been walked against live
  Gemini endpoints, both tiers invoked, leaving **31 `agent_runs` rows** where
  there were zero — each carrying `model_calls[]` attribution
  (`ops/agent-graph-live-run.txt`). A live Judge Mode counterfactual is recorded
  in `ops/counterfactual-live-run-2.txt` with `parity.all_equal = true` and all
  ten case revisions unmoved.

  Three defects only a live call could have found are in the ledger, and the
  sharpest is worth stating here: **`ExtractionResult` could not be sent to
  Gemini at all** — `types.Schema` is `extra="forbid"` and rejects the `ge`/`le`
  and `prefixItems` the contract emits. The 252-test router suite was green
  because every test sent a `ToyOutput` defined in the test file. Not an
  assertion that checks nothing: an entire suite checking a stand-in for the
  thing under test.

- **Retrieval is stages without a pipeline.** `app/retrieval/` holds all eight
  stages and they are good — `ann.py` parses the canonical predicate out of the
  spec markdown so the SQL cannot drift from the document — but
  `pipeline.py` is a `STAGES` tuple and a `call_order()` function, and **no
  module runs A through G end to end**. That is why `internal.retrieve` is
  unbound, and why the Judge Mode counterfactual currently passes an empty
  `evidence` array to its MEMORY ON side. The badge says `IN FLIGHT` for that
  reason and not as a hedge.
- **The event plane went green while this file was being written.** The trigger
  predicate parser had 19 failing tests an hour before this edit and now has
  none: `services/control_plane/tests/events` is 262 passing, `actions` 74,
  `agents/runtime` 310. Treat every count in this document as a timestamp rather
  than a fact, and re-run the command instead of quoting the number.
- ~~**The Gemini model ids are UNPROBED.**~~ **Settled 2026-08-24.** Every id
  above was *invoked*, not listed: `PASS 11 | FAIL 0 | CANNOT RUN 0`, exit 0,
  transcript at `ops/gemini-probe.txt`. `client.models.list()` appears in that
  transcript under an explicit `REFERENCE ONLY, NOT PROOF` heading, because
  enumeration is not invocation and listing is the trap the previous model canon
  fell into — where all four frozen ids turned out to be un-invocable.

  Two of this probe's own first-run verdicts were wrong and **both were defects
  in the probe**: `D-00-046` recorded PASS for three ids that answered nothing
  (`max_output_tokens` is one allowance shared with thinking, so the budget was
  spent before the first visible token), and `D-00-047` recorded a capability
  FAIL that was a 1×1 transparent PNG the API rejects — an 8×8 solid one of the
  same 75 bytes succeeds. Acting on that FAIL would have kept an external OCR
  dependency on the evidence of one transparent pixel.
- **The vectors in `evidence_items` are Titan vectors at 1024 dimensions**, not
  Gemini vectors at 1536. Migration `0009_gemini_embedding_plane` widens the
  column to `VECTOR(1536)`, repoints both model `CHECK` constraints to the
  Gemini ids, and **nulls every embedding** — a 1024-dimension vector is not a
  truncation of a 1536-dimension one. The rows, their text, their hashes and
  their grounding edges survive; the vectors do not. The Gemini re-embed that
  refills them is in flight, and until it lands retrieval returns nothing,
  because the canonical query filters `embedding_version = 'v2'`.
- **Deployed to Cloud Run and serving.** Two services in `provenance-agentic-2026` / `us-east4`, both `Ready`. `GET /v1/version` reports `fixture_mode: false` and `db_ok: true`; an unauthenticated read is refused with `401`; the dashboard renders `USD 2,020.00` summed by the API from Kernel-written rows.
  `deploy/README.md` is the runbook and `deploy/cloudrun.sh up` reproduces it.
- **A running control plane does not imply a reachable database.**
  `build_dependencies()` no longer raises, and `make run-api` starts a real
  server — but startup deliberately survives a refused pool and reports
  `db_ok: false` on `GET /v1/version` rather than crash-looping. A `200` from
  that endpoint is therefore not evidence of a database; the `db_ok` field is.

---

## 2. The data spine — the six-way separation

This is the idea. Ordinary retrieval-augmented generation has one level of
representation — the chunk — and resolves a contradiction by cosine similarity.
Provenance keeps six levels apart: each is a distinct table, a distinct
lifecycle, and a distinct authority to change things.

All six levels are `BUILT` in the schema: migrations `0001`–`0008`, 26 tables.
Migration `0009` widens the evidence vector for Gemini and is authored but not
yet paired with a re-embed.

```mermaid
flowchart LR

  classDef lvl fill:#ede7f6,stroke:#5e35b1,color:#2c1465

  A["1 ARTIFACT<br/>source_artifacts<br/>immutable bytes, content_sha256<br/>the same forward twice is one artifact"]
  E["2 EVIDENCE<br/>evidence_items<br/>append-only, span-anchored observations<br/>carries the embedding and retraction_status"]
  C["3 CLAIM<br/>claims<br/>WHO asserted it, and in what capacity<br/>the level ordinary RAG cannot represent"]
  B["4 BELIEF<br/>beliefs, belief_versions, belief_support<br/>lineage is the version chain<br/>grounding is the SUPPORTS CONTRADICTS QUALIFIES edges"]
  M["5 COMMITMENT<br/>commitments, fulfillments<br/>outstanding equals committed minus fulfilled<br/>computed in Python, enforced by a CHECK constraint"]
  S["6 STATE<br/>cases, state_transitions, conflicts, outbox_events<br/>the transactional projection an application may act on"]

  A --> E --> C --> B --> M --> S

  class A,E,C,B,M,S lvl
```

The worked example, from `docs/00_PRODUCT.md` §2.3: an ISP invoice for USD 186
arriving four months after the same ISP confirmed termination in writing does
**not** make USD 186 owed. It makes USD 186 *claimed*, by a party with a
financial interest, covering a period that begins after a termination that
party's own documents establish. Every one of those qualifiers is a column, not
a sentence in a summary.

The consequence is that `balance_owed` gets a new belief version whose **value is
unchanged at USD 0** and whose **`epistemic_status` moves `CONFIRMED` to
`DISPUTED`**. Prose cannot express that. A version chain plus grounding edges
can, and the `conflicts` row that records it is queryable, countable and
displayable rather than implicit.

---

## 3. The five SQL roles, ordered by privilege

<!--
  Five, not four. The heading said four and the section then listed five. The
  four comes from `RUNTIME_ROLES` in migration 0008, which counts the roles a
  RUNNING process may hold and therefore excludes `pv_migrator` -- correctly,
  since nothing serving a request may migrate. But README.md, STATUS.md and
  `pools.SqlRole` all say five, and this section lists five, so the heading was
  the only thing disagreeing.
-->

The permission boundary is the grant, not the prompt. `pv_agent_reader` is not
asked nicely to stay out of the base tables; it holds zero grants on them.

```mermaid
flowchart TB

  classDef ddl   fill:#fbe9e7,stroke:#d84315,color:#5c1a06
  classDef write fill:#ffebee,stroke:#c62828,stroke-width:3px,color:#6d0000
  classDef app   fill:#e8f5e9,stroke:#2e7d32,color:#14371a
  classDef ro    fill:#e3f2fd,stroke:#1565c0,color:#0b2f57

  MIG["pv_migrator<br/>DDL only. Owns every table.<br/>NEVER used at runtime - migrations and reset only.<br/>Holds no CREATEROLE on this cluster."]
  KW["pv_kernel_writer<br/>The ONLY role that writes canonical tables.<br/>INSERT and UPDATE on beliefs, cases, commitments, conflicts.<br/>INSERT-only on claims, belief_support, state_transitions, outbox_events.<br/>Explicitly REVOKEd from action_intents."]
  ARW["pv_app_reader_writer<br/>SELECT on all 26 tables.<br/>INSERT and UPDATE on identity, artifacts, action and idempotency tables.<br/>INSERT-only on evidence_items, memory_proposals, processed_events.<br/>Cannot touch beliefs, claims, conflicts or commitments."]
  AGR["pv_agent_reader<br/>SELECT on FIVE agent views. Nothing else.<br/>REVOKE ALL ON TABLE all-26 - zero table grants.<br/>THIS is the agent boundary."]
  OPS["pv_ops_reader<br/>Strictly read-only. Operator and CI credential.<br/>SELECT on the 5 views plus 11 operational tables.<br/>No INSERT, UPDATE or DELETE anywhere."]

  VIEWS["agent_case_context_v1<br/>agent_active_beliefs_v1<br/>agent_belief_lineage_v1<br/>agent_evidence_retrieval_v1<br/>agent_open_obligations_v1<br/>the views apply tenant, user and retraction_status ACTIVE filters"]
  TABLES["26 canonical tables"]

  MIG ==> |"CREATE ALTER GRANT - migrations only"| TABLES
  KW  ==> |"the canonical write path"| TABLES
  ARW --> |"scoped writes: artifacts, evidence, proposals, action plane"| TABLES
  ARW -. "SELECT" .-> TABLES
  OPS -. "SELECT - read-only forever" .-> TABLES
  AGR -. "SELECT" .-> VIEWS
  VIEWS -. "defined over" .-> TABLES
  AGR -. "REVOKE ALL - no grant exists to use" .-x TABLES

  class MIG ddl
  class KW write
  class ARW app
  class AGR,OPS,VIEWS ro
```

Privilege ordering, strongest to weakest, with the rule that goes with each:

1. **`pv_migrator`** — DDL only. Owns the tables. Never appears in a runtime
   connection string.
2. **`pv_kernel_writer`** — the only role permitted to write canonical epistemic
   and state tables. Held by exactly one module.
3. **`pv_app_reader_writer`** — reads everything; writes only the non-canonical
   surface: identity, artifacts, evidence admission, proposals, the action plane.
4. **`pv_agent_reader`** — `SELECT` on five views, zero table grants.
5. **`pv_ops_reader`** — read-only operator credential. Listed for completeness
   because it exists in migration `0008`, and because an undocumented role is a
   role nobody revokes.

Source: `db/migrations/versions/0008_events_infrastructure.py`, constants
`RUNTIME_ROLES`, `AGENT_VIEWS`, `CANONICAL_TABLES` and `GRANT_DDL`.

---

## 4. The human-approval gate on external actions — `BUILT`

`services/control_plane/app/actions/` is ~3,100 lines across nine modules:
`policy`, `drafts`, `intents`, `executor`, `support_validation`, `sink`, and two
stores. `basis_case_revision`, `draft_sha256`, revalidation and idempotency all
appear in the implementation, not only in the contract.

The sequence below is the frozen contract from `docs/CANONICAL_DECISIONS.md` →
*Memory, action, and time* → *External action*. The schema encodes the same
commitment: `action_intents` and `action_executions` are two of the 26 tables,
and `pv_kernel_writer` is explicitly **revoked** from `action_intents` so the
Kernel itself cannot arm an action.

What is **not** proved: no intent has been executed against a real external
sink. `sink.py` exists and the demo-safe path is the intended one, but an
end-to-end send has not been demonstrated.

```mermaid
sequenceDiagram
    autonumber
    participant AD as Advocate agent
    participant PO as Action policy
    participant HU as Human
    participant EX as Executor
    participant DB as CockroachDB
    participant EXT as External sink

    Note over AD,EXT: BUILT - not yet exercised against a real external sink.

    AD->>PO: draft reply, every factual sentence carrying a belief_version_id
    PO->>DB: validate grounding of every cited belief version
    DB-->>PO: grounding edges plus current case revision
    PO->>DB: create ActionIntent - status PROPOSED
    Note right of PO: a PROPOSED intent can produce no side effect
    PO-->>HU: render the draft and its evidence references
    HU->>PO: edit if desired, then approve explicitly
    PO->>DB: bind approval to case revision AND sha256 of the approved draft
    Note right of DB: the approval is for THOSE bytes at THAT revision
    EX->>DB: revalidate - has the case moved since approval?
    alt case revision changed or draft sha256 differs
        DB-->>EX: refuse
        EX->>DB: intent stays unexecuted, reason recorded
    else still valid
        EX->>EXT: execute once, under an idempotency key
        EXT-->>EX: provider correlation id
        EX->>DB: record execution, correlation id, outcome
        Note over EX,DB: the outcome re-enters the pipeline as new evidence
    end
```

Why revalidation exists: an approval not bound to a specific `case.revision` and
a specific `sha256` approves a *situation*, and situations move. Binding to both
lets the executor prove that the thing it is about to send is the thing a human
read.

---

## 5. What is deployed

**Deployed.** Two Cloud Run services in `provenance-agentic-2026` / `us-east4`:

```
web            https://provenance-web-vaq74wztva-uk.a.run.app
control plane  https://provenance-control-plane-vaq74wztva-uk.a.run.app
```

Nine secrets in Secret Manager, every one mounted by reference rather than
inlined into a revision spec. The control plane reaches the seeded CockroachDB
cluster on AWS `us-east-1` from GCP `us-east4` — the same physical metro, which
is why that region was chosen.

Note the shape: `agent-runtime` is **not** a third Cloud Run service. It runs
in-process inside the control plane, which is what a modular monolith means
here, and is why there are two containers rather than four.

```mermaid
flowchart LR

  classDef planned fill:#fff8e1,stroke:#f9a825,color:#5c4400,stroke-dasharray: 6 4
  classDef built   fill:#e8f5e9,stroke:#2e7d32,color:#14371a
  classDef image   fill:#e3f2fd,stroke:#1565c0,color:#0b2f57

  DEV["Developer workstation<br/>Python 3.12, Node 20+, GNU Make 3.82+<br/>BUILT - this is where everything currently runs"]
  IMG1["image: control-plane<br/>python:3.12-slim, linux/amd64, non-root<br/>BUILT - runs locally, 200 on /v1/healthz,<br/>401 on an unauthenticated read"]
  IMG2["image: web<br/>node:20-slim, Next standalone output<br/>BUILT - runs locally, serves the dashboard"]
  CR1["Cloud Run - control-plane<br/>agent-runtime runs IN-PROCESS here<br/>DEPLOYED and serving - db_ok true"]
  CR2["Cloud Run - web<br/>DEPLOYED - LIVE, no fixture banner"]
  SM["Secret Manager<br/>9 secrets, all mounted by reference<br/>CREATED"]
  CRDB[("CockroachDB Cloud - AWS us-east-1<br/>BUILT - migrated and seeded, 18035 evidence rows")]
  GAPI["Gemini Developer API<br/>BUILT - PROBED, 11 PASS, and both tiers<br/>invoked by the agent graphs"]

  DEV --> CRDB
  DEV --> GAPI
  DEV ==> |"make deploy-images"| IMG1
  DEV ==> |"make deploy-images"| IMG2
  IMG1 ==> |"deploy/cloudrun.sh up"| CR1
  IMG2 ==> |"deploy/cloudrun.sh up"| CR2
  SM ==> |"secretKeyRef"| CR1
  CR1 ==> |"sslmode=verify-full"| CRDB
  CR1 ==> |"google-genai"| GAPI

  class CR1,CR2,SM built
  class IMG1,IMG2 image
  class DEV,CRDB,GAPI built
```

The prior build's ten AWS CDK stacks — 170 resources, 304 synthesis tests — are
discarded by the pivot and are not drawn. They synthesised; they were never
deployed either. The *reasoning* inside them (least privilege, immutable image
tags, alarms with `treatMissingData` set so a quiet system reports `OK` rather
than nothing) is what should carry across to the Google Cloud equivalent, rather
than being rediscovered.

---

## Sources

- `docs/CANONICAL_DECISIONS.md` — the frozen register; outranks every other document
- `docs/implementation/00_IMPLEMENTATION_MAP.md` §2, §4, §5
- `docs/00_PRODUCT.md` §0, §1, §2
- `docs/ARCHITECTURE.md` §7, §9, §11, §17, §22 — pre-pivot in its AWS specifics; the service boundaries survive the pivot
- `db/migrations/versions/0008_events_infrastructure.py` — roles, grants, views
- `services/control_plane/app/` — the code the badges describe
- `STATUS.md`, `PIVOT.md` — measured build state
