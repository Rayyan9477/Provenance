import { ErrorState } from "@/components/primitives/States";
import { CaseIndexList, CoverageNote } from "@/components/record/CaseIndexList";
import { collectCases } from "@/lib/cases";
import { loadMe, timeZoneOf } from "@/lib/session";

/**
 * S02a -- the case index.
 *
 * `frontend/30_UX_SPEC.md` section 2.2 is firm that case detail is reached contextually and
 * never from the primary navigation, because it needs an id the reader must first have
 * chosen. That rule is honoured: the primary destinations carry `data-primary` -- four of them, or three when `judge_mode_enabled` is false, since Sign out was removed for doing nothing and
 * case detail is not among them.
 *
 * This screen exists because the design's screen index lists all fourteen surfaces, and a
 * navigation entry that leads to a 404 is its own small dishonesty. It is a chooser, not a
 * destination: no case content renders here, only the ids and the endpoint each was found
 * through.
 */

export const dynamic = "force-dynamic";

export default async function CaseIndexPage() {
  const [me, index] = await Promise.all([loadMe(), collectCases()]);
  const timeZone = timeZoneOf(me);

  if (!index.dashboardRead && index.relationshipsRead === 0) {
    return (
      <ErrorState heading="We could not read your cases.">
        <p>
          Neither the dashboard nor any relationship file answered. Nothing is listed below, because
          an empty list and an unreachable endpoint are different facts and only one of them is
          about your record.
        </p>
      </ErrorState>
    );
  }

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Cases</h1>
        <p className="pv-label">
          {index.cases.length} case{index.cases.length === 1 ? "" : "s"} reachable
        </p>
      </header>

      <p className="pv-prose">
        A case is one running matter with one counterparty. Open one for its merged docket: every
        artifact, claim, kernel decision and action, newest first, append-only.
      </p>

      <CoverageNote index={index} />

      <CaseIndexList
        index={index}
        timeZone={timeZone}
        hrefFor={(caseId) => `/cases/${caseId}`}
        actionLabel="Open case"
        emptyHeading="No case is on your record yet."
        emptyBody="A case opens when the first artifact naming a counterparty is admitted. None has been."
      />
    </div>
  );
}
