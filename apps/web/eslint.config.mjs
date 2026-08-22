import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

/**
 * Layer 1 of the render-honesty rule. `scripts/check-render-honesty.mjs` is the
 * authoritative enforcement (it runs in `npm run build`); this rule exists so the
 * violation is visible in the editor rather than only at build time.
 */
const fixtureContainment = {
  files: ["src/**/*.ts", "src/**/*.tsx"],
  ignores: [
    "src/**/__tests__/**",
    "src/**/*.test.ts",
    "src/**/*.test.tsx",
    "src/**/*.stories.tsx",
    "src/fixtures/**",
    "src/lib/api/fixture-source.ts",
  ],
  rules: {
    "no-restricted-imports": [
      "error",
      {
        patterns: [
          {
            group: ["**/fixtures/*", "@/fixtures/*", "@/fixtures"],
            message:
              "Fixtures may only be imported by tests, stories, or src/lib/api/fixture-source.ts. A component that imports a fixture renders data with no backing row (G12.x render honesty).",
          },
        ],
      },
    ],
  },
};

export default [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  fixtureContainment,
  { ignores: [".next/**", "node_modules/**", "coverage/**"] },
];
