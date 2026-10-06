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
import { InputError, isPermissionError, readPermissionError, unreadableError } from "./errors";
import { readUntrustedJsonFile } from "./verify";

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
  // Trailing slashes dropped, as Python's `root / rel` drops them: `name/` names the file `name`.
  const candidate = join(root, rel).replace(/(?<=.)\/+$/, "");
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

function bundleUnreadable(bundleDir: string, rel: string): InputError {
  return unreadableError(
    "input.bundle_unreadable",
    "the evidence bundle",
    bundleDir,
    rel,
    "evidence bundle",
  );
}

/** True when `path` cannot even be looked at because of a permission error (an unreadable parent
 * folder), as Python's `permission_denied` (18.68). */
export function permissionDenied(path: string): boolean {
  try {
    statSync(path);
  } catch (err) {
    return isPermissionError(err);
  }
  return false;
}

function safeMember(root: string, rel: string): string {
  const member = confineToRoot(root, rel);
  if (member === null) {
    // Python resolves an unlistable folder's member without error and then finds it unreadable;
    // realpath fails here instead, so a lexically safe path that is denied is unreadable too.
    const lexicallySafe = rel !== "" && !rel.startsWith("/") && !rel.split("/").includes("..");
    if (lexicallySafe && permissionDenied(join(root, rel))) {
      throw bundleUnreadable(root, rel);
    }
    throw new InputError(
      "input.bundle_manifest_path",
      `manifest lists an unsafe path ${rel}: it is absolute, contains '..', resolves outside the bundle root (a symlink or junction escapes it), or cannot be safely resolved.`,
      "the manifest must list only paths that stay inside the bundle after symlinks resolve.",
    );
  }
  return member;
}

export function loadBundle(bundleDir: string): Bundle {
  const manifestPath = join(bundleDir, "manifest.json");
  if (!safeIsFile(manifestPath)) {
    if (permissionDenied(manifestPath)) {
      throw bundleUnreadable(bundleDir, "manifest.json");
    }
    throw new InputError(
      "input.bundle_manifest_missing",
      `the bundle at ${bundleDir} has no manifest.json.`,
      "an agent writes a bundle by running with the agentce_emit emitter on: set " +
        "`AGENTCE_EMIT=1 AGENTCE_EMIT_OUT=<dir>` and see docs/integrate.md; to watch one built, run " +
        "`examples/custom-loop/run.sh <dir>` from a checkout, then `agentce validate --bundle <dir>`.",
    );
  }
  if (readPermissionError(manifestPath)) {
    throw bundleUnreadable(bundleDir, "manifest.json");
  }
  let parsed: unknown;
  try {
    // The untrusted-JSON reader (verify.ts's own file-reading wrapper, shared with its other
    // readers), not the lenient `parseJson`: strict UTF-8, no BOM, bounded nesting, well-formed
    // strings/keys -- the same guard Python's `load_bundle` already gets from
    // `parse_untrusted_json` and Java's `Bundle.load` now gets from `Verify.parseUntrustedJson`
    // (18.65). `keepNumberTokens` so a float survives to `digest`'s lazy canonicalize check instead
    // of being silently folded.
    parsed = readUntrustedJsonFile(manifestPath, true);
  } catch {
    // A fixed message, not the parser's own text: the three engines' JSON readers each produce
    // different exception text for the same malformed input, which would make this refusal's
    // cause diverge across engines for no reason a reader could use (F3, 18.65).
    throw new InputError(
      "input.bundle_manifest_invalid",
      "manifest.json is not valid JSON.",
      "regenerate the bundle so its manifest.json is well-formed.",
    );
  }
  if (!isRecord(parsed)) {
    throw new InputError(
      "input.bundle_manifest_invalid",
      "manifest.json is not an object.",
      "regenerate the bundle so its manifest.json is well-formed.",
    );
  }
  const manifest: Record<string, unknown> = parsed;

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
    if (
      !isRecord(entry) ||
      !("path" in entry) ||
      !("sha256" in entry) ||
      // A non-string path/sha256 folds into the same refusal as a missing one: `String()`,
      // `JSON.stringify` and Jackson's `asText()` disagree on how to render an array, object or
      // null, which would make the following messages diverge across engines for no reason a
      // reader could use.
      typeof entry.path !== "string" ||
      typeof entry.sha256 !== "string"
    ) {
      throw new InputError(
        "input.bundle_manifest_entry",
        "a 'files' entry is missing 'path' or 'sha256'.",
        'each entry needs {"path": ..., "sha256": ...}.',
      );
    }
    const rel = entry.path;
    const member = safeMember(bundleDir, rel);
    if (!safeIsFile(member)) {
      if (permissionDenied(member)) {
        throw bundleUnreadable(bundleDir, rel);
      }
      throw new InputError(
        "input.bundle_manifest_mismatch",
        `manifest lists ${rel}, which is missing from the bundle.`,
        "regenerate the bundle so its files match the manifest.",
      );
    }
    let actual: string;
    try {
      actual = fileSha256Hex(member);
    } catch (err) {
      if (isPermissionError(err)) {
        throw bundleUnreadable(bundleDir, rel);
      }
      throw err;
    }
    if (actual !== normaliseDigest(entry.sha256)) {
      throw new InputError(
        "input.bundle_manifest_mismatch",
        `${rel} does not match its manifest SHA-256.`,
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
      // A non-string `id` (an array, object, number, null) folds into the same "not declared" skip
      // as a missing one, rather than coercing with `String()`: Python's `str()`, TypeScript's
      // `String()` and Java's Jackson `asText()` each render a non-string JSON value differently,
      // which would make a tampered source's trust classification diverge across engines for no
      // reason a reader could use (verifier round 1, 18.65).
      if (!isRecord(item) || typeof item.id !== "string") {
        continue;
      }
      const sourceId = item.id;
      ids.add(sourceId);
      if (typeof item.class === "string" && item.class) {
        sourceClasses.set(sourceId, item.class);
      }
    }
    if (ids.size > 0) {
      sources = ids;
    }
  }

  // Computed at most once, and only if read: `verify --bundle` never reads `digest`, and
  // canonicalizing the manifest throws on a float anywhere in it (Python's `Bundle.digest` is a
  // lazy `@property` for the same reason, Java's a memoizing method -- 18.65). `assess`/`conformance`
  // read `.digest` repeatedly in one run, so this caches rather than recomputing on every access.
  let digestCache: string | undefined;
  return {
    root: bundleDir,
    manifest,
    eventFiles: eventFiles.sort(),
    sources,
    sourceClasses: sourceClasses.size > 0 ? sourceClasses : null,
    get digest(): string {
      if (digestCache === undefined) {
        digestCache = `sha256:${sha256Hex(manifest)}`;
      }
      return digestCache;
    },
  };
}
