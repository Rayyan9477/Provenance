# Gate G-0 — scaffold, licence, settings, cluster verification

> **Verdict: REJECTED.** Rounds 1 and 2 run 2026-08-18. Four of seven assertions
> pass, one fails, two cannot run. Independently, `72_DEFECT_PROTOCOL.md` §4.3
> rejects any report carrying an open BLOCKER or MAJOR against its own phase, and
> this phase carries **0 and 10** after the close-proof sweep — down from 1 and 35,
> but not zero, so the verdict stands.

Commit: `291ef912e7d0341e51eac70d4f8b47f307221faf`   Branch: `main`
Builder: Integrator + 6 delegated agents        Reviewer: **not yet independent — see Q1**
Round opened: 2026-08-17T20:00Z                 Round closed: 2026-08-18T00:10Z
Verdict: **REJECTED**

## Environment of record

- Checkout: reused working tree at `291ef912`. **Not a fresh clone** — G0.4 requires one and could not run; see below.
- Database: `rayyandb` (`4023638b-52be-42bd-9677-d3611c613477`), database `provenance`, **CockroachDB CCL v26.2.5**, BASIC, AWS us-east-1.
- Deployed target: none. Nothing is deployed until Phase 13.
- Toolchain deviations that materially affect this gate: **GNU Make 4.4.1** (ezwinports, installed this round; GnuWin32 3.81 is still first on the system PATH), **gitleaks 8.30.1** (installed this round, floor raised from 8.21.2), `psql 16` standing in for the absent `cockroach` CLI, `ccloud` and `asm-exec` **not installed**.

## Exit assertions

Battery: `make gate-0`. Logs written by `tools/gate.sh` to `ops/gates/logs/<ID>.291ef912.log`.

**The battery aborts at the first failure.** `.SHELLFLAGS` carries `-e`, so the
`make gate-0` run stopped after G0.2 and left five assertions unrun. §3 rule 2
makes a silently omitted assertion a gate failure, so each remaining assertion
was run individually through the same harness and every one produced a log. That
is a defect in the battery, not a workaround: a gate that stops at the first red
cannot produce the complete picture a reviewer needs.

| ID | Result | Log |
|---|---|---|
| G0.1 | **PASS** — exit 0 | `G0.1.291ef912.log` |
| G0.2 | **FAIL** — exit 1. `PRIVATE`, `spdxId` empty | `G0.2.291ef912.log` |
| G0.3 | **PASS** — exit 0, `no leaks found`, 4.14 MB, 5 commits | `G0.3.291ef912.log` |
| G0.3b | **PASS** — exit 0, `no leaks found`, working tree, `ops/` | `G0.3b.291ef912.log` |
| G0.4 | **CANNOT RUN** — no pushed commit to clone | `G0.4-preflight.291ef912.log` |
| G0.5 | **CANNOT RUN** — exit 127, `ccloud` absent. Substance proved separately | `G0.5.291ef912.log`, `G0.5b-alt.291ef912.log` |
| G0.6 | **PASS** — exit 0, `-- P headers: 11`, `VARIANT lines: 1` | `G0.6.291ef912.log` |
| G0.7 | **PASS** — exit 0, non-zero exit **and** names `COCKROACH_DATABASE_URL` | `G0.7.291ef912.log` |

### CANNOT RUN is not FAIL, and this gate is where that distinction was learned

`D-00-005` was filed because a probe script that could not connect reported that
*"none of the three variants was accepted by this cluster"* — a capability
verdict drawn from a run that attempted nothing. It would have forced a working
capability into a fallback. The same discipline applies to the rows above:

- **G0.5 exit 127** is `ccloud: command not found`. It is not evidence that the
  cluster is absent. The **substance** of G0.5 — "the cluster exists and answers;
  `SELECT version()` returns one row beginning `CockroachDB CCL v`" — was proved
  through `psql` and logged as `G0.5b-alt`:
  `CockroachDB CCL v26.2.5 (x86_64-pc-linux-gnu, built 2026/07/28, go1.25.5)`.
  Recorded as a **deviation**, not a pass: the assertion as written names `ccloud`
  and `asm-exec`, and neither ran. A reviewer may accept the substitute or not.
- **G0.4** clones from GitHub and runs `make bootstrap && make lint && make test`.
  The remote is reachable and private, and `git branch -r --contains HEAD` is
  **empty** — the four commits of this round are local only. A clone today would
  fetch the docs-only initial commit and certify a tree that is not this one.
  Running it would produce a green log about the wrong code.

### G0.2 is a deliberate deferral, not a defect

The repository is private by the owner's explicit decision and becomes public at
submission. This assertion cannot pass before then and is not expected to. It is
recorded as **FAIL rather than WAIVED** because the assertion did run, did have a
verdict, and the verdict was negative; carrying it as a waiver would hide a real
precondition of `S1`. It must be re-run at G-15.

## Tests green

```
python -m pytest -q
  456 passed

python -m pytest packages/python/provenance_contracts/tests \
                packages/python/provenance_domain/tests -q
  336 passed          # contracts 243 + domain 93, both measured
```

Required by §6 at this gate — `packages/python/provenance_contracts/tests/test_settings.py`:
all three named tests pass (`rejects_missing_required`, `rejects_unknown_embedding_dimension`,
`never_defaults_a_credential`).

`python -m ruff check .` → `All checks passed!`. `python -m mypy --strict` on
`provenance_domain` and `provenance_contracts` → `Success`.

`make lint` passes in full, and that is newer than it looks: the import-linter
contracts had never actually run on this machine. The console script is present
but not executable from Git Bash (exit 126), and the module form
`python -m importlinter.cli` exits 0 having evaluated **zero** contracts. Both
were measured. The recipe now takes whichever invocation works and greps the
output for `Contracts: N kept`, so a vacuous run fails instead of passing:

```
provenance_domain.kernel is pure KEPT
provenance_domain does not depend on pydantic KEPT
provenance_contracts performs no I/O KEPT
the agent runtime cannot reach the canonical write path KEPT
Contracts: 4 kept, 0 broken.
```

**`provenance_domain` collects exactly 93**, which is the figure
`CANONICAL_DECISIONS.md` fixes for it (230 = 93 + 137 kernel, the latter Phase 4).
9 + 38 + 7 + 18 + 21 = 93. The task plan's `274 passed` for both packages is
**wrong** and was not chased: the measured total is 336. A gate asserting 274
fails on arrival.

## Sabotage probes run

| Symbol sabotaged | Tests expected to fail | Did they? |
|---|---|---|
| `provenance_domain.money.outstanding` | `test_derivations.py` | **Yes — 3 failed, 22 passed, exit 1** |
| `RESOLVED→REOPENED` reason-code guard removed | `test_transitions.py` | Yes — 7 failed |
| `FULFILLED→ACTIVE` added; `outstanding_amount` comparison inverted; ARMED precondition removed | `test_transitions.py` | Yes — 6 failed |

All three implementations were restored byte-for-byte and re-verified green.

Sabotage matrix entry count at this gate: **1**. `tests/sabotage_matrix.yaml` was
created by T1.4 and registers `provenance_domain.money.outstanding`. It stood at 0
when this report was first written because the file did not exist, and an
unregistered hook is invisible to `make sabotage` at `G14.6`.
`72_DEFECT_PROTOCOL.md` §10.2 detector 2 requires `count(N) >= count(N-1)`, so
**1 is the floor every later gate must meet or exceed**; T14.5 reconciles it to 18.

The `G1.7` wiring is now proven rather than demonstrated. `PV_SABOTAGE` rebinds
the symbol on the module object, so a `from`-import in `invariants.py` would copy
the reference first and the sabotage would never arrive — `G1.7` would report a
green sabotage run, which §23 counts as a **failure**. Measured: baseline exit 0;
sabotaged `test_invariants.py` exit 1 (3 failed, 18 passed); sabotaged matrix
selection exit 1 (6 failed, 87 passed). An AST check, not a substring scan,
asserts the import style — the substring version tripped on the docstring that
explains the rule.

## Defect ledger

```
OPEN BLOCKER: 0  OPEN MAJOR: 10  OPEN MINOR: 4  CARRIED: 0  REJECTED: 0   [phase 0]
defect_lint: 0 violations
```

§4.3: a report with an open BLOCKER or MAJOR against its own phase is **REJECTED**,
never `SIGNED WITH CARRIED DEBT`. **This alone still rejects the gate**, independently
of G0.2 and G0.4 — 10 open MAJOR remain.

Round 2 of the close-proof sweep moved this from `1 / 35 / 7` to `0 / 10 / 4`. The
BLOCKER, `D-00-042`, closed on a real counterfactual: re-adding the deleted allowlist
made gitleaks report **zero** findings on a file carrying two live-shaped DSNs and no
redaction marker. 33 counterfactuals were recorded, each run in an isolated `git
worktree` so the live tree was never neutered.

**The `[phase 0]` suffix is new, and it matters.** This line previously counted every
record in the ledger, not this phase's. It read `20 open MAJOR` when 10 belong to
phase 0, 9 were discovered in phase 1, and one is `D-06-001`. §11.3 makes this line the
reviewer's precondition and §4.3 scopes rejection to "its own phase", so unscoped it
answered a different question from the one the rule asks — a phase-0 reviewer would
have read another phase's work as their own blockers. `print_report` had always
filtered; the line beneath it had not.

Most of the 35 are `AWAITING_REVERIFY` — fixed, but without the §7.4 close-proof
counterfactual. A close-proof sweep is in flight. It faces a structural obstacle
worth stating plainly: Phase 0 was authored **before this repository had commit
discipline**, so for most records there is no pre-fix state to revert to and the
mechanical counterfactual cannot run. Those records will stay `AWAITING_REVERIFY`
with the reason recorded, rather than being closed on assertion.

The open BLOCKER is `D-00-042`, filed and fixed **this round**: `allowlist.paths`
is a whole-file skip evaluated before the file is read, so `condition = "AND"`
never runs and eight files — including the transcript the probe scripts write
credentials through the scrubber into — were never scanned at all.

## Standing questions (§22.3) — answered honestly

**Q1 · What did I claim without running?** Nothing in the table above; every row
has a log. But the **role separation itself is unmet**: §1390 requires a
fresh-context reviewer who did not watch the code being written, and this report
is written by the Integrator who dispatched every builder. The six delegated
agents had isolated context; the signer does not. This is the largest single
weakness of this gate and no amount of care inside the report repairs it.

**Q2 · What is mocked that should be real?** `ccloud` and `asm-exec` are absent, so
the secret-resolution path (`{{resolve:secretsmanager:...}}`) has never executed —
credentials are read from a local `.env`. That is the Phase-0-appropriate posture,
but it means the Secrets Manager integration is **entirely unexercised**, and
`G0.5b` as written is the assertion that would have exercised it.

**Q3 · Which invariant is currently unproven?** All five. Phase 0 builds no
canonical write path. Invariant 5 (grounding) has a predicate in `derivations.py`
but no database to enforce it against; `INVARIANTS.md` and
`tools/invariant_map_check.py` are T1.4 and do not exist, so there is not yet a
mapping from invariant to proving test.

**Q4 · What would a hostile judge click on first?** The claim that vector indexing
works. It is the sponsor-facing claim and it rests on `ops/cluster-probe.txt`. That
file is honest and re-derived, but the planner proof it contains is the reason
`D-06-001` exists: a correlated subquery produces a full scan with correct results
and no warning. If Phase 6 ships that shape, every functional test still passes and
the sponsor claim is quietly false.

**Q5 · What passed because of seeded state rather than logic?** Nothing — there is
no seed. This is the one round where that answer is legitimately "nothing", and it
stops being legitimate at G-2.

**Q6 · What did I not look at?** `ops/probes/phase0-probe.ps1` and
`ops/probes/README.md` were committed but not re-read this round; the probes were
re-run through purpose-written scripts in `tmp/` instead, so the committed probe
scripts are **not** what produced the committed transcripts. `D-00-030` already
records that `phase0-probe.sh` does not exist. `support.js` from the design bundle
is unread (Phase 12). The 26-page design PDF was checked against the hero canon but
not read page by page.

**Q7 · If this phase is secretly broken, how and when would I find out?** The most
likely silent breakage is the secret scan drifting back to permissive. It has
already failed that way twice — once as `D-00-010`, once as `D-00-042`, and the
second was introduced by the fix for the first. The detector is now
`tools/tests/test_gitleaks_config.py`, which asserts the **positive** half of every
allowlist. If that file is ever weakened to make a scan pass, nothing downstream
notices until the repository is public, and by then rotation is the only remedy.

> §22.3 note: an answer of "nothing" to Q1–Q6 is itself a finding. Q5 is answered
> "nothing" here and the reason is stated: there is no seeded state to pass on.

## Carried debt

```
$ make debt
0 carried items
```

No MINOR has been accepted as debt, because the gate is REJECTED and only a signing
gate may accept debt (§9.2). The 7 open MINOR records are open, not carried, and
`ops/defects/CARRIED_DEBT.md` correctly does not exist — which the linter reports as
a note, distinguishing *absent* from *empty*.

## Rollback position at time of signing

Not signed. The rollback position is unchanged from the documented Phase 0 baseline:
*nothing exists yet*.

```
git reset --hard c2a0c05        # the docs-only initial commit
```

destroys all five commits of this round. Nothing is deployed, no cloud resource was
created that persists (the PB-4 probe role and the PB-6 clone were both dropped and
verified gone), and the cluster itself predates this work.

**Cannot be undone:** a secret committed and pushed to a public repository. Nothing
has been pushed. The database password in `.env` was transmitted in a chat transcript
and **must be rotated before the first push**, independently of anything in this gate.

## What must happen before G-0 can be re-run for signature

1. Close-proof sweep completes; `OPEN BLOCKER` and `OPEN MAJOR` both reach 0.
2. Push, so G0.4's clean-clone assertion certifies this tree rather than the initial commit.
3. Make the repository public (G0.2) — owner's decision, timed to submission.
4. Install `ccloud` and `asm-exec`, or record an accepted deviation for G0.5 as written.
5. An independent reviewer with fresh context re-runs the battery (Q1).
