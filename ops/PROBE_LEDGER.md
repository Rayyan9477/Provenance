# Phase 0 probe ledger

Cluster `<cluster>` (`<cluster-id>`) · BASIC · AWS · us-east-1 · CockroachDB CCL **v26.2.5**
Run 2026-08-17 by the bootstrap SQL user. A gate reviewer reads this table first.

> ## Evidence destroyed, then regenerated. Resolved 2026-08-18.
>
> On 2026-08-17 a re-run of `make probe` on a machine with no `cockroach` CLI and no
> `PV_PROBE_DB_URL` truncated all four transcripts and rewrote
> `ops/decisions/VECTOR_INDEX_VARIANT.md` as **NO VARIANT SELECTED** — from a run that
> connected to nothing. `ops/` is untracked, so there was no `git checkout` recovery.
>
> **The transcripts were not restored from memory; every probe was re-run against the live
> cluster and the files below are that run's actual output.** Restoring asserted results from
> a previous conversation would have been fabrication, which `23_PHASE_GATES.md` §23 exists to
> prevent. Every result reproduced identically: `feature.vector_index.enabled` already true,
> Variant A accepted first try, prefix column at `seq_in_index = 1`, the planner choosing
> `vector search` for a literal query vector and full-scanning for a subquery, the view read
> succeeding while both base-table statements were refused.
>
> `G0.6` now passes both halves: `grep -c "^-- P" ops/cluster-probe.txt` returns **11**, and
> `ops/decisions/VECTOR_INDEX_VARIANT.md` carries exactly one `VARIANT:` line.
>
> `D-00-005` (the script defect — a probe that cannot connect must report "could not run",
> never "failed") is fixed. `D-00-006` (destroyed evidence) is closed by this regeneration.
>
> **The lesson is worth more than the incident.** The failing script reported *"none of the
> three variants was accepted by this cluster"* when none had been *attempted*. That reads as a
> capability verdict and would have driven `PV_RETRIEVAL_MODE` into the brute-force fallback,
> blocking the vector-indexing claim — over a missing binary. Inability to run and
> failure are opposite findings and must never share an output path.

| Probe | Question | Result | Variant / fallback taken | Transcript |
|---|---|---|---|---|
| **PB-1** | Vector indexing enabled on a managed Basic cluster | **PASS** | None needed. Setting was already `true` by default **and** is settable by the bootstrap user. | `ops/cluster-probe.txt` P2 |
| **PB-2** | Prefix-column cosine vector index created **and chosen by the planner** | **PASS** | `VARIANT: A` — first form succeeded, B and C never attempted | `ops/cluster-probe.txt` P4–P8 + planner proof; `ops/decisions/VECTOR_INDEX_VARIANT.md` |
| **PB-3** | Generated `STORED` column (plus TTL and column families) | **PASS** | None needed. `is_retrieval_eligible` will be a generated column; the boolean-plus-CHECK fallback is unused. | `ops/cluster-probe.txt` P9–P11 |
| **PB-4** | View read succeeds, base table denied | **PASS** | None needed. `PV_MCP_ENABLED` stays `true`; Phase 11 not blocked. | `ops/grant-probe.txt` |
| **PB-5** | Bedrock access to the three model ids | **PASS** (re-run 2026-08-17T22:14Z) | None needed. **Tier E** `us.anthropic.claude-haiku-4-5-20251001-v1:0`, **Tier R** `us.anthropic.claude-opus-4-6-v1`, **embeddings** `amazon.titan-embed-text-v2:0` (1024 dims, unit norm) — all invocable. Opus 5 and Sonnet 5 stay denied and nothing blocks on them. Two id-form findings: Anthropic requires inference-profile ids (`D-00-002`), every other provider requires bare ids and rejects the profile form (`D-00-040`). | `ops/bedrock-probe.txt` |
| **PB-6** | Seed clone and restore under 90 s | **CAPABILITY PASS / TIMING NOT RUN** | Mechanism confirmed available: `BACKUP INTO 'userfile://…'`, `SHOW BACKUPS`, and `RESTORE … WITH new_db_name` all succeed on this managed BASIC cluster over the Postgres wire protocol, no `cockroach` CLI and no paid backup tier required. The R6 fallback is not pre-emptively triggered. **The timing half cannot be run before T2.8** — there is no seed, and a clone time measured against an empty database is a number unrelated to the quantity the probe bounds. | `ops/restore-probe.txt` |

## What this bought

The four probes that could have forced a redesign all passed, and three of them passed without needing their fallbacks at all:

- **Vector indexing works, cosine works, and the `user_id` prefix drives filter acceleration.** `PV_RETRIEVAL_MODE` stays `VECTOR_INDEX`; the brute-force per-user scan is not needed; the vector-indexing claim is unblocked and needs no disclosure.
- **The cosine posture holds**, so `EMBEDDING_NORMALIZATION=L2_UNIT` and Variant C's L2-ordering equivalence argument are both unused.
- **The agent-safe view boundary holds.** This was the only probe whose failure stops a phase. `pv_agent_reader` on views alone, base tables denied, MCP wired read-only — the product's central safety claim is enforceable by SQL grants on this cluster, which is what the project asserts.
- **`STORED` computed columns, row-level TTL and column families are all available**, so three separate schema fallbacks stay unused.

## What is still open

| Item | Consequence if it fails | Blocks |
|---|---|---|
| **PB-6 timing** — clone wall-clock against the seeded template, unmeasured until T2.8 | Per-scenario database isolation may be unaffordable; falls back to sequential scenarios with rollback and a `hero-lite` 500-decoy commit lane | G-14 throughput only, not correctness |
| **PB-6 index survival** — whether a `VECTOR INDEX` survives BACKUP/RESTORE *and is still chosen by the planner* | Sharper than the timing question. A restore that silently invalidates the ANN index leaves every cloned scenario correct but full-scanning — right answers, no error, no warning, which is the exact failure shape of `D-06-001`. Assert `EXPLAIN` on the restored clone at T2.8. | G-14, and any retrieval measurement taken on a cloned database |

**Tier R access is no longer on this list.** It was, as `D-00-004`, filed as *"no frozen reasoning model is invocable"*. The re-run disproved the statement: `us.anthropic.claude-opus-4-6-v1` invokes, and `us.anthropic.claude-sonnet-4-6` invokes behind it. The original probe had tested only the Anthropic family and drawn a catalogue-wide conclusion from a one-family sample — the same shape of error as `D-00-005`, where a probe that could not connect reported that a capability had failed. **A negative result is only as broad as the search that produced it.** What survives is a disclosure obligation, not a blocker: the build ships on Opus 4.6 and must say so.

## Findings that changed nothing but must not be forgotten

**The ANN query vector must be a literal or bound parameter.** A correlated subquery in `ORDER BY` produces a full scan with correct results, no error, and no warning. Reproduced at 1024 and 3 dimensions; survives `ANALYZE`. Logged as `D-06-001` against Phase 6. This is not in any specification and it is the kind of defect that passes every functional test.

**Grants must be revoked before a role can be dropped.** `DROP ROLE` failed with *"grants still exist on provenance, provenance.public"* until `REVOKE ALL` ran first. The Phase 11 revoke/restore harness will hit the same ordering.

**PB-4's denial wording is build-specific.** This build says *"does not have SELECT privilege"*; the probe script's original matcher tested for *"no SELECT privilege"* and would have read a correct denial as a failure on the one probe that stops a phase. Matcher widened before the probe ran.
