---
title: Canonical IRIs
description: Every one of the 19 canonical, dereferenceable IRIs the specification defines.
---

Every canonical, dereferenceable IRI the specification defines (SPEC §6, §9; `website/iri-manifest.json`), 19 in total: the JSON-LD context, the RDF vocabulary, and every JSON Schema an engine or a report validates against. Each is served byte-identically at its own canonical path and, here, linked to a human-readable page.

| IRI | Page | Content type |
|---|---|---|
| `/contexts/evidence/v1` | [/contexts/evidence/v1](/reference/iris/contexts-evidence-v1/) | application/ld+json |
| `/vocab/evidence/v1` | [/vocab/evidence/v1](/reference/iris/vocab-evidence-v1/) | text/turtle |
| `/schema/evidence/v1` | [agentce-evidence](/reference/iris/schema-evidence-v1/) | application/schema+json |
| `/spec/model/applicability-profile.schema.json` | [AgentCE applicability profile](/reference/iris/spec-model-applicability-profile/) | application/schema+json |
| `/spec/report/applicability-statement.schema.json` | [AgentCE applicability statement](/reference/report-schemas/applicability-statement/) | application/schema+json |
| `/spec/report/assertions.schema.json` | [AgentCE assertions](/reference/report-schemas/assertions/) | application/schema+json |
| `/spec/report/claim.schema.json` | [AgentCE conformance claim](/reference/report-schemas/claim/) | application/schema+json |
| `/spec/report/deviation-register.schema.json` | [AgentCE deviation register](/reference/report-schemas/deviation-register/) | application/schema+json |
| `/spec/report/integrity-result.schema.json` | [AgentCE integrity result](/reference/report-schemas/integrity-result/) | application/schema+json |
| `/spec/report/manifest.schema.json` | [AgentCE reproducibility manifest](/reference/report-schemas/manifest/) | application/schema+json |
| `/spec/report/oscal-assessment-results.schema.json` | [AgentCE OSCAL Assessment Results (profile)](/reference/report-schemas/oscal-assessment-results/) | application/schema+json |
| `/spec/report/oscal-component-definition.schema.json` | [AgentCE OSCAL Component Definition (profile)](/reference/report-schemas/oscal-component-definition/) | application/schema+json |
| `/spec/report/quarantine.schema.json` | [AgentCE quarantine record](/reference/report-schemas/quarantine/) | application/schema+json |
| `/spec/report/remediation-package.schema.json` | [AgentCE remediation package](/reference/report-schemas/remediation-package/) | application/schema+json |
| `/spec/report/results-sarif.schema.json` | [AgentCE SARIF results (profile)](/reference/report-schemas/results-sarif/) | application/schema+json |
| `/spec/rules/checklist.schema.json` | [AgentCE manual checklist](/reference/iris/spec-rules-checklist/) | application/schema+json |
| `/spec/rules/control.schema.json` | [AgentCE control](/reference/iris/spec-rules-control/) | application/schema+json |
| `/spec/rules/metric.schema.json` | [AgentCE metric specification](/reference/iris/spec-rules-metric/) | application/schema+json |
| `/spec/rules/probe.schema.json` | [AgentCE probe specification](/reference/iris/spec-rules-probe/) | application/schema+json |
