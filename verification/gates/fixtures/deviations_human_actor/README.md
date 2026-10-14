# deviations_human_actor

Fixtures for VG-DEVIATIONS-PARITY scenarios S24, S25 and S26 (SPEC §10.4: the deviation register cannot waive the
human-actor rule).

- `approval/`: an ApprovalDecided for decision `d1` whose actor names no `session_ref`, so it fails the human-actor
  rule. The register names CND-02, OVS-01, OVS-08 and RSK-02, which read that approval and stay non-conformant with a
  not-applied limitation each, and OVS-05 (the decision's missing authorization), which the register still waives.
- `override/`: an Override made in the agent's own session. The register names OVS-07, which stays non-conformant.
- `thirdparty/`: a third-party catalog run over `approval/`'s records (S26). TPX-01 and TPX-04 target the ToolCall and
  reach the decision's approval through a sequence path (`agentce:executes` then `agentce:approvedBy` or
  `agentce:reviewedBy`), TPX-02 reads the decision through an alternative path, and all three stay non-conformant.
  TPX-03 reads `agentce:approvedByProxy`, which the rule does not decide, so the register waives it.
