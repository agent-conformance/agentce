---
title: Evidence model
description: Every class in the AgentCE evidence model, generated from the LinkML source.
---

Every class in the AgentCE evidence model (SPEC §6.2), generated from `spec/model/agentce-evidence.linkml.yaml`. One page per class.

| Class | Abstract | Description |
|---|---|---|
| [`AgentRef`](/reference/evidence/agentref/) |  | The acting agent. |
| [`ApprovalDecidedPayload`](/reference/evidence/approvaldecidedpayload/) |  |  |
| [`ApprovalRequestedPayload`](/reference/evidence/approvalrequestedpayload/) |  |  |
| [`AttestationPayload`](/reference/evidence/attestationpayload/) |  |  |
| [`AuthzCheckPayload`](/reference/evidence/authzcheckpayload/) |  |  |
| [`BundleLoadedPayload`](/reference/evidence/bundleloadedpayload/) |  |  |
| [`ComponentRef`](/reference/evidence/componentref/) |  |  |
| [`DecisionOption`](/reference/evidence/decisionoption/) |  |  |
| [`DecisionPayload`](/reference/evidence/decisionpayload/) |  |  |
| [`DelegationIssuedPayload`](/reference/evidence/delegationissuedpayload/) |  |  |
| [`DisclosurePayload`](/reference/evidence/disclosurepayload/) |  |  |
| [`EvidenceEvent`](/reference/evidence/evidenceevent/) | yes | CloudEvent envelope carrying an AgentCE evidence payload (SPEC 6.2.1). |
| [`IncidentPayload`](/reference/evidence/incidentpayload/) |  |  |
| [`InstructionPayload`](/reference/evidence/instructionpayload/) |  |  |
| [`IntegrityBlock`](/reference/evidence/integrityblock/) |  | Per-event integrity envelope (SPEC 6.6). |
| [`IntegrityResultPayload`](/reference/evidence/integrityresultpayload/) |  | Engine-computed verification outcome per stream (SPEC 6.6). |
| [`InterruptPayload`](/reference/evidence/interruptpayload/) |  |  |
| [`MemoryReadPayload`](/reference/evidence/memoryreadpayload/) |  |  |
| [`MemoryWritePayload`](/reference/evidence/memorywritepayload/) |  |  |
| [`ModelCallPayload`](/reference/evidence/modelcallpayload/) |  |  |
| [`ModelRef`](/reference/evidence/modelref/) |  |  |
| [`NoticePayload`](/reference/evidence/noticepayload/) |  |  |
| [`OutcomePayload`](/reference/evidence/outcomepayload/) |  |  |
| [`OverridePayload`](/reference/evidence/overridepayload/) |  |  |
| [`Payload`](/reference/evidence/payload/) | yes | Common members of every event payload (SPEC 6.2.2). |
| [`PolicyDecisionPayload`](/reference/evidence/policydecisionpayload/) |  |  |
| [`Principal`](/reference/evidence/principal/) |  | A human, service, or agent principal. |
| [`Refs`](/reference/evidence/refs/) |  | Typed references to other events; slot_uri gives the graph relation (SPEC 6.3). A ref names an event by its graph IRI, the event id prefixed with agentce:event/ (SPEC 3). It resolves only to a held event with that IRI. A ref in any other form, a bare event id included, names nothing held, and a control that needs the event it names fails closed. The one exception is decision: it also resolves its padded and bare-id forms (SPEC 10.4) to the held decision, because approval and oversight systems write their own ids and cannot write the engine's IRI. |
| [`RefusalPayload`](/reference/evidence/refusalpayload/) |  |  |
| [`ResourceAccessPayload`](/reference/evidence/resourceaccesspayload/) |  |  |
| [`ResourceRef`](/reference/evidence/resourceref/) |  |  |
| [`SessionEndPayload`](/reference/evidence/sessionendpayload/) |  |  |
| [`SessionStartPayload`](/reference/evidence/sessionstartpayload/) |  |  |
| [`ToolCallPayload`](/reference/evidence/toolcallpayload/) |  |  |
| [`ToolRef`](/reference/evidence/toolref/) |  |  |
| [`Usage`](/reference/evidence/usage/) |  |  |
| [`Verification`](/reference/evidence/verification/) |  |  |
