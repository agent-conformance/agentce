/**
 * The PyYAML rules js-yaml does not keep, for the domain binding, the applicability profile and the deviation
 * register (18.184p): the scanner's tab, flow `?` and empty flow key refusals, Python's key equality for a repeated
 * key, the Unicode 15.0 digit table, the profile read as Python reads it, and a list `control` named as Python prints
 * it. Java's `UntrustedYamlFormsTest` holds the same cases.
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { InputError } from "./errors";
import { loadProfile } from "./profile";
import { scannerProblem } from "./pyyamlScanner";
import { deviationLint, pyyamlLoad } from "./readiness";

function withFile(text: string, read: (path: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-yaml-"));
  try {
    const path = join(dir, "p.yaml");
    writeFileSync(path, text);
    read(path);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("the scanner refuses what PyYAML's scanner refuses", () => {
  for (const text of [
    "a: [a?, b]\n",
    "a: [a ?, b]\n",
    "a: {a?: 1}\n",
    "a: [a ?]\n",
    "a: [a?b]\n",
    'a: [lookup ?,\n "yes"]\n',
    "a: b\t\n",
    "a: 'b'\t\n",
    "a: [b]\t\n",
    "a\t: b\n",
    "a:\tb\n",
    "-\ta\n",
    "a: |\t\n  x\n",
    "a: [b,\tc]\n",
    "a: b\t# c\n",
    "a: {: 1}\n",
    "a: [: 1]\n",
    "a: {a: 1, : 2}\n",
  ]) {
    assert.throws(() => pyyamlLoad(text), Error, text);
  }
  for (const text of [
    "a: a?\n",
    'a: ["a?", b]\n',
    "a: [? a]\n",
    'a: "b\tc"\n',
    "a: b # c\td\n",
    "a: |\n  x\ty\n",
    "a: [b, c]\n",
    "a: {b: c,\n  d: e}\n",
    "? [a, b]\n: c\n",
    "a: &x {k: v}\nb: *x\n",
  ]) {
    assert.equal(scannerProblem(text), null, text);
  }
});

test("a repeated key is decided by Python's key equality", () => {
  for (const text of [
    "{1: a, 1.0: b}",
    "{1: a, true: b}",
    "{0: a, false: b}",
    "{0x1: a, 1: b}",
    "{null: a, ~: b}",
    "{.nan: a, .nan: b}",
    "{2026-01-01: a, 2026-01-01: b}",
    "{2026-01-01T00:00:00Z: a, 2026-01-01T01:00:00+01:00: b}",
    "a: 1\nb: 2\na: 3",
  ]) {
    assert.throws(() => pyyamlLoad(`${text}\n`, true), /found the key .* twice/, text);
  }
  for (const text of [
    '{"1": a, 1: b}',
    '{null: a, "null": b}',
    "{2026-01-01: a, 2026-01-02: b}",
    '{2026-01-01: a, "2026-01-01": b}',
    "{1.5: a, 15e-1: b}",
    "{a: &x v, b: &y v}",
  ]) {
    pyyamlLoad(`${text}\n`, true);
  }
});

test("a flow `?` or `:` is an indicator, as PyYAML's scanner reads it", () => {
  for (const [text, value] of [
    ["a: {?b: 1}\n", { a: { b: 1 } }],
    ["a: {? b: 1}\n", { a: { b: 1 } }],
    ["a: [?b]\n", { a: [{ b: null }] }],
    ["a: [c, ?b: d]\n", { a: ["c", { b: "d" }] }],
    ["a: {?b}\n", { a: { b: null } }],
    ["a: {?: 1}\n", { a: { null: 1 } }],
    ["a: [?]\n", { a: [{ null: null }] }],
    ['a: ["?b", c]\n', { a: ["?b", "c"] }],
    ["?b: 1\n", { "?b": 1 }],
    [
      "a: [\u{10000}, ?b, \u{10000}, ?c]\n",
      { a: ["\u{10000}", { b: null }, "\u{10000}", { c: null }] },
    ],
    ["a: {?:1}\n", { a: { null: 1 } }],
    ['a: {"b":1}\n', { a: { b: 1 } }],
    ["a: {b:1}\n", { a: { "b:1": null } }],
    ["a: [c, &d :e]\n", { a: ["c", { null: "e" }] }],
    ["a: {?:, b: 2}\n", { a: { null: null, b: 2 } }],
  ] as const) {
    assert.deepEqual(pyyamlLoad(text), value, text);
  }
  for (const text of [
    "a: {?:1, ?:2}\n",
    "a: {? :1, ? :2}\n",
    "a: {&b :1, &c :2}\n",
    "a: {!!str :1, !!str :2}\n",
  ]) {
    assert.throws(() => pyyamlLoad(text, true), /found the key .* twice/, text);
  }
  assert.throws(() => pyyamlLoad("a: {b: 1, ?b: 2}\n", true), /found the key .* twice/);
  assert.deepEqual(pyyamlLoad("a: {b: 1, ?b: 2}\n"), { a: { b: 2 } });
});

test("a long flow list is read, never a stack overflow", () => {
  const items = Array.from({ length: 200000 }, (_, i) => i);
  assert.equal(scannerProblem(`a: [${items.join(",")}]\n`), null);
  assert.equal(scannerProblem(`%${"X".repeat(300000)} a\n---\nb: 1\n`), null);
});

test("an explicit merge key and a later one read as PyYAML reads them", () => {
  assert.deepEqual(pyyamlLoad("? <<\n: {a: 1}\n<<: {a: 2}\n"), { a: 2 });
  assert.deepEqual(pyyamlLoad("<<: {a: 2}\n? <<\n: {a: 1, b: 1}\nb: 0\n"), { a: 1, b: 0 });
});

test("only a Unicode 15.0 digit reads as a digit", () => {
  assert.deepEqual(pyyamlLoad('a: !!int "١٠"\n'), { a: 10 });
  for (const text of ['a: !!int "𐵁𐵀"\n', 'a: !!float "𐵁.5"\n', 'a: !!int "𖵱"\n']) {
    assert.throws(() => pyyamlLoad(text), Error, text);
  }
});

test("a profile reads its catalogs through a merge key", () => {
  withFile("b: &b {catalogs: [eu-ai-act]}\n<<: *b\n", (path) => {
    assert.deepEqual(loadProfile(path).catalogs, ["eu-ai-act"]);
  });
});

test("of two merge keys the later one wins, as PyYAML flattens them", () => {
  assert.deepEqual(pyyamlLoad("{<<: {a: 1}, <<: {a: 2}}"), { a: 2 });
  assert.deepEqual(pyyamlLoad("{<<: {a: 2, b: 2}, <<: [{a: 1}, {a: 3, c: 3}]}"), {
    a: 1,
    b: 2,
    c: 3,
  });
  assert.deepEqual(pyyamlLoad("{a: 0, <<: {a: 1}, <<: {a: 2, b: 2}}"), { a: 0, b: 2 });
  assert.deepEqual(pyyamlLoad("- <<: {control: DOC-01}\n  <<: {control: NOPE-99}\n"), [
    { control: "NOPE-99" },
  ]);
  assert.throws(() => pyyamlLoad("{<<: {a: 1}, <<: {a: 2}}", true), Error);
});

test("a profile with a YAML error is refused as a profile problem", () => {
  for (const text of [
    "x: !!binary aGVsbG8=\n",
    "x: !!set {a: null}\n",
    "<<:\n",
    "x: [a\n",
    "x: a\t\n",
  ]) {
    withFile(text, (path) => {
      assert.throws(
        () => loadProfile(path),
        (error: unknown) => error instanceof InputError && error.key === "input.profile_invalid",
        text,
      );
    });
  }
});

test("a list or mapping control is named as Python prints it", () => {
  const lint = (control: unknown): string | undefined =>
    deviationLint([{ control }], {
      controlIds: new Set(["DOC-01"]),
      outcomesByControl: new Map(),
      appliedControls: new Set(),
      asOf: null,
    })[0];
  assert.equal(lint(["DOC-01"]), "['DOC-01']: control is not in the catalog");
  assert.equal(lint({ "DOC-01": "x" }), "{'DOC-01': 'x'}: control is not in the catalog");
});
