/**
 * `catalogue()` loads the vendored `spec/i18n` catalogue rather than a hand-copied dictionary, so these
 * tests compare against a live read of the spec file at test time -- not a copy-pasted literal -- so a
 * reverted loader (one that returns a hardcoded object again) cannot pass even if it still hard-codes a
 * few correct-looking spot values (18.35).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { catalogue } from "./messages";

const REPO = join(__dirname, "..", "..", "..");
const PREFIXES = ["report.", "verdict.", "next.", "outcome.", "readiness."];

function specReportKeys(language: string): Record<string, string> {
  const path = join(REPO, "spec", "i18n", `messages.${language}.json`);
  const data = JSON.parse(readFileSync(path, "utf-8")) as Record<string, string>;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(data)) {
    if (PREFIXES.some((prefix) => key.startsWith(prefix))) out[key] = value;
  }
  return out;
}

test("catalogue('en') deep-equals a live, filtered read of spec/i18n/messages.en.json", () => {
  assert.deepEqual(catalogue("en"), specReportKeys("en"));
});

test("catalogue('de') deep-equals en filtered-and-merged with de filtered", () => {
  assert.deepEqual(catalogue("de"), { ...specReportKeys("en"), ...specReportKeys("de") });
});

test("catalogue() with no argument defaults to en", () => {
  assert.deepEqual(catalogue(), catalogue("en"));
});

test("report.activity_none_undeclared matches the spec text (14.1 drift this item fixes)", () => {
  assert.equal(
    catalogue("en")["report.activity_none_undeclared"],
    "every agent, tool, and model your records show is declared",
  );
});

test("report.activity_undeclared_agents_label is present and matches the spec text (14.1 drift this item fixes)", () => {
  assert.equal(catalogue("en")["report.activity_undeclared_agents_label"], "Agents");
});
