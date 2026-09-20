/**
 * The catalogs and the quickstart project the engine ships (`data/`) stay byte-identical to their
 * `spec/` and `corpus/` originals, and to the copies the Python and Java engines carry.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { test } from "node:test";

const ENGINE = join(__dirname, "..");
const REPO = join(ENGINE, "..", "..");

function files(root: string): Map<string, Buffer> {
  const found = new Map<string, Buffer>();
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir).sort()) {
      if (name === ".DS_Store" || name === "__pycache__") continue;
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else found.set(relative(root, path).split("\\").join("/"), readFileSync(path));
    }
  };
  walk(root);
  return found;
}

// (vendored tree, authoritative tree); the overlays tree carries a README the engine does not vendor.
const TREES: [string, string, boolean][] = [
  [join(ENGINE, "data", "catalogs", "base"), join(REPO, "spec", "catalogs", "base"), false],
  [join(ENGINE, "data", "catalogs", "overlays"), join(REPO, "spec", "catalogs", "overlays"), true],
  [join(ENGINE, "data", "corpus", "quickstart"), join(REPO, "corpus", "quickstart"), false],
];

for (const [vendored, authoritative, skipRootReadme] of TREES) {
  test(`the vendored ${relative(ENGINE, vendored)} tree is byte-identical to its original`, () => {
    assert.ok(existsSync(vendored), `${vendored} is missing`);
    const want = files(authoritative);
    if (skipRootReadme) want.delete("README.md");
    const got = files(vendored);
    assert.deepEqual([...got.keys()], [...want.keys()], "the two trees list different files");
    const drifted = [...want.keys()].filter(
      (name) => !got.get(name)?.equals(want.get(name) as Buffer),
    );
    assert.deepEqual(drifted, [], `vendored bytes drifted from ${relative(REPO, authoritative)}`);
  });
}

test("the vendored data is byte-identical to the Python engine's copy", () => {
  const python = join(REPO, "engines", "python", "agentce", "data");
  const pairs: [string, string][] = [
    [join(ENGINE, "data", "catalogs"), join(python, "catalogs")],
    [join(ENGINE, "data", "corpus"), join(python, "corpus")],
  ];
  for (const [ours, theirs] of pairs) {
    const a = files(ours);
    const b = files(theirs);
    assert.deepEqual([...a.keys()], [...b.keys()]);
    for (const [name, bytes] of a) assert.ok(bytes.equals(b.get(name) as Buffer), name);
  }
});
