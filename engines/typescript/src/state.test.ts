/**
 * The incremental-assessment state directory is byte-identical to the Python reference (SPEC §9.6).
 *
 * `testdata/state-golden.json` was produced by the reference engine's `StateDir`: record a report,
 * then plan a re-assessment against a changed bundle with a late event. The TypeScript port must write
 * the same `state.json` bytes (so a state directory is portable across engines), compute the same
 * manifest digest, and produce the same `supersedes` and per-stream late-event counts.
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  linkSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { canonicalString } from "./canonical";
import { InputError } from "./errors";
import { STATE_VERSION, StateDir, windowEnd } from "./state";
import { withUnwritableDir } from "./testSupport";

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

test("a Python-written state.json with prior outcomes loads and saves unchanged", () => {
  const golden = JSON.parse(readFileSync(join(TESTDATA, "state-golden.json"), "utf-8"));
  const stateDir = mkdtempSync(join(tmpdir(), "agentce-state-"));
  writeFileSync(join(stateDir, "state.json"), golden.state_json_with_outcomes);
  StateDir.load(stateDir).save();
  assert.equal(
    readFileSync(join(stateDir, "state.json"), "utf-8"),
    golden.state_json_with_outcomes,
  );
});

test("STATE_VERSION matches the reference and windowEnd prefers the declared end", () => {
  assert.equal(STATE_VERSION, 1);
  assert.equal(windowEnd({ end: "2026-03-31T00:00:00Z" }, []), "2026-03-31T00:00:00Z");
  assert.equal(
    windowEnd({}, [{ time: "2026-02-01T00:00:00.000Z" }, { time: "2026-01-01T00:00:00.000Z" }]),
    "2026-02-01T00:00:00.000Z",
  );
  // 18.87: an empty time counts, so the end does not parse and a deviation register is refused.
  assert.equal(windowEnd({}, [{ time: "" }]), "");
  assert.equal(windowEnd({}, []), "1970-01-01T00:00:00Z");
});

test("StateDir.ensureWritable raises a keyed error on an unwritable --state directory", () => {
  withUnwritableDir("agentce-state-", (ro) => {
    assert.throws(
      () => StateDir.ensureWritable(join(ro, "state")),
      (err: unknown) => err instanceof InputError && err.key === "input.state_dir_unwritable",
    );
  });
});

test("StateDir.ensureWritable raises on a pre-existing unwritable --state directory, not only a missing parent", () => {
  withUnwritableDir("agentce-state-", (stateDir) => {
    assert.throws(
      () => StateDir.ensureWritable(stateDir),
      (err: unknown) => err instanceof InputError && err.key === "input.state_dir_unwritable",
    );
  });
});

test("StateDir.save raises a keyed error on an unwritable --state directory", () => {
  withUnwritableDir("agentce-state-", (stateDir) => {
    const state = StateDir.load(stateDir);
    assert.throws(
      () => state.save(),
      (err: unknown) => err instanceof InputError && err.key === "input.state_dir_unwritable",
    );
  });
});

/** A state directory with one recorded report; returns its state.json path. */
function saved(work: string): string {
  const manifest = join(work, "manifest.json");
  writeFileSync(manifest, "{}\n");
  StateDir.load(join(work, "state")).record("sha256:bbb", manifest, "2026-02-01T00:00:00Z");
  return join(work, "state", "state.json");
}

const refusedWith = (cause: string) => (err: unknown) =>
  err instanceof InputError &&
  err.key === "input.state_dir_unwritable" &&
  err.message.endsWith(cause);

test("ensureWritable refuses a FIFO state.json without opening it (18.184d)", () => {
  const work = mkdtempSync(join(tmpdir(), "agentce-state-"));
  mkdirSync(join(work, "state"));
  execFileSync("mkfifo", [join(work, "state", "state.json")]);
  assert.throws(
    () => StateDir.ensureWritable(join(work, "state")),
    refusedWith("state.json' is not a regular file."),
  );
});

test("ensureWritable refuses an unreadable state.json", () => {
  const work = mkdtempSync(join(tmpdir(), "agentce-state-"));
  const sj = saved(work);
  chmodSync(sj, 0);
  try {
    assert.throws(
      () => StateDir.ensureWritable(join(work, "state")),
      refusedWith("state.json': Permission denied"),
    );
  } finally {
    chmodSync(sj, 0o644);
  }
});

test("a dangling state.json is a fresh series and save replaces the link", () => {
  const work = mkdtempSync(join(tmpdir(), "agentce-state-"));
  mkdirSync(join(work, "state"));
  symlinkSync(join(work, "away", "state.json"), join(work, "state", "state.json"));
  StateDir.ensureWritable(join(work, "state"));
  assert.equal(StateDir.load(join(work, "state")).lastReportDigest, null);
  const sj = saved(work);
  assert.equal(lstatSync(sj).isFile(), true);
  assert.equal(existsSync(join(work, "away")), false);
});

test("save keeps a hard link outside --state and the mode of state.json", () => {
  const work = mkdtempSync(join(tmpdir(), "agentce-state-"));
  const sj = saved(work);
  chmodSync(sj, 0o600);
  const outside = join(work, "outside.json");
  linkSync(sj, outside);
  const before = readFileSync(outside, "utf-8");
  const state = StateDir.load(join(work, "state"));
  state.lastWindowEnd = "2026-03-01T00:00:00Z";
  state.save();
  assert.equal(readFileSync(outside, "utf-8"), before);
  assert.equal(statSync(outside).nlink, 1);
  assert.equal(statSync(sj).mode & 0o777, 0o600);
  assert.equal(JSON.parse(readFileSync(sj, "utf-8")).last_window_end, "2026-03-01T00:00:00Z");
});
