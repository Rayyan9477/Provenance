"use client";

import { useEffect } from "react";
import { ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";

/**
 * The sign-in segment's own boundary.
 *
 * Without this file a throw in `(auth)/login/page.tsx` fell through to
 * `app/error.tsx`, which told the reader "the frame around every screen -- the
 * navigation, the identity block, the clocks -- threw while it was being
 * built". On this route that is false: the sign-in screen renders no shell,
 * has no navigation and no identity block, and there is nothing above it to
 * fail. A boundary that misreports which part of the system broke is the
 * project's own failure mode committed by the component that exists to handle
 * failure, so the segment gets a boundary that knows where it is.
 *
 * The copy stays narrow for the same reason the shell boundary's does: it names
 * what this screen was doing and nothing else. It cannot say a session was or
 * was not established, because a throw during render tells us nothing about
 * that either way -- so it says what is certain, which is that this screen did
 * not finish rendering.
 */
export default function AuthError({
  error,
  reset,
}: {
  readonly error: Error & { digest?: string };
  readonly reset: () => void;
}) {
  useEffect(() => {
    console.error("Provenance: the sign-in screen failed to render", error);
  }, [error]);

  return (
    <main className="pv-auth" id="pv-main">
      <div className="pv-card">
        <div className="pv-card-pad">
          <p className="pv-wordmark">Provenance</p>
        </div>
        <div
          className="pv-card-pad"
          style={{ borderTop: "var(--pv-rule) solid var(--pv-line-hairline)" }}
        >
          <ErrorState heading="The sign-in screen did not render.">
            <p>
              This screen threw while it was being built. It is a defect in Provenance, not a
              refusal: nothing here judges whether you may sign in, and no credential was read,
              because this application never sees one.
            </p>
            <p className="pv-mono" style={{ marginTop: "var(--pv-space-3)" }}>
              digest={error.digest ?? <Absent describe="this failure carries no digest" />}
            </p>
            <p
              style={{ marginTop: "var(--pv-space-4)", display: "flex", gap: "var(--pv-space-3)" }}
            >
              <button type="button" className="pv-button" onClick={reset}>
                Try building it again
              </button>
              <a className="pv-button" data-emphasis="primary" href="/dashboard">
                Go to the dashboard
              </a>
            </p>
          </ErrorState>
        </div>
      </div>
    </main>
  );
}
