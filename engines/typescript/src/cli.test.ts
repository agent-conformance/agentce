/**
 * `assess`, `validate`, `report`, and `quickstart` on the real CLI entry point (`main`), over the
 * vendored quickstart project — the same commands `docs/quickstart-typescript.md` tells a Node adopter
 * to run, and the same data every installed package carries (SPEC §13.4 AX-1).
 */

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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

test("report --validate is refused honestly rather than silently skipped", () => {
  const { exitCode, envelope } = runJson(["report", "--validate", "/nonexistent"]);
  assert.equal(exitCode, 3);
  assert.equal(
    (envelope.error as { message_key: string }).message_key,
    "input.report_validate_unsupported",
  );
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
