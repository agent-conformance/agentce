/**
 * The full assess path (graph → catalog → structural → assertions) is byte-identical to the reference.
 *
 * `testdata/assess-golden.json` is the reference engine's `assess_subjects` output over the base
 * catalog for several event sets (a control's passed and failed fixtures, and an empty stream). The
 * comparison also covers evidence pointers, whose digests canonicalise the real events — so any drift
 * in the canonical form, the graph, the shape parse, or the evaluator would break it.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { assertionToJson } from "./assertions";
import { assessSubjects } from "./assess";
import { canonicalString } from "./canonical";
import { loadCatalog } from "./catalog";
import { DomainBinding } from "./domain";
import { profileFromDict } from "./profile";

const REPO = join(__dirname, "..", "..", "..");
const BASE = join(REPO, "spec", "catalogs", "base", "eu-ai-act");
const TESTDATA = join(__dirname, "..", "testdata");
const SUBJECT = "spiffe://corp/agents/a";

function eventsOf(controlId: string, kase: string): Array<Record<string, unknown>> {
  return readFileSync(join(BASE, "test", controlId, `${kase}.jsonl`), "utf-8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

test("assess_subjects over the base catalog matches the Python reference", () => {
  const golden = JSON.parse(readFileSync(join(TESTDATA, "assess-golden.json"), "utf-8"));
  const catalog = loadCatalog(BASE);
  const domain = DomainBinding.load(join(BASE, "test", "domain.yaml"));
  const profile = profileFromDict({
    subjects: [{ id: SUBJECT, role: "both" }],
    catalogs: ["base/eu-ai-act"],
  });

  const scenarios: Record<string, Array<Record<string, unknown>>> = {
    "ovs-passed": eventsOf("OVS-03", "passed"),
    "ovs-failed": eventsOf("OVS-03", "failed"),
    "rec-passed": eventsOf("REC-04", "passed"),
    empty: [],
  };

  const result: Record<string, unknown> = {};
  for (const [name, events] of Object.entries(scenarios)) {
    result[name] = assessSubjects(events, profile, [catalog], domain).map(assertionToJson);
  }
  assert.equal(canonicalString(result), canonicalString(golden));
});
