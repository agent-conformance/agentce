/** The audience-view registry and the security view's renderers match Python (18.16g): every case of
 * security_cases.json renders security_expected.json's text, the dispatch writes security.md and
 * security.html only when asked (inbox row 106 M2), and writeReport writes the security files only
 * while the preset is registered (M3). */

import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { summarizeActivity } from "./activity";
import { profileFromDict } from "./profile";
import {
  AUDIENCE_VIEWS,
  type ReportViewContext,
  SEAM_FOR,
  renderSecurityHtml,
  renderSecurityMd,
  writeAudienceView,
  writeReport,
} from "./report";
import type { SecurityView } from "./securityView";

const FIXTURES = join(__dirname, "..", "..", "..", "tools", "fixtures", "report_formats");
const cases = (
  JSON.parse(readFileSync(join(FIXTURES, "security_cases.json"), "utf-8")) as {
    cases: { name: string; language: string; view: SecurityView }[];
  }
).cases;
const expected = JSON.parse(
  readFileSync(join(FIXTURES, "security_expected.json"), "utf-8"),
) as Record<string, string>;

test("security_cases.json and security_expected.json name the same cases", () => {
  assert.deepEqual(
    cases.flatMap((c) => [`${c.name}.md`, `${c.name}.html`]).sort(),
    Object.keys(expected).sort(),
  );
});

for (const c of cases) {
  test(`security case ${c.name} renders Python's security.md and security.html`, () => {
    assert.equal(renderSecurityMd(c.view, c.language), expected[`${c.name}.md`]);
    assert.equal(renderSecurityHtml(c.view, c.language), expected[`${c.name}.html`]);
  });
}

test("the registry's keys are exactly [security]", () => {
  assert.deepEqual(Object.keys(AUDIENCE_VIEWS), ["security"]);
});

function context(): ReportViewContext {
  return { activity: summarizeActivity([], profileFromDict({})), assertions: [], language: "en" };
}

function written(preset: string | undefined, wanted: string[]): string[] {
  const names: string[] = [];
  writeAudienceView(
    preset,
    context(),
    (ext) => wanted.includes(ext),
    (name) => names.push(name),
    (name) => names.push(name),
  );
  return names;
}

test("M2: the security view writes .md and .html only when asked", () => {
  assert.deepEqual(written("security", []), ["security.json"]);
  assert.deepEqual(written("security", ["md"]), ["security.json", "security.md"]);
  assert.deepEqual(written("security", ["html"]), ["security.json", "security.html"]);
  assert.deepEqual(written("security", ["md", "html"]), [
    "security.json",
    "security.md",
    "security.html",
  ]);
});

test("an unknown preset and no preset write nothing", () => {
  assert.deepEqual(written("nonsense", ["md", "html"]), []);
  assert.deepEqual(written(undefined, ["md", "html"]), []);
  assert.deepEqual(written("toString", ["md", "html"]), []);
});

const SECURITY_FILES = ["security.json", "security.md", "security.html"];

function reportWith(preset: string | undefined): { dir: string; outputs: Record<string, string> } {
  const dir = mkdtempSync(join(tmpdir(), "agentce-audience-"));
  SEAM_FOR.preset = preset;
  try {
    const manifest = writeReport(dir, [], {
      bundleDigest: "sha256:abc",
      catalogs: [],
      invocation: [],
    });
    return { dir, outputs: manifest.outputs as Record<string, string> };
  } finally {
    SEAM_FOR.preset = undefined;
  }
}

test("M3: writeReport writes the security files while security is registered, and none once removed", () => {
  const registered = reportWith("security");
  for (const name of SECURITY_FILES) {
    assert.ok(existsSync(join(registered.dir, name)), name);
    assert.ok(name in registered.outputs, name);
  }
  rmSync(registered.dir, { recursive: true, force: true });

  const spec = AUDIENCE_VIEWS.security;
  Reflect.deleteProperty(AUDIENCE_VIEWS, "security");
  try {
    const removed = reportWith("security");
    for (const name of SECURITY_FILES) {
      assert.equal(existsSync(join(removed.dir, name)), false, name);
      assert.equal(name in removed.outputs, false, name);
    }
    rmSync(removed.dir, { recursive: true, force: true });
  } finally {
    AUDIENCE_VIEWS.security = spec;
  }
});

test("writeReport writes no security file with no preset or an unknown one", () => {
  for (const preset of [undefined, "nonsense"]) {
    const { dir, outputs } = reportWith(preset);
    for (const name of SECURITY_FILES) {
      assert.equal(existsSync(join(dir, name)), false, name);
      assert.equal(name in outputs, false, name);
    }
    rmSync(dir, { recursive: true, force: true });
  }
});
