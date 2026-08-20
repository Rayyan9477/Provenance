import { apiGet, dataSource } from "./client";
import type { ApiResult } from "./client";
import type {
  ActionIntentListItem,
  ActionIntentResponse,
  ArtifactListItem,
  ArtifactResponse,
  ContextListItem,
  CaseResponse,
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
} from "./contract";

/**
 * Every read the application performs, in one place.
 *
 * Each function names its endpoint from `specs/15_API_SPEC.md` and returns an
 * `ApiResult`. None of them throws, and none of them substitutes a default on failure --
 * the caller renders an error state naming the endpoint, because "we could not read this"
 * and "this is empty" are different facts about the reader's record.
 */

export function getVersion(): Promise<ApiResult<VersionResponse>> {
  return apiGet<VersionResponse>("/v1/version");
}

export function getMe(token?: string | null): Promise<ApiResult<MeResponse>> {
  return apiGet<MeResponse>("/v1/me", { token });
}

export interface DashboardQuery {
  readonly contextId?: string | null;
  readonly attentionOnly?: boolean;
  readonly status?: readonly string[];
}

export function getDashboard(query: DashboardQuery = {}): Promise<ApiResult<DashboardResponse>> {
  return apiGet<DashboardResponse>("/v1/dashboard", {
    query: {
      context_id: query.contextId ?? undefined,
      attention_only: query.attentionOnly === true ? "true" : undefined,
      status: query.status,
    },
  });
}

export function getContexts(): Promise<ApiResult<Paginated<ContextListItem>>> {
  return apiGet<Paginated<ContextListItem>>("/v1/contexts");
}

export function getRelationships(): Promise<ApiResult<Paginated<RelationshipListItem>>> {
  return apiGet<Paginated<RelationshipListItem>>("/v1/relationships");
}

export function getRelationship(id: string): Promise<ApiResult<RelationshipResponse>> {
  return apiGet<RelationshipResponse>(`/v1/relationships/${id}`);
}

export function getCase(id: string): Promise<ApiResult<CaseResponse>> {
  return apiGet<CaseResponse>(`/v1/cases/${id}`);
}

export interface TimelineQuery {
  readonly kind?: readonly string[];
  readonly sinceRevision?: number | null;
}

export function getTimeline(
  id: string,
  query: TimelineQuery = {},
): Promise<ApiResult<Paginated<TimelineEntry>>> {
  return apiGet<Paginated<TimelineEntry>>(`/v1/cases/${id}/timeline`, {
    query: { kind: query.kind, since_revision: query.sinceRevision ?? undefined },
  });
}

export interface StateProofQuery {
  readonly includeRetracted?: boolean;
  readonly beliefId?: readonly string[];
}

export function getStateProof(
  id: string,
  query: StateProofQuery = {},
): Promise<ApiResult<StateProofResponse>> {
  return apiGet<StateProofResponse>(`/v1/cases/${id}/state-proof`, {
    query: {
      include_retracted: query.includeRetracted === true ? "true" : undefined,
      belief_id: query.beliefId,
    },
  });
}

export function getTriggers(): Promise<ApiResult<Paginated<TriggerItem>>> {
  return apiGet<Paginated<TriggerItem>>("/v1/triggers");
}

export function getArtifacts(): Promise<ApiResult<Paginated<ArtifactListItem>>> {
  return apiGet<Paginated<ArtifactListItem>>("/v1/artifacts");
}

export function getArtifact(id: string): Promise<ApiResult<ArtifactResponse>> {
  return apiGet<ArtifactResponse>(`/v1/artifacts/${id}`);
}

export function getIngestAlias(): Promise<ApiResult<IngestAliasResponse>> {
  return apiGet<IngestAliasResponse>("/v1/ingest-alias");
}

export function getActionIntents(): Promise<ApiResult<Paginated<ActionIntentListItem>>> {
  return apiGet<Paginated<ActionIntentListItem>>("/v1/action-intents");
}

export function getActionIntent(id: string): Promise<ApiResult<ActionIntentResponse>> {
  return apiGet<ActionIntentResponse>(`/v1/action-intents/${id}`);
}

export function getTrace(id: string): Promise<ApiResult<TraceResponse>> {
  return apiGet<TraceResponse>(`/v1/traces/${id}`);
}

export function getCounterfactual(id: string): Promise<ApiResult<CounterfactualResponse>> {
  return apiGet<CounterfactualResponse>(`/v1/judge-mode/counterfactual/${id}`);
}

export interface EntryPoints {
  readonly heroCaseId: string | null;
  readonly heroTraceId: string | null;
  readonly heroCounterfactualId: string | null;
}

/**
 * The ids a screen needs in order to build a link.
 *
 * A component cannot contain an id (rule R2), so it asks for one. Live, they are derived
 * from real rows: the most urgent case on the dashboard, and the trace its timeline
 * points at. In fixture mode they come from the fixture module through the single
 * permitted importer.
 *
 * The counterfactual id has no live derivation because it is created by
 * `POST /v1/judge-mode/counterfactual`, which is a Phase 8 mutation this build does not
 * yet perform. Live, it is null, and the panel says so rather than inventing one.
 */
export async function getEntryPoints(): Promise<EntryPoints> {
  if (dataSource() === "FIXTURE") {
    const { fixtureEntryPoints } = await import("./fixture-source");
    return fixtureEntryPoints;
  }

  const dashboard = await getDashboard({ attentionOnly: true });
  if (!dashboard.ok) return { heroCaseId: null, heroTraceId: null, heroCounterfactualId: null };

  const first = dashboard.data.cases_attention[0];
  if (first === undefined) {
    return { heroCaseId: null, heroTraceId: null, heroCounterfactualId: null };
  }

  const timeline = await getTimeline(first.case_id);
  const traceId = timeline.ok
    ? (timeline.data.items.find((entry) => entry.trace_id !== null)?.trace_id ?? null)
    : null;

  return { heroCaseId: first.case_id, heroTraceId: traceId, heroCounterfactualId: null };
}
