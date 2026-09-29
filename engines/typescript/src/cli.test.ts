/**
 * `assess`, `validate`, `report`, and `quickstart` on the real CLI entry point (`main`), over the
 * vendored quickstart project — the same commands `docs/quickstart-typescript.md` tells a Node adopter
 * to run, and the same data every installed package carries (SPEC §13.4 AX-1).
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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
