/**
 * The adapters run `conformance run --adapters` folds into the implementation report (18.111).
 * Python's `_adapter_conformance`/`_adapter_claim` are the reference; `ArgvActionTest.java` pins the
 * same. The action level of the command-line grammar is pinned in `argv.test.ts`.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { quickstartDir } from "./bundled";
import { type AdapterRunner, adapterClaim, adapterConformance, runEcs } from "./conformance";
import { InputError } from "./errors";

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

test("the adapters' counts are read as Python's int() reads them, from the orchestrator's exact JSON", () => {
  const claimOf = (stdout: string) =>
    adapterClaim(adapterConformance("/adapters", null, fakeRunner(stdout).run));
  const big =
    '{"total": 18446744073709551617, "identical": 18446744073709551616, "round_trip": true}';
  assert.equal(claimOf(big), "partial");
  assert.equal(
    claimOf(
      '{"total": 18446744073709551617, "identical": 18446744073709551617, "round_trip": true}',
    ),
    "full",
  );
  assert.equal(claimOf('{"total": 1.9, "identical": 1, "round_trip": true}'), "full");
  assert.equal(claimOf('{"total": " 1_0 ", "identical": 10, "round_trip": true}'), "full");
  assert.equal(claimOf('{"total": true, "identical": true, "round_trip": 1}'), "full");
  assert.equal(claimOf('{"total": 1, "identical": 1, "round_trip": 0.0}'), "partial");
  assert.equal(claimOf('{"total": 1, "identical": 1, "round_trip": NaN}'), "full");
  for (const bad of ['"1.0"', '"1e0"', "null", "1e400", "NaN", "[]"]) {
    assert.throws(
      () => claimOf(`{"total": ${bad}, "identical": 1, "round_trip": true}`),
      Error,
      bad,
    );
  }
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

test("runEcs refuses the run when a project report under --out cannot be written (18.184d)", () => {
  const root = mkdtempSync(join(tmpdir(), "agentce-ecs-fifo-"));
  try {
    const out = join(root, "out");
    mkdirSync(join(out, "projects", "q"), { recursive: true });
    execFileSync("mkfifo", [join(out, "projects", "q", "report.md")]);
    assert.throws(
      () => runEcs({ enginePath: join(__dirname, ".."), corpusDir: corpusOne(root), outDir: out }),
      (err: unknown) =>
        err instanceof InputError &&
        err.key === "input.out_dir_unwritable" &&
        err.message.includes("cannot be written"),
    );
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
