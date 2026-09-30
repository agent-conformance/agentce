/**
 * `agentce sign`, the `kms` profile (SPEC §8.7, §9.1): the Ed25519/DSSE/in-toto signing primitives,
 * ported byte-for-byte from Python's `signing.py` (`_pae`, `keyid_for`, `sign_statement`,
 * `_load_ed25519_private_key`) and `commands/__init__.py`'s `_sign_subjects`. Mirrors `readiness.ts`'s
 * shape as the compute seam `cli.ts`'s `cmdSign` wires up. The `sigstore-*` keyless profiles are
 * accepted as flag values but always refuse offline (handled entirely in `cli.ts`, per Python's own
 * `_sign_signer`), so this module builds no certificate path at all.
 */

import {
  type KeyObject,
  createHash,
  createPrivateKey,
  createPublicKey,
  sign as cryptoSign,
} from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { canonicalize } from "./canonical";
import { InputError } from "./errors";

/** DSSE payload type for an in-toto Statement (matches `signing.py`'s `INTOTO_PAYLOAD_TYPE`). */
export const INTOTO_PAYLOAD_TYPE = "application/vnd.in-toto+json";
/** in-toto Statement schema version (matches `signing.py`'s `INTOTO_STATEMENT_TYPE`). */
export const INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1";

/** DSSE Pre-Authentication Encoding: the exact bytes a signature covers, matching Python's `_pae`
 * (`b"DSSEv1 %d %s %d %s" % (len(pt), pt, len(payload), payload)`) -- built with `Buffer.concat` over
 * discrete byte segments, never a template-literal-then-`Buffer.from` round trip that could
 * double-encode a `payload` containing raw bytes outside valid UTF-8. */
export function dssePae(payloadType: string, payload: Buffer): Buffer {
  const typeBytes = Buffer.from(payloadType, "utf-8");
  return Buffer.concat([
    Buffer.from("DSSEv1 ", "ascii"),
    Buffer.from(String(typeBytes.length), "ascii"),
    Buffer.from(" ", "ascii"),
    typeBytes,
    Buffer.from(" ", "ascii"),
    Buffer.from(String(payload.length), "ascii"),
    Buffer.from(" ", "ascii"),
    payload,
  ]);
}

/** A content-addressed key id: `sha256:` over the raw public key (matches `signing.py:118-125`'s
 * `keyid_for`, over the raw 32-byte public key, never the DER-wrapped form). */
export function keyidFor(publicKeyRaw: Buffer): string {
  return `sha256:${createHash("sha256").update(publicKeyRaw).digest("hex")}`;
}

/** An operator-held signing identity. The `kms` profile's only implementation, {@link KmsSigner},
 * is the sole profile this port signs offline. */
export interface Signer {
  sign(data: Buffer): Buffer;
  readonly keyid: string;
}

/** The fixed 12-byte RFC 8410 SPKI DER prefix (`302a300506032b6570032100`) every Ed25519 SPKI public
 * key export carries before its 32 raw key bytes -- confirmed byte-for-byte against independently
 * generated keys; slicing it off gives the exact bytes Python's `Encoding.Raw, PublicFormat.Raw`
 * gives directly, with no JWK round trip. */
const ED25519_SPKI_PREFIX_LEN = 12;

/** A key pinned in the trust root by content-addressed `keyid` (the `kms` profile, SPEC §9.1). */
export class KmsSigner implements Signer {
  readonly keyid: string;
  readonly publicKeyB64: string;
  private readonly privateKeyObject: KeyObject;

  private constructor(privateKeyObject: KeyObject, publicKeyRaw: Buffer) {
    this.privateKeyObject = privateKeyObject;
    this.keyid = keyidFor(publicKeyRaw);
    this.publicKeyB64 = publicKeyRaw.toString("base64");
  }

  /** The one-shot Ed25519 signing API: `algorithm` is always `null` for EdDSA, which signs the
   * message directly with no separate digest step (matches Python's `private_key.sign(data)`). */
  sign(data: Buffer): Buffer {
    return cryptoSign(null, data, this.privateKeyObject);
  }

  /** Loads and validates an Ed25519 private key PEM (matches `_load_ed25519_private_key`'s two-error-
   * path split: an unparseable file, or a parseable key of the wrong algorithm, are distinguished
   * cases, not one). `pemPath` must already be a confirmed-existing file (the caller's `requireFile`
   * check runs first). */
  static load(pemPath: string): KmsSigner {
    let privateKeyObject: KeyObject;
    try {
      const pemText = readFileSync(pemPath, "utf-8");
      privateKeyObject = createPrivateKey(pemText);
    } catch {
      throw new InputError(
        "sign.key_unreadable",
        "the signing key file could not be parsed as an unencrypted PEM private key.",
        "supply an unencrypted Ed25519 private key PEM (`openssl genpkey -algorithm ed25519 " +
          "-out key.pem`, or `agentce catalog sign --new-key <path>`).",
      );
    }
    if (privateKeyObject.asymmetricKeyType !== "ed25519") {
      throw new InputError(
        "sign.key_algorithm",
        "the signing key is not an Ed25519 private key.",
        "supply an Ed25519 key (the algorithm the engine signs with, SPEC §8.7).",
      );
    }
    const publicKeyDer = createPublicKey(privateKeyObject).export({ type: "spki", format: "der" });
    const publicKeyRaw = Buffer.from(publicKeyDer.subarray(ED25519_SPKI_PREFIX_LEN));
    return new KmsSigner(privateKeyObject, publicKeyRaw);
  }
}

export interface DsseEnvelope {
  readonly payloadType: string;
  readonly payload: string;
  readonly signatures: readonly { readonly keyid: string; readonly sig: string }[];
}

/** Produces a DSSE envelope over `statement` (canonical bytes) signed by `signer` -- matches
 * `sign_statement` exactly. A `KmsSigner`'s envelope never carries a `cert` field (only a keyless
 * signer's would, and this port never builds one). */
export function signStatement(statement: unknown, signer: Signer): DsseEnvelope {
  const payload = canonicalize(statement);
  const signature = signer.sign(dssePae(INTOTO_PAYLOAD_TYPE, payload));
  return {
    payloadType: INTOTO_PAYLOAD_TYPE,
    payload: payload.toString("base64"),
    signatures: [{ keyid: signer.keyid, sig: signature.toString("base64") }],
  };
}

export interface SignSubject {
  readonly name: string;
  readonly digest: { readonly sha256: string };
}

/** The in-toto subjects a claim signature covers: the claim body and the manifest, by digest --
 * matches `_sign_subjects` (`commands/__init__.py:2577-2596`) exactly. The claim body is every
 * top-level key except `signatures`, built by omission (never a JSON round trip that could reorder or
 * coerce values); the manifest, when present, is hashed over its raw file bytes (never
 * re-encoded/normalized). */
export function signSubjects(reportDir: string, claim: Record<string, unknown>): SignSubject[] {
  const body: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(claim)) {
    if (key !== "signatures") {
      body[key] = value;
    }
  }
  const subjects: SignSubject[] = [
    {
      name: "claim.json",
      digest: { sha256: createHash("sha256").update(canonicalize(body)).digest("hex") },
    },
  ];
  const manifestPath = join(reportDir, "manifest.json");
  if (existsSync(manifestPath) && statSync(manifestPath).isFile()) {
    subjects.push({
      name: "manifest.json",
      digest: { sha256: createHash("sha256").update(readFileSync(manifestPath)).digest("hex") },
    });
  }
  return subjects;
}
