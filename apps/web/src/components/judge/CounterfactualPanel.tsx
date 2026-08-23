import type { CounterfactualArm, CounterfactualResponse, ParityCheckKey } from "@/lib/api/contract";
import { PARITY_CHECK_KEYS } from "@/lib/api/contract";
import { Absent } from "@/components/primitives/Absent";

/**
 * S12 -- the counterfactual, and the parity render gate.
 *
 * The gate is the point of the whole panel. `parity.all_equal === false` means the two
 * output columns are **not rendered** and a failure banner replaces them. That is
 * normative in `CANONICAL_DECISIONS.md` and in section 8.31, and the reasoning is worth
 * restating: a counterfactual that cannot prove the two runs were identical in every
 * respect except memory invites exactly the accusation it exists to defeat. Better to
 * show nothing than to show two columns whose difference might be down to a different
 * model.
 *
 * Only the four permitted differences are rendered: `retrieval_enabled`,
 * `canonical_memory_enabled`, `corpus_size_visible`, and the resulting `output`. The
 * returned design carries five further comparison rows -- CONTRADICTION DETECTED, CASE
 * STATE AFTER, BELIEF ON BALANCE, ACTION PROPOSED, EFFECT ON YOUR MONEY. Three of those
 * are fields of `output`, so they are rendered from it. The other two are not:
 * "EFFECT ON YOUR MONEY" has no API field and would require client-side arithmetic over
 * the outstanding total, and the design's MEMORY OFF cell "No case exists" asserts more
 * than the experiment establishes -- MEMORY OFF receives empty retrieval, not a world in
 * which the case was never opened. Both are reported rather than invented.
 *
 * Header copy comes from `memory_on.strategy`, never a client constant. Under
 * `REPLAY_COMMITTED` the MEMORY ON column is the already-committed production run, and
 * the UI must not claim it ran just now.
 */

const PARITY_LABEL: Record<ParityCheckKey, string> = {
  artifact_id: "Artifact id",
  artifact_sha256: "Content hash",
  model_id: "Model id",
  prompt_version: "Prompt version",
  graph_version: "Graph version",
  decode_params_sha256: "Decode parameters",
};

/** Strategy copy. Each entry describes what the MEMORY ON column actually is. */
const STRATEGY_COPY: Readonly<Record<string, string>> = {
  REPLAY_COMMITTED:
    "The MEMORY ON column is the production run that was already committed. It is being replayed, not re-executed.",
  RERUN_LIVE: "Both columns were executed for this comparison.",
};

function ParityGate({ parity }: { readonly parity: CounterfactualResponse["parity"] }) {
  return (
    <section aria-labelledby="pv-parity-heading">
      <div className="pv-section-heading">
        <h3 className="pv-label" id="pv-parity-heading">
          Pre-flight parity gate · {PARITY_CHECK_KEYS.length} checks
        </h3>
        <p className="pv-mono" data-parity-all-equal={String(parity.all_equal)}>
          all_equal={String(parity.all_equal)}
        </p>
      </div>
      <div className="pv-parity-grid" style={{ marginTop: "var(--pv-space-3)" }}>
        {PARITY_CHECK_KEYS.map((key) => {
          const entry = parity[key];
          return (
            <div className="pv-parity-cell" key={key} data-parity-check={key}>
              <span>
                <span className="pv-label">{PARITY_LABEL[key]}</span>
                <span className="pv-mono" style={{ display: "block" }}>
                  {entry.equal ? entry.on : `off=${entry.off} on=${entry.on}`}
                </span>
              </span>
              <span className="pv-parity-verdict" data-equal={String(entry.equal)}>
                {entry.equal ? "MATCH" : "DIFFERS"}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ArmCell({ arm }: { readonly arm: CounterfactualArm }) {
  return (
    <>
      <p className="pv-label">{arm.mode}</p>
      <p className="pv-mono">
        model_id={arm.model_id} · duration_ms={arm.duration_ms}
      </p>
      {arm.error ? (
        <p className="pv-mono" style={{ color: "var(--pv-conflict)" }}>
          error={arm.error.code}: {arm.error.message}
        </p>
      ) : null}
    </>
  );
}

function OutputRows({ cf }: { readonly cf: CounterfactualResponse }) {
  const rows: readonly (readonly [string, string, string])[] = [
    [
      "Retrieval enabled",
      String(cf.memory_off.retrieval_enabled),
      String(cf.memory_on.retrieval_enabled),
    ],
    [
      "Canonical memory enabled",
      String(cf.memory_off.canonical_memory_enabled),
      String(cf.memory_on.canonical_memory_enabled),
    ],
    [
      "Corpus visible",
      String(cf.memory_off.corpus_size_visible),
      String(cf.memory_on.corpus_size_visible),
    ],
    ["Reading of the artifact", cf.memory_off.output.headline, cf.memory_on.output.headline],
    ["Classification", cf.memory_off.output.classification, cf.memory_on.output.classification],
    [
      "Conflicts detected",
      String(cf.memory_off.output.conflicts_detected),
      String(cf.memory_on.output.conflicts_detected),
    ],
    [
      "Recommended action",
      cf.memory_off.output.recommended_action,
      cf.memory_on.output.recommended_action,
    ],
    [
      "Case linked",
      cf.memory_off.output.case_linked === null
        ? "no case linked"
        : `${cf.memory_off.output.case_linked.title} · ${cf.memory_off.output.case_linked.status_before} to ${cf.memory_off.output.case_linked.status_after}`,
      cf.memory_on.output.case_linked === null
        ? "no case linked"
        : `${cf.memory_on.output.case_linked.title} · ${cf.memory_on.output.case_linked.status_before} to ${cf.memory_on.output.case_linked.status_after}`,
    ],
  ];

  return (
    <>
      {rows.map(([label, off, on]) => (
        <div style={{ display: "contents" }} key={label}>
          <div className="pv-cf-cell" data-column="label">
            <span className="pv-label">{label}</span>
          </div>
          <div className="pv-cf-cell" data-column="off">
            <span className="pv-mono">{off}</span>
          </div>
          <div className="pv-cf-cell" data-column="on">
            <span className="pv-mono">{on}</span>
          </div>
        </div>
      ))}
    </>
  );
}

export function CounterfactualPanel({ cf }: { readonly cf: CounterfactualResponse }) {
  const strategy = cf.memory_on.strategy;
  const strategyCopy =
    strategy === undefined ? null : (STRATEGY_COPY[strategy] ?? `Strategy: ${strategy}.`);

  return (
    <div className="pv-stack" data-counterfactual-id={cf.counterfactual_id}>
      <header className="pv-section-heading">
        <div>
          <h2 className="pv-title">Counterfactual · the same bytes, with and without the record</h2>
          <p className="pv-mono">
            status={cf.status} · artifact_id={cf.artifact_id}
          </p>
        </div>
      </header>

      <p className="pv-prose">{cf.artifact_summary}</p>

      {strategyCopy === null ? (
        <p className="pv-prose">
          <Absent describe="memory_on.strategy was not returned, so the header copy cannot be selected" />
        </p>
      ) : (
        <p className="pv-prose" data-strategy={strategy}>
          {strategyCopy}
        </p>
      )}

      <ParityGate parity={cf.parity} />

      {cf.parity.all_equal ? (
        <section aria-labelledby="pv-cf-columns-heading">
          <h3 className="pv-sr-only" id="pv-cf-columns-heading">
            Memory off compared with memory on
          </h3>
          <div className="pv-cf-columns" data-columns-rendered="true">
            <div className="pv-cf-cell" data-column="label" />
            <div className="pv-cf-cell" data-column="off">
              <ArmCell arm={cf.memory_off} />
            </div>
            <div className="pv-cf-cell" data-column="on">
              <ArmCell arm={cf.memory_on} />
            </div>
            <OutputRows cf={cf} />
          </div>
        </section>
      ) : (
        /* The render gate. The columns are not rendered, and this replaces them. */
        <div className="pv-state" data-kind="ERROR" role="alert" data-columns-rendered="false">
          <p className="pv-title">Parity failed. The comparison is not shown.</p>
          <p className="pv-prose">
            The two runs did not match on every field that must be identical, so any difference
            between their outputs could be caused by something other than memory. Showing the
            columns anyway would invite exactly the objection this comparison exists to answer.
          </p>
          <ul className="pv-mono" style={{ marginTop: "var(--pv-space-3)" }}>
            {PARITY_CHECK_KEYS.filter((key) => !cf.parity[key].equal).map((key) => (
              <li key={key}>
                {key}: off={cf.parity[key].off} on={cf.parity[key].on}
              </li>
            ))}
          </ul>
        </div>
      )}

      <section aria-labelledby="pv-delta-heading">
        <div className="pv-section-heading">
          <h3 className="pv-label" id="pv-delta-heading">
            Delta ledger
          </h3>
        </div>
        <div className="pv-table-scroll">
          <table className="pv-table">
            <thead>
              <tr>
                <th scope="col">Measure</th>
                <th scope="col" className="pv-num">
                  Memory off
                </th>
                <th scope="col" className="pv-num">
                  Memory on
                </th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["Contradictions surfaced", cf.delta.conflicts_detected],
                  ["Cases reopened", cf.delta.cases_reopened],
                  ["Actions recommended", cf.delta.actions_recommended],
                  ["Evidence recalled (days)", cf.delta.evidence_recalled_days],
                ] as const
              ).map(([label, entry]) => (
                <tr key={label}>
                  <td>{label}</td>
                  <td className="pv-num">{entry.off}</td>
                  <td className="pv-num">{entry.on}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="pv-prose" style={{ marginTop: "var(--pv-space-2)" }}>
          {cf.delta.verdict}
        </p>
      </section>

      <section className="pv-card pv-card-pad" aria-labelledby="pv-safety-heading">
        <h3 className="pv-label" id="pv-safety-heading">
          Safety · what the counterfactual did not do
        </h3>
        <ul className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
          {Object.entries(cf.safety).map(([key, value]) => (
            <li key={key} data-safety-check={key}>
              {key}={String(value)}
            </li>
          ))}
        </ul>
        <p className="pv-prose" style={{ marginTop: "var(--pv-space-2)" }}>
          The safety block is part of the API response, not a claim made by this page. A reader can
          check it against the record by asking the database for the case revision before and after.
        </p>
      </section>
    </div>
  );
}
