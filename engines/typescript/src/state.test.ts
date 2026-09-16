/**
 * The incremental-assessment state directory is byte-identical to the Python reference (SPEC §9.6).
 *
 * `testdata/state-golden.json` was produced by the reference engine's `StateDir`: record a report,
 * then plan a re-assessment against a changed bundle with a late event. The TypeScript port must write
 * the same `state.json` bytes (so a state directory is portable across engines), compute the same
 * manifest digest, and produce the same `supersedes` and per-stream late-event counts.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { canonicalString } from "./canonical";
import { STATE_VERSION, StateDir, windowEnd } from "./state";

const TESTDATA = join(__dirname, "..", "testdata");

test("state directory matches the Python reference golden", () => {
  const golden = JSON.parse(readFileSync(join(TESTDATA, "state-golden.json"), "utf-8"));
  const work = mkdtempSync(join(tmpdir(), "agentce-state-"));
  const manifest = join(work, "manifest.json");
  writeFileSync(manifest, golden.manifest_bytes);
  const stateDir = join(work, "s");

  const state = StateDir.load(stateDir);
  const digest = state.record(golden.bundle_a, manifest, golden.window_a);
  assert.equal(digest, golden.manifest_digest);
  assert.equal(readFileSync(join(stateDir, "state.json"), "utf-8"), golden.state_json_after_record);

  const reloaded = StateDir.load(stateDir);
  const [supersedes, late] = reloaded.plan(golden.bundle_b, golden.events_b, golden.window_b);
  assert.equal(canonicalString(supersedes), canonicalString(golden.supersedes));
  assert.equal(canonicalString(late), canonicalString(golden.late_events));
});

test("STATE_VERSION matches the reference and windowEnd prefers the declared end", () => {
  assert.equal(STATE_VERSION, 1);
  assert.equal(windowEnd({ end: "2026-03-31T00:00:00Z" }, []), "2026-03-31T00:00:00Z");
  assert.equal(
    windowEnd({}, [{ time: "2026-02-01T00:00:00.000Z" }, { time: "2026-01-01T00:00:00.000Z" }]),
    "2026-02-01T00:00:00.000Z",
  );
});
