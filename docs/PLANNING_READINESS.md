# Provenance — Planning Readiness and Traceability

Status: planning complete v1.1  
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared  
Assessed: 2026-08-17

This is the planning handoff ledger. It proves that every product objective has an owning design contract and an execution gate. It does not assert that code, cloud resources, tests, or integrations exist.

## Objective-to-proof matrix

| Objective or invariant | Authoritative design | Runtime mechanism planned | Primary verification | Delivery gate |
|---|---|---|---|---|
| Evidence is append-only | `00_PRODUCT.md` §0.1; `specs/10_DATABASE_DDL.md` | immutable artifact/evidence rows; correction, retraction, and supersession metadata rather than mutation | DB mutation-denial and lifecycle tests | G2, G4, G14 |
| Beliefs are revisable | `MEMORY_SYSTEM.md`; `specs/12_KERNEL_ALGORITHMS.md` | version chain, explicit supersession reason, current-version uniqueness | lineage golden cases and State Proof assertions | G4, G5, G12, G14 |
| State is transactional | `ARCHITECTURE.md`; `specs/12_KERNEL_ALGORITHMS.md` | single Kernel writer, serializable callback, deterministic recomputation, transactional outbox | rollback, 40001 retry, second-connection reread, aggregate revision tests | G3, G4, G10, G14 |
| Actions are permissioned | `MEMORY_SYSTEM.md`; `specs/15_API_SPEC.md` | grounded draft, human approval, revision and SHA-256 binding, stale recheck, idempotent executor | stale-approval sabotage and provider-call count | G8, G9, G14 |
| Every canonical belief is grounded | `00_PRODUCT.md` §0.2; `specs/10_DATABASE_DDL.md`; `specs/12_KERNEL_ALGORITHMS.md` | `belief_support` edge or declared deterministic derivation; tombstone on lost support | unsupported-belief rejection and deletion/retraction scenarios | G4, G5, G14 |
| Contradictions reopen durable work | `00_PRODUCT.md`; `specs/12_KERNEL_ALGORITHMS.md` | mutual-exclusion registry, conflict row, atomic case reopen and revision advance | hero conflict golden and duplicate/no-change controls | G4, G12, G14 |
| Old context is found safely | `specs/13_RETRIEVAL_SPEC.md` | identity gate, temporal filter, active-evidence filter, per-user ANN, rerank, grounding quota | 18,035-row retrieval suite (16,035 user-scoped); tenant and retraction leakage at zero | G6, G11, G14 |
| Models advise but never own truth | `ARCHITECTURE.md`; `specs/11_CONTRACTS.md`; `specs/14_PROMPTS.md` | typed proposals, Tier E/Tier R routes, schema validation, deterministic Kernel admission | fixture graphs, live smoke tests, adversarial prompt suite | G1, G7, G14 |
| Prospective memory revalidates | `specs/16_TRIGGER_DSL.md` | safe AST, current projection, generation/idempotency guard, exactly one result/reason pair | false, fulfilled, resolved, stale-generation, duplicate, and fire cases | G10, G14 |
| Agent database use is visible and bounded | `specs/13_RETRIEVAL_SPEC.md`; `specs/15_API_SPEC.md` | MCP read role limited to exactly five views; base tables denied; calls captured in trace | grant sabotage, cross-user query, and trace completeness checks | G11, G12, G14 |
| Product claims are demonstrably honest | `00_PRODUCT.md`; `implementation/05_RELIABILITY_EVAL_DEMO.md` | live/fixture banner, seed disclosure, Memory OFF/ON request diff, persisted Judge Mode | seed perturbation, trace tamper checks, reset-and-rerun evidence | G12, G13, G15 |

## Contract ownership

| Concern | Single owner | Required dependants |
|---|---|---|
| Product scope and vocabulary | `00_PRODUCT.md` | every document and UI copy |
| Cross-cutting frozen decisions | `CANONICAL_DECISIONS.md` | architecture, specs, quality, implementation handoff |
| Tables, views, roles, indexes, migration order | `specs/10_DATABASE_DDL.md` | repositories, API projections, MCP, gates |
| Enums and boundary objects | `specs/11_CONTRACTS.md` | Kernel, agents, API, events, eval fixtures |
| Canonical decisions and transitions | `specs/12_KERNEL_ALGORITHMS.md` | DB runtime, API, events, read models |
| Retrieval policy | `specs/13_RETRIEVAL_SPEC.md` | repositories, agents, MCP, evaluation |
| Prompts and model parameters | `specs/14_PROMPTS.md` | agent graphs and live-model evals |
| HTTP, events, auth, idempotency, traces | `specs/15_API_SPEC.md` | services, consumers, frontend, observability |
| Trigger language and lifecycle | `specs/16_TRIGGER_DSL.md` | evaluator, scheduler, Kernel proposal, UI |
| Test method and gate evidence | `quality/20_TDD_STRATEGY.md`, `quality/23_PHASE_GATES.md` | all execution phases |
| Evaluation corpus and thresholds | `quality/22_EVAL_DATASETS.md` | model, Kernel, retrieval, end-to-end gates |

No implementation-oriented summary may introduce a table, enum, model route, endpoint, or state transition that is absent from its owner.

## Planning closure ledger

| Area | Planning state | Evidence of closure |
|---|---|---|
| Intent, user, scope, exclusions | closed | product thesis, wedge, hero story, explicit non-goals |
| Naming and vocabulary | frozen | Provenance, `provenance_*`, `pv_*`, grounding versus lineage |
| Logical and deployment architecture | closed | services, trust boundaries, ownership, topology, failure posture |
| Data and migrations | closed | 26-table order, constraints, indexes, five views, role grants, compatibility gate |
| Domain and boundary contracts | closed | closed enums, typed proposals/results/events, validators, schema versioning |
| Kernel behavior | closed | decision pipeline, reason ordering, transaction algorithm, retry/no-op semantics |
| Retrieval | closed | stages, weights, filters, lifecycle eligibility, audit and latency budgets |
| Models and prompts | frozen for v1 | Haiku 4.5 Tier E, Opus 5 Tier R, exact prompts, fallbacks, version rules |
| API, auth, events, traces | closed | principals, endpoints, idempotency, event registry, trace integrity |
| Triggers and time | closed | AST, projection registry, results/reasons, re-arm, business-day convention |
| Evaluation and delivery | closed | 51 scenarios, thresholds, 16 phase gates, sign-off and rollback evidence |

## Execution-time proofs that remain intentionally open

These are not design questions. Phase 0 already fixes the success path, fallback, and blocking consequence for each:

- actual Bedrock access to the frozen model IDs and structured-output/tool behavior;
- CockroachDB Cloud vector-index syntax, generated-column support, view/grant behavior, and clone/restore API;
- measured latency, cost, retry contention, prompt-cache economics, and managed-service quotas;
- AWS IAM, Scheduler, EventBridge, AgentCore, SES, and public deployment behavior in the selected account;
- live evaluation results and browser evidence.

No prose change may convert an unverified external capability into a claimed fact. A failed probe follows `CANONICAL_DECISIONS.md` and Gate 0; it does not permit an undocumented redesign.

## Documentation-only validation record

The 2026-08-17 reconciliation pass performed no build, test, install, migration, cloud, or runtime operation. Static checks over the planning pack recorded:

| Check | Result |
|---|---|
| Markdown inventory carries the non-execution status | 25 of 25 files |
| Canonical DDL tables, excluding five disposable Phase 0 probe tables | 26 |
| Canonical `_v1` agent-safe views | 5 |
| Contract enums compared with their persisted DDL `CHECK` vocabularies | 31 pairs checked; 0 mismatches |
| Unbalanced fenced-code blocks | 0 files |
| Unicode replacement characters | 0 |
| Unresolved work or pending-design markers | 0 |
| Deprecated package, SQL-role, view, index, event, artifact-source, action, and trigger aliases | 0 outside explicit naming/model-history text |

These checks establish internal planning consistency only. They do not validate syntax against a live interpreter/database, prove vendor behavior, or satisfy any execution gate.

## Authorization boundary

Planning is complete and ready for an implementation handoff. Execution has not begun. Do not create application packages, migrations, infrastructure, fixtures, tests, cloud resources, or deployment state until the user explicitly authorizes implementation.
