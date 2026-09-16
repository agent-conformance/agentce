# `agentce catalog`

Catalog tools.

```text
usage: agentce catalog [-h] [--json] [--debug] [--quiet] <action> ...

positional arguments:
  <action>
    lint             validate controls, shapes, and test cases
    coverage-matrix  regenerate the automation coverage matrix (SPEC 7.5)

options:
  -h, --help         show this help message and exit

global options:
  --json             emit machine-readable JSON on stdout
  --debug            verbose logs on stderr and a stack trace on unexpected
                     errors
  --quiet            log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
