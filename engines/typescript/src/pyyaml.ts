/**
 * Emit YAML byte for byte as Python's `yaml.dump(value, Dumper=_ProfileDumper, sort_keys=False)` does: PyYAML's
 * `SafeDumper` written inside the SPEC 8.7 YAML profile (18.184u), with no `&idNNN` anchor for a value met twice
 * (each occurrence written out in full) and text a YAML 1.1 reader would not keep as text ({@link scalarRule})
 * single-quoted.
 *
 * The Python engine writes a derived applicability profile with PyYAML's default settings (block
 * style, indent 2, width 80, `allow_unicode=False`, `\n` line breaks, no `---`). This module ports
 * the parts of PyYAML 6 that such a value reaches: the safe representer (one node per occurrence, as
 * `ignore_aliases` keeps every value from being shared), the implicit resolver that decides whether a string may
 * stay plain, and
 * the emitter's scalar analysis, style choice and plain, single-quoted and double-quoted writers
 * with their folding at column 80. Text is handled by code point, as Python strings are.
 *
 * Supported values: plain objects with string keys (in `Object.keys` order), arrays, strings, safe
 * integers, booleans and `null`. Anything else throws. No network, no learned component.
 */

import { scalarRule } from "./yamlProfile";

const BEST_INDENT = 2;
const BEST_WIDTH = 80;

type Tag = "str" | "int" | "bool" | "null";

interface ScalarNode {
  kind: "scalar";
  tag: Tag;
  value: string;
  /** `'` when the representer asks for single quotes (`_ProfileDumper.represent_str`). */
  style?: "'";
}

interface SequenceNode {
  kind: "sequence";
  items: YamlNode[];
}

interface MappingNode {
  kind: "mapping";
  pairs: [ScalarNode, YamlNode][];
}

type YamlNode = ScalarNode | SequenceNode | MappingNode;

// resolver.py's implicit resolvers, each keyed by the first characters it may match. A `\n?` before
// `$` reproduces Python's `$`, which also matches before one trailing newline.
const IMPLICIT_RESOLVERS: [Tag | "other", RegExp, string[]][] = [
  [
    "bool",
    /^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)\n?$/,
    [..."yYnNtTfFoO"],
  ],
  [
    "other", // float
    /^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))\n?$/,
    [..."-+0123456789."],
  ],
  [
    "int",
    /^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)\n?$/,
    [..."-+0123456789"],
  ],
  ["other", /^(?:<<)\n?$/, ["<"]], // merge
  ["null", /^(?:~|null|Null|NULL|)\n?$/, ["~", "n", "N", ""]],
  [
    "other", // timestamp
    /^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]|[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9](?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)\n?$/,
    [..."0123456789"],
  ],
  ["other", /^(?:=)\n?$/, ["="]], // value
  ["other", /^(?:!|&|\*)\n?$/, [..."!&*"]], // yaml
];

/** Whether resolving `value` as a plain scalar gives `tag:yaml.org,2002:str` (no resolver matches). */
function resolvesToStr(value: string): boolean {
  const first = value === "" ? "" : String.fromCodePoint(value.codePointAt(0) as number);
  return !IMPLICIT_RESOLVERS.some(([, re, firsts]) => firsts.includes(first) && re.test(value));
}

/** `_ProfileDumper.represent_str`: text a YAML 1.1 reader would not keep as text is single-quoted. */
function strNode(value: string): ScalarNode {
  return scalarRule(value) === null
    ? { kind: "scalar", tag: "str", value }
    : { kind: "scalar", tag: "str", value, style: "'" };
}

/** The safe representer with `ignore_aliases`: one node per occurrence, so a value met twice is written twice.
 * `open` holds the lists and objects being represented; meeting one again inside itself (a cycle, which Python
 * recurses on until its RecursionError) is refused. */
function represent(value: unknown, open: Set<object>): YamlNode {
  if (value === null) return { kind: "scalar", tag: "null", value: "null" };
  if (typeof value === "string") return strNode(value);
  if (typeof value === "boolean") return { kind: "scalar", tag: "bool", value: String(value) };
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new TypeError(`unsupported YAML number: ${value}`);
    return { kind: "scalar", tag: "int", value: String(value) };
  }
  if (typeof value !== "object") throw new TypeError(`unsupported YAML value: ${typeof value}`);
  if (open.has(value)) throw new TypeError("unsupported YAML value: a cycle");
  if (Array.isArray(value)) {
    open.add(value);
    const node: SequenceNode = {
      kind: "sequence",
      items: value.map((item) => represent(item, open)),
    };
    open.delete(value);
    return node;
  }
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) {
    throw new TypeError("unsupported YAML value: not a plain object");
  }
  open.add(value);
  const node: MappingNode = {
    kind: "mapping",
    pairs: Object.entries(value).map(([key, item]) => [strNode(key), represent(item, open)]),
  };
  open.delete(value);
  return node;
}

const isSpaceOrBreakOrNul = (ch: number): boolean =>
  ch === 0 ||
  ch === 0x20 ||
  ch === 0x09 ||
  ch === 0x0d ||
  ch === 0x0a ||
  ch === 0x85 ||
  ch === 0x2028 ||
  ch === 0x2029;
const isBreak = (ch: number): boolean =>
  ch === 0x0a || ch === 0x85 || ch === 0x2028 || ch === 0x2029;

interface Analysis {
  text: number[];
  empty: boolean;
  multiline: boolean;
  allowBlockPlain: boolean;
  allowSingleQuoted: boolean;
}

/** Emitter.analyze_scalar with `allow_unicode=False`. */
function analyzeScalar(scalar: string): Analysis {
  const text = Array.from(scalar, (ch) => ch.codePointAt(0) as number);
  if (text.length === 0) {
    return {
      text,
      empty: true,
      multiline: false,
      allowBlockPlain: true,
      allowSingleQuoted: true,
    };
  }
  let blockIndicators = false;
  let lineBreaks = false;
  let specialCharacters = false;
  let leadingSpace = false;
  let leadingBreak = false;
  let trailingSpace = false;
  let trailingBreak = false;
  let breakSpace = false;
  let spaceBreak = false;
  if (scalar.startsWith("---") || scalar.startsWith("...")) {
    blockIndicators = true;
  }
  let precededByWhitespace = true;
  let followedByWhitespace = text.length === 1 || isSpaceOrBreakOrNul(text[1] as number);
  let previousSpace = false;
  let previousBreak = false;
  for (let index = 0; index < text.length; ) {
    const ch = text[index] as number;
    const c = String.fromCodePoint(ch);
    if (index === 0) {
      if ("#,[]{}&*!|>'\"%@`".includes(c)) blockIndicators = true;
      if ((c === "?" || c === ":" || c === "-") && followedByWhitespace) blockIndicators = true;
    } else {
      if (c === ":" && followedByWhitespace) blockIndicators = true;
      if (c === "#" && precededByWhitespace) blockIndicators = true;
    }
    if (isBreak(ch)) lineBreaks = true;
    if (!(ch === 0x0a || (ch >= 0x20 && ch <= 0x7e))) {
      // Printable non-ASCII is still special, since allow_unicode is off; so is everything else.
      specialCharacters = true;
    }
    if (ch === 0x20) {
      if (index === 0) leadingSpace = true;
      if (index === text.length - 1) trailingSpace = true;
      if (previousBreak) breakSpace = true;
      previousSpace = true;
      previousBreak = false;
    } else if (isBreak(ch)) {
      if (index === 0) leadingBreak = true;
      if (index === text.length - 1) trailingBreak = true;
      if (previousSpace) spaceBreak = true;
      previousSpace = false;
      previousBreak = true;
    } else {
      previousSpace = false;
      previousBreak = false;
    }
    index += 1;
    precededByWhitespace = isSpaceOrBreakOrNul(ch);
    followedByWhitespace =
      index + 1 >= text.length || isSpaceOrBreakOrNul(text[index + 1] as number);
  }
  let allowBlockPlain = true;
  let allowSingleQuoted = true;
  if (leadingSpace || leadingBreak || trailingSpace || trailingBreak) {
    allowBlockPlain = false;
  }
  if (breakSpace) allowBlockPlain = allowSingleQuoted = false;
  if (spaceBreak || specialCharacters) {
    allowBlockPlain = allowSingleQuoted = false;
  }
  if (lineBreaks || blockIndicators) allowBlockPlain = false;
  return {
    text,
    empty: false,
    multiline: lineBreaks,
    allowBlockPlain,
    allowSingleQuoted,
  };
}

const ESCAPE_REPLACEMENTS = new Map<number, string>([
  [0x00, "0"],
  [0x07, "a"],
  [0x08, "b"],
  [0x09, "t"],
  [0x0a, "n"],
  [0x0b, "v"],
  [0x0c, "f"],
  [0x0d, "r"],
  [0x1b, "e"],
  [0x22, '"'],
  [0x5c, "\\"],
  [0x85, "N"],
  [0xa0, "_"],
  [0x2028, "L"],
  [0x2029, "P"],
]);

const hex = (code: number, width: number): string =>
  code.toString(16).toUpperCase().padStart(width, "0");
const slice = (text: number[], start: number, end: number): string =>
  String.fromCodePoint(...text.slice(start, end));

interface Context {
  root?: boolean;
  mapping?: boolean;
  simpleKey?: boolean;
}

/** The block-style subset of PyYAML's Emitter, driven by a node walk instead of an event queue. */
class Emitter {
  private readonly out: string[] = [];
  private readonly indents: (number | null)[] = [];
  private indent: number | null = null;
  private column = 0;
  private whitespace = true;
  private indention = true;
  private openEnded = false;

  emitDocument(root: YamlNode): string {
    this.expectNode(root, { root: true });
    this.writeIndent();
    if (this.openEnded) {
      this.writeIndicator("...", true);
      this.writeIndent();
    }
    return this.out.join("");
  }

  private expectNode(node: YamlNode, ctx: Context): void {
    if (node.kind === "scalar") {
      this.expectScalar(node, analyzeScalar(node.value), ctx);
    } else if (node.kind === "sequence") {
      if (node.items.length === 0) this.writeEmptyFlow("[", "]");
      else this.expectBlockSequence(node, ctx);
    } else if (node.pairs.length === 0) {
      this.writeEmptyFlow("{", "}");
    } else {
      this.expectBlockMapping(node);
    }
  }

  private writeEmptyFlow(open: string, close: string): void {
    this.writeIndicator(open, true, true);
    this.increaseIndent(true);
    this.indent = this.indents.pop() ?? null;
    this.writeIndicator(close, false);
  }

  private expectBlockSequence(node: SequenceNode, ctx: Context): void {
    this.increaseIndent(false, Boolean(ctx.mapping) && !this.indention);
    for (const item of node.items) {
      this.writeIndent();
      this.writeIndicator("-", true, false, true);
      this.expectNode(item, {});
    }
    this.indent = this.indents.pop() ?? null;
  }

  private expectBlockMapping(node: MappingNode): void {
    this.increaseIndent(false);
    for (const [key, value] of node.pairs) {
      this.writeIndent();
      const analysis = analyzeScalar(key.value);
      // `_ProfileDumper.check_simple_key`: an explicit `? ` key is outside the SPEC 8.7 profile, so a one-line
      // key is written plain or quoted up to 1000 characters; an empty or multiline one still goes after `?`.
      if (analysis.text.length < 1000 && !analysis.empty && !analysis.multiline) {
        this.expectScalar(key, analysis, { mapping: true, simpleKey: true });
        this.writeIndicator(":", false);
      } else {
        this.writeIndicator("?", true, false, true);
        this.expectScalar(key, analysis, { mapping: true });
        this.writeIndent();
        this.writeIndicator(":", true, false, true);
      }
      this.expectNode(value, { mapping: true });
    }
    this.indent = this.indents.pop() ?? null;
  }

  private expectScalar(node: ScalarNode, analysis: Analysis, ctx: Context): void {
    const style = this.chooseScalarStyle(node, analysis, ctx);
    // process_tag: a str scalar is implicit in any quoted style; other tags only when plain.
    if (style !== "" && node.tag !== "str") {
      throw new TypeError(`a ${node.tag} scalar cannot be written plain: ${node.value}`);
    }
    this.increaseIndent(true);
    const split = !ctx.simpleKey;
    if (style === '"') this.writeDoubleQuoted(analysis.text, split);
    else if (style === "'") this.writeSingleQuoted(analysis.text, split);
    else this.writePlain(analysis.text, split, Boolean(ctx.root));
    this.indent = this.indents.pop() ?? null;
  }

  private chooseScalarStyle(node: ScalarNode, analysis: Analysis, ctx: Context): "" | "'" | '"' {
    const implicitPlain = node.tag !== "str" || resolvesToStr(node.value);
    if (
      node.style === undefined &&
      implicitPlain &&
      !(ctx.simpleKey && (analysis.empty || analysis.multiline)) &&
      analysis.allowBlockPlain
    ) {
      return "";
    }
    if (analysis.allowSingleQuoted && !(ctx.simpleKey && analysis.multiline)) return "'";
    return '"';
  }

  private increaseIndent(flow: boolean, indentless = false): void {
    this.indents.push(this.indent);
    if (this.indent === null) this.indent = flow ? BEST_INDENT : 0;
    else if (!indentless) this.indent += BEST_INDENT;
  }

  private write(data: string, width: number): void {
    this.column += width;
    this.out.push(data);
  }

  private writeIndicator(
    indicator: string,
    needWhitespace: boolean,
    whitespace = false,
    indention = false,
  ): void {
    const data = this.whitespace || !needWhitespace ? indicator : ` ${indicator}`;
    this.whitespace = whitespace;
    this.indention = this.indention && indention;
    this.openEnded = false;
    this.write(data, data.length);
  }

  private writeIndent(): void {
    const indent = this.indent ?? 0;
    if (!this.indention || this.column > indent || (this.column === indent && !this.whitespace)) {
      this.writeLineBreak();
    }
    if (this.column < indent) {
      this.whitespace = true;
      this.write(" ".repeat(indent - this.column), indent - this.column);
    }
  }

  private writeLineBreak(data = "\n"): void {
    this.whitespace = true;
    this.indention = true;
    this.column = 0;
    this.out.push(data);
  }

  private writeBreaks(text: number[], start: number, end: number): void {
    if (text[start] === 0x0a) this.writeLineBreak();
    for (let i = start; i < end; i++) {
      const br = text[i] as number;
      this.writeLineBreak(br === 0x0a ? "\n" : String.fromCodePoint(br));
    }
    this.writeIndent();
  }

  private writeSingleQuoted(text: number[], split: boolean): void {
    this.writeIndicator("'", true);
    let spaces = false;
    let breaks = false;
    let start = 0;
    for (let end = 0; end <= text.length; end++) {
      const ch = end < text.length ? (text[end] as number) : null;
      if (spaces) {
        if (ch !== 0x20) {
          if (
            start + 1 === end &&
            this.column > BEST_WIDTH &&
            split &&
            start !== 0 &&
            end !== text.length
          ) {
            this.writeIndent();
          } else {
            this.write(slice(text, start, end), end - start);
          }
          start = end;
        }
      } else if (breaks) {
        if (ch === null || !isBreak(ch)) {
          this.writeBreaks(text, start, end);
          start = end;
        }
      } else if (ch === null || ch === 0x20 || isBreak(ch) || ch === 0x27) {
        if (start < end) {
          this.write(slice(text, start, end), end - start);
          start = end;
        }
      }
      if (ch === 0x27) {
        this.write("''", 2);
        start = end + 1;
      }
      if (ch !== null) {
        spaces = ch === 0x20;
        breaks = isBreak(ch);
      }
    }
    this.writeIndicator("'", false);
  }

  private writeDoubleQuoted(text: number[], split: boolean): void {
    this.writeIndicator('"', true);
    let start = 0;
    for (let end = 0; end <= text.length; end++) {
      const ch = end < text.length ? (text[end] as number) : null;
      if (
        ch === null ||
        ch === 0x22 ||
        ch === 0x5c ||
        ch === 0x85 ||
        ch === 0x2028 ||
        ch === 0x2029 ||
        ch === 0xfeff ||
        !(ch >= 0x20 && ch <= 0x7e)
      ) {
        if (start < end) {
          this.write(slice(text, start, end), end - start);
          start = end;
        }
        if (ch !== null) {
          const short = ESCAPE_REPLACEMENTS.get(ch);
          let data: string;
          if (short !== undefined) data = `\\${short}`;
          else if (ch <= 0xff) data = `\\x${hex(ch, 2)}`;
          else if (ch <= 0xffff) data = `\\u${hex(ch, 4)}`;
          else data = `\\U${hex(ch, 8)}`;
          this.write(data, data.length);
          start = end + 1;
        }
      }
      if (
        end > 0 &&
        end < text.length - 1 &&
        (ch === 0x20 || start >= end) &&
        this.column + (end - start) > BEST_WIDTH &&
        split
      ) {
        // Fold: end the line with `\`, and escape a space that would begin the next one.
        const data = `${slice(text, start, end)}\\`;
        const width = Math.max(end - start, 0) + 1;
        if (start < end) start = end;
        this.write(data, width);
        this.writeIndent();
        this.whitespace = false;
        this.indention = false;
        if (text[start] === 0x20) this.write("\\", 1);
      }
    }
    this.writeIndicator('"', false);
  }

  private writePlain(text: number[], split: boolean, root: boolean): void {
    if (root) this.openEnded = true;
    if (text.length === 0) return;
    if (!this.whitespace) this.write(" ", 1);
    this.whitespace = false;
    this.indention = false;
    let spaces = false;
    let breaks = false;
    let start = 0;
    for (let end = 0; end <= text.length; end++) {
      const ch = end < text.length ? (text[end] as number) : null;
      if (spaces) {
        if (ch !== 0x20) {
          if (start + 1 === end && this.column > BEST_WIDTH && split) {
            this.writeIndent();
            this.whitespace = false;
            this.indention = false;
          } else {
            this.write(slice(text, start, end), end - start);
          }
          start = end;
        }
      } else if (breaks) {
        if (ch === null || !isBreak(ch)) {
          this.writeBreaks(text, start, end);
          this.whitespace = false;
          this.indention = false;
          start = end;
        }
      } else if (ch === null || ch === 0x20 || isBreak(ch)) {
        this.write(slice(text, start, end), end - start);
        start = end;
      }
      if (ch !== null) {
        spaces = ch === 0x20;
        breaks = isBreak(ch);
      }
    }
  }
}

/** The text `yaml.dump(value, Dumper=_ProfileDumper, sort_keys=False)` returns for `value`. */
export function safeDump(value: unknown): string {
  return new Emitter().emitDocument(represent(value, new Set()));
}
