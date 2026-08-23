"use client";

import { useCallback, useState } from "react";
import type { ActionIntentResponse, GroundedSentence } from "@/lib/api/contract";
import { abbreviateHash } from "@/lib/format";

/**
 * The draft, sentence by sentence.
 *
 * Every factual sentence renders with its support reference attached, so a reader can
 * click a sentence and see the evidence and belief version it rests on. A sentence with
 * no support is not hidden and is not quietly dropped: it renders marked as the user's
 * own words, because the difference between "the record says this" and "you are saying
 * this" is the difference between a grounded claim and an assertion.
 *
 * The approval controls are deliberately unglamorous. Approve is not pre-selected, both
 * consent checkboxes start unchecked, and rejecting is a plain, equally available control.
 * The approval binds to the case revision the draft was prepared against and to the draft
 * hash, both of which are shown.
 */

function SentenceRow({
  claim,
  index,
  expanded,
  onToggle,
}: {
  readonly claim: GroundedSentence;
  readonly index: number;
  readonly expanded: boolean;
  readonly onToggle: (index: number) => void;
}) {
  const grounded = claim.support_ids.length > 0;
  return (
    <li data-grounded={String(grounded)} data-validated={String(claim.validated)}>
      <p className="pv-prose">
        {claim.sentence_or_span}{" "}
        {grounded ? (
          <button
            type="button"
            className="pv-idchip"
            aria-expanded={expanded}
            onClick={() => onToggle(index)}
            aria-label={`Show what sentence ${index + 1} rests on`}
          >
            {index + 1}
          </button>
        ) : (
          <span className="pv-label" data-ungrounded="true">
            your own words
          </span>
        )}
      </p>
      {expanded && grounded ? (
        <ul className="pv-inset pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
          {claim.support_ids.map((id, position) => (
            <li key={id}>
              {claim.support_kinds[position] ?? "SUPPORT"}={id}
            </li>
          ))}
          <li>validated={String(claim.validated)}</li>
        </ul>
      ) : null}
    </li>
  );
}

export function GroundedDraft({ intent }: { readonly intent: ActionIntentResponse }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [readConfirmed, setReadConfirmed] = useState(false);
  const [authorised, setAuthorised] = useState(false);

  const toggle = useCallback((index: number) => {
    setExpanded((prior) => (prior === index ? null : index));
  }, []);

  const ungrounded = intent.draft.claims.filter((claim) => claim.support_ids.length === 0).length;
  const canApprove = readConfirmed && authorised && intent.status === "NEEDS_REVIEW";

  return (
    <div className="pv-stack">
      <div className="pv-card">
        <div className="pv-card-pad pv-boundary">
          <span className="pv-boundary-model">Model drafted this wording</span>
          <span className="pv-boundary-kernel">Kernel binds it on your approval</span>
        </div>

        <div className="pv-card-pad">
          <p className="pv-mono">subject: {intent.draft.subject}</p>
          <ol style={{ marginTop: "var(--pv-space-4)" }} className="pv-stack-tight">
            {intent.draft.claims.map((claim, index) => (
              <SentenceRow
                claim={claim}
                index={index}
                key={claim.sentence_or_span}
                expanded={expanded === index}
                onToggle={toggle}
              />
            ))}
          </ol>
          <p className="pv-label" style={{ marginTop: "var(--pv-space-4)" }}>
            {intent.draft.claims.length - ungrounded} grounded · {ungrounded} attributed to you
          </p>
        </div>
      </div>

      <div className="pv-card pv-card-pad">
        <p className="pv-label">Draft fingerprint</p>
        <p className="pv-mono">{abbreviateHash(intent.draft_sha256, 16, 8)}</p>
        <p className="pv-mono" style={{ wordBreak: "break-all", color: "var(--pv-ink-faint)" }}>
          {intent.draft_sha256}
        </p>
        <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
          Approval binds this hash and case revision {intent.basis_case_revision}. Editing the draft
          recomputes the hash and invalidates any prior approval.
        </p>
      </div>

      {intent.draft.unresolved_risks.length > 0 ? (
        <div className="pv-card pv-card-pad">
          <p className="pv-label">Unresolved risks the model flagged</p>
          <ul className="pv-prose">
            {intent.draft.unresolved_risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="pv-card pv-card-pad">
        <p className="pv-label">What approving does</p>
        <div className="pv-grid pv-grid-4" style={{ marginTop: "var(--pv-space-3)" }}>
          <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
            Sends one message to one recipient. No other party is contacted.
          </p>
          <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
            Records ACTION_APPROVED and ACTION_EXECUTED against revision{" "}
            {intent.basis_case_revision}.
          </p>
          <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
            Does not change the canonical belief. The dispute stays open until it is answered.
          </p>
        </div>
      </div>

      <form
        className="pv-card pv-card-pad"
        aria-labelledby="pv-consent-heading"
        onSubmit={(event) => event.preventDefault()}
      >
        <p className="pv-label" id="pv-consent-heading">
          Consent
        </p>
        <p style={{ marginTop: "var(--pv-space-3)" }}>
          <label>
            <input
              type="checkbox"
              checked={readConfirmed}
              onChange={(event) => setReadConfirmed(event.target.checked)}
            />{" "}
            I have read the draft and the evidence each sentence rests on.
          </label>
        </p>
        <p>
          <label>
            <input
              type="checkbox"
              checked={authorised}
              onChange={(event) => setAuthorised(event.target.checked)}
            />{" "}
            I authorise sending this, as written, at revision {intent.basis_case_revision}.
          </label>
        </p>

        <div
          style={{
            display: "flex",
            gap: "var(--pv-space-3)",
            marginTop: "var(--pv-space-4)",
            flexWrap: "wrap",
          }}
        >
          <button
            type="button"
            className="pv-button"
            data-emphasis="primary"
            disabled={!canApprove}
            data-action="approve"
          >
            Approve and send
          </button>
          <button type="button" className="pv-button" data-action="reject">
            Reject this draft
          </button>
        </div>

        <p className="pv-label" style={{ marginTop: "var(--pv-space-3)" }}>
          Both boxes required · no pre-checked consent
        </p>
        <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
          These controls are inert in this build. Approving calls POST /v1/action-intents/{"{id}"}
          /approve, a Phase 8 mutation that does not exist yet, so pressing them would do nothing
          while appearing to do something.
        </p>
      </form>
    </div>
  );
}
