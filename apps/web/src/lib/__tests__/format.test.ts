import { describe, expect, it } from "vitest";

import {
  abbreviateHash,
  daysBetween,
  formatBeliefValue,
  formatDate,
  formatDateOrRaw,
  formatDecimal,
  formatInstant,
  formatInstantOrRaw,
  formatMoney,
  sumSameCurrency,
} from "@/lib/format";

/**
 * Money is never a JS number, and time is never the browser's.
 *
 * Both rules exist because their violations are invisible in review and catastrophic in a
 * legal record. `0.1 + 0.2` is the canonical demonstration of the first. The second is
 * quieter: a date rendered in the reader's local zone rather than the zone the record was
 * kept in shifts by a day at the boundary, and "which day did this happen" is the question
 * this product exists to answer.
 */

describe("decimal money never passes through a float", () => {
  it("keeps four decimal places as the API sends them, trimming only trailing noise", () => {
    expect(formatDecimal("1800.0000")).toBe("1,800.00");
    expect(formatDecimal("0.0000")).toBe("0.00");
    expect(formatDecimal("186.0000")).toBe("186.00");
  });

  it("keeps significant digits beyond two places rather than truncating them away", () => {
    expect(formatDecimal("0.7100")).toBe("0.71");
    expect(formatDecimal("1.2345")).toBe("1.2345");
    expect(formatDecimal("0.0001")).toBe("0.0001");
  });

  it("groups thousands without arithmetic", () => {
    expect(formatDecimal("1000000.0000")).toBe("1,000,000.00");
    expect(formatDecimal("999.9900")).toBe("999.99");
    expect(formatDecimal("-1234.5600")).toBe("-1,234.56");
  });

  it("renders a currency alongside every amount", () => {
    expect(formatMoney({ currency: "USD", amount: "220.0000" })).toBe("USD 220.00");
  });

  it("sums decimal strings exactly, where a float would not", () => {
    const total = sumSameCurrency([
      { currency: "USD", amount: "0.1000" },
      { currency: "USD", amount: "0.2000" },
    ]);
    expect(total?.amount).toBe("0.3000");
    /* The float this replaces: 0.1 + 0.2 === 0.30000000000000004. */
    expect(Number("0.1") + Number("0.2")).not.toBe(0.3);
  });

  it("refuses to add across currencies, as the Kernel does", () => {
    expect(
      sumSameCurrency([
        { currency: "USD", amount: "1.0000" },
        { currency: "EUR", amount: "1.0000" },
      ]),
    ).toBeNull();
  });

  it("returns null for an empty list rather than a zero", () => {
    /* A zero total would assert a currency the caller never named. */
    expect(sumSameCurrency([])).toBeNull();
  });
});

describe("time is rendered in the record's zone, and never invented", () => {
  const instant = "2026-09-18T14:05:00.000Z";

  it("renders a date in the zone it is given", () => {
    expect(formatDate(instant, "UTC")).toBe("18 SEP 2026");
    /* 14:05 UTC is 10:05 the same day in New York; the date holds. */
    expect(formatDate(instant, "America/New_York")).toBe("18 SEP 2026");
  });

  it("shifts the day at the boundary, which is why the zone must come from the record", () => {
    const lateEvening = "2026-09-18T02:30:00.000Z";
    expect(formatDate(lateEvening, "UTC")).toBe("18 SEP 2026");
    expect(formatDate(lateEvening, "America/New_York")).toBe("17 SEP 2026");
  });

  it("names the zone in a rendered instant", () => {
    expect(formatInstant(instant, "UTC")).toContain("UTC");
  });

  it("returns null for an unparseable instant rather than a plausible date", () => {
    expect(formatDate("not-a-date", "UTC")).toBeNull();
    expect(formatInstant("not-a-date", "UTC")).toBeNull();
  });

  it("falls back to the stored value, never to a blank", () => {
    /*
     * The row holds something. Printing nothing would tell the reader it holds nothing,
     * which is false. Printing the raw value tells the truth: this is what we have, and
     * we could not present it more legibly.
     */
    expect(formatDateOrRaw("not-a-date", "UTC")).toBe("not-a-date");
    expect(formatInstantOrRaw("not-a-date", "UTC")).toBe("not-a-date");
    expect(formatDateOrRaw(instant, "UTC")).toBe("18 SEP 2026");
  });

  it("counts whole days between two instants that both came from the record", () => {
    expect(daysBetween("2026-06-15T00:00:00.000Z", "2026-09-18T14:05:00.000Z")).toBe(95);
    expect(daysBetween("bad", "2026-09-18T14:05:00.000Z")).toBeNull();
  });
});

describe("hashes are abbreviated visibly, never silently", () => {
  it("keeps head and tail with an ellipsis between them", () => {
    const hash = "c4d5e6f7a8b91c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a41f20a";
    const short = abbreviateHash(hash);
    expect(short.startsWith("c4d5e6f7")).toBe(true);
    expect(short.endsWith("f20a")).toBe(true);
    expect(short).toContain("…");
  });

  it("returns a short hash untouched rather than padding it", () => {
    expect(abbreviateHash("abcd")).toBe("abcd");
  });
});

/**
 * One belief value, one formatter.
 *
 * Three components rendered `value_json` and all three fell through to
 * `JSON.stringify`, so a state belief printed the literal `{"state":"TERMINATED"}`
 * in the same headline slot where a money belief printed `USD 1,800.00`. The
 * lineage rail additionally built money by hand, so one screen showed
 * `USD 1,800.00` and `USD 1800.0000` for the same value.
 */
describe("formatBeliefValue", () => {
  it("formats money through formatMoney, with separators and two places", () => {
    expect(formatBeliefValue({ currency: "USD", amount: "1800.0000" })).toBe("USD 1,800.00");
  });

  it("never renders a money value two different ways", () => {
    const money = { currency: "USD", amount: "1800.0000" };
    expect(formatBeliefValue(money)).toBe(formatMoney(money));
  });

  it("unwraps a single-key envelope to its scalar", () => {
    expect(formatBeliefValue({ state: "TERMINATED" })).toBe("TERMINATED");
    expect(formatBeliefValue({ state: "ACTIVE" })).toBe("ACTIVE");
  });

  it("renders primitives as themselves", () => {
    expect(formatBeliefValue("OPEN")).toBe("OPEN");
    expect(formatBeliefValue(42)).toBe("42");
    expect(formatBeliefValue(false)).toBe("false");
  });

  it("returns null for absence, so the caller can render <Absent>", () => {
    expect(formatBeliefValue(null)).toBeNull();
    expect(formatBeliefValue(undefined)).toBeNull();
    expect(formatBeliefValue({})).toBeNull();
  });

  it("never emits JSON punctuation for a composite value", () => {
    const out = formatBeliefValue({ state: "ACTIVE", since: "2026-05-15", nested: { a: 1 } });
    expect(out).not.toBeNull();
    expect(out).not.toContain('{"');
    expect(out).not.toContain('":"');
    expect(out).toContain("state=ACTIVE");
  });

  it("never returns the string 'undefined' or 'null'", () => {
    for (const value of [{ state: undefined }, { a: null }, { a: null, b: "x" }]) {
      const out = formatBeliefValue(value);
      if (out !== null) {
        expect(out).not.toContain("undefined");
        expect(out).not.toContain("null");
      }
    }
  });
});
