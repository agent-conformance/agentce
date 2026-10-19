/**
 * An empty path value ('' or --opt=) is refused with the role's key before anything is read or
 * written, never read as the working directory, the default, or no value (item 18.178). Mirrors
 * Python's tests/test_empty_path_values.py for the commands this engine implements.
 */
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { catalogsDir, quickstartDir } from "./bundled";
import { main } from "./cli";

const QS = quickstartDir();
const BUNDLE = join(QS, "evidence");
const PROFILE = join(QS, "applicability.yaml");
// A catalog the vendored trust root verifies, so a dropped --trust-root '' would assess and exit 0 or 2.
const ASSESS_SIGNED = [
  "assess",
  "--bundle",
  BUNDLE,
  "--profile",
  PROFILE,
  "--domain",
  join(QS, "domain.linkml.yaml"),
  "--catalog-dir",
  join(catalogsDir(), "base", "eu-ai-act"),
];

function files(root: string): string[] {
  return (readdirSync(root, { recursive: true }) as string[]).sort();
}

function refusal(argv: string[]): { exitCode: number; key: unknown } {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => {
    lines.push(line);
  };
  let exitCode: number;
  try {
    exitCode = main([argv[0] as string, "--json", ...argv.slice(1)]);
  } finally {
    console.log = original;
  }
  const envelope = JSON.parse(lines.join("\n")) as { error?: { message_key?: unknown } };
  return { exitCode, key: envelope.error?.message_key };
}

/** Runs `body` with the working directory set to a fresh folder holding `r/` and `a.json`. */
function inScratch(body: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-empty-path-"));
  const cwd = process.cwd();
  try {
    mkdirSync(join(dir, "r"));
    writeFileSync(join(dir, "a.json"), "[]");
    process.chdir(dir);
    body(dir);
  } finally {
    process.chdir(cwd);
    rmSync(dir, { recursive: true, force: true });
  }
}

test("an empty path value is refused with the role's key and writes nothing (18.178)", () => {
  const cases: Array<[string[], string]> = [
    [["assess", ""], "input.records_not_a_directory"],
    [["assess", "--bundle", "", "--profile", PROFILE], "input.bundle_not_a_directory"],
    [["assess", "--bundle=", "--profile", PROFILE], "input.bundle_not_a_directory"],
    [
      ["assess", "--bundle", BUNDLE, "--profile", PROFILE, "--catalog-dir", ""],
      "input.catalog_dir_not_a_directory",
    ],
    [["assess", "--bundle", BUNDLE, "--profile", PROFILE, "--out", ""], "input.out_dir_unwritable"],
    [["assess", "--bundle", BUNDLE, "--profile", PROFILE, "--out="], "input.out_dir_unwritable"],
    [
      ["assess", "--bundle", BUNDLE, "--profile", PROFILE, "--state", ""],
      "input.state_dir_unwritable",
    ],
    [
      ["assess", "--bundle", BUNDLE, "--profile", PROFILE, "--state="],
      "input.state_dir_unwritable",
    ],
    [[...ASSESS_SIGNED, "--trust-root", ""], "input.trust_root_not_a_file"],
    [[...ASSESS_SIGNED, "--trust-root="], "input.trust_root_not_a_file"],
    [["quickstart", "--out", ""], "input.out_dir_unwritable"],
    [["sign", ""], "input.report_dir_not_a_directory"],
    [["readiness", ""], "input.report_dir_not_a_directory"],
    [["readiness", "r", "--gaps", ""], "input.gaps_not_a_file"],
    [["readiness", "r", "--deviations="], "input.deviations_not_a_file"],
    [["readiness", "r", "--catalog-dir", ""], "input.catalog_dir_not_a_directory"],
    [["report", "--from", "a.json", "--out", ""], "input.out_file_unwritable"],
  ];
  inScratch((dir) => {
    const before = files(dir);
    for (const [argv, key] of cases) {
      assert.deepEqual(refusal(argv), { exitCode: 3, key }, argv.join(" "));
      assert.deepEqual(files(dir), before, argv.join(" "));
    }
  });
});

test("kms sign --key '' gets the key an absent key file gets (18.178)", () => {
  inScratch((dir) => {
    assert.equal(refusal(["quickstart", "--out", join(dir, "rep")]).exitCode, 0);
    const before = files(dir);
    for (const key of [["--key", ""], ["--key="]]) {
      const argv = ["sign", "rep", "--as", "claimant", "--profile", "kms", ...key];
      assert.deepEqual(refusal(argv), { exitCode: 3, key: "input.key_not_a_file" }, argv.join(" "));
    }
    assert.deepEqual(files(dir), before);
  });
});

test("the signed catalog assesses with no --trust-root, so the refusal is the empty value's (18.178)", () => {
  inScratch((dir) => {
    const { exitCode } = refusal([...ASSESS_SIGNED, "--out", join(dir, "out")]);
    assert.ok(exitCode === 0 || exitCode === 2, `exit ${exitCode}`);
  });
});
