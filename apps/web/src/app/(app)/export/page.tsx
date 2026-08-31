import { ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { buildSearchCorpus } from "@/lib/corpus";
import type { SearchCategory } from "@/lib/corpus";

/**
 * S10 -- audit export.
 *
 * The design shows a scope chooser, a contents list with a record count against each row,
 * and a GENERATE PACKAGE control. Section 8 of the API spec defines no export endpoint, so
 * this screen does two things and refuses to do a third.
 *
 * It renders the contents list with counts that were actually read. Each row says which
 * endpoint produced its number, and a row whose number cannot be reached renders an
 * explicit absence rather than a zero: "we could not count your claims" and "you have no
 * claims" are different statements about a person's own record, and a zero says the second
 * while meaning the first.
 *
 * It does not offer a generate control. A button that produced nothing, or produced a file
 * assembled in the browser from what this session happened to have read, would be the
 * clearest possible instance of the failure this application is built to avoid: a package
 * that looks like a signed export of the record and is a screenshot of a browser tab.
 *
 * The counts are lower bounds wherever a list endpoint reported a further page, and the
 * screen says "at least" in that case rather than printing a total it did not count.
 */

export const dynamic = "force-dynamic";

interface ContentRow {
  readonly label: string;
  readonly description: string;
  readonly count: number | null;
  readonly unit: string;
  readonly origin: string;
}

export default async function AuditExportPage() {
  const corpus = await buildSearchCorpus();

  if (corpus.endpointsRead.length === 0) {
    return (
      <ErrorState
        heading="We could not read anything to describe."
        detail={corpus.endpointsFailed.map((f) => `${f.path} ${f.code}`).join("; ")}
      >
        <p>
          No endpoint answered. A contents list of zeros here would describe an empty record, which
          is not what happened.
        </p>
      </ErrorState>
    );
  }

  const count = (category: SearchCategory): number =>
    corpus.records.filter((r) => r.category === category).length;

  const failedPaths = new Set(corpus.endpointsFailed.map((f) => f.path));
  const reachable = (fragment: string): boolean =>
    ![...failedPaths].some((path) => path.includes(fragment));

  const rows: readonly ContentRow[] = [
    {
      label: "Artifacts",
      description: "raw bytes and their hashes",
      count: reachable("/v1/artifacts") ? count("ARTIFACT") : null,
      unit: "records",
      origin: "/v1/artifacts",
    },
    {
      label: "Evidence",
      description: "spans, offsets and extraction confidences",
      count: corpus.proofCount > 0 ? count("EVIDENCE") : null,
      unit: "records",
      origin: "/v1/cases/{id}/state-proof",
    },
    {
      label: "Claims",
      description: "actor assertions with their authority scores",
      count: corpus.proofCount > 0 ? count("CLAIM") : null,
      unit: "records",
      origin: "/v1/cases/{id}/state-proof",
    },
    {
      label: "Beliefs",
      description: "every version, including superseded ones",
      count: corpus.proofCount > 0 ? count("BELIEF") : null,
      unit: `records, ${corpus.beliefVersionCount} versions`,
      origin: "/v1/cases/{id}/state-proof",
    },
    {
      label: "Commitments",
      description: "arithmetic and fulfilments",
      count: corpus.proofCount > 0 ? corpus.commitmentCount : null,
      unit: "records",
      origin: "/v1/cases/{id}/state-proof",
    },
    {
      label: "Docket",
      description: "the append-only event stream",
      count: count("DOCKET") > 0 || corpus.proofCount > 0 ? count("DOCKET") : null,
      unit: "entries",
      origin: "/v1/cases/{id}/timeline",
    },
    {
      label: "State proofs",
      description: "one document per case revision",
      count: corpus.proofCount,
      unit: "documents",
      origin: "/v1/cases/{id}/state-proof",
    },
  ];

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Audit export</h1>
        <p className="pv-label">Deterministic · no model involved</p>
      </header>

      <p className="pv-prose">
        An audit package is a signed, self-verifying copy of what your record holds: the raw bytes,
        every span and claim with its scores, every belief version including the superseded ones,
        and the docket that produced them.
      </p>

      <section aria-labelledby="pv-export-contents-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-export-contents-heading">
            What a package would contain
          </h2>
          <p className="pv-label">
            {corpus.truncated ? "counts are lower bounds" : "counted at read time"}
          </p>
        </div>

        <div className="pv-table-scroll">
          <table className="pv-table">
            <caption className="pv-sr-only">
              Contents of an audit package, with the endpoint each count was read from
            </caption>
            <thead>
              <tr>
                <th scope="col">Contents</th>
                <th scope="col" className="pv-num">
                  Counted
                </th>
                <th scope="col">Read from</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label} data-export-row={row.label}>
                  <td>
                    <span className="pv-title" style={{ fontSize: "var(--pv-size-prose)" }}>
                      {row.label}
                    </span>
                    <span className="pv-label" style={{ display: "block" }}>
                      {row.description}
                    </span>
                  </td>
                  <td className="pv-num pv-mono">
                    {row.count === null ? (
                      <Absent
                        reason="NO_ROW"
                        describe={`this count could not be read from ${row.origin}`}
                      />
                    ) : row.count === 0 ? (
                      /* `corpus.truncated` is one global flag, so a further page
                         on ANY endpoint prefixed EVERY row with "at least" --
                         including a row whose count is 0. "At least 0 records"
                         claims nothing. This file's own docstring warns against
                         exactly it: a zero that means "we could not count" reads
                         as "you have none". */
                      <Absent
                        reason="NO_ROW"
                        describe={`no ${row.unit} is reachable through ${row.origin} on this build`}
                      />
                    ) : (
                      `${corpus.truncated ? "at least " : ""}${row.count} ${row.unit}`
                    )}
                  </td>
                  <td className="pv-mono">{row.origin}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="pv-card pv-card-pad" aria-labelledby="pv-export-generate-heading">
        <h2 className="pv-label" id="pv-export-generate-heading">
          Generating a package
        </h2>
        <p className="pv-prose">
          There is no control here, and its absence is deliberate. The API contract this build
          renders against defines no export endpoint, so nothing on this page can produce a package
          whose manifest hash means anything.
        </p>
        <p className="pv-prose">
          A package assembled in the browser out of what this session happened to have read would
          carry a hash over a subset chosen by a page, not a signature over the record. It would be
          the wrong artifact wearing the right name, which is worse than having no button at all.
        </p>
        <p className="pv-label" style={{ marginTop: "var(--pv-space-3)" }}>
          {corpus.endpointsRead.length} endpoint
          {corpus.endpointsRead.length === 1 ? "" : "s"} read for the counts above
          {corpus.endpointsFailed.length > 0
            ? `; ${corpus.endpointsFailed.length} did not answer`
            : ""}
          .
        </p>
      </section>

      {corpus.endpointsFailed.length > 0 ? (
        <section className="pv-state" data-kind="ERROR" role="alert">
          <p className="pv-title">Part of your record could not be counted.</p>
          <ul className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
            {corpus.endpointsFailed.map((failure) => (
              <li key={`${failure.path}:${failure.code}`}>
                {failure.path} · {failure.code}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
