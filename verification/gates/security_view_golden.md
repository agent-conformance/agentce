# AgentCE security view

AgentCE doesn't block anything. It reads what your guardrails recorded and shows where they could have stopped an action and where nothing could have.

## Tool access

- ‹script›alert(1) (, )

## Actions by effect class

- irreversible 1

## Enforcement-point evidence

- This answers whether anything could have stopped this run's actions at the whole-run level: the count of irreversible actions next to the count of approvals and denials this run recorded. It does not link one action to the check that covered it.
- Approvals recorded by: 0
- Denied or blocked: blocked by an authorization check 1

## Drift

- Tools: ‹script›alert(1)
- Models: security-view-fixture-model

## Standards citations

- `INT-01`: mitre-atlas 2026.09 AML.T0101 (clause reference unverified)
- `INT-01`: mitre-atlas 2026.09 AML.T0108 (clause reference unverified)
- `INT-01`: owasp-asi-2026 2025.12 T13 (clause reference unverified)
- `INT-01`: owasp-asi-2026 2025.12 T8 (clause reference unverified)
- `OVS-03`: mitre-atlas 2026.09 AML.T0101 (clause reference unverified)
- `OVS-03`: owasp-asi-2026 2025.12 T10 (clause reference unverified)
- `OVS-03`: owasp-asi-2026 2025.12 T7 (clause reference unverified)
- `REC-01`: owasp-acs 0.1.0 Hook_SubagentStart+Hook_SubagentStop (clause reference unverified)
- `REC-01`: owasp-asi-2026 2025.12 T8 (clause reference unverified)
- `REC-04`: mitre-atlas 2026.09 AML.T0073 (clause reference unverified)
- `REC-04`: mitre-atlas 2026.09 AML.T0074 (clause reference unverified)
- `REC-04`: owasp-asi-2026 2025.12 T6 (clause reference unverified)
- `REC-04`: owasp-asi-2026 2025.12 T8 (clause reference unverified)
- `ROB-02`: mitre-atlas 2026.09 AML.T0051 (clause reference unverified)
- `ROB-02`: mitre-atlas 2026.09 AML.T0051.001 (clause reference unverified)
- `ROB-02`: mitre-atlas 2026.09 AML.T0080 (clause reference unverified)
- `ROB-02`: mitre-atlas 2026.09 AML.T0080.000 (clause reference unverified)
- `ROB-02`: owasp-asi-2026 2025.12 T1 (clause reference unverified)
- `ROB-02`: owasp-asi-2026 2025.12 T11 (clause reference unverified)

