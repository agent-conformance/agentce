/**
 * The scanner's ACTION level (18.111), against a grammar shaped like conformance's (a command level
 * of global flags and one action, `run`, with value options), and the adapters run `conformance run
 * --adapters` folds into the implementation report. Python's argparse subparsers and
 * `_adapter_conformance`/`_adapter_claim` are the reference; `ArgvActionTest.java` pins the same.
 */
import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { type Grammar, scanArgv } from "./argv";
import { quickstartDir } from "./bundled";
import { type AdapterRunner, adapterClaim, adapterConformance, runEcs } from "./conformance";
import { InputError } from "./errors";

const COMMON: Grammar["options"] = [
  { names: ["-h", "--help"], kind: "help" },
  { names: ["--json"], kind: "flag" },
];

const REFUSALS = {
  unrecognizedKey: "input.t_unrecognized_flag",
  needsValueKey: "input.t_unrecognized_flag",
  needsValueCause: "plain",
  flagFix: "FLAG-FIX",
  extraArgFix: "EXTRA-FIX",
} as const;

const RUN: Grammar = {
  options: [
    ...COMMON,
    { names: ["--engine"], kind: "value", needsValueKey: "input.engine_missing" },
    { names: ["--out"], kind: "value" },
  ],
  maxPositionals: 0,
  ...REFUSALS,
};

const G: Grammar = {
  options: COMMON,
  maxPositionals: 0,
  action: {
    grammars: { run: RUN },
    error: () => new InputError("input.t_action", "ACTION-CAUSE", "ACTION-FIX"),
  },
  ...REFUSALS,
};

function refusal(argv: string[], grammar: Grammar = G): { key: string; cause: string } {
  try {
    scanArgv(argv, grammar);
  } catch (exc) {
    assert.ok(exc instanceof InputError);
    return { key: exc.key, cause: exc.cause };
  }
  assert.fail(`no refusal for ${JSON.stringify(argv)}`);
}

const action = { key: "input.t_action", cause: "ACTION-CAUSE" };
const unknownFlag = (token: string) => ({
  key: "input.t_unrecognized_flag",
  cause: `unrecognized flag '${token}'.`,
});

test("an action that is not named is refused at once, before an earlier unknown flag", () => {
  assert.deepEqual(refusal(["nope"]), action);
  assert.deepEqual(refusal(["--he", "nope"]), action);
  // a flag of the action's, given before it, is unknown to the command: its value is the action
  assert.deepEqual(refusal(["--engine", "x", "run"]), action);
  // `--` names the action too, and no action is called `--`
  assert.deepEqual(refusal(["--", "run"]), action);
  // a negative number is a positional, so it is the action
  assert.deepEqual(refusal(["-1"]), action);
});

test("HELP before the action names the command's usage, after it the action's", () => {
  const before = scanArgv(["-h", "run"], G);
  assert.equal(before.help, true);
  assert.equal(before.action, undefined);
  const after = scanArgv(["run", "--engine", "e", "-h"], G);
  assert.equal(after.help, true);
  assert.equal(after.action, "run");
  // HELP in the action returns at once, past an earlier unknown flag of either level
  const past = scanArgv(["--he", "run", "--nope", "--help"], G);
  assert.equal(past.help, true);
  assert.equal(past.action, "run");
  assert.equal(scanArgv(["--he", "-h"], G).help, true);
});

test("the command level's unknown flag is refused before the action's, once the scan ends", () => {
  assert.deepEqual(refusal(["--he", "run", "--no-such"]), unknownFlag("--he"));
  assert.deepEqual(refusal(["run", "--no-such", "--other"]), unknownFlag("--no-such"));
  assert.deepEqual(refusal(["run", "run"]), {
    key: "input.t_unrecognized_flag",
    cause: "unrecognized argument 'run'.",
  });
  assert.deepEqual(refusal(["run", "--engine", "x", "--", "y"]), unknownFlag("--"));
  // the action's missing value is refused at once, ahead of an unknown flag of either level
  assert.deepEqual(refusal(["--he", "run", "--he", "--engine"]), {
    key: "input.engine_missing",
    cause: "flag '--engine' needs a value.",
  });
});

test("the cluster pre-scan stops at the action; the action's own pre-scan checks the rest", () => {
  assert.deepEqual(refusal(["-hx", "run"]), unknownFlag("-hx"));
  assert.deepEqual(refusal(["run", "-hx"]), unknownFlag("-hx"));
  // past the action, the command level's pre-scan does not reach `-hx`, so the unknown action is first
  assert.deepEqual(refusal(["nope", "-hx"]), action);
  // a cluster in the action's part is refused when the action is reached, ahead of the
  // command level's unknown flag
  assert.deepEqual(refusal(["--he", "run", "-hx"]), unknownFlag("-hx"));
});

test("a named action's scan joins the command's record", () => {
  const scan = scanArgv(["--json", "run", "--engine=e", "--out", "o", "--engine", "f"], G);
  assert.equal(scan.help, false);
  assert.equal(scan.action, "run");
  assert.deepEqual(scan.values, { "--engine": "f", "--out": "o" });
  assert.deepEqual([...scan.flags], ["--json"]);
  assert.deepEqual(scan.positionals, []);
  // no action at all: the command decides (conformance refuses with its action error)
  const bare = scanArgv(["--json"], G);
  assert.equal(bare.action, undefined);
  assert.equal("action" in bare, false);
});

test("a grammar with no action scans as before: positionals, `--`, and no action field", () => {
  const plain: Grammar = { ...RUN, maxPositionals: Number.POSITIVE_INFINITY };
  const scan = scanArgv(["a", "--out", "o", "b", "--", "-h", "c"], plain);
  assert.deepEqual(scan.positionals, ["a", "b", "-h", "c"]);
  assert.equal("action" in scan, false);
  assert.deepEqual(scanArgv(["run"], { ...RUN, maxPositionals: 1 }).positionals, ["run"]);
  // the cluster pre-scan runs past a positional when there is no action level
  assert.deepEqual(refusal(["x", "-hx"], { ...RUN, maxPositionals: 1 }), unknownFlag("-hx"));
  assert.deepEqual(refusal(["x"], RUN), {
    key: "input.t_unrecognized_flag",
    cause: "unrecognized argument 'x'.",
  });
});

/** A fake orchestrator: records how it was run and answers with `stdout`/`stderr`. */
function fakeRunner(
  stdout: string,
  stderr = "",
): {
  run: AdapterRunner;
  calls: Array<{ command: string; args: readonly string[]; cwd: string }>;
} {
  const calls: Array<{ command: string; args: readonly string[]; cwd: string }> = [];
  return {
    calls,
    run: (command, args, cwd) => {
      calls.push({ command, args, cwd });
      return { stdout, stderr };
    },
  };
}

const PASS = JSON.stringify({
  adapters: [{ name: "sample", identical: true }],
  total: 1,
  identical: 1,
  round_trip: true,
});

test("the adapters run is Python's command, in the adapters directory, given the absolute --out", () => {
  const s = fakeRunner(PASS);
  const detail = adapterConformance("/adapters", "out", s.run);
  assert.deepEqual(s.calls, [
    {
      command: "uv",
      args: ["run", "--quiet", "python", "conformance.py", "--json", "--out", resolve("out")],
      cwd: "/adapters",
    },
  ]);
  assert.equal(detail.total, 1);
  assert.equal(adapterClaim(detail), "full");
  adapterConformance("/adapters", null, s.run);
  assert.deepEqual(s.calls[1]?.args, ["run", "--quiet", "python", "conformance.py", "--json"]);
});

test("the adapters claim: full needs every adapter identical and the round trip", () => {
  assert.equal(adapterClaim({ total: 1, identical: 1, round_trip: true }), "full");
  assert.equal(adapterClaim({ total: 1, identical: 1, round_trip: false }), "partial");
  assert.equal(adapterClaim({ total: 2, identical: 1, round_trip: true }), "partial");
  assert.equal(adapterClaim({ total: 0, identical: 0, round_trip: true }), "none");
  assert.equal(adapterClaim({}), "none");
});

test("stdout that is not JSON becomes Python's error record: no adapters, claim none", () => {
  const fromStderr = adapterConformance("/a", null, fakeRunner("not json", "  boom\n").run);
  assert.deepEqual(fromStderr, {
    adapters: [],
    total: 0,
    identical: 0,
    round_trip: false,
    error: "boom",
  });
  assert.equal(adapterClaim(fromStderr), "none");
  // no stderr: the last 800 characters of stdout, stripped
  const long = `${"x".repeat(900)}y \n`;
  const fromStdout = adapterConformance("/a", null, fakeRunner(long).run);
  assert.equal(fromStdout.error, `${"x".repeat(799)}y`);
});

/** A one-project corpus (the vendored quickstart), whose ECS claim is full. */
function corpusOne(root: string): string {
  const corpus = join(root, "corpus");
  mkdirSync(join(corpus, "projects"), { recursive: true });
  cpSync(quickstartDir(), join(corpus, "projects", "q"), { recursive: true });
  writeFileSync(
    join(corpus, "corpus-manifest.json"),
    JSON.stringify({ corpus_version: "t", projects: [{ id: "q" }] }),
  );
  return corpus;
}

test("runEcs folds the fake runner's JSON into the report as adapter_conformance, claim full", () => {
  const root = mkdtempSync(join(tmpdir(), "agentce-ecs-adapters-"));
  try {
    const out = join(root, "out");
    const report = runEcs({
      enginePath: join(__dirname, ".."),
      corpusDir: corpusOne(root),
      outDir: out,
      adaptersDir: root,
      runAdapters: fakeRunner(PASS).run,
    });
    assert.equal(report.claim, "full");
    assert.equal(report.adapters, "full");
    assert.deepEqual(report.adapter_conformance, JSON.parse(PASS));
    const written = JSON.parse(readFileSync(join(out, "implementation-report.json"), "utf-8"));
    assert.equal(written.adapters, "full");
    assert.deepEqual(written.adapter_conformance, JSON.parse(PASS));
    // without --adapters the report carries neither key
    const bare = runEcs({
      enginePath: join(__dirname, ".."),
      corpusDir: corpusOne(join(root, "b")),
      outDir: null,
    });
    assert.equal("adapters" in bare, false);
    assert.equal("adapter_conformance" in bare, false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("runEcs with an orchestrator that prints no JSON: the error record, claim none", () => {
  const root = mkdtempSync(join(tmpdir(), "agentce-ecs-adapters-nojson-"));
  try {
    const report = runEcs({
      enginePath: join(__dirname, ".."),
      corpusDir: corpusOne(root),
      outDir: null,
      adaptersDir: root,
      runAdapters: fakeRunner("Traceback ...", "").run,
    });
    assert.equal(report.claim, "full");
    assert.equal(report.adapters, "none");
    assert.deepEqual(report.adapter_conformance, {
      adapters: [],
      total: 0,
      identical: 0,
      round_trip: false,
      error: "Traceback ...",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
