/** Shared helpers. */

/**
 * Compare two strings by their UTF-8 bytes, matching SQLite's BINARY collation (and so the Python
 * engine's `ORDER BY` on the graph store). For the ASCII IRIs and CURIEs the graph uses this equals
 * a code-unit comparison, but bytewise is exact for any literal value too.
 */
export function byteCompare(a: string, b: string): number {
  return Buffer.compare(Buffer.from(a, "utf-8"), Buffer.from(b, "utf-8"));
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
