/**
 * A run leaves --out as it found it when it is refused for a bad state.json or for a link in --out
 * that leads outside it (SPEC 8.7, ruling R7; census odd-inputs lines 85, 87 and 89, 18.184s).
 * Mirrors Python's tests/test_out_links_and_early_state.py.
 */
import assert from "node:assert/strict";
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { quickstartDir } from "./bundled";
import { runJson } from "./testSupport";

const QS = quickstartDir();

function assess(out: string, ...extra: string[]): string[] {
  return [
    "assess",
    "--bundle",
    join(QS, "evidence"),
    "--profile",
    join(QS, "applicability.yaml"),
    "--domain",
    join(QS, "domain.linkml.yaml"),
    "--out",
    out,
    ...extra,
  ];
}

function run(argv: string[]): { exitCode: number; key: unknown; cause: string } {
  const { exitCode, envelope } = runJson(argv);
  const error = envelope.error as { message_key?: unknown; detail?: string } | undefined;
  return { exitCode, key: error?.message_key, cause: error?.detail ?? "" };
}

/** Every path under `folder`, relative and sorted, without following links. */
function files(folder: string): string[] {
  const found: string[] = [];
  const walk = (dir: string, rel: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      found.push(`${rel}${entry.name}`);
      if (entry.isDirectory()) {
        walk(join(dir, entry.name), `${rel}${entry.name}/`);
      }
    }
  };
  walk(folder, "");
  return found.sort();
}

function inScratch(body: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-out-links-"));
  try {
    body(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const BAD_STATE: Array<[string, Buffer, string]> = [
  [
    "nonutf8",
    Buffer.from([0x7b, 0x22, 0x78, 0x22, 0x3a, 0x20, 0x22, 0xff, 0xfe, 0x22, 0x7d]),
    "input.state_unreadable",
  ],
  ["malformed", Buffer.from("{not json"), "input.state_unreadable"],
  ["empty", Buffer.from(""), "input.state_unreadable"],
  ["array", Buffer.from("[]"), "input.state_unreadable"],
  [
    "duplicate_key",
    Buffer.from('{"state_version": 1, "state_version": 1}'),
    "input.state_unreadable",
  ],
  ["no_version", Buffer.from("{}"), "input.state_version_incompatible"],
];

for (const [name, content, key] of BAD_STATE) {
  test(`a bad state.json (${name}) is refused before --out is written (18.184s)`, () => {
    inScratch((dir) => {
      const state = join(dir, "st");
      mkdirSync(state);
      writeFileSync(join(state, "state.json"), content);
      const out = join(dir, "out");
      const { exitCode, key: got } = run(assess(out, "--state", state));
      assert.deepEqual([exitCode, got], [3, key]);
      assert.ok(!existsSync(out) || files(out).length === 0);
    });
  });
}

test("a state.json with a BOM is read (18.184s)", () => {
  inScratch((dir) => {
    const first = join(dir, "st");
    assert.equal(run(assess(join(dir, "o1"), "--state", first)).exitCode, 0);
    const stateFile = join(first, "state.json");
    writeFileSync(
      stateFile,
      Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), readFileSync(stateFile)]),
    );
    assert.equal(run(assess(join(dir, "o2"), "--state", first)).exitCode, 0);
  });
});

test("the state key wins over a missing deviations file (18.184s)", () => {
  inScratch((dir) => {
    const state = join(dir, "st");
    mkdirSync(state);
    writeFileSync(join(state, "state.json"), "{not json");
    const argv = assess(
      join(dir, "out"),
      "--state",
      state,
      "--deviations",
      join(dir, "absent.yaml"),
    );
    const { exitCode, key } = run(argv);
    assert.deepEqual([exitCode, key], [3, "input.state_unreadable"]);
  });
});

const ENTRIES: Array<[string, boolean]> = [
  ["integrity.jsonl", false],
  ["manifest.json", false],
  ["report.md", false],
  ["quarantine.jsonl", false],
  ["claim.json", false],
  ["results.sarif", false],
  ["not-written-by-the-run.txt", false],
  ["packs", true],
  ["skill", true],
];

for (const command of ["assess", "quickstart"]) {
  for (const [entry, folder] of ENTRIES) {
    test(`${command}: a link in --out leading outside (${entry}) is refused (18.184s)`, () => {
      inScratch((dir) => {
        const outside = join(dir, "outside");
        if (folder) {
          mkdirSync(outside);
          writeFileSync(join(outside, "kept.txt"), "kept\n");
        } else {
          writeFileSync(outside, "kept\n");
        }
        const out = join(dir, "out");
        mkdirSync(out);
        symlinkSync(outside, join(out, entry), folder ? "dir" : "file");
        const argv = command === "assess" ? assess(out) : ["quickstart", "--out", out];
        const { exitCode, key, cause } = run(argv);
        assert.deepEqual([exitCode, key], [3, "input.out_link_outside"]);
        assert.ok(cause.includes(entry), cause);
        assert.deepEqual(files(out), [entry]);
        if (folder) {
          assert.deepEqual(files(outside), ["kept.txt"]);
        } else {
          assert.equal(readFileSync(outside, "utf-8"), "kept\n");
        }
      });
    });
  }
}

test("a link deeper in --out leading outside is refused, named by its relative path (18.184s)", () => {
  inScratch((dir) => {
    const out = join(dir, "out");
    mkdirSync(join(out, "packs"), { recursive: true });
    symlinkSync(join(dir, "elsewhere.txt"), join(out, "packs", "x.json"));
    const { exitCode, key, cause } = run(["quickstart", "--out", out]);
    assert.deepEqual([exitCode, key], [3, "input.out_link_outside"]);
    assert.equal(
      cause,
      "packs/x.json in --out links outside the output folder, so the run would write through it and change a file elsewhere.",
    );
  });
});

test("a dangling link pointing outside is refused (18.184s)", () => {
  inScratch((dir) => {
    const out = join(dir, "out");
    mkdirSync(out);
    symlinkSync(join(dir, "missing.jsonl"), join(out, "integrity.jsonl"));
    const { exitCode, key } = run(["quickstart", "--out", out]);
    assert.deepEqual([exitCode, key], [3, "input.out_link_outside"]);
    assert.ok(!existsSync(join(dir, "missing.jsonl")));
  });
});

test("a link inside --out is followed (18.184s)", () => {
  inScratch((dir) => {
    const out = join(dir, "out");
    mkdirSync(out);
    writeFileSync(join(out, "notes.txt"), "mine\n");
    symlinkSync(join(out, "notes.txt"), join(out, "notes-link.txt"));
    assert.equal(run(["quickstart", "--out", out]).exitCode, 0);
    assert.ok(lstatSync(join(out, "notes-link.txt")).isSymbolicLink());
  });
});

for (const command of ["assess", "quickstart"]) {
  for (const where of ["unenterable parent", "name too long"]) {
    test(`${command} --out with ${where} is input.out_dir_unwritable, never internal.unexpected (18.184zd)`, (t) => {
      if (where === "unenterable parent" && process.getuid?.() === 0) {
        t.skip("root enters any folder");
        return;
      }
      inScratch((dir) => {
        const shut = join(dir, "shut");
        mkdirSync(shut);
        const out = where === "unenterable parent" ? join(shut, "out") : join(dir, "n".repeat(300));
        const argv = command === "assess" ? assess(out) : ["quickstart", "--out", out];
        chmodSync(shut, 0o000);
        try {
          const { exitCode, key } = run(argv);
          assert.deepEqual([exitCode, key], [3, "input.out_dir_unwritable"]);
        } finally {
          chmodSync(shut, 0o755);
        }
      });
    });
  }
}
