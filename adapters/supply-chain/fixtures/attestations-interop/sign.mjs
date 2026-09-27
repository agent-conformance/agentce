// Produces input.jsonl and adapt.json for this fixture with Node's own crypto, independently of the
// adapter and of the repository's Python code: the DSSE pre-authentication encoding is built here and
// signed with Ed25519 (deterministic, RFC 8032). Run: `node sign.mjs` from this directory.
// The seed is a published test key: it signs fixtures only and protects nothing.
import { createPrivateKey, createPublicKey, sign } from "node:crypto";
import { writeFileSync } from "node:fs";

const seed = Buffer.alloc(32, 0xc3);
const pkcs8Prefix = Buffer.from("302e020100300506032b657004220420", "hex");
const privateKey = createPrivateKey({ key: Buffer.concat([pkcs8Prefix, seed]), format: "der", type: "pkcs8" });
const spki = createPublicKey(privateKey).export({ format: "der", type: "spki" });
const publicRaw = spki.subarray(spki.length - 32);

const payloadType = "application/vnd.in-toto+json";
const statement = {
  _type: "https://in-toto.io/Statement/v1",
  subject: [{ name: "bundle-interop", digest: { sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08" } }],
  predicateType: "https://slsa.dev/provenance/v1",
  predicate: {},
};
const payload = Buffer.from(JSON.stringify(statement), "utf8");
const pae = Buffer.concat([
  Buffer.from(`DSSEv1 ${Buffer.byteLength(payloadType)} ${payloadType} ${payload.length} `, "utf8"),
  payload,
]);
const signature = sign(null, pae, privateKey);

const record = {
  timestamp: "2026-05-08T09:30:00.000Z",
  id: "att-interop-1",
  kind: "attestation",
  format: "in-toto",
  convention_version: "1.0",
  system: "admission-controller",
  observed_digests: ["sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"],
  dsse: {
    payloadType,
    payload: payload.toString("base64"),
    signatures: [{ keyid: "key-interop", sig: signature.toString("base64") }],
  },
};
const adapt = {
  subject: "spiffe://corp/agents/credit-underwriter",
  source_class: "enforcement_point",
  trusted_keys: { "key-interop": publicRaw.toString("base64") },
};
writeFileSync("input.jsonl", JSON.stringify(record) + "\n");
writeFileSync("adapt.json", JSON.stringify(adapt, null, 2) + "\n");
