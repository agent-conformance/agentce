/**
 * The report-file reader contract (SPEC 8.7) against the shared case table
 * `spec/report/reader-cases.json`, the rows Python's and Java's tests and
 * `tools/report_validate_parity_check.py` read too. Each case edits one file of a fresh full report
 * folder this engine wrote, runs `report --validate`, `readiness`, `sign` or `diff` through the CLI
 * entry point, and checks the exit code, the message key and that the problem line or error message
 * names the rule.
 */

import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import {
  cpSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { runJson } from "./testSupport";

const REPO = join(__dirname, "..", "..", "..");

interface Edit {
  readonly op: "write" | "prepend" | "append" | "replace" | "replace_all";
  readonly hex: string;
  readonly find_hex?: string;
}

interface ReaderCase {
  readonly id: string;
  readonly rule: string;
  readonly command: "report-validate" | "readiness" | "sign" | "diff";
  readonly file: string;
  readonly edits: readonly Edit[];
  readonly exit: number;
  readonly key?: string;
  readonly names?: string;
}

const CASES: readonly ReaderCase[] = JSON.parse(
  readFileSync(join(REPO, "spec", "report", "reader-cases.json"), "utf-8"),
).cases;

const EMIT = "md,html,oscal,sarif,pack,junit,csv,oscal_xml";

function xmlEscape(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** A JSON value as XML elements: an object's members as child elements, an array's entries as
 * `<item>`, a scalar as escaped text (the shape Python's oscal_xml writer gives). */
function jsonToXml(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((item) => `<item>${jsonToXml(item)}</item>`).join("");
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `<${key}>${jsonToXml(item)}</${key}>`)
      .join("");
  }
  return value === null ? "" : xmlEscape(String(value));
}

/** report.csv, report.junit.xml and oscal-ar.xml as Python's --emit writes them, from the run's own
 * assertions.json and oscal-ar.json: this engine refuses --emit with input.emit_unsupported until
 * 18.16b ports the formats, and the case table edits those three files too. */
function writeEmitOnlyFormats(out: string): void {
  const assertions = JSON.parse(readFileSync(join(out, "assertions.json"), "utf-8")) as Array<
    Record<string, unknown>
  >;
  const columns = ["control", "subject", "outcome", "control_version", "rung", "mode"];
  const csv = [columns.join(","), ...assertions.map((a) => columns.map((c) => a[c]).join(","))];
  writeFileSync(join(out, "report.csv"), `${csv.join("\n")}\n`);
  const failing = assertions.filter((a) => a.outcome !== "conformant");
  const cases = assertions.map((a) => {
    const open = `<testcase classname="${xmlEscape(String(a.subject))}" name="${xmlEscape(String(a.control))}"`;
    if (a.outcome === "conformant") {
      return `${open} />`;
    }
    const outcome = xmlEscape(String(a.outcome));
    return `${open}><failure message="${outcome}" type="agentce.non_conformant">${xmlEscape(String(a.control))} on ${xmlEscape(String(a.subject))}: ${outcome}</failure></testcase>`;
  });
  writeFileSync(
    join(out, "report.junit.xml"),
    `<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="agentce" tests="${assertions.length}" failures="${failing.length}" errors="0" skipped="0">${cases.join("")}</testsuite>\n`,
  );
  const oscal = JSON.parse(readFileSync(join(out, "oscal-ar.json"), "utf-8"));
  writeFileSync(
    join(out, "oscal-ar.xml"),
    `<?xml version="1.0" encoding="UTF-8"?>\n${jsonToXml(oscal)}\n`,
  );
}

let work = "";
let base = "";
let keyPath = "";

before(() => {
  work = mkdtempSync(join(tmpdir(), "agentce-reader-cases-"));
  base = join(work, "base");
  const quickstart = join(REPO, "corpus", "quickstart");
  const argv = [
    "assess",
    "--bundle",
    join(quickstart, "evidence"),
    "--profile",
    join(quickstart, "applicability.yaml"),
    "--domain",
    join(quickstart, "domain.linkml.yaml"),
    "--catalog",
    "eu-ai-act@2026.09",
    "--catalog-dir",
    join(REPO, "spec", "catalogs", "base", "eu-ai-act"),
    "--out",
    base,
  ];
  const emitted = runJson([...argv, "--emit", EMIT]);
  if (emitted.exitCode !== 0) {
    const error = emitted.envelope.error as Record<string, unknown>;
    assert.equal(error.message_key, "input.emit_unsupported");
    const core = runJson(argv);
    assert.equal(core.exitCode, 0, JSON.stringify(core.envelope));
    writeEmitOnlyFormats(base);
  }
  const clean = runJson(["report", "--validate", base]);
  assert.equal(clean.exitCode, 0, JSON.stringify(clean.envelope));
  const { privateKey } = generateKeyPairSync("ed25519");
  keyPath = join(work, "key.pem");
  writeFileSync(keyPath, privateKey.export({ type: "pkcs8", format: "pem" }));
});

after(() => {
  rmSync(work, { recursive: true, force: true });
});

function applyEdit(data: Buffer, edit: Edit): Buffer {
  const hex = Buffer.from(edit.hex, "hex");
  if (edit.op === "write") {
    return hex;
  }
  if (edit.op === "prepend") {
    return Buffer.concat([hex, data]);
  }
  if (edit.op === "append") {
    return Buffer.concat([data, hex]);
  }
  const find = Buffer.from(edit.find_hex as string, "hex");
  const parts: Buffer[] = [];
  let from = 0;
  for (;;) {
    const at = data.indexOf(find, from);
    if (at < 0) {
      assert.ok(from > 0, `find_hex ${edit.find_hex} is not in the file`);
      break;
    }
    parts.push(data.subarray(from, at), hex);
    from = at + find.length;
    if (edit.op === "replace") {
      break;
    }
  }
  parts.push(data.subarray(from));
  return Buffer.concat(parts);
}

/** Every file under `dir` with its size and modification time, to see that a refusal wrote nothing. */
function snapshot(dir: string): string[] {
  return readdirSync(dir, { recursive: true, encoding: "utf-8" })
    .sort()
    .map((name) => {
      const info = statSync(join(dir, name));
      return `${name} ${info.size} ${info.mtimeMs}`;
    });
}

const readinessFiles = (dir: string): string[] =>
  readdirSync(dir).filter((name) => name.startsWith("report-readiness-"));

for (const c of CASES) {
  test(`reader case ${c.id} (${c.rule})`, () => {
    const dir = join(work, c.id);
    cpSync(base, dir, { recursive: true });
    const path = join(dir, c.file);
    let data: Buffer = readFileSync(path);
    for (const edit of c.edits) {
      data = applyEdit(data, edit);
    }
    writeFileSync(path, data);
    const before = snapshot(dir);
    const argv =
      c.command === "report-validate"
        ? ["report", "--validate", dir]
        : c.command === "readiness"
          ? ["readiness", dir]
          : c.command === "sign"
            ? ["sign", dir, "--as", "claimant", "--profile", "kms", "--key", keyPath]
            : ["diff", join(base, c.file), path];
    const { exitCode, envelope } = runJson(argv);
    const shown = JSON.stringify(envelope);
    assert.equal(exitCode, c.exit, shown);
    if (c.exit === 0) {
      return;
    }
    const error = envelope.error as Record<string, unknown> | undefined;
    if (c.command === "report-validate") {
      assert.equal(envelope.message_key, c.key, shown);
      const problems = envelope.problems as string[];
      assert.ok(
        problems.some((p) => p.includes(c.names as string)),
        shown,
      );
    } else {
      assert.equal(error?.message_key, c.key, shown);
      assert.ok(String(error?.detail).includes(c.names as string), shown);
    }
    if (c.command === "readiness") {
      assert.deepEqual(readinessFiles(dir), []);
    }
    if (c.command === "sign") {
      assert.deepEqual(snapshot(dir), before);
    }
  });
}
