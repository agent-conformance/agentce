---
title: agentce verify
description: integrity or signature verification
---

Integrity or signature verification.

```text
usage: agentce verify [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                      [--catalog CATALOG] [--release RELEASE]

options:
  -h, --help         show this help message and exit
  --bundle BUNDLE    verify an evidence bundle's integrity
  --catalog CATALOG  verify a catalog's signatures
  --release RELEASE  verify a release artifact's signatures

global options:
  --json             emit machine-readable JSON on stdout
  --debug            verbose logs on stderr and a stack trace on unexpected
                     errors
  --quiet            log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
