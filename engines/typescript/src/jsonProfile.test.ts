/**
 * The JSON profile (SPEC 8.7) against the shared case table `spec/report/json-cases.json`, the rows
 * Python's `test_json_profile.py` and Java's `JsonProfileTest` read too. A row whose rule this engine
 * does not run fails the last test.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { canonicalString } from "./canonical";
import {
  JsonProfileError,
  type NonCanonicalNumber,
  isJsonInteger,
  jsonEqual,
  readJson,
  readJsonl,
  renderNumber,
  renderValue,
} from "./json";

interface Case {
  id: string;
  rule: string;
  mode?: "json" | "jsonl";
  bom?: "ignore" | "refuse";
  text?: string;
  hex?: string;
  expect?: string;
  canonical?: string;
  line?: number;
  records?: number[];
  input?: string;
  left?: string;
  right?: string;
  equal?: boolean;
  rendered?: string;
}

const CASES = (
  JSON.parse(
    readFileSync(join(__dirname, "..", "..", "..", "spec", "report", "json-cases.json"), "utf-8"),
  ) as {
    cases: Case[];
  }
).cases;

const raw = (c: Case): Buffer =>
  c.hex !== undefined ? Buffer.from(c.hex, "hex") : Buffer.from(c.text ?? "", "utf8");
const value = (text: string): unknown => readJson(Buffer.from(text, "utf8"), "refuse");

function refusal(fn: () => unknown): [string, number] {
  try {
    fn();
  } catch (exc) {
    assert.ok(exc instanceof JsonProfileError, String(exc));
    return [exc.rule, exc.line];
  }
  return assert.fail("accepted");
}

const RUN = new Set<string>();

for (const c of CASES.filter((row) => row.mode !== undefined)) {
  RUN.add(c.rule);
  test(`reader ${c.rule} ${c.id}`, () => {
    const bom = c.bom ?? "refuse";
    if (c.mode === "json") {
      if (c.expect === "accept") {
        assert.equal(renderValue(readJson(raw(c), bom)), c.canonical);
      } else {
        assert.deepEqual(
          refusal(() => readJson(raw(c), bom)),
          [c.rule, c.line],
        );
      }
      return;
    }
    let lines: Array<[number, unknown]>;
    try {
      lines = readJsonl(raw(c), bom);
    } catch (exc) {
      assert.ok(exc instanceof JsonProfileError && c.expect === "refuse");
      assert.deepEqual([exc.rule, exc.line], [c.rule, c.line]);
      return;
    }
    const bad = lines.filter(([, v]) => v instanceof JsonProfileError) as Array<
      [number, JsonProfileError]
    >;
    if (c.expect === "accept") {
      assert.deepEqual(bad, []);
      assert.deepEqual(
        lines.map(([n]) => n),
        c.records,
      );
    } else {
      assert.deepEqual([bad[0][1].rule, bad[0][1].line], [c.rule, c.line]);
    }
  });
}

for (const c of CASES) {
  if (c.rule === "number_rendering") {
    RUN.add(c.rule);
    test(`number_rendering ${c.id}`, () => {
      assert.equal(renderNumber(value(c.input as string) as number | NonCanonicalNumber), c.expect);
    });
  } else if (c.rule === "compare_by_value" || c.rule === "compare_across_types") {
    RUN.add(c.rule);
    test(`${c.rule} ${c.id}`, () => {
      const left = value(c.left as string);
      const right = value(c.right as string);
      assert.equal(jsonEqual(left, right), c.equal);
      assert.equal(jsonEqual(right, left), c.equal);
    });
  } else if (c.rule === "integer_field_float_form") {
    RUN.add(c.rule);
    test(`integer_field_float_form ${c.id}`, () => {
      assert.equal(isJsonInteger(value(c.text as string)), c.expect === "accept");
    });
  } else if (c.rule === "non_integer_number") {
    RUN.add(c.rule);
    test(`non_integer_number ${c.id}`, () => {
      assert.throws(
        () => canonicalString(value(c.text as string)),
        (exc: Error) =>
          exc.message.includes("non_integer_number") && exc.message.includes(c.rendered as string),
      );
    });
  }
}

test("every rule in the case table is run", () => {
  assert.deepEqual(
    [...new Set(CASES.map((c) => c.rule))].filter((rule) => !RUN.has(rule)),
    [],
  );
});
