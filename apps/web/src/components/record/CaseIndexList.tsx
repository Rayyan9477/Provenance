import Link from "next/link";
import { AttentionChip, CaseStatusBadge, RevisionBadge } from "@/components/primitives/Chips";
import { Absent } from "@/components/primitives/Absent";
import { EmptyState } from "@/components/primitives/States";
import { formatInstant } from "@/lib/format";
import type { CaseIndex } from "@/lib/cases";

/**
 * The shared body of the two case indexes: the case list and the State Proof list.
 *
 * They differ only in where the row's control goes, so they share everything else,
 * including the provenance note that says which endpoint each row was found through and
 * the coverage note that says what could not be read. Duplicating those two would have
 * been the easiest way for one of the screens to quietly stop reporting its gaps.
 */

export function CoverageNote({ index }: { readonly index: CaseIndex }) {
  return (
    <p className="pv-label">
      {index.dashboardRead
        ? "Assembled from the dashboard attention list and the cases named on each relationship file."
        : "The dashboard could not be read, so this list comes only from the relationship files and may be short."}
      {index.relationshipsUnreadable > 0
        ? ` ${index.relationshipsUnreadable} relationship file could not be read; cases held only there are missing.`
        : ""}
    </p>
  );
}

export function CaseIndexList({
  index,
  timeZone,
  hrefFor,
  actionLabel,
  emptyHeading,
  emptyBody,
}: {
  readonly index: CaseIndex;
  readonly timeZone: string;
  readonly hrefFor: (caseId: string) => string;
  readonly actionLabel: string;
  readonly emptyHeading: string;
  readonly emptyBody: string;
}) {
  if (index.cases.length === 0) {
    return (
      <EmptyState heading={emptyHeading}>
        <p>{emptyBody}</p>
      </EmptyState>
    );
  }

  return (
    <ul className="pv-stack-tight">
      {index.cases.map((item) => (
        <li className="pv-attention-row" key={item.caseId} data-case-id={item.caseId}>
          <div className="pv-attention-body" data-attention={item.attentionLevel}>
            <p className="pv-title">{item.title}</p>
            <div className="pv-meta-row">
              <span>
                {item.counterparty ?? <Absent describe="no counterparty named on this row" />}
              </span>
              <CaseStatusBadge status={item.status} />
              <RevisionBadge revision={item.revision} />
              <AttentionChip level={item.attentionLevel} />
            </div>
            <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
              found through {item.via}
            </p>
          </div>
          <div style={{ display: "flex", gap: "var(--pv-space-4)", alignItems: "flex-start" }}>
            <span>
              <span className="pv-label">Record time</span>
              <span className="pv-mono" style={{ display: "block" }}>
                {formatInstant(item.lastActivityAt, timeZone) ?? (
                  <Absent describe="last activity time not returned" />
                )}
              </span>
            </span>
            <Link className="pv-button" href={hrefFor(item.caseId)}>
              {actionLabel}
            </Link>
          </div>
        </li>
      ))}
    </ul>
  );
}
