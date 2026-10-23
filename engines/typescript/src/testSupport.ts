/** Shared test-only helpers: unwritable-directory error paths and running the CLI for its envelope. */

import {
  constants,
  accessSync,
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { main } from "./cli";

/** Run `agentce <argv> --json` in-process; return its exit code and the parsed `--json` envelope. */
export function runJson(argv: string[]): { exitCode: number; envelope: Record<string, unknown> } {
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

/**
 * Creates a fresh temp directory, chmods it to 0o555, and runs `body` against it -- unless this
 * process can still write to a mode-555 directory (e.g. running as root), in which case there is
 * nothing to prove and `body` is skipped. Always restores permissions and removes the temp
 * directory afterward, even on failure.
 */
export function withUnwritableDir(prefix: string, body: (dir: string) => void): void {
  const work = mkdtempSync(join(tmpdir(), prefix));
  const dir = join(work, "ro");
  mkdirSync(dir, { mode: 0o555 });
  try {
    accessSync(dir, constants.W_OK);
    return; // this user can write to a mode-555 directory; nothing to prove here
  } catch {
    // expected: not writable, proceed
  }
  try {
    body(dir);
  } finally {
    chmodSync(dir, 0o755);
    rmSync(work, { recursive: true, force: true });
  }
}

/** One row of the shared report-file-rule table (`verification/gates/fixtures/report_file_rule/cases.json`, 18.184q). */
export interface ReportFileRuleCase {
  readonly id: string;
  readonly file: string;
  readonly content: string | null;
  readonly repeat?: { readonly open: string; readonly close: string; readonly times: number };
  readonly readiness: string[];
  readonly validate: "refused" | "valid" | null;
}

/** The shared report-file-rule cases, read by Python's and Java's tests too. */
export const REPORT_FILE_RULE_CASES: readonly ReportFileRuleCase[] = JSON.parse(
  readFileSync(
    join(
      __dirname,
      "..",
      "..",
      "..",
      "verification",
      "gates",
      "fixtures",
      "report_file_rule",
      "cases.json",
    ),
    "utf-8",
  ),
).cases;

/** A case's content with `@@` expanded as cases.json describes; `null` deletes the file. */
export function ruleCaseContent({ content, repeat }: ReportFileRuleCase): string | null {
  if (content === null || repeat === undefined) {
    return content;
  }
  return content.replace(
    "@@",
    () => repeat.open.repeat(repeat.times) + repeat.close.repeat(repeat.times),
  );
}
