import { ErrorState } from "@/components/primitives/States";
import { CaseIndexList, CoverageNote } from "@/components/record/CaseIndexList";
import { collectCases } from "@/lib/cases";
import { loadMe, timeZoneOf } from "@/lib/session";

/**
 * S04a -- the State Proof index.
 *
 * A State Proof is always the proof *of a case* at a revision, so this screen has no
 * content of its own. Its only job is to let a reader who arrived from the navigation
 * choose a case, and its only honest source of case ids is the API.
 */

export const dynamic = "force-dynamic";

export default async function StateProofIndexPage() {
  const [me, index] = await Promise.all([loadMe(), collectCases()]);
  const timeZone = timeZoneOf(me);

  if (!index.dashboardRead && index.relationshipsRead === 0) {
    return (
      <ErrorState heading="We could not read any case to prove.">
        <p>
          Neither the dashboard nor any relationship file answered, so this screen has no case ids.
          An empty list here would say you have no cases, which is a claim it cannot make.
        </p>
      </ErrorState>
    );
  }

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">State proofs</h1>
        <p className="pv-label">
          {index.cases.length} case{index.cases.length === 1 ? "" : "s"} reachable
        </p>
      </header>

      <p className="pv-prose">
        A state proof is computed by database query at the revision you open it at. No language
        model is involved in producing one, and each proof says so with the field it rests on rather
        than as a promise.
      </p>

      <CoverageNote index={index} />

      <CaseIndexList
        index={index}
        timeZone={timeZone}
        hrefFor={(caseId) => `/cases/${caseId}/proof`}
        actionLabel="Open the proof"
        emptyHeading="No case is on your record yet."
        emptyBody="A proof exists only where a case does, and none does."
      />
    </div>
  );
}
