import type { Instant } from "@/lib/api/contract";
import { formatDateRange, formatInstant } from "@/lib/format";
import { Absent } from "./Absent";

/**
 * Bitemporality, rendered rather than implied.
 *
 * Every attention row, every piece of evidence, and every timeline entry carries two
 * clocks, and they are labelled separately because they answer different questions:
 *
 *   VALID TIME   when the thing was true in the world  (1 JUN - 30 JUN 2026)
 *   RECORD TIME  when the system came to know it       (ADMITTED 18 SEP 2026, 14:05 UTC)
 *
 * Collapsing them into one timestamp is the failure that makes a contradiction look like
 * a mistake: the June invoice is a perfectly ordinary document on its record clock and an
 * impossibility on its valid clock, and only showing both makes that legible.
 *
 * The two are distinguished by rule style as well as hue: valid time carries a solid
 * underline, record time a dashed one.
 */

export interface TimePairProps {
  readonly timeZone: string;
  readonly validFrom?: Instant | null;
  readonly validTo?: Instant | null;
  /** Pre-rendered valid-time phrasing, when the surface has a more precise one. */
  readonly validLabel?: string;
  readonly recordedAt?: Instant | null;
  /** Verb for the record clock: ADMITTED, WATCH FIRED, RECEIVED. */
  readonly recordVerb?: string;
}

export function TimePair({
  timeZone,
  validFrom = null,
  validTo = null,
  validLabel,
  recordedAt = null,
  recordVerb = "RECORDED",
}: TimePairProps) {
  const valid = validLabel ?? formatDateRange(validFrom, validTo, timeZone);
  const recorded = recordedAt === null ? null : formatInstant(recordedAt, timeZone);

  return (
    <div className="pv-time-pair">
      <div className="pv-time-field" data-clock="VALID">
        <span className="pv-label">Valid time</span>
        <span className="pv-time-value" data-valid-time={validFrom ?? validTo ?? ""}>
          {valid === null ? <Absent describe="valid time not recorded" /> : valid}
        </span>
      </div>
      <div className="pv-time-field" data-clock="RECORD">
        <span className="pv-label">Record time</span>
        <span className="pv-time-value" data-record-time={recordedAt ?? ""}>
          {recorded === null ? (
            <Absent describe="record time not recorded" />
          ) : (
            `${recordVerb} ${recorded}`
          )}
        </span>
      </div>
    </div>
  );
}
