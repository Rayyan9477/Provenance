import Link from "next/link";
import { ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { RetractionBadge } from "@/components/primitives/Chips";
import { TimePair } from "@/components/primitives/TimePair";
import { getArtifact, getStateProof } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { formatInstant, formatInstantOrRaw } from "@/lib/format";
import type { EvidenceSource } from "@/lib/api/contract";

/**
 * S06b -- one artifact.
 *
 * The design shows a rendered page of the document, a list of extracted spans with their
 * character offsets, and a five-step chain of custody with a timestamp on each step.
 * Section 8.20 returns the first as a pre-signed `download_url` that may be null, the
 * second not at all, and the third only in part. This page therefore does three things
 * and is explicit about each.
 *
 *   The page render appears only when `download_url` is non-null. When the deployment
 *   does not issue one, the region says so instead of showing a grey rectangle that a
 *   reader could mistake for a document that failed to load.
 *
 *   The extracted spans come from the State Proof of the cases this artifact is linked
 *   to, filtered to evidence whose `artifact_id` is this one. That is a real read of a
 *   documented endpoint. Where no linked case has a proof, the region says the spans
 *   cannot be reached from here rather than inventing offsets.
 *
 *   The chain of custody renders the steps the payload actually stamps. The per-step
 *   timestamps the design shows are trace spans, not artifact columns, so the region
 *   links to the trace instead of printing times that no row holds.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly params: Promise<{ artifactId: string }>;
}

export default async function ArtifactPage({ params }: PageProps) {
  const { artifactId } = await params;
  const [me, artifact] = await Promise.all([loadMe(), getArtifact(artifactId)]);
  const timeZone = timeZoneOf(me);

  if (!artifact.ok) {
    return (
      <ErrorState
        heading="We could not read this artifact."
        detail={`GET ${artifact.path} returned ${artifact.status} ${artifact.code}`}
        traceId={artifact.traceId}
      />
    );
  }

  const data = artifact.data;

  const proofs = await Promise.all(data.linked_cases.map((c) => getStateProof(c.case_id)));
  const readableProofs = proofs.filter((p) => p.ok);
  const spans: readonly EvidenceSource[] = readableProofs
    .flatMap((p) => (p.ok ? p.data.beliefs : []))
    .flatMap((belief) => belief.grounding)
    .map((edge) => edge.source)
    /* `exact_text`, not `evidence_id`: `ClaimSource` declares `evidence_id` as well, so
       the obvious test would pull every counterparty claim in as an extracted span. */
    .filter((source): source is EvidenceSource => "exact_text" in source)
    .filter((source) => source.artifact_id === data.artifact_id);

  const uniqueSpans = [...new Map(spans.map((s) => [s.evidence_id, s])).values()];

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <div>
          <h1 className="pv-display">
            {data.filename ?? <Absent describe="this artifact carries no filename" />}
          </h1>
          <p className="pv-mono">
            immutable · {data.size_bytes.toLocaleString("en-GB")} bytes · {data.mime_type}
          </p>
        </div>
        <p className="pv-meta-row">
          <span className="pv-chip" data-status={data.parser_status}>
            {data.parser_status}
          </span>
          {data.trace_id === null ? null : (
            <Link className="pv-button" href={`/judge?trace_id=${data.trace_id}`}>
              Open the trace
            </Link>
          )}
        </p>
      </header>

      <div
        className="pv-grid"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(22rem, 1fr))" }}
      >
        <section className="pv-card" aria-labelledby="pv-render-heading">
          <div className="pv-card-pad pv-section-heading">
            <h2 className="pv-label" id="pv-render-heading">
              The stored bytes
            </h2>
            <p className="pv-label">Rendered from storage, never re-generated</p>
          </div>
          <div className="pv-card-pad">
            {data.download_url === null ? (
              <div className="pv-state" data-kind="EMPTY">
                <p className="pv-title">This deployment did not issue a download link.</p>
                <p className="pv-prose">
                  Section 8.20 returns `download_url` as a short-lived pre-signed URL, and it may be
                  null. Nothing is shown in its place. A grey placeholder here would be
                  indistinguishable from a document that failed to load, and the two are different
                  facts.
                </p>
              </div>
            ) : (
              <p>
                <a className="pv-button" href={data.download_url} rel="noreferrer">
                  Open the stored bytes
                </a>
                <span
                  className="pv-label"
                  style={{ display: "block", marginTop: "var(--pv-space-2)" }}
                >
                  link expires{" "}
                  {data.download_url_expires_at === null
                    ? "at an unstated time"
                    : formatInstantOrRaw(data.download_url_expires_at, timeZone)}
                </span>
              </p>
            )}

            <div className="pv-inset" style={{ marginTop: "var(--pv-space-4)" }}>
              <p className="pv-label">Content blocks the parser found</p>
              {data.content_blocks_summary === undefined ||
              data.content_blocks_summary.length === 0 ? (
                <p className="pv-mono">
                  <Absent describe="this endpoint returned no content block summary" />
                </p>
              ) : (
                <ul className="pv-mono">
                  {data.content_blocks_summary.map((block) => (
                    <li key={block.block_id} data-block-id={block.block_id}>
                      {block.kind} · {block.char_count} characters
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </section>

        <div className="pv-stack">
          <section className="pv-card pv-card-pad" aria-labelledby="pv-bytes-heading">
            <h2 className="pv-label" id="pv-bytes-heading">
              Bytes
            </h2>
            <p
              className="pv-mono"
              style={{ wordBreak: "break-all", marginTop: "var(--pv-space-2)" }}
            >
              sha256 {data.content_sha256}
            </p>
            <p className="pv-mono">
              size {data.size_bytes.toLocaleString("en-GB")} bytes · parser {data.parser_version}
            </p>
            <p className="pv-mono">
              from{" "}
              {data.sender_display ?? <Absent describe="no sender recorded on this artifact" />}
            </p>
            <p className="pv-mono">
              to{" "}
              {data.recipient_display ?? (
                <Absent describe="no recipient recorded on this artifact" />
              )}
            </p>
            <p className="pv-mono">
              subject {data.subject ?? <Absent describe="this artifact carries no subject line" />}
            </p>
            <div style={{ marginTop: "var(--pv-space-3)" }}>
              <TimePair
                timeZone={timeZone}
                validFrom={data.event_time}
                recordedAt={data.received_at}
                recordVerb="RECEIVED"
              />
            </div>
          </section>

          <section className="pv-card pv-card-pad" aria-labelledby="pv-custody-heading">
            <h2 className="pv-label" id="pv-custody-heading">
              Chain of custody
            </h2>
            <ol className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
              <li>
                received ·{" "}
                {formatInstant(data.received_at, timeZone) ?? (
                  <Absent describe="receipt time not returned" />
                )}
              </li>
              <li>hashed · sha256 computed over the raw bytes</li>
              <li>stored · write-once object</li>
              <li>
                parsed · {data.parser_status} by {data.parser_version}
              </li>
              <li>
                extracted · {data.evidence_item_count} evidence item
                {data.evidence_item_count === 1 ? "" : "s"} admitted
              </li>
            </ol>
            <p className="pv-label" style={{ marginTop: "var(--pv-space-3)" }}>
              Only the first step carries a timestamp on this endpoint. The rest are trace spans;
              the trace holds their times.
            </p>
            {Object.keys(data.parser_metadata).length > 0 ? (
              <ul className="pv-mono" style={{ marginTop: "var(--pv-space-3)" }}>
                {Object.entries(data.parser_metadata).map(([key, value]) => (
                  <li key={key} data-parser-metadata={key}>
                    {key}={typeof value === "object" ? JSON.stringify(value) : String(value)}
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        </div>
      </div>

      <section aria-labelledby="pv-spans-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-spans-heading">
            Extracted spans
          </h2>
          <p className="pv-label">
            {uniqueSpans.length} reachable through the linked cases · {data.evidence_item_count}{" "}
            admitted in total
          </p>
        </div>

        {uniqueSpans.length === 0 ? (
          <p className="pv-prose">
            {data.linked_cases.length === 0
              ? "This artifact is linked to no case, so no state proof reaches its evidence. The spans exist on the evidence rows; there is no endpoint on this build that lists them for an artifact directly."
              : "No state proof for the linked cases returned evidence from this artifact. Nothing is shown rather than an approximation."}
          </p>
        ) : (
          <ul className="pv-stack-tight">
            {uniqueSpans.map((span) => (
              <li
                className="pv-card pv-card-pad"
                key={span.evidence_id}
                data-evidence-id={span.evidence_id}
              >
                <blockquote className="pv-quote">{span.exact_text}</blockquote>
                <div className="pv-meta-row">
                  <span className="pv-mono">{span.evidence_type}</span>
                  <span className="pv-mono">
                    {span.source_locator === null ? (
                      <Absent describe="no character offsets recorded for this span" />
                    ) : (
                      `chars ${span.source_locator.char_start} to ${span.source_locator.char_end}`
                    )}
                  </span>
                  <span className="pv-mono">
                    extraction_confidence={span.extraction_confidence} · source_authority=
                    {span.source_authority}
                  </span>
                  <RetractionBadge status={span.retraction_status} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="pv-card pv-card-pad" aria-labelledby="pv-retraction-heading">
        <h2 className="pv-label" id="pv-retraction-heading">
          Retraction
        </h2>
        <p className="pv-prose">
          Retracting excludes an artifact from future evaluation. It is never deleted, and every
          belief it once grounded keeps the record of it.
        </p>
        <p className="pv-label" style={{ marginTop: "var(--pv-space-3)" }}>
          No control is offered here. Retraction is a mutation this build cannot perform, and a
          button that did nothing would be worse than its absence.
        </p>
      </section>

      {data.linked_cases.length > 0 ? (
        <section aria-labelledby="pv-linked-heading">
          <div className="pv-section-heading">
            <h2 className="pv-label" id="pv-linked-heading">
              Linked cases
            </h2>
          </div>
          <ul className="pv-stack-tight">
            {data.linked_cases.map((linked) => (
              <li className="pv-ledger-line" key={linked.case_id}>
                <span>{linked.title}</span>
                <Link className="pv-button" href={`/cases/${linked.case_id}`}>
                  Open case
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
