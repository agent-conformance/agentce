/**
 * Trust-root parse parity (18.81): `loadTrustRoot` parses with `parseUntrustedJson`, the rule Python's
 * `signing.load_trust_root` and Java's `Verify.loadTrustRoot` share, so a trust root nested past
 * `MAX_JSON_DEPTH` gets one fixed text and the three engines accept the same files. Python's
 * `tests/test_trust_root_parse.py` and Java's `TrustRootParseTest` pin the same behaviour;
 * `VG-TRUST-ROOT-PARSE-PARITY` runs all three.
 */
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { loadTrustRoot } from "./verify";

const TOO_DEEP = "is not readable JSON: it nests containers more than 1000 levels deep";

function refusal(name: string, raw: Buffer): string {
  const work = mkdtempSync(join(tmpdir(), "agentce-trust-parse-"));
  const path = join(work, `${name}.json`);
  writeFileSync(path, raw);
  try {
    loadTrustRoot(path);
  } catch (exc) {
    return (exc as Error).message.split(path).join("<path>");
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
  throw new Error(`${name} loaded as a trust root`);
}

const pastTheLimit: [string, Buffer][] = [
  ["deep-array-10000", Buffer.from("[".repeat(10000) + "]".repeat(10000))],
  ["deep-object-10000", Buffer.from(`${'{"a":'.repeat(10000)}1${"}".repeat(10000)}`)],
  ["keys-1001", Buffer.from(`{"keys":${"[".repeat(1000)}${"]".repeat(1000)}}`)],
  ["truncated-1001", Buffer.from("[".repeat(1001))],
];
for (const [name, raw] of pastTheLimit) {
  test(`a trust root past the limit is one fixed text: ${name}`, () => {
    assert.equal(refusal(name, raw), `<path> ${TOO_DEEP}`);
  });
}

test("a trust root at the limit reaches the shape stage", () => {
  const raw = Buffer.from(`{"keys":${"[".repeat(999)}${"]".repeat(999)}}`);
  assert.equal(
    refusal("keys-999", raw),
    "<path> is not a usable trust root: keys is not a mapping of key id to key entry",
  );
});

const otherRefusals: [string, Buffer][] = [
  ["nan", Buffer.from('{"keys":{},"x":NaN}')],
  ["lone-surrogate", Buffer.from('{"keys":{},"x":"\\ud800"}')],
  [
    "invalid-utf8",
    Buffer.concat([Buffer.from('{"keys":{},"x":"'), Buffer.from([0xff]), Buffer.from('"}')]),
  ],
  ["bom", Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from('{"keys":{}}')])],
  ["not-json", Buffer.from("{not json")],
];
for (const [name, raw] of otherRefusals) {
  test(`another parse refusal names the file: ${name}`, () => {
    const text = refusal(name, raw);
    assert.ok(text.startsWith("<path> is not readable JSON"), text);
    assert.ok(!text.includes("not readable JSON: not readable JSON"), text);
    assert.ok(!text.includes("1000 levels"), text);
  });
}

test("a lone surrogate has no parser suffix", () => {
  assert.equal(
    refusal("s", Buffer.from('{"keys":{},"x":"\\ud800"}')),
    "<path> is not readable JSON",
  );
});
