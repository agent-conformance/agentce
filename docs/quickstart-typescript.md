# Quickstart — TypeScript engine

Assess the vendored quickstart project with the TypeScript engine on a fresh machine (SPEC §13.4,
§5.3). The TypeScript engine produces byte-identical `assertions.json` to the reference engine after
RFC 8785 canonicalisation.

## Prerequisites

- Node 22 (with Corepack)

## Build and run

```bash
cd engines/typescript
corepack enable
pnpm install --frozen-lockfile
pnpm build
```

Then assess the vendored [`corpus/quickstart`](../corpus/quickstart) project from the repository root:

```bash
node engines/typescript/bin/agentce.js assess \
  --bundle corpus/quickstart/evidence \
  --profile corpus/quickstart/applicability.yaml \
  --domain corpus/quickstart/domain.linkml.yaml \
  --catalog eu-ai-act@2026.09 \
  --catalog-dir spec/catalogs/base/eu-ai-act \
  --out ./out
```

The report directory has the same shape as the reference engine's — see the
[main quickstart](quickstart.md) for what each file contains.
