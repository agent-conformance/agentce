/**
 * The `agentce` command-line interface (SPEC §8.5): the same verbs, `--json` envelope, and exit-code
 * scheme as the Python reference. `assess`, `validate`, `report`, and `quickstart` are implemented on
 * the existing ECS/report path (the same modules `conformance run` already exercises); a verb not yet
 * implemented returns a stable `input_error` envelope rather than a guess. Every command line is read
 * by one scanner (`argv.ts`) under the vendored SPEC 8.5 grammar table, as Python's `_parse` reads it.
 */

import {
  accessSync,
  closeSync,
  existsSync,
  constants as fsConstants,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { homedir } from "node:os";
import { basename, relative, resolve as resolvePath, sep } from "node:path";
import { load } from "js-yaml";
import { summarizeActivity } from "./activity";
import { resolve as resolveApplicability } from "./applicability";
import {
  type Command as ArgvCommand,
  HELP,
  type Scan,
  command as argvCommand,
  isCommand,
  scan as scanArgv,
} from "./argv";
import { type Assertion, aggregate, assertionFromJson, assertionToJson } from "./assertions";
import {
  WITHHELD_KEY,
  applyDeviations,
  assessSubjects,
  deviationsByControl,
  evaluatedNothing,
} from "./assess";
import { computeBlindSpots } from "./blindSpots";
import { confineToRoot, loadBundle } from "./bundle";
import { DEFAULT_LENS, catalogsDir as bundledCatalogsDir, quickstartDir } from "./bundled";
import { CanonicalizationError, canonicalString } from "./canonical";
import { type Catalog, loadCatalog } from "./catalog";
import { runEcs } from "./conformance";
import { computeCoverage } from "./coverage";
import {
  DIFF_FORMATS,
  diffAssertionSets,
  diffChangeLine,
  diffMdLines,
  normalizePosixPath,
  whatChanged,
} from "./diff";
import { DomainBinding } from "./domain";
import { errorCause, errorFix } from "./errorCatalogue";
import { AgentceError, InputError } from "./errors";
import { ExitCode } from "./exitCodes";
import { isPySpace, parseFailOn } from "./failOn";
import { buildGraph } from "./graph";
import { ingest } from "./ingest";
import {
  IntegrityStrength,
  integrityCaps,
  integrityResultToJson,
  verifyBundle,
  verifyStreams,
} from "./integrity";
import { JsonProfileError, asParsed, isJsonInteger, notValid, readJson } from "./json";
import { DEFAULT_LANGUAGE, availableLanguages, catalogue } from "./messages";
import { installedNoMl } from "./noMl";
import {
  OUT_DIR,
  type OutputRole,
  UNREACHABLE,
  checkOutput,
  refuse,
  throughMissingFolder,
  writeFile,
  writeJsonl,
  writeOutput,
  writingInto,
} from "./outputs";
import { type Profile, loadProfile } from "./profile";
import { writeQuarantine } from "./quarantine";
import {
  NOT_READY,
  computeReadiness,
  deviationLint,
  loadDeviationRegister,
  parseGapsFile,
} from "./readiness";
import {
  BUNDLE_DIR,
  DERIVED_PROFILE_FILE,
  RECORDS_LIMITATION,
  type ScannedRecords,
  recordsLines,
  recordsProfile,
  recordsSubject,
  scanRecords,
} from "./records";
import {
  activityCliLines,
  appliedDeviationIds,
  blindSpotsCliLines,
  digestBytes,
  renderEvidencePack,
  renderOscal,
  renderPublicStatement,
  renderReportHtml,
  renderReportMd,
  renderSarif,
  writeReport,
} from "./report";
import { REPORT_DATA_FILES, validateReport } from "./reportValidate";
import { CommandResult } from "./result";
import { INTOTO_STATEMENT_TYPE, KmsSigner, type Signer, signStatement, signSubjects } from "./sign";
import { StateDir, windowEnd } from "./state";
import { GraphStore } from "./store";
import {
  byteCompare,
  formatTemplate,
  joinPath,
  jsonStringifyAscii,
  pyRepr,
  pyStr,
  sortKeysDeep,
} from "./util";
import { summarize } from "./verdict";
import {
  type TrustRoot,
  loadTrustRoot,
  parseUntrustedJson,
  vendoredTrust,
  verifyCatalog,
  verifyRelease,
} from "./verify";
import { ENGINE_NAME, SPEC_VERSION, engineVersion } from "./version";

const DEFAULT_OUT_DIR = "out";
const REPORT_FORMATS = ["md", "html", "oscal", "sarif", "public", "pack"] as const;
const REPORT_ROLES = ["provider", "deployer"] as const;

function emit(result: CommandResult, json: boolean): void {
  if (json) {
    // json.dumps(envelope, sort_keys=True, indent=2, ensure_ascii=True), matching the Python reference
    console.log(jsonStringifyAscii(sortKeysDeep(result.envelope()), 2));
  } else {
    for (const line of result.humanLines) {
      console.log(line);
    }
  }
}

/** `undefined` stays `undefined`; an empty string becomes `undefined` too, matching the Python
 * reference's `if value` truthiness check (`commands/__init__.py:261-269`), which has no TypeScript
 * equivalent. */
function emptyToUndefined(value: string | undefined): string | undefined {
  return value === undefined || value === "" ? undefined : value;
}

/** The digest assess records for an input it already read and parsed (deviation register, profile,
 * domain file), as Python's `_digest_of`: a read that fails now gets its own key, not
 * `internal.unexpected`. Exported for its unit test. */
export function digestOf(path: string): string {
  let bytes: Buffer;
  try {
    bytes = readFileSync(path);
  } catch (exc) {
    const reason = exc instanceof Error ? exc.message : String(exc);
    throw new InputError(
      "input.digest_unreadable",
      `${path} could not be read to record its digest: ${reason}.`,
      "make the file readable, then re-run.",
    );
  }
  return digestBytes(bytes);
}

/** The options a command writes to (18.180): each output is checked and written through one role. */
const REPORT_OUT: OutputRole = {
  key: "input.out_file_unwritable",
  flag: "--out",
  fix: "pass --out <file> with a writable path.",
};
const SIGN_REPORT_DIR: OutputRole = {
  key: "input.report_dir_unwritable",
  flag: "the report directory",
  fix: "make the report directory writable, then re-run.",
  directory: true,
};
const SIGN_REPORT_FILE: OutputRole = {
  key: "input.report_dir_unwritable",
  flag: "the report file",
  fix: "make the report directory writable, then re-run.",
};
const READINESS_OUT: OutputRole = {
  key: "warning.readiness_file_unwritten",
  flag: "the readiness file",
  fix: "make the report directory writable to keep the readiness file.",
};

function requireDir(
  raw: string | undefined,
  key: string,
  what: string,
  fixOverride?: string,
): string {
  const fix = fixOverride ?? `pass --${key} <dir>.`;
  if (raw === undefined) {
    throw new InputError(`input.${key}_missing`, `${what} is required.`, fix);
  }
  let isDir = false;
  try {
    isDir = statSync(raw).isDirectory();
  } catch {
    isDir = false;
  }
  if (raw === "" || !isDir) {
    throw new InputError(
      `input.${key}_not_a_directory`,
      `${what} ${pyRepr(raw)} is not an existing directory.`,
      fix,
    );
  }
  return raw;
}

function requireFile(
  raw: string | undefined,
  key: string,
  what: string,
  fixOverride?: string,
): string {
  const fix = fixOverride ?? `pass --${key} <file>.`;
  if (raw === undefined) {
    throw new InputError(`input.${key}_missing`, `${what} is required.`, fix);
  }
  let isFile = false;
  try {
    isFile = statSync(raw).isFile();
  } catch {
    isFile = false;
  }
  if (raw === "" || !isFile) {
    throw new InputError(
      `input.${key}_not_a_file`,
      `${what} ${pyRepr(raw)} is not an existing file.`,
      fix,
    );
  }
  // Opened once here, so a file the user cannot read is refused under the role's key before a
  // loader reads it, never internal.unexpected (18.179).
  try {
    closeSync(openSync(raw, "r"));
  } catch {
    throw new InputError(
      `input.${key}_unreadable`,
      `${what} ${pyRepr(raw)} exists but could not be read.`,
      "make the file readable, then re-run.",
    );
  }
  return raw;
}

/** An engine-written JSON file {@link requireFile} accepted, read under the JSON profile (SPEC 8.7, a
 * byte-order mark refused); outside it is `input.<key>_unreadable` naming the rule. Mirrors Python's
 * `_read_input_json`. */
function readInputJson(path: string, key: string, what: string): unknown {
  try {
    return readJson(readFileSync(path), "refuse");
  } catch (exc) {
    if (!(exc instanceof JsonProfileError)) {
      throw exc;
    }
    throw new InputError(
      `input.${key}_unreadable`,
      `${what} ${pyRepr(path)} is ${exc.message}.`,
      "re-run the command that wrote it, or correct the JSON at that line.",
    );
  }
}

/** Each assertion's `rung` and `population` counts, where present, are JSON integers (SPEC 8.7
 * `integer_field_float_form`): `1.5`, `1e20` or `true` is refused, never truncated. Mirrors Python's
 * `_require_integer_fields`. */
function requireIntegerFields(path: string, data: unknown): void {
  (Array.isArray(data) ? data : []).forEach((item, i) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      return;
    }
    const entry = item as Record<string, unknown>;
    const population = entry.population;
    const fields: Array<[string, unknown]> = [["rung", "rung" in entry ? entry.rung : 0]];
    if (population !== null && typeof population === "object" && !Array.isArray(population)) {
      const counts = population as Record<string, unknown>;
      for (const name of ["applicable", "failed"]) {
        fields.push([`population.${name}`, name in counts ? counts[name] : 0]);
      }
    }
    for (const [name, value] of fields) {
      if (!isJsonInteger(value)) {
        throw new InputError(
          "input.from_unreadable",
          `the assertions file ${pyRepr(path)} is outside the JSON profile: ` +
            `integer_field_float_form at [${i}].${name} (SPEC 8.7).`,
          "re-run the command that wrote it, or correct the JSON at that line.",
        );
      }
    }
  });
}

/** A folder the command reads files inside must be listable and enterable, else `key`. */
function requireEnterable(path: string, key: string, what: string): void {
  try {
    accessSync(path, fsConstants.R_OK | fsConstants.X_OK);
  } catch {
    throw new InputError(
      key,
      `${what} ${pyRepr(path)} exists but could not be read.`,
      "make the report directory readable and enterable, then re-run.",
    );
  }
}

/**
 * A path safe to write into a shared artifact (SPEC §8.4: `invocation` records paths, never a person
 * or their local filesystem layout). Under the home directory it becomes `~/…`; else under the current
 * directory it becomes a relative path; anywhere else only the final path component is kept. Mirrors
 * the Python reference's `_scrub_path`.
 */
function scrubPath(raw: string): string {
  const abs = resolvePath(raw);
  const home = resolvePath(homedir());
  if (abs === home || abs.startsWith(home + sep)) {
    const rel = relative(home, abs);
    return rel === "" ? "~/" : `~/${rel.split(sep).join("/")}`;
  }
  const cwd = resolvePath(process.cwd());
  if (abs === cwd || abs.startsWith(cwd + sep)) {
    const rel = relative(cwd, abs);
    return rel === "" ? "." : rel.split(sep).join("/");
  }
  return basename(abs);
}

/** Every catalog the engine ships (base and sector overlays), keyed `id@version`. */
function vendoredCatalogs(): Map<string, string> {
  const found = new Map<string, string>();
  const root = bundledCatalogsDir();
  for (const kind of ["base", "overlays"]) {
    const dir = joinPath(root, kind);
    let names: string[] = [];
    try {
      names = readdirSync(dir).sort(byteCompare);
    } catch {
      names = [];
    }
    for (const name of names) {
      const catalogYaml = joinPath(dir, name, "catalog.yaml");
      if (!existsSync(catalogYaml)) {
        continue;
      }
      try {
        const meta = load(readFileSync(catalogYaml, "utf-8")) as Record<string, unknown> | null;
        if (meta && typeof meta.id === "string" && typeof meta.version === "string") {
          found.set(`${meta.id}@${meta.version}`, joinPath(dir, name));
        }
      } catch {
        // a malformed vendored catalog is skipped, never fatal to discovery
      }
    }
  }
  return found;
}

/** The trust root catalog verification consults: `--trust-root`, else `AGENTCE_TRUST_ROOT`, else the
 * trust root vendored in the engine (SPEC §8.7). Mirrors `_effective_trust_root`: it runs even with
 * zero `--catalog-dir`s, and `quickstart` (no `--trust-root` flag) still honours the variable. The
 * fix text is Python's literal, not the vendored catalogue's. */
function effectiveTrustRoot(flagValue: string | undefined): TrustRoot {
  return explicitTrustRoot(flagValue) ?? vendoredTrust();
}

/** The trust root the operator named (`--trust-root`, else `AGENTCE_TRUST_ROOT`), or null. Mirrors
 * `_explicit_trust_root`: a given `--trust-root` is always checked, so an empty one is refused; an empty or blank
 * variable is unset. */
function explicitTrustRoot(flagValue: string | undefined): TrustRoot | null {
  let raw = flagValue;
  if (raw === undefined) {
    raw = process.env.AGENTCE_TRUST_ROOT;
    if (raw === undefined || raw.trim() === "") {
      return null;
    }
  }
  const path = requireFile(raw, "trust_root", "the trust root", "pass --trust-root <file>.");
  try {
    return loadTrustRoot(path);
  } catch (exc) {
    const message = exc instanceof Error ? exc.message : String(exc);
    throw new InputError(
      "input.trust_root_invalid",
      `the trust root ${pyRepr(path)} could not be loaded: ${message}`,
      "pass --trust-root <file> (or set AGENTCE_TRUST_ROOT) to a trust root in the form of the " +
        "engine's vendored data/trust/dev-root.json.",
    );
  }
}

/** The keys a `source_signed` record's signature must verify against (SPEC §6.6): the operator's own trust root
 * only, less the vendored development keys, whose private seed is published, even when the operator names that
 * root. Read only when a record declares `source_signed`, so a run with no signed stream never depends on the trust
 * root. Mirrors `_source_trust`. */
function sourceTrust(
  flagValue: string | undefined,
  records: ReadonlyArray<Record<string, unknown>>,
): TrustRoot | null {
  if (!records.some(declaresSourceSigned)) {
    return null;
  }
  return explicitTrustRoot(flagValue)?.without(vendoredTrust()) ?? null;
}

function declaresSourceSigned(record: Record<string, unknown>): boolean {
  const data = record.data;
  const block =
    typeof data === "object" && data !== null && !Array.isArray(data)
      ? (data as Record<string, unknown>).integrity
      : undefined;
  return (
    typeof block === "object" &&
    block !== null &&
    !Array.isArray(block) &&
    (block as Record<string, unknown>).strength === IntegrityStrength.SOURCE_SIGNED
  );
}

/** Refuses every `--catalog-dir` catalog whose signature does not verify against `trust` (SPEC
 * §8.7), mirroring `_verify_catalog_dirs` exactly. `allowUnverified` (`--allow-unverified-catalog`)
 * lets the run proceed and records each unverifiable catalog as a limitation string instead; it
 * covers only an absent or failing signature -- the identity cross-check in `resolveCatalogs` has no
 * override. */
function verifyCatalogDirs(
  loaded: Catalog[],
  trust: TrustRoot,
  allowUnverified: boolean,
): string[] {
  const limitations: string[] = [];
  for (const catalog of loaded) {
    // The bytes the load read (18.171), so what verifies is what is assessed.
    const outcome = verifyCatalog(catalog.files ?? catalog.directory, trust);
    if (outcome.verified) {
      continue;
    }
    if (allowUnverified) {
      limitations.push(
        `catalog ${catalog.id}@${catalog.version} at ${scrubPath(catalog.directory)} was used ` +
          `unverified (--allow-unverified-catalog): ${outcome.reason}`,
      );
      continue;
    }
    throw new InputError(
      "input.catalog_unverified",
      `the catalog directory ${scrubPath(catalog.directory)} did not verify against the ` +
        `effective trust root: ${outcome.reason}`,
      "point --catalog-dir at a catalog whose catalog.sig.json verifies, or pass --trust-root " +
        "<file> (or set AGENTCE_TRUST_ROOT) for the root that signed it; --allow-unverified-" +
        "catalog assesses it anyway and records the override as a limitation.",
    );
  }
  return limitations;
}

/**
 * The catalogs an assessment evaluates, and their `id@version` labels (mirrors the Python reference's
 * `_resolve_catalogs`). Each `--catalog-dir` is loaded as given and its signature verified against
 * `trust` before anything else happens (SPEC §8.7). Each requested id — the `--catalog` list, else
 * the profile's declared `catalogs` when no directory was passed — must resolve to a directory that
 * was passed or to a vendored catalog, and every directory that was passed must be one the request
 * names (the identity cross-check below, unconditional on `allowUnverified`: a verified signature
 * proves the bytes were not altered, not that the directory holds the catalog that was asked for). A
 * run that passes no `--catalog` and no `--catalog-dir`, whose profile declares no catalogs,
 * evaluates the baseline (`DEFAULT_LENS`).
 */
function resolveCatalogs(
  requested: string | undefined,
  profile: Profile,
  catalogDirs: string[],
  trust: TrustRoot,
  allowUnverified: boolean,
): { catalogs: Catalog[]; labels: string[]; limitations: string[] } {
  const loaded = catalogDirs.map((d) =>
    loadCatalog(requireDir(d, "catalog_dir", "the catalog directory", "pass --catalog-dir <dir>.")),
  );
  const limitations = verifyCatalogDirs(loaded, trust, allowUnverified);
  const byLabel = new Map<string, Catalog>();
  for (const c of loaded) {
    byLabel.set(`${c.id}@${c.version}`, c);
  }
  let ids: string[];
  if (requested) {
    ids = requested
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  } else if (catalogDirs.length > 0) {
    ids = [];
  } else {
    ids = [...profile.catalogs];
    if (ids.length === 0 && requested === undefined) {
      ids = [DEFAULT_LENS];
    }
  }
  if (ids.length === 0 && loaded.length === 0) {
    throw new InputError(
      "input.catalog_missing",
      "--catalog was given but names no catalog.",
      "pass --catalog <id@version>, or leave --catalog out to assess against the baseline.",
    );
  }
  const uniqueIds = [...new Set(ids)];
  let unresolved = uniqueIds.filter((i) => !byLabel.has(i));
  if (unresolved.length > 0) {
    const vendored = vendoredCatalogs();
    for (const label of unresolved.filter((u) => vendored.has(u))) {
      byLabel.set(label, loadCatalog(vendored.get(label) as string));
    }
    unresolved = unresolved.filter((u) => !vendored.has(u));
  }
  if (unresolved.length > 0) {
    const vendored = vendoredCatalogs();
    const available = [...new Set([...byLabel.keys(), ...vendored.keys()])].sort(byteCompare);
    throw new InputError(
      "input.catalog_unresolved",
      `no catalog directory resolves ${unresolved.map((u) => `'${u}'`).join(", ")} ` +
        `(available: ${available.join(", ") || "none"}).`,
      "use an available <id>@<version>, or pass --catalog-dir <dir> for a catalog on disk.",
    );
  }
  // A verified signature proves the bytes were not altered; it does not prove the directory holds
  // the catalog that was asked for. When both a request and a directory were given, every directory
  // must carry an id@version the request names -- a rebranded or swapped catalog is refused here
  // even though its own signature verifies. Deliberately separate from the signature check above and
  // with no override: SPEC §8.7's --allow-unverified-catalog waives "unsigned or unverifiable", and a
  // catalog whose signature verifies but whose identity is not the one requested is neither.
  if (ids.length > 0 && loaded.length > 0) {
    const wanted = new Set(ids);
    const unrequested = loaded
      .map((c) => `${c.id}@${c.version}`)
      .filter((label) => !wanted.has(label));
    if (unrequested.length > 0) {
      throw new InputError(
        "input.catalog_mismatch",
        `a --catalog-dir carries ${unrequested.map((u) => pyRepr(u)).join(", ")}, which --catalog ` +
          `did not request (${ids.map((i) => pyRepr(i)).join(", ")}).`,
        "pass --catalog-dir for the catalog you named, or name the id@version the directory carries.",
      );
    }
  }
  const labels = [...new Set([...ids, ...loaded.map((c) => `${c.id}@${c.version}`)])];
  return {
    catalogs: labels.map((l) => byLabel.get(l) as Catalog),
    labels,
    limitations,
  };
}

/** The exit-3 error for a run whose every (control, subject) pair was inapplicable or unassessed. */
function nothingEvaluated(
  profile: Profile,
  accepted: Array<Record<string, unknown>>,
  pairs: number,
): InputError {
  const declared = [...new Set(profile.subjects.map((s) => s.id))].sort(byteCompare);
  const seen = [
    ...new Set(accepted.filter((e) => "subject" in e).map((e) => String(e.subject))),
  ].sort(byteCompare);
  const matched = declared.filter((s) => seen.includes(s));
  const ids = (list: string[], cap = 3): string => {
    const shown = list
      .slice(0, cap)
      .map((i) => `'${i.slice(0, 80)}'`)
      .join(", ");
    return (shown || "none") + (list.length > cap ? ` and ${list.length - cap} more` : "");
  };
  let why: string;
  if (accepted.length === 0) {
    why = "the bundle has no accepted events";
  } else if (matched.length === 0) {
    why =
      `none of the ${accepted.length} accepted events is about a subject the profile declares ` +
      `(profile: ${ids(declared)}; bundle: ${ids(seen)})`;
  } else {
    why =
      `the ${accepted.length} accepted events give no control an applicable population ` +
      `(subjects matched: ${ids(matched)})`;
  }
  return new InputError(
    "input.nothing_evaluated",
    `assessed ${pairs} (control, subject) pairs and none reached conformant, non-conformant, or ` +
      `insufficient_evidence: ${why}. The reports were written, but they judge nothing.`,
    "emit under the subject and source the profile declares, and record the evidence the " +
      "catalog's controls apply to.",
  );
}

/** Python's `agentce conformance --help` (COLUMNS=80), word for word. */
const CONFORMANCE_USAGE = `usage: agentce conformance [-h] [--json] [--debug] [--quiet] <action> ...

positional arguments:
  <action>
    run       run the ECS and emit an implementation report

options:
  -h, --help  show this help message and exit

global options:
  --json      emit machine-readable JSON on stdout
  --debug     verbose logs on stderr and a stack trace on unexpected errors
  --quiet     log warnings and errors only
`;

/** Python's `agentce conformance run --help` (COLUMNS=80), word for word. */
const CONFORMANCE_RUN_USAGE = `usage: agentce conformance run [-h] [--json] [--debug] [--quiet]
                               [--engine ENGINE] [--corpus CORPUS] [--out OUT]
                               [--adapters ADAPTERS]

options:
  -h, --help           show this help message and exit
  --engine ENGINE      the engine path under test
  --corpus CORPUS      the corpus directory
  --out OUT            the output directory for the implementation report
  --adapters ADAPTERS  the adapters directory; also run adapter conformance
                       (SPEC 11.5, 12.3)

global options:
  --json               emit machine-readable JSON on stdout
  --debug              verbose logs on stderr and a stack trace on unexpected
                       errors
  --quiet              log warnings and errors only
`;

/** Python's `commands.conformance_action_error`: no action, or one conformance does not have. */
function conformanceActionError(): InputError {
  return new InputError(
    "input.conformance_action",
    "the only conformance action is `run`.",
    "run `agentce conformance run ...`.",
  );
}

/** Python's `commands._refuse_empty`, the one empty-value rule for a path option the command reads
 * with a default or writes to: an empty value names no path, so it is refused under the role's key
 * before anything is read or written, never read as the working directory, the default, or no value.
 * `requireDir` and `requireFile` apply the same rule to the paths they check. */
function refuseEmpty(
  raw: string | undefined,
  key: string,
  flag: string,
  fix: string,
  noun = "file",
): void {
  if (raw === "") {
    throw new InputError(key, `${flag} '' names no ${noun}.`, fix);
  }
}

/** Python's `commands._refuse_empty_out`: not empty, and not through a folder that does not exist
 * (`nosub/../out`), so a refusal creates nothing (18.184c). */
function refuseEmptyOut(out: string | undefined): void {
  refuseEmpty(
    out,
    "input.out_dir_unwritable",
    "--out",
    "choose a writable --out directory.",
    "directory",
  );
  if (out !== undefined && throughMissingFolder(out)) {
    throw refuse(OUT_DIR, out, UNREACHABLE);
  }
}

/** `agentce conformance run`, read from its grammar's scan alone. An empty --engine, --corpus or
 * --adapters names no directory (Node's stat of `""` fails, as Python's `_require_dir` refuses ''). */
function cmdConformance(args: Scan): CommandResult {
  const result = new CommandResult("conformance");
  if (args.action === undefined) {
    throw conformanceActionError(); // the scanner refuses any action but run itself
  }
  const v = args.values;
  const engine = requireDir(v["--engine"], "engine", "the engine path");
  const corpus = requireDir(v["--corpus"], "corpus", "the corpus directory");
  const adapters = v["--adapters"];
  const adaptersDir =
    adapters !== undefined ? requireDir(adapters, "adapters", "the adapters directory") : null;
  // The adapters folder becomes the orchestrator's working folder, so one that cannot be listed
  // or entered is refused here, before any subprocess starts (18.179).
  if (adaptersDir !== null) {
    try {
      accessSync(adaptersDir, fsConstants.R_OK | fsConstants.X_OK);
    } catch {
      throw new InputError(
        "input.adapters_unreadable",
        `the adapters directory ${pyRepr(adaptersDir)} exists but could not be read.`,
        "make the folder readable and enterable, then re-run.",
      );
    }
  }
  const out = v["--out"];
  refuseEmptyOut(out);
  if (out !== undefined) {
    checkOutput(out, OUT_DIR);
  }
  const report = runEcs({
    enginePath: engine,
    corpusDir: corpus,
    outDir: out ?? null,
    adaptersDir,
  });

  result.data.action = "run";
  result.data.engine = engine;
  result.data.corpus = corpus;
  if (out !== undefined) {
    result.data.out = out;
  }
  Object.assign(result.data, report); // report.engine (impl/version) overwrites the engine path, as in the reference
  result.note(
    `ECS: ${report.projects.identical}/${report.projects.total} identical; ` +
      `claim ${report.claim}; no_ml ${report.no_ml}`,
  );
  const detail = report.adapter_conformance;
  if (report.adapters !== undefined && detail !== undefined) {
    result.note(
      `adapters: ${pyStr(detail.identical)}/${pyStr(detail.total)} identical; ` +
        `round_trip ${pyStr(detail.round_trip)}; claim ${report.adapters}`,
    );
  }
  if (report.claim !== "full") {
    result.addCode(ExitCode.FINDINGS);
  }
  if (report.adapters !== undefined && report.adapters !== "full") {
    result.addCode(ExitCode.FINDINGS);
  }
  return result;
}

/** Python's `agentce validate --help` (COLUMNS=80), word for word. */
const VALIDATE_USAGE = `usage: agentce validate [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                        [--out OUT]

options:
  -h, --help       show this help message and exit
  --bundle BUNDLE  the evidence bundle directory
  --out OUT        write quarantine.jsonl to this directory

global options:
  --json           emit machine-readable JSON on stdout
  --debug          verbose logs on stderr and a stack trace on unexpected
                   errors
  --quiet          log warnings and errors only
`;

function cmdValidate(args: Scan): CommandResult {
  const result = new CommandResult("validate");
  const bundleDir = requireDir(args.values["--bundle"], "bundle", "the evidence bundle");
  const out = args.values["--out"];
  refuseEmptyOut(out);
  const bundle = loadBundle(bundleDir); // raises InputError (exit 3) on a missing/mismatching manifest
  const ingested = ingest(bundle);
  result.data.bundle = bundleDir;
  result.data.bundle_digest = bundle.digest;
  result.data.accepted = ingested.accepted.length;
  result.data.quarantined = ingested.quarantined.length;
  const byReason = new Map<string, number>();
  for (const q of ingested.quarantined) {
    byReason.set(q.reason, (byReason.get(q.reason) ?? 0) + 1);
  }
  const quarantineByReason: Record<string, number> = {};
  for (const reason of [...byReason.keys()].sort()) {
    quarantineByReason[reason] = byReason.get(reason) as number;
  }
  result.data.quarantine_by_reason = quarantineByReason;
  if (out !== undefined) {
    const quarantinePath = joinPath(out, "quarantine.jsonl");
    writeQuarantine(ingested.quarantined, quarantinePath);
    result.data.quarantine_file = quarantinePath;
  }
  result.note(
    `validated ${bundleDir}: ${ingested.accepted.length} accepted, ` +
      `${ingested.quarantined.length} quarantined`,
  );
  if (ingested.quarantined.length > 0) {
    result.addCode(ExitCode.FINDINGS);
  }
  return result;
}

interface AssessOptions {
  /** `assess <folder>`'s records folder; unset for a `--bundle`/`--profile` run. */
  folder?: string;
  bundle?: string;
  profile?: string;
  /** `--package-for-sharing`, which a records folder refuses. */
  packageForSharing?: boolean;
  catalog?: string;
  domain?: string;
  catalogDirs: string[];
  out: string;
  state?: string;
  invocationCommand: string;
  /** `--trust-root`'s value; `quickstart` has no such flag, so it leaves this unset. */
  trustRootFlag?: string;
  /** `--allow-unverified-catalog`. */
  allowUnverified: boolean;
  /** `--deviations`' register path; `quickstart` has no such flag. */
  deviations?: string;
  /** `--fail-on`'s expression; `quickstart` never sets it. */
  failOn?: string;
  /** `--emit`'s raw value as given (`""` included); `quickstart` never sets it. */
  emit?: string;
  /** `--for`'s preset as given; `quickstart` never sets it. */
  forPreset?: string;
  /** `--report-language` as given; unset means `en`. `quickstart` never sets it. */
  reportLanguage?: string;
}

/** Every format Python's `assess --emit` accepts, in Python's order (`commands.EMIT_FORMATS`). */
const EMIT_FORMATS = [
  "md",
  "html",
  "oscal",
  "sarif",
  "public",
  "pack",
  "junit",
  "csv",
  "oscal_xml",
  "pdf",
  "remediation",
  "skill",
];

/** Python's `assess --for` presets, in Python's order (`commands.PRESET_EMIT`). */
const FOR_PRESETS = [
  "engineering",
  "compliance",
  "security",
  "ci",
  "share",
  "risk-lead",
  "auditor",
  "buyer",
];

/** Refuse `--emit`/`--for` before anything is written. Python's three refusals come first, in
 * Python's order (an unknown format, then both flags, then an unknown preset); a well-formed value is
 * then refused with `input.emit_unsupported`, because this engine writes only the core outputs and
 * would otherwise ignore the flag (18.105, until 18.16b ports the formats and presets). */
function refuseEmitAndFor(emit: string | undefined, forPreset: string | undefined): void {
  if (emit !== undefined) {
    const tokens = emit
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t !== "");
    const invalid = tokens.filter((t) => !EMIT_FORMATS.includes(t));
    if (invalid.length > 0) {
      throw new InputError(
        "input.emit_format",
        `unknown --emit format(s): ${invalid.map((t) => `'${t}'`).join(", ")}.`,
        `choose from: ${EMIT_FORMATS.join(", ")}.`,
      );
    }
  }
  if (forPreset !== undefined && emit !== undefined) {
    throw new InputError(
      "input.for_emit_ambiguous",
      "both --for and --emit were given.",
      "pass --for <preset> or --emit <formats>, not both.",
    );
  }
  if (forPreset !== undefined && !FOR_PRESETS.includes(forPreset)) {
    throw new InputError(
      "input.for_preset",
      `unknown --for preset '${forPreset}'.`,
      `choose one of: ${FOR_PRESETS.join(", ")}.`,
    );
  }
  if (forPreset !== undefined || emit !== undefined) {
    const flag = forPreset !== undefined ? "--for" : "--emit";
    throw new InputError(
      "input.emit_unsupported",
      `the TypeScript engine cannot write ${flag} output yet; it writes only the core outputs.`,
      `drop ${flag} for the core outputs, or run assess with the Python engine for the role views and extra formats.`,
    );
  }
}

/** Run a full assessment (ingest, integrity, graph, coverage, applicability, evaluate, report) — the
 * same pipeline `conformance run` exercises per corpus project, generalised to an arbitrary bundle. */
function runAssess(options: AssessOptions): CommandResult {
  const result = new CommandResult("assess");
  const out = options.out;
  let bundleDir: string;
  let profilePath: string;
  let profileObj: Profile;
  let scanned: ScannedRecords | undefined;
  /** The adopter's own --profile on a records run (undefined: the run derives one). */
  let declared: Profile | undefined;
  if (options.folder === undefined) {
    bundleDir = requireDir(options.bundle, "bundle", "the evidence bundle");
    profilePath = requireFile(options.profile, "profile", "the applicability profile");
    profileObj = loadProfile(profilePath);
  } else {
    // A records folder is read in memory first; nothing is written until the run is known to be
    // well-formed, so a refused run leaves nothing behind that looks like a result.
    if (options.bundle !== undefined) {
      throw new InputError(
        "input.records_source_ambiguous",
        "both a records folder and --bundle were given.",
        "pass either a folder of trace exports or --bundle <dir>, not both.",
      );
    }
    const folder = requireDir(
      options.folder,
      "records",
      "the records folder",
      "pass a folder of OpenTelemetry GenAI or OpenInference trace exports.",
    );
    if (options.profile !== undefined) {
      profilePath = requireFile(options.profile, "profile", "the applicability profile");
      declared = loadProfile(profilePath);
    } else {
      profilePath = joinPath(out, DERIVED_PROFILE_FILE);
    }
    const forced = recordsSubject(declared);
    scanned = scanRecords(folder, {
      subject: forced,
      exclude: out,
      perAgent: declared !== undefined && forced === undefined,
    });
    profileObj = recordsProfile(declared, scanned);
    bundleDir = joinPath(out, BUNDLE_DIR);
    if (options.packageForSharing) {
      throw new InputError(
        "input.package_requires_bundle",
        "--package-for-sharing works only with --bundle/--profile, not a records folder.",
        "pass --bundle and --profile instead of a records folder, or drop --package-for-sharing.",
      );
    }
  }
  // Then, in Python's order and before anything is written: packaging (not ported: refused rather
  // than accepted and dropped), the report language, and the unverified-catalog override, which has
  // nothing to apply to without a --catalog-dir (18.109).
  if (options.packageForSharing) {
    throw new InputError(
      "input.package_unsupported",
      errorCause("input.package_unsupported"),
      errorFix("input.package_unsupported"),
    );
  }
  const language = options.reportLanguage ?? DEFAULT_LANGUAGE;
  if (!availableLanguages().includes(language)) {
    throw reportLanguageError("--report-language", language);
  }
  const messages = catalogue(language);
  if (options.allowUnverified && options.catalogDirs.length === 0) {
    throw new InputError(
      "input.allow_unverified_requires_catalog_dir",
      "--allow-unverified-catalog was given without --catalog-dir.",
      "add --catalog-dir <dir>, or drop --allow-unverified-catalog.",
    );
  }
  refuseEmitAndFor(options.emit, options.forPreset);
  // --fail-on is parsed (never eval'd) before any output is written: a hostile or malformed expression
  // is refused at exit 3 before any assertion is evaluated against it (SPEC §7), and before the
  // catalogs, --state or --deviations are looked at, in Python's order.
  const failOnPredicate = options.failOn !== undefined ? parseFailOn(options.failOn) : undefined;
  // --state's writability is proved here, before any --out write below (writeQuarantine etc.), not
  // only when state.record() finally writes at the end of this function: a run refused for an
  // unwritable --state must leave nothing in --out that looks like a result either.
  if (options.state !== undefined) {
    StateDir.ensureWritable(options.state);
  }
  // The effective trust root resolves on every run, unconditionally -- even with zero
  // `--catalog-dir`s (python-reference.md §9) -- before `resolveCatalogs` ever inspects one.
  const trust = effectiveTrustRoot(options.trustRootFlag);
  const {
    catalogs,
    labels: catalogLabels,
    limitations,
  } = resolveCatalogs(
    options.catalog,
    profileObj,
    options.catalogDirs,
    trust,
    options.allowUnverified,
  );
  // The file arguments read later are checked here too, so a run refused for one leaves --out
  // empty (census odd-inputs line 46, 18.184c).
  const domainPath =
    options.domain !== undefined
      ? requireFile(options.domain, "domain", "the domain binding")
      : undefined;
  const deviationFile =
    options.deviations !== undefined
      ? requireFile(options.deviations, "deviations", "the deviation register")
      : undefined;
  if (scanned !== undefined) {
    scanned.write(out, declared === undefined);
    limitations.push(RECORDS_LIMITATION);
  }

  // Stage 1: ingest and validate. A missing/mismatching manifest aborts with exit 3.
  const bundle = loadBundle(bundleDir);
  const ingested = ingest(bundle);
  // Resolved before anything is written: a trust root that cannot be loaded refuses the run (SPEC §6.6).
  const signedTrust = sourceTrust(options.trustRootFlag, ingested.rawAccepted);
  writeQuarantine(ingested.quarantined, joinPath(out, "quarantine.jsonl"));

  // Stage 2: integrity verification, one IntegrityResult per stream.
  const integrityStreams = verifyStreams(
    ingested.rawAccepted,
    bundle.manifest,
    bundle.root,
    signedTrust,
  );
  const integrityResults = [...integrityStreams.values()];
  writingInto(out, OUT_DIR, () =>
    writeJsonl(integrityResults.map(integrityResultToJson), joinPath(out, "integrity.jsonl")),
  );

  // Stage 3: build the provenance graph (in-memory store; ADR-0001's TypeScript variant).
  const domain = domainPath !== undefined ? DomainBinding.load(domainPath) : DomainBinding.empty();
  const store = new GraphStore();
  buildGraph(ingested.accepted, { domain, store });
  const graphTriples = store.tripleCount();

  // Stage 4: coverage and reconciliation against independent denominators.
  const coverage = computeCoverage(ingested.accepted, profileObj, bundle.members);
  mkdirSync(out, { recursive: true });
  writingInto(out, OUT_DIR, () =>
    writeFile(
      joinPath(out, "coverage.json"),
      `${JSON.stringify(sortKeysDeep(coverage), null, 2)}\n`,
    ),
  );

  // Stage 5: applicability resolution and drift.
  const statements = resolveApplicability(profileObj, ingested.accepted);
  writingInto(out, OUT_DIR, () => writeJsonl(statements, joinPath(out, "applicability.jsonl")));
  const driftFindings = statements.reduce(
    (n, s) => n + ((s.drift as unknown[] | undefined)?.length ?? 0),
    0,
  );

  // Stage 6: catalog evaluation and report artifacts.
  let evaluated = assessSubjects(
    ingested.accepted,
    profileObj,
    catalogs,
    domain,
    scanned === undefined,
    ingested.withheld,
    integrityCaps(ingested.rawAccepted, ingested.accepted, integrityStreams),
    coverage,
  );
  // A records run that judged nothing renders no report: its headline would read "Conformant".
  if (scanned !== undefined && evaluatedNothing(evaluated)) {
    throw nothingEvaluated(profileObj, ingested.accepted, evaluated.length);
  }

  // Stage 6a: incremental state (SPEC §5.4 B7, HR-10).
  const newWindowEnd = windowEnd(profileObj.observationWindow, ingested.accepted);
  let state: StateDir | null = null;
  let supersedes: string[] = [];
  if (options.state !== undefined) {
    state = StateDir.load(options.state); // incompatible state_version aborts with exit 3
    [supersedes] = state.plan(bundle.digest, ingested.accepted, newWindowEnd);
  }

  // Deviation application (SPEC §13.3.4 Stage 2), in Python's order: after evaluation and before the
  // blind spots, the report and every rendering, so all of them see the flipped outcomes and the
  // expired-and-ignored limitations.
  let deviations: Record<string, unknown>[] | undefined;
  let deviationRegisterDigest: string | undefined;
  if (deviationFile !== undefined) {
    deviations = loadDeviationRegister(deviationFile);
    const outcomesByControl = new Map<string, Set<string>>();
    for (const a of evaluated) {
      const outcomes = outcomesByControl.get(a.control) ?? new Set<string>();
      outcomes.add(a.outcome);
      outcomesByControl.set(a.control, outcomes);
    }
    const problems = deviationLint(deviations, {
      controlIds: new Set(catalogs.flatMap((c) => c.controls.map((control) => control.id))),
      outcomesByControl,
      appliedControls: new Set<string>(),
      asOf: newWindowEnd,
    });
    if (problems.length > 0) {
      throw new InputError(
        "input.deviation_invalid",
        `the deviation register at ${pyRepr(options.deviations)} is invalid: ${problems.join("; ")}`,
        "correct the deviation register and re-run.",
      );
    }
    const applied = applyDeviations(evaluated, deviations, newWindowEnd);
    evaluated = applied.assertions;
    const byControl = deviationsByControl(deviations);
    const template = messages["readiness.deviation_expired_ignored"] as string;
    for (const control of applied.expired) {
      limitations.push(
        formatTemplate(template, {
          control,
          expiry: pyStr(byControl.get(control)?.expiry ?? ""),
        }),
      );
    }
    const notWaivable = messages["readiness.deviation_not_waivable"] as string;
    for (const control of new Set(applied.notWaivable)) {
      limitations.push(formatTemplate(notWaivable, { control }));
    }
    deviationRegisterDigest = digestOf(deviationFile);
  }

  // A records run with several subjects declares only what the adopter's own profile names (18.77).
  let declaredSubjectIds: Set<string> | undefined;
  if (scanned !== undefined && profileObj.subjects.length > 1) {
    declaredSubjectIds = new Set(declared?.subjects.map((s) => s.id) ?? []);
  }
  const activity = summarizeActivity(ingested.accepted, profileObj, declaredSubjectIds);
  const blindSpots = computeBlindSpots(evaluated, profileObj, catalogs, ingested.accepted);
  const applicabilityProfileDigest = digestOf(profilePath);
  const domainBindingDigest = domainPath !== undefined ? digestOf(domainPath) : undefined;
  // The data files Stages 1/2/4/5 wrote above get a manifest-tracked digest too, as in
  // commands/__init__.py: read back once, in this process, right after this run wrote them.
  const extraOutputs: Record<string, Buffer> = {};
  for (const artifact of REPORT_DATA_FILES) {
    extraOutputs[artifact] = readFileSync(joinPath(out, artifact));
  }
  writingInto(out, OUT_DIR, () =>
    writeReport(out, evaluated, {
      bundleDigest: bundle.digest,
      catalogs: catalogLabels,
      catalogObjects: catalogs,
      // config.py's `operator` default (SPEC §8.4); agentce.toml is not read here (Python only).
      operator: process.env.AGENTCE_OPERATOR ?? "unset",
      invocation:
        options.folder !== undefined
          ? ["assess", scrubPath(options.folder)]
          : [options.invocationCommand, scrubPath(bundleDir), scrubPath(profilePath)],
      supersedes,
      activity,
      blindSpots,
      events: ingested.accepted,
      profile: profileObj,
      limitations,
      applicabilityProfileDigest,
      domainBindingDigest,
      deviationRegisterDigest,
      deviations,
      declaredSubjectIds,
      reportLanguage: language,
      extraOutputs,
    }),
  );
  if (state !== null) {
    state.record(bundle.digest, joinPath(out, "manifest.json"), newWindowEnd);
  }

  const nonConformant = evaluated.filter((a) => a.outcome === "non-conformant").length;
  const summary = summarize(evaluated);
  result.data.bundle = bundleDir;
  result.data.bundle_digest = bundle.digest;
  result.data.profile = profilePath;
  result.data.catalogs = catalogLabels;
  result.data.out = out;
  result.data.accepted = ingested.accepted.length;
  result.data.quarantined = ingested.quarantined.length;
  result.data.streams = integrityResults.length;
  result.data.graph_triples = graphTriples;
  result.data.subjects = Object.keys(coverage.subjects as Record<string, unknown>).length;
  result.data.drift_findings = driftFindings;
  result.data.assertions = evaluated.length;
  result.data.summary = {
    verdict: summary.verdict,
    counts: summary.counts,
    top_gaps: summary.topGaps,
  };
  result.data.activity = activity;
  result.data.blind_spots = blindSpots;
  if (scanned !== undefined) {
    result.data.records = scanned.summary;
    const shown = declared === undefined ? `${out}/${DERIVED_PROFILE_FILE}` : undefined;
    for (const line of recordsLines(scanned.summary, shown)) {
      result.note(line);
    }
  }
  if (options.state !== undefined) {
    result.data.supersedes = supersedes;
  }
  if (limitations.length > 0) {
    // The override is never silent: on stderr for the operator and in the manifest for every later
    // reader (SPEC §8.7).
    result.data.limitations = limitations;
    for (const limitation of limitations) {
      result.note(limitation);
    }
  }
  // An insufficient_evidence outcome whose withheld records read non-conformant with the held ones exits as a
  // non-conformant outcome would, so a late record never gives a better exit code (SPEC §8.5, 18.149).
  const withheldNonConformant = evaluated
    .filter((a) => a.withheldNonConformant === true)
    .sort((a, b) => byteCompare(a.control, b.control) || byteCompare(a.subject, b.subject));
  if (withheldNonConformant.length > 0) {
    result.data.withheld_non_conformant = withheldNonConformant.map((a) => ({
      control: a.control,
      subject: a.subject,
      key: WITHHELD_KEY,
    }));
  }
  if (failOnPredicate !== undefined) {
    // A policy-scoped gate replaces the default any-non-conformant rule: the exit code reflects only
    // the assertions the expression names, never the whole run (SPEC §8.5, finding #29). A withheld
    // non-conformant outcome is matched as the non-conformant outcome it would be on time.
    const matched = evaluated.filter(
      (a) =>
        failOnPredicate(a) ||
        (a.withheldNonConformant === true && failOnPredicate({ ...a, outcome: "non-conformant" })),
    ).length;
    result.data.fail_on = { expression: options.failOn, matched };
    if (matched > 0) {
      result.addCode(ExitCode.FINDINGS);
    }
  } else if (nonConformant > 0 || withheldNonConformant.length > 0) {
    result.addCode(ExitCode.FINDINGS);
  }
  // SPEC.md:1076 (SPEC Sec.8.5): exit 2 whenever any assertion is both insufficient_evidence and
  // severity: high -- scoped, as in Python, to the formal --bundle/--profile assessment: a records run
  // never returns 2 on its own, so a first scan is not a failure (VG-RECORDS-EXIT-CODE).
  let highInsufficient: string[] = [];
  if (scanned === undefined) {
    highInsufficient = Array.from(
      new Set(
        evaluated
          .filter((a) => a.outcome === "insufficient_evidence" && a.severity === "high")
          .map((a) => a.control),
      ),
    ).sort();
  }
  if (highInsufficient.length > 0) {
    result.addCode(ExitCode.INSUFFICIENT_EVIDENCE);
  }
  if (evaluatedNothing(evaluated)) {
    throw nothingEvaluated(profileObj, ingested.accepted, evaluated.length);
  }
  for (const line of activityCliLines(activity, messages)) {
    result.note(line);
  }
  for (const line of blindSpotsCliLines(blindSpots)) {
    result.note(line);
  }
  result.note(`verdict: ${summary.verdict}`);
  result.note(
    `assessed ${evaluated.length} (control, subject) pairs; ${nonConformant} non-conformant`,
  );
  if (highInsufficient.length > 0) {
    result.note(
      `insufficient evidence on severity-high control(s): ${highInsufficient.join(", ")}`,
    );
  }
  if (withheldNonConformant.length > 0) {
    const controls = [...new Set(withheldNonConformant.map((a) => a.control))];
    result.note(`${WITHHELD_KEY}: ${controls.join(", ")}: ${errorCause(WITHHELD_KEY)}`);
  }
  return result;
}

/** Python's `agentce assess --help` (COLUMNS=80), word for word. */
const ASSESS_USAGE = `usage: agentce assess [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                      [--catalog CATALOG] [--profile PROFILE]
                      [--deviations DEVIATIONS] [--domain DOMAIN]
                      [--catalog-dir CATALOG_DIR] [--trust-root TRUST_ROOT]
                      [--allow-unverified-catalog] [--package-for-sharing]
                      [--out OUT] [--state STATE]
                      [--report-language REPORT_LANGUAGE] [--emit EMIT]
                      [--for PRESET] [--fail-on FAIL_ON]
                      [folder]

Run a full assessment: \`agentce assess <folder>\` over a folder of trace
exports, or \`agentce assess --bundle <dir> --profile <file>\` over an evidence
bundle.

positional arguments:
  folder                a folder of OpenTelemetry GenAI or OpenInference trace
                        exports (.json, .jsonl, .ndjson): assess reads it and
                        writes a default profile, so no other flag is needed

options:
  -h, --help            show this help message and exit
  --bundle BUNDLE       the evidence bundle directory
  --catalog CATALOG     catalog ids, comma-separated: <id@ver>[,<id@ver>...]
                        (default: the profile's catalogs, else the baseline)
  --profile PROFILE     the applicability profile file
  --deviations DEVIATIONS
                        the deviation register file: a lint-clean, unexpired
                        entry flips its control's non-conformant outcome to
                        partial
  --domain DOMAIN       the domain ontology binding file
  --catalog-dir CATALOG_DIR
                        a catalog directory to evaluate (repeatable)
  --trust-root TRUST_ROOT
                        trust root every --catalog-dir signature is verified
                        against (default: AGENTCE_TRUST_ROOT, else the
                        vendored development root)
  --allow-unverified-catalog
                        assess a --catalog-dir catalog whose signature is
                        absent or does not verify, recording the override as a
                        limitation in the manifest and the claim (SPEC 8.7).
                        Requires --catalog-dir.
  --package-for-sharing
                        copy the evidence bundle, profile, domain binding and
                        every --catalog-dir into --out/bundle/ so the
                        directory is self-contained: \`agentce sign\` then
                        \`agentce verify --report\` re-runs it offline on
                        another machine. Requires --bundle (not a records
                        folder).
  --out OUT             the output directory (default: ./out)
  --state STATE         the incremental state directory
  --report-language REPORT_LANGUAGE
                        message-key catalogue for the report: de or en
                        (default: en); does not affect assertions.json (SPEC
                        9.3)
  --emit EMIT           comma-separated report formats to render (default:
                        html, md, oscal, pack, sarif, skill); one or more of:
                        md, html, oscal, sarif, public, pack, junit, csv,
                        oscal_xml, pdf, remediation, skill. Setting CI adds
                        junit to the default automatically (see --for); an
                        explicit --emit is never extended.
  --for PRESET          report preset for an audience, in place of --emit:
                        engineering (html, md, remediation, skill); compliance
                        (csv, oscal, oscal_xml, pack, public); security (html,
                        md, sarif); ci (junit, sarif); share (html, md, pack,
                        pdf, public); risk-lead (html, md); auditor (oscal,
                        oscal_xml, pack); buyer (pack). CI detected
                        automatically (adds junit to the default) when neither
                        --for nor --emit is given.
  --fail-on FAIL_ON     gate the exit code on a tiny deterministic expression
                        over assertion fields (control, subject, outcome,
                        severity, family, rung, mode), e.g. 'outcome=="non-
                        conformant" and severity=="high"' (comparisons joined
                        by and/or; never a general expression language).
                        Replaces the default any-non-conformant rule when
                        given.

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
`;

/** `agentce assess`, read from its grammar's scan alone. */
function cmdAssess(args: Scan): CommandResult {
  const v = args.values;
  refuseEmptyOut(v["--out"]);
  refuseEmpty(
    v["--state"],
    "input.state_dir_unwritable",
    "--state",
    "choose a writable --state directory.",
    "directory",
  );
  return runAssess({
    folder: args.positionals[0],
    packageForSharing: args.flags.has("--package-for-sharing"),
    deviations: v["--deviations"],
    failOn: v["--fail-on"],
    emit: v["--emit"],
    forPreset: v["--for"],
    bundle: v["--bundle"],
    profile: v["--profile"],
    catalog: v["--catalog"],
    domain: v["--domain"],
    catalogDirs: [...(args.lists["--catalog-dir"] ?? [])],
    out: v["--out"] ?? DEFAULT_OUT_DIR,
    state: v["--state"],
    invocationCommand: "assess",
    trustRootFlag: v["--trust-root"],
    allowUnverified: args.flags.has("--allow-unverified-catalog"),
    reportLanguage: v["--report-language"],
  });
}

/** Python's `agentce quickstart --help`, word for word. */
const QUICKSTART_USAGE = `usage: agentce quickstart [-h] [--json] [--debug] [--quiet] [--out OUT]

options:
  -h, --help  show this help message and exit
  --out OUT   the output directory for the report (default: ./out)

global options:
  --json      emit machine-readable JSON on stdout
  --debug     verbose logs on stderr and a stack trace on unexpected errors
  --quiet     log warnings and errors only
`;

/** Assess the bundled quickstart project end to end — one command, offline (SPEC §13.4 AX-1). */
function cmdQuickstart(args: Scan): CommandResult {
  const result = new CommandResult("quickstart");
  refuseEmptyOut(args.values["--out"]);
  const out = args.values["--out"] ?? DEFAULT_OUT_DIR;
  const quickstart = quickstartDir();
  if (!statSync(quickstart, { throwIfNoEntry: false })?.isDirectory()) {
    throw new InputError(
      "input.quickstart_missing",
      `the quickstart project is missing at ${quickstart}.`,
      "reinstall the engine: the quickstart project ships inside the package.",
    );
  }
  const catalogDir = joinPath(bundledCatalogsDir(), "base", "eu-ai-act");
  const assess = runAssess({
    bundle: joinPath(quickstart, "evidence"),
    profile: joinPath(quickstart, "applicability.yaml"),
    domain: joinPath(quickstart, "domain.linkml.yaml"),
    catalog: "eu-ai-act@2026.09",
    catalogDirs: [catalogDir],
    out,
    invocationCommand: "quickstart",
    allowUnverified: false,
  });
  Object.assign(result.data, assess.data);
  result.data.quickstart = "ok";
  for (const code of assess.codes) {
    result.addCode(code);
  }
  for (const line of assess.humanLines) {
    result.note(line);
  }
  result.note(`quickstart complete: ${assess.data.assertions ?? 0} assertions; report in ${out}`);
  return result;
}

/** Python's `agentce report --help` (COLUMNS=80), word for word. */
const REPORT_USAGE = `usage: agentce report [-h] [--json] [--debug] [--quiet] [--from FILE]
                      [--format {md,html,oscal,sarif,public,pack}]
                      [--role {provider,deployer}] [--catalog LABELS]
                      [--language LANG] [--out FILE] [--validate DIR]

options:
  -h, --help            show this help message and exit
  --from FILE           an assertions.json to re-render
  --format {md,html,oscal,sarif,public,pack}
                        the output format (default: md)
  --role {provider,deployer}
                        evidence-pack role variant; --format pack only
  --catalog LABELS      catalog labels for the public statement, comma-
                        separated; --format public only
  --language LANG       message-key catalogue: de or en (default: en);
                        --format md or html only (SPEC 9.3)
  --out FILE            write the rendering to this file
  --validate DIR        validate every artifact in a report directory; takes
                        no other report option

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
`;

/** Python's `commands.report_format_error`: a format report does not render. */
function reportFormatError(format: string): InputError {
  return new InputError(
    "input.report_format",
    `unknown report format ${pyRepr(format)}.`,
    `choose one of: ${REPORT_FORMATS.join(", ")}.`,
  );
}

/** Python's `commands.report_role_error`: an evidence-pack role report does not know. */
function reportRoleError(role: string): InputError {
  return new InputError(
    "input.report_role",
    `unknown evidence-pack role ${pyRepr(role)}.`,
    `choose one of: ${REPORT_ROLES.join(", ")}.`,
  );
}

/** The error Python's `commands.refuse_unknown_report_language` raises: `flag` names a report
 * language with no catalogue (assess's `--report-language`, report's `--language`). */
function reportLanguageError(flag: string, language: string): InputError {
  return new InputError(
    "input.report_language_unknown",
    `${flag} ${pyRepr(language)} has no report catalogue.`,
    `choose one of: ${availableLanguages().join(", ")}.`,
  );
}

/** The key `report --validate` answers with when a report folder fails validation. */
const REPORT_INVALID = "report.validation_failed";

/** The rendering options `report --validate` never reads, in the order the refusal names the first:
 * every value option of the grammar but --validate itself. */
const REPORT_RENDER_FLAGS = [...new Set(argvCommand("report").level.options.values())]
  .filter((opt) => opt.kind === "value" && opt.name !== "--validate")
  .map((opt) => opt.name);

/** Each rendering option a format may not read: the formats that read it and the fix's first step. */
const REPORT_FORMAT_ONLY: readonly (readonly [string, readonly string[], string])[] = [
  ["--role", ["pack"], "add --format pack"],
  ["--catalog", ["public"], "add --format public"],
  ["--language", ["md", "html"], "add --format md or --format html"],
];

/** Whether `text` is empty once Python's `str.strip()` has run (JavaScript's `\s` adds U+FEFF and
 * lacks U+001C-U+001F and U+0085). */
const isBlank = (text: string): boolean =>
  [...text].every((ch) => isPySpace(ch.codePointAt(0) as number));

/** Re-render a report from a committed `assertions.json` (SPEC §9.4), or, with `--validate`, schema-
 * validate every artifact in a report directory at parity with the Python reference (item 18.27).
 * Read from its grammar's scan alone; every option is either read or refused with a key, in
 * Python's `cmd_report` order (18.110). */
function cmdReport(args: Scan): CommandResult {
  const result = new CommandResult("report");
  const v = args.values;
  const validate = v["--validate"];
  if (validate !== undefined) {
    const unused = REPORT_RENDER_FLAGS.find((flag) => v[flag] !== undefined);
    if (unused !== undefined) {
      throw new InputError(
        "input.report_flag_unused",
        `--validate checks a report directory and does not read ${unused}.`,
        `drop ${unused}, or drop --validate to re-render with --from.`,
      );
    }
    const reportDir = requireDir(validate, "validate", "the report directory");
    const problems = validateReport(reportDir);
    result.data.report_dir = reportDir;
    result.data.valid = problems.length === 0;
    result.data.problems = problems;
    if (problems.length > 0) {
      // A refusal names its key and a fix, in text and in --json, as an error does (18.167).
      result.data.message_key = REPORT_INVALID;
      result.addCode(ExitCode.INPUT_ERROR);
      result.note(
        `error [${REPORT_INVALID}]: ${reportDir}: ${problems.length} artifact(s) failed validation`,
      );
      for (const problem of problems) {
        result.note(`  ${problem}`);
      }
      result.note(`  fix: ${errorFix(REPORT_INVALID)}`);
    } else {
      result.note(`${reportDir}: all artifacts valid`);
    }
    return result;
  }
  refuseEmpty(
    v["--out"],
    "input.out_file_unwritable",
    "--out",
    "pass --out <file> with a writable path.",
  );
  if (v["--out"] !== undefined) {
    checkOutput(v["--out"], REPORT_OUT);
  }
  const source = requireFile(v["--from"], "from", "the assertions file");
  const format = v["--format"] || "md";
  for (const [flag, reads, add] of REPORT_FORMAT_ONLY) {
    if (v[flag] !== undefined && !reads.includes(format)) {
      throw new InputError(
        "input.report_flag_unused",
        `${flag} is read only with --format ${reads.join(" or ")}; this run renders ${format}.`,
        `${add}, or drop ${flag}.`,
      );
    }
  }
  const languageFlag = v["--language"];
  if (languageFlag !== undefined && !availableLanguages().includes(languageFlag)) {
    throw reportLanguageError("--language", languageFlag);
  }
  const catalogFlag = v["--catalog"];
  let catalogs: string[] | undefined;
  if (catalogFlag !== undefined) {
    catalogs = catalogFlag.split(",");
    if (catalogs.some(isBlank)) {
      throw new InputError(
        "input.report_catalog_label_empty",
        `--catalog ${pyRepr(catalogFlag)} holds an empty catalog label.`,
        "pass the labels comma-separated with none empty, e.g. --catalog eu-ai-act,nist-ai-rmf.",
      );
    }
  }
  const loaded = readInputJson(source, "from", "the assertions file");
  requireIntegerFields(source, loaded);
  const parsed = asParsed(loaded);
  const assertions: Assertion[] = Array.isArray(parsed) ? parsed.map(assertionFromJson) : [];
  const counts = aggregate(assertions);
  const language = languageFlag || DEFAULT_LANGUAGE;
  // json.dumps(..., sort_keys=True, indent=2), as Python renders oscal, sarif and pack.
  const pyJson = (value: unknown): string => jsonStringifyAscii(sortKeysDeep(value), 2);
  let rendering: string;
  if (format === "md") {
    rendering = renderReportMd(assertions, counts, language);
  } else if (format === "html") {
    rendering = renderReportHtml(assertions, counts, language);
  } else if (format === "oscal") {
    rendering = pyJson(renderOscal(assertions));
  } else if (format === "sarif") {
    rendering = pyJson(renderSarif(assertions));
  } else if (format === "public") {
    rendering = renderPublicStatement(assertions, catalogs, appliedDeviationIds(assertions));
  } else {
    // pack
    const bySubject = new Map<string, Assertion[]>();
    for (const a of assertions) {
      const bucket = bySubject.get(a.subject);
      if (bucket === undefined) {
        bySubject.set(a.subject, [a]);
      } else {
        bucket.push(a);
      }
    }
    const packs: Record<string, unknown> = {};
    for (const subject of [...bySubject.keys()].sort(byteCompare)) {
      packs[subject] = renderEvidencePack(
        subject,
        bySubject.get(subject) as Assertion[],
        v["--role"],
      );
    }
    rendering = pyJson(packs);
  }
  result.data.from = source;
  result.data.format = format;
  result.data.rendering = rendering;
  const out = v["--out"];
  if (out !== undefined) {
    writeOutput(out, REPORT_OUT, rendering);
    result.data.out = out;
  }
  result.note(rendering);
  return result;
}

/** Python's `agentce diff --help` (COLUMNS=80), word for word. */
const DIFF_USAGE = `usage: agentce diff [-h] [--json] [--debug] [--quiet] [--format FORMAT]
                    [report_a] [report_b]

positional arguments:
  report_a         the first assertions.json
  report_b         the second assertions.json

options:
  -h, --help       show this help message and exit
  --format FORMAT  how to render the diff (default: text)

global options:
  --json           emit machine-readable JSON on stdout
  --debug          verbose logs on stderr and a stack trace on unexpected
                   errors
  --quiet          log warnings and errors only
`;

/** `agentce diff` (SPEC §9.3, item 18.6): a real, deterministic assertion-set diff, matching the
 * Python reference's `cmd_diff` byte for byte (see `diff.ts`). Read from its grammar's scan alone. */
function cmdDiff(args: Scan, json: boolean): CommandResult {
  const result = new CommandResult("diff");
  const positional = args.positionals;
  if (positional.length > 2) {
    throw new InputError(
      "input.diff_extra_argument",
      `diff takes exactly two positional arguments, got ${positional.length}.`,
      "pass exactly two files: `agentce diff <report-a> <report-b>`.",
    );
  }
  const fix = "pass two assertion files: `agentce diff <report-a> <report-b>`.";
  const reportA = requireFile(positional[0], "report_a", "the first assertion set", fix);
  const reportB = requireFile(positional[1], "report_b", "the second assertion set", fix);
  const format = args.values["--format"] ?? "text";
  if (!(DIFF_FORMATS as readonly string[]).includes(format)) {
    throw new InputError(
      "input.diff_format",
      `--format must be one of ${DIFF_FORMATS.join(", ")}, not ${pyRepr(format)}.`,
      "pass --format text|json|md.",
    );
  }
  result.data.report_a = normalizePosixPath(reportA);
  result.data.report_b = normalizePosixPath(reportB);
  const a = readInputJson(reportA, "report_a", "the first assertion set") as Record<
    string,
    unknown
  >[];
  const b = readInputJson(reportB, "report_b", "the second assertion set") as Record<
    string,
    unknown
  >[];
  const changes = diffAssertionSets(a, b);
  const grouped = whatChanged(changes);
  result.data.changed = changes.length;
  result.data.diff = changes;
  result.data.what_changed = grouped;
  if (changes.length > 0) {
    result.addCode(ExitCode.FINDINGS);
  }
  if (json) {
    // Never render a format only --json will discard (the same rule 18.22's C1(h) established for
    // `version`) -- mirrors Python's early return before any `result.note` call.
    return result;
  }
  if (format === "md") {
    for (const line of diffMdLines(grouped)) {
      result.note(line);
    }
  } else if (format === "json") {
    result.note(jsonStringifyAscii(sortKeysDeep(result.data), 2));
  } else if (changes.length > 0) {
    result.note(`${changes.length} assertion(s) differ:`);
    for (const c of changes) {
      result.note(`  ${diffChangeLine(c)}`);
    }
  } else {
    result.note("no differences");
  }
  return result;
}

/** Today's date in the **local** calendar, `YYYY-MM-DD` -- never `toISOString()`, which is UTC-based
 * and would name tomorrow's report file every evening west of UTC. Matches Python's
 * `date.today().isoformat()` (local-clock-based) and Java's `LocalDate.now()` (likewise local). */
function localIsoDate(): string {
  const d = new Date();
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Control severities from the catalogs the verdict reads, and the limitations the override records,
 * as Python's `_readiness_severities`: a `--catalog-dir` is verified against `trust` as assess
 * verifies it (SPEC §8.7), so a severity edited after signing never reaches the verdict; the bundled
 * catalogs are the engine's own (every vendored base catalog, deliberately not {@link
 * vendoredCatalogs}, which skips a malformed catalog and scans overlays). A later catalog's control
 * overwrites an earlier one sharing an id. `trust` is `undefined` only for `sign`, which takes no
 * `--catalog-dir`. */
function readinessSeverities(
  catalogDirs: string[],
  trust?: TrustRoot,
  allowUnverified = false,
): { severities: Map<string, string>; limitations: string[] } {
  if (allowUnverified && catalogDirs.length === 0) {
    throw new InputError(
      "input.allow_unverified_requires_catalog_dir",
      "--allow-unverified-catalog was given without --catalog-dir.",
      "add --catalog-dir <dir>, or drop --allow-unverified-catalog.",
    );
  }
  let dirs = catalogDirs;
  if (dirs.length === 0) {
    const base = joinPath(bundledCatalogsDir(), "base");
    let names: string[] = [];
    try {
      names = readdirSync(base).sort(byteCompare);
    } catch {
      names = [];
    }
    dirs = names
      .map((name) => joinPath(base, name))
      .filter((dir) => existsSync(joinPath(dir, "catalog.yaml")));
  }
  const catalogs = dirs.map((dir) =>
    loadCatalog(requireDir(dir, "catalog_dir", "a catalog directory", "pass --catalog-dir <dir>.")),
  );
  const limitations =
    catalogDirs.length > 0
      ? verifyCatalogDirs(catalogs, trust ?? effectiveTrustRoot(undefined), allowUnverified)
      : [];
  const severities = new Map<string, string>();
  for (const catalog of catalogs) {
    for (const control of catalog.controls) {
      severities.set(control.id, control.severity);
    }
  }
  return { severities, limitations };
}

/** Python's `agentce readiness --help` (COLUMNS=80), word for word. */
const READINESS_USAGE = `usage: agentce readiness [-h] [--json] [--debug] [--quiet] [--gaps GAPS]
                         [--deviations DEVIATIONS] [--catalog-dir CATALOG_DIR]
                         [--trust-root TRUST_ROOT]
                         [--allow-unverified-catalog]
                         [report_dir]

positional arguments:
  report_dir            the report directory

options:
  -h, --help            show this help message and exit
  --gaps GAPS           a gaps file listing accepted high-severity evidence
                        gaps
  --deviations DEVIATIONS
                        a deviation register to validate
  --catalog-dir CATALOG_DIR
                        a catalog directory whose control severities the
                        verdict reads (repeatable); verified as assess
                        verifies it
  --trust-root TRUST_ROOT
                        trust root every --catalog-dir signature is verified
                        against (default: AGENTCE_TRUST_ROOT, else the
                        vendored development root)
  --allow-unverified-catalog
                        read a --catalog-dir catalog whose signature is absent
                        or does not verify, recording the override as a
                        limitation in the verdict (SPEC 8.7). Requires
                        --catalog-dir.

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
`;

/** `agentce readiness` (SPEC §13.3.4 stage 4, item 18.25): the report-readiness verdict, matching the
 * Python reference's `cmd_readiness` byte for byte (see `readiness.ts`). Exit 0 for READY and READY
 * WITH LIMITATIONS, 1 for NOT READY -- the verdict logic lives in the engine, never in a skill. */
function cmdReadiness(scan: Scan): CommandResult {
  const result = new CommandResult("readiness");
  const args = {
    gaps: scan.values["--gaps"],
    deviations: scan.values["--deviations"],
    catalogDirs: scan.lists["--catalog-dir"] ?? [],
    trustRoot: scan.values["--trust-root"],
    allowUnverified: scan.flags.has("--allow-unverified-catalog"),
  };
  const reportDir = requireDir(
    scan.positionals[0],
    "report_dir",
    "the report directory",
    "pass the report directory: `agentce readiness <report-dir>`.",
  );
  requireEnterable(reportDir, "input.report_dir_unreadable", "the report directory");
  let gaps = new Set<string>();
  if (args.gaps !== undefined) {
    const text = readFileSync(requireFile(args.gaps, "gaps", "the gaps file"), "utf-8");
    gaps = parseGapsFile(text);
  }
  let deviations: Record<string, unknown>[] = [];
  if (args.deviations !== undefined) {
    deviations = loadDeviationRegister(
      requireFile(args.deviations, "deviations", "the deviation register"),
    );
  }
  // The trust root is loaded whether or not --catalog-dir is given, as assess loads it: a
  // --trust-root that is empty, missing or not a trust root is refused, never ignored.
  const { severities, limitations } = readinessSeverities(
    args.catalogDirs,
    effectiveTrustRoot(args.trustRoot),
    args.allowUnverified,
  );
  const verdict = computeReadiness(reportDir, {
    severities,
    deviations,
    gaps,
    limitations,
  });
  result.data.verdict = verdict.verdict;
  result.data.reasons = verdict.reasons;
  result.data.limitations = verdict.limitations;
  const out = joinPath(reportDir, `report-readiness-${localIsoDate()}.md`);
  const lines = [`# Report readiness — ${verdict.verdict}`, ""];
  if (verdict.reasons.length > 0) {
    lines.push("## Blocking reasons", ...verdict.reasons.map((r) => `- ${r}`), "");
  }
  if (verdict.limitations.length > 0) {
    lines.push("## Limitations", ...verdict.limitations.map((l) => `- ${l}`), "");
  }
  let unwritten: string | undefined;
  try {
    writeOutput(out, READINESS_OUT, `${lines.join("\n")}\n`);
    result.data.report = out;
  } catch (err) {
    if (!(err instanceof InputError)) {
      throw err;
    }
    // Reading is all the verdict needs (a read-only report folder still gets one); the file is the
    // only thing lost, and the run says so under its own key.
    result.data.report = null;
    result.data.report_warning = err.key;
    unwritten = `warning [${err.key}]: ${err.cause} The verdict is not saved to a file.`;
  }
  if (verdict.verdict === NOT_READY) {
    result.addCode(ExitCode.FINDINGS);
  }
  result.note(
    `${verdict.verdict}: ${verdict.reasons.length} reason(s), ${verdict.limitations.length} limitation(s)`,
  );
  if (unwritten !== undefined) {
    result.note(unwritten);
  }
  return result;
}

const SIGN_ROLES = ["claimant", "assessor"] as const;
const SIGN_PROFILES = ["sigstore-public", "sigstore-private", "kms"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Python's `dict.get(key, default)` restricted to the one shape this call site needs: `obj` may be
 * any JSON value (a hostile `claim.json` need not carry a mapping at every level), and `default` is
 * substituted only when `obj` is itself a plain mapping missing `key`, never when `obj` is some other
 * type -- matching `claim.get("claimant", {}).get("org", "unset")`'s own attribute-style access only
 * making sense on an actual mapping. */
function pyGetField(obj: unknown, key: string, defaultValue: unknown): unknown {
  if (isRecord(obj)) {
    return key in obj ? obj[key] : defaultValue;
  }
  return defaultValue;
}

/** Resolves the operator's signing key for `agentce sign` (SPEC §9.1), matching `_sign_signer`
 * exactly: `kms` signs with an operator-held `--key`; the two keyless `sigstore-*` profiles always
 * refuse offline (this port never obtains a Fulcio certificate). */
function signSigner(keyPath: string | undefined, profile: string): Signer {
  if (profile === "kms") {
    if (keyPath === undefined) {
      throw new InputError(
        "sign.kms_key_missing",
        "the kms profile signs with an operator-held key.",
        "pass --key <ed25519-private-key.pem>.",
      );
    }
    return KmsSigner.load(requireFile(keyPath, "key", "the signing key"));
  }
  throw new InputError(
    "sign.keyless_offline",
    `the ${profile} profile is keyless and obtains a certificate from a Fulcio instance (network); the engine does not sign it offline.`,
    "use --profile kms --key <file> offline, or run keyless signing where the Fulcio and Rekor endpoints are reachable.",
  );
}

/** Every path `sign` writes resolves inside the report folder: a claim.json, signatures/ or
 * trust-root.json that links out of it is refused before anything is written (ruling R7); matches
 * `_refuse_sign_targets_outside`. */
function refuseSignTargetsOutside(
  reportDir: string,
  role: string,
  profile: string,
  writeTrustRoot: boolean,
): void {
  const names = ["claim.json", `signatures/${role}-${profile}.dsse.json`];
  if (writeTrustRoot) {
    names.push("trust-root.json");
  }
  for (const name of names) {
    if (confineToRoot(reportDir, name) === null) {
      throw new InputError(
        "sign.claim_outside_report",
        `${name} links outside the report directory.`,
        "replace the link with the file itself, inside the report directory.",
      );
    }
  }
}

/** manifest.json's bytes, which the signature covers: absent, not a regular file or unreadable (mode
 * 000, behind a folder that cannot be entered) is refused, never signed without; a manifest.json that
 * links outside the report directory is refused too, so the signature covers a file the folder holds
 * (by analogy with ruling R7); matches `_read_sign_manifest`. */
function readSignManifest(reportDir: string): Buffer {
  const manifestPath = joinPath(reportDir, "manifest.json");
  let regular = false;
  try {
    regular = statSync(manifestPath).isFile();
  } catch {
    // fall through to the refusal
  }
  if (regular && confineToRoot(reportDir, "manifest.json") === null) {
    throw new InputError(
      "sign.claim_outside_report",
      "manifest.json links outside the report directory.",
      "replace the link with the file itself, inside the report directory.",
    );
  }
  try {
    if (regular) {
      return readFileSync(manifestPath);
    }
  } catch {
    // fall through to the refusal
  }
  throw new InputError(
    "sign.no_manifest",
    "the report directory has no readable manifest.json, which the signature covers.",
    "produce the report again: `agentce assess … --out <report-dir>`.",
  );
}

/** claim.json as `sign` appends to it, as Python's `_read_sign_claim`: an object whose `signatures`,
 * when present, is a list. Read through verify's own strict reader, with verify's wording. */
function readSignClaim(claimPath: string): Record<string, unknown> {
  const fix =
    "re-run the `agentce assess` command that wrote this report, then sign the new report.";
  let claim: unknown;
  try {
    claim = parseUntrustedJson(readFileSync(claimPath));
  } catch (exc) {
    throw new InputError("sign.claim_malformed", notValid("claim.json", exc), fix);
  }
  if (!isRecord(claim)) {
    throw new InputError("sign.claim_malformed", "claim.json is not an object.", fix);
  }
  if ("signatures" in claim && !Array.isArray(claim.signatures)) {
    throw new InputError(
      "sign.claim_malformed",
      "claim.json's signatures field is not a list.",
      fix,
    );
  }
  try {
    // The signature covers the canonical claim body, so a float is refused here (SPEC 8.7).
    canonicalString(Object.fromEntries(Object.entries(claim).filter(([k]) => k !== "signatures")));
  } catch (exc) {
    if (!(exc instanceof CanonicalizationError)) {
      throw exc;
    }
    throw new InputError(
      "sign.claim_malformed",
      `claim.json cannot be put in canonical form: ${exc.message}.`,
      fix,
    );
  }
  return claim;
}

/** Python's `agentce sign --help` (COLUMNS=80), word for word. */
const SIGN_USAGE = `usage: agentce sign [-h] [--json] [--debug] [--quiet]
                    [--as {claimant,assessor}]
                    [--profile {sigstore-public,sigstore-private,kms}]
                    [--key KEY] [--dry-run] [--write-trust-root]
                    [report_dir]

positional arguments:
  report_dir            the report directory

options:
  -h, --help            show this help message and exit
  --as {claimant,assessor}
                        the signing role
  --profile {sigstore-public,sigstore-private,kms}
                        the signing profile
  --key KEY             operator Ed25519 private key (PEM) for the kms profile
  --dry-run             plan only; sign nothing
  --write-trust-root    write trust-root.json (the signer's public key) into
                        the report directory, so a recipient can \`agentce
                        verify --report\` this bundle without any other key
                        exchange. Requires --profile kms (the only profile
                        with an exportable key).

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
`;

/** `agentce sign` (SPEC §8.7, §9.1, item 18.26): the Ed25519/DSSE/in-toto signing flow, `kms` profile,
 * matching the Python reference's `cmd_sign` byte for byte (see `sign.ts`). Refuses to sign unless the
 * report's readiness verdict is not `NOT READY` (`agentce readiness`'s own gate, computed the same
 * way). */
function cmdSign(scan: Scan): CommandResult {
  const result = new CommandResult("sign");
  const args = {
    asRole: scan.values["--as"],
    profile: scan.values["--profile"],
    key: scan.values["--key"],
    dryRun: scan.flags.has("--dry-run"),
    writeTrustRoot: scan.flags.has("--write-trust-root"),
  };
  const reportDir = requireDir(
    scan.positionals[0],
    "report_dir",
    "the report directory",
    "pass the report directory: `agentce sign <report-dir> --as claimant|assessor`.",
  );
  const role = args.asRole;
  if (role !== "claimant" && role !== "assessor") {
    throw new InputError(
      "input.sign_role",
      "--as must be `claimant` or `assessor`.",
      "pass --as claimant|assessor.",
    );
  }
  const profile = args.profile || "sigstore-public";
  if (!(SIGN_PROFILES as readonly string[]).includes(profile)) {
    throw new InputError(
      "input.sign_profile",
      `unknown signing profile ${pyRepr(profile)}.`,
      `choose one of: ${SIGN_PROFILES.join(", ")}.`,
    );
  }
  const writeTrustRoot = args.writeTrustRoot;
  if (writeTrustRoot && profile !== "kms") {
    throw new InputError(
      "sign.trust_root_requires_kms",
      `--write-trust-root needs an exportable public key; the ${pyRepr(profile)} profile has none.`,
      "pass --profile kms --key <ed25519-private-key.pem> --write-trust-root.",
    );
  }
  const dryRun = args.dryRun;
  if (!dryRun) {
    checkOutput(reportDir, SIGN_REPORT_DIR);
  }
  result.data.report_dir = reportDir;
  result.data.as = role;
  result.data.profile = profile;
  result.data.dry_run = dryRun;

  // The engine refuses to sign a report that is not ready to publish (SPEC §8.5); `sign` never takes
  // its own `--catalog-dir`/`--gaps`/`--deviations` flags, so this always uses the bundled catalogs
  // with no deviations/gaps.
  const verdict = computeReadiness(reportDir, {
    severities: readinessSeverities([]).severities,
  });
  result.data.readiness = verdict.verdict;
  if (verdict.verdict === NOT_READY) {
    throw new InputError(
      "sign.not_ready",
      `the report is ${verdict.verdict}: ${verdict.reasons.join("; ")}.`,
      "resolve the blocking reasons (agentce readiness <report-dir>) before signing.",
    );
  }

  // The claim is read and checked before the dry-run return, so a dry run answers as the real sign
  // would, and before the key is read (TRADEOFFS 2026-10-06 18.53).
  const claimPath = joinPath(reportDir, "claim.json");
  if (!existsSync(claimPath) || !statSync(claimPath).isFile()) {
    throw new InputError(
      "sign.no_claim",
      "the report directory has no claim.json to sign.",
      "produce the report first: `agentce assess … --out <report-dir>`.",
    );
  }
  refuseSignTargetsOutside(reportDir, role, profile, writeTrustRoot);
  const claim = readSignClaim(claimPath);
  const manifest = readSignManifest(reportDir);

  if (dryRun) {
    result.note(`dry run: would sign the claim as ${role} (${profile})`);
    return result;
  }

  // Every file sign writes is checked before claim.json is rewritten, so a refused run writes nothing.
  const sigDir = joinPath(reportDir, "signatures");
  const detached = joinPath(sigDir, `${role}-${profile}.dsse.json`);
  const trustRootPath = joinPath(reportDir, "trust-root.json");
  checkOutput(sigDir, SIGN_REPORT_DIR);
  for (const target of [claimPath, detached, ...(writeTrustRoot ? [trustRootPath] : [])]) {
    checkOutput(target, SIGN_REPORT_FILE);
  }
  const signer = signSigner(args.key, profile);
  const subjects = signSubjects(claim, manifest);
  const statement = {
    _type: INTOTO_STATEMENT_TYPE,
    subject: subjects,
    predicateType: "https://agent-conformance.org/attestation/claim/v1",
    predicate: {
      role,
      profile,
      statement:
        "This report states conformance to the named catalogs as evaluated by the named engine " +
        "over the named evidence. It is not a legal compliance determination.",
    },
  };
  const envelope = signStatement(statement, signer);
  const record = { role, profile, ...envelope };
  const signatures = (claim.signatures as unknown[] | undefined) ?? [];
  signatures.push(record);
  claim.signatures = signatures;
  writeOutput(claimPath, SIGN_REPORT_FILE, `${jsonStringifyAscii(sortKeysDeep(claim), 2)}\n`);
  writeOutput(detached, SIGN_REPORT_FILE, `${jsonStringifyAscii(sortKeysDeep(record), 2)}\n`);
  result.data.signature = detached;
  result.data.keyid = signer.keyid;
  result.data.signatures = signatures.length;

  if (writeTrustRoot) {
    // A KmsSigner is the only signer reachable here: `writeTrustRoot` requires `profile === "kms"`
    // (checked above), and `signSigner` only ever returns a KmsSigner for that profile -- asserted at
    // runtime, matching Python's own `assert isinstance(signer, signing.KmsSigner)` at this same call
    // site (`commands/__init__.py:2549`), not just a compile-time cast.
    if (!(signer instanceof KmsSigner)) {
      throw new Error("internal: --write-trust-root reached with a non-KmsSigner");
    }
    const kmsSigner = signer;
    const claimant = pyGetField(claim, "claimant", {});
    const org = pyGetField(claimant, "org", "unset");
    const document = {
      keys: {
        [kmsSigner.keyid]: {
          public_key: kmsSigner.publicKeyB64,
          identity: org,
        },
      },
    };
    writeOutput(
      trustRootPath,
      SIGN_REPORT_FILE,
      `${jsonStringifyAscii(sortKeysDeep(document), 2)}\n`,
    );
    result.data.trust_root = trustRootPath;
  }
  result.note(`signed ${basename(claimPath)} as ${role} (${profile})`);
  return result;
}

/** Python's `agentce verify --help` (COLUMNS=80), word for word. */
const VERIFY_USAGE = `usage: agentce verify [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                      [--catalog CATALOG] [--release RELEASE]
                      [--report REPORT]
                      [--signer-trust-root SIGNER_TRUST_ROOT]
                      [--expect-keyid EXPECT_KEYID]

Integrity or signature verification. \`--report <dir>\` re-runs a shareable
report bundle (\`assess --package-for-sharing\`) offline through nine stages, in
order, each with its own message key: (1) the claim exists and is signed
(verify.report_no_claim, verify.report_claim_malformed,
verify.report_unsigned); (2) the trust root resolves
(verify.report_no_trust_root, input.trust_root_invalid,
verify.report_keyid_mismatch); (3) a claimant signature verifies
(verify.report_signature_invalid); (4) the signed subjects match what's on
disk (verify.report_subject_missing, verify.report_manifest_tampered,
verify.report_claim_tampered); (5) the engine build matches
(verify.report_engine_mismatch); (6) every manifest-tracked output's digest
matches (verify.report_output_tampered); (7) the packaged evidence, profile,
domain binding, and catalogs match (verify.report_evidence_tampered); (8) an
offline re-run reproduces every canonical output byte for byte
(verify.report_reproduction_mismatch); (9) success (\`reproduced: true\`).

options:
  -h, --help            show this help message and exit
  --bundle BUNDLE       verify an evidence bundle's integrity
  --catalog CATALOG     verify a catalog's signatures
  --release RELEASE     verify a release artifact's signatures
  --report REPORT       re-run a shareable report bundle (assess --package-
                        for-sharing) offline and check it reproduces byte for
                        byte, refusing any tampering
  --signer-trust-root SIGNER_TRUST_ROOT
                        verify --report's signature against this trust root
                        file, instead of an embedded trust-root.json inside
                        the report directory
  --expect-keyid EXPECT_KEYID
                        verify --report: refuse unless the claim signature's
                        keyid matches this value

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
`;

/** `agentce verify --bundle/--catalog/--release/--report`: offline DSSE/certificate verification
 * (SPEC §8.7, §9.1). Ports `cmd_verify`'s own input validation verbatim (`commands/__init__.py:254-
 * 282`); `--report` (the 9-stage offline reproduction) is out of scope for this port (18.26's own
 * disposition) and falls to {@link notImplemented} once validation passes. */
function cmdVerify(args: Scan): CommandResult {
  const result = new CommandResult("verify");
  // An empty value (`--catalog ""`) is treated as not provided (F4, 18.65): otherwise it would fall
  // through to `requireDir`, which refuses it under a different key than the other two engines. A
  // repeated target never reaches here: the command-line grammar refuses it (SPEC 8.5).
  const bundle = emptyToUndefined(args.values["--bundle"]);
  const catalog = emptyToUndefined(args.values["--catalog"]);
  const release = emptyToUndefined(args.values["--release"]);
  const report = emptyToUndefined(args.values["--report"]);
  const chosenCount = [bundle, catalog, release, report].filter((v) => v !== undefined).length;
  if (chosenCount !== 1) {
    throw new InputError(
      "input.verify_target",
      "verify needs exactly one of --bundle, --catalog, --release, or --report.",
      "pass exactly one target, e.g. `agentce verify --bundle <dir>`.",
    );
  }
  const signerTrustRoot = args.values["--signer-trust-root"];
  const expectKeyid = args.values["--expect-keyid"];
  if (report === undefined && (signerTrustRoot !== undefined || expectKeyid !== undefined)) {
    throw new InputError(
      "input.verify_target",
      "--signer-trust-root/--expect-keyid apply only to --report.",
      "pass --report <dir> together with --signer-trust-root/--expect-keyid, or drop them.",
    );
  }

  if (bundle !== undefined) {
    const bundleDir = requireDir(bundle, "bundle", "the evidence bundle");
    const loaded = loadBundle(bundleDir);
    const ingested = ingest(loaded);
    const results = verifyBundle(
      ingested.rawAccepted,
      loaded.manifest,
      loaded.root,
      sourceTrust(undefined, ingested.rawAccepted),
    );
    const clean = new Set(["verified", "verified_weak"]);
    const broken = results.filter((r) => !clean.has(r.status));
    result.data.bundle = bundleDir;
    result.data.streams = results.map(integrityResultToJson);
    result.data.stream_count = results.length;
    result.data.broken_streams = broken.length;
    result.note(`verified ${bundleDir}: ${results.length} streams, ${broken.length} broken`);
    if (broken.length > 0) {
      result.addCode(ExitCode.FINDINGS);
    }
    return result;
  }

  if (catalog !== undefined) {
    const catalogDir = requireDir(catalog, "catalog", "the catalog directory");
    const outcome = verifyCatalog(catalogDir, vendoredTrust());
    result.data.catalog = catalogDir;
    Object.assign(result.data, outcome);
    if (outcome.verified) {
      result.note(`verified catalog ${basename(catalogDir)}: signer ${outcome.signer}`);
    } else {
      result.note(`catalog ${basename(catalogDir)}: verification failed`);
      result.addCode(ExitCode.INPUT_ERROR);
    }
    return result;
  }

  if (release !== undefined) {
    if (!existsSync(release)) {
      throw new InputError(
        "input.release_missing",
        `the release artifact ${pyRepr(release)} does not exist.`,
        "pass --release <bundle-dir-or-envelope>.",
      );
    }
    const outcome = verifyRelease(release, vendoredTrust());
    Object.assign(result.data, outcome);
    if (outcome.verified) {
      if ("signer" in outcome) {
        result.note(`verified ${basename(release)}: signer ${outcome.signer}`);
      } else {
        result.note(`verified release ${basename(release)}: ${outcome.signers.length} signatures`);
      }
    } else {
      result.note(`release ${basename(release)}: verification failed`);
      result.addCode(ExitCode.INPUT_ERROR);
    }
    return result;
  }

  // `report` is the only remaining target (exactly-one was already enforced above).
  return notImplemented("verify");
}

function notImplemented(command: string): CommandResult {
  const result = new CommandResult(command);
  result.addCode(ExitCode.INPUT_ERROR);
  result.data.error = {
    message_key: "cli.not_implemented",
    detail: "this command is not yet implemented in the TypeScript engine",
    command: command || null,
  };
  result.note("agentce (TypeScript engine) — command not yet implemented.");
  return result;
}

/** Python's `agentce version --help` (COLUMNS=80), word for word. */
const VERSION_USAGE = `usage: agentce version [-h] [--json] [--debug] [--quiet]

options:
  -h, --help  show this help message and exit

global options:
  --json      emit machine-readable JSON on stdout
  --debug     verbose logs on stderr and a stack trace on unexpected errors
  --quiet     log warnings and errors only
`;

/** `agentce version` (SPEC §8.5): the same structured envelope every other command returns, with a
 * real installed-artifact `no_ml` self-report (mirrors Python's `cmd_version`). Distinct from the
 * bare `--version`/`-V` flag, which stays a plain one-line shortcut (handled before this is ever
 * reached, `main` below). */
function cmdVersion(): CommandResult {
  const result = new CommandResult("version");
  const scan = installedNoMl();
  result.data.engine = ENGINE_NAME;
  result.data.engine_version = engineVersion();
  result.data.spec_version = SPEC_VERSION;
  result.data.supported_catalogs = [];
  result.data.no_ml = scan.result;
  result.data.no_ml_detail = scan;
  result.note(`${ENGINE_NAME} ${engineVersion()} (spec ${SPEC_VERSION})`);
  result.note(`no_ml: ${scan.result}`);
  if (scan.result !== "pass") {
    // A learned component is present: an input/environment error for a model-free engine.
    result.addCode(ExitCode.INPUT_ERROR);
  }
  return result;
}

function errorResult(command: string, error: AgentceError): CommandResult {
  const result = new CommandResult(command);
  result.addCode(error.exitCode);
  result.data.error = {
    message_key: error.key,
    detail: error.cause,
    fix: error.fix,
  };
  result.note(`${error.key}: ${error.cause}`);
  if (error.fix) {
    result.note(`fix: ${error.fix}`);
  }
  return result;
}

/** Python's `agentce --help` (COLUMNS=80), word for word. */
const TOP_USAGE = `usage: agentce [-h] [--json] [--debug] [--quiet] [-V] <command> ...

Agent Conformance Engine — deterministic conformance evidence for AI agents.

positional arguments:
  <command>
    validate     schema-validate a bundle
    verify       integrity or signature verification
    assess       run a full assessment
    report       re-render a report, or validate one
    collect      run a scheduled collection job over sources with a local
                 export, or plan one
    ingest       adapt a real adapter export into an evidence bundle
    catalog      catalog tools
    conformance  engine conformance suite
    diff         deterministic diff of two assertion sets
    sign         sign a report as claimant or assessor
    readiness    compute the report-readiness verdict (SPEC 13.3.4)
    doctor       diagnose a project and name the exact fix (SPEC 13.4)
    quickstart   assess the bundled quickstart project
    init         write a starter applicability profile
    config       show engine configuration
    version      print engine, spec, and no_ml information

options:
  -h, --help     show this help message and exit
  -V, --version  show program's version number and exit

global options:
  --json         emit machine-readable JSON on stdout
  --debug        verbose logs on stderr and a stack trace on unexpected errors
  --quiet        log warnings and errors only
`;

/** Each command's usage (`-h`/`--help`), and each action's under `<command> <action>`. */
const USAGES: Readonly<Record<string, string>> = {
  validate: VALIDATE_USAGE,
  verify: VERIFY_USAGE,
  assess: ASSESS_USAGE,
  report: REPORT_USAGE,
  conformance: CONFORMANCE_USAGE,
  "conformance run": CONFORMANCE_RUN_USAGE,
  diff: DIFF_USAGE,
  sign: SIGN_USAGE,
  readiness: READINESS_USAGE,
  quickstart: QUICKSTART_USAGE,
  version: VERSION_USAGE,
};

const TOP_FIX =
  "name a command: `agentce <command>`; run `agentce --help` for the commands agentce takes.";
const TOP_FLAG_FIX = "run `agentce --help` for the commands and global flags agentce takes.";
const TOP_COMMAND_FIX = "run `agentce --help` for the commands agentce takes.";
const GLOBAL_FLAGS: readonly string[] = ["--json", "--debug", "--quiet"];

/** A top-level refusal: the command is `agentce`, every key input.unknown_command. */
function topRefusal(cause: string, fix = TOP_FIX): InputError {
  return new InputError("input.unknown_command", cause, fix);
}

type TopLevel =
  | { kind: "help" }
  | { kind: "version" }
  | { kind: "run"; pre: string[]; command: string; rest: string[] };

/** The tokens before the command, read as Python's `_scan_top_level` reads them (18.108): a short
 * cluster, a value on a flag that takes none, an unknown flag before the command, -V/--version with
 * anything else on the line, a `--` with nothing (or a dash token) after it, or global flags with no
 * command are refused; -h/--help win when reached. `pre` holds the global flags before the command, in
 * order; a `--` before the command is dropped. Every refusal is input.unknown_command. */
export function scanTopLevel(argv: readonly string[]): TopLevel {
  let line = [...argv];
  let unknown: string | undefined;
  let hasCommand = false;
  for (let i = 0; i < line.length; i++) {
    const token = line[i] as string;
    if (token === "--") {
      const next = line[i + 1];
      if (next === undefined) {
        throw topRefusal("no command given after '--'.");
      }
      if (next.startsWith("-")) {
        // No command starts with a dash.
        throw topRefusal(`unrecognized command '${next}'.`, TOP_COMMAND_FIX);
      }
      hasCommand = true;
      line = [...line.slice(0, i), ...line.slice(i + 1)];
      break;
    }
    if (!token.startsWith("-") || token === "-") {
      hasCommand = true;
      break;
    }
    const name = token.split("=", 1)[0] as string;
    if (!token.startsWith("--") && token.length > 2) {
      throw topRefusal(`unrecognized flag '${token}'.`, TOP_FLAG_FIX);
    }
    const help = HELP.includes(name);
    const version = name === "-V" || name === "--version";
    const known = help || version || GLOBAL_FLAGS.includes(name);
    if (known && token.includes("=")) {
      throw topRefusal(`flag '${name}' takes no value.`, `drop the value: ${name}.`);
    }
    if (help) {
      return { kind: "help" };
    }
    if (version) {
      if (line.length !== 1) {
        throw topRefusal(
          `'${name}' takes no other arguments.`,
          "run `agentce --version` alone, or `agentce version --json` for the JSON envelope.",
        );
      }
      return { kind: "version" };
    }
    if (!known) {
      unknown ??= token;
    }
  }
  if (unknown !== undefined) {
    throw topRefusal(`unrecognized flag '${unknown}'.`, TOP_FLAG_FIX);
  }
  if (!hasCommand) {
    if (line.length > 0) {
      throw topRefusal("no command given.");
    }
    return { kind: "help" };
  }
  const at = line.findIndex((token) => !token.startsWith("-") || token === "-");
  return {
    kind: "run",
    pre: line.slice(0, at),
    command: line[at] as string,
    rest: line.slice(at + 1),
  };
}

/** A refusal is the JSON envelope when `--json` comes before any `--` (SPEC 8.5). */
function wantsJson(argv: readonly string[]): boolean {
  const at = argv.indexOf("--");
  return (at < 0 ? argv : argv.slice(0, at)).includes("--json");
}

/** A value outside an option's `choices`, by command and option: the command's own keyed error. */
const CHOICE_ERRORS: Readonly<
  Record<string, Readonly<Record<string, (value: string) => InputError>>>
> = {
  report: { "--format": reportFormatError, "--role": reportRoleError },
};

/** The error for an action a command does not have. */
const ACTION_ERRORS: Readonly<Record<string, () => InputError>> = {
  conformance: conformanceActionError,
};

/** A value outside an option's `choices`: the command's own error, else (Python's `_parse` fallback)
 * argparse's invalid-choice sentence under the command's unrecognized-flag key. */
function choiceError(cmd: ArgvCommand): (option: string, value: string) => InputError {
  return (option, value) => {
    const own = CHOICE_ERRORS[cmd.name]?.[option];
    if (own !== undefined) {
      return own(value);
    }
    const allowed = (cmd.level.options.get(option)?.choices ?? []).map(pyRepr).join(", ");
    return new InputError(
      cmd.unrecognizedKey,
      `argument ${option}: invalid choice: ${pyRepr(value)} (choose from ${allowed}).`,
      cmd.flagFix,
    );
  };
}

/** What a line asks for, read under the SPEC 8.5 grammar: a usage or version text to print, a command
 * this engine has not built, or a command with its scan and the global flags given. */
type Parsed =
  | { kind: "print"; text: string }
  | { kind: "version" }
  | { kind: "not_implemented"; command: string }
  | { kind: "run"; command: string; scan: Scan; globals: Set<string> };

function parse(rawArgv: readonly string[]): Parsed {
  const top = scanTopLevel(rawArgv);
  if (top.kind === "help") {
    return { kind: "print", text: TOP_USAGE };
  }
  if (top.kind === "version") {
    return { kind: "version" };
  }
  const { pre, command: name } = top;
  pre.forEach((flag, i) => {
    if (pre.indexOf(flag) < i) {
      throw topRefusal(`flag '${flag}' was given more than once.`, `give ${flag} once.`);
    }
  });
  if (!isCommand(name)) {
    throw topRefusal(`unrecognized command '${name}'.`, TOP_COMMAND_FIX);
  }
  const cmd = argvCommand(name);
  if (!cmd.implementedIn.includes("typescript")) {
    return { kind: "not_implemented", command: name };
  }
  let scan: Scan;
  try {
    const actionError = ACTION_ERRORS[name];
    scan = scanArgv(cmd, top.rest, {
      seen: pre,
      choiceError: choiceError(cmd),
      ...(actionError === undefined ? {} : { actionError }),
    });
  } catch (exc) {
    throw exc instanceof InputError ? new CommandRefusal(name, exc) : exc;
  }
  if (scan.help) {
    const usage = USAGES[scan.action === undefined ? name : `${name} ${scan.action}`] as string;
    return { kind: "print", text: usage };
  }
  return {
    kind: "run",
    command: name,
    scan,
    globals: new Set([...pre, ...scan.flags]),
  };
}

/** A refusal of a command's own line, carrying the command for the envelope's `command`. */
class CommandRefusal extends InputError {
  readonly command: string;

  constructor(command: string, err: InputError) {
    super(err.key, err.cause, err.fix);
    this.command = command;
  }
}

function run(command: string, scan: Scan, json: boolean): CommandResult {
  switch (command) {
    case "conformance":
      return cmdConformance(scan);
    case "validate":
      return cmdValidate(scan);
    case "assess":
      return cmdAssess(scan);
    case "report":
      return cmdReport(scan);
    case "diff":
      return cmdDiff(scan, json);
    case "readiness":
      return cmdReadiness(scan);
    case "sign":
      return cmdSign(scan);
    case "verify":
      return cmdVerify(scan);
    case "quickstart":
      return cmdQuickstart(scan);
    default:
      return cmdVersion();
  }
}

export function main(rawArgv: string[]): number {
  let parsed: Parsed;
  try {
    parsed = parse(rawArgv);
  } catch (exc) {
    if (!(exc instanceof InputError)) {
      throw exc;
    }
    const refused = errorResult(exc instanceof CommandRefusal ? exc.command : "agentce", exc);
    emit(refused, wantsJson(rawArgv));
    return refused.exitCode;
  }
  if (parsed.kind === "print") {
    process.stdout.write(parsed.text);
    return 0;
  }
  if (parsed.kind === "version") {
    console.log(`agentce ${engineVersion()}`);
    return 0;
  }
  if (parsed.kind === "not_implemented") {
    const result = notImplemented(parsed.command);
    emit(result, wantsJson(rawArgv));
    return result.exitCode;
  }
  const { command, scan, globals } = parsed;
  const json = globals.has("--json");
  const debug = globals.has("--debug");
  if (debug) {
    logDebug(command, "command.start");
  }
  let result: CommandResult;
  let failed = false;
  try {
    result = run(command, scan, json);
  } catch (exc) {
    failed = true;
    if (exc instanceof AgentceError) {
      result = errorResult(command, exc);
    } else if (debug) {
      // Matches Python's own `--debug` behaviour (`cli.py:562-564`, `if debug: raise`): let the
      // exception propagate to the runtime's default handler (a full stack trace on stderr), rather
      // than swallowing it into the keyed envelope below.
      throw exc;
    } else {
      // A non-AgentceError exception here would otherwise crash the process at exit 1 -- colliding
      // with ExitCode.FINDINGS, so a malformed-input crash would be indistinguishable from a real
      // finding to a CI script gating on exit code. Matches Python's key, exit code, and (now that
      // `--debug` is real in this engine too) fix text -- not its full error envelope shape (a real,
      // pre-existing, engine-wide difference this does not close).
      const message = exc instanceof Error ? exc.message : String(exc);
      result = errorResult(
        command,
        new AgentceError(
          "internal.unexpected",
          message,
          "re-run with --debug to see the stack trace, then file an issue for an AgentCE maintainer to investigate.",
        ),
      );
    }
  }
  emit(result, json);
  if (debug && !failed) {
    logDebug(command, "command.end", { exit_code: result.exitCode });
  }
  return result.exitCode;
}

/** One of Python's `--debug` log records (`logsetup.JsonFormatter`): one sorted-key, compact JSON line
 * on stderr, its timestamp in Python's `isoformat()` shape. */
function logDebug(command: string, event: string, extra: Record<string, unknown> = {}): void {
  const ts = new Date().toISOString().replace("Z", "000+00:00");
  const record = {
    command,
    event,
    level: "debug",
    logger: "agentce",
    ts,
    ...extra,
  };
  process.stderr.write(`${jsonStringifyAscii(sortKeysDeep(record))}\n`);
}

if (require.main === module) {
  // exitCode, not exit(): a piped stdout drains before the process ends.
  process.exitCode = main(process.argv.slice(2));
}
