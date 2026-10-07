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
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { confineToRoot } from "./bundle";
import { CanonicalizationError, canonicalize, sha256Hex } from "./canonical";
import { errorCause } from "./errorCatalogue";
import {
  InputError,
  isPermissionError,
  readPermissionError,
  unreadableError,
  unreadableRel,
} from "./errors";
import { NonCanonicalNumber, parseJson } from "./json";
import { pyTruthy } from "./readiness";
import { digestTree } from "./report";
import { dssePae, keyidFor } from "./sign";
import { b64dStrict, decodeUtf8Strict, formatTemplate, pyRepr } from "./util";

/** The detached signature a signed catalog (or corpus) directory carries (SPEC §8.7). */
export const CATALOG_SIGNATURE_NAME = "catalog.sig.json";

/** A verify refusal: its text, and the catalogue keys the text was built from in reading order (one
 * key for a leaf refusal, a wrapper's own key followed by its inner refusal's keys). `verify --json`
 * lists them as `reason_keys`. Mirrors `signing.VerificationError`. */
export class VerifyRefusal extends Error {
  readonly keys: readonly string[];

  constructor(text: string, keys: readonly string[]) {
    super(text);
    this.keys = keys;
  }
}

/** The refusal for catalogue `key`: its cause with `{params}` filled in. A wrapper passes the error it
 * wraps as `inner`, which fills `{inner}` and adds its keys. Mirrors `VerificationError.refusal`. */
export function refusal(
  key: string,
  params: Record<string, string> = {},
  inner?: unknown,
): VerifyRefusal {
  const keys = [key];
  const vars = { ...params };
  if (inner !== undefined) {
    vars.inner = inner instanceof Error ? inner.message : String(inner);
    if (inner instanceof VerifyRefusal) {
      keys.push(...inner.keys);
    }
  }
  return new VerifyRefusal(formatTemplate(errorCause(key), vars), keys);
}

/** The fixed 12-byte RFC 8410 SPKI DER prefix every Ed25519 SPKI public key export carries before
 * its 32 raw key bytes -- the same constant `sign.ts` slices off; here it is prepended to go the
 * other direction, from raw bytes to a `KeyObject` `crypto.verify` can use. */
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    !(value instanceof NonCanonicalNumber)
  );
}

/** The `(id, entry)` pairs of a trust root's `keys` or `certificate_authorities`, as Python's
 * `signing._trust_entries`: a falsy value is no entries, and each malformed shape gets one stable
 * cause, the same words in all three engines (18.80). */
function* trustEntries(
  data: Record<string, unknown>,
  field: string,
  shape: string,
): Generator<[string, Record<string, unknown>]> {
  const value = data[field];
  let mapping: Record<string, unknown> = {};
  if (isRecord(value)) {
    mapping = value;
  } else if (pyTruthy(value)) {
    throw new Error(`${field} is not a mapping of ${shape}`);
  }
  for (const [id, entry] of Object.entries(mapping)) {
    if (!isRecord(entry)) {
      throw new Error(`${field} entry ${pyRepr(id)} is not a mapping`);
    }
    if (entry.public_key === undefined || entry.public_key === null) {
      throw new Error(`${field} entry ${pyRepr(id)} has no public_key`);
    }
    yield [id, entry];
  }
}

/** The deepest container nesting {@link parseUntrustedJson} accepts (mirrors `signing.MAX_JSON_DEPTH`,
 * Jackson's own default); all three engines enforce it with the same byte pre-scan. */
export const MAX_JSON_DEPTH = 1000;

/** The generic text of the untrusted parser's own refusals; it names no parser error. */
const NOT_READABLE_JSON = "not readable JSON";

/** A document nested past {@link MAX_JSON_DEPTH}; its message stays "not readable JSON" for callers
 * that do not tell depth apart. Mirrors `signing.JsonTooDeep`. */
export class JsonTooDeep extends Error {
  constructor() {
    super(NOT_READABLE_JSON);
  }
}

/** Whether `raw` opens more than {@link MAX_JSON_DEPTH} containers at once, counting `[` and `{` up and
 * `]` and `}` down outside strings (a backslash in a string skips the next byte). Run before parsing,
 * so depth is found first whatever else is wrong with the bytes. Mirrors `signing._too_deep`. */
function tooDeep(raw: Buffer): boolean {
  let depth = 0;
  let inString = false;
  for (let i = 0; i < raw.length; i++) {
    const byte = raw[i];
    if (inString) {
      if (byte === 0x5c) {
        i++;
      } else if (byte === 0x22) {
        inString = false;
      }
    } else if (byte === 0x22) {
      inString = true;
    } else if (byte === 0x5b || byte === 0x7b) {
      depth++;
      if (depth > MAX_JSON_DEPTH) {
        return true;
      }
    } else if (byte === 0x5d || byte === 0x7d) {
      depth--;
    }
  }
  return false;
}

const LONE_SURROGATE = /[\uD800-\uDFFF]/u;
const PLAIN_ASCII = /^[ !#-&(-\[\]-~]*$/;

/** Mirrors `signing.parse_untrusted_json`: parse JSON `verify` reads from an untrusted file or signed
 * payload, or throw. Strict UTF-8 with no byte-order mark, standard JSON only, containers nested at
 * most {@link MAX_JSON_DEPTH} deep, and every string and key well-formed Unicode. With
 * `keepNumberTokens`, a non-canonical number token survives as a {@link NonCanonicalNumber} so the
 * canonical form can still refuse it (the release manifest). Callers turn the error into their own
 * fixed reason text; its message is never shown. A document too deep throws {@link JsonTooDeep},
 * whatever else is wrong with it. */
export function parseUntrustedJson(raw: Buffer, keepNumberTokens = false): unknown {
  if (tooDeep(raw)) {
    throw new JsonTooDeep();
  }
  const text = decodeUtf8Strict(raw);
  const value = keepNumberTokens ? parseJson(text) : JSON.parse(text);
  const stack: unknown[] = [value];
  while (stack.length > 0) {
    const node = stack.pop();
    if (typeof node === "string") {
      if (LONE_SURROGATE.test(node)) {
        throw new Error(NOT_READABLE_JSON);
      }
    } else if (Array.isArray(node)) {
      for (const child of node) stack.push(child);
    } else if (isRecord(node)) {
      for (const [key, child] of Object.entries(node)) stack.push(child, key);
    }
  }
  return value;
}

/** {@link parseUntrustedJson} for one input verify reads, refused with that input's own key:
 * `verify.json_too_deep` naming `what` past the depth limit, else `unreadableKey`. Mirrors
 * `signing.parse_verify_input`. */
function parseVerifyInput(
  raw: Buffer,
  unreadableKey: string,
  what: string,
  keepNumberTokens = false,
): unknown {
  try {
    return parseUntrustedJson(raw, keepNumberTokens);
  } catch (exc) {
    if (exc instanceof JsonTooDeep) {
      throw refusal("verify.json_too_deep", { what, limit: String(MAX_JSON_DEPTH) });
    }
    throw refusal(unreadableKey);
  }
}

/** {@link parseUntrustedJson} over a file's bytes (mirrors Java's `readUntrustedJsonFile`). */
export function readUntrustedJsonFile(path: string, keepNumberTokens = false): unknown {
  return parseUntrustedJson(readFileSync(path), keepNumberTokens);
}

/** Mirrors `signing.describe_untrusted`: how a refusal names a value read from an untrusted document
 * (a keyid, an issuer) -- `None`, a quoted plain-ASCII string, or a fixed description. */
export function describeUntrusted(value: unknown): string {
  if (value === null || value === undefined) {
    return "None";
  }
  if (typeof value === "string") {
    return PLAIN_ASCII.test(value) ? `'${value}'` : "<a string with special characters>";
  }
  return "<not a string>";
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
    throw refusal("verify.certificate_signature_invalid");
  }
  const issuer = cert.issuer;
  const ca = typeof issuer === "string" ? authorities.get(issuer) : undefined;
  if (ca === undefined) {
    throw refusal("verify.certificate_issuer_unknown", { issuer: describeUntrusted(issuer) });
  }
  let leaf: KeyEntry;
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
    publicKeyFromRaw(leafRaw); // validate eagerly, as Python's `load_public_ed25519` does, so a
    // malformed leaf key collapses to this function's own message, not a later deferred one.
    const identity = cert.identity;
    if (typeof identity !== "string") {
      throw new Error("missing certificate identity");
    }
    leaf = { publicKeyRaw: leafRaw, identity };
  } catch {
    throw refusal("verify.certificate_signature_invalid");
  }
  checkCertificateFields(cert);
  return leaf;
}

/** RFC 3339 `date-time` in UTC (§5.6): `T`/`Z` in either case, ASCII digits only, an optional fraction. */
const RFC3339_UTC =
  /^([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?[Zz]$/;
const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

/** `<key>: <cause>` with the catalogue's cause text, minus its final period (the aggregate reasons
 * that wrap it add their own); mirrors `signing._certificate_refusal`. */
function certificateRefusal(key: string): VerifyRefusal {
  return new VerifyRefusal(`${key}: ${errorCause(key).replace(/\.$/, "")}`, [key]);
}

/** A text form of an RFC 3339 UTC timestamp that sorts in time order, or `null` if `value` is not one:
 * the 14 date-time digits, then the fraction without trailing zeros. Mirrors `signing._utc_order_key`. */
function utcOrderKey(value: unknown): string | null {
  const match = typeof value === "string" ? RFC3339_UTC.exec(value) : null;
  if (match === null) {
    return null;
  }
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  if (month < 1 || month > 12) {
    return null;
  }
  const days = month === 2 && leap ? 29 : DAYS_IN_MONTH[month - 1];
  if (day < 1 || day > days || hour > 23 || minute > 59 || second > 60) {
    return null;
  }
  return `${match.slice(1, 7).join("")}.${(match[7] ?? "").replace(/0+$/, "")}`;
}

/** `algorithm` exactly `ed25519`, and a well-formed RFC 3339 UTC window with `not_before <=
 * not_after`, never compared with the clock; mirrors `signing._check_certificate_fields`. */
function checkCertificateFields(cert: Record<string, unknown>): void {
  if (cert.algorithm !== "ed25519") {
    throw certificateRefusal("verify.certificate_algorithm");
  }
  const notBefore = utcOrderKey(cert.not_before);
  const notAfter = utcOrderKey(cert.not_after);
  if (notBefore === null || notAfter === null) {
    throw certificateRefusal("verify.certificate_validity_malformed");
  }
  if (notBefore > notAfter) {
    throw certificateRefusal("verify.certificate_validity_inverted");
  }
}

/** Marks an error `TrustRoot.fromDict` raises directly, itself (the content-addressing check),
 * mirroring Python's own `VerificationError` raised directly inside `from_dict` -- as opposed to a
 * bare language exception (`.items()` on a non-mapping, a malformed key's `AttributeError`/
 * `KeyError`/`TypeError`/`ValueError`) that `load_trust_root` catches and re-wraps. */
class TrustRootError extends Error {}

/** The offline material a verifier trusts: pinned KMS keys and keyless certificate authorities
 * (mirrors `signing.TrustRoot` exactly). */
export class TrustRoot {
  private constructor(
    readonly keys: ReadonlyMap<string, KeyEntry>,
    readonly authorities: ReadonlyMap<string, AuthorityEntry>,
  ) {}

  /** Loads a trust root from its JSON shape. Every `keys` entry's declared id must equal
   * `keyidFor` of the key it maps to (`signing.py:295-297`). The top-level-is-an-object check is
   * `loadTrustRoot`'s, as in Python (`signing.py:570-571`). `keys`/`certificate_authorities` go
   * through {@link trustEntries}, Python's `_trust_entries`. */
  static fromDict(data: Record<string, unknown>): TrustRoot {
    const keys = new Map<string, KeyEntry>();
    for (const [keyid, entry] of trustEntries(data, "keys", "key id to key entry")) {
      const publicKeyRaw = b64dStrict(String(entry.public_key));
      if (keyidFor(publicKeyRaw) !== keyid) {
        // Python's `from_dict` raises its own `VerificationError` here directly, so
        // `load_trust_root`'s `except (AttributeError, KeyError, TypeError, ValueError)` never
        // catches and re-wraps it -- `TrustRootError` marks it the same way, so `loadTrustRoot`
        // below passes it through unwrapped too.
        throw new TrustRootError(`trust root entry ${pyRepr(keyid)} does not match its own key`);
      }
      const identity = typeof entry.identity === "string" ? entry.identity : keyid;
      keys.set(keyid, { publicKeyRaw, identity });
    }
    const authorities = new Map<string, AuthorityEntry>();
    for (const [issuer, entry] of trustEntries(data, "certificate_authorities", "id to entry")) {
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
      throw refusal("verify.keyid_untrusted", { keyid: describeUntrusted(keyid) });
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

/** Reads a trust root file (`--trust-root`-shaped JSON), mirroring `signing.load_trust_root`: the
 * bytes go through {@link parseUntrustedJson}, the rule all three engines' loaders share (18.81), with
 * a fixed text past {@link MAX_JSON_DEPTH}; the top level must be an object (`signing.py:570-571`), and
 * any non-`TrustRootError` that `fromDict` throws is re-wrapped as Python's `except (AttributeError,
 * KeyError, TypeError, ValueError)` does (`signing.py:572-578`). */
export function loadTrustRoot(path: string): TrustRoot {
  let data: unknown;
  try {
    data = readUntrustedJsonFile(path);
  } catch (exc) {
    if (exc instanceof JsonTooDeep) {
      const tooDeep = refusal("verify.json_too_deep", {
        what: "it",
        limit: String(MAX_JSON_DEPTH),
      });
      throw new Error(`${path} is not readable JSON: ${tooDeep.message}`);
    }
    // The shared parser's own refusals (a lone surrogate) carry only its generic text, and name nothing.
    const message = exc instanceof Error ? exc.message : String(exc);
    throw new Error(
      `${path} is not readable JSON${message === NOT_READABLE_JSON ? "" : `: ${message}`}`,
    );
  }
  if (!isRecord(data)) {
    throw new Error(`${path} does not hold a trust-root object`);
  }
  try {
    return TrustRoot.fromDict(data);
  } catch (exc) {
    if (exc instanceof TrustRootError) {
      throw exc;
    }
    const message = exc instanceof Error ? exc.message : String(exc);
    throw new Error(`${path} is not a usable trust root: ${message}`);
  }
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
    throw refusal("verify.envelope_malformed");
  }
  const payloadType = envelope.payloadType;
  if (typeof payloadType !== "string") {
    throw refusal("verify.envelope_malformed");
  }
  const rawPayload = envelope.payload;
  if (typeof rawPayload !== "string") {
    throw refusal("verify.envelope_malformed");
  }
  let payload: Buffer;
  try {
    payload = b64dStrict(rawPayload);
  } catch {
    throw refusal("verify.envelope_malformed");
  }
  const signatures = envelope.signatures;
  if (!Array.isArray(signatures)) {
    throw refusal("verify.envelope_malformed");
  }
  if (signatures.length === 0) {
    throw refusal("verify.envelope_no_signatures");
  }
  const pae = dssePae(payloadType, payload);
  let lastError: unknown;
  for (const entry of signatures) {
    try {
      if (!isRecord(entry)) {
        // A non-object entry has no keyid to resolve; route it through the same missing-keyid
        // refusal `TrustRoot.resolve` gives for an absent `keyid`.
        throw refusal("verify.keyid_untrusted", { keyid: describeUntrusted(null) });
      }
      const { publicKeyRaw, identity } = trust.resolve(entry);
      const sig = entry.sig;
      if (typeof sig !== "string") {
        throw refusal("verify.signature_sig_missing");
      }
      let sigBytes: Buffer;
      try {
        sigBytes = b64dStrict(sig);
      } catch {
        throw refusal("verify.signature_not_base64");
      }
      const ok = cryptoVerify(null, pae, publicKeyFromRaw(publicKeyRaw), sigBytes);
      if (!ok) {
        // A bad signature has a sentence of its own (18.68).
        throw refusal("verify.signature_invalid");
      }
      const keyidVal = entry.keyid;
      return {
        payload,
        identity,
        keyid: typeof keyidVal === "string" ? keyidVal : null,
        // `resolve`'s own test: a "cert": null entry resolves as a key (18.68).
        keyless: entry.cert !== undefined && entry.cert !== null,
      };
    } catch (exc) {
      lastError = exc;
    }
  }
  throw refusal("verify.no_signature_verified", {}, lastError);
}

/** Mirrors `signing.statement_subject_digest`: the `sha256:` digest of the first subject of the
 * in-toto Statement in `payload`, or one of two fixed refusals. */
function statementSubjectDigest(payload: Buffer): string {
  const statement = parseVerifyInput(
    payload,
    "verify.statement_unreadable",
    "the signed statement",
  );
  const subject = isRecord(statement) ? statement.subject : undefined;
  const first = Array.isArray(subject) && subject.length > 0 ? subject[0] : undefined;
  const digest = isRecord(first) ? first.digest : undefined;
  const sha256 = isRecord(digest) ? digest.sha256 : undefined;
  if (typeof sha256 !== "string") {
    throw refusal("verify.statement_no_digest");
  }
  return `sha256:${sha256}`;
}

export interface CatalogVerifyResult {
  readonly verified: boolean;
  readonly digest: string;
  readonly signer?: string;
  readonly keyid?: string | null;
  readonly keyless?: boolean;
  readonly reason?: string;
  readonly reason_keys?: readonly string[];
}

/** `input.catalog_unreadable`, naming the file or folder that cannot be read (Python's
 * `_catalog_unreadable`, 18.68). */
export function catalogUnreadable(dir: string, err: unknown): InputError {
  return unreadableError(
    "input.catalog_unreadable",
    "the catalog directory",
    dir,
    unreadableRel(dir, err),
    "catalog directory",
  );
}

/** Verifies a catalog directory's detached signature against `trust` (mirrors `_verify_catalog`/
 * `verify_catalog_directory`, `signing.py:426-461`, `commands/__init__.py:321-352`). */
export function verifyCatalog(dir: string, trust: TrustRoot): CatalogVerifyResult {
  let digest: string;
  try {
    digest = digestTree(dir, new Set([CATALOG_SIGNATURE_NAME]));
  } catch (err) {
    throw catalogUnreadable(dir, err);
  }
  const fail = (exc: unknown): CatalogVerifyResult => ({
    verified: false,
    digest,
    ...refused(exc),
  });
  const sigPath = join(dir, CATALOG_SIGNATURE_NAME);
  let sigIsFile = false;
  try {
    sigIsFile = statSync(sigPath).isFile();
  } catch {
    sigIsFile = false;
  }
  if (!sigIsFile) {
    return fail(refusal("verify.catalog_unsigned"));
  }
  const sigDenied = readPermissionError(sigPath);
  if (sigDenied) {
    throw catalogUnreadable(dir, sigDenied);
  }
  let signedDigest: string;
  let verified: VerifiedEnvelope;
  try {
    const envelope = loadReleaseJson(
      sigPath,
      () => true,
      "verify.catalog_signature_unreadable",
      CATALOG_SIGNATURE_NAME,
    );
    verified = verifyEnvelope(envelope, trust);
    signedDigest = statementSubjectDigest(verified.payload);
  } catch (exc) {
    return fail(exc);
  }
  if (signedDigest !== digest) {
    return fail(refusal("verify.catalog_digest_mismatch"));
  }
  return {
    verified: true,
    digest,
    signer: verified.identity,
    keyid: verified.keyid,
    keyless: verified.keyless,
  };
}

/** The shared soft-fail shape for a release that cannot be verified at all (no `manifest_digest`/
 * `signers` field): mirrors `_verify_release_soft_fail`'s own three-field shape exactly -- every
 * early-return soft-fail in this function, including the directory-bundle branch's own JSON-parse
 * failures, uses this shape, never the richer bundle shape below (Python's `_verify_release_soft_fail`
 * is the one function every such return calls, and it only ever sets these three fields). */
export interface ReleaseSoftFail {
  readonly release: string;
  readonly verified: false;
  readonly reason: string;
  readonly reason_keys: readonly string[];
}

/** A verified:false result's `reason` and `reason_keys`: the refusals' texts joined with "; " and their
 * keys in the same order (mirrors `commands._refused`). */
function refused(...refusals: unknown[]): { reason: string; reason_keys: string[] } {
  return {
    reason: refusals.map((r) => (r instanceof Error ? r.message : String(r))).join("; "),
    reason_keys: refusals.flatMap((r) => (r instanceof VerifyRefusal ? [...r.keys] : [])),
  };
}

/** Reads one JSON file of a release with {@link parseVerifyInput}, refusing a file that cannot be read
 * or is not of the expected shape with `unreadableKey` (mirrors `_load_release_json`). */
function loadReleaseJson(
  path: string,
  isShape: (value: unknown) => boolean,
  unreadableKey: string,
  what: string,
  keepNumberTokens = false,
): unknown {
  let raw: Buffer;
  try {
    raw = readFileSync(path);
  } catch {
    throw refusal(unreadableKey);
  }
  const value = parseVerifyInput(raw, unreadableKey, what, keepNumberTokens);
  if (!isShape(value)) {
    throw refusal(unreadableKey);
  }
  return value;
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
  readonly manifest_digest: string;
  readonly signers: ReadonlyArray<{ readonly profile: string | null; readonly identity: string }>;
  readonly reason?: string;
  readonly reason_keys?: readonly string[];
}

export type ReleaseResult = ReleaseSoftFail | ReleaseSingleResult | ReleaseBundleResult;

/** Throws `input.release_unreadable` naming `path` when a permission error stops it being read
 * (Python's PermissionError branch of `cmd_verify`'s release wrapper, 18.68). */
function requireReleaseReadable(releasePath: string, path: string): void {
  const denied = readPermissionError(path);
  if (denied) {
    throw unreadableError(
      "input.release_unreadable",
      "the release artifact",
      releasePath,
      unreadableRel(releasePath, denied),
      "release",
    );
  }
}

/** Verifies a release bundle (or a single DSSE envelope) offline against `trust` -- the item's core
 * fix target, built correct from the start: every branch uses the soft-fail shape, and every new
 * JSON-parse-failure path this function adds uses a fixed, engine-neutral reason text (mirrors
 * `_verify_release`, `commands/__init__.py:758-875`). */
export function verifyRelease(releasePath: string, trust: TrustRoot): ReleaseResult {
  const softFail = (exc: unknown): ReleaseSoftFail => ({
    release: releasePath,
    verified: false,
    ...refused(exc),
  });
  const stat = statSync(releasePath);
  if (stat.isFile()) {
    requireReleaseReadable(releasePath, releasePath);
    try {
      const envelope = loadReleaseJson(
        releasePath,
        () => true,
        "verify.release_envelope_unreadable",
        "release envelope",
      );
      const verified = verifyEnvelope(envelope, trust);
      return {
        release: releasePath,
        verified: true,
        signer: verified.identity,
        keyid: verified.keyid,
        keyless: verified.keyless,
      };
    } catch (exc) {
      return softFail(exc);
    }
  }

  const manifestPath = join(releasePath, "release-manifest.json");
  const signaturesPath = join(releasePath, "signatures.json");
  const isFile = (path: string): boolean => {
    try {
      return statSync(path).isFile();
    } catch {
      return false;
    }
  };
  if (!isFile(manifestPath) || !isFile(signaturesPath)) {
    requireReleaseReadable(releasePath, manifestPath);
    requireReleaseReadable(releasePath, signaturesPath);
    throw new InputError(
      "input.release_bundle",
      `${releasePath} is not a release bundle (release-manifest.json/signatures.json).`,
      "pass the --out directory produced by the release tooling.",
    );
  }

  requireReleaseReadable(releasePath, manifestPath);
  let manifest: Record<string, unknown>;
  try {
    // Number tokens kept: `manifestDigest` below canonicalizes `manifest`, which must refuse a
    // non-canonical number token (`1.0`, `1e2`, `-0.0`) the way Python's and Java's decoders do,
    // rather than have `JSON.parse` fold it to an ordinary number first.
    manifest = loadReleaseJson(
      manifestPath,
      isRecord,
      "verify.release_manifest_unreadable",
      "release manifest",
      true,
    ) as Record<string, unknown>;
  } catch (exc) {
    return softFail(exc);
  }

  let manifestDigest: string;
  try {
    manifestDigest = `sha256:${sha256Hex(manifest)}`;
  } catch (exc) {
    // `RangeError` alongside `CanonicalizationError`: a manifest nested deep enough overflows the
    // recursive serialiser's call stack before it ever reaches a number or shape it would refuse,
    // and that must soft-fail the same way, not crash (mirrors Python's `RecursionError`).
    if (!(exc instanceof CanonicalizationError) && !(exc instanceof RangeError)) {
      throw exc;
    }
    return softFail(refusal("verify.release_manifest_uncanonical"));
  }
  const problems: VerifyRefusal[] = [];
  const artifacts = Array.isArray(manifest.artifacts) ? manifest.artifacts : [];
  for (const raw of artifacts) {
    const artifact = isRecord(raw) ? raw : {};
    const name = artifact.name;
    if (typeof name !== "string") {
      problems.push(refusal("verify.release_artifact_unnamed"));
      continue;
    }
    const artifactFile = confineToRoot(releasePath, name);
    if (artifactFile !== null) {
      requireReleaseReadable(releasePath, artifactFile);
    }
    let content: Buffer | null = null;
    try {
      content = artifactFile === null ? null : readFileSync(artifactFile);
    } catch {
      // Unreadable (a directory, no permission, gone) counts as missing, as in Python.
      content = null;
    }
    if (content === null) {
      problems.push(refusal("verify.release_artifact_missing", { name }));
      continue;
    }
    const actual = `sha256:${createHash("sha256").update(content).digest("hex")}`;
    if (actual !== artifact.digest) {
      problems.push(refusal("verify.release_artifact_digest", { name }));
    }
  }

  requireReleaseReadable(releasePath, signaturesPath);
  let signatureEntries: unknown[];
  try {
    signatureEntries = loadReleaseJson(
      signaturesPath,
      Array.isArray,
      "verify.release_signatures_unreadable",
      "release signatures",
    ) as unknown[];
  } catch (exc) {
    // The bare soft-fail shape (`_verify_release_soft_fail`'s own shape) -- Python computes
    // `manifest_digest` as a local before this point too, but never adds it to `result.data` on this
    // path, so this port must not either (a field TS alone would carry is a real parity bug, not a
    // cosmetic one: a consumer branching on the field's presence would see a different shape per engine).
    return softFail(exc);
  }

  const signers: Array<{ profile: string | null; identity: string }> = [];
  for (const raw of signatureEntries) {
    if (!isRecord(raw)) {
      problems.push(
        refusal(
          "verify.release_signature",
          { profile: "None" },
          refusal("verify.release_signature_not_object"),
        ),
      );
      continue;
    }
    const profile = typeof raw.profile === "string" ? raw.profile : null;
    try {
      if (!("envelope" in raw)) {
        throw refusal("verify.release_envelope_missing");
      }
      const verified = verifyEnvelope(raw.envelope, trust);
      if (statementSubjectDigest(verified.payload) !== manifestDigest) {
        throw refusal("verify.release_manifest_not_covered");
      }
      signers.push({ profile, identity: verified.identity });
    } catch (exc) {
      problems.push(refusal("verify.release_signature", { profile: profile ?? "None" }, exc));
    }
  }
  if (problems.length === 0 && signers.length === 0) {
    problems.push(refusal("verify.release_no_signatures"));
  }

  if (problems.length === 0) {
    return { release: releasePath, verified: true, manifest_digest: manifestDigest, signers };
  }
  return {
    release: releasePath,
    verified: false,
    manifest_digest: manifestDigest,
    signers,
    ...refused(...problems),
  };
}
