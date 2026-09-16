/** Shared helpers. */

/**
 * Compare two strings by their UTF-8 bytes, matching SQLite's BINARY collation (and so the Python
 * engine's `ORDER BY` on the graph store). For the ASCII IRIs and CURIEs the graph uses this equals
 * a code-unit comparison, but bytewise is exact for any literal value too.
 */
export function byteCompare(a: string, b: string): number {
  return Buffer.compare(Buffer.from(a, "utf-8"), Buffer.from(b, "utf-8"));
}
