# Gate G-9 — actions, approval, executor

> **Status: NOT RUN — phase not started.** Instantiated from
> `docs/quality/23_PHASE_GATES.md` §4.1 by task `T0.3`. Every assertion row is
> pre-filled, so an assertion that is never run shows up as an **omission**
> rather than as an absence (§3 rule 2).
>
> **This phase owns invariant 4.** §22.4: it always gets the **full** verification
> round. No fast lane.

Commit: `<full sha>`            Branch: `<name>`
Builder: `<name>`               Reviewer: `<name, must differ from builder>`
Round opened: `<ISO8601>`       Round closed: `<ISO8601>`
Verdict: `SIGNED | REJECTED | SIGNED WITH CARRIED DEBT`

## Environment of record

- Checkout: `fresh clone into <path> at <sha>` | `reused working tree (state why)`
- Database: `<cluster name>`, database `<provenance|provenance_ci>`, CockroachDB `<version>`
- Provider sink: `<the sink whose call log the assertions read>` — not a mock counter

## Exit assertions

Battery: `make gate-9`. Entry criterion: `G-8` signed.

| ID | Result | Log |
|---|---|---|
| G9.1 | NOT RUN — phase not started | — |
| G9.2 | NOT RUN — phase not started | — |
| G9.3 | NOT RUN — phase not started | — |
| G9.4 | NOT RUN — phase not started | — |
| G9.5 | NOT RUN — phase not started | — |
| G9.6 | NOT RUN — phase not started | — |
| G9.7 | NOT RUN — phase not started | — |

What each one asserts (§15):

- **G9.1** — DB test 7: a stale approval cannot execute. Approve at `basis_case_revision=13`, commit an unrelated Kernel change (revision → 14), executor query returns 0 rows, `action_executions` row `ABORTED_STALE / CASE_REVISION_MOVED`, **provider calls made: 0**, asserted against the sink's call log rather than a mock counter.
- **G9.2** — editing a draft invalidates a prior approval: `approval_draft_sha256` changes, execute → `409 ACTION_STALE`.
- **G9.3** — an ungrounded claim in a draft cannot ship: `DRAFT_CLAIM_UNSUPPORTED`, no `ActionIntent` created.
- **G9.4** — execution is idempotent at the provider boundary: two executes under one key → sink message count 1; `attempt_no` 1 and 2 both recorded; the second returns the first's outcome.
- **G9.5** — the recipient allowlist is real: `RECIPIENT_NOT_ALLOWLISTED`, zero provider calls.
- **G9.6** — invariant 4 stated directly: an `ActionIntent` whose case has no committed `kernel_decision` → `409 NO_COMMITTED_BASIS`; a REJECTED proposal cannot produce an `ActionIntent` at all.
- **G9.7** — sabotage `actions.executor.revalidate_revision`: G9.1 goes red, `exit=1`.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§15): `tests/actions/`, DDL §19 test 7. Canon paths
(`70_TASK_PLAN.md` §2.4): `services/control_plane/tests/unit/test_action_policy_pure.py`
and `services/control_plane/tests/db/test_actions.py`.

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `actions.executor.revalidate_revision` | DDL §19 test 7 | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=9`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`, `L-TIME` — 5.

Worked BLOCKER to hunt for explicitly (`72_` §4.4, B-ex-4): the executor revalidating on
one connection and sending on another. G9.1 still passes, because its test moves the
revision *before* the executor runs. The only irreversible operation in the system is
exactly the one with a time-of-check-to-time-of-use window.

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real? — name the provider sink and the phase in which it becomes real, if ever.
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

Documented position (§15): roll back to the `G-8` commit; set
`PV_ACTION_EXECUTION_MODE=DISABLED`. Approvals continue to be recorded and nothing is
sent. **This is the kill switch and it must be tested at this gate, not discovered at the
demo.** **Cannot be undone:** a message already sent — the only irreversible operation in
the entire system, which is why it sits behind revision revalidation, a draft hash, a
recipient allowlist, and a human click.
