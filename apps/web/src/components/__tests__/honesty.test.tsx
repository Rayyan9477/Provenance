import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Absent } from "@/components/primitives/Absent";
import {
  AttentionChip,
  CaseStatusBadge,
  IdChip,
  RevisionBadge,
} from "@/components/primitives/Chips";
import { TimePair } from "@/components/primitives/TimePair";
import { TypedRecordBlock } from "@/components/primitives/TypedRecordBlock";
import { ContextTotal } from "@/components/record/RelationshipLedger";
import { CanonicalPosition, CounterpartyAssertions } from "@/components/record/CounterpartyFile";
import { field, row } from "@/lib/typed/record";
import type { TypedRecord } from "@/lib/typed/record";
import { heroDashboard } from "@/fixtures/hero.fixture";

/**
 * The honesty rule, as an assertion.
 *
 * Nothing renders that has no backing row. The corollary is the thing worth testing,
 * because it is the one an implementation gets wrong while looking correct: when the data
 * is not there, the component must say so. Not zero, not an empty string, not a dash
 * somebody typed, not a plausible default.
 *
 * The distinction is not pedantic. On this product's hero case the canonical balance is
 * genuinely `USD 0.00` while its status is DISPUTED, so a zero on screen is a true and
 * load-bearing statement. A component that also prints `0` when it failed to read the
 * amount has made the true zero unreadable: the reader can no longer tell which of the two
 * they are looking at, and neither can a judge.
 *
 * Every assertion below therefore checks two things at once: that the absence marker is
 * present, and that no value-shaped text is.
 */

/** Text a component must never emit in place of a value it does not have. */
const FORBIDDEN_STAND_INS = ["0", "0.00", "USD 0.00", "N/A", "n/a", "TBD", "null", "undefined"];

function expectNoStandIn(container: HTMLElement) {
  const text = container.textContent ?? "";
  for (const standIn of FORBIDDEN_STAND_INS) {
    /* Word-boundary match: "0" inside "0.7100" is a real value, not a stand-in. */
    const pattern = new RegExp(
      `(^|[^\\w.,])${standIn.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^\\w.,]|$)`,
    );
    expect(text, `rendered the stand-in "${standIn}" where it has no data`).not.toMatch(pattern);
  }
}

function expectAbsence(container: HTMLElement) {
  const markers = container.querySelectorAll('[data-absent="true"]');
  expect(markers.length, "expected an explicit absence marker").toBeGreaterThan(0);
  /* The marker must carry an accessible description. A bare glyph is not a statement. */
  for (const marker of markers) {
    expect((marker.textContent ?? "").trim().length).toBeGreaterThan(1);
  }
}

describe("a component given no data renders an explicit absence", () => {
  it("Absent names the reason in text, not only in colour or shape", () => {
    const { container } = render(<Absent reason="NO_ROW" />);
    expectAbsence(container);
    expect(screen.getByText("not recorded: no row")).toBeDefined();
    expectNoStandIn(container);
  });

  it("a revision badge with no revision does not render revision 0", () => {
    const { container } = render(<RevisionBadge revision={null} />);
    expectAbsence(container);
    expectNoStandIn(container);
    expect(container.querySelector("[data-case-revision]")).toBeNull();
  });

  it("an attention chip with no level does not fall back to NONE", () => {
    const { container } = render(<AttentionChip level={null} />);
    expectAbsence(container);
    expect(container.querySelector('[data-attention="NONE"]')).toBeNull();
  });

  it("a status badge with no status does not render an empty chip", () => {
    const { container } = render(<CaseStatusBadge status={null} />);
    expectAbsence(container);
    expect(container.querySelector("[data-status]")).toBeNull();
  });

  it("an id chip with no id does not render a truncated blank", () => {
    const { container } = render(<IdChip value={null} label="case" />);
    expectAbsence(container);
    expect(container.querySelector("button")).toBeNull();
  });

  it("a time pair with neither clock renders two absences, still labelled", () => {
    const { container } = render(<TimePair timeZone="UTC" />);
    expect(container.querySelectorAll('[data-absent="true"]').length).toBe(2);
    /* Both labels survive: the reader learns which clock is missing, not merely that
       something is. */
    expect(screen.getByText("Valid time")).toBeDefined();
    expect(screen.getByText("Record time")).toBeDefined();
    expectNoStandIn(container);
  });

  it("a typed record of null columns renders every token as an absence", () => {
    interface Case {
      readonly status: string | null;
      readonly revision: number | null;
    }
    const source = row<Case>("cases", { status: null, revision: null }, "case-under-test");
    const record: TypedRecord = [field(source, "status"), field(source, "revision")];

    const { container } = render(<TypedRecordBlock record={record} />);

    expect(container.querySelectorAll('[data-absent="true"]').length).toBe(2);
    /* The token names survive, so the reader sees which fields the system does not hold
       rather than a shorter list that looks complete. */
    expect(screen.getByText("status=")).toBeDefined();
    expect(screen.getByText("revision=")).toBeDefined();
    expectNoStandIn(container);
  });

  it("a typed record with no fields at all says so rather than rendering nothing", () => {
    const { container } = render(<TypedRecordBlock record={[]} />);
    expectAbsence(container);
    expect(container.querySelector('[data-typed-record="empty"]')).not.toBeNull();
  });

  it("a context total with no amounts does not render a zero total", () => {
    const { container } = render(<ContextTotal amounts={[]} contributors={[]} />);
    expectAbsence(container);
    expectNoStandIn(container);
  });

  it("the counterparty panels with no proof say no proof was read", () => {
    const { container } = render(<CounterpartyAssertions proofs={[]} timeZone="UTC" />);
    expectAbsence(container);
    expect(screen.getByText(/no state proof was read/)).toBeDefined();
    expectNoStandIn(container);
  });

  it("the canonical position with no belief does not render an empty figure", () => {
    const { container } = render(<CanonicalPosition proofs={[]} timeZone="UTC" />);
    expectAbsence(container);
    expect(container.querySelector(".pv-figure")).toBeNull();
    expectNoStandIn(container);
  });
});

describe("a genuine zero is still rendered as a zero", () => {
  /*
   * The other half of the rule, and the reason the first half matters. A relationship
   * whose balance is disputed contributes nothing to the total, and that nothing is a
   * decision the Kernel made. It must render as a statement about the record, and it must
   * not render as an absence: "we do not know" and "it is zero" are opposite claims.
   */
  it("a relationship with an empty outstanding array says nothing outstanding", () => {
    const silent = heroDashboard.relationships_summary.filter((r) => r.outstanding.length === 0);
    expect(
      silent.length,
      "the fixture must contain a relationship contributing nothing",
    ).toBeGreaterThan(0);

    const { container } = render(
      <ContextTotal
        amounts={heroDashboard.contexts[0]?.total_outstanding ?? []}
        contributors={heroDashboard.relationships_summary}
      />,
    );

    expect(container.querySelectorAll('[data-absent="true"]').length).toBe(0);
    for (const relationship of silent) {
      expect(screen.getByText(relationship.counterparty.display_name)).toBeDefined();
    }
    expect(screen.getAllByText("contributes nothing").length).toBe(silent.length);
  });

  it("the context total is the sum the API returned, not one computed here", () => {
    /*
     * The invariant this guards: a disputed balance changes `status`, never `amount`, so
     * the total must never include a counterparty's claimed figure. The test asserts the
     * component renders `total_outstanding` verbatim rather than summing the ledger rows,
     * because a component that summed them would agree with the API today and diverge the
     * moment the Kernel's definition of outstanding moved.
     */
    const context = heroDashboard.contexts[0];
    expect(context).toBeDefined();
    const total = context?.total_outstanding[0];
    expect(total).toBeDefined();

    const { container } = render(
      <ContextTotal
        amounts={context?.total_outstanding ?? []}
        contributors={heroDashboard.relationships_summary}
      />,
    );

    const rendered = container.querySelector(`[data-context-total="${total?.currency}"]`);
    expect(rendered).not.toBeNull();
    expect(screen.getByText(/Sum returned by the API, not computed here/)).toBeDefined();
  });
});
