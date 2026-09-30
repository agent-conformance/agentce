/**
 * The `agentce` command-line interface (SPEC §8.5): the same verbs, `--json` envelope, and exit-code
 * scheme as the Python reference. `assess`, `validate`, `report`, and `quickstart` are implemented on
 * the existing ECS/report path (the same modules `conformance run` already exercises); a verb not yet
 * implemented returns a stable `input_error` envelope rather than a guess.
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join, relative, resolve as resolvePath, sep } from "node:path";
import { load } from "js-yaml";
import { type Activity, summarizeActivity } from "./activity";
import { resolve as resolveApplicability } from "./applicability";
import { type Assertion, aggregate, assertionFromJson, assertionToJson } from "./assertions";
import { assessSubjects, evaluatedNothing } from "./assess";
import { computeBlindSpots } from "./blindSpots";
import { loadBundle } from "./bundle";
import { catalogsDir as bundledCatalogsDir, quickstartDir } from "./bundled";
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
import { AgentceError, InputError } from "./errors";
import { ExitCode } from "./exitCodes";
import { buildGraph } from "./graph";
import { ingest } from "./ingest";
import { integrityResultToJson, verifyBundle } from "./integrity";
import { DEFAULT_LANGUAGE, catalogue } from "./messages";
import { evaluateInstalledNoMl, loadVendoredDenylist } from "./noMl";
import { computeVectorFile } from "./numerics";
import { type Profile, loadProfile } from "./profile";
import { writeQuarantine } from "./quarantine";
import { NOT_READY, computeReadiness, loadDeviationRegister, parseGapsFile } from "./readiness";
import {
  activityCliLines,
  blindSpotsCliLines,
  catalogProvenanceDigest,
  renderEvidencePack,
  renderOscal,
  renderReportHtml,
  renderReportMd,
  renderSarif,
  writeReport,
} from "./report";
import { validateReport } from "./reportValidate";
import { CommandResult } from "./result";
import { computeSecurityView } from "./securityView";
import { INTOTO_STATEMENT_TYPE, KmsSigner, type Signer, signStatement, signSubjects } from "./sign";
import { StateDir, windowEnd } from "./state";
import { GraphStore } from "./store";
import { byteCompare, jsonStringifyAscii, pyRepr, sortKeysDeep, writeJsonl } from "./util";
import { summarize } from "./verdict";
import { ENGINE_NAME, SPEC_VERSION, engineVersion } from "./version";

const DEFAULT_OUT_DIR = "out";
/** The catalog an assessment evaluates when nothing names one: the cross-standard baseline. */
const DEFAULT_LENS = "baseline@2026.09";
const REPORT_FORMATS = ["md", "html", "oscal", "sarif", "pack"] as const;

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

/** The value following the first `--name` in argv, or undefined. */
function flagValue(argv: string[], name: string): string | undefined {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && index + 1 < argv.length ? argv[index + 1] : undefined;
}

/** Every value following a (repeatable) `--name` in argv, in order. */
function flagValues(argv: string[], name: string): string[] {
  const out: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === `--${name}` && i + 1 < argv.length) {
      out.push(argv[i + 1] as string);
    }
  }
  return out;
}

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
  if (!isDir) {
    throw new InputError(
      `input.${key}_not_a_directory`,
      `${what} '${raw}' is not an existing directory.`,
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
  if (!isFile) {
    throw new InputError(
      `input.${key}_not_a_file`,
      `${what} '${raw}' is not an existing file.`,
      fix,
    );
  }
  return raw;
}

/** The global flags every command accepts, none of which takes a value. */
const GLOBAL_BOOLEAN_FLAGS = new Set(["--json", "--debug", "--quiet"]);

/** A hardened positional-argument scanner for `diff` (the first CLI verb in this engine with
 * positional, not `--flag`, arguments). `argv[0]` is the command name and is not itself scanned.
 * Skips exactly `--json`, `--format <value>`, `--debug`, and `--quiet` (the same four flags Python's
 * own top-level parser accepts for every command; `--debug`/`--quiet` are silently ignored here too,
 * exactly as they are everywhere else in both engines today) and collects every other token in order,
 * but throws `input.diff_unrecognized_flag` on any other `--`-prefixed token instead of silently
 * reading it as a positional (catches a genuine typo without inventing new behaviour for a flag
 * Python's diff actually accepts). `--format=<value>` (single-token, `=`-joined) is out of scope,
 * matching every other flag in both engines today. */
function positionalArgs(argv: string[]): string[] {
  const out: string[] = [];
  for (let i = 1; i < argv.length; i++) {
    const token = argv[i] as string;
    if (GLOBAL_BOOLEAN_FLAGS.has(token)) {
      continue;
    }
    if (token === "--format") {
      i++; // also skip the value token, if any
      continue;
    }
    if (token.startsWith("--")) {
      throw new InputError(
        "input.diff_unrecognized_flag",
        `unrecognized flag '${token}'.`,
        "pass --format text|json|md, or drop the flag.",
      );
    }
    out.push(token);
  }
  return out;
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
    const dir = join(root, kind);
    let names: string[] = [];
    try {
      names = readdirSync(dir).sort(byteCompare);
    } catch {
      names = [];
    }
    for (const name of names) {
      const catalogYaml = join(dir, name, "catalog.yaml");
      if (!existsSync(catalogYaml)) {
        continue;
      }
      try {
        const meta = load(readFileSync(catalogYaml, "utf-8")) as Record<string, unknown> | null;
        if (meta && typeof meta.id === "string" && typeof meta.version === "string") {
          found.set(`${meta.id}@${meta.version}`, join(dir, name));
        }
      } catch {
        // a malformed vendored catalog is skipped, never fatal to discovery
      }
    }
  }
  return found;
}

/**
 * The catalogs an assessment evaluates, and their `id@version` labels (mirrors the Python reference's
 * `_resolve_catalogs`). Each `--catalog-dir` is loaded as given. Each requested id — the `--catalog`
 * list, else the profile's declared `catalogs` when no directory was passed — must resolve to a
 * directory that was passed or to a vendored catalog. A run that passes no `--catalog` and no
 * `--catalog-dir`, whose profile declares no catalogs, evaluates the baseline (`DEFAULT_LENS`).
 */
function resolveCatalogs(
  requested: string | undefined,
  profile: Profile,
  catalogDirs: string[],
): { catalogs: Catalog[]; labels: string[] } {
  const loaded = catalogDirs.map((d) =>
    loadCatalog(requireDir(d, "catalog-dir", "the catalog directory")),
  );
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
  const labels = [...new Set([...ids, ...loaded.map((c) => `${c.id}@${c.version}`)])];
  return { catalogs: labels.map((l) => byLabel.get(l) as Catalog), labels };
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

function cmdConformance(argv: string[]): CommandResult {
  const result = new CommandResult("conformance");
  const action = argv[1];
  if (action !== "run") {
    throw new InputError(
      "input.conformance_action",
      "the only conformance action is `run`.",
      "run `agentce conformance run ...`.",
    );
  }
  const engine = requireDir(flagValue(argv, "engine"), "engine", "the engine path");
  const corpus = requireDir(flagValue(argv, "corpus"), "corpus", "the corpus directory");
  const out = flagValue(argv, "out") ?? null;
  const report = runEcs({ enginePath: engine, corpusDir: corpus, outDir: out });

  result.data.action = "run";
  result.data.engine = engine;
  result.data.corpus = corpus;
  if (out !== null) {
    result.data.out = out;
  }
  Object.assign(result.data, report); // report.engine (impl/version) overwrites the engine path, as in the reference
  result.note(
    `ECS: ${report.projects.identical}/${report.projects.total} identical; ` +
      `claim ${report.claim}; no_ml ${report.no_ml}`,
  );
  if (report.claim !== "full") {
    result.addCode(ExitCode.FINDINGS);
  }
  return result;
}

function cmdValidate(argv: string[]): CommandResult {
  const result = new CommandResult("validate");
  const bundleDir = requireDir(flagValue(argv, "bundle"), "bundle", "the evidence bundle");
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
  const out = flagValue(argv, "out");
  if (out !== undefined) {
    const quarantinePath = join(out, "quarantine.jsonl");
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
  bundle: string;
  profile: string;
  catalog?: string;
  domain?: string;
  catalogDirs: string[];
  out: string;
  state?: string;
  invocationCommand: string;
}

/** Run a full assessment (ingest, integrity, graph, coverage, applicability, evaluate, report) — the
 * same pipeline `conformance run` exercises per corpus project, generalised to an arbitrary bundle. */
function runAssess(options: AssessOptions): CommandResult {
  const result = new CommandResult("assess");
  const bundleDir = requireDir(options.bundle, "bundle", "the evidence bundle");
  const profilePath = requireFile(options.profile, "profile", "the applicability profile");
  const out = options.out;
  const profileObj = loadProfile(profilePath);
  const { catalogs, labels: catalogLabels } = resolveCatalogs(
    options.catalog,
    profileObj,
    options.catalogDirs,
  );

  // Stage 1: ingest and validate. A missing/mismatching manifest aborts with exit 3.
  const bundle = loadBundle(bundleDir);
  const ingested = ingest(bundle);
  writeQuarantine(ingested.quarantined, join(out, "quarantine.jsonl"));

  // Stage 2: integrity verification, one IntegrityResult per stream.
  const integrityResults = verifyBundle(ingested.accepted, bundle.manifest, bundle.root);
  writeJsonl(integrityResults.map(integrityResultToJson), join(out, "integrity.jsonl"));

  // Stage 3: build the provenance graph (in-memory store; ADR-0001's TypeScript variant).
  const domain =
    options.domain !== undefined
      ? DomainBinding.load(requireFile(options.domain, "domain", "the domain binding"))
      : DomainBinding.empty();
  const store = new GraphStore();
  buildGraph(ingested.accepted, { domain, store });
  const graphTriples = store.tripleCount();

  // Stage 4: coverage and reconciliation against independent denominators.
  const coverage = computeCoverage(ingested.accepted, profileObj, bundleDir);
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, "coverage.json"), `${JSON.stringify(sortKeysDeep(coverage), null, 2)}\n`);

  // Stage 5: applicability resolution and drift.
  const statements = resolveApplicability(profileObj, ingested.accepted);
  writeJsonl(statements, join(out, "applicability.jsonl"));
  const driftFindings = statements.reduce(
    (n, s) => n + ((s.drift as unknown[] | undefined)?.length ?? 0),
    0,
  );

  // Stage 6: catalog evaluation and report artifacts.
  const evaluated = assessSubjects(ingested.accepted, profileObj, catalogs, domain);

  // Stage 6a: incremental state (SPEC §5.4 B7, HR-10).
  const newWindowEnd = windowEnd(profileObj.observationWindow, ingested.accepted);
  let state: StateDir | null = null;
  let supersedes: string[] = [];
  if (options.state !== undefined) {
    state = StateDir.load(options.state); // incompatible state_version aborts with exit 3
    [supersedes] = state.plan(bundle.digest, ingested.accepted, newWindowEnd);
  }

  const activity = summarizeActivity(ingested.accepted, profileObj);
  const blindSpots = computeBlindSpots(evaluated, profileObj, catalogs, ingested.accepted);
  writeReport(out, evaluated, {
    bundleDigest: bundle.digest,
    catalogs: catalogLabels,
    catalogObjects: catalogs,
    operator: process.env.AGENTCE_OPERATOR ?? "unknown",
    invocation: [options.invocationCommand, scrubPath(bundleDir), scrubPath(profilePath)],
    supersedes,
    activity,
    blindSpots,
    events: ingested.accepted,
    profile: profileObj,
  });
  if (state !== null) {
    state.record(bundle.digest, join(out, "manifest.json"), newWindowEnd);
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
  if (options.state !== undefined) {
    result.data.supersedes = supersedes;
  }
  if (nonConformant > 0) {
    result.addCode(ExitCode.FINDINGS);
  }
  if (evaluatedNothing(evaluated)) {
    throw nothingEvaluated(profileObj, ingested.accepted, evaluated.length);
  }
  for (const line of activityCliLines(activity, catalogue(DEFAULT_LANGUAGE))) {
    result.note(line);
  }
  for (const line of blindSpotsCliLines(blindSpots)) {
    result.note(line);
  }
  result.note(`verdict: ${summary.verdict}`);
  result.note(
    `assessed ${evaluated.length} (control, subject) pairs; ${nonConformant} non-conformant`,
  );
  return result;
}

function cmdAssess(argv: string[]): CommandResult {
  return runAssess({
    bundle: flagValue(argv, "bundle") as string,
    profile: flagValue(argv, "profile") as string,
    catalog: flagValue(argv, "catalog"),
    domain: flagValue(argv, "domain"),
    catalogDirs: flagValues(argv, "catalog-dir"),
    out: flagValue(argv, "out") ?? DEFAULT_OUT_DIR,
    state: flagValue(argv, "state"),
    invocationCommand: "assess",
  });
}

/** Assess the bundled quickstart project end to end — one command, offline (SPEC §13.4 AX-1). */
function cmdQuickstart(argv: string[]): CommandResult {
  const result = new CommandResult("quickstart");
  const out = flagValue(argv, "out") ?? DEFAULT_OUT_DIR;
  const quickstart = quickstartDir();
  if (!statSync(quickstart, { throwIfNoEntry: false })?.isDirectory()) {
    throw new InputError(
      "input.quickstart_missing",
      `the quickstart project is missing at ${quickstart}.`,
      "reinstall the engine: the quickstart project ships inside the package.",
    );
  }
  const catalogDir = join(bundledCatalogsDir(), "base", "eu-ai-act");
  const assess = runAssess({
    bundle: join(quickstart, "evidence"),
    profile: join(quickstart, "applicability.yaml"),
    domain: join(quickstart, "domain.linkml.yaml"),
    catalog: "eu-ai-act@2026.09",
    catalogDirs: [catalogDir],
    out,
    invocationCommand: "quickstart",
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

/** Re-render a report from a committed `assertions.json` (SPEC §9.4), or, with `--validate`, schema-
 * validate every artifact in a report directory at parity with the Python reference (item 18.27); the
 * `public` format is not yet ported and is refused with a named, honest error rather than a silent
 * guess. */
function cmdReport(argv: string[]): CommandResult {
  const result = new CommandResult("report");
  if (argv.includes("--validate")) {
    const reportDir = requireDir(flagValue(argv, "validate"), "validate", "the report directory");
    const problems = validateReport(reportDir);
    result.data.report_dir = reportDir;
    result.data.valid = problems.length === 0;
    result.data.problems = problems;
    if (problems.length > 0) {
      result.addCode(ExitCode.INPUT_ERROR);
      result.note(`${reportDir}: ${problems.length} artifact(s) failed validation`);
    } else {
      result.note(`${reportDir}: all artifacts valid`);
    }
    return result;
  }
  const source = requireFile(flagValue(argv, "from"), "from", "the assertions file");
  const format = flagValue(argv, "format") ?? "md";
  if (!(REPORT_FORMATS as readonly string[]).includes(format)) {
    throw new InputError(
      "input.report_format",
      `unknown or not-yet-implemented report format '${format}'.`,
      `choose one of: ${REPORT_FORMATS.join(", ")}.`,
    );
  }
  const parsed = JSON.parse(readFileSync(source, "utf-8"));
  const assertions: Assertion[] = Array.isArray(parsed) ? parsed.map(assertionFromJson) : [];
  const counts = aggregate(assertions);
  const language = flagValue(argv, "language") ?? DEFAULT_LANGUAGE;
  let rendering: string;
  if (format === "md") {
    rendering = renderReportMd(assertions, counts, language);
  } else if (format === "html") {
    rendering = renderReportHtml(assertions, counts, language);
  } else if (format === "oscal") {
    rendering = `${JSON.stringify(sortKeysDeep(renderOscal(assertions)), null, 2)}\n`;
  } else if (format === "sarif") {
    rendering = `${JSON.stringify(sortKeysDeep(renderSarif(assertions)), null, 2)}\n`;
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
      packs[subject] = renderEvidencePack(subject, bySubject.get(subject) as Assertion[]);
    }
    rendering = `${JSON.stringify(sortKeysDeep(packs), null, 2)}\n`;
  }
  result.data.from = source;
  result.data.format = format;
  result.data.rendering = rendering;
  const out = flagValue(argv, "out");
  if (out !== undefined) {
    writeFileSync(out, rendering);
    result.data.out = out;
  }
  result.note(rendering);
  return result;
}

/** `agentce diff` (SPEC §9.3, item 18.6): a real, deterministic assertion-set diff, matching the
 * Python reference's `cmd_diff` byte for byte (see `diff.ts`). */
function cmdDiff(argv: string[]): CommandResult {
  const result = new CommandResult("diff");
  const positional = positionalArgs(argv);
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
  const format = flagValue(argv, "format") ?? "text";
  if (!(DIFF_FORMATS as readonly string[]).includes(format)) {
    throw new InputError(
      "input.diff_format",
      `--format must be one of ${DIFF_FORMATS.join(", ")}, not '${format}'.`,
      "pass --format text|json|md.",
    );
  }
  result.data.report_a = normalizePosixPath(reportA);
  result.data.report_b = normalizePosixPath(reportB);
  const a = JSON.parse(readFileSync(reportA, "utf-8")) as Record<string, unknown>[];
  const b = JSON.parse(readFileSync(reportB, "utf-8")) as Record<string, unknown>[];
  const changes = diffAssertionSets(a, b);
  const grouped = whatChanged(changes);
  result.data.changed = changes.length;
  result.data.diff = changes;
  result.data.what_changed = grouped;
  if (changes.length > 0) {
    result.addCode(ExitCode.FINDINGS);
  }
  if (argv.includes("--json")) {
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

/** Every control's severity, from the given `--catalog-dir`s, else every vendored base catalog (SPEC
 * §13.3.4). Deliberately **not** a factoring of {@link vendoredCatalogs}: that function silently
 * skips a subdirectory whose `catalog.yaml` fails to parse and also scans `overlays/`, neither of
 * which matches this function's own throw-on-malformed, `base`-only behaviour (mirrors Python's
 * `_readiness_severities`, `commands/__init__.py:2815-2827`, which has no try/catch around
 * `load_catalog`). A later catalog's control silently overwrites an earlier one sharing an id
 * (plain last-write-wins, matching Python's dict-comprehension). */
function readinessSeverities(catalogDirs: string[]): Map<string, string> {
  let dirs = catalogDirs;
  if (dirs.length === 0) {
    const base = join(bundledCatalogsDir(), "base");
    let names: string[] = [];
    try {
      names = readdirSync(base).sort(byteCompare);
    } catch {
      names = [];
    }
    dirs = names
      .map((name) => join(base, name))
      .filter((dir) => existsSync(join(dir, "catalog.yaml")));
  }
  const severities = new Map<string, string>();
  for (const dir of dirs) {
    const catalog = loadCatalog(requireDir(dir, "catalog-dir", "a catalog directory"));
    for (const control of catalog.controls) {
      severities.set(control.id, control.severity);
    }
  }
  return severities;
}

interface ReadinessArgs {
  reportDir: string | undefined;
  gaps: string | undefined;
  deviations: string | undefined;
  catalogDirs: string[];
}

/** A token argparse classifies as an option rather than a value: it starts with `-`, is not a bare
 * `-`, does not look like a negative number and holds no space (`argparse._parse_optional`). */
function looksLikeOption(token: string): boolean {
  return (
    token.startsWith("-") &&
    token !== "-" &&
    !/^-\d+$|^-\d*\.\d+$/.test(token) &&
    !token.includes(" ")
  );
}

function readinessUnrecognized(detail: string, fix: string): InputError {
  return new InputError("input.readiness_unrecognized_flag", detail, fix);
}

/** `agentce readiness`'s argv, read the way Python's `readiness` subparser (argparse,
 * `allow_abbrev=False`) reads it: `--gaps`/`--deviations` take one value (separate or `=`-joined, the
 * last one wins), `--catalog-dir` appends, `--json`/`--debug`/`--quiet` take none, `--` ends the
 * options, and there is at most one positional. Anything argparse would refuse (an unknown or
 * abbreviated flag, a value flag with no value, a second positional) throws
 * `input.readiness_unrecognized_flag` rather than being skipped, so a mistyped flag can never give a
 * verdict computed without it. An empty `--gaps`/`--deviations` value is ignored, as Python's
 * `if gaps_path:` does. */
function parseReadinessArgv(argv: string[]): ReadinessArgs {
  const flagFix = "pass --gaps, --deviations, or --catalog-dir, or drop the flag.";
  const parsed: ReadinessArgs = {
    reportDir: undefined,
    gaps: undefined,
    deviations: undefined,
    catalogDirs: [],
  };
  let optionsEnded = false;
  for (let i = 1; i < argv.length; i++) {
    const token = argv[i] as string;
    if (!optionsEnded && token === "--") {
      optionsEnded = true;
      continue;
    }
    if (!optionsEnded && looksLikeOption(token)) {
      const eq = token.indexOf("=");
      const name = eq >= 0 ? token.slice(0, eq) : token;
      if (GLOBAL_BOOLEAN_FLAGS.has(name)) {
        if (eq >= 0) {
          throw readinessUnrecognized(`flag '${name}' takes no value.`, `drop the value: ${name}.`);
        }
        continue;
      }
      if (name !== "--gaps" && name !== "--deviations" && name !== "--catalog-dir") {
        throw readinessUnrecognized(`unrecognized flag '${token}'.`, flagFix);
      }
      let value: string;
      if (eq >= 0) {
        value = token.slice(eq + 1);
      } else {
        const next = argv[i + 1];
        if (next === undefined || looksLikeOption(next)) {
          throw readinessUnrecognized(`flag '${name}' needs a value.`, `pass ${name} <path>.`);
        }
        value = next;
        i++;
      }
      if (name === "--catalog-dir") {
        parsed.catalogDirs.push(value);
      } else {
        parsed[name === "--gaps" ? "gaps" : "deviations"] = value === "" ? undefined : value;
      }
      continue;
    }
    if (parsed.reportDir !== undefined) {
      throw readinessUnrecognized(
        `unrecognized argument '${token}'.`,
        "pass exactly one report directory: `agentce readiness <report-dir>`.",
      );
    }
    parsed.reportDir = token;
  }
  return parsed;
}

/** `agentce readiness` (SPEC §13.3.4 stage 4, item 18.25): the report-readiness verdict, matching the
 * Python reference's `cmd_readiness` byte for byte (see `readiness.ts`). Exit 0 for READY and READY
 * WITH LIMITATIONS, 1 for NOT READY -- the verdict logic lives in the engine, never in a skill. */
function cmdReadiness(argv: string[]): CommandResult {
  const result = new CommandResult("readiness");
  const args = parseReadinessArgv(argv);
  const reportDir = requireDir(
    args.reportDir,
    "report_dir",
    "the report directory",
    "pass the report directory: `agentce readiness <report-dir>`.",
  );
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
  const severities = readinessSeverities(args.catalogDirs);
  const verdict = computeReadiness(reportDir, { severities, deviations, gaps });
  result.data.verdict = verdict.verdict;
  result.data.reasons = verdict.reasons;
  result.data.limitations = verdict.limitations;
  const out = join(reportDir, `report-readiness-${localIsoDate()}.md`);
  const lines = [`# Report readiness — ${verdict.verdict}`, ""];
  if (verdict.reasons.length > 0) {
    lines.push("## Blocking reasons", ...verdict.reasons.map((r) => `- ${r}`), "");
  }
  if (verdict.limitations.length > 0) {
    lines.push("## Limitations", ...verdict.limitations.map((l) => `- ${l}`), "");
  }
  writeFileSync(out, `${lines.join("\n")}\n`);
  result.data.report = out;
  if (verdict.verdict === NOT_READY) {
    result.addCode(ExitCode.FINDINGS);
  }
  result.note(
    `${verdict.verdict}: ${verdict.reasons.length} reason(s), ${verdict.limitations.length} limitation(s)`,
  );
  return result;
}

const SIGN_ROLES = ["claimant", "assessor"] as const;
const SIGN_PROFILES = ["sigstore-public", "sigstore-private", "kms"] as const;

interface SignArgs {
  reportDir: string | undefined;
  asRole: string | undefined;
  profile: string | undefined;
  key: string | undefined;
  dryRun: boolean;
  writeTrustRoot: boolean;
}

function signUnrecognized(detail: string, fix: string): InputError {
  return new InputError("input.sign_unrecognized_flag", detail, fix);
}

/** `agentce sign`'s argv, read the way Python's `sign` subparser (argparse, `allow_abbrev=False`)
 * reads it: `--as`/`--profile`/`--key` take one value (separate or `=`-joined, the last one wins),
 * `--dry-run`/`--write-trust-root` take none, `--` ends the options, and there is at most one
 * positional -- the same shape `parseReadinessArgv` established for `readiness` in 18.25. Anything
 * argparse would refuse throws `input.sign_unrecognized_flag` rather than being silently skipped. */
function parseSignArgv(argv: string[]): SignArgs {
  const flagFix =
    "pass --as, --profile, --key, --dry-run, or --write-trust-root, or drop the flag.";
  const parsed: SignArgs = {
    reportDir: undefined,
    asRole: undefined,
    profile: undefined,
    key: undefined,
    dryRun: false,
    writeTrustRoot: false,
  };
  let optionsEnded = false;
  for (let i = 1; i < argv.length; i++) {
    const token = argv[i] as string;
    if (!optionsEnded && token === "--") {
      optionsEnded = true;
      continue;
    }
    if (!optionsEnded && looksLikeOption(token)) {
      const eq = token.indexOf("=");
      const name = eq >= 0 ? token.slice(0, eq) : token;
      if (GLOBAL_BOOLEAN_FLAGS.has(name)) {
        if (eq >= 0) {
          throw signUnrecognized(`flag '${name}' takes no value.`, `drop the value: ${name}.`);
        }
        continue;
      }
      if (name === "--dry-run" || name === "--write-trust-root") {
        if (eq >= 0) {
          throw signUnrecognized(`flag '${name}' takes no value.`, `drop the value: ${name}.`);
        }
        if (name === "--dry-run") {
          parsed.dryRun = true;
        } else {
          parsed.writeTrustRoot = true;
        }
        continue;
      }
      if (name !== "--as" && name !== "--profile" && name !== "--key") {
        throw signUnrecognized(`unrecognized flag '${token}'.`, flagFix);
      }
      let value: string;
      if (eq >= 0) {
        value = token.slice(eq + 1);
      } else {
        const next = argv[i + 1];
        if (next === undefined || looksLikeOption(next)) {
          throw signUnrecognized(`flag '${name}' needs a value.`, `pass ${name} <value>.`);
        }
        value = next;
        i++;
      }
      if (name === "--as") {
        parsed.asRole = value;
      } else if (name === "--profile") {
        parsed.profile = value;
      } else {
        parsed.key = value;
      }
      continue;
    }
    if (parsed.reportDir !== undefined) {
      throw signUnrecognized(
        `unrecognized argument '${token}'.`,
        "pass exactly one report directory: `agentce sign <report-dir> --as claimant|assessor`.",
      );
    }
    parsed.reportDir = token;
  }
  return parsed;
}

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
    if (!keyPath) {
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

/** `agentce sign` (SPEC §8.7, §9.1, item 18.26): the Ed25519/DSSE/in-toto signing flow, `kms` profile,
 * matching the Python reference's `cmd_sign` byte for byte (see `sign.ts`). Refuses to sign unless the
 * report's readiness verdict is not `NOT READY` (`agentce readiness`'s own gate, computed the same
 * way). */
function cmdSign(argv: string[]): CommandResult {
  const result = new CommandResult("sign");
  const args = parseSignArgv(argv);
  const reportDir = requireDir(
    args.reportDir,
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
  result.data.report_dir = reportDir;
  result.data.as = role;
  result.data.profile = profile;
  result.data.dry_run = dryRun;

  // The engine refuses to sign a report that is not ready to publish (SPEC §8.5); `sign` never takes
  // its own `--catalog-dir`/`--gaps`/`--deviations` flags, so this always uses the bundled catalogs
  // with no deviations/gaps.
  const verdict = computeReadiness(reportDir, { severities: readinessSeverities([]) });
  result.data.readiness = verdict.verdict;
  if (verdict.verdict === NOT_READY) {
    throw new InputError(
      "sign.not_ready",
      `the report is ${verdict.verdict}: ${verdict.reasons.join("; ")}.`,
      "resolve the blocking reasons (agentce readiness <report-dir>) before signing.",
    );
  }

  if (dryRun) {
    result.note(`dry run: would sign the claim as ${role} (${profile})`);
    return result;
  }

  const claimPath = join(reportDir, "claim.json");
  if (!existsSync(claimPath) || !statSync(claimPath).isFile()) {
    throw new InputError(
      "sign.no_claim",
      "the report directory has no claim.json to sign.",
      "produce the report first: `agentce assess … --out <report-dir>`.",
    );
  }
  // A fatal decode, not `readFileSync(claimPath, "utf-8")`: Node's utf-8 string coercion silently
  // replaces invalid byte sequences with U+FFFD, while Python's and Java's decoders refuse
  // (18.26 round-3 verifier finding). `TextDecoder`'s `fatal: true` throws instead.
  const claimText = new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(claimPath));
  const parsedClaim: unknown = JSON.parse(claimText);
  if (!isRecord(parsedClaim)) {
    // Matches Python's `claim.setdefault("signatures", [])` (AttributeError on a non-dict claim)
    // and Java's `(ObjectNode) Json.parseFile(claimPath)` cast (ClassCastException) -- both crash
    // into `internal.unexpected` rather than silently appending a "signatures" property that
    // `JSON.stringify` would then drop from a top-level array (18.26 round-2 verifier finding).
    throw new Error(
      `claim.json does not hold an object (got ${Array.isArray(parsedClaim) ? "array" : parsedClaim === null ? "null" : typeof parsedClaim})`,
    );
  }
  const claim = parsedClaim;

  const signer = signSigner(args.key, profile);
  const subjects = signSubjects(reportDir, claim);
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
  // A present-but-non-array `signatures` (null, a string, an object) matches Python's
  // `claim["signatures"].append(record)` crashing with AttributeError, not a silent replacement
  // with a fresh array (18.26 round-3 verifier finding).
  if (claim.signatures !== undefined && !Array.isArray(claim.signatures)) {
    throw new Error(`claim.json's "signatures" is not an array (got ${typeof claim.signatures})`);
  }
  const signatures = (claim.signatures as unknown[] | undefined) ?? [];
  signatures.push(record);
  claim.signatures = signatures;
  writeFileSync(claimPath, `${jsonStringifyAscii(sortKeysDeep(claim), 2)}\n`);
  const sigDir = join(reportDir, "signatures");
  mkdirSync(sigDir, { recursive: true });
  const detached = join(sigDir, `${role}-${profile}.dsse.json`);
  writeFileSync(detached, `${jsonStringifyAscii(sortKeysDeep(record), 2)}\n`);
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
    const trustRootPath = join(reportDir, "trust-root.json");
    const document = {
      keys: {
        [kmsSigner.keyid]: { public_key: kmsSigner.publicKeyB64, identity: org },
      },
    };
    writeFileSync(trustRootPath, `${jsonStringifyAscii(sortKeysDeep(document), 2)}\n`);
    result.data.trust_root = trustRootPath;
  }
  result.note(`signed ${basename(claimPath)} as ${role} (${profile})`);
  return result;
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

/** `agentce version` (SPEC §8.5): the same structured envelope every other command returns, with a
 * real installed-artifact `no_ml` self-report (mirrors Python's `cmd_version`). Distinct from the
 * bare `--version`/`-V` flag, which stays a plain one-line shortcut (handled before this is ever
 * reached, `main` below). */
function cmdVersion(): CommandResult {
  const result = new CommandResult("version");
  const resolveFrom = join(__dirname, "..");
  const scan = evaluateInstalledNoMl(loadVendoredDenylist(), resolveFrom);
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
  result.data.error = { message_key: error.key, detail: error.cause, fix: error.fix };
  result.note(`${error.key}: ${error.cause}`);
  if (error.fix) {
    result.note(`fix: ${error.fix}`);
  }
  return result;
}

export function main(argv: string[]): number {
  const command = argv[0];
  if (command === "--version" || command === "-V") {
    console.log(`agentce ${engineVersion()}`);
    return 0;
  }

  // The numerics verb is a plain computation seam for the two-engine vector check (P3.1): it reads a
  // `numerics-vectors` case file and prints {caseName: result} as plain JSON, not the envelope.
  if (command === "numerics") {
    const casefile = argv[1];
    if (casefile === undefined) {
      console.error("numerics: a case file path is required");
      return ExitCode.INPUT_ERROR;
    }
    const data = JSON.parse(readFileSync(casefile, "utf-8"));
    console.log(JSON.stringify(computeVectorFile(data)));
    return 0;
  }

  // digest-tree is a plain computation seam (the same pattern as `numerics` above), driven from
  // outside the repo's TypeScript sources by `tools/catalog_digest_check.py` against the built
  // `dist/`: it prints one catalog directory's real content digest, nothing else.
  if (command === "digest-tree") {
    const dir = argv[1];
    if (dir === undefined) {
      console.error("digest-tree: a directory path is required");
      return ExitCode.INPUT_ERROR;
    }
    console.log(catalogProvenanceDigest(dir));
    return 0;
  }

  // security-view is a plain computation seam (the same pattern as `numerics`/`digest-tree` above),
  // driven by the security-view build gate's cross-engine diff (18.16, C5): it reads a fixture file
  // with `{activity, assertions}`, runs `computeSecurityView`, and prints the result as JSON -- not
  // part of the public `assess` command surface (no engine here has `--for`/multi-format
  // `write_report` yet, TRADEOFFS row 8).
  if (command === "security-view") {
    const fixturePath = argv[1];
    if (fixturePath === undefined) {
      console.error("security-view: a fixture file path is required");
      return ExitCode.INPUT_ERROR;
    }
    const data = JSON.parse(readFileSync(fixturePath, "utf-8"));
    const activity = data.activity as Activity;
    const assertions = (data.assertions as unknown[]).map(assertionFromJson);
    console.log(JSON.stringify(computeSecurityView(activity, assertions)));
    return 0;
  }

  const json = argv.includes("--json");
  const debug = argv.includes("--debug");
  let result: CommandResult;
  try {
    if (command === "conformance") {
      result = cmdConformance(argv);
    } else if (command === "validate") {
      result = cmdValidate(argv);
    } else if (command === "assess") {
      result = cmdAssess(argv);
    } else if (command === "report") {
      result = cmdReport(argv);
    } else if (command === "diff") {
      result = cmdDiff(argv);
    } else if (command === "readiness") {
      result = cmdReadiness(argv);
    } else if (command === "sign") {
      result = cmdSign(argv);
    } else if (command === "quickstart") {
      result = cmdQuickstart(argv);
    } else if (command === "version") {
      result = cmdVersion();
    } else {
      result = notImplemented(command ?? "");
    }
  } catch (exc) {
    if (exc instanceof AgentceError) {
      result = errorResult(command ?? "", exc);
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
        command ?? "",
        new AgentceError(
          "internal.unexpected",
          message,
          "re-run with --debug to see the stack trace, then file an issue.",
        ),
      );
    }
  }
  emit(result, json);
  return result.exitCode;
}

if (require.main === module) {
  process.exit(main(process.argv.slice(2)));
}
