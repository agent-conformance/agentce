/**
 * The full assess path (graph → catalog → structural → assertions) is byte-identical to the reference.
 *
 * `testdata/assess-golden.json` is the reference engine's `assess_subjects` output over the base
 * catalog for several event sets (a control's passed and failed fixtures, and an empty stream). The
 * comparison also covers evidence pointers, whose digests canonicalise the real events — so any drift
 * in the canonical form, the graph, the shape parse, or the evaluator would break it.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { type Assertion, assertionToJson, makeAssertion } from "./assertions";
import { applyDeviations, assessSubjects } from "./assess";
import { canonicalString } from "./canonical";
import { loadCatalog } from "./catalog";
import { DomainBinding } from "./domain";
import { InputError } from "./errors";
import { profileFromDict } from "./profile";
import { loadDeviationRegister } from "./readiness";

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

function deviationAssertion(control: string, outcome: string): Assertion {
  return makeAssertion({
    control,
    controlVersion: "2026.09",
    subject: SUBJECT,
    outcome,
    rung: 2,
    mode: "automated",
    window: ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"],
    population: [1, 1],
    severity: "high",
    family: "AUV",
  });
}

function deviationEntry(control: string, expiry: string): Record<string, unknown> {
  return {
    control,
    rationale: "r",
    compensating_control: "c",
    owner: "user:a@example.com",
    approver: "user:b@example.com",
    granted: "2025-06-01T00:00:00Z",
    expiry,
  };
}

const AS_OF = "2026-02-01T00:00:00Z";

test("applyDeviations applies an unexpired entry: non-conformant becomes partial and names the control", () => {
  const { assertions, expired } = applyDeviations(
    [deviationAssertion("AUV-01", "non-conformant")],
    [deviationEntry("AUV-01", "2026-06-01T00:00:00Z")],
    AS_OF,
  );
  assert.equal(assertions[0]?.outcome, "partial");
  assert.equal(assertions[0]?.deviation, "AUV-01");
  assert.deepEqual(expired, []);
});

test("applyDeviations ignores an expired entry and returns its control as expired", () => {
  const { assertions, expired } = applyDeviations(
    [deviationAssertion("AUV-02", "non-conformant")],
    [deviationEntry("AUV-02", "2025-11-01T00:00:00Z")],
    AS_OF,
  );
  assert.equal(assertions[0]?.outcome, "non-conformant");
  assert.equal(assertions[0]?.deviation, null);
  assert.deepEqual(expired, ["AUV-02"]);
});

test("applyDeviations leaves every outcome other than non-conformant untouched", () => {
  const inputs = ["conformant", "insufficient_evidence", "not_assessed", "not_applicable"].map(
    (o) => deviationAssertion("AUV-01", o),
  );
  const { assertions, expired } = applyDeviations(
    inputs,
    [deviationEntry("AUV-01", "2026-06-01T00:00:00Z")],
    AS_OF,
  );
  assert.deepEqual(assertions, inputs);
  assert.deepEqual(expired, []);
});

test("applyDeviations never mutates its input assertions or register", () => {
  const inputs = [deviationAssertion("AUV-01", "non-conformant")];
  const register = [deviationEntry("AUV-01", "2026-06-01T00:00:00Z")];
  const before = JSON.stringify([inputs, register]);
  const { assertions } = applyDeviations(inputs, register, AS_OF);
  assert.equal(JSON.stringify([inputs, register]), before);
  assert.notEqual(assertions[0], inputs[0]);
});

test("loadDeviationRegister refuses non-UTF-8 bytes instead of decoding them lossily", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-deviations-utf8-"));
  try {
    const path = join(dir, "reg.yaml");
    writeFileSync(
      path,
      Buffer.concat([
        Buffer.from("deviation_register_version: 1\ndeviations:\n  - control: AUV-"),
        Buffer.from([0xff]),
        Buffer.from("\n"),
      ]),
    );
    assert.throws(
      () => loadDeviationRegister(path),
      (error: unknown) =>
        error instanceof InputError &&
        error.key === "input.deviation_invalid" &&
        error.cause.startsWith(`the deviation register at '${path}' is not valid UTF-8`),
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
