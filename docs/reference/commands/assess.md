# `agentce assess`

Run a full assessment.

```text
usage: agentce assess [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                      [--catalog CATALOG] [--profile PROFILE]
                      [--deviations DEVIATIONS] [--domain DOMAIN]
                      [--catalog-dir CATALOG_DIR] [--manual MANUAL]
                      [--probes PROBES] [--out OUT] [--state STATE]
                      [--report-language REPORT_LANGUAGE]

options:
  -h, --help            show this help message and exit
  --bundle BUNDLE       the evidence bundle directory
  --catalog CATALOG     catalog ids, comma-separated: <id@ver>[,<id@ver>...]
                        (default: the profile's catalogs)
  --profile PROFILE     the applicability profile file
  --deviations DEVIATIONS
                        the deviation register file
  --domain DOMAIN       the domain ontology binding file
  --catalog-dir CATALOG_DIR
                        a catalog directory to evaluate (repeatable)
  --manual MANUAL       the manual-records directory
  --probes PROBES       the probe-results directory
  --out OUT             the output directory
  --state STATE         the incremental state directory
  --report-language REPORT_LANGUAGE
                        message-key catalogue for the report; does not affect
                        assertions.json (SPEC 9.3)

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
