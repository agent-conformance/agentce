---
title: agentce assess
description: run a full assessment
---

Run a full assessment.

```text
usage: agentce assess [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                      [--catalog CATALOG] [--profile PROFILE]
                      [--deviations DEVIATIONS] [--domain DOMAIN]
                      [--catalog-dir CATALOG_DIR] [--trust-root TRUST_ROOT]
                      [--allow-unverified-catalog] [--manual MANUAL]
                      [--probes PROBES] [--out OUT] [--state STATE]
                      [--report-language REPORT_LANGUAGE] [--emit EMIT]
                      [--fail-on FAIL_ON]
                      [folder]

Run a full assessment: `agentce assess <folder>` over a folder of trace
exports, or `agentce assess --bundle <dir> --profile <file>` over an evidence
bundle.

positional arguments:
  folder                a folder of OpenTelemetry GenAI or OpenInference trace
                        exports (.json, .jsonl, .ndjson): assess reads it and
                        writes a default profile, so no other flag is needed

options:
  -h, --help            show this help message and exit
  --bundle BUNDLE       the evidence bundle directory
  --catalog CATALOG     catalog ids, comma-separated: <id@ver>[,<id@ver>...]
                        (default: the profile's catalogs, else the baseline)
  --profile PROFILE     the applicability profile file
  --deviations DEVIATIONS
                        the deviation register file
  --domain DOMAIN       the domain ontology binding file
  --catalog-dir CATALOG_DIR
                        a catalog directory to evaluate (repeatable)
  --trust-root TRUST_ROOT
                        trust root every --catalog-dir signature is verified
                        against (default: AGENTCE_TRUST_ROOT, else the
                        vendored development root)
  --allow-unverified-catalog
                        assess a --catalog-dir catalog whose signature is
                        absent or does not verify, recording the override as a
                        limitation in the manifest and the claim (SPEC 8.7)
  --manual MANUAL       the manual-records directory
  --probes PROBES       the probe-results directory
  --out OUT             the output directory (default: ./out)
  --state STATE         the incremental state directory
  --report-language REPORT_LANGUAGE
                        message-key catalogue for the report; does not affect
                        assertions.json (SPEC 9.3)
  --emit EMIT           comma-separated report formats to render (default:
                        html, md, oscal, pack, sarif, skill); one or more of:
                        md, html, oscal, sarif, public, pack, junit, csv,
                        oscal_xml, pdf, remediation, skill
  --fail-on FAIL_ON     gate the exit code on a tiny deterministic expression
                        over assertion fields (control, subject, outcome,
                        severity, family, rung, mode), e.g. 'outcome=="non-
                        conformant" and severity=="high"' (comparisons joined
                        by and/or; never a general expression language).
                        Replaces the default any-non-conformant rule when
                        given.

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
