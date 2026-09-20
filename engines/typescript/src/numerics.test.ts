/**
 * The exact numerics kernel meets the cross-language vectors (SPEC §7.2, `spec/rules/numerics.md`).
 *
 * `spec/rules/numerics-vectors/cases/*.json` are the golden both engines conform to: the nearest-rank
 * quantile, exact comparison, the regularised incomplete beta, the Beta quantile, and the
 * Clopper–Pearson interval. The TypeScript kernel must reproduce every published digit (P3.1).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { computeVectorFile } from "./numerics";

const CASES = join(__dirname, "..", "..", "..", "spec", "rules", "numerics-vectors", "cases");
const FILES = [
  "quantile.json",
  "compare.json",
  "clopper-pearson.json",
  "incomplete-beta.json",
  "beta-quantile.json",
  "edge-comparison.json",
  "edge-canonical-numbers.json",
];

for (const file of FILES) {
  test(`numerics vectors: ${file}`, () => {
    const data = JSON.parse(readFileSync(join(CASES, file), "utf-8"));
    const results = computeVectorFile(data);
    for (const c of data.cases) {
      const expected =
        data.algorithm === "clopper_pearson" ? { lower: c.lower, upper: c.upper } : c.expected;
      assert.deepEqual(results[c.name], expected, `${file} / ${c.name}`);
    }
  });
}
