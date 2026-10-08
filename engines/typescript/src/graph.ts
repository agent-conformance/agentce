/**
 * Build the provenance graph from ingested events (SPEC §6.3, §7.2).
 *
 * Each event becomes a typed node with deterministic IRIs, the §6.3 relations become edges, and the
 * engine materialises the glue edges the Portable Shape Profile needs (`agentce:chainTerminus`,
 * `agentce:chainVerified`, `agentce:executesConsequential`, `agentce:oversightModalityMatchesDeclared`,
 * `agentce:danglingRef`, `agentce:precededBy`). The `rdfs:subClassOf*` closure of the class hierarchy
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
  private readonly operated: Record<Family, Set<string>> = { tool: new Set(), model: new Set() };
  private hasCalls = false;

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
    const merged = { ...BASE_SUBCLASS, ...Object.fromEntries(this.domain.subclasses) };
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
    if (isRecord(agent) && typeof agent.id === "string") {
      const agentId = agent.id;
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
      if (ptype === "ToolCall") {
        this.store.addEdge(node, "agentce:executes", decision);
      } else if (ptype === "Outcome") {
        this.store.addEdge(decision, "agentce:resultedIn", node);
        this.outcomeDecision.set(node, decision);
      } else if (ptype === "ApprovalDecided") {
        this.store.addEdge(decision, "agentce:reviewedBy", node);
        this.decisionReviewed.add(decision);
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
    const untrusted = typeof sourceClass === "string" && !TRUSTED_INSTRUCTION.has(sourceClass);
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

  /** Flag every ToolCall/Decision that acts on an untrusted instruction (SPEC §7.7, CND-05). Runs
   * after every event is mapped so the acting event may precede its instruction. */
  private actsOnUntrusted(events: Event[]): void {
    for (const event of events) {
      const ptype = this.ptype(event);
      if (ptype !== "ToolCall" && ptype !== "Decision") {
        continue;
      }
      const instruction = refsOf(event).instruction;
      const untrusted =
        typeof instruction === "string" && (this.instructionUntrusted.get(instruction) ?? false);
      this.store.addLiteral(
        eventIri(String(event.id)),
        "agentce:actsOnUntrusted",
        untrusted ? "true" : "false",
        BOOL,
      );
    }
  }

  private materialise(events: Event[]): void {
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
    this.actsOnUntrusted(events);
    this.conductScopeBudget(events);
    this.precededBy();
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
  options: { domain?: DomainBinding; store?: GraphStore; pseudonymKey?: Buffer } = {},
): GraphStore {
  const builder = new Builder(
    options.store ?? new GraphStore(),
    options.domain ?? DomainBinding.empty(),
    options.pseudonymKey ?? ZERO_KEY,
  );
  return builder.build(events);
}
