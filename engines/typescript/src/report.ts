/**
 * Render the report artifacts from assertions (SPEC §9).
 *
 * `assertions.json` is written in RFC 8785 canonical form (byte-identical across engines); the human
 * report (md/html), OSCAL Assessment Results, SARIF, and role-aware evidence packs are rendered from
 * it, and the reproducibility manifest records the digest of every input and output. DC-5 is enforced
 * before anything is written: a supporting verdict without an evidence pointer aborts the run. This is
 * a faithful port of the Python reference; the manifest is serialised as Python's
 * `json.dumps(sort_keys=True, indent=2)` so it is byte-for-byte identical.
 */

import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { arch, platform } from "node:os";
import { dirname, join } from "node:path";
import { type Assertion, aggregate, assertionToJson, checkDc5 } from "./assertions";
import { canonicalize } from "./canonical";
import { byteCompare, sortKeysDeep } from "./util";
import { ENGINE_NAME, SPEC_VERSION, engineVersion } from "./version";

const ZERO_DIGEST = `sha256:${"0".repeat(64)}`;
const NAMESPACE_URL = "6ba7b811-9dad-11d1-80b4-00c04fd430c8";

const SARIF_LEVEL: Record<string, string> = {
  "non-conformant": "error",
  partial: "warning",
  insufficient_evidence: "warning",
};
const OSCAL_STATE: Record<string, string> = {
  conformant: "satisfied",
  "non-conformant": "not-satisfied",
  partial: "not-satisfied",
  not_applicable: "not-satisfied",
  not_assessed: "not-satisfied",
  insufficient_evidence: "not-satisfied",
};

function digestBytes(data: Buffer): string {
  return `sha256:${createHash("sha256").update(data).digest("hex")}`;
}

function packageDigest(): string {
  return `sha256:${createHash("sha256").update(`${ENGINE_NAME}:${engineVersion()}`).digest("hex")}`;
}

/** RFC 4122 v5 UUID (SHA-1) in the URL namespace, matching Python's `uuid.uuid5`. */
function uuid5(...parts: string[]): string {
  const ns = Buffer.from(NAMESPACE_URL.replace(/-/g, ""), "hex");
  const name = Buffer.from(`agentce:${parts.join(":")}`, "utf-8");
  const hash = createHash("sha1").update(ns).update(name).digest().subarray(0, 16);
  const bytes = Buffer.from(hash);
  bytes[6] = ((bytes[6] as number) & 0x0f) | 0x50; // version 5
  bytes[8] = ((bytes[8] as number) & 0x3f) | 0x80; // variant
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

function safe(name: string): string {
  return [...name].map((c) => (/[\p{L}\p{N}]/u.test(c) || "-._".includes(c) ? c : "_")).join("");
}

function bySubjectControl(a: Assertion, b: Assertion): number {
  return byteCompare(a.subject, b.subject) || byteCompare(a.control, b.control);
}

export function renderReportMd(assertions: Assertion[], counts: Record<string, number>): string {
  const lines = ["# AgentCE conformance report", "", "## Outcome summary", ""];
  for (const [outcome, count] of Object.entries(counts)) {
    lines.push(`- ${outcome}: ${count}`);
  }
  lines.push("", "## Assertions", "");
  if (assertions.length === 0) {
    lines.push("_No controls were evaluated._");
  }
  for (const a of [...assertions].sort(bySubjectControl)) {
    lines.push(
      `- \`${a.control}\` @ \`${a.subject}\` -> **${a.outcome}** ` +
        `(rung ${a.rung}, ${a.mode}; ${a.population[1]}/${a.population[0]} failed)`,
    );
  }
  return `${lines.join("\n")}\n`;
}

export function renderReportHtml(assertions: Assertion[], counts: Record<string, number>): string {
  const summary = Object.entries(counts)
    .map(([o, c]) => `<li>${o}: ${c}</li>`)
    .join("");
  const rows = [...assertions]
    .sort(bySubjectControl)
    .map((a) => `<tr><td>${a.control}</td><td>${a.subject}</td><td>${a.outcome}</td></tr>`)
    .join("");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>AgentCE conformance report</title></head><body><h1>AgentCE conformance report</h1><h2>Outcome summary</h2><ul>${summary}</ul><h2>Assertions</h2><table><tr><th>Control</th><th>Subject</th><th>Outcome</th></tr>${rows}</table></body></html>\n`;
}

export function renderOscal(assertions: Assertion[]): Record<string, unknown> {
  const findings = [...assertions].sort(bySubjectControl).map((a) => ({
    uuid: uuid5("finding", a.control, a.subject),
    title: `${a.control} for ${a.subject}`,
    target: {
      type: "objective-id",
      "target-id": a.control,
      status: { state: OSCAL_STATE[a.outcome] ?? "not-satisfied", reason: a.outcome },
    },
  }));
  return {
    "assessment-results": {
      uuid: uuid5("assessment-results"),
      metadata: {
        title: "AgentCE Assessment Results",
        version: engineVersion(),
        "oscal-version": "1.1.2",
      },
      results: [{ uuid: uuid5("result"), title: "AgentCE structural assessment", findings }],
    },
  };
}

export function renderSarif(assertions: Assertion[]): Record<string, unknown> {
  const rules = [...new Set(assertions.map((a) => a.control))]
    .sort(byteCompare)
    .map((control) => ({ id: control }));
  const results = [...assertions]
    .sort(bySubjectControl)
    .filter((a) => a.outcome in SARIF_LEVEL)
    .map((a) => ({
      ruleId: a.control,
      level: SARIF_LEVEL[a.outcome],
      message: { text: `${a.control} on ${a.subject}: ${a.outcome}` },
    }));
  return {
    version: "2.1.0",
    runs: [{ tool: { driver: { name: ENGINE_NAME, version: engineVersion(), rules } }, results }],
  };
}

export function renderEvidencePack(
  subject: string,
  assertions: Assertion[],
): Record<string, unknown> {
  const refs = new Set<string>();
  for (const a of assertions) {
    for (const e of a.evidence) {
      refs.add(e.ref);
    }
  }
  return {
    subject,
    assertions: assertions.map((a) => ({ control: a.control, outcome: a.outcome, mode: a.mode })),
    evidence: [...refs].sort(byteCompare),
  };
}

function now(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export interface ManifestOptions {
  bundleDigest: string;
  catalogs: string[];
  outputs: Record<string, string>;
  operator: string;
  invocation: string[];
  supersedes: string[];
}

export function buildManifest(options: ManifestOptions): Record<string, unknown> {
  const pkgDigest = packageDigest();
  const host = createHash("sha256").update(`${platform()}|${arch()}|${pkgDigest}`).digest("hex");
  const catalogRefs = options.catalogs.map((entry) => {
    const at = entry.indexOf("@");
    const cid = at >= 0 ? entry.slice(0, at) : entry;
    const version = at >= 0 ? entry.slice(at + 1) : "";
    return { id: cid, version: version || "0", digest: ZERO_DIGEST };
  });
  const manifest: Record<string, unknown> = {
    agentce_manifest_version: 1,
    engine: {
      impl: ENGINE_NAME,
      version: engineVersion(),
      spec_version: SPEC_VERSION,
      package_digest: pkgDigest,
    },
    inputs: { bundle_digest: options.bundleDigest, catalogs: catalogRefs },
    outputs: options.outputs,
    run: {
      started_at: now(),
      operator: options.operator,
      host_fingerprint: `sha256:${host}`,
      invocation: options.invocation,
    },
  };
  if (options.supersedes.length > 0) {
    manifest.supersedes = options.supersedes;
  }
  return manifest;
}

export interface WriteReportOptions {
  bundleDigest: string;
  catalogs: string[];
  operator?: string;
  invocation?: string[];
  supersedes?: string[];
}

/** Write every report artifact for `assertions` and return the reproducibility manifest. */
export function writeReport(
  outDir: string,
  assertions: Assertion[],
  options: WriteReportOptions,
): Record<string, unknown> {
  checkDc5(assertions); // DC-5: refuse a supporting verdict without an evidence pointer
  mkdirSync(outDir, { recursive: true });
  const outputs: Record<string, string> = {};

  const writeJson = (name: string, obj: unknown): void => {
    const data = canonicalize(obj);
    writeFileSync(join(outDir, name), data);
    outputs[name] = digestBytes(data);
  };
  const writeTextFile = (name: string, text: string): void => {
    const data = Buffer.from(text, "utf-8");
    writeFileSync(join(outDir, name), data);
    outputs[name] = digestBytes(data);
  };

  const counts = aggregate(assertions);
  writeJson("assertions.json", assertions.map(assertionToJson));
  writeTextFile("report.md", renderReportMd(assertions, counts));
  writeTextFile("report.html", renderReportHtml(assertions, counts));
  writeJson("oscal-ar.json", renderOscal(assertions));
  writeJson("results.sarif", renderSarif(assertions));

  const subjects = [...new Set(assertions.map((a) => a.subject))].sort(byteCompare);
  for (const subject of subjects) {
    const pack = renderEvidencePack(
      subject,
      assertions.filter((a) => a.subject === subject),
    );
    const rel = `packs/${safe(subject)}/pack.json`;
    const data = canonicalize(pack);
    const path = join(outDir, rel);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, data);
    outputs[rel] = digestBytes(data);
  }

  const manifest = buildManifest({
    bundleDigest: options.bundleDigest,
    catalogs: options.catalogs,
    outputs,
    operator: options.operator ?? "unknown",
    invocation: options.invocation ?? [],
    supersedes: options.supersedes ?? [],
  });
  writeFileSync(join(outDir, "manifest.json"), JSON.stringify(sortKeysDeep(manifest), null, 2));
  return manifest;
}
