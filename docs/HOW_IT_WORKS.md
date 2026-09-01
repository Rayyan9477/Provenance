# How Provenance works

Written for the person who built it, after weeks in the weeds, who now has to
explain the whole thing out loud in four minutes and then defend it under
questioning.

Everything below was read out of this tree. Where a claim is measured, the
command or the transcript is named. Where something is **not** built, this
document says so in the same sentence rather than in a footnote — because
`STATUS.md` §4 is the file that gets you caught, and the only safe posture is to
say it first.

Authorities, in precedence order: `docs/CANONICAL_DECISIONS.md` (frozen; it
outranks everything), `STATUS.md`, then this file. If this file and `STATUS.md`
disagree, `STATUS.md` is right and this file is stale.

**If you have fifteen minutes:** read §1, §2, §4 and §7, then skim §9. Those are
the thesis, the idea, the mechanism and the differentiator. §3, §5, §6 and §8 are
the ones you jump to mid-conversation when somebody asks *where*.

---

## 1. The one-sentence thesis

**Provenance is the consumer's side of the institutional record: your open
obligations with counterparties, held as versioned beliefs grounded in immutable
evidence, projected into transactional state, with prospective triggers so that
a deadline passing is itself an event.**

It exists because of an asymmetry. Your internet provider has your account
number, your service address history, every billing period, the policy version
in force on the day you called, and the retention schedule governing all of it.
You have a mail archive you cannot search by obligation. When the two records
disagree, only one of them is written down in a form that can be cited. The
$1,800 deposit that quietly was not returned is worth a few hundred dollars and
four hours of reconstruction, which is exactly why it goes unresolved. **The
problem is evidentiary, not motivational.**

If you only get one sentence on camera, use the second half: *"a contradiction
arriving four months later reopens a closed case in one serializable
transaction, instead of starting an argument."*

---

## 2. The data model, level by level

Six levels, six lifecycles, six authorities to change things. Twenty-six tables
total, created by migrations `0001`–`0008`
(`db/migrations/versions/`). The list of canonical tables is a constant:
`CANONICAL_TABLES` in `db/migrations/versions/0008_events_infrastructure.py`.

```
Artifact → Evidence → Claim → Belief → Commitment → State
```

Ordinary RAG has one level — the chunk — and resolves a contradiction by cosine
similarity. **Level 3 is the one it cannot represent at all.**

The worked example below is the ISP invoice from `docs/00_PRODUCT.md` §2.3: an
invoice for USD 186 covering 1–30 June, arriving four months after the same ISP
confirmed the cancellation in writing.

> **Names and figures, so you do not mix them up on camera.** §2.3's "$186 /
> example-isp.com" is the *illustrative* canon in the product document. The
> **seeded** counterparties are **Northline Fiber** (the ISP, carrying two
> relationships on one counterparty — the old address and the new) and
> **Harborview Property Management** (the landlord, USD 1,800 deposit due
> 2026-06-15) — see `scripts/seed/counterparties.py`. The two live
> counterfactual runs used `northline-final-invoice` and a balance of **$74.20**
> (`ops/counterfactual-live-run-2.txt`). Talk about the *shape* with §2.3's
> numbers if you like; quote the *cluster* with Northline's.

### Level 1 — Artifact (`source_artifacts`)

**What it is:** the immutable external object. Bytes in object storage, identity
and hash in CockroachDB. Identified by `content_sha256`, so the same forward
twice is one artifact.

**What it is NOT:** parsed content. Provenance never edits an artifact and never
re-parses it in place.

**Invoice:** one row, `source_type = 'EMAIL_INBOUND'`, `mime_type =
'message/rfc822'`, with two clocks — `received_at` (when the bytes arrived) and
`event_time` (the invoice issue date read off the document). The pair is what
makes "a June invoice arriving in September" visible; either clock alone makes
it unremarkable.

### Level 2 — Evidence (`evidence_items`)

**What it is:** atomic, immutable, span-anchored observations extracted from one
artifact. Each carries a `source_locator` (part, char_start, char_end), an
`extraction_confidence`, an embedding, and a `retraction_status`.

**What it is NOT:** a conclusion. Evidence is append-only — nothing in the system
edits or deletes an admitted row.

**Invoice:** three rows — a `DATE_ASSERTION` ("service period 2026-06-01 through
2026-06-30"), an `AMOUNT_ASSERTION` ("amount due USD 186.00"), and an
`IDENTIFIER_ASSERTION` ("account 88-114-2039"). The identifier is what makes
retrieval deterministic rather than lucky: it exact-matches
`relationships.external_account_ref` before any vector search is consulted.

### Level 3 — Claim (`claims`)

**What it is:** an assertion bound to an actor, in a capacity, over a validity
interval. Columns: `predicate`, `object_json`, `actor_type`, `actor_id`,
`claim_kind`, `evidence_id`, `valid_from`, `valid_to`, `authority_score`.

**What it is NOT:** a fact. This is the whole point.

**Invoice:** one row. `predicate = 'balance_owed'`, `object_json =
{"amount":"186.0000","currency":"USD"}`, `actor_type = 'COUNTERPARTY'`,
`claim_kind = 'COUNTERPARTY_CLAIM'` — explicitly not `OBSERVATION`.

> The invoice arriving does not make $186 owed. It makes $186 **claimed**, by a
> party with a financial interest, about a period beginning after a termination
> that same party confirmed in writing. Every one of those qualifiers is a
> column.

### Level 4 — Belief (`beliefs`, `belief_versions`, `belief_support`)

**What it is:** three tables doing three jobs. `beliefs` is a stable proposition
identity with a pointer to its current version. `belief_versions` is the value at
a point in the record, with an `epistemic_status` and a supersession chain —
that is **lineage**. `belief_support` is the append-only edge set
`SUPPORTS` / `CONTRADICTS` / `QUALIFIES` — that is **grounding**.

**What it is NOT:** a summary, and not one table. Grounding and lineage are
deliberately never merged. `services/control_plane/app/state_proof/builder.py`
defines a `GroundingLineageMergeError` for exactly that reason: the merge is a
*plausible* refactor that a reviewer skimming a diff would not stop, and what it
destroys is the product. A user disputing a charge needs to show the
counterparty the evidence; a version history answers a different question with
total confidence.

**Invoice:** `balance_owed` gets version v2 whose **value is unchanged at
USD 0.00** and whose **`epistemic_status` moves `CONFIRMED` → `DISPUTED`**. Its
grounding: two `SUPPORTS` edges to the termination confirmation and the
service-end notice, and one `CONTRADICTS` edge to the $186 claim. Prose cannot
express "same value, different confidence, because of this specific counter-
assertion". A version chain plus edges can.

Note the consequence for the Kernel: because `belief_support` rows are written
only in the transaction that creates the version they attach to, **any change in
grounding requires a new version** — so even `RETAIN_INCUMBENT_AUTO` writes
v(n+1) with an identical value and a new `CONTRADICTS` edge
(`app/memory_kernel/disposition.py`, rule G3).

### Level 5 — Commitment (`commitments`, `fulfillments`)

**What it is:** an obligation with an obligor, a deadline, and a fulfillment
ledger. `outstanding_amount = committed_amount - fulfilled_amount`, computed in
Python (`app/memory_kernel/money_ops.py`) and enforced by a database `CHECK`.

**What it is NOT:** an LLM's arithmetic, and not every claim. The ISP invoice is
deliberately *not* a commitment — a counterparty demanding money does not create
a user obligation here.

**Sibling rows, which is what the demo shows:** the moving company owes USD 420
with USD 200 fulfilled, so USD 220 outstanding, status `PARTIAL`. The landlord
owes USD 1,800 with USD 0.00 fulfilled, due 2026-06-15, status `ACTIVE`.

A model that reasons its way to $220 and a `CHECK` constraint are not the same
engineering artifact, and only one of them is load-bearing.

### Level 6 — State (`cases`, `state_transitions`, `conflicts`, `outbox_events`)

**What it is:** the transactional projection an application may act on and an
action is allowed to cite. `cases.revision` is the aggregate version;
`state_transitions` is the append-only history; `conflicts` materialises a
disagreement as a queryable, countable row rather than leaving it implicit in
the edges; `outbox_events` is written in the same transaction as the state it
describes.

**Invoice:** `cases` moves `RESOLVED → REOPENED`, `revision` increments by
exactly one, `reopened_count` 0 → 1, `attention_level` `NONE → URGENT`; one
`state_transitions` row; one `conflicts` row (`VALUE_CONFLICT`, `NEEDS_HUMAN`,
`HIGH`); one `outbox_events` row (`case.reopened.v1`) whose `aggregate_version`
is the **post**-increment revision.

### Why six tables and not one

Because each level has a different authority to change it, a different
lifecycle, and a different answer to a different question:

| Level | Question it answers | Who may change it |
|---|---|---|
| Artifact | What arrived? | nobody — content-addressed |
| Evidence | What does it say, exactly where? | append-only |
| Claim | Who asserted it, in what capacity? | append-only, Kernel |
| Belief | What do we hold, and why? | Kernel, versioned |
| Commitment | What is owed, and how much is left? | Kernel + a `CHECK` |
| State | What may an application act on? | Kernel, one revision at a time |

Collapse any two and you lose a distinction the product is built on. Collapse
Claim into Belief and an invoice becomes a fact. Collapse grounding into lineage
and "here is the evidence" becomes "here is the history".

---

## 3. The request path, end to end

A visitor opens the deployed web app and lands on the dashboard. There is nothing
to sign into: the web app holds its API token server-side.

**1. Next.js server component.**
`apps/web/src/app/(app)/dashboard/page.tsx`. `export const dynamic =
"force-dynamic"`; the component `await`s `loadMe()` and `getDashboard()`. Every
read runs on the server. The page performs **no arithmetic** — the context total
is `contexts[].total_outstanding`, the counts are `counts.*`, the relationship
figures are `relationships_summary[].outstanding`. Summing the relationship rows
in the browser would produce a number that happened to match today.

**2. The client.**
`apps/web/src/lib/api/reads.ts::getDashboard` →
`apps/web/src/lib/api/client.ts::apiGet("/v1/dashboard")`. `apiBaseUrl()` reads
`PV_API_BASE_URL`; unset means **FIXTURE** mode behind a permanent,
non-dismissible banner. `apiToken()` reads `PV_API_TOKEN`, deliberately without
a `NEXT_PUBLIC_` prefix so Next.js cannot inline it into the browser bundle.
Failures are values, not exceptions — a screen that cannot read an endpoint
renders an error state naming the endpoint and never falls back to a fixture.

**3. The route.**
`services/control_plane/app/api/routes/memory.py`, line 105:
`@router.get("/dashboard")`.

**4. Authentication and scope.**
`services/control_plane/app/api/deps.py::owner_scope` is the only place an
`OwnerScope` is built on the public surface, and it is built from a `Principal`
resolved by `app/auth/principal.py::build_human_principal` — a `users` row
lookup keyed on the verified subject identifier, **never** from a token claim
and never from the body. Every port method's first parameter is an `OwnerScope`,
so a route physically cannot issue an unscoped read: ownership is a required
positional argument and the only factory for it ignores the request body.

**5. The port.**
`services/control_plane/app/api/ports.py`, line 108: `ReadPort.dashboard`. A
Protocol, so the route depends on a shape rather than on an implementation.

**6. The adapter.**
`services/control_plane/app/api/adapters/read.py::SqlReadPort.dashboard`, line
147. **It contains no SQL** — `tests/api/test_port_adapters.py` asserts that by
walking the AST. It chooses which repository function to call and reshapes what
comes back.

**7. The SQL.**
`packages/python/provenance_db/src/provenance_db/repositories/`:

- `dashboard.py` — `ATTENTION_CASE_COUNT_SQL`, `ATTENTION_CASES_SQL`
- `commitments.count_open_commitments`, `conflicts.count_open_conflicts`,
  `actions.count_pending_action_intents`, `triggers.get_trigger_counts`
- `contexts.list_contexts` / `list_context_outstanding`
- `relationships.list_relationships` / `list_relationship_outstanding`

**The headline `USD 2,020.00` is `CONTEXT_OUTSTANDING_SQL`** in `contexts.py`:
`sum(cm.outstanding_amount)` grouped by `(context_id, currency)`, over
commitments in `PROPOSED`, `ACTIVE`, `PARTIAL` or `DISPUTED`. The per-
relationship figures beneath it are the same shape from
`RELATIONSHIP_OUTSTANDING_SQL` in `relationships.py`, grouped by
`(relationship_id, currency)`. Both are **never summed across currencies** —
that is why the grouping carries `currency` at all, and it is why the page
renders the context total rather than adding the relationship rows up in the
browser.

**8. The connection.**
The pool is `pv_app_reader_writer`, constructed in
`services/control_plane/app/main.py::build_runtime` through
`provenance_db.pools.application_pool(SqlRole.APP, ...)`. One pool per SQL role
(`SqlRole` in `packages/python/provenance_db/src/provenance_db/pools.py`, line
90). Every statement runs through
`repositories/_execute.py`, which reuses `provenance_db.retry`'s SQLSTATE set
rather than holding a second copy of `"40001"`.

**9. Back out.**
The route returns `json_response({**row, "generated_at": deps.clock()})`. The
clock is injected, never read from the wall — "overdue" is a comparison, and the
case header, the commitment list and the trigger predicate must all get the same
answer from one instant.

The State Proof follows the identical path:
`/v1/cases/{case_id}/state-proof` (`routes/memory.py` line 241) →
`ReadPort.state_proof` → `adapters/read.py` line 430 → seven repository calls
over `beliefs`, `commitments`, `conflicts`, `cases`, `actions`, `evidence`. The
payload sets `"model_used": None` at `read.py` line 491, and the page renders
that field rather than asserting it
(`apps/web/src/app/(app)/cases/[caseId]/proof/page.tsx`).

---

## 4. The write path

### Why agents cannot write

Not "are instructed not to" — *cannot*, enforced three ways:

1. **By grant.** Migration `0008`'s `GRANT_DDL` gives `pv_agent_reader`
   `SELECT` on five views and then, explicitly,
   `REVOKE ALL ON TABLE <all 26> FROM pv_agent_reader`. There is no credential
   to misuse.
2. **By the tool surface.** `IngestionDeps` in
   `agents/runtime/graphs/ingestion_graph.py` and `AdvocateDeps` in
   `advocate_graph.py` are frozen dataclasses a reviewer can read at once. There
   is no `update_belief`, no `resolve_case`, no `send_email`, and no client
   object from which one could be reached. `kernel` accepts a typed proposal and
   returns a receipt.
3. **By lint.** `agents/runtime/tests/test_no_write_tools.py` walks the AST of
   every shipped agent module for canonical write vocabulary and for the two
   writer SQL roles, and runs twelve injected adversarial artifacts end to end
   asserting *kernel commits caused: 0, action intents created: 0, scopes
   escalated: 0* — with a positive control that each injected artifact is still
   admitted as evidence, because silently dropping the text would break the
   append-only invariant while looking like a pass.

The only door is `POST /internal/v1/agent-runs/{id}/proposals` →
`app/api/adapters/internal.py::KernelInternalPort.submit_proposal` (line 620),
and even that is two steps split by a grant: the **app** inserts the
`memory_proposals` row (`app/proposals/submission.py::register_proposal`, write
rule `W4`), then the **Kernel** decides it. `commit_proposal` only ever
`UPDATE`s that row, and `fk_kernel_decisions_proposal` refuses the decision row
when the proposal row is absent — so the order is a foreign key, not a
preference.

### What the Kernel does, in order

`services/control_plane/app/memory_kernel/transaction.py::commit_proposal`
(line 906).

**PHASE A — before any transaction opens.** `_read_preflight_snapshot`, then
`preflight.preflight` (`app/memory_kernel/preflight.py`). A refusal here writes
its `kernel_decisions` row with `transaction_opened = false` and returns. That
field is a **property, not a parameter** — it cannot report otherwise. The
guarantee is not that Python refused; the composite foreign keys refuse a
cross-user evidence reference at the database level even if the Kernel is
bypassed. What preflight adds is that the refusal is cheap, named with a
reason code from a closed catalogue, and auditable.

**PHASE B — one `SERIALIZABLE` transaction.** The callback is wrapped by
`provenance_db.retry.run_in_serializable_tx` — the only retry loop in the
repository, bounded, on SQLSTATE `40001`. Inside, in order:

1. Mint a fresh `decision_id` (rule 4 of §7.3 forbids deterministic UUIDs across
   attempts — idempotency comes from `proposal_id` and the unique constraints).
2. `_read_aggregate` — **fresh reads on every attempt.** A retry replays nothing
   from a rolled-back snapshot; `CommitContext` deliberately carries four
   identity fields and no plan, so it *cannot*.
3. `pipeline.build_write_plan` (`app/memory_kernel/pipeline.py`) — a pure
   function of (rows read, proposal payload, frozen config) producing a
   declarative `WritePlan`. The whole decision is reachable from a unit test
   with no network, no credentials and no model call.
4. `decisions.build_decision_row`.
5. `apply_write_plan` executes the plan in `STATEMENT_ORDER`
   (`transaction.py`, line 144):

   ```
   read_case → kernel_decisions → claims → belief_versions → belief_support
   → beliefs_pointer → belief_versions_supersede → conflicts
   → commitments_insert → fulfillments → commitments → cases
   → state_transitions → prospective_triggers → memory_proposals
   → outbox_events
   ```

   The order is not stylistic. Foreign keys are validated at statement time:
   `belief_versions` and `state_transitions` both carry a `NOT NULL`
   `kernel_decision_id`, so the decision row goes first. `commitments_insert`
   precedes `fulfillments` because a fulfillment against an obligation this same
   commit opened would otherwise hit a row that does not exist yet.
   `outbox_events` goes **last** because its `aggregate_version` is the
   post-increment revision — writing it before the `cases` UPDATE produces a
   plausible-looking row with the wrong version, and nothing downstream can tell.

`apply_write_plan` returns the labels it executed, and a test compares them to
`STATEMENT_ORDER`. Without that, "the order is the specification" is a comment.

**Retry exhaustion performs no side effect and enqueues nothing.** There is no
Kernel retry queue. Re-drive is the caller's job, over `503` plus `Retry-After`.

**Case legality** lives in `app/memory_kernel/case_ops.py`, which *wraps*
`provenance_domain.transitions.CASE_MACHINE` rather than re-implementing it — a
second copy of the legality table would diverge on the day someone adds a state
to one of them. `RESOLVED → REOPENED` is guarded by `qualifies_for_reopen`, five
conjuncts evaluated in order:

- **Q1** at least one evidence item never before linked to this case;
- **Q2** record-time freshness against `resolved_at` (valid time may be old, and
  in the hero it is);
- **Q3** the new evidence must have *done* something canonical — a material
  conflict, a material commitment status change, a trigger that fired on a true
  predicate, or a disputing claim. **This is the conjunct that stops a marketing
  email reopening a case**, and none of its four branches is "the model thought
  this was important";
- **Q4** artifact-hash dedupe;
- **Q5** the flapping guard — a case that has reopened `max_reopens` times needs
  a person, not a sixth automatic reopen.

`revision_after` enforces exactly one increment or none at all.

### What `write_path_lint` enforces, and how

`tools/write_path_lint.py`. Run it: `python -m tools.write_path_lint`.

It parses every Python module under `services`, `packages`, `workers`, `agents`,
`apps/web` and walks the **string literals** in the AST (canonical SQL lives in
string constants), plus non-Python files as comment-stripped text. Against
seventeen `CANONICAL_TABLES` it applies five rules:

| Rule | Meaning |
|---|---|
| `W1` | canonical `INSERT` is Kernel-only |
| `W2` | canonical `UPDATE` is Kernel-only |
| `W3` | canonical `DELETE` is forbidden **everywhere** |
| `W4` | evidence and proposal `INSERT` is app-permitted (`app/ingestion`, `app/proposals`) |
| `W5` | `UPDATE outbox_events SET status` is dispatcher-permitted (`app/events/dispatcher.py`) |

`W4` and `W5` can never produce a violation and are listed as rules anyway,
because *an exemption that is not counted as a rule is an exemption nobody
reviews.*

It prints four numbers on every run — rules, violations, modules walked, and
statements found **inside** the Kernel — because `0 violations` over `0` scanned
statements is a vacuous pass, and that exact failure is in the defect ledger.
The Kernel's statements are **named** in
`transaction.CANONICAL_WRITE_STATEMENTS` (19 entries), so the count is a claim
about specific statements rather than a magic constant.

Two companion linters carry the rest: `tools/txn_purity_lint.py` (no model or
network construct inside a transaction callback — a callback runs once per
retry, so a model call inside it is charged again on every attempt while the
transaction holds its locks, and an external effect inside it cannot be rolled
back) and `tools/invariant_map_check.py` (every invariant names a test that
actually runs; a skipped or `xfail`-ed test reports `UNPROVEN` as loudly as a
missing one).

**Re-run these rather than quoting the numbers.** They move as the ingestion
path grows, and a figure in prose that nobody re-measures is the failure the
lint exists to prevent.

---

## 5. The agent graphs

Three graphs, all in `agents/runtime/graphs/`, built natively against
`google-genai` (verified at 1.60.0). Each topology is a **constant tuple** in
`agents/runtime/state.py` that the graph walks, so "do not invent an eighth
node" is a test on a tuple rather than a code review of control flow, and the
visit order a test prints is the same object the graph dispatched on.

### Ingestion — `ingestion_graph.py`, 11 nodes

`INGESTION_NODES` (`state.py` line 163): `load_artifact_metadata`,
`load_normalized_content`, `extract_structured_evidence`,
`validate_extraction_schema`, `register_or_lookup_evidence`,
`retrieve_candidate_context`, `route_resolution_need`, `strong_resolution`,
`build_memory_proposal`, `submit_to_memory_kernel`, `route_commit_result`.

Exactly **one** conditional edge, at `strong_resolution`, whose predicate is
`should_resolve` — a pure function over six documented signals: the Kernel's
preflight asked for resolution, the extraction contradicts a canonical belief,
the validity interval is ambiguous, a commitment supersession is possible, there
is blocking uncertainty, or identity is weak (top case score below
`IDENTITY_STRONG_THRESHOLD`, or margin below `IDENTITY_MARGIN_THRESHOLD`). Both
thresholds come from `provenance_domain.invariants`, so tightening one is a
reviewed change to a named constant rather than an edit inside a paragraph of
English.

**Tiers.** `extract_structured_evidence` routes to **Tier E**,
`gemini-3.5-flash-lite`, no thinking effort, 8192 output tokens — it is bulk
structured extraction, and the answer is checkable against spans in the
document, so the cheap fast model is the right one. `strong_resolution` routes
to **Tier R**, `gemini-3.7-flash`, `effort=HIGH`, 16000 tokens, tools enabled —
it only runs when identity is genuinely ambiguous, which is a judgement call
that costs more to get wrong than to compute.

### Advocate — `advocate_graph.py`, 6 nodes

`ADVOCATE_NODES`: `load_state_proof`, `classify_attention_need`,
`select_action_template`, `draft_action`, `validate_draft_claims`,
`create_action_intent`. Both model nodes are **Tier R** —
`classify_attention_need` at `effort=MEDIUM`, `draft_action` at `effort=HIGH` —
because one decides whether to bother a human and the other writes words that
will be sent to a counterparty over the user's name.

**Approval is not a graph state.** The module ends at `create_action_intent` and
has *no notion of approval at all*. The approval that matters is a database
transition authenticated as the user and bound to the case revision and the
draft's SHA-256. The capability is absent rather than unused, which is the
difference between a design and an intention.

### Counterfactual — `counterfactual_graph.py`, 2 nodes

`COUNTERFACTUAL_NODES`: `bind_memory`, `draft_reading`. **Both modes walk the
same tuple** — that is what "the same graph" means structurally rather than as a
promise in a docstring. Both route through `draft_action`'s spec (Tier R,
`pv-draft-1.0.0`, `effort=HIGH`), so the decode parameters are a function of the
route table and not of this module.

MEMORY OFF does **not** call the binder. It substitutes `EMPTY_MEMORY_CONTEXT`
and never touches `deps.memory` at all, so "memory off could not see memory" is
a property of the control flow rather than a conditional inside a collaborator.
`tests/test_counterfactual_graph.py` proves it with a binder that raises on
contact. The module imports nothing that can write — no kernel client, no
evidence registrar, no intent writer.

### What a `MemoryProposal` is

`packages/python/provenance_contracts/src/provenance_contracts/proposal.py`,
line 419. A typed pydantic object carrying `proposal_type`, `trace_id`,
`agent_run_id`, `user_id`, the source artifact and evidence ids, and up to five
kinds of proposed change: `claims`, `commitments`, `belief_mutations`,
`conflict_hints`, `trigger_mutations`. Plus `model: ModelAttribution`,
`idempotency_key`, and an optional `requested_case_transition`.

**Absent by design:** `tenant_id` (the Kernel derives tenancy from the
authenticated capability binding — a field the agent could fill in is a field an
attacker could fill in), any authority score, and any SQL, table name or
permission grant. `user_id` **is** present, as a cross-check rather than a
grant: the Kernel rejects the proposal when it disagrees with the binding,
because a machine client asserting a user id it was not issued is a security
event and should be loud.

A validator refuses an empty proposal: *"if nothing was learned, do not submit
and let the run end with a visible NOOP status."*

### When a model returns something that will not validate

`agents/runtime/model_router/router.py`. Three budgets, enforced in the router
rather than per node, because a per-node budget is a budget three places can
disagree about:

| Failure | Budget | Response |
|---|---|---|
| schema / semantic invalid | 1 | one repair on the same model, with a structured `REPAIR_INSTRUCTION` naming the JSON path and the problem |
| invocation failed (Tier E) | 1 | one call on the Tier R model at `LOW` effort |
| invocation failed (Tier R) | 0 | `PENDING_REVIEW` immediately — Tier R never downgrades |
| transport (throttle, 5xx) | N | bounded backoff, spends no other budget (currently `DEFAULT_MAX_TRANSPORT_RETRIES = 0`, off until there is a live endpoint to calibrate against) |
| refusal | 0 | `PENDING_REVIEW` immediately — a refusal is a decision, not an outage |

`MAX_MODEL_CALLS_PER_NODE = 2` is a hard cap dominating both, so "never three
calls" is true on every path and not only on the schema path.

**`PENDING_REVIEW` is returned, not raised.** An exception can be swallowed by a
caller in a hurry; a returned `PendingReview` has to be handled, carries a
closed-set reason code, and carries the call records the `agent_runs` row needs
— so the failure is *persisted* rather than merely logged. Failing is always
cheaper than committing a guess.

The graph then re-validates independently: `validate_extraction_schema` in
`agents/runtime/nodes/ingestion.py` (line 261) re-runs the same pure check over
data already in hand, because the router is a dependency the graph does not own
— a router that silently stopped calling `validate` would otherwise put an
unverified span into `evidence_items` and nothing would say so. A failure there
terminates the run as `FAIL_SAFE`, one of seven visible `IngestionOutcome`
values.

**Every model id is configuration, never a literal at a call site.**
`GeminiRouterConfig.model_id_for` is the only tier→string mapping and
`ALLOWED_MODEL_IDS` is a `Literal`-derived frozenset, so an unknown id fails at
container start rather than on the first artifact that routes through it. The id
that actually served each call is recorded in `agent_runs.model_calls[]`.

**Live status:** both tiers have been invoked against live Gemini endpoints —
`ops/agent-graph-live-run.txt`, `PASS 33 | FAIL 0 | CANNOT RUN 11`, 31
`agent_runs` rows where there were zero. Every id is settled by *invocation*, not
by listing: `ops/gemini-probe.txt`, `PASS 11 | FAIL 0 | CANNOT RUN 0`.

---

## 6. Prospective triggers

**How a deadline becomes an event.** A commitment with a `due_at` produces a
`prospective_triggers` row, armed by the Kernel in the same transaction that
recorded the obligation (`prospective_triggers` is statement 13 of
`STATEMENT_ORDER`).

### The row — `db/migrations/versions/0006_prospective_memory.py`

```
predicate_ast       JSONB       NOT NULL
not_before          TIMESTAMPTZ NULL
expires_at          TIMESTAMPTZ NULL
state               STRING      NOT NULL DEFAULT 'ARMED'
evaluation_version  INT8        NOT NULL DEFAULT 0
basis_case_revision INT8        NOT NULL
```

with `CHECK ((state = 'FIRED') = (fired_at IS NOT NULL))`.

### The predicate AST — `app/triggers/ast.py`

The stored predicate is **data, not code**, and the argument is short: a trigger
predicate is authored from attacker-influenceable text and then sits in a
database for months. If it were executable, a forwarded PDF would have got a
code-execution primitive with a several-month fuse.

`ALL_OPS` has thirteen members — `AND OR NOT`, `EQ NE GT GTE LT LTE`,
`IS_NULL NOT_NULL`, `FIELD CONST` — and **none of them computes**. There is no
arithmetic node, no call, no set membership. Budgets: `MAX_NODES = 128`,
`MAX_DEPTH = 12`, `MAX_ARGS = 16`, `MAX_CONST_STRING_LEN = 256`. Parsing is
total: either a fully typed tree comes back or `TriggerSpecError` is raised,
before storage.

"Days overdue" is not an expression. It is `commitments.<binding>.days_overdue`,
a named field derived once in reviewed Python in `app/triggers/projection.py`
and whitelisted in `app/triggers/registry.py`. A reviewer reading a stored term
can tell what it means without also auditing how it computes.

`registry.py` is a **closed whitelist**, and what it omits is the point: no path
reaches `evidence_items`, `claims`, `beliefs`, `belief_versions`,
`source_artifacts`, `action_intents` or `outbox_events`, and **no path reaches
raw text of any kind**. A predicate cannot match on a subject line, a sender or
a body. It compares scalars from the state and obligation planes — exactly the
planes where an attacker who forwarded a hostile PDF has no influence.

### The evaluator — `app/triggers/evaluator.py`

Pure, total, eager, reproducible. No I/O, no clock, no randomness — `clock.now`
arrives in the value map, taken from `now()` in the same read-only transaction
as the rest of the projection, because reading a local clock here would compare
a worker's time against a database-written deadline.

**Three-valued.** The naive rule "a comparison against NULL is false" is unsafe:
`NOT(EQ(x, 'FULFILLED'))` would then be **true** when `x` is unknown, and the
trigger would fire on missing data — a demand for money that may already have
been paid, sent because a column was empty. So a NULL operand yields `UNKNOWN`,
and **a trigger fires only when the root evaluates to exactly `TRUE`**.
`UNKNOWN` is memory correctly declining to assert something it does not know.

Evaluation is eager rather than short-circuiting, so the trace records every
subexpression. `EVALUATOR_CODE_VERSION` is bumped whenever the semantics change,
so an old evaluation's replay is never silently reinterpreted by new code.

### What "armed" means

`state = 'ARMED'` means: a parsed predicate is stored, a generation counter
(`evaluation_version`) is set, the case revision it was armed against is
recorded, and a `not_before` instant exists — **and nothing has happened yet.**
Arming is not a promise to fire. Firing requires a wake *plus* a re-evaluation
against freshly read canonical state that comes back `TRUE`.

> A wakeup is an invitation to re-evaluate, never an instruction to act. The
> envelope was frozen at arm time — possibly months ago — and carries identity
> only. Everything the outcome depends on is read again at wake time.
> (`app/triggers/service.py` module docstring.)

Every wake — scheduler, sweeper, replay, and the demo's manual button — lands on
the same `evaluate_trigger`. `wake.source` is a label for metrics and the Memory
Trace, and `test_no_branch_reads_the_wake_source_to_decide_behaviour` scans the
source to keep it that way. There is **no `force` parameter**, and
`TriggerWakeRequest` forbids unknown fields, so `{"force": true}` is a `422`
rather than a silently ignored key.

The route is `POST /v1/triggers/{trigger_id}/wake`
(`app/api/routes/memory.py`, line 391). It deliberately takes **no**
`Idempotency-Key`, and it is the one place in the write surface where that is
correct: pressing the button twice must reach guard `G2` and answer
`NO_OP / TRIGGER_NOT_ARMED`, because the first press disarmed the trigger. A
replayed response would return the first press's `FIRED` again — a cached
verdict presented as a fresh evaluation, on stage. The dedupe is instead the
idempotency claim the Kernel makes as the *first* statement of the fire
transaction, keyed on `evaluation_version`, which is derived from canonical state
rather than supplied by the caller. Migration `0009b` exists to grant
`pv_kernel_writer` the `SELECT, INSERT` that claim needs.

A `FALSE` result is not one thing. `app/triggers/outcomes.py::classify_false`
distinguishes *the landlord paid* (discharged; re-arming would be a standing
promise to keep asking about a settled matter) from *the deadline has not
arrived yet* (false now, true later; dropping it would silently lose the
obligation). It reads statuses the Kernel wrote; it consults no model.

The fire itself is a canonical write, so it goes through the Kernel:
`app/memory_kernel/trigger_commit.py::commit_trigger_evaluation`. An earlier
attempt put that function in `app/triggers/` and produced five `W1`/`W2`
violations — the linter doing exactly its job.

**Live status:** two triggers are armed on the deployed cluster — a
`COMMITMENT_DEADLINE` at `2026-06-15T00:01:00Z` (the landlord deposit) and a
`RESPONSE_DEADLINE` at `2026-06-29T00:00:00Z`. The wake route reaches its
handler. **Neither has been fired**, deliberately: the first press disarms it and
it is the demo's second reveal (`ops/demo-rehearsal-live-cloudrun.txt`).

---

## 7. What is deliberately absent, and how the system says so

This is the differentiator. The one-breath version:

> **A memory product must never claim confidently that it has nothing.** So
> every unbuilt capability here names itself: unbound methods raise and are
> counted in one register, the HTTP surface answers `501 NOT_IMPLEMENTED` naming
> the subsystem it waits on rather than `500` or an empty list, the UI has
> exactly one component permitted to render "we do not have this", and every
> transcript records `CANNOT RUN` as a third verdict distinct from `FAIL`,
> because the two lead to opposite decisions.

### The unbound-port register

`services/control_plane/app/api/adapters/unbound.py` — a dict from
`port.method` to *what it is waiting on*. Six entries out of the API's 47
methods:

| Method | Waiting on |
|---|---|
| `read.get_trace` | the trace assembler |
| `read.memory_trace` | the trace assembler |
| `internal.retrieve` | the retrieval pipeline's executor |
| `internal.create_action_intent` | a typed `StateProof` the live read path does not build |
| `write.create_correction` | `app/ingestion`, plus a `RETRACT_EVIDENCE` writer that exists nowhere |
| `write.rotate_ingest_alias` | the ingest-alias minting path |

Two reasons it is a register and not a scattered `raise NotImplementedError`:

- **An empty list is a lie.** A read method with no backing returning `[]`
  renders as "no conflicts on this case" — indistinguishable from a real empty
  result, and believable enough that nobody investigates.
- **A register can be counted.** Greps drift silently. This dict is asserted by
  `tests/api/test_port_adapters.py`, and **wiring a method means deleting a line
  from it — a visible act in a diff.**

`unbound()` also raises `KeyError` if a method starts refusing without being
declared, which keeps the register honest in both directions.

### 501 versus 500

`app/api/errors.py`. `ErrorCode.NOT_IMPLEMENTED` maps to `501`
(`DEFAULT_HTTP_STATUS`, line 199), and the `NotImplementedError` handler at line
476 passes the register's sentence through verbatim rather than replacing it
with the generic message.

The comment on that handler is the argument: the catch-all used to flatten this
to `500 INTERNAL_ERROR` with "Something went wrong on our side", and Judge Mode
showed a reader `GET /v1/traces/... returned 500 INTERNAL_ERROR`. That is
`CANNOT RUN` reported as `FAIL`, one layer up. It is also wrong in detail —
"nothing was committed" implies a write was attempted and rolled back, and for
an unbound read nothing was attempted.

The frontend keeps the distinction:
`apps/web/src/app/(app)/judge/page.tsx` line 181 renders *"The trace assembler
is not built yet"* for a `501` and *"Trace unreadable"* for anything else, and
prints the API's message verbatim only in the `501` branch.

### The `Absent` primitive

`apps/web/src/components/primitives/Absent.tsx` is **the only component
permitted to render the absence glyph**, enforced by rule `R4` of
`apps/web/scripts/check-render-honesty.mjs`. If an em dash can appear anywhere,
a reader cannot tell "the system does not have this" from "the designer wanted a
dash there". Concentrating it gives the glyph one meaning and an accessible name
(`data-absence-reason` is one of `NO_ROW`, `NULL_COLUMN`, `EMPTY_COLLECTION`).

**It must never render a zero.** `USD 0.00` on a disputed balance is a true
statement about the record; `USD 0.00` standing in for a number we failed to
fetch is a lie, and the two are indistinguishable once printed. That is also
rule `R6`, `NO_VALUE_FALLBACK`: `?? 0` on an amount or `?? "N/A"` on a status is
a build failure. It is the rule that survives review, because the code looks
defensive.

The checker runs on `npm run build` and has a `--counterfactual` mode that
materialises a throwaway tree containing one deliberate violation per rule and
exits non-zero unless every rule fires.

### `CANNOT RUN` is not `FAIL`

They lead to opposite decisions. A probe that could not connect once reported
that a *capability* had failed — which would have forced a working capability
into a fallback. The third verdict appears in `make evals`, in `make sabotage`,
in every `ops/*.txt` transcript, and over HTTP as `501`.

The counterfactual screen is the same rule from the other side: live it says
*"No counterfactual has been run"* and stops
(`apps/web/src/app/(app)/judge/counterfactual/page.tsx`), because a specimen
comparison would be indistinguishable from a real one — *which is exactly the
objection the parity block exists to answer.*

### Where the line actually falls, stated precisely

- **Retrieval** — all eight stages exist (`STAGES` in
  `app/retrieval/pipeline.py`: `A_SCOPE`, `B_IDENTITY`, `C_TEMPORAL`,
  `D_VECTOR`, `E_RELATIONAL`, `F_GROUNDING`, `G_RERANK`, `H_CONTEXT`) and the
  ANN statement is proved against the corpus — `ann.py` parses the canonical
  predicate out of the spec markdown so the SQL cannot drift from the document.
  **No module runs A through G end to end.** That is why `internal.retrieve` is
  unbound and why the counterfactual passes an empty `evidence` array to its
  MEMORY ON side. The State Proof it *does* pass is rich, so that side is not
  evidence-starved — but retrieval is not on the live path.
- **Action intents** — `approve`, `reject`, `execute_action` and both reads are
  implemented, tested and bound; `basis_case_revision`, `draft_sha256`,
  revalidation and idempotency are all real. **Creation is not bound**, and it
  is not a wiring job: `GroundingSnapshot.from_state_proof` needs a typed
  `provenance_contracts.proof.StateProof`, and `build_state_proof` has **no
  production call site** — it and `from_state_proof` are reachable only from
  tests. The live State Proof is a dict assembled independently in
  `adapters/read.py`. Binding the method means refactoring a live tested path.
- **The trace assembler** — `app/observability/` is 140 lines and settles
  `agent_runs`. The seventeen closed trace node types would be assembled from
  `agent_runs`, `state_transitions`, `kernel_decisions`, `outbox_events` and
  `tool_calls`, all of which are persisted. It is assembly over existing rows,
  and it is real work that has not been done.
- **`commit_proposal` has never run on a live path.** `ops/ingestion-live-run.txt`
  (re-recorded 2026-08-31) measures every step from `write.upload_intent`
  through the typed `MemoryProposal` to the app-side `memory_proposals` INSERT
  against a real CockroachDB — `PASS 13, FAIL 0, CANNOT RUN 1` — and rolls back.
  The single `CANNOT RUN` is the Kernel's own call, and the reason is structural
  rather than blocked: the Kernel commits its own transaction and the runner
  rolls back, so a Kernel decision would outlive the proposal row it decided.
  **Say this before a reviewer finds it.**
- **The 51-scenario labelled eval corpus does not exist.** `evals/datasets/`
  holds two empty directories; the harness records `MEM-03 CANNOT RUN` rather
  than scoring around it.
- **Gate batteries 1 and 3–15 are not implemented**, and no gate has been
  signed. `ops/gates/PHASE_00.md` carries a real `REJECTED` verdict.
- **The demo video is not recorded.**

---

## 8. The five SQL roles and the agent-safe views

Defined once, in `db/migrations/versions/0008_events_infrastructure.py`
(`RUNTIME_ROLES`, `AGENT_VIEWS`, `CANONICAL_TABLES`, `GRANT_DDL`) and mirrored
as `SqlRole` in `packages/python/provenance_db/src/provenance_db/pools.py`.
Roles are cluster-scoped identities with passwords, provisioned once; `0008`
deliberately mints no credentials and `pv_migrator` holds no `CREATEROLE`.

| Role | Reads | Writes |
|---|---|---|
| `pv_migrator` | — | DDL only. Owns every table. **Never in a runtime connection string.** |
| `pv_kernel_writer` | `SELECT` on 22 tables | `INSERT`+`UPDATE` on `counterparties`, `relationships`, `contexts`, `cases`, `beliefs`, `belief_versions`, `conflicts`, `commitments`, `prospective_triggers`, `kernel_decisions`, `evidence_items`; `INSERT`-only on `claims`, `belief_support`, `fulfillments`, `state_transitions`, `outbox_events`; `UPDATE`-only on `memory_proposals`. **`REVOKE INSERT, UPDATE ON action_intents`** — the Kernel can never arm an action. `REVOKE ALL` on `action_executions`, `ingest_aliases`, `idempotency_records`, `processed_events` — it can never send anything. |
| `pv_app_reader_writer` | `SELECT` on all 26 | `INSERT`+`UPDATE` on `tenants`, `users`, `ingest_aliases`, `source_artifacts`, `action_intents`, `action_executions`, `agent_runs`, `idempotency_records`; `INSERT`-only on `evidence_items`, `memory_proposals`, `processed_events`; `UPDATE` on `outbox_events` (dispatch status only). |
| `pv_agent_reader` | `SELECT` on **five views**, nothing else. `REVOKE ALL ON TABLE <all 26>`. | nothing |
| `pv_ops_reader` | `SELECT` on the five views plus eleven operational tables | nothing — `REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public`. Provably read-only, which is why it is the credential that verifies traces: a principal that could not have authored what it verifies. |

`ALTER DEFAULT PRIVILEGES ... REVOKE ALL ON TABLES` for all four runtime roles
closes the last hole: nothing new is ever granted implicitly.

### The five agent-safe views

Views execute with the owner's table privileges, so no base-table grant is
needed for the agent — and none is given. Each view carries the tenant and user
columns and applies a filter the agent cannot remove:

| View | What it exposes, and what it withholds |
|---|---|
| `agent_case_context_v1` | live cases only — `status <> 'SUPERSEDED'` |
| `agent_active_beliefs_v1` | current version only, `epistemic_status <> 'RETRACTED'` |
| `agent_belief_lineage_v1` | the supersession chain joined to its `kernel_decisions` — the only unfiltered one, because lineage without its retracted links is not lineage |
| `agent_evidence_retrieval_v1` | `retraction_status = 'ACTIVE'`, and the raw `exact_text` and `source_locator` are **withheld** — the agent gets the normalised string plus the identifiers it needs to cite |
| `agent_open_obligations_v1` | commitments in `PROPOSED/ACTIVE/PARTIAL/DISPUTED`, conflicts in `OPEN/NEEDS_HUMAN` |

Note the asymmetry that matters: **retracted evidence is *shown* in the State
Proof and *excluded* from retrieval.** Those are different questions.
`app/state_proof/builder.py` answers the first — a retracted invoice is
frequently the `CONTRADICTS` edge that explains why a belief moved, and hiding
it would defeat the purpose of keeping lineage. `app/retrieval/predicates.py`
answers the second: keep it out of a *new* prompt.

### Where it is enforced

- **At runtime, by grant** — `GRANT_DDL` in migration `0008`. A statement issued
  under the wrong role simply fails.
- **At process start, by pool** — `APPLICATION_ROLES` in `pools.py` is
  `(APP, KERNEL, AGENT)`; `main.py::build_runtime` opens one pool per role and
  the Kernel's adapter holds only the Kernel pool. One pool with a role argument
  would turn the property back into a convention, because it would depend on
  call order rather than on credentials.
- **At review time, by lint** — `tools/write_path_lint.py`, §4 above.
- **Over the MCP surface** — `services/control_plane/app/mcp/` serves the same
  five views and nothing else.

The grant is the stronger of the two runtime/review checks, and the lint is the
earlier one. Keep both: a grant tells you in production at the moment the row
was needed; a lint tells you when the statement was typed.

---

## 9. If someone asks X, say Y

**Q. Why is approve-and-send blocked? Isn't the human gate the whole point?**
The gate is built and tested — `approve`, `reject`, `execute_action` and both
reads are bound, and `basis_case_revision`, `draft_sha256`, revalidation and
idempotency are all implemented, 76 tests. What is missing is the *creation* of
an intent: `internal.create_action_intent` is unbound, so there is no
`action_intents` row for the approve half to approve. It is not wiring. Binding
it requires `GroundingSnapshot.from_state_proof`, which needs a typed
`StateProof`, and `build_state_proof` has no production call site — the live
read path assembles a dict. So it is a refactor of a tested path, and I did not
do it in the last week. That is `STATUS.md` §4, first paragraph.

**Q. Why isn't retrieval on the live path?**
Because there are eight stages and no executor. `app/retrieval/` has all of
them, and the ANN statement is proved against the corpus — `ann.py` parses the
canonical predicate out of the spec markdown so the SQL cannot silently drift
from the document. But `pipeline.py` is a `STAGES` tuple and a `call_order()`
function; no module runs A through G end to end. So `internal.retrieve` answers
`501` naming the missing executor, and the counterfactual's MEMORY ON side gets
an empty `evidence` array. The State Proof it does get is rich — beliefs with
grounding edges, commitments with fulfillments, conflicts, derivations — so that
side is not evidence-starved, but I am not going to call retrieval live.

**Q. Why are there no Gemini Pro models?**
Because there is no Pro model on the Developer API that clears the version
floor. `gemini-3.1-pro-preview` is the only Pro available and it is version 3.1,
*below* the 3.5 floor this build holds to. Both tiers are therefore Flash-class:
`gemini-3.7-flash` for Tier R and `gemini-3.5-flash-lite` for Tier E, with
`gemini-3.6-flash` as a capacity fallback. That is a capability constraint, not
a preference, and it is recorded in the code
(`agents/runtime/model_router/models.py`, "The August 2026 model floor") and
frozen in `docs/CANONICAL_DECISIONS.md`.

**Q. Is the corpus real?**
Partly, and the split is published in `db/seeds/MANIFEST.json`. 18,035
`evidence_items`: **32 curated hero rows** and **3 retraction fixtures** are
hand-authored; **18,000 are synthetic decoys** (16,000 in the hero user's scope,
2,000 across two isolation tenants) generated from a seeded RNG. The decoys are
there to make retrieval hard on purpose — the eval reports recall@20 = 0.7715
over 31 queries and **names the two hero documents the decoy field buries**
rather than letting an average hide them. The vectors are AWS Titan at 1024
dimensions, cached in `db/seeds/vectors.parquet` and pinned by content hash. The
Gemini re-embed has not run.

**Q. Then are you actually using Gemini, or is this an AWS project with a new
label?**
Every live generation request goes to Gemini through `google-genai`, and both
tiers have been invoked — `ops/agent-graph-live-run.txt`, 31 `agent_runs` rows
each carrying `model_calls[]` attribution. Every id was settled by invocation
rather than by listing (`ops/gemini-probe.txt`, `PASS 11 | FAIL 0`). What is
still Titan is the *embedding* corpus, and `gemini-embedding-2` is probed and
canonical but not in use. Migration `0009`, which widens the column to
`VECTOR(1536)`, is authored and deliberately unapplied, because applying it
nulls 18,035 vectors — a 1024-dimension vector is not a truncation of a
1536-dimension one.

**Q. How do I know the dashboard number isn't hard-coded?**
Three ways. It is summed by `RELATIONSHIP_OUTSTANDING_SQL` in
`provenance_db/repositories/relationships.py` at query time, and the page does
no arithmetic. `apps/web/scripts/check-render-honesty.mjs` rule `R3` fails the
build if a hero-dataset value appears in rendering source, and `R2` fails it for
a hard-coded UUID. And `ops/demo-rehearsal-live-cloudrun.txt` fetched the
deployed page and asserted the rendered strings — `USD 1,800.00`,
`USD 2,020.00`, `USD 220.00` — against the API's own numbers.

**Q. What stops a forwarded PDF from telling the agent to do something?**
Three layers, none of which is a prompt instruction. The agent holds **no write
credential** — `pv_agent_reader` has `REVOKE ALL ON TABLE` for all 26 tables.
The graph's dependency object contains no writer to call, and
`test_no_write_tools.py` proves that against the AST plus twelve injected
adversarial artifacts, with a positive control that each injected artifact is
still admitted as evidence. And the only way into canonical memory is a typed
`MemoryProposal` that the deterministic Kernel validates, re-reads against, and
may reject with a named reason code. The strongest thing a malicious document
can achieve is a rejected proposal and a row in the audit ledger.

**Q. What if the model hallucinates a quotation?**
The extraction contract is span-anchored, and both the router and the graph
validate it independently — `validate_extraction_schema` re-runs the same pure
check over the same blocks and the same nonce. A span that is not a substring of
the block it claims is `SPAN_NOT_IN_BLOCK`, and the run terminates `FAIL_SAFE`
rather than admitting the evidence. That refusal is measured on a live path:
`ops/ingestion-live-run.txt`, *"9.4 steps 1–2 refuse an invented quotation —
VALIDATION_FAILED reason=SPAN_NOT_IN_BLOCK."*

**Q. The Memory Trace panel is broken.**
It is not broken, it is unbuilt, and the difference is the point of the panel.
It answers `501 NOT_IMPLEMENTED` and names the subsystem it waits on — the trace
assembler in `app/observability`. A read method with no backing that returned
`[]` would render as "memory did nothing on this case", indistinguishable from a
real empty result and believable enough that nobody investigates. Every unbuilt
capability here looks like that, and the six of them are listed in one file,
`app/api/adapters/unbound.py`, so wiring one is a visible deletion in a diff.

**Q. Why the Fortified Enterprise Fleet and not the Taskmaster?**
Because a Taskmaster takes autonomous action and this system deliberately does
not. Every outbound draft stops at a human approval bound to the case revision
and the draft's SHA-256. Claiming a category whose defining verb the
architecture refuses on purpose would be the wrong kind of ambition. The Fleet's
list — registry, runtime, memory, security, observability — is close to a table
of contents for what was built: `agent_runs`, three typed graphs, the Memory
Kernel, five roles plus five views plus base-table denial, and
`kernel_decisions` plus the State Proof plus the counterfactual harness.

---

## Appendix — the commands that check the claims

```bash
python -m tools.write_path_lint          # 5 rules, 0 violations, + 4 counts
python -m tools.txn_purity_lint services packages workers
python -m tools.invariant_map_check packages/python/provenance_domain/INVARIANTS.md
python -m tools.manifest_check db/seeds/MANIFEST.json
make route-sweep                         # every live route, against Cloud Run
make evals                               # PASS/FAIL/CANNOT RUN, read-only
npm --prefix apps/web run verify         # honesty + counterfactual + types + tests

curl -s https://provenance-control-plane-vaq74wztva-uk.a.run.app/v1/version
# read db_ok, not the status code
```

Transcripts: `ops/gemini-probe.txt`, `ops/agent-graph-live-run.txt`,
`ops/ingestion-live-run.txt`, `ops/counterfactual-live-run-2.txt`,
`ops/demo-rehearsal-live-cloudrun.txt`, `ops/route-sweep-live-cloudrun.txt`.

**Re-measure rather than quote.** Every number in this file is a timestamp.
