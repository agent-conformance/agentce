/**
 * The AgentCE canonical form (SPEC §6.7): RFC 8785 JCS under the evidence profile.
 *
 * The evidence profile forbids non-integer JSON numbers, so the hard part of JCS — shortest
 * round-trip formatting of IEEE-754 doubles — never arises, and the canonical form is a small exact
 * set of rules every engine reproduces byte for byte (see `spec/rules`/`canonical-form.md`, the
 * shared vectors under `spec/model/test-vectors/`). This is the TypeScript engine's own
 * implementation; it must agree with the Python reference to the byte.
 *
 * A JSON number is accepted only when its token is an integer (no fraction, no exponent) of at most
 * 2^53 - 1 in magnitude. `1.5`, `0.1`, `1e+30`, `1.0`, `1e2` and `-0.0` are refused as
 * `non_integer_number`, and `9007199254740992` and above as `integer_out_of_range`, exactly as the
 * reference does. `JSON.parse` folds those tokens to ordinary numbers before the canonical form can
 * see them, so evidence is read with `parseJson` (`./json`), which keeps them refusable.
 * No network, no learned component.
 */

import { createHash } from "node:crypto";

import { NonCanonicalNumber } from "./json";

export class CanonicalizationError extends Error {
  readonly reason: string;

  constructor(reason: string, detail = "") {
    super(detail ? `${reason}: ${detail}` : reason);
    this.reason = reason;
    this.name = "CanonicalizationError";
  }
}

const SHORT_ESCAPES = new Map<number, string>([
  [0x08, "\\b"],
  [0x09, "\\t"],
  [0x0a, "\\n"],
  [0x0c, "\\f"],
  [0x0d, "\\r"],
  [0x22, '\\"'],
  [0x5c, "\\\\"],
]);

function nfc(text: string): string {
  return text.normalize("NFC");
}

function serialiseString(text: string): string {
  const out: string[] = ['"'];
  for (const ch of nfc(text)) {
    const code = ch.codePointAt(0) as number;
    const escaped = SHORT_ESCAPES.get(code);
    if (escaped !== undefined) {
      out.push(escaped);
    } else if (code < 0x20) {
      out.push(`\\u${code.toString(16).padStart(4, "0")}`);
    } else {
      out.push(ch);
    }
  }
  out.push('"');
  return out.join("");
}

function serialiseObject(obj: Record<string, unknown>): string {
  const members: Array<[string, unknown]> = [];
  const seen = new Set<string>();
  for (const rawKey of Object.keys(obj)) {
    const key = nfc(rawKey);
    if (seen.has(key)) {
      throw new CanonicalizationError("duplicate_key_after_nfc", key);
    }
    seen.add(key);
    members.push([key, obj[rawKey]]);
  }
  // JS string comparison is by UTF-16 code unit, matching the reference's UTF-16-BE byte order.
  members.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return `{${members.map(([key, val]) => `${serialiseString(key)}:${serialise(val)}`).join(",")}}`;
}

function serialise(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (value instanceof NonCanonicalNumber) {
    throw new CanonicalizationError(value.reason, value.source);
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      const reason = Number.isInteger(value) ? "integer_out_of_range" : "non_integer_number";
      throw new CanonicalizationError(reason, String(value));
    }
    return String(value);
  }
  if (typeof value === "string") {
    return serialiseString(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(serialise).join(",")}]`;
  }
  if (typeof value === "object") {
    return serialiseObject(value as Record<string, unknown>);
  }
  throw new CanonicalizationError("unsupported_type", typeof value);
}

/** The canonical form of a JSON value as a string (SPEC §6.7). */
export function canonicalString(value: unknown): string {
  return serialise(value);
}

/** The canonical form of a JSON value as UTF-8 bytes (SPEC §6.7). */
export function canonicalize(value: unknown): Buffer {
  return Buffer.from(canonicalString(value), "utf-8");
}

/** The lowercase hex SHA-256 of the canonical form (basis of `integrity.hash`, SPEC §6.6). */
export function sha256Hex(value: unknown): string {
  return createHash("sha256").update(canonicalize(value)).digest("hex");
}
