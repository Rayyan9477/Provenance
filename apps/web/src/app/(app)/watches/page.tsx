import Link from "next/link";
import { EmptyState, ErrorState } from "@/components/primitives/States";
import { Absent } from "@/components/primitives/Absent";
import { getTriggers } from "@/lib/api/reads";
import { loadMe, timeZoneOf } from "@/lib/session";
import { formatInstantOrRaw } from "@/lib/format";
import type { PredicateAst, PredicateAstDocument } from "@/lib/api/contract";

/**
 * S07 -- watches.
 *
 * Prospective memory, made visible. `predicate_ast` is rendered verbatim because the
 * grammar is a small closed whitelist over named projection fields, evaluated by
 * deterministic Python; it contains no executable code and no personal data. The values
 * the predicate actually saw at wakeup come from `last_evaluation.field_values`, which is
 * what turns "it fired" into "it fired, and here is what it read".
 *
 * A NO_OP is shown alongside a FIRED. A demonstration that only ever shows the trigger
 * firing proves nothing about the predicate.
 */

export const dynamic = "force-dynamic";

/** Render the AST as an s-expression. Layout only; nothing is inferred or defaulted. */
/**
 * Render one predicate node. Total: it has no branch that can emit `undefined`.
 *
 * It had one. `predicate_ast` on the wire is a wrapper -- `{ast_version,
 * bindings, predicate}` -- and this function was handed the wrapper. No `op`,
 * no `args`, so the final template interpolated `undefined` and the Watches
 * screen carried the literal string `(undefined )` under the heading for
 * prospective memory, beside an API-supplied "No predicate recorded." that was
 * equally wrong and produced by the same misreading one layer down.
 *
 * `unwrapPredicate` below takes the wrapper apart. This function now only ever
 * sees a node, and returns `null` rather than a glyph when there is nothing to
 * print -- `check-render-honesty.mjs` rule R4 is right that "we do not have
 * this" must be one component with one meaning, so the caller renders `<Absent>`
 * and this function says nothing at all.
 */
function renderAst(ast: PredicateAst | null | undefined): string | null {
  if (ast === null || ast === undefined) return null;
  if (ast.op === "FIELD") return (ast as { path?: string }).path ?? null;
  if (ast.op === "CONST") return (ast as { value?: string }).value ?? null;
  if (!ast.op) return null;
  const args = (ast as { args?: readonly PredicateAst[] }).args ?? [];
  if (args.length === 0) return `(${ast.op})`;
  const rendered = args.map(renderAst).filter((part): part is string => part !== null);
  return rendered.length === 0 ? `(${ast.op})` : `(${ast.op} ${rendered.join(" ")})`;
}

/** The node inside the wire document, or the value itself if it is already one. */
function unwrapPredicate(
  document: PredicateAstDocument | PredicateAst | null | undefined,
): PredicateAst | null {
  if (document === null || document === undefined) return null;
  if ("predicate" in document) return (document as PredicateAstDocument).predicate;
  return document as PredicateAst;
}

export default async function WatchesPage() {
  const [me, triggers] = await Promise.all([loadMe(), getTriggers()]);
  const timeZone = timeZoneOf(me);

  if (!triggers.ok) {
    return (
      <ErrorState
        heading="We could not read your watches."
        detail={`GET ${triggers.path} returned ${triggers.status} ${triggers.code}`}
        traceId={triggers.traceId}
      />
    );
  }

  const items = triggers.data.items;
  const counts = {
    armed: items.filter((t) => t.state === "ARMED").length,
    fired: items.filter((t) => t.state === "FIRED").length,
    disarmed: items.filter((t) => t.state === "DISARMED").length,
    expired: items.filter((t) => t.state === "EXPIRED").length,
  };

  return (
    <div className="pv-stack">
      <header className="pv-section-heading">
        <h1 className="pv-display">Watches</h1>
        <p className="pv-label">Evaluated on schedule, and on every new artifact</p>
      </header>

      <p className="pv-prose">
        A watch is a predicate the Kernel keeps evaluating on your behalf. It wakes on elapsed time
        or on new evidence, and it never sends anything. It only changes what needs your attention.
      </p>

      <ul className="pv-grid pv-grid-5">
        {(
          [
            ["Armed", counts.armed],
            ["Fired", counts.fired],
            ["Disarmed", counts.disarmed],
            ["Expired", counts.expired],
          ] as const
        ).map(([label, value]) => (
          <li className="pv-card pv-card-pad" key={label}>
            <p className="pv-figure">{value}</p>
            <p className="pv-label">{label}</p>
          </li>
        ))}
      </ul>

      <p className="pv-label">
        You set none of these. Provenance has no user-reminder feature, so there is no count of
        reminders to render.
      </p>

      {items.length === 0 ? (
        <EmptyState heading="No watch is set on your record.">
          <p>Watches are created by the Kernel when a commitment carries a deadline.</p>
        </EmptyState>
      ) : (
        <ul className="pv-stack-tight">
          {items.map((trigger) => (
            <li
              className="pv-card pv-card-pad"
              key={trigger.trigger_id}
              data-trigger-state={trigger.state}
            >
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "var(--pv-space-4)",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <p className="pv-prose" style={{ fontSize: "var(--pv-size-body)" }}>
                    {trigger.predicate_summary}
                  </p>
                  <p className="pv-mono">
                    {renderAst(unwrapPredicate(trigger.predicate_ast)) ?? (
                      <Absent describe="this trigger carries no predicate expression" />
                    )}
                  </p>
                </div>
                <span
                  className="pv-chip"
                  data-attention={trigger.state === "FIRED" ? "URGENT" : "INFO"}
                >
                  {trigger.state}
                </span>
              </div>

              <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
                trigger_type={trigger.trigger_type} · basis_case_revision=
                {trigger.basis_case_revision} · evaluation_version={trigger.evaluation_version} ·
                last_result=
                {trigger.last_result ?? (
                  <Absent describe="this watch has produced no evaluation result" />
                )}{" "}
                · last_reason_code=
                {trigger.last_reason_code ?? (
                  <Absent describe="no reason code was recorded for the last evaluation" />
                )}
              </p>
              <p className="pv-mono">
                not_before={formatInstantOrRaw(trigger.not_before, timeZone)} · last_evaluated_at=
                {trigger.last_evaluated_at === null
                  ? "never"
                  : formatInstantOrRaw(trigger.last_evaluated_at, timeZone)}
              </p>

              {trigger.last_evaluation === null ? (
                <p className="pv-mono">
                  <Absent describe="this watch has not been evaluated yet" />
                </p>
              ) : (
                <div className="pv-inset" style={{ marginTop: "var(--pv-space-2)" }}>
                  <p className="pv-label">What the predicate saw at wakeup</p>
                  <ul className="pv-mono">
                    {Object.entries(trigger.last_evaluation.field_values).map(([key, value]) => (
                      <li key={key}>
                        {key}=
                        {value === null || value === undefined ? (
                          <Absent describe="the projection field was null at evaluation" />
                        ) : typeof value === "object" ? (
                          JSON.stringify(value)
                        ) : (
                          String(value)
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <p style={{ marginTop: "var(--pv-space-3)" }}>
                <Link className="pv-button" href={`/cases/${trigger.case_id}`}>
                  Open {trigger.case_title}
                </Link>
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
