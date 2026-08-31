"use client";

import { useCallback, useState } from "react";
import type {
  AttentionLevel,
  EpistemicStatus,
  RetractionStatus,
  SupportRelation,
} from "@/lib/api/contract";
import { EPISTEMIC_STATUSES } from "@/lib/api/contract";
import { Absent } from "./Absent";

/**
 * Chips.
 *
 * Every variant here is driven by a closed vocabulary from `specs/11_CONTRACTS.md`. An
 * unrecognised value is rendered verbatim with a warning treatment rather than mapped to
 * a default -- silently coercing an unknown enum to "NONE" would hide a contract drift
 * exactly where it matters.
 */

const ATTENTION_GLYPH: Record<AttentionLevel, string> = {
  NONE: "·",
  INFO: "i",
  ATTENTION: "!",
  URGENT: "!!",
};

export function AttentionChip({ level }: { readonly level: AttentionLevel | null | undefined }) {
  if (level === null || level === undefined) {
    return <Absent describe="attention level not returned" />;
  }
  const glyph = ATTENTION_GLYPH[level];
  return (
    <span className="pv-chip" data-attention={level}>
      {glyph ? (
        <span className="pv-chip-glyph" aria-hidden="true">
          {glyph}
        </span>
      ) : null}
      {level}
    </span>
  );
}

/** Case status straight from `cases.status`. Never re-worded, never abbreviated. */
export function CaseStatusBadge({ status }: { readonly status: string | null | undefined }) {
  if (!status) return <Absent describe="case status not returned" />;
  return (
    <span className="pv-chip" data-status={status}>
      {status}
    </span>
  );
}

export function RevisionBadge({ revision }: { readonly revision: number | null | undefined }) {
  if (revision === null || revision === undefined) {
    return <Absent describe="case revision not returned" />;
  }
  /*
   * G12.4 reads this from the DOM. It is a first-class, visible value with a stable
   * hook -- a revision behind a tooltip cannot be asserted, and the mutation probe is
   * the test that distinguishes a live UI from a screenshot.
   */
  return (
    <span className="pv-chip" data-case-revision={revision}>
      revision {revision}
    </span>
  );
}

const RETRACTION_COPY: Record<RetractionStatus, string> = {
  ACTIVE: "ACTIVE",
  RETRACTED: "RETRACTED · excluded from retrieval, retained in the record",
  SUPERSEDED: "SUPERSEDED · excluded from retrieval, retained in the record",
  QUARANTINED: "QUARANTINED · held pending review",
};

/**
 * Shown wherever retracted or superseded evidence is deliberately displayed.
 *
 * Active evidence carries no badge: a badge on everything is a badge on nothing.
 */
export function RetractionBadge({ status }: { readonly status: RetractionStatus }) {
  if (status === "ACTIVE") return null;
  return (
    <span className="pv-chip" data-retraction={status} data-attention="ATTENTION">
      {RETRACTION_COPY[status]}
    </span>
  );
}

/**
 * How strongly Provenance holds a belief version.
 *
 * Every belief on the State Proof used to render this status in the URGENT treatment,
 * CONFIRMED included: a settled position and an open contradiction wore the same alarm,
 * in the same weight, in the same red. A docket where every row shouts has lost the
 * ability to say which row is the one a reader has to act on, and the hero case turns on
 * exactly that distinction -- one belief is DISPUTED and the others are not.
 *
 * So the six statuses map onto the attention vocabulary that already exists. DISPUTED is
 * the only URGENT one, because it is the only one that means two records disagree and a
 * human has to choose between them. RETRACTED asks for attention: the position was
 * withdrawn and the row is shown because withdrawal is part of the record. PROBABLE and
 * UNCERTAIN are informational -- they are honest hedges, not faults, and treating a
 * declared uncertainty as an alarm would teach readers to distrust the hedge rather than
 * read it. CONFIRMED and SUPERSEDED stay quiet: one is settled, the other is history.
 *
 * The mapping is a presentation decision about emphasis and nothing else. It never
 * rewords the status, which is always printed verbatim, so the record's own vocabulary is
 * what the reader sees.
 *
 * A departure, named. `frontend/30_UX_SPEC.md` section 1114 requires that when
 * `epistemic_status` is DISPUTED, UNCERTAIN or RETRACTED the status be "the visual and
 * reading-order primary". DISPUTED and RETRACTED are treated that way here. UNCERTAIN is
 * not: it is INFO, the second-quietest treatment available. The argument is the one above
 * -- a declared hedge is the system being honest about its own confidence, and rendering
 * honesty as alarm teaches readers to distrust the hedge -- but it is an argument against
 * a spec sentence, so the sentence is named rather than quietly outvoted. If the spec is
 * right, the fix is one line in the record below.
 */
const EPISTEMIC_ATTENTION: Record<EpistemicStatus, AttentionLevel> = {
  CONFIRMED: "NONE",
  PROBABLE: "INFO",
  UNCERTAIN: "INFO",
  DISPUTED: "URGENT",
  SUPERSEDED: "NONE",
  RETRACTED: "ATTENTION",
};

function isKnownStatus(status: string): status is EpistemicStatus {
  return (EPISTEMIC_STATUSES as readonly string[]).includes(status);
}

export function EpistemicStatusChip({ status }: { readonly status: string | null | undefined }) {
  if (status === null || status === undefined || status === "") {
    return <Absent describe="epistemic status not returned" />;
  }
  /*
   * An unrecognised status keeps its own name and takes the ATTENTION treatment, per the
   * rule at the top of this file. Mapping it to NONE would render a contract drift as a
   * settled belief, which is the one outcome worse than rendering it as itself.
   */
  const known = isKnownStatus(status);
  return (
    <span
      className="pv-chip"
      data-epistemic-status={status}
      data-attention={known ? EPISTEMIC_ATTENTION[status] : "ATTENTION"}
      {...(known ? {} : { "data-unrecognised": "true" })}
    >
      {status}
    </span>
  );
}

const RELATION_GLYPH: Record<SupportRelation, string> = {
  SUPPORTS: "+",
  CONTRADICTS: "×",
  QUALIFIES: "~",
};

/**
 * Grounding relation.
 *
 * Hue, glyph, and rule style all carry the distinction, so SUPPORTS and CONTRADICTS stay
 * separable with colour removed. `grayscale.test.tsx` asserts the non-colour channels.
 */
export function RelationLabel({ relation }: { readonly relation: SupportRelation }) {
  return (
    <span className="pv-label" data-relation={relation}>
      <span className="pv-relation-glyph" aria-hidden="true">
        {RELATION_GLYPH[relation]}{" "}
      </span>
      {relation}
    </span>
  );
}

/**
 * The correlation primitive: a judge carries an id between panels by clicking it.
 *
 * The id is always shown in full on demand, never permanently truncated, because a
 * truncated id cannot be matched against a database row.
 */
export function IdChip({
  value,
  label,
  onSelect,
  selected = false,
}: {
  readonly value: string | null | undefined;
  readonly label?: string;
  readonly onSelect?: (value: string) => void;
  readonly selected?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const toggle = useCallback(() => {
    setExpanded((prior) => !prior);
    if (value !== null && value !== undefined && onSelect) onSelect(value);
  }, [onSelect, value]);

  if (value === null || value === undefined || value === "") {
    return <Absent describe={label ? `${label} not returned` : "identifier not returned"} />;
  }

  const shown = expanded || value.length <= 12 ? value : `${value.slice(0, 8)}…`;
  return (
    <button
      type="button"
      className="pv-idchip"
      aria-pressed={selected}
      aria-label={`${label ?? "identifier"} ${value}`}
      title={value}
      onClick={toggle}
      data-id-value={value}
    >
      {shown}
    </button>
  );
}
