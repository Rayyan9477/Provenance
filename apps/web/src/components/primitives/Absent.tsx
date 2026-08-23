import type { AbsenceReason, RowRef } from "@/lib/typed/record";
import { refToAttribute } from "@/lib/typed/record";

/**
 * The only component permitted to render the absence glyph.
 *
 * `scripts/check-render-honesty.mjs` rule R4 enforces that. The reason is narrow and
 * important: if an em dash can appear anywhere, a reader cannot tell "the system does not
 * have this" from "the designer wanted a dash there". Concentrating the glyph in one
 * component gives it exactly one meaning, and gives that meaning an accessible name.
 *
 * What this component must never do is render a zero. A zero is a value. `USD 0.00` on a
 * disputed balance is a true statement about the record; `USD 0.00` standing in for a
 * number we failed to fetch is a lie, and the two are indistinguishable once printed.
 */

const REASON_TEXT: Record<AbsenceReason, string> = {
  NO_ROW: "not recorded: no row",
  NULL_COLUMN: "not recorded",
};

export interface AbsentProps {
  readonly reason?: AbsenceReason;
  readonly ref_?: RowRef;
  /** Overrides the accessible description when a surface has a more precise phrasing. */
  readonly describe?: string;
}

export function Absent({ reason = "NULL_COLUMN", ref_, describe }: AbsentProps) {
  const description = describe ?? REASON_TEXT[reason];
  return (
    <span
      className="pv-absent"
      data-absent="true"
      data-absence-reason={reason}
      {...(ref_ ? { "data-row-ref": refToAttribute(ref_) } : {})}
      title={description}
    >
      <span aria-hidden="true">—</span>
      <span className="pv-sr-only">{description}</span>
    </span>
  );
}
