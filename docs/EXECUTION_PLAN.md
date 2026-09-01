# Provenance — Execution-Ready Delivery Plan

Status: planning complete v1.1  
Implementation status: substantial; see `STATUS.md` at the repository root, which is measured rather than declared  
Execution authorization: not granted by this document

This plan makes the design executable without beginning implementation. `quality/23_PHASE_GATES.md` owns detailed commands and evidence requirements; this document owns sequencing, work ownership, integration points, and completion boundaries.

## Delivery principle

Build the smallest truthful vertical slice from contracts and invariants outward. UI, prompts, and cloud plumbing may not define domain truth. A phase is complete only when its gate evidence is recorded; file presence or a successful demo is insufficient.

## Workstreams

| Workstream | Owns | Must not own |
|---|---|---|
| Domain/contracts | enums, transition tables, invariants, Pydantic boundary schemas | persistence, HTTP, model calls |
| Database | DDL, migrations, repositories, transaction retry, SQL roles/views | semantic decisions, model routing |
| Memory Kernel | proposal validation, deterministic decisions, atomic write plan | document parsing, external side effects |
| Retrieval | identity hints, temporal filtering, ANN, rerank, bounded context | canonical decisions or writes |
| Agents | extraction, conditional resolution, attention, grounded drafting | SQL mutation, action execution |
| API/auth | verified principals, authorization, idempotency, public/internal contracts | bypassing kernel or executor |
| Events/actions | outbox, consumers, trigger reevaluation, approval, executor | treating delivery or schedules as truth |
| Frontend | product projections, approval UX, State Proof, Judge Mode | hard-coded state or hidden fixture behavior |
| Quality/evals | fixtures, sabotage matrix, invariant tests, live evals, gate reports | changing expected fixtures merely to make code pass |
| Infrastructure | least-privilege AWS/CDK resources, deployment, observability | a second product memory store |

## Phase sequence and integration milestones

| Phase | Outcome | Integration milestone |
|---|---|---|
| 0 | Scaffold, settings, licences, live capability probes | Frozen probe report selects supported DB/vector variants without changing logical contracts. |
| 1 | Shared domain and contracts | Every downstream package imports one versioned type system. |
| 2 | Schema, migrations, deterministic seed | Fresh database contains exactly 26 tables, five agent views, and reproducible hero/isolation data. |
| 3 | DB runtime and retry | Serializable callback proves retry safety and complete rollback. |
| 4 | Memory Kernel | Hero proposal atomically creates conflict, belief version, case reopen, transition, and outbox row. |
| 5 | Deterministic read models | Dashboard, timeline, State Proof, conflict, and trace projections work without a model. |
| 6 | Embeddings and retrieval | Correct old ISP case is retrieved from an 18,035-row corpus (16,035 of them in the hero user's own partition) with zero tenant/retraction leakage. |
| 7 | LangGraph workflows | Typed ingestion and Advocate graphs run in fixtures and canonical live-model smoke tests. |
| 8 | API and authentication | Public and internal endpoints enforce principal binding, scopes, staleness, and idempotency. |
| 9 | Approval and execution | Exact draft/revision approval executes once; stale approval never calls the provider. |
| 10 | Events and prospective memory | Outbox, dedupe, DLQ, and landlord predicate reevaluation are operational. |
| 11 | MCP and database permissions | Agent reads are visible and load-bearing; base-table access is demonstrably denied. |
| 12 | Frontend and Judge Mode | Browser hero flow uses real API state and trace rows; no hard-coded identifiers. |
| 13 | Deployment | Reviewed build is reachable publicly and observable end to end. |
| 14 | Evaluation | All 51 scenarios, adversarial cases, concurrency runs, and sabotage checks meet thresholds. |
| 15 | Release | Public repository, live URL, disclosure, video, reset proof, and final gate ledger are complete. |

## Critical path

```text
contracts
  -> schema/seed
  -> transaction runtime
  -> Memory Kernel
  -> deterministic read models
  -> retrieval
  -> agent graphs
  -> API/auth
  -> actions/events/MCP
  -> frontend
  -> deployment
  -> evaluation and release proof
```

Infrastructure definitions may be prepared in parallel after Phase 1, but no deployed component may bypass the critical-path contracts.

## First coherent vertical slice

The first integration milestone is complete only when one June invoice travels through the real artifact, extraction fixture/live adapter, retrieval, typed proposal, kernel transaction, State Proof, action intent, approval, safe-sink executor, timeline, and trace paths. The following must be observed from persisted state:

- case `RESOLVED -> REOPENED`;
- revision `12 -> 13` exactly once;
- current balance value remains USD 0 while status becomes `DISPUTED`;
- one conflict and complete grounding/lineage rows exist;
- one outbox event exists for aggregate version 13;
- the draft cites valid support IDs;
- approval binds revision 13 and the exact draft hash;
- execution occurs once and its outcome returns as evidence.

## Cut and fallback policy

- Never cut contracts, schema correctness, kernel invariants, human approval, tenant isolation, or real trace backing.
- Retrieval may degrade to a disclosed brute-force per-user scan for development, but the vector-index claim remains blocked.
- Agent fixture mode may unblock deterministic development, but it must be visible and cannot satisfy the live-mode gate.
- Optional SES inbound may follow upload ingestion; both must converge on the same artifact contract.
- Optional multimodal extraction, multi-region compute, broad integrations, and professional-user features remain out of scope.

## Change freeze boundaries

- After Phase 1: schema names, enums, contracts, and model routes require a compatibility plan.
- After Phase 2: database changes are forward-only and migrations must remain compatible with the previous application image.
- After Phase 7: prompt or model changes require targeted live eval and prompt-version increment.
- After Phase 12: demo-flow changes require full browser, trace-integrity, and reset reruns.
- After Phase 13: only forward-compatible fixes may enter; every fix reruns deployed hero and alarm checks.

## Evidence and sign-off

Every phase report records:

- exact reviewed revision;
- environment and external service versions;
- commands and unedited outputs;
- mutation/sabotage result proving the test can fail;
- known debt and owner;
- rollback or forward-fix posture;
- signer distinct from the implementer where possible.

The final readiness claim requires all Phase 0–15 reports, zero invariant violations, live `fixture_mode: false`, a reset-and-rerun proof, and explicit disclosure of synthetic data and build-time AI tools.

## Stop condition

This repository is ready for execution planning handoff when all documentation consistency checks pass. It is not authorized to start implementation merely because this plan exists. Implementation begins only on an explicit user instruction after this planning baseline is accepted.
