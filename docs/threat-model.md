# Threat model (v1)

Status: accepted
Spec refs: SPEC §8.7 (security of the engine itself), §6.6 (integrity envelope), §10.4 (corroboration),
§6.4 (trust classes), §6.5 (coverage denominators), §7.7.5 (ingress instruction attestation)

This threat model covers the evidence pipeline and the engine that assesses it. It enumerates every
threat named in SPEC §8.7, groups them into three attack classes, and gives each a mitigation and the
test or control that proves the mitigation holds. It is updated in the same change as any modification
to inputs, parsing, or integrity handling.

## Assets

- **The evidence bundle** — events, attestations, reference snapshots, and the manifest — from which
  every outcome is derived.
- **The conformance claim and report** — the signed statement of what was assessed.
- **The engine, catalogs, overlays, and corpus** — the code and data that decide outcomes.
- **The pseudonymisation key** (SPEC §6.7) — supplied by reference, never in the bundle.

## Trust boundaries

1. Between the **agent (subject)** and the **enforcement point** that could have prevented an action:
   agent self-reports are lower-class evidence (SPEC §6.4, IR-03).
2. Between the **enforcement point** and an **independent system** in which no principal in the agent's
   delegation chain can create or transition records (SPEC §6.4).
3. Between the **evidence source** and an **independent coverage denominator** that is independent of
   both the agent and the enforcement point (SPEC §6.5, denominator class A1).
4. Between the **produced bundle** and the **engine/verifier** that reads it read-only (SPEC §8.7).

## Attacker model

The adversary may control the agent and its held credentials, may influence what a cooperating source
records, and may attempt to alter records after the fact or substitute the toolchain. The adversary does
**not** control independent systems, the independent coverage denominators, the signing trust roots, or
an independent verifier's rerun. The engine is read-only over its inputs and runs with no network in the
evaluation path (SPEC §8.7), so the attack surface is the evidence and the trust base, not a live
service.

## Attack classes

- **A1 — Fabrication and omission at the source.** The agent, or a source colluding with it, produces
  false records, omits actions, or crafts records that satisfy a shape without meaning what the shape
  assumes. This class holds evidence spoofing, self-approval through agent-held credentials,
  enforcement-point bypass (direct provider calls), shape-satisfying-but-meaningless records, and
  instruction-provenance laundering. Countered by trust classes, minimum-evidence and source-class
  requirements, cross-source corroboration (SPEC §10.4), and coverage denominators independent of both
  the agent and the enforcement point.
- **A2 — Tampering with the record after the fact.** Replay, reordering, timestamp manipulation, chain
  recomputation after export, or re-identification of pseudonymised subjects. Countered by the integrity
  envelope (SPEC §6.6): hash chains, out-of-band anchoring, timestamp cross-checks, and keyed
  pseudonyms.
- **A3 — Subversion of the toolchain and trust base.** A modified engine or catalog, catalog or corpus
  substitution, or collusion between the source and the assessor. Countered by signed, offline-verified
  catalogs and releases, the engine and input digests in the manifest, and independent verifier reruns.

## Threats, mitigations, and the check that proves each

| # | Threat (SPEC §8.7) | Class | Mitigation | Test / control reference |
|---|---|---|---|---|
| 1 | Evidence spoofing | A1 | Trust classes: a `self_report` stream never satisfies a control that declares `min_source_class: enforcement_point`; model-stated rationale is never accepted as evidence of "why" | SPEC §6.4, IR-03; a control's `min_source_class` and `minimum_evidence` (SPEC §7.1); ECS spoofing fixtures |
| 2 | Replay / duplicate events | A2 | Hash chain (`integrity.prev`) and per-stream ordering; a repeated id is quarantined `duplicate_id` | SPEC §6.6; quarantine reason `duplicate_id` (App F); `quarantine.schema.json` |
| 3 | Timestamp manipulation | A2 | Source timestamps cross-checked against the anchors that cover them; an out-of-range event is `time_suspect` and excluded from timing controls; `time_order` quarantine beyond skew | SPEC §6.6; `IntegrityResult.status = time_suspect`; INC/REC timing controls |
| 4 | Self-approval through agent-held credentials | A1 | `independent_system` requires that no principal in the agent's delegation chain can create or transition the record; the human-actor rule requires a distinct human approver | SPEC §6.4, §10.4; OVS-03 expectation E2, CND-02 |
| 5 | Enforcement-point bypass (direct provider calls) | A1 | Coverage denominators independent of both the agent and the enforcement point (egress proxy, firewall, admission attestation); unreconciled coverage is `unknown` and yields `insufficient_evidence` on INT/REC controls | SPEC §6.5; `coverage.json`; INT and REC families |
| 6 | Export-host chain recomputation | A2 | `export_anchored` anchors each stream head out-of-band to a write-once location; an `export_chained` enforcement-point stream is capped at `verified_weak` and its rung-2 outcomes at `partial` | SPEC §6.6; `IntegrityResult.strength`; integrity strength rules |
| 7 | Modified engines | A3 | The manifest records the engine package digest; a report from an engine whose digest is not a published release is marked `engine_unverified`; verifiers rerun with the signed release and check its signatures with `agentce verify --release` | SPEC §8.7, §8.4; `manifest.json`, claim `engine_unverified`; [verification](verification.md) |
| 8 | Shape-satisfying-but-meaningless records | A1 | Minimum evidence and source class plus corroboration: a well-formed self-report alone cannot satisfy a high-severity control; the shape reads engine-materialised edges from enforcement-point facts | SPEC §7.1 `minimum_evidence`, IR-02/IR-03; §10.4 |
| 9 | Collusion between source and assessor | A3 | Independent verifier reruns from the reproducibility manifest; `assessor_independent` recorded in the claim; coverage denominators independent of the source | SPEC §8.4; claim `assessor_independent`; ECS reproducibility |
| 10 | Catalog or corpus substitution | A3 | Catalogs, overlays, probe corpora, and fixtures carry a detached signature verified offline before use: `agentce assess` verifies every `--catalog-dir` catalog against the effective trust root (`--trust-root`, else `AGENTCE_TRUST_ROOT`, else the vendored development root) before it evaluates a control, and refuses an unsigned, untrusted, or altered one with `input.catalog_unverified` (exit 3); unverified use requires the explicit `--allow-unverified-catalog`, which the manifest and the claim record as a limitation; a catalog whose own `id@version` is not the one requested is refused with `input.catalog_mismatch` and has no override; the manifest records each catalog's content digest | SPEC §8.7; `agentce verify --catalog`; [verification](verification.md); `manifest.json` input digests and `limitations`; `engines/python/tests/test_catalog_signature.py` |
| 11 | Instruction-provenance laundering | A1 | Ingress instruction attestation records the channel `source_class` at entry; an agent-side `source_class` that disagrees is a finding `CND.instruction_relabelled`; high-severity instruction controls require corroboration | SPEC §7.7.5; CND-04, CND-05 |
| 12 | Pseudonym re-identification by dictionary | A2 | `person_ref` values are keyed pseudonyms (HMAC-SHA-256 with a per-subject key held outside the bundle and supplied by reference); the manifest names, but never contains, the key | SPEC §6.7, IR-12; `manifest.json` `pseudonym_key_id` |
| 13 | Cross-engine parser divergence on number tokens | A2 | A number token the canonical form refuses (a fraction or exponent, even on a whole number; an integer above `2^53 − 1`) is refused by every engine, and the accepted form is identical, so a value one engine hashes cannot be one another folds to a different number or refuses; literal ordering in shape constraints is exact and covers only integer and date-time forms | `spec/model/canonical-form.md` (number grammar), `spec/rules/psp.md` (literal ordering); the `numeric-temporal-edge-vectors` job in `three-engine.yml` |

## Untrusted evidence-bundle input hardening

An evidence bundle's contents -- the manifest, the YAML files that ride with it (the applicability
profile, the domain binding), and every event line -- are adversarial input to the file-system and
parsing layer, independent of whatever the evidence itself claims about the assessed agent (SPEC
§8.1, §8.7). This section names the mitigations that hold that boundary and the check that proves
each.

- **Path confinement and symlink handling.** `bundle.py`'s manifest loader refuses a listed path that
  is absolute or contains a `..` segment, and additionally resolves every listed path (following
  symlinks) and refuses one that resolves outside the bundle root -- so a manifest entry with an
  innocuous relative string that is actually a symlink to a file outside the bundle cannot be read
  through. Both cases are refused with `input.bundle_manifest_path` at exit `3`, never silently
  followed. Proved by `engines/python/tests/test_bundle.py` and the bundle-manifest checks in
  `engines/python/tests/test_input_hardening.py`.
- **Structural depth limits.** `Profile.load`/`DomainBinding.load` (`safe_yaml.py`) and per-event JSON
  parsing (`ingest.py`) each catch the `RecursionError` a pathologically deep -- but small-byte --
  nested structure raises, and refuse it with a specific, named `input.*` key (`input.profile_invalid`,
  `input.domain_binding_invalid`, `input.event_structure_too_deep`) at exit `3`, never letting it fall
  through to the generic `internal.unexpected` catch-all a genuinely unforeseen bug uses.
- **YAML-hazard handling.** The same `safe_yaml.py` wrapper around `yaml.safe_load` catches
  `yaml.YAMLError` (a disallowed tag such as `!!python/object/apply:...`, which `safe_load` already
  refuses to construct) and reports it under the same named `input.*` key, rather than the generic
  catch-all.
- **Size limits.** A single evidence-event line over the per-event byte limit continues to be
  quarantined `oversize` (`ingest.py`, `DEFAULT_MAX_EVENT_BYTES`); a manifest-listed file's own byte
  size is now checked with `stat()`, before it is opened or hashed, against
  `DEFAULT_MAX_MANIFEST_FILE_BYTES` (`bundle.py`), refused with `input.bundle_manifest_file_too_large`
  if it is over the limit.
- **Archive hazards: no attack surface today, by design.** `--bundle` must already be an existing
  directory (`commands/__init__.py`'s `_require_dir`); no archive format (zip, tar, gzip) is ever
  parsed anywhere in the ingestion path, so zip-slip and decompression-bomb payloads have nothing to
  reach. This is a stated design choice, not an oversight: if archive convenience-loading is ever
  proposed, its hazards must be closed here first, before any extraction step is added.
- **Fuzz fixtures and the CI guard.** The hostile fixtures above (a symlink escaping the bundle root, a
  disallowed YAML tag, pathologically deep YAML and JSON, an oversized manifest-listed file) are proved
  by the `evidence-input-hardening` job in `.github/workflows/ci.yml`, which runs on every push and
  pull request and fails if any of these refusals regresses.

## Assumptions and residual risk

- The integrity guarantees rest on at least one **independent system** and one **independent coverage
  denominator** per subject; a deployment that declares neither is limited to design-level (`partial`)
  outcomes, and the report says so.
- Offline signature verification depends on a **vendored trust root**; a stale root is a warning recorded
  in the manifest, never a silent network call (SPEC §8.7). The [verification procedure](verification.md)
  gives the trust roots and the per-profile checks.
- The engine treats evidence content as data, never code (no template expansion, no native
  deserialisation, path confinement, structural depth limits, per-event and per-file size limits — see
  "Untrusted evidence-bundle input hardening" above), so a crafted payload cannot execute; this is a
  standing invariant checked by the input-handling tests rather than a per-threat control.
