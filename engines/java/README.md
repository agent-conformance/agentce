# agentce-java

The Java engine of the Agent Conformance Engine (SPEC §5.3): a deterministic, read-only, model-free
port of the reference engine. It implements the Engine Conformance Suite path so `agentce conformance
run` produces reports byte-identical to the other engines after RFC 8785 canonicalisation.

The command-line tool implements `conformance run` (the conformance suite), `assess`, `validate`,
`report` (including `report --validate`, schema validation of the report artifacts), `quickstart`, and
`--version`.

Every module cites the specification section it implements. The evaluation path has no learned
component (SPEC §8.7, HR-1/HR-2); `no_ml` scans `gradle.lockfile` against the shared denylist.

## Build and test

```
./gradlew --no-daemon check
```

Java 21 and the Gradle wrapper (pinned to 8.10.2) are the only prerequisites; dependencies are locked
in `gradle.lockfile`. The vendored `agentce-evidence.schema.json` is kept byte-identical to
`spec/model/generated/json-schema/` by a sync test.

## Run the conformance suite

```
./gradlew -q installDist
build/install/agentce/bin/agentce conformance run --engine . --corpus ../../corpus --json
```

`--adapters <dir>` also runs adapter conformance (SPEC §11.5, §12.3) as the Python engine does. It runs
the directory's own `conformance.py` through `uv run` and writes the result into the report under
`adapter_conformance`, with the claim under `adapters`. The run exits 1 unless that claim is `full`.
`conformance`, `validate`, `diff` and `version` read their command lines through the same declared
grammar as `assess` (below), so `-h` prints the Python engine's usage and an unknown flag, an extra
argument or an empty value exits 3 with a key.

## Assess a bundle, or try the bundled quickstart project

```
./gradlew -q installDist
build/install/agentce/bin/agentce assess \
  --bundle path/to/evidence --profile path/to/applicability.yaml \
  --catalog-dir path/to/catalog --out ./out --json
build/install/agentce/bin/agentce quickstart --json
build/install/agentce/bin/agentce report --from ./out/assertions.json --format md
```

`assess` reads its command line through one declared grammar (`Argv.java`, SPEC §8.5) the way the
Python engine's argparse does: `--<option>=<value>` works for every value option, the last of a repeated
option wins, `-h`/`--help` prints the usage wherever it sits before `--`, and an unknown flag exits 3
with `input.assess_unrecognized_flag`. `--report-language de|en` sets the report language (SPEC §9.3).
Until they are ported, `--package-for-sharing` is refused with `input.package_unsupported` and a
records folder (`agentce assess <folder>`) with `input.records_unsupported`; pass `--bundle` and
`--profile`, or use the Python engine.

## Test seams

The cross-engine checks call a few computation seams (`numerics`, `digest-tree`, `security-view`,
`auditor-view`, `fail-on-check`, `otel-genai-fixture`). They are not `agentce` commands, and
`agentce digest-tree` is refused as an unknown command. They run from their own class in the same jar:

```
java -cp 'build/install/agentce/lib/*' org.agentce.Seams digest-tree path/to/catalog
```
