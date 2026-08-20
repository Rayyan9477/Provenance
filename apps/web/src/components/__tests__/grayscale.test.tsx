import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { AttentionChip, RelationLabel, RetractionBadge } from "@/components/primitives/Chips";
import { TimePair } from "@/components/primitives/TimePair";
import { FixtureModeBanner } from "@/components/primitives/Banners";
import { ATTENTION_LEVELS, SUPPORT_RELATIONS } from "@/lib/api/contract";

/**
 * Colour is never the only carrier of meaning.
 *
 * This matters more here than on a typical product surface. The distinctions this design
 * makes with hue are not decorative rankings; they are the epistemic vocabulary. SUPPORTS
 * against CONTRADICTS, valid time against record time, the model proposing against the
 * Kernel committing. A reader who cannot separate red from green must still be able to see
 * that a piece of evidence contradicts rather than supports the position they are about to
 * act on.
 *
 * So every distinction carries a non-colour channel as well: a glyph, a word, a rule style,
 * or a data attribute that CSS turns into one. These tests read the text and the
 * attributes only. They never look at a computed colour, which is the point: if they pass
 * with the stylesheet removed entirely, the meaning survives grayscale.
 */

describe("meaning survives with colour removed", () => {
  it("every attention level renders its own name, not only its own hue", () => {
    for (const level of ATTENTION_LEVELS) {
      const { container, unmount } = render(<AttentionChip level={level} />);
      expect(container.textContent).toContain(level);
      expect(container.querySelector(`[data-attention="${level}"]`)).not.toBeNull();
      unmount();
    }
  });

  it("attention levels are distinguishable from each other by glyph alone", () => {
    const glyphs = new Set<string>();
    for (const level of ATTENTION_LEVELS) {
      const { container, unmount } = render(<AttentionChip level={level} />);
      const glyph = container.querySelector(".pv-chip-glyph")?.textContent ?? "";
      glyphs.add(glyph);
      unmount();
    }
    expect(glyphs.size, "two attention levels share a glyph").toBe(ATTENTION_LEVELS.length);
  });

  it("every support relation renders its word and its own glyph", () => {
    const glyphs = new Set<string>();
    for (const relation of SUPPORT_RELATIONS) {
      const { container, unmount } = render(<RelationLabel relation={relation} />);
      expect(container.textContent).toContain(relation);
      expect(container.querySelector(`[data-relation="${relation}"]`)).not.toBeNull();
      glyphs.add(container.querySelector(".pv-relation-glyph")?.textContent ?? "");
      unmount();
    }
    expect(glyphs.size, "two relations share a glyph").toBe(SUPPORT_RELATIONS.length);
  });

  it("the two clocks are labelled in words as well as separated by hue", () => {
    const { container } = render(
      <TimePair
        timeZone="UTC"
        validFrom="2026-06-01T00:00:00.000Z"
        validTo="2026-06-30T00:00:00.000Z"
        recordedAt="2026-09-18T14:05:00.000Z"
        recordVerb="ADMITTED"
      />,
    );
    expect(container.textContent).toContain("Valid time");
    expect(container.textContent).toContain("Record time");
    expect(container.querySelector('[data-clock="VALID"]')).not.toBeNull();
    expect(container.querySelector('[data-clock="RECORD"]')).not.toBeNull();
    /* The record verb is part of the sentence, not a colour: "when we came to know it". */
    expect(container.textContent).toContain("ADMITTED");
  });

  it("a retracted source says what retraction means, in words", () => {
    const { container } = render(<RetractionBadge status="RETRACTED" />);
    expect(container.textContent).toContain("RETRACTED");
    expect(container.textContent).toContain("excluded from retrieval");
    expect(container.textContent).toContain("retained in the record");
  });

  it("active evidence carries no badge, so a badge always means something", () => {
    const { container } = render(<RetractionBadge status="ACTIVE" />);
    expect(container.textContent).toBe("");
  });
});

describe("the fixture-mode disclosure", () => {
  /*
   * G12.7 fixes this copy character for character and the gate asserts it verbatim. The
   * test is here rather than only in the gate because the string is the kind of thing a
   * later edit tidies: "outputs" to "output", the dash to a hyphen, "DEMO" dropped as
   * redundant. Each of those would pass review and fail the gate.
   */
  it("renders the mandated copy exactly", () => {
    const { container } = render(<FixtureModeBanner fixtureMode={true} />);
    expect(container.textContent).toContain("DEMO FIXTURE MODE — model outputs are replayed");
  });

  it("has no control that could hide it", () => {
    const { container } = render(<FixtureModeBanner fixtureMode={true} />);
    const banner = container.querySelector('[data-fixture-banner="true"]');
    expect(banner).not.toBeNull();
    expect(banner?.querySelector("button")).toBeNull();
    expect(banner?.getAttribute("aria-hidden")).toBeNull();
    expect(banner?.getAttribute("hidden")).toBeNull();
  });

  it("renders nothing at all when the flag is false, rather than an empty banner", () => {
    const { container } = render(<FixtureModeBanner fixtureMode={false} />);
    expect(container.innerHTML).toBe("");
  });
});
