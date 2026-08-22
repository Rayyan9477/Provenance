import Link from "next/link";
import { TimelineList } from "@/components/record/TimelineList";
import { ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { AttentionChip, CaseStatusBadge, RevisionBadge } from "@/components/primitives/Chips";
import { getCase, getTimeline } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { formatDate, formatDateOrRaw, formatInstantOrRaw, formatMoney } from "@/lib/format";
import type { CaseCommitment, Money } from "@/lib/api/contract";

/**
 * S02 -- case detail and the merged docket.
 *
 * The revision is a first-class, visible value carrying `data-case-revision`, because
 * G12.4's mutation probe reads it from the DOM: commit a real correction, reload, and the
 * number must move. A revision hidden behind a tooltip cannot be asserted, and a UI whose
 * revision does not move when the database does is a picture of a system rather than the
 * system.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly params: Promise<{ caseId: string }>;
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function CommitmentRow({ commitment }: { readonly commitment: CaseCommitment }) {
  // The endpoint sends each amount as a complete `Money` -- currency and value
  // together -- so it is passed through, not rebuilt. The previous version took
  // a bare decimal and re-paired it with `commitment.currency`, which wrapped an
  // object inside another object and threw `amount.split is not a function` on
  // every case detail route. Reading the currency off the amount rather than off
  // the commitment also means a row can never render a value under a currency
  // that belongs to a different field.
  const money = (amount: Money | null) => (amount === null ? null : formatMoney(amount));

  return (
    <li className="pv-card pv-card-pad" data-commitment-id={commitment.commitment_id}>
      <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
        {commitment.description}
      </p>
      <div className="pv-ledger-line">
        <span className="pv-label">Committed</span>
        <span>
          {money(commitment.committed_amount) ?? <Absent describe="no committed amount" />}
        </span>
      </div>
      <div className="pv-ledger-line">
        <span className="pv-label">Fulfilled</span>
        <span>
          {money(commitment.fulfilled_amount) ?? <Absent describe="no fulfilled amount" />}
        </span>
      </div>
      <div className="pv-ledger-line" data-total="true">
        <span className="pv-label">Outstanding</span>
        <span>
          {money(commitment.outstanding_amount) ?? <Absent describe="no outstanding amount" />}
        </span>
      </div>
      <div className="pv-meta-row">
        <span className="pv-chip">{commitment.status}</span>
        <span className="pv-mono">
          due{" "}
          {commitment.due_at === null ? (
            <Absent describe="no due date recorded" />
          ) : (
            formatDateOrRaw(commitment.due_at, "UTC")
          )}
        </span>
      </div>
    </li>
  );
}

export default async function CaseDetailPage({ params, searchParams }: PageProps) {
  const { caseId } = await params;
  const query = await searchParams;
  const kindParam = query["kind"];
  const kind =
    kindParam === undefined ? undefined : Array.isArray(kindParam) ? kindParam : [kindParam];
  const sinceRevision =
    typeof query["since_revision"] === "string"
      ? Number.parseInt(query["since_revision"], 10)
      : null;

  const [me, caseResult, timeline] = await Promise.all([
    loadMe(),
    getCase(caseId),
    getTimeline(caseId, { kind, sinceRevision }),
  ]);
  const timeZone = timeZoneOf(me);

  if (!caseResult.ok) {
    return (
      <ErrorState
        heading="We could not read this case."
        detail={`GET ${caseResult.path} returned ${caseResult.status} ${caseResult.code}`}
        traceId={caseResult.traceId}
      />
    );
  }

  const record = caseResult.data;

  return (
    <div className="pv-stack">
      <header className="pv-card pv-card-pad">
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--pv-space-4)",
            justifyContent: "space-between",
          }}
        >
          <div>
            <p className="pv-label">Case docket</p>
            <h1 className="pv-display">{record.title}</h1>
            {/* A case can sit outside any context, and several in the corpus do.
                The separator goes with the title rather than being stranded, and
                the absence is stated rather than rendered as an empty gap that
                reads like a failed load. */}
            <p className="pv-mono">
              {record.counterparty.display_name} · {record.case_type}
              {record.context === null ? (
                <>
                  {" · "}
                  <Absent describe="this case belongs to no context" />
                </>
              ) : (
                ` · ${record.context.title}`
              )}
            </p>
          </div>
          <div style={{ display: "flex", gap: "var(--pv-space-2)", alignItems: "flex-start" }}>
            <Link className="pv-button" href={`/cases/${caseId}/proof`}>
              View state proof
            </Link>
            <Link className="pv-button" href="/judge">
              How this changed
            </Link>
          </div>
        </div>

        <dl
          className="pv-grid pv-grid-5"
          style={{
            marginTop: "var(--pv-space-5)",
            borderTop: "1px solid var(--pv-line-hairline)",
            paddingTop: "var(--pv-space-4)",
          }}
        >
          <div>
            <dt className="pv-label">Status</dt>
            <dd>
              <CaseStatusBadge status={record.status} />
            </dd>
          </div>
          <div>
            <dt className="pv-label">Revision</dt>
            <dd>
              <RevisionBadge revision={record.revision} />
            </dd>
          </div>
          <div>
            <dt className="pv-label">Reopen count</dt>
            <dd className="pv-mono">{record.reopened_count}</dd>
          </div>
          <div>
            <dt className="pv-label">Opened</dt>
            <dd className="pv-mono">
              {formatDate(record.opened_at, timeZone) ?? (
                <Absent describe="open date not returned" />
              )}
            </dd>
          </div>
          <div>
            <dt className="pv-label">Attention</dt>
            <dd>
              <AttentionChip level={record.attention_level} />
            </dd>
          </div>
        </dl>

        <p className="pv-mono" style={{ marginTop: "var(--pv-space-3)" }}>
          attention_reason_codes={record.attention_reason_codes.join(",") || "none"} ·
          evidence_items={record.counts.evidence_items} · claims={record.counts.claims} · beliefs=
          {record.counts.beliefs} · state_transitions={record.counts.state_transitions}
        </p>
      </header>

      {record.active_conflicts.length > 0 ? (
        <section aria-labelledby="pv-case-conflicts">
          <div className="pv-section-heading">
            <h2 className="pv-label" id="pv-case-conflicts" style={{ color: "var(--pv-conflict)" }}>
              Open contradiction
            </h2>
          </div>
          <ul className="pv-stack-tight" style={{ marginTop: "var(--pv-space-3)" }}>
            {record.active_conflicts.map((conflict) => (
              <li
                className="pv-card pv-card-pad pv-relation"
                data-relation="CONTRADICTS"
                key={conflict.conflict_id}
                data-conflict-id={conflict.conflict_id}
              >
                <p className="pv-prose">{conflict.summary}</p>
                <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
                  conflict_type={conflict.conflict_type} predicate={conflict.predicate} status=
                  {conflict.status} severity={conflict.severity} requires_human=
                  {String(conflict.requires_human)} detected=
                  {formatInstantOrRaw(conflict.detected_at, timeZone)}
                </p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {record.commitments.length > 0 ? (
        <section aria-labelledby="pv-case-commitments">
          <div className="pv-section-heading">
            <h2 className="pv-label" id="pv-case-commitments">
              Commitments
            </h2>
          </div>
          <ul className="pv-grid pv-grid-4" style={{ marginTop: "var(--pv-space-4)" }}>
            {record.commitments.map((commitment) => (
              <CommitmentRow commitment={commitment} key={commitment.commitment_id} />
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-labelledby="pv-boundary-heading">
        <h2 className="pv-sr-only" id="pv-boundary-heading">
          What the model proposed and what the Kernel committed
        </h2>
        <p className="pv-boundary">
          <span className="pv-boundary-model">Model proposes · not binding</span>
          <span className="pv-boundary-kernel">Kernel commits · binding</span>
        </p>

        {record.latest_action_intent === null ? (
          <p className="pv-prose" style={{ marginTop: "var(--pv-space-3)" }}>
            Nothing has been drafted against this case.
          </p>
        ) : (
          <div className="pv-card pv-card-pad" style={{ marginTop: "var(--pv-space-3)" }}>
            <p className="pv-label" style={{ color: "var(--pv-model)" }}>
              ACTION_PROPOSED · {record.latest_action_intent.action_type}
            </p>
            <p className="pv-title" style={{ marginTop: "var(--pv-space-2)" }}>
              A draft is waiting for your approval.
            </p>
            <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
              Nothing has been sent. The model prepared the wording; only your approval binds it,
              and only at the revision it was prepared against.
            </p>
            <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
              status={record.latest_action_intent.status} basis_case_revision=
              {record.latest_action_intent.basis_case_revision}
            </p>
            <p style={{ marginTop: "var(--pv-space-3)" }}>
              <Link
                className="pv-button"
                data-emphasis="primary"
                href={`/actions/${record.latest_action_intent.action_intent_id}`}
              >
                Review and approve
              </Link>
            </p>
          </div>
        )}
      </section>

      <section aria-labelledby="pv-docket-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-docket-heading">
            Merged docket · newest first
          </h2>
          <p className="pv-label">Append-only · nothing is ever removed</p>
        </div>
        {timeline.ok ? (
          <TimelineList
            entries={timeline.data.items}
            timeZone={timeZone}
            hasMore={timeline.data.page.has_more}
          />
        ) : (
          <ErrorState
            heading="We could not read the docket."
            detail={`GET ${timeline.path} returned ${timeline.status} ${timeline.code}`}
            traceId={timeline.traceId}
          />
        )}
      </section>
    </div>
  );
}
