/**
 * Verify refusals carry stable message keys (18.64), the same behaviour Python's
 * `tests/test_verify_refusal_keys.py` and Java's `VerifyRefusalKeysTest` pin; `VG-VERIFY-REFUSAL-KEYS`
 * runs all three.
 */
import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { cpSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { messageKeys } from "./errorCatalogue";
import { keyidFor } from "./sign";
import {
  CATALOG_SIGNATURE_NAME,
  MAX_JSON_DEPTH,
  TrustRoot,
  VerifyRefusal,
  parseUntrustedJson,
  refusal,
  vendoredTrust,
  verifyCatalog,
  verifyEnvelope,
} from "./verify";

const REPO = join(__dirname, "..", "..", "..");
const EU_AI_ACT = join(REPO, "spec", "catalogs", "base", "eu-ai-act");

test("every verify key verify.ts names is in the catalogue", () => {
  const source = readFileSync(join(__dirname, "verify.ts"), "utf8");
  const named = new Set([...source.matchAll(/"(verify\.[a-z_]+)"/g)].map((m) => m[1]));
  assert.ok(named.has("verify.json_too_deep"));
  assert.deepEqual(
    [...named].filter((key) => !messageKeys().has(key)),
    [],
  );
});

test("a leaf refusal is its cause with params filled", () => {
  const leaf = refusal("verify.keyid_untrusted", { keyid: "'k1'" });
  assert.equal(leaf.message, "no trusted key for keyid 'k1'");
  assert.deepEqual(leaf.keys, ["verify.keyid_untrusted"]);
});

test("a wrapper lists its key, then the inner keys", () => {
  const wrapped = refusal("verify.no_signature_verified", {}, refusal("verify.signature_invalid"));
  const outer = refusal("verify.release_signature", { profile: "kms" }, wrapped);
  assert.equal(
    outer.message,
    "signature (kms): no signature verified against the trust root: signature does not verify",
  );
  assert.deepEqual(outer.keys, [
    "verify.release_signature",
    "verify.no_signature_verified",
    "verify.signature_invalid",
  ]);
});

test("a missing sig field is a sentence, not a KeyError repr", () => {
  const { publicKey } = generateKeyPairSync("ed25519");
  const raw = Buffer.from(publicKey.export({ type: "spki", format: "der" }).subarray(12));
  const keyid = keyidFor(raw);
  const trust = TrustRoot.fromDict({ keys: { [keyid]: { public_key: raw.toString("base64") } } });
  const envelope = {
    payloadType: "application/vnd.in-toto+json",
    payload: "e30=",
    signatures: [{ keyid }],
  };
  assert.throws(
    () => verifyEnvelope(envelope, trust),
    (err: unknown) =>
      err instanceof VerifyRefusal &&
      err.message ===
        "no signature verified against the trust root: a signature entry has no sig field" &&
      err.keys.join() === "verify.no_signature_verified,verify.signature_sig_missing",
  );
  assert.equal(
    messageKeys().get("verify.release_envelope_missing")?.cause,
    "a signature entry has no envelope",
  );
});

function nested(depth: number): Buffer {
  return Buffer.from("[".repeat(depth) + "]".repeat(depth));
}

function verifySignatureBytes(raw: Buffer) {
  const dir = join(mkdtempSync(join(tmpdir(), "agentce-refusal-keys-")), "catalog");
  cpSync(EU_AI_ACT, dir, { recursive: true });
  writeFileSync(join(dir, CATALOG_SIGNATURE_NAME), raw);
  return verifyCatalog(dir, vendoredTrust());
}

test("json_too_deep starts one past the limit", () => {
  const atLimit = verifySignatureBytes(nested(MAX_JSON_DEPTH));
  assert.equal(atLimit.verified, false);
  assert.notDeepEqual(atLimit.reason_keys, ["verify.json_too_deep"]);
  const past = verifySignatureBytes(nested(MAX_JSON_DEPTH + 1));
  assert.deepEqual(past.reason_keys, ["verify.json_too_deep"]);
  assert.equal(
    past.reason,
    `catalog.sig.json nests containers more than ${MAX_JSON_DEPTH} levels deep`,
  );
});

test("depth is found before any other problem", () => {
  const deep = nested(MAX_JSON_DEPTH + 1);
  for (const raw of [
    deep.subarray(0, deep.length - 1), // truncated
    Buffer.from("[".repeat(100_000)), // truncated, far past the limit
    Buffer.concat([deep, Buffer.from(" x")]), // trailing garbage
    Buffer.concat([Buffer.from('["\\ud800",'), nested(MAX_JSON_DEPTH), Buffer.from("]")]), // a lone surrogate first
    Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), deep]), // a byte-order mark
  ]) {
    assert.deepEqual(verifySignatureBytes(raw).reason_keys, ["verify.json_too_deep"]);
  }
});

test("brackets inside strings do not count", () => {
  const raw = Buffer.from(`["${"[".repeat(MAX_JSON_DEPTH + 5)}\\"["]`);
  assert.ok(parseUntrustedJson(raw));
});

test("other callers keep the not readable text", () => {
  assert.throws(() => parseUntrustedJson(nested(MAX_JSON_DEPTH + 1)), /^Error: not readable JSON$/);
});
