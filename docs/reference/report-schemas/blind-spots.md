# `blind-spots.schema.json`

AgentCE blind spots.

blind-spots.json in the report directory: catalog evidence requirements no assertion's records can yet show, grouped by the one requirement that would satisfy them and ranked by how many checks it alone would unlock, each with an evidence-ladder rung, an owner, and whether the step is a code change or a request to another team. Built only from (assertions, catalogs); never affects an outcome or the verdict. No natural-language text is stored here -- the report renders owner_key/step_kind/event/class/checks_unlocked into the ladder's sentence per report language.

[View the schema](../../../spec/report/blind-spots.schema.json) (`https://agent-conformance.org/spec/report/blind-spots.schema.json`).

| Property | Type | Description |
|---|---|---|
| `blind_spots` | array |  |
| `no_population` | array |  |
