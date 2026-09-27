/**
 * Blind spots: the one missing record type that would unlock the most checks (Hill 2).
 *
 * `computeBlindSpots` groups every `insufficient_evidence` assertion by the normalized (event, class)
 * requirement its subject's own events did and did not satisfy, and ranks each group by how many
 * checks supplying it would provably unlock. A faithful port of the Python reference
 * (`agentce/blind_spots.py`); RFC 0008 is the operative spec for every rule here. Read-only over
 * `(assertions, profile, catalogs, events)`: it never affects an outcome or the verdict.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Assertion } from "./assertions";
import { SELF_REPORT_EQUIVALENT_CLASSES, classOk, indexBySubject, requirementMet } from "./assess";
import type { Catalog, ControlSpec } from "./catalog";
import type { Profile } from "./profile";
import { byteCompare } from "./util";

type Event = Record<string, unknown>;

/** The seven OpenTelemetry-GenAI-shaped trace events (RFC 0008 Sec.3): a self-report requirement for
 * one of these is rung 1 ("what happened"); every other self-report requirement is rung 2. */
const OTEL_SHAPED_EVENTS = new Set([
  "ModelCall",
  "ToolCall",
  "ResourceAccess",
  "MemoryRead",
  "MemoryWrite",
  "SessionStart",
  "SessionEnd",
]);

/** (ownerKey, stepKind) by rung (RFC 0008 Sec.3; `VALUE-PROP.md`'s evidence-ladder table verbatim). */
const OWNER_AND_STEP_BY_RUNG: Record<number, [string, string]> = {
  1: ["agent_team", "code_change"],
  2: ["agent_team", "code_change"],
  3: ["platform_or_security", "request"],
  4: ["ticketing_or_iam", "request"],
};

function normalizeClass(cls: string): string {
  return SELF_REPORT_EQUIVALENT_CLASSES.has(cls) ? "self_report" : cls;
}

type EventProducers = Record<string, Record<string, string>>;

let cachedEventProducers: EventProducers | null = null;

/** The vendored, per-event map of adapter name to that adapter's default trust class (RFC 0008
 * Sec.5), kept in sync with every `adapters/*\/support-matrix.yaml` by
 * `tools/support_matrix_sync_check.py`. */
function eventProducers(): EventProducers {
  if (cachedEventProducers === null) {
    const path = join(__dirname, "..", "data", "support_matrices", "event_producers.json");
    cachedEventProducers = JSON.parse(readFileSync(path, "utf-8")) as EventProducers;
  }
  return cachedEventProducers;
}

/** The evidence-ladder rung for a grouping key: by who can actually supply `event` today (RFC 0008
 * Sec.3), not only the catalog's literal required class. */
function ladderRung(event: string, normalizedClass: string): number {
  if (normalizedClass === "independent_system") return 4;
  if (normalizedClass === "enforcement_point") return 3;
  const producers = eventProducers()[event] ?? {};
  const classes = Object.values(producers);
  if (classes.length === 0) {
    // No adapter declares this event at all: a generic, adapter-free event (Decision/Outcome/
    // Incident) any agent can emit directly -- not a vacuous rung 3.
    return 2;
  }
  if (classes.every((c) => c === "enforcement_point")) {
    // The catalog would accept self-reported evidence in principle, but today only a gateway,
    // policy engine, identity provider, or supply-chain verifier ever produces this event.
    return 3;
  }
  return OTEL_SHAPED_EVENTS.has(event) ? 1 : 2;
}

/** Only the adapters whose default class actually satisfies this key (RFC 0008 Sec.3), reusing
 * `classOk` -- never every adapter that merely declares the event. */
function supplyingAdapters(event: string, normalizedClass: string): string[] {
  const producers = eventProducers()[event] ?? {};
  return Object.entries(producers)
    .filter(([, sourceClass]) => classOk(sourceClass, normalizedClass))
    .map(([adapter]) => adapter)
    .sort(byteCompare);
}

export interface CheckRef {
  subject: string;
  catalog: string;
  control: string;
  control_version: string;
}

function checkSortKey(ref: CheckRef): [string, string, string, string] {
  return [ref.subject, ref.catalog, ref.control, ref.control_version];
}

function compareCheckRefs(a: CheckRef, b: CheckRef): number {
  const ka = checkSortKey(a);
  const kb = checkSortKey(b);
  for (let i = 0; i < ka.length; i++) {
    const c = byteCompare(ka[i] as string, kb[i] as string);
    if (c !== 0) return c;
  }
  return 0;
}

/** The exact `(subject.id, catalog.id, control)` sequence `assessSubjects` builds, in its own
 * iteration order (RFC 0008 Sec.6) -- so `assertions` (that same loop's own output) can be paired
 * with the `(catalog, control)` it came from by position, without a `catalog` field on `Assertion`
 * itself. */
function replayTriples(
  profile: Profile,
  catalogs: Catalog[],
): Array<[string, string, ControlSpec]> {
  const triples: Array<[string, string, ControlSpec]> = [];
  for (const subject of profile.subjects) {
    for (const catalog of catalogs) {
      for (const control of catalog.controls) {
        triples.push([subject.id, catalog.id, control]);
      }
    }
  }
  return triples;
}

export interface BlindSpot {
  event: string;
  class: string;
  ladder_rung: number;
  owner_key: string;
  step_kind: string;
  supplying_adapters: string[];
  checks_unlocked: number;
  unlocked_checks: CheckRef[];
  needed_by: number;
  needed_by_checks: CheckRef[];
}

export interface BlindSpots {
  blind_spots: BlindSpot[];
  no_population: CheckRef[];
}

/** Return `{blindSpots: [...], noPopulation: [...]}` for `assertions`.
 *
 * `assertions` must be `assessSubjects`'s own output, in its own order -- that order is meaningful
 * input here (RFC 0008 Sec.6), not incidental: it is replayed positionally against
 * `profile`/`catalogs` to recover each assertion's `(catalog, control)` origin without a schema
 * change to `Assertion`. A pure function otherwise: no I/O beyond reading the vendored
 * `event_producers.json`, no network, no clock. */
export function computeBlindSpots(
  assertions: Assertion[],
  profile: Profile,
  catalogs: Catalog[],
  events: Event[],
): BlindSpots {
  const triples = replayTriples(profile, catalogs);
  if (triples.length !== assertions.length) {
    throw new Error(
      `computeBlindSpots: ${assertions.length} assertions but ${triples.length} (subject, catalog, control) triples replayed from profile/catalogs -- assertions must be assessSubjects' own output.`,
    );
  }

  const eventsBySubject = indexBySubject(events);
  // Keyed by `JSON.stringify([...])`, never a delimiter-joined string: two distinct triples whose
  // fields happen to abut at a boundary (e.g. one field ending where the next begins) must never
  // collide into the same cache/group entry (the exact class of bug 18.4's activity port fixed for
  // its own tuple keys).
  const metCache = new Map<string, boolean>();
  const met = (subjectId: string, requirement: Record<string, string>): boolean => {
    const event = requirement.event ?? "";
    const cls = requirement.class ?? "any";
    const key = JSON.stringify([subjectId, event, cls]);
    let cached = metCache.get(key);
    if (cached === undefined) {
      cached = requirementMet(eventsBySubject.get(subjectId) ?? [], requirement);
      metCache.set(key, cached);
    }
    return cached;
  };

  const groups = new Map<
    string,
    { key: [string, string]; unlocked: CheckRef[]; needed: CheckRef[] }
  >();
  const noPopulation: CheckRef[] = [];

  for (let i = 0; i < triples.length; i++) {
    const [subjectId, catalogId, control] = triples[i] as [string, string, ControlSpec];
    const a = assertions[i] as Assertion;
    if (
      a.subject !== subjectId ||
      a.control !== control.id ||
      a.controlVersion !== control.version
    ) {
      throw new Error(
        `computeBlindSpots: assertion is (${a.subject}, ${a.control}, ${a.controlVersion}) but the replayed sequence expects (${subjectId}, ${catalogId}, ${control.id}, ${control.version}) -- assertions is not assessSubjects' own order.`,
      );
    }
    if (a.outcome !== "insufficient_evidence") continue;
    const missing = control.minimumEvidence.filter((r) => !met(subjectId, r));
    const normalizedByKey = new Map<string, [string, string]>();
    for (const r of missing) {
      const tuple: [string, string] = [r.event ?? "", normalizeClass(r.class ?? "any")];
      normalizedByKey.set(JSON.stringify(tuple), tuple);
    }
    const normalizedKeys = [...normalizedByKey.values()].sort((x, y) => {
      const byEvent = byteCompare(x[0], y[0]);
      return byEvent !== 0 ? byEvent : byteCompare(x[1], y[1]);
    });
    const checkRef: CheckRef = {
      subject: subjectId,
      catalog: catalogId,
      control: control.id,
      control_version: control.version,
    };
    if (normalizedKeys.length === 0) {
      // Every minimum_evidence entry is actually satisfied, yet the shape's own structural
      // population was still empty (RFC 0008 Sec.4) -- checked regardless of population[0].
      noPopulation.push(checkRef);
      continue;
    }
    // The minimum-evidence branch's non-empty missing set is provably sufficient only when exactly
    // one key is missing (no partial credit); the empty-population branch's non-empty missing set
    // is informative but never provably sufficient on its own, so it always lands in needed_by.
    const unlocked = a.population[0] > 0 && normalizedKeys.length === 1;
    for (const [event, cls] of normalizedKeys) {
      const groupKey = JSON.stringify([event, cls]);
      let group = groups.get(groupKey);
      if (group === undefined) {
        group = { key: [event, cls], unlocked: [], needed: [] };
        groups.set(groupKey, group);
      }
      (unlocked ? group.unlocked : group.needed).push(checkRef);
    }
  }

  const blindSpots: BlindSpot[] = [];
  for (const {
    key: [event, cls],
    unlocked,
    needed,
  } of groups.values()) {
    const rung = ladderRung(event, cls);
    const [ownerKey, stepKind] = OWNER_AND_STEP_BY_RUNG[rung] as [string, string];
    const unlockedSorted = [...unlocked].sort(compareCheckRefs);
    const neededSorted = [...needed].sort(compareCheckRefs);
    blindSpots.push({
      event,
      class: cls,
      ladder_rung: rung,
      owner_key: ownerKey,
      step_kind: stepKind,
      supplying_adapters: supplyingAdapters(event, cls),
      checks_unlocked: unlockedSorted.length,
      unlocked_checks: unlockedSorted,
      needed_by: neededSorted.length,
      needed_by_checks: neededSorted,
    });
  }
  blindSpots.sort((a, b) => {
    if (a.checks_unlocked !== b.checks_unlocked) return b.checks_unlocked - a.checks_unlocked;
    if (a.needed_by !== b.needed_by) return b.needed_by - a.needed_by;
    if (a.ladder_rung !== b.ladder_rung) return a.ladder_rung - b.ladder_rung;
    const byEvent = byteCompare(a.event, b.event);
    if (byEvent !== 0) return byEvent;
    return byteCompare(a.class, b.class);
  });
  noPopulation.sort(compareCheckRefs);
  return { blind_spots: blindSpots, no_population: noPopulation };
}
