# Quickstart — Java engine

Run the Engine Conformance Suite with the Java engine on a fresh machine (SPEC §11.5, §5.3). The Java
engine implements the `conformance run` command: it assesses every project in the simulated corpus and
writes an `assertions.json` per project that is byte-identical to the reference engine's after RFC 8785
canonicalisation.

The Java engine does not yet implement `assess`, `report`, `validate`, or `quickstart`; those are planned.
To assess an agent's evidence today, use the Python engine — see the [main quickstart](quickstart.md).

## Prerequisites

- JDK 21 (Temurin or another distribution)
- Gradle (the wrapper `./gradlew` is vendored)
- `uv`, which the suite uses to materialise the corpus by running its Python generator

## Build and run

From the repository root:

```bash
cd engines/java
./gradlew --no-daemon installDist
build/install/agentce/bin/agentce conformance run --engine . --corpus ../../corpus --out ./out
```

The run prints one line such as `ECS: 30/30 identical; claim full; no_ml pass` and exits 0 when the engine
claims `full`. The output directory holds `implementation-report.json` and one
`projects/<domain>/<style>/<variant>/assertions.json` per project.
