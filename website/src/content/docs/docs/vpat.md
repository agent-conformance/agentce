---
title: VPAT / Accessibility Conformance Report
description: A VPAT 2.5 mapping this site to EN 301 549 clauses 9 (Web), 10 (Non-web documents), and 11 (Software).
---

This is a Voluntary Product Accessibility Template (VPAT® 2.5 INT edition), reporting on
`https://agent-conformance.org` against EN 301 549 (V3.2.1) clauses 9 (Web), 10 (Non-web documents), and
11 (Software), and the WCAG 2.2 success criteria those clauses incorporate by reference.

**Honesty rule:** an automated rules engine cannot certify a WCAG success criterion the way a human
tester can. Every row below reads **Not Evaluated** until [the manual test protocol](/docs/accessibility-testing-protocol/)
has actually been run against this site by a person; the Remarks column records what automated evidence
exists so a reader can see exactly how much of each row's claim is backed by a real check versus
still open. The full-page automated scan is currently paused in continuous integration by maintainer
decision (its self-test, which proves the scan can still detect a real violation, runs on every change;
the full site scan does not) — the Remarks below report what its last full run found, and accessibility
conformance is verified manually, downstream, until the scan resumes or the manual protocol completes.
See the [accessibility statement](/docs/accessibility-statement/) for the same status in prose.

## Web (EN 301 549 Clause 9) — WCAG 2.2 Report

| Criteria | Conformance Level | Remarks |
|---|---|---|
| 1.1.1 Non-text Content | Not Evaluated | Automated axe-core WCAG 2.2 AA scan (every page, both themes) reported no violations for this rule in its last full run before the scan was paused in CI; not yet manually verified. |
| 1.3.1 Info and Relationships | Not Evaluated | Automated axe-core scan reported no violations in its last full run; not yet manually verified. |
| 1.3.2 Meaningful Sequence | Not Evaluated | No automated coverage for reading order; pending manual review. |
| 1.3.4 Orientation | Not Evaluated | No automated coverage; pending manual review. |
| 1.3.5 Identify Input Purpose | Not Evaluated | No automated coverage; pending manual review. |
| 1.4.1 Use of Color | Not Evaluated | Automated axe-core scan reported no violations in its last full run; not yet manually verified. |
| 1.4.3 Contrast (Minimum) | Not Evaluated | Automated axe-core `color-contrast` rule reported no violations across every page and theme in its last full run; not yet manually verified. |
| 1.4.4 Resize Text | Not Evaluated | No automated coverage; pending manual review (see the 200%-400% zoom step of the manual test protocol). |
| 1.4.10 Reflow | Not Evaluated | No automated coverage; pending manual review (see the manual test protocol). |
| 1.4.11 Non-text Contrast | Not Evaluated | Automated axe-core scan reported no violations in its last full run; not yet manually verified. |
| 1.4.12 Text Spacing | Not Evaluated | No automated coverage; pending manual review. |
| 1.4.13 Content on Hover or Focus | Not Evaluated | No automated coverage; pending manual review. |
| 2.1.1 Keyboard | Not Evaluated | No automated coverage; pending manual review (see the keyboard-only step of the manual test protocol). |
| 2.1.2 No Keyboard Trap | Not Evaluated | No automated coverage; pending manual review. |
| 2.4.3 Focus Order | Not Evaluated | No automated coverage; pending manual review. |
| 2.4.7 Focus Visible | Not Evaluated | No automated coverage; pending manual review. |
| 2.4.11 Focus Not Obscured (Minimum) | Not Evaluated | No automated coverage; pending manual review. |
| 2.5.7 Dragging Movements | Not Evaluated | No automated coverage; pending manual review. |
| 2.5.8 Target Size (Minimum) | Not Evaluated | Automated axe-core scan reported no violations for `target-size` in its last full run; not yet manually verified. |
| 3.1.1 Language of Page | Not Evaluated | Automated axe-core `html-has-lang` rule reported no violations in its last full run; not yet manually verified. |
| 3.2.6 Consistent Help | Not Evaluated | No automated coverage; pending manual review. |
| 3.3.1 Error Identification | Not Evaluated | No automated coverage; pending manual review (this site has no forms that produce validation errors today). |
| 3.3.7 Redundant Entry | Not Evaluated | No automated coverage; pending manual review (this site has no multi-step forms today). |
| 3.3.8 Accessible Authentication (Minimum) | Not Evaluated | No automated coverage; pending manual review (this site has no authentication today). |
| 4.1.2 Name, Role, Value | Not Evaluated | Automated axe-core scan (`button-name`, `image-alt`, and related rules) reported no violations in its last full run; not yet manually verified. |

## Non-web Documents (EN 301 549 Clause 10)

| Criteria | Conformance Level | Remarks |
|---|---|---|
| 10.1.1.1 Accessible Electronic Documents (based on WCAG 1.1.1 Non-text Content) | Not Evaluated | No automated coverage for downloadable documents; pending manual review. |
| 10.1.3.1 Accessible Electronic Documents (based on WCAG 1.3.1 Info and Relationships) | Not Evaluated | No automated coverage; pending manual review. |
| 10.1.4.3 Accessible Electronic Documents (based on WCAG 1.4.3 Contrast) | Not Evaluated | No automated coverage; pending manual review. |
| 10.2.1.1 Accessible Electronic Documents (based on WCAG 2.1.1 Keyboard) | Not Evaluated | No automated coverage; pending manual review. |
| 10.4.1.2 Accessible Electronic Documents (based on WCAG 4.1.2 Name, Role, Value) | Not Evaluated | No automated coverage; pending manual review. |

## Software (EN 301 549 Clause 11)

| Criteria | Conformance Level | Remarks |
|---|---|---|
| 11.3 Accessibility Services (platform accessibility API support) | Not Evaluated | No automated coverage; pending manual review with a screen reader (see the manual test protocol). |
| 11.5.2.1 Accessible Content Technologies | Not Evaluated | No automated coverage; pending manual review. |
| 11.5.2.5 Objects that are Programmatically Determinable | Not Evaluated | Automated axe-core scan reported no violations in its last full run; not yet manually verified. |
| 11.7 Interoperability with Assistive Technology | Not Evaluated | No automated coverage; pending manual review with a screen reader. |
| 11.8.2 User Preferences | Not Evaluated | No automated coverage; pending manual review (theme toggle, forced-colors mode). |

**How to read this table:** "Not Evaluated" is not "Fails" or "Not Applicable" — it means a human has not
yet completed the manual test that would let this document honestly claim a conformance level for that
criterion, per the rule that automated checks alone never justify "Supports" in a VPAT. When
[the manual protocol](/docs/accessibility-testing-protocol/) is run against this site, each row will be
updated to the real level the testing found (`Supports`, `Partially Supports`, `Does Not Support`, or
`Not Applicable`), and the Remarks column will keep the automated evidence already recorded here
alongside the new human finding.
