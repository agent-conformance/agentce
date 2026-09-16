# Security process

How a vulnerability is reported, handled, and disclosed (SPEC §14.2). The repository root
[`SECURITY.md`](../SECURITY.md) is the entry point for reporters; this document records the process the
maintainers follow.

## Reporting

- Report privately to `security@agent-conformance.org`. Do **not** open a public issue for a suspected
  vulnerability.
- Include the affected component and version, a description, and a reproduction if you have one.

## Response

| Step | Commitment |
|---|---|
| Acknowledge the report | Within 3 business days. |
| Fix a critical issue | Within 30 days of acknowledgement. |
| Fix any other issue | Within 90 days. |
| Request a CVE | For every fix, so downstream users can track it. |

## Disclosure

- A fix is released through the trusted-publishing workflow; no maintainer holds a long-lived registry
  token.
- An **OpenVEX** statement is published with every release, so an adopter's scanner can triage findings
  against AgentCE without opening a support ticket.
- The release is signed and verifiable offline against the vendored trust roots (SPEC §8.7); see
  [`docs/verification.md`](../docs/verification.md).

## Scope

The evaluation path is deterministic and read-only over its inputs, and no engine or script uses a
learned component (SPEC §8.7, HR-1/HR-2); the `no_ml` dependency-denylist job enforces it. Threats to
the evidence and the assessment itself are catalogued in [`docs/threat-model.md`](../docs/threat-model.md).
