# 0008 — TypeScript and Java library equivalents

Status: accepted
Spec refs: SPEC §5.3, §14.5; ADR-0001..0006

## Context

The specification is realised by several engines that must produce identical results (SPEC §5.3), and
conformance requires **independent** implementations agreeing, not one shared binary (SPEC §14.5). Each
choice made for the Python reference engine therefore needs a TypeScript and Java equivalent that yields
the same bytes and outcomes, without coupling the engines to a common native core.

## Decision

Each engine is implemented independently to the shared specifications (the canonical form, the numerics
document, the PSP, and the schemas), using these library equivalents:

| Concern | Python (reference) | TypeScript | Java |
|---|---|---|---|
| Canonical form (ADR-0003) | `canonical.py` (stdlib) | hand-implemented to the same rules | hand-implemented to the same rules |
| Graph store (ADR-0001) | SQLite (stdlib `sqlite3`) | SQLite via a native binding | SQLite via JDBC |
| PSP compilation (ADR-0002) | PSP AST → SQL | PSP AST → SQL | PSP AST → SQL |
| YAML (ADR-0005) | PyYAML `safe_load` | a YAML parser in safe mode | SnakeYAML `SafeConstructor` |
| JSON Schema (ADR-0005) | `jsonschema` (2020-12) | `ajv` (2020-12) | a 2020-12 validator |
| Numerics (ADR-0006) | `Fraction` + `decimal` | `BigInt` + `decimal.js` | `BigInteger` + `BigDecimal` |
| CLI (ADR-0004) | `argparse` | `util.parseArgs` | a minimal parser or picocli |

The shared artifacts — canonical-form vectors, numerics vectors, the PSP and its `psp_check`, and the
schemas — are the contract each engine is written against.

## Alternatives considered (with why not)

- **A shared native core exposed via FFI or WebAssembly.** Makes the engines one implementation behind
  three wrappers, which undermines the independent-agreement basis of conformance (SPEC §14.5).
- **Transpiling the Python engine to TypeScript/Java.** Produces non-idiomatic, fragile code and, again,
  a single implementation rather than an independent one.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- **Determinism and portability.** Independence is preserved; agreement is proven empirically by the
  conformance suite rather than assumed from shared code.
- **Performance.** Each engine uses its platform's native SQLite and big-number facilities, so the
  performance targets (SPEC §8.6) are met per engine within the 2× conformance band.

## Verification (the test or check that proves the decision holds)

The two-engine ECS is byte-identical on the full corpus (item 3.6) and the three-engine ECS in CI
(item 5.2); the canonical-form and numerics vectors pass in every engine. Divergence in any digit fails
conformance.
