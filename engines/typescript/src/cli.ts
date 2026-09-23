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
import { resolve as resolveApplicability } from "./applicability";
import { type Assertion, aggregate, assertionFromJson, assertionToJson } from "./assertions";
import { assessSubjects, evaluatedNothing } from "./assess";
import { loadBundle } from "./bundle";
import { catalogsDir as bundledCatalogsDir, quickstartDir } from "./bundled";
import { type Catalog, loadCatalog } from "./catalog";
import { runEcs } from "./conformance";
import { computeCoverage } from "./coverage";
import { DomainBinding } from "./domain";
import { AgentceError, InputError } from "./errors";
import { ExitCode } from "./exitCodes";
import { buildGraph } from "./graph";
import { ingest } from "./ingest";
import { integrityResultToJson, verifyBundle } from "./integrity";
import { DEFAULT_LANGUAGE } from "./messages";
import { computeVectorFile } from "./numerics";
import { type Profile, loadProfile } from "./profile";
import { writeQuarantine } from "./quarantine";
import {
  renderEvidencePack,
  renderOscal,
  renderReportHtml,
  renderReportMd,
  renderSarif,
  writeReport,
} from "./report";
import { CommandResult } from "./result";
import { StateDir, windowEnd } from "./state";
import { GraphStore } from "./store";
import { byteCompare, sortKeysDeep, writeJsonl } from "./util";
import { summarize } from "./verdict";
import { engineVersion } from "./version";

const DEFAULT_OUT_DIR = "out";
const REPORT_FORMATS = ["md", "html", "oscal", "sarif", "pack"] as const;

function emit(result: CommandResult, json: boolean): void {
  if (json) {
    // json.dumps(envelope, sort_keys=True, indent=2), matching the Python reference
    console.log(JSON.stringify(sortKeysDeep(result.envelope()), null, 2));
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

function requireDir(raw: string | undefined, key: string, what: string): string {
  const fix = `pass --${key} <dir>.`;
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

function requireFile(raw: string | undefined, key: string, what: string): string {
  const fix = `pass --${key} <file>.`;
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
 * directory that was passed or to a vendored catalog.
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
  }
  if (ids.length === 0 && loaded.length === 0) {
    throw new InputError(
      "input.catalog_missing",
      "no catalog to evaluate: --catalog and --catalog-dir were not passed and the profile " +
        "declares no catalogs.",
      "pass --catalog <id@version>, or list the catalogs to apply under `catalogs:` in the profile.",
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

  writeReport(out, evaluated, {
    bundleDigest: bundle.digest,
    catalogs: catalogLabels,
    operator: process.env.AGENTCE_OPERATOR ?? "unknown",
    invocation: [options.invocationCommand, scrubPath(bundleDir), scrubPath(profilePath)],
    supersedes,
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
  if (options.state !== undefined) {
    result.data.supersedes = supersedes;
  }
  if (nonConformant > 0) {
    result.addCode(ExitCode.FINDINGS);
  }
  if (evaluatedNothing(evaluated)) {
    throw nothingEvaluated(profileObj, ingested.accepted, evaluated.length);
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

/** Re-render a report from a committed `assertions.json` (SPEC §9.4); `--validate` and the `public`
 * format are not yet ported and are refused with a named, honest error rather than a silent guess. */
function cmdReport(argv: string[]): CommandResult {
  const result = new CommandResult("report");
  if (argv.includes("--validate")) {
    throw new InputError(
      "input.report_validate_unsupported",
      "the TypeScript engine has no report --validate support.",
      "validate the report's artifacts against their vendored schemas with the Python engine.",
    );
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
  if (command === "--version" || command === "-V" || command === "version") {
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

  const json = argv.includes("--json");
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
    } else if (command === "quickstart") {
      result = cmdQuickstart(argv);
    } else {
      result = notImplemented(command ?? "");
    }
  } catch (exc) {
    if (exc instanceof AgentceError) {
      result = errorResult(command ?? "", exc);
    } else {
      throw exc;
    }
  }
  emit(result, json);
  return result.exitCode;
}

if (require.main === module) {
  process.exit(main(process.argv.slice(2)));
}
