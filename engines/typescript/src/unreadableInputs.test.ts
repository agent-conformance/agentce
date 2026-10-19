/**
 * A path the user names that cannot be read, or whose name is too long, is refused with the role's
 * key, never internal.unexpected; conformance run reads no_ml from this engine's own install, never
 * from the folder --engine names (item 18.179). Mirrors Python's tests/test_unreadable_inputs.py.
 */
import assert from "node:assert/strict";
import { chmodSync, cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { quickstartDir } from "./bundled";
import { runEcs } from "./conformance";
import { runJson } from "./testSupport";

const QS = quickstartDir();
const BUNDLE = join(QS, "evidence");
const PROFILE = join(QS, "applicability.yaml");
const REPO = join(__dirname, "..", "..", "..");
const ROOT_READS_ANYTHING = process.getuid?.() === 0;

function refusal(argv: string[]): { exitCode: number; key: unknown } {
  const { exitCode, envelope } = runJson(argv);
  return { exitCode, key: (envelope.error as { message_key?: unknown } | undefined)?.message_key };
}

function inScratch(body: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-unreadable-"));
  try {
    body(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test(
  "an unreadable file is refused with the role's _unreadable key (18.179)",
  {
    skip: ROOT_READS_ANYTHING,
  },
  () => {
    inScratch((dir) => {
      const locked = join(dir, "locked.json");
      writeFileSync(locked, "[]");
      chmodSync(locked, 0);
      mkdirSync(join(dir, "r"));
      const cases: Array<[string[], string]> = [
        [["assess", "--bundle", BUNDLE, "--profile", locked], "input.profile_unreadable"],
        [
          ["assess", "--bundle", BUNDLE, "--profile", PROFILE, "--trust-root", locked],
          "input.trust_root_unreadable",
        ],
        [["readiness", join(dir, "r"), "--gaps", locked], "input.gaps_unreadable"],
        [["diff", locked, locked], "input.report_a_unreadable"],
        [["report", "--from", locked], "input.from_unreadable"],
      ];
      try {
        for (const [argv, key] of cases) {
          assert.deepEqual(refusal(argv), { exitCode: 3, key }, argv.join(" "));
        }
      } finally {
        chmodSync(locked, 0o600);
      }
    });
  },
);

test("a 300-byte name is not a file (18.179)", () => {
  inScratch((dir) => {
    const long = join(dir, "a".repeat(300));
    assert.deepEqual(refusal(["assess", "--bundle", BUNDLE, "--profile", long]), {
      exitCode: 3,
      key: "input.profile_not_a_file",
    });
  });
});

test(
  "an adapters folder that cannot be listed or entered is refused (18.179)",
  {
    skip: ROOT_READS_ANYTHING,
  },
  () => {
    inScratch((dir) => {
      const adapters = join(dir, "adapters");
      mkdirSync(adapters);
      chmodSync(adapters, 0o600);
      const argv = [
        "conformance",
        "run",
        "--engine",
        join(REPO, "engines", "typescript"),
        "--corpus",
        join(REPO, "corpus"),
        "--adapters",
        adapters,
      ];
      try {
        assert.deepEqual(refusal(argv), { exitCode: 3, key: "input.adapters_unreadable" });
      } finally {
        chmodSync(adapters, 0o700);
      }
    });
  },
);

test("conformance run's no_ml does not follow --engine to another engine's folder (18.179)", () => {
  inScratch((dir) => {
    const corpus = join(dir, "corpus");
    mkdirSync(join(corpus, "projects"), { recursive: true });
    cpSync(QS, join(corpus, "projects", "q"), { recursive: true });
    writeFileSync(
      join(corpus, "corpus-manifest.json"),
      JSON.stringify({ corpus_version: "t", projects: [{ id: "q" }] }),
    );
    // engines/python has no pnpm-lock.yaml: reading the lock under --engine crashed here.
    const report = runEcs({
      enginePath: join(REPO, "engines", "python"),
      corpusDir: corpus,
      outDir: null,
    });
    assert.equal(report.no_ml, "pass");
    assert.equal(report.claim, "full");
  });
});
