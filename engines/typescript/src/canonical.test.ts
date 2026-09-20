/** The TypeScript canonical form reproduces the shared vectors byte for byte (SPEC §6.7). */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { CanonicalizationError, canonicalString, sha256Hex } from "./canonical";
import { NonCanonicalNumber, parseJson } from "./json";

const VECTORS = join(__dirname, "..", "..", "..", "spec", "model", "test-vectors");
const files = readdirSync(VECTORS)
  .filter((f) => f.endsWith(".json"))
  .sort();

test("the shared vector set is present", () => {
  assert.ok(files.length >= 50);
});

for (const file of files) {
  test(`canonical vector ${file}`, () => {
    const vector = parseJson(readFileSync(join(VECTORS, file), "utf-8")) as Record<string, unknown>;
    // A vector that carries `input_json` gives the input as JSON text, so that a number token such as
    // `1.0` or `1e2` reaches the engine as written rather than as the value a parser folds it to.
    const input =
      typeof vector.input_json === "string" ? parseJson(vector.input_json) : vector.input;
    if ("error" in vector) {
      assert.throws(
        () => canonicalString(input),
        (err: unknown) => err instanceof CanonicalizationError && err.reason === vector.error,
      );
    } else {
      assert.equal(canonicalString(input), vector.canonical);
      assert.equal(sha256Hex(input), vector.sha256);
    }
  });
}

test("parseJson reads what JSON.parse reads when every number is a canonical integer", () => {
  const text =
    '{"a":[1,-2,0,9007199254740991,-9007199254740991],"b":{"c":"x\\n\\u00e9"},"d":null,"e":true}';
  assert.deepEqual(parseJson(text), JSON.parse(text));
  const slow = '{"n":1.5,"a":[1,-2,0,9007199254740991],"s":"1.0"}';
  assert.deepEqual(Object.keys(parseJson(slow) as object), ["n", "a", "s"]);
});

test("parseJson keeps a number token the canonical grammar refuses", () => {
  const cases: Array<[string, string]> = [
    ["1.0", "non_integer_number"],
    ["1e2", "non_integer_number"],
    ["1E2", "non_integer_number"],
    ["-0.0", "non_integer_number"],
    ["9007199254740992", "integer_out_of_range"],
    ["-9007199254740992", "integer_out_of_range"],
    ["10000000000000001", "integer_out_of_range"],
    ["123456789012345678901234567890", "integer_out_of_range"],
  ];
  for (const [token, reason] of cases) {
    const parsed = parseJson(`[${token}]`) as unknown[];
    assert.ok(parsed[0] instanceof NonCanonicalNumber, token);
    assert.equal((parsed[0] as NonCanonicalNumber).reason, reason, token);
    assert.throws(
      () => canonicalString(parsed),
      (err: unknown) => err instanceof CanonicalizationError && err.reason === reason,
      token,
    );
  }
});

test("parseJson accepts -0 as the integer zero and refuses malformed text", () => {
  const parsed = parseJson("[-0,1.5]") as unknown[];
  assert.ok(Object.is(parsed[0], 0));
  for (const bad of [
    "[1.0,",
    '{"a":1.0,}',
    "[01.5]",
    "1.0 2",
    "[1.0}",
    '{"a" 1.5}',
    '"unterminated 1.0',
  ]) {
    assert.throws(() => parseJson(bad), SyntaxError, bad);
  }
});

test("parseJson keeps a __proto__ member an ordinary own property", () => {
  const parsed = parseJson('{"__proto__":{"x":1.5},"a":1}') as Record<string, unknown>;
  assert.deepEqual(Object.keys(parsed), ["__proto__", "a"]);
  assert.equal(Object.getPrototypeOf(parsed), Object.prototype);
});

test("an integer JavaScript number beyond the safe range is out of range, not non-integer", () => {
  assert.throws(
    () => canonicalString(2 ** 53),
    (err: unknown) => err instanceof CanonicalizationError && err.reason === "integer_out_of_range",
  );
  assert.throws(
    () => canonicalString(1.5),
    (err: unknown) => err instanceof CanonicalizationError && err.reason === "non_integer_number",
  );
});
