/**
 * The auditor view (18.17): "can I rely on this, clause by clause?"
 *
 * A faithful port of the Python reference (`agentce/auditor_view.py`). `computeAuditorView` adds no
 * new rollup and re-derives no verdict: one `Assertion` already is one clause's full record, so
 * `clauses` selects that record as-is, one entry per assertion, sorted by (control, subject).
 * `by_clause` is a navigation index built from the same entries' own `crosswalk` field, sorted by
 * (framework, clause) with its control ids deduplicated and sorted. A manual or semi-automated control
 * still `not_assessed` carries the disclosed not-yet-evaluated note instead of a fabricated result.
 */

import { type Assertion, aggregate, evidencePointerToJson } from "./assertions";
import { deviationsByControl } from "./assess";
import { catalogue } from "./messages";
import { byteCompare, pyStr } from "./util";

/** Modes whose `not_assessed` outcome gets the disclosed not-yet-evaluated note. */
const MANUAL_MODES = new Set(["manual", "semi-automated"]);

/** The register record's six required fields (deviation-register.schema.json), copied verbatim. */
const DEVIATION_FIELDS = [
  "rationale",
  "compensating_control",
  "owner",
  "approver",
  "granted",
  "expiry",
] as const;

/** The matching register entry for `deviation` (an assertion's own `deviation` field), verbatim, or,
 * when no register was passed (a report re-rendered from `assertions.json` alone), a minimal record
 * naming only the control id. Never re-validated here: the register was already linted. */
function deviationDetail(
  deviation: string,
  byControl: Map<string, Record<string, unknown>>,
): Record<string, unknown> {
  const entry = byControl.get(deviation);
  if (entry === undefined) {
    return { control: deviation };
  }
  const detail: Record<string, unknown> = {};
  for (const field of DEVIATION_FIELDS) {
    detail[field] = pyStr(entry[field] ?? "");
  }
  const refs = entry.evidence_refs;
  if (Array.isArray(refs) ? refs.length > 0 : Boolean(refs)) {
    detail.evidence_refs = Array.from(refs as Iterable<unknown>, (ref) => pyStr(ref));
  }
  return detail;
}

/** Return the clause-by-clause selection of `assertions` for a whole run. Deterministic: no clock,
 * no locale, no dependence on the input order. */
export function computeAuditorView(
  assertions: Assertion[],
  deviations?: Record<string, unknown>[] | null,
  counts?: Record<string, number>,
): Record<string, unknown> {
  const byControl = deviationsByControl(deviations);
  const note = catalogue()["report.manual_checklist_not_yet_evaluated"];

  const clauses: Record<string, unknown>[] = [];
  const byClause = new Map<string, Map<string, Set<string>>>();
  const ordered = [...assertions].sort(
    (a, b) => byteCompare(a.control, b.control) || byteCompare(a.subject, b.subject),
  );
  for (const a of ordered) {
    const entry: Record<string, unknown> = {
      control: a.control,
      control_version: a.controlVersion,
      subject: a.subject,
      outcome: a.outcome,
      mode: a.mode,
      rung: a.rung,
      evidence: a.evidence.map(evidencePointerToJson),
      crosswalk: a.crosswalk,
    };
    if (a.deviation) {
      entry.deviation = deviationDetail(a.deviation, byControl);
    }
    if (MANUAL_MODES.has(a.mode) && a.outcome === "not_assessed") {
      entry.manual_checklist_note = note;
    }
    clauses.push(entry);
    for (const xw of a.crosswalk) {
      const framework = pyStr(xw.framework ?? "");
      const clause = pyStr(xw.clause ?? "");
      const clauseMap = byClause.get(framework) ?? new Map<string, Set<string>>();
      byClause.set(framework, clauseMap);
      const ids = clauseMap.get(clause) ?? new Set<string>();
      clauseMap.set(clause, ids);
      ids.add(a.control);
    }
  }

  const byClauseOut: Record<string, Record<string, string[]>> = {};
  for (const framework of [...byClause.keys()].sort(byteCompare)) {
    const clauseMap = byClause.get(framework) as Map<string, Set<string>>;
    const out: Record<string, string[]> = {};
    for (const clause of [...clauseMap.keys()].sort(byteCompare)) {
      out[clause] = [...(clauseMap.get(clause) as Set<string>)].sort(byteCompare);
    }
    byClauseOut[framework] = out;
  }
  return { clauses, by_clause: byClauseOut, counts: counts ?? aggregate(assertions) };
}
