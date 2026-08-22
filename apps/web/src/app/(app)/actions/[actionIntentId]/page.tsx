import Link from "next/link";
import { GroundedDraft } from "@/components/actions/GroundedDraft";
import { ErrorState, StaleState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { getActionIntent } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { formatInstant } from "@/lib/format";

/**
 * S09 -- action approval.
 *
 * The stale path is rendered as a safety mechanism working, not as an error, because that
 * is what it is: the case moved after the draft was prepared, and approval binds to the
 * revision it was prepared against. Copy that reads like a failure teaches the reader to
 * distrust the system at the exact moment it is protecting them.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly params: Promise<{ actionIntentId: string }>;
}

export default async function ActionApprovalPage({ params }: PageProps) {
  const { actionIntentId } = await params;
  const [me, result] = await Promise.all([loadMe(), getActionIntent(actionIntentId)]);
  const timeZone = timeZoneOf(me);

  if (!result.ok) {
    return (
      <ErrorState
        heading="We could not read this action."
        detail={`GET ${result.path} returned ${result.status} ${result.code}`}
        traceId={result.traceId}
      />
    );
  }

  const intent = result.data;

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <div>
          <h1 className="pv-display">Approve one outbound action</h1>
          <p className="pv-mono">
            {intent.action_type} · status={intent.status}
          </p>
        </div>
        <Link className="pv-button" href={`/cases/${intent.case_id}`}>
          Open the case
        </Link>
      </header>

      <dl className="pv-card pv-card-pad pv-grid pv-grid-4">
        <div>
          <dt className="pv-label">To</dt>
          <dd className="pv-mono">{intent.recipient}</dd>
          <dd className="pv-mono" style={{ color: "var(--pv-ink-faint)" }}>
            recipient_allowlisted={String(intent.recipient_allowlisted)}
          </dd>
        </div>
        <div>
          <dt className="pv-label">Prepared at</dt>
          <dd className="pv-mono">revision {intent.basis_case_revision}</dd>
          <dd className="pv-mono">
            {formatInstant(intent.created_at, timeZone) ?? (
              <Absent describe="creation time not returned" />
            )}
          </dd>
        </div>
        <div>
          <dt className="pv-label">Current case revision</dt>
          <dd className="pv-mono">{intent.current_case_revision}</dd>
        </div>
        <div>
          <dt className="pv-label">Requested outcome</dt>
          <dd className="pv-mono">{intent.draft.requested_outcome}</dd>
        </div>
      </dl>

      {intent.warnings.length > 0 ? (
        <div className="pv-card pv-card-pad pv-relation" data-relation="CONTRADICTS">
          <p className="pv-label">Warnings carried on this intent</p>
          <ul className="pv-mono">
            {intent.warnings.map((warning) => (
              <li key={warning.code}>
                {warning.code}: {warning.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {intent.is_stale ? (
        <StaleState
          basisRevision={intent.basis_case_revision}
          currentRevision={intent.current_case_revision}
        >
          <p style={{ marginTop: "var(--pv-space-3)", display: "flex", gap: "var(--pv-space-3)" }}>
            <Link className="pv-button" href={`/cases/${intent.case_id}`}>
              See what changed
            </Link>
            <Link className="pv-button" href={`/cases/${intent.case_id}/proof`}>
              Open the updated proof
            </Link>
          </p>
        </StaleState>
      ) : null}

      <p className="pv-prose">{intent.rationale}</p>

      <GroundedDraft intent={intent} />

      <section className="pv-card pv-card-pad" aria-labelledby="pv-supporting-heading">
        <h2 className="pv-label" id="pv-supporting-heading">
          Belief versions this draft rests on
        </h2>
        <ul className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
          {intent.supporting_belief_versions.map((version) => (
            <li key={version.belief_version_id} data-belief-version-id={version.belief_version_id}>
              {version.predicate} v{version.version_no} · still_current=
              {String(version.still_current)}
            </li>
          ))}
        </ul>
        <p style={{ marginTop: "var(--pv-space-3)" }}>
          <Link className="pv-button" href={`/cases/${intent.case_id}/proof`}>
            What it rests on
          </Link>
        </p>
      </section>
    </div>
  );
}
