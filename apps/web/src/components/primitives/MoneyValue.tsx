import { Absent } from "@/components/primitives/Absent";
import { formatMoney } from "@/lib/format";
import type { Money } from "@/lib/api/contract";

/**
 * One amount, or an honest statement that there is no amount.
 *
 * Why this exists as a component rather than a `?:` at each call site
 * -------------------------------------------------------------------
 * A commitment can be **non-monetary** -- an obligation to send a letter, attend an
 * inspection, restore a service. The API sends `currency`, `committed_amount`,
 * `fulfilled_amount` and `outstanding_amount` all as `null` for those, and the corpus
 * contains one. The three fields were declared non-null, so the relationship file threw
 *
 *     TypeError: Cannot read properties of null (reading 'currency')
 *
 * and that whole counterparty's record was unreachable.
 *
 * The obvious repair -- `formatMoney(amount ?? ZERO)` -- is the dangerous one, and it is
 * dangerous in the specific way this product is about. `USD 0.00 outstanding` says the
 * obligation is **discharged**. `null` says it was never denominated in money at all.
 * Those are opposite answers to "does this counterparty still owe you something", which
 * is the one question the whole record exists to answer. A component makes the
 * distinction the default and a `??` fallback the thing you have to go out of your way
 * to write.
 *
 * `Absent` is the only component permitted to render the absence glyph (render-honesty
 * rule R4), so the non-monetary case routes through it and inherits its accessible name
 * rather than printing a bare dash.
 */

export interface MoneyValueProps {
  readonly amount: Money | null;
  /**
   * What the absence means here. Defaults to the non-monetary reading, because that is
   * what a null amount means on a commitment; a surface where null means "we failed to
   * read it" must say so, since those are different facts.
   */
  readonly absentDescribe?: string;
  readonly className?: string;
}

export function MoneyValue({
  amount,
  absentDescribe = "not a monetary commitment: no amount is denominated",
  className = "pv-mono",
}: MoneyValueProps) {
  if (amount === null) {
    return <Absent describe={absentDescribe} />;
  }
  return (
    <span className={className} data-money-currency={amount.currency}>
      {formatMoney(amount)}
    </span>
  );
}
