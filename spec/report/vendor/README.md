# Vendored third-party schemas

Unmodified, byte-for-byte copies of schemas AgentCE validates against but does not author. Each is
mirrored into every engine's `data/schemas/` and checked for drift by that engine's test suite
(the repository's vendoring-with-sync-test pattern).

| File | Source | Retrieved | SHA-256 |
|---|---|---|---|
| `oscal-assessment-results-nist-1.1.2.schema.json` | `https://github.com/usnistgov/OSCAL/releases/download/v1.1.2/oscal_assessment-results_schema.json` | 2026-09-23 | `d033da70154cf6625ae46a746199e88e58f2928b1387dfac051d381b92f41b0d` |

## Re-vendoring

To refresh a file after a new upstream release, re-run its retrieval command, update the table above
with the new date and digest, and re-sync every engine's mirror:

```
curl -sSL -o spec/report/vendor/oscal-assessment-results-nist-1.1.2.schema.json \
  https://github.com/usnistgov/OSCAL/releases/download/v1.1.2/oscal_assessment-results_schema.json
rsync -a spec/report/vendor/oscal-assessment-results-nist-1.1.2.schema.json \
  engines/python/agentce/data/schemas/
```

This file is JSON Schema draft-07 (`$schema: http://json-schema.org/draft-07/schema#`), not the
2020-12 dialect the rest of `spec/report/` uses; validate it with a draft-07 validator. Its
`TokenDatatype` definition uses a Unicode property escape (`\p{L}`, `\p{N}`) that Python's standard
`re` module cannot compile; engines validate against it with the third-party `regex` module instead
(see `agentce.report._oscal_ar_validator`).
