# Gate G-7 — LangGraph graphs

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
- Model ids actually invoked, from `agent_runs.model_route`: `<tier E id>`, `<tier R id>`

## Exit assertions

Battery: `make gate-7`. Entry criteria: `G-5` and `G-6` signed;
`03_AGENTS_LANGGRAPH_CONTRACTS.md` and `14_PROMPTS.md` read.

| ID | Result | Log |
|---|---|---|
| G7.1 | NOT RUN — phase not started | — |
| G7.2 | NOT RUN — phase not started | — |
| G7.3 | NOT RUN — phase not started | — |
| G7.4 | NOT RUN — phase not started | — |
| G7.5 | NOT RUN — phase not started | — |
| G7.6 | NOT RUN — phase not started | — |
| G7.7 | NOT RUN — phase not started | — |

What each one asserts (§13):

- **G7.1** — topology and routing on fixtures, deterministic; node visit order printed per test; the resolver is invoked **only** in the ambiguous-identity fixture and is absent in the other 6.
- **G7.2** — exactly one schema repair attempt, then the pending path: `model_calls=2` then `status=PENDING_REVIEW`; never 3.
- **G7.3** — the agent package cannot write: `write_path_lint --package agents` → `canonical write statements: 0`, and no writer role name appears anywhere under `agents/`.
- **G7.4** — live smoke: one call per tier, model id printed.
- **G7.5** — prompt injection reaches no capability: 12 injected artifacts, `kernel commits caused: 0 | action intents created: 0 | scopes escalated: 0`, and **each artifact is still admitted as immutable evidence** — the injection text is evidence, and suppressing it would violate invariant 1.
- **G7.6** — every terminal state validates as a `MemoryProposal` or a `KernelCommitResult`; the graph's output is a typed proposal, not prose.
- **G7.7** — sabotage `agents.runtime.graphs.ingestion_graph.should_resolve`: G7.1 goes red, `exit=1`.

> **G7.4 model ids.** `CANONICAL_DECISIONS.md` → *Bedrock model id canon* supersedes the
> bare ids printed in §13: Anthropic chat models are invoked by **inference-profile id**.
> Tier E `us.anthropic.claude-haiku-4-5-20251001-v1:0` (verified invocable). Tier R target
> `us.anthropic.claude-opus-5` (**access denied on this account**, defect `D-00-004`); in
> force until the grant lands, `us.anthropic.claude-opus-4-6-v1`. Record the id **actually
> used** here and in `README.md`. Claiming Opus 5 while running 4.6 is exactly the
> small checkable dishonesty §23 exists to prevent. Any output naming Sonnet 4.6, Gemma 4,
> GLM 5 or Kimi K2.5 is a FAILURE — those are stale identifiers from superseded documents.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§13): `agents/runtime/tests/`, and the injection suite at
`services/control_plane/tests/adversarial/` (`70_TASK_PLAN.md` §2.4).

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `agents.runtime.graphs.ingestion_graph.should_resolve` | `agents/runtime/tests` | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=7`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND`, `L-TIME` — 5.

**Open at instantiation:** `D-00-004` (BLOCKER) — Tier R access denied. A BLOCKER never
carries at any gate under any schedule pressure (§4.3). It closes either when
`us.anthropic.claude-opus-5` returns `ok`, **or** when the team decides to ship on 4.6
and the README states the model actually used.

## Standing questions (§22.3) — answered honestly

- **Q1** What did I claim without running?
- **Q2** What is mocked that should be real? — fixture mode is the substitute here; name the phase in which it becomes real, and note that it must be **OFF** at `G-15` (§23.12).
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

Documented position (§13): roll back to the `G-6` commit; set `PV_AGENT_MODE=FIXTURE`.
The Kernel path is still exercised through the deterministic proposal endpoint, so the
system stays demonstrable without live agents. **Cannot be undone:** nothing — but
fixture mode must be visibly disclosed in the UI whenever it is on, and must be OFF at
`G-15`.
