import type { ReactNode } from "react";
import { Navigation } from "@/components/shell/Navigation";
import { BuildStamp, FixtureDataBanner, FixtureModeBanner } from "@/components/primitives/Banners";
import { DisclosureProvider, DisclosureSwitch } from "@/components/primitives/Disclosure";
import { Absent } from "@/components/primitives/Absent";
import { dataSource } from "@/lib/api/client";
import { formatInstant } from "@/lib/format";
import { loadDashboard, loadMe, loadVersion, timeZoneOf } from "@/lib/session";

/**
 * The app shell.
 *
 * A server component: the identity block, the banners, and the build stamp are all read
 * server-side with the human token, and none of them is held in a client store. Only the
 * disclosure switch is a client component, because it is the one piece of genuinely local
 * state on the screen.
 */
export default async function AppLayout({ children }: { readonly children: ReactNode }) {
  const [me, version, dashboard] = await Promise.all([loadMe(), loadVersion(), loadDashboard()]);

  const timeZone = timeZoneOf(me);
  const fixtureMode = me.ok ? me.data.feature_flags.fixture_mode === true : false;
  const judgeModeEnabled = me.ok ? me.data.judge_mode_enabled : false;
  const counterfactualEnabled = me.ok
    ? me.data.feature_flags.counterfactual_enabled === true
    : false;

  const serverClock = dashboard.ok ? formatInstant(dashboard.data.generated_at, "UTC") : null;
  const localClock =
    dashboard.ok && me.ok ? formatInstant(dashboard.data.generated_at, timeZone) : null;

  return (
    <DisclosureProvider>
      <div className="pv-shell">
        <a className="pv-skip-link" href="#pv-main">
          Skip to content
        </a>

        {/* Both disclosures are unconditional in structure: they take a boolean and
            render or do not. There is no control that hides either one. */}
        <FixtureDataBanner active={dataSource() === "FIXTURE"} />
        <FixtureModeBanner fixtureMode={fixtureMode} />

        <header className="pv-masthead">
          <p className="pv-wordmark">Provenance</p>
          <p className="pv-promise">
            A system of record for the institutions that already have one of you.
          </p>
        </header>

        <Navigation
          judgeModeEnabled={judgeModeEnabled}
          counterfactualEnabled={counterfactualEnabled}
        />

        <div className="pv-identity">
          {me.ok ? (
            <>
              <span className="pv-avatar" aria-hidden="true">
                {me.data.display_name
                  .split(/\s+/)
                  .map((part) => part[0] ?? "")
                  .join("")
                  .slice(0, 2)
                  .toUpperCase()}
              </span>
              <span>
                <span style={{ display: "block" }}>{me.data.display_name}</span>
                <span className="pv-mono">{me.data.timezone}</span>
              </span>
            </>
          ) : (
            <span>
              <span className="pv-label">Signed-in identity</span>{" "}
              <Absent describe="GET /v1/me could not be read" />
            </span>
          )}
        </div>

        <div className="pv-statusbar">
          <DisclosureSwitch />
          <span className="pv-statusbar-spacer" />
          <span>
            <span className="pv-label">System time</span>
            <span className="pv-mono" style={{ display: "block" }}>
              {serverClock ?? <Absent describe="server clock not read" />}
            </span>
            <span className="pv-mono" style={{ display: "block", color: "var(--pv-ink-faint)" }}>
              {localClock ?? (
                <Absent describe="local rendering of the server clock not available" />
              )}
            </span>
          </span>
          <BuildStamp version={version.ok ? version.data : null} />
        </div>

        <main className="pv-main" id="pv-main">
          {children}
        </main>
      </div>
    </DisclosureProvider>
  );
}
