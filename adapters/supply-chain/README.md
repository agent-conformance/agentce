# supply-chain adapter

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3`
fixtures and support matrix; trust class per `§6.4`) for supply-chain evidence.

Supply-chain evidence comes from admission controllers and registries — Sigstore/in-toto attestations,
CycloneDX AIBOMs, registry pins, and runtime bundle-load logs — so its records are trust class
**`enforcement_point`** (admission) or **`independent_system`** (registry). The adapter maps a
normalised supply-chain log (JSON Lines, one record per line, each naming its `kind` and `format`):

| Record `kind` | Source `format` | AgentCE event |
|---|---|---|
| `bundle_loaded` | `bundle-load`, `cyclonedx` | `BundleLoaded` (components: skill / mcp_server / model / prompt / config / policy) |
| `attestation` | `sigstore`, `in-toto` | `Attestation` |

## Verification at adapt time (SPEC §12.2)

The adapter verifies each attestation and records the outcome in `Attestation.verification.status`.
Verification is **deterministic and offline**. An attestation record carries a DSSE envelope (`dsse`)
over an in-toto Statement, and the caller passes `trusted_keys`, a mapping from each trusted signer's
key id to its public key (PEM or base64 DER SPKI; a raw base64 32-byte Ed25519 key is also accepted):

- **`verified`** — a signature in the envelope verifies, over the DSSE pre-authentication encoding,
  against a trusted public key (Ed25519, or ECDSA on P-256 with SHA-256 and a DER signature) **and**
  the Statement's subject digests equal the digests observed at load (`observed_digests`);
- **`failed`** — a signature is invalid, the signer's key id is not trusted, the envelope is malformed
  (wrong payload type, undecodable payload), or the signed subject differs from what was observed;
- **`unverified`** — the record carries no signature, the signer's key id is trusted but supplied
  without key material, or there are no observed digests to bind the Statement to. A key id alone is
  never enough.

The statement type and subject digests are read from the signed payload, never from unsigned record
fields, and the reported `signer` is the id of the key that verified. A trusted key of any other type
(or an undecodable one) raises `AdapterError` with the stable reason `bad_trusted_key`. The
`agentce ingest` path passes no trusted keys, so an ingested attestation is never `verified` on its own.

The fixtures under `fixtures/` carry real DSSE envelopes signed with published Ed25519 test keys
(`fixtures/generate.py` rebuilds them byte-for-byte; `attestations-hostile/` pins each forged,
tampered, and relabelled case to its expected status; `attestations-interop/` was signed by Node's
crypto rather than by this repository's code).

## Contract (SPEC §12.1)

Deterministic ids (`supply-chain:<record id>`); preserved timestamps; the declared trust class on
every event; a recorded convention (`<format>:<version>`); malformed or partial records recorded in
the `AdapterReport` (`invalid_json`, `not_an_object`, `missing_envelope`, `unknown_kind`), never
emitted. Standard library, PyYAML and `cryptography` — no network, no learned component.

## Commands

```bash
uv run pytest -q
uv run python -m agentce_adapters.check_support_matrix .
uv run python fixtures/generate.py --check
```

`pytest` proves each fixture maps to its expected events byte-for-byte (including the verified,
tampered, unsigned, and untrusted-signer attestations), that every event is schema-valid, and that the
contract properties above hold. `check_support_matrix` proves `support-matrix.yaml` stays consistent
with what the adapter emits over the fixtures.
