import Link from "next/link";
import { ConflictPair, GroundingEdgeRow, LineageChain } from "@/components/proof/Grounding";
import { EmptyState, ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { CaseStatusBadge, EpistemicStatusChip, RevisionBadge } from "@/components/primitives/Chips";
import { getStateProof } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { formatInstantOrRaw, formatBeliefValue, formatMoney } from "@/lib/format";
import type { SupportRelation } from "@/lib/api/contract";

/**
 * S04 -- State Proof.
 *
 * The deterministic answer to "why does Provenance believe this?". `deterministic: true`
 * and `model_used: null` are rendered rather than assumed, because the claim that no
 * language model touched this page is only worth making if the page shows the field it
 * rests on.
 */

export const dynamic = "force-dynamic";

const RELATION_ORDER: readonly SupportRelation[] = ["SUPPORTS", "CONTRADICTS", "QUALIFIES"];

interface PageProps {
  readonly params: Promise<{ caseId: string }>;
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function StateProofPage({ params, searchParams }: PageProps) {
  const { caseId } = await params;
  const query = await searchParams;
  const includeRetracted = query["include_retracted"] === "true";
  const beliefFilter = query["belief_id"];
  const beliefId =
    beliefFilter === undefined
      ? undefined
      : Array.isArray(beliefFilter)
        ? beliefFilter
        : [beliefFilter];

  const [me, proof] = await Promise.all([
    loadMe(),
    getStateProof(caseId, { includeRetracted, beliefId }),
  ]);
  const timeZone = timeZoneOf(me);

  if (!proof.ok) {
    return (
      <ErrorState
        heading="We could not read the state proof for this case."
        detail={`GET ${proof.path} returned ${proof.status} ${proof.code}`}
        traceId={proof.traceId}
      />
    );
  }

  const data = proof.data;

  return (
    <div className="pv-stack">
      <div
        className="pv-card pv-card-pad"
        style={{ borderLeft: "var(--pv-accent-rule) solid var(--pv-kernel)" }}
      >
        <p className="pv-label" style={{ color: "var(--pv-kernel)" }}>
          Deterministic page
        </p>
        <p className="pv-prose">
          Computed by database query. No language model was involved in producing this page.
        </p>
        <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
          deterministic={String(data.deterministic)} model_used={data.model_used ?? "null"}{" "}
          schema_version={data.schema_version}
        </p>
      </div>

      <header className="pv-section-heading">
        <div>
          <p className="pv-label">1 · Case header and revision stamp</p>
          <h1 className="pv-title">State proof</h1>
        </div>
        <p className="pv-meta-row">
          <CaseStatusBadge status={data.case_status} />
          <RevisionBadge revision={data.case_revision} />
          <span className="pv-mono">stamped {formatInstantOrRaw(data.generated_at, timeZone)}</span>
        </p>
      </header>

      {data.integrity_warnings && data.integrity_warnings.length > 0 ? (
        <div className="pv-state" data-kind="ERROR" role="alert">
          <p className="pv-title">Integrity warning</p>
          <ul>
            {data.integrity_warnings.map((warning) => (
              <li className="pv-mono" key={warning.code + (warning.belief_id ?? "")}>
                {warning.code}: {warning.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <section aria-labelledby="pv-beliefs-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-beliefs-heading">
            2 · Beliefs docket
          </h2>
        </div>

        {data.beliefs.length === 0 ? (
          <EmptyState heading="No belief is currently held on this case.">
            <p>
              The case exists and carries commitments, but no predicate has yet been raised to a
              canonical belief.
            </p>
          </EmptyState>
        ) : (
          <div className="pv-stack" style={{ marginTop: "var(--pv-space-4)" }}>
            {data.beliefs.map((belief) => {
              return (
                <article
                  className="pv-card pv-card-pad"
                  key={belief.belief_id}
                  id={`belief-${belief.belief_id}`}
                  data-belief-id={belief.belief_id}
                >
                  <div className="pv-meta-row">
                    <EpistemicStatusChip status={belief.current_version.epistemic_status} />
                    <span className="pv-figure">
                      {/* One formatter, shared with the relationship file and the
                          lineage rail. This branch stringified anything that was
                          not money, so a state belief rendered raw JSON here. */}
                      {formatBeliefValue(belief.current_version.value_json) ?? (
                        <Absent describe="this belief version carries no value payload" />
                      )}
                    </span>
                    <span className="pv-mono">
                      {belief.predicate} · v{belief.current_version.version_no} · belief_confidence=
                      {belief.current_version.belief_confidence ?? (
                        <Absent describe="no confidence returned on this belief version" />
                      )}
                    </span>
                  </div>

                  <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
                    grounded={String(belief.grounded)} · subject_type={belief.subject_type}
                  </p>

                  <h3 className="pv-label" style={{ marginTop: "var(--pv-space-5)" }}>
                    3 · Grounding · what this rests on
                  </h3>
                  {RELATION_ORDER.map((relation) => {
                    const edges = belief.grounding.filter((edge) => edge.relation === relation);
                    return (
                      <section key={relation} style={{ marginTop: "var(--pv-space-3)" }}>
                        <p className="pv-label">
                          {relation} ({edges.length})
                        </p>
                        {edges.length === 0 ? (
                          <p className="pv-mono">none</p>
                        ) : (
                          <ul>
                            {edges.map((edge) => (
                              <GroundingEdgeRow
                                edge={edge}
                                key={edge.support_id}
                                timeZone={timeZone}
                              />
                            ))}
                          </ul>
                        )}
                      </section>
                    );
                  })}

                  <h3 className="pv-label" style={{ marginTop: "var(--pv-space-5)" }}>
                    4 · Lineage · how this changed
                  </h3>
                  <div style={{ marginTop: "var(--pv-space-3)" }}>
                    <LineageChain belief={belief} timeZone={timeZone} />
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {data.conflicts.length > 0 ? (
        <section className="pv-stack-tight" aria-label="Conflicts">
          {data.conflicts.map((conflict) => (
            <ConflictPair conflict={conflict} key={conflict.conflict_id} timeZone={timeZone} />
          ))}
        </section>
      ) : null}

      {/* Rendered unconditionally, because the ordinals are hard-coded and
          dropping the section made the page read 1, 2, 3, 4, 6. A missing
          number reads as a section that failed to render -- the opposite of
          what this page is claiming -- and it contradicts the rule the rest of
          the build follows: absence is not emptiness, and it is marked rather
          than omitted. */}
      <section aria-labelledby="pv-derivations-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-derivations-heading">
            5 · Arithmetic derivations
          </h2>
        </div>
        {data.derivations.length === 0 ? (
          <p className="pv-prose">
            <Absent describe="no arithmetic derivation is recorded for this case" />
          </p>
        ) : (
          <div className="pv-table-scroll">
            <table className="pv-table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Expression</th>
                  <th scope="col">Inputs</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {data.derivations.map((derivation) => (
                  <tr key={`${derivation.name}-${derivation.target.id}`}>
                    <td>{derivation.name}</td>
                    <td>{derivation.expression}</td>
                    <td>
                      {Object.entries(derivation.inputs).map(([key, money]) => (
                        <span key={key} style={{ display: "block" }}>
                          {key}={formatMoney(money)}
                        </span>
                      ))}
                    </td>
                    <td className="pv-num">{formatMoney(derivation.result)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="pv-card pv-card-pad" aria-labelledby="pv-excluded-heading">
        <h2 className="pv-label" id="pv-excluded-heading">
          6 · Retraction and exclusion integrity
        </h2>
        <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
          retraction_filter_applied={String(data.excluded.retraction_filter_applied)} ·
          retracted_evidence_count={data.excluded.retracted_evidence_count} ·
          superseded_belief_versions_hidden={data.excluded.superseded_belief_versions_hidden}
        </p>
        <p className="pv-prose" style={{ marginTop: "var(--pv-space-2)" }}>
          Retracted sources keep their rows, their bytes, and their embeddings. They are excluded
          from retrieval and from the grounding computation, and they stay visible in history.
        </p>
        <p style={{ marginTop: "var(--pv-space-3)" }}>
          <Link
            className="pv-button"
            href={`/cases/${caseId}/proof?include_retracted=${includeRetracted ? "false" : "true"}`}
          >
            {includeRetracted ? "Hide retracted sources" : "Show retracted sources"}
          </Link>
        </p>
      </section>

      <section aria-labelledby="pv-relying-heading">
        <div className="pv-section-heading">
          <h2 className="pv-label" id="pv-relying-heading">
            Actions relying on this state
          </h2>
        </div>
        {data.actions_relying_on_this_state.length === 0 ? (
          <p className="pv-prose">
            Nothing is prepared against this revision. <Absent describe="no action intents" />
          </p>
        ) : (
          <ul className="pv-stack-tight" style={{ marginTop: "var(--pv-space-3)" }}>
            {data.actions_relying_on_this_state.map((action) => (
              <li className="pv-card pv-card-pad" key={action.action_intent_id}>
                <p className="pv-mono">
                  {action.action_type} · status={action.status} · basis_case_revision=
                  {action.basis_case_revision} · still_current={String(action.still_current)}
                </p>
                <p style={{ marginTop: "var(--pv-space-2)" }}>
                  <Link className="pv-button" href={`/actions/${action.action_intent_id}`}>
                    Review the draft
                  </Link>
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p>
        <Link className="pv-button" href={`/cases/${caseId}`}>
          Back to the case
        </Link>
      </p>
    </div>
  );
}
