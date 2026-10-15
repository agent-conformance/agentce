/**
 * Load a control catalog (SPEC §7.1, §7.3-7.4).
 *
 * A catalog is a directory with `catalog.yaml` (metadata and the control list), `controls/*.yaml` (one
 * control each), and `shapes/*.ttl` (the Portable Shape Profile shapes the rung-2 controls reference).
 * This is a faithful port of the reference loader; catalog linting (which re-runs every fixture through
 * the evaluator) is not needed by the conformance pipeline and is not ported here.
 */

import { readFileSync, readdirSync, realpathSync, statSync } from "node:fs";
import { join, sep } from "node:path";
import { load } from "js-yaml";
import { errorCause, errorFix } from "./errorCatalogue";
import { AgentceError, InputError, catalogUnreadable } from "./errors";
import { type Shape, parseShapesTtl } from "./psp";
import { SOURCE_CLASS_FROM, SOURCE_CLASS_RANK } from "./sourceClass";
import { byteCompare, formatTemplate } from "./util";
import { type CatalogFiles, readCatalogFiles } from "./verify";

const EVIDENCE_SHAPE_UNRESOLVED = "catalog.evidence_shape.unresolved";
const SHAPE_UNRESOLVED = "catalog.shape.unresolved";
const MIN_SOURCE_CLASS_UNKNOWN = "catalog.control.min_source_class_unknown";
const SOURCE_CLASS_FROM_UNKNOWN = "catalog.control.source_class_from_unknown";

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
  evidenceShapePath: string | null;
  tolerance: Record<string, unknown>;
  testCases: Array<Record<string, string>>;
  raw: Record<string, unknown>;
  /** `evaluation.source_class_from`: the graph links to the records the judged class also rests on. */
  sourceClassFrom: string[];
}

export interface Catalog {
  id: string;
  version: string;
  directory: string;
  controls: ControlSpec[];
  shapes: Map<string, Shape>;
  /** Each declared `evaluation.evidence_shape` path -> the IRI of the one node shape it holds. */
  evidenceShapeIris: Map<string, string>;
  /** The bytes the catalog was loaded from (18.171): its signature check and provenance digest read
   * these, never the folder again. Absent only for a catalog built in memory. */
  files?: CatalogFiles;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The covered `controls/<name>.yaml` files, one level deep as the listing always was, sorted. */
function controlFiles(files: Iterable<string>): string[] {
  return [...files]
    .filter(
      (rel) => rel.startsWith("controls/") && rel.split("/").length === 2 && rel.endsWith(".yaml"),
    )
    .sort(byteCompare);
}

function refuseUncovered(key: string, control: ControlSpec, path: string): InputError {
  return new InputError(
    key,
    formatTemplate(errorCause(key), { control: control.id, path }),
    errorFix(key),
  );
}

/** `value` as Python's `json.dumps(value, ensure_ascii=False)` writes a decoded YAML scalar or
 * collection: `", "` and `": "` separators, keys in their own order. */
function pyJsonDumps(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(pyJsonDumps).join(", ")}]`;
  }
  if (isRecord(value)) {
    const members = Object.entries(value).map(
      ([k, v]) => `${JSON.stringify(k)}: ${pyJsonDumps(v)}`,
    );
    return `{${members.join(", ")}}`;
  }
  return value === undefined ? "null" : JSON.stringify(value);
}

/** The control's `evaluation.min_source_class`, exactly one of the SPEC §6.4 classes. Anything else
 * (missing, null, empty, not a string, `any`, another case or another word) is refused when the
 * catalog loads, never read as no minimum. */
function minSourceClass(controlId: string, evaluation: unknown): string {
  const present = isRecord(evaluation) && "min_source_class" in evaluation;
  const value = present ? evaluation.min_source_class : null;
  if (typeof value === "string" && SOURCE_CLASS_RANK.has(value)) {
    return value;
  }
  const shown = present ? pyJsonDumps(value) : "(missing)";
  throw new InputError(
    MIN_SOURCE_CLASS_UNKNOWN,
    formatTemplate(errorCause(MIN_SOURCE_CLASS_UNKNOWN), { control: controlId, value: shown }),
    errorFix(MIN_SOURCE_CLASS_UNKNOWN),
  );
}

/** The named `source_class_from` links, distinct and byte-sorted. A value that is not a list of known link
 * names is refused when the catalog loads, never dropped: a dropped link would judge the control on the focus
 * record alone. */
function sourceClassFrom(controlId: string, evaluation: Record<string, unknown>): string[] {
  if (!("source_class_from" in evaluation)) {
    return [];
  }
  const value = evaluation.source_class_from;
  if (
    Array.isArray(value) &&
    value.every((name) => typeof name === "string" && SOURCE_CLASS_FROM.has(name))
  ) {
    return [...new Set(value as string[])].sort(byteCompare);
  }
  throw new InputError(
    SOURCE_CLASS_FROM_UNKNOWN,
    formatTemplate(errorCause(SOURCE_CLASS_FROM_UNKNOWN), {
      control: controlId,
      value: pyJsonDumps(value),
    }),
    errorFix(SOURCE_CLASS_FROM_UNKNOWN),
  );
}

function controlFromDict(data: Record<string, unknown>): ControlSpec {
  const evaluation = isRecord(data.evaluation) ? data.evaluation : {};
  const applicability = isRecord(data.applicability) ? data.applicability : {};
  const id = String(data.id ?? "");
  const minimum = minSourceClass(id, evaluation);
  return {
    id,
    version: String(data.version ?? ""),
    title: String(data.title ?? ""),
    appliesToRoles: Array.isArray(applicability.applies_to_roles)
      ? applicability.applies_to_roles.map(String)
      : [],
    mode: String(evaluation.mode ?? ""),
    rung: Number.parseInt(String(evaluation.rung ?? 0), 10),
    severity: String(data.severity ?? ""),
    minSourceClass: minimum,
    minimumEvidence: Array.isArray(evaluation.minimum_evidence)
      ? (evaluation.minimum_evidence as Array<Record<string, string>>)
      : [],
    // A non-string shape reads as "" (not a covered file), which load refuses; null stays "no shape".
    shapePath:
      evaluation.shape === undefined || evaluation.shape === null
        ? null
        : typeof evaluation.shape === "string"
          ? evaluation.shape
          : "",
    // A declared evidence shape is always resolved: a value that is not a path reads as "", which load
    // refuses, so it can never be skipped into a pass.
    evidenceShapePath:
      "evidence_shape" in evaluation
        ? typeof evaluation.evidence_shape === "string"
          ? evaluation.evidence_shape
          : ""
        : null,
    tolerance: isRecord(data.tolerance) ? data.tolerance : { kind: "count", max: 0 },
    testCases: Array.isArray(data.test_cases)
      ? (data.test_cases as Array<Record<string, string>>)
      : [],
    raw: data,
    sourceClassFrom: sourceClassFrom(id, evaluation),
  };
}

function hasTarget(shape: Shape): boolean {
  return (
    shape.targetClasses.length > 0 || shape.targetNodes.length > 0 || shape.targetWhere.length > 0
  );
}

/** The file inside `directory` that `path` names, after symlinks, or null when it is missing, not a
 * file, or outside the catalog folder (where the catalog's signature would not cover it). */
function fileInside(directory: string, path: string): string | null {
  if (path === "") {
    return null;
  }
  try {
    const root = realpathSync(directory);
    const resolved = realpathSync(join(directory, path));
    return resolved.startsWith(root + sep) && statSync(resolved).isFile() ? resolved : null;
  } catch {
    return null;
  }
}

/** The shapes in the file a control names as `evaluation.evidence_shape`. The file must hold exactly one
 * node shape with a target, whose IRI is returned with the file's shapes; anything else, including a file
 * outside the catalog folder, is refused, so a declared evidence shape can never be skipped and read as a
 * pass. */
function loadEvidenceShape(
  files: CatalogFiles,
  control: ControlSpec,
): [string, Map<string, Shape>] {
  const path = control.evidenceShapePath ?? "";
  const bytes = files.files.get(path);
  const inside = bytes !== undefined && fileInside(files.root, path) !== null;
  const loaded = inside ? parseShapesTtl(bytes.toString("utf-8"), path) : new Map<string, Shape>();
  const targeted = [...loaded].filter(([, shape]) => hasTarget(shape)).map(([iri]) => iri);
  if (targeted.length !== 1) {
    throw refuseUncovered(EVIDENCE_SHAPE_UNRESOLVED, control, path);
  }
  return [targeted[0] as string, loaded];
}

/** Load `catalog.yaml` and every control and shape in `directory`. Only files the catalog digest covers
 * are read, each by its exact name (`coveredFiles`, 18.168): a dot-named control file is never loaded,
 * and a shape path that names anything else is refused, so a signed catalog cannot change what it
 * loads without changing its digest. The load walks the folder as the digest does, so a folder it
 * cannot read is refused as input.catalog_unreadable, as verify and sign refuse it. */
export function loadCatalog(directory: string): Catalog {
  try {
    return loadCovered(directory);
  } catch (err) {
    if (
      err instanceof AgentceError ||
      typeof (err as { code?: unknown } | null)?.code !== "string"
    ) {
      throw err;
    }
    throw catalogUnreadable(directory, err);
  }
}

function loadCovered(directory: string): Catalog {
  // catalog.yaml first, as before: a folder without one is refused naming it. Every covered file is
  // then read once (18.171) and kept as `files`, which the signature check and provenance digest read.
  const metaBytes = readFileSync(join(directory, "catalog.yaml"));
  const meta = load(metaBytes.toString("utf-8"));
  const metaRecord = isRecord(meta) ? meta : {};
  const controls: ControlSpec[] = [];
  const shapes = new Map<string, Shape>();
  const evidenceShapeIris = new Map<string, string>();
  const files = readCatalogFiles(directory, new Map([["catalog.yaml", metaBytes]]));
  const text = (rel: string): string => (files.files.get(rel) as Buffer).toString("utf-8");
  for (const rel of controlFiles(files.files.keys())) {
    const data = load(text(rel));
    if (!isRecord(data)) {
      continue;
    }
    const control = controlFromDict(data);
    controls.push(control);
    if (control.shapePath !== null) {
      if (!files.files.has(control.shapePath)) {
        throw refuseUncovered(SHAPE_UNRESOLVED, control, control.shapePath);
      }
      for (const [iri, shape] of parseShapesTtl(text(control.shapePath), control.shapePath)) {
        shapes.set(iri, shape);
      }
    }
    if (control.evidenceShapePath !== null) {
      const [iri, loaded] = loadEvidenceShape(files, control);
      evidenceShapeIris.set(control.evidenceShapePath, iri);
      for (const [shapeIri, shape] of loaded) {
        shapes.set(shapeIri, shape);
      }
    }
  }
  return {
    id: String(metaRecord.id ?? ""),
    version: String(metaRecord.version ?? ""),
    directory,
    controls,
    shapes,
    evidenceShapeIris,
    files,
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
    if (hasTarget(shape)) {
      return shape;
    }
  }
  return null;
}

/** The shape a focus must meet for the control to judge it (`evaluation.evidence_shape`); a focus that
 * violates it lacks the evidence, so the outcome is insufficient_evidence. */
export function evidenceShapeFor(catalog: Catalog, control: ControlSpec): Shape | null {
  if (control.evidenceShapePath === null) {
    return null;
  }
  return catalog.shapes.get(catalog.evidenceShapeIris.get(control.evidenceShapePath) ?? "") ?? null;
}
