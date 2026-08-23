import type { ReactNode } from "react";

/**
 * The five system states, plus the two that are not states.
 *
 * The taxonomy matters because the alternative is worse: a screen that renders zero when
 * it means "loading", or an empty list when it means "we could not reach the database",
 * has told the reader something false about their own record. Each state below says which
 * of those it is, and none of them renders a value.
 */

export function SkeletonBlock({
  lines = 3,
  label,
}: {
  readonly lines?: number;
  readonly label?: string;
}) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" className="pv-stack-tight">
      <span className="pv-sr-only">{label ?? "Loading"}</span>
      {Array.from({ length: lines }, (_, index) => (
        <span
          className="pv-skeleton"
          key={index}
          aria-hidden="true"
          style={{ width: `${100 - index * 12}%` }}
        />
      ))}
    </div>
  );
}

export function EmptyState({
  heading,
  children,
}: {
  readonly heading: string;
  readonly children?: ReactNode;
}) {
  return (
    <div className="pv-state" data-kind="EMPTY">
      <p className="pv-title">{heading}</p>
      {children ? <div className="pv-prose">{children}</div> : null}
    </div>
  );
}

/**
 * Something we could not read.
 *
 * The distinction this component exists to preserve: an unreachable endpoint is not an
 * empty result. `detail` names the endpoint so the failure is diagnosable rather than
 * merely apologetic.
 */
export function ErrorState({
  heading,
  detail,
  traceId,
  children,
}: {
  readonly heading: string;
  readonly detail?: string;
  readonly traceId?: string | null;
  readonly children?: ReactNode;
}) {
  return (
    <div className="pv-state" data-kind="ERROR" role="alert">
      <p className="pv-title">{heading}</p>
      {detail ? (
        <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
          {detail}
        </p>
      ) : null}
      {traceId ? (
        <p className="pv-mono" style={{ marginTop: "var(--pv-space-2)" }}>
          trace_id={traceId}
        </p>
      ) : null}
      {children ? <div className="pv-prose">{children}</div> : null}
    </div>
  );
}

export function ForbiddenState({
  heading,
  children,
}: {
  readonly heading: string;
  readonly children?: ReactNode;
}) {
  return (
    <div className="pv-state" data-kind="FORBIDDEN" role="alert">
      <p className="pv-title">{heading}</p>
      {children ? <div className="pv-prose">{children}</div> : null}
    </div>
  );
}

/**
 * A safety mechanism working, not an error.
 *
 * A 409 ACTION_STALE means the case moved underneath a prepared action. That is the
 * approval binding doing its job. The copy has to read that way, or a reader concludes
 * the system is broken at the exact moment it is protecting them.
 */
export function StaleState({
  basisRevision,
  currentRevision,
  children,
}: {
  readonly basisRevision: number;
  readonly currentRevision: number;
  readonly children?: ReactNode;
}) {
  return (
    <div className="pv-state" data-kind="STALE" role="status">
      <p className="pv-title">The record moved while this was waiting for you.</p>
      <p className="pv-prose" style={{ marginTop: "var(--pv-space-2)" }}>
        This action was prepared against revision {basisRevision}. The case is now at revision{" "}
        {currentRevision}. Approval binds to the revision it was prepared against, so it will not
        send against a record it has not seen. Nothing was sent.
      </p>
      {children}
    </div>
  );
}
