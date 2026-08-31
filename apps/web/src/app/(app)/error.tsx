"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";

/**
 * The error boundary for every screen inside the shell.
 *
 * The incident this exists for is `D-12-003`. Four declarations in `lib/api/contract.ts`
 * disagreed with the shapes the API actually sends, and nine live routes threw while
 * rendering -- including every case docket. What a reader saw was the framework's own
 * fallback: "Application error: a server-side exception has occurred", alone on the page,
 * with no route, no navigation, and no way back. The record was intact the entire time.
 * That screen gave a reader no way to tell.
 *
 * Read failures do not arrive here, and the distinction is the whole reason this file can
 * say something specific. `lib/api/client.ts` returns failures as values, so a page that
 * cannot reach an endpoint renders an `ErrorState` naming that endpoint and this boundary
 * never runs. What lands here is the other kind of failure: this application's own
 * rendering code meeting something it did not expect -- a payload shape it mis-declared, a
 * field it assumed was present. The copy therefore says that the screen failed rather than
 * apologising for an unspecified problem, because the two claims lead a reader to different
 * next actions.
 *
 * `error.digest` is the one detail worth printing. Next.js scrubs the message in production
 * and leaves a digest that matches a line in the server log, so it is the only handle a
 * reader can carry to somebody who can read that log. Where there is no digest, the absence
 * is marked rather than papered over with an empty string.
 */
export default function AppError({
  error,
  reset,
}: {
  readonly error: Error & { digest?: string };
  readonly reset: () => void;
}) {
  const pathname = usePathname();

  /*
   * The browser console is the only place the untruncated error survives on the client.
   * Next.js hands it to this component and to nothing else, so failing to log it here
   * would lose the stack a developer needs while leaving the digest pointing at a server
   * log line they may not be able to read.
   */
  useEffect(() => {
    console.error("Provenance: a screen failed to render", error);
  }, [error]);

  return (
    <ErrorState heading="This screen did not render." detail={`route ${pathname}`}>
      <p>
        Something in this application threw while building the page. This is a defect in Provenance,
        not a statement about your record: nothing was written, nothing was sent, and the underlying
        rows are unchanged. Every other screen is still reachable from the navigation.
      </p>
      <p style={{ marginTop: "var(--pv-space-3)" }}>
        Rendering it again runs exactly the same code against exactly the same response, so it will
        usually fail the same way. It is offered because a transient failure does exist, not because
        retrying is likely to work.
      </p>
      <p className="pv-mono" style={{ marginTop: "var(--pv-space-3)" }}>
        digest={error.digest ?? <Absent describe="this failure carries no digest" />}
      </p>
      <p style={{ marginTop: "var(--pv-space-4)", display: "flex", gap: "var(--pv-space-3)" }}>
        <button type="button" className="pv-button" onClick={reset}>
          Render this screen again
        </button>
        <Link className="pv-button" data-emphasis="primary" href="/dashboard">
          Back to the dashboard
        </Link>
      </p>
    </ErrorState>
  );
}
