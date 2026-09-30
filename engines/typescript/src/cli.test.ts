/**
 * `assess`, `validate`, `report`, and `quickstart` on the real CLI entry point (`main`), over the
 * vendored quickstart project — the same commands `docs/quickstart-typescript.md` tells a Node adopter
 * to run, and the same data every installed package carries (SPEC §13.4 AX-1).
 */

import assert from "node:assert/strict";
import { createPublicKey, verify as cryptoVerify, generateKeyPairSync } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { quickstartDir } from "./bundled";
import { main } from "./cli";

function runJson(argv: string[]): { exitCode: number; envelope: Record<string, unknown> } {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => {
    lines.push(line);
  };
  let exitCode: number;
  try {
    exitCode = main([...argv, "--json"]);
  } finally {
    console.log = original;
  }
  return { exitCode, envelope: JSON.parse(lines.join("\n")) };
}

function runText(argv: string[]): { exitCode: number; lines: string[] } {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => {
    lines.push(line);
  };
  let exitCode: number;
  try {
    exitCode = main(argv);
  } finally {
    console.log = original;
  }
  return { exitCode, lines };
}

function diffFixture(dir: string, name: string, records: unknown[]): string {
  const path = join(dir, name);
  writeFileSync(path, JSON.stringify(records));
  return path;
}

const REPO = join(__dirname, "..", "..", "..");
/** A real, vendored catalog (`OVS-03` is `severity: high`) -- the same catalog `catalog.test.ts` uses. */
const CATALOG_DIR = join(REPO, "spec", "catalogs", "base", "eu-ai-act");

function readinessReport(
  dir: string,
  overrides: {
    assertions?: Record<string, unknown>[];
    integrity?: Record<string, unknown>[];
  } = {},
): string {
  const reportDir = join(dir, "report");
  mkdirSync(reportDir);
  writeFileSync(join(reportDir, "assertions.json"), JSON.stringify(overrides.assertions ?? []));
  writeFileSync(
    join(reportDir, "integrity.jsonl"),
    (overrides.integrity ?? []).map((r) => `${JSON.stringify(r)}\n`).join(""),
  );
  writeFileSync(join(reportDir, "coverage.json"), JSON.stringify({ subjects: {} }));
  writeFileSync(join(reportDir, "applicability.jsonl"), "");
  return reportDir;
}

test("quickstart assesses the vendored project end to end and writes a real report", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-quickstart-"));
  try {
    const { exitCode, envelope } = runJson(["quickstart", "--out", out]);
    assert.equal(envelope.quickstart, "ok");
    assert.ok((envelope.assertions as number) > 0, "quickstart evaluated no assertions");
    assert.ok([0, 1].includes(exitCode), `unexpected exit code ${exitCode}`);

    const assertions = JSON.parse(readFileSync(join(out, "assertions.json"), "utf-8"));
    assert.ok(Array.isArray(assertions) && assertions.length > 0, "assertions.json is empty");
    assert.ok(readFileSync(join(out, "report.html"), "utf-8").includes("<html"));
    assert.ok(JSON.parse(readFileSync(join(out, "oscal-ar.json"), "utf-8")));
    assert.ok(JSON.parse(readFileSync(join(out, "results.sarif"), "utf-8")));

    // The manifest's invocation never carries this machine's raw bundle/profile path (SPEC §8.4).
    const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8"));
    const invocation = (manifest.run as { invocation: string[] }).invocation;
    assert.ok(!invocation.some((part) => part.includes(quickstartDir())), invocation.join(" "));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("assess on the vendored quickstart bundle matches quickstart's own output", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-assess-"));
  try {
    const quickstart = quickstartDir();
    const { exitCode, envelope } = runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--domain",
      join(quickstart, "domain.linkml.yaml"),
      "--catalog",
      "eu-ai-act@2026.09",
      "--out",
      out,
    ]);
    assert.ok([0, 1].includes(exitCode), `unexpected exit code ${exitCode}`);
    assert.ok((envelope.assertions as number) > 0);
    assert.equal((envelope.summary as { verdict: string }).verdict, "incomplete");
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

/** The quickstart profile with its `catalogs:` list removed, written into `dir`. */
function profileWithoutCatalogs(dir: string): string {
  const kept: string[] = [];
  let inCatalogs = false;
  for (const line of readFileSync(join(quickstartDir(), "applicability.yaml"), "utf-8").split(
    "\n",
  )) {
    if (line === "catalogs:") {
      inCatalogs = true;
    } else if (!(inCatalogs && line.startsWith("  - "))) {
      inCatalogs = false;
      kept.push(line);
    }
  }
  const path = join(dir, "no-catalog.yaml");
  writeFileSync(path, kept.join("\n"));
  return path;
}

function manifestCatalogs(out: string): string[] {
  const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8")) as {
    inputs: { catalogs: Array<{ id: string; version: string }> };
  };
  return manifest.inputs.catalogs.map((c) => `${c.id}@${c.version}`);
}

test("assess with no catalog named evaluates the baseline; an empty --catalog is refused", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-assess-default-"));
  try {
    const quickstart = quickstartDir();
    const argv = [
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      profileWithoutCatalogs(out),
      "--domain",
      join(quickstart, "domain.linkml.yaml"),
    ];
    const run = runJson([...argv, "--out", join(out, "default")]);
    assert.ok([0, 1].includes(run.exitCode), `unexpected exit code ${run.exitCode}`);
    assert.deepEqual(manifestCatalogs(join(out, "default")), ["baseline@2026.09"]);
    const explicit = runJson([...argv, "--catalog", "eu-ai-act@2026.09", "--out", join(out, "eu")]);
    assert.ok([0, 1].includes(explicit.exitCode));
    assert.deepEqual(manifestCatalogs(join(out, "eu")), ["eu-ai-act@2026.09"]);
    const empty = runJson([...argv, "--catalog", ",", "--out", join(out, "none")]);
    assert.equal(empty.exitCode, 3);
    assert.equal(
      (empty.envelope.error as { message_key: string }).message_key,
      "input.catalog_missing",
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("assess refuses an unresolvable catalog with a named input error, not a guess", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-assess-badcat-"));
  try {
    const quickstart = quickstartDir();
    const { exitCode, envelope } = runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--catalog",
      "no-such-catalog@1.0",
      "--out",
      out,
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.catalog_unresolved",
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("validate quarantines the vendored quickstart bundle's known-bad events", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-validate-"));
  try {
    const { exitCode, envelope } = runJson([
      "validate",
      "--bundle",
      join(quickstartDir(), "evidence"),
      "--out",
      out,
    ]);
    assert.equal(exitCode, 1); // the quickstart bundle carries deliberately quarantined events
    assert.ok((envelope.quarantined as number) > 0);
    const quarantine = readFileSync(join(out, "quarantine.jsonl"), "utf-8").trim().split("\n");
    assert.equal(quarantine.length, envelope.quarantined);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("report re-renders a committed assertions.json to every supported format", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-report-"));
  try {
    const quickstart = quickstartDir();
    runJson([
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
    const from = join(out, "assertions.json");
    for (const format of ["md", "html", "oscal", "sarif", "pack"]) {
      const { exitCode, envelope } = runJson(["report", "--from", from, "--format", format]);
      assert.equal(exitCode, 0, format);
      assert.ok((envelope.rendering as string).length > 0, format);
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("report --validate refuses a path that is not a directory", () => {
  const { exitCode, envelope } = runJson(["report", "--validate", "/nonexistent"]);
  assert.equal(exitCode, 3);
  assert.equal(
    (envelope.error as { message_key: string }).message_key,
    "input.validate_not_a_directory",
  );
});

test("report --validate accepts a genuine assess run and rejects a corrupted one", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-report-validate-"));
  try {
    const quickstart = quickstartDir();
    runJson([
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
    const clean = runJson(["report", "--validate", out]);
    assert.equal(clean.exitCode, 0, JSON.stringify(clean.envelope.problems));
    assert.equal(clean.envelope.valid, true);
    assert.deepEqual(clean.envelope.problems, []);

    const assertionsPath = join(out, "assertions.json");
    const assertions = JSON.parse(readFileSync(assertionsPath, "utf-8"));
    assertions[0].control = undefined;
    writeFileSync(assertionsPath, JSON.stringify(assertions));
    const corrupted = runJson(["report", "--validate", out]);
    assert.equal(corrupted.exitCode, 3);
    assert.equal(corrupted.envelope.valid, false);
    assert.ok(
      (corrupted.envelope.problems as string[]).some((p) => p.includes("assertions.json")),
      JSON.stringify(corrupted.envelope.problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("report --validate catches a real-schema-only OSCAL violation past the local profile", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-report-validate-oscal-"));
  try {
    const quickstart = quickstartDir();
    runJson([
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
    const oscalPath = join(out, "oscal-ar.json");
    const oscal = JSON.parse(readFileSync(oscalPath, "utf-8"));
    oscal["assessment-results"].uuid = "not-a-uuid"; // AgentCE's local profile types uuid as a bare
    // string (no pattern); only the real vendored NIST 1.1.2 schema enforces the UUID pattern.
    writeFileSync(oscalPath, JSON.stringify(oscal));
    const { exitCode, envelope } = runJson(["report", "--validate", out]);
    assert.equal(exitCode, 3);
    assert.ok(
      (envelope.problems as string[]).some((p) =>
        p.startsWith("oscal-ar.json (NIST OSCAL 1.1.2): "),
      ),
      JSON.stringify(envelope.problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("version --json returns the same structured envelope every other command produces", () => {
  const { exitCode, envelope } = runJson(["version"]);
  assert.equal(envelope.command, "version");
  assert.equal(envelope.engine, "agentce-ts");
  assert.equal(typeof envelope.engine_version, "string");
  assert.equal(envelope.spec_version, "0.6");
  assert.deepEqual(envelope.supported_catalogs, []);
  assert.equal(envelope.no_ml, "pass");
  assert.deepEqual(envelope.no_ml_detail, { result: "pass", denylisted_present: [] });
  assert.ok(Array.isArray(envelope.exit_codes));
  assert.equal(exitCode, 0);
});

test("plain `version` prints exactly two human-readable lines, distinct from --version/-V", () => {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => lines.push(line);
  let exitCode: number;
  try {
    exitCode = main(["version"]);
  } finally {
    console.log = original;
  }
  assert.equal(exitCode, 0);
  assert.equal(lines.length, 2);
  assert.match(lines[0] as string, /^agentce-ts \S+ \(spec 0\.6\)$/);
  assert.equal(lines[1], "no_ml: pass");

  const flagLines: string[] = [];
  console.log = (line: string) => flagLines.push(line);
  try {
    main(["--version"]);
    main(["-V"]);
  } finally {
    console.log = original;
  }
  assert.equal(flagLines.length, 2);
  assert.match(flagLines[0] as string, /^agentce \S+$/);
  assert.equal(flagLines[0], flagLines[1]);
});

test("digest-tree prints one catalog directory's real content digest", () => {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => lines.push(line);
  let exitCode: number;
  try {
    exitCode = main([
      "digest-tree",
      join(__dirname, "..", "..", "..", "spec", "model", "test-vectors", "digest-tree"),
    ]);
  } finally {
    console.log = original;
  }
  assert.equal(exitCode, 0);
  const expected = readFileSync(
    join(__dirname, "..", "..", "..", "spec", "model", "test-vectors", "digest-tree.expected"),
    "utf-8",
  ).trim();
  assert.equal(lines[0], expected);
});

test("security-view CLI verb runs the fixture and prints a standards_citations array", () => {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => lines.push(line);
  let exitCode: number;
  try {
    exitCode = main([
      "security-view",
      join(
        __dirname,
        "..",
        "..",
        "..",
        "verification",
        "gates",
        "fixtures",
        "security_view",
        "activity_and_assertions.json",
      ),
    ]);
  } finally {
    console.log = original;
  }
  assert.equal(exitCode, 0);
  const securityView = JSON.parse(lines[0] as string);
  const citations = securityView.standards_citations as { framework: string }[];
  assert.ok(Array.isArray(citations));
  assert.deepEqual(
    new Set(citations.map((c) => c.framework)),
    new Set(["owasp-asi-2026", "mitre-atlas", "owasp-acs"]),
  );
});

test("diff --json: envelope carries report_a/report_b/changed/diff/what_changed; exit 1 on a real change", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", [
      { control: "C-01", subject: "s1", outcome: "non-conformant" },
    ]);
    const b = diffFixture(dir, "b.json", [
      { control: "C-01", subject: "s1", outcome: "conformant" },
    ]);
    const { exitCode, envelope } = runJson(["diff", a, b]);
    assert.equal(exitCode, 1);
    assert.equal(envelope.report_a, a);
    assert.equal(envelope.report_b, b);
    assert.equal(envelope.changed, 1);
    assert.deepEqual(envelope.diff, [
      { control: "C-01", subject: "s1", from: "non-conformant", to: "conformant" },
    ]);
    assert.deepEqual(envelope.what_changed, {
      closed: [{ control: "C-01", subject: "s1", from: "non-conformant", to: "conformant" }],
      opened: [],
      other: [],
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff identical inputs: exit 0, text 'no differences' (no trailing period)", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", [
      { control: "C-01", subject: "s1", outcome: "conformant" },
    ]);
    const { exitCode, lines } = runText(["diff", a, a]);
    assert.equal(exitCode, 0);
    assert.deepEqual(lines, ["no differences"]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff --format md, identical inputs: the fixed three-line section, 'no differences.' with a period", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", [
      { control: "C-01", subject: "s1", outcome: "conformant" },
    ]);
    const { exitCode, lines } = runText(["diff", a, a, "--format", "md"]);
    assert.equal(exitCode, 0);
    assert.deepEqual(lines, ["## What changed", "", "no differences."]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff --format json (without --json): one note line, byte-equal to the sorted-keys envelope data", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", [
      { control: "C-01", subject: "s1", outcome: "conformant" },
    ]);
    const { exitCode, lines } = runText(["diff", a, b, "--format", "json"]);
    assert.equal(exitCode, 1);
    assert.equal(lines.length, 1);
    const parsed = JSON.parse(lines[0] as string);
    assert.equal(parsed.changed, 1);
    assert.equal(parsed.report_a, a);
    assert.ok(
      !("command" in parsed),
      "note-rendered JSON is result.data only, never the full envelope",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: missing report_b gives input.report_b_missing with the diff-specific fix text, exit 3", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const { exitCode, envelope } = runJson(["diff", a]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; fix: string };
    assert.equal(error.message_key, "input.report_b_missing");
    assert.equal(error.fix, "pass two assertion files: `agentce diff <report-a> <report-b>`.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: a malformed (not-JSON) input file gives a keyed internal.unexpected result, never a crash", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = join(dir, "a.json");
    writeFileSync(a, "not json");
    const b = diffFixture(dir, "b.json", []);
    const { exitCode, envelope } = runJson(["diff", a, b]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; fix: string };
    assert.equal(error.message_key, "internal.unexpected");
    assert.equal(error.fix, "re-run with --debug to see the stack trace, then file an issue.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: --debug on a malformed input file re-throws instead of returning a keyed envelope", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = join(dir, "a.json");
    writeFileSync(a, "not json");
    const b = diffFixture(dir, "b.json", []);
    assert.throws(() => main(["diff", a, b, "--debug"]));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: an extra positional argument gives input.diff_extra_argument", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", []);
    const c = diffFixture(dir, "c.json", []);
    const { exitCode, envelope } = runJson(["diff", a, b, c]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.diff_extra_argument",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: an unrecognized flag gives input.diff_unrecognized_flag, never a silent positional read", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", []);
    const { exitCode, envelope } = runJson(["diff", a, b, "--forma", "text"]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.diff_unrecognized_flag",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: --debug and --quiet are accepted and silently ignored, matching every other command", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const { exitCode, lines } = runText(["diff", a, a, "--debug", "--quiet"]);
    assert.equal(exitCode, 0);
    assert.deepEqual(lines, ["no differences"]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: --format outside {text, json, md} gives input.diff_format with the exact Python fix text", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", []);
    const { exitCode, envelope } = runJson(["diff", a, b, "--format", "yaml"]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; fix: string };
    assert.equal(error.message_key, "input.diff_format");
    assert.equal(error.fix, "pass --format text|json|md.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: the echoed report_a/report_b path keeps '..' unchanged (normalizePosixPath, not path.normalize)", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    diffFixture(dir, "a.json", []);
    mkdirSync(join(dir, "sub"));
    const raw = `${dir}/sub/../a.json`; // a real, existing file via '..'; the literal segment must survive the echo
    const b = diffFixture(dir, "b.json", []);
    const { envelope } = runJson(["diff", raw, b]);
    assert.ok((envelope.report_a as string).includes("sub/../a.json"), envelope.report_a as string);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff --json escapes non-ASCII content exactly like Python's ensure_ascii=True", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", [{ control: "cé", subject: "😀", outcome: "conformant" }]);
    const lines: string[] = [];
    const original = console.log;
    console.log = (line: string) => lines.push(line);
    try {
      main(["diff", a, b, "--json"]);
    } finally {
      console.log = original;
    }
    const raw = lines.join("\n");
    assert.ok(raw.includes("c\\u00e9"), raw);
    assert.ok(raw.includes("\\ud83d\\ude00"), raw);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// --- readiness (item 18.25) ---------------------------------------------------------------------

test("readiness: a clean report is READY, exit 0, and writes a real report-readiness-*.md file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir, {
      assertions: [{ control: "OVS-03", outcome: "conformant", subject: "s" }],
      integrity: [{ status: "verified", stream: "a" }],
    });
    const { exitCode, envelope } = runJson(["readiness", report, "--catalog-dir", CATALOG_DIR]);
    assert.equal(exitCode, 0);
    assert.equal(envelope.verdict, "READY");
    assert.deepEqual(envelope.reasons, []);
    const reportPath = envelope.report as string;
    assert.ok(reportPath.includes("report-readiness-"), reportPath);
    const written = readFileSync(reportPath, "utf-8");
    assert.ok(written.startsWith("# Report readiness — READY\n"), written);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: broken integrity is NOT READY, exit 1, blocking reasons section written", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir, { integrity: [{ status: "failed", stream: "gw" }] });
    const { exitCode, envelope } = runJson(["readiness", report, "--catalog-dir", CATALOG_DIR]);
    assert.equal(exitCode, 1);
    assert.equal(envelope.verdict, "NOT READY");
    const reportPath = envelope.report as string;
    const written = readFileSync(reportPath, "utf-8");
    assert.ok(written.includes("## Blocking reasons"), written);
    assert.ok(written.includes("- integrity failed on stream gw"), written);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a high-severity insufficient_evidence recorded in --gaps is READY WITH LIMITATIONS", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir, {
      assertions: [{ control: "OVS-03", outcome: "insufficient_evidence", subject: "s" }],
    });
    const gaps = join(dir, "gaps.md");
    writeFileSync(gaps, "OVS-03 owned by alice on 2026-02-01\n");
    const deviations = join(dir, "deviations.yaml");
    writeFileSync(deviations, "deviations: []\n");
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      CATALOG_DIR,
      "--gaps",
      gaps,
      "--deviations",
      deviations,
    ]);
    assert.equal(exitCode, 0);
    assert.equal(envelope.verdict, "READY WITH LIMITATIONS");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: missing report_dir gives input.report_dir_missing with the readiness-specific fix text", () => {
  const { exitCode, envelope } = runJson(["readiness"]);
  assert.equal(exitCode, 3);
  const error = envelope.error as { message_key: string; fix: string };
  assert.equal(error.message_key, "input.report_dir_missing");
  assert.equal(error.fix, "pass the report directory: `agentce readiness <report-dir>`.");
});

test("readiness: a report_dir that is not a directory gives input.report_dir_not_a_directory", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const notADir = join(dir, "nope");
    const { exitCode, envelope } = runJson(["readiness", notADir]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.report_dir_not_a_directory",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: an unrecognized flag gives input.readiness_unrecognized_flag, never a silent misread of report_dir", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson(["readiness", "--gasp", report]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.readiness_unrecognized_flag",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a given-but-bad --gaps path is input.gaps_not_a_file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      CATALOG_DIR,
      "--gaps",
      join(dir, "missing.md"),
    ]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "input.gaps_not_a_file");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a given-but-bad --deviations path is input.deviations_not_a_file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      CATALOG_DIR,
      "--deviations",
      join(dir, "missing.yaml"),
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.deviations_not_a_file",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a bad --catalog-dir is input.catalog-dir_not_a_directory", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      join(dir, "no-such-catalog"),
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.catalog-dir_not_a_directory",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a malformed deviation register gives input.deviation_invalid, never a crash", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const deviations = join(dir, "deviations.yaml");
    writeFileSync(deviations, "deviations: not-a-list\n");
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      CATALOG_DIR,
      "--deviations",
      deviations,
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.deviation_invalid",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: with no --catalog-dir, every vendored base catalog is used", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir, {
      assertions: [{ control: "OVS-03", outcome: "conformant", subject: "s" }],
    });
    const { exitCode, envelope } = runJson(["readiness", report]);
    assert.ok([0, 1].includes(exitCode));
    assert.ok(typeof envelope.verdict === "string");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// --- sign (item 18.26) ---------------------------------------------------------------------------

/** A fresh Ed25519 PEM key file, written under `dir`, and the raw 32-byte public key it derives. */
function edKeyFile(dir: string, name = "key.pem"): { path: string; rawPublicKey: Buffer } {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const path = join(dir, name);
  writeFileSync(path, privateKey.export({ type: "pkcs8", format: "pem" }));
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  return { path, rawPublicKey: Buffer.from(publicDer.subarray(12)) };
}

/** A READY report directory (reusing {@link readinessReport}'s clean shape) with a `claim.json` to
 * sign, the minimal shape `_sign_subjects`/`cmd_sign` need. */
function signReportDir(
  dir: string,
  claim: Record<string, unknown> = { claimant: { org: "acme" } },
): string {
  const reportDir = readinessReport(dir);
  writeFileSync(join(reportDir, "claim.json"), JSON.stringify(claim));
  return reportDir;
}

function edPublicKeyObject(rawPublicKey: Buffer) {
  return createPublicKey({
    key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), rawPublicKey]),
    format: "der",
    type: "spki",
  });
}

test("sign: kms profile happy path signs claim.json, writes a detached signature, exit 0", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { path: keyPath, rawPublicKey } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 0);
    assert.equal(envelope.as, "claimant");
    assert.equal(envelope.profile, "kms");
    assert.equal(envelope.dry_run, false);
    assert.equal(envelope.readiness, "READY");
    assert.equal(envelope.signatures, 1);
    const detachedPath = envelope.signature as string;
    assert.ok(existsSync(detachedPath));
    const detached = JSON.parse(readFileSync(detachedPath, "utf-8"));
    assert.equal(detached.role, "claimant");
    assert.equal(detached.profile, "kms");
    assert.equal(detached.payloadType, "application/vnd.in-toto+json");
    assert.equal(detached.signatures[0].keyid, envelope.keyid);

    const claim = JSON.parse(readFileSync(join(report, "claim.json"), "utf-8"));
    assert.equal(claim.signatures.length, 1);
    assert.deepEqual(claim.signatures[0], detached);
    assert.ok(!("trust_root" in envelope));
    assert.ok(!existsSync(join(report, "trust-root.json")));

    // The signature actually verifies against the key's real derived public key -- a real
    // cryptographic round trip, not a shape-only assertion.
    const payload = Buffer.from(detached.payload as string, "base64");
    const statement = JSON.parse(payload.toString("utf-8"));
    assert.equal(statement._type, "https://in-toto.io/Statement/v1");
    assert.equal(statement.predicate.role, "claimant");
    assert.equal(statement.predicate.profile, "kms");
    const pae = Buffer.concat([
      Buffer.from("DSSEv1 28 application/vnd.in-toto+json ", "ascii"),
      Buffer.from(String(payload.length), "ascii"),
      Buffer.from(" ", "ascii"),
      payload,
    ]);
    const sig = Buffer.from(detached.signatures[0].sig as string, "base64");
    assert.equal(cryptoVerify(null, pae, edPublicKeyObject(rawPublicKey), sig), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --write-trust-root writes a trust-root.json whose public key verifies the signature", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir, { claimant: { org: "acme corp" } });
    const { path: keyPath, rawPublicKey } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "assessor",
      "--profile",
      "kms",
      "--key",
      keyPath,
      "--write-trust-root",
    ]);
    assert.equal(exitCode, 0);
    const trustRootPath = envelope.trust_root as string;
    assert.ok(existsSync(trustRootPath));
    const trustRoot = JSON.parse(readFileSync(trustRootPath, "utf-8"));
    const keyid = envelope.keyid as string;
    assert.equal(trustRoot.keys[keyid].public_key, rawPublicKey.toString("base64"));
    assert.equal(trustRoot.keys[keyid].identity, "acme corp");

    const detached = JSON.parse(readFileSync(envelope.signature as string, "utf-8"));
    const payload = Buffer.from(detached.payload as string, "base64");
    const pae = Buffer.concat([
      Buffer.from("DSSEv1 28 application/vnd.in-toto+json ", "ascii"),
      Buffer.from(String(payload.length), "ascii"),
      Buffer.from(" ", "ascii"),
      payload,
    ]);
    const sig = Buffer.from(detached.signatures[0].sig as string, "base64");
    const publicKeyObject = createPublicKey({
      key: Buffer.concat([
        Buffer.from("302a300506032b6570032100", "hex"),
        Buffer.from(trustRoot.keys[keyid].public_key, "base64"),
      ]),
      format: "der",
      type: "spki",
    });
    assert.equal(cryptoVerify(null, pae, publicKeyObject, sig), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --write-trust-root defaults identity to 'unset' when claimant.org is absent", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir, {});
    const { path: keyPath } = edKeyFile(dir);
    const { envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
      "--write-trust-root",
    ]);
    const trustRoot = JSON.parse(readFileSync(envelope.trust_root as string, "utf-8"));
    const keyid = envelope.keyid as string;
    assert.equal(trustRoot.keys[keyid].identity, "unset");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --dry-run writes nothing, touches no key, even with a bad --key value", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const claimPath = join(report, "claim.json");
    const before = readFileSync(claimPath, "utf-8");
    const beforeMtime = statSync(claimPath).mtimeMs;
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      join(dir, "does-not-exist.pem"),
      "--dry-run",
    ]);
    assert.equal(exitCode, 0);
    assert.equal(envelope.dry_run, true);
    assert.equal(envelope.readiness, "READY");
    assert.ok(!("signature" in envelope));
    assert.equal(readFileSync(claimPath, "utf-8"), before);
    assert.equal(statSync(claimPath).mtimeMs, beforeMtime);
    assert.ok(!existsSync(join(report, "signatures")));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: a second sign call appends a second signatures[] entry rather than replacing the first", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const key1 = edKeyFile(dir, "key1.pem");
    const key2 = edKeyFile(dir, "key2.pem");
    runJson(["sign", report, "--as", "claimant", "--profile", "kms", "--key", key1.path]);
    const { envelope } = runJson([
      "sign",
      report,
      "--as",
      "assessor",
      "--profile",
      "kms",
      "--key",
      key2.path,
    ]);
    assert.equal(envelope.signatures, 2);
    const claim = JSON.parse(readFileSync(join(report, "claim.json"), "utf-8"));
    assert.equal(claim.signatures.length, 2);
    assert.equal(claim.signatures[0].role, "claimant");
    assert.equal(claim.signatures[1].role, "assessor");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: a NOT_READY report refuses with sign.not_ready before touching any key or file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = readinessReport(dir, { integrity: [{ status: "failed", stream: "gw" }] });
    writeFileSync(join(report, "claim.json"), JSON.stringify({}));
    const { path: keyPath } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.not_ready");
    assert.ok(error.detail.startsWith("the report is NOT READY: "));
    assert.equal(
      error.fix,
      "resolve the blocking reasons (agentce readiness <report-dir>) before signing.",
    );
    assert.equal(
      JSON.parse(readFileSync(join(report, "claim.json"), "utf-8")).signatures,
      undefined,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --as absent gives input.sign_role", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "input.sign_role");
    assert.equal(error.detail, "--as must be `claimant` or `assessor`.");
    assert.equal(error.fix, "pass --as claimant|assessor.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --as bogus gives input.sign_role", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--as", "bogus"]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "input.sign_role");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an unknown --profile gives input.sign_profile with a pyRepr-quoted profile name", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "bogus",
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "input.sign_profile");
    assert.equal(error.detail, "unknown signing profile 'bogus'.");
    assert.equal(error.fix, "choose one of: sigstore-public, sigstore-private, kms.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --profile \"it's\" gives input.sign_profile with repr's double-quote switch", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "it's",
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string };
    assert.equal(error.message_key, "input.sign_profile");
    assert.equal(error.detail, `unknown signing profile "it's".`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --profile '' falls through to the sigstore-public default, then refuses keyless_offline", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--as", "claimant", "--profile", ""]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string };
    assert.equal(error.message_key, "sign.keyless_offline");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an omitted --profile defaults to sigstore-public, which refuses offline", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--as", "claimant"]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.keyless_offline");
    assert.equal(
      error.detail,
      "the sigstore-public profile is keyless and obtains a certificate from a Fulcio instance " +
        "(network); the engine does not sign it offline.",
    );
    assert.equal(
      error.fix,
      "use --profile kms --key <file> offline, or run keyless signing where the Fulcio and Rekor " +
        "endpoints are reachable.",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --profile sigstore-private also refuses offline with sign.keyless_offline, even with --key given", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { path: keyPath } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "sigstore-private",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "sign.keyless_offline");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --write-trust-root without profile kms gives sign.trust_root_requires_kms, quoting the profile", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--write-trust-root",
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.trust_root_requires_kms");
    assert.equal(
      error.detail,
      "--write-trust-root needs an exportable public key; the 'sigstore-public' profile has none.",
    );
    assert.equal(
      error.fix,
      "pass --profile kms --key <ed25519-private-key.pem> --write-trust-root.",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: a missing claim.json gives sign.no_claim", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = readinessReport(dir);
    const { path: keyPath } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.no_claim");
    assert.equal(error.detail, "the report directory has no claim.json to sign.");
    assert.equal(error.fix, "produce the report first: `agentce assess … --out <report-dir>`.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an array-shaped claim.json refuses with internal.unexpected, leaving the file untouched", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = readinessReport(dir);
    const claimPath = join(report, "claim.json");
    writeFileSync(claimPath, JSON.stringify([1, 2]));
    const before = readFileSync(claimPath);
    const { path: keyPath } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string };
    assert.equal(error.message_key, "internal.unexpected");
    assert.deepEqual(readFileSync(claimPath), before);
    assert.equal(existsSync(join(report, "signatures")), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: profile kms with no --key gives sign.kms_key_missing", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.kms_key_missing");
    assert.equal(error.detail, "the kms profile signs with an operator-held key.");
    assert.equal(error.fix, "pass --key <ed25519-private-key.pem>.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: profile kms with --key '' gives sign.kms_key_missing (falsy, like an omitted --key)", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      "",
    ]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "sign.kms_key_missing");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --key given but not an existing file gives input.key_not_a_file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      join(dir, "no-such-key.pem"),
    ]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "input.key_not_a_file");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: a non-Ed25519 key gives sign.key_algorithm with the exact catalogued text", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
    const keyPath = join(dir, "rsa.pem");
    writeFileSync(keyPath, privateKey.export({ type: "pkcs8", format: "pem" }));
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.key_algorithm");
    assert.equal(error.detail, "the signing key is not an Ed25519 private key.");
    assert.equal(
      error.fix,
      "supply an Ed25519 key (the algorithm the engine signs with, SPEC §8.7).",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an unparseable key file gives sign.key_unreadable with the exact catalogued text", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const keyPath = join(dir, "garbage.pem");
    writeFileSync(keyPath, "not a pem\n");
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.key_unreadable");
    assert.equal(
      error.detail,
      "the signing key file could not be parsed as an unencrypted PEM private key.",
    );
    assert.equal(
      error.fix,
      "supply an unencrypted Ed25519 private key PEM (`openssl genpkey -algorithm ed25519 " +
        "-out key.pem`, or `agentce catalog sign --new-key <path>`).",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an unrecognized flag gives input.sign_unrecognized_flag, never a silent misread of report_dir", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--role", "claimant"]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.sign_unrecognized_flag",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
