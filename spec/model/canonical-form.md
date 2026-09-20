# Canonical form

Normative. Implements the specification, §6.7 ("Serialisation and canonical form") and §5.3 ("one
specification, several engines, identical results"). Every engine — Python, TypeScript, Java —
produces the same canonical bytes for the same evidence, so that a hash taken by one engine verifies
under another. `canonical.py` is the reference implementation; `test-vectors/*.json` is the
cross-language contract.

## Where the canonical form is used

- **Integrity hashing (§6.6).** `integrity.hash` is the SHA-256 of the canonical form of the
  CloudEvent with `data.integrity` removed; `integrity.prev` chains those hashes. The bundle digest is
  the SHA-256 of the canonical manifest.
- **Stable identity.** Any place two engines must agree on the bytes of a value — a golden output, a
  signed statement's subject — uses the canonical form.

## Definition

The canonical form is the **RFC 8785 JSON Canonicalization Scheme (JCS)** applied under the AgentCE
evidence profile. The profile removes JCS's only implementation-sensitive area — floating-point number
formatting — by forbidding non-integer JSON numbers, so the canonical form reduces to a small set of
exact rules.

A JSON value is serialised as follows, producing **UTF-8 bytes** with no insignificant whitespace.

1. **null / true / false** serialise as the tokens `null`, `true`, `false`.
2. **Numbers are integers only.** An integer serialises as its exact decimal representation, with a
   leading `-` for negatives and no leading zeros, plus sign, decimal point, or exponent. A JSON
   number that is not an integer (it has a fractional part or an exponent, i.e. a floating-point value)
   is **refused**: it is a profile violation, never coerced. Counts and durations are integers;
   decimals are carried as strings with a declared scale (below), and never as JSON numbers. Integers
   are within the range every engine represents exactly (`|n| ≤ 2^53 − 1`); an integer of larger
   magnitude is **refused** as `integer_out_of_range`, never rounded. The rule is stated on the number
   **token as written**, in the grammar below, not on the value a JSON parser folds it to.
3. **Strings** are first normalised to **Unicode NFC**, then wrapped in `"` and escaped exactly as
   ECMAScript `JSON.stringify` escapes: `"` → `\"`, `\` → `\\`, U+0008 → `\b`, U+0009 → `\t`,
   U+000A → `\n`, U+000C → `\f`, U+000D → `\r`; any other character below U+0020 → `\u00xx` with
   **lowercase** hex; every other character, including the forward solidus `/`, U+007F, and all
   non-ASCII characters, is emitted **literally** as UTF-8. NFC is applied to string values and to
   object keys.
4. **Arrays** serialise as `[` element `,` element … `]` in the **original order** (arrays are never
   reordered), with no whitespace.
5. **Objects** serialise as `{` key `:` value `,` … `}` with no whitespace, where the members are
   **sorted by the UTF-16 code units of their NFC-normalised keys**. Sorting is by UTF-16 code unit,
   not Unicode code point: a character outside the Basic Multilingual Plane (encoded as a surrogate
   pair beginning U+D800–U+DBFF) sorts before a BMP character in U+E000–U+FFFF, the reverse of
   code-point order. Two keys that are distinct before normalisation but equal after it make the
   object ambiguous and are **refused** (RFC 8785 forbids duplicate members).

### Number grammar

The canonical form accepts a JSON number only when its token matches this grammar (the integer
production of RFC 8259 §6, with no fraction and no exponent):

```
canonical-number = [ "-" ] ( "0" / ( digit1-9 *DIGIT ) )
```

and its magnitude is at most `2^53 − 1` (`9007199254740991`). A token is decided as follows:

| Token | Decision | Canonical form / reason |
|---|---|---|
| `0`, `42`, `-42`, `9007199254740991`, `-9007199254740991` | accepted | the token itself |
| `-0` | accepted | `0` (an integer has no negative zero) |
| `1.0`, `-0.0`, `2.50` (a fraction, even a whole one) | refused | `non_integer_number` |
| `1e2`, `1E2`, `1e+30`, `5e-1` (an exponent, even a whole one) | refused | `non_integer_number` |
| `9007199254740992`, `-9007199254740992`, `10000000000000001`, any larger integer | refused | `integer_out_of_range` |

A conforming reader therefore keeps the token: it must not read `1.0`, `1e2`, or `-0.0` into a value
that has already lost the fraction or exponent, and must not read an integer above `2^53 − 1` into a
double that has already rounded it. The TypeScript engine reads evidence with a parser that keeps such
tokens (`parseJson`), because `JSON.parse` folds `1.0` to `1` and `10000000000000001` to `10000000000000000`;
the Python and Java parsers already distinguish an integer token from a float token.

### Field-level rules the model enforces (§6.7)

These constrain the events that reach canonicalisation; they are validated by the generated JSON
Schema, and the canonical form above assumes them:

- **Timestamps** are RFC 3339 UTC with exactly three fractional digits (milliseconds), e.g.
  `2026-07-14T09:21:07.412Z`. As canonical strings they follow the string rule unchanged.
- **Decimals** are strings with a declared scale, never JSON numbers, so that no precision is lost and
  no floating-point formatting is involved.
- **IRIs** are formed as the specification requires: `agentce:event/<id>` for events;
  `agentce:principal/<hmac-sha256(k, id)>` for principals under the per-subject pseudonymisation key of
  IR-12; domain IRIs from the binding. These are ordinary strings to the canonicaliser.

## Reference implementation and vectors

`canonical.py` implements the rules above (`canonicalize` → bytes, `canonical_string` → str,
`sha256_hex` → the integrity hash of a value). It uses only the standard library and contains no
learned component.

`test-vectors/*.json` holds the cross-language vectors, each self-describing:

```json
{ "id": "...", "description": "...", "input": <any JSON value>,
  "canonical": "<the canonical string>", "sha256": "<hex of the canonical UTF-8 bytes>" }
```

A vector file is read with the parser an engine reads evidence with, one that keeps a number token as
written: `input` may be `1.0`, `1e2`, `-0.0`, or an integer above `2^53 − 1`, and a reader that folded
it to a plain number first would accept what the profile refuses.

An **error vector** replaces `canonical`/`sha256` with `"error": "<reason>"`, one of the stable
reasons `non_integer_number`, `integer_out_of_range`, or `duplicate_key_after_nfc`. A conforming engine reproduces `canonical`
and `sha256` for every value vector and refuses every error vector with the stated reason. The vectors
cover the literals, integer boundaries (including the whole-number floats and the integers just
above `2^53 − 1` that JSON parsers fold), string escaping, Unicode NFC normalisation, UTF-16 key
ordering (including an astral-plane key), nested ordering, and two complete Appendix G events.
`test-vectors/test_vectors.py` runs the vectors in Python and adds hand-written assertions for the
tricky rules so the suite does not merely restate what the generator produced. Regenerate the vectors
with `python gen_vectors.py` after any change to the rules.
