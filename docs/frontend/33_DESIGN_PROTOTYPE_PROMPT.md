# Design commission — Provenance

You are the lead product designer for a product called **Provenance**. I am commissioning the complete visual design for it: tokens, components, and every screen, at desktop and mobile.

Read this entire brief before you respond. Then follow the process instruction in section 14: **your first reply is directions only.** Do not produce tokens, components, or screens until I have chosen a direction.

Everything you need is in this brief. There is nothing else to consult and nothing to ask for before you can start. Where a number, a name, a date, or a typed value appears below, it is real and it is fixed — use it exactly, and do not invent alternatives.

---

## 1. What Provenance is

### 1.1 The thesis: memory asymmetry

Institutions keep durable, structured, adversarially useful records about people. People keep nothing comparable about institutions.

Your internet provider knows your account number, your service address history, every billing period, the exact policy version in force on the day you called, the ticket ID of that call, the name of the agent who handled it, and the retention schedule governing all of it. That record survives staff turnover, system migration, and the four months during which you thought about none of it.

Your side of the same relationship is a mail archive you cannot search by obligation, a screenshot you took because you had a feeling, and a memory that degrades on a predictable curve. When the two records disagree, only one of them is written down in a form that can be cited.

This asymmetry has a price, and the price is paid in small tail obligations: the deposit promised "within 30 days of inspection" that quietly was not returned; the reimbursement that arrived at USD 200 against a USD 420 commitment and was never topped up; the cancellation confirmed in writing on 15 May and then billed again for June. Each is worth a few hundred dollars and roughly four hours of reconstructing what happened. That ratio is exactly why they go unresolved.

The problem is evidentiary, not motivational. People do not fail to pursue these because they stopped caring. They fail because they cannot cheaply reconstruct *what was promised, by whom, on what date, supported by which document, and what is still outstanding right now*.

Provenance is the record that makes it cheap to be right. Its one-line promise: **a system of record for the institutions that already have one of you.**

### 1.2 What the product actually does

Provenance is a personal system of record for one person's open obligations with institutions. It is not a life assistant and does not try to remember everything about a person. It maintains one thing well: the user's side of unresolved obligations with counterparties, and the moment a new document contradicts, fulfills, withdraws, or expires one of them.

A user forwards or uploads a document. The system keeps the original bytes forever, unmodified. It extracts small immutable observations from that document, each anchored to the exact characters it came from. It records who asserted what, in what capacity. It maintains versioned conclusions that are always traceable back to the documents behind them. It tracks obligations with deadlines and a running ledger of what has been delivered against them. It arms future conditions so that a deadline passing is itself an event, with no reminder set by the user. And when the user wants to act, it drafts a message in which every factual sentence is attached to the specific record that backs it, then requires explicit human approval before anything is sent.

### 1.3 The six levels, worked

The system holds six distinct levels. Keeping them separate is the entire product. Each is a different kind of object with a different lifecycle and a different authority to change things.

| # | Level | What it is | The worked example |
|---|---|---|---|
| 1 | **Artifact** | The original bytes that arrived, stored unmodified forever and identified by a hash of their content. | A forwarded email from Northline Fiber, subject "Invoice 88431 — account ••••8802", received 18 September 2026. |
| 2 | **Evidence** | An atomic, immutable observation lifted out of that artifact, anchored to the exact character range it came from, never a conclusion. | "Service period 2026-06-01 through 2026-06-30", extracted from characters 412–455 of the plain-text part. |
| 3 | **Claim** | An assertion by a specific actor in a specific capacity. **A claim is never automatically a fact.** | Northline Fiber asserts `balance_owed` = USD 186.00 for that period. Actor type `COUNTERPARTY`. Authority for this kind of statement: 0.5500. |
| 4 | **Belief** | What Provenance currently holds. A stable proposition with an ordered chain of versions, each version traceable to the records behind it. | `balance_owed` for the Northline Fiber relationship: version 1 was USD 0.00 `CONFIRMED`; version 2 is USD 0.00 `DISPUTED`. |
| 5 | **Commitment** | A promise with an obligor, an optional amount, a deadline, and a fulfillment ledger. Amounts are arithmetic, not opinion. | Deposit return: USD 1,800.00 committed, USD 0.00 fulfilled, USD 1,800.00 outstanding, due 15 June 2026. |
| 6 | **State** | The transactional summary an application may act on and an action is allowed to cite: case status, amounts, open contradictions, attention level, revision number. | Case status `REOPENED`, revision 13, attention `URGENT`, one open conflict. |

An ordinary retrieval-and-summarise system has exactly one level: the chunk. A chunk that says "USD 186 due" and a chunk that says "service terminated 31 May" are both just chunks; whichever ranks higher wins the answer, and a contradiction gets resolved by text similarity. Provenance never lets an invoice become a fact merely by existing. **That refusal is the product, and the interface is where it either becomes legible or does not.**

### 1.4 Four rules that must be visible in the interface

These four rules govern the system. Each one has to be legible on screen. If a user cannot feel these from the interface, the design has failed.

1. **Evidence is append-only.** Nothing admitted is ever rewritten or deleted. Corrections arrive as new evidence that supersedes old evidence; the old evidence stays visible in history with a status marker.
2. **Beliefs are revisable.** When a conclusion changes, a new version is created, and the previous version and the reason it was superseded are preserved forever.
3. **State is transactional.** The system can never be caught half-updated. A case is never reopened without the contradiction that reopened it, and vice versa.
4. **Actions are permissioned.** Nothing is sent without an explicit human approval, and the approval is bound to an exact version of the record and an exact hash of the text. If anything changes between approval and send, the approval goes stale and the send is refused.

There is a fifth structural rule that shows up constantly in Judge Mode (section 4.6) and matters to half the audience: **language models propose; a deterministic engine decides, commits, and acts.** The models emit a typed proposal and have no write access to the record at all. Where that boundary sits must be a visible element in the design, not a footnote.

### 1.5 Vocabulary you must use exactly

Three terms are load-bearing. Getting them wrong makes the product incoherent.

- **Provenance** is the product name. Always capitalised, always a proper noun, always the name of this product. It is never used as an ordinary noun meaning "where data came from" — that sense of the word is banned outright, and the next two terms exist precisely so you never need it.
- **grounding** means the links between a conclusion and the specific evidence and claims that **support**, **contradict**, or **qualify** it. Grounding answers: *what is this based on?*
- **lineage** means the chain of versions of a conclusion — version 1 superseded by version 2 superseded by version 3 — together with the recorded reason for each change. Lineage answers: *what did we used to think, and what changed?*

Grounding and lineage are two different things and must be **visually distinct structures**. If a sentence would read the same with the two words swapped, it is wrong.

You may use the plain-English phrase **"chain of custody"** in user-facing copy as a friendly gloss for grounding plus lineage. That is the only permitted informal substitute, and at most once per screen.

The user-facing headings for the two regions are **"What this rests on"** (grounding) and **"How this changed"** (lineage). The technical terms appear in tooltips, in detail disclosures, and in Judge Mode, where the audience wants them.

Additional required terms, spelled exactly this way in the interface: **evidence**, **claim**, **belief**, **belief version**, **commitment**, **fulfillment**, **case**, **case revision**, **conflict**, **counterparty**, **relationship**, **context**, **attention level**, **source authority**, **extraction confidence**, **belief confidence**, **weight**.

Four of those numbers are routinely confused and must never be merged into a single "trust" indicator:

| Number | What it means | Where it lives |
|---|---|---|
| **weight** | How much one grounding link counts toward the conclusion. | On each grounding link. |
| **source authority** | How authoritative this *kind of source* is for this *kind of statement*. A bank record is near 1.0 about a payment being received and near worthless about what a lease says. | On each source. |
| **extraction confidence** | How reliably the system read the document text. High confidence reading a weak source still yields a weak claim. | On each evidence item. |
| **belief confidence** | How certain the current conclusion is, given everything admitted. | On each belief version. |

All four render with four decimal places, exactly as stored — `0.9200`, never `92%`. A percentage implies a frequency interpretation these numbers do not have. Alongside every source authority figure, an info control offers this fixed sentence, which may be restyled but not dropped: *"How authoritative this kind of source is for this kind of claim. These bands are engineering judgement, not measurement."*

### 1.6 The enumerated values you must style

These are real typed values in the system, not labels I made up for the design. You may design a friendly display label for each, but **the machine value must remain visible on the surface that shows it**, because part of the audience needs to see that these are typed values and not prose. Design the full set — a value you do not style will appear on screen unstyled.

**Case status** — `OPEN`, `WAITING`, `ACTIONABLE`, `IN_PROGRESS`, `DISPUTED`, `BLOCKED`, `AWAITING_USER`, `RESOLVED`, `REOPENED`, `SUPERSEDED`.

**Attention level** — `NONE`, `INFO`, `ATTENTION`, `URGENT`. Suggested labels: "No action needed", "For information", "Worth a look", "Needs attention now". The word `URGENT` appears only as the literal value beside its neutral label, never as a standalone alarm.

**Claim kind** — `OBSERVATION`, `COUNTERPARTY_CLAIM`, `USER_CLAIM`, `COMMITMENT_CLAIM`, `POLICY_TERM`, `FULFILLMENT_CLAIM`, `CORRECTION`, `INFERENCE`.

**Epistemic status** of a belief version — `CONFIRMED`, `PROBABLE`, `UNCERTAIN`, `DISPUTED`, `SUPERSEDED`, `RETRACTED`.

**Support relation** on a grounding link — `SUPPORTS`, `CONTRADICTS`, `QUALIFIES`.

**Commitment status** — `PROPOSED`, `ACTIVE`, `PARTIAL`, `DISPUTED`, `FULFILLED`, `EXPIRED`, `SUPERSEDED`.

**Conflict type** — `VALUE_CONFLICT`, `TEMPORAL_CONFLICT`, `AUTHORITY_CONFLICT`, `IDENTITY_CONFLICT`, `COMMITMENT_WITHDRAWAL_CONFLICT`, `FULFILLMENT_CONFLICT`, `POLICY_VERSION_CONFLICT`.

**Conflict severity** — `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.

**Conflict status** — `OPEN`, `AUTO_RESOLVED`, `NEEDS_HUMAN`, `RESOLVED`, `SUPERSEDED`.

**Action state** — `PROPOSED`, `NEEDS_REVIEW`, `APPROVED`, `REJECTED`, `EXECUTING`, `EXECUTED`, `FAILED_RETRYABLE`, `FAILED_FINAL`, `CANCELLED`, `CANCELLED_STALE`.

**Retraction status** on an evidence item — `ACTIVE`, `SUPERSEDED`, `RETRACTED`, `QUARANTINED`. Only `ACTIVE` evidence may ground a new conclusion; the other three keep their text and their search-index entry forever and are shown in history with a status badge and the line "Shown for history. This was not used to reach the current conclusion."

**Trigger type** — `COMMITMENT_DEADLINE`, `RESPONSE_DEADLINE`, `CONFLICT_TIMEOUT`, `WARRANTY_WINDOW`.

**Trigger state** — `ARMED`, `FIRED`, `DISARMED`, `EXPIRED`. **Trigger evaluation result** — `FIRED`, `NO_OP`, `DISARMED`, `EXPIRED`, `ERROR`.

**Actor of a timeline entry** — `USER` ("You"), `COUNTERPARTY` (the institution's own name, plus "the other side"), `KERNEL` ("Provenance record change"), `AGENT` ("Proposed by a model"), `SCHEDULER` ("Scheduled check"), `EXECUTOR` ("Send action"), `SYSTEM`.

One typographic consequence: `SCREAMING_SNAKE_CASE` values appear beside sentence-case labels everywhere in this product. Decide early how those two registers coexist. Treating the machine values as ugly and hiding them is not available to you — they are half the credibility of the interface.

---

## 2. The hero scenario, in full

This is the story the design must carry. Design against this data. **Do not invent different data, different people, or different institutions.** Every number on every screen you produce must come from this section or be an obvious structural placeholder.

### 2.1 The setup

Four months ago the user, **Alex Rivera**, moved apartments. Her timezone is `America/New_York`. Four relationships from that move are grouped into a single context titled **"The Move — 214 Ridgeway to 88 Larkin"** (short form "The Move" in headings). Total outstanding across the context: **USD 2,020.00**.

| Counterparty | Kind | What is open | Money | Status at the start |
|---|---|---|---|---|
| **Harborview Property Management** | Landlord | Security deposit return, promised "within 30 days" of the 16 May 2026 final inspection, due 15 June 2026 | USD 1,800.00 committed, USD 0.00 returned, **USD 1,800.00 outstanding** | Commitment `ACTIVE`, past its promised date |
| **Beltline Movers** | Moving company | Reimbursement for damage to a dining table during the 16 May move | USD 420.00 committed, USD 200.00 paid on 12 June, **USD 220.00 outstanding** | Commitment `PARTIAL`, due 30 June 2026 |
| **Northline Fiber** | Internet provider | Cancellation of the old apartment's service. Confirmed in writing 15 May 2026; termination effective 31 May 2026 | Nothing outstanding | Case `RESOLVED` four months ago, revision 12, attention `NONE` |
| **Kestrel Analytics** | Employer | Relocation expense reimbursement, USD 2,350.00 | Settled in full | Case `RESOLVED`, commitment `FULFILLED` |

Two further relationships exist outside this context and matter to the design because they are traps: a **second Northline Fiber account** for the new address (account `NF-9913-2250`, distinct from the old apartment's `NF-4471-8802`), and a **Cascade Power** electricity account. Same counterparty name, two different relationships. Any design that keys identity on the counterparty's name — a logo, a colour per brand, a grouped header — will merge them and silently attach the wrong document to the wrong account. The interface must make *which relationship* legible, not just which institution.

The current date and time in the product is **18 September 2026, 14:05 UTC** — 10:05 in Alex's timezone. Against the deposit's 15 June deadline that is **95 days** past the promised date.

### 2.2 The event

At 14:05 on 18 September, Alex forwards an email into Provenance. It is an invoice from Northline Fiber:

- Invoice number **88431**
- Amount **USD 186.00**
- Service period **1 June through 30 June 2026**
- Account **`NF-4471-8802`** — the *old* apartment's account, shown masked as `••••8802` in lists
- Issued 1 July 2026, forwarded by Alex on 18 September 2026

That service period begins one day after a termination that the same company confirmed in writing.

### 2.3 What the system does, step by step

1. The forwarded email is stored byte-for-byte and identified by a hash of its content. Forwarding it twice produces one artifact, not two. Duplicate bytes never create duplicate obligations.
2. **Three** immutable evidence items are lifted out of it, each anchored to the exact characters it came from: a `DATE_ASSERTION` (the service period), an `AMOUNT_ASSERTION` (USD 186.00), and an `IDENTIFIER_ASSERTION` (the account reference).
3. The account reference **exact-matches** the stored account for the old Northline Fiber relationship. This match is deterministic and happens *before* any similarity search is consulted. Similarity search then supplies additional candidates, advisory only, never canonical truth.
4. Retrieval runs over **16,035** of Alex's own evidence records, returns **20** similarity candidates, narrows to **7** after re-ranking, with **1** exact identifier hit, **2** records excluded because they were retracted or superseded, and **0** results from any other user. Retracted evidence keeps its text and its index entry forever but is never allowed to ground a new conclusion.
5. The invoice is recorded as a **`COUNTERPARTY_CLAIM`**, not as a fact. The invoice arriving does not make USD 186.00 owed. It makes USD 186.00 **claimed**, by a party with a financial interest, about a period that begins the day after a termination that same party confirmed in writing.
6. The system detects that this claim is mutually exclusive with what it currently holds and records the contradiction as a durable object: type `VALUE_CONFLICT`, status `NEEDS_HUMAN`, severity `HIGH`, requires human review. It is not a transient warning string; it is a row with an identifier that can be linked to, counted, and reopened.
7. **One single all-or-nothing database transaction** writes: the claim, a new belief version, the grounding links, the contradiction record, the case status change from `RESOLVED` to `REOPENED` with reason code `CONTRADICTORY_EVIDENCE`, the case revision going from **12 to 13**, the state transition record, and one outbound event. Either all of it is true or none of it is. There is no moment in which the case is reopened but the contradiction is missing.
8. The interface tells Alex, in a deterministic sentence with no model involved: **"This relationship was closed. New evidence reopened it."**
9. The system drafts a reply in which every factual sentence carries the identifier of the record that backs it.
10. Alex reviews the draft, edits it if she wants, and activates **Approve and send**. The approval is bound to case revision 13 and to a hash of the exact draft text on screen.
11. Approving is itself a change to the record, so the revision moves 13 → 14 and the send is bound to 14. Before sending, the executor re-checks that the case is still at revision 14 and the draft still hashes to the approved value. Only then does it send.

### 2.4 The single most important detail in the whole product

The belief `balance_owed` for the old Northline Fiber relationship goes from version 1 to version 2:

```
version 1   value USD 0.00      status CONFIRMED    confidence 0.9400
            recorded 15 May 2026 · superseded 18 September 2026

version 2   value USD 0.00      status DISPUTED     confidence 0.7100
            recorded 18 September 2026   <- current
```

**The value did not change. The confidence in it did.**

Read that twice, because it is the whole epistemic claim of the product compressed into one number. A naive system, handed an invoice for USD 186.00, sets the balance to USD 186.00 — it adopts the counterparty's number because the counterparty is the one who spoke most recently. Provenance does not. It keeps holding USD 0.00, marks that position **disputed**, and records exactly what is arguing against it. The system does not adjudicate and it does not capitulate.

A viewer who reads "USD 0.00 → USD 0.00" and concludes "nothing happened" has missed the entire point, and a design that renders lineage as a sequence of values will produce exactly that reading. **Making this visible is the highest-value thing you can do in this engagement.** The fixed caption the interface uses is: *"The amount did not change; our confidence in it did."* Design around that sentence, do not rely on it alone.

Version 2's grounding is what makes the disputed status legible:

| Relation | Source | Weight | Reason code |
|---|---|---|---|
| `SUPPORTS` | **Evidence** — written cancellation confirmation, 15 May 2026, from the provider itself. Source authority 0.9000 for service status. | 0.9200 | `RC_WRITTEN_CONFIRMATION` |
| `SUPPORTS` | **Evidence** — service-end notice, effective 31 May 2026, from the provider itself | 0.8800 | `RC_EFFECTIVE_DATE` |
| `CONTRADICTS` | **Claim** — the USD 186.00 invoice, asserted by the provider, financially interested. Authority 0.5500 for a balance. | 0.5500 | `RC_POST_TERMINATION_PERIOD` |

The exact text of the first supporting source, which you will need to typeset verbatim in a quotation, is:

> Your cancellation request has been processed. Service will end on 31 May 2026 and no further charges will apply.

Received 15 May 2026 from `billing@northlinebroadband.example`, subject "Cancellation confirmation — account ••••8802", extracted from `text/plain` characters 412–528, extraction confidence 0.9800, retraction status `ACTIVE`. That is the level of detail available one interaction beneath the quotation — design for it, because it is what makes the quotation checkable rather than decorative.

Read the grounding table aloud and the product explains itself: *the conclusion still rests on two documents the provider itself wrote, and the only thing arguing against it is one interested-party claim about a period after the termination those documents establish.*

Note the asymmetry of the sources: two are **evidence**, one is a **claim**. Two are things a document says; one is a thing an actor asserts. They must not look like three rows of the same list.

The lineage entry for version 1 carries its own supersession reason codes — `PROVIDER_WRITTEN_CONFIRMATION`, `EXPLICIT_EFFECTIVE_DATE` — rendered both as a sentence for the user and as raw codes for the auditor. Both, always. The sentence is not a replacement for the codes.

### 2.5 The second reveal: the deposit that raised itself

While Alex is looking at the ISP case, the dashboard shows something she never asked for.

The landlord deposit case has raised itself, because a condition armed months ago came true on its own. The promised 30 days elapsed on 15 June 2026 and USD 1,800.00 is still outstanding — 95 days ago as of the demo clock. **Nobody set a reminder.** No notification was scheduled. A durable future condition was armed when the promise was admitted, and it woke on elapsed time and evaluated a predicate against the *current* state of the record.

The headline the system generates for this is deterministic, produced by a template with no language model involved:

> The promised 30 days elapsed and USD 1,800.00 is still outstanding.

The trigger is type `COMMITMENT_DEADLINE`, was in state `ARMED`, and is now `FIRED`. Its predicate, in plain words, is *outstanding amount is greater than zero, and now is at or past the due date*, and the exact field values it saw when it fired are available in a detail disclosure: `outstanding_amount = 1800.0000`, `due_at = 15 June 2026`, `now = 18 September 2026`. That disclosure is what makes prospective memory auditable rather than magical, and it is a design element, not debug output.

---

## 3. Who this is for — and this is unusual

The design has to serve two audiences at once, and the order matters absolutely.

### 3.1 Audience one: a person under mild financial stress

Someone who has just realised they are being billed for service they cancelled, or that a deposit they need has not come back. Not a crisis, but not neutral either — the state of mind is low-grade dread plus the suspicion that pursuing it is not worth the hours. They are not technical. They have never heard of a vector index and never will. They must trust this product **instantly**, because a system-of-record product that is not trusted in the first ten seconds is deleted.

What earns that trust: calm, specific, monetary, legible. Real amounts and real dates in the first screenful. A tone that is composed rather than urgent. The visible fact that nothing is hidden and nothing was thrown away. The unmistakable message that **they are in control** and the software will not act on its own.

What destroys it: alarm colouring, exclamation marks, gamification, anything that resembles a notification demanding action, or anything that implies the software has already done something on their behalf.

Copy discipline follows from that posture. The product never characterises anyone's conduct and never asserts an entitlement:

| Never | Instead |
|---|---|
| "Northline billed you incorrectly." | "This invoice covers 1–30 June 2026. Their 15 May confirmation gives a termination date of 31 May 2026." |
| "Your landlord broke their promise." | "The 30 days promised on 16 May elapsed on 15 June. USD 1,800.00 remains outstanding in the record." |
| "You owe USD 186.00." | "Northline Fiber's invoice states USD 186.00 is due for 1–30 June 2026." |
| "We noticed something odd." | "A new invoice covers a period after a recorded termination date." |
| "We'll remind you." | "Provenance will check this again on 20 June 2026." |
| "All clear." | Name what is absent and offer one action. The record's silence is not a guarantee about the world. |

### 3.2 Audience two: engineers evaluating this as a serious system

The same interface is read by database and AI engineers weighing five things equally: memory design, technical implementation, real-world impact, product readiness — security, observability, resilience, access control — and creativity. They will look for production seriousness and they will notice its absence immediately.

They want to see typed values, version numbers, identifiers they can copy, transaction boundaries, permission boundaries, retry counts, millisecond durations, model identifiers, and honest failure states. Identifiers on this product are **content, not debug output**: a case revision, a belief version identifier, a support identifier, a draft fingerprint, and a trace identifier are what make the record citable, and they are rendered as visible, selectable, copyable text wherever their object is rendered. They are never hidden behind a developer toggle.

### 3.3 The rule that resolves the tension

**Earn emotional trust first. Reveal technical depth on demand. Never the reverse.**

Every screen has a calm consumer surface. Every screen has technical depth available one deliberate interaction away — a disclosure, a detail panel, a mode toggle. The identifiers, statuses, revision numbers, authorities, and weights exist on every screen; they are simply not the first thing the eye lands on.

Concretely: a consumer sees "Confirmed in writing by Northline Fiber on 15 May 2026". One interaction away, an engineer sees the evidence identifier, the exact character range it was extracted from — `text/plain`, characters 412–528 — the source authority of 0.9000, the extraction confidence of 0.9800, and the retraction status `ACTIVE`. Same component, two depths, no separate "advanced" application.

This is the central design problem of the product. Solve it once, in the component system, and all seven screens inherit the solution. Tell me in your response what the disclosure mechanism is and why it is the same one everywhere.

---

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

Two of these are easy to under-invest in and both are load-bearing. **Login** is the first thing anyone sees, and a generic login screen sets the wrong expectation for everything after it. **Upload and forward** is where the product's only real input arrives; if adding evidence feels like clerical work, nobody builds the record that makes the other six screens possible.

There is a persistent primary navigation with exactly five destinations: Dashboard, Approvals, Add a document, Judge Mode (present only for accounts that have it enabled), and Sign out. Case detail, State Proof, and an individual approval are always reached contextually, never from the primary navigation, because they require a specific object the user must have selected. Design the shell as part of this.

### 4.1 Login

**Emotional job: trustworthiness.** "This is going to hold things that matter to me."

This is a single-purpose screen and the temptation is to give it no thought. Resist that. It carries the product's whole promise in one view, before any data exists to be impressive with.

**Information hierarchy:** the product name and the promise in one sentence; the sign-in control; and nothing else competing with them. No feature tour, no marketing carousel, no testimonial, no screenshot of the dashboard.

The design problem: convey *durability and discretion* — this is a place records are kept safely — without the visual language of a bank or a security vendor, which reads as cold and adversarial to someone who is already mildly anxious about money. The screen should feel like a well-kept archive, not a safe.

There is no password field on this screen; authentication is delegated, and the product never sees a credential. Say that plainly in one line, because it is reassuring and true.

Design three states: the signing-in state (a status line, not a spinner-only screen), the failed sign-in state, and a terminal state for an account that exists but has not been provisioned — which cannot be fixed by retrying, so it must not offer a retry control. None of the three may blame the user.

### 4.2 Dashboard — "The Move"

**Emotional job: relief.** "Nothing was lost. Here is exactly what is still owed to you." This is the first ten seconds and it must state the money before it states anything about technology.

**Information hierarchy:**

1. The context **"The Move"** — 4 relationships, **USD 2,020.00 outstanding**. Note this is an array of amounts per currency, never a sum across currencies; with one currency it renders as a single figure, and your layout must not assume exactly one.
2. Cases needing attention, in the order the record supplies them, each with a one-line deterministic headline: *"A new invoice contradicts your recorded cancellation."* · *"The promised 30 days elapsed and USD 1,800.00 is still outstanding."* Each row carries the case title, its status, its attention level, its counterparty, its last activity, and — literally, as text — "revision 13". The revision on the row is not decoration; watching it move is how a viewer knows they are looking at a live record and not a screenshot.
3. The four relationship cards: counterparty name and kind, what is open, outstanding amount, attention level, last activity. Remember the two-Northline-accounts trap from section 2.1 — the card identifies a *relationship*, not a brand.
4. A counter row: unresolved commitments, active conflicts, drafts awaiting approval, cases needing attention, watches armed, watches fired. Each is a link to the correspondingly filtered view. **A zero renders as "0" and is never hidden** — hiding zeros makes the absence of a problem indistinguishable from the absence of a feature.
5. An outstanding-commitments strip and a watches strip. A commitment shows "95 days past the promised date", never "late" and never "broken promise". A fired watch expands to show the exact field values its predicate saw.
6. A quiet, always-available way to add evidence — forward or upload.

Two traps specific to this screen. **A closed case can still carry `URGENT` attention** — that is exactly what happens to the Northline cancellation — so "closed" and "quiet" are different things and the layout must not conflate them. And **an empty outstanding amount is not zero**: when the record contains no outstanding figure the card says "No outstanding amount recorded", never "USD 0.00", because asserting a zero balance the record does not contain is precisely the failure the product exists to prevent.

**Empty state:** a first-run dashboard has no relationships at all. It must still communicate the thesis and offer exactly one next action.

### 4.3 Case detail and timeline

**Emotional job: orientation.** "Here is the whole story of this case, in order, and I can see who said what."

**Information hierarchy:** a case header — title, counterparty, relationship, context, status, revision, attention level and its reason codes, opened date, resolved date, and, when it is greater than zero, an explicit **"Reopened once"** — then the commitments, then the open conflicts, then the next armed watch, then any pending draft, then a reverse-chronological merged stream of everything that happened.

Each timeline entry carries: an actor, a timestamp, a case revision chip, a deterministic headline, and an expandable detail. The entry kinds you must style are:

`ARTIFACT_RECEIVED` · `EVIDENCE_ADMITTED` · `CLAIM_RECORDED` · `BELIEF_CHANGED` · `CONFLICT_OPENED` · `CONFLICT_RESOLVED` · `COMMITMENT_CREATED` · `COMMITMENT_UPDATED` · `FULFILLMENT_ADMITTED` · `STATE_TRANSITION` · `TRIGGER_ARMED` · `TRIGGER_FIRED` · `TRIGGER_NOOP` · `ACTION_PROPOSED` · `ACTION_APPROVED` · `ACTION_REJECTED` · `ACTION_EXECUTED` · `ACTION_FAILED` · `USER_CORRECTION`

Nineteen kinds is too many for nineteen icons. Find a system: a small number of visual families that group them meaningfully, with the specific kind carried in text. An icon that appears on every row carries no information.

The visual distinction that matters most on this screen: **what a counterparty asserted** versus **what the deterministic engine did** versus **what a language model proposed**. Three different kinds of authorship in one stream, and a user must never be confused about which is which. A `USER_CORRECTION` entry is a fourth: the user's own words, in quotation marks, attributed to them, stored verbatim and never edited by the system.

Note also that a correction is first-class evidence, not an edit. When a user says "actually they paid me USD 200", nothing is overwritten — a new record is admitted and the belief gains a version. The receipt shown afterwards is one of the most persuasive moments in the product and deserves real design: *"Recorded. Case revision 13 → 14. One belief version created. One conflict updated. One state transition."*

The timeline is paginated by an explicit "Show older entries" control. Infinite scroll is prohibited here: it makes keyboard and screen-reader users traverse an unbounded list to reach anything below it, and it makes "how much record is there" unanswerable.

### 4.4 State Proof

**Emotional job: verifiability without intimidation.** "I can see why it believes this, and I could check it myself if I wanted."

This is the most important screen in the product. It is generated entirely by database query with no language model involved, and it must remain correct and fully renderable when every model in the system is unavailable. The screen states that about itself, from the data rather than as a decoration: **"Computed by database query. No model was involved in producing this page."**

**Document order is normative** — it is also the screen-reader reading order, and it must not be rearranged for visual convenience:

1. **Header** — case, revision, status, when this proof was generated, and the determinism statement.
2. **Beliefs**, and for each one, in this order: the current value → **grounding** → **lineage**.
3. **Derivations.**
4. **Commitments and their fulfillments.**
5. **Conflicts.**
6. **State transitions.**
7. **Actions relying on this state.**
8. **Exclusions and integrity.**

**The current value.** When the epistemic status is `DISPUTED`, `UNCERTAIN`, or `RETRACTED`, **the status is the visual and reading-order primary and the value is secondary** — the heading reads "Disputed — balance_owed" and the value follows. This is the direct fix for the trap in section 2.4. The value-unchanged caption appears immediately beneath.

**Grounding** — heading "What this rests on". Three groups with counts in the headings: "Supports (2)", "Contradicts (1)", "Qualifies (0)". **A zero group renders with its heading and the word "None", never omitted** — the absence of contradicting evidence is information. Each source renders its exact text verbatim in a quotation, never paraphrased, never silently truncated, with "as Provenance read it" underneath showing the normalised form so the user can object to the normalisation. The exact character range it came from is available in a disclosure — that span anchor is what makes the quotation checkable against the original document.

**Lineage** — heading "How this changed". A structurally separate region, never interleaved with grounding. Each version states its position before its content — "Version 1 of 2", "Version 2 of 2 — current" — then its value, status, confidence, when it was recorded, when it was superseded, and the reason, as a sentence *and* as raw codes. A single-version lineage says "Version 1 of 1 — current. This has not changed since it was first recorded." and is never an empty region.

**Derivations** need their own treatment and are not grounded by evidence at all:

> **outstanding_amount** — USD 420.0000 − USD 200.0000 = **USD 220.0000**
> `committed_amount - fulfilled_amount`. Computed and checked by the database. Not a model output, and not an opinion.
> This has no supporting evidence because it is arithmetic, not an observation.

Make it feel like arithmetic rather than judgement. It is the one place on this screen where an absence of evidence is correct rather than alarming.

**Exclusions** are a visible footer, not a hidden detail: *"2 retracted or superseded sources were excluded from this proof. The retraction filter ran. [Include them for history]"* — because the filter having run is a correctness property a skeptic should be able to see without reading any code.

**Integrity warning.** Design the rare, serious state: a conclusion with **no** supporting evidence that is not a derivation. This should be impossible and the system refuses to write it, so if it ever renders, something is badly wrong. The belief still renders in full — hiding it would remove the only view of the anomaly — with a warning above it that is grave without being theatrical.

One layout constraint that is not negotiable: **grounding and lineage are never placed side by side at any width.** They are different structures and adjacency invites conflation. Lineage always follows grounding vertically.

#### 4.4.1 The contradiction panel — a component, not a screen

This component appears inside both case detail (4.3) and State Proof (4.4). It is called out here because it is the hardest single component in the product, not because it is an eighth screen. Design it once, as a component that sits correctly in both hosts.

**Emotional job: composure.** "Two records disagree. That is a normal, expected, handled situation, and the system is holding both without pretending to resolve it."

**Information hierarchy:** the two sides presented as **peers**, each with its source kind, its authority for this kind of statement, its date, and its exact wording; then the type, the severity, whether it needs a human, and — crucially — **which side remains canonical while the contradiction is open**.

That last part is the design problem. The system does not flip to the newest assertion. It keeps holding its position, marks it disputed, and says so:

> **Currently canonical:** belief version `018f8b22-…` (`balance_owed` version 2).
> Both sides remain in the record. This contradiction has not been resolved.

`requires_human: true` renders as **"Provenance will not decide this on its own."** — never "action required from you", because the system does not know that the user must act.

Neither side is styled as the wrong one. But the two sides are not the same *kind* of thing — one is a belief version, one is a claim by an interested party — and the containers must differ even though the visual weight does not.

### 4.5 Action approval

**Emotional job: agency and safety.** "I am in control. Nothing goes out without me, and I can see exactly what it is based on."

**Information hierarchy:** the canonical block first — case, revision, recipient, what this action is, which belief versions support it, any warnings — and only then the drafted message inside a clearly labelled container. That order is a hard rule: the user sees the case, the revision, the recipient, and the supporting record **before** they see a single generated word, which is the correct order in which to form a judgement.

Every model-authored region carries a visible label, fixed copy that may be restyled but not shortened to an icon and not moved below the text it qualifies:

> Written by a model from the record below. Not itself part of the record.

Critical mechanics to design:

- **Sentence-level grounding inside prose.** Each factual sentence in the draft is marked as an inline region and carries one or more support identifiers. Activating a sentence opens its supports: for a belief version, its predicate, version number and status; for an evidence item, its quoted exact text, when it was observed, and its source authority. A sentence with no support is labelled **"your own words"** — not "unsupported". Provenance does not refuse a user's own words; it declines to vouch for them. A summary line above the draft states the count: *"3 sentences are supported by your record. 1 sentence is your own words."*
- **The staleness state.** This is the most important error surface in the product, because it is where the system refuses to forge consent. Approval binds to case revision 13 and to a hash of the exact draft text. If the record moves to revision 15 before the send, the approval is refused, and the screen is **taken over** — not warned with a toast — by a panel showing: why it went stale; the revision arithmetic rendered literally ("Prepared at revision 13. Your screen showed revision 13. The case is now at revision 15."); a numbered list of what changed; which conclusions moved; whether the message text itself changed; and exactly three exits — *Review what changed*, *Open the updated State Proof*, *Reload this draft*. There is no "approve anyway", no "ignore", and no "retry", because retrying an approval in code would forge consent. **This must feel like a safety feature working, not like an error.**
- **The draft fingerprint.** After any edit, the fingerprint change is shown immediately and prominently — `9a1f2b3c` → `c4d5e6f7`, first eight characters visible with the full value available and copyable. Eight characters is enough for a human to see it moved; the full value is what a skeptic checks.
- **Honest risk disclosure.** The draft carries lines under "What this draft does not settle", such as *"The provider may hold a distinct final-period charge that is contractually valid."* The product does not claim the user is right. It claims the record is intact and citable. Design that block so it reads as integrity rather than as hedging.
- **The recipient is deliberately not editable.** Design it as a stated guarantee — "Changing who receives this would change what was checked. A different recipient needs a new draft." — rather than as a greyed-out field.
- **The post-approval sequence.** "Approved. This exact message is now locked to fingerprint `9a1f2b3c` and to case revision 13. The case is now at revision 14. If the record changes before this sends, it will not send." Then queued, then sent, with the executor's revalidation stated: "The executor re-checked case revision 14 and the message fingerprint, then sent it."
- **Rejection is recorded, not discarded.** "This is kept in your record as your position on this draft."

Also design the confirm step. Its primary control is labelled **"Approve and send"**, never "OK", and it restates the recipient in full, the subject, the grounded-versus-own-words counts, and the revision the approval will bind to.

### 4.6 Judge Mode — Memory Trace, system status, and the counterfactual

**Emotional job: technical respect.** "This is an X-ray of a working system", not "this is a debug console."

Judge Mode is one permission-gated screen with four panels plus the counterfactual, all built entirely from real persisted records. The vertical order is fixed and is not yours to change: consumer state and State Proof sit side by side at the top, Memory Trace spans the full width beneath them, system status beneath that, and the counterfactual comparison (section 5) sits **below all four**. The product result is revealed before the infrastructure that produced it; a viewer who meets the counterfactual before they have seen what the system concluded has nothing to compare it against.

The consumer-state and State Proof panels **reuse the exact same components as screens 4.2 and 4.4**. This is a requirement, not an optimisation: someone comparing the consumer screen with the Judge Mode panel must see the same rendering of the same fields, or the panels become a second, unverifiable surface. Design those components so they work in both hosts.

The difference between an X-ray and a debug console is the difference between an instrument panel and a log dump. An X-ray is composed, deliberately laid out, shows structure rather than text, and is designed to be read by someone who did not build the machine. Judge Mode must be presentable on a projector, readable in a screen recording, and must never look like something that leaked out of a developer's terminal.

**System status** renders the service version, build identifier, region, liveness, the operating mode, every feature flag with its boolean, the transaction isolation level and retry count from the selected trace, outbound event delivery, model routing with token counts, the embedding configuration, and — as a first-class line — **"Cross-user results: 0"**.

There is one banner that overrides everything else in the design. If the system is running on replayed fixture data rather than live processing, a permanent, non-dismissible banner renders at the top of **every** screen, not just this one:

> **DEMO FIXTURE MODE — model outputs are replayed**

There is no state in which it is hidden, no query parameter that suppresses it, and no subtle version of it. It is an honesty requirement. Design it so that it is impossible to miss and still does not wreck the layout beneath it.

#### 4.6.1 The Memory Trace panel

**Emotional job: credibility.** "This is a real machine that did real work, and I can see where the model stopped and the deterministic logic started."

Every element here comes from persisted records of an actual run. Nothing is animated narration and nothing is a scripted sequence.

**Information hierarchy:** for each trace that materially changed the case — the headline, the case revision before and after, the decision and its reason codes, the retry count, the memory operations with counts, the retrieval statistics, the database tool calls with the role and access mode they ran under, and the model calls with their model identifiers.

The real values from the hero run:

```
Kernel decision       ACCEPTED_WITH_CONFLICT
Reason codes          MUTUAL_EXCLUSION_DETECTED, CASE_REOPEN_QUALIFIED
Serialization retries 0
Case revision         12 -> 13
State transition      RESOLVED -> REOPENED   reason CONTRADICTORY_EVIDENCE

Memory operations     evidence admitted        3
                      claim recorded           1   COUNTERPARTY_CLAIM
                      belief version created   1   balance_owed v2, DISPUTED
                      grounding links written  3   2 SUPPORTS, 1 CONTRADICTS
                      conflict opened          1   VALUE_CONFLICT, NEEDS_HUMAN, HIGH
                      case reopened            1
                      state transition         1
                      outbound event           1

Retrieval             corpus scoped to user    16,035
                      similarity candidates    20
                      after re-ranking         7
                      exact identifier hits    1
                      retraction filter        applied
                      retracted excluded       2
                      results from other users 0
                      embedding model          amazon.titan-embed-text-v2:0
                      dimensions               1024
                      distance                 cosine

Database tool calls   1  query_agent_case_context      view agent_case_context_v1        44 ms   1 row
                      2  query_agent_evidence_search   view agent_evidence_retrieval_v1  128 ms  20 rows
                      3  query_agent_active_beliefs    view agent_active_beliefs_v1      31 ms   6 rows
                      all under database role pv_agent_reader, access mode READ_ONLY

Model calls           extract_structured_evidence   tier E   anthropic.claude-haiku-4-5
                      strong_resolution             tier R   anthropic.claude-opus-5
```

The database role and access mode are shown deliberately: the permission boundary is enforced by database grants, not by instructions given to a model. **A denied call is rendered visibly rather than hidden** — design that denied state, because a visible refusal is a feature and a hidden one is a lie.

The line that must be visually obvious, and that I want you to treat as a real structural element rather than a caption: **where the language model stopped proposing and the deterministic engine started committing.** The trace groups its nodes into exactly two labelled families for this reason, and the fixed sentence rendered beside them is *"Model nodes propose. Deterministic nodes decide, commit, and act."*

Below 1024 pixels this trace becomes a vertical indented list rather than a graph. A graph you have to pan is a graph you cannot audit, so a horizontally scrolling canvas is not an acceptable small-screen answer.

### 4.7 Upload and forward

**Emotional job: effortlessness.** "Getting something into the record costs me nothing."

This is the product's only real input surface and everything else is downstream of it. If it feels like filing paperwork, the record never gets built and the other six screens never have anything to show.

**Information hierarchy:** the drop target for a file, first and largest; the user's private forwarding address, presented so it can be copied in one action and understood without explanation; a short honest statement of what happens next; then recent documents.

Design problems specific to this screen:

- **Two ingestion routes, one mental model.** A user can drag a file in, or forward an email to a private address that belongs only to them. These are the same act — "add this to the record" — and must not look like two different features. The forwarding address is an opaque random token, something like `n7k4q9wv2x@in.provenance.app`, and it is random on purpose: it identifies the user without containing their name, so a forwarded message header does not leak who they are. Design how an unreadable string is presented so it reads as deliberate rather than suspicious. Say the reason next to it.
- **The wait is real and must be honest.** Adding evidence is not instantaneous — the file is hashed, stored, parsed, interpreted, matched against existing memory, and only then does anything change. Report the mechanism at each stage — parser status, page count, evidence items admitted, cases linked — not "analysing your document…" over a moving bar. Do not show a fake progress bar and do not imply the record has been updated before it has.
- **Duplicate detection is a success, not an error.** Forwarding the same invoice twice is normal and expected; the system deduplicates on the content hash. The response is *"Already in your record. This is byte-for-byte the same document you added on 5 June 2026. Nothing was duplicated. [Open the original]"*, with one line underneath that is worth a technical viewer's attention: **"Duplicate bytes never create duplicate obligations."** The word "error" never appears. Treating a duplicate as a mistake teaches people to stop forwarding, which kills the product.
- **A separate, subtler state: saved but not yet read.** If storage succeeds and interpretation is unavailable, the document *is* in the record and its bytes and hash are committed; only the reading is pending. "Saved. Your document is in your record and its contents are unchanged. Provenance cannot read it right now; it will be read automatically and this page will update." This is the four invariants made visible to a non-technical person, and it deserves a real state rather than an error card.
- **Rejection must be specific.** Unsupported types and oversized files are refused with the precise limit and the precise accepted list, at the moment of failure, without jargon.
- **Rotating the forwarding address is destructive to inbound mail** and gets a confirm dialog; choosing a file does not. Ceremony is proportional to consequence.
- **The empty first-run state.** A brand-new user lands here with nothing: *"Nothing has been added yet. Start with the last email an institution sent you."* This is the moment the product either explains itself or loses them.

---

## 5. The counterfactual view — this is the centrepiece

Design this as its own view, reached from Judge Mode. It is the single most persuasive twenty-five seconds of the product and the easiest thing in it to accuse of being rigged. Everything below exists to make that accusation answerable **from the screen itself**.

The same document is processed twice by the **same model, with the same prompt, in the same code path**. The only difference is that one run has retrieval and canonical memory switched off. Nothing is faked and nothing is nerfed; one code path simply runs with its memory removed.

### 5.1 The parity block gates the whole comparison

Before either column may render, six equality checks run across the two runs:

| Check | Must be equal across both runs |
|---|---|
| 1 | Artifact identifier |
| 2 | Artifact content hash |
| 3 | Model identifier |
| 4 | Prompt version |
| 5 | Graph version |
| 6 | Decode parameters hash |

Render these six as visible checks **above** the columns. If all six pass, the columns render. **If any one of them fails, the two output columns are not rendered at all** — in their place, a failure banner reading **"PARITY FAILED — this comparison is not valid"**, listing exactly which pairs differed.

Design both outcomes. A counterfactual that cannot prove parity is worse than no counterfactual, because showing two columns whose equivalence is unproven invites precisely the accusation the screen was built to defeat. This is the one place in the product where the design's job is to *withhold* the most persuasive thing on the screen.

Exactly four differences between the two runs are permitted: whether retrieval was enabled, whether canonical memory was enabled, how many records were visible, and the resulting output. Anything else differing is a parity failure.

### 5.2 The two columns

Both columns are rendered by the same component with the same field order, so the only difference the eye can find is content.

**Memory OFF**

```
Retrieval          off
Canonical memory   off
Corpus visible     0 records
Model              anthropic.claude-opus-5
Duration           4,120 ms
Output             "Invoice for USD 186.00 due 30 June."
Classification     ROUTINE_INVOICE
Case linked        No case linked
Conflicts detected 0
Recommended        No action recommended
Draft              No draft produced
Why                Without retrieval the document is self-describing: a valid
                   invoice with a due date.
```

**Memory ON**

```
Retrieval          on
Canonical memory   on
Corpus visible     16,035 records
Model              anthropic.claude-opus-5
Duration           9,420 ms
Output             "Contradicts your 15 May termination confirmation — case
                   reopened, dispute drafted."
Classification     COUNTERPARTY_CLAIM_CONTRADICTING_CANONICAL
Case linked        Old ISP service cancellation, RESOLVED -> REOPENED
Conflicts detected 1
Recommended        Outbound dispute email
Draft              (grounded draft text)
Grounding          service_terminated, evidence observed 15 May 2026, authority 0.9000
Case revision      12 -> 13
```

A null on the OFF side is **rendered as an explicit statement** — "No case linked", "No draft produced" — never as blank space. Blank space reads as "not loaded yet"; an explicit null reads as "this is what happened".

**The difference strip**, beneath the columns:

```
Contradictions found      0  ->  1
Cases reopened            0  ->  1
Actions recommended       0  ->  1
Evidence recalled from    0  ->  126 days ago
Verdict                   Memory OFF treated a contradiction as a routine bill.
```

**The safety block** — render this, do not hide it. Show the literal field name beside each boolean, so a skeptic can match the screen to the underlying payload:

```
memory_off_wrote_canonical_state          false   Memory OFF wrote no canonical state.
memory_off_admitted_evidence              false   Memory OFF admitted no evidence.
memory_off_had_proposal_tool              false   Memory OFF was not given the proposal tool.
case_revision_changed_by_counterfactual   false   This comparison did not change the
                                                  record it demonstrates.
```

### 5.3 The header sentence is not a constant

The header above the columns depends on how the ON side was produced, and the interface must select it from the run rather than hard-coding it:

- When the ON side is the run that **actually happened** when the document first arrived: *"The left column ran just now. The right column is the decision this document actually produced when it arrived — same document, same model, same prompt, same graph. The only difference is that the left column was given no retrieval and an empty State Proof."*
- When both were **re-run now**, read-only: *"Both columns ran just now, against the same document, with the same model, the same prompt, and the same graph. The only difference is that the left column was given no retrieval and an empty State Proof."*

Claiming a replayed run "just ran" would be a small, checkable, entirely avoidable lie of exactly the kind this screen exists to prevent. Design the header so that swapping between these two sentences does not break the layout.

Two further labelling requirements with visual consequences: the identifiers for the comparison, the trace, the document, and the completion time are all rendered with copy controls, so a skeptic can request the identical data and diff it against the screen; and running it again versus showing the stored result are **two separately labelled controls**, with the replayed case saying so in visible text — "Replayed from the stored result." Neither is the silent default.

### 5.4 Running, partial, and the fairness constraint

Both runs take several seconds and either can fail independently. While running, the view shows elapsed wall time and nothing else: it does not pre-render either side, does not animate a thinking sequence, and does not stage a reveal. Each column appears as soon as its own result exists; if one arrives first, the other says "still running", which is the truth.

If one side fails, that column renders its actual error and the difference strip is suppressed with *"One side did not complete; there is nothing to compare."* Filling a failed side with a plausible sentence would be the exact fraud this view exists to prevent.

**The fairness constraint, which governs the whole layout:** the two sides must read as **peers running under identical conditions**. If the OFF side is visually degraded — dimmer, smaller, greyed, pushed to a subordinate position — the whole demonstration reads as a strawman and the credibility is lost. Make identical conditions visible in the layout itself and let the difference come entirely from the content.

Below 1024 pixels, stacking the columns destroys the comparison. The answer is one mode at a time behind a segmented control that **preserves scroll position**, so switching compares the same field rather than the top of each column, plus a per-row "compare this row" affordance and a persistent banner: *"Showing one column at a time. Both ran together, on the same document, with the same model."* No field may be dropped at any width, and the difference strip and safety block always render in full.

---

## 6. The seven hard design problems

This is the interesting part of the brief and the reason it exists. Anyone can lay out a dashboard. **These seven are what I am actually commissioning.** Address each one explicitly in your response and tell me what you did.

### Problem 1 — A claim must read as a claim, not a fact, everywhere it appears

An invoice arriving does not make money owed. It makes money **claimed**, by an interested party. This distinction is the ethical and technical core of the product and it has to survive at every scale: in a dense timeline row, in a one-line dashboard headline, inside a sentence of drafted prose, in a grounding list next to an evidence item, and in a full detail panel.

A claim carries an actor, a capacity, and an authority weighting for that specific kind of statement. An observation and a counterparty claim are not the same thing and must never look the same. A claim is always rendered in reported speech with attribution **first**, before its content — "Northline Fiber states —" — and never inherits the typography of a record.

The naive solution is a coloured badge. Badges fail at small sizes, fail in a screen recording, fail for colour-blind viewers, and are ignored after five minutes of familiarity. Find something **structural** — attribution that is part of the shape of the component rather than an ornament on it.

Test your solution against three cases and show me all three: a claim quoted inside a drafted email sentence; a claim shown next to an evidence item in a grounding list; and a claim in a 32-pixel-tall timeline row.

### Problem 2 — A contradiction must not read as an error or a failure

When two sources disagree, the system is working correctly. It found the thing it exists to find. But every visual convention available — red, warning triangles, alert banners — says "something broke".

Design a contradiction treatment that means *"two records disagree and I am holding both"*, is clearly more significant than ordinary content, is clearly not a malfunction, and does not deploy the same vocabulary as a real system error. You will also need genuine error states elsewhere in this product — a failed model call, a failed send, a database error — so whatever you choose here must remain distinguishable from those at a glance.

### Problem 3 — Grounding and lineage must be understandable by a non-technical person

A person must be able to look at a conclusion and understand **why the system believes it**, without knowing what a graph edge or a version chain is.

Grounding is *what is this based on?* — several sources, each supporting, contradicting, or qualifying the conclusion, each with a different authority for this specific kind of statement. A provider's own written confirmation is near-authoritative about whether service was cancelled. That same provider's invoice is weakly authoritative about whether money is owed, because they are the interested party. Same institution, same document type, different authority, and the *reason* is the predicate.

Lineage is *what did we used to think and what changed?* — a chain of versions, each with a value, a status, a confidence, a time, and a recorded reason it was replaced.

They are different shapes of information and must be different shapes on screen. And they must compose: on State Proof a user moves between them constantly, so they cannot be two unrelated visual languages.

The specific case that will break a naive design: **version 1 was USD 0.00 `CONFIRMED`, version 2 is USD 0.00 `DISPUTED`.** The value did not move. The confidence did. A design that shows lineage as a sequence of values will render this as "USD 0.00 → USD 0.00" and look like nothing happened. **Solve exactly this case and show me your solution for it.** It is the single highest-value thing in this brief.

### Problem 4 — "This was closed, and new evidence reopened it" must feel like relief and control

The literal event is bad news: a case the user thought was finished is open again and there is a new USD 186.00 charge. But the **product** event is good news, and it is the moment the entire thesis pays off — the system remembered something from four months ago that the user had entirely stopped tracking, and it caught it automatically.

The design has to carry: *you are not back at square one — you are further ahead than you were, because the record held.*

Get this wrong in the alarming direction and the product becomes another source of stress. Get it wrong in the celebratory direction and it is tone-deaf about someone's money. Find the register between those, and design the transition — this is one of the very few moments where motion is genuinely load-bearing, because the state change happens live in front of the user.

There is a choreography question inside this one and I want your opinion on it. The deterministic state change commits seconds before the drafted prose returns from the model. So there are two arrival moments, not one. Does the reopen transition play on the commit, or is it held until the draft is ready so the user sees one composed moment? My posture is that it plays on the commit, because withholding a committed state change to improve a transition misrepresents when the system actually knew something — but tell me if you think the composed moment is worth it and how you would keep it honest.

The same treatment must also work for the landlord case, where the system raised something with **no external trigger at all**. That version has an extra beat to communicate: *nobody set a reminder; this woke up by itself.*

### Problem 5 — The memory OFF/ON comparison is the product's centrepiece

Covered in section 5. The design constraint restated: **both sides must look like fair fights.** Identical framing, identical prominence, identical typography, identical treatment. The persuasion must come entirely from the content, and the layout must visibly support the claim that only one variable changed.

And the parity block must be able to *withhold* the comparison entirely without the screen looking broken.

### Problem 6 — Judge Mode must read as an X-ray, not a debug console

A debug console dumps text and expects the reader to already know the system. An X-ray reveals structure to someone encountering it for the first time. Judge Mode contains genuinely technical content — role names, view names, retry counts, millisecond durations, model identifiers, token counts — and **none of it may be dumbed down or hidden**. But it must be composed: hierarchy, alignment, deliberate density, and a visible narrative from "an email arrived" to "one transaction committed and one event was published".

It also has to survive being screen-recorded at 1080p and played at speed. Thin hairlines, low-contrast greys, and 11-pixel monospace all disappear under video compression. Assume the most important thirty seconds of this product's life happen inside a compressed video.

### Problem 7 — Time must be represented honestly

The system tracks two independent kinds of time and constantly needs to show both:

- **valid time** — when something is true in the outside world. The invoice covers 1 June through 30 June 2026.
- **record time** — when the system learned it. That invoice arrived on 18 September 2026.

They routinely disagree, sometimes by months. A document that arrives in September can describe a fact from June, and sorting by arrival date would tell the story wrong. Validity is an interval, is half-open, and can be open-ended: "true from 31 May 2026, with no recorded end" — which is rendered exactly that way and never as "forever" or "ongoing".

Design a treatment that shows both without doubling the visual weight of every timestamp; that makes a period-versus-instant distinction obvious at a glance; that renders an open-ended interval honestly; that allows relative time ("4 months ago") only *beside* an absolute date, never instead of one; and that makes it immediately visible when a document's coverage period sits **after** the event it contradicts — which is the exact fact that makes the hero contradiction obvious to a human being in under two seconds.

---

## 7. Deliverable — design tokens

A complete token set, named systematically, presented as a table and defined as CSS custom properties.

- **Colour.** Full **light and dark** palettes. Every semantic role covered: surfaces at several depths, borders at two or three weights, primary and secondary and tertiary text, focus, and specific roles for the semantics of this product — supporting evidence, contradicting evidence, qualifying evidence, claim attribution, deterministic system output, model-generated output, superseded content, retracted content, denied operations, and the four attention levels `NONE`, `INFO`, `ATTENTION`, `URGENT`. **Dark mode is not an afterthought**; the product will be recorded and demonstrated in one of the two themes and both must be finished to the same standard.
- **Type.** A full scale with sizes, line heights, weights, and letter spacing. Include a tabular/data treatment for amounts and identifiers, a monospace treatment for machine values and hashes that survives video compression, and a display treatment for the amounts on the dashboard.
- **Spacing.** One consistent scale, with a stated rule for which step is used where.
- **Radii.** A small set with a stated rule.
- **Elevation.** Shadows or their alternative, defined in **both** themes. Shadows that only work on white are a common and disqualifying failure.
- **Motion.** Durations and easing curves, plus a named token for the case-reopen transition specifically. **Every motion token must have a documented reduced-motion behaviour**, including that one.

Name tokens systematically and keep the names stable across revisions. The token table is the contract between your design and the engineering team; the HTML is only a witness to it.

## 8. Deliverable — component inventory

Every component with all of its states: default, hover, focus-visible, active, disabled, loading, empty, error — and, where applicable, stale, superseded, retracted, denied, and frozen.

At minimum:

`AppShell` · `AttentionChip` · `CaseHeader` · `CaseStatusBadge` · `RevisionChip` · `ClaimCard` · `ClaimAttribution` · `EvidenceCard` · `BeliefCard` · `GroundingList` (supports / contradicts / qualifies, with counts and authority) · `LineageChain` · `ConflictPair` · `DerivationBlock` · `CommitmentMeter` (committed / fulfilled / outstanding) · `TriggerCard` (with its predicate field values) · `TimelineEntry` (every kind listed in section 4.3) · `ActorTag` · `TimeRange` (valid time and record time) · `CopyableId` · `AuthorityFigure` · `DraftReview` · `GroundedSentence` · `SupportReference` · `ApprovalBar` · `StalenessTakeover` · `TraceNode` · `BoundaryDivider` (the propose/commit boundary) · `DatabaseToolCallRow` · `ModelCallRow` · `RetrievalStats` · `ParityBlock` · `CounterfactualPanel` · `SafetyAssertionList` · `DeterminismBadge` · `EmptyState` · `SkeletonBlock` · `ErrorState` · `ForbiddenState` · `NotFoundState` · `FixtureModeBanner`

## 9. Deliverable — screens

All seven screens from section 4, plus the counterfactual view from section 5. Each at **1440 pixels wide** (desktop) and **390 pixels wide** (mobile).

The mobile designs are not scaled-down desktop. State Proof, Judge Mode, and the counterfactual in particular need genuine rethinking at 390 pixels rather than horizontal scrolling. Three constraints carry across every width: monetary values wrap rather than truncate; exact quotations clamp to a fixed number of lines with an explicit "Show the full quotation" control and are never silently cut, because a partially shown quotation is a misquotation; and identifiers show their first eight characters with the full value available and copyable.

## 10. Deliverable — system states

Design all five explicitly, and show them at minimum for the dashboard, State Proof, and the approval screen:

- **Empty** — no relationships yet; no evidence yet; a case with no contradictions; no drafts waiting. Empty states must still teach the thesis and must offer exactly one next action. Never an illustration with no next step.
- **Loading** — this system has genuinely slow operations. Model work takes seconds; the deterministic state change completes long before the drafted prose does. **The interface must show real canonical state immediately rather than blocking the whole screen behind the slowest thing on it.** Design partial loading, not a spinner over everything. Loading regions reserve their final height, and their headings are already present so the page outline is stable. A control that moves under a cursor because a region expanded is a consent hazard, not a polish issue.
- **Error** — a model was unavailable; a database call failed; a send failed and will be retried. Error copy has three parts in this order: what happened, what was **not** changed, one next step — then a details disclosure with the error code and a copyable trace identifier. "Nothing was written" is not reassurance, it is a true statement backed by a transaction boundary, and the design should let it carry that weight.
- **Stale** — the approval no longer matches the current record, or the case moved while the user was reading. This is a safety mechanism working correctly. Show what changed. On read-only screens it is a persistent bar with a "Show the current record" action and nothing auto-refreshes under the user's eyes; on the approval screen it is the takeover described in section 4.5.
- **Forbidden** — the viewer is not permitted to see this. Judge Mode is permission-gated and its navigation entry does not exist for accounts without it. Design the honest forbidden state — and separately, the deliberately uninformative **not-found** state, because the system returns "not found" rather than "forbidden" for objects belonging to other people, so that the interface never confirms another person's data exists. Two different states, two different designs, and the second one must not leak the first.

## 11. Accessibility, as a product-readiness requirement

Part of the audience scores this product on production seriousness, and accessibility is part of that score. Target WCAG 2.2 level AA.

- Contrast: body and UI text at least 4.5:1; large text and meaningful non-text boundaries at least 3:1 — **in both themes**.
- **No information conveyed by colour alone.** Supports versus contradicts, claim versus record, every status, severity, attention level, epistemic status, and retraction status must be distinguishable with all colour removed. **Include a grayscale proof of at least the grounding list and the contradiction pair.**
- Visible focus indicators on every interactive element: at least 2 pixels, at least 3:1 against both the element and its surroundings, never removed.
- Interactive targets at least 24 × 24 CSS pixels; the approve, reject, and rotate controls at least 44 × 44.
- Meaningful reading order and heading structure. Grounding is heterogeneous and is **not** a table — a table forces empty cells and makes a screen reader announce columns that do not apply. Group counts belong in the headings so a screen-reader user knows the size of a group before entering it. Lineage is an ordered list and each entry states its position before its content.
- Full functionality at 200% zoom and at 320 pixels wide with no horizontal page scrolling. Wide content scrolls inside its own container with a visible affordance.
- A stated reduced-motion behaviour for every motion token, including the case-reopen transition. Under reduced motion, progress is reported as text.
- Skip links on every page, plus "Skip to lineage" and "Skip to conflicts" on State Proof, because the grounding region can be very long.

## 12. Anti-requirements — do not do these

These are not stylistic preferences. Each one is a specific failure I have seen and will reject.

1. **No generic AI-product aesthetic.** No sparkle icons, no thinking shimmer, no orb, no gradient mesh backdrop, no glassmorphism, no assistant avatar. Nothing that signals "a language model made this". The product's whole argument is that the deterministic parts are what make it trustworthy.
2. **No purple-on-dark gradients.** Violet-to-indigo on near-black is the default aesthetic of every AI demo of the last several years and it makes this product invisible in a field of competitors.
3. **No Inter, no Roboto, no `system-ui` default stack, no Helvetica, no Arial.** Choose real typefaces with a point of view and name them explicitly with their weights. If you cannot embed the actual font files, say so plainly, specify the exact typefaces and a documented fallback stack, and note that they will be self-hosted. Do not silently substitute a default and do not pretend a fallback is the design.
4. **No cookie-cutter admin dashboard.** No fixed left icon rail plus top bar plus card grid of statistics. That layout says "internal tool", and this is a personal record about a person's own money.
5. **No chat interface.** There is no message thread, no input box at the bottom, no conversational turn-taking anywhere, not even behind a tab. The product's explicit argument is that a transcript is not a system of record: a transcript has no constraints, no revision semantics, and no permission boundary. A chat surface would contradict the product in its own layout. The only free-text inputs that exist are typed fields on typed forms — a correction statement, a draft subject and body, a rejection reason — and none of them is interpreted as an instruction.
6. **No fabricated data implying capabilities the product does not have.** Do not draw bank-account balances, credit scores, legal advice, "we filed a dispute for you", automated portal logins, calendar integrations, contact lists, or previews of documents that do not exist in section 2. Do not show the system taking an action without approval. The product drafts and asks; it does not act, adjudicate, or advise on entitlement.
7. **No decorative stock illustration and no icon-per-row noise.** Icons must carry meaning; an icon that appears on every row carries none.
8. **No dark patterns around approval.** The approve action is never pre-selected, never the only visible path, and never styled to make rejection feel like a mistake.

## 13. Output format

The result has to be directly implementable by an engineering team working in a modern component framework, so:

1. **Self-contained HTML with embedded CSS.** No external stylesheets, no CDN links, no remote fonts, no remote images, **no network requests of any kind**. Any illustration or icon is inline SVG. Any raster asset is a base64 data URI. The pages must render correctly when opened directly from a local file with no internet connection.
2. **Structure.** One page per screen is fine, and a single page with clearly separated screen sections is also fine — state which you are giving me. Include a light/dark toggle that actually works: define the light palette on the root, redefine the tokens under both the dark colour-scheme preference and an explicit dark attribute on the root so the toggle wins in both directions, and give the page body an explicit background from a token rather than leaving it transparent.
3. **A design-token table** in markdown: token name, light value, dark value, and what it is for.
4. **Per-component implementation notes** in markdown: the states the component has, which tokens it consumes, its layout behaviour from 390 to 1440 pixels, its accessibility requirements, and any non-obvious interaction. Where a component renders a typed value from section 1.6, say which values map to which visual treatment.
5. **Semantic, realistic markup.** Class names an engineer would keep. No inline styles except where a token genuinely must be set per instance. Structure the HTML the way a component tree would be structured, so the translation is mechanical.
6. **A short design rationale** at the top: the direction chosen, the typographic decision, the colour logic, and one paragraph for each of the seven hard problems in section 6 explaining what you did about it.

One constraint on the data you put into the markup: **you invent field labels, you do not invent fields.** Every value you render should be traceable to something described in this brief. If you need a field that this brief does not give you, name it explicitly in your notes as a request rather than quietly drawing it.

## 14. Process — do this first

**Do not produce the full design yet.**

Your first reply must contain **3 to 4 distinct visual directions**, each with:

- a name;
- **one line** of rationale — specifically, why this direction earns instant trust from a person under mild financial stress *and* technical respect from a database engineer;
- the typeface pairing it implies, named explicitly with weights;
- the colour logic in one sentence;
- and the one hard problem from section 6 that this direction is best positioned to solve.

Genuinely distinct means distinct: different typographic voices, different colour logic, different structural metaphors. Four variations on a neutral grey design system is one direction with four accent colours, and I will send it back.

Then **stop and wait for me to choose.** Do not produce tokens, components, or screens in your first reply.

---

## Risks and open questions

Read these before you answer, and take a position on each one in your first reply or your second. They are the places where this brief knows it is asking for something in tension, and I would rather you argue with me than silently pick one side.

**The typography requirement and the no-network requirement pull against each other.** I have banned the default stacks and also banned remote font requests. That leaves you two honest options: embed licensed font files as base64, or specify exact typefaces with a documented fallback and state that they will be self-hosted. Choose one and say which. Do not resolve this silently by falling back to a system stack — that is the one outcome that fails both requirements at once.

**Fair-fight symmetry may read as flat on camera.** Section 5 forbids visually degrading the memory-OFF side, which is right for credibility. But a perfectly symmetrical layout may not read as decisive in a fast-moving screen recording. My assumption is that content asymmetry plus the difference strip carries the persuasion without any layout asymmetry. If you think that assumption is wrong, the acceptable fix is emphasis inside the difference strip — never dimming, shrinking, or subordinating the OFF column.

**"Emotional trust first, technical depth on demand" is a principle, not a measured finding.** Nobody has user-tested it. The depth threshold — how much technical content sits at the first level versus one interaction away — is a judgement call you are being asked to make on my behalf. Make it once, in a single reusable disclosure mechanism, so that if the threshold turns out to be wrong it is one component change and not seven screen changes. Tell me where you put the line and why.

**Nineteen timeline entry kinds, eight component-state matrices, seven screens, and two breakpoints is a large single deliverable.** If your output is going to be truncated, produce it in this order, which is by demonstration value: Dashboard, State Proof, contradiction panel, counterfactual view, action approval, case timeline, Memory Trace, login, upload and forward. A truncated deliverable that follows that order is still usable.

**The reopen choreography is genuinely undecided.** Problem 4 states my posture — play the transition on the commit, not on the arrival of the prose — but I have not tested it and the composed-moment argument is real. I want a recommendation with a reason, not a compliant implementation of my posture.

**One number in this brief will be misread if you render it carelessly.** The corpus figure of 16,035 is the count of records visible to this one user. It is counted at query time, it is not a constant, and it is not the total size of the underlying store. Any surface that renders it as a global system statistic states something false. Design it as a scoped figure with its scope visible in the label.

**Nothing in this brief is a screenshot of a running product.** The system it describes is fully specified and not yet built. The brief is written in the present tense because that is how you brief a designer, not because any of these screens exist. Your deliverable is a design, and it should not be captioned, annotated, or framed anywhere as a record of something that already ran.
