/** Shared helpers. */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
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
 * Mirrors Python's implicit `str()` in an f-string for the handful of types a raw dict `.get()`
 * result can be in this codebase: `null`/`undefined` (an absent or JSON `null` field) as `"None"`,
 * matching Python's `None`; a boolean as `"True"`/`"False"`; anything else via plain `String()`
 * (already a no-op for the only other case these call sites ever feed it, an existing string).
 */
export function pyStr(value: unknown): string {
  if (value === null || value === undefined) {
    return "None";
  }
  if (typeof value === "boolean") {
    return value ? "True" : "False";
  }
  return String(value);
}

/**
 * A code point Python's `str.isprintable()` would call printable: `U+0020` itself, or a code point
 * whose Unicode general category is neither `C*` (control/format/surrogate/private-use/unassigned)
 * nor `Z*` other than `U+0020` (verified against an exhaustive Unicode 15.0 scan: 0 differences from
 * this characterization).
 */
function isPrintableCodePoint(codePoint: number): boolean {
  if (codePoint === 0x20) {
    return true;
  }
  const ch = String.fromCodePoint(codePoint);
  return !/\p{C}/u.test(ch) && !/\p{Z}/u.test(ch);
}

/**
 * Mirrors Python's `repr()` for the narrow set of types this codebase ever feeds it (a string, a
 * boolean, a finite number, or `null`/`undefined`): a string picks single quotes unless it contains
 * a `'` and no `"`, in which case it switches to double quotes (exactly `repr`'s own quote-choice
 * rule), escapes a literal backslash first, then the chosen quote character, then every
 * non-{@link isPrintableCodePoint} code point as `\t`/`\n`/`\r` (the three literal two-character
 * escapes), `\xHH` (<= 0xFF), `\uHHHH` (<= 0xFFFF), or `\UHHHHHHHH` (else) -- never a bare
 * `\x09`/`\x0a`/`\x0d` for tab/newline/carriage-return, which `repr` always spells out as the named
 * escape instead. An object or array (not reachable through this codebase's own schema-checked
 * inputs) is an accepted, out-of-scope input: rendered some deterministic way, not pinned to match
 * Python's `repr(dict)`/`repr(list)` character-for-character.
 */
export function pyRepr(value: unknown): string {
  if (value === null || value === undefined) {
    return "None";
  }
  if (typeof value === "boolean") {
    return value ? "True" : "False";
  }
  if (typeof value === "number") {
    return String(value);
  }
  if (typeof value !== "string") {
    return String(value);
  }
  const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
  let body = "";
  for (const ch of value) {
    const codePoint = ch.codePointAt(0) as number;
    if (ch === "\\") {
      body += "\\\\";
    } else if (ch === quote) {
      body += `\\${quote}`;
    } else if (ch === "\t") {
      body += "\\t";
    } else if (ch === "\n") {
      body += "\\n";
    } else if (ch === "\r") {
      body += "\\r";
    } else if (isPrintableCodePoint(codePoint)) {
      body += ch;
    } else if (codePoint <= 0xff) {
      body += `\\x${codePoint.toString(16).padStart(2, "0")}`;
    } else if (codePoint <= 0xffff) {
      body += `\\u${codePoint.toString(16).padStart(4, "0")}`;
    } else {
      body += `\\U${codePoint.toString(16).padStart(8, "0")}`;
    }
  }
  return `${quote}${body}${quote}`;
}

/**
 * A validating base64 decoder (standard alphabet, `base64.b64decode(..., validate=True)`'s TS
 * mirror): refuses any string containing a character outside `[A-Za-z0-9+/]=` or whose length is not
 * a multiple of 4, rather than `Buffer.from(s, "base64")`'s own silent-drop-invalid-characters
 * behaviour (which would let a junk-character payload decode as if it were valid, verdict and all).
 * Used wherever a DSSE/certificate field is base64: `verify.ts`'s `payload`/`sig`/`public_key`/
 * `signature` fields, never a bare `Buffer.from(s, "base64")`.
 */
export function b64dStrict(value: string): Buffer {
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(value) || value.length % 4 !== 0) {
    throw new Error(`invalid base64: ${pyRepr(value)}`);
  }
  return Buffer.from(value, "base64");
}

/**
 * Reads a JSON file with a fatal UTF-8 decode (Node's lenient `utf-8` string coercion would
 * otherwise silently replace invalid byte sequences with U+FFFD rather than refuse, unlike Python's
 * `read_text("utf-8")` and Java's decoder); throws on a decode or parse failure, never on a
 * valid-JSON-but-wrong-shape value (the caller decides what "wrong shape" means). `ignoreBOM: true`
 * keeps a leading U+FEFF in the decoded text instead of stripping it, so a byte-order-marked file
 * fails `JSON.parse` here exactly as it does against Python's and Java's decoders, rather than
 * silently parsing where they refuse.
 */
export function readJsonFileStrict(path: string): unknown {
  const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
    readFileSync(path),
  );
  return JSON.parse(text);
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
