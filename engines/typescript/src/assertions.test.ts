/**
 * Assertion serialisation, aggregation, and the DC-5 evidence rule (SPEC §9.2, §9.4).
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { OUTCOMES, aggregate, assertionToJson, checkDc5, makeAssertion } from "./assertions";
import { AgentceError } from "./errors";
import { denominatorIds, profileFromDict } from "./profile";

test("aggregate lists every outcome, even at zero", () => {
  const conformant = makeAssertion({
    control: "C1",
    controlVersion: "1",
    subject: "s",
    outcome: "conformant",
    rung: 2,
    mode: "test",
    window: ["a", "b"],
    population: [3, 0],
    severity: "high",
    family: "C",
    evidence: [{ ref: "r", digest: "sha256:d", sourceClass: "instrumented" }],
  });
  const counts = aggregate([conformant]);
  assert.equal(counts.conformant, 1);
  for (const outcome of OUTCOMES) {
    assert.equal(outcome in counts, true);
  }
});

test("assertionToJson nests window and population and omits empty collections", () => {
  const json = assertionToJson(
    makeAssertion({
      control: "C1",
      controlVersion: "1",
      subject: "s",
      outcome: "not_applicable",
      rung: 2,
      mode: "test",
      window: ["a", "b"],
      population: [0, 0],
      severity: "high",
      family: "C",
    }),
  );
  assert.deepEqual(json.window, { start: "a", end: "b" });
  assert.deepEqual(json.population, { applicable: 0, failed: 0 });
  assert.equal("evidence" in json, false);
  assert.equal("violations" in json, false);
});

test("checkDc5 refuses a supporting verdict with no evidence", () => {
  const bare = makeAssertion({
    control: "C1",
    controlVersion: "1",
    subject: "s",
    outcome: "non-conformant",
    rung: 2,
    mode: "test",
    window: ["a", "b"],
    population: [3, 1],
    severity: "high",
    family: "C",
  });
  assert.throws(
    () => checkDc5([bare]),
    (err: unknown) => {
      assert.ok(err instanceof AgentceError);
      assert.equal(err.key, "report.missing_evidence_pointer");
      return true;
    },
  );
});

test("profile parsing collects denominator source ids", () => {
  const profile = profileFromDict({
    subjects: [
      {
        id: "s",
        coverage_denominators: [
          { kind: "router", source: "r-1" },
          { kind: "log", source: "r-2" },
        ],
      },
    ],
  });
  assert.deepEqual([...denominatorIds(profile.subjects[0] as never)].sort(), ["r-1", "r-2"]);
});
