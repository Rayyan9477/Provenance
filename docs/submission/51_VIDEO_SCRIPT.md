# Provenance — Demo Video Script

Purpose: give the person holding the microphone and the person holding the mouse a shot-by-shot, word-by-word, second-by-second script for a submission video that runs 170 seconds, shows the working system, shows the CockroachDB memory layer, and earns all five equally weighted judging criteria without a single fabricated frame.

Status: planning complete v1.1
Implementation status: not started

Audience: whoever records, narrates, and edits the submission video; the gate reviewer signing `G-15`; and any judge who wants to check that what they watched is what the system actually did.

> Nothing in this document may be recorded until `G-13` (deploy) and `G-14` (evals) are signed. This is a script, not a claim that a recording exists.

---

## 0. Contents

1. Hard constraints and the 170-second budget
2. Change-control note: divergence from `00_PRODUCT.md` §5
3. The master script
4. Full shot list
5. Narration timing table
6. Screen-recording setup
7. Pre-load and pre-warm
8. Per-beat fallbacks
9. Honesty rules and on-screen disclosure
10. Title, thumbnail, description
11. The 30-second cut-down
12. Post-production and delivery checklist
13. Risks and open questions

---

## 1. Hard constraints and the 170-second budget

| Constraint | Source | Consequence for this script |
|---|---|---|
| Video **strictly under 180.0 seconds**, measured by `ffprobe` | `quality/23_PHASE_GATES.md` §24 `S4` | Target duration **170.0 s**, giving a 10 s margin. `179.4` passes and `180.2` fails, so the margin is not negotiable. |
| Publicly viewable at a URL returning `200` | `S4` | Unlisted YouTube is acceptable; "anyone with the link" on a Drive folder is not, because Drive can return a consent interstitial rather than `200`. |
| Must show at least **two CockroachDB tools** | `S5` | Distributed Vector Indexing (beat 3 retrieval, beat 7 trace) and the CockroachDB Cloud Managed MCP Server (beat 7). `ccloud` is the third tool and is evidenced in the repository, not on camera. |
| Must show at least **one AWS service** | `S6` | Bedrock model ids, S3 artifact storage, EventBridge Scheduler trigger, and SES send are all on the demo path and all appear on screen. |
| `fixture_mode: false` in the recorded demo | `CANONICAL_DECISIONS.md` "Fixture mode"; `S3` | The live badge in §9 is mandatory, not decorative. |
| Judge Mode is built from persisted rows and spans; scripted trace animation and hard-coded identifiers are forbidden | `CANONICAL_DECISIONS.md` "Judge Mode" | Every number on screen is rendered by the application from an API response. The editor may crop and scale; the editor may not typeset a value. |
| The counterfactual payload diff is a live Q&A artifact, not video content | `CANONICAL_DECISIONS.md`; `00_PRODUCT.md` R2 | Beat 7 shows the two **outputs** side by side for five seconds. It does not show the request diff. |
| Trigger demonstration uses the same manual-wake entry point for a false-predicate no-op and the landlord fire, with no hidden state revert | `CANONICAL_DECISIONS.md` "Trigger demonstration" | Beat 6 contains both wakes. The no-op is not optional garnish; it is the thing that proves the fire was earned. |

**Budget.** 170.0 s of programme, hard cut to black on the last frame. No pre-roll logo, no post-roll credits, no music bed under the first ten seconds. Every second not spent on the product is a second borrowed from a criterion.

**Criterion legend used throughout:** `AMD` Agentic Memory Design · `TI` Technological Implementation · `RWI` Real-World Impact · `PR` Product Readiness · `CO` Creativity and Originality.

---

## 2. Change-control note: divergence from `00_PRODUCT.md` §5

`00_PRODUCT.md` §5 carries an illustrative shot list totalling 2:55 with segments A–G, in which the Memory ON/OFF counterfactual occupies segment D at 1:20–1:45. **This script diverges from it** in three ways:

1. Total runtime is **170 s**, not 175 s, to keep a real margin under the `ffprobe` gate.
2. The counterfactual moves from a standalone 25-second segment to a **5-second closing frame inside the technical reveal** (beat 7). The full side-by-side remains available live and via `GET /v1/judge-mode/counterfactual/{counterfactual_id}`.
3. The prospective-memory reveal expands to include the **false-predicate no-op**, which `00_PRODUCT.md` §5 omitted and `CANONICAL_DECISIONS.md` requires.

Neither the product intent, the vocabulary, the invariants, nor any frozen decision changes. Under `README.md` "Change control", closing this divergence requires one documentation change updating `00_PRODUCT.md` §5's shot-list table and the "Where the judge sees it" column of its rubric table to the segment letters and timecodes used here. **Do that before `G-15` is signed**, or the submission pack contradicts itself in a way a careful judge will notice.

Segment mapping for that edit:

| §5 segment | This script | New timecode |
|---|---|---|
| — (new) | Beat 1, the problem | 0:00–0:20 |
| A | Beat 2, the dashboard | 0:20–0:45 |
| B | Beat 3, invoice and reopen | 0:45–1:15 |
| C | Beat 4, State Proof | 1:15–1:45 |
| E | Beat 5, draft, approval, send | 1:45–2:10 |
| F | Beat 6, the trigger | 2:10–2:35 |
| G + D | Beat 7, trace, MCP, counterfactual | 2:35–2:50 |

---

## 3. The master script

Read the narration column aloud as written. It is metred against §5. Do not improvise: every line was counted.

Currency is spoken in words and shown in figures. The demo user is **Alex Rivera**; the counterparties are **Northline Fiber**, **Harborview Property Management**, **Beltline Movers**, and **Kestrel Analytics**. All are fictional and are disclosed as such in §9.

### Beat 1 — the problem (0:00–0:20)

| Timecode | On-screen action | Narration (exact words) | Criteria |
|---|---|---|---|
| 0:00–0:08 | Cold open on the "The Move" dashboard already loaded, held still. No cursor movement. The four relationship rows and the outstanding total are the only things on screen. Overlay lower-left: the live badge (§9.1). | "Four months ago you moved. A deposit was promised. A reimbursement was half paid. A service was cancelled in writing." | RWI |
| 0:08–0:14 | Slow 1.15× push-in on the two rows carrying money: Harborview `$1,800.00`, Beltline `$220.00`. No clicks. | "Every one of those institutions still has a perfect record of you. You have an inbox." | RWI |
| 0:14–0:20 | Push-in settles. Cursor appears at rest. Nothing has been clicked yet. | "Provenance is the record on your side. Watch what happens when one of them gets it wrong." | RWI, CO |

**Why this earns it.** Real-World Impact is decided in the first ten seconds, before any technology is named. The money is on screen before the word "database" is spoken, which is the rule `00_PRODUCT.md` §5 sets: never let infrastructure precede the product moment.

### Beat 2 — months-old memory across four relationships (0:20–0:45)

| Timecode | On-screen action | Narration (exact words) | Criteria |
|---|---|---|---|
| 0:20–0:28 | Cursor moves to the context header. The context title "The Move — 214 Ridgeway to 88 Larkin", `relationship_count 4`, and `total_outstanding USD 2,020.00` are all legible. | "This is one context: The Move. Four relationships, two thousand and twenty dollars outstanding." | RWI, AMD |
| 0:28–0:38 | Hover the Harborview row: `attention_level URGENT`, outstanding `$1,800.00`, case "Landlord deposit return", status `WAITING`. Then hover the Beltline row: commitment `$420.00` committed, `$200.00` fulfilled, `$220.00` outstanding, status `PARTIAL`. | "The landlord owes eighteen hundred, promised within thirty days of inspection. The movers still owe two hundred and twenty of four hundred and twenty." | RWI, AMD |
| 0:38–0:45 | Hover the Kestrel row (relocation reimbursement, resolved). Then settle the cursor on the Northline Fiber row: case "Old ISP service cancellation", status `RESOLVED`, `revision 12`, last activity four months ago. Hold. | "The employer is settled. And the old ISP cancellation was resolved four months ago. Remember that one." | AMD, RWI |

**Why this earns it.** Agentic Memory Design starts here, quietly: four relationships, distinct statuses, a derived `outstanding_amount` that a model did not compute, and a case whose last activity is four months old. That is memory with a lifecycle, not a transcript.

### Beat 3 — the invoice arrives and the closed case reopens (0:45–1:15)

| Timecode | On-screen action | Narration (exact words) | Criteria |
|---|---|---|---|
| 0:45–0:53 | Cut to the upload / forward screen. Drag `demo/artifacts/northline-june-invoice.eml` onto the drop target. The parsed preview renders: sender Northline Fiber, `Amount due USD 186.00`, `Service period 2026-06-01 to 2026-06-30`, `Account NF-4471-8802`. | "A forwarded email arrives. Northline Fiber. One hundred and eighty-six dollars, for service June first through June thirtieth." | RWI, TI |
| 0:53–1:02 | The artifact row appears with `content_sha256` (first 12 hex characters legible), `s3_key` prefix, `parser_status PARSED`. Three `evidence_items` appear beneath it: `DATE_ASSERTION`, `AMOUNT_ASSERTION`, `IDENTIFIER_ASSERTION`. A `claims` row appears with `claim_kind COUNTERPARTY_CLAIM`, highlighted. | "Nothing here is trusted. The bytes are hashed and admitted as immutable evidence. The amount becomes a counterparty claim, not a fact." | AMD, PR |
| 1:02–1:09 | Auto-navigate to the case detail screen for "Old ISP service cancellation". The identity line renders: exact match on `external_account_ref = NF-4471-8802`. Status badge animates from `RESOLVED` to `REOPENED` on the application's own re-render, not on a video transition. | "Retrieval links it to the account number on a case that closed in May. And the case reopens." | AMD, TI, CO |
| 1:09–1:15 | Hold on the case header: `REOPENED`, `revision 13`, `reopened_count 1`, `attention_level URGENT`. Below it, the conflict card: `VALUE_CONFLICT`, severity `HIGH`. | "Resolved, to reopened. Nobody clicked anything. A contradiction is now a row in the database." | AMD, CO |

**Why this earns it.** This is the "wait, it reopened the case" moment, and it must land before any database concept is explained. The `COUNTERPARTY_CLAIM` badge is the single most important pixel in the video: it is the level ordinary retrieval-augmented generation has no representation for.

**Recording note.** The status flip must be the application re-rendering after its own poll or subscription completes. If the editor has to cut around a wait, §9.3 governs the caption.

### Beat 4 — State Proof: grounding and lineage (1:15–1:45)

| Timecode | On-screen action | Narration (exact words) | Criteria |
|---|---|---|---|
| 1:15–1:23 | Click through to State Proof. The panel header renders the case, the revision, and the note that the proof is assembled by SQL. Two labelled sections are visible at once: **Grounding** and **Lineage**. | "State Proof answers one question. Why does the system believe what it believes? No model wrote this. SQL did." | AMD, PR |
| 1:23–1:33 | Punch in 1.6× on the Grounding section. Three edges render with their relation, weight, and source authority: `SUPPORTS` on the 15 May provider confirmation; `SUPPORTS` on the 31 May service-end notice; `CONTRADICTS` on the new counterparty claim. Each carries a clickable evidence or claim identifier. | "Grounding: two provider documents support the termination. The confirmation on fifteen May, the effective date of thirty-one May. Against them, one interested party claim." | AMD |
| 1:33–1:39 | Punch out, then punch in 1.6× on the Lineage section: version 1 and version 2 of the same belief, side by side, with the supersession reason code rendered between them. | "Lineage is different. Version one, zero owed, confirmed. Version two, zero owed, disputed." | AMD |
| 1:39–1:45 | Hold on the UI's own caption beneath the lineage pair: "the amount did not change; our confidence in it did." Cursor rests on the `CONTRADICTS` edge so its tooltip shows the source authority. | "The amount did not change. Our confidence in it did. A summary cannot say that." | AMD, CO |

**Why this earns it.** Agentic Memory Design is won here or nowhere. The judge sees the two words that this product refuses to conflate — grounding and lineage — rendered as two separate sections of one panel, and sees a belief whose *value* is unchanged while its *epistemic status* moved. `00_PRODUCT.md` R3 names this as the hardest thing in the build to land in twenty seconds; the punch-in and the UI caption are the two mitigations.

**Narration variant, decided at rehearsal, not in the edit.** Line N13 states the lineage pair as "zero owed, confirmed" then "zero owed, disputed", matching `00_PRODUCT.md` §2.3. If the built system instead renders the retained `service_active` belief as the version pair (both `TERMINATED`, `CONFIRMED`, retained under contradiction, per `specs/12_KERNEL_ALGORITHMS.md` §1.6 step 19), record this alternate instead, at the same 13-word length:

> "Lineage is different. Version one said terminated. Version two still says terminated, now contested."

Choose by reading `GET /v1/cases/{case_id}/state-proof` in rehearsal. Never narrate a version pair the panel does not render.

### Beat 5 — the grounded draft, approval, revalidation, send (1:45–2:10)

| Timecode | On-screen action | Narration (exact words) | Criteria |
|---|---|---|---|
| 1:45–1:53 | Navigate to the action approval screen. The drafted reply renders with an inline support chip after each factual sentence, each chip showing a truncated evidence or claim identifier. Hover one chip; it resolves to the 15 May confirmation. | "The Advocate drafts a reply from that proof. Every factual sentence carries the identifier of the evidence behind it." | PR, AMD |
| 1:53–2:01 | Punch in 1.6× on the approval strip: `basis_case_revision 13` and `approval_draft_sha256` (first 12 hex characters legible), recipient shown against the allowlist. Click **Approve & Send**. | "It cannot send. Approval binds to case revision thirteen, and to the SHA-256 of this exact draft. I approve." | PR |
| 2:01–2:10 | The executor panel renders its revalidation line by line as the rows appear: revision re-read and matched, draft hash re-computed and matched, recipient allowlisted, then `action_executions` status and the provider correlation id. The controlled demo mailbox shows the message arriving. | "The executor re-reads the case, confirms the revision and the hash still match, then sends. If memory had moved, this aborts." | PR |

**Why this earns it.** Product Readiness is a claim about what the system *refuses* to do, and refusals are invisible unless you show the check running. The revalidation line is the only place in the video where a judge sees invariant 4 — actions are permissioned — as a mechanism rather than an assertion.

### Beat 6 — the second reveal, prospective memory (2:10–2:35)

| Timecode | On-screen action | Narration (exact words) | Criteria |
|---|---|---|---|
| 2:10–2:17 | Cut to Judge Mode, triggers list. Two armed triggers are visible with their `trigger_type`, `not_before`, and `basis_case_revision`. Cursor moves toward the first. | "One more thing. Memory here is not only reactive. Watch a trigger the user never set." | CO, AMD |
| 2:17–2:24 | Wake the movers follow-up trigger (`RESPONSE_DEADLINE`, case "Movers damage reimbursement") through `POST /v1/judge/triggers/{trigger_id}/wake`. The result renders: `NO_OP`, reason `PREDICATE_FALSE`, `cases.revision` unchanged, trigger re-armed. | "Same button, same evaluator. This one says no-op. The predicate is false, so nothing happens." | PR, AMD |
| 2:24–2:30 | Wake the deposit trigger (`COMMITMENT_DEADLINE`, case "Landlord deposit return"). The evaluated field values render from the projection: `outstanding_amount 1800.0000`, `due_at` in the past, commitment `status ACTIVE`. Result: `FIRED`, reason `COMMITMENT_OVERDUE_UNPAID`. | "Now the landlord deposit. Thirty days elapsed. Eighteen hundred still outstanding. It fires." | CO, TI |
| 2:30–2:35 | The case card updates: status `WAITING` to `ACTIONABLE`, attention raised, `trigger.fired.v1` listed in the outbox. Hold on the case card. | "Nobody set that reminder. The memory of an unmet promise woke itself." | CO, RWI |

**Why this earns it.** The no-op is what makes the fire credible. A trigger that always fires is a timer; a trigger that refuses on a false predicate, through the identical `evaluate_trigger()` entry point, is prospective memory that re-evaluates current state. `specs/16_TRIGGER_DSL.md` §13.1 makes the shared entry point structural, and `CANONICAL_DECISIONS.md` forbids mutating and reverting canonical state to stage this.

### Beat 7 — the technical reveal (2:35–2:50)

| Timecode | On-screen action | Narration (exact words) | Criteria |
|---|---|---|---|
| 2:35–2:41 | Cut to Judge Mode Panel C, Memory Trace, scrolled to the kernel node. Rendered from `GET /v1/cases/{case_id}/memory-trace`: `decision ACCEPTED_WITH_CONFLICT`, `case_revision_before 12`, `case_revision_after 13`, `retry_count 0`, and the memory operations list — evidence admitted, claim recorded, conflict opened, case reopened, state transition, outbox event. | "One serializable transaction moved this case from revision twelve to thirteen. Claim, conflict, reopen, outbox event." | TI, AMD |
| 2:41–2:45 | Scroll one node up. The MCP calls render as first-class trace nodes: `agent_case_context_v1`, `agent_evidence_retrieval_v1`, `agent_active_beliefs_v1`, each with `sql_role pv_agent_reader`, `access_mode READ_ONLY`, row counts, durations. The retrieval node shows `vector_index evidence_embedding_ann_idx`, `corpus_size_user_scoped`, candidates before and after rerank, `retraction_filter_applied true`, `cross_user_results 0`. | "Read through the CockroachDB MCP server, read-only, five governed views." | TI, PR |
| 2:45–2:50 | Cut to the counterfactual panel, already resolved, two columns held static and legible. Left, Memory OFF: the one-line summary containing `$186`. Right, Memory ON: the summary naming the 15 May confirmation and the reopen. Beneath both, `safety.case_revision_changed_by_counterfactual: false`. Hard cut to black on the last word. | "With memory off, the same model says only: one eighty-six due." | CO, TI |

**Why this earns it.** Technological Implementation needs one artifact a judge can verify at a glance: the revision transition inside a single serializable commit, with `retry_count` beside it. The MCP node earns the sponsor-tool claim with the SQL role visible, so the permission boundary is legible as a grant rather than as prompt discipline. Ending on the counterfactual leaves the last impression as "the memory did this, not the model."

---

## 4. Full shot list

Capture source abbreviations: **BR** browser window capture · **ED** editor-side punch-in on BR footage · **VO** voiceover only.

| # | Beat | In–Out | Source | What is captured | Asset or precondition |
|---|---|---|---|---|---|
| S01 | 1 | 0:00–0:08 | BR | Dashboard at rest, live badge visible | Seeded database, dashboard pre-warmed |
| S02 | 1 | 0:08–0:14 | ED | 1.15× push-in on the two money rows | Reuse S01 footage |
| S03 | 1 | 0:14–0:20 | ED | Push-in settles, cursor idles | Reuse S01 footage |
| S04 | 2 | 0:20–0:28 | BR | Context header, four relationships, `$2,020.00` | Same take as S01 if the operator holds still |
| S05 | 2 | 0:28–0:38 | BR | Hover Harborview, then Beltline | Hover states must render outstanding amounts |
| S06 | 2 | 0:38–0:45 | BR | Hover Kestrel, settle on Northline `RESOLVED / revision 12` | Case 1 must read `revision 12` |
| S07 | 3 | 0:45–0:53 | BR | Drag-and-drop `.eml`, parsed preview | `demo/artifacts/northline-june-invoice.eml` |
| S08 | 3 | 0:53–1:02 | BR | Artifact hash, three evidence rows, `COUNTERPARTY_CLAIM` | Live agent run; no fixture mode |
| S09 | 3 | 1:02–1:09 | BR | Identity match line, `RESOLVED` → `REOPENED` re-render | Live kernel commit |
| S10 | 3 | 1:09–1:15 | BR | Case header at `revision 13`, conflict card | Same take as S09 |
| S11 | 4 | 1:15–1:23 | BR | State Proof panel, both sections visible | `GET /v1/cases/{id}/state-proof` |
| S12 | 4 | 1:23–1:33 | ED | 1.6× punch-in on Grounding, three edges | Reuse S11 footage |
| S13 | 4 | 1:33–1:39 | ED | 1.6× punch-in on Lineage, version pair | Reuse S11 footage |
| S14 | 4 | 1:39–1:45 | ED | UI caption plus `CONTRADICTS` tooltip | Reuse S11 footage; tooltip must be live |
| S15 | 5 | 1:45–1:53 | BR | Draft with support chips, one chip hovered | Live Advocate run |
| S16 | 5 | 1:53–2:01 | ED | 1.6× on `basis_case_revision` and `approval_draft_sha256`, then the click | Punch-in from a BR take that includes the click |
| S17 | 5 | 2:01–2:10 | BR | Executor revalidation lines, `action_executions`, mailbox arrival | Controlled demo mailbox in a second window |
| S18 | 6 | 2:10–2:17 | BR | Judge Mode triggers list, two armed rows | Both triggers `ARMED` |
| S19 | 6 | 2:17–2:24 | BR | Movers trigger wake → `NO_OP / PREDICATE_FALSE` | Predicate must be false at demo time |
| S20 | 6 | 2:24–2:30 | BR | Deposit trigger wake → field values, `FIRED` | `not_before` elapsed, outstanding `1800.0000` |
| S21 | 6 | 2:30–2:35 | BR | Case card `WAITING` → `ACTIONABLE`, outbox event | Same take as S20 |
| S22 | 7 | 2:35–2:41 | BR | Memory Trace kernel node, 12 → 13, `retry_count 0` | `GET /v1/cases/{id}/memory-trace` |
| S23 | 7 | 2:41–2:45 | BR | MCP nodes with `sql_role`, vector index node | Same take as S22, scrolled |
| S24 | 7 | 2:45–2:50 | BR | Counterfactual two-column panel, `safety` block | Counterfactual run before recording, replayed |

**Take discipline.** S01–S06 should come from one continuous browser take; S07–S10 from a second; S11–S14 from a third; S15–S17 from a fourth; S18–S21 from a fifth; S22–S24 from a sixth. Six takes, six cuts, no cross-fades. A shot that needs eleven takes is a shot that is fighting the product, and the correct response is to change the shot, not to fake it.

---

## 5. Narration timing table

Pace target **2.45 words per second** (147 words per minute) — measured, unhurried, no rushing over numbers. Every slot below has slack; the slack is where the visual lands.

| Line | Slot | Seconds | Words | Words / s | Spoken text |
|---|---|---|---|---|---|
| N1 | 0:00–0:08 | 8.0 | 20 | 2.50 | Four months ago you moved. A deposit was promised. A reimbursement was half paid. A service was cancelled in writing. |
| N2 | 0:08–0:14 | 6.0 | 16 | 2.67 | Every one of those institutions still has a perfect record of you. You have an inbox. |
| N3 | 0:14–0:20 | 6.0 | 17 | 2.83 | Provenance is the record on your side. Watch what happens when one of them gets it wrong. |
| N4 | 0:20–0:28 | 8.0 | 14 | 1.75 | This is one context: The Move. Four relationships, two thousand and twenty dollars outstanding. |
| N5 | 0:28–0:38 | 10.0 | 24 | 2.40 | The landlord owes eighteen hundred, promised within thirty days of inspection. The movers still owe two hundred and twenty of four hundred and twenty. |
| N6 | 0:38–0:45 | 7.0 | 17 | 2.43 | The employer is settled. And the old ISP cancellation was resolved four months ago. Remember that one. |
| N7 | 0:45–0:53 | 8.0 | 19 | 2.38 | A forwarded email arrives. Northline Fiber. One hundred and eighty-six dollars, for service June first through June thirtieth. |
| N8 | 0:53–1:02 | 9.0 | 22 | 2.44 | Nothing here is trusted. The bytes are hashed and admitted as immutable evidence. The amount becomes a counterparty claim, not a fact. |
| N9 | 1:02–1:09 | 7.0 | 18 | 2.57 | Retrieval links it to the account number on a case that closed in May. And the case reopens. |
| N10 | 1:09–1:15 | 6.0 | 15 | 2.50 | Resolved, to reopened. Nobody clicked anything. A contradiction is now a row in the database. |
| N11 | 1:15–1:23 | 8.0 | 19 | 2.38 | State Proof answers one question. Why does the system believe what it believes? No model wrote this. SQL did. |
| N12 | 1:23–1:33 | 10.0 | 25 | 2.50 | Grounding: two provider documents support the termination. The confirmation on fifteen May, the effective date of thirty-one May. Against them, one interested party claim. |
| N13 | 1:33–1:39 | 6.0 | 13 | 2.17 | Lineage is different. Version one, zero owed, confirmed. Version two, zero owed, disputed. |
| N14 | 1:39–1:45 | 6.0 | 15 | 2.50 | The amount did not change. Our confidence in it did. A summary cannot say that. |
| N15 | 1:45–1:53 | 8.0 | 19 | 2.38 | The Advocate drafts a reply from that proof. Every factual sentence carries the identifier of the evidence behind it. |
| N16 | 1:53–2:01 | 8.0 | 20 | 2.50 | It cannot send. Approval binds to case revision thirteen, and to the SHA-256 of this exact draft. I approve. |
| N17 | 2:01–2:10 | 9.0 | 22 | 2.44 | The executor re-reads the case, confirms the revision and the hash still match, then sends. If memory had moved, this aborts. |
| N18 | 2:10–2:17 | 7.0 | 16 | 2.29 | One more thing. Memory here is not only reactive. Watch a trigger the user never set. |
| N19 | 2:17–2:24 | 7.0 | 16 | 2.29 | Same button, same evaluator. This one says no-op. The predicate is false, so nothing happens. |
| N20 | 2:24–2:30 | 6.0 | 13 | 2.17 | Now the landlord deposit. Thirty days elapsed. Eighteen hundred still outstanding. It fires. |
| N21 | 2:30–2:35 | 5.0 | 12 | 2.40 | Nobody set that reminder. The memory of an unmet promise woke itself. |
| N22 | 2:35–2:41 | 6.0 | 16 | 2.67 | One serializable transaction moved this case from revision twelve to thirteen. Claim, conflict, reopen, outbox event. |
| N23 | 2:41–2:45 | 4.0 | 11 | 2.75 | Read through the CockroachDB MCP server, read-only, five governed views. |
| N24 | 2:45–2:50 | 5.0 | 12 | 2.40 | With memory off, the same model says only: one eighty-six due. |
| **Total** | **0:00–2:50** | **170.0** | **411** | **2.42** | |

**Verification before the edit is locked.** Record the voiceover first, dry, against a stopwatch, and check:

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 audio/vo-master.wav
#   → must be <= 168.0 ; if it exceeds, cut words, never speed up the take
```

The three fastest lines are N3 (2.83), N23 (2.75), and N2/N22 (2.67). If any of them sounds rushed on playback, delete a word rather than compress the slot: N3 loses "of them", N23 loses "governed", N22 loses "this".

---

## 6. Screen-recording setup

### 6.1 Display and capture

| Setting | Value | Reason |
|---|---|---|
| Output resolution | 1920 × 1080, 30 fps, progressive | Universal, and every punch-in in §4 is at most 1.6×, which stays above 1200 px source width when the source is captured at 1080p. |
| OS display scaling | 100 % | Fractional scaling produces half-pixel text edges that survive re-encoding as mush. |
| Capture mode | OBS Studio, **Window Capture** on the Chrome window | Window capture excludes the taskbar, notification toasts, and any second monitor. Display capture invites an accident. |
| Encoder | x264, CRF 18, preset `slow`, High profile, `yuv420p` | `yuv420p` because several players show green frames on `yuv444p`. |
| Bitrate ceiling | 12 Mb/s | Above this, upload time becomes a submission-day risk for no visible gain. |
| Audio | 48 kHz, AAC 192 kb/s, mono | Mono voiceover; stereo adds nothing and doubles the phase-cancellation failure mode. |

### 6.2 Browser chrome

Launch a dedicated clean profile so no extension, no autofill dropdown, and no saved-password bubble can appear mid-take:

```bash
chrome \
  --user-data-dir="$HOME/.provenance-demo-profile" \
  --window-size=1920,1080 \
  --window-position=0,0 \
  --hide-crash-restore-bubble \
  --disable-features=Translate,MediaRouter \
  --disable-extensions \
  --no-default-browser-check \
  --no-first-run \
  "$PV_WEB"
```

- **Keep the address bar visible.** The public demo URL on screen is evidence that this is a deployed application and not a local build. Hiding it with `--app=` saves 40 pixels and costs credibility.
- **Hide the bookmarks bar** (`Ctrl+Shift+B` until it is off).
- **One tab only.** A second tab reads as "the other tab has the real one".
- **Page zoom 125 %** (`Ctrl` + `+` twice from 100 %). Chrome's zoom steps are 100 → 110 → 125. At 125 % on a 1080p capture, body text renders around 20 px and the numbers that matter render above 28 px, which survives a judge watching in a 640 px-wide embedded player.
- **No live zoom changes during a take.** Changing zoom mid-take reflows the layout and looks like a glitch. All emphasis is a post-production punch-in (§4, `ED` shots).

### 6.3 Cursor

- Cursor **visible at all times**. An invisible cursor makes a live demo look like a rendered animation, which is the exact suspicion `CANONICAL_DECISIONS.md` forbids Judge Mode from earning.
- Windows pointer size **2**, default scheme. Not a giant novelty cursor.
- Click visualisation **on**, as a single subtle ring at the click point, 300 ms, no sound.
- **No** cursor smoothing, no auto-zoom-follow, no motion trails. Those effects are the visual grammar of a scripted product tour, and this video's entire argument is that nothing is scripted.
- Move deliberately. One target per move, arrive, pause 200 ms, then click.

### 6.4 Legibility for judges on small screens

Assume the worst realistic viewing condition: a 640 px-wide embedded player on a laptop, watched once, at 1×.

1. **Minimum on-screen text height after any punch-in: 24 px at 1080p.** Anything smaller is decoration and should not be a shot.
2. **The seven values a judge must be able to read** are, in order of importance: `$2,020.00`; `COUNTERPARTY_CLAIM`; `RESOLVED → REOPENED`; `revision 12 → 13`; `SUPPORTS` / `CONTRADICTS`; `pv_agent_reader` + `READ_ONLY`; `FIRED`. Each gets either a punch-in or a full-frame hold. None of them gets a fly-by.
3. **Hold every number for at least 1.5 s** after the narration names it.
4. **No text overlay smaller than 32 px**, and overlays live in the lower third with a 70 %-opacity plate behind them so they survive re-encoding.
5. **Contrast:** the recording runs in the application's light theme. Dark themes lose thin-stroke digits to YouTube's compression far more readily.

---

## 7. Pre-load and pre-warm

Run in this order. This is a checklist, not a suggestion; two of these items are the difference between a demo and a reseed at 2 a.m.

### 7.1 Rehearse, then reset

```bash
source ops/gate-env.sh

# 1. Full rehearsal of every beat, end to end, on the deployed stack.
#    This warms every cold path AND proves the take is possible today.

# 2. Then destroy what the rehearsal did. The recording must start from seed.
make demo-reset && make seed && make db-verify
#   → "V1 0  V2 0  V3 0  V4 0  V5 0  V6 0  V7 0  V8 0  V9 0  V10 0  V11 3"
```

`make demo-reset && make seed` is the same pair the `S10` submission gate requires. Rehearsing and *not* resetting is the single most likely way to record an invalid video: the case would already be at `revision 13`, the deposit trigger would already be `FIRED`, and the second wake would correctly return `NO_OP / TRIGGER_NOT_ARMED` on camera.

### 7.2 Pre-load assertions

Every one of these must produce the stated output before a single frame is recorded.

```bash
# P1 — the hero case is closed and at revision 12.
HERO_CASE=$(cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT id FROM cases WHERE title = 'Old ISP service cancellation';" | tail -n +2)
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT status, revision, reopened_count FROM cases WHERE id = '$HERO_CASE';"
#   → RESOLVED,12,0

# P2 — the June invoice is NOT in the database. It is uploaded on camera.
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT count(*) FROM claims WHERE object_json->>'amount' LIKE '186%';"
#   → 0
test -s demo/artifacts/northline-june-invoice.eml && echo "artifact present"
#   → artifact present

# P3 — both triggers are ARMED, and the deposit deadline has elapsed.
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT trigger_type, state, not_before < now() AS elapsed
  FROM prospective_triggers ORDER BY 1;"
#   → COMMITMENT_DEADLINE,ARMED,true
#   → RESPONSE_DEADLINE,ARMED,<either>

# P4 — the no-op trigger really will no-op. Dry run writes nothing.
curl -sS -X POST "$PV_API/v1/judge/triggers/$MOVERS_TRIGGER/wake" \
  -H "Authorization: Bearer $PV_TOKEN" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"dry_run": true}' | jq '{outcome_preview, reason_code}'
#   → outcome_preview NO_OP, reason_code PREDICATE_FALSE
#   If this returns anything else, §8 beat 6 fallback applies and the shot changes.

# P5 — the commitment ledger is exactly as narrated.
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT committed_amount, fulfilled_amount, outstanding_amount, status
  FROM commitments ORDER BY committed_amount;"
#   → 420.0000,200.0000,220.0000,PARTIAL
#   → 1800.0000,0.0000,1800.0000,ACTIVE
#   → 2350.0000,2350.0000,0.0000,FULFILLED

# P6 — live mode, and the deployed build is the reviewed build.
curl -sS "$PV_API/v1/version" | jq '{git_sha, fixture_mode, agent_mode, schema_revision}'
#   → fixture_mode false ; build_sha equal to `git rev-parse HEAD`

# P7 — the vector index is on the query the demo runs.
cockroach sql --url "$PV_DB_APP" -e "EXPLAIN <the retrieval query>;" | grep evidence_embedding_ann_idx
#   → a line naming the index; a "full scan" line here means beat 7 must not claim vector indexing

# P8 — the recipient allowlist contains the controlled demo mailbox and nothing else.
echo "$PV_ACTION_ALLOWLIST"
#   → exactly one address, and it is the mailbox that will be on screen in S17
```

### 7.3 Pre-warm

Cold starts are the second most likely way to lose a take. `G13.8` allows a cold start above 10 s only if the demo script includes a warm-up, so here it is.

```bash
# W1 — control plane, three times, until time_total settles under 1.0 s.
for i in 1 2 3; do
  curl -sS -o /dev/null -w '%{time_total}\n' "$PV_API/v1/me" -H "Authorization: Bearer $PV_TOKEN"
done

# W2 — every read model the video touches.
for p in "/v1/dashboard" "/v1/cases/$HERO_CASE" "/v1/cases/$HERO_CASE/state-proof" \
         "/v1/cases/$HERO_CASE/memory-trace" "/v1/triggers" "/v1/action-intents"; do
  curl -sS -o /dev/null -w "$p %{time_total}\n" "$PV_API$p" -H "Authorization: Bearer $PV_TOKEN"
done

# W3 — both Bedrock tiers, by id, one call each.
python -m agents.runtime.tools.smoke --tier E --tier R --print-model-id
#   → "tier=E model=anthropic.claude-haiku-4-5 ok"
#   → "tier=R model=anthropic.claude-opus-5 ok"

# W4 — the counterfactual for beat 7, run BEFORE recording so S24 replays a resolved run.
#      REPLAY_COMMITTED is the default and reads the already-committed decision, so it is
#      fast and it changes nothing. Confirm safety.case_revision_changed_by_counterfactual.

# W5 — the frontend: load every screen once in the demo profile, then hard-reload the
#      dashboard so the recording starts from a cold render of warm data.
```

### 7.4 Environment hygiene

- Notifications off at the OS level (Windows Focus Assist / macOS Do Not Disturb). One Slack toast ruins a take and, worse, may leak a name.
- Second monitor disconnected or the demo mailbox window placed there deliberately for S17.
- Wired network. Record the network you are on; a hotspot mid-take is a re-record.
- Wall clock and any visible timestamps should be consistent with the seed anchor `DEMO_ANCHOR = 2026-09-18T09:00:00-04:00`. If the machine clock disagrees wildly with the seeded dates, do not fake the clock — the seed computes offsets from the anchor precisely so "four months ago" stays true whenever the recording happens.

---

## 8. Per-beat fallbacks

A fallback is what you do *in the take*, not a licence to fabricate. Every fallback below preserves the rule that nothing on screen is typeset by an editor.

| Beat | Failure | Immediate fallback | Cost if used |
|---|---|---|---|
| 1 | Dashboard slow to first paint | Start the take three seconds later; the shot is a static hold and the first three seconds are expendable. | None. |
| 2 | A hover state does not render outstanding amounts | Click into the relationship detail screen instead of hovering; re-time N5 across the click. | Loses one second of pace, no content. |
| 3 | Agent run exceeds 25 s from drop to reopen | Let it run. In the edit, cut the wait and place the §9.3 elapsed-time caption over the cut. | Honest, and a judge who reads the caption learns the real latency. |
| 3 | Agent run fails (Bedrock throttle, schema-repair exhaustion) | Retry once. The artifact is content-hashed, so a second upload of the same bytes is one logical artifact and the second attempt is not a duplicate commit. If it fails twice, stop; reset and reseed before trying again, because a partial run leaves the case at an unknown revision. | A reseed costs about ten minutes; embeddings come from `db/seeds/vectors.parquet`, not from Bedrock. |
| 3 | Case flips but the conflict card does not render | Hold on the case header only and drop the conflict card to beat 4, where State Proof shows the `CONTRADICTS` edge anyway. Cut N10 to "Resolved, to reopened. Nobody clicked anything." | Loses the word "contradiction" nine seconds early; beat 4 recovers it. |
| 4 | The lineage pair renders the `service_active` belief rather than `balance_owed` | Use the pre-approved N13 alternate in §3 beat 4. This is decided at rehearsal, not in the edit. | None. Both are true; only one is on screen. |
| 4 | A support chip tooltip does not resolve | Click the chip and let it navigate to the evidence detail; punch in there instead. | Costs one second, gains a stronger shot. |
| 5 | SES send fails or the mailbox is slow | Keep rolling: the executor panel still shows revalidation passing and the `action_executions` row with its attempt number. End the beat on the row rather than the mailbox and cut N17 after "then sends." | Loses the arrival shot. The revalidation, which is the criterion-earning part, is intact. |
| 5 | Approval is stale because something else committed | This is a *correct* refusal and it is a legitimate shot: show `ABORTED_STALE` / `CASE_REVISION_MOVED`, and narrate the alternate line "Something changed the case. The approval is stale, and it refuses to send." (13 words, fits the 9 s slot.) Then reseed and re-record the intended take. | None if used deliberately. Do not use it accidentally. |
| 6 | The movers trigger does not return `NO_OP` | Skip S19 entirely and give its 7 seconds to S20/S21, extending the field-value hold. Note the omission in the live Q&A: the no-op is still demonstrable on request. | Weakens the "it re-evaluates" argument. Prefer fixing the seed. |
| 6 | The deposit trigger is already `FIRED` | Stop. This means §7.1 was skipped. Reseed and restart the whole recording session. There is no in-take fallback, and disarming and re-arming it by hand is exactly the hidden mutation `CANONICAL_DECISIONS.md` forbids. | A full reseed and a lost session. |
| 7 | Memory Trace is slow to assemble | Pre-warm it in W2 and open the panel one beat early during S21, so it is rendered by the time the cut lands. | None. |
| 7 | The counterfactual has not resolved | Use the run from W4, which is already resolved and stored, and show its stored result. It is the same artifact, the same graph, and the same stored rows a judge can re-fetch by `counterfactual_id`. | None; this is why W4 exists. |
| Any | Fixture mode turns out to be on | Stop recording. A recorded demo with `fixture_mode: true` invalidates the submission under `S3`. Fix the environment, do not crop the banner. | The banner is non-dismissible by design (`G12.7`). |

---

## 9. Honesty rules and on-screen disclosure

These are not stylistic preferences. `CANONICAL_DECISIONS.md` makes seed disclosure and fixture-mode disclosure binding, and `23_PHASE_GATES.md` §23.12 calls undisclosed fixture mode fraud.

### 9.1 The live badge — present for all 170 seconds

A persistent lower-left overlay, 32 px text on a 70 %-opacity plate:

```
LIVE  ·  fixture_mode: false  ·  build <first 8 of build_sha>
```

The values are read from `GET /v1/healthz` in the pre-flight and typed into the overlay **once**, and the same `/healthz` response is shown at full legibility in the description's linked repository. If a judge disputes it, the check is one `curl` away and returns the same `build_sha`.

### 9.2 The seeded-versus-computed caption

At S23, when `corpus_size_user_scoped` appears, a three-second lower-third caption:

```
18,000 decoy evidence rows are synthetic. 32 hero evidence items are hand-curated.
The conflict, the reopen, the revision increment, the trigger evaluation,
and the draft are computed live.
```

This is the same sentence as `README.md` "What is seeded vs what is computed", which `S7` requires. Saying it on camera costs three seconds and removes the single most damaging question a judge can ask afterwards.

### 9.3 Trimmed waits

Any cut that removes real waiting time carries a caption at the cut point, for at least 1.5 s:

```
wait trimmed · 41 s elapsed
```

The number is the true elapsed wall time, read from the trace or the recording timeline. Never smooth-speed a wait without the caption; a 4× speed ramp with no label is indistinguishable from a faster system.

### 9.4 What must never appear

- A typeset value. If a number is on screen, the application rendered it. The editor may crop, scale, and hold. The editor may not set type over the product.
- A motion graphic depicting database rows, a commit, or a trace. Beat 7 shows the real Memory Trace panel or it shows nothing.
- The Memory OFF panel with a different prompt, a different model, or a different artifact than the ON panel. `13_RETRIEVAL_SPEC.md` §14.7 makes this a single request flag; the video must not imply anything richer.
- Any real person's name, address, account number, or mailbox. Every identity on screen is fictional.
- Raw model reasoning. `G12.6` asserts no chain-of-thought reaches the browser; do not go looking for a panel that would contradict it.

### 9.5 The disclosure line in the video description

Verbatim, in the published description:

> Alex Rivera, Northline Fiber, Harborview Property Management, Beltline Movers, Kestrel Analytics, and every amount shown are fictional. 18,000 decoy evidence rows are synthetic; 32 hero evidence items are hand-curated; the conflict detection, case reopen, revision increment, trigger evaluation, and drafted reply are computed live during this recording. Runtime models: anthropic.claude-opus-5, anthropic.claude-haiku-4-5, amazon.titan-embed-text-v2:0. Full tool-usage disclosure in SUBMISSION.md.

---

## 10. Title, thumbnail, description

### 10.1 Title

**Recommended:** `Provenance — the closed case that reopened itself`

Rationale: it is a claim about behaviour, not a category, and it sets up the beat-3 payoff in seven words. It survives truncation in a YouTube grid at roughly 50 characters, and it does not lead with sponsor names, which read as compliance rather than confidence.

**Platform-specific variant** where the submission form or a hackathon gallery shows sponsors: `Provenance — the closed case that reopened itself (CockroachDB × AWS Agentic Memory)`. Keep the product-first half intact; append, never prepend.

**Rejected alternatives, and why:** "Provenance: Agentic Memory on CockroachDB" leads with the mechanism and earns nothing on Real-World Impact. "AI That Remembers Your Bills" is legible but promises a life assistant, which is an explicit non-goal. "A System of Record for the Institutions That Already Have One of You" is the tagline, and it is a great second line and a poor title at 62 characters.

### 10.2 Thumbnail

1280 × 720. A single split frame, no faces, no stock photography.

| Element | Specification |
|---|---|
| Layout | Vertical 50/50 split with a 4 px divider. |
| Left half | A desaturated crop of the real case header at `RESOLVED`, `revision 12`. |
| Right half | The same crop at `REOPENED`, `revision 13`, in the application's urgent accent colour. |
| Overlay text | `RESOLVED` (left) and `REOPENED` (right), 96 px, weight 700, all caps. |
| Lower band | `four months later, one invoice` — 44 px, sentence case, on a solid plate. |
| Wordmark | `Provenance` bottom-right, 40 px, low contrast. |
| Forbidden | Arrows, circles, shocked faces, gradients over text, and any number smaller than 40 px. |

The thumbnail is built from two frames of the actual recording, so it is subject to §9.4: crop and scale only, plus the overlay text, which is commentary and not a product value.

### 10.3 Description block

```
Provenance is a personal system of record for your relationships with institutions.
Evidence is append-only. Beliefs are revisable. State is transactional. Actions are permissioned.

0:00  The problem
0:20  Four relationships, four months old
0:45  A June invoice arrives and a closed case reopens
1:15  State Proof: grounding and lineage
1:45  A grounded draft, human approval, executor revalidation
2:10  A trigger nobody set
2:35  One serializable commit, the MCP read path, and memory off

Built on CockroachDB Cloud (canonical state, Distributed Vector Indexing, transactional outbox),
the CockroachDB Cloud Managed MCP Server, and AWS (Bedrock AgentCore Runtime, Bedrock models,
S3, Cognito, App Runner, EventBridge + Scheduler, SQS, SES, CloudWatch, Amplify Hosting).

Repository: <public repo URL>          Live demo: <public demo URL>

<the §9.5 disclosure line, verbatim>
```

The two angle-bracketed items are filled from `S1` and `S3` output at publish time; they are the only two values in this document deliberately not fixed here, because they do not exist until `G-13` and `G-0` produce them.

---

## 11. The 30-second cut-down

Purpose: a social post, a gallery preview tile, and the clip you send to someone who will not watch three minutes. It is **not** a submission artifact and does not replace the 170-second video.

Total 30.0 s. Assembled entirely from footage already captured for the main cut; no new takes, no new narration recording session beyond the four lines below.

| Timecode | Source shots | On-screen | Narration (exact words) | Criteria |
|---|---|---|---|---|
| 0:00–0:07 | S01, S06 | Dashboard hold, then the Northline row at `RESOLVED`, `revision 12` | "Four months ago you moved. This case was closed." | RWI |
| 0:07–0:15 | S07, S09 | The `.eml` drop, then `RESOLVED` → `REOPENED` on the app's own re-render | "Then the provider billed you for June, after confirming your cancellation in May." | AMD, CO |
| 0:15–0:24 | S12, S13 | Grounding punch-in, then lineage punch-in | "Two documents support the termination. One interested party claim opposes it. The record keeps both." | AMD |
| 0:24–0:30 | S22 | Memory Trace kernel node, 12 → 13, hard cut to black | "One serializable commit. Revision twelve to thirteen. Provenance." | TI |

Word count: 12 + 13 + 16 + 9 = **50 words over 30 s = 1.67 words per second.** Deliberately slower than the main cut, because a short clip is usually watched with sound off and the visuals must carry it. Burn in captions for all four lines, 40 px, lower third, since sound-off is the default.

The live badge from §9.1 stays. The §9.2 seeded-versus-computed caption is replaced by a single line in the post copy, because there is no room for it on screen at this length.

---

## 12. Post-production and delivery checklist

```bash
# C1 — duration, the gate that fails submissions.
ffprobe -v error -show_entries format=duration -of csv=p=0 demo/provenance-demo.mp4
#   → 170.0 (accept 168.0–172.0; anything at or above 180.0 is a FAIL under S4)

# C2 — the stream is what players expect.
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,pix_fmt \
  -of default=nw=1 demo/provenance-demo.mp4
#   → h264 / 1920 / 1080 / 30000/1001 or 30/1 / yuv420p

# C3 — loudness, so a judge does not reach for the volume knob.
ffmpeg -i demo/provenance-demo.mp4 -af ebur128=peak=true -f null - 2>&1 | tail -20
#   → integrated -16 LUFS +/- 1.0 ; true peak <= -1.5 dBTP

# C4 — publicly viewable, unauthenticated.
curl -sS -o /dev/null -w '%{http_code}\n' "<public video url>"
#   → 200

# C5 — watched end to end, in a private window, by someone who did not edit it.
#      Record the reviewer's name in ops/gates/SUBMISSION.md. This is S4's last clause
#      and it is the only check in this document that a machine cannot run.
```

Manual checks, all of which must be initialled in `ops/gates/SUBMISSION.md`:

- [ ] The live badge is legible in every frame, including during punch-ins.
- [ ] Every value on screen was rendered by the application; nothing was typeset.
- [ ] Every trimmed wait carries its true elapsed-time caption.
- [ ] The seeded-versus-computed caption appears and is readable at 640 px width.
- [ ] `fixture_mode: false` appears on screen at least once at full legibility.
- [ ] No real name, address, account number, or mailbox appears in any frame, including browser autofill, tab titles, and the OS clock area.
- [ ] The `RESOLVED → REOPENED` flip is a re-render of the application, not a video transition.
- [ ] The counterfactual panel shows both columns produced by the same model and prompt.
- [ ] `00_PRODUCT.md` §5's shot-list table has been updated per §2 of this document.
- [ ] The description contains the §9.5 disclosure line verbatim.

---

## 13. Risks and open questions

**R1 — This script contradicts `00_PRODUCT.md` §5's shot list, and the contradiction is in the submission pack until someone fixes it.** §2 states the divergence and the exact edit required. *Residual risk:* a reviewer reads §5, watches the video, and concludes the pack is stale. **Decision:** the `00_PRODUCT.md` §5 edit is a blocking item on the §12 checklist, not a nice-to-have.

**R2 — The case revision numbers differ between authorities, and the video puts one of them on screen for six seconds.** `10_DATABASE_DDL.md` §17.4 seeds case 1 at `revision = 12`; `00_PRODUCT.md` §2.3, `15_API_SPEC.md`, `G4.1`, and `G12.1` all use 12 → 13. `12_KERNEL_ALGORITHMS.md` §1.6's worked example uses 7 → 8 with its own illustrative fixture. This script narrates **twelve to thirteen** because the seed is the thing that will actually be running. *Mitigation:* §7.2 `P1` asserts `revision 12` before recording, and if it returns anything else, N22 is re-recorded with the real numbers. **The narration follows the database, never the other way around.**

**R3 — The `balance_owed` lineage pair may not be what the built State Proof renders.** `00_PRODUCT.md` §2.3 shows `balance_owed` v1 → v2 with the value unchanged and the status moving `CONFIRMED` → `DISPUTED`; `12_KERNEL_ALGORITHMS.md` §1.6 step 19 instead creates `bv_isp_balance_v1` fresh as `DISPUTED` and versions `service_active` v1 → v2. Beat 4 carries a pre-approved alternate narration for exactly this. *Open question for the build team:* which belief does the State Proof panel put first, and does it render a version pair for `balance_owed` at all? Answer it at `G-5`, not at the recording session.

**R4 — Resolved; the narration may now name the status.** The hero conflict is `VALUE_CONFLICT`, status **`NEEDS_HUMAN`**, severity `HIGH`, `requires_human = true`. It is on the **`BALANCE`** family — the seeded `balance_owed = $0.0000 CONFIRMED` incumbent against the USD 186.00 counterparty claim — so gate H5 of `specs/12_KERNEL_ALGORITHMS.md` §3.3 fires (`monetary_exposure = 186.00 ≥ human_review_amount_threshold = 100.00`) and short-circuits before the authority-margin test. The disposition is `RETAIN_INCUMBENT_DISPUTED`: value unchanged, `epistemic_status` `CONFIRMED → DISPUTED`, confidence decayed. `AUTO_RESOLVED` in `12_KERNEL_ALGORITHMS.md` §1.6 belongs to a **different** conflict in a different worked dataset — the `SERVICE_STATUS` family conflict produced by entailment EN-1, which has no incumbent balance and no monetary exposure gate. Both can be true; they are not the same row. `00_PRODUCT.md` §2.3 was corrected from the unproducible `status = 'OPEN'` to `'NEEDS_HUMAN'`.

**R5 — Resolved.** `specs/10_DATABASE_DDL.md` §17.6 now seeds `COMMITMENT_DEADLINE` (deposit-overdue) and `RESPONSE_DEADLINE` (damage-followup), matching the frozen four in `CANONICAL_DECISIONS.md` and `specs/16_TRIGGER_DSL.md`. The previous `DEADLINE_ELAPSED` / `NO_RESPONSE_BY` values would have been rejected by the seed's own `ck_prospective_triggers_type` CHECK. `P3` still reads the values out of `prospective_triggers` before recording, because a pre-flight that verifies rather than assumes is the point of `P3`.

**R6 — The no-op trigger's predicate must be genuinely false at demo time, and nothing in the seed spec guarantees it.** `CANONICAL_DECISIONS.md` requires a false-predicate no-op alongside the landlord fire, and `16_TRIGGER_DSL.md` R6 names it a "pre-seeded false-predicate trigger". `sid('trigger','damage-followup')` (`RESPONSE_DEADLINE`, case 5) is the only pre-seeded candidate, and its predicate is false only if the seeded counterparty response is admitted as evidence — case 5's curated items include an outstanding-balance email from Beltline Movers, which should satisfy it. *This is an assumption, and `P4` is the check.* If `P4` returns `PREDICATE_TRUE`, the honest options are: fix the seed so the response is admitted; or drop S19 and lose the argument. Waking a trigger that fires when you promised the audience a no-op is worse than not showing one.

**R7 — Latency between the drop and the reopen is unknown and is the video's largest timing risk.** Beat 3 allocates 24 seconds from `.eml` drop to `revision 13` on screen, covering extraction (Tier E), embedding, ANN retrieval over roughly 16,000 user-scoped rows, optional Tier R resolution, proposal, and a serializable commit. Nothing in the planning pack measures this end to end. *Mitigation:* §9.3's trimmed-wait caption makes any overrun honest rather than fatal, and `05_RELIABILITY_EVAL_DEMO.md` §4 already specifies that artifact completion returns `QUEUED` rather than blocking. *Open question:* what is the p50 and p90 artifact-to-commit latency on the deployed stack? Measure it at `G-13` and record the number here before the recording session.

**R8 — Fitting the counterfactual into five seconds may under-sell the strongest asset in the build.** `00_PRODUCT.md` calls it "the single most persuasive twenty-five seconds in the video" and R2 there warns it is also the easiest to accuse of being rigged. Five seconds shows the outcome and not the fairness argument. *Decision, taken deliberately:* the prospective-memory reveal is the sharper counterfactual (`16_TRIGGER_DSL.md` §13.5 — with memory off, prospective memory does not degrade, it ceases to exist), and it gets 25 seconds. The invoice counterfactual gets the closing frame and the live Q&A. *Residual risk:* moderate. If the beat-6 no-op has to be cut under R6, reconsider giving those seconds back to the counterfactual.

**R9 — Speaking amounts in words while showing figures could read as evasive if they ever disagree.** "Two thousand and twenty dollars" against `$2,020.00` is fine; "eighteen hundred" against `$1,800.00` is fine; a mismatch is a credibility event. *Mitigation:* `P5` asserts the exact ledger before recording, and §12's manual checklist requires a reviewer to confirm every spoken figure against the frame it sits over.

**R10 — The 24 shots assume seven working screens and four Judge Mode panels that do not exist yet.** Every `ED` punch-in assumes the target value is rendered in a stable position; every hover assumes a hover state exists. This script is written against `G-12`'s deliverables, not against a built interface. *Consequence:* the first rehearsal will change shot framing, and possibly shot count. Treat §4 as the plan of record and amend it in place after rehearsal rather than improvising on recording day — an amended shot list is a document, and an improvised one is a rumour.

**R11 — Nothing in this document has been recorded, timed against real footage, or spoken aloud by a human.** The word counts in §5 are arithmetic, not measurement, and the 2.42 words-per-second target is a convention rather than an observation of this particular narrator. *Mitigation:* §5's `ffprobe` check on the dry voiceover, run before any video editing begins, converts the arithmetic into a measurement at the cheapest possible moment.
