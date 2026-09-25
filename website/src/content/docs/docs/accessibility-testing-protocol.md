---
title: Accessibility Test Protocol
description: The manual test procedure a human tester runs to complete the VPAT — screen reader, keyboard-only, 200%-400% zoom, and forced-colors mode.
---

This is the manual test procedure referenced by the [accessibility statement](/docs/accessibility-statement/)
and the [VPAT](/docs/vpat/). Automated scanning (axe-core, which can run against every page in both
themes over the WCAG 2.2 A/AA tag set, though the full scan is currently paused in continuous
integration by maintainer decision — see the accessibility statement) cannot certify a WCAG success
criterion the way a human tester following this procedure can. Running it, and recording the result
against each VPAT row, is a human action — no automated check performs it. Until the full scan resumes in continuous integration, conformance is verified manually, downstream, by following this procedure.

## Scope

Every page published under `https://agent-conformance.org`, in both the light and dark themes.

## Screen reader testing

Test with at least one desktop screen reader (VoiceOver on macOS/Safari, or NVDA on Windows/Firefox or
Chrome) on the landing page, a documentation page, and a generated-reference page:

1. Navigate by landmark and by heading; confirm the heading structure matches the visual outline and
   every landmark (banner, navigation, main, contentinfo) is announced.
2. Navigate every link and button by Tab and by the screen reader's element list; confirm each has an
   accessible name that describes its destination or action, not "link" or "button" alone.
3. Confirm images convey their accessible-name/alt text correctly, and that purely decorative images are
   silent.
4. Toggle the light/dark theme control and confirm the screen reader announces the resulting state
   change.
5. Read a code block and confirm its content and any "copy" control are both reachable and announced.

## Keyboard-only testing

Unplug or ignore the pointing device and operate the whole site by keyboard alone:

1. Tab from the top of the page through every interactive control (skip link, navigation links, the
   sidebar's document tree, the theme toggle, in-page search, and every code block's copy control) and
   confirm a visible focus indicator follows the caret at every step.
2. Confirm the tab order matches the visual reading order and that no control is skipped or duplicated.
3. Confirm no keyboard trap exists — every control that can be entered by keyboard can also be left by
   keyboard (Tab, Shift+Tab, or Escape).
4. Confirm a "skip to content" mechanism reaches the main content without traversing the whole
   navigation first.
5. Operate the sidebar's collapsible sections and the theme toggle using only Enter/Space and confirm
   each responds correctly.

## Zoom and reflow (200%–400%)

Test the landing page, a documentation page, and a generated-reference page at both 200% and 400%
browser zoom:

1. At 200% zoom, confirm no content requires horizontal scrolling to read (WCAG 1.4.10 Reflow), text is
   not clipped or overlapping, and every control remains operable.
2. At 400% zoom, repeat the same checks at a 320px-equivalent effective viewport width.
3. Confirm no information or functionality is lost at either zoom level compared to 100%.

## Forced-colors (high-contrast) mode

Test with the operating system's or browser's forced-colors mode enabled (Windows High Contrast / Edge
and Chrome "Forced Colors" mode, or the equivalent `forced-colors: active` media feature):

1. Confirm every interactive control's boundary and focus indicator remain visible.
2. Confirm no information conveyed only through a custom background color or icon disappears.
3. Confirm the light/dark theme toggle and code-block styling remain legible and operable.

## Recording results

For each criterion in the [VPAT](/docs/vpat/), record the real conformance level the testing above
supports (`Supports`, `Partially Supports`, `Does Not Support`, or `Not Applicable`) in place of "Not
Evaluated", and keep the existing automated-evidence remark alongside the new finding from this protocol.
