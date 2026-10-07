/**
 * The error-key registry, its loader, the trust-root shape causes and the digest-read key (18.80), the
 * same behaviour Python's `tests/test_error_catalogue.py` and Java's `ErrorCatalogueTest` pin;
 * `VG-I18N-ERROR-CATALOGUE` runs all three.
 */
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { digestOf } from "./cli";
import { catalogueGaps, errorCause, messageKeys } from "./errorCatalogue";
import { InputError } from "./errors";
import { loadCatalog, readCatalogFile } from "./messages";
import { loadTrustRoot } from "./verify";

const REPO = join(__dirname, "..", "..", "..");

function engineSources(): string[] {
  return readdirSync(__dirname)
    .filter((name) => name.endsWith(".ts") && !name.endsWith(".test.ts"))
    .map((name) => readFileSync(join(__dirname, name), "utf-8"));
}

test("the registry holds every errors.<key> pair of spec/i18n/messages.en.json", () => {
  const spec = JSON.parse(
    readFileSync(join(REPO, "spec", "i18n", "messages.en.json"), "utf-8"),
  ) as Record<string, string>;
  for (const [key, entry] of messageKeys()) {
    assert.equal(entry.cause, spec[`errors.${key}.cause`]);
    assert.equal(entry.fix, spec[`errors.${key}.fix`]);
  }
  assert.ok(messageKeys().has("input.digest_unreadable"));
  assert.equal(
    errorCause("input.trust_root_invalid"),
    spec["errors.input.trust_root_invalid.cause"],
  );
});

test("catalogueGaps is empty over the engine's own sources", () => {
  assert.deepEqual(catalogueGaps(engineSources()), []);
});

test("catalogueGaps names a raised key the catalogue lacks", () => {
  assert.deepEqual(catalogueGaps(['throw new InputError("input.not_a_real_key", "x", "y");']), [
    "input.not_a_real_key: raised but not in the catalogue",
  ]);
});

test("the loader: a missing file is empty; malformed JSON or a non-object top level throws", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-catalogue-"));
  assert.deepEqual(readCatalogFile(join(dir, "messages.en.json")), {});
  assert.deepEqual(loadCatalog("xx"), {});
  writeFileSync(join(dir, "malformed.json"), "{not json");
  assert.throws(() => readCatalogFile(join(dir, "malformed.json")), SyntaxError);
  writeFileSync(join(dir, "array.json"), "[1, 2]");
  assert.throws(() => readCatalogFile(join(dir, "array.json")), /not hold a message catalogue/);
});

const SHAPES: Array<[unknown, string]> = [
  [{ keys: "x" }, "keys is not a mapping of key id to key entry"],
  [{ keys: { abc: "s" } }, "keys entry 'abc' is not a mapping"],
  [{ keys: { abc: { identity: "x" } } }, "keys entry 'abc' has no public_key"],
  [{ certificate_authorities: "x" }, "certificate_authorities is not a mapping of id to entry"],
  [
    { certificate_authorities: { ca1: "x" } },
    "certificate_authorities entry 'ca1' is not a mapping",
  ],
  [
    { certificate_authorities: { ca1: {} } },
    "certificate_authorities entry 'ca1' has no public_key",
  ],
  [{ keys: { "a'b\nc": "x" } }, `keys entry "a'b\\nc" is not a mapping`],
];

test("each malformed trust-root shape has one stable cause", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-trust-"));
  for (const [data, tail] of SHAPES) {
    const path = join(dir, "root.json");
    writeFileSync(path, JSON.stringify(data));
    assert.throws(
      () => loadTrustRoot(path),
      (err: Error) => err.message === `${path} is not a usable trust root: ${tail}`,
    );
  }
});

test("empty trust-root mappings are no entries", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-trust-"));
  const path = join(dir, "root.json");
  writeFileSync(path, JSON.stringify({ keys: {}, certificate_authorities: null }));
  assert.doesNotThrow(() => loadTrustRoot(path));
});

test("a digest read that fails raises input.digest_unreadable", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-digest-"));
  assert.throws(
    () => digestOf(dir),
    (err: unknown) =>
      err instanceof InputError &&
      err.key === "input.digest_unreadable" &&
      err.cause.startsWith(`${dir} could not be read to record its digest: `) &&
      err.fix === "make the file readable, then re-run.",
  );
});
