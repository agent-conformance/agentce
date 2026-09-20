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
}

// A number token only needs the slow path when it has a fraction or exponent, or enough digits to
// leave the safe-integer range; every other document parses identically with the native parser.
const NEEDS_TOKEN_SCAN = /[0-9][.eE]|[0-9]{16}/;
const NUMBER_TOKEN = /-?(?:0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?/y;

class Reader {
  private pos = 0;

  constructor(private readonly text: string) {}

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
    if (match[1] !== undefined || match[2] !== undefined) {
      return new NonCanonicalNumber(token, "non_integer_number");
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

/** Parse JSON text like `JSON.parse`, keeping a non-canonical number token as a marker value. */
export function parseJson(text: string): unknown {
  if (!NEEDS_TOKEN_SCAN.test(text)) {
    return JSON.parse(text);
  }
  return new Reader(text).document();
}
