# Gate G-13 — deploy

> **Status: NOT RUN — phase not started.** Instantiated from
> `docs/quality/23_PHASE_GATES.md` §4.1 by task `T0.3`. Every assertion row is
> pre-filled, so an assertion that is never run shows up as an **omission**
> rather than as an absence (§3 rule 2).
>
> **The demo URL is a Stage One item.** §22.4: this phase always gets the **full**
> verification round. From here on, §23.11 applies: any assertion in this report
> whose command names `localhost` is marked **NOT RUN**.

Commit: `<full sha>`            Branch: `<name>`
Builder: `<name>`               Reviewer: `<name, must differ from builder>`
Round opened: `<ISO8601>`       Round closed: `<ISO8601>`
Verdict: `SIGNED | REJECTED | SIGNED WITH CARRIED DEBT`

## Environment of record

- Checkout: `fresh clone into <path> at <sha>` | `reused working tree (state why)`
- Database: `<cluster name>`, database `provenance`, CockroachDB `<version>`
- Deployed target: `<url>`, git sha `<sha>` as reported by `GET /v1/version`
- Last two migration revisions, for the on-call path: `<n-1>`, `<n>`

## Exit assertions

Battery: `make gate-13`. Entry criterion: `G-12` signed on a local or preview stack.

| ID | Result | Log |
|---|---|---|
| G13.1 | NOT RUN — phase not started | — |
| G13.2 | NOT RUN — phase not started | — |
| G13.3 | NOT RUN — phase not started | — |
| G13.4 | NOT RUN — phase not started | — |
| G13.5 | NOT RUN — phase not started | — |
| G13.6 | NOT RUN — phase not started | — |
| G13.7 | NOT RUN — phase not started | — |
| G13.8 | NOT RUN — phase not started | — |
| G13.9 | NOT RUN — phase not started | — |

What each one asserts (§19):

- **G13.1** — `cdk diff --all` reports "There were no differences" for every stack. Drift after deploy is a gate failure.
- **G13.2** — the deployed build is the reviewed build: `GET /v1/version` `git_sha` equals `git rev-parse HEAD` by **string equality, not a prefix**, and `schema_revision` is `0008`. The field is `git_sha`; `build_sha` is not a field name.
- **G13.3** — the demo URL answers `200` in under 3.0s, **re-run from a second network** (phone hotspot). Both recorded. A URL that only resolves on the build machine is not a functional demo URL.
- **G13.4** — the hero flow runs against the **deployed** stack, not localhost; the resulting `trace_id` is pasted here.
- **G13.5** — the trace exists in CloudWatch with the expected spans: `artifact.register`, `agent.interpreter.run`, `retrieval.vector`, `memory.kernel.transaction`, `outbox.dispatch`, `action.approve`, `action.execute`.
- **G13.6** — no secret is a plaintext environment value: the secret ARNs are in `RuntimeEnvironmentSecrets`, and no `RuntimeEnvironmentVariables` value matches `://`, `AKIA` or `BEGIN `.
- **G13.7** — every `provenance-` alarm is `OK`, not `INSUFFICIENT_DATA`: outbox-pending-age, dlq-depth, kernel-retry-rate, action-abort-rate.
- **G13.8** — a cold start is survivable on demo day: three timings recorded, subsequent under 1.0s. If cold start exceeds 10s, the demo script must include a warm-up request and **that must be written down here**.
- **G13.9** — the immediately previous application image runs against the head schema: `previous_image_vs_head_schema: PASS`. A failure blocks deployment.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§19): the full local suite, plus `e2e/` executed against the
deployed URL.

`<exact selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| — | — | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=13`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): **all six**.

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

Documented position (§19): roll back to the previous App Runner service revision and the
previous Amplify deployment — both one command:
`aws apprunner update-service --source-configuration <previous image tag>` and Amplify
promote-previous. **Cannot be undone: schema.** From here migrations roll forward only.
A code rollback must be compatible with the head schema; if it is not, the correct move
is a forward fix, not a downgrade. G13.9 is what keeps that true, and the last two
migration revisions are recorded in the environment block above so the on-call path is
obvious.
