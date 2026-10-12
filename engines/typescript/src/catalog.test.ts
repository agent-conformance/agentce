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
import { createHash } from "node:crypto";
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
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
import { coveredFiles, digestTree } from "./report";
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

// 18.168: the loaders read only files the catalog digest covers, by exact name ("covered path" tests).

function withControlValue(
  control: string,
  field: string,
  value: string,
  place: string | null,
): { dir: string; tmp: string } {
  let tmpDir = "";
  const dir = withCatalogCopy((d, tmp) => {
    tmpDir = tmp;
    const doc = join(d, "controls", `${control}.yaml`);
    const text = readFileSync(doc, "utf-8");
    const line = text.split("\n").find((l) => l.trim().startsWith(`${field}:`)) ?? "";
    const original = line.split(":", 2)[1]?.trim() ?? "";
    writeFileSync(doc, text.replace(`${field}: ${original}`, `${field}: ${value}`));
    const copyTo = place === null ? null : place.startsWith("/") ? place : join(d, place);
    if (copyTo !== null) {
      mkdirSync(join(copyTo, ".."), { recursive: true });
      copyFileSync(join(d, original), copyTo);
    }
  });
  return { dir, tmp: tmpDir };
}

function refusal(dir: string): { key: string; cause: string } {
  try {
    loadCatalog(dir);
    return { key: "loaded", cause: "" };
  } catch (error) {
    const e = error as { key?: string; cause?: unknown; message?: string };
    return { key: e.key ?? String(error), cause: String(e.cause ?? e.message ?? "") };
  } finally {
    rmSync(join(dir, ".."), { recursive: true, force: true });
  }
}

test("covered path: coveredFiles is what digestTree hashes", () => {
  const tmp = mkdtempSync(join(tmpdir(), "agentce-covered-"));
  const root = join(tmp, "tree");
  for (const d of ["a/.hidden", "a/__pycache__"]) {
    mkdirSync(join(root, d), { recursive: true });
  }
  mkdirSync(join(tmp, "real"));
  writeFileSync(join(root, "a", "x.ttl"), "x");
  writeFileSync(join(root, "a", ".x.ttl"), "x");
  writeFileSync(join(root, "a", ".hidden", "y.ttl"), "y");
  writeFileSync(join(root, "a", "__pycache__", "z.ttl"), "z");
  writeFileSync(join(tmp, "real", "w.ttl"), "w");
  symlinkSync(join(tmp, "real"), join(root, "linked"), "dir");
  symlinkSync(join(tmp, "real", "w.ttl"), join(root, "a", "v.ttl"));
  writeFileSync(join(root, "catalog.sig.json"), "{}");
  const exclude = new Set(["catalog.sig.json"]);
  const covered = coveredFiles(root, exclude);
  assert.deepEqual(covered, ["a/v.ttl", "a/x.ttl", "linked/w.ttl"]);
  const lines = covered.map(
    (rel) =>
      `${rel}\0${createHash("sha256")
        .update(readFileSync(join(root, rel)))
        .digest("hex")}`,
  );
  assert.equal(
    digestTree(root, exclude),
    `sha256:${createHash("sha256").update(lines.join("\n")).digest("hex")}`,
  );
  rmSync(tmp, { recursive: true, force: true });
});

test("covered path: a dot-named or AppleDouble control file is never read", () => {
  for (const name of [".DOC-99.yaml", "._DOC-01.yaml"]) {
    const plain = withCatalogCopy(() => undefined);
    const before = loadCatalog(plain).controls.map((c) => c.id);
    rmSync(join(plain, ".."), { recursive: true, force: true });
    // Not a control at all: a loader that read it would fail, not merely add a control.
    const dir = withCatalogCopy((d) =>
      writeFileSync(join(d, "controls", name), "{not: [a control\n"),
    );
    assert.deepEqual(
      loadCatalog(dir).controls.map((c) => c.id),
      before,
      name,
    );
    rmSync(join(dir, ".."), { recursive: true, force: true });
  }
});

test("covered path: a shape or evidence shape outside the digest is refused, naming control and path", () => {
  const cases: Array<[string, string | null]> = [
    ["shapes/.X.ttl", "shapes/.X.ttl"],
    ["shapes/._X.ttl", "shapes/._X.ttl"],
    [".hidden/X.ttl", ".hidden/X.ttl"],
    ["shapes/__pycache__/X.ttl", "shapes/__pycache__/X.ttl"],
    ["shapes/__PYCACHE__/X.ttl", "shapes/__pycache__/X.ttl"],
    ["../outside.ttl", "../outside.ttl"],
    ["shapes/../shapes/X.ttl", "shapes/X.ttl"],
    ["./shapes/X.ttl", "shapes/X.ttl"],
    ["shapes//X.ttl", "shapes/X.ttl"],
    ["ABS", "ABS"],
    ["shapes/MISSING.ttl", null],
    ['""', null],
    ["5", null],
  ];
  for (const [field, control, key] of [
    ["shape", "DOC-01", "catalog.shape.unresolved"],
    ["evidence_shape", "ROB-02", "catalog.evidence_shape.unresolved"],
  ] as const) {
    for (const [declared, place] of cases) {
      const abs = join(mkdtempSync(join(tmpdir(), "agentce-abs-")), "X.ttl");
      const value = declared === "ABS" ? abs : declared;
      const { dir } = withControlValue(control, field, value, place === "ABS" ? abs : place);
      const got = refusal(dir);
      assert.equal(got.key, key, `${field} ${value}`);
      if (value !== '""' && value !== "5") {
        assert.ok(got.cause.includes(control) && got.cause.includes(value), got.cause);
      }
    }
  }
});

test("covered path: a symlinked shape inside the catalog loads; a null shape is no shape", () => {
  const { dir } = withControlValue("DOC-01", "shape", "shapes/linked.ttl", null);
  symlinkSync(join(dir, "shapes", "DOC-01.ttl"), join(dir, "shapes", "linked.ttl"));
  const catalog = loadCatalog(dir);
  const doc = catalog.controls.find((c) => c.id === "DOC-01");
  assert.ok(doc && shapeFor(catalog, doc) !== null);
  rmSync(join(dir, ".."), { recursive: true, force: true });
  const nulled = withControlValue("DOC-01", "shape", "null", null).dir;
  assert.equal(loadCatalog(nulled).controls.find((c) => c.id === "DOC-01")?.shapePath, null);
  rmSync(join(nulled, ".."), { recursive: true, force: true });
});
