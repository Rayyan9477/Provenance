import type { VersionResponse } from "@/lib/api/contract";
import { Absent } from "./Absent";

/**
 * Two disclosures. They mean different things and must never be confused.
 */

/**
 * G12.7. Rendered when `GET /v1/me` returns `feature_flags.fixture_mode: true`.
 *
 * The copy is fixed by `frontend/30_UX_SPEC.md` section 10.7 and asserted verbatim by the
 * gate. It says the *model outputs* are replayed; the Kernel, database, transactions, and
 * event path are still live, which is what makes fixture mode a legitimate emergency
 * fallback rather than a fake.
 *
 * Non-dismissible by construction: no close control, no `aria-hidden`, no prop that hides
 * it, no CSS state in which it disappears. The component takes one boolean and either
 * renders the banner or renders nothing. There is nothing to suppress.
 */
export function FixtureModeBanner({ fixtureMode }: { readonly fixtureMode: boolean }) {
  if (!fixtureMode) return null;
  return (
    <div className="pv-banner pv-banner-disclosure" role="status" data-fixture-banner="true">
      <span>DEMO FIXTURE MODE — model outputs are replayed</span>
      <span className="pv-banner-note">
        The Kernel, database, transactions, and event path are live.
      </span>
    </div>
  );
}

/**
 * A different disclosure, with a deliberately different wording.
 *
 * This one says no Provenance API is connected at all and the screen is drawing the
 * design fixture. It exists because Phase 8 has not been built, and it must not be
 * mistakable for the product's own fixture-mode disclosure above -- one describes a
 * running system with replayed model calls, the other describes no running system.
 *
 * Also non-dismissible. If the numbers on screen did not come from a database, the
 * reader is entitled to know that at all times.
 */
export function FixtureDataBanner({ active }: { readonly active: boolean }) {
  if (!active) return null;
  return (
    <div className="pv-banner pv-banner-disclosure" role="status" data-fixture-data-banner="true">
      <span>DESIGN FIXTURE DATA · no Provenance API is connected</span>
      <span className="pv-banner-note">
        Every value on screen is read from src/fixtures, typed against the API contract. Nothing
        here has been computed by the Kernel.
      </span>
    </div>
  );
}

/**
 * The build stamp.
 *
 * Every field comes from `GET /v1/version`, the single authoritative disclosure channel.
 * When the version read fails the stamp says "unknown" rather than guessing, because a
 * confident wrong commit sha is worse than an admitted gap.
 */
export function BuildStamp({ version }: { readonly version: VersionResponse | null }) {
  if (version === null) {
    return (
      <span className="pv-mono" data-build-stamp="unknown">
        build unknown · version read failed
      </span>
    );
  }
  return (
    <span className="pv-mono" data-build-stamp={version.git_sha}>
      git_sha={version.git_sha} schema=
      {version.schema_revision === null || version.schema_revision === "" ? (
        /* An unset SCHEMA_REVISION rendered as a bare "schema=", which reads as
           a field that broke rather than one nobody supplied. This is the same
           rule the rest of the record follows: absence is marked, never
           implied by a gap. */
        <Absent describe="SCHEMA_REVISION is not set on this deployment" />
      ) : (
        version.schema_revision
      )}{" "}
      agent_mode={version.agent_mode} otlp={version.otlp_export} db_ok={String(version.db_ok)}
    </span>
  );
}
