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
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { canonicalString } from "./canonical";
import { loadCatalog, shapeFor } from "./catalog";
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
