# Gate G-2 — schema, migrations, seed

> **Status: NOT SIGNED. Six assertions pass, one fails, one is stale and needs
> re-running, and one passed by a route the assertion does not name.**
> Instantiated from `docs/quality/23_PHASE_GATES.md` §4.1 by task `T0.3`. Every
> assertion row is pre-filled, so an assertion that is never run shows up as an
> **omission** rather than as an absence (§3 rule 2).
>
> The rows below were rewritten on 2026-08-31. They previously read `NOT RUN --
> phase not started` for all eight while `db/seeds/MANIFEST.json` sat in the
> same repository reporting a seeded 26-table database, and while
> `ops/gates/logs/G2.*.log` held six real verdicts. The seed section further
> down described a deferral that ended on 2026-08-24. Both are corrected here.
>
> **What still stops this gate.** `G2.1` failed on its last recorded run and has
> not been re-run, and `D-02-005` stands against this phase as a MAJOR at
> `AWAITING_REVERIFY`, which blocks exactly as `OPEN` does (§4.3). `make gate-2`
> exists and runs -- one of only two batteries that do -- so unlike most gates
> in this directory the missing thing here is a clean round, not a battery.

Commit: `no round opened`       Branch: `main`
Builder: Integrator             Reviewer: **none, no round was opened**
Round opened: `n/a`             Round closed: `n/a`
Verdict: **NOT SIGNED**

## Environment of record

- Checkout: reused working tree. The assertion logs were written at
  `33e593c1` on 2026-08-18; the seed and manifest facts are current as of
  2026-08-31; the invariant verdict under `G2.5` was measured on 2026-08-28.
  Three dates, stated rather than flattened into one.
- Database: `<cluster>`, CockroachDB CCL v26.2.5, BASIC, AWS us-east-1. The
  battery runs against `provenance_ci`, which is disposable by design and must
  never be pointed at `provenance`.
- **The cluster is at `0009b`, and migration `0009` is deliberately unapplied.**
  This changes how `G2.1` must be read and is the reason that row cannot simply
  be re-run: `upgrade head` now includes `0009_gemini_embedding_plane`, which
  widens `evidence_items.embedding` to `VECTOR(1536)` and leaves every embedding
  NULL. On `provenance` that destroys 18,035 Titan vectors. `README.md` and
  `STATUS.md` §8 both carry the decision.
- Deployed target: two Cloud Run services are serving (Phase 13). No assertion
  at this gate was taken against them; the schema is read directly.

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

Measured again on 2026-08-30 over the whole corpus by the eval harness, which is
a different code path on a different database: `RTR-01 PASS -- 0 row(s) where
is_retrieval_eligible disagrees with (retraction_status = 'ACTIVE')`.

**Vector index variant: A**, per `ops/decisions/VECTOR_INDEX_VARIANT.md`, with
`user_id` as the prefix column at `seq_in_index = 1`. Verified against the built
schema: `EXPLAIN` with a literal query vector selects
`evidence_items@evidence_embedding_ann_idx` with prefix spans; the same query with the
vector as a subquery full-scans. That is `D-06-001` reproducing against the real
tables, and `test_query_vector_as_a_subquery_silently_loses_the_index` now stands as a
regression guard rather than a note in a probe transcript.

## Seed step 9 has landed, and the row counts must be read that way

**This section formerly said step 9 was DEFERRED and that twelve tables were
legitimately empty. That stopped being true on 2026-08-24 and the correction
matters more than the original note did**, because the deferral was the reason
this gate was allowed to read `26 tables checked, 26 match` as a claim about a
known-partial seed. It is no longer partial in that way.

`70_TASK_PLAN.md` T2.8 step 9 replays the curated `MemoryProposal` fixtures
**through `MemoryKernel.commit()`**, which is Phase 4 work. It landed together
with the Kernel's commitment and trigger write path, and `SEED_PROFILE` in the
`Makefile` flipped from `schema-only` to `all` **in the same change** -- which is
what the old note demanded, so that the two states could not drift apart
silently. `db/seeds/MANIFEST.json` now carries `"profile": "all"`, a `deferred`
block whose `step` is `null` and whose `tables` list is empty, and the counts
that `--profile all` produces. They are asserted by
`services/control_plane/tests/db/test_seed_step9.py`, which builds a throwaway
database rather than writing into `provenance` or `provenance_ci`.

The canonical tables the Kernel wrote at seed time:

```
claims 36   beliefs 9   belief_versions 9   belief_support 9
commitments 4   fulfillments 2   memory_proposals 11   kernel_decisions 11
prospective_triggers 2   state_transitions 8   outbox_events 8
```

**Seven tables are still zero, and only one of them is a decision.**
`conflicts` is 0 **on purpose**: the June invoice, the reopen and the 12 → 13
revision increment are the demo's to perform, not the seed's. Seeding the
conflict would spend the reveal and would make the demo a replay of a row that
already existed. The other six -- `action_executions`, `action_intents`,
`agent_runs`, `idempotency_records`, `ingest_aliases`, `processed_events` -- are
runtime tables that a seed has no business writing.

A reviewer reading `26 tables checked, 26 match` should still not read it as
"the database is full". It means the seed reproduces its own manifest, and the
manifest's `deferred` block is where any remaining refusal would be named.

### The figure the landing screen renders

`USD 2,020.00` is now **taken from the database**. `commitments` holds the four
Kernel-written rows, the API sums them, and the web app renders the total with no
fixture banner (`STATUS.md` §2). Before step 9 landed it could not be: the table
was empty and `python -m scripts.seed --outstanding-total` printed
`USD 0 over 0 commitment rows`.

The proof written for that period is still in the tree and still earns its place:
`test_the_outstanding_total_is_2020_against_the_real_schema` inserts the four
commitments and their claims **as `pv_kernel_writer`, inside a transaction, and
rolls back**. The production SQL returns `2020.00` over 4 rows and the row count
before equals the row count after. What it binds is the schema, not the seed:
`ck_commitments_type` is a closed vocabulary and `REFUND` / `REIMBURSEMENT` --
both entirely plausible, neither a member -- were the first values these fixtures
carried. `ck_commitments_outstanding_identity`, `ck_commitments_partial_status`
and `ck_commitments_fulfilled_needs_payment` all bind there too.

**Its docstring is now stale** and says `commitments` is empty because step 9
waits for Phase 4. The test is correct; the sentence above it is not. Recorded
here rather than quietly fixed, because a gate report is where a reviewer should
find out that a comment and its code disagree.

## Exit assertions

Battery: `make gate-2`. Entry criteria: `G-1` signed; `10_DATABASE_DDL.md` read in full;
`ops/decisions/VECTOR_INDEX_VARIANT.md` populated; a **separate** `provenance_ci`
database exists so destructive gate work never touches demo data.

**`G-1` is not signed** -- no gate is -- so this battery ran with its entry
criterion unmet. The other three are met: the variant decision is populated and
`provenance_ci` is a separate database that `conftest.py` refuses to let a test
escape from.

| ID | Result | Log |
|---|---|---|
| G2.1 | **FAIL** -- exit 1 on the fourth cycle, `no database or schema specified`. Not re-run, and see the note below | `G2.1.33e593c1.log` |
| G2.2 | **PASS** -- exit 0, `canonical base tables: 26` | `G2.2.33e593c1.log` |
| G2.2b | **PASS** -- exit 0, `expected_tables.txt matches the live schema` | `G2.2b.33e593c1.log` |
| G2.3 | **PASS** -- exit 0, the five `agent_*_v1` views under their canon names and nothing else | `G2.3.33e593c1.log` |
| G2.4 | **PASS** -- exit 0, `user_id\|1`, `embedding\|2`, `id\|3` | `G2.4.33e593c1.log` |
| G2.5a | **FAIL** -- exit 1. The pre-seed half asserted nothing, because the database was not empty when it ran | `G2.5a.33e593c1.log` |
| G2.5 | **PASS, as a deviation** -- `VERDICT PASS`, `V1 0 … V10 0  V11 3`, measured on `provenance` and recorded in `STATUS.md` §1 rather than through `tools/gate.sh` | none written |
| G2.6 | **PASS, and stale** -- seeding is idempotent and matched its manifest on 2026-08-18. That manifest has since been replaced | `ops/tdd/GREEN_T2.8.txt` |
| G2.6b | **PASS** -- exit 0, `non-view grants: 0` for `pv_agent_reader` | `G2.6b.33e593c1.log` |
| G2.7 | **PASS** -- `test_nothing_fulfilled_with_outstanding`, green in the db lane | `ops/tdd/GREEN_T4.6-4.13.txt` §8 |
| G2.8 | **PASS** -- `test_belief_cannot_be_canonical_without_grounding`, green in the db lane | `ops/tdd/GREEN_T2.1-2.3.txt`, `ops/tdd/GREEN_T4.6-4.13.txt` §8 |

What each one asserts (§8):

- **G2.1** — `downgrade base` → `upgrade head` → `downgrade base` → `upgrade head`, exit 0 each time.
- **G2.2** — exactly **26** base tables and nothing extra (`diff` against `db/expected_tables.txt`).
- **G2.3** — the five agent-safe views exist under their canon names.
- **G2.4** — `evidence_embedding_ann_idx` exists and its indexed columns begin with `user_id`.
- **G2.5** — `make db-verify` → `V1 0 … V10 0  V11 3`. **V11 < 3 is a FAILURE**: it means the retraction fixtures were deleted rather than retracted.
- **G2.6** — seeding is idempotent and matches `db/seeds/MANIFEST.json` (`26 tables checked, 26 match`).
- **G2.7** — the schema itself refuses `status='FULFILLED'` with `outstanding_amount > 0`, with no Python in the path.
- **G2.8** — the schema refuses an ungrounded canonical belief version.

### G2.1 -- a real failure, and it cannot simply be re-run

The recorded run completed three and a half of its four cycles and then died on
`upgrade head` at `0003_epistemic_plane` with
`psycopg.errors.InvalidName: no database or schema specified`. That is an
environment fault rather than a migration fault -- `COCKROACH_DATABASE_URL` was
being read out of `.env` inside a loop that had already succeeded three times --
but **the assertion is that four cycles exit 0, and they did not.** It is
recorded as FAIL and not as an infrastructure excuse.

Partial evidence exists and is worth naming rather than leaving for someone to
find: `ops/tdd/GREEN_T2.1-2.3.txt` holds a **two-cycle reversibility drill over
`0001`–`0003`** with `exit=0` on all four commands and `alembic current`
returning `0003_epistemic_plane (head)` at both ends. So the reversibility of
the first three revisions is proved; the reversibility of the full chain is not.

**Re-running it today is a decision, not a step.** `upgrade head` now reaches
`0009`, and `downgrade base` on a database holding the corpus is the same loss by
a different route. Re-running this assertion means either doing it on a scratch
database with no corpus -- which proves the chain and says nothing about the
demo database -- or deciding the `0009` question first. Recording the choice is
part of what a signature would mean.

### G2.5 and G2.5a -- one assertion in two halves, and neither ran as designed

`G2.5a` is the more interesting half. It exists to prove that the verification
suite **refuses** an empty corpus, so that `V1 0 … V10 0` cannot be reported as
success over a database with nothing in it. It failed because `provenance_ci`
was not empty at the moment it ran, which means the assertion proved nothing --
it did not prove something false. The distinction is the one `D-00-005` was
filed over.

The substance of that half **is** proved, separately, in `ops/tdd/GREEN_T2.7.txt`
§4b, where `make db-verify` against an empty database prints
`db-verify FAILED: the corpus is empty, so V1-V10 returning zero proves nothing`.
Recorded as a deviation, the way `G0.5` was: the assertion as written ran and
failed, and the property it was protecting is demonstrated elsewhere. A reviewer
may accept the substitute or not.

`G2.5` proper -- the seeded half -- has no gate log. What exists is a
measurement recorded in `STATUS.md` §1 on 2026-08-28: `VERDICT PASS`,
`V1 0 … V10 0  V11 3`, taken against `provenance` rather than `provenance_ci`.
`V11 3` is the positive control (§23.7): the three retraction fixtures still
exist and still carry their embeddings, so the zeros above them mean "excluded"
rather than "deleted".

**A finding, recorded because this is where it belongs.** The battery asserts
`G2.5` with `grep -qE "^VERDICT PASS"`, and `db-verify` can emit
`VERDICT PASS_PARTIAL`, which that pattern also matches. `PASS_PARTIAL` is what
the suite says when V1–V10 returned zero **over rows it never examined** --
precisely the vacuity `G2.5a` exists to prevent, arriving through the assertion
that was supposed to catch it. This gate does not rest on that hole: the
recorded verdict is the unqualified `PASS`. The pattern should be anchored
before anyone signs.

### G2.6 -- passing, and old enough to need re-running

`ops/tdd/GREEN_T2.8.txt` records the acceptance in the right shape: seed twice,
then `diff` the row counts, `diff exit=0` with no output. And
`python -m scripts.seed --check-manifest --scoped` printed
`26 tables checked, 26 match`.

That transcript is from 2026-08-18 and the manifest it matched was the
schema-only one, with twelve tables at zero and a populated `deferred` block.
`db/seeds/MANIFEST.json` has since been replaced with the `all`-profile manifest
described above. **The idempotence property is unlikely to have changed and the
manifest it was checked against certainly has**, so this row is a pass that a
signature would have to re-earn.

The same transcript records something a re-run will hit again:
`python -m tools.manifest_check` **unscoped** exits 1 against `provenance_ci`
with eight tables over-count, because four other phases write fixture rows into
that shared database. The unscoped comparison is the right one for the demo
database and for a freshly reset CI database, and the wrong one for a CI
database that four phases are actively testing against. That is a property of
the environment, not a seed defect, and it should not be read as one.

## Tests green

Required at this gate (§8):

```
pytest tests/db/test_02_grounding_required.py                        # DDL §19 test 2
pytest tests/db/test_05_no_fulfilled_with_outstanding.py             # test 5
pytest tests/db/test_11_cross_user_reference_rejected.py -k raw_sql  # test 11, the FK half
```

Canon path mapping (`70_TASK_PLAN.md` §2.4): these live at
`services/control_plane/tests/db/test_kernel_required.py`.

Measured, in the `db`-marked lane against `provenance_ci`:

```
2026-08-18, at c2a0c05 (ops/tdd/GREEN_T2.1-2.3.txt)
  pytest services/control_plane/tests/db -m db
  60 passed in 571.61s

2026-08-19, after T4.13 (ops/tdd/GREEN_T4.6-4.13.txt §8)
  pytest services/control_plane/tests/db/test_kernel_hero.py \
         services/control_plane/tests/db/test_kernel_required.py -q -m db
  27 passed in 445.17s
```

**Deferred tests, with the phase that closes them.** Not listing them is a
§22.3 Q3 violation, and a scheduled deferral is not debt
(`72_DEFECT_PROTOCOL.md` §9.1), so it belongs here and not in `CARRIED_DEBT.md`.
Two of the three groups have since closed and the table says which:

| Deferred test | Closes at | State on 2026-08-31 |
|---|---|---|
| DDL §19 tests 1, 3, 4, 6, 7, 8, 9, 10 and the Kernel half of 11 | phases 4, 9, 10 | The phase-4 group landed with T4.13 and is in the **27 passed** run above. The phase-9 and phase-10 halves are **not separately identified in any transcript**, so this row records them as unverified from this gate rather than as closed. |
| DDL §19 test 12 | phase 6 | **Closed.** Proved in all four parts, including the positive control. See `ops/gates/PHASE_06.md`, G6.3. |

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| -- | -- | **NOT RUN -- this phase registers no sabotage entry, and the reason is structural** |

Sabotage matrix entry count at this gate: **13** (previous gate: 1 at `G-0`).
§10.2 detector 2 requires only that the count never shrinks, and it has grown
because later phases registered their own symbols.

Phase 2's assertions are almost all **schema** assertions: a `CHECK` constraint,
a view definition, an index prefix, a `STORED` computed column. `PV_SABOTAGE`
rebinds a named attribute on a Python module object, and a constraint in
CockroachDB is not a Python symbol, so there is nothing here for the mechanism
to neuter. That is not a gap to be filled by inventing an entry; it is the
reason `G2.7` and `G2.8` are written as "with no Python in the path" in the
first place. The corresponding proof for a schema assertion is the **negative
test** -- attempt the forbidden write, require the database to refuse it -- and
that is what `test_kernel_required.py` does throughout.

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. `make defects PHASE=2`, run 2026-08-31:

```
OPEN BLOCKER: 0  OPEN MAJOR: 1  OPEN MINOR: 0  CARRIED: 0  REJECTED: 0   [phase 2]
defect_lint: 0 violations
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-TIME` — 4.

The one record is `D-02-005` (MAJOR, `L-INV`) -- `_fetch_all` turns a mapping row
into its own column names, silently, and `strict=True` cannot catch it. Its
status is `AWAITING_REVERIFY`: fixed, but without the §7.4 close-proof
counterfactual. **`AWAITING_REVERIFY` blocks a gate exactly as `OPEN` does**, so
under §4.3 this record rejects the gate on its own, independently of `G2.1`.

## Standing questions (§22.3) — answered honestly

- **Q1 · What did I claim without running?** `G2.5`. The verdict in the table is
  a measurement taken against `provenance` on 2026-08-28 and recorded in
  `STATUS.md`, not a run of the assertion as the battery writes it. Everything
  else in the table has either a log under `ops/gates/logs/` or a named
  transcript under `ops/tdd/`.
- **Q2 · What is mocked that should be real?** Nothing at this gate. Every
  assertion here talks to a real CockroachDB cluster; there is no fixture layer
  between the test and the schema, which is the point of a schema gate.
- **Q3 · Which invariant is currently unproven?** None is unmapped --
  `tools/invariant_map_check.py` reports **5 invariants, 5 mapped, 0 UNPROVEN**
  tree-wide. What this gate proves is only the **database half**: the schema
  refuses the impossible states. The application half of every invariant belongs
  to the Kernel at `G-4`, and a schema that refuses a bad row says nothing about
  whether the code ever tries to write one.
- **Q4 · What would a hostile judge click on first?** The seed. `26 tables
  checked, 26 match` is the kind of line that reads as completeness, and seven
  tables in that count are zero. The section above names all seven and separates
  the one deliberate refusal from the six runtime tables, so the answer is in the
  document before the question is asked.
- **Q5 · What passed because of seeded state rather than logic?** `G2.5`'s
  `V11 3` is a statement about seeded fixtures, and it is the assertion most
  exposed to the failure it is meant to detect: if the fixtures were deleted
  rather than retracted, V11 falls to 0 and the surrounding zeros would look
  better, not worse. That inversion is why `V11 < 3` is a FAILURE and not a
  warning.
- **Q6 · What did I not look at?** The `0009` chain. `0009`, `0009a` and `0009b`
  all postdate every log in this report; `0009a` and `0009b` are applied to the
  cluster and `0009` is not, and no assertion at this gate has been run against
  that state. `test_migration_0009.py` exists and was not re-read for this
  report.
- **Q7 · If this phase is secretly broken, how and when would I find out?**
  Through the seed drifting away from its manifest without anyone noticing,
  because the check that would catch it is the one row here that is stale.
  `G2.6` is the detector, `db/seeds/MANIFEST.json` is what it compares against,
  and the two were last compared before the manifest changed. The failure would
  surface as a demo whose numbers do not match its documentation, which is the
  worst place to discover it.

## Carried debt

```
$ make debt
0 carried items (no CARRIED_DEBT.md ledger exists yet - nothing was read)
defect_lint: 0 violations
```

The ledger is **absent**, which is not the same claim as "the ledger is empty".
`72_DEFECT_PROTOCOL.md` §9.2 creates it at the first gate that accepts a MINOR,
and no gate has signed, so none has.

## Rollback position at time of signing

Nothing to roll back. No round was opened and nothing was signed.

Documented position (§8): roll back to the `G-1` commit plus `alembic downgrade base`.
Undo is `make db-reset` for `provenance_ci`; for `provenance`,
`alembic downgrade base && alembic upgrade head && make seed`. **That undo is no
longer safe as written**, and this is the sentence the original template said
would eventually need qualifying. `upgrade head` now includes `0009`, so the
sequence above would return a `VECTOR(1536)` schema with every embedding NULL and
an 18,035-row corpus that has to be re-embedded before retrieval returns anything.
Upgrade **by name** to `0009b`. **Cannot be undone:** the Titan vectors, if
`0009` is applied without `db/seeds/vectors.parquet` in hand.
