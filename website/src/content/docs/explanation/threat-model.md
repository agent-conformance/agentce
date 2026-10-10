---
title: Threat model (v1)
description: 'Status: accepted Spec refs: SPEC §8.7 (security of the engine itself), §6.6 (integrity envelope), §10.4 (corroboration), §6.4 (trust classes), §6.5 (c'
---

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
- **The pseudonymisation key** (SPEC §6.7) — SPEC §6.7 says it is supplied by reference and never kept in
  the bundle; the engines take no key yet (row 12).

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
  instruction-provenance laundering. Today the engines counter it only in part: a control's minimum
  evidence asks for events of named types and source classes. Three defenses the SPEC names are not
  built yet. They are holding each control to the minimum source class it declares (planned as 18.127), the
  two-source check of SPEC §10.4 (planned as 20.11), and coverage denominators that decide INT and REC
  verdicts (SPEC §6.5, planned as 18.134). Until they land, the engines take each record's declared
  source class as given; rows 1, 5, 8 and 11 say what this leaves open.
- **A2 — Tampering with the record after the fact.** Replay, reordering, timestamp manipulation, chain
  recomputation after export, or re-identification of pseudonymised subjects. The SPEC counters it with
  the integrity envelope (SPEC §6.6) and keyed pseudonyms (SPEC §6.7). The engines check hash chains,
  anchors and timestamps and report the results in `integrity.jsonl`, but those results do not yet
  change a verdict (rows 2, 3 and 6, planned as 18.132), and the engines take no pseudonym key yet
  (row 12).
- **A3 — Subversion of the toolchain and trust base.** A modified engine or catalog, catalog or corpus
  substitution, or collusion between the source and the assessor. Countered by signed, offline-verified
  catalogs and releases, the engine and input digests in the manifest, and independent verifier reruns.

## Threats, mitigations, and the check that proves each

| # | Threat (SPEC §8.7) | Class | Mitigation | Test / control reference |
|---|---|---|---|---|
| 1 | Evidence spoofing | A1 | Trust classes. Every event carries the class its source declared, and a control's `minimum_evidence` asks for at least one event of a named type and class in the subject's records. The engines read a control's `min_source_class` but do not apply it yet (`source_class_satisfied` is always true), so a `self_report`-only stream can satisfy a control that declares a higher minimum when its `minimum_evidence` does not name that class (INT-01 and INC-02, for example), and the class check never looks at the records a verdict rests on. The fix is planned as 18.127. Model-stated rationale is never accepted as evidence of "why" | SPEC §6.4, IR-03; a control's `min_source_class` and `minimum_evidence` (SPEC §7.1); ECS spoofing fixtures |
| 2 | Replay / duplicate events | A2 | Hash chain (`integrity.prev`) and per-stream ordering; a repeated id is quarantined `duplicate_id`. A broken or reordered chain appears in `integrity.jsonl` but does not yet change a verdict (planned as 18.132) | SPEC §6.6; quarantine reason `duplicate_id` (App F); `quarantine.schema.json` |
| 3 | Timestamp manipulation | A2 | Source timestamps cross-checked against the anchors that cover them; the engine marks an out-of-range stream `time_suspect` in `integrity.jsonl` and quarantines `time_order` an event whose time precedes the one before it in its stream. A `time_suspect` event still counts in timing controls today, because integrity results do not yet change a verdict | SPEC §6.6; `IntegrityResult.status = time_suspect`; INC/REC timing controls |
| 4 | Self-approval through agent-held credentials | A1 | The human-actor rule requires a distinct human approver. SPEC §6.4 defines `independent_system` as a system in which no principal in the agent's delegation chain can create or transition the record; the engines take a source's declared class as given and do not yet hold a control to its declared minimum class (planned as 18.127) | SPEC §6.4, §10.4; OVS-03 expectation E2, CND-02 |
| 5 | Enforcement-point bypass (direct provider calls) | A1 | Coverage denominators independent of both the agent and the enforcement point (egress proxy, firewall, admission attestation); `coverage.json` and `agentce readiness` report unreconciled coverage as `unknown`. That `unknown` does not yet change a verdict, so INT and REC controls can read conformant without an independent denominator (SPEC §6.5 says `insufficient_evidence`; planned as 18.134) | SPEC §6.5; `coverage.json`; INT and REC families |
| 6 | Export-host chain recomputation | A2 | `export_anchored` anchors each stream head out-of-band to a write-once location; an `export_chained` stream's integrity result is `verified_weak`. The SPEC §6.6 cap of its rung-2 outcomes at `partial` is not built yet, because integrity results go to `integrity.jsonl` and do not yet change a verdict (planned as 18.132) | SPEC §6.6; `IntegrityResult.strength`; integrity strength rules |
| 7 | Modified engines | A3 | The manifest records the engine package digest; verifiers rerun with the signed release and check its signatures with `agentce verify --release`. The engines do not yet mark a report from an engine whose digest is not a published release `engine_unverified` in the claim (SPEC §8.7) | SPEC §8.7, §8.4; `manifest.json`, claim `engine_unverified`; [verification](/explanation/verification/) |
| 8 | Shape-satisfying-but-meaningless records | A1 | Minimum evidence. A control needs the events its `minimum_evidence` names, but that check asks only that one such event exists somewhere in the subject's records, not that the records a verdict rests on have that class, and the two-source check of SPEC §10.4 is not applied, so a well-formed self-report beside one unrelated enforcement-point event can satisfy a high-severity control today (CND-05 does). Planned as 18.127 (source class) and 20.11 (§10.4) | SPEC §7.1 `minimum_evidence`, IR-02/IR-03; §10.4 |
| 9 | Collusion between source and assessor | A3 | Independent verifier reruns from the reproducibility manifest. The claim does not yet record `assessor_independent` (SPEC §9.1), and coverage denominators do not yet decide a verdict (row 5) | SPEC §8.4; claim `assessor_independent`; ECS reproducibility |
| 10 | Catalog substitution | A3 | `agentce assess` verifies every `--catalog-dir` catalog against the effective trust root (`--trust-root`, else `AGENTCE_TRUST_ROOT`, else the vendored development root) before it evaluates a control, and refuses an unsigned, untrusted, or altered one with `input.catalog_unverified` (exit 3); unverified use requires the explicit `--allow-unverified-catalog`, which the manifest and the claim record as a limitation; a catalog whose own `id@version` is not the one requested is refused with `input.catalog_mismatch` and has no override; the manifest records each catalog's content digest. This covers the base catalog today; the sector overlays are not yet signed (tracked as follow-up work). The probe corpus is protected by a separate, non-Sigstore mechanism instead — a frozen content hash plus a recorded human sign-off, checked at load time (SPEC §7.2) — and rule fixtures under `spec/rules/` carry no signature or integrity check today. SPEC §8.7's text naming catalogs, overlays, probe corpora, and rule fixtures together as Sigstore-signed is broader than what is built; closing that gap for overlays, probe corpora, and rule fixtures is tracked as follow-up work, not yet built | SPEC §8.7; `agentce verify --catalog`; [verification](/explanation/verification/); `manifest.json` input digests and `limitations`; `engines/python/tests/test_catalog_signature.py`; `engines/python/agentce/probe.py` (probe corpus hash + sign-off check) |
| 11 | Instruction-provenance laundering | A1 | Each Instruction event carries the `source_class` its producer declared, and CND-05 judges an action's instruction chain from those classes, so an action on an instruction that declares no trusted class reads non-conformant. Nothing yet compares an agent-side `source_class` with an ingress record of the same instruction, and no adapter produces ingress Instruction events, so an instruction the agent relabels as `user` is not caught. Taking the declared class as given is planned to end with 18.127; reconciling against ingress records waits for the first gateway reader of Instruction events | SPEC §7.7.5; CND-04, CND-05 |
| 12 | Pseudonym re-identification by dictionary | A2 | Principal IRIs are HMAC-SHA-256 pseudonyms of the principal id. The engines take no key yet, so every run uses the documented all-zero key and its principal IRIs can be reversed by dictionary; a per-subject key supplied by reference, the manifest's `pseudonym_key_id`, and the `pseudonymisation: none` limitation in the claim (SPEC §6.7) are not built yet | SPEC §6.7, IR-12; `manifest.json` `pseudonym_key_id` |
| 13 | Cross-engine parser divergence on number tokens | A2 | A number token the canonical form refuses (a fraction or exponent, even on a whole number; an integer above `2^53 − 1`) is refused by every engine, and the accepted form is identical, so a value one engine hashes cannot be one another folds to a different number or refuses; literal ordering in shape constraints is exact and covers only integer and date-time forms | `spec/model/canonical-form.md` (number grammar), `spec/rules/psp.md` (literal ordering); the `numeric-temporal-edge-vectors` job in `three-engine.yml` |
| 14 | Code injection through `assess --fail-on` | A3 | The expression is parsed by a hand-written tokenizer and a parser with no call, attribute-access, or grouping syntax in the grammar at all, into a flat list of OR-groups of AND-ed comparisons, so evaluating even a long chain never recurses; a dunder-import call, a backtick-embedded shell command, a bare `os.system(...)`, or a well-formed clause with an evaluable tail appended after `or` is refused as an unexpected token at parse time, before any assertion is evaluated, never by `eval`/`exec` | SPEC §7, §8.5; `agentce/fail_on.py`, `engines/typescript/src/failOn.ts`, `engines/java/src/main/java/org/agentce/FailOn.java`; `input.fail_on_invalid_expression` (exit 3); `engines/python/tests/test_policy_gating.py` |

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

## Untrusted `ingest`/`collect` export-file input

`agentce ingest` and a real (non-dry-run) `agentce collect` both read an already-exported evidence
file from disk -- an OTel Collector's file or object-storage export, or whatever `source.export`
names in a collect config (SPEC §5.4) -- through an adapter (`ExportAdapter`), not through the
`--bundle` layout the section above covers. This is a distinct surface with its own trust boundary,
sharing the same hardening building blocks, not the same mitigation:

- **Structural depth limits.** `collect.py`'s config loader now goes through the same
  `safe_yaml.py` wrapper `Profile.load`/`DomainBinding.load` use, so a pathologically deep collect
  config is a deliberate `input.collect_config` refusal at exit `3`, never an uncaught
  `RecursionError`. The adapter's own export parse (`adapters/_ingest.py`, shared by every v1
  adapter, invoked by both `ingest` and the real `collect` path) catches a `RecursionError` from a
  pathologically deep export and reports it in its `{"error": ...}` contract, so the caller
  (`commands/__init__.py`'s `_adapt_export`) surfaces a clean `input.ingest_failed` message rather
  than a raw traceback fragment. Proved by `engines/python/tests/test_collect.py` and the
  `check_hardened_input` fact in `tools/collect_ingest_check.py`.
- **Path confinement does not apply here, by design.** `bundle.confine_to_root`'s threat model is a
  bundle-relative reference inside a shared root someone else produced (a manifest entry that could
  symlink-escape). `source.export`/`agentce ingest --in` are operator-supplied absolute paths naming
  wherever a collector wrote its own output -- the same shape as `--bundle` itself -- so there is no
  shared root to confine them to; the operator invoking `ingest`/`collect` already has whatever
  filesystem access the path grants, exactly as with `--bundle`.
- **Size limits and archive hazards: not yet a control.** Unlike the per-event and per-manifest-file
  limits above, the export file itself carries no byte-size cap and no archive format is parsed here
  either, for the same reason: no archive extraction step exists in this path. If a size limit for
  export files is added, it belongs here.
- **CI guard.** `check_hardened_input` (`tools/collect_ingest_check.py`) runs a deeply nested export
  through `agentce ingest` and a deeply nested config through `agentce collect`, asserting each is
  refused with its named key and no `Traceback` text reaches the user; it is part of the
  `collect-ingest` job in `.github/workflows/ci.yml`, which runs on every push and pull request.

## AI-consumed generated-output surface (remediation package + skill folder)

`agentce assess --emit remediation` and `--emit skill` both generate Markdown that a coding assistant
or AI agent is expected to read and act on directly (SPEC §13.3 S-1..S-10) -- a different trust
boundary from the machine-readable formats above, because here evidence- and profile-derived strings
(a subject id, an evidence ref, a validation path, a tool name) sit inline in the same document as the
catalog- and template-authored instruction text the assistant is meant to follow. An adversary who
controls one of those derived strings (for example, an evidence bundle's `subject` field, or a tool
name observed on an assessed agent) could otherwise smuggle an instruction-shaped line -- a fake
Markdown heading, a bare imperative sentence, a fenced code block -- into that document and have the
reading assistant treat it as part of the trusted prompt rather than as inert data about the finding.

- **Sanitising, not trust separation by format.** Both renderers build their template context by
  routing every evidence- or profile-derived field -- the subject id, each evidence ref, each
  validation path, each tool-call name -- through the same `sanitize_for_markdown` function
  (`engines/python/agentce/report.py:1354`, via the `_sanitize_field` convenience at line 1391;
  `_remediation_md_context` at line 1599 for the remediation package, `_skill_finding_context` at line
  1660 for the skill folder) before it reaches the template (SPEC §7 injection hardening).
  `sanitize_for_markdown` calls the shared `_neutralize` core (line 1298) first -- which collapses
  embedded newlines, other control characters, and Unicode whitespace to single spaces (so a derived
  string can never start a new line and become a live heading or a bare instruction line of its own)
  and drops bidi-override, zero-width, and other `Default_Ignorable_Code_Point` characters entirely --
  then substitutes backtick, `<`/`>`, `[`/`]`, and `&` for inert lookalikes (so it cannot break out of
  the template's own backtick delimiters, pass through as raw HTML, or form Markdown link/image/entity
  syntax of its own), and caps the result at `_SANITIZE_CAP` (200) characters. Instruction sentences in
  the rendered document come only from the template or the signed catalog; everything evidence-derived
  is sanitised and length-capped this way, never trusted verbatim. This is sanitising, not erasure --
  the string still appears, as inert data.
- **Same mitigation, two output formats.** `render_remediation_md` (line 1622) and
  `render_skill_finding_md`/`render_skill_md` (lines 1679/1688) are two templates over the same
  canonical `remediation-package.json`, and both derive their context through `sanitize_for_markdown`,
  so one row covers both F17's and F31's rendered output.
- **Proved by hostile fixtures.** `engines/python/tests/test_remediation.py::test_sanitize_for_markdown_escapes_a_hostile_subject_id`
  renders a package whose subject id contains a Markdown heading and a backtick fence and asserts the
  rendered document neutralises both; `engines/python/tests/test_skill_emit.py::test_finding_note_escapes_a_hostile_tool_name`
  does the equivalent for a hostile tool-call name in a generated skill finding note;
  `engines/python/tests/test_sanitize.py` covers the shared sanitiser directly, including a
  render-level property test that decodes the rendered Markdown/HTML and asserts no bidi-override or
  zero-width character survives, driven over every named adversarial vector in the committed
  `spec/report/test-vectors/sanitize-vectors.json` (item 18.20).

## Supply-chain attestation input (DSSE verification)

The `supply-chain` adapter (SPEC §12) turns admission-controller and registry records into
`Attestation` and `BundleLoaded` events. An adversary who controls the agent, or a colluding source,
can submit an attestation record whose `dsse` envelope is missing, malformed, unsigned, signed by an
untrusted key, or signed over a different subject than what was actually loaded; a fake or superficial
verification check that only reads the record's own claimed `statement_type`/`subject_digests` fields,
rather than cryptographically verifying the signature, would report such a forged, unsigned, or
relabelled attestation as `verified` and let it pass a supply-chain control.

- **Verification against a trusted key, not field-reading.** `_verify`
  (`adapters/supply-chain/src/agentce_adapters/supply_chain.py`) marks an `Attestation` event
  `verified` only on signature and digest binding, never on unsigned record fields; see
  `adapters/supply-chain/README.md` ("Verification at adapt time") for the full `verified`/`failed`/
  `unverified` mechanism (the DSSE pre-authentication encoding, the trusted key table, and the
  `observed_digests` binding). Every negative case resolves to a named `failed` or `unverified` status
  with `signer: null` -- never `verified`, and never an uncaught exception.
- **`BundleLoaded` carries no signature of its own -- a named residual risk, not yet closed.** Unlike
  `Attestation`, a `BundleLoaded` record (a bundle-load log line or CycloneDX AIBOM) has no `dsse`
  envelope and no `verification` field; its trust rests on the adapter run's declared source class
  (`enforcement_point` by default -- `event_producers.json` -- above the `self_report` class that rows
  1 and 8 discuss, though those rows' class gates are not built yet) and the evidence stream's hash-chain and
  anchoring (rows 2, 6), never on cryptographic signature verification. A record can name
  `attestation_refs`, but no engine resolves or cross-checks those ids against a verified `Attestation`
  event today -- `attestation_refs` appears only in schema/type-generation code and the adapter's own
  pass-through, confirmed absent from every engine's logic -- and the undeclared-component
  applicability check (`applicability.py`) reads its unsigned `components` field directly. A forged or
  relabelled `BundleLoaded` record is today's residual risk, tracked as follow-up work, not covered by
  the DSSE mitigation above.
- **Proved by hostile and interoperability fixtures, enforced by a seeded-fault gate.** The public
  verification suite's `VG-ATTESTATION-SIGNATURES` gate (`verification/gates.json`) runs
  `adapters/supply-chain/tests/test_signatures.py` and seeds two faults directly against `_verify` --
  an always-succeeding signature check, and a dropped `observed_digests` comparison -- each
  demonstrated red by `./verification/run --demo-fault VG-ATTESTATION-SIGNATURES` and green again once
  restored. The adapter's own hostile fixtures (`adapters/supply-chain/fixtures/attestations-hostile/`:
  a non-verifying signature, an all-zero forged signature, an untrusted signer, a digest mismatch, a
  malformed payload type) and a cross-signed interoperability fixture
  (`adapters/supply-chain/fixtures/attestations-interop/`) run in
  `adapters/supply-chain/tests/seeded_fault_demo.py` and the `adapter-supply-chain` job in
  `.github/workflows/ci.yml`, on every push and pull request (item 18.43).

## Assumptions and residual risk

- The integrity guarantees rest on at least one **independent system** and one **independent coverage
  denominator** per subject. SPEC §6.5 and §6.6 limit the outcomes of a deployment that has neither,
  but the engines do not apply that limit yet: coverage is written to `coverage.json` and integrity
  results to `integrity.jsonl`, and neither changes a verdict (planned as 18.134 and 18.132), so such a
  deployment can still read conformant.
- Offline signature verification depends on a **vendored trust root** and never makes a network call
  (SPEC §8.7). The engines do not yet check whether that root is stale, so the manifest carries no
  stale-root warning (SPEC §8.7 asks for one; planned as 18.135). The [verification procedure](/explanation/verification/)
  gives the trust roots and the per-profile checks.
- `BundleLoaded` events are not DSSE-verified; see "Supply-chain attestation input" above for the
  residual risk this leaves open.
- The engine treats evidence content as data, never code (no template expansion, no native
  deserialisation, path confinement, structural depth limits, per-event and per-file size limits — see
  "Untrusted evidence-bundle input hardening" above), so a crafted payload cannot execute; this is a
  standing invariant checked by the input-handling tests rather than a per-threat control.
