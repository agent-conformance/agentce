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

import { lstatSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import type { CodeKeywordDefinition, KeywordCxt } from "ajv";
import Ajv2020 from "ajv/dist/2020";
import anyOfDef from "ajv/dist/vocabularies/applicator/anyOf";
import oneOfDef from "ajv/dist/vocabularies/applicator/oneOf";
import {
  JsonProfileError,
  NonCanonicalNumber,
  decodeProfile,
  jsonlRecords,
  parseJson,
  parseReportJson,
  readBundled,
} from "./json";
import { dataFileProblem } from "./readiness";
import { ReaderError, checkXml, decodeReportText, isBlank, readCsv } from "./reportReader";
import { byteCompare, joinPath } from "./util";

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
/** The data files every assess run writes and records in manifest.json's outputs, beside the
 * schema-checked artifacts: required when the manifest recorded them, and parsed when present;
 * matches Python's `_REPORT_DATA_FILES` (18.167). */
export const REPORT_DATA_FILES = [
  "integrity.jsonl",
  "coverage.json",
  "applicability.jsonl",
  "quarantine.jsonl",
];

const CSV_COLUMNS = ["control", "subject", "outcome", "control_version", "rung", "mode"];

function schemaDir(): string {
  return resolve(__dirname, "..", "schema");
}

function loadSchemaJson(name: string): Record<string, unknown> {
  return readBundled(readFileSync(joinPath(schemaDir(), `${name}.schema.json`))) as Record<
    string,
    unknown
  >;
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

/** A report file that is there but cannot be read as a regular file; the message is the reason
 * `report --validate` prints in parentheses, as Python's `_CannotRead` (18.167). */
class CannotRead extends Error {}

/** Whether the report folder holds an entry by this name at all; a link counts, dangling or not, and
 * a folder that cannot be entered counts as holding it, as Python's `_has_entry` (18.167). */
function hasEntry(path: string): boolean {
  try {
    lstatSync(path);
    return true;
  } catch (exc) {
    const code = (exc as NodeJS.ErrnoException).code;
    return code !== "ENOENT" && code !== "ENOTDIR";
  }
}

/** The bytes of one report file, or `null` when the folder has no entry by that name. An entry that
 * is there but is not a readable regular file (a FIFO, a folder, a dangling link, a link into a folder
 * that cannot be entered, a file without read permission) throws `CannotRead`: it is reported, never
 * skipped as absent. A FIFO is never opened. Matches Python's `_read_artifact` (18.167). */
function readArtifact(path: string): Buffer | null {
  if (!hasEntry(path)) {
    return null;
  }
  try {
    if (statSync(path).isFile()) {
      return readFileSync(path);
    }
  } catch (exc) {
    const code = (exc as NodeJS.ErrnoException).code;
    if (code === "EACCES" || code === "EPERM") {
      throw new CannotRead("permission denied");
    }
  }
  throw new CannotRead("not a regular file");
}

/** How a report file's bytes become text (SPEC 8.7): a JSON or JSON Lines file by the JSON profile's
 * decoding with a byte-order mark refused, every other format by the report-file reader contract's. */
type Decode = (data: Buffer) => string;
const decodeJsonText: Decode = (data) => decodeProfile(data, "refuse");

function recordedOutputs(outDir: string): Record<string, string> {
  try {
    const data = readArtifact(joinPath(outDir, "manifest.json"));
    const manifest = data === null ? null : parseReportJson(decodeJsonText(data));
    const outputs = isRecord(manifest) ? manifest.outputs : undefined;
    return isRecord(outputs) ? (outputs as Record<string, string>) : {};
  } catch {
    return {}; // a manifest that cannot be read records nothing here; the schema check reports it
  }
}

/** Every non-blank line of `text` ({@link jsonlRecords}) is one JSON object on its own, read by the
 * report-file rule ({@link parseReportJson}). Mirrors `report._validate_jsonl`. */
function validateJsonl(text: string, filename: string): string[] {
  const problems: string[] = [];
  for (const [n, line] of jsonlRecords(text)) {
    let value: unknown;
    try {
      value = parseReportJson(line, n);
    } catch (exc) {
      problems.push(`${filename}: ${(exc as Error).message}`);
      continue;
    }
    if (!isRecord(value)) {
      problems.push(`${filename}: line ${n} is not a JSON object`);
    }
  }
  return problems;
}

/** report.csv by the RFC 4180 reader ({@link readCsv}): a reader-contract fault is its one problem;
 * else no header row, each missing column, and each record whose field count differs from the header's. */
function validateCsv(text: string, filename: string): string[] {
  let rows: string[][];
  try {
    rows = readCsv(text);
  } catch (exc) {
    if (!(exc instanceof ReaderError)) {
      throw exc;
    }
    return [`${filename}: ${exc.message}`];
  }
  if (rows.length === 0) {
    return [`${filename}: empty CSV (no header row)`];
  }
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

/** An XML report file by the hand-written XML 1.0 reader ({@link checkXml}). */
function validateXml(text: string, filename: string): string[] {
  try {
    checkXml(text);
  } catch (exc) {
    if (!(exc instanceof ReaderError)) {
      throw exc;
    }
    return [`${filename}: ${exc.message}`];
  }
  return [];
}

/** Read one report file for `validateReport`: its text, `null` when absent, or the problem line
 * pushed onto `problems` (returned as `undefined`) when it is there but cannot be read. */
function readOrReport(
  outDir: string,
  filename: string,
  problems: string[],
  decode: Decode,
  wrap = "",
): string | null | undefined {
  let data: Buffer | null;
  try {
    data = readArtifact(joinPath(outDir, filename));
  } catch (exc) {
    if (!(exc instanceof CannotRead)) {
      throw exc;
    }
    problems.push(`${filename}: cannot read (${exc.message})`);
    return undefined;
  }
  if (data === null) {
    return null;
  }
  try {
    return decode(data);
  } catch (exc) {
    if (!(exc instanceof JsonProfileError || exc instanceof ReaderError)) {
      throw exc;
    }
    problems.push(`${filename}: ${wrap}${exc.message}`);
    return undefined;
  }
}

/** Schema-validation problems for every artifact in `outDir` (empty when it is entirely valid). An
 * entry that is there but cannot be read is a problem, never an absence; claim.json is required when
 * assertions.json holds an assertion (18.167). */
export function validateReport(outDir: string): string[] {
  const problems: string[] = [];
  for (const filename of Object.keys(MANDATORY_ARTIFACT_SCHEMAS)) {
    if (!hasEntry(joinPath(outDir, filename))) {
      problems.push(`${filename}: missing`);
    }
  }
  const recorded = recordedOutputs(outDir);
  const trackedNames = new Set([
    ...Object.keys(ARTIFACT_SCHEMAS),
    "report.md",
    "report.html",
    ...OPTIONAL_ARTIFACTS,
    ...REPORT_DATA_FILES,
  ]);
  for (const filename of trackedNames) {
    if (filename in recorded && !hasEntry(joinPath(outDir, filename))) {
      problems.push(`${filename}: missing (recorded in manifest.json's outputs but not on disk)`);
    }
  }
  let hasAssertions = false;
  for (const [filename, schemaName] of Object.entries(ARTIFACT_SCHEMAS)) {
    const text = readOrReport(outDir, filename, problems, decodeJsonText);
    if (text == null) {
      continue; // mandatory absence was already reported above; optional absence is not a problem
    }
    let instance: unknown;
    try {
      instance = parseReportJson(text);
    } catch (exc) {
      problems.push(`${filename}: ${(exc as Error).message}`);
      continue;
    }
    if (filename === "assertions.json") {
      hasAssertions = Array.isArray(instance) && instance.length > 0;
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
      problems.push(...validateSarif210(instance, text));
    }
  }
  for (const filename of ["report.md", "report.html"]) {
    const text = readOrReport(outDir, filename, problems, decodeReportText);
    if (text != null && isBlank(text)) {
      problems.push(`${filename}: empty`);
    }
  }
  for (const filename of [...REPORT_DATA_FILES, "claim.json"]) {
    // A data file's refusal reads "cannot be read: ...", as readiness's own reason does (18.184q).
    const wrap = filename === "claim.json" ? "" : "cannot be read: ";
    const text = readOrReport(outDir, filename, problems, decodeJsonText, wrap);
    if (text === undefined) {
      continue;
    }
    if (text === null) {
      if (filename === "claim.json" && hasAssertions) {
        problems.push(
          "claim.json: missing (assertions.json holds assertions, and a run that records any writes claim.json)",
        );
      }
      continue;
    }
    if (filename === "claim.json") {
      try {
        parseReportJson(text);
      } catch (exc) {
        problems.push(`${filename}: ${(exc as Error).message}`);
      }
      continue;
    }
    const reason = dataFileProblem(filename, text);
    if (reason !== undefined) {
      problems.push(`${filename}: cannot be read: ${reason}`);
    }
  }
  for (const filename of OPTIONAL_ARTIFACTS) {
    const jsonl = filename === "runtime_drift.jsonl";
    const text = readOrReport(
      outDir,
      filename,
      problems,
      jsonl ? decodeJsonText : decodeReportText,
    );
    if (text == null) {
      continue; // optional: only present, and only validated, when a run actually produced it
    }
    if (filename === "report.csv") {
      problems.push(...validateCsv(text, filename));
    } else if (jsonl) {
      problems.push(...validateJsonl(text, filename));
    } else {
      problems.push(...validateXml(text, filename));
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

/** The keyword {@link compiledSarifValidator} adds beside every `"type": "integer"`: the number at
 * that place was written without a fraction or exponent. Ajv reads `0.0` as the number 0, which draft-04
 * `integer` would accept, so the lexeme form is kept apart (SPEC 8.7: a field the schema types as an
 * integer refuses the float form), as Python's draft-04 checker and Java's Jackson nodes already do. */
const INTEGER_LEXEME = "x-agentce-integer-lexeme";

/** The JSON pointers (Ajv's `instancePath` form) of the numbers in the SARIF document being validated
 * that were written in the float form; set just before a validation runs. */
let sarifFloatPointers: ReadonlySet<string> = new Set();

/** The JSON pointer of every number in `value` read in the float form (a fraction or an exponent). */
function floatPointers(value: unknown, pointer = "", out = new Set<string>()): Set<string> {
  if (value instanceof NonCanonicalNumber) {
    if (value.reason === "non_integer_number") {
      out.add(pointer);
    }
  } else if (Array.isArray(value)) {
    value.forEach((item, i) => floatPointers(item, `${pointer}/${i}`, out));
  } else if (isRecord(value)) {
    for (const [key, item] of Object.entries(value)) {
      floatPointers(item, `${pointer}/${key.replace(/~/g, "~0").replace(/\//g, "~1")}`, out);
    }
  }
  return out;
}

/** `schema` with {@link INTEGER_LEXEME} beside every `"type": "integer"`, walked without changing it. */
function withIntegerLexeme(schema: unknown): unknown {
  if (Array.isArray(schema)) {
    return schema.map(withIntegerLexeme);
  }
  if (!isRecord(schema)) {
    return schema;
  }
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(schema)) {
    out[key] = withIntegerLexeme(value);
  }
  const type = schema.type;
  const integer =
    type === "integer" ||
    (Array.isArray(type) && type.includes("integer") && !type.includes("number"));
  if (integer) {
    out[INTEGER_LEXEME] = true;
  }
  return out;
}

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
    const fixed = withIntegerLexeme({ $id: id, ...rest }) as Record<string, unknown>;
    sarifAjv = newAjv({ validateSchema: false });
    sarifAjv.addKeyword({
      keyword: INTEGER_LEXEME,
      type: "number",
      schemaType: "boolean",
      error: { message: "must be integer" },
      validate: (
        _schema: boolean,
        _data: number,
        _parent: unknown,
        cxt?: { instancePath: string },
      ) => !sarifFloatPointers.has(cxt?.instancePath ?? ""),
    });
    sarifValidateCache = sarifAjv.compile(fixed);
  }
  return sarifValidateCache;
}

/** `document` (read from `text`) against the real OASIS SARIF 2.1.0 schema. */
function validateSarif210(document: unknown, text: string): string[] {
  const validate = compiledSarifValidator();
  sarifFloatPointers = floatPointers(parseJson(text));
  try {
    if (validate(document)) {
      return [];
    }
  } finally {
    sarifFloatPointers = new Set();
  }
  return ajvProblems("results.sarif (OASIS SARIF 2.1.0): ", document, validate.errors);
}
