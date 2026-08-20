import Link from "next/link";
import { EmptyState, ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { getArtifacts, getIngestAlias } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { abbreviateHash, formatDate, formatInstantOrRaw } from "@/lib/format";

/**
 * S05 -- the intake gateway.
 *
 * `alias_display` may legitimately be null: the stored value is an HMAC, and a deployment
 * that does not keep the reversible display column has nothing to show. That case renders
 * "rotate to reveal" rather than a plausible-looking address, because an address that
 * does not work is worse than no address.
 *
 * The recent-artifacts table carries both clocks per row. VALID TIME is the period the
 * document is about; RECORD TIME is when the bytes arrived. The June invoice that arrives
 * in September is unremarkable on one clock and impossible on the other, and only the pair
 * makes that visible.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function IngestPage({ searchParams }: PageProps) {
  const query = await searchParams;
  const focusArtifact = typeof query["artifact_id"] === "string" ? query["artifact_id"] : null;

  const [me, alias, artifacts] = await Promise.all([loadMe(), getIngestAlias(), getArtifacts()]);
  const timeZone = timeZoneOf(me);
  const uploadEnabled = me.ok ? me.data.feature_flags.upload_ingest_enabled === true : false;
  const sesEnabled = me.ok ? me.data.feature_flags.ses_inbound_enabled === true : false;

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Intake gateway</h1>
        <p className="pv-label">
          {artifacts.ok
            ? `${artifacts.data.items.length} artifacts listed`
            : "artifact list unread"}
        </p>
      </header>

      <div className="pv-grid" style={{ gridTemplateColumns: "3fr 2fr" }}>
        <section className="pv-state" data-kind="EMPTY" aria-labelledby="pv-upload-heading">
          <h2 className="pv-title" id="pv-upload-heading">
            Drop a bill, letter, receipt or screenshot here.
          </h2>
          <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
            PDF · PNG · JPG · EML · TXT · bytes are stored exactly as received
          </p>
          <p style={{ marginTop: "var(--pv-space-4)" }}>
            <button type="button" className="pv-button" disabled={!uploadEnabled}>
              Choose a file
            </button>
          </p>
          <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
            {uploadEnabled
              ? "Upload is enabled for this account. It runs through a pre-signed URL scoped to one key the server chooses, then a completion call. This build performs reads only, so the control is inert."
              : "Upload is disabled for this account by feature flag upload_ingest_enabled."}
          </p>
        </section>

        <section className="pv-card pv-card-pad" aria-labelledby="pv-alias-heading">
          <h2 className="pv-label" id="pv-alias-heading">
            Your inbound address
          </h2>
          {!alias.ok ? (
            <ErrorState
              heading="Address unreadable."
              detail={`GET ${alias.path} returned ${alias.status} ${alias.code}`}
            />
          ) : alias.data.alias_display === null ? (
            <>
              <p className="pv-mono">
                <Absent describe="this deployment stores only the HMAC of the alias" />
              </p>
              <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                Rotate to reveal. The stored value is a hash, not the token, so the address cannot
                be read back.
              </p>
            </>
          ) : (
            <>
              <p className="pv-mono" data-ingest-alias="true">
                {alias.data.alias_display}
              </p>
              <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                The address is opaque on purpose. It carries no name, no address and no account
                number, so forwarding a bill never discloses who you are to anyone who reads the
                header.
              </p>
              <p className="pv-mono">
                status={alias.data.status} · artifacts_received={alias.data.artifacts_received} ·
                last_received_at=
                {alias.data.last_received_at === null
                  ? "never"
                  : formatInstantOrRaw(alias.data.last_received_at, timeZone)}
              </p>
              {sesEnabled ? null : (
                <p className="pv-label">
                  Inbound mail is not being accepted: ses_inbound_enabled is false.
                </p>
              )}
            </>
          )}
        </section>
      </div>

      <section aria-labelledby="pv-recent-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-recent-heading">
            Recent artifacts
          </h2>
          <p className="pv-label">Bytes are immutable · both clocks are shown</p>
        </div>

        {!artifacts.ok ? (
          <ErrorState
            heading="We could not read your artifacts."
            detail={`GET ${artifacts.path} returned ${artifacts.status} ${artifacts.code}`}
          />
        ) : artifacts.data.items.length === 0 ? (
          <EmptyState heading="Nothing has been added to the record yet." />
        ) : (
          <div className="pv-table-scroll">
            <table className="pv-table">
              <thead>
                <tr>
                  <th scope="col">File</th>
                  <th scope="col">Content hash</th>
                  <th scope="col" className="pv-num">
                    Bytes
                  </th>
                  <th scope="col" style={{ color: "var(--pv-valid-time)" }}>
                    Valid time
                  </th>
                  <th scope="col" style={{ color: "var(--pv-record-time)" }}>
                    Record time
                  </th>
                  <th scope="col">Parser</th>
                </tr>
              </thead>
              <tbody>
                {artifacts.data.items.map((artifact) => (
                  <tr
                    key={artifact.artifact_id}
                    data-artifact-id={artifact.artifact_id}
                    style={
                      focusArtifact === artifact.artifact_id
                        ? { background: "var(--pv-surface-band)" }
                        : undefined
                    }
                  >
                    <td>
                      <Link href={`/artifacts/${artifact.artifact_id}`}>
                        {artifact.filename ?? artifact.artifact_id}
                      </Link>
                    </td>
                    <td>{abbreviateHash(artifact.content_sha256)}</td>
                    <td className="pv-num">{artifact.size_bytes.toLocaleString("en-GB")}</td>
                    <td>
                      {artifact.event_time === null ? (
                        <Absent describe="no event time was extracted for this artifact" />
                      ) : (
                        formatDate(artifact.event_time, timeZone)
                      )}
                    </td>
                    <td>{formatInstantOrRaw(artifact.received_at, timeZone)}</td>
                    <td>{artifact.parser_status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="pv-card pv-card-pad">
        <p className="pv-label">Pipeline · what happens to the bytes</p>
        <ol className="pv-grid pv-grid-4" style={{ marginTop: "var(--pv-space-3)" }}>
          {(
            [
              ["1 Hashing", "SHA-256 over the raw bytes, before anything else touches them."],
              ["2 Byte storage", "Immutable object write. The original is never modified."],
              ["3 Extraction", "Spans and embeddings. Every span keeps its character offsets."],
              ["4 Predicate evaluation", "The deterministic Kernel decides what changes."],
            ] as const
          ).map(([step, detail]) => (
            <li key={step}>
              <p className="pv-label">{step}</p>
              <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                {detail}
              </p>
            </li>
          ))}
        </ol>
        <p
          className="pv-prose"
          style={{ fontSize: "var(--pv-size-body)", marginTop: "var(--pv-space-3)" }}
        >
          Uploading the same bill twice is an informative outcome, not an error. Duplicate bytes
          hash to the same value and never create a duplicate obligation.
        </p>
      </section>
    </div>
  );
}
