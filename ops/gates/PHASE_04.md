# Gate G-4 — Memory Kernel

> **Status: NOT RUN — phase not started.** Instantiated from
> `docs/quality/23_PHASE_GATES.md` §4.1 by task `T0.3`. Every assertion row is
> pre-filled, so an assertion that is never run shows up as an **omission**
> rather than as an absence (§3 rule 2).
>
> **This is the phase where the product either exists or does not.** §22.4: phases
> 4, 9, 11, 13 and 15 always get the **full** verification round. No fast lane here.

Commit: `<full sha>`            Branch: `<name>`
Builder: `<name>`               Reviewer: `<name, must differ from builder>`
Round opened: `<ISO8601>`       Round closed: `<ISO8601>`
Verdict: `SIGNED | REJECTED | SIGNED WITH CARRIED DEBT`

## Environment of record

- Checkout: `fresh clone into <path> at <sha>` | `reused working tree (state why)`
- Database: `<cluster name>`, database `<provenance|provenance_ci>`, CockroachDB `<version>`
- Deployed target (if any): `<url>`, git sha `<sha>` as reported by `GET /v1/version`

## Exit assertions

Battery: `make gate-4`. Entry criteria: `G-3` signed; `12_KERNEL_ALGORITHMS.md` and
`02_DATA_MEMORY_TRANSACTIONS.md` read in full; `10_DATABASE_DDL.md` §13 understood as
**ordering**, not as a suggestion.

| ID | Result | Log |
|---|---|---|
| G4.1 | NOT RUN — phase not started | — |
| G4.2 | NOT RUN — phase not started | — |
| G4.3 | NOT RUN — phase not started | — |
| G4.4 | NOT RUN — phase not started | — |
| G4.5 | NOT RUN — phase not started | — |
| G4.6 | NOT RUN — phase not started | — |
| G4.7 | NOT RUN — phase not started | — |
| G4.8 | NOT RUN — phase not started | — |
| G4.9 | NOT RUN — phase not started | — |

What each one asserts (§10):

- **G4.1** — the hero commit: one proposal, one transaction, six row effects, `cases.revision` 12 → 13, `status` RESOLVED → REOPENED, `reopened_count` 1, conflict `VALUE_CONFLICT`, `state_transitions` reason `CONTRADICTORY_EVIDENCE`, `outbox_events` `case.reopened.v1` at `aggregate_version=13`.
- **G4.2** — the commit is real: re-read on a **new** pool connection opened after commit, and again from a separate shell after the test process exits. An in-transaction read-back is not evidence of a commit and is rejected at review.
- **G4.3** — `write_path_lint`: canonical write statements in exactly the modules named below; `agents/: 0  workers/: 0  apps/web/: 0  packages/: 0`.

  **Amended 2026-08-24.** This assertion read "in **1** module, `memory_kernel`" until Phase 10 landed the outbox dispatcher. It is now **2**: `app/memory_kernel` (17 statements) and `app/events` (6, every one `UPDATE outbox_events SET status = ...`).

  The amendment is to the *count*, not to the guarantee. Rule `W5` has always permitted the dispatcher that one write, and `provenance_db.repositories.__init__` enumerates it: status bookkeeping about a row the Kernel already wrote, carrying no domain meaning. What the gate must assert is **which** modules hold canonical writes, not how many — a count admits any second module, whereas naming them fails on a third and equally on the right module being replaced by the wrong one.

  `tools/tests/test_write_path_lint.py` was changed the same way and for the same reason, rather than being relaxed from `1` to `2`.
- **G4.4** — foreign evidence is refused **before** a transaction opens: `REJECTED_INVALID_PROVENANCE`, `kernel_decisions.transaction_opened = false`.
- **G4.5** — a duplicate proposal is a NOOP with `NOOP_ALREADY_APPLIED`, not a second commit; conflict, outbox and revision counts unchanged.
- **G4.6** — money moves atomically and the derived value is derived: fulfilled 0 → 300, outstanding 1200 → 900, ACTIVE → PARTIAL, revision +1, all in one transaction.
- **G4.7** — concurrency: 10 consecutive passes; no row where `status='FULFILLED' AND outstanding_amount > 0` ever existed; at least one run recorded `retry_count >= 1`.
- **G4.8** — `make db-verify` still holds after the Kernel suite has run.
- **G4.9** — sabotage `memory_kernel.preflight.assert_grounded`: `test_02_grounding_required` and at least one kernel test go red, `exit=1`.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§10): DDL §19 tests **1, 2, 3, 4, 5, 6, 9 (Kernel half), 10, 11**
plus `tests/kernel/*`. Tests 7, 8 and 12 remain deferred and must be listed as such.

| Deferred test | Closes at |
|---|---|
| DDL §19 test 7 | phase 9 |
| DDL §19 test 8 | phase 10 |
| DDL §19 test 12 | phase 6 |

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `memory_kernel.preflight.assert_grounded` | `services/control_plane/tests/db` + kernel suite | NOT RUN — phase not started |
| `memory_kernel.transaction.write_outbox` | `<selection>` | NOT RUN — phase not started |
| `memory_kernel.contradiction.detect` | `<selection>` | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=4`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`, `L-TIME` — 5.

§23.8 check, run and pasted here: every `kernel_decisions.status='NOOP'` row carries a
`reason_code` the demo script expects. An unexpected code, or a NULL, is a gate failure
even though nothing "broke".

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real?
- **Q3** Which invariant is currently unproven?
- **Q4** What would a hostile judge click on first?
- **Q5** What passed because of seeded state rather than logic? — run `make seed-perturb` (§23.1) and record what survived.
- **Q6** What did I not look at?
- **Q7** If this phase is secretly broken, how and when would I find out?

## Carried debt

```
<make debt output — `0 carried items` if empty, and that line must still appear>
```

## Rollback position at time of signing

`<the exact command that returns the system to the last known-good state>`

Documented position (§10): roll back to the `G-3` commit; revert `app/memory_kernel/`;
`make db-reset && make seed` clears anything the Kernel wrote. **Cannot be undone:**
nothing — the demo database is regenerable from seed at every phase, deliberately. If
that ever stops being true, `23_PHASE_GATES.md` is wrong and must be amended before
proceeding.
