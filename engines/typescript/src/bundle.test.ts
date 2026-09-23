/**
 * Path confinement for bundle-adjacent references (SPEC §8.1).
 *
 * A manifest path is untrusted input: it must stay inside the bundle root *after symlinks resolve*,
 * not merely look relative. These tests build real temporary bundles -- with real symlinks, a real
 * symlink loop, and a path carrying an embedded NUL -- because the hazard is a filesystem behaviour
 * no in-memory fixture can reproduce.
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { confineToRoot, loadBundle, safeIsFile } from "./bundle";
import { AgentceError } from "./errors";

interface ManifestEntry {
  path: string;
  sha256: string;
}

function sha256File(contents: string): string {
  return createHash("sha256").update(Buffer.from(contents, "utf-8")).digest("hex");
}

function writeManifest(root: string, files: ManifestEntry[]): void {
  writeFileSync(join(root, "manifest.json"), JSON.stringify({ files }), "utf-8");
}

/** Run `body` against a fresh temporary directory, removed afterwards whatever happens. */
function withTempDir(body: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-bundle-"));
  try {
    body(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function inputErrorKey(fn: () => unknown): string {
  try {
    fn();
  } catch (exc) {
    assert.ok(exc instanceof AgentceError, `expected an AgentceError, got ${exc}`);
    return exc.key;
  }
  assert.fail("expected the bundle to be refused, but it loaded");
}

test("a manifest path that is a symlink escaping the bundle root is refused", () => {
  withTempDir((dir) => {
    const root = join(dir, "bundle");
    mkdirSync(join(root, "events"), { recursive: true });
    const contents = "outside the bundle root\n";
    const outside = join(dir, "outside.txt");
    writeFileSync(outside, contents, "utf-8");
    symlinkSync(outside, join(root, "events", "evil.jsonl"));
    writeManifest(root, [{ path: "events/evil.jsonl", sha256: sha256File(contents) }]);

    assert.equal(
      inputErrorKey(() => loadBundle(root)),
      "input.bundle_manifest_path",
    );
  });
});

test("a manifest path with a literal '..' segment is still refused", () => {
  withTempDir((dir) => {
    const root = join(dir, "bundle");
    mkdirSync(root, { recursive: true });
    const contents = "x\n";
    writeFileSync(join(dir, "outside.txt"), contents, "utf-8");
    writeManifest(root, [{ path: "../outside.txt", sha256: sha256File(contents) }]);

    assert.equal(
      inputErrorKey(() => loadBundle(root)),
      "input.bundle_manifest_path",
    );
  });
});

test("an absolute manifest path is still refused", () => {
  withTempDir((dir) => {
    const root = join(dir, "bundle");
    mkdirSync(root, { recursive: true });
    const contents = "x\n";
    const outside = join(dir, "outside.txt");
    writeFileSync(outside, contents, "utf-8");
    writeManifest(root, [{ path: outside, sha256: sha256File(contents) }]);

    assert.equal(
      inputErrorKey(() => loadBundle(root)),
      "input.bundle_manifest_path",
    );
  });
});

test("a symlink resolving inside the bundle root is accepted", () => {
  // Positive control: confinement checks the *resolved target*, not "is it a symlink".
  withTempDir((dir) => {
    const root = join(dir, "bundle");
    mkdirSync(join(root, "events"), { recursive: true });
    const contents = "{}\n";
    const real = join(root, "events", "real.jsonl");
    writeFileSync(real, contents, "utf-8");
    const link = join(root, "events", "alias.jsonl");
    symlinkSync(real, link);
    writeManifest(root, [{ path: "events/alias.jsonl", sha256: sha256File(contents) }]);

    const bundle = loadBundle(root);
    assert.deepEqual(bundle.eventFiles, [link]);
  });
});

test("a manifest path that is simply missing is a mismatch, not a confinement failure", () => {
  withTempDir((dir) => {
    const root = join(dir, "bundle");
    mkdirSync(join(root, "events"), { recursive: true });
    writeManifest(root, [{ path: "events/gone.jsonl", sha256: sha256File("{}\n") }]);

    assert.equal(
      inputErrorKey(() => loadBundle(root)),
      "input.bundle_manifest_mismatch",
    );
  });
});

test("confineToRoot refuses an empty, absolute, or '..'-bearing relative path", () => {
  withTempDir((dir) => {
    assert.equal(confineToRoot(dir, ""), null);
    assert.equal(confineToRoot(dir, "/etc/passwd"), null);
    assert.equal(confineToRoot(dir, "reference/../../secret.json"), null);
  });
});

test("confineToRoot returns a path that does not exist yet, for the caller to report", () => {
  withTempDir((dir) => {
    assert.equal(confineToRoot(dir, "reference/absent.json"), join(dir, "reference/absent.json"));
  });
});

test("confineToRoot handles a symlink loop without raising", () => {
  withTempDir((dir) => {
    symlinkSync(join(dir, "b"), join(dir, "a"));
    symlinkSync(join(dir, "a"), join(dir, "b"));

    assert.equal(confineToRoot(dir, "a"), null);
    assert.equal(safeIsFile(join(dir, "a")), false);
  });
});

test("confineToRoot handles an embedded NUL byte without raising", () => {
  withTempDir((dir) => {
    // A literal NUL byte would not survive the formatter, so build the name at runtime.
    const nul = String.fromCharCode(0);
    assert.equal(confineToRoot(dir, `evil${nul}name`), null);
  });
});

test("confineToRoot refuses a root that cannot itself be resolved", () => {
  withTempDir((dir) => {
    assert.equal(confineToRoot(join(dir, "no-such-root"), "events/e.jsonl"), null);
  });
});

test("safeIsFile is false for a directory and for a missing path", () => {
  withTempDir((dir) => {
    assert.equal(safeIsFile(dir), false);
    assert.equal(safeIsFile(join(dir, "absent")), false);
    writeFileSync(join(dir, "present"), "x", "utf-8");
    assert.equal(safeIsFile(join(dir, "present")), true);
  });
});
