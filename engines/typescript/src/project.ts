/**
 * The project view: every agent's records in one run, side by side (Hill 7).
 *
 * A multi-subject run already assesses every subject; this module rolls that up into one ordered,
 * per-agent view -- a summary row per subject (declared or not), the undeclared agents nobody's
 * profile named, and the top blind spots across the whole project, each naming which agents it
 * touches. Read-only over `assessSubjects`'/`summarizeActivity`'s/`computeBlindSpots`'s own outputs:
 * it never re-derives a verdict or re-scans events. A faithful port of the Python reference
 * (`agentce/project.py`); field names, key order, and sort order match exactly so `project.json` is
 * byte-identical across engines.
 */

import type { Assertion } from "./assertions";
import type { BlindSpot, BlindSpots, CheckRef } from "./blindSpots";
import type { Profile } from "./profile";
import { byteCompare } from "./util";
import { summarize } from "./verdict";

/** How many blind spots `computeProjectView` lists at the top level. */
export const MAX_TOP_GAPS = 5;

/** The distinct subjects a global blind-spot entry's own check-refs name, byte-sorted. */
function entrySubjects(entry: {
  unlocked_checks: CheckRef[];
  needed_by_checks: CheckRef[];
}): string[] {
  return [
    ...new Set([
      ...entry.unlocked_checks.map((cr) => cr.subject),
      ...entry.needed_by_checks.map((cr) => cr.subject),
    ]),
  ].sort(byteCompare);
}

/**
 * Group `computeBlindSpots`'s global `blind_spots` list by the subjects each entry actually names,
 * RE-SCOPING each entry's `unlocked_checks`/`needed_by_checks`/`checks_unlocked`/`needed_by` to that
 * one subject rather than copying the global counts (a global entry spanning two subjects must not
 * report the other subject's checks or count under either one).
 */
export function blindSpotsBySubject(blindSpots: BlindSpots): Map<string, BlindSpot[]> {
  const bySubject = new Map<string, BlindSpot[]>();
  for (const entry of blindSpots.blind_spots) {
    for (const subject of entrySubjects(entry)) {
      const ownUnlocked = entry.unlocked_checks.filter((cr) => cr.subject === subject);
      const ownNeeded = entry.needed_by_checks.filter((cr) => cr.subject === subject);
      const ownEntry: BlindSpot = {
        ...entry,
        unlocked_checks: ownUnlocked,
        needed_by_checks: ownNeeded,
        checks_unlocked: ownUnlocked.length,
        needed_by: ownNeeded.length,
      };
      const list = bySubject.get(subject);
      if (list === undefined) {
        bySubject.set(subject, [ownEntry]);
      } else {
        list.push(ownEntry);
      }
    }
  }
  return bySubject;
}

/**
 * Group `computeBlindSpots`'s global `no_population` list by each entry's own `subject` field:
 * unlike `blind_spots`, every `no_population` entry already names exactly one subject, so no
 * re-scoping is needed, only grouping.
 */
export function noPopulationBySubject(noPopulation: CheckRef[]): Map<string, CheckRef[]> {
  const bySubject = new Map<string, CheckRef[]>();
  for (const entry of noPopulation) {
    const list = bySubject.get(entry.subject);
    if (list === undefined) {
      bySubject.set(entry.subject, [entry]);
    } else {
      list.push(entry);
    }
  }
  return bySubject;
}

export interface ProjectAgentRow {
  id: string;
  declared: boolean;
  summary: { verdict: string; counts: Record<string, number>; top_gaps: unknown[] };
  blind_spots_count: number;
  agents_observed: string[];
}

export interface ProjectView {
  agents: ProjectAgentRow[];
  undeclared_agents: string[];
  top_gaps: Array<BlindSpot & { agents: string[] }>;
}

/**
 * Return `{agents, undeclared_agents, top_gaps}` for a multi-agent run.
 *
 * `blindSpots` is `computeBlindSpots`'s own single, global return value (called once, unchanged) --
 * this function derives every per-agent view from it, it never re-computes blind spots per subject.
 * Deterministic: no wall-clock, no locale, no filesystem-order dependency.
 */
export function computeProjectView(
  assertions: Assertion[],
  profile: Profile,
  declaredSubjectIds: Set<string>,
  activityBySubject: Map<string, { agents: string[] }>,
  blindSpots: BlindSpots,
): ProjectView {
  const bySubject = blindSpotsBySubject(blindSpots);
  const subjectIds = [
    ...new Set([...assertions.map((a) => a.subject), ...profile.subjects.map((s) => s.id)]),
  ].sort(byteCompare);

  const assertionsBySubject = new Map<string, Assertion[]>();
  for (const assertion of assertions) {
    const list = assertionsBySubject.get(assertion.subject);
    if (list) {
      list.push(assertion);
    } else {
      assertionsBySubject.set(assertion.subject, [assertion]);
    }
  }
  const agents: ProjectAgentRow[] = subjectIds.map((subjectId) => {
    const subjectAssertions = assertionsBySubject.get(subjectId) ?? [];
    const summary = summarize(subjectAssertions);
    return {
      id: subjectId,
      declared: declaredSubjectIds.has(subjectId),
      summary: { verdict: summary.verdict, counts: summary.counts, top_gaps: summary.topGaps },
      blind_spots_count: (bySubject.get(subjectId) ?? []).length,
      agents_observed: activityBySubject.get(subjectId)?.agents ?? [],
    };
  });

  const undeclaredAgents = [...new Set(agents.flatMap((row) => row.agents_observed))]
    .filter((agentId) => !declaredSubjectIds.has(agentId))
    .sort(byteCompare);

  const topGaps = blindSpots.blind_spots.slice(0, MAX_TOP_GAPS).map((entry) => ({
    ...entry,
    agents: entrySubjects(entry),
  }));

  return { agents, undeclared_agents: undeclaredAgents, top_gaps: topGaps };
}
