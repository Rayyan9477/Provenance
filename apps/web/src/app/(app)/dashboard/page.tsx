import Link from "next/link";
import { AttentionRow } from "@/components/record/AttentionRow";
import { ContextTotal, RelationshipCard } from "@/components/record/RelationshipLedger";
import { EmptyState, ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { getDashboard } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { formatDate } from "@/lib/format";

/**
 * S01 -- the dashboard, "The Move".
 *
 * Every number here is a field of `GET /v1/dashboard`. There is no client-side arithmetic
 * over a different source: the context total is `contexts[].total_outstanding`, the counts
 * are `counts.*`, and the relationship figures are `relationships_summary[].outstanding`.
 * The alternative -- summing the relationship rows in the browser -- would produce a
 * number that happened to match today and would silently diverge the moment the Kernel's
 * definition of "outstanding" moved.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function asArray(value: string | string[] | undefined): readonly string[] | undefined {
  if (value === undefined) return undefined;
  return Array.isArray(value) ? value : [value];
}

export default async function DashboardPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const contextId = typeof params["context_id"] === "string" ? params["context_id"] : null;
  const attentionOnly = params["attention_only"] === "true";
  const status = asArray(params["status"]);

  const [me, dashboard] = await Promise.all([
    loadMe(),
    getDashboard({ contextId, attentionOnly, status }),
  ]);
  const timeZone = timeZoneOf(me);

  if (!dashboard.ok) {
    return (
      <ErrorState
        heading="We could not read your dashboard."
        detail={`GET ${dashboard.path} returned ${dashboard.status} ${dashboard.code}`}
        traceId={dashboard.traceId}
      >
        <p>
          Nothing is shown below rather than an empty record, because an unreachable endpoint and an
          empty record are different facts and only one of them is about you.
        </p>
      </ErrorState>
    );
  }

  const data = dashboard.data;
  const context = data.contexts[0];
  const generated = formatDate(data.generated_at, timeZone);

  return (
    <div className="pv-stack">
      <section className="pv-card pv-card-pad" aria-labelledby="pv-context-heading">
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--pv-space-6)",
            justifyContent: "space-between",
          }}
        >
          <div>
            <p className="pv-label">Context</p>
            <h1 className="pv-display" id="pv-context-heading">
              {context ? context.title : <Absent describe="no context returned" />}
            </h1>
            <p className="pv-meta-row pv-mono">
              {me.ok ? (
                <span>{me.data.display_name}</span>
              ) : (
                <Absent describe="identity not read" />
              )}
              <span>·</span>
              <span>
                {context ? (
                  `${context.relationship_count} relationships in scope`
                ) : (
                  <Absent describe="relationship count not returned" />
                )}
              </span>
              <span>·</span>
              <span>
                {generated === null ? (
                  <Absent describe="generation time not returned" />
                ) : (
                  `as at ${generated}`
                )}
              </span>
            </p>
          </div>

          {context ? (
            <ContextTotal
              amounts={context.total_outstanding}
              contributors={data.relationships_summary}
            />
          ) : null}
        </div>
      </section>

      <section aria-labelledby="pv-attention-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-attention-heading">
            Attention
          </h2>
          <p className="pv-label">{data.counts.cases_needing_attention} requiring response</p>
        </div>

        {data.cases_attention.length === 0 ? (
          <EmptyState heading="Nothing is waiting on you.">
            <p>
              No case in this context carries an attention level above NONE. That is a statement
              about the record, not a failure to load it.
            </p>
          </EmptyState>
        ) : (
          <ul>
            {data.cases_attention.map((item) => (
              <AttentionRow key={item.case_id} item={item} timeZone={timeZone} />
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="pv-ledger-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-ledger-heading">
            Relationship ledger
          </h2>
          <p className="pv-label">Committed less fulfilled equals outstanding</p>
        </div>
        <ul className="pv-grid pv-grid-4" style={{ marginTop: "var(--pv-space-4)" }}>
          {data.relationships_summary.map((item) => (
            <RelationshipCard key={item.relationship_id} item={item} />
          ))}
        </ul>
      </section>

      <section aria-labelledby="pv-counts-heading">
        <h2 className="pv-sr-only" id="pv-counts-heading">
          Counts
        </h2>
        <ul className="pv-grid pv-grid-5">
          {(
            [
              ["Unresolved commitments", data.counts.unresolved_commitments],
              ["Active conflicts", data.counts.active_conflicts],
              ["Drafts pending", data.counts.action_intents_pending],
              ["Armed watches", data.counts.triggers_armed],
              ["Fired watches", data.counts.triggers_fired_unhandled],
            ] as const
          ).map(([label, value]) => (
            <li className="pv-card pv-card-pad" key={label}>
              <p className="pv-figure">{value}</p>
              <p className="pv-label">{label}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="pv-card pv-card-pad">
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--pv-space-4)",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <p className="pv-title">Add to the record</p>
            <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
              Drop bytes, or forward a bill to your inbound address.
            </p>
          </div>
          <Link className="pv-button" href="/ingest">
            Open intake gateway
          </Link>
        </div>
      </section>
    </div>
  );
}
