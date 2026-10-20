/**
 * Every catalog YAML file is one mapping read as Python's unique-key loader reads it (18.184i): anything else is
 * refused as input.catalog_unreadable naming the file (Python's tests/test_catalog_yaml_files.py, the same cases).
 */

import assert from "node:assert/strict";
import {
  cpSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { loadCatalog } from "./catalog";
import { InputError } from "./errors";

const REPO = join(__dirname, "..", "..", "..");
const BASE = join(REPO, "spec", "catalogs", "base", "eu-ai-act");
const CONTROL = "controls/DAT-01.yaml";
const SHAPE = "shapes/DAT-01.ttl";

function edit(dir: string, rel: string, old: string, replacement: string): void {
  const text = readFileSync(join(dir, rel), "utf-8");
  assert.ok(text.includes(old), old);
  writeFileSync(join(dir, rel), text.replace(old, replacement));
}

function append(dir: string, rel: string, tail: Buffer): void {
  writeFileSync(join(dir, rel), Buffer.concat([readFileSync(join(dir, rel)), tail]));
}

const NOT_YAML = "is not YAML the engine can load";
const NOT_MAPPING = "does not hold one YAML mapping";
const NOT_UTF8 = "is not valid UTF-8";

const CASES: Record<string, [string, (dir: string) => void, string]> = {
  "control key twice, the weaker last": [
    CONTROL,
    (d) =>
      edit(
        d,
        CONTROL,
        "  min_source_class: self_report\n",
        "  min_source_class: independent_system\n  min_source_class: self_report\n",
      ),
    NOT_YAML,
  ],
  "catalog.yaml key twice": [
    "catalog.yaml",
    (d) => edit(d, "catalog.yaml", 'version: "2026.09"\n', 'version: "2026.09"\nversion: "9"\n'),
    NOT_YAML,
  ],
  "catalog.yaml empty": [
    "catalog.yaml",
    (d) => writeFileSync(join(d, "catalog.yaml"), ""),
    NOT_MAPPING,
  ],
  "catalog.yaml a list": [
    "catalog.yaml",
    (d) => writeFileSync(join(d, "catalog.yaml"), "- id: eu-ai-act\n"),
    NOT_MAPPING,
  ],
  "control empty": [CONTROL, (d) => writeFileSync(join(d, CONTROL), ""), NOT_MAPPING],
  "control a list": [
    CONTROL,
    (d) => writeFileSync(join(d, CONTROL), "- id: DAT-01\n"),
    NOT_MAPPING,
  ],
  "control not UTF-8": [
    CONTROL,
    (d) => append(d, CONTROL, Buffer.from([0x23, 0x20, 0xff, 0x0a])),
    NOT_UTF8,
  ],
  "shape not UTF-8": [
    SHAPE,
    (d) => append(d, SHAPE, Buffer.from([0x23, 0x20, 0xff, 0x0a])),
    NOT_UTF8,
  ],
  "control syntax error": [
    CONTROL,
    (d) => writeFileSync(join(d, CONTROL), "id: [DAT-01\n"),
    NOT_YAML,
  ],
  "control nested too deeply": [
    CONTROL,
    (d) => writeFileSync(join(d, CONTROL), `x: ${"[".repeat(5000)}${"]".repeat(5000)}\n`),
    "is nested too deeply to read safely",
  ],
  "control python tag": [
    CONTROL,
    (d) => writeFileSync(join(d, CONTROL), "id: !!python/object:object {}\n"),
    NOT_YAML,
  ],
  "control second document": [
    CONTROL,
    (d) => append(d, CONTROL, Buffer.from("---\nid: DAT-99\n")),
    NOT_YAML,
  ],
};

for (const [name, [rel, change, problem]] of Object.entries(CASES)) {
  test(`a catalog file that cannot be loaded is refused naming it: ${name}`, () => {
    const tmp = mkdtempSync(join(tmpdir(), "agentce-catalog-yaml-"));
    const dir = join(tmp, "cat");
    try {
      cpSync(BASE, dir, { recursive: true });
      rmSync(join(dir, "catalog.sig.json"));
      change(dir);
      assert.throws(
        () => loadCatalog(dir),
        (err: unknown) => {
          assert.ok(err instanceof InputError);
          assert.equal(err.key, "input.catalog_unreadable");
          assert.ok(
            err.cause.includes(`holds a file that cannot be loaded: ${rel} ${problem}`),
            err.cause,
          );
          return true;
        },
      );
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
}

test("every shipped catalog still loads through the check (18.184i)", () => {
  const dirs: string[] = [];
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) {
        walk(path);
      } else if (name === "catalog.yaml") {
        dirs.push(dir);
      }
    }
  };
  walk(join(REPO, "spec", "catalogs"));
  walk(join(REPO, "engines", "typescript", "data", "catalogs"));
  assert.ok(dirs.length > 0);
  for (const dir of dirs) {
    assert.notEqual(loadCatalog(dir).version, "", dir);
  }
});
