/**
 * Build the provenance graph from ingested events (SPEC §6.3, §7.2).
 *
 * Each event becomes a typed node with deterministic IRIs, the §6.3 relations become edges, and the
 * engine materialises the glue edges the Portable Shape Profile needs (`agentce:chainTerminus`,
 * `agentce:chainVerified`, `agentce:executesConsequential`, `agentce:oversightModalityMatchesDeclared`,
 * `agentce:danglingRef`, `agentce:precededBy`, `agentce:componentDeclared`,
 * `agentce:declaredComponentsObserved`, `agentce:triggersIncident`, `agentce:oversightCoverageComplete`,
 * `agentce:interventionEffective`, `agentce:interventionByHuman`, `agentce:incidentResponded` and
 * `agentce:riskReviewed`). The
 * `rdfs:subClassOf*` closure of the class hierarchy
 * (base vocabulary plus the domain binding) is materialised so class membership needs no inference.
 * This is a faithful port of the Python reference; it must build byte-identical graphs.
 */

import { DomainBinding, TOKEN_CAP, tokenCount } from "./domain";
import { pyStrip } from "./failOn";
import type { UnreadableWrite } from "./ingest";
import { ZERO_KEY, eventIri, isEventRef, principalIri } from "./iri";
import { SourceClassJudge } from "./sourceClass";
import { GraphStore } from "./store";
import { compareLiterals } from "./structural";
import { byteCompare } from "./util";

const BOOL = "xsd:boolean";
/** CND-02's population (SPEC §7.7.4), materialised by `conductGate`. */
export const GATED_ACTION = "agentce:GatedAction";
/** CND-04's population (SPEC §7.7.4), materialised by `conductProvenance`. */
export const CONSEQUENTIAL_ACTION = "agentce:ConsequentialAction";
/** A ModelCall in a task: consumption CND-07 judges against the task's budgets. */
export const TASK_MODEL_CALL = "agentce:TaskModelCall";
/** An action whose task budgets the records cannot tell (CND-07's evidence shape). */
export const BUDGET_UNJUDGED = "agentce:BudgetUnjudged";
const DATETIME = "xsd:dateTime";
const INTEGER = "xsd:integer";

/** What each task's records show it consumed (SPEC §7.7.3 budget row); the Python engine's `_TaskUsage`
 * documents the reading. */
class TaskUsage {
  readonly tokens = new Map<string, number>();
  readonly tokensUnread = new Set<string>();
  readonly spendTasks = new Set<string>();
  readonly spendUnknown = new Set<string>();

  add(ptype: string, data: Record<string, unknown>, tasks: Set<string>): void {
    if (ptype === "ToolCall") {
      const effect = data.effect_class;
      if (effect === "spend") {
        for (const task of tasks) this.spendTasks.add(task);
      } else if (typeof effect !== "string") {
        for (const task of tasks) this.spendUnknown.add(task);
      }
      return;
    }
    const usage = isRecord(data.usage) ? data.usage : {};
    const usedIn = tokenCount(usage.input_tokens);
    const usedOut = tokenCount(usage.output_tokens);
    for (const task of tasks) {
      this.spendUnknown.add(task);
      if (usedIn !== null && usedOut !== null) {
        // each term is at most TOKEN_CAP and rounding is monotonic, so a sum at or past 2^53 reads TOKEN_CAP exactly
        this.tokens.set(task, Math.min((this.tokens.get(task) ?? 0) + usedIn + usedOut, TOKEN_CAP));
      } else {
        this.tokensUnread.add(task);
      }
    }
  }

  /** true within the limit, false over it, null when the records cannot tell. */
  verdict(task: string, dimension: string, limit: number | null): boolean | null {
    if (dimension === "tokens") {
      if (limit === null) {
        return null;
      }
      if ((this.tokens.get(task) ?? 0) > limit) {
        return false;
      }
      // no readable usage for the task, or a model call without one: the records cannot tell
      return this.tokensUnread.has(task) || !this.tokens.has(task) ? null : true;
    }
    if (dimension === "spend") {
      if (limit === null) {
        return null;
      }
      if (this.spendTasks.has(task)) {
        return limit === 0 ? false : null;
      }
      return this.spendUnknown.has(task) ? null : true;
    }
    return null;
  }
}

/** The base class hierarchy (child -> parent), mirroring spec/vocab/agentce.ttl (SPEC §6.3). */
const BASE_SUBCLASS: Record<string, string> = {
  "prov:SoftwareAgent": "prov:Agent",
  "agentce:Agent": "prov:SoftwareAgent",
  "agentce:Principal": "prov:Agent",
  "agentce:HumanPrincipal": "agentce:Principal",
  "agentce:ServicePrincipal": "agentce:Principal",
  "agentce:Activity": "prov:Activity",
  "agentce:ContextItem": "prov:Entity",
  "agentce:PolicyDecision": "agentce:Activity",
  "agentce:DelegationIssued": "agentce:Activity",
  "agentce:Decision": "agentce:Activity",
  "agentce:ConsequentialDecision": "agentce:Decision",
  "agentce:Instruction": "agentce:Activity",
  "agentce:Refusal": "agentce:Activity",
  "agentce:ToolCall": "agentce:Activity",
  "agentce:ModelCall": "agentce:Activity",
  "agentce:ResourceAccess": "agentce:Activity",
  "agentce:MemoryRead": "agentce:Activity",
  "agentce:MemoryWrite": "agentce:Activity",
};

/** refs.* keys that map directly to an edge from the referring event (SPEC §6.3). */
const GENERIC_REFS: Record<string, string> = {
  authorization: "agentce:authorizedBy",
  request: "agentce:authorizedBy",
  delegation: "agentce:delegatedVia",
  instruction: "agentce:actsOn",
  parent: "agentce:derivedFrom",
  origin: "agentce:derivedFrom",
};

/** Instruction source classes an agent may act on without corroboration (SPEC §7.7, Appendix F). Any
 * other declared class is untrusted for the Conduct overlay's provenance and isolation controls. */
export const TRUSTED_INSTRUCTION: ReadonlySet<string> = new Set([
  "user",
  "operator",
  "service",
  "agent_identified",
  "memory_trusted",
]);

/** The Appendix F classes that name an authorised principal: CND-04's chain must end at one (memory_trusted
 * does not, ruling 12 A of 2026-10-12). */
const AUTHORISED_INSTRUCTION: ReadonlySet<string> = new Set(
  [...TRUSTED_INSTRUCTION].filter((c) => c !== "memory_trusted"),
);

/** A memory record's trust, or a read's `trust_min`, that ROB-02 treats as untrusted, and the memory guard
 * verdicts that mark a record untrusted whatever its trust field says (SPEC §7.4). */
const UNTRUSTED_TRUST = new Set(["untrusted", "quarantined"]);
const UNTRUSTED_VERDICTS = new Set(["quarantine", "block"]);

/** Payload fields that carry content into their event, and those naming content their event produced
 * (SPEC §6.2); ROB-02's taint flows along both. Identity, audit and person refs carry none. */
const CONTENT_IN = [
  "inputs",
  "used",
  "args_ref",
  "input_ref",
  "record_refs",
  "provenance_origin_ref",
];
const CONTENT_OUT = ["output_ref", "result_ref", "record_ref", "content_ref"];
/** `refs` members that carry content into their event: the producing activity, the instruction acted
 * on and the instruction it was derived from. */
const REFS_IN = ["origin", "instruction", "parent"];

/** An Appendix F source class outside the trusted set (CND-05's rule, also ROB-02's). */
function untrustedSourceClass(value: unknown): boolean {
  return typeof value === "string" && !TRUSTED_INSTRUCTION.has(value);
}

/** The own classes whose record of an instruction's principal is read as recorded (SPEC §6.4 order, at or above
 * enforcement_point); below them a principal's role needs an enforcement-point witness (CND-04). */
const AT_OR_ABOVE_EP = new Set(["enforcement_point", "independent_system"]);

/** A principal's id, trimmed; "" when absent or blank. */
function principalKey(principal: Record<string, unknown>): string {
  return pyStrip(text(principal.id) ?? "");
}

function isOneOf(value: unknown, values: ReadonlySet<string>): boolean {
  return typeof value === "string" && values.has(value);
}

/** A MemoryWrite that marks its record untrusted: trust untrusted or quarantined, a quarantine or block verdict, or no
 * trust and no verdict but an untrusted Appendix F `provenance_origin_class` (SPEC §7.4, ROB-02). */
function flagsUntrusted(data: Record<string, unknown>): boolean {
  return (
    isOneOf(data.trust, UNTRUSTED_TRUST) ||
    isOneOf(data.guard_verdict, UNTRUSTED_VERDICTS) ||
    (isAbsent(data.trust) &&
      isAbsent(data.guard_verdict) &&
      untrustedSourceClass(data.provenance_origin_class))
  );
}

/** A write a guard ruled on: an enforcement-point MemoryWrite with a trust or a verdict (18.120). */
function ruledWrite(event: Event, data: Record<string, unknown>): boolean {
  return (
    event.agentcesourceclass === "enforcement_point" &&
    (!isAbsent(data.trust) || !isAbsent(data.guard_verdict))
  );
}

function noteStore(stores: Map<string, Set<string>>, record: string, store: unknown): void {
  let named = stores.get(record);
  if (named === undefined) {
    named = new Set();
    stores.set(record, named);
  }
  if (typeof store === "string") {
    named.add(store);
  }
}

/** The records unreadable MemoryWrites may have changed (SPEC §7.4, 18.172): the one each names; with no readable
 * record_ref, every record of its store, a record no event names a store for counting as one of them (fail closed);
 * with no readable store either, every record. */
function unreadRecords(
  writes: readonly UnreadableWrite[],
  records: ReadonlySet<string>,
  stores: ReadonlyMap<string, ReadonlySet<string>>,
): Set<string> {
  const unread = new Set<string>();
  for (const { recordRef, store } of writes) {
    if (recordRef !== null) {
      unread.add(recordRef);
    } else {
      for (const record of records) {
        const named = stores.get(record);
        if (store === null || named === undefined || named.size === 0 || named.has(store)) {
          unread.add(record);
        }
      }
    }
  }
  return unread;
}

/** Each MemoryWrite in `events` that marks its record untrusted or writes with no guard ruling (anything but an
 * enforcement-point write with a trust or a verdict), with its subject and data. */
function* flaggedWrites(events: Iterable<Event>): Generator<[string, Record<string, unknown>]> {
  for (const event of events) {
    const data = dataOf(event);
    if (data["@type"] === "MemoryWrite" && (flagsUntrusted(data) || !ruledWrite(event, data))) {
      yield [String(event.subject ?? ""), data];
    }
  }
}

/** Each record a MemoryWrite in `events` marks untrusted or writes with no guard ruling, with the subjects whose
 * records hold such a write. A subject's graph reads a record another subject flagged or rewrote unruled as one its
 * guard never ruled on (18.120). */
export function flaggedRecords(events: Iterable<Event>): Map<string, Set<string>> {
  const flagged = new Map<string, Set<string>>();
  for (const [subject, data] of flaggedWrites(events)) {
    const record = text(data.record_ref);
    if (record !== null) {
      let subjects = flagged.get(record);
      if (subjects === undefined) {
        subjects = new Set();
        flagged.set(record, subjects);
      }
      subjects.add(subject);
    }
  }
  return flagged;
}

/** Each such write whose `record_ref` cannot be read (absent, null or empty), under any subject, as its store: it
 * counts against every record of its store, or every record, as an unreadable write does (SPEC §7.4, 18.202). */
export function unkeyedFlags(events: Iterable<Event>): UnreadableWrite[] {
  const unkeyed: UnreadableWrite[] = [];
  for (const [, data] of flaggedWrites(events)) {
    if (text(data.record_ref) === null) {
      unkeyed.push({ recordRef: null, store: text(data.store) });
    }
  }
  return unkeyed;
}

function flow(edges: Map<string, Set<string>>, source: unknown, target: unknown): void {
  if (typeof source === "string" && typeof target === "string") {
    let targets = edges.get(source);
    if (targets === undefined) {
      targets = new Set();
      edges.set(source, targets);
    }
    targets.add(target);
  }
}

/** Grow `seeds` in place to everything reachable along `edges` (order- and cycle-free). */
function reach(seeds: Set<string>, edges: Map<string, Set<string>>): void {
  const pending = [...seeds];
  while (pending.length > 0) {
    for (const target of edges.get(pending.pop() as string) ?? []) {
      if (!seeds.has(target)) {
        seeds.add(target);
        pending.push(target);
      }
    }
  }
}

/** A field the record leaves out or sets to null (Python's None). */
function isAbsent(value: unknown): boolean {
  return value === undefined || value === null;
}

export type Event = Record<string, unknown>;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function dataOf(event: Event): Record<string, unknown> {
  return isRecord(event.data) ? event.data : {};
}

/** Whether `time` is at or before every action time, compared as instants (SPEC §6.7); a time that cannot be
 * compared with an action's gates nothing. */
function beforeEvery(time: string, actions: string[]): boolean {
  return actions.every((action) => {
    const order = compareLiterals(time, action);
    return order === -1 || order === 0;
  });
}

/** Whether `time` is after every action time, compared as instants (SPEC §6.7): a decision recorded after the action
 * already ran cannot withdraw the approval it ran under (18.142). Never true when no action ran. */
function afterEvery(time: string, actions: string[]): boolean {
  return actions.length > 0 && actions.every((action) => compareLiterals(time, action) === 1);
}

export function refsOf(event: Event): Record<string, unknown> {
  const refs = dataOf(event).refs;
  return isRecord(refs) ? refs : {};
}

function closure(subclass: Record<string, string>, classes: Set<string>): Array<[string, string]> {
  const nodes = new Set<string>(classes);
  for (const key of Object.keys(subclass)) {
    nodes.add(key);
    nodes.add(subclass[key] as string);
  }
  const pairs: Array<[string, string]> = [];
  for (const node of nodes) {
    pairs.push([node, node]); // reflexive
    let current = node;
    const seen = new Set<string>();
    while (current in subclass && !seen.has(current)) {
      seen.add(current);
      current = subclass[current] as string;
      pairs.push([node, current]);
    }
  }
  return pairs;
}

function principalRef(entry: unknown): [string | null, string | null] {
  if (typeof entry === "string") {
    return [entry, null];
  }
  if (isRecord(entry) && typeof entry.id === "string") {
    return [entry.id, typeof entry.kind === "string" ? entry.kind : null];
  }
  return [null, null];
}

/** The characters the human-actor rule trims from each end of a principal id: Unicode White_Space plus U+FEFF,
 * written out because Python's strip, JavaScript's trim and Java's strip each trim a different set (SPEC §10.4). */
const ID_PAD =
  "\t\n\u000b\u000c\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a" +
  "\u2028\u2029\u202f\u205f\u3000\ufeff";

/** A principal id as the human-actor rule compares it: trimmed of `ID_PAD` at both ends, `null` when nothing is
 * left, so a padded spelling of an id is the same principal everywhere in the rule. */
function ruleId(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  let start = 0;
  let end = value.length;
  while (start < end && ID_PAD.includes(value[start] as string)) {
    start++;
  }
  while (end > start && ID_PAD.includes(value[end - 1] as string)) {
    end--;
  }
  return end > start ? value.slice(start, end) : null;
}

/** The trimmed id of a human principal (SPEC §6.2 `actor`, SessionStart `principal`; §10.4 human-actor rule);
 * `null` for a missing, unnamed or non-human principal. */
function humanId(principal: unknown): string | null {
  return isRecord(principal) && principal.kind === "human" ? ruleId(principal.id) : null;
}

/** Python's `==` over JSON values: a bool equals the number 0 or 1, objects compare by key set and
 * value whatever their key order, arrays element by element. */
export function pyEquals(a: unknown, b: unknown): boolean {
  const num = (v: unknown): unknown => (typeof v === "boolean" ? Number(v) : v);
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((v, i) => pyEquals(v, b[i]))
    );
  }
  if (isRecord(a) || isRecord(b)) {
    if (!isRecord(a) || !isRecord(b)) {
      return false;
    }
    const keys = Object.keys(a);
    return (
      keys.length === Object.keys(b).length &&
      keys.every((k) => Object.prototype.hasOwnProperty.call(b, k) && pyEquals(a[k], b[k]))
    );
  }
  return num(a) === num(b);
}

/** Interrupt effects that stop the agent, and the interrupt mechanisms SPEC §6.2 names (OVS-07). */
const INTERRUPT_STOPS = new Set(["halted", "paused"]);
const INTERRUPT_MECHANISMS = new Set(["stop_button", "kill_switch", "circuit_breaker", "manual"]);
/** The stream classes an identity-provider login record must come from (SPEC §6.2 ApprovalDecided.session_ref, §10.4). */
const LOGIN_CLASSES = new Set(["independent_system", "enforcement_point"]);
/** The effect classes SPEC §7.7.4 holds to CND-02 whatever the task scope marks (ruling Q6). */
const HIGH_EFFECT = new Set(["irreversible", "external_communication", "spend", "physical"]);
/** The records the SPEC §10.4 human-actor rule applies to. */
const OVERSIGHT_TYPES = new Set(["ApprovalDecided", "Override", "Interrupt"]);
/** The Incident members that record a response after detection (SPEC §6.2; ROB-07). */
const INCIDENT_RESPONSES = ["causal_assessment_at", "provider_notified_at", "reported_at"];

function principalClass(kind: string | null): string {
  if (kind === "human") {
    return "agentce:HumanPrincipal";
  }
  if (kind === "service") {
    return "agentce:ServicePrincipal";
  }
  return "agentce:Principal";
}

// DOC-01 (SPEC §7.4, Art. 11 / Annex IV): the events that declare an operating component (a
// BundleLoaded manifest) or exercise one (a ToolCall or ModelCall) are typed with this
// engine-materialised class, so one shape can compare the declaration with what operated.
const COMPONENT_RECORD = "agentce:ComponentRecord";
const COMPONENT_RECORD_TYPES = new Set(["BundleLoaded", "ToolCall", "ModelCall"]);

// Which declared component kinds a call can exercise. A tool call exercises a skill or an MCP server
// (by its tool name or server name), a model call a model; a component with no kind may be either.
// Prompts, configs and policies are never exercised by a call, so they take no part in DOC-01.
type Family = "tool" | "model";

function families(kind: string | null): Family[] {
  if (kind === null) {
    return ["tool", "model"];
  }
  return kind === "model" ? ["model"] : kind === "skill" || kind === "mcp_server" ? ["tool"] : [];
}

// Declared kinds that must be seen operating (DOC-01 S2). A skill is left out: its name is not
// reliably a tool name, so not seeing it is not evidence that it never ran.
function mustOperate(kind: string | null): Family | null {
  return kind === "model" ? "model" : kind === "mcp_server" ? "tool" : null;
}

export function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

// [kind, name, pins] for each named component a BundleLoaded manifest declares, where pins holds its
// declared version and digest (empty: any version matches).
function declaredComponents(data: Record<string, unknown>): [string | null, string, string[]][] {
  const components = Array.isArray(data.components) ? data.components : [];
  const out: [string | null, string, string[]][] = [];
  for (const component of components) {
    const bare = text(component);
    if (bare !== null) {
      out.push([null, bare, []]);
    } else if (isRecord(component) && text(component.name) !== null) {
      const kind = typeof component.kind === "string" ? component.kind : null;
      const pins = [component.version, component.digest]
        .map(text)
        .filter((p): p is string => p !== null);
      out.push([kind, component.name as string, pins]);
    }
  }
  return out;
}

// [family, names, version] of the component a ToolCall or ModelCall exercises: a tool call is known
// by its tool name and its server name, a model call by its model name.
function operated(ptype: string, data: Record<string, unknown>): [Family, string[], string | null] {
  const family: Family = ptype === "ToolCall" ? "tool" : "model";
  const ref = data[family];
  if (!isRecord(ref)) {
    return [family, [], null];
  }
  const keys = family === "tool" ? ["name", "server"] : ["name"];
  const names = keys.map((k) => text(ref[k])).filter((n): n is string => n !== null);
  return [family, names, text(ref.version_or_digest)];
}

class Builder {
  private readonly eventIris = new Set<string>();
  private readonly decisionType = new Map<string, string>();
  private readonly decisionTime = new Map<string, string>();
  private readonly decisionReviewed = new Set<string>();
  private readonly outcomeDecision = new Map<string, string>(); // Outcome IRI -> its refs.decision IRI, if any
  private readonly decisionNotice = new Map<string, Set<string>>(); // Decision IRI -> every notifying Notice IRI (order-independent)
  private readonly danglingNodes = new Set<string>(); // event IRI -> has >=1 agentce:danglingRef literal
  private readonly instructionUntrusted = new Map<string, boolean>(); // Instruction IRI -> untrusted flag
  // Instructions that declare no source class: ROB-02 taint seeds of their own (18.127).
  private readonly instructionUnclassed = new Set<string>();
  // DOC-01: family -> declared name -> the union of its declared pins over every BundleLoaded manifest in
  // the subject's records (empty: any version matches), so event order does not matter and an unpinned
  // declaration never cancels another manifest's pin (18.37j).
  private readonly declared: Record<Family, Map<string, Set<string>>> = {
    tool: new Map(),
    model: new Map(),
  };
  private readonly operated: Record<Family, Set<string>> = {
    tool: new Set(),
    model: new Set(),
  };
  private hasCalls = false;
  // SPEC §10.4 human-actor rule, read in the second pass so file order never matters: event IRI -> [type, source class,
  // agent id, the IRI of the human principal it names] of every event, the last read from any type (SessionStart,
  // PolicyDecision and Instruction carry one) so only the type check keeps a login a session (18.140); agent id ->
  // every principal IRI in its delegation chain (its acted_for and its DelegationIssued chains); event IRI -> the
  // principal IRIs of its own acted_for.
  private readonly eventInfo = new Map<
    string,
    [string, string | null, string | null, string | null]
  >();
  private readonly agentChain = new Map<string, Set<string>>();
  // event IRI -> the agents it concerns: its own agent and its CloudEvents subject (the assessed agent), so an event
  // that leaves out the optional agent still concerns the subject.
  private readonly eventAgents = new Map<string, Set<string>>();
  private readonly actedFor = new Map<string, Set<string>>();
  // agent id -> how many held events concern it, built on first use by an oversight record whose refs.decision names
  // no held event (18.158).
  private agentEvents: Map<string, number> | null = null;
  // OVS-08: decision IRI -> the actor IRI of every verified ApprovalDecided whose outcome is approve.
  private readonly humanApprovers = new Map<string, Set<string>>();
  // INC-03: event IRI -> the decision its refs.decision names, whatever the event type.
  private readonly refDecision = new Map<string, string>();

  constructor(
    private readonly store: GraphStore,
    private readonly domain: DomainBinding,
    private readonly key: Buffer,
    // Records another subject's MemoryWrite marked untrusted or rewrote with no ruling: never ruled on in this
    // subject's graph (18.120).
    private readonly foreignUntrusted: ReadonlySet<string>,
    // The record_ref and store of each MemoryWrite another subject holds that ingest could not read (18.172).
    private readonly foreignUnreadable: readonly UnreadableWrite[],
  ) {}

  build(events: Event[]): GraphStore {
    const usedClasses = new Set<string>(Object.keys(BASE_SUBCLASS));
    usedClasses.add("prov:Agent");
    usedClasses.add("prov:Activity");
    usedClasses.add("prov:Entity");
    usedClasses.add(GATED_ACTION);
    usedClasses.add(CONSEQUENTIAL_ACTION);
    usedClasses.add(TASK_MODEL_CALL);
    usedClasses.add(BUDGET_UNJUDGED);
    for (const event of events) {
      const node = eventIri(String(event.id));
      this.eventIris.add(node);
      // The held decisions, known before the first pass so a refs.decision read before its Decision still resolves
      // to it (18.156).
      if (this.ptype(event) === "Decision") {
        this.decisionTime.set(node, typeof event.time === "string" ? event.time : "");
      }
    }
    for (const event of events) {
      usedClasses.add(`agentce:${this.ptype(event)}`);
      if (COMPONENT_RECORD_TYPES.has(this.ptype(event))) {
        usedClasses.add(COMPONENT_RECORD);
      }
      this.mapEvent(event);
    }
    for (const type of this.decisionType.values()) {
      usedClasses.add(type);
    }
    const merged = {
      ...BASE_SUBCLASS,
      ...Object.fromEntries(this.domain.subclasses),
    };
    this.store.addSubclassClosure(closure(merged, usedClasses));
    this.materialise(events);
    return this.store;
  }

  private ptype(event: Event): string {
    const type = dataOf(event)["@type"];
    return typeof type === "string" ? type : "Activity";
  }

  private mapEvent(event: Event): void {
    const node = eventIri(String(event.id));
    const data = dataOf(event);
    const ptype = this.ptype(event);
    this.store.addType(node, `agentce:${ptype}`);
    if (typeof event.agentcesourceclass === "string") {
      this.store.addEdge(node, "agentce:sourceClass", `agentce:${event.agentcesourceclass}`);
    }
    if (typeof event.time === "string") {
      this.store.addLiteral(node, "prov:atTime", event.time, DATETIME);
    }

    const agent = data.agent;
    const agentId = isRecord(agent) && typeof agent.id === "string" ? agent.id : null;
    const sourceClass =
      typeof event.agentcesourceclass === "string" ? event.agentcesourceclass : null;
    const loginPrincipal = humanId(data.principal);
    this.eventInfo.set(node, [
      ptype,
      sourceClass,
      agentId,
      loginPrincipal === null ? null : principalIri(loginPrincipal, this.key),
    ]);
    const actedFor = this.principalIris(data.acted_for);
    this.actedFor.set(node, actedFor);
    const owners = new Set(
      [agentId, event.subject].filter((a): a is string => typeof a === "string"),
    );
    this.eventAgents.set(node, owners);
    for (const owner of owners) {
      let chain = this.agentChain.get(owner);
      if (!chain) {
        chain = new Set();
        this.agentChain.set(owner, chain);
      }
      for (const p of actedFor) {
        chain.add(p);
      }
      if (ptype === "DelegationIssued") {
        for (const p of this.principalIris(data.chain)) {
          chain.add(p);
        }
      }
    }
    if (agentId !== null) {
      this.store.addType(agentId, "agentce:Agent");
      this.store.addEdge(node, "prov:wasAssociatedWith", agentId);
      this.mapChain(agentId, data.acted_for);
    }

    const used = Array.isArray(data.used) ? data.used : [];
    for (const item of used) {
      if (typeof item === "string") {
        this.store.addType(item, "agentce:ContextItem");
        this.store.addEdge(node, "prov:used", item);
      }
    }

    for (const [key, value] of Object.entries(refsOf(event))) {
      const predicate = GENERIC_REFS[key];
      if (predicate !== undefined && typeof value === "string") {
        this.store.addEdge(node, predicate, value);
      }
    }

    this.mapDecisionLinks(node, ptype, event);
    this.mapConduct(node, ptype, data);
    this.mapComponents(node, ptype, data);
    this.dangling(node, event);

    if (ptype === "DelegationIssued") {
      this.mapDelegationPrincipals(data.chain);
    }

    if (ptype === "Decision") {
      const dtype = data.decision_type;
      if (typeof dtype === "string") {
        this.decisionType.set(node, dtype);
        this.store.addType(node, dtype);
      }
    }
  }

  private mapDelegationPrincipals(chain: unknown): void {
    if (!Array.isArray(chain)) {
      return;
    }
    chain.forEach((entry, index) => {
      const [pid, kind] = principalRef(entry);
      if (pid === null) {
        return;
      }
      const pIri = principalIri(pid, this.key);
      this.store.addType(pIri, principalClass(kind));
      this.store.addLiteral(pIri, "agentce:chainIndex", String(index), INTEGER);
    });
  }

  /** The pseudonymised IRIs of an `acted_for` or `chain` list (SPEC §6.3: the delegation chain), ids trimmed as the
   * human-actor rule compares them. */
  private principalIris(principals: unknown): Set<string> {
    const out = new Set<string>();
    if (Array.isArray(principals)) {
      for (const entry of principals) {
        const pid = ruleId(principalRef(entry)[0]);
        if (pid !== null) {
          out.add(principalIri(pid, this.key));
        }
      }
    }
    return out;
  }

  /** SPEC §10.4 human-actor rule: the actor IRI of an ApprovalDecided, Override or Interrupt whose actor is a human
   * principal whose `session_ref` names a held identity-provider login record of that same human, and who is nowhere
   * in the delegation chain of the activity; `null` when any part fails (fail closed).
   *
   * The login record is a held `SessionStart` (the only session record the event model has) from an
   * independent_system or enforcement_point stream whose `principal` is the actor (ids trimmed of `ID_PAD`) and whose
   * agent is none of the agents the oversight record concerns: the own agent and CloudEvents subject of the record
   * and of the named decision, whose own run session is no human's login. The delegation chain is the named
   * decision's and the record's own `acted_for` and every chain of those agents. */
  private verifiedActor(node: string, event: Event): string | null {
    const data = dataOf(event);
    const actor = humanId(data.actor);
    const login = data.session_ref;
    const loginInfo = typeof login === "string" ? this.eventInfo.get(login) : undefined;
    if (actor === null || typeof login !== "string" || loginInfo === undefined) {
      return null;
    }
    // Every name the ref may mean an event by, the held decision among them: a name that leads to another held
    // event, or to none, still counts that event's agents, so resolving never loosens the rule (fail closed).
    const refs = refsOf(event);
    const named = this.refNames(refs.decision);
    // A present refs.decision that names no held event (null or empty included) may mean any of them, so the record
    // concerns every held event but its own login (18.158).
    const unresolved =
      Object.hasOwn(refs, "decision") && !named.some((name) => this.eventInfo.has(name));
    const concerned = new Set(this.eventAgents.get(node));
    for (const name of named) {
      for (const agent of this.eventAgents.get(name) ?? []) {
        concerned.add(agent);
      }
    }
    const [loginType, loginClass, loginAgent, loginPrincipal] = loginInfo;
    if (loginType !== "SessionStart" || loginClass === null || !LOGIN_CLASSES.has(loginClass)) {
      return null;
    }
    if (loginAgent === null || concerned.has(loginAgent)) {
      return null;
    }
    // The login itself is one event concerning its agent; any other makes it that agent's session (fail closed).
    if (unresolved && (this.agentEventCounts().get(loginAgent) ?? 0) > 1) {
      return null;
    }
    const actorIri = principalIri(actor, this.key);
    if (loginPrincipal !== actorIri) {
      return null;
    }
    const chain = new Set(this.actedFor.get(node));
    for (const name of named) {
      for (const p of this.actedFor.get(name) ?? []) {
        chain.add(p);
      }
    }
    for (const agentId of concerned) {
      for (const p of this.agentChain.get(agentId) ?? []) {
        chain.add(p);
      }
    }
    return chain.has(actorIri) ? null : actorIri;
  }

  /** How many held events concern each agent (their own agent or CloudEvents subject; 18.158). */
  private agentEventCounts(): Map<string, number> {
    if (this.agentEvents === null) {
      this.agentEvents = new Map();
      for (const agents of this.eventAgents.values()) {
        for (const agent of agents) {
          this.agentEvents.set(agent, (this.agentEvents.get(agent) ?? 0) + 1);
        }
      }
    }
    return this.agentEvents;
  }

  private mapChain(agentId: string, actedFor: unknown): void {
    if (!Array.isArray(actedFor)) {
      return;
    }
    actedFor.forEach((entry, index) => {
      const [pid, kind] = principalRef(entry);
      if (pid === null) {
        return;
      }
      const pIri = principalIri(pid, this.key);
      this.store.addType(pIri, principalClass(kind));
      this.store.addEdge(agentId, "prov:actedOnBehalfOf", pIri);
      this.store.addLiteral(pIri, "agentce:chainIndex", String(index), INTEGER);
    });
  }

  /** The SPEC §6.3 glue edges of a refs.decision, read through `heldDecision` as approvedBy reads it; a ref naming
   * no held decision keeps its text (18.156). */
  private mapDecisionLinks(node: string, ptype: string, event: Event): void {
    const refs = refsOf(event);
    const ref = refs.decision;
    if (typeof ref === "string") {
      const decision = this.heldDecision(ref) ?? ref;
      this.refDecision.set(node, decision);
      if (ptype === "ToolCall") {
        this.store.addEdge(node, "agentce:executes", decision);
      } else if (ptype === "Outcome") {
        this.store.addEdge(decision, "agentce:resultedIn", node);
        this.outcomeDecision.set(node, decision);
      } else if (ptype === "Override") {
        this.store.addEdge(decision, "agentce:overriddenBy", node);
      } else if (ptype === "Interrupt") {
        this.store.addEdge(decision, "agentce:interruptedBy", node);
      } else if (ptype === "Notice") {
        this.store.addEdge(decision, "agentce:notifiedBy", node);
        let notices = this.decisionNotice.get(decision);
        if (!notices) {
          notices = new Set();
          this.decisionNotice.set(decision, notices);
        }
        notices.add(node);
      }
    }
    if (ptype === "Refusal") {
      const instruction = refs.instruction;
      if (typeof instruction === "string") {
        this.store.addEdge(instruction, "agentce:refusedBy", node);
      }
    }
  }

  // Collect what the manifests declare and what the calls exercise (DOC-01); the two literals are set
  // in the second pass, once every event is mapped.
  private mapComponents(node: string, ptype: string, data: Record<string, unknown>): void {
    if (!COMPONENT_RECORD_TYPES.has(ptype)) {
      return;
    }
    if (ptype === "BundleLoaded") {
      for (const [kind, name, pins] of declaredComponents(data)) {
        for (const family of families(kind)) {
          const known = this.declared[family];
          let declaredPins = known.get(name);
          if (!declaredPins) {
            declaredPins = new Set();
            known.set(name, declaredPins);
          }
          for (const pin of pins) {
            declaredPins.add(pin);
          }
        }
      }
    } else {
      this.store.addType(node, COMPONENT_RECORD);
      this.hasCalls = true;
      const [family, names] = operated(ptype, data);
      for (const name of names) {
        this.operated[family].add(name);
      }
    }
  }

  /** Materialise the Conduct-overlay instruction-trust flag (SPEC §7.7): whether an instruction's
   * declared source class is untrusted (Appendix F). Scope and budget are computed from the
   * enforcement point's records in a second pass (`conductScopeBudget`). */
  private mapConduct(node: string, ptype: string, data: Record<string, unknown>): void {
    if (ptype !== "Instruction") {
      return;
    }
    const sourceClass = data.source_class;
    const untrusted = untrustedSourceClass(sourceClass);
    this.instructionUntrusted.set(node, untrusted);
    if (typeof sourceClass !== "string") {
      // No class at all (or not a string): ROB-02 reads it as untrusted content (18.127).
      this.instructionUnclassed.add(node);
    }
    this.store.addLiteral(node, "agentce:instructionUntrusted", untrusted ? "true" : "false", BOOL);
  }

  /**
   * Whether a ToolCall stays within the declared effect scope of every task it names, by
   * `refs.task` or the `agentcetask` attribute (SPEC §7.7.4 CND-01, §7.7.6): each scope the domain
   * binding holds the task to (`DomainBinding.scopesFor`) allows its tool and its effect class. A
   * task held to no scope, or a call with no effect class, is out of scope.
   */
  private withinTaskScope(event: Event): boolean {
    const data = dataOf(event);
    const tool = data.tool;
    const name = isRecord(tool) ? tool.name : undefined;
    for (const task of this.actionTasks(event)) {
      const scopes = this.domain.scopesFor(task);
      if (scopes.length === 0 || !scopes.every((s) => s.allows(name, data.effect_class))) {
        return false;
      }
    }
    return true;
  }

  /** The tasks an action names, by `refs.task` or the `agentcetask` attribute. */
  private actionTasks(event: Event): Set<string> {
    return new Set(
      [refsOf(event).task, event.agentcetask].filter((t): t is string => typeof t === "string"),
    );
  }

  /** Materialise CND-02's population and gate (SPEC §7.7.4, rulings Q5 to Q10); the Python engine's
   * `_conduct_gate` documents the reading. An action whose effect class a scope of its task marks
   * `requires_approval`, or whose class is high-effect, is a `GatedAction`; a high-effect action in no task,
   * or in a task held to no scope, is held to the approval path. The approval path needs an
   * enforcement-point PolicyDecision naming it that allows it or asks for approval and an approval of the
   * held decision it executes; any other high-effect action needs an enforcement-point allow, or a
   * require_approval such an approval released. A deny naming it closes the gate. A ToolCall with no effect
   * class, and a consequential decision no ToolCall executes, are unjudged. */
  private conductGate(events: Event[]): void {
    const policies = new Map<string, Event>();
    for (const e of events) {
      if (this.ptype(e) === "PolicyDecision") policies.set(eventIri(String(e.id)), e);
    }
    const named = new Map<string, Event[]>();
    for (const policy of policies.values()) {
      const request = refsOf(policy).request;
      if (typeof request === "string") {
        const list = named.get(request) ?? [];
        list.push(policy);
        named.set(request, list);
      }
    }
    const executed = new Set<string>();
    for (const event of events) {
      const ptype = this.ptype(event);
      if (ptype !== "ToolCall" && ptype !== "ResourceAccess") continue;
      const node = eventIri(String(event.id));
      const refs = refsOf(event);
      const held = this.heldDecision(refs.decision);
      if (ptype === "ToolCall" && held !== null) executed.add(held);
      const effect = dataOf(event).effect_class;
      if (typeof effect !== "string") {
        if (ptype === "ToolCall") this.gate(node, true, false);
        continue;
      }
      const scopes = [...this.actionTasks(event)].map((t) => this.domain.scopesFor(t));
      let approval = scopes.some((ss) => ss.some((sc) => sc.requiresApproval.has(effect)));
      if (
        HIGH_EFFECT.has(effect) &&
        (scopes.length === 0 || scopes.some((ss) => ss.length === 0))
      ) {
        approval = true;
      }
      if (!approval && !HIGH_EFFECT.has(effect)) continue;
      const gating = [...(named.get(node) ?? [])];
      const authorization = refs.authorization;
      if (typeof authorization === "string" && policies.has(authorization)) {
        gating.push(policies.get(authorization) as Event);
      }
      const rulings = new Set(
        gating
          .filter(
            (p) =>
              typeof p.agentcesourceclass === "string" && LOGIN_CLASSES.has(p.agentcesourceclass),
          )
          .map((p) => dataOf(p).decision),
      );
      const denied = gating.some((p) => dataOf(p).decision === "deny");
      const approvals = held !== null ? this.store.objects(held, "agentce:approvedBy") : [];
      const released = approval
        ? rulings.has("allow") || rulings.has("require_approval")
        : !rulings.has("allow") && rulings.has("require_approval");
      if (released || approval) {
        for (const approved of approvals) this.store.addEdge(node, "agentce:releasedBy", approved);
      }
      const met =
        !denied && ((rulings.has("allow") && !approval) || (released && approvals.length > 0));
      this.gate(node, met, true);
      if (!met && held !== null && this.store.humanActorFailed.has(held)) {
        // An approval that fails SPEC §10.4 left the gate shut: the deviation register cannot waive it.
        this.store.humanActorFailed.add(node);
      }
    }
    for (const decision of [...this.decisionTime.keys()].sort()) {
      if (!executed.has(decision) && this.store.isA(decision, "agentce:ConsequentialDecision")) {
        this.gate(decision, true, false);
      }
    }
  }

  /** Materialise CND-04's population and judgment (SPEC §7.7.4; rulings 9 to 14 of 2026-10-12); the Python
   * engine's `_conduct_provenance` documents the reading. A ToolCall that executes a held consequential decision,
   * and every consequential Decision, is a `ConsequentialAction`, judged on its refs.instruction chain
   * (`instructionChain`), the chain's last principal's role (`roleAuthorised`) and the delegations it rests on: a
   * ToolCall's refs.delegation, a Decision's those of the ToolCalls executing it. */
  private conductProvenance(events: Event[]): void {
    const byIri = new Map<string, Event>();
    for (const e of events) byIri.set(eventIri(String(e.id)), e);
    const executing = new Map<string, Event[]>();
    const foci: [string, Event][] = [];
    for (const node of [...byIri.keys()].sort(byteCompare)) {
      const event = byIri.get(node) as Event;
      const ptype = this.ptype(event);
      if (ptype === "ToolCall") {
        const decision = this.heldDecision(refsOf(event).decision);
        if (decision !== null) {
          const list = executing.get(decision) ?? [];
          list.push(event);
          executing.set(decision, list);
          if (this.domain.consequential.has(this.decisionType.get(decision) ?? "")) {
            foci.push([node, event]);
          }
        }
      } else if (ptype === "Decision" && this.store.isA(node, "agentce:ConsequentialDecision")) {
        foci.push([node, event]);
      }
    }
    for (const [node, event] of foci) {
      let [met, judged, terminus] = this.instructionChain(byIri, refsOf(event).instruction);
      const calls = this.ptype(event) === "Decision" ? (executing.get(node) ?? []) : [event];
      if (terminus !== null) {
        const [roleMet, roleJudged] = this.roleAuthorised(byIri, node, event, calls, terminus);
        met = met && roleMet;
        judged = judged && roleJudged;
      }
      const delegations = calls.map((call) => refsOf(call).delegation).filter((d) => !isAbsent(d));
      for (const ref of delegations) {
        const held = typeof ref === "string" ? byIri.get(ref) : undefined;
        if (held === undefined || this.ptype(held) !== "DelegationIssued") {
          judged = false;
        } else if (
          !this.store.literalValues(ref as string, "agentce:chainVerified").includes("true")
        ) {
          met = false;
        }
      }
      if (delegations.length === 0) {
        judged = false;
      }
      // A definite failure beats a reading the records cannot finish (critic r2 F3).
      judged = judged || !met;
      this.store.addType(node, CONSEQUENTIAL_ACTION);
      this.literal(node, "agentce:provenanceMet", met);
      this.literal(node, "agentce:provenanceJudged", judged);
    }
  }

  /** [met, judged, the chain's last Instruction] for CND-04's instruction chain from `instruction` (see
   * `conductProvenance`). A parent cycle never terminates, so it is unjudged and has no last Instruction. */
  private instructionChain(
    byIri: Map<string, Event>,
    instruction: unknown,
  ): [boolean, boolean, Event | null] {
    const start = typeof instruction === "string" ? byIri.get(instruction) : undefined;
    if (start === undefined || this.ptype(start) !== "Instruction") {
      return [false, true, null];
    }
    let met = true;
    let judged = true;
    const seen = new Set<string>();
    let node: unknown = instruction;
    let terminus: Event | null = null;
    while (typeof node === "string") {
      if (seen.has(node)) {
        judged = false;
        terminus = null;
        break;
      }
      seen.add(node);
      const event = byIri.get(node);
      if (event === undefined || this.ptype(event) !== "Instruction") {
        judged = false;
        terminus = null;
        break;
      }
      terminus = event;
      const data = dataOf(event);
      const refs = refsOf(event);
      if (
        !isOneOf(data.source_class, AUTHORISED_INSTRUCTION) ||
        !(isRecord(data.principal) && principalKey(data.principal))
      ) {
        met = false;
      }
      const origin = refs.origin;
      if (!isAbsent(origin)) {
        const held = typeof origin === "string" ? byIri.get(origin) : undefined;
        const otype = held !== undefined ? this.ptype(held) : null;
        const trust =
          held !== undefined && otype === "MemoryRead" ? dataOf(held).trust_min : undefined;
        if (
          otype === "ToolCall" ||
          otype === "ResourceAccess" ||
          (otype === "MemoryRead" && isOneOf(trust, UNTRUSTED_TRUST))
        ) {
          met = false;
        } else if (otype !== "MemoryRead" || trust !== "trusted") {
          judged = false;
        }
      }
      node = refs.parent;
      if (!isAbsent(node) && typeof node !== "string") {
        judged = false;
        terminus = null;
      }
    }
    return [met, judged, terminus];
  }

  /**
   * CND-06's population and judgment (SPEC §7.7.4; rulings 16 A, 17 B, 18 B), as graph.py `_conduct_refusals`: an
   * Instruction needs a Refusal when its source_class is absent, not a string or untrusted; one declaring an authorised
   * class is in scope, unjudged, when the records do not show it (no principal id where §6.2.3 requires one, or not
   * attested for every held call or Decision acting on it, or for itself when nothing acts on it). An enforcement-point
   * (or better) deny needs a Refusal of the instruction its request acts on; it is unjudged when that cannot be shown.
   */
  private conductRefusals(events: Event[]): void {
    const byIri = new Map<string, Event>();
    for (const e of events) byIri.set(eventIri(String(e.id)), e);
    const acting = new Map<string, string[]>();
    for (const [node, event] of byIri) {
      const instruction = refsOf(event).instruction;
      if (["ToolCall", "Decision"].includes(this.ptype(event)) && typeof instruction === "string") {
        const list = acting.get(instruction) ?? [];
        list.push(node);
        acting.set(instruction, list);
      }
    }
    let judge: SourceClassJudge | null = null;
    const refused = (instruction: string): boolean =>
      this.store.objects(instruction, "agentce:refusedBy").length > 0;
    const mark = (node: string, met: boolean, shown: boolean): void => {
      this.literal(node, "agentce:refusalInScope", true);
      this.literal(node, "agentce:refusalMet", met);
      this.literal(node, "agentce:refusalNeedShown", shown);
    };
    for (const node of [...byIri.keys()].sort(byteCompare)) {
      const event = byIri.get(node) as Event;
      const ptype = this.ptype(event);
      const data = dataOf(event);
      if (ptype === "Instruction") {
        const sourceClass = data.source_class;
        if (!isOneOf(sourceClass, TRUSTED_INSTRUCTION)) {
          mark(node, refused(node), true);
          continue;
        }
        judge ??= new SourceClassJudge(this.store, byIri);
        const j = judge;
        const shown =
          (!isOneOf(sourceClass, AUTHORISED_INSTRUCTION) ||
            (isRecord(data.principal) && principalKey(data.principal) !== "")) &&
          (acting.get(node) ?? [node]).every((focus) => j.attested(focus, node));
        if (!shown) mark(node, true, refused(node));
      } else if (
        ptype === "PolicyDecision" &&
        isOneOf(event.agentcesourceclass, AT_OR_ABOVE_EP) &&
        data.decision === "deny"
      ) {
        const instruction = this.deniedInstruction(byIri, refsOf(event));
        if (instruction === null) mark(node, true, false);
        else if (instruction) mark(node, refused(instruction), true);
      }
    }
  }

  /**
   * The instruction a deny's request acts on: its refs.instruction, or that of the held Decision its refs.decision
   * names; "" when the request is an enforcement-point record naming none (outside the deny clause); null when the
   * records cannot show it (graph.py `_denied_instruction`).
   */
  private deniedInstruction(
    byIri: Map<string, Event>,
    refs: Record<string, unknown>,
  ): string | null {
    const request = refs.request;
    const event = typeof request === "string" ? byIri.get(request) : undefined;
    if (event === undefined) return null;
    const instruction = refsOf(event).instruction;
    if (typeof instruction === "string") return instruction;
    const decision = refsOf(event).decision;
    if (decision !== undefined && decision !== null) {
      const held = byIri.get(this.heldDecision(decision) ?? "");
      if (held === undefined) return null;
      const named = refsOf(held).instruction;
      if (typeof named === "string") return named;
    }
    return isOneOf(event.agentcesourceclass, AT_OR_ABOVE_EP) ? "" : null;
  }

  /** [met, judged]: whether the chain's last principal holds a role every scope of the action's task authorises
   * (SPEC §7.7.4 "for the task type", §7.7.6 `authorised_principal_roles`). A Decision naming no task takes the
   * tasks of the calls executing it. No scope, a scope that declares no roles, or a principal with no role cannot
   * be judged (fail closed, HR-5); a role outside a declared list does not meet the control. Below
   * enforcement_point the role is the agent's word, so each enforcement-point witness of the same principal must
   * record the same role (`witnessRoles`; inbox row 358). */
  private roleAuthorised(
    byIri: Map<string, Event>,
    node: string,
    event: Event,
    calls: Event[],
    terminus: Event,
  ): [boolean, boolean] {
    let tasks = this.actionTasks(event);
    if (tasks.size === 0 && this.ptype(event) === "Decision") {
      tasks = new Set(calls.flatMap((c) => [...this.actionTasks(c)]));
    }
    let lists = [...tasks].sort(byteCompare).map((t) => this.domain.scopesFor(t));
    if (lists.length === 0) {
      lists = [this.domain.scopesFor("")];
    }
    const scopes = lists.flat();
    if (
      lists.some((l) => l.length === 0) ||
      scopes.some((s) => s.authorisedPrincipalRoles === null)
    ) {
      return [true, false];
    }
    const principal = dataOf(terminus).principal;
    if (!isRecord(principal) || !principalKey(principal)) {
      return [true, true]; // no principal already fails the chain (met false)
    }
    const declared = principal.role;
    const role = typeof declared === "string" ? pyStrip(declared) : "";
    if (!role) {
      return [true, false];
    }
    let met = scopes.every((s) => s.authorisedPrincipalRoles?.has(role) ?? false);
    let judged = true;
    if (!isOneOf(terminus.agentcesourceclass, AT_OR_ABOVE_EP)) {
      for (const witnessed of this.witnessRoles(byIri, node, calls, terminus)) {
        if (witnessed === null) {
          judged = false;
        } else if (witnessed !== role) {
          met = false;
        }
      }
    }
    return [met, judged];
  }

  /** The role (trimmed, null when absent or blank) each enforcement-point witness records for the terminus's
   * principal: a PolicyDecision allow whose refs.request is the action or a call executing it, and a twin
   * Instruction of the same content_ref, refs.task, source_class and principal id (SPEC §7.7.5). */
  private witnessRoles(
    byIri: Map<string, Event>,
    node: string,
    calls: Event[],
    terminus: Event,
  ): (string | null)[] {
    const data = dataOf(terminus);
    const refs = refsOf(terminus);
    const pid = principalKey(data.principal as Record<string, unknown>);
    const requests = new Set([node, ...calls.map((c) => eventIri(String(c.id)))]);
    const roles: (string | null)[] = [];
    for (const other of byIri.values()) {
      if (other === terminus || other.agentcesourceclass !== "enforcement_point") {
        continue;
      }
      const odata = dataOf(other);
      const orefs = refsOf(other);
      const witness = odata.principal;
      if (!isRecord(witness) || principalKey(witness) !== pid) {
        continue;
      }
      const ptype = this.ptype(other);
      const allows =
        ptype === "PolicyDecision" &&
        odata.decision === "allow" &&
        typeof orefs.request === "string" &&
        requests.has(orefs.request);
      const twin =
        ptype === "Instruction" &&
        !isAbsent(data.content_ref) &&
        !isAbsent(refs.task) &&
        pyEquals(odata.content_ref ?? null, data.content_ref ?? null) &&
        pyEquals(orefs.task ?? null, refs.task ?? null) &&
        pyEquals(odata.source_class ?? null, data.source_class ?? null);
      if (!allows && !twin) {
        continue;
      }
      const role = typeof witness.role === "string" ? pyStrip(witness.role) : "";
      roles.push(role || null);
    }
    return roles;
  }

  private gate(node: string, met: boolean, judged: boolean): void {
    this.store.addType(node, GATED_ACTION);
    this.literal(node, "agentce:gateMet", met);
    this.literal(node, "agentce:gateJudged", judged);
  }

  /** Compute the Conduct within-scope and within-budget flags (SPEC §7.7, CND-01/CND-07): an
   * action is out of scope when a `PolicyDecision` denies its request, or when it is a ToolCall in a
   * task whose declared effect scope does not allow its tool and effect class (`withinTaskScope`);
   * it is over budget when a `Refusal` with reason class `budget_exceeded` names it, or when its task's
   * recorded usage exceeds a budget a scope of the task declares (`taskBudget`). An action in no task
   * and never denied reads within scope. */
  private conductScopeBudget(events: Event[]): void {
    const denied = new Set<string>();
    const overBudget = new Set<string>();
    const usage = new TaskUsage();
    for (const event of events) {
      const ptype = this.ptype(event);
      const data = dataOf(event);
      const refs = refsOf(event);
      const request = refs.request;
      if (ptype === "PolicyDecision" && data.decision === "deny" && typeof request === "string") {
        denied.add(request);
      }
      if (
        ptype === "Refusal" &&
        data.reason_class === "budget_exceeded" &&
        typeof request === "string"
      ) {
        overBudget.add(request);
      }
      if (ptype === "ModelCall" || ptype === "ToolCall") {
        usage.add(ptype, data, this.actionTasks(event));
      }
    }
    for (const event of events) {
      const ptype = this.ptype(event);
      if (ptype !== "ToolCall" && ptype !== "ResourceAccess" && ptype !== "ModelCall") {
        continue;
      }
      const node = eventIri(String(event.id));
      const tasks = this.actionTasks(event);
      if (ptype === "ModelCall") {
        if (tasks.size === 0) {
          continue;
        }
        // A model call in a task is consumption CND-07 judges (SPEC §7.7.3 ModelCall.usage).
        this.store.addType(node, TASK_MODEL_CALL);
      } else {
        const inScope = !denied.has(node) && (ptype !== "ToolCall" || this.withinTaskScope(event));
        this.literal(node, "agentce:withinScope", inScope);
      }
      const [within, judged] = this.taskBudget(tasks, usage);
      this.literal(node, "agentce:withinBudget", within && !overBudget.has(node));
      if (!judged) {
        this.store.addType(node, BUDGET_UNJUDGED);
        this.literal(node, "agentce:budgetJudged", false);
      }
    }
  }

  /** Whether an action's tasks stayed within every budget their scopes declare (SPEC §7.7.4 CND-07,
   * §7.7.6 budgets), and whether the records can tell; the Python engine's `_task_budget` documents
   * the reading. */
  private taskBudget(tasks: Set<string>, usage: TaskUsage): [boolean, boolean] {
    let within = true;
    let judged = true;
    for (const task of tasks) {
      const scopes = this.domain.scopesFor(task);
      if (scopes.length === 0) {
        judged &&= this.domain.taskScopes.size === 0 && !this.domain.taskScopesMalformed;
        continue;
      }
      for (const scope of scopes) {
        for (const [dimension, limit] of scope.budgets) {
          const verdict = usage.verdict(task, dimension, limit);
          within &&= verdict !== false;
          judged &&= verdict !== null;
        }
      }
    }
    return [within, judged];
  }

  /** Flag every ToolCall/Decision that acts on an instruction whose chain passes through an untrusted
   * source class (SPEC §7.7.4, CND-05). The chain is the instruction's `refs.parent` and `refs.origin`
   * lineage (`agentce:derivedFrom`, SPEC §6.3) for as many hops as the records show. It passes through
   * an untrusted source class at an instruction of an untrusted Appendix F class, at a tool call or
   * resource access (the content they produce is `tool_output` or `retrieved`), at a parent or origin the
   * bundle does not hold, and at anything ROB-02's closures reached (`tainted`: an untrusted memory
   * read or write, content the memory guard never ruled on, or content that used either). An instruction
   * whose source class is not a declared trusted one (none given, say) cannot show a trusted root
   * either, so it fails closed. So does an action whose `refs.instruction` names no held Instruction
   * (nothing the bundle holds, or an event or record of another type: SPEC §6.2.3 types the ref
   * Ref→Instruction). A ToolCall that names no instruction at all (`refs.instruction` absent, null or
   * not a string, though SPEC §6.2.3 requires it) is not flagged, but the records cannot show where it
   * came from, so `agentce:actsOnJudged` is false and CND-05 reads insufficient_evidence for it (HR-5);
   * a Decision's ref is optional, so it is always judged. A closure over all events, so the acting
   * event may precede its instruction and cycles end. */
  private actsOnUntrusted(events: Event[], tainted: Set<string>): void {
    const untrusted = new Set(tainted);
    const derived = new Map<string, Set<string>>();
    const held = new Set<string>();
    for (const event of events) {
      const ptype = this.ptype(event);
      const node = eventIri(String(event.id));
      held.add(node);
      if (ptype === "ToolCall" || ptype === "ResourceAccess") {
        untrusted.add(node);
      } else if (ptype === "Instruction") {
        if (!isOneOf(dataOf(event).source_class, TRUSTED_INSTRUCTION)) {
          untrusted.add(node);
        }
        const refs = refsOf(event);
        for (const key of ["parent", "origin"]) {
          flow(derived, refs[key], node);
        }
      }
    }
    // A lineage the bundle does not hold cannot show a trusted root, so it fails closed.
    for (const source of derived.keys()) {
      if (!held.has(source)) {
        untrusted.add(source);
      }
    }
    reach(untrusted, derived);
    for (const event of events) {
      const ptype = this.ptype(event);
      if (ptype !== "ToolCall" && ptype !== "Decision") {
        continue;
      }
      const instruction = refsOf(event).instruction;
      const named = typeof instruction === "string";
      const acts =
        named && (untrusted.has(instruction) || !this.instructionUntrusted.has(instruction));
      const node = eventIri(String(event.id));
      this.literal(node, "agentce:actsOnUntrusted", acts);
      this.literal(node, "agentce:actsOnJudged", named || ptype === "Decision");
    }
  }

  /** Flag whether each Decision kept untrusted content out, and whether the memory guard ruled on all
   * it used (SPEC §7.4, ROB-02).
   *
   * Untrusted content starts at a MemoryWrite that marks its record untrusted (`flagsUntrusted`); a MemoryRead
   * whose `trust_min` is untrusted or quarantined; and an instruction with an untrusted Appendix F source class
   * (CND-05's rule). Content the guard never ruled on starts at a MemoryRead not reported by an enforcement point;
   * a record no enforcement-point read at `trust_min` trusted covers, unless every write of it is a ruled
   * enforcement-point write (an unruled rewrite can change what the guard ruled on, 18.120); a record another
   * subject's write marked untrusted or rewrote with no ruling, whatever this subject's own records say of it (18.120);
   * a record another subject's unreadable MemoryWrite may have changed (`unreadRecords`, ROB-02 only, 18.172); and a
   * ref the bundle does not hold.
   * Both flow along every edge that carries content into an event (CONTENT_IN, CONTENT_OUT, a read to
   * its consumer, a record to and from its writes) for as many hops as the records show. Closures over
   * all events, so order and cycles never matter. Returns both closures, which CND-05's instruction
   * chain also reads (`actsOnUntrusted`). */
  private robustToUntrusted(events: Event[]): Set<string> {
    const flows = new Map<string, Set<string>>();
    const tainted = new Set<string>();
    const unruled = new Set<string>();
    const held = new Set<string>();
    const records = new Set<string>();
    // Records an enforcement-point read with a trust_min covers; records with a ruled enforcement-point write;
    // records with any other write.
    const readRuled = new Set<string>();
    const writeRuled = new Set<string>();
    const rewritten = new Set<string>();
    // The stores this subject's memory events name each record under (none when they name no store).
    const stores = new Map<string, Set<string>>();
    for (const event of events) {
      const ptype = this.ptype(event);
      const data = dataOf(event);
      const refs = refsOf(event);
      const node = eventIri(String(event.id));
      held.add(node);
      const enforced = event.agentcesourceclass === "enforcement_point";
      const store = data.store;
      for (const key of CONTENT_IN) {
        const value = data[key];
        for (const ref of Array.isArray(value) ? value : [value]) {
          flow(flows, ref, node);
        }
      }
      for (const key of REFS_IN) {
        flow(flows, refs[key], node);
      }
      for (const key of CONTENT_OUT) {
        flow(flows, node, data[key]);
        if (typeof data[key] === "string") {
          held.add(data[key] as string);
        }
      }
      flow(flows, node, refs.consumer);
      if (ptype === "MemoryWrite") {
        const record = data.record_ref;
        // A record and each write of it stand for the same content.
        flow(flows, record, node);
        if (typeof record === "string") {
          records.add(record);
          noteStore(stores, record, store);
          (ruledWrite(event, data) ? writeRuled : rewritten).add(record);
        }
        if (flagsUntrusted(data)) {
          tainted.add(node);
        }
      } else if (ptype === "MemoryRead") {
        const read = data.record_refs;
        // A guard's read rules on its records only when it let through trusted records alone (18.172).
        const ruling = enforced && data.trust_min === "trusted";
        for (const record of Array.isArray(read) ? read : []) {
          if (typeof record === "string") {
            records.add(record);
            noteStore(stores, record, store);
            if (ruling) {
              readRuled.add(record);
            }
          }
        }
        if (!enforced) {
          unruled.add(node);
        }
        if (isOneOf(data.trust_min, UNTRUSTED_TRUST)) {
          tainted.add(node);
        }
      }
    }
    for (const [iri, untrusted] of this.instructionUntrusted) {
      if (untrusted) {
        tainted.add(iri);
      }
    }
    for (const record of records) {
      if (!readRuled.has(record) && (!writeRuled.has(record) || rewritten.has(record))) {
        unruled.add(record);
      }
    }
    for (const ref of flows.keys()) {
      if ((!held.has(ref) && !records.has(ref)) || this.foreignUntrusted.has(ref)) {
        unruled.add(ref);
      }
    }
    const fed = new Set<string>();
    for (const targets of flows.values()) {
      for (const target of targets) {
        fed.add(target);
      }
    }
    reach(tainted, flows);
    reach(unruled, flows);
    // A record another subject's unreadable write may have changed is one no guard ruled on (SPEC §7.4); nobody
    // could read that content, so it stays out of the closures CND-05 reads, as an unclassed instruction does.
    const unread = unreadRecords(this.foreignUnreadable, records, stores);
    for (const record of unread) {
      if (!flows.has(record)) {
        unread.delete(record);
      }
    }
    reach(unread, flows);
    // An instruction that declares no source class cannot show its content came from a trusted source, so
    // ROB-02 reads it as untrusted content (18.127); CND-05's own chain rule already fails it, so it stays out
    // of the closures returned to that rule.
    const unclassed = new Set(this.instructionUnclassed);
    reach(unclassed, flows);
    const decisions = events.filter((e) => this.ptype(e) === "Decision");
    // A decision nothing recorded flows into used nothing the guard could have ruled on, so ROB-02 cannot
    // judge it (ruling 15): it reads untrustedContentRuledOn false itself, and taints nothing downstream.
    const unfed = new Set(
      decisions.map((e) => eventIri(String(e.id))).filter((iri) => !fed.has(iri)),
    );
    this.robustLiterals(
      decisions,
      new Set([...tainted, ...unclassed]),
      new Set([...unruled, ...unread, ...unfed]),
    );
    for (const node of unruled) {
      tainted.add(node);
    }
    return tainted;
  }

  /** ROB-02's two literals on each of `decisions` (see `robustToUntrusted`). */
  private robustLiterals(decisions: Event[], tainted: Set<string>, unruled: Set<string>): void {
    for (const event of decisions) {
      const node = eventIri(String(event.id));
      this.store.addLiteral(
        node,
        "agentce:robustToUntrustedContent",
        tainted.has(node) ? "false" : "true",
        BOOL,
      );
      this.store.addLiteral(
        node,
        "agentce:untrustedContentRuledOn",
        unruled.has(node) ? "false" : "true",
        BOOL,
      );
    }
  }

  private materialise(events: Event[]): void {
    const byHuman = this.mapOversight(events);
    const incidentDecisions = this.incidentDecisions(events);
    const policyDecisions = new Set(
      events.filter((e) => this.ptype(e) === "PolicyDecision").map((e) => eventIri(String(e.id))),
    );
    for (const event of events) {
      const node = eventIri(String(event.id));
      const ptype = this.ptype(event);
      const data = dataOf(event);
      if (ptype === "DelegationIssued") {
        this.chainVerified(node, data);
      }
      if (ptype === "ToolCall") {
        this.executesConsequential(node, refsOf(event));
      }
      if (ptype === "Decision") {
        this.oversightMatches(node, data);
        this.explanationReconstructable(node);
        this.oversightCoverage(node, data);
        this.literal(
          node,
          "agentce:triggersIncident",
          incidentDecisions === null || incidentDecisions.has(node),
        );
        this.riskReviewed(node, refsOf(event), policyDecisions);
      }
      if (ptype === "Override" || ptype === "Interrupt") {
        this.intervention(node, ptype, event, byHuman.get(node) === true);
      }
      if (ptype === "Incident") {
        this.literal(
          node,
          "agentce:incidentResponded",
          text(data.detected_at) !== null && INCIDENT_RESPONSES.some((k) => text(data[k]) !== null),
        );
      }
      if (ptype === "Outcome") {
        this.adverseOutcomeLinked(node, data);
      }
      if (ptype === "ToolCall" || ptype === "ModelCall") {
        this.componentDeclared(node, ptype, data);
      }
      if (ptype === "BundleLoaded") {
        this.declaredComponentsObserved(node, data);
      }
      this.chainTerminus(node, data);
    }
    this.actsOnUntrusted(events, this.robustToUntrusted(events));
    this.conductScopeBudget(events);
    this.conductGate(events);
    this.conductProvenance(events);
    this.conductRefusals(events);
    this.precededBy();
  }

  private literal(node: string, predicate: string, value: boolean): void {
    this.store.addLiteral(node, predicate, value ? "true" : "false", BOOL);
  }

  /**
   * INC-03: the held decisions the `Incident` events name in `related_refs[]` or `refs.decision`,
   * directly or through the `refs.decision` of the event named (an `Outcome`, a `ToolCall`, an
   * `Override`, ...). `null` when any name does not lead to a held decision: the records cannot show
   * which decision triggered that incident, so every decision is held to the rule (fail closed).
   */
  private incidentDecisions(events: Event[]): Set<string> | null {
    const out = new Set<string>();
    for (const event of events) {
      if (this.ptype(event) !== "Incident") {
        continue;
      }
      const related = dataOf(event).related_refs;
      const names: unknown[] = Array.isArray(related) ? [...related] : [];
      const decision = refsOf(event).decision;
      if (!isAbsent(decision)) {
        names.push(decision);
      }
      if (names.length === 0) {
        return null;
      }
      for (const ref of names) {
        const named =
          typeof ref === "string"
            ? this.decisionTime.has(ref)
              ? ref
              : this.refDecision.get(ref)
            : undefined;
        if (named === undefined || !this.decisionTime.has(named)) {
          return null;
        }
        out.add(named);
      }
    }
    return out;
  }

  /** RSK-02 (Art. 9): a review of the decision, or a `refs.authorization` (or `refs.request`) naming a
   * `PolicyDecision` the bundle holds. A name that leads to no held policy decision gates nothing. */
  private riskReviewed(
    node: string,
    refs: Record<string, unknown>,
    policyDecisions: Set<string>,
  ): void {
    this.literal(
      node,
      "agentce:riskReviewed",
      this.decisionReviewed.has(node) ||
        ["authorization", "request"].some((k) => isOneOf(refs[k], policyDecisions)),
    );
  }

  /** The held decision a `refs.decision` names (SPEC §6.3 approvedBy): its IRI, else the ref trimmed of `ID_PAD` when
   * that is a held decision's IRI, else `agentce:event/<trimmed ref>` when that is (a bare id); `null` when it names
   * no held decision. Exact match first, so no name resolves to another held decision. */
  /** The names a refs.decision may mean an event by, in the order they are tried: as written, trimmed of ID_PAD,
   * and agentce:event/<trimmed ref> (a bare id); none when the ref is no string. */
  private refNames(ref: unknown): string[] {
    if (typeof ref !== "string") {
      return [];
    }
    const trimmed = ruleId(ref) ?? "";
    return [ref, trimmed, eventIri(trimmed)];
  }

  private heldDecision(ref: unknown): string | null {
    return this.refNames(ref).find((name) => this.decisionTime.has(name)) ?? null;
  }

  /** Whether each ApprovalDecided, Override and Interrupt meets the SPEC §10.4 human-actor rule. An ApprovalDecided
   * reviews the decision it names (`agentce:reviewedBy`; OVS-01, INC-03, RSK-02) only when it does, whatever its
   * outcome; it also approves it (`agentce:approvedBy`; CND-02, OVS-08) only when its outcome is approve, it is
   * recorded at or before every ToolCall that executes the decision (18.141) and every Outcome that names it (18.144:
   * the decision had already taken effect), and the same human recorded no other
   * decision on it at or after it, or at a time that cannot be placed against it, unless that decision came after
   * every such ToolCall (18.142: each human's most recent decision governs); one that fails it is no human's review.
   * Every refs.decision is read through `heldDecision` (18.148), and a ToolCall or Outcome naming no held decision
   * (a null refs.decision included, 18.157) bounds every decision's approvals. */
  private mapOversight(events: Event[]): Map<string, boolean> {
    const byHuman = new Map<string, boolean>();
    const actionTimes = new Map<string, string[]>();
    const outcomeTimes = new Map<string, string[]>();
    // decision -> human -> that human's decisions on it: [node, time, approve]
    // The time of every ToolCall and Outcome whose refs.decision names no held decision: it may be any decision's
    // effect, so it bounds every decision's approvals (fails closed, 18.148), though it withdraws none.
    const unresolvedTimes: string[] = [];
    const decided = new Map<string, Map<string, Array<[string, string, boolean]>>>();
    for (const event of events) {
      const ptype = this.ptype(event);
      const refs = refsOf(event);
      // No refs.decision key is no claim; a present null names no held decision (18.157).
      if ((ptype !== "ToolCall" && ptype !== "Outcome") || !Object.hasOwn(refs, "decision")) {
        continue;
      }
      const ref = refs.decision;
      const time = String(event.time ?? "");
      const decision = this.heldDecision(ref);
      if (decision === null) {
        unresolvedTimes.push(time);
        continue;
      }
      const map = ptype === "ToolCall" ? actionTimes : outcomeTimes;
      const times = map.get(decision) ?? [];
      times.push(time);
      map.set(decision, times);
    }
    for (const event of events) {
      const ptype = this.ptype(event);
      if (!OVERSIGHT_TYPES.has(ptype)) {
        continue;
      }
      const node = eventIri(String(event.id));
      const key = this.verifiedActor(node, event);
      byHuman.set(node, key !== null);
      const ref = refsOf(event).decision;
      const held = ptype === "ApprovalDecided" ? this.heldDecision(ref) : null;
      if (key === null) {
        // What the deviation register cannot waive (SPEC §10.4): the failing record, and the held decision a failing
        // approval names.
        this.store.humanActorFailed.add(node);
        if (held !== null) {
          this.store.humanActorFailed.add(held);
        }
      }
      if (ptype !== "ApprovalDecided" || typeof ref !== "string") {
        continue;
      }
      // A name that leads to no held decision keeps its own key: it reviews and approves no held decision.
      const decision = held ?? ref;
      const approve = dataOf(event).outcome === "approve";
      const actor = humanId(dataOf(event).actor);
      // A human's own no withdraws that human's approve even when it fails §10.4 (fail closed, 18.142).
      const human = key ?? (actor !== null && !approve ? principalIri(actor, this.key) : null);
      if (human === null) {
        continue;
      }
      if (key !== null) {
        this.store.addEdge(decision, "agentce:reviewedBy", node);
        this.decisionReviewed.add(decision);
      }
      let byActor = decided.get(decision);
      if (!byActor) {
        byActor = new Map();
        decided.set(decision, byActor);
      }
      const records = byActor.get(human) ?? [];
      records.push([node, String(event.time ?? ""), approve]);
      byActor.set(human, records);
    }
    for (const [decision, byActor] of decided) {
      const actions = actionTimes.get(decision) ?? [];
      const outcomes = outcomeTimes.get(decision) ?? [];
      for (const [key, records] of byActor) {
        const withdrawals = records
          .filter(([, time, approve]) => !approve && !afterEvery(time, actions))
          .map(([, time]) => time);
        for (const [node, time, approve] of records) {
          if (
            !approve ||
            !beforeEvery(time, actions) ||
            !beforeEvery(time, outcomes) ||
            !beforeEvery(time, unresolvedTimes) ||
            !withdrawals.every((w) => compareLiterals(time, w) === 1)
          ) {
            continue;
          }
          this.store.addEdge(decision, "agentce:approvedBy", node);
          let approvers = this.humanApprovers.get(decision);
          if (!approvers) {
            approvers = new Set();
            this.humanApprovers.set(decision, approvers);
          }
          approvers.add(key);
        }
      }
    }
    return byHuman;
  }

  /** OVS-08 (Art. 14(5)): enough distinct human approvers -- two when the decision's observed or
   * domain-declared oversight modality is `dual_control`, otherwise one. */
  private oversightCoverage(node: string, data: Record<string, unknown>): void {
    const dtype = data.decision_type;
    const declared =
      typeof dtype === "string" ? this.domain.requiredOversight.get(dtype) : undefined;
    const dual = data.oversight_modality === "dual_control" || declared === "dual_control";
    const approvers = this.humanApprovers.get(node)?.size ?? 0;
    this.literal(node, "agentce:oversightCoverageComplete", approvers >= (dual ? 2 : 1));
  }

  /** OVS-07: an `Override` is effective when it names a held decision and records a replacement that
   * differs from the original; an `Interrupt` when a named mechanism halted or paused the agent.
   * Either is by a human when its actor meets the SPEC §10.4 human-actor rule. */
  private intervention(node: string, ptype: string, event: Event, byHuman: boolean): void {
    const data = dataOf(event);
    let effective: boolean;
    if (ptype === "Override") {
      const replacement = data.replacement ?? null;
      effective =
        this.heldDecision(refsOf(event).decision) !== null &&
        replacement !== null &&
        !pyEquals(replacement, data.original ?? null);
    } else {
      effective =
        isOneOf(data.effect, INTERRUPT_STOPS) && isOneOf(data.mechanism, INTERRUPT_MECHANISMS);
    }
    this.literal(node, "agentce:interventionEffective", effective);
    this.literal(node, "agentce:interventionByHuman", byHuman);
  }

  private dangling(node: string, event: Event): void {
    const candidates: string[] = [];
    for (const value of Object.values(refsOf(event))) {
      if (typeof value === "string") {
        candidates.push(value);
      }
    }
    const used = Array.isArray(dataOf(event).used) ? (dataOf(event).used as unknown[]) : [];
    for (const value of used) {
      if (typeof value === "string") {
        candidates.push(value);
      }
    }
    for (const value of candidates) {
      if (isEventRef(value) && !this.eventIris.has(value)) {
        this.store.addLiteral(node, "agentce:danglingRef", value);
        this.danglingNodes.add(node);
      }
    }
  }

  /** INC-01 (SPEC §7.4): an adverse outcome is linked to the consequential decision it resulted
   * from; a non-adverse outcome carries no such expectation and is vacuously linked. A
   * `refs.decision` that is missing, dangling (names no ingested event), or resolves to a decision
   * that is not itself a ConsequentialDecision does not count as linked -- a real link requires a
   * real consequential decision, not merely the shape of one. */
  private adverseOutcomeLinked(node: string, data: Record<string, unknown>): void {
    const adverse = Boolean(data.adverse ?? false);
    const decision = this.outcomeDecision.get(node);
    const linked =
      !adverse ||
      (decision !== undefined &&
        this.eventIris.has(decision) &&
        this.store.isA(decision, "agentce:ConsequentialDecision"));
    this.store.addLiteral(node, "agentce:adverseOutcomeLinked", linked ? "true" : "false", BOOL);
  }

  /** TRN-03 (SPEC §7.6): affected persons are informed (notifiedBy a Notice) and the evidence chain
   * resolves on both ends of that notification -- no danglingRef on the decision itself (every ref
   * or `used` value it names resolves to an ingested event) AND no danglingRef on the Notice that
   * notified it (round-2 critic finding: a Notice's own dangling ref, e.g. a free-form
   * explanation_ref, must not be invisible just because `dangling` lands the literal on the Notice
   * node, not the Decision node) -- so the evidence an explanation would be built from is actually
   * present on both legs. A decision can be notifiedBy more than one Notice; checking only the last
   * one mapped made the result depend on event order, so this checks every notifying Notice and
   * passes if any one of them is clean (a verifier-found regression, fixed this item). Scoped
   * narrower than the Notice's own content_ref or the Decision's own rationale_claim_ref (SPEC model
   * attributes, not refs edges): neither is materialised as a graph reference anywhere today, so
   * neither can dangle in this model yet -- disclosed, out of this item's scope (see this item's
   * contract, split to 18.37h). */
  private explanationReconstructable(node: string): void {
    const notices = this.decisionNotice.get(node);
    const reconstructable =
      !!notices &&
      notices.size > 0 &&
      !this.danglingNodes.has(node) &&
      [...notices].some((notice) => !this.danglingNodes.has(notice));
    this.store.addLiteral(
      node,
      "agentce:explanationReconstructable",
      reconstructable ? "true" : "false",
      BOOL,
    );
  }

  // DOC-01 S1: a call exercises a component some BundleLoaded manifest declares -- one of its names is
  // declared for its family, and when both the call and the declaration state a version or digest,
  // they agree. A call that names nothing cannot be shown declared.
  private componentDeclared(node: string, ptype: string, data: Record<string, unknown>): void {
    const [family, names, version] = operated(ptype, data);
    const known = this.declared[family];
    const declared = names.some((name) => {
      const pins = known.get(name);
      return pins !== undefined && (pins.size === 0 || version === null || pins.has(version));
    });
    this.store.addLiteral(node, "agentce:componentDeclared", declared ? "true" : "false", BOOL);
  }

  // DOC-01 S2: every model and MCP server this manifest declares is exercised by at least one call in
  // the subject's records (declared but never seen is drift too). A manifest with nothing to compare -- no
  // call in the records and no model or MCP server to see -- is not a component record, so DOC-01 never
  // reads conformant on it alone (18.37j).
  private declaredComponentsObserved(node: string, data: Record<string, unknown>): void {
    const mustSee = declaredComponents(data).flatMap(([kind, name]) => {
      const family = mustOperate(kind);
      return family === null ? [] : [[family, name] as const];
    });
    if (!this.hasCalls && mustSee.length === 0) {
      return;
    }
    this.store.addType(node, COMPONENT_RECORD);
    const observed = mustSee.every(([family, name]) => this.operated[family].has(name));
    this.store.addLiteral(
      node,
      "agentce:declaredComponentsObserved",
      observed ? "true" : "false",
      BOOL,
    );
  }

  private chainVerified(node: string, data: Record<string, unknown>): void {
    const verification = data.verification;
    let verified = isRecord(verification) && verification.status === "verified";
    if ("chain_verified" in data) {
      verified = Boolean(data.chain_verified);
    }
    this.store.addLiteral(node, "agentce:chainVerified", verified ? "true" : "false", BOOL);
  }

  private executesConsequential(node: string, refs: Record<string, unknown>): void {
    const decision = this.heldDecision(refs.decision);
    const consequential =
      decision !== null && this.domain.consequential.has(this.decisionType.get(decision) ?? "");
    this.store.addLiteral(
      node,
      "agentce:executesConsequential",
      consequential ? "true" : "false",
      BOOL,
    );
  }

  private oversightMatches(node: string, data: Record<string, unknown>): void {
    const dtype = data.decision_type;
    const required =
      typeof dtype === "string" ? this.domain.requiredOversight.get(dtype) : undefined;
    const observed = data.oversight_modality;
    const matches = required === undefined || observed === required;
    this.store.addLiteral(
      node,
      "agentce:oversightModalityMatchesDeclared",
      matches ? "true" : "false",
      BOOL,
    );
  }

  private chainTerminus(node: string, data: Record<string, unknown>): void {
    const actedFor = data.acted_for;
    if (Array.isArray(actedFor) && actedFor.length > 0) {
      const [pid] = principalRef(actedFor[actedFor.length - 1]);
      if (pid !== null) {
        this.store.addEdge(node, "agentce:chainTerminus", principalIri(pid, this.key));
      }
    }
  }

  private precededBy(): void {
    const byType = new Map<string, string[]>();
    for (const [node, dtype] of this.decisionType) {
      const bucket = byType.get(dtype);
      if (bucket === undefined) {
        byType.set(dtype, [node]);
      } else {
        bucket.push(node);
      }
    }
    for (const nodes of byType.values()) {
      const ordered = [...nodes].sort((a, b) => {
        const ta = this.decisionTime.get(a) ?? "";
        const tb = this.decisionTime.get(b) ?? "";
        return byteCompare(ta, tb) || byteCompare(a, b);
      });
      ordered.forEach((node, position) => {
        for (let i = position - 1; i >= 0; i--) {
          const earlier = ordered[i] as string;
          if (this.decisionReviewed.has(earlier)) {
            this.store.addEdge(node, "agentce:precededBy", earlier);
            break;
          }
        }
      });
    }
  }
}

export function buildGraph(
  events: Event[],
  options: {
    domain?: DomainBinding;
    store?: GraphStore;
    pseudonymKey?: Buffer;
    /** The records another subject's write marked untrusted or rewrote with no ruling (`flaggedRecords`). */
    foreignUntrusted?: ReadonlySet<string>;
    /** The record_ref and store of each unreadable MemoryWrite another subject holds (18.172). */
    foreignUnreadable?: readonly UnreadableWrite[];
  } = {},
): GraphStore {
  const builder = new Builder(
    options.store ?? new GraphStore(),
    options.domain ?? DomainBinding.empty(),
    options.pseudonymKey ?? ZERO_KEY,
    options.foreignUntrusted ?? new Set(),
    options.foreignUnreadable ?? [],
  );
  return builder.build(events);
}
