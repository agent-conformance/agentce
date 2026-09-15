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
Verification is **deterministic and offline**:

- **`verified`** — the signer's `key_id` is in the vendored `trusted_keys` set and the attested
  `subject_digests` match the `observed_digests` recorded at load;
- **`failed`** — the observed digests differ from the attested subject (tampering) or the signer's key
  is not trusted;
- **`unverified`** — the attestation carries no signature.

Cryptographic verification of the signature bytes against a Fulcio/Rekor trust root is a release-time
concern (SPEC §8.7, Phase 5), exactly as the engine's integrity verifier defers it; the reference
adapter verifies the digest binding and signer trust.

## Contract (SPEC §12.1)

Deterministic ids (`supply-chain:<record id>`); preserved timestamps; the declared trust class on
every event; a recorded convention (`<format>:<version>`); malformed or partial records recorded in
the `AdapterReport` (`invalid_json`, `not_an_object`, `missing_envelope`, `unknown_kind`), never
emitted. Standard library plus PyYAML — no network, no learned component.

## Commands

```bash
uv run pytest -q
uv run python -m agentce_adapters.check_support_matrix .
```

`pytest` proves each fixture maps to its expected events byte-for-byte (including the verified,
tampered, unsigned, and untrusted-signer attestations), that every event is schema-valid, and that the
contract properties above hold. `check_support_matrix` proves `support-matrix.yaml` stays consistent
with what the adapter emits over the fixtures.
