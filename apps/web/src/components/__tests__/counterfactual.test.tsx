import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { CounterfactualPanel } from "@/components/judge/CounterfactualPanel";
import { heroCounterfactual } from "@/fixtures/hero.fixture";
import type { CounterfactualResponse } from "@/lib/api/contract";

/**
 * The parity render gate.
 *
 * `CANONICAL_DECISIONS.md` (Counterfactual parity canon, frozen 2026-08-17) is normative:
 * `parity.all_equal = false` means the two output columns are **not rendered**, and a
 * failure banner replaces them.
 *
 * The reason is worth restating because it is the reason the gate must be a test rather
 * than a convention. The counterfactual's entire claim is that the only difference between
 * the two runs is memory. If the pre-flight checks show the runs differed in the model id,
 * the prompt version, the graph version, the decode parameters, the artifact, or its hash,
 * then any difference in output might be caused by that instead. Rendering the columns
 * anyway invites precisely the objection the comparison exists to defeat, and it invites it
 * from the one reader whose scepticism matters.
 *
 * Showing nothing is the stronger position. This test holds it.
 */

function withParity(overrides: Partial<CounterfactualResponse["parity"]>): CounterfactualResponse {
  return {
    ...heroCounterfactual,
    parity: { ...heroCounterfactual.parity, ...overrides },
  };
}

describe("the counterfactual parity gate", () => {
  it("the fixture passes parity, so the negative cases below are meaningful", () => {
    expect(heroCounterfactual.parity.all_equal).toBe(true);
  });

  it("renders both columns when every check matched", () => {
    const { container } = render(<CounterfactualPanel cf={heroCounterfactual} />);
    expect(container.querySelector('[data-columns-rendered="true"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-column="off"]').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('[data-column="on"]').length).toBeGreaterThan(0);
  });

  it("renders NO columns when all_equal is false", () => {
    const failed = withParity({
      all_equal: false,
      model_id: { off: "model-a", on: "model-b", equal: false },
    });

    const { container } = render(<CounterfactualPanel cf={failed} />);

    expect(container.querySelector('[data-columns-rendered="true"]')).toBeNull();
    expect(container.querySelector('[data-columns-rendered="false"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-column="off"]').length).toBe(0);
    expect(container.querySelectorAll('[data-column="on"]').length).toBe(0);
  });

  it("does not render either arm's output text when parity failed", () => {
    const failed = withParity({
      all_equal: false,
      graph_version: { off: "graph-a", on: "graph-b", equal: false },
    });

    const { container } = render(<CounterfactualPanel cf={failed} />);
    const text = container.textContent ?? "";

    /*
     * The strongest form of the assertion. It is not enough that the grid element is
     * absent; the readings themselves must not appear anywhere on the page, because a
     * reader quoting one of them has been misled regardless of which element carried it.
     */
    expect(text).not.toContain(heroCounterfactual.memory_off.output.headline);
    expect(text).not.toContain(heroCounterfactual.memory_on.output.headline);
    expect(text).not.toContain(heroCounterfactual.memory_off.output.recommended_action);
    expect(text).not.toContain(heroCounterfactual.memory_on.output.recommended_action);
  });

  it("names every check that differed, so the failure is diagnosable", () => {
    const failed = withParity({
      all_equal: false,
      model_id: { off: "model-a", on: "model-b", equal: false },
      prompt_version: { off: "prompt-1", on: "prompt-2", equal: false },
    });

    const { container } = render(<CounterfactualPanel cf={failed} />);
    const text = container.textContent ?? "";

    expect(text).toContain("model-a");
    expect(text).toContain("model-b");
    expect(text).toContain("prompt-1");
    expect(text).toContain("prompt-2");
  });

  it("gates on all_equal alone, even when every individual check reads as equal", () => {
    /*
     * `all_equal` is the server's conjunction and it is the field the canon names. If it
     * is false while the six entries say otherwise, the payload is inconsistent, and the
     * safe reading of an inconsistent parity block is that parity failed. A client that
     * recomputed the conjunction itself would render the columns here, which is exactly
     * the wrong direction to fail in.
     */
    const failed = withParity({ all_equal: false });

    const { container } = render(<CounterfactualPanel cf={failed} />);
    expect(container.querySelector('[data-columns-rendered="true"]')).toBeNull();
  });

  it("renders corpus_size_visible from the payload rather than as a constant", () => {
    const { container } = render(<CounterfactualPanel cf={heroCounterfactual} />);
    const text = container.textContent ?? "";
    expect(text).toContain(String(heroCounterfactual.memory_on.corpus_size_visible));
    expect(text).toContain(String(heroCounterfactual.memory_off.corpus_size_visible));
  });

  it("selects its header copy from memory_on.strategy, never from a constant", () => {
    const noStrategy: CounterfactualResponse = {
      ...heroCounterfactual,
      memory_on: { ...heroCounterfactual.memory_on, strategy: undefined },
    };

    const { container } = render(<CounterfactualPanel cf={noStrategy} />);
    /* No strategy means no claim about what the MEMORY ON column is. */
    expect(container.querySelector('[data-absent="true"]')).not.toBeNull();
    expect(container.querySelector("[data-strategy]")).toBeNull();
  });

  it("renders the safety block from the payload, including its false values", () => {
    const { container } = render(<CounterfactualPanel cf={heroCounterfactual} />);
    for (const key of Object.keys(heroCounterfactual.safety)) {
      expect(container.querySelector(`[data-safety-check="${key}"]`)).not.toBeNull();
    }
  });
});
