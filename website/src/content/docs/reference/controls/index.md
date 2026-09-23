---
title: Controls
description: Every control across the EU AI Act base catalog and its overlays, 60 in total.
---

Every control across the EU AI Act base catalog and its overlays (SPEC §7.3), 60 in total, generated from the catalog YAML. One page per control, grouped here by catalog.

## Conduct overlay

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`CND-01`](/reference/controls/cnd-01/) | Every action stays within its declared task effect scope | high | automated | 2 |
| [`CND-02`](/reference/controls/cnd-02/) | High-effect actions are gated by a recorded approval | high | automated | 2 |
| [`CND-03`](/reference/controls/cnd-03/) | Harm signals are triaged into an incident or recorded decision | medium | semi-automated | 3 |
| [`CND-04`](/reference/controls/cnd-04/) | Consequential tool calls act on a recorded instruction | high | automated | 2 |
| [`CND-05`](/reference/controls/cnd-05/) | No action is taken on an untrusted instruction | high | automated | 2 |
| [`CND-06`](/reference/controls/cnd-06/) | Every untrusted instruction is refused | medium | automated | 2 |
| [`CND-07`](/reference/controls/cnd-07/) | Task and window resource budgets are not exceeded | medium | automated | 2 |
| [`CND-08`](/reference/controls/cnd-08/) | Adverse-outcome rates by declared group are within thresholds | high | semi-automated | 3 |

## Employment overlay

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`EMP-01`](/reference/controls/emp-01/) | Automated employment decisions carry a human-review record | high | manual | 3 |

## EU AI Act base catalog

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`DAT-01`](/reference/controls/dat-01/) | Consequential decisions record the data they consumed | medium | automated | 2 |
| [`DAT-02`](/reference/controls/dat-02/) | Input data quality and representativeness are measured | medium | semi-automated | 2 |
| [`DAT-03`](/reference/controls/dat-03/) | Outcome parity across declared groups is within thresholds | medium | semi-automated | 2 |
| [`DAT-04`](/reference/controls/dat-04/) | Data-governance measures are documented and reviewed | medium | manual | 3 |
| [`DOC-01`](/reference/controls/doc-01/) | Operating components match the declared documentation | high | automated | 2 |
| [`DOC-02`](/reference/controls/doc-02/) | Consequential decisions are documented with a time of record | medium | automated | 2 |
| [`DOC-03`](/reference/controls/doc-03/) | Declared egress paths match the observed enforcement point | medium | automated | 2 |
| [`DOC-04`](/reference/controls/doc-04/) | Configuration drift from the declared snapshot is bounded | medium | semi-automated | 2 |
| [`DOC-05`](/reference/controls/doc-05/) | Technical documentation is complete and current | medium | manual | 3 |
| [`INC-01`](/reference/controls/inc-01/) | Adverse outcomes are linked to their consequential decision | medium | automated | 2 |
| [`INC-02`](/reference/controls/inc-02/) | Serious incidents are recorded with an accountable actor (by role) | high | automated | 2 |
| [`INC-03`](/reference/controls/inc-03/) | Incident-triggering decisions carry an oversight review | medium | automated | 2 |
| [`INC-04`](/reference/controls/inc-04/) | Incident-linked records carry no dangling references | high | automated | 2 |
| [`INT-01`](/reference/controls/int-01/) | Consequential tool calls are captured by an enforcement point | high | automated | 2 |
| [`INT-02`](/reference/controls/int-02/) | Delegation chains for consequential actions are verified | high | automated | 2 |
| [`INT-03`](/reference/controls/int-03/) | Consequential tool-call records carry no dangling references | high | automated | 2 |
| [`INT-04`](/reference/controls/int-04/) | Consequential tool calls carry a corroborated source class | high | automated | 2 |
| [`OVS-01`](/reference/controls/ovs-01/) | Every consequential decision is reviewed by an oversight activity | medium | automated | 2 |
| [`OVS-02`](/reference/controls/ovs-02/) | Observed oversight modality matches the declared modality | high | automated | 2 |
| [`OVS-03`](/reference/controls/ovs-03/) | Consequential tool calls run under verified human oversight | high | automated | 2 |
| [`OVS-04`](/reference/controls/ovs-04/) | Consequential tool calls run over a verified delegation chain | high | automated | 2 |
| [`OVS-05`](/reference/controls/ovs-05/) | Consequential decisions record the authorisation that gated them | medium | automated | 2 |
| [`OVS-06`](/reference/controls/ovs-06/) | Consequential tool calls execute through an enforcement point | high | automated | 2 |
| [`OVS-07`](/reference/controls/ovs-07/) | Overrides and interrupts are effective and recorded | medium | semi-automated | 2 |
| [`OVS-08`](/reference/controls/ovs-08/) | Oversight coverage of consequential decisions is complete | medium | semi-automated | 2 |
| [`OVS-09`](/reference/controls/ovs-09/) | Human overseers are competent and able to intervene | medium | manual | 3 |
| [`REC-01`](/reference/controls/rec-01/) | Every consequential decision records the acting agent | high | automated | 2 |
| [`REC-02`](/reference/controls/rec-02/) | Every consequential decision is timestamped | medium | automated | 2 |
| [`REC-03`](/reference/controls/rec-03/) | Every consequential decision records the inputs it used | medium | automated | 2 |
| [`REC-04`](/reference/controls/rec-04/) | Consequential decisions are recorded with actor and time | medium | automated | 2 |
| [`REC-05`](/reference/controls/rec-05/) | Every consequential decision records the authority it acted under | medium | automated | 2 |
| [`REC-06`](/reference/controls/rec-06/) | Consequential tool calls carry no dangling evidence references | high | automated | 2 |
| [`ROB-01`](/reference/controls/rob-01/) | Consequential actions traverse a trusted, corroborated source | high | automated | 2 |
| [`ROB-02`](/reference/controls/rob-02/) | No untrusted content influences consequential decisions | medium | automated | 2 |
| [`ROB-03`](/reference/controls/rob-03/) | Consequential tool calls carry no dangling references | medium | automated | 2 |
| [`ROB-04`](/reference/controls/rob-04/) | Consequential tool calls run over a verified delegation chain | high | automated | 2 |
| [`ROB-05`](/reference/controls/rob-05/) | Adversarial-probe resilience is measured within thresholds | medium | semi-automated | 2 |
| [`ROB-06`](/reference/controls/rob-06/) | Resource-exhaustion resistance is measured within budgets | medium | semi-automated | 2 |
| [`ROB-07`](/reference/controls/rob-07/) | Cyber-incident detection and response are exercised | medium | semi-automated | 2 |
| [`RSK-01`](/reference/controls/rsk-01/) | Consequential decisions record the authorisation gate they cleared | medium | automated | 2 |
| [`RSK-02`](/reference/controls/rsk-02/) | Consequential decisions are subject to a risk-review activity | medium | automated | 2 |
| [`RSK-03`](/reference/controls/rsk-03/) | Residual-risk indicators are measured within thresholds | medium | semi-automated | 2 |
| [`RSK-04`](/reference/controls/rsk-04/) | A fundamental-rights impact assessment is recorded | medium | manual | 3 |
| [`RSK-05`](/reference/controls/rsk-05/) | Registration and applicability declarations are current | medium | manual | 3 |
| [`TRN-01`](/reference/controls/trn-01/) | Affected persons are notified for every consequential decision | medium | automated | 2 |
| [`TRN-02`](/reference/controls/trn-02/) | Every consequential decision records the agent that made it | high | automated | 2 |
| [`TRN-03`](/reference/controls/trn-03/) | Affected persons informed and explanation reconstructable | medium | semi-automated | 2 |
| [`TRN-04`](/reference/controls/trn-04/) | Consequential decisions are timestamped for explanation | medium | automated | 2 |
| [`TRN-05`](/reference/controls/trn-05/) | AI-interaction disclosure is present where required | medium | semi-automated | 2 |

## Finance overlay

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`FIN-01`](/reference/controls/fin-01/) | Creditworthiness models meet the declared suitability bounds | high | manual | 3 |

## Insurance overlay

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`INS-01`](/reference/controls/ins-01/) | Adverse-action notices are issued within the sector window | high | manual | 3 |
