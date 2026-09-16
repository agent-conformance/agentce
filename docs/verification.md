# Verifying AgentCE artifacts

This document is the verification procedure for the Agent Conformance Engine (SPEC §8.7, §9.1): the
cryptographic primitives the engine uses and where, the trust roots it verifies against, how to
verify a signed catalog, corpus, or release **offline**, and how the three signing profiles are
checked. It is the reference for `agentce verify` and `agentce sign`.

Everything here runs without a network. Verification never contacts a transparency log at assess or
verify time (hard requirement HR-1); a stale vendored trust root is a recorded warning, never a
network call.

## Cryptographic primitives, and where each is used

The engine uses only the platform's standard cryptographic primitives, behind the narrow surface of
`engines/python/agentce/signing.py`, so a deployment that requires FIPS 140-3 validated modules
substitutes a validated provider without touching the callers (SPEC §8.7).

| Primitive | Where it is used |
|---|---|
| **SHA-256** (`sha256:<hex>`) | Content digests everywhere: bundle and manifest digests (§8.4), the `integrity.hash` chain (§6.6), catalog/corpus/release digests, and every digest in a signed statement. |
| **HMAC-SHA256** | Pseudonymised principal IRIs, `agentce:principal/<hmac-sha256(k, id)>` (§6.7); the key is supplied by reference and never stored in a bundle. |
| **Ed25519** | The signature algorithm for every DSSE envelope: catalog and corpus signatures, release signatures, and the claim/assessor signatures produced by `agentce sign`. Ed25519 is deterministic (RFC 8032), so re-signing the same bytes with the same key is byte-identical. |
| **RFC 8785 (JCS)** | The canonical form of any JSON that is hashed or signed — the DSSE payload and the keyless certificate body — so a signature is over a single exact byte sequence regardless of key order or whitespace (§6.7). |
| **DSSE + in-toto Statement** | The signature envelope. The payload is an in-toto Statement v1 whose subject carries the `sha256:` digest of the signed artifact; the signature covers the DSSE Pre-Authentication Encoding of `(payloadType, payload)`. |

The specification also permits ECDSA P-256 through DSSE (SPEC §8.7); the reference engine signs and
verifies Ed25519. Algorithm identifiers are explicit in every digest (`sha256:`) and key
(`"algorithm": "ed25519"`).

## Trust roots

A verifier trusts a **trust root**: a set of pinned public keys (for the `kms` profile) and keyless
certificate authorities (for the `sigstore-*` profiles). The engine vendors one at
`engines/python/agentce/data/trust/dev-root.json` and loads it for `agentce verify`.

That vendored root is the repository's **development trust root**. It verifies the artifacts this
repository ships — the base catalogs, the corpus, and the dry-run release — which are signed with
keys derived from a **published, non-production seed** (see `conformance/dev_trust.py`). Deriving a
key from that seed is deterministic and open to anyone, which is the point: those keys sign only
repository fixtures, exactly as the specification ships a *published test key* for pseudonymisation
(§6.7). The development root is **not** the public Sigstore TUF root.

A production deployment configures its own trust root: the public Sigstore TUF root where internet
access exists, or the deployment's own Sigstore/KMS root for the `sigstore-private` and `kms`
profiles. Verification of `kms` and `sigstore-private` needs no network once the trust root is
supplied.

## The three signing profiles (SPEC §9.1)

`agentce sign` records each signature's `profile`:

- **`sigstore-public`** — keyless signing against the public Fulcio and Rekor. An ephemeral key
  signs, a short-lived certificate binds the signer's OIDC identity to that key, and inclusion is
  recorded in the public transparency log. This is the default where internet access exists.
- **`sigstore-private`** — keyless against the deployment's own Sigstore instance; otherwise as
  above. Verifiable offline once the deployment's certificate authority is in the trust root.
- **`kms`** — a DSSE signature by a KMS- or HSM-held key (Ed25519), for air-gapped hosts. The trust
  root pins the key by content-addressed `keyid`. Fully offline to produce and to verify.

A keyless signature carries its certificate in the DSSE signature entry (`cert`); a verifier checks
the certificate against the pinned authority, then checks the signature with the certified key. A
`kms` signature carries a `keyid` that must resolve to a pinned key.

## Verifying a catalog or corpus

Catalogs, overlays, probe corpora, and rule fixtures are distributed with a detached signature,
`catalog.sig.json`, over the directory's content digest. The engine verifies it before use and
refuses an unsigned or unverifiable one unless `--allow-unverified-catalog` is passed (SPEC §8.7).

```bash
agentce verify --catalog spec/catalogs/base/eu-ai-act --json
```

The command recomputes the directory's content digest (a SHA-256 over the sorted `<relpath>\0<file
digest>` lines, excluding the signature file), verifies `catalog.sig.json` against the vendored trust
root, and confirms the signed digest matches the recomputed one. It prints the signer identity and
the digest:

```json
{
  "verified": true,
  "catalog": "spec/catalogs/base/eu-ai-act",
  "digest": "sha256:…",
  "signer": "agentce-release (development catalog-signing key)",
  "keyless": false
}
```

Exit code `0` means verified; `3` means unsigned, tampered, or signed by an untrusted key (the
`verified` field is then `false` with a `reason`).

## Verifying a release

The engine's own release is signed and reproducible, and its SBOM is published (SPEC §8.7). The
release tooling (`conformance/release.py`) assembles the release as a bundle:

```
release-manifest.json   the release contents and their digests (the signed subject)
signatures.json         one DSSE envelope per signing profile
artifacts/…             deterministic per-engine source tarballs
sbom.cdx.json           CycloneDX 1.5 SBOM
openvex.json            OpenVEX 0.2.0 document
```

Verify the whole bundle offline:

```bash
agentce verify --release <bundle-dir> --json
```

This recomputes the canonical digest of `release-manifest.json`, checks that every artifact's digest
matches the file on disk, and verifies every signature in `signatures.json` against the trust root,
confirming each covers the manifest digest. `agentce verify --release <envelope.json>` verifies a
single DSSE envelope instead.

## Reproducing a release

A release is reproducible: two builds from the same source produce byte-identical artifacts. Each
per-engine source tarball is written with fixed member metadata (mtime 0, uid/gid 0, mode 0644) in
sorted path order, so its bytes depend only on file content and layout. The release dry run builds
the sources twice and compares the combined digest:

```bash
cd conformance && uv run python release.py --dry-run --json
```

The reported `release_digest` is the content address of the source release; re-running yields the
same value. Nothing is published — a stub registry records that no publish call was ever made
(`no_publish: true`), and the tool refuses any mode but `--dry-run`.

To exercise every signing profile offline:

```bash
cd conformance && uv run python release.py --dry-run --profiles sigstore-public,sigstore-private,kms --offline
```

## SBOM and VEX

The SBOM is CycloneDX 1.5 (`sbom.cdx.json`): the three engines as `application` components and their
locked dependencies (from `uv.lock`, `pnpm-lock.yaml`, and `gradle.lockfile`) as `library`
components. It is validated against the vendored CycloneDX 1.5 profile schema and cross-checked
against the engine's own locked dependency set — the same set the no-learned-components gate reads
(SPEC §8.7). The `cryptography` provider that supplies Ed25519 appears in the SBOM.

The VEX is OpenVEX 0.2.0 (`openvex.json`). At release time it asserts no statements; statements are
added as advisories against the release are triaged.

## Signing a report

`agentce sign` signs a finished report on the invoking identity's behalf; the engine holds no signing
identity of its own (SPEC §9.1). It refuses to sign a report whose `agentce readiness` verdict is
`NOT READY`.

```bash
agentce sign <report-dir> --as claimant --profile kms --key <ed25519-private-key.pem>
```

The signature is a DSSE envelope over the claim body and the manifest digests; it is appended to the
claim's `signatures[]` and written to `signatures/<role>-<profile>.dsse.json`. The keyless
`sigstore-*` profiles obtain their certificate from a Fulcio instance and therefore need network;
`kms` signs offline with an operator-held key.

## Threat model

The integrity and provenance threats these mechanisms address — catalog or corpus substitution,
modified engines, evidence spoofing and replay, and instruction-provenance laundering — and their
mitigations are catalogued in [the threat model](./threat-model.md).
