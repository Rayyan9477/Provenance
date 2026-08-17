# Provenance — Documentation Index and Authority

Status: planning complete v1.1  
Implementation status: not started  
Last reconciled: 2026-08-17

This repository is the implementation-ready design pack for **Provenance**, formerly called NeverReset. It contains product, architecture, contract, algorithm, quality, and delivery specifications. It does not contain application code and does not claim that any runtime behavior has been implemented or validated.

## Read order

1. `00_PRODUCT.md` — product intent, vocabulary, invariants, scope, and demo story.
2. `CANONICAL_DECISIONS.md` — cross-document precedence and every frozen cross-cutting choice.
3. `ARCHITECTURE.md` — system context, logical services, trust boundaries, and deployment topology.
4. `MEMORY_SYSTEM.md` — domain model, state machines, retrieval semantics, and action safety.
5. `specs/10_DATABASE_DDL.md` — executable database contract.
6. `specs/11_CONTRACTS.md` — executable Python type contract.
7. `specs/12_KERNEL_ALGORITHMS.md` — deterministic canonical-write algorithms.
8. `specs/13_RETRIEVAL_SPEC.md` — deterministic hybrid retrieval pipeline.
9. `specs/14_PROMPTS.md` — exact model prompts and routing policy.
10. `specs/15_API_SPEC.md` — HTTP, event, idempotency, and trace contracts.
11. `specs/16_TRIGGER_DSL.md` — prospective-memory predicate language and lifecycle.
12. `quality/20_TDD_STRATEGY.md` — test-first implementation contract.
13. `quality/21_OBSERVABILITY_ANALYTICS.md` — correlation ids, span map, metric catalogue, redaction, trace integrity.
14. `quality/22_EVAL_DATASETS.md` — 51-scenario evaluation corpus.
15. `quality/23_PHASE_GATES.md` — machine-checkable build and submission gates.
16. `quality/24_CONSISTENCY_REVIEW.md` — adversarial cross-document review, its findings, and their disposition.
17. `frontend/30_UX_SPEC.md` — functional UX: seven screens, states, data bindings, accessibility.
18. `frontend/31_DESIGN_BRIEF_FOR_OPUS5.md` — the commissioning brief for the visual design, written to be pasted into a separate session.
19. `frontend/32_JUDGE_MODE.md` — Judge Mode panels, Memory Trace DAG, MCP visibility, counterfactual.
20. `ops/40_INFRA_IAC.md` — AWS CDK stacks, Cognito, S3, SES, EventBridge, App Runner, ccloud, SQL roles, MCP wiring.
21. `ops/41_RUNBOOK.md` — zero-to-running, Phase 0 capability probes, seeding order, failure playbooks, demo operations.
22. `submission/50_README_DRAFT.md` — the public repository README.
23. `submission/51_VIDEO_SCRIPT.md` — shot-by-shot script for the sub-3-minute demo video.
24. `EXECUTION_PLAN.md` — delivery sequence, ownership, dependencies, and stop conditions.
25. `PLANNING_READINESS.md` — objective-to-contract traceability and the final documentation-only readiness ledger.
26. `implementation/` — concise implementation-oriented restatements and coding-agent handoff.

## Authority rules

- `00_PRODUCT.md` owns product intent, scope, vocabulary, the four invariants, and the hero-scenario dates.
- `CANONICAL_DECISIONS.md` owns cross-document names and decisions, and outranks every other document.
- `specs/10_DATABASE_DDL.md` §17 owns the seeded dataset: the hero user, the counterparties, and every name that may legitimately appear in an example. Examples must be drawn from it, never invented.
- Within a technical concern, the numbered specification that owns it is authoritative.
- Quality documents may strengthen acceptance criteria but may not change product or runtime semantics.
- Frontend documents own presentation and may not introduce a field, endpoint, or state absent from `specs/15_API_SPEC.md`.
- Files under `implementation/` explain the authoritative specifications; they do not override them.
- A conflict is resolved by the authority order above. It must not be deferred to implementation preference.

## Planning completion definition

Planning is complete when:

- every cross-cutting identifier has one spelling;
- every component has an owner and explicit forbidden capabilities;
- every state-changing path names its transaction and idempotency boundary;
- every external side effect names its approval and staleness checks;
- every phase has entry criteria, deliverables, executable exit assertions, and rollback posture;
- managed-service uncertainty is converted into a Phase 0 probe with a predetermined fallback;
- no unresolved design choice can change a contract after Phase 1 begins.

This baseline satisfies those conditions. Cloud availability, latency, vector-index syntax, IAM behavior, and model access remain execution-time verification items because documentation cannot prove external service state.

## Change control

Any future change to a frozen decision must update, in one documentation change:

1. `CANONICAL_DECISIONS.md` with rationale and migration impact;
2. the owning numbered specification;
3. dependent contracts, examples, gates, and evaluation fixtures;
4. the compatibility or migration plan.

Do not begin implementation from an older standalone document without reading the decision register.
