# Gate G-14 — evals, adversarial, concurrency

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
- Deployed target (if any): `<url>`, git sha `<sha>` as reported by `GET /v1/version`
- Full-suite wall-clock time from a clean clone: `<duration>`

## Exit assertions

Battery: `make gate-14`. Entry criteria: `G-7` signed; `G-13` preferably signed so evals
can run against the deployed stack.

| ID | Result | Log |
|---|---|---|
| G14.1 | NOT RUN — phase not started | — |
| G14.2 | NOT RUN — phase not started | — |
| G14.3 | NOT RUN — phase not started | — |
| G14.4 | NOT RUN — phase not started | — |
| G14.5 | NOT RUN — phase not started | — |
| G14.6 | NOT RUN — phase not started | — |
| G14.7 | NOT RUN — phase not started | — |

What each one asserts (§20):

- **G14.1** — the corpus is large enough and covers every category: `scenarios: 51`, `identity 9 | temporal 8 | contradiction 10 | commitments 9 | prospective 7 | safety 8`, `categories with zero scenarios: none`. 51 is the canon figure (`CANONICAL_DECISIONS.md` → *Test and corpus counts*); the two `20_TDD_STRATEGY.md` occurrences of 62 are corrected.
- **G14.2** — the full suite with thresholds **asserted**, not merely reported, and `INVARIANT VIOLATIONS: 0`. Non-zero there fails the gate outright.
- **G14.3** — adversarial: `capability escalations: 0 | canonical writes caused: 0 | action intents created: 0 | evidence preserved: 24/24`.
- **G14.4** — the concurrency test is not flaky: `25 passed`, at least one run observing `retry_count >= 1`. **24/25 is a FAILURE** — a race that fails 4% of the time will fail during the video.
- **G14.5** — fixtures were not regenerated to match the code that was supposed to satisfy them: `commits touching both evals/datasets|tests/fixtures and services|packages|agents: 0`.
- **G14.6** — the eval harness itself is not vacuous: `sabotages: 18 | detected: 18 | UNDETECTED: 0`. Any UNDETECTED entry names a test that asserts nothing. **Fix the test, not the matrix.**
- **G14.7** — no mocks in the end-to-end suite: `PV_FORBID_MOCKS=1` makes the e2e conftest raise on any `unittest.mock` import.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§20): **everything**. This is the phase where the entire suite runs
together, in one command, from a clean clone, and the summary line goes into this report
verbatim. Full-suite total: **626** across the eight layers (`CANONICAL_DECISIONS.md`).

`<exact selection and its summary line>`

## Sabotage probes run

The full matrix, one row per entry. `make sabotage` prints the count and this table records it.

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| — | — | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`). Target at G14.6: **18**.
`72_DEFECT_PROTOCOL.md` §10.2 detector 1: the matrix is append-only —
`python -m tools.sabotage_guard --base "$(git merge-base HEAD main)"` fails on any removed
or renamed `symbol:` key. A removal is accepted only when the symbol is also absent from
the tree (`--assert-symbol-gone`) and the commit carries a `Sabotage-Entry-Removal:` trailer.

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=14`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): **all six**.

**Nothing carries past this gate (§9.4).** At `G-14` the debt ledger must contain no item
whose `Closes by` is `G-15` or earlier and whose status is not `CLOSED`. Every remaining
item takes one of exactly two paths: it closes before `G-15` opens, or it is converted to
a **disclosed limitation** — deleted from `CARRIED_DEBT.md` and written in plain words into
`README.md`, which `S7` greps for. Run `make debt --assert-empty`
and paste the result.

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real? — a substitute with no closing phase is permanent and must be disclosed as such in `README.md`.
- **Q3** Which invariant is currently unproven?
- **Q4** What would a hostile judge click on first?
- **Q5** What passed because of seeded state rather than logic?
- **Q6** What did I not look at?
- **Q7** If this phase is secretly broken, how and when would I find out?

## Carried debt

```
<make debt --assert-empty output>
```

## Rollback position at time of signing

`<the exact command that returns the system to the last known-good state>`

Documented position (§20): roll back to the `G-13` commit. Evals are not deployed and
there is nothing to undo. **Cannot be undone:** nothing — but a failing `G14.2` threshold
**blocks** `G-15`. Lowering a threshold to pass is a §23.4 violation and requires a written
justification naming who approved it and why the new number is still honest.
