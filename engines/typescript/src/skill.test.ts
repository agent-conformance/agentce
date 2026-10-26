/** The skill folder's functions render every case of skill_cases.json as Python does (18.16f). */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { doubleQuoted } from "./pyyaml";
import { skillCase } from "./seams";

const FIXTURES = join(__dirname, "..", "..", "..", "tools", "fixtures", "report_formats");
const cases = (
  JSON.parse(readFileSync(join(FIXTURES, "skill_cases.json"), "utf-8")) as {
    cases: Record<string, unknown>[];
  }
).cases;
const expected = JSON.parse(readFileSync(join(FIXTURES, "skill_expected.json"), "utf-8")) as Record<
  string,
  string
>;

test("skill_cases.json and skill_expected.json name the same cases", () => {
  assert.deepEqual(cases.map((c) => c.name).sort(), Object.keys(expected).sort());
});

for (const c of cases) {
  test(`skill case ${String(c.name)} renders Python's text`, () => {
    assert.equal(skillCase(c), expected[c.name as string]);
  });
}

test("doubleQuoted escapes as PyYAML's allow_unicode writer and never folds", () => {
  assert.equal(doubleQuoted(""), '""');
  assert.equal(doubleQuoted('a"b\\c'), '"a\\"b\\\\c"');
  assert.equal(doubleQuoted("\u0085  ﻿\u0000\u001b"), '"\\N\\L\\P\\uFEFF\\0\\e"');
  assert.equal(doubleQuoted(" ퟿�￾￿"), '" ퟿�\\uFFFE\\uFFFF"');
  assert.equal(doubleQuoted("\u{1F600}\u007f"), '"\\U0001F600\\x7F"');
  assert.equal(doubleQuoted("x ".repeat(100)), `"${"x ".repeat(100)}"`);
});
