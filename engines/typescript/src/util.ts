/** Shared helpers. */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

/**
 * Compare two strings by their UTF-8 bytes, matching SQLite's BINARY collation (and so the Python
 * engine's `ORDER BY` on the graph store). For the ASCII IRIs and CURIEs the graph uses this equals
 * a code-unit comparison, but bytewise is exact for any literal value too.
 */
export function byteCompare(a: string, b: string): number {
  return Buffer.compare(Buffer.from(a, "utf-8"), Buffer.from(b, "utf-8"));
}

/** Write `records` to `path` as one compact JSON object per line, in input order (side-channel diagnostics — not part of the cross-engine canonical output set). */
export function writeJsonl(records: Iterable<unknown>, path: string): void {
  mkdirSync(dirname(path), { recursive: true });
  const lines: string[] = [];
  for (const record of records) {
    lines.push(JSON.stringify(sortKeysDeep(record)));
  }
  writeFileSync(path, lines.length > 0 ? `${lines.join("\n")}\n` : "");
}

/**
 * Recursively sort object keys, matching Python's `json.dumps(sort_keys=True)`. With
 * `JSON.stringify(sortKeysDeep(x), null, 2)` the output is byte-for-byte identical to
 * `json.dumps(x, sort_keys=True, indent=2)` for ASCII content.
 */
export function sortKeysDeep(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortKeysDeep);
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort(byteCompare)) {
      out[key] = sortKeysDeep((value as Record<string, unknown>)[key]);
    }
    return out;
  }
  return value;
}
