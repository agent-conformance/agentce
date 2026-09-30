import assert from "node:assert/strict";
import { test } from "node:test";
import { jsonStringifyAscii } from "./util";

test("jsonStringifyAscii escapes non-ASCII exactly like Python's json.dumps(ensure_ascii=True)", () => {
  // Python: json.dumps({"control": "cé", "subject": "😀"}, indent=2, sort_keys=True)
  // -> '{\n  "control": "c\\u00e9",\n  "subject": "\\ud83d\\ude00"\n}'
  const out = jsonStringifyAscii({ control: "cé", subject: "😀" }, 2);
  assert.equal(out, '{\n  "control": "c\\u00e9",\n  "subject": "\\ud83d\\ude00"\n}');
});

test("jsonStringifyAscii leaves ASCII content untouched", () => {
  assert.equal(jsonStringifyAscii({ a: "plain text" }), '{"a":"plain text"}');
});

test("jsonStringifyAscii lowercases hex digits above 0x7E", () => {
  const out = jsonStringifyAscii("À"); // "À"
  assert.equal(out, '"\\u00c0"');
});
