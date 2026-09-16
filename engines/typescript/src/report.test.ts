/**
 * The report renderers are byte-identical to the Python reference (SPEC §9).
 *
 * `testdata/report-golden.json` holds the reference engine's `render_*` output for the OVS-03 failed
 * scenario (a mix of conformant, non-conformant, and not_applicable, with evidence pointers); the SARIF
 * engine name is normalised to `agentce-ts` since that is the only engine-specific field. The OSCAL
 * comparison also pins the deterministic `uuid5` findings. `writeReport` is smoke-tested to a temp dir.
 */

import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { aggregate, assertionToJson } from "./assertions";
import { assessSubjects } from "./assess";
import { canonicalString } from "./canonical";
import { loadCatalog } from "./catalog";
import { DomainBinding } from "./domain";
import { profileFromDict } from "./profile";
import {
  renderEvidencePack,
  renderOscal,
  renderReportHtml,
  renderReportMd,
  renderSarif,
  writeReport,
} from "./report";

const REPO = join(__dirname, "..", "..", "..");
const BASE = join(REPO, "spec", "catalogs", "base", "eu-ai-act");
const TESTDATA = join(__dirname, "..", "testdata");
const SUBJECT = "spiffe://corp/agents/a";

function ovsFailedAssertions() {
  const catalog = loadCatalog(BASE);
  const domain = DomainBinding.load(join(BASE, "test", "domain.yaml"));
  const profile = profileFromDict({ subjects: [{ id: SUBJECT, role: "both" }] });
  const events = readFileSync(join(BASE, "test", "OVS-03", "failed.jsonl"), "utf-8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
  return assessSubjects(events, profile, [catalog], domain);
}

test("report renderers match the Python reference golden", () => {
  const golden = JSON.parse(readFileSync(join(TESTDATA, "report-golden.json"), "utf-8"));
  const assertions = ovsFailedAssertions();
  const counts = aggregate(assertions);

  assert.equal(renderReportMd(assertions, counts), golden.md);
  assert.equal(renderReportHtml(assertions, counts), golden.html);
  assert.equal(canonicalString(renderOscal(assertions)), canonicalString(golden.oscal));
  assert.equal(canonicalString(renderSarif(assertions)), canonicalString(golden.sarif));
  assert.equal(
    canonicalString(renderEvidencePack(SUBJECT, assertions)),
    canonicalString(golden.pack),
  );
  assert.equal(
    canonicalString(assertions.map(assertionToJson)),
    canonicalString(golden.assertions),
  );
});

test("writeReport emits every artifact and a well-formed manifest", () => {
  const assertions = ovsFailedAssertions();
  const outDir = mkdtempSync(join(tmpdir(), "agentce-report-"));
  const manifest = writeReport(outDir, assertions, {
    bundleDigest: "sha256:abc",
    catalogs: ["base/eu-ai-act@1"],
    operator: "ecs",
    invocation: ["conformance", "p1"],
  });

  for (const name of [
    "assertions.json",
    "report.md",
    "report.html",
    "oscal-ar.json",
    "results.sarif",
    "manifest.json",
  ]) {
    assert.equal(existsSync(join(outDir, name)), true, `${name} should exist`);
  }
  assert.equal(
    existsSync(join(outDir, "packs", SUBJECT.replace(/[^\p{L}\p{N}\-._]/gu, "_"), "pack.json")),
    true,
  );
  const engine = manifest.engine as Record<string, unknown>;
  assert.equal(engine.impl, "agentce-ts");
  const outputs = manifest.outputs as Record<string, string>;
  assert.equal("assertions.json" in outputs, true);
  // the manifest on disk is Python-style json.dumps(sort_keys, indent=2): sorted top-level keys
  const onDisk = readFileSync(join(outDir, "manifest.json"), "utf-8");
  assert.equal(onDisk.startsWith('{\n  "agentce_manifest_version": 1,'), true);
});
