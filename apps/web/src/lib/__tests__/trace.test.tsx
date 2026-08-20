import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { flatten, layoutTrace } from "@/lib/trace";
import { MemoryTracePanel } from "@/components/judge/MemoryTrace";
import { heroTrace } from "@/fixtures/hero.fixture";
import type { TraceResponse } from "@/lib/api/contract";

/**
 * The trace is not an animation.
 *
 * `G12.2` states the property as a browser assertion: intercept `GET /v1/traces/{id}`,
 * collect every `[data-node-id]` in the DOM, and require the DOM set to be a subset of the
 * payload set. That gate needs Phase 8's API and a running browser. This is the same
 * property, asserted against the layout module and the panel directly, so that the moment
 * a node id can be synthesised anywhere in the client the unit suite goes red rather than
 * waiting for the end-to-end run.
 *
 * The subset direction is the one that matters. A DOM node whose id is not in the payload
 * is a node the client invented, and a trace with an invented node in it is a drawing of a
 * system rather than a record of one.
 */

describe("trace layout synthesises nothing", () => {
  it("lays out exactly the nodes the payload carries", () => {
    const layout = layoutTrace(heroTrace);
    const laidOut = flatten(layout.roots).map((entry) => entry.node.id);

    expect(new Set(laidOut).size).toBe(heroTrace.nodes.length);
    expect(layout.totalNodes).toBe(heroTrace.nodes.length);
    for (const id of laidOut) {
      expect(heroTrace.nodes.some((node) => node.id === id)).toBe(true);
    }
  });

  it("puts a node whose parent is absent at the root rather than inventing a parent", () => {
    const firstNode = heroTrace.nodes[0];
    expect(firstNode).toBeDefined();
    if (firstNode === undefined) return;

    const orphaned: TraceResponse = {
      ...heroTrace,
      nodes: [{ ...firstNode, id: "orphan", parent_id: "a-parent-that-does-not-exist" }],
    };

    const layout = layoutTrace(orphaned);
    expect(layout.roots.length).toBe(1);
    expect(layout.roots[0]?.node.id).toBe("orphan");
    expect(flatten(layout.roots).length).toBe(1);
  });

  it("reports a boundary that names nodes the payload does not carry", () => {
    const dangling: TraceResponse = {
      ...heroTrace,
      boundary: {
        ...heroTrace.boundary,
        model_node_ids: [...heroTrace.boundary.model_node_ids, "not-in-the-payload"],
      },
    };

    const layout = layoutTrace(dangling);
    expect(layout.danglingBoundaryIds).toContain("not-in-the-payload");
    /* Reported, never silently dropped: a boundary that names a missing node is a
       server-side defect, and hiding it makes an inconsistent trace look consistent. */
    const { container } = render(<MemoryTracePanel trace={dangling} />);
    expect(container.textContent).toContain("not-in-the-payload");
  });

  it("classifies a node the boundary does not mention as unclassified, not as deterministic", () => {
    const unclassified: TraceResponse = {
      ...heroTrace,
      boundary: { ...heroTrace.boundary, model_node_ids: [], deterministic_node_ids: [] },
    };

    const layout = layoutTrace(unclassified);
    expect(layout.modelCount).toBe(0);
    expect(layout.deterministicCount).toBe(0);
    expect(layout.unclassifiedCount).toBe(heroTrace.nodes.length);
    /*
     * Defaulting an unclassified node into the deterministic lane would put a model call
     * on the side of the boundary that binds. Nothing may drift in that direction.
     */
  });
});

describe("every rendered node id exists in the payload", () => {
  it("the DOM set is a subset of the payload set (G12.2, as a unit test)", () => {
    const { container } = render(<MemoryTracePanel trace={heroTrace} />);

    const domIds = [...container.querySelectorAll("[data-node-id]")].map((el) =>
      el.getAttribute("data-node-id"),
    );
    const payloadIds = new Set(heroTrace.nodes.map((node) => node.id));

    expect(domIds.length).toBeGreaterThanOrEqual(8);
    for (const id of domIds) {
      expect(payloadIds.has(id ?? ""), `${id} is not in the payload`).toBe(true);
    }
  });

  it("renders an unknown node type verbatim rather than coercing it to a known one", () => {
    const firstNode = heroTrace.nodes[0];
    if (firstNode === undefined) return;

    const drifted: TraceResponse = {
      ...heroTrace,
      nodes: [{ ...firstNode, type: "SOMETHING_NEW" as never }],
      boundary: { ...heroTrace.boundary, model_node_ids: [], deterministic_node_ids: [] },
    };

    const { container } = render(<MemoryTracePanel trace={drifted} />);
    expect(container.querySelector('[data-node-type="SOMETHING_NEW"]')).not.toBeNull();
    expect(container.textContent).toContain("not one of the seventeen node types");
  });

  it("renders a failed node in the failure treatment rather than omitting it", () => {
    const firstNode = heroTrace.nodes[0];
    if (firstNode === undefined) return;

    const failed: TraceResponse = {
      ...heroTrace,
      nodes: heroTrace.nodes.map((node, index) =>
        index === 0 ? { ...node, status: "FAILED" as const } : node,
      ),
    };

    const { container } = render(<MemoryTracePanel trace={failed} />);
    expect(container.querySelector('[data-node-status="FAILED"]')).not.toBeNull();
  });

  it("carries no animation: nothing in the panel schedules a timer", () => {
    /*
     * Section 23.3 forbids an animated trace outright. The structural guarantee is that
     * the panel is a server component with no client boundary at all, so there is nothing
     * to animate with. This asserts the consequence a reader can check: the rendered
     * markup contains no element that would run on a clock.
     */
    const { container } = render(<MemoryTracePanel trace={heroTrace} />);
    expect(container.querySelector("[style*='animation']")).toBeNull();
    expect(container.querySelector("[style*='transition']")).toBeNull();
  });
});
