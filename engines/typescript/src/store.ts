/**
 * The evidence graph store (ADR-0001), as an in-memory triple set.
 *
 * The Python reference holds the graph in SQLite; this engine holds the same triples in memory and
 * answers the same queries in the same order (every read is sorted by UTF-8 bytes, matching SQLite's
 * BINARY collation) so the two engines build byte-identical evidence. Triples are a set: writes are
 * idempotent. IRIs are compact CURIEs (`agentce:`, `prov:`, `rdf:type`).
 */

import { byteCompare } from "./util";

export const RDF_TYPE = "rdf:type";
const SEP = "";

export class GraphStore {
  /** key `s\x1fp` -> set of objects */
  private readonly spo = new Map<string, Set<string>>();
  /** key `p\x1fo` -> set of subjects */
  private readonly pos = new Map<string, Set<string>>();
  /** key `s\x1fp` -> set of `val\x1fdatatype` */
  private readonly lit = new Map<string, Set<string>>();
  /** `descendant\x1fancestor` pairs */
  private readonly closure = new Set<string>();
  /** ancestor -> set of descendants */
  private readonly descendantsByAncestor = new Map<string, Set<string>>();
  private edges = 0;
  private literals = 0;

  private static add(index: Map<string, Set<string>>, key: string, value: string): boolean {
    let bucket = index.get(key);
    if (bucket === undefined) {
      bucket = new Set<string>();
      index.set(key, bucket);
    }
    if (bucket.has(value)) {
      return false;
    }
    bucket.add(value);
    return true;
  }

  addEdge(s: string, p: string, o: string): void {
    const added = GraphStore.add(this.spo, s + SEP + p, o);
    if (added) {
      GraphStore.add(this.pos, p + SEP + o, s);
      this.edges += 1;
    }
  }

  addType(s: string, cls: string): void {
    this.addEdge(s, RDF_TYPE, cls);
  }

  addLiteral(s: string, p: string, val: string, datatype = "xsd:string"): void {
    const added = GraphStore.add(this.lit, s + SEP + p, val + SEP + datatype);
    if (added) {
      this.literals += 1;
    }
  }

  addSubclassClosure(pairs: Iterable<[string, string]>): void {
    for (const [descendant, ancestor] of pairs) {
      this.closure.add(descendant + SEP + ancestor);
      GraphStore.add(this.descendantsByAncestor, ancestor, descendant);
    }
  }

  objects(s: string, p: string): string[] {
    return [...(this.spo.get(s + SEP + p) ?? [])].sort(byteCompare);
  }

  subjects(p: string, o: string): string[] {
    return [...(this.pos.get(p + SEP + o) ?? [])].sort(byteCompare);
  }

  literalValues(s: string, p: string): string[] {
    const bucket = this.lit.get(s + SEP + p);
    if (bucket === undefined) {
      return [];
    }
    return [...bucket].map((entry) => entry.slice(0, entry.indexOf(SEP))).sort(byteCompare);
  }

  literalPairs(s: string, p: string): Array<[string, string]> {
    const bucket = this.lit.get(s + SEP + p);
    if (bucket === undefined) {
      return [];
    }
    return [...bucket]
      .map((entry): [string, string] => {
        const at = entry.indexOf(SEP);
        return [entry.slice(0, at), entry.slice(at + 1)];
      })
      .sort((a, b) => byteCompare(a[0], b[0]) || byteCompare(a[1], b[1]));
  }

  instancesOf(cls: string): string[] {
    const descendants = this.descendantsByAncestor.get(cls);
    if (descendants === undefined) {
      return [];
    }
    const found = new Set<string>();
    for (const descendant of descendants) {
      for (const subject of this.pos.get(RDF_TYPE + SEP + descendant) ?? []) {
        found.add(subject);
      }
    }
    return [...found].sort(byteCompare);
  }

  isA(node: string, cls: string): boolean {
    for (const type of this.spo.get(node + SEP + RDF_TYPE) ?? []) {
      if (this.closure.has(type + SEP + cls)) {
        return true;
      }
    }
    return false;
  }

  /** Every triple as a sorted line list, for byte-identity checks against the reference engine. */
  dumpTriples(): string[] {
    const lines: string[] = [];
    for (const [key, objects] of this.spo) {
      const [s, p] = key.split(SEP);
      for (const o of objects) {
        lines.push(`E\t${s}\t${p}\t${o}`);
      }
    }
    for (const [key, entries] of this.lit) {
      const [s, p] = key.split(SEP);
      for (const entry of entries) {
        const [val, datatype] = entry.split(SEP);
        lines.push(`L\t${s}\t${p}\t${val}\t${datatype}`);
      }
    }
    for (const pair of this.closure) {
      const [descendant, ancestor] = pair.split(SEP);
      lines.push(`C\t${descendant}\t${ancestor}`);
    }
    return lines.sort(byteCompare);
  }

  edgeCount(): number {
    return this.edges;
  }

  literalCount(): number {
    return this.literals;
  }

  tripleCount(): number {
    return this.edges + this.literals;
  }
}
