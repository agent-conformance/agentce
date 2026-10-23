/**
 * The SPEC 8.5 command-line grammar scanner, against the vendored table's own commands: one pass, the
 * first refusal in argv order, help over every refusal, the action level, choices and needs-value
 * keys. `argvCases.test.ts` runs the shared case table through `main`; these pin the scanner's own
 * words. Python's `agentce.argv` is the reference.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { ArgvRefusal, type ScanOptions, command, scan } from "./argv";
import { argvGrammarPath } from "./bundled";
import { InputError } from "./errors";

const REPO = join(__dirname, "..", "..", "..");

const OPTS: ScanOptions = {
  choiceError: (option, value) => new InputError("input.t_choice", `${option} ${value}`, "CHOICE"),
  actionError: () => new InputError("input.t_action", "ACTION-CAUSE", "ACTION-FIX"),
};

function refusal(
  name: string,
  tokens: string[],
  seen: string[] = [],
): { rule: string; key: string; cause: string; fix: string } {
  try {
    scan(command(name), tokens, { ...OPTS, seen });
  } catch (exc) {
    assert.ok(exc instanceof InputError);
    const rule = exc instanceof ArgvRefusal ? exc.rule : "";
    return { rule, key: exc.key, cause: exc.cause, fix: exc.fix };
  }
  assert.fail(`no refusal for ${name} ${JSON.stringify(tokens)}`);
}

const read = (name: string, tokens: string[]) => scan(command(name), tokens, OPTS);

test("the vendored grammar is byte-identical to spec/cli/argv-grammar.json", () => {
  const spec = readFileSync(join(REPO, "spec", "cli", "argv-grammar.json"));
  assert.ok(readFileSync(argvGrammarPath()).equals(spec));
});

test("values from = (spaces, dashes, empty) or the next token; flags; positionals", () => {
  const s = read("assess", ["--out=a b", "--fail-on", "x", "--emit=-1", "--json", "dir"]);
  assert.deepEqual(s.values, { "--out": "a b", "--fail-on": "x", "--emit": "-1" });
  assert.deepEqual([...s.flags], ["--json"]);
  assert.deepEqual(s.positionals, ["dir"]);
  assert.equal(read("readiness", ["--gaps="]).values["--gaps"], "");
});

test("a repeatable option keeps every value in order; any other given twice is refused", () => {
  assert.deepEqual(read("readiness", ["--catalog-dir", "b", "--catalog-dir=a"]).lists, {
    "--catalog-dir": ["b", "a"],
  });
  assert.deepEqual(refusal("readiness", ["--gaps", "a", "--gaps=b"]), {
    rule: "repeated_option",
    key: "input.readiness_unrecognized_flag",
    cause: "flag '--gaps' was given more than once.",
    fix: "give --gaps once.",
  });
  // a global flag given before the command word counts
  assert.equal(refusal("version", ["--json"], ["--json"]).rule, "repeated_option");
});

test("a missing value: at the end, before `--`, before any dash token; help there is no help", () => {
  const needs = (rule: string) => ({
    rule,
    key: "input.assess_flag_needs_value",
    cause: "flag '--out' needs a value.",
    fix: "pass --out <value>.",
  });
  assert.deepEqual(refusal("assess", ["--out"]), needs("needs_value"));
  assert.deepEqual(
    refusal("assess", ["--out", "--", "x"]),
    needs("needs_value_before_double_dash"),
  );
  assert.deepEqual(refusal("assess", ["--out", "-1"]), needs("needs_value_before_option"));
  assert.deepEqual(refusal("assess", ["--out", "-"]), needs("needs_value_before_option"));
  assert.deepEqual(refusal("assess", ["--out", "--help"]), needs("needs_value_before_option"));
  assert.equal(refusal("readiness", ["--gaps"]).fix, "pass --gaps <path>.");
  // an option's own key, else the command's
  assert.equal(refusal("report", ["--from"]).key, "input.from_missing");
  assert.equal(refusal("report", ["--out"]).key, "input.report_unrecognized_flag");
  assert.equal(refusal("conformance", ["run", "--engine"]).key, "input.engine_missing");
});

test("a dash token that is not a spelling, by rule, with the command's flag fix", () => {
  const unknown = (rule: string, token: string) => ({
    rule,
    key: "input.sign_unrecognized_flag",
    cause: `unrecognized flag '${token}'.`,
    fix: "pass --as, --profile, --key, --dry-run, or --write-trust-root, or drop the flag.",
  });
  assert.deepEqual(refusal("sign", ["--pro", "x"]), unknown("exact_spelling", "--pro"));
  assert.deepEqual(refusal("sign", ["--as x"]), unknown("exact_spelling", "--as x"));
  assert.deepEqual(refusal("sign", ["-hx"]), unknown("short_cluster", "-hx"));
  assert.deepEqual(refusal("sign", ["-1.5"]), unknown("short_cluster", "-1.5"));
  assert.deepEqual(refusal("sign", ["-"]), unknown("dash_token_not_option", "-"));
  assert.deepEqual(refusal("sign", ["-1"]), unknown("dash_token_not_option", "-1"));
});

test("a value on a flag, help included, is refused", () => {
  assert.deepEqual(refusal("sign", ["--dry-run=1"]), {
    rule: "value_on_flag",
    key: "input.sign_unrecognized_flag",
    cause: "flag '--dry-run' takes no value.",
    fix: "drop the value: --dry-run.",
  });
  assert.equal(refusal("sign", ["--help=x"]).cause, "flag '--help' takes no value.");
});

test("`--` ends the options; alone, last, or to a command with no positionals it is refused", () => {
  assert.deepEqual(read("diff", ["--", "-1", "--format"]).positionals, ["-1", "--format"]);
  for (const [name, tokens] of [
    ["readiness", ["dir", "--"]],
    ["readiness", ["--"]],
    ["verify", ["--", "x"]],
  ] as const) {
    const refused = refusal(name, [...tokens]);
    assert.equal(refused.rule, "double_dash_alone");
    assert.equal(refused.cause, "unrecognized flag '--'.");
  }
});

test("an extra positional is refused with the command's extra fix", () => {
  assert.deepEqual(refusal("readiness", ["a", "b"]), {
    rule: "extra_positional",
    key: "input.readiness_unrecognized_flag",
    cause: "unrecognized argument 'b'.",
    fix: "pass exactly one report directory: `agentce readiness <report-dir>`.",
  });
  // diff has no bound: the command counts its own
  assert.deepEqual(read("diff", ["a", "b", "c"]).positionals, ["a", "b", "c"]);
});

test("the first refusal in argv order wins; help wins over every refusal", () => {
  assert.equal(refusal("assess", ["--nope", "--out"]).cause, "unrecognized flag '--nope'.");
  assert.equal(refusal("assess", ["a", "b", "--nope"]).cause, "unrecognized argument 'b'.");
  for (const tokens of [
    ["--nope", "-h"],
    ["a", "b", "--help"],
    ["--out", "o", "--out", "p", "-h"],
  ]) {
    const s = read("assess", tokens);
    assert.equal(s.help, true, tokens.join(" "));
    assert.deepEqual(s.values, {});
  }
  // help after `--` is a positional
  assert.deepEqual(read("readiness", ["--", "--help"]).positionals, ["--help"]);
});

test("choices: a value outside them is worded by the command's callback", () => {
  assert.deepEqual(refusal("report", ["--format=nope"]), {
    rule: "choices",
    key: "input.t_choice",
    cause: "--format nope",
    fix: "CHOICE",
  });
  assert.equal(read("report", ["--format", "pack"]).values["--format"], "pack");
});

test("the action level: the first word names it; help names whose usage", () => {
  const s = read("conformance", ["--json", "run", "--engine=e", "--out", "o"]);
  assert.equal(s.action, "run");
  assert.deepEqual(s.values, { "--engine": "e", "--out": "o" });
  assert.deepEqual([...s.flags], ["--json"]);
  assert.equal(read("conformance", []).action, undefined);
  assert.equal(refusal("conformance", ["nope"]).key, "input.t_action");
  assert.equal(refusal("conformance", ["--", "run"]).key, "input.t_action");
  // a run option before the action is unknown to the command level
  assert.equal(refusal("conformance", ["--engine", "e", "run"]).rule, "exact_spelling");
  assert.equal(read("conformance", ["-h", "run"]).action, undefined);
  assert.equal(read("conformance", ["--he", "run", "-h"]).action, "run");
});
