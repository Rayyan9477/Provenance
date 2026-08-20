/**
 * The API contract, transcribed from `docs/specs/15_API_SPEC.md`.
 *
 * Phase 8 has not been built, so this file is hand-transcribed rather than generated
 * from `build/openapi.json`. T12.3 requires generation from the exported OpenAPI once it
 * exists; until then this module is the single place where the shape of the API is
 * written down, and it is the type every fixture and every component is checked against.
 *
 * Nothing here carries a value. It carries only shapes. A concrete id, amount, or
 * display name in this file would be a rendered lie waiting to happen.
 */

/* -- Scalars (section 1.3) -------------------------------------------------- */

/** UUIDv7 as a string. Never constructed client-side. */
export type Uuid = string;
/** RFC 3339 UTC instant, millisecond precision. */
export type Instant = string;
/** Decimal string, 4 dp. Never a JS number: binary floats cannot hold money. */
export type Decimal = string;

export interface Money {
  readonly currency: string;
  readonly amount: Decimal;
}

/* -- Closed vocabularies (specs/11_CONTRACTS.md owns membership) ------------ */

export const ATTENTION_LEVELS = ["NONE", "INFO", "ATTENTION", "URGENT"] as const;
export type AttentionLevel = (typeof ATTENTION_LEVELS)[number];

export const RETRACTION_STATUSES = ["ACTIVE", "RETRACTED", "SUPERSEDED", "QUARANTINED"] as const;
export type RetractionStatus = (typeof RETRACTION_STATUSES)[number];

export const TRIGGER_TYPES = [
  "COMMITMENT_DEADLINE",
  "RESPONSE_DEADLINE",
  "CONFLICT_TIMEOUT",
  "WARRANTY_WINDOW",
] as const;
export type TriggerType = (typeof TRIGGER_TYPES)[number];

export const TRIGGER_STATES = ["ARMED", "FIRED", "DISARMED", "EXPIRED"] as const;
export type TriggerState = (typeof TRIGGER_STATES)[number];

export const TRIGGER_RESULTS = ["FIRED", "NO_OP", "DISARMED", "EXPIRED", "ERROR"] as const;
export type TriggerResult = (typeof TRIGGER_RESULTS)[number];

export const SUPPORT_RELATIONS = ["SUPPORTS", "CONTRADICTS", "QUALIFIES"] as const;
export type SupportRelation = (typeof SUPPORT_RELATIONS)[number];

export const ACTOR_TYPES = [
  "USER",
  "COUNTERPARTY",
  "KERNEL",
  "AGENT",
  "SCHEDULER",
  "EXECUTOR",
  "SYSTEM",
] as const;
export type ActorType = (typeof ACTOR_TYPES)[number];

/** Section 8.28: seventeen, closed. */
export const TRACE_NODE_TYPES = [
  "API_REQUEST",
  "ARTIFACT_PARSE",
  "EMBEDDING",
  "AGENT_RUN",
  "MODEL_CALL",
  "MCP_TOOL_CALL",
  "RETRIEVAL",
  "PROPOSAL",
  "KERNEL_DECISION",
  "DB_TRANSACTION",
  "CANONICAL_CHANGE",
  "OUTBOX_EVENT",
  "EVENT_CONSUMER",
  "TRIGGER_EVALUATION",
  "ACTION_INTENT",
  "ACTION_APPROVAL",
  "ACTION_EXECUTION",
] as const;
export type TraceNodeType = (typeof TRACE_NODE_TYPES)[number];

export const TRACE_NODE_STATUSES = ["OK", "FAILED", "RETRIED", "SKIPPED", "PENDING"] as const;
export type TraceNodeStatus = (typeof TRACE_NODE_STATUSES)[number];

export const CANONICAL_CHANGE_KINDS = [
  "CLAIM_ADMITTED",
  "BELIEF_VERSIONED",
  "GROUNDING_EDGE_ADDED",
  "CONFLICT_OPENED",
  "CONFLICT_RESOLVED",
  "CASE_STATUS_CHANGED",
  "COMMITMENT_CHANGED",
  "FULFILLMENT_ADMITTED",
  "TRIGGER_STATE_CHANGED",
] as const;
export type CanonicalChangeKind = (typeof CANONICAL_CHANGE_KINDS)[number];

export const TIMELINE_KINDS = [
  "ARTIFACT_RECEIVED",
  "EVIDENCE_ADMITTED",
  "CLAIM_RECORDED",
  "BELIEF_CHANGED",
  "CONFLICT_OPENED",
  "CONFLICT_RESOLVED",
  "COMMITMENT_CREATED",
  "COMMITMENT_UPDATED",
  "FULFILLMENT_ADMITTED",
  "STATE_TRANSITION",
  "TRIGGER_ARMED",
  "TRIGGER_FIRED",
  "TRIGGER_NOOP",
  "ACTION_PROPOSED",
  "ACTION_APPROVED",
  "ACTION_REJECTED",
  "ACTION_EXECUTED",
  "ACTION_FAILED",
  "USER_CORRECTION",
] as const;
export type TimelineKind = (typeof TIMELINE_KINDS)[number];

/* -- Section 4.1 error envelope --------------------------------------------- */

export interface ApiErrorBody {
  readonly error: {
    readonly code: string;
    readonly message: string;
    readonly trace_id?: Uuid | null;
    readonly details?: unknown;
  };
}

/* -- Section 5 pagination --------------------------------------------------- */

export interface Page {
  readonly limit: number;
  readonly has_more: boolean;
  readonly next_cursor: string | null;
}

export interface Paginated<T> {
  readonly items: readonly T[];
  readonly page: Page;
}

/* -- Section 8.2 GET /v1/version -------------------------------------------- */

export interface VersionResponse {
  readonly service: string;
  readonly version: string;
  /** git_sha, never build_sha (Hero commit canon, operating-mode disclosure). */
  readonly git_sha: string;
  readonly api_version: string;
  readonly contracts_schema_version: string;
  readonly region: string;
  readonly built_at: Instant;
  readonly schema_revision: string;
  readonly fixture_mode: boolean;
  readonly agent_mode: "LIVE" | "FIXTURE" | "DEGRADED";
  readonly otlp_export: "ENABLED" | "DISABLED" | "FAILING";
  readonly db_ok: boolean;
}

/* -- Section 8.3 GET /v1/me ------------------------------------------------- */

export interface FeatureFlags {
  readonly ses_inbound_enabled?: boolean;
  readonly upload_ingest_enabled?: boolean;
  readonly counterfactual_enabled?: boolean;
  readonly mcp_trace_visible?: boolean;
  /** Mirror of GET /v1/version.fixture_mode. An absent flag is false. */
  readonly fixture_mode?: boolean;
}

export interface MeResponse {
  readonly user_id: Uuid;
  readonly tenant_id: Uuid;
  readonly display_name: string;
  readonly email: string;
  readonly timezone: string;
  readonly home_region: string;
  readonly created_at: Instant;
  readonly feature_flags: FeatureFlags;
  readonly judge_mode_enabled: boolean;
  readonly ingest_alias_status: string;
}

/* -- Section 8.4 GET /v1/dashboard ------------------------------------------ */

export interface CounterpartyRef {
  readonly counterparty_id: Uuid;
  readonly display_name: string;
  readonly kind: string;
  readonly canonical_domain?: string;
}

export interface DashboardCounts {
  readonly unresolved_commitments: number;
  readonly active_conflicts: number;
  readonly action_intents_pending: number;
  readonly cases_needing_attention: number;
  readonly triggers_armed: number;
  readonly triggers_fired_unhandled: number;
}

export interface DashboardContext {
  readonly context_id: Uuid;
  readonly title: string;
  readonly context_type: string;
  readonly status: string;
  readonly relationship_count: number;
  readonly open_case_count: number;
  /** An array, because the Kernel refuses arithmetic across currencies. */
  readonly total_outstanding: readonly Money[];
}

export interface RelationshipSummary {
  readonly relationship_id: Uuid;
  readonly counterparty: CounterpartyRef;
  readonly label: string;
  readonly relationship_type: string;
  readonly status: string;
  readonly attention_level: AttentionLevel;
  readonly open_case_count: number;
  readonly last_activity_at: Instant;
  readonly outstanding: readonly Money[];
}

export interface CaseAttentionItem {
  readonly case_id: Uuid;
  readonly title: string;
  readonly status: string;
  readonly revision: number;
  readonly attention_level: AttentionLevel;
  readonly attention_reason_codes: readonly string[];
  readonly relationship_id: Uuid;
  readonly counterparty_display_name: string;
  readonly last_activity_at: Instant;
  /** Deterministic template output keyed on reason codes. Never model-generated. */
  readonly headline: string;
}

export interface DashboardResponse {
  readonly generated_at: Instant;
  readonly counts: DashboardCounts;
  readonly contexts: readonly DashboardContext[];
  readonly relationships_summary: readonly RelationshipSummary[];
  readonly cases_attention: readonly CaseAttentionItem[];
}

/* -- Section 8.7 GET /v1/relationships/{id} --------------------------------- */

export interface RelationshipCaseRef {
  readonly case_id: Uuid;
  readonly title: string;
  readonly case_type: string;
  readonly status: string;
  readonly revision: number;
  readonly attention_level: AttentionLevel;
  readonly opened_at: Instant;
  readonly resolved_at: Instant | null;
  readonly reopened_count: number;
  readonly last_activity_at: Instant;
}

export interface RelationshipResponse {
  readonly relationship_id: Uuid;
  readonly counterparty: CounterpartyRef;
  readonly label: string;
  readonly relationship_type: string;
  readonly status: string;
  readonly external_account_ref_masked: string | null;
  readonly normalized_identifiers: Readonly<Record<string, string>>;
  readonly valid_from: Instant | null;
  readonly valid_to: Instant | null;
  readonly revision: number;
  readonly context: { readonly context_id: Uuid; readonly title: string };
  readonly cases: readonly RelationshipCaseRef[];
  readonly summary: {
    readonly total_cases: number;
    readonly open_cases: number;
    readonly active_conflicts: number;
    readonly unresolved_commitments: number;
    readonly outstanding: readonly Money[];
    readonly first_evidence_at: Instant | null;
    readonly last_evidence_at: Instant | null;
  };
}

/* -- Section 8.9 GET /v1/cases/{id} ----------------------------------------- */

export interface CaseCommitment {
  readonly commitment_id: Uuid;
  readonly commitment_type: string;
  readonly description: string;
  readonly obligor_type: string;
  readonly beneficiary_type: string;
  readonly status: string;
  readonly committed_amount: Decimal | null;
  readonly fulfilled_amount: Decimal | null;
  readonly outstanding_amount: Decimal | null;
  readonly currency: string | null;
  readonly due_at: Instant | null;
  readonly revision: number;
}

export interface CaseConflict {
  readonly conflict_id: Uuid;
  readonly conflict_type: string;
  readonly predicate: string;
  readonly status: string;
  readonly severity: string;
  readonly requires_human: boolean;
  readonly detected_at: Instant;
  readonly summary: string;
}

export interface CaseTriggerRef {
  readonly trigger_id: Uuid;
  readonly trigger_type: TriggerType;
  readonly state: TriggerState;
  readonly not_before: Instant;
  readonly expires_at: Instant | null;
  readonly basis_case_revision: number;
}

export interface CaseActionIntentRef {
  readonly action_intent_id: Uuid;
  readonly action_type: string;
  readonly status: string;
  readonly basis_case_revision: number;
  readonly created_at: Instant;
}

export interface CaseResponse {
  readonly case_id: Uuid;
  readonly revision: number;
  readonly status: string;
  readonly attention_level: AttentionLevel;
  readonly attention_reason_codes: readonly string[];
  readonly title: string;
  readonly case_type: string;
  readonly relationship: {
    readonly relationship_id: Uuid;
    readonly label: string;
    readonly status: string;
  };
  readonly counterparty: CounterpartyRef;
  readonly context: { readonly context_id: Uuid; readonly title: string };
  readonly opened_at: Instant;
  readonly resolved_at: Instant | null;
  readonly reopened_count: number;
  readonly last_activity_at: Instant;
  readonly commitments: readonly CaseCommitment[];
  readonly active_conflicts: readonly CaseConflict[];
  readonly next_trigger: CaseTriggerRef | null;
  readonly latest_action_intent: CaseActionIntentRef | null;
  readonly counts: {
    readonly evidence_items: number;
    readonly claims: number;
    readonly beliefs: number;
    readonly state_transitions: number;
  };
}

/* -- Section 8.10 GET /v1/cases/{id}/timeline ------------------------------- */

export interface TimelineEntry {
  readonly id: Uuid;
  readonly kind: TimelineKind;
  readonly occurred_at: Instant;
  readonly case_revision: number;
  readonly trace_id: Uuid | null;
  readonly actor: { readonly type: ActorType; readonly label: string };
  readonly headline: string;
  readonly detail: Readonly<Record<string, unknown>>;
}

/* -- Section 8.11 GET /v1/cases/{id}/state-proof ---------------------------- */

export interface SourceLocator {
  readonly part: string;
  readonly char_start: number;
  readonly char_end: number;
}

export interface EvidenceSource {
  readonly evidence_id: Uuid;
  readonly artifact_id: Uuid;
  readonly evidence_type: string;
  readonly exact_text: string;
  readonly normalized_text: string | null;
  readonly source_locator: SourceLocator | null;
  readonly observed_at: Instant;
  readonly source_authority: Decimal;
  readonly extraction_confidence: Decimal;
  readonly retraction_status: RetractionStatus;
  readonly retracted_at?: Instant | null;
  readonly retraction_reason_code?: string | null;
  readonly retracted_by_evidence_id?: Uuid | null;
  readonly artifact: {
    readonly source_type: string;
    readonly sender_display: string | null;
    readonly subject: string | null;
    readonly received_at: Instant;
  };
}

export interface ClaimSource {
  readonly claim_id: Uuid;
  readonly claim_kind: string;
  readonly predicate: string;
  readonly object_json: Readonly<Record<string, unknown>>;
  readonly actor_type: ActorType;
  readonly authority_score: Decimal;
  readonly evidence_id: Uuid | null;
  readonly recorded_at: Instant;
}

export interface GroundingEdge {
  readonly support_id: Uuid;
  readonly relation: SupportRelation;
  readonly source_kind: "EVIDENCE" | "CLAIM";
  readonly source_id: Uuid;
  readonly weight: Decimal;
  readonly reason_code: string;
  readonly created_at: Instant;
  readonly source: EvidenceSource | ClaimSource;
}

export interface BeliefVersion {
  readonly belief_version_id: Uuid;
  readonly version_no: number;
  readonly value_type?: string;
  readonly value_json?: Readonly<Record<string, unknown>>;
  readonly epistemic_status: string;
  readonly belief_confidence?: Decimal;
  readonly valid_from?: Instant | null;
  readonly valid_to?: Instant | null;
  readonly recorded_at?: Instant;
  readonly superseded_at?: Instant | null;
  readonly superseded_by_version_no?: number | null;
  readonly supersession_reason_codes?: readonly string[];
  readonly kernel_decision_id: Uuid;
  readonly grounding_count?: number;
  readonly is_current?: boolean;
}

export interface Belief {
  readonly belief_id: Uuid;
  readonly subject_type: string;
  readonly subject_id: Uuid;
  readonly predicate: string;
  /** Computed, never stored (section 8.11.1). */
  readonly grounded: boolean;
  readonly current_version: BeliefVersion;
  readonly grounding: readonly GroundingEdge[];
  readonly lineage: readonly BeliefVersion[];
}

export interface Fulfillment {
  readonly fulfillment_id: Uuid;
  readonly amount: Money;
  readonly fulfilled_at: Instant;
  readonly admission_status: string;
  readonly confidence: Decimal;
  readonly evidence_id: Uuid;
}

export interface ProofCommitment {
  readonly commitment_id: Uuid;
  readonly description: string;
  readonly status: string;
  readonly currency: string;
  readonly committed_amount: Money;
  readonly fulfilled_amount: Money;
  readonly outstanding_amount: Money;
  readonly due_at: Instant | null;
  readonly source_claim_id: Uuid | null;
  readonly fulfillments: readonly Fulfillment[];
}

export interface ConflictSide {
  readonly source_kind: string;
  readonly source_id: Uuid;
  readonly summary: string;
}

export interface ProofConflict {
  readonly conflict_id: Uuid;
  readonly conflict_type: string;
  readonly predicate: string;
  readonly status: string;
  readonly severity: string;
  readonly requires_human: boolean;
  readonly detected_at: Instant;
  readonly resolved_at: Instant | null;
  readonly resolution_reason_code: string | null;
  readonly left: ConflictSide;
  readonly right: ConflictSide;
  readonly canonical_belief_version_id: Uuid;
}

export interface Derivation {
  readonly name: string;
  readonly target: { readonly kind: string; readonly id: Uuid };
  readonly expression: string;
  readonly inputs: Readonly<Record<string, Money>>;
  readonly result: Money;
  readonly deterministic_derivation: boolean;
  readonly grounding_exempt: boolean;
}

export interface StateTransition {
  readonly case_revision: number;
  readonly transition_type: string;
  readonly from_state: string;
  readonly to_state: string;
  readonly reason_code: string;
  readonly kernel_decision_id: Uuid;
  readonly trace_id: Uuid | null;
  readonly recorded_at: Instant;
}

export interface ActionRelyingOnState {
  readonly action_intent_id: Uuid;
  readonly action_type: string;
  readonly status: string;
  readonly basis_case_revision: number;
  readonly supporting_belief_versions: readonly Uuid[];
  readonly still_current: boolean;
}

export interface IntegrityWarning {
  readonly code: string;
  readonly belief_id?: Uuid;
  readonly belief_version_id?: Uuid;
  readonly message: string;
}

export interface StateProofResponse {
  readonly schema_version: string;
  readonly case_id: Uuid;
  readonly case_revision: number;
  readonly case_status: string;
  readonly generated_at: Instant;
  /** Always true: this endpoint never calls a model. */
  readonly deterministic: boolean;
  readonly model_used: string | null;
  readonly beliefs: readonly Belief[];
  readonly commitments: readonly ProofCommitment[];
  readonly conflicts: readonly ProofConflict[];
  readonly derivations: readonly Derivation[];
  readonly state_transitions: readonly StateTransition[];
  readonly actions_relying_on_this_state: readonly ActionRelyingOnState[];
  readonly excluded: {
    readonly retracted_evidence_count: number;
    readonly superseded_belief_versions_hidden: number;
    readonly retraction_filter_applied: boolean;
  };
  readonly integrity_warnings?: readonly IntegrityWarning[];
}

/* -- Section 8.16 GET /v1/triggers ------------------------------------------ */

export type PredicateAst =
  | { readonly op: "FIELD"; readonly path: string }
  | { readonly op: "CONST"; readonly value: string }
  | { readonly op: string; readonly args: readonly PredicateAst[] };

export interface TriggerItem {
  readonly trigger_id: Uuid;
  readonly case_id: Uuid;
  readonly case_title: string;
  readonly trigger_type: TriggerType;
  readonly state: TriggerState;
  readonly not_before: Instant;
  readonly expires_at: Instant | null;
  readonly basis_case_revision: number;
  readonly evaluation_version: number;
  readonly last_evaluated_at: Instant | null;
  readonly last_result: TriggerResult | null;
  readonly last_reason_code: string | null;
  readonly schedule_name: string | null;
  /** Deterministic template output, not a model sentence. */
  readonly predicate_summary: string;
  readonly predicate_ast: PredicateAst;
  readonly last_evaluation: {
    readonly evaluated_at: Instant;
    readonly result: TriggerResult;
    readonly case_revision_at_evaluation: number;
    readonly field_values: Readonly<Record<string, unknown>>;
  } | null;
}

/* -- Section 8.20 GET /v1/artifacts/{id} ------------------------------------ */

export interface ContentBlockSummary {
  readonly block_id: string;
  readonly kind: string;
  readonly char_count: number;
}

export interface ArtifactResponse {
  readonly artifact_id: Uuid;
  readonly source_type: string;
  readonly mime_type: string;
  readonly filename: string | null;
  readonly size_bytes: number;
  readonly content_sha256: string;
  readonly sender_display: string | null;
  readonly recipient_display: string | null;
  readonly subject: string | null;
  readonly source_message_id: string | null;
  readonly received_at: Instant;
  readonly event_time: Instant | null;
  readonly parser_status: string;
  readonly parser_version: string;
  readonly parser_metadata: Readonly<Record<string, unknown>>;
  readonly content_blocks_summary?: readonly ContentBlockSummary[];
  readonly evidence_item_count: number;
  readonly linked_cases: readonly { readonly case_id: Uuid; readonly title: string }[];
  readonly agent_run_id: Uuid | null;
  readonly trace_id: Uuid | null;
  readonly download_url: string | null;
  readonly download_url_expires_at: Instant | null;
}

/* -- Section 8.21 GET /v1/ingest-alias -------------------------------------- */

export interface IngestAliasResponse {
  /** null when the deployment stores only the HMAC. The UI then says "rotate to reveal". */
  readonly alias_display: string | null;
  readonly status: string;
  readonly created_at: Instant;
  readonly rotated_at: Instant | null;
  readonly artifacts_received: number;
  readonly last_received_at: Instant | null;
}

/* -- Section 8.24 GET /v1/action-intents/{id} ------------------------------- */

export interface GroundedSentence {
  readonly sentence_or_span: string;
  readonly support_ids: readonly Uuid[];
  readonly support_kinds: readonly string[];
  readonly validated: boolean;
}

export interface ActionDraft {
  readonly subject: string;
  readonly body: string;
  readonly claims: readonly GroundedSentence[];
  readonly requested_outcome: string;
  readonly tone: string;
  readonly unresolved_risks: readonly string[];
}

export interface SupportingBeliefVersion {
  readonly belief_version_id: Uuid;
  readonly belief_id: Uuid;
  readonly predicate: string;
  readonly version_no: number;
  readonly still_current: boolean;
}

export interface ActionApproval {
  readonly approved_by_user_id: Uuid;
  readonly approved_at: Instant;
  readonly approval_draft_sha256: string;
  readonly approved_case_revision: number;
}

export interface ActionExecution {
  readonly attempt_no: number;
  readonly status: string;
  readonly provider: string;
  readonly provider_correlation_id: string | null;
  readonly executed_at: Instant | null;
  readonly error_code: string | null;
}

export interface ActionIntentResponse {
  readonly action_intent_id: Uuid;
  readonly case_id: Uuid;
  readonly action_type: string;
  readonly status: string;
  readonly recipient: string;
  readonly recipient_allowlisted: boolean;
  readonly draft: ActionDraft;
  readonly draft_sha256: string;
  readonly rationale: string;
  readonly supporting_belief_versions: readonly SupportingBeliefVersion[];
  readonly state_proof_url: string;
  readonly basis_case_revision: number;
  readonly current_case_revision: number;
  readonly is_stale: boolean;
  readonly warnings: readonly { readonly code: string; readonly message: string }[];
  readonly approval: ActionApproval | null;
  readonly executions: readonly ActionExecution[];
  readonly created_at: Instant;
  readonly created_by_agent_run_id: Uuid | null;
  readonly trace_id: Uuid | null;
}

/* -- Section 7.3 409 ACTION_STALE ------------------------------------------- */

export interface ActionStaleDetail {
  readonly basis_case_revision: number;
  readonly current_case_revision: number;
  readonly changed_since: readonly {
    readonly kind: string;
    readonly headline: string;
    readonly case_revision: number;
  }[];
  readonly state_proof_url: string;
}

/* -- Section 8.28 GET /v1/traces/{id} --------------------------------------- */

export interface TraceNodeRef {
  readonly table: string;
  readonly column: string;
  readonly value: string;
  readonly cardinality?: string;
}

export interface TraceNode {
  readonly id: string;
  readonly type: TraceNodeType;
  readonly status: TraceNodeStatus;
  readonly parent_id?: string;
  readonly started_at: Instant;
  readonly duration_ms: number;
  readonly summary: string;
  /** Allowlisted at construction: ids, counts, durations, versions, revisions. Never prose. */
  readonly attributes: Readonly<Record<string, unknown>>;
  readonly refs?: readonly TraceNodeRef[];
}

export interface TraceEdge {
  readonly from: string;
  readonly to: string;
}

export interface TraceResponse {
  readonly trace_id: Uuid;
  readonly started_at: Instant;
  readonly finished_at: Instant | null;
  readonly duration_ms: number;
  readonly status: string;
  readonly case_ids: readonly Uuid[];
  readonly nodes: readonly TraceNode[];
  readonly edges: readonly TraceEdge[];
  readonly boundary: {
    readonly deterministic_node_ids: readonly string[];
    readonly model_node_ids: readonly string[];
    readonly note: string;
  };
}

/* -- Section 8.31 GET /v1/judge-mode/counterfactual/{id} -------------------- */

export interface ParityEntry {
  readonly off: string;
  readonly on: string;
  readonly equal: boolean;
}

/** The six normative checks. all_equal is the conjunction, and it gates rendering. */
export interface Parity {
  readonly artifact_id: ParityEntry;
  readonly artifact_sha256: ParityEntry;
  readonly model_id: ParityEntry;
  readonly prompt_version: ParityEntry;
  readonly graph_version: ParityEntry;
  readonly decode_params_sha256: ParityEntry;
  readonly all_equal: boolean;
}

export const PARITY_CHECK_KEYS = [
  "artifact_id",
  "artifact_sha256",
  "model_id",
  "prompt_version",
  "graph_version",
  "decode_params_sha256",
] as const;
export type ParityCheckKey = (typeof PARITY_CHECK_KEYS)[number];

export interface CounterfactualOutput {
  readonly headline: string;
  readonly classification: string;
  readonly case_linked: {
    readonly case_id: Uuid;
    readonly title: string;
    readonly status_before: string;
    readonly status_after: string;
    readonly resolved_days_ago: number;
  } | null;
  readonly conflicts_detected: number;
  readonly recommended_action: string;
  readonly draft_text: string | null;
}

export interface CounterfactualArm {
  readonly mode: "MEMORY_OFF" | "MEMORY_ON";
  /** Present on the MEMORY_ON arm. Header copy is selected from this, never a constant. */
  readonly strategy?: string;
  readonly retrieval_enabled: boolean;
  readonly canonical_memory_enabled: boolean;
  /** Counted at query time. Never a constant. */
  readonly corpus_size_visible: number;
  readonly model_id: string;
  readonly duration_ms: number;
  readonly output: CounterfactualOutput;
  readonly why?: string;
  readonly grounding?: readonly {
    readonly belief_id: Uuid;
    readonly predicate: string;
    readonly supporting_evidence_id: Uuid;
    readonly observed_at: Instant;
    readonly source_authority: Decimal;
  }[];
  readonly kernel_decision_id?: Uuid;
  readonly case_revision_before?: number;
  readonly case_revision_after?: number;
  readonly trace_url?: string;
  readonly error?: { readonly code: string; readonly message: string };
}

export interface DeltaEntry {
  readonly off: number;
  readonly on: number;
}

export interface CounterfactualResponse {
  readonly counterfactual_id: Uuid;
  readonly status: "RUNNING" | "COMPLETED" | "FAILED" | "PARTIAL";
  readonly artifact_id: Uuid;
  readonly artifact_summary: string;
  readonly completed_at: Instant | null;
  readonly parity: Parity;
  readonly memory_off: CounterfactualArm;
  readonly memory_on: CounterfactualArm;
  readonly delta: {
    readonly conflicts_detected: DeltaEntry;
    readonly cases_reopened: DeltaEntry;
    readonly actions_recommended: DeltaEntry;
    readonly evidence_recalled_days: DeltaEntry;
    readonly verdict: string;
  };
  readonly safety: {
    readonly memory_off_wrote_canonical_state: boolean;
    readonly memory_off_admitted_evidence: boolean;
    readonly memory_off_had_proposal_tool: boolean;
    readonly case_revision_changed_by_counterfactual: boolean;
  };
}

/* -- Section 8.5 GET /v1/contexts ------------------------------------------- */

export interface ContextListItem {
  readonly context_id: Uuid;
  readonly title: string;
  readonly context_type: string;
  readonly status: string;
  readonly created_at: Instant;
  readonly case_count: number;
  readonly open_case_count: number;
}

/* -- Section 8.6 GET /v1/relationships -------------------------------------- */

/**
 * The list shape, which is NOT the detail shape.
 *
 * Section 8.6 returns neither `cases[]` nor `summary`, so a list surface cannot render an
 * outstanding figure. Reusing `RelationshipResponse` here would let a component read
 * `summary.outstanding` off a payload that never carries it, and TypeScript would have
 * agreed with it. The two shapes are therefore kept apart.
 */
export interface RelationshipListItem {
  readonly relationship_id: Uuid;
  readonly counterparty: CounterpartyRef;
  readonly label: string;
  readonly relationship_type: string;
  readonly status: string;
  readonly external_account_ref_masked: string | null;
  readonly valid_from: Instant | null;
  readonly valid_to: Instant | null;
  readonly revision: number;
  readonly open_case_count: number;
  readonly attention_level: AttentionLevel;
  readonly last_activity_at: Instant;
  readonly updated_at: Instant;
}

/* -- Section 8.23 GET /v1/action-intents ------------------------------------ */

/**
 * Again a list shape distinct from its detail shape, and the distinction matters more
 * here than anywhere else on the surface.
 *
 * The list carries `recipient_masked`, `subject_preview`, and `warning_count`. It does
 * NOT carry the draft, the sentence grounding, the draft hash, or the supporting belief
 * versions. An approvals index built on `ActionIntentResponse` would have compiled and
 * then rendered blanks where the grounding should be -- which is exactly the failure this
 * application exists to prevent. Approval is only possible on the detail screen, where
 * the evidence each sentence rests on is actually present.
 */
export interface ActionIntentListItem {
  readonly action_intent_id: Uuid;
  readonly case_id: Uuid;
  readonly case_title: string;
  readonly counterparty_display_name: string;
  readonly action_type: string;
  readonly status: string;
  readonly recipient_masked: string;
  readonly subject_preview: string;
  readonly basis_case_revision: number;
  readonly current_case_revision: number;
  readonly is_stale: boolean;
  readonly warning_count: number;
  readonly created_at: Instant;
  readonly created_by_agent_run_id: Uuid | null;
}

/* -- Section 8.17 GET /v1/artifacts ----------------------------------------- */

/** "the §8.20 response minus `download_url` and `content_blocks_summary`", verbatim. */
export type ArtifactListItem = Omit<
  ArtifactResponse,
  "download_url" | "download_url_expires_at" | "content_blocks_summary"
>;
