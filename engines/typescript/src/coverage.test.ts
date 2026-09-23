/**
 * Coverage reconciliation is byte-identical to the Python reference (SPEC §6.5, §8.3).
 *
 * `testdata/coverage-{fixture,golden}.json` were emitted by the reference engine's `compute_coverage`
 * over subjects that are covered, below threshold, and uncovered, exercising the `ok`/`gap`/`unknown`
 * roll-ups, repeating-decimal ratio rounding, and a manifest-declared denominator (read from
 * `testdata/coverage-bundle/`).
 */

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
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

// --- A denominator manifest is a bundle-adjacent reference: it must stay inside the bundle root
// --- after symlinks resolve, or it is not read at all (SPEC §6.5, §8.1).

const DENOMINATOR_SOURCE = "urn:src:egress";

/** One observed ToolCall plus two of the denominator source's own: the fallback expects 2. */
const EVENTS: Array<Record<string, unknown>> = [
  { source: "urn:src:agent", subject: "subj-A", data: { "@type": "ToolCall" } },
  { source: DENOMINATOR_SOURCE, subject: "subj-A", data: { "@type": "ToolCall" } },
  { source: DENOMINATOR_SOURCE, subject: "subj-A", data: { "@type": "ToolCall" } },
];

function profileWithManifest(manifest: string) {
  return profileFromDict({
    subjects: [
      {
        id: "subj-A",
        coverage_denominators: [
          { kind: "registry", source: DENOMINATOR_SOURCE, covers: ["ToolCall"], manifest },
        ],
      },
    ],
  });
}

/** The `expected` count the reconciliation used for `subj-A`'s ToolCall events. */
function expectedToolCalls(result: Record<string, unknown>): unknown {
  const subjects = result.subjects as Record<string, { event_types: Record<string, unknown> }>;
  const entry = subjects["subj-A"]?.event_types.ToolCall as { expected: unknown };
  return entry.expected;
}

function withTempBundle(body: (dir: string, root: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-coverage-"));
  try {
    const root = join(dir, "bundle");
    mkdirSync(join(root, "reference"), { recursive: true });
    body(dir, root);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("a denominator manifest escaping the bundle root with '..' is not read", () => {
  withTempBundle((dir, root) => {
    writeFileSync(join(dir, "secret.json"), '{"expected": {"ToolCall": 999999}}', "utf-8");

    const result = computeCoverage(EVENTS, profileWithManifest("../secret.json"), root);
    assert.equal(expectedToolCalls(result), 2); // the source's own ingested events, not the escape
  });
});

test("a denominator manifest whose symlink escapes the bundle root is not read", () => {
  withTempBundle((dir, root) => {
    const outside = join(dir, "secret.json");
    writeFileSync(outside, '{"expected": {"ToolCall": 999999}}', "utf-8");
    symlinkSync(outside, join(root, "reference", "counts.json"));

    const result = computeCoverage(EVENTS, profileWithManifest("reference/counts.json"), root);
    assert.equal(expectedToolCalls(result), 2);
  });
});

test("a denominator manifest inside the bundle root is still read", () => {
  withTempBundle((_dir, root) => {
    writeFileSync(join(root, "reference", "counts.json"), '{"expected": {"ToolCall": 7}}', "utf-8");

    const result = computeCoverage(EVENTS, profileWithManifest("reference/counts.json"), root);
    assert.equal(expectedToolCalls(result), 7);
  });
});

test("a denominator manifest that is a symlink inside the bundle root is still read", () => {
  withTempBundle((_dir, root) => {
    const real = join(root, "reference", "real-counts.json");
    writeFileSync(real, '{"counts": {"ToolCall": 5}}', "utf-8");
    symlinkSync(real, join(root, "reference", "counts.json"));

    const result = computeCoverage(EVENTS, profileWithManifest("reference/counts.json"), root);
    assert.equal(expectedToolCalls(result), 5);
  });
});
