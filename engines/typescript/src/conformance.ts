/**
 * The Engine Conformance Suite (ECS) runner (SPEC §11.5).
 *
 * `agentce conformance run` executes every project in a corpus through the engine and emits an
 * `implementation-report.json` recording how many projects were identical and the overall `claim`,
 * folding in the `no_ml` dependency check: `claim: full` requires `no_ml: pass`. Phase 1 runs a single
 * engine against a corpus whose golden outputs are not committed (SPEC §11.7), so every project that
 * assesses without error is counted identical (self-conformance). The corpus is materialised by running
 * its Python generator — the shared deterministic source — since the corpus output is never committed.
 * Every project is assessed with a stable operator and invocation so per-project manifests are identical
 * across machines but for `run.started_at` and `run.host_fingerprint`. This is a faithful port.
 */

import { execFileSync, spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname } from "node:path";
import { summarizeActivity } from "./activity";
import { resolve as resolveApplicability } from "./applicability";
import { aggregate } from "./assertions";
import { assessSubjects } from "./assess";
import { computeBlindSpots } from "./blindSpots";
import { loadBundle, resolveLoose } from "./bundle";
import { type Catalog, loadCatalog } from "./catalog";
import { computeCoverage } from "./coverage";
import { DomainBinding } from "./domain";
import { InputError, isPermissionError } from "./errors";
import { pyStrip } from "./failOn";
import { ingest } from "./ingest";
import { integrityCaps, verifyStreams } from "./integrity";
import { NonCanonicalNumber, asParsed, parseJson } from "./json";
import { installedNoMl } from "./noMl";
import { parsePythonIntGrammar, toBigIntFromInt } from "./otelGenai";
import { OUT_DIR, writeFile, writingInto } from "./outputs";
import { loadProfile } from "./profile";
import { pyGet, pyTruthy } from "./readiness";
import { writeReport } from "./report";
import { byteCompare, decodeUtf8Strict, joinPath, jsonStringifyAscii, sortKeysDeep } from "./util";
import { ENGINE_NAME, SPEC_VERSION, engineVersion } from "./version";

/** Stable provenance for ECS assessments, so per-project manifests are identical across machines. */
const ECS_OPERATOR = "ecs";

/** Whether `path` in the corpus folder is a regular file after links. One it cannot reach is refused
 * as unreadable, never read as absent, as Python's `_materialise_corpus` (18.169). */
function isCorpusFile(corpusDir: string, path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch (exc) {
    if (isPermissionError(exc)) {
      throw new InputError(
        "input.corpus_unreadable",
        `${path} in the corpus folder ${corpusDir} could not be read (Permission denied).`,
        "make the corpus folder readable and enterable, then re-run.",
      );
    }
    return false;
  }
}

function materialiseCorpus(corpusDir: string): string {
  if (isCorpusFile(corpusDir, joinPath(corpusDir, "corpus-manifest.json"))) {
    return corpusDir;
  }
  const generator = joinPath(corpusDir, "generator", "generate.py");
  if (isCorpusFile(corpusDir, generator)) {
    const tmp = mkdtempSync(joinPath(tmpdir(), "agentce-corpus-"));
    try {
      execFileSync(
        "uv",
        ["run", "--project", corpusDir, "python", generator, "--set", "v1", "--out", tmp],
        {
          stdio: "pipe",
        },
      );
    } catch (exc) {
      const detail = exc instanceof Error ? exc.message : String(exc);
      throw new InputError(
        "input.corpus_generate_failed",
        `generating the corpus at ${corpusDir} failed: ${detail.slice(0, 200)}`,
        "check that the corpus generator runs: python corpus/generator/generate.py --out <dir>.",
      );
    }
    if (!existsSync(joinPath(tmp, "corpus-manifest.json"))) {
      throw new InputError(
        "input.corpus_generate_failed",
        `the corpus generator at ${corpusDir} produced no corpus-manifest.json.`,
        "check that the corpus generator runs: python corpus/generator/generate.py --out <dir>.",
      );
    }
    return tmp;
  }
  throw new InputError(
    "input.corpus_not_found",
    `${corpusDir} has neither a corpus-manifest.json nor a generator/generate.py.`,
    "pass a generated corpus directory or the corpus source tree.",
  );
}

function catalogsFor(repoRoot: string): { catalogs: Catalog[]; labels: string[] } {
  const base = joinPath(repoRoot, "spec", "catalogs", "base");
  const catalogs: Catalog[] = [];
  const labels: string[] = [];
  let subdirs: string[] = [];
  try {
    subdirs = readdirSync(base).sort(byteCompare);
  } catch {
    subdirs = [];
  }
  for (const sub of subdirs) {
    if (existsSync(joinPath(base, sub, "catalog.yaml"))) {
      const catalog = loadCatalog(joinPath(base, sub));
      catalogs.push(catalog);
      labels.push(`${catalog.id}@${catalog.version}`);
    }
  }
  if (catalogs.length === 0) {
    throw new InputError(
      "input.catalog_not_found",
      `no base catalog found under ${base}.`,
      "the engine expects the base catalog under spec/catalogs/base/<id>/.",
    );
  }
  return { catalogs, labels };
}

function assessProject(
  corpusRoot: string,
  project: Record<string, unknown>,
  outDir: string,
  catalogs: Catalog[],
  labels: string[],
  outRaw: string | null,
): void {
  const pid = String(project.id);
  const proj = joinPath(corpusRoot, "projects", pid);
  const bundle = loadBundle(joinPath(proj, "evidence"));
  const ingested = ingest(bundle);
  // Findings inform, never abort; hash what the source signed, not the trust-corrected copy.
  const streams = verifyStreams(ingested.rawAccepted, bundle.manifest, bundle.root);
  const domain = DomainBinding.load(joinPath(proj, "domain.linkml.yaml"));
  const profile = loadProfile(joinPath(proj, "applicability.yaml"));
  const coverage = computeCoverage(ingested.accepted, profile, bundle.members);
  resolveApplicability(profile, ingested.accepted);
  const assertions = assessSubjects(
    ingested.accepted,
    profile,
    catalogs,
    domain,
    true,
    ingested.withheld,
    integrityCaps(ingested.rawAccepted, ingested.accepted, streams),
    coverage,
  );
  const activity = summarizeActivity(ingested.accepted, profile);
  const blindSpots = computeBlindSpots(assertions, profile, catalogs, ingested.accepted);
  const write = (): unknown =>
    writeReport(outDir, assertions, {
      bundleDigest: bundle.digest,
      catalogs: labels,
      catalogObjects: catalogs,
      operator: ECS_OPERATOR,
      invocation: ["conformance", pid],
      activity,
      blindSpots,
    });
  // Under the user's --out a report write that fails is refused under OUT_DIR, as assess --out
  // refuses it (18.184d).
  if (outRaw !== null) {
    writingInto(outRaw, OUT_DIR, write);
  } else {
    write();
  }
}

export interface EcsReport {
  engine: { impl: string; version: string };
  spec_version: string;
  corpus_version: string;
  projects: { total: number; identical: number; different: number };
  no_ml: string;
  claim: string;
  failures: Array<{ project: string; error: string }>;
  /** The adapters' own orchestrator's report, with --adapters only. */
  adapter_conformance?: AdapterDetail;
  /** The adapter-conformance claim, with --adapters only. */
  adapters?: string;
}

/** What the adapters' orchestrator printed (its JSON object, kept as given), or Python's error record
 * when it printed no JSON. */
export type AdapterDetail = Record<string, unknown>;

/** How the orchestrator is run: its stdout and stderr, as text. A test passes a sample runner. */
export type AdapterRunner = (
  command: string,
  args: readonly string[],
  cwd: string,
) => { stdout: string; stderr: string };

const runProcess: AdapterRunner = (command, args, cwd) => {
  const proc = spawnSync(command, args, { cwd, maxBuffer: 1 << 30 });
  if (proc.error !== undefined) {
    throw proc.error; // uv missing from PATH: internal.unexpected, as Python's FileNotFoundError
  }
  // strict, as Python's subprocess.run(text=True): bytes that are not UTF-8 raise, never U+FFFD
  return { stdout: decodeUtf8Strict(proc.stdout), stderr: decodeUtf8Strict(proc.stderr) };
};

/** Run adapter conformance in the adapters directory's own environments (SPEC 11.5, 12.3), as
 * Python's `_adapter_conformance` does: each adapter is a separate environment, so the adapters' own
 * orchestrator runs as a subprocess in that directory, given the absolute --out (a relative one
 * resolves against the working directory, never the adapters directory). */
export function adapterConformance(
  adaptersDir: string,
  outDir: string | null,
  run: AdapterRunner = runProcess,
): AdapterDetail {
  const args = ["run", "--quiet", "python", "conformance.py", "--json"];
  if (outDir !== null) {
    args.push("--out", resolveLoose(outDir) ?? outDir);
  }
  const proc = run("uv", args, adaptersDir);
  let parsed: unknown;
  try {
    parsed = parseJson(proc.stdout, { pythonConstants: true });
  } catch {
    // the last 800 characters (code points, as Python counts them) of stderr, else stdout
    const error = [...pyStrip(proc.stderr || proc.stdout)].slice(-800).join("");
    return { adapters: [], total: 0, identical: 0, round_trip: false, error };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    // Python's `_adapter_claim` calls `.get` on it and fails: internal.unexpected
    const kind = Array.isArray(parsed) ? "list" : parsed === null ? "NoneType" : typeof parsed;
    throw new Error(`'${kind}' object has no attribute 'get'`);
  }
  return parsed as AdapterDetail;
}

/** Python's `int()` of a report count (a number, a boolean or an integer string), exact at any
 * size: an integer token past 2^53 is read from its source text, a float token truncated (an
 * infinite or NaN one refused), and a string goes through the same `int(str)` grammar the OTel
 * adapter reader uses. */
function pyInt(raw: unknown): bigint {
  if (raw instanceof NonCanonicalNumber && raw.reason === "integer_out_of_range") {
    return toBigIntFromInt(raw);
  }
  const value = raw instanceof NonCanonicalNumber ? raw.toNumber() : raw;
  if (typeof value === "number" && Number.isFinite(value)) {
    return BigInt(Math.trunc(value));
  }
  if (typeof value === "boolean") {
    return value ? 1n : 0n;
  }
  if (typeof value === "string") {
    const parsed = parsePythonIntGrammar(value);
    if (parsed !== null) {
      return toBigIntFromInt(parsed);
    }
  }
  throw new Error(`int() argument is not a number: ${JSON.stringify(value)}`);
}

/** The adapter-conformance claim, mirroring the ECS claim scheme (Python's `_adapter_claim`, SPEC
 * §11.5): full needs every adapter identical and the round trip holding. */
export function adapterClaim(detail: AdapterDetail): string {
  const total = pyInt(pyGet(detail, "total", 0));
  const identical = pyInt(pyGet(detail, "identical", 0));
  if (total > 0n && identical === total && pyTruthy(asParsed(detail.round_trip))) {
    return "full";
  }
  if (identical > 0n) {
    return "partial";
  }
  return "none";
}

export interface RunEcsOptions {
  enginePath: string;
  corpusDir: string;
  outDir: string | null;
  /** Also run adapter conformance over this directory and fold it into the report. */
  adaptersDir?: string | null;
  /** How the adapters' orchestrator is run (default: a real subprocess). */
  runAdapters?: AdapterRunner;
}

/** Run every corpus project through the engine and return the implementation report. */
export function runEcs(options: RunEcsOptions): EcsReport {
  const enginePath = realpathSync.native(options.enginePath);
  const repoRoot = dirname(dirname(enginePath));
  const { catalogs, labels } = catalogsFor(repoRoot);
  const corpusRoot = materialiseCorpus(realpathSync.native(options.corpusDir));
  const reportsRoot =
    options.outDir !== null
      ? joinPath(options.outDir, "projects")
      : mkdtempSync(joinPath(tmpdir(), "agentce-ecs-"));

  const manifest = JSON.parse(readFileSync(joinPath(corpusRoot, "corpus-manifest.json"), "utf-8"));
  const projects: Array<Record<string, unknown>> = Array.isArray(manifest.projects)
    ? manifest.projects
    : [];
  let identical = 0;
  const failures: Array<{ project: string; error: string }> = [];
  const sorted = [...projects].sort((a, b) => byteCompare(String(a.id), String(b.id)));
  for (const project of sorted) {
    const pid = String(project.id);
    try {
      assessProject(
        corpusRoot,
        project,
        joinPath(reportsRoot, pid),
        catalogs,
        labels,
        options.outDir,
      );
      identical += 1; // self-golden: the reference regenerates its own golden (SPEC §11.7)
    } catch (exc) {
      if (exc instanceof InputError && exc.key === OUT_DIR.key) {
        throw exc; // --out cannot be written: the run is refused, not one project
      }
      const name = exc instanceof Error ? exc.name : "Error";
      const message = exc instanceof Error ? exc.message : String(exc);
      failures.push({ project: pid, error: `${name}: ${message}` });
    }
  }

  // The running engine's own installed dependency tree, as `agentce version --json` and Python's
  // conformance run report it: never a lock file under whatever path --engine names (18.179).
  const scan = installedNoMl();
  const total = projects.length;
  const different = total - identical;
  let claim: string;
  if (scan.result !== "pass") {
    claim = "none"; // a learned component in the tree voids the claim (SPEC §11.5)
  } else if (different === 0 && total > 0) {
    claim = "full";
  } else if (identical > 0) {
    claim = "partial";
  } else {
    claim = "none";
  }

  const report: EcsReport = {
    engine: { impl: ENGINE_NAME, version: engineVersion() },
    spec_version: SPEC_VERSION,
    corpus_version: String(manifest.corpus_version ?? "unknown"),
    projects: { total, identical, different },
    no_ml: scan.result,
    claim,
    failures,
  };
  if (options.adaptersDir !== undefined && options.adaptersDir !== null) {
    const detail = adapterConformance(options.adaptersDir, options.outDir, options.runAdapters);
    report.adapter_conformance = asParsed(detail) as AdapterDetail;
    report.adapters = adapterClaim(detail);
  }
  const outDir = options.outDir;
  if (outDir !== null) {
    writingInto(outDir, OUT_DIR, () => {
      mkdirSync(outDir, { recursive: true });
      // json.dumps(report, sort_keys=True, indent=2): Python's ensure_ascii default included
      writeFile(
        joinPath(outDir, "implementation-report.json"),
        `${jsonStringifyAscii(sortKeysDeep(report), 2)}\n`,
      );
    });
  }
  return report;
}
