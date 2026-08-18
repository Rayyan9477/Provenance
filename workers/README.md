# `workers` — deployment unit 4 of 4

Small Lambda functions for the asynchronous edges of the system.

`implementation/00_IMPLEMENTATION_MAP.md` §4.2 fixes the deployment units at
exactly four: `web`, `control-plane`, `agent-runtime`, **`workers`**.
`ARCHITECTURE.md` §25, which had no `workers/` at all, is **superseded**.

Do not add a fifth deployment unit.

## What this unit owns

| Directory | Concern |
|---|---|
| `ses_ingest/` | SES inbound notification; registers the artifact |
| `textract_complete/` | Document-analysis completion callback |
| `outbox_dispatch/` | Sweeps the transactional outbox and dispatches |
| `trigger_wakeup/` | Handles scheduler wakeups |

These are deliberately **thin handlers**. Their logic lives in the control
plane and is covered by the control plane's tests; a worker that grows a
decision is a worker that has taken a decision out of the tested path.

## Two rules that are easy to get wrong

1. **A wakeup is a hint, never a fact.** `trigger_wakeup` re-evaluates the
   predicate against current canonical state before anything fires. A trigger
   that wakes after its case has been resolved is a no-op, and that no-op is a
   demonstrated behaviour, not an accident.
2. **Delivery is at-least-once.** Duplicate processing of the same outbox event
   must be a no-op. Nothing here may rely on exactly-once delivery.

Neither worker holds a canonical write credential. Canonical writes go through
the Memory Kernel in the control plane, and nowhere else.

## Status

Not yet built. Phase 10 (`T10.1` onward) creates these handlers.
