# Provenance — Memory Kernel Algorithms

Purpose: specify every deterministic algorithm the Memory Kernel executes, precisely enough that a competent engineer can implement it and unit-test it with zero calls to Bedrock or any other model.

Status: planning-complete baseline v1.1
Implementation status: not started

Audience: backend engineers implementing `services/control_plane/app/memory_kernel/` and `packages/python/provenance_domain/`, coding agents generating that code, and judges auditing whether the "deterministic kernel" claim is real.

---

## 0. Scope, placement, and the one rule that governs this document

### 0.1 What the Kernel is

The Memory Kernel is the **only canonical writer** in Provenance. LLM agents produce typed `MemoryProposal` objects. The Kernel decides what — if anything — becomes canonical state, and it decides using code, not prompts.

Architectural north star, restated because every algorithm below serves it:

> Evidence is append-only. Beliefs are revisable. State is transactional. Actions are permissioned.

### 0.2 The governing rule of this document

> **If a decision in this document requires a model to run, the decision is specified wrong.**

Every function below is a pure function of (database rows, proposal payload, frozen config). Its unit test is a fixture in, a `ChangePlan` out. `pytest` must pass with no network access and no AWS credentials.

The model's *only* influence on kernel decisions is through values it wrote into a `MemoryProposal`, and every such value is either (a) re-validated against database rows, or (b) advisory metadata that cannot alter canonical state. Section 2.9 enumerates exactly which proposal fields are advisory.

### 0.3 Vocabulary (do not collapse these three)

| Term | Meaning in this document |
|---|---|
| **Provenance** | The product. Never used as a common noun here. |
| **grounding** | The `belief_support` edges linking a `belief_versions` row to evidence, claims, other belief versions, or a named derivation. Relations: `SUPPORTS`, `CONTRADICTS`, `QUALIFIES`. |
| **lineage** | The `belief_versions` chain for one belief (v1 superseded by v2 …), plus the `reason_code` recorded for each supersession. |

State Proof renders both. The table name `belief_support` is unchanged.

### 0.4 Code placement

```text
packages/python/provenance_domain/kernel/
├── config.py            # KernelConfig — every threshold in this document
├── propositions.py      # normalization + entailment (§2.2, §2.3)
├── families.py          # predicate family registry + matcher table (§2.5)
├── contradiction.py     # match(), overlap(), ConflictFinding (§2.6)
├── disposition.py       # auto-resolve vs human review (§3)
├── money.py             # commitment recompute (§4)
├── case_machine.py      # transition matrix + reopen test (§5)
├── revision.py          # ChangePlan dirtiness + revision rule (§6)
├── temporal.py          # bitemporal rules T1–T4 (§8)
├── reasons.py           # ReasonCode enum (§9.3)
└── result.py            # KernelCommitResult assembly (§9)

packages/python/provenance_db/
└── retry.py             # serialization retry contract (§7)

services/control_plane/app/memory_kernel/
├── pipeline.py          # the 30 steps (§1)
└── repositories.py      # all SQL; no SQL anywhere else in the kernel
```

`provenance_domain.kernel` **must not import** `provenance_db`, `boto3`, `httpx`, or any Bedrock client. Enforce with an import-linter contract in CI. That single lint rule is what makes "testable without an LLM" structurally true rather than aspirational.

### 0.5 Frozen configuration (v1 defaults)

```python
# provenance_domain/kernel/config.py
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class KernelConfig:
    # --- §2 contradiction detection ---
    material_overlap_min_seconds: int = 86_400          # 24h
    payment_match_window_days: int = 3
    instant_widen_days: int = 1
    amount_abs_tolerance: Decimal = Decimal("0.01")
    amount_rel_tolerance: Decimal = Decimal("0.005")    # 0.5%

    # --- §3 disposition ---
    entailment_penalty: Decimal = Decimal("0.30")
    auto_resolve_margin: Decimal = Decimal("0.25")
    auto_resolve_floor: Decimal = Decimal("0.80")
    high_authority_floor: Decimal = Decimal("0.80")
    confirmed_status_floor: Decimal = Decimal("0.90")
    dispute_decay: Decimal = Decimal("0.40")
    action_confidence_floor: Decimal = Decimal("0.60")
    human_review_amount_threshold: Decimal = Decimal("100.00")   # in commitment currency
    critical_amount_threshold: Decimal = Decimal("1000.00")
    unknown_source_class_authority: Decimal = Decimal("0.10")

    # --- §4 money ---
    overpay_tolerance: Decimal = Decimal("0.00")
    commitment_grace_seconds: int = 0

    # --- §5 case machine ---
    max_reopens: int = 5

    # --- §7 retry ---
    max_tx_attempts: int = 5
    retry_base_delay_ms: int = 50
    retry_max_delay_ms: int = 2_000

    # --- §8 temporal ---
    future_validity_horizon_days: int = 3_650           # 10 years
    supersession_authority_floor: Decimal = Decimal("0.80")
```

Thresholds live here. They never live in prompt text. Changing a threshold is a code change with a test diff, which is the point.

---

## 1. The 30-step decision pipeline

### 1.1 Two phases, and why the split matters

The pipeline in `02_DATA_MEMORY_TRANSACTIONS.md` §8 is authoritative and is reproduced verbatim below. What that document does not say — and what kills builds — is that **steps 11–16 must run twice**.

- **PHASE A — PREFLIGHT (outside the write transaction).** Reads may hit the database in a separate read-only transaction. Everything computed here is *advisory*: it exists for early rejection, for cheap telemetry, and to decide whether a write transaction is worth opening at all. It is **never** relied on for correctness.
- **PHASE B — COMMIT (inside one `SERIALIZABLE` transaction).** The deterministic core re-executes against rows read *inside* the transaction, and every guard PHASE A established is re-checked. Then, and only then, writes happen.

The reason is §7: a `40001` retry restarts the callback from fresh reads. If the `ChangePlan` were computed once in PHASE A and merely replayed on retry, a retry would write a plan derived from a stale snapshot — precisely the "impossible partial aggregate state" invariant 3 forbids.

**Absolute rule: no network call, no model call, no S3 read, no EventBridge publish, no `sleep`, and no wall-clock read inside PHASE B.** The only clock inside PHASE B is `tx_now`, captured once (§1.4).

### 1.2 The 30 steps, annotated

| # | Step | Runs in | Deterministic output | Failure mode → decision |
|---|---|---|---|---|
| 0 | receive proposal | PREFLIGHT | `MemoryProposal` (Pydantic) | transport error → 400, no kernel row |
| 1 | validate schema/version | PREFLIGHT | validated model | `SCHEMA_VERSION_UNSUPPORTED` / `SCHEMA_FIELD_MISSING` / `SCHEMA_TYPE_INVALID` → `REJECTED_SCHEMA` |
| 2 | derive tenant from authenticated internal principal | PREFLIGHT | `tenant_id` | missing/invalid M2M token → 401 before the kernel is entered |
| 3 | validate proposal user == principal user | PREFLIGHT | — | `PRINCIPAL_USER_MISMATCH` / `TENANT_MISMATCH` → `REJECTED_INVALID_PROVENANCE` |
| 4 | load referenced artifact/evidence rows | **BOTH** | `dict[UUID, EvidenceRow]`, `dict[UUID, ArtifactRow]` | `EVIDENCE_NOT_FOUND` / `ARTIFACT_NOT_FOUND` → `REJECTED_INVALID_PROVENANCE` |
| 5 | reject any foreign/missing provenance | **BOTH** | — | `EVIDENCE_FOREIGN_USER` / `ARTIFACT_FOREIGN_USER` / `EVIDENCE_ARTIFACT_MISMATCH` / `SOURCE_RETRACTED_EXCLUDED` → `REJECTED_INVALID_PROVENANCE` |
| 6 | check artifact/proposal dedupe | **BOTH** | `is_duplicate: bool` | `PROPOSAL_ALREADY_DECIDED` / `ARTIFACT_CONTENT_DUPLICATE` → `NOOP_DUPLICATE` (returns the stored result) |
| 7 | resolve/validate relationship and case identity | **BOTH** | `case_id`, `relationship_id` | `IDENTITY_UNRESOLVED` / `IDENTITY_AMBIGUOUS_MULTI_CASE` / `IDENTITY_CONFIDENCE_BELOW_FLOOR` → `PENDING_IDENTITY` |
| 8 | load current case revision + relevant canonical state | **BOTH** | `AggregateSnapshot` | `CASE_TERMINAL_SUPERSEDED` → `REJECTED_INVARIANT` |
| 9 | validate temporal applicability | **BOTH** | normalized validity intervals | `VALIDITY_INVERTED` / `VALIDITY_FUTURE_BEYOND_HORIZON` → `REJECTED_SCHEMA` |
| 10 | materialize proposed claims | **BOTH** | `list[Proposition]` incl. entailments (§2.3) | `CLAIM_EVIDENCE_UNLINKED` → `REJECTED_INVALID_PROVENANCE` |
| 11 | compare against current beliefs/commitments | **BOTH** | `list[ConflictFinding]` (§2.6) | none — pure |
| 12 | create conflict plan if mutually exclusive | **BOTH** | `list[ConflictPlanItem]` + dispositions (§3) | none — pure |
| 13 | compute deterministic fulfillment/amount/status changes | **BOTH** | `list[CommitmentDelta]` (§4) | `CONFLICT_CURRENCY_MISMATCH` → conflict, not rejection |
| 14 | evaluate state-machine transition legality | **BOTH** | `CaseTransition \| None` (§5) | `CASE_TRANSITION_ILLEGAL` / `CASE_TRANSITION_MULTIPLE_IN_COMMIT` → `REJECTED_INVARIANT` |
| 15 | evaluate trigger changes | **BOTH** | `list[TriggerDelta]` | illegal trigger AST → `REJECTED_SCHEMA` |
| 16 | evaluate hard invariants | **BOTH** | `ChangePlan` (frozen) | `INVARIANT_*` → `REJECTED_INVARIANT` |
| 17 | begin/continue serializable transaction | **TX boundary** | `tx_now` captured | — |
| 18 | write claims/evidence links | TX | `claims` rows | `23505` on dedupe constraint → §7.5 mapping |
| 19 | write new belief versions + support edges | TX | `belief_versions` + `belief_support` | `INVARIANT_BELIEF_UNGROUNDED` → abort |
| 20 | update conflicts | TX | `conflicts` rows | — |
| 21 | update commitments/fulfillments | TX | `commitments`, `fulfillments` | `CHECK` violation → abort, `REJECTED_INVARIANT` |
| 22 | update case state/revision | TX | `cases` row, `revision += 1` | 0 rows updated → `OPTIMISTIC_REVISION_MISMATCH` → retry |
| 23 | append state transitions | TX | `state_transitions` rows | — |
| 24 | write trigger mutations | TX | `prospective_triggers` rows | — |
| 25 | write `kernel_decision` | TX | `kernel_decisions` row | — |
| 26 | update proposal status | TX | `memory_proposals.status` | — |
| 27 | write outbox event(s) | TX | `outbox_events` rows | — |
| 28 | COMMIT | TX end | — | `40001` → step 29 |
| 29 | on SQLSTATE 40001: rollback / backoff / retry from fresh reads | OUTSIDE | attempt counter | cap exceeded → `RETRYABLE_CONCURRENCY` |
| 30 | return `KernelCommitResult` | OUTSIDE | result object (§9) | — |

Steps 4–16 marked **BOTH** are the re-executed core. Steps marked PREFLIGHT-only are pure request validation and cannot change under concurrency.

### 1.3 Forbidden inside PHASE B (steps 17–28)

Any of the following inside the transaction is a build defect, and CI must fail on it:

- `bedrock_runtime.invoke_model` / `converse` (Tier R or Tier E)
- `bedrock_runtime` Titan embedding calls (`amazon.titan-embed-text-v2:0`)
- `s3.get_object` / `put_object`
- `events.put_events` (EventBridge) — this is exactly what the outbox exists to avoid
- `ses.send_email`
- any MCP tool call, including the CockroachDB Cloud Managed MCP Server
- `time.sleep`, `datetime.now()`, `random` without a seeded generator
- HTTP to any host

Embeddings are computed *before* the kernel is entered, during evidence registration. The kernel never embeds.

Enforce with a context-local guard:

```python
# provenance_db/retry.py
import contextvars
_IN_KERNEL_TX = contextvars.ContextVar("in_kernel_tx", default=False)

class SideEffectInsideTransaction(RuntimeError):
    pass

def assert_no_side_effects(op: str) -> None:
    if _IN_KERNEL_TX.get():
        raise SideEffectInsideTransaction(
            f"{op} attempted inside the kernel serializable transaction"
        )
```

Every outbound client wrapper in `provenance_telemetry` calls `assert_no_side_effects()` first. A unit test asserts that a fake proposal handler which calls Bedrock inside the callback raises.

### 1.4 The kernel clock

```python
# step 17, first statement inside the transaction
tx_now: datetime = await conn.fetchval("SELECT transaction_timestamp()")
```

Every `recorded_at`, `created_at`, `updated_at`, `detected_at`, `superseded_at`, and `last_activity_at` written in this commit uses `tx_now`. Every `now()` evaluated by trigger predicates in this commit uses `tx_now`. One commit, one instant. This makes `state_transitions` orderable, makes the "at most one live belief version per instant" invariant (§8.6, G4) checkable, and makes fixtures reproducible.

`preflight_now` exists but is **never persisted** and never compared against a stored timestamp for a decision that survives into PHASE B.

### 1.5 The `ChangePlan`

PHASE B produces exactly one immutable object before any write:

```python
# provenance_domain/kernel/revision.py
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ChangePlan:
    case_id: UUID
    case_revision_before: int
    claims: tuple[ClaimWrite, ...] = ()
    belief_versions: tuple[BeliefVersionWrite, ...] = ()
    support_edges: tuple[SupportEdgeWrite, ...] = ()
    conflicts: tuple[ConflictWrite, ...] = ()
    commitment_deltas: tuple[CommitmentDelta, ...] = ()
    fulfillments: tuple[FulfillmentWrite, ...] = ()
    case_transition: CaseTransition | None = None
    case_attention_level: str | None = None
    trigger_deltas: tuple[TriggerDelta, ...] = ()
    outbox: tuple[OutboxWrite, ...] = ()
    reason_codes: tuple[ReasonCode, ...] = ()
    requires_human_review: bool = False

    def is_canonical_noop(self) -> bool:
        return not (
            self.claims or self.belief_versions or self.conflicts
            or self.commitment_deltas or self.fulfillments
            or self.case_transition or self.trigger_deltas
            or self.case_attention_level is not None
        )
```

Writes in steps 18–27 are a mechanical translation of the plan. No branching logic lives in the write path. This is what lets §6's revision rule be a one-line check.

### 1.6 Worked example — the hero scenario through all 30 steps

Fixture: `evals/datasets/the_move/E3_isp_invoice.json`. User timezone `America/New_York`. Seeded state before the event:

| Object | Value |
|---|---|
| `relationships.rel_isp` | ISP account `ISP-40192-7`, status `CLOSED`, valid_to `2026-06-01T04:00:00Z` |
| `cases.case_isp_cancel` | "Cancel internet service", status `RESOLVED`, revision `7`, `resolved_at 2026-06-02T13:00:00Z`, `reopened_count 0` |
| `beliefs.b_isp_service` | subject `RELATIONSHIP/rel_isp`, predicate `service_active`, `current_version_id = bv_isp_service_v1` |
| `belief_versions.bv_isp_service_v1` | `{"state":"TERMINATED"}`, valid `[2026-06-01T04:00:00Z, ∞)`, `CONFIRMED`, confidence `0.94`, `superseded_at NULL` |
| `belief_support` | `(bv_isp_service_v1, CLAIM, cl_isp_002, SUPPORTS, 0.88)` |
| `claims.cl_isp_002` | predicate `service_terminated`, actor `ISP`, `source_class=PROVIDER_SYSTEM_NOTICE`, `authority_score 0.88` |

Incoming artifact: forwarded ISP invoice PDF, `content_sha256 = 9f3c…a1`, received `2026-09-05T13:12:00Z`, amount due `USD 186.00`, service period `2026-06-01` through `2026-06-30`, account `ISP-40192-7`.

| # | What actually happens |
|---|---|
| 0 | Interpreter graph POSTs `MemoryProposal` `pr_8841` with `schema_version "1.0"`, 3 evidence ids, `candidate_case_id = case_isp_cancel` (identity confidence 0.97 on exact `external_account_ref` match). |
| 1 | Pydantic validates. `schema_version` in `{"1.0"}`. OK. |
| 2 | M2M token `provenance-agent-runtime`, scope `provenance.memory/propose` → `tenant_id = t_demo`. |
| 3 | `proposal.user_id == principal.user_id`. OK. |
| 4 | Loads `art_isp_003`, `ev_isp_003` (amount), `ev_isp_004` (service period), `ev_isp_005` (account ref). |
| 5 | All three rows carry `user_id = u_judge`, all point at `art_isp_003`. No `CORRECTION` claim retracts them. Pass. |
| 6 | `content_sha256 9f3c…a1` not present in `source_artifacts` for this user; `pr_8841` has no row in `kernel_decisions`. Not a duplicate. |
| 7 | `external_account_ref = 'ISP-40192-7'` hits the unique index → exactly one relationship, one non-`SUPERSEDED` case. `IDENTITY` resolved deterministically; the Tier R resolver is never invoked. |
| 8 | `SELECT … FROM cases WHERE id=… FOR UPDATE` (inside TX) → revision `7`, status `RESOLVED`. Snapshot loads `b_isp_service` + current version + its support edges + `commitments` for the case (none). |
| 9 | `ev_isp_004` service period normalizes to `[2026-06-01T04:00:00Z, 2026-07-01T04:00:00Z)` under the day-boundary convention (§2.4). `valid_from < valid_to`, `valid_to` within horizon. Pass. |
| 10 | Materializes `cl_003` (`balance_owed`, `USD 186.00`, `COUNTERPARTY_CLAIM`, `source_class=PROVIDER_SYSTEM_NOTICE`, kernel-assigned `authority_score 0.90` for family `BALANCE`). Entailment **EN-1** fires (§2.3): the billed service period yields entailed proposition `P_e` = `SERVICE_STATUS(state=ACTIVE)` over `[2026-06-01T04:00:00Z, 2026-07-01T04:00:00Z)`, base authority `0.88`, effective authority `0.88 − 0.30 = 0.58`. |
| 11 | Matcher compares `P_e` against incumbent `P_i` from `bv_isp_service_v1`: same subject `rel_isp`, same family `SERVICE_STATUS`, overlap = 30 days ≥ 24h, `ACTIVE ≠ TERMINATED` → mutual exclusion. One `ConflictFinding`, `conflict_type = VALUE_CONFLICT`. |
| 12 | Disposition (§3.3): `Δauthority = 0.88 − 0.58 = 0.30 ≥ 0.25`; winner `0.88 ≥ 0.80`; family not monetary so the amount gate does not apply; neither side is a user dispute → **`RETAIN_INCUMBENT_AUTO`**, conflict status `AUTO_RESOLVED`, `requires_human = false`, severity `HIGH` (incumbent is `CONFIRMED`). |
| 13 | No commitments on this case. `CommitmentDelta` list empty. |
| 14 | Requested transition `RESOLVED → REOPENED`. Matrix cell is `G` (guarded). Qualifying-evidence test (§5.3) Q1–Q5 all pass. Legal. |
| 15 | Arms `tr_isp_dispute_followup`: `ARMED`, `not_before = tx_now + 14 days`, predicate `AND(EQ(case.status,'DISPUTED'), IS_NULL(case.resolved_at))`. |
| 16 | Invariants: every new belief version has ≥1 support edge; exactly one case status transition; no negative outstanding; single case in plan. Pass. `ChangePlan` frozen. |
| 17 | `BEGIN; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;` → `tx_now = 2026-09-05T13:12:44.118Z`. Steps 4–16 re-execute against rows read here; identical plan. |
| 18 | Insert `cl_003`. Insert `evidence_items`→`claims` link via `claims.evidence_id`. |
| 19 | Insert `bv_isp_service_v2` (`{"state":"TERMINATED"}`, same interval, `CONFIRMED`, confidence `0.94` **unchanged** — the incumbent won). Insert support edges: `(v2, CLAIM, cl_isp_002, SUPPORTS, 0.88)` carried forward, `(v2, CLAIM, cl_003, CONTRADICTS, 0.58, reason='EN-1')`. Insert `bv_isp_balance_v1` (`{"currency":"USD","amount":"186.00"}`, `DISPUTED`, confidence `0.53`) with `(SUPPORTS→cl_003, 0.90)` and `(CONTRADICTS→BELIEF_VERSION bv_isp_service_v2)`. `UPDATE belief_versions SET superseded_at=tx_now WHERE id=bv_isp_service_v1`. `UPDATE beliefs SET current_version_id=bv_isp_service_v2`. |
| 20 | Insert `conflicts.cf_2201`: `VALUE_CONFLICT`, `AUTO_RESOLVED`, `HIGH`, `requires_human=false`, `canonical_belief_version_id=bv_isp_service_v2`, `resolution_reason_code='AUTO_RESOLVED_ENTAILMENT_PENALTY'`. |
| 21 | No-op. |
| 22 | `UPDATE cases SET status='REOPENED', revision=8, reopened_count=1, attention_level='URGENT', last_activity_at=tx_now WHERE id=case_isp_cancel AND revision=7` → 1 row. |
| 23 | Four `state_transitions` rows, all `case_revision = 8`: `CASE_STATUS(RESOLVED→REOPENED, CASE_REOPENED_QUALIFYING_EVIDENCE)`, `BELIEF_VERSION(→bv_isp_service_v2, BELIEF_RETAINED_UNDER_CONTRADICTION)`, `BELIEF_VERSION(→bv_isp_balance_v1, BELIEF_MARKED_DISPUTED)`, `CONFLICT(→AUTO_RESOLVED, CONFLICT_VALUE_MUTUAL_EXCLUSION)`. |
| 24 | Insert `tr_isp_dispute_followup`, `basis_case_revision = 8`. |
| 25 | Insert `kernel_decisions.kd_5510`: decision `ACCEPTED_WITH_CONFLICT`, `case_revision_before=7`, `case_revision_after=8`, `retry_count=0`. |
| 26 | `UPDATE memory_proposals SET status='ACCEPTED_WITH_CONFLICT', decided_at=tx_now, kernel_decision_id=kd_5510`. |
| 27 | Two `outbox_events`, `aggregate_type='CASE'`, `aggregate_id=case_isp_cancel`, `aggregate_version=8`: `conflict.detected.v1` and `case.reopened.v1`. |
| 28 | `COMMIT`. |
| 29 | No `40001`. |
| 30 | Returns `KernelCommitResult` (§9.2 shows the exact JSON). |

**Second reveal (independent commit, no artifact involved).** On `2026-06-20T04:00:00Z`, EventBridge Scheduler wakes `tr_landlord_deposit`. The wakeup worker submits a `proposal_type = TRIGGER_EVALUATION` proposal with no claims and no model in the loop. Steps 4–6 and 10–13 are trivial; step 13 recomputes `deposit_outstanding` from the `fulfillments` ledger and gets `USD 1800.00 − 0.00 = 1800.00`; the predicate `AND(GT(commitments.deposit.outstanding_amount, 0), GTE(clock.now, commitments.deposit.due_at))` evaluates `TRUE` against `tx_now`; step 14 moves `case_landlord_deposit` `WAITING → ACTIONABLE`; the trigger goes `ARMED → FIRED`. Reason codes: `TRIGGER_FIRED_PREDICATE_TRUE`, `COMMITMENT_PARTIAL_RECOMPUTED`. Prospective memory fired with zero LLM involvement and zero user-set reminders.

---

## 2. Contradiction detection

This is the part of the existing documentation that is most under-specified, and it is where a build dies: "values are mutually exclusive" is not implementable. This section narrows it to a closed, finite, testable set.

### 2.1 Design decision: a closed predicate-family registry

v1 recognizes **exactly five predicate families**. A proposition whose predicate is not in the registry is admitted as a claim, may ground a belief, and **never produces a `conflicts` row**. There is no generic semantic contradiction detector, because a generic one cannot be tested and cannot be explained in State Proof.

| Family | Canonical predicate (stored in `beliefs.predicate`) | Surface predicates accepted from proposals | Belief subject | Value schema |
|---|---|---|---|---|
| `SERVICE_STATUS` | `service_active` | `service_active`, `service_terminated`, `service_cancelled`, `service_suspended` | `RELATIONSHIP` | `{"state": "ACTIVE"\|"TERMINATED"}` |
| `BALANCE` | `balance_owed` | `balance_owed`, `amount_due`, `invoice_total` | `RELATIONSHIP` or `CASE` | `{"currency": "USD", "amount": "186.0000"}` |
| `PAYMENT` | `payment_received` | `payment_received`, `payment_sent`, `payment_not_received` | *(no belief row — see §2.7)* | `{"currency","amount","paid_at","external_ref","asserted"}` |
| `OUTSTANDING` | `deposit_outstanding` | `deposit_outstanding`, `refund_outstanding`, `reimbursement_outstanding` | `COMMITMENT` | `{"currency","amount","commitment_id"}` |
| `COMMITMENT_STATUS` | `commitment_withdrawn` | `commitment_withdrawn`, `commitment_revoked`, `promise_retracted` | `COMMITMENT` | `{"withdrawn": bool, "commitment_id": UUID}` |

**Rule N1 — one belief per (subject, family).** `beliefs.predicate` always stores the family's *canonical* predicate. `service_terminated` and `service_active` are two surface forms of one belief. Without this rule the `UNIQUE (tenant_id, user_id, subject_type, subject_id, predicate)` constraint would let two mutually exclusive beliefs coexist as separate rows and the whole contradiction model would silently no-op.

**Rule N2 — the kernel assigns authority, not the model.** `claims.authority_score` is written by the kernel from `(family, source_class)` (§3.2). A model-proposed `authority_score` is discarded. A model-proposed `source_class` outside the closed enum maps to `unknown_source_class_authority` with reason `AUTHORITY_UNMAPPED_SOURCE_CLASS`.

### 2.2 Normalization

```python
# provenance_domain/kernel/propositions.py
@dataclass(frozen=True)
class Proposition:
    prop_id: UUID                  # claims.id or belief_versions.id
    source_kind: str               # CLAIM | BELIEF_VERSION | DERIVATION
    subject_type: str
    subject_id: UUID
    family: Family
    value: FamilyValue             # ServiceStatusValue | BalanceValue | ...
    valid_from: datetime | None
    valid_to: datetime | None
    validity_basis: str            # EXPLICIT | EXPLICIT_OPEN | UNKNOWN
    base_authority: Decimal
    entailed_from: UUID | None     # non-None => entailment penalty applied
    entailment_rule: str | None    # "EN-1" | "EN-2" | None
    actor_ref: str | None          # normalized actor identity for tie-breaks
    recorded_at: datetime
    is_incumbent: bool

    @property
    def authority(self) -> Decimal:
        if self.entailed_from is None:
            return self.base_authority
        return max(Decimal("0.00"), self.base_authority - CFG.entailment_penalty)
```

Normalization steps, in order:

1. Map surface predicate → family, or `UNMAPPED` (stop; no contradiction detection).
2. Coerce the value into the family's schema. `service_terminated: true` → `{"state":"TERMINATED"}`. `service_active: false` → `{"state":"TERMINATED"}`. `payment_not_received` → `PaymentValue(asserted=False)`.
3. Normalize money: `Decimal`, quantized to 4 dp, ISO-4217 3-char currency, uppercase. Never `float`.
4. Normalize the validity interval (§2.4).
5. Set `validity_basis` from `claims.object_json.validity_basis`, defaulting to `UNKNOWN`.
6. Look up `base_authority` from the grid in §3.2.

### 2.3 Entailment (the rule that makes the hero scenario deterministic)

An invoice for June does not literally say "service was active in June". Without entailment, the ISP invoice never contradicts the termination belief and the demo does not exist. v1 defines **two** entailment rules and no more.

| Rule | Antecedent | Entailed proposition | Notes |
|---|---|---|---|
| **EN-1** | A `BALANCE` proposition whose `object_json.service_period` is a non-null, non-inverted interval `[a, b)` | `SERVICE_STATUS(state=ACTIVE)` over `[a, b)`, same subject relationship | The obligor is billing for a period; billing entails the service was supplied. Authority inherits from the parent, minus `entailment_penalty`. |
| **EN-2** | An `OUTSTANDING` proposition asserting `amount == 0` for commitment C | `COMMITMENT_STATUS(withdrawn=False)` **and** a synthetic `PAYMENT(asserted=True, amount=C.committed_amount)` for the identity key `(C.id, "ENTAILED_FULL_SETTLEMENT")` | Lets "your deposit was fully returned" collide with the fulfillment ledger. |

Entailed propositions:

- carry `entailed_from = parent claim id` and `entailment_rule`;
- get `authority = base − 0.30`;
- are **never** persisted as their own `claims` row — they exist only in memory. Their trace is the `belief_support` edge that records `source_id = parent claim id` with `reason_code = 'EN-1'`. This keeps `claims` a faithful record of what actors actually asserted (invariant 1) while still letting the matcher see the implication.

The penalty is the whole game: a *direct* statement about service status outranks an *implied* one by 0.30, which is more than `auto_resolve_margin` (0.25). This is what makes both the forward hero case and the late-arriving case (§8.7) resolve correctly and automatically.

### 2.4 Validity interval normalization and the overlap test

Intervals are half-open `[valid_from, valid_to)`, UTC, per `00_IMPLEMENTATION_MAP.md` §7.

**Day-boundary convention (mandatory).** Sources state dates, not instants. Given a calendar date `D` and the user's timezone:

| Phrase pattern | Normalized instant |
|---|---|
| "effective / starting / from `D`" | `D 00:00:00` in user tz → UTC (inclusive lower bound) |
| "terminated / ends / through / until `D`" | `(D + 1 day) 00:00:00` in user tz → UTC (exclusive upper bound) |
| "service period `D1` to `D2`" | `[D1 00:00, (D2+1) 00:00)` in user tz → UTC |

Worked: "service terminated 31 May 2026", tz `America/New_York` (UTC−4 in May) → the `TERMINATED` state begins at `2026-06-01T04:00:00Z`. "Invoice period June 1–30" → `[2026-06-01T04:00:00Z, 2026-07-01T04:00:00Z)`. Overlap is 30 days, not zero. Get this convention wrong by one day and the hero scenario produces a `TEMPORAL_CONFLICT` instead of a `VALUE_CONFLICT`, so it is a required unit test.

**Unknown validity.** If `validity_basis == 'UNKNOWN'`, the proposition **does not participate in interval-overlap contradiction detection at all** (rule T2, §8.2). It may still ground a belief, and it may produce a `QUALIFIES` support edge, but it can never create a `conflicts` row. Reason code `VALIDITY_UNKNOWN_NOT_COMPARABLE`. This single rule eliminates the largest source of false-positive conflicts.

**Instant widening.** `PAYMENT` propositions are point events. `paid_at = t` widens to `[t − 3 days, t + 3 days)` (`payment_match_window_days`) for identity matching only. Other families with a zero-length interval widen to `[t, t + 1 day)` (`instant_widen_days`).

```python
# provenance_domain/kernel/contradiction.py
NEG_INF = datetime(1, 1, 1, tzinfo=timezone.utc)
POS_INF = datetime(9999, 12, 31, tzinfo=timezone.utc)

def bounds(p: Proposition, cfg: KernelConfig) -> tuple[datetime, datetime]:
    lo = p.valid_from or NEG_INF
    hi = p.valid_to or POS_INF
    if hi <= lo:                                   # instant or inverted
        if p.family is Family.PAYMENT:
            w = timedelta(days=cfg.payment_match_window_days)
            return lo - w, lo + w
        return lo, lo + timedelta(days=cfg.instant_widen_days)
    return lo, hi

def material_overlap(a: Proposition, b: Proposition,
                     cfg: KernelConfig) -> timedelta | None:
    """None => not comparable. Otherwise the overlap duration."""
    if a.validity_basis == "UNKNOWN" or b.validity_basis == "UNKNOWN":
        return None
    a_lo, a_hi = bounds(a, cfg)
    b_lo, b_hi = bounds(b, cfg)
    lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
    if hi <= lo:
        return None
    dur = hi - lo
    contained = (a_lo >= b_lo and a_hi <= b_hi) or (b_lo >= a_lo and b_hi <= a_hi)
    if contained or dur.total_seconds() >= cfg.material_overlap_min_seconds:
        return dur
    return None                                     # brushing overlap: ignore
```

Rationale for `material_overlap_min_seconds = 86400`: two service periods that touch for four hours across a timezone-inference error are a parsing artifact, not a contradiction. Full containment always counts regardless of duration, so a one-hour period wholly inside a one-year period is never dismissed.

### 2.5 Amount comparison

```python
def amounts_differ(x: Decimal, y: Decimal, cfg: KernelConfig) -> bool:
    delta = abs(x - y)
    tol = max(cfg.amount_abs_tolerance, cfg.amount_rel_tolerance * max(abs(x), abs(y)))
    return delta > tol
```

`186.00` vs `186.00` → equal. `1800.00` vs `1791.00` → delta `9.00` > tol `9.00`? No — `0.005 × 1800 = 9.00`, and `9.00 > 9.00` is false, so equal. `1800.00` vs `1780.00` → delta `20.00` > `9.00` → differ. Tolerance exists because rounding and fee lines produce sub-percent noise that is not a dispute.

### 2.6 The matcher decision table

This is the complete v1 rule set. Each row is one entry in the registry in `families.py`. `L` is the incumbent (canonical belief version or existing ledger row), `R` is the challenger (new proposition from the proposal). All rows additionally require: same `tenant_id`/`user_id`, same `(subject_type, subject_id)`, both `validity_basis != UNKNOWN`, and neither side's source evidence retracted (§2.8).

| # | Family | Additional match key | Mutual-exclusion predicate | Overlap test | `conflict_type` |
|---|---|---|---|---|---|
| M1 | `SERVICE_STATUS` | — | `L.state != R.state` | `material_overlap(L,R)` not None | `VALUE_CONFLICT` |
| M2 | `SERVICE_STATUS` | — | `L.state == R.state` and both intervals begin, but `abs(L.lo − R.lo) > 24h` | intervals overlap at all | `TEMPORAL_CONFLICT` |
| M3 | `BALANCE` | `L.currency == R.currency` | `amounts_differ(L.amount, R.amount)` | `material_overlap(L,R)` not None | `VALUE_CONFLICT` |
| M4 | `BALANCE` | — | `L.currency != R.currency` | `material_overlap(L,R)` not None | `VALUE_CONFLICT` + reason `CONFLICT_CURRENCY_MISMATCH` |
| M5 | `PAYMENT` | `payment_key(L) == payment_key(R)` (§2.7) | `L.asserted != R.asserted` | key match implies window overlap | `FULFILLMENT_CONFLICT` |
| M6 | `PAYMENT` | `payment_key(L) == payment_key(R)`, same currency | `amounts_differ(L.amount, R.amount)` | key match implies window overlap | `FULFILLMENT_CONFLICT` |
| M7 | `PAYMENT` | `L.external_ref == R.external_ref` (both non-null) | `L.currency != R.currency` | — | `FULFILLMENT_CONFLICT` + `CONFLICT_CURRENCY_MISMATCH` |
| M8 | `OUTSTANDING` | same `commitment_id`, same currency | `amounts_differ(L.amount, R.amount)` | `material_overlap(L,R)` not None | `VALUE_CONFLICT` |
| M9 | `OUTSTANDING` | same `commitment_id` | `R.amount == 0` and `L.amount > 0` (counterparty says settled; ledger says not) | `material_overlap(L,R)` not None | `FULFILLMENT_CONFLICT` |
| M10 | `OUTSTANDING` | same `commitment_id` | `L.currency != R.currency` | `material_overlap(L,R)` not None | `VALUE_CONFLICT` + `CONFLICT_CURRENCY_MISMATCH` |
| M11 | `COMMITMENT_STATUS` | same `commitment_id` | `R.withdrawn == True` and commitment status ∈ `{ACTIVE, PARTIAL}` | commitment validity contains `R.valid_from` | `COMMITMENT_WITHDRAWAL_CONFLICT` |
| M12 | `COMMITMENT_STATUS` | same `commitment_id` | `L.withdrawn != R.withdrawn` | `material_overlap(L,R)` not None | `VALUE_CONFLICT` |
| M13 | *(post-pass, any family)* | a finding from M1–M12 already exists | both sides' `authority ≥ 0.80` **and** `abs(ΔA) < auto_resolve_margin` | inherited | upgrade `conflict_type` → `AUTHORITY_CONFLICT` |

M13 runs after M1–M12 and rewrites `conflict_type` in place. It is the only rule that produces `AUTHORITY_CONFLICT`, and `AUTHORITY_CONFLICT` always means "two credible sources, no deterministic winner" → human review (§3.4, H1).

```python
# provenance_domain/kernel/contradiction.py
def match(left: Proposition, right: Proposition,
          ctx: MatchContext, cfg: KernelConfig) -> ConflictFinding | None:
    if left.family is not right.family:
        return None
    if (left.subject_type, left.subject_id) != (right.subject_type, right.subject_id):
        return None
    if ctx.is_retrieval_ineligible(left) or ctx.is_retrieval_ineligible(right):
        return None                                   # SOURCE_RETRACTED_EXCLUDED
    rule = FAMILY_RULES[left.family]                  # M1..M12 for that family
    finding = rule(left, right, ctx, cfg)
    if finding is None:
        return None
    return _apply_authority_upgrade(finding, left, right, cfg)   # M13
```

The matcher is invoked as a full cross-product of *(incumbent propositions in the aggregate snapshot) × (proposition materialized from this proposal)*. Snapshots are bounded — one case, its beliefs, its commitments — so the cross-product is small (single-digit × single-digit in the demo). No index or heuristic pruning is needed at v1 scale, and adding one would create a correctness surface with no measured benefit.

### 2.7 Payment identity key

`PAYMENT` propositions never create a belief row (Rule N1 would collapse every payment for a subject into one belief). They are matched against `fulfillments` rows and feed §4.

```python
def payment_key(p: Proposition) -> tuple:
    v = p.value
    if v.external_ref:
        return (p.subject_id, "REF", v.external_ref.strip().upper())
    bucket = (v.paid_at - EPOCH) // timedelta(days=CFG.payment_match_window_days)
    return (p.subject_id, "AMT", v.currency, v.amount, bucket)
```

The amount-and-window fallback is deliberately coarse. It will occasionally merge two genuinely distinct same-amount payments three days apart. That produces a `FULFILLMENT_CONFLICT` routed to a human, which is the safe failure direction: a false conflict costs the user one click; a missed conflict costs them money.

### 2.8 Retraction filtering (canon addition C)

Retracted and superseded evidence keeps its embedding in the CockroachDB vector index. If retrieval or the matcher ignores this, corrected evidence resurfaces and re-litigates settled facts.

**Retraction is a stored column, not a derived predicate.** `evidence_items.retraction_status STRING NOT NULL CHECK (… IN ('ACTIVE','RETRACTED','SUPERSEDED','QUARANTINED'))` and the generated `is_retrieval_eligible = (retraction_status = 'ACTIVE')` are defined in `specs/10_DATABASE_DDL.md` §5.4, and `agent_evidence_retrieval_v1` — defined in `specs/10_DATABASE_DDL.md` §14, which owns all five agent-safe views — filters on `e.retraction_status = 'ACTIVE'`. This document does **not** define that view.

An earlier draft of this section carried a `CREATE OR REPLACE VIEW agent_evidence_retrieval_v1` whose predicate was `NOT EXISTS (… claims.claim_kind = 'CORRECTION' …)`. It is withdrawn and must not be implemented. It was wrong twice: it excluded only CORRECTION-retracted rows, so `SUPERSEDED` and `QUARANTINED` evidence flowed straight back into retrieval and grounding — precisely the silent failure `00_PRODUCT.md` R4 names — and it projected `e.embedding` and `e.source_locator`, which the canonical view deliberately withholds from `pv_agent_reader`.

The grant posture stated there is still correct and still owned by `specs/10_DATABASE_DDL.md` §14 / `ops/40_INFRA_IAC.md` §11.5: `GRANT SELECT ON agent_evidence_retrieval_v1 TO pv_agent_reader` and no grant of any kind on `evidence_items` to that role.

Kernel rule: `MatchContext.is_retrieval_ineligible(p)` returns `True` when the proposition's ultimate evidence has `retraction_status != 'ACTIVE'`, or when the source is a `belief_versions` row with `epistemic_status = 'RETRACTED'`. Ineligible sources are excluded from matching and may not ground a new belief version. Reason code `SOURCE_RETRACTED_EXCLUDED` remains the stable event vocabulary for backward compatibility.

Every ANN query issued anywhere in Provenance reads `agent_evidence_retrieval_v1`, never `evidence_items`, and always carries the `user_id` prefix predicate. The `REVOKE` makes that structural for the agent role rather than a convention.

### 2.9 Advisory-only proposal fields

The following `MemoryProposal` fields are read for telemetry, ranking, and State Proof narration, and **cannot** by themselves create, suppress, or re-type a conflict:

| Field | Effect on canonical state |
|---|---|
| `conflict_hints[]` | If a hint names a subject/predicate pair that maps to a v1 family, the matcher runs on that pair (it would have anyway). If it maps to no family, the kernel emits `CONFLICT_HINT_UNMAPPED_FAMILY`, records the hint text as an `unresolved_question` on the case, and writes **no** `conflicts` row. |
| `identity.confidence` | Gates `PENDING_IDENTITY` only. Never overrides a deterministic `external_account_ref` match. |
| `claims[].authority_score` | Discarded. Kernel recomputes (Rule N2). |
| `claims[].source_class` | Used, but only as a key into the frozen grid; unknown values fall to `0.10`. |
| `requested_case_transition` | A *request*. §5 decides legality; an illegal request is a rejection, not a negotiation. |
| `model.*`, `unresolved_questions[]` | Recorded, never decisive. |

A model that hallucinates `"conflict_type": "AUTHORITY_CONFLICT"` changes nothing. That is the point of the Kernel.

### 2.10 Explicitly OUT of scope for v1

Stated plainly so nobody builds it by accident and nobody claims it exists:

1. **`POLICY_VERSION_CONFLICT` and any `policy_term` family.** No policy-versioning reasoning. Policy documents are admitted as evidence and claims; they never conflict.
2. **`IDENTITY_CONFLICT` as a matcher output.** Identity ambiguity is resolved at step 7 and yields `PENDING_IDENTITY`, not a `conflicts` row. The `conflict_type` value remains reserved.
3. **Entitlement / eligibility / legal-interpretation reasoning.** ("Are you *owed* this?" is out; "does the ledger say outstanding > 0?" is in.)
4. **Currency conversion.** The kernel never converts. Mismatched currency is always a conflict (M4, M7, M10).
5. **Multi-hop entailment.** EN-1 and EN-2 fire once. An entailed proposition never feeds another entailment rule. No transitive closure, no fixpoint loop.
6. **Free-text negation and semantic contradiction.** "We never agreed to that" does not become a conflict unless a Tier R model maps it onto a v1 family predicate with an explicit value, and even then §2.9 applies.
7. **Non-monetary quantity units.** Only `DECIMAL` money with an ISO currency. No "3 boxes vs 4 boxes".
8. **Cross-case and cross-relationship contradictions.** The matcher scope is one case aggregate. A contradiction spanning two cases surfaces as two independent findings or not at all.
9. **Cross-user contradictions.** Impossible by construction; tenant isolation precedes the matcher.
10. **Conflicts between two versions of the *same* belief.** Supersession is lineage (§8), not contradiction.
11. **Automatic conflict re-opening when a threshold changes.** Config changes do not retroactively re-derive conflicts. Historical decisions stand with the config version recorded in `kernel_decisions.reason_codes`.

---

## 3. Auto-resolution versus human review

### 3.1 Disposition is not the same as canonical retention

The two questions the existing docs conflate:

1. **Which value stays canonical?** — always answered, always deterministically.
2. **Is the conflict closed, or does a human have to look at it?** — a separate answer.

Four dispositions, and no others:

| Disposition | `conflicts.status` | New belief version? | New version's `epistemic_status` | `requires_human` |
|---|---|---|---|---|
| `NO_INCUMBENT` | *(no conflict row)* | yes — belief v1 | `CONFIRMED` if authority ≥ 0.90 else `PROBABLE` | false |
| `RETAIN_INCUMBENT_AUTO` | `AUTO_RESOLVED` | yes — identical value, new grounding | unchanged from v_n | false |
| `PROMOTE_CHALLENGER_AUTO` | `AUTO_RESOLVED` | yes — challenger value | `CONFIRMED` if authority ≥ 0.90 else `PROBABLE` | false |
| `RETAIN_INCUMBENT_DISPUTED` | `NEEDS_HUMAN` | yes — identical value, decayed confidence | `DISPUTED` | true |

**Rule G3 — grounding is frozen per version.** `belief_support` rows are append-only and are written *only* in the transaction that creates the `belief_versions` row they attach to. Consequently **any change in grounding requires a new belief version.** This is why even `RETAIN_INCUMBENT_AUTO` writes a v_{n+1}: the incumbent's value did not change, but its grounding did — it now carries a `CONTRADICTS` edge. That new version is exactly what State Proof renders as "confirmed, contradicted, and retained — here is why", which is the product's most persuasive single screen.

Confidence formula for `RETAIN_INCUMBENT_DISPUTED`:

```python
conf_new = max(Decimal("0.05"),
               (conf_old * (Decimal(1) - cfg.dispute_decay * challenger.authority))
               .quantize(Decimal("0.0001")))
```

For `RETAIN_INCUMBENT_AUTO`, confidence is **unchanged** — the incumbent won on the merits, so decaying it would be arbitrary.

### 3.2 Predicate-aware source authority bands (config table)

`source_class` is a closed enum the model may propose; the kernel maps `(family, source_class)` → authority. This grid is `provenance_domain/kernel/authority.py` and is the *entire* authority model.

| `source_class` | `SERVICE_STATUS` | `BALANCE` | `PAYMENT` | `OUTSTANDING` | `COMMITMENT_STATUS` |
|---|---|---|---|---|---|
| `BANK_OR_CARD_STATEMENT` | 0.10 | 0.55 | **0.97** | 0.60 | 0.10 |
| `PAYMENT_PROCESSOR_RECORD` | 0.10 | 0.60 | **0.96** | 0.60 | 0.10 |
| `SIGNED_AGREEMENT` | 0.92 | 0.85 | 0.30 | **0.90** | **0.95** |
| `PROVIDER_SYSTEM_NOTICE` | **0.88** | **0.90** | 0.70 | 0.72 | 0.55 |
| `PROVIDER_AGENT_WRITTEN` | 0.85 | 0.72 | 0.55 | 0.70 | **0.88** |
| `PROVIDER_AGENT_CHAT` | 0.68 | 0.55 | 0.45 | 0.55 | 0.70 |
| `OFFICIAL_POLICY_DOC` | 0.60 | 0.50 | 0.20 | 0.45 | 0.62 |
| `MARKETING_PAGE` | 0.35 | 0.25 | 0.05 | 0.20 | 0.30 |
| `USER_UPLOADED_RECEIPT` | 0.30 | 0.45 | 0.80 | 0.50 | 0.25 |
| `USER_STATEMENT` | 0.45 | 0.40 | 0.50 | 0.48 | 0.40 |
| `USER_CORRECTION` | 0.75 | 0.70 | 0.70 | 0.72 | 0.70 |
| `MODEL_INFERENCE` | 0.05 | 0.05 | 0.05 | 0.05 | 0.05 |
| *(unmapped)* | 0.10 | 0.10 | 0.10 | 0.10 | 0.10 |

Read the grid horizontally to see why it must be a grid. `BANK_OR_CARD_STATEMENT` is 0.97 for `PAYMENT` and 0.10 for `SERVICE_STATUS`: your bank knows a charge cleared; it knows nothing about whether your ISP honored a cancellation. `MARKETING_PAGE` is 0.35 for `SERVICE_STATUS` and 0.05 for `PAYMENT`. `MODEL_INFERENCE` is 0.05 everywhere — a Tier R model's opinion is never authoritative for anything, which is the machine-readable form of the kernel rule.

**Authority of an incumbent belief version** is the maximum authority among its `SUPPORTS` edges, computed inside the transaction:

```sql
SELECT COALESCE(MAX(COALESCE(bs.weight, c.authority_score)), 0.0)
FROM belief_support bs
LEFT JOIN claims c ON c.id = bs.source_id AND bs.source_kind = 'CLAIM'
WHERE bs.belief_version_id = $1
  AND bs.relation = 'SUPPORTS';
```

### 3.3 Auto-resolution: the exact deterministic condition

```python
# provenance_domain/kernel/disposition.py
AUTO_RESOLVABLE_TYPES = {"VALUE_CONFLICT", "TEMPORAL_CONFLICT", "FULFILLMENT_CONFLICT"}
MONETARY_FAMILIES = {Family.BALANCE, Family.PAYMENT, Family.OUTSTANDING}

def decide(f: ConflictFinding, cfg: KernelConfig) -> Disposition:
    inc, chal = f.incumbent, f.challenger
    reasons: list[ReasonCode] = []

    # ---- mandatory human-review gates, evaluated first, short-circuiting ----
    if f.conflict_type == "AUTHORITY_CONFLICT":                        # H1
        return Disposition.needs_human(R.HUMAN_REQUIRED_AUTHORITY_TIE)
    if f.conflict_type == "COMMITMENT_WITHDRAWAL_CONFLICT":            # H2
        return Disposition.needs_human(R.HUMAN_REQUIRED_WITHDRAWAL)
    if chal.source_claim_kind in ("USER_CLAIM", "CORRECTION"):         # H4
        return Disposition.needs_human(R.HUMAN_REQUIRED_USER_DISPUTE)
    if (f.family in MONETARY_FAMILIES
            and f.monetary_exposure >= cfg.human_review_amount_threshold):   # H5
        return Disposition.needs_human(R.HUMAN_REQUIRED_MONETARY_THRESHOLD)
    if f.blocks_approved_action:                                       # H6
        return Disposition.needs_human(R.HUMAN_REQUIRED_ACTION_BLOCKING)

    # ---- deterministic auto-resolution ----
    if f.conflict_type not in AUTO_RESOLVABLE_TYPES:
        return Disposition.needs_human(R.HUMAN_REQUIRED_UNRESOLVABLE_TYPE)

    delta = inc.authority - chal.authority
    winner = inc if delta >= 0 else chal
    if abs(delta) >= cfg.auto_resolve_margin and winner.authority >= cfg.auto_resolve_floor:
        code = (R.AUTO_RESOLVED_ENTAILMENT_PENALTY
                if (inc.entailed_from or chal.entailed_from)
                else R.AUTO_RESOLVED_AUTHORITY_MARGIN)
        return (Disposition.retain(code) if winner is inc
                else Disposition.promote(code))

    # ---- temporal precedence tie-break (narrow, same-actor only) ----
    if (f.family in (Family.SERVICE_STATUS, Family.BALANCE)
            and inc.actor_ref is not None
            and inc.actor_ref == chal.actor_ref
            and chal.valid_from is not None and inc.valid_from is not None
            and chal.valid_from > inc.valid_from
            and chal.recorded_at > inc.recorded_at
            and chal.authority >= cfg.supersession_authority_floor):
        return Disposition.promote(R.AUTO_RESOLVED_TEMPORAL_PRECEDENCE)

    return Disposition.needs_human(R.HUMAN_REQUIRED_AUTHORITY_TIE)
```

`monetary_exposure` is defined precisely:

- monetary families: `abs(incumbent.amount − challenger.amount)`, or `challenger.amount` when there is no incumbent amount;
- non-monetary families: the maximum absolute amount among monetary claims admitted **in the same commit against the same case**, else `0`.

**Auto-resolution is never silent.** Every `AUTO_RESOLVED` disposition still writes: a durable `conflicts` row, a new belief version with a `CONTRADICTS` grounding edge, a `state_transitions` row, an outbox event, and a `resolution_reason_code`. `00_IMPLEMENTATION_MAP.md` §12's rule — "do not silently resolve two high-authority conflicting sources" — is enforced by H1: two sources both at or above 0.80 within 0.25 of each other *cannot* auto-resolve, by construction.

### 3.4 Human review: the exact conditions

| ID | Condition | Reason code | Rationale |
|---|---|---|---|
| H1 | `conflict_type == AUTHORITY_CONFLICT` (M13: both ≥ 0.80, `abs(Δ) < 0.25`) | `HUMAN_REQUIRED_AUTHORITY_TIE` | Two credible sources disagree; no deterministic winner exists. |
| H2 | `conflict_type == COMMITMENT_WITHDRAWAL_CONFLICT` | `HUMAN_REQUIRED_WITHDRAWAL` | The obligor is retracting their own promise. Authority is symmetric by definition, so a margin test is meaningless. Always human, always. |
| H3 | Identity ambiguous across ≥ 2 non-superseded cases, or top identity confidence < 0.90, or top-two gap < 0.15 | `IDENTITY_AMBIGUOUS_MULTI_CASE` | Handled at step 7 as `PENDING_IDENTITY`; no conflict row is written. |
| H4 | Either side is `USER_CLAIM` or `CORRECTION` contradicting canonical state | `HUMAN_REQUIRED_USER_DISPUTE` | The user disputing their own record is the strongest possible signal that the model got something wrong. Never auto-resolve against or in favor of the user. |
| H5 | Monetary family and `monetary_exposure ≥ 100.00` | `HUMAN_REQUIRED_MONETARY_THRESHOLD` | Money moves; a wrong auto-resolution is unrecoverable from the user's perspective. |
| H6 | The conflict's subject grounds a `belief_versions` row referenced by an `action_intents` row in status `APPROVED` or `EXECUTING` | `HUMAN_REQUIRED_ACTION_BLOCKING` | Invariant 4. A pending external side effect must not have its basis silently rewritten. Also forces §18 approval-staleness revalidation. |
| H7 | Post-dispute `belief_confidence < 0.60` and an `action_intents` row references that belief | `HUMAN_REQUIRED_ACTION_BLOCKING` | Grounded advocacy requires a belief worth advocating. |
| H8 | `conflict_type` outside `AUTO_RESOLVABLE_TYPES` | `HUMAN_REQUIRED_UNRESOLVABLE_TYPE` | Fail closed on anything the table does not cover. |

`NEEDS_HUMAN` conflicts set `cases.attention_level = 'ATTENTION'` and emit `conflict.detected.v1` with `status='NEEDS_HUMAN'`. They never block ingestion: evidence and claims are still admitted (invariant 1), the incumbent belief is still canonical, and it is marked `DISPUTED`.

### 3.5 Why a single global trust score is forbidden

A `sources.trust_score DECIMAL` column would be smaller code, and it would be wrong in four independent ways.

1. **It is empirically false.** A bank statement is near-perfect evidence for `payment_received` (0.97) and near-worthless for `service_terminated` (0.10). One number cannot hold both. Averaging them produces a source that is mediocre at everything and correct at nothing.
2. **It breaks the hero scenario.** The ISP is a single source. Its 15 May termination confirmation and its 5 September invoice would carry *identical* trust. Every collision between them would land on H1 and demand human review — including the one the product exists to resolve automatically. The correct outcome falls out only because `SERVICE_STATUS` distinguishes a *direct* statement from one *entailed* by billing.
3. **It is unexplainable.** State Proof must answer "why did you keep the old belief?" The answer "the ISP scores 0.71" is not an answer. "A direct cancellation confirmation (0.88) outranks a service period implied by an invoice (0.88 − 0.30 entailment penalty = 0.58) by more than the 0.25 auto-resolution margin" is auditable, arguable, and changeable.
4. **It creates a recency ratchet.** A global score inevitably gets updated from outcomes, so the highest-volume counterparty accumulates the most trust. Institutions that send you the most mail would become the most authoritative about your own life. That is precisely the failure Provenance exists to reverse.

Corollary: `evidence_items.source_authority` is stored for display and eval only. **No kernel decision reads it.** Decisions read `claims.authority_score`, which the kernel wrote from the `(family, source_class)` grid.

---

## 4. Monetary commitment algorithm

### 4.1 The central design decision: recompute, never increment

`02_DATA_MEMORY_TRANSACTIONS.md` §12 presents `new_fulfilled = old_fulfilled + F`. Implement it that way and the first duplicate delivery double-counts a payment. The kernel instead **recomputes the projection from the fulfillment ledger by aggregation inside the transaction.**

```text
admitted_sum   = Σ f.amount  where f.commitment_id = C.id
                             and f.admission_status = 'ADMITTED'
                             and f.currency = C.currency
fulfilled      = MIN(admitted_sum, C.committed_amount)
outstanding    = C.committed_amount - fulfilled          -- always >= 0
excess         = admitted_sum - C.committed_amount       -- > 0 => anomaly
```

Because `fulfillments` carries `UNIQUE (commitment_id, evidence_id)`, replaying the same evidence is a no-op at the constraint level *and* at the arithmetic level. Idempotency stops being a discipline and becomes a property.

### 4.2 The algorithm

```python
# provenance_domain/kernel/money.py
def apply_fulfillment(c: CommitmentRow,
                      ledger: list[FulfillmentRow],
                      new: ProposedFulfillment,
                      open_conflicts: list[ConflictRow],
                      tx_now: datetime,
                      cfg: KernelConfig) -> CommitmentDelta:
    reasons: list[ReasonCode] = []
    conflicts: list[ConflictWrite] = []

    # ---- 1. currency gate: never convert, never guess ----
    if new.currency is not None and c.currency is not None and new.currency != c.currency:
        return CommitmentDelta(
            commitment_id=c.id,
            fulfillment=FulfillmentWrite(
                **new.as_row(), admission_status="REJECTED_CURRENCY"),
            conflicts=[ConflictWrite(
                conflict_type="FULFILLMENT_CONFLICT",
                status="NEEDS_HUMAN", severity="HIGH", requires_human=True,
                reason_code=R.CONFLICT_CURRENCY_MISMATCH,
                detected_at=tx_now)],
            status_before=c.status, status_after="DISPUTED",
            fulfilled_after=c.fulfilled_amount,
            outstanding_after=c.outstanding_amount,
            reasons=[R.FULFILLMENT_CURRENCY_REJECTED, R.CONFLICT_CURRENCY_MISMATCH],
        )

    # ---- 2. duplicate evidence: exact no-op, still a valid decision ----
    if any(f.evidence_id == new.evidence_id for f in ledger):
        return CommitmentDelta.noop(c, reasons=[R.FULFILLMENT_EVIDENCE_DUPLICATE])

    # ---- 3. recompute from the ledger ----
    admitted = [f for f in ledger if f.admission_status == "ADMITTED"
                and f.currency == c.currency] + [new]
    admitted_sum = sum((f.amount or Decimal(0) for f in admitted), Decimal(0))
    committed    = c.committed_amount or Decimal(0)
    fulfilled    = min(admitted_sum, committed)
    outstanding  = committed - fulfilled
    excess       = admitted_sum - committed
    reasons.append(R.FULFILLMENT_ADMITTED)

    # ---- 4. over-fulfilment anomaly: flag, never silently clamp ----
    if excess > cfg.overpay_tolerance:
        conflicts.append(ConflictWrite(
            conflict_type="FULFILLMENT_CONFLICT",
            status="NEEDS_HUMAN", severity="HIGH", requires_human=True,
            reason_code=R.CONFLICT_OVER_FULFILMENT,
            detected_at=tx_now,
            notes=f"admitted {admitted_sum} {c.currency} against committed "
                  f"{committed} {c.currency}; excess {excess}"))
        reasons.append(R.COMMITMENT_DISPUTED_EXCESS)

    status_after = commitment_status(
        c, admitted_sum, committed, outstanding, excess,
        open_conflicts + conflicts, tx_now, cfg)
    if status_after == "PARTIAL":
        reasons.append(R.COMMITMENT_PARTIAL_RECOMPUTED)
    elif status_after == "FULFILLED":
        reasons.append(R.COMMITMENT_FULFILLED)

    return CommitmentDelta(
        commitment_id=c.id,
        fulfillment=FulfillmentWrite(**new.as_row(), admission_status="ADMITTED"),
        conflicts=conflicts,
        status_before=c.status, status_after=status_after,
        fulfilled_after=fulfilled, outstanding_after=outstanding,
        revision_after=c.revision + 1, reasons=reasons)
```

### 4.3 Over-fulfilment: what "do not silently clamp" means concretely

The schema enforces `fulfilled_amount <= committed_amount` and `outstanding_amount >= 0`. Storing a negative outstanding is therefore not available. The kernel's resolution loses no information and clamps nothing that matters:

- the **`fulfillments` row keeps the full observed amount** `F` — it is evidence-linked and immutable;
- the **projection** is capped at `committed_amount`;
- `excess = Σ admitted − committed` is recomputable at any time from the ledger, so it is derived, not discarded;
- a `FULFILLMENT_CONFLICT` with `CONFLICT_OVER_FULFILMENT` records the exact excess in `resolution_notes`;
- the commitment goes to `DISPUTED`, **not** `FULFILLED`.

"Silently clamping" would be setting `fulfilled = committed` and status `FULFILLED` with no artifact of the overage. Nothing here is silent: there is a conflict row, a `DISPUTED` status, a state transition, and an outbox event.

### 4.4 Status decision function

Evaluated in this exact order; first match wins.

```python
def commitment_status(c, admitted_sum, committed, outstanding, excess,
                      conflicts, tx_now, cfg) -> str:
    if c.status == "SUPERSEDED":
        return "SUPERSEDED"                                    # terminal
    if c.status == "PROPOSED" and c.condition_ast is not None:
        activation = evaluate_condition(c.condition_ast, c.projection, tx_now)
        if activation is not True:
            return "PROPOSED"                                  # FALSE or UNKNOWN
    if any(x.status in ("OPEN", "NEEDS_HUMAN")
           and x.conflict_type in ("FULFILLMENT_CONFLICT",
                                   "COMMITMENT_WITHDRAWAL_CONFLICT")
           for x in conflicts):
        return "DISPUTED"                                      # dispute dominates
    if excess > cfg.overpay_tolerance:
        return "DISPUTED"
    if c.committed_amount is None:                             # non-monetary
        return non_monetary_status(c, tx_now)
    if outstanding == 0 and admitted_sum >= committed:
        return "FULFILLED"
    if admitted_sum > 0 and outstanding > 0:
        return "PARTIAL"
    if c.valid_to is not None and tx_now >= c.valid_to:
        return "EXPIRED"
    return "ACTIVE"
```

Consequences worth stating because tests assert them:

- `outstanding > 0` can never coexist with `FULFILLED`. Enforced twice: here, and by the DB `CHECK`.
- A past `due_at` does **not** expire a commitment. A missed deadline makes the case *actionable* (via a trigger, §1.6 second reveal); it does not extinguish the obligation. Only `valid_to` expires it.
- `DISPUTED` outranks `FULFILLED`. If the counterparty says "paid in full" and the ledger says otherwise, the commitment is disputed, not fulfilled.

`condition_ast` is an activation condition, not a fulfillment test. An admitted conditional promise remains `PROPOSED` while it evaluates `FALSE` or `UNKNOWN`; it becomes `ACTIVE` only when it evaluates `TRUE`. `UNKNOWN` raises case attention and never arms an overdue trigger. After activation, non-monetary commitments (`committed_amount IS NULL`) become `FULFILLED` when at least one qualifying fulfillment is admitted; otherwise they remain `ACTIVE` or become `EXPIRED` at `valid_to`. A later false activation condition does not undo an already activated commitment; changing the obligation requires superseding evidence and a new commitment version.

### 4.5 Atomicity: one transaction, six writes

Steps 21–27 for a fulfillment are a single serializable unit:

```sql
-- inside the kernel transaction, tx_now already captured
INSERT INTO fulfillments (id, tenant_id, user_id, commitment_id, evidence_id,
                          currency, amount, fulfilled_at, admission_status,
                          confidence, created_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'ADMITTED',$9,$tx_now);

UPDATE commitments
   SET fulfilled_amount   = $10,
       outstanding_amount = $11,
       status             = $12,
       revision           = revision + 1,
       updated_at         = $tx_now
 WHERE id = $4 AND revision = $13;          -- optimistic guard, §6 R4

UPDATE cases
   SET revision = revision + 1, last_activity_at = $tx_now, updated_at = $tx_now
 WHERE id = $14 AND revision = $15;

INSERT INTO state_transitions (id, tenant_id, user_id, case_id, case_revision,
                               transition_type, from_state, to_state, reason_code,
                               proposal_id, kernel_decision_id, trace_id, recorded_at)
VALUES ($16,$2,$3,$14,$17,'COMMITMENT_STATUS',$18,$12,
        'COMMITMENT_PARTIAL_RECOMPUTED',$19,$20,$21,$tx_now);

INSERT INTO outbox_events (id, tenant_id, user_id, aggregate_type, aggregate_id,
                           aggregate_version, event_type, payload_version, payload,
                           trace_id, status, next_attempt_at, created_at)
VALUES ($22,$2,$3,'CASE',$14,$17,'memory.commitment_updated','1',
        $23::JSONB,$21,'PENDING',$tx_now,$tx_now);
```

If any statement fails, all of it rolls back. There is no window in which `outstanding` is updated but `status` is not — invariant 3.

### 4.6 Worked example — moving company, $420 committed, $200 paid

| Field | Before | After |
|---|---|---|
| `commitments.cm_moving.committed_amount` | `420.0000 USD` | `420.0000 USD` |
| `fulfillments` rows | `[]` | `[(ev_mov_004, 200.0000 USD, 2026-06-11, ADMITTED)]` |
| `admitted_sum` | `0.0000` | `200.0000` |
| `fulfilled_amount` | `0.0000` | `200.0000` |
| `outstanding_amount` | `420.0000` | `220.0000` |
| `excess` | — | `-220.0000` (no anomaly) |
| `status` | `ACTIVE` | `PARTIAL` |
| `commitments.revision` | `2` | `3` |
| `cases.case_moving_damage.revision` | `4` | `5` |
| `state_transitions` | — | `COMMITMENT_STATUS ACTIVE→PARTIAL, COMMITMENT_PARTIAL_RECOMPUTED, case_revision=5` |
| `outbox_events` | — | `commitment.partially_fulfilled.v1, aggregate_version=5` |

Now replay the identical artifact (adversarial case: duplicate forward). Step 6 catches the artifact `content_sha256`; if it somehow reaches step 13, branch 2 of `apply_fulfillment` returns a no-op; if it somehow reaches step 21, `UNIQUE (commitment_id, evidence_id)` raises `23505` mapped to `NOOP_DUPLICATE` (§7.5). Three independent defenses, because this is the failure the demo audience will try first.

Currency-mismatch variant: the same $200 arrives as `EUR 200.00`. The fulfillment row is written with `admission_status = 'REJECTED_CURRENCY'`, `outstanding` stays `420.0000`, status becomes `DISPUTED`, a `FULFILLMENT_CONFLICT` with `CONFLICT_CURRENCY_MISMATCH` is opened for human review, and the case revision still increments — a conflict row is a canonical change (§6).

---

## 5. Case transition legality

### 5.1 The matrix

Rows are the current state, columns the target. `Y` = legal unconditionally. `G` = legal only if the guard passes. `—` = illegal (`CASE_TRANSITION_ILLEGAL` → `REJECTED_INVARIANT`). Self-transitions are `—` in every case: a status that does not change is not a transition and must not consume a revision.

| from \ to | OPEN | WAITING | ACTIONABLE | IN_PROGRESS | DISPUTED | BLOCKED | AWAITING_USER | RESOLVED | REOPENED | SUPERSEDED |
|---|---|---|---|---|---|---|---|---|---|---|
| **OPEN** | — | Y | Y | — | Y | Y | — | Y | — | G2 |
| **WAITING** | — | — | Y | — | Y | Y | — | Y | — | G2 |
| **ACTIONABLE** | — | — | — | Y | Y | — | Y | Y | — | G2 |
| **IN_PROGRESS** | — | Y | Y | — | Y | — | — | Y | — | G2 |
| **DISPUTED** | — | Y | Y | — | — | — | Y | Y | — | G2 |
| **BLOCKED** | — | Y | Y | — | — | — | — | Y | — | G2 |
| **AWAITING_USER** | — | — | Y | Y | — | — | — | Y | — | G2 |
| **RESOLVED** | — | — | — | — | — | — | — | — | **G1** | G2 |
| **REOPENED** | — | Y | Y | — | Y | — | — | Y | — | G2 |
| **SUPERSEDED** | — | — | — | — | — | — | — | — | — | — |

Guards:

- **G1** — `RESOLVED → REOPENED` requires the qualifying-evidence test in §5.3.
- **G2** — `* → SUPERSEDED` requires a `CASE_MERGE` proposal naming a surviving `case_id` in the same tenant/user/relationship whose status is not `SUPERSEDED`. The surviving id is recorded in the `state_transitions.reason_code` payload and the outbox event. `SUPERSEDED` is terminal. Not exercised by the demo; implemented and tested because leaving it out makes case-merge impossible to add later without a migration.

**Rule C1 — at most one case status transition per commit.** Two requested transitions in one `ChangePlan` → `CASE_TRANSITION_MULTIPLE_IN_COMMIT` → `REJECTED_INVARIANT`. Urgency that a second hop would have conveyed is carried by `cases.attention_level` (`NONE | INFO | URGENT | ATTENTION`), which is not a status and does not need a transition. In the hero scenario the case lands on `REOPENED` with `attention_level = URGENT`; it moves `REOPENED → DISPUTED` in the *later* commit that records the dispute being sent.

```python
# provenance_domain/kernel/case_machine.py
LEGAL: dict[str, dict[str, str]] = {          # "Y" | "G1" | "G2"
    "OPEN":          {"WAITING":"Y","ACTIONABLE":"Y","DISPUTED":"Y","BLOCKED":"Y",
                      "RESOLVED":"Y","SUPERSEDED":"G2"},
    "WAITING":       {"ACTIONABLE":"Y","DISPUTED":"Y","BLOCKED":"Y","RESOLVED":"Y",
                      "SUPERSEDED":"G2"},
    "ACTIONABLE":    {"IN_PROGRESS":"Y","AWAITING_USER":"Y","DISPUTED":"Y",
                      "RESOLVED":"Y","SUPERSEDED":"G2"},
    "IN_PROGRESS":   {"WAITING":"Y","ACTIONABLE":"Y","DISPUTED":"Y","RESOLVED":"Y",
                      "SUPERSEDED":"G2"},
    "DISPUTED":      {"WAITING":"Y","ACTIONABLE":"Y","AWAITING_USER":"Y",
                      "RESOLVED":"Y","SUPERSEDED":"G2"},
    "BLOCKED":       {"WAITING":"Y","ACTIONABLE":"Y","RESOLVED":"Y","SUPERSEDED":"G2"},
    "AWAITING_USER": {"ACTIONABLE":"Y","IN_PROGRESS":"Y","RESOLVED":"Y",
                      "SUPERSEDED":"G2"},
    "RESOLVED":      {"REOPENED":"G1","SUPERSEDED":"G2"},
    "REOPENED":      {"WAITING":"Y","ACTIONABLE":"Y","DISPUTED":"Y","RESOLVED":"Y",
                      "SUPERSEDED":"G2"},
    "SUPERSEDED":    {},
}
```

### 5.2 Who may request a transition

| Requester | Allowed targets |
|---|---|
| Kernel, derived from a conflict or commitment recompute | `DISPUTED`, `ACTIONABLE`, `REOPENED` (G1) |
| Trigger evaluation (`TRIGGER_EVALUATION` proposal) | `ACTIONABLE`, `AWAITING_USER` |
| Agent proposal `requested_case_transition` | any cell marked `Y`; `G1`/`G2` require the guard to pass independently of the request |
| Authenticated user via API | `RESOLVED`, `BLOCKED`, `AWAITING_USER`, `REOPENED` (G1 waived — an explicit user request is itself qualifying) |
| Action executor after a successful send | `IN_PROGRESS`, `WAITING` |

An agent request is never sufficient on its own for a guarded transition. That is invariant 4 applied to state, not just to outbound side effects.

### 5.3 The qualifying-evidence test (G1)

`RESOLVED → REOPENED` is the single most consequential transition in the product — it is what "the move that never really ended" means — and it is also the one most likely to fire spuriously on a marketing email. The test is a conjunction; all five must hold.

```python
def qualifies_for_reopen(case: CaseRow, plan: ChangePlan,
                         snap: AggregateSnapshot, tx_now: datetime,
                         cfg: KernelConfig) -> tuple[bool, ReasonCode]:

    # Q1 — at least one evidence item never before linked to this case
    new_ev = set(plan.evidence_ids) - snap.evidence_ids_linked_to_case
    if not new_ev:
        return False, R.CASE_REOPEN_REFUSED_NON_QUALIFYING

    # Q2 — record-time freshness: learned AFTER the case was resolved.
    #      Valid time may be old (late-arriving evidence, rule T4) — that is fine
    #      and in fact expected — but re-importing an artifact we already had
    #      must never reopen anything.
    if not any(snap.evidence[e].created_at > case.resolved_at for e in new_ev):
        return False, R.CASE_REOPEN_REFUSED_NON_QUALIFYING

    # Q3 — the new evidence must have DONE something canonical
    material = (
        any(c.severity in ("MEDIUM", "HIGH", "CRITICAL") and c.case_id == case.id
            for c in plan.conflicts)                                          # (a)
        or any(d.status_before in ("FULFILLED", "EXPIRED")
               and d.status_after in ("ACTIVE", "PARTIAL", "DISPUTED")
               for d in plan.commitment_deltas)                               # (b)
        or any(t.new_state == "FIRED" and t.predicate_result is True
               for t in plan.trigger_deltas)                                  # (c)
        or any(c.claim_kind in ("USER_CLAIM", "CORRECTION")
               and c.disputes_case_belief for c in plan.claims)               # (d)
    )
    if not material:
        return False, R.CASE_REOPEN_REFUSED_NON_QUALIFYING

    # Q4 — artifact-level dedupe (defence in depth over step 6)
    if any(a.content_sha256 in snap.artifact_hashes_linked_to_case
           for a in plan.artifacts):
        return False, R.ARTIFACT_CONTENT_DUPLICATE

    # Q5 — flapping guard
    if case.reopened_count >= cfg.max_reopens:
        return False, R.CASE_REOPEN_LIMIT_REACHED     # -> NEEDS_HUMAN, not reopen

    return True, R.CASE_REOPENED_QUALIFYING_EVIDENCE
```

On success the kernel writes, in the same transaction:

```sql
UPDATE cases
   SET status           = 'REOPENED',
       reopened_count   = reopened_count + 1,
       attention_level  = 'URGENT',
       last_activity_at = $tx_now,
       updated_at       = $tx_now,
       revision         = revision + 1
       -- resolved_at is deliberately NOT cleared: when the case was
       -- previously resolved is a historical fact, and Q2 depends on it.
 WHERE id = $1 AND revision = $2 AND status = 'RESOLVED';
```

Q5 failing does not discard the evidence. The claim, the conflict, and the belief version are all still written; only the status transition is withheld, `attention_level` becomes `ATTENTION`, and the decision carries `CASE_REOPEN_LIMIT_REACHED`. A case that has reopened five times is a case that needs a person, not a sixth automatic reopen.

### 5.4 Hero-scenario evaluation of G1

| Test | Value | Pass |
|---|---|---|
| Q1 new evidence | `{ev_isp_003, ev_isp_004, ev_isp_005}`, none linked to `case_isp_cancel` | ✅ |
| Q2 record-time | `ev_isp_003.created_at = 2026-09-05T13:12Z > resolved_at 2026-06-02T13:00Z` | ✅ |
| Q3 material | `cf_2201` severity `HIGH` on `case_isp_cancel` → branch (a) | ✅ |
| Q4 dedupe | `9f3c…a1` not among artifacts linked to this case | ✅ |
| Q5 flapping | `reopened_count = 0 < 5` | ✅ |

→ legal. `RESOLVED → REOPENED`, revision `7 → 8`, `reopened_count 0 → 1`, reason `CASE_REOPENED_QUALIFYING_EVIDENCE`.

Negative control that must also be in the fixture set: an ISP *marketing* email arriving the same day. It produces new evidence (Q1 ✅) recorded after resolution (Q2 ✅) but no conflict, no commitment change, no trigger, no user dispute (Q3 ❌). The case stays `RESOLVED`, revision unchanged, decision `ACCEPTED` with `CASE_REOPEN_REFUSED_NON_QUALIFYING`. Without Q3 this scenario reopens the case and the product looks broken to a judge in the first thirty seconds.

---

## 6. Aggregate revision rule

### 6.1 The six rules

**R1 — exactly one increment per canonical commit, per aggregate.** A commit that changes canonical state increments `cases.revision` by exactly 1, regardless of how many rows it touches. Ten belief versions and three conflicts in one transaction still yield `revision + 1`.

**R2 — no increment on a no-op.** If `ChangePlan.is_canonical_noop()` the kernel writes **no** `state_transitions` row, **no** `outbox_events` row, and does **not** touch `cases`. It still writes a `kernel_decisions` row (audit is not optional) and updates `memory_proposals.status`, and returns `NOOP_DUPLICATE` with reason `NO_CANONICAL_CHANGE`. `kernel_decisions.case_revision_before == case_revision_after` is the machine-readable signature of a no-op.

**R3 — every transition and event in the commit carries the *new* revision.** `state_transitions.case_revision = new_revision` and `outbox_events.aggregate_version = new_revision` for every row written in that commit. This is what makes `UNIQUE (case_id, case_revision, transition_type, id)` meaningful and what lets a consumer order events without a global clock.

**R4 — the update is written with an optimistic predicate.**

```sql
UPDATE cases SET revision = revision + 1, updated_at = $tx_now, ...
 WHERE id = $case_id AND revision = $revision_read_inside_this_tx;
```

Under `SERIALIZABLE` the read-then-write is already protected, so this predicate is technically redundant. It is required anyway: it converts a subtle isolation regression into a loud, immediate `OPTIMISTIC_REVISION_MISMATCH` (0 rows updated → raise → retry path). Defence in depth costs one `AND` clause.

**R5 — commitments carry their own revision, under the same rule.** A fulfillment increments both `commitments.revision` and the parent `cases.revision`. `commitments.revision` guards commitment-scoped optimistic updates; `cases.revision` is the one that binds `action_intents.basis_case_revision`, because approval staleness is a case-level concern.

**R6 — replay returns the stored result.** A second submission of the same `proposal_id` does not re-execute. Step 6 looks up `kernel_decisions` by `proposal_id`, and if a row exists returns the reconstructed `KernelCommitResult` with `NOOP_DUPLICATE` / `PROPOSAL_ALREADY_DECIDED`. The revision does not move.

### 6.2 What counts as a canonical change

| Table | Canonical? | Note |
|---|---|---|
| `claims` | **yes** | Admitting a claim is a memory change even if no belief moves. |
| `belief_versions`, `belief_support` | **yes** | |
| `conflicts` | **yes** | Including an `AUTO_RESOLVED` conflict. |
| `commitments`, `fulfillments` | **yes** | |
| `cases` (status, `attention_level`, `reopened_count`) | **yes** | `last_activity_at` alone is **not** canonical — see below. |
| `prospective_triggers` (state or predicate change) | **yes** | `last_evaluated_at` alone is **not**. |
| `relationships` | **yes** | |
| `state_transitions`, `outbox_events` | consequences | Written because of a canonical change; never the reason for one. |
| `kernel_decisions`, `memory_proposals`, `agent_runs` | no | Audit and workflow metadata. |
| `processed_events`, `idempotency_records` | no | Infrastructure. |
| `source_artifacts`, `evidence_items` | no (see note) | Registered *before* the kernel, on the ingestion path. Admitting evidence is not a kernel commit. |

A trigger evaluation that finds its predicate `FALSE` touches only `last_evaluated_at` and `last_result`. That is a no-op: no revision, no transition, no event. This is invariant I8 ("scheduler says look now; memory says act or no-op") expressed as a revision rule, and it is what stops a daily scheduler from inflating every case's revision and invalidating every pending approval.

**Rule R7 — one case per proposal.** A `MemoryProposal` binds to exactly one `case_id`. A proposal implying writes to two cases is `REJECTED_INVARIANT` with `INVARIANT_MULTI_CASE_PROPOSAL`; the ingestion graph must split it upstream. This keeps R1 unambiguous, keeps the transaction footprint to one aggregate, and keeps serialization contention low.

### 6.3 Test assertions

```python
def test_noop_does_not_increment(kernel, seeded_case):
    before = seeded_case.revision
    r1 = kernel.submit(proposal_fixture("isp_invoice"))
    r2 = kernel.submit(proposal_fixture("isp_invoice"))     # identical proposal_id
    assert r1.decision == "ACCEPTED_WITH_CONFLICT"
    assert r1.case_revision_after == before + 1
    assert r2.decision == "NOOP_DUPLICATE"
    assert r2.case_revision_after == r1.case_revision_after   # unchanged
    assert ReasonCode.PROPOSAL_ALREADY_DECIDED in r2.reason_codes

def test_all_artifacts_of_one_commit_share_the_new_revision(db, result):
    rev = result.case_revision_after
    assert {t.case_revision for t in db.transitions(result.kernel_decision_id)} == {rev}
    assert {e.aggregate_version for e in db.outbox(result.kernel_decision_id)} == {rev}
```

---

## 7. Serialization retry contract

### 7.1 What is retryable

| SQLSTATE | Meaning | Kernel behavior |
|---|---|---|
| `40001` | serialization failure — all CockroachDB retry reasons (`RETRY_SERIALIZABLE`, `RETRY_WRITE_TOO_OLD`, `RETRY_ASYNC_WRITE_FAILURE`, `ABORT_REASON_*`, `ReadWithinUncertaintyInterval`) | retry the **entire** callback from fresh reads |
| `40003` | statement completion unknown | do **not** blindly retry; check `kernel_decisions` for `proposal_id`, then retry or return the stored result |
| `25P02` | in failed transaction | rollback, then treat as `40001` |
| `23505` | unique violation | **not** retryable — §7.5 mapping |
| `23514` | check violation | not retryable → `REJECTED_INVARIANT` |
| `57014` | statement timeout | not retryable → `RETRYABLE_CONCURRENCY` and enqueue |

Never inspect the error message string. Match on `sqlstate`.

### 7.2 The retry loop

```python
# provenance_db/retry.py
import asyncio, random
from psycopg import errors as pgerr

RETRYABLE = {"40001", "25P02"}

async def run_in_serializable_tx(pool, callback, cfg: KernelConfig, telemetry):
    """`callback(conn, tx_now)` MUST be free of side effects outside this
    connection, and MUST re-read every row it depends on. It is called once
    per attempt and its previous return value is never reused."""
    last: Exception | None = None
    for attempt in range(1, cfg.max_tx_attempts + 1):
        try:
            async with pool.connection() as conn:
                await conn.set_isolation_level("SERIALIZABLE")
                token = _IN_KERNEL_TX.set(True)
                try:
                    async with conn.transaction():
                        tx_now = await conn.fetchval("SELECT transaction_timestamp()")
                        result = await callback(conn, tx_now)   # steps 4–27
                finally:
                    _IN_KERNEL_TX.reset(token)
            telemetry.observe("kernel_tx_retries_total", attempt - 1)
            return result.with_retry_count(attempt - 1)

        except pgerr.Error as e:
            if e.sqlstate not in RETRYABLE:
                raise
            last = e
            telemetry.increment("kernel_tx_retry", {"sqlstate": e.sqlstate,
                                                    "attempt": attempt})
            if attempt == cfg.max_tx_attempts:
                break
            delay_ms = min(cfg.retry_base_delay_ms * (2 ** (attempt - 1)),
                           cfg.retry_max_delay_ms)
            await asyncio.sleep(random.uniform(0.5 * delay_ms, 1.5 * delay_ms) / 1000)

    telemetry.increment("kernel_tx_retry_exhausted")
    raise RetryExhausted(cfg.max_tx_attempts, last)
```

Backoff schedule with `base = 50ms`, `cap = 2000ms`: attempt 1 → 25–75 ms, 2 → 50–150 ms, 3 → 100–300 ms, 4 → 200–600 ms, then give up. Worst-case added latency before exhaustion is ~1.1 s, which keeps the synchronous ingestion API inside a sane p99.

Jitter is `uniform(0.5×d, 1.5×d)`, not `uniform(0, d)`. Two kernel writers colliding on one case is the expected contention pattern (payment feed plus email import); symmetric jitter around the target keeps both from collapsing toward zero delay and re-colliding.

### 7.3 The five rules the callback must obey

1. **Side-effect-free outside the connection.** No Bedrock, no S3, no EventBridge, no SES, no MCP, no logging of computed state that a downstream reader treats as authoritative. Enforced by `_IN_KERNEL_TX` (§1.3).
2. **Fresh reads every attempt.** Re-run steps 4–16 inside the callback. Do not close over a `ChangePlan`, an `AggregateSnapshot`, a revision number, or a `ConflictFinding` computed before the loop.
3. **No reuse of derived state from a failed attempt.** `ChangePlan` is `frozen=True` and constructed inside the callback; a retry constructs a new one. Reusing a plan computed against a rolled-back snapshot is exactly how "impossible partial aggregate state" gets written.
4. **Deterministic UUIDs are forbidden across attempts.** Generate new UUIDs each attempt. Idempotency comes from `proposal_id` and the unique constraints, not from stable primary keys.
5. **The agent is never asked to reason about retries.** `retry_count` appears in `kernel_decisions` and in telemetry. It never appears in a prompt.

### 7.4 After the cap

The Kernel does **not** enqueue its own re-drive. The control-plane task role deliberately carries no `sqs:*` permission (`ops/40_INFRA_IAC.md` §8.3), and there is no `provenance-kernel-retry-queue` among the queues in `ops/40_INFRA_IAC.md` §6.2. A self-enqueue would fail with `AccessDenied` outside the transaction, leaving `memory_proposals.status = 'SUBMITTED'` forever and a UI that says "queued" about something that is not queued. Re-drive is the **caller's** responsibility, over the HTTP contract that already exists.

```python
try:
    result = await run_in_serializable_tx(pool, kernel_callback, cfg, telemetry)
except RetryExhausted:
    # No enqueue. No side effect of any kind. The caller re-drives.
    result = KernelCommitResult.retryable_concurrency(
        proposal_id=proposal.id, trace_id=proposal.trace_id,
        reason_codes=[R.RETRYABLE_CONCURRENCY, R.RETRY_EXHAUSTED_NOT_ENQUEUED])
```

The HTTP layer maps this to `503 RETRYABLE_CONCURRENCY` with `Retry-After: 1` (`specs/15_API_SPEC.md` §4.3), whose stated contract is already that "the client should retry the identical request with the identical `Idempotency-Key`." For `POST /internal/v1/memory/proposals` the caller is the ingestion graph's submit step, which retries under its own bounded policy; for a `TRIGGER_EVALUATION` proposal the caller is the wake worker, whose SQS message returns to the queue and is redelivered by the queue that *does* exist (`provenance-worker-dlq`'s source queue), with `provenance-scheduler-dlq` as the terminal sink.

Re-drive is safe because R6 makes replay a lookup: if the earlier attempt in fact committed (the `40003` case), the replay finds the `kernel_decisions` row and returns `NOOP_DUPLICATE`. The proposal stays in `SUBMITTED` until a re-drive lands, and the UI shows "queued behind a concurrent update" only while a re-drive is genuinely outstanding.

### 7.5 Unique-violation mapping

`23505` is never retried. It is a deterministic statement about what already exists, so it is mapped by constraint name:

| Constraint | Meaning | Decision | Reason code |
|---|---|---|---|
| `source_artifacts_tenant_user_sha_type_key` | same bytes already ingested | `NOOP_DUPLICATE` | `ARTIFACT_CONTENT_DUPLICATE` |
| `source_artifacts_tenant_user_message_id_key` | same email already ingested | `NOOP_DUPLICATE` | `ARTIFACT_CONTENT_DUPLICATE` |
| `fulfillments_commitment_evidence_key` | same evidence already applied | `NOOP_DUPLICATE` | `FULFILLMENT_EVIDENCE_DUPLICATE` |
| `belief_versions_belief_version_no_key` | concurrent version race | *retry as if `40001`* | `RETRYABLE_CONCURRENCY` |
| `belief_support_unique` | duplicate grounding edge in one plan | `REJECTED_INVARIANT` | `INVARIANT_DUPLICATE_SUPPORT_EDGE` |
| `beliefs_subject_predicate_key` | Rule N1 violated (two beliefs, one family) | `REJECTED_INVARIANT` | `INVARIANT_BELIEF_IDENTITY` |
| `action_intents_idempotency_key_key` | duplicate action | `NOOP_DUPLICATE` | `ACTION_IDEMPOTENCY_REPLAY` |
| `state_transitions_case_revision_key` | R3 violated | `REJECTED_INVARIANT` | `INVARIANT_REVISION_NOT_MONOTONIC` |
| *(any other)* | unknown | `REJECTED_INVARIANT` | `INVARIANT_UNIQUE_VIOLATION` |

`belief_versions_belief_version_no_key` is the one unique violation treated as retryable: it means another commit inserted `version_no = n+1` between our read and our write, which is a serialization race wearing a different error code, and a fresh read resolves it.

### 7.6 Concurrency test (required)

`02_DATA_MEMORY_TRANSACTIONS.md` §20 test 10, made concrete: fire the moving-company `$200` payment evidence and the ISP invoice evidence at the same case aggregate concurrently, 50 iterations, `asyncio.gather`. Assertions after every iteration:

- final `cases.revision == initial + 2` (both commits landed, exactly once each);
- `outstanding == committed − Σ admitted` holds exactly;
- no commitment is `FULFILLED` with `outstanding > 0`;
- `state_transitions` for that case have contiguous, gapless `case_revision` values;
- `Σ kernel_decisions.retry_count > 0` at least once across the 50 iterations — if it is always zero, the test is not actually contending and proves nothing.

---

## 8. Bitemporal rules

Two clocks. **Valid time** `[valid_from, valid_to)` is when a statement is true in the world. **Record time** `recorded_at` / `created_at` is when Provenance learned it. Sorting by record time alone produces confidently wrong answers, which is the failure mode of every RAG-over-your-inbox product.

### 8.1 T1 — record time never substitutes for valid time

`recorded_at`, `created_at`, `detected_at`, and `superseded_at` are assigned from `tx_now` (§1.4) — never from model output, never from an email header, never from a client. `valid_from` / `valid_to` come only from evidence content or from a deterministic derivation.

Enforcement: the `MemoryProposal` Pydantic model has **no** `recorded_at` field. There is nothing for a model to populate. Any inbound JSON containing one is dropped by `model_config = ConfigDict(extra="ignore")` and counted in telemetry.

### 8.2 T2 — unknown validity stays unknown

When evidence gives no trustworthy effective date, `valid_from` and `valid_to` are `NULL` and `object_json.validity_basis = 'UNKNOWN'`. The kernel never invents a date and never falls back to `received_at`.

Three `validity_basis` values, and their consequences:

| `validity_basis` | Meaning | Participates in overlap matching? | Can create a conflict? |
|---|---|---|---|
| `EXPLICIT` | both bounds stated or derivable | yes | yes |
| `EXPLICIT_OPEN` | lower bound stated, open-ended ("terminated 31 May") | yes; upper bound `+∞` | yes |
| `UNKNOWN` | no trustworthy date | **no** | **no** — `QUALIFIES` edge only, reason `VALIDITY_UNKNOWN_NOT_COMPARABLE` |

This is the highest-value rule in this section. "We processed your refund" with no date is *not* evidence that the refund happened during any particular window, and treating it as `[-∞, +∞)` would make it conflict with everything.

### 8.3 T3 — supersession requires authority

A claim that explicitly supersedes an earlier commitment or belief effective from `T` starts a new version at `T`, and may close the prior version's `valid_to` at `T`, only if **both** hold:

1. `challenger.authority ≥ supersession_authority_floor` (0.80) for that family;
2. the challenger's `actor_ref` is the same actor as the incumbent's strongest `SUPPORTS` source, **or** the challenger's authority strictly exceeds it.

Otherwise the prior version's `valid_to` is left untouched and the disagreement is routed through the normal contradiction path (§2–§3). Reason code when the floor is missed: `SUPERSESSION_AUTHORITY_INSUFFICIENT`.

Rationale: an actor may amend their own statements; a third party may not silently rewrite the validity of somebody else's.

### 8.4 T4 — late-arriving evidence may create history without changing the present

Evidence recorded now may describe a window entirely in the past. It creates a **historical belief version** and does **not** move `beliefs.current_version_id`.

```python
def is_historical(new_version, tx_now) -> bool:
    return new_version.valid_to is not None and new_version.valid_to <= tx_now
```

A historical version:

- is inserted with the next `version_no` (record-time ordering — `version_no` is a lineage sequence, not a validity sequence);
- has `superseded_at = NULL` — it was never superseded, it simply does not cover now;
- carries `LATE_ARRIVING_HISTORICAL_VERSION` in the kernel decision;
- **does** count as a canonical change (revision increments, transition and event written);
- **does not** update `beliefs.current_version_id`.

### 8.5 Current-version selection

`beliefs.current_version_id` is a denormalized pointer, recomputed inside the transaction after every belief write:

```sql
SELECT id
FROM belief_versions
WHERE belief_id = $1
  AND superseded_at IS NULL
  AND epistemic_status <> 'RETRACTED'
  AND (valid_from IS NULL OR valid_from <= $tx_now)
  AND (valid_to   IS NULL OR valid_to   >  $tx_now)
ORDER BY valid_from DESC NULLS LAST, recorded_at DESC, version_no DESC
LIMIT 1;
```

If the query returns no row, `current_version_id` is set to `NULL` — a belief with no version covering now is a legitimate state ("we know what was true in June; we know nothing about today"), and forcing a stale pointer would be a lie the State Proof would faithfully render.

### 8.6 Two structural invariants

**G4 — at most one live version per instant.** For any belief and any instant `t`, at most one `belief_versions` row with `superseded_at IS NULL` and `epistemic_status <> 'RETRACTED'` has an interval containing `t`. Maintained by the supersession discipline: any new version whose interval materially overlaps a live version's interval must set that version's `superseded_at`, or the plan is rejected with `INVARIANT_OVERLAPPING_LIVE_VERSIONS`.

**G5 — only `superseded_at` is ever updated on an existing version row.** `value_json`, `epistemic_status`, `belief_confidence`, `valid_from`, `valid_to`, and `recorded_at` are write-once. Correcting any of them means writing a new version, which is invariant 2 stated as a column-level rule.

### 8.7 Worked example — late-arriving evidence, both shapes

Scenario L runs the hero universe with the ingestion order reversed, which is `MEMORY_SYSTEM.md` §29.4 made concrete.

**L-1: `2026-09-05` — the invoice arrives first, with no prior service-status belief.**

| | valid time | record time | authority |
|---|---|---|---|
| `cl_003` `balance_owed USD 186.00` | `[2026-09-05, ∞)` `EXPLICIT_OPEN` | `2026-09-05T13:12Z` | 0.90 (`BALANCE`) |
| entailed `P_e` `SERVICE_STATUS ACTIVE` (EN-1) | `[2026-06-01T04:00Z, 2026-07-01T04:00Z)` `EXPLICIT` | — | 0.88 − 0.30 = **0.58** |

No incumbent → disposition `NO_INCUMBENT`. Writes `b_isp_service` v1 = `{"state":"ACTIVE"}`, `PROBABLE` (0.58 < 0.90 confirmed floor), grounded `SUPPORTS → cl_003 (EN-1)`. `current_version_id = v1`? The interval ends `2026-07-01T04:00Z`, which is before `tx_now` — so by §8.4 this is **already historical on arrival**, `current_version_id` stays `NULL`, and the decision carries `LATE_ARRIVING_HISTORICAL_VERSION`. Provenance now knows something about June and nothing about September. That is the honest state.

**L-2: `2026-09-08` — the user forwards the 15 May confirmation that was never imported.**

| | valid time | record time | authority |
|---|---|---|---|
| `cl_isp_002` `service_terminated` | `[2026-06-01T04:00Z, ∞)` `EXPLICIT_OPEN` | `2026-09-08T10:30Z` | **0.88** (direct, no penalty) |

Matcher M1: same subject, same family, overlap `[2026-06-01T04:00Z, 2026-07-01T04:00Z)` = 30 days, `ACTIVE ≠ TERMINATED` → `VALUE_CONFLICT`.
Disposition: no human gate applies (`SERVICE_STATUS` is non-monetary; challenger is a `COUNTERPARTY_CLAIM` forwarded by the user, not a `USER_CLAIM`); `Δ = 0.58 − 0.88 = −0.30`, `abs(Δ) = 0.30 ≥ 0.25`, winner authority `0.88 ≥ 0.80` → **`PROMOTE_CHALLENGER_AUTO`**, reason `AUTO_RESOLVED_ENTAILMENT_PENALTY`.

Resulting lineage — note that record-time order and valid-time truth diverge, which is the entire point:

| version | value | valid interval | `recorded_at` | `superseded_at` | current? |
|---|---|---|---|---|---|
| `v1` | `ACTIVE` | `[2026-06-01T04:00Z, 2026-07-01T04:00Z)` | `2026-09-05T13:12Z` | `2026-09-08T10:30Z` | no |
| `v2` | `TERMINATED` | `[2026-06-01T04:00Z, ∞)` | `2026-09-08T10:30Z` | `NULL` | **yes** |

`v2` grounding: `SUPPORTS → cl_isp_002 (0.88)`, `CONTRADICTS → cl_003 (0.58, reason EN-1)`. The `balance_owed` belief flips to `DISPUTED` in the same commit, the case moves to `DISPUTED`, revision increments once.

**L-3: pure T4 — history that must not touch the present.** On `2026-09-12` the user imports an ISP letter dated `2026-05-02`: "your service is active for the May billing period." Valid interval `[2026-05-01T04:00Z, 2026-06-01T04:00Z)`, `EXPLICIT`.

Overlap with `v2` (`[2026-06-01T04:00Z, ∞)`) is **zero** — the day-boundary convention makes the intervals abut exactly, and `[a,b)` half-open semantics mean abutting is not overlapping. `material_overlap` returns `None`. **No conflict.** The kernel writes `v3` = `{"state":"ACTIVE"}`, valid `[2026-05-01T04:00Z, 2026-06-01T04:00Z)`, `superseded_at = NULL`, and leaves `current_version_id = v2`.

Provenance now holds a coherent history — active through May, terminated from June — assembled from three artifacts that arrived in an order that matches none of it. A system sorting by `received_at` would conclude the service is active. Reason code: `LATE_ARRIVING_HISTORICAL_VERSION`.

This example is also the reason §2.4's day-boundary convention is non-negotiable: off by one day and L-3 produces a spurious 24-hour `VALUE_CONFLICT` and a reopened case for a letter that agrees with everything.

---

## 9. `KernelCommitResult` and the reason-code catalogue

### 9.1 Construction

`KernelCommitResult` is assembled **after** the transaction commits, from the `ChangePlan` and the ids the write path returned. It is never partially constructed inside the transaction, because a rollback must leave no trace of a result that did not happen.

```python
# provenance_domain/kernel/result.py
class KernelCommitResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    proposal_id: UUID
    kernel_decision_id: UUID | None            # None only for pre-TX rejections
    trace_id: UUID
    decision: Decision                          # §9.4
    case_id: UUID | None
    case_revision_before: int | None
    case_revision_after: int | None
    created_belief_version_ids: list[UUID] = []
    superseded_belief_version_ids: list[UUID] = []
    created_or_updated_conflict_ids: list[UUID] = []
    commitment_changes: list[CommitmentChangeView] = []
    trigger_changes: list[TriggerChangeView] = []
    state_transition_ids: list[UUID] = []
    outbox_event_ids: list[UUID] = []
    attention_required: bool = False
    requires_human_review: bool = False
    reason_codes: list[ReasonCode] = []
    retry_count: int = 0
```

Construction rules:

1. `decision` is derived, never passed in:

```python
def derive_decision(plan, rejections, pending) -> Decision:
    if rejections:                       return rejections[0].decision   # first wins
    if pending:                          return pending[0].decision
    if plan.is_canonical_noop():         return Decision.NOOP_DUPLICATE
    if plan.conflicts:                   return Decision.ACCEPTED_WITH_CONFLICT
    return Decision.ACCEPTED
```

2. `reason_codes` is ordered: rejection/pending codes first, then conflict codes, then commitment codes, then case codes, then trigger codes, then informational. Deduplicated preserving first occurrence. Order is stable so golden-file tests can assert on it.
3. `attention_required = (cases.attention_level != 'NONE')` after the commit.
4. `requires_human_review = any(c.requires_human for c in plan.conflicts)`.
5. `retry_count` comes from the retry loop and is written to `kernel_decisions.retry_count` in the *successful* attempt.
6. On a rejection before step 17, `kernel_decision_id` is `None`, `case_revision_after` is `None`, and no `kernel_decisions` row exists — nothing was decided in the durable sense. On a rejection at steps 17–27 the transaction is rolled back and the kernel writes a decision row in a **second, tiny transaction** so the rejection is auditable: `INSERT INTO kernel_decisions … decision='REJECTED_INVARIANT'` plus `UPDATE memory_proposals SET status=…`. That second transaction touches no aggregate, so R2 holds.
7. The result is what step 30 returns and what the ingestion graph records as its terminal node in the Memory Trace.

### 9.2 The hero result, verbatim

```json
{
  "schema_version": "1.0",
  "proposal_id": "0198f2a1-7c31-7c4a-9f10-3ac6b1d20881",
  "kernel_decision_id": "0198f2a1-7c42-7f0b-a2c1-9dd41e5510aa",
  "trace_id": "0198f2a1-7b90-7d55-8c33-11ab90c4e001",
  "decision": "ACCEPTED_WITH_CONFLICT",
  "case_id": "0198e110-4a10-7b21-9c02-77ac31d5c0de",
  "case_revision_before": 7,
  "case_revision_after": 8,
  "created_belief_version_ids": [
    "0198f2a1-7c50-7a11-8b09-2ef4c0a91120",
    "0198f2a1-7c51-7a12-8b0a-2ef4c0a91121"
  ],
  "superseded_belief_version_ids": ["0198e110-5511-7c00-9a31-40bb2c118801"],
  "created_or_updated_conflict_ids": ["0198f2a1-7c60-7d22-9c11-58aa30f22201"],
  "commitment_changes": [],
  "trigger_changes": [
    {"trigger_id": "0198f2a1-7c70-7e33-8d20-61bc41a33301",
     "from_state": null, "to_state": "ARMED",
     "not_before": "2026-09-19T13:12:44.118Z"}
  ],
  "state_transition_ids": [
    "0198f2a1-7c80-7f44-9e31-72cd52b44401",
    "0198f2a1-7c81-7f45-9e32-72cd52b44402",
    "0198f2a1-7c82-7f46-9e33-72cd52b44403",
    "0198f2a1-7c83-7f47-9e34-72cd52b44404"
  ],
  "outbox_event_ids": [
    "0198f2a1-7c90-7a55-8f40-83de63c55501",
    "0198f2a1-7c91-7a56-8f41-83de63c55502"
  ],
  "attention_required": true,
  "requires_human_review": false,
  "reason_codes": [
    "CONFLICT_VALUE_MUTUAL_EXCLUSION",
    "AUTO_RESOLVED_ENTAILMENT_PENALTY",
    "BELIEF_RETAINED_UNDER_CONTRADICTION",
    "BELIEF_MARKED_DISPUTED",
    "CASE_REOPENED_QUALIFYING_EVIDENCE",
    "TRIGGER_ARMED"
  ],
  "retry_count": 0
}
```

`requires_human_review` is `false` while `attention_required` is `true`, and that distinction is the product. The *memory* decision was deterministic and needs no human. The *action* it enables — sending a dispute to the ISP — requires explicit approval under invariant 4, and that gate lives in `action_intents`, not here.

This object is also the Judge Mode "Memory ON" payload (canon addition A). With retrieval and canonical memory disabled, the same artifact yields `decision: ACCEPTED`, zero conflicts, zero belief versions, `case_revision_after: null`, and a single claim — "Invoice for $186 due 30 June." Same input, same code path, memory toggled. The diff *is* the demo.

### 9.3 Reason-code catalogue

Every code is a member of `ReasonCode(str, Enum)` in `provenance_domain/kernel/reasons.py`. "Step" is the pipeline step from §1.2. "User-visible" means it can appear in the UI timeline or State Proof; the rest are audit and telemetry only.

| Code | Step | Decision context | User-visible | What the user or engineer should do |
|---|---|---|---|---|
| `SCHEMA_VERSION_UNSUPPORTED` | 1 | `REJECTED_SCHEMA` | no | Agent runtime is on a stale contract version; redeploy. |
| `SCHEMA_FIELD_MISSING` | 1 | `REJECTED_SCHEMA` | no | Structured-output repair; one retry then fail the run. |
| `SCHEMA_TYPE_INVALID` | 1 | `REJECTED_SCHEMA` | no | Same as above. |
| `PROPOSAL_TOO_LARGE` | 1 | `REJECTED_SCHEMA` | no | Interpreter emitted an unbounded list; cap upstream. |
| `PRINCIPAL_USER_MISMATCH` | 3 | `REJECTED_INVALID_PROVENANCE` | no | Security event. Alarm. |
| `TENANT_MISMATCH` | 3 | `REJECTED_INVALID_PROVENANCE` | no | Security event. Alarm. |
| `EVIDENCE_NOT_FOUND` | 4 | `REJECTED_INVALID_PROVENANCE` | no | Agent referenced an id it invented. |
| `ARTIFACT_NOT_FOUND` | 4 | `REJECTED_INVALID_PROVENANCE` | no | Same. |
| `EVIDENCE_FOREIGN_USER` | 5 | `REJECTED_INVALID_PROVENANCE` | no | Cross-user leak attempt. Alarm. Required test 11. |
| `ARTIFACT_FOREIGN_USER` | 5 | `REJECTED_INVALID_PROVENANCE` | no | Same. |
| `EVIDENCE_ARTIFACT_MISMATCH` | 5 | `REJECTED_INVALID_PROVENANCE` | no | Evidence does not belong to a cited artifact. |
| `CLAIM_EVIDENCE_UNLINKED` | 10 | `REJECTED_INVALID_PROVENANCE` | no | A claim cited no evidence. Invariant 1. |
| `SOURCE_RETRACTED_EXCLUDED` | 5, 11 | informational | yes | Corrected evidence was excluded from matching (addition C). |
| `PROPOSAL_ALREADY_DECIDED` | 6 | `NOOP_DUPLICATE` | no | Replay; stored result returned. |
| `ARTIFACT_CONTENT_DUPLICATE` | 6, Q4 | `NOOP_DUPLICATE` | yes | "You already forwarded this." |
| `CLAIM_SEMANTIC_DUPLICATE` | 10 | informational | no | Same predicate/value/interval already admitted. |
| `FULFILLMENT_EVIDENCE_DUPLICATE` | 13 | `NOOP_DUPLICATE` | yes | Payment already applied. |
| `NO_CANONICAL_CHANGE` | 16 | `NOOP_DUPLICATE` | no | R2 no-op; revision unchanged. |
| `IDENTITY_UNRESOLVED` | 7 | `PENDING_IDENTITY` | yes | "Which account is this about?" |
| `IDENTITY_AMBIGUOUS_MULTI_CASE` | 7 | `PENDING_IDENTITY` | yes | Same, with candidates listed. |
| `IDENTITY_CONFIDENCE_BELOW_FLOOR` | 7 | `PENDING_IDENTITY` | yes | Top candidate < 0.90 or gap < 0.15. |
| `RELATIONSHIP_NOT_FOUND` | 7 | `PENDING_IDENTITY` | yes | Offer to create a relationship. |
| `CASE_NOT_IN_RELATIONSHIP` | 7 | `REJECTED_INVARIANT` | no | Agent proposed a mismatched pair. |
| `CASE_TERMINAL_SUPERSEDED` | 8 | `REJECTED_INVARIANT` | yes | Route to the surviving case. |
| `VALIDITY_UNKNOWN_NOT_COMPARABLE` | 9 | informational | yes | T2: dateless statement cannot conflict. |
| `VALIDITY_INVERTED` | 9 | `REJECTED_SCHEMA` | no | `valid_to <= valid_from`. Extractor bug. |
| `VALIDITY_FUTURE_BEYOND_HORIZON` | 9 | `REJECTED_SCHEMA` | no | Date > 10 years out; almost always a parse error. |
| `LATE_ARRIVING_HISTORICAL_VERSION` | 19 | informational | yes | T4: history recorded, present unchanged. |
| `SUPERSESSION_AUTHORITY_INSUFFICIENT` | 12 | informational | yes | T3 floor not met; incumbent's `valid_to` untouched. |
| `CONFLICT_VALUE_MUTUAL_EXCLUSION` | 11 | `ACCEPTED_WITH_CONFLICT` | yes | M1/M3/M8/M12. |
| `CONFLICT_TEMPORAL_OVERLAP` | 11 | `ACCEPTED_WITH_CONFLICT` | yes | M2. |
| `CONFLICT_AUTHORITY_TIE` | 11 | `ACCEPTED_WITH_CONFLICT` | yes | M13 upgrade. |
| `CONFLICT_CURRENCY_MISMATCH` | 11, 13 | `ACCEPTED_WITH_CONFLICT` | yes | M4/M7/M10 or §4.2 step 1. Never converted. |
| `CONFLICT_OVER_FULFILMENT` | 13 | `ACCEPTED_WITH_CONFLICT` | yes | Σ admitted > committed. |
| `CONFLICT_COMMITMENT_WITHDRAWAL` | 11 | `ACCEPTED_WITH_CONFLICT` | yes | M11. Always human. |
| `CONFLICT_PAYMENT_DENIAL` | 11 | `ACCEPTED_WITH_CONFLICT` | yes | M5: one side denies a payment the other asserts. |
| `CONFLICT_HINT_UNMAPPED_FAMILY` | 12 | informational | no | Model hinted at a predicate outside the v1 registry. |
| `AUTHORITY_UNMAPPED_SOURCE_CLASS` | 10 | informational | no | Unknown `source_class` → 0.10. |
| `AUTO_RESOLVED_AUTHORITY_MARGIN` | 12 | `ACCEPTED_WITH_CONFLICT` | yes | Δ ≥ 0.25 and winner ≥ 0.80. |
| `AUTO_RESOLVED_ENTAILMENT_PENALTY` | 12 | `ACCEPTED_WITH_CONFLICT` | yes | Direct statement beat an entailed one. |
| `AUTO_RESOLVED_TEMPORAL_PRECEDENCE` | 12 | `ACCEPTED_WITH_CONFLICT` | yes | Same actor, later valid_from, later record time. |
| `HUMAN_REQUIRED_AUTHORITY_TIE` | 12 | `PENDING_HUMAN_REVIEW` | yes | H1. |
| `HUMAN_REQUIRED_WITHDRAWAL` | 12 | `PENDING_HUMAN_REVIEW` | yes | H2. |
| `HUMAN_REQUIRED_USER_DISPUTE` | 12 | `PENDING_HUMAN_REVIEW` | yes | H4. |
| `HUMAN_REQUIRED_MONETARY_THRESHOLD` | 12 | `PENDING_HUMAN_REVIEW` | yes | H5, ≥ 100.00. |
| `HUMAN_REQUIRED_ACTION_BLOCKING` | 12 | `PENDING_HUMAN_REVIEW` | yes | H6/H7. Revalidate the pending action. |
| `HUMAN_REQUIRED_UNRESOLVABLE_TYPE` | 12 | `PENDING_HUMAN_REVIEW` | yes | H8, fail-closed. |
| `BELIEF_RETAINED_UNDER_CONTRADICTION` | 19 | informational | yes | New version, same value, `CONTRADICTS` edge added. |
| `BELIEF_SUPERSEDED_BY_CHALLENGER` | 19 | informational | yes | Challenger promoted; lineage preserved. |
| `BELIEF_MARKED_DISPUTED` | 19 | informational | yes | `epistemic_status = DISPUTED`, confidence decayed. |
| `BELIEF_CREATED` | 19 | informational | yes | First version of a belief. |
| `COMMITMENT_PARTIAL_RECOMPUTED` | 13 | informational | yes | "$220 still outstanding." |
| `COMMITMENT_FULFILLED` | 13 | informational | yes | outstanding == 0, no blocking conflict. |
| `COMMITMENT_EXPIRED` | 13 | informational | yes | `valid_to` passed. |
| `COMMITMENT_DISPUTED_EXCESS` | 13 | informational | yes | Over-fulfilment anomaly. |
| `FULFILLMENT_ADMITTED` | 13 | informational | yes | Payment applied to the ledger. |
| `FULFILLMENT_CURRENCY_REJECTED` | 13 | informational | yes | Wrong currency; not applied. |
| `CASE_REOPENED_QUALIFYING_EVIDENCE` | 14 | informational | yes | G1 passed. The headline event. |
| `CASE_REOPEN_REFUSED_NON_QUALIFYING` | 14 | informational | no | Q1/Q2/Q3 failed; case stays resolved. |
| `CASE_REOPEN_LIMIT_REACHED` | 14 | `PENDING_HUMAN_REVIEW` | yes | Q5; needs a person. |
| `CASE_TRANSITION_ILLEGAL` | 14 | `REJECTED_INVARIANT` | no | Matrix cell is `—`. |
| `CASE_TRANSITION_MULTIPLE_IN_COMMIT` | 14 | `REJECTED_INVARIANT` | no | Rule C1. |
| `TRIGGER_ARMED` | 15 | informational | yes | Prospective memory set. |
| `TRIGGER_DISARMED_RESOLVED` | 15 | informational | yes | Case resolved; trigger stood down. |
| `TRIGGER_FIRED_PREDICATE_TRUE` | 15 | informational | yes | The landlord-deposit reveal. |
| `TRIGGER_NOOP_PREDICATE_FALSE` | 15 | `NOOP_DUPLICATE` | no | I8: woke, checked, did nothing. |
| `TRIGGER_EXPIRED` | 15 | informational | yes | `expires_at` passed. |
| `INVARIANT_BELIEF_UNGROUNDED` | 16, 19 | `REJECTED_INVARIANT` | no | Grounding invariant. Required test 2. |
| `INVARIANT_OUTSTANDING_NEGATIVE` | 16 | `REJECTED_INVARIANT` | no | Arithmetic bug. |
| `INVARIANT_FULFILLED_STATUS_MISMATCH` | 16 | `REJECTED_INVARIANT` | no | `FULFILLED` with `outstanding > 0`. Required test 5. |
| `INVARIANT_REVISION_NOT_MONOTONIC` | 22 | `REJECTED_INVARIANT` | no | R3 violated. |
| `INVARIANT_OVERLAPPING_LIVE_VERSIONS` | 16 | `REJECTED_INVARIANT` | no | G4 violated. |
| `INVARIANT_DUPLICATE_SUPPORT_EDGE` | 19 | `REJECTED_INVARIANT` | no | Duplicate grounding edge in one plan. |
| `INVARIANT_BELIEF_IDENTITY` | 19 | `REJECTED_INVARIANT` | no | Rule N1 violated. |
| `INVARIANT_MULTI_CASE_PROPOSAL` | 16 | `REJECTED_INVARIANT` | no | Rule R7. Split upstream. |
| `INVARIANT_TENANT_LEAK` | 16 | `REJECTED_INVARIANT` | no | A plan row carried a foreign tenant. Alarm. |
| `INVARIANT_UNIQUE_VIOLATION` | 18–27 | `REJECTED_INVARIANT` | no | Unmapped `23505`. |
| `OPTIMISTIC_REVISION_MISMATCH` | 22 | retry | no | R4 guard tripped. |
| `RETRYABLE_CONCURRENCY` | 29 | `RETRYABLE_CONCURRENCY` | yes | "Queued behind a concurrent update." |
| `RETRY_EXHAUSTED_NOT_ENQUEUED` | 29 | `RETRYABLE_CONCURRENCY` | no | The Kernel performed no side effect. Re-drive is the caller's, over `503` + `Retry-After` (§7.4). |
| `ACTION_IDEMPOTENCY_REPLAY` | 18 | `NOOP_DUPLICATE` | no | Duplicate action intent. |

### 9.4 Decision values

Exactly nine, matching `memory_proposals.status` plus `RETRYABLE_CONCURRENCY`:

`ACCEPTED`, `ACCEPTED_WITH_CONFLICT`, `NOOP_DUPLICATE`, `PENDING_IDENTITY`, `PENDING_HUMAN_REVIEW`, `REJECTED_INVALID_PROVENANCE`, `REJECTED_INVARIANT`, `REJECTED_SCHEMA`, `RETRYABLE_CONCURRENCY`.

`RETRYABLE_CONCURRENCY` never persists to `memory_proposals.status`; the proposal remains `SUBMITTED` pending re-drive.

### 9.5 Conflict-type vocabulary reconciliation

`MEMORY_SYSTEM.md` §12.1 and `02_DATA_MEMORY_TRANSACTIONS.md` §11.2 use different casings for the same concepts. **The `02` set is canonical** and is what `conflicts.conflict_type` stores. The mapping, so nobody has to guess:

| `MEMORY_SYSTEM.md` §12.1 | Canonical `conflict_type` | In v1? |
|---|---|---|
| `mutually_exclusive_value` | `VALUE_CONFLICT` | yes |
| `amount_mismatch` | `VALUE_CONFLICT` | yes |
| `temporal_overlap` | `TEMPORAL_CONFLICT` | yes |
| `fulfillment_dispute` | `FULFILLMENT_CONFLICT` | yes |
| `commitment_withdrawal_conflict` | `COMMITMENT_WITHDRAWAL_CONFLICT` | yes |
| *(none)* | `AUTHORITY_CONFLICT` | yes (M13 only) |
| `actor_identity_conflict` | `IDENTITY_CONFLICT` | **no** — reserved; step 7 yields `PENDING_IDENTITY` |
| `policy_version_conflict` | `POLICY_VERSION_CONFLICT` | **no** — reserved, §2.10 item 1 |

Severity, computed deterministically, first match wins:

| Severity | Condition |
|---|---|
| `CRITICAL` | `monetary_exposure ≥ 1000.00`, or the conflict blocks an `APPROVED`/`EXECUTING` action |
| `HIGH` | `monetary_exposure ≥ 100.00`, or the incumbent's `epistemic_status == CONFIRMED`, or `conflict_type ∈ {AUTHORITY_CONFLICT, COMMITMENT_WITHDRAWAL_CONFLICT}` |
| `MEDIUM` | `max(authority) ≥ 0.60` |
| `LOW` | otherwise |

Severity gates the reopen test (Q3a requires `≥ MEDIUM`) and the UI sort order. It does **not** gate auto-resolution — that is §3.3's job alone, and keeping the two independent is what stops a severity heuristic from quietly becoming the resolution policy.

---

## 10. Deterministic test manifest

Every algorithm above must be provable with fixtures and no model. Minimum set, mapped to sections; these subsume `02_DATA_MEMORY_TRANSACTIONS.md` §20.

| # | Test | Asserts |
|---|---|---|
| 1 | `test_day_boundary_convention` | "terminated 31 May", tz `America/New_York` → `2026-06-01T04:00:00Z`. §2.4 |
| 2 | `test_en1_entailment_creates_service_active` | Invoice with June period yields a `SERVICE_STATUS ACTIVE` proposition at authority `base − 0.30`. §2.3 |
| 3 | `test_hero_forward_auto_resolves_to_incumbent` | `RETAIN_INCUMBENT_AUTO`, conflict `AUTO_RESOLVED`, `requires_human=false`, case `REOPENED`, revision `+1`. §1.6 |
| 4 | `test_hero_reversed_promotes_challenger` | L-2: `PROMOTE_CHALLENGER_AUTO`, v1 superseded, v2 current. §8.7 |
| 5 | `test_abutting_intervals_do_not_conflict` | L-3: `[…,Jun1T04Z)` vs `[Jun1T04Z,∞)` → no conflict, `current_version_id` unchanged. §8.7 |
| 6 | `test_unknown_validity_never_conflicts` | `validity_basis=UNKNOWN` → zero `conflicts` rows, `QUALIFIES` edge only. §8.2 |
| 7 | `test_authority_tie_requires_human` | Two `PROVIDER_SYSTEM_NOTICE` direct claims → `AUTHORITY_CONFLICT`, `NEEDS_HUMAN`. §3.4 H1 |
| 8 | `test_marketing_email_does_not_reopen` | Q3 fails → status `RESOLVED`, revision unchanged. §5.4 |
| 9 | `test_partial_fulfillment_atomic` | `420 − 200 = 220`, `PARTIAL`, one revision, one transition, one outbox row. §4.6 |
| 10 | `test_duplicate_fulfillment_noop` | Second apply → `NOOP_DUPLICATE`, amounts unchanged. §4.2 |
| 11 | `test_over_fulfilment_disputes` | `500` against `420` → `DISPUTED`, `outstanding=0`, conflict holds excess `80`. §4.3 |
| 12 | `test_currency_mismatch_never_converts` | `EUR` against a `USD` commitment → `REJECTED_CURRENCY`, `DISPUTED`. §4.2 |
| 13 | `test_no_fulfilled_with_outstanding` | Property test over random ledgers. §4.4 |
| 14 | `test_belief_requires_grounding` | Plan with a support-less version → `INVARIANT_BELIEF_UNGROUNDED`. §9.3 |
| 15 | `test_retracted_evidence_excluded` | Evidence with a `CORRECTION` retraction produces no conflict and grounds nothing. §2.8 |
| 16 | `test_concurrent_writers_serialize` | 50 iterations, gapless revisions, `Σ retry_count > 0`. §7.6 |
| 17 | `test_no_side_effects_inside_tx` | A callback calling a stubbed Bedrock client raises `SideEffectInsideTransaction`. §1.3 |
| 18 | `test_trigger_false_predicate_is_noop` | No revision, no transition, no event. §6.2 |
| 19 | `test_cross_user_evidence_rejected` | `EVIDENCE_FOREIGN_USER` → `REJECTED_INVALID_PROVENANCE`. §1.2 |
| 20 | `test_kernel_imports_no_aws` | Import-linter: `provenance_domain.kernel` imports no `boto3`, `httpx`, or `provenance_db`. §0.4 |

Tests 1–15 and 17–20 require no database. Tests 3–5, 8–13, 16, 18–19 run against a local CockroachDB or CockroachDB Cloud. None of them require Bedrock, which is the falsifiable form of the claim in `MEMORY_SYSTEM.md` §31.

---

## 11. Risks and decided posture

**R1 — The five predicate families are a bet, and the bet is visible.** Anything outside `SERVICE_STATUS`, `BALANCE`, `PAYMENT`, `OUTSTANDING`, `COMMITMENT_STATUS` produces zero conflicts. Real inboxes contain warranty windows, eligibility disputes, and delivery promises that v1 will admit as evidence and then say nothing about. Mitigation: the registry is a table, not a code path, so adding a sixth family is a row, a value schema, and matcher rows M14+. Risk accepted deliberately: a small provably-correct matcher beats a large untestable one, and the honest failure mode (silence) is better than the dishonest one (invented conflicts).

**R2 — `entailment_penalty = 0.30` is calibrated on one scenario.** It is the number that makes both directions of the hero case auto-resolve. It has not been validated against a corpus, and a corpus does not exist. If it is too high, genuine billing evidence gets systematically discounted; too low and every invoice for a terminated service demands human review. Mitigation: it is a single config value, the eval harness in `evals/memory/` reports auto-resolve precision and recall per scenario, and §2.10 item 11 guarantees that changing it never rewrites history.

**R3 — Over-fulfilment caps the projection.** `fulfilled_amount` is capped at `committed_amount` because the schema's `CHECK` forbids exceeding it. Nothing is lost (§4.3) but `outstanding_amount` alone no longer answers "how much moved" — you must read the ledger. A future migration could relax the check and store a signed outstanding. The tradeoff was taken to avoid a schema change mid-build; it is the least defensible decision in this document and the one most likely to be revisited.

**R4 — Authority grid values are expert priors, not measurements.** Sixty numbers, chosen by argument. Their *relative ordering* within a column is defensible; their absolute magnitudes are not, and `auto_resolve_margin` is denominated in the same fictional units. Mitigation: the eval dataset labels expected dispositions per scenario, so the grid can be fit rather than asserted once enough labeled scenarios exist. Until then, treat any auto-resolution whose margin lands within 0.05 of the threshold as suspect and log it (`kernel_auto_resolve_margin` histogram).

**R5 — `payment_key`'s amount-and-window fallback over-merges.** Two genuine $200 payments three days apart with no reference number merge into one identity and raise a false `FULFILLMENT_CONFLICT`. The failure direction is safe (a human sees it) but it is noise, and noisy conflicts train users to click through. Mitigation: prefer `external_ref` whenever the extractor finds one; consider narrowing the window to 1 day once real payment artifacts are in the eval set.

**R6 — Rule C1 (one status transition per commit) can produce a state a human would call stale.** In the hero flow the case sits at `REOPENED` with `attention_level = URGENT` rather than `DISPUTED`, because the dispute has not been sent. This is correct but reads oddly on a dashboard. Mitigation: the UI renders `attention_level`, not raw status, as the primary badge. If it still confuses judges, the fix is a UI label, not a second transition.

**R7 — Rule R7 (one case per proposal) pushes real complexity upstream.** **Decision:** the ingestion graph groups extracted candidates by resolved `case_id` and emits one proposal per case, each with its own deterministic `proposal_id`, trace child span, revision, kernel decision, and outbox event. Candidates without a unique case remain in a separate `PENDING_IDENTITY` proposal and are never guessed into a case. A shared artifact may therefore produce several honest independent commits; the kernel never opens a cross-case transaction.

**R8 — Evidence lifecycle metadata is mutable while evidence content is immutable.** **Decision:** `retraction_status` and `is_retrieval_eligible` are stored on `evidence_items` and maintained exclusively by the kernel; the agent view filters `retraction_status = 'ACTIVE'`. Content columns remain immutable and mutation tests distinguish content from lifecycle metadata.

**R9 — Overlap-based matching is O(incumbents × challengers) per commit.** Fine at one-case scope with single-digit propositions. A case that accumulates hundreds of belief versions — a long-running billing dispute — degrades. Mitigation: the snapshot loads only *current* belief versions, not lineage, so the incumbent count is bounded by the number of distinct beliefs on the case, not by history depth. Verify this bound with a load fixture before claiming it.

**R10 — Bitemporal correctness depends entirely on extractor date quality.** Every rule in §8 assumes `valid_from` / `valid_to` are right. A Tier E model that reads "invoice date 5 September" as the service period start silently converts a valid-time question into a wrong answer, and the kernel cannot detect it — the interval is well-formed, just false. Mitigation: T2 makes "no date" safe, so the extractor prompt must be biased toward emitting `UNKNOWN` over guessing, and the extraction eval must score date precision separately from date recall. This is the largest residual correctness risk in the entire kernel, and it lives outside the kernel.
