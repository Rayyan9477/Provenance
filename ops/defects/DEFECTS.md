# Provenance — defect log

Severity rules are owned by `docs/EXECUTION/72_DEFECT_PROTOCOL.md` §2. In short: **BLOCKER** violates an invariant, crosses a tenant or capability boundary, permits an external effect from uncommitted state, or renders a UI/trace element with no backing row. **MAJOR** is wrong behaviour that does not violate an invariant, a vacuously-passing assertion, or a spec/code divergence. **MINOR** may carry as debt with a named owner and a closing phase.

A defect without a reproduction is a rumour. Every row must carry the command and the observed-versus-expected output.

BLOCKER and MAJOR must close before their phase's gate signs. Nothing carries past G-14.

---

## Open

| id | phase | lens | sev | summary | status |
|---|---|---|---|---|---|
| `D-00-004` | 0 | `L-DRIFT` | **MAJOR(M1)** | Tier R target (Opus 5) denied; shipping on Opus 4.6, which carries a Phase-15 disclosure obligation | OPEN |
| `D-00-040` | 0 | `L-DRIFT` | **MAJOR(M3)** | Bedrock identifier form is provider-dependent and mirror-imaged; one uniform rule cannot call both families | AWAITING_REVERIFY |
| `D-00-041` | 0 | `L-VAC` | **MAJOR(M2)** | On gitleaks 8.21.x every top-level allowlist is ignored for custom rules, so the `pv-*` rules ran unallowlisted | CLOSED |
| `D-00-042` | 0 | `L-VAC` | **BLOCKER(B4)** | `allowlist.paths` is a whole-file skip; eight files including the probe transcripts were never scanned | CLOSED |
| `D-00-043` | 0 | `L-VAC` | **MAJOR(M2)** | G0.3b was the same scan run twice and could not see `ops/`, which was untracked | CLOSED |
| `D-00-039` | 0 | `L-VAC` | **MAJOR(M2)** | Workspace packages are not installable with plain `pip install -e`; inter-package deps pinned `==1.0.0` resolve against PyPI | OPEN |
| `D-06-001` | 6 | `L-DRIFT` | **MAJOR(M2)** | ANN query vector supplied as a correlated subquery silently defeats vector-index selection | OPEN |
| `D-00-002` | 0 | `L-DRIFT` | **MAJOR(M3)** | Every frozen Anthropic model id was un-invocable; Bedrock requires inference-profile ids | CLOSED |
| `D-00-003` | 0 | `L-VAC` | **MAJOR(M2)** | PB-5 invocation never executed; access unproven | CLOSED |
| `D-00-005` | 0 | `L-VAC` | **MAJOR(M1)** | `make probe` truncates committed evidence before any connectivity check | CLOSED |
| `D-00-006` | 0 | `L-DRIFT` | **MAJOR(M1)** | Phase 0 evidence destroyed; the probe ledger asserts PASS against empty transcripts | CLOSED |
| `D-00-007` | 0 | `L-VAC` | **MAJOR(M2)** | `make lint`'s import-linter fallback exits 0 having evaluated zero contracts | CLOSED |
| `D-00-008` | 0 | `L-VAC` | **MAJOR(M1)** | Unpinned ruff makes `ruff format --check .` return a different verdict by install date | CLOSED |
| `D-00-009` | 0 | `L-RENDER` | **MAJOR(M1)** | `tools/scrub.py` had no rule for five credential shapes this build emits | CLOSED |
| `D-00-010` | 0 | `L-RENDER` | **MAJOR(M2)** | gitleaks allowlist C excused any gate-log line carrying a redaction marker | CLOSED |
| `D-00-011` | 0 | `L-VAC` | **MAJOR(M2)** | The CI evidence check never opened a log and ignored the whole `S1`..`S10` family | CLOSED |
| `D-00-012` | 0 | `L-VAC` | **MAJOR(M1)** | The unit-lane guard did not block `gethostbyname`; a `unit` test resolved a real host | CLOSED |
| `D-00-013` | 0 | `L-VAC` | **MAJOR(M2)** | Three guard mechanisms had no test that fails when the mechanism is deleted | CLOSED |
| `D-00-014` | 0 | `L-DRIFT` | **MAJOR(M2)** | `testpaths` excluded `tools/`, so T0.3's 28 declared tests were never collected | CLOSED |
| `D-00-015` | 0 | `L-VAC` | **MAJOR(M2)** | `make debt` printed `0 carried items` and exited 0 with no ledger file to read | CLOSED |
| `D-00-016` | 0 | `L-VAC` | **MAJOR(M2)** | G0.7's exit-code assertion is a dead statement; the gate can fail on only one of its two claims | CLOSED |
| `D-00-017` | 0 | `L-DRIFT` | **MAJOR(M3)** | The defect ledger failed its own linter, which §11.3 makes a binding gate precondition | CLOSED |
| `D-00-018` | 0 | `L-DRIFT` | **MAJOR(M3)** | `extra="forbid"` does not reject an undeclared environment variable, as §12.11 claims | OPEN |
| `D-00-019` | 0 | `L-RENDER` | **MAJOR(M1)** | `ValidationError.errors()` renders the raw environment, credentials in plaintext | CLOSED |
| `D-00-020` | 0 | `L-DRIFT` | **MAJOR(M3)** | PB-5 probed the superseded bare `anthropic.claude-*` ids | CLOSED |
| `D-00-021` | 0 | `L-RENDER` | **MAJOR(M3)** | The probe scrubber preserved the userinfo *user*, which `tools/scrub.py` deliberately redacts | CLOSED |
| `D-00-022` | 0 | `L-DRIFT` | **MAJOR(M3)** | Two probe ledgers existed at two paths with contradictory verdicts | CLOSED |
| `D-00-023` | 0 | `L-VAC` | **MAJOR(M2)** | `.SHELLFLAGS` is inert on GNU Make 3.81, the only `make` on the build machine | AWAITING_REVERIFY |
| `D-00-024` | 0 | `L-RENDER` | **MAJOR(M2)** | gitleaks is not installed, so G0.3 cannot run and `tools/scrub.py` is the only filter | CLOSED |
| `D-00-025` | 0 | `L-RENDER` | **MAJOR(M3)** | `make gate-0` ran one of the two scans `.gitleaks.toml` declares G0.3 to be | CLOSED |
| `D-00-026` | 0 | `L-VAC` | **MAJOR(M2)** | `tools/gate.sh` did not check the scrubber's status on the command line | CLOSED |
| `D-00-027` | 0 | `L-VAC` | **MAJOR(M3)** | `make` does not run under PowerShell and T0.7's both-shells transcript does not exist | OPEN |
| `D-00-028` | 0 | `L-DRIFT` | **MAJOR(M3)** | `D-00-001` was never filed and T0.3's seven pack-level discrepancies are absent | OPEN |
| `D-00-029` | 0 | `L-DRIFT` | **MAJOR(M3)** | `ops/cluster-provision.txt` and `ops/decisions/CLUSTER.md` are named deliverables that do not exist | OPEN |
| `D-00-030` | 0 | `L-DRIFT` | **MAJOR(M3)** | `ops/probes/phase0-probe.sh` does not exist although T0.3 requires both stubs from the start | OPEN |
| `D-00-031` | 0 | `L-DRIFT` | **MAJOR(M3)** | `.pre-commit-config.yaml` and `gitleaks.yml` are absent and the deviation is unrecorded | OPEN |
| `D-00-032` | 0 | `L-VAC` | **MINOR(m1)** | The `arn-account-id` scrub rule is entirely subsumed by `bare-account-id` | OPEN |
| `D-00-033` | 0 | `L-RENDER` | **MINOR(m1)** | `.gitignore` missed `evals/reports/*.json`, six credential shapes, and spelled `lib64/` as `lib60/` | CLOSED |
| `D-00-034` | 0 | `L-DRIFT` | **MINOR(m1)** | `NOTICE` attributes LangGraph to two graphs that appear nowhere in the design pack | CLOSED |
| `D-00-035` | 0 | `L-VAC` | **MINOR(m1)** | `sabotage_guard --min-count 0` reports a missing file as a false numeric comparison | OPEN |
| `D-00-036` | 0 | `L-RENDER` | **MINOR(m1)** | `tools/defect_lint.py` never scrubs a reproduction although §5.1 requires it | OPEN |
| `D-00-037` | 0 | `L-RENDER` | **MINOR(m1)** | The real SQL username and cluster FQDN are committed to a repository that becomes public | OPEN |
| `D-00-038` | 0 | `L-RENDER` | **MINOR(m1)** | Allowlist C's `never committed` regex was the only one requiring no redaction marker | CLOSED |
| `D-00-045` | 0 | `L-RENDER` | **MAJOR(M1)** | `tools/scrub.py` had no rule for the Google AI Studio key, which the Gemini API takes as a URL query parameter | AWAITING_REVERIFY |
| `D-00-046` | 0 | `L-VAC` | **MAJOR(M2)** | PB-G2 recorded PASS for any call that did not raise, so three ids that answered nothing were reported as evidence | AWAITING_REVERIFY |
| `D-00-047` | 0 | `L-VAC` | **MAJOR(M2)** | PB-G6 probed multimodal with a 1x1 transparent PNG the API rejects, and recorded the refusal as a capability FAIL | AWAITING_REVERIFY |
| `D-00-048` | 0 | `L-VAC` | **MAJOR(M2)** | `probe_verdict` matched the model id as a bare substring, so a FAILING id reported PASS from a longer id's line | AWAITING_REVERIFY |
| `D-12-001` | 12 | `L-RENDER` | **MINOR(m1)** | `TimePair`'s label and value rendered welded together as `RECORD TIMELAST ACTIVITY ...` | AWAITING_REVERIFY |
| `D-12-002` | 12 | `L-RENDER` | **MINOR(m1)** | A `pv-sr-only` span escaped `.pv-table-scroll` and scrolled the whole page sideways at 390px | AWAITING_REVERIFY |
| `D-00-049` | 0 | `L-DRIFT` | **MAJOR(M2)** | `.env.example` omits `PV_LOCAL_AUTH_SECRET`, which `PV_PLATFORM=local` refuses to start without | AWAITING_REVERIFY |
| `D-08-001` | 8 | `L-VAC` | **MAJOR(M1)** | `make run-api` loaded no environment, so the server exited with 8 missing-field errors and `input_value={}` | AWAITING_REVERIFY |
| `D-08-002` | 8 | `L-DRIFT` | **MAJOR(M1)** | `--loop asyncio` SELECTS the proactor loop on Windows, so the API ran `db_ok=false` against a healthy cluster | AWAITING_REVERIFY |
| `D-08-003` | 8 | `L-INV` | **BLOCKER(B2)** | A capability proof verifies only during the wall-clock second it was issued, so TRIGGER_EVALUATION and ACTION_INTENT fail intermittently | AWAITING_REVERIFY |
| `D-08-004` | 8 | `L-VAC` | **MAJOR(M2)** | `internal.submit_proposal` called `unbound()` with a key absent from the register, raising `KeyError` instead of the typed refusal | AWAITING_REVERIFY |
| `D-12-003` | 12 | `L-DRIFT` | **BLOCKER(B3)** | The web contract declared four field shapes the API does not send; nine live routes 500'd, including every case docket | AWAITING_REVERIFY |
| `D-12-004` | 12 | `L-RENDER` | **MAJOR(M1)** | LIVE mode was unreachable: every read runs server-side, the API refuses anonymous reads, and nothing supplied a token | AWAITING_REVERIFY |
| `D-07-001` | 7 | `L-VAC` | **BLOCKER(B4)** | `ExtractionResult` cannot be sent to Gemini at all; 252 router tests are green because every one sends a `ToyOutput` | AWAITING_REVERIFY |
| `D-07-002` | 7 | `L-DRIFT` | **MAJOR(M1)** | `GRAPH_NAME_INGESTION` has never been a value `ck_agent_runs_graph` admits, so the first `agent_runs` INSERT would fail on a CHECK | AWAITING_REVERIFY |
| `D-07-003` | 7 | `L-INV` | **MAJOR(M2)** | `validate_extraction` requires character-exact span offsets no language model can produce; every candidate exhausted its repair | OPEN |
| `D-07-004` | 7 | `L-INV` | **MAJOR(M2)** | `build_memory_proposal` raises past the graph boundary, contradicting `run_ingestion`'s documented "the loop never raises" | OPEN |
| `D-02-005` | 2 | `L-INV` | **MAJOR(M1)** | `_fetch_all` turns a mapping row into its own column names, silently, and `strict=True` cannot catch it | AWAITING_REVERIFY |

### Triage round 1 — how these severities were decided

`72_DEFECT_PROTOCOL.md` §4.1 is an **ordered** rule and the triager applies it
rather than the severity a hunter proposed. `B1`–`B4` all name runtime product
behaviour — an invariant violation, a tenant or user boundary crossing, an
external side effect from uncommitted state, a UI element with no backing row.
Phase 0 has no runtime and no UI, so every finding in this round answers `B1`–`B4`
**no** and lands on `M1`, `M2`, `M3` or `m1`. Three hunter findings were
proposed as BLOCKER (`D-00-005`, `D-00-007`, `D-00-009`, and the allowlist half of
`D-00-010`); each is recorded at the severity the ordered rule gives, with the
rule id in the `Sev` cell, because §2 forbids the triager overruling the rule and
§4.2 rule 3 does not apply — the reproductions answer `B1`–`B4` unambiguously
rather than leaving them unanswerable.

**This changes nothing about what has to happen before `G-0` signs.** §4.3: a
gate report with an open BLOCKER **or MAJOR** against its own phase is
`REJECTED`, not `SIGNED WITH CARRIED DEBT`. Twenty-seven MAJOR records above are
`OPEN` or `AWAITING_REVERIFY`, and §7.4 is explicit that `AWAITING_REVERIFY`
"blocks the gate exactly as OPEN does". Only the seven `MINOR(m1)` rows are
carriable, and only with a named owner and a closing phase.

`AWAITING_REVERIFY` on the rows below means: the fix is applied in the working
tree, and the close-proof does not exist yet. §7.4 — "No close-proof, no
`CLOSED`" — and §2 gives closing to the reviewer, not to the triager or the
fixer. Each record names the assertion that will close it.

---

### `D-00-004` — Tier R target denied; shipping on Opus 4.6 with a disclosure obligation

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M1) · **Found** 2026-08-17 · **Owning file** `packages/python/provenance_contracts/src/provenance_contracts/settings.py` · **Status** OPEN

> **Re-triaged 2026-08-18, BLOCKER(B4) → MAJOR(M1).** The record was filed as
> *"no frozen reasoning model is invocable"* and that statement is false. The
> original probe tested only the Anthropic family and drew a catalogue-wide
> conclusion from a one-family sample. A full re-probe (`ops/bedrock-probe.txt`,
> 2026-08-17T22:14Z) shows `us.anthropic.claude-opus-4-6-v1` invoking, with
> `us.anthropic.claude-sonnet-4-6` reachable behind it and seven third-party
> families reachable beyond that.
>
> `B4` required a rendered claim with no row behind it. There is no such claim:
> `DEFAULT_REASONING_MODEL_ID` is `us.anthropic.claude-opus-4-6-v1`, `.env`
> agrees, and `CANONICAL_DECISIONS.md` now records Opus 4.6 as the shipped
> configuration rather than a temporary substitution. What survives is `M1` —
> wrong behaviour that violates no invariant — and it is a *documentation*
> obligation owed at Phase 15, not a runtime one.
>
> **The generalisable error is in the search, not the model access.** A negative
> result is only as broad as the search that produced it. This is the same shape
> as `D-00-005`, where a probe that could not connect reported that a capability
> had failed. Both turned an untested region of the space into a stated absence.

> **Severity rule, recorded (triage round 1, 2026-08-17).** The rule id is `B4`.
> The grant itself lives in the AWS account and not in this repository, but
> `72_DEFECT_PROTOCOL.md` §5.2 requires exactly one repository-relative path —
> "the file whose change makes the reproduction stop reproducing" — and that is
> `settings.py`, whose `DEFAULT_REASONING_MODEL_ID` is the single line that
> moves when the grant lands. `B4` rather than `M1`: the failure this record
> exists to prevent is a submission that renders "Tier R = Opus 5" from a
> constant while the runtime called 4.6, which is a claim on screen with no
> row behind it. The record's own **Closes when** clause says the same thing.

**Reproduction.**

```
aws bedrock-runtime converse --region us-east-1 --model-id us.anthropic.claude-opus-5 ...
AccessDeniedException: anthropic.claude-opus-5 is not available for this account.
```

Denied through both the `us.` and `global.` inference profiles, so this is a **model-access grant** problem, not an identifier problem. Probed the neighbourhood to establish what is reachable:

| Candidate | Result |
|---|---|
| `us.anthropic.claude-opus-5` | DENIED |
| `us.anthropic.claude-opus-4-8` | DENIED |
| `us.anthropic.claude-opus-4-7` | DENIED |
| `us.anthropic.claude-sonnet-5` | DENIED |
| `us.anthropic.claude-fable-5` | DENIED |
| **`us.anthropic.claude-opus-4-6-v1`** | **OK** |
| `us.anthropic.claude-sonnet-4-6` | OK |

**Why BLOCKER.** Tier R does contradiction characterisation, temporal interpretation, identity resolution and advocacy drafting — the reasoning the product's whole claim rests on. Every one of those is a `G-7` deliverable and a `G-14` eval gate. Without an invocable Tier R the graphs cannot run live, and `PV_AGENT_MODE=FIXTURE` invalidates the recorded submission (`S3`).

**Mitigation in force.** Tier R runs on `us.anthropic.claude-opus-4-6-v1`, the most capable reachable model. The router reads both ids from configuration, so the grant landing later is an environment change.

**Fix.** Request Claude Opus 5 access in the Bedrock console, us-east-1. **Only the account owner can do this.** Grants are not instantaneous.

**Closes when** `us.anthropic.claude-opus-5` returns `ok` from Converse and `.env` moves `BEDROCK_REASONING_MODEL_ID` onto it — **or** when the team decides to ship on 4.6 and `SUBMISSION.md` plus the README state the model actually used. Shipping on 4.6 while claiming Opus 5 is the exact dishonesty `23_PHASE_GATES.md` §23 exists to prevent.

---

### `D-06-001` — Correlated subquery in `ORDER BY` defeats the vector index

**Phase** 6 · **Lens** `L-DRIFT` · **Severity** MAJOR(M2) · **Found** 2026-08-17, Phase 0 probing · **Owning file** `docs/specs/13_RETRIEVAL_SPEC.md`

**Reproduction.** Against `rayyandb`, CockroachDB v26.2.5, on a table with a `(k, v vector_cosine_ops)` vector index and 500 seeded rows:

```sql
-- OBSERVED: full scan
EXPLAIN SELECT id FROM _pv_probe
 WHERE k = '00000000-0000-4000-8000-000000000001'
 ORDER BY v <=> (SELECT v FROM _pv_probe LIMIT 1) LIMIT 40;
--   table: _pv_probe@_pv_probe_pkey
--   spans: FULL SCAN

-- EXPECTED, and what a literal produces:
EXPLAIN SELECT id FROM _pv_probe
 WHERE k = '00000000-0000-4000-8000-000000000001'
 ORDER BY v <=> '[0.5,0.5,...]'::VECTOR(1024) LIMIT 40;
--   • vector search
--       table: _pv_probe@_pv_probe_a
--       prefix spans: [/'00000000-...-0001' - /'00000000-...-0001']
```

Survives `ANALYZE`, so it is not a stale-statistics artifact. Reproduced identically at `VECTOR(3)`, so it is not dimension-dependent.

**Why MAJOR rather than MINOR.** It fails silently. Results are correct, no error is raised, and the only symptom is latency — which at demo scale is invisible. A retrieval layer built this way would pass every functional test, then fail `G6.2` at the one moment it is inspected, or worse, pass a `G6.2` written against a non-production query shape and ship a full scan into the submission while claiming distributed vector indexing.

**Fix.** `13_RETRIEVAL_SPEC.md` must state that the ANN stage embeds first and passes the query vector as a bound parameter; computing it inside the ranking statement is forbidden. `G6.2`'s `EXPLAIN` assertion must run against the production query shape, parameter binding included.

**Closes when** the retrieval repository's ANN entry point takes a vector argument rather than deriving one, and `G6.2` names `evidence_embedding_ann_idx` with `prefix spans` present.

---

### `D-00-002` — Tier E model id form unresolved

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17 · **Owning file** `docs/CANONICAL_DECISIONS.md` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_phase0_probe_evidence.py::test_pb5_records_a_real_invocation_for_both_tiers + packages/python/provenance_contracts/tests/test_settings.py::test_bare_anthropic_chat_model_id_is_rejected`
- **Close-proof:** close-proof D-00-002: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: truncated `ops/bedrock-probe.txt`; the assertion requires a Tier E verdict naming the inference-profile id that actually invoked. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


> **Status corrected in triage round 1 (2026-08-17).** This row read
> `RESOLVED-WITH-CANON-CHANGE`, which is not in the `72_DEFECT_PROTOCOL.md`
> §5.2 closed set, so `make defects` — a binding gate precondition under §11.3
> — returned non-zero on the ledger itself. The canon did change and
> `settings.py` now pins the inference-profile ids, but §7.4 is explicit that
> without a close-proof the status is `AWAITING_REVERIFY`, and this record's
> own **Closes when** clause is not yet satisfied: see `D-00-018`, no committed
> transcript records a `us.anthropic.*` invocation. Filed as `D-00-017`.

**Reproduction.**

```
aws bedrock list-foundation-models --region us-east-1 ...
anthropic.claude-haiku-4-5-20251001-v1:0     <-- dated form only
anthropic.claude-opus-5                       <-- bare form, as frozen
amazon.titan-embed-text-v2:0                  <-- bare form, as frozen
```

The canon freezes Tier E as `anthropic.claude-haiku-4-5`. That exact string does not appear in the region listing; only the dated variant does. Tier R and the embedding model both appear exactly as frozen.

**Why it matters.** `specs/14_PROMPTS.md` §10 and the model router both pin the bare form. If only the dated id is invocable, every Tier E call fails with `ValidationException` at Phase 7 — after the graphs are built against the wrong constant.

**Fix.** Resolve by invocation, not by listing: try the bare id first, fall back to the dated one, record which resolved. If the bare form does not work, this is a canon change and `CANONICAL_DECISIONS.md`, `14_PROMPTS.md` and the router constant all move together.

**Closes when** `ops/bedrock-probe.txt` carries a successful Tier E call naming the id that worked.

---

### `D-00-003` — PB-5 invocation unproven

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-17 · **Owning file** `ops/bedrock-probe.txt` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_phase0_probe_evidence.py::test_pb5_records_a_real_invocation_for_both_tiers`
- **Close-proof:** close-proof D-00-003: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: truncated `ops/bedrock-probe.txt` to its header, which is the same neutering `make probe` used to perform by accident; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


> **Status corrected in triage round 1 (2026-08-17).** This row read `CLOSED`
> with no fix SHA, no verifying assertion and no close-proof — three separate
> `72_DEFECT_PROTOCOL.md` §5.2 requirements — while its own **Closes when**
> clause ("both tiers return `ok` and the transcript records which Tier E id
> form was used") is unsatisfied: `grep -rn "us\.anthropic" ops/` matches only
> prose, never a probe transcript. §7.4: "No close-proof, no CLOSED."
> Demoted to `AWAITING_REVERIFY`, which blocks the gate exactly as OPEN does.
> Filed as `D-00-017`.

**Reproduction.** `ops/bedrock-probe.txt` step 2 records a `BLOCKED` and two `FAIL` results, not a successful invocation. Listing a model proves it exists in the region; it does not prove this account can invoke it. Model-access grants are per-account and not instantaneous.

```
grep -n "RESULT\|BLOCKED" ops/bedrock-probe.txt
grep -rn "us\.anthropic" ops/ | grep -v "\.md:"
```

Observed: `BLOCKED: the anthropic SDK is not importable`, `RESULT PB-5 Tier E (...): FAIL`, `RESULT PB-5 Tier R (...): FAIL`, and **zero** matches for a `us.anthropic.*` invocation in any transcript. Expected: one `tier=E model=... ok text=` line and one `tier=R model=... ok text=` line.

**Why it is logged rather than ignored.** This is exactly the vacuity failure `L-VAC` exists to catch: PB-5 currently has two green sub-results and one absent one, and a ledger that recorded PB-5 as PASS would be asserting access nobody demonstrated. Bedrock access is a prerequisite for G-7 and for every live-model evaluation in G-14.

**Fix.** Run the two `converse` calls in `ops/bedrock-probe.txt` step 2 and append raw output.

**Closes when** both tiers return `ok` and the transcript records which Tier E id form was used.

---

### `D-00-005` — `make probe` truncates committed evidence before it checks whether it can connect

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M1) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/probes/phase0-probe.ps1` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_phase0_probe_evidence.py::test_the_probe_refuses_to_overwrite_existing_evidence`
- **Close-proof:** close-proof D-00-005: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: disabled the `$ExistingEvidence` refusal in the preflight and ran the probe with no database URL; the six evidence files were overwritten and the refusal banner never printed. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


Found independently by `L-VAC` (finding B2) and `L-DRIFT` (finding 1). One defect, two directions.

**Reproduction.** With `$env:PV_PROBE_DB_URL` unset and no `cockroach` CLI on `PATH` — the state of the build machine — run the read-sounding target:

```
make probe
grep -c "^-- P" ops/cluster-probe.txt
grep -cE "^VARIANT: (A|B|C)$" ops/decisions/VECTOR_INDEX_VARIANT.md
```

Observed: `ops/cluster-probe.txt` fell from a full `P1`..`P11` transcript (6544 bytes) to a 682-byte stub with **0** `^-- P` headers; `ops/grant-probe.txt` became `PB-4 not run: no usable SQL connection`; `ops/decisions/VECTOR_INDEX_VARIANT.md` became `## NO VARIANT SELECTED` with **0** `VARIANT:` lines. Expected: `11` and `1`, unchanged, because the run reached nothing and therefore learned nothing.

**Why.** `ops/probes/phase0-probe.ps1` opened all four transcripts with `File::WriteAllText($f, '')` at line 306, in the preflight section — *before* the CA-certificate check, before the connection string is read, before `$SqlReady` exists. The no-variant branch then called `Write-TextFile` over `ops/decisions/VECTOR_INDEX_VARIANT.md`, which is a truncating write. So the failure mode was not "a bad run wrote a bad transcript"; it was "any invocation at all, including one that cannot connect, destroys the evidence of the run that could".

**Why MAJOR(M1) and not BLOCKER.** Both hunters proposed BLOCKER. `B1`–`B4` all name runtime product behaviour and answer **no** here: no invariant, no tenant or user boundary, no external side effect from uncommitted state, no rendered UI element. `M1` is the first YES — the observed count is `0` where the assertion requires `11`. Per §4.3 a MAJOR blocks the `G-0` signature just as absolutely as a BLOCKER; the severity changes the label, not the consequence.

**Fix (applied).** `ops/probes/phase0-probe.ps1` now (a) computes the evidence-file set *before* opening anything and refuses to start, touching no file, when any of the six is non-empty and `-Force` was not passed; (b) never writes `NO VARIANT SELECTED` over a file that already carries a `VARIANT:` line — it prints what it would have written and leaves the decision alone, because a run that reached nothing has not disproved a decision; (c) adds the `-Force` switch for deliberate regeneration.

**Closes when** `G0.6` passes on regenerated transcripts **and** a second invocation of `ops/probes/phase0-probe.ps1` with no database URL exits non-zero having left all six evidence files byte-identical.

---

### `D-00-006` — Phase 0 evidence is destroyed and the ledger asserts PASS against it

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M1) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/PROBE_LEDGER.md` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_phase0_probe_evidence.py::test_the_g0_6_evidence_is_present_and_complete`
- **Close-proof:** close-proof D-00-006: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: truncated `ops/cluster-probe.txt`; the assertion requires 11 `-- P` headers and exactly one VARIANT line. Resolved by events as well: the transcripts were regenerated from live runs and `ops/` is tracked as of 62e3f1c, so the recovery gap is closed. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


The live consequence of `D-00-005`. Filed separately because fixing the script does not restore the transcripts, and `D-00-005` would otherwise be closable while `G0.6` still fails.

**Reproduction.**

```
grep -c "^-- P" ops/cluster-probe.txt
grep -cE "^VARIANT: (A|B|C)$" ops/decisions/VECTOR_INDEX_VARIANT.md
grep -n "PB-1" ops/PROBE_LEDGER.md
```

Observed: `0`, `0`, and a ledger row reading `PB-1 ... PASS`. Expected: `11`, `1`, and a ledger whose rows are backed by the transcripts they cite. `ops/` is untracked at this point in the build, so there is no `git checkout` recovery.

**Why it matters more than the byte loss.** A ledger that reads PASS while the transcript it cites is empty is the precise failure `23_PHASE_GATES.md` §3 exists to prevent — an assertion reported complete without pasted output. It is also the same shape as `B4` one level up: a claim rendered from a stored constant rather than from the thing it claims to describe.

**Fix.** Re-run `PB-1`..`PB-4` against `rayyandb` with `ops/probes/phase0-probe.ps1 -Force`, which needs the `cockroach` CLI installed and `$env:PV_PROBE_DB_URL` set in the operator's shell. A warning banner has been added at the top of `ops/PROBE_LEDGER.md` in the meantime so no reviewer can read the table as evidence.

**Closes when** `grep -c "^-- P" ops/cluster-probe.txt` returns `11`, `ops/decisions/VECTOR_INDEX_VARIANT.md` carries exactly one `VARIANT:` line, `G0.6` passes through `tools/gate.sh`, and the banner is removed in the same change.

---

### `D-00-007` — `make lint`'s import-linter fallback exits 0 having evaluated zero contracts

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `./Makefile` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_make_lint_refuses_the_import_linter_module_fallback`
- **Close-proof:** close-proof D-00-007: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: added `$(PY) -m importlinter.cli lint-imports` back into the `lint` recipe; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


Found by `L-VAC` (B1) and `L-DRIFT` (2).

**Reproduction.**

```
python -m importlinter.cli lint-imports; echo "rc=$?"
python -m importlinter.cli this-is-not-a-command --nonsense; echo "rc=$?"
python -c "from importlinter.cli import lint_imports_command; lint_imports_command()"
```

Observed: the first two print **nothing** on either stream and return `rc=0`. The third prints `Contracts: 4 kept, 0 broken.` `importlinter/cli.py` declares its click commands at module scope and has no `if __name__ == "__main__"` guard, and the package ships no `__main__.py`, so `python -m importlinter.cli <anything>` imports the module and returns. On a tree deliberately broken with `import boto3` inside `provenance_domain/kernel/__init__.py`, the real entry point returns `Contracts: 2 kept, 2 broken` and `rc=1`; the fallback still returns `rc=0` with no output.

**Why it matters.** The fallback was the branch taken on every machine without the `lint-imports` console script on `PATH`. `ci.yml` calls E1 kernel purity "the one this project cannot ship without", and on that branch it was decoration.

**Fix (applied).** The fallback is deleted. `make lint` now fails loudly and names `make bootstrap` when `lint-imports` is not on `PATH`. The four contracts themselves were verified correct and are unchanged — the defect was the invocation, not the configuration.

**Closes when** `make lint` on a tree with `import boto3` added to `provenance_domain/kernel/__init__.py` exits non-zero and names the contract, **and** `make lint` with `lint-imports` removed from `PATH` exits non-zero rather than 0.

---

### `D-00-008` — `ruff>=0.6,<1` makes `ruff format --check .` return a different verdict by install date

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M1) · **Found** 2026-08-17, triage round 1 · **Owning file** `pyproject.toml` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_ruff_is_pinned_to_an_exact_version`
- **Close-proof:** close-proof D-00-008: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: restored `ruff>=0.6,<1` in `pyproject.toml`; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** In a clean virtualenv built the way `make bootstrap` builds one:

```
python -m pip install "ruff>=0.6,<1"
python -m ruff --version
python -m ruff format --check .; echo "rc=$?"
```

Observed with the resolved version (`0.16.3`): `3 files would be reformatted, 40 files already formatted`, `rc=1`, on `tools/close_proof.py`, `tools/defect_lint.py` and `tools/tests/test_scrub.py`. With `0.6.9`: `43 files already formatted`, `rc=0`. `ruff check .` and `mypy --strict` pass on both.

**Why it matters.** `ruff format --check .` is step 2 of `make lint`, which is a step in the `lint` CI job **and** a step inside `G0.4`'s clean-clone proof. A formatter version range means the merge lane's verdict depends on the day the runner resolved its dependencies. All three were red on any runner that resolved ruff ≥ 0.9.

**Fix (applied).** Pinned to `ruff==0.6.9` — the version the tree is formatted for and the one `ruff format --check .` currently passes under. Moving the pin is a deliberate change that reformats the tree in the same commit.

**Closes when** `make bootstrap && make lint` passes in a clean clone (`G0.4`) and `python -m ruff --version` in that clone prints the pinned version.

---

### `D-00-009` — `tools/scrub.py` had no rule for five credential shapes this build emits

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MAJOR(M1) · **Found** 2026-08-17, triage round 1 · **Owning file** `tools/scrub.py` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_scrub.py::test_rule_redacts_the_secret_it_names + tools/tests/test_scrub.py::test_every_rule_has_at_least_one_leak_case`
- **Close-proof:** close-proof D-00-009: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: made the `password-assignment` rule a no-op by echoing its own capture; the PGPASSWORD leak case came through verbatim. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** Drive the real capture harness with the four shapes:

```
PV_GATE_LOG=/tmp/pvgate bash tools/gate.sh HUNTER-1 -- bash -c 'echo "PGPASSWORD=<PW> psql"; echo "aws_secret_access_key = <KEY>"'
grep -c "<PW>\|<KEY>" /tmp/pvgate/HUNTER-1.*.log
```

Observed before the fix: the written log is headed `scrubbed-by=tools/scrub.py` and its body contained every value verbatim; only the `postgresql://` line was redacted. Expected, and observed after the fix: every value replaced by `[REDACTED-SECRET]` and the `grep -c` returning `0`.

**Why it matters.** `.gitleaks.toml` lines 46–50 enumerate six shapes "this project can actually leak"; `scrub.py` covered three. `ops/probes/phase0-probe.ps1` lines 99–104 already implemented the missing ones — so the repository shipped two scrubbers and the **weaker one guarded the committed artefact**. Combined with `D-00-024` (no gitleaks binary on this machine) this made the weaker scrubber the *only* filter between a credential and a public repository.

**Fix (applied).** Four rules added to `RULES` — `sql-password-literal`, `password-assignment`, `aws-secret-credential`, `named-secret-assignment` — each with a leak case in `tools/tests/test_scrub.py::LEAKS`, so `test_every_rule_has_at_least_one_leak_case` enforces the pairing. The value classes are `%q`-aware: `tools/gate.sh` renders the child command with `printf '%q '`, so a rule anchored on plain `\s` redacted the command's output and missed the identical credential in the command's arguments on the `# cmd=` header line.

**Closes when** `tools/tests/test_scrub.py` passes with a leak case for every rule, and a `tools/gate.sh` run over all five shapes produces a log containing none of them in body or header.

---

### `D-00-010` — gitleaks allowlist C excused any gate-log line carrying a redaction marker

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `.gitleaks.toml` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_gitleaks_config.py::test_transcript_paths_are_scanned_not_skipped + tools/tests/test_gitleaks_config.py::test_repo_ops_directory_is_clean`
- **Close-proof:** close-proof D-00-010: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: re-added a redaction-marker allowlist scoped by `paths` over the gate logs and the probe transcript; the planted DSN on a line also carrying a redaction marker stopped being reported. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** Apply rule `pv-password-assignment` and allowlist C verbatim to the log produced by `D-00-009`'s reproduction:

```
python tmp/allowc.py
```

Observed: the line carrying both `[REDACTED-USER]` (from the DSN the scrubber did redact) and a live `PGPASSWORD` assignment (which it did not) reports `path allowlisted=True line carries redaction marker=True -> EXCUSED BY ALLOWLIST C`. The same secret on a line with no marker is reported. Expected: reported on both.

**Why it matters.** The residual risk the config states at lines 279–284 is reachable through `tools/gate.sh`'s own header format, not hypothetical: `gate.sh` renders the whole child command onto a single `# cmd=` line, so any command naming both a DSN and a second credential produces exactly such a line. Both layers bypassed at once.

**Fix (applied).** `ops/gates/logs/*.log` is removed from allowlist C's `paths`. Gate logs are machine-written by `tools/scrub.py`, whose markers (`[REDACTED-USER]`, `[REDACTED-SECRET]`, `[REDACTED-ACCOUNT]`) match no rule in the file, so the entry excused nothing that would otherwise be reported and cost the bypass above. Allowlist C's `never committed` regex — the only one that did not require a redaction marker — was narrowed in the same change (`D-00-038`).

**Closes when** `gitleaks detect --source ops/gates --config .gitleaks.toml` reports a planted `PGPASSWORD` assignment on a line that also carries `[REDACTED-USER]`, and `G0.3` plus `G0.3b` pass on the real tree.

---

### `D-00-011` — the CI evidence-before-assertion step never opened a log, and ignored `S1`..`S10`

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `.github/workflows/ci.yml` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_ci_evidence_check.py::test_a_pass_row_whose_log_records_a_failure_is_rejected + tools/tests/test_ci_evidence_check.py::test_a_forged_row_in_the_submission_ledger_is_rejected`
- **Close-proof:** close-proof D-00-011: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4, run twice, once per hole: disabling the `exit_code != 0` branch reds the first assertion, and narrowing `ident` back to the G-family regex reds the two submission-row assertions. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


Found by `L-VAC` (M1) and `L-DRIFT` (7).

**Reproduction.** Extract the heredoc to a file and run it against a forged ledger:

```
python tmp/evid/evidence_check.py
```

Observed, two separate holes. (1) With a ledger row `| G0.1 | PASS | ... |` and a log `ops/gates/logs/G0.1.deadbeef.log` whose header reads `# gate=G0.1 sha=deadbeef exit=7`, the check prints `no PASS row is missing its evidence` and returns `rc=0` — it tested only that a filename existed. (2) A forged `| S3 | PASS | ... |` row in `ops/gates/PHASE_15.md` was not detected at all, because the row regex is `^\|\s*(G\d+\.\d+[a-z]?)\s*\|` and `PHASE_15.md` states outright that there is no `G15.x`. Expected: `rc=1` in both cases.

**Why it matters.** The submission gate — the one T0.7 sub-task 4 exists to protect — was entirely unguarded, and a `PASS` row whose only evidence records a failure was accepted everywhere else.

**Fix (applied).** The step now parses line 1 of each matched log, requires `exit=0` (rejecting `exit=CAPTURE-FAILED` and any non-zero) and requires `sha=` to equal the commit under test; both id families are matched by `(?:G\d+\.\d+[a-z]?|S\d+)`; and `ops/gates/SUBMISSION.md` is scanned alongside `PHASE_*.md`. The sha check is what stops a stale green from surviving a red re-run: `tools/gate.sh` names logs `<ID>.<sha8>.log`, so a re-run at the same commit overwrites in place.

**Closes when** the step returns non-zero for each of: a `PASS` row whose log records `exit=7`; a `PASS` row whose log was captured at a different sha; and a forged `| S3 | PASS |` row.

---

### `D-00-012` — the unit-lane guard did not block `gethostbyname`, and a `unit` test resolved a real host

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M1) · **Found** 2026-08-17, triage round 1 · **Owning file** `conftest.py` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tests/test_socket_guard.py::test_the_legacy_resolvers_are_refused + tests/test_socket_guard.py::test_every_blocked_reach_is_actually_patched`
- **Close-proof:** close-proof D-00-012: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: removed `gethostbyname` from `_CANDIDATE_REACHES`; the resolver stopped raising. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** A `unit`-marked test against the unmodified `conftest.py`:

```
python -m pytest tmp/triage/test_escape.py -q -s
```

where the test body is `socket.gethostbyname("example.com")`. Observed before the fix: `ESCAPED: resolved example.com -> 104.20.23.154`, and `gethostbyname_ex` returned two more. Real DNS left the machine from inside the hermetic lane. Expected, and observed after the fix: `REFUSED: NetworkAccessInUnitTestError`.

**Why it matters.** The conftest docstring claimed "Everything a unit test could use to leave the machine goes through one of those five." It was false: `gethostbyname`, `gethostbyname_ex` and `gethostbyaddr` bottom out in `_socket.gethostbyname`/`gethostbyaddr` and do not route through `getaddrinfo`. The leak is exactly the one `test_getaddrinfo_is_refused` says it prevents — "it leaks the lookup itself".

**Fix (applied).** The blocked set is now the data structure `BLOCKED_REACHES`, which `install_socket_guard` iterates; the three `gethostby*` reaches and `sendmsg` are members, `sendmsg` filtered by `hasattr` because it is absent on Windows and present on the Linux CI runner. Each member has a test that names it, plus `test_every_blocked_reach_is_actually_patched`, so the list and the installation cannot drift apart.

**Closes when** `tests/test_socket_guard.py::test_the_legacy_resolvers_are_refused` passes for all three names and the escape reproduction above raises rather than resolving.

---

### `D-00-013` — three guard mechanisms had no test that fails when the mechanism is deleted

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `tests/test_socket_guard.py` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tests/test_socket_guard.py::test_sendto_is_refused + tests/test_socket_guard.py::test_the_credential_unsetter_actually_removes_them`
- **Close-proof:** close-proof D-00-013: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4, run twice: removing the `sendto` reach reds the first, and gutting `unset_credentials` reds the second on a machine that has the five variables and on one that does not. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** A mutation matrix: copy `conftest.py`, remove one patch line, run the suite.

```
python -m pytest tests/test_socket_guard.py -q
```

Observed before the fix — removing `socket.socket.sendto`: **17 passed**. Removing `socket.create_connection`: **17 passed** (both tests that name it fall through to the still-patched `getaddrinfo` and raise the same exception type). Removing `unset_credentials(monkeypatch)` and running under `env -u AWS_PROFILE -u AWS_REGION -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u COCKROACH_DATABASE_URL`: **17 passed**. Expected: red in all three cases. Controls: removing `connect`, `connect_ex` or `getaddrinfo` each went red, so the harness does detect a removed patch.

**Why it matters.** `E3` is a mechanism whose whole value is that it fires. The credential half was vacuous in precisely the environment that enforces it — a CI runner that never had the five variables — which is where the lane actually runs.

**Fix (applied).** `test_sendto_is_refused` (a `SOCK_DGRAM` socket, asserting on the message); message assertions added to `test_create_connection_is_refused` and `test_getaddrinfo_is_refused` so each sees its own patch; and `test_the_credential_unsetter_actually_removes_them`, which sets a sentinel for each of the five variables and then calls the helper through the `SocketGuard` fixture — so it fails on any machine if the mechanism is deleted. The ambient-environment assertions are kept as a second layer.

**Closes when** removing any single line of `install_socket_guard`, or the `unset_credentials` call, turns `tests/test_socket_guard.py` red.

---

### `D-00-014` — `testpaths` excluded `tools/`, so T0.3's 28 declared tests were never collected

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `pyproject.toml` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_testpaths_collects_the_tools_tree`
- **Close-proof:** close-proof D-00-014: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: dropped `tools` from `testpaths`; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
python -m pytest --collect-only -q
python -m pytest tools/tests/test_scrub.py -q
```

Observed before the fix: the first collected `54 tests`, from `packages/python/provenance_contracts/tests/test_settings.py` and `tests/test_socket_guard.py` only; the second ran `28 passed`. Twenty-eight tests existed and zero of them ran — not in `make test`, not in `make test-fast`, not in CI.

**Why it matters.** T0.3 declares `tools/tests/test_scrub.py` as its "Tests first". The scrubber that keeps credentials out of committed gate logs had a suite that no lane executed, which is why `D-00-009` survived to be found by a hunter rather than by the build.

**Fix (applied).** `testpaths = ["packages", "services", "agents", "tests", "tools"]`, with the deviation from `20_TDD_STRATEGY.md` §3.4 recorded in the comment block above it — the file already records two other deviations in the same form.

**Closes when** `python -m pytest --collect-only -q` reports a count that includes `tools/tests/test_scrub.py`.

---

### `D-00-015` — `make debt` printed `0 carried items` and exited 0 with no ledger file to read

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `tools/defect_lint.py` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_make_debt_distinguishes_a_missing_ledger_from_an_empty_one`
- **Close-proof:** close-proof D-00-015: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: made `print_debt` ignore `ledger_exists`; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
python -m tools.defect_lint --debt --check-escalation
python -m tools.defect_lint --debt --debt-file ops/defects/NO_SUCH_FILE.md
```

Observed before the fix: both printed `0 carried items` and `defect_lint: 0 violations` and returned `rc=0`, identically, although `ops/defects/CARRIED_DEBT.md` does not exist. Expected: the absence stated. Compare `tools/sabotage_guard.py --count`, which does print "does not exist yet" for its missing file.

**Why it matters.** §11.3 makes this a binding gate precondition and §9.3 says the line is pasted verbatim into the gate report. A reviewer therefore read "no carried debt" from a tool that had opened no file — "the ledger is empty" and "there is no ledger" rendered as the same sentence.

**Fix (applied).** The `--debt` branch used to `return _finish(...)` before the "does not exist yet" notes were written; the notes now run first, and `print_debt` takes `ledger_exists` so the two cases print differently.

**Closes when** `python -m tools.defect_lint --debt` names the missing ledger, and prints a distinguishable line once `CARRIED_DEBT.md` exists and is empty.

---

### `D-00-016` — G0.7's exit-code assertion is a dead statement

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `./Makefile` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_g0_7_exit_code_assertion_is_not_a_dead_statement`
- **Close-proof:** close-proof D-00-016: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: deleted `|| exit 1` from G0.7's exit-code assertion; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** The recipe runs under `set -uo pipefail` — no `-e` — so a bare `test ...;` is a statement whose status is discarded:

```
bash -c 'set -uo pipefail; out=$(bash -c "echo COCKROACH_DATABASE_URL mentioned; exit 0"); rc=$?; printf "exit=%s\n" "$rc"; test "$rc" -ne 0; printf "%s" "$out" | grep -q "COCKROACH_DATABASE_URL"'; echo "rc=$?"
```

Observed: prints `exit=0` and returns `rc=0`. Expected: non-zero. `G0.7` claims two things — that `Settings()` exits non-zero on a missing required variable, and that it names `COCKROACH_DATABASE_URL` — and could only ever fail on the second.

**Why `-e` is not the fix.** `-e` is omitted from these flags on purpose so that `rc=$?` survives the deliberately-failing command. `G0.1`, `G0.4`, `G0.5` and `G0.6` all use `set -euo pipefail`; `G0.7` is the only one that cannot.

**Fix (applied).** `test "$$rc" -ne 0 || exit 1;`, with the reason recorded in a comment beneath the recipe. The gate passes substantively today — `env -i` construction does exit 1 and does name the variable — so this was a dead arm rather than a wrong result.

**Closes when** `G0.7` returns non-zero against a stub `Settings()` that exits 0 while printing `COCKROACH_DATABASE_URL`.

---

### `D-00-017` — the defect ledger failed its own linter

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/defects/DEFECTS.md` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_the_defect_ledger_passes_its_own_linter`
- **Close-proof:** close-proof D-00-017: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: dropped the severity rule id from one row, DL05 fired; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
python -m tools.defect_lint --report; echo "rc=$?"
```

Observed before the fix: `defect_lint: 10 violation(s)`, `rc=1` — `DL05` ×4 (no severity rule id on any of the four records), `DL08` (`D-00-004`'s owning file was the prose "AWS account configuration", not a path), `DL09` (`D-00-002`'s status `RESOLVED-WITH-CANON-CHANGE` is outside the §5.2 closed set), and `DL07`/`DL10`/`DL11`/`DL12` (`D-00-003` `CLOSED` with no runnable reproduction, no fix SHA, no verifying assertion and no close-proof). Expected: `rc=0`.

**Why it matters.** §11.3 makes a clean `make defects` a binding precondition of **every** gate verdict, so `G-0` could not be signed at all while the ledger itself was malformed.

**Fix (applied).** The ledger, not the linter. Rule ids added to all four severity cells; `D-00-004` given the repository-relative path whose change makes the reproduction stop reproducing; `D-00-002` moved to `AWAITING_REVERIFY`; `D-00-003` demoted from `CLOSED` to `AWAITING_REVERIFY` and given a runnable reproduction. The 6-column index table is kept: `tools/defect_lint.py` reads the union of the table row and the §5.3 detail block, which is the schema applied to the record rather than to a column layout.

**Closes when** `python -m tools.defect_lint --report` exits 0 with every record in the file.

---

### `D-00-018` — `extra="forbid"` does not reject an undeclared environment variable

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `packages/python/provenance_contracts/src/provenance_contracts/settings.py` · **Status** OPEN

**Reproduction.** With a complete valid environment plus two typo'd names:

```
python tmp/probe_extra_env.py
```

which sets `COCKROACH_DATABSE_URL` and `BEDROCK_REASONING_MODEL_D` and constructs `Settings()`. Observed: `CONSTRUCTED OK despite undeclared env vars set.` Expected per `ops/40_INFRA_IAC.md` §12.11 — "a stale variable left behind by a refactor cannot silently do nothing" — a `ValidationError`.

**Why it matters.** `extra="forbid"` constrains the **keyword** path, not the env source: pydantic-settings' environment source only reads declared aliases, so an undeclared `COCKROACH_*` name is invisible rather than rejected. `test_unknown_variable_passed_in_is_refused` tests the keyword path, so the suite looks like it covers the guarantee and does not.

**Fix.** Either add a `model_validator` that scans `os.environ` for the documented `COCKROACH_*` / `BEDROCK_*` / `PV_*` / `COGNITO_*` prefix set and rejects unknown names, or correct §12.11 to state that the guarantee covers keyword construction only. Left OPEN rather than fixed in triage: which of the two is right is a specification decision owned by `40_INFRA_IAC.md`, and this round does not rewrite specifications.

**Closes when** a test that exports `COCKROACH_DATABSE_URL` and constructs `Settings()` asserts the documented behaviour, whichever of the two it ends up being, and §12.11 and the code agree.

---

### `D-00-019` — `ValidationError.errors()` renders the raw environment, credentials in plaintext

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MAJOR(M1) · **Found** 2026-08-17, triage round 1 · **Owning file** `packages/python/provenance_contracts/src/provenance_contracts/settings.py` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `packages/python/provenance_contracts/tests/test_settings.py::test_a_validation_error_carries_the_raw_environment_and_get_settings_drops_it`
- **Close-proof:** close-proof D-00-019: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: rebuilt `SettingsValidationError` from `exc.errors()` instead of the redacted triple, and the sentinel DSN reappeared in the rendered message. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** Trip a cross-field `model_validator(mode="after")` with a live-shaped DSN in the environment:

```
python tmp/probe_verr.py
```

which sets `COCKROACH_POOL_MIN=9`, `COCKROACH_POOL_MAX=1` and a sentinel DSN. Observed before the fix: `exc.json() leaks secret: True` and `errors()` carries `COCKROACH_DATABASE_URL: postgresql://...@h:26257/provenance` in plaintext, while `str(exc) leaks secret: False`. Expected: masked on every path. A full matrix confirmed `repr`, `str`, `format`, `model_dump`, `model_dump_json`, `dict()`, `__dict__`, `logging %s`/`%r` and `deepcopy` all mask correctly; only `errors()`, `.json()` and `pickle.dumps` leak.

**Why it matters.** A `mode="after"` validator failure attaches `input` — the whole environment mapping — to every error entry. `str()` masks it, which is exactly why the leak survived a test suite that checked `repr`, `str`, `format` and `model_dump_json`; a structured logger and most traceback formatters reach for `errors()`. The module docstring's claim that `SecretStr` keeps a credential "out of an exception traceback" was false as written.

**Fix (applied).** `get_settings()` catches `ValidationError` and re-raises `SettingsValidationError`, rebuilt from `(type, loc, msg)` with `input`, `url` and `ctx` dropped, `from None` so the original is not chained back into the traceback. `Settings()` constructed directly still raises pydantic's exception — `G0.7` asserts on that path — and the docstring now states the boundary rather than overclaiming. A test asserts both halves: that pydantic really does leak on that path, and that `get_settings` does not.

**Closes when** `packages/python/provenance_contracts/tests/test_settings.py::test_a_validation_error_carries_the_raw_environment_and_get_settings_drops_it` passes, and `G0.7` still exits non-zero naming `COCKROACH_DATABASE_URL`.

---

### `D-00-020` — PB-5 probed the superseded bare `anthropic.claude-*` ids

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/probes/phase0-probe.ps1` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_phase0_probe_evidence.py::test_the_probe_names_no_superseded_bare_anthropic_id + tools/tests/test_phase0_probe_evidence.py::test_pb5_probes_all_three_anthropic_tiers_through_inference_profiles`
- **Close-proof:** close-proof D-00-020: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: put the bare `anthropic.claude-haiku-4-5` id back into the PB-5 loop; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
grep -rn "anthropic.claude" ops/probes/phase0-probe.ps1 ops/probes/README.md | grep -v "us\.anthropic"
```

Observed before the fix: 7 hits naming `anthropic.claude-haiku-4-5` and `anthropic.claude-opus-5` in the probe script and its README. `CANONICAL_DECISIONS.md` → *Bedrock model id canon (frozen 2026-08-17)* states these are not invocable in any form and that every bare id elsewhere in the pack is superseded. Expected: zero.

**Why it matters.** Any future `make probe` would have re-failed PB-5 for the wrong reason and written the superseded ids back into the transcript, producing evidence that says the models do not work when what does not work is the id form.

**Fix (applied).** PB-5 now probes `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Tier E) and `us.anthropic.claude-opus-4-6-v1` (Tier R in force), plus `us.anthropic.claude-opus-5` as a third `R-TARGET` probe that is *expected* to be denied and whose failure deliberately does not fail PB-5 — so the transcript dates the grant when it lands. The header block and `ops/probes/README.md` name the inference-profile requirement.

**Closes when** `ops/bedrock-probe.txt` records `tier=E model=us.anthropic.claude-haiku-4-5-20251001-v1:0 ok text=` and no bare `anthropic.claude-*` id appears anywhere under `ops/`.

---

### `D-00-021` — the probe scrubber preserved the userinfo *user*, which `tools/scrub.py` deliberately redacts

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/probes/phase0-probe.ps1` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_phase0_probe_evidence.py::test_the_probe_scrubber_redacts_both_halves_of_the_userinfo`
- **Close-proof:** close-proof D-00-021: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: restored the password-only URL rule that captures and re-emits the userinfo user; the token-in-user-field DSN survived. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** Dot-source the scrubber section and call it on a DSN whose *user* field carries the token and whose password field is empty — the CockroachDB Cloud shape this rule exists for. No literal DSN is written here: the probe reads the operator's own from the environment and prints only the scrubbed result.

```
powershell -NoProfile -Command ". .\ops\probes\phase0-probe.ps1 -SkipSql -SkipAws; Protect-Text $env:PV_PROBE_DB_URL"
```

Observed before the fix: everything after the colon is replaced and the **user half survives verbatim**. Expected, and what `tools/scrub.py` produces on the same input: both halves of the userinfo replaced by markers.

**Why it matters.** `tools/tests/test_scrub.py::test_the_sql_role_name_does_not_survive_a_connection_url` states the reason in its own docstring: "Some CockroachDB Cloud connection strings carry the token in the user field, so redacting only the password leaks on exactly the shape that matters." This scrubber writes the committed `ops/*.txt` transcripts, so the two must not disagree. The current transcripts are clean — verified by piping all seven through `tools/scrub.py` and diffing — so this is prevention for the next run rather than a live leak.

**Fix (applied).** The URL rule now replaces **both** halves of the userinfo, matching `tools/scrub.py`'s markers, and the `*_HMAC_KEY` / `*_SECRET` / `*_TOKEN` rule from `D-00-009` was ported across so the two scrubbers cover the same shapes.

**Closes when** `Protect-Text` and `tools.scrub.scrub_text` produce a userinfo-free result for the same token-in-user-field DSN.

---

### `D-00-022` — two probe ledgers existed at two paths with contradictory verdicts

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/probes/PROBE_LEDGER.md` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_phase0_probe_evidence.py::test_there_is_exactly_one_probe_ledger`
- **Close-proof:** close-proof D-00-022: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: wrote PB result rows back into `ops/probes/PROBE_LEDGER.md`, which is kept only as a redirect. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
diff <(head -20 ops/PROBE_LEDGER.md) <(head -20 ops/probes/PROBE_LEDGER.md)
```

Observed before the fix: every `PB-` row differs. The curated `ops/PROBE_LEDGER.md` read `PB-1`..`PB-4 = PASS` and `VARIANT: A`; the script-generated `ops/probes/PROBE_LEDGER.md` read `NOT RUN` and `VARIANT: none -- BRUTE_FORCE_PARTITION`. Expected: one ledger.

**Why it matters.** A reviewer who finds either has no reason to look for the other, and `41_RUNBOOK.md` §3.7 names one ledger while `00_IMPLEMENTATION_MAP.md` §5 names neither path.

**Fix (applied).** `ops/PROBE_LEDGER.md` is the single committed path — it is the one `.gitleaks.toml` enumerates in its transcript allowlist — and `$LedgerFile` in the probe script now points there. `ops/probes/PROBE_LEDGER.md` is replaced by a redirect note rather than deleted, so a habit pointing there lands on the reason.

**Closes when** `ls ops/probes/` shows no generated ledger and `41_RUNBOOK.md` §3.7 names `ops/PROBE_LEDGER.md` explicitly.

---

### `D-00-023` — `.SHELLFLAGS` is inert on GNU Make 3.81, the only `make` on the build machine

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `./Makefile` · **Status** AWAITING_REVERIFY

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_a_gate_battery_refuses_a_make_older_than_3_82`
- **Close-proof:** close-proof D-00-023: test FAILED with the fix neutered (exit=1) — PASS, twice. Manual counterfactual per §7.4: lowering the guard's tuple to (3, 81) reds it, and deleting the `$(call require_make_version)` line from the gate-0 recipe reds it. Test written by this sweep; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.
- **Not closed:** resolved by events on the mechanism half — GNU Make 4.4.1 is installed and `make gate-0` now refuses below 3.82 — but this record's own Closes-when clause has a second half, `ops/41_RUNBOOK.md` §1 lists the minimum, and that table still carries no GNU Make row. The edit is under `docs/`, outside this sweep's write boundary, so the record stays AWAITING_REVERIFY rather than closing on the half that was provable.


**Reproduction.** A minimal makefile carrying the same two lines:

```
make --version
make -f tmp/mk/Makefile probe-e
```

with the recipe `@false; echo "reached end"`. Observed: `GNU Make 3.81`, then `reached end` and `rc=0` — `-e` was not honoured. An unset-variable recipe printed `[]` and continued. Expected: non-zero on the first failing command. `.SHELLFLAGS` was introduced in GNU Make 3.82.

**Why it matters.** Every multi-command recipe in the file silently loses fail-fast, unset-variable and pipefail protection on the machine T0.7 requires the clean-clone proof to run on — including the gate recipes, where a step that scrolls past with exit 0 is a green log for an assertion that failed.

**Fix (applied).** `make bootstrap` now asserts `$(MAKE_VERSION) >= 3.82` and fails with the reason and the remedy (Git for Windows ships GNU Make 4.x; put its `bin` ahead of any GnuWin32 `make` on `PATH`). The requirement belongs in `ops/41_RUNBOOK.md` §1's version table alongside Python and Node; that edit is not made here because this round does not rewrite specifications.

**Closes when** `make bootstrap` exits non-zero on GNU Make 3.81 and `ops/41_RUNBOOK.md` §1 lists the minimum.

---

### `D-00-024` — gitleaks is not installed, so G0.3 cannot run and `tools/scrub.py` is the only filter

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `./Makefile` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_the_gitleaks_binary_is_installed_and_meets_the_floor`
- **Close-proof:** close-proof D-00-024: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: ran the module with gitleaks off PATH and USERPROFILE pointed away from bin/gitleaks.exe; the assertion is deliberately unskippable so an absent scanner goes red rather than green. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
gitleaks detect --source . --redact --no-banner --exit-code 1
```

Observed: `bash: gitleaks: command not found`. Expected per `ops/41_RUNBOOK.md` §2's toolchain pin: `gitleaks version` ≥ 8.21.0.

**Why it matters.** Every claim in `.gitleaks.toml` and `SECURITY.md` lines 71–73 about a "second filter" is locally unavailable, which promoted `D-00-009`'s weak scrubber from "first of two filters" to "the only filter". CI would catch a leak, but only *after* the push — at which point `.gitleaks.toml` lines 10–13 say rotation is already required.

**Fix.** `make bootstrap` now fails with an install pointer when `gitleaks` is absent, and prints `gitleaks version` when present. The binary itself still has to be installed on this machine; that is an operator action, so the record stays OPEN.

**Closes when** `gitleaks version` reports ≥ 8.21.0 on the build machine and `G0.3` and `G0.3b` both produce a log through `tools/gate.sh`.

---

### `D-00-025` — `make gate-0` ran one of the two scans `.gitleaks.toml` declares G0.3 to be

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `./Makefile` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_gate_0_runs_both_gitleaks_scans_with_the_project_config`
- **Close-proof:** close-proof D-00-025: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: deleted the `$(GATE) G0.3b` line from the gate-0 recipe; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
grep -n "G0.3" Makefile
grep -n "gitleaks detect" .gitleaks.toml .github/workflows/ci.yml
```

Observed before the fix: one `$(GATE) G0.3` line in the `Makefile`, against two scans declared in `.gitleaks.toml` lines 6–7 and two run by `ci.yml`. Neither named `--config`, so the local run could silently use a different ruleset from CI. Expected: the reviewer running the documented gate command gets both assertions G0.3 is defined to be.

**Fix (applied).** `G0.3b` added — `gitleaks detect --source ops/gates` — and `--config .gitleaks.toml` added to both so neither can fall back to the default ruleset.

**Closes when** `make gate-0` writes both `ops/gates/logs/G0.3.<sha8>.log` and `ops/gates/logs/G0.3b.<sha8>.log`.

---

### `D-00-026` — `tools/gate.sh` did not check the scrubber's status on the command line

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-17, triage round 1 · **Owning file** `tools/gate.sh` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_gate_sh_takes_the_capture_failed_path_when_the_command_scrub_fails`
- **Close-proof:** close-proof D-00-026: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: removed the `CMD_SCRUB_STATUS` check from `tools/gate.sh`; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** With a `tools/scrub.py` shim that always exits 3:

```
PV_PYTHON=/tmp/scrub-fail-shim tools/gate.sh PROBE-CAPFAIL -- bash -c 'echo hello; exit 0'
```

Observed before the fix: the *body* pipeline's failure is caught correctly (`exit=CAPTURE-FAILED`, `rc=70`), but the `CMD_DISPLAY` assignment's status was never read, so a scrubber that failed there and succeeded in the body wrote a log with an empty `# cmd=` line and a normal `exit=0` header — a gate log that does not say what was run, with nothing flagging it. Expected: the same `CAPTURE-FAILED` path.

**Fix (applied).** The command-substitution's status (which is the status of the last command in its pipeline — the scrubber) is captured and checked; an empty `CMD_DISPLAY` is treated the same way. Both take the `CAPTURE-FAILED` path and exit `EX_SOFTWARE` (70), which cannot be read as a gate result.

**Closes when** the shim reproduction above produces a log headed `exit=CAPTURE-FAILED` with `# cmd=<UNAVAILABLE>` and returns 70.

---

### `D-00-027` — `make` does not run under PowerShell and T0.7's both-shells transcript does not exist

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `docs/ops/41_RUNBOOK.md` · **Status** OPEN

**Reproduction.**

```
powershell -NoProfile -Command "cd D:\Repo\neverreset; make help"
grep -rl "make bootstrap" ops/
```

Observed: `process_begin: CreateProcess(NULL, printf ...) failed.` / `make (e=2): The system cannot find the file specified.` / `make: *** [help] Error 2` — because `SHELL := /bin/bash` is not a resolvable Windows path outside MSYS. The same command under Git Bash returns the full target list with `rc=0`. The `grep` matches only `ops/gates/PHASE_00.md`, the template: no clean-clone transcript exists for either shell.

**Why it matters.** `70_TASK_PLAN.md` T0.7 sub-task 3 requires proving the clean-clone path "in **both** Git Bash and PowerShell" and recording both transcripts. Neither exists.

**Fix.** Either resolve `SHELL` to a Windows path form when not running under MSYS, or record in `ops/41_RUNBOOK.md` that `make` is Git-Bash-only — and file the PowerShell transcript either way, because T0.7 asks for the transcript whichever answer is right. Left OPEN: it is a runbook decision, and this round does not rewrite specifications.

**Closes when** `ops/` carries a captured clean-clone transcript for each shell and `41_RUNBOOK.md` states which shell `make` is supported in.

---

### `D-00-028` — `D-00-001` was never filed and T0.3's seven pack-level discrepancies are absent

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/defects/DEFECTS.md` · **Status** OPEN

**Reproduction.**

```
grep -c "D-00-001" ops/defects/DEFECTS.md
python -m tools.defect_lint --report
```

Observed: `0`, and a ledger holding only live-probe findings. `72_DEFECT_PROTOCOL.md` R6 names that exact id — "it should be filed as `D-00-001` in the first round" — and T0.3 requires the ledger be seeded with the seven specification discrepancies (`70_TASK_PLAN.md` §24 risks 2–6: the test-path spellings, the counterparty count 4-vs-5-vs-6, `G8.1`'s 31-vs-44 routes, the two eval-tree paths, `G12.1`'s 4-vs-6 relationships). Expected: those seven present, `D-00-001` among them.

**Why it is not filed in this round.** Each of the seven needs its own reproduction — a `grep` naming both spellings and the two documents that disagree — and its own owning phase. Producing them is a triage round against the *design pack*, not against Phase 0's code, and inventing rows without running those greps would be the rumour §5.2 forbids.

**Closes when** `D-00-001` exists with a `grep`-shaped verifying assertion per R8, and the remaining six pack discrepancies are filed with an owning phase each.

---

### `D-00-029` — `ops/cluster-provision.txt` and `ops/decisions/CLUSTER.md` are named deliverables that do not exist

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/cluster-provision.txt` · **Status** OPEN

**Reproduction.**

```
test -s ops/cluster-provision.txt; echo "rc=$?"
ls ops/decisions/
grep -n "cluster-provision" .gitleaks.toml
```

Observed: `rc=1`; `ops/decisions/` holds only `LICENSE_SHA.txt` and `VECTOR_INDEX_VARIANT.md`; and `.gitleaks.toml` line 294 already allowlists `ops/cluster-provision.txt` — the configuration references a file the tree lacks. Both are named in `00_IMPLEMENTATION_MAP.md` §5, in `23_PHASE_GATES.md` §6's deliverable list, and in T0.5's "Creates".

**Fix.** Write both from the `ccloud cluster describe` transcript, stating explicitly that they record a *describe* rather than a *create* — T0.5's own instruction, and what `S5` needs in order not to overclaim.

**Closes when** both files exist, `test -s ops/cluster-provision.txt` returns 0, and `G0.5` cites them.

---

### `D-00-030` — `ops/probes/phase0-probe.sh` does not exist

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/probes/phase0-probe.sh` · **Status** OPEN

**Reproduction.**

```
ls ops/probes/
grep -n "phase0-probe" Makefile
```

Observed: `PROBE_LEDGER.md`, `README.md`, `phase0-probe.ps1` — no `.sh`. `Makefile` prefers the `.sh` and falls through to a PowerShell host when it is absent. T0.3 is explicit about why both must exist from the start: "both must exist from the start or one of them never gets written", and `00_IMPLEMENTATION_MAP.md` §5 prints `phase0-probe.sh / .ps1`.

**Why it matters here specifically.** The CI runner is Linux. A probe that only exists as PowerShell cannot be re-run in the lane that would have caught `D-00-005` before it destroyed anything.

**Fix.** Add the Git Bash script, or amend `00_IMPLEMENTATION_MAP.md` §5 and T0.3 in the same change to declare the probe PowerShell-only. Not both silently.

**Closes when** `ls ops/probes/phase0-probe.sh` succeeds, or the layout authority records the deviation.

---

### `D-00-031` — `.pre-commit-config.yaml` and `gitleaks.yml` are absent and the deviation is unrecorded

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-17, triage round 1 · **Owning file** `.github/workflows/ci.yml` · **Status** OPEN

**Reproduction.**

```
ls .github/workflows/
ls -a | grep pre-commit
grep -rn "pre-commit" docs/EXECUTION/70_TASK_PLAN.md
```

Observed: `ci.yml` only; no `.pre-commit-config.yaml`; and no mention of pre-commit in the task plan, although `20_TDD_STRATEGY.md` line 2088 requires a pre-commit lane and `make bootstrap` prints "`.pre-commit-config.yaml` does not exist yet (T0.7)". T0.7's "Creates" names `.github/workflows/gitleaks.yml`, which does not exist — the scans live in `ci.yml`'s `secrets` job, which is arguably better, but `ci.yml`'s own header block documents every *other* deviation and is silent about this one.

**Why it matters.** A `gitleaks protect --staged` pre-commit hook is what moves the catch to *before* the commit rather than after the push, which is the difference between a mistake and a rotation (`D-00-024`).

**Fix.** Record the `gitleaks.yml` deviation in `ci.yml`'s header block, and either land `.pre-commit-config.yaml` or change the `Makefile` message to name the phase that actually owns it. The `Makefile`'s false-message arm — printing "does not exist yet" when the file exists but `pre-commit` is not on `PATH` — is already fixed.

**Closes when** `ci.yml`'s header names the `gitleaks.yml` decision, and `make bootstrap`'s pre-commit message names a phase that the task plan agrees owns it.

---

### `D-00-032` — the `arn-account-id` scrub rule is entirely subsumed by `bare-account-id`

**Phase** 0 · **Lens** `L-VAC` · **Severity** MINOR(m1) · **Found** 2026-08-17, triage round 1 · **Owning file** `tools/scrub.py` · **Status** OPEN · **Owner** tooling · **Closes by** phase 14

**Reproduction.** Re-run each rule's leak case with that rule removed:

```
python tmp/probe/rule_vacuity.py
```

Observed: five of six rules report `goes red`; `arn-account-id` reports `STILL GREEN (vacuous)`, output `arn:aws:secretsmanager:us-east-1:[REDACTED-ACCOUNT]:secret:provenance/db-AbCdEf`. Every ARN puts `:` on both sides of the account field, which satisfies `bare-account-id`'s lookarounds, and both rules emit the same marker. `test_an_arn_keeps_its_service_and_region_after_the_account_is_redacted` also stays green with the rule deleted; only the bookkeeping set-equality test notices.

**Why MINOR.** Nothing leaks. `test_rule_redacts_the_secret_it_names`'s docstring — "Without the named rule this exact line reaches a committed gate log" — is false for this one rule, which is a claim a test makes about itself rather than a credential exposure.

**Fix.** Either drop `arn-account-id` and let `bare-account-id` carry it, or give it a leak case only it can catch — an ARN whose account is followed by a character `bare-account-id`'s lookahead rejects. As written it is defence-in-depth with a test that cannot see it.

**Closes when** every rule in `RULES` fails its leak case when deleted, verified by the vacuity matrix.

---

### `D-00-033` — `.gitignore` gaps, and `lib64/` spelled `lib60/`

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MINOR(m1) · **Found** 2026-08-17, triage round 1 · **Owning file** `.gitignore` · **Status** CLOSED · **Owner** tooling · **Closes by** phase 14

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_gitignore_covers_the_credential_shapes + tools/tests/test_build_lane_guards.py::test_the_named_eval_baseline_is_not_ignored`
- **Close-proof:** close-proof D-00-033: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: removed `.envrc` from `.gitignore`; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
git check-ignore -v evals/reports/_probe_run.json
git check-ignore -q .envrc; echo "rc=$?"
grep -n "lib60" .gitignore
```

Observed before the fix: the first two reported not-ignored, and `lib60/` — a corruption of `lib64/` from the standard GitHub Python template, matching nothing — was present at line 37. Also uncovered: `.netrc`, `.npmrc`, `.pypirc`, `*.p12`, `*.pfx`, `*.jks`, `id_rsa*`, `secrets.json` (only the `secrets/` *directory* was ignored), `*.tfvars` and `ops/gate-env.local.sh`.

**Why the shapes matter.** `ops/41_RUNBOOK.md` §2.5 and `settings.py` both say the shell exports the environment, which is exactly the direnv use case, so `.envrc` is the most likely next credential file in this tree. `ops/gate-env.sh` advertises itself as "committed and contains no secret", which invites a `*.local.sh` sibling that is not.

**Fix (applied).** `evals/reports/*.json` plus a `!evals/reports/baseline.json` negation (T0.2's named entry, and `22_EVAL_DATASETS.md` §1.4's rule); the ten credential shapes added to the secrets block; `lib60/` → `lib64/`.

**Closes when** `git check-ignore` reports ignored for each of the ten shapes and for `evals/reports/*.json`, and not-ignored for `evals/reports/baseline.json`.

---

### `D-00-034` — `NOTICE` attributes LangGraph to two graphs that appear nowhere in the design pack

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MINOR(m1) · **Found** 2026-08-17, triage round 1 · **Owning file** `./NOTICE` · **Status** CLOSED · **Owner** submission · **Closes by** phase 15

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_notice_attributes_langgraph_to_graphs_that_exist`
- **Close-proof:** close-proof D-00-034: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: restored the invented Librarian and Registrar names in `NOTICE`; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
grep -rn "Librarian\|Registrar" docs/ NOTICE
```

Observed before the fix: zero matches under `docs/` and two in `NOTICE`, which read "graph orchestration for the Librarian, Registrar and Advocate graphs". `00_IMPLEMENTATION_MAP.md` §5 names `ingestion_graph.py` and `advocate_graph.py`; §4.1 names "Interpreter LangGraph, Advocate LangGraph".

**Why it is worth a record at all.** `NOTICE` is a public submission artifact reviewed at `S2` and `S7`, and two invented component names in it read as a project describing a system it did not build.

**Fix (applied).** "graph orchestration for the ingestion and advocate graphs".

**Closes when** `grep -rn "Librarian\|Registrar" .` returns no match outside this ledger.

---

### `D-00-035` — `sabotage_guard --min-count 0` reports a missing file as a false numeric comparison

**Phase** 0 · **Lens** `L-VAC` · **Severity** MINOR(m1) · **Found** 2026-08-17, triage round 1 · **Owning file** `tools/sabotage_guard.py` · **Status** OPEN · **Owner** tooling · **Closes by** phase 14

**Reproduction.**

```
python -m tools.sabotage_guard --min-count 0; echo "rc=$?"
```

Observed: `min-count: FAIL (0 entries < required 0)` and `rc=1`. The failure verdict is correct — the matrix file does not exist yet — but `0 < 0` is not true, so the printed reason is a false statement about a comparison that did not decide anything.

**Fix.** Print the missing-file reason on that line rather than a synthesised comparison, the way the same tool's `--count` path already does.

**Closes when** `python -m tools.sabotage_guard --min-count 0` fails with a reason that names the absent file.

---

### `D-00-036` — `tools/defect_lint.py` never scrubs a reproduction although §5.1 requires it

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MINOR(m1) · **Found** 2026-08-17, triage round 1 · **Owning file** `tools/defect_lint.py` · **Status** OPEN · **Owner** tooling · **Closes by** phase 14

**Reproduction.**

```
grep -n "scrub" tools/defect_lint.py tools/close_proof.py tools/sabotage_guard.py
```

Observed: no matches. `tools/scrub.py`'s docstring lines 16–17 and `72_DEFECT_PROTOCOL.md` §5.1 both state that "every reproduction is scrubbed before it is committed", and `ops/defects/DEFECTS.md` is a committed, gitleaks-scanned file. `defect_lint.py` already parses the reproduction block (`_reproduction_from_block`) and checks `DL07` that it is runnable — it has the text in hand and never checks it is scrubbed. The rule is cultural with no mechanical half.

**Fix.** Add `DL-SCRUB` to `check_records`: `if scrub_text(record.reproduction) != record.reproduction: violation(...)`, naming the line and the rule that fired. `tools/__init__.py` exists, so `from tools.scrub import scrub_text` is a one-line import. The same check belongs in `close_proof.py` for pasted close evidence.

**Closes when** a reproduction block containing an unscrubbed DSN with userinfo makes `python -m tools.defect_lint` exit non-zero, naming the record and the rule that fired.

---



### `D-04-001` — a payment denial is credited as a fulfillment and extinguishes the debt

**Phase** 4 · **Lens** `L-INV` · **Severity** BLOCKER(B1) · **Found** 2026-08-24 by adversarial review, verified by execution · **Owning file** `services/control_plane/app/memory_kernel/pipeline.py` · **Status** AWAITING_REVERIFY

**Reproduction.** Build a `ProposedClaim` with `predicate="payment_not_received"`, `subject_type=COMMITMENT`, `object_value={"currency":"USD","amount":"420.0000",...}` against a `CommitmentRow(committed=420.0000, fulfilled=0.0000, status=ACTIVE)` with an empty ledger, and call `pipeline.build_write_plan`.

```
predicate     : payment_received          predicate     : payment_not_received
decision      : ACCEPTED                  decision      : ACCEPTED
reason codes  : FULFILLMENT_ADMITTED,     reason codes  : FULFILLMENT_ADMITTED,
                COMMITMENT_FULFILLED                      COMMITMENT_FULFILLED
fulfillment   : ADMITTED 420.0000 USD     fulfillment   : ADMITTED 420.0000 USD
commitment    : ACTIVE -> FULFILLED       commitment    : ACTIVE -> FULFILLED
outstanding   : 0.0000                    outstanding   : 0.0000
conflicts     : 0                         conflicts     : 0
```

Byte-identical. An assertion and its denial produce the same ledger movement.

**Mechanism.** `families.coerce_value` sets `PaymentValue(asserted=False)` for `payment_not_received` — its own docstring calls this "the denial flag rule M5 needs". `_apply_payment` (`pipeline.py:1477`) then reads only `value.amount` (:1498) and `value.currency` (:1499); **`value.asserted` is never read**, so the polarity is discarded at the point of use. M5 cannot compensate: the `Family.PAYMENT` branch at `:649` calls `_apply_payment` and `continue`s, while the only `contradiction.match` is at `:747`, making M5 structurally unreachable for this family from the write path.

**It fails the project's own canonical eval.** `quality/22_EVAL_DATASETS.md:908-917` (CX-04) requires `ACCEPTED_WITH_CONFLICT` with `CONFLICT_PAYMENT_DENIAL` and states *"the denial claim is preserved; the ledger is unchanged."* Actual: a **second** USD 40.00 is credited, admitted 40 → 80, outstanding 380 → 340, zero conflicts, no human attention.

**Why it is silent, and why that makes it a BLOCKER rather than a MAJOR.** Every DDL guard is satisfied by the wrong numbers — `ck_commitments_outstanding_identity` (420−420=0), `ck_commitments_fulfilled_needs_payment`, `ck_commitments_outstanding_blocks_fulfilled`. The `fulfillments` row is written `ADMITTED`, so State Proof and the ledger recompute agree with each other permanently. `asserted` is not persisted on the fulfillment row, so the denial is not recoverable after the fact even by a human reading the record.

This inverts the product's central claim. `00_PRODUCT.md` §2.3: an invoice arriving "does not make $186 owed, it makes $186 **claimed**, by a party with a financial interest". Here a counterparty's *denial* does not merely become a fact — it discharges the obligation.

**Fix.** `_is_denial()` in `pipeline.py:1616` is now the single place the write path reads `PaymentValue.asserted`, and the `Family.PAYMENT` branch consults it before any ledger movement. A denial writes **no** fulfillment and raises a `FULFILLMENT_CONFLICT` instead, which is what `22_EVAL_DATASETS.md` CX-04 required all along.

- **Verifying test:** `services/control_plane/tests/kernel/test_payment_denial.py` — 19 tests, including `test_an_asserted_payment_and_a_denial_no_longer_agree`, which is the measurement that found the defect. It asserts `denied.plan.fulfillments == ()` and `len(denied.plan.conflicts) == 1` against a `payment_received` control that produces the opposite. CX-04 and CX-05 are both present and deliberately straddle the 100.00 human-review gate, so a fix that satisfied one by moving the threshold would fail the other.

- **Verifying assertion:** `services/control_plane/tests/kernel/test_payment_denial.py::test_an_asserted_payment_and_a_denial_no_longer_agree + services/control_plane/tests/kernel/test_payment_denial.py::test_cx04_the_denial_does_not_reach_the_ledger`

- **Why AWAITING_REVERIFY and not CLOSED.** Section 5.2 requires a full 40-character fix SHA on a closed record, and there is none: **nothing in this repository is committed.** The fix is on disk, tested, and counterfactually proved — but "closed" asserts a commit a reader could check out, and asserting one that does not exist is the same class of small checkable dishonesty the pack exists to prevent. It closes when the work is committed and the SHA is recorded.

- **Close-proof:** close-proof D-04-001: test FAILED with the fix neutered (exit=1) — PASS. Counterfactual per §7.4: replaced `_is_denial`'s body with `return False`, which is precisely the pre-fix behaviour — the write path stops reading the denial flag. Result **13 failed, 6 passed**; restored, **19 passed**. The six that survive the neutering are the currency-rejection and unmapped-predicate cases, which do not depend on polarity, so the neutering is discriminating rather than blunt.

### `D-04-002` — `subject_local_ref` is never resolved

**Phase** 4 · **Lens** `L-INV` · **Severity** MAJOR(M1) · **Found** 2026-08-24 · **Owning file** `services/control_plane/app/memory_kernel/pipeline.py` · **Status** OPEN · **Owner** kernel

The `agents/runtime/nodes/ingestion.py` write site is the other half and is tracked here rather than as a separate record: the two are one defect, and splitting them would let either be closed while the pair still fails.

**Reproduction.**

```
grep -rn "subject_local_ref" services/ packages/ agents/ | grep -v /tests/
```

Observed — exactly three sites, none of them a resolution:

```
packages/.../provenance_contracts/proposal.py:124    field declaration
packages/.../provenance_contracts/proposal.py:141    exactly-one-of validator
agents/runtime/nodes/ingestion.py:680                the only write site
```

`ClaimCandidate` has no `subject_id`, so an agent-authored claim structurally cannot carry one, and nothing turns the local ref into an id.

`ClaimCandidate` has no `subject_id`, so an agent-authored claim structurally cannot carry one. Consequences, all silent: `claims.subject_id` falls back to the **case id** for a `RELATIONSHIP`-scoped claim; `_normalise` substitutes `uuid.UUID(int=0)`; `snapshot.incumbent_for` therefore never matches; the pipeline takes the `incumbent is None` branch and writes a fresh belief at `version_no = 1` with no supersession and **no conflict row**.

Invariant 2 — beliefs are revisable, and a changed conclusion creates a new version preserving the prior one — is skipped rather than violated loudly. The hero contradiction is dropped.

`uq_beliefs_proposition` is UNIQUE on `(tenant_id, user_id, subject_type, subject_id, predicate)` and carries no `case_id`, so every `RELATIONSHIP`-scoped belief of one predicate collapses onto the nil-subject row. The Northline old/new account pair that `CANONICAL_DECISIONS.md` calls "the sharpest decoy in the corpus" becomes a single belief, and the second distinct case raises `23505`.

### `D-00-044` — a conftest ImportError aborts the whole unit lane and nothing guarded against it

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-24 · **Owning file** `tools/tests/test_build_lane_guards.py` · **Status** OPEN · **Owner** integrator

**Reproduction.**

```
python -m pytest -q -m unit
  786 deselected, 3 errors in 4.79s
  Interrupted: 3 errors during collection
  exit 2
```

Observed: `ImportError: cannot import name 'drafts' from services.control_plane.app.actions`, `cannot import name 'canon' from _support`, and one in `tests/mcp/`. Each is a `conftest.py` or module import failing while a phase is mid-build.

**Why it is a record, and why it is the third of its kind.** pytest aborts the **entire** session on a single collection error. It is not scoped to the broken directory: one unfinished `conftest.py` silences every other test in the run. `1950/2736 tests collected` and then nothing executes.

That is the same shape as two closed defects. `D-00-014`: `testpaths` omitted `tools/`, so 28 scrubber tests were never collected — not in `make test`, not in `make test-fast`, not in CI. `D-00-005`: the `infra/` omission hid 304 CDK tests identically. Both were *suites that existed and nothing ran*, and both read as a clean pass to anyone checking only the summary line.

Two properties make this worse than either:

1. **It exits 2, not 1.** A gate asserting a failing lane returns `1` misreads an aborted lane as something else entirely — and `make` reports its own exit code on top of that, which `STATUS.md` already records as a rule that cost something to learn.
2. **The count still looks plausible.** `1950/2736 tests collected` appears in the output immediately before the interruption, so a reader skimming for a number finds one.

**What closed the structural half.** `test_the_unit_lane_actually_collects` now runs `pytest --collect-only -q -m unit` in a subprocess and asserts zero collection errors, exit 0, and a collected count above a deliberately loose floor. The floor is loose on purpose: its job is to catch "collection aborted" and "the marker expression matches nothing", not to track suite size — a tight floor fails on every legitimate deletion and gets raised until it means nothing.

**What remains open.** The three current errors are in-flight phase work (`app/actions`, `app/events`, `app/mcp`) and clear when those land. The record stays OPEN until the lane collects clean, because the guard going green is the evidence, not the intention.

**Finding 2 — the first remediation could not fire in the configuration this defect describes.** Recorded because it is the sharper half of the record.

`tools/tests/test_build_lane_guards.py` carries `pytestmark = pytest.mark.unit` at module level, so `test_the_unit_lane_actually_collects` lived **inside the lane it guarded**. A collection abort ends the session before any test executes, including that one. Measured:

```
pytest -q -m unit
  occurrences of "test_the_unit_lane_actually_collects" in output:  0
  Interrupted: 3 errors during collection
  1969/2768 tests collected (799 deselected)          exit 2
```

It reported only when invoked in a scope narrow enough to dodge the broken conftests — which is how it was first seen red. Its own docstring stated the mechanism correctly: *"pytest aborts the entire session on a single conftest ImportError, so one broken directory silences every other test in the run."* It did not say that *every other test* includes itself. The `Makefile` offered no narrower scope either: `test` and `test-fast` are single sessions collecting the whole tree.

**The general form.** *No in-session pytest test can guard against session-wide collection abort, because the abort is what prevents it running.* The check must execute outside the session it judges.

**What actually closes it.** `require_collection` in the `Makefile` runs `pytest --collect-only -q <lane>` in its own session before each of `test`, `test-fast`, `test-db` and `test-all`. `--collect-only` performs the same collection and exits 2 on abort, so the recipe fails at the true cause and nothing has to survive the failure it detects. Status is taken from the command directly, never through a pipe — a pipe discards the exit code, which `STATUS.md` records as having produced a false green three separate times. `test_the_makefile_prechecks_collection` keeps the precheck from being deleted; removing it from one lane turns that test red, verified.

**The defect is not static.** Collection errors were observed at 2, then 3, then 6, then 7 across four measurements within a single session as parallel work landed and moved. Throughout, `pytest -m unit` executed nothing and exited 2, and nothing reported it.

**Provenance of this finding.** Seventh in a session-long run of descriptions drifting from mechanisms, and the third of those inside a remediation for another. The distinction that names the whole class: a description that encodes *where failures usually are* rather than *what this failure does*. Here the drift was structural rather than textual — the guard's **placement** encoded the habit.

### `D-00-037` — the real SQL username and cluster FQDN are committed to a repository that becomes public

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MINOR(m1) · **Found** 2026-08-17, triage round 1 · **Owning file** `ops/probes/README.md` · **Status** OPEN · **Owner** security · **Closes by** phase 15

**Reproduction.** A scan that loads `.env` values in-process and prints variable *names* and hit locations only, never values:

```
python tmp/envleak.py
```

Observed: `LEAK-CANDIDATE var=PV_PROBE_DB_URL::user file=ops\probes\phase0-probe.ps1 line=37`, `var=PV_PROBE_DB_URL::hostport file=ops\cluster-probe.txt line=4`, `var=PV_CLUSTER_ID file=tools\tests\test_scrub.py line=20`, and further hits in `ops/probes/README.md`. The **password appears nowhere** — zero hits — which is the reassuring half of the same scan.

**Why it is a record.** The project spends two dedicated rules (`scrub.py`'s `bare-account-id` and `.gitleaks.toml` rule 6) masking a 12-digit AWS account id on the stated grounds that it is "the identifier an attacker needs to target one". A real SQL username plus the exact cluster endpoint is a strictly stronger targeting primitive: half the credential pair plus the address. Leaving both postures in the same repository means one of them is wrong.

**Re-measured 2026-08-24, and now blocking a decision rather than a phase.** The same scan, widened to every directory that is a candidate for commit:

| | files |
|---|---|
| cluster host (FQDN) | **12** |
| cluster id | **9** |
| SQL username | **42** |
| **password** | **0** |

Every hit is inside `ops/`. `gitleaks detect --source ops --no-git` over the same 1.37 MB reports **no leaks found** — correctly, because a hostname and a username are not credential-shaped, which is precisely why this record exists and the scanner cannot replace it.

This is now urgent for a reason it was not on 2026-08-17: **committing `ops/` is a pending action**, it is the only remaining failure in the unit lane (`test_g0_3b_is_a_working_tree_scan_of_ops`), and the repository becomes public at submission. The three outcomes are:

1. **Commit as-is.** The cluster FQDN, cluster id and SQL username become public. Acceptable only if the cluster is destroyed after the hackathon.
2. **Extend `tools/scrub.py` to redact all three, then re-scrub `ops/`.** Preserves every probe result, exit code and verdict — the parts that are actually evidence — and removes the targeting primitive. Cost: it rewrites committed transcripts, which this project otherwise treats as immutable, so it must be a disclosed act rather than a quiet one.
3. **Leave `ops/` untracked.** Keeps `G0.3b` failing, keeps the evidence directory invisible to every git-mode secret scan, and leaves a destroyed transcript unrecoverable — which has already happened once.

Option 2 is the only one that satisfies both postures. It is a human decision because it trades evidence immutability against attack surface, and the password rotation that must precede any push does not by itself close this: a known host plus a known username is half the credential pair.

**Fix.** Decide the threat model once and apply it: either mask the username and host in committed transcripts and use `example-cluster.cockroachlabs.cloud` in docstrings and tests, or drop the bare-account-id rule as inconsistent. Not both.

**Closes when** `python tmp/envleak.py` reports no hit for the SQL username or the cluster FQDN, or `SECURITY.md` records the decision to publish them and why.

---

### `D-00-038` — allowlist C's `never committed` regex was the only one requiring no redaction marker

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MINOR(m1) · **Found** 2026-08-17, triage round 1 · **Owning file** `.gitleaks.toml` · **Status** CLOSED · **Owner** security · **Closes by** phase 14

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_a_secret_on_a_never_committed_line_is_still_reported`
- **Close-proof:** close-proof D-00-038: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: restored the bare `never committed` regex with regexTarget line; the live DSN password on that line stopped being reported. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.**

```
grep -n "never committed" .gitleaks.toml
```

Observed before the fix: the bare phrase `'''never committed'''` among allowlist C's `regexes`. Under `condition = "AND"` with `regexTarget = "line"`, that excused *any* finding on any line containing those two words inside the eleven enumerated `ops/` files. The other three regexes all require an actual redaction marker.

**Why MINOR rather than MAJOR.** Not reproduced against the real scanner — no `gitleaks` binary on this machine (`D-00-024`) — so this is filed on the configuration as read rather than on observed behaviour. It is the widest entry in the file and it costs nothing to narrow.

**Fix (applied).** Narrowed to `<[^<>]*never committed[^<>]*>`, which matches only the angle-bracketed placeholder form the probe scripts actually write (`.gitleaks.toml` line 99) — a form that cannot simultaneously carry a live value.

**Closes when** a planted secret on a line reading `never committed` inside `ops/cluster-probe.txt` is reported by `gitleaks detect --config .gitleaks.toml`.

---

## Closed

None yet.

## Carried debt

None yet. Anything carried must name an owner and a closing phase, and is re-read at every subsequent gate.

---

### `D-00-039` — Workspace packages are not installable with plain pip

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-18 · **Owning file** `packages/python/*/pyproject.toml`

**Reproduction.**

```
$ python -m pip install -e packages/python/provenance_contracts
ERROR: No matching distribution found for provenance-domain==1.0.0

$ python -m pip install -e packages/python/provenance_db
ERROR: No matching distribution found for provenance-contracts==1.0.0
```

Each package declares its siblings as hard `==1.0.0` dependencies. Plain pip resolves those against PyPI, where they do not exist, so two of the four packages fail to install. Until they install, `import provenance_contracts` fails and **the entire test suite fails at collection** — 96 tests that cannot run look identical to 96 tests that do not exist.

**Why MAJOR.** `G0.7` asserts a clean clone can bootstrap and run the unit lane from zero. On this machine that assertion currently depends on knowing to pass `--no-deps`, which is nowhere in the runbook. The CI `bootstrap` job prefers `uv` (which resolves workspace members locally and is unaffected), so **CI would pass while a developer following the runbook fails** — the worst shape for a bootstrap defect, because it is invisible to the thing meant to catch it.

**Workaround in force.** `python -m pip install --no-deps -e packages/python/<name>` for each of the four. Suite then collects and passes 96/96.

**Fix, one of.** Declare the sibling dependencies with a local-path or workspace marker rather than a bare version pin; **or** make `make bootstrap` require `uv` and fail loudly with an install instruction when it is absent, rather than silently falling through to a pip path that cannot work.

**Closes when** a clean clone runs `make bootstrap` followed by `make test-fast` with no manual flags, on a machine with only Python and pip.

### `D-00-040` — the Bedrock identifier form is provider-dependent, and mirror-imaged

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18 · **Owning file** `docs/CANONICAL_DECISIONS.md` · **Status** AWAITING_REVERIFY

- **Close-proof unavailable:** the record's own Closes-when clause names a Phase 7 artefact — a model router with a test that fails if a prefix is added to or stripped from a configured id. No router exists, so no counterfactual can be run and no verifying assertion can name a test that would have caught this. The canon change is real and `packages/python/provenance_contracts/tests/test_settings.py::test_embedding_model_id_is_a_bare_id_and_is_not_rejected` covers the settings-level half, but the pass-through obligation this record exists to protect is unasserted until the router is built.


**Reproduction.**

```
aws bedrock-runtime converse --region us-east-1 --model-id us.zai.glm-5 ...
ValidationException
aws bedrock-runtime converse --region us-east-1 --model-id zai.glm-5 ...
ok
```

Reproduced across four providers. `us.moonshotai.kimi-k2.5`, `us.google.gemma-3-27b-it` and `us.deepseek.v3.2` all return `ValidationException`; the same four ids without the prefix all invoke. Anthropic behaves in exactly the opposite way (`D-00-002`): the bare id is rejected and only the `us.`/`global.` inference-profile form works.

**Why it matters.** `D-00-002` established "Anthropic chat models need an inference-profile prefix" and that is easy to generalise into "Bedrock needs an inference-profile prefix". A router that applies either rule uniformly can call one family and not the other, and the failure surfaces as `ValidationException` at the first live invocation — Phase 7, not Phase 1. The rule is not "add a prefix"; it is "the prefix is part of the configured id and the router must never synthesise one".

**Fix.** `CANONICAL_DECISIONS.md` → *Bedrock model id canon* now carries both identifier rows and an explicit router obligation: configured model ids are passed through unmodified. Evidence `ops/bedrock-probe.txt` Step 3 versus Step 4, which is a deliberate control pair — the same four models, both id forms, in one transcript.

**Closes when** the Phase 7 model router has a test that fails if a prefix is added to or stripped from a configured id.

---

### `D-00-041` — on gitleaks 8.21.x every top-level allowlist is ignored for custom rules

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-18 · **Owning file** `./.gitleaks.toml` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_the_gitleaks_version_floor_is_declared_consistently + tools/tests/test_gitleaks_config.py::test_binary_meets_the_version_floor`
- **Close-proof:** close-proof D-00-041: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: put the 8.21.x floor back in `.gitleaks.toml`; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`. G0.3 and G0.3b were captured through `tools/gate.sh` on gitleaks 8.30.1 at 291ef912.


**Reproduction.** Against a fixture containing the single line `key = AKIAIOSFODNN7EXAMPLE`:

```
8.21.2  custom rule + global [[allowlists]]    -> 1 finding   (allowlist ignored)
8.21.2  custom rule + inline [rules.allowlist] -> 0 findings  (allowlist applied)
8.30.1  custom rule + global [[allowlists]]    -> 0 findings  (allowlist applied)
```

On 8.21.2 the top-level `[[allowlists]]` array is applied to the extended **default** ruleset only. It is silently ignored for `[[rules]]` declared in the same file — with or without `targetRules`, with or without `condition = "AND"`.

**Why it matters.** Allowlists A, A2, B, C and D were all top-level, so every `pv-*` rule ran with no allowlist at all — and the `pv-*` rules are precisely the six shapes this project can leak. Allowlist B named three custom rules in `targetRules` and protected none of them.

**Why MAJOR(M2) and not BLOCKER.** §4.1 is ordered and `B1`–`B4` all name runtime product behaviour. Nothing here reaches production behaviour; it is a vacuously-passing assertion in the build, which is `M2`.

**The reasoning error worth keeping.** The file's own header claimed the stale-binary failure direction was safe: *"a stale scanner makes noise rather than silently passing"*. The opposite was true. An allowlist that is ignored for a rule that never fires in CI is indistinguishable from one that works — nothing was noisy and nothing was protected. A safety argument that depends on a failure being loud must be tested, not asserted.

**Fix.** Version floor raised to 8.30.0 in `.gitleaks.toml`, `.github/workflows/ci.yml` and `docs/ops/41_RUNBOOK.md` §2. `tools/tests/test_gitleaks_config.py::test_binary_meets_the_version_floor` asserts it at runtime, because a downgrade is otherwise silent.

**Closes when** `G0.3` and `G0.3b` are captured through `tools/gate.sh` on a binary ≥ 8.30.0.

---

### `D-00-042` — `allowlist.paths` is a whole-file skip, so `condition = "AND"` never runs

**Phase** 0 · **Lens** `L-VAC` · **Severity** BLOCKER(B4) · **Found** 2026-08-18 · **Owning file** `./.gitleaks.toml` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_gitleaks_config.py::test_no_allowlist_uses_paths + tools/tests/test_gitleaks_config.py::test_transcript_paths_are_scanned_not_skipped`
- **Close-proof:** close-proof D-00-042: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: re-added the deleted allowlist C as a `paths` entry over `ops/cluster-probe.txt` and `ops/gates/logs/`; both named tests went red and gitleaks reported the planted DSN on neither line. Transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** One file containing a live-shaped CockroachDB DSN and **no redaction marker anywhere in it**:

```
at ops/cluster-probe.txt (a path allowlist C named):
  scanned ~0 bytes (0) ... no leaks found
the identical bytes renamed to ops/zzz.txt:
  scanned ~109 bytes ... leaks found: 1
```

gitleaks evaluates `allowlist.paths` as a file-level skip, decided before the file is read. There is no content at that point, so the `regexes` half of an `AND` condition is never evaluated and the file is not scanned at all.

**Why BLOCKER(B4).** `ops/cluster-probe.txt` is the file the probe scripts write live credentials *through the scrubber* into, and this repository becomes public at submission. A scrubber defect on any one line would have produced a committed, published credential that the gate protecting it could not see. Eight files were exempt: four probe transcripts, the probe ledger, the probe README and script, the decision records, `ops/defects/DEFECTS.md`, and `tools/tests/test_scrub.py`. Rule `B4` reads "renders a UI or trace element with no backing row"; the closest fit here is the same failure in the evidence layer — the gate reported a protection it did not have.

**The compounding detail.** Allowlist C was itself written to close `D-00-010`, a narrower version of this bug. The fix for a too-broad allowlist introduced a much broader one, and the header comment for it explained at length why it was safe. Every claim in that comment was about `condition = "AND"`, and `condition = "AND"` was never reached.

**Fix.** Every `paths` key removed. The policy is now one line — *use value-scoped `regexes` only, never `paths`* — with the measurement above recorded beside it. Allowlist C was **deleted rather than rewritten**: scanning the real 310 KB `ops/` tree without it produces exactly one finding, a Bedrock model id, now handled by a value-scoped entry. An allowlist that excuses nothing real is pure attack surface. `tools/tests/test_gitleaks_config.py::test_no_allowlist_uses_paths` and `::test_transcript_paths_are_scanned_not_skipped` both guard the regression.

**Closes when** `G0.3` and `G0.3b` are captured through `tools/gate.sh` and the two named tests are green in that run.

---

### `D-00-043` — G0.3b was the same scan run twice, and could not see `ops/`

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-18 · **Owning file** `./Makefile` · **Status** CLOSED

- **Fix commit:** `62e3f1c27cb60997d54720f86f17a0957320196a`
- **Verifying assertion:** `tools/tests/test_build_lane_guards.py::test_g0_3b_is_a_working_tree_scan_of_ops + tools/tests/test_gitleaks_config.py::test_working_tree_scan_of_ops_detects_a_planted_credential`
- **Close-proof:** close-proof D-00-043: test FAILED with the fix neutered (exit=1) — PASS. Manual counterfactual per §7.4: reverted G0.3b to `--source ops/gates` in git mode; transcript in `ops/tdd/CLOSE_PROOF_COUNTERFACTUALS.txt`.


**Reproduction.** With a CockroachDB DSN carrying a real-shaped password planted at `ops/gates/_canary.md`:

```
gitleaks detect --source ops/gates --config .gitleaks.toml --redact --exit-code 1
  1 commits scanned. scanned ~2751832 bytes (2.75 MB) ... no leaks found
gitleaks detect --source ops --config .gitleaks.toml --redact --no-git --exit-code 1
  scanned ~90770 bytes (90.77 KB) ... leaks found: 1
```

Two independent faults. In git mode gitleaks scans the repository containing `--source` and ignores the path entirely — the byte count and commit count were identical to the `--source .` scan on the line above it, so it was never a second scan. And `ops/` was untracked (`git ls-files ops/` returned 0), so no git-mode scan could reach it regardless.

**Why it matters.** `.gitleaks.toml` declared G0.3 to be two scans, the second covering "the committed artefacts most likely to carry a command line with a credential". That directory was the one directory neither scan could see. Compounding: because `ops/` was untracked, the probe evidence an agent destroyed had no `git checkout` recovery and every probe had to be re-run against the live cluster.

**Fix.** G0.3b is now `--source ops --no-git`, in both the `Makefile` and `ci.yml`, with the reasoning recorded at both sites. `ops/` is tracked as of commit `62e3f1c`. `tools/tests/test_gitleaks_config.py::test_working_tree_scan_of_ops_detects_a_planted_credential` re-plants the canary and asserts it is reported.

**Closes when** `G0.3b` is captured through `tools/gate.sh` and `git ls-files ops/` is non-empty at the signing commit.

---

## Triage round 2 — the `G-1` inbox merge

`ops/defects/inbox/G-01.L-DRIFT.md` carried nine `L-DRIFT` findings from the T1.2
and T1.3 builds. All nine are merged below and the inbox file is kept, per §11.1:
it is the record of what was looked at.

**How these severities were decided.** §4.1 is ordered and the triager applies it
to the reproduction, not to the reporter's framing. `B1`–`B4` all name runtime
product behaviour — an invariant violation, a tenant or user boundary crossing,
an external side effect from uncommitted state, a rendered element with no
backing row. Phase 1 builds pure domain functions with no runtime, no database
and no surface, so every reproduction answers `B1`–`B4` **no**. Eight land on
`M3`: a name, a return type, a file path, an enum member or a module location on
which a contract or an acceptance clause depends, spelled two ways in two
documents. One lands on `M2` (`D-01-004`), because the acceptance clause as
written passes while answering about the wrong state machine. None is `m1`: §R5
confines the cosmetic carve-out to divergences nothing depends on, and each of
these is depended on by a `T1.x` acceptance clause or by `T1.6`'s doc lint.

**Two were submitted knowing they were already addressed**, and §6 makes that the
triager's call rather than the reporter's. Both are still filed. `D-01-001` is
genuinely corrected in place — `70_TASK_PLAN.md` line 292 now carries a dated
correction block, and the wrong lifecycle survives only inside it as the record
of the correction. **`D-01-006` is not.** The inbox header says it was corrected
in `70_TASK_PLAN.md`; line 319 still reads *"reject negative committed amounts"*
with no correction note, so what was scoped was the implementation and not the
clause. That is precisely the state the finding predicted — "the next builder
will implement it that way" — and it is filed at full severity.

**Why none of the nine closes here.** Every owning file is under `docs/`. §R8
gives documentation defects a provisional close path — the change-control rule
plus an `rg`/`grep` command returning zero hits for the old spelling — but
`tools/defect_lint.py`'s `DL11` requires a gate id or a pytest node id, and a
`grep` command is neither. So a documentation defect cannot reach `CLOSED`
without either a pytest wrapper around the grep or a change to the linter.
Recorded here rather than worked around; see the sweep note in
`ops/tdd/close_proof_sweep.txt`.

| id | phase | lens | sev | summary | status |
|---|---|---|---|---|---|
| `D-01-001` | 1 | `L-DRIFT` | **MAJOR(M3)** | T1.2 states the trigger lifecycle as a machine over `SCHEDULED`, `WOKEN` and `ERROR`, which are not members of `TriggerState` | TRIAGED |
| `D-01-002` | 1 | `L-DRIFT` | **MAJOR(M3)** | `legal_transition` is declared `-> bool` by 11_CONTRACTS §4 and `-> TransitionVerdict` by the task plan and 20_TDD_STRATEGY §5.1 | TRIAGED |
| `D-01-003` | 1 | `L-DRIFT` | **MAJOR(M3)** | the exception is `IllegalTransition` in the canon register and `IllegalTransitionError` in 11_CONTRACTS §4 and its own §20.1 tests | TRIAGED |
| `D-01-004` | 1 | `L-DRIFT` | **MAJOR(M2)** | the T1.2 acceptance clause elides the machine argument, and `RESOLVED` is a member of two machines, so the assertion passes while answering about the wrong one | TRIAGED |
| `D-01-005` | 1 | `L-DRIFT` | **MAJOR(M3)** | 11_CONTRACTS §5 defines no `money.py`; the task plan creates one and assigns `Money`'s whole acceptance list to it | TRIAGED |
| `D-01-006` | 1 | `L-DRIFT` | **MAJOR(M3)** | the T1.3 constructor clause bans negative committed amounts, which contradicts §5.1 and §4.3 requiring a negative outstanding to be representable | TRIAGED |
| `D-01-007` | 1 | `L-DRIFT` | **MAJOR(M3)** | the authority grid is `provenance_domain/kernel/authority.py` in 12_KERNEL_ALGORITHMS §3.2 and `provenance_domain/authority.py` in 11_CONTRACTS §5.2 and the task plan | TRIAGED |
| `D-01-008` | 1 | `L-DRIFT` | **MAJOR(M3)** | 11_CONTRACTS §5.2's code block advertises `AUTHORITY_BANDS` in `__all__`, a name it never defines | TRIAGED |
| `D-01-009` | 1 | `L-DRIFT` | **MAJOR(M3)** | 11_CONTRACTS §5 contains no authority ranking although the task plan and §5's own title assign one to it | TRIAGED |
| `D-00-044` | 0 | `L-VAC` | **MAJOR(M2)** | A conftest ImportError aborts the whole unit lane, so zero tests run and nothing guarded against it | OPEN |
| `D-04-001` | 4 | `L-INV` | **BLOCKER(B1)** | A `payment_not_received` denial is credited as an ADMITTED fulfillment; the counterparty's denial extinguishes the debt | AWAITING_REVERIFY |
| `D-04-002` | 4 | `L-INV` | **MAJOR(M1)** | `subject_local_ref` is never resolved, so every agent-authored claim forks belief lineage at the nil UUID instead of revising the incumbent | OPEN |

---

### `D-01-001` — the trigger lifecycle is described over enum members that do not exist

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18, T1.2 build · **Owning file** `docs/EXECUTION/70_TASK_PLAN.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-1`

**Reproduction.**

```
python -c "from provenance_domain.enums import TriggerState; print(sorted(m.value for m in TriggerState))"
```

Observed: `['ARMED', 'DISARMED', 'EXPIRED', 'FIRED']` — zero overlap with `{SCHEDULED, WOKEN, ERROR}`.
Expected: the lifecycle expressed as `(state, result) -> state` per `16_TRIGGER_DSL.md` §9.10, with `TriggerState` unchanged.

**Notes.** Built from that bullet alone a builder adds three members to a canon-frozen enum, and it surfaces at Phase 10 where wakeups are persisted — far from where it was introduced. Corrected in place at `70_TASK_PLAN.md` line 292 by the T1.2 builder; the wrong spelling survives only inside the dated correction block, which is the record of the correction rather than a live instruction. Filed anyway: §6 gives the suppress-or-file decision to the triager, and a correction with no record is indistinguishable from an edit nobody reviewed.

---

### `D-01-002` — `legal_transition` has two declared return types

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18, T1.2 build · **Owning file** `docs/specs/11_CONTRACTS.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-2`

**Reproduction.**

```
grep -n "legal_transition" docs/specs/11_CONTRACTS.md docs/EXECUTION/70_TASK_PLAN.md docs/quality/20_TDD_STRATEGY.md
```

Observed: 30 hits in `11_CONTRACTS.md`, 3 in the task plan, 4 in `20_TDD_STRATEGY.md`. §4 declares `-> bool` and §20.1 writes `assert not legal_transition(...)`; the task plan requires a verdict object carrying the rejecting guard and reason code.
Expected: one declared return type.

**Notes.** Reconciled in code by a verdict whose `__bool__` is its legality, so both call styles are literally true. The documents still disagree, and a later gate asserting either shape in isolation will read as a contract violation.

---

### `D-01-003` — the illegal-transition exception is spelled two ways

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18, T1.2 build · **Owning file** `docs/specs/11_CONTRACTS.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-3`

**Reproduction.**

```
grep -rno "IllegalTransitionError|IllegalTransition\b" -E docs/
```

Observed: 5 occurrences of the bare `IllegalTransition` and 9 of `IllegalTransitionError`, in documents of different authority.
Expected: one name.

**Notes.** Shipped as one class with both names bound, so a second exception type cannot come into existence. A closed reason-code set is only as closed as the exception that carries it.

---

### `D-01-004` — the T1.2 acceptance clause elides the machine, and `RESOLVED` belongs to two

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M2) · **Found** 2026-08-18, T1.2 build · **Owning file** `docs/EXECUTION/70_TASK_PLAN.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-4`

**Reproduction.**

```
python -c "from provenance_domain.enums import CaseStatus, ConflictStatus; print('RESOLVED' in {m.value for m in CaseStatus}, 'RESOLVED' in {m.value for m in ConflictStatus})"
```

Observed: `True True`.
Expected: a signature that cannot silently answer about the wrong machine.

**Notes.** `M2` rather than `M3`: the acceptance clause is an assertion, and a machine-defaulting two-argument form makes it pass while answering confidently about `CaseStatus` when the caller meant `ConflictStatus` — an assertion that cannot distinguish the broken system from the working one. Deliberately not guessed by the builder. Needs a spec amendment, not an implementation choice.

---

### `D-01-005` — no `money.py` in the specification that owns `Money`

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18, T1.3 build · **Owning file** `docs/specs/11_CONTRACTS.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-5`

**Reproduction.**

```
grep -n "^### 5\.|money\.py" -E docs/specs/11_CONTRACTS.md
```

Observed: §5's three subsections are `invariants.py` (line 1577), `authority.py` (1736) and `derivations.py` (1801); no `money.py` anywhere. §5.1 models money as a bare `Decimal` beside a sibling currency string, and the only `Money` class in the pack is the pydantic one in `provenance_contracts.base` (§6).
Expected: either a §5 subsection describing the domain-side `Money`, or the acceptance clauses rewritten against the §5 shape.

**Notes.** There are now two money types in the system with no document relating them. That is the condition under which a float creeps into one of them.

---

### `D-01-006` — the T1.3 constructor clause bans a value the kernel must be able to represent

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18, T1.3 build · **Owning file** `docs/EXECUTION/70_TASK_PLAN.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-6`

**Reproduction.**

```
grep -n "Over-fulfilment|do not silently clamp" -E docs/specs/11_CONTRACTS.md docs/specs/12_KERNEL_ALGORITHMS.md
grep -n "reject negative committed amounts" docs/EXECUTION/70_TASK_PLAN.md
```

Observed: `11_CONTRACTS.md:1668` "Never clamps. Over-fulfilment yields a negative outstanding"; `12_KERNEL_ALGORITHMS.md:777` §4.3 makes that visibility the point of "do not silently clamp"; and `70_TASK_PLAN.md:319` still reads "reject negative committed amounts".
Expected: the ban scoped to the committed argument, not to the type.

**Notes.** A `Money` that refuses negatives cannot hold the over-fulfilment anomaly the kernel is required to surface. **The inbox header states this was already corrected in `70_TASK_PLAN.md`; it was not.** Line 319 is unchanged and carries no correction note — what was scoped to the argument was the T1.3 implementation. The clause still reads as a type-level ban, which is exactly the outcome the reporter predicted for the next builder, so this is filed at full severity rather than as a closed loop.

---

### `D-01-007` — the authority grid has two module paths

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18, T1.3 build · **Owning file** `docs/specs/12_KERNEL_ALGORITHMS.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-7`

**Reproduction.**

```
grep -rn "authority.py" docs/specs/ docs/EXECUTION/70_TASK_PLAN.md
```

Observed: `12_KERNEL_ALGORITHMS.md:573` names `provenance_domain/kernel/authority.py`; `11_CONTRACTS.md:37`, `11_CONTRACTS.md:1736` and `70_TASK_PLAN.md:311` all name `provenance_domain/authority.py`.
Expected: one path.

**Notes.** T1.6's doc lint compares the shipped matrix against the spec table and needs to be told which file it is reading. A file path an assertion depends on is squarely inside §R5's M3 carve-out.

---

### `D-01-008` — `__all__` advertises a name the module never defines

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18, T1.3 build · **Owning file** `docs/specs/11_CONTRACTS.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-8`

**Reproduction.**

```
grep -n "AUTHORITY_BANDS|AUTHORITY_SCORES" -E docs/specs/11_CONTRACTS.md
```

Observed: line 1749 declares `__all__` with `AUTHORITY_BANDS` in it; the module defines `AUTHORITY_SCORES` at line 1771 and reads it at 1796. `AUTHORITY_BANDS` is defined nowhere.
Expected: `__all__` matching the module.

**Notes.** Transcribed verbatim this is an `ImportError` at first use. The same block's chained `.get(...).get(...)` also fails `mypy --strict`, so the specification's own code does not typecheck under the settings the specification mandates.

---

### `D-01-009` — §5 carries no authority ranking although two documents assign one to it

**Phase** 1 · **Lens** `L-DRIFT` · **Severity** MAJOR(M3) · **Found** 2026-08-18, T1.3 build · **Owning file** `docs/EXECUTION/70_TASK_PLAN.md` · **Status** TRIAGED

- **Merged from:** `ops/defects/inbox/G-01.L-DRIFT.md` finding `F-L-DRIFT-9`

**Reproduction.**

```
grep -n "predicate_family|authority_for|rank" -E docs/specs/11_CONTRACTS.md
```

Observed: §5.2 ships `predicate_family` and `authority_for` only; the ordering that decides an incumbent is `12_KERNEL_ALGORITHMS.md` §3.3 `decide()`, in a Phase 4 module.
Expected: the task plan pointing at §3.3 for the ranking half.

**Notes.** The risk is a reader treating an authority ranking as a disposition. Gates H1–H6 short-circuit before the ranking is consulted, and the hero conflict resolves on H5 (monetary exposure ≥ 100.00), not on the authority margin — so a ranking that "wins" does not mean the conflict resolves that way.

---

### `D-00-045` — `tools/scrub.py` had no rule for the Google AI Studio key shape

**Phase** 0 · **Lens** `L-RENDER` · **Severity** MAJOR(M1) · **Found** 2026-08-24 · **Owning file** `tools/scrub.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `tools/tests/test_scrub.py::test_rule_redacts_the_secret_it_names[google.genai.errors.ClientEr] + [Client(api_key='AIzaSyC7QwEr] + [GOOGLE_API_KEY=sk-notgoogle-]`
- **Close-proof:** pending a commit; the three leak cases failed before the rule existed (`4 failed, 37 passed`) and pass after (`41 passed`), transcript in this session's log.

**Reproduction.**

```
python -c "from tools.scrub import scrub_text; print(scrub_text(\"400 calling https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent?key=AIzaSyC7QwErTyUiOpAsDfGhJkLzXcVbNm123\"))"
```

Observed before the fix: the key came through verbatim. After: `?key=[REDACTED-GOOGLE-API-KEY]`, with `gemini-3.7-flash` intact.

**Why it matters.** The Gemini Developer API takes its key as a **query parameter**, not an `Authorization` header, so every `google-genai` error that renders its failing request URL carries the live key in the message body. `ops/probes/gemini_probe.py` writes exactly those messages into `ops/gemini-probe.txt`, which is committed to a repository that becomes public at submission. No existing rule matched: `url-credential` requires a `user:secret@` userinfo and a query string has none, and `named-secret-assignment`'s closed suffix list ran `HMAC_KEY|SIGNING_KEY|PRIVATE_KEY|SECRET_KEY|_SECRET|_TOKEN|_PASSWORD` — `_API_KEY` was not a member. The AI Studio key is the *only* credential on the Gemini path (no service account, no ADC, no IAM), so exposing it hands over the entire model budget.

**Fix (applied).** New `google-api-key` rule anchored on the `AIza` prefix and matching bare, plus `_API_KEY` added to the `named-secret-assignment` suffix list for keys with no distinguishing prefix. The quantifier is `{30,}` rather than the published 39-character width **on purpose**: pinning the exact length means a key of any other length passes through in full, and under-redaction is the failure that cannot be undone. `useDefault = true` in `.gitleaks.toml` means the upstream `gcp-api-key` rule is the second filter; `gitleaks detect --source ops` reports `no leaks found` over 1.4 MB.

**Closes when** the three leak cases pass, the live key is absent from `ops/gemini-probe.txt`, and gitleaks is clean over `ops/`.

---

### `D-00-046` — PB-G2 recorded PASS for a model that answered nothing

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-24 · **Owning file** `ops/probes/gemini_probe.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `tools/tests/test_gemini_probe.py::TestTheChatVerdictIsNotVacuous`
- **Close-proof:** pending a commit; the five verdict tests failed before `chat_verdict` existed and pass after.

**Reproduction.** The first live run, verbatim from `ops/gemini-probe.txt`:

```
PASS  gemini-3.7-flash             reply=':' tokens=20  (Tier R canon)
PASS  gemini-3.5-flash-lite        reply='ok' tokens=9  (Tier E canon)
PASS  gemini-3.6-flash             reply='' tokens=21   (Tier R fallback)
PASS  gemini-3.5-flash             reply='' tokens=21   (alternate Tier E)
```

Three of four replies are unusable and all four are PASS.

**Why it matters.** Every Flash tier above Lite thinks by default, and `max_output_tokens` is **one allowance shared between thinking and the visible answer** — it is not a cap on the reply. At `max_output_tokens=16` the allowance was gone before the first visible token: measured `thoughts_token_count=12`, `candidates_token_count=None`, `finish_reason=MAX_TOKENS`, so `response.text` was `''`. An empty string is not an exception, and the probe's verdict was `PASS` for any call that did not raise. A verdict that cannot tell `'ok'` from `''` is not a verdict, and this one sat in the transcript whose entire purpose is to be believed — the artefact `CANONICAL_DECISIONS.md` cites as the evidence that the ids are settled.

The router is **not** affected: it budgets 8192/16000, and `agents/runtime/model_router/router.py:482` treats truncation as a schema failure. The defect is confined to the probe, which is worse rather than better — the probe is what the canon quotes.

**Fix (applied).** `chat_verdict(text, finish_reason)` extracted as a pure function so the decision that edits canon is testable with no key, no network and no SDK. Three-valued as the file's own doctrine requires: text present → PASS; empty with `MAX_TOKENS` → **CANNOT RUN**, because that measures the probe's budget and not the id (calling it FAIL would licence demoting a working Tier R model on our own misconfiguration, which is `D-00-005`); empty with anything else → FAIL. `CHAT_MAX_OUTPUT_TOKENS` raised to 2048 against a measured floor of 84–133 thinking tokens.

**Closes when** `TestTheChatVerdictIsNotVacuous` passes and a re-run records a non-empty reply per id.

---

### `D-00-047` — PB-G6 recorded a capability FAIL that was a property of its own fixture

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-24 · **Owning file** `ops/probes/gemini_probe.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `tools/tests/test_gemini_probe.py::TestTheMultimodalProbeUsesAnImageTheAPIAccepts`
- **Close-proof:** pending a commit; the three image tests failed before `PROBE_IMAGE` existed and pass after. The live differential is recorded below.

**Reproduction.** Same model, same minute, same request shape, three fixtures:

```
FAIL  1x1 transparent (current probe)    bytes=   75  400 INVALID_ARGUMENT: Unable to process input image
PASS  8x8 solid red                      bytes=   75  reply='Solid red square.'
PASS  64x64 solid blue                   bytes=  168  reply='Solid blue square.'
```

**Why it matters.** The failing and passing fixtures are **the same 75 bytes**. The API accepted the request shape and rejected the image content, so the FAIL said nothing about multimodal support. Acting on it would have kept an external OCR dependency in the ingestion pipeline and forfeited the multimodal submission category — on the evidence of one transparent pixel. This is `D-00-005` in its purest form: a probe that could not perform the action reported that the capability had failed. The probe's own docstring asserted a 1x1 PNG was "enough to establish the request shape is accepted"; that sentence was a prediction, and it was wrong.

**Fix (applied).** `PROBE_IMAGE` is a 64x64 two-tone PNG, and `probe_image_size()` reads width and height from the IHDR rather than from a comment, so the 1x1 regression is caught against the bytes that will actually be uploaded. Two tones rather than one so a correct reply has to describe something the model saw; the re-run returns `'Red and blue.'`.

**Closes when** `probe_image_size()` reports at least 8x8 and PB-G6 records PASS with a reply describing the image.

---

### `D-00-048` — `probe_verdict` let a longer model id decide a shorter one's verdict

**Phase** 0 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-24 · **Owning file** `agents/runtime/tools/smoke.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `agents/runtime/tests/test_model_router.py::test_a_longer_id_containing_a_shorter_one_does_not_decide_its_verdict`
- **Close-proof:** pending a commit; the demonstration below returned `PASS` before the fix and returns `FAIL` after.

**Reproduction.**

```
python -c "
from agents.runtime.tools.smoke import probe_verdict
t = '''  PASS  PB-G2  invoke gemini-3.5-flash-lite  reply=ok
  FAIL  PB-G2  invoke gemini-3.5-flash        404 NOT_FOUND'''
print(probe_verdict('gemini-3.5-flash', t))"
```

Observed before the fix: `PASS`, for an id the transcript records as `FAIL`.

**Why it matters.** `gemini-3.5-flash` is a prefix of `gemini-3.5-flash-lite`, the probe invokes both, and both lines sit in one transcript — so whichever appeared first decided the verdict for the other. This is the same mistake the function's own docstring warns about one level up: it argues carefully that a transcript is not a result, then reads the wrong line. Latent today because `gemini-3.5-flash` is not in `ALLOWED_MODEL_IDS`, but promoting the alternate Tier E is a one-word configuration change, and the failure is silent and green.

**Fix (applied).** `_names_exactly()` requires the match to be flanked by something that is not an id character (`[0-9A-Za-z._-]`), with `re.escape` because a version number contains `.`. Two further tests were added alongside: a PASS may not be read off the PB-G1 **listing** section, which prints every enumerable id and is labelled `REFERENCE ONLY, NOT PROOF`.

**Closes when** the substring test passes and every canon id resolves to its own transcript line.

---

### `D-12-001` — `TimePair` rendered its label and value welded together

**Phase** 12 · **Lens** `L-RENDER` · **Severity** MINOR(m1) · **Found** 2026-08-24 · **Owning file** `apps/web/src/styles/app.css` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** measured in-browser: for every `.pv-time-field`, `value.left - label.right >= 2`.
- **Close-proof:** pending a commit; measured 0px gap before, 12px (RECORD) and 268px (VALID) after, at viewport 485.

**Reproduction.** The rule is statically checkable, and fails before the fix:

```
node -e "const c=require('fs').readFileSync('apps/web/src/styles/app.css','utf8');const m=c.match(/\\.pv-time-field\\s*\\{[^}]*\\}/)[0];console.log(m);process.exit(/display:\\s*flex/.test(m)?0:1)"
```

Exit `1` before the fix (`display: block`), `0` after. The visual symptom needs a
browser: `cd apps/web && npm run dev`, open `/dashboard`, and evaluate

```
[...document.querySelectorAll('.pv-time-field')].map(f=>{const l=f.querySelector('.pv-label').getBoundingClientRect(),v=f.querySelector('.pv-time-value').getBoundingClientRect();return Math.round(v.left-l.right)})
```

Observed before the fix: `[0,0,0,0]`, rendering as `RECORD TIMELAST ACTIVITY 18
SEP 2026, 10:05 GMT-4`. After: `[268,12,268,12]` at viewport 485.

**Why it matters.** `.pv-label` and `.pv-time-value` are two inline `<span>`s inside a `display: block` div, and JSX strips the newline whitespace between sibling elements — so they concatenate with no separator. Valid time versus record time is the distinction the whole record rests on (`TimePair.tsx:11`), and a reader who cannot see where the label ends cannot tell which clock they are reading. Invisible to the component tests, which assert on text content and never on layout.

**Fix (applied).** `.pv-time-field` is `display: flex` with `justify-content: space-between`, putting the value at the right edge of the coloured rule the field already draws full-width, plus `min-width: 0` and `overflow-wrap: anywhere` on the value so a long timestamp wraps instead of widening the column.

**Closes when** no `.pv-time-field` reports a label/value gap below 2px at 390px and 1440px.

---

### `D-12-002` — a screen-reader-only span escaped its scroll container and scrolled the page sideways

**Phase** 12 · **Lens** `L-RENDER` · **Severity** MINOR(m1) · **Found** 2026-08-24 · **Owning file** `apps/web/src/styles/app.css` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** measured in-browser at viewport 485: `documentElement.scrollWidth - clientWidth == 0`.
- **Close-proof:** counterfactual run this session — reverting `position: relative` restored `bodyOverflowPx: 15, horizontalScroll: true` on the same real page at the same viewport; restoring it returned `0` / `false`.

**Reproduction.** The containing-block requirement is statically checkable, and
fails before the fix:

```
node -e "const c=require('fs').readFileSync('apps/web/src/styles/app.css','utf8');const m=c.match(/\\.pv-table-scroll\\s*\\{[^}]*\\}/)[0];console.log(m);process.exit(/position:\\s*(relative|absolute|sticky)/.test(m)?0:1)"
```

Exit `1` before the fix, `0` after. The symptom itself needs a browser:
`cd apps/web && npm run dev`, open `/judge` at 390px wide, and evaluate

```
document.documentElement.scrollWidth - document.documentElement.clientWidth
```

Observed before the fix: `15`. After: `0`, with the table still reporting 169px
of internal scroll.

**Why it matters.** The whole page scrolled horizontally on a phone. The cause was **not** the table: measured at that width the scroller reports `scrollWidth 622` inside `width 453` and every ancestor reports no overflow, so `overflow-x: auto` was doing its job. `.pv-sr-only` is `position: absolute`, and `.pv-table-scroll` was `position: static`, so the span's containing block was the **initial** one — the document. It positioned against the page rather than the scroller and pushed it 15px wide, from a one-pixel element nobody can see. Those spans are how an absent value stays absent for a screen reader instead of being read as the em-dash beside it, so removing them was never an option.

Worth recording separately: the first offender scan reported the table cells themselves, because a `getBoundingClientRect().right > viewport` test also catches elements sitting legitimately inside a horizontally scrollable container. Filtering those out emptied the list and left the real cause.

**Fix (applied).** `position: relative` on `.pv-table-scroll`, so absolutely positioned descendants resolve against the scroller.

**Closes when** every route reports zero body overflow at 390px with the table still scrolling internally.

---

### `D-00-049` — `.env.example` omits the one variable `PV_PLATFORM=local` refuses to start without

**Phase** 0 · **Lens** `L-DRIFT` · **Severity** MAJOR(M2) · **Found** 2026-08-24 · **Owning file** `.env.example` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `grep -q '^PV_LOCAL_AUTH_SECRET=' .env.example`
- **Close-proof:** pending a commit; the grep exited 1 before the fix and 0 after.

**Reproduction.**

```
grep -c '^PV_LOCAL_AUTH_SECRET=' .env.example; echo "exit=$?"
```

Observed before the fix: `0`, exit `1`.

**Why it matters.** `.env.example` presents `local` as the reviewer's mode — "a
laptop with no cloud account. Needs no provider variables at all". It needs one,
and the API refuses to start without it: `PV_PLATFORM=local requires
PV_LOCAL_AUTH_SECRET -- unset. It signs the development issuer's tokens; there
is no default, because a default signing key verifies forged tokens.` The
refusal is correct and the template contradicted it, so a judge following the
template hits a hard stop with no documented variable to set. The hackathon's
spin-up requirement is a step-by-step guide a stranger can follow.

**Fix (applied).** `PV_LOCAL_AUTH_SECRET=` added to the template with a
generator one-liner, left **empty** on purpose — there is no such thing as a
shareable signing key — and the `local` description corrected from "no provider
variables at all" to "no CLOUD provider variables".

**Closes when** the grep passes and a clean `.env` built from the template alone
starts the API.

---

### `D-08-001` — `make run-api` loaded no environment and could not start

**Phase** 8 · **Lens** `L-VAC` · **Severity** MAJOR(M1) · **Found** 2026-08-24 · **Owning file** `Makefile` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `make run-api` reaches `Application startup complete` and `GET /v1/version` returns 200.
- **Close-proof:** pending a commit; before the fix the server exited during startup with 8 `Field required` errors, after it serves 200.

**Reproduction.**

```
make run-api 2>&1 | head -40
```

Observed before the fix:

```
APP_BASE_URL
  Field required [type=missing, input_value={}, input_type=dict]
  ... 8 errors ...
```

**Why it matters.** `input_value={}` is the tell: `Settings` saw an **empty
environment**. `settings.py:331` declines `env_file` deliberately and correctly —
a repository-root dotenv holding a live credential must not be parsed by every
test that happens to run from the repo root — and delegates to the shell:
"The shell exports the environment (`ops/41_RUNBOOK.md` §2.5); this object only
reads it." Nothing did. Several `Makefile` targets grep single variables out of
`.env` ad hoc, but `run-api` loaded nothing, so the control plane could not be
started by its own documented command.

Two `.env` gaps surfaced underneath it, both now closed: the file predated the
settings refactor and was missing all 8 core variables of `.env.example` §2, and
`PV_PLATFORM` had no value.

**Fix (applied).** `set -a; [ -f .env ] && . ./.env; set +a;` before the server
command, which keeps the design intact — the shell exports, `Settings` only
reads. `.env` is confirmed shell-safe to source (39 assignments, no value
containing a space, quote, `$` or backtick).

**Closes when** `make run-api` serves `GET /v1/version` 200 from a clean shell.

---

### `D-08-002` — `--loop asyncio` selects the loop psycopg refuses, and the recipe said it was the fix

**Phase** 8 · **Lens** `L-DRIFT` · **Severity** MAJOR(M1) · **Found** 2026-08-24 · **Owning file** `scripts/run_api.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `GET /v1/version` reports `"db_ok":true` and the server log contains no `Psycopg cannot use the 'ProactorEventLoop'`.
- **Close-proof:** pending a commit; measured `db_ok:false` with 5 connection errors before, `db_ok:true` with 0 after, same DSN and same minute.

**Reproduction.**

```
python -c "import uvicorn.loops.asyncio as a,inspect;print(inspect.getsource(a))"
```

Observed on uvicorn 0.40.0:

```
def asyncio_loop_factory(use_subprocess: bool = False):
    if sys.platform == "win32" and not use_subprocess:
        return asyncio.ProactorEventLoop
    return asyncio.SelectorEventLoop
```

**Why it matters.** The `run-api` recipe carried the comment *"psycopg async
refuses uvicorn's default proactor loop. `--loop asyncio` is not optional
there."* The first clause is true; the second does not follow. On Windows that
flag **selects the proactor loop** — the one psycopg refuses. The observable
result was a server that started cleanly, answered `/v1/version` with `200`, and
reported `db_ok=false` while the log filled with `Psycopg cannot use the
'ProactorEventLoop'`. Every request needing canonical memory failed.

The cluster was never the problem, and that is measured rather than assumed: run
under a selector loop the same DSN connects as `pv_kernel_writer` to
`provenance`, CockroachDB v26.2.5, and counts **18,035** rows in
`evidence_items` — the corpus size canon records. A comment predicting how
something fails is a claim to be executed; this one had been written and never
run.

Setting `WindowsSelectorEventLoopPolicy` would not have fixed it either: uvicorn
0.40 passes a *loop factory* to `asyncio.run` and never consults the policy.

**Fix (applied).** `scripts/run_api.py` builds the server with `loop="none"` and
calls `asyncio.run(server.serve(), loop_factory=...)` with an explicit
`SelectorEventLoop`. Platform-independent by construction rather than branching
on `win32`. The recipe's comment was rewritten to state what the flag actually
does, and keeps the true half: startup still survives a refused pool and reports
`db_ok=false` rather than crash-looping, so a 200 from `/v1/version` does not
imply a database — read the field.

**Closes when** `/v1/version` reports `db_ok:true` and the log is free of
proactor errors.

---

### `D-08-003` — a capability proof verifies only during the second it was issued

**Phase** 8 · **Lens** `L-INV` · **Severity** BLOCKER(B2) · **Found** 2026-08-24 · **Owning file** `services/control_plane/app/api/adapters/directory.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `services/control_plane/tests/auth/test_capability_proof_is_stable.py`
- **Close-proof:** pending a commit; the five tests failed against the old signature and pass after. The live measurement below is the reproduction.

**Reproduction.**

```
python - <<'PY'
import sys, time; sys.path.insert(0, '.')
from services.control_plane.app.api.adapters.directory import _derived_expiry
from services.control_plane.app.auth.capability_proof import issue_capability_proof, verify_capability_proof
from services.control_plane.app.api.errors import ApiError
KEY=b"k"*32; CAP="a7803e23-b035-43ee-ac68-af87087bc905"
proof = issue_capability_proof("TRIGGER_EVALUATION", CAP, _derived_expiry(), key=KEY)
for i in range(6):
    try: verify_capability_proof("TRIGGER_EVALUATION", CAP, _derived_expiry(), proof, key=KEY); print(i,"VERIFIED")
    except ApiError: print(i,"REFUSED")
    time.sleep(0.4)
PY
```

Observed before the fix, with nothing changing but the clock:

```
0 VERIFIED
1 VERIFIED
2 REFUSED
3 REFUSED
4 REFUSED
5 REFUSED
```

**Why it matters.** `CapabilityRecord.expires_at` for `TRIGGER_EVALUATION` and
`ACTION_INTENT` was `_derived_expiry()`, i.e. `datetime.now(UTC) + TTL`. The
proof's MAC covers `int(expires_at.timestamp())`, and `verify_capability_proof`
is documented to take that value **from the loaded row** — but for these two
kinds the "loaded row" value was recomputed from the wall clock on every
request. The number inside the MAC therefore changed once a second.

These are two of the four capability kinds and they gate both of the demo's
reveals: `TRIGGER_EVALUATION` is how a fired obligation reaches the Kernel —
prospective memory, one of the four capabilities `00_PRODUCT.md` §2.2 claims
ordinary RAG structurally cannot do — and `ACTION_INTENT` is how an approved
draft reaches an executor. `AGENT_RUN` was unaffected: it uses a stored column,
exactly as the docstring intends.

The failure being **intermittent** is what makes it a BLOCKER rather than a
MAJOR. A retry sometimes works, so it presents as a flaky network rather than a
broken credential, and the natural response is to retry rather than to look.

**Fix (applied).** The lifetime is still *derived* — the original comment is
right that a months-away obligation deadline is far too long for a credential —
but from a **stored** anchor rather than from `now`: `updated_at` for triggers
and intents, `COALESCE(rotated_at, created_at)` for ingest aliases.
`TRIGGER_CAPABILITY_SQL` now projects `updated_at`; `INTENT_CAPABILITY_SQL`
already did and was not using it. A missing anchor falls back to the epoch, not
to `now`, so an unbounded row fails closed and visibly rather than
intermittently.

This is strictly stronger than what was intended, not merely a repair: the proof
now **rotates whenever the row changes**, so a capability id observed in a trace
— and ids do appear in traces, which is the reason the proof exists — stops
working the moment the trigger is evaluated or the intent approved.

**Closes when** a proof issued once verifies repeatedly against an unchanged
row, and stops verifying once the row changes.

---

### `D-08-004` — a refusal that raised `KeyError` instead of naming its subsystem

**Phase** 8 · **Lens** `L-VAC` · **Severity** MAJOR(M2) · **Found** 2026-08-24 · **Owning file** `services/control_plane/app/api/adapters/unbound.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `services/control_plane/tests/api/test_unbound_register_agrees_with_call_sites.py`
- **Close-proof:** pending a commit; the cross-check failed naming `internal.submit_proposal` and passes after the entry was added.

**Reproduction.**

```
python -c "
import sys; sys.path.insert(0,'.')
from services.control_plane.app.api.adapters.unbound import UNBOUND
print('internal.submit_proposal' in UNBOUND)"
```

Observed before the fix: `False`, while `internal.py:252` called
`unbound('internal.submit_proposal')`.

**Why it matters.** `unbound()` looks the key up to build its message, so the
call raised `KeyError: 'internal.submit_proposal'` rather than the typed
`NotImplementedError` naming the subsystem it waits on. That inverts both
reasons `unbound.py`'s docstring gives for the register existing — "an empty
list is a lie" and "a register can be counted". The caller gets an opaque error
naming nothing, and `len(UNBOUND)` under-reports the unbound surface, because
the one method missing from the count is the one whose absence is invisible.

**Fix (applied).** The entry was added, naming the real blocker: §9.7 needs an
app-side `memory_proposals` INSERT under write rule W4 before
`memory_kernel.transaction.commit_proposal` can be called, and no such INSERT
exists in `services/`. A cross-check test now asserts register and call sites
agree **in both directions**, so a new `unbound(...)` without an entry, or an
entry left behind after a method is bound, both fail.

**Closes when** the cross-check passes and `internal.submit_proposal` is either
bound or registered.

---

### `D-12-003` — the web contract declared four shapes the API does not send

**Phase** 12 · **Lens** `L-DRIFT` · **Severity** BLOCKER(B3) · **Found** 2026-08-24 · **Owning file** `apps/web/src/lib/api/contract.ts` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `python -m tools.route_sweep` (exit 0) and `apps/web/src/lib/api/__tests__/contract-conformance.test.ts`
- **Close-proof:** pending a commit; the sweep reported 9 broken routes before and 0 after, over 50 routes discovered from the API.

**Reproduction.**

```
make run-api          # terminal 1
make run-web          # terminal 2
python -m tools.route_sweep --warm
```

Observed before the fix: nine routes broken, including **every case docket**,
rendering `Application error: a server-side exception has occurred`.

**Why it matters.** Four declarations disagreed with the server:

| field | declared | actually sent |
|---|---|---|
| `CaseCommitment.committed_amount` | `Decimal` (a string) | `{currency, amount}` |
| `ArtifactResponse.parser_metadata` | `Record<string, unknown>` | `null` |
| `CaseResponse.context` | present | `null` for cases outside a context |
| `ProofCommitment.outstanding_amount` | `Money` | `null` on a non-monetary commitment |

**TypeScript could not catch any of them**, and the reason is the interesting
part: the compiler was satisfied *precisely because* the types were wrong — the
pages passed a `Decimal` where a `Decimal` was declared. A type only checks code
against a claim, and every one of these claims was false. `ProofCommitment` and
`CaseCommitment` declared the same three fields differently, so the repository
held two answers to one question and the wrong one sat on the busier path.

The **fixtures encoded the same misreading**, which is why 65 component tests
passed while every live route died: fixture and type were written from one
reading of the spec, so they agreed with each other and only the server
disagreed.

The last row is the one that matters most for the product. `USD 0.00
outstanding` says an obligation is **discharged**; `null` says it was never
denominated in money. Those are opposite answers to "does this counterparty
still owe you something", which is the question the whole record exists to
answer — so the repair is a `MoneyValue` component that renders the distinction,
not a `?? 0` fallback.

**Fix (applied).** A nullability audit walked 45 live endpoints and compared
every field against the declaration; 13 declarations were widened. A captured
real response is now a test fixture (`__tests__/captured/case-detail.json`) so
the conformance test can disagree with the contract — a hand-written fixture
cannot, being another statement of the same belief. `tools/route_sweep.py` walks
every route with ids read from the API and is wired as `make route-sweep`.

**Closes when** `python -m tools.route_sweep` exits 0 over all discovered routes.

---

### `D-12-004` — LIVE mode was unreachable, so the app could only ever show fixtures

**Phase** 12 · **Lens** `L-RENDER` · **Severity** MAJOR(M1) · **Found** 2026-08-24 · **Owning file** `apps/web/src/lib/api/client.ts` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `/dashboard` renders without the `DESIGN FIXTURE DATA` banner and shows `USD 2,020.00` computed by the API.
- **Close-proof:** pending a commit; measured banner-present before and absent after, against the same running control plane.

**Reproduction.**

```
curl -s http://localhost:3000/dashboard | grep -c "DESIGN FIXTURE DATA"
```

Observed with `PV_API_BASE_URL` set and no token: `1`, and every screen an error
state.

**Why it matters.** Every read in `reads.ts` takes an optional bearer token,
`session.ts` passed none, and the control plane answers `401 UNAUTHENTICATED` to
an anonymous read — correctly. So setting `PV_API_BASE_URL` did not switch the
app to live data, it switched it to a wall of errors, and the only mode that
rendered anything was FIXTURE. The banner was telling the truth; there was no
path to making it false.

Compounding it, `PV_PLATFORM=local` — the mode `.env.example` calls the
reviewer's mode — had a working HS256 verifier and **no way to obtain a token**:
no login route, no console, no issuer to redirect to.

**Fix (applied).** `apiToken()` reads `PV_API_TOKEN` from the server
environment, deliberately without the `NEXT_PUBLIC_` prefix so Next.js cannot
inline it into the browser bundle; every read runs in a server component, so it
never leaves the server. `scripts/mint_local_token.py` is the one command-line
entry to the existing `issue_local_token`, so the claim vocabulary still has
exactly one definition.

**Closes when** `/dashboard` renders live data with no fixture banner.

---

### `D-07-001` — the production response schema cannot be sent, and 252 green tests said otherwise

**Phase** 7 · **Lens** `L-VAC` · **Severity** BLOCKER(B4) · **Found** 2026-08-24 · **Owning file** `agents/runtime/model_router/wire_schema.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `agents/runtime/tests/test_production_schemas_are_wire_sendable.py`
- **Close-proof:** pending a commit; the raw schema is proven to carry forbidden keywords and the converted one proven not to, so the pair is a differential rather than a single-sided claim.

**Reproduction.**

```
python -c "
from provenance_contracts.ingestion import ExtractionResult
from google.genai import types
types.Schema(**ExtractionResult.model_json_schema())"
```

Observed: seventeen validation errors, raised before any request leaves the
process. `google.genai.types.Schema` is `extra='forbid'` and rejects the
`ge`/`le` that `Confidence` emits and the `prefixItems` that `bbox` emits.

**Why it matters.** `ExtractionResult` is the response schema the ingestion
graph uses on **every** extraction, and it could not be handed to the client at
all. The first live extraction failed instantly.

The reason nobody knew is the finding. The model-router suite has 252 tests and
is green, and `ExtractionResult` appears in it **zero times** — every test sends
a `ToyOutput` defined in the test file. The suite proved the router can send *a*
Pydantic model; the one model production sends was never tried. This is vacuity
in its purest form: not an assertion that checks nothing, but an entire suite
checking a stand-in for the thing under test. A reader counting 252 green tests
concludes the transport works.

A second, sharper case surfaced during bisection against the live endpoint:
`{"type":"array","items":{"type":"string"},"maxItems":60}` is accepted, and the
same bound over an *object* item returns `400 INVALID_ARGUMENT` naming no field.

**Fix (applied).** `to_wire_schema` derives the wire document *from* the
contract — no second definition, per §3.3 — inlining `$ref`s and stripping the
forbidden keywords. `test_production_schemas_are_wire_sendable.py` enumerates
the schemas this build actually sends and asserts both halves: that the raw
schema is unsendable (so the conversion is load-bearing) and that the converted
one carries no forbidden keyword and still describes every field.

**Closes when** every schema in `PRODUCTION_RESPONSE_SCHEMAS` converts cleanly
and a live extraction returns a parsed object.

---

### `D-07-002` — a graph name the database has never accepted

**Phase** 7 · **Lens** `L-DRIFT` · **Severity** MAJOR(M1) · **Found** 2026-08-24 · **Owning file** `agents/runtime/state.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `agents/runtime/tests/test_graph_names_match_the_schema.py`
- **Close-proof:** pending a commit; the agreement test named both constants before the fix and passes after.

**Reproduction.**

```
python -c "
from agents.runtime.state import GRAPH_NAME_INGESTION, GRAPH_NAME_ADVOCATE
print(GRAPH_NAME_INGESTION, GRAPH_NAME_ADVOCATE)"
grep -A 2 'ck_agent_runs_graph' db/migrations/versions/0008_events_infrastructure.py
```

Observed before the fix: `ingestion_graph advocate_graph` against a CHECK
admitting `'ingestion' | 'advocate' | 'resolver' | 'counterfactual'`.

**Why it matters.** The constants were defined, exported, type-checked and used
as dataclass defaults, and every one of those steps is satisfied by a string the
database rejects. Nothing noticed because nothing had ever written an
`agent_runs` row — so the first INSERT would have failed on a CHECK at the
**end** of a live model call, after the tokens were spent.

**Fix (applied).** Both constants corrected to the admitted values. The test
parses the CHECK out of the migration rather than restating the list, because
restating it would create the second registry that caused the problem.

**Closes when** every `GRAPH_NAME_*` constant is a value the CHECK admits.

---

### `D-07-003` — extraction validation demands offsets no model can produce

**Phase** 7 · **Lens** `L-INV` · **Severity** MAJOR(M2) · **Found** 2026-08-24 · **Owning file** `agents/runtime/schemas/validation.py` · **Status** OPEN

- **Verifying assertion:** *(none yet — the repair belongs in the extraction node or the prompt, not in a runner)*

**Reproduction.**

```
python scripts/run_ingestion_graph.py --artifact harborview-deposit-promise
```

Observed on the first live run: `SCHEMA_REPAIR_EXHAUSTED / SPAN_TEXT_MISMATCH`
on every candidate, twice.

**Why it matters.** `validate_extraction` requires character-exact span offsets.
A language model cannot count characters reliably, and no amount of schema
repair fixes arithmetic — the single repair attempt §7 of `14_PROMPTS.md`
budgets is spent re-failing. The defence the check exists for is real: a
quotation the model invented must not become evidence. But offset equality is
the wrong instrument for it.

`scripts/run_ingestion_graph.py` currently anchors `exact_text` inside the block
it cites, which preserves the defence — a quotation that is not a substring
still fails — and drops the counting. **That belongs in
`extract_structured_evidence` or in the prompt, not in a runner**, and is
recorded here rather than moved, because `agents/runtime/nodes/` is another
lane's file.

**Closes when** the anchoring lives in the extraction path and a live run
produces admitted evidence without exhausting repair.

---

### `D-07-004` — the ingestion loop raises, having documented that it never does

**Phase** 7 · **Lens** `L-INV` · **Severity** MAJOR(M2) · **Found** 2026-08-24 · **Owning file** `agents/runtime/graphs/ingestion_graph.py` · **Status** OPEN

- **Verifying assertion:** *(none yet)*

**Reproduction.**

```
python scripts/run_ingestion_graph.py --artifact northline-final-invoice
```

**Why it matters.** `build_memory_proposal` raises past the graph boundary
whenever evidence registration returns a partial map: a commitment whose source
claim was filtered out reaches `MemoryProposal` and fails its cross-reference
validator. `run_ingestion`'s own contract says "the loop never raises", and a
caller written against that contract has no handler.

The cross-reference validator is right to refuse — a commitment citing a claim
that was not admitted is exactly the ungrounded state the Kernel exists to
prevent. What is wrong is the *shape* of the refusal: it escapes as an exception
instead of becoming a typed outcome the loop can record.

**Closes when** a partial registration produces a recorded failure rather than a
raised exception.

---

### `D-02-005` — a row silently replaced by its own column names

**Phase** 2 · **Lens** `L-INV` · **Severity** MAJOR(M1) · **Found** 2026-08-24 · **Owning file** `packages/python/provenance_db/src/provenance_db/repositories/_execute.py` · **Status** AWAITING_REVERIFY

- **Verifying assertion:** `packages/python/provenance_db/tests/unit/test_fetch_all_row_shapes.py`
- **Close-proof:** pending a commit; the six assertions fail against the old expression and pass after.

**Reproduction.**

```
python -c "
columns = ['id', 'distance', 'text']
print(dict(zip(columns, (7, 0.42, 'hello'), strict=True)))
print(dict(zip(columns, {'id': 7, 'distance': 0.42, 'text': 'hello'}, strict=True)))"
```

Observed:

```
{'id': 7,    'distance': 0.42,       'text': 'hello'}
{'id': 'id', 'distance': 'distance', 'text': 'text'}
```

**Why it matters.** `_fetch_all` built every result row with
`dict(zip(columns, record, strict=True))`. That is correct for the tuple rows
psycopg returns by default. A caller that opens its connection with
`row_factory=dict_row` hands back a **mapping**; iterating a mapping yields its
*keys*; and the lengths match, so `strict=True` never fires. Every value is
replaced by the name of its own column, with no exception, no warning, and a
correct-looking shape.

The symptom is not a crash. A retrieval ranking would sort by the string
`"distance"` for every candidate — a perfectly stable total order carrying no
information — so the failure surfaces as *plausible, wrong results*. On this
product that means a State Proof citing the wrong evidence while looking
entirely well-formed.

`strict=True` is the part worth dwelling on. It was added to catch exactly this
class — a row that does not line up with its description — and it cannot,
because the failure preserves length. A guard that appears to cover a case and
does not is worse than no guard, because it stops anyone looking again.

**Fix (applied).** `_rows_as_mappings` handles both row factories. A mapping is
accepted rather than refused — it is a legitimate factory — but its keys must
still agree with `cursor.description`; a row missing a declared column raises,
because returning it would trade one quiet wrong answer for another. The
length check on the tuple path is unchanged.

Found while building the eval harness, which hit it in a spike.

**Closes when** a mapping row and a tuple row produce the same result, and no
value in a returned row equals its own key.
