# Evidence model

Every class in the AgentCE evidence model (SPEC §6.2), generated from `spec/model/agentce-evidence.linkml.yaml`. One page per class.

| Class | Abstract | Description |
|---|---|---|
| [`AgentRef`](AgentRef.md) |  | The acting agent. |
| [`ApprovalDecidedPayload`](ApprovalDecidedPayload.md) |  |  |
| [`ApprovalRequestedPayload`](ApprovalRequestedPayload.md) |  |  |
| [`AttestationPayload`](AttestationPayload.md) |  |  |
| [`AuthzCheckPayload`](AuthzCheckPayload.md) |  |  |
| [`BundleLoadedPayload`](BundleLoadedPayload.md) |  |  |
| [`ComponentRef`](ComponentRef.md) |  |  |
| [`DecisionOption`](DecisionOption.md) |  |  |
| [`DecisionPayload`](DecisionPayload.md) |  |  |
| [`DelegationIssuedPayload`](DelegationIssuedPayload.md) |  |  |
| [`DisclosurePayload`](DisclosurePayload.md) |  |  |
| [`EvidenceEvent`](EvidenceEvent.md) | yes | CloudEvent envelope carrying an AgentCE evidence payload (SPEC 6.2.1). |
| [`IncidentPayload`](IncidentPayload.md) |  |  |
| [`InstructionPayload`](InstructionPayload.md) |  |  |
| [`IntegrityBlock`](IntegrityBlock.md) |  | Per-event integrity envelope (SPEC 6.6). |
| [`IntegrityResultPayload`](IntegrityResultPayload.md) |  | Engine-computed verification outcome per stream (SPEC 6.6). |
| [`InterruptPayload`](InterruptPayload.md) |  |  |
| [`MemoryReadPayload`](MemoryReadPayload.md) |  |  |
| [`MemoryWritePayload`](MemoryWritePayload.md) |  |  |
| [`ModelCallPayload`](ModelCallPayload.md) |  |  |
| [`ModelRef`](ModelRef.md) |  |  |
| [`NoticePayload`](NoticePayload.md) |  |  |
| [`OutcomePayload`](OutcomePayload.md) |  |  |
| [`OverridePayload`](OverridePayload.md) |  |  |
| [`Payload`](Payload.md) | yes | Common members of every event payload (SPEC 6.2.2). |
| [`PolicyDecisionPayload`](PolicyDecisionPayload.md) |  |  |
| [`Principal`](Principal.md) |  | A human, service, or agent principal. |
| [`Refs`](Refs.md) |  | Typed references to other events; slot_uri gives the graph relation (SPEC 6.3). A ref names an event by its graph IRI, the event id prefixed with agentce:event/ (SPEC 3). It resolves only to a held event with that IRI. A ref in any other form, a bare event id included, names nothing held, and a control that needs the event it names fails closed. The one exception is decision: it also resolves its padded and bare-id forms (SPEC 10.4) to the held decision, because approval and oversight systems write their own ids and cannot write the engine's IRI. |
| [`RefusalPayload`](RefusalPayload.md) |  |  |
| [`ResourceAccessPayload`](ResourceAccessPayload.md) |  |  |
| [`ResourceRef`](ResourceRef.md) |  |  |
| [`SessionEndPayload`](SessionEndPayload.md) |  |  |
| [`SessionStartPayload`](SessionStartPayload.md) |  |  |
| [`ToolCallPayload`](ToolCallPayload.md) |  |  |
| [`ToolRef`](ToolRef.md) |  |  |
| [`Usage`](Usage.md) |  |  |
| [`Verification`](Verification.md) |  |  |
