/**
 * `report --validate`: schema-validate every artifact in a report directory (SPEC §9), at parity
 * with the Python reference (`agentce.report.validate_report`). Every emitted artifact is checked
 * against its vendored JSON Schema (`schema/*.schema.json`, mirrored from `spec/report/`); `oscal-
 * ar.json` and `results.sarif` are checked twice — first against AgentCE's own bounded local
 * profile, then, only on success, against the real third-party standard vendored under the same
 * directory (NIST OSCAL 1.1.2, OASIS SARIF 2.1.0) — so a document that satisfies AgentCE's narrower
 * profile but not the real standard is still caught (`harness/remediation/evidence/P18-18.27/
 * python-reference.md`, cases 6 and 8).
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import type { CodeKeywordDefinition, KeywordCxt } from "ajv";
import Ajv2020 from "ajv/dist/2020";
import anyOfDef from "ajv/dist/vocabularies/applicator/anyOf";
import oneOfDef from "ajv/dist/vocabularies/applicator/oneOf";
import { XMLParser, XMLValidator } from "fast-xml-parser";
import { MAX_INT_STR_DIGITS, parseJson } from "./json";
import { byteCompare } from "./util";

/** Always written, regardless of `--emit`: the run's structural core. */
const MANDATORY_ARTIFACT_SCHEMAS: Record<string, string> = {
  "assertions.json": "assertions",
  "manifest.json": "manifest",
  "activity.json": "activity",
  "blind-spots.json": "blind-spots",
};

/** Written only when `--emit` (or the engine's own fixed bundle) produced them; validated when
 * present, skipped when a narrower run legitimately left them unwritten. */
const OPTIONAL_ARTIFACT_SCHEMAS: Record<string, string> = {
  "oscal-ar.json": "oscal-assessment-results",
  "results.sarif": "results-sarif",
  "project.json": "project",
  "security.json": "security",
  "auditor.json": "auditor",
  "buyer.json": "buyer",
};

const ARTIFACT_SCHEMAS: Record<string, string> = {
  ...MANDATORY_ARTIFACT_SCHEMAS,
  ...OPTIONAL_ARTIFACT_SCHEMAS,
};

/** The newer optional artifacts (SPEC §9): validated only when a run actually produced them
 * (`--emit`, or for `runtime_drift.jsonl`, `--state`), unlike `ARTIFACT_SCHEMAS`'s entries. */
const OPTIONAL_ARTIFACTS = [
  "report.junit.xml",
  "oscal-ar.xml",
  "report.csv",
  "runtime_drift.jsonl",
];

const CSV_COLUMNS = ["control", "subject", "outcome", "control_version", "rung", "mode"];

function schemaDir(): string {
  return join(__dirname, "..", "schema");
}

/** A digit run long enough to hold an over-limit integer literal; text without one needs no token scan. */
const LONG_DIGIT_RUN = new RegExp(`[0-9]{${MAX_INT_STR_DIGITS + 1}}`);

/**
 * `JSON.parse`, refusing an integer literal over 4300 digits as Python's `json.loads` and Java's
 * `Json.parse` do (item 18.71). The native parse keeps its own syntax messages and gives the value;
 * `parseJson` then adds only the digit check, and only when such a digit run exists.
 */
function parseJsonText(text: string): unknown {
  const value = JSON.parse(text);
  if (LONG_DIGIT_RUN.test(text)) {
    parseJson(text); // throws only for an integer literal past MAX_INT_STR_DIGITS once the native parse passed
  }
  return value;
}

function loadSchemaJson(name: string): Record<string, unknown> {
  const text = readFileSync(join(schemaDir(), `${name}.schema.json`), "utf-8");
  return JSON.parse(text) as Record<string, unknown>;
}

/** An `anyOf`/`oneOf` keyword that, when it fails, drops every error its branches recorded and
 * reports itself once -- the `iter_errors` rule the Python reference follows: one problem per failing
 * combinator at its own instance location, never one per failing branch. Ajv's own definition (with
 * `allErrors: true`) keeps every branch error alongside the combinator's, and its schema paths name
 * the `$ref` target rather than the branch, so filtering afterwards cannot find a branch error that
 * came through a `$ref` (P18-18.27 verifier round 2, D1). Resetting the error count the keyword
 * started with, just before it reports, needs no path matching at all. */
function collapsingCombinator(def: CodeKeywordDefinition): CodeKeywordDefinition {
  return {
    ...def,
    code(cxt: KeywordCxt, ruleType?: string) {
      const report = cxt.error.bind(cxt);
      cxt.error = (...args: Parameters<KeywordCxt["error"]>) => {
        cxt.reset();
        report(...args);
      };
      def.code(cxt, ruleType);
    },
  };
}

// `strict: false`/`logger: false` mirror schema.ts. `validateFormats: false`: `format` is an
// annotation in all three engines, never an assertion (Python's validators run without a
// FormatChecker; Java turns format assertions off).
function newAjv(options: { validateSchema?: boolean } = {}): InstanceType<typeof Ajv2020> {
  const ajv = new Ajv2020({
    allErrors: true,
    strict: false,
    logger: false,
    validateFormats: false,
    ...options,
  });
  for (const def of [anyOfDef, oneOfDef]) {
    ajv.removeKeyword(def.keyword as string);
    ajv.addKeyword(collapsingCombinator(def));
  }
  return ajv;
}

let localAjv: InstanceType<typeof Ajv2020> | null = null;
const localValidators = new Map<string, ReturnType<InstanceType<typeof Ajv2020>["compile"]>>();

function localValidatorFor(schemaName: string) {
  let validate = localValidators.get(schemaName);
  if (validate === undefined) {
    if (localAjv === null) {
      localAjv = newAjv();
    }
    validate = localAjv.compile(loadSchemaJson(schemaName));
    localValidators.set(schemaName, validate);
  }
  return validate;
}

/** One step of a problem's location: an object key, or an array index. */
type Segment = string | number;

/** Ajv's `instancePath` (a JSON pointer) as typed segments: each token decoded (`~1` to `/`, then `~0`
 * to `~`, RFC 6901), and a token that indexes an array -- found by walking `instance` along the path,
 * never guessed from the token's digits -- as a number, like Python's `absolute_path`. */
function pathSegments(instance: unknown, instancePath: string): Segment[] {
  if (instancePath === "") {
    return [];
  }
  const segments: Segment[] = [];
  let node = instance;
  for (const token of instancePath.slice(1).split("/")) {
    const key = token.replace(/~1/g, "/").replace(/~0/g, "~");
    if (Array.isArray(node)) {
      segments.push(Number(key));
      node = node[Number(key)];
    } else {
      segments.push(key);
      node = isRecord(node) ? node[key] : undefined;
    }
  }
  return segments;
}

/** Python's list ordering over `absolute_path`: array indices compare as numbers, object keys as
 * strings by code point, and a path sorts before any longer path it is a prefix of. */
function compareSegments(a: Segment[], b: Segment[]): number {
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const segA = a[i] as Segment;
    const segB = b[i] as Segment;
    const diff =
      typeof segA === "number" && typeof segB === "number"
        ? segA - segB
        : byteCompare(String(segA), String(segB));
    if (diff !== 0) {
      return diff;
    }
  }
  return a.length - b.length;
}

interface AjvError {
  instancePath: string;
  schemaPath: string;
  keyword: string;
  params: Record<string, unknown>;
  message?: string;
}

interface SchemaProblem {
  segments: Segment[];
  keyword: string;
  message: string;
  /** For an `additionalProperties` problem: every unexpected key on that object. */
  extraKeys?: string[];
}

/** Every schema error as `"<prefix><location>: <message>"`, following the Python reference's model
 * (P18-18.27; `python-reference.md`'s Design notes): one problem per failing combinator (done at
 * compile time, `collapsingCombinator`); one `additionalProperties` problem per object naming its
 * unexpected keys in code-point order, where Ajv reports one per key; sorted by location, then
 * keyword, keeping validator order for ties. */
function ajvProblems(prefix: string, instance: unknown, errors: AjvError[] | null | undefined) {
  const problems: SchemaProblem[] = [];
  const byObject = new Map<string, SchemaProblem>();
  for (const e of errors ?? []) {
    if (e.keyword === "additionalProperties") {
      const group = `${e.instancePath}\u0000${e.schemaPath}`;
      const existing = byObject.get(group);
      if (existing !== undefined) {
        existing.extraKeys?.push(String(e.params.additionalProperty));
        continue;
      }
      const problem: SchemaProblem = {
        segments: pathSegments(instance, e.instancePath),
        keyword: e.keyword,
        message: "",
        extraKeys: [String(e.params.additionalProperty)],
      };
      byObject.set(group, problem);
      problems.push(problem);
      continue;
    }
    problems.push({
      segments: pathSegments(instance, e.instancePath),
      keyword: e.keyword,
      message: e.message ?? "invalid",
    });
  }
  for (const problem of byObject.values()) {
    const named = (problem.extraKeys ?? []).sort(byteCompare).map((k) => `'${k}'`);
    problem.message = `must NOT have additional properties (${named.join(", ")})`;
  }
  problems.sort(
    (a, b) => compareSegments(a.segments, b.segments) || byteCompare(a.keyword, b.keyword),
  );
  return problems.map((p) => {
    const location = p.segments.length === 0 ? "<root>" : p.segments.join("/");
    return `${prefix}${location}: ${p.message}`;
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function recordedOutputs(outDir: string): Record<string, string> {
  const manifestPath = join(outDir, "manifest.json");
  if (!existsSync(manifestPath)) {
    return {};
  }
  try {
    const manifest = parseJsonText(readFileSync(manifestPath, "utf-8"));
    const outputs = isRecord(manifest) ? manifest.outputs : undefined;
    return isRecord(outputs) ? (outputs as Record<string, string>) : {};
  } catch {
    return {};
  }
}

// The five entities XML itself predefines (SPEC/no DTD support, matching Java's `SUPPORT_DTD=false`
// and Python's expat-backed `ElementTree`, neither of which resolves a custom DTD-declared entity for
// these report artifacts): any other named reference is undefined.
const PREDEFINED_XML_ENTITIES = new Set(["amp", "lt", "gt", "apos", "quot"]);

/** `fast-xml-parser`'s own `XMLValidator` checks an entity reference's *syntax* (`&word;`) but never
 * whether `word` names a real entity (confirmed empirically: it accepts `&undefined;` outright, unlike
 * Python's `ElementTree`/Java's `XMLStreamReader`, both of which refuse it as "undefined entity"). Walk
 * the raw text the same way `XMLValidator` does -- skipping comments, CDATA, and the DOCTYPE's own
 * internal subset, where a literal `&` is just text -- and refuse any named (non-numeric) reference
 * outside that set. */
function validateXmlEntities(text: string, filename: string): string[] {
  let i = 0;
  while (i < text.length) {
    if (text.startsWith("<!--", i)) {
      const end = text.indexOf("-->", i + 4);
      i = end === -1 ? text.length : end + 3;
      continue;
    }
    if (text.startsWith("<![CDATA[", i)) {
      const end = text.indexOf("]]>", i + 9);
      i = end === -1 ? text.length : end + 3;
      continue;
    }
    if (text.startsWith("<!DOCTYPE", i)) {
      let depth = 1;
      let j = i + "<!DOCTYPE".length;
      while (j < text.length && depth > 0) {
        if (text[j] === "<") depth++;
        else if (text[j] === ">") depth--;
        j++;
      }
      i = j;
      continue;
    }
    if (text[i] === "&") {
      const rest = text.slice(i);
      const numeric = /^&#(x[0-9a-fA-F]+|[0-9]+);/.exec(rest);
      if (numeric !== null) {
        i += numeric[0].length;
        continue;
      }
      const named = /^&([A-Za-z_][\w.-]*);/.exec(rest);
      if (named !== null) {
        if (!PREDEFINED_XML_ENTITIES.has(named[1] as string)) {
          return [`${filename}: invalid XML (undefined entity "${named[1]}")`];
        }
        i += named[0].length;
        continue;
      }
      i += 1; // a bare '&': XMLValidator.validate's own ampersand check already rejects this shape
      continue;
    }
    i += 1;
  }
  return [];
}

/** `XMLValidator.validate` tracks whether the root tag closed, but never refuses a *self-closing*
 * root followed by a sibling element (confirmed empirically: `<a/><b/>` passes it outright) --
 * `reachedRoot` is only ever set from the paired open/close branch. Parse the document instead and
 * check that exactly one top-level element exists: more than one key (ignoring the `?xml` declaration
 * and comments), or a single key whose value is an array (two siblings sharing one tag name), means
 * more than one root. */
function validateXmlSingleRoot(text: string, filename: string): string[] {
  let parsed: unknown;
  try {
    parsed = new XMLParser({ preserveOrder: false, ignoreAttributes: true }).parse(text);
  } catch {
    return []; // a parse failure here is XMLValidator.validate's job to report, not this check's
  }
  if (!isRecord(parsed)) {
    return [];
  }
  const rootKeys = Object.keys(parsed).filter((k) => !k.startsWith("?") && !k.startsWith("#"));
  const multiple =
    rootKeys.length > 1 || (rootKeys.length === 1 && Array.isArray(parsed[rootKeys[0] as string]));
  return multiple ? [`${filename}: invalid XML (multiple root elements)`] : [];
}

function validateXmlWellformed(path: string, filename: string): string[] {
  const text = readFileSync(path, "utf-8");
  const result = XMLValidator.validate(text);
  if (result !== true) {
    return [`${filename}: invalid XML (${result.err.msg})`];
  }
  const entityProblems = validateXmlEntities(text, filename);
  if (entityProblems.length > 0) {
    return entityProblems;
  }
  return validateXmlSingleRoot(text, filename);
}

function validateJsonl(path: string, filename: string): string[] {
  const problems: string[] = [];
  const lines = readFileSync(path, "utf-8").split(/\r?\n/);
  lines.forEach((line, i) => {
    if (line.trim() === "") {
      return;
    }
    try {
      parseJsonText(line);
    } catch (exc) {
      problems.push(`${filename}: line ${i + 1} is not valid JSON (${(exc as Error).message})`);
    }
  });
  return problems;
}

/** A minimal RFC 4180 field splitter: exactly what `report.csv`'s own writer produces (no embedded
 * newlines are ever written into a field), enough to accept a genuine run's output and reject
 * garbage, matching Python's `csv.reader` behaviour for this bounded shape. */
function splitCsvLine(line: string): string[] {
  const fields: string[] = [];
  let i = 0;
  while (i <= line.length) {
    let field = "";
    if (line[i] === '"') {
      i++;
      while (i < line.length) {
        if (line[i] === '"' && line[i + 1] === '"') {
          field += '"';
          i += 2;
        } else if (line[i] === '"') {
          i++;
          break;
        } else {
          field += line[i];
          i++;
        }
      }
    } else {
      while (i < line.length && line[i] !== ",") {
        field += line[i];
        i++;
      }
    }
    fields.push(field);
    if (line[i] === ",") {
      i++;
    } else {
      break;
    }
  }
  return fields;
}

function validateCsv(path: string, filename: string): string[] {
  let text: string;
  try {
    text = readFileSync(path, "utf-8");
  } catch (exc) {
    return [`${filename}: invalid CSV (${(exc as Error).message})`];
  }
  const lines = text.split(/\r?\n/).filter((_, i, arr) => i < arr.length - 1 || arr[i] !== "");
  if (lines.length === 0) {
    return [`${filename}: empty CSV (no header row)`];
  }
  const rows = lines.map(splitCsvLine);
  const header = rows[0] as string[];
  const problems: string[] = [];
  for (const column of CSV_COLUMNS) {
    if (!header.includes(column)) {
      problems.push(`${filename}: missing column '${column}'`);
    }
  }
  const width = header.length;
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i] as string[];
    if (row.length !== width) {
      problems.push(`${filename}: row ${i + 1} has ${row.length} field(s), expected ${width}`);
    }
  }
  return problems;
}

/** Schema-validation problems for every artifact in `outDir` (empty when it is entirely valid). */
export function validateReport(outDir: string): string[] {
  const problems: string[] = [];
  for (const filename of Object.keys(MANDATORY_ARTIFACT_SCHEMAS)) {
    if (!existsSync(join(outDir, filename)) || !statSync(join(outDir, filename)).isFile()) {
      problems.push(`${filename}: missing`);
    }
  }
  const recorded = recordedOutputs(outDir);
  const trackedNames = new Set([
    ...Object.keys(ARTIFACT_SCHEMAS),
    "report.md",
    "report.html",
    ...OPTIONAL_ARTIFACTS,
  ]);
  for (const filename of trackedNames) {
    if (filename in recorded) {
      const path = join(outDir, filename);
      if (!existsSync(path) || !statSync(path).isFile()) {
        problems.push(`${filename}: missing (recorded in manifest.json's outputs but not on disk)`);
      }
    }
  }
  for (const [filename, schemaName] of Object.entries(ARTIFACT_SCHEMAS)) {
    const path = join(outDir, filename);
    if (!existsSync(path) || !statSync(path).isFile()) {
      continue; // mandatory absence was already reported above; optional absence is not a problem
    }
    let instance: unknown;
    try {
      instance = parseJsonText(readFileSync(path, "utf-8"));
    } catch (exc) {
      problems.push(`${filename}: invalid JSON (${(exc as Error).message})`);
      continue;
    }
    const localValidate = localValidatorFor(schemaName);
    if (!localValidate(instance)) {
      problems.push(...ajvProblems(`${filename}: `, instance, localValidate.errors));
      continue;
    }
    if (filename === "oscal-ar.json") {
      problems.push(...validateOscalArNist(instance));
    }
    if (filename === "results.sarif") {
      problems.push(...validateSarif210(instance));
    }
  }
  for (const filename of ["report.md", "report.html"]) {
    const path = join(outDir, filename);
    if (existsSync(path) && statSync(path).isFile() && readFileSync(path, "utf-8").trim() === "") {
      problems.push(`${filename}: empty`);
    }
  }
  for (const filename of OPTIONAL_ARTIFACTS) {
    const path = join(outDir, filename);
    if (!existsSync(path) || !statSync(path).isFile()) {
      continue; // optional: only present, and only validated, when a run actually produced it
    }
    if (filename === "report.csv") {
      problems.push(...validateCsv(path, filename));
    } else if (filename === "runtime_drift.jsonl") {
      problems.push(...validateJsonl(path, filename));
    } else {
      problems.push(...validateXmlWellformed(path, filename));
    }
  }
  return problems;
}

// --- Real third-party standards: validated only once AgentCE's own bounded profile has already
// accepted the document (see the module docstring). --------------------------------------------

let nistAjv: InstanceType<typeof Ajv2020> | null = null;
let nistValidateCache: ReturnType<InstanceType<typeof Ajv2020>["compile"]> | null = null;

function compiledOscalNistValidator() {
  if (nistValidateCache === null) {
    // `validateSchema: false`: the vendored schema declares itself draft-07 (`$schema`), which this
    // 2020-12 `Ajv2020` instance has no meta-schema for -- Ajv would otherwise refuse to compile it
    // trying to check the schema against a meta-schema it does not have, before ever reaching the
    // instance documents this validates (spec/report/vendor/README.md: draft-07).
    nistAjv = newAjv({ validateSchema: false });
    // Override the built-in `pattern` keyword so it compiles with the `u` flag: the only difference
    // this schema needs from Ajv's default (see the module docstring's Unicode-property note).
    nistAjv.removeKeyword("pattern");
    nistAjv.addKeyword({
      keyword: "pattern",
      type: "string",
      schemaType: "string",
      compile(patternStr: string) {
        const re = new RegExp(patternStr, "u");
        return (data: string) => re.test(data);
      },
      error: {
        message: (cxt: { schema: unknown }) => `must match pattern "${String(cxt.schema)}"`,
      },
    });
    nistValidateCache = nistAjv.compile(loadSchemaJson("oscal-assessment-results-nist-1.1.2"));
  }
  return nistValidateCache;
}

function validateOscalArNist(document: unknown): string[] {
  const validate = compiledOscalNistValidator();
  if (validate(document)) {
    return [];
  }
  return ajvProblems("oscal-ar.json (NIST OSCAL 1.1.2): ", document, validate.errors);
}

let sarifAjv: InstanceType<typeof Ajv2020> | null = null;
let sarifValidateCache: ReturnType<InstanceType<typeof Ajv2020>["compile"]> | null = null;

function compiledSarifValidator() {
  if (sarifValidateCache === null) {
    // The vendored OASIS schema is JSON Schema draft-04 and names itself with the draft-04 `id`
    // keyword; Ajv (draft ≥ 06) only recognises `$id`. This is the schema's only draft-04-specific
    // construct it actually uses (spec/report/vendor/README.md: no exclusiveMinimum/Maximum, no
    // boolean schemas), so renaming just the top-level key is enough -- every nested `id` in this
    // schema is an ordinary data-property name under `properties`, never a schema identifier, and is
    // left untouched.
    const raw = loadSchemaJson("sarif-2.1.0");
    const { id, ...rest } = raw as { id?: string };
    const fixed = { $id: id, ...rest };
    sarifAjv = newAjv({ validateSchema: false });
    sarifValidateCache = sarifAjv.compile(fixed);
  }
  return sarifValidateCache;
}

function validateSarif210(document: unknown): string[] {
  const validate = compiledSarifValidator();
  if (validate(document)) {
    return [];
  }
  return ajvProblems("results.sarif (OASIS SARIF 2.1.0): ", document, validate.errors);
}
