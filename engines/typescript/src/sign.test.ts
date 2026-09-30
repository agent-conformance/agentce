/**
 * `sign.ts`'s DSSE/in-toto primitives (item 18.26): the compute seam `cli.ts`'s `cmdSign` wires up.
 * `cli.test.ts` covers the full `agentce sign` command end to end.
 */

import assert from "node:assert/strict";
import { createPublicKey, verify as cryptoVerify, generateKeyPairSync } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { InputError } from "./errors";
import { KmsSigner, dssePae, keyidFor, signStatement, signSubjects } from "./sign";

function ed25519PemPair(): { privatePem: string; rawPublicKey: Buffer } {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const privatePem = privateKey.export({ type: "pkcs8", format: "pem" }).toString();
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  return { privatePem, rawPublicKey: Buffer.from(publicDer.subarray(12)) };
}

test("dssePae builds the exact DSSEv1 pre-authentication-encoding bytes", () => {
  const payload = Buffer.from("hello", "utf-8");
  const pae = dssePae("application/vnd.in-toto+json", payload);
  assert.equal(pae.toString("utf-8"), "DSSEv1 28 application/vnd.in-toto+json 5 hello");
});

test("dssePae never double-encodes a payload containing raw non-UTF-8-safe bytes", () => {
  const payload = Buffer.from([0xff, 0x00, 0x41]);
  const pae = dssePae("t", payload);
  // "DSSEv1 " (7) + "1" (1) + " " (1) + "t" (1) + " " (1) + "3" (1) + " " (1) = 13 bytes of header.
  assert.equal(pae.length, 13 + payload.length);
  assert.deepEqual(pae.subarray(13), payload);
});

test("keyidFor is sha256: over the raw public key bytes", () => {
  const { rawPublicKey } = ed25519PemPair();
  const keyid = keyidFor(rawPublicKey);
  assert.ok(keyid.startsWith("sha256:"));
  assert.equal(keyid.length, "sha256:".length + 64);
});

test("KmsSigner.load derives the correct paired public key from a private-key-only PEM", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-sign-"));
  try {
    const { privatePem, rawPublicKey } = ed25519PemPair();
    const keyPath = join(dir, "key.pem");
    writeFileSync(keyPath, privatePem);
    const signer = KmsSigner.load(keyPath);
    assert.equal(signer.publicKeyB64, rawPublicKey.toString("base64"));
    assert.equal(signer.keyid, keyidFor(rawPublicKey));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("KmsSigner.sign produces a signature that verifies against the derived public key", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-sign-"));
  try {
    const { privatePem, rawPublicKey } = ed25519PemPair();
    const keyPath = join(dir, "key.pem");
    writeFileSync(keyPath, privatePem);
    const signer = KmsSigner.load(keyPath);
    const data = Buffer.from("some data to sign", "utf-8");
    const sig = signer.sign(data);
    const publicKeyObject = createPublicKey({
      key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), rawPublicKey]),
      format: "der",
      type: "spki",
    });
    assert.equal(cryptoVerify(null, data, publicKeyObject, sig), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("KmsSigner.load rejects a non-Ed25519 (RSA) key with sign.key_algorithm", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-sign-"));
  try {
    const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
    const keyPath = join(dir, "key.pem");
    writeFileSync(keyPath, privateKey.export({ type: "pkcs8", format: "pem" }));
    assert.throws(
      () => KmsSigner.load(keyPath),
      (err: unknown) => err instanceof InputError && err.key === "sign.key_algorithm",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("KmsSigner.load rejects an unparseable file with sign.key_unreadable", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-sign-"));
  try {
    const keyPath = join(dir, "key.pem");
    writeFileSync(keyPath, "not a pem file at all\n");
    assert.throws(
      () => KmsSigner.load(keyPath),
      (err: unknown) => err instanceof InputError && err.key === "sign.key_unreadable",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("KmsSigner.load rejects a password-protected key with sign.key_unreadable", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-sign-"));
  try {
    const { privateKey } = generateKeyPairSync("ed25519");
    const keyPath = join(dir, "key.pem");
    writeFileSync(
      keyPath,
      privateKey.export({
        type: "pkcs8",
        format: "pem",
        cipher: "aes-256-cbc",
        passphrase: "sekret",
      }),
    );
    assert.throws(
      () => KmsSigner.load(keyPath),
      (err: unknown) => err instanceof InputError && err.key === "sign.key_unreadable",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("signStatement canonicalizes the statement, signs its PAE, and base64-encodes payload/signature; the envelope carries no cert field", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-sign-"));
  try {
    const { privatePem, rawPublicKey } = ed25519PemPair();
    const keyPath = join(dir, "key.pem");
    writeFileSync(keyPath, privatePem);
    const signer = KmsSigner.load(keyPath);
    const statement = { b: 2, a: 1 };
    const envelope = signStatement(statement, signer);
    assert.equal(envelope.payloadType, "application/vnd.in-toto+json");
    assert.equal(envelope.signatures.length, 1);
    assert.equal(envelope.signatures[0]?.keyid, signer.keyid);
    assert.ok(!("cert" in (envelope.signatures[0] as object)));
    const payload = Buffer.from(envelope.payload, "base64");
    assert.equal(payload.toString("utf-8"), '{"a":1,"b":2}');
    const publicKeyObject = createPublicKey({
      key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), rawPublicKey]),
      format: "der",
      type: "spki",
    });
    const sig = Buffer.from(envelope.signatures[0]?.sig as string, "base64");
    assert.equal(
      cryptoVerify(null, dssePae("application/vnd.in-toto+json", payload), publicKeyObject, sig),
      true,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("signSubjects hashes the claim body (minus signatures) and, when present, the manifest's raw bytes", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-sign-"));
  try {
    const claim = { claimant: { org: "acme" }, signatures: [{ keyid: "x", sig: "y" }] };
    const subjectsNoManifest = signSubjects(dir, claim);
    assert.equal(subjectsNoManifest.length, 1);
    assert.equal(subjectsNoManifest[0]?.name, "claim.json");

    writeFileSync(join(dir, "manifest.json"), '{"k":"v"}');
    const subjectsWithManifest = signSubjects(dir, claim);
    assert.equal(subjectsWithManifest.length, 2);
    assert.equal(subjectsWithManifest[1]?.name, "manifest.json");
    // The claim.json digest is identical whether or not `signatures` is present, since it is always
    // excluded from the hashed body.
    const claimNoSignatures = { claimant: { org: "acme" } };
    const subjectsNoSig = signSubjects(dir, claimNoSignatures);
    assert.equal(subjectsNoSig[0]?.digest.sha256, subjectsWithManifest[0]?.digest.sha256);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
