---
title: Accessibility Statement
description: Compliance status, scope, feedback contact, and enforcement procedure for agent-conformance.org.
---

This accessibility statement follows the structure of the European Commission's model accessibility
statement (Commission Implementing Decision (EU) 2018/1523) and applies to this website,
`https://agent-conformance.org`.

## Compliance status

This website targets WCAG 2.2 level AA. An automated rules engine (axe-core), built into this
repository, can scan every published page in both the light and dark themes against the WCAG 2.2 A/AA
tag set; its last full run reported zero violations. Running that full scan automatically is currently
paused in continuous integration by maintainer decision — the scan's own self-test, which proves it can
still detect a real violation, runs on every change, but the full-site scan itself does not — so
accessibility conformance is verified manually, downstream, rather than continuously in CI. Automated
scanning also cannot detect every barrier a human encounters, and no manual test — screen reader,
keyboard-only navigation, 200%–400% zoom and reflow, or forced-colors mode — has been completed yet
(see [the manual test protocol](/docs/accessibility-testing-protocol/)). Until manual testing is
complete and automated scanning resumes in CI, this site's WCAG 2.2 AA conformance is **not yet
confirmed**.

The [VPAT / Accessibility Conformance Report](/docs/vpat/) records this same honest status per criterion:
every row reads "Not Evaluated" until a person completes the manual protocol, with whatever automated
evidence exists recorded alongside it.

## Scope

This statement covers every page published under `https://agent-conformance.org`, in every declared
locale. Most locales presently fall back to English content; the fallback pages inherit this statement's
compliance status. This statement does not cover the assessment reports (`report.html`) that the engines
described on this site generate — those are a separate output governed by the specification's own
rendering requirements, not by this website.

## Non-accessible content

No content was known to fail the automated WCAG 2.2 AA scan described above as of its last full run.
Because that scan is currently paused in CI and manual testing has not yet run, this statement cannot
yet confirm that no barrier exists beyond what automated tooling detected at that time — that
confirmation is exactly what a resumed automated scan, the manual test protocol, and the VPAT's pending
rows exist to close.

## Preparation of this statement

This statement was prepared based on the results of the automated accessibility gate's last full run
over every page, in both themes. That gate's self-test runs on every change to this site to prove it
has not been silently disabled; the full-site scan itself is currently paused in continuous integration
by maintainer decision, and this statement will be updated when it resumes or when manual testing
completes.

## Feedback and contact information

If you encounter an accessibility barrier on this site, please report it:

- Open an issue on the [project's GitHub repository](https://github.com/agent-conformance/agentce/issues).
- Or email `security@agent-conformance.org`.

We aim to acknowledge accessibility feedback within 5 business days.

## Enforcement procedure

AgentCE is an open-source software project, not a public-sector body, so no monitoring or enforcement
body is designated for it under Directive (EU) 2016/2102. An organization that deploys this site's
content, or the reports its engines generate, as part of a public-sector or European-Accessibility-Act-
covered service should follow that jurisdiction's own accessibility enforcement procedure if a complaint
raised through the feedback channel above is not resolved to their satisfaction.
