/**
 * Quarantine records write deterministically, and an unwritable `--out` raises the keyed
 * `input.out_dir_unwritable` error (loophole L18.6), not the `internal.unexpected` catch-all.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { InputError } from "./errors";
import { QuarantineReason, writeQuarantine } from "./quarantine";
import { withUnwritableDir } from "./testSupport";

test("writeQuarantine writes one JSON object per line", () => {
  const work = mkdtempSync(join(tmpdir(), "agentce-quarantine-"));
  const path = join(work, "out", "quarantine.jsonl");
  const written = writeQuarantine([{ reason: QuarantineReason.DUPLICATE_ID, eventId: "x" }], path);
  assert.equal(written, 1);
  const line = readFileSync(path, "utf-8").trim();
  assert.deepEqual(JSON.parse(line), { event_id: "x", reason: "duplicate_id" });
  rmSync(work, { recursive: true, force: true });
});

test("writeQuarantine raises a keyed error on an unwritable --out directory", () => {
  withUnwritableDir("agentce-quarantine-", (ro) => {
    assert.throws(
      () => writeQuarantine([], join(ro, "out", "quarantine.jsonl")),
      (err: unknown) => err instanceof InputError && err.key === "input.out_dir_unwritable",
    );
  });
});

test("writeQuarantine raises on a pre-existing unwritable --out directory, not only a missing parent", () => {
  withUnwritableDir("agentce-quarantine-", (out) => {
    assert.throws(
      () => writeQuarantine([], join(out, "quarantine.jsonl")),
      (err: unknown) => err instanceof InputError && err.key === "input.out_dir_unwritable",
    );
  });
});
