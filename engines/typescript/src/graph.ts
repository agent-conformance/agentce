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

class Builder {
  private readonly eventIris = new Set<string>();
  private readonly decisionType = new Map<string, string>();
  private readonly decisionTime = new Map<string, string>();
  private readonly decisionReviewed = new Set<string>();
  private readonly outcomeDecision = new Map<string, string>(); // Outcome IRI -> its refs.decision IRI, if any
  private readonly decisionNotice = new Map<string, Set<string>>(); // Decision IRI -> every notifying Notice IRI (order-independent)
  private readonly danglingNodes = new Set<string>(); // event IRI -> has >=1 agentce:danglingRef literal
  private readonly instructionUntrusted = new Map<string, boolean>(); // Instruction IRI -> untrusted flag

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
