import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/*
 * `process.cwd()` rather than `import.meta.url`: under the jsdom environment the module
 * URL is an http one, and `fileURLToPath` refuses it. Vitest runs from the package root,
 * which is the directory the checker expects to be pointed at anyway.
 */
const APP_ROOT = process.cwd();
const CHECKER = join(APP_ROOT, "scripts", "check-render-honesty.mjs");

function runChecker(args: readonly string[]): { status: number; output: string } {
  try {
    const output = execFileSync(process.execPath, [CHECKER, ...args], {
      cwd: APP_ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { status: 0, output };
  } catch (error) {
    const failure = error as { status?: number; stdout?: string; stderr?: string };
    return {
      status: failure.status ?? 1,
      output: `${failure.stdout ?? ""}${failure.stderr ?? ""}`,
    };
  }
}

/**
 * The render-honesty checker, bound to the test suite.
 *
 * The checker already runs inside `npm run build`, which is where it does its real work.
 * It is bound here as well for one reason: a build step can be removed by editing one line
 * of `package.json`, and that edit reads as tidying. Removing it from the test suite too
 * requires deleting a file whose name says what it was for.
 *
 * The second test is the one that matters. A check that has never failed is
 * indistinguishable from a check that cannot fail, so the checker carries a counterfactual
 * mode: it materialises a throwaway tree containing one deliberate violation per rule,
 * runs the same engine over it, and exits non-zero unless every rule fires. This asserts
 * that the counterfactual passes, which is to say: the checker demonstrably catches
 * dishonest source, on every run, rather than being trusted to.
 */

describe("the render-honesty checker", () => {
  it("passes over this application's own source", () => {
    const { status, output } = runChecker([]);
    expect(output).toContain("0 violations");
    expect(status).toBe(0);
  });

  it("fails on dishonest source: every rule fires on a planted violation", () => {
    const { status, output } = runChecker(["--counterfactual"]);

    expect(output).toContain("COUNTERFACTUAL PASSED");
    expect(status).toBe(0);

    /* Each rule named, each caught. A rule that silently stopped firing would leave a
       MISSED line here, and the checker's own exit code would already be non-zero. */
    for (const rule of [
      "R1 FIXTURE_CONTAINMENT",
      "R2 NO_UUID_LITERAL",
      "R3 NO_CANON_LITERAL",
      "R4 ABSENCE_GLYPH_SCOPE",
      "R5 FIXTURE_NAMING",
      "R6 NO_VALUE_FALLBACK",
    ]) {
      expect(output, `${rule} did not fire`).toContain(`CAUGHT  ${rule}`);
    }
    expect(output).not.toContain("MISSED");
  });

  it("still enforces G12.3 verbatim: zero identifier literals outside tests and fixtures", () => {
    /*
     * G12.3 is a grep, and the checker's R2 is that grep. Restating it here means the
     * gate's assertion and the build's assertion cannot drift apart without one of them
     * going red.
     */
    const { output } = runChecker([]);
    expect(output).toContain("R2 no identifier literals");
  });
});
