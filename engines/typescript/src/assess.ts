/**
 * Assemble assertions by evaluating a catalog against each subject (SPEC §7, §9).
 *
 * For every subject the engine builds that subject's graph, then for every control in every applied
 * catalog it decides the outcome: `not_applicable` when the control's role does not match or the shape
 * targets nothing, `insufficient_evidence` when the control's minimum evidence (event type and source
 * class) is absent, and otherwise the structural verdict against the control's tolerance. Supporting
 * verdicts carry evidence pointers to the focus nodes so DC-5 holds. Only rung-2 structural controls
 * are evaluated in Phase 1; other rungs become `not_assessed`. This is a faithful port of the reference.
 */

import { effectiveRoles } from "./applicability";
import { type Assertion, type EvidencePointer, makeAssertion } from "./assertions";
import { sha256Hex } from "./canonical";
import { type Catalog, type ControlSpec, shapeFor } from "./catalog";
import type { DomainBinding } from "./domain";
import { buildGraph } from "./graph";
import { eventIri } from "./iri";
import type { Profile, Subject } from "./profile";
import type { GraphStore } from "./store";
import { evaluateShape, violationToJson, withinTolerance } from "./structural";
import { byteCompare } from "./util";

type Event = Record<string, unknown>;

const EVIDENCE_CAP = 20;
const ROLES = ["deployer", "provider"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function roleApplies(roles: Set<string>, appliesToRoles: string[]): boolean {
  const targets = new Set(appliesToRoles);
  if (targets.has("both")) {
    for (const r of ROLES) {
      targets.add(r);
    }
  }
  for (const r of roles) {
    if (targets.has(r)) {
      return true;
    }
  }
  return false;
}

function classOk(observed: string, required: string): boolean {
  return required === "any" || required === "self_report" || observed === required;
}

function eventType(event: Event): string {
  const data = event.data;
  return isRecord(data) && typeof data["@type"] === "string" ? data["@type"] : "";
}

function hasMinimumEvidence(events: Event[], minimum: Array<Record<string, string>>): boolean {
  for (const requirement of minimum) {
    const wantType = requirement.event;
    const requiredClass = requirement.class ?? "any";
    if (
      !events.some(
        (e) =>
          eventType(e) === wantType && classOk(String(e.agentcesourceclass ?? ""), requiredClass),
      )
    ) {
      return false;
    }
  }
  return true;
}

function window(profile: Profile, events: Event[]): [string, string] {
  const w = profile.observationWindow;
  if ("start" in w && "end" in w) {
    return [w.start as string, w.end as string];
  }
  const times = events
    .map((e) => String(e.time ?? ""))
    .filter((t) => t)
    .sort(byteCompare);
  if (times.length > 0) {
    return [times[0] as string, times[times.length - 1] as string];
  }
  return ["1970-01-01T00:00:00Z", "1970-01-01T00:00:00Z"];
}

function evidence(eventsByIri: Map<string, Event>, focusNodes: string[]): EvidencePointer[] {
  const pointers: EvidencePointer[] = [];
  for (const focus of focusNodes.slice(0, EVIDENCE_CAP)) {
    const event = eventsByIri.get(focus);
    if (event === undefined) {
      continue;
    }
    pointers.push({
      ref: focus,
      digest: `sha256:${sha256Hex(event)}`,
      sourceClass: String(event.agentcesourceclass || "self_report"),
    });
  }
  return pointers;
}

function assertControl(
  store: GraphStore,
  catalog: Catalog,
  control: ControlSpec,
  subject: Subject,
  roles: Set<string>,
  events: Event[],
  eventsByIri: Map<string, Event>,
  win: [string, string],
): Assertion {
  const base = {
    control: control.id,
    controlVersion: control.version,
    subject: subject.id,
    rung: control.rung,
    mode: control.mode,
    window: win,
  };
  if (!roleApplies(roles, control.appliesToRoles)) {
    return makeAssertion({ ...base, outcome: "not_applicable", population: [0, 0] });
  }

  const shape = shapeFor(catalog, control);
  if (control.rung !== 2 || shape === null) {
    return makeAssertion({ ...base, outcome: "not_assessed", population: [0, 0] });
  }

  const [applicable, failing, violations] = evaluateShape(store, shape, catalog.shapes, control.id);
  if (applicable.length === 0) {
    return makeAssertion({ ...base, outcome: "not_applicable", population: [0, 0] });
  }
  if (!hasMinimumEvidence(events, control.minimumEvidence)) {
    return makeAssertion({
      ...base,
      outcome: "insufficient_evidence",
      population: [applicable.length, 0],
    });
  }

  const conformant = withinTolerance(applicable.length, failing.size, control.tolerance);
  const focusForEvidence = failing.size > 0 ? [...failing].sort(byteCompare) : applicable;
  return makeAssertion({
    ...base,
    outcome: conformant ? "conformant" : "non-conformant",
    population: [applicable.length, failing.size],
    violations: violations.map(violationToJson),
    evidence: evidence(eventsByIri, focusForEvidence),
    sourceClassSatisfied: true,
    evidenceStrength:
      typeof control.raw.evidence_strength === "string" ? control.raw.evidence_strength : null,
  });
}

/** Group `accepted` by subject id in one pass over the list. */
function indexBySubject(accepted: Event[]): Map<string, Event[]> {
  const index = new Map<string, Event[]>();
  for (const event of accepted) {
    const key = String(event.subject ?? "");
    const events = index.get(key);
    if (events) {
      events.push(event);
    } else {
      index.set(key, [event]);
    }
  }
  return index;
}

/** Evaluate every catalog control against every subject and return the assertions. */
export function assessSubjects(
  accepted: Event[],
  profile: Profile,
  catalogs: Catalog[],
  domain: DomainBinding,
): Assertion[] {
  const assertions: Assertion[] = [];
  const eventsBySubject = indexBySubject(accepted);
  for (const subject of profile.subjects) {
    const subjectEvents = eventsBySubject.get(subject.id) ?? [];
    const store = buildGraph(subjectEvents, { domain });
    const eventsByIri = new Map<string, Event>();
    for (const event of subjectEvents) {
      if ("id" in event) {
        eventsByIri.set(eventIri(String(event.id)), event);
      }
    }
    const roles = new Set(effectiveRoles(subject.role));
    const win = window(profile, subjectEvents);
    for (const catalog of catalogs) {
      for (const control of catalog.controls) {
        assertions.push(
          assertControl(store, catalog, control, subject, roles, subjectEvents, eventsByIri, win),
        );
      }
    }
  }
  return assertions;
}

/** Outcomes that count as a verdict was reached at all (a run that assessed nothing reaches none). */
const VERDICT_OUTCOMES = new Set(["conformant", "non-conformant", "insufficient_evidence"]);

/** True when no assertion reached a verdict (see {@link VERDICT_OUTCOMES}). */
export function evaluatedNothing(assertions: Assertion[]): boolean {
  return !assertions.some((a) => VERDICT_OUTCOMES.has(a.outcome));
}
