import type { ClaimSource, EvidenceSource, StateProofResponse } from "@/lib/api/contract";
import { formatBeliefValue, formatDate, formatInstantOrRaw, formatMoney } from "@/lib/format";
import { Absent } from "@/components/primitives/Absent";
import { RelationLabel } from "@/components/primitives/Chips";

/**
 * The two panels the design puts opposite each other on a counterparty file: what they
 * told you, and what the record concluded.
 *
 * Keeping them adjacent is the whole argument of the screen. A counterparty assertion is
 * rendered as an assertion, attributed, scored, and dated; the canonical position is
 * rendered as a belief version with its confidence and its derivation. Neither is allowed
 * to look like the other, because "they said" and "it is so" are the two things this
 * product exists to keep apart.
 *
 * Both panels read from `GET /v1/cases/{id}/state-proof`, which is the only endpoint that
 * carries claims and belief versions. Nothing here is inferred from the relationship
 * payload, and where a proof was unreadable the panel says the record is partial rather
 * than rendering the part it managed to get as if it were the whole.
 */

function isClaim(source: EvidenceSource | ClaimSource): source is ClaimSource {
  return "claim_id" in source;
}

interface PanelProps {
  readonly proofs: readonly StateProofResponse[];
  readonly timeZone: string;
}

/** Formatted amount out of a claim's `object_json`, when it carries one. */
function claimAmount(claim: ClaimSource): string | null {
  const object = claim.object_json as {
    amount?: { currency?: string; amount?: string };
  };
  const money = object.amount;
  if (money?.currency === undefined || money.amount === undefined) return null;
  return formatMoney({ currency: money.currency, amount: money.amount });
}

export function CounterpartyAssertions({ proofs, timeZone }: PanelProps) {
  const claims = proofs
    .flatMap((proof) => proof.beliefs)
    .flatMap((belief) => belief.grounding)
    .filter((edge) => isClaim(edge.source))
    .filter((edge) => (edge.source as ClaimSource).actor_type === "COUNTERPARTY");

  return (
    <section aria-labelledby="pv-assertions-heading">
      <div className="pv-section-heading">
        <h2 className="pv-label" id="pv-assertions-heading">
          What they told you
        </h2>
        <p className="pv-label">
          {claims.length} assertion{claims.length === 1 ? "" : "s"}
        </p>
      </div>

      {claims.length === 0 ? (
        <p className="pv-prose">
          {proofs.length === 0 ? (
            <Absent describe="no state proof was read, so no counterparty assertion can be shown" />
          ) : (
            "No claim on this file is attributed to the counterparty. Every position on the record came from you or from an admitted document."
          )}
        </p>
      ) : (
        <ul className="pv-stack-tight">
          {claims.map((edge) => {
            const claim = edge.source as ClaimSource;
            const amount = claimAmount(claim);
            return (
              <li
                className="pv-card pv-card-pad pv-relation"
                key={claim.claim_id}
                data-relation={edge.relation}
                data-claim-id={claim.claim_id}
              >
                <p className="pv-label">[{claim.actor_type}: asserts]</p>
                <p className="pv-quote">
                  {claim.predicate}
                  {amount === null ? "" : ` = ${amount}`}
                </p>
                <div className="pv-meta-row">
                  <RelationLabel relation={edge.relation} />
                  <span className="pv-mono">
                    source_authority={claim.authority_score} · weight={edge.weight}
                  </span>
                  <span className="pv-mono">
                    {formatDate(claim.recorded_at, timeZone) ?? (
                      <Absent describe="record time not returned on this claim" />
                    )}
                  </span>
                </div>
                <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
                  An assertion, not a fact. It is held on the record and scored; it has not changed
                  the canonical position.
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function CanonicalPosition({ proofs, timeZone }: PanelProps) {
  const beliefs = proofs.flatMap((proof) => proof.beliefs);
  const derivations = proofs.flatMap((proof) => proof.derivations);

  return (
    <section aria-labelledby="pv-canonical-heading">
      <div className="pv-section-heading">
        <h2 className="pv-label" id="pv-canonical-heading">
          Canonical position
        </h2>
        <p className="pv-label">Kernel commits · binding</p>
      </div>

      {beliefs.length === 0 ? (
        <p className="pv-prose">
          <Absent describe="no belief was returned for the cases on this file" />
        </p>
      ) : (
        <ul className="pv-stack-tight">
          {beliefs.map((belief) => {
            const version = belief.current_version;
            // One formatter for every belief value. This used to narrow to a
            // money shape and JSON.stringify anything else, so a state belief
            // rendered {"state":"TERMINATED"} in the headline slot beside a
            // money belief rendering USD 1,800.00.
            const shown = formatBeliefValue(version.value_json);

            return (
              <li
                className="pv-card pv-card-pad"
                key={belief.belief_id}
                data-belief-id={belief.belief_id}
              >
                <p className="pv-figure">
                  {shown ?? (
                    <Absent
                      reason="NULL_COLUMN"
                      describe="this belief version carries no value payload"
                    />
                  )}
                </p>
                <p className="pv-mono">
                  {belief.predicate} · v{version.version_no} · {version.epistemic_status} ·
                  belief_confidence=
                  {version.belief_confidence ?? (
                    <Absent describe="no confidence returned on this version" />
                  )}
                </p>
                <p className="pv-mono">
                  grounded={String(belief.grounded)} · grounding edges {belief.grounding.length} ·
                  recorded{" "}
                  {version.recorded_at === undefined ? (
                    <Absent describe="record time not returned" />
                  ) : (
                    formatInstantOrRaw(version.recorded_at, timeZone)
                  )}
                </p>
              </li>
            );
          })}
        </ul>
      )}

      {derivations.length > 0 ? (
        <div className="pv-card pv-card-pad" style={{ marginTop: "var(--pv-space-4)" }}>
          <p className="pv-label">Arithmetic, as the Kernel computed it</p>
          <ul className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
            {derivations.map((derivation) => (
              <li key={derivation.name} data-derivation={derivation.name}>
                {derivation.name}: {derivation.expression} = {formatMoney(derivation.result)}
                {derivation.deterministic_derivation ? "" : " · not deterministic"}
              </li>
            ))}
          </ul>
          <p className="pv-label" style={{ marginTop: "var(--pv-space-2)" }}>
            The expression and the result both come from the proof. Nothing on this page performs
            arithmetic.
          </p>
        </div>
      ) : null}
    </section>
  );
}
