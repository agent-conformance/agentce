/**
 * Load a control catalog (SPEC §7.1, §7.3-7.4).
 *
 * A catalog is a directory with `catalog.yaml` (metadata and the control list), `controls/*.yaml` (one
 * control each), and `shapes/*.ttl` (the Portable Shape Profile shapes the rung-2 controls reference).
 * This is a faithful port of the reference loader; catalog linting (which re-runs every fixture through
 * the evaluator) is not needed by the conformance pipeline and is not ported here.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { load } from "js-yaml";
import { type Shape, loadShapes } from "./psp";
import { byteCompare } from "./util";

export interface ControlSpec {
  id: string;
  version: string;
  title: string;
  appliesToRoles: string[];
  mode: string;
  rung: number;
  severity: string;
  minSourceClass: string;
  minimumEvidence: Array<Record<string, string>>;
  shapePath: string | null;
  tolerance: Record<string, unknown>;
  testCases: Array<Record<string, string>>;
  raw: Record<string, unknown>;
}

export interface Catalog {
  id: string;
  version: string;
  directory: string;
  controls: ControlSpec[];
  shapes: Map<string, Shape>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function yamlFiles(directory: string): string[] {
  try {
    return readdirSync(directory)
      .filter((name) => name.endsWith(".yaml"))
      .sort(byteCompare);
  } catch {
    return [];
  }
}

function controlFromDict(data: Record<string, unknown>): ControlSpec {
  const evaluation = isRecord(data.evaluation) ? data.evaluation : {};
  const applicability = isRecord(data.applicability) ? data.applicability : {};
  return {
    id: String(data.id ?? ""),
    version: String(data.version ?? ""),
    title: String(data.title ?? ""),
    appliesToRoles: Array.isArray(applicability.applies_to_roles)
      ? applicability.applies_to_roles.map(String)
      : [],
    mode: String(evaluation.mode ?? ""),
    rung: Number.parseInt(String(evaluation.rung ?? 0), 10),
    severity: String(data.severity ?? ""),
    minSourceClass: String(evaluation.min_source_class ?? "any"),
    minimumEvidence: Array.isArray(evaluation.minimum_evidence)
      ? (evaluation.minimum_evidence as Array<Record<string, string>>)
      : [],
    shapePath: typeof evaluation.shape === "string" ? evaluation.shape : null,
    tolerance: isRecord(data.tolerance) ? data.tolerance : { kind: "count", max: 0 },
    testCases: Array.isArray(data.test_cases)
      ? (data.test_cases as Array<Record<string, string>>)
      : [],
    raw: data,
  };
}

/** Load `catalog.yaml` and every control and shape in `directory`. */
export function loadCatalog(directory: string): Catalog {
  const meta = load(readFileSync(join(directory, "catalog.yaml"), "utf-8"));
  const metaRecord = isRecord(meta) ? meta : {};
  const controls: ControlSpec[] = [];
  const shapes = new Map<string, Shape>();
  for (const controlFile of yamlFiles(join(directory, "controls"))) {
    const data = load(readFileSync(join(directory, "controls", controlFile), "utf-8"));
    if (!isRecord(data)) {
      continue;
    }
    const control = controlFromDict(data);
    controls.push(control);
    if (control.shapePath) {
      for (const [iri, shape] of loadShapes(join(directory, control.shapePath))) {
        shapes.set(iri, shape);
      }
    }
  }
  return {
    id: String(metaRecord.id ?? ""),
    version: String(metaRecord.version ?? ""),
    directory,
    controls,
    shapes,
  };
}

/** The PSP shape a control evaluates against (by `<id>-Shape` suffix, then any shape with targets). */
export function shapeFor(catalog: Catalog, control: ControlSpec): Shape | null {
  if (control.shapePath === null) {
    return null;
  }
  const want = `${control.id}-Shape`;
  for (const [iri, shape] of catalog.shapes) {
    if (iri.endsWith(want)) {
      return shape;
    }
  }
  for (const shape of catalog.shapes.values()) {
    if (shape.targetClass || shape.targetNodes.length > 0 || shape.targetWhere.length > 0) {
      return shape;
    }
  }
  return null;
}
