import type { TraceNode, TraceResponse } from "@/lib/api/contract";

/**
 * Trace layout.
 *
 * This module performs layout and nothing else. It synthesizes no data: no inferred node,
 * no placeholder edge, no default label, no assumed parent. If the payload lacks a node,
 * the DAG lacks it too. If a node's type is not one of the seventeen, it is still
 * rendered -- with its actual type string -- rather than coerced into a known one, because
 * a silently remapped node type would hide a contract drift in the one view whose whole
 * job is to be checkable.
 *
 * G12.2 intercepts `GET /v1/traces/{id}`, collects every `[data-node-id]` in the DOM, and
 * asserts the DOM set is a subset of the payload set. That assertion can only hold if the
 * only source of node ids is the payload, which is why there is no id construction
 * anywhere below.
 */

export type Lane = "MODEL" | "DETERMINISTIC" | "UNCLASSIFIED";

export interface LaidOutNode {
  readonly node: TraceNode;
  readonly lane: Lane;
  readonly children: readonly LaidOutNode[];
}

export interface TraceLayout {
  readonly roots: readonly LaidOutNode[];
  readonly modelCount: number;
  readonly deterministicCount: number;
  readonly unclassifiedCount: number;
  readonly totalNodes: number;
  /**
   * Nodes named by `boundary` that do not appear in `nodes[]`.
   *
   * Reported rather than ignored: a boundary that names a node the payload does not carry
   * is a server-side defect, and hiding it here would make the trace look consistent when
   * it is not.
   */
  readonly danglingBoundaryIds: readonly string[];
}

function laneOf(trace: TraceResponse, nodeId: string): Lane {
  if (trace.boundary.model_node_ids.includes(nodeId)) return "MODEL";
  if (trace.boundary.deterministic_node_ids.includes(nodeId)) return "DETERMINISTIC";
  return "UNCLASSIFIED";
}

export function layoutTrace(trace: TraceResponse): TraceLayout {
  const byId = new Map<string, TraceNode>();
  for (const node of trace.nodes) byId.set(node.id, node);

  const childrenOf = new Map<string, TraceNode[]>();
  const roots: TraceNode[] = [];

  for (const node of trace.nodes) {
    const parentId = node.parent_id;
    /* A parent_id naming a node the payload does not carry makes the node a root. It is
       not given an invented parent. */
    if (parentId !== undefined && byId.has(parentId)) {
      const bucket = childrenOf.get(parentId);
      if (bucket === undefined) childrenOf.set(parentId, [node]);
      else bucket.push(node);
    } else {
      roots.push(node);
    }
  }

  const build = (node: TraceNode): LaidOutNode => ({
    node,
    lane: laneOf(trace, node.id),
    children: (childrenOf.get(node.id) ?? []).map(build),
  });

  const declared = new Set([
    ...trace.boundary.model_node_ids,
    ...trace.boundary.deterministic_node_ids,
  ]);
  const dangling = [...declared].filter((id) => !byId.has(id));

  let model = 0;
  let deterministic = 0;
  let unclassified = 0;
  for (const node of trace.nodes) {
    const lane = laneOf(trace, node.id);
    if (lane === "MODEL") model += 1;
    else if (lane === "DETERMINISTIC") deterministic += 1;
    else unclassified += 1;
  }

  return {
    roots: roots.map(build),
    modelCount: model,
    deterministicCount: deterministic,
    unclassifiedCount: unclassified,
    totalNodes: trace.nodes.length,
    danglingBoundaryIds: dangling,
  };
}

/** Flatten for counting and for tests. Order is the payload's order. */
export function flatten(nodes: readonly LaidOutNode[]): readonly LaidOutNode[] {
  return nodes.flatMap((entry) => [entry, ...flatten(entry.children)]);
}
