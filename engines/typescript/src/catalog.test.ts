/**
 * The catalog loader, PSP parser, and structural evaluator are byte-identical to the Python reference.
 *
 * This exercises the whole rung-2 path over the real base catalog (`spec/catalogs/base/eu-ai-act`): it
 * parses each control's Turtle shape with N3, builds the graph from the control's own passed / failed /
 * inapplicable fixtures, and evaluates the control. `testdata/catalog-golden.json` is the reference
 * engine's `evaluate_control` output for the same inputs; the two must agree to the byte.
 *
 * The second test below does the same over the Conduct overlay (`spec/catalogs/overlays/conduct`,
 * SPEC §7.7): it is a real, user-reachable regression guard that `withinScope`/`actsOnUntrusted`
 * (CND-01/CND-05) are materialised the same way Python does -- the whole overlay was Python-only
 * until item 18.37 found and ported it, and this is the test that would have caught it (`graph.test.ts`
 * alone did not, since its generic fixture never exercised a shape depending on these literals).
 */

import assert from "node:assert/strict";
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { canonicalString } from "./canonical";
import { evidenceShapeFor, loadCatalog, shapeFor } from "./catalog";
import { DomainBinding } from "./domain";
import { buildGraph } from "./graph";
import { controlOutcomeToJson, evaluateControl } from "./structural";

const REPO = join(__dirname, "..", "..", "..");
const BASE = join(REPO, "spec", "catalogs", "base", "eu-ai-act");
const CONDUCT = join(REPO, "spec", "catalogs", "overlays", "conduct");
const TESTDATA = join(__dirname, "..", "testdata");
const CASES = ["passed", "failed", "inapplicable"];

function readEvents(path: string): Array<Record<string, unknown>> {
  return readFileSync(path, "utf-8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

function evaluateCatalog(directory: string): Record<string, Record<string, unknown>> {
  const catalog = loadCatalog(directory);
  const domain = DomainBinding.load(join(directory, "test", "domain.yaml"));

  const result: Record<string, Record<string, unknown>> = {};
  for (const control of catalog.controls) {
    const shape = shapeFor(catalog, control);
    if (shape === null) {
      continue;
    }
    const perCase: Record<string, unknown> = {};
    for (const c of CASES) {
      const fixture = join(directory, "test", control.id, `${c}.jsonl`);
      if (!existsSync(fixture)) {
        continue;
      }
      const store = buildGraph(readEvents(fixture), { domain });
      const outcome = evaluateControl(store, shape, catalog.shapes, control.id, control.tolerance);
      perCase[c] = controlOutcomeToJson(outcome);
    }
    result[control.id] = perCase;
  }
  return result;
}

test("structural evaluation over the base catalog matches the Python reference", () => {
  const golden = JSON.parse(readFileSync(join(TESTDATA, "catalog-golden.json"), "utf-8"));
  assert.equal(canonicalString(evaluateCatalog(BASE)), canonicalString(golden));
});

test("structural evaluation over the Conduct overlay matches the Python reference", () => {
  const golden = JSON.parse(readFileSync(join(TESTDATA, "conduct-golden.json"), "utf-8"));
  assert.equal(canonicalString(evaluateCatalog(CONDUCT)), canonicalString(golden));
});

// 18.37k: ROB-02's evidence shape resolves from the file the control declares; a declared evidence
// shape that is not exactly one targeted node shape inside the catalog is refused.
const EVIDENCE_PREFIXES =
  "@prefix sh: <http://www.w3.org/ns/shacl#> .\n" +
  "@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .\n";
const EVIDENCE_BODY =
  " a sh:NodeShape ; sh:targetClass agentce:ConsequentialDecision ;" +
  " sh:property [ sh:path agentce:untrustedContentRuledOn ; sh:hasValue true ] .\n";

function withCatalogCopy(edit: (dir: string, tmp: string) => void): string {
  const tmp = mkdtempSync(join(tmpdir(), "agentce-evidence-shape-"));
  const dir = join(tmp, "cat");
  cpSync(BASE, dir, { recursive: true });
  edit(dir, tmp);
  return dir;
}

function refusalKey(dir: string): string {
  try {
    loadCatalog(dir);
    return "loaded";
  } catch (error) {
    return (error as { key?: string }).key ?? String(error);
  } finally {
    rmSync(join(dir, ".."), { recursive: true, force: true });
  }
}

test("an evidence shape resolves from its own file whatever its name", () => {
  const dir = withCatalogCopy((d) =>
    writeFileSync(
      join(d, "shapes", "ROB-02-evidence.ttl"),
      `${EVIDENCE_PREFIXES}agentce:Anything${EVIDENCE_BODY}`,
    ),
  );
  const catalog = loadCatalog(dir);
  const control = catalog.controls.find((c) => c.id === "ROB-02");
  assert.ok(control);
  assert.deepEqual(evidenceShapeFor(catalog, control)?.targetClasses, [
    "agentce:ConsequentialDecision",
  ]);
  rmSync(join(dir, ".."), { recursive: true, force: true });
});

test("an unresolvable evidence shape is refused", () => {
  const texts: Array<string | null> = [
    null,
    `${EVIDENCE_PREFIXES}agentce:E a sh:NodeShape ; sh:property [ sh:path agentce:x ; sh:minCount 1 ] .\n`,
    `${EVIDENCE_PREFIXES}agentce:E1${EVIDENCE_BODY}agentce:E2${EVIDENCE_BODY}`,
  ];
  for (const text of texts) {
    const dir = withCatalogCopy((d) => {
      const shape = join(d, "shapes", "ROB-02-evidence.ttl");
      if (text === null) {
        rmSync(shape);
      } else {
        writeFileSync(shape, text);
      }
    });
    assert.equal(refusalKey(dir), "catalog.evidence_shape.unresolved", String(text));
  }
});

test("a declared evidence shape that is not a file in the catalog is refused", () => {
  const declared = "evidence_shape: shapes/ROB-02-evidence.ttl";
  for (const value of ['""', "null", "5", "../outside.ttl", "linked.ttl"]) {
    const dir = withCatalogCopy((d, tmp) => {
      const control = join(d, "controls", "ROB-02.yaml");
      const text = readFileSync(control, "utf-8");
      assert.ok(text.includes(declared));
      writeFileSync(control, text.replace(declared, `evidence_shape: ${value}`));
      const outside = join(tmp, "outside.ttl");
      copyFileSync(join(d, "shapes", "ROB-02-evidence.ttl"), outside);
      symlinkSync(outside, join(d, "linked.ttl"));
    });
    assert.equal(refusalKey(dir), "catalog.evidence_shape.unresolved", value);
  }
});
