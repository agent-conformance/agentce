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
import { type Catalog, type ControlSpec, evidenceShapeFor, shapeFor } from "./catalog";
import type { DomainBinding } from "./domain";
import { buildGraph } from "./graph";
import type { Withheld } from "./ingest";
import { eventIri } from "./iri";
import type { Profile, Subject } from "./profile";
import { compareDate, parseDate } from "./readiness";
import { BELOW_MINIMUM_OUTCOME, SourceClassJudge, belowMinimum } from "./sourceClass";
import type { GraphStore } from "./store";
import { evaluateShape, violationToJson, withinTolerance } from "./structural";
import { byteCompare, pyStr } from "./util";

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

/** A required class of `any` or `self_report` is satisfied by any observed source class (mirrored by
 * `blindSpots.ts`'s `normalizeClass`, which groups both onto the same blind-spot key). */
export const SELF_REPORT_EQUIVALENT_CLASSES = new Set(["any", "self_report"]);

/** The predicates whose value the SPEC §10.4 human-actor rule decides: a human's approval or review of a decision,
 * enough human approvers, a human intervention, and the review half of a risk review (18.186). */
const HUMAN_ACTOR_PATHS = new Set([
  "agentce:approvedBy",
  "agentce:reviewedBy",
  "agentce:oversightCoverageComplete",
  "agentce:interventionByHuman",
  "agentce:riskReviewed",
]);

export function classOk(observed: string, required: string): boolean {
  return SELF_REPORT_EQUIVALENT_CLASSES.has(required) || observed === required;
}

function eventType(event: Event): string {
  const data = event.data;
  return isRecord(data) && typeof data["@type"] === "string" ? data["@type"] : "";
}

/** Whether `events` carries at least one event satisfying one `minimum_evidence` entry -- factored
 * out of `hasMinimumEvidence` so the blind-spots ranking (Hill 2) can report, per requirement, which
 * of a control's minimum-evidence entries this subject's own events did and did not satisfy, using
 * the exact same rule the assessment itself used to reach its outcome. */
export function requirementMet(events: Event[], requirement: Record<string, string>): boolean {
  const wantType = requirement.event;
  const requiredClass = requirement.class ?? "any";
  return events.some(
    (e) => eventType(e) === wantType && classOk(String(e.agentcesourceclass ?? ""), requiredClass),
  );
}

function hasMinimumEvidence(events: Event[], minimum: Array<Record<string, string>>): boolean {
  return minimum.every((requirement) => requirementMet(events, requirement));
}

/** What an insufficient_evidence `assertion` lacks: each `minimum_evidence` entry `events` do not meet,
 * then a record of each type whose class held a focus below the control's `min_source_class` (HR-5), at
 * that minimum. The one reading the blind spots name the gap with. */
export function missingEvidence(
  control: ControlSpec,
  events: Event[],
  assertion: Assertion,
): Array<Record<string, string>> {
  return [
    ...control.minimumEvidence.filter((r) => !requirementMet(events, r)),
    ...(assertion.classGap ?? []).map((kind) => ({ event: kind, class: control.minSourceClass })),
  ];
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

/**
 * Carry each control's own crosswalk entries into every assertion built for it (SPEC §7.3).
 *
 * `verified` is read from the control's `verified_against_text` and coerced to a real boolean --
 * never invented, and never set true by the engine itself; only a human with access to the licensed
 * standard text may flip that flag in the catalog source (human action H4).
 */
/** The control's family: the letter prefix of its own id (SPEC §7.1; mirrors the Python reference). */
function familyOf(controlId: string): string {
  const dash = controlId.indexOf("-");
  return dash === -1 ? controlId : controlId.slice(0, dash);
}

function crosswalkFor(control: ControlSpec): Array<Record<string, string | boolean>> {
  const raw = control.raw.crosswalk;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.map((entry) => {
    const e = isRecord(entry) ? entry : {};
    return {
      framework: e.framework as string,
      clause: e.clause as string,
      verified: e.verified_against_text === true,
    };
  });
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
  judge: () => SourceClassJudge,
  applicabilityDeclared: boolean,
): Assertion {
  const base = {
    control: control.id,
    controlVersion: control.version,
    subject: subject.id,
    rung: control.rung,
    mode: control.mode,
    window: win,
    severity: control.severity,
    family: familyOf(control.id),
    crosswalk: crosswalkFor(control),
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
    // Where nobody declared what the agent decides (a profile derived from the records), an empty
    // population says the records show none, not that the control does not apply.
    return makeAssertion({
      ...base,
      outcome: applicabilityDeclared ? "not_applicable" : "insufficient_evidence",
      population: [0, 0],
    });
  }
  if (!hasMinimumEvidence(events, control.minimumEvidence)) {
    return makeAssertion({
      ...base,
      outcome: "insufficient_evidence",
      population: [applicable.length, 0],
    });
  }

  const conformant = withinTolerance(applicable.length, failing.size, control.tolerance);
  const evidenceShape = evidenceShapeFor(catalog, control);
  if (conformant && evidenceShape !== null) {
    const [, unjudged] = evaluateShape(store, evidenceShape, catalog.shapes, control.id);
    if (unjudged.size > 0) {
      // A focus the control cannot judge from these records never counts as a pass; a failure
      // elsewhere still reads non-conformant (above).
      return makeAssertion({
        ...base,
        outcome: "insufficient_evidence",
        population: [applicable.length, 0],
        evidence: evidence(eventsByIri, [...unjudged].sort(byteCompare)),
      });
    }
  }
  // HR-5: a pass counts only when every focus's judged class meets the control's minimum (SPEC §6.4). The
  // rule never runs on a non-conformant verdict, which stays non-conformant.
  const [below, kinds] = conformant
    ? belowMinimum(judge, applicable, control.minSourceClass, control.sourceClassFrom)
    : [[], []];
  const isBelow = below.length > 0;
  const outcome = isBelow ? BELOW_MINIMUM_OUTCOME : conformant ? "conformant" : "non-conformant";
  // Below the minimum, the outcome rests on the below foci alone: none counts as failing, and no verdict is given.
  const focusForEvidence = isBelow
    ? below
    : failing.size > 0
      ? [...failing].sort(byteCompare)
      : applicable;
  return makeAssertion({
    ...base,
    outcome,
    population: [applicable.length, isBelow ? 0 : failing.size],
    violations: isBelow ? [] : violations.map(violationToJson),
    evidence: evidence(eventsByIri, focusForEvidence),
    sourceClassSatisfied: !isBelow,
    evidenceStrength:
      !isBelow && typeof control.raw.evidence_strength === "string"
        ? control.raw.evidence_strength
        : null,
    classGap: kinds,
    humanActorFailed:
      outcome === "non-conformant" &&
      violations.some((v) => store.humanActorFailed.has(v.focus) && readsHumanActor(v.path)),
  });
}

/** Whether a rendered shape path passes through a predicate whose value the SPEC §10.4 human-actor rule decides. */
export function readsHumanActor(path: string): boolean {
  return path.split(/[\s()|^]+/).some((token) => HUMAN_ACTOR_PATHS.has(token));
}

/** `make`, called at most once: on the first call, and its value returned from then on. */
function lazy<T>(make: () => T): () => T {
  let made: { value: T } | null = null;
  return () => {
    made ??= { value: make() };
    return made.value;
  };
}

/** Group `accepted` by subject id in one pass over the list. */
export function indexBySubject(accepted: Event[]): Map<string, Event[]> {
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

function eventsByIriOf(events: Event[]): Map<string, Event> {
  const byIri = new Map<string, Event>();
  for (const event of events) {
    if ("id" in event) {
      byIri.set(eventIri(String(event.id)), event);
    }
  }
  return byIri;
}

/** The subject's held events plus the readable withheld ones (18.119). A withheld record whose id is taken (a
 * duplicate_id) joins under a fresh id, so it is judged as the record it is instead of merging with the held one. */
function counterfactual(subjectEvents: Event[], readable: Event[]): Event[] {
  const taken = new Set(subjectEvents.map((e) => String(e.id)));
  const events = [...subjectEvents];
  readable.forEach((original, position) => {
    let event = original;
    const eventId = String(event.id);
    if (taken.has(eventId)) {
      event = { ...event, id: `${eventId}#withheld-${position}` };
    }
    taken.add(String(event.id));
    events.push(event);
  });
  return events;
}

function withheldEvidence(withheld: Withheld[]): EvidencePointer[] {
  const pointers = new Map<string, EvidencePointer>();
  for (const w of withheld) {
    if (w.record.eventId === undefined || w.digest === null) {
      continue;
    }
    const pointer = {
      ref: eventIri(w.record.eventId),
      digest: `sha256:${w.digest}`,
      sourceClass:
        w.event !== null ? String(w.event.agentcesourceclass || "self_report") : "self_report",
    };
    pointers.set(`${pointer.ref}\u0000${pointer.digest}\u0000${pointer.sourceClass}`, pointer);
  }
  return [...pointers.keys()]
    .sort(byteCompare)
    .slice(0, EVIDENCE_CAP)
    .map((key) => pointers.get(key) as EvidencePointer);
}

/** The message key naming an insufficient_evidence outcome whose withheld records read non-conformant (18.149). */
export const WITHHELD_KEY = "withheld_reads_non_conformant";

/** A control quarantine may have flattered reads insufficient_evidence naming the withheld records (18.119).
 * `nonConformant` says judging the readable withheld records with the held ones reads non-conformant (18.149). */
function notEnoughWithWithheld(
  assertion: Assertion,
  withheld: Withheld[],
  nonConformant: boolean,
): Assertion {
  return {
    ...assertion,
    outcome: "insufficient_evidence",
    population: [assertion.population[0], 0],
    violations: [],
    evidence: withheldEvidence(withheld),
    sourceClassSatisfied: null,
    evidenceStrength: null,
    withheldNonConformant: nonConformant,
    classGap: [],
  };
}

/**
 * Evaluate every catalog control against every subject and return the assertions.
 * `applicabilityDeclared` is false only for a records-folder run, whose profile nobody declared.
 *
 * `withheld` is required, so every caller decides what ingest withheld; it is ingest's quarantined records that may hold evidence (18.119): quarantine never gives a better
 * outcome. A control that reads conformant, or not_applicable for want of a population, reads insufficient_evidence
 * naming them when a record ingest could not read may belong to the subject, or when judging the subject's readable
 * withheld records with its held ones changes that outcome.
 */
export function assessSubjects(
  accepted: Event[],
  profile: Profile,
  catalogs: Catalog[],
  domain: DomainBinding,
  applicabilityDeclared: boolean,
  withheld: Withheld[],
): Assertion[] {
  const assertions: Assertion[] = [];
  const eventsBySubject = indexBySubject(accepted);
  for (const subject of profile.subjects) {
    const subjectEvents = eventsBySubject.get(subject.id) ?? [];
    const store = buildGraph(subjectEvents, { domain });
    const eventsByIri = eventsByIriOf(subjectEvents);
    const roles = new Set(effectiveRoles(subject.role));
    const win = window(profile, subjectEvents);
    const own = withheld.filter((w) => w.subject === null || w.subject === subject.id);
    // A readable record always names its subject (the schema requires it), so it is this subject's.
    const unreadable = own.some((w) => w.event === null);
    const readable = own.map((w) => w.event).filter((e): e is Event => e !== null);
    // The comparison runs whenever a readable record is withheld, even beside an unreadable one: the unreadable record
    // already decides the outcome, but the exit code still needs what the readable ones show (18.149).
    const cfEvents = readable.length > 0 ? counterfactual(subjectEvents, readable) : [];
    const cfStore = cfEvents.length > 0 ? buildGraph(cfEvents, { domain }) : store;
    const cfByIri = eventsByIriOf(cfEvents);
    // Each graph's judge is built once, on the first control whose minimum it may fall below.
    const judge = lazy(() => new SourceClassJudge(store, eventsByIri));
    const cfJudge = lazy(() => new SourceClassJudge(cfStore, cfByIri));
    for (const catalog of catalogs) {
      for (const control of catalog.controls) {
        let assertion = assertControl(
          store,
          catalog,
          control,
          subject,
          roles,
          subjectEvents,
          eventsByIri,
          win,
          judge,
          applicabilityDeclared,
        );
        const flattered =
          own.length > 0 &&
          (assertion.outcome === "conformant" ||
            (assertion.outcome === "not_applicable" && roleApplies(roles, control.appliesToRoles)));
        // Held records that read insufficient_evidence may lack only what a late record supplies, so a
        // readable withheld record is judged with them too: it can still read non-conformant (18.149).
        const short = readable.length > 0 && assertion.outcome === "insufficient_evidence";
        if (!(flattered || short)) {
          assertions.push(assertion);
          continue;
        }
        const withWithheld =
          readable.length === 0
            ? assertion.outcome
            : assertControl(
                cfStore,
                catalog,
                control,
                subject,
                roles,
                cfEvents,
                cfByIri,
                win,
                cfJudge,
                applicabilityDeclared,
              ).outcome;
        if ((unreadable && flattered) || withWithheld !== assertion.outcome) {
          assertion = notEnoughWithWithheld(assertion, own, withWithheld === "non-conformant");
        }
        assertions.push(assertion);
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

/** Index a deviation register by its `control` field (`assess.deviations_by_control`). Shared by every
 * reader of an already-loaded register (`applyDeviations`, the expired-entry limitation text,
 * `renderOscal`, `computeAuditorView`) so the keying rule lives in one place; a later entry for the
 * same control replaces an earlier one, as a Python dict comprehension does. */
export function deviationsByControl(
  deviations: Record<string, unknown>[] | null | undefined,
): Map<string, Record<string, unknown>> {
  const byControl = new Map<string, Record<string, unknown>>();
  for (const entry of deviations ?? []) {
    byControl.set(pyStr(entry.control ?? null), entry);
  }
  return byControl;
}

/** Apply an already-linted deviation register to `assertions` (SPEC §13.3.4 Stage 2; a port of
 * `assess.apply_deviations`): every `non-conformant` assertion whose control has a matching, unexpired
 * entry becomes `partial` and carries that control's id as `deviation`. Pure: never mutates its inputs
 * and never reads a clock (`asOf` is the caller's window end). An entry whose `expiry` is before
 * `asOf` is never applied; its control id is returned in `expired` so the caller reports it as a
 * limitation. */
export function applyDeviations(
  assertions: Assertion[],
  deviations: Record<string, unknown>[],
  asOf: string,
): { assertions: Assertion[]; expired: string[]; notWaivable: string[] } {
  const byControl = deviationsByControl(deviations);
  const asOfDate = parseDate(asOf);
  const expired: string[] = [];
  const notWaivable: string[] = [];
  const applied = assertions.map((assertion) => {
    const entry =
      assertion.outcome === "non-conformant" ? byControl.get(assertion.control) : undefined;
    if (entry === undefined) {
      return assertion;
    }
    // SPEC §10.4: the deviation register cannot waive the human-actor rule, expired or not (18.186).
    if (assertion.humanActorFailed === true) {
      notWaivable.push(assertion.control);
      return assertion;
    }
    const expiry = typeof entry.expiry === "string" ? parseDate(entry.expiry) : null;
    if (expiry !== null && asOfDate !== null && compareDate(expiry, asOfDate) < 0) {
      expired.push(assertion.control);
      return assertion;
    }
    return { ...assertion, outcome: "partial", deviation: assertion.control };
  });
  return { assertions: applied, expired, notWaivable };
}
