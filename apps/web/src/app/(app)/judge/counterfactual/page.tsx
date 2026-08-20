import Link from "next/link";
import { CounterfactualPanel } from "@/components/judge/CounterfactualPanel";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/primitives/States";
import { getCounterfactual, getEntryPoints } from "@/lib/api/reads";
import { loadMe } from "@/lib/session";

/**
 * S12 -- the counterfactual, as its own screen.
 *
 * Gated twice: `judge_mode_enabled` opens Judge Mode, and `counterfactual_enabled` opens
 * this sub-surface. An absent flag is treated as false, because a surface that appears
 * when a flag is missing is a leak rather than a default.
 *
 * A counterfactual is created by `POST /v1/judge-mode/counterfactual`, which is a Phase 8
 * mutation this build does not perform. Where no id can be resolved from the record, this
 * screen says so and stops. It does not display a specimen comparison.
 */

export const dynamic = "force-dynamic";

interface PageProps {
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function CounterfactualPage({ searchParams }: PageProps) {
  const query = await searchParams;
  const [me, entry] = await Promise.all([loadMe(), getEntryPoints()]);

  if (!me.ok) {
    return (
      <ErrorState
        heading="We could not read your session."
        detail={`GET ${me.path} returned ${me.status} ${me.code}`}
      />
    );
  }

  if (!me.data.judge_mode_enabled || me.data.feature_flags.counterfactual_enabled !== true) {
    return (
      <ForbiddenState heading="The counterfactual is not enabled for this account.">
        <p>
          It is gated on `judge_mode_enabled` and `counterfactual_enabled` from GET /v1/me. An
          absent flag is false.
        </p>
      </ForbiddenState>
    );
  }

  const id =
    typeof query["counterfactual_id"] === "string"
      ? query["counterfactual_id"]
      : entry.heroCounterfactualId;

  if (id === null) {
    return (
      <EmptyState heading="No counterfactual has been run.">
        <p>
          A counterfactual is created by POST /v1/judge-mode/counterfactual and then polled. This
          build performs reads only, so there is no run to show. Nothing is displayed in its place:
          a specimen comparison would be indistinguishable from a real one, which is exactly the
          objection the parity block exists to answer.
        </p>
        <p style={{ marginTop: "var(--pv-space-3)" }}>
          <Link className="pv-button" href="/judge">
            Back to Judge Mode
          </Link>
        </p>
      </EmptyState>
    );
  }

  const result = await getCounterfactual(id);
  if (!result.ok) {
    return (
      <ErrorState
        heading="We could not read this counterfactual."
        detail={`GET ${result.path} returned ${result.status} ${result.code}`}
        traceId={result.traceId}
      />
    );
  }

  return (
    <div className="pv-stack">
      <CounterfactualPanel cf={result.data} />
      <p>
        <Link className="pv-button" href="/judge">
          Back to Judge Mode
        </Link>
      </p>
    </div>
  );
}
