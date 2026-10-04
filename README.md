# (AgentCE)

**Open, repeatable checks of AI agent behavior against AI rules and standards.**

**Check how your AI agents behave against the AI rules and standards you follow, then fix the gaps.**

Point AgentCE at the records you already keep. It shows where your agents meet the standard you follow, where they fall short, and where your records can't show. Your team gets reports written for different roles, and the AI assistant your developers use gets a skill for the fixes.

**Status:** under development — not yet ready for use.

## Ingest sources AgentCE supports

**Supported** (46): Claude Code session files, OpenTelemetry GenAI traces, MCP tools/list, A2A Agent Cards, AWS CloudTrail, CycloneDX, and 40 more — see the full matrix.

**Experimental** (4): Cursor CLI output, OpenAI Agents API session traces, Datadog LLM Observability, AuthZEN decisions.

**Roadmap** (4): Cursor, LangGraph dev server checkpoints, OpenAI Agents SDK hosted traces, Cedar — help wanted.

See the [full support matrix](https://agent-conformance.org/reference/ingest-support-matrix/) for each source's definition, version and risk.

## Reach: one reader per standard

Reads records from 400+ tools and 600+ cloud services, and the tool declarations of 25,000+ MCP servers, through the open standards they already write. One reader per standard, not one integration per product.

| Standard | Products confirmed to write it | Checked | Source |
|---|---|---|---|
| OpenTelemetry GenAI and OpenInference traces | 71 | 2026-09-29 | https://github.com/open-telemetry/opentelemetry-python-genai |
| Kubernetes audit events | 146 | 2026-09-29 | https://github.com/cncf/k8s-conformance |
| CycloneDX | 108 | 2026-09-29 | https://cyclonedx.org/tool-center/ |
| SPDX | 43 | 2026-09-29 | https://spdx.dev/use/spdx-tools/ |
| OCSF | 39 | 2026-09-29 | https://docs.aws.amazon.com/security-lake/latest/userguide/integrations-third-party.html |
| Sigstore and in-toto | 8 | 2026-09-29 | https://docs.npmjs.com/generating-provenance-statements/ |
| Distinct products, deduplicated | 408 (348 counting only self-managed Kubernetes) | 2026-09-29 | — |
| Cloud audit logs | 633 | 2026-09-29 | https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-aws-service-specific-topics.html, https://docs.cloud.google.com/logging/docs/audit/services, https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-services-resource-providers |
| MCP tool declarations | 27,992 | 2026-09-29 | https://registry.modelcontextprotocol.io/v0/servers?version=latest&limit=100 |

See the [reach table on the website](https://agent-conformance.org/reference/ingest-support-matrix/#reach-one-reader-per-standard) for the same numbers, generated from the matrix.

**License:** Apache-2.0
