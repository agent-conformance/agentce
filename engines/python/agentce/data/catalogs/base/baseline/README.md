# Baseline catalog

Implements the specification §7.3 (catalog structure and crosswalk hygiene). The checks the standards
share, built as a profile of existing standards: it is the lens an assessment uses when nothing names a
catalog, and each standard-specific catalog stays a selectable lens (`agentce assess --catalog <id@version>`).

- **Two standards per control.** `catalog.yaml` sets `min_crosswalk_frameworks: 2`; `agentce catalog lint`
  fails any control that cites fewer than two distinct standards.
- **No requirement of its own.** Every citation is a clause the standards crosswalk files
  (`../eu-ai-act/crosswalk/`) or the standard-specific catalogs already cite for that control.
  Requirement-area mappings (AIUC-1) and draft standards (prEN 18229-1, ISO/IEC 24970) are not cited until
  they map clauses.
- **Shared detection.** Each control reuses the shape and test fixtures of the standard-specific catalogs
  verbatim; only the citations differ.
- **Unreviewed references.** Every citation carries `verified_against_text: false` until a reviewer with a
  licensed copy of the standard confirms it; clause identifiers only, never the standard's text.

The catalog is vendored, byte for byte, into each engine's bundle and signed with the repository's
development catalog-signing key like the others. The build gate `VG-BASELINE-LENS` holds all of it.
