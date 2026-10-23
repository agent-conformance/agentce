/**
 * Parse JSON evidence without losing the shape of a number token.
 *
 * `JSON.parse` folds `1.0`, `1e2` and `-0.0` to the safe integers `1`, `100` and `0`, and folds an
 * integer above 2^53 - 1 to a nearby double, so a value the canonical form (SPEC §6.7,
 * `spec/model/canonical-form.md`) must refuse would be accepted. `parseJson` reads the same text and
 * returns the same structure as `JSON.parse`, except that a number token outside the canonical number
 * grammar is returned as a {@link NonCanonicalNumber} carrying its source text; the canonical form
 * refuses it with the reason the other engines give. No network, no learned component.
 */

const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

/** A JSON number token the canonical number grammar refuses. */
export class NonCanonicalNumber {
  readonly source: string;
  readonly reason: "non_integer_number" | "integer_out_of_range";

  constructor(source: string, reason: "non_integer_number" | "integer_out_of_range") {
    this.source = source;
    this.reason = reason;
  }

  toString(): string {
    return this.source;
  }

  /** The double `JSON.parse` gives for this token (Python's `inf` and `-inf`, read from `Infinity`, too). */
  toNumber(): number {
    return Number(this.source.replace(/inf$/, "Infinity"));
  }
}

/** `value` as `JSON.parse` reads it: a number token {@link parseJson} kept exact becomes the double
 * `JSON.parse` would give, in nested arrays and objects too. */
export function asParsed(value: unknown): unknown {
  if (value instanceof NonCanonicalNumber) {
    return value.toNumber();
  }
  if (Array.isArray(value)) {
    return value.map(asParsed);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, asParsed(item)]));
  }
  return value;
}

// A number token only needs the slow path when it has a fraction or exponent, or enough digits to
// leave the safe-integer range; every other document parses identically with the native parser.
const NEEDS_TOKEN_SCAN = /[0-9][.eE]|[0-9]{16}/;
/**
 * CPython's `sys.int_max_str_digits` default: the most digits `int(str)` reads. Python's `json.loads`
 * refuses a longer integer literal; so does this reader, so the engines agree on which documents parse.
 */
export const MAX_INT_STR_DIGITS = 4300;
const NUMBER_TOKEN = /-?(?:0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?/y;

interface ReaderOptions {
  /** Read `NaN`, `Infinity` and `-Infinity` as Python's `json.loads` does. */
  readonly pythonConstants?: boolean;
  /** Refuse a key written twice in one object, compared decoded (`"st\u0061tus"` repeats `"status"`). */
  readonly uniqueKeys?: boolean;
  /** Return every number as `JSON.parse` would, never as a {@link NonCanonicalNumber}. */
  readonly plainNumbers?: boolean;
}

class Reader {
  private pos = 0;

  constructor(
    private readonly text: string,
    private readonly options: ReaderOptions = {},
  ) {}

  fail(what: string): never {
    throw new SyntaxError(`${what} in JSON at position ${this.pos}`);
  }

  private skipWhitespace(): void {
    while (this.pos < this.text.length) {
      const ch = this.text.charCodeAt(this.pos);
      if (ch !== 0x20 && ch !== 0x09 && ch !== 0x0a && ch !== 0x0d) {
        return;
      }
      this.pos += 1;
    }
  }

  document(): unknown {
    const value = this.value();
    this.skipWhitespace();
    if (this.pos !== this.text.length) {
      this.fail("Unexpected non-whitespace character after JSON");
    }
    return value;
  }

  private value(): unknown {
    this.skipWhitespace();
    const ch = this.text[this.pos];
    if (ch === "{") {
      return this.object();
    }
    if (ch === "[") {
      return this.array();
    }
    if (ch === '"') {
      return this.string();
    }
    for (const [word, result] of [
      ["true", true],
      ["false", false],
      ["null", null],
    ] as const) {
      if (this.text.startsWith(word, this.pos)) {
        this.pos += word.length;
        return result;
      }
    }
    if (this.options.pythonConstants === true) {
      // Python's `json.loads` reads these three words as floats; the canonical form refuses a float,
      // so each is kept as a non-canonical number carrying Python's own `repr` of it.
      for (const [word, repr] of PYTHON_CONSTANTS) {
        if (this.text.startsWith(word, this.pos)) {
          this.pos += word.length;
          return new NonCanonicalNumber(repr, "non_integer_number");
        }
      }
    }
    return this.number();
  }

  private number(): unknown {
    NUMBER_TOKEN.lastIndex = this.pos;
    const match = NUMBER_TOKEN.exec(this.text);
    if (match === null) {
      return this.fail("Unexpected token");
    }
    const token = match[0];
    this.pos += token.length;
    const fraction = match[1] !== undefined || match[2] !== undefined;
    if (fraction && this.options.plainNumbers !== true) {
      return new NonCanonicalNumber(token, "non_integer_number");
    }
    if (!fraction && token.length - (token[0] === "-" ? 1 : 0) > MAX_INT_STR_DIGITS) {
      return this.fail(`Integer literal exceeds ${MAX_INT_STR_DIGITS} digits`);
    }
    if (this.options.plainNumbers === true) {
      return Number(token);
    }
    const big = BigInt(token);
    if (big > MAX_SAFE || big < -MAX_SAFE) {
      return new NonCanonicalNumber(token, "integer_out_of_range");
    }
    return Number(token) || 0; // -0 is the integer 0
  }

  private string(): string {
    let end = this.pos + 1;
    while (end < this.text.length && this.text[end] !== '"') {
      end += this.text[end] === "\\" ? 2 : 1;
    }
    if (end >= this.text.length) {
      this.fail("Unterminated string");
    }
    const token = this.text.slice(this.pos, end + 1);
    this.pos = end + 1;
    return JSON.parse(token) as string; // exact escape and control-character rules of the platform
  }

  private array(): unknown[] {
    const items: unknown[] = [];
    this.pos += 1;
    this.skipWhitespace();
    if (this.text[this.pos] === "]") {
      this.pos += 1;
      return items;
    }
    for (;;) {
      items.push(this.value());
      this.skipWhitespace();
      const ch = this.text[this.pos];
      this.pos += 1;
      if (ch === "]") {
        return items;
      }
      if (ch !== ",") {
        this.pos -= 1;
        this.fail("Expected ',' or ']'");
      }
    }
  }

  private object(): Record<string, unknown> {
    const members: Record<string, unknown> = {};
    this.pos += 1;
    this.skipWhitespace();
    if (this.text[this.pos] === "}") {
      this.pos += 1;
      return members;
    }
    for (;;) {
      this.skipWhitespace();
      if (this.text[this.pos] !== '"') {
        this.fail("Expected a property name");
      }
      const key = this.string();
      this.skipWhitespace();
      if (this.text[this.pos] !== ":") {
        this.fail("Expected ':'");
      }
      this.pos += 1;
      if (this.options.uniqueKeys === true && Object.hasOwn(members, key)) {
        this.fail("A key is repeated in one object");
      }
      // defineProperty keeps a `__proto__` member an ordinary own property, as JSON.parse does.
      Object.defineProperty(members, key, {
        value: this.value(),
        enumerable: true,
        writable: true,
        configurable: true,
      });
      this.skipWhitespace();
      const ch = this.text[this.pos];
      this.pos += 1;
      if (ch === "}") {
        return members;
      }
      if (ch !== ",") {
        this.pos -= 1;
        this.fail("Expected ',' or '}'");
      }
    }
  }
}

/** The words Python's `json.loads` accepts beyond JSON, each with Python's `repr` of its float. */
const PYTHON_CONSTANTS = [
  ["NaN", "nan"],
  ["Infinity", "inf"],
  ["-Infinity", "-inf"],
] as const;

/** The non-blank JSON Lines of `text` by the profile's line rule ({@link jsonlRecords}). */
export function jsonlLines(text: string): Array<[number, string]> {
  return jsonlRecords(text);
}

/** Parse JSON text like `JSON.parse`, keeping a non-canonical number token as a marker value. With
 * `pythonConstants`, `NaN`, `Infinity` and `-Infinity` are read as Python's `json.loads` reads them
 * (as non-canonical numbers) instead of being refused. */
export function parseJson(text: string, options: { pythonConstants?: boolean } = {}): unknown {
  const pythonConstants = options.pythonConstants ?? false;
  if (!NEEDS_TOKEN_SCAN.test(text)) {
    try {
      return JSON.parse(text);
    } catch (exc) {
      if (!pythonConstants) {
        throw exc;
      }
    }
  }
  return new Reader(text, { pythonConstants }).document();
}

/** The deepest container nesting a strict reader accepts (Jackson's own default limit); all three
 * engines enforce it with the same byte pre-scan, so they refuse at the same depth. Mirrors
 * `safe_json.MAX_JSON_DEPTH`. */
export const MAX_JSON_DEPTH = 1000;

/** Whether `raw` opens more than {@link MAX_JSON_DEPTH} containers at once, counting `[` and `{` up and
 * `]` and `}` down outside strings (a backslash in a string skips the next byte). Run before parsing,
 * so depth is found first whatever else is wrong with the bytes. Mirrors `safe_json.too_deep`. */
export function tooDeep(raw: Buffer): boolean {
  let depth = 0;
  let inString = false;
  for (let i = 0; i < raw.length; i++) {
    const byte = raw[i];
    if (inString) {
      if (byte === 0x5c) {
        i++;
      } else if (byte === 0x22) {
        inString = false;
      }
    } else if (byte === 0x22) {
      inString = true;
    } else if (byte === 0x5b || byte === 0x7b) {
      depth++;
      if (depth > MAX_JSON_DEPTH) {
        return true;
      }
    } else if (byte === 0x5d || byte === 0x7d) {
      depth--;
    }
  }
  return false;
}

/** Parse one report file (or, with `line`, that JSON Lines line of one) under the JSON profile with a
 * byte-order mark refused (SPEC 8.7, §13.3.4 stage 4), as plain JS values: every engine's readiness, sign
 * and `report --validate` read report files by this rule. Mirrors `safe_json.load_report_json` and
 * `Json.parseReport`; outside the profile throws {@link JsonProfileError}. */
export function parseReportJson(text: string, line?: number): unknown {
  checkProfile(text, line);
  return new Reader(text, { plainNumbers: true }).document();
}

// --- The JSON profile (SPEC 8.7) -------------------------------------------------------------------
//
// One hand-written check, the same rules as Python's `safe_json.check_profile` and `Json.java`, runs
// before any parser sees the text, so a file outside the profile is refused with the rule and line
// whatever the parser would have done. Rule ids are the `rule` values of `spec/report/json-cases.json`.

const BOM = [0xef, 0xbb, 0xbf] as const;
const LINE_BREAKS = "\f\u0085  ";
const SIMPLE_ESCAPES = '"\\/bfnrt';

/** Text outside the JSON profile: `rule` is a case-table rule id, `line` is 1-based. The message is the
 * phrase every engine's refusal carries. */
export class JsonProfileError extends SyntaxError {
  constructor(
    readonly rule: string,
    readonly line: number,
  ) {
    super(`outside the JSON profile: ${rule} at line ${line} (SPEC 8.7)`);
  }
}

function hex4(text: string, i: number): number | undefined {
  const digits = text.slice(i, i + 4);
  return /^[0-9a-fA-F]{4}$/.test(digits) ? Number.parseInt(digits, 16) : undefined;
}

/** Scan the string whose quote is at `i`; return its decoded value and the index after it. */
function scanString(text: string, start: number, line: number): [string, number] {
  let out = "";
  let i = start + 1;
  for (;;) {
    if (i >= text.length) {
      throw new JsonProfileError("syntax", line);
    }
    const c = text[i];
    if (c === '"') {
      return [out, i + 1];
    }
    if (c < " ") {
      throw new JsonProfileError("syntax", line);
    }
    if (c !== "\\") {
      out += c;
      i += 1;
      continue;
    }
    const e = text[i + 1] ?? "";
    if (e !== "" && SIMPLE_ESCAPES.includes(e)) {
      out += JSON.parse(`"\\${e}"`) as string;
      i += 2;
      continue;
    }
    if (e !== "u") {
      throw new JsonProfileError("syntax", line);
    }
    let cp = hex4(text, i + 2);
    if (cp === undefined) {
      throw new JsonProfileError("syntax", line);
    }
    i += 6;
    if (cp >= 0xdc00 && cp <= 0xdfff) {
      throw new JsonProfileError("lone_surrogate", line);
    }
    if (cp >= 0xd800 && cp <= 0xdbff) {
      const low = text.slice(i, i + 2) === "\\u" ? hex4(text, i + 2) : undefined;
      if (low === undefined || low < 0xdc00 || low > 0xdfff) {
        throw new JsonProfileError("lone_surrogate", line);
      }
      cp = 0x10000 + ((cp - 0xd800) << 10) + (low - 0xdc00);
      i += 6;
    }
    out += String.fromCodePoint(cp);
  }
}

const isDigit = (c: string | undefined): boolean => c !== undefined && c >= "0" && c <= "9";

/** Scan the number starting at `i` (a `-` or a digit); return the index after it. */
function scanNumber(text: string, start: number, line: number): number {
  let i = start;
  if (text[i] === "-") {
    i += 1;
    if (text.startsWith("Infinity", i)) {
      throw new JsonProfileError("infinity", line);
    }
  }
  if (!isDigit(text[i])) {
    throw new JsonProfileError("syntax", line);
  }
  if (text[i] === "0" && isDigit(text[i + 1])) {
    throw new JsonProfileError("leading_zero", line);
  }
  while (isDigit(text[i])) {
    i += 1;
  }
  let isFloat = false;
  if (text[i] === ".") {
    isFloat = true;
    i += 1;
    if (!isDigit(text[i])) {
      throw new JsonProfileError("syntax", line);
    }
    while (isDigit(text[i])) {
      i += 1;
    }
  }
  if (text[i] === "e" || text[i] === "E") {
    isFloat = true;
    i += 1;
    if (text[i] === "+" || text[i] === "-") {
      i += 1;
    }
    if (!isDigit(text[i])) {
      throw new JsonProfileError("syntax", line);
    }
    while (isDigit(text[i])) {
      i += 1;
    }
  }
  const lexeme = text.slice(start, i);
  if (isFloat) {
    if (!Number.isFinite(Number(lexeme))) {
      throw new JsonProfileError("infinity", line);
    }
  } else if (lexeme.length - (lexeme.startsWith("-") ? 1 : 0) > MAX_INT_STR_DIGITS) {
    throw new JsonProfileError("integer_digit_limit", line);
  }
  return i;
}

type Expect = "value" | "first" | "member" | "key" | "first_key" | "colon" | "after" | "end";

/** Throw {@link JsonProfileError} when `text` (one JSON document, or one JSON Lines line when `jsonlLine`
 * gives its number) is outside the profile. Iterative, so depth costs no recursion. Mirrors Python's
 * `safe_json.check_profile`. */
export function checkProfile(text: string, jsonlLine?: number): void {
  let line = jsonlLine ?? 1;
  const n = text.length;
  let i = 0;
  // Each open container: an object's keys seen, or null for an array.
  const stack: Array<Set<string> | null> = [];
  let expect: Expect = "value";
  let sawCr = false;
  for (;;) {
    while (i < n && " \t\n\r".includes(text[i])) {
      if (text[i] === "\n") {
        line += 1;
      } else if (text[i] === "\r") {
        sawCr = true;
      }
      i += 1;
    }
    if (i >= n) {
      if (expect !== "end") {
        throw new JsonProfileError("syntax", line);
      }
      return;
    }
    const c = text[i];
    if (c === "﻿") {
      throw new JsonProfileError("bom", line);
    }
    if (c === "/") {
      throw new JsonProfileError("comment", line);
    }
    if (jsonlLine !== undefined && LINE_BREAKS.includes(c)) {
      throw new JsonProfileError("jsonl_line_split", line);
    }
    if (expect === "end") {
      throw new JsonProfileError(
        jsonlLine !== undefined && sawCr ? "jsonl_line_split" : "extra_value",
        line,
      );
    }
    const top = stack.length > 0 ? stack[stack.length - 1] : undefined;
    if (expect === "value" || expect === "first" || expect === "member") {
      if (c === "]" && expect === "first" && top === null) {
        stack.pop();
        i += 1;
      } else if (c === "]" || c === "}") {
        throw new JsonProfileError(
          expect === "value" && stack.length > 0 ? "trailing_comma" : "syntax",
          line,
        );
      } else if (c === "[" || c === "{") {
        stack.push(c === "[" ? null : new Set());
        if (stack.length > MAX_JSON_DEPTH) {
          throw new JsonProfileError("depth_over_limit", line);
        }
        i += 1;
        expect = c === "[" ? "first" : "first_key";
        continue;
      } else if (c === '"') {
        i = scanString(text, i, line)[1];
      } else if (c === "-" || isDigit(c)) {
        i = scanNumber(text, i, line);
      } else if (c === "+") {
        throw new JsonProfileError("leading_plus", line);
      } else {
        const word = (
          [
            ["true", ""],
            ["false", ""],
            ["null", ""],
            ["NaN", "nan"],
            ["Infinity", "infinity"],
          ] as const
        ).find(([w]) => text.startsWith(w, i));
        if (word === undefined) {
          throw new JsonProfileError("syntax", line);
        }
        if (word[1] !== "") {
          throw new JsonProfileError(word[1], line);
        }
        i += word[0].length;
      }
      expect = stack.length > 0 ? "after" : "end";
    } else if (expect === "key" || expect === "first_key") {
      if (c === "}" && expect === "first_key") {
        stack.pop();
        i += 1;
        expect = stack.length > 0 ? "after" : "end";
      } else if (c === "}") {
        throw new JsonProfileError("trailing_comma", line);
      } else if (c === '"') {
        const [key, next] = scanString(text, i, line);
        i = next;
        const keys = top as Set<string>;
        if (keys.has(key)) {
          throw new JsonProfileError("duplicate_key", line);
        }
        keys.add(key);
        expect = "colon";
      } else {
        throw new JsonProfileError("syntax", line);
      }
    } else if (expect === "colon") {
      if (c !== ":") {
        throw new JsonProfileError("syntax", line);
      }
      i += 1;
      expect = "member";
    } else {
      if (c === ",") {
        i += 1;
        expect = top === null ? "value" : "key";
      } else if ((c === "]" && top === null) || (c === "}" && top !== null)) {
        stack.pop();
        i += 1;
        expect = stack.length > 0 ? "after" : "end";
      } else {
        throw new JsonProfileError("syntax", line);
      }
    }
  }
}

/** The index of the first byte of `raw` that does not start a well-formed UTF-8 sequence (overlong
 * forms, encoded surrogates and code points past U+10FFFF included), or -1 when all of it is UTF-8. */
function invalidUtf8At(raw: Uint8Array): number {
  let i = 0;
  while (i < raw.length) {
    const b = raw[i];
    if (b < 0x80) {
      i += 1;
      continue;
    }
    let need: number;
    let lo = 0x80;
    let hi = 0xbf;
    if (b >= 0xc2 && b <= 0xdf) {
      need = 1;
    } else if (b >= 0xe0 && b <= 0xef) {
      need = 2;
      if (b === 0xe0) lo = 0xa0;
      if (b === 0xed) hi = 0x9f;
    } else if (b >= 0xf0 && b <= 0xf4) {
      need = 3;
      if (b === 0xf0) lo = 0x90;
      if (b === 0xf4) hi = 0x8f;
    } else {
      return i;
    }
    for (let k = 1; k <= need; k++) {
      const c = raw[i + k];
      if (c === undefined || c < (k === 1 ? lo : 0x80) || c > (k === 1 ? hi : 0xbf)) {
        return i;
      }
    }
    i += need + 1;
  }
  return -1;
}

function countLf(raw: Uint8Array, end: number): number {
  let lines = 0;
  for (let i = 0; i < end; i++) {
    if (raw[i] === 0x0a) lines += 1;
  }
  return lines;
}

function hasBom(raw: Uint8Array): boolean {
  return raw.length >= 3 && raw[0] === BOM[0] && raw[1] === BOM[1] && raw[2] === BOM[2];
}

/** `raw` without one leading byte-order mark (SPEC 8.7: an input's mark is ignored). */
export function stripBom(raw: Buffer): Buffer {
  return hasBom(raw) ? raw.subarray(3) : raw;
}

/** `raw` as text under the profile's encoding rules: one leading byte-order mark ignored (`bom: "ignore"`)
 * or refused (`"refuse"`), then strict UTF-8. Mirrors Python's `safe_json._decode`. */
export function decodeProfile(raw: Uint8Array, bom: "ignore" | "refuse"): string {
  let bytes = raw;
  if (hasBom(bytes)) {
    if (bom === "refuse") {
      throw new JsonProfileError("bom", 1);
    }
    bytes = bytes.subarray(3);
  }
  const bad = invalidUtf8At(bytes);
  if (bad >= 0) {
    throw new JsonProfileError("invalid_utf8", countLf(bytes, bad) + 1);
  }
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
}

/** One JSON document under the profile, with number kinds kept: a float (any `.` or exponent) is a
 * {@link NonCanonicalNumber} marker and an integer past 2^53 - 1 too, so `5.0` stays apart from `5`.
 * `bom` is `"ignore"` for an input an adopter's tool may have written, `"refuse"` for a file an engine
 * wrote. Mirrors Python's `safe_json.read_json`. */
export function readJson(raw: Uint8Array, bom: "ignore" | "refuse"): unknown {
  const text = decodeProfile(raw, bom);
  checkProfile(text);
  return new Reader(text).document();
}

/** The non-blank JSON Lines of `text` as [1-based line number, line]: LF alone ends a line, a CRLF's CR
 * is dropped, a line holding only spaces and tabs is blank. Mirrors Python's `safe_json.jsonl_lines`. */
export function jsonlRecords(text: string): Array<[number, string]> {
  const out: Array<[number, string]> = [];
  text.split("\n").forEach((raw, i) => {
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (/[^ \t]/.test(line)) {
      out.push([i + 1, line]);
    }
  });
  return out;
}

/** One JSON Lines line (`line` is its number in the file) under the profile, number kinds kept. */
export function readJsonlLine(text: string, line: number): unknown {
  checkProfile(text, line);
  return new Reader(text).document();
}

/** JSON Lines under the profile: [line number, value] per record, a {@link JsonProfileError} in place of
 * the value of a line outside it. Bytes that are not UTF-8 or a refused mark throw for the whole file.
 * Mirrors Python's `safe_json.read_jsonl`. */
export function readJsonl(raw: Uint8Array, bom: "ignore" | "refuse"): Array<[number, unknown]> {
  return jsonlRecords(decodeProfile(raw, bom)).map(([n, line]): [number, unknown] => {
    try {
      return [n, readJsonlLine(line, n)];
    } catch (exc) {
      if (exc instanceof JsonProfileError) {
        return [n, exc];
      }
      throw exc;
    }
  });
}

/** The refusal sentence for `noun` that `exc` failed to read: the profile rule when it names one, else
 * `<noun> is not valid JSON.` Mirrors Python's `safe_json.not_valid`. */
export function notValid(noun: string, exc: unknown): string {
  return exc instanceof JsonProfileError
    ? `${noun} is ${exc.message}.`
    : `${noun} is not valid JSON.`;
}

/** Data shipped inside the package, under the same profile, as plain JS values typed as `JSON.parse`
 * types them; a failure is a build defect. */
// biome-ignore lint/suspicious/noExplicitAny: bundled data is typed at each call site, as JSON.parse leaves it
export function readBundled(raw: Uint8Array): any {
  const text = decodeProfile(raw, "refuse");
  checkProfile(text);
  return JSON.parse(text);
}

/** Whether `value` read as a JSON integer (SPEC 8.7: the form is by syntax, so `0.0`, `1e2`, `true` and
 * `"3"` are not). An integer past 2^53 - 1 is one, held as a marker. */
export function isJsonInteger(value: unknown): boolean {
  if (value instanceof NonCanonicalNumber) {
    return value.reason === "integer_out_of_range";
  }
  return typeof value === "number" && Number.isInteger(value);
}

/** The one rendering of a JSON number in reason and report text (SPEC 8.7): an integer as its digits, a
 * float as the shortest decimal that reads back to it, `.0` when integral, exponent form `d.ddde+XX` at
 * 1e16 and above or below 1e-4, `-0.0` for negative zero (Python's `repr`). A marker renders from its
 * token, so `5.0` stays `5.0`; a plain JS number renders as an integer when integral. */
export function renderNumber(value: number | NonCanonicalNumber): string {
  if (value instanceof NonCanonicalNumber) {
    if (value.reason === "integer_out_of_range") {
      return BigInt(value.source).toString();
    }
    // Python's `nan`/`inf` from a `pythonConstants` read have no JSON rendering: shown as read.
    return Number.isFinite(Number(value.source)) ? renderFloat(Number(value.source)) : value.source;
  }
  return Number.isInteger(value) && !Object.is(value, -0) ? String(value) : renderFloat(value);
}

function renderFloat(v: number): string {
  if (Object.is(v, -0)) {
    return "-0.0";
  }
  const sign = v < 0 ? "-" : "";
  // The shortest round-trip digits and the decimal exponent, from JS's own exponential form.
  const [mantissa, exp] = Math.abs(v).toExponential().split("e");
  const digits = mantissa.replace(".", "");
  const e = Number(exp);
  const abs = Math.abs(v);
  if (abs >= 1e16 || (abs !== 0 && abs < 1e-4)) {
    const frac = digits.length > 1 ? `.${digits.slice(1)}` : "";
    return `${sign}${digits[0]}${frac}e${e < 0 ? "-" : "+"}${String(Math.abs(e)).padStart(2, "0")}`;
  }
  if (e < 0) {
    return `${sign}0.${"0".repeat(-e - 1)}${digits}`;
  }
  const intPart = digits.slice(0, e + 1).padEnd(e + 1, "0");
  const fracPart = digits.slice(e + 1);
  return `${sign}${intPart}.${fracPart === "" ? "0" : fracPart}`;
}

/** A JSON value as reason text: numbers by {@link renderNumber}, literals as JSON writes them, a string
 * JSON-quoted, a container as compact JSON with its keys sorted. Mirrors Python's `render_value`. */
export function renderValue(value: unknown): string {
  if (value instanceof NonCanonicalNumber || typeof value === "number") {
    return renderNumber(value);
  }
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(renderValue).join(",")}]`;
  }
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort(codePointCompare)
    .map((k) => `${JSON.stringify(k)}:${renderValue(record[k])}`)
    .join(",")}}`;
}

function codePointCompare(a: string, b: string): number {
  const x = [...a].map((c) => c.codePointAt(0) as number);
  const y = [...b].map((c) => c.codePointAt(0) as number);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    if (x[i] !== y[i]) return x[i] - y[i];
  }
  return x.length - y.length;
}

/** An integral JSON number as an exact bigint (an integral double converts exactly), else undefined. */
function integralValue(value: number | NonCanonicalNumber): bigint | undefined {
  if (value instanceof NonCanonicalNumber && value.reason === "integer_out_of_range") {
    return BigInt(value.source);
  }
  const d = value instanceof NonCanonicalNumber ? Number(value.source) : value;
  return Number.isInteger(d) ? BigInt(d) : undefined;
}

/** Equality of two JSON values (SPEC 8.7): numbers by value, values of different JSON types never equal
 * (`1` is not `true`), arrays element-wise, objects by key set and values. Mirrors Python's `json_equal`. */
export function jsonEqual(left: unknown, right: unknown): boolean {
  const isNum = (v: unknown): v is number | NonCanonicalNumber =>
    typeof v === "number" || v instanceof NonCanonicalNumber;
  if (isNum(left) || isNum(right)) {
    if (!isNum(left) || !isNum(right)) {
      return false;
    }
    // An integer and a float compare by exact value; two non-integral doubles as doubles.
    const l = integralValue(left);
    const r = integralValue(right);
    if (l !== undefined || r !== undefined) {
      return l === r;
    }
    return Number(String(left)) === Number(String(right));
  }
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") {
    return left === right;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, i) => jsonEqual(item, right[i]))
    );
  }
  const a = left as Record<string, unknown>;
  const b = right as Record<string, unknown>;
  const keys = Object.keys(a);
  return (
    keys.length === Object.keys(b).length &&
    keys.every((k) => Object.hasOwn(b, k) && jsonEqual(a[k], b[k]))
  );
}
