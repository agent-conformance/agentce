# Quickstart — TypeScript engine

Run the Engine Conformance Suite with the TypeScript engine on a fresh machine (SPEC §11.5, §5.3). The
TypeScript engine implements the `conformance run` command: it assesses every project in the simulated
corpus and writes an `assertions.json` per project that is byte-identical to the reference engine's after
RFC 8785 canonicalisation. Its evidence packs are byte-identical too, and its `oscal-ar.json` and
`results.sarif` are identical once the engine name and version each engine carries are set aside.

The engine also implements `assess`, `validate`, `report`, and `quickstart` on the same evaluation path,
so you can assess an agent's own evidence with it directly. `report --validate` (schema validation of the
report artifacts) is not yet ported; use the Python engine for that one check — see the
[main quickstart](quickstart.md).

## Prerequisites

- Node 22 (with Corepack)
- `uv`, which the suite uses to materialise the corpus by running its Python generator

## Build

From the repository root:

```bash
cd engines/typescript
corepack enable
pnpm install --frozen-lockfile
pnpm build
```

## Run the conformance suite

```bash
node bin/agentce.js conformance run --engine . --corpus ../../corpus --out ./out
```

The run prints one line such as `ECS: 30/30 identical; claim full; no_ml pass` and exits 0 when the engine
claims `full`. The output directory holds `implementation-report.json` and one
`projects/<domain>/<style>/<variant>/assertions.json` per project.

## Assess an agent's evidence

`node bin/agentce.js quickstart --out ./out` assesses the engine's own vendored quickstart project end to
end, offline, in one command. To assess a bundle of your own:

```bash
node bin/agentce.js assess \
  --bundle ../../corpus/quickstart/evidence \
  --profile ../../corpus/quickstart/applicability.yaml \
  --domain ../../corpus/quickstart/domain.linkml.yaml \
  --out ./out
```

The profile's `catalogs:` list names what to assess against; each `id@version` resolves to a catalog that
ships inside the engine, or pass `--catalog-dir <dir>` for one on disk. `node bin/agentce.js validate
--bundle <dir>` reports what an evidence bundle would ingest and quarantine without assessing it, and
`node bin/agentce.js report --from ./out/assertions.json --format {md,html,oscal,sarif,pack}` re-renders a
report from a committed `assertions.json`.
