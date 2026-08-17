# Provenance — Operational Runbook and Local Development Guide

Purpose: take an engineer from an empty machine to a running Provenance stack, run the Phase 0 capability probes that gate every later phase, and give a symptom-first playbook for every failure this build is known to be able to produce.

Status: planning complete v1.1
Implementation status: not started

Audience: the engineer or coding agent standing up the stack for the first time; the on-call operator during a gate battery or a demo; the reviewer checking that a reported failure was diagnosed rather than guessed at. Read `00_PRODUCT.md`, `CANONICAL_DECISIONS.md`, `specs/10_DATABASE_DDL.md` §1, and `quality/23_PHASE_GATES.md` §6 before acting on anything here.

---

## 0. How to use this document

This runbook is procedural. It owns **how to run things**. It does not own contracts, schema, prompts, or gate criteria, and where it appears to disagree with an owning specification, the owning specification wins under the authority order in `README.md`.

| This document owns | This document defers to |
|---|---|
| Command ordering, ports, local substitution rules, failure playbooks, demo operations | `specs/10_DATABASE_DDL.md` (schema, probes P1–P11, seed content, verification queries V1–V11) |
| The probe transcript format and where results are recorded | `quality/23_PHASE_GATES.md` (what counts as a passing gate) |
| The contingency ladder and disclosure rules | `CANONICAL_DECISIONS.md` (every predetermined fallback) |

Two rules apply to everything below.

1. **Nothing in this repository is built.** Every command here is written to be run once the corresponding phase exists. A command that has never been run is not evidence of anything.
2. **Output is the record.** `quality/23_PHASE_GATES.md` §3 forbids reporting a step complete without pasted output. This runbook is written so that every step produces output worth pasting.

### 0.1 Make targets referenced throughout

`quality/23_PHASE_GATES.md` §6 requires `bootstrap`, `lint`, `test`, `db-migrate`, `db-verify`, `seed`, `seed-perturb`, `sabotage`, and `gate-0` … `gate-15`. This runbook additionally uses the following, which are additive convenience wrappers and carry no gate authority:

```
make probe            # runs the Phase 0 probe suite, writes ops/*.txt transcripts
make run-api          # control plane on :8080
make run-web          # Next.js on :3000
make run-crdb         # local single-node CockroachDB container (CI parity only)
make run-sink         # local mail sink on :1025 / UI :8025
make embeddings-warm  # populates the embedding cache without touching the database
make demo-reset       # destructive: reset to clean demo state (§8.2)
make demo-rehearse    # scripted dress rehearsal (§8.1)
make test-submission  # the quality/20_TDD_STRATEGY.md §14.4 pre-submission lane
```

---

## 1. Prerequisites and versions

Pin these. Version drift in the toolchain is the cheapest possible source of a lost afternoon.

| Tool | Required version | Why this version | Check |
|---|---|---|---|
| Python | **3.12.x** (not 3.13) | `provenance_contracts` targets 3.12 typing; AgentCore Runtime and the LangGraph pin are validated on 3.12. | `python3.12 --version` |
| Node.js | **20.x LTS** | Next.js on Amplify Hosting; 20 is the widest-supported Amplify runtime. | `node --version` |
| npm | 10.x (ships with Node 20) | Lockfile format v3. | `npm --version` |
| Docker | 24.0+ | Runs `cockroachdb/cockroach:latest-v25.3` for the CI-parity local database and the mail sink. | `docker --version` |
| AWS CLI | **v2.x** | v1 lacks `--cli-binary-format`, which the Titan probe in §3.6 depends on. | `aws --version` |
| `ccloud` CLI | latest | Cluster provisioning and SQL shell. Counts as the third qualifying CockroachDB tool (`quality/23_PHASE_GATES.md` §24 S5). | `ccloud version` |
| `cockroach` CLI | v25.3.x | `cockroach sql` is what every gate command in the phase gates uses. | `cockroach version` |
| `jq` | 1.6+ | Every probe and gate assertion parses JSON with it. | `jq --version` |
| `gh` | 2.x | Repository visibility and licence assertions (`G0.2`, `S1`, `S2`). | `gh --version` |
| `gitleaks` | 8.x | `G0.3` and `S8`. | `gitleaks version` |
| `ffprobe` (ffmpeg) | any | `S4` measures the video against the hard 180.0 s limit. | `ffprobe -version` |
| `uuidgen` | any | Idempotency-key generation in gate commands. | `uuidgen` |

Optional but recommended: `uv` (fast resolver; `make bootstrap` uses it when present and falls back to `pip`), `direnv`, and `asm-exec` for the Secrets Manager resolution pattern used by every command that needs a database URL.

### 1.1 Accounts and access you must have before starting

- An AWS account in **us-east-1** with Bedrock model access **requested and granted** for `anthropic.claude-opus-5`, `anthropic.claude-haiku-4-5`, and `amazon.titan-embed-text-v2:0`. Access grants are not instant. Request them on day zero; §3.6 verifies them.
- A CockroachDB Cloud organisation with a Basic cluster quota.
- A GitHub repository, public, Apache-2.0.
- An SES-verified sender address and at least one verified recipient (the safe demo inbox). While the account is in the SES sandbox, both ends must be verified. See §7.8.

---

## 2. Zero to running

Run these in order. Do not reorder; §2.5 depends on §2.4, and §4 depends on §3.

### 2.1 Repository setup

```bash
git clone https://github.com/<org>/provenance.git
cd provenance
git rev-parse HEAD | tee ops/CHECKOUT_SHA.txt
```

Every gate report records this SHA. If you are reviewing rather than building, clone into an empty directory rather than reusing a working tree (`quality/23_PHASE_GATES.md` §22.2 step 1).

### 2.2 Dependency install

```bash
python3.12 -m venv .venv
source .venv/bin/activate                 # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

make bootstrap
```

`make bootstrap` performs, in order:

```bash
pip install -e packages/python/provenance_contracts \
            -e packages/python/provenance_domain \
            -e packages/python/provenance_db \
            -e packages/python/provenance_telemetry
pip install -r requirements-dev.txt        # alembic, pytest, mypy, ruff, hypothesis, anthropic, boto3
npm --prefix apps/web ci
pre-commit install
```

Verify:

```bash
make lint && make test
#   → ruff: no issues
#   → mypy --strict: "Success: no issues found in NN source files"
#   → pytest summary line with a non-zero passed count
```

A failure here before any cloud resource exists is the cheapest failure you will get all week. Do not proceed past it.

### 2.3 AWS credential configuration

```bash
aws configure sso            # or: aws login, if your organisation uses it
export AWS_REGION=us-east-1
export AWS_PROFILE=<your-profile>

aws sts get-caller-identity --query '[Account,Arn]' --output text
#   → <account-id>  arn:aws:sts::<account-id>:assumed-role/<role>/<session>
```

Region is not optional. Every canonical decision in this build assumes `us-east-1`, and a Bedrock call to the wrong region returns `AccessDeniedException` in a way that looks exactly like a missing model grant (§7.5).

**Secrets are never typed into a command and never appear in a log.** The pattern used by every gate battery is:

```bash
asm-exec --env PV_DB_MIGRATOR='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' \
         -- cockroach sql --url "$PV_DB_MIGRATOR" -e "SELECT version();"
```

The resolved value exists only in the child process environment. It does not enter shell history, `ps` output, or a gate log. Five connection strings live in `provenance/db`, keyed `migrator_url`, `app_url`, `kernel_url`, `agent_url`, `ops_reader_url`, one per SQL role. `ops_reader_url` is an operator and CI credential only; no runtime service loads it, and its role is provably read-only (`ops/40_INFRA_IAC.md` §11.5, `G12.8`).

### 2.4 ccloud login and cluster provisioning

```bash
ccloud auth login

# Provision. The transcript is a Phase 0 deliverable and a submission artifact (S5, tool 3).
ccloud cluster create basic provenance-dev \
  --cloud aws --region us-east-1 \
  2>&1 | tee ops/cluster-provision.txt

ccloud cluster list --output json | jq -r '.[] | .name + " " + .state'
#   → "provenance-dev CREATED"
```

`ccloud`'s flag surface changes between releases. Confirm with `ccloud cluster create basic --help` before assuming the invocation above; the transcript is what matters, not the exact flags.

Create the four SQL roles as login users. Passwords are generated by `ccloud` or the Cloud console and written straight to Secrets Manager. They never appear in a migration file (`specs/10_DATABASE_DDL.md` §2).

```bash
for role in pv_migrator pv_app_reader_writer pv_kernel_writer pv_agent_reader; do
  ccloud cluster user create provenance-dev "$role" | tee -a ops/cluster-provision.txt
done
```

Then create the two databases. `provenance_ci` exists so destructive gate work never touches demo data (`quality/23_PHASE_GATES.md` §8 entry criteria):

```bash
asm-exec --env U='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' -- \
  cockroach sql --url "$U" -e "
    CREATE DATABASE IF NOT EXISTS provenance;
    CREATE DATABASE IF NOT EXISTS provenance_ci;
    SHOW DATABASES;"
```

### 2.5 Environment file

Copy `.env.example` to `.env.local`. The settings object (`provenance_contracts/settings.py`) **raises on any missing required value and defaults no credential** (`G0.7`). Non-secret values live in the file; every secret is an ARN that resolves at runtime.

```bash
APP_ENV=local
APP_BASE_URL=http://localhost:8080
AWS_REGION=us-east-1

COGNITO_USER_POOL_ID=us-east-1_XXXXXXXXX
COGNITO_ISSUER=https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX
COGNITO_WEB_CLIENT_ID=<provenance-web client id>
COGNITO_AGENT_CLIENT_ID=<provenance-agent-runtime client id>
COGNITO_AGENT_CLIENT_SECRET_ARN=arn:aws:secretsmanager:us-east-1:<acct>:secret:provenance/cognito/agent
COGNITO_WORKER_CLIENT_ID=<provenance-workers client id>
COGNITO_WORKER_CLIENT_SECRET_ARN=arn:aws:secretsmanager:us-east-1:<acct>:secret:provenance/cognito/worker

COCKROACH_DATABASE_URL=<resolved at runtime from provenance/db; never literal here>

S3_ARTIFACT_BUCKET=provenance-artifacts-use1
SES_INGEST_DOMAIN=ingest.<your-domain>
SES_FROM_ADDRESS=provenance@<your-domain>
EVENTBRIDGE_BUS_NAME=provenance-bus
EVENTBRIDGE_SCHEDULER_GROUP=provenance-triggers
SQS_DLQ_URL=https://sqs.us-east-1.amazonaws.com/<acct>/provenance-dlq

BEDROCK_REASONING_MODEL_ID=anthropic.claude-opus-5
BEDROCK_EXTRACTION_MODEL_ID=anthropic.claude-haiku-4-5
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0
EMBEDDING_DIMENSIONS=1024
EMBEDDING_VERSION=v1
EMBEDDING_NORMALIZATION=NONE            # becomes L2_UNIT only if probe PB-2 selects Variant C

AGENTCORE_RUNTIME_ARN=<arn>
MCP_SERVER_URL=<CockroachDB Cloud Managed MCP Server endpoint>
MCP_AUTH_SECRET_ARN=arn:aws:secretsmanager:us-east-1:<acct>:secret:provenance/mcp
OTEL_SERVICE_NAME=provenance-control-plane

PV_AGENT_MODE=LIVE                      # LIVE | FIXTURE  (§6)
PV_ACTION_EXECUTION_MODE=ENABLED        # ENABLED | DISABLED  (§7.9 kill switch)
PV_MCP_ENABLED=true
PV_ACTION_ALLOWLIST=demo-inbox@<your-domain>
```

Confirm the settings object refuses to start without them:

```bash
env -i PATH="$PATH" python -c "from provenance_contracts.settings import Settings; Settings()"; echo "exit=$?"
#   → pydantic ValidationError naming COCKROACH_DATABASE_URL and others; exit=1
```

---

## 3. Phase 0 capability probes

**Read this section before writing any migration.** These six probes answer questions that no vendor document can settle for your specific cluster and account, and each one has a predetermined fallback already frozen in `CANONICAL_DECISIONS.md` §"Phase 0 verification decisions". A probe is not an invitation to redesign the product. It selects between paths that are already written.

### 3.0 Probe protocol

```bash
make probe
```

produces four committed transcripts:

| File | Contents | Gate that reads it |
|---|---|---|
| `ops/cluster-probe.txt` | DDL §1 probes P1–P11, verbatim, one `-- P` header each | `G0.6` (`grep -c "^-- P"` must return **11**) |
| `ops/grant-probe.txt` | PB-4, the view-read / base-deny miniature | reviewed at `G-0`, re-asserted at `G11.1`–`G11.2` |
| `ops/bedrock-probe.txt` | PB-5, the three model identifiers | reviewed at `G-0`, re-asserted at `G7.4` |
| `ops/restore-probe.txt` | PB-6, clone and restore timing | reviewed at `G-0`, feeds `20_TDD_STRATEGY.md` R6 |

and one decision record, `ops/decisions/VECTOR_INDEX_VARIANT.md`, containing exactly one line matching `^VARIANT: (A|B|C)$`.

**Transcript gotcha, stated because it will otherwise cost you a gate rerun.** `specs/10_DATABASE_DDL.md` §1 numbers the cleanup step `-- P12. Clean up.`, but `G0.6` asserts that `ops/cluster-probe.txt` contains exactly **11** lines beginning `-- P`. Write the cleanup header as `-- CLEANUP` in the transcript. The eleven `-- P` headers are the eleven capability probes; cleanup is not a capability.

Every probe below states: the command, the output that means pass, the output that means fail, and the predetermined fallback.

---

### 3.1 PB-1 — Vector indexing enabled on a CockroachDB Cloud Basic cluster

**Question.** Can `pv_migrator` turn on vector indexing on a Basic cluster? Cluster-setting privileges on managed Basic clusters are restricted, and this is the single probe most likely to fail.

**Command.** Run as `pv_migrator`.

```sql
-- P2. Any vector-related cluster settings, and whether they are enabled.
SELECT variable, value
FROM [SHOW CLUSTER SETTINGS]
WHERE variable ILIKE '%vector%';

SET CLUSTER SETTING feature.vector_index.enabled = true;

SHOW CLUSTER SETTING feature.vector_index.enabled;
```

```bash
asm-exec --env U='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' -- \
  cockroach sql --url "$U" --format=csv \
    -e "SET CLUSTER SETTING feature.vector_index.enabled = true;" \
    -e "SHOW CLUSTER SETTING feature.vector_index.enabled;" \
  2>&1 | tee -a ops/cluster-probe.txt
```

**PASS.**

```
feature.vector_index.enabled
true
```

Also passing: the setting does not appear in `SHOW CLUSTER SETTINGS` at all **and** PB-2 succeeds anyway. On builds where vector indexing is on by default there is no gate to open. Record which of the two you saw; they are different facts.

**FAIL.**

```
ERROR: only users with the MODIFYCLUSTERSETTING or MODIFYSQLCLUSTERSETTING privilege are allowed to set cluster setting 'feature.vector_index.enabled'
```

or

```
ERROR: unknown cluster setting 'feature.vector_index.enabled'
```

with PB-2 also failing.

**Predetermined fallback.** Do not attempt to escalate privileges or migrate to a paid tier as a first move. In order:

1. Re-run PB-2. On several builds the setting is absent because the feature is unconditional. A successful `CREATE VECTOR INDEX` makes PB-1 moot.
2. If PB-2 also fails, open a CockroachDB Cloud support request to enable the feature on the cluster and record the ticket id in `ops/cluster-probe.txt`. Continue Phase 1 and 2 work in the meantime; nothing before Phase 6 needs the index.
3. If it cannot be enabled: `CANONICAL_DECISIONS.md` — *"L2-normalized vector index; if no vector index works, disclose brute-force user-partition scan and fail the sponsor vector-index submission gate."* Set `PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION`, which scans within `user_id = $1` over roughly 16,035 hero rows. It is survivable for a demo and it is **not** vector indexing. It must be disclosed in Judge Mode and in `SUBMISSION.md`, and `G6.2` and submission item `S5` tool 1 stay blocked.

---

### 3.2 PB-2 — Prefix-column cosine vector index, created and queried

**Question.** Which of the three index variants in `specs/10_DATABASE_DDL.md` §5 does this cluster accept, and does a query actually use it? Filter acceleration works **only** through prefix columns, so the `user_id` prefix is a correctness mechanism, not a performance hint.

**Command.** The full P4–P8 block, run in order, keeping the **first** variant that succeeds.

```sql
-- P4. Does the VECTOR type exist with a fixed dimension?
CREATE TABLE IF NOT EXISTS _pv_probe (
    id UUID NOT NULL PRIMARY KEY,
    k  UUID NOT NULL,
    v  VECTOR(1024)
);

-- P5. Which distance operators parse? Run each line separately and note failures.
SELECT '[1,2,3]'::VECTOR(3) <-> '[3,2,1]'::VECTOR(3) AS l2_distance;
SELECT '[1,2,3]'::VECTOR(3) <=> '[3,2,1]'::VECTOR(3) AS cosine_distance;
SELECT '[1,2,3]'::VECTOR(3) <#> '[3,2,1]'::VECTOR(3) AS neg_inner_product;

-- P6. Which index syntax and operator class is accepted? Keep the FIRST that succeeds.
CREATE VECTOR INDEX _pv_probe_a ON _pv_probe (k, v vector_cosine_ops);            -- variant A
CREATE INDEX       _pv_probe_b ON _pv_probe USING cspann (k, v vector_cosine_ops); -- variant B
CREATE VECTOR INDEX _pv_probe_c ON _pv_probe (k, v);                              -- variant C, default L2 opclass

-- P7. Confirm what was actually created, including access method and opclass.
SHOW INDEXES FROM _pv_probe;
SELECT create_statement FROM [SHOW CREATE TABLE _pv_probe];

-- P8. Confirm a multi-column prefix is permitted (needed for optional index variant R).
CREATE TABLE IF NOT EXISTS _pv_probe2 (
    id UUID NOT NULL PRIMARY KEY,
    k  UUID NOT NULL,
    ok BOOL NOT NULL,
    v  VECTOR(1024)
);
CREATE VECTOR INDEX _pv_probe2_a ON _pv_probe2 (k, ok, v vector_cosine_ops);
```

Then prove the index is *chosen*, which is the part that actually matters:

```sql
-- P3. Session-level ANN tuning knob. Default beam size is 32.
SHOW vector_search_beam_size;

INSERT INTO _pv_probe (id, k, v)
SELECT gen_random_uuid(),
       '00000000-0000-4000-8000-000000000001',
       (SELECT ('[' || string_agg(random()::STRING, ',') || ']')::VECTOR(1024)
        FROM generate_series(1, 1024))
FROM generate_series(1, 500);

EXPLAIN (VERBOSE)
SELECT id FROM _pv_probe
WHERE k = '00000000-0000-4000-8000-000000000001'
ORDER BY v <=> (SELECT v FROM _pv_probe LIMIT 1)
LIMIT 40;
```

**PASS.** `SHOW INDEXES FROM _pv_probe` names the index, the indexed columns begin with `k`, and the `EXPLAIN` output contains a vector-index scan node naming `_pv_probe_a` (or `_pv_probe_b`). Record:

```
VARIANT: A
```

in `ops/decisions/VECTOR_INDEX_VARIANT.md`, with the probe output that selected it.

**FAIL, three distinguishable ways.**

| Symptom | Meaning | Action |
|---|---|---|
| Variant A errors, B succeeds | Access-method syntax only | `VARIANT: B`, use `specs/10_DATABASE_DDL.md` §5.2. Semantics identical. |
| `<=>` errors at P5, or A and B both reject `vector_cosine_ops` | Cosine opclass unavailable | `VARIANT: C`, use §5.3. Set `EMBEDDING_NORMALIZATION=L2_UNIT` and request Titan v2 embeddings with `"normalize": true`. On unit vectors, `l2(a,b)^2 = 2 - 2*cos(a,b)`, so L2 ordering **is** cosine ordering and ranking is unchanged. |
| All three error, or `EXPLAIN` shows a full scan | No usable vector index | Fall to PB-1's step 3: `PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION`, disclosed, sponsor gate blocked. |

A `full scan` node in `EXPLAIN` while the index exists is a **failure even if the results are correct** (`G6.2`). It means the prefix or the opclass does not match the query shape. Check that `k = $1` sits inside the ranked block and that no non-prefix predicate was added to it (`specs/10_DATABASE_DDL.md` §5.5).

**If P8 fails.** Skip optional index variant R (`evidence_embedding_ann_active_idx`). Retraction filtering falls back to over-fetch-then-filter, which is the default path anyway: `k_raw = greatest(40, 4 * k_final)`, `k_final = 20`.

**Cleanup.** `DROP TABLE IF EXISTS _pv_probe, _pv_probe2 CASCADE;` under the `-- CLEANUP` header.

---

### 3.3 PB-3 — Generated stored column support

**Question.** Can `evidence_items.is_retrieval_eligible` be a `STORED` computed column, or must it be a plain flag with a consistency check?

**Command.**

```sql
-- P10. Confirm STORED computed columns.
CREATE TABLE IF NOT EXISTS _pv_probe4 (
    id UUID NOT NULL PRIMARY KEY,
    s  STRING NOT NULL,
    b  BOOL NOT NULL AS (s = 'ACTIVE') STORED
);
INSERT INTO _pv_probe4 (id, s) VALUES (gen_random_uuid(), 'ACTIVE'),
                                      (gen_random_uuid(), 'RETRACTED');
SELECT s, b FROM _pv_probe4 ORDER BY s;
```

**PASS.**

```
s,b
ACTIVE,true
RETRACTED,false
```

Use the generated column: `is_retrieval_eligible BOOL NOT NULL AS (retraction_status = 'ACTIVE') STORED`.

**FAIL.** Any error on the `AS (...) STORED` clause, or a `b` column that does not track `s`.

**Predetermined fallback.** `CANONICAL_DECISIONS.md`: *"Plain boolean plus consistency check, written only by the kernel."* Replace the column with:

```sql
is_retrieval_eligible BOOL NOT NULL DEFAULT true,
CONSTRAINT ck_evidence_retrieval_flag_consistent CHECK (
    is_retrieval_eligible = (retraction_status = 'ACTIVE')
)
```

The Kernel's retraction statement (`specs/10_DATABASE_DDL.md` §5.6) then sets both columns in the same `UPDATE`. Nothing else may write either one. Record the choice in `ops/cluster-probe.txt` so the migration author does not have to guess.

**Also run in the same block, because their fallbacks are cheap and their failure is silent:**

```sql
-- P9. Row-level TTL, used by idempotency_records.
CREATE TABLE IF NOT EXISTS _pv_probe3 (
    id UUID NOT NULL PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL
) WITH (ttl_expiration_expression = 'expires_at', ttl_job_cron = '@hourly');
-- FAIL → drop the WITH clause from idempotency_records and add a scheduled
--        DELETE ... WHERE expires_at < now() worker.

-- P11. Column families alongside a VECTOR column.
CREATE TABLE IF NOT EXISTS _pv_probe5 (
    id UUID NOT NULL PRIMARY KEY,
    t  STRING NOT NULL,
    v  VECTOR(1024),
    FAMILY f_meta (id), FAMILY f_text (t), FAMILY f_vec (v)
);
-- FAIL → delete the three FAMILY lines from evidence_items. Storage optimisation only.
```

---

### 3.4 PB-4 — `pv_agent_reader` reads views and is denied base tables

**Question.** On this cluster, does a role granted `SELECT` on a view alone actually read the view (executing with the owner's table privileges) while being refused every base table? This is the SQL boundary the entire agent-safety claim rests on, and `CANONICAL_DECISIONS.md` makes it the one probe whose failure **stops a phase** rather than degrading it.

**Phase 0 runs a miniature.** The five `agent_*_v1` views do not exist until migration 0008, so the Phase 0 probe uses a two-object fixture that answers the same question.

```sql
-- Run as pv_migrator.
CREATE TABLE IF NOT EXISTS _pv_grant_base (
    id UUID NOT NULL PRIMARY KEY,
    secret STRING NOT NULL
);
INSERT INTO _pv_grant_base VALUES (gen_random_uuid(), 'base-table-row');

CREATE VIEW _pv_grant_v1 AS SELECT id FROM _pv_grant_base;

CREATE ROLE IF NOT EXISTS _pv_probe_reader WITH LOGIN PASSWORD '<generated, never committed>';
GRANT CONNECT ON DATABASE provenance TO _pv_probe_reader;
GRANT USAGE ON SCHEMA public TO _pv_probe_reader;
GRANT SELECT ON _pv_grant_v1 TO _pv_probe_reader;
-- Deliberately grant nothing on _pv_grant_base.
```

Then, connected **as `_pv_probe_reader`**:

```bash
asm-exec --env U='{{resolve:secretsmanager:provenance/db:SecretString:probe_reader_url}}' -- \
  cockroach sql --url "$U" \
    -e "SELECT count(*) FROM _pv_grant_v1;" \
    -e "SELECT secret FROM _pv_grant_base LIMIT 1;" \
    -e "INSERT INTO _pv_grant_base (id, secret) VALUES (gen_random_uuid(), 'x');" \
  2>&1 | tee ops/grant-probe.txt
```

**PASS.** Exactly this shape, all three results:

```
count
1
ERROR: user _pv_probe_reader has no SELECT privilege on relation _pv_grant_base
ERROR: user _pv_probe_reader has no INSERT privilege on relation _pv_grant_base
```

The view read succeeds **and** both base-table statements are refused. One without the other is a failure.

**FAIL, two ways, with different meanings.**

| Symptom | Meaning |
|---|---|
| The view read errors with `no SELECT privilege on relation _pv_grant_base` | Views do **not** execute with owner privileges here. The agent-safe view design does not hold as written. |
| The base-table `SELECT` succeeds | The role has reach it must not have. Inspect `SHOW GRANTS FOR _pv_probe_reader` and every `ALTER DEFAULT PRIVILEGES` in effect. |

**Predetermined fallback.** `CANONICAL_DECISIONS.md`: *"Stop Phase 11; do not weaken grants. Use a controlled read API until the database boundary is proven."* Concretely: set `PV_MCP_ENABLED=false`, route the Interpreter's context read through the control-plane retrieval endpoint (which `G11.7` requires to stay functional regardless), and record in `ops/grant-probe.txt` that the Managed MCP Server claim is blocked. **Do not** grant `pv_agent_reader` base-table `SELECT` to make MCP work. That trades the product's central safety claim for a demo feature, and a judge reading `information_schema.role_table_grants` will find it.

**Cleanup.** `DROP VIEW _pv_grant_v1; DROP TABLE _pv_grant_base; DROP ROLE _pv_probe_reader;`

**Re-assert at Phase 11** against the real objects, per `G11.1` and `G11.2`:

```bash
cockroach sql --url "$PV_DB_MIGRATOR" --format=csv -e "
  SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
  WHERE grantee='pv_agent_reader' AND table_name NOT LIKE 'agent\_%\_v1';"
#   → header only; zero data rows
```

---

### 3.5 PB-5 — Bedrock model access

**Question.** Are all three canonical model identifiers invocable from this account, in `us-east-1`, with the credentials the runtime will actually use?

**Step 1, availability.**

```bash
aws bedrock list-foundation-models --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'claude-opus-5')
                        || contains(modelId,'claude-haiku-4-5')
                        || contains(modelId,'titan-embed-text-v2')].[modelId,modelLifecycle.status]" \
  --output table | tee ops/bedrock-probe.txt
```

Listing a model proves it exists in the region. It does **not** prove you have access. Step 2 does.

**Step 2, Tier E and Tier R, one real call each.** Use `AnthropicBedrockMantle`, the Messages-API Bedrock endpoint that `specs/14_PROMPTS.md` §10 pins. Do not use the legacy `AnthropicBedrock` client; it targets `bedrock-runtime` `InvokeModel` with a different request shape. Set no `temperature`, `top_p`, or `top_k`; all three return HTTP 400 on these models.

```bash
python - <<'PY' 2>&1 | tee -a ops/bedrock-probe.txt
from anthropic import AnthropicBedrockMantle
client = AnthropicBedrockMantle(aws_region="us-east-1")
for tier, model_id in (("E", "anthropic.claude-haiku-4-5"),
                       ("R", "anthropic.claude-opus-5")):
    r = client.messages.create(
        model=model_id,
        max_tokens=16,
        messages=[{"role": "user", "content": "Reply with exactly: ok"}],
    )
    print(f"tier={tier} model={model_id} ok text={r.content[0].text.strip()!r} "
          f"in={r.usage.input_tokens} out={r.usage.output_tokens}")
PY
```

**Step 3, embeddings, with the dimension and norm asserted.**

```bash
aws bedrock-runtime invoke-model --region us-east-1 \
  --model-id amazon.titan-embed-text-v2:0 \
  --cli-binary-format raw-in-base64-out \
  --content-type application/json --accept application/json \
  --body '{"inputText":"Provenance embedding probe","dimensions":1024,"normalize":true}' \
  /dev/stdout \
  | jq '{dims: (.embedding | length), l2: ([.embedding[] | . * .] | add | sqrt)}' \
  | tee -a ops/bedrock-probe.txt
```

**PASS.**

```
tier=E model=anthropic.claude-haiku-4-5 ok text='ok' in=14 out=2
tier=R model=anthropic.claude-opus-5 ok text='ok' in=14 out=2
{ "dims": 1024, "l2": 0.9999999 }
```

`dims` must be exactly **1024**. `l2` at approximately 1.0 confirms `"normalize": true` produced unit vectors, which is precisely the property Variant C's L2 fallback depends on (§3.2).

**FAIL.**

| Output | Meaning | Action |
|---|---|---|
| `AccessDeniedException: You don't have access to the model with the specified model ID` | Model access not granted, or granted in another region | Request access in the Bedrock console for **us-east-1**; confirm `AWS_REGION`. Grants are not instantaneous. |
| `ValidationException: The provided model identifier is invalid` | Wrong identifier form | Use the canonical ids verbatim. `G7.4` explicitly fails any output naming Sonnet 4.6, Gemma 4, GLM 5, or Kimi K2.5; those are stale identifiers from superseded drafts. |
| `ThrottlingException` on the first call | Account-level quota at zero | See §7.5. This is not the same as access denied and must not be reported as such. |
| `dims` is 512 or 256 | The request omitted or mis-set `dimensions` | Titan v2 supports several output sizes. 1024 is frozen for the life of the index. |

**Predetermined fallback.** `CANONICAL_DECISIONS.md`: *"Fixture mode for development only; live submission remains blocked until model access works."* Set `PV_AGENT_MODE=FIXTURE`. The real Kernel, the real database, and the real event path still execute; only model outputs are replayed. A non-dismissible banner appears (`G12.7`) and `GET /v1/version` reports `fixture_mode: true` and `agent_mode: "FIXTURE"`, which invalidates the recorded submission (`S3`). Fixture mode is a development unblocker and an emergency demo fallback, never a submission state.

---

### 3.6 PB-6 — Seed clone and restore path

**Question.** Can a seeded template database be cloned quickly enough to give each test scenario a fresh database, and does a clone survive the vector index?

Reseeding from scratch costs roughly six minutes and, without a warm embedding cache, real Bedrock spend. If cloning works, `--profile all` runs once and every scenario forks from it.

**Command.**

```bash
{
  echo "== template backup =="
  time asm-exec --env U='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' -- \
    cockroach sql --url "$U" -e "
      BACKUP DATABASE provenance
      INTO 'userfile://defaultdb.public.userfiles_\$user/pv-template';"

  echo "== restore as a new logical database =="
  time asm-exec --env U='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' -- \
    cockroach sql --url "$U" -e "
      RESTORE DATABASE provenance
      FROM LATEST IN 'userfile://defaultdb.public.userfiles_\$user/pv-template'
      WITH new_db_name = 'provenance_scn_01';"

  echo "== the clone kept its index and its rows =="
  asm-exec --env U='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' -- \
    cockroach sql --url "$U" --database=provenance_scn_01 --format=csv -e "
      SELECT count(*) AS evidence_rows FROM evidence_items;
      SHOW INDEXES FROM evidence_items;"
} 2>&1 | tee ops/restore-probe.txt
```

**PASS.** Both statements succeed, the restore completes in **under 90 seconds**, `evidence_rows` equals the template's count (18,035 after `--profile all`), and `SHOW INDEXES FROM evidence_items` still lists `evidence_embedding_ann_idx` with `user_id` first.

**FAIL, three distinguishable ways.**

| Symptom | Meaning | Action |
|---|---|---|
| `ERROR: BACKUP ... not supported` / permission denied on `userfile` | Managed Basic restricts the operation | Fall back below. |
| Restore succeeds but `SHOW INDEXES` omits the vector index | The index did not survive the restore | Restore, then `CREATE VECTOR INDEX` from the recorded variant, then re-time. If that stays under budget, the clone path is still viable. |
| Restore exceeds 90 s | Too slow for per-scenario isolation | Fall back below. |

**Predetermined fallback.** `CANONICAL_DECISIONS.md`: *"Sequential scenarios with transaction rollback; isolate live-model writes explicitly."* Plus `20_TDD_STRATEGY.md` R6: the commit lane uses a `hero-lite` profile with **500** decoys, and the full 18,000-row corpus stays in the nightly retrieval lane. Isolation always retains the cross-tenant honeypot rows — `iso-a` and `iso-b` are never dropped to save time, because they are the only thing that makes `G6.3(a)` non-vacuous.

Cheapest working fallback if `BACKUP`/`RESTORE` is unavailable at all:

```bash
cockroach sql --url "$PV_DB_MIGRATOR" -e "CREATE DATABASE provenance_scn_01;"
PV_TARGET_DB=provenance_scn_01 alembic upgrade head
PV_TARGET_DB=provenance_scn_01 python -m scripts.seed --profile hero    # ~20 s, no decoys
```

### 3.7 Probe result ledger

Fill this in and commit it with the transcripts. A gate reviewer reads this table first.

| Probe | Question | Result | Variant / fallback taken | Transcript |
|---|---|---|---|---|
| PB-1 | Vector indexing enabled on Basic | | | `ops/cluster-probe.txt` |
| PB-2 | Prefix cosine vector index created and used | | `VARIANT: _` | `ops/cluster-probe.txt`, `ops/decisions/VECTOR_INDEX_VARIANT.md` |
| PB-3 | Generated `STORED` column | | | `ops/cluster-probe.txt` |
| PB-4 | View read, base-table deny | | | `ops/grant-probe.txt` |
| PB-5 | Bedrock access, 3 model ids | | | `ops/bedrock-probe.txt` |
| PB-6 | Clone and restore | | | `ops/restore-probe.txt` |

---

## 4. Migrations and seeding

### 4.1 Migrations

Eight linear revisions, `0001_identity_aggregates` through `0008_events_infrastructure` (`specs/10_DATABASE_DDL.md` §16). No branches, no autogenerate, `transaction_per_migration = true`.

```bash
asm-exec --env COCKROACH_DATABASE_URL='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' -- \
  alembic upgrade head
#   → final line "Running upgrade 0007_action_plane -> 0008_events_infrastructure"

make db-verify
#   → "V1 0  V2 0  V3 0  V4 0  V5 0  V6 0  V7 0  V8 0  V9 0  V10 0  V11 0"
```

`V11 0` is correct on an empty database and becomes `V11 3` only after seeding. `V11 < 3` after a seed is a failure: it means the retraction fixtures were deleted rather than retracted, and retraction filtering is untested (`G2.5`).

Prove the chain is reversible before you depend on it:

```bash
alembic downgrade base && alembic upgrade head && alembic downgrade base && alembic upgrade head
#   → exit 0 each time
```

**Downgrade is for local iteration only.** From Phase 13 onward, schema rolls forward and code rolls back (`quality/23_PHASE_GATES.md` §5, forward-only rule).

**Never mix DDL and DML in a revision.** CockroachDB rejects schema changes that follow data writes in the same transaction. The seed is a separate program.

### 4.2 The mandatory seed order

This ordering is not a preference. Two external constraints force it:

- **`IMPORT INTO` is unsupported on a table carrying a vector index.** If you intend to bulk-load with `IMPORT INTO`, the index cannot exist at that moment.
- **Large batch inserts into a vector-indexed table degrade badly.** Every insert must also do partition maintenance on the ANN structure, so an 18,000-row load with the index live is dramatically slower than the same load followed by one index build.

Therefore: **bulk-load `evidence_items` FIRST, create the vector index AFTER.**

Migration `0002_evidence_plane` creates the index as part of the schema, so the seed must drop and rebuild it. The end state is byte-identical to the migration's; only the procedure differs.

```bash
make seed        # python -m scripts.seed --profile all --reset
```

`scripts/seed/__main__.py` executes exactly this sequence:

```text
 1. Guard:   refuse unless APP_ENV in {local, demo}.  --reset never runs elsewhere.
 2. Truncate in reverse FK order (--reset only).
 3. Load the small planes as pv_migrator:
      tenants(3) -> users(3) -> counterparties(5) -> relationships(6)
      -> contexts(1) -> cases(10)
 4. DROP INDEX IF EXISTS evidence_embedding_ann_idx CASCADE;      <-- MANDATORY, as pv_migrator
 5. Resolve embeddings for all 18,035 texts (§4.3). Cache-first; Bedrock only on a miss.
 6. Bulk-load source_artifacts, then evidence_items, as pv_app_reader_writer:
      16,000 hero decoys + 1,000 iso-a + 1,000 iso-b + 32 curated + 3 retraction = 18,035
      Multi-row INSERT, 500 rows per statement, inside explicit transactions.
 7. CREATE VECTOR INDEX evidence_embedding_ann_idx ...            <-- the variant from PB-2
 8. Wait for the schema-change job to finish (schema changes are asynchronous).
 9. Replay the curated MemoryProposal fixtures through MemoryKernel.commit() as
      pv_kernel_writer: claims, beliefs, belief_versions, belief_support,
      commitments(4), fulfillments(2), prospective_triggers(2), state_transitions.
10. Apply the 3 retraction fixtures with the §5.6 Kernel UPDATE. Embeddings untouched.
11. Run every verification query (§18) and exit non-zero on any violation.
```

Steps 4 and 7, verbatim:

```sql
-- Step 4, before any evidence bulk load.
DROP INDEX IF EXISTS evidence_embedding_ann_idx CASCADE;

-- Step 7, after the last evidence row is committed. Variant A shown; substitute
-- the variant recorded in ops/decisions/VECTOR_INDEX_VARIANT.md.
CREATE VECTOR INDEX evidence_embedding_ann_idx
    ON evidence_items (user_id, embedding vector_cosine_ops);
```

Step 8, because a `CREATE INDEX` that has returned is not a `CREATE INDEX` that has finished:

```sql
SHOW JOBS WHEN COMPLETE (
    SELECT job_id FROM [SHOW JOBS]
    WHERE description ILIKE '%evidence_embedding_ann_idx%'
      AND status NOT IN ('succeeded', 'failed', 'canceled')
);
```

Then confirm it came back, every time, because a seed that silently leaves the index dropped produces a demo that works and a `G6.2` that fails:

```bash
cockroach sql --url "$PV_DB_APP" -e "SHOW INDEXES FROM evidence_items;" | grep evidence_embedding_ann_idx
#   → at least one row; the indexed columns begin with user_id
```

**Optional faster loader.** With the index dropped, `IMPORT INTO` from CSV in S3 is materially faster than 36 batched inserts:

```sql
IMPORT INTO evidence_items (id, tenant_id, user_id, artifact_id, evidence_type,
                            normalized_text, valid_from, valid_to, observed_at,
                            source_authority, extraction_confidence,
                            retraction_status, embedding_version, embedding)
CSV DATA ('s3://provenance-artifacts-use1/seed/evidence-v1.csv?AUTH=implicit')
WITH skip = '1', nullif = '';
```

This is valid **only** between steps 4 and 7. Running it while `evidence_embedding_ann_idx` exists fails outright. The 500-row batched `INSERT` path is the default because it needs no S3 staging and no `IMPORT` privilege; `IMPORT INTO` is an optimisation to reach for only if seed time becomes a bottleneck.

Expected wall time for the full profile: three to six minutes for the inserts, plus embedding resolution (§4.3), plus one to two minutes for the index build.

### 4.3 Embedding generation, batching, and cost

`scripts/seed/embeddings.py` is cache-first and resumable. The cache key is `(normalized_text_sha256, embedding_version)` — the same key the runtime cache uses, so a warm seed cache also warms the first demo run.

```python
# scripts/seed/embeddings.py -- shape, not final code.
import hashlib, json, pathlib, concurrent.futures as cf
import boto3

MODEL_ID          = "amazon.titan-embed-text-v2:0"
DIMENSIONS        = 1024
EMBEDDING_VERSION = "v1"
NORMALIZE         = True          # keep True; required if PB-2 selected Variant C
CACHE_DIR         = pathlib.Path("scripts/seed/.embedding-cache")
MAX_INFLIGHT      = 8             # 16 reliably throttles a fresh account

_rt = boto3.client("bedrock-runtime", region_name="us-east-1")


def _key(text: str) -> str:
    return hashlib.sha256(f"{EMBEDDING_VERSION}\x00{text}".encode()).hexdigest()


def _cached(text: str) -> bytes | None:
    p = CACHE_DIR / f"{_key(text)}.f32"
    return p.read_bytes() if p.exists() else None


def _invoke(text: str) -> bytes:
    """One Titan call. Retries ThrottlingException with exponential backoff and
    jitter; every other ClientError is raised so the seed fails loudly."""
    body = json.dumps({"inputText": text,
                       "dimensions": DIMENSIONS,
                       "normalize": NORMALIZE})
    resp = _rt.invoke_model(modelId=MODEL_ID, body=body,
                            contentType="application/json",
                            accept="application/json")
    vec = json.loads(resp["body"].read())["embedding"]
    assert len(vec) == DIMENSIONS, f"expected {DIMENSIONS} dims, got {len(vec)}"
    return _pack_f32(vec)


def resolve(texts: list[str]) -> dict[str, bytes]:
    """Cache-first, bounded-concurrency resolution. Writes each result to the
    cache as it lands, so an interrupted run resumes rather than restarts."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out, misses = {}, []
    for t in texts:
        hit = _cached(t)
        out[t] = hit if hit else None
        if hit is None:
            misses.append(t)

    with cf.ThreadPoolExecutor(max_workers=MAX_INFLIGHT) as pool:
        for text, packed in zip(misses, pool.map(_invoke, misses)):
            (CACHE_DIR / f"{_key(text)}.f32").write_bytes(packed)
            out[text] = packed
    return out
```

Rules the implementation must honour:

- **Never regenerate a cached vector.** That is what `idx_evidence_text_hash` and the cache key exist for.
- **`dimensions` is 1024 and `embedding_version` is `v1`, frozen for the life of the index.** Changing either requires a parallel index and a backfill, never an in-place mix (`specs/13_RETRIEVAL_SPEC.md` §"embedding contract").
- **`normalize: true` always.** It costs nothing under Variants A and B and is mandatory under Variant C.
- **Concurrency is bounded at 8.** Higher rates produce `ThrottlingException` on accounts with default Bedrock quotas, and a throttled seed is a partially-embedded corpus, which is worse than a slow one.

**Cost and time estimate for the roughly 18,000 decoy rows.**

| Quantity | Value | Basis |
|---|---|---|
| Rows requiring an embedding | 18,035 | 18,000 decoys + 32 curated + 3 retraction fixtures |
| Mean tokens per `normalized_text` | ~40 | Evidence items are single atomic observations, not documents |
| Total input tokens | ~721,400 | 18,035 × 40 |
| Unit price assumed | USD 0.02 per 1M input tokens | Amazon Titan Text Embeddings V2 list price at time of writing |
| **Estimated cost, cold** | **≈ USD 0.015** | 0.7214M × 0.02 |
| Cost at 10× the token estimate | ≈ USD 0.15 | Sensitivity check; still negligible |
| Serial wall time | ~42 min | 18,035 × ~140 ms p95 |
| **Wall time at concurrency 8** | **≈ 5–7 min** | The real constraint |
| Cache size on disk | ≈ 71 MiB | 18,035 × 1024 × 4 bytes |

**Verify the price against the current Bedrock pricing page before quoting it to anyone.** The number is small enough that the honest operational conclusion holds regardless: *money is not the constraint; wall-clock time and throttling are.* Populate the cache at the first seed, not later (`quality/23_PHASE_GATES.md` §25 risk 6).

```bash
make embeddings-warm     # resolves every seed text into the cache, touches no database
```

Confirm uniformity afterwards — one embedding version, no drift (`G6.1`):

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT embedding_version, count(*) FROM evidence_items
  WHERE embedding IS NOT NULL GROUP BY 1;"
#   → exactly one row: "v1,18035"
```

Two rows here means two embedding spaces are being ranked against each other, which is silently wrong. See §7.4.

### 4.4 Post-seed verification

```bash
make db-verify
#   → "V1 0  V2 0  V3 0  V4 0  V5 0  V6 0  V7 0  V8 0  V9 0  V10 0  V11 3"

python -m tools.manifest_check db/seeds/MANIFEST.json
#   → "26 tables checked, 26 match"

make seed && make seed        # idempotence: two runs, identical counts
```

---

## 5. Running each component locally

### 5.1 Ports and URLs

| Component | Command | URL / endpoint | Notes |
|---|---|---|---|
| Control plane (FastAPI) | `make run-api` | `http://localhost:8080` | `PV_API`. Health at `/healthz`; OpenAPI at `/openapi.json`. |
| Web (Next.js) | `make run-web` | `http://localhost:3000` | `PV_WEB`. Reads `NEXT_PUBLIC_API_BASE_URL=http://localhost:8080`. |
| Local CockroachDB (CI parity) | `make run-crdb` | SQL `localhost:26257`, console `http://localhost:8081` | **Console is mapped to 8081**, because CockroachDB's HTTP default is 8080 and the control plane owns that port. |
| Agent runtime harness | `make run-agents` | `http://localhost:8090` | Local stand-in for AgentCore Runtime. Same graph code, same typed I/O. |
| Outbox dispatcher | `python -m workers.outbox_dispatch.local --interval 2` | no port | Replaces the EventBridge-driven Lambda locally. |
| Trigger evaluator | `python -m workers.trigger_wakeup.local --trigger-id <uuid>` | no port | Manual wake. Same entry point the scheduler calls. |
| Mail sink | `make run-sink` | SMTP `localhost:1025`, UI `http://localhost:8025` | The safe demo inbox locally. Never a real mailbox. |
| OTEL collector | `docker compose up otel` | `localhost:4317` | Optional. Without it, spans log to stdout. |

Underlying commands:

```bash
# Control plane
uvicorn services.control_plane.app.main:app --host 0.0.0.0 --port 8080 --reload

# Web
npm --prefix apps/web run dev            # binds :3000

# Local CockroachDB, single node, CI parity with the commit lane
docker run -d --name pv-crdb \
  -p 26257:26257 -p 8081:8080 \
  cockroachdb/cockroach:latest-v25.3 start-single-node --insecure
# → postgresql://root@localhost:26257/provenance?sslmode=disable

# Mail sink
docker run -d --name pv-sink -p 1025:1025 -p 8025:8025 axllent/mailpit
```

### 5.2 Bring-up order

```bash
# 1. Database reachable
cockroach sql --url "$PV_DB_APP" -e "SELECT 1;"

# 2. Schema and data present
make db-verify

# 3. Control plane
make run-api &
curl -sS localhost:8080/v1/version | jq '{git_sha, fixture_mode, agent_mode, schema_revision, db_ok}'
#   → {"build_sha":"<sha>","fixture_mode":false,"schema_revision":"0008","db_ok":true}

# 4. Workers
python -m workers.outbox_dispatch.local --interval 2 &

# 5. Web
make run-web &
open http://localhost:3000
```

### 5.3 A local smoke that proves the vertical slice

```bash
export PV_TOKEN=$(python -m tools.dev_token --user hero)

curl -sS "$PV_API/v1/cases" -H "Authorization: Bearer $PV_TOKEN" \
  | jq '[.items[] | {title, status, attention_level}]'
#   → 10 cases; the ISP cancellation case RESOLVED with attention_level "NONE"

curl -sS -X POST "$PV_API/v1/artifacts" \
  -H "Authorization: Bearer $PV_TOKEN" -H "Idempotency-Key: $(uuidgen)" \
  -F file=@demo_data/the_move/E3_isp_invoice.eml | jq '{artifact_id, trace_id, status}'
#   → status "QUEUED"

# Drain the local workers, then read canonical state back on a fresh connection.
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT status, revision, reopened_count, attention_level
  FROM cases WHERE id = '<hero-case-id>';"
#   → REOPENED,13,1,URGENT
```

The final read runs from a separate shell after the request finished, over a connection that was never inside the Kernel transaction. Reading back inside the transaction is not evidence of a commit (`quality/23_PHASE_GATES.md` §23.2).

---

## 6. Local mode semantics

Local mode substitutes **infrastructure**. It never substitutes **semantics**. The list below is exhaustive in both directions; anything not named in the permitted column is not permitted.

### 6.1 What may be substituted

| Substitute | Enabled by | What stays real | Closes at |
|---|---|---|---|
| **Recorded model fixtures** replace Bedrock calls | `PV_AGENT_MODE=FIXTURE` | The Kernel, the database, the transaction, the outbox, the trace. Every graph runs end to end from stored outputs. `agent_runs.model_route.mode = "FIXTURE_REPLAY"`. | Phase 7 for development; must be `LIVE` at `G-15`. |
| **Direct worker invocation** replaces EventBridge and Scheduler | run `workers.*.local` in-process | The outbox row, the dedupe on `processed_events`, the predicate re-evaluation against current state. | Phase 10 for the deployed path. The scheduler's own timing is never exercised locally, and that gap is a standing Q2 answer. |
| **Local artifact storage** replaces S3 | `PV_ARTIFACT_STORE=file:///…` or MinIO | `content_sha256` identity, the immutable-bytes contract, the `source_artifacts` row. | Before the demo. S3 integration must be exercised at least once (`06_CODING_AGENT_HANDOFF.md` §18). |
| **Local mail sink** replaces SES | `SES_TRANSPORT=smtp://localhost:1025` | Recipient allowlist, idempotency key, `action_executions` rows with `attempt_no`, provider correlation id. | Phase 9; the demo send targets a controlled mailbox in every case. |
| **Compressed clock** in trigger tests | `--frozen-clock=<iso8601>` | Predicate evaluation against real canonical state. | Never closes; it is a test facility and `G10.6` requires identical results at two frozen instants. |
| **Single-node CockroachDB in Docker** for the commit lane | `make run-crdb` | `SERIALIZABLE`, `40001`, the retry wrapper, the full schema. | Nightly and pre-submission lanes run against the real Cloud cluster, where index behaviour, `EXPLAIN` shape, and grants are the ones that ship. |

### 6.2 What may never be substituted

These are not preferences. Each corresponds to an invariant or to a canonical decision.

1. **The Memory Kernel.** It is never mocked, stubbed, faked, or bypassed in any correctness test. `PV_FORBID_MOCKS=1` makes the end-to-end conftest raise on any `unittest.mock` import (`G14.7`), and `grep -rn "MagicMock\|FakeKernel\|StubDB" tests/e2e` must return nothing. A demo whose commit came from anything but the Kernel is a fabricated demo.
2. **The real CockroachDB.** Never SQLite, never an in-memory shim, never an ORM-level fake. The invariants live in `CHECK` constraints, composite foreign keys, and `SERIALIZABLE` isolation. A test database without them tests nothing that matters.
3. **The real transaction path.** One serializable transaction per accepted proposal, written in the statement order of `specs/10_DATABASE_DDL.md` §13, with bounded retry on SQLSTATE `40001` and **no model call or network call inside the callback** (`G3.5` enforces this with an AST lint over every transaction callback).
4. **Canonical write authority.** Only `pv_kernel_writer`, only from `services/control_plane/app/memory_kernel/`. `python -m tools.write_path_lint` must report canonical write statements in exactly one module and zero in `agents/`, `workers/`, `apps/web/`, and `packages/`.
5. **Trace and State Proof backing.** Both are assembled from persisted rows by SQL. No scripted animation, no hard-coded identifiers, no model in the State Proof path (`G5.1` constructs an exploding Bedrock client and requires the suite to pass anyway).
6. **The approval binding.** `basis_case_revision` and `approval_draft_sha256` are revalidated by the executor immediately before the send. Never skip it locally "because it is only the sink".

### 6.3 The fixture-mode contract

Fixture mode is legitimate and disclosed, or it is fraud. There is no third state.

- A permanent, non-dismissible banner: `DEMO FIXTURE MODE — model outputs are replayed`.
- `fixture_mode: true` in `GET /v1/version` and in every trace payload.
- `agent_runs.model_route.mode = "FIXTURE_REPLAY"`, and the trace node `agent.interpreter.run` summary begins `FIXTURE REPLAY`.
- The Kernel, the database, and the event path still execute for real.
- `S3` requires `fixture_mode == false` in the recorded submission. A `true` there invalidates it.

---

## 7. Failure playbooks

Each playbook is **symptom → diagnosis → fix**, plus what not to do. Start from the symptom you actually observed, not from the cause you suspect.

### 7.1 SQLSTATE 40001 retry storm

**Symptom.** `kernel_decisions.retry_count` at 3–5 across many decisions; commit latency spikes; occasional `503 RETRYABLE_CONCURRENCY` from the API; the CloudWatch `kernel-retry-rate` alarm in ALARM.

**Diagnose.**

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT retry_count, count(*) FROM kernel_decisions
  WHERE created_at > now() - INTERVAL '15 minutes'
  GROUP BY 1 ORDER BY 1;"

cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT case_id, count(*) AS commits
  FROM kernel_decisions WHERE created_at > now() - INTERVAL '15 minutes'
  GROUP BY 1 ORDER BY 2 DESC LIMIT 5;"
```

Then read the three causes apart:

| Reading | Cause |
|---|---|
| Retries concentrated on one `case_id` | Genuine contention on one aggregate. Expected under the concurrency test; suspicious in a demo. |
| Retries on a path with a single writer | A bug, not contention (`quality/23_PHASE_GATES.md` §23.9). Something outside the Kernel is touching canonical rows. |
| Retries with commit latency in seconds | A slow statement inside the transaction. Most often a model call or an HTTP call that escaped the purity lint. |

**Fix.**

1. Confirm nothing external is inside the callback: `python -m tools.txn_purity_lint services packages workers` must print `0 network constructs found`. A model call inside a transaction turns every model latency spike into a retry storm and is a hard guardrail violation.
2. Confirm the writer really is single: `python -m tools.write_path_lint`.
3. If contention is genuine, do **not** raise the retry budget above 5. Exhaustion must surface as an error, never as a silent no-op (`G3.3`). Reduce concurrent submissions instead, or accept the retry and let telemetry show it.
4. Keep the transaction short. Compute the full write plan, including every UUID and the `kernel_decision_id`, before `BEGIN`. That is why the schema forbids `DEFAULT gen_random_uuid()` on primary keys.

**Do not** monkeypatch the driver to suppress `40001`, and do not prove retry behaviour by monkeypatching one in. `G3.2` requires the conflict be forced by two overlapping transactions on one row; a patched error proves nothing about CockroachDB.

---

### 7.2 Outbox rows stuck `PENDING`, or reaching `DEAD`

**Symptom.** The case reopened in the UI but no downstream reaction occurred. `outbox-pending-age` alarm firing, or `dlq-depth` above zero.

**Diagnose.**

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT status, count(*), min(created_at) AS oldest, max(attempt_count) AS max_attempts
  FROM outbox_events GROUP BY 1 ORDER BY 1;"

cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT id, event_type, aggregate_id, aggregate_version, attempt_count, last_error
  FROM outbox_events WHERE status IN ('PENDING','DEAD')
  ORDER BY created_at LIMIT 10;"

aws sqs get-queue-attributes --queue-url "$SQS_DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

| Reading | Cause |
|---|---|
| `PENDING` rows, oldest is minutes old, `attempt_count = 0` | The dispatcher is not running. Locally: the worker was never started. Deployed: the EventBridge rule is disabled or the Lambda is erroring on init. |
| `PENDING` with rising `attempt_count` | Publish is failing. Read `last_error`. Usually IAM on `events:PutEvents` or a wrong `EVENTBRIDGE_BUS_NAME`. |
| `DEAD` rows | The 1s/5s/30s/2m/10m schedule was exhausted. Alarm fired. Manual replay required. |
| DLQ non-empty | A consumer rejected the payload repeatedly. This is a poison message, not a delivery failure. |

**Fix.**

```bash
# Dispatcher not running, locally
python -m workers.outbox_dispatch.local --interval 2

# Dispatcher not running, deployed
aws events enable-rule --name provenance-outbox-sweep --event-bus-name "$EVENTBRIDGE_BUS_NAME"
aws logs tail /aws/lambda/provenance-outbox-dispatch --since 10m

# Replay DEAD rows after the root cause is fixed
python -m workers.outbox_dispatch.replay --status DEAD --limit 50 --confirm
```

Replay is safe by construction: the sweeper takes a lease, and consumers dedupe on `(event_id, consumer_name)` in `processed_events`, so a replayed event produces exactly one effect (`G10.1`). Verify after replay:

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT status, count(*) FROM outbox_events GROUP BY 1;"
#   → PUBLISHED only
```

**Do not** delete stuck rows. An outbox row is the durable record that a committed state change has not yet been announced; deleting it makes "state changed" and "the world was told" disagree permanently, which is the exact failure the outbox exists to prevent. **Do not** rely on exactly-once delivery anywhere; it is a listed guardrail violation.

---

### 7.3 Vector index missing, disabled, or wrong dimensionality

**Symptom.** Retrieval is slow; `EXPLAIN` shows a full scan; `G6.2` fails; or writes to `evidence_items` reject with a dimension error.

**Diagnose.**

```bash
cockroach sql --url "$PV_DB_APP" -e "SHOW INDEXES FROM evidence_items;"
cockroach sql --url "$PV_DB_APP" -e "SHOW CLUSTER SETTING feature.vector_index.enabled;"
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT count(*) AS rows_with_vec,
         count(*) FILTER (WHERE embedding IS NULL) AS rows_without_vec
  FROM evidence_items;"
cockroach sql --url "$PV_DB_APP" -e "EXPLAIN (VERBOSE) <the §5.5 retrieval query>;" \
  | grep -iE "evidence_embedding_ann_idx|full scan"
```

| Reading | Cause | Fix |
|---|---|---|
| Index absent from `SHOW INDEXES` | The seed dropped it (§4.2 step 4) and never recreated it. **This is the single most likely cause.** | Re-run step 7's `CREATE VECTOR INDEX`, wait for the job, re-check. |
| Setting is `false` | Someone reset the cluster setting | `SET CLUSTER SETTING feature.vector_index.enabled = true;` as `pv_migrator`, then rebuild the index (§3.1). |
| Index present, `EXPLAIN` shows full scan | The query shape does not match the prefix | `user_id = $1` must sit **inside** the ranked CTE; `tenant_id`, `retraction_status`, and `embedding_version` filters must sit **outside** it. Adding non-prefix predicates to the ANN block disqualifies the index. |
| `expected 1024 dimensions, got N` on insert | An embedding was produced at another size | Titan v2 supports several output sizes; `dimensions: 1024` is frozen. Clear the affected cache entries and re-resolve (§4.3). |
| Index build job stuck | Asynchronous schema change still running | `SHOW JOBS WHERE description ILIKE '%evidence_embedding_ann_idx%';` and wait. Do not start a second build. |

**Fix, index rebuild:**

```sql
DROP INDEX IF EXISTS evidence_embedding_ann_idx CASCADE;
CREATE VECTOR INDEX evidence_embedding_ann_idx
    ON evidence_items (user_id, embedding vector_cosine_ops);   -- or the recorded variant
```

**Degradation, if it cannot be rebuilt.** `PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION`. Roughly 16,035 hero rows scanned within the user partition; survivable for a demo, disclosed in Judge Mode and in `SUBMISSION.md`, sponsor vector-index claim blocked. Never present it as vector indexing.

**Do not** "fix" retrieval by deleting the embeddings of retracted rows. `specs/10_DATABASE_DDL.md` §5.4 gives four independent reasons that is wrong; retracted vectors stay indexed and the query filters them.

---

### 7.4 Embedding version mismatch

**Symptom.** Recall collapses without an error. Retrieval returns semantically unrelated neighbours. `G6.1` returns more than one row.

**Diagnose.**

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT embedding_version, count(*) FROM evidence_items
  WHERE embedding IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;"
#   → PASS: exactly one row, "v1,18035"
#   → FAIL: two or more rows

grep -n "EMBEDDING_VERSION" .env.local
python -c "from provenance_contracts.settings import Settings; s=Settings(); \
           print(s.embedding_version, s.embedding_dimensions, s.bedrock_embedding_model_id)"
```

**Cause.** The query vector and the stored vectors are in different spaces. Either the settings were changed after seeding, a partial backfill ran, or the normalisation template changed. `specs/13_RETRIEVAL_SPEC.md` is explicit that a template change shifts the vector space just as surely as a model change; both are breaking.

**Fix.**

1. Decide which version is canonical. It is `v1` unless a deliberate, recorded migration says otherwise.
2. Re-embed the minority set with the canonical version, or delete the minority rows if they are decoys and reseed. Never mix.
3. Confirm the retrieval query binds `embedding_version = $6` as a predicate. It exists so a half-finished backfill degrades recall **visibly** rather than silently returning garbage neighbours.

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT id FROM evidence_items WHERE embedding_version <> 'v1' LIMIT 20;"
python -m scripts.seed --reembed --embedding-version v1 --only-mismatched
```

**Do not** change `EMBEDDING_VERSION` to make a mismatch disappear. That relabels the problem. A genuine version change requires a parallel index and a full backfill, never an in-place mix.

---

### 7.5 Bedrock throttling and access denied

These two produce similar-looking failures and have opposite fixes. Separate them first.

**Symptom A — `ThrottlingException`.** Intermittent; worsens under load; the seed's embedding pass stalls; extraction fails and retries.

**Diagnose.**

```bash
aws logs filter-log-events --log-group-name /provenance/control-plane \
  --filter-pattern "ThrottlingException" --max-items 20
aws service-quotas list-service-quotas --service-code bedrock \
  --query "Quotas[?contains(QuotaName,'Claude')||contains(QuotaName,'Titan')].[QuotaName,Value]" \
  --output table
```

**Fix.** Reduce concurrency before anything else: embeddings to `MAX_INFLIGHT=8` or lower, and no parallel graph runs during a demo. The canonical retry budget is 2–3 network/throttle retries with exponential backoff and jitter, and **no nested retry loops**. Tier E may take one schema-repair attempt and then one Opus 5 fallback at low effort; exhaustion becomes a pending review. **Tier R never downgrades to a weaker model** — failure persists a pending-human-review result. Request a quota increase for the deployed account; it is not instant, so do it in Phase 0.

**Symptom B — `AccessDeniedException`.**

**Diagnose.**

```bash
aws sts get-caller-identity
echo "$AWS_REGION"                       # must be us-east-1
python - <<'PY'
from anthropic import AnthropicBedrockMantle
c = AnthropicBedrockMantle(aws_region="us-east-1")
print(c.messages.create(model="anthropic.claude-haiku-4-5", max_tokens=8,
      messages=[{"role":"user","content":"ok"}]).content[0].text)
PY
```

| Reading | Cause | Fix |
|---|---|---|
| Denied for all three model ids | Model access never granted, or wrong region | Grant access in the Bedrock console for us-east-1. Re-run PB-5. |
| Denied for Opus 5 only | Per-model grant missing | Request that model specifically. Tier R has no fallback model by canon. |
| Denied from AgentCore but fine locally | Execution role lacks `bedrock:InvokeModel` | Fix the AgentCore execution role policy, not the client. |
| `400` mentioning `temperature`/`top_p`/`top_k`/`budget_tokens` | Forbidden sampling parameter | Remove it. These return 400 on both canonical models; steer with prompt text only. |

**Do not** silently fall back to a different model. `G7.4` fails any output naming a non-canonical identifier, and an undisclosed model swap is exactly the kind of thing the tool-usage disclosure exists to prevent.

---

### 7.6 AgentCore cold start

**Symptom.** The first artifact upload after an idle period takes many seconds or times out with `503 UPSTREAM_UNAVAILABLE` and `dependency: "AGENTCORE"`. Subsequent runs are fast.

**Diagnose.**

```bash
for i in 1 2 3; do
  curl -sS -o /dev/null -w '%{time_total}\n' "$PV_API/v1/me" -H "Authorization: Bearer $PV_TOKEN"
done
#   → first value is the cold number; record it

aws logs tail /provenance/agent-runtime --since 15m --filter-pattern "cold_start"
```

**Fix.**

1. This is a latency problem, not a correctness problem. The artifact stays `PENDING_INTERPRETATION` and is retried; evidence is preserved even when cognition is unavailable. That behaviour is the design, not a bug to work around.
2. If the cold path exceeds 10 seconds, the demo script **must** include an explicit warm-up request, and that fact must be written into the demo runbook rather than remembered (`G13.8`).
3. Warm-up, run at T-15 minutes before any demo:

```bash
curl -sS "$PV_API/v1/me"     -H "Authorization: Bearer $PV_TOKEN" > /dev/null
curl -sS "$PV_API/v1/healthz"   > /dev/null
python -m agents.runtime.tools.smoke --tier E --tier R --print-model-id
#   → "tier=E model=anthropic.claude-haiku-4-5 ok"
#   → "tier=R model=anthropic.claude-opus-5 ok"
```

**Do not** paper over a cold start by pre-committing the hero result. The reopen must be computed at demo time; `S7`'s seeded-versus-computed table says so in writing.

---

### 7.7 Cognito token audience or issuer mismatch

**Symptom.** `401` on every authenticated route, or `403` with `WORKLOAD_TOKEN_ON_PUBLIC_ROUTE` / `BROWSER_TOKEN_ON_INTERNAL_ROUTE` when the caller believes it holds the right token.

**Diagnose.** Decode the token's claims without validating (locally only, never on a real user's token):

```bash
python - <<'PY'
import base64, json, os, sys
tok = os.environ["PV_TOKEN"].split(".")[1]
tok += "=" * (-len(tok) % 4)
print(json.dumps(json.loads(base64.urlsafe_b64decode(tok)), indent=2))
PY
#   inspect: iss, aud (or client_id), token_use, scope, exp
```

```bash
grep -E "COGNITO_ISSUER|COGNITO_WEB_CLIENT_ID|COGNITO_AGENT_CLIENT_ID|COGNITO_WORKER_CLIENT_ID" .env.local
curl -sS "$COGNITO_ISSUER/.well-known/openid-configuration" | jq '{issuer, jwks_uri}'
```

| Reading | Cause | Fix |
|---|---|---|
| `iss` differs from `COGNITO_ISSUER` | Wrong pool or wrong region | Set `COGNITO_ISSUER` to `https://cognito-idp.us-east-1.amazonaws.com/<pool-id>` exactly. A trailing slash is a mismatch. |
| `token_use: "access"` with `client_id` but no `aud` | Normal for client-credentials tokens | Validate `client_id` for M2M and `aud` for human ID tokens. Validating `aud` on an access token will always fail. |
| `scope` lacks the required value | App client not granted the scope | Grant it on the app client: agents get `provenance.memory/read`, `provenance.memory/propose`, `provenance.action/propose`; workers get `provenance.ingest/write`, `provenance.trigger/evaluate`, `provenance.action/execute`, `provenance.outbox/dispatch`. |
| `403 WORKLOAD_TOKEN_ON_PUBLIC_ROUTE` | A machine token hit `/v1` | Correct behaviour. The route-class check is working. Use `/internal/v1`. |
| `403 BROWSER_TOKEN_ON_INTERNAL_ROUTE` | A browser token hit `/internal/v1` | Correct behaviour. |
| `exp` in the past | Clock skew or a stale token | Re-mint. Allow at most 60 s of skew in validation. |

**Do not** relax the route-class check, and never accept a caller-supplied `user_id` from a machine client. Internal APIs take a capability object id (`agent_run_id`, `trigger_id`, `action_intent_id`, ingest alias) and resolve the subject server-side.

---

### 7.8 SES sandbox rejection

**Symptom.** The executor's send fails with `MessageRejected: Email address is not verified. The following identities failed the check in region US-EAST-1: <recipient>`. `action_executions` records the failure; the case is unaffected.

**Diagnose.**

```bash
aws sesv2 get-account --query '{SendingEnabled:SendingEnabled, ProductionAccess:ProductionAccessEnabled, Quota:SendQuota}'
aws sesv2 list-email-identities --query 'EmailIdentities[].[IdentityName,VerifiedForSendingStatus]' --output table
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT attempt_no, status, error_code, provider_correlation_id
  FROM action_executions ORDER BY created_at DESC LIMIT 5;"
```

**Cause.** In the SES sandbox, **both** sender and recipient must be verified identities, and the daily send quota is small.

**Fix.**

```bash
aws sesv2 create-email-identity --email-identity "$SES_FROM_ADDRESS"
aws sesv2 create-email-identity --email-identity demo-inbox@<your-domain>
# Click the verification links in both mailboxes, then:
aws sesv2 get-email-identity --email-identity demo-inbox@<your-domain> \
  --query 'VerifiedForSendingStatus'
#   → true
```

Keep `PV_ACTION_ALLOWLIST` set to the verified demo inbox and nothing else. The allowlist is asserted independently by `G9.5`, so an unverified recipient should be refused with `RECIPIENT_NOT_ALLOWLISTED` **before** SES ever sees it. If SES is the layer that rejected it, the allowlist is misconfigured too — fix both.

If production access is still pending at demo time, the correct move is the local sink (`SES_TRANSPORT=smtp://localhost:1025`) with the substitution disclosed on screen. The safety property being demonstrated is revalidation-then-send, and that property is fully visible against a sink.

**Do not** request production access days before the deadline and assume it lands. **Do not** widen the allowlist to a judge's real mailbox.

---

### 7.9 A stale `ActionIntent` blocks a send

**Symptom.** Approve and Send returns `409 ACTION_STALE`, or the executor records `status=ABORTED_STALE`, `error_code=CASE_REVISION_MOVED`, and zero provider calls.

**Diagnose.**

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT ai.id, ai.status, ai.basis_case_revision, c.revision AS current_revision,
         encode(ai.approval_draft_sha256,'hex') AS approved_hash
  FROM action_intents ai JOIN cases c ON c.id = ai.case_id
  WHERE ai.id = '<action-intent-id>';"

curl -sS "$PV_API/v1/cases/<case-id>/timeline?since_revision=<basis_case_revision>" \
  -H "Authorization: Bearer $PV_TOKEN" | jq '[.items[] | {kind, at, summary}]'
```

| Reading | Cause |
|---|---|
| `current_revision > basis_case_revision` | Memory changed after the approval. **This is the system working.** |
| Hashes differ | The draft was edited after approval. Also working as designed. |
| Both match, still refused | Look for `NO_COMMITTED_BASIS`: the intent's case has no committed `kernel_decisions` row, so invariant 4 refuses it. |

**Fix.** There is no fix that bypasses the check, and there must never be one. The correct path:

1. Refetch the case and the State Proof.
2. Render `changed_since` as a diff for the human: "2 things changed since this draft".
3. Regenerate or re-review the draft.
4. Take a **fresh** human approval, which binds the new `case.revision` and the new `draft_sha256`.

**Never auto-retry an approval.** An approval is a human act; retrying it in code forges consent. If you need to stop all sends immediately, use the kill switch:

```bash
PV_ACTION_EXECUTION_MODE=DISABLED
# Approvals continue to be recorded; nothing is sent.
```

---

### 7.10 A trigger fired but no-op'd unexpectedly

**Symptom.** The landlord deposit trigger woke and produced `NO_OP` when you expected `FIRED`, or produced an unexplained `NO_OP` during a rehearsal.

**Diagnose.** Every `NO_OP` carries a reason code from the closed set. Read it; absence of an error is not a diagnosis.

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT id, trigger_type, state, last_result, last_reason_code,
         not_before, basis_case_revision, evaluation_version, fired_at
  FROM prospective_triggers WHERE case_id = '<case-id>';"

cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT status, outstanding_amount, due_at FROM commitments
  WHERE id = '<commitment-id>';"
```

| `last_reason_code` | Meaning | Action |
|---|---|---|
| `PREDICATE_FALSE` | The obligation is genuinely satisfied or not yet due | Correct behaviour. Check `outstanding_amount` and `due_at`; if the seed left `outstanding_amount = 0`, the seed is wrong, not the evaluator. |
| `WOKE_TOO_EARLY` | `db_now < not_before` (guard G5) | Correct. It re-arms at `not_before + 60 s`. |
| `TRIGGER_NOT_ARMED` (guard G2) | Already `FIRED` or `DISARMED` | Correct. Pressing the manual wake twice is safe and Judge Mode shows the second result as a feature. |
| `STALE_SCHEDULE_GENERATION` (guard G3) | The schedule's `evaluation_version` is behind the row | Correct. An old EventBridge schedule fired against a re-armed trigger. |
| `CASE_RESOLVED` with `state = DISARMED` | The case closed before the deadline | Correct, and it is exactly what `G10.2` asserts. |
| `BINDING_SUPERSEDED` | The watched commitment was replaced by a renegotiated one | Correct. The new commitment's own trigger is the live one. |
| `CONCURRENT_CASE_MUTATION` | The Kernel returned retryable concurrency | Re-arms in 5 minutes. Investigate only if it repeats. |
| `PREDICATE_UNKNOWN` | A projection field was unavailable | **This one is a real problem.** A field in the predicate AST does not resolve. Check the projection registry. |
| `NULL` reason code | A swallowed exception | **Gate failure.** Every no-op names its reason (`quality/23_PHASE_GATES.md` §23.8). Fix the handler. |

**Fix, to reproduce a legitimate fire:**

```bash
python -m workers.trigger_wakeup.local --trigger-id <uuid> --print-field-values
#   → {"outstanding_amount": "1800.0000", "due_at": "<past>", "status": "ACTIVE"}
#   → result=FIRED reason=COMMITMENT_OVERDUE_UNPAID
```

**Do not** mutate canonical deposit state and secretly revert it to make a trigger fire on cue. `CANONICAL_DECISIONS.md` forbids it by name: the no-op demonstration uses a real false predicate and no hidden state revert. Use the same manual-wake entry point for both the false-predicate no-op and the landlord fire.

---

### 7.11 Cross-tenant isolation test failure

**Symptom.** `tests/db/test_12_vector_scope_and_retraction.py` part (a) fails: at least one of 200 returned ids belongs to `iso-a` or `iso-b`.

**This is the most serious failure in this document.** Treat it as a stop-work item. It means the isolation spine is not doing its job, and every other correctness claim in the build inherits the doubt.

**Diagnose.**

```bash
cockroach sql --url "$PV_DB_APP" --format=csv -e "
  SELECT e.tenant_id, count(*) FROM evidence_items e GROUP BY 1;"
#   → three tenants; the hero tenant with 16,035, iso-a and iso-b with 1,000 each

cockroach sql --url "$PV_DB_APP" -e "SHOW INDEXES FROM evidence_items;" \
  | grep evidence_embedding_ann_idx
#   → the indexed columns MUST begin with user_id

pytest tests/retrieval/test_no_unscoped_sql.py -q
#   → every retrieval statement contains a user_id predicate
```

Then read the ANN query against `specs/10_DATABASE_DDL.md` §5.5, line by line:

| Check | Requirement |
|---|---|
| `user_id = $1` | **Inside** the ranked CTE. Moving it to the outer query breaks the prefix match and scans every user. |
| `tenant_id = $2` | Outside the CTE, as defence in depth. |
| Over-fetch | `k_raw = greatest(40, 4 * k_final)`. Never `k_raw == k_final`. |
| Source of `$1`/`$2` | The verified `Principal`. **Never** a request body. |

**Fix.**

1. If the index prefix is wrong, rebuild it with `user_id` first. Nothing else will hold: the prefix is the mechanism by which ANN physically cannot return another user's evidence, and a `WHERE` clause a future refactor could drop is not the same guarantee.
2. If the query shape drifted, restore it to §5.5 verbatim. `provenance_db.repositories.evidence.ann_search()` is the only sanctioned emitter; a hand-rolled variant is rejected under the handoff guardrails.
3. Re-run with the positive control, so a pass cannot be vacuous:

```bash
pytest tests/retrieval/test_isolation.py tests/db/test_12_vector_scope_and_retraction.py -q -s
#   → (a) 0 of 200 ids belong to iso-a or iso-b
#   → (b) EXPLAIN names evidence_embedding_ann_idx
#   → (c) none of the 3 retraction fixtures appear
#   → (d) POSITIVE CONTROL: with the retraction predicate removed,
#         sid('evidence','isp-wrong-term-date') appears within the top 20
#   → "4 passed"
```

If (d) fails, (c) was passing vacuously and (c) proves nothing.

**Do not** narrow the isolation corpora to make the test pass. The `iso-a` and `iso-b` rows are deliberately near-identical to the hero's — same ISP name, same amounts, same dates — precisely so a broken prefix leaks loudly instead of passing on an empty database.

---

### 7.12 App Runner deploy failure

**Symptom.** The service goes to `CREATE_FAILED` or `OPERATION_IN_PROGRESS` and never becomes `RUNNING`; or `GET /v1/version` returns a `git_sha` that is not the reviewed commit.

**Diagnose.**

```bash
aws apprunner describe-service --service-arn "$PV_APPRUNNER_ARN" \
  --query 'Service.{Status:Status, Image:SourceConfiguration.ImageRepository.ImageIdentifier}'
aws apprunner list-operations --service-arn "$PV_APPRUNNER_ARN" --max-results 5
aws logs tail /aws/apprunner/provenance-control-plane/service --since 20m
curl -sS "$PV_API/v1/version" | jq -r '.git_sha + " " + .schema_revision'
```

| Reading | Cause | Fix |
|---|---|---|
| Health check failing at `/v1/healthz` | Container starts, dependency missing | Almost always `COCKROACH_DATABASE_URL` not resolving. Check `RuntimeEnvironmentSecrets` and the instance role's `secretsmanager:GetSecretValue`. |
| `ImagePullFailure` | ECR permission or wrong tag | Confirm the ECR policy allows the App Runner access role and that the tag exists. |
| Settings `ValidationError` in logs | A required env var is absent | By design: the settings object raises rather than defaulting. Add the variable; do not add a default. |
| Service runs, `build_sha` is stale | Deployment did not roll | `aws apprunner start-deployment --service-arn "$PV_APPRUNNER_ARN"`, then re-check. `G13.2` requires **string equality** with `git rev-parse HEAD`, not a prefix. |
| `cdk diff` shows differences after deploy | Drift | Drift after deploy is a gate failure (`G13.1`). Re-deploy from code; never hand-edit the service. |
| Plaintext secret in `RuntimeEnvironmentVariables` | Secret leaked into config | Rotate first, fix config second (`G13.6`). |

**Rollback.**

```bash
aws apprunner update-service --service-arn "$PV_APPRUNNER_ARN" \
  --source-configuration '{"ImageRepository":{"ImageIdentifier":"<previous-tag>", ...}}'
```

**Schema does not roll back.** Code rolls back; migrations roll forward. `G13.9` verifies the immediately previous image against the head schema **before** each deployment, so a code rollback stays possible. If a migration breaks that compatibility, the correct move is a forward fix or an expand/migrate/contract sequence across separate releases, never a downgrade against a deployed database.

---

## 8. Demo operations

### 8.1 Dress rehearsal

Run the full rehearsal **twice**: once at T-24 hours and once within two hours of recording or presenting. The first run finds the problems; the second proves nothing rotted.

```bash
make demo-rehearse
```

which performs, in order:

```text
 1. make demo-reset && make seed && make db-verify
      → "V1 0 ... V10 0  V11 3"
 2. Warm-up: /v1/healthz, /v1/version, /v1/me, and one Tier E + one Tier R smoke call.
 3. Assert live mode:
      curl -sS "$PV_API/v1/version" | jq '{fixture_mode, agent_mode, git_sha, schema_revision}'
      → fixture_mode == false
 4. Dashboard state, before anything is uploaded:
      4 relationships in "The Move"; USD 2,020 outstanding;
      old ISP cancellation RESOLVED, revision 12, attention_level NONE.
 5. Upload demo_data/the_move/E3_isp_invoice.eml through the real UI path.
 6. Assert, from a fresh connection, after the workers drain:
      cases: RESOLVED -> REOPENED, revision 12 -> 13, reopened_count 0 -> 1,
             attention_level NONE -> URGENT
      claims +1 (COUNTERPARTY_CLAIM), conflicts +1 (NEEDS_HUMAN, HIGH, requires_human),
      belief_support +1 CONTRADICTS, state_transitions +1, outbox_events +1
             (aggregate_version 13)
 7. State Proof: grounding relations {SUPPORTS, CONTRADICTS}; lineage depth 2;
      one superseded version. Confirm the balance_owed caption reads correctly:
      the amount did not change, the confidence did.
 8. Counterfactual: POST /v1/judge-mode/counterfactual, then assert
      memory_off contains "$186" and NOT "15 May"/"terminat"/"reopen";
      memory_on contains "15 May" AND "reopened";
      safety.case_revision_changed_by_counterfactual == false;
      cases.revision identical before and after.
 9. Approve and send. Executor revalidates revision 13 and the draft SHA-256.
      Confirm the message landed in the safe demo inbox exactly once.
10. Landlord trigger: wake the false-predicate trigger first (expect NO_OP with
      its reason code), then the deposit trigger (expect FIRED).
      Both through the same manual-wake entry point. No hidden state mutation.
11. Memory Trace: >= 3 MCP tool calls, every sql_role == pv_agent_reader,
      every access_mode == READ_ONLY, every view in the five agent_*_v1 names.
12. Reset again, so the rehearsal does not leave the demo half-consumed.
```

Time the whole thing and write the number down. The video budget is 2:55 against a hard 180.0-second limit; a flow that takes four minutes to execute cannot be narrated in three.

### 8.2 Reset to clean demo state

```bash
make demo-reset && make seed && make db-verify
```

`make demo-reset` is destructive and guarded:

```bash
#!/usr/bin/env bash
# ops/demo-reset.sh
set -euo pipefail

[[ "${APP_ENV:-}" =~ ^(local|demo)$ ]] || {
  echo "REFUSING: APP_ENV='${APP_ENV:-unset}' is not local or demo" >&2; exit 1; }

echo "Target database: $(cockroach sql --url "$PV_DB_MIGRATOR" --format=csv \
        -e 'SELECT current_database();' | tail -1)"
read -r -p "Type the database name to confirm destruction: " CONFIRM
[[ "$CONFIRM" == "$PV_EXPECTED_DB" ]] || { echo "ABORTED" >&2; exit 1; }

# 1. Stop consumers so nothing writes mid-reset.
pkill -f workers.outbox_dispatch.local || true

# 2. Truncate in reverse foreign-key order. Never DROP the schema: the migration
#    chain is the schema contract and re-running it is slower and riskier here.
python -m scripts.seed --reset --truncate-only

# 3. Clear the demo-only artifact prefix. Curated artifacts are re-uploaded by the seed.
aws s3 rm "s3://${S3_ARTIFACT_BUCKET}/raw/${PV_DEMO_TENANT}/" --recursive

# 4. Purge queues so a stale event cannot fire mid-demo.
aws sqs purge-queue --queue-url "$SQS_DLQ_URL" || true

# 5. Delete any one-time EventBridge schedules left from a previous rehearsal.
aws scheduler list-schedules --group-name "$EVENTBRIDGE_SCHEDULER_GROUP" \
  --query 'Schedules[].Name' --output text \
  | tr '\t' '\n' | while read -r s; do
      [[ -n "$s" ]] && aws scheduler delete-schedule --name "$s" \
        --group-name "$EVENTBRIDGE_SCHEDULER_GROUP"
    done

echo "Reset complete. Run: make seed && make db-verify"
```

Two properties make this safe to run under pressure:

- **The demo database is regenerable at every phase, deliberately.** If that ever stops being true, the phase-gates document is wrong and must be amended before proceeding.
- **The embedding cache survives a reset.** `scripts/seed/.embedding-cache/` is not touched, so a reseed costs minutes of inserts, not minutes of Bedrock calls plus spend. Populate the cache at the first seed, not later.

`S10` requires this exact sequence to be run **last** in the pre-submission battery, followed by re-running the hero flow. A demo that only works on a database that has already been demoed on does not work.

### 8.3 Pre-demo checklist

**T-60 minutes**

- [ ] `make demo-reset && make seed && make db-verify` → `V1 0 … V10 0  V11 3`
- [ ] `curl -sS "$PV_API/v1/version" | jq '{git_sha, fixture_mode, agent_mode, schema_revision}'` → `fixture_mode: false`, `agent_mode: "LIVE"`, `git_sha` equals `git rev-parse HEAD`
- [ ] `cdk diff --all` → "There were no differences"
- [ ] `aws cloudwatch describe-alarms --alarm-name-prefix provenance-` → every alarm `OK`, not `INSUFFICIENT_DATA`
- [ ] `SHOW INDEXES FROM evidence_items` includes `evidence_embedding_ann_idx`
- [ ] `demo_data/the_move/E3_isp_invoice.eml` present locally **and** the flow verified from a local file, so SES inbound DNS is not on the critical path
- [ ] Safe demo inbox reachable and empty

**T-15 minutes**

- [ ] Warm-up: `/v1/healthz`, `/v1/version`, `/v1/me`, one Tier E and one Tier R smoke call (§7.6)
- [ ] Second network check: `curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' "$PV_WEB"` from a phone hotspot → `200` under 3.0 s
- [ ] Judge login works from a clean browser profile
- [ ] Browser: one tab, cache cleared, console open and empty, zoom at 100%
- [ ] `PV_ACTION_EXECUTION_MODE=ENABLED`, `PV_MCP_ENABLED=true`, `PV_AGENT_MODE=LIVE`
- [ ] Contingency ladder (§9) open in a second window, with the disclosure lines ready to read

**T-2 minutes**

- [ ] Dashboard loaded and showing four relationships, USD 2,020 outstanding, the ISP case resolved
- [ ] `outbox_events` has zero `PENDING` rows
- [ ] No armed trigger is due to fire during the demo window except the one you intend to wake
- [ ] Screen recording started before the first click, so the "before" state is on tape

---

## 9. Contingency ladder

If a live component fails mid-demo, walk **down** this ladder one rung at a time. Each rung names what may be degraded, what must be said out loud and shown on screen, and what may never be faked. Do not skip rungs; each one preserves more of the claim than the next.

| Rung | Trigger | Degrade to | Disclose on screen | Cost |
|---|---|---|---|---|
| **L0** | Nominal | Nothing | Nothing | — |
| **L1** | Managed MCP Server unreachable or slow | `PV_MCP_ENABLED=false`; the Interpreter falls back to the control-plane retrieval endpoint | Memory Trace renders **"MCP UNAVAILABLE — degraded read path"**. Say it: "the governed MCP read is down; this is the fallback, and the trace shows the degradation." | One of the two required CockroachDB tools is not live. If it stays down through submission, `S5` tool 2 is blocked. |
| **L2** | Vector index missing or `EXPLAIN` shows a full scan | `PV_RETRIEVAL_MODE=BRUTE_FORCE_PARTITION` | Judge Mode shows **"BRUTE-FORCE PARTITION SCAN — vector index unavailable"**. Say it: "this is a scan within the user partition, not vector indexing." | Sponsor vector-index claim blocked; `G6.2` and `S5` tool 1 fail. Retrieval results are still correct. |
| **L3** | Bedrock throttled or a model call fails mid-run | Retry once within the canonical budget; if Tier E fails, one schema repair then one Opus 5 fallback at low effort; **Tier R never downgrades** and persists a pending-human-review result | The UI shows the pending-review state, which is a designed outcome, not an error screen. Say it: "the reasoning model is unavailable; the evidence is committed and the conclusion is queued for a human." | Reasoning delayed. Evidence and canonical state are unaffected, which is the point worth making. |
| **L4** | AgentCore cold, unavailable, or the graph run times out | `PV_AGENT_MODE=FIXTURE` | The non-dismissible banner **"DEMO FIXTURE MODE — model outputs are replayed"**, plus `fixture_mode: true` in `GET /v1/version`. Say it: "model outputs are replayed; the Kernel, the database, the transaction, and the outbox are all live." | The recorded submission is invalidated (`S3`). Acceptable live; never acceptable on tape. |
| **L5** | SES rejects or is throttled | `SES_TRANSPORT=smtp://localhost:1025`, the local sink | Show the sink's inbox and say it is a local sink, not a delivered email. | Nothing about the safety property is lost: revalidation of revision and draft hash happens before the sink call, and that is what the segment demonstrates. |
| **L6** | The deployed stack is unreachable | Run the local stack against the **same real CockroachDB Cloud cluster** | Say it: "the deployed URL is down; this is the same build running locally against the same production database." | `S3`'s functional-demo-URL requirement is at risk. The memory system itself is unaffected. |
| **L7** | The database is unreachable | **Stop the demo.** | Say so, and take questions on architecture from the specification documents. | Everything. There is no rung below this one, because everything below it would be theatre. |

### 9.1 What may never be faked, at any rung

These are not judgement calls.

1. **A database commit.** No pre-inserted conflict, no pre-reopened case, no pre-incremented revision. The reopen, the conflict, the revision increment, the trigger evaluation, and the draft are computed at demo time, and `S7`'s seeded-versus-computed table says so in writing.
2. **Memory Trace content.** Every rendered node id must exist in the API payload. No scripted animation, no hard-coded UUIDs in `apps/web/src`. A trace that does not change when the database changes is a picture, and it is the first thing a hostile judge will test.
3. **The counterfactual.** Memory OFF must run the identical graph, identical model (`anthropic.claude-opus-5`), identical prompt, and identical artifact, differing only in that retrieval returns empty and State Proof is empty. A different prompt on the same context is a rigged comparison. Keep the request-payload diff available for live questions.
4. **MCP tool calls.** The trace's MCP nodes come from the `agent_runs.tool_calls` column. Deleting that row must empty the panel. Denied calls are rendered in red, not swallowed.
5. **A sent message.** If nothing was sent, do not imply that something was.
6. **Fixture mode without its banner.** Undisclosed fixture mode is fraud, and it is trivially detectable by a judge who reads `GET /v1/version`.
7. **Case revision numbers.** They are the spine of the approval-staleness guarantee. A cosmetic revision number is a lie about the safety property.

### 9.2 The disclosure sentence

Whatever rung you are on, one sentence covers it, and it is better said early than extracted later:

> "One component is degraded right now: **\<name\>**. Here is what that changes: **\<the specific capability\>**. Everything you are about to see below that line is live."

An honest degradation demonstrated well scores better than a perfect demo a judge suspects. The five criteria are equally weighted, and Product Readiness explicitly covers resilience.

---

## 10. Risks and open questions

**R1 — The probe-transcript line count is a documented conflict, and it will bite.** `specs/10_DATABASE_DDL.md` §1 numbers the cleanup step `-- P12`, while `G0.6` asserts exactly eleven `-- P` lines in `ops/cluster-probe.txt`. §3.0 resolves it operationally by writing cleanup as `-- CLEANUP`. *Assumption:* eleven is the intended count because there are eleven capability probes and cleanup is not one. If a reviewer reads it the other way, `G0.6`'s expected value changes, not this procedure. Raise it at `G-0` rather than discovering it during a battery.

**R2 — The seed's drop-and-rebuild of the vector index is a procedure this runbook introduces.** `specs/10_DATABASE_DDL.md` §16 has migration `0002_evidence_plane` create the index; the external constraint that `IMPORT INTO` is unsupported on vector-indexed tables and that large batch inserts degrade badly forces the seed to drop it, load, and rebuild. The end state is identical to the migration's, so no schema contract changes. *Risk:* a seed that fails between step 4 and step 7 leaves the database without its index, and everything still appears to work while `G6.2` fails. *Mitigation:* step 7 is followed by an explicit `SHOW INDEXES` assertion in the seed itself, and the seed exits non-zero if the index is absent. This mitigation must actually be implemented; it is the difference between a loud failure and a quiet one.

**R3 — The embedding cost estimate depends on a price I cannot verify from here.** The USD 0.015 figure assumes USD 0.02 per million input tokens for `amazon.titan-embed-text-v2:0` and a 40-token mean per evidence item. Both are engineering estimates, not measurements. *Mitigation:* the sensitivity check at 10× still lands under USD 0.15, so the operational conclusion — time and throttling are the constraints, not money — is robust to a large pricing error. Verify against the current Bedrock pricing page before quoting the number to a judge.

**R4 — The committed vector cache does not fit in git.** `quality/23_PHASE_GATES.md` §12 recommends caching seed vectors in `db/seeds/vectors.parquet` so a reseed does not re-invoke Bedrock. At 18,035 × 1024 × float32 that is roughly 74 MB before compression, and random-ish floats compress poorly. That is above GitHub's 50 MB warning and uncomfortably near its 100 MB hard limit. *Recommendation:* keep `scripts/seed/.embedding-cache/` local and gitignored, publish one `s3://$S3_ARTIFACT_BUCKET/seed/vectors-v1.parquet`, and commit only a SHA-256 manifest. Storing float16 (about 37 MB) is a second option but changes the stored vectors, which touches the frozen embedding contract and should not be done casually. This needs a decision before Phase 2.

**R5 — Resolved.** Trigger-type naming is reconciled. `specs/10_DATABASE_DDL.md` §17.6 was corrected to `COMMITMENT_DEADLINE` (deposit-overdue) and `RESPONSE_DEADLINE` (damage-followup), matching `CANONICAL_DECISIONS.md` and `specs/16_TRIGGER_DSL.md`. The seed no longer emits values that its own `ck_prospective_triggers_type` CHECK would reject. This runbook's canonical four stand.

**R6 — Resolved.** The corpus arithmetic is settled at **18,035 total / 16,035 user-scoped** (16,000 hero decoys + 1,000 `iso-a` + 1,000 `iso-b` + 32 curated + 3 retraction fixtures; the hero user's own partition is 16,000 + 32 + 3). `EXECUTION_PLAN.md` and `PLANNING_READINESS.md` were corrected from 18,032. `db/seeds/MANIFEST.json` encodes 18,035. Any surface that renders a *user-scoped* figure must render 16,035 or, better, the value counted at query time.

**R7 — Resolved, not open.** The embedding-version spelling was reconciled in `quality/24_CONSISTENCY_REVIEW.md` (finding B1). `CANONICAL_DECISIONS.md` freezes `embedding_version = 'v1'`; the descriptor spellings previously carried by `specs/13_RETRIEVAL_SPEC.md`, `specs/15_API_SPEC.md`, `specs/11_CONTRACTS.md` and `quality/22_EVAL_DATASETS.md` were corrected to `v1`. `v1` denotes the whole embedding contract — model, dimensionality, distance function and normalisation template — so a template change is a new version, not a silent reuse. Nothing about this is still open; the runbook's `v1` assertions stand as written.

**R8 — PB-4 at Phase 0 tests a miniature, not the real objects.** The five `agent_*_v1` views do not exist until migration 0008, so the Phase 0 grant probe uses a two-object fixture. It answers the general question (does a view execute with owner privileges here?) but not the specific one (do these five views, with these joins, behave that way?). *Mitigation:* `G11.1`–`G11.2` re-assert against the real objects. *Residual risk:* if the real views behave differently — most plausibly because a view over a table the agent role cannot reach is rejected at definition rather than at query time — the discovery lands at Phase 11 instead of Phase 0, which is late for a decision that stops a phase.

**R9 — PB-6's restore path may not exist at all on CockroachDB Cloud Basic.** `BACKUP`/`RESTORE` to `userfile` is assumed available. If Basic restricts it, the probe fails and the fallback (recreate database, migrate, seed `--profile hero`) is what actually gets used. That fallback is fine for correctness and roughly 20 seconds per scenario, but it means per-scenario isolation with the **full** 18,000-row corpus is not available, and the retrieval suite stays sequential. That is exactly the `20_TDD_STRATEGY.md` R6 posture, so nothing breaks; the honest statement is that the fast path may simply not exist.

**R10 — Every command in this document is unexecuted.** Nothing here has been run, because nothing has been built. Flag surfaces change (`ccloud` most of all), output strings change between CockroachDB releases, and the exact wording of a CockroachDB permission error is a version detail. Treat every "PASS output" above as the shape to expect, not a string to match with `grep -F`. The first person to run each command should correct the expected output in place, in the same commit, and note the version they ran against.

**R11 — The contingency ladder assumes a failure is noticed.** Every rung is triggered by an operator observing something. A silent degradation — MCP returning stale rows, retrieval quietly falling back, a trigger no-op'ing with a reason nobody reads — has no rung, because nobody walks down a ladder they do not know they are standing on. *Mitigation:* the Judge Mode systems panel and the trace's degradation rendering exist to make these visible. *Residual risk:* real, and the honest answer to "how would you find out?" for several of them is "at the demo."
