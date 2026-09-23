/**
 * Evidence bundle loading and manifest verification (SPEC §8.1, §5.4).
 *
 * A bundle is a directory with `events/*.jsonl`, `attestations/`, `reference/`, and a `manifest.json`
 * listing every file with its SHA-256. Loading verifies the manifest is present and every listed file
 * hashes correctly; a missing or mismatching manifest aborts with an InputError (exit 3).
 */

import { createHash } from "node:crypto";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, join, relative, sep } from "node:path";
import { sha256Hex } from "./canonical";
import { InputError } from "./errors";
import { parseJson } from "./json";

export interface Bundle {
  root: string;
  manifest: Record<string, unknown>;
  eventFiles: string[];
  sources: Set<string> | null;
  sourceClasses: Map<string, string> | null;
  digest: string;
}

function fileSha256Hex(path: string): string {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function normaliseDigest(raw: string): string {
  return raw.startsWith("sha256:") ? raw.slice("sha256:".length).toLowerCase() : raw.toLowerCase();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * `statSync(path).isFile()`, but an OS-level hazard a confined path can still hit at access time (a
 * symlink loop, a name too long for the filesystem) is treated as "not present", never left to
 * propagate as an unexpected error.
 */
export function safeIsFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

/**
 * Resolve `rel` against `root`; return the path only if it stays inside `root` after symlinks
 * resolve, else `null`. Every bundle-adjacent reference an adversarial evidence bundle can carry --
 * the primary manifest's own file list, a coverage denominator's manifest, an integrity block's
 * `sig_ref` -- is confined through this one function, so a symlink escape, a literal `..`/absolute
 * path, a symlink loop, an embedded NUL byte, or a path segment too long for the filesystem are
 * refused the same deliberate way everywhere, never left to surface as an unexpected error.
 */
export function confineToRoot(root: string, rel: string): string | null {
  if (!rel || rel.startsWith("/") || rel.split("/").includes("..")) {
    return null;
  }
  const candidate = join(root, rel);
  let resolvedRoot: string;
  try {
    resolvedRoot = realpathSync(root);
  } catch {
    return null;
  }
  let resolved: string;
  try {
    resolved = realpathSync(candidate);
  } catch (exc) {
    // A path that simply is not there is not a confinement failure: the caller's own
    // "missing from the bundle" check reports that with its own message key. Any other
    // hazard (symlink loop, name too long, an embedded NUL) fails closed.
    return (exc as NodeJS.ErrnoException | null)?.code === "ENOENT" ? candidate : null;
  }
  const inside = relative(resolvedRoot, resolved);
  if (inside !== "" && (inside === ".." || inside.startsWith(`..${sep}`) || isAbsolute(inside))) {
    return null;
  }
  return candidate;
}

function safeMember(root: string, rel: string): string {
  const member = confineToRoot(root, rel);
  if (member === null) {
    throw new InputError(
      "input.bundle_manifest_path",
      `manifest lists an unsafe path ${JSON.stringify(rel)}.`,
      "the manifest must list only paths inside the bundle.",
    );
  }
  return member;
}

export function loadBundle(bundleDir: string): Bundle {
  const manifestPath = join(bundleDir, "manifest.json");
  if (!safeIsFile(manifestPath)) {
    throw new InputError(
      "input.bundle_manifest_missing",
      `the bundle at ${JSON.stringify(bundleDir)} has no manifest.json.`,
      "add a manifest.json listing every file with its SHA-256.",
    );
  }
  let manifest: Record<string, unknown>;
  try {
    const parsed = parseJson(readFileSync(manifestPath, "utf-8"));
    if (!isRecord(parsed)) {
      throw new Error("manifest is not an object");
    }
    manifest = parsed;
  } catch (exc) {
    throw new InputError(
      "input.bundle_manifest_invalid",
      `manifest.json is not valid JSON: ${exc}.`,
      "regenerate the bundle so its manifest.json is well-formed.",
    );
  }

  const files = manifest.files;
  if (!Array.isArray(files) || files.length === 0) {
    throw new InputError(
      "input.bundle_manifest_files",
      "manifest.json has no non-empty 'files' array.",
      "the manifest must list every file with its path and sha256.",
    );
  }

  const eventFiles: string[] = [];
  for (const entry of files) {
    if (!isRecord(entry) || !("path" in entry) || !("sha256" in entry)) {
      throw new InputError(
        "input.bundle_manifest_entry",
        "a 'files' entry is missing 'path' or 'sha256'.",
        'each entry needs {"path": ..., "sha256": ...}.',
      );
    }
    const rel = String(entry.path);
    const member = safeMember(bundleDir, rel);
    if (!safeIsFile(member)) {
      throw new InputError(
        "input.bundle_manifest_mismatch",
        `manifest lists ${JSON.stringify(rel)}, which is missing from the bundle.`,
        "regenerate the bundle so its files match the manifest.",
      );
    }
    if (fileSha256Hex(member) !== normaliseDigest(String(entry.sha256))) {
      throw new InputError(
        "input.bundle_manifest_mismatch",
        `${JSON.stringify(rel)} does not match its manifest SHA-256.`,
        "regenerate the bundle so its files match the manifest.",
      );
    }
    if (rel.startsWith("events/") && rel.endsWith(".jsonl")) {
      eventFiles.push(member);
    }
  }

  let sources: Set<string> | null = null;
  const sourceClasses = new Map<string, string>();
  const declared = manifest.sources;
  if (Array.isArray(declared)) {
    const ids = new Set<string>();
    for (const item of declared) {
      if (!isRecord(item) || !("id" in item)) {
        continue;
      }
      const sourceId = String(item.id);
      ids.add(sourceId);
      if (typeof item.class === "string" && item.class) {
        sourceClasses.set(sourceId, item.class);
      }
    }
    if (ids.size > 0) {
      sources = ids;
    }
  }

  return {
    root: bundleDir,
    manifest,
    eventFiles: eventFiles.sort(),
    sources,
    sourceClasses: sourceClasses.size > 0 ? sourceClasses : null,
    digest: `sha256:${sha256Hex(manifest)}`,
  };
}
