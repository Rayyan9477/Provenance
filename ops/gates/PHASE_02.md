# Gate G-2 — schema, migrations, seed

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

## Schema decisions recorded at build time

**`is_retrieval_eligible` is a generated `STORED` column.** T2.2's acceptance makes
this file the place that records which branch was taken, because the fallback — a
kernel-written boolean plus a consistency CHECK — is a different correctness argument
and a reader must not have to infer which one shipped.

PB-3 (probe P10, `ops/cluster-probe.txt`) proved `STORED` computed columns work on
this managed BASIC cluster, so the generated form is in force and the boolean fallback
is unused. The column is `(retraction_status = 'ACTIVE')`, which means a retracted row
cannot disagree with its own eligibility flag — the desync is unrepresentable rather
than merely tested for.

**Vector index variant: A**, per `ops/decisions/VECTOR_INDEX_VARIANT.md`, with
`user_id` as the prefix column at `seq_in_index = 1`. Verified against the built
schema: `EXPLAIN` with a literal query vector selects
`evidence_items@evidence_embedding_ann_idx` with prefix spans; the same query with the
vector as a subquery full-scans. That is `D-06-001` reproducing against the real
tables, and `test_query_vector_as_a_subquery_silently_loses_the_index` now stands as a
regression guard rather than a note in a probe transcript.

## Seed step 9 is DEFERRED, and the gate must read the row counts that way

`db/seeds/MANIFEST.json` reports `26 tables checked, 26 match`, and twelve of
those twenty-six match at **zero rows**: `claims`, `beliefs`, `belief_versions`,
`belief_support`, `conflicts`, `commitments`, `fulfillments`,
`prospective_triggers`, `state_transitions`, `memory_proposals`,
`kernel_decisions`, `outbox_events`.

That is a deliberate refusal, not an incomplete seed. `70_TASK_PLAN.md` T2.8
step 9 replays the curated `MemoryProposal` fixtures **through
`MemoryKernel.commit()`**, which is Phase 4 and did not exist when the corpus was
loaded. Seeding those tables by raw `INSERT` would have created a **second
canonical writer** — the one thing the architecture forbids, and the thing every
import contract, `tools/write_path_lint.py` and `G4.3` exist to prevent. A seed
that took the shortcut would have produced a fuller-looking database and a
broken invariant.

`SEED_PROFILE` in the `Makefile` is therefore pinned to `schema-only`. **Flip it
to `all` in the same commit that lands the Phase 4 kernel replay** — not before,
and not separately, or the two states drift apart silently.

A reviewer reading `26 tables checked, 26 match` must read it as a claim about a
**known-partial** seed. `MANIFEST.json` carries a `deferred` block naming the
twelve tables and the reason, so the zeros are explained rather than merely
present.

### The one figure that could not be taken from the database

`USD 2,020.00` — the total the landing screen renders — is computed from
`commitments`, which is Kernel-written and therefore empty. Queried live it
returns `USD 0 over 0 commitment rows`.

It was proved the only honest way available: `test_the_outstanding_total_is_2020_against_the_real_schema`
inserts the four commitments and their claims **as `pv_kernel_writer`, inside a
transaction, and rolls back**. The production SQL returns `2020.00` over 4 rows
and the row count before equals the row count after.

That probe caught a real defect that reads perfectly well in prose:
`commitment_type` was written as `REFUND` / `REIMBURSEMENT`, and **neither is in
`ck_commitments_type`**. The rollback probe also binds
`ck_commitments_outstanding_identity`, `ck_commitments_partial_status` and
`ck_commitments_fulfilled_needs_payment`.

## Exit assertions

Battery: `make gate-2`. Entry criteria: `G-1` signed; `10_DATABASE_DDL.md` read in full;
`ops/decisions/VECTOR_INDEX_VARIANT.md` populated; a **separate** `provenance_ci`
database exists so destructive gate work never touches demo data.

| ID | Result | Log |
|---|---|---|
| G2.1 | NOT RUN — phase not started | — |
| G2.2 | NOT RUN — phase not started | — |
| G2.3 | NOT RUN — phase not started | — |
| G2.4 | NOT RUN — phase not started | — |
| G2.5 | NOT RUN — phase not started | — |
| G2.6 | NOT RUN — phase not started | — |
| G2.7 | NOT RUN — phase not started | — |
| G2.8 | NOT RUN — phase not started | — |

What each one asserts (§8):

- **G2.1** — `downgrade base` → `upgrade head` → `downgrade base` → `upgrade head`, exit 0 each time.
- **G2.2** — exactly **26** base tables and nothing extra (`diff` against `db/expected_tables.txt`).
- **G2.3** — the five agent-safe views exist under their canon names.
- **G2.4** — `evidence_embedding_ann_idx` exists and its indexed columns begin with `user_id`.
- **G2.5** — `make db-verify` → `V1 0 … V10 0  V11 3`. **V11 < 3 is a FAILURE**: it means the retraction fixtures were deleted rather than retracted.
- **G2.6** — seeding is idempotent and matches `db/seeds/MANIFEST.json` (`26 tables checked, 26 match`).
- **G2.7** — the schema itself refuses `status='FULFILLED'` with `outstanding_amount > 0`, with no Python in the path.
- **G2.8** — the schema refuses an ungrounded canonical belief version.

`<verbatim output for every assertion, or a link to the committed log>`

## Tests green

Required at this gate (§8):

```
pytest tests/db/test_02_grounding_required.py                        # DDL §19 test 2
pytest tests/db/test_05_no_fulfilled_with_outstanding.py             # test 5
pytest tests/db/test_11_cross_user_reference_rejected.py -k raw_sql  # test 11, the FK half
```

Canon path mapping (`70_TASK_PLAN.md` §2.4): these live at
`services/control_plane/tests/db/test_kernel_required.py`.

**Deferred tests must be listed here with the phase that closes them.** DDL §19 tests
1, 3, 4, 6, 7, 8, 9, 10 and the Kernel half of 11 defer to phases 4, 9, 10; test 12 to
phase 6. Not listing them is a §22.3 Q3 violation. A scheduled deferral is **not** debt
(`72_DEFECT_PROTOCOL.md` §9.1) — it belongs here, not in `CARRIED_DEBT.md`.

| Deferred test | Closes at |
|---|---|
| `<test>` | `<phase>` |

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| — | — | NOT RUN — phase not started |

Sabotage matrix entry count at this gate: `<n>` (previous gate: `<n>`).

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. Paste the last line of `make defects PHASE=2`:

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

## Carried debt

```
<make debt output — `0 carried items` if empty, and that line must still appear>
```

## Rollback position at time of signing

`<the exact command that returns the system to the last known-good state>`

Documented position (§8): roll back to the `G-1` commit plus `alembic downgrade base`.
Undo is `make db-reset` for `provenance_ci`; for `provenance`,
`alembic downgrade base && alembic upgrade head && make seed`. **Cannot be undone:**
nothing yet — and this is the last phase at which that sentence is true without
qualification. Record that here.
