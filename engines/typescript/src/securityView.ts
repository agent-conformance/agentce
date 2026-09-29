/**
 * The security view (18.16): the same run, read for tool access, enforcement-point evidence, and
 * external-standard citations.
 *
 * `computeSecurityView` adds no new rollup: `summarizeActivity` (activity.ts) already counts tool
 * access, actions by effect class, approvals and denials, and drift, and every `Assertion`
 * (assertions.ts) already carries its control's own `crosswalk` entries. This module only selects and
 * groups those two already-computed inputs -- it never re-scans events or re-derives a verdict. A
 * faithful port of the Python reference (`agentce/security_view.py`); field names, key order, and sort
 * order match exactly so `security.json` is byte-identical across engines.
 */

import type { Activity } from "./activity";
import type { Assertion } from "./assertions";
import { byteCompare } from "./util";

/** The three external-standard frameworks the security view cites (contracts/P18-18.16.md); a
 * control's crosswalk entries against any other framework (`eu-ai-act`, `iso-42001`, `nist-ai-rmf`,
 * `aiuc-1`) are out of scope for this view. */
const CITED_FRAMEWORKS = new Set(["owasp-asi-2026", "mitre-atlas", "owasp-acs"]);

export interface StandardsCitation {
  control: string;
  framework: string;
  clause: string;
  verified: boolean;
}

export interface SecurityView {
  tool_access: Activity["tools"];
  actions_by_effect_class: Record<string, number>;
  enforcement_point_evidence: {
    denied_or_blocked: Record<string, number>;
    approvals_by_recorder: Record<string, number>;
  };
  drift: { tools: string[]; models: string[] };
  standards_citations: StandardsCitation[];
}

function compareCitation(a: StandardsCitation, b: StandardsCitation): number {
  return (
    byteCompare(a.control, b.control) ||
    byteCompare(a.framework, b.framework) ||
    byteCompare(a.clause, b.clause) ||
    Number(a.verified) - Number(b.verified)
  );
}

/** Return the security-framed selection of `activity`/`assertions` for a whole run.
 *
 * Runs once over the pooled run regardless of subject count: unlike the project view's per-agent
 * split, "what could have stopped this run's actions" is a whole-run posture question. Deterministic:
 * no wall-clock, no locale, no assertion-order dependency (`standards_citations` is deduplicated and
 * sorted). */
export function computeSecurityView(activity: Activity, assertions: Assertion[]): SecurityView {
  const seen = new Map<string, StandardsCitation>();
  for (const a of assertions) {
    for (const e of a.crosswalk) {
      const framework = e.framework;
      if (typeof framework !== "string" || !CITED_FRAMEWORKS.has(framework)) {
        continue;
      }
      const clause = String(e.clause);
      const verified = Boolean(e.verified);
      const key = JSON.stringify([a.control, framework, clause, verified]);
      seen.set(key, { control: a.control, framework, clause, verified });
    }
  }
  const citations = [...seen.values()].sort(compareCitation);

  return {
    tool_access: activity.tools,
    actions_by_effect_class: activity.actions_by_effect_class,
    enforcement_point_evidence: {
      denied_or_blocked: activity.denied_or_blocked,
      approvals_by_recorder: activity.approvals_by_recorder,
    },
    drift: {
      tools: activity.undeclared.tools,
      models: activity.undeclared.models,
    },
    standards_citations: citations,
  };
}
