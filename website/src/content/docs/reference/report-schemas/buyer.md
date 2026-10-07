---
title: AgentCE buyer view
description: 'buyer.json in the report directory. It answers "does this vendor''s agent meet what I asked?": one entry per (assertion, crosswalk citation) whose fram'
---

AgentCE buyer view.

buyer.json in the report directory. It answers "does this vendor's agent meet what I asked?": one entry per (assertion, crosswalk citation) whose framework is a generated questionnaire this run cites (CAIQ today; the AI Controls Matrix crosswalk is a disclosed placeholder until its objective list can be confirmed, so it adds no answers yet), grouped for reading by question in `by_question`. A question with zero mapped controls is simply absent, never a fabricated answer; "not enough evidence" names the concrete next step where one is known. This view adds no new fact and no new outcome; it is a different selection of the same assertions, and it is not itself a certification. AgentCE writes it only with `--for buyer`.

[View the schema](/spec/report/buyer.schema.json) (`https://agent-conformance.org/spec/report/buyer.schema.json`).

| Property | Type | Description |
|---|---|---|
| `answers` | array |  |
| `by_question` | object |  |
| `counts` | `counts` |  |
