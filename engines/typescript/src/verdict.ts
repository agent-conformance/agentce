/**
 * The run verdict: one categorical word plus the six-outcome tally (SPEC §9.2, §9.3).
 *
 * The verdict is derived from the assertions and nothing else: SPEC §9.2 forbids a single composite
 * score, so the verdict is one of three fixed states and the report still shows all six counts beside
 * it. This is a faithful port of the Python reference (`verdict.py`); the message-catalogue-driven
 * `cli_lines`/`gap_text` rendering is deferred to the i18n infrastructure (#47) and not ported here.
 */

import type { Assertion } from "./assertions";
import { aggregate } from "./assertions";
import { sanitizeForMarkdown } from "./report";
import { byteCompare } from "./util";

export const NON_CONFORMANT = "non-conformant";
export const INCOMPLETE = "incomplete";
export const CONFORMANT = "conformant";

/** Outcomes that count as a gap, most urgent first (a failure before a shortfall in evidence). */
const GAP_OUTCOMES = ["non-conformant", "partial", "insufficient_evidence", "not_assessed"];

/** How many controls a gap line names before it says how many more there are. */
const MAX_LISTED = 5;

export interface Gap {
  outcome: string;
  controls: string[];
  more: number;
}

export interface Summary {
  verdict: string;
  counts: Record<string, number>;
  topGaps: Gap[];
}

/** Return `{verdict, counts, topGaps}` for `assertions` (order-, clock-, and locale-independent). */
export function summarize(assertions: Assertion[]): Summary {
  const counts = aggregate(assertions);
  let verdict: string;
  if (counts["non-conformant"]) {
    verdict = NON_CONFORMANT;
  } else if (counts.partial || counts.insufficient_evidence || counts.not_assessed) {
    verdict = INCOMPLETE;
  } else {
    verdict = CONFORMANT;
  }
  const topGaps: Gap[] = [];
  for (const outcome of GAP_OUTCOMES) {
    const controls = [
      ...new Set(assertions.filter((a) => a.outcome === outcome).map((a) => a.control)),
    ].sort(byteCompare);
    if (controls.length > 0) {
      topGaps.push({
        outcome,
        controls: controls.slice(0, MAX_LISTED),
        more: Math.max(0, controls.length - MAX_LISTED),
      });
    }
  }
  return { verdict, counts, topGaps };
}

/** English CLDR plural category: `"one"` for exactly 1, `"other"` otherwise -- the one plural rule
 * the message catalogue's `report.gaps_more` needs (mirrors the Python reference's
 * `i18n_format._english_plural_category`). */
function pluralCategory(n: number): string {
  return n === 1 ? "one" : "other";
}

/** Render an ICU MessageFormat plural template (`"{n, plural, one {...} other {...}}"`) against
 * `n`, replacing `#` with the formatted count -- a minimal port of the one construct the message
 * catalogue actually uses this way (`report.gaps_more`), not a general ICU engine (mirrors the
 * Python reference's `i18n_format.format_message`). */
function formatPlural(template: string, n: number): string {
  const match = /^\{n,\s*plural,\s*(.*)\}$/s.exec(template);
  if (!match) {
    return template;
  }
  const body = match[1] as string;
  const categories = new Map<string, string>();
  const categoryRe = /([A-Za-z0-9_=]+)\s*\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  // biome-ignore lint/suspicious/noAssignInExpressions: the standard exec-loop idiom
  while ((m = categoryRe.exec(body)) !== null) {
    categories.set(m[1] as string, m[2] as string);
  }
  const exact = `=${n}`;
  const category = categories.has(exact) ? exact : pluralCategory(n);
  const chosen = categories.get(category) ?? categories.get("other") ?? "";
  return chosen.replace("#", String(n));
}

/** One gap as text: `insufficient evidence: DAT-01, DAT-02 (+14 more gaps)` (mirrors the Python
 * reference's `verdict.gap_text` and Java's `Verdict.gapText`). Each control id is sanitised with
 * `sanitizeForMarkdown` -- mid-line, after the fixed label prefix, so no backtick-wrap is needed
 * here (unlike the summary tally). */
export function gapText(gap: Gap, cat: Record<string, string>): string {
  const label = cat[`outcome.${gap.outcome}`] ?? gap.outcome;
  const controls = gap.controls.map((c) => sanitizeForMarkdown(c)).join(", ");
  let text = `${label}: ${controls}`;
  if (gap.more > 0) {
    text += ` (${formatPlural(cat["report.gaps_more"] as string, gap.more)})`;
  }
  return text;
}
