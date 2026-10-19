/**
 * Every output a command writes is checked through one role and refused under its key: a target of
 * the wrong type, one that cannot be reached, or a folder that cannot be written answers exit 3
 * before anything is written, and a FIFO is never waited on (SPEC §8.7, §8.5; item 18.180). Mirrors
 * Python's tests/test_output_paths.py.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { generateKeyPairSync } from "node:crypto";
import {
  chmodSync,
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { quickstartDir } from "./bundled";
import { InputError } from "./errors";
import {
  DANGLING,
  NOT_A_DIRECTORY,
  NOT_A_FILE,
  type OutputRole,
  UNREACHABLE,
  UNWRITABLE,
  checkOutput,
  writeFile,
  writeOutput,
} from "./outputs";
import { runJson } from "./testSupport";

const QS = quickstartDir();
const FILE: OutputRole = { key: "input.out_file_unwritable", flag: "--out", fix: "f" };
const DIR: OutputRole = {
  key: "input.out_dir_unwritable",
  flag: "--out",
  fix: "f",
  directory: true,
};
const ROOT = process.getuid?.() === 0;

function inScratch(body: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-outputs-"));
  try {
    body(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function mkfifo(path: string): void {
  execFileSync("mkfifo", [path]);
}

function refusal(raw: string, role: OutputRole): string {
  try {
    checkOutput(raw, role);
  } catch (err) {
    assert.ok(err instanceof InputError);
    assert.equal(err.key, role.key);
    return err.cause;
  }
  assert.fail(`${raw} was not refused`);
}

function keyOf(argv: string[]): [number, unknown] {
  const { exitCode, envelope } = runJson(argv);
  return [exitCode, (envelope.error as { message_key?: unknown } | undefined)?.message_key];
}

test("each reason a target cannot be written is named under the role's key", () => {
  inScratch((dir) => {
    assert.ok(refusal(dir, FILE).includes(NOT_A_FILE));
    writeFileSync(join(dir, "f"), "x");
    assert.ok(refusal(join(dir, "f"), DIR).includes(NOT_A_DIRECTORY));
    mkfifo(join(dir, "fifo"));
    assert.ok(refusal(join(dir, "fifo"), FILE).includes(NOT_A_FILE));
    assert.throws(() => writeOutput(join(dir, "fifo"), FILE, "x"), InputError);
    assert.throws(() => writeFile(join(dir, "fifo"), "x"));
    symlinkSync(join(dir, "gone"), join(dir, "link"));
    assert.ok(refusal(join(dir, "link"), FILE).includes(DANGLING));
    symlinkSync("loop", join(dir, "loop"));
    for (const raw of ["loop", "loop/x", "n".repeat(300), `${"n".repeat(300)}/x`]) {
      assert.ok(refusal(join(dir, raw), FILE).includes(UNREACHABLE), raw);
    }
    writeOutput(join(dir, "a", "b", "r.md"), FILE, "report");
    assert.equal(readFileSync(join(dir, "a", "b", "r.md"), "utf-8"), "report");
  });
});

test("a read-only or unenterable folder is refused", { skip: ROOT }, () => {
  inScratch((dir) => {
    const ro = join(dir, "ro");
    const shut = join(dir, "shut");
    mkdirSync(ro, { mode: 0o555 });
    mkdirSync(shut, { mode: 0o000 });
    try {
      assert.ok(refusal(join(ro, "x.md"), FILE).includes(UNWRITABLE));
      assert.ok(refusal(ro, DIR).includes(UNWRITABLE));
      assert.ok(refusal(join(shut, "x.md"), FILE).includes(UNREACHABLE));
    } finally {
      chmodSync(ro, 0o755);
      chmodSync(shut, 0o755);
    }
  });
});

test("report --out on a FIFO is refused, not waited on", () => {
  inScratch((dir) => {
    mkfifo(join(dir, "fifo"));
    const from = join(dir, "assertions.json");
    writeFileSync(from, "[]");
    assert.deepEqual(keyOf(["report", "--from", from, "--out", join(dir, "fifo")]), [
      3,
      "input.out_file_unwritable",
    ]);
  });
});

function assessed(dir: string): string {
  const out = join(dir, "report");
  const { exitCode } = runJson([
    "assess",
    "--bundle",
    join(QS, "evidence"),
    "--profile",
    join(QS, "applicability.yaml"),
    "--domain",
    join(QS, "domain.linkml.yaml"),
    "--out",
    out,
  ]);
  assert.ok(exitCode === 0 || exitCode === 1);
  return out;
}

function keyFile(dir: string): string {
  const { privateKey } = generateKeyPairSync("ed25519");
  const path = join(dir, "k.pem");
  writeFileSync(path, privateKey.export({ type: "pkcs8", format: "pem" }));
  return path;
}

function snapshot(root: string): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (rel: string): void => {
    for (const name of readdirSync(join(root, rel)).sort()) {
      const path = join(root, rel, name);
      const info = statSync(path);
      if (info.isDirectory()) {
        walk(join(rel, name));
      } else if (info.isFile()) {
        out[join(rel, name)] = readFileSync(path, "base64");
      }
    }
  };
  walk("");
  return out;
}

test("sign refuses a bad write target before rewriting the claim", () => {
  inScratch((dir) => {
    const base = assessed(dir);
    const key = keyFile(dir);
    for (const kind of ["fifo", "folder", "signatures_file"]) {
      const report = join(dir, kind);
      cpSync(base, report, { recursive: true });
      rmSync(join(report, "signatures"), { recursive: true, force: true });
      if (kind === "signatures_file") {
        writeFileSync(join(report, "signatures"), "x");
      } else {
        mkdirSync(join(report, "signatures"));
        const target = join(report, "signatures", "claimant-kms.dsse.json");
        if (kind === "fifo") {
          mkfifo(target);
        } else {
          mkdirSync(target);
        }
      }
      const before = snapshot(report);
      assert.deepEqual(
        keyOf(["sign", report, "--as", "claimant", "--profile", "kms", "--key", key]),
        [3, "input.report_dir_unwritable"],
        kind,
      );
      assert.deepEqual(snapshot(report), before, kind);
    }
    assert.equal(keyOf(["sign", base, "--as", "claimant", "--profile", "kms", "--key", key])[0], 0);
    assert.ok(statSync(join(base, "signatures", "claimant-kms.dsse.json")).isFile());
  });
});

test("readiness on a read-only report warns under its key", { skip: ROOT }, () => {
  inScratch((dir) => {
    const report = assessed(dir);
    chmodSync(report, 0o555);
    try {
      const { exitCode, envelope } = runJson(["readiness", report]);
      assert.ok(exitCode === 0 || exitCode === 1);
      assert.equal(envelope.report, null);
      assert.equal(envelope.report_warning, "warning.readiness_file_unwritten");
    } finally {
      chmodSync(report, 0o755);
    }
  });
});
