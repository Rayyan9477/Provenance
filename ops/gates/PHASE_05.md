# Gate G-5 — deterministic read models

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

Battery: `make gate-5`. Entry criterion: `G-4` signed.

| ID | Result | Log |
|---|---|---|
| G5.1 | NOT RUN — phase not started | — |
| G5.2 | NOT RUN — phase not started | — |
| G5.3 | NOT RUN — phase not started | — |
| G5.4 | NOT RUN — phase not started | — |
| G5.5 | NOT RUN — phase not started | — |

What each one asserts (§11):

- **G5.1** — State Proof with Bedrock made **impossible**, not merely unused: `PV_BEDROCK_CLIENT=provenance_telemetry.testing.ExplodingClient` raises on construction, so a passing suite proves no model was in the path.
- **G5.2** — State Proof carries **grounding** and **lineage** under those names: `grounding` relations `["CONTRADICTS","SUPPORTS"]`, `lineage_depth` 2, `superseded` 1. Grounding is `belief_support` edges; lineage is the `belief_versions` supersession chain. Conflating the two is rejected at review (`CANONICAL_DECISIONS.md`, product vocabulary).
- **G5.3** — the snapshot is hand-written, not regenerated; `tests/fixtures/state_proof_hero.expected.json` is under `tools/fixture_guard.py` protection (§23.4).
- **G5.4** — no chain-of-thought key (`thinking`, `reasoning_trace`, `scratchpad`, `raw_completion`) anywhere in a read-model payload.
- **G5.5** — sabotage `read_models.state_proof.load_grounding`: the snapshot test goes red, `exit=1`.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§11): `tests/read_models/` in full, plus phases 2–4 re-run.
Canon path (`70_TASK_PLAN.md` §2.4): `services/control_plane/tests/db/test_read_models.py`.

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `read_models.state_proof.load_grounding` | `services/control_plane/tests/db/test_read_models.py` | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=5`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND` — 4.

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

`m-ex-1` in `72_DEFECT_PROTOCOL.md` §4.6 predicts the first likely carry from this phase:
State Proof rendering a disputed belief's unchanged value with equal weight to its changed
status. If it is accepted, it needs a **named** owner and `Closes by: G-12`; an unowned
MINOR is not carriable and blocks the gate exactly like a MAJOR.

## Rollback position at time of signing

`<the exact command that returns the system to the last known-good state>`

Documented position (§11): roll back to the `G-4` commit; revert the module.
**Cannot be undone:** nothing.
