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

## Distribution channels

Which channels serve a runnable release is recorded in one file, [`release-state.json`](release-state.json):
PyPI, npm, Maven Central, the container registry, the Homebrew tap, and the site. Each channel is
`published: false` until the maintainer publishes to it. A page shows the zero-install command for a
channel only while it is published; until then it shows the command that works today from a checkout.

- `python3 tools/release_state_check.py` fails when any tracked text presents the one-liner of an
  unpublished channel.
- `python3 tools/release_state_check.py --online` is the verification after flipping a channel: it
  downloads what the registry serves and runs it from an empty directory with the network cut off.
- `python3 tools/installed_artifacts_check.py all` builds the wheel, sdist, npm tarball and runnable jar
  from the current sources and runs each the same way; the `installed-artifacts-offline` job in
  `.github/workflows/quickstart.yml` runs it on every pull request.

The TypeScript and Java artifacts run the conformance suite and `--version` today; their `assess` and
`quickstart` commands are on the roadmap, and the checks tighten when those land.

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
