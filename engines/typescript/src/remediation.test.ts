/**
 * renderRemediationMd renders every package of the shared case table to the Python reference's bytes
 * (tools/fixtures/report_formats/remediation_md_expected.json, written by report_format_parity_check.py
 * --write-expected; 18.16e).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { renderRemediationMd } from "./remediation";

const FIXTURES = join(__dirname, "..", "..", "..", "tools", "fixtures", "report_formats");

test("renderRemediationMd renders the case table as Python does", () => {
  const read = (name: string) => JSON.parse(readFileSync(join(FIXTURES, name), "utf-8"));
  const cases = read("remediation_md_cases.json").cases as {
    name: string;
    package: Record<string, unknown>;
  }[];
  const expected = read("remediation_md_expected.json") as Record<string, string>;
  assert.deepEqual(cases.map((c) => c.name).sort(), Object.keys(expected).sort());
  for (const c of cases) {
    assert.equal(renderRemediationMd(c.package), expected[c.name], c.name);
  }
});
