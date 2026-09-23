---
title: agentce doctor
description: diagnose a project and name the exact fix (SPEC 13.4)
---

Diagnose a project and name the exact fix (SPEC 13.4).

```text
usage: agentce doctor [-h] [--json] [--debug] [--quiet] [--project PROJECT]
                      [--write-errors WRITE_ERRORS]

options:
  -h, --help            show this help message and exit
  --project PROJECT     the project directory to diagnose (default: the
                        current directory)
  --write-errors WRITE_ERRORS
                        regenerate the message-key catalogue at this path
                        instead of diagnosing

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
