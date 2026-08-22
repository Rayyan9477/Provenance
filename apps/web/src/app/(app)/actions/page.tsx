import Link from "next/link";
import { EmptyState, ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { getActionIntents } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { formatInstant } from "@/lib/format";

/**
 * S09a -- the approvals index.
 *
 * The list endpoint carries a masked recipient, a subject preview, and a warning count.
 * It does not carry the draft, the per-sentence grounding, the draft hash, or the
 * supporting belief versions, so nothing here offers an approval control. Approving from
 * a list would mean consenting to a message whose evidence the reader has not been shown,
 * which is precisely the transaction the approval binding exists to prevent.
 *
 * `is_stale` is computed server-side as `basis_case_revision <> current_case_revision`.
 * A stale draft is marked before the reader opens it, because discovering staleness after
 * pressing approve teaches distrust of a mechanism that is working correctly.
 */

export const dynamic = "force-dynamic";

export default async function ActionsIndexPage() {
  const [me, intents] = await Promise.all([loadMe(), getActionIntents()]);
  const timeZone = timeZoneOf(me);

  if (!intents.ok) {
    return (
      <ErrorState
        heading="We could not read your pending drafts."
        detail={`GET ${intents.path} returned ${intents.status} ${intents.code}`}
        traceId={intents.traceId}
      />
    );
  }

  const items = intents.data.items;
  const stale = items.filter((item) => item.is_stale).length;

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Approvals</h1>
        <p className="pv-label">
          {items.length} draft{items.length === 1 ? "" : "s"} · {stale} bound to a revision that has
          moved
        </p>
      </header>

      <p className="pv-prose">
        Nothing here has been sent. A draft leaves this system only after you have read it and both
        consent boxes are checked, and only against the case revision it was prepared for.
      </p>

      {items.length === 0 ? (
        <EmptyState heading="No draft is waiting on you.">
          <p>
            Drafts are proposed by the agent when a case needs an outbound message. None has been.
          </p>
        </EmptyState>
      ) : (
        <ul className="pv-stack-tight">
          {items.map((item) => (
            <li
              className="pv-attention-row"
              key={item.action_intent_id}
              data-action-intent-id={item.action_intent_id}
              data-stale={String(item.is_stale)}
            >
              <div
                className="pv-attention-body"
                data-attention={item.is_stale ? "ATTENTION" : "INFO"}
              >
                <p className="pv-title">{item.subject_preview}</p>
                <div className="pv-meta-row">
                  <span className="pv-mono">{item.action_type}</span>
                  <span className="pv-chip" data-status={item.status}>
                    {item.status}
                  </span>
                  <span className="pv-mono" data-recipient="masked">
                    to {item.recipient_masked}
                  </span>
                  <span className="pv-mono">{item.counterparty_display_name}</span>
                </div>
                <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
                  prepared at revision {item.basis_case_revision} · case is now at revision{" "}
                  {item.current_case_revision} · warnings {item.warning_count}
                </p>
                {item.is_stale ? (
                  <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                    The record moved after this was prepared. Approval binds to the revision it was
                    prepared against, so this draft cannot send as it stands. That is the binding
                    working.
                  </p>
                ) : null}
              </div>

              <div style={{ display: "flex", gap: "var(--pv-space-4)", alignItems: "flex-start" }}>
                <span>
                  <span className="pv-label">Record time</span>
                  <span className="pv-mono" style={{ display: "block" }}>
                    {formatInstant(item.created_at, timeZone) ?? (
                      <Absent describe="creation time not returned" />
                    )}
                  </span>
                </span>
                <Link className="pv-button" href={`/actions/${item.action_intent_id}`}>
                  Review the draft
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="pv-label">
        This list offers no approval control. The draft and the evidence each sentence rests on are
        on the draft&rsquo;s own screen, and consent is only meaningful there.
      </p>
    </div>
  );
}
