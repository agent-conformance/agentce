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

import { execFileSync } from "node:child_process";
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
import { resolve as resolveApplicability } from "./applicability";
import { aggregate } from "./assertions";
import { assessSubjects } from "./assess";
import { loadBundle } from "./bundle";
import { type Catalog, loadCatalog } from "./catalog";
import { computeCoverage } from "./coverage";
import { DomainBinding } from "./domain";
import { InputError } from "./errors";
import { ingest } from "./ingest";
import { verifyBundle } from "./integrity";
import { evaluateNoMl } from "./noMl";
import { loadProfile } from "./profile";
import { writeReport } from "./report";
import { byteCompare, sortKeysDeep } from "./util";
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
  verifyBundle(ingested.accepted, bundle.manifest, bundle.root); // findings inform, never abort
  const domain = DomainBinding.load(join(proj, "domain.linkml.yaml"));
  const profile = loadProfile(join(proj, "applicability.yaml"));
  computeCoverage(ingested.accepted, profile, bundle.root);
  resolveApplicability(profile, ingested.accepted);
  const assertions = assessSubjects(ingested.accepted, profile, catalogs, domain);
  writeReport(outDir, assertions, {
    bundleDigest: bundle.digest,
    catalogs: labels,
    operator: ECS_OPERATOR,
    invocation: ["conformance", pid],
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
}

export interface RunEcsOptions {
  enginePath: string;
  corpusDir: string;
  outDir: string | null;
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
  if (options.outDir !== null) {
    mkdirSync(options.outDir, { recursive: true });
    writeFileSync(
      join(options.outDir, "implementation-report.json"),
      `${JSON.stringify(sortKeysDeep(report), null, 2)}\n`,
    );
  }
  return report;
}
