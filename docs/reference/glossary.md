# Glossary

Canonical AgentCE terminology (SPEC Appendix E), generated from `spec/methodology/glossary.yaml` so the published glossary cannot drift from the specification's own term list.

<dl>
<dt>Assertion</dt>
<dd>the engine's outcome for one control on one subject, with evidence pointers.</dd>
<dt>Bundle</dt>
<dd>the unit of assessment: events, attestations, reference snapshots, manifest.</dd>
<dt>Chokepoint</dt>
<dd>a place in an agent architecture where every instance of an action must pass (model I/O, tool access, authorization, human decision, artifact load).</dd>
<dt>Conformance claim</dt>
<dd>the scoped statement of what was assessed, by whom, against what, with what methods.</dd>
<dt>Deviation</dt>
<dd>an accepted, time-bounded departure from a control with a compensating measure.</dd>
<dt>Enforcement point</dt>
<dd>a system that could have prevented the action and whose record does not depend on the agent.</dd>
<dt>Glue vocabulary</dt>
<dd>the small set of relations AgentCE defines because no existing vocabulary carries them.</dd>
<dt>Portable shape profile (PSP)</dt>
<dd>the SHACL Core subset all engines execute identically.</dd>
<dt>Rung</dt>
<dd>the position of an outcome on the correctness ladder (coverage, integrity, structural, statistical, semantic, legal).</dd>
<dt>Subject</dt>
<dd>the agent deployment being assessed.</dd>
</dl>
