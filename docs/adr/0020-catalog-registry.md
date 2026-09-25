# 0020 — Catalog and implementation-report registry schemas live under `conformance/registry/`

Status: accepted
Spec refs: SPEC §9, §11.5, §14.5 CP-1

## Context

Item 16.4 adds two machine-validated registries: a **catalog registry** (one entry per published,
signed AgentCE base catalog — `eu-ai-act` and `nist-ai-rmf` today) and a schema for the existing
**implementation-report registry** (`conformance/registry_check.py`, SPEC §11.5, §14.5 CP-1), which
previously validated a report record only by signature/digest/claim logic in code, never against a
schema. Both need a JSON Schema and, for the catalog registry, a data file listing real entries.

`docs/build.py::_report_schema_rows` globs every `spec/report/*.schema.json` and raises unless the
file is registered in `website/iri-manifest.json`, which then generates a public reference page
checked by `docs/build.py --check-links`/`--check-publish` and the `website` package's own CI job on
every pull request, plus `pnpm run check:iri`. `spec/report/` is SPEC §9's published report-format
surface: every schema there is a canonical, IRI-addressed artifact the site serves.

Neither registry is that yet. `PHASE-16-DOMAIN-COMPLETION.md` row 16.k is explicit that this item
builds "the technical parts; foundation/marks are human actions" — opening either registry to
third-party (non-AgentCE) submissions, standing up a public registry endpoint, and the
conformance-marks/attestation regime are human-gated decisions (`H10`, `GA-9`, `GA-12`), not something
this item's code changes.

## Decision

1. **Both new schemas live under `conformance/registry/`**, beside the registry data they validate:
   `conformance/registry/catalog-registry.schema.json` and
   `conformance/registry/implementation-report.schema.json`. Neither carries an
   `agent-conformance.org` IRI as its `$id`, so neither is discovered by `_report_schema_rows`'s glob
   and neither needs a `website/iri-manifest.json` entry or a matching generated reference page. This
   keeps a registry that is not yet public off the publication surface that is checked, and published,
   on every pull request.
2. **Validation reuses the already-depended-on `jsonschema` library** (`engines/python/pyproject.toml`
   pins it directly), the same way `agentce/catalog.py`, `agentce/report.py`, and
   `agentce/readiness.py` already validate their own inputs. No new dependency, no lockfile change.
3. **The catalog registry is seeded with two real entries.** `conformance/registry/catalogs.json` lists
   `eu-ai-act` and `nist-ai-rmf`: both are genuinely published, genuinely signed base catalogs, so the
   registry is not a demonstration over invented data. `conformance/catalog_registry_check.py`
   recomputes each entry's digest from the real catalog directory on disk and verifies the signature
   against the vendored trust root before calling an entry listed — an entry whose recorded digest no
   longer matches a fresh recomputation (a rebranded or edited catalog reusing the same id) is refused,
   never silently listed.
4. **The implementation-report registry's data directory stays empty.** `conformance/reports/` is
   empty by design pre-general-availability: no engine or adapter's report has been produced and
   accepted yet. `GA-9` tracks populating it once real, verified reports exist; this item adds the
   schema `registry_check.py` now validates every future report record against, before its existing
   signature/digest/claim checks run.
5. **A future item can promote either schema to a canonical IRI** once the registry it validates is
   actually published — that is a separate, human-gated decision (`H10`/`GA-9`/`GA-12`), tracked in
   `HUMAN_ACTIONS.md`, not implied by this decision.

## Alternatives considered (with why not)

- **Put both schemas under `spec/report/` now, ahead of publication.** Rejected: that directory is
  SPEC §9's checked, published report-format surface. Placing an internal-only validation schema there
  would force a `website/iri-manifest.json` entry and a generated, publicly served reference page for
  a registry nobody can query yet, misrepresenting it as a finished public artifact.
- **Skip a schema for the implementation-report registry and rely on `registry_check.py`'s existing
  Python-level signature/digest/claim checks alone.** Rejected: those checks are real, but they are not
  schema validation — a structurally malformed record (for example, missing the `claim` field) reached
  the same code path as a well-formed one and was refused only for failing the conformance claim, not
  for being malformed. A dedicated schema step catches structural defects earlier and independently,
  with its own status value (`schema-invalid`).
- **Open the catalog registry to any catalog directory found on disk, signed or not.** Rejected: an
  unsigned catalog has no basis for trust offline. Only catalogs carrying a detached signature that
  verifies against the vendored trust root are eligible to be listed.

## Consequences (including determinism, portability to TS/Java, performance)

- **Determinism / offline.** Digest recomputation (`signing.digest_tree`) and signature verification
  (`signing.verify_catalog_directory`) are pure, deterministic functions over files on disk and the
  vendored trust root; no network, clock, or locale dependence. Schema validation with `jsonschema` is
  likewise deterministic and offline.
- **Portability.** Both schemas and the registry checkers are repository-level conformance tooling in
  `conformance/`, not part of any engine's evaluation path; they have no TypeScript or Java
  counterpart and touch no engine dependency tree.
- **No weakening.** `registry_check.py`'s existing signature/digest/claim gate is unchanged; schema
  validation runs strictly before it, as an additional, earlier refusal path.
- **Residual scope.** These registries validate and seed data; they do not accept third-party
  submissions and are not served at a public endpoint. That remains a human-gated decision recorded in
  `HUMAN_ACTIONS.md`, closed only when the site is ready to publish either as a real, addressable
  service.

## Verification (the test or check that proves the decision holds)

- `conformance/catalog_registry_check.py --self-test` proves both the accept path (a well-formed
  entry is listed) and the refuse path (a digest-mismatched entry is refused), validating
  `catalogs.json` against `catalog-registry.schema.json` first.
- `conformance/registry_check.py --self-test` proves a well-formed report is listed, a report with an
  unverifiable golden digest is refused, and a report missing a schema-required field is refused with
  a distinct `schema-invalid` status before signature verification is attempted.
- `tools/claims_check` and the repository's authored-text sweep both run over every file this item
  adds; neither registry appears in `website/iri-manifest.json`, so
  `docs/build.py --check-links`/`--check-publish`
  and the `website` package's own checks are unaffected by this decision.
