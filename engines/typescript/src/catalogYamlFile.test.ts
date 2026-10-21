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
// 18.184o: a tag PyYAML's SafeLoader constructs but no engine reads in a catalog.
for (const [tag, value] of Object.entries({
  binary: "aGVsbG8=",
  set: "{a: null}",
  omap: "[{a: 1}]",
  pairs: "[{a: 1}]",
})) {
  CASES[`control !!${tag}`] = [
    CONTROL,
    (d) => edit(d, CONTROL, "id: DAT-01\n", `id: DAT-01\nextra: !!${tag} ${value}\n`),
    NOT_YAML,
  ];
}
// 18.184o: a merge source's tag is checked on its own, and a key it writes twice is refused.
for (const [name, merge] of Object.entries({
  "!!set": "!!set {min_source_class: self_report}",
  "!!omap": "!!omap [{min_source_class: self_report}]",
  "!!pairs": "!!pairs [{min_source_class: self_report}]",
  "!!set in a list": "[!!set {min_source_class: self_report}]",
  "!!merge": "!!merge {min_source_class: self_report}",
  "local tag": "!foo {min_source_class: self_report}",
  "repeated key": "{min_source_class: independent_system, min_source_class: self_report}",
  "repeated key in a list":
    "[{min_source_class: independent_system, min_source_class: self_report}]",
})) {
  CASES[`control merge source ${name}`] = [
    CONTROL,
    (d) => edit(d, CONTROL, "id: DAT-01\n", `id: DAT-01\nextra:\n  <<: ${merge}\n`),
    NOT_YAML,
  ];
}
CASES["catalog.yaml !!binary"] = [
  "catalog.yaml",
  (d) => edit(d, "catalog.yaml", "id: eu-ai-act\n", "id: eu-ai-act\nextra: !!binary aGVsbG8=\n"),
  NOT_YAML,
];

function withCatalog(body: (dir: string) => void): void {
  const tmp = mkdtempSync(join(tmpdir(), "agentce-catalog-yaml-"));
  const dir = join(tmp, "cat");
  try {
    cpSync(BASE, dir, { recursive: true });
    rmSync(join(dir, "catalog.sig.json"));
    body(dir);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

for (const [name, [rel, change, problem]] of Object.entries(CASES)) {
  test(`a catalog file that cannot be loaded is refused naming it: ${name}`, () => {
    withCatalog((dir) => {
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
    });
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

const FLOOR = "  min_source_class: self_report\n";

// 18.184o: a merge key in a control is read as PyYAML merges it (Python's
// test_a_merge_key_in_a_control_is_read_as_pyyaml_merges, the same cases): an explicit key beats a merged one
// wherever it is written, and the first source of a merge list wins.
const MERGES: Record<string, [string, string, string]> = {
  "inline mapping": ["", "  <<: {min_source_class: independent_system}\n", "independent_system"],
  alias: ["x-b: &b {min_source_class: independent_system}\n", "  <<: *b\n", "independent_system"],
  "list, stronger first": [
    "",
    "  <<: [{min_source_class: independent_system}, {min_source_class: self_report}]\n",
    "independent_system",
  ],
  "list, weaker first": [
    "",
    "  <<: [{min_source_class: self_report}, {min_source_class: independent_system}]\n",
    "self_report",
  ],
  "nested override": [
    "x-a: &a {min_source_class: self_report}\nx-b: &b {<<: *a, min_source_class: independent_system}\n",
    "  <<: *b\n",
    "independent_system",
  ],
  "explicit key after the merge": [
    "",
    `  <<: {min_source_class: independent_system}\n${FLOOR}`,
    "self_report",
  ],
  "explicit key before the merge": [
    "",
    "  min_source_class: independent_system\n  <<: {min_source_class: self_report}\n",
    "independent_system",
  ],
};

for (const [name, [head, floor, expected]] of Object.entries(MERGES)) {
  test(`a merge key in a control is read as PyYAML merges it: ${name}`, () => {
    withCatalog((dir) => {
      const text = readFileSync(join(dir, CONTROL), "utf-8");
      assert.ok(text.includes(FLOOR));
      writeFileSync(join(dir, CONTROL), head + text.replace(FLOOR, floor));
      const control = loadCatalog(dir).controls.find((c) => c.id === "DAT-01");
      assert.equal(control?.minSourceClass, expected);
    });
  });
}

test("a merged value that is not a source class is refused (18.184o)", () => {
  withCatalog((dir) => {
    edit(dir, CONTROL, FLOOR, "  <<: {min_source_class: trusted}\n");
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) =>
        err instanceof InputError && err.key === "catalog.control.min_source_class_unknown",
    );
  });
});

test("a merge key in catalog.yaml is read (18.184o)", () => {
  withCatalog((dir) => {
    edit(dir, "catalog.yaml", 'version: "2026.09"\n', '<<: {version: "2026.09"}\n');
    assert.equal(loadCatalog(dir).version, "2026.09");
  });
});

test("explicit core tags stay readable (18.184o)", () => {
  withCatalog((dir) => {
    edit(dir, CONTROL, "id: DAT-01\n", "id: !!str DAT-01\nsince: !!timestamp 2026-01-02\n");
    assert.ok(loadCatalog(dir).controls.some((c) => c.id === "DAT-01"));
  });
});
