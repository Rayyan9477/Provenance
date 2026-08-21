import type { AttentionLevel } from "@/lib/api/contract";
import { getRelationship, getRelationships } from "@/lib/api/reads";
import { loadDashboard } from "@/lib/session";

/**
 * Where a case index gets its case ids.
 *
 * The API this build renders against exposes `GET /v1/cases` in its index but this
 * application does not read it, so two endpoints that it does read are unioned instead:
 * the dashboard's attention list, and the cases named on each relationship file.
 *
 * The interesting field is `via`. Every row on a case index says which endpoint produced
 * it, and the index reports how many relationship files it could not read. A case list
 * that is quietly short is a statement that the reader has fewer cases than they do, and
 * the reader has no way to notice it unless the screen keeps count of what it missed.
 */

export interface CaseSummary {
  readonly caseId: string;
  readonly title: string;
  readonly status: string;
  readonly revision: number;
  readonly attentionLevel: AttentionLevel;
  readonly lastActivityAt: string;
  readonly counterparty: string | null;
  /** The endpoint this row was found through, rendered next to it. */
  readonly via: string;
}

export interface CaseIndex {
  readonly cases: readonly CaseSummary[];
  readonly dashboardRead: boolean;
  readonly relationshipsRead: number;
  readonly relationshipsUnreadable: number;
}

export async function collectCases(): Promise<CaseIndex> {
  const [dashboard, relationships] = await Promise.all([loadDashboard(), getRelationships()]);

  const details = relationships.ok
    ? await Promise.all(relationships.data.items.map((r) => getRelationship(r.relationship_id)))
    : [];

  const found = new Map<string, CaseSummary>();

  if (dashboard.ok) {
    for (const item of dashboard.data.cases_attention) {
      found.set(item.case_id, {
        caseId: item.case_id,
        title: item.title,
        status: item.status,
        revision: item.revision,
        attentionLevel: item.attention_level,
        lastActivityAt: item.last_activity_at,
        counterparty: item.counterparty_display_name,
        via: "the dashboard attention list",
      });
    }
  }

  for (const detail of details) {
    if (!detail.ok) continue;
    for (const entry of detail.data.cases) {
      if (found.has(entry.case_id)) continue;
      found.set(entry.case_id, {
        caseId: entry.case_id,
        title: entry.title,
        status: entry.status,
        revision: entry.revision,
        attentionLevel: entry.attention_level,
        lastActivityAt: entry.last_activity_at,
        counterparty: detail.data.counterparty.display_name,
        via: `the file for ${detail.data.counterparty.display_name}`,
      });
    }
  }

  return {
    cases: [...found.values()],
    dashboardRead: dashboard.ok,
    relationshipsRead: details.filter((d) => d.ok).length,
    relationshipsUnreadable: details.filter((d) => !d.ok).length,
  };
}
