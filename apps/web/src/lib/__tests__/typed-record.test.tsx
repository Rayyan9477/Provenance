import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import {
  field,
  isWhollyAbsent,
  nested,
  presentCount,
  refToAttribute,
  row,
} from "@/lib/typed/record";
import type { TypedRecord } from "@/lib/typed/record";
import { TypedRecordBlock } from "@/components/primitives/TypedRecordBlock";
import { heroCases, heroStateProofs, CASE_ISP } from "@/fixtures/hero.fixture";

/**
 * The PROSE / TYPED RECORD guarantee, asserted rather than asserted-about.
 *
 * The claim the design makes is strong: in TYPED RECORD every token traces to a real row.
 * It is also the easiest claim in the whole product to fake, because a typed record looks
 * exactly the same whether it was projected from a payload or typed into a template.
 * `belief=balance_owed v2 status=DISPUTED confidence=0.7100` is nine tokens of pure trust.
 *
 * The guarantee is therefore structural, not editorial. A token cannot be written; it can
 * only be projected, by `field(src, column)`, where `column` is checked by the compiler
 * against `keyof T` and the text is `src.row[column]`. There is no parameter through which
 * a caller can supply the rendered text.
 *
 * These tests hold the three properties that follow: that the text is the row's value,
 * that a missing value is an absence rather than a plausible default, and that every
 * present token carries the table, column and row id it came from into the DOM, where it
 * can be checked from outside the application.
 */

describe("a typed token is a projection of a row, not a string", () => {
  it("renders the column's actual value", () => {
    const record = heroCases[CASE_ISP];
    expect(record).toBeDefined();
    if (record === undefined) return;

    const source = row("cases", record, record.case_id);
    const typed: TypedRecord = [field(source, "status"), field(source, "revision")];

    expect(typed[0]).toMatchObject({ state: "PRESENT", text: record.status });
    expect(typed[1]).toMatchObject({ state: "PRESENT", text: String(record.revision) });
  });

  it("carries table, column and row id on every present token", () => {
    const record = heroCases[CASE_ISP];
    if (record === undefined) return;

    const source = row("cases", record, record.case_id);
    const typed: TypedRecord = [field(source, "status"), field(source, "revision")];

    const { container } = render(<TypedRecordBlock record={typed} />);

    const refs = [...container.querySelectorAll("[data-row-ref]")].map((el) =>
      el.getAttribute("data-row-ref"),
    );
    expect(refs).toContain(`cases.status#${record.case_id}`);
    expect(refs).toContain(`cases.revision#${record.case_id}`);

    /*
     * The property that makes the claim falsifiable from outside: the id in the attribute
     * is the id of a row a reader can go and ask the database for. It is never
     * constructed here, only carried.
     */
    for (const ref of refs) {
      expect(ref).toContain(record.case_id);
    }
  });

  it("marks a null column ABSENT rather than defaulting it", () => {
    interface Row {
      readonly resolved_at: string | null;
      readonly title: string;
    }
    const source = row<Row>("cases", { resolved_at: null, title: "a case" }, "row-1");

    const absent = field(source, "resolved_at");
    expect(absent).toMatchObject({ state: "ABSENT", reason: "NULL_COLUMN" });
    expect(absent).not.toHaveProperty("text");
  });

  it("distinguishes a missing row from a null column", () => {
    interface Row {
      readonly revision: number;
    }
    const noRow = row<Row>("cases", null, null);
    const projected = field(noRow, "revision");

    expect(projected).toMatchObject({ state: "ABSENT", reason: "NO_ROW" });
    /*
     * The distinction is not cosmetic. NULL_COLUMN says the system holds a row and does
     * not know this fact about it. NO_ROW says the system has nothing at all. A reader
     * deciding whether to chase a counterparty needs to know which.
     */
    expect(refToAttribute({ table: "cases", column: "revision", id: null })).toBe("cases.revision");
  });

  it("projects a nested payload key with the full path in its reference", () => {
    interface Row {
      readonly detail: Record<string, unknown>;
    }
    const source = row<Row>("timeline", { detail: { reason_code: "RC_TEST" } }, "entry-1");

    const projected = nested(source, "detail", "reason_code", { key: "detail.reason_code" });
    expect(projected).toMatchObject({ state: "PRESENT", text: "RC_TEST" });
    expect(refToAttribute(projected.ref)).toBe("timeline.detail.reason_code#entry-1");
  });

  it("a nested key the payload does not carry is ABSENT, not an empty string", () => {
    interface Row {
      readonly detail: Record<string, unknown>;
    }
    const source = row<Row>("timeline", { detail: {} }, "entry-1");
    expect(nested(source, "detail", "reason_code")).toMatchObject({ state: "ABSENT" });
  });

  it("counts how much of a record is actually backed", () => {
    interface Row {
      readonly a: string | null;
      readonly b: string;
    }
    const source = row<Row>("t", { a: null, b: "value" }, "id");
    const record: TypedRecord = [field(source, "a"), field(source, "b")];

    expect(presentCount(record)).toBe(1);
    expect(isWhollyAbsent(record)).toBe(false);
    expect(isWhollyAbsent([field(source, "a")])).toBe(true);
  });
});

describe("the typed view of the hero belief traces to real rows", () => {
  /*
   * The design's example token string, checked against the payload it claims to come from.
   * Every part of `belief=... v2 status=... confidence=...` is read out of the state proof
   * rather than compared against a literal, because a literal here would be the same lie
   * this file exists to make impossible.
   */
  it("belief predicate, version, status and confidence are all payload fields", () => {
    const proof = heroStateProofs[CASE_ISP];
    expect(proof).toBeDefined();
    if (proof === undefined) return;

    const belief = proof.beliefs[0];
    expect(belief).toBeDefined();
    if (belief === undefined) return;

    const version = belief.current_version;
    const source = row("belief_versions", version, version.belief_version_id);

    const typed: TypedRecord = [
      field(source, "version_no"),
      field(source, "epistemic_status"),
      field(source, "belief_confidence"),
    ];

    for (const token of typed) {
      expect(token.state, `${token.key} must be backed by a row`).toBe("PRESENT");
    }

    const { container } = render(<TypedRecordBlock record={typed} />);
    const text = container.textContent ?? "";

    expect(text).toContain(`version_no=${version.version_no}`);
    expect(text).toContain(`epistemic_status=${version.epistemic_status}`);
    expect(text).toContain(`belief_confidence=${version.belief_confidence}`);

    /* Confidence is shown at source precision. Rounding it up would be a small,
       checkable dishonesty about how sure the system is. */
    expect(text).not.toContain("belief_confidence=0.71 ");
  });
});

/**
 * An empty collection is a fact, and it must not render as nothing.
 *
 * `field()` guarded null and undefined and stopped there. An empty array is
 * neither, so it reached the formatter, where `[].join(",")` produced the empty
 * string — and the dashboard, the first screen a reader sees, printed
 * `reason_codes=` followed by nothing on a case with no reason codes.
 *
 * "None were recorded" and "we could not read them" are different claims that
 * land in the same slot, which is why EMPTY_COLLECTION is its own reason rather
 * than being folded into NULL_COLUMN.
 */
describe("an empty collection is an explicit absence", () => {
  const src = { table: "cases", id: "case-1", row: { codes: [] as string[], name: "x" } };

  it("does not render an empty array as an empty string", () => {
    const f = field(src, "codes", { format: (c) => c.join(",") });
    expect(f.state).toBe("ABSENT");
    if (f.state === "ABSENT") expect(f.reason).toBe("EMPTY_COLLECTION");
  });

  it("distinguishes an empty collection from an unknown one", () => {
    const nullSrc = { table: "cases", id: "case-1", row: { codes: null } };
    const unknown = field(nullSrc as never, "codes" as never);
    const empty = field(src, "codes", { format: (c) => c.join(",") });
    expect(unknown.state).toBe("ABSENT");
    expect(empty.state).toBe("ABSENT");
    if (unknown.state === "ABSENT" && empty.state === "ABSENT") {
      expect(unknown.reason).not.toBe(empty.reason);
    }
  });

  it("treats a formatter that yields nothing the same way", () => {
    const f = field(src, "name", { format: () => "" });
    expect(f.state).toBe("ABSENT");
  });

  it("still renders a non-empty collection", () => {
    const has = { table: "cases", id: "case-1", row: { codes: ["COMMITMENT_OVERDUE"] } };
    const f = field(has, "codes", { format: (c) => c.join(",") });
    expect(f.state).toBe("PRESENT");
    if (f.state === "PRESENT") expect(f.text).toBe("COMMITMENT_OVERDUE");
  });

  it("still renders a genuine zero as a zero", () => {
    const zero = { table: "cases", id: "case-1", row: { revision: 0 } };
    const f = field(zero, "revision");
    expect(f.state).toBe("PRESENT");
    if (f.state === "PRESENT") expect(f.text).toBe("0");
  });
});
