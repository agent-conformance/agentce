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

/**
 * `JSON.stringify(value)`, then escape every UTF-16 code unit above `0x7E` as a lowercase `\uxxxx`
 * sequence -- matching Python's `json.dumps(..., ensure_ascii=True)` (the default) and Java's
 * `Json.pretty`, both lowercase hex. Since JS strings are already UTF-16 code units, this naturally
 * reproduces Python's surrogate-pair escaping for astral characters too: each half of the pair is a
 * code unit above `0x7E` and is escaped on its own, exactly as `ensure_ascii=True` does. Every
 * command's `--json` envelope and `--format json` output must go through this, not a bare
 * `JSON.stringify`, so no engine leaks unescaped non-ASCII content Python's own CLI always escapes.
 */
export function jsonStringifyAscii(value: unknown, indent?: number): string {
  const raw = indent === undefined ? JSON.stringify(value) : JSON.stringify(value, null, indent);
  let out = "";
  // Iterate UTF-16 code units, not code points: an astral character is already a surrogate pair in
  // a JS string, and each half (each above 0x7E on its own) must be escaped separately to match
  // Python's `ensure_ascii=True` byte-for-byte, never as one combined \u{......} codepoint escape.
  for (let i = 0; i < raw.length; i++) {
    const code = raw.charCodeAt(i);
    if (code > 0x7e) {
      out += `\\u${code.toString(16).padStart(4, "0")}`;
    } else {
      out += raw[i];
    }
  }
  return out;
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
