import Link from "next/link";
import { EmptyState, ErrorState } from "@/components/primitives/States";
import { buildSearchCorpus, matchRecords, SEARCH_CATEGORY_ORDER } from "@/lib/corpus";
import type { SearchCategory, SearchRecord } from "@/lib/corpus";

/**
 * S08 -- search.
 *
 * The screen states its own scope before it shows a result, because the gap between what
 * this is and what the design shows is the kind of gap a reader must not have to guess at.
 *
 * The design's search is a ranked corpus search over the reader's whole record. The API
 * contract this build renders against defines no search endpoint at all. So this is a
 * literal substring match over the records the documented endpoints returned, every result
 * carries the endpoint path it came from, and the header says how many endpoints were read
 * and how many failed. A reader who searches for something and finds nothing is told
 * whether that means "your record does not contain it" or "this screen could not see that
 * far", which are different facts and only one of them is about them.
 *
 * The query lives in the URL, so a result set is a link a judge can be handed.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function Result({ record }: { readonly record: SearchRecord }) {
  return (
    <li
      className="pv-attention-row"
      data-search-category={record.category}
      data-record-id={record.id}
    >
      <div className="pv-attention-body" data-attention="INFO">
        <p className="pv-prose" style={{ fontSize: "var(--pv-size-prose)" }}>
          {record.title}
        </p>
        <p className="pv-mono">{record.detail}</p>
        <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
          read from {record.origin}
        </p>
      </div>
      {record.href === null ? null : (
        <Link className="pv-button" href={record.href}>
          Open
        </Link>
      )}
    </li>
  );
}

export default async function SearchPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const raw = params["q"];
  const query = typeof raw === "string" ? raw : "";

  const corpus = await buildSearchCorpus();

  if (corpus.endpointsRead.length === 0) {
    return (
      <ErrorState
        heading="We could not read anything to search."
        detail={corpus.endpointsFailed.map((f) => `${f.path} ${f.code}`).join("; ")}
      >
        <p>
          No endpoint answered, so this screen has no corpus. An empty result list here would say
          your record is empty, which is a claim it cannot make.
        </p>
      </ErrorState>
    );
  }

  const matches = matchRecords(corpus.records, query);
  const byCategory = new Map<SearchCategory, SearchRecord[]>();
  for (const record of matches) {
    const bucket = byCategory.get(record.category);
    if (bucket === undefined) byCategory.set(record.category, [record]);
    else bucket.push(record);
  }

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Search</h1>
        <p className="pv-label">
          {query.trim() === ""
            ? `${corpus.records.length} records in view`
            : `${matches.length} match${matches.length === 1 ? "" : "es"} of ${corpus.records.length} records in view`}
        </p>
      </header>

      <form className="pv-card pv-card-pad" role="search" action="/search" method="get">
        <label className="pv-label" htmlFor="pv-search-input">
          Search your record
        </label>
        <div style={{ display: "flex", gap: "var(--pv-space-3)", marginTop: "var(--pv-space-2)" }}>
          <input
            className="pv-input"
            id="pv-search-input"
            name="q"
            type="search"
            defaultValue={query}
            autoComplete="off"
            placeholder="a counterparty, an amount, a file name, a reason code"
          />
          <button className="pv-button" data-emphasis="primary" type="submit">
            Search
          </button>
        </div>
      </form>

      <section className="pv-card pv-card-pad" aria-labelledby="pv-search-scope-heading">
        <h2 className="pv-label" id="pv-search-scope-heading">
          What this search is
        </h2>
        <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
          A literal, case-insensitive substring match over the records {corpus.endpointsRead.length}{" "}
          endpoint
          {corpus.endpointsRead.length === 1 ? "" : "s"} returned. It is not ranked, not semantic,
          and not a search of your whole corpus: the API contract this build renders against defines
          no search endpoint, so there is nothing to rank against. Every result below names the
          endpoint it was read from.
        </p>
        {corpus.truncated ? (
          <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
            At least one list endpoint reported a further page that was not fetched. Records beyond
            the first page cannot appear here, and their absence is not evidence that they do not
            exist.
          </p>
        ) : null}
        {corpus.endpointsFailed.length > 0 ? (
          <div className="pv-inset" style={{ marginTop: "var(--pv-space-3)" }}>
            <p className="pv-label">Endpoints that did not answer</p>
            <ul className="pv-mono">
              {corpus.endpointsFailed.map((failure) => (
                <li key={`${failure.path}:${failure.code}`}>
                  {failure.path} · {failure.code}
                </li>
              ))}
            </ul>
            <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
              Anything held only behind these is missing from the results.
            </p>
          </div>
        ) : null}
      </section>

      {query.trim() === "" ? (
        <EmptyState heading="Type something to search.">
          <p>
            Nothing is listed until you ask for it. A default result set would be a ranking this
            screen is not able to compute.
          </p>
        </EmptyState>
      ) : matches.length === 0 ? (
        <EmptyState heading="Nothing in view matches that.">
          <p>
            {corpus.records.length} records were searched, drawn from {corpus.endpointsRead.length}{" "}
            endpoint reads. That is not the whole of your record, so this is not proof that your
            record does not hold it.
          </p>
        </EmptyState>
      ) : (
        SEARCH_CATEGORY_ORDER.filter((category) => byCategory.has(category)).map((category) => {
          const bucket = byCategory.get(category) ?? [];
          return (
            <section key={category} aria-labelledby={`pv-search-${category}`}>
              <div className="pv-section-heading">
                <h2 className="pv-label" id={`pv-search-${category}`}>
                  {category}
                </h2>
                <p className="pv-label">
                  {bucket.length} record{bucket.length === 1 ? "" : "s"}
                </p>
              </div>
              <ul className="pv-stack-tight">
                {bucket.map((record) => (
                  <Result key={`${record.category}:${record.id}`} record={record} />
                ))}
              </ul>
            </section>
          );
        })
      )}
    </div>
  );
}
