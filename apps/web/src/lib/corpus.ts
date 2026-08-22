import {
  getActionIntents,
  getArtifacts,
  getRelationship,
  getRelationships,
  getStateProof,
  getTimeline,
  getTriggers,
} from "@/lib/api/reads";
import { loadDashboard } from "@/lib/session";
import type { ClaimSource, EvidenceSource } from "@/lib/api/contract";

/**
 * The search corpus, assembled from endpoints that exist.
 *
 * `specs/15_API_SPEC.md` section 8 defines no search endpoint. The design shows one, over
 * a corpus scoped to the reader, returning ranked matches across artifacts, evidence,
 * claims, beliefs, watches, docket entries and proofs.
 *
 * Two ways to close that gap. The dishonest one is to filter a fixture and print a match
 * count, which produces a screen indistinguishable from the real thing and true of
 * nothing. The honest one is this: read the endpoints that do exist, tag every record with
 * the endpoint path that produced it, and match literally over the text those endpoints
 * returned. The screen then states what it is, every row names its origin, and when the
 * search endpoint lands this module is deleted rather than corrected.
 *
 * What this is not, and the screen says so: it is not ranked, not semantic, and not scoped
 * to the whole corpus. It sees the first page of each list endpoint and the cases those
 * pages reach. A record beyond those pages will not appear, and a reader is entitled to
 * know that rather than to conclude their record does not contain it.
 */

/**
 * Discriminate a grounding edge's source.
 *
 * `"evidence_id" in source` looks like the obvious test and is wrong: `ClaimSource` also
 * declares `evidence_id` (nullable, naming the evidence a claim was extracted from), so
 * that check is true for both and would render every counterparty claim as though it were
 * an admitted piece of evidence. `exact_text` is on `EvidenceSource` alone, and quoting
 * verbatim source text is the thing that makes evidence evidence.
 */
function isEvidenceSource(source: EvidenceSource | ClaimSource): source is EvidenceSource {
  return "exact_text" in source;
}

export type SearchCategory =
  | "ARTIFACT"
  | "RELATIONSHIP"
  | "CASE"
  | "EVIDENCE"
  | "CLAIM"
  | "BELIEF"
  | "WATCH"
  | "DOCKET"
  | "DRAFT";

export interface SearchRecord {
  readonly category: SearchCategory;
  readonly id: string;
  readonly title: string;
  readonly detail: string;
  /** The endpoint path this record was read from. Rendered next to every result. */
  readonly origin: string;
  readonly href: string | null;
  /** Every string this record is matched against. Never includes invented text. */
  /**
   * Searchable terms. Entries may be null at the source -- a commitment whose
   * counterparty name lives on the case, an action intent with no subject --
   * and `terms()` drops those rather than indexing the string "null", which
   * would make every such record findable by typing "null" and would rank
   * unrelated rows together.
   */
  readonly haystack: readonly string[];
}

/** Drop the nulls a nullable API field contributes; keep everything else. */
function terms(...values: readonly (string | null | undefined)[]): readonly string[] {
  return values.filter((v): v is string => typeof v === "string" && v.length > 0);
}

export interface SearchCorpus {
  readonly records: readonly SearchRecord[];
  /** Counts that are not searchable records but are exportable contents. */
  readonly commitmentCount: number;
  readonly beliefVersionCount: number;
  readonly proofCount: number;
  /** Endpoints that were read successfully, in the order they were read. */
  readonly endpointsRead: readonly string[];
  /** Endpoints that failed, with their code. The screen reports these. */
  readonly endpointsFailed: readonly { readonly path: string; readonly code: string }[];
  /** True when any list endpoint reported a further page this build did not fetch. */
  readonly truncated: boolean;
}

export const SEARCH_CATEGORY_ORDER: readonly SearchCategory[] = [
  "ARTIFACT",
  "EVIDENCE",
  "CLAIM",
  "BELIEF",
  "WATCH",
  "DOCKET",
  "CASE",
  "RELATIONSHIP",
  "DRAFT",
];

export async function buildSearchCorpus(): Promise<SearchCorpus> {
  const records: SearchRecord[] = [];
  const endpointsRead: string[] = [];
  const endpointsFailed: { path: string; code: string }[] = [];
  let truncated = false;
  let commitmentCount = 0;
  let beliefVersionCount = 0;
  let proofCount = 0;

  const [dashboard, relationships, artifacts, triggers, drafts] = await Promise.all([
    loadDashboard(),
    getRelationships(),
    getArtifacts(),
    getTriggers(),
    getActionIntents(),
  ]);

  const caseIds = new Set<string>();

  if (dashboard.ok) {
    endpointsRead.push("/v1/dashboard");
    for (const item of dashboard.data.cases_attention) {
      caseIds.add(item.case_id);
      records.push({
        category: "CASE",
        id: item.case_id,
        title: item.title,
        detail: `${item.status} · revision ${item.revision} · ${item.counterparty_display_name}`,
        origin: "/v1/dashboard",
        href: `/cases/${item.case_id}`,
        haystack: terms(
          item.title,
          item.status,
          item.counterparty_display_name,
          item.headline,
          ...item.attention_reason_codes,
        ),
      });
    }
  } else {
    endpointsFailed.push({ path: dashboard.path, code: dashboard.code });
  }

  if (relationships.ok) {
    endpointsRead.push("/v1/relationships");
    truncated = truncated || relationships.data.page.has_more;
    for (const item of relationships.data.items) {
      records.push({
        category: "RELATIONSHIP",
        id: item.relationship_id,
        title: item.counterparty.display_name,
        detail: `${item.counterparty.kind} · ${item.relationship_type} · ${item.status}`,
        origin: "/v1/relationships",
        href: `/relationships/${item.relationship_id}`,
        haystack: [
          item.counterparty.display_name,
          item.counterparty.kind,
          item.label,
          item.relationship_type,
          item.status,
          item.external_account_ref_masked ?? "",
        ],
      });
    }

    const details = await Promise.all(
      relationships.data.items.map((r) => getRelationship(r.relationship_id)),
    );
    for (const detail of details) {
      if (!detail.ok) {
        endpointsFailed.push({ path: detail.path, code: detail.code });
        continue;
      }
      endpointsRead.push(`/v1/relationships/${detail.data.relationship_id}`);
      for (const entry of detail.data.cases) {
        caseIds.add(entry.case_id);
        if (records.some((r) => r.category === "CASE" && r.id === entry.case_id)) continue;
        records.push({
          category: "CASE",
          id: entry.case_id,
          title: entry.title,
          detail: `${entry.status} · revision ${entry.revision} · ${entry.case_type}`,
          origin: `/v1/relationships/${detail.data.relationship_id}`,
          href: `/cases/${entry.case_id}`,
          haystack: [entry.title, entry.status, entry.case_type],
        });
      }
    }
  } else {
    endpointsFailed.push({ path: relationships.path, code: relationships.code });
  }

  if (artifacts.ok) {
    endpointsRead.push("/v1/artifacts");
    truncated = truncated || artifacts.data.page.has_more;
    for (const artifact of artifacts.data.items) {
      records.push({
        category: "ARTIFACT",
        id: artifact.artifact_id,
        title: artifact.filename ?? artifact.subject ?? artifact.artifact_id,
        detail: `${artifact.size_bytes.toLocaleString("en-GB")} bytes · ${artifact.parser_status}`,
        origin: "/v1/artifacts",
        href: `/artifacts/${artifact.artifact_id}`,
        haystack: [
          artifact.filename ?? "",
          artifact.subject ?? "",
          artifact.sender_display ?? "",
          artifact.source_type,
          artifact.mime_type,
          artifact.content_sha256,
        ],
      });
    }
  } else {
    endpointsFailed.push({ path: artifacts.path, code: artifacts.code });
  }

  if (triggers.ok) {
    endpointsRead.push("/v1/triggers");
    truncated = truncated || triggers.data.page.has_more;
    for (const trigger of triggers.data.items) {
      records.push({
        category: "WATCH",
        id: trigger.trigger_id,
        title: trigger.predicate_summary,
        detail: `${trigger.state} · ${trigger.trigger_type} · ${trigger.case_title}`,
        origin: "/v1/triggers",
        href: `/watches`,
        haystack: [
          trigger.predicate_summary,
          trigger.state,
          trigger.trigger_type,
          trigger.case_title,
          trigger.last_reason_code ?? "",
        ],
      });
    }
  } else {
    endpointsFailed.push({ path: triggers.path, code: triggers.code });
  }

  if (drafts.ok) {
    endpointsRead.push("/v1/action-intents");
    truncated = truncated || drafts.data.page.has_more;
    for (const draft of drafts.data.items) {
      records.push({
        category: "DRAFT",
        id: draft.action_intent_id,
        title: draft.subject_preview,
        detail: `${draft.status} · ${draft.action_type} · ${draft.counterparty_display_name}`,
        origin: "/v1/action-intents",
        href: `/actions/${draft.action_intent_id}`,
        haystack: terms(
          draft.subject_preview,
          draft.status,
          draft.action_type,
          draft.counterparty_display_name,
          draft.recipient_masked,
        ),
      });
    }
  } else {
    endpointsFailed.push({ path: drafts.path, code: drafts.code });
  }

  /* Evidence, claims, beliefs and docket entries live inside a case. They are reachable
     only for the cases the reads above found, which is why the screen states the scope. */
  const perCase = await Promise.all(
    [...caseIds].map(async (caseId) => {
      const [proof, timeline] = await Promise.all([getStateProof(caseId), getTimeline(caseId)]);
      return { caseId, proof, timeline };
    }),
  );

  for (const { caseId, proof, timeline } of perCase) {
    if (proof.ok) {
      endpointsRead.push(`/v1/cases/${caseId}/state-proof`);
      proofCount += 1;
      commitmentCount += proof.data.commitments.length;
      beliefVersionCount += proof.data.beliefs.reduce((n, b) => n + b.lineage.length, 0);
      const origin = `/v1/cases/${caseId}/state-proof`;
      for (const belief of proof.data.beliefs) {
        records.push({
          category: "BELIEF",
          id: belief.belief_id,
          title: belief.predicate,
          detail: `v${belief.current_version.version_no} · ${belief.current_version.epistemic_status} · grounded=${String(belief.grounded)}`,
          origin,
          href: `/cases/${caseId}/proof`,
          haystack: [
            belief.predicate,
            belief.current_version.epistemic_status,
            JSON.stringify(belief.current_version.value_json ?? {}),
          ],
        });

        for (const edge of belief.grounding) {
          const source = edge.source;
          if (isEvidenceSource(source)) {
            records.push({
              category: "EVIDENCE",
              id: source.evidence_id,
              title: source.exact_text,
              detail: `${source.evidence_type} · extraction ${source.extraction_confidence} · ${source.retraction_status}`,
              origin,
              href: `/cases/${caseId}/proof`,
              haystack: [
                source.exact_text,
                source.normalized_text ?? "",
                source.evidence_type,
                source.artifact.subject ?? "",
                source.artifact.sender_display ?? "",
              ],
            });
          } else {
            records.push({
              category: "CLAIM",
              id: source.claim_id,
              title: source.predicate,
              detail: `${source.claim_kind} · ${source.actor_type} · authority ${source.authority_score}`,
              origin,
              href: `/cases/${caseId}/proof`,
              haystack: [
                source.predicate,
                source.claim_kind,
                source.actor_type,
                JSON.stringify(source.object_json),
              ],
            });
          }
        }
      }
    } else {
      endpointsFailed.push({ path: proof.path, code: proof.code });
    }

    if (timeline.ok) {
      endpointsRead.push(`/v1/cases/${caseId}/timeline`);
      truncated = truncated || timeline.data.page.has_more;
      for (const entry of timeline.data.items) {
        records.push({
          category: "DOCKET",
          id: entry.id,
          title: entry.headline,
          detail: `${entry.kind} · revision ${entry.case_revision} · ${entry.actor.type}`,
          origin: `/v1/cases/${caseId}/timeline`,
          href: `/cases/${caseId}`,
          haystack: [entry.headline, entry.kind, entry.actor.type, entry.actor.label],
        });
      }
    } else {
      endpointsFailed.push({ path: timeline.path, code: timeline.code });
    }
  }

  /* De-duplicate by category and id. The same evidence row grounds several beliefs, and a
     reader counting matches should not be told the record holds three copies of it. */
  const seen = new Set<string>();
  const unique = records.filter((record) => {
    const key = `${record.category}:${record.id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return {
    records: unique,
    commitmentCount,
    beliefVersionCount,
    proofCount,
    endpointsRead,
    endpointsFailed,
    truncated,
  };
}

/** Literal, case-insensitive substring match. Not ranked, and the screen says so. */
export function matchRecords(
  records: readonly SearchRecord[],
  query: string,
): readonly SearchRecord[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return [];
  return records.filter((record) =>
    record.haystack.some((text) => text.toLowerCase().includes(needle)),
  );
}
