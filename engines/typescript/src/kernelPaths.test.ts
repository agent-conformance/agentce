/**
 * A path through a symlink is read where the kernel reads it: `lnk/..` is the parent of the link's
 * target, never the folder the link sits in (SPEC §8.7; item 18.182). Each case puts a decoy at the
 * lexical location, so an engine that folds `..` as text reads or writes the decoy. Mirrors Python's
 * tests/test_kernel_paths.py.
 */
import assert from "node:assert/strict";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { resolveLoose } from "./bundle";
import { quickstartDir } from "./bundled";
import { type AdapterRunner, adapterConformance, runEcs } from "./conformance";
import { runJson } from "./testSupport";
import { joinPath } from "./util";

const QS = quickstartDir();
const ASSESS = [
  "assess",
  "--bundle",
  join(QS, "evidence"),
  "--profile",
  join(QS, "applicability.yaml"),
  "--domain",
  join(QS, "domain.linkml.yaml"),
];

/** `w/lnk -> w/far/deep`, so the kernel reads `w/lnk/..` as `w/far`; `body` gets the real `w`. */
function withLink(body: (w: string) => void): void {
  const w = realpathSync(mkdtempSync(join(tmpdir(), "agentce-kernel-paths-")));
  const cwd = process.cwd();
  try {
    mkdirSync(join(w, "far", "deep"), { recursive: true });
    symlinkSync(join(w, "far", "deep"), join(w, "lnk"));
    body(w);
  } finally {
    process.chdir(cwd);
    rmSync(w, { recursive: true, force: true });
  }
}

test("joinPath keeps every `..` and otherwise gives node's join", () => {
  assert.equal(joinPath("/w/lnk/..", "target"), "/w/lnk/../target");
  assert.equal(joinPath("lnk", "..", "x/"), "lnk/../x");
  for (const parts of [
    ["/a", "b"],
    ["a/", "./b", "c"],
    ["/", "a"],
    ["a", ""],
    ["./a//b", "c.json"],
  ]) {
    assert.equal(joinPath(...parts), join(...parts), parts.join(" + "));
  }
});

test("resolveLoose walks a relative `lnk/..` from the working directory after the link", () => {
  withLink((w) => {
    process.chdir(w);
    assert.equal(resolveLoose("lnk/../x"), join(w, "far", "x"));
    assert.equal(resolveLoose(`${w}/lnk/../x`), join(w, "far", "x"));
  });
});

test("validate --bundle lnk/../target reads the kernel's folder, not the decoy", () => {
  withLink((w) => {
    cpSync(join(QS, "evidence"), join(w, "far", "target"), { recursive: true });
    writeFileSync(join(w, "target"), "decoy"); // a file where `..` folded as text would land
    const { exitCode, envelope } = runJson(["validate", "--bundle", `${w}/lnk/../target`]);
    // the quickstart evidence holds two records it quarantines on purpose: exit 1, never 3
    assert.equal(exitCode, 1, JSON.stringify(envelope));
    assert.equal(envelope.accepted, 6);
  });
});

test("assess --out and readiness through lnk/.. land in the kernel's folder, absolute and relative", () => {
  withLink((w) => {
    mkdirSync(join(w, "target")); // the decoy: must stay empty
    const out = runJson([...ASSESS, "--out", `${w}/lnk/../target`]);
    assert.equal(out.exitCode, 0, JSON.stringify(out.envelope));
    assert.ok(existsSync(join(w, "far", "target", "assertions.json")));
    const ready = runJson(["readiness", `${w}/lnk/../target`]);
    assert.ok([0, 1].includes(ready.exitCode), JSON.stringify(ready.envelope));
    assert.ok(readdirSync(join(w, "far", "target")).some((n) => n.startsWith("report-readiness-")));

    process.chdir(w);
    const rel = runJson([...ASSESS, "--out", "lnk/../rel"]);
    assert.equal(rel.exitCode, 0, JSON.stringify(rel.envelope));
    assert.ok(existsSync(join(w, "far", "rel", "assertions.json")));
    assert.ok(!existsSync(join(w, "rel")));
    assert.deepEqual(readdirSync(join(w, "target")), []);
  });
});

test("report --out x.md/ writes x.md and echoes the path as typed", () => {
  withLink((w) => {
    const from = join(w, "qs");
    assert.equal(runJson([...ASSESS, "--out", from]).exitCode, 0);
    const typed = join(w, "x.md/");
    const { exitCode, envelope } = runJson([
      "report",
      "--from",
      join(from, "assertions.json"),
      "--format",
      "md",
      "--out",
      typed,
    ]);
    assert.equal(exitCode, 0, JSON.stringify(envelope));
    assert.ok(existsSync(join(w, "x.md")));
    assert.ok(JSON.stringify(envelope).includes(JSON.stringify(typed)));
  });
});

test("conformance run resolves a symlinked engine and corpus to the real folders", () => {
  withLink((w) => {
    symlinkSync(resolve(__dirname, ".."), join(w, "engine")); // engines/typescript
    mkdirSync(join(w, "corpus"));
    writeFileSync(join(w, "corpus", "corpus-manifest.json"), '{"projects": []}');
    symlinkSync(join(w, "corpus"), join(w, "corpus-link"));
    // the catalogs are found only from the real engine's grandparent, never the link's
    const report = runEcs({
      enginePath: join(w, "engine"),
      corpusDir: join(w, "corpus-link"),
      outDir: null,
    });
    assert.deepEqual(report.failures, []);
  });
});

test("the adapters' orchestrator is given the kernel's --out, absolute and relative", () => {
  withLink((w) => {
    const seen: string[] = [];
    const run: AdapterRunner = (_command, args) => {
      seen.push(args[args.indexOf("--out") + 1]);
      return { stdout: "{}", stderr: "" };
    };
    adapterConformance(w, `${w}/lnk/../cout`, run);
    process.chdir(w);
    adapterConformance(w, "lnk/../rcout", run);
    assert.deepEqual(seen, [join(w, "far", "cout"), join(w, "far", "rcout")]);
  });
});
