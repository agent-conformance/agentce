/**
 * `assess`, `validate`, `report`, and `quickstart` on the real CLI entry point (`main`), over the
 * vendored quickstart project — the same commands `docs/quickstart-typescript.md` tells a Node adopter
 * to run, and the same data every installed package carries (SPEC §13.4 AX-1).
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
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

    // The manifest's invocation never carries this machine's raw bundle/profile path (SPEC §8.4, F25).
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
