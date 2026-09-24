---
title: Accessibility Statement
description: Compliance status, scope, feedback contact, and enforcement procedure for agent-conformance.org.
---

This accessibility statement follows the structure of the European Commission's model accessibility
statement (Commission Implementing Decision (EU) 2018/1523) and applies to this website,
`https://agent-conformance.org`.

## Compliance status

This website targets WCAG 2.2 level AA. An automated rules engine (axe-core) scans every published page
in both the light and dark themes on every change and currently reports zero violations against the
WCAG 2.2 A/AA tag set. Automated scanning cannot detect every barrier a human encounters, and no manual
test — screen reader, keyboard-only navigation, 200%–400% zoom and reflow, or forced-colors mode — has
been completed yet (see [the manual test protocol](/docs/accessibility-testing-protocol/)). Until that
testing is complete, this site is **partially compliant**: conformant with the automated checks it runs,
not yet independently verified by manual testing.

The [VPAT / Accessibility Conformance Report](/docs/vpat/) records this same honest status per criterion:
every row reads "Not Evaluated" until a person completes the manual protocol, with the automated evidence
recorded alongside it.

## Scope

This statement covers every page published under `https://agent-conformance.org`, in every declared
locale. Most locales presently fall back to English content; the fallback pages inherit this statement's
compliance status. This statement does not cover the assessment reports (`report.html`) that the engines
described on this site generate — those are a separate output governed by the specification's own
rendering requirements, not by this website.

## Non-accessible content

No content is currently known to fail the automated WCAG 2.2 AA scan described above. Because manual
testing has not yet run, this statement cannot yet confirm that no barrier exists beyond what automated
tooling can detect — that confirmation is exactly what the manual test protocol and the VPAT's pending
rows exist to close.

## Preparation of this statement

This statement was prepared based on the results of the automated accessibility gate that runs in
continuous integration on every page, in both themes, on every change to this site.

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
