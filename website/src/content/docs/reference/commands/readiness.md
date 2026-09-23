---
title: agentce readiness
description: compute the report-readiness verdict (SPEC 13.3.4)
---

Compute the report-readiness verdict (SPEC 13.3.4).

```text
usage: agentce readiness [-h] [--json] [--debug] [--quiet] [--gaps GAPS]
                         [--deviations DEVIATIONS] [--catalog-dir CATALOG_DIR]
                         [report_dir]

positional arguments:
  report_dir            the report directory

options:
  -h, --help            show this help message and exit
  --gaps GAPS           a gaps file listing accepted high-severity evidence
                        gaps
  --deviations DEVIATIONS
                        a deviation register to validate
  --catalog-dir CATALOG_DIR
                        a catalog directory whose control severities the
                        verdict reads (repeatable)

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
