import Link from "next/link";
import { ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { MoneyValue } from "@/components/primitives/MoneyValue";
import { AttentionChip, CaseStatusBadge, RevisionBadge } from "@/components/primitives/Chips";
import { TimePair } from "@/components/primitives/TimePair";
import { CanonicalPosition, CounterpartyAssertions } from "@/components/record/CounterpartyFile";
import { getRelationship, getStateProof, getTimeline } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { daysBetween, formatDateOrRaw, formatInstant, formatMoney } from "@/lib/format";
import type { StateProofResponse, TimelineEntry } from "@/lib/api/contract";

/**
 * S03b -- the counterparty file.
 *
 * The design puts four things on this screen: the ledger line, what the counterparty told
 * you, the canonical position, and a contact log. Only the first arrives from
 * `GET /v1/relationships/{id}`. The other three are State Proof and timeline content, so
 * this page reads those endpoints too, for the cases the relationship names, and says on
 * screen which endpoint produced which region.
 *
 * That is the honest version of the design's composition. The dishonest version would
 * have been to render the design's panels from the relationship payload alone and fill
 * the gaps with plausible text; the panels would have looked identical and been fiction.
 *
 * The elapsed figure is computed from two instants that both came from the record: the
 * commitment's `due_at` and the proof's `generated_at`. Never from a constant, and never
 * from the browser's clock, which in a seeded demo is not the system's clock at all.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly params: Promise<{ relationshipId: string }>;
}

export default async function RelationshipFilePage({ params }: PageProps) {
  const { relationshipId } = await params;
  const [me, relationship] = await Promise.all([loadMe(), getRelationship(relationshipId)]);
  const timeZone = timeZoneOf(me);

  if (!relationship.ok) {
    return (
      <ErrorState
        heading="We could not read this relationship."
        detail={`GET ${relationship.path} returned ${relationship.status} ${relationship.code}`}
        traceId={relationship.traceId}
      />
    );
  }

  const data = relationship.data;

  /* One State Proof and one timeline per case the relationship names. Both are read here
     rather than guessed at, and a case whose proof cannot be read is reported as such
     instead of being dropped from the page. */
  const perCase = await Promise.all(
    data.cases.map(async (entry) => {
      const [proof, timeline] = await Promise.all([
        getStateProof(entry.case_id),
        getTimeline(entry.case_id),
      ]);
      return { caseRef: entry, proof, timeline };
    }),
  );

  const proofs: readonly StateProofResponse[] = perCase
    .map((r) => (r.proof.ok ? r.proof.data : null))
    .filter((p): p is StateProofResponse => p !== null);
  const unreadableProofs = perCase.filter((r) => !r.proof.ok);
  const firstProof = proofs[0];

  const commitments = proofs.flatMap((p) => p.commitments);
  const contactLog: readonly TimelineEntry[] = perCase
    .flatMap((r) => (r.timeline.ok ? r.timeline.data.items : []))
    .slice(0, 12);

  return (
    <div className="pv-stack">
      <section className="pv-card" aria-labelledby="pv-counterparty-heading">
        <div className="pv-card-pad pv-section-heading">
          <div>
            <p className="pv-label">Counterparty file</p>
            <h1 className="pv-display" id="pv-counterparty-heading">
              {data.counterparty.display_name}
            </h1>
            <p className="pv-meta-row pv-mono">
              <span>{data.counterparty.kind}</span>
              <span>·</span>
              <span data-account-ref="masked">
                {data.external_account_ref_masked ?? (
                  <Absent describe="no external account reference is held" />
                )}
              </span>
              <span>·</span>
              <span>
                {data.valid_from === null ? (
                  <Absent describe="no start of validity recorded" />
                ) : (
                  `in record since ${formatDateOrRaw(data.valid_from, timeZone)}`
                )}
              </span>
              <span>·</span>
              <span>revision {data.revision}</span>
            </p>
          </div>
          <p className="pv-meta-row">
            <span className="pv-chip" data-status={data.status}>
              {data.status}
            </span>
            <Link className="pv-button" href="/watches">
              Watches
            </Link>
          </p>
        </div>

        <div
          className="pv-card-pad pv-grid pv-grid-4"
          style={{ borderTop: "var(--pv-rule) solid var(--pv-line-hairline)" }}
        >
          <div>
            <p className="pv-label">Outstanding, per currency</p>
            {data.summary.outstanding.length === 0 ? (
              <p className="pv-mono" data-outstanding="none">
                nothing outstanding
              </p>
            ) : (
              data.summary.outstanding.map((money) => (
                <p className="pv-figure" key={money.currency}>
                  {formatMoney(money)}
                </p>
              ))
            )}
          </div>
          <div>
            <p className="pv-label">Cases</p>
            <p className="pv-mono">
              {data.summary.open_cases} open of {data.summary.total_cases}
            </p>
            {/*
              The conflict count is gone rather than rendered empty. This read
              `data.summary.active_conflicts`, which the API has never sent, so
              the line came out as " active conflicts" with nothing in front of
              it on every relationship file. A count with no number is not a
              smaller truth than a count -- it is a different claim, and this
              screen's rule is that nothing renders without a backing row. The
              conflicts a case carries are on the case page, which reads them
              from an endpoint that returns them.
            */}
          </div>
          <div>
            <p className="pv-label">Outstanding</p>
            {/*
              This was `{data.summary.unresolved_commitments}` in a `.pv-figure`
              -- the largest type on the card -- and the API sends no such field,
              so the headline slot rendered empty. `outstanding` is what the API
              does send here, it is the figure the dashboard shows for this same
              relationship, and it is the number a reader came to see.
            */}
            {data.summary.outstanding.length === 0 ? (
              <p className="pv-figure">
                <Absent describe="no outstanding amount is recorded for this relationship" />
              </p>
            ) : (
              data.summary.outstanding.map((money) => (
                <p className="pv-figure" key={`${money.currency}-${money.amount}`}>
                  {formatMoney(money)}
                </p>
              ))
            )}
          </div>
          <div>
            <p className="pv-label">Evidence span</p>
            <TimePair
              timeZone={timeZone}
              validFrom={data.valid_from}
              validTo={data.valid_to}
              recordedAt={null}
              recordVerb="LAST EVIDENCE"
            />
          </div>
        </div>
      </section>

      <section aria-labelledby="pv-rel-ledger-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-rel-ledger-heading">
            Committed less fulfilled equals outstanding
          </h2>
          <p className="pv-label">Read from the state proof, not from this file</p>
        </div>

        {commitments.length === 0 ? (
          <p className="pv-prose">
            No commitment is recorded against this counterparty.{" "}
            {unreadableProofs.length > 0
              ? `${unreadableProofs.length} state proof could not be read, so this may be incomplete. It is said here rather than left to read as a clean zero.`
              : null}
          </p>
        ) : (
          <ul className="pv-stack-tight">
            {commitments.map((commitment) => {
              const elapsed =
                commitment.due_at === null || firstProof === undefined
                  ? null
                  : daysBetween(commitment.due_at, firstProof.generated_at);
              return (
                <li
                  className="pv-card pv-card-pad"
                  key={commitment.commitment_id}
                  data-commitment-id={commitment.commitment_id}
                >
                  <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                    {commitment.description}
                  </p>
                  <div className="pv-ledger-line">
                    <span className="pv-label">Committed</span>
                    <MoneyValue amount={commitment.committed_amount} />
                  </div>
                  <div className="pv-ledger-line">
                    <span className="pv-label">Fulfilled</span>
                    <MoneyValue amount={commitment.fulfilled_amount} />
                  </div>
                  <div className="pv-ledger-line" data-total="true">
                    <span className="pv-label">Outstanding</span>
                    <MoneyValue amount={commitment.outstanding_amount} />
                  </div>
                  <div className="pv-meta-row">
                    <span className="pv-chip">{commitment.status}</span>
                    <span className="pv-mono">
                      promised by{" "}
                      {commitment.due_at === null ? (
                        <Absent describe="no promised date recorded on this commitment" />
                      ) : (
                        formatDateOrRaw(commitment.due_at, timeZone)
                      )}
                    </span>
                    <span
                      className="pv-mono"
                      data-elapsed-days={elapsed === null ? undefined : elapsed}
                      style={
                        elapsed !== null && elapsed > 0
                          ? { color: "var(--pv-conflict)" }
                          : undefined
                      }
                    >
                      {elapsed === null ? (
                        <Absent describe="elapsed time needs both a promised date and a proof stamp" />
                      ) : elapsed > 0 ? (
                        `${elapsed} days past promised date`
                      ) : (
                        `${Math.abs(elapsed)} days remaining`
                      )}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <div
        className="pv-grid"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(22rem, 1fr))" }}
      >
        <CounterpartyAssertions proofs={proofs} timeZone={timeZone} />
        <CanonicalPosition proofs={proofs} timeZone={timeZone} />
      </div>

      <section aria-labelledby="pv-contact-log-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-contact-log-heading">
            Contact log
          </h2>
          <p className="pv-label">Read from the case timelines</p>
        </div>
        {contactLog.length === 0 ? (
          <p className="pv-prose">
            <Absent describe="no timeline entry was returned for the cases on this file" />
          </p>
        ) : (
          <ul className="pv-stack-tight">
            {contactLog.map((entry) => (
              <li className="pv-ledger-line" key={entry.id} data-timeline-kind={entry.kind}>
                <span className="pv-mono">
                  {formatInstant(entry.occurred_at, timeZone) ?? (
                    <Absent describe="occurrence time not returned" />
                  )}
                </span>
                <span className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                  {entry.headline}
                </span>
                <span className="pv-chip" data-actor={entry.actor.type}>
                  {entry.actor.type}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="pv-rel-cases-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-rel-cases-heading">
            Cases on this file
          </h2>
        </div>
        <ul className="pv-stack-tight">
          {data.cases.map((entry) => (
            <li className="pv-card pv-card-pad" key={entry.case_id} data-case-id={entry.case_id}>
              <div className="pv-section-heading">
                <p className="pv-title">{entry.title}</p>
                <p className="pv-meta-row">
                  <CaseStatusBadge status={entry.status} />
                  <RevisionBadge revision={entry.revision} />
                  <AttentionChip level={entry.attention_level} />
                </p>
              </div>
              <p className="pv-mono">
                {entry.case_type} · reopened {entry.reopened_count} time
                {entry.reopened_count === 1 ? "" : "s"} · opened{" "}
                {formatDateOrRaw(entry.opened_at, timeZone)}
              </p>
              <p className="pv-meta-row" style={{ marginTop: "var(--pv-space-3)" }}>
                <Link className="pv-button" href={`/cases/${entry.case_id}`}>
                  Open case
                </Link>
                <Link className="pv-button" href={`/cases/${entry.case_id}/proof`}>
                  State proof
                </Link>
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
