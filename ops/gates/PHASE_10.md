# Gate G-10 — events, outbox, scheduler

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
- EventBridge bus / SQS DLQ: `<names>`; frozen clocks used: `<instants>`

## Exit assertions

Battery: `make gate-10`. Entry criteria: `G-8` signed; `16_TRIGGER_DSL.md` read.

| ID | Result | Log |
|---|---|---|
| G10.1 | NOT RUN — phase not started | — |
| G10.2 | NOT RUN — phase not started | — |
| G10.3 | NOT RUN — phase not started | — |
| G10.4 | NOT RUN — phase not started | — |
| G10.5 | NOT RUN — phase not started | — |
| G10.6 | NOT RUN — phase not started | — |
| G10.7 | NOT RUN — phase not started | — |

What each one asserts (§16):

- **G10.1** — DB test 9: duplicate delivery is a no-op. Second insert on `(event_id, consumer_name)` → duplicate key; the consumer returns NOOP; the downstream side-effect count stays 1; a Kernel retry after an injected `40001` cannot double-insert `(aggregate_id, aggregate_version, event_type)`.
- **G10.2** — DB test 8: a trigger waking after resolution is a no-op **with a reason**: `last_result=DISARMED`, `last_reason_code=CASE_RESOLVED`, `state=DISARMED`, `fired_at IS NULL`, `cases.revision` unchanged, only `trigger.noop.v1` emitted. An unexplained NOOP is a gate failure — the reason code is asserted, not merely the absence of an error.
- **G10.3** — the landlord deposit trigger fires on its own, on real state: predicate field values printed, `trigger.fired.v1` emitted, attention created, `cases.revision` incremented exactly once. The user set no reminder; this is prospective memory and the assertion is on rows.
- **G10.4** — the outbox retries, then DEADs, then replays, and the consumer still produces exactly one effect.
- **G10.5** — a poisoned event lands in the DLQ rather than blocking the queue: DLQ depth 0 before, >= 1 after.
- **G10.6** — identical pass/fail results at two frozen clocks (§23.13).
- **G10.7** — sabotage `triggers.evaluator.reevaluate_predicate`: G10.2 goes red, `exit=1`.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§16): `tests/events/`, `tests/triggers/`, DDL §19 tests 8 and 9.
Canon paths (`70_TASK_PLAN.md` §2.4):
`services/control_plane/tests/db/test_outbox_and_events.py`,
`services/control_plane/tests/db/test_triggers.py`,
`services/control_plane/tests/unit/test_predicate_evaluator.py`,
`services/control_plane/tests/unit/test_predicate_parser.py`.

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `triggers.evaluator.reevaluate_predicate` | DDL §19 test 8 | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=10`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`, `L-TIME` — 5.

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real? — the compressed clock and the simulated EventBridge invocation both belong here. **State the asymmetry honestly (§23.13):** EventBridge Scheduler runs on AWS wall time and cannot be frozen, so the deployed trigger is exercised by setting `not_before` into the past. That tests the evaluator and not the scheduler's own timing. That gap is real.
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

Documented position (§16): roll back to the `G-8` commit; `aws events disable-rule` on
the Provenance rules and stop the sweeper. Outbox rows accumulate harmlessly in `PENDING`
and the sweeper is idempotent, so re-enabling drains them without duplication.
**Cannot be undone:** an outbound action already triggered by an event — covered by
phase 9's controls.
