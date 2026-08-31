"use client";

import { useEffect } from "react";
import { ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";

/**
 * The root-segment boundary.
 *
 * Not the boundary of last resort: `global-error.tsx` is, and a throw inside
 * `src/app/layout.tsx` still reaches the framework's default page. That layout
 * renders only `<html>` and `<body>`, so the exposure is nil, but the
 * distinction is worth keeping straight in the file whose job is to be accurate
 * about failure.
 *
 * `(app)/error.tsx` catches a page that threw, and it renders inside the shell so the
 * reader keeps the navigation. It cannot catch the shell itself.
 *
 * The copy below does not name the shell as the cause, because this boundary
 * cannot know it was: it is the root segment, so it also catches anything a
 * sibling segment throws that the sibling did not catch first. It said "the
 * frame around every screen ... threw" and rendered that sentence over the
 * sign-in screen, which has no shell at all. `(auth)/error.tsx` now catches
 * that case where it happens, and this copy says what is true of every case it
 * can still see. `(app)/layout.tsx` reads
 * `GET /v1/me` and renders the identity block from the response, so a payload that omits a
 * field the layout indexes into throws above that boundary -- which is the same class of
 * failure as `D-12-003`, one level higher, and without this file it would land on the
 * framework's default "Application error" page exactly as the nine broken routes did.
 *
 * This screen therefore renders with no navigation and no status bar, because those are
 * the things that failed. It offers a plain link rather than a `next/link` for the same
 * reason: a full document load is the one route back that does not depend on the shell
 * having mounted.
 */
export default function RootError({
  error,
  reset,
}: {
  readonly error: Error & { digest?: string };
  readonly reset: () => void;
}) {
  useEffect(() => {
    console.error("Provenance: the application shell failed to render", error);
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
          <ErrorState heading="The application shell did not render.">
            <p>
              A screen threw while it was being built, above the boundary that would have kept the
              navigation on the page -- most often the shell itself: the frame that renders the
              navigation, the identity block and the clocks. This is a defect in Provenance. It says
              nothing about your record: no write was attempted and no action was sent.
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
                Reload the dashboard
              </a>
            </p>
          </ErrorState>
        </div>
      </div>
    </main>
  );
}
