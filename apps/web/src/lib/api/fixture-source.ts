/**
 * The one module permitted to import a fixture.
 *
 * `scripts/check-render-honesty.mjs` rule R1 names this file explicitly. Everything
 * upstream of it -- every component, every page -- sees only `ApiResult<T>` and cannot
 * tell a fixture from a live response, which is the point: when Phase 8's control plane
 * lands, `PV_API_BASE_URL` is set, this module stops being called, and no rendering code
 * changes at all.
 *
 * The routing table below is deliberately literal rather than clever. A path with no
 * entry returns a 404-shaped failure, exactly as the real API would, so a screen written
 * against an endpoint that does not exist yet fails visibly instead of rendering blanks.
 */

import * as hero from "@/fixtures/hero.fixture";
import type { ApiResult } from "./client";

type Query = Readonly<
  Record<string, string | number | boolean | readonly string[] | null | undefined>
>;

function found<T>(data: T): ApiResult<T> {
  return { ok: true, data: data as T, source: "FIXTURE", caseRevision: null };
}

function notFound<T>(path: string, code: string): ApiResult<T> {
  return {
    ok: false,
    status: 404,
    code,
    message: `No fixture is defined for ${path}.`,
    traceId: null,
    path,
  };
}

function segment(path: string, index: number): string | undefined {
  return path.split("/").filter(Boolean)[index];
}

export async function readFixture<T>(path: string, query: Query): Promise<ApiResult<T>> {
  const parts = path.split("/").filter(Boolean);

  if (path === "/v1/version") return found(hero.heroVersion) as ApiResult<T>;
  if (path === "/v1/me") return found(hero.heroMe) as ApiResult<T>;
  if (path === "/v1/dashboard") return found(hero.heroDashboard) as ApiResult<T>;
  if (path === "/v1/contexts") return found(hero.heroContexts) as ApiResult<T>;
  if (path === "/v1/relationships") return found(hero.heroRelationshipList) as ApiResult<T>;
  if (path === "/v1/triggers") return found(hero.heroTriggers) as ApiResult<T>;
  if (path === "/v1/artifacts") return found(hero.heroArtifacts) as ApiResult<T>;
  if (path === "/v1/ingest-alias") return found(hero.heroIngestAlias) as ApiResult<T>;
  if (path === "/v1/action-intents") return found(hero.heroActionIntentList) as ApiResult<T>;

  if (parts[0] === "v1" && parts[1] === "relationships" && parts.length === 3) {
    const id = segment(path, 2) ?? "";
    const relationship = hero.heroRelationships[id];
    return relationship
      ? (found(relationship) as ApiResult<T>)
      : notFound<T>(path, "RELATIONSHIP_NOT_FOUND");
  }

  if (parts[0] === "v1" && parts[1] === "cases") {
    const id = segment(path, 2) ?? "";
    if (parts.length === 3) {
      const record = hero.heroCases[id];
      return record ? (found(record) as ApiResult<T>) : notFound<T>(path, "CASE_NOT_FOUND");
    }
    if (parts.length === 4 && parts[3] === "timeline") {
      const timeline = hero.heroTimelines[id];
      if (!timeline) return notFound<T>(path, "CASE_NOT_FOUND");
      const kinds = query["kind"];
      if (kinds === undefined || kinds === null) return found(timeline) as ApiResult<T>;
      const wanted = new Set(Array.isArray(kinds) ? kinds : [String(kinds)]);
      return found({
        ...timeline,
        items: timeline.items.filter((entry) => wanted.has(entry.kind)),
      }) as ApiResult<T>;
    }
    if (parts.length === 4 && parts[3] === "state-proof") {
      const proof = hero.heroStateProofs[id];
      return proof ? (found(proof) as ApiResult<T>) : notFound<T>(path, "CASE_NOT_FOUND");
    }
  }

  if (parts[0] === "v1" && parts[1] === "artifacts" && parts.length === 3) {
    const artifact = hero.heroArtifactsById[segment(path, 2) ?? ""];
    return artifact ? (found(artifact) as ApiResult<T>) : notFound<T>(path, "ARTIFACT_NOT_FOUND");
  }

  if (parts[0] === "v1" && parts[1] === "action-intents" && parts.length === 3) {
    const intent = hero.heroActionIntents[segment(path, 2) ?? ""];
    return intent ? (found(intent) as ApiResult<T>) : notFound<T>(path, "ACTION_INTENT_NOT_FOUND");
  }

  if (parts[0] === "v1" && parts[1] === "traces" && parts.length === 3) {
    return segment(path, 2) === hero.TRACE_HERO
      ? (found(hero.heroTrace) as ApiResult<T>)
      : notFound<T>(path, "TRACE_NOT_FOUND");
  }

  if (parts[0] === "v1" && parts[1] === "judge-mode" && parts[2] === "counterfactual") {
    return segment(path, 3) === hero.COUNTERFACTUAL_ID
      ? (found(hero.heroCounterfactual) as ApiResult<T>)
      : notFound<T>(path, "COUNTERFACTUAL_NOT_FOUND");
  }

  return notFound<T>(path, "NOT_FOUND");
}

/**
 * Ids the fixture-backed screens link to.
 *
 * A route needs a concrete id to construct a link, and constructing one in a component
 * would violate R2. This is the seam: the ids come from the fixture module, through the
 * one file allowed to read it, and a live deployment gets them from the API instead.
 */
export const fixtureEntryPoints = {
  heroCaseId: hero.CASE_ISP,
  heroTraceId: hero.TRACE_HERO,
  heroCounterfactualId: hero.COUNTERFACTUAL_ID,
} as const;

/**
 * The case ids the State Proof index offers.
 *
 * Live, the index derives them from `GET /v1/dashboard` and the relationship files. In
 * fixture mode there is no dashboard call to derive from at the point the index is built,
 * so the ids come from here -- still through the one permitted importer, still never from
 * a component.
 */
export const fixtureCaseIds: readonly string[] = hero.CASE_IDS;
