/**
 * The typed record.
 *
 * The PROSE / TYPED RECORD control is a disclosure control, not a theme. TYPED RECORD
 * shows raw field-level state, and every token in it must come from a real row.
 *
 * That guarantee is structural rather than editorial. A typed record is not a string
 * and cannot be written as one. It is a list of {@link TypedField}, and the only way to
 * make a field is {@link field}, which takes a {@link RowSource} -- a table name, a row
 * id, and the row object itself -- plus the name of a column *on that row's type*. The
 * column name is checked by the compiler against `keyof T`, so a token can only name a
 * field the API contract actually defines.
 *
 * Three consequences follow, and they are the whole point:
 *
 *   1. `field(src, "revision")` renders whatever `src.row.revision` holds. There is no
 *      parameter through which a caller can supply the text instead.
 *   2. If the row is missing, or the column is `null`/`undefined`, the field is ABSENT.
 *      It renders as an explicit absence marker -- never `0`, never an empty string,
 *      never a plausible-looking placeholder.
 *   3. Every PRESENT field carries a {@link RowRef} naming the table, column, and row id
 *      it came from. That reference is emitted into the DOM as `data-row-ref`, so the
 *      claim "this token traces to a row" is checkable from outside the application.
 *
 * The remaining hole -- someone passing a hand-written object literal as `row` -- is
 * closed by `scripts/check-render-honesty.mjs`, which forbids literal canon values and
 * fixture imports anywhere under `src/app` and `src/components`.
 */

/** Where a token came from. Emitted into the DOM so the claim is falsifiable. */
export interface RowRef {
  readonly table: string;
  readonly column: string;
  readonly id: string | null;
}

export type AbsenceReason =
  /** The API returned no row at all for this subject. */
  | "NO_ROW"
  /** The row exists and the column is null. The system genuinely does not know. */
  | "NULL_COLUMN";

export interface PresentField {
  readonly state: "PRESENT";
  readonly key: string;
  readonly text: string;
  readonly ref: RowRef;
}

export interface AbsentField {
  readonly state: "ABSENT";
  readonly key: string;
  readonly ref: RowRef;
  readonly reason: AbsenceReason;
}

export type TypedField = PresentField | AbsentField;

export type TypedRecord = readonly TypedField[];

/**
 * A row from an API payload, tagged with the table it came from.
 *
 * `id` may be null when the payload nests an unidentified sub-object (a `counts` block,
 * for instance). The reference is still emitted; it just names no row id.
 */
export interface RowSource<T extends object> {
  readonly table: string;
  readonly id: string | null;
  readonly row: T | null | undefined;
}

/** Tag an API payload object with the table it was read from. */
export function row<T extends object>(
  table: string,
  value: T | null | undefined,
  id: string | null = null,
): RowSource<T> {
  return { table, id, row: value };
}

export interface FieldOptions<V> {
  /** Token name, if it should differ from the column name. */
  readonly key?: string;
  /** How to render the value. Receives the non-null value only. */
  readonly format?: (value: V) => string;
}

function defaultFormat(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return JSON.stringify(value);
}

/**
 * Project one column of one row into a token.
 *
 * There is deliberately no parameter that supplies the rendered text directly. The text
 * is `src.row[column]`, formatted, or it is an explicit absence.
 */
export function field<T extends object, K extends keyof T & string>(
  src: RowSource<T>,
  column: K,
  options: FieldOptions<NonNullable<T[K]>> = {},
): TypedField {
  const key = options.key ?? column;
  const ref: RowRef = { table: src.table, column, id: src.id };

  if (src.row === null || src.row === undefined) {
    return { state: "ABSENT", key, ref, reason: "NO_ROW" };
  }

  const value = src.row[column];
  if (value === null || value === undefined) {
    return { state: "ABSENT", key, ref, reason: "NULL_COLUMN" };
  }

  const format = options.format ?? defaultFormat;
  return {
    state: "PRESENT",
    key,
    text: format(value as NonNullable<T[K]>),
    ref,
  };
}

/**
 * Project a column that is itself an object, by naming a sub-key.
 *
 * Used for nested payload members that are not worth tagging as their own row -- an
 * `attributes` bag on a trace node, for example. The reference records the full path so
 * the token is still traceable to a column.
 */
export function nested<T extends object, K extends keyof T & string>(
  src: RowSource<T>,
  column: K,
  subKey: string,
  options: FieldOptions<unknown> = {},
): TypedField {
  const key = options.key ?? subKey;
  const ref: RowRef = { table: src.table, column: `${column}.${subKey}`, id: src.id };

  if (src.row === null || src.row === undefined) {
    return { state: "ABSENT", key, ref, reason: "NO_ROW" };
  }

  const bag = src.row[column];
  if (bag === null || bag === undefined || typeof bag !== "object") {
    return { state: "ABSENT", key, ref, reason: "NULL_COLUMN" };
  }

  const value = (bag as Record<string, unknown>)[subKey];
  if (value === null || value === undefined) {
    return { state: "ABSENT", key, ref, reason: "NULL_COLUMN" };
  }

  const format = options.format ?? defaultFormat;
  return { state: "PRESENT", key, text: format(value), ref };
}

/** Serialise a {@link RowRef} for the `data-row-ref` attribute. */
export function refToAttribute(ref: RowRef): string {
  return ref.id === null ? `${ref.table}.${ref.column}` : `${ref.table}.${ref.column}#${ref.id}`;
}

/** How many tokens in this record are backed by a row. */
export function presentCount(record: TypedRecord): number {
  return record.filter((f) => f.state === "PRESENT").length;
}

/** True when nothing in this record has a backing row. */
export function isWhollyAbsent(record: TypedRecord): boolean {
  return record.length > 0 && presentCount(record) === 0;
}
