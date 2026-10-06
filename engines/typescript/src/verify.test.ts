/**
 * `verify.ts`'s DSSE/certificate verification primitives (item 18.28): the compute seam `cli.ts`'s
 * `cmdVerify` wires up. `cli.test.ts` covers the full `agentce verify` command end to end.
 */

import assert from "node:assert/strict";
import { sign as cryptoSign, verify as cryptoVerify, generateKeyPairSync } from "node:crypto";
import {
  chmodSync,
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { loadBundle } from "./bundle";
import { canonicalize, sha256Hex } from "./canonical";
import { InputError } from "./errors";
import { errorCause } from "./messages";
import { digestTree } from "./report";
import { dssePae, keyidFor, signStatement } from "./sign";
import {
  type ReleaseResult,
  TrustRoot,
  publicKeyFromRaw,
  vendoredTrustPath,
  verifyCatalog,
  verifyEnvelope,
  verifyRelease,
} from "./verify";

// --- The vendored trust root is a byte-for-byte copy of Python's, as `verify.ts`'s own header
// claims -- a sync test, not merely a shared fixture, so the two can never silently drift apart
// (mirrors 18.35's upcoming i18n sync-test shape; this item adds its own narrow one rather than
// waiting on that item). ---------------------------------------------------------------------

test("the vendored TypeScript trust root parses to the same value as Python's dev-root.json", () => {
  const tsRoot = JSON.parse(readFileSync(vendoredTrustPath(), "utf-8"));
  const pythonPath = join(
    __dirname,
    "..",
    "..",
    "python",
    "agentce",
    "data",
    "trust",
    "dev-root.json",
  );
  const pythonRoot = JSON.parse(readFileSync(pythonPath, "utf-8"));
  assert.deepEqual(tsRoot, pythonRoot);
  // `canonicalize` sorts keys deeply (the same canonicalisation every signed statement uses), so
  // this is the same "parses to the same sorted value" check regardless of on-disk key order.
  assert.equal(canonicalize(tsRoot).toString("utf-8"), canonicalize(pythonRoot).toString("utf-8"));
});

function ed25519RawPair(): {
  privateKeyObject: ReturnType<typeof generateKeyPairSync>["privateKey"];
  rawPublicKey: Buffer;
} {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  return { privateKeyObject: privateKey, rawPublicKey: Buffer.from(publicDer.subarray(12)) };
}

interface Fixture {
  readonly keyid: string;
  readonly identity: string;
  readonly trust: TrustRoot;
  sign(data: Buffer): Buffer;
}

function kmsFixture(identity = "test-identity"): Fixture {
  const { privateKeyObject, rawPublicKey } = ed25519RawPair();
  const keyid = keyidFor(rawPublicKey);
  const trustDict = {
    keys: { [keyid]: { public_key: rawPublicKey.toString("base64"), identity } },
  };
  return {
    keyid,
    identity,
    trust: TrustRoot.fromDict(trustDict),
    sign: (data: Buffer) => cryptoSign(null, data, privateKeyObject),
  };
}

function signEnvelope(statement: unknown, fixture: Fixture): Record<string, unknown> {
  const envelope = signStatement(statement, {
    keyid: fixture.keyid,
    sign: fixture.sign,
  });
  return envelope as unknown as Record<string, unknown>;
}

// --- verifyEnvelope: malformed shape (every check C5 pins in Python's rewritten verify_envelope). ---

test("verifyEnvelope refuses a non-object envelope", () => {
  const fixture = kmsFixture();
  assert.throws(() => verifyEnvelope(5, fixture.trust), /malformed DSSE envelope/);
  assert.throws(() => verifyEnvelope(null, fixture.trust), /malformed DSSE envelope/);
});

test("verifyEnvelope refuses a non-string payloadType", () => {
  const fixture = kmsFixture();
  assert.throws(
    () => verifyEnvelope({ payloadType: 5, payload: "e30=", signatures: [] }, fixture.trust),
    /malformed DSSE envelope/,
  );
});

test("verifyEnvelope refuses a non-string payload", () => {
  const fixture = kmsFixture();
  assert.throws(
    () => verifyEnvelope({ payloadType: "t", payload: 5, signatures: [] }, fixture.trust),
    /malformed DSSE envelope/,
  );
});

test("verifyEnvelope refuses an unparseable-base64 payload", () => {
  const fixture = kmsFixture();
  assert.throws(
    () => verifyEnvelope({ payloadType: "t", payload: "!!!!", signatures: [] }, fixture.trust),
    /malformed DSSE envelope/,
  );
});

test("verifyEnvelope refuses a non-array signatures field", () => {
  const fixture = kmsFixture();
  assert.throws(
    () => verifyEnvelope({ payloadType: "t", payload: "e30=", signatures: 5 }, fixture.trust),
    /malformed DSSE envelope/,
  );
});

test('verifyEnvelope refuses signatures: ["x"] with the per-entry resolve message, not malformed envelope', () => {
  const fixture = kmsFixture();
  assert.throws(
    () => verifyEnvelope({ payloadType: "t", payload: "e30=", signatures: ["x"] }, fixture.trust),
    /no signature verified against the trust root: no trusted key for keyid None/,
  );
});

test("verifyEnvelope refuses an empty signatures array", () => {
  const fixture = kmsFixture();
  assert.throws(
    () => verifyEnvelope({ payloadType: "t", payload: "e30=", signatures: [] }, fixture.trust),
    /DSSE envelope carries no signatures/,
  );
});

test("verifyEnvelope refuses a signature entry missing 'sig'", () => {
  const fixture = kmsFixture();
  const envelope = {
    payloadType: "t",
    payload: "e30=",
    signatures: [{ keyid: fixture.keyid }],
  };
  assert.throws(
    () => verifyEnvelope(envelope, fixture.trust),
    /no signature verified against the trust root: 'sig'/,
  );
});

test("verifyEnvelope refuses an unresolvable keyid", () => {
  const fixture = kmsFixture();
  const envelope = {
    payloadType: "t",
    payload: "e30=",
    signatures: [{ keyid: "sha256:deadbeef", sig: "AAAA" }],
  };
  assert.throws(
    () => verifyEnvelope(envelope, fixture.trust),
    /no signature verified against the trust root: no trusted key for keyid 'sha256:deadbeef'/,
  );
});

// --- verifyEnvelope: real signed round trips. ---

test("verifyEnvelope verifies a real kms-signed envelope and returns the identity/keyid", () => {
  const fixture = kmsFixture("kms-identity");
  const envelope = signEnvelope({ hello: "world" }, fixture);
  const verified = verifyEnvelope(envelope, fixture.trust);
  assert.equal(verified.identity, "kms-identity");
  assert.equal(verified.keyid, fixture.keyid);
  assert.equal(verified.keyless, false);
});

test("verifyEnvelope refuses a tampered signature with 'signature does not verify' (Python's SIGNATURE_INVALID)", () => {
  const fixture = kmsFixture();
  const envelope = signEnvelope({ hello: "world" }, fixture) as {
    signatures: Array<{ sig: string }>;
  };
  // Flip one byte of the signature, preserving valid base64 shape.
  const entry = envelope.signatures[0] as { sig: string };
  const sigBuf = Buffer.from(entry.sig, "base64");
  sigBuf[0] = (sigBuf[0] ?? 0) ^ 0xff;
  entry.sig = sigBuf.toString("base64");
  assert.throws(
    () => verifyEnvelope(envelope, fixture.trust),
    /^Error: no signature verified against the trust root: signature does not verify$/,
  );
});

test("verifyEnvelope's second signature error is used when there are two entries and only the second should appear", () => {
  const fixture = kmsFixture();
  const envelope = signEnvelope({ hello: "world" }, fixture) as {
    signatures: Array<{ keyid: string; sig: string }>;
  };
  envelope.signatures.unshift({ keyid: "sha256:unknown", sig: "AAAA" });
  // First entry fails to resolve; second entry (the real signature) verifies -- first success wins.
  const verified = verifyEnvelope(envelope, fixture.trust);
  assert.equal(verified.keyid, fixture.keyid);
});

// --- Certificate-based (keyless) trust resolution. ---

function issueCertificate(
  caPrivateKeyObject: ReturnType<typeof generateKeyPairSync>["privateKey"],
  issuer: string,
  identity: string,
  leafPublicKeyRaw: Buffer,
): Record<string, unknown> {
  const body = {
    issuer,
    identity,
    algorithm: "ed25519",
    public_key: leafPublicKeyRaw.toString("base64"),
    not_before: "2026-01-01T00:00:00Z",
    not_after: "2027-01-01T00:00:00Z",
  };
  const signature = cryptoSign(null, canonicalize(body), caPrivateKeyObject);
  return { ...body, signature: signature.toString("base64") };
}

function certFixture(): { trust: TrustRoot; sign(statement: unknown): Record<string, unknown> } {
  const ca = ed25519RawPair();
  const leaf = ed25519RawPair();
  const issuer = "test-ca";
  const trust = TrustRoot.fromDict({
    certificate_authorities: { [issuer]: { public_key: ca.rawPublicKey.toString("base64") } },
  });
  const cert = issueCertificate(ca.privateKeyObject, issuer, "keyless-identity", leaf.rawPublicKey);
  return {
    trust,
    sign(statement: unknown) {
      const payload = canonicalize(statement);
      const signature = cryptoSign(
        null,
        dssePae("application/vnd.in-toto+json", payload),
        leaf.privateKeyObject,
      );
      return {
        payloadType: "application/vnd.in-toto+json",
        payload: payload.toString("base64"),
        signatures: [
          { keyid: keyidFor(leaf.rawPublicKey), sig: signature.toString("base64"), cert },
        ],
      };
    },
  };
}

test("verifyEnvelope verifies a real certificate (keyless) signed envelope", () => {
  const fixture = certFixture();
  const envelope = fixture.sign({ hello: "world" });
  const verified = verifyEnvelope(envelope, fixture.trust);
  assert.equal(verified.identity, "keyless-identity");
  assert.equal(verified.keyless, true);
});

test("verifyEnvelope refuses an unknown certificate issuer", () => {
  const fixture = certFixture();
  const envelope = fixture.sign({ hello: "world" }) as {
    signatures: Array<{ cert: Record<string, unknown> }>;
  };
  const sig0 = envelope.signatures[0] as { cert: Record<string, unknown> };
  sig0.cert.issuer = "nope";
  assert.throws(
    () => verifyEnvelope(envelope, fixture.trust),
    /no signature verified against the trust root: unknown certificate issuer 'nope'/,
  );
});

test("verifyEnvelope collapses a corrupted certificate signature to 'certificate signature does not verify'", () => {
  const fixture = certFixture();
  const envelope = fixture.sign({ hello: "world" }) as {
    signatures: Array<{ cert: Record<string, unknown> }>;
  };
  const sig0 = envelope.signatures[0] as { cert: Record<string, unknown> };
  sig0.cert.signature = "AAAA";
  assert.throws(
    () => verifyEnvelope(envelope, fixture.trust),
    /no signature verified against the trust root: certificate signature does not verify/,
  );
});

test("verifyEnvelope collapses a non-object cert to 'certificate signature does not verify'", () => {
  const fixture = kmsFixture();
  const envelope = {
    payloadType: "t",
    payload: "e30=",
    signatures: [{ cert: "not-an-object" }],
  };
  assert.throws(
    () => verifyEnvelope(envelope, fixture.trust),
    /no signature verified against the trust root: certificate signature does not verify/,
  );
});

// --- TrustRoot.fromDict: the content-addressing invariant. ---

test("TrustRoot.fromDict refuses a keys entry whose declared id does not match its own key", () => {
  const fixture = ed25519RawPair();
  assert.throws(
    () =>
      TrustRoot.fromDict({
        keys: {
          "sha256:wrong": { public_key: fixture.rawPublicKey.toString("base64"), identity: "x" },
        },
      }),
    /trust root entry 'sha256:wrong' does not match its own key/,
  );
});

// --- publicKeyFromRaw: the DER-reconstruction round trip. ---

test("publicKeyFromRaw reconstructs a usable public key from raw bytes", () => {
  const { privateKeyObject, rawPublicKey } = ed25519RawPair();
  const data = Buffer.from("round trip", "utf-8");
  const sig = cryptoSign(null, data, privateKeyObject);
  const publicKey = publicKeyFromRaw(rawPublicKey);
  assert.equal(cryptoVerify(null, data, publicKey, sig), true);
});

// --- verifyCatalog. ---

function catalogDirWith(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "agentce-verify-catalog-"));
  for (const [name, content] of Object.entries(files)) {
    writeFileSync(join(dir, name), content);
  }
  return dir;
}

test("verifyCatalog soft-fails with the exact unsigned sentence when catalog.sig.json is absent", () => {
  const fixture = kmsFixture();
  const dir = catalogDirWith({ "rule.yaml": "x: 1\n" });
  try {
    const result = verifyCatalog(dir, fixture.trust);
    assert.equal(result.verified, false);
    assert.equal(
      result.reason,
      "unsigned: catalog.sig.json is absent, so there is no signature to verify (SPEC §8.7).",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyCatalog soft-fails on unparseable catalog.sig.json with the fixed text", () => {
  const fixture = kmsFixture();
  const dir = catalogDirWith({ "rule.yaml": "x: 1\n", "catalog.sig.json": "not json" });
  try {
    const result = verifyCatalog(dir, fixture.trust);
    assert.equal(result.verified, false);
    assert.equal(result.reason, "catalog.sig.json is not readable JSON");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyCatalog verifies a real signed directory and reports verified:true, keyless:false", () => {
  const fixture = kmsFixture("catalog-signer");
  const dir = catalogDirWith({ "rule.yaml": "x: 1\n" });
  try {
    const digest = digestTree(dir, new Set(["catalog.sig.json"]));
    const statement = {
      _type: "https://in-toto.io/Statement/v1",
      subject: [{ name: "catalog", digest: { sha256: digest.slice("sha256:".length) } }],
      predicateType: "https://agent-conformance.org/attestation/catalog/v1",
      predicate: {},
    };
    const envelope = signEnvelope(statement, fixture);
    writeFileSync(join(dir, "catalog.sig.json"), JSON.stringify(envelope));
    const result = verifyCatalog(dir, fixture.trust);
    assert.equal(result.verified, true);
    assert.equal(result.digest, digest);
    assert.equal(result.signer, "catalog-signer");
    assert.equal(result.keyless, false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyCatalog soft-fails with no trailing period when the signed digest does not match the directory", () => {
  const fixture = kmsFixture();
  const dir = catalogDirWith({ "rule.yaml": "x: 1\n" });
  try {
    const statement = {
      _type: "https://in-toto.io/Statement/v1",
      subject: [{ name: "catalog", digest: { sha256: "0".repeat(64) } }],
      predicateType: "https://agent-conformance.org/attestation/catalog/v1",
      predicate: {},
    };
    const envelope = signEnvelope(statement, fixture);
    writeFileSync(join(dir, "catalog.sig.json"), JSON.stringify(envelope));
    const result = verifyCatalog(dir, fixture.trust);
    assert.equal(result.verified, false);
    assert.equal(
      result.reason,
      "the signature covers a different catalog digest than the directory content",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// --- verifyRelease: single-file form. ---

function tmpFile(name: string, content: string): { dir: string; path: string } {
  const dir = mkdtempSync(join(tmpdir(), "agentce-verify-release-"));
  const path = join(dir, name);
  writeFileSync(path, content);
  return { dir, path };
}

test("verifyRelease soft-fails with the fixed sentence on unreadable envelope JSON", () => {
  const fixture = kmsFixture();
  const { dir, path } = tmpFile("release.dsse.json", "not json");
  try {
    const result = verifyRelease(path, fixture.trust);
    assert.equal(result.verified, false);
    assert.equal((result as { reason?: string }).reason, "release envelope is not readable JSON");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease soft-fails (never internal.unexpected) on an unresolvable keyid, exit shape matches --catalog", () => {
  const fixture = kmsFixture();
  const envelope = {
    payloadType: "application/vnd.in-toto+json",
    payload: "e30=",
    signatures: [{ keyid: "sha256:deadbeef", sig: "AAAA" }],
  };
  const { dir, path } = tmpFile("release.dsse.json", JSON.stringify(envelope));
  try {
    const result = verifyRelease(path, fixture.trust);
    assert.equal(result.verified, false);
    assert.ok(!("error" in result));
    assert.ok("reason" in result);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('verifyRelease soft-fails on malformed field types without crashing (payload:5, payloadType:5, signatures:["x"])', () => {
  const fixture = kmsFixture();
  for (const bad of [
    { payloadType: "t", payload: 5, signatures: [] },
    { payloadType: 5, payload: "e30=", signatures: [] },
    { payloadType: "t", payload: "e30=", signatures: ["x"] },
  ]) {
    const { dir, path } = tmpFile("release.dsse.json", JSON.stringify(bad));
    try {
      const result = verifyRelease(path, fixture.trust);
      assert.equal(result.verified, false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }
});

test("verifyRelease verifies a real single-file kms-signed release", () => {
  const fixture = kmsFixture("release-signer");
  const envelope = signEnvelope({ hello: "release" }, fixture);
  const { dir, path } = tmpFile("release.dsse.json", JSON.stringify(envelope));
  try {
    const result = verifyRelease(path, fixture.trust);
    assert.equal(result.verified, true);
    assert.equal((result as { signer: string }).signer, "release-signer");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease refuses a byte-order-marked envelope instead of silently stripping it (verifier round-1 Finding 4)", () => {
  const fixture = kmsFixture();
  const envelope = { payloadType: "x", payload: "", signatures: [] as unknown[] };
  const { dir, path } = tmpFile("release.dsse.json", `﻿${JSON.stringify(envelope)}`);
  try {
    const result = verifyRelease(path, fixture.trust) as { verified: boolean; reason?: string };
    assert.equal(result.verified, false);
    assert.equal(result.reason, "release envelope is not readable JSON");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// --- verifyRelease: directory-bundle form. ---

function bundleDir(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "agentce-verify-bundle-"));
  for (const [name, content] of Object.entries(files)) {
    writeFileSync(join(dir, name), content);
  }
  return dir;
}

test("verifyRelease soft-fails with the fixed sentence on an unreadable manifest, no manifest_digest/signers field", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({ "release-manifest.json": "not json", "signatures.json": "[]" });
  try {
    const result = verifyRelease(dir, fixture.trust);
    assert.equal(result.verified, false);
    assert.equal((result as { reason?: string }).reason, "release manifest is not readable JSON");
    assert.ok(!("manifest_digest" in result));
    assert.ok(!("signers" in result));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease soft-fails on a manifest that parses but is not an object (e.g. [])", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({ "release-manifest.json": "[]", "signatures.json": "[]" });
  try {
    const result = verifyRelease(dir, fixture.trust);
    assert.equal(result.verified, false);
    assert.equal((result as { reason?: string }).reason, "release manifest is not readable JSON");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease soft-fails with the fixed sentence on unreadable signatures.json, no manifest_digest/signers field", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({ "release-manifest.json": "{}", "signatures.json": "not json" });
  try {
    const result = verifyRelease(dir, fixture.trust);
    assert.equal(result.verified, false);
    assert.equal(
      (result as { reason?: string }).reason,
      "release signatures are not readable JSON",
    );
    // `_verify_release_soft_fail` is the one function every such Python return calls, and it only
    // ever sets {release, verified, reason} -- not even for this late a failure, after
    // `manifest_digest` was already computed locally (a divergence this port once had, fixed here).
    assert.ok(!("manifest_digest" in result));
    assert.ok(!("signers" in result));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease folds signatures.json valid-JSON-but-not-array (e.g. 5) into the same not-readable sentence", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({ "release-manifest.json": "{}", "signatures.json": "5" });
  try {
    const result = verifyRelease(dir, fixture.trust);
    assert.equal(result.verified, false);
    assert.equal(
      (result as { reason?: string }).reason,
      "release signatures are not readable JSON",
    );
    assert.ok(!("manifest_digest" in result));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease flags an artifacts[] entry with no name, and one whose name is not a string", () => {
  const fixture = kmsFixture();
  const manifest = { artifacts: [{ not_name: "x" }, { name: 5 }] };
  const dir = bundleDir({
    "release-manifest.json": JSON.stringify(manifest),
    "signatures.json": "[]",
  });
  try {
    const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
    assert.equal(result.verified, false);
    assert.equal(
      ((result.reason ?? "").match(/release manifest has an artifact entry with no name/g) ?? [])
        .length,
      2,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease flags a missing artifact file and a digest mismatch", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({
    "release-manifest.json": JSON.stringify({
      artifacts: [
        { name: "missing.bin", digest: "sha256:0" },
        { name: "present.bin", digest: "sha256:0" },
      ],
    }),
    "signatures.json": "[]",
    "present.bin": "actual content",
  });
  try {
    const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
    assert.equal(result.verified, false);
    assert.ok((result.reason ?? "").includes("missing artifact missing.bin"));
    assert.ok((result.reason ?? "").includes("digest mismatch for present.bin"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease flags a non-object signatures.json entry with the fixed 'signature (None): ...' text", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({
    "release-manifest.json": "{}",
    "signatures.json": JSON.stringify(["x"]),
  });
  try {
    const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
    assert.equal(result.verified, false);
    assert.equal(result.reason, "signature (None): signature entry is not an object");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease flags a signature entry missing its 'envelope' key with pyStr(profile) rendering", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({
    "release-manifest.json": "{}",
    "signatures.json": JSON.stringify([{ profile: "kms" }]),
  });
  try {
    const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
    assert.equal(result.verified, false);
    assert.equal(result.reason, "signature (kms): 'envelope'");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease verifies a real directory-bundle release signed by one kms entry", () => {
  const fixture = kmsFixture("bundle-signer");
  const manifest = { artifacts: [] as unknown[] };
  const manifestJson = JSON.stringify(
    Object.fromEntries(Object.entries(manifest).sort(([a], [b]) => (a < b ? -1 : 1))),
  );
  const manifestDigest = `sha256:${sha256Hex(JSON.parse(manifestJson))}`;
  const statement = {
    _type: "https://in-toto.io/Statement/v1",
    subject: [{ name: "release-manifest.json", digest: { sha256: manifestDigest.slice(7) } }],
    predicateType: "https://agent-conformance.org/attestation/release/v1",
    predicate: {},
  };
  const envelope = signEnvelope(statement, fixture);
  const dir = bundleDir({
    "release-manifest.json": manifestJson,
    "signatures.json": JSON.stringify([{ profile: "kms", envelope }]),
  });
  try {
    const result = verifyRelease(dir, fixture.trust) as {
      verified: boolean;
      manifest_digest: string;
      signers: ReadonlyArray<{ readonly identity: string }>;
    };
    assert.equal(result.verified, true);
    assert.equal(result.manifest_digest, manifestDigest);
    assert.equal(result.signers.length, 1);
    assert.equal(result.signers[0]?.identity, "bundle-signer");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease refuses an unsigned bundle (zero signature entries), not verified:true (verifier round-1 Finding 1)", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({
    "release-manifest.json": JSON.stringify({ artifacts: [] }),
    "signatures.json": "[]",
  });
  try {
    const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
    assert.equal(result.verified, false);
    assert.equal(result.reason, "release bundle carries no signatures");
    assert.deepEqual((result as unknown as { signers: unknown[] }).signers, []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

for (const [label, artifacts] of Object.entries({
  "artifacts-null": null,
  "artifacts-int": 5,
  "artifacts-str": "ab",
})) {
  test(`verifyRelease treats a non-array artifacts field (${label}) as empty, not a crash (verifier round-1 Finding 2)`, () => {
    const fixture = kmsFixture();
    const dir = bundleDir({
      "release-manifest.json": JSON.stringify({ artifacts }),
      "signatures.json": "[]",
    });
    try {
      const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
      assert.equal(result.verified, false);
      assert.equal(result.reason, "release bundle carries no signatures");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
}

test("verifyRelease soft-fails when the manifest cannot be canonicalized, instead of crashing (verifier round-1 Finding 3)", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({
    "release-manifest.json": JSON.stringify({ artifacts: [], size: 1.5 }),
    "signatures.json": "[]",
  });
  try {
    const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
    assert.equal(result.verified, false);
    assert.equal(result.reason, "release manifest cannot be canonicalized");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyRelease throws input.release_bundle for a directory with no manifest/signatures files", () => {
  const fixture = kmsFixture();
  const dir = bundleDir({ "readme.txt": "not a release" });
  try {
    assert.throws(() => verifyRelease(dir, fixture.trust), /input\.release_bundle/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// --- Verifier round 2: integral floats, path containment, recursion depth, cert edge cases. ---

for (const label of ["1.0", "1e2", "-0.0"]) {
  test(`verifyRelease soft-fails on an integral-valued float manifest field (${label}), not verified:true (verifier round-2)`, () => {
    const fixture = kmsFixture();
    const dir = bundleDir({
      "release-manifest.json": `{"artifacts": [], "size": ${label}}`,
      "signatures.json": "[]",
    });
    try {
      const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
      assert.equal(result.verified, false);
      assert.equal(result.reason, "release manifest cannot be canonicalized");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
}

for (const [label, name] of [
  ["absolute", "/etc/hosts"],
  ["dotdot-escape", "../../../../../../etc/hosts"],
  ["embedded-nul", "a\0b"],
] as const) {
  test(`verifyRelease treats an unsafe artifact name (${label}) as missing, never escaping the release directory (verifier round-2)`, () => {
    const fixture = kmsFixture();
    const dir = bundleDir({
      "release-manifest.json": JSON.stringify({
        artifacts: [{ name, digest: "sha256:0" }],
      }),
      "signatures.json": "[]",
    });
    try {
      const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
      assert.equal(result.verified, false);
      assert.ok((result.reason ?? "").includes(`missing artifact ${name}`));
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
}

test("verifyRelease refuses a manifest nested past MAX_JSON_DEPTH as unreadable, instead of crashing (verifier round-2)", () => {
  const fixture = kmsFixture();
  const nested = "[".repeat(5000) + "]".repeat(5000);
  const dir = bundleDir({
    "release-manifest.json": `{"artifacts": [], "nested": ${nested}}`,
    "signatures.json": "[]",
  });
  try {
    const result = verifyRelease(dir, fixture.trust) as { verified: boolean; reason?: string };
    assert.equal(result.verified, false);
    assert.equal(result.reason, "release manifest is not readable JSON");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyCertificate collapses a non-string identity to 'certificate signature does not verify' (verifier round-2)", () => {
  const ca = ed25519RawPair();
  const leaf = ed25519RawPair();
  const issuer = "test-ca";
  const trust = TrustRoot.fromDict({
    certificate_authorities: { [issuer]: { public_key: ca.rawPublicKey.toString("base64") } },
  });
  const body = {
    issuer,
    identity: 5,
    algorithm: "ed25519",
    public_key: leaf.rawPublicKey.toString("base64"),
    not_before: "2026-01-01T00:00:00Z",
    not_after: "2027-01-01T00:00:00Z",
  };
  const signature = cryptoSign(null, canonicalize(body), ca.privateKeyObject);
  const cert = { ...body, signature: signature.toString("base64") };
  const envelope = {
    payloadType: "application/vnd.in-toto+json",
    payload: "e30=",
    signatures: [{ keyid: keyidFor(leaf.rawPublicKey), sig: "AAAA", cert }],
  };
  assert.throws(
    () => verifyEnvelope(envelope, trust),
    /no signature verified against the trust root: certificate signature does not verify/,
  );
});

test("verifyCertificate collapses a wrong-length leaf key to 'certificate signature does not verify', not a native crypto message (verifier round-2)", () => {
  const ca = ed25519RawPair();
  const issuer = "test-ca";
  const trust = TrustRoot.fromDict({
    certificate_authorities: { [issuer]: { public_key: ca.rawPublicKey.toString("base64") } },
  });
  const body = {
    issuer,
    identity: "ci@agent-conformance.org",
    algorithm: "ed25519",
    public_key: Buffer.from("too-short").toString("base64"),
    not_before: "2026-01-01T00:00:00Z",
    not_after: "2027-01-01T00:00:00Z",
  };
  const signature = cryptoSign(null, canonicalize(body), ca.privateKeyObject);
  const cert = { ...body, signature: signature.toString("base64") };
  const envelope = {
    payloadType: "application/vnd.in-toto+json",
    payload: "e30=",
    signatures: [{ sig: Buffer.alloc(64).toString("base64"), cert }],
  };
  assert.throws(
    () => verifyEnvelope(envelope, trust),
    /no signature verified against the trust root: certificate signature does not verify/,
  );
});

// --- TrustRoot.fromDict mirrors Python's `data.get(field) or {}` (18.36 critic round 2, must-fix A). ---

test("TrustRoot.fromDict treats a falsy keys/certificate_authorities as empty, as Python does", () => {
  for (const empty of [null, [], "", 0, false]) {
    assert.equal(TrustRoot.fromDict({ keys: empty }).keys.size, 0, JSON.stringify(empty));
    assert.equal(
      TrustRoot.fromDict({ certificate_authorities: empty }).authorities.size,
      0,
      JSON.stringify(empty),
    );
  }
});

test("TrustRoot.fromDict refuses a truthy keys value that is not a mapping, as Python does", () => {
  for (const bad of ["x", [1], 5, true]) {
    assert.throws(() => TrustRoot.fromDict({ keys: bad }), JSON.stringify(bad));
    assert.throws(() => TrustRoot.fromDict({ certificate_authorities: bad }), JSON.stringify(bad));
  }
});

// --- Keyless certificate fields (item 18.63): algorithm, and the validity window's shape and order. ---

/** A certificate-signed envelope whose certificate body the test authority re-signs after `changes`
 * (a value of `undefined` deletes the field), so only the field checks can refuse it. */
function keylessWith(changes: Record<string, unknown>): {
  trust: TrustRoot;
  envelope: Record<string, unknown>;
} {
  const ca = ed25519RawPair();
  const leaf = ed25519RawPair();
  const cert = issueCertificate(
    ca.privateKeyObject,
    "test-ca",
    "keyless-identity",
    leaf.rawPublicKey,
  );
  const merged: Record<string, unknown> = { ...cert, ...changes };
  const body = Object.fromEntries(
    Object.entries(merged).filter(([field, value]) => field !== "signature" && value !== undefined),
  );
  const signature = cryptoSign(null, canonicalize(body), ca.privateKeyObject).toString("base64");
  const payload = canonicalize({ hello: "world" });
  const sig = cryptoSign(
    null,
    dssePae("application/vnd.in-toto+json", payload),
    leaf.privateKeyObject,
  );
  return {
    trust: TrustRoot.fromDict({
      certificate_authorities: { "test-ca": { public_key: ca.rawPublicKey.toString("base64") } },
    }),
    envelope: {
      payloadType: "application/vnd.in-toto+json",
      payload: payload.toString("base64"),
      signatures: [
        {
          keyid: keyidFor(leaf.rawPublicKey),
          sig: sig.toString("base64"),
          cert: { ...body, signature },
        },
      ],
    },
  };
}

function certificateRefusal(key: string): string {
  return `no signature verified against the trust root: ${key}: ${errorCause(key).replace(/\.$/, "")}`;
}

test("keyless certificate: an algorithm other than exactly ed25519 is refused with verify.certificate_algorithm", () => {
  for (const algorithm of ["ecdsa-p256", "ED25519", "ed25519 ", 1, ["ed25519"], null, undefined]) {
    const { trust, envelope } = keylessWith({ algorithm });
    assert.throws(() => verifyEnvelope(envelope, trust), {
      message: certificateRefusal("verify.certificate_algorithm"),
    });
  }
});

test("keyless certificate: a not_before or not_after that is not an RFC 3339 UTC timestamp is refused with verify.certificate_validity_malformed", () => {
  const cases: Record<string, unknown>[] = [
    { not_before: "2026-01-01 00:00:00Z" },
    { not_before: "2026-01-01T00:00:00+00:00" },
    { not_before: "2026-01-01T00:00:00" },
    { not_before: "2026-02-29T00:00:00Z" },
    { not_before: "2026-01-01T24:00:00Z" },
    { not_before: "2026-01-01T00:00:00Z\n" },
    { not_before: "٢٠٢٦-01-01T00:00:00Z" },
    { not_before: 1767225600 },
    { not_before: undefined },
    { not_after: "2027-01-32T00:00:00Z" },
    { not_after: "2027-01-01T00:00:00.Z" },
    { not_after: null },
  ];
  for (const changes of cases) {
    const { trust, envelope } = keylessWith(changes);
    assert.throws(() => verifyEnvelope(envelope, trust), {
      message: certificateRefusal("verify.certificate_validity_malformed"),
    });
  }
});

test("keyless certificate: a reversed window is refused with verify.certificate_validity_inverted", () => {
  for (const [notBefore, notAfter] of [
    ["2027-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
    ["2026-01-01T00:00:00.5Z", "2026-01-01T00:00:00.49Z"],
  ]) {
    const { trust, envelope } = keylessWith({ not_before: notBefore, not_after: notAfter });
    assert.throws(() => verifyEnvelope(envelope, trust), {
      message: certificateRefusal("verify.certificate_validity_inverted"),
    });
  }
});

test("keyless certificate: well-formed windows verify, the algorithm is checked first", () => {
  for (const changes of [
    { not_before: "2027-01-01T00:00:00Z", not_after: "2027-01-01T00:00:00Z" },
    { not_before: "2026-01-01t00:00:00z" },
    { not_after: "2026-12-31T23:59:60Z" },
    { not_before: "2024-02-29T00:00:00Z" },
    { not_before: "2026-01-01T00:00:00.5Z", not_after: "2026-01-01T00:00:00.50Z" },
    { not_before: "2026-01-01T00:00:00.50Z", not_after: "2026-01-01T00:00:00.5Z" },
  ]) {
    const { trust, envelope } = keylessWith(changes);
    assert.equal(verifyEnvelope(envelope, trust).keyless, true);
  }
  const { trust, envelope } = keylessWith({ algorithm: "rsa", not_before: "junk" });
  assert.throws(() => verifyEnvelope(envelope, trust), {
    message: certificateRefusal("verify.certificate_algorithm"),
  });
});

test("keyless certificate: a window wholly in 1970 or wholly in 2999 verifies, so the clock is never read", () => {
  for (const [notBefore, notAfter] of [
    ["1970-01-01T00:00:00Z", "1970-01-01T00:10:00Z"],
    ["2999-01-01T00:00:00Z", "2999-01-01T00:10:00Z"],
  ]) {
    const { trust, envelope } = keylessWith({ not_before: notBefore, not_after: notAfter });
    assert.equal(verifyEnvelope(envelope, trust).keyless, true);
  }
});

// --- 18.68: an input that cannot be read is refused with one key per target, naming the path. ---

const QUICKSTART_EVIDENCE = join(__dirname, "..", "..", "..", "corpus", "quickstart", "evidence");
const STREAM = "events/urn-agentce-source-langgraph-gateway-eu-1.jsonl";
const asRoot = process.getuid?.() === 0; // root reads a mode-000 file anyway

/** Runs `fn` with `path` made unreadable (chmod 000), restoring it after, and returns the refusal. */
function unreadableRefusal(path: string, fn: () => unknown): { key: string; cause: string } {
  chmodSync(path, 0);
  try {
    fn();
  } catch (err) {
    assert.ok(err instanceof InputError, String(err));
    return { key: err.key, cause: err.cause };
  } finally {
    chmodSync(path, 0o755);
  }
  assert.fail("expected a refusal");
}

function evidenceCopy(): { dir: string; bundle: string } {
  const dir = mkdtempSync(join(tmpdir(), "agentce-unreadable-"));
  const bundle = join(dir, "evidence");
  cpSync(QUICKSTART_EVIDENCE, bundle, { recursive: true });
  return { dir, bundle };
}

test("verify unreadable input: a bundle directory names manifest.json", { skip: asRoot }, () => {
  const { dir, bundle } = evidenceCopy();
  try {
    assert.deepEqual(
      unreadableRefusal(bundle, () => loadBundle(bundle)),
      {
        key: "input.bundle_unreadable",
        cause: `the evidence bundle ${bundle} holds a file or folder that cannot be read: manifest.json.`,
      },
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test(
  "verify unreadable input: an events/ directory names a file under it",
  { skip: asRoot },
  () => {
    const { dir, bundle } = evidenceCopy();
    try {
      const { key, cause } = unreadableRefusal(join(bundle, "events"), () => loadBundle(bundle));
      assert.equal(key, "input.bundle_unreadable");
      assert.match(cause, /cannot be read: events\//);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  },
);

test("verify unreadable input: a stream file is named", { skip: asRoot }, () => {
  const { dir, bundle } = evidenceCopy();
  try {
    assert.deepEqual(
      unreadableRefusal(join(bundle, STREAM), () => loadBundle(bundle)),
      {
        key: "input.bundle_unreadable",
        cause: `the evidence bundle ${bundle} holds a file or folder that cannot be read: ${STREAM}.`,
      },
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verify unreadable input: a release directory", { skip: asRoot }, () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-unreadable-"));
  const release = join(dir, "release");
  mkdirSync(release);
  writeFileSync(join(release, "release-manifest.json"), "{}");
  try {
    const { key, cause } = unreadableRefusal(release, () =>
      verifyRelease(release, TrustRoot.fromDict({ keys: {} })),
    );
    assert.equal(key, "input.release_unreadable");
    assert.ok(cause.startsWith(`the release artifact ${release} `), cause);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test(
  "verify unreadable input: a release artifact in an unreadable folder",
  { skip: asRoot },
  () => {
    const dir = mkdtempSync(join(tmpdir(), "agentce-unreadable-"));
    const release = join(dir, "release");
    mkdirSync(join(release, "sub"), { recursive: true });
    writeFileSync(join(release, "sub", "a.txt"), "a\n");
    writeFileSync(
      join(release, "release-manifest.json"),
      JSON.stringify({ artifacts: [{ name: "sub/a.txt", sha256: "00" }] }),
    );
    writeFileSync(join(release, "signatures.json"), '{"signatures": []}');
    try {
      const { key, cause } = unreadableRefusal(join(release, "sub"), () =>
        verifyRelease(release, TrustRoot.fromDict({ keys: {} })),
      );
      assert.equal(key, "input.release_unreadable");
      assert.ok(cause.endsWith("cannot be read: sub/a.txt."), cause);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  },
);

test(
  "verify unreadable input: a link out of the target into an unreadable folder is an escape",
  { skip: asRoot },
  () => {
    // A link is judged by where it points, as Python's resolve() judges it: the folder it leads to
    // is outside the target, so the member is unsafe (bundle) or missing (release), never
    // "unreadable" inside the target (18.68 verifier r2).
    const { dir, bundle } = evidenceCopy();
    const sec = join(dir, "sec");
    mkdirSync(sec);
    writeFileSync(join(sec, "x"), "outside\n");
    const manifest = join(bundle, "manifest.json");
    writeFileSync(
      manifest,
      readFileSync(manifest, "utf8").replace("events/_benign-quarantine.jsonl", "link-sec"),
    );
    symlinkSync("../sec/x", join(bundle, "link-sec"));
    const release = join(dir, "release");
    mkdirSync(release);
    writeFileSync(
      join(release, "release-manifest.json"),
      JSON.stringify({ artifacts: [{ name: "link-sec", sha256: "00" }] }),
    );
    writeFileSync(join(release, "signatures.json"), "[]");
    symlinkSync("../sec/x", join(release, "link-sec"));
    try {
      assert.equal(
        unreadableRefusal(sec, () => loadBundle(bundle)).key,
        "input.bundle_manifest_path",
      );
      chmodSync(sec, 0);
      let result: ReleaseResult;
      try {
        result = verifyRelease(release, TrustRoot.fromDict({ keys: {} }));
      } finally {
        chmodSync(sec, 0o755);
      }
      assert.equal("reason" in result ? result.reason : undefined, "missing artifact link-sec");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  },
);

test("verify unreadable input: a catalog subdirectory is named", { skip: asRoot }, () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-unreadable-"));
  writeFileSync(join(dir, "catalog.yaml"), "id: demo\n");
  mkdirSync(join(dir, "controls"));
  writeFileSync(join(dir, "controls", "c.yaml"), "id: c\n");
  try {
    assert.deepEqual(
      unreadableRefusal(join(dir, "controls"), () =>
        verifyCatalog(dir, TrustRoot.fromDict({ keys: {} })),
      ),
      {
        key: "input.catalog_unreadable",
        cause: `the catalog directory ${dir} holds a file or folder that cannot be read: controls.`,
      },
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
