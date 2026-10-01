/**
 * `agentce verify`, the offline DSSE/certificate verification primitives (SPEC §8.7, §9.1): ported
 * byte-for-byte from Python's `signing.py` (`TrustRoot`, `verify_certificate`, `verify_envelope`,
 * `verify_catalog_directory`) and `commands/__init__.py`'s `_verify_catalog`/`_verify_release`, as
 * actually rewritten by the Python-side fix for the item's own named defect (every exception this
 * module raises corresponds to a path Python's rewritten source raises the same way; see
 * `signing.py` and `commands/__init__.py` for the reference). `sign.ts` only ever signs; this module
 * is this engine's first verify primitive, so it builds its own `TrustRoot`/certificate machinery
 * rather than reusing anything from `sign.ts` beyond `dssePae`/`keyidFor`.
 */

import { type KeyObject, createHash, createPublicKey, verify as cryptoVerify } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { canonicalize, sha256Hex } from "./canonical";
import { InputError } from "./errors";
import { digestTree } from "./report";
import { dssePae, keyidFor } from "./sign";
import { b64dStrict, pyRepr, pyStr } from "./util";

/** The detached signature a signed catalog (or corpus) directory carries (SPEC §8.7). */
export const CATALOG_SIGNATURE_NAME = "catalog.sig.json";

/** The exact unsigned sentence `verify_catalog_directory` raises when no signature is present. */
const UNSIGNED_SENTENCE =
  "unsigned: catalog.sig.json is absent, so there is no signature to verify (SPEC §8.7).";

/** The fixed 12-byte RFC 8410 SPKI DER prefix every Ed25519 SPKI public key export carries before
 * its 32 raw key bytes -- the same constant `sign.ts` slices off; here it is prepended to go the
 * other direction, from raw bytes to a `KeyObject` `crypto.verify` can use. */
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Reconstructs an Ed25519 public `KeyObject` from its raw 32 bytes (the inverse of `sign.ts`'s own
 * DER-slicing trick). */
export function publicKeyFromRaw(raw: Buffer): KeyObject {
  return createPublicKey({
    key: Buffer.concat([ED25519_SPKI_PREFIX, raw]),
    format: "der",
    type: "spki",
  });
}

/** A base64-decoded key entry: the raw public key bytes and (for a `keys` entry) its identity. */
interface KeyEntry {
  readonly publicKeyRaw: Buffer;
  readonly identity: string;
}
interface AuthorityEntry {
  readonly publicKeyRaw: Buffer;
}

/** Verifies a keyless certificate against the pinned authorities; returns the leaf key and identity
 * it binds. Every way a certificate can be malformed -- not an object, an unknown issuer aside, a
 * missing/non-base64 `signature`, a signature that does not verify, a missing `public_key`/
 * `identity` -- collapses to the one `"certificate signature does not verify"` message, mirroring
 * `verify_certificate`'s own collapse exactly. */
function verifyCertificate(
  cert: unknown,
  authorities: ReadonlyMap<string, AuthorityEntry>,
): KeyEntry {
  if (!isRecord(cert)) {
    throw new Error("certificate signature does not verify");
  }
  const issuer = cert.issuer;
  const ca = typeof issuer === "string" ? authorities.get(issuer) : undefined;
  if (ca === undefined) {
    throw new Error(`unknown certificate issuer ${pyRepr(issuer)}`);
  }
  try {
    const body: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(cert)) {
      if (key !== "signature") {
        body[key] = value;
      }
    }
    const sig = b64dStrict(String(cert.signature));
    const ok = cryptoVerify(null, canonicalize(body), publicKeyFromRaw(ca.publicKeyRaw), sig);
    if (!ok) {
      throw new Error("certificate signature invalid");
    }
    const leafRaw = b64dStrict(String(cert.public_key));
    const identity = cert.identity;
    if (typeof identity !== "string") {
      throw new Error("missing certificate identity");
    }
    return { publicKeyRaw: leafRaw, identity };
  } catch {
    throw new Error("certificate signature does not verify");
  }
}

/** The offline material a verifier trusts: pinned KMS keys and keyless certificate authorities
 * (mirrors `signing.TrustRoot` exactly). */
export class TrustRoot {
  private constructor(
    readonly keys: ReadonlyMap<string, KeyEntry>,
    readonly authorities: ReadonlyMap<string, AuthorityEntry>,
  ) {}

  /** Loads a trust root from its JSON shape. Every `keys` entry's declared id must equal
   * `keyidFor` of the key it maps to -- a forged/corrupted entry (the real signer's own keyid
   * mapped to an attacker's key) is refused, not silently accepted (`signing.py:295-297`). */
  static fromDict(data: unknown): TrustRoot {
    const root = isRecord(data) ? data : {};
    const keys = new Map<string, KeyEntry>();
    const keysIn = isRecord(root.keys) ? root.keys : {};
    for (const [keyid, raw] of Object.entries(keysIn)) {
      const entry = isRecord(raw) ? raw : {};
      const publicKeyRaw = b64dStrict(String(entry.public_key));
      if (keyidFor(publicKeyRaw) !== keyid) {
        throw new Error(`trust root entry ${pyRepr(keyid)} does not match its own key`);
      }
      const identity = typeof entry.identity === "string" ? entry.identity : keyid;
      keys.set(keyid, { publicKeyRaw, identity });
    }
    const authorities = new Map<string, AuthorityEntry>();
    const authsIn = isRecord(root.certificate_authorities) ? root.certificate_authorities : {};
    for (const [issuer, raw] of Object.entries(authsIn)) {
      const entry = isRecord(raw) ? raw : {};
      authorities.set(issuer, { publicKeyRaw: b64dStrict(String(entry.public_key)) });
    }
    return new TrustRoot(keys, authorities);
  }

  /** Resolves a DSSE signature entry to the key/identity that must verify it (`cert` checked before
   * `keyid`, matching `signing.py:324-329`'s own order). */
  resolve(signature: Record<string, unknown>): KeyEntry {
    const cert = signature.cert;
    if (cert !== undefined && cert !== null) {
      return verifyCertificate(cert, this.authorities);
    }
    const keyid = signature.keyid;
    if (typeof keyid !== "string" || !this.keys.has(keyid)) {
      throw new Error(`no trusted key for keyid ${pyRepr(keyid)}`);
    }
    return this.keys.get(keyid) as KeyEntry;
  }
}

/** The path to the trust root vendored in the engine package (a byte-for-byte copy of
 * `engines/python/agentce/data/trust/dev-root.json`, pinned by `bundledData.test.ts`'s sibling
 * sync test, `verify.test.ts`). */
export function vendoredTrustPath(): string {
  return join(__dirname, "..", "data", "trust", "dev-root.json");
}

/** Reads and parses a trust root file (`--trust-root`-shaped JSON) at `path`. */
export function loadTrustRoot(path: string): TrustRoot {
  return TrustRoot.fromDict(JSON.parse(readFileSync(path, "utf-8")));
}

/** Loads the trust root vendored in the engine package. */
export function vendoredTrust(): TrustRoot {
  return loadTrustRoot(vendoredTrustPath());
}

export interface VerifiedEnvelope {
  readonly payload: Buffer;
  readonly identity: string;
  readonly keyid: string | null;
  readonly keyless: boolean;
}

/** Verifies a DSSE envelope against `trust`; throws on any failure. Shape is validated explicitly,
 * one ordered check at a time -- mirrors `verify_envelope` exactly as rewritten by the Python-side
 * fix, so the two are a real mirror, not an analogy (see `signing.py:365-417`). */
export function verifyEnvelope(envelope: unknown, trust: TrustRoot): VerifiedEnvelope {
  if (!isRecord(envelope)) {
    throw new Error("malformed DSSE envelope");
  }
  const payloadType = envelope.payloadType;
  if (typeof payloadType !== "string") {
    throw new Error("malformed DSSE envelope");
  }
  const rawPayload = envelope.payload;
  if (typeof rawPayload !== "string") {
    throw new Error("malformed DSSE envelope");
  }
  let payload: Buffer;
  try {
    payload = b64dStrict(rawPayload);
  } catch {
    throw new Error("malformed DSSE envelope");
  }
  const signatures = envelope.signatures;
  if (!Array.isArray(signatures)) {
    throw new Error("malformed DSSE envelope");
  }
  if (signatures.length === 0) {
    throw new Error("DSSE envelope carries no signatures");
  }
  const pae = dssePae(payloadType, payload);
  let lastError = "";
  for (const entry of signatures) {
    try {
      if (!isRecord(entry)) {
        // A non-object entry has no keyid to resolve; route it through the same missing-keyid
        // message `TrustRoot.resolve` gives for an absent `keyid`, not a second ad-hoc string.
        throw new Error(`no trusted key for keyid ${pyRepr(undefined)}`);
      }
      const { publicKeyRaw, identity } = trust.resolve(entry);
      const sig = entry.sig;
      if (typeof sig !== "string") {
        // Mirrors Python's `KeyError` repr for the same missing-key access.
        throw new Error("'sig'");
      }
      const sigBytes = b64dStrict(sig);
      const ok = cryptoVerify(null, pae, publicKeyFromRaw(publicKeyRaw), sigBytes);
      if (!ok) {
        // An empty message, matching `cryptography`'s `InvalidSignature` -- `str()` of which is
        // empty -- so the aggregate message below renders with nothing after the colon-space.
        throw new Error("");
      }
      const keyidVal = entry.keyid;
      return {
        payload,
        identity,
        keyid: typeof keyidVal === "string" ? keyidVal : null,
        keyless: "cert" in entry,
      };
    } catch (exc) {
      lastError = exc instanceof Error ? exc.message : String(exc);
    }
  }
  throw new Error(`no signature verified against the trust root: ${lastError}`);
}

/** Mirrors `signing.statement_subject_digest`: the `sha256:` digest of an in-toto Statement's
 * single subject. A statement with no `subject` key renders the same `"'subject'"` `KeyError`-repr
 * text Python gives (pinned); every other malformed shape is disclosed as shape-only. */
function statementSubjectDigest(statement: unknown): string {
  if (!isRecord(statement) || !("subject" in statement)) {
    throw new Error("'subject'");
  }
  const subject = statement.subject;
  if (!Array.isArray(subject) || subject.length === 0) {
    throw new Error("list index out of range");
  }
  const first = subject[0];
  const digest = isRecord(first) ? first.digest : undefined;
  if (!isRecord(digest) || typeof digest.sha256 !== "string") {
    throw new Error("'sha256'");
  }
  return `sha256:${digest.sha256}`;
}

/** Reads a JSON file with a fatal UTF-8 decode (Node's lenient `utf-8` string coercion would
 * otherwise silently replace invalid byte sequences with U+FFFD rather than refuse, unlike Python's
 * `read_text("utf-8")`); throws on a decode or parse failure, never on a valid-JSON-but-wrong-shape
 * value (the caller decides what "wrong shape" means). */
function readJsonFileStrict(path: string): unknown {
  const text = new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(path));
  return JSON.parse(text);
}

export interface CatalogVerifyResult {
  readonly verified: boolean;
  readonly digest: string;
  readonly signer?: string;
  readonly keyid?: string | null;
  readonly keyless?: boolean;
  readonly reason?: string;
}

/** Verifies a catalog directory's detached signature against `trust` (mirrors `_verify_catalog`/
 * `verify_catalog_directory`, `signing.py:426-461`, `commands/__init__.py:321-352`). */
export function verifyCatalog(dir: string, trust: TrustRoot): CatalogVerifyResult {
  const digest = digestTree(dir, new Set([CATALOG_SIGNATURE_NAME]));
  const sigPath = join(dir, CATALOG_SIGNATURE_NAME);
  let sigIsFile = false;
  try {
    sigIsFile = statSync(sigPath).isFile();
  } catch {
    sigIsFile = false;
  }
  if (!sigIsFile) {
    return { verified: false, digest, reason: UNSIGNED_SENTENCE };
  }
  let envelope: unknown;
  try {
    envelope = readJsonFileStrict(sigPath);
  } catch (exc) {
    const message = exc instanceof Error ? exc.message : String(exc);
    return {
      verified: false,
      digest,
      reason: `${CATALOG_SIGNATURE_NAME} is not readable JSON: ${message}`,
    };
  }
  let verified: VerifiedEnvelope;
  try {
    verified = verifyEnvelope(envelope, trust);
  } catch (exc) {
    const message = exc instanceof Error ? exc.message : String(exc);
    return { verified: false, digest, reason: message };
  }
  let signedDigest: string;
  try {
    const statement = JSON.parse(verified.payload.toString("utf-8"));
    signedDigest = statementSubjectDigest(statement);
  } catch (exc) {
    const message = exc instanceof Error ? exc.message : String(exc);
    return {
      verified: false,
      digest,
      reason: `the signed statement carries no catalog digest: ${message}`,
    };
  }
  if (signedDigest !== digest) {
    return {
      verified: false,
      digest,
      reason: "the signature covers a different catalog digest than the directory content",
    };
  }
  return {
    verified: true,
    digest,
    signer: verified.identity,
    keyid: verified.keyid,
    keyless: verified.keyless,
  };
}

/** The shared soft-fail shape for a release that cannot be verified at all (no `manifestDigest`/
 * `signers` field): mirrors `_verify_release_soft_fail`'s own three-field shape exactly. */
export interface ReleaseSoftFail {
  readonly release: string;
  readonly verified: false;
  readonly reason: string;
}

export interface ReleaseSingleResult {
  readonly release: string;
  readonly verified: true;
  readonly signer: string;
  readonly keyid: string | null;
  readonly keyless: boolean;
}

export interface ReleaseBundleResult {
  readonly release: string;
  readonly verified: boolean;
  readonly manifestDigest: string;
  readonly signers: ReadonlyArray<{ readonly profile: unknown; readonly identity: string }>;
  readonly reason?: string;
}

export type ReleaseResult = ReleaseSoftFail | ReleaseSingleResult | ReleaseBundleResult;

/** Verifies a release bundle (or a single DSSE envelope) offline against `trust` -- the item's core
 * fix target, built correct from the start: every branch uses the soft-fail shape, and every new
 * JSON-parse-failure path this function adds uses a fixed, engine-neutral reason text (mirrors
 * `_verify_release`, `commands/__init__.py:758-875`). */
export function verifyRelease(releasePath: string, trust: TrustRoot): ReleaseResult {
  const stat = statSync(releasePath);
  if (stat.isFile()) {
    let envelope: unknown;
    try {
      envelope = readJsonFileStrict(releasePath);
    } catch {
      return {
        release: releasePath,
        verified: false,
        reason: "release envelope is not readable JSON",
      };
    }
    try {
      const verified = verifyEnvelope(envelope, trust);
      return {
        release: releasePath,
        verified: true,
        signer: verified.identity,
        keyid: verified.keyid,
        keyless: verified.keyless,
      };
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      return { release: releasePath, verified: false, reason: message };
    }
  }

  const manifestPath = join(releasePath, "release-manifest.json");
  const signaturesPath = join(releasePath, "signatures.json");
  const manifestExists = existsSync(manifestPath) && statSync(manifestPath).isFile();
  const signaturesExist = existsSync(signaturesPath) && statSync(signaturesPath).isFile();
  if (!manifestExists || !signaturesExist) {
    throw new InputError(
      "input.release_bundle",
      `${releasePath} is not a release bundle (release-manifest.json/signatures.json).`,
      "pass the --out directory produced by the release tooling.",
    );
  }

  let manifest: unknown;
  try {
    manifest = readJsonFileStrict(manifestPath);
  } catch {
    return {
      release: releasePath,
      verified: false,
      reason: "release manifest is not readable JSON",
    };
  }
  if (!isRecord(manifest)) {
    return {
      release: releasePath,
      verified: false,
      reason: "release manifest is not readable JSON",
    };
  }

  const manifestDigest = `sha256:${sha256Hex(manifest)}`;
  const problems: string[] = [];
  const artifacts = Array.isArray(manifest.artifacts) ? manifest.artifacts : [];
  for (const raw of artifacts) {
    const artifact = isRecord(raw) ? raw : {};
    const name = artifact.name;
    if (typeof name !== "string") {
      problems.push("release manifest has an artifact entry with no name");
      continue;
    }
    const artifactFile = join(releasePath, name);
    let artifactIsFile = false;
    try {
      artifactIsFile = statSync(artifactFile).isFile();
    } catch {
      artifactIsFile = false;
    }
    if (!artifactIsFile) {
      problems.push(`missing artifact ${name}`);
      continue;
    }
    const actual = `sha256:${createHash("sha256").update(readFileSync(artifactFile)).digest("hex")}`;
    if (actual !== artifact.digest) {
      problems.push(`digest mismatch for ${name}`);
    }
  }

  let signatureEntries: unknown;
  try {
    signatureEntries = readJsonFileStrict(signaturesPath);
  } catch {
    return {
      release: releasePath,
      verified: false,
      manifestDigest,
      signers: [],
      reason: "release signatures are not readable JSON",
    };
  }
  if (!Array.isArray(signatureEntries)) {
    return {
      release: releasePath,
      verified: false,
      manifestDigest,
      signers: [],
      reason: "release signatures are not readable JSON",
    };
  }

  const signers: Array<{ profile: unknown; identity: string }> = [];
  for (const raw of signatureEntries) {
    if (!isRecord(raw)) {
      problems.push("signature (None): signature entry is not an object");
      continue;
    }
    const profile = raw.profile;
    try {
      if (!("envelope" in raw)) {
        throw new Error("'envelope'");
      }
      const verified = verifyEnvelope(raw.envelope, trust);
      const statement = JSON.parse(verified.payload.toString("utf-8"));
      if (statementSubjectDigest(statement) !== manifestDigest) {
        throw new Error("signature does not cover the release manifest");
      }
      signers.push({ profile, identity: verified.identity });
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      problems.push(`signature (${pyStr(profile)}): ${message}`);
    }
  }

  if (problems.length === 0) {
    return { release: releasePath, verified: true, manifestDigest, signers };
  }
  return {
    release: releasePath,
    verified: false,
    manifestDigest,
    signers,
    reason: problems.join("; "),
  };
}
