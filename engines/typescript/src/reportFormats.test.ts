/**
 * report.junit.xml, report.csv, oscal-ar.xml, public-statement.md and report.pdf match the Python reference byte
 * for byte (SPEC §9, §9.4, §9.5; 18.16c, 18.16d). `tools/fixtures/report_formats/expected.json` is Python's
 * rendering of each case in `cases.json` (empty, every outcome out of order, hostile characters, an odd OSCAL
 * document, code-point order, odd started_at values, German), report.pdf as Latin-1 text; VG-REPORT-FORMAT-PARITY
 * keeps it current and also compares the corpus.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { aggregate, assertionFromJson } from "./assertions";
import {
  appliedDeviationIds,
  renderCsv,
  renderJunit,
  renderOscal,
  renderOscalXml,
  renderPdf,
  renderPublicStatement,
} from "./report";

const FIXTURES = join(__dirname, "..", "..", "..", "tools", "fixtures", "report_formats");

interface Case {
  name: string;
  assertions: unknown[];
  catalogs: string[];
  oscal?: Record<string, unknown>;
  started_at?: string;
  report_language?: string;
}

/** A case's started_at when it gives none (the checker's CASE_STARTED_AT). */
const CASE_STARTED_AT = "2026-09-01T00:00:00Z";

const cases: Case[] = JSON.parse(readFileSync(join(FIXTURES, "cases.json"), "utf-8")).cases;
const expected: Record<string, Record<string, string>> = JSON.parse(
  readFileSync(join(FIXTURES, "expected.json"), "utf-8"),
);

for (const c of cases) {
  test(`report formats match Python: ${c.name}`, () => {
    const assertions = c.assertions.map(assertionFromJson);
    const want = expected[c.name];
    assert.ok(want, `expected.json has no ${c.name}`);
    assert.equal(renderJunit(assertions), want["report.junit.xml"]);
    assert.equal(renderCsv(assertions), want["report.csv"]);
    assert.equal(renderOscalXml(c.oscal ?? renderOscal(assertions)), want["oscal-ar.xml"]);
    assert.equal(
      renderPublicStatement(assertions, c.catalogs, appliedDeviationIds(assertions)),
      want["public-statement.md"],
    );
    const pdf = renderPdf(
      assertions,
      aggregate(assertions),
      c.started_at ?? CASE_STARTED_AT,
      c.report_language ?? "en",
    );
    assert.equal(pdf.toString("latin1"), want["report.pdf"]);
  });
}

test("every case has an expected rendering", () => {
  assert.deepEqual(Object.keys(expected).sort(), cases.map((c) => c.name).sort());
});
