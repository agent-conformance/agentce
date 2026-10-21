# `agentce readiness`

Compute the report-readiness verdict (SPEC 13.3.4).

```text
usage: agentce readiness [-h] [--json] [--debug] [--quiet] [--gaps GAPS]
                         [--deviations DEVIATIONS] [--catalog-dir CATALOG_DIR]
                         [--trust-root TRUST_ROOT]
                         [--allow-unverified-catalog]
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
                        verdict reads (repeatable); verified as assess
                        verifies it
  --trust-root TRUST_ROOT
                        trust root every --catalog-dir signature is verified
                        against (default: AGENTCE_TRUST_ROOT, else the
                        vendored development root)
  --allow-unverified-catalog
                        read a --catalog-dir catalog whose signature is absent
                        or does not verify, recording the override as a
                        limitation in the verdict (SPEC 8.7). Requires
                        --catalog-dir.

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
