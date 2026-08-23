import type { TraceNode, TraceResponse } from "@/lib/api/contract";
import { TRACE_NODE_TYPES } from "@/lib/api/contract";
import { flatten, layoutTrace } from "@/lib/trace";
import type { LaidOutNode, Lane } from "@/lib/trace";
import { Absent } from "@/components/primitives/Absent";

/**
 * Panel C -- the Memory Trace DAG.
 *
 * Two lanes, left to right: what the model proposed, and what the Kernel deterministically
 * decided, committed, and did. The lanes come from the payload's own `boundary` block, not
 * from a client-side guess about which node types are "AI".
 *
 * `data-node-id` is on every node, including the `CANONICAL_CHANGE` children nested under
 * their `DB_TRANSACTION`, so G12.2 can collect the DOM set and check it against the
 * payload. Nothing here animates: an animated trace is a cartoon of a system, and section
 * 23.3 forbids it outright.
 *
 * MCP calls are first-class nodes, and a denied call is rendered in the failure treatment
 * rather than hidden. A denial is the most informative thing in the whole panel -- it is
 * the database boundary refusing the agent in public.
 */

const KNOWN_TYPES = new Set<string>(TRACE_NODE_TYPES);

function AttributeList({ node }: { readonly node: TraceNode }) {
  const entries = Object.entries(node.attributes);
  if (entries.length === 0) {
    return (
      <p className="pv-mono">
        <Absent describe="no attributes were returned for this node" />
      </p>
    );
  }
  return (
    <ul className="pv-mono">
      {entries.map(([key, value]) => (
        <li key={key} data-attribute={key}>
          {key}={typeof value === "object" ? JSON.stringify(value) : String(value)}
        </li>
      ))}
    </ul>
  );
}

function Node({
  entry,
  depth,
  omitLane,
}: {
  readonly entry: LaidOutNode;
  readonly depth: number;
  /* Model nodes render once, in the model lane. The deterministic lane omits them rather
     than emitting a second element with the same data-node-id. */
  readonly omitLane?: Lane;
}) {
  const { node } = entry;
  const children = entry.children.filter((child) => child.lane !== omitLane);
  const unknownType = !KNOWN_TYPES.has(node.type);

  return (
    <div
      className={depth === 0 ? "pv-dag-node" : "pv-dag-node pv-dag-child"}
      data-node-id={node.id}
      data-node-type={node.type}
      data-node-status={node.status}
      data-lane={entry.lane}
    >
      <p className="pv-mono">
        <strong>{node.type}</strong>
        {unknownType ? (
          <span className="pv-chip" data-attention="URGENT">
            not one of the seventeen node types
          </span>
        ) : null}{" "}
        · {node.status} · {node.duration_ms}ms
      </p>
      <p className="pv-mono" style={{ color: "var(--pv-ink-secondary)" }}>
        {node.summary}
      </p>
      <AttributeList node={node} />
      {node.refs && node.refs.length > 0 ? (
        <ul className="pv-mono" style={{ color: "var(--pv-ink-faint)" }}>
          {node.refs.map((ref) => (
            <li key={`${ref.table}.${ref.column}.${ref.value}`} data-node-ref="true">
              {ref.table}.{ref.column}={ref.value}
              {ref.cardinality === undefined ? "" : ` (${ref.cardinality})`}
            </li>
          ))}
        </ul>
      ) : null}
      {children.length > 0 ? (
        <div className="pv-stack-tight" style={{ marginTop: "var(--pv-space-2)" }}>
          {children.map((child) => (
            <Node entry={child} key={child.node.id} depth={depth + 1} omitLane={omitLane} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function MemoryTracePanel({ trace }: { readonly trace: TraceResponse }) {
  const layout = layoutTrace(trace);
  const all = flatten(layout.roots);
  const modelRoots = layout.roots.filter((entry) => entry.lane === "MODEL");
  const otherRoots = layout.roots.filter((entry) => entry.lane !== "MODEL");

  return (
    <section aria-labelledby="pv-trace-heading" data-trace-id={trace.trace_id}>
      <div className="pv-section-heading">
        <h2 className="pv-label" id="pv-trace-heading">
          Panel C · Memory trace
        </h2>
        <p className="pv-mono">
          {layout.totalNodes} nodes · {trace.edges.length} edges · {trace.duration_ms}ms wall ·
          status={trace.status}
        </p>
      </div>

      {layout.danglingBoundaryIds.length > 0 ? (
        <p className="pv-state" data-kind="ERROR" role="alert">
          The boundary block names {layout.danglingBoundaryIds.length} node
          {layout.danglingBoundaryIds.length === 1 ? "" : "s"} the payload does not carry:{" "}
          {layout.danglingBoundaryIds.join(", ")}. Reported rather than hidden.
        </p>
      ) : null}

      <div className="pv-dag" style={{ marginTop: "var(--pv-space-4)" }}>
        <div className="pv-dag-lane" data-lane="MODEL">
          <p className="pv-label" style={{ color: "var(--pv-model)" }}>
            Agent proposes · no write authority
          </p>
          {modelRoots.length === 0 ? (
            <p className="pv-mono">
              No node in this trace is classified as a model call at the top level. Model calls
              appear nested under their agent run.
            </p>
          ) : null}
          {all
            .filter((entry) => entry.lane === "MODEL")
            .map((entry) => (
              <Node entry={{ ...entry, children: [] }} key={`model-${entry.node.id}`} depth={0} />
            ))}
          <p className="pv-label" style={{ color: "var(--pv-model)" }}>
            {layout.modelCount} model node{layout.modelCount === 1 ? "" : "s"}
          </p>
        </div>

        <div className="pv-dag-lane" data-lane="DETERMINISTIC">
          <p className="pv-label" style={{ color: "var(--pv-kernel)" }}>
            Kernel commits · ACID, deterministic
          </p>
          {otherRoots.map((entry) => (
            <Node entry={entry} key={entry.node.id} depth={0} omitLane="MODEL" />
          ))}
          <p className="pv-label" style={{ color: "var(--pv-kernel)" }}>
            {layout.deterministicCount} deterministic node
            {layout.deterministicCount === 1 ? "" : "s"}
            {layout.unclassifiedCount > 0
              ? ` · ${layout.unclassifiedCount} unclassified by the boundary block`
              : ""}
          </p>
        </div>
      </div>

      <p className="pv-prose" style={{ marginTop: "var(--pv-space-3)" }}>
        {trace.boundary.note}
      </p>
    </section>
  );
}
