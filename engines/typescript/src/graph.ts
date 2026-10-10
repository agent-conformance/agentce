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

import { DomainBinding } from "./domain";
import { ZERO_KEY, eventIri, isEventRef, principalIri } from "./iri";
import { GraphStore } from "./store";
import { byteCompare } from "./util";

const BOOL = "xsd:boolean";
const DATETIME = "xsd:dateTime";
const INTEGER = "xsd:integer";

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
const TRUSTED_INSTRUCTION = new Set([
  "user",
  "operator",
  "service",
  "agent_identified",
  "memory_trusted",
]);

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

function isOneOf(value: unknown, values: Set<string>): boolean {
  return typeof value === "string" && values.has(value);
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function dataOf(event: Event): Record<string, unknown> {
  return isRecord(event.data) ? event.data : {};
}

function refsOf(event: Event): Record<string, unknown> {
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
function pyEquals(a: unknown, b: unknown): boolean {
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

function text(value: unknown): string | null {
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
  // SPEC §10.4 human-actor rule, read in the second pass so file order never matters: event IRI -> [type, source
  // class, agent id, login principal IRI] of every event, the last set only on a SessionStart that names a human
  // (18.140); agent id -> every principal IRI in its delegation chain (its acted_for and its DelegationIssued
  // chains); event IRI -> the principal IRIs of its own acted_for.
  private readonly eventInfo = new Map<
    string,
    [string, string | null, string | null, string | null]
  >();
  private readonly agentChain = new Map<string, Set<string>>();
  // event IRI -> the agents it concerns: its own agent and its CloudEvents subject (the assessed agent), so an event
  // that leaves out the optional agent still concerns the subject.
  private readonly eventAgents = new Map<string, Set<string>>();
  private readonly actedFor = new Map<string, Set<string>>();
  // OVS-08: decision IRI -> the actor IRI of every verified ApprovalDecided that reviewed it.
  private readonly humanReviewers = new Map<string, Set<string>>();
  // INC-03: event IRI -> the decision its refs.decision names, whatever the event type.
  private readonly refDecision = new Map<string, string>();

  constructor(
    private readonly store: GraphStore,
    private readonly domain: DomainBinding,
    private readonly key: Buffer,
  ) {}

  build(events: Event[]): GraphStore {
    const usedClasses = new Set<string>(Object.keys(BASE_SUBCLASS));
    usedClasses.add("prov:Agent");
    usedClasses.add("prov:Activity");
    usedClasses.add("prov:Entity");
    for (const event of events) {
      this.eventIris.add(eventIri(String(event.id)));
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
    const loginPrincipal = ptype === "SessionStart" ? humanId(data.principal) : null;
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
      this.decisionTime.set(node, typeof event.time === "string" ? event.time : "");
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
    const decision = refsOf(event).decision;
    const concerned = new Set([
      ...(this.eventAgents.get(node) ?? []),
      ...((typeof decision === "string" ? this.eventAgents.get(decision) : undefined) ?? []),
    ]);
    const [loginType, loginClass, loginAgent, loginPrincipal] = loginInfo;
    if (loginType !== "SessionStart" || loginClass === null || !LOGIN_CLASSES.has(loginClass)) {
      return null;
    }
    if (loginAgent === null || concerned.has(loginAgent)) {
      return null;
    }
    const actorIri = principalIri(actor, this.key);
    if (loginPrincipal !== actorIri) {
      return null;
    }
    const chain = new Set(this.actedFor.get(node));
    if (typeof decision === "string") {
      for (const p of this.actedFor.get(decision) ?? []) {
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

  private mapDecisionLinks(node: string, ptype: string, event: Event): void {
    const refs = refsOf(event);
    const decision = refs.decision;
    if (typeof decision === "string") {
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
    this.store.addLiteral(node, "agentce:instructionUntrusted", untrusted ? "true" : "false", BOOL);
  }

  /** Compute the Conduct within-scope and within-budget flags (SPEC §7.7, CND-01/CND-07) from the
   * enforcement point's records: an action is out of scope when a `PolicyDecision` denies its
   * request, and over budget when a `Refusal` with reason class `budget_exceeded` names it; both
   * default to conformant, so the flags are inert for a bundle that records neither. */
  private conductScopeBudget(events: Event[]): void {
    const denied = new Set<string>();
    const overBudget = new Set<string>();
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
    }
    for (const event of events) {
      if (this.ptype(event) !== "ToolCall" && this.ptype(event) !== "ResourceAccess") {
        continue;
      }
      const node = eventIri(String(event.id));
      this.store.addLiteral(node, "agentce:withinScope", denied.has(node) ? "false" : "true", BOOL);
      this.store.addLiteral(
        node,
        "agentce:withinBudget",
        overBudget.has(node) ? "false" : "true",
        BOOL,
      );
    }
  }

  /** Flag every ToolCall/Decision that acts on an instruction whose chain passes through an untrusted
   * source class (SPEC §7.7.4, CND-05). The chain is the instruction's `refs.parent` and `refs.origin`
   * lineage (`agentce:derivedFrom`, SPEC §6.3) for as many hops as the records show. It passes through
   * an untrusted source class at an instruction of an untrusted Appendix F class, at a tool call or
   * resource access (the content they produce is `tool_output` or `retrieved`), at a parent or origin the
   * bundle does not hold, and at anything ROB-02's closures reached (`tainted`: an untrusted memory
   * read or write, content the memory guard never ruled on, or content that used either). An instruction
   * whose source class is not a declared trusted one (none given, say) cannot show a trusted root
   * either, so it fails closed. A closure over all events, so the acting event may precede its
   * instruction and cycles end. */
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
      const acts = typeof instruction === "string" && untrusted.has(instruction);
      this.store.addLiteral(
        eventIri(String(event.id)),
        "agentce:actsOnUntrusted",
        acts ? "true" : "false",
        BOOL,
      );
    }
  }

  /** Flag whether each Decision kept untrusted content out, and whether the memory guard ruled on all
   * it used (SPEC §7.4, ROB-02).
   *
   * Untrusted content starts at a MemoryWrite whose trust is untrusted or quarantined, whose guard
   * verdict is quarantine or block, or which carries no trust and no verdict but an untrusted Appendix F
   * `provenance_origin_class`; a MemoryRead whose `trust_min` is untrusted or quarantined; and an
   * instruction with an untrusted Appendix F source class (CND-05's rule). Content the guard never ruled
   * on starts at a MemoryRead not reported by an enforcement point, a record no ruled enforcement-point
   * write and no enforcement-point read with a `trust_min` covers, and a ref the bundle does not hold.
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
    const guarded = new Set<string>();
    for (const event of events) {
      const ptype = this.ptype(event);
      const data = dataOf(event);
      const refs = refsOf(event);
      const node = eventIri(String(event.id));
      held.add(node);
      const enforced = event.agentcesourceclass === "enforcement_point";
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
        const unmarked = isAbsent(data.trust) && isAbsent(data.guard_verdict);
        if (typeof record === "string") {
          records.add(record);
          if (enforced && !unmarked) {
            guarded.add(record);
          }
        }
        if (
          isOneOf(data.trust, UNTRUSTED_TRUST) ||
          isOneOf(data.guard_verdict, UNTRUSTED_VERDICTS) ||
          (unmarked && untrustedSourceClass(data.provenance_origin_class))
        ) {
          tainted.add(node);
        }
      } else if (ptype === "MemoryRead") {
        const read = data.record_refs;
        // A guard's read rules on its records only when it filtered them by trust.
        const ruling = enforced && !isAbsent(data.trust_min);
        for (const record of Array.isArray(read) ? read : []) {
          if (typeof record === "string") {
            records.add(record);
            if (ruling) {
              guarded.add(record);
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
      if (!guarded.has(record)) {
        unruled.add(record);
      }
    }
    for (const ref of flows.keys()) {
      if (!held.has(ref) && !records.has(ref)) {
        unruled.add(ref);
      }
    }
    reach(tainted, flows);
    reach(unruled, flows);
    for (const event of events) {
      if (this.ptype(event) === "Decision") {
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
    for (const node of unruled) {
      tainted.add(node);
    }
    return tainted;
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

  /** Whether each ApprovalDecided, Override and Interrupt meets the SPEC §10.4 human-actor rule. An ApprovalDecided
   * reviews the decision it names (`agentce:reviewedBy`; OVS-01, OVS-08, CND-02, INC-03, RSK-02) only when it does;
   * one that fails it is no human's review. */
  private mapOversight(events: Event[]): Map<string, boolean> {
    const byHuman = new Map<string, boolean>();
    for (const event of events) {
      const ptype = this.ptype(event);
      if (!OVERSIGHT_TYPES.has(ptype)) {
        continue;
      }
      const node = eventIri(String(event.id));
      const key = this.verifiedActor(node, event);
      byHuman.set(node, key !== null);
      const decision = refsOf(event).decision;
      if (ptype !== "ApprovalDecided" || key === null || typeof decision !== "string") {
        continue;
      }
      this.store.addEdge(decision, "agentce:reviewedBy", node);
      this.decisionReviewed.add(decision);
      let reviewers = this.humanReviewers.get(decision);
      if (!reviewers) {
        reviewers = new Set();
        this.humanReviewers.set(decision, reviewers);
      }
      reviewers.add(key);
    }
    return byHuman;
  }

  /** OVS-08 (Art. 14(5)): enough distinct human reviewers -- two when the decision's observed or
   * domain-declared oversight modality is `dual_control`, otherwise one. */
  private oversightCoverage(node: string, data: Record<string, unknown>): void {
    const dtype = data.decision_type;
    const declared =
      typeof dtype === "string" ? this.domain.requiredOversight.get(dtype) : undefined;
    const dual = data.oversight_modality === "dual_control" || declared === "dual_control";
    const reviewers = this.humanReviewers.get(node)?.size ?? 0;
    this.literal(node, "agentce:oversightCoverageComplete", reviewers >= (dual ? 2 : 1));
  }

  /** OVS-07: an `Override` is effective when it names a held decision and records a replacement that
   * differs from the original; an `Interrupt` when a named mechanism halted or paused the agent.
   * Either is by a human when its actor meets the SPEC §10.4 human-actor rule. */
  private intervention(node: string, ptype: string, event: Event, byHuman: boolean): void {
    const data = dataOf(event);
    let effective: boolean;
    if (ptype === "Override") {
      const decision = refsOf(event).decision;
      const replacement = data.replacement ?? null;
      effective =
        typeof decision === "string" &&
        this.decisionTime.has(decision) &&
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
    const decision = refs.decision;
    let consequential = false;
    if (typeof decision === "string") {
      consequential = this.domain.consequential.has(this.decisionType.get(decision) ?? "");
    }
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
  } = {},
): GraphStore {
  const builder = new Builder(
    options.store ?? new GraphStore(),
    options.domain ?? DomainBinding.empty(),
    options.pseudonymKey ?? ZERO_KEY,
  );
  return builder.build(events);
}
