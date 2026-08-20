import { cache } from "react";
import { getDashboard, getMe, getVersion } from "@/lib/api/reads";
import type { ApiResult } from "@/lib/api/client";
import type { DashboardResponse, MeResponse, VersionResponse } from "@/lib/api/contract";

/**
 * Shell reads, deduplicated per request.
 *
 * The app shell needs `GET /v1/me` for the timezone, the feature flags, and the fixture
 * banner, and `GET /v1/version` for the build stamp. The dashboard is read here too
 * because the status bar renders the server's own clock from `generated_at` -- the demo
 * runs against a seeded clock, and rendering the browser's wall time instead would put a
 * date on screen that no row contains.
 *
 * `React.cache` collapses the repeats: a layout and the page beneath it that both ask for
 * `me` issue one request.
 */

export const loadMe = cache((): Promise<ApiResult<MeResponse>> => getMe());
export const loadVersion = cache((): Promise<ApiResult<VersionResponse>> => getVersion());
export const loadDashboard = cache((): Promise<ApiResult<DashboardResponse>> => getDashboard());

/** UTC fallback. The timezone is a rendered value; when we cannot read it, say so. */
export const FALLBACK_TIMEZONE = "UTC";

export function timeZoneOf(me: ApiResult<MeResponse>): string {
  return me.ok ? me.data.timezone : FALLBACK_TIMEZONE;
}
