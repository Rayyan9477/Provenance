import type { TypedField, TypedRecord } from "@/lib/typed/record";
import { refToAttribute } from "@/lib/typed/record";
import { Absent } from "./Absent";

/**
 * Renders a {@link TypedRecord}.
 *
 * Every token emitted here is `key=value` where `value` came from `row[column]`, and the
 * element carries `data-row-ref="table.column#id"`. That attribute is the honesty
 * argument made checkable: a test, or a judge with the element inspector, can read the
 * claimed provenance of any token on screen and then go look for the row.
 *
 * A field with no backing row renders {@link Absent}, not a zero and not a blank. The
 * token still appears, because the fact that the system does not know something is itself
 * part of the record.
 */

function Token({ field }: { readonly field: TypedField }) {
  if (field.state === "ABSENT") {
    return (
      <span data-token={field.key}>
        <span className="pv-typed-key">{field.key}=</span>
        <Absent reason={field.reason} ref_={field.ref} />
      </span>
    );
  }
  return (
    <span data-token={field.key} data-row-ref={refToAttribute(field.ref)}>
      <span className="pv-typed-key">{field.key}=</span>
      <span className="pv-typed-value">{field.text}</span>
    </span>
  );
}

export interface TypedRecordBlockProps {
  readonly record: TypedRecord;
  /** Tokens per line. Defaults to three, matching the design's dense inset blocks. */
  readonly perLine?: number;
  readonly label?: string;
}

export function TypedRecordBlock({ record, perLine = 3, label }: TypedRecordBlockProps) {
  if (record.length === 0) {
    return (
      <div className="pv-typed" data-typed-record="empty">
        <span className="pv-typed-line">
          <Absent describe="no fields were returned for this subject" />
        </span>
      </div>
    );
  }

  const lines: TypedField[][] = [];
  for (let i = 0; i < record.length; i += perLine) {
    lines.push(record.slice(i, i + perLine));
  }

  return (
    <div
      className="pv-typed"
      data-typed-record="true"
      role="group"
      {...(label ? { "aria-label": label } : {})}
    >
      {lines.map((line, index) => (
        <span className="pv-typed-line" key={index}>
          {line.map((field, position) => (
            <span key={field.key}>
              {position > 0 ? " " : null}
              <Token field={field} />
            </span>
          ))}
        </span>
      ))}
    </div>
  );
}
