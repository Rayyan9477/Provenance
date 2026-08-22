# Provenance — architecture diagrams

Submission artifact for the **All Things Agentic Hackathon** (deadline
2026-08-31, 17:00 PDT).

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
    UI["14 screens: State Proof, approvals, Judge Mode<br/>BUILT - 65 tests, render-honesty checker<br/>runs in FIXTURE mode with a permanent banner<br/>until PV_API_BASE_URL is set"]
  end

  subgraph AGENTS["agent-runtime - google-genai SDK - target Cloud Run"]
    INTERP["Interpreter - ingestion_graph<br/>Tier E gemini-3.5-flash-lite<br/>classify, extract, emit MemoryProposal<br/>BUILT - no write tool exists to call"]
    RESOLVE["Resolver - invoked only on ambiguity<br/>Tier R gemini-3.7-flash<br/>BUILT"]
    ADVOCATE["Advocate - advocate_graph<br/>Tier R gemini-3.7-flash<br/>reads State Proof, drafts, cites<br/>BUILT"]
  end

  subgraph CP["control-plane - one FastAPI container - target Cloud Run"]
    API["API and auth<br/>capability-typed Principal, never built from request data<br/>BUILT - ports bound to the database, one pool per SQL role<br/>starts even when the pool is refused, reporting db_ok false"]
    RETR["Retrieval - eight stages A to H<br/>A scope, B identity, C temporal, D vector ANN,<br/>E relational, F grounding, G rerank, H context<br/>BUILT - vector is stage D, never stage A"]
    KERNEL["MEMORY KERNEL<br/>deterministic: no model call, no network call<br/>validate, reconcile, enforce invariants<br/>THE ONLY CANONICAL WRITER<br/>BUILT"]
    SPROOF["State Proof builder<br/>grounding edges plus version lineage<br/>BUILT"]
    ACTION["Action policy, intents and executor<br/>bind to case revision plus draft sha256, revalidate, execute once<br/>BUILT - see diagram 4"]
  end

  subgraph GEM["Gemini Developer API - AI Studio key"]
    MODELS["gemini-3.7-flash - Tier R<br/>gemini-3.5-flash-lite - Tier E<br/>gemini-3.6-flash - capacity fallback<br/>gemini-embedding-2 at 1536 dims<br/>IN FLIGHT - these ids are UNPROBED"]
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

  class UI,RETR,SPROOF,INTERP,RESOLVE,ADVOCATE,ACTION built
  class OUTBOX built
  class API,MODELS inflight
  class KERNEL kernel
  class DB store
  class IN untrusted
```

### What that diagram claims, and what backs each claim

| Claim | How it is checked |
|---|---|
| The Kernel is the only canonical writer | `python -m tools.write_path_lint` — measured on this tree: *5 rules, 0 violations; 23 canonical write statements, 17 of them in the Kernel; `agents/` 0, `workers/` 0, `apps/web/` 0, `packages/` 0*. The Kernel's seventeen are **named** in `memory_kernel/transaction.CANONICAL_WRITE_STATEMENTS`, so the count is a claim about specific statements rather than a constant nobody re-measures. The other six are all `UPDATE outbox_events` in `app/events/dispatcher.py` — claim, mark-dispatched, mark-failed, mark-dead, reclaim-lease, replay — permitted by rule `W5-outbox-UPDATE-is-dispatcher-permitted`, because they move a dispatch status field on an already-committed event rather than canonical epistemic state. (`processed_events` is **not** in the linter's `CANONICAL_TABLES`; it belongs to `pv_app_reader_writer` outright, so `consumer.py`'s update is not counted at all.) |
| The agent layer holds no write capability | `agents/runtime/tests/test_no_write_tools.py` — enumerates the tool protocols, walks the AST of every shipped agent module for canonical write vocabulary and for the two writer SQL roles, and runs 12 injected adversarial artifacts end to end asserting *kernel commits caused: 0, action intents created: 0, scopes escalated: 0* — with a positive control that every injected artifact is still admitted as evidence, because silently dropping the text would break the append-only invariant while looking like a pass. |
| Agents cannot write, by grant rather than by prompt | Migration `0008` issues `GRANT SELECT` on the five `agent_*_v1` views to `pv_agent_reader`, then `REVOKE ALL ON TABLE <all 26> FROM pv_agent_reader`. |
| The commit is one transaction | `services/control_plane/app/memory_kernel/transaction.py` — `SERIALIZABLE`, bounded retry on SQLSTATE `40001`. |
| No model or network call inside that transaction | `python -m tools.txn_purity_lint services packages workers`, wired into `make lint`. |
| Vector search is stage D, not stage A | `STAGES` in `services/control_plane/app/retrieval/pipeline.py`, asserted by a test rather than left as a comment. |
| Every invariant names a test that actually runs | `python -m tools.invariant_map_check packages/python/provenance_domain/INVARIANTS.md` — *5 invariants, 5 mapped, 0 UNPROVEN*. |

### The honest gaps in diagram 1

- **The agent layer is new and its model calls are unexercised.**
  `agents/runtime/` is now ~9,500 lines — ingestion and advocate graphs, the
  resolver, the Gemini model router, typed schemas — and **252 tests pass**. But
  every one of those runs against fakes. No graph has executed against a live
  Gemini endpoint, because there is no API key on this machine. The structure is
  proved; the integration is not.
- **The event plane went green while this file was being written.** The trigger
  predicate parser had 19 failing tests an hour before this edit and now has
  none: `services/control_plane/tests/events` is 262 passing, `actions` 74,
  `agents/runtime` 252. Treat every count in this document as a timestamp rather
  than a fact, and re-run the command instead of quoting the number.
- **The Gemini model ids are UNPROBED.** `ops/probes/gemini_probe.py` exists;
  `ops/gemini-probe.txt` currently records `CANNOT RUN — GOOGLE_API_KEY is not
  set`. Every id above is transcribed from documentation. The last time this
  project froze model ids from documentation, all of them were wrong.
  `CANNOT RUN` is recorded distinctly from `FAIL`, because the two lead to
  opposite decisions.
- **The vectors in `evidence_items` are Titan vectors at 1024 dimensions**, not
  Gemini vectors at 1536. Migration `0009_gemini_embedding_plane` widens the
  column to `VECTOR(1536)`, repoints both model `CHECK` constraints to the
  Gemini ids, and **nulls every embedding** — a 1024-dimension vector is not a
  truncation of a 1536-dimension one. The rows, their text, their hashes and
  their grounding edges survive; the vectors do not. The Gemini re-embed that
  refills them is in flight, and until it lands retrieval returns nothing,
  because the canonical query filters `embedding_version = 'v2'`.
- **Nothing is deployed to Cloud Run.** "target Cloud Run" means exactly that: a
  target. No container has been built or pushed. This is the one gap on this
  list that no amount of local work closes.
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

## 3. The four SQL roles, ordered by privilege

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

Nothing. The build runs on a workstation against a live CockroachDB Cloud
cluster.

```mermaid
flowchart LR

  classDef planned fill:#fff8e1,stroke:#f9a825,color:#5c4400,stroke-dasharray: 6 4
  classDef built   fill:#e8f5e9,stroke:#2e7d32,color:#14371a

  DEV["Developer workstation<br/>Python 3.12, Node 20+, GNU Make 3.82+<br/>BUILT - this is where everything currently runs"]
  CR1["Cloud Run - control-plane<br/>NOT DEPLOYED"]
  CR2["Cloud Run - web<br/>NOT DEPLOYED"]
  CR3["Cloud Run - agent-runtime<br/>NOT DEPLOYED"]
  CRDB[("CockroachDB Cloud<br/>BUILT - migrated and seeded, 18035 evidence rows")]
  GAPI["Gemini Developer API<br/>reachable - no probe transcript yet"]

  DEV --> CRDB
  DEV -. "planned" .-> CR1
  DEV -. "planned" .-> CR2
  DEV -. "planned" .-> CR3
  CR1 -. "planned" .-> CRDB
  CR3 -. "planned" .-> GAPI

  class CR1,CR2,CR3,GAPI planned
  class DEV,CRDB built
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
