# Controls

Every control across the EU AI Act base catalog and its overlays (SPEC §7.3), 60 in total, generated from the catalog YAML. One page per control, grouped here by catalog.

## Conduct overlay

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`CND-01`](cnd-01.md) | Every action stays within its declared task effect scope | high | automated | 2 |
| [`CND-02`](cnd-02.md) | High-effect actions are gated by a recorded approval | high | automated | 2 |
| [`CND-03`](cnd-03.md) | Harm signals are triaged into an incident or recorded decision | medium | semi-automated | 3 |
| [`CND-04`](cnd-04.md) | Consequential tool calls act on a recorded instruction | high | automated | 2 |
| [`CND-05`](cnd-05.md) | No action is taken on an untrusted instruction | high | automated | 2 |
| [`CND-06`](cnd-06.md) | Every untrusted instruction is refused | medium | automated | 2 |
| [`CND-07`](cnd-07.md) | Task and window resource budgets are not exceeded | medium | automated | 2 |
| [`CND-08`](cnd-08.md) | Adverse-outcome rates by declared group are within thresholds | high | semi-automated | 3 |

## Employment overlay

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`EMP-01`](emp-01.md) | Automated employment decisions carry a human-review record | high | manual | 3 |

## EU AI Act base catalog

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`DAT-01`](dat-01.md) | Consequential decisions record the data they consumed | medium | automated | 2 |
| [`DAT-02`](dat-02.md) | Input data quality and representativeness are measured | medium | semi-automated | 2 |
| [`DAT-03`](dat-03.md) | Outcome parity across declared groups is within thresholds | medium | semi-automated | 2 |
| [`DAT-04`](dat-04.md) | Data-governance measures are documented and reviewed | medium | manual | 3 |
| [`DOC-01`](doc-01.md) | Operating components match the declared documentation | high | automated | 2 |
| [`DOC-02`](doc-02.md) | Consequential decisions are documented with a time of record | medium | automated | 2 |
| [`DOC-03`](doc-03.md) | Declared egress paths match the observed enforcement point | medium | automated | 2 |
| [`DOC-04`](doc-04.md) | Configuration drift from the declared snapshot is bounded | medium | semi-automated | 2 |
| [`DOC-05`](doc-05.md) | Technical documentation is complete and current | medium | manual | 3 |
| [`INC-01`](inc-01.md) | Adverse outcomes are linked to their consequential decision | medium | automated | 2 |
| [`INC-02`](inc-02.md) | Serious incidents are recorded with an accountable actor (by role) | high | automated | 2 |
| [`INC-03`](inc-03.md) | Incident-triggering decisions carry an oversight review | medium | automated | 2 |
| [`INC-04`](inc-04.md) | Incident-linked records carry no dangling references | high | automated | 2 |
| [`INT-01`](int-01.md) | Consequential tool calls are captured by an enforcement point | high | automated | 2 |
| [`INT-02`](int-02.md) | Delegation chains for consequential actions are verified | high | automated | 2 |
| [`INT-03`](int-03.md) | Consequential tool-call records carry no dangling references | high | automated | 2 |
| [`INT-04`](int-04.md) | Consequential tool calls carry a corroborated source class | high | automated | 2 |
| [`OVS-01`](ovs-01.md) | Every consequential decision is reviewed by an oversight activity | medium | automated | 2 |
| [`OVS-02`](ovs-02.md) | Observed oversight modality matches the declared modality | high | automated | 2 |
| [`OVS-03`](ovs-03.md) | Consequential tool calls run under verified human oversight | high | automated | 2 |
| [`OVS-04`](ovs-04.md) | Consequential tool calls run over a verified delegation chain | high | automated | 2 |
| [`OVS-05`](ovs-05.md) | Consequential decisions record the authorisation that gated them | medium | automated | 2 |
| [`OVS-06`](ovs-06.md) | Consequential tool calls execute through an enforcement point | high | automated | 2 |
| [`OVS-07`](ovs-07.md) | Overrides and interrupts are effective and recorded | medium | semi-automated | 2 |
| [`OVS-08`](ovs-08.md) | Oversight coverage of consequential decisions is complete | medium | semi-automated | 2 |
| [`OVS-09`](ovs-09.md) | Human overseers are competent and able to intervene | medium | manual | 3 |
| [`REC-01`](rec-01.md) | Every consequential decision records the acting agent | high | automated | 2 |
| [`REC-02`](rec-02.md) | Every consequential decision is timestamped | medium | automated | 2 |
| [`REC-03`](rec-03.md) | Every consequential decision records the inputs it used | medium | automated | 2 |
| [`REC-04`](rec-04.md) | Consequential decisions are recorded with actor and time | medium | automated | 2 |
| [`REC-05`](rec-05.md) | Every consequential decision records the authority it acted under | medium | automated | 2 |
| [`REC-06`](rec-06.md) | Consequential tool calls carry no dangling evidence references | high | automated | 2 |
| [`ROB-01`](rob-01.md) | Consequential actions traverse a trusted, corroborated source | high | automated | 2 |
| [`ROB-02`](rob-02.md) | No untrusted content influences consequential decisions | medium | automated | 2 |
| [`ROB-03`](rob-03.md) | Consequential tool calls carry no dangling references | medium | automated | 2 |
| [`ROB-04`](rob-04.md) | Consequential tool calls run over a verified delegation chain | high | automated | 2 |
| [`ROB-05`](rob-05.md) | Adversarial-probe resilience is measured within thresholds | medium | semi-automated | 2 |
| [`ROB-06`](rob-06.md) | Resource-exhaustion resistance is measured within budgets | medium | semi-automated | 2 |
| [`ROB-07`](rob-07.md) | Cyber-incident detection and response are exercised | medium | semi-automated | 2 |
| [`RSK-01`](rsk-01.md) | Consequential decisions record the authorisation gate they cleared | medium | automated | 2 |
| [`RSK-02`](rsk-02.md) | Consequential decisions are subject to a risk-review activity | medium | automated | 2 |
| [`RSK-03`](rsk-03.md) | Residual-risk indicators are measured within thresholds | medium | semi-automated | 2 |
| [`RSK-04`](rsk-04.md) | A fundamental-rights impact assessment is recorded | medium | manual | 3 |
| [`RSK-05`](rsk-05.md) | Registration and applicability declarations are current | medium | manual | 3 |
| [`TRN-01`](trn-01.md) | Affected persons are notified for every consequential decision | medium | automated | 2 |
| [`TRN-02`](trn-02.md) | Every consequential decision records the agent that made it | high | automated | 2 |
| [`TRN-03`](trn-03.md) | Affected persons informed and explanation reconstructable | medium | semi-automated | 2 |
| [`TRN-04`](trn-04.md) | Consequential decisions are timestamped for explanation | medium | automated | 2 |
| [`TRN-05`](trn-05.md) | AI-interaction disclosure is present where required | medium | semi-automated | 2 |

## Finance overlay

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`FIN-01`](fin-01.md) | Creditworthiness models meet the declared suitability bounds | high | manual | 3 |

## Insurance overlay

| Control | Title | Severity | Mode | Rung |
|---|---|---|---|---|
| [`INS-01`](ins-01.md) | Adverse-action notices are issued within the sector window | high | manual | 3 |
