import { SkeletonBlock } from "@/components/primitives/States";

/**
 * The loading state for every screen inside the shell.
 *
 * Each page here is a server component that awaits the control plane before it can render
 * anything, and until this file existed there was no boundary to fill that gap: a
 * navigation left the previous screen on display with nothing to say the record was being
 * re-read, and a cold load showed an empty document. Both of those are the failure
 * `components/primitives/States.tsx` was written against -- a screen that shows nothing
 * when it means "we are still asking".
 *
 * The block is deliberately shapeless. It is three bars, not a ghost of the table that is
 * coming, because a skeleton shaped like a five-row docket has promised five rows before a
 * single row has been read. It renders no figure, no id and no count, so there is nothing
 * on it that could be mistaken for a value that arrived.
 *
 * What this boundary does not cover is the shell's own reads. `(app)/layout.tsx` awaits
 * `GET /v1/me`, `/v1/version` and the dashboard above this Suspense boundary, so a cold
 * load still waits on those three before anything below appears. Closing that gap needs a
 * loading state at the root, and the root also serves the sign-in screen, where this copy
 * would be wrong. It is named here rather than left for a reader to discover.
 */
export default function AppLoading() {
  return (
    <div className="pv-stack">
      <p className="pv-label">Reading the record</p>
      <SkeletonBlock lines={3} label="Reading the record" />
    </div>
  );
}
