# Gate G-8 — API and auth

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
- `PV_API`: `<url>` — state whether this is `localhost` or a deployed stack (§23.11)

## Exit assertions

Battery: `make gate-8`. Entry criteria: `G-5` signed; `04_API_EVENTS_SECURITY.md` and
`15_API_SPEC.md` read in full.

| ID | Result | Log |
|---|---|---|
| G8.1 | NOT RUN — phase not started | — |
| G8.2 | NOT RUN — phase not started | — |
| G8.3 | NOT RUN — phase not started | — |
| G8.4 | NOT RUN — phase not started | — |
| G8.5 | NOT RUN — phase not started | — |
| G8.6 | NOT RUN — phase not started | — |
| G8.7 | NOT RUN — phase not started | — |
| G8.8 | NOT RUN — phase not started | — |

What each one asserts (§14):

- **G8.1** — the implementation matches the written spec: `routes: 31 documented, 31 implemented, 0 drift; error codes: 0 drift`. Drift is a gate failure.
- **G8.2** — a workload token on a public route → `403 WORKLOAD_TOKEN_ON_PUBLIC_ROUTE`.
- **G8.3** — a browser token on an internal route → `403 BROWSER_TOKEN_ON_INTERNAL_ROUTE`.
- **G8.4** — a cross-user read → `404 CASE_NOT_FOUND`, **not 403**: existence is not disclosed.
- **G8.5** — a proposal cannot name an arbitrary `user_id`: `403 CAPABILITY_SUBJECT_MISMATCH`; a completed `AGENT_RUN` → `409 CAPABILITY_RETIRED`.
- **G8.6** — idempotency replay is exact and a body change is a conflict: identical bodies diff clean, `idempotency-replayed: true` on the second only, a changed body → `409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_BODY`.
- **G8.7** — `X-Provenance-Trace-Id` is present on **failures**, which is when it matters.
- **G8.8** — sabotage `api.auth.route_class_check`: at least 2 tests go red, `exit=1`.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§14): `tests/api/` in full — auth, capability, idempotency,
pagination, error envelope — plus phases 2–5 re-run. Canon paths (`70_TASK_PLAN.md` §2.4):
`services/control_plane/tests/unit/` (pure) and `services/control_plane/tests/db/`
(integration).

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `api.auth.route_class_check` | `services/control_plane/tests` (>= 2 tests) | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=8`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`, `L-TIME` — 5.

§23.10 applies here: the idempotency test must assert **string equality of the key across
attempts** before it asserts the single effect. A test that generates a fresh key per
attempt is testing the situation idempotency is meant to survive, and passes vacuously.

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

Documented position (§14): roll back to the `G-5` commit; redeploy the previous container
image tag. Nothing persistent is undone; `idempotency_records` rows written during gating
are harmless and expire. **Cannot be undone:** nothing.
