# Portable Shape Profile (PSP)

Normative for rung-2 structural evaluation. Implements the specification, §7.2 ("Expression
languages"), and the shape in Appendix B. This document defines the profile; `psp_check.py` enforces
it; `control.schema.json` points a control's `evaluation.shape` at a file written within it.

## Why a profile

A rung-2 outcome is a structural fact about the evidence graph: a shape either holds over the
materialised graph or it does not. For that outcome to be reproducible across independent engines —
one using a SHACL library, another compiling shapes to query plans — every engine must agree on
exactly which SHACL constructs a shape may use and what each one means. The Portable Shape Profile is
that agreement: a strict subset of SHACL Core [R22] with no construct whose evaluation is
implementation-defined, unbounded, or dependent on a query or scripting engine. A control may not
depend on a construct one engine evaluates and another silently ignores.

## The profile

A shape file is **within the profile** when every shape in it uses only the following.

### Targets

- `sh:targetClass` — the shape applies to every node of the class (membership per the class-hierarchy
  rule below).
- `sh:targetNode` — the shape applies to a named node.
- `agentce:targetWhere` — a conjunction of property-value equalities, resolved by the engine to a set
  of focus nodes *before* validation. Its object is a node whose properties are simple value
  equalities (predicate to literal or IRI); it carries no nested shape, list, or further structure.

No other target mechanism is permitted (`sh:targetSubjectsOf` and `sh:targetObjectsOf` are excluded).

### Property paths

- a predicate (a single IRI),
- an inverse of a permitted path (`sh:inversePath`),
- a sequence of at most three permitted paths (an RDF list as the value of `sh:path`),
- an alternative of at most three permitted paths (`sh:alternativePath`).

No zero-or-more, one-or-more, or zero-or-one paths. Unbounded traversal is replaced by an
engine-materialised edge (for example `agentce:chainTerminus` stands in for walking a delegation
chain to its end), so that path evaluation is always bounded and identical across engines.

### Constraints

`sh:minCount`, `sh:maxCount`, `sh:class`, `sh:datatype`, `sh:nodeKind`, `sh:in`, `sh:hasValue`,
`sh:node` (a nested shape, itself within the profile), `sh:qualifiedValueShape` with
`sh:qualifiedMinCount`, `sh:equals`, `sh:disjoint`, `sh:lessThan`, `sh:lessThanOrEquals`, and
`sh:minInclusive` / `sh:maxInclusive` on `xsd:integer` and `xsd:dateTime` only.

### Literal ordering

`sh:minInclusive`, `sh:maxInclusive`, `sh:lessThan`, and `sh:lessThanOrEquals` order two literals by
their lexical form, exactly, and every engine gives the same answer. Two forms are comparable and no
others:

- **Integer.** `[+-]?[0-9]+` (ASCII digits only, no whitespace, no fraction, no exponent). Integers
  compare as exact integers of any size; leading zeros and a `+` sign do not change the value, and
  `-0` equals `0`.
- **Date-time.** `YYYY-MM-DD"T"hh:mm:ss` with an optional fraction of one or more digits (`.` then
  digits) and an optional zone (`Z` or `±hh:mm`). Upper-case `T` and `Z` only. The date must exist in
  the proleptic Gregorian calendar (a leap day only in a leap year), `hh` is `00`–`23`, `mm` and `ss`
  are `00`–`59` (no leap second, no `24:00:00`), and a zone offset is at most `±23:59`. An aware
  date-time (one with a zone) compares as an instant, to any fractional precision, so `…T00:00:00Z`
  equals `…T01:00:00+01:00` and `.5` equals `.500`. A naive date-time (no zone) compares with another
  naive date-time by its written fields.

Two literals are **incomparable** when either is outside both forms, when one is an integer and the
other a date-time, or when one date-time is aware and the other naive. An incomparable pair never
satisfies a constraint: it is a violation, not an error. `sh:lessThan` is strict — it is satisfied only
when the first literal orders strictly before the second, never merely because the two are written
differently.

No comparison goes through floating point or a runtime date type. `numerics-vectors/cases/
edge-comparison.json` is the shared golden (`literal_order`: `lt`, `eq`, `gt`, or `incomparable`);
every engine reproduces it, and `conformance/numerics.py --engines python,typescript,java` proves they
agree.

`sh:pattern` is permitted only as an anchored literal prefix: the value begins with `^` and the
remainder contains no regular-expression metacharacters (`. ^ $ * + ? ( ) [ ] { } | \`) and no
`sh:flags`. Anything richer is refused, because regular-expression engines differ.

### Annotations

`sh:name`, `sh:message`, `sh:description`, `sh:order`, `sh:group`, and `sh:severity` are permitted;
they never affect an outcome. Violation messages in a report come from the control file's message
keys, never from the validator (§7.2, result mapping).

### Excluded (non-exhaustive)

`sh:sparql`, `sh:js` and their helpers (`sh:select`, `sh:ask`, `sh:prefixes`, `sh:jsLibrary`, …),
`sh:closed` and `sh:ignoredProperties`, the logical combinators `sh:and`, `sh:or`, `sh:not`,
`sh:xone`, the unbounded path operators, `sh:languageIn`, `sh:uniqueLang`, `sh:qualifiedMaxCount`,
and any custom constraint component. Any SHACL term that is not listed as permitted above is refused.

### Class-membership semantics

Class membership — `sh:class`, `sh:targetClass`, and a `subClassOf` test in `agentce:targetWhere` — is
evaluated over `rdf:type` / `rdfs:subClassOf*` with the domain binding's class hierarchy loaded into
the data graph and **no other inference** (no RDFS domain/range, no OWL). An engine that does not run
a SHACL library MUST materialise the transitive closure of the class hierarchy before evaluation. The
Engine Conformance Suite includes hierarchy-depth fixtures.

### Result mapping

Each `sh:ValidationResult` becomes one violation record `{focus_node, path, constraint,
message_key}`. The message text is taken from the control file, never from the validator, so that a
report is deterministic and translatable.

## The checker: `psp_check.py`

`psp_check.py` decides membership. It is a structural check over the shapes graph; it validates no
data, runs no query, and contains no learned component.

```
psp_check.py <shape.ttl> [<shape.ttl> ...]
```

| Result | Output | Exit |
|---|---|---|
| every file is within the profile | `PSP OK` | 0 |
| a shape uses an excluded construct | `REFUSED: <feature>` | 1 |
| a file will not parse | `ERROR: <detail>` | 2 |

The refusal names the first excluded feature found, in a fixed priority order, so the message does
not depend on triple ordering; a SPARQL constraint is always reported as `REFUSED: sh:sparql`. Every
shape a shipped catalog carries passes `psp_check`; the check runs in the catalog build and in the
conformance evaluation. A construct that a real control needs but the profile excludes is a change to
the profile, proposed through the specification's RFC process — never a local exception in one engine.
