# Gate G-1 — contracts and domain

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

Battery: `make gate-1`. Entry criterion: `G-0` signed; `11_CONTRACTS.md` read in full.

| ID | Result | Log |
|---|---|---|
| G1.1 | NOT RUN — phase not started | — |
| G1.2 | NOT RUN — phase not started | — |
| G1.3 | NOT RUN — phase not started | — |
| G1.4 | NOT RUN — phase not started | — |
| G1.5 | NOT RUN — phase not started | — |
| G1.6 | NOT RUN — phase not started | — |
| G1.7 | NOT RUN — phase not started | — |

What each one asserts (§7):

- **G1.1** — `mypy --strict` is clean over `provenance_contracts` and `provenance_domain`.
- **G1.2** — contract and domain tests: `NN passed` with `NN >= 60`, `0 failed`.
- **G1.3** — `contract_lint: 3 rules, 0 violations` — no float money, `schema_version` present, no SQL in contracts.
- **G1.4** — Hypothesis round-trip over every boundary model: build, dump, reload, assert equality.
- **G1.5** — the rejection matrix: bad input raises rather than coerces (negative confidence, confidence > 1, float amount, naive datetime, `valid_to <= valid_from`, 4-letter currency, missing `schema_version`).
- **G1.6** — `invariant_map_check`: `5 invariants, 5 mapped, 0 UNPROVEN`.
- **G1.7** — sabotage `provenance_domain.money.outstanding`: at least one FAILED, `exit=1`. **A green run here is a gate failure.**

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

`<exact pytest selection and its summary line>`

Required at this gate (§7):

```
packages/python/provenance_contracts/tests/test_scalars.py
packages/python/provenance_contracts/tests/test_roundtrip.py
packages/python/provenance_domain/tests/test_invariants.py
packages/python/provenance_domain/tests/test_transitions.py
```

Count authority: `CANONICAL_DECISIONS.md` → *Test and corpus counts* — `provenance_domain`
230, Layer 1 total 392. A gate that asserts a test count against a wrong figure fails on
arrival, so these are contract values.

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `provenance_domain.money.outstanding` | `packages/python/provenance_domain/tests` | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).
`python -m tools.sabotage_guard --count`; `--min-count <previous>` enforces §10.2
detector 2.

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=1`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-TIME` — 4.

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real?
- **Q3** Which invariant is currently unproven?
- **Q4** What would a hostile judge click on first?
- **Q5** What passed because of seeded state rather than logic?
- **Q6** What did I not look at?
- **Q7** If this phase is secretly broken, how and when would I find out?

> "Nothing" or "none" to Q1–Q6 is itself a finding.

## Carried debt

```
<make debt output — `0 carried items` if empty, and that line must still appear>
```

## Rollback position at time of signing

`<the exact command that returns the system to the last known-good state>`

Documented position (§7): roll back to the `G-0` commit; `git revert` the package
directories. No database and no infrastructure is touched. **Cannot be undone:** nothing.
