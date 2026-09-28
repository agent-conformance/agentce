---
title: agentce sign
description: sign a report as claimant or assessor
---

Sign a report as claimant or assessor.

```text
usage: agentce sign [-h] [--json] [--debug] [--quiet]
                    [--as {claimant,assessor}]
                    [--profile {sigstore-public,sigstore-private,kms}]
                    [--key KEY] [--dry-run] [--write-trust-root]
                    [report_dir]

positional arguments:
  report_dir            the report directory

options:
  -h, --help            show this help message and exit
  --as {claimant,assessor}
                        the signing role
  --profile {sigstore-public,sigstore-private,kms}
                        the signing profile
  --key KEY             operator Ed25519 private key (PEM) for the kms profile
  --dry-run             plan only; sign nothing
  --write-trust-root    write trust-root.json (the signer's public key) into
                        the report directory, so a recipient can `agentce
                        verify --report` this bundle without any other key
                        exchange (SPEC 18.8, Hill 3). Requires --profile kms
                        (the only profile with an exportable key).

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
