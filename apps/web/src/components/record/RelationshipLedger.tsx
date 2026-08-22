import Link from "next/link";
import type { Money, RelationshipSummary } from "@/lib/api/contract";
import { formatMoney } from "@/lib/format";
import { AttentionChip } from "@/components/primitives/Chips";
import { Absent } from "@/components/primitives/Absent";

/**
 * The relationship ledger.
 *
 * `outstanding` is an array of per-currency amounts because the Kernel refuses arithmetic
 * across currencies, and so does this. An empty array is rendered as "nothing
 * outstanding" -- a true statement -- rather than as `USD 0.00`, which would assert a
 * currency the row does not name.
 *
 * The Northline row is the one that matters: its balance is disputed, and a disputed
 * balance changes `status`, never `amount`. It appears in the ledger contributing
 * nothing. Summing the counterparty's claimed figure into a total would contradict the
 * Kernel on the landing screen.
 */

function Outstanding({ amounts }: { readonly amounts: readonly Money[] }) {
  if (amounts.length === 0) {
    return (
      <span className="pv-mono" data-outstanding="none">
        nothing outstanding
      </span>
    );
  }
  return (
    <span className="pv-mono">
      {amounts.map((money) => (
        <span key={money.currency} style={{ display: "block" }}>
          {formatMoney(money)}
        </span>
      ))}
    </span>
  );
}

export function RelationshipCard({ item }: { readonly item: RelationshipSummary }) {
  return (
    <li className="pv-card pv-card-pad" data-relationship-id={item.relationship_id}>
      <p className="pv-title">{item.counterparty.display_name}</p>
      <p className="pv-label" style={{ marginTop: "var(--pv-space-1)" }}>
        {item.counterparty.kind} · {item.relationship_type}
      </p>
      <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
        {item.label}
      </p>

      <div className="pv-ledger-line" data-total="true" style={{ marginTop: "var(--pv-space-3)" }}>
        <span className="pv-label">Outstanding</span>
        <Outstanding amounts={item.outstanding} />
      </div>

      <div className="pv-meta-row">
        <span className="pv-chip" data-status={item.status}>
          {item.status}
        </span>
        <AttentionChip level={item.attention_level} />
        <span className="pv-mono">
          {item.open_case_count} open {item.open_case_count === 1 ? "case" : "cases"}
        </span>
      </div>

      <p style={{ marginTop: "var(--pv-space-3)" }}>
        <Link className="pv-button" href={`/relationships/${item.relationship_id}`}>
          Open file
        </Link>
      </p>
    </li>
  );
}

export function ContextTotal({
  amounts,
  contributors,
}: {
  readonly amounts: readonly Money[];
  readonly contributors: readonly RelationshipSummary[];
}) {
  const contributing = contributors.filter((r) => r.outstanding.length > 0);
  const silent = contributors.filter((r) => r.outstanding.length === 0);

  return (
    <div>
      <p className="pv-label">Total outstanding</p>
      {amounts.length === 0 ? (
        <p className="pv-figure">
          <Absent describe="no outstanding total returned for this context" />
        </p>
      ) : (
        amounts.map((money) => (
          <p className="pv-figure" key={money.currency} data-context-total={money.currency}>
            {formatMoney(money)}
          </p>
        ))
      )}

      {/* `label`, not `counterparty.display_name`. The corpus holds two live
          relationships with the same counterparty -- an ISP account at the old
          address and another at the new one -- so display names rendered two
          visually identical rows carrying different balances, with nothing on
          screen to tell a reader which was which. The label is what carries the
          distinguishing part ("- 88 Larkin" against "- 214 Ridgeway Apt 3B"). */}
      <div style={{ marginTop: "var(--pv-space-3)" }}>
        {contributing.map((r) => (
          <div className="pv-ledger-line" key={r.relationship_id}>
            <span>{r.label}</span>
            <span>{r.outstanding.map(formatMoney).join(" · ")}</span>
          </div>
        ))}
        {silent.map((r) => (
          <div className="pv-ledger-line" key={r.relationship_id} style={{ opacity: 0.75 }}>
            <span>{r.label}</span>
            <span>contributes nothing</span>
          </div>
        ))}
      </div>

      {/* The breakdown lists EVERY relationship the reader has, while the
          heading above it counts only the ones inside this context -- 6 rows
          under "4 relationships in scope". Both numbers are correct and they
          answer different questions, so the scope is stated rather than left
          for a reader to reconcile. Silently trimming the list to four would
          hide two counterparties who owe nothing *yet*, which is exactly the
          kind of quiet omission this record exists to prevent. */}
      <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
        {contributors.length} {contributors.length === 1 ? "relationship" : "relationships"} on
        file, all counterparties · sum returned by the API, not computed here
      </p>
    </div>
  );
}
