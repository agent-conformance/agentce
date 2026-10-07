/**
 * Parse a Portable Shape Profile shape into a small AST (SPEC §7.2, ADR-0002).
 *
 * The PSP is a strict, bounded subset of SHACL Core, so the AST is finite: a node shape has targets
 * (`sh:targetClass`, `sh:targetNode`, and the engine-resolved `agentce:targetWhere` property-value
 * conjunction) and property shapes, each with a path (a predicate, an inverse, or a bounded sequence
 * or alternative) and the profile's constraints. The structural evaluator compiles this AST to queries
 * over the graph store. This is a faithful port of the Python reference; it parses Turtle with N3.js
 * where the reference uses rdflib, and compacts terms to the store's CURIE representation identically.
 * `spec/rules/psp_check.py` is the authoring-time checker for the profile; this module reads the same
 * term list (`psp-terms.json`) and refuses every shape that checker refuses, and every shape file that
 * will not parse, under a stable message key before a shape ever reaches the evaluator (18.34, 18.78).
 */

import { readFileSync } from "node:fs";
import { DataFactory, Parser, Store, type Term } from "n3";
import { pspTermsPath } from "./bundled";
import { InputError } from "./errors";
import { errorCause, errorFix } from "./messages";
import { byteCompare } from "./util";

const { namedNode } = DataFactory;

const SH = "http://www.w3.org/ns/shacl#";
const AGENTCE = "https://agent-conformance.org/vocab/evidence/v1#";
const PROV = "http://www.w3.org/ns/prov#";
const RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#";
const XSD = "http://www.w3.org/2001/XMLSchema#";
const RDFS = "http://www.w3.org/2000/01/rdf-schema#";

const RDF_TYPE = namedNode(`${RDF_NS}type`);
const RDF_FIRST = namedNode(`${RDF_NS}first`);
const RDF_REST = namedNode(`${RDF_NS}rest`);
const RDF_NIL = `${RDF_NS}nil`;

const PREFIXES: Array<[string, string]> = [
  ["agentce:", AGENTCE],
  ["prov:", PROV],
  ["sh:", SH],
  ["rdfs:", RDFS],
  ["xsd:", XSD],
];

/** Compact an RDF term to the store's representation (CURIE for known IRIs; lexical literal). */
export function curie(term: Term): string {
  if (term.termType === "Literal") {
    return term.value;
  }
  const text = term.value;
  if (text === `${RDF_NS}type`) {
    return "rdf:type";
  }
  for (const [prefix, namespace] of PREFIXES) {
    if (text.startsWith(namespace)) {
      return prefix + text.slice(namespace.length);
    }
  }
  return text;
}

// --- path AST ---

export interface Predicate {
  kind: "predicate";
  iri: string;
}
export interface Inverse {
  kind: "inverse";
  path: PathExpr;
}
export interface Sequence {
  kind: "sequence";
  steps: PathExpr[];
}
export interface Alternative {
  kind: "alternative";
  options: PathExpr[];
}
export type PathExpr = Predicate | Inverse | Sequence | Alternative;

export interface PropertyShape {
  path: PathExpr;
  name: string | null;
  messageKey: string | null;
  minCount: number | null;
  maxCount: number | null;
  cls: string | null;
  datatype: string | null;
  nodeKind: string | null;
  inValues: string[] | null;
  hasValue: string | null;
  node: string | null;
  qualifiedValueShape: string | null;
  qualifiedMinCount: number | null;
  equals: string | null;
  disjoint: string | null;
  lessThan: string | null;
  lessThanOrEquals: string | null;
  minInclusive: string | null;
  maxInclusive: string | null;
}

export interface Shape {
  iri: string;
  targetClass: string | null;
  targetNodes: string[];
  targetWhere: Array<[string, string]>;
  properties: PropertyShape[];
}

function sh(name: string): Term {
  return namedNode(SH + name);
}

function value(store: Store, subject: Term, predicate: Term): Term | null {
  const objects = store.getObjects(subject, predicate, null);
  return objects.length > 0 ? (objects[0] as Term) : null;
}

/** Read an RDF list (`rdf:first`/`rdf:rest`) as an array of member terms. */
function collection(store: Store, head: Term): Term[] {
  const items: Term[] = [];
  let current: Term | null = head;
  while (current !== null && current.value !== RDF_NIL) {
    const first = value(store, current, RDF_FIRST);
    if (first === null) {
      break;
    }
    items.push(first);
    current = value(store, current, RDF_REST);
  }
  return items;
}

function intValue(term: Term | null): number | null {
  return term !== null && term.termType === "Literal" ? Number.parseInt(term.value, 10) : null;
}

function parsePath(store: Store, node: Term): PathExpr {
  if (node.termType === "NamedNode") {
    return { kind: "predicate", iri: curie(node) };
  }
  const inverse = value(store, node, sh("inversePath"));
  if (inverse !== null) {
    return { kind: "inverse", path: parsePath(store, inverse) };
  }
  const alternative = value(store, node, sh("alternativePath"));
  if (alternative !== null) {
    return {
      kind: "alternative",
      options: collection(store, alternative).map((item) => parsePath(store, item)),
    };
  }
  if (node.termType === "BlankNode") {
    // an RDF list is a sequence path
    return {
      kind: "sequence",
      steps: collection(store, node).map((item) => parsePath(store, item)),
    };
  }
  return { kind: "predicate", iri: curie(node) };
}

function emptyProperty(path: PathExpr): PropertyShape {
  return {
    path,
    name: null,
    messageKey: null,
    minCount: null,
    maxCount: null,
    cls: null,
    datatype: null,
    nodeKind: null,
    inValues: null,
    hasValue: null,
    node: null,
    qualifiedValueShape: null,
    qualifiedMinCount: null,
    equals: null,
    disjoint: null,
    lessThan: null,
    lessThanOrEquals: null,
    minInclusive: null,
    maxInclusive: null,
  };
}

function parseProperty(store: Store, node: Term): PropertyShape {
  const prop = emptyProperty(parsePath(store, value(store, node, sh("path")) as Term));
  const name = value(store, node, sh("name"));
  prop.name = name !== null ? name.value : null;
  const messageKey = value(store, node, namedNode(`${AGENTCE}messageKey`));
  prop.messageKey = messageKey !== null ? messageKey.value : null;
  prop.minCount = intValue(value(store, node, sh("minCount")));
  prop.maxCount = intValue(value(store, node, sh("maxCount")));
  const curieAttrs: Array<[keyof PropertyShape, string]> = [
    ["cls", "class"],
    ["datatype", "datatype"],
    ["nodeKind", "nodeKind"],
    ["node", "node"],
    ["equals", "equals"],
    ["disjoint", "disjoint"],
    ["lessThan", "lessThan"],
    ["lessThanOrEquals", "lessThanOrEquals"],
  ];
  for (const [attr, term] of curieAttrs) {
    const found = value(store, node, sh(term));
    if (found !== null) {
      (prop[attr] as string) = curie(found);
    }
  }
  for (const [attr, term] of [
    ["minInclusive", "minInclusive"],
    ["maxInclusive", "maxInclusive"],
  ] as Array<[keyof PropertyShape, string]>) {
    const found = value(store, node, sh(term));
    if (found !== null) {
      (prop[attr] as string) = found.value;
    }
  }
  const hasValue = value(store, node, sh("hasValue"));
  if (hasValue !== null) {
    prop.hasValue = curie(hasValue);
  }
  const inList = value(store, node, sh("in"));
  if (inList !== null) {
    prop.inValues = collection(store, inList).map(curie);
  }
  const qualified = value(store, node, sh("qualifiedValueShape"));
  if (qualified !== null) {
    prop.qualifiedValueShape = qualified.value;
    prop.qualifiedMinCount = intValue(value(store, node, sh("qualifiedMinCount")));
  }
  return prop;
}

/** Parse every `sh:NodeShape` in the store into the PSP AST, keyed by shape IRI. */
export function parseShapes(store: Store): Map<string, Shape> {
  const shapes = new Map<string, Shape>();
  for (const shapeNode of store.getSubjects(RDF_TYPE, sh("NodeShape"), null)) {
    const shape: Shape = {
      iri: shapeNode.value,
      targetClass: null,
      targetNodes: [],
      targetWhere: [],
      properties: [],
    };
    const targetClass = value(store, shapeNode, sh("targetClass"));
    if (targetClass !== null) {
      shape.targetClass = curie(targetClass);
    }
    shape.targetNodes = store.getObjects(shapeNode, sh("targetNode"), null).map(curie);
    for (const where of store.getObjects(shapeNode, namedNode(`${AGENTCE}targetWhere`), null)) {
      for (const quad of store.getQuads(where, null, null, null)) {
        if (quad.predicate.value !== `${RDF_NS}type`) {
          shape.targetWhere.push([curie(quad.predicate), curie(quad.object)]);
        }
      }
    }
    for (const propNode of store.getObjects(shapeNode, sh("property"), null)) {
      shape.properties.push(parseProperty(store, propNode));
    }
    shapes.set(shapeNode.value, shape);
  }
  return shapes;
}

/** The Portable Shape Profile's term lists (spec/rules/psp-terms.json, vendored byte-identical and held
 * in sync by bundledData.test.ts): the same file spec/rules/psp_check.py reads, so the engine refuses
 * exactly the shapes the authoring-time checker refuses (18.78). Left unchecked, an excluded predicate
 * parses as ordinary Turtle and the reads above, which only ask for the predicates they name, drop it
 * silently: the shape would be evaluated as if the construct were not there. */
interface PspTerms {
  allowed: string[];
  priority_deny: string[];
  max_path_length: number;
  range_datatypes: string[];
  regex_meta: string;
}
const TERMS = JSON.parse(readFileSync(pspTermsPath(), "utf-8")) as PspTerms;
const ALLOWED = new Set(TERMS.allowed);
const REGEX_META = new Set(TERMS.regex_meta);
const RANGE_DATATYPES = new Set(TERMS.range_datatypes);

/** The two features 18.34 gave their own keys; every other excluded feature is outside_profile. */
const FEATURE_KEYS: Record<string, string> = {
  "sh:sparql": "catalog.shape.sparql_forbidden",
  "sh:js": "catalog.shape.script_forbidden",
  "sh:javascript": "catalog.shape.script_forbidden",
};
const OUTSIDE_PROFILE = "catalog.shape.outside_profile";
const PARSE_ERROR = "catalog.shape.parse_error";

function hasQuad(store: Store, subject: Term, predicate: Term): boolean {
  return store.getObjects(subject, predicate, null).length > 0;
}

/** The excluded feature a property path uses, or null: a predicate, an inverse of a permitted path,
 * or a sequence or alternative of at most `max_path_length` permitted paths. */
function pathFeature(
  store: Store,
  node: Term,
  seen: ReadonlySet<string> = new Set(),
): string | null {
  if (node.termType === "NamedNode") {
    return null;
  }
  if (seen.has(node.value)) {
    return "path (cyclic)";
  }
  const inner = new Set(seen).add(node.value);
  for (const banned of ["zeroOrMorePath", "oneOrMorePath", "zeroOrOnePath"]) {
    if (hasQuad(store, node, sh(banned))) {
      return `sh:${banned}`;
    }
  }
  const inverse = value(store, node, sh("inversePath"));
  if (inverse !== null) {
    return pathFeature(store, inverse, inner);
  }
  const alternative = value(store, node, sh("alternativePath"));
  if (alternative !== null) {
    if (!hasQuad(store, alternative, RDF_FIRST)) {
      return "sh:alternativePath (not a list)";
    }
    return membersFeature(store, collection(store, alternative), "alternative", inner);
  }
  if (hasQuad(store, node, RDF_FIRST)) {
    return membersFeature(store, collection(store, node), "sequence", inner);
  }
  return "path (unsupported blank node)";
}

function membersFeature(
  store: Store,
  members: Term[],
  kind: string,
  seen: ReadonlySet<string>,
): string | null {
  if (members.length > TERMS.max_path_length) {
    return `path ${kind} of ${members.length} (max ${TERMS.max_path_length})`;
  }
  for (const member of members) {
    const feature = pathFeature(store, member, seen);
    if (feature !== null) {
      return feature;
    }
  }
  return null;
}

/** The path, pattern, range and targetWhere stages, in that order, each as the features it finds. */
function structuralFeatures(store: Store): string[][] {
  const paths = store
    .getObjects(null, sh("path"), null)
    .map((node) => pathFeature(store, node))
    .filter((f): f is string => f !== null);
  const patterns = store
    .getObjects(null, sh("pattern"), null)
    .filter((v) => !v.value.startsWith("^") || [...v.value.slice(1)].some((c) => REGEX_META.has(c)))
    .map(() => "sh:pattern (non-literal regex)");
  const ranges = ["minInclusive", "maxInclusive"].flatMap((name) =>
    store
      .getObjects(null, sh(name), null)
      .filter((v) => v.termType !== "Literal" || !RANGE_DATATYPES.has(v.datatype.value))
      .map(() => `${name} on a non-integer/dateTime bound`),
  );
  const targetWhere = store
    .getObjects(null, namedNode(`${AGENTCE}targetWhere`), null)
    .flatMap((where) => store.getObjects(where, null, null))
    .filter((v) => v.termType === "BlankNode")
    .map(() => "agentce:targetWhere (nested node, not a value equality)");
  return [paths, patterns, ranges, targetWhere];
}

/** The first feature the store uses that the Portable Shape Profile excludes, or null. The order is
 * fixed so the answer never depends on triple order, and matches Python's `profile_feature`: the
 * priority terms in list order, matched case-insensitively and named canonically (`sh:CLOSED` is
 * `sh:closed`); then any other SHACL-namespace predicate the profile does not allow, as written,
 * least by code point; then the path, pattern, range and targetWhere stages, each naming the
 * code-point-least feature it finds (spec/rules/psp.md). */
export function profileFeature(store: Store): string | null {
  const used = new Set<string>();
  for (const predicate of store.getPredicates(null, null, null)) {
    if (predicate.value.startsWith(SH)) {
      used.add(predicate.value.slice(SH.length));
    }
  }
  const sorted = [...used].sort(byteCompare);
  const folded = new Set(sorted.map((name) => name.toLowerCase()));
  for (const term of TERMS.priority_deny) {
    if (folded.has(term.toLowerCase())) {
      return `sh:${term}`;
    }
  }
  for (const name of sorted) {
    if (!ALLOWED.has(name)) {
      return `sh:${name}`;
    }
  }
  for (const found of structuralFeatures(store)) {
    if (found.length > 0) {
      return found.sort(byteCompare)[0] as string;
    }
  }
  return null;
}

function fill(template: string, vars: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => vars[name] ?? whole);
}

/** Parse PSP shapes from Turtle, refusing a file that will not parse or a shape outside the profile.
 * `shown` names the shape file in the message (a catalog-relative path). */
export function parseShapesTtl(text: string, shown = "(inline)"): Map<string, Shape> {
  const store = new Store();
  try {
    store.addQuads(new Parser().parse(text));
  } catch (err) {
    const detail = String(err instanceof Error ? err.message : err)
      .split(/\s+/)
      .filter(Boolean)
      .join(" ");
    throw new InputError(
      PARSE_ERROR,
      fill(errorCause(PARSE_ERROR), { path: shown, detail: detail.slice(0, 200) }),
      errorFix(PARSE_ERROR),
    );
  }
  const feature = profileFeature(store);
  if (feature !== null) {
    const key = FEATURE_KEYS[feature] ?? OUTSIDE_PROFILE;
    throw new InputError(key, fill(errorCause(key), { path: shown, feature }), errorFix(key));
  }
  return parseShapes(store);
}

export function loadShapes(path: string, shown: string): Map<string, Shape> {
  return parseShapesTtl(readFileSync(path, "utf-8"), shown);
}
