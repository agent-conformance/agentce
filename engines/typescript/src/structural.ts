/**
 * The structural evaluator: compile a PSP shape to queries over the graph store (SPEC §7.2, §9.2).
 *
 * Target nodes are the applicable population; each is checked against the shape's property shapes, and
 * a node with any failing constraint is a failure. Outcomes are computed at the population level
 * against the control's declared `tolerance` (§9.2): `conformant` when failures are within tolerance,
 * `non-conformant` otherwise. Class membership uses the materialised `rdfs:subClassOf*` closure and no
 * other inference. Each violation is `{focus, path, constraint, message_key}`. This is a faithful port
 * of the Python reference; ratio tolerances are compared exactly with BigInt cross-multiplication.
 */

import type { PathExpr, PropertyShape, Shape } from "./psp";
import type { GraphStore } from "./store";
import { byteCompare } from "./util";

interface Value {
  repr: string;
  isIri: boolean;
  datatype: string | null;
}

export interface Violation {
  focus: string;
  path: string;
  constraint: string;
  messageKey: string;
}

export function violationToJson(violation: Violation): Record<string, string> {
  return {
    focus: violation.focus,
    path: violation.path,
    constraint: violation.constraint,
    message_key: violation.messageKey,
  };
}

export function renderPath(path: PathExpr): string {
  if (path.kind === "predicate") {
    return path.iri;
  }
  if (path.kind === "inverse") {
    return `^${renderPath(path.path)}`;
  }
  if (path.kind === "sequence") {
    return `(${path.steps.map(renderPath).join(" ")})`;
  }
  return `(${path.options.map(renderPath).join(" | ")})`;
}

function predicateValues(store: GraphStore, focus: string, predicate: string): Value[] {
  const values: Value[] = store
    .objects(focus, predicate)
    .map((obj) => ({ repr: obj, isIri: true, datatype: null }));
  for (const [val, datatype] of store.literalPairs(focus, predicate)) {
    values.push({ repr: val, isIri: false, datatype });
  }
  return values;
}

export function resolvePath(store: GraphStore, focus: string, path: PathExpr): Value[] {
  if (path.kind === "predicate") {
    return predicateValues(store, focus, path.iri);
  }
  if (path.kind === "inverse") {
    if (path.path.kind === "predicate") {
      return store
        .subjects(path.path.iri, focus)
        .map((s) => ({ repr: s, isIri: true, datatype: null }));
    }
    return []; // deeper inverses are outside the profile
  }
  if (path.kind === "alternative") {
    const out: Value[] = [];
    for (const option of path.options) {
      out.push(...resolvePath(store, focus, option));
    }
    return out;
  }
  // Sequence: traverse IRIs step by step; the last step yields the values.
  let current = [focus];
  for (let index = 0; index < path.steps.length; index += 1) {
    const collected: Value[] = [];
    for (const node of current) {
      collected.push(...resolvePath(store, node, path.steps[index] as PathExpr));
    }
    if (index === path.steps.length - 1) {
      return collected;
    }
    current = collected.filter((v) => v.isIri).map((v) => v.repr);
  }
  return [];
}

function asNumber(value: string): number | null {
  const s = value.trim();
  if (/^[+-]?\d+\/\d+$/.test(s)) {
    const [num, den] = s.split("/").map(Number);
    return den !== 0 ? (num as number) / (den as number) : null;
  }
  if (/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(s)) {
    return Number(s);
  }
  return null;
}

function asDatetime(value: string): number | null {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function le(a: string, b: string): boolean | null {
  const na = asNumber(a);
  const nb = asNumber(b);
  if (na !== null && nb !== null) {
    return na <= nb;
  }
  const da = asDatetime(a);
  const db = asDatetime(b);
  if (da !== null && db !== null) {
    return da <= db;
  }
  return null;
}

function setsEqual(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) {
    return false;
  }
  for (const item of a) {
    if (!b.has(item)) {
      return false;
    }
  }
  return true;
}

function intersects(a: Set<string>, b: Set<string>): boolean {
  for (const item of a) {
    if (b.has(item)) {
      return true;
    }
  }
  return false;
}

function shapeByCurie(shapes: Map<string, Shape>, ref: string): Shape | undefined {
  const colon = ref.indexOf(":");
  const local = colon >= 0 ? ref.slice(colon + 1) : ref;
  for (const shape of shapes.values()) {
    if (shape.iri.endsWith(local)) {
      return shape;
    }
  }
  return undefined;
}

function nodeViolations(
  store: GraphStore,
  focus: string,
  shape: Shape,
  shapes: Map<string, Shape>,
  controlId: string,
): Violation[] {
  const out: Violation[] = [];
  for (const prop of shape.properties) {
    out.push(...checkProperty(store, focus, prop, shapes, controlId));
  }
  return out;
}

function checkProperty(
  store: GraphStore,
  focus: string,
  prop: PropertyShape,
  shapes: Map<string, Shape>,
  controlId: string,
): Violation[] {
  const values = resolvePath(store, focus, prop.path);
  const pathText = renderPath(prop.path);
  const failed: string[] = []; // constraint ids that failed

  if (prop.minCount !== null && values.length < prop.minCount) {
    failed.push("sh:minCount");
  }
  if (prop.maxCount !== null && values.length > prop.maxCount) {
    failed.push("sh:maxCount");
  }
  if (prop.hasValue !== null && !values.some((v) => v.repr === prop.hasValue)) {
    failed.push("sh:hasValue");
  }
  if (prop.cls !== null && !values.every((v) => v.isIri && store.isA(v.repr, prop.cls as string))) {
    failed.push("sh:class");
  }
  if (prop.datatype !== null && !values.every((v) => !v.isIri && v.datatype === prop.datatype)) {
    failed.push("sh:datatype");
  }
  if (prop.nodeKind !== null) {
    const wantIri = prop.nodeKind === "sh:IRI";
    if (!values.every((v) => v.isIri === wantIri)) {
      failed.push("sh:nodeKind");
    }
  }
  if (prop.inValues !== null) {
    const allowed = new Set(prop.inValues);
    if (!values.every((v) => allowed.has(v.repr))) {
      failed.push("sh:in");
    }
  }
  if (
    prop.minInclusive !== null &&
    !values.every((v) => le(prop.minInclusive as string, v.repr) === true)
  ) {
    failed.push("sh:minInclusive");
  }
  if (
    prop.maxInclusive !== null &&
    !values.every((v) => le(v.repr, prop.maxInclusive as string) === true)
  ) {
    failed.push("sh:maxInclusive");
  }
  if (prop.equals !== null) {
    const others = new Set(predicateValues(store, focus, prop.equals).map((v) => v.repr));
    if (!setsEqual(new Set(values.map((v) => v.repr)), others)) {
      failed.push("sh:equals");
    }
  }
  if (prop.disjoint !== null) {
    const others = new Set(predicateValues(store, focus, prop.disjoint).map((v) => v.repr));
    if (intersects(new Set(values.map((v) => v.repr)), others)) {
      failed.push("sh:disjoint");
    }
  }
  const relational: Array<[string | null, string, (a: string, b: string) => boolean]> = [
    [prop.lessThan, "sh:lessThan", (a, b) => le(a, b) === true && a !== b],
    [prop.lessThanOrEquals, "sh:lessThanOrEquals", (a, b) => le(a, b) === true],
  ];
  for (const [otherPath, constraint, ok] of relational) {
    if (otherPath !== null) {
      const otherValues = predicateValues(store, focus, otherPath);
      if (!values.every((v) => otherValues.every((o) => ok(v.repr, o.repr)))) {
        failed.push(constraint);
      }
    }
  }
  if (prop.node !== null) {
    const nested = shapes.get(prop.node) ?? shapeByCurie(shapes, prop.node);
    if (
      nested !== undefined &&
      !values.every(
        (v) => v.isIri && nodeViolations(store, v.repr, nested, shapes, controlId).length === 0,
      )
    ) {
      failed.push("sh:node");
    }
  }
  if (prop.qualifiedValueShape !== null && prop.qualifiedMinCount !== null) {
    const nested =
      shapes.get(prop.qualifiedValueShape) ?? shapeByCurie(shapes, prop.qualifiedValueShape);
    let conforming = 0;
    for (const v of values) {
      if (
        v.isIri &&
        nested !== undefined &&
        nodeViolations(store, v.repr, nested, shapes, controlId).length === 0
      ) {
        conforming += 1;
      }
    }
    if (conforming < prop.qualifiedMinCount) {
      failed.push("sh:qualifiedMinCount");
    }
  }

  const key =
    prop.messageKey ?? (prop.name ? `${controlId}.${prop.name}` : `${controlId}.${pathText}`);
  return failed.map((constraint) => ({ focus, path: pathText, constraint, messageKey: key }));
}

function hasValue(store: GraphStore, node: string, predicate: string, value: string): boolean {
  return (
    store.objects(node, predicate).includes(value) ||
    store.literalValues(node, predicate).includes(value)
  );
}

function targetNodes(store: GraphStore, shape: Shape): string[] {
  const focus = new Set(shape.targetNodes);
  if (shape.targetClass !== null) {
    for (const instance of store.instancesOf(shape.targetClass)) {
      focus.add(instance);
    }
  }
  const sorted = [...focus].sort(byteCompare);
  if (shape.targetWhere.length === 0) {
    return sorted;
  }
  return sorted.filter((node) =>
    shape.targetWhere.every(([pred, val]) => hasValue(store, node, pred, val)),
  );
}

export interface ControlOutcome {
  control: string;
  outcome: string;
  applicable: number;
  failed: number;
  violations: Violation[];
}

export function controlOutcomeToJson(outcome: ControlOutcome): Record<string, unknown> {
  return {
    control: outcome.control,
    outcome: outcome.outcome,
    population: { applicable: outcome.applicable, failed: outcome.failed },
    violations: outcome.violations.map(violationToJson),
  };
}

/** Return [applicable focus nodes, failing focus nodes, violations] for `shape`. */
export function evaluateShape(
  store: GraphStore,
  shape: Shape,
  shapes: Map<string, Shape>,
  controlId: string,
): [string[], Set<string>, Violation[]] {
  const applicable = targetNodes(store, shape);
  const failing = new Set<string>();
  const violations: Violation[] = [];
  for (const focus of applicable) {
    const nodeVs: Violation[] = [];
    for (const prop of shape.properties) {
      nodeVs.push(...checkProperty(store, focus, prop, shapes, controlId));
    }
    if (nodeVs.length > 0) {
      failing.add(focus);
      violations.push(...nodeVs);
    }
  }
  return [applicable, failing, violations];
}

function parseRatio(max: string): [bigint, bigint] {
  if (max.includes("/")) {
    const [num, den] = max.split("/");
    return [BigInt(num as string), BigInt(den as string)];
  }
  const dot = max.indexOf(".");
  if (dot >= 0) {
    const digits = max.slice(0, dot) + max.slice(dot + 1);
    const fractional = max.length - dot - 1;
    return [BigInt(digits), 10n ** BigInt(fractional)];
  }
  return [BigInt(max), 1n];
}

/** Whether `failed` failures out of `applicable` are within the control's tolerance (§9.2). */
export function withinTolerance(
  applicable: number,
  failed: number,
  tolerance: Record<string, unknown>,
): boolean {
  const kind = (tolerance.kind as string) ?? "count";
  if (kind === "ratio" && applicable > 0) {
    const [num, den] = parseRatio(String(tolerance.max ?? "0"));
    // failed/applicable <= num/den  <=>  failed*den <= num*applicable  (all non-negative)
    return BigInt(failed) * den <= num * BigInt(applicable);
  }
  return failed <= Number.parseInt(String(tolerance.max ?? 0), 10);
}

/** Evaluate one control's shape and apply its tolerance to reach a structural outcome. */
export function evaluateControl(
  store: GraphStore,
  shape: Shape,
  shapes: Map<string, Shape>,
  controlId: string,
  tolerance: Record<string, unknown> = { kind: "count", max: 0 },
): ControlOutcome {
  const [applicable, failing, violations] = evaluateShape(store, shape, shapes, controlId);
  const conformant = withinTolerance(applicable.length, failing.size, tolerance);
  const sortedViolations = [...violations].sort(
    (a, b) => byteCompare(a.focus, b.focus) || byteCompare(a.constraint, b.constraint),
  );
  return {
    control: controlId,
    outcome: conformant ? "conformant" : "non-conformant",
    applicable: applicable.length,
    failed: failing.size,
    violations: sortedViolations,
  };
}
