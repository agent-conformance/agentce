/**
 * The domain binding's YAML node forms read as PyYAML's SafeLoader reads them (SPEC §7.4; 18.184k).
 *
 * `testdata/yaml-node-forms-golden.json` is written by `generate_goldens.py` from the Python reference:
 * each case's YAML (tags x scalar styles x values, key kinds, merges, nesting inside task_scopes and
 * budgets) and Python's reading of it through `load_untrusted_yaml(..., unique_keys=True)`, by type, or
 * the key it is refused under. {@link loadPyyaml} must give the same reading for every case.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { InputError } from "./errors";
import { isPyyamlTimestamp, loadPyyaml, pythonizeTimestamp } from "./readiness";
import { byteCompare } from "./util";

const TESTDATA = join(__dirname, "..", "testdata");

interface NodeFormCase {
  readonly name: string;
  readonly yaml: string;
  readonly reads: unknown;
}

/** A reading encoded as the golden encodes Python's: numbers by value, keys in code point order. */
function reading(value: unknown): unknown {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "boolean") {
    return { b: value };
  }
  if (typeof value === "number") {
    return Number.isFinite(value)
      ? { n: value }
      : { n: String(value).replace("Infinity", "inf").replace("NaN", "nan") };
  }
  if (typeof value === "string") {
    return { s: value };
  }
  if (isPyyamlTimestamp(value)) {
    return { ts: pythonizeTimestamp(value.pyyamlTimestamp) };
  }
  if (Array.isArray(value)) {
    return { seq: value.map(reading) };
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).sort(byteCompare);
    return { map: keys.map((key) => [key, reading((value as Record<string, unknown>)[key])]) };
  }
  return { other: String(value) };
}

test("every YAML node form reads as Python reads it", () => {
  const cases = JSON.parse(
    readFileSync(join(TESTDATA, "yaml-node-forms-golden.json"), "utf-8"),
  ) as NodeFormCase[];
  const dir = mkdtempSync(join(tmpdir(), "agentce-node-forms-"));
  const path = join(dir, "domain.yaml");
  const differ: string[] = [];
  try {
    for (const { name, yaml, reads } of cases) {
      writeFileSync(path, yaml, "utf-8");
      let got: unknown;
      try {
        got = reading(loadPyyaml(path, "input.domain_binding_invalid", "the domain binding"));
      } catch (error) {
        got = error instanceof InputError ? { refused: error.key } : { crash: String(error) };
      }
      if (JSON.stringify(got) !== JSON.stringify(reads)) {
        differ.push(
          `${name}: ${JSON.stringify(yaml)} Python ${JSON.stringify(reads)}, TypeScript ${JSON.stringify(got)}`,
        );
      }
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  assert.ok(cases.length > 1000, `the table holds ${cases.length} cases`);
  assert.deepEqual(
    differ,
    [],
    `${differ.length} of ${cases.length} node forms differ:\n${differ.join("\n")}`,
  );
});
