# Provenance — Defect Triage Protocol

Status: execution apparatus v1.0
Implementation status: in force. The protocol runs: `ops/defects/DEFECTS.md` is its ledger and `make defects` is a binding gate precondition. See `STATUS.md` at the repository root, which is measured rather than declared
Owns: severity classification, the defect record schema, deduplication, the fix-and-reverify loop, rejected findings, carried debt, and the anti-gaming detectors
Does not own: gate exit assertions (`quality/23_PHASE_GATES.md`), test layer counts (`quality/20_TDD_STRATEGY.md`), or any product or runtime semantic (`00_PRODUCT.md`, `CANONICAL_DECISIONS.md`)

Audience: the hunters running a verification round, the triager who merges their findings, the gate reviewer who signs `G-N`, and the fixer who closes a record.

> `CANONICAL_DECISIONS.md` outranks this document without exception. `quality/23_PHASE_GATES.md` owns the 118 numbered exit assertions — `G0.1`–`G14.7` (108) plus the ten `S1`–`S10` items of the `G-15` pre-submission battery. There is no `G15.x`. **This document adds none of them and does not change that count.** Everything here attaches to the *verification-round protocol* (`23_PHASE_GATES.md` §22) and to the *report template* (§4.1), both of which are reviewer procedure rather than gate arithmetic.

---

## 0. Contents

1. The gap this document closes
2. Roles, and who is allowed to do what
3. The lens roster
4. The severity taxonomy and its decision rule
5. The defect record schema
6. The triage loop
7. The fix-and-reverify rule
8. The false-positive path
9. The carried-debt ledger
10. The anti-gaming rules
11. Files, tools, and make targets this document adds
12. Risks and open questions

---

## 1. The gap this document closes

The design pack is unusually well defended against a build that lies to its authors. It has sabotage (`23_PHASE_GATES.md` §23.5), positive controls (§23.7), fixture guards (§23.4), a mock ban (§23.6), a mutation probe in the review protocol (§22.2 step 4), and seven standing questions that are uncomfortable on purpose (§22.3).

Every one of those mechanisms is bound to a **pre-written assertion**. `G6.3(d)` catches a vacuous retraction filter *because someone already thought of the retraction filter*. `make sabotage` reports on `tests/sabotage_matrix.yaml` and, as §25 risk 10 states plainly, "nothing in `make sabotage` will tell you the entry is missing."

So the pack has no answer for the defect nobody anticipated. There is no severity taxonomy, no defect log format, no rule about who decides when two reviewers disagree, no requirement that a fix be proven by a test that would have caught the thing, no place to put a finding that turned out not to be a defect, and no ledger that survives from one gate to the next. `CANONICAL_DECISIONS.md` names `ops/defects/DEFECTS.md` as committed execution evidence and `implementation/00_IMPLEMENTATION_MAP.md` §5 places the directory in the tree — and no document says what goes in the file.

This document is that apparatus. It is deliberately mechanical, because the fixer and the hunter in this build may be the same model family with the same blind spots and the same preference for making a red thing green.

**One sentence to carry:** *a defect without a reproduction is a rumour, and a fix without a test that would have caught it is a rewrite.*

---

## 2. Roles, and who is allowed to do what

| Role | Does | Must not |
|---|---|---|
| **Hunter** | Runs exactly one lens (§3) against the phase under review. Writes findings to its own inbox file. | Fix anything. Allocate a defect id. Edit `DEFECTS.md`. |
| **Triager** | Merges the inbox into `ops/defects/DEFECTS.md`. Allocates ids. Applies the ordered severity rule. Owns deduplication. | Overrule the ordered rule from taste. Delete a finding. |
| **Gate reviewer** | Signs `G-N` per `23_PHASE_GATES.md` §22.1 — fresh context, not the builder. Decides disputed severity. Runs the debt re-read. | Be the builder of the phase. Close a defect they fixed. |
| **Fixer** | Writes the failing test first, then the fix. Produces the RED transcript and the close-proof output. | Mark a record `CLOSED`. That is the reviewer's or triager's act. |

In a solo agent-driven build these four collapse onto one operator wearing four contexts. The protocol survives that collapse only because **none of the three conditions for closing a defect is a judgement** (§7): a RED transcript, a close-proof exit code, and a battery summary line. Machine output is the reviewer of last resort. This is stated as a limitation, not a claim of rigour — see §12.

---

## 3. The lens roster

A verification round runs 4–6 hunters **in parallel**, one lens each. A lens is a fixed question, not a subject area, so two hunters looking at the same file ask different things and find different defects — which is also why the same defect regularly arrives twice (§6.2).

> **This section is the lens authority.** The `L-*` ids below are canonical because the lens id is a required field on every defect record, and defect-record fields must be stable. `71_AGENT_WORKFLOW.md` §4 describes the same six lenses under prose names (Invariant, Isolation, Concurrency, Honesty, Contract drift, Vacuity) and carries the richer per-lens attack patterns and worked example defects — read §4 there for *how to hunt*, and use the ids here for *what to record*. §4.0 of that document holds the mapping table. The per-phase assignment table below (§3.2) also wins over `71_` §4.7.

| Lens | Name | The question it asks, verbatim | Anchors in `23_PHASE_GATES.md` §23 |
|---|---|---|---|
| `L-INV` | Invariant | Which of the five invariants can I violate from outside the Memory Kernel, and what is the shortest path to doing it? | 23.2, 23.8 |
| `L-BND` | Boundary | Which `tenant_id`, `user_id`, SQL role, Cognito scope, or capability boundary can I cross, and does crossing it return data or an error? | 23.7 |
| `L-VAC` | Vacuity | Which currently-green assertion would stay green if the feature it names were deleted? | 23.5, 23.7, 23.15 |
| `L-DRIFT` | Drift | Where does the code disagree with the specification that owns it — a name, an enum member, a count, a statement order, a threshold, a reason code? | 23.4 |
| `L-RENDER` | Render | Which number, node, badge, or column on screen is not backed by a row read at request time? | 23.3, 23.12 |
| `L-TIME` | Time and concurrency | What breaks at a different clock, a different arrival order, a retry, or a duplicate delivery? | 23.9, 23.10, 23.13 |

### 3.1 Which lenses are mandatory at which gate

`L-VAC` and `L-DRIFT` run at **every** gate; they are the two that need no running system.

| Gate | Mandatory lenses | Count |
|---|---|---|
| `G-0` … `G-3` | `L-VAC`, `L-DRIFT`, `L-INV`, `L-TIME` | 4 |
| `G-4` | `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`, `L-TIME` | 5 |
| `G-5`, `G-6` | `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND` | 4 |
| `G-7`, `G-8` | `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`, `L-TIME` | 5 |
| `G-9`, `G-10` | `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`, `L-TIME` | 5 |
| `G-11` | `L-VAC`, `L-DRIFT`, `L-BND`, `L-RENDER` | 4 |
| `G-12`, `G-13` | all six | 6 |
| `G-14`, `G-15` | all six | 6 |

This mapping mirrors `23_PHASE_GATES.md` §22.4: phases 4, 9, 11, 13 and 15 always get the full round, and the lens counts above are the floor for those rounds, not a substitute for them.

### 3.2 Hunter output format

A hunter never writes to `DEFECTS.md`. It writes one file:

```text
ops/defects/inbox/G-04.L-INV.md
```

with findings keyed `F-<LENS>-<n>` — lens-local, so six hunters writing at once cannot collide. Each finding carries the five fields the triager needs and nothing else:

```markdown
## F-L-INV-3
summary:  outbox_events row is written after COMMIT, not inside the kernel transaction
repro:    pytest services/control_plane/tests/db/test_outbox_atomicity.py -q -s
observed: cases.revision=13, outbox_events for aggregate_version=13: 0 rows
expected: both rows present, or neither
file:     services/control_plane/app/memory_kernel/commit.py
```

Findings with no `repro` line are still submitted — the triager marks them `INCOMPLETE` and returns them (§6.3). A hunter that suppresses its own weak findings is doing the triager's job badly.

---

## 4. The severity taxonomy and its decision rule

Three severities. The rule is an **ordered sequence of yes/no questions answered from the reproduction transcript alone**. First YES wins. A triager who needs to reason about intent, likelihood, or "how bad is it really" has left the rule and is doing something else.

### 4.1 The decision rule

```text
Read the reproduction. Answer in order. Stop at the first YES.

B1. Does the observed output show a violation of one of the FIVE invariants?
      1 evidence append-only   2 beliefs revisable   3 state transactional
      4 actions permissioned   5 grounding (>=1 belief_support edge unless
                                 an explicitly declared DERIVATION)
    -> BLOCKER

B2. Does the observed output show a row, response, retrieval result, embedding,
    or error message crossing a tenant_id or user_id boundary?
    -> BLOCKER

B3. Does the observed output show an external side effect — an SES send, a
    provider call, an EventBridge publish to a real bus — reachable from state
    that is not committed, or from an approval stale in basis_case_revision or
    approval_draft_sha256?
    -> BLOCKER

B4. Does the observed output show a UI element, trace node, Judge Mode panel,
    State Proof field, or rendered count produced from a constant, a template,
    a client-side literal, or an animation rather than from a row read at
    request time?
    -> BLOCKER

M1. Is the observed output wrong — a value, a status, an enum member, an order,
    a count, a code — with none of B1..B4 true?
    -> MAJOR

M2. Does a gate assertion, verification query, or test pass while the defect is
    present?  (Equivalently: would the assertion produce identical output if the
    feature it names were deleted?)
    -> MAJOR

M3. Does the code disagree with the specification that owns the concern —
    a name, an enum member, a count, a statement order, a threshold, a reason
    code, a field name, a file path?
    -> MAJOR

m1. Everything else: cosmetic, or correct-but-unclear.
    -> MINOR
```

### 4.2 The three tie-break rules

These exist to remove the last three judgement calls.

1. **Grounding counts as an invariant.** B1 lists five, not four. `23_PHASE_GATES.md` §1 and §22.3 Q3 both treat "the five (four canon invariants plus grounding)" as one set. A triager who has to decide whether grounding is an invariant is making exactly the call this rule exists to prevent.
2. **Insufficient reproduction goes back, never down.** If the transcript cannot answer B1–B4 yes or no, the finding is `INCOMPLETE` and returns to the hunter. It does **not** become MAJOR because the BLOCKER questions were unanswerable. Downgrading for want of evidence is the single most likely way a real BLOCKER gets carried into a release.
3. **Genuine uncertainty resolves upward.** A finding that plausibly satisfies a BLOCKER question is filed BLOCKER. It may be lowered only by the gate reviewer, only by naming the ordered rule that excludes it, and only with that reason written into the record. The burden of proof is on lowering, always.

### 4.3 Consequence by severity

| Severity | May the gate sign with it open? | May it carry? | Closes by |
|---|---|---|---|
| BLOCKER | **No.** No exceptions, no carry, no "signed with carried debt". | Never | Before `G-N` is signed |
| MAJOR | **No.** | Never | Before `G-N` is signed |
| MINOR | Yes, as `SIGNED WITH CARRIED DEBT` | Yes, with a **named owner** and a **closing phase** | The named phase, or converted to disclosure at `G-14` (§9) |

A gate report with an open BLOCKER or MAJOR against its own phase is `REJECTED`, not `SIGNED WITH CARRIED DEBT`. The `SIGNED WITH CARRIED DEBT` verdict in `23_PHASE_GATES.md` §4.1 is legitimate and expected — for MINOR items only.

### 4.4 BLOCKER — five worked examples

**B-ex-1 — Evidence rewritten in place on re-parse (rule B1, invariant 1).**
The ingestion worker re-parses an artifact whose `content_sha256` already exists after a parser upgrade and issues `UPDATE evidence_items SET normalized_text = $1, extraction_confidence = $2 WHERE id = $3`. The evidence id is stable, so nothing appears to break — the hero flow is green, `cases.revision` moves correctly, and the June invoice still produces its three admitted rows (`DATE_ASSERTION`, `AMOUNT_ASSERTION`, `IDENTIFIER_ASSERTION`). The defect is that an admitted observation changed value without a new row, so every `belief_support` edge that cited it now grounds a belief in text that was never the text it was grounded in. BLOCKER at B1. Corrections arrive as new evidence; there is no second path.

**B-ex-2 — `outbox_events` written after `COMMIT` (rule B1, invariant 3).**
The kernel commits the claim, belief version, grounding edges, conflict, case reopen, revision bump and `state_transitions` row in one serializable transaction, then inserts `outbox_events` on the next statement. The window is small and the demo never hits it, but the state "case is `REOPENED` at revision 13 and no event will ever be published" is reachable, which is precisely the impossible partial aggregate that invariant 3 forbids and precisely what `00_PRODUCT.md` §2.6 says cannot exist. BLOCKER at B1. Fully worked as `D-04-002` in §5.4.

**B-ex-3 — The brute-force retrieval fallback drops the user predicate (rule B2).**
`ops/decisions/VECTOR_INDEX_VARIANT.md` permits a disclosed brute-force partition scan if the vector index probe fails. The fallback query in `provenance_db.repositories.evidence.ann_search()` keeps `retraction_status = 'ACTIVE'` and `embedding_version` but omits `user_id`, because in the indexed path `user_id` was carried by the index prefix rather than by the `WHERE` clause. Over the 18,035-row corpus this returns `iso-a` and `iso-b` rows to the hero user's retrieval. BLOCKER at B2. Note the shape: the boundary was a schema property in one path and nothing at all in the other, which is the general failure mode `00_PRODUCT.md` R8 describes as "a schema property rather than a query-authoring discipline" — true of the index, false of the fallback.

**B-ex-4 — Executor revalidates on one connection and sends on another (rule B3, invariant 4).**
The executor reads `cases.revision` on the app pool, compares it to `basis_case_revision`, then calls SES. A kernel commit landing between the read and the send moves revision 13 → 14 and the message goes anyway. `G9.1` still passes, because its test moves the revision *before* the executor runs. BLOCKER at B3: an external side effect from state the approval no longer describes. The only irreversible operation in the system is exactly the one with a time-of-check-to-time-of-use window.

**B-ex-5 — Counterfactual columns rendered when `parity.all_equal = false` (rule B4).**
`GET /v1/judge-mode/counterfactual/{id}` returns `parity.all_equal = false` because `decode_params_sha256` differs between the MEMORY OFF and MEMORY ON runs. The UI renders both output columns anyway and shows the parity badge in grey. The render gate in `CANONICAL_DECISIONS.md` → *Counterfactual parity canon* is binding on `frontend/30_UX_SPEC.md` §14.4 item 9 and `frontend/32_JUDGE_MODE.md` §7.2 alike: `all_equal = false` means the columns are **not rendered** and a failure banner replaces them. BLOCKER at B4 — the side-by-side is a claim about identity of inputs, and rendering it when the identity does not hold puts an unbacked claim on screen. This is also the single most persuasive asset in the build and therefore the one a hostile reviewer attacks first (`23_PHASE_GATES.md` §22.3 Q4).

### 4.5 MAJOR — five worked examples

**M-ex-1 — An advocate attention class written into `cases.attention_level` (rules M1 and M3).**
The attention node emits `ACTION_REQUIRED` and the persistence path writes it straight into `cases.attention_level`, whose closed set is `NONE | INFO | ATTENTION | URGENT`. `CANONICAL_DECISIONS.md` is explicit: the advocate classes `NONE | FYI | ACTION_SUGGESTED | ACTION_REQUIRED | HUMAN_DECISION` are "mapped deterministically to case attention and action policy, never stored directly in `cases.attention_level`". If the DDL `CHECK` exists the write fails loudly and this is MAJOR at M1; if the `CHECK` is missing, the missing constraint is a *second* MAJOR at M3. No invariant is violated and nothing crosses a boundary, so it is not a BLOCKER — but the dashboard shows the wrong urgency and the deterministic mapping that makes the model's output safe has been bypassed.

**M-ex-2 — `G6.3(c)` passes vacuously because its positive control asserts the wrong thing (rule M2).**
Part (d) is written as `assert len(results) > 0` instead of asserting that `sid('evidence','isp-wrong-term-date')` appears in the top 20 with the retraction predicate removed. Part (c) — "none of the 3 retraction fixtures appear" — then passes whether or not the filter exists, because nothing proves the fixtures were ever retrievable. The mechanical proof of vacuity is that `G6.7`, the sabotage that neuters `retrieval.predicates.retraction_filter`, stays green. MAJOR at M2. Fully worked as `D-06-004` in §5.4.

**M-ex-3 — `agent_runs.mcp_tool_calls` used as a column name (rule M3).**
The repository selects `mcp_tool_calls` from `agent_runs`. `CANONICAL_DECISIONS.md` → *Hero commit canon* is single-valued here: the **column** is `agent_runs.tool_calls` and the **HTTP field** is `mcp_tool_calls[]`. If a view alias hides the difference the hero path is green and `G11.4`'s second query — `SELECT count(*) FROM agent_runs WHERE id='…' AND tool_calls IS NOT NULL` — returns 0, so the assertion that proves the rendered trace is backed by a real row fails at the MCP gate. MAJOR at M3.

**M-ex-4 — Business-day arithmetic counts Saturday (rule M1).**
`TM-04` expects v1 semantics: Monday through Friday, no holiday calendar, and extraction must surface `BUSINESS_DAY_CALENDAR_ASSUMED`. An implementation that counts calendar days, or that counts Saturday, produces a `due_at` one or two days off. Nothing is corrupted and the transaction is sound; the obligation is simply due on the wrong day, and a deadline that is wrong by a day is the whole product being wrong by a day. MAJOR at M1.

**M-ex-5 — A `NO_OP` recorded with a NULL reason code (rules M1 and M2).**
`G10.2` asserts `last_result=DISARMED, last_reason_code=CASE_RESOLVED` for the resolved-case wake. A *different* no-op path — the trigger whose predicate evaluates `UNKNOWN`, `PM-07` — records `NO_OP` with `last_reason_code IS NULL`. §23.8 names this exactly: "an unexplained NOOP is a gate failure". It is MAJOR twice over — wrong behaviour at M1, and at M2 because `G10.2` passes while it is present, having asserted only the one path.

### 4.6 MINOR — four worked examples

Each carries a named owner and a closing phase, per §9. The `<owner>` slot below is filled with a real name when the record is written; a MINOR with no owner is not carriable and blocks the gate exactly like a MAJOR.

**m-ex-1 — Disputed status reads as "nothing happened".** State Proof renders `balance_owed` v1 → v2 as `$0.00 → $0.00` with `CONFIRMED → DISPUTED` in secondary weight. `00_PRODUCT.md` R3 predicted this and prescribed the fix: status is the visual primary for disputed beliefs, with the caption "the amount did not change; our confidence in it did." Cosmetic, and it costs the most intellectually interesting twenty seconds of the video. Owner: frontend. Closes by: phase 12.

**m-ex-2 — Enum leaked into user-facing copy.** The timeline renders `NOOP_ALREADY_APPLIED` verbatim instead of a sentence. Correct-but-unclear. Owner: frontend. Closes by: phase 12.

**m-ex-3 — Gate log filenames use the full SHA.** `tools/gate.sh` writes `logs/<ID>.<full-sha>.log` where `23_PHASE_GATES.md` §2.2 specifies `<ID>.<sha8>.log`. Nothing breaks; `ops/gates/logs/` becomes unreadable at a glance and the report links drift. MINOR at M3 by the letter of the rule — a real spec divergence with no behavioural consequence. Owner: tooling. Closes by: phase 14.

**m-ex-4 — Trace id not selectable in the UI.** `X-Provenance-Trace-Id` is correctly returned on success and error (`G8.7`) but the UI does not display it, so a reviewer cannot copy a trace id to correlate with the Memory Trace panel. Cosmetic. Owner: frontend. Closes by: phase 12.

### 4.7 Four boundary cases the rule decides for you

These are the ones that generate arguments. The rule already answers them.

| Finding | Instinct | Rule says | Why |
|---|---|---|---|
| `corpus_size_user_scoped` rendered as the constant `16035` | MINOR, it is the right number | **BLOCKER (B4)** | `CANONICAL_DECISIONS.md`: any surface rendering it renders the value counted at query time, never a constant. A correct constant is still a number with no backing row, and it stays correct exactly until the corpus changes. |
| A retracted evidence row reaches `agent_evidence_retrieval_v1` and grounds a new belief version | MAJOR, retrieval hygiene | **BLOCKER (B1)** | Grounding is the fifth invariant, and `CANONICAL_DECISIONS.md` states only `ACTIVE` evidence may ground a new belief. §4.2 rule 1 removes the argument. |
| The agent runtime container has `pv_app_reader_writer` in its environment but never uses it | MAJOR, it is unused | **BLOCKER (B3)** | The kernel rule is that no agent gets SQL write access, ever. The capability existing is the defect; "never uses it" is an assertion about today's code path, and `G7.3`'s `grep` exists precisely because that assertion decays. |
| `kernel_decisions.retry_count` is 2 on a single-writer test path | BLOCKER, the kernel is retrying wrongly | **MAJOR (M1)** | §23.9: retries must appear where contention is intended and nowhere else. Wrong behaviour, no invariant violated, no boundary crossed, nothing rendered — and the final state is still correct. It is a MAJOR that must close before the gate signs, which is consequence enough. |

---

## 5. The defect record schema

### 5.1 Files

```text
ops/defects/
├── DEFECTS.md          # the ledger — the name of record, per CANONICAL_DECISIONS
├── REJECTED.md         # findings triaged as not-defects, never deleted (§8)
├── CARRIED_DEBT.md     # the MINOR items accepted at a gate (§9)
└── inbox/
    └── G-<NN>.<LENS>.md   # one per hunter per round; merged, then kept
```

`implementation/00_IMPLEMENTATION_MAP.md` §5 places `ops/defects/` in the tree with `DEFECTS.md` in it. The three siblings above are additions of this document; `DEFECTS.md` remains the file the rest of the pack names.

**All four are committed and gitleaks-scanned**, exactly like the gate logs (`CANONICAL_DECISIONS.md` → *Repository layout canon*). Therefore: **every reproduction is scrubbed before it is committed.** A transcript containing `postgresql://<user>:…@<cluster-host>.cockroachlabs.cloud:26257` is a credential leak in a committed file, and the rotation comes before the defect work. Run reproductions under `asm-exec` so the URL never enters the transcript, and pipe pasted output through `tools/scrub.py` before committing:

```bash
asm-exec --env PV_DB_APP='{{resolve:secretsmanager:provenance/db:SecretString:app_url}}' -- \
  cockroach sql --url "$PV_DB_APP" --format=csv -e "SELECT revision FROM cases WHERE id = :'hero';" \
  | python tools/scrub.py
```

### 5.2 The ledger table

`DEFECTS.md` opens with one table. Every field is required; `—` is permitted only where the column note says so.

```markdown
| ID | Phase | Lens | Sev | Summary | Repro | Owning file | Status | Fix commit | Verifying assertion |
|---|---|---|---|---|---|---|---|---|---|
```

| Field | Rule |
|---|---|
| `ID` | `D-<phase>-<n>`. `<phase>` is the two-digit **discovering** phase, `00`–`15`. `<n>` is a zero-padded three-digit sequence within that phase, allocated by the triager in triage order, never reused. A defect found at `G-6` in phase-4 code is `D-06-nnn`; the origin phase is a field in the detail block. Ids record *when it was found*, which is the number `23_PHASE_GATES.md` §22.3 Q7 asks about. |
| `Phase` | The discovering phase, restated as a number so the table sorts and filters. Must equal the `<phase>` in the id. `tools/defect_lint.py` checks this. |
| `Lens` | The lens code (§3) that produced it. On a merged record, **both** lenses, comma-separated (§6.2) — the second lens is signal about blast radius and dropping it loses that signal. |
| `Sev` | `BLOCKER` / `MAJOR` / `MINOR`, plus the rule id that decided it: `BLOCKER(B1)`. The rule id is not decoration; it is what makes a disputed severity a conversation about a rule rather than about vibes. |
| `Summary` | One line, present tense, states the wrong behaviour and not the guess about the cause. "outbox row written after COMMIT", not "transaction handling needs work". |
| `Repro` | The single command, in backticks, that reproduces. The verbatim observed-versus-expected transcript lives in the detail block, linked by anchor. **A record with no runnable command is not a defect record.** |
| `Owning file` | Exactly one repository-relative path: the file whose change makes the reproduction stop reproducing. If the triager cannot name one, the defect is not yet understood and the status stays `TRIAGED`. Additional touched files go in the detail block. |
| `Status` | Closed set: `OPEN`, `TRIAGED`, `INCOMPLETE`, `IN_FIX`, `AWAITING_REVERIFY`, `CLOSED`, `CARRIED`, `REJECTED`, `DUPLICATE → D-xx-nnn`. |
| `Fix commit` | The full 40-character SHA. `—` while the status is not `CLOSED`. A short SHA is rejected by the lint; gate reports elsewhere in the pack use full SHAs and one convention is cheaper than two. |
| `Verifying assertion` | A thing you can **run**: a gate id (`G4.2`), a pytest node id (`path::test_name`), or both separated by ` + `. Never "manual check", never "reviewed". `—` while not `CLOSED`. |

### 5.3 The detail block

Every record in the table has a detail block below it, anchored by its id. The block is where the reproduction actually lives.

```markdown
### D-<phase>-<n> — <summary>

- **Discovered:** <ISO8601 date> · round `G-<N>` · lens `<LENS>` · hunter `<name/context>`
- **Origin phase:** <phase whose code introduced it, if different from the discovering phase>
- **Severity:** <SEV> — rule <id>. <one sentence naming what the rule matched>
- **Owning file:** `<path>`
- **Also touched:** `<path>`, `<path>`   (or `none`)
- **Status:** <status>
- **Fix commit:** `<40-char sha>`
- **Verifying assertion:** `<gate id and/or pytest node id>`
- **Close-proof:** `<exit code and command, per §7.4>`

**Reproduction**

```bash
$ <exact command as run>
```

Observed:

```text
<verbatim, scrubbed>
```

Expected:

```text
<verbatim, or the exact assertion that should have held>
```

**Notes** — anything the next reader needs and cannot derive: why the obvious fix is wrong,
which sibling assertion was passing while this was present, which gate must be re-run.
```

### 5.4 Two fully worked example rows

> These two records are **illustrative**. No code exists, no test has been run, and neither defect has been observed. They are written against the specified system so that the format is unambiguous rather than described.

```markdown
| ID | Phase | Lens | Sev | Summary | Repro | Owning file | Status | Fix commit | Verifying assertion |
|---|---|---|---|---|---|---|---|---|---|
| [D-04-002](#d-04-002) | 4 | L-INV, L-TIME | BLOCKER(B1) | `outbox_events` row is inserted after `COMMIT`, so a crash leaves the case `REOPENED` at revision 13 with no event | `pytest services/control_plane/tests/db/test_outbox_atomicity.py -q -s` | `services/control_plane/app/memory_kernel/commit.py` | CLOSED | `4f1c9b0e2a7d6c3418ff05b9a2d47e6301c8b5da` | `G4.1 + services/control_plane/tests/db/test_outbox_atomicity.py::test_outbox_row_is_written_inside_the_kernel_transaction` |
| [D-06-004](#d-06-004) | 6 | L-VAC | MAJOR(M2) | `G6.3(c)` passes vacuously: the part-(d) positive control asserts a row count instead of the seeded retracted id | `PV_SABOTAGE=retrieval.predicates.retraction_filter pytest "services/control_plane/tests/db/test_kernel_required.py::test_vector_retrieval_always_scopes_by_user_prefix" -q` | `services/control_plane/tests/db/test_kernel_required.py` | CLOSED | `9d2e77a4c015b8e6f3a0d91c47582bb6e0a3f14c` | `G6.3 + G6.7` |
```

#### D-04-002

- **Discovered:** 2026-08-24 · round `G-4` · lens `L-INV` (independently as `F-L-TIME-2`) · hunter: fresh-context reviewer
- **Origin phase:** 4
- **Severity:** BLOCKER — rule B1. Invariant 3: a reachable state where `cases.status = 'REOPENED'` at `revision = 13` and no `outbox_events` row for `aggregate_version = 13` will ever exist.
- **Owning file:** `services/control_plane/app/memory_kernel/commit.py`
- **Also touched:** `packages/python/provenance_db/hooks.py` (the post-callback fault hook the reproduction needs), `tests/sabotage_matrix.yaml`
- **Status:** CLOSED
- **Fix commit:** `4f1c9b0e2a7d6c3418ff05b9a2d47e6301c8b5da`
- **Verifying assertion:** `G4.1` + `services/control_plane/tests/db/test_outbox_atomicity.py::test_outbox_row_is_written_inside_the_kernel_transaction`
- **Close-proof:** `python -m tools.close_proof D-04-002` → `close-proof D-04-002: test FAILED with fix reverted (exit=1) — PASS`

**Reproduction**

```bash
$ pytest services/control_plane/tests/db/test_outbox_atomicity.py -q -s
```

Observed:

```text
kernel commit returned: status=ACCEPTED_WITH_CONFLICT retry_count=0
fault raised in KernelHooks.after_transaction (simulated worker crash)
-- re-read on a second connection opened after the process fault --
cases.status                              = REOPENED
cases.revision                            = 13
cases.reopened_count                      = 1
state_transitions rows for revision 13    = 1
conflicts rows (VALUE_CONFLICT)           = 1
outbox_events where aggregate_version=13  = 0
FAILED services/control_plane/tests/db/test_outbox_atomicity.py::test_outbox_row_is_written_inside_the_kernel_transaction
1 failed
```

Expected:

```text
Either all of the above are present, or none of them are.
cases.revision                            = 12       (transaction rolled back)
outbox_events where aggregate_version=13  = 0
-- or --
cases.revision                            = 13
outbox_events where aggregate_version=13  = 1        (type=case.reopened.v1, status=PENDING)
```

**Notes** — `G4.1` was green throughout, because it asserts the six row effects on the happy path where no fault is injected; it never asks whether they arrive together. The outbox is the mechanism `00_PRODUCT.md` §3 calls "the thing that makes 'state changed' and 'the world was told' impossible to disagree" — writing it one statement later removes exactly that property while leaving every existing assertion green. The fix moves the insert inside the callback in `10_DATABASE_DDL.md` §13 statement order (last position, after `state_transitions`). Sabotage entry `memory_kernel.commit.enqueue_outbox` added in the same commit per §23 risk 10. Re-ran `make gate-4` and `make gate-3`; `make sabotage` count 18 → 19.

#### D-06-004

- **Discovered:** 2026-08-27 · round `G-6` · lens `L-VAC` · hunter: fresh-context reviewer
- **Origin phase:** 6
- **Severity:** MAJOR — rule M2. `G6.3(c)` and its sabotage `G6.7` both pass while the retraction predicate is neutered, so neither assertion distinguishes a working filter from an absent one.
- **Owning file:** `services/control_plane/tests/db/test_kernel_required.py`
- **Also touched:** none
- **Status:** CLOSED
- **Fix commit:** `9d2e77a4c015b8e6f3a0d91c47582bb6e0a3f14c`
- **Verifying assertion:** `G6.3` + `G6.7`
- **Close-proof:** `python -m tools.close_proof D-06-004` → `close-proof D-06-004: test FAILED with fix reverted (exit=1) — PASS`

**Reproduction**

```bash
$ PV_SABOTAGE=retrieval.predicates.retraction_filter \
    pytest "services/control_plane/tests/db/test_kernel_required.py::test_vector_retrieval_always_scopes_by_user_prefix" -q -s ; echo "exit=$?"
```

Observed:

```text
(a) cross-tenant ids in 200 results          : 0
(b) EXPLAIN names evidence_embedding_ann_idx : yes
(c) retraction fixtures in results           : 0
(d) positive control                         : 47 rows returned
4 passed
exit=0
```

Expected:

```text
(d) positive control : sid('evidence','isp-wrong-term-date') present in top-20
1 failed
exit=1
```

**Notes** — part (d) was written as `assert len(results) > 0`, which is true of every non-empty query and therefore proves nothing about whether the three retraction fixtures are *reachable at all* when the predicate is removed. With (d) weak, (c) is the assertion-on-an-empty-set that §23.7 names: it returns zero because nothing was ever a candidate, not because the filter excluded anything. The fix asserts membership of the specific seeded id from `scripts/seed/ids.py`. This defect makes `G6.7` — the sabotage that is supposed to prove `G6.3(c)` can fail — itself vacuous, which is why the verifying assertion names both. Canon item C, retraction filtering, was formally untested at `G-6` until this closed; recorded in the `G-6` report answer to Q3 as `UNPROVEN` at the time of discovery.

---

## 6. The triage loop

### 6.1 The round

```text
1. Reviewer opens the round.  Lenses assigned per §3.1, 4-6 hunters, in parallel.
   Each hunter is given: this document, its own lens block, the specs that phase
   depends on, ops/defects/REJECTED.md, and the repository at the gate commit.
   NOT the builder's completion report (23_PHASE_GATES.md §22.2 step 2), and NOT
   23_PHASE_GATES.md in any form -- not the phase section, not the battery, not
   the exit assertions.  That is rule R-I1 in 71_AGENT_WORKFLOW.md §6.2 and it is
   the reason the hunter role exists: the assertions are the failures the authors
   already anticipated, and a hunter holding them re-verifies instead of hunting,
   because a checklist terminates and a search does not.  Everything a hunter
   needs from that document is restated in its lens block in different words.

2. Hunters write ops/defects/inbox/G-<NN>.<LENS>.md.  No hunter reads another
   hunter's inbox file during the round.  Independence is the point; two lenses
   converging on one defect is evidence, and it stops being evidence the moment
   one hunter has seen the other's finding.

3. Triager merges: dedup (§6.2) -> completeness (§6.3) -> severity (§4.1)
   -> id allocation -> DEFECTS.md.  Rejections go to REJECTED.md (§8).

4. Fixers work.  Every fix follows §7.  BLOCKER first, then MAJOR, then MINOR.

5. Reviewer re-reads the carried-debt ledger (§9), answers the seven standing
   questions, and writes the verdict.

6. `make defects PHASE=<N>` must print `OPEN BLOCKER: 0  OPEN MAJOR: 0` before
   the verdict may be SIGNED or SIGNED WITH CARRIED DEBT.
```

Step 6 is inserted into `23_PHASE_GATES.md` §22.2 between its step 6 (standing questions) and step 7 (verdict). It is a **reviewer-protocol step, not a numbered exit assertion**; the count of 118 is unchanged.

### 6.2 Deduplication

Two hunters with different lenses routinely find one defect from two directions. `L-INV` finds "the outbox row can be missing"; `L-TIME` finds "a worker crash between two statements leaves an orphaned case revision". Same defect, two vocabularies.

Apply in order:

1. **Same command, same observed line** → duplicate. Mechanical; no judgement.
2. **Different commands.** The triager writes down, in one line each, the change that would make each reproduction stop reproducing. If both name **the same symbol**, they are duplicates. This is the rule that catches the L-INV/L-TIME case above: both fixes are "move the insert inside the callback in `commit.py`".
3. **Same symbol, different fixes** → **not** duplicates. Two defects in one function is an ordinary thing and merging them hides one of them. The test is the fix, not the file.
4. **One reproduction is strictly cheaper** (no cluster, no AWS, fewer steps) → the survivor is the cheaper one, because it is the one that will actually be re-run at every subsequent gate.

On merge:

- The surviving record's `Lens` field lists **both** lenses. A defect visible through two lenses has a larger blast radius than one visible through one, and that is the only place that fact is recorded.
- The surviving record's severity is the **maximum** of the two, not the triager's fresh reading.
- The merged-away record stays in the ledger with `Status: DUPLICATE → D-xx-nnn`. It is never deleted. Deleting it means the same finding is re-hunted, re-triaged and re-argued at the next gate, at full cost, sixteen times.

### 6.3 Incomplete findings

A finding whose transcript cannot answer B1–B4 is marked `INCOMPLETE` and returned to the hunter with the specific missing observation named ("this needs the row count on a second connection, not the return value"). `INCOMPLETE` is a normal state and carries no stigma. What it never does is become MAJOR by default (§4.2 rule 2).

An `INCOMPLETE` finding that the hunter cannot complete within the round is filed at the severity its *worst plausible reading* implies, with `Status: OPEN` and a note stating that the reproduction is partial. That blocks the gate — correctly. An unreproducible claim about invariant 4 is a good reason not to sign.

### 6.4 Disputed severity: who decides

Most severity disputes are not severity disputes. Run this first:

> **Is the disagreement about what the rule says, or about what the transcript shows?**

If it is about the transcript, the answer is §6.3: the reproduction is inadequate, and the fix is a better reproduction, not a negotiation. This resolves the majority of disputes at zero cost.

For the remainder:

- **The gate reviewer for the phase decides.** Singular. The person who signs `G-N` and who, per `23_PHASE_GATES.md` §22.1, is not the builder. Not a vote, not the hunter, not the fixer, not the triager.
- The decision is recorded **in the record** as `Sev: MAJOR(M1) — lowered from BLOCKER(B3) by <reviewer> 2026-08-27: B3 requires an external side effect; the sink call log shows zero provider calls.` One line, naming the rule and the observation that excludes it.
- **Lowering requires naming the ordered rule that excludes the higher severity.** Raising requires nothing (§4.2 rule 3).
- **Time box: five minutes.** A dispute that outlives it resolves to the higher severity and the fixer works on it. Triage is overhead; the moment it costs more than the fix, it is the problem.
- Where reviewer and builder are the same operator in different contexts, the reviewer context must produce the exclusion sentence *before* seeing the fixer's argument, and the sentence must cite the transcript. That is a weak guarantee and §12 says so.

---

## 7. The fix-and-reverify rule

Three conditions. All three, in order, every defect, every severity.

### 7.1 (a) A failing test first, and it must fail for the right reason

Write the test that reproduces the defect **before** touching the code. Run it. Paste the RED transcript into the record's detail block.

`20_TDD_STRATEGY.md` §1.3 is binding here: a RED test must fail for the right reason. A test that errors on collection, raises `ImportError`, or fails on a fixture is not RED — it is broken, and it will go green when the import is fixed rather than when the defect is. The RED transcript must show an **assertion failure naming the defect's symptom**:

```text
E   AssertionError: outbox_events for aggregate_version=13: expected 1, got 0
```

not

```text
E   ImportError: cannot import name 'after_transaction' from 'provenance_db.hooks'
```

The test goes in the layer that owns the behaviour, per `20_TDD_STRATEGY.md` §3.1 — a kernel-atomicity defect is `L2 db`, a predicate-evaluator defect is `L1 unit`, a tenant-crossing defect is `L7 adversarial`. Putting it in the wrong layer is how a test that should run on every commit ends up running nightly.

### 7.2 (b) Make that test pass

Ordinary. The only rule is that the fix changes **non-test** code. A defect closed by a change confined to test files is either a MAJOR-M2 vacuity defect (where the test *is* the owning file, as in `D-06-004`) or an instance of AG-1 (§10.1). The triager decides which by asking whether the record's rule id is `M2`. There is no third case.

### 7.3 (c) Re-run the full phase battery, not just the new test

```bash
make gate-<N>                     # the whole battery, not the one assertion
make gate-<N-1>                   # regression sweep, per §22.2 step 3
make gate-<N-2>                   # if the fix touched provenance_contracts,
                                  # provenance_domain, provenance_db, the Kernel,
                                  # or the schema
make sabotage                     # if the fix touched an invariant-bearing symbol
```

**A fix that breaks a sibling assertion is not a fix.** This is the most-skipped step and the most valuable one: the natural shape of a defect fix is a local change that satisfies a local test, and the natural shape of this system is a set of assertions that constrain each other. Moving the outbox insert inside the kernel transaction changes the statement order that `10_DATABASE_DDL.md` §13 fixes and that `G4.1` asserts; if the fix put it in the wrong position, `G4.1` goes red and the fix was wrong.

Paste the battery **summary lines** into the gate report, not into the defect record — the record carries the RED transcript and the close-proof; the report carries the battery. Do not summarise: `23_PHASE_GATES.md` §3 rule 1 wants the output.

### 7.4 The close condition: a test that would have caught it

> **A defect may be closed only by a test that would have caught it.**

Not "a test that passes now". A test that passes now is compatible with a fix that changed nothing and a test that asserts nothing. The distinguishing question is counterfactual, and it is mechanically answerable:

```bash
python -m tools.close_proof D-04-002
```

`tools/close_proof.py` reads the record, and:

1. checks out the fix commit into a scratch worktree;
2. reverts **only the non-test hunks** of that commit (`git revert -n <sha> -- <non-test paths>`), leaving the new test in place;
3. runs the record's `Verifying assertion` pytest node id;
4. asserts the run exits **non-zero**;
5. prints `close-proof <id>: test FAILED with fix reverted (exit=1) — PASS`, or `— FAIL: the named test passes without the fix; it does not catch this defect`.

The output line goes into the record's `Close-proof` field verbatim. **No close-proof, no `CLOSED`.** The status stays `AWAITING_REVERIFY`, which blocks the gate exactly as `OPEN` does.

Two corollaries:

- **A gate assertion that passed while the defect was present is itself a defect.** `G4.1` was green throughout `D-04-002`; that fact gets its own record at MAJOR(M2), because an assertion that cannot distinguish the broken system from the working one is decoration (`23_PHASE_GATES.md` §2.1). This is cheap to file and it is how the assertion set gets better instead of just larger.
- **Invariant-bearing fixes ship a sabotage entry in the same commit.** §25 risk 10: a code path with no matrix entry is a path whose tests were never checked for vacuousness. The close-proof proves the new test catches *this* defect; the matrix entry is what keeps it catching the next one.

---

## 8. The false-positive path

Hunters will report things that are not defects. Six lenses at sixteen gates, run adversarially with the instruction "the phase is broken and my job is to find out how", will produce findings that are correct-by-design, already-decided, out of scope, or simply wrong. That is the cost of the instruction and it is worth paying.

**Silently discarding them is the worst available option**, for three reasons:

1. **Cost.** The same non-defect is re-found by the same lens at the next gate, and re-triaged, and re-argued. There are sixteen gates. A rejection recorded once is a rejection paid for once.
2. **The rejection is a design decision nobody wrote down.** "Retracted evidence keeps its embedding" looks like a defect to `L-VAC` and is a deliberate decision recorded in `CANONICAL_DECISIONS.md` → *Historical visibility*. Rejecting it without recording the citation means the next hunter has no path from the symptom to the decision, and the decision gets re-litigated by someone with less context.
3. **A wrong rejection is invisible without a record.** The only way to notice that "working as designed" was itself the defect is to have the record in hand when the symptom recurs from a second direction. A discarded rejection cannot be revisited because nobody knows it happened.

### 8.1 The rejected-finding record

`ops/defects/REJECTED.md`:

```markdown
| ID | Phase | Lens | Symptom key | Basis | Citation | Rejected by | Date |
|---|---|---|---|---|---|---|---|
| R-06-001 | 6 | L-VAC | retracted evidence rows still carry embeddings after retraction | WORKING_AS_DESIGNED | `CANONICAL_DECISIONS.md` → Historical visibility; `db/verify.sql` V11 asserts >= 3 | <reviewer> | 2026-08-27 |
| R-04-002 | 4 | L-INV | kernel returns RETRYABLE_CONCURRENCY without enqueuing a retry job | WORKING_AS_DESIGNED | `CANONICAL_DECISIONS.md` → Kernel retry exhaustion: no side effect after the cap; the caller re-drives over 503 + Retry-After | <reviewer> | 2026-08-24 |
```

`Basis` is a **closed set**. An open-ended rejection reason is a place to write "looked fine to me".

| Basis | Means | Must carry |
|---|---|---|
| `SPEC_SAYS_OTHERWISE` | The behaviour is what the owning specification specifies. | The document, section, and the sentence. |
| `WORKING_AS_DESIGNED` | The behaviour is a recorded decision, not an accident. | The decision's location in `CANONICAL_DECISIONS.md` or the owning spec. |
| `NOT_REPRODUCIBLE` | The reproduction did not reproduce. | The failed attempt's verbatim output **and the number of attempts**. |
| `OUT_OF_SCOPE` | The finding is about a declared non-goal. | The bullet in `00_PRODUCT.md` §6 or the cut in `EXECUTION_PLAN.md`. |
| `ALREADY_KNOWN_DEBT` | Duplicate of an accepted MINOR. | The `C-<phase>-<n>` id in `CARRIED_DEBT.md`. |

### 8.2 The symptom key, and how a rejection stops the re-hunt

Every rejection carries a **symptom key**: the finding stated as an observable, in the words a future hunter would use. Hunters `grep REJECTED.md` at the start of every round — it is part of the material handed to them (§6.1 step 1).

A new finding whose symptom key matches an existing rejection must either **cite the rejection and state what changed**, or is dropped without a new record. "What changed" is a real category: a rejection made at `G-6` on the basis of `db/verify.sql` V11 does not survive a phase-11 change to `agent_evidence_retrieval_v1`.

### 8.3 Reopening

A rejection reopens on exactly one thing: **a new reproduction the basis does not cover.** The record moves `REJECTED → OPEN`, the new reproduction is appended below the old one, and the original basis stays visible so the reader can see why it was insufficient. Reopening is normal. A rejection ledger with zero reopens across sixteen gates is more likely to indicate that nobody is re-reading it than that every rejection was right.

**One rejection basis is dangerous enough to name.** `NOT_REPRODUCIBLE` on a concurrency finding is the most likely wrong rejection in this system: `D10` / `G4.7` exists because interleavings are rare, and `G14.4` requires 25 consecutive passes precisely because "a race that fails 4% of the time will fail during the video". A `NOT_REPRODUCIBLE` rejection on anything marked `concurrency` requires the attempt count in the record, and an attempt count below 25 is not a rejection — it is an incomplete reproduction attempt.

---

## 9. The carried-debt ledger

### 9.1 What may carry

**MINOR only.** BLOCKER and MAJOR never carry, at any gate, under any schedule pressure. That is what §4.3 means and it is the whole load-bearing content of the severity taxonomy.

Two things that look like debt and are not:

- **A scheduled deferral is not debt.** `G-2` explicitly defers DDL §19 tests 1, 3, 4, 6, 7, 8, 9, 10, 12 to phases 4, 6, 9 and 10 and requires the gate report to list them with their closing phase. That is a plan, it lives in the gate report, and putting it in the debt ledger would bury the real debt in twelve rows of expected work.
- **A disclosed limitation is not debt.** Fixture mode, the brute-force retrieval fallback, and the EventBridge Scheduler timing gap (§23.13) are disclosures that ship in `README.md`. Debt is something that closes; a disclosure is something that is true.

### 9.2 Format

`ops/defects/CARRIED_DEBT.md`:

```markdown
| ID | Source defect | Description | Owner | Closes by | Accepted at | Re-read log | Consequence if it does not close |
|---|---|---|---|---|---|---|---|
| C-05-001 | D-05-003 | State Proof renders a disputed belief's unchanged value with equal weight to its changed status | <frontend owner> | G-12 | G-5 | G-6 STILL ACCEPTED · G-7 STILL ACCEPTED · G-8 STILL ACCEPTED → ESCALATED | The most interesting twenty seconds of the video reads as "nothing happened" |
```

`Owner` is a **name**, not a role and not "TBD" — an unowned MINOR is not carriable and blocks the gate exactly like a MAJOR. `Closes by` is a **gate id**, not "later" and not "someday".

### 9.3 The re-read rule

> **The carried-debt ledger is re-read, in full, at every subsequent gate.**

`make debt` prints the open ledger. Its output is pasted verbatim into the *Carried debt* section of every `ops/gates/PHASE_NN.md`, and the reviewer marks each open item:

- `STILL ACCEPTED` — with one sentence saying why it is still the right call at this gate;
- `ESCALATED` — the item becomes a MAJOR defect with a new `D-<phase>-<n>` id and blocks this gate;
- `CLOSED` — with the commit.

An empty ledger prints `0 carried items` and **that line must still appear in the report**, because a missing section and a forgotten section are indistinguishable to a reader.

**The three-gate rule:** an item marked `STILL ACCEPTED` at three consecutive gates is **automatically escalated** to MAJOR at the third. Debt that survives three independent reviews is not debt; it is a decision nobody wants to make, and the escalation forces it to be made while there is still schedule left to make it in. `make debt --check-escalation` implements this and exits non-zero when an item crosses the line.

### 9.4 Nothing carries past `G-14`

At `G-14`, the ledger must contain no item whose `Closes by` is `G-15` or earlier and whose status is not `CLOSED`. Any item still open at `G-14` takes one of exactly two paths:

1. **It closes before `G-15` opens.** Normal for a genuine MINOR.
2. **It is converted to a disclosed limitation** — deleted from `CARRIED_DEBT.md` and written, in plain words, into the *What is seeded vs what is computed* section of `README.md`, which `S7` greps for. A disclosure is a truthful statement about the shipped system; carrying it silently is not.

`G-15` has no debt section by construction: the §24 battery is `S1`–`S10` and every item is binary. `make debt --assert-empty` is run once at `G-14` and once as part of the final release assembly, and its failure blocks the release the same way a failing `G14.2` threshold does.

---

## 10. The anti-gaming rules

These matter more here than in an ordinary build, because the hunter and the fixer may be the same model family. They share training, they share blind spots, and — the specific hazard — they share a strong prior that the correct end state is a green test. Every rule below describes a real, cheap, locally-rational move that turns a red thing green without fixing anything. Each has a detector that does not depend on anyone's judgement.

### 10.1 AG-1 — A defect may not be closed by weakening its test

**The failure mode.** The RED test is written honestly and fails. The fix turns out to be hard. The assertion is loosened instead: an exact `reason_code` becomes `is not None`; `assert sid('evidence','isp-wrong-term-date') in top20` becomes `assert len(results) > 0`; `assert retry_count == 0` becomes `assert retry_count >= 0`; a `-s` transcript assertion becomes a smoke check. The test is green, the record closes, and the defect ships. `D-06-004` in §5.4 is this failure mode caught after the fact rather than prevented.

**Detector 1 — the close-proof (§7.4).** `python -m tools.close_proof <id>` reverts the non-test hunks and asserts the named test **fails**. A weakened assertion passes without the fix, and the tool prints `FAIL: the named test passes without the fix`. This is the primary detector and it is not circumventable by a fixer who is also the reviewer, because the exit code is in the record.

**Detector 2 — the assertion-count rule.** `tools/defect_lint.py --commit <sha>` computes, over test files in the fixing commit's diff, `added_assertions - removed_assertions`. A fixing commit with a negative value is rejected unless it carries a `Test-Weakening-Justification:` trailer and a second reviewer's initials. This is deliberately the same shape as the `Fixture-Change-Justification:` trailer that `23_PHASE_GATES.md` §23.4 already requires, so the repository has one habit and not two.

**Detector 3 — no test-only closes outside M2.** A record whose fixing commit touches only test files, and whose severity rule is not `M2`, is rejected by the lint (§7.2).

### 10.2 AG-2 — A sabotage matrix entry may not be deleted to make the matrix pass

**The failure mode.** `make sabotage` reports `sabotages: 18 | detected: 17 | UNDETECTED: 1`. The honest fix is to write the missing assertion — `23_PHASE_GATES.md` §23.5 says so in four words: "Fix the test, not the matrix." The cheap fix is to delete the entry from `tests/sabotage_matrix.yaml`, after which `make sabotage` prints `sabotages: 17 | detected: 17 | UNDETECTED: 0` and `G14.6` **passes**. The gate assertion as written cannot tell the difference, because it asserts a ratio.

**Detector 1 — the matrix is append-only under CI.** `python -m tools.sabotage_guard --base "$(git merge-base HEAD main)"` diffs `tests/sabotage_matrix.yaml` and fails on any removed or renamed `symbol:` key. Legitimate removals exist — the symbol was deleted from the codebase — and the guard handles them mechanically: a removal is accepted only when the named symbol is **also absent from the tree** at that commit, checked by `--assert-symbol-gone <symbol>`, and the commit carries a `Sabotage-Entry-Removal:` trailer naming the reason.

**Detector 2 — assert the count upward, not just the ratio.** `make sabotage` prints the entry count, and each `ops/gates/PHASE_NN.md` records it. The reviewer's check at gate `N` is `count(N) >= count(N-1)`, and a phase that added invariant-bearing modules and zero entries is flagged — which turns `23_PHASE_GATES.md` §25 risk 10 from an instruction to remember into a number to compare. `tools/sabotage_guard --min-count <n>` takes the previous gate's number from the previous report.

**Why both.** Detector 1 catches deletion. Detector 2 catches the subtler version: never adding the entry in the first place, so there is nothing to delete. §25 risk 10 states plainly that `make sabotage` "reports on what is listed"; the count comparison is the only thing that notices what is not.

### 10.3 AG-3 — An eval fixture may not be regenerated from current output to make a scenario pass

**The failure mode.** `CX-01` — the hero contradiction — fails after a prompt change. `pytest --update-fixtures` or `--golden-update` rewrites the expected output and its `expected_output_sha256`. The scenario passes, and now asserts only that the system agrees with itself. The same failure wearing a different hat is editing a number in `evals/thresholds.yaml` downward until `G14.2` clears.

**Detector 1 — `tools/fixture_guard.py`, already specified (`G14.5`).** A commit touching both `evals/datasets/**` or `tests/fixtures/**` and `services|packages|agents/**` fails unless it carries `Fixture-Change-Justification:` and a second reviewer's initials.

**Detector 2 — regeneration is hard-disabled in CI.** `--update-fixtures` and `--golden-update` refuse to run when `CI=true` (§23.4). Every fixture carries `expected_output_sha256` recorded at authoring time, so a regenerated fixture changes a hash that is itself visible in the diff.

**Detector 3 — the defect-record requirement, which is this document's addition.** Any defect record whose `Verifying assertion` names an eval scenario id (`CX-01`, `PM-07`, `SF-04`, …) **must** carry, in its detail block, the output of:

```bash
git diff <fix-sha>^ <fix-sha> -- evals/ tests/fixtures/
```

A zero-line diff is the normal case and costs one line in the record. A non-zero diff on a scenario-verified defect is the exact signature of a regenerated fixture, and it must be accompanied by the pre-fix **system output** — not the pre-fix fixture — so a reader can see that the system's behaviour changed and not merely the expectation. Where the fix legitimately changes a threshold, the record states the **old and new values** explicitly; `evals/thresholds.yaml` is a fixture and is guarded identically.

**The honest gap.** `23_PHASE_GATES.md` §25 risk 7 already concedes it: splitting the fixture change and the code change into two commits defeats detector 1. Detector 3 narrows that gap specifically for defect work, because a defect record with a fixing commit that does not contain the fixture change will show the zero-line diff and then fail its own close-proof — the test passes with the fix reverted, since the fixture was already regenerated. It does not close the gap in general. Nothing in this document does.

### 10.4 AG-4 — Structural note on same-family hunting and fixing

There is no detector for this one; it is the reason the other three are shaped as they are.

- **None of the three close conditions is a judgement.** A RED transcript, a close-proof exit code, and a battery summary line. A model that wants a green outcome cannot produce any of the three by wanting it.
- **Re-hunt a closed BLOCKER with a different lens than the one that found it.** At the next gate, the record's `Lens` field says which lens has already looked. `L-INV` found the outbox defect; `L-TIME` should be the one to confirm it stays fixed, because the two ask different questions of the same code.
- **The gate logs, the defect ledger, and the rejection ledger are committed, timestamped, and public.** `23_PHASE_GATES.md` §25 risk 12 is right that no mechanism makes a team honest. The available defence is that every claim in this protocol is a file in the repository with a SHA next to it.

---

## 11. Files, tools, and make targets this document adds

### 11.1 Files

| Path | Contents |
|---|---|
| `ops/defects/DEFECTS.md` | The ledger table (§5.2) plus one detail block per record (§5.3). Named by `CANONICAL_DECISIONS.md`; this document specifies its contents. |
| `ops/defects/REJECTED.md` | Rejected findings (§8.1). Read by every hunter at the start of every round. |
| `ops/defects/CARRIED_DEBT.md` | Accepted MINOR items with owner, closing gate, and re-read log (§9.2). |
| `ops/defects/inbox/G-<NN>.<LENS>.md` | Raw hunter output (§3.2). Kept after the merge; it is the record of what was looked at. |

All four are scrubbed with `tools/scrub.py` before commit and covered by the existing `gitleaks detect --source ops` scan.

### 11.2 Tools

| Tool | Does |
|---|---|
| `tools/defect_lint.py` | Validates `DEFECTS.md` and `REJECTED.md`: ids unique and well-formed; `Phase` matches the id; `Status` in the closed set; `CLOSED` requires a 40-character fix SHA, a runnable verifying assertion, and a close-proof line; `Sev` carries a rule id; `Repro` is non-empty; every `DUPLICATE → D-xx-nnn` target exists. Also implements the AG-1 assertion-count rule with `--commit`. |
| `tools/close_proof.py` | The §7.4 counterfactual: revert the non-test hunks of the fix, run the named test, require a non-zero exit. |
| `tools/sabotage_guard.py` | The AG-2 detectors: append-only matrix diff, `--assert-symbol-gone`, `--min-count`. |

`tools/defect_lint.py` joins the `guards` job of the commit lane (`20_TDD_STRATEGY.md` §14.2) alongside `test_no_kernel_mocks.py`, `test_no_sql_in_contracts.py` and `scripts/check_vocabulary.py`. It is fast — it parses two markdown files — and it fails a push that leaves the ledger malformed.

### 11.3 Make targets

```make
defects:            ## Print the ledger, grouped by status. PHASE=<N> filters to one phase.
	python -m tools.defect_lint --report $(if $(PHASE),--phase $(PHASE),)

debt:               ## Print the open carried-debt ledger for the gate report.
	python -m tools.defect_lint --debt --check-escalation

close-proof:        ## make close-proof ID=D-04-002
	python -m tools.close_proof $(ID)

triage-round:       ## make triage-round PHASE=4 — merge inbox files, report collisions.
	python -m tools.defect_lint --merge-inbox --phase $(PHASE)
```

`make defects PHASE=<N>` prints, as its last line, the string the reviewer needs before writing a verdict:

```text
OPEN BLOCKER: 0  OPEN MAJOR: 0  OPEN MINOR: 2  CARRIED: 1  REJECTED: 4
```

---

## 12. Risks and open questions

**R1 — In a solo build, all four roles are one operator, and the fresh-context guarantee is weak.** `23_PHASE_GATES.md` §25 risk 4 already concedes that an agent reviewing its own work re-derives the same blind spots from the same specs. This document inherits that risk fully and adds a role structure that a single operator can satisfy only by convention. *Mitigation:* every close condition is machine output (§7.4), and the disputed-severity procedure requires the exclusion sentence to be written before the fixer's argument is seen. *Residual risk:* high and unavoidable in a solo build. The honest framing for a reader is that this protocol makes dishonesty require a deliberate act rather than a convenient one, which is the same thing `23_PHASE_GATES.md` §25 risk 7 says about the fixture guard.

**R2 — Triage overhead competes directly with build time, and build time is the binding constraint.** Six lenses at sixteen gates is up to 96 hunter passes. `23_PHASE_GATES.md` §25 risk 3 already notes that 16 full verification rounds is a working day of pure review and that verification is the first thing compressed when the build runs late. Adding a triage step makes that worse. *Mitigation:* the five-minute dispute box, the mandatory-lens table that drops to four lenses for phases 0–3 and 5–6, and `make` targets so a round costs a command. *Decision:* if the round must be cut, cut lenses — never cut the severity rule or the close-proof, because those are what make the remaining findings trustworthy.

**R3 — The `L-VAC` lens is the highest-value and the hardest to run.** Asking "which green assertion would stay green if the feature were deleted" is the question the whole pack is organised around, and answering it honestly requires actually deleting the feature. Where a sabotage matrix entry exists, `PV_SABOTAGE` makes it a one-liner; where one does not, the hunter must write the neutering by hand, and it will sometimes be skipped. *Mitigation:* AG-2 detector 2 makes a phase with zero new matrix entries visible. *Residual risk:* real. The lens degrades to reading tests rather than breaking code, which finds less.

**R4 — `tools/close_proof.py` assumes a clean separation between test and non-test hunks.** A fix that changes a shipped test seam — `provenance_db.hooks`, which `20_TDD_STRATEGY.md` §7.3 and §R2 describe as shipped code existing solely so tests can force an interleaving — has hunks that are neither cleanly test nor cleanly production. `D-04-002` in §5.4 is exactly this case: its fix touches `hooks.py`. *Decision:* `hooks.py` is classified as **non-test** for close-proof purposes, so reverting it is part of reverting the fix. This is the conservative direction — it makes the close-proof harder to pass, not easier. The classification list lives in `tools/close_proof.py` and any addition to it is an AG-1 surface that needs the same trailer treatment.

**R5 — The severity rule is ordered and mechanical, and mechanical rules produce occasional wrong answers with total confidence.** `m-ex-3` — gate log filenames using a full SHA instead of `sha8` — is a genuine spec divergence and therefore MAJOR by the letter of rule M3, which would block a gate over a filename. It is filed MINOR in §4.6 because it is cosmetic, and that is an inconsistency in this document, not a subtlety. *Decision:* M3 applies to divergences in **names, enums, counts, orders, thresholds, reason codes, and field names that a contract, query, or assertion depends on**. A filename in a log directory depends on nothing and falls to `m1`. This carve-out is narrow, it is written down, and it is the one place where the rule needs a reader rather than a matcher. If it is abused, the abuse will look like MAJORs being re-labelled cosmetic, and the detector is that a MINOR whose owning file is under `services/`, `packages/`, or `agents/` is almost certainly mis-severitied.

**R6 — Assumption: the design pack's test paths are the ones the build will use, and two documents disagree.** `20_TDD_STRATEGY.md` §3.3 places the twelve required database tests at `services/control_plane/tests/db/test_kernel_required.py`, consistent with the `CANONICAL_DECISIONS.md` test-placement rule that per-package tests live beside their package. `23_PHASE_GATES.md` §8–§17 refers to them as `tests/db/test_02_grounding_required.py`, `tests/db/test_12_vector_scope_and_retraction.py`, and so on, at the repository root. **This document uses the `20_TDD_STRATEGY.md` paths**, because the placement rule is canon and a test importing only from `control_plane` belongs beside it. That choice makes the `Repro` and `Verifying assertion` cells in §5.4 concrete, and it means those cells are wrong if the reconciliation lands the other way. *This is itself a MAJOR-M3 documentation defect in the pack* — one file path, two spellings, in two documents that both gate the same work — and it should be filed as `D-00-001` in the first round rather than resolved by whoever writes the test first. Recording it here is the first use of the protocol this document defines.

**R7 — Open question: whether the `INCOMPLETE` state is stable under time pressure.** §6.3 says an incomplete finding about a BLOCKER-class question blocks the gate. That is correct and it is also the rule most likely to be quietly skipped at 2 a.m. on the day before a release, because the cheapest way to unblock a gate is to decide the reproduction was never going to reproduce. There is no detector for that; `NOT_REPRODUCIBLE` in `REJECTED.md` requires an attempt count, which raises the cost of the shortcut but does not remove it. Flagged rather than solved.

**R8 — Open question: what happens when a defect's owning file is a document rather than code.** `R6` above is a real defect whose owning file is `docs/quality/23_PHASE_GATES.md`. The close-proof is undefined for a documentation fix — there is no test to revert the fix against. *Provisional decision:* documentation defects close on the **change-control rule in `README.md`** instead of a close-proof: the decision register, the owning specification, the dependent contracts and examples, and the migration note updated in one change. The record's `Verifying assertion` field carries the `rg`/`grep` command that returns zero hits for the old spelling. That is weaker than a close-proof and it is the strongest thing available for prose.
