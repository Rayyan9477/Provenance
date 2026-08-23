"use client";

import { useCallback, useState } from "react";
import type { AttentionLevel, RetractionStatus, SupportRelation } from "@/lib/api/contract";
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
