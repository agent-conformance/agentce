/**
 * The class a control's judgment rests on, against the control's minimum (SPEC §6.4, HR-5, §7.7.5).
 *
 * Every control declares `evaluation.min_source_class`. An assertion meets it only when every applicable
 * focus does: {@link SourceClassJudge.judgedClass} is the lowest class among the focus's own record (left
 * out for a Decision, which is the agent's own account by nature, §7.7.5) and the records one hop away
 * through each predicate the control names in `evaluation.source_class_from`. A record's class is its own
 * `agentcesourceclass`, except for an Instruction, which counts as `enforcement_point` only when attested
 * ({@link SourceClassJudge.attested}) and as `self_report` otherwise: an agent's own record that it was
 * told something by the user is never taken as given. A port of Python's `agentce.source_class`.
 */

import type { GraphStore } from "./store";
import { byteCompare, sortKeysDeep } from "./util";

type Event = Record<string, unknown>;

/** The source classes in their SPEC §6.4 order; the rank is the order. `any` is not a class. */
export const SOURCE_CLASS_RANK: ReadonlyMap<string, number> = new Map([
  ["self_report", 0],
  ["enforcement_point", 1],
  ["independent_system", 2],
]);
const ENFORCEMENT_POINT = "enforcement_point";

/** The graph links `evaluation.source_class_from` may name, each one hop from the focus (SPEC §6.3), and
 * the record type each one names. */
export const SOURCE_CLASS_FROM: ReadonlyMap<string, string> = new Map([
  ["actsOn", "Instruction"],
  ["delegatedVia", "DelegationIssued"],
  ["approvedBy", "ApprovalDecided"],
]);

/** The outcome an assertion takes when a focus's judged class is below the control's minimum (HR-5). One
 * constant, so a later reading of single-source evidence (SPEC §10.4 `partial`) changes it in one place. */
export const BELOW_MINIMUM_OUTCOME = "insufficient_evidence";

/** The Appendix F instruction source classes an agent may act on (the graph's trusted set, SPEC §7.7). */
const TRUSTED_INSTRUCTION = new Set([
  "user",
  "operator",
  "service",
  "agent_identified",
  "memory_trusted",
]);

/** The rank of a record's class; a record with no class, or one outside SPEC §6.4, ranks as self_report. */
export function classRank(value: unknown): number {
  return typeof value === "string" ? (SOURCE_CLASS_RANK.get(value) ?? 0) : 0;
}

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

function typeOf(event: Event): string {
  const ptype = dataOf(event)["@type"];
  return typeof ptype === "string" ? ptype : "";
}

function principalId(value: unknown): string | null {
  return isRecord(value) && typeof value.id === "string" && value.id !== "" ? value.id : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** Python `==` over decoded JSON values: an absent member and null are both None. */
function sameValue(a: unknown, b: unknown): boolean {
  const left = a === undefined ? null : a;
  const right = b === undefined ? null : b;
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") {
    return left === right;
  }
  return JSON.stringify(sortKeysDeep(left)) === JSON.stringify(sortKeysDeep(right));
}

/** Which witnesses may stand in for an instruction's own class at (iv): its own class only (a twin), its
 * own class or a twin (a parent: the PolicyDecision allows the action, which acts on the child only), or
 * all. */
const OWN = 0;
const TWIN = 1;
const ALL = 2;

/** A focus's judged class: its rank and the record types that set it (the lowest contributors). */
export interface Judged {
  rank: number;
  lowest: string[];
}

/** Judges foci against one subject's held records (`events`: graph IRI -> event) and graph. */
export class SourceClassJudge {
  private readonly instructions: string[];
  private readonly policyDecisions: string[];

  constructor(
    private readonly store: GraphStore,
    private readonly events: Map<string, Event>,
  ) {
    this.instructions = [...events].filter(([, e]) => typeOf(e) === "Instruction").map(([i]) => i);
    this.policyDecisions = [...events]
      .filter(([, e]) => typeOf(e) === "PolicyDecision")
      .map(([i]) => i);
  }

  private ownRank(node: string): number {
    const event = this.events.get(node);
    return event !== undefined ? classRank(event.agentcesourceclass) : 0;
  }

  /** (i): the Instruction a ref names by its graph IRI among this subject's held records. */
  private heldInstruction(node: unknown): Event | null {
    const event = typeof node === "string" ? this.events.get(node) : undefined;
    return event !== undefined && typeOf(event) === "Instruction" ? event : null;
  }

  /** Whether `instruction` is attested for `focus` (ruling 5: an enforcement-point ingress record): (i)
   * held under the subject by graph IRI; (ii) every refs.parent held and itself attested, by its own class
   * or a twin; (iii) its refs.origin, if any, an enforcement-point MemoryRead at trust_min trusted; (iv) its
   * own class enforcement_point, or witnessed by an attested enforcement-point twin or by an
   * enforcement-point PolicyDecision allowing the focus for the instruction's principal (corroboration
   * replaces only (iv)). */
  attested(focus: string, instruction: string): boolean {
    return this.attestedAt(focus, instruction, new Set(), ALL);
  }

  private attestedAt(
    focus: string,
    node: string,
    visiting: ReadonlySet<string>,
    witnesses: number,
  ): boolean {
    const event = this.heldInstruction(node);
    if (event === null) {
      return false; // (i) held under the subject by graph IRI
    }
    // A parent cycle: the node is already being judged further up the chain.
    if (visiting.has(node)) {
      return true;
    }
    const here = new Set(visiting).add(node);
    const refs = refsOf(event);
    if (
      "parent" in refs &&
      !(
        typeof refs.parent === "string" &&
        this.attestedAt(focus, refs.parent, here, Math.min(witnesses, TWIN))
      )
    ) {
      return false; // (ii) every parent held and itself attested
    }
    if ("origin" in refs && !this.trustedOrigin(refs.origin)) {
      return false; // (iii)
    }
    // (iv) by its own class, exactly enforcement_point (ruling 5); then the witnesses this hop allows.
    if (event.agentcesourceclass === ENFORCEMENT_POINT) {
      return true;
    }
    if (witnesses === OWN) {
      return false;
    }
    if (this.twinWitnesses(node, event, here)) {
      return true;
    }
    return witnesses === ALL && this.decisionWitnesses(focus, event);
  }

  /** (iii): an origin is an enforcement-point MemoryRead held under the subject whose trust_min is
   * trusted. */
  private trustedOrigin(origin: unknown): boolean {
    const event = typeof origin === "string" ? this.events.get(origin) : undefined;
    return (
      event !== undefined &&
      typeOf(event) === "MemoryRead" &&
      event.agentcesourceclass === ENFORCEMENT_POINT &&
      dataOf(event).trust_min === "trusted"
    );
  }

  /** (iv) by an enforcement-point twin: the same content_ref, refs.task, source_class and principal, held
   * under the subject, meeting (i)-(iv) by its own class, every instruction above it declaring a trusted
   * class. */
  private twinWitnesses(node: string, event: Event, visiting: ReadonlySet<string>): boolean {
    const data = dataOf(event);
    const content = text(data.content_ref);
    const task = text(refsOf(event).task);
    if (content === null || task === null) {
      return false;
    }
    for (const twin of this.instructions) {
      if (twin === node || visiting.has(twin)) {
        continue;
      }
      const other = this.events.get(twin) as Event;
      const otherData = dataOf(other);
      if (
        other.agentcesourceclass === ENFORCEMENT_POINT &&
        text(otherData.content_ref) === content &&
        text(refsOf(other).task) === task &&
        sameValue(otherData.source_class, data.source_class) &&
        principalId(otherData.principal) === principalId(data.principal) &&
        this.attestedAt(twin, twin, visiting, OWN) &&
        this.lineageTrusted(twin)
      ) {
        return true;
      }
    }
    return false;
  }

  /** Every instruction above `node` through refs.parent declares a trusted Appendix F class. */
  private lineageTrusted(node: string): boolean {
    const seen = new Set<string>();
    let parent = refsOf(this.events.get(node) as Event).parent;
    while (typeof parent === "string" && !seen.has(parent)) {
      seen.add(parent);
      const event = this.heldInstruction(parent);
      const sourceClass = event === null ? undefined : dataOf(event).source_class;
      if (
        event === null ||
        typeof sourceClass !== "string" ||
        !TRUSTED_INSTRUCTION.has(sourceClass)
      ) {
        return false;
      }
      parent = refsOf(event).parent;
    }
    return true;
  }

  /** (iv) by an enforcement-point PolicyDecision allow whose refs.request is the focus and whose principal
   * is the instruction's (both present). */
  private decisionWitnesses(focus: string, event: Event): boolean {
    const principal = principalId(dataOf(event).principal);
    if (principal === null) {
      return false;
    }
    return this.policyDecisions.some((decision) => {
      const record = this.events.get(decision) as Event;
      const data = dataOf(record);
      return (
        record.agentcesourceclass === ENFORCEMENT_POINT &&
        data.decision === "allow" &&
        refsOf(record).request === focus &&
        principalId(data.principal) === principal
      );
    });
  }

  /** A record one hop from the focus: an Instruction ranks enforcement_point only when attested; anything
   * else ranks as its own class, and a ref naming no held record as self_report (`kind` names it). */
  private attestedRank(focus: string, node: string, kind: string): [number, string] {
    const event = this.events.get(node);
    if (event === undefined) {
      return [0, kind];
    }
    if (typeOf(event) === "Instruction") {
      const ep = SOURCE_CLASS_RANK.get(ENFORCEMENT_POINT) as number;
      return [this.attested(focus, node) ? ep : 0, "Instruction"];
    }
    return [this.ownRank(node), typeOf(event) || kind];
  }

  /** The lowest class the judgment of `focus` rests on; null when nothing contributes (a Decision with no
   * record through any named link: there is no record the judgment rests on to hold to a class). */
  judgedClass(focus: string, sourceClassFrom: Iterable<string>): Judged | null {
    const contributors: Array<[number, string]> = [];
    const event = this.events.get(focus);
    if (event === undefined || typeOf(event) !== "Decision") {
      contributors.push([this.ownRank(focus), event !== undefined ? typeOf(event) : ""]);
    }
    const names = [...new Set(sourceClassFrom)].filter((n) => SOURCE_CLASS_FROM.has(n));
    for (const name of names.sort(byteCompare)) {
      for (const node of this.store.objects(focus, `agentce:${name}`)) {
        contributors.push(this.attestedRank(focus, node, SOURCE_CLASS_FROM.get(name) as string));
      }
    }
    if (contributors.length === 0) {
      return null;
    }
    const rank = Math.min(...contributors.map(([r]) => r));
    const lowest = new Set(contributors.filter(([r]) => r === rank).map(([, t]) => t));
    return { rank, lowest: [...lowest].sort(byteCompare) };
  }
}

/** The foci whose judged class is below `minimum`, sorted, and the record types that put them there. */
export function belowMinimum(
  judge: SourceClassJudge,
  foci: Iterable<string>,
  minimum: string,
  sourceClassFrom: Iterable<string>,
): [string[], string[]] {
  const floor = SOURCE_CLASS_RANK.get(minimum) as number;
  const named = [...sourceClassFrom];
  const below: string[] = [];
  const kinds = new Set<string>();
  for (const focus of [...foci].sort(byteCompare)) {
    const judged = judge.judgedClass(focus, named);
    if (judged !== null && judged.rank < floor) {
      below.push(focus);
      for (const kind of judged.lowest) {
        kinds.add(kind);
      }
    }
  }
  return [below, [...kinds].sort(byteCompare)];
}
