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

test("a self-closing root followed by a sibling element is multiple root elements, not valid XML", () => {
  // fast-xml-parser's own `XMLValidator.validate` only flips `reachedRoot` from the paired
  // open/close branch, so `<a/><b/>` -- a self-closing root followed by a sibling -- slips past it
  // (confirmed empirically); Python's `ElementTree` and Java's `XMLStreamReader` both refuse it.
  const out = freshFullReport();
  try {
    writeFileSync(join(out, "report.junit.xml"), '<?xml version="1.0"?>\n<a/><b/>\n');
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.startsWith("report.junit.xml: invalid XML")),
      JSON.stringify(problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("a named entity reference that isn't one of XML's five predefined entities is not valid XML", () => {
  // `XMLValidator.validate` checks an entity reference's syntax (`&word;`) but never whether `word`
  // actually names something, so it accepts `&undefined;` outright (confirmed empirically); Python's
  // `ElementTree` and Java's `XMLStreamReader` (DTD support off in both) both refuse it as undefined.
  const out = freshFullReport();
  try {
    writeFileSync(join(out, "oscal-ar.xml"), '<?xml version="1.0"?>\n<a>&undefined;</a>\n');
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.startsWith("oscal-ar.xml: invalid XML")),
      JSON.stringify(problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("the five predefined XML entities and numeric character references are still accepted", () => {
  const out = freshFullReport();
  try {
    writeFileSync(
      join(out, "report.junit.xml"),
      '<?xml version="1.0"?>\n<a>&amp; &lt; &gt; &apos; &quot; &#65; &#x41;</a>\n',
    );
    const problems = validateReport(out);
    assert.ok(!problems.some((p) => p.startsWith("report.junit.xml:")), JSON.stringify(problems));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("a non-object manifest.json is a tidy problem, not a crash", () => {
  // Python's own `_recorded_outputs` crashed with an uncaught `AttributeError` on this input before
  // this item's follow-up fix (`manifest.get("outputs")` assumed a dict); `recordedOutputs`'s
  // `isRecord` guard already made TypeScript safe here -- this test locks that in.
  const out = freshFullReport();
  try {
    writeFileSync(join(out, "manifest.json"), "[1, 2, 3]");
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.includes("manifest.json")),
      JSON.stringify(problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("non-UTF-8 bytes in a mandatory artifact are a tidy problem, not a crash", () => {
  // Python's own `path.read_text(encoding="utf-8")` crashed with an uncaught `UnicodeDecodeError`
  // on this input before this item's follow-up fix; `readFileSync(path, "utf-8")` never raises on
  // invalid bytes (Node substitutes U+FFFD, so the subsequent `JSON.parse` fails as ordinary invalid
  // JSON) -- this test locks that in.
  const out = freshFullReport();
  try {
    writeFileSync(join(out, "assertions.json"), Buffer.from([0xff, 0xfe, 0x00, 0x01, 0x78]));
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.startsWith("assertions.json:")),
      JSON.stringify(problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("a non-UTF-8 runtime_drift.jsonl line is a tidy problem, not a crash", () => {
  const out = freshFullReport();
  try {
    const manifestPath = join(out, "manifest.json");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
    manifest.outputs["runtime_drift.jsonl"] = `sha256:${"0".repeat(64)}`;
    writeFileSync(manifestPath, JSON.stringify(manifest));
    writeFileSync(
      join(out, "runtime_drift.jsonl"),
      Buffer.concat([
        Buffer.from('{"subject": "x"}\n'),
        Buffer.from([0xff, 0xfe]),
        Buffer.from("x"),
      ]),
    );
    const problems = validateReport(out);
    assert.ok(
      problems.some((p) => p.startsWith("runtime_drift.jsonl:")),
      JSON.stringify(problems),
    );
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

test("case 22: two local-stage violations in one file are both reported, located", () => {
  const out = freshFullReport();
  try {
    const path = join(out, "assertions.json");
    const assertions = JSON.parse(readFileSync(path, "utf-8"));
    assertions[0].control = undefined;
    assertions[1].control = undefined;
    writeFileSync(path, JSON.stringify(assertions));
    const problems = validateReport(out).filter((p) => p.startsWith("assertions.json:"));
    assert.deepEqual(problems, [
      "assertions.json: 0: must have required property 'control'",
      "assertions.json: 1: must have required property 'control'",
    ]);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 23: an anyOf failure at the real schema stage collapses to one combinator problem", () => {
  const out = freshFullReport();
  try {
    const path = join(out, "results.sarif");
    const sarif = JSON.parse(readFileSync(path, "utf-8"));
    sarif.runs[0].results[0].locations[0].physicalLocation.region = {};
    writeFileSync(path, JSON.stringify(sarif));
    const problems = validateReport(out).filter((p) =>
      p.startsWith("results.sarif (OASIS SARIF 2.1.0):"),
    );
    assert.deepEqual(problems, [
      "results.sarif (OASIS SARIF 2.1.0): runs/0/results/0/locations/0/physicalLocation/region: must match a schema in anyOf",
    ]);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("case 24: real-schema violations at indices 2 and 10 sort numerically, not lexicographically", () => {
  const out = freshFullReport();
  try {
    const path = join(out, "results.sarif");
    const sarif = JSON.parse(readFileSync(path, "utf-8"));
    const results = sarif.runs[0].results;
    // The lightweight fixture this suite's own `assess` run produces may have fewer than 11
    // results; pad it with clones of the last one so indices 2 and 10 both exist.
    while (results.length <= 10) {
      results.push(JSON.parse(JSON.stringify(results[results.length - 1])));
    }
    results[2].message.text = undefined;
    results[10].message.text = undefined;
    writeFileSync(path, JSON.stringify(sarif));
    const problems = validateReport(out).filter((p) => p.startsWith("results.sarif:"));
    assert.deepEqual(problems, [
      "results.sarif: runs/0/results/2/message: must have required property 'text'",
      "results.sarif: runs/0/results/10/message: must have required property 'text'",
    ]);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});
