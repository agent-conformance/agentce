/**
 * Every catalog YAML file is one mapping read as Python's loader reads it, under the SPEC 8.7 YAML profile
 * (18.184i, 18.184u): anything else is refused as input.catalog_unreadable naming the file (Python's tests/test_catalog_yaml_files.py, the same cases).
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
/** The SPEC 8.7 YAML profile's refusal naming `rule` (18.184u); the line follows. */
const outside = (rule: string): string => `is outside the YAML profile: ${rule} at line `;

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
    outside("duplicate_key"),
  ],
  "catalog.yaml key twice": [
    "catalog.yaml",
    (d) => edit(d, "catalog.yaml", 'version: "2026.09"\n', 'version: "2026.09"\nversion: "9"\n'),
    outside("duplicate_key"),
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
    outside("depth_over_100"),
  ],
  "control python tag": [
    CONTROL,
    (d) => writeFileSync(join(d, CONTROL), "id: !!python/object:object {}\n"),
    outside("tag"),
  ],
  "control second document": [
    CONTROL,
    (d) => append(d, CONTROL, Buffer.from("---\nid: DAT-99\n")),
    outside("second_document"),
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
    outside("tag"),
  ];
}
// 18.184o: a merge source's tag is checked on its own, and a key it writes twice is refused; under the YAML profile
// (18.184u) the `<<` key itself is refused first.
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
    outside("merge_key"),
  ];
}
CASES["catalog.yaml !!binary"] = [
  "catalog.yaml",
  (d) => edit(d, "catalog.yaml", "id: eu-ai-act\n", "id: eu-ai-act\nextra: !!binary aGVsbG8=\n"),
  outside("tag"),
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

/** `loadCatalog(dir)` refused as input.catalog_unreadable naming `rel` as outside the YAML profile under `rule`. */
function assertOutside(dir: string, rel: string, rule: string): void {
  assert.throws(
    () => loadCatalog(dir),
    (err: unknown) => {
      assert.ok(err instanceof InputError);
      assert.equal(err.key, "input.catalog_unreadable");
      assert.ok(err.cause.includes(`cannot be loaded: ${rel} ${outside(rule)}`), err.cause);
      return true;
    },
  );
}

// 18.184o read a merge key in a control as PyYAML merges it; under the YAML profile (18.184u) the merge key, and
// the anchor or alias that feeds it, is refused instead (the first one written).
const MERGES: Record<string, [string, string, string]> = {
  "inline mapping": ["", "  <<: {min_source_class: independent_system}\n", "merge_key"],
  alias: ["x-b: &b {min_source_class: independent_system}\n", "  <<: *b\n", "anchor"],
  "list, stronger first": [
    "",
    "  <<: [{min_source_class: independent_system}, {min_source_class: self_report}]\n",
    "merge_key",
  ],
  "list, weaker first": [
    "",
    "  <<: [{min_source_class: self_report}, {min_source_class: independent_system}]\n",
    "merge_key",
  ],
  "nested override": [
    "x-a: &a {min_source_class: self_report}\nx-b: &b {<<: *a, min_source_class: independent_system}\n",
    "  <<: *b\n",
    "anchor",
  ],
  "explicit key after the merge": [
    "",
    `  <<: {min_source_class: independent_system}\n${FLOOR}`,
    "merge_key",
  ],
  "explicit key before the merge": [
    "",
    "  min_source_class: independent_system\n  <<: {min_source_class: self_report}\n",
    "merge_key",
  ],
};

for (const [name, [head, floor, rule]] of Object.entries(MERGES)) {
  test(`a merge key in a control is refused by the YAML profile: ${name}`, () => {
    withCatalog((dir) => {
      const text = readFileSync(join(dir, CONTROL), "utf-8");
      assert.ok(text.includes(FLOOR));
      writeFileSync(join(dir, CONTROL), head + text.replace(FLOOR, floor));
      assertOutside(dir, CONTROL, rule);
    });
  });
}

test("a merged value is refused by the YAML profile before its source class is read (18.184u)", () => {
  withCatalog((dir) => {
    edit(dir, CONTROL, FLOOR, "  <<: {min_source_class: trusted}\n");
    assertOutside(dir, CONTROL, "merge_key");
  });
});

test("a merge key in catalog.yaml is refused by the YAML profile (18.184u)", () => {
  withCatalog((dir) => {
    edit(dir, "catalog.yaml", 'version: "2026.09"\n', '<<: {version: "2026.09"}\n');
    assertOutside(dir, "catalog.yaml", "merge_key");
  });
});

test("explicit core tags are refused by the YAML profile (18.184u)", () => {
  withCatalog((dir) => {
    edit(dir, CONTROL, "id: DAT-01\n", "id: !!str DAT-01\nsince: !!timestamp 2026-01-02\n");
    assertOutside(dir, CONTROL, "tag");
  });
});
