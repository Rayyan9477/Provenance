# Provenance — Visual Design Brief for a Separate Opus 5 Session

Purpose: carry a ready-to-paste, self-contained prompt that commissions the complete visual product design for Provenance from a separate Claude Opus 5 session that has no access to this repository.

Status: planning complete v1.1
Implementation status: not started

Audience: the project owner, who will run the design session; and the frontend implementation team, who will receive the result and build it against `specs/15_API_SPEC.md`.

---

## 1. How to use this

### 1.1 What this file is

Section 3 of this file is a prompt, not documentation. It is written to be selected and pasted wholesale into a fresh Claude Opus 5 conversation. Everything the design session needs is inside it: the thesis, the hero data, the screen list, the hard problems, the anti-requirements, and the output contract. It deliberately contains **no file paths, no repository references, and no cross-document pointers**, because the receiving session will not have them and a dangling reference is the fastest way to get a hallucinated design system back.

Section 4, "Risks and open questions", is for us and is **not** part of the prompt. Do not paste it.

### 1.2 Exactly what to paste

Select from the line reading `=== BEGIN PROMPT` to the line reading `=== END PROMPT` inclusive, and paste that into a new conversation. Nothing above it, nothing below it. The prompt is self-delimiting and does not need a preamble from you.

Recommended session settings:

| Setting | Value | Why |
|---|---|---|
| Model | Claude Opus 5 | The brief asks for original visual direction and long self-contained HTML. A smaller model reverts to the generic aesthetic the brief forbids. |
| Attachments | None | The prompt is complete. Attaching our specifications leaks internal vocabulary that would show up as UI copy. |
| Extended thinking | On, if offered | Direction selection and the grounding/lineage rendering problem both benefit from it. |
| New conversation | Required | A session with prior context will inherit that context's aesthetic defaults. |

### 1.3 What you should get back, in order

**Turn 1 — directions only.** The prompt instructs the session to propose 3 to 4 distinct visual directions, one line of rationale each, and then to **stop and wait**. If it returns a full design on turn 1, it ignored the instruction; reply with `You skipped the direction step. Give me 3 to 4 directions with one-line rationales and stop.` and do not accept the unsolicited design — it will be the median of all four directions and will read as generic.

Judge the directions on one question only: *would a person under mild financial stress trust this in three seconds, and would a database engineer respect it in thirty?* Reject any direction whose rationale is about taste rather than about that question.

**Turn 2 — the full design.** After you name a direction, expect: a design-token table, a component inventory with states, all seven screens at desktop and mobile, the memory OFF/ON counterfactual view, the five system states (empty, loading, error, stale, forbidden), and self-contained HTML/CSS with per-component implementation notes.

The full design will be long. If output is truncated, ask for it screen by screen in this order: Relationship Dashboard, State Proof, Contradiction Panel, Judge Mode counterfactual, Action Approval Inbox, Case Timeline, Memory Trace Inspector. That order is by demo value, so a truncated deliverable still covers the video.

### 1.4 How to iterate

Iterate by **naming the artifact and the failure**, never by restating taste. Effective revision requests look like:

- `The CLAIM badge and the EVIDENCE badge are distinguishable by hue only. Make them distinguishable with hue removed.`
- `The reopened-case banner reads as an error. Rewrite it so the dominant feeling is relief, keeping the same information.`
- `The lineage rail does not communicate that the value stayed at $0.00 while the status changed. Solve that specific case.`
- `Judge Mode reads as a debug console. It must read as an instrument panel on a working machine.`

Freeze the token table early. Ask for token **additions** in later turns, never renames — every rename costs us a search-and-replace across the implementation.

### 1.5 What to do with the result

1. Save the returned HTML/CSS into this repository under `docs/frontend/` as static reference pages. They are design reference, not application code, and must not be imported by the Next.js application.
2. Transcribe the token table into the application's token layer as the single source of truth. Tokens are the contract between the design and the implementation; the HTML is only a witness to them.
3. Reconcile every rendered data field against `specs/15_API_SPEC.md`. **The design session invents field labels; it does not invent fields.** Any field in the returned design that has no API source is a design fiction and must be removed or added to the API deliberately.
4. Re-check the vocabulary lint: the returned copy must use "grounding" and "lineage" as distinct terms, must never use the product name as a common noun, and may use "chain of custody" as the plain-English gloss.

### 1.6 Two honesty notes before you paste

- **Provisional display labels.** Three demo display names are canonical in our specifications and appear in the prompt as-is: the user **Alex Rivera**, the ISP **Northline Fiber**, and the landlord **Harborview Property Management**. Two are **not** yet canonical anywhere and appear in the prompt as provisional seed labels: the mover **Beltline Movers** and the employer **Kestrel Analytics**. If the seed specification later names them differently, they are a find-and-replace in the returned HTML and nothing more.
- **Nothing is built.** The prompt describes a system that is fully specified and not yet implemented. It is written in the present tense because that is how you brief a designer, but do not let the returned deliverable be described anywhere as a screenshot of a running product.

---

## 2. Why the brief is shaped this way

Three structural choices in the prompt are deliberate and should survive any editing you do to it.

**It leads with the thesis, not the screens.** A designer who understands memory asymmetry will independently invent the right visual metaphors for grounding and lineage. A designer given only a screen list will produce a competent admin dashboard, which is exactly the failure mode we are paying to avoid.

**It names the hard problems as the interesting part.** Seven specific rendering problems are called out as the reason the brief exists. This reframes the engagement from decoration to problem-solving and is the single highest-leverage paragraph in the prompt.

**It forces a direction choice before the full design.** Without that gate, a model asked for a complete design system produces the safe average of every direction it considered. The gate costs one round trip and buys genuine differentiation.

---

## 3. The prompt

=== BEGIN PROMPT (copy from this line) ===

You are the lead product designer for a product called Provenance. I need the complete visual design for it. Read this entire brief before responding, and follow the process instruction at the end: your first reply is **directions only**.

## 1. What Provenance is

### 1.1 The thesis: memory asymmetry

Institutions keep durable, structured, adversarially useful records about people. People keep nothing comparable about institutions.

Your internet provider knows your account number, your service address history, every billing period, the exact policy version in force on the day you called, the ticket ID of that call, the name of the agent who handled it, and the retention schedule governing all of it. That record survives staff turnover, system migration, and the four months during which you thought about none of it.

Your side of the same relationship is a mail archive you cannot search by obligation, a screenshot you took because you had a feeling, and a memory that degrades on a predictable curve. When the two records disagree, only one of them is written down in a form that can be cited.

This asymmetry has a price, and the price is paid in small tail obligations: the deposit promised "within 30 days of inspection" that quietly was not returned, the reimbursement that arrived at $200 against a $420 commitment and was never topped up, the cancellation confirmed in writing on 15 May and then billed again for June. Each is worth a few hundred dollars and roughly four hours of reconstructing what happened. That ratio is exactly why they go unresolved. The problem is evidentiary, not motivational. People do not fail to pursue these because they stopped caring. They fail because they cannot cheaply reconstruct *what was promised, by whom, on what date, supported by which document, and what is still outstanding right now*.

Provenance is the record that makes it cheap to be right.

### 1.2 What the product actually does

Provenance is a personal system of record for a person's open obligations with institutions. It is not a life assistant and does not try to remember everything about a person. It maintains one thing well: the user's side of unresolved obligations with counterparties.

A user forwards or uploads a document. The system keeps the original bytes forever, unmodified. It extracts small immutable observations from that document. It records who asserted what, in what capacity. It maintains versioned conclusions that are always traceable back to the documents behind them. It tracks obligations with deadlines and a running ledger of what has been delivered against them. It arms future conditions so that a deadline passing is itself an event, with no reminder set by the user. And when the user wants to act, it drafts a message in which every factual sentence is attached to the specific record that backs it, then requires explicit human approval before anything is sent.

The system holds six distinct levels, and keeping them separate is the entire product:

| Level | What it is | Example from the demo |
|---|---|---|
| Artifact | The original bytes that arrived, stored unmodified forever | A forwarded email containing an invoice PDF |
| Evidence | An atomic, immutable observation lifted out of that artifact, anchored to the exact character range it came from | "Service period 1 June through 30 June 2026" |
| Claim | An assertion by a specific actor in a specific capacity. **A claim is never automatically a fact.** | Northline Fiber asserts a balance of USD 186.00 |
| Belief | What Provenance currently holds, versioned, with every version traceable to the records behind it | "Balance owed: $0.00", now marked DISPUTED |
| Commitment | A promise with a deadline and a fulfillment ledger | Deposit return, $1,800, promised within 30 days of inspection |
| State | The transactional summary an application may act on: case status, amounts, open contradictions, attention level | Case status: REOPENED, revision 13 |

An ordinary retrieval-and-summarise system has exactly one level: the chunk. A chunk that says "$186 due" and a chunk that says "service terminated 31 May" are both just chunks; whichever ranks higher wins the answer, and a contradiction gets resolved by text similarity. Provenance never lets an invoice become a fact merely by existing.

### 1.3 Four rules that are visible in the interface

These four rules govern the system and each one has to be legible on screen. If a user cannot feel these from the interface, the design has failed.

1. **Evidence is append-only.** Nothing admitted is ever rewritten or deleted. Corrections arrive as new evidence that supersedes old evidence; the old evidence stays visible in history with a status marker.
2. **Beliefs are revisable.** When a conclusion changes, a new version is created, and the previous version and the reason it was superseded are preserved forever.
3. **State is transactional.** The system can never be caught half-updated. A case is never reopened without the contradiction that reopened it, and vice versa.
4. **Actions are permissioned.** Nothing is sent without an explicit human approval, and the approval is bound to an exact version of the record. If anything changes between approval and send, the approval goes stale and the send is refused.

### 1.4 Vocabulary you must use exactly

Three terms are load-bearing. Getting them wrong makes the product incoherent.

- **Provenance** is the product name. Always capitalised, always a proper noun, always the name of this product. It is never used as an ordinary noun meaning "where data came from" — that sense of the word is banned outright, and the next two terms exist precisely so you never need it.
- **grounding** means the links between a conclusion and the specific evidence and claims that **support**, **contradict**, or **qualify** it. Grounding answers: *what is this based on?*
- **lineage** means the chain of versions of a conclusion — version 1 superseded by version 2 superseded by version 3 — together with the recorded reason for each change. Lineage answers: *what did we used to think, and what changed?*

Grounding and lineage are two different things and must be visually distinct. If a sentence would read the same with the two words swapped, it is wrong.

You may use the plain-English phrase **"chain of custody"** in user-facing copy as a friendly gloss for grounding plus lineage. That is the only permitted informal substitute.

Additional required terms, spelled exactly this way in the interface:

- **evidence**, **claim**, **belief**, **commitment**, **case**, **conflict**, **counterparty**
- **case revision** — a number that increments every time the record for a case changes
- **attention level** — one of `NONE`, `INFO`, `ATTENTION`, `URGENT`
- **case status** — one of `OPEN`, `WAITING`, `ACTIONABLE`, `IN_PROGRESS`, `DISPUTED`, `BLOCKED`, `AWAITING_USER`, `RESOLVED`, `REOPENED`, `SUPERSEDED`
- **claim kind** — one of `OBSERVATION`, `COUNTERPARTY_CLAIM`, `USER_CLAIM`, `COMMITMENT_CLAIM`, `POLICY_TERM`, `FULFILLMENT_CLAIM`, `CORRECTION`, `INFERENCE`
- **epistemic status** of a belief version — one of `CONFIRMED`, `PROBABLE`, `UNCERTAIN`, `DISPUTED`, `SUPERSEDED`, `RETRACTED`
- **support relation** on a grounding link — one of `SUPPORTS`, `CONTRADICTS`, `QUALIFIES`
- **commitment status** — one of `PROPOSED`, `ACTIVE`, `PARTIAL`, `DISPUTED`, `FULFILLED`, `EXPIRED`, `SUPERSEDED`

These are real enumerated values in the system. You may design a friendly display label above each one, but the machine value must be visible somewhere on the surface that shows it, because part of the audience needs to see that these are typed values and not prose.

## 2. The hero scenario, in full

This is the story the design must carry. Design against this data. Do not invent different data.

### 2.1 The setup

Four months ago the user, **Alex Rivera**, moved apartments. Four relationships from that move are grouped into a single context called **"The Move"**. Total outstanding across the context: **$2,020**.

| Counterparty | Kind | What is open | Money | Status |
|---|---|---|---|---|
| **Harborview Property Management** | Landlord | Security deposit return, promised within 30 days of the 16 May final inspection, due 15 June 2026 | $1,800 committed, $0 returned, **$1,800 outstanding** | Commitment `ACTIVE`, overdue |
| **Beltline Movers** | Moving company | Reimbursement for damage to a dining table during the 16 May move | $420 committed, $200 paid on 12 June, **$220 outstanding** | Commitment `PARTIAL` |
| **Northline Fiber** | ISP | Cancellation of the old apartment's service. Confirmed in writing 15 May 2026; termination effective 31 May 2026 | Nothing outstanding | Case `RESOLVED` four months ago |
| **Kestrel Analytics** | Employer | Relocation expense reimbursement | Settled | Case `RESOLVED` |

The current date in the product is **18 September 2026, 14:05 UTC**.

### 2.2 The event

At 14:05 on 18 September, Alex forwards an email into Provenance. It is an invoice from Northline Fiber:

- Invoice number **88431**
- Amount **USD 186.00**
- Service period **1 June through 30 June 2026**
- Account ending **4417**
- Issued 1 July 2026, forwarded by Alex on 18 September 2026

That service period begins one day after a termination that the same company confirmed in writing.

### 2.3 What the system does, step by step

1. The forwarded email is stored byte-for-byte and identified by its content hash. Forwarding it twice produces one artifact, not two.
2. Three immutable evidence items are lifted out of it, each anchored to the exact characters it came from — a `DATE_ASSERTION`, an `AMOUNT_ASSERTION`, and an `IDENTIFIER_ASSERTION` (`00_PRODUCT.md` §2.3).
3. The account number **exact-matches** the stored account reference for the Northline Fiber relationship. This match is deterministic and happens *before* any similarity search is consulted. Similarity search then supplies additional candidates, advisory only.
4. Retrieval over **16,035** of the user's own evidence records returns 20 candidates, narrowed to 7. Two records are excluded because they were retracted or superseded — retracted evidence keeps its text and its search index entry forever, but is never allowed to ground a new conclusion.
5. The invoice is recorded as a **`COUNTERPARTY_CLAIM`**, not as a fact. The invoice arriving does not make $186 owed. It makes $186 **claimed**, by a party with a financial interest, about a period that begins the day after a termination that same party confirmed.
6. The system detects that this claim is mutually exclusive with what it currently holds, and records the contradiction as a durable object: type `VALUE_CONFLICT`, status `NEEDS_HUMAN`, severity `HIGH`, requires human review.
7. **One single all-or-nothing database transaction** writes: the claim, a new belief version, the grounding links, the contradiction record, the case status change from `RESOLVED` to `REOPENED`, the case revision going from **12 to 13**, the state transition record, and one outbound event. Either all of it is true or none of it is. There is no moment when the case is reopened but the contradiction is missing.
8. The interface tells Alex: **"This relationship was closed. New evidence reopened it."**
9. The system drafts a reply in which every factual sentence carries the identifier of the record that backs it.
10. Alex reviews the draft, edits it if she wants, and clicks **Approve & Send**. The approval is bound to case revision 13 and to a hash of the exact draft text.
11. Before sending, the system re-checks that the case is still at revision 13 and the draft still hashes to the approved value. Only then does it send.

### 2.4 The most important detail in the whole product

The belief `balance_owed` for Northline Fiber goes from version 1 to version 2:

```
version 1   value: $0.00        status: CONFIRMED   confidence: 0.94
            recorded 15 May 2026, superseded 18 September 2026

version 2   value: $0.00        status: DISPUTED    confidence: 0.71
            recorded 18 September 2026   <- current
```

**The value did not change. The confidence in it did.** That is precisely what an unresolved contradiction looks like, and it is the single hardest thing in this product to communicate. A viewer who reads "$0.00 → $0.00" and concludes "nothing happened" has missed the entire point.

Version 2's grounding, which is what makes the disputed status legible:

| Relation | Source | Weight | Reason |
|---|---|---|---|
| `SUPPORTS` | Evidence: written cancellation confirmation, 15 May 2026, from the provider itself | 0.92 | Written confirmation from the counterparty |
| `SUPPORTS` | Evidence: service-end notice, effective 31 May 2026, from the provider itself | 0.88 | Explicit effective date |
| `CONTRADICTS` | Claim: the $186 invoice, asserted by the provider, financially interested | 0.55 | Covers a period after termination |

Read that table aloud and the product explains itself: *the conclusion still rests on two documents the provider itself wrote, and the only thing arguing against it is one interested-party claim about a period after the termination those documents establish.* Weight and source authority are different from confidence: authority is how much a given kind of source is worth **for this particular kind of statement** — a bank record is near-authoritative about a payment being received and worthless about what a lease says.

### 2.5 The second reveal

While Alex is looking at the ISP case, the dashboard shows something she never asked for. The landlord deposit case has raised itself, because a condition armed months ago came true on its own: the promised 30 days elapsed on 15 June, and $1,800 is still outstanding. Nobody set a reminder. The system woke itself on elapsed time evaluated against current state.

The headline the system generates for this, deterministically and without a language model:

> The promised 30 days elapsed and USD 1,800.00 is still outstanding.

## 3. Who this is for — and this is unusual

The design has to serve two audiences at once, and the order matters absolutely.

### 3.1 Audience one: a person under mild financial stress

Someone who has just realised they are being billed for service they cancelled, or that a deposit they need has not come back. Not a crisis, but not neutral either — the state of mind is low-grade dread plus the suspicion that pursuing it is not worth the hours. They are not technical. They have never heard of a vector index and never will. They must trust this product **instantly**, because a system-of-record product that is not trusted in the first ten seconds is deleted.

What earns that trust: calm, specific, monetary, and legible. Real amounts and real dates in the first screenful. A tone that is composed rather than urgent. The visible fact that nothing is hidden and nothing was thrown away. The unmistakable message that **they are in control** and the software will not act on its own.

What destroys it: alarm colouring, exclamation marks, gamification, anything that resembles a notification demanding action, or anything that implies the software has already done something on their behalf.

### 3.2 Audience two: hackathon judges who are database and AI engineers

The same interface is judged in a competition by engineers evaluating five equally weighted criteria: memory design, technical implementation, real-world impact, product readiness (security, observability, scalability, resilience, access control), and creativity. They will look for production seriousness and they will notice its absence immediately. They want to see typed values, version numbers, transaction boundaries, permission boundaries, retry counts, and honest failure states.

### 3.3 The rule that resolves the tension

**Earn emotional trust first, reveal technical depth on demand, never the reverse.**

Every screen has a calm consumer surface. Every screen has technical depth available one deliberate interaction away — a disclosure, a detail panel, a mode toggle. The identifiers, statuses, revision numbers, and weights exist on every screen; they are simply not the first thing the eye lands on.

Concretely: a consumer sees "Confirmed in writing by Northline Fiber on 15 May". One click away, an engineer sees the evidence identifier, the character range it was extracted from, the source authority of 0.90, the extraction confidence of 0.98, and the retraction status. Same component, two depths, no separate "advanced" application.

This is the central design problem of the product. Solve it once, in the component system, and every screen inherits the solution.

## 4. The seven screens

Design all seven, at desktop and mobile. These are the complete set — there is no eighth surface, and none of these may be dropped as "obvious".

| # | Screen | Emotional job in one word |
|---|---|---|
| 4.1 | Login | trustworthiness |
| 4.2 | Dashboard — "The Move" | relief |
| 4.3 | Case detail and timeline | orientation |
| 4.4 | State Proof | verifiability |
| 4.5 | Action approval | agency |
| 4.6 | Judge Mode — Memory Trace, system status, counterfactual | technical respect |
| 4.7 | Upload and forward | effortlessness |

Two of these are easy to under-invest in and both are load-bearing. **Login** is the first thing anyone sees, including a judge, and a generic login screen sets the wrong expectation for everything after it. **Upload and forward** is where the product's only real input arrives; if adding evidence feels like clerical work, nobody builds the record that makes the rest possible.

### 4.1 Login

**Emotional job:** trustworthiness. "This is going to hold things that matter to me."

This is a single-purpose screen and the temptation is to give it no thought. Resist that. It carries the product's whole promise in one view, before any data exists to be impressive with.

**Information hierarchy:** the product name and the promise in one sentence; the sign-in control; and nothing else competing with them. No feature tour, no marketing carousel, no testimonial.

The design problem: convey *durability and discretion* — this is a place records are kept safely — without the visual language of a bank or a security vendor, which reads as cold and adversarial. The user arriving here is mildly stressed about money. The screen should feel like a well-kept archive, not a safe.

Design the error state (rejected credentials) and the returning-session state. Neither may blame the user.

### 4.2 Dashboard — "The Move"

**Emotional job:** relief. "Nothing was lost. Here is exactly what is still owed to you." This is the first ten seconds and it must state the money before it states anything about technology.

**Information hierarchy:**
1. The context **"The Move"**, 4 relationships, **$2,020 total outstanding**
2. Cases needing attention, ordered by attention level, each with a one-line generated headline: *"A new invoice contradicts your recorded cancellation."* / *"The promised 30 days elapsed and USD 1,800.00 is still outstanding."*
3. The four relationships with counterparty name, what is open, outstanding amount, attention level, and last activity
4. Counters: unresolved commitments, active conflicts, drafts awaiting approval, armed future conditions
5. A quiet, always-available way to add evidence — forward or upload

Note: relationships whose case is closed can still carry `URGENT` attention. "Closed" and "quiet" are different things, and the dashboard must not conflate them.

**Empty-state consideration:** a first-run dashboard has no relationships. It must still communicate the thesis.

### 4.3 Case detail and timeline

**Emotional job:** orientation. "Here is the whole story of this case, in order, and I can see who said what."

**Information hierarchy:** case header (title, counterparty, status, revision, attention), then a reverse-chronological merged stream. Each entry has: an actor (`USER`, `COUNTERPARTY`, `KERNEL`, `AGENT`, `SCHEDULER`, `EXECUTOR`, `SYSTEM`), a timestamp, a case revision number, a headline, and expandable detail.

Entry kinds you must style: `ARTIFACT_RECEIVED`, `EVIDENCE_ADMITTED`, `CLAIM_RECORDED`, `BELIEF_CHANGED`, `CONFLICT_OPENED`, `CONFLICT_RESOLVED`, `COMMITMENT_CREATED`, `COMMITMENT_UPDATED`, `FULFILLMENT_ADMITTED`, `STATE_TRANSITION`, `TRIGGER_ARMED`, `TRIGGER_FIRED`, `TRIGGER_NOOP`, `ACTION_PROPOSED`, `ACTION_APPROVED`, `ACTION_REJECTED`, `ACTION_EXECUTED`, `ACTION_FAILED`, `USER_CORRECTION`.

The visual distinction that matters most: **what a counterparty asserted** versus **what the system deterministically did** versus **what a language model proposed**. Three different kinds of authorship in one stream, and a user must never be confused about which is which.

### 4.4 State Proof

**Emotional job:** verifiability without intimidation. "I can see why it believes this, and I could check it myself if I wanted."

This is the most important screen in the product. It is generated entirely from the database with no language model involved, and it must still be correct and renderable when every model in the system is unavailable.

**Information hierarchy:** the conclusion in plain language, then **grounding** (supporting and contradicting sources, each with its source authority, the exact text extracted, where in the document it came from, and when it was observed), then **lineage** (each previous version, its value, its status, when it was superseded and why), then derived values, then open contradictions, then any pending action that depends on this state.

Grounding and lineage must be **visually distinct structures**, not two lists with different headings. They answer different questions and a user must be able to tell at a glance which question they are looking at.

Derived values need their own treatment. `outstanding_amount = committed_amount - fulfilled_amount` is arithmetic performed by code and enforced by a database constraint. It is not a model opinion, and it is exempt from needing evidence because it is a calculation. Show the inputs, the expression, and the result, and make it feel like arithmetic rather than judgement.

Include a rare but real state: a conclusion with **no** supporting evidence. This should be impossible and is treated as a serious data-integrity alarm. Design the warning that appears when it happens.

#### 4.4.1 The contradiction panel — a component, not a screen

This appears inside both the case detail (4.3) and State Proof (4.4). It is called out separately here because it is the hardest single component in the product, not because it is an eighth screen. Design it once, as a component that sits correctly in both hosts.

**Emotional job:** composure. "Two records disagree. That is a normal, expected, handled situation, and the system is holding both without pretending to resolve it."

**Information hierarchy:** the two sides presented as peers, each with its source, its authority, its date, and its exact wording; then the type (`VALUE_CONFLICT`, `TEMPORAL_CONFLICT`, `AUTHORITY_CONFLICT`, `IDENTITY_CONFLICT`, `COMMITMENT_WITHDRAWAL_CONFLICT`, `FULFILLMENT_CONFLICT`, `POLICY_VERSION_CONFLICT`), the severity (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), whether it needs a human, and — crucially — **which side remains canonical while the contradiction is open**.

That last part is the design problem. The system does not flip to the newest assertion. It keeps holding its position, marks it disputed, and says so. The panel must communicate "we are still standing on this, and here is the thing arguing against it" rather than "we do not know".

### 4.5 Action approval

**Emotional job:** agency and safety. "I am in control. Nothing goes out without me, and I can see exactly what it is based on."

**Information hierarchy:** the drafted message, with **each factual sentence visibly attached to the record that backs it**; the recipient; the requested outcome; the tone; the unresolved risks the system is honest about; and the approval control.

Critical mechanics to design:
- Sentence-level grounding inside prose. Each factual sentence carries one or more support identifiers and a validated flag. A draft containing an unvalidated sentence can be reviewed but can never be approved.
- The **staleness** state. Approval binds to case revision 13 and to a hash of the exact draft text. If the record changes to revision 14 before sending, the approval is stale and the send is refused with a clear explanation and a diff of what changed. This is a safety feature and it must feel like one, not like an error.
- Honest risk disclosure. The draft carries lines like *"The provider may hold a distinct final-period charge that is contractually valid."* The product does not claim the user is right. It claims the record is intact and citable.
- The recipient is deliberately not editable. Design that as a stated guarantee rather than a greyed-out field.

### 4.6 Judge Mode — Memory Trace, system status, and the counterfactual

**Emotional job:** technical respect. "This is an X-ray of a working system", not "this is a debug console."

Judge Mode is one permission-gated screen with four panels built entirely from real records: consumer state, State Proof, **Memory Trace** (detailed immediately below), and system status. The memory OFF/ON comparison described in §5 is reached from here. System status covers the transaction commit and its retry count, outbound event delivery, model routing, and current health.

The difference between an X-ray and a debug console is the difference between an instrument panel and a log dump. An X-ray is composed, deliberately laid out, shows structure rather than text, and is designed to be read by someone who did not build the machine. Judge Mode must be presentable on a projector, must be readable in a screen recording, and must never look like something that leaked out of a developer's terminal.

#### 4.6.1 The Memory Trace panel

**Emotional job:** credibility. "This is a real machine that did real work, and I can see where the model stopped and the deterministic logic started."

Every element here comes from persisted records of an actual run. Nothing is animated narration.

**Information hierarchy:** for each trace that materially changed the case — the headline, the case revision before and after, the outcome decision and its reason codes, the retry count, the memory operations performed with counts, the retrieval statistics, the database tool calls with the role and access mode they ran under, and the model calls with their model identifiers.

Real values from the hero run:

```
Kernel decision      ACCEPTED_WITH_CONFLICT
Reason codes         MUTUAL_EXCLUSION_DETECTED, CASE_REOPEN_QUALIFIED
Serialization retries 0
Case revision        12 -> 13

Memory operations    evidence admitted        6
                     claim recorded           1   COUNTERPARTY_CLAIM
                     conflict opened          1   VALUE_CONFLICT
                     case reopened            1
                     state transition         1
                     outbound event           1

Retrieval            corpus scoped to user    16,035
                     similarity candidates    20
                     after re-ranking         7
                     exact identifier hits    1
                     retracted excluded       2
                     results from other users 0
                     embedding model          amazon.titan-embed-text-v2:0
                     distance                 cosine

Database tool calls  1  query_agent_case_context      view agent_case_context_v1        44 ms   1 row
                     2  query_agent_evidence_search   view agent_evidence_retrieval_v1  128 ms  20 rows
                     3  query_agent_active_beliefs    view agent_active_beliefs_v1      31 ms   6 rows
                     all under database role pv_agent_reader, access mode READ_ONLY

Model calls          extract_structured_evidence   tier E   anthropic.claude-haiku-4-5
                     strong_resolution             tier R   anthropic.claude-opus-5
```

The database role and access mode are shown deliberately: the permission boundary is enforced by database grants, not by instructions to a model. A **denied** call is rendered visibly rather than hidden. Design that denied state; it is a feature.

The line that must be visually obvious: **where the language model stopped proposing and the deterministic engine started committing.** The models never write to the database. They emit a typed proposal; a deterministic component is the only thing with write access. Make that boundary a real visual element, not a footnote.

### 4.7 Upload and forward

**Emotional job:** effortlessness. "Getting something into the record costs me nothing."

This is the product's only real input surface, and everything else in the system is downstream of it. If it feels like filing paperwork, the record never gets built and none of the other six screens ever have anything to show.

**Information hierarchy:** the drop target for a file; the user's private forwarding address, presented so it can be copied in one action and understood without explanation; and a short, honest statement of what happens next.

Design problems specific to this screen:

- **Two ingestion routes, one mental model.** A user can drag a PDF in, or forward an email to a private address that belongs only to them. These are the same act — "add this to the record" — and must not look like two different features. The forwarding address is an opaque token, not a readable name; design how an unreadable string is presented so it still feels legitimate rather than suspicious.
- **The wait is real and must be honest.** Adding evidence is not instantaneous — the artifact is stored, parsed, interpreted, matched against existing memory, and only then does anything change. Design the progression through those stages truthfully. Do not show a fake progress bar, and do not imply the record has been updated before it has.
- **Duplicate detection is a success, not an error.** Forwarding the same invoice twice is normal and expected behaviour, and the system deduplicates on content hash. The response is "you already have this, here it is" — a reassurance, with a link to where it already lives. It must never read as a rejection or a failure.
- **Rejection must be specific.** Unsupported file types and oversized files are refused. Say precisely what is accepted and what the limit is, at the moment of failure, without jargon.
- **The empty first-run state.** A brand-new user lands here with nothing. This is the moment the product either explains itself or loses them.

## 5. The counterfactual view — this is the centrepiece

Design this as its own view. It is the single most persuasive twenty-five seconds of the product.

The same artifact is processed twice by the **same model, with the same prompt, in the same code path**. The only difference is that one run has retrieval and canonical memory switched off. Nothing is faked and nothing is nerfed; one code path simply runs with its memory removed.

**Memory OFF**

```
Output           "Invoice for $186 due 30 June."
Classification   ROUTINE_INVOICE
Case linked      none
Contradictions   0
Recommended      none
Corpus visible   0 records
Model            anthropic.claude-opus-5
Duration         4,120 ms
Why              Without retrieval the document is self-describing: a valid invoice with a due date.
```

**Memory ON**

```
Output           "Contradicts your 15 May termination confirmation — case reopened, dispute drafted."
Classification   COUNTERPARTY_CLAIM_CONTRADICTING_CANONICAL
Case linked      Old ISP cancellation, RESOLVED -> REOPENED
Contradictions   1
Recommended      Outbound dispute email
Corpus visible   16,035 records
Model            anthropic.claude-opus-5
Duration         9,420 ms
```

**Difference**

```
Contradictions found      0  ->  1
Cases reopened            0  ->  1
Actions recommended       0  ->  1
Evidence recalled from    0  ->  126 days ago
Verdict                   Memory OFF treated a contradiction as a routine bill.
```

**Safety block — render this, do not hide it.** A judge must be able to see that the demonstration did not modify the memory it is demonstrating:

```
Memory OFF wrote canonical state              no
Memory OFF admitted evidence                  no
Memory OFF had access to the write tool       no
Case revision changed by this comparison      no
```

The design problem: the two sides must read as **peers running under identical conditions**, so nobody can accuse the comparison of being rigged. If the OFF side is visually degraded — dimmer, smaller, greyed — the whole demonstration reads as a strawman and the credibility is lost. Make identical conditions visible in the layout itself, and let the difference come entirely from the content.

Also design its `RUNNING` and `PARTIAL` states. Both runs take several seconds and either one can fail independently.

## 6. The hard design problems

This is the interesting part of the brief and the reason it exists. Anyone can lay out a dashboard. These seven are what I am actually commissioning. Address each one explicitly in your response and tell me what you did.

### Problem 1 — A claim must read as a claim, not a fact, everywhere it appears

An invoice arriving does not make money owed. It makes money **claimed**, by an interested party. This distinction is the ethical and technical core of the product, and it has to survive at every scale: in a dense timeline row, in a one-line dashboard headline, inside a sentence of drafted prose, and in a detail panel.

A claim carries an actor, a capacity, and an authority weighting for that specific kind of statement. An observation and a counterparty claim are not the same thing and must never look the same.

The naive solution is a coloured badge. Badges fail at small sizes, fail in a screen recording, fail for colour-blind viewers, and are ignored after five minutes of familiarity. Find something structural. Attribution that is part of the shape of the component rather than an ornament on it.

Test your solution against: a claim quoted inside a drafted email sentence; a claim shown next to an evidence item in a grounding list; and a claim in a 32-pixel-tall timeline row.

### Problem 2 — A contradiction must not read as an error or a failure

When two sources disagree, the system is working correctly. It found the thing it exists to find. But every visual convention available — red, warning triangles, alert banners — says "something broke".

Design a contradiction treatment that means *"two records disagree and I am holding both"*, is clearly more significant than ordinary content, is clearly not a malfunction, and does not deploy the same vocabulary as a real system error. You will also need a genuine error state elsewhere in the system, so whatever you choose here must remain distinguishable from that.

### Problem 3 — Grounding and lineage must be understandable by a non-technical person

A person must be able to look at a conclusion and understand **why the system believes it**, without knowing what a graph edge or a version chain is.

Grounding is: *what is this based on?* Several sources, each either supporting, contradicting, or qualifying the conclusion, each with a different authority for this specific kind of statement. A provider's own written confirmation is near-authoritative about whether service was cancelled. That same provider's invoice is weakly authoritative about whether money is owed, because they are the interested party.

Lineage is: *what did we used to think and what changed?* A chain of versions, each with a value, a status, a time it was recorded, and a reason it was replaced.

They are different shapes of information and must be different shapes on screen. And they must compose: on the State Proof screen a user moves between them constantly, so they cannot be two unrelated visual languages.

The specific case that will break a naive design: **version 1 was $0.00 CONFIRMED, version 2 is $0.00 DISPUTED.** The value did not move. The confidence did. A design that shows lineage as a sequence of values will render this as "$0.00 → $0.00" and look like nothing happened. Solve exactly this case and show me your solution for it.

### Problem 4 — "This was closed, and new evidence reopened it" must feel like relief and control

The literal event is bad news: a case the user thought was finished is open again and there is a new $186 charge. But the **product** event is good news, and it is the moment the entire thesis pays off: the system remembered something from four months ago that the user had entirely stopped tracking, and it caught it automatically.

The design has to carry: *you are not back at square one — you are further ahead than you were, because the record held.*

Get this wrong in the alarming direction and the product feels like another source of stress. Get it wrong in the celebratory direction and it feels tone-deaf about someone's money. Find the register between those, and design the transition — this is one of the few moments where motion is genuinely load-bearing, because the state change happens live in front of the user.

Note that the same treatment must work for the landlord case, where the system raised something with no external trigger at all. That version has an extra beat to communicate: *nobody set a reminder; this woke up by itself.*

### Problem 5 — The memory OFF/ON comparison is the product's centrepiece

Covered in section 5. The design constraint restated: **both sides must look like fair fights.** Identical framing, identical prominence, identical typography, identical treatment. The persuasion must come entirely from the content, and the layout must visibly support the claim that only one variable changed.

### Problem 6 — Judge Mode must read as an X-ray, not a debug console

A debug console dumps text and expects the reader to already know the system. An X-ray reveals structure to someone encountering it for the first time. Judge Mode contains genuinely technical content — role names, view names, retry counts, millisecond durations, model identifiers — and none of it may be dumbed down. But it must be **composed**: hierarchy, alignment, deliberate density, and a visible narrative from "an email arrived" to "one transaction committed and one event was published".

It also has to survive being screen-recorded at 1080p and played at speed. Thin hairlines, low-contrast greys, and 11-pixel monospace all disappear in video compression.

### Problem 7 — Time must be represented honestly

The system tracks two independent kinds of time and constantly needs to show both:

- **valid time** — when something is true in the outside world. The invoice covers 1 June through 30 June 2026.
- **record time** — when the system learned it. That invoice arrived on 18 September 2026.

They routinely disagree, sometimes by months. A document that arrives in September can describe a fact from June, and sorting by arrival date would get the story wrong. Validity is also an interval and can be open-ended: "terminated effective 31 May, still true today".

Design a treatment that shows both without doubling the visual weight of every timestamp, that makes a period-versus-instant distinction obvious, that renders an open-ended interval honestly, and that makes it immediately visible when a document's coverage period sits **after** the event it contradicts — which is the exact fact that makes the hero contradiction obvious to a human.

## 7. What I need delivered

### 7.1 Design tokens

A complete token set, named systematically, presented as a table and defined as CSS custom properties.

- **Colour.** Full light and dark palettes. Every semantic role covered: surfaces at several depths, borders, primary and secondary text, and specific roles for the semantics of this product — supporting evidence, contradicting evidence, qualifying evidence, claim attribution, deterministic system output, model-generated output, superseded and retracted content, and the four attention levels `NONE`, `INFO`, `ATTENTION`, `URGENT`. Dark mode is not an afterthought; the demo will be recorded in one of the two themes and both must be finished.
- **Type.** A full scale with sizes, line heights, weights, and letter spacing. Include a data/tabular treatment for amounts and identifiers, and a display treatment for the amounts on the dashboard.
- **Spacing.** One consistent scale.
- **Radii.** A small set with a stated rule for which is used where.
- **Elevation.** Shadows or their alternative, defined in both themes. Shadows that only work on white are a common and disqualifying failure.
- **Motion.** Durations and easing curves, plus a named token for the case-reopen transition specifically. Every motion token must have a documented reduced-motion behaviour.

### 7.2 Component inventory

Every component with all its states: default, hover, focus-visible, active, disabled, loading, empty, error, and where applicable stale, superseded, retracted, and denied.

At minimum:

`AttentionChip` · `CaseHeader` · `CaseStatusBadge` · `ClaimAttribution` · `EvidenceCard` · `BeliefCard` · `GroundingList` (supports / contradicts / qualifies, with authority) · `LineageChain` · `ConflictPair` · `DerivationBlock` · `CommitmentMeter` (committed / fulfilled / outstanding) · `TimelineEntry` (all kinds from section 4.2) · `ActorTag` · `TimeRange` (valid time and record time) · `DraftReview` · `GroundedSentence` · `SupportReference` · `ApprovalBar` · `StalenessNotice` · `TraceNode` · `DatabaseToolCallRow` · `ModelCallRow` · `RetrievalStats` · `CounterfactualPanel` · `SafetyAssertionList` · `EmptyState` · `SkeletonBlock` · `ErrorState` · `ForbiddenState` · `FixtureModeBanner` (a permanent, unmissable banner shown whenever the system is running on fixture data rather than live processing — this is an honesty requirement and cannot be subtle).

### 7.3 Screens

All seven from section 4, plus the counterfactual view from section 5. Each at desktop (1440 wide) and mobile (390 wide). The mobile designs are not scaled-down desktop; State Proof and Judge Mode in particular need genuine rethinking at 390 pixels rather than horizontal scrolling.

### 7.4 System states

Design all five for every screen where they can occur, and show them explicitly for at least the dashboard, State Proof, and the approval inbox:

- **Empty** — no relationships yet; no evidence yet; a case with no contradictions. Empty states must still teach the thesis.
- **Loading** — this system has genuinely slow operations. Model work takes seconds. The deterministic state change completes long before the drafted prose does, and the interface must show real state immediately rather than blocking the whole screen behind the slowest thing on it. Design partial loading, not a spinner over everything.
- **Error** — a model was unavailable; a database call failed; a send failed and will be retried. Errors must be specific and must never lose the user's work.
- **Stale** — the approval no longer matches the current record. This is a safety mechanism working correctly. Show what changed.
- **Forbidden** — the viewer is not permitted to see this. Judge Mode is permission-gated. Note that the system deliberately returns "not found" rather than "forbidden" for objects belonging to other users, so that the interface never confirms another person's data exists. Design both the honest forbidden state and the deliberately uninformative not-found state.

### 7.5 Accessibility, as a product-readiness requirement

Part of the audience is scoring this product on production seriousness, and accessibility is part of that score.

- WCAG 2.2 AA contrast for all text and all meaningful non-text elements, in **both** themes.
- No information conveyed by colour alone. Supports versus contradicts, and claim versus evidence, must each be distinguishable with all colour removed. Include a grayscale proof of at least the grounding list and the contradiction pair.
- Visible focus states on every interactive element. Keyboard reachability for every action, especially approval.
- Meaningful reading order and heading structure; ARIA only where a native element cannot do the job.
- A stated reduced-motion behaviour for every motion token, including the case-reopen transition.

## 8. Anti-requirements — do not do these

These are not stylistic preferences. Each one is a specific failure I have seen and will reject.

1. **No generic AI-product aesthetic.** No sparkle icons, no "thinking" shimmer, no orb, no gradient mesh backdrop, no glassmorphism, no assistant avatar. Nothing that signals "a language model made this". The product's whole argument is that the deterministic parts are what make it trustworthy.
2. **No purple-on-dark gradients.** Violet-to-indigo on near-black is the default aesthetic of every AI demo of the last several years and it makes this product invisible in a field of competitors.
3. **No Inter, no Roboto, no `system-ui` default stack, no Helvetica or Arial.** Choose real typefaces with a point of view and name them explicitly with weights. If you cannot embed the actual font files, say so plainly, specify the exact typefaces and a documented fallback stack, and note that we will self-host them. Do not silently substitute a default and do not pretend a fallback is the design.
4. **No cookie-cutter admin dashboard.** No fixed left icon rail plus top bar plus card grid of statistics. That layout says "internal tool" and this is a personal record about a person's own money.
5. **No chat interface.** There is no message thread, no input box at the bottom, no conversational turn-taking anywhere. The product's explicit argument is that a transcript is not a system of record: a transcript has no constraints, no revision semantics, and no permission boundary. A chat interface would contradict the product in its own layout.
6. **No fabricated data implying capabilities the product does not have.** Do not draw bank-account balances, credit scores, legal advice, "we filed a dispute for you", automated portal logins, calendar integrations, contact lists, or document previews of files that do not exist in the scenario. Do not show the system taking an action without approval. The product drafts and asks; it does not act, adjudicate, or advise on entitlement. Every number on every screen must come from section 2 of this brief or be an obvious structural placeholder.
7. **No decorative stock illustration and no icon-per-row noise.** Icons must carry meaning; if an icon appears on every row it carries none.
8. **No dark patterns around approval.** The approve action is never pre-selected, never the only visible path, and never styled to make rejection feel like a mistake.

## 9. Output format

The result has to be directly implementable by an engineering team working in a modern component framework, so:

1. **Self-contained HTML with embedded CSS.** No external stylesheets, no CDN links, no remote fonts, no remote images, no network requests of any kind. Any illustration or icon is inline SVG. Any raster asset is a base64 data URI. The pages must render correctly opened directly from a local file with no internet connection.
2. **Structure.** One page per screen is fine, and a single page with clearly separated screen sections is also fine — state which you are giving me. Include a light/dark toggle that works: define the light palette on `:root`, redefine the tokens under both `prefers-color-scheme: dark` and an explicit `[data-theme="dark"]` attribute so the toggle wins in both directions, and give the page body an explicit background from a token.
3. **A design-token table** in markdown: token name, light value, dark value, and what it is for. This table is the contract between your design and our implementation, so name tokens systematically and keep names stable across revisions.
4. **Per-component implementation notes** in markdown: the states the component has, which tokens it consumes, its layout behaviour from 390 to 1440 pixels, its accessibility requirements, and any non-obvious interaction. Where a component renders a typed value from section 1.4, say which values map to which visual treatment.
5. **Semantic, realistic markup.** Class names an engineer would keep. No inline styles except where a token genuinely must be set per instance. Structure the HTML the way a component tree would be structured, so the translation is mechanical.
6. **A short design rationale** at the top: the direction chosen, the typographic decision, the colour logic, and one paragraph for each of the seven hard problems in section 6 explaining what you did about it.

## 10. Process — do this first

**Do not produce the full design yet.**

Your first reply must contain **3 to 4 distinct visual directions**, each with:

- a name,
- one line of rationale — specifically, why this direction earns instant trust from a person under financial stress *and* technical respect from a database engineer,
- the typeface pairing it implies,
- the colour logic in one sentence,
- and the one hard problem from section 6 that this direction is best positioned to solve.

Genuinely distinct means distinct: different typographic voices, different colour logic, different structural metaphors. Four variations on a neutral grey design system is one direction with four accent colours, and I will send it back.

Then **stop and wait for me to choose.** Do not produce tokens, components, or screens in your first reply.

=== END PROMPT (copy up to this line) ===

---

## 4. Risks and open questions

**R1 — Two demo display labels are invented here and are not canonical anywhere.** "Beltline Movers" and "Kestrel Analytics" do not appear in any authoritative document; "Alex Rivera", "Northline Fiber", and "Harborview Property Management" do. *Mitigation:* they are flagged as provisional in §1.6 and appear only as display strings, never as identifiers. *Action:* the seed specification should adopt these two names or replace them in one edit before seeding begins, so the design and the fixtures agree.

**R2 — Source documents disagree on three demo numbers, and this brief follows the higher authority.** `00_PRODUCT.md` fixes the demo clock at `2026-09-18T14:05:00Z`, the mover's remaining balance at $220 against a $420 commitment, and the mover's deadline at 30 June 2026. Examples in `specs/15_API_SPEC.md` are internally dated 5 June 2026, and `implementation/05_RELIABILITY_EVAL_DEMO.md` §14 shows the mover line as "$420 overdue". The prompt uses the `00_PRODUCT.md` figures because it holds product authority. *Residual risk:* if the returned design is compared against an API example, the timestamps will not match. *Action:* reconcile the example timestamps in the API specification to the canonical demo clock, or explicitly annotate them as illustrative.

**R3 — The conflict predicate differs between documents.** `00_PRODUCT.md` §2.3 records the hero conflict on `balance_owed` with the left side being `balance_owed` v1; the State Proof example in `specs/15_API_SPEC.md` §8.11 shows a conflict on `service_terminated`. This brief follows `00_PRODUCT.md`. Both are defensible readings of the same event, but only one can be seeded. *Action:* pick one before the seed is written; the design does not depend on which, but the demo narration does.

**R4 — "Fair fight" framing of the counterfactual is in tension with visual storytelling.** The brief forbids visually degrading the memory-OFF side, which is correct for credibility, but a perfectly symmetrical layout may not read as decisive in a three-minute video. *Assumption made:* content asymmetry plus the explicit difference block carries the persuasion without layout asymmetry. If the returned design is symmetrical but flat on camera, the fix is emphasis in the difference block, never dimming the OFF side.

**R5 — The dual-audience rule is asserted but untested.** "Emotional trust first, technical depth on demand" is a design principle, not a measured finding. No usability testing is planned within the hackathon window. *Residual risk:* moderate. *Mitigation:* the disclosure mechanism is a single reusable component, so if the depth threshold is set wrong it is one component change rather than seven screen changes.

**R6 — The typography requirement and the no-network requirement conflict.** Rejecting Inter, Roboto, and system stacks while forbidding external font requests means the design session must either embed licensed font files as base64 or hand back a specification we self-host. The prompt instructs it to be explicit about which. *Action:* budget for a font licence, or accept a high-quality open-licence pairing that is not on the forbidden list. Do not let this be resolved silently by falling back to a system stack.

**R7 — The design session cannot validate any field against the API contract.** Because the prompt is deliberately self-contained, the receiving session will name fields from the brief's prose and may invent plausible ones. *Mitigation:* §1.5 step 3 makes field reconciliation a mandatory intake step. *Residual risk:* low but certain to require rework on at least one screen; State Proof is the most likely place.

**R8 — Seven screens plus eight component-state matrices plus two breakpoints is a large single deliverable.** Truncation is likely. *Mitigation:* §1.3 gives a priority order for a screen-by-screen request. *Open question:* whether to commission Judge Mode and the Memory Trace Inspector as a separate second engagement, since they share almost no components with the consumer surfaces and have a different audience. Current posture: keep them in one engagement so the token system stays unified, and split only if turn 2 truncates badly.

**R9 — Motion for the case-reopen transition is specified but its trigger timing is not.** The state change commits in the deterministic path well before the drafted prose returns from the model, so the interface has two arrival moments, not one. The brief tells the designer about partial loading but does not fix the choreography. *Open question:* does the reopen animation play on the state commit, or is it held until the draft is ready so the user sees one composed moment? Recommended posture: play on commit, because withholding a committed state change to improve a transition would misrepresent when the system actually knew something.
