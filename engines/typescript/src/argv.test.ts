/**
 * Every branch of the table-driven argv scanner (18.109), against a small grammar shaped like assess's:
 * the cluster pre-scan, token classification, `--`, values from `=` or the next token, the missing
 * value, HELP at once, a value on a FLAG/HELP, the deferred unknown flag and extra argument, last wins
 * and append. Python's argparse is the reference for each answer; `ArgvTest.java` pins the same.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Grammar, scanArgv } from "./argv";
import { InputError } from "./errors";

const G: Grammar = {
  options: [
    { names: ["-h", "--help"], kind: "help" },
    { names: ["--json"], kind: "flag" },
    { names: ["--out"], kind: "value" },
    { names: ["--fail-on"], kind: "value", valueHint: "<expression>" },
    { names: ["--catalog-dir"], kind: "append" },
  ],
  maxPositionals: 1,
  unrecognizedKey: "input.t_unrecognized_flag",
  needsValueKey: "input.t_flag_needs_value",
  flagFix: "FLAG-FIX",
  extraArgFix: "EXTRA-FIX",
};

function refusal(argv: string[]): { key: string; cause: string; fix: string } {
  try {
    scanArgv(argv, G);
  } catch (exc) {
    assert.ok(exc instanceof InputError);
    return { key: exc.key, cause: exc.cause, fix: exc.fix };
  }
  assert.fail(`no refusal for ${JSON.stringify(argv)}`);
}

const unrecognizedFlag = (token: string) => ({
  key: "input.t_unrecognized_flag",
  cause: `unrecognized flag '${token}'.`,
  fix: "FLAG-FIX",
});

const needsValue = (flag: string, hint = "<value>") => ({
  key: "input.t_flag_needs_value",
  cause: `argument ${flag}: expected one argument`,
  fix: `pass ${flag} ${hint}.`,
});

test("a short cluster is refused first, before anything else on the line", () => {
  assert.deepEqual(refusal(["-hx"]), unrecognizedFlag("-hx"));
  // ahead of an earlier unknown flag, a later help and a later missing value
  assert.deepEqual(refusal(["--nope", "-hx", "-h"]), unrecognizedFlag("-hx"));
  assert.deepEqual(refusal(["-hx", "--out"]), unrecognizedFlag("-hx"));
  assert.deepEqual(refusal(["-h=x"]), unrecognizedFlag("-h=x"));
  // a one-dash token with a space is a cluster too, as in Python's pre-scan
  assert.deepEqual(refusal(["-x y"]), unrecognizedFlag("-x y"));
});

test("the cluster pre-scan skips an option's value, stops at `--`, and yields to a missing value", () => {
  assert.equal(scanArgv(["--out", "-1"], G).values["--out"], "-1");
  assert.deepEqual(scanArgv(["--", "-hx"], G).positionals, ["-hx"]);
  assert.deepEqual(refusal(["--out", "-hx"]), needsValue("--out"));
  // a negative number is not a cluster
  assert.deepEqual(scanArgv(["-12"], G).positionals, ["-12"]);
  assert.deepEqual(scanArgv(["-1.5"], G).positionals, ["-1.5"]);
});

test("classification: a known name or name=value is an option; -, a negative number or a space is positional", () => {
  const scan = scanArgv(["--out=a", "-"], G);
  assert.equal(scan.values["--out"], "a");
  assert.deepEqual(scan.positionals, ["-"]);
  assert.deepEqual(scanArgv(["--x y"], G).positionals, ["--x y"]);
  assert.deepEqual(scanArgv(["-١"], G).positionals, ["-١"]);
  // an unknown name with = is an unknown flag, never a prefix of a known one
  assert.deepEqual(refusal(["--nope=1"]), unrecognizedFlag("--nope=1"));
  assert.deepEqual(refusal(["--ou", "o"]), unrecognizedFlag("--ou"));
});

test("`--` makes every later token positional", () => {
  const scan = scanArgv(["--out", "o", "--", "-h"], G);
  assert.equal(scan.help, false);
  assert.deepEqual(scan.positionals, ["-h"]);
  // the extra one after `--` is refused by its own spelling
  assert.deepEqual(refusal(["--", "r", "--out", "o"]), unrecognizedFlag("--out"));
});

test("a value comes from = (empty included) or the next token", () => {
  assert.equal(scanArgv(["--out="], G).values["--out"], "");
  assert.equal(scanArgv(["--out", "o"], G).values["--out"], "o");
  assert.equal(scanArgv(["--out", "-"], G).values["--out"], "-");
  assert.equal(scanArgv(["--fail-on", "-a b"], G).values["--fail-on"], "-a b");
  assert.equal(scanArgv(["--out=--json"], G).values["--out"], "--json");
});

test("a missing value is refused at once: at the end, before an option-like token, before `--`", () => {
  assert.deepEqual(refusal(["--out"]), needsValue("--out"));
  assert.deepEqual(refusal(["--out", "--json"]), needsValue("--out"));
  assert.deepEqual(refusal(["--out", "--nope"]), needsValue("--out"));
  assert.deepEqual(refusal(["--out", "--json=1"]), needsValue("--out"));
  assert.deepEqual(refusal(["--out", "--", "x"]), needsValue("--out"));
  assert.deepEqual(refusal(["--fail-on", "-h"]), needsValue("--fail-on", "<expression>"));
  assert.deepEqual(refusal(["--catalog-dir"]), needsValue("--catalog-dir"));
  // at once: ahead of an earlier unknown flag, which is deferred
  assert.deepEqual(refusal(["--nope", "--out"]), needsValue("--out"));
});

test("HELP returns at once, after a deferred unknown and before a later one", () => {
  for (const argv of [
    ["-h"],
    ["--help"],
    ["--nope", "-h"],
    ["-h", "--nope"],
    ["a", "b", "--help"],
  ]) {
    assert.equal(scanArgv(argv, G).help, true, argv.join(" "));
  }
  // a missing value before it is refused first
  assert.deepEqual(refusal(["--out", "-h"]), needsValue("--out"));
});

test("a FLAG or HELP given a value is refused at once, in Python's words", () => {
  assert.deepEqual(refusal(["--json=1"]), {
    key: "input.t_unrecognized_flag",
    cause: "flag '--json' takes no value.",
    fix: "drop the value: --json.",
  });
  assert.deepEqual(refusal(["--help=x", "--out"]), {
    key: "input.t_unrecognized_flag",
    cause: "argument -h/--help: ignored explicit argument 'x'.",
    fix: "FLAG-FIX",
  });
  assert.deepEqual(refusal(["--nope", "--json="]).cause, "flag '--json' takes no value.");
});

test("an unknown flag and an extra argument are deferred; the first in argv order is refused", () => {
  assert.deepEqual(refusal(["--nope", "a", "b"]), unrecognizedFlag("--nope"));
  assert.deepEqual(refusal(["a", "b", "--nope"]), {
    key: "input.t_unrecognized_flag",
    cause: "unrecognized argument 'b'.",
    fix: "EXTRA-FIX",
  });
  assert.deepEqual(refusal(["a", "-1"]), unrecognizedFlag("-1"));
  assert.deepEqual(refusal(["-x"]), unrecognizedFlag("-x"));
});

test("a VALUE's last occurrence wins; an APPEND keeps every value in order; a FLAG is recorded", () => {
  const scan = scanArgv(
    ["--out", "a", "--catalog-dir", "c1", "--out=b", "--catalog-dir=c2", "--json", "r"],
    G,
  );
  assert.deepEqual(scan.values, { "--out": "b" });
  assert.deepEqual(scan.lists, { "--catalog-dir": ["c1", "c2"] });
  assert.deepEqual([...scan.flags], ["--json"]);
  assert.deepEqual(scan.positionals, ["r"]);
  assert.equal(scan.help, false);
  assert.deepEqual(scanArgv([], G), {
    help: false,
    values: {},
    lists: {},
    flags: new Set(),
    positionals: [],
  });
});
