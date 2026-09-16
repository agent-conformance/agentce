# Versioning and migration

How each artifact is versioned and how compatibility is signalled (SPEC §6.11, §14.2). The rule
throughout: a change that could alter a conformance outcome is visible in a version.

## Evidence model

- Model versions follow `MAJOR.MINOR`; events carry a `type` suffix `.v1`.
- Adding an optional member is a **MINOR** change. Renaming or removing a member is a **MAJOR** change
  and ships with a migration script under `spec/model/migrations/`.

## Catalogs

- Catalog versions are `YYYY.MM[.patch]` and are **immutable once published**. A report names the
  catalog version it was assessed against.
- The engine warns when a newer catalog version exists, so an adopter learns about an update without
  a silent change of outcome.
- A catalog's `provenance` block binds its version and a content digest to its files (SPEC §14.5
  CP-3); `agentce catalog lint --require-provenance` recomputes the digest.

## Engines

- Engine packages follow Semantic Versioning. A major version changes only with a documented reason;
  major versions overlap during the support window (see [SUPPORT.md](SUPPORT.md)).
- Correctness is defined by the Engine Conformance Suite, not by a version string: every engine at any
  version produces byte-identical `assertions.json` for a given corpus and catalog after RFC 8785
  canonicalisation.

## Adapters

- An adapter records the upstream convention version it maps (`agentceconv`), because some upstream
  conventions (for example the OpenTelemetry GenAI attributes) are still evolving.

## Datasets

- The corpus, golden, and probe datasets are pinned by commit hash and manifest SHA-256. A report
  names the golden revision it was compared against, and the yardstick-integrity rule (SPEC §14.5
  CP-4) requires that revision to be signed.

## Reports and claims

- The claim schema and report formats are versioned with the specification. A report is reproducible
  from its manifest (SPEC §8.4): the same inputs, catalog, and engine produce the same outputs.
