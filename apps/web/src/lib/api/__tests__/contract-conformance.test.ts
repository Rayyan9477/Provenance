import { describe, expect, it } from "vitest";

import caseDetail from "./captured/case-detail.json";
import { formatMoney } from "@/lib/format";
import type { CaseResponse, Money } from "@/lib/api/contract";

/**
 * The declared contract must match what the control plane actually sends.
 *
 * The defect this closes
 * ----------------------
 * `CaseCommitment` declared `committed_amount: Decimal | null` -- and `Decimal` is a
 * string alias. The API sends `{"currency":"USD","amount":"420.0000"}`, a `Money`
 * object. The case detail page passed that object to `formatMoney`, which called
 * `.split(".")` on it, and every case detail route died with
 *
 *     TypeError: amount.split is not a function
 *
 * rendering "Application error: a server-side exception has occurred". Case detail is
 * the screen the whole product is about, and it 500'd on every one of ten cases.
 *
 * **TypeScript could not catch this.** The compiler was satisfied precisely because the
 * type was wrong: the page passed a `Decimal` where a `Decimal` was declared. A type
 * only checks code against a claim, and this claim was false. `ProofCommitment` -- the
 * same three fields on a different endpoint -- declared `Money` correctly, so the
 * repository held two answers to one question and the wrong one was on the busier path.
 *
 * Why a captured payload rather than a hand-written fixture
 * ---------------------------------------------------------
 * A hand-written fixture is another statement of the same belief, written by the same
 * person, at the same time, from the same misreading. It agrees with the contract
 * because both came from the author's mental model, which is the thing under test.
 * `captured/case-detail.json` is a byte-for-byte response from the running control
 * plane, so it can disagree -- and it did.
 *
 * Regenerate with:
 *
 *     TOKEN=$(python scripts/mint_local_token.py --quiet)
 *     curl -s -H "Authorization: Bearer $TOKEN" \
 *       "$PV_API_BASE_URL/v1/cases/<id>" | python -m json.tool \
 *       > apps/web/src/lib/api/__tests__/captured/case-detail.json
 */

function isMoney(value: unknown): value is Money {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Money).currency === "string" &&
    typeof (value as Money).amount === "string"
  );
}

describe("the captured case payload", () => {
  it("is a real response and not an empty object", () => {
    // Vacuity guard. Every assertion below passes trivially over `{}`.
    expect(Object.keys(caseDetail).length).toBeGreaterThan(10);
    expect(caseDetail).toHaveProperty("case_id");
    expect(caseDetail).toHaveProperty("commitments");
  });

  it("carries at least one commitment to assert against", () => {
    const { commitments } = caseDetail as unknown as CaseResponse;
    expect(commitments.length).toBeGreaterThan(0);
  });

  it.each(["committed_amount", "fulfilled_amount", "outstanding_amount"] as const)(
    "sends %s as a Money object, not a bare decimal string",
    (field) => {
      const { commitments } = caseDetail as unknown as CaseResponse;
      for (const commitment of commitments) {
        const value = commitment[field];
        if (value === null) continue;
        expect(
          isMoney(value),
          `${field} is ${JSON.stringify(value)}; the contract must declare Money`,
        ).toBe(true);
      }
    },
  );

  it("renders every commitment amount without throwing", () => {
    // The regression itself. Before the fix this threw
    // "amount.split is not a function" and took the whole route down.
    const { commitments } = caseDetail as unknown as CaseResponse;
    for (const commitment of commitments) {
      for (const field of ["committed_amount", "fulfilled_amount", "outstanding_amount"] as const) {
        const value = commitment[field];
        if (value === null) continue;
        expect(() => formatMoney(value)).not.toThrow();
        expect(formatMoney(value)).toMatch(/^[A-Z]{3} [\d,]+\.\d{2}$/);
      }
    }
  });

  it("formats the known amount exactly", () => {
    // Named, not counted: a shape assertion passes on the wrong number.
    const { commitments } = caseDetail as unknown as CaseResponse;
    const rendered = commitments
      .map((c) => (c.committed_amount === null ? null : formatMoney(c.committed_amount)))
      .filter((s): s is string => s !== null);
    expect(rendered).toContain("USD 420.00");
  });
});

describe("formatMoney refuses a shape it cannot format", () => {
  it("does not silently accept a bare string where Money is required", () => {
    // Positive control for the guard above: if `formatMoney` ever grew a
    // permissive branch that coerced anything, the tests above would pass on a
    // payload they are meant to reject.
    // @ts-expect-error -- deliberately passing the shape the old contract claimed.
    expect(() => formatMoney("420.0000")).toThrow();
  });
});
