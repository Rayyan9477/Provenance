"use client";

import Link from "next/link";
import type { CaseAttentionItem } from "@/lib/api/contract";
import { field, row } from "@/lib/typed/record";
import type { TypedRecord } from "@/lib/typed/record";
import { AttentionChip, CaseStatusBadge, RevisionBadge } from "@/components/primitives/Chips";
import { TimePair } from "@/components/primitives/TimePair";
import { TypedRecordBlock } from "@/components/primitives/TypedRecordBlock";
import { useDisclosure } from "@/components/primitives/Disclosure";

/**
 * One row of the attention list.
 *
 * Two things here are worth reading closely.
 *
 * The typed record is built only from columns `GET /v1/dashboard` actually returns. The
 * returned design shows richer tokens on this row -- `belief=balance_owed v2`,
 * `conflict=X-31`, `claim=C-512 source_authority=0.5500` -- but section 8.4 of the API
 * spec returns none of those; they live on the State Proof. Rendering them here would
 * mean either a second round of requests the design does not describe or, far worse,
 * constants. They are therefore not rendered, and the discrepancy is reported rather
 * than papered over.
 *
 * Valid time is the same story in miniature. The design commits to VALID TIME and RECORD
 * TIME on every attention row. `cases_attention[]` carries `last_activity_at`, which is a
 * record-clock value, and no valid-time field at all. So record time renders and valid
 * time renders as an explicit absence naming the missing field. A plausible date here
 * would be the exact failure this whole application exists to make impossible.
 */
export function AttentionRow({
  item,
  timeZone,
}: {
  readonly item: CaseAttentionItem;
  readonly timeZone: string;
}) {
  const { mode } = useDisclosure();
  const source = row("cases", item, item.case_id);

  const typed: TypedRecord = [
    field(source, "status"),
    field(source, "revision"),
    field(source, "attention_level"),
    field(source, "counterparty_display_name", { key: "counterparty" }),
    field(source, "attention_reason_codes", {
      key: "reason_codes",
      format: (codes) => codes.join(","),
    }),
    field(source, "case_id"),
  ];

  return (
    <li className="pv-attention-row" data-case-id={item.case_id}>
      <div className="pv-attention-body" data-attention={item.attention_level}>
        {mode === "PROSE" ? (
          <p className="pv-title">{item.headline}</p>
        ) : (
          <p className="pv-mono">headline={item.headline}</p>
        )}

        <div className="pv-meta-row">
          <span>{item.counterparty_display_name}</span>
          <CaseStatusBadge status={item.status} />
          <RevisionBadge revision={item.revision} />
          <AttentionChip level={item.attention_level} />
        </div>

        <div style={{ marginTop: "var(--pv-space-3)" }}>
          <TypedRecordBlock record={typed} label="Case row, as stored" />
        </div>
      </div>

      <div style={{ display: "flex", gap: "var(--pv-space-4)", alignItems: "flex-start" }}>
        <TimePair
          timeZone={timeZone}
          recordedAt={item.last_activity_at}
          recordVerb="LAST ACTIVITY"
        />
        <Link className="pv-button" href={`/cases/${item.case_id}`}>
          Open case
        </Link>
      </div>
    </li>
  );
}
