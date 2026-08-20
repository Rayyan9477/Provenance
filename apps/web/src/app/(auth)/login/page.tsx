import { ForbiddenState } from "@/components/primitives/States";

/**
 * S14 -- sign-in.
 *
 * Three things about this screen are load-bearing.
 *
 * There is no password field, and there never will be. Authentication is delegated;
 * `frontend/30_UX_SPEC.md` section 5.2 is explicit that the application never sees a
 * credential. A field here would be a lie about the architecture before the reader has
 * even signed in, and `G0.3` scans this repository for anything credential-shaped.
 *
 * The screen's state is chosen by the identity provider, never by the visitor. The
 * returned design carries a three-way state picker (DEFAULT, SIGNING IN, UNPROVISIONED)
 * and labels it a review affordance, noting that in production the grant lookup chooses.
 * That is honoured literally: the only thing that moves this screen off its default is a
 * parameter the provider set on the callback.
 *
 * `?next=` is validated against the route patterns this application actually serves. An
 * off-pattern value is discarded silently and the reader lands on the dashboard, because
 * an open redirect on a sign-in screen is how a session gets handed to somebody else.
 */

export const dynamic = "force-dynamic";

/** The routes a sign-in may return a reader to. Everything else is discarded. */
const NEXT_ALLOWLIST: readonly RegExp[] = [
  /^\/dashboard(\?.*)?$/,
  /^\/cases\/[^/]+(\/proof)?(\?.*)?$/,
  /^\/relationships(\/[^/]+)?(\?.*)?$/,
  /^\/artifacts(\/[^/]+)?(\?.*)?$/,
  /^\/actions(\/[^/]+)?(\?.*)?$/,
  /^\/judge(\/counterfactual)?(\?.*)?$/,
  /^\/ingest(\?.*)?$/,
  /^\/watches(\?.*)?$/,
  /^\/search(\?.*)?$/,
  /^\/export(\?.*)?$/,
  /^\/proof(\?.*)?$/,
  /^\/settings(\?.*)?$/,
];

function safeNext(value: string | undefined): string {
  if (value === undefined) return "/dashboard";
  if (!value.startsWith("/") || value.startsWith("//")) return "/dashboard";
  return NEXT_ALLOWLIST.some((pattern) => pattern.test(value)) ? value : "/dashboard";
}

interface PageProps {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function LoginPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const first = (key: string): string | undefined => {
    const value = params[key];
    return typeof value === "string" ? value : undefined;
  };

  const next = safeNext(first("next"));
  const providerError = first("error");
  const unprovisioned = first("code") === "USER_NOT_PROVISIONED";
  const authorizeUrl = process.env["NEXT_PUBLIC_PV_AUTHORIZE_URL"] ?? null;

  return (
    <main className="pv-auth" id="pv-main">
      <div className="pv-card">
        <div className="pv-card-pad">
          <p className="pv-wordmark">Provenance</p>
          <p className="pv-promise" style={{ marginTop: "var(--pv-space-2)" }}>
            A system of record for the institutions that already have one of you.
          </p>
        </div>

        <div
          className="pv-card-pad"
          style={{ borderTop: "var(--pv-rule) solid var(--pv-line-hairline)" }}
        >
          {unprovisioned ? (
            <ForbiddenState heading="Your account is not set up yet.">
              <p>
                Access is managed by institutional identity grants. There is nothing to retry here:
                Provenance does not create accounts on demand, so signing in again cannot change the
                answer. Your identity administrator issues the grant.
              </p>
            </ForbiddenState>
          ) : providerError !== undefined ? (
            <div className="pv-state" data-kind="ERROR" role="alert">
              <p className="pv-title">We could not complete sign-in.</p>
              <p className="pv-prose">
                The identity provider returned an error. The detail it gave is logged against the
                request id rather than shown here, because provider error text is
                attacker-influenceable and this screen is the one place a reader has no context to
                judge it.
              </p>
              {authorizeUrl === null ? null : (
                <p style={{ marginTop: "var(--pv-space-4)" }}>
                  <a
                    className="pv-button"
                    data-emphasis="primary"
                    href={`${authorizeUrl}&state=${encodeURIComponent(next)}`}
                  >
                    Try signing in again
                  </a>
                </p>
              )}
            </div>
          ) : (
            <>
              <p className="pv-label">Delegated authentication</p>
              {authorizeUrl === null ? (
                <div
                  className="pv-state"
                  data-kind="EMPTY"
                  style={{ marginTop: "var(--pv-space-3)" }}
                >
                  <p className="pv-title">No identity provider is configured in this build.</p>
                  <p className="pv-prose">
                    Sign-in redirects to a hosted authorization endpoint, and this deployment has
                    not been given one. No control is shown, because a button that went nowhere
                    would suggest the failure was yours.
                  </p>
                </div>
              ) : (
                <p style={{ marginTop: "var(--pv-space-3)" }}>
                  <a
                    className="pv-button"
                    data-emphasis="primary"
                    href={`${authorizeUrl}&state=${encodeURIComponent(next)}`}
                  >
                    Continue with your passkey
                  </a>
                </p>
              )}

              <p className="pv-prose" style={{ marginTop: "var(--pv-space-4)" }}>
                Provenance operates on zero credential knowledge. It never stores, receives or
                inspects an authentication secret, and there is no password field on this page for
                the same reason there is no password in the database.
              </p>
            </>
          )}
        </div>

        <div
          className="pv-card-pad"
          style={{ borderTop: "var(--pv-rule) solid var(--pv-line-hairline)" }}
        >
          <p className="pv-label">After sign-in</p>
          <p className="pv-mono">{next}</p>
          <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
            Validated against the routes this application serves. Anything else is discarded and you
            land on the dashboard.
          </p>
        </div>
      </div>
    </main>
  );
}
