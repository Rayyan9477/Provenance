# Gate G-6 — embeddings and retrieval

> **Status: NOT SIGNED. Six of the seven assertions have been proved
> individually and their transcripts are committed; the battery that would run
> them together does not exist.** Instantiated from
> `docs/quality/23_PHASE_GATES.md` §4.1 by task `T0.3`. Every assertion row is
> pre-filled, so an assertion that is never run shows up as an **omission**
> rather than as an absence (§3 rule 2).
>
> The rows below were rewritten on 2026-08-31. They previously read `NOT RUN --
> phase not started` for all seven while `ops/tdd/EXPLAIN_G6.2.txt`,
> `ops/tdd/SABOTAGE_T5-T6.txt` and `ops/tdd/GREEN_T5-T6.txt` sat in the same
> repository recording the opposite. Under-reporting is not the safe direction
> of error: it makes committed evidence unfindable, and a reviewer who reads
> this file first concludes the work does not exist.
>
> **Two things reject this gate independently of any row in the table.**
> `make gate-6` is a deliberate non-zero stub naming the phase that owns it, so
> no battery run, no round and no reviewer signature exists -- the results below
> are read off separately committed evidence, which is weaker than one battery
> under one sha. And `D-06-001` is still **OPEN** as a MAJOR against this phase;
> §4.3 rejects any report carrying one.

Commit: `no round opened`       Branch: `main`
Builder: Integrator             Reviewer: **none, no round was opened**
Round opened: `n/a`             Round closed: `n/a`
Verdict: **NOT SIGNED**

## Environment of record

- Checkout: reused working tree, and the evidence spans **two shas**. The
  retrieval transcripts were captured at `33e593c` on 2026-08-18 and 2026-08-19;
  the corpus and eval figures at `80a6a3e` on 2026-08-30. Saying that rather
  than picking one is the point: nothing below was produced by a single run.
- Database: `<cluster>`, CockroachDB CCL v26.2.5, BASIC, AWS us-east-1.
  `tests/retrieval` and both EXPLAIN transcripts ran against `provenance_ci`;
  the corpus counts and the eval numbers were taken against `provenance`.
- **The cluster is at `0009b` and migration `0009` is deliberately unapplied.**
  That is what makes the rows below readable at all: `0009` widens the vector to
  `VECTOR(1536)` for Gemini and leaves every `embedding` NULL, so the 18,035
  Titan vectors these assertions are measured over would be gone. `STATUS.md`
  §8 and `README.md` both record the decision; it is repeated here because a
  reader who runs `alembic upgrade head` before re-running these assertions will
  get an empty corpus and no error.
- Deployed target: two Cloud Run services are serving (Phase 13), and **nothing
  below was taken against them.** Retrieval is not reachable from the deployed
  API. See the next section.

## What this gate cannot say, and says instead

`app/retrieval/` holds all eight stages of `13_RETRIEVAL_SPEC.md` and they are
individually tested. **No module composes them.** `pipeline.py` is a `STAGES`
tuple, a `call_order()` function and one boolean constant; nothing runs A
through G end to end. So `internal.retrieve` is unbound, retrieval is not on the
live request path, and the Judge Mode counterfactual passes an empty `evidence`
array to its MEMORY ON side.

Every PASS below is therefore a claim about a **stage or a statement**, not
about a working retrieval subsystem. The distinction is the whole reason this
section sits ahead of the table: seven green rows over eight unassembled stages
would read to a judge as "retrieval works", and it does not. `STATUS.md` §4 is
the measured account of what is missing and what building it would cost.

## Exit assertions

Battery: `make gate-6`. Entry criteria: `G-2` signed with the vector index present;
`13_RETRIEVAL_SPEC.md` read.

**Neither entry criterion is met.** `G-2` is not signed, and no other gate is
either, so these assertions were proved individually while the gate before them
was still open. The vector index *is* present, and that half is evidenced by
`G2.4` and by the `SHOW INDEXES` precondition block at the head of
`ops/tdd/EXPLAIN_G6.2.txt`.

| ID | Result | Log |
|---|---|---|
| G6.1 | **PASS** -- `amazon.titan-embed-text-v2:0` / 1024 / `v1`, **1 distinct version** over 18,035 rows, **0 without an embedding** | `evals/reports/latest.json`, corpus note |
| G6.2 | **PASS** -- `vector search` on `evidence_items@evidence_embedding_ann_idx`, `prefix spans` present, **no `FULL SCAN`**, against the shipped statement with the vector bound | `ops/tdd/EXPLAIN_G6.2.txt` |
| G6.3 | **PASS, all four parts** -- (a) 0 of 200, (b) the index is named, (c) 0 of 3 fixtures survive, (d) the positive control fires | `ops/tdd/GREEN_T5-T6.txt` §2, `ops/tdd/EXPLAIN_G6.2.txt` |
| G6.4 | **PASS** -- the static scan finds every evidence statement in the tree and each one binds `user_id`, `tenant_id` and a retraction predicate | `ops/tdd/GREEN_T5-T6.txt` §1 |
| G6.5 | **CANNOT RUN** -- the threshold is written against a measurement no credential on this machine can take. Nearest measured figures below | `evals/reports/latest.json`, suite `RET` |
| G6.6 | **PASS, with the embedder stubbed** -- clearing the cache costs a call and returns the same bytes. No live model was in the path | `ops/tdd/GREEN_T5-T6.txt` §1 |
| G6.7 | **PASS** -- 1 failed, 5 passed under the sabotage, and the failing test is the one the gate intends | `ops/tdd/SABOTAGE_T5-T6.txt` |

What each one asserts (§12):

- **G6.1** — one frozen `embedding_version` over every embedded row; exactly one group, e.g. `amazon.titan-embed-text-v2:0/1024/v1,18035`.
- **G6.2** — `EXPLAIN` names `evidence_embedding_ann_idx`. **A "full scan" line here is a FAILURE even if the results are correct.** Run it against the **production query shape**, parameter binding included — see the open defect `D-06-001`.
- **G6.3** — DB test 12, all four parts: (a) 0 of 200 returned ids belong to `iso-a`/`iso-b` over the 18,035-row corpus; (b) EXPLAIN names the index; (c) none of the 3 retraction fixtures appear; (d) **positive control** — with the retraction predicate removed, `sid('evidence','isp-wrong-term-date')` appears in the top 20. (d) failing means (c) was passing vacuously.
- **G6.4** — every retrieval statement carries a `user_id` predicate.
- **G6.5** — retrieval eval thresholds **asserted**, not merely reported: case R@1 >= 0.85, R@3 >= 0.95, and the hero scenario a HIT.
- **G6.6** — the cache is a cache, not a correctness dependency: cache cleared, identical vector recomputed.
- **G6.7** — sabotage `retrieval.predicates.retraction_filter`: G6.3(c) goes red, `exit=1`.

### G6.1

`evals/reports/latest.json` carries the corpus note, computed at query time
against `provenance` rather than read from configuration:

```
embedding space: amazon.titan-embed-text-v2:0 version v1, 1 distinct version(s)
in the corpus. Nothing here embeds text; every query vector is a vector already
stored in this corpus.

corpus counted at query time: 18035 evidence rows (18032 ACTIVE, 3 not),
34 hero artifacts, 16000 hero decoys, 0 without an embedding.
```

`0 without an embedding` is the half that makes the version count mean
something. One distinct version over a corpus where half the rows carry no
vector at all would be the same string and a much smaller fact.

### G6.2 -- the assertion `D-06-001` exists to make non-vacuous

`ops/tdd/EXPLAIN_G6.2.txt` runs `EXPLAIN` over the text that
`services.control_plane.app.retrieval.ann.render_ann_sql()` actually emits, with
parameters from `ann.bind()`, so the query vector reaches the planner through
psycopg's bind path exactly as it does in production. The transcript records
both halves:

```
VERDICT A (bound parameter, the production shape):
  names evidence_embedding_ann_idx : True
  contains 'FULL SCAN'             : False
  contains 'prefix spans'          : True
  contains 'vector search'         : True

VERDICT B (the same vector as a correlated subquery):
  names evidence_embedding_ann_idx : False
  contains 'FULL SCAN'             : True
  contains 'vector search'         : False
```

Verdict B is why this row is worth reading twice. `D-06-001` still reproduces on
this cluster **with the index present**: both forms return correct results, and
only the plan differs. The rule is enforced by `ann.bind()` refusing SQL-shaped
input rather than by a comment, and the regression guard is
`test_query_vector_as_a_subquery_silently_loses_the_index`.

**The defect is nevertheless still OPEN**, and that is a rule about who may
close a record rather than a doubt about the transcript. Its **Closes when**
clause has two limbs: the ANN entry point takes a vector argument rather than
deriving one, and `G6.2` names the index with `prefix spans` present. Both are
satisfied by the evidence above. What is missing is the §7.4 close-proof
counterfactual and the change to the owning file, `docs/specs/13_RETRIEVAL_SPEC.md`.
Closing the record here, inside the gate report the record blocks, would be the
gate marking its own homework. It stays OPEN, and it rejects this gate.

### G6.3 -- four parts, and the one that licenses the other three

All four are `db`-marked tests in `tests/retrieval/`, green in the 25-test db
lane recorded in `ops/tdd/GREEN_T5-T6.txt` §2:

| Part | Assertion | Test |
|---|---|---|
| (a) | 0 of 200 returned ids belong to `iso-a`/`iso-b`, queried with `iso-b`'s honeypot vector, which is the single worst case | `test_ann_never_returns_another_users_evidence` |
| (b) | `EXPLAIN` names the index | `ops/tdd/EXPLAIN_G6.2.txt`; `test_user_id_prefix_constrains_the_ann_partition` |
| (c) | none of the 3 retraction fixtures appear, each queried with its own embedding so it would be rank 1 unfiltered | `test_no_retraction_fixture_survives_the_production_statement` |
| (d) | **positive control** -- with `render_ann_sql(retraction_filter=False)`, `sid('evidence','isp-wrong-term-date')` is in the top 20 | `test_retracted_evidence_is_reachable_without_the_filter_positive_control` |

Read (d) first. It is the assertion that stops (c) from passing vacuously, and
`test_the_corpus_is_the_one_the_gate_describes` is the second vacuity guard
underneath both: 18,035 rows across exactly three seeded partitions, asserted
out loud rather than implied by four green tests.

Measured again independently on `provenance` by the eval harness on 2026-08-30,
which is a different database, a different sha and a different code path:
`RTR-02 PASS -- 3 non-ACTIVE row(s) queried with their own vector (cosine
distance 0.0, rank 1 in any unfiltered search); 0 resurfaced within the top 100`.

### G6.4

`tests/retrieval/test_no_unscoped_sql.py` walks the retrieval modules, finds the
statements, and asserts on each: `test_the_scan_finds_statements_at_all` is the
vacuity guard, because a scan that finds nothing passes every other assertion in
the file; then `test_every_evidence_statement_binds_user_id`,
`test_every_evidence_statement_binds_tenant_id` and
`test_every_evidence_statement_filters_retraction_status`. It is a static scan,
so what it proves is that no unscoped evidence statement exists in the tree. It
does not prove that a scoped one was executed.

### G6.5 -- CANNOT RUN, and the nearest measured numbers

The assertion as written wants **natural-language query recall**: case R@1 >=
0.85, R@3 >= 0.95, and the hero scenario a HIT. None of the three can be taken
today.

- The corpus is 18,035 Titan vectors at 1024 dimensions and **there is no Titan
  credential on this machine.** A query embedded by any other model lands in a
  different space; its cosine distances stay ordered and stop meaning anything.
  The harness reports this as `RET-02 CANNOT RUN`, naming the credential it
  waits on, rather than scoring around it.
- The hero scenario is `RET-03 CANNOT RUN`. `demo/artifacts/northline-june-invoice.eml`
  is deliberately absent from `source_artifacts` because the demo ingests it
  live to create the conflict. Ranking a row that merely mentions the invoice
  would be a different question answered in the same table.

What **is** measured is `RET-01`, a document-to-document probe: each hero row's
own stored vector as the query, scored against its case-mates over the decoy
field.

```
queries 31   gold_pairs 110   gold_never_ranked_within_100 25
recall@1  0.2849    recall@5  0.6989    recall@10 0.7715
recall@20 0.7715    recall@50 0.7903    recall@100 0.8011
MRR@100 0.7609      decoy_share@20 0.6774
```

**Those numbers do not clear G6.5's thresholds, and they are also not the
measurement G6.5 names.** A document vector carries a whole page; a query vector
carries a question. Reporting `recall@1 = 0.2849` as a failure of "case R@1 >=
0.85" would be as wrong as reporting `recall@20 = 0.7715` as a pass. Both read
one measurement as an answer to a different question. The honest verdict is
CANNOT RUN with the neighbouring numbers printed beside it, which is what this
row does.

### G6.6 -- a pass with its stub disclosed

`test_same_normalised_text_yields_the_identical_vector` and
`test_clearing_the_cache_yields_the_identical_vector_recomputed` both pass, in
the hermetic unit lane, against a `RecordingEmbedder`. What that proves is the
whole of the cache's contract: the second call is served from the cache (one
call recorded), clearing it forces a recomputation (two calls recorded), and the
bytes are identical across the clear. What it does **not** prove is that a live
embedding model returns the same vector twice. No model was in the path, by
design, because the unit lane opens no sockets.

The third test in that block is the one worth keeping: a cache miss in offline
mode raises `EmbeddingCacheMissError` rather than returning a zero vector. A
database of zero vectors passes every row-count assertion and returns nonsense.

## Tests green

Required at this gate (§12): `tests/retrieval/`, DDL §19 test 12, the
`evals/retrieval` threshold run.

From `ops/tdd/GREEN_T5-T6.txt`, captured 2026-08-18T21:13:38Z at `33e593c`, with
**0 live model calls** -- every query vector is read from
`evidence_items.embedding`, which the seed already paid for:

```
unit + retrieval lane (hermetic; no database, no network, no credentials)
  137 passed, 25 deselected in 0.74s

db lane: tests/retrieval (marked db)
  25 passed, 120 deselected in 73.30s

pre-existing Phase 2 retrieval-SQL guard (T2.2), still green
  17 passed in 17.73s

lint, types, contracts, transaction purity
  All checks passed! / 30 files already formatted /
  Success: no issues found in 15 source files /
  Contracts: 5 kept, 0 broken. /
  scanned 7 transaction callbacks, 0 network constructs found
```

Canon path mapping: DDL §19 test 12 is the four-part G6.3 assertion above, and it
lives in `tests/retrieval/test_isolation.py` and
`tests/retrieval/test_retraction_filtering.py` rather than under `tests/db/`.

The `evals/retrieval` threshold run is the third requirement and it is **not
green: it is CANNOT RUN**, for the reasons under G6.5. Listing it here as
required-and-unavailable rather than omitting it is §3 rule 2.

**Deferred tests, with the phase that closes them:** none, and that is not the
good news it looks like. Nothing composes the eight stages, so there is no
end-to-end retrieval test waiting on a later phase -- the test is unwritten
rather than scheduled, because the thing it would exercise is unbuilt. A
scheduled deferral is not debt; an unwritten test for an unassembled subsystem
is the gap this gate exists to expose.

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `retrieval.predicates.retraction_filter` | `tests/retrieval/test_retraction_filtering.py` | **Yes -- 1 failed, 5 passed**, `test_layer_three_excludes_what_a_lost_sql_predicate_would_admit` |

Sabotage matrix entry count at this gate: **13** (previous gate: 13). The count
is taken over the whole of `tests/sabotage_matrix.yaml`, which later phases have
grown; §10.2 detector 2 requires only that it never shrinks.

**The counter-evidence recorded in `ops/tdd/SABOTAGE_T5-T6.txt` changes how this
row must be read, and it is the most useful thing in this file.** §13.3 has
three enforcement layers for the retraction filter and this symbol is layer 3
only. Layer 1 is the SQL predicate in the canonical ANN statement; layer 2 is
baked into `agent_evidence_retrieval_v1`. So a sabotage run pointed at the
layer-1 assertion alone is **GREEN** with the symbol neutered -- the SQL still
excludes the fixtures -- and that green would have been read as G6.7 passing
while proving nothing. The matrix entry therefore names the file that also
carries the layer-3 assertion, and the transcript records the green run beside
the red one so the distinction survives being forgotten.

Two transcripts record this sabotage and they name **different failing tests in
the same file**. `ops/tdd/GREEN_T5-T6.txt` §6, captured 2026-08-18, names the
layer-1 test; `ops/tdd/SABOTAGE_T5-T6.txt` and the matrix entry, both
2026-08-19, name the layer-3 test. Both report `1 failed, 5 passed`. The later
pair is the record, because it was written after the three-layer structure was
understood. The discrepancy is left visible rather than tidied away: a reviewer
who notices it should be able to see which run superseded which.

## Defect ledger

`72_DEFECT_PROTOCOL.md` §11.3. `make defects PHASE=6`, run 2026-08-31:

```
OPEN BLOCKER: 0  OPEN MAJOR: 1  OPEN MINOR: 0  CARRIED: 0  REJECTED: 0   [phase 6]
defect_lint: 0 violations
```

Mandatory lenses at this gate (§3.1): `L-VAC`, `L-DRIFT`, `L-INV`, `L-BND` — 4.

**Open:** `D-06-001` (MAJOR) -- an ANN query vector supplied as a
correlated subquery silently produces a FULL SCAN. Both limbs of its **Closes
when** clause are satisfied on the evidence under G6.2; what is missing is the
§7.4 close-proof and the owning-file change, and neither is this report's to
supply. A MAJOR never carries: this gate cannot sign with it open (§4.3).

## Standing questions (§22.3) — answered honestly

- **Q1 · What did I claim without running?** The battery. `make gate-6` is a
  stub, so no row in the table was produced by running G-6; each was produced by
  running the thing the row describes, at one of two shas, against two
  databases. That is weaker evidence than one battery under one sha, and the
  table should be read that way.
- **Q2 · What is mocked that should be real?** The embedder, under G6.6, which
  the row discloses. And the whole live path: because nothing composes the
  stages, every assertion here is taken against a statement or a stage invoked
  directly by a test, never against a request arriving at the deployed API.
- **Q3 · Which invariant is currently unproven?** None of the five is owned by
  this phase, and `tools/invariant_map_check.py` reports **5 mapped, 0 UNPROVEN**
  tree-wide. What is unproven *here* is the product claim the phase exists to
  support: that retrieval finds the right evidence for a user's question. G6.5
  is the assertion that would have measured it, and it cannot run.
- **Q4 · What would a hostile judge click on first?** The vector-indexing claim,
  and then `internal.retrieve`. The index claim holds and
  `ops/tdd/EXPLAIN_G6.2.txt` is the proof. The second click is the damaging one:
  the method is unbound and the counterfactual's MEMORY ON side passes an empty
  `evidence` array. `STATUS.md` §4 says so before a judge has to find it.
- **Q5 · What passed because of seeded state rather than logic?** G6.1 and
  G6.3(a) are both statements about the seeded corpus, and both are guarded:
  `test_the_corpus_is_the_one_the_gate_describes` asserts 18,035 rows over three
  partitions, and `0 without an embedding` is measured rather than assumed. If
  the seed were partial, G6.1's "1 distinct version" would still be true and
  would mean much less.
- **Q6 · What did I not look at?** `rerank.py`, `temporal.py`, `grounding.py`,
  `scope.py`, `identity.py`, `relational.py` and `context.py` have unit tests and
  were not re-read for this report. Their composition is the gap, and nobody has
  looked at that because it does not exist.
- **Q7 · If this phase is secretly broken, how and when would I find out?** By
  the ANN statement losing its index shape while nobody notices, which is
  exactly `D-06-001`: correct results, no error, no warning, and at demo scale
  no latency a human perceives. The detector is `G6.2` run against the shipped
  statement text, and it works only because `ann.render_ann_sql()` is the single
  source of that text. If a second copy of the statement is ever written
  anywhere, the detector goes quiet against the copy.
  `test_only_one_module_carries_the_vector_operator` is the guard on the guard.

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

Documented position (§12): roll back to the `G-3` commit; revert the retrieval module.
If the ANN index is the problem, `DROP INDEX evidence_embedding_ann_idx` and fall back
to a brute-force scan over the user's partition — survivable for a demo at ~16,000 rows,
and it **must be disclosed** in Judge Mode and in the submission, never presented as
vector indexing. **Cannot be undone cheaply:** re-embedding 18,000 rows costs model
spend and tens of minutes. `db/seeds/vectors.parquet` holds 18,043 cached Titan vectors
and is the only reason the corpus survives a reseed without paying for it again;
migration `0009` invalidates that file, which is why applying it is a decision and not
a step.
