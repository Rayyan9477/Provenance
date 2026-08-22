# Gate G-12 — frontend, Judge Mode, counterfactual

> **Status: NOT RUN — phase not started.** Instantiated from
> `docs/quality/23_PHASE_GATES.md` §4.1 by task `T0.3`. Every assertion row is
> pre-filled, so an assertion that is never run shows up as an **omission**
> rather than as an absence (§3 rule 2).

Commit: `<full sha>`            Branch: `<name>`
Builder: `<name>`               Reviewer: `<name, must differ from builder>`
Round opened: `<ISO8601>`       Round closed: `<ISO8601>`
Verdict: `SIGNED | REJECTED | SIGNED WITH CARRIED DEBT`

## Environment of record

- Checkout: `fresh clone into <path> at <sha>` | `reused working tree (state why)`
- Database: `<cluster name>`, database `<provenance|provenance_ci>`, CockroachDB `<version>`
- `PV_WEB` / `PV_API`: `<urls>` — state whether these are local or deployed (§23.11)

## Exit assertions

Battery: `make gate-12`. Entry criteria: `G-9` and `G-11` signed.

| ID | Result | Log |
|---|---|---|
| G12.1 | NOT RUN — phase not started | — |
| G12.2 | NOT RUN — phase not started | — |
| G12.3 | NOT RUN — phase not started | — |
| G12.4 | NOT RUN — phase not started | — |
| G12.5 | NOT RUN — phase not started | — |
| G12.6 | NOT RUN — phase not started | — |
| G12.7 | NOT RUN — phase not started | — |

What each one asserts (§18):

- **G12.1** — the hero flow end to end in a browser with **zero** console errors: dashboard shows 4 relationships and 2 overdue; upload the June invoice; the case moves RESOLVED → REOPENED; revision text 12 → 13; State Proof lists the 15 May confirmation; approve; the executor sends; the timeline shows the outcome.
- **G12.2** — the trace is not an animation: every rendered `data-node-id` exists in the intercepted API payload, and `|DOM| >= 8`.
- **G12.3** — zero UUID literals in `apps/web/src`. A hard-coded id is a rendered lie.
- **G12.4** — the mutation probe: commit a real correction through the API (revision 13 → 14), reload, assert the UI changed. A UI still reading 13 is rendering a snapshot, not the system.
- **G12.5** — the counterfactual actually differs **and changes nothing**: `memory_off.summary` contains `$186` and does **not** contain `15 May` / `terminat` / `reopen`; `memory_on.summary` contains `15 May` **and** `reopened`; `safety.case_revision_changed_by_counterfactual == false`; `cases.revision` identical before and after.
- **G12.6** — no raw chain-of-thought reaches the browser: 0 hits across every network response body.
- **G12.7** — the fixture-mode banner cannot be suppressed: persistent, non-dismissible, reading `DEMO FIXTURE MODE — model outputs are replayed`.

> **Counterfactual parity is a render gate, not a badge.** `CANONICAL_DECISIONS.md` →
> *Counterfactual parity canon*: `parity.all_equal = false` means the two output columns
> are **not rendered** and a failure banner replaces them. Rendering them anyway is a
> BLOCKER at rule B4 (`72_DEFECT_PROTOCOL.md` §4.4, B-ex-5) — the side-by-side is a claim
> about identity of inputs, and this is the single most persuasive asset in the build.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§18): `apps/web` unit tests and `e2e/` in full. Canon path
(`70_TASK_PLAN.md` §2.4): `tests/e2e/*.spec.ts`.

`<exact npm / playwright selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| — | — | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=12`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): **all six** — `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`,
`L-RENDER`, `L-TIME`.

Debt closing here: the MINOR items whose `Closes by` is `G-12` (`72_` §4.6, m-ex-1, m-ex-2,
m-ex-4). Each is either CLOSED with a commit or ESCALATED to MAJOR. An item marked
`STILL ACCEPTED` at three consecutive gates escalates automatically (§9.3).

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real?
- **Q3** Which invariant is currently unproven?
- **Q4** What would a hostile judge click on first? — name three and state exactly what happens for each. §23.3 says the Memory Trace is the single thing a hostile judge is most likely to test.
- **Q5** What passed because of seeded state rather than logic?
- **Q6** What did I not look at?
- **Q7** If this phase is secretly broken, how and when would I find out?

## Carried debt

```
<make debt output — `0 carried items` if empty, and that line must still appear>
```

## Rollback position at time of signing

`<the exact command that returns the system to the last known-good state>`

Documented position (§18): roll back to the `G-11` commit; Amplify "promote previous
deployment". Nothing persistent is undone except counterfactual `agent_runs` rows, which
are excluded from case timelines by construction and are harmless.
**Cannot be undone:** nothing.
