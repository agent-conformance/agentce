/**
 * One table-driven argv scanner (SPEC §8.5): a command declares its grammar as a plain table (each
 * option's names and kind, the most positionals it takes, its refusal keys and fix texts) and
 * {@link scanArgv} reads argv the way Python's argparse reads it for that command (`allow_abbrev=False`,
 * plus Python's `_refuse_short_clusters`), returning a plain record. `assess` is the first command on
 * it (18.109); the others move onto it with 18.50. Mirrors `engines/java/.../Argv.java`.
 */

import { InputError } from "./errors";
import { pyRepr } from "./util";

/** VALUE: one value, the last occurrence wins. APPEND: every value, in order. FLAG: present or not,
 * takes no value. HELP: asks for the usage, at once. */
export type OptionKind = "value" | "append" | "flag" | "help";

export interface OptionSpec {
  /** Every spelling, as argparse declares them (`["-h", "--help"]`); the last is the result's key. */
  readonly names: readonly string[];
  readonly kind: OptionKind;
  /** What the missing-value fix names after the flag (default `<value>`). */
  readonly valueHint?: string;
}

export interface Grammar {
  readonly options: readonly OptionSpec[];
  /** The most positionals the command takes; a further one is refused as an unrecognized argument. */
  readonly maxPositionals: number;
  /** The key for an unknown flag, an extra argument, a cluster and a value on a flag. */
  readonly unrecognizedKey: string;
  /** The key for a VALUE/APPEND option given no value. */
  readonly needsValueKey: string;
  /** The fix for an unknown flag. */
  readonly flagFix: string;
  /** The fix for an argument past {@link Grammar.maxPositionals}. */
  readonly extraArgFix: string;
}

/** What a scan read: by each option's key (its last name), and the positionals in order. */
export interface Scan {
  readonly help: boolean;
  readonly values: Readonly<Record<string, string>>;
  readonly lists: Readonly<Record<string, readonly string[]>>;
  readonly flags: ReadonlySet<string>;
  readonly positionals: readonly string[];
}

/** argparse's `_negative_number_matcher` (a str regex: `\d` is any Unicode decimal digit and `$` also
 * matches before one trailing newline). */
const NEGATIVE_NUMBER = /^-\p{Nd}+\n?$|^-\p{Nd}*\.\p{Nd}+\n?$/u;

type Classified =
  | {
      readonly kind: "option";
      readonly spec: OptionSpec;
      readonly name: string;
      readonly explicit?: string;
    }
  | { readonly kind: "positional" }
  | { readonly kind: "unknown" };

function lookup(grammar: Grammar, name: string): OptionSpec | undefined {
  return grammar.options.find((spec) => spec.names.includes(name));
}

/** argparse's `_parse_optional`: a known name, or `name=value` with a known name, is an option; else
 * a token that does not start with `-`, is `-`, is a negative number or holds a space is positional;
 * anything else is an unknown flag. */
function classify(grammar: Grammar, token: string): Classified {
  const exact = lookup(grammar, token);
  if (exact !== undefined) {
    return { kind: "option", spec: exact, name: token };
  }
  if (!token.startsWith("-") || token === "-") {
    return { kind: "positional" };
  }
  const eq = token.indexOf("=");
  if (eq >= 0) {
    const name = token.slice(0, eq);
    const spec = lookup(grammar, name);
    if (spec !== undefined) {
      return { kind: "option", spec, name, explicit: token.slice(eq + 1) };
    }
  }
  if (NEGATIVE_NUMBER.test(token) || token.includes(" ")) {
    return { kind: "positional" };
  }
  return { kind: "unknown" };
}

/** The refusal Python's `_ArgvErrors.unknown` gives for a token argparse left unparsed: a flag when it
 * starts with `-` (and is not `-`), else an argument. */
function unrecognized(grammar: Grammar, token: string): InputError {
  return token.startsWith("-") && token !== "-"
    ? new InputError(grammar.unrecognizedKey, `unrecognized flag '${token}'.`, grammar.flagFix)
    : new InputError(
        grammar.unrecognizedKey,
        `unrecognized argument '${token}'.`,
        grammar.extraArgFix,
      );
}

/** Python's `_refuse_short_clusters`: agentce has no clustered short options, so a one-dash token of
 * three or more characters that is neither an option nor a negative number (`-hx`) is refused before
 * anything else, up to `--`. A token taken as an option's value is skipped; where a value is missing
 * (the next token is option-like) the scan stops, so the missing value is refused first. */
function refuseShortClusters(argv: readonly string[], grammar: Grammar): void {
  let pending = false;
  for (const token of argv) {
    if (token === "--") {
      return;
    }
    if (pending) {
      pending = false;
      if (classify(grammar, token).kind === "positional") {
        continue;
      }
      return;
    }
    const spec = lookup(grammar, token);
    if (spec !== undefined && (spec.kind === "value" || spec.kind === "append")) {
      pending = true;
    }
    if (
      token.startsWith("-") &&
      !token.startsWith("--") &&
      token.length > 2 &&
      spec === undefined &&
      !NEGATIVE_NUMBER.test(token)
    ) {
      throw unrecognized(grammar, token);
    }
  }
}

function label(spec: OptionSpec): string {
  return spec.names.join("/");
}

/** A FLAG or HELP given `=value`: argparse's "ignored explicit argument", worded as Python words it. */
function takesNoValue(grammar: Grammar, spec: OptionSpec, value: string): InputError {
  const flag = label(spec);
  if (/^--[\w-]+$/.test(flag)) {
    return new InputError(
      grammar.unrecognizedKey,
      `flag '${flag}' takes no value.`,
      `drop the value: ${flag}.`,
    );
  }
  return new InputError(
    grammar.unrecognizedKey,
    `argument ${flag}: ignored explicit argument ${pyRepr(value)}.`,
    grammar.flagFix,
  );
}

/** Read `argv` (the tokens after the command name) against `grammar`, in argparse's order: a short
 * cluster first; then left to right, a missing value or a value on a flag is refused at once, HELP
 * returns at once, and an unknown flag or an argument past the maximum is kept and the first of them in
 * argv order is refused once the scan ends. `--` makes every later token positional. */
export function scanArgv(argv: readonly string[], grammar: Grammar): Scan {
  refuseShortClusters(argv, grammar);
  const values: Record<string, string> = {};
  const lists: Record<string, string[]> = {};
  const flags = new Set<string>();
  const positionals: string[] = [];
  let deferred: string | undefined;
  let ended = false;
  const positional = (token: string): void => {
    if (positionals.length < grammar.maxPositionals) {
      positionals.push(token);
    } else {
      deferred ??= token;
    }
  };
  for (let i = 0; i < argv.length; i++) {
    const token = argv[i] as string;
    if (ended) {
      positional(token);
      continue;
    }
    if (token === "--") {
      ended = true;
      continue;
    }
    const c = classify(grammar, token);
    if (c.kind === "positional") {
      positional(token);
      continue;
    }
    if (c.kind === "unknown") {
      deferred ??= token;
      continue;
    }
    const { spec } = c;
    const key = spec.names[spec.names.length - 1] as string;
    if (spec.kind === "flag" || spec.kind === "help") {
      if (c.explicit !== undefined) {
        throw takesNoValue(grammar, spec, c.explicit);
      }
      if (spec.kind === "help") {
        return { help: true, values, lists, flags, positionals };
      }
      flags.add(key);
      continue;
    }
    let value = c.explicit;
    if (value === undefined) {
      const next = argv[i + 1];
      if (next === undefined || next === "--" || classify(grammar, next).kind !== "positional") {
        throw new InputError(
          grammar.needsValueKey,
          `argument ${label(spec)}: expected one argument`,
          `pass ${label(spec)} ${spec.valueHint ?? "<value>"}.`,
        );
      }
      value = next;
      i++;
    }
    if (spec.kind === "value") {
      values[key] = value;
    } else {
      const list = lists[key] ?? [];
      list.push(value);
      lists[key] = list;
    }
  }
  if (deferred !== undefined) {
    throw unrecognized(grammar, deferred);
  }
  return { help: false, values, lists, flags, positionals };
}
