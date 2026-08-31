import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Absent } from "@/components/primitives/Absent";
import {
  AttentionChip,
  CaseStatusBadge,
  IdChip,
  RevisionBadge,
} from "@/components/primitives/Chips";
import { BuildStamp } from "@/components/primitives/Banners";
import { TimePair } from "@/components/primitives/TimePair";
import { TypedRecordBlock } from "@/components/primitives/TypedRecordBlock";
import { ContextTotal } from "@/components/record/RelationshipLedger";
import { CanonicalPosition, CounterpartyAssertions } from "@/components/record/CounterpartyFile";
import { GroundingEdgeRow } from "@/components/proof/Grounding";
import { field, row } from "@/lib/typed/record";
import type { TypedRecord } from "@/lib/typed/record";
import type { EvidenceSource } from "@/lib/api/contract";
import { heroDashboard, heroStateProofs } from "@/fixtures/hero.fixture";

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
      // `label`, not `counterparty.display_name`. The corpus holds two live
      // relationships with the SAME counterparty -- an ISP account at the old
      // address and one at the new -- so display names produced two visually
      // identical rows carrying different balances. The label is what carries
      // the distinguishing part.
      expect(screen.getByText(relationship.label)).toBeDefined();
    }
    expect(screen.getAllByText("contributes nothing").length).toBe(silent.length);
  });

  it("names each row by its label, so two accounts with one counterparty stay apart", () => {
    /*
     * The LIVE corpus holds two relationships with the same counterparty -- an
     * ISP account at the old address and one at the new -- and rendering
     * `counterparty.display_name` produced two visually identical rows carrying
     * different balances. One of them is the ISP whose cancellation the entire
     * demo turns on.
     *
     * This asserts the MECHANISM (the row is named by `label`) rather than the
     * corpus property (a duplicate exists), because `hero.fixture.ts` has no
     * duplicate: the first draft of this test guarded the property, went red,
     * and the red was the fixture disagreeing with production. That gap is
     * worth its own note -- it is the same class that put a `Money` object
     * where a decimal string was declared and took nine live routes down --
     * but a test that can only pass once the fixture is rewritten is a test
     * that gets deleted, so it checks the thing the component does.
     */
    render(
      <ContextTotal
        amounts={heroDashboard.contexts[0]?.total_outstanding ?? []}
        contributors={heroDashboard.relationships_summary}
      />,
    );

    for (const relationship of heroDashboard.relationships_summary) {
      expect(
        screen.getByText(relationship.label),
        `row for ${relationship.relationship_id} is not named by its label`,
      ).toBeDefined();
    }

    // And the labels are DISTINCT, which is the property that actually makes
    // two rows tellable apart. An earlier draft asserted the label was longer
    // than the display name -- a guess, and a wrong one: a fixture label is 19
    // characters against a 30-character display name.
    const labels = heroDashboard.relationships_summary.map((r) => r.label);
    expect(new Set(labels).size, "two relationships share a label").toBe(labels.length);
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
    expect(screen.getByText(/sum returned by the API, not computed here/i)).toBeDefined();
  });
});

/**
 * The build stamp is on every screen, so an absence rendered as a gap is on
 * every screen too.
 *
 * `SCHEMA_REVISION` is an environment variable with no default. Cloud Run did
 * not set it, the API served `schema_revision: null`, and the stamp interpolated
 * that straight into the line -- so all fourteen screens carried a bare
 * `schema=` followed by a space. A reader cannot tell a field that broke from a
 * field nobody supplied, and the whole point of this strip is that it is the
 * channel a judge checks the running system against.
 */
describe("the build stamp marks an absent schema revision", () => {
  const base = {
    service: "provenance-control-plane",
    version: "1.0.0",
    git_sha: "775b47d29f6b5db98d2e984e9a0422a2ad61c67a",
    api_version: "v1",
    contracts_schema_version: "1.0",
    region: "us-east4",
    built_at: "2026-08-30T21:13:01.949644Z",
    fixture_mode: false,
    agent_mode: "LIVE",
    otlp_export: "DISABLED",
    db_ok: true,
  } as const;

  it("renders an absence marker when the deployment did not set one", () => {
    render(<BuildStamp version={{ ...base, schema_revision: null }} />);
    const stamp = screen.getByText(/git_sha=/).closest("span");
    expect(stamp?.textContent).not.toMatch(/schema=\s*agent_mode/);
    expect(screen.getByTitle(/SCHEMA_REVISION is not set/)).toBeTruthy();
  });

  it("treats an empty string the same as null", () => {
    render(<BuildStamp version={{ ...base, schema_revision: "" }} />);
    expect(screen.getByTitle(/SCHEMA_REVISION is not set/)).toBeTruthy();
  });

  it("renders the revision verbatim when there is one", () => {
    render(<BuildStamp version={{ ...base, schema_revision: "0009_gemini_embedding_plane" }} />);
    expect(screen.getByText(/0009_gemini_embedding_plane/)).toBeTruthy();
  });
});

/**
 * A hash on screen must come from a hash column.
 *
 * The grounding row used to print `sha256:` in front of an abbreviated `artifact.subject`,
 * falling back to the evidence UUID when the artifact had no subject line. Neither of those
 * is a digest. `EvidenceSource` carries no hash at all, and the elision that makes a long
 * hash readable is exactly what made an email subject look like one. On the State Proof,
 * which exists to show a reader why Provenance believes something, that is a fabricated
 * cryptographic claim sitting beside genuine ones.
 *
 * The assertion is the plain one: the string "sha256" must not appear on a grounding row,
 * because nothing on that row is a sha256.
 */
describe("the grounding row never manufactures a hash", () => {
  const evidenceEdges = Object.values(heroStateProofs)
    .flatMap((proof) => proof.beliefs)
    .flatMap((belief) => belief.grounding)
    .filter((edge) => "exact_text" in edge.source);

  it("has evidence-backed grounding to assert against", () => {
    expect(evidenceEdges.length).toBeGreaterThan(0);
  });

  it("prints no digest, and shows the subject line as a subject", () => {
    for (const edge of evidenceEdges) {
      const { container, unmount } = render(
        <ul>
          <GroundingEdgeRow edge={edge} timeZone="UTC" />
        </ul>,
      );
      const text = container.textContent ?? "";
      expect(text, "a grounding row claimed a hash the payload does not carry").not.toContain(
        "sha256:",
      );

      const subject = (edge.source as EvidenceSource).artifact.subject;
      if (subject === null) {
        expectAbsence(container);
      } else {
        expect(text).toContain(subject);
      }
      unmount();
    }
  });
});

/**
 * The null-subject branch, exercised on purpose.
 *
 * The test above walks the hero fixture and takes whichever branch each edge
 * happens to fall into. Every evidence source in `heroStateProofs` carries a
 * subject, so the `<Absent>` path added to the grounding row was never reached
 * by it -- the assertion existed and was dead. A branch that is only tested
 * when the fixture happens to contain it is tested by luck.
 *
 * So this constructs the case directly. It is the branch that matters most:
 * the old code fell back to `abbreviateHash(evidence_id)` here and labelled a
 * UUID as a digest, which is the more misleading of the two failures because a
 * UUID already looks like a hash.
 */
describe("a grounding row whose artifact has no subject", () => {
  const withSubject = Object.values(heroStateProofs)
    .flatMap((proof) => proof.beliefs)
    .flatMap((belief) => belief.grounding)
    .find((edge) => "exact_text" in edge.source);

  it("marks the absence instead of printing the evidence id as a hash", () => {
    expect(withSubject, "the hero fixture carries no evidence-backed grounding").toBeDefined();
    if (withSubject === undefined) return;

    const source = withSubject.source as EvidenceSource;
    const edge = {
      ...withSubject,
      source: { ...source, artifact: { ...source.artifact, subject: null } },
    };

    const { container } = render(
      <ul>
        <GroundingEdgeRow edge={edge} timeZone="UTC" />
      </ul>,
    );
    const text = container.textContent ?? "";

    expectAbsence(container);
    // `sha256:` with the colon is the fabricated form. The row legitimately
    // contains "content_sha256" -- it links to the artifact page, which renders
    // the real digest from the column that holds one -- so asserting on the
    // bare word would forbid the fix as well as the defect.
    expect(text, "a null subject fell back to something that looks like a digest").not.toContain(
      "sha256:",
    );
    expect(text, "the evidence id was printed where a subject belongs").not.toContain(
      source.evidence_id,
    );
  });
});
