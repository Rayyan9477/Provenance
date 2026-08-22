# Gate G-6 — embeddings and retrieval

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

Battery: `make gate-6`. Entry criteria: `G-2` signed with the vector index present;
`13_RETRIEVAL_SPEC.md` read.

| ID | Result | Log |
|---|---|---|
| G6.1 | NOT RUN — phase not started | — |
| G6.2 | NOT RUN — phase not started | — |
| G6.3 | NOT RUN — phase not started | — |
| G6.4 | NOT RUN — phase not started | — |
| G6.5 | NOT RUN — phase not started | — |
| G6.6 | NOT RUN — phase not started | — |
| G6.7 | NOT RUN — phase not started | — |

What each one asserts (§12):

- **G6.1** — one frozen `embedding_version` over every embedded row; exactly one group, e.g. `amazon.titan-embed-text-v2:0/1024/v1,18035`.
- **G6.2** — `EXPLAIN` names `evidence_embedding_ann_idx`. **A "full scan" line here is a FAILURE even if the results are correct.** Run it against the **production query shape**, parameter binding included — see the open defect `D-06-001`.
- **G6.3** — DB test 12, all four parts: (a) 0 of 200 returned ids belong to `iso-a`/`iso-b` over the 18,035-row corpus; (b) EXPLAIN names the index; (c) none of the 3 retraction fixtures appear; (d) **positive control** — with the retraction predicate removed, `sid('evidence','isp-wrong-term-date')` appears in the top 20. (d) failing means (c) was passing vacuously.
- **G6.4** — every retrieval statement carries a `user_id` predicate.
- **G6.5** — retrieval eval thresholds **asserted**, not merely reported: case R@1 >= 0.85, R@3 >= 0.95, and the hero scenario a HIT.
- **G6.6** — the cache is a cache, not a correctness dependency: cache cleared, identical vector recomputed.
- **G6.7** — sabotage `retrieval.predicates.retraction_filter`: G6.3(c) goes red, `exit=1`.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§12): `tests/retrieval/`, DDL §19 test 12, the
`evals/retrieval` threshold run.

`<exact pytest selection and its summary line>`

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `retrieval.predicates.retraction_filter` | DDL §19 test 12, part (c) | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=6`:

```
OPEN BLOCKER: ?  OPEN MAJOR: ?  OPEN MINOR: ?  CARRIED: ?  REJECTED: ?
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND` — 4.

**Open at instantiation:** `D-06-001` (MAJOR) — an ANN query vector supplied as a
correlated subquery silently produces a FULL SCAN. It closes only when the ANN entry
point takes a vector argument rather than deriving one, and G6.2 names the index with
`prefix spans` present. A MAJOR never carries: this gate cannot sign with it open (§4.3).

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

Documented position (§12): roll back to the `G-3` commit; revert the retrieval module.
If the ANN index is the problem, `DROP INDEX evidence_embedding_ann_idx` and fall back
to a brute-force scan over the user's partition — survivable for a demo at ~16,000 rows,
and it **must be disclosed** in Judge Mode and in the submission, never presented as
vector indexing. **Cannot be undone cheaply:** re-embedding 18,000 rows costs Bedrock
spend and tens of minutes; `db/seeds/vectors.parquet` must be populated at first seed.
