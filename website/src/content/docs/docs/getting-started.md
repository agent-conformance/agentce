---
title: Getting Started
description: Install the Agent Conformance engine and run your first assessment.
---

Agent Conformance assesses how an AI agent behaves against a shared, executable specification. The
engine is deterministic and read-only over its inputs: it collects evidence about an agent, evaluates
that evidence against a control catalog, and produces a report you can reproduce and verify.

This page is the entry point to the documentation. The remaining guides — concepts, the specification,
running assessments, CI integration, and contributing — are being written as part of the site build.

## What you need

- A recent release of the engine for your language (Python, TypeScript, or Java).
- Evidence from an agent run, or one of the bundled example projects.

## Next steps

Once the engine is installed, a first assessment runs from a single command over a bundled example and
prints a report in a few minutes. The [specification](https://agent-conformance.org) defines the
evidence model, the control catalog, and the report formats the engine produces.
