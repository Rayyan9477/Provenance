# Provenance — Product Definition

Purpose: fix the product thesis, the domain vocabulary, and the naming decisions that every other document in this repository defers to.

Status: planning-complete baseline v1.1
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared

Audience: implementation team, coding agents, reviewers, future contributors. Read this before `ARCHITECTURE.md`, `MEMORY_SYSTEM.md`, or anything under `docs/implementation/`.

---

## 0. Canon fixed by this document

Everything in this section is frozen. Other documents may elaborate; they may not contradict.

| Item | Value |
|---|---|
| Product name | **Provenance** |
| Tagline | A system of record for the institutions that already have one of you. |
| Former name | NeverReset (deprecated; retained only in git history) |
| Architectural north star | Evidence is append-only. Beliefs are revisable. State is transactional. Actions are permissioned. |
| Kernel rule | LLM agents propose typed `MemoryProposal` objects. The deterministic Memory Kernel is the only canonical writer. No agent gets SQL write access, ever. |
| Hero scenario | "The Move That Never Really Ended" |
| Demo clock | `2026-09-18T14:05:00Z` (seeded; the move happened four months earlier, mid-May 2026) |

### 0.1 The four invariants

1. **Evidence is append-only.** Admitted evidence is never rewritten or deleted; corrections arrive as new evidence.
2. **Beliefs are revisable.** A changed conclusion creates a new belief version and preserves the prior version and the reason it was superseded.
3. **State is transactional.** No case, commitment, or conflict may be left in an impossible partial aggregate state.
4. **Actions are permissioned.** No uncommitted proposal and no agent scratchpad may produce an external side effect.

Any implementation decision that breaks one of these is rejected, regardless of how convenient it is.

### 0.2 The three-term vocabulary

This distinction is load-bearing across every document, every prompt, and every UI string. It is restated in the glossary (§3) and enforced by a lint rule (§4.4).

| Term | Meaning | Never means |
|---|---|---|
| **Provenance** | The product. Always capitalised, always a proper noun. | The general concept of data origin. |
| **grounding** | The `belief_support` edges linking a belief version to the evidence and claims that back or oppose it (`SUPPORTS` / `CONTRADICTS` / `QUALIFIES`). | The version chain. |
| **lineage** | The `belief_versions` chain — v1 superseded by v2 superseded by v3 — and the recorded reason for each supersession. | The evidence edges. |

**Grounding invariant:** a canonical belief version must be GROUNDED — at least one `belief_support` edge — unless it is an explicitly declared deterministic derivation (for example `outstanding_amount = committed_amount - fulfilled_amount`, which carries a `source_kind = 'DERIVATION'` edge instead).

State Proof renders **both** grounding and lineage. The table name `belief_support` is unchanged and will not be renamed.

---

## 1. Thesis

**Institutions keep durable, structured, adversarially-useful records about people. People keep nothing comparable about institutions.** An internet provider knows your account number, your service address history, every billing period, the exact policy version in force on the day you called, the ticket ID of the call, the agent who handled it, and the retention schedule that governs all of it. That record survives staff turnover, system migrations, and the four months during which you thought about none of it. Your side of the same relationship is a mail archive you cannot search by obligation, a screenshot you took because you had a feeling, and a memory that degrades on a predictable curve. When the two records disagree, only one of them is written down in a form that can be cited. This is not a fairness complaint; it is a description of the information architecture that every consumer relationship is actually built on.

**The asymmetry has a price, and the price is paid in tail obligations.** The costly failures are not dramatic — they are the deposit that was promised "within 30 days of inspection" and quietly was not, the reimbursement that arrived at $200 against a $420 commitment and was never topped up, the cancellation that was confirmed in writing on 15 May and then billed again for June. Each individually is worth a few hundred dollars and roughly four hours of reconstructing what happened. That ratio is exactly why they go unresolved: the cost of assembling proof exceeds the expected value of the claim, so a rational person drops it, and an institution that faces a population of rational people can under-perform on tail obligations at essentially no cost. The problem is evidentiary, not motivational. People do not fail to pursue these because they forgot they cared; they fail because they cannot cheaply reconstruct *what was promised, by whom, on what date, supported by which document, and what remains outstanding right now*.

**The wedge is the unresolved obligation — and specifically the moment a new artifact contradicts, fulfills, or expires one.** Provenance is not a life assistant and does not try to remember everything about a person. It maintains exactly one thing: the user's side of open obligations with counterparties, held as versioned beliefs that are grounded in immutable evidence, projected into transactional state, and armed with prospective triggers so that a deadline passing is itself an event. The wedge is narrow on purpose, because narrowness is what makes the record citable. When a forwarded invoice arrives four months after a confirmed termination, Provenance does not summarise it — it admits it as immutable evidence, types it as a counterparty claim rather than a fact, detects that it is mutually exclusive with the canonical `service_terminated` belief, reopens the closed case in one serializable transaction, and drafts a reply whose every factual sentence carries a support ID. That is the product: **the record that makes it cheap to be right.**

---

## 2. Why this is not a chatbot and not RAG

### 2.1 Not a chatbot

A chatbot's memory is a transcript. Transcripts have three properties that disqualify them as a system of record:

- **No invariants.** Nothing prevents a transcript from containing both "your deposit was returned" and "$1,800 outstanding." A database with a `CHECK` constraint cannot hold both.
- **No revision semantics.** When a chatbot changes its mind, the old conclusion is either gone or buried in scrollback with no recorded reason. There is no answer to "when did we start believing this, and what changed?"
- **No permission boundary.** The thing that generates the sentence is the same thing that would send the email. Provenance separates them with an approval that binds to a specific `case.revision` and a specific `sha256` of the approved draft (§3, *case revision*).

A system of record must answer three questions that a transcript structurally cannot: *what did we know at time T* (record time), *what changed and why* (lineage), and *what is this conclusion based on* (grounding).

### 2.2 Not RAG

Ordinary retrieval-augmented generation has exactly one level of representation: the chunk. A document is retrieved, its text is placed in a context window, and the model writes prose. Everything that matters to an obligation is flattened away:

- A chunk that says "$186 due" and a chunk that says "service terminated 31 May" are both just chunks. Whichever ranks higher wins the answer. Contradiction is resolved by cosine similarity.
- The invoice is treated as a *fact* because it is in the index, not as a *claim by an interested party*.
- There is no write path, so there is no state to be wrong about — and no state to be right about either.
- Retracted or superseded documents keep their embeddings and keep resurfacing. (Provenance handles this explicitly; see §3, *retraction filtering*.)

Provenance keeps six levels separate. Each one is a distinct table, a distinct lifecycle, and a distinct authority to change things.

### 2.3 The six-way separation, worked

`Artifact → Evidence → Claim → Belief → Commitment → State`

The worked example below is the hero demo event: a forwarded ISP invoice for $186 covering 1–30 June, arriving on the demo clock date, four months after the cancellation case was resolved.

#### Level 1 — Artifact: the bytes that arrived

An artifact is an immutable external object. Provenance never edits it, never re-parses it in place, and identifies it by content hash so the same forward twice is one artifact.

```text
source_artifacts
  id               = 0199f2c1-...-a41d
  tenant_id        = <hero tenant>
  user_id          = <hero user>
  source_type      = 'EMAIL_INBOUND'
  s3_bucket        = provenance-artifacts-use1
  s3_key           = raw/{tenant_id}/{user_id}/0199f2c1-...-a41d/original
  content_sha256   = \x7d2f...c19a
  mime_type        = 'message/rfc822'
  sender           = 'billing@example-isp.com'
  subject          = 'Your invoice is ready — account 88-114-2039'
  received_at      = 2026-09-18T14:05:11Z
  event_time       = 2026-07-01T06:00:00Z      -- invoice issue date from the document
  parser_status    = 'PARSED'
```

What ordinary RAG does here: chunks it. What Provenance does: stores the bytes in S3, stores identity and hash in CockroachDB, and treats the content as untrusted input for the rest of the pipeline.

#### Level 2 — Evidence: immutable observations extracted from the artifact

Evidence items are atomic, immutable, span-anchored observations. They carry the embedding used for retrieval. They are never conclusions.

```text
evidence_items  (3 rows admitted from this artifact)

  id = 0199f2c2-...-1b07
  evidence_type          = 'DATE_ASSERTION'
  normalized_text        = 'Service period 2026-06-01 through 2026-06-30'
  valid_from             = 2026-06-01T00:00:00Z
  valid_to               = 2026-07-01T00:00:00Z     -- half-open [from, to)
  source_locator         = {"part":"text/plain","char_start":412,"char_end":455}
  extraction_confidence  = 0.9840
  source_authority       = 0.8800                   -- provider statement re: service_status
  embedding              = <1024-dim, amazon.titan-embed-text-v2:0, cosine>
  retraction_status      = 'ACTIVE'

  id = 0199f2c2-...-1b08
  evidence_type          = 'AMOUNT_ASSERTION'
  normalized_text        = 'Amount due USD 186.00'
  extraction_confidence  = 0.9960

  id = 0199f2c2-...-1b09
  evidence_type          = 'IDENTIFIER_ASSERTION'
  normalized_text        = 'Account 88-114-2039'
  extraction_confidence  = 0.9970
```

The account reference is what makes retrieval deterministic rather than lucky: it exact-matches `relationships.external_account_ref` before any vector search is consulted.

#### Level 3 — Claim: who asserted what, and in what capacity

A claim binds an assertion to an actor. This is the level ordinary RAG has no representation for at all, and it is the single most important level in the product.

```text
claims
  id                    = 0199f2c3-...-4e51
  case_id               = <isp-cancellation case>
  relationship_id       = <isp relationship>
  subject_type          = 'RELATIONSHIP'
  subject_id            = <isp relationship>
  predicate             = 'balance_owed'
  object_type           = 'MONEY'
  object_json           = {"amount":"186.0000","currency":"USD"}
  actor_type            = 'COUNTERPARTY'
  actor_id              = 'example-isp.com'
  evidence_id           = 0199f2c2-...-1b08
  claim_kind            = 'COUNTERPARTY_CLAIM'      -- NOT 'OBSERVATION', NOT a fact
  valid_from            = 2026-06-01T00:00:00Z
  valid_to              = 2026-07-01T00:00:00Z
  authority_score       = 0.5500                    -- provider authority for `balance_owed`
  extraction_confidence = 0.9960
```

The invoice arriving does not make $186 owed. It makes $186 **claimed**, by a party with a financial interest, about a period that begins one day after a termination the same party confirmed in writing. Every one of those qualifiers is a column.

#### Level 4 — Belief: what Provenance currently holds, versioned and grounded

A belief is a stable proposition identity. A belief *version* is its value at a point in the record. Two beliefs matter here.

```text
beliefs
  id        = 0199e8a1-...-77c2
  predicate = 'service_terminated'
  subject   = RELATIONSHIP <isp relationship>
  current_version_id -> belief_versions v2      (unchanged by this event)

  id        = 0199e8a1-...-77c3
  predicate = 'balance_owed'
  subject   = RELATIONSHIP <isp relationship>
  current_version_id -> belief_versions v2      (NEW, written by this event)
```

Lineage of `balance_owed`:

```text
belief_versions
  v1  value_json      = {"amount":"0.0000","currency":"USD"}
      epistemic_status = 'CONFIRMED'
      belief_confidence= 0.9400
      valid_from       = 2026-06-01T00:00:00Z
      recorded_at      = 2026-05-15T09:22:00Z
      superseded_at    = 2026-09-18T14:05:19Z

  v2  value_json      = {"amount":"0.0000","currency":"USD"}   -- value UNCHANGED
      epistemic_status = 'DISPUTED'                            -- status CHANGED
      belief_confidence= 0.7100
      recorded_at      = 2026-09-18T14:05:19Z
      kernel_decision_id = 0199f2c4-...-9a30
```

Grounding of `balance_owed` v2:

```text
belief_support
  (v2, EVIDENCE, <15 May termination confirmation>,  SUPPORTS,    weight 0.92, reason RC_WRITTEN_CONFIRMATION)
  (v2, EVIDENCE, <31 May service-end notice>,        SUPPORTS,    weight 0.88, reason RC_EFFECTIVE_DATE)
  (v2, CLAIM,    0199f2c3-...-4e51,                  CONTRADICTS, weight 0.55, reason RC_POST_TERMINATION_PERIOD)
```

This is the payoff of separating grounding from lineage. **Lineage** says: we used to believe $0 owed with status CONFIRMED, and we now believe $0 owed with status DISPUTED, and here is the decision that changed it. **Grounding** says: the belief still rests on two provider-authored documents, and the thing arguing against it is one interested-party claim covering a period after the termination those documents establish. A belief version can change its epistemic status without changing its value — which is precisely what an unresolved contradiction looks like, and precisely what a prose summary cannot express.

The contradiction is also materialised as a first-class row, so it is queryable, countable, and displayable rather than implicit in the edges:

```text
conflicts
  predicate        = 'balance_owed'
  left_source      = (BELIEF_VERSION, balance_owed v1)
  right_source     = (CLAIM,          0199f2c3-...-4e51)
  conflict_type    = 'VALUE_CONFLICT'
  status           = 'NEEDS_HUMAN'      -- gate H5: monetary family, exposure USD 186.00 >= 100.00
  severity         = 'HIGH'
  requires_human   = true
  canonical_belief_version_id = <balance_owed v2>
  detected_at      = 2026-09-18T14:05:19Z
```

#### Level 5 — Commitment: an obligation with a deadline and a fulfillment ledger

A commitment is a claim that creates a future obligation, plus deterministic arithmetic over what has been delivered against it. The ISP invoice is deliberately *not* a commitment — a counterparty demanding money does not create a user obligation in this system unless the user's obligation is separately admitted, and here the grounding contradicts it. The sibling relationships in the same "Move" context show what commitments actually look like:

```text
commitments  (moving company)
  obligor_type      = 'COUNTERPARTY'
  commitment_type   = 'MONETARY_REIMBURSEMENT'
  description       = 'Reimbursement for damage to dining table during 16 May move'
  currency          = 'USD'
  committed_amount  = 420.0000
  fulfilled_amount  = 200.0000
  outstanding_amount= 220.0000        -- DERIVATION, not an LLM opinion
  status            = 'PARTIAL'       -- outstanding > 0 forbids 'FULFILLED' by CHECK constraint
  due_at            = 2026-06-30T00:00:00Z

commitments  (landlord)
  commitment_type   = 'DEPOSIT_RETURN'
  committed_amount  = 1800.0000
  fulfilled_amount  = 0.0000
  outstanding_amount= 1800.0000
  due_at            = 2026-06-15T00:00:00Z   -- 30 days after the 16 May final inspection
  status            = 'ACTIVE'

fulfillments  (moving company, one row)
  evidence_id  = <bank credit notification, 12 June>
  amount       = 200.0000
  admission_status = 'ADMITTED'
  confidence   = 0.9900
```

`outstanding_amount` is computed by Python, checked by the database, and never inferred by a model. A model that "reasons" its way to $220 and a `CHECK (outstanding_amount = committed_amount - fulfilled_amount)` constraint are not the same engineering artifact, and only one of them is load-bearing.

#### Level 6 — State: the transactional projection an application can act on

State is what the dashboard reads and what an action is allowed to cite. The kernel writes all of it in **one** serializable CockroachDB transaction:

```text
BEGIN;  -- SERIALIZABLE, retried on SQLSTATE 40001

  INSERT claims           (the $186 counterparty claim)
  INSERT belief_versions  (balance_owed v2, DISPUTED)
  INSERT belief_support   (2 SUPPORTS edges, 1 CONTRADICTS edge)
  UPDATE beliefs          (current_version_id -> v2)
  INSERT conflicts        (VALUE_CONFLICT, NEEDS_HUMAN, requires_human)
  UPDATE cases            (status RESOLVED -> REOPENED,
                           revision 12 -> 13,
                           reopened_count 0 -> 1,
                           attention_level 'NONE' -> 'URGENT')
  INSERT state_transitions(RESOLVED -> REOPENED, reason CONTRADICTORY_EVIDENCE,
                           case_revision 13, kernel_decision_id, trace_id)
  INSERT kernel_decisions (decision 'ACCEPTED_WITH_CONFLICT', retry_count 0)
  INSERT outbox_events    (case.reopened.v1, aggregate_version 13, PENDING)

COMMIT;
```

Either all of that is true or none of it is. There is no window in which the case is reopened but the conflict is missing, or the conflict exists but no event will ever be published.

### 2.4 The collapse, side by side

This is Judge Mode's counterfactual toggle, and it is the single most persuasive asset in the build. The same artifact, the same model, the same prompt — retrieval and canonical memory disabled versus enabled:

| | Memory OFF (ordinary RAG) | Memory ON (Provenance) |
|---|---|---|
| Output | "Invoice for $186 due 30 June." | "This contradicts your 15 May termination confirmation. Case reopened; dispute drafted." |
| Levels used | 1 (chunk) | 6 (artifact → evidence → claim → belief → commitment → state) |
| Contradiction | invisible | `conflicts` row, `NEEDS_HUMAN`, `HIGH`, `requires_human` |
| Durable effect | none | case revision 12 → 13, one outbox event, one armed action |
| Citable | no | every factual sentence carries a `belief_version_id` |

The toggle is implemented as a request flag on the Advocate graph that (a) skips structured identity retrieval, (b) skips `agent_evidence_retrieval_v1`, and (c) passes an empty State Proof. Nothing is faked; one code path just runs with its memory removed.

---

## 3. Glossary

> **Do not confuse: Provenance / grounding / lineage.**
> **Provenance** is the product name and never appears in these documents as a common noun.
> **grounding** is the set of `belief_support` edges between a belief version and the evidence or claims that back or oppose it.
> **lineage** is the `belief_versions` chain and the recorded reason for each supersession.
> A belief is *grounded* by edges and has *lineage* through versions. If a sentence would read the same with the two words swapped, it is wrong.

Alphabetical.

**ActionIntent** — A proposed consequential side effect (in v1: one outbound email) carrying its draft, a `draft_sha256`, its supporting belief version IDs, its `basis_case_revision`, and its idempotency key; it cannot execute without explicit human approval.

**agent-safe view** — A read-only CockroachDB view (`agent_case_context_v1`, `agent_active_beliefs_v1`, `agent_evidence_retrieval_v1`) granted to `pv_agent_reader` and reachable through the CockroachDB MCP server; the SQL grant, not the prompt, is the permission boundary.

**artifact** — An immutable external object (forwarded email, PDF, screenshot, upload) stored byte-for-byte in S3 and identified in CockroachDB by `content_sha256`; row in `source_artifacts`.

**belief** — A stable proposition identity that Provenance tracks over time, such as `service_terminated(relationship)`; row in `beliefs`, holding a pointer to its current version.

**belief confidence** — How certain the current canonical belief is given all admitted evidence; computed by the kernel after admission, distinct from extraction confidence and from source authority.

**belief version** — The value, epistemic status, confidence, and validity interval of a belief at one point in the record; row in `belief_versions`, immutable once superseded.

**canonical state** — The transactional projection an application may act on: case status, commitment amounts, active conflicts, attention level; the only thing an ActionIntent is permitted to cite.

**case** — A bounded episode inside a relationship (a cancellation, a deposit return, a damage reimbursement); the primary consistency aggregate and the unit that carries `revision`.

**case revision** — A monotonically incrementing integer on `cases`, bumped exactly once per kernel commit that changes the case; approvals bind to it, so any memory change between approval and execution makes the approval stale.

**claim** — An assertion by a specific actor in a specific capacity, typed by `claim_kind` (`COUNTERPARTY_CLAIM`, `USER_CLAIM`, `POLICY_TERM`, `COMMITMENT_CLAIM`, `FULFILLMENT_CLAIM`, `CORRECTION`, `OBSERVATION`, `INFERENCE`); a claim is never automatically a belief.

**commitment** — A promised future behaviour with an obligor, an optional amount and currency, an optional deadline, and a deterministic fulfillment ledger; row in `commitments`.

**conflict** — A durable contradiction object recording two mutually exclusive sources, its type, severity, whether it requires a human, and which belief version remains canonical; row in `conflicts`, never a transient prompt string.

**context** — An optional cross-relationship grouping such as "The Move", letting several relationships and cases share one user-facing narrative; row in `contexts`.

**counterparty** — The institution or person on the other side of a relationship (ISP, landlord, mover, employer); shared reference metadata, with user-specific facts living on the relationship.

**epistemic status** — The stance Provenance takes toward a belief version: `CONFIRMED`, `PROBABLE`, `UNCERTAIN`, `DISPUTED`, `SUPERSEDED`, `RETRACTED`; can change while the value stays the same.

**evidence** — An atomic, immutable, span-anchored observation extracted from an artifact, carrying its embedding, validity interval, extraction confidence, and retraction flag; row in `evidence_items`.

**extraction confidence** — How reliably the model read the artifact text, produced by the extraction step; high extraction confidence over a weak source still yields a weak claim.

**fulfillment** — Evidence that partially or fully satisfies a commitment, with an amount or quantity and an admission status; row in `fulfillments`, unique per `(commitment_id, evidence_id)`.

**grounding** — The `belief_support` edges connecting a belief version to the evidence and claims that support, contradict, or qualify it. A canonical belief version must be grounded by at least one edge unless it is an explicitly declared deterministic derivation. **Not** the version chain — that is lineage.

**idempotency key** — A caller-supplied or generated string scoping a side-effecting operation; same key plus same request hash replays the same result, same key plus different request hash returns `409 IDEMPOTENCY_CONFLICT`; stored in `idempotency_records`.

**Judge Mode** — An inspection surface exposing four panels built from real records: consumer state, State Proof, Memory Trace, and system status — plus the Memory ON/OFF counterfactual toggle.

**lineage** — The `belief_versions` chain (v1 superseded by v2 superseded by v3) together with the recorded reason for each supersession. **Not** the evidence edges — those are grounding.

**Memory Kernel** — The deterministic, LLM-free module that validates proposals, resolves identity, detects conflicts, computes state transitions, enforces invariants, and is the only component with canonical write privileges (`pv_kernel_writer`). It must be unit-testable without Bedrock.

**Memory Trace** — The end-to-end record of how one artifact changed the system: extraction, retrieval candidates, MCP tool calls, resolver decision, proposal, kernel decision, transaction and retry count, canonical changes, outbox event, action intent, approval, execution. Rendered in Judge Mode from real rows, never from narration.

**MemoryProposal** — The typed contract an agent submits to the kernel: artifact and evidence IDs, identity candidates, proposed claims, commitments, belief mutations, conflict hints, trigger mutations, unresolved questions, and model metadata. It may never contain SQL, table names as commands, or permissions.

**outbox** — `outbox_events`, written inside the same serializable transaction as the state change it describes, then published to EventBridge by a dispatcher; the mechanism that makes "state changed" and "the world was told" impossible to disagree.

**prospective trigger** — A durable future condition with a safe predicate AST, a `not_before` time, a `basis_case_revision`, and a state of `ARMED | FIRED | DISARMED | EXPIRED`. The scheduler says "look now"; the predicate, evaluated against current canonical state, says "act or no-op".

**Provenance** — This product. A personal system of record for a user's obligations with institutions. Always a proper noun in these documents.

**record time** — When Provenance learned or committed something (`recorded_at`, `observed_at`, `created_at`). It never substitutes for valid time.

**relationship** — The long-lived link between a user and a counterparty (the ISP account, the lease), carrying the external account reference used for deterministic identity matching; row in `relationships`.

**retraction filtering** — The mandatory retrieval predicate excluding every evidence row whose `retraction_status` is not `ACTIVE`. Retracted and superseded evidence keeps its embedding for audit and historical State Proof, so without `retraction_status = 'ACTIVE'`, corrected evidence resurfaces and re-poisons downstream reasoning.

**source authority** — How authoritative a given source kind is *for a given predicate family* — a bank record for `payment_received` is near 1.0; a model inference for any external fact is near 0.0. Never a single global trustworthiness score per sender.

**State Proof** — A deterministic read model, assembled by SQL and not by a model, showing current state, its grounding (supporting and contradicting evidence with source authority), its lineage (prior versions and why each was superseded), derived values, active conflicts, and the action intents that depended on it. An LLM may summarise it; it may not replace it.

**trace id** — A UUID propagated across the entire vertical slice — API request, artifact, agent run, retrieval, proposal, kernel decision, transaction, outbox event, advocate run, approval, execution — and the join key for Memory Trace and OpenTelemetry spans.

**valid time** — When something is true in the outside world (`valid_from`, `valid_to`), stored as a half-open interval `[valid_from, valid_to)` with `NULL` meaning open-ended. A July import can describe a March fact; sorting by ingestion time would get that wrong.

---

## 4. Naming rationale

### 4.1 The candidates

| Name | Why it was considered | Why it lost |
|---|---|---|
| **NeverReset** | Names the core promise — the relationship does not restart from zero each time. | Defines the product by what it refuses to do. It is a negation, it reads as a password-manager or a factory-reset utility, and it says nothing about evidence, obligation, or trust. It also ages badly: "never" is a claim the retention section of `MEMORY_SYSTEM.md` explicitly declines to make. |
| **Recourse** | Names the user benefit precisely: you have somewhere to go. | Adversarial framing. It presumes a fight before one exists, which is the wrong default for a system whose most common outcome is "your deposit arrived, case resolved." It also implies legal remedy, which is an explicit non-goal (§6). |
| **Standing** | Elegant double meaning — legal standing plus the standing of a relationship. | Too abstract to survive first contact with a reviewer or a user. Nothing in the word suggests documents, memory, or state. Heavily overloaded in finance and in ordinary English ("standing order", "standing by"). |
| **Lineage** | Names a real and central mechanism: the versioned belief chain. | Names *one* of the two mechanisms and would have made the three-term vocabulary impossible — the product name and the technical term would be the same word, which is exactly the collision we are trying to avoid. Also the narrower of the two mechanisms: grounding is what convinces a skeptic; lineage is what convinces an auditor. |
| **Provenance** | Names what the product actually produces. | One real weakness — see §4.3. |

### 4.2 Why Provenance won

Three reasons, in order of weight.

1. **It names the output, not the mechanism or the grievance.** What the user walks away with is a chain of custody from a claim back to the document that supports it. That is a provenance record, in the same sense the word carries in art and archives: not "where the data came from" in a pipeline sense, but "the documented history that makes this object's claim credible."
2. **It survives the three-second test with a non-technical user.** "Where did this come from and who said so" is legible to someone who has never heard of RAG. Neither *Standing* nor *Recourse* is.
3. **It sets the correct expectation about failure.** A provenance record does not promise that you win. It promises that the history is intact and citable. That matches the product's honest posture — the system reopens the case and drafts the dispute; it does not adjudicate it.

The tagline carries the asymmetry that the name alone cannot: *A system of record for the institutions that already have one of you.*

### 4.3 The one real weakness

**"Provenance" is a common noun in data engineering.** W3C PROV, data-provenance literature, lineage tooling in warehouses, and the general phrase "provenance metadata" all occupy the word. A reader skimming a document could plausibly parse "Provenance detected a contradiction" as a category statement rather than a product statement, and — worse — an engineer writing a new document could reach for the lowercase word to mean "the support edges", reintroducing exactly the ambiguity the product name was supposed to resolve.

### 4.4 How the three-term vocabulary neutralises it

We remove the word from the common-noun slot entirely. The two concepts that would otherwise be called "provenance" have their own dedicated names, and neither is a synonym for the other:

- the edges between a belief version and its evidence are **grounding**;
- the chain of belief versions and their supersession reasons is **lineage**.

Because both jobs are taken, there is no sentence in this codebase where the lowercase word is the right choice. That makes the rule mechanically checkable rather than a matter of taste:

```bash
# Lint: the lowercase common noun must not appear in our own prose.
# Allowed only inside quoted external references (W3C PROV, vendor docs, URLs).
rg -n --glob 'docs/**/*.md' --glob '!docs/00_PRODUCT.md' '\bprovenance\b' \
  | rg -v 'PROV-O|W3C|https?://'
# Expected output: nothing. Any hit is a rename that was missed.
```

`00_PRODUCT.md` is excluded because this section necessarily quotes the word to explain the rule.

The database table keeps its name. `belief_support` was never called `provenance_edges`, so nothing in the schema needs to change, and the SQL vocabulary (`source_kind`, `relation`, `SUPPORTS`) already reads as grounding without using the word.

**One deliberate exception:** the phrase "chain of custody" is permitted in user-facing copy as a plain-English gloss on grounding plus lineage. It is not a defined term and does not appear in schemas or contracts.

---

## 5. Capability map and demo walkthrough

Five dimensions carry this build, and they carry it at **equal weight**. Equal weighting is the important part: a system that is technically extraordinary and impossible to understand is worth no more than one that is charming and shallow. Each row below names the specific artifact in this build that earns the dimension and the exact place in the three-minute walkthrough where it is visible.

**Walkthrough shot list (2:55 total):**

| Segment | Time | Content |
|---|---|---|
| A | 0:00–0:20 | Dashboard: "The Move" — four relationships, $2,020 outstanding, one resolved four months ago |
| B | 0:20–0:50 | Forward the June invoice; case flips RESOLVED → REOPENED live |
| C | 0:50–1:20 | State Proof: grounding (supports/contradicts) and lineage (v1 → v2, and why) |
| D | 1:20–1:45 | Judge Mode counterfactual: Memory OFF vs Memory ON, same artifact, side by side |
| E | 1:45–2:10 | Grounded draft with support IDs; Approve & Send; executor revalidates revision 13 |
| F | 2:10–2:35 | Second reveal: landlord deposit trigger fired on its own, no reminder was ever set |
| G | 2:35–2:55 | Memory Trace: MCP tool calls, vector candidates, one serializable commit, outbox event |

| Dimension | The artifact in this build that earns it | Where it is visible |
|---|---|---|
| **Agentic Memory Design** | Six separated levels with distinct tables and lifecycles (`source_artifacts` → `evidence_items` → `claims` → `beliefs`/`belief_versions` → `commitments` → `cases`), grounding via `belief_support` with `SUPPORTS`/`CONTRADICTS`/`QUALIFIES`, lineage via versioned supersession with reason codes, conflicts as durable rows, bitemporal valid/record time, and retraction filtering on the vector index so corrected evidence cannot resurface. | **C (0:50–1:20)** — State Proof shows both grounding and lineage explicitly, including a belief whose *value* did not change while its *epistemic status* did. Reinforced at **G**. |
| **Technological Implementation** | One `SERIALIZABLE` CockroachDB transaction writing claim + belief version + support edges + conflict + case reopen + revision increment + state transition + outbox event, with `40001` retry handling; CockroachDB Distributed Vector Indexing (`amazon.titan-embed-text-v2:0`, 1024-dim, cosine, `user_id`-prefixed) doing the semantic candidate work; CockroachDB Cloud Managed MCP Server serving `agent_case_context_v1` / `agent_active_beliefs_v1` / `agent_evidence_retrieval_v1` to `pv_agent_reader`; LangGraph on Bedrock AgentCore Runtime with `anthropic.claude-opus-5` for reasoning and `anthropic.claude-haiku-4-5` for extraction. | **G (2:35–2:55)** — Memory Trace surfaces the actual MCP tool calls and the views they hit as first-class trace nodes, plus vector candidate counts and the single commit with its revision transition. Setup shown at **B**. |
| **Real-World Impact** | A concrete, monetary, universally recognised loss: $1,800 deposit unreturned past its promised deadline, $220 outstanding on a $420 damage reimbursement, and a $186 charge for service the provider itself confirmed terminated. Nothing in the demo is a toy domain. | **A (0:00–0:20)** — the dashboard states the money at stake in the first ten seconds, before any technology is mentioned. Closed at **F** when the deposit surfaces itself. |
| **Product Readiness** | Cognito human app client plus M2M client-credentials clients with scoped resource servers (`provenance.memory/propose`, `provenance.action/execute`, …); five separated SQL roles (`pv_migrator`, `pv_app_reader_writer`, `pv_kernel_writer`, `pv_agent_reader`, and the read-only `pv_ops_reader` used by trace verification); approval bound to `basis_case_revision` **and** `approval_draft_sha256` so any memory change between approval and send makes the approval stale; idempotency keys on every side-effecting endpoint; transactional outbox with backoff and SQS DLQ; `processed_events` consumer dedupe; OpenTelemetry spans and CloudWatch metrics on every stage; architectural prompt-injection containment (the Interpreter has no send tool and no write privilege, so a malicious PDF has no capability path). | **E (1:45–2:10)** — the executor visibly revalidates case revision 13 and the draft hash before sending, and the UI shows the approval was bound to a specific state. Panel D of Judge Mode shows commit, retry count, outbox delivery, and model route live. |
| **Creativity & Originality** | The Memory ON/OFF counterfactual: the same artifact, the same model, the same prompt, run with retrieval and canonical memory disabled versus enabled, rendered side by side — "Invoice for $186 due 30 June" against "Contradicts your 15 May termination confirmation — case reopened, dispute drafted." Plus prospective memory that fires on elapsed time against current state, producing an alert the user never asked for and never scheduled. | **D (1:20–1:45)** for the counterfactual — the single most persuasive twenty-five seconds in the video. **F (2:10–2:35)** for the unprompted trigger. |

Two rules for the walkthrough: never show a fabricated commit or trace, and never let infrastructure precede the product moment. The viewer should feel the "wait, it reopened the case" reaction at **B** before being shown a single database concept.

---

## 6. Non-goals

Taken from `ARCHITECTURE.md` §5 and preserved deliberately. Each carries the reason it is excluded, because a non-goal without a reason gets quietly re-adopted by the next contributor.

- **Full Gmail mailbox ingestion.** OAuth mailbox scopes turn a demo into a compliance project and put the entire ingestion surface on a review path this build cannot complete inside its schedule. Upload-first with SES inbound layered on later gives the same evidence with a fraction of the blast radius.
- **Autonomous legal advice.** Provenance asserts what the record contains, never what a person is entitled to. The moment the system characterises an entitlement it inherits an obligation to be right about jurisdiction, and the honest posture — "here is the documented history, cited" — is both safer and more defensible.
- **Autonomous financial decisions.** No payment, no dispute filing, no chargeback initiation. Every consequential action passes through the human approval gate, and money movement is a category where an idempotency bug is not a retry, it is a loss.
- **Background web browsing of arbitrary institutions.** Scraping counterparty portals introduces unauthenticated, unversioned, untyped content of unknown authority into the evidence plane — precisely the input the source-authority model is designed to keep out.
- **Universal life-assistant behaviour.** The value comes from the narrowness. A system that remembers everything about a person cannot maintain a per-predicate authority model or a meaningful invariant set, and it dilutes the one thing this product does that nothing else does.
- **Broad robotic process automation.** Clicking through counterparty UIs on the user's behalf is a brittle, permission-hostile capability that would need its own safety model, and it earns nothing on any of the five dimensions in §5.
- **Full policy/legal interpretation engine.** Provenance stores `POLICY_TERM` claims with valid-time intervals and surfaces which version applied when. It does not resolve what those terms *mean* in contested cases; that is human work, and pretending otherwise would put unearned confidence into a State Proof.
- **Cross-border regulatory compliance implementation.** GDPR/CCPA erasure, data residency, and lawful-basis tracking are real production requirements. The architecture reserves room for them — tombstones, belief recomputation when support is erased, tenant-scoped S3 paths — but implementing them now would consume the entire build budget for zero demonstrable v1 value.
- **Multi-region active-active AWS application compute.** CockroachDB's multi-region capability is real and we will describe the production topology truthfully; standing up active-active application compute for a demo-scale workload would be theatre, and claiming it without deploying it would be a lie a reader could catch.

The system stays narrow: **maintain the user's relationship state and safely continue from the record.**

---

## 7. Risks and decided posture

**R1 — The name collides with a common noun, and lint only covers our own prose.** §4.4 removes the lowercase word from our documents, but a reviewer, a blog post, or a future integration doc will still use "provenance" generically, and the CockroachDB MCP tooling and OpenTelemetry ecosystems both use it. *Mitigation:* the three-term table appears in §0 of this document and is restated in the glossary; every UI string uses "grounding" or "lineage" and never the bare word; the lint runs in CI. *Residual risk:* moderate and permanent. We accept it because the alternative names were worse on the dimensions that matter more.

**R2 — The counterfactual toggle is the most persuasive asset and also the easiest to accuse of being rigged.** A side-by-side where one side is obviously worse invites the question "did you nerf it?" *Mitigation:* Memory OFF must run the identical graph, identical model (`anthropic.claude-opus-5`), identical prompt, and identical artifact, differing only in that retrieval returns empty and State Proof is empty. **Decision:** keep the request-payload diff as a live Q&A artifact; do not spend eight seconds of the three-minute video on it.

**R3 — Belief-status-without-value-change is subtle and may not land in twenty seconds.** The `balance_owed` v1→v2 transition — same `$0.00`, `CONFIRMED` → `DISPUTED` — is the most intellectually interesting thing in the data model and the hardest to explain quickly. *Mitigation:* State Proof renders status as the visual primary and value as secondary for disputed beliefs. *Risk:* a reader takes "$0.00 → $0.00" for "nothing happened." Consider a one-line caption in the UI: "the amount did not change; our confidence in it did."

**R4 — Retraction filtering is a correctness requirement disguised as a demo detail.** Non-active evidence retains its embedding in the distributed vector index. If any retrieval path forgets `retraction_status = 'ACTIVE'`, corrected evidence resurfaces and grounds a belief on a fact the user already disowned — a silent, plausible-looking failure. *Mitigation:* the filter lives inside `agent_evidence_retrieval_v1` and the repository query; a golden test seeds a retracted item that ranks top-1 by cosine and asserts it is absent. **Decision:** `RETRACTED`, `SUPERSEDED`, and `QUARANTINED` evidence are excluded from new retrieval and new grounding, while remaining visible in historical State Proof.

**R5 — "System of record" is a strong claim and retention policy undercuts it.** `MEMORY_SYSTEM.md` §23 explicitly declines to promise "remember forever," and the production direction includes user-initiated deletion. *Mitigation:* market continuity with user control, never permanence; preserve minimal audit where legally permissible; recompute beliefs that lose their grounding rather than leaving them dangling. **Decision:** a belief that loses all grounding becomes `RETRACTED` with a tombstoned support edge; it is never silently deleted.

**R6 — The wedge may be too narrow to be a business and too broad to be a demo.** Obligations with institutions is a real category, but the per-user event rate is low — a handful of qualifying artifacts a year — which is excellent for correctness and poor for engagement. *Mitigation:* the "Move" context bundles four relationships so the demo has density. **Decision:** v1 is consumer-facing; professional advocates remain a post-v1 discovery hypothesis and do not change v1 contracts.

**R7 — Authority scores are configuration presented as knowledge.** The predicate-aware authority bands in `02_DATA_MEMORY_TRANSACTIONS.md` §6 are hand-set numbers. They will be read as if they were measured. *Mitigation:* the kernel uses explicit rules for high-value predicates and treats the numeric band as a tiebreaker, not an oracle; the eval corpus tests conflict outcomes, not scores. *Assumption stated plainly:* the initial bands are engineering judgement, not empirical calibration, and the documents say so wherever the table appears.

**R8 — Single-tenant demo, multi-tenant claims.** The build seeds at least two tenants to prove vector-search isolation in tests, but the UI only ever shows one. A reader cannot see isolation working. *Mitigation:* the cross-tenant retrieval test is part of the required database test list and its result can be shown on request; the `user_id`-prefixed vector index makes isolation a schema property rather than a query-authoring discipline. *Residual risk:* low technically, moderate rhetorically.
