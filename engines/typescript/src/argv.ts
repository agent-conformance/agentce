/**
 * One table-driven argv scanner (SPEC §8.5): a command declares its grammar as a plain table (each
 * option's names and kind, the most positionals it takes, its refusal keys and fix texts) and
 * {@link scanArgv} reads argv the way Python's argparse reads it for that command (`allow_abbrev=False`,
 * plus Python's `_refuse_short_clusters`), returning a plain record. `assess` is the first command on
 * it (18.109), `report` the second (18.110), then `validate`, `diff`, `version` and `conformance`
 * (18.111); the others move onto it with 18.50. An option may declare its `choices` (refused with its
 * own error the moment its value is read, in argv order, as argparse checks a choice) and its own
 * needs-value key. A grammar may declare an ACTION level (argparse's subparsers, `conformance run`):
 * the first positional names the action, whose own grammar reads the rest. Mirrors
 * `engines/java/.../Argv.java`.
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
  /** The values a VALUE option accepts; any other is refused with its `error` at once, the moment the
   * value is read (argparse's invalid choice), whether given as `--x=v` or `--x v`. */
  readonly choices?: {
    readonly values: readonly string[];
    readonly error: (value: string) => InputError;
  };
  /** This option's key when given no value, in place of {@link Grammar.needsValueKey}. */
  readonly needsValueKey?: string;
}

/** argparse's subparsers: the actions a command takes, each with its own grammar. */
export interface ActionLevel {
  readonly grammars: Readonly<Record<string, Grammar>>;
  /** The refusal for an action that is not named, given at once (argparse's invalid choice). */
  readonly error: () => InputError;
}

export interface Grammar {
  readonly options: readonly OptionSpec[];
  /** The most positionals the command takes (`Infinity`: no bound); a further one is refused as an
   * unrecognized argument. With an {@link action} level the first positional is the action instead. */
  readonly maxPositionals: number;
  /** The command's actions: the first positional token, or `--`, names one, and its grammar reads
   * every later token. */
  readonly action?: ActionLevel;
  /** The key for an unknown flag, an extra argument, a cluster and a value on a flag. */
  readonly unrecognizedKey: string;
  /** The key for a VALUE/APPEND option given no value, unless the option names its own. */
  readonly needsValueKey: string;
  /** How a missing value's cause reads: argparse's own sentence (`argument --x: expected one
   * argument`, assess) or Python's plain one (`flag '--x' needs a value.`); default argparse. */
  readonly needsValueCause?: "argparse" | "plain";
  /** The fix for an unknown flag. */
  readonly flagFix: string;
  /** The fix for an argument past {@link Grammar.maxPositionals}. */
  readonly extraArgFix: string;
}

/** What a scan read: by each option's key (its last name), and the positionals in order. With an
 * action level, `action` is the action read (set only once one is); on `help` it names whose usage to
 * print (unset: the command's). */
export interface Scan {
  readonly help: boolean;
  readonly action?: string;
  readonly values: Readonly<Record<string, string>>;
  readonly lists: Readonly<Record<string, readonly string[]>>;
  readonly flags: ReadonlySet<string>;
  readonly positionals: readonly string[];
}

/** argparse's `_negative_number_matcher` (a str regex: `\d` is any Unicode decimal digit and `$` also
 * matches before one trailing newline). */
export const NEGATIVE_NUMBER = /^-\p{Nd}+\n?$|^-\p{Nd}*\.\p{Nd}+\n?$/u;

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
  // With allow_abbrev=False argparse still matches a single-dash token's first two characters
  // against the short options (`-h y` is -h with a joined value), so it is never a positional.
  const shortJoined = !token.startsWith("--") && lookup(grammar, token.slice(0, 2)) !== undefined;
  if (!shortJoined && (NEGATIVE_NUMBER.test(token) || token.includes(" "))) {
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
 * anything else, up to `--` (and, for a grammar with actions, up to the first token that does not
 * start with `-`: the action's own scan checks the rest). A token taken as an option's value is
 * skipped; where a value is missing (the next token is option-like) the scan stops, so the missing
 * value is refused first. */
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
    if (!token.startsWith("-") && grammar.action !== undefined) {
      return;
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

/** One level's scan: what it read, and the refusal for the first token it left unparsed (an unknown
 * flag or an argument past the maximum), which waits until the whole line has been scanned. */
interface LevelScan {
  readonly scan: Scan;
  readonly leftover?: InputError;
}

/** Read `argv` (the tokens after the command name) against `grammar`, in argparse's order: a short
 * cluster first; then left to right, a missing value, a value outside an option's choices or a value
 * on a flag is refused at once, HELP returns at once, and an unknown flag or an argument past the
 * maximum is kept and the first of them in argv order is refused once the scan ends. `--` makes every
 * later token positional; to a command that takes none, `--` is itself an unrecognized flag. With an
 * action level, the first positional token (or `--`) is the action: one that is not named is refused
 * at once, and a named one's grammar scans every later token the same way (its HELP, too, returns at
 * once, naming the action); a token the command level left unparsed is refused before the action's. */
export function scanArgv(argv: readonly string[], grammar: Grammar): Scan {
  const { scan, leftover } = scanLevel(argv, grammar);
  if (leftover !== undefined) {
    throw leftover;
  }
  return scan;
}

function scanLevel(argv: readonly string[], grammar: Grammar): LevelScan {
  refuseShortClusters(argv, grammar);
  const values: Record<string, string> = {};
  const lists: Record<string, string[]> = {};
  const flags = new Set<string>();
  const positionals: string[] = [];
  let deferred: string | undefined;
  let ended = false;
  const leftover = (): InputError | undefined =>
    deferred === undefined ? undefined : unrecognized(grammar, deferred);
  const positional = (token: string): void => {
    if (positionals.length < grammar.maxPositionals) {
      positionals.push(token);
    } else {
      deferred ??= token;
    }
  };
  /** The action `token` names reads every later token; its scan joins this level's record. */
  const takeAction = (token: string, rest: readonly string[]): LevelScan => {
    const actions = grammar.action as ActionLevel;
    const actionGrammar = Object.hasOwn(actions.grammars, token)
      ? actions.grammars[token]
      : undefined;
    if (actionGrammar === undefined) {
      throw actions.error();
    }
    const sub = scanLevel(rest, actionGrammar);
    const merged: Record<string, string[]> = { ...lists };
    for (const [key, list] of Object.entries(sub.scan.lists)) {
      merged[key] = [...(merged[key] ?? []), ...list];
    }
    const scan: Scan = {
      help: sub.scan.help,
      action: token,
      values: { ...values, ...sub.scan.values },
      lists: merged,
      flags: new Set([...flags, ...sub.scan.flags]),
      positionals: [...positionals, ...sub.scan.positionals],
    };
    return sub.scan.help ? { scan } : { scan, leftover: leftover() ?? sub.leftover };
  };
  for (let i = 0; i < argv.length; i++) {
    const token = argv[i] as string;
    if (ended) {
      positional(token);
      continue;
    }
    if (token === "--") {
      if (grammar.action !== undefined) {
        return takeAction(token, argv.slice(i + 1));
      }
      // A command with no positionals leaves `--` itself unparsed, as argparse does (Python 3.12).
      if (grammar.maxPositionals === 0) {
        deferred ??= token;
      }
      ended = true;
      continue;
    }
    const c = classify(grammar, token);
    if (c.kind === "positional") {
      if (grammar.action !== undefined) {
        return takeAction(token, argv.slice(i + 1));
      }
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
        return { scan: { help: true, values, lists, flags, positionals } };
      }
      flags.add(key);
      continue;
    }
    let value = c.explicit;
    if (value === undefined) {
      const next = argv[i + 1];
      if (next === undefined || next === "--" || classify(grammar, next).kind !== "positional") {
        throw new InputError(
          spec.needsValueKey ?? grammar.needsValueKey,
          grammar.needsValueCause === "plain"
            ? `flag '${label(spec)}' needs a value.`
            : `argument ${label(spec)}: expected one argument`,
          `pass ${label(spec)} ${spec.valueHint ?? "<value>"}.`,
        );
      }
      value = next;
      i++;
    }
    if (spec.choices !== undefined && !spec.choices.values.includes(value)) {
      throw spec.choices.error(value);
    }
    if (spec.kind === "value") {
      values[key] = value;
    } else {
      const list = lists[key] ?? [];
      list.push(value);
      lists[key] = list;
    }
  }
  return { scan: { help: false, values, lists, flags, positionals }, leftover: leftover() };
}
