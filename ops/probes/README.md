# Phase 0 capability probes — how to run them and what the answers mean

**Status:** nothing is built. No code, no migration, no cloud resource, no test run and no integration
exists yet. This directory contains one script whose only job is to ask the live CockroachDB cluster
and the live AWS account six questions that no vendor document can answer for *your* cluster and *your*
account, and to write the answers down in the exact files gate `G-0` reads.

**Authority.** The six probes are specified in full in `docs/ops/41_RUNBOOK.md` §3. The eleven SQL
probes `P1`–`P11` they consume are specified in `docs/specs/10_DATABASE_DDL.md` §1. Every fallback the
script prints is already frozen in `docs/CANONICAL_DECISIONS.md` §"Phase 0 verification decisions".
**A probe is not an invitation to redesign the product. It selects between paths that are already
written.** If a probe fails, take the frozen fallback; do not invent a new one.

| File | Purpose |
|---|---|
| `ops/probes/phase0-probe.ps1` | The runnable probe. Windows / PowerShell 5.1+. |
| `ops/probes/README.md` | This file. |
| `ops/probes/PROBE_LEDGER.md` | Written by the script. Pre-filled ledger you paste back. |

---

## 1. What you need before you run it

| Requirement | Check | If missing |
|---|---|---|
| PowerShell 5.1 or 7.x | `$PSVersionTable.PSVersion` | Ships with Windows 11. |
| `cockroach` CLI **v25.3.x** | `cockroach version` | Download the Windows zip from `binaries.cockroachdb.com`, extract, add to `PATH`. The script prints the exact one-liner if it cannot find it. |
| AWS CLI **v2.x** | `aws --version` | v1 lacks `--cli-binary-format`, which the Titan embedding probe depends on. |
| Python **3.12.x** with the `anthropic` SDK | `python -m pip install --upgrade anthropic boto3` | Only PB-5 step 2 needs it. Its absence is reported as `BLOCKED`, not as a Bedrock failure — those are different facts. |
| AWS credentials for `us-east-1` | `aws sts get-caller-identity` | `aws configure sso`, then `$env:AWS_REGION = 'us-east-1'`. |
| Bedrock model access **requested and granted** | Bedrock console, us-east-1 | Grants are not instantaneous. Request `us.anthropic.claude-haiku-4-5-20251001-v1:0`, `us.anthropic.claude-opus-4-6-v1`, `us.anthropic.claude-opus-5` and `amazon.titan-embed-text-v2:0` before you run anything. The Anthropic chat ids are **inference-profile** ids (`us.` prefix); the bare `anthropic.claude-*` form is not invocable in any form on this account (`CANONICAL_DECISIONS.md` → *Bedrock model id canon*). The embedding id is bare, and that is correct. |
| The CockroachDB CA certificate | see below | `sslmode=verify-full` cannot succeed without it. |

### 1.1 The CA certificate (Windows)

`sslmode=verify-full` requires the cluster CA at `%APPDATA%\postgresql\root.crt`. If it is not there,
the script tells you and keeps going so the AWS probe still reports. Fetch it with exactly this:

```powershell
New-Item -ItemType Directory -Force -Path "$env:APPDATA\postgresql" | Out-Null
Invoke-WebRequest -Uri "https://cockroachlabs.cloud/clusters/<cluster-id>/cert" -OutFile "$env:APPDATA\postgresql\root.crt"
```

That cluster is plan BASIC, AWS `us-east-1`,
host `<cluster-host>.cockroachlabs.cloud:26257`.

The script appends `sslmode=verify-full`, `sslrootcert=<that path>`, `connect_timeout=15` and
`application_name=provenance-phase0-probe` to every connection string it derives, so you do not have to.

### 1.2 The connection string — the one thing you must set yourself

**The script never hardcodes your SQL password, never echoes it, and never writes it to any file it
produces.** It reads the connection string from an environment variable *you* set in *your* shell,
extracts the password once into an in-memory redaction table, and scrubs it out of every line before
that line reaches a transcript or your console.

**What the script cannot protect you from is how you set that variable.** PSReadLine writes every
console line to `%APPDATA%\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt`, and its
sensitive-line filter matches only the words `password`, `secret`, `token`, `apikey` and
`asplaintext` — **none of which appear in a `postgresql://user:pw@host` literal**. Typing the URL
with the password inline therefore persists your production database credential to a plaintext file
on disk, permanently, outside the repository and outside every scrubber in this directory. Prompt for
it instead:

```powershell
$pw = Read-Host 'CockroachDB SQL password' -AsSecureString
$env:PV_PROBE_DB_URL = 'postgresql://<sql-user>:' +
    [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw)) +
    '@<cluster-host>.cockroachlabs.cloud:26257/defaultdb?sslmode=verify-full'
```

If you have already typed a URL with an inline password at a prompt, the credential is in that
history file now. Delete the line, or rotate the password — rotation is the only complete answer.

Notes on that value:

- Point it at **`defaultdb`**, which is what the CockroachDB console hands you. The application
  database is **`provenance`** and does **not** exist yet — the script creates `provenance` and
  `provenance_ci` with the bootstrap user before any probe that needs them, then re-points every
  derived URL at `provenance`.
- The bootstrap SQL user is the cluster's own console account (`<sql-user>`). The four SQL roles (`pv_migrator`, `pv_app_reader_writer`,
  `pv_kernel_writer`, `pv_agent_reader`, plus the optional `pv_ops_reader`) do not exist until the
  migrations run, so PB-1 is answered for the bootstrap user. If you have already created
  `pv_migrator`, also set `$env:PV_PROBE_MIGRATOR_URL` and the script re-asks PB-1 as that role —
  a bootstrap user holding `MODIFYCLUSTERSETTING` proves nothing about `pv_migrator`.
- The URL **must** carry a password. `cockroach sql` prompts interactively when it does not, and a
  prompt in the middle of a six-probe run looks exactly like a hang.
- Clear it when you are done: `Remove-Item Env:\PV_PROBE_DB_URL`.

**Never** put the URL in `.env`, in a `.ps1`, in a commit, or in a chat message.

---

## 2. Running it

```powershell
cd D:\Repo\neverreset
.\ops\probes\phase0-probe.ps1
```

The run does **not** stop on the first failure. Each probe has a frozen fallback, so you need all six
results in one pass. Every probe prints an unmistakable `RESULT <label>: PASS` or `RESULT <label>: FAIL`
line and, on failure, the exact fallback to take.

Useful switches:

| Switch | Effect |
|---|---|
| `-SkipAws` | Run the SQL probes only. Use this when re-running step 1 of the PB-1 ladder. |
| `-SkipSql` | Run PB-5 only. Use this while you are waiting for a Bedrock access grant. |
| `-KeepProbeObjects` | Do not drop the `_pv_*` probe objects. Only for debugging a failed probe; drop them before migration 0001. |
| `-RestoreBudgetSeconds 90` | PB-6's budget. 90 is the number in the runbook; changing it changes the answer, so leave it alone unless you are recording why. |
| `-ProbeRowCount 500` | Rows inserted for the PB-2 `EXPLAIN` proof. |
| `-RepoRoot <path>` | Write the transcripts somewhere else. Use it for a rehearsal run so you do not overwrite real gate evidence. |

Exit codes: `0` all six passed · `1` at least one failed or did not run · `2` **PB-4 failed**, the one
phase-stopping probe. A preflight that cannot start leaves every probe at `not run`, which is exit
`1` and is already visible in the summary block; there is deliberately no separate code for it.

---

## 3. What each probe asks, and what the answer commits you to

### PB-1 — Can vector indexing be turned on? *(consumes P1, P2)*

**The likeliest probe to fail.** Cluster-setting privileges are restricted on managed BASIC clusters.

`SET CLUSTER SETTING feature.vector_index.enabled = true;` then `SHOW CLUSTER SETTING …`.

There are **two different passing facts** and the script records which one you got:

1. The setting exists and now reads `true`.
2. The setting does not appear in `SHOW CLUSTER SETTINGS` at all **and PB-2 succeeds anyway** — on
   builds where vector indexing is unconditional there is no gate to open.

Because of (2), the script prints a PB-1 *interim* state next to `-- P2` and its **final** verdict
after PB-2 has run. Read the final one.

> #### PB-1 fallback ladder — take these three steps in this order
>
> Do **not** escalate privileges and do **not** migrate to a paid tier as a first move.
>
> **Step 1.** Re-run PB-2 on its own: `.\ops\probes\phase0-probe.ps1 -SkipAws`.
> A successful `CREATE VECTOR INDEX` makes PB-1 moot.
>
> **Step 2.** If PB-2 also fails, open a CockroachDB Cloud support request to enable the feature on
> cluster `<cluster-id>` and record the ticket id in `ops/cluster-probe.txt`.
> Continue Phase 1 and Phase 2 meanwhile — nothing before Phase 6 needs the index.
>
> **Step 3.** If it cannot be enabled, take the frozen fallback: *"L2-normalized vector index; if no
> vector index works, disclose brute-force user-partition scan and fail the vector-index
> gate."* Set `PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION`, which scans within
> `user_id = $1` over roughly 16,035 hero-partition rows. It is survivable for a demo and it is
> **not** vector indexing. It must be disclosed in Judge Mode, and `G6.2`
> plus release item `S5` tool 1 stay **blocked**.

### PB-2 — Which index variant works, and is it actually *chosen*? *(consumes P3–P8 + an `EXPLAIN` proof)*

The script tries the three variants **in order** and keeps the **first** that succeeds:

| Variant | Statement | Spec |
|---|---|---|
| **A** | `CREATE VECTOR INDEX … (k, v vector_cosine_ops)` | `10_DATABASE_DDL.md` §5.1 |
| **B** | `CREATE INDEX … USING cspann (k, v vector_cosine_ops)` | §5.2 |
| **C** | `CREATE VECTOR INDEX … (k, v)` — default L2 opclass | §5.3 |

Then it inserts 500 distinct probe vectors and runs `EXPLAIN (VERBOSE)`. **A full scan while the index
exists is a FAILURE even when the results are correct** (`G6.2`). It means the prefix or the opclass
does not match the query shape.

Two details the script gets right that a hand-run would not:

- Under **Variant C** the ranked block is ordered with `<->`, not `<=>`. An L2 index can never serve a
  cosine ordering, so ordering by `<=>` against a Variant C index is a guaranteed full scan and would
  misreport PB-2 as a failure.
- If the runbook's `EXPLAIN` form (query vector as a subquery) full-scans, the script retries once with
  a literal query vector. That distinguishes *"the planner cannot use a subquery here"* from *"the index
  is unusable"*. `provenance_db.repositories.evidence.ann_search()` binds `$3 :: VECTOR(1024)` as a bound
  parameter (`10_DATABASE_DDL.md` §5.5), which is the literal-equivalent shape, so a pass on the second
  form is a real pass — and the script says so in the transcript rather than quietly upgrading the result.

| Symptom | Meaning | Action |
|---|---|---|
| A errors, B succeeds | access-method syntax only | `VARIANT: B`, use §5.2. Semantics identical. |
| `<=>` errors at P5, or A and B both reject `vector_cosine_ops` | cosine opclass unavailable | `VARIANT: C`, use §5.3. Set `EMBEDDING_NORMALIZATION=L2_UNIT` and request Titan v2 with `"normalize": true`. On unit vectors `l2(a,b)² = 2 − 2·cos(a,b)`, so L2 ordering **is** cosine ordering and ranking is unchanged. |
| All three error, or `EXPLAIN` shows a full scan | no usable vector index | PB-1 step 3: `PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION`, disclosed, vector-index gate blocked. |
| P8 fails | multi-column prefix rejected | Skip optional index variant R (`evidence_embedding_ann_active_idx`). Retraction filtering uses over-fetch-then-filter, the default path anyway: `k_raw = greatest(40, 4 * k_final)`, `k_final = 20`. |

The `user_id` prefix is **mandatory**. It is the mechanism by which ANN physically cannot return
another user's evidence — not a performance hint.

### PB-3 — Generated `STORED` column? *(consumes P9, P10, P11)*

P10 is PB-3's real question: can `evidence_items.is_retrieval_eligible` be
`BOOL NOT NULL AS (retraction_status = 'ACTIVE') STORED`?

On failure, the frozen fallback is a plain flag plus a consistency check, written only by the Kernel:

```sql
is_retrieval_eligible BOOL NOT NULL DEFAULT true,
CONSTRAINT ck_evidence_retrieval_flag_consistent CHECK (
    is_retrieval_eligible = (retraction_status = 'ACTIVE')
)
```

P9 (row-level TTL for `idempotency_records`) and P11 (column families beside a `VECTOR` column) ride
along in the same block because their fallbacks are cheap and their failure is silent. P9 failing means
drop the `WITH (…)` clause and add a scheduled `DELETE … WHERE expires_at < now()` worker. P11 failing
means delete the three `FAMILY` lines from `evidence_items` — storage optimisation only.

### PB-4 — Does a view read while the base table is denied? **Phase-stopping.**

The whole agent-safety claim rests on this SQL boundary: agents emit typed `MemoryProposal`s, the
deterministic Memory Kernel is the only canonical writer, and agents hold **no** SQL write credentials.

Phase 0 runs a two-object miniature, because the five agent-safe views — `agent_case_context_v1`,
`agent_active_beliefs_v1`, `agent_belief_lineage_v1`, `agent_evidence_retrieval_v1`,
`agent_open_obligations_v1` — do not exist until migration 0008. The script creates `_pv_grant_base`, a
view `_pv_grant_v1` over it, and a throwaway login role `_pv_probe_reader` whose password it generates
in memory and never prints. Then, as that role, it runs three statements:

```
count
1
ERROR: user _pv_probe_reader has no SELECT privilege on relation _pv_grant_base
ERROR: user _pv_probe_reader has no INSERT privilege on relation _pv_grant_base
```

The view read must succeed **and** both base-table statements must be refused. One without the other
is a failure. The role, view and table are dropped at the end.

| Symptom | Meaning |
|---|---|
| The view read errors naming `_pv_grant_base` | Views do **not** execute with owner privileges here. The agent-safe view design does not hold as written. |
| The base-table `SELECT` succeeds | The role has reach it must not have. Inspect `SHOW GRANTS FOR _pv_probe_reader` and every `ALTER DEFAULT PRIVILEGES` in effect. |

**Fallback (this one stops a phase):** *"Stop Phase 11; do not weaken grants. Use a controlled read API
until the database boundary is proven."* Set `PV_MCP_ENABLED=false`, route the Interpreter's context read
through the control-plane retrieval endpoint (`G11.7` requires it to stay functional regardless), and
record that the Managed MCP Server claim is blocked. **Do not** grant `pv_agent_reader` base-table
`SELECT` to make MCP work. That trades the product's central safety claim for a demo feature, and a judge
reading `information_schema.role_table_grants` will find it.

### PB-5 — Bedrock access to all three canonical model ids

1. `aws bedrock list-foundation-models` for the three ids. Listing proves a model exists in the region;
   it does **not** prove access.
2. One **real** Tier E call (`us.anthropic.claude-haiku-4-5-20251001-v1:0`), one **real** Tier R call on
   the model in force (`us.anthropic.claude-opus-4-6-v1`), and one **expected-to-fail** call on the Tier R
   target (`us.anthropic.claude-opus-5`, denied to this account — `D-00-004`; its failure does not fail the
   probe, it dates the grant) through `AnthropicBedrockMantle`, the Messages-API Bedrock endpoint pinned
   by `14_PROMPTS.md` §9.1. Not the legacy `AnthropicBedrock` client, which targets `bedrock-runtime`
   `InvokeModel` with a different request shape. **No `temperature`, no `top_p`, no `top_k`** — all three
   return HTTP 400 on these models.
3. One **real** embedding call to `amazon.titan-embed-text-v2:0` with `dimensions: 1024` and
   `normalize: true`, asserting `dims == 1024` and an L2 norm within 0.01 of 1.0. The norm assertion is
   not cosmetic: it is exactly the property Variant C's L2 fallback depends on.

| Output | Meaning | Action |
|---|---|---|
| `AccessDeniedException: You don't have access to the model…` | access not granted, or granted in another region | Request access in the Bedrock console for **us-east-1**; confirm `AWS_REGION`. |
| `ValidationException: The provided model identifier is invalid` | wrong identifier form | Use the canonical ids verbatim. `G7.4` fails any output naming Sonnet 4.6, Gemma 4, GLM 5 or Kimi K2.5 — stale identifiers from superseded drafts. |
| `ThrottlingException` on the first call | account-level quota at zero | `41_RUNBOOK.md` §7.5. This is **not** access denied and must not be reported as such. |
| `dims` is 512 or 256 | `dimensions` omitted or mis-set | 1024 is frozen for the life of the index. |

**Fallback:** *"Fixture mode for development only; live submission remains blocked until model access
works."* Set `PV_AGENT_MODE=FIXTURE`. The real Kernel, the real database and the real event path still
execute; only model outputs are replayed. A non-dismissible banner appears (`G12.7`) and
`GET /v1/version` reports `fixture_mode: true` and `agent_mode: "FIXTURE"`, which invalidates the
recorded submission (`S3`). Fixture mode is a development unblocker and an emergency demo fallback,
never a submission state.

### PB-6 — Clone and restore, 90-second budget

`BACKUP DATABASE provenance INTO 'userfile://…/pv-template'`, then
`RESTORE … WITH new_db_name = 'provenance_scn_01'`, then check the rows and the vector index survived.

**Read this before you believe the timing.** At Phase 0 the `provenance` database is empty apart from
one small synthetic table the script creates for the purpose. This run proves the **mechanism** —
`BACKUP`/`RESTORE` permitted on a managed BASIC cluster, `new_db_name` accepted, vector index survives
the round trip — and produces a **lower bound** on the timing. It does **not** prove the 90-second
budget at corpus scale. Re-run PB-6 after `python -m scripts.seed --profile all` has loaded 18,035
evidence rows (16,035 of them in the hero user's partition) and record that second measurement before
relying on per-scenario cloning. The script says the same thing at the top of `ops/restore-probe.txt`.

Also note: `IMPORT INTO` is unsupported on vector-indexed tables and large batch inserts degrade badly,
so seeding must bulk-load `evidence_items` **first** and create the vector index **after**. PB-6 does
not test that ordering; Phase 5 does.

**Fallback:** *"Sequential scenarios with transaction rollback; isolate live-model writes explicitly."*
Plus `20_TDD_STRATEGY.md` R6: the commit lane uses a `hero-lite` profile with 500 decoys, and the full
18,000-row corpus stays in the nightly retrieval lane. Isolation **always** retains the cross-tenant
honeypot rows — `iso-a` and `iso-b` are never dropped to save time, because they are the only thing that
makes `G6.3(a)` non-vacuous.

---

## 4. What the run writes, and which gate reads it

| File | Contents | Gate |
|---|---|---|
| `ops/cluster-probe.txt` | `P1`–`P11` verbatim, one `-- P` header each, plus PB-1, PB-2 and PB-3 verdicts and fallbacks | `G0.6`: `grep -c "^-- P" ops/cluster-probe.txt` must return **11** |
| `ops/decisions/VECTOR_INDEX_VARIANT.md` | the selected variant and the probe output that selected it | `G0.6`: `grep -E "^VARIANT: (A\|B\|C)$"` must return exactly one line |
| `ops/grant-probe.txt` | PB-4, the view-read / base-deny miniature | reviewed at `G-0`, re-asserted at `G11.1`–`G11.2` |
| `ops/bedrock-probe.txt` | PB-5, the three model identifiers and the two live calls | reviewed at `G-0`, re-asserted at `G7.4` |
| `ops/restore-probe.txt` | PB-6, clone and restore timing | reviewed at `G-0`, feeds `20_TDD_STRATEGY.md` R6 |
| `ops/probes/PROBE_LEDGER.md` | the §3.7 ledger, pre-filled from this run | a gate reviewer reads this table first |

The cleanup step is headed **`-- CLEANUP`**, not `-- P12`, even though `10_DATABASE_DDL.md` §1 numbers
it `P12`. `G0.6` counts lines beginning `-- P` and expects exactly **11**; the eleven `-- P` headers are
the eleven capability probes, and cleanup is not a capability. The script self-checks this count at the
end of every run and tells you if it is not 11.

The other `G-0` exit assertions — `G0.1` licence, `G0.2` repository visibility, `G0.3` gitleaks,
`G0.4` clean-clone bootstrap, `G0.5` cluster liveness, `G0.7` settings object — are **not** covered by
this script. `G0.5`'s `SELECT version();` half is answered by `-- P1`; the rest is not.

---

## 5. What to paste back

Paste these five things, in this order:

1. The **console summary block** — the six `PB1..PB6` verdict lines plus the `G0.6` self-check.
2. `ops/probes/PROBE_LEDGER.md` in full.
3. `ops/decisions/VECTOR_INDEX_VARIANT.md` in full.
4. The `EXPLAIN` block from `ops/cluster-probe.txt` under `-- INDEX-CHOSEN PROOF`. This is the one
   result whose exact node labels vary by CockroachDB build, so the raw text matters more than the
   script's verdict on it.
5. Any block the script printed in red.

Before you commit any of it:

```powershell
gitleaks detect --source . --redact --no-banner --exit-code 1
```

If that ever fails after a push, **rotate the credential first** and treat the history rewrite as
secondary (`23_PHASE_GATES.md` §6, rollback position).

### The ledger, blank, if you would rather fill it in by hand

Matches `41_RUNBOOK.md` §3.7 exactly.

| Probe | Question | Result | Variant / fallback taken | Transcript |
|---|---|---|---|---|
| PB-1 | Vector indexing enabled on Basic | | | `ops/cluster-probe.txt` |
| PB-2 | Prefix cosine vector index created and used | | `VARIANT: _` | `ops/cluster-probe.txt`, `ops/decisions/VECTOR_INDEX_VARIANT.md` |
| PB-3 | Generated `STORED` column | | | `ops/cluster-probe.txt` |
| PB-4 | View read, base-table deny | | | `ops/grant-probe.txt` |
| PB-5 | Bedrock access, 3 model ids | | | `ops/bedrock-probe.txt` |
| PB-6 | Clone and restore | | | `ops/restore-probe.txt` |

---

## 6. Secret handling, stated precisely

- The password is read **once**, from `$env:PV_PROBE_DB_URL`, into an in-memory redaction table. It is
  never written to disk and never printed.
- Every byte that reaches a transcript or the console passes through a scrubber that replaces (a) the
  known password literal and its percent-encoded and percent-decoded forms, (b) any
  `postgres://user:…@` userinfo, (c) `password=`/`pwd=`/`PASSWORD '…'` forms, (d) `AKIA…`/`ASIA…` access
  key ids, (e) `aws_secret_access_key`, `aws_session_token` and `Authorization: Bearer` values, and
  (f) the AWS account id, both inside an ARN and bare. PB-5 runs `aws sts get-caller-identity`, and
  `ops/` is committed in a repository that `G0.2` and `S1` require to be **public**; `gitleaks` does
  not flag a bare 12-digit account id, so the scrubber is the only thing standing between that
  command and a published account number.
- PB-4's throwaway role gets a 31-character alphanumeric password from
  `System.Security.Cryptography.RandomNumberGenerator`. It is added to the redaction table *before* the
  `CREATE ROLE` statement runs, the statement text is never echoed, and the role is dropped at the end.
- Lines longer than 1200 characters are truncated with an explicit marker, so a stray 1024-dimension
  vector literal cannot bloat a transcript into something nobody reads.

The scrubber is defence in depth, not a licence to be careless. The real control is that the password
only ever exists in your shell's environment and in the child process's environment.

---

## 7. After the probes pass

In order, from `41_RUNBOOK.md`:

1. Record the ledger and commit `ops/` (§3.7).
2. Set `EMBEDDING_NORMALIZATION` in `.env.local` to `L2_UNIT` if and only if PB-2 selected Variant C,
   otherwise `NONE` (§2.5).
3. Write migrations `0001_identity_aggregates` … `0008_events_infrastructure` — eight linear revisions,
   no branches, no autogenerate, `transaction_per_migration = true` (§4.1). 26 tables, 5 `agent_*_v1`
   views, 4 `pv_` roles plus the optional `pv_ops_reader` created in `0008`.
4. Seed in the mandatory order, and remember: bulk-load `evidence_items` **first**, create the vector
   index **after** (§4.2).

---

## Risks and open questions

1. **PB-1 is the probe most likely to fail, and its worst outcome is a release risk, not a build
   risk.** `MODIFYCLUSTERSETTING` is commonly withheld on managed BASIC clusters. Nothing before Phase 6
   needs the index, so a failure does not block Phase 1–5 — but if it reaches step 3 of the ladder, the
   vector-index claim (`S5` tool 1) and `G6.2` are gone, and the demo scans ~16,035 rows per
   query. Budget the support ticket in step 2 as calendar time, not as work.
2. **PB-1 is answered for the bootstrap user, not for `pv_migrator`.** The roles do not exist yet. A
   bootstrap user that can set a cluster setting proves nothing about `pv_migrator`. Set
   `$env:PV_PROBE_MIGRATOR_URL` and re-run once the roles exist, or accept that PB-1 must be re-asserted
   at Phase 2. The script says so in the transcript rather than papering over it.
3. **The `EXPLAIN` pass criterion is heuristic.** The script looks for the index name in the plan and the
   absence of `FULL SCAN`. Vector-index node labels have moved across CockroachDB releases; a future
   build could name the node in a form this heuristic reads wrong in either direction. Paste the raw
   `EXPLAIN` block back and read it yourself. This is why item 4 of §5 exists.
4. **PB-6's Phase 0 timing is a lower bound and nothing more.** It measures a near-empty database. If
   the seeded 18,035-row measurement turns out to exceed 90 seconds, the per-scenario clone strategy
   dies at Phase 5, not at Phase 0, which is later than anyone would like. Consider re-running PB-6
   immediately after the first `--profile all` seed rather than waiting for the isolation work.
5. **PB-4's miniature is not the real thing.** Two objects and one throwaway role answer the same
   *question* as five `agent_*_v1` views and `pv_agent_reader`, but they are not the same *objects*.
   `ALTER DEFAULT PRIVILEGES` set later, or an ownership difference between the probe view and the real
   views, could make Phase 0 pass and `G11.1`/`G11.2` fail. The re-assertion at Phase 11 is mandatory,
   not a formality.
6. **`AnthropicBedrockMantle` availability is an SDK-version assumption.** If the installed `anthropic`
   package does not export it, the script reports `BLOCKED` and tells you to upgrade rather than falling
   back to the legacy `AnthropicBedrock` client — substituting the legacy client would test a different
   wire shape and produce a false PASS. The specific minimum SDK version that exports it is not pinned
   anywhere in the design pack; that is an open item for `requirements-dev.txt`.
7. **Cluster settings are cluster-wide.** `SET CLUSTER SETTING feature.vector_index.enabled = true`
   affects every database on the cluster, including `defaultdb` and `provenance_ci`. On a
   single-purpose cluster that is fine. It would not be on a shared one.
8. **PB-5 spends real money and real quota**, in tokens on Opus 5 and Haiku 4.5 and one Titan embedding.
   The amounts are trivial (`max_tokens=16`), but a `ThrottlingException` on a fresh account is a quota
   fact, not an access fact, and the script is careful to report it as such.
9. **The script creates and drops SQL objects on a live cluster.** `_pv_probe*`, `_pv_grant_*`,
   `_pv_probe_reader`, `_pv_restore_probe`, the database `provenance_scn_01`, and the userfile
   `pv-template`. All are dropped at the end unless `-KeepProbeObjects` is passed, but a run that dies
   mid-probe leaves them behind. Check for them before running migration 0001.
10. **This README asserts nothing about results.** No probe has been run. Every PASS example in §3 is
    quoted from `41_RUNBOOK.md` §3 as the shape to expect, not as an observation.
