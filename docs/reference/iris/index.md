# Canonical IRIs

Every canonical, dereferenceable IRI the specification defines (SPEC §6, §9; `website/iri-manifest.json`), 19 in total: the JSON-LD context, the RDF vocabulary, and every JSON Schema an engine or a report validates against. Each is served byte-identically at its own canonical path and, here, linked to a human-readable page.

| IRI | Page | Content type |
|---|---|---|
| `/contexts/evidence/v1` | [/contexts/evidence/v1](contexts-evidence-v1.md) | application/ld+json |
| `/vocab/evidence/v1` | [/vocab/evidence/v1](vocab-evidence-v1.md) | text/turtle |
| `/schema/evidence/v1` | [agentce-evidence](schema-evidence-v1.md) | application/schema+json |
| `/spec/model/applicability-profile.schema.json` | [AgentCE applicability profile](spec-model-applicability-profile.md) | application/schema+json |
| `/spec/report/applicability-statement.schema.json` | [AgentCE applicability statement](../report-schemas/applicability-statement.md) | application/schema+json |
| `/spec/report/assertions.schema.json` | [AgentCE assertions](../report-schemas/assertions.md) | application/schema+json |
| `/spec/report/claim.schema.json` | [AgentCE conformance claim](../report-schemas/claim.md) | application/schema+json |
| `/spec/report/deviation-register.schema.json` | [AgentCE deviation register](../report-schemas/deviation-register.md) | application/schema+json |
| `/spec/report/integrity-result.schema.json` | [AgentCE integrity result](../report-schemas/integrity-result.md) | application/schema+json |
| `/spec/report/manifest.schema.json` | [AgentCE reproducibility manifest](../report-schemas/manifest.md) | application/schema+json |
| `/spec/report/oscal-assessment-results.schema.json` | [AgentCE OSCAL Assessment Results (profile)](../report-schemas/oscal-assessment-results.md) | application/schema+json |
| `/spec/report/oscal-component-definition.schema.json` | [AgentCE OSCAL Component Definition (profile)](../report-schemas/oscal-component-definition.md) | application/schema+json |
| `/spec/report/quarantine.schema.json` | [AgentCE quarantine record](../report-schemas/quarantine.md) | application/schema+json |
| `/spec/report/remediation-package.schema.json` | [AgentCE remediation package](../report-schemas/remediation-package.md) | application/schema+json |
| `/spec/report/results-sarif.schema.json` | [AgentCE SARIF results (profile)](../report-schemas/results-sarif.md) | application/schema+json |
| `/spec/rules/checklist.schema.json` | [AgentCE manual checklist](spec-rules-checklist.md) | application/schema+json |
| `/spec/rules/control.schema.json` | [AgentCE control](spec-rules-control.md) | application/schema+json |
| `/spec/rules/metric.schema.json` | [AgentCE metric specification](spec-rules-metric.md) | application/schema+json |
| `/spec/rules/probe.schema.json` | [AgentCE probe specification](spec-rules-probe.md) | application/schema+json |
