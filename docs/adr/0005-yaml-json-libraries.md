# 0005 — YAML and JSON libraries

Status: accepted
Spec refs: SPEC §7.1, §8.7, §6.7; ADR-0003

## Context

The engine parses catalogs, applicability profiles, domain bindings, and deviation registers written in
YAML; validates them and the evidence against JSON Schema; and emits JSON outputs. Evidence and
declarations are data, never code (SPEC §8.7), so parsing must not construct arbitrary objects. Outputs
that feed digests must be produced by the canonical form (ADR-0003), not by a pretty-printer.

## Decision

- **YAML:** PyYAML with **`safe_load` only**; the unsafe full loader is never used.
- **JSON:** the standard-library `json` module for reading and ordinary (non-canonical) writing; the
  canonical form (ADR-0003) for any bytes that are hashed or compared.
- **Schema validation:** the `jsonschema` package against **JSON Schema 2020-12**, the dialect every
  schema in `spec/` declares.

YAML timestamps are a known hazard: `safe_load` turns an unquoted RFC 3339 string into a native
datetime. Schemas treat timestamps as strings, and the authored examples quote them, so a declaration
that relies on the datetime coercion is rejected rather than silently reshaped.

## Alternatives considered (with why not)

- **`yaml.load` (full loader) or ruamel round-trip.** The full loader can construct arbitrary Python
  objects from input — a code-execution surface incompatible with SPEC §8.7. Round-trip preservation is
  not needed and adds weight.
- **A third-party JSON parser.** The standard library suffices and keeps the dependency tree minimal.
- **An older JSON Schema draft.** 2020-12 gives `$defs`, `prefixItems`, and a stable `referencing`
  registry for cross-file `$ref` (used by the control schema), and is well supported in all three
  languages.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- **Determinism.** `safe_load` yields plain types; validation and canonicalisation are deterministic.
- **Portability.** The TypeScript engine uses a YAML parser in safe mode plus `ajv` for 2020-12, and the
  Java engine uses SnakeYAML's `SafeConstructor` plus a 2020-12 validator (ADR-0008); all reject unsafe
  YAML constructs and validate against the same schemas.
- **Performance.** Parsing and validation are linear and off the hot evaluation path.

## Verification (the test or check that proves the decision holds)

`spec/validate_examples.py` validates every worked example against its schema (eval P0.6); a lint
forbids `yaml.load` (unsafe) in the engine sources; the schemas self-declare the 2020-12 dialect and are
meta-validated.
