---
title: agentce diff
description: deterministic diff of two assertion sets
---

Deterministic diff of two assertion sets.

```text
usage: agentce diff [-h] [--json] [--debug] [--quiet] [report_a] [report_b]

positional arguments:
  report_a    the first assertions.json
  report_b    the second assertions.json

options:
  -h, --help  show this help message and exit

global options:
  --json      emit machine-readable JSON on stdout
  --debug     verbose logs on stderr and a stack trace on unexpected errors
  --quiet     log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
