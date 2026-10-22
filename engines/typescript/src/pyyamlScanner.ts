/**
 * The PyYAML scanner rules js-yaml does not keep (18.184p): where a tab may sit, a `?` ending a plain scalar in a
 * flow collection, and an empty flow key. {@link scannerProblem} walks the text as PyYAML's scanner does (its indent
 * stack, simple keys, plain, quoted and block scalar extents, directives, anchors, tags and aliases) and checks each
 * top-level flow collection against PyYAML's flow grammar. It reports only a refusal PyYAML makes; a document PyYAML
 * refuses for another reason may pass, and the composer refuses it. Java's `PyyamlScanner` is the same walk.
 * {@link pyyamlScan} also returns the text js-yaml should read: PyYAML takes every `?` and `:` that starts a token in
 * a flow collection as the key or value indicator (`{?a: 1}` is `{a: 1}`, `{&x :1}` is `{None: 1}`), js-yaml only
 * one followed by a space, so it gets that space. SnakeYAML reads them as PyYAML does, so Java needs no such text.
 */

import { pyRepr } from "./util";

const codes = (chars: string): Set<number> =>
  new Set(Array.from(chars, (ch) => ch.codePointAt(0) ?? 0));

const BREAKS = "\r\n\u0085  ";
const BREAK = codes(BREAKS);
const END = codes(`\0 \t${BREAKS}`);
const END_IN_FLOW = codes(`\0 \t${BREAKS},[]{}`);
const NOT_BREAK_END = codes(`\0${BREAKS}`);
const FLOW_PLAIN_STOP = codes(",?[]{}");

const at = (ch: string): number => ch.codePointAt(0) ?? 0;
const SPACE = at(" ");
const TAB = at("\t");
const HASH = at("#");

class Problem extends Error {}

interface Tok {
  readonly kind: string;
  readonly line: number;
}

/** `null` when PyYAML's scanner and flow parser get past every tab, `?`, `:` and `-` in `text`, else PyYAML's
 * reason. */
export function scannerProblem(text: string): string | null {
  return scannerProblemOf(new Scanner(text));
}

/** {@link scannerProblem}, and when it is `null`, `text` with a space after each flow-collection `?` or `:`
 * indicator that has none, so js-yaml reads it as PyYAML's indicator. */
export function pyyamlScan(text: string): { problem: string | null; text: string } {
  const scanner = new Scanner(text);
  const problem = scannerProblemOf(scanner);
  return { problem, text: problem === null ? scanner.indicatorText() : text };
}

function scannerProblemOf(scanner: Scanner): string | null {
  try {
    scanner.run();
  } catch (error) {
    if (error instanceof Problem) {
      return error.message;
    }
    throw error;
  }
  return null;
}

const nameChar = (c: number): boolean => c < 128 && /[A-Za-z0-9_-]/.test(String.fromCodePoint(c));

class Scanner {
  private readonly t: number[];
  private readonly n: number;
  private i: number;
  private lineStart: number;
  private line = 0;
  private readonly indents: number[] = [];
  private indent = -1;
  private depth = 0;
  private flow: Tok[] = [];
  private allowKey = true;
  private keyLine = -1;
  private keyCol = 0;
  private readonly flowIndicators: number[] = [];

  constructor(private readonly text: string) {
    this.t = Array.from(text, (ch) => ch.codePointAt(0) ?? 0);
    this.n = this.t.length;
    this.i = this.n > 0 && this.t[0] === 0xfeff ? 1 : 0;
    this.lineStart = this.i;
  }

  /** The text with a space after each flow `?` or `:` indicator {@link token} found with none. */
  indicatorText(): string {
    let out = "";
    let from = 0;
    let unit = 0;
    let cp = 0;
    for (const k of this.flowIndicators) {
      // flowIndicators holds code-point indices; the text is sliced by UTF-16 offsets.
      for (; cp <= k; cp += 1) {
        unit += (this.t[cp] ?? 0) > 0xffff ? 2 : 1;
      }
      out += `${this.text.slice(from, unit)} `;
      from = unit;
    }
    return from === 0 ? this.text : out + this.text.slice(from);
  }

  private peek(k = 0): number {
    const j = this.i + k;
    return j < this.n ? (this.t[j] ?? 0) : 0;
  }

  private col(): number {
    return this.i - this.lineStart;
  }

  private startsWith(s: string): boolean {
    return Array.from(s).every((ch, k) => this.t[this.i + k] === at(ch));
  }

  private documentMarker(): boolean {
    return (
      this.col() === 0 &&
      (this.startsWith("---") || this.startsWith("...")) &&
      END.has(this.peek(3))
    );
  }

  private newline(): void {
    this.i += this.peek() === at("\r") && this.peek(1) === at("\n") ? 2 : 1;
    this.lineStart = this.i;
    this.line += 1;
    if (this.depth === 0) {
      this.allowKey = true;
    }
  }

  private tab(): never {
    throw new Problem(`found character '\\t' that cannot start any token (line ${this.line + 1})`);
  }

  private addIndent(column: number): void {
    if (this.indent < column) {
      this.indents.push(this.indent);
      this.indent = column;
    }
  }

  private emit(kind: string, line = this.line): void {
    this.flow.push({ kind, line });
  }

  private nodeStart(): void {
    if (this.depth === 0 && this.allowKey) {
      this.keyLine = this.line;
      this.keyCol = this.col();
    }
  }

  run(): void {
    for (;;) {
      for (;;) {
        const c = this.peek();
        if (c === SPACE) {
          this.i += 1;
        } else if (c === HASH) {
          while (!NOT_BREAK_END.has(this.peek())) {
            this.i += 1;
          }
        } else if (BREAK.has(c)) {
          this.newline();
        } else {
          break;
        }
      }
      if (this.i >= this.n) {
        break;
      }
      if (this.keyLine >= 0 && this.keyLine !== this.line) {
        this.keyLine = -1;
      }
      if (this.depth === 0) {
        while (this.indent > this.col()) {
          this.indent = this.indents.pop() ?? -1;
        }
      }
      this.token();
    }
    if (this.depth > 0) {
      throw new Problem("a flow collection is not closed");
    }
  }

  private token(): void {
    const c = this.peek();
    const ch = String.fromCodePoint(c);
    const nextEnds = END.has(this.peek(1));
    if (c === TAB) {
      this.tab();
    }
    if (ch === "%" && this.col() === 0) {
      this.directive();
      return;
    }
    if (this.documentMarker()) {
      if (this.depth > 0) {
        throw new Problem("a document marker inside a flow collection");
      }
      this.i += 3;
      this.indents.length = 0;
      this.indent = -1;
      this.keyLine = -1;
      return;
    }
    if (ch === "[" || ch === "{") {
      this.nodeStart();
      this.depth += 1;
      this.emit(ch);
      this.i += 1;
      this.allowKey = true;
      return;
    }
    if (ch === "]" || ch === "}") {
      this.emit(ch);
      this.i += 1;
      this.allowKey = false;
      if (this.depth > 0) {
        this.depth -= 1;
        if (this.depth === 0) {
          const done = this.flow;
          this.flow = [];
          checkFlow(done);
        }
      }
      return;
    }
    if (ch === "," && this.depth > 0) {
      this.emit(",");
      this.i += 1;
      this.allowKey = true;
      return;
    }
    if (ch === "-" && nextEnds) {
      if (this.depth > 0) {
        throw new Problem("a block entry inside a flow collection");
      }
      this.addIndent(this.col());
      this.i += 1;
      this.allowKey = true;
      return;
    }
    if (ch === "?" && (this.depth > 0 || nextEnds)) {
      if (this.depth > 0) {
        this.emit("?");
        this.allowKey = false;
        if (!nextEnds) {
          this.flowIndicators.push(this.i);
        }
      } else {
        this.addIndent(this.col());
        this.allowKey = true;
      }
      this.i += 1;
      return;
    }
    if (ch === ":" && (this.depth > 0 || nextEnds)) {
      if (this.depth > 0) {
        this.emit(":");
        if (!nextEnds) {
          this.flowIndicators.push(this.i);
        }
      } else {
        if (this.keyLine >= 0) {
          this.addIndent(this.keyCol);
          this.keyLine = -1;
        } else {
          this.addIndent(this.col());
        }
        this.allowKey = true;
      }
      this.i += 1;
      return;
    }
    if (ch === "&" || ch === "!" || ch === "*") {
      this.nodeStart();
      this.i += 1;
      if (ch === "!") {
        while (!END.has(this.peek())) {
          this.i += 1;
        }
      } else {
        while (nameChar(this.peek())) {
          this.i += 1;
        }
      }
      if (this.depth > 0) {
        this.emit(ch === "&" ? "anchor" : ch === "!" ? "tag" : "alias");
      }
      this.allowKey = false;
      return;
    }
    if ((ch === "|" || ch === ">") && this.depth === 0) {
      this.allowKey = true;
      this.blockScalar();
      return;
    }
    this.nodeStart();
    this.allowKey = false;
    const atLine = this.line;
    if (ch === "'" || ch === '"') {
      this.quoted(ch);
      this.allowKey = false;
    } else {
      this.plain(); // a line break inside allows a simple key again, as scan_plain_spaces does
    }
    if (this.depth > 0) {
      this.emit("node", atLine);
    }
  }

  /** scan_directive: a name, then for YAML and TAG values and spaces (a tab refused); any other directive's line is
   * skipped whole. */
  private directive(): void {
    this.i += 1;
    const start = this.i;
    while (nameChar(this.peek())) {
      this.i += 1;
    }
    if (this.peek() === TAB) {
      this.tab();
    }
    // Not String.fromCodePoint(...): spreading a long name would overflow the call stack.
    const name = this.t
      .slice(start, this.i)
      .map((c) => String.fromCodePoint(c))
      .join("");
    const known = name === "YAML" || name === "TAG";
    while (!NOT_BREAK_END.has(this.peek())) {
      if (known && this.peek() === TAB) {
        this.tab();
      }
      if (known && this.peek() === HASH && this.t[this.i - 1] === SPACE) {
        break;
      }
      this.i += 1;
    }
    while (!NOT_BREAK_END.has(this.peek())) {
      this.i += 1;
    }
  }

  private quoted(q: string): void {
    const quote = at(q);
    this.i += 1;
    for (;;) {
      const c = this.peek();
      if (this.i >= this.n) {
        throw new Problem("a quoted scalar is not closed");
      }
      if (q === "'" && c === quote) {
        if (this.peek(1) === quote) {
          this.i += 2;
          continue;
        }
        this.i += 1;
        return;
      }
      if (q === '"' && c === at("\\")) {
        this.i += 1;
        if (BREAK.has(this.peek())) {
          this.newline();
        } else {
          this.i += 1;
        }
        continue;
      }
      if (q === '"' && c === quote) {
        this.i += 1;
        return;
      }
      if (BREAK.has(c)) {
        this.newline();
        continue;
      }
      this.i += 1;
    }
  }

  private plain(): void {
    const inFlow = this.depth > 0;
    const minIndent = this.indent + 1;
    for (;;) {
      if (this.peek() === HASH) {
        return;
      }
      let length = 0;
      for (;;) {
        const c = this.peek(length);
        if (
          END.has(c) ||
          (c === at(":") && (inFlow ? END_IN_FLOW : END).has(this.peek(length + 1))) ||
          (inFlow && FLOW_PLAIN_STOP.has(c))
        ) {
          break;
        }
        length += 1;
      }
      if (length === 0) {
        return;
      }
      this.i += length;
      const markI = this.i;
      const markLineStart = this.lineStart;
      const markLine = this.line;
      while (this.peek() === SPACE) {
        this.i += 1;
      }
      if (BREAK.has(this.peek())) {
        this.newline();
        for (;;) {
          if (this.documentMarker()) {
            this.i = markI;
            this.lineStart = markLineStart;
            this.line = markLine;
            return;
          }
          if (this.peek() === SPACE) {
            this.i += 1;
          } else if (BREAK.has(this.peek())) {
            this.newline();
          } else {
            break;
          }
        }
      } else if (this.i === markI) {
        return;
      }
      if (this.peek() === HASH || this.i >= this.n || (!inFlow && this.col() < minIndent)) {
        return;
      }
    }
  }

  private blockScalar(): void {
    const minIndent = Math.max(this.indent + 1, 1);
    this.i += 1;
    let increment = -1;
    for (let k = 0; k < 2; k += 1) {
      const c = this.peek();
      if (c === at("+") || c === at("-")) {
        this.i += 1;
      } else if (c >= at("1") && c <= at("9") && increment < 0) {
        increment = c - at("0");
        this.i += 1;
      }
    }
    while (this.peek() === SPACE) {
      this.i += 1;
    }
    if (this.peek() === HASH) {
      while (!NOT_BREAK_END.has(this.peek())) {
        this.i += 1;
      }
    }
    if (!NOT_BREAK_END.has(this.peek())) {
      if (this.peek() === TAB) {
        this.tab();
      }
      throw new Problem("expected a comment or a line break");
    }
    if (this.i >= this.n) {
      return;
    }
    this.newline();
    let blockIndent: number;
    if (increment < 0) {
      let maxIndent = 0;
      while ((this.peek() === SPACE || BREAK.has(this.peek())) && this.i < this.n) {
        if (this.peek() === SPACE) {
          this.i += 1;
          maxIndent = Math.max(maxIndent, this.col());
        } else {
          this.newline();
        }
      }
      blockIndent = Math.max(minIndent, maxIndent);
    } else {
      blockIndent = minIndent + increment - 1;
      this.breaks(blockIndent);
    }
    while (this.col() === blockIndent && this.i < this.n) {
      while (!NOT_BREAK_END.has(this.peek())) {
        this.i += 1;
      }
      if (this.i >= this.n) {
        return;
      }
      this.newline();
      this.breaks(blockIndent);
    }
  }

  private breaks(blockIndent: number): void {
    while (this.col() < blockIndent && this.peek() === SPACE) {
      this.i += 1;
    }
    while (BREAK.has(this.peek()) && this.i < this.n) {
      this.newline();
      while (this.col() < blockIndent && this.peek() === SPACE) {
        this.i += 1;
      }
    }
  }
}

function checkFlow(toks: Tok[]): void {
  let keyed = withKeys(toks);
  if (keyed[0] === "?") {
    keyed = keyed.slice(1);
  }
  new Flow(keyed).collection();
}

const isOpen = (kind: string | undefined): boolean => kind === "[" || kind === "{";
const isClose = (kind: string | undefined): boolean => kind === "]" || kind === "}";
const isProperty = (kind: string | undefined): boolean => kind === "anchor" || kind === "tag";

/** The flow tokens with PyYAML's KEY token inserted before a node that a `:` on its own line follows. */
function withKeys(toks: Tok[]): string[] {
  const out: string[] = [];
  const kindAt = (j: number): string | undefined => toks[j]?.kind;
  let k = 0;
  while (k < toks.length) {
    const tok = toks[k] as Tok;
    const kind = tok.kind;
    if (!(kind === "node" || kind === "alias" || isOpen(kind) || isProperty(kind))) {
      out.push(kind);
      k += 1;
      continue;
    }
    const start = k;
    while (k < toks.length && isProperty(kindAt(k))) {
      k += 1;
    }
    const body = toks.slice(start, k).map((p) => p.kind);
    if (k < toks.length && isOpen(kindAt(k))) {
      let lvl = 0;
      let j = k;
      while (j < toks.length) {
        if (isOpen(kindAt(j))) {
          lvl += 1;
        } else if (isClose(kindAt(j))) {
          lvl -= 1;
          if (lvl === 0) {
            break;
          }
        }
        j += 1;
      }
      body.push(kindAt(k) as string);
      pushAll(body, withKeys(toks.slice(k + 1, Math.min(j, toks.length))));
      if (j < toks.length) {
        body.push(kindAt(j) as string);
      }
      k = j + 1;
    } else if (
      k < toks.length &&
      (kindAt(k) === "node" || (kindAt(k) === "alias" && k === start))
    ) {
      body.push(kindAt(k) as string);
      k += 1;
    }
    if (k < toks.length && kindAt(k) === ":" && toks[k]?.line === tok.line && out.at(-1) !== "?") {
      out.push("?");
    }
    pushAll(out, body);
  }
  return out;
}

/** `into.push(...items)` without spreading, which overflows the call stack on a long flow collection. */
function pushAll(into: string[], items: string[]): void {
  for (const item of items) {
    into.push(item);
  }
}

/** PyYAML's flow grammar over the keyed tokens. */
class Flow {
  private k = 0;

  constructor(private readonly t: string[]) {}

  private peek(): string {
    return this.t[this.k] ?? "eof";
  }

  private take(): void {
    this.k += 1;
  }

  private fail(why: string): never {
    throw new Problem(`while parsing a flow collection: ${why}, but found ${pyRepr(this.peek())}`);
  }

  /** parse_node: an alias, or at most one anchor and one tag (either order) before the content, which may be empty
   * after them. */
  private node(): void {
    if (this.peek() === "alias") {
      this.take();
      return;
    }
    const props: string[] = [];
    while (isProperty(this.peek()) && !props.includes(this.peek())) {
      props.push(this.peek());
      this.take();
    }
    if (this.peek() === "node") {
      this.take();
    } else if (isOpen(this.peek())) {
      this.collection();
    } else if (props.length === 0) {
      this.fail("expected the node content");
    }
  }

  collection(): void {
    const end = this.peek() === "[" ? "]" : "}";
    this.take();
    let first = true;
    while (this.peek() !== end) {
      if (!first) {
        if (this.peek() !== ",") {
          this.fail(`expected ',' or '${end}'`);
        }
        this.take();
        if (this.peek() === end) {
          break;
        }
      }
      first = false;
      if (this.peek() === "?") {
        this.take();
        if (!(this.peek() === ":" || this.peek() === "," || this.peek() === end)) {
          this.node();
        }
        if (this.peek() === ":") {
          this.take();
          if (!(this.peek() === "," || this.peek() === end)) {
            this.node();
          }
        }
      } else {
        this.node();
      }
    }
    this.take();
  }
}
