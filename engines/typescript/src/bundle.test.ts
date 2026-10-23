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
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  bundleLimits,
  bundleReadHooks,
  confineToRoot,
  loadBundle,
  readConfined,
  safeIsFile,
} from "./bundle";
import { AgentceError } from "./errors";
import { ingest } from "./ingest";

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

/** A valid bundle at `<dir>/b` whose manifest.json is moved and linked back per `kind` (18.183);
 * returns the folder to load. */
function placeManifest(dir: string, kind: string): string {
  const root = join(dir, "b");
  mkdirSync(join(root, "events"), { recursive: true });
  writeFileSync(join(root, "events", "s.jsonl"), "{}\n", "utf-8");
  writeManifest(root, [{ path: "events/s.jsonl", sha256: sha256File("{}\n") }]);
  const manifest = join(root, "manifest.json");
  const out = join(dir, "out");
  mkdirSync(out);
  if (kind === "plain") {
    return root;
  }
  if (kind === "root_link") {
    symlinkSync(root, join(dir, "rl"));
    return join(dir, "rl");
  }
  if (kind === "dangling") {
    unlinkSync(manifest);
    symlinkSync(join(out, "none.json"), manifest);
    return root;
  }
  const targets: Record<string, [string, string]> = {
    outside: ["../out/manifest.json", join(out, "manifest.json")],
    inside: ["meta/m.json", join(root, "meta", "m.json")],
    folder_link_out: ["meta/m.json", join(out, "m.json")],
    link_to_link_out: ["inner", join(out, "m.json")],
    out_and_back: ["../b/m.json", join(root, "m.json")],
    sibling_prefix: ["../b-evil/manifest.json", join(dir, "b-evil", "manifest.json")],
  };
  const [link, target] = targets[kind];
  mkdirSync(join(target, ".."), { recursive: true });
  renameSync(manifest, target);
  if (kind === "folder_link_out") {
    symlinkSync(out, join(root, "meta"));
  }
  if (kind === "link_to_link_out") {
    symlinkSync(target, join(root, "inner"));
  }
  symlinkSync(link, manifest);
  return root;
}

for (const kind of ["outside", "folder_link_out", "link_to_link_out", "sibling_prefix"]) {
  test(`a manifest.json that resolves outside the bundle is refused (${kind})`, () => {
    withTempDir((dir) => {
      assert.equal(
        inputErrorKey(() => loadBundle(placeManifest(dir, kind))),
        "input.bundle_manifest_path",
      );
    });
  });
}

for (const kind of ["plain", "inside", "root_link", "out_and_back"]) {
  test(`a manifest.json that resolves inside the bundle loads (${kind})`, () => {
    withTempDir((dir) => {
      assert.ok(Array.isArray(loadBundle(placeManifest(dir, kind)).manifest.files));
    });
  });
}

test("a manifest.json that is a dangling link is still missing", () => {
  withTempDir((dir) => {
    assert.equal(
      inputErrorKey(() => loadBundle(placeManifest(dir, "dangling"))),
      "input.bundle_manifest_missing",
    );
  });
});

/** Swap `target` for a link to an outside copy of its own bytes at the `when` seam, once (18.184f). */
function swapAt(when: "afterConfine" | "afterCheck", target: string, outside: string): string[] {
  const swapped: string[] = [];
  bundleReadHooks[when] = (rel, member) => {
    if (rel === target && swapped.length === 0) {
      writeFileSync(outside, readFileSync(member));
      unlinkSync(member);
      symlinkSync(outside, member);
      swapped.push(rel);
    }
  };
  return swapped;
}

test("bundle member swapped after confinement", () => {
  const event = '{"id":"e1"}\n';
  const fresh = (dir: string, name: string): string => {
    const root = join(dir, name);
    mkdirSync(join(root, "events"), { recursive: true });
    writeFileSync(join(root, "events", "a.jsonl"), event, "utf-8");
    writeManifest(root, [{ path: "events/a.jsonl", sha256: sha256File(event) }]);
    return root;
  };
  withTempDir((dir) => {
    try {
      // Swapped after the check and before the open: refused, for a member and for manifest.json alike.
      for (const target of ["events/a.jsonl", "manifest.json"]) {
        const root = fresh(dir, `b-${target.replace("/", "-")}`);
        const swapped = swapAt(
          "afterConfine",
          target,
          join(dir, `outside-${target.replace("/", "-")}`),
        );
        let message = "";
        assert.equal(
          inputErrorKey(() => {
            try {
              loadBundle(root);
            } catch (exc) {
              message = String((exc as Error).message);
              throw exc;
            }
          }),
          "input.bundle_manifest_path",
        );
        assert.deepEqual(swapped, [target]);
        assert.match(message, new RegExp(`${target} changed while it was being read`));
        bundleReadHooks.afterConfine = undefined;
      }
      // Swapped once the open file is checked: the bytes come from the checked file, never the outside one.
      const root = fresh(dir, "b-after-check");
      writeFileSync(join(dir, "elsewhere"), "outside bytes\n", "utf-8");
      bundleReadHooks.afterCheck = (rel, member) => {
        unlinkSync(member);
        symlinkSync(join(dir, "elsewhere"), member);
      };
      const member = join(root, "events", "a.jsonl");
      assert.equal(readConfined(root, "events/a.jsonl", member).toString("utf-8"), event);
      bundleReadHooks.afterCheck = undefined;
      // A member rewritten after loadBundle never reaches ingest: it reads the checked bytes.
      const again = fresh(dir, "b-loaded");
      const loaded = loadBundle(again);
      const before = JSON.stringify(ingest(loaded));
      writeFileSync(join(again, "events", "a.jsonl"), "not json\n", "utf-8");
      assert.equal(JSON.stringify(ingest(loaded)), before);
    } finally {
      bundleReadHooks.afterConfine = undefined;
      bundleReadHooks.afterCheck = undefined;
    }
  });
});

/** The folder `dir` spelled in upper case, or null where the filesystem tells names apart by case
 * (KD-441: the case-variant cases discriminate only on a case-insensitive filesystem). */
function folded(dir: string): string | null {
  const upper = dir.toUpperCase();
  return upper !== dir && existsSync(upper) ? upper : null;
}

test("confineToRoot judges a link spelled with the bundle in another case by folder identity (18.184l)", (t) => {
  withTempDir((dir) => {
    const bundle = join(dir, "bundle");
    mkdirSync(join(bundle, "moved-inside"), { recursive: true });
    mkdirSync(join(dir, "bundle2"));
    writeFileSync(join(bundle, "moved-inside", "x.jsonl"), "{}\n");
    writeFileSync(join(dir, "bundle2", "x.jsonl"), "{}\n");
    writeFileSync(join(dir, "up.jsonl"), "{}\n");
    const upper = folded(bundle);
    if (upper === null) {
      t.skip("needs a case-insensitive filesystem");
      return;
    }
    symlinkSync(join(upper, "moved-inside", "x.jsonl"), join(bundle, "inside.jsonl"));
    symlinkSync(join(dir, "bundle2", "x.jsonl").toUpperCase(), join(bundle, "sibling.jsonl"));
    symlinkSync(join(dir, "up.jsonl").toUpperCase(), join(bundle, "parent.jsonl"));
    assert.equal(confineToRoot(bundle, "inside.jsonl"), join(bundle, "inside.jsonl"));
    assert.equal(confineToRoot(bundle, "sibling.jsonl"), null);
    assert.equal(confineToRoot(bundle, "parent.jsonl"), null);
  });
});

test("manifest.json unreadable at the read is input.bundle_unreadable, never not valid JSON", () => {
  // Census odd-inputs line 90 (18.184r): readable at the pre-check, refused at the read; one key per cause.
  withTempDir((dir) => {
    const root = join(dir, "b");
    mkdirSync(join(root, "events"), { recursive: true });
    writeFileSync(join(root, "events", "a.jsonl"), '{"id":"e1"}\n', "utf-8");
    writeManifest(root, [{ path: "events/a.jsonl", sha256: sha256File('{"id":"e1"}\n') }]);
    bundleReadHooks.afterConfine = (rel, member) => {
      if (rel === "manifest.json") {
        chmodSync(member, 0o000);
      }
    };
    let message = "";
    try {
      assert.equal(
        inputErrorKey(() => {
          try {
            loadBundle(root);
          } catch (exc) {
            message = String((exc as Error).message);
            throw exc;
          }
        }),
        "input.bundle_unreadable",
      );
      assert.match(message, /cannot be read: manifest\.json\./);
    } finally {
      bundleReadHooks.afterConfine = undefined;
      chmodSync(join(root, "manifest.json"), 0o644);
    }
  });
});

/** Run `body` with the per-file limit lowered to `limit` bytes and the read seams cleared afterwards. */
function withLimit(limit: number, body: () => void): void {
  const saved = bundleLimits.maxManifestFileBytes;
  bundleLimits.maxManifestFileBytes = limit;
  try {
    body();
  } finally {
    bundleLimits.maxManifestFileBytes = saved;
    bundleReadHooks.afterConfine = undefined;
  }
}

function sizedBundle(dir: string, contents: string): string {
  const root = join(dir, "b");
  mkdirSync(join(root, "events"), { recursive: true });
  writeFileSync(join(root, "events", "a.jsonl"), contents, "utf-8");
  writeManifest(root, [{ path: "events/a.jsonl", sha256: sha256File(contents) }]);
  return root;
}

test("a manifest member exactly at the per-file limit is read", () => {
  // Census odd-inputs line 91 (18.184r): Python's order, the size from the stat, then a bounded read.
  withTempDir((dir) => {
    const root = sizedBundle(dir, "0123456\n");
    withLimit(8, () => {
      assert.equal(loadBundle(root).eventFiles.length, 1);
    });
  });
});

test("a manifest member over the per-file limit is refused from the stat, never read", () => {
  withTempDir((dir) => {
    const root = sizedBundle(dir, "0123456789\n");
    const read: string[] = [];
    let message = "";
    withLimit(8, () => {
      bundleReadHooks.afterConfine = (rel) => {
        read.push(rel);
      };
      assert.equal(
        inputErrorKey(() => {
          try {
            loadBundle(root);
          } catch (exc) {
            message = String((exc as Error).message);
            throw exc;
          }
        }),
        "input.bundle_manifest_file_too_large",
      );
    });
    assert.deepEqual(read, ["manifest.json"]);
    assert.match(message, /events\/a\.jsonl is 11 bytes, over the 8-byte per-file limit\.$/);
  });
});

test("a manifest member that grows past the per-file limit during the read is refused with the size key", () => {
  withTempDir((dir) => {
    const root = sizedBundle(dir, "0123\n");
    withLimit(8, () => {
      bundleReadHooks.afterConfine = (rel, member) => {
        if (rel === "events/a.jsonl") {
          writeFileSync(member, "0123456789abcdef\n", "utf-8");
        }
      };
      assert.equal(
        inputErrorKey(() => loadBundle(root)),
        "input.bundle_manifest_file_too_large",
      );
    });
  });
});
