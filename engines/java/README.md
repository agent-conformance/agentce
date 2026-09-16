# agentce-java

The Java engine of the Agent Conformance Engine (SPEC §5.3): a deterministic, read-only, model-free
port of the reference engine. It implements the Engine Conformance Suite path so `agentce conformance
run` produces reports byte-identical to the other engines after RFC 8785 canonicalisation.

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
