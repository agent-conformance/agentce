---
title: The Conformance Spec
description: How the specification fits together — the evidence model, the control catalog, and the report formats.
---

The specification is language-neutral and executable. It defines the evidence an engine reads, the
controls it evaluates, and the reports it writes, so that independent implementations agree on the
result. This page is a map; the normative artifacts are served at their canonical IRIs.

## The evidence model

The evidence model defines the event types an assessment consumes — sessions, model calls, tool
calls, decisions, oversight actions, and their relations. It is published as:

- a JSON-LD context at
  [`https://agent-conformance.org/contexts/evidence/v1`](https://agent-conformance.org/contexts/evidence/v1),
- a vocabulary at
  [`https://agent-conformance.org/vocab/evidence/v1`](https://agent-conformance.org/vocab/evidence/v1), and
- a JSON Schema at
  [`https://agent-conformance.org/schema/evidence/v1`](https://agent-conformance.org/schema/evidence/v1).

Each of these resolves to a document served byte-for-byte from the specification sources, so a
reference in a report and the document it names never disagree.

## The control catalog

Controls are grouped into families and published as a catalog. The base catalog maps the EU AI Act;
its families cover areas such as record-keeping, human oversight, integrity, and incident handling.
Overlays extend the base with domain and framework requirements. Every control declares the evidence
it needs, so coverage — what could and could not be evaluated — is computed, not asserted.

## The report formats

An assessment writes one set of evidence in several renderings, each with a published schema: the
machine-readable assertions, the human report, an OSCAL Assessment Results document, a SARIF file, and
the applicability, coverage, integrity, and quarantine records. Every report has a canonical byte
form, which is what makes two runs — or two engines — comparable down to the byte.

## Read next

- [Running Assessments](/docs/running-assessments/) — the commands that produce these artifacts.
- [Concepts](/docs/concepts/) — the model in plain terms.
