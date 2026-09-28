# `agentce verify`

Integrity or signature verification.

```text
usage: agentce verify [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                      [--catalog CATALOG] [--release RELEASE]
                      [--report REPORT]
                      [--signer-trust-root SIGNER_TRUST_ROOT]
                      [--expect-keyid EXPECT_KEYID]

options:
  -h, --help            show this help message and exit
  --bundle BUNDLE       verify an evidence bundle's integrity
  --catalog CATALOG     verify a catalog's signatures
  --release RELEASE     verify a release artifact's signatures
  --report REPORT       re-run a shareable report bundle (assess --package-
                        for-sharing) offline and check it reproduces byte for
                        byte, refusing any tampering (SPEC 18.8, Hill 3)
  --signer-trust-root SIGNER_TRUST_ROOT
                        verify --report's signature against this trust root
                        file, instead of an embedded trust-root.json inside
                        the report directory
  --expect-keyid EXPECT_KEYID
                        verify --report: refuse unless the claim signature's
                        keyid matches this value

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
