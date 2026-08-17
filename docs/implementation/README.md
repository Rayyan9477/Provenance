# Provenance Implementation Architecture Pack

Status: planning complete v1.1  
Implementation status: not started

This directory is the implementation-grade expansion of the original `ARCHITECTURE.md` and `MEMORY_SYSTEM.md`.

Read in order:

1. [00_IMPLEMENTATION_MAP.md](./00_IMPLEMENTATION_MAP.md)
2. [01_SYSTEM_ARCHITECTURE_DETAILED.md](./01_SYSTEM_ARCHITECTURE_DETAILED.md)
3. [02_DATA_MEMORY_TRANSACTIONS.md](./02_DATA_MEMORY_TRANSACTIONS.md)
4. [03_AGENTS_LANGGRAPH_CONTRACTS.md](./03_AGENTS_LANGGRAPH_CONTRACTS.md)
5. [04_API_EVENTS_SECURITY.md](./04_API_EVENTS_SECURITY.md)
6. [05_RELIABILITY_EVAL_DEMO.md](./05_RELIABILITY_EVAL_DEMO.md)
7. [06_CODING_AGENT_HANDOFF.md](./06_CODING_AGENT_HANDOFF.md)

## Frozen stack

- Next.js + TypeScript frontend
- Python/FastAPI control-plane
- LangGraph on Amazon Bedrock AgentCore Runtime
- `anthropic.claude-opus-5` Tier R reasoning and advocacy
- `anthropic.claude-haiku-4-5` Tier E extraction and classification
- one Tier E repair/fallback budget; Tier R failures persist for human review
- Titan Text Embeddings V2 / 1024 dimensions
- CockroachDB Cloud as canonical memory + vector store
- CockroachDB MCP for governed agent memory reads
- Cognito, S3, SES, Textract, EventBridge/Scheduler, Lambda, SQS, CloudWatch

## Architectural north star

> Evidence is append-only. Beliefs are revisable. State is transactional. Actions are permissioned.

## Coding-agent north star

> LLMs propose semantic changes. Only the deterministic Memory Kernel commits canonical relationship state.
