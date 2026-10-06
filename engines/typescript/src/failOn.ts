/**
 * A tiny, deterministic `--fail-on` expression language for `agentce assess` (SPEC §8.5, §7).
 *
 * A faithful port of the Python reference (`agentce/fail_on.py`). The grammar is comparisons of the
 * form `field=="literal"` joined by `and`/`or`, over a fixed field allow-list. Nothing is ever
 * evaluated as code: a hand-written tokenizer and an iterative parser only recognize a quoted string,
 * the `==` operator, and a bare identifier, so an injection payload is refused as an unexpected token
 * at parse time, before any assertion is evaluated against the expression.
 *
 * With no grouping, every expression is already a disjunction of conjunctions, so the parser returns a
 * flat list of OR-groups of AND-ed comparisons and evaluation is `some`/`every` over it: no tree, no
 * recursion, and a chain of thousands of clauses evaluates like a short one.
 *
 * Characters are read as code points (never UTF-16 units) with Python 3.12's own classes, so a
 * position in a message and what counts as whitespace or a letter match the reference exactly.
 */

import type { Assertion } from "./assertions";
import { InputError } from "./errors";
import { pyRepr } from "./util";

/** The only fields a --fail-on expression may compare against (SPEC §9.4 assertion fields), sorted. */
export const ALLOWED_FIELDS = [
  "control",
  "family",
  "mode",
  "outcome",
  "rung",
  "severity",
  "subject",
] as const;

type Field = (typeof ALLOWED_FIELDS)[number];

const KEYWORDS = new Set(["and", "or"]);

/** Python's `str.isspace()`: exactly these 29 code points, never the runtime's notion of whitespace. */
const PY_WHITESPACE = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001,
  0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f,
  0x205f, 0x3000,
]);

/** A parsed expression: OR-groups, each a list of AND-ed `[field, literal]` comparisons. */
type FailOnGroups = Array<Array<[Field, string]>>;

/** Internal syntax/semantic error; always translated to an `InputError` before it escapes. */
class ExprError extends Error {}

interface Token {
  kind: "string" | "op" | "ident";
  value: string;
}

/** Python's `str.isalpha()` (`c == "_"` is checked by the caller): general category L*. */
function isAlpha(c: string): boolean {
  return /\p{L}/u.test(c);
}

/** Python's `str.isalnum()` as the tokenizer uses it: general category L* or N*. */
function isAlnum(c: string): boolean {
  return /[\p{L}\p{N}]/u.test(c);
}

/** Lex `expr` into string/op/ident tokens. Any other character is a syntax error -- there is no
 * catch-all "skip and continue" branch, so a backtick, a parenthesis, a dot, or any other character
 * outside this tiny alphabet fails the whole expression rather than being silently discarded. */
function tokenize(expr: string): Token[] {
  const chars = Array.from(expr); // code points, so positions count what Python's do
  const tokens: Token[] = [];
  const n = chars.length;
  let i = 0;
  while (i < n) {
    const c = chars[i] as string;
    if (PY_WHITESPACE.has(c.codePointAt(0) as number)) {
      i += 1;
      continue;
    }
    if (c === '"' || c === "'") {
      let j = i + 1;
      let buf = "";
      while (j < n && chars[j] !== c) {
        if (chars[j] === "\\" && j + 1 < n) {
          buf += chars[j + 1];
          j += 2;
          continue;
        }
        buf += chars[j];
        j += 1;
      }
      if (j >= n) {
        throw new ExprError(`unterminated string literal starting at position ${i}`);
      }
      tokens.push({ kind: "string", value: buf });
      i = j + 1;
      continue;
    }
    if (c === "=" && chars[i + 1] === "=") {
      tokens.push({ kind: "op", value: "==" });
      i += 2;
      continue;
    }
    if (isAlpha(c) || c === "_") {
      let j = i;
      while (j < n && (isAlnum(chars[j] as string) || chars[j] === "_")) {
        j += 1;
      }
      tokens.push({ kind: "ident", value: chars.slice(i, j).join("") });
      i = j;
      continue;
    }
    throw new ExprError(`unexpected character ${pyRepr(c)} at position ${i}`);
  }
  return tokens;
}

/** expr := and_expr ("or" and_expr)* ; and_expr := comparison ("and" comparison)* ;
 * comparison := FIELD "==" STRING. No parentheses, no other operators -- the whole grammar. */
class Parser {
  private pos = 0;

  constructor(private readonly tokens: Token[]) {}

  private peek(): Token | undefined {
    return this.tokens[this.pos];
  }

  private advance(): Token | undefined {
    const tok = this.peek();
    this.pos += 1;
    return tok;
  }

  parse(): FailOnGroups {
    const groups = [this.andExpr()];
    while (this.atKeyword("or")) {
      this.advance();
      groups.push(this.andExpr());
    }
    const trailing = this.peek();
    if (trailing !== undefined) {
      throw new ExprError(`unexpected token ${pyRepr(trailing.value)} after a complete expression`);
    }
    return groups;
  }

  private atKeyword(word: string): boolean {
    const tok = this.peek();
    return tok !== undefined && tok.kind === "ident" && tok.value === word;
  }

  private andExpr(): Array<[Field, string]> {
    const group = [this.comparison()];
    while (this.atKeyword("and")) {
      this.advance();
      group.push(this.comparison());
    }
    return group;
  }

  private comparison(): [Field, string] {
    const fieldTok = this.advance();
    if (fieldTok === undefined || fieldTok.kind !== "ident") {
      throw new ExprError("expected a field name");
    }
    if (KEYWORDS.has(fieldTok.value)) {
      throw new ExprError(`expected a field name, got the reserved word ${pyRepr(fieldTok.value)}`);
    }
    if (!(ALLOWED_FIELDS as readonly string[]).includes(fieldTok.value)) {
      throw new ExprError(
        `unknown field ${pyRepr(fieldTok.value)}; choose from: ${ALLOWED_FIELDS.join(", ")}`,
      );
    }
    const opTok = this.advance();
    if (opTok === undefined || opTok.kind !== "op") {
      throw new ExprError(`expected '==' after field name ${pyRepr(fieldTok.value)}`);
    }
    const valTok = this.advance();
    if (valTok === undefined || valTok.kind !== "string") {
      throw new ExprError("expected a quoted string literal after '=='");
    }
    return [fieldTok.value as Field, valTok.value];
  }
}

const FIELD_GETTERS: Record<Field, (a: Assertion) => string> = {
  control: (a) => a.control,
  subject: (a) => a.subject,
  outcome: (a) => a.outcome,
  severity: (a) => a.severity,
  family: (a) => a.family,
  rung: (a) => String(a.rung), // Python's str(int)
  mode: (a) => a.mode,
};

/** Parse a `--fail-on` expression into a predicate over an `Assertion`. Throws an `InputError` keyed
 * `input.fail_on_invalid_expression` on any syntax error, unknown field, or disallowed token, at
 * parse time, before any assertion is evaluated against the expression (SPEC §7: never `eval`). */
export function parseFailOn(expr: string): (assertion: Assertion) => boolean {
  let groups: FailOnGroups;
  try {
    const tokens = tokenize(expr);
    if (tokens.length === 0) {
      throw new ExprError("the --fail-on expression is empty");
    }
    groups = new Parser(tokens).parse();
  } catch (exc) {
    if (!(exc instanceof ExprError)) {
      throw exc;
    }
    throw new InputError(
      "input.fail_on_invalid_expression",
      `--fail-on ${pyRepr(expr)} is not a valid expression: ${exc.message}`,
      `use comparisons of the form field=="literal" joined by and/or, over: ${ALLOWED_FIELDS.join(", ")}.`,
    );
  }
  return (assertion) =>
    groups.some((group) =>
      group.every(([field, literal]) => FIELD_GETTERS[field](assertion) === literal),
    );
}
