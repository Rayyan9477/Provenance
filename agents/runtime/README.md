# `agent-runtime` — deployment unit 3 of 4

The LangGraph agent package on Bedrock AgentCore Runtime.

`implementation/00_IMPLEMENTATION_MAP.md` §4.2 fixes the deployment units at
exactly four: `web`, `control-plane`, **`agent-runtime`**, `workers`.
`ARCHITECTURE.md` §25, which specified three separate agent services, is
**superseded**. There is one agent runtime holding two graphs.

Do not add a fifth deployment unit.

## What this unit owns

| Directory | Concern |
|---|---|
| `graphs/` | `ingestion_graph.py`, `advocate_graph.py` |
| `nodes/` | Individual graph nodes, each testable against a cassette |
| `prompts/` | Byte-exact prompt assets and `render.py` (`specs/14_PROMPTS.md`) |
| `schemas/` | Structured-output schemas and their validators |
| `tools/` | The read-only tool surface, including MCP client wiring |
| `model_router/` | Bedrock routing, schema repair, fallback policy |

## The boundary this unit exists inside

Agents **propose**; they never write. Their database identity is
`pv_agent_reader`, which holds `SELECT` on the five `agent_*_v1` views and is
refused the base tables — a boundary confirmed live in Phase 0, not assumed.
`.importlinter` forbids anything under `agents` from importing
`services.control_plane.app.memory_kernel`, so the rule is a property of the
import graph rather than a habit.

## Model routing

Both tier ids are read from configuration, never hard-coded:

- `BEDROCK_EXTRACTION_MODEL_ID` — Tier E, extraction and classification
- `BEDROCK_REASONING_MODEL_ID` — Tier R, semantic resolution, contradiction
  characterization, attention assessment, advocacy drafting

Anthropic chat models on Bedrock are invoked by **inference-profile id**, never
by bare model id; a bare id returns `ValidationException`. Embeddings
(`amazon.titan-embed-text-v2:0`, 1024 dimensions) are the exception and use the
bare id. `agent_runs.model_route` records the id that actually served each run,
so the README can state the model it really used.

Tier R has no downgrade path: a failure persists a pending-human-review result
rather than quietly answering with a weaker model.

## Status

Not yet built. Phase 7 (`T7.1` onward) creates the graphs; Phase 11 adds MCP;
Phase 13 deploys to AgentCore.
