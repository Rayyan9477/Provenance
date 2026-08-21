"use client";

import type { TimelineEntry } from "@/lib/api/contract";
import { field, nested, row } from "@/lib/typed/record";
import type { TypedRecord } from "@/lib/typed/record";
import { formatInstant } from "@/lib/format";
import { TypedRecordBlock } from "@/components/primitives/TypedRecordBlock";
import { useDisclosure } from "@/components/primitives/Disclosure";
import { Absent } from "@/components/primitives/Absent";

/**
 * The merged docket.
 *
 * Append-only: nothing is ever removed, and the list says so. Each entry is attributed to
 * the actor that produced it -- KERNEL, AGENT, COUNTERPARTY, USER, SCHEDULER, EXECUTOR --
 * because "the system decided" and "a counterparty asserted" are the two facts a reader
 * most needs kept apart.
 *
 * In TYPED RECORD mode the entry renders its own columns plus the `detail` keys the API
 * returned for that kind. `nested()` names the path, so a token reads
 * `detail.reason_code` and still carries a row reference.
 */

/** Which `detail` keys to surface per kind, per section 8.10's table. */
const DETAIL_KEYS: Readonly<Record<string, readonly string[]>> = {
  ARTIFACT_RECEIVED: ["artifact_id", "source_type", "mime_type", "parser_status"],
  EVIDENCE_ADMITTED: ["artifact_id", "evidence_type_counts"],
  CLAIM_RECORDED: ["claim_id", "claim_kind", "predicate", "actor_type"],
  BELIEF_CHANGED: [
    "belief_id",
    "predicate",
    "from_version_no",
    "to_version_no",
    "epistemic_status",
  ],
  CONFLICT_OPENED: ["conflict_id", "conflict_type", "severity", "status"],
  CONFLICT_RESOLVED: ["conflict_id", "status", "resolution_reason_code"],
  COMMITMENT_CREATED: ["commitment_id", "status", "committed_amount", "outstanding_amount"],
  COMMITMENT_UPDATED: ["commitment_id", "status", "outstanding_amount"],
  FULFILLMENT_ADMITTED: ["fulfillment_id", "commitment_id", "admission_status"],
  STATE_TRANSITION: ["from_state", "to_state", "reason_code", "kernel_decision_id"],
  TRIGGER_FIRED: ["trigger_id", "trigger_type", "state", "last_result"],
  TRIGGER_ARMED: ["trigger_id", "trigger_type", "state"],
  TRIGGER_NOOP: ["trigger_id", "state", "last_result"],
  ACTION_PROPOSED: ["action_intent_id", "action_type", "status", "recipient_masked"],
  ACTION_APPROVED: ["action_intent_id", "status"],
  ACTION_EXECUTED: ["action_intent_id", "status", "provider_correlation_id"],
  ACTION_FAILED: ["action_intent_id", "status", "error_code"],
  USER_CORRECTION: ["evidence_id", "correction_type"],
};

function ActorTag({ entry }: { readonly entry: TimelineEntry }) {
  const binding = entry.actor.type === "KERNEL" || entry.actor.type === "EXECUTOR";
  return (
    <span
      className="pv-chip"
      data-actor={entry.actor.type}
      style={{
        color: binding
          ? "var(--pv-kernel)"
          : entry.actor.type === "AGENT"
            ? "var(--pv-model)"
            : undefined,
      }}
    >
      {entry.actor.type}
    </span>
  );
}

export function TimelineList({
  entries,
  timeZone,
  hasMore,
}: {
  readonly entries: readonly TimelineEntry[];
  readonly timeZone: string;
  readonly hasMore: boolean;
}) {
  const { mode } = useDisclosure();

  if (entries.length === 0) {
    return (
      <p className="pv-prose">
        No docket entry matched. <Absent describe="no timeline entries returned" />
      </p>
    );
  }

  return (
    <>
      <ul>
        {entries.map((entry) => {
          const source = row("timeline", entry, entry.id);
          const keys = DETAIL_KEYS[entry.kind] ?? [];
          const typed: TypedRecord = [
            field(source, "kind"),
            field(source, "case_revision"),
            ...keys.map((key) => nested(source, "detail", key, { key: `detail.${key}` })),
          ];

          return (
            <li
              className="pv-attention-row"
              key={entry.id}
              data-timeline-kind={entry.kind}
              data-case-revision={entry.case_revision}
            >
              <div style={{ flex: "0 0 13rem" }}>
                <p className="pv-mono">
                  {formatInstant(entry.occurred_at, timeZone) ?? (
                    <Absent describe="occurrence time not returned" />
                  )}
                </p>
                <p className="pv-label">{entry.kind.replace(/_/g, " ")}</p>
              </div>

              <div
                className="pv-attention-body"
                data-attention={entry.actor.type === "COUNTERPARTY" ? "ATTENTION" : "INFO"}
                style={{ flex: "1 1 24rem" }}
              >
                {mode === "PROSE" ? (
                  <p className="pv-prose">{entry.headline}</p>
                ) : (
                  <p className="pv-mono">headline={entry.headline}</p>
                )}
                <div className="pv-meta-row">
                  <ActorTag entry={entry} />
                  <span className="pv-mono">{entry.actor.label}</span>
                </div>
                <div style={{ marginTop: "var(--pv-space-2)" }}>
                  <TypedRecordBlock record={typed} label={`${entry.kind} row, as stored`} />
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {hasMore ? (
        <p className="pv-label" style={{ marginTop: "var(--pv-space-4)" }}>
          More docket entries exist beyond this page. Append-only · nothing is ever removed.
        </p>
      ) : (
        <p className="pv-label" style={{ marginTop: "var(--pv-space-4)" }}>
          End of the docket. Append-only · nothing is ever removed.
        </p>
      )}
    </>
  );
}
