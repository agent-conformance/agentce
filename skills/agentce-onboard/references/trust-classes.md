# Trust classes, honestly

The single most consequential judgement in onboarding is the **trust class** of each evidence source
(SPEC §6.4). Getting it wrong — usually by flattering an agent's self-report into an enforcement point
— is the failure this skill guards against hardest (rule S-2).

| Class | Meaning | Examples |
|---|---|---|
| `self_report` | Emitted by the agent runtime about itself. It could be wrong or omitted and nothing would stop the action. | Framework spans, SDK tracing processors, in-process approval prompts, agent-written log files. |
| `enforcement_point` | Emitted by a system that **could have prevented** the action. | A gateway on the only egress path; a policy engine evaluated server-side; an admission controller. |
| `independent_system` | Emitted by a system whose state **no principal in the agent's delegation chain can transition**. | A ticketing/approval system of record; a registry. |

**Rules of thumb.**

- Agent-side emission is *always* `self_report`, no matter how trustworthy the framework feels. If the
  agent's own code emits it, it is a self-report.
- A source is only an `enforcement_point` for the calls it actually sits in front of. A gateway that
  proxies tool calls is an enforcement point for `ToolCall`, not for the model's reasoning.
- `independent_system` requires that the agent cannot change the record's state — not merely that a
  different service wrote it. State the reason in `class_justification`; an assessor will challenge it.

`lint_profile` refuses a source whose `kind` is a known self-report pattern (`framework_interrupt`,
`sdk_tracing`, `in_process_approval`, `agent_log`) declared as anything but `self_report`. When no
enforcement point exists, proceed with `self_report` and say so prominently — an honest `self_report`
with a documented gap is conformant practice; a mislabelled one is not.
