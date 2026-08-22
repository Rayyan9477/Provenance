# Gate G-11 — MCP, SQL roles, agent views

> **Status: NOT RUN — phase not started.** Instantiated from
> `docs/quality/23_PHASE_GATES.md` §4.1 by task `T0.3`. Every assertion row is
> pre-filled, so an assertion that is never run shows up as an **omission**
> rather than as an absence (§3 rule 2).
>
> **Sponsor tool requirement — not cuttable.** §22.4: this phase always gets the
> **full** verification round.

Commit: `<full sha>`            Branch: `<name>`
Builder: `<name>`               Reviewer: `<name, must differ from builder>`
Round opened: `<ISO8601>`       Round closed: `<ISO8601>`
Verdict: `SIGNED | REJECTED | SIGNED WITH CARRIED DEBT`

## Environment of record

- Checkout: `fresh clone into <path> at <sha>` | `reused working tree (state why)`
- Database: `<cluster name>`, database `<provenance|provenance_ci>`, CockroachDB `<version>`
- MCP server endpoint and the role it authenticates as: `<endpoint>`, `pv_agent_reader`

## Exit assertions

Battery: `make gate-11`. Entry criteria: `G-7` signed; the five agent-safe views exist
from phase 2.

| ID | Result | Log |
|---|---|---|
| G11.1 | NOT RUN — phase not started | — |
| G11.2 | NOT RUN — phase not started | — |
| G11.3 | NOT RUN — phase not started | — |
| G11.4 | NOT RUN — phase not started | — |
| G11.5 | NOT RUN — phase not started | — |
| G11.6 | NOT RUN — phase not started | — |
| G11.7 | NOT RUN — phase not started | — |

What each one asserts (§17):

- **G11.1** — V9: `pv_agent_reader` holds no grant on anything outside `agent_%_v1`. Header only, zero data rows.
- **G11.2** — the boundary is SQL grants, demonstrated by **refusal**: `SELECT` on `evidence_items` denied, `SELECT` on `agent_active_beliefs_v1` returns 1 row, `INSERT INTO claims` denied.
- **G11.3** — V10 and V11 together: `V10 0` (no retracted row reachable through the view) and `V11 3` (the retracted rows still exist and still carry embeddings). The pair is the positive control (§23.7).
- **G11.4** — the trace's MCP calls come from rows, not a template: >= 3 entries, every `sql_role == "pv_agent_reader"`, every `access_mode == "READ_ONLY"`, every `view_name` one of the five. The JSON field is `mcp_tool_calls[]`; the backing **column** is `agent_runs.tool_calls`. `agent_runs.mcp_tool_calls` is not a column name.
- **G11.5** — a denied call is rendered, not swallowed: the trace entry appears with `denied=true` and the SQL error class, and the run does not crash.
- **G11.6** — the view names in the database equal the view names in the API response; `diff` produces no output.
- **G11.7** — MCP is load-bearing: with it off, the Interpreter falls back to the control-plane retrieval endpoint and the trace renders `MCP UNAVAILABLE — degraded read path` rather than silently succeeding.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§17): `tests/mcp/`, plus V9/V10/V11. Canon path
(`70_TASK_PLAN.md` §2.4): `services/control_plane/tests/db/test_mcp_boundary.py`.

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| — | — | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=11`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-BND`, `L-RENDER` — 4.

Phase 0 already proved the boundary is real: the grant probe confirmed a role granted
`SELECT` on a view alone reads the view and is refused the base table
(`ops/grant-probe.txt`). If G11.1 or G11.2 disagrees with that transcript, the grants
regressed — and the predetermined fallback is to **stop Phase 11**, never to weaken a
grant (`CANONICAL_DECISIONS.md` → *Phase 0 verification decisions*).

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

Documented position (§17): roll back to the `G-7` commit; `REVOKE` the agent grants and
set `PV_MCP_ENABLED=false`. The agent then falls back to control-plane retrieval, which
must therefore stay functional — that is a real dependency and G11.7 asserts it.
**Cannot be undone:** nothing — but removing MCP removes one of the two required
CockroachDB tools and would fail the Stage One gate (§24, S5).
