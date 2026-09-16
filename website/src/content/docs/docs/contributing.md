---
title: Contributing
description: How the standard changes, how to contribute, and how implementations are recognised.
---

Agent Conformance is an open standard. The specification, the engines, the catalogs, and this site are
developed in the open under the Apache-2.0 license, and the project welcomes contributions to all of
them.

## How the standard changes

Normative changes go through a public request-for-comments process, so that a change to what the
specification requires is deliberate, reviewed, and recorded. Editorial fixes, new source adapters,
additional catalog crosswalks, and engine improvements move through ordinary pull requests. Working
groups steward the specification, the engines, and the catalogs; the governance documents describe how
each operates.

## Ways to contribute

- **Report or fix an issue** in an engine, adapter, or the specification sources.
- **Propose a normative change** as a request for comments when it affects what conformance requires.
- **Extend coverage** with a source adapter for a new evidence format, or a crosswalk that maps the
  base catalog to another framework.
- **Add an implementation** in a new language that produces byte-identical output on the conformance
  suite.

Every commit carries a Developer Certificate of Origin sign-off, and commit messages describe the
change rather than the tooling that produced it.

## How implementations are recognised

An implementation demonstrates conformance by producing verified reports over the published conformance
suite. The conformance program describes what a conformance claim means and how a passing
implementation is listed in the public registry — the process is evidence-based: a listing is backed by
reports anyone can re-run and verify, not by self-assertion.

## Where to start

- The [specification](/docs/specification/) and [concepts](/docs/concepts/) pages for orientation.
- The source repository on
  [GitHub](https://github.com/agent-conformance/agentce) for issues, discussions, and the governance
  and RFC process.
