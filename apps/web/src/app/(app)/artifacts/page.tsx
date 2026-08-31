import Link from "next/link";
import { EmptyState, ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { getArtifacts } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { abbreviateHash, formatDateOrRaw, formatInstant } from "@/lib/format";

/**
 * S06a -- the artifact index.
 *
 * Two clocks per row, labelled, as everywhere else: `event_time` is when the document was
 * about, `received_at` is when the system got it. The June invoice that arrives in
 * September is unremarkable on one clock and impossible on the other, and a table with a
 * single "date" column is the design decision that hides that.
 *
 * The content hash is shown head-and-tail rather than truncated silently, and the full
 * value is on the artifact's own page. A hash that has been shortened without saying so
 * cannot be checked against anything, which defeats its only purpose.
 */

export const dynamic = "force-dynamic";

/**
 * The document types a person actually forwarded or uploaded.
 *
 * The seeded corpus holds 18,000 `SEED_FIXTURE` decoys -- deliberately, because
 * retrieval that is never asked to discriminate proves nothing. But the index
 * sorts by `received_at DESC` and the decoy field runs later than the story
 * documents, so the unfiltered first page was 25 nameless fixtures and not one
 * of the bills this record is about, on the screen the navigation calls
 * Evidence.
 *
 * So the default view is the admitted documents. The decoys are not hidden --
 * the count and the link below say they are there and how to see them, and
 * `?all=1` shows the whole corpus. Filtering a view is a different act from
 * concealing rows, and the difference is whether the page says so.
 */
const ADMITTED_SOURCE_TYPES = ["EMAIL_INBOUND", "UPLOAD_PDF"] as const;

interface PageProps {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function ArtifactsPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const showAll = params["all"] === "1";
  const [me, artifacts] = await Promise.all([
    loadMe(),
    getArtifacts(showAll ? [] : [...ADMITTED_SOURCE_TYPES]),
  ]);
  const timeZone = timeZoneOf(me);

  if (!artifacts.ok) {
    return (
      <ErrorState
        heading="We could not read your artifacts."
        detail={`GET ${artifacts.path} returned ${artifacts.status} ${artifacts.code}`}
        traceId={artifacts.traceId}
      />
    );
  }

  const items = artifacts.data.items;
  const quarantined = items.filter((a) => a.parser_status === "FAILED").length;

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Artifacts</h1>
        <p className="pv-label">
          {/* "listed", not "stored". The footer on this same page says "More
              artifacts exist beyond this page", so "25 stored" was contradicted
              by its own screen. /ingest already words it as listed. */}
          {items.length} listed · {quarantined} unparsed
        </p>
      </header>

      <p className="pv-prose">
        Bytes are stored exactly as received and never rewritten. Everything the record holds about
        a document points back to one of these rows.
      </p>

      <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
        {showAll ? (
          <>
            Showing <strong>every</strong> artifact, including the 18,000 seeded decoys the
            retrieval evaluation scores against. They are near-neighbours of the real documents on
            purpose. <Link href="/artifacts">Show admitted documents only</Link>.
          </>
        ) : (
          <>
            Showing the documents that were forwarded or uploaded. The record also holds{" "}
            <strong>18,000 seeded decoys</strong>: adversarial near-misses that exist so retrieval
            has something to discriminate against. They are excluded here because they sort newer
            than the real documents and would fill this page.{" "}
            <Link href="/artifacts?all=1">Show every artifact</Link>.
          </>
        )}
      </p>

      {items.length === 0 ? (
        <EmptyState heading="Nothing has been admitted to your record.">
          <p>
            Forward a bill to your inbound address, or drop bytes into the intake gateway. Until
            then this is empty, and empty is the truth rather than a failure to load.
          </p>
        </EmptyState>
      ) : (
        <div className="pv-table-scroll">
          <table className="pv-table">
            <caption className="pv-sr-only">
              Artifacts, with valid time and record time as separate columns
            </caption>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Content hash</th>
                <th scope="col" className="pv-num">
                  Bytes
                </th>
                <th scope="col">Valid time</th>
                <th scope="col">Record time</th>
                <th scope="col">Parser</th>
                <th scope="col">
                  <span className="pv-sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((artifact) => (
                <tr key={artifact.artifact_id} data-artifact-id={artifact.artifact_id}>
                  <td>
                    <span className="pv-mono">
                      {artifact.filename ?? <Absent describe="this artifact carries no filename" />}
                    </span>
                    <span className="pv-label" style={{ display: "block" }}>
                      {artifact.source_type} · {artifact.mime_type}
                    </span>
                  </td>
                  <td className="pv-mono">{abbreviateHash(artifact.content_sha256)}</td>
                  <td className="pv-num pv-mono">{artifact.size_bytes.toLocaleString("en-GB")}</td>
                  <td className="pv-mono" data-clock="VALID">
                    {artifact.event_time === null ? (
                      <Absent describe="this artifact asserts no period it is about" />
                    ) : (
                      formatDateOrRaw(artifact.event_time, timeZone)
                    )}
                  </td>
                  <td className="pv-mono" data-clock="RECORD">
                    {formatInstant(artifact.received_at, timeZone) ?? (
                      <Absent describe="receipt time not returned" />
                    )}
                  </td>
                  <td>
                    <span className="pv-chip" data-status={artifact.parser_status}>
                      {artifact.parser_status}
                    </span>
                  </td>
                  <td>
                    <Link className="pv-button" href={`/artifacts/${artifact.artifact_id}`}>
                      Open
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {artifacts.data.page.has_more ? (
        <p className="pv-label">
          More artifacts exist beyond this page. The record is append-only; nothing here has been
          removed.
        </p>
      ) : null}
    </div>
  );
}
