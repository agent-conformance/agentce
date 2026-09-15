# 0003 — Canonicalisation by a self-contained reference implementation

Status: accepted
Spec refs: SPEC §6.6, §6.7, §5.3

## Context

Integrity hashing (SPEC §6.6) requires that every engine produce identical canonical bytes for the same
evidence (SPEC §6.7). The canonical form is RFC 8785 (JCS). JCS's one implementation-sensitive area is
shortest round-trip formatting of IEEE 754 doubles, and third-party JCS libraries vary in how they
handle object-key ordering by UTF-16 code unit and Unicode normalisation.

## Decision

Canonicalisation is a **self-contained reference implementation** using only the standard library
(`spec/model/canonical.py`), under the AgentCE evidence profile that forbids non-integer JSON numbers.
Because floats never appear in canonical evidence, the hard part of JCS is removed and the canonical
form reduces to a small set of exact rules (integers as exact decimal, NFC-normalised strings with
ECMAScript escaping, arrays in order, object members sorted by the UTF-16 code units of their keys). The
TypeScript and Java engines implement the same rules rather than depending on a JCS library.

## Alternatives considered (with why not)

- **A third-party JCS library per language.** Hides the rules behind versions that can diverge; some
  libraries mishandle UTF-16 key ordering for astral characters or do not apply NFC, which would break
  cross-engine hashing.
- **Each language's native JSON serializer.** Not canonical: key order, whitespace, and escaping differ.
- **Full RFC 8785 including float formatting.** Unnecessary once the profile forbids non-integer
  numbers, and the float path is exactly the part most likely to diverge across languages.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- **Determinism.** The rules are fixed and exercised by 56 cross-language vectors; a non-integer number
  or a duplicate key after NFC is refused with a stable reason.
- **Portability.** Each engine implements roughly 120 lines to the same rules (ADR-0008); the vectors are
  the shared contract.
- **Performance.** Linear in input size; no formatting search.

## Verification (the test or check that proves the decision holds)

`spec/model/test-vectors/` with `test_vectors.py` runs the vectors in Python and adds hand-written
assertions for UTF-16 ordering, escaping, and NFC (eval P0.5); the ECS requires the same vectors to pass
byte-identically in the TypeScript and Java engines (item 3.4 onward).
