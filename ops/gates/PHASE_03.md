# Gate G-3 — database runtime and retry

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

## Exit assertions

Battery: `make gate-3`. Entry criterion: `G-2` signed.

| ID | Result | Log |
|---|---|---|
| G3.1 | NOT RUN — phase not started | — |
| G3.2 | NOT RUN — phase not started | — |
| G3.3 | NOT RUN — phase not started | — |
| G3.4 | NOT RUN — phase not started | — |
| G3.5 | NOT RUN — phase not started | — |
| G3.6 | NOT RUN — phase not started | — |

What each one asserts (§9):

- **G3.1** — the pools really are separate roles: `current_user` is `pv_app_reader_writer`, `pv_kernel_writer`, `pv_agent_reader` respectively. `3 passed`.
- **G3.2** — an injected serialization failure is retried and the final state is correct; `retry_count=2` printed. The `40001` is forced by **two overlapping transactions on one row**, not by monkeypatching the driver — a monkeypatched `40001` proves nothing about CockroachDB and is rejected at review.
- **G3.3** — the retry budget is bounded and exhaustion raises, carrying `attempts=5`; it is never a silent NOOP.
- **G3.4** — rollback leaves no partial writes; row counts before == after, read over a **second** connection.
- **G3.5** — `txn_purity_lint`: `scanned NN transaction callbacks, 0 network constructs found`.
- **G3.6** — sabotage `provenance_db.retry.is_retryable`: G3.2 goes red, `exit=1`.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§9): `tests/db/test_pool_identity.py`, `tests/db/test_retry.py`
(4 tests), plus phase 2's suite re-run. Canon paths (`70_TASK_PLAN.md` §2.4):
`packages/python/provenance_db/tests/db/test_pool_and_roles.py`,
`packages/python/provenance_db/tests/db/test_retry.py`,
`packages/python/provenance_db/tests/unit/test_retry_semantics.py`.

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `provenance_db.retry.is_retryable` | `packages/python/provenance_db/tests/db/test_retry.py` | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=3`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-TIME` — 4.

§23.9 applies from here on: `retry_count` must be **0** on every single-writer path and
`>= 1` on the contended one. A nonzero retry count on a single-writer test is a MAJOR(M1),
not a curiosity.

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real?
- **Q3** Which invariant is currently unproven?
- **Q4** What would a hostile judge click on first?
- **Q5** What passed because of seeded state rather than logic?
- **Q6** What did I not look at?
- **Q7** If this phase is secretly broken, how and when would I find out?

## Carried debt

```
<make debt output — `0 carried items` if empty, and that line must still appear>
```

## Rollback position at time of signing

`<the exact command that returns the system to the last known-good state>`

Documented position (§9): roll back to the `G-2` commit; revert
`packages/python/provenance_db`. The database is untouched by this phase.
**Cannot be undone:** nothing.
