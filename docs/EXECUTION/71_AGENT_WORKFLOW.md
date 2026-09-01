# Provenance — Agent-Driven Execution Model

Status: execution process baseline v1.0
Implementation status: substantial. Most of what this document describes was built. See `STATUS.md` at the repository root, which is measured rather than declared and names what is still partial or absent.
Authority: subordinate. This document owns **process**. It may not change a name, a count, an enum value, a schema, an endpoint, or an acceptance criterion. Where it appears to, `CANONICAL_DECISIONS.md`, `00_PRODUCT.md`, and the owning numbered specification win, in that order, per `README.md` → *Authority rules*.

Audience: the human running the build, and every agent the human dispatches.

---

## 0. Contents

1. Why this document exists
2. The five roles
3. The per-phase loop
4. The six Bug Hunter lenses
5. Dispatch briefs
6. Context isolation rules
7. Parallelism and file-conflict safety
8. When to stop using agents
9. Artifacts, ledgers, and where evidence lands
10. Honest limitations
11. Risks and open questions

---

## 1. Why this document exists

### 1.1 The gap in the design pack

The pack is unusually well defended against one specific failure: **an assertion that passes vacuously.** It carries four separate mechanisms for that, and they are good:

| Apparatus | Where it lives | Size | What it proves |
|---|---|---|---|
| Sabotage matrix | `tests/sabotage_matrix.yaml`, run by `make sabotage`, gated at `G14.6` | 18 entries | For each listed symbol, neutering it turns a named test selection red. |
| Per-phase mutation probe | `quality/23_PHASE_GATES.md` §22.2 step 4 | 1 per gate | The single most load-bearing exit assertion of the phase notices when the thing it claims to protect is broken. |
| Injection corpus | `evals/adversarial/injection_corpus.jsonl`, gated at `G14.3` | 24 cases | Hostile artifact content produces zero capability escalations, zero canonical writes, zero action intents, and evidence is still preserved 24/24. |
| Anti-self-deception checklist | `quality/23_PHASE_GATES.md` §23 | 15 items | Each named self-deception has a mechanical detector rather than a resolution to be careful. |

Every one of those is **pre-scripted**. Each entry was written by the same authors who wrote the assertions it validates, before any code existed. That is exactly what makes it cheap and repeatable, and it is also its ceiling. The pack states the ceiling itself, in `quality/23_PHASE_GATES.md` §25 risk 10:

> `tests/sabotage_matrix.yaml` is hand-maintained. A code path with no entry is a code path whose tests were never checked for vacuousness, and nothing in `make sabotage` will tell you the entry is missing — it reports on what is listed.

So the existing machinery answers one question well: *are the assertions we wrote non-vacuous?* It cannot answer the other one: *what is broken that we never thought to assert?* Nothing in 34 documents, 118 numbered exit assertions (108 across `G0`–`G14`, plus the 10 `S1`–`S10` items of the pre-submission gate), 626 tests, or 51 eval scenarios hunts for a defect the gates did not anticipate. The gate battery is a closed set. The defect space is not.

### 1.2 The one process rule the pack already fixed

`quality/23_PHASE_GATES.md` §22.1 fixes the reviewer's context, and this document treats it as binding rather than as advice:

> The reviewer must not be the builder. In an agent-driven build this means a **fresh context** — a reviewer agent that has not seen the implementation conversation, given only: this document, the phase's section, the specs it depends on, and the repository. A reviewer who has been in the room while the code was written has already absorbed the builder's model of why it works, which is the exact thing under test.

And §25 risk 4 concedes the weakness of that guarantee honestly:

> The reviewer may be the same agent that built the phase. Fresh context is a weaker guarantee than a different person. An agent reviewing its own work re-derives the same blind spots from the same specs.

Everything below is designed around those two sentences. Section 6 generalises the isolation rule from the Verifier to every role. Section 10 refuses to overclaim what that buys.

### 1.3 What this document adds

Three things, and nothing else:

1. **Five roles with non-overlapping context.** Each role's blind spot is covered by a role that was denied the context which creates it.
2. **An open-world adversarial step** — the Bug Hunt — inserted between "the scripted checks pass" and "the gate is verified". It is the only step in the build whose success criterion is *finding something*.
3. **Concurrency discipline for the humans and agents**, so that fanning out does not cost more in merge damage than it saves in wall time.

It does not add a gate, relax a gate, or change what `SIGNED` means.

---

## 2. The five roles

| Role | Count per phase | Sees the build conversation? | Sees the gate battery? | Writes production code? | Output artifact |
|---|---|---|---|---|---|
| **Builder** | N (one per parallel-safe task) | its own only | its phase's section only | yes | branch + `ops/agents/tasks/T<N>.<k>.md` |
| **Bug Hunter** | 4–6 | no | **no** | no | `ops/agents/hunts/H<N>.<lens>.md` |
| **Gate Verifier** | 1 | **no** | yes, in full | no | `ops/gates/PHASE_<NN>.md` |
| **Fixer** | 1 per confirmed defect | no | no | yes, minimally | branch + defect record update |
| **Integrator** | 1 | yes, all of it | yes | only shared-file reconciliation | `ops/agents/PHASE_<NN>_DISPATCH.md` |

### 2.1 Builder

**Receives.**

- `CANONICAL_DECISIONS.md` and `00_PRODUCT.md` §0 (canon and the four invariants). Always. Every role gets these.
- The phase's **owning specs only**. Phase 4 gets `specs/12_KERNEL_ALGORITHMS.md`, `implementation/02_DATA_MEMORY_TRANSACTIONS.md`, and `specs/10_DATABASE_DDL.md` §13. It does not get `specs/14_PROMPTS.md` or `frontend/30_UX_SPEC.md`.
- `implementation/00_IMPLEMENTATION_MAP.md` §5 (repository layout) and §12 (implementation rules).
- `quality/20_TDD_STRATEGY.md` §1 (RED-GREEN-REFACTOR as practised here) and §13.3 (the Memory Kernel is never mocked).
- **One task**: an id, a one-sentence goal, an explicit owned-path list, an explicit forbidden-path list, and the acceptance criteria for that task alone.
- The phase's section of `quality/23_PHASE_GATES.md` — the exit assertions its work must eventually satisfy. This is deliberate: a Builder who cannot see the target builds to a different one.

**Must not receive.**

- Another Builder's task brief or branch. Two Builders who can see each other's plans converge on one shared helper module and then both edit it.
- The Bug Hunter lens list. A Builder who knows the Concurrency hunter is coming writes code that reads as concurrency-safe. The hunt is supposed to test the code, not the code's self-presentation.
- Any other phase's specs. Scope leakage is the cheapest way to blow a phase budget.

**Job.** Test-first, strictly:

1. **RED.** Write the failing test first, and confirm it fails **for the right reason**. `quality/20_TDD_STRATEGY.md` §1.3 is explicit that a test failing on `ImportError` is not RED; the stub must exist and return a wrong-but-typed value. Paste the failure output into the task report.
2. **GREEN.** Smallest change that passes. No speculative generality, no adjacent refactor.
3. **REFACTOR.** Only with the test green, and re-run after.
4. If the task adds an invariant-bearing function, add its `tests/sabotage_matrix.yaml` entry **in the same commit**. `quality/23_PHASE_GATES.md` §25 risk 10 says a phase whose new modules added zero matrix entries is suspicious on its face; that judgement is enforced here as a task-level rule rather than left to the gate.

**Must not.** Run `make gate-<N>` and report the result as a gate outcome. A Builder may run any test for its own feedback. Only the Gate Verifier's battery run counts, and only from a clean clone.

**Output artifact.** A branch `phase<N>/<task-id>` plus `ops/agents/tasks/T<N>.<k>.md` containing: files created and modified, the RED output, the GREEN output, the sabotage entries added, every acceptance criterion with its evidence, and an explicit list of what was assumed rather than read.

**Parallelism.** N per phase, one per parallel-safe task, where "parallel-safe" is defined by disjoint owned paths (§7). N is 1 for phases 4 (kernel pipeline) and 13 (CDK), and 3–6 elsewhere.

### 2.2 Bug Hunter

**Receives.**

- The built code at a named commit, in a clean clone or a dedicated worktree.
- **Exactly one lens** (§4), stated in full: what it hunts, its attack patterns, and its example defect classes.
- The specs that define correct behaviour for the surface under attack — because a hunter that cannot tell correct from incorrect finds only style.
- `CANONICAL_DECISIONS.md`, `00_PRODUCT.md` §0, `implementation/06_CODING_AGENT_HANDOFF.md` §19 (the pull-request guardrails).
- A working database and the ability to run arbitrary read queries and to run the test suite.

**Must not receive.**

- **The gate assertions. This is the rule the role exists for.** A hunter given `G4.1`–`G4.9` will re-verify `G4.1`–`G4.9`, because a checklist is easier than a search, and it will report "all nine pass" as though that were a hunt. The assertions are the anticipated failures; the hunter's entire job is the unanticipated ones. Concretely: the Hunter never receives `quality/23_PHASE_GATES.md` in any form, and the lens brief restates any principle it needs (positive controls, sabotage) in its own words so the technique transfers without the answer key.
- The build conversation, the Builder's task report, or the other lenses' reports. Five hunters that read each other's findings produce one finding five times.
- The Verifier's battery output.

**Job.** Find defects the gates did not anticipate, and **prove each one with a reproduction**. A finding without a reproduction is a hypothesis and is filed as such. The hunter is graded on confirmed defects, not on report length, and is explicitly permitted to return "no confirmed defect under this lens, here is the search I ran" — that is a legitimate and useful result. It is not permitted to return a summary of what the code does.

**Must not.** Fix anything. A hunter that fixes a defect has destroyed the reproduction and has entered the code as an unreviewed Builder. Hunters may write only to their own report file and to a throwaway worktree.

**Output artifact.** `ops/agents/hunts/H<N>.<lens>.md`, one section per finding:

```markdown
## F-<N>.<lens>.<k> — <one-line claim>
Severity: BLOCKS_GATE | CORRECTNESS | ROBUSTNESS | HYGIENE
Invariant or contract at risk: <invariant 1-4 | grounding | a named spec clause>
Reproduction: <exact commands, exact SQL, exact fixture; must run from a clean clone>
Observed: <verbatim output>
Expected, and the document that says so: <clause reference>
Blast radius if shipped: <what a reviewer or a user sees>
```

**Parallelism.** 4–6 per phase, all concurrent, all read-only. Which lenses are mandatory for which phase is fixed in §4.7.

### 2.3 Gate Verifier

**Receives.** Exactly what `quality/23_PHASE_GATES.md` §22.1 permits, and nothing more:

- `quality/23_PHASE_GATES.md` in full.
- The phase's section.
- The specs that phase depends on.
- The repository, at the gate commit, via `git clone` into an empty directory. Never a working tree.

**Must not receive.**

- **The build conversation.** Not a summary of it, not "here is what we did", not a rationale. This is the §22.1 rule, and its whole content is that the builder's model of why the code works is the thing under test.
- The Builder's completion report — **on the first pass**. §22.2 step 2 is explicit: form an independent result first, then diff it against the claim. The dispatch is therefore two messages (§5.3): message 1 is the battery run, message 2 hands over the Builder's report and the Hunter findings for reconciliation. A Verifier that reads the claim first is anchored to it.
- Any authority to relax an assertion. `NOT RUN` with a reason is available; lowering a threshold is not.

**Job.** §22.2 in order: clean clone → bootstrap and battery → regression sweep of `gate-<N-1>` and, if shared code was touched, `gate-<N-2>` → mutation probe → guardrail diff read against `implementation/06_CODING_AGENT_HANDOFF.md` §19 → the seven standing questions of §22.3 answered in writing → verdict. Raw output is pasted, never summarised.

**Output artifact.** `ops/gates/PHASE_<NN>.md` in the §4.1 report template, plus the scrubbed logs under `ops/gates/logs/`. Verdict is `SIGNED`, `SIGNED WITH CARRIED DEBT` (debt enumerated, each item naming the phase that closes it), or `REJECTED` (naming the specific assertion that failed).

**Parallelism.** Exactly 1, and strictly last. Parallel verification is not verification; it is two partial reviews that each assume the other looked.

### 2.4 Fixer

**Receives.**

- **One** confirmed defect record and its reproduction.
- The owning spec clause the defect violates.
- The file or files named in the defect.
- Nothing else. Not the other defects, not the phase's full spec set, not the gate battery.

**Must not receive.**

- The gate assertion that the fix will eventually be checked by. A Fixer holding the assertion fixes the assertion — it adds the narrow special case that makes the named query return zero rows, and the defect survives one input away.
- The other open defects. Batched context produces opportunistic refactoring, and an opportunistic refactor inside a fix is the change nobody reviewed.

**Job.**

1. Reproduce the defect first and paste the failure. A Fixer that cannot reproduce returns `NOT_REPRODUCED` and the defect goes back to the Hunter. This is common and is not a failure of either role.
2. Write a **regression test that fails before the fix and passes after**, at the correct layer (`quality/20_TDD_STRATEGY.md` §3.1: kernel and state behaviour is L2, not L1 with a fake connection).
3. Make the minimal change.
4. Add the `tests/sabotage_matrix.yaml` entry for the symbol the fix now depends on.
5. Re-run the reproduction and paste the result.

**Output artifact.** A branch `fix/<defect-id>`, the regression test, and the defect record in `ops/defects/DEFECTS.md` moved to `FIXED` with both outputs attached.

**Parallelism.** One per confirmed defect, concurrent, **provided the defects touch disjoint files**. Two defects in one file are serialised, in severity order, by the Integrator. This is the single most common way an agent-driven build corrupts itself: three Fixers, one file, three "successful" merges, one file that satisfies none of the three tests.

### 2.5 Integrator

This is the human, or the human's primary session. It is the only role that holds the whole picture, and therefore the only role with no adversary. §10 says so plainly.

**Receives.** Everything. All conversations, all reports, all specs, the ledgers, the cluster.

**Job, in five verbs.**

- **Decompose.** Turn a phase into tasks with disjoint owned paths and explicit acceptance criteria. Write the forbidden-path list, not just the owned-path list — the negative half is what prevents the collision.
- **Dispatch.** Fill in the briefs of §5 and send them. Record every dispatch in `ops/agents/PHASE_<NN>_DISPATCH.md` before the agent returns, so a lost session is still auditable.
- **Reconcile.** Merge in the order of §7.4. Own every shared file: `Makefile`, `pyproject.toml`, `.importlinter`, `alembic` revision pointers, `tests/sabotage_matrix.yaml`, `db/expected_tables.txt`, `db/seeds/MANIFEST.json`, `provenance_domain/INVARIANTS.md`, and the CDK stack graph. No Builder edits these.
- **Decide.** Triage every Hunter finding to `CONFIRMED`, `NOT_A_DEFECT` (with the clause that makes it correct), or `ACCEPTED_DEBT` (with the phase that closes it). Decide what is cut. Decide when a task stops being a fan-out and becomes a single careful pass (§8).
- **Sign.** The Verifier writes the technical verdict; the Integrator countersigns and personally owns every item of carried debt. Carried debt that is not written down is the failure mode the whole apparatus exists to prevent, and it is an Integrator failure, not a Builder one.

**Must not.** Write production code and then verify it in the same session. If the Integrator implements a task, that task gets a Hunter and a Verifier like any other, and the Integrator's implementation conversation is denied to both.

**Parallelism.** 1. Always. There is no second Integrator, and a build with two is a build with two merge orders.

---

## 3. The per-phase loop

```text
                 ┌─────────────────────────────────────────────────┐
                 │  1  DECOMPOSE       Integrator                   │
                 │     phase -> N tasks, disjoint owned paths       │
                 └───────────────────────┬─────────────────────────┘
                                         │
                 ┌───────────────────────▼─────────────────────────┐
                 │  2  BUILD           Builder x N   (parallel)     │
                 │     RED -> GREEN -> REFACTOR, one task each      │
                 └───────────────────────┬─────────────────────────┘
                                         │  merge, §7.4 order
                 ┌───────────────────────▼─────────────────────────┐
                 │  3  SCRIPTED VERIFY  Integrator                  │
                 │     make lint test; make sabotage; make db-verify│
                 │     the PRE-SCRIPTED apparatus, on merged tip    │
                 └───────────────────────┬─────────────────────────┘
                                         │  green, and only then
                 ┌───────────────────────▼─────────────────────────┐
                 │  4  BUG HUNT        Hunter x 4-6  (parallel)     │
                 │     one lens each, no gate assertions            │
                 └───────────────────────┬─────────────────────────┘
                                         │
                 ┌───────────────────────▼─────────────────────────┐
                 │  5  TRIAGE          Integrator                   │
                 │     CONFIRMED | NOT_A_DEFECT | ACCEPTED_DEBT     │
                 └───────────────────────┬─────────────────────────┘
                                         │
                 ┌───────────────────────▼─────────────────────────┐
                 │  6  FIX             Fixer x M     (parallel)     │
                 │     one defect each, regression test first       │
                 └───────────────────────┬─────────────────────────┘
                                         │  re-run step 3
                 ┌───────────────────────▼─────────────────────────┐
                 │  7  GATE            Verifier x 1  (fresh ctx)    │
                 │     §22.2 steps 1-6, raw output, Q1-Q7           │
                 └───────────────────────┬─────────────────────────┘
                                         │
                 ┌───────────────────────▼─────────────────────────┐
                 │  8  SIGN or REJECT  Verifier verdict,            │
                 │     Integrator countersign + carried debt        │
                 └─────────────────────────────────────────────────┘
                    REJECTED -> back to step 5 with the failed
                    assertion as a CONFIRMED defect. Never to step 2.
```

### 3.1 Step by step

**1. Decompose.** Output is a task table: id, goal, owned paths, forbidden paths, acceptance criteria, dependency order. If two tasks cannot be given disjoint owned paths, they are one task. Do not decompose to increase parallelism; decompose to reduce the size of the thing one agent must hold correct at once.

**2. Build.** Builders run concurrently. Each stops at its own acceptance criteria and does not run the phase battery. Merge in §7.4 order.

**3. Scripted verify.** On the merged tip, run everything the pack already scripted: `make lint`, `make test`, `make sabotage`, `make db-verify`, `make seed-perturb` where the phase is one of the four §23.1 names it (4, 6, 10, 12). This is cheap, it is deterministic, and it is the gate on whether step 4 is worth spending. **Do not run the Bug Hunt on code that does not pass its own scripted checks.** Hunters will spend their whole budget rediscovering the red test.

**4. BUG HUNT.** Explained below.

**5. Triage.** The Integrator reads every finding and assigns one of three dispositions. `NOT_A_DEFECT` requires citing the clause that makes the behaviour correct — "I do not think that is a problem" is not a disposition. `ACCEPTED_DEBT` requires the phase that closes it, and it goes into the gate report's carried-debt section whether or not the Verifier finds it independently.

**6. Fix.** One Fixer per confirmed defect, parallel across disjoint files. Then re-run step 3 in full, not just the new regression tests: the second-most common self-inflicted wound in this loop is a fix that turns an earlier phase's gate red and nobody re-runs it until the Verifier's regression sweep, by which point three more merges have landed on top.

**7. Gate.** The Verifier, fresh context, clean clone. Two-message protocol (§5.3).

**8. Sign or reject.** On `REJECTED`, the failed assertion becomes a `CONFIRMED` defect and re-enters at step 5. It does not re-enter at step 2 — re-opening the build step invites a rewrite where a fix is called for, and a rewrite discards every hunt already spent on the code.

### 3.2 Why steps 4–6 exist

The honest answer is one sentence: **because everything else in this build only checks the failures its authors already imagined.**

Trace what each pre-scripted mechanism actually proves:

- `make sabotage` neuters 18 named symbols and confirms 18 named test selections go red. It proves those 18 tests are not decoration. It says nothing about the nineteenth symbol, and by its own construction *it cannot*: it reports on what is listed.
- The §22.2 step 4 mutation probe breaks the one thing the phase's most important assertion protects. One thing. Per phase.
- The 24-case injection corpus proves 24 specific hostile inputs cause zero capability escalations. The twenty-fifth input is not covered, and the corpus was written from the same threat model that produced the containment design — the Interpreter has no send tool and no write privilege — so it tests the containment the authors already built.
- The 15-item anti-self-deception checklist is a list of 15 named self-deceptions. It is an unusually good list. It is still a list.

Every one of them is a **closed set defined before the code existed**. Their union is the answer key. A build that only runs the answer key learns exactly one thing: whether the answers are right. It never learns whether the questions were the right questions.

The Bug Hunt is the open half. Its success criterion is inverted from every other step: a hunt that finds nothing has either proven something valuable (rare) or wasted its budget (common), and the only way to tell them apart is the reproduction discipline in §2.2 — a hunter that documents the search it ran has produced evidence; a hunter that says "looks correct" has produced §2.1-vocabulary from `quality/23_PHASE_GATES.md` §2.1, which is rejected as a gate item and is rejected here too.

Triage exists because hunters produce false positives at a rate that will surprise anyone who has not run them. Six lenses against a phase's worth of new code routinely yields fifteen findings of which four are real. Without a triage step with a citation requirement, the build spends its remaining time fixing correct code.

Fix exists as a separate role from Build for one reason: **the defect's reproduction is the specification of the fix**, and it is a much sharper specification than the original task brief. A Builder handed "also fix these four things" folds them into a broad edit; a Fixer handed one reproduction produces a diff whose correctness is checkable in a minute.

---

## 4. The six Bug Hunter lenses

Each lens is a stance, not a checklist. The example defects are real shapes this system can take — they are written from the frozen specs, and each one names the clause it would violate. None of them is a claim that the defect exists; no code exists.

### 4.0 Lens identity — reconciled with `72_DEFECT_PROTOCOL.md`

This document and `72_DEFECT_PROTOCOL.md` were authored in parallel and initially named the same six lenses differently. **`72_DEFECT_PROTOCOL.md` §3 is the lens authority**, because the lens id is a required field on every defect record and ids must be stable. The six are identical in substance; use the `L-*` ids everywhere a defect is recorded.

| `L-*` id (canonical) | Name in §3 of `72_` | Section below | Scope |
|---|---|---|---|
| `L-INV` | Invariant | §4.1 Invariant | Which invariant can be violated from outside the Kernel, by the shortest path |
| `L-BND` | Boundary | §4.2 Isolation | Any `tenant_id`, `user_id`, SQL role, Cognito scope, or capability boundary that can be crossed |
| `L-TIME` | Time and concurrency | §4.3 Concurrency | A different clock, arrival order, retry, or duplicate delivery |
| `L-RENDER` | Render | §4.4 Honesty | Any number, node, badge or column not backed by a row read at request time |
| `L-DRIFT` | Drift | §4.5 Contract drift | Code disagreeing with the spec that owns it — name, enum member, count, statement order, threshold, reason code |
| `L-VAC` | Vacuity | §4.6 Vacuity | Any green assertion that would stay green if the feature it names were deleted |

Two differences in `72_`'s definitions are deliberate improvements and win over the narrower readings here: **`L-BND` is broader than "isolation"** — it includes SQL-role and capability boundaries, which is where invariant 4 is actually attacked, not only cross-tenant reads. And **`L-TIME` is broader than "concurrency"** — clock-dependent defects are found by the same stance as interleaving defects, and separating them would leave the frozen-clock failures unowned.

Per-phase lens assignment is owned by `72_` §3, not by §4.7 below; where the two tables disagree, `72_` wins.

**Role reconciliation.** This document defines five roles (Builder, Bug Hunter, Gate Verifier, Fixer, Integrator); `72_` names four (Hunter, Triager, Reviewer, Fixer) because it describes only the defect half of the loop. They map: `72_`'s **Hunter** is the Bug Hunter, its **Fixer** is the Fixer, its **Triager** is the Integrator acting in triage, and its **Reviewer** is the Gate Verifier. There is no role in either document without a counterpart in the other.

### 4.1 Invariant

**Hunts.** States the four invariants and the grounding invariant forbid, reached through a path the schema `CHECK` constraints do not cover. Also the revision rule, which is an invariant in everything but name.

**Attack patterns.**

- Enumerate every write to a canonical table and ask which invariant it could break. Then look for the one write that is not a mechanical translation of a frozen `ChangePlan` (`specs/12_KERNEL_ALGORITHMS.md` §1.5 — "no branching logic lives in the write path"). Branching in the write path is where invariants die.
- Find the gap between the database check and the domain predicate. A `CHECK` is a coarse net; the domain function is the fine one. Anywhere they differ is reachable.
- Try to reach a forbidden state by a *legal sequence of legal operations* rather than by one illegal operation. Invariant violations in transactional systems are almost never single-statement.
- Count. For each commit path, count revision increments, count `state_transitions` rows, count `outbox_events` rows, and compare against `specs/12_KERNEL_ALGORITHMS.md` §6.1 rules R1 and R3.
- Ask what happens when a set is empty: zero support edges, zero fulfillments, zero evidence ids, zero conflicts.

**Mandatory at phases.** 1, 2, 4, 9, 10. (Phase 4 owns the canonical write path; phase 9 owns invariant 4.)

**Example defects in this system.**

1. **A `FULFILLED` commitment with `outstanding_amount` above zero.** The schema check `ck_commitments_outstanding_blocks_fulfilled` blocks the obvious form. The reachable form is arithmetic: `specs/12_KERNEL_ALGORITHMS.md` §4.1 defines `fulfilled = MIN(admitted_sum, committed_amount)` and `outstanding = committed_amount - fulfilled`, with money as `DECIMAL(20,4)`. A status decision that quantises to 2 dp before comparing against zero writes `FULFILLED` while `outstanding_amount` holds `0.0001`, and the check constraint compares the stored 4 dp value — so either the constraint fires at runtime during the demo, or the same rounding was applied to the stored column and the invariant is silently false. Second reachable form: a fulfillment whose `admission_status` is `REJECTED_CURRENCY` (§4.2 step 1) counted into `admitted_sum` because the aggregation filters on `admission_status != 'REJECTED'` rather than `== 'ADMITTED'`. Beltline Movers, `committed_amount` 420.0000, `fulfilled_amount` 200.0000, `outstanding_amount` 220.0000 is the fixture to attack.
2. **A canonical belief version with no grounding.** Two spellings of the grounding invariant exist in the pack, and the gap between them is the defect. `00_PRODUCT.md` §0.2 says "at least one `belief_support` edge"; `quality/23_PHASE_GATES.md` §1 says "at least one `SUPPORTS` edge in `belief_support`"; `quality/20_TDD_STRATEGY.md` §5.1 requires a test for "one `QUALIFIES` edge only" among the three grounding cases. `G2.8` checks a stored `support_edge_count`. So a belief version whose only edge is `QUALIFIES` or whose only edge is `CONTRADICTS` satisfies the database check and violates the domain predicate. The path that reaches it: `RETAIN_INCUMBENT_AUTO` and `RETAIN_INCUMBENT_DISPUTED` both write a new version whose *value is unchanged* (§3.1 rule G3, grounding is frozen per version, so any grounding change needs a new version). If the carry-forward of the incumbent's `SUPPORTS` edges sits inside a branch that only runs when the value changed, the retained version ships carrying only its new `CONTRADICTS` edge. That is precisely the hero's `balance_owed` v2, which is the single most-looked-at row in the demo.
3. **A case revision that increments twice for one commit.** `specs/12_KERNEL_ALGORITHMS.md` §6.1 R1: exactly one increment per canonical commit per aggregate, regardless of how many rows are touched. R5: a fulfillment increments both `commitments.revision` and the parent `cases.revision`. A `ChangePlan` carrying both a `CommitmentDelta` and a `case_transition` has two code paths that each want to bump the case, and both are individually correct. Reproduction: submit a proposal that admits a fulfillment *and* qualifies for a status transition, then assert `kernel_decisions.case_revision_after - case_revision_before == 1` and that every `state_transitions.case_revision` and `outbox_events.aggregate_version` in that commit equals the single new value (R3). A double increment silently invalidates every `action_intents.basis_case_revision` bound to the old value, which surfaces as an approval that mysteriously goes stale.
4. **Evidence mutated rather than appended.** Invariant 1. The reachable path is a re-parse: an artifact re-processed after a parser fix, where the ingestion code `UPDATE`s `evidence_items.normalized_text` or `extraction_confidence` in place instead of inserting new rows and marking the old ones `SUPERSEDED`. Detect by asserting no `UPDATE` statement in the repository layer targets `evidence_items` except the single lifecycle column transition, and by re-running ingestion on the same `content_sha256` and diffing every row of `evidence_items` for that artifact.
5. **An external effect from an uncommitted basis.** Invariant 4. Attack the ordering: create an `ActionIntent` in the same request that submitted the proposal, before `kernel_decisions.committed_at` is non-null. `G9.6` asserts `409 NO_COMMITTED_BASIS` for the direct case; the hunt is for the indirect one, where the intent is built from a State Proof read on a connection that has not yet observed the commit.

### 4.2 Isolation

**Hunts.** Any path by which one user's or one tenant's data reaches another, and any path by which a principal exceeds its granted capability. The corpus is built for this: 18,035 evidence rows total, 16,035 in the hero user's own partition, with `iso-a` and `iso-b` contributing 1,000 each specifically so leakage is detectable.

**Attack patterns.**

- Remove one predicate at a time and ask what else catches it. `specs/13_RETRIEVAL_SPEC.md` §14 declares three layers: the mandatory `user_id` vector-index prefix, the bound parameters in `provenance_db.repositories.evidence.ann_search()`, and static analysis forbidding a second vector-SQL implementation. Hunt for the fourth query that nobody counted.
- Follow every join in the five `agent_*_v1` views and check that the scope predicate is repeated on the joined side, not merely on the driving table.
- Read every `GRANT` in migrations `0001`–`0008` and ask who else is included. `G11.1` filters `grantee='pv_agent_reader'`; a grant to `PUBLIC` is invisible to it.
- Take every identifier that arrives from a request body and ask whether it is used for authorisation or only for lookup. `specs/15_API_SPEC.md` and `quality/23_PHASE_GATES.md` `G8.5` require capability objects rather than caller-supplied `user_id`.
- Check that non-existence and non-authorisation are indistinguishable to the caller: `G8.4` demands `404 CASE_NOT_FOUND`, not `403`.

**Mandatory at phases.** 2, 6, 8, 11. (Phase 6 owns retrieval isolation; phase 11 owns the SQL grant boundary and the governed MCP read path.)

**Example defects in this system.**

1. **An agent view that leaks across the join.** `agent_evidence_retrieval_v1` filters `retraction_status = 'ACTIVE'` and the user scope on `evidence_items`, then joins `claims` for context. If the join condition is `claims.evidence_id = evidence_items.id` without repeating the user predicate, and any `claims` row for `iso-a` references a hero evidence id through a data defect, the view returns a foreign row. `G6.3(a)` tests the repository path over 200 returned ids; it does not test the view path, and phase 11's `G11.1`–`G11.3` test grants and retraction, not join scope. Reproduction: query `agent_evidence_retrieval_v1` as `pv_agent_reader` for the hero user and assert every returned row's `user_id` equals the hero user, then repeat for `iso-a`.
2. **A second vector query.** `specs/13_RETRIEVAL_SPEC.md` §14 makes `provenance_db.repositories.evidence.ann_search()` the only entry point, with `$1 tenant_id` and `$2 user_id` bound. Judge Mode's counterfactual endpoint needs a retrieval run with memory disabled, and the natural implementation writes its own query "just for the OFF side". That query is outside the static-analysis net and, being the OFF path, returns few or zero rows in every test, so nothing notices its scope. Reproduction: `grep` for `<=>` and for `vector_cosine_ops` outside the repository module, then run `EXPLAIN` on every hit and check for a constrained `user_id` prefix on `evidence_embedding_ann_idx`.
3. **`idempotency_records` keyed without the principal.** `G8.6` proves replay returns an identical body for the same key and same body, and `409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_BODY` otherwise. Neither half involves a second user. If the uniqueness is on the key alone rather than on `(tenant_id, user_id, key)`, a second user presenting the same key receives the first user's stored response body. Reproduction: `POST /v1/artifacts/upload-intent` as the hero with `Idempotency-Key: K`, then the same key as an `iso-a` user, and diff the bodies.
4. **A grant to `PUBLIC`.** Any `GRANT SELECT ON <view> TO PUBLIC` in a migration satisfies `pv_agent_reader`'s needs and is invisible to `G11.1`, whose `WHERE grantee='pv_agent_reader'` filter never sees it. It also silently grants the Cloud Managed MCP Server's role, whichever role that turns out to be, access to whatever the view exposes. Reproduction: `SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee IN ('public','PUBLIC');` — expect zero rows, and pair it with a positive control proving the query returns rows when a `PUBLIC` grant exists.
5. **Cross-user grounding.** `implementation/00_IMPLEMENTATION_MAP.md` and the DDL test list cover cross-user *evidence* references (test 11). `belief_support.source_id` can point at a `CLAIM` or a `BELIEF_VERSION`. Hunt for whether those two source kinds are validated for user scope at kernel preflight step 5 the way evidence is, or whether only `EVIDENCE` is checked.

### 4.3 Concurrency

**Hunts.** Anything that is correct in a single-threaded reading and wrong under a retry, a duplicate delivery, or an interleaving. This system is `SERIALIZABLE` with a bounded `40001` retry, a transactional outbox, and an external scheduler, so it has three independent sources of repetition.

**Attack patterns.**

- For every value computed before `BEGIN` and used after it, ask what happens on retry. `specs/12_KERNEL_ALGORITHMS.md` §1.1 states the rule directly: steps 4–16 run **twice**, and a plan replayed from PHASE A is a plan derived from a stale snapshot.
- Force a retry rather than simulate one. `G3.2` is explicit that a monkeypatched `40001` proves nothing about CockroachDB; two overlapping transactions on one row is the only accepted method.
- For every idempotency key, assert **string equality across attempts** before asserting the single effect (§23.10). A key derived from `uuid4()` at call time makes every idempotency test pass and every production replay double.
- Kill the process at each step boundary and ask what is now true. Between `put_events` and marking the outbox row `SENT` is the classic one.
- Look for state read outside the transaction that is written inside it.

**Mandatory at phases.** 3, 4, 9, 10, 14.

**Example defects in this system.**

1. **The retry that replays a stale plan.** The `ChangePlan` is frozen at step 16 and steps 17–28 are a mechanical translation of it. The tempting implementation computes the plan once in PHASE A and passes it into the transaction callback. On the first attempt this is indistinguishable from correct. On a `40001` retry the callback re-runs with the same plan against re-read rows, writing `case_revision_before` from a revision that no longer exists, and `UPDATE cases ... WHERE revision = $stale` matches zero rows — which surfaces as `OPTIMISTIC_REVISION_MISMATCH` if R4's redundant predicate was implemented, and as a **silently skipped case update with the belief writes committed** if it was not. Reproduction: run the concurrency harness, then assert that the number of `SELECT ... FROM cases ... FOR UPDATE` executions equals the attempt count, not 1.
2. **`tx_now` captured in the wrong scope.** §1.4 requires `tx_now = SELECT transaction_timestamp()` as the first statement inside the transaction, and every `recorded_at`, `superseded_at`, `detected_at` in the commit uses it. Capture it once per *call* instead of per *attempt* and two attempts stamp the same instant; capture it with `datetime.now()` and you have violated the §1.3 ban on wall-clock reads inside PHASE B, which also breaks fixture reproducibility and the §8.6 "at most one live belief version per instant" structural invariant. Reproduction: force two attempts and assert the committed `recorded_at` equals `transaction_timestamp()` of the *successful* attempt.
3. **Outbox double-publish that the consumer does not absorb.** The sweeper claims a row under a lease, calls `events.put_events`, then marks it `SENT`. A crash in between republishes on the next sweep. `processed_events` dedupe on `(event_id, consumer_name)` is designed to absorb exactly this — provided `event_id` is stable across publishes. Hunt for whether `event_id` is the `outbox_events` row id (stable) or generated at dispatch time (not). `G10.1` asserts the second insert on `(event_id, consumer_name)` is a duplicate key; it does not assert that two dispatches of one outbox row produce the same `event_id`.
4. **The approval that goes stale between revalidation and send.** `G9.1` proves a stale approval aborts. The hunt is the race: the executor reads `cases.revision`, compares it to `basis_case_revision`, then sends. If the read and the `action_executions` insert are not in one transaction, a kernel commit landing in that window sends an email whose basis moved. This matters because the send is, per `quality/23_PHASE_GATES.md` §15, the only irreversible operation in the entire system.
5. **Concurrent fulfillments both recomputing from the ledger.** §4.1 recomputes `admitted_sum` by aggregation inside the transaction precisely so duplicates cannot double-count. If the ledger read happens in PHASE A and only the write is inside the transaction, two concurrent fulfillments against Beltline Movers both compute `fulfilled = 200` and one silently overwrites the other, leaving `outstanding_amount` at 220.0000 after two payments.

### 4.4 Honesty

**Hunts.** The gap between what the system does and what the system says it does. This lens exists because Provenance is a system of record whose demo is its argument, and the pack's own framing — `quality/23_PHASE_GATES.md` §1 — is that every failure here is a reporting failure before it is an engineering failure.

**Attack patterns.**

- For every number on a screen, ask whether it is counted at query time or written as a constant. Canon requires `corpus_size_user_scoped` and `corpus_size_visible` to be counted at query time, never a constant.
- Delete the backing row and reload. `G11.4` says deleting `agent_runs.tool_calls` must empty the MCP panel. Generalise it: every panel gets this treatment.
- Change the truth and see whether the surface moves. This is `G12.4`'s mutation probe generalised beyond the trace.
- Read every disclosure surface and check it is computed from the same source as the behaviour it discloses.
- For every `NOOP`, demand the reason code (§23.8). An unexplained `NOOP` is an exception wearing a costume.

**Mandatory at phases.** 5, 11, 12, 15.

**Example defects in this system.**

1. **A rendered constant where a count is required.** The hero's user-scoped corpus is 16,035 of 18,035 total. Both numbers are known at authoring time, which is exactly why one of them ends up in a template. Canon forbids both the constant and, separately, rendering the cross-tenant 18,035 as a user-scoped figure. Reproduction: insert one evidence row for the hero user, reload Judge Mode, and assert the displayed figure moved to 16,036.
2. **A trace panel that survives its data.** `DELETE`/`UPDATE ... SET tool_calls = NULL` on the hero `agent_runs` row and reload. If the MCP panel still renders three calls against `pv_agent_reader`, it is templated. Do the same for the Memory Trace nodes and for the State Proof lineage: the pack's `G12.2` proves the rendered node ids are a subset of the API payload, which a template that reads ids from the payload and then renders fixed labels still satisfies.
3. **Counterfactual parity that is not parity.** Canon permits exactly four differences between MEMORY OFF and MEMORY ON: `retrieval_enabled`, `canonical_memory_enabled`, `corpus_size_visible`, and the resulting `output`. Anything else differing is a parity failure, and `parity.all_equal = false` must replace both output columns with a failure banner rather than rendering them. The reachable defect is a different `max_tokens` or a different decode parameter on the OFF path, which changes `decode_params_sha256` and makes the most persuasive twenty-five seconds of the video a rigged comparison. Also confirm there is no `pv-draft-nomemory-*` prompt asset: canon says MEMORY OFF uses `pv-draft-1.0.0` with an empty TRUSTED STRUCTURED CONTEXT block.
4. **A disclosure computed from a different source than the behaviour.** `GET /v1/version` carries `fixture_mode`, `agent_mode`, `otlp_export`, `schema_revision`, `db_ok`, `git_sha`. If `fixture_mode` is read from an environment variable at control-plane import time while the agent runtime resolves its own mode per request, the two can disagree: the banner says live and the graph replays cassettes. `S3` makes `fixture_mode: false` a release-invalidating field, so this is the highest-consequence honesty defect in the build. Reproduction: flip the agent runtime to fixture mode without restarting the control plane and check whether `/v1/version` notices.
5. **A `NOOP` that is a swallowed exception.** Query `SELECT reason_code, count(*) FROM kernel_decisions WHERE status='NOOP' GROUP BY 1;` after a full demo run and check every code against the closed enum and against what the demo script expects. A `NULL` or an unexpected code is a defect even though nothing appeared to break.
6. **Wrong field names in a disclosure.** Canon fixes `git_sha` (not `build_sha`), the HTTP field `mcp_tool_calls[]` against the column `agent_runs.tool_calls`, and `/v1/healthz` as a bare liveness probe that never carries `fixture_mode`.

### 4.5 Contract drift

**Hunts.** Divergence between the implementation and the frozen names, counts, enums, and shapes. This is the highest-yield lens in the first four phases and the cheapest to run, because most of it is mechanical.

**Attack patterns.**

- Diff every closed enum in code against `specs/11_CONTRACTS.md`, which owns enum membership. Canon: DDL checks, generated prompt schemas, APIs, fixtures, and UI filters mirror those values exactly, with no layer-local aliases.
- Diff the route list against `specs/15_API_SPEC.md` (`G8.1` asserts 31 documented, 31 implemented, 0 drift — run the same comparison for error codes and for field names within each payload, which `G8.1` covers less thoroughly).
- Grep for every canonical identifier and check its exact spelling, including prefixes and suffixes.
- Read the worked examples in the specs against the canon register, because **the specs contain worked examples that canon explicitly declares to be different datasets**, and a builder will implement whichever it read last.
- Check every example name against `specs/10_DATABASE_DDL.md` §17.3. A counterparty name that appears in code and not in §17.3 is a defect, not a stylistic choice.

**Mandatory at phases.** 1, 2, 7, 8, 12.

**Example defects in this system.**

1. **The two hero worked examples.** `specs/12_KERNEL_ALGORITHMS.md` §1.6 walks the pipeline to revision `7 → 8` with disposition `RETAIN_INCUMBENT_AUTO`, conflict status `AUTO_RESOLVED`, `requires_human = false`, via entailment EN-1 on the `SERVICE_STATUS` family. The hero commit canon is revision `12 → 13`, conflict `VALUE_CONFLICT` in family `BALANCE`, status `NEEDS_HUMAN`, severity `HIGH`, `requires_human = true`, disposition `RETAIN_INCUMBENT_DISPUTED`, produced by gate H5 because `monetary_exposure = 186.00 >= 100.00` short-circuits before the authority-margin test. Canon says both are correct and they are **not the same row**. A Builder who implements §1.6's numbers as the hero fixture produces a phase 4 that passes its own tests and fails `G4.1` on arrival. This is the single most likely contract-drift defect in the build.
2. **Attention enum crossing.** `cases.attention_level` accepts exactly `NONE`, `INFO`, `ATTENTION`, `URGENT`, with no aliases. The Advocate's attention classes are a separate model output — `NONE`, `FYI`, `ACTION_SUGGESTED`, `ACTION_REQUIRED`, `HUMAN_DECISION` — mapped deterministically and **never stored directly**. Writing `ACTION_REQUIRED` into `cases.attention_level` is the defect; it will pass a Pydantic model that types the column as `str`.
3. **Model and embedding identifier drift.** Tier E is `anthropic.claude-haiku-4-5`; Tier R is `anthropic.claude-opus-5`; embeddings are `amazon.titan-embed-text-v2:0` at 1024 dimensions, cosine, frozen version `v1`. `G7.4` explicitly names Sonnet 4.6, Gemma 4, GLM 5 and Kimi K2.5 as failures because they are stale identifiers from superseded documents. Drop the `:0` suffix or the `anthropic.` prefix and the call fails at runtime, not at review.
4. **The reopen reason code.** `CONTRADICTORY_EVIDENCE` is a **guard** on the `RESOLVED → REOPENED` transition, not merely a label. `CONTRADICTORY_EVIDENCE_ADMITTED` or `RC_CONTRADICTORY_EVIDENCE` raises `IllegalTransition`, so this drift fails loudly — but it fails in phase 4, at the gate, after the whole kernel is written.
5. **Hero dataset drift.** Northline Fiber (two relationships, `NF-4471-8802` and `NF-9913-2250`), Harborview Property Management (`HPM-LEASE-2024-3B`), Beltline Movers (`BM-88214`), Kestrel Analytics (`KA-EMP-3308`, the **employer**, never the mover), Cascade Power (`CP-770194`), user Alex Rivera. The retired persona "Dana Whitfield" and the retired "Kestrel Moving Co." must not appear anywhere, and the latter matters because it would attribute the USD 420 damage claim to the user's employer. Also check the dates: inspection `2026-05-16`, deposit `due_at` `2026-06-15T00:00:00Z`, trigger wake `2026-06-15T00:01:00Z` (`due_at + WAKE_MARGIN_SECONDS`), demo clock `2026-09-18`, and 95 days overdue **derived** from those two rather than stored as a literal.
6. **Structural counts.** 26 tables, 5 `agent_*_v1` views, 51 eval scenarios, 626 tests across 8 layers (392/96/58/14/22/9/24/11), 18,035 evidence rows of which 16,035 are the hero's, 18 sabotage entries, 17 closed trace node types. A gate that asserts a count against a wrong figure fails on arrival, which is why canon calls these contract values rather than documentation trivia.

### 4.6 Vacuity

**Hunts.** Tests, assertions, and gate commands that cannot fail. This lens overlaps with the sabotage matrix on purpose and extends past it: the matrix checks the 18 entries someone wrote, and this lens checks everything else, including the harness itself.

**Attack patterns.**

- For each new test, delete the production line it claims to protect and re-run. If it stays green, the test is decoration. This is `make sabotage` applied by hand to code that has no matrix entry, and its output is the missing entries.
- For every "expect zero rows" assertion, find its positive control or write one. §23.7 is the rule; the hunt is for the assertions that arrived without a pair.
- Check that a test selection selects. A typo'd `-k` expression or an unregistered marker collects nothing.
- Check the harness's own exit-code plumbing.
- Confirm RED was observed. A test written after the implementation has never failed and nobody knows what it would take to make it fail.
- Look for assertions satisfied by an exception. `assert result.status == "REJECTED"` is not satisfied by an exception, but `pytest.raises(Exception)` is satisfied by a typo.

**Mandatory at phases.** every phase. Under time pressure, this is the lens to keep (§10).

**Example defects in this system.**

1. **The gate wrapper masking exit codes.** `tools/gate.sh` runs a command and tees stdout and stderr into `ops/gates/logs/<ID>.<sha8>.log`, then records the exit code in the log header. In a POSIX shell, `cmd | tee log` exits with **tee's** status, not the command's, so a failing battery step records `exit=0` and the log header lies. The fix is `${PIPESTATUS[0]}` (or `set -o pipefail`), and the reproduction is a one-liner: wrap `false` in the harness and read the recorded exit code. This defect makes every gate log in the repository untrustworthy, and it is invisible to every assertion in the pack because the pack's assertions are what the harness runs.
2. **`V10` passing because the view returns nothing to anyone.** `V10` asserts no retracted row is reachable through `agent_evidence_retrieval_v1`; `V11` asserts at least 3 retracted rows still exist and still carry embeddings. `V11` counts rows in the base table. So a view with a broken predicate — one that returns zero rows for every user — satisfies `V10` while `V11` stays at 3, and the pairing that §23.7 requires does not actually cover the failure. The missing control is an `ACTIVE` fixture that **must** be reachable through the view. Write it, and confirm it fails when the view's predicate is inverted.
3. **A phase whose new modules added zero sabotage entries.** `G14.6` reports `sabotages: 18 | detected: 18 | UNDETECTED: 0` and says nothing about the nineteenth. The hunt is: list every function added this phase whose name contains a decision, a check, a validation, or a filter; check each against `tests/sabotage_matrix.yaml`; the missing ones are the deliverable. Phase 4 is the acute case, where `provenance_domain/kernel/{authority,disposition,money,case_machine,propositions,revision}.py` are all invariant-bearing.
4. **A rejection test satisfied by the wrong rejection.** `G4.4` requires `decision.status = REJECTED`, `reason_code = REJECTED_INVALID_PROVENANCE`, **and** `kernel_decisions.transaction_opened = false`. A test asserting only the status passes when the kernel rejects for a schema reason, for a tenancy reason, or by raising before it ever set a status — three different bugs, all green. The general rule for this system: assert the reason code and the transaction-opened flag, because the closed reason-code catalogue exists precisely so that assertions can be specific.
5. **A regenerated fixture.** `tools/fixture_guard.py` fails a commit touching both a fixture path and a source path unless it carries a `Fixture-Change-Justification:` trailer and a second reviewer's initials, and the pack concedes in §25 risk 7 that splitting the change across two commits defeats it. The hunt is the git-history read the guard cannot do: for each fixture, find the commit that last changed it and the commit that last changed the code it validates, and look at the distance between them.

### 4.7 Lens assignment by phase

Four is the floor, six the ceiling. The bracketed lenses are the optional two when budget allows.

| Phase | Mandatory lenses | Optional |
|---|---|---|
| 0 scaffold + cluster probe | Vacuity, Contract drift | (Honesty) |
| 1 contracts + domain | Invariant, Contract drift, Vacuity | (Isolation) |
| 2 schema + seed | Invariant, Isolation, Contract drift, Vacuity | (Concurrency) |
| 3 db runtime + retry | Concurrency, Vacuity | (Invariant), (Isolation) |
| 4 **Memory Kernel** | Invariant, Concurrency, Contract drift, Vacuity, Isolation, Honesty | — all six, always |
| 5 read models | Honesty, Vacuity | (Isolation), (Contract drift) |
| 6 embeddings + retrieval | Isolation, Vacuity, Concurrency | (Honesty) |
| 7 LangGraph graphs | Contract drift, Vacuity | (Isolation), (Honesty) |
| 8 API + auth | Isolation, Contract drift, Concurrency, Vacuity | (Honesty) |
| 9 **actions + executor** | Invariant, Concurrency, Vacuity, Isolation | (Honesty), (Contract drift) |
| 10 events + scheduler | Concurrency, Invariant, Vacuity | (Honesty) |
| 11 **MCP + SQL roles** | Isolation, Honesty, Vacuity, Contract drift | (Invariant) |
| 12 frontend + Judge Mode | Honesty, Contract drift, Vacuity | (Isolation) |
| 13 deploy | Honesty, Isolation, Vacuity | (Concurrency) |
| 14 evals + adversarial | Vacuity, Honesty, Invariant | (Concurrency) |
| 15 submission | Honesty, Contract drift, Vacuity | — |

Phases 4, 9, 11, 13 and 15 are the five that `quality/23_PHASE_GATES.md` §22.4 says always get the full verification round. Phase 4 gets all six lenses because it is the only phase where the product either exists or does not.

---

## 5. Dispatch briefs

These are literal and pasteable. Placeholders are written `{{LIKE_THIS}}` and every one of them must be filled before sending — an unfilled placeholder is the one thing in this document that behaves like a `TODO`, and a brief sent with one is a dispatch error, not a style problem.

Every brief begins with the same three lines, referred to below as `{{PREAMBLE}}`:

```text
Read these first and treat them as binding, in this order:
  docs/CANONICAL_DECISIONS.md          (highest authority; frozen names, counts, enums)
  docs/00_PRODUCT.md sections 0 and 3  (the four invariants, the kernel rule, the glossary)
  docs/README.md                       (authority order between documents)
Never invent a name, a number, an enum value, a model id, a role, a view, or a table.
Look it up. If two documents disagree, CANONICAL_DECISIONS.md wins, then 00_PRODUCT.md,
then the numbered specification that owns the concern.
Vocabulary: "grounding" = belief_support edges. "lineage" = the belief_versions
supersession chain. Provenance is the product name only. Do not conflate them.
```

### 5.1 Builder

```text
{{PREAMBLE}}

ROLE: Builder. Phase {{N}} — {{PHASE_NAME}}. Task {{TASK_ID}}.

GOAL (one sentence)
{{TASK_GOAL}}

YOUR SPECS — read these in full before writing anything
{{SPEC_PATHS}}
docs/implementation/00_IMPLEMENTATION_MAP.md  sections 5 and 12
docs/quality/20_TDD_STRATEGY.md               sections 1 and 13.3
docs/quality/23_PHASE_GATES.md                section {{PHASE_SECTION}} only

FILES YOU OWN — you may create and edit these and nothing else
{{OWNED_PATHS}}

FILES YOU MUST NOT TOUCH — other agents own them, or the Integrator does
{{FORBIDDEN_PATHS}}
Always forbidden to every Builder: Makefile, pyproject.toml, .importlinter,
db/migrations/ revision pointers, tests/sabotage_matrix.yaml (append-only, see below),
db/expected_tables.txt, db/seeds/MANIFEST.json,
packages/python/provenance_domain/INVARIANTS.md, infra/cdk/**.
If you believe you need a change in a forbidden file, STOP and report the need.
Do not make it. Do not work around it.

METHOD — test-first, no exceptions
1. RED. Write the failing test first. Confirm it fails FOR THE RIGHT REASON:
   the stub must exist and return a wrong-but-correctly-typed value, so the
   failure is an assertion failure, not an ImportError or an AttributeError.
   Paste the RED output into your report.
2. GREEN. The smallest change that passes. No speculative generality.
   No adjacent refactor. No "while I was in there".
3. REFACTOR. Only with the test green. Re-run after.
4. If you added a function that enforces an invariant, makes a decision,
   validates, or filters, append its entry to tests/sabotage_matrix.yaml in the
   same commit: the symbol to neuter, and the test selection that must then fail.
   Append only. Do not reorder or edit existing entries.

RULES THAT OVERRIDE CONVENIENCE
- The Memory Kernel is never mocked in a correctness test.
- Money is Decimal(20,4) and an ISO 3-character currency. Never float.
- No model call, no network call, no S3 read, no wall-clock read inside a
  database transaction callback.
- No canonical write outside services/control_plane/app/memory_kernel/.
- Every boundary payload carries schema_version.
- Every example name comes from docs/specs/10_DATABASE_DDL.md section 17.3.

ACCEPTANCE CRITERIA FOR THIS TASK
{{ACCEPTANCE_CRITERIA}}

WHAT YOU MUST NOT DO
- Do not run `make gate-{{N}}` and report the result as a gate outcome.
  You may run any test for your own feedback. Only the Gate Verifier's run counts.
- Do not read or ask for another Builder's task, branch, or report.
- Do not implement anything outside {{TASK_ID}}, even if you can see it is missing.
  Report it instead.

DELIVERABLE
Branch: phase{{N}}/{{TASK_ID}}
Report: ops/agents/tasks/{{TASK_ID}}.md containing
  - files created and modified
  - the verbatim RED output and the verbatim GREEN output
  - the sabotage matrix entries you appended
  - each acceptance criterion with the command and output that satisfies it
  - EXPLICITLY: what did you assume rather than read? List at least one item or
    describe the search that legitimately came up empty. "Nothing" is not an answer.
```

### 5.2 Bug Hunter

```text
{{PREAMBLE}}

ROLE: Bug Hunter. Phase {{N}} — {{PHASE_NAME}}. Lens: {{LENS_NAME}}.
Commit under attack: {{COMMIT_SHA}}

YOUR STARTING ASSUMPTION
This code is broken. Your job is to find out how. You are not reviewing it,
you are not summarising it, and you are not confirming that it works.
A hunt that returns a description of the code has failed.
A hunt that returns "no confirmed defect, here is the search I ran" has succeeded.

YOUR LENS — hunt this and nothing else
{{LENS_BLOCK}}
  (paste section 4.{{LENS_INDEX}} of docs/EXECUTION/71_AGENT_WORKFLOW.md verbatim:
   what it hunts, the attack patterns, the example defect classes)

WHAT YOU HAVE
- A clean clone at {{COMMIT_SHA}} in {{WORKTREE_PATH}}
- A working database: {{DB_URL_ENV_VAR}} (resolved at call time, never printed)
- These specs, which define correct behaviour for the surface you are attacking:
  {{SPEC_PATHS}}
- docs/implementation/06_CODING_AGENT_HANDOFF.md section 19 (the guardrail list)

WHAT YOU DO NOT HAVE, AND WHY
You have not been given docs/quality/23_PHASE_GATES.md, the phase's exit assertions,
or the gate battery. This is deliberate and is not an oversight you should work
around. Those assertions are the failures the authors already anticipated; a hunter
holding them re-verifies them instead of hunting past them, because a checklist is
easier than a search. Everything you need from that document has been restated in
your lens block in different words. Do not read it, do not grep for it, and do not
ask for it. If you find yourself wanting it, that is the signal that you are about
to re-verify rather than hunt.
You also do not have the build conversation, the Builder's report, or the other
lenses' findings. Do not request them.

WHAT YOU MAY WRITE
Your report file, and throwaway files inside {{WORKTREE_PATH}}.
You may NOT fix anything. A fix destroys the reproduction and enters the code as an
unreviewed change. If you find a defect, prove it and stop.

REPRODUCTION IS THE DELIVERABLE
A finding without a reproduction that runs from a clean clone is a hypothesis.
File hypotheses separately, under "Unproven suspicions", and keep them short.

DELIVERABLE
ops/agents/hunts/H{{N}}.{{LENS_NAME}}.md

For each confirmed finding:
## F-{{N}}.{{LENS_NAME}}.<k> — <one-line claim>
Severity: BLOCKS_GATE | CORRECTNESS | ROBUSTNESS | HYGIENE
Invariant or contract at risk: <invariant 1-4 | grounding | named spec clause>
Reproduction: <exact commands / SQL / fixture, runnable from a clean clone>
Observed: <verbatim output>
Expected, and the document clause that says so: <reference>
Blast radius if shipped: <what a reviewer or a user sees>

Then, always, a final section:
## Search performed
<what you looked at, what you ruled out and on what basis, what you did not look at>
```

### 5.3 Gate Verifier — message 1 of 2

```text
{{PREAMBLE}}

ROLE: Gate Verifier. Gate G-{{N}} — {{PHASE_NAME}}. Commit {{COMMIT_SHA}}.

You are running a verification round as defined in docs/quality/23_PHASE_GATES.md
section 22. Read that document in full, then section {{PHASE_SECTION}}, then the
specs this phase depends on:
{{SPEC_PATHS}}

You have NOT been given the build conversation, the Builder's completion report, or
any Bug Hunter findings. That is the section 22.1 rule and it is the point of this
role: a reviewer who was in the room while the code was written has already absorbed
the builder's model of why it works, which is the exact thing under test. You will be
given the Builder's claims in a SECOND message, AFTER you have produced your own
independent result. Do not ask for them now. Do not go looking for them in the
repository history.

RUN, IN THIS ORDER (section 22.2)
1. CLEAN CLONE. git clone {{REPO_URL}} into an empty directory, checkout
   {{COMMIT_SHA}}. Never review a working tree. "Works on the build machine" is
   caught here or not at all.
2. BOOTSTRAP AND BATTERY. make bootstrap && make gate-{{N}}. Capture everything,
   verbatim, including the exit code of each step. If the harness reports an exit
   code, verify independently that it is the command's exit code and not a pipeline's.
3. REGRESSION SWEEP. make gate-{{N_MINUS_1}}, and make gate-{{N_MINUS_2}} if this
   phase touched provenance_contracts, provenance_domain, provenance_db, the
   Memory Kernel, or the schema. A gate that passes while breaking an earlier one
   is a failing gate.
4. MUTATION PROBE. Pick the single exit assertion that matters most for this phase
   and break the thing it claims to protect. Use `make sabotage` where an entry
   exists; write a new entry where it does not. If the assertion still passes, the
   assertion is decoration and the gate is REJECTED regardless of everything else.
   The symbol you chose and why is part of the report.
5. GUARDRAIL DIFF READ. Read this phase's diff against
   docs/implementation/06_CODING_AGENT_HANDOFF.md section 19.
6. STANDING QUESTIONS. Answer Q1-Q7 from section 22.3 in writing, in your own
   words, before writing a verdict. An answer of "nothing" or "none" to Q1-Q6 is
   itself a finding: either produce an item or describe the search that came up empty.

EVIDENCE RULE (section 3)
No assertion is reported without its command and its verbatim output. Not a summary.
Not "0 failures". The output. An assertion you could not run is reported as
NOT RUN with a reason — that is an acceptable, honest state. A silently omitted
assertion is a gate failure.

DELIVERABLE — message 1
ops/gates/PHASE_{{NN}}.md in the section 4.1 template, complete except for the
verdict line, which you leave as PENDING RECONCILIATION.
Plus the scrubbed logs under ops/gates/logs/.
Do not write a verdict yet.
```

### 5.4 Gate Verifier — message 2 of 2

```text
Here are the Builder's completion report and the Bug Hunter findings for phase {{N}}.
{{BUILDER_REPORTS}}
{{HUNTER_REPORTS}}
{{TRIAGE_DISPOSITIONS}}

Now do three things.

1. DIFF YOUR RESULT AGAINST THE CLAIM. For every statement in the Builder's report
   of the form "X works", locate your own output that confirms or contradicts it.
   Any such sentence with no adjacent output in YOUR run is struck, and you say so.

2. CHECK THE HUNT WAS ABSORBED. For every finding dispositioned CONFIRMED, locate the
   regression test that now covers it and run it. For every finding dispositioned
   ACCEPTED_DEBT, confirm it appears in your carried-debt section with the phase that
   closes it. A confirmed defect with no regression test is carried debt whether or
   not anyone called it that.

3. WRITE THE VERDICT.
   SIGNED — every assertion PASS with output, no unlisted debt.
   SIGNED WITH CARRIED DEBT — legitimate and expected under time pressure. Enumerate
     every item and name the phase that closes it. What is not legitimate is SIGNED
     with debt that was not written down.
   REJECTED — name the specific assertion that failed and the output that shows it.

Update ops/gates/PHASE_{{NN}}.md with the verdict, the reconciliation, and the
rollback position at time of signing.
```

### 5.5 Fixer

```text
{{PREAMBLE}}

ROLE: Fixer. Defect {{DEFECT_ID}}.

THE DEFECT
{{DEFECT_RECORD}}
  (the full finding block from the hunt report: claim, severity, invariant at risk,
   reproduction, observed output, expected behaviour and its clause)

THE CLAUSE THAT DEFINES CORRECT BEHAVIOUR
{{SPEC_CLAUSE}}

FILES IN SCOPE
{{FILES_IN_SCOPE}}
You may not edit anything else. If the fix requires touching a file outside this
list, STOP and report that, with the reason. Do not widen the scope yourself.

METHOD
1. REPRODUCE FIRST. Run the reproduction exactly as written and paste the failure.
   If it does not reproduce, stop and return NOT_REPRODUCED with what you observed.
   That is a normal outcome and not a failure of anyone's work.
2. WRITE THE REGRESSION TEST. It must fail before your change and pass after, and it
   must live at the correct layer per docs/quality/20_TDD_STRATEGY.md section 3.1
   (kernel and state behaviour is a database integration test, not a unit test with
   a fake connection).
3. MAKE THE MINIMAL CHANGE. Fix the defect, not the neighbourhood. Do not rename,
   do not reorganise, do not "clean up while I am here".
4. ADD THE SABOTAGE ENTRY. Append the symbol your fix now depends on, and the test
   selection that must fail when it is neutered, to tests/sabotage_matrix.yaml.
5. RE-RUN THE REPRODUCTION and paste the result.

WHAT YOU HAVE NOT BEEN GIVEN, AND WHY
- The other open defects. Batched context produces opportunistic refactoring, and an
  opportunistic refactor inside a fix is the change nobody reviewed.
- The gate assertion that will eventually check this area. A fixer holding the
  assertion fixes the assertion — it adds the narrow special case that makes the
  named query return zero rows, and the defect survives one input away. Fix the
  defect described above, on its own terms.

DELIVERABLE
Branch: fix/{{DEFECT_ID}}
The regression test, the minimal diff, the sabotage entry, and the defect record in
ops/defects/DEFECTS.md moved to FIXED with the before-output and after-output attached.
```

### 5.6 Integrator — self-brief at the start of each phase

Not a dispatch. A checklist the Integrator answers in writing in `ops/agents/PHASE_<NN>_DISPATCH.md` before dispatching anything.

```text
PHASE {{N}} — {{PHASE_NAME}}    opened {{ISO8601}}    base commit {{COMMIT_SHA}}

1. Entry criteria: is G-{{N_MINUS_1}} signed? Where is the report? What debt did it carry
   into this phase, and which task closes each item?
2. Decomposition: list every task with its owned paths and its forbidden paths.
   For every pair of tasks, are the owned-path sets disjoint? If not, they are one task.
3. Shared files: which files will need to change that no Builder may touch? I own those.
   When do I make each change — before dispatch, or at reconcile?
4. Single-pass carve-out (section 8): does this phase contain work that must NOT be
   fanned out? Name it. Phase 4's kernel transaction ordering and phase 13's CDK graph
   always qualify.
5. Lenses: which four to six, per section 4.7? Who runs them and against what commit?
6. Verifier: is the context clean? Have I leaked the build conversation into any file
   the Verifier will read — a commit message, a code comment, a report checked into
   the tree at a path the Verifier's clone will contain?
7. Budget: what do I cut first if this phase runs long? (Section 10 answers this:
   never the mutation probe and never the Vacuity lens.)
8. Rollback: what is the exact command that returns the system to the G-{{N_MINUS_1}}
   state, and what about it cannot be undone?
```

---

## 6. Context isolation rules

Isolation is the whole mechanism. Every role's blind spot is covered by a role that was denied the context which creates it.

### 6.1 The matrix

| Artifact | Builder | Hunter | Verifier | Fixer | Integrator |
|---|---|---|---|---|---|
| `CANONICAL_DECISIONS.md`, `00_PRODUCT.md` §0/§3 | yes | yes | yes | yes | yes |
| Owning specs for the surface in question | yes | yes | yes | clause only | yes |
| Other phases' specs | no | no | dependencies only | no | yes |
| `quality/23_PHASE_GATES.md` phase section | yes | **no** | yes | **no** | yes |
| `quality/23_PHASE_GATES.md` in full | no | **no** | yes | no | yes |
| The build conversation | own only | **no** | **no** | no | yes |
| Builder completion report | own only | no | **message 2 only** | no | yes |
| Other Builders' task briefs and branches | no | n/a | n/a | n/a | yes |
| Hunter findings | no | own only | message 2 only | own defect only | yes |
| Other open defects | no | no | message 2 only | **no** | yes |
| Gate battery output | no | **no** | own run | no | yes |
| Cluster write credentials | task-scoped | **read only** | read only | task-scoped | yes |

### 6.2 The rules, stated as rules

**R-I1 — A Hunter never sees the gate battery.** Not the assertions, not `make gate-<N>`, not the phase section of `quality/23_PHASE_GATES.md`. The reason is behavioural, not procedural: given a checklist and an open-ended search, an agent will do the checklist, because the checklist terminates and the search does not. The battery is the set of failures the authors already anticipated; a hunter that re-verifies it produces a duplicate of the Verifier's work and contributes nothing the pack did not already have. Anything a Hunter needs from that document is restated in the lens block in different words, so the technique transfers and the answer key does not.

**R-I2 — A Verifier never sees the build conversation.** This is `quality/23_PHASE_GATES.md` §22.1, unmodified. It extends to indirect channels, which is where it actually leaks: a commit message that explains why an approach was chosen, a code comment that argues for correctness, a design note checked into the tree, a `NOTES.md` at the repository root. The Verifier clones the repository, so anything in the repository is in its context. **The Integrator's phase-7 job is to check that the tree contains no build-conversation residue before the Verifier is dispatched.**

**R-I3 — Builders do not see each other.** Two Builders with visibility into each other's plans converge on a shared helper, then both write it. The observed failure is not a merge conflict, which is loud; it is two subtly different helpers, or one helper that satisfies neither caller.

**R-I4 — A Fixer sees one defect.** Named separately from R-I3 because it is the rule most often broken for apparent efficiency. Handing one agent four defects saves three dispatches and costs one unreviewable diff.

**R-I5 — Nothing crosses upward without an artifact.** An agent's output is a file in the repository, not a claim in a conversation. This is `quality/23_PHASE_GATES.md` §3 applied to the process rather than to the phase: if it was not written down with its output, it did not happen. It also means a lost session costs nothing that was already filed.

**R-I6 — Credentials follow the role.** Hunters and Verifiers get read-only database access (`pv_ops_reader` where it exists after migration `0008`, or `pv_app_reader_writer` against `provenance_ci` where a read-write path is genuinely needed). No agent of any role receives `pv_migrator` or `pv_kernel_writer` credentials against the `provenance` database interactively; migrations and kernel writes happen through committed code, run by the Integrator. This is the same argument the product makes about agents and SQL write credentials, applied to the build.

### 6.3 What may cross, deliberately

- **Canon.** Every role gets `CANONICAL_DECISIONS.md`. There is no version of this process where an agent guesses a name.
- **Reproductions.** A Hunter's reproduction crosses to the Fixer verbatim. That is the whole value of the reproduction discipline.
- **Regression tests.** A Fixer's test crosses to the Verifier by being in the tree, which is exactly the intended channel.
- **Carried debt.** Every gate's carried debt crosses into the next phase's decomposition as a task. Debt that does not become a task is debt that will be discovered at release.

---

## 7. Parallelism and file-conflict safety

### 7.1 The rule that prevents most of the damage

**Two Builders never own the same file.** Not "coordinate on", not "be careful with" — never own. Decomposition is complete when every task's owned-path set is disjoint from every other's. If two tasks cannot be given disjoint paths, they are one task, and the correct response is to reduce the fan-out rather than to add coordination.

The forbidden-path list is as important as the owned-path list, and it is the half that gets skipped. A Builder told what it owns will still edit an adjacent file it can see is wrong. A Builder told what it must not touch reports the problem instead.

### 7.2 Per-phase decomposition

Paths follow `implementation/00_IMPLEMENTATION_MAP.md` §5, which is the layout authority. `ARCHITECTURE.md` §25 is superseded and must not be used.

> **Task ids in this table are not authoritative — `70_TASK_PLAN.md` is.** This table and the task plan were authored in parallel and initially numbered tasks differently (this table gave Phase 0 five tasks; the plan gives it seven, with `T0.2` = licence rather than gate tooling). **Read the parallelism *shape* from this table — how many Builders, which paths are disjoint, what must serialise and why — and read every task id, title and acceptance criterion from `70_TASK_PLAN.md`.** An Integrator who dispatches by id from this table sends the wrong brief. Where a row below still carries an id that `70_` does not define, the row's *shape* claim stands and its id does not.

| Phase | Parallel Builders | Task split by owned path | Serialised, and why |
|---|---|---|---|
| 0 | 1 then 4 lanes | T0.1 skeleton + `Makefile` + `pyproject.toml` + `.importlinter` (**first, alone**); then four concurrent lanes — T0.2 licence and repository posture │ T0.3 gate tooling (`tools/gate.sh`, `tools/scrub.py`, `ops/gate-env.sh`) │ T0.4 typed settings │ T0.5 cluster provisioning ─► T0.6 capability probes │ T0.7 CI joins T0.1 + T0.3 | T0.1 creates every shared file. Nothing else can start until it lands. T0.5 ─► T0.6 is strictly serial: the probes need the `provenance` database and the SQL users that T0.5 creates. |
| 1 | 4 | T1.1 `provenance_contracts/` models │ T1.2 `provenance_domain/` enums + case and trigger state machines │ T1.3 `provenance_domain/` money + invariant functions │ T1.4 `INVARIANTS.md` + `tools/invariant_map_check.py` + `tools/contract_lint.py` | `provenance_domain/__init__.py` exports: Integrator, at reconcile. |
| 2 | 1 + 4 | T2.1 `db/migrations/0001`–`0008` (**alone**) │ T2.2 `db/verify.sql` │ T2.3 `scripts/seed/ids.py` + `decoys.py` │ T2.4 `scripts/seed/embeddings.py` + the `db/seeds/vectors.parquet` cache │ T2.5 `tools/manifest_check.py` | Alembic is a linear chain: two Builders both set `down_revision` and produce a fork that `alembic upgrade head` rejects. Also: `IMPORT INTO` is unsupported on vector-indexed tables, so the seed must bulk-load `evidence_items` **before** the vector index is created — that ordering lives in one Builder's head or in nobody's. |
| 3 | 4 | T3.1 pools per role │ T3.2 `provenance_db/retry.py` │ T3.3 repositories │ T3.4 `tools/txn_purity_lint.py` | — |
| 4 | 5 then **1** | T4.1–T4.5 the pure decision functions, one file each: `provenance_domain/kernel/{authority,disposition,money,case_machine,propositions}.py`; then T4.0 `services/control_plane/app/memory_kernel/` — **single Builder, single careful pass** | See §8. The transaction statement order is the phase's entire correctness content and it is invisible in any single-task diff. |
| 5 | 5 | dashboard │ case projection + timeline │ State Proof │ conflict view │ memory-trace query — one module and one test file each | — |
| 6 | 3 + 1 | T6.1 Titan client + embedding cache │ T6.2 `repositories/evidence.ann_search()` + predicates (**alone**) │ T6.3 rerank + `RetrievalContext` assembly │ T6.4 `evals/retrieval/` harness | T6.2 is the isolation boundary. One function, one author, no split. |
| 7 | 4 | ingestion graph │ advocate graph │ model router │ prompts + schemas + cassettes | — |
| 8 | 5 | auth + route-class + capability │ idempotency + error envelope + pagination │ case/State Proof/dashboard routes │ internal proposal + ingest routes │ `tools/spec_lint.py` | Router registration file: Integrator. |
| 9 | 2 + 1 | T9.1 draft support-id validation │ T9.2 approval binding (`basis_case_revision` + `approval_draft_sha256`) and T9.3 executor revalidation — **same Builder**, they are two halves of one staleness contract | Splitting approval from revalidation puts invariant 4 across a seam. |
| 10 | 4 | outbox sweeper │ EventBridge rules + `processed_events` dedupe │ scheduler + trigger evaluator │ DLQ + replay tool | — |
| 11 | 3 | MCP server wiring + config │ tool-call recording onto `agent_runs.tool_calls` │ grant probe + revoke/restore harness | Migration `0008` grants: Integrator. |
| 12 | 6 | dashboard + case detail │ State Proof screen │ approval flow │ Judge Mode panels + trace DAG │ counterfactual UI │ `e2e/` specs — disjoint route directories | Design tokens, layout shell, and the router: Integrator. |
| 13 | **1** (+ workers) | `infra/cdk/` as one task; `workers/{ses_ingest,textract_complete,outbox_dispatch,trigger_wakeup}/` may be four parallel tasks | A CDK app is one synth graph. Parallel edits produce cross-stack reference churn that `cdk diff` reports as real drift. |
| 14 | 4 | `evals/datasets/` + runner │ adversarial corpus │ concurrency harness │ sabotage matrix completion + `fixture_guard` | `evals/thresholds.yaml`: Integrator. |
| 15 | **1** | Documents only. Integrator-owned. | — |

### 7.3 When a worktree is warranted, and when it is not

Naturally disjoint tasks do not need worktrees. One working tree, N Builders on N branches, disjoint paths, is simpler, and on this Windows machine it avoids paying for N Python virtual environments and N `npm install` runs.

A `git worktree` is warranted in exactly four situations:

1. **A Hunter needs to break things.** The mutation probe and the Vacuity lens both work by neutering a symbol and re-running. That must not happen in a tree a Builder is using.
2. **Two tasks genuinely must touch one file** and cannot be re-decomposed. Rare, and usually a sign the decomposition is wrong. Rarest legitimate case: a large mechanical rename that must land atomically.
3. **The Verifier's clean clone.** Not strictly a worktree — §22.2 step 1 requires `git clone`, and a worktree shares the object store and the config with the origin working tree, which weakens the "clean clone" claim. Use a real clone into an empty directory.
4. **A long-running task blocks a short urgent one.** A phase-13 deployment in progress and a phase-12 hotfix, for example.

Commands, for this repository on this machine:

```bash
# Git Bash. A hunter worktree, throwaway, at the commit under attack.
git worktree add ../pv-hunt-06-isolation {{COMMIT_SHA}}
cd ../pv-hunt-06-isolation && python -m venv .venv && source .venv/Scripts/activate
pip install -e ".[dev]"
# ... hunt ...
cd - && git worktree remove ../pv-hunt-06-isolation --force
```

Two Windows notes that will otherwise cost an hour each. First, each worktree needs its own virtual environment; a shared `.venv` resolves editable installs back to the origin tree and every hunt then silently tests the wrong code. Second, the gate batteries and `tools/gate.sh` are POSIX shell and must be run from Git Bash, not PowerShell — `make`, `$(...)`, `tee`, and `grep -c` in the assertion text all assume it.

### 7.4 Merge and integration order

Fixed order. Deviating from it is how a phase loses a morning.

1. **Shared and foundational first.** Integrator-owned files (`Makefile`, `pyproject.toml`, `.importlinter`, migration chain pointers, `tests/sabotage_matrix.yaml`, `db/expected_tables.txt`, `db/seeds/MANIFEST.json`, `INVARIANTS.md`) land before any task branch. Every Builder then rebases onto that tip.
2. **Task branches in dependency order**, not in completion order. In phase 4 the pure decision functions land before the kernel pipeline that composes them, even if the pipeline finished first.
3. **Run the scripted verify on the merged tip** (loop step 3) before dispatching any Hunter. Hunting a broken merge wastes six agents at once.
4. **Fix branches last**, onto the reconciled tip, one at a time when they share a file, in severity order.
5. **The Verifier clones the merged tip.** Never a branch, never a working tree, never a tag that was moved.
6. **Every merge is a real commit on the phase branch.** No squash across tasks: the gate report cites a commit, the hunt reports cite a commit, and `tools/fixture_guard.py` reads commit boundaries to detect a fixture regenerated alongside the code it validates. Squashing four tasks into one commit merges a fixture change and a source change into a single commit and either trips the guard or, worse, hides the pair inside a legitimate-looking bulk commit.

### 7.5 The conflict that the file rule does not prevent

Disjoint files do not mean disjoint semantics. Two Builders can produce a clean merge and a broken system:

- Both add an enum member to *different* closed enums that must stay aligned (`specs/11_CONTRACTS.md` owns membership; DDL checks, prompt schemas, APIs, fixtures and UI filters mirror it exactly).
- Both add a `state_transitions` row type for the same event, in different modules.
- One changes the shape of a `provenance_contracts` model; the other consumed the old shape in a file it owns.

The mitigation is not a process step, it is a lens: **Contract drift is mandatory in every phase where more than three Builders ran in parallel**, and its first attack pattern is a diff of every closed enum against `specs/11_CONTRACTS.md`.

---

## 8. When to stop using agents

Fan-out has a cost, and the cost is not the dispatch. It is that a defect introduced by a parallel edit is detected later and diagnosed harder than one introduced by a single careful pass. Do a single pass whenever the expected detection latency of a wrong edit is long, or the diagnosis is expensive, or the correctness lives in an ordering that no individual diff shows.

**Always a single careful pass:**

1. **The Memory Kernel transaction ordering.** `specs/10_DATABASE_DDL.md` §13 fixes the statement order inside the kernel transaction, and `specs/12_KERNEL_ALGORITHMS.md` §1.2 steps 17–28 spell it out: claim → belief version and grounding edges → conflict → case status and `revision + 1` → `state_transitions` → `kernel_decisions` → proposal status → `outbox_events` → `COMMIT`. That order is the phase's correctness. It is not visible in any single-task diff, it is not expressible as a per-file acceptance criterion, and a Builder holding only "write the conflict rows" cannot tell whether it is in the right place. Worse, the wrong order frequently *passes*: `SERIALIZABLE` and the foreign keys absorb many reorderings, and the failure appears only under contention, in the concurrency harness, at phase 14, with no obvious pointer back to phase 4. One agent, one pass, the whole pipeline in one context.
2. **The PHASE A / PHASE B split.** §1.1 requires steps 4–16 to execute twice, once advisory and once inside the transaction against rows read there. It is a single design idea distributed across thirteen steps. Split across Builders, each half looks correct.
3. **The Alembic chain.** Linear `down_revision` pointers. Two authors, one fork, and `alembic downgrade base && alembic upgrade head` (`G2.1`) fails in a way that reads as a tooling problem.
4. **The seed's identifier derivation.** `scripts/seed/ids.py` and the `sid()` helper produce deterministic ids that every fixture, every expected-output file, and `db/seeds/MANIFEST.json` depend on. A parallel edit that changes the derivation changes 18,035 rows' identity and every golden comparison at once, and the diff that caused it is three lines.
5. **Closed-enum membership.** One author per enum change, propagated in one commit across `specs/11_CONTRACTS.md`'s consumers. Canon forbids layer-local aliases specifically because the drift is silent.
6. **The CDK stack graph.** One synth graph, cross-stack references, and `G13.1` requires `cdk diff --all` to report no differences.
7. **Anything after phase 13 that touches the schema.** Migrations are forward-only from there, and `G13.9` requires the previous image to run against the head schema. A speculative parallel migration is not revertible.
8. **The vector index decision.** `ops/decisions/VECTOR_INDEX_VARIANT.md` records one variant chosen from probe output. This is a decision, not a task, and it is the likeliest Phase 0 probe to fail outright: vector indexes need `SET CLUSTER SETTING feature.vector_index.enabled = true`, and on a managed BASIC cluster that privilege may be restricted. The fallback is predetermined and disclosed; what must not happen is two agents independently concluding two different things.
9. **Anything re-embedding the corpus.** Re-embedding 18,035 rows costs Bedrock spend and tens of minutes, and `quality/23_PHASE_GATES.md` §25 risk 6 is explicit: populate `db/seeds/vectors.parquet` at first seed, not later. A parallel agent that "just re-seeds to check something" pays that cost twice and can exhaust a budget quietly.
10. **Gate signing.** One Verifier. A gate signed by two agents is a gate signed by neither.

**Also stop when the brief costs more than the work.** Writing a Builder brief — owned paths, forbidden paths, acceptance criteria, spec list — takes ten to fifteen minutes of real thought. For a change under roughly thirty lines in a file the Integrator already understands, do it directly and dispatch a Hunter afterwards if it touches an invariant. The Bug Hunt is worth running on small changes; the Builder fan-out usually is not.

**Do not fan out to compensate for an unclear spec.** If three Builders each need to ask what a field means, the problem is upstream, and three agents will produce three different guesses that all typecheck. Resolve it against canon first, in one place.

---

## 9. Artifacts, ledgers, and where evidence lands

```text
ops/
├── gates/
│   ├── PHASE_00.md .. PHASE_15.md     # Verifier output, §4.1 template, one per phase
│   └── logs/                          # scrubbed battery output, <ID>.<sha8>.log
├── defects/
│   └── DEFECTS.md                     # every finding, its disposition, its fix
├── decisions/
│   └── VECTOR_INDEX_VARIANT.md        # and any other one-way decision
└── agents/                            # PROPOSED ADDITION — see §11
    ├── PHASE_00_DISPATCH.md .. PHASE_15_DISPATCH.md
    ├── tasks/   T<N>.<k>.md           # Builder reports
    └── hunts/   H<N>.<lens>.md        # Hunter reports
```

`ops/gates/`, `ops/defects/`, and `ops/decisions/` are already in the layout canon. `ops/agents/` is an addition this document proposes; it is listed in §11 as an open item because `implementation/00_IMPLEMENTATION_MAP.md` §5 owns the tree.

**Defect record fields**, in `ops/defects/DEFECTS.md`:

| Field | Content |
|---|---|
| `id` | `D-<phase>-<seq>`, for example `D-04-003` |
| `found_by` | role and lens, for example `Hunter / Concurrency` |
| `found_at_commit` | full sha |
| `severity` | `BLOCKS_GATE`, `CORRECTNESS`, `ROBUSTNESS`, `HYGIENE` |
| `invariant_or_clause` | invariant 1–4, grounding, or a named spec clause |
| `reproduction` | verbatim, runnable from a clean clone |
| `disposition` | `CONFIRMED`, `NOT_A_DEFECT` (+ the clause that makes it correct), `ACCEPTED_DEBT` (+ closing phase) |
| `fixed_by` | branch and commit |
| `regression_test` | the test path and selection |
| `sabotage_entry` | the symbol added to `tests/sabotage_matrix.yaml`, or why none applies |

Everything under `ops/` is committed and `gitleaks`-scanned, per canon. That includes hunt reports, which will contain database output — the same scrubbing rule applies (`tools/scrub.py`, then `gitleaks detect --source ops`), and a hunt report that leaks a connection URL rotates the credential first and gets rewritten second.

---

## 10. Honest limitations

**1. Fresh context is a weaker guarantee than a different person, and this document does not fix that.** `quality/23_PHASE_GATES.md` §25 risk 4 says it first and says it correctly. Everything here is a rearrangement of what one model family knows, prompted differently. Two agents built from the same specs share a prior. If the specs contain an error, no arrangement of context isolation finds it, because every role derives the same wrong thing from the same wrong sentence. The lens most likely to catch a spec error is Contract drift, and only because it compares documents against each other rather than against code.

**2. An agent reviewing work built from the same specs re-derives the same blind spots.** Concretely: if `specs/12_KERNEL_ALGORITHMS.md` §3.3's disposition function has a gap, the Builder implements the gap, the Invariant hunter reads the same function and finds the implementation faithful, and the Verifier's battery asserts the behaviour the gap produces. All three agree. All three are wrong. The only defence that does not depend on that shared prior is the mutation probe, and it is a narrow one.

**3. The mutation and sabotage probes are the only review steps that do not depend on reviewer judgement.** The code either notices the sabotage or it does not; there is no interpretation step. Everything else — the lenses, the standing questions, the triage, the guardrail diff read — is judgement, and judgement is exactly the thing a shared prior compromises. **If a time crunch forces the review down to one step, make it the sabotage matrix plus the phase's mutation probe.** That is `quality/23_PHASE_GATES.md` §25 risk 4's conclusion and this document does not improve on it. The second thing to keep is the Vacuity lens, for the same reason: its findings are demonstrated by deleting a line and re-running, not argued.

**4. Six lenses across sixteen phases is roughly eighty hunter runs, and that will not happen.** §4.7's table is a priority order, not a schedule. Under real pressure the honest reduction is: phases 4, 9 and 11 keep their full lens set; every other phase keeps Vacuity plus one lens chosen by what the phase touches; phases 0, 3, 5 and 7 may drop the hunt entirely and rely on the scripted apparatus. Write down which hunts were skipped, in the gate report's carried-debt section. A skipped hunt that is recorded is a known gap; a skipped hunt that is not recorded is an unknown one, and this whole document exists to prefer the first.

**5. Hunters generate false positives, and the triage cost is real.** Expect roughly a third of findings to survive triage in the early phases and rather less later, when the obvious classes are exhausted. The cost is the Integrator's attention, which is also the scarcest resource in the build. A hunt dispatched without budget for triage is worse than no hunt: it produces a backlog that gets skimmed, and skimming a defect list is how a real finding gets dispositioned `NOT_A_DEFECT` without a citation.

**6. The Integrator has no adversary.** Every other role is checked by a role denied its context. The Integrator sees everything, decides everything, and is reviewed by nobody. If the Integrator's model of the system is wrong, the decomposition is wrong, the triage is wrong, and the carried debt is wrong, in a mutually consistent way that no gate detects. The only real controls are that the ledgers are committed and timestamped, and that `quality/23_PHASE_GATES.md` §24 runs the pre-submission battery twice — once 24 hours out and once within 2 hours of cutting the release — with the second run's job being to prove nothing rotted.

**7. Context isolation is a discipline, not a mechanism.** Nothing enforces it. An agent asked not to read the gate document can read it; a Verifier's clean clone contains every commit message anyone wrote. Repository residue is the leak that will actually happen, and §6.2 R-I2 puts a check for it in the Integrator's phase-7 job precisely because it is the one that requires no bad intent.

**8. This document cannot make the build honest.** `quality/23_PHASE_GATES.md` §25 risk 12 makes this point about the gates and it applies verbatim to the process: every mechanism here is bypassable by anyone willing to dispatch a Hunter and ignore its report, or to write "no confirmed defect" without searching. It is designed for a team that wants to know the truth and needs a structure to make finding it routine.

**9. Nothing here has been run.** No agent has been dispatched, no defect has been found, no gate has been signed, and no line of Provenance exists. The role definitions, the loop, the lenses and the briefs are designed artifacts, not measured ones. The first phase to use them should expect the decomposition step to take longer than budgeted and the triage step to take about twice as long as expected.

---

## 11. Risks and open questions

**R1 — The Bug Hunt is the step that gets cut, and it is the step with no gate.** Every other item in this document is either mandated by `quality/23_PHASE_GATES.md` or is cheap. The hunt costs four to six agent runs plus triage, produces no artifact any gate asserts on, and is therefore first in line when the schedule slips. *Mitigation:* the gate report's carried-debt section must name every skipped hunt by phase and lens, which converts a silent omission into a recorded one. *Residual risk:* high, and accepted. The counter-argument to running it at all is that the pack's pre-scripted apparatus is unusually strong; the counter-counter-argument is §1.1's ceiling, which no amount of strength in a closed set removes.

**R2 — Six lenses is a guess.** The lens set was derived from the shape of this system — an invariant-heavy transactional core, a hard multi-tenant boundary, a retry-and-outbox concurrency story, a demo whose credibility is the product, a large frozen contract surface, and a pack that already worries about vacuity. It was not derived from observed defect distributions in agent-built systems, because none were available. If defects cluster somewhere else — resource lifecycle, error-path coverage, cost, cold-start behaviour — the lens set is thin exactly there. `quality/23_PHASE_GATES.md` §25 risk 9 makes the same concession about the hostile-judge model, and for the same reason.

**R3 — `ops/agents/` is not in the layout canon.** `implementation/00_IMPLEMENTATION_MAP.md` §5 is the layout authority, and canon lists `ops/` as holding probes, decisions, gate ledgers, logs, and `ops/defects/DEFECTS.md`. This document proposes `ops/agents/` with `tasks/` and `hunts/` subdirectories. **Open question:** amend §5 to include it, or place the dispatch ledgers under `ops/gates/` as an appendix per phase. Until that is resolved, the paths in §9 are a proposal, not canon. The change-control rule in `README.md` requires the amendment to land in one documentation change alongside the register.

**R4 — Placement resolved; one relationship still worth watching.** *Resolved 2026-08-17:* `70_TASK_PLAN.md` exists and is the task authority; `72_DEFECT_PROTOCOL.md` is the lens-id and severity authority; all four `EXECUTION/` documents plus `frontend/33_DESIGN_PROTOTYPE_PROMPT.md` are now entries 25–28 in `docs/README.md` under an *Execution layer* heading, and this document's subordinate, process-only authority position is recorded there. **Still worth watching:** `EXECUTION/` **complements** rather than supersedes the root `EXECUTION_PLAN.md` — that document owns workstream ownership boundaries and the phase-level integration milestones, while `70_` owns tasks. Two documents describing sequencing at different altitudes is workable, but if they ever disagree about *order* rather than *granularity*, `EXECUTION_PLAN.md` is the older artifact and `70_` should win. That precedence is not yet written into `README.md`'s authority rules.

**R5 — The Verifier's two-message protocol depends on the harness honouring it.** §5.3 and §5.4 assume the Builder's report can be withheld until after the battery run. In a session where the Verifier can read the repository freely and the Builder's report is committed at `ops/agents/tasks/`, the withholding is nominal: nothing stops the agent from opening the file. *Mitigation:* hold task reports outside the tree until the Verifier's message 1 lands, then commit them. That costs a commit-ordering discipline and it is the honest fix; the alternative is to accept that message 1 is a request rather than a constraint.

**R6 — The lens briefs restate gate principles in different words, and paraphrase drifts.** §6.2 R-I1 keeps the Hunter away from `quality/23_PHASE_GATES.md` by restating what it needs. Every restatement is a fork of the original, and a fork that is not maintained becomes wrong. *Mitigation:* the lens blocks in §4 cite the clause they paraphrase, so a reader can check the fork against the source. *Residual risk:* moderate; this document is the fork, and it will need re-reading whenever §23 or §22 of the gate document changes.

**R7 — Parallel Builders against one CockroachDB Cloud BASIC cluster.** A single cluster `<cluster>` (`<cluster-id>`, BASIC, AWS `us-east-1`) is the only one available, and `quality/23_PHASE_GATES.md` §25 risk 6 already flags that gates share the cluster with the demo, mitigated by a separate `provenance_ci` database. Four to six concurrent agents plus a Hunter running sabotage multiplies that: request-unit consumption, connection count, and the chance that one agent's destructive run lands on the wrong `PV_DB_*` URL. *Mitigation:* one logical database per concurrent agent where the phase involves destructive work, named `provenance_ci_<task-id>`, dropped at task close; read-only credentials for every Hunter. *Open question:* whether a BASIC cluster tolerates that many logical databases and that concurrency at all. This is a Phase 0 probe item and it is not currently one of the eleven.

**R8 — Hunter reports are committed and may contain sensitive output.** Everything under `ops/` is committed and `gitleaks`-scanned, and hunt reports by construction contain database rows, connection behaviour, and error text. A hunt into the Isolation lens will deliberately produce cross-user query output. *Mitigation:* the same `tools/scrub.py` and `gitleaks` path as gate logs, applied before commit. *Residual risk:* the seeded corpus is synthetic — 18,000 decoys are generated, 32 hero rows are curated — so the exposure is credentials, not personal data. That is a real distinction and it is why the scrubber matters more than redaction of row content.

**R9 — Defect-fix churn can invalidate a hunt that already ran.** Six hunters run against commit `X`; three fixes land; the code at the gate commit is not the code that was hunted. *Mitigation:* loop step 6 re-runs the scripted verify in full after fixes, and the Verifier's regression sweep covers the previous gate. *What is not mitigated:* a fix that introduces a defect in a class some lens would have caught, in a phase where that lens has already run. Re-hunting after fixes is correct and will not fit the schedule; the honest posture is to re-dispatch only the lens whose class the fix touched, and to record that decision.

**R10 — The `tools/gate.sh` exit-code hazard in §4.6 is a prediction, not an observation.** The script does not exist yet. It is described in `quality/23_PHASE_GATES.md` §2.2 as teeing output to a log and recording the exit code in the header, which is the shape that produces the bug in POSIX shells. If the implementation uses `set -o pipefail` or `${PIPESTATUS[0]}` from the start, the defect never exists and the example in §4.6 is a false alarm. It is retained because the cost of checking is one command and the cost of not checking is that every gate log in the repository is untrustworthy.
