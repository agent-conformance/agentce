/**
 * `report --validate` at parity with the Python reference (item 18.27); every case here reproduces
 * one of `harness/remediation/evidence/P18-18.27/python-reference.md`'s numbered captures.
 */

import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { quickstartDir } from "./bundled";
import { main } from "./cli";
import { validateReport } from "./reportValidate";

const ENGINE = join(__dirname, "..");
const REPO = join(ENGINE, "..", "..");

const SCHEMA_NAMES = [
  "activity",
  "assertions",
  "auditor",
  "blind-spots",
  "buyer",
  "manifest",
  "oscal-assessment-results",
  "project",
  "results-sarif",
  "security",
];

test("every local report schema is byte-identical to spec/report", () => {
  for (const name of SCHEMA_NAMES) {
    const vendored = readFileSync(join(ENGINE, "schema", `${name}.schema.json`), "utf-8");
    const spec = readFileSync(join(REPO, "spec", "report", `${name}.schema.json`), "utf-8");
    assert.equal(vendored, spec, `${name}.schema.json has drifted from spec/report`);
  }
});

test("the two vendored third-party schemas are byte-identical to spec/report/vendor", () => {
  for (const name of ["oscal-assessment-results-nist-1.1.2", "sarif-2.1.0"]) {
    const vendored = readFileSync(join(ENGINE, "schema", `${name}.schema.json`), "utf-8");
    const spec = readFileSync(
      join(REPO, "spec", "report", "vendor", `${name}.schema.json`),
      "utf-8",
    );
    assert.equal(vendored, spec, `${name}.schema.json has drifted from spec/report/vendor`);
  }
});

function runJson(argv: string[]): { exitCode: number; envelope: Record<string, unknown> } {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => lines.push(line);
  let exitCode: number;
  try {
    exitCode = main([...argv, "--json"]);
  } finally {
    console.log = original;
  }
  return { exitCode, envelope: JSON.parse(lines.join("\n")) };
}

function freshFullReport(): string {
  const out = mkdtempSync(join(tmpdir(), "agentce-report-validate-"));
  const quickstart = quickstartDir();
  const { envelope } = runJson([
    "assess",
    "--bundle",
    join(quickstart, "evidence"),
    "--profile",
    join(quickstart, "applicability.yaml"),
    "--catalog",
    "eu-ai-act@2026.09",
    "--out",
    out,
  ]);
  assert.equal(envelope.command, "assess");
  return out;
}

test("case 1: a clean directory validates with no problems", () => {
  const out = freshFullReport();
  try {
    assert.deepEqual(validateReport(out), []);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 2: a missing mandatory artifact is flagged twice (mandatory, and recorded-but-absent)", () => {
  const out = freshFullReport();
  try {
    unlinkSync(join(out, "assertions.json"));
    const problems = validateReport(out);
    assert.ok(problems.includes("assertions.json: missing"));
    assert.ok(
      problems.includes(
        "assertions.json: missing (recorded in manifest.json's outputs but not on disk)",
      ),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 3: invalid JSON is reported as invalid JSON, not a crash", () => {
  const out = freshFullReport();
  try {
    writeFileSync(join(out, "manifest.json"), "not json{");
    const problems = validateReport(out);
    assert.ok(problems.some((p) => p.startsWith("manifest.json: invalid JSON")));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 4: a local-schema violation names the missing required property", () => {
  const out = freshFullReport();
  try {
    const path = join(out, "assertions.json");
    const assertions = JSON.parse(readFileSync(path, "utf-8"));
    assertions[0].control = undefined;
    writeFileSync(path, JSON.stringify(assertions));
    const problems = validateReport(out);
    assert.ok(problems.some((p) => p.startsWith("assertions.json:") && p.includes("control")));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 5: oscal-ar.json missing 'assessment-results' fails the local profile", () => {
  const out = freshFullReport();
  try {
    const path = join(out, "oscal-ar.json");
    const oscal = JSON.parse(readFileSync(path, "utf-8"));
    oscal["assessment-results"] = undefined;
    writeFileSync(path, JSON.stringify(oscal));
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.startsWith("oscal-ar.json:") && !p.includes("NIST")),
      JSON.stringify(problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 6: a bad OSCAL uuid passes the local profile but fails the real NIST 1.1.2 schema", () => {
  const out = freshFullReport();
  try {
    const path = join(out, "oscal-ar.json");
    const oscal = JSON.parse(readFileSync(path, "utf-8"));
    oscal["assessment-results"].uuid = "not-a-uuid";
    writeFileSync(path, JSON.stringify(oscal));
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.startsWith("oscal-ar.json (NIST OSCAL 1.1.2): ")),
      JSON.stringify(problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 7: results.sarif missing 'runs' fails the local profile", () => {
  const out = freshFullReport();
  try {
    const path = join(out, "results.sarif");
    const sarif = JSON.parse(readFileSync(path, "utf-8"));
    sarif.runs = undefined;
    writeFileSync(path, JSON.stringify(sarif));
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.startsWith("results.sarif:") && !p.includes("OASIS")),
      JSON.stringify(problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 8: an extra message field passes the local profile but fails the real OASIS 2.1.0 schema", () => {
  const out = freshFullReport();
  try {
    const path = join(out, "results.sarif");
    const sarif = JSON.parse(readFileSync(path, "utf-8"));
    if (sarif.runs[0].results.length === 0) {
      // The quickstart run may have zero results on some fixtures; give it one to corrupt.
      sarif.runs[0].results.push({
        ruleId: "x",
        level: "note",
        message: { text: "x" },
        locations: [],
        partialFingerprints: {},
      });
    }
    sarif.runs[0].results[0].message.bogus_field = "x";
    writeFileSync(path, JSON.stringify(sarif));
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.startsWith("results.sarif (OASIS SARIF 2.1.0): ")),
      JSON.stringify(problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 9: a corrupted report.csv names each missing column", () => {
  const out = freshFullReport();
  try {
    writeFileSync(join(out, "report.csv"), "not a csv file at all, just garbage\x00\x01\n");
    writeFileSync(
      join(out, "manifest.json"),
      JSON.stringify({
        ...JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8")),
        outputs: {
          ...JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8")).outputs,
          "report.csv": `sha256:${"0".repeat(64)}`,
        },
      }),
    );
    const problems = validateReport(out);
    for (const column of ["control", "subject", "outcome", "control_version", "rung", "mode"]) {
      assert.ok(
        problems.includes(`report.csv: missing column '${column}'`),
        JSON.stringify(problems),
      );
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 10/11: malformed XML is reported as invalid XML for either optional XML artifact", () => {
  const out = freshFullReport();
  try {
    writeFileSync(join(out, "report.junit.xml"), "<not-closed>");
    writeFileSync(join(out, "oscal-ar.xml"), "<not-closed>");
    const problems = validateReport(out);
    assert.ok(problems.some((p) => p.startsWith("report.junit.xml: invalid XML")));
    assert.ok(problems.some((p) => p.startsWith("oscal-ar.xml: invalid XML")));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 12/13: runtime_drift.jsonl is fine when every line parses, flagged on the first bad line", () => {
  const out = freshFullReport();
  try {
    const manifestPath = join(out, "manifest.json");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
    manifest.outputs["runtime_drift.jsonl"] = `sha256:${"0".repeat(64)}`;
    writeFileSync(manifestPath, JSON.stringify(manifest));

    writeFileSync(join(out, "runtime_drift.jsonl"), '{"subject": "x"}\n{"subject": "y"}\n');
    assert.deepEqual(validateReport(out), []);

    writeFileSync(join(out, "runtime_drift.jsonl"), '{"subject": "x"}\nnot json at all\n');
    const problems = validateReport(out);
    assert.ok(problems.some((p) => p.startsWith("runtime_drift.jsonl: line 2 is not valid JSON")));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 14: an empty report.md is flagged", () => {
  const out = freshFullReport();
  try {
    writeFileSync(join(out, "report.md"), "   \n");
    const problems = validateReport(out);
    assert.ok(problems.includes("report.md: empty"));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 15: a recorded output that goes missing is distinguished from one --emit never wrote", () => {
  const out = freshFullReport();
  try {
    assert.deepEqual(validateReport(out), []);
    unlinkSync(join(out, "report.html"));
    const problems = validateReport(out);
    assert.ok(
      problems.includes(
        "report.html: missing (recorded in manifest.json's outputs but not on disk)",
      ),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 16: --validate given a non-directory path refuses before opening any artifact", () => {
  const { exitCode, envelope } = runJson([
    "report",
    "--validate",
    join(tmpdir(), "agentce-nope-xyz"),
  ]);
  assert.equal(exitCode, 3);
  assert.equal(
    (envelope.error as { message_key: string }).message_key,
    "input.validate_not_a_directory",
  );
});
