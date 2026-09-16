/**
 * Coverage reconciliation is byte-identical to the Python reference (SPEC §6.5, §8.3).
 *
 * `testdata/coverage-{fixture,golden}.json` were emitted by the reference engine's `compute_coverage`
 * over subjects that are covered, below threshold, and uncovered, exercising the `ok`/`gap`/`unknown`
 * roll-ups, repeating-decimal ratio rounding, and a manifest-declared denominator (read from
 * `testdata/coverage-bundle/`).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { canonicalString } from "./canonical";
import { computeCoverage } from "./coverage";
import { profileFromDict } from "./profile";

const TESTDATA = join(__dirname, "..", "testdata");

test("coverage reconciliation matches the Python reference golden", () => {
  const fixture = JSON.parse(readFileSync(join(TESTDATA, "coverage-fixture.json"), "utf-8"));
  const golden = JSON.parse(readFileSync(join(TESTDATA, "coverage-golden.json"), "utf-8"));
  const profile = profileFromDict(fixture.profile);
  const result = computeCoverage(fixture.events, profile, join(TESTDATA, "coverage-bundle"));
  assert.equal(canonicalString(result), canonicalString(golden));
});
