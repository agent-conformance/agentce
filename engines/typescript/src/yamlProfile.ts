/**
 * The YAML profile every YAML input is checked against before it is parsed (SPEC 8.7).
 *
 * A small hand-written lexical pass, the same rules in all three engines (Python `yaml_profile.py`, Java
 * `YamlProfile.java`), and not a port of any YAML library's scanner. It finds the first construct outside the
 * profile in document order and names its rule; the caller refuses the file under its role's existing `input.*`
 * key. Repeated keys need the parsed tree, so the loader checks them on the composed nodes (`duplicate_key`). The
 * rules and their edge cases are pinned by the shared case table `spec/rules/yaml-profile-cases.json`. Each line is
 * read by code point, as Python indexes a `str`, so a column never depends on UTF-16 surrogates.
 */

export const MAX_DEPTH = 100;

/** One hint per rule, the same words in every engine (the case table's `hints` holds the same text). */
export const HINTS: Readonly<Record<string, string>> = {
  anchor: "write the value out in full instead of using an anchor",
  alias: "write the value out in full instead of using an alias",
  merge_key: "write the merged keys out in full instead of using <<",
  tag: "remove the tag; quote the value to keep it as text",
  directive: "remove the directive line",
  explicit_key: "write the key as a plain or quoted key followed by a colon",
  second_document: "keep one document in the file and remove the extra --- or ... line",
  tab: "replace the tab with spaces",
  non_string_key: "quote the key to make it text",
  duplicate_key: "write each key once",
  yaml11_bool: "quote the value to keep it as text",
  underscore_number: "quote the value to keep it as text, or write the number without _",
  sexagesimal: "quote the value to keep it as text",
  octal: "quote the value to keep it as text",
  exponent_no_dot: "quote the value to keep it as text, or write the number with a dot (1.0e3)",
  flow_scalar_indicator_start: "quote the value to keep it as text",
  flow_line_not_indented: "indent the line further than the key the list or mapping belongs to",
  depth_over_100: "flatten the nesting to at most 100 levels",
};

const YAML11_BOOL = /^(?:y|n|yes|no|on|off)$/i;
const UNDERSCORE_NUMBER =
  /^(?:[-+]?(0b[01_]+|0x[0-9a-fA-F_]+|0[0-7_]+|[0-9][0-9_]*(\.[0-9_]*)?([eE][-+][0-9]+)?|\.[0-9_]+([eE][-+][0-9]+)?))$/;
const SEXAGESIMAL = /^(?:[-+]?[0-9][0-9_]*(:[0-5]?[0-9])+(\.[0-9_]*)?)$/;
const OCTAL = /^(?:[-+]?0[0-7]+|0o[0-7]+)$/;
const EXPONENT_NO_DOT = /^(?:[-+]?[0-9]+[eE][-+]?[0-9]+)$/;
/** A plain key in this list does not read as a string (the written list of SPEC 8.7's "every mapping key is a
 * string"). */
const NON_STRING_KEY = new RegExp(
  "^(?:" +
    "|~|null|Null|NULL|true|True|TRUE|false|False|FALSE" +
    "|[-+]?(0|[1-9][0-9_]*)|[-+]?0b[01_]+|[-+]?0x[0-9a-fA-F_]+" +
    "|[-+]?[0-9][0-9_]*\\.[0-9_]*([eE][-+][0-9]+)?|\\.[0-9][0-9_]*([eE][-+][0-9]+)?" +
    "|[-+]?\\.(inf|Inf|INF)|\\.(nan|NaN|NAN)" +
    "|[0-9]{4}-[0-9]{2}-[0-9]{2}" +
    "|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}([Tt]|[ \\t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(\\.[0-9]*)?([ \\t]*(Z|[-+][0-9]{1,2}(:[0-9]{2})?))?" +
    ")$",
);

const FLOW_INDICATORS = ",[]{}";

const SCALAR_RULES: readonly (readonly [string, RegExp])[] = [
  ["yaml11_bool", YAML11_BOOL],
  ["underscore_number", UNDERSCORE_NUMBER],
  ["sexagesimal", SEXAGESIMAL],
  ["octal", OCTAL],
  ["exponent_no_dot", EXPONENT_NO_DOT],
];

export class ProfileViolation {
  constructor(
    readonly rule: string,
    readonly line: number,
  ) {}

  /** The message tail every engine prints after naming the file, byte for byte. */
  message(): string {
    return `is outside the YAML profile: ${this.rule} at line ${this.line} (SPEC 8.7)`;
  }

  get hint(): string {
    return HINTS[this.rule] as string;
  }
}

/** A YAML file outside the SPEC 8.7 profile; the caller refuses it under the file role's `input.*` key. */
export class YamlProfileError extends Error {
  constructor(readonly violation: ProfileViolation) {
    super(violation.message());
    this.name = "YamlProfileError";
  }
}

class Violation extends Error {
  constructor(readonly found: ProfileViolation) {
    super(found.rule);
  }
}

/** The rule a plain scalar's text breaks, or `null`. */
export function scalarRule(text: string): string | null {
  for (const [rule, pattern] of SCALAR_RULES) {
    if (pattern.test(text) && (rule !== "underscore_number" || text.includes("_"))) {
      return rule;
    }
  }
  return null;
}

/** One line as code points, indexed as Python indexes a `str`. */
type Line = readonly string[];

const text = (line: Line, from: number, to = line.length): string => line.slice(from, to).join("");

function spaceOrEnd(line: Line, i: number): boolean {
  return i >= line.length || line[i] === " " || line[i] === "\t";
}

/** `value` without its trailing spaces (an index walk: a regex anchored at the end backtracks on long inner runs). */
function rstripSpaces(value: string): string {
  let end = value.length;
  while (end > 0 && value[end - 1] === " ") {
    end -= 1;
  }
  return value.slice(0, end);
}

type BlockKind = "map" | "seq";

class Scanner {
  private readonly lines: string[];
  private ln = 0;
  private contentSeen = false;
  private docStartSeen = false;
  /** Open block collections: [column, kind]. */
  private readonly stack: [number, BlockKind][] = [];
  /** Open flow collections. */
  private readonly flow: string[] = [];
  /** The enclosing block's indentation when the outermost flow collection opened. */
  private flowIndent = -1;
  /** A quoted scalar left open at the end of a line. */
  private quote: string | null = null;
  /** The parent indentation while inside a block scalar body. */
  private blockScalar: number | null = null;
  /** A block plain scalar that may continue: text, line, owner. */
  private pending: [string, number, number] | null = null;
  private pendingMulti = false;

  constructor(source: string) {
    const body = source.startsWith("﻿") ? source.slice(1) : source;
    this.lines = body.split(/\r\n|\r|\n/);
  }

  private fail(rule: string, line?: number): Violation {
    return new Violation(new ProfileViolation(rule, line ?? this.ln));
  }

  run(): ProfileViolation | null {
    try {
      this.lines.forEach((line, index) => {
        this.ln = index + 1;
        this.line(Array.from(line));
      });
      this.finishPending();
    } catch (error) {
      if (error instanceof Violation) {
        return error.found;
      }
      throw error;
    }
    return null;
  }

  // -- depth ---------------------------------------------------------------------------------------------------

  private checkDepth(): void {
    if (this.stack.length + this.flow.length > MAX_DEPTH) {
      throw this.fail("depth_over_100");
    }
  }

  private blockEntry(col: number, kind: BlockKind): void {
    const last = this.stack.at(-1);
    if (last !== undefined && last[0] === col) {
      const top = last[1];
      if (top === kind) {
        return;
      }
      if (top === "seq" && kind === "map") {
        this.stack.pop(); // an indentless sequence ends at the next key of its mapping
        const under = this.stack.at(-1);
        if (under !== undefined && under[0] === col && under[1] === "map") {
          return;
        }
      }
    }
    this.stack.push([col, kind]);
    this.checkDepth();
  }

  private owner(): number {
    return this.stack.at(-1)?.[0] ?? -1;
  }

  // -- plain scalars -------------------------------------------------------------------------------------------

  private checkPlain(value: string, line: number, isKey: boolean): void {
    if (value === "<<") {
      throw this.fail("merge_key", line);
    }
    const rule = scalarRule(value);
    if (rule !== null) {
      throw this.fail(rule, line);
    }
    if (isKey && NON_STRING_KEY.test(value)) {
      throw this.fail("non_string_key", line);
    }
  }

  private finishPending(): void {
    if (this.pending !== null) {
      const [value, line] = this.pending;
      const multi = this.pendingMulti;
      this.pending = null;
      this.pendingMulti = false;
      if (!multi) {
        this.checkPlain(value, line, false);
      }
    }
  }

  // -- lines ---------------------------------------------------------------------------------------------------

  private line(line: Line): void {
    const head = text(line, 0, 3);
    if ((head === "---" || head === "...") && spaceOrEnd(line, 3)) {
      if (this.blockScalar !== null) {
        this.blockScalar = null;
      }
      if (head === "---" && !this.contentSeen && !this.docStartSeen && this.quote === null) {
        this.docStartSeen = true;
        this.scan(line, 3);
        return;
      }
      this.finishPending();
      throw this.fail("second_document");
    }
    if (line[0] === "%" && this.quote === null && this.blockScalar === null) {
      this.finishPending();
      throw this.fail("directive");
    }
    let col = 0;
    while (col < line.length && line[col] === " ") {
      col += 1;
    }
    const blank = col === line.length;
    if (this.blockScalar !== null) {
      if (blank || col > this.blockScalar) {
        return; // the block scalar's body: text, tabs included
      }
      this.blockScalar = null;
    }
    if (blank) {
      return;
    }
    if (this.flow.length > 0 && col <= this.flowIndent) {
      throw this.fail("flow_line_not_indented");
    }
    if (this.quote !== null) {
      let end = this.quotedEnd(line, 0, this.quote);
      if (end === null) {
        return;
      }
      this.quote = null;
      end = this.afterScalar(line, end, null);
      if (end >= 0) {
        this.scan(line, end);
      }
      return;
    }
    if (this.pending !== null) {
      if (line[col] !== "#" && col > this.pending[2]) {
        this.pendingMulti = true;
        this.continuation(line, col);
        return;
      }
      this.finishPending();
    }
    if (this.flow.length === 0) {
      while (this.stack.length > 0 && (this.stack.at(-1) as [number, BlockKind])[0] > col) {
        this.stack.pop();
      }
    }
    this.scan(line, col);
  }

  /** A block plain scalar continued on a more indented line: text up to a comment. */
  private continuation(line: Line, from: number): void {
    for (let i = from; i < line.length; i += 1) {
      const ch = line[i];
      if (ch === "\t") {
        throw this.fail("tab");
      }
      if (ch === "#" && line[i - 1] === " ") {
        this.comment(line, i);
        this.finishPending();
        return;
      }
    }
  }

  private comment(line: Line, i: number): void {
    if (line.indexOf("\t", i) !== -1) {
      throw this.fail("tab");
    }
  }

  private skipSpaces(line: Line, from: number): number {
    let i = from;
    while (i < line.length && (line[i] === " " || line[i] === "\t")) {
      if (line[i] === "\t") {
        throw this.fail("tab");
      }
      i += 1;
    }
    return i;
  }

  /** The index after the closing `quote` from `i` on, or `null` when the scalar runs past the line. */
  private quotedEnd(line: Line, from: number, quote: string): number | null {
    let i = from;
    while (i < line.length) {
      const ch = line[i];
      if (quote === '"' && ch === "\\") {
        i += 2;
        continue;
      }
      if (ch === quote) {
        if (quote === "'" && line[i + 1] === "'") {
          i += 2;
          continue;
        }
        return i + 1;
      }
      i += 1;
    }
    return null;
  }

  /** Read tokens from `i` to the end of the line; every position reached after spaces is a token start. */
  private scan(line: Line, from: number): void {
    let i = from;
    for (;;) {
      i = this.skipSpaces(line, i);
      if (i >= line.length) {
        return;
      }
      const ch = line[i] as string;
      const nxt = i + 1 < line.length ? (line[i + 1] as string) : "";
      const inFlow = this.flow.length > 0;
      if (ch === "#") {
        this.comment(line, i);
        return;
      }
      if (ch === "&") {
        throw this.fail("anchor");
      }
      if (ch === "*") {
        throw this.fail("alias");
      }
      if (ch === "!") {
        throw this.fail("tag");
      }
      this.contentSeen = true;
      if (ch === "?") {
        if (spaceOrEnd(line, i + 1) || (inFlow && nxt !== "" && FLOW_INDICATORS.includes(nxt))) {
          throw this.fail("explicit_key");
        }
        if (inFlow) {
          throw this.fail("flow_scalar_indicator_start");
        }
      }
      if (ch === ":") {
        if (spaceOrEnd(line, i + 1) || (inFlow && nxt !== "" && FLOW_INDICATORS.includes(nxt))) {
          throw this.fail("non_string_key"); // a value with no key: the key is empty
        }
        if (inFlow) {
          throw this.fail("flow_scalar_indicator_start");
        }
      }
      if (inFlow) {
        if (ch === "[" || ch === "{") {
          this.openFlow(ch);
          i += 1;
          continue;
        }
        if (ch === "]" || ch === "}") {
          this.flow.pop();
          i = this.afterScalar(line, i + 1, null, true);
          if (i < 0) {
            return;
          }
          continue;
        }
        if (ch === ",") {
          i += 1;
          continue;
        }
      } else {
        if (ch === "-" && spaceOrEnd(line, i + 1)) {
          this.blockEntry(i, "seq");
          i += 1;
          continue;
        }
        if (ch === "|" || ch === ">") {
          this.blockScalar = this.owner();
          this.comment(line, i);
          return;
        }
        if (ch === "[" || ch === "{") {
          this.flowIndent = this.owner();
          this.openFlow(ch);
          i += 1;
          continue;
        }
      }
      if (ch === "'" || ch === '"') {
        const end = this.quotedEnd(line, i + 1, ch);
        if (end === null) {
          this.quote = ch;
          return;
        }
        i = this.afterScalar(line, end, i);
        if (i < 0) {
          return;
        }
        continue;
      }
      i = this.plain(line, i);
      if (i < 0) {
        return;
      }
    }
  }

  private openFlow(ch: string): void {
    this.flow.push(ch);
    this.checkDepth();
  }

  /** After a quoted scalar or a closed flow collection: a `:` makes it a key. Returns where to go on, or -1. */
  private afterScalar(line: Line, i: number, keyCol: number | null, collection = false): number {
    const j = this.skipSpaces(line, i);
    if (j < line.length && line[j] === ":" && (this.flow.length > 0 || spaceOrEnd(line, j + 1))) {
      if (collection) {
        throw this.fail("non_string_key");
      }
      if (this.flow.length === 0 && keyCol !== null) {
        this.blockEntry(keyCol, "map");
      }
      return j + 1;
    }
    if (j < line.length && line[j] === "#") {
      this.comment(line, j);
      return -1;
    }
    return j;
  }

  /** A plain scalar from `start`: checked when it ends (a key at once). Returns where to go on, or -1. */
  private plain(line: Line, start: number): number {
    let i = start;
    const n = line.length;
    const inFlow = this.flow.length > 0;
    while (i < n) {
      const ch = line[i] as string;
      if (ch === "\t") {
        throw this.fail("tab");
      }
      if (ch === "#" && line[i - 1] === " ") {
        break;
      }
      if (
        ch === ":" &&
        (spaceOrEnd(line, i + 1) || (inFlow && FLOW_INDICATORS.includes(line[i + 1] as string)))
      ) {
        this.checkPlain(rstripSpaces(text(line, start, i)), this.ln, true);
        if (!inFlow) {
          this.blockEntry(start, "map");
        }
        return i + 1;
      }
      if (inFlow && ",?[]{}".includes(ch)) {
        break;
      }
      i += 1;
    }
    const value = rstripSpaces(text(line, start, i));
    if (inFlow) {
      this.checkPlain(value, this.ln, false);
      return i;
    }
    if (i < n) {
      // ended by a comment
      this.checkPlain(value, this.ln, false);
      this.comment(line, i);
      return -1;
    }
    this.pending = [value, this.ln, this.owner()];
    this.pendingMulti = false;
    return -1;
  }
}

/** The first construct in `source` outside the SPEC 8.7 YAML profile, or `null` (repeated keys excepted: the
 * loader checks them on the composed tree). */
export function profileViolation(source: string): ProfileViolation | null {
  return new Scanner(source).run();
}
