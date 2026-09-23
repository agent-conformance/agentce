---
title: AgentCE remediation package
description: 'remediation-package.json (SPEC §7; Appendix A2): a pure deterministic join, for one subject, of each non-passing control''s catalog data (title, severi'
---

AgentCE remediation package.

remediation-package.json (SPEC §7; Appendix A2): a pure deterministic join, for one subject, of each non-passing control's catalog data (title, severity, crosswalk with its verified flag, expectation text, remediation techniques, minimum evidence) with that control's real assertion outcome (evidence, violations, evidence gap). No model call; RFC 8785 canonical; byte-identical across independent runs of the same assertions.

[View the schema](/spec/report/remediation-package.schema.json) (`https://agent-conformance.org/spec/report/remediation-package.schema.json`).

| Property | Type | Description |
|---|---|---|
| `$schema` | string |  |
| `$id` | string |  |
| `package_version` | integer |  |
| `generated_from` | object |  |
| `subject` | string |  |
| `findings` | array |  |
| `not_assessed` | array |  |
| `deviations_applied` | array |  |
