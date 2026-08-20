import type { ApiErrorBody } from "./contract";

/**
 * The API client.
 *
 * Phase 8's control plane does not exist yet, so this application runs against one of two
 * sources and is explicit about which:
 *
 *   LIVE     `PV_API_BASE_URL` is set. Every read is an HTTP request against the real API.
 *   FIXTURE  it is not. Reads are served from `src/fixtures`, and a non-dismissible
 *            banner says so on every screen.
 *
 * There is no third mode, and in particular there is no mode in which a fixture is served
 * without saying so. That is the difference between a prototype and a lie.
 *
 * Failures are values, not exceptions. A screen that cannot read an endpoint renders an
 * explicit error state naming the endpoint; it does not fall back to a fixture, and it
 * does not render zero.
 */

export type DataSource = "LIVE" | "FIXTURE";

export interface ApiFailure {
  readonly ok: false;
  readonly status: number;
  readonly code: string;
  readonly message: string;
  readonly traceId: string | null;
  readonly path: string;
}

export interface ApiSuccess<T> {
  readonly ok: true;
  readonly data: T;
  readonly source: DataSource;
  /** Echoed from `X-Provenance-Case-Revision` where the endpoint sets it. */
  readonly caseRevision: number | null;
}

export type ApiResult<T> = ApiSuccess<T> | ApiFailure;

export function apiBaseUrl(): string | null {
  const raw = process.env.PV_API_BASE_URL ?? process.env.NEXT_PUBLIC_PV_API_BASE_URL ?? "";
  const trimmed = raw.trim();
  return trimmed === "" ? null : trimmed.replace(/\/+$/, "");
}

export function dataSource(): DataSource {
  return apiBaseUrl() === null ? "FIXTURE" : "LIVE";
}

function failure(
  path: string,
  status: number,
  code: string,
  message: string,
  traceId: string | null = null,
): ApiFailure {
  return { ok: false, status, code, message, traceId, path };
}

/**
 * One live GET.
 *
 * `no-store` is deliberate. Judge Mode must re-fetch on reload -- a cached trace is
 * indistinguishable from a scripted one, and section 1 of `frontend/32_JUDGE_MODE.md`
 * forbids a client-side store that holds trace data between navigations.
 */
async function liveGet<T>(path: string, token: string | null): Promise<ApiResult<T>> {
  const base = apiBaseUrl();
  if (base === null) return failure(path, 0, "NO_API_CONFIGURED", "PV_API_BASE_URL is not set.");

  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
      },
    });
  } catch (cause) {
    return failure(
      path,
      0,
      "NETWORK_UNREACHABLE",
      cause instanceof Error ? cause.message : "fetch failed",
    );
  }

  const revisionHeader = response.headers.get("X-Provenance-Case-Revision");
  const caseRevision = revisionHeader === null ? null : Number.parseInt(revisionHeader, 10);

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return failure(path, response.status, "MALFORMED_RESPONSE", "Response body was not JSON.");
  }

  if (!response.ok) {
    const envelope = body as Partial<ApiErrorBody>;
    return failure(
      path,
      response.status,
      envelope.error?.code ?? "UNKNOWN_ERROR",
      envelope.error?.message ?? `Request failed with ${response.status}.`,
      envelope.error?.trace_id ?? null,
    );
  }

  return {
    ok: true,
    data: body as T,
    source: "LIVE",
    caseRevision: caseRevision === null || Number.isNaN(caseRevision) ? null : caseRevision,
  };
}

export interface GetOptions {
  readonly token?: string | null;
  readonly query?: Readonly<
    Record<string, string | number | boolean | readonly string[] | null | undefined>
  >;
}

function buildPath(path: string, query: GetOptions["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      for (const entry of value) params.append(key, entry);
    } else {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs === "" ? path : `${path}?${qs}`;
}

/**
 * Read one endpoint.
 *
 * The fixture branch is loaded dynamically so that a live deployment never pulls the
 * fixture module into its bundle at all. `scripts/check-render-honesty.mjs` rule R1
 * permits exactly one importer of `src/fixtures`, and this is the call that reaches it.
 */
export async function apiGet<T>(path: string, options: GetOptions = {}): Promise<ApiResult<T>> {
  const full = buildPath(path, options.query);
  if (dataSource() === "LIVE") {
    return liveGet<T>(full, options.token ?? null);
  }
  const { readFixture } = await import("./fixture-source");
  return readFixture<T>(path, options.query ?? {});
}
