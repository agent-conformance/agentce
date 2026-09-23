---
title: DecisionPayload
description: The `DecisionPayload` evidence-model class.
---

Extends `Payload`.

| Attribute | Range | Required | Description |
|---|---|---|---|
| `decision_id` | string |  |  |
| `decision_type` | string |  |  |
| `affects_natural_person` | boolean |  |  |
| `legal_or_significant_effect` | boolean |  |  |
| `ai_role` | AiRole |  |  |
| `options` | DecisionOption |  |  |
| `chosen` | string |  |  |
| `inputs` | string |  |  |
| `rationale_claim_ref` | string |  |  |
| `oversight_modality` | OversightModality |  |  |
| `person_ref` | string |  |  |
