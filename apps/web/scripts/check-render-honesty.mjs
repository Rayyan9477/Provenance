#!/usr/bin/env node
/**
 * check-render-honesty.mjs -- the L-RENDER lens, as a build step.
 *
 * The rule this enforces: nothing renders that has no backing row. No hard-coded id,
 * no fixture masquerading as live data, no placeholder that looks like a value.
 *
 * The rule is easy to state and easy to violate by accident, so it is checked
 * mechanically rather than by review. `npm run build` runs this first; a violation
 * fails the build.
 *
 * Five rules:
 *
 *   R1 FIXTURE_CONTAINMENT   Fixtures may be imported only by tests, stories, and the
 *                            single declared adapter. A component that imports a fixture
 *                            renders data with no backing row.
 *   R2 NO_UUID_LITERAL       G12.3, verbatim. A hard-coded id is a rendered lie.
 *   R3 NO_CANON_LITERAL      Hero-dataset values (names, ids, amounts, dates) must not
 *                            appear in rendering source. If Alex Rivera's name is typed
 *                            into a component, that component is not rendering the record.
 *   R4 ABSENCE_GLYPH_SCOPE   The absence marker may only be produced by the one primitive
 *                            that means "we do not have this". Sprinkled elsewhere it
 *                            becomes decoration, and decoration cannot be distinguished
 *                            from a missing value.
 *   R5 FIXTURE_NAMING        Everything under src/fixtures must be named *.fixture.ts, so
 *                            a fixture is identifiable from its path alone.
 *   R6 NO_VALUE_FALLBACK     A missing value may not fall back to something that reads as a
 *                            value. `?? 0` on an amount, `?? "N/A"` on a status, or a
 *                            formatter falling back to "" all put a plausible thing on
 *                            screen where the truth is "we do not have this". This is the
 *                            failure the whole rule exists to prevent, and it is the one
 *                            that survives review, because the code looks defensive.
 *
 * Run `--counterfactual` to prove the checker fails on dishonest code: it materialises a
 * throwaway tree containing one deliberate violation per rule, runs the same engine over
 * it, and exits non-zero unless every rule fired.
 */

import {
  readdirSync,
  readFileSync,
  statSync,
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { join, relative, sep, posix } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const APP_ROOT = join(HERE, "..");

/* ---------------------------------------------------------------- rule inputs */

const SOURCE_EXTENSIONS = [".ts", ".tsx"];

/** Paths permitted to import a fixture. Everything else is a violation. */
const FIXTURE_IMPORT_ALLOWLIST = [
  /(^|\/)__tests__\//,
  /\.test\.tsx?$/,
  /\.stories\.tsx?$/,
  /^src\/fixtures\//,
  /^src\/lib\/api\/fixture-source\.ts$/,
  /^scripts\//,
];

/** G12.3's pattern, unchanged. */
const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

/**
 * G12.3's own exclusions, unchanged, and the exclusion list for R3, R4 and R6 as well.
 *
 * A test is the one place that must be allowed to write the forbidden thing. The
 * grayscale suite asserts the fixture-mode banner copy character for character, and that
 * copy contains the em dash R4 reserves for the absence primitive. A test proving R6 fires
 * has to contain the fallback R6 forbids. Excluding tests is not a loophole in the rule;
 * it is what makes the rule testable, and the checker's own counterfactual mode is the
 * proof that the exclusion has not hollowed it out.
 */
const UUID_EXEMPT = [/(^|\/)__tests__\//, /\.test\.tsx?$/, /\.fixture\.tsx?$/];

/**
 * Hero-dataset canon, from CANONICAL_DECISIONS.md. These are the values the demo is
 * about. Every one of them must arrive from the API at runtime. Finding one typed into a
 * rendering module means that module renders a constant and would keep rendering it after
 * the database changed -- which is precisely what G12.4's mutation probe exists to catch.
 */
const CANON_LITERALS = [
  "Alex Rivera",
  "Northline Fiber",
  "Harborview",
  "Beltline",
  "Kestrel",
  "Cascade Power",
  "NF-4471-8802",
  "NF-9913-2250",
  "HPM-LEASE-2024-3B",
  "BM-88214",
  "KA-EMP-3308",
  "CP-770194",
  "2,020.00",
  "1,800.00",
  "2020.0000",
  "1800.0000",
  "186.00",
  "TRG-64",
  "TRG-77",
  "CMT-208",
  "X-31",
  "A-4412",
  "16,035",
  "16035",
  "18035",
  "18,035",
  "n7k4q9wv2x",
];

/** Directories whose job is to render. Canon literals are forbidden here. */
const RENDERING_ROOTS = ["src/app/", "src/components/", "src/lib/"];

/** Rendering source may not contain these; only the owners below may. */
const ABSENCE_GLYPHS = ["—"]; // em dash

/**
 * Who may write the glyph, and why. Both entries are deliberate, and each one is a
 * decision rather than an escape hatch:
 *
 *   Absent.tsx  owns the glyph's meaning. It is the absence marker.
 *   Banners.tsx carries `DEMO FIXTURE MODE — model outputs are replayed`, whose copy is
 *               fixed verbatim by G12.7 and asserted character-for-character by the gate.
 *               The dash there is punctuation in mandated text, not a rendered value.
 */
const ABSENCE_GLYPH_OWNERS = [
  "src/components/primitives/Absent.tsx",
  "src/components/primitives/Banners.tsx",
];

/**
 * R6's patterns. Each is a way of writing "if we do not have it, print this instead",
 * where the replacement is indistinguishable from a real value.
 *
 * `?? 0` is the worst of them, because a zero amount is a true and important statement
 * about a disputed balance, and a zero standing in for a number we failed to fetch is a
 * lie that renders identically.
 *
 * A formatter falling back to the empty string is the quiet version: the cell is blank,
 * the reader concludes the record holds nothing, and the record holds a timestamp that
 * could not be formatted. `formatDateOrRaw` exists so that call has an honest spelling.
 */
const VALUE_FALLBACK_PATTERNS = [
  { pattern: /\?\?\s*0(?![.\d])/, name: "?? 0" },
  { pattern: /\|\|\s*0(?![.\d])/, name: "|| 0" },
  { pattern: /\?\?\s*"0/, name: '?? "0..."' },
  {
    /*
     * Absence rendered as prose. "not recorded" in a mono token is indistinguishable from
     * a value, it duplicates the meaning that <Absent /> owns (rule R4), and it carries no
     * accessible description and no row reference. Where the row genuinely holds nothing,
     * <Absent /> is the spelling; where it holds something unformattable, render it raw.
     */
    pattern: /\?\?\s*"(?:N\/A|n\/a|TBD|tbd|--?|unknown|none|never|not [a-z ]+)"/,
    name: "?? placeholder string",
  },
  {
    pattern: /\bformat[A-Za-z]*\([^()]*(?:\([^()]*\))?[^()]*\)\s*\?\?\s*""/,
    name: 'formatter ?? ""',
  },
];

const IMPORT_PATTERN =
  /(?:import|export)[\s\S]*?from\s*["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)/g;

/* ---------------------------------------------------------------- engine */

function walk(dir, root, out = []) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const entry of entries) {
    if (entry === "node_modules" || entry === ".next" || entry === "coverage") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      walk(full, root, out);
    } else if (SOURCE_EXTENSIONS.some((ext) => entry.endsWith(ext))) {
      out.push(full);
    }
  }
  return out;
}

function toPosix(p) {
  return p.split(sep).join(posix.sep);
}

function matchesAny(relPath, patterns) {
  return patterns.some((p) => p.test(relPath));
}

/** Strip line and block comments so a rule name in a doc comment is not a violation. */
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`\\])\/\/[^\n]*/g, "$1");
}

function lineOf(source, index) {
  return source.slice(0, index).split("\n").length;
}

function checkFile(relPath, rawSource) {
  const violations = [];
  const source = stripComments(rawSource);

  // R1 -- fixture containment.
  if (!matchesAny(relPath, FIXTURE_IMPORT_ALLOWLIST)) {
    IMPORT_PATTERN.lastIndex = 0;
    let m;
    while ((m = IMPORT_PATTERN.exec(source)) !== null) {
      const spec = m[1] ?? m[2];
      if (!spec) continue;
      if (/(^|\/)fixtures(\/|$)/.test(spec) || /\.fixture(\.|$)/.test(spec)) {
        violations.push({
          rule: "R1 FIXTURE_CONTAINMENT",
          file: relPath,
          line: lineOf(source, m.index),
          detail: `imports "${spec}". Only tests, stories, and ${"src/lib/api/fixture-source.ts"} may import a fixture.`,
        });
      }
    }
  }

  // R2 -- no UUID literals (G12.3).
  if (!matchesAny(relPath, UUID_EXEMPT)) {
    const lines = rawSource.split("\n");
    lines.forEach((line, i) => {
      const hit = UUID_PATTERN.exec(line);
      if (hit) {
        violations.push({
          rule: "R2 NO_UUID_LITERAL",
          file: relPath,
          line: i + 1,
          detail: `contains the identifier literal "${hit[0]}". G12.3 requires zero.`,
        });
      }
    });
  }

  // R3 -- no hero-canon literals in rendering source.
  if (
    RENDERING_ROOTS.some((root) => relPath.startsWith(root)) &&
    !matchesAny(relPath, UUID_EXEMPT)
  ) {
    for (const literal of CANON_LITERALS) {
      const idx = source.indexOf(literal);
      if (idx !== -1) {
        violations.push({
          rule: "R3 NO_CANON_LITERAL",
          file: relPath,
          line: lineOf(source, idx),
          detail: `contains the hero-canon value "${literal}". It must arrive from the API, not from source.`,
        });
      }
    }
  }

  // R4 -- absence glyph scope.
  if (
    !ABSENCE_GLYPH_OWNERS.includes(relPath) &&
    !matchesAny(relPath, UUID_EXEMPT) &&
    RENDERING_ROOTS.some((root) => relPath.startsWith(root))
  ) {
    for (const glyph of ABSENCE_GLYPHS) {
      const idx = source.indexOf(glyph);
      if (idx !== -1) {
        violations.push({
          rule: "R4 ABSENCE_GLYPH_SCOPE",
          file: relPath,
          line: lineOf(source, idx),
          detail: `renders the absence glyph directly. Use <Absent />, so "we do not have this" is one component with one meaning.`,
        });
      }
    }
  }

  // R6 -- no value-shaped fallback for a missing value.
  if (
    !matchesAny(relPath, UUID_EXEMPT) &&
    RENDERING_ROOTS.some((root) => relPath.startsWith(root))
  ) {
    const lines = source.split("\n");
    lines.forEach((line, i) => {
      for (const { pattern, name } of VALUE_FALLBACK_PATTERNS) {
        if (pattern.test(line)) {
          violations.push({
            rule: "R6 NO_VALUE_FALLBACK",
            file: relPath,
            line: i + 1,
            detail: `falls back to a value-shaped default (${name}). Render <Absent /> for "we do not have this", or the stored value itself where one exists.`,
          });
        }
      }
    });
  }

  // R5 -- fixture naming.
  if (relPath.startsWith("src/fixtures/") && !/\.fixture\.tsx?$/.test(relPath)) {
    violations.push({
      rule: "R5 FIXTURE_NAMING",
      file: relPath,
      line: 1,
      detail: `must be named *.fixture.ts so a fixture is identifiable from its path alone.`,
    });
  }

  return violations;
}

function run(root, { scanScripts = true } = {}) {
  const targets = [join(root, "src")];
  if (scanScripts) targets.push(join(root, "scripts"));
  const files = targets.flatMap((t) => walk(t, root));
  const violations = [];
  for (const file of files) {
    const relPath = toPosix(relative(root, file));
    violations.push(...checkFile(relPath, readFileSync(file, "utf8")));
  }
  return { fileCount: files.length, violations };
}

/* ---------------------------------------------------------------- counterfactual */

/**
 * The proof that the checker is load-bearing.
 *
 * A check that has never failed is indistinguishable from a check that cannot fail. This
 * builds a throwaway tree in which each rule is violated exactly once, runs the same
 * engine over it, and requires every rule to fire.
 */
function counterfactual() {
  const dir = mkdtempSync(join(tmpdir(), "pv-render-honesty-"));
  try {
    const write = (rel, body) => {
      const full = join(dir, rel);
      mkdirSync(join(full, ".."), { recursive: true });
      writeFileSync(full, body, "utf8");
    };

    // R1: a component reaching into the fixture module.
    write(
      "src/components/DishonestImport.tsx",
      [
        'import { heroDashboard } from "@/fixtures/hero.fixture";',
        "export const x = heroDashboard;",
        "",
      ].join("\n"),
    );

    // R2: a hard-coded object identifier.
    write(
      "src/components/DishonestId.tsx",
      ['export const CASE = "018f8a10-4c22-7f31-9b7d-2ac1e5f09b41";', ""].join("\n"),
    );

    // R3: the hero total typed into a component, so it survives a database change.
    write(
      "src/components/DishonestTotal.tsx",
      ["export function Total() {", "  return <p>USD 2,020.00</p>;", "}", ""].join("\n"),
    );

    // R4: an absence glyph outside the primitive that owns the meaning.
    write("src/components/DishonestDash.tsx", ['export const gap = "—";', ""].join("\n"));

    // R5: a fixture whose path does not say it is one.
    write("src/fixtures/data.ts", ["export const data = {};", ""].join("\n"));

    /*
     * R6: an outstanding balance that falls back to zero when the amount is missing.
     *
     * This is the violation worth planting, because it is the one that survives review.
     * It reads as careful defensive code, it never throws, and it renders a number that
     * is indistinguishable from a real balance of zero -- which on this product's hero
     * case is itself a true and load-bearing value. A reader cannot tell the two apart,
     * and neither can a reviewer.
     */
    write(
      "src/components/DishonestFallback.tsx",
      [
        "export function Outstanding({ amount }: { amount: number | null }) {",
        '  return <p className="pv-figure">USD {amount ?? 0}</p>;',
        "}",
        "",
      ].join("\n"),
    );

    const { violations } = run(dir, { scanScripts: false });
    const fired = new Set(violations.map((v) => v.rule));
    const expected = [
      "R1 FIXTURE_CONTAINMENT",
      "R2 NO_UUID_LITERAL",
      "R3 NO_CANON_LITERAL",
      "R4 ABSENCE_GLYPH_SCOPE",
      "R5 FIXTURE_NAMING",
      "R6 NO_VALUE_FALLBACK",
    ];
    const missing = expected.filter((r) => !fired.has(r));

    process.stdout.write("render-honesty counterfactual\n");
    process.stdout.write(`  planted 6 violations across 6 files in a throwaway tree\n`);
    for (const rule of expected) {
      const hits = violations.filter((v) => v.rule === rule);
      process.stdout.write(
        `  ${fired.has(rule) ? "CAUGHT " : "MISSED "} ${rule}  (${hits.length} finding${hits.length === 1 ? "" : "s"})\n`,
      );
      for (const h of hits)
        process.stdout.write(`            ${h.file}:${h.line} -- ${h.detail}\n`);
    }

    if (missing.length > 0) {
      process.stderr.write(
        `\nCOUNTERFACTUAL FAILED: the checker did not catch ${missing.join(", ")}.\n` +
          `A check that cannot fail proves nothing.\n`,
      );
      return 1;
    }
    process.stdout.write("\nCOUNTERFACTUAL PASSED: every rule fires on dishonest source.\n");
    return 0;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/* ---------------------------------------------------------------- main */

const isCounterfactual = process.argv.includes("--counterfactual");

if (isCounterfactual) {
  process.exit(counterfactual());
}

const { fileCount, violations } = run(APP_ROOT);

if (violations.length === 0) {
  process.stdout.write(
    `render-honesty: ${fileCount} source files scanned, 0 violations across 6 rules.\n` +
      `  R1 fixture containment  R2 no identifier literals  R3 no hero-canon literals\n` +
      `  R4 absence-glyph scope  R5 fixture naming       R6 no value-shaped fallback\n`,
  );
  process.exit(0);
}

process.stderr.write(
  `render-honesty: ${violations.length} violation(s) in ${fileCount} files.\n\n`,
);
for (const v of violations) {
  process.stderr.write(`  ${v.rule}\n    ${v.file}:${v.line}\n    ${v.detail}\n\n`);
}
process.stderr.write(
  "Nothing renders that has no backing row. Where data is unavailable the UI says so;\n" +
    "it does not invent a plausible value.\n",
);
process.exit(1);
