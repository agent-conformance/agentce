# Golden outputs

Where reference engine outputs (`assertions.json`, `coverage.json`, the applicability statement, and
canonicalised OSCAL) land when regenerated locally. Golden outputs are produced by the reference
engine and are **not committed** — they are too large and too frequently regenerated for git, and are
hosted as versioned datasets keyed by corpus revision × catalog version (SPEC §11.7). The Engine
Conformance Suite (item 1.11) writes and compares them.
