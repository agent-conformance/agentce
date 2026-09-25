# Catalog and implementation-report registries

Two registries live here, each machine-validated against a JSON Schema: the **catalog registry**
(`catalogs.json`, `catalog-registry.schema.json`) and the **implementation-report registry** schema
(`implementation-report.schema.json`, consumed by [`../registry_check.py`](../registry_check.py)).
Both are the technical validation machinery only (SPEC §11.5, §14.5 CP-1; item 16.4 "catalog registry
(technical parts)" / "implementation-report registry (technical parts)"); see
[`docs/adr/0020-catalog-registry.md`](../../docs/adr/0020-catalog-registry.md) for why the schemas live
here and not under `spec/report/`.

## Catalog registry

`catalogs.json` lists one entry per published, signed AgentCE base catalog (`id`, `version`, `digest`,
`source_path`, signing metadata: `eu-ai-act` and `nist-ai-rmf` today, both real and already signed).
[`../catalog_registry_check.py`](../catalog_registry_check.py) validates the file against
`catalog-registry.schema.json`, recomputes each entry's digest from the real catalog directory on
disk, and verifies the signature against the vendored trust root — refusing an entry whose recorded
digest does not match a fresh recomputation (a rebranded or edited catalog under the same id):

```
cd conformance && uv run python catalog_registry_check.py --self-test
cd conformance && uv run python catalog_registry_check.py
```

## Implementation-report registry

`implementation-report.schema.json` is the shape a report record in `../reports/` must have
(`engine.impl`/`version`/`package_digest`, `claim`, `no_ml`, `golden.revision`/`digest`/`signature`).
`../registry_check.py`'s `evaluate_report` validates a record against this schema first — a
structurally malformed record (for example, one missing the schema-required `claim` field) is refused
with a distinct `schema-invalid` status before its golden-revision signature is even checked — and
then applies the existing signature/digest/claim gate: a report is `listed` only if its golden
revision verifies against its signature and it claims `full` conformance with `no_ml: pass`.
`../reports/` itself stays empty by design pre-GA; `GA-9` tracks populating it with every shipped
engine and adapter's verified report.

```
cd conformance && uv run python registry_check.py --self-test
cd conformance && uv run python registry_check.py --json
```

## Scope: technical parts only

Neither registry is a public, served artifact yet. Opening the catalog registry to third-party
(non-AgentCE) catalog submissions, standing up a public registry endpoint, and the
conformance-marks/attestation regime are human-gated: `H10` (marks), `GA-9` (populate the
implementation-report registry with every shipped engine/adapter's verified report, `HUMAN_ACTIONS.md`),
and `GA-12` (close the marks decision). This item builds and seeds the validation machinery only, with
AgentCE's own two real, already-signed catalogs.
