# Quickstart — Java engine

Assess the vendored quickstart project with the Java engine on a fresh machine (SPEC §13.4, §5.3). The
Java engine produces byte-identical `assertions.json` to the reference engine after RFC 8785
canonicalisation.

## Prerequisites

- JDK 21 (Temurin or another distribution)
- Gradle (the wrapper `./gradlew` is vendored)

## Build and run

```bash
cd engines/java
./gradlew --no-daemon installDist
```

Then assess the vendored [`corpus/quickstart`](../corpus/quickstart) project from the repository root:

```bash
engines/java/build/install/agentce/bin/agentce assess \
  --bundle corpus/quickstart/evidence \
  --profile corpus/quickstart/applicability.yaml \
  --domain corpus/quickstart/domain.linkml.yaml \
  --catalog eu-ai-act@2026.09 \
  --catalog-dir spec/catalogs/base/eu-ai-act \
  --out ./out
```

The report directory has the same shape as the reference engine's — see the
[main quickstart](quickstart.md) for what each file contains.
