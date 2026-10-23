/**
 * Every case in `spec/cli/argv-cases.json` (SPEC 8.5, "Command-line grammar") through the real
 * scanner and the real CLI entry: exit code and key as the table says, a usage for a help line, the
 * parsed values of an accept case, and a refused run that leaves its report directory byte-identical
 * (18.184w). A case whose `engines` leave this engine out runs a command it has not built, and gets
 * cli.not_implemented. Mirrors Python's `tests/test_argv_cases.py`.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { after, before, test } from "node:test";
import { command, grammar, scan } from "./argv";
import { main } from "./cli";

const REPO = join(__dirname, "..", "..", "..");

interface Case {
  readonly id: string;
  readonly rule: string;
  readonly argv: string[];
  readonly exit: number;
  readonly key: string | null;
  readonly engines?: string[];
  readonly scan?: Record<string, unknown>;
  readonly unchanged?: string;
}

const CASES: readonly Case[] = JSON.parse(
  readFileSync(join(REPO, "spec", "cli", "argv-cases.json"), "utf-8"),
).cases;

const places: Record<string, string> = {};
const home = process.cwd();
let root = "";

/** `main` in-process, from a fresh temp cwd: its exit code and everything it printed. */
function run(argv: string[]): { code: number; out: string } {
  const chunks: string[] = [];
  const log = console.log;
  const write = process.stdout.write;
  const errWrite = process.stderr.write;
  console.log = (line: string) => {
    chunks.push(`${line}\n`);
  };
  process.stdout.write = ((chunk: string) => {
    chunks.push(chunk);
    return true;
  }) as typeof process.stdout.write;
  process.stderr.write = (() => true) as typeof process.stderr.write;
  process.chdir(mkdtempSync(join(root, "cwd-")));
  try {
    return { code: main(argv), out: chunks.join("") };
  } finally {
    console.log = log;
    process.stdout.write = write;
    process.stderr.write = errWrite;
    process.chdir(home);
  }
}

function fill<T>(value: T): T {
  if (typeof value === "string") {
    let filled: string = value;
    for (const [name, path] of Object.entries(places)) {
      filled = filled.split(name).join(path);
    }
    return filled as T;
  }
  if (Array.isArray(value)) {
    return value.map(fill) as T;
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, fill(v)])) as T;
  }
  return value;
}

function digest(dir: string): Record<string, string> {
  const found: Record<string, string> = {};
  const walk = (at: string): void => {
    for (const entry of readdirSync(at, { withFileTypes: true })) {
      const path = join(at, entry.name);
      if (entry.isDirectory()) {
        walk(path);
      } else {
        found[relative(dir, path)] = createHash("sha256").update(readFileSync(path)).digest("hex");
      }
    }
  };
  walk(dir);
  return found;
}

/** The refusal's key: the envelope's when `--json` comes before any `--`, else the text line's. */
function keyOf(line: string[], code: number, out: string): string | null {
  const at = line.indexOf("--");
  const head = at < 0 ? line : line.slice(0, at);
  if (head.includes("--json") && code !== 0) {
    const error = JSON.parse(out).error as { message_key: string };
    return error.message_key;
  }
  const found = /^([a-z_]+\.[a-z0-9_.]+): /.exec(out);
  return found === null ? null : (found[1] as string);
}

const implemented = (c: Case): boolean => {
  const name = fill(c.argv).find((token) => !token.startsWith("-")) as string;
  return command(name).implementedIn.includes("typescript");
};

before(() => {
  root = mkdtempSync(join(tmpdir(), "argv-cases-"));
  const report = join(root, "report");
  const space = join(root, "sp ace");
  places["{report}"] = report;
  places["{space}"] = space;
  assert.equal(run(["quickstart", "--out", report]).code, 0);
  mkdirSync(space);
  cpSync(join(REPO, "spec", "catalogs", "base", "eu-ai-act"), join(space, "cat"), {
    recursive: true,
  });
  writeFileSync(join(space, "gaps.md"), "");
});

after(() => {
  process.chdir(home);
});

for (const c of CASES) {
  test(`case ${c.id} through the CLI`, () => {
    const line = fill(c.argv);
    const ours = c.engines === undefined || c.engines.includes("typescript");
    if (!ours && !implemented(c)) {
      const { code, out } = run(["--json", ...line]);
      assert.equal(code, 3, out);
      assert.match(out, /"cli\.not_implemented"/);
      return;
    }
    const unchanged = c.unchanged === undefined ? undefined : fill(c.unchanged);
    const was = unchanged === undefined ? undefined : digest(unchanged);
    const { code, out } = run(line);
    assert.deepEqual([code, keyOf(line, code, out)], [c.exit, c.key], out);
    if (c.rule === "help_after_command") {
      assert.ok(out.startsWith("usage: agentce"), out);
    }
    if (unchanged !== undefined) {
      assert.deepEqual(digest(unchanged), was);
    }
  });
}

for (const c of CASES.filter((c) => c.scan !== undefined)) {
  test(`case ${c.id} reads its values`, () => {
    const line = fill(c.argv);
    const at = line.findIndex((token) => !token.startsWith("-"));
    const read = scan(command(line[at] as string), line.slice(at + 1), { seen: line.slice(0, at) });
    for (const [field, want] of Object.entries(fill(c.scan as Record<string, unknown>))) {
      assert.deepEqual(read[field as keyof typeof read], want, field);
    }
  });
}

test("every grammar rule has a case, and every command appears in one", () => {
  assert.deepEqual(new Set(grammar().rules), new Set(CASES.map((c) => c.rule)));
  const words = new Set(CASES.flatMap((c) => c.argv.filter((t) => !t.startsWith("-"))));
  for (const name of Object.keys(grammar().commands)) {
    assert.ok(words.has(name), name);
  }
});
