# `agentce verify`

Integrity or signature verification.

```text
usage: agentce verify [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                      [--catalog CATALOG] [--release RELEASE]
                      [--report REPORT]
                      [--signer-trust-root SIGNER_TRUST_ROOT]
                      [--expect-keyid EXPECT_KEYID]

Integrity or signature verification. `--report <dir>` re-runs a shareable
report bundle (`assess --package-for-sharing`) offline through nine stages, in
order, each with its own message key: (1) the claim exists and is signed
(verify.report_no_claim, verify.report_claim_malformed,
verify.report_unsigned); (2) the trust root resolves
(verify.report_no_trust_root, input.trust_root_invalid,
verify.report_keyid_mismatch); (3) a claimant signature verifies
(verify.report_signature_invalid); (4) the signed subjects match what's on
disk (verify.report_subject_missing, verify.report_manifest_tampered,
verify.report_claim_tampered); (5) the engine build matches
(verify.report_engine_mismatch); (6) every manifest-tracked output's digest
matches (verify.report_output_tampered); (7) the packaged evidence, profile,
domain binding, and catalogs match (verify.report_evidence_tampered); (8) an
offline re-run reproduces every canonical output byte for byte
(verify.report_reproduction_mismatch); (9) success (`reproduced: true`).

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
