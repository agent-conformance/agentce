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
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { summarizeActivity } from "./activity";
import { resolve as resolveApplicability } from "./applicability";
import { aggregate } from "./assertions";
import { assessSubjects } from "./assess";
import { computeBlindSpots } from "./blindSpots";
import { loadBundle } from "./bundle";
import { type Catalog, loadCatalog } from "./catalog";
import { computeCoverage } from "./coverage";
import { DomainBinding } from "./domain";
import { InputError } from "./errors";
import { isPySpace } from "./failOn";
import { ingest } from "./ingest";
import { verifyBundle } from "./integrity";
import { evaluateNoMl } from "./noMl";
import { parsePythonIntGrammar } from "./otelGenai";
import { loadProfile } from "./profile";
import { pyTruthy } from "./readiness";
import { writeReport } from "./report";
import { byteCompare, jsonStringifyAscii, sortKeysDeep } from "./util";
import { ENGINE_NAME, SPEC_VERSION, engineVersion } from "./version";

/** Stable provenance for ECS assessments, so per-project manifests are identical across machines. */
const ECS_OPERATOR = "ecs";

function materialiseCorpus(corpusDir: string): string {
  if (existsSync(join(corpusDir, "corpus-manifest.json"))) {
    return corpusDir;
  }
  const generator = join(corpusDir, "generator", "generate.py");
  if (existsSync(generator)) {
    const tmp = mkdtempSync(join(tmpdir(), "agentce-corpus-"));
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
    if (!existsSync(join(tmp, "corpus-manifest.json"))) {
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
  const base = join(repoRoot, "spec", "catalogs", "base");
  const catalogs: Catalog[] = [];
  const labels: string[] = [];
  let subdirs: string[] = [];
  try {
    subdirs = readdirSync(base).sort(byteCompare);
  } catch {
    subdirs = [];
  }
  for (const sub of subdirs) {
    if (existsSync(join(base, sub, "catalog.yaml"))) {
      const catalog = loadCatalog(join(base, sub));
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
): void {
  const pid = String(project.id);
  const proj = join(corpusRoot, "projects", pid);
  const bundle = loadBundle(join(proj, "evidence"));
  const ingested = ingest(bundle);
  verifyBundle(ingested.rawAccepted, bundle.manifest, bundle.root); // findings inform, never abort; hash what the source signed, not the trust-corrected copy
  const domain = DomainBinding.load(join(proj, "domain.linkml.yaml"));
  const profile = loadProfile(join(proj, "applicability.yaml"));
  computeCoverage(ingested.accepted, profile, bundle.root);
  resolveApplicability(profile, ingested.accepted);
  const assertions = assessSubjects(ingested.accepted, profile, catalogs, domain);
  const activity = summarizeActivity(ingested.accepted, profile);
  const blindSpots = computeBlindSpots(assertions, profile, catalogs, ingested.accepted);
  writeReport(outDir, assertions, {
    bundleDigest: bundle.digest,
    catalogs: labels,
    catalogObjects: catalogs,
    operator: ECS_OPERATOR,
    invocation: ["conformance", pid],
    activity,
    blindSpots,
  });
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
  const proc = spawnSync(command, args, { cwd, encoding: "utf-8", maxBuffer: 1 << 30 });
  if (proc.error !== undefined) {
    throw proc.error; // uv missing from PATH: internal.unexpected, as Python's FileNotFoundError
  }
  return { stdout: proc.stdout, stderr: proc.stderr };
};

/** Python's `str.strip()` (JavaScript's `trim` differs on a few code points). */
function pyStrip(text: string): string {
  const chars = [...text];
  let start = 0;
  let end = chars.length;
  while (start < end && isPySpace(chars[start]?.codePointAt(0) as number)) {
    start++;
  }
  while (end > start && isPySpace(chars[end - 1]?.codePointAt(0) as number)) {
    end--;
  }
  return chars.slice(start, end).join("");
}

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
    args.push("--out", resolve(outDir));
  }
  const proc = run("uv", args, adaptersDir);
  let parsed: unknown;
  try {
    parsed = JSON.parse(proc.stdout);
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
 * size: a string goes through the same `int(str)` grammar the OTel adapter reader uses. */
function pyInt(value: unknown): bigint {
  if (typeof value === "number" && Number.isFinite(value)) {
    return BigInt(Math.trunc(value));
  }
  if (typeof value === "boolean") {
    return value ? 1n : 0n;
  }
  if (typeof value === "string") {
    const parsed = parsePythonIntGrammar(value);
    if (parsed !== null) {
      return typeof parsed === "number" ? BigInt(parsed) : BigInt(parsed.source);
    }
  }
  throw new Error(`int() argument is not a number: ${JSON.stringify(value)}`);
}

/** The adapter-conformance claim, mirroring the ECS claim scheme (Python's `_adapter_claim`, SPEC
 * §11.5): full needs every adapter identical and the round trip holding. */
export function adapterClaim(detail: AdapterDetail): string {
  const total = pyInt(detail.total ?? 0);
  const identical = pyInt(detail.identical ?? 0);
  if (total > 0n && identical === total && pyTruthy(detail.round_trip)) {
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
  const enginePath = resolve(options.enginePath);
  const repoRoot = dirname(dirname(enginePath));
  const { catalogs, labels } = catalogsFor(repoRoot);
  const corpusRoot = materialiseCorpus(resolve(options.corpusDir));
  const reportsRoot =
    options.outDir !== null
      ? join(options.outDir, "projects")
      : mkdtempSync(join(tmpdir(), "agentce-ecs-"));

  const manifest = JSON.parse(readFileSync(join(corpusRoot, "corpus-manifest.json"), "utf-8"));
  const projects: Array<Record<string, unknown>> = Array.isArray(manifest.projects)
    ? manifest.projects
    : [];
  let identical = 0;
  const failures: Array<{ project: string; error: string }> = [];
  const sorted = [...projects].sort((a, b) => byteCompare(String(a.id), String(b.id)));
  for (const project of sorted) {
    const pid = String(project.id);
    try {
      assessProject(corpusRoot, project, join(reportsRoot, pid), catalogs, labels);
      identical += 1; // self-golden: the reference regenerates its own golden (SPEC §11.7)
    } catch (exc) {
      const name = exc instanceof Error ? exc.name : "Error";
      const message = exc instanceof Error ? exc.message : String(exc);
      failures.push({ project: pid, error: `${name}: ${message}` });
    }
  }

  const scan = evaluateNoMl(repoRoot, join(enginePath, "pnpm-lock.yaml"));
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
    report.adapter_conformance = detail;
    report.adapters = adapterClaim(detail);
  }
  if (options.outDir !== null) {
    mkdirSync(options.outDir, { recursive: true });
    // json.dumps(report, sort_keys=True, indent=2): Python's ensure_ascii default included
    writeFileSync(
      join(options.outDir, "implementation-report.json"),
      `${jsonStringifyAscii(sortKeysDeep(report), 2)}\n`,
    );
  }
  return report;
}
