import Link from "next/link";
import { MemoryTracePanel } from "@/components/judge/MemoryTrace";
import { ErrorState, ForbiddenState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { AttentionChip, CaseStatusBadge, RevisionBadge } from "@/components/primitives/Chips";
import { getCase, getEntryPoints, getStateProof, getTrace } from "@/lib/api/reads";
import { loadMe, loadVersion, timeZoneOf } from "@/lib/session";
import { formatInstantOrRaw } from "@/lib/format";

/**
 * S11 -- Judge Mode.
 *
 * Panels A and B (what the consumer sees, and why the system believes it) sit above C and
 * D (the trace, and the systems status), because the product "aha" has to precede the
 * infrastructure reveal. A judge who sees the DAG first sees a diagram; a judge who sees
 * the contradiction first sees a diagram of something.
 *
 * Every panel is a server component fetching with the human access token. There is no
 * client-side store holding trace data between navigations, so a reload re-fetches and a
 * stale trace cannot survive a database change -- which is what G12.4 exists to prove.
 *
 * Access is gated on `GET /v1/me.judge_mode_enabled`. A judge requesting another tenant's
 * trace receives 404 rather than 403, because 403 confirms the trace exists.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function JudgeModePage({ searchParams }: PageProps) {
  const query = await searchParams;
  const [me, version, entry] = await Promise.all([loadMe(), loadVersion(), getEntryPoints()]);

  if (!me.ok) {
    return (
      <ErrorState
        heading="We could not read your session."
        detail={`GET ${me.path} returned ${me.status} ${me.code}`}
        traceId={me.traceId}
      />
    );
  }

  if (!me.data.judge_mode_enabled) {
    return (
      <ForbiddenState heading="Judge Mode is not enabled for this account.">
        <p>
          Judge Mode is gated on `judge_mode_enabled` from GET /v1/me. It is not a hidden route with
          a secret URL; the flag is the access control.
        </p>
      </ForbiddenState>
    );
  }

  const caseId = typeof query["case_id"] === "string" ? query["case_id"] : entry.heroCaseId;
  const traceId = typeof query["trace_id"] === "string" ? query["trace_id"] : entry.heroTraceId;
  const timeZone = timeZoneOf(me);

  const [caseResult, proof, trace] = await Promise.all([
    caseId === null ? null : getCase(caseId),
    caseId === null ? null : getStateProof(caseId),
    traceId === null ? null : getTrace(traceId),
  ]);

  const mcpVisible = me.data.feature_flags.mcp_trace_visible === true;
  const counterfactualEnabled = me.data.feature_flags.counterfactual_enabled === true;

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <div>
          <h1 className="pv-display">Judge mode</h1>
          <p className="pv-mono">
            {traceId === null ? (
              <Absent describe="no trace id could be resolved from the record" />
            ) : (
              `trace_id=${traceId}`
            )}
          </p>
        </div>
        {counterfactualEnabled ? (
          <Link className="pv-button" href="/judge/counterfactual">
            Open the counterfactual
          </Link>
        ) : (
          <p className="pv-label">Counterfactual disabled by feature flag</p>
        )}
      </header>

      <section className="pv-card pv-card-pad" aria-labelledby="pv-panel-a">
        <h2 className="pv-label" id="pv-panel-a">
          Panel A · what the consumer sees
        </h2>
        {caseResult === null ? (
          <p className="pv-prose">
            <Absent describe="no case id could be resolved from the record" />
          </p>
        ) : caseResult.ok ? (
          <>
            <p className="pv-title" style={{ marginTop: "var(--pv-space-2)" }}>
              {caseResult.data.title}
            </p>
            <div className="pv-meta-row">
              <span>{caseResult.data.counterparty.display_name}</span>
              <CaseStatusBadge status={caseResult.data.status} />
              <RevisionBadge revision={caseResult.data.revision} />
              <AttentionChip level={caseResult.data.attention_level} />
            </div>
            <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
              reopened_count={caseResult.data.reopened_count} · last_activity_at=
              {formatInstantOrRaw(caseResult.data.last_activity_at, timeZone)}
            </p>
            <p style={{ marginTop: "var(--pv-space-3)" }}>
              <Link className="pv-button" href={`/cases/${caseResult.data.case_id}`}>
                Open the consumer view
              </Link>
            </p>
          </>
        ) : (
          <ErrorState
            heading="Case unreadable."
            detail={`GET ${caseResult.path} returned ${caseResult.status} ${caseResult.code}`}
          />
        )}
      </section>

      <section className="pv-card pv-card-pad" aria-labelledby="pv-panel-b">
        <h2 className="pv-label" id="pv-panel-b">
          Panel B · state proof
        </h2>
        {proof === null ? (
          <p className="pv-prose">
            <Absent describe="no case id could be resolved from the record" />
          </p>
        ) : proof.ok ? (
          <>
            <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
              deterministic={String(proof.data.deterministic)} · model_used=
              {proof.data.model_used ?? "null"} · beliefs={proof.data.beliefs.length} · conflicts=
              {proof.data.conflicts.length} · retraction_filter_applied=
              {String(proof.data.excluded.retraction_filter_applied)} · retracted_evidence_count=
              {proof.data.excluded.retracted_evidence_count}
            </p>
            <ul className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
              {proof.data.beliefs.map((belief) => (
                <li key={belief.belief_id} data-belief-id={belief.belief_id}>
                  {belief.predicate} v{belief.current_version.version_no}{" "}
                  {belief.current_version.epistemic_status} grounded={String(belief.grounded)}{" "}
                  grounding_edges={belief.grounding.length}
                </li>
              ))}
            </ul>
            <p style={{ marginTop: "var(--pv-space-3)" }}>
              <Link className="pv-button" href={`/cases/${proof.data.case_id}/proof`}>
                Open the full proof
              </Link>
            </p>
          </>
        ) : (
          <ErrorState
            heading="State proof unreadable."
            detail={`GET ${proof.path} returned ${proof.status} ${proof.code}`}
          />
        )}
      </section>

      {trace === null ? (
        <section className="pv-card pv-card-pad">
          <h2 className="pv-label">Panel C · memory trace</h2>
          <p className="pv-prose">
            <Absent describe="no trace id could be resolved from the record" />
          </p>
        </section>
      ) : trace.ok ? (
        <MemoryTracePanel trace={trace.data} />
      ) : (
        <ErrorState
          heading={
            trace.status === 501 ? "The trace assembler is not built yet." : "Trace unreadable."
          }
          detail={`GET ${trace.path} returned ${trace.status} ${trace.code}`}
          traceId={trace.traceId}
        >
          {/* 501 and 500 are different claims and must not read alike. A 501
              says this capability has never been written and names the
              subsystem it waits on; a 500 says something broke. Showing
              "unreadable" for both told a reader the product was faulty when it
              was being precise about its own boundary -- the same confusion as
              reporting CANNOT RUN as FAIL, one layer up and in front of a
              judge. The API's message is rendered verbatim because it is a
              curated constant naming the missing subsystem, not an exception
              string. */}
          {trace.status === 501 ? (
            <p>{trace.message}</p>
          ) : (
            <p>
              A judge requesting a trace outside the demo tenant receives TRACE_NOT_FOUND rather
              than a forbidden response, because a forbidden response would confirm the trace
              exists.
            </p>
          )}
        </ErrorState>
      )}

      {mcpVisible && trace !== null && trace.ok ? (
        <section aria-labelledby="pv-mcp-heading">
          <div className="pv-section-heading">
            <h2 className="pv-label" id="pv-mcp-heading">
              MCP tool calls
            </h2>
            <p className="pv-label">A denied call is the boundary working</p>
          </div>
          <div className="pv-table-scroll">
            <table className="pv-table">
              <thead>
                <tr>
                  <th scope="col">View or statement</th>
                  <th scope="col">SQL role</th>
                  <th scope="col">Mode</th>
                  <th scope="col" className="pv-num">
                    Rows
                  </th>
                  <th scope="col" className="pv-num">
                    Time
                  </th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {trace.data.nodes
                  .filter((node) => node.type === "MCP_TOOL_CALL")
                  .map((node) => {
                    const attributes = node.attributes as Record<string, unknown>;
                    const cell = (key: string) =>
                      attributes[key] === undefined ? (
                        <Absent describe={`${key} not returned for this node`} />
                      ) : (
                        String(attributes[key])
                      );
                    return (
                      <tr key={node.id} data-node-id={node.id} data-node-status={node.status}>
                        <td>{cell("view_name")}</td>
                        <td>{cell("sql_role")}</td>
                        <td>{cell("access_mode")}</td>
                        <td className="pv-num">{cell("rows_returned")}</td>
                        <td className="pv-num">{node.duration_ms}ms</td>
                        <td
                          style={{
                            color: node.status === "OK" ? "var(--pv-kernel)" : "var(--pv-conflict)",
                          }}
                        >
                          {node.status}
                          {attributes["denied"] === true ? " · DENIED" : ""}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <section className="pv-card pv-card-pad" aria-labelledby="pv-panel-d">
        <h2 className="pv-label" id="pv-panel-d">
          Panel D · systems status
        </h2>
        {version.ok ? (
          <ul className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
            <li>git_sha={version.data.git_sha}</li>
            <li>
              schema_revision=
              {version.data.schema_revision === null || version.data.schema_revision === "" ? (
                <Absent describe="SCHEMA_REVISION is not set on this deployment" />
              ) : (
                version.data.schema_revision
              )}
            </li>
            <li>fixture_mode={String(version.data.fixture_mode)}</li>
            <li>agent_mode={version.data.agent_mode}</li>
            <li>otlp_export={version.data.otlp_export}</li>
            <li>db_ok={String(version.data.db_ok)}</li>
          </ul>
        ) : (
          <ErrorState
            heading="Operating mode unreadable."
            detail={`GET ${version.path} returned ${version.status} ${version.code}`}
          />
        )}
        <p className="pv-prose" style={{ marginTop: "var(--pv-space-3)" }}>
          These six indicators come from GET /v1/version, the single authoritative disclosure
          channel. It is unauthenticated by design, so a reader can curl it without a token and
          check this page against it.
        </p>

        <h3 className="pv-label" style={{ marginTop: "var(--pv-space-5)" }}>
          Feature flags, every key with its value
        </h3>
        <ul className="pv-mono">
          {Object.entries(me.data.feature_flags).map(([key, value]) => (
            <li key={key}>
              {key}={String(value)}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
