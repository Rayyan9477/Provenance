# Platform migration — Bedrock and AWS to Gemini and Cloud Run

Written 2026-08-19 as a point-in-time assessment of moving this build off Amazon
Bedrock and AWS. **Read the status block below before anything else in this
file:** four of its recommendations have since been decided or superseded, and
one of them was wrong.

> ### Status as of 2026-08-24
>
> **Decided, and binding:**
>
> 1. **The AWS deployment is discarded.** `infra/cdk/` (10 stacks, 7,861
>    lines, 304 tests) is dead weight rather than dual-use.
> 2. **The database does not move.** CockroachDB Cloud stays exactly where it
>    is, on AWS `us-east-1`. §4's recommendation to stand up a new cluster on
>    GCP is therefore **not** being followed — the eight migrations, the seed and
>    ~390 tests are worth more than co-location, and Cloud Run in `us-east4` is
>    the same physical metro anyway.
> 3. **Model access is an AI Studio API key** — the Gemini Developer API. No
>    Vertex, no service account, no IAM.
>
> **§5's embedding recommendation is superseded.** It recommends
> `gemini-embedding-001` at 1536. The shipped choice is **`gemini-embedding-2`**,
> because `001` requires the *caller* to normalize any width other than 3072
> while `-2` auto-normalizes truncated widths. This stack ranks by cosine, and a
> missed normalization is silent — the distances stay numbers, stay ordered, and
> stop meaning anything. `-2` also raises the input ceiling from 2,048 to 8,192
> tokens and is multimodal.
>
> **The canonical record is now `docs/CANONICAL_DECISIONS.md` → *Gemini model id
> canon*.** Where this file and that section disagree, that section wins.
>
> **Nothing below had been probed when this was written.** Every Gemini model id
> in this repository was transcribed from documentation and none had been
> invoked; `ops/probes/gemini_probe.py` reported
> `CANNOT RUN — GOOGLE_API_KEY is not set`.
>
> > **Superseded 2026-08-24.** The probe ran. The transcript is committed at
> > `ops/gemini-probe.txt` — PASS 11, FAIL 0, CANNOT RUN 0, exit 0 — so the ids
> > are measured rather than transcribed. §6 is the reason it was run first, and
> > it stands as written.

Every measurement here was taken from the tree on the day it was written.
Nothing is estimated.

---

## 1. What the target platform commits to

Three commitments, and everything downstream follows from them:

1. **Gemini 3.5 or newer**, reached through the Gemini Developer API rather than
   through Vertex AI.
2. **The Google Gen AI SDK** (`google-genai`) as the agent runtime's client.
3. **Google Cloud for compute** — Cloud Run.

Nothing in that list touches the decision core. The part of this build that cost
the most — system design, state management, the security boundary, failure
handling — is the part the migration must not damage, and §3 measures how much
of it is coupled to a cloud at all.

---

## 2. Scope and non-goals

The product thesis is unchanged by the migration and is deliberately
vendor-neutral: institutions keep durable structured records about people;
people keep nothing comparable about institutions.

**In scope, and already built:**

- **A single canonical writer.** Capability-typed principals that cannot be
  constructed from request data, five `agent_*_v1` views, `pv_agent_reader`
  holding **zero** table grants, and one canonical write path proved by
  `write_path_lint` (17 canonical write statements in the Kernel, named in
  `transaction.CANONICAL_WRITE_STATEMENTS`), with 63 adversarial cross-tenant
  tests.
- **A human gate that is part of the model rather than a wrapper on it.** The
  `NEEDS_HUMAN` gate and the H1–H8 disposition ladder; the hero case resolves on
  H5 rather than on model confidence.
- **A multi-step pipeline as the unit of work** —
  ingest → claim → belief → conflict → commitment → action.
- **Verification as a shipped artifact** — 118 machine-checkable gate
  assertions, sabotage testing where a green run is a failure, and a defect
  ledger with counterfactual close-proofs.

**Not in scope, and worth naming because the components look adjacent:**

- **An institutional agent fleet.** This is one consumer's record. An agent
  registry for cross-department discovery, agent identity, an agent gateway and
  reasoning-chain observability are a different product. Reading the
  capability-typed principals and the zero-grant reader role as that product is
  an analogy rather than a fact: they are excellent database security, and they
  are not a gateway.
- **Autonomous outbound action.** Every action passes through a recorded,
  human-approved intent, and that is a boundary rather than a milestone still to
  be crossed.

---

## 3. What survives, what changes, what is discarded

### Measured coupling

| | |
|---|---|
| Files importing `boto3`/`botocore` at module level | **0** |
| Real AWS SDK usage outside `infra/` | **one lazy import**, `scripts/seed/embeddings.py:196` |
| Files whose AWS coupling is only a model-id string | 20 |
| `infra/` files that are AWS-specific | 30 of 36 |

The codebase was built against interfaces rather than against AWS. There is a
`TextEmbedder` **Protocol** in `app/retrieval/embeddings.py` with
`BedrockTitanEmbedder` as one implementation — so the model swap is a second
implementation of an existing interface.

### Survives unchanged (~70% of the build)

- `provenance_domain` — 93 tests, pure Python, zero cloud coupling
- `provenance_contracts` — 243 tests, pydantic only
- `provenance_db` — psycopg; CockroachDB is Postgres-wire
- **Memory Kernel** — 534 tests, the whole decision core and the transaction
- **Migrations 0001–0008** — 26 tables, 5 agent views, all grants
- **Seed** — 18,035 rows, idempotent, manifest-checked
- **API + auth** — 291 tests including the adversarial lane
- **Frontend** — 14 screens, 65 tests, render-honesty enforced
- All gate tooling: `write_path_lint`, `txn_purity_lint`, `invariant_map_check`,
  `sabotage_guard`, `defect_lint`, `scrub`, `gate.sh`

### Changes

| Layer | From | To | Cost |
|---|---|---|---|
| Reasoning model | `us.anthropic.claude-opus-4-6-v1` (Bedrock) | Gemini 3.5+ | config + a probe |
| Extraction model | `us.anthropic.claude-haiku-4-5` | Gemini Flash tier | config + a probe |
| Embeddings | `amazon.titan-embed-text-v2:0`, 1024 dims | `gemini-embedding-001` | **see §5 — the expensive one** |
| Agent framework | LangGraph on Bedrock AgentCore | **Google ADK** | Phase 7 was never built — see below |
| Compute | App Runner | **Cloud Run** | container already needed |
| Frontend hosting | Amplify | Cloud Run or Firebase Hosting | |
| Auth | Cognito | Identity Platform / Firebase Auth | JWT verification is already abstracted |
| Async | EventBridge + SQS | **Pub/Sub** | the outbox is transport-agnostic by design |
| Database | CockroachDB on AWS | **CockroachDB on GCP** | see §4 |

**Phase 7 was never started, and that is now an advantage.** There is no
LangGraph code to port. The agent layer gets written once, natively, against a
Memory Kernel that already refuses to let an agent write canonical state.

### Discarded

`infra/cdk/` — 10 stacks, 170 resources, 7,861 lines, **304 tests**. It is
AWS-specific and does not port. The *reasoning* inside it survives — least
privilege, `aws:SourceAccount` confused-deputy conditions, IMMUTABLE image tags,
24 alarms with `treatMissingData` set so a quiet system reports `OK` — and should
be carried across to the Google Cloud equivalent rather than rediscovered.

---

## 4. CockroachDB can stay, and probably should

CockroachDB Cloud runs on GCP in 28+ regions with vector indexing across cloud
offerings. §1 needs compute on Google Cloud and nothing more; Cloud Run
satisfies that on its own.

Keeping it preserves 8 migrations, 26 tables, 5 agent views, the seed, and
roughly 390 database and retrieval tests — the single largest block of verified
work in the repository. Moving to Cloud SQL or AlloyDB would mean re-doing the
vector index strategy, and `CREATE VECTOR INDEX` has no exact equivalent
(pgvector/ScaNN differ in both syntax and tuning).

**Recommendation: create a new CockroachDB Cloud cluster on GCP, in the same
region as Cloud Run, and re-run the migrations there.**

---

## 5. The expensive item: embedding dimensions

`gemini-embedding-001` defaults to **3072** dimensions and supports truncation to
Google's recommended **768 / 1536 / 3072** via `output_dimensionality`. The
schema is `VECTOR(1024)`, frozen in migration `0002` and in
`CANONICAL_DECISIONS.md`.

So the migration requires:

1. A migration `0009` altering `evidence_items.embedding` to the new width.
2. **Re-embedding all 18,035 texts** with Gemini — real spend, and the Titan run
   took ~50 minutes at 24 workers.
3. **A full ANN index rebuild — measured at 52–55 minutes on this cluster**,
   three times. The runbook's "one to two minutes" is wrong by ~30×.
4. `db/seeds/vectors.parquet` (67 MB) is regenerated; its content hash in
   `MANIFEST.json` changes.

Budget **two hours minimum** for that sequence, and do not run two seeds
concurrently: CockroachDB serialises schema changes, so a second seed's
`DROP INDEX` queues behind the first's `CREATE` and fires the instant it
succeeds. That destroyed one complete build already.

**Recommendation: 1536.** It is on Google's recommended list, halves the storage
and index-build cost against 3072, and MRL truncation means little quality loss.

---

## 6. Probe before freezing anything

The single most expensive lesson from the last build: **`list-foundation-models`
returns ids that are not invocable.** Bedrock's frozen model ids were all wrong —
Anthropic chat models needed `us.` inference-profile prefixes, every other
provider needed bare ids, and Opus 5 was denied outright. That cost a full
re-probe and a canon rewrite.

Before writing a line of Gemini integration, run the equivalent of PB-5: invoke
each candidate model and record the transcript. Do not freeze a model id from
documentation. The same applies to `output_dimensionality` — confirm the value
the API actually returns rather than the one the docs recommend.

---

## 7. How the pending items fold in

From `STATUS.md`, and the migration changes their priority:

**Now cheaper or moot**
- Phase 13 (deploy) — the AWS CDK is discarded; Cloud Run is a far smaller
  surface than App Runner + Amplify + AgentCore + Cognito + SES + EventBridge.
  The `provenance-teardown-role` deploy blocker disappears with it.
- Phase 7 — no port needed; built once against the Google SDK.

**Unchanged and still required**
- **G4.7 concurrency test** — still missing, and it is the assertion that would
  have caught the dead `23505` constraint map.
- `ann_search()` still raises `NotImplementedError`, so layer 3's retraction
  filter has no production call site.
- Seed step 9 — the curated `MemoryProposal` fixtures are still unauthored, so
  twelve tables remain empty. `SEED_PROFILE` flips to `all` in the same commit
  that lands the kernel replay.
- Phase 5 is one task of five; Phase 9, 10, 11 (MCP), 14, 15 unstarted.
- 10 open MAJOR defects against phase 0; `ops/` still untracked.

**New, created by the migration**
- A Gemini model and embedding probe, with a committed transcript.
- Migration `0009` for the embedding width, plus a re-seed.
- The agent layer (Phase 7), written natively against the Google SDK.
- Google Cloud infrastructure, replacing `infra/cdk/`.
- **An architecture diagram** — none exists.
- **README spin-up instructions** — currently absent.

---

## 8. Recommended sequence

Ordered by what unblocks the most:

1. **Probe the models.** Model ids, `output_dimensionality`, quota. Commit the
   transcript. Half a day, and it prevents the mistake that cost days last time.
2. **Stand up CockroachDB on GCP** and run migrations `0001`–`0008` against it.
3. **Migration `0009`** for the embedding width; re-embed and re-seed. Start this
   early — it is two hours of wall clock and mostly unattended.
4. **A `GeminiEmbedder`** behind the existing `TextEmbedder` Protocol.
5. **The agent layer** — the ingestion and advocate graphs, emitting typed
   `MemoryProposal`s only. The Kernel boundary is already enforced.
6. **Cloud Run** for the control plane and the frontend; Pub/Sub for the outbox.
7. **Architecture diagram, README spin-up, walkthrough recording.** All three are
   at zero. Do not leave them to the end.

Items 1–4 are largely mechanical because of the Protocol seam. Item 5 is the real
new engineering. Items 6–7 are where production readiness is either demonstrated
or is not.

---

## 9. Decisions needed

1. **Does `infra/cdk/` stay in the tree?** If nothing will ever be deployed from
   it, it can be deleted rather than maintained.
   > **Decided 2026-08-31: it stays, committed and attributed.** Its 304 tests
   > still run and the reasoning inside it is worth reading, but nothing is
   > deployed from it and no AWS account is reachable from the running system.
   > `NOTICE` says so, under its own heading.
2. **Embedding width** — 1536 recommended.
3. **Cluster region** — Cloud Run and CockroachDB should be co-located.
   > **Superseded by status decision 2 above.** The cluster does not move;
   > `us-east4` and AWS `us-east-1` are the same Northern Virginia metro, so the
   > cross-cloud hop stays in single-digit milliseconds.
