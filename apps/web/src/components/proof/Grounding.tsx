import type {
  Belief,
  BeliefVersion,
  ClaimSource,
  EvidenceSource,
  GroundingEdge,
  ProofConflict,
} from "@/lib/api/contract";
import { abbreviateHash, formatDateOrRaw, formatInstantOrRaw } from "@/lib/format";
import { Absent } from "@/components/primitives/Absent";
import { RelationLabel, RetractionBadge } from "@/components/primitives/Chips";

/**
 * Grounding and lineage.
 *
 * These are two different things and the vocabulary keeps them apart:
 *
 *   GROUNDING  the `belief_support` edges. What this rests on, right now.
 *   LINEAGE    the `belief_versions` supersession chain. How it came to say this.
 *
 * They render as two separate labelled regions under those two words, never merged. A
 * merged view reads as a single history and loses the distinction between "the evidence
 * beneath the current position" and "the positions we previously held".
 */

/*
 * `"evidence_id" in source` is the obvious test and it is wrong. `ClaimSource` declares
 * `evidence_id` too -- nullable, naming the evidence a claim was extracted from -- so the
 * check is true for both, and every counterparty claim would have rendered in the evidence
 * treatment: quoted as source text, with an authority score presented as an extraction
 * confidence. `exact_text` is on `EvidenceSource` alone.
 */
function isEvidence(source: EvidenceSource | ClaimSource): source is EvidenceSource {
  return "exact_text" in source;
}

function EvidenceBody({
  source,
  timeZone,
}: {
  readonly source: EvidenceSource;
  readonly timeZone: string;
}) {
  return (
    <>
      <blockquote className="pv-quote">“{source.exact_text}”</blockquote>
      <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
        sha256:{abbreviateHash(source.artifact.subject ?? source.evidence_id)} ·{" "}
        {source.artifact.source_type}
        {source.source_locator === null ? null : (
          <>
            {" · chars "}
            {source.source_locator.char_start}
            {"-"}
            {source.source_locator.char_end}
          </>
        )}
      </p>
      <p className="pv-mono">observed {formatInstantOrRaw(source.observed_at, timeZone)}</p>
      <RetractionBadge status={source.retraction_status} />
    </>
  );
}

function ClaimBody({
  source,
  timeZone,
}: {
  readonly source: ClaimSource;
  readonly timeZone: string;
}) {
  const amount = (source.object_json as { amount?: { currency?: string; amount?: string } }).amount;
  return (
    <>
      <p className="pv-label">[{source.actor_type}: asserts]</p>
      <p className="pv-quote">
        {source.predicate}
        {amount?.currency && amount.amount ? ` = ${amount.currency} ${amount.amount}` : ""}
      </p>
      <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
        {source.claim_kind} · authority_score={source.authority_score} · recorded{" "}
        {formatInstantOrRaw(source.recorded_at, timeZone)}
      </p>
    </>
  );
}

export function GroundingEdgeRow({
  edge,
  timeZone,
}: {
  readonly edge: GroundingEdge;
  readonly timeZone: string;
}) {
  return (
    <li
      className="pv-relation"
      data-relation={edge.relation}
      data-support-id={edge.support_id}
      style={{ paddingBlock: "var(--pv-space-3)" }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--pv-space-4)",
          justifyContent: "space-between",
        }}
      >
        <RelationLabel relation={edge.relation} />
        <span className="pv-mono">
          weight={edge.weight} · reason_code={edge.reason_code}
        </span>
      </div>

      {isEvidence(edge.source) ? (
        <>
          <p className="pv-mono">
            source_authority={edge.source.source_authority} · extraction_confidence=
            {edge.source.extraction_confidence}
          </p>
          <EvidenceBody source={edge.source} timeZone={timeZone} />
        </>
      ) : (
        <ClaimBody source={edge.source} timeZone={timeZone} />
      )}
    </li>
  );
}

/**
 * The lineage rail.
 *
 * The case this exists to solve: the balance's *value* did not change while its *status*
 * did. A lineage that only showed value changes would render the hero as a no-op -- two
 * rows both reading USD 0.00 and nothing to see. So each entry states the value and the
 * epistemic status separately, and where the value is unchanged between versions it says
 * so in as many words.
 */
export function LineageChain({
  belief,
  timeZone,
}: {
  readonly belief: Belief;
  readonly timeZone: string;
}) {
  const describeValue = (version: BeliefVersion): string | null => {
    if (version.value_json === undefined) return null;
    const value = version.value_json as { currency?: string; amount?: string };
    if (value.currency && value.amount) return `${value.currency} ${value.amount}`;
    return JSON.stringify(version.value_json);
  };

  return (
    <ol className="pv-lineage">
      {belief.lineage.map((version, index) => {
        const previous = index === 0 ? undefined : belief.lineage[index - 1];
        const value = describeValue(version);
        const previousValue = previous === undefined ? null : describeValue(previous);
        const valueUnchanged = previousValue !== null && value !== null && previousValue === value;
        const superseded = version.superseded_at !== null && version.superseded_at !== undefined;

        return (
          <li
            className="pv-lineage-entry"
            key={version.belief_version_id}
            data-current={version.is_current === true ? "true" : "false"}
            data-version-no={version.version_no}
          >
            <span className="pv-mono">
              {version.recorded_at === undefined ? (
                <Absent describe="record time not returned" />
              ) : (
                formatDateOrRaw(version.recorded_at, timeZone)
              )}
            </span>
            <div>
              <p className={superseded ? "pv-mono pv-lineage-superseded" : "pv-mono"}>
                v{version.version_no} · {value ?? "value not returned"} · {version.epistemic_status}{" "}
                · belief_confidence=
                {version.belief_confidence ?? (
                  <Absent describe="no confidence returned on this version" />
                )}
              </p>
              <p className="pv-mono" style={{ color: "var(--pv-ink-faint)" }}>
                {superseded ? "superseded, retained permanently" : "current"}
                {version.supersession_reason_codes && version.supersession_reason_codes.length > 0
                  ? ` · reason_code ${version.supersession_reason_codes.join(", ")}`
                  : ""}
                {valueUnchanged ? " · value unchanged" : ""}
              </p>
              {valueUnchanged ? (
                <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                  The amount did not change. Our confidence in it did.
                </p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The conflict pair.
 *
 * Both records are shown side by side and neither is presented as settled. Provenance
 * holds both; the canonical position is unchanged until a human resolves it.
 */
export function ConflictPair({
  conflict,
  timeZone,
}: {
  readonly conflict: ProofConflict;
  readonly timeZone: string;
}) {
  return (
    <div className="pv-card" data-conflict-id={conflict.conflict_id}>
      <div className="pv-card-pad pv-section-heading">
        <p className="pv-label" style={{ color: "var(--pv-conflict)" }}>
          Open contradiction · {conflict.conflict_type} · {conflict.status}
        </p>
        <p className="pv-mono">
          severity={conflict.severity} requires_human={String(conflict.requires_human)} · detected{" "}
          {formatInstantOrRaw(conflict.detected_at, timeZone)}
        </p>
      </div>

      <div className="pv-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="pv-card-pad pv-relation" data-relation="SUPPORTS">
          <p className="pv-label">Record on file</p>
          <p className="pv-quote">{conflict.left.summary}</p>
          <p className="pv-mono">{conflict.left.source_kind}</p>
        </div>
        <div className="pv-card-pad pv-relation" data-relation="CONTRADICTS">
          <p className="pv-label">Competing assertion</p>
          <p className="pv-quote">{conflict.right.summary}</p>
          <p className="pv-mono">{conflict.right.source_kind}</p>
        </div>
      </div>

      <p className="pv-card-pad pv-prose" style={{ background: "var(--pv-surface-band)" }}>
        Provenance holds both records. The canonical position remains unchanged until resolved by
        human consent.
      </p>
    </div>
  );
}
