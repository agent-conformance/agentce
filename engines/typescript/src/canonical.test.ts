/** The TypeScript canonical form reproduces the shared vectors byte for byte (SPEC §6.7). */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { CanonicalizationError, canonicalString, sha256Hex } from "./canonical";

const VECTORS = join(__dirname, "..", "..", "..", "spec", "model", "test-vectors");
const files = readdirSync(VECTORS)
  .filter((f) => f.endsWith(".json"))
  .sort();

test("the shared vector set is present", () => {
  assert.ok(files.length >= 50);
});

for (const file of files) {
  test(`canonical vector ${file}`, () => {
    const vector = JSON.parse(readFileSync(join(VECTORS, file), "utf-8"));
    if ("error" in vector) {
      assert.throws(
        () => canonicalString(vector.input),
        (err: unknown) => err instanceof CanonicalizationError && err.reason === vector.error,
      );
    } else {
      assert.equal(canonicalString(vector.input), vector.canonical);
      assert.equal(sha256Hex(vector.input), vector.sha256);
    }
  });
}
