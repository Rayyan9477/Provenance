# `control-plane` — deployment unit 2 of 4

One FastAPI container on AWS App Runner. Served as
`services.control_plane.app.main:app`.

`implementation/00_IMPLEMENTATION_MAP.md` §4.2 fixes the deployment units at
exactly four: `web`, **`control-plane`**, `agent-runtime`, `workers`.
`ARCHITECTURE.md` §25, which split this container into five services and put
the Memory Kernel in its own process, is **superseded**. Building it would have
broken the single-canonical-writer boundary, which is the property the whole
system rests on.

Do not add a fifth deployment unit.

## What this unit owns

| Package | Concern |
|---|---|
| `app/api/` | Public REST endpoints (`specs/15_API_SPEC.md`) |
| `app/auth/` | Cognito JWT to `Principal`; tenant scoping |
| `app/ingestion/` | Artifact registration and dedupe |
| `app/retrieval/` | Structured and vector retrieval (`specs/13_RETRIEVAL_SPEC.md`) |
| `app/memory_kernel/` | **The only canonical write path** (`specs/12_KERNEL_ALGORITHMS.md`) |
| `app/state_proof/` | Deterministic explanation read model |
| `app/actions/` | Intents, approval, revalidation, idempotent execution |
| `app/events/` | Transactional outbox helpers |
| `app/observability/` | Trace and correlation utilities |

The module boundaries are real even though the deployment is one container.
`tools/write_path_lint.py` (Phase 4) proves mechanically that no canonical
table is written from outside `app/memory_kernel/`.

## Invariants that live here

1. **Single canonical writer.** Only the deterministic Memory Kernel, using
   `pv_kernel_writer`, writes canonical tables. Agents never receive canonical
   write credentials.
2. **No model or network call inside a transaction callback.** Transactions are
   `SERIALIZABLE` with bounded retry on SQLSTATE `40001`; after the retry cap
   the Kernel performs **no** side effect, returns `RETRYABLE_CONCURRENCY` with
   `RETRY_EXHAUSTED_NOT_ENQUEUED`, and the caller re-drives over `503` +
   `Retry-After`. There is no kernel retry queue.
3. **Tenant scoping is never optional**, and money is never a float.
4. **No external effect from an uncommitted proposal.** An approval binds to a
   case revision and to the SHA-256 of the draft it approved, and is revalidated
   immediately before execution.

## Status

Not yet built. Phase 4 builds the Kernel, Phase 5 the read models, Phase 8 the
API and auth, Phase 9 the actions. `make run-api` reports the owning phase
until then.
