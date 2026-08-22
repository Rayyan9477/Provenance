# Gate G-15 — submission artifacts

> **Status: NOT RUN — phase not started.** Instantiated from
> `docs/quality/23_PHASE_GATES.md` §4.1 by task `T0.3`. Every assertion row is
> pre-filled, so an assertion that is never run shows up as an **omission**
> rather than as an absence (§3 rule 2).
>
> **There is no `G15.x`.** `23_PHASE_GATES.md` §21: "Exit assertions — see §24."
> `G-15`'s battery is the ten pre-submission items `S1`–`S10`, and the signed
> battery with its pasted output lives in **`ops/gates/SUBMISSION.md`**. This file
> is the phase report; that file is the battery. Together they are the last two
> of the 118 assertions' worth of evidence: `G0.1`–`G14.7` (108) plus `S1`–`S10`
> (10).
>
> §22.4: this phase always gets the **full** verification round.

Commit: `<full sha>`            Branch: `<name>`
Builder: `<name>`               Reviewer: `<name, must differ from builder>`
Round opened: `<ISO8601>`       Round closed: `<ISO8601>`
Verdict: `SIGNED | REJECTED | SIGNED WITH CARRIED DEBT`

## Environment of record

- Checkout: `fresh clone into <path> at <sha>` | `reused working tree (state why)`
- Database: `<cluster name>`, database `provenance`, CockroachDB `<version>`
- Deployed target: `<url>`, git sha `<sha>` as reported by `GET /v1/version`
- Runtime model ids **actually used**, per `agent_runs.model_route`: `<tier E>`, `<tier R>`

## Exit assertions

Battery: `make gate-15`, recorded in full in `ops/gates/SUBMISSION.md`. Entry criteria:
`G-13` and `G-14` signed. **Run the battery twice** — once 24 hours before the deadline,
once within 2 hours of submitting. The first run finds the problems; the second proves
nothing rotted.

| ID | Result | Log |
|---|---|---|
| S1 | NOT RUN — phase not started | — |
| S2 | NOT RUN — phase not started | — |
| S3 | NOT RUN — phase not started | — |
| S4 | NOT RUN — phase not started | — |
| S5 | NOT RUN — phase not started | — |
| S6 | NOT RUN — phase not started | — |
| S7 | NOT RUN — phase not started | — |
| S8 | NOT RUN — phase not started | — |
| S9 | NOT RUN — phase not started | — |
| S10 | NOT RUN — phase not started | — |

What each one asserts (§24). Stage One is **binary**: every item is PASS or FAIL with
pasted output.

- **S1** — public repository, verified as an anonymous client and not as the authenticated owner.
- **S2** — Apache-2.0 licence, SPDX-detected by GitHub, `NOTICE` carrying a copyright line.
- **S3** — functional demo URL from a network that is not the build network; `fixture_mode == false`; judge credentials work from a clean browser profile; the whole hero flow on the public URL.
- **S4** — demo video strictly under 180.0 seconds and publicly viewable. 179.4 is fine; 180.2 is a FAIL.
- **S5** — at least two CockroachDB tools genuinely used, **each with its stated degradation if removed**: vector index, Managed MCP Server, `ccloud`. A "tool used" that breaks nothing when removed is decoration; say so if it is true.
- **S6** — at least one AWS service, evidenced by a real trace on the demo path, not by a list.
- **S7** — the tool-usage disclosure in `SUBMISSION.md` and the "what is seeded vs what is computed" table in `README.md`.
- **S8** — clean `gitleaks` scan on the repository **and** separately on `ops/gates`.
- **S9** — a stranger can run it, timed by someone who did not build it. Record the real number even if it is 90 minutes; an honest number is useful and an aspirational one is not.
- **S10** — the demo survives a full reset. Run it **last**, then re-run S3.

`<verbatim output for every assertion — or the link to ops/gates/SUBMISSION.md, which is where it belongs>`

## Tests green

Required at this gate: the `G-14` full-suite run at the submitted commit, re-run from a
clean clone. `<exact selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| — | — | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): **all six**.

**`G-15` has no debt section by construction (§9.4):** the battery is `S1`–`S10` and every
item is binary. `make debt --assert-empty` runs once at `G-14` and once as part of the
final `SUBMISSION.md` assembly, and its failure blocks submission the same way a failing
`G14.2` threshold does. The section below therefore records the assertion, not a ledger.

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real? — anything still substituted at submission is a **disclosure**, and it ships in `README.md` and `SUBMISSION.md` rather than being carried silently.
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

Documented position (§21): roll back to the `G-13` deployed revision. Submission artifacts
are documents and carry no runtime risk. **Cannot be undone: a submitted entry.** Verify
§24 before submitting, not after.

## Ledger completeness check

`§24.1` requires, as its own checklist item, that `ops/gates/PHASE_00.md` … `PHASE_15.md`
are **all present, all SIGNED, with carried debt listed**. Confirm here, by listing all
sixteen with their verdicts:

| Report | Verdict | Signed at |
|---|---|---|
| `PHASE_00.md` | NOT RUN | — |
| `PHASE_01.md` | NOT RUN | — |
| `PHASE_02.md` | NOT RUN | — |
| `PHASE_03.md` | NOT RUN | — |
| `PHASE_04.md` | NOT RUN | — |
| `PHASE_05.md` | NOT RUN | — |
| `PHASE_06.md` | NOT RUN | — |
| `PHASE_07.md` | NOT RUN | — |
| `PHASE_08.md` | NOT RUN | — |
| `PHASE_09.md` | NOT RUN | — |
| `PHASE_10.md` | NOT RUN | — |
| `PHASE_11.md` | NOT RUN | — |
| `PHASE_12.md` | NOT RUN | — |
| `PHASE_13.md` | NOT RUN | — |
| `PHASE_14.md` | NOT RUN | — |
| `PHASE_15.md` | NOT RUN | — |
