# credit / claude-agent-sdk / known-pass

A simulated **Claude Agent SDK coding-agent session** making consequential credit decisions (SPEC §11.2–11.3). Assessed subject: `spiffe://corp/agents/credit-claude-agent-sdk`.

Variant **known-pass**: full enforcement-point evidence, every declared measure observed.

## Ground truth (control → expected outcome → why)

| Control | Expected | Rationale / seeded fault |
|---|---|---|
| INC-02 | conformant | declared measure observed |
| INT-01 | conformant | declared measure observed |
| OVS-03 | conformant | declared measure observed |
| REC-04 | conformant | declared measure observed |

Outcomes are authored to the reference engine and proven by the corpus test suite; `expected/outcomes.json` is the machine-readable form (SPEC §11.3).
