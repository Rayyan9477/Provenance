/**
 * Formatting.
 *
 * Two rules govern this module.
 *
 * Money is never a JS number. Amounts arrive as decimal strings and are grouped by
 * string manipulation, because a binary float cannot hold 1800.0000 and a rounding
 * artefact in a legal record is not a cosmetic defect.
 *
 * Time is always rendered in the user's timezone, which arrives from `GET /v1/me`. There
 * is no fallback to the browser's zone: a record read in the wrong zone shows the wrong
 * day, and "which day did this happen" is the question the whole product answers.
 */

import type { Instant, Money } from "@/lib/api/contract";

/** Group an integer string in threes without going through a float. */
function groupIntegerPart(digits: string): string {
  const negative = digits.startsWith("-");
  const bare = negative ? digits.slice(1) : digits;
  let out = "";
  for (let i = 0; i < bare.length; i += 1) {
    if (i > 0 && (bare.length - i) % 3 === 0) out += ",";
    out += bare[i];
  }
  return negative ? `-${out}` : out;
}

/**
 * Render a 4-dp decimal string for display.
 *
 * Trailing zeros beyond two places are dropped, because `1800.0000` reads as machine
 * output rather than money. Significant digits beyond two places are kept: silently
 * truncating them would change the amount.
 */
export function formatDecimal(amount: string): string {
  const [whole = "0", fraction = ""] = amount.split(".");
  const trimmed = fraction.replace(/0+$/, "");
  const places = Math.max(2, trimmed.length);
  const padded = fraction.padEnd(places, "0").slice(0, places);
  return `${groupIntegerPart(whole)}.${padded}`;
}

export function formatMoney(money: Money): string {
  return `${money.currency} ${formatDecimal(money.amount)}`;
}

/**
 * Sum a list of amounts in one currency, as decimal strings.
 *
 * Used only where the API returns per-currency arrays and the surface has already
 * established that they share a currency. Mixed currencies return null rather than a
 * number, because the Kernel refuses arithmetic across currencies and so does this.
 */
export function sumSameCurrency(items: readonly Money[]): Money | null {
  if (items.length === 0) return null;
  const currency = items[0]?.currency;
  if (currency === undefined) return null;
  if (items.some((m) => m.currency !== currency)) return null;

  const scale = 4;
  let total = 0n;
  for (const item of items) {
    const [whole = "0", fraction = ""] = item.amount.split(".");
    const digits = `${whole}${fraction.padEnd(scale, "0").slice(0, scale)}`;
    total += BigInt(digits);
  }
  const negative = total < 0n;
  const raw = (negative ? -total : total).toString().padStart(scale + 1, "0");
  const whole = raw.slice(0, raw.length - scale);
  const fraction = raw.slice(raw.length - scale);
  return { currency, amount: `${negative ? "-" : ""}${whole}.${fraction}` };
}

const TIME_PARTS: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
};

function partsOf(
  instant: Instant,
  timeZone: string,
  options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormatPart[] | null {
  const date = new Date(instant);
  if (Number.isNaN(date.getTime())) return null;
  try {
    return new Intl.DateTimeFormat("en-GB", { ...options, timeZone }).formatToParts(date);
  } catch {
    return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" }).formatToParts(date);
  }
}

function partValue(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((part) => part.type === type)?.value ?? "";
}

/**
 * `18 SEP 2026`.
 *
 * The date is assembled from `formatToParts` rather than taken from `format()`, and the
 * month is truncated to three letters deliberately.
 *
 * `en-GB` with `month: "short"` renders September as "SEPT" in current ICU, and every
 * other month as three letters. The demo clock is 18 September 2026, so the one month
 * where the locale disagrees with the canon in `CANONICAL_DECISIONS.md` is the only month
 * the hero dataset ever shows. Left alone, every date on the recorded demo would read
 * "18 SEPT 2026" against a design and a canon that both say "18 SEP 2026", and the
 * mismatch would be blamed on the design rather than on ICU.
 *
 * Truncating is safe here because the locale is pinned: this application renders dates in
 * `en-GB` only, and the month is uppercased in any case. It is a presentation decision
 * about a value that arrives whole; nothing about the instant itself is altered.
 */
export function formatDate(instant: Instant, timeZone: string): string | null {
  const parts = partsOf(instant, timeZone, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
  if (parts === null) return null;
  const day = partValue(parts, "day");
  const month = partValue(parts, "month").slice(0, 3).toUpperCase();
  const year = partValue(parts, "year");
  return `${day} ${month} ${year}`;
}

/** `18 SEP 2026, 14:05 UTC`. Zone abbreviation included, because it matters. */
export function formatInstant(instant: Instant, timeZone: string): string | null {
  const date = formatDate(instant, timeZone);
  const timeParts = partsOf(instant, timeZone, TIME_PARTS);
  if (date === null || timeParts === null) return null;

  const hour = partValue(timeParts, "hour");
  const minute = partValue(timeParts, "minute");

  const zoneParts = partsOf(instant, timeZone, { timeZoneName: "short" });
  const zone = zoneParts === null ? timeZone : partValue(zoneParts, "timeZoneName") || timeZone;

  return `${date}, ${hour}:${minute} ${zone}`;
}

/** `1 JUN - 30 JUN 2026`, or a single date when the ends coincide. */
export function formatDateRange(
  from: Instant | null,
  to: Instant | null,
  timeZone: string,
): string | null {
  const start = from === null ? null : formatDate(from, timeZone);
  const end = to === null ? null : formatDate(to, timeZone);
  if (start === null && end === null) return null;
  if (start !== null && end === null) return `FROM ${start}`;
  if (start === null && end !== null) return `UNTIL ${end}`;
  if (start === end) return start;
  return `${start} – ${end}`;
}

/**
 * Whole days between two instants.
 *
 * Returned so a surface can render "95 days past" from two timestamps that both came
 * from the record, rather than from a constant.
 */
export function daysBetween(from: Instant, to: Instant): number | null {
  const a = new Date(from).getTime();
  const b = new Date(to).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.floor((b - a) / 86_400_000);
}

/** `0.7100` stays `0.7100`. Confidence is shown at source precision, never rounded up. */
export function formatConfidence(value: string): string {
  return value;
}

/** `sha256:c4d5e6f7...1f20` -- head and tail, never a silent truncation. */
export function abbreviateHash(hash: string, head = 8, tail = 4): string {
  const bare = hash.startsWith("sha256:") ? hash.slice(7) : hash;
  if (bare.length <= head + tail) return bare;
  return `${bare.slice(0, head)}…${bare.slice(-tail)}`;
}

/**
 * Render a date, falling back to the stored instant rather than to a blank.
 *
 * The failing case is narrow but real: the row exists and carries a timestamp, and
 * `Intl` could not format it (a malformed instant, an unknown zone). Three responses are
 * available and only one is honest.
 *
 * Rendering nothing is a silent blank: the reader sees an empty cell and concludes the
 * system does not hold the date, when it does. Rendering the absence marker asserts the
 * same falsehood more explicitly. Rendering the raw value says exactly what is true --
 * this is what the record holds, and we could not present it more legibly than this.
 *
 * `check-render-honesty.mjs` rule R6 forbids the first of those, so this helper is the
 * only way to write the call.
 */
export function formatDateOrRaw(instant: Instant, timeZone: string): string {
  return formatDate(instant, timeZone) ?? instant;
}

/** As {@link formatDateOrRaw}, for a full instant. */
export function formatInstantOrRaw(instant: Instant, timeZone: string): string {
  return formatInstant(instant, timeZone) ?? instant;
}
