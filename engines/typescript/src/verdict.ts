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
