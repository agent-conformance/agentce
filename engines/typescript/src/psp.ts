/**
 * Parse a Portable Shape Profile shape into a small AST (SPEC §7.2, ADR-0002).
 *
 * The PSP is a strict, bounded subset of SHACL Core, so the AST is finite: a node shape has targets
 * (`sh:targetClass`, `sh:targetNode`, and the engine-resolved `agentce:targetWhere` property-value
 * conjunction) and property shapes, each with a path (a predicate, an inverse, or a bounded sequence
 * or alternative) and the profile's constraints. The structural evaluator compiles this AST to queries
 * over the graph store. This is a faithful port of the Python reference; it parses Turtle with N3.js
 * where the reference uses rdflib, and compacts terms to the store's CURIE representation identically.
 */

import { readFileSync } from "node:fs";
import { DataFactory, Parser, Store, type Term } from "n3";

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

export function parseShapesTtl(text: string): Map<string, Shape> {
  const store = new Store();
  store.addQuads(new Parser().parse(text));
  return parseShapes(store);
}

export function loadShapes(path: string): Map<string, Shape> {
  return parseShapesTtl(readFileSync(path, "utf-8"));
}
