/**
 * The no-learned-components check (SPEC §8.7, HR-1/HR-2): the repo-lockfile scan `evaluateNoMl`
 * exercises via `conformance run` (P2.7), and the installed-artifact self-report
 * `evaluateInstalledNoMl` (item 18.22) that `agentce version --json` uses -- an installed npm
 * package or jar has neither `pnpm-lock.yaml` nor `gradle.lockfile` on disk, so `version` needs a
 * self-report that works from what is actually resolvable at runtime instead.
 */

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { evaluateInstalledNoMl, evaluateNoMl, loadVendoredDenylist } from "./noMl";

const REPO = join(__dirname, "..", "..", "..");
const ENGINE_ROOT = join(__dirname, "..");

test("the vendored denylist is byte-identical to the authoritative copy", () => {
  const vendored = readFileSync(join(ENGINE_ROOT, "data", "no-ml-denylist.txt"), "utf-8");
  const authoritative = readFileSync(join(REPO, "spec", "rules", "no-ml-denylist.txt"), "utf-8");
  assert.equal(vendored, authoritative);
});

test("evaluateInstalledNoMl detects a real, always-bundled dependency (ajv) as present", () => {
  const result = evaluateInstalledNoMl(new Set(["ajv"]), ENGINE_ROOT);
  assert.equal(result.result, "fail");
  assert.deepEqual(result.denylisted_present, ["ajv"]);
});

test("evaluateInstalledNoMl with the real vendored denylist passes against the real installed engine", () => {
  const result = evaluateInstalledNoMl(loadVendoredDenylist(), ENGINE_ROOT);
  assert.equal(result.result, "pass");
  assert.deepEqual(result.denylisted_present, []);
});

test("evaluateInstalledNoMl passes for a name that is not installed at all", () => {
  const result = evaluateInstalledNoMl(new Set(["definitely-not-a-real-package-xyz"]), ENGINE_ROOT);
  assert.equal(result.result, "pass");
});

test("evaluateInstalledNoMl detects an ESM-only package (no CJS '.' export) without crashing", () => {
  // A plain `require.resolve(name)` throws `ERR_PACKAGE_PATH_NOT_EXPORTED` for a package present but
  // ESM-only; treating that as "absent" would produce a false `no_ml: pass` (a hard-invariant
  // violation, contracts/P18-18.22.md C1(d) note N1).
  const root = mkdtempSync(join(tmpdir(), "agentce-nomml-esm-"));
  try {
    const pkgDir = join(root, "node_modules", "esm-only-pkg");
    mkdirSync(pkgDir, { recursive: true });
    writeFileSync(
      join(pkgDir, "package.json"),
      JSON.stringify({ name: "esm-only-pkg", exports: { import: "./x.mjs" } }),
    );
    writeFileSync(join(pkgDir, "x.mjs"), "export default 1;\n");
    const result = evaluateInstalledNoMl(new Set(["esm-only-pkg"]), root);
    assert.equal(result.result, "fail");
    assert.deepEqual(result.denylisted_present, ["esm-only-pkg"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("evaluateInstalledNoMl detects a scoped denylist entry when only its scope directory is present", () => {
  const root = mkdtempSync(join(tmpdir(), "agentce-nomml-scope-"));
  try {
    mkdirSync(join(root, "node_modules", "@scope"), { recursive: true });
    const result = evaluateInstalledNoMl(new Set(["@scope/sub"]), root);
    assert.equal(result.result, "fail");
    assert.deepEqual(result.denylisted_present, ["@scope/sub"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("evaluateNoMl (the repo-lockfile scan) still passes over the engine's own lockfile", () => {
  const result = evaluateNoMl(REPO, join(ENGINE_ROOT, "pnpm-lock.yaml"));
  assert.equal(result.result, "pass");
  assert.deepEqual(result.violations, []);
  assert.ok(result.packages > 0);
});
