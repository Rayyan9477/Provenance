import Link from "next/link";
import { EmptyState, ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { AttentionChip } from "@/components/primitives/Chips";
import { TimePair } from "@/components/primitives/TimePair";
import { getRelationships } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";

/**
 * S03a -- the relationship index.
 *
 * `GET /v1/relationships` (section 8.6) returns neither `summary` nor `cases[]`, so this
 * screen renders no outstanding figure. That is a deliberate omission rather than an
 * oversight: the dashboard has the per-relationship money because `GET /v1/dashboard`
 * returns it, and this endpoint does not. Rendering a figure here would mean either an
 * undocumented extra request per row or a number carried over from another screen, and
 * the second of those is how a stale total ends up on a page that never fetched it.
 *
 * `external_account_ref_masked` is rendered as the API returns it. The full account
 * reference is never masked or unmasked client-side; section 8.6 masks it server-side and
 * surfaces the full value only inside State Proof evidence text, where the reader is
 * looking at their own source document.
 */

export const dynamic = "force-dynamic";

export default async function RelationshipsPage() {
  const [me, relationships] = await Promise.all([loadMe(), getRelationships()]);
  const timeZone = timeZoneOf(me);

  if (!relationships.ok) {
    return (
      <ErrorState
        heading="We could not read your relationships."
        detail={`GET ${relationships.path} returned ${relationships.status} ${relationships.code}`}
        traceId={relationships.traceId}
      />
    );
  }

  const items = relationships.data.items;

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Relationships</h1>
        <p className="pv-label">
          {items.length} counterpart{items.length === 1 ? "y" : "ies"} in the record
        </p>
      </header>

      <p className="pv-prose">
        One file per counterparty. Each holds what they told you, what the Kernel concluded, and
        every case that ran between you.
      </p>

      {items.length === 0 ? (
        <EmptyState heading="No counterparty is in your record yet.">
          <p>
            A relationship is created when the first artifact naming a counterparty is admitted.
            Nothing has been.
          </p>
        </EmptyState>
      ) : (
        <ul className="pv-stack-tight">
          {items.map((item) => (
            <li
              className="pv-attention-row"
              key={item.relationship_id}
              data-relationship-id={item.relationship_id}
            >
              <div className="pv-attention-body" data-attention={item.attention_level}>
                <p className="pv-title">{item.counterparty.display_name}</p>
                <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                  {item.label}
                </p>
                <div className="pv-meta-row">
                  <span className="pv-mono">
                    {item.counterparty.kind} · {item.relationship_type}
                  </span>
                  <span className="pv-chip" data-status={item.status}>
                    {item.status}
                  </span>
                  <AttentionChip level={item.attention_level} />
                  <span className="pv-mono" data-account-ref="masked">
                    {item.external_account_ref_masked ?? (
                      <Absent describe="no external account reference is held for this relationship" />
                    )}
                  </span>
                  <span className="pv-mono">
                    {item.open_case_count} open {item.open_case_count === 1 ? "case" : "cases"}
                  </span>
                </div>
                <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
                  This endpoint returns no money. Outstanding figures are on the dashboard and in
                  each case file, where the Kernel computes them.
                </p>
              </div>

              <div style={{ display: "flex", gap: "var(--pv-space-4)", alignItems: "flex-start" }}>
                <TimePair
                  timeZone={timeZone}
                  validFrom={item.valid_from}
                  validTo={item.valid_to}
                  recordedAt={item.last_activity_at}
                  recordVerb="LAST ACTIVITY"
                />
                <Link className="pv-button" href={`/relationships/${item.relationship_id}`}>
                  Open file
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
