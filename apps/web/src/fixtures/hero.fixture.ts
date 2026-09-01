/**
 * The hero fixture.
 *
 * This is the ONLY module in the application that contains data. It exists because
 * Phase 8's control plane has not been built, and it is deliberately conspicuous:
 *
 *   - one file, named `*.fixture.ts`, under `src/fixtures/`
 *   - every export typed against `src/lib/api/contract.ts`, so a shape that drifts from
 *     `specs/15_API_SPEC.md` fails `tsc` rather than rendering something plausible
 *   - importable by exactly one non-test module, `src/lib/api/fixture-source.ts`
 *   - every screen served from it carries a permanent banner saying so
 *
 * `scripts/check-render-honesty.mjs` enforces all four. When the real API lands, this
 * file is deleted and `PV_API_BASE_URL` is set; nothing else changes, because no
 * component has ever seen it.
 *
 * Values are the frozen hero canon from `docs/CANONICAL_DECISIONS.md`. Three of them are
 * worth stating, because they are the ones a hostile reader checks first:
 *
 *   Outstanding is USD 2,020.00 = Harborview 1,800.00 + Beltline 220.00. Northline is in
 *   the ledger and contributes nothing, because a disputed balance changes `status`, not
 *   `amount`. Summing the disputed 186.00 into the total would contradict the Kernel on
 *   the landing screen.
 *
 *   The invoice's USD 186.00 is a counterparty *claim*. It is never admitted to the
 *   ledger, so it appears as `claimed_against_you`, never as an obligation.
 *
 *   Kestrel Analytics is the employer. The USD 420.00 damage claim belongs to Beltline
 *   Movers, the mover. Attributing it to the employer is a rejection.
 */

import type {
  ActionIntentListItem,
  ActionIntentResponse,
  ArtifactListItem,
  ArtifactResponse,
  CaseResponse,
  ContextListItem,
  CounterfactualResponse,
  DashboardResponse,
  IngestAliasResponse,
  MeResponse,
  Paginated,
  RelationshipListItem,
  RelationshipResponse,
  StateProofResponse,
  TimelineEntry,
  TraceResponse,
  TriggerItem,
  VersionResponse,
} from "@/lib/api/contract";

/* -- identifiers ------------------------------------------------------------ */

const USER_ID = "018f7a01-0000-7000-8000-00000000abcd";
const TENANT_ID = "018f7a00-0000-7000-8000-00000000ffff";
const CONTEXT_ID = "018f7b00-0000-7000-8000-000000000001";

const REL_LANDLORD = "018f7c00-0000-7000-8000-000000000001";
const REL_MOVER = "018f7c00-0000-7000-8000-000000000002";
const REL_EMPLOYER = "018f7c00-0000-7000-8000-000000000003";
const REL_ISP_OLD = "018f7c00-0000-7000-8000-000000000004";
const REL_ISP_NEW = "018f7c00-0000-7000-8000-000000000005";

const CP_LANDLORD = "018f7d00-0000-7000-8000-000000000001";
const CP_MOVER = "018f7d00-0000-7000-8000-000000000002";
const CP_EMPLOYER = "018f7d00-0000-7000-8000-000000000003";
const CP_ISP = "018f7d00-0000-7000-8000-000000000004";

export const CASE_ISP = "018f8a10-4c22-7f31-9b7d-2ac1e5f09b41";
export const CASE_DEPOSIT = "018f8a11-4c22-7f31-9b7d-2ac1e5f09b42";
export const CASE_DAMAGE = "018f8a12-4c22-7f31-9b7d-2ac1e5f09b43";

const BELIEF_BALANCE = "018f8b21-77aa-7cd2-9e33-11b0c9d4e5f6";
const BV_BALANCE_V1 = "018f8b22-0000-7000-8000-000000000001";
const BV_BALANCE_V2 = "018f8b22-0000-7000-8000-000000000002";
const BELIEF_TERMINATED = "018f8b23-77aa-7cd2-9e33-11b0c9d4e5f7";
const BV_TERMINATED_V1 = "018f8b24-0000-7000-8000-000000000001";

const KD_RESOLVED = "018f8b90-0000-7000-8000-000000000001";
const KD_REOPENED = "018f8b90-0000-7000-8000-000000000002";

const EV_USER_CANCEL = "018f8a90-0000-7000-8000-000000000006";
const EV_CONFIRMATION = "018f8a90-0000-7000-8000-000000000007";
const EV_INVOICE_PERIOD = "018f8aa0-0000-7000-8000-000000000021";
const CLAIM_BALANCE = "018f8ab0-0000-7000-8000-000000000011";

const ART_INVOICE = "018f9e80-0000-7000-8000-000000000001";
const ART_CONFIRMATION = "018f8a80-0000-7000-8000-000000000003";
const ART_LEASE = "018f8a80-0000-7000-8000-000000000004";
const ART_RECEIPT = "018f8a80-0000-7000-8000-000000000005";

const CONFLICT_BALANCE = "018f8d40-0000-7000-8000-000000000001";
const COMMITMENT_DEPOSIT = "018f8c30-0000-7000-8000-000000000001";
const COMMITMENT_DAMAGE = "018f8c30-0000-7000-8000-000000000002";
const COMMITMENT_RELOCATION = "018f8c30-0000-7000-8000-000000000003";

const TRIGGER_DEPOSIT = "018f8e50-0000-7000-8000-000000000002";
const TRIGGER_BALANCE = "018f8e50-0000-7000-8000-000000000003";
const TRIGGER_LEASE_STATEMENT = "018f8e50-0000-7000-8000-000000000004";
const TRIGGER_DAMAGE = "018f8e50-0000-7000-8000-000000000005";
const TRIGGER_RELOCATION = "018f8e50-0000-7000-8000-000000000006";

const ACTION_DISPUTE = "018f9c2f-1111-7abc-8def-000000000001";
export const TRACE_HERO = "018f9c2e-9a41-7a13-b0e2-6d2b1c4f8a90";
const AGENT_RUN_INGEST = "018f9e90-0000-7000-8000-000000000001";
const AGENT_RUN_ADVOCATE = "018f9ec0-0000-7000-8000-000000000002";
export const COUNTERFACTUAL_ID = "018fa010-0000-7000-8000-000000000001";

/* -- the demo clock --------------------------------------------------------- */

/** 18 SEP 2026, 14:05 UTC. Every "days past" figure derives from it. */
const NOW = "2026-09-18T14:05:00.000Z";

/* -- model ids -------------------------------------------------------------- */

/*
 * Gemini model id canon, frozen 2026-08-24 (`docs/CANONICAL_DECISIONS.md`), superseding
 * the Bedrock canon these constants previously carried. Judge Mode renders `model_id`
 * verbatim, so leaving `us.anthropic.claude-opus-4-6-v1` here would have printed a model
 * id this build never calls on the one screen whose whole purpose is showing what ran.
 *
 * There is deliberately no Pro tier: `gemini-3.1-pro-preview` is version 3.1, BELOW the
 * "Gemini 3.5 or newer" floor, so both tiers are Flash-class.
 *
 * UNPROBED. None of these ids has been invoked -- `ops/gemini-probe.txt` currently reads
 * `CANNOT RUN`. The previous canon was disproved by live invocation after being frozen
 * from documentation, which is exactly the state these three are in now. A fixture that
 * names a model nobody called is a claim, and `agent_runs.model_route` is what will make
 * it checkable once the run is real.
 */
const TIER_R = "gemini-3.7-flash";
const TIER_E = "gemini-3.5-flash-lite";
const EMBEDDING_MODEL = "gemini-embedding-2";

/* -- GET /v1/version -------------------------------------------------------- */

export const heroVersion: VersionResponse = {
  service: "provenance-control-plane",
  version: "0.1.0",
  git_sha: "0000000",
  api_version: "v1",
  contracts_schema_version: "1.0",
  region: "us-east-1",
  built_at: "2026-09-18T09:00:00.000Z",
  schema_revision: "0008_events_infrastructure",
  fixture_mode: false,
  agent_mode: "LIVE",
  otlp_export: "ENABLED",
  db_ok: true,
};

/* -- GET /v1/me ------------------------------------------------------------- */

export const heroMe: MeResponse = {
  user_id: USER_ID,
  tenant_id: TENANT_ID,
  display_name: "Alex Rivera",
  email: "alex@example.invalid",
  timezone: "America/New_York",
  home_region: "US-NY",
  created_at: "2026-04-02T15:41:00.000Z",
  feature_flags: {
    ses_inbound_enabled: true,
    upload_ingest_enabled: true,
    counterfactual_enabled: true,
    mcp_trace_visible: true,
    fixture_mode: false,
  },
  judge_mode_enabled: true,
  ingest_alias_status: "ACTIVE",
};

/* -- GET /v1/dashboard ------------------------------------------------------ */

export const heroDashboard: DashboardResponse = {
  generated_at: NOW,
  counts: {
    unresolved_commitments: 2,
    active_conflicts: 1,
    action_intents_pending: 1,
    cases_needing_attention: 2,
    triggers_armed: 3,
    triggers_fired_unhandled: 1,
  },
  contexts: [
    {
      context_id: CONTEXT_ID,
      title: "The Move",
      context_type: "RELOCATION",
      status: "ACTIVE",
      /*
       * Four in scope, which is what G12.1 asserts. The seed creates six relationships;
       * the two outside this context are the new Northline account at the new address and
       * the decoy utility. The dashboard is context-scoped, so the count is 4 by
       * construction rather than by coincidence.
       */
      relationship_count: 4,
      open_case_count: 3,
      total_outstanding: [{ currency: "USD", amount: "2020.0000" }],
    },
  ],
  relationships_summary: [
    {
      relationship_id: REL_LANDLORD,
      counterparty: {
        counterparty_id: CP_LANDLORD,
        display_name: "Harborview Property Management",
        kind: "LANDLORD",
      },
      label: "Old apartment lease",
      relationship_type: "TENANCY",
      status: "CLOSED",
      attention_level: "URGENT",
      open_case_count: 1,
      last_activity_at: "2026-09-18T04:00:00.000Z",
      outstanding: [{ currency: "USD", amount: "1800.0000" }],
    },
    {
      relationship_id: REL_MOVER,
      counterparty: { counterparty_id: CP_MOVER, display_name: "Beltline Movers", kind: "MOVER" },
      label: "Move of 2 April 2026",
      relationship_type: "SERVICE_ENGAGEMENT",
      status: "ACTIVE",
      attention_level: "ATTENTION",
      open_case_count: 1,
      last_activity_at: "2026-06-12T21:40:00.000Z",
      outstanding: [{ currency: "USD", amount: "220.0000" }],
    },
    {
      relationship_id: REL_ISP_OLD,
      counterparty: { counterparty_id: CP_ISP, display_name: "Northline Fiber", kind: "ISP" },
      label: "Old apartment ISP account",
      relationship_type: "SERVICE_ACCOUNT",
      status: "CLOSED",
      attention_level: "URGENT",
      open_case_count: 1,
      last_activity_at: NOW,
      /* Empty, not zero. The disputed claim is not an obligation. */
      outstanding: [],
    },
    {
      relationship_id: REL_EMPLOYER,
      counterparty: {
        counterparty_id: CP_EMPLOYER,
        display_name: "Kestrel Analytics",
        kind: "EMPLOYER",
      },
      label: "Relocation expense claim",
      relationship_type: "EMPLOYMENT",
      status: "ACTIVE",
      attention_level: "NONE",
      open_case_count: 0,
      last_activity_at: "2026-06-20T00:00:00.000Z",
      outstanding: [],
    },
  ],
  cases_attention: [
    {
      case_id: CASE_ISP,
      title: "Old ISP cancellation",
      status: "REOPENED",
      revision: 13,
      attention_level: "URGENT",
      attention_reason_codes: ["CONFLICT_OPEN", "ACTION_AWAITING_APPROVAL"],
      relationship_id: REL_ISP_OLD,
      counterparty_display_name: "Northline Fiber",
      last_activity_at: NOW,
      headline: "A new invoice contradicts your recorded cancellation.",
    },
    {
      case_id: CASE_DEPOSIT,
      title: "Security deposit return",
      status: "WAITING",
      revision: 6,
      attention_level: "URGENT",
      attention_reason_codes: ["TRIGGER_FIRED", "COMMITMENT_OVERDUE"],
      relationship_id: REL_LANDLORD,
      counterparty_display_name: "Harborview Property Management",
      last_activity_at: "2026-09-18T04:00:00.000Z",
      headline: "The promised 30 days elapsed and USD 1,800.00 is still outstanding.",
    },
  ],
};

/* -- GET /v1/relationships/{id} --------------------------------------------- */

export const heroRelationships: Readonly<Record<string, RelationshipResponse>> = {
  [REL_LANDLORD]: {
    relationship_id: REL_LANDLORD,
    counterparty: {
      counterparty_id: CP_LANDLORD,
      display_name: "Harborview Property Management",
      kind: "LANDLORD",
      canonical_domain: "harborviewpm.example",
    },
    label: "Old apartment lease",
    relationship_type: "TENANCY",
    status: "CLOSED",
    external_account_ref_masked: "••••4417",
    normalized_identifiers: { lease_ref: "HPM-LEASE-2024-3B" },
    valid_from: "2024-04-01T00:00:00.000Z",
    valid_to: "2026-05-16T00:00:00.000Z",
    revision: 6,
    context: { context_id: CONTEXT_ID, title: "The Move" },
    cases: [
      {
        case_id: CASE_DEPOSIT,
        title: "Security deposit return",
        case_type: "DEPOSIT_RETURN",
        status: "WAITING",
        revision: 6,
        attention_level: "URGENT",
        opened_at: "2026-04-02T16:12:00.000Z",
        resolved_at: null,
        reopened_count: 0,
        last_activity_at: "2026-09-18T04:00:00.000Z",
      },
    ],
    summary: {
      total_cases: 1,
      open_cases: 1,
      outstanding: [{ currency: "USD", amount: "1800.0000" }],
    },
  },
  [REL_ISP_OLD]: {
    relationship_id: REL_ISP_OLD,
    counterparty: {
      counterparty_id: CP_ISP,
      display_name: "Northline Fiber",
      kind: "ISP",
      canonical_domain: "northlinefiber.example",
    },
    label: "Old apartment ISP account",
    relationship_type: "SERVICE_ACCOUNT",
    status: "CLOSED",
    external_account_ref_masked: "••••8802",
    normalized_identifiers: { account_ref: "NF-4471-8802" },
    valid_from: "2023-08-01T00:00:00.000Z",
    valid_to: "2026-05-31T00:00:00.000Z",
    revision: 4,
    context: { context_id: CONTEXT_ID, title: "The Move" },
    cases: [
      {
        case_id: CASE_ISP,
        title: "Old ISP cancellation",
        case_type: "SERVICE_CANCELLATION",
        status: "REOPENED",
        revision: 13,
        attention_level: "URGENT",
        opened_at: "2026-04-04T10:00:00.000Z",
        resolved_at: null,
        reopened_count: 1,
        last_activity_at: NOW,
      },
    ],
    summary: {
      total_cases: 1,
      open_cases: 1,
      outstanding: [],
    },
  },
  [REL_MOVER]: {
    relationship_id: REL_MOVER,
    counterparty: {
      counterparty_id: CP_MOVER,
      display_name: "Beltline Movers",
      kind: "MOVER",
      canonical_domain: "beltlinemovers.example",
    },
    label: "Move of 2 April 2026",
    relationship_type: "SERVICE_ENGAGEMENT",
    status: "ACTIVE",
    external_account_ref_masked: "••••1902",
    normalized_identifiers: { job_ref: "BM-88214" },
    valid_from: "2026-04-02T00:00:00.000Z",
    valid_to: null,
    revision: 3,
    context: { context_id: CONTEXT_ID, title: "The Move" },
    cases: [
      {
        case_id: CASE_DAMAGE,
        title: "Damage reimbursement",
        case_type: "DAMAGE_CLAIM",
        status: "ACTIVE",
        revision: 4,
        attention_level: "ATTENTION",
        opened_at: "2026-04-12T09:00:00.000Z",
        resolved_at: null,
        reopened_count: 0,
        last_activity_at: "2026-06-12T21:40:00.000Z",
      },
    ],
    summary: {
      total_cases: 1,
      open_cases: 1,
      outstanding: [{ currency: "USD", amount: "220.0000" }],
    },
  },
  [REL_EMPLOYER]: {
    relationship_id: REL_EMPLOYER,
    counterparty: {
      counterparty_id: CP_EMPLOYER,
      display_name: "Kestrel Analytics",
      kind: "EMPLOYER",
      canonical_domain: "kestrelanalytics.example",
    },
    label: "Relocation expense claim",
    relationship_type: "EMPLOYMENT",
    status: "ACTIVE",
    external_account_ref_masked: "••••3308",
    normalized_identifiers: { employment_ref: "KA-EMP-3308" },
    valid_from: "2024-01-08T00:00:00.000Z",
    valid_to: null,
    revision: 2,
    context: { context_id: CONTEXT_ID, title: "The Move" },
    cases: [],
    summary: {
      total_cases: 1,
      open_cases: 0,
      outstanding: [],
    },
  },
  [REL_ISP_NEW]: {
    relationship_id: REL_ISP_NEW,
    counterparty: {
      counterparty_id: CP_ISP,
      display_name: "Northline Fiber",
      kind: "ISP",
      canonical_domain: "northlinefiber.example",
    },
    label: "New address ISP account",
    relationship_type: "SERVICE_ACCOUNT",
    status: "ACTIVE",
    external_account_ref_masked: "••••2250",
    normalized_identifiers: { account_ref: "NF-9913-2250" },
    valid_from: "2026-04-10T00:00:00.000Z",
    valid_to: null,
    revision: 2,
    context: { context_id: CONTEXT_ID, title: "The Move" },
    cases: [],
    summary: {
      total_cases: 0,
      open_cases: 0,
      outstanding: [],
    },
  },
};

/* -- GET /v1/cases/{id} ----------------------------------------------------- */

export const heroCases: Readonly<Record<string, CaseResponse>> = {
  [CASE_ISP]: {
    case_id: CASE_ISP,
    revision: 13,
    status: "REOPENED",
    attention_level: "URGENT",
    attention_reason_codes: ["CONFLICT_OPEN", "ACTION_AWAITING_APPROVAL"],
    title: "Old ISP cancellation",
    case_type: "SERVICE_CANCELLATION",
    relationship: {
      relationship_id: REL_ISP_OLD,
      label: "Old apartment ISP account",
      status: "CLOSED",
    },
    counterparty: { counterparty_id: CP_ISP, display_name: "Northline Fiber", kind: "ISP" },
    context: { context_id: CONTEXT_ID, title: "The Move" },
    opened_at: "2026-04-04T10:00:00.000Z",
    resolved_at: null,
    reopened_count: 1,
    last_activity_at: NOW,
    commitments: [],
    active_conflicts: [
      {
        conflict_id: CONFLICT_BALANCE,
        conflict_type: "VALUE_CONFLICT",
        predicate: "balance_owed",
        /*
         * Hero conflict canon: NEEDS_HUMAN, not OPEN. `status = 'OPEN'` is a legal column
         * value but no disposition rule emits it for a monetary-family conflict whose
         * exposure clears the 100.00 threshold, so it is not the hero's value.
         */
        status: "NEEDS_HUMAN",
        severity: "HIGH",
        requires_human: true,
        detected_at: NOW,
        summary: "A June invoice asserts a billable balance after a confirmed 31 May termination.",
      },
    ],
    next_trigger: {
      trigger_id: TRIGGER_BALANCE,
      trigger_type: "RESPONSE_DEADLINE",
      state: "ARMED",
      not_before: "2026-10-02T00:00:00.000Z",
      expires_at: "2026-12-18T00:00:00.000Z",
      basis_case_revision: 13,
    },
    latest_action_intent: {
      action_intent_id: ACTION_DISPUTE,
      action_type: "OUTBOUND_EMAIL_DISPUTE",
      status: "NEEDS_REVIEW",
      basis_case_revision: 13,
      created_at: "2026-09-18T14:06:11.900Z",
    },
    counts: { evidence_items: 14, claims: 9, beliefs: 6, state_transitions: 13 },
  },
  [CASE_DEPOSIT]: {
    case_id: CASE_DEPOSIT,
    revision: 6,
    status: "WAITING",
    attention_level: "URGENT",
    attention_reason_codes: ["TRIGGER_FIRED", "COMMITMENT_OVERDUE"],
    title: "Security deposit return",
    case_type: "DEPOSIT_RETURN",
    relationship: { relationship_id: REL_LANDLORD, label: "Old apartment lease", status: "CLOSED" },
    counterparty: {
      counterparty_id: CP_LANDLORD,
      display_name: "Harborview Property Management",
      kind: "LANDLORD",
    },
    context: { context_id: CONTEXT_ID, title: "The Move" },
    opened_at: "2026-04-02T16:12:00.000Z",
    resolved_at: null,
    reopened_count: 0,
    last_activity_at: "2026-09-18T04:00:00.000Z",
    commitments: [
      {
        commitment_id: COMMITMENT_DEPOSIT,
        commitment_type: "MONETARY_RETURN",
        description: "Return the security deposit within 30 days of the final inspection.",
        obligor_type: "COUNTERPARTY",
        beneficiary_type: "USER",
        status: "UNFULFILLED",
        committed_amount: { currency: "USD", amount: "1800.0000" },
        fulfilled_amount: { currency: "USD", amount: "0.0000" },
        outstanding_amount: { currency: "USD", amount: "1800.0000" },
        currency: "USD",
        due_at: "2026-06-15T00:00:00.000Z",
        revision: 4,
      },
    ],
    active_conflicts: [],
    next_trigger: {
      trigger_id: TRIGGER_LEASE_STATEMENT,
      trigger_type: "RESPONSE_DEADLINE",
      state: "ARMED",
      not_before: "2026-09-19T04:00:00.000Z",
      expires_at: "2026-10-15T00:00:00.000Z",
      basis_case_revision: 6,
    },
    latest_action_intent: null,
    counts: { evidence_items: 8, claims: 5, beliefs: 3, state_transitions: 6 },
  },
  [CASE_DAMAGE]: {
    case_id: CASE_DAMAGE,
    revision: 4,
    status: "ACTIVE",
    attention_level: "ATTENTION",
    attention_reason_codes: ["COMMITMENT_PARTIAL"],
    title: "Damage reimbursement",
    case_type: "DAMAGE_CLAIM",
    relationship: { relationship_id: REL_MOVER, label: "Move of 2 April 2026", status: "ACTIVE" },
    counterparty: { counterparty_id: CP_MOVER, display_name: "Beltline Movers", kind: "MOVER" },
    context: { context_id: CONTEXT_ID, title: "The Move" },
    opened_at: "2026-04-12T09:00:00.000Z",
    resolved_at: null,
    reopened_count: 0,
    last_activity_at: "2026-06-12T21:40:00.000Z",
    commitments: [
      {
        commitment_id: COMMITMENT_DAMAGE,
        commitment_type: "MONETARY_REIMBURSEMENT",
        description: "Reimburse USD 420.00 for the table damaged during the move.",
        obligor_type: "COUNTERPARTY",
        beneficiary_type: "USER",
        status: "PARTIAL",
        committed_amount: { currency: "USD", amount: "420.0000" },
        fulfilled_amount: { currency: "USD", amount: "200.0000" },
        outstanding_amount: { currency: "USD", amount: "220.0000" },
        currency: "USD",
        due_at: "2026-10-01T00:00:00.000Z",
        revision: 3,
      },
    ],
    active_conflicts: [],
    next_trigger: {
      trigger_id: TRIGGER_DAMAGE,
      trigger_type: "COMMITMENT_DEADLINE",
      state: "ARMED",
      not_before: "2026-10-01T00:01:00.000Z",
      expires_at: "2026-12-01T00:00:00.000Z",
      basis_case_revision: 4,
    },
    latest_action_intent: null,
    counts: { evidence_items: 6, claims: 4, beliefs: 2, state_transitions: 4 },
  },
};

/* -- GET /v1/cases/{id}/timeline -------------------------------------------- */

export const heroTimelines: Readonly<Record<string, Paginated<TimelineEntry>>> = {
  [CASE_ISP]: {
    items: [
      {
        id: "018f8f60-0000-7000-8000-000000000112",
        kind: "ACTION_PROPOSED",
        occurred_at: "2026-09-18T14:06:11.900Z",
        case_revision: 13,
        trace_id: TRACE_HERO,
        actor: { type: "AGENT", label: "Advocate" },
        headline: "Formal dispute letter drafted for your approval. Not sent.",
        detail: {
          action_intent_id: ACTION_DISPUTE,
          action_type: "OUTBOUND_EMAIL_DISPUTE",
          status: "NEEDS_REVIEW",
          recipient_masked: "b•••••g@northlinefiber.example",
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000111",
        kind: "STATE_TRANSITION",
        occurred_at: NOW,
        case_revision: 13,
        trace_id: TRACE_HERO,
        actor: { type: "KERNEL", label: "Memory Kernel" },
        headline: "Case moved RESOLVED to REOPENED at revision 13.",
        detail: {
          transition_type: "CASE_STATUS",
          from_state: "RESOLVED",
          to_state: "REOPENED",
          reason_code: "CONTRADICTORY_EVIDENCE",
          kernel_decision_id: KD_REOPENED,
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000110",
        kind: "BELIEF_CHANGED",
        occurred_at: NOW,
        case_revision: 13,
        trace_id: TRACE_HERO,
        actor: { type: "KERNEL", label: "Memory Kernel" },
        headline:
          "Belief balance_owed superseded: value unchanged at USD 0.00, status now DISPUTED.",
        detail: {
          belief_id: BELIEF_BALANCE,
          predicate: "balance_owed",
          from_version_no: 1,
          to_version_no: 2,
          epistemic_status: "DISPUTED",
          grounded: true,
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000109",
        kind: "CONFLICT_OPENED",
        occurred_at: NOW,
        case_revision: 13,
        trace_id: TRACE_HERO,
        actor: { type: "KERNEL", label: "Memory Kernel" },
        headline: "Conflict opened between the recorded cancellation and the invoice claim.",
        detail: {
          conflict_id: CONFLICT_BALANCE,
          conflict_type: "VALUE_CONFLICT",
          severity: "HIGH",
          status: "NEEDS_HUMAN",
          resolution_reason_code: null,
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000108",
        kind: "CLAIM_RECORDED",
        occurred_at: NOW,
        case_revision: 13,
        trace_id: TRACE_HERO,
        actor: { type: "COUNTERPARTY", label: "Northline Fiber" },
        headline: "Northline Fiber asserts a balance of USD 186.00 is owed.",
        detail: {
          claim_id: CLAIM_BALANCE,
          claim_kind: "COUNTERPARTY_CLAIM",
          predicate: "balance_owed",
          actor_type: "COUNTERPARTY",
          object_summary: "USD 186.00 for service period 1 June to 30 June 2026",
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000107",
        kind: "EVIDENCE_ADMITTED",
        occurred_at: NOW,
        case_revision: 13,
        trace_id: TRACE_HERO,
        actor: { type: "KERNEL", label: "Memory Kernel" },
        headline: "Three assertions admitted from the September invoice.",
        detail: {
          artifact_id: ART_INVOICE,
          evidence_ids: [EV_INVOICE_PERIOD],
          /* Exactly 3, per 00_PRODUCT.md section 2.3. */
          evidence_type_counts: {
            DATE_ASSERTION: 1,
            AMOUNT_ASSERTION: 1,
            IDENTIFIER_ASSERTION: 1,
          },
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000106",
        kind: "ARTIFACT_RECEIVED",
        occurred_at: NOW,
        case_revision: 12,
        trace_id: TRACE_HERO,
        actor: { type: "COUNTERPARTY", label: "Northline Fiber" },
        headline: "Invoice received from Northline Fiber for USD 186.00.",
        detail: {
          artifact_id: ART_INVOICE,
          source_type: "EMAIL_INBOUND",
          mime_type: "application/pdf",
          sender_display: "billing@northlinefiber.example",
          subject: "Invoice 88431 for account ••••8802",
          received_at: NOW,
          parser_status: "PARSED",
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000105",
        kind: "STATE_TRANSITION",
        occurred_at: "2026-06-01T04:00:00.000Z",
        case_revision: 12,
        trace_id: null,
        actor: { type: "KERNEL", label: "Memory Kernel" },
        headline: "Case moved ACTIVE to RESOLVED at revision 12.",
        detail: {
          transition_type: "CASE_STATUS",
          from_state: "ACTIVE",
          to_state: "RESOLVED",
          reason_code: "OBLIGATION_DISCHARGED",
          kernel_decision_id: KD_RESOLVED,
        },
      },
    ],
    page: { limit: 8, has_more: true, next_cursor: "eyJvIjoiMjAyNi0wNi0wMSJ9" },
  },
  [CASE_DEPOSIT]: {
    items: [
      {
        id: "018f8f60-0000-7000-8000-000000000206",
        kind: "TRIGGER_FIRED",
        occurred_at: "2026-09-18T04:00:00.000Z",
        case_revision: 6,
        trace_id: null,
        actor: { type: "SCHEDULER", label: "Prospective memory" },
        headline: "The promised 30 days elapsed and USD 1,800.00 is still outstanding.",
        detail: {
          trigger_id: TRIGGER_DEPOSIT,
          trigger_type: "COMMITMENT_DEADLINE",
          state: "FIRED",
          evaluation_version: 1,
          last_result: "FIRED",
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000205",
        kind: "CLAIM_RECORDED",
        occurred_at: "2026-07-14T18:22:00.000Z",
        case_revision: 5,
        trace_id: null,
        actor: { type: "USER", label: "You" },
        headline: "You logged a phone call in which a cheque was said to have been posted.",
        detail: {
          claim_id: "018f8ab0-0000-7000-8000-000000000402",
          claim_kind: "COUNTERPARTY_CLAIM",
          predicate: "payment_sent",
          actor_type: "COUNTERPARTY",
          object_summary: "unverified verbal, no artifact",
        },
      },
      {
        id: "018f8f60-0000-7000-8000-000000000204",
        kind: "COMMITMENT_CREATED",
        occurred_at: "2026-05-16T13:02:00.000Z",
        case_revision: 4,
        trace_id: null,
        actor: { type: "KERNEL", label: "Memory Kernel" },
        headline: "Deposit return committed, due 15 June 2026.",
        detail: {
          commitment_id: COMMITMENT_DEPOSIT,
          status: "UNFULFILLED",
          committed_amount: "1800.0000",
          fulfilled_amount: "0.0000",
          outstanding_amount: "1800.0000",
        },
      },
    ],
    page: { limit: 8, has_more: false, next_cursor: null },
  },
  [CASE_DAMAGE]: {
    items: [
      {
        id: "018f8f60-0000-7000-8000-000000000303",
        kind: "FULFILLMENT_ADMITTED",
        occurred_at: "2026-06-12T21:40:00.000Z",
        case_revision: 4,
        trace_id: null,
        actor: { type: "KERNEL", label: "Memory Kernel" },
        headline: "USD 200.00 admitted against the damage reimbursement.",
        detail: {
          fulfillment_id: "018f8c50-0000-7000-8000-000000000001",
          commitment_id: COMMITMENT_DAMAGE,
          amount: { currency: "USD", amount: "200.0000" },
          admission_status: "ADMITTED",
        },
      },
    ],
    page: { limit: 8, has_more: false, next_cursor: null },
  },
};

/* -- GET /v1/cases/{id}/state-proof ----------------------------------------- */

export const heroStateProofs: Readonly<Record<string, StateProofResponse>> = {
  [CASE_ISP]: {
    schema_version: "1.0",
    case_id: CASE_ISP,
    case_revision: 13,
    case_status: "REOPENED",
    generated_at: NOW,
    deterministic: true,
    model_used: null,
    beliefs: [
      {
        belief_id: BELIEF_BALANCE,
        subject_type: "RELATIONSHIP",
        subject_id: REL_ISP_OLD,
        predicate: "balance_owed",
        grounded: true,
        current_version: {
          belief_version_id: BV_BALANCE_V2,
          version_no: 2,
          value_type: "MONEY",
          value_json: { currency: "USD", amount: "0.0000" },
          epistemic_status: "DISPUTED",
          belief_confidence: "0.7100",
          valid_from: "2026-05-31T00:00:00.000Z",
          valid_to: null,
          recorded_at: NOW,
          kernel_decision_id: KD_REOPENED,
          is_current: true,
          grounding_count: 3,
        },
        grounding: [
          {
            support_id: "018f8b40-0000-7000-8000-000000000001",
            relation: "SUPPORTS",
            source_kind: "EVIDENCE",
            source_id: EV_USER_CANCEL,
            weight: "0.4500",
            reason_code: "USER_WRITTEN_INSTRUCTION",
            created_at: "2026-05-15T09:16:44.000Z",
            source: {
              evidence_id: EV_USER_CANCEL,
              artifact_id: ART_CONFIRMATION,
              evidence_type: "USER_STATEMENT",
              exact_text: "Cancel account NF-4471-8802 effective 31 May 2026.",
              normalized_text: "User instructs cancellation effective 2026-05-31.",
              source_locator: { part: "text/plain", char_start: 0, char_end: 58 },
              observed_at: "2026-05-15T09:14:00.000Z",
              source_authority: "0.9000",
              extraction_confidence: "1.0000",
              retraction_status: "ACTIVE",
              artifact: {
                source_type: "EMAIL_OUTBOUND",
                sender_display: "alex@example.invalid",
                subject: "Cancellation request for account ••••8802",
                received_at: "2026-05-15T09:16:44.000Z",
              },
            },
          },
          {
            support_id: "018f8b40-0000-7000-8000-000000000002",
            relation: "SUPPORTS",
            source_kind: "EVIDENCE",
            source_id: EV_CONFIRMATION,
            weight: "0.5000",
            reason_code: "PROVIDER_WRITTEN_CONFIRMATION",
            created_at: "2026-05-16T09:41:00.000Z",
            source: {
              evidence_id: EV_CONFIRMATION,
              artifact_id: ART_CONFIRMATION,
              evidence_type: "CONFIRMATION",
              exact_text:
                "Your service on account NF-4471-8802 will end on 31 May 2026. No further charges will be issued after that date.",
              normalized_text: "Provider confirms service ends 2026-05-31, no further charges.",
              source_locator: { part: "text/plain", char_start: 412, char_end: 528 },
              observed_at: "2026-05-16T09:38:00.000Z",
              source_authority: "0.9200",
              extraction_confidence: "0.9800",
              retraction_status: "ACTIVE",
              artifact: {
                source_type: "EMAIL_INBOUND",
                sender_display: "billing@northlinefiber.example",
                subject: "Cancellation confirmation for account ••••8802",
                received_at: "2026-05-16T09:41:00.000Z",
              },
            },
          },
          {
            support_id: "018f8b40-0000-7000-8000-000000000003",
            relation: "CONTRADICTS",
            source_kind: "CLAIM",
            source_id: CLAIM_BALANCE,
            weight: "0.3000",
            reason_code: "COUNTERPARTY_BILLING_ASSERTION",
            created_at: NOW,
            source: {
              claim_id: CLAIM_BALANCE,
              claim_kind: "COUNTERPARTY_CLAIM",
              predicate: "balance_owed",
              object_json: {
                period_start: "2026-06-01",
                period_end: "2026-06-30",
                amount: { currency: "USD", amount: "186.0000" },
              },
              actor_type: "COUNTERPARTY",
              authority_score: "0.5500",
              evidence_id: EV_INVOICE_PERIOD,
              recorded_at: NOW,
            },
          },
        ],
        lineage: [
          {
            belief_version_id: BV_BALANCE_V1,
            version_no: 1,
            value_type: "MONEY",
            value_json: { currency: "USD", amount: "0.0000" },
            epistemic_status: "SUPERSEDED",
            belief_confidence: "0.9400",
            valid_from: "2026-05-31T00:00:00.000Z",
            valid_to: null,
            recorded_at: "2026-06-01T04:00:00.000Z",
            superseded_at: NOW,
            superseded_by_version_no: 2,
            supersession_reason_codes: ["RC_POST_TERMINATION_PERIOD"],
            kernel_decision_id: KD_RESOLVED,
            grounding_count: 2,
          },
          {
            belief_version_id: BV_BALANCE_V2,
            version_no: 2,
            value_type: "MONEY",
            value_json: { currency: "USD", amount: "0.0000" },
            epistemic_status: "DISPUTED",
            belief_confidence: "0.7100",
            recorded_at: NOW,
            superseded_at: null,
            superseded_by_version_no: null,
            supersession_reason_codes: [],
            kernel_decision_id: KD_REOPENED,
            grounding_count: 3,
            is_current: true,
          },
        ],
      },
      {
        belief_id: BELIEF_TERMINATED,
        subject_type: "RELATIONSHIP",
        subject_id: REL_ISP_OLD,
        predicate: "service_terminated",
        grounded: true,
        current_version: {
          belief_version_id: BV_TERMINATED_V1,
          version_no: 1,
          value_type: "STRUCT",
          value_json: { terminated: true, effective_date: "2026-05-31" },
          epistemic_status: "CONFIRMED",
          belief_confidence: "0.9600",
          valid_from: "2026-05-31T00:00:00.000Z",
          valid_to: null,
          recorded_at: "2026-05-16T09:41:00.000Z",
          kernel_decision_id: KD_RESOLVED,
          is_current: true,
          grounding_count: 1,
        },
        grounding: [
          {
            support_id: "018f8b40-0000-7000-8000-000000000004",
            relation: "SUPPORTS",
            source_kind: "EVIDENCE",
            source_id: EV_CONFIRMATION,
            weight: "0.9500",
            reason_code: "PROVIDER_WRITTEN_CONFIRMATION",
            created_at: "2026-05-16T09:41:00.000Z",
            source: {
              evidence_id: EV_CONFIRMATION,
              artifact_id: ART_CONFIRMATION,
              evidence_type: "CONFIRMATION",
              exact_text:
                "Your service on account NF-4471-8802 will end on 31 May 2026. No further charges will be issued after that date.",
              normalized_text: "Provider confirms service ends 2026-05-31.",
              source_locator: { part: "text/plain", char_start: 412, char_end: 528 },
              observed_at: "2026-05-16T09:38:00.000Z",
              source_authority: "0.9200",
              extraction_confidence: "0.9800",
              retraction_status: "ACTIVE",
              artifact: {
                source_type: "EMAIL_INBOUND",
                sender_display: "billing@northlinefiber.example",
                subject: "Cancellation confirmation for account ••••8802",
                received_at: "2026-05-16T09:41:00.000Z",
              },
            },
          },
        ],
        lineage: [
          {
            belief_version_id: BV_TERMINATED_V1,
            version_no: 1,
            epistemic_status: "CONFIRMED",
            belief_confidence: "0.9600",
            recorded_at: "2026-05-16T09:41:00.000Z",
            superseded_at: null,
            superseded_by_version_no: null,
            supersession_reason_codes: [],
            kernel_decision_id: KD_RESOLVED,
            grounding_count: 1,
            is_current: true,
          },
        ],
      },
    ],
    commitments: [],
    conflicts: [
      {
        conflict_id: CONFLICT_BALANCE,
        conflict_type: "VALUE_CONFLICT",
        predicate: "balance_owed",
        status: "NEEDS_HUMAN",
        severity: "HIGH",
        requires_human: true,
        detected_at: NOW,
        resolved_at: null,
        resolution_reason_code: null,
        left: {
          source_kind: "BELIEF_VERSION",
          source_id: BV_BALANCE_V2,
          summary: "Balance is USD 0.00; service terminated 31 May 2026 by written confirmation.",
        },
        right: {
          source_kind: "CLAIM",
          source_id: CLAIM_BALANCE,
          summary: "Invoice asserts USD 186.00 owed for 1 to 30 June 2026.",
        },
        canonical_belief_version_id: BV_BALANCE_V2,
      },
    ],
    derivations: [],
    state_transitions: [
      {
        case_revision: 13,
        transition_type: "CASE_STATUS",
        from_state: "RESOLVED",
        to_state: "REOPENED",
        reason_code: "CONTRADICTORY_EVIDENCE",
        kernel_decision_id: KD_REOPENED,
        trace_id: TRACE_HERO,
        recorded_at: NOW,
      },
      {
        case_revision: 12,
        transition_type: "CASE_STATUS",
        from_state: "ACTIVE",
        to_state: "RESOLVED",
        reason_code: "OBLIGATION_DISCHARGED",
        kernel_decision_id: KD_RESOLVED,
        trace_id: null,
        recorded_at: "2026-06-01T04:00:00.000Z",
      },
    ],
    actions_relying_on_this_state: [
      {
        action_intent_id: ACTION_DISPUTE,
        action_type: "OUTBOUND_EMAIL_DISPUTE",
        status: "NEEDS_REVIEW",
        basis_case_revision: 13,
        supporting_belief_versions: [BV_BALANCE_V2, BV_TERMINATED_V1],
        still_current: true,
      },
    ],
    excluded: {
      retracted_evidence_count: 2,
      superseded_belief_versions_hidden: 0,
      retraction_filter_applied: true,
    },
  },
  [CASE_DEPOSIT]: {
    schema_version: "1.0",
    case_id: CASE_DEPOSIT,
    case_revision: 6,
    case_status: "WAITING",
    generated_at: NOW,
    deterministic: true,
    model_used: null,
    beliefs: [],
    commitments: [
      {
        commitment_id: COMMITMENT_DEPOSIT,
        description: "Return the security deposit within 30 days of the final inspection.",
        status: "UNFULFILLED",
        currency: "USD",
        committed_amount: { currency: "USD", amount: "1800.0000" },
        fulfilled_amount: { currency: "USD", amount: "0.0000" },
        outstanding_amount: { currency: "USD", amount: "1800.0000" },
        due_at: "2026-06-15T00:00:00.000Z",
        source_claim_id: "018f8ab0-0000-7000-8000-000000000341",
        fulfillments: [],
      },
    ],
    conflicts: [],
    derivations: [
      {
        name: "outstanding_amount",
        target: { kind: "COMMITMENT", id: COMMITMENT_DEPOSIT },
        expression: "committed_amount - fulfilled_amount",
        inputs: {
          committed_amount: { currency: "USD", amount: "1800.0000" },
          fulfilled_amount: { currency: "USD", amount: "0.0000" },
        },
        result: { currency: "USD", amount: "1800.0000" },
        deterministic_derivation: true,
        grounding_exempt: true,
      },
    ],
    state_transitions: [
      {
        case_revision: 6,
        transition_type: "CASE_STATUS",
        from_state: "ACTIVE",
        to_state: "WAITING",
        reason_code: "AWAITING_COUNTERPARTY_RESPONSE",
        kernel_decision_id: "018f8b90-0000-7000-8000-000000000006",
        trace_id: null,
        recorded_at: "2026-09-18T04:00:00.000Z",
      },
    ],
    actions_relying_on_this_state: [],
    excluded: {
      retracted_evidence_count: 0,
      superseded_belief_versions_hidden: 0,
      retraction_filter_applied: true,
    },
  },
  [CASE_DAMAGE]: {
    schema_version: "1.0",
    case_id: CASE_DAMAGE,
    case_revision: 4,
    case_status: "ACTIVE",
    generated_at: NOW,
    deterministic: true,
    model_used: null,
    beliefs: [],
    commitments: [
      {
        commitment_id: COMMITMENT_DAMAGE,
        description: "Reimburse USD 420.00 for the table damaged during the move.",
        status: "PARTIAL",
        currency: "USD",
        committed_amount: { currency: "USD", amount: "420.0000" },
        fulfilled_amount: { currency: "USD", amount: "200.0000" },
        outstanding_amount: { currency: "USD", amount: "220.0000" },
        due_at: "2026-10-01T00:00:00.000Z",
        source_claim_id: "018f8ab0-0000-7000-8000-000000000004",
        fulfillments: [
          {
            fulfillment_id: "018f8c50-0000-7000-8000-000000000001",
            amount: { currency: "USD", amount: "200.0000" },
            fulfilled_at: "2026-06-12T21:40:00.000Z",
            admission_status: "ADMITTED",
            confidence: "0.9900",
            evidence_id: "018f8a90-0000-7000-8000-000000000031",
          },
        ],
      },
    ],
    conflicts: [],
    derivations: [
      {
        name: "outstanding_amount",
        target: { kind: "COMMITMENT", id: COMMITMENT_DAMAGE },
        expression: "committed_amount - fulfilled_amount",
        inputs: {
          committed_amount: { currency: "USD", amount: "420.0000" },
          fulfilled_amount: { currency: "USD", amount: "200.0000" },
        },
        result: { currency: "USD", amount: "220.0000" },
        deterministic_derivation: true,
        grounding_exempt: true,
      },
    ],
    state_transitions: [],
    actions_relying_on_this_state: [],
    excluded: {
      retracted_evidence_count: 0,
      superseded_belief_versions_hidden: 0,
      retraction_filter_applied: true,
    },
  },
};

/* -- GET /v1/triggers ------------------------------------------------------- */

export const heroTriggers: Paginated<TriggerItem> = {
  items: [
    {
      trigger_id: TRIGGER_DEPOSIT,
      case_id: CASE_DEPOSIT,
      case_title: "Security deposit return",
      trigger_type: "COMMITMENT_DEADLINE",
      state: "FIRED",
      /* due_at + WAKE_MARGIN_SECONDS, per the hero dataset canon. */
      not_before: "2026-06-15T00:01:00.000Z",
      expires_at: "2026-12-15T00:00:00.000Z",
      basis_case_revision: 5,
      evaluation_version: 1,
      last_evaluated_at: "2026-09-18T04:00:00.000Z",
      last_result: "FIRED",
      last_reason_code: "COMMITMENT_OVERDUE_UNPAID",
      schedule_name: "provenance-trigger-deposit-return",
      predicate_summary: "Outstanding deposit is greater than 0 and the due date has passed.",
      predicate_ast: {
        op: "AND",
        args: [
          {
            op: "GT",
            args: [
              { op: "FIELD", path: "commitments.deposit.outstanding_amount" },
              { op: "CONST", value: "0" },
            ],
          },
          {
            op: "GTE",
            args: [
              { op: "FIELD", path: "clock.now" },
              { op: "FIELD", path: "commitments.deposit.due_at" },
            ],
          },
        ],
      },
      last_evaluation: {
        evaluated_at: "2026-09-18T04:00:00.000Z",
        result: "FIRED",
        case_revision_at_evaluation: 5,
        field_values: {
          "commitments.deposit.outstanding_amount": { currency: "USD", amount: "1800.0000" },
          "commitments.deposit.due_at": "2026-06-15T00:00:00.000Z",
          "clock.now": "2026-09-18T04:00:00.000Z",
        },
      },
    },
    {
      trigger_id: TRIGGER_BALANCE,
      case_id: CASE_ISP,
      case_title: "Old ISP cancellation",
      trigger_type: "RESPONSE_DEADLINE",
      state: "ARMED",
      not_before: "2026-10-02T00:00:00.000Z",
      expires_at: "2026-12-18T00:00:00.000Z",
      basis_case_revision: 13,
      evaluation_version: 1,
      last_evaluated_at: NOW,
      last_result: "NO_OP",
      last_reason_code: "PREDICATE_FALSE_NO_REPLY_WINDOW_OPEN",
      schedule_name: "provenance-trigger-isp-response",
      predicate_summary: "No counterparty reply received within 14 days of the dispute being sent.",
      predicate_ast: {
        op: "AND",
        args: [
          { op: "IS_NULL", args: [{ op: "FIELD", path: "cases.isp.last_counterparty_reply_at" }] },
          {
            op: "GTE",
            args: [
              { op: "FIELD", path: "clock.now" },
              { op: "FIELD", path: "cases.isp.response_deadline_at" },
            ],
          },
        ],
      },
      last_evaluation: {
        evaluated_at: NOW,
        result: "NO_OP",
        case_revision_at_evaluation: 13,
        field_values: {
          "cases.isp.last_counterparty_reply_at": null,
          "cases.isp.response_deadline_at": "2026-10-02T00:00:00.000Z",
          "clock.now": NOW,
        },
      },
    },
    {
      trigger_id: TRIGGER_LEASE_STATEMENT,
      case_id: CASE_DEPOSIT,
      case_title: "Security deposit return",
      trigger_type: "RESPONSE_DEADLINE",
      state: "ARMED",
      not_before: "2026-09-19T04:00:00.000Z",
      expires_at: "2026-10-15T00:00:00.000Z",
      basis_case_revision: 6,
      evaluation_version: 1,
      last_evaluated_at: "2026-09-18T04:00:00.000Z",
      last_result: "NO_OP",
      last_reason_code: "PREDICATE_FALSE_NOT_YET_DUE",
      schedule_name: "provenance-trigger-lease-statement",
      predicate_summary: "A final lease statement has not been received before 15 October 2026.",
      predicate_ast: {
        op: "IS_NULL",
        args: [{ op: "FIELD", path: "cases.deposit.final_statement_received_at" }],
      },
      last_evaluation: {
        evaluated_at: "2026-09-18T04:00:00.000Z",
        result: "NO_OP",
        case_revision_at_evaluation: 6,
        field_values: { "cases.deposit.final_statement_received_at": null },
      },
    },
    {
      trigger_id: TRIGGER_DAMAGE,
      case_id: CASE_DAMAGE,
      case_title: "Damage reimbursement",
      trigger_type: "COMMITMENT_DEADLINE",
      state: "ARMED",
      not_before: "2026-10-01T00:01:00.000Z",
      expires_at: "2026-12-01T00:00:00.000Z",
      basis_case_revision: 4,
      evaluation_version: 1,
      last_evaluated_at: "2026-09-18T04:00:00.000Z",
      last_result: "NO_OP",
      last_reason_code: "PREDICATE_FALSE_NOT_YET_DUE",
      schedule_name: "provenance-trigger-damage-balance",
      predicate_summary: "Damage reimbursement is still outstanding after 1 October 2026.",
      predicate_ast: {
        op: "GT",
        args: [
          { op: "FIELD", path: "commitments.damage.outstanding_amount" },
          { op: "CONST", value: "0" },
        ],
      },
      last_evaluation: {
        evaluated_at: "2026-09-18T04:00:00.000Z",
        result: "NO_OP",
        case_revision_at_evaluation: 4,
        field_values: {
          "commitments.damage.outstanding_amount": { currency: "USD", amount: "220.0000" },
        },
      },
    },
    {
      trigger_id: TRIGGER_RELOCATION,
      case_id: CASE_DAMAGE,
      case_title: "Relocation expense claim",
      trigger_type: "COMMITMENT_DEADLINE",
      state: "DISARMED",
      not_before: "2026-07-01T00:01:00.000Z",
      expires_at: "2026-09-01T00:00:00.000Z",
      basis_case_revision: 2,
      evaluation_version: 1,
      last_evaluated_at: "2026-06-20T04:00:00.000Z",
      last_result: "DISARMED",
      last_reason_code: "COMMITMENT_FULFILLED",
      schedule_name: null,
      predicate_summary: "Relocation expense reimbursed before 1 July 2026.",
      predicate_ast: {
        op: "EQ",
        args: [
          { op: "FIELD", path: "commitments.relocation.outstanding_amount" },
          { op: "CONST", value: "0" },
        ],
      },
      last_evaluation: {
        evaluated_at: "2026-06-20T04:00:00.000Z",
        result: "DISARMED",
        case_revision_at_evaluation: 2,
        field_values: {
          "commitments.relocation.outstanding_amount": { currency: "USD", amount: "0.0000" },
          "commitments.relocation.commitment_id": COMMITMENT_RELOCATION,
        },
      },
    },
  ],
  page: { limit: 25, has_more: false, next_cursor: null },
};

/* -- GET /v1/artifacts, GET /v1/artifacts/{id} ------------------------------ */

const artifactInvoice: ArtifactResponse = {
  artifact_id: ART_INVOICE,
  source_type: "EMAIL_INBOUND",
  mime_type: "application/pdf",
  filename: "northline-invoice-sep.pdf",
  size_bytes: 48211,
  content_sha256: "c4d5e6f7a8b91c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a41f20a",
  sender_display: "billing@northlinefiber.example",
  recipient_display: "n7k4q9wv2x@in.provenance.app",
  subject: "Invoice 88431 for account ••••8802",
  source_message_id: null,
  received_at: NOW,
  event_time: "2026-06-01T00:00:00.000Z",
  parser_status: "PARSED",
  parser_version: "mime-2 / pdf-text-1",
  parser_metadata: { pages: 1, used_textract: false, attachment_count: 0, spans_extracted: 11 },
  content_blocks_summary: [
    { block_id: "b1", kind: "SUBJECT", char_count: 38 },
    { block_id: "b2", kind: "BODY", char_count: 1104 },
    { block_id: "b3", kind: "TABLE", char_count: 212 },
  ],
  evidence_item_count: 3,
  linked_cases: [{ case_id: CASE_ISP, title: "Old ISP cancellation" }],
  agent_run_id: AGENT_RUN_INGEST,
  trace_id: TRACE_HERO,
  download_url: null,
  download_url_expires_at: null,
};

const artifactConfirmation: ArtifactResponse = {
  artifact_id: ART_CONFIRMATION,
  source_type: "EMAIL_INBOUND",
  mime_type: "message/rfc822",
  filename: "northline-cancellation-confirm.eml",
  size_bytes: 9118,
  content_sha256: "9b1f0d55a2c3e4f5061728394a5b6c7d8e9f0a1b2c3d4e5f60718293a4b54ac2",
  sender_display: "billing@northlinefiber.example",
  recipient_display: "alex@example.invalid",
  subject: "Cancellation confirmation for account ••••8802",
  source_message_id: null,
  received_at: "2026-05-16T09:41:00.000Z",
  event_time: "2026-05-31T00:00:00.000Z",
  parser_status: "PARSED",
  parser_version: "mime-2",
  parser_metadata: { quoted_history_blocks: 1, attachment_count: 0 },
  content_blocks_summary: [{ block_id: "b1", kind: "BODY", char_count: 604 }],
  evidence_item_count: 2,
  linked_cases: [{ case_id: CASE_ISP, title: "Old ISP cancellation" }],
  agent_run_id: null,
  trace_id: null,
  download_url: null,
  download_url_expires_at: null,
};

const artifactLease: ArtifactResponse = {
  artifact_id: ART_LEASE,
  source_type: "UPLOAD_PDF",
  mime_type: "application/pdf",
  filename: "harborview-lease-addendum.pdf",
  size_bytes: 221904,
  content_sha256: "2e88a10c4b5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d577b4",
  sender_display: null,
  recipient_display: null,
  subject: null,
  source_message_id: null,
  received_at: "2026-04-02T16:12:00.000Z",
  event_time: "2026-04-01T00:00:00.000Z",
  parser_status: "PARSED",
  parser_version: "pdf-text-1",
  parser_metadata: { pages: 12, used_textract: false },
  content_blocks_summary: [{ block_id: "b1", kind: "BODY", char_count: 24118 }],
  evidence_item_count: 4,
  linked_cases: [{ case_id: CASE_DEPOSIT, title: "Security deposit return" }],
  agent_run_id: null,
  trace_id: null,
  download_url: null,
  download_url_expires_at: null,
};

const artifactReceipt: ArtifactResponse = {
  artifact_id: ART_RECEIPT,
  source_type: "UPLOAD_IMAGE",
  mime_type: "image/jpeg",
  filename: "beltline-damage-receipt.jpg",
  size_bytes: 1204882,
  content_sha256: "5f01bd93a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3dc0a8",
  sender_display: null,
  recipient_display: null,
  subject: null,
  source_message_id: null,
  received_at: "2026-06-12T21:40:00.000Z",
  event_time: "2026-06-12T00:00:00.000Z",
  parser_status: "PARSED",
  parser_version: "textract-1",
  parser_metadata: { pages: 1, used_textract: true },
  content_blocks_summary: [{ block_id: "b1", kind: "BODY", char_count: 318 }],
  evidence_item_count: 2,
  linked_cases: [{ case_id: CASE_DAMAGE, title: "Damage reimbursement" }],
  agent_run_id: null,
  trace_id: null,
  download_url: null,
  download_url_expires_at: null,
};

export const heroArtifacts: Paginated<ArtifactListItem> = {
  items: [artifactInvoice, artifactLease, artifactReceipt, artifactConfirmation],
  page: { limit: 25, has_more: false, next_cursor: null },
};

export const heroArtifactsById: Readonly<Record<string, ArtifactResponse>> = {
  [ART_INVOICE]: artifactInvoice,
  [ART_CONFIRMATION]: artifactConfirmation,
  [ART_LEASE]: artifactLease,
  [ART_RECEIPT]: artifactReceipt,
};

/* -- GET /v1/ingest-alias --------------------------------------------------- */

export const heroIngestAlias: IngestAliasResponse = {
  alias_display: "n7k4q9wv2x@in.provenance.app",
  status: "ACTIVE",
  created_at: "2026-04-02T15:41:00.000Z",
  rotated_at: null,
  artifacts_received: 23,
  last_received_at: NOW,
};

/* -- GET /v1/action-intents/{id} -------------------------------------------- */

const disputeIntent: ActionIntentResponse = {
  action_intent_id: ACTION_DISPUTE,
  case_id: CASE_ISP,
  action_type: "OUTBOUND_EMAIL_DISPUTE",
  status: "NEEDS_REVIEW",
  recipient: "billing@northlinefiber.example",
  recipient_allowlisted: true,
  draft: {
    subject: "Dispute of invoice dated 18 September 2026 for account NF-4471-8802",
    body: [
      "On 15 May 2026 I cancelled account NF-4471-8802, effective 31 May 2026.",
      "Northline Fiber acknowledged the cancellation in writing on 16 May 2026.",
      "The invoice dated 18 September 2026 covers 1 June 2026 to 30 June 2026, a period entirely after the effective termination date.",
      "I am therefore disputing the charge of USD 186.00 in full.",
      "Please confirm in writing that the balance on NF-4471-8802 is USD 0.00.",
    ].join("\n\n"),
    claims: [
      {
        sentence_or_span: "On 15 May 2026 I cancelled account NF-4471-8802, effective 31 May 2026.",
        support_ids: [EV_USER_CANCEL],
        support_kinds: ["EVIDENCE"],
        validated: true,
      },
      {
        sentence_or_span:
          "Northline Fiber acknowledged the cancellation in writing on 16 May 2026.",
        support_ids: [EV_CONFIRMATION, BV_TERMINATED_V1],
        support_kinds: ["EVIDENCE", "BELIEF_VERSION"],
        validated: true,
      },
      {
        sentence_or_span:
          "The invoice dated 18 September 2026 covers 1 June 2026 to 30 June 2026, a period entirely after the effective termination date.",
        support_ids: [EV_INVOICE_PERIOD],
        support_kinds: ["EVIDENCE"],
        validated: true,
      },
      {
        sentence_or_span: "I am therefore disputing the charge of USD 186.00 in full.",
        support_ids: [CLAIM_BALANCE, BV_BALANCE_V2],
        support_kinds: ["CLAIM", "BELIEF_VERSION"],
        validated: true,
      },
      {
        sentence_or_span: "Please confirm in writing that the balance on NF-4471-8802 is USD 0.00.",
        support_ids: [],
        support_kinds: [],
        /*
         * A request, not a factual assertion. It carries no support, so it renders as the
         * user's own words rather than as something the record vouches for. A draft with an
         * unvalidated claim can exist only in NEEDS_REVIEW, never in APPROVED.
         */
        validated: false,
      },
    ],
    requested_outcome: "CANCEL_INVOICE_AND_CONFIRM_CLOSURE",
    tone: "FIRM_POLITE",
    unresolved_risks: [
      "The provider may hold a distinct final-period charge that is contractually valid.",
    ],
  },
  draft_sha256: "a075478717616ffc7c9ff99fdf41cae05efefa47eea4850742f2c1acdd58df7f",
  rationale:
    "A counterparty claim asserts a billable balance inside a period that a higher-authority written confirmation says was terminated.",
  supporting_belief_versions: [
    {
      belief_version_id: BV_BALANCE_V2,
      belief_id: BELIEF_BALANCE,
      predicate: "balance_owed",
      version_no: 2,
      still_current: true,
    },
    {
      belief_version_id: BV_TERMINATED_V1,
      belief_id: BELIEF_TERMINATED,
      predicate: "service_terminated",
      version_no: 1,
      still_current: true,
    },
  ],
  state_proof_url: `/v1/cases/${CASE_ISP}/state-proof`,
  basis_case_revision: 13,
  current_case_revision: 13,
  is_stale: false,
  warnings: [
    {
      code: "OPEN_CONFLICT_REQUIRES_HUMAN",
      message: "This case has an open conflict flagged for human review.",
    },
  ],
  approval: null,
  executions: [],
  created_at: "2026-09-18T14:06:11.900Z",
  created_by_agent_run_id: AGENT_RUN_ADVOCATE,
  trace_id: TRACE_HERO,
};

export const heroActionIntents: Readonly<Record<string, ActionIntentResponse>> = {
  [ACTION_DISPUTE]: disputeIntent,
};

/*
 * The list item is a different shape from the detail response (section 8.23 versus 8.24),
 * and it is written out separately rather than derived from `disputeIntent`, because the
 * list genuinely carries less: a masked recipient, a subject preview, and a warning
 * count. Deriving it would have quietly given the index fields the real endpoint never
 * returns.
 */
export const heroActionIntentList: Paginated<ActionIntentListItem> = {
  items: [
    {
      action_intent_id: ACTION_DISPUTE,
      case_id: CASE_ISP,
      case_title: "Old ISP cancellation",
      counterparty_display_name: "Northline Fiber",
      action_type: "OUTBOUND_EMAIL_DISPUTE",
      status: "NEEDS_REVIEW",
      recipient_masked: "b•••••g@northlinefiber.example",
      subject_preview: disputeIntent.draft.subject,
      basis_case_revision: 13,
      current_case_revision: 13,
      is_stale: false,
      warning_count: disputeIntent.warnings.length,
      created_at: "2026-09-18T14:06:00.000Z",
      created_by_agent_run_id: AGENT_RUN_ADVOCATE,
    },
  ],
  page: { limit: 25, has_more: false, next_cursor: null },
};

/* -- GET /v1/traces/{id} ---------------------------------------------------- */

export const heroTrace: TraceResponse = {
  trace_id: TRACE_HERO,
  started_at: "2026-09-18T14:05:02.001Z",
  finished_at: "2026-09-18T14:06:11.930Z",
  duration_ms: 69929,
  status: "COMPLETED",
  case_ids: [CASE_ISP],
  nodes: [
    {
      id: "n1",
      type: "API_REQUEST",
      status: "OK",
      started_at: "2026-09-18T14:05:02.001Z",
      duration_ms: 61,
      summary: "POST /v1/artifacts/{id}/complete",
      attributes: { artifact_id: ART_INVOICE, http_status: 202, idempotency_replayed: false },
    },
    {
      id: "n2",
      type: "ARTIFACT_PARSE",
      status: "OK",
      started_at: "2026-09-18T14:05:02.140Z",
      duration_ms: 812,
      summary: "PDF text extraction, 1 page, 3 content blocks",
      attributes: { parser_version: "pdf-text-1", used_textract: false },
    },
    {
      id: "n3",
      type: "AGENT_RUN",
      status: "OK",
      started_at: "2026-09-18T14:05:03.010Z",
      duration_ms: 9420,
      summary: "ingestion_graph v1.3.0",
      attributes: {
        agent_run_id: AGENT_RUN_INGEST,
        graph_name: "ingestion_graph",
        graph_version: "1.3.0",
      },
    },
    {
      id: "n4",
      type: "MODEL_CALL",
      status: "OK",
      parent_id: "n3",
      started_at: "2026-09-18T14:05:03.220Z",
      duration_ms: 2110,
      summary: "extract_structured_evidence (Tier E)",
      attributes: {
        model_id: TIER_E,
        prompt_version: "pv-extract-1.1.0",
        input_tokens: 3184,
        output_tokens: 742,
        repair_attempts: 0,
      },
    },
    {
      id: "n5",
      type: "EMBEDDING",
      status: "OK",
      parent_id: "n3",
      started_at: "2026-09-18T14:05:05.400Z",
      duration_ms: 310,
      summary: "3 evidence embeddings generated, 0 reused",
      attributes: { model_id: EMBEDDING_MODEL, dimensions: 1024, embedding_version: "v1" },
    },
    {
      id: "n6",
      type: "MCP_TOOL_CALL",
      status: "OK",
      parent_id: "n3",
      started_at: "2026-09-18T14:05:05.900Z",
      duration_ms: 128,
      summary: "CockroachDB MCP: agent_evidence_retrieval_v1",
      attributes: {
        mcp_server: "cockroachdb-mcp",
        tool_name: "query_agent_evidence_search",
        view_name: "agent_evidence_retrieval_v1",
        sql_role: "pv_agent_reader",
        access_mode: "READ_ONLY",
        rows_returned: 20,
        retraction_filter_applied: true,
        retracted_rows_excluded: 2,
        vector_index: "evidence_embedding_ann_idx",
      },
    },
    {
      id: "n6b",
      type: "MCP_TOOL_CALL",
      status: "FAILED",
      parent_id: "n3",
      started_at: "2026-09-18T14:05:06.020Z",
      duration_ms: 9,
      summary: "CockroachDB MCP: direct table read denied",
      attributes: {
        mcp_server: "cockroachdb-mcp",
        tool_name: "query_base_table",
        view_name: "belief_versions",
        sql_role: "pv_agent_reader",
        access_mode: "READ_ONLY",
        sqlstate: "42501",
        denied: true,
      },
    },
    {
      id: "n7",
      type: "RETRIEVAL",
      status: "OK",
      parent_id: "n3",
      started_at: "2026-09-18T14:05:06.040Z",
      duration_ms: 190,
      summary: "user-scoped vectors to ANN candidates to reranked set to exact match",
      attributes: {
        corpus_size_user_scoped: 16035,
        vector_candidates: 20,
        after_rerank: 7,
        exact_identifier_hits: 1,
        retraction_filtered: 2,
        cross_user_results: 0,
      },
    },
    {
      id: "n8",
      type: "MODEL_CALL",
      status: "OK",
      parent_id: "n3",
      started_at: "2026-09-18T14:05:06.400Z",
      duration_ms: 5900,
      summary: "strong_resolution (Tier R), contradiction characterisation",
      attributes: {
        model_id: TIER_R,
        prompt_version: "pv-resolve-1.1.0",
        input_tokens: 5210,
        output_tokens: 1104,
        requires_human_review: true,
      },
    },
    {
      id: "n9",
      type: "PROPOSAL",
      status: "OK",
      parent_id: "n3",
      started_at: "2026-09-18T14:05:12.400Z",
      duration_ms: 40,
      summary: "MemoryProposal submitted",
      attributes: {
        proposal_id: "018f9fa0-0000-7000-8000-000000000001",
        claims: 1,
        belief_mutations: 1,
        conflict_hints: 1,
      },
    },
    {
      id: "n10",
      type: "KERNEL_DECISION",
      status: "OK",
      started_at: "2026-09-18T14:05:12.460Z",
      duration_ms: 178,
      summary: "ACCEPTED_WITH_CONFLICT, case revision 12 to 13",
      attributes: {
        kernel_decision_id: KD_REOPENED,
        decision: "ACCEPTED_WITH_CONFLICT",
        case_revision_before: 12,
        case_revision_after: 13,
        retry_count: 0,
        sqlstate_40001_retries: 0,
        reason_codes: ["MUTUAL_EXCLUSION_DETECTED", "CONTRADICTORY_EVIDENCE"],
      },
    },
    {
      id: "n11",
      type: "DB_TRANSACTION",
      status: "OK",
      parent_id: "n10",
      started_at: "2026-09-18T14:05:12.470Z",
      duration_ms: 141,
      summary: "SERIALIZABLE commit",
      attributes: { isolation: "SERIALIZABLE", rows_written: 7, retry_count: 0 },
    },
    {
      id: "n11a",
      type: "CANONICAL_CHANGE",
      status: "OK",
      parent_id: "n11",
      started_at: "2026-09-18T14:05:12.480Z",
      duration_ms: 12,
      summary: "Claim admitted",
      attributes: { change_kind: "CLAIM_ADMITTED", case_revision: 13 },
      refs: [{ table: "claims", column: "id", value: CLAIM_BALANCE, cardinality: "1" }],
    },
    {
      id: "n11b",
      type: "CANONICAL_CHANGE",
      status: "OK",
      parent_id: "n11",
      started_at: "2026-09-18T14:05:12.492Z",
      duration_ms: 18,
      summary: "Belief versioned, value unchanged, status DISPUTED",
      attributes: { change_kind: "BELIEF_VERSIONED", case_revision: 13 },
      refs: [{ table: "belief_versions", column: "id", value: BV_BALANCE_V2, cardinality: "1" }],
    },
    {
      id: "n11c",
      type: "CANONICAL_CHANGE",
      status: "OK",
      parent_id: "n11",
      started_at: "2026-09-18T14:05:12.510Z",
      duration_ms: 9,
      summary: "Conflict opened",
      attributes: { change_kind: "CONFLICT_OPENED", case_revision: 13 },
      refs: [{ table: "conflicts", column: "id", value: CONFLICT_BALANCE, cardinality: "1" }],
    },
    {
      id: "n11d",
      type: "CANONICAL_CHANGE",
      status: "OK",
      parent_id: "n11",
      started_at: "2026-09-18T14:05:12.519Z",
      duration_ms: 11,
      summary: "Case status changed RESOLVED to REOPENED",
      attributes: { change_kind: "CASE_STATUS_CHANGED", case_revision: 13 },
      refs: [{ table: "cases", column: "status", value: CASE_ISP, cardinality: "1" }],
    },
    {
      id: "n12",
      type: "OUTBOX_EVENT",
      status: "OK",
      started_at: "2026-09-18T14:05:12.900Z",
      duration_ms: 62,
      summary: "case.reopened.v1 dispatched to EventBridge",
      attributes: {
        event_id: "018f9fb0-0000-7000-8000-000000000001",
        event_type: "case.reopened.v1",
        attempt_count: 1,
        status: "DISPATCHED",
      },
    },
    {
      id: "n12b",
      type: "EVENT_CONSUMER",
      status: "OK",
      started_at: "2026-09-18T14:05:13.400Z",
      duration_ms: 74,
      summary: "advocacy consumer accepted case.reopened.v1",
      attributes: {
        event_id: "018f9fb0-0000-7000-8000-000000000001",
        consumer: "advocacy-worker",
        dedupe_hit: false,
      },
    },
    {
      id: "n13",
      type: "AGENT_RUN",
      status: "OK",
      started_at: "2026-09-18T14:05:14.200Z",
      duration_ms: 7300,
      summary: "advocate_graph v1.2.0, grounded dispute draft",
      attributes: {
        agent_run_id: AGENT_RUN_ADVOCATE,
        model_id: TIER_R,
        claims_validated: 4,
        claims_unsupported: 1,
      },
    },
    {
      id: "n14",
      type: "ACTION_INTENT",
      status: "OK",
      started_at: "2026-09-18T14:06:11.900Z",
      duration_ms: 30,
      summary: "OUTBOUND_EMAIL_DISPUTE proposed, basis revision 13",
      attributes: { action_intent_id: ACTION_DISPUTE, status: "NEEDS_REVIEW" },
    },
    {
      id: "n15",
      type: "ACTION_APPROVAL",
      status: "PENDING",
      started_at: "2026-09-18T14:06:11.930Z",
      duration_ms: 0,
      summary: "Awaiting human approval. Nothing has been sent.",
      attributes: { basis_case_revision: 13 },
    },
  ],
  edges: [
    { from: "n1", to: "n2" },
    { from: "n2", to: "n3" },
    { from: "n3", to: "n4" },
    { from: "n4", to: "n5" },
    { from: "n5", to: "n6" },
    { from: "n6", to: "n6b" },
    { from: "n6b", to: "n7" },
    { from: "n7", to: "n8" },
    { from: "n8", to: "n9" },
    { from: "n9", to: "n10" },
    { from: "n10", to: "n11" },
    { from: "n11", to: "n11a" },
    { from: "n11", to: "n11b" },
    { from: "n11", to: "n11c" },
    { from: "n11", to: "n11d" },
    { from: "n11", to: "n12" },
    { from: "n12", to: "n12b" },
    { from: "n12b", to: "n13" },
    { from: "n13", to: "n14" },
    { from: "n14", to: "n15" },
  ],
  boundary: {
    deterministic_node_ids: [
      "n1",
      "n2",
      "n5",
      "n6",
      "n6b",
      "n7",
      "n9",
      "n10",
      "n11",
      "n11a",
      "n11b",
      "n11c",
      "n11d",
      "n12",
      "n12b",
      "n14",
      "n15",
    ],
    model_node_ids: ["n4", "n8", "n13"],
    note: "Model nodes propose. Deterministic nodes decide, commit, and act.",
  },
};

/* -- GET /v1/judge-mode/counterfactual/{id} --------------------------------- */

const ARTIFACT_SHA = "c4d5e6f7a8b91c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a41f20a";
const DECODE_SHA = "b41c9d2e3f4a5b6c7d8e9f0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4";

export const heroCounterfactual: CounterfactualResponse = {
  counterfactual_id: COUNTERFACTUAL_ID,
  status: "COMPLETED",
  artifact_id: ART_INVOICE,
  artifact_summary: "Invoice 88431, USD 186.00, service period 1 to 30 June 2026, account ••••8802",
  completed_at: "2026-09-18T14:20:41.220Z",
  parity: {
    artifact_id: { off: ART_INVOICE, on: ART_INVOICE, equal: true },
    artifact_sha256: { off: ARTIFACT_SHA, on: ARTIFACT_SHA, equal: true },
    model_id: { off: TIER_R, on: TIER_R, equal: true },
    /*
     * The same prompt asset in both arms. MEMORY OFF strips memory by supplying an empty
     * TRUSTED STRUCTURED CONTEXT block, not by using a different prompt. There is no
     * pv-draft-nomemory asset; this is what makes the parity block provable.
     */
    prompt_version: { off: "pv-draft-1.0.0", on: "pv-draft-1.0.0", equal: true },
    graph_version: { off: "1.3.0", on: "1.3.0", equal: true },
    decode_params_sha256: { off: DECODE_SHA, on: DECODE_SHA, equal: true },
    all_equal: true,
  },
  memory_off: {
    mode: "MEMORY_OFF",
    retrieval_enabled: false,
    canonical_memory_enabled: false,
    corpus_size_visible: 0,
    model_id: TIER_R,
    duration_ms: 4120,
    output: {
      headline: "Invoice for $186 due 30 June.",
      classification: "ROUTINE_INVOICE",
      case_linked: null,
      conflicts_detected: 0,
      recommended_action: "NONE",
      draft_text: null,
    },
    why: "Without retrieval, the artifact is self-describing: a valid invoice with a due date.",
  },
  memory_on: {
    mode: "MEMORY_ON",
    strategy: "REPLAY_COMMITTED",
    retrieval_enabled: true,
    canonical_memory_enabled: true,
    corpus_size_visible: 16035,
    model_id: TIER_R,
    duration_ms: 9420,
    output: {
      headline: "Contradicts your 15 May termination confirmation. Case reopened, dispute drafted.",
      classification: "COUNTERPARTY_CLAIM_CONTRADICTING_CANONICAL",
      case_linked: {
        case_id: CASE_ISP,
        title: "Old ISP cancellation",
        status_before: "RESOLVED",
        status_after: "REOPENED",
        resolved_days_ago: 109,
      },
      conflicts_detected: 1,
      recommended_action: "OUTBOUND_EMAIL_DISPUTE",
      draft_text:
        "On 15 May 2026 I cancelled account NF-4471-8802, effective 31 May 2026. Northline Fiber acknowledged the cancellation in writing on 16 May 2026.",
    },
    grounding: [
      {
        belief_id: BELIEF_TERMINATED,
        predicate: "service_terminated",
        supporting_evidence_id: EV_CONFIRMATION,
        observed_at: "2026-05-16T09:38:00.000Z",
        source_authority: "0.9200",
      },
    ],
    kernel_decision_id: KD_REOPENED,
    case_revision_before: 12,
    case_revision_after: 13,
    trace_url: `/v1/traces/${TRACE_HERO}`,
  },
  delta: {
    conflicts_detected: { off: 0, on: 1 },
    cases_reopened: { off: 0, on: 1 },
    actions_recommended: { off: 0, on: 1 },
    evidence_recalled_days: { off: 0, on: 125 },
    verdict: "Memory OFF treated a contradiction as a routine bill.",
  },
  safety: {
    memory_off_wrote_canonical_state: false,
    memory_off_admitted_evidence: false,
    memory_off_had_proposal_tool: false,
    case_revision_changed_by_counterfactual: false,
  },
};

/* -- GET /v1/contexts ------------------------------------------------------- */

export const heroContexts: Paginated<ContextListItem> = {
  items: [
    {
      context_id: CONTEXT_ID,
      title: "The Move — 214 Ridgeway to 88 Larkin",
      context_type: "RELOCATION",
      status: "ACTIVE",
      created_at: "2026-04-02T15:41:00.000Z",
      case_count: 5,
      open_case_count: 3,
    },
  ],
  page: { limit: 25, has_more: false, next_cursor: null },
};

/* -- GET /v1/relationships -------------------------------------------------- */

/**
 * The list, section 8.6.
 *
 * Written out rather than projected from `heroRelationships`, for the same reason the
 * action-intent list is: 8.6 carries `open_case_count`, `attention_level`,
 * `last_activity_at` and `updated_at`, which the detail response does not, and it carries
 * neither `cases[]` nor `summary`, which the detail response does. Two shapes, two
 * fixtures, so a screen written against the list cannot read a field the list lacks.
 *
 * The counterparty and label are taken from the detail records, because those two facts
 * are genuinely the same row in both responses and letting them drift would model an API
 * that contradicts itself.
 */
function listedRelationship(
  detail: RelationshipResponse,
  extra: Pick<
    RelationshipListItem,
    "open_case_count" | "attention_level" | "last_activity_at" | "updated_at"
  >,
): RelationshipListItem {
  return {
    relationship_id: detail.relationship_id,
    counterparty: detail.counterparty,
    label: detail.label,
    relationship_type: detail.relationship_type,
    status: detail.status,
    external_account_ref_masked: detail.external_account_ref_masked,
    valid_from: detail.valid_from,
    valid_to: detail.valid_to,
    revision: detail.revision,
    ...extra,
  };
}

export const heroRelationshipList: Paginated<RelationshipListItem> = {
  items: [
    listedRelationship(heroRelationships[REL_ISP_OLD] as RelationshipResponse, {
      open_case_count: 1,
      attention_level: "URGENT",
      last_activity_at: NOW,
      updated_at: NOW,
    }),
    listedRelationship(heroRelationships[REL_LANDLORD] as RelationshipResponse, {
      open_case_count: 1,
      attention_level: "URGENT",
      last_activity_at: "2026-09-18T04:00:00.000Z",
      updated_at: "2026-09-18T04:00:00.000Z",
    }),
    listedRelationship(heroRelationships[REL_MOVER] as RelationshipResponse, {
      open_case_count: 1,
      attention_level: "ATTENTION",
      last_activity_at: "2026-06-12T21:40:00.000Z",
      updated_at: "2026-06-12T21:40:00.000Z",
    }),
    listedRelationship(heroRelationships[REL_EMPLOYER] as RelationshipResponse, {
      open_case_count: 0,
      attention_level: "NONE",
      last_activity_at: "2026-06-20T00:00:00.000Z",
      updated_at: "2026-06-20T00:00:00.000Z",
    }),
    listedRelationship(heroRelationships[REL_ISP_NEW] as RelationshipResponse, {
      open_case_count: 0,
      attention_level: "NONE",
      last_activity_at: "2026-06-04T09:12:00.000Z",
      updated_at: "2026-06-04T09:12:00.000Z",
    }),
  ],
  page: { limit: 25, has_more: false, next_cursor: null },
};

/* -- the ids a fixture-backed screen needs in order to build a link --------- */

export const CASE_IDS = [CASE_ISP, CASE_DEPOSIT, CASE_DAMAGE] as const;
