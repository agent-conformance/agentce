/**
 * `agentce diff` (SPEC §9.3): a real, content-keyed delta between two assertion sets -- every
 * `(control, subject)` whose outcome changed, was added, or was removed -- keyed by identity, never
 * array position, so the result is independent of either input's element order. A byte-for-byte port
 * of the Python reference's `_diff_assertion_sets`/`_diff_field`/`_classify_change`/`_what_changed`/
 * `_diff_change_line`/`_diff_md_lines` (`commands/__init__.py:2282-2424`).
 */

import { InputError } from "./errors";
import { sanitizeForMarkdown } from "./report";
import { byteCompare } from "./util";

export { normalizePosixPath } from "./util";
import { GAP_OUTCOMES } from "./verdict";

export interface Change {
  control: string;
  subject: string;
  from: string | null;
  to: string | null;
}

export interface WhatChanged {
  closed: Change[];
  opened: Change[];
  other: Change[];
}

/** `agentce diff --format`'s three renderings (SPEC §9.3): today's flat human-note list, the
 * machine-readable `what_changed` object standalone, and a Markdown "## What changed" section. */
export const DIFF_FORMATS = ["text", "json", "md"] as const;

/** `control`/`subject`/`outcome` read off an arbitrary record; throws `input.diff_field_not_string`
 * when the field is present but not a JSON string (a disclosed, intentionally narrower divergence
 * from Python's silent `str()` coercion -- TRADEOFFS.md). A structurally-missing field is an
 * uncaught error, mirroring Python's uncaught `KeyError` -- a pre-existing, accepted gap shared by
 * every other command's malformed-input path in this engine, not introduced here. */
function stringField(entry: Record<string, unknown>, field: string): string {
  if (!(field in entry)) {
    throw new Error(`missing required field '${field}'`);
  }
  const value = entry[field];
  if (typeof value !== "string") {
    throw new InputError(
      "input.diff_field_not_string",
      `assertion field '${field}' must be a string, not ${JSON.stringify(value)}.`,
      "emit control/subject/outcome as JSON strings.",
    );
  }
  return value;
}

/** `entries` keyed by `(control, subject)` identity, as a `control -> subject -> outcome` nested
 * map -- no composite string anywhere, so two distinct pairs can never collide the way a joined
 * string could. A repeated `(control, subject)` pair within one side's own array resolves
 * last-write-wins (mirrors a Python dict literal's own key-collision rule): the later `set` call
 * simply overwrites the earlier one in the same inner map. */
function bySide(entries: Record<string, unknown>[]): Map<string, Map<string, string>> {
  const out = new Map<string, Map<string, string>>();
  for (const entry of entries) {
    const control = stringField(entry, "control");
    const subject = stringField(entry, "subject");
    const outcome = stringField(entry, "outcome");
    let bySubject = out.get(control);
    if (bySubject === undefined) {
      bySubject = new Map<string, string>();
      out.set(control, bySubject);
    }
    bySubject.set(subject, outcome);
  }
  return out;
}

/** A real, content-keyed delta between two assertion sets: every `(control, subject)` whose outcome
 * changed, added, or was removed -- keyed by identity, never by array position, so the result is
 * independent of either input's element order. Sorted by `(control, subject)`: `byteCompare(control)`
 * then, on a tie, `byteCompare(subject)` -- real tuple comparison, never a composite-string sort. */
export function diffAssertionSets(
  a: Record<string, unknown>[],
  b: Record<string, unknown>[],
): Change[] {
  const left = bySide(a);
  const right = bySide(b);
  const pairs: { control: string; subject: string }[] = [];
  for (const control of new Set<string>([...left.keys(), ...right.keys()])) {
    const subjects = new Set<string>([
      ...(left.get(control)?.keys() ?? []),
      ...(right.get(control)?.keys() ?? []),
    ]);
    for (const subject of subjects) {
      pairs.push({ control, subject });
    }
  }
  pairs.sort((x, y) => byteCompare(x.control, y.control) || byteCompare(x.subject, y.subject));
  const changes: Change[] = [];
  for (const { control, subject } of pairs) {
    const before = left.get(control)?.get(subject) ?? null;
    const after = right.get(control)?.get(subject) ?? null;
    if (before !== after) {
      changes.push({ control, subject, from: before, to: after });
    }
  }
  return changes;
}

/** `value` sanitised for the terminal, or the fixed literal `"(none)"` when the key was absent on one
 * side of the diff (an added or removed assertion) -- `null` is never passed into the sanitiser,
 * whose own empty-value substitute means something different (a string that neutralises to nothing). */
function diffField(value: string | null): string {
  return value !== null ? sanitizeForMarkdown(value) : "(none)";
}

/** One of `"closed"`, `"opened"`, `"other"` for a single `(before, after)` outcome pair (SPEC §9.3;
 * item 18.6): `"closed"` iff `before` was a gap (`GAP_OUTCOMES`, which already includes
 * `not_assessed`) and `after` is `"conformant"`; `"opened"` iff the reverse. The two conditions cannot
 * both hold -- `"conformant"` is not itself a member of `GAP_OUTCOMES` -- so every pair lands in
 * exactly one bucket. An added or removed assertion (either side `null`) and any pair touching
 * `"not_applicable"` (a scope change, not a regression or a fix) always land in `"other"`; an
 * out-of-vocabulary outcome string does too, by the same plain string comparison, with no
 * special-casing. */
export function classifyChange(before: string | null, after: string | null): string {
  if (before !== null && GAP_OUTCOMES.includes(before) && after === "conformant") {
    return "closed";
  }
  if (before === "conformant" && after !== null && GAP_OUTCOMES.includes(after)) {
    return "opened";
  }
  return "other";
}

/** Group `changes` (`diffAssertionSets`'s own output, already sorted by `(control, subject)`) by
 * `classifyChange`, preserving that order within each of the three groups so the grouping is a
 * stable, cross-engine output contract. */
export function whatChanged(changes: Change[]): WhatChanged {
  const grouped: WhatChanged = { closed: [], opened: [], other: [] };
  for (const change of changes) {
    grouped[classifyChange(change.from, change.to) as keyof WhatChanged].push(change);
  }
  return grouped;
}

/** `control @ subject: from -> to`, sanitised -- the one line both the text and md formats render for
 * a single change, so the two renderings cannot drift apart from each other. */
export function diffChangeLine(change: Change): string {
  const control = sanitizeForMarkdown(change.control);
  const subject = sanitizeForMarkdown(change.subject);
  return `${control} @ ${subject}: ${diffField(change.from)} -> ${diffField(change.to)}`;
}

/** The `--format md` "## What changed" section as a list of lines: one `### <Label> (<n>)`
 * subsection per **non-empty** group only, in `closed, opened, other` order, each a bullet list built
 * from the same fields the text format already renders. */
export function diffMdLines(grouped: WhatChanged): string[] {
  const groups: [keyof WhatChanged, string][] = [
    ["closed", "Closed"],
    ["opened", "Opened"],
    ["other", "Other changes"],
  ];
  const nonEmpty = groups.filter(([key]) => grouped[key].length > 0);
  if (nonEmpty.length === 0) {
    return ["## What changed", "", "no differences."];
  }
  const lines = ["## What changed"];
  for (const [key, label] of nonEmpty) {
    lines.push("");
    lines.push(`### ${label} (${grouped[key].length})`);
    for (const change of grouped[key]) {
      lines.push(`- ${diffChangeLine(change)}`);
    }
  }
  return lines;
}
