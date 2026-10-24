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
import { type Catalog, type ControlSpec, evidenceShapeFor, shapeFor } from "./catalog";
import { STATUS_COVERED, STATUS_UNKNOWN } from "./coverage";
import type { DomainBinding } from "./domain";
import { buildGraph, flaggedRecords, unkeyedFlags } from "./graph";
import { type UnreadableWrite, type Withheld, digestOf } from "./ingest";
import type { IntegrityCaps } from "./integrity";
import { eventIri } from "./iri";
import type { Profile, Subject } from "./profile";
import { compareDate, parseDate } from "./readiness";
import { BELOW_MINIMUM_OUTCOME, SourceClassJudge, belowMinimum } from "./sourceClass";
import type { GraphStore } from "./store";
import {
  type Violation,
  evaluateShape,
  propertyReads,
  violationToJson,
  withinTolerance,
} from "./structural";
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
 * enough human approvers, a human intervention, the review half of a risk review (18.186), the human approval that
 * clears a require_approval gate (18.37f), and an action's CND-02 gate, which a human's approval releases (18.187). */
const HUMAN_ACTOR_PATHS = new Set([
  "agentce:approvedBy",
  "agentce:gateMet",
  "agentce:reviewedBy",
  "agentce:oversightCoverageComplete",
  "agentce:interventionByHuman",
  "agentce:riskReviewed",
  "agentce:authorizationGateCleared",
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
    const digest = digestOf(event);
    if (digest === null) {
      // a record with no canonical form (an integer past 2^53) has no digest to cite
      continue;
    }
    pointers.push({
      ref: focus,
      digest: `sha256:${digest}`,
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
      restsOnHumanActorFailure(store, violations, () => propertyReads(shape, catalog.shapes)),
  });
}

/** Whether a violation's constraint reads a predicate the SPEC §10.4 human-actor rule decides and, from its focus,
 * reaches a node that fails the rule (`reads` gives each path's predicates, nested shapes included). */
function restsOnHumanActorFailure(
  store: GraphStore,
  violations: Violation[],
  reads: () => Map<string, string[]>,
): boolean {
  if (store.humanActorFailed.size === 0) return false;
  const byPath = reads();
  return violations.some((v) => reachesHumanActorFailure(store, v.focus, byPath.get(v.path) ?? []));
}

/** Whether `reads` names a predicate the human-actor rule decides and, walking its other predicates from `focus` (`^`
 * for an inverse) in any order and as often as they lead somewhere new, the walk reaches a node failing the rule. It
 * reaches at least what the shape itself reaches, a repeated step included, so it fails closed. */
export function reachesHumanActorFailure(
  store: GraphStore,
  focus: string,
  reads: readonly string[],
): boolean {
  const predicate = (step: string): string => (step.startsWith("^") ? step.slice(1) : step);
  if (!reads.some((step) => HUMAN_ACTOR_PATHS.has(predicate(step)))) return false;
  const steps = reads.filter((step) => !HUMAN_ACTOR_PATHS.has(predicate(step)));
  const reach = new Set([focus]);
  let frontier = [focus];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const start of frontier) {
      for (const step of steps) {
        const nodes = step.startsWith("^")
          ? store.subjects(step.slice(1), start)
          : store.objects(start, step);
        for (const node of nodes) {
          if (!reach.has(node)) {
            reach.add(node);
            next.push(node);
          }
        }
      }
    }
    frontier = next;
  }
  return [...reach].some((node) => store.humanActorFailed.has(node));
}

/** `make`, called at most once: on the first call, and its value returned from then on. */
function lazy<T>(make: () => T): () => T {
  let made: { value: T } | null = null;
  return () => {
    made ??= { value: make() };
    return made.value;
  };
}

/**
 * The families whose controls judge times (SPEC §6.6: INC, and REC retention, which no catalog has yet): a time_suspect
 * stream's records are left out of them.
 */
const TIMING_FAMILIES: ReadonlySet<string> = new Set(["INC"]);

/** One capped reading of a subject's records, its graph and judge built on first use. */
interface View {
  events: Event[];
  store: () => GraphStore;
  byIri: () => Map<string, Event>;
  judge: () => SourceClassJudge;
}

/** What other subjects' records say of one subject's records: the records their writes marked untrusted or rewrote
 * with no ruling (18.120), and the record_ref and store of each of their MemoryWrites ingest could not read (18.172). */
interface Foreign {
  untrusted: ReadonlySet<string>;
  unreadable: readonly UnreadableWrite[];
}

function foreignGraph(events: Event[], domain: DomainBinding, foreign: Foreign): GraphStore {
  return buildGraph(events, {
    domain,
    foreignUntrusted: foreign.untrusted,
    foreignUnreadable: foreign.unreadable,
  });
}

function makeView(events: Event[], domain: DomainBinding, foreign: Foreign): View {
  const store = lazy(() => foreignGraph(events, domain, foreign));
  const byIri = lazy(() => eventsByIriOf(events));
  return { events, store, byIri, judge: lazy(() => new SourceClassJudge(store(), byIri())) };
}

/**
 * The integrity caps of SPEC §6.6 and §8.1 over one subject's records, as ceilings on an outcome read from them as
 * declared. A pass must also hold with every record on a broken stream left out, and in a timing control every
 * time_suspect record (else insufficient_evidence), and then with every enforcement-point record on a weak stream left
 * out too (else partial, weak_integrity). Only a pass is capped: a violation is never changed.
 */
class CappedViews {
  readonly any: boolean;
  private readonly broken: Set<string>;
  private readonly weak: Set<string>;
  private readonly suspect: Set<string>;
  private readonly views = new Map<string, View>();

  constructor(
    private readonly events: Event[],
    caps: IntegrityCaps,
    private readonly domain: DomainBinding,
    private readonly foreign: Foreign,
  ) {
    const ids = new Set(events.map((e) => String(e.id)));
    const mine = (set: ReadonlySet<string>) => new Set([...set].filter((id) => ids.has(id)));
    this.broken = mine(caps.broken);
    this.weak = mine(caps.weak);
    this.suspect = mine(caps.timeSuspect);
    this.any = this.broken.size > 0 || this.weak.size > 0 || this.suspect.size > 0;
  }

  private view(weak: boolean, timing: boolean): View {
    const key = `${weak}|${timing}`;
    let view = this.views.get(key);
    if (view === undefined) {
      const dropped = new Set([
        ...this.broken,
        ...(weak ? this.weak : []),
        ...(timing ? this.suspect : []),
      ]);
      view = makeView(
        this.events.filter((e) => !dropped.has(String(e.id))),
        this.domain,
        this.foreign,
      );
      this.views.set(key, view);
    }
    return view;
  }

  ceiling(assertion: Assertion, judge: (view: View) => Assertion): Assertion {
    const timing = this.suspect.size > 0 && TIMING_FAMILIES.has(assertion.family);
    if (this.broken.size > 0 || timing) {
      if (judge(this.view(false, timing)).outcome !== "conformant") {
        return {
          ...assertion,
          outcome: "insufficient_evidence",
          population: [assertion.population[0], 0],
          violations: [],
          sourceClassSatisfied: false,
          evidenceStrength: null,
        };
      }
    }
    if (this.weak.size > 0 && judge(this.view(true, timing)).outcome !== "conformant") {
      return { ...assertion, outcome: "partial" };
    }
    return assertion;
  }
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
/** The families whose controls read insufficient_evidence while a subject's coverage is unknown (SPEC §6.5). */
export const COVERAGE_FAMILIES: ReadonlySet<string> = new Set(["INT", "REC"]);
/** The outcomes unknown coverage lowers: each reads better than insufficient_evidence. */
const LOWERED_OUTCOMES: ReadonlySet<string> = new Set(["conformant", "partial", "not_applicable"]);

/** True when `coverage` (computeCoverage's structure) does not show the subject's capture reconciled against an
 * independent denominator: its roll-up is unknown, or the subject has no entry at all (SPEC §6.5). */
function coverageUnknown(coverage: Record<string, unknown>, subject: string): boolean {
  const subjects = coverage.subjects;
  const entry =
    typeof subjects === "object" && subjects !== null
      ? (subjects as Record<string, unknown>)[subject]
      : undefined;
  return (
    typeof entry !== "object" ||
    entry === null ||
    Array.isArray(entry) ||
    (entry as Record<string, unknown>).coverage_status === STATUS_UNKNOWN
  );
}

/** Whether the coverage of the type `control`'s `requiresDenominator` names is unknown (true when no row exists) when
 * the subject's capture of it does not match a declared independent denominator of one of its kinds (18.37e, DOC-03): no
 * such denominator, another denominator holds the count, or the captured count differs from it either way (a declared
 * path that saw fewer records than were captured does not match them either); null when it matches or the control
 * declares none. Fail closed on anything missing. */
function denominatorUnshown(
  control: ControlSpec,
  subject: Subject,
  coverage: Record<string, unknown>,
): boolean | null {
  const need = control.requiresDenominator;
  if (need === null) {
    return null;
  }
  const sources = new Set(
    subject.coverageDenominators.filter((d) => need.kinds.has(d.kind)).map((d) => d.source),
  );
  const field = (value: unknown, key: string): unknown =>
    isRecord(value) ? value[key] : undefined;
  const row = field(field(field(coverage.subjects, subject.id), "event_types"), need.covers);
  if (!isRecord(row)) {
    return true;
  }
  const counted = row.denominators;
  return row.status === STATUS_COVERED &&
    row.observed === row.expected &&
    Array.isArray(counted) &&
    counted.some((source) => sources.has(source))
    ? null
    : row.status === STATUS_UNKNOWN;
}

/** An INT or REC outcome that reads better than insufficient_evidence while coverage is unknown reads
 * insufficient_evidence (SPEC §6.5): a call that bypassed capture would not be in the records. A violation stays.
 * `always` lowers a control of any family (a declared `requiresDenominator` the records do not show); `unknown` false
 * keeps it out of the coverage-unknown blind spot when the type's coverage is known (a shortfall, or another
 * denominator's count). */
export function uncovered(assertion: Assertion, always = false, unknown = true): Assertion {
  if (
    (!COVERAGE_FAMILIES.has(assertion.family) && !always) ||
    !LOWERED_OUTCOMES.has(assertion.outcome)
  ) {
    return assertion;
  }
  return {
    ...assertion,
    outcome: "insufficient_evidence",
    population: [assertion.population[0], 0],
    violations: [],
    sourceClassSatisfied: null,
    evidenceStrength: null,
    coverageUnknown: unknown,
  };
}

export function assessSubjects(
  accepted: Event[],
  profile: Profile,
  catalogs: Catalog[],
  domain: DomainBinding,
  applicabilityDeclared: boolean,
  withheld: Withheld[],
  integrity: IntegrityCaps,
  coverage: Record<string, unknown>,
): Assertion[] {
  const assertions: Assertion[] = [];
  const eventsBySubject = indexBySubject(accepted);
  // A record another subject's write marked untrusted, held or withheld, is never ruled on for this one (18.120);
  // such a write with no readable record_ref, under any subject, counts against its store's records, as an unreadable
  // write does (18.202).
  const flagSources = [
    ...accepted,
    ...withheld.map((w) => w.event).filter((e): e is Event => e !== null),
  ];
  const flagged = flaggedRecords(flagSources);
  const unkeyed = unkeyedFlags(flagSources);
  for (const subject of profile.subjects) {
    const subjectEvents = eventsBySubject.get(subject.id) ?? [];
    const foreign: Foreign = {
      untrusted: new Set(
        [...flagged].filter(([, by]) => [...by].some((s) => s !== subject.id)).map(([r]) => r),
      ),
      unreadable: [
        ...withheld
          .filter((w) => w.subject !== null && w.subject !== subject.id)
          .flatMap((w) => (w.write !== undefined ? [w.write] : [])),
        ...unkeyed,
      ],
    };
    const store = foreignGraph(subjectEvents, domain, foreign);
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
    const cfStore = cfEvents.length > 0 ? foreignGraph(cfEvents, domain, foreign) : store;
    const cfByIri = eventsByIriOf(cfEvents);
    // Each graph's judge is built once, on the first control whose minimum it may fall below.
    const judge = lazy(() => new SourceClassJudge(store, eventsByIri));
    const cfJudge = lazy(() => new SourceClassJudge(cfStore, cfByIri));
    const capped = new CappedViews(subjectEvents, integrity, domain, foreign);
    // Unknown coverage lowers the subject's INT and REC checks wherever its role applies (SPEC §6.5).
    const isUncovered = coverageUnknown(coverage, subject.id);
    for (const catalog of catalogs) {
      for (const control of catalog.controls) {
        const applies = roleApplies(roles, control.appliesToRoles);
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
        if (capped.any && assertion.outcome === "conformant") {
          assertion = capped.ceiling(assertion, (view) =>
            assertControl(
              view.store(),
              catalog,
              control,
              subject,
              roles,
              view.events,
              view.byIri(),
              win,
              view.judge,
              applicabilityDeclared,
            ),
          );
        }
        const flattered =
          own.length > 0 &&
          (assertion.outcome === "conformant" ||
            (assertion.outcome === "not_applicable" && applies));
        // Held records that read insufficient_evidence may lack only what a late record supplies, so a
        // readable withheld record is judged with them too: it can still read non-conformant (18.149).
        const short = readable.length > 0 && assertion.outcome === "insufficient_evidence";
        if (flattered || short) {
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
        }
        if (isUncovered && applies) {
          assertion = uncovered(assertion);
        }
        const unknown = applies ? denominatorUnshown(control, subject, coverage) : null;
        if (unknown !== null) {
          assertion = uncovered(assertion, true, unknown);
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
  // A check the unknown-coverage rule lowered with no population in scope (it was not applicable) judged nothing either.
  return !assertions.some(
    (a) => VERDICT_OUTCOMES.has(a.outcome) && !(a.coverageUnknown && a.population[0] === 0),
  );
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
