import { ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { DisclosureSwitch } from "@/components/primitives/Disclosure";
import { SurfaceSwitch } from "@/components/shell/SurfaceSwitch";
import { BuildStamp } from "@/components/primitives/Banners";
import { getIngestAlias } from "@/lib/api/reads";
import { loadMe, loadVersion, timeZoneOf } from "@/lib/session";
import { formatInstant, formatInstantOrRaw } from "@/lib/format";

/**
 * S13 -- settings.
 *
 * The design divides this screen into identity, preferences, record policy and data. Three
 * of the four are rendered from endpoints that exist. The fourth is not, and says so.
 *
 * Identity is `GET /v1/me` plus `GET /v1/ingest-alias`. The design also shows a passkey
 * list and an active-session list with per-device timestamps. No endpoint in section 8
 * returns either, so those regions render an explicit absence naming what is missing. A
 * device list is exactly the kind of region where invented rows are most dangerous: a
 * reader who sees a session they do not recognise acts on it, and a reader who sees only
 * sessions they recognise stops looking.
 *
 * Record policy is rendered as what it is: invariants of the system, not rows and not
 * settings. They carry no control because there is nothing to set. Presenting an invariant
 * as a switch that happens to be locked would imply a configuration surface that does not
 * exist.
 */

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const [me, version, alias] = await Promise.all([loadMe(), loadVersion(), getIngestAlias()]);
  const timeZone = timeZoneOf(me);

  if (!me.ok) {
    return (
      <ErrorState
        heading="We could not read your account."
        detail={`GET ${me.path} returned ${me.status} ${me.code}`}
        traceId={me.traceId}
      />
    );
  }

  const account = me.data;
  const flags = Object.entries(account.feature_flags);

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Settings</h1>
        <p className="pv-mono">
          {account.display_name} · {account.home_region}
        </p>
      </header>

      <div
        className="pv-grid"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(24rem, 1fr))" }}
      >
        <div className="pv-stack">
          <section className="pv-card pv-card-pad" aria-labelledby="pv-identity-heading">
            <h2 className="pv-label" id="pv-identity-heading">
              Identity
            </h2>
            <dl style={{ marginTop: "var(--pv-space-3)" }}>
              <div className="pv-ledger-line">
                <dt className="pv-label">Name</dt>
                <dd>{account.display_name}</dd>
              </div>
              <div className="pv-ledger-line">
                <dt className="pv-label">Email</dt>
                <dd className="pv-mono">{account.email}</dd>
              </div>
              <div className="pv-ledger-line">
                <dt className="pv-label">Time zone</dt>
                <dd className="pv-mono">{account.timezone}</dd>
              </div>
              <div className="pv-ledger-line">
                <dt className="pv-label">Home region</dt>
                <dd className="pv-mono">{account.home_region}</dd>
              </div>
              <div className="pv-ledger-line">
                <dt className="pv-label">In record since</dt>
                <dd className="pv-mono">
                  {formatInstant(account.created_at, timeZone) ?? (
                    <Absent describe="account creation time not returned" />
                  )}
                </dd>
              </div>
            </dl>
            <p className="pv-label" style={{ marginTop: "var(--pv-space-3)" }}>
              Provenance holds no password for this account. Authentication is delegated, and no
              credential ever reaches this application.
            </p>
          </section>

          <section className="pv-card pv-card-pad" aria-labelledby="pv-devices-heading">
            <h2 className="pv-label" id="pv-devices-heading">
              Passkeys and active sessions
            </h2>
            <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
              <Absent
                reason="NO_ROW"
                describe="no endpoint in this API returns registered authenticators or active sessions"
              />{" "}
              Neither list is available to read, so neither is shown. A list of devices assembled
              from anything other than the authoritative session store would be actively harmful: it
              invites a reader to conclude that a session they cannot see does not exist.
            </p>
          </section>

          <section className="pv-card pv-card-pad" aria-labelledby="pv-inbound-heading">
            <h2 className="pv-label" id="pv-inbound-heading">
              Inbound address
            </h2>
            {alias.ok ? (
              <>
                <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
                  {alias.data.alias_display ?? (
                    <Absent describe="this deployment stores only the address hash; rotate to reveal a new one" />
                  )}
                </p>
                <p className="pv-mono">
                  status={alias.data.status} · artifacts_received={alias.data.artifacts_received}
                </p>
                <p className="pv-mono">
                  last received{" "}
                  {alias.data.last_received_at === null ? (
                    <Absent describe="nothing has arrived at this address" />
                  ) : (
                    formatInstantOrRaw(alias.data.last_received_at, timeZone)
                  )}
                </p>
              </>
            ) : (
              <p className="pv-mono">
                <Absent
                  reason="NO_ROW"
                  describe={`GET ${alias.path} returned ${alias.status} ${alias.code}`}
                />
              </p>
            )}
          </section>
        </div>

        <div className="pv-stack">
          <section className="pv-card pv-card-pad" aria-labelledby="pv-preferences-heading">
            <h2 className="pv-label" id="pv-preferences-heading">
              Preferences
            </h2>
            <p className="pv-label" style={{ marginTop: "var(--pv-space-3)" }}>
              These are local to this browser. Neither is written anywhere, because neither changes
              what the record says.
            </p>
            <div className="pv-ledger-line" style={{ marginTop: "var(--pv-space-3)" }}>
              <span>Disclosure</span>
              <DisclosureSwitch />
            </div>
            <div className="pv-ledger-line">
              <span>Surface</span>
              <SurfaceSwitch />
            </div>
            <div className="pv-ledger-line">
              <span>Times shown in</span>
              <span className="pv-mono">{account.timezone}</span>
            </div>
            <div className="pv-ledger-line">
              <span>Amounts shown</span>
              <span className="pv-mono">as recorded, per currency</span>
            </div>
          </section>

          <section className="pv-card pv-card-pad" aria-labelledby="pv-policy-heading">
            <h2 className="pv-label" id="pv-policy-heading">
              Record policy
            </h2>
            <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
              Invariants of the system, not settings. There is nothing here to change, which is why
              nothing here offers a control.
            </p>
            <div style={{ marginTop: "var(--pv-space-3)" }}>
              <div className="pv-ledger-line">
                <span>
                  <span style={{ display: "block" }}>Evidence is append-only</span>
                  <span className="pv-label">
                    Nothing you or a counterparty said is ever removed.
                  </span>
                </span>
                <span className="pv-chip" style={{ color: "var(--pv-kernel)" }}>
                  ENFORCED
                </span>
              </div>
              <div className="pv-ledger-line">
                <span>
                  <span style={{ display: "block" }}>Model output requires your approval</span>
                  <span className="pv-label">No draft leaves this system unapproved.</span>
                </span>
                <span className="pv-chip" style={{ color: "var(--pv-kernel)" }}>
                  ENFORCED
                </span>
              </div>
              <div className="pv-ledger-line">
                <span>
                  <span style={{ display: "block" }}>Retracted sources are excluded</span>
                  <span className="pv-label">
                    Excluded from evaluation, retained and visible in history. Each state proof
                    reports whether the filter was applied to it.
                  </span>
                </span>
                <span className="pv-chip">PER PROOF</span>
              </div>
            </div>
          </section>

          <section className="pv-card pv-card-pad" aria-labelledby="pv-flags-heading">
            <h2 className="pv-label" id="pv-flags-heading">
              What this deployment has enabled
            </h2>
            <ul className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
              <li>judge_mode_enabled={String(account.judge_mode_enabled)}</li>
              <li>ingest_alias_status={account.ingest_alias_status}</li>
              {flags.length === 0 ? (
                <li>
                  <Absent describe="no feature flags were returned; an absent flag is false" />
                </li>
              ) : (
                flags.map(([key, value]) => (
                  <li key={key} data-feature-flag={key}>
                    {key}={String(value)}
                  </li>
                ))
              )}
            </ul>
            <p style={{ marginTop: "var(--pv-space-3)" }}>
              <BuildStamp version={version.ok ? version.data : null} />
            </p>
          </section>

          <section className="pv-card pv-card-pad" aria-labelledby="pv-data-heading">
            <h2 className="pv-label" id="pv-data-heading">
              Your data
            </h2>
            <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
              Closing an account deletes the bytes and the indexes. Because the record is
              append-only, deletion is the only way anything leaves it, and it cannot be undone.
            </p>
            <p className="pv-label" style={{ marginTop: "var(--pv-space-3)" }}>
              No control is offered. Closing an account and building an audit package are both
              mutations, and this build performs none.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
