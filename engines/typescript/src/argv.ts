/**
 * The command-line grammar scanner (SPEC §8.5, "Command-line grammar").
 *
 * Every engine reads argv under one table, `spec/cli/argv-grammar.json` (vendored byte for byte to
 * `data/cli/`), with the same one-pass scan: no prefix matching, no short-flag clusters, no option
 * given twice unless the table marks it repeatable, a value only after `=` or in the next token when
 * that token does not start with `-`, and `--` ending the options. `-h`/`--help` in an option position
 * wins over any refusal; otherwise the first refusal in argv order is raised, keyed by the command.
 * The scan never reads the file system. Mirrors `engines/python/agentce/argv.py` and
 * `engines/java/.../Argv.java`.
 */

import { readFileSync } from "node:fs";
import { argvGrammarPath } from "./bundled";
import { InputError } from "./errors";

export const HELP: readonly string[] = ["-h", "--help"];

/** A line outside the grammar: exit 3 under the command's key, naming the SPEC 8.5 rule. */
export class ArgvRefusal extends InputError {
  readonly rule: string;
  readonly token: string;

  constructor(rule: string, token: string, key: string, cause: string, fix: string) {
    super(key, cause, fix);
    this.rule = rule;
    this.token = token;
  }
}

export interface Option {
  readonly names: readonly string[];
  readonly kind: "flag" | "value";
  readonly repeatable: boolean;
  readonly needsValueKey?: string;
  readonly valueHint: string;
  readonly choices?: readonly string[];
  /** The option's last spelling: what a scan records it under. */
  readonly name: string;
}

/** One command, or one action under it: its options and its positional bound (`null`: none). */
export interface Level {
  readonly options: ReadonlyMap<string, Option>;
  readonly maxPositionals: number | null;
  readonly actions: ReadonlyMap<string, Level>;
}

export interface Command {
  readonly name: string;
  readonly level: Level;
  readonly unrecognizedKey: string;
  readonly needsValueKey: string;
  readonly flagFix: string;
  readonly extraFix: string;
  readonly implementedIn: readonly string[];
}

/** What a scan read. `help` is set when the line asks for a usage; `action` then names whose
 * (unset: the command's). `values` holds single values, `lists` a repeatable option's values in argv
 * order, `flags` the flags given, each by the option's last spelling. */
export interface Scan {
  help: boolean;
  action?: string;
  values: Record<string, string>;
  lists: Record<string, string[]>;
  flags: Set<string>;
  positionals: string[];
}

interface RawOption {
  names: string[];
  kind: "flag" | "value";
  repeatable: boolean;
  needs_value_key?: string;
  value_hint?: string;
  choices?: string[];
}

interface RawLevel {
  options: RawOption[];
  positionals: { min: number; max: number | null };
  actions?: Record<string, RawLevel>;
}

interface RawCommand extends RawLevel {
  implemented_in: string[];
  keys: { unrecognized: string; needs_value: string };
  fixes: { flag: string; extra: string };
}

export interface RawGrammar {
  rules: string[];
  globals: RawOption[];
  commands: Record<string, RawCommand>;
}

function option(raw: RawOption): Option {
  return {
    names: raw.names,
    kind: raw.kind,
    repeatable: raw.repeatable,
    ...(raw.needs_value_key === undefined ? {} : { needsValueKey: raw.needs_value_key }),
    valueHint: raw.value_hint ?? "<value>",
    ...(raw.choices === undefined ? {} : { choices: raw.choices }),
    name: raw.names[raw.names.length - 1] as string,
  };
}

function level(raw: RawLevel, globals: readonly Option[]): Level {
  const options = new Map<string, Option>();
  for (const o of [...globals, ...raw.options.map(option)]) {
    for (const n of o.names) {
      options.set(n, o);
    }
  }
  const actions = new Map<string, Level>();
  for (const [name, action] of Object.entries(raw.actions ?? {})) {
    actions.set(name, level(action, globals));
  }
  return { options, maxPositionals: raw.positionals.max, actions };
}

let cached: RawGrammar | undefined;

/** The vendored grammar file, as data. */
export function grammar(): RawGrammar {
  cached ??= JSON.parse(readFileSync(argvGrammarPath(), "utf-8")) as RawGrammar;
  return cached;
}

/** Whether the grammar names `name` as a command (an own key, never `__proto__` or `toString`). */
export function isCommand(name: string): boolean {
  return Object.hasOwn(grammar().commands, name);
}

const commands = new Map<string, Command>();

export function command(name: string): Command {
  let cmd = commands.get(name);
  if (cmd === undefined) {
    const raw = grammar().commands[name] as RawCommand;
    cmd = {
      name,
      level: level(raw, grammar().globals.map(option)),
      unrecognizedKey: raw.keys.unrecognized,
      needsValueKey: raw.keys.needs_value,
      flagFix: raw.fixes.flag,
      extraFix: raw.fixes.extra,
      implementedIn: raw.implemented_in,
    };
    commands.set(name, cmd);
  }
  return cmd;
}

/** Words a value outside an option's choices, given the option's name and the value. */
export type ChoiceError = (option: string, value: string) => InputError;
/** Words an action the command does not have. */
export type ActionError = () => InputError;

export interface ScanOptions {
  /** The global flags already given before the command word (a second one is a repeat). */
  readonly seen?: readonly string[];
  readonly choiceError?: ChoiceError;
  readonly actionError?: ActionError;
}

/** Read `tokens` (the line after the command word) under `cmd`'s grammar. */
export function scan(cmd: Command, tokens: readonly string[], opts: ScanOptions = {}): Scan {
  const out: Scan = { help: false, values: {}, lists: {}, flags: new Set(), positionals: [] };
  let lvl = cmd.level;
  const given = new Set<string>(opts.seen ?? []);
  let first: InputError | undefined;
  let helpSeen = false;
  let helpAction: string | undefined;

  const refuse = (rule: string, token: string, cause: string, fix: string, key?: string): void => {
    first ??= new ArgvRefusal(rule, token, key ?? cmd.unrecognizedKey, cause, fix);
  };
  const unrecognized = (rule: string, token: string): void =>
    refuse(rule, token, `unrecognized flag '${token}'.`, cmd.flagFix);
  const positional = (token: string): void => {
    if (lvl.maxPositionals !== null && out.positionals.length >= lvl.maxPositionals) {
      refuse("extra_positional", token, `unrecognized argument '${token}'.`, cmd.extraFix);
    } else {
      out.positionals.push(token);
    }
  };
  const refuseAction = (token: string): void => {
    const err = (opts.actionError as ActionError)(); // a command with actions words its unknown action
    refuse("unknown_action", token, err.cause, err.fix, err.key);
  };

  let ended = false;
  let i = 0;
  while (i < tokens.length) {
    const token = tokens[i] as string;
    i++;
    if (ended) {
      positional(token);
      continue;
    }
    if (token === "--") {
      if (lvl.actions.size > 0 && out.action === undefined) {
        refuseAction(token);
        break;
      }
      if (lvl.maxPositionals === 0 || i === tokens.length) {
        refuse("double_dash_alone", token, "unrecognized flag '--'.", cmd.flagFix);
      }
      ended = true;
      continue;
    }
    if (HELP.includes(token)) {
      if (!helpSeen) {
        helpSeen = true;
        helpAction = out.action;
      }
      continue;
    }
    if (!token.startsWith("-")) {
      if (lvl.actions.size > 0 && out.action === undefined) {
        const action = lvl.actions.get(token);
        if (action !== undefined) {
          out.action = token;
          lvl = action;
          continue;
        }
        refuseAction(token);
        break;
      }
      positional(token);
      continue;
    }
    const eqAt = token.indexOf("=");
    const name = eqAt < 0 ? token : token.slice(0, eqAt);
    const opt = lvl.options.get(name);
    if (opt === undefined) {
      if (HELP.includes(name)) {
        refuse(
          "value_on_flag",
          token,
          `flag '${name}' takes no value.`,
          `drop the value: ${name}.`,
        );
      } else if (token.startsWith("--")) {
        unrecognized("exact_spelling", token);
      } else if (token.length > 2) {
        unrecognized("short_cluster", token);
      } else {
        unrecognized("dash_token_not_option", token);
      }
      continue;
    }
    if (given.has(opt.name) && !opt.repeatable) {
      refuse(
        "repeated_option",
        token,
        `flag '${name}' was given more than once.`,
        `give ${name} once.`,
      );
    }
    given.add(opt.name);
    if (opt.kind === "flag") {
      if (eqAt >= 0) {
        refuse(
          "value_on_flag",
          token,
          `flag '${name}' takes no value.`,
          `drop the value: ${name}.`,
        );
      }
      out.flags.add(opt.name);
      continue;
    }
    let value: string;
    if (eqAt >= 0) {
      value = token.slice(eqAt + 1);
    } else if (i < tokens.length && !(tokens[i] as string).startsWith("-")) {
      value = tokens[i] as string;
      i++;
    } else {
      const next = tokens[i];
      const rule =
        next === undefined
          ? "needs_value"
          : next === "--"
            ? "needs_value_before_double_dash"
            : "needs_value_before_option";
      refuse(
        rule,
        name,
        `flag '${name}' needs a value.`,
        `pass ${name} ${opt.valueHint}.`,
        opt.needsValueKey ?? cmd.needsValueKey,
      );
      if (next !== undefined && HELP.includes(next)) {
        i++; // help in a value slot is no help (SPEC 8.5)
      }
      continue;
    }
    if (
      opt.choices !== undefined &&
      !opt.choices.includes(value) &&
      opts.choiceError !== undefined
    ) {
      const err = opts.choiceError(opt.name, value);
      refuse("choices", token, err.cause, err.fix, err.key);
    }
    if (opt.repeatable) {
      out.lists[opt.name] ??= [];
      out.lists[opt.name]?.push(value);
    } else {
      out.values[opt.name] = value;
    }
  }
  if (helpSeen) {
    const help: Scan = { help: true, values: {}, lists: {}, flags: new Set(), positionals: [] };
    if (helpAction !== undefined) {
      help.action = helpAction;
    }
    return help;
  }
  if (first !== undefined) {
    throw first;
  }
  return out;
}
