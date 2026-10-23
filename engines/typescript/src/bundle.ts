/**
 * Evidence bundle loading and manifest verification (SPEC §8.1, §5.4).
 *
 * A bundle is a directory with `events/*.jsonl`, `attestations/`, `reference/`, and a `manifest.json`
 * listing every file with its SHA-256. Loading verifies the manifest is present and every listed file
 * hashes correctly; a missing or mismatching manifest aborts with an InputError (exit 3).
 */

import { createHash } from "node:crypto";
import {
  constants,
  closeSync,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
  readlinkSync,
  realpathSync,
  statSync,
} from "node:fs";
import { isAbsolute, posix, relative, sep } from "node:path";
import { sha256Hex } from "./canonical";
import { InputError, isPermissionError, readPermissionError, unreadableError } from "./errors";
import { notValid } from "./json";
import { underByIdentity } from "./report";
import { joinPath } from "./util";
import { parseUntrustedJson } from "./verify";

export interface Bundle {
  root: string;
  manifest: Record<string, unknown>;
  eventFiles: string[];
  /** The bytes of each listed member (keyed by its path under `root`), exactly as `loadBundle` checked and hashed
   * them: ingest uses these, never a second read of the same name (18.184f). */
  contents: Map<string, Buffer>;
  /** The same bytes keyed by each listed path as the manifest writes it (the coverage denominator's lookup). */
  members: Map<string, Buffer>;
  sources: Set<string> | null;
  sourceClasses: Map<string, string> | null;
  digest: string;
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
 * path, a symlink loop, or an embedded NUL byte are refused the same deliberate way everywhere,
 * never left to surface as an unexpected error. A path that cannot be looked at (missing, too long,
 * under a file or an unreadable folder) is returned for the caller's own checks to report.
 */
export function confineToRoot(root: string, rel: string): string | null {
  if (!rel || rel.startsWith("/") || rel.split("/").includes("..")) {
    return null;
  }
  // joinPath drops a trailing slash, as Python's `root / rel` does: `name/` names the file `name`.
  const candidate = joinPath(root, rel);
  let resolvedRoot: string;
  try {
    resolvedRoot = realpathSync.native(root);
  } catch {
    return null;
  }
  // The member resolves the way Python's does, so a link is judged by where it points even when
  // what it points at cannot be read.
  const resolved = walkLoose(resolvedRoot, rel, new Map());
  if (resolved === null) {
    return null;
  }
  const inside = relative(resolvedRoot, resolved);
  if (
    inside !== "" &&
    (inside === ".." || inside.startsWith(`..${sep}`) || isAbsolute(inside)) &&
    // The same folder spelled another way (letter case), judged by identity (18.184l).
    !underByIdentity(resolvedRoot, resolved)
  ) {
    return null;
  }
  return candidate;
}

/**
 * `path` with every symlink it can follow resolved, as Python's `Path.resolve(strict=False)` does
 * (`posixpath._joinrealpath`): a component that cannot be looked at (missing, not a folder, a name
 * too long, inside a folder that cannot be listed) is kept as written, with the rest of the path, so
 * a link that leaves the root is seen to leave it even when its target cannot be read. `null` where
 * Python's `resolve()` raises: a symlink loop, a link that cannot be read, or a name no path can
 * hold (18.68). A relative path is walked from the working directory as written, never through
 * `posix.join`, which would fold `lnk/..` before the link is followed (18.182).
 */
export function resolveLoose(path: string): string | null {
  return walkLoose("/", isAbsolute(path) ? path : `${process.cwd()}/${path}`, new Map());
}

/** `rest` resolved from the already resolved folder `start`, for {@link resolveLoose}; `seen` maps
 * each link to what it resolved to, or `null` while it is being resolved (a loop). */
function walkLoose(start: string, rest: string, seen: Map<string, string | null>): string | null {
  let current = rest.startsWith("/") ? "/" : start;
  for (const name of rest.split("/")) {
    if (!name || name === ".") {
      continue;
    }
    if (name === "..") {
      current = posix.dirname(current);
      continue;
    }
    const next = posix.join(current, name);
    let isLink = false;
    try {
      isLink = lstatSync(next).isSymbolicLink();
    } catch (err) {
      if (typeof (err as NodeJS.ErrnoException | null)?.errno !== "number") {
        return null; // not an OS refusal: a name no path can hold (an embedded NUL)
      }
    }
    if (!isLink) {
      current = next;
      continue;
    }
    const cached = seen.get(next);
    if (cached === null) {
      return null; // a symlink loop
    }
    if (cached === undefined) {
      seen.set(next, null);
      let target: string;
      try {
        target = readlinkSync(next);
      } catch {
        return null;
      }
      const inner = walkLoose(current, target, seen);
      if (inner === null) {
        return null;
      }
      seen.set(next, inner);
      current = inner;
    } else {
      current = cached;
    }
  }
  return current;
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

/** Test seams for 18.184f: `afterConfine` runs once a member's confinement check has passed and before it is
 * opened, `afterCheck` once the open file has been checked again and before it is read. */
export const bundleReadHooks: {
  afterConfine?: (rel: string, member: string) => void;
  afterCheck?: (rel: string, member: string) => void;
} = {};

function changedWhileRead(rel: string): InputError {
  return new InputError(
    "input.bundle_manifest_path",
    `${rel} changed while it was being read: the file opened is not the one checked to be inside the bundle root.`,
    "keep the bundle folder unchanged while the command reads it, then run the command again.",
  );
}

/**
 * The bytes of `member`, the path `confineToRoot` returned for `rel`, read through one open file, and only when,
 * once that file is open, `rel` still resolves inside `root` to the very file opened (same device and inode). A
 * member swapped between the confinement check and the read (for a link outside the bundle, or for any other file)
 * is refused with `input.bundle_manifest_path` and its bytes are never used, as Python's `read_confined` (18.184f).
 * An error from opening or reading propagates for the caller to report.
 */
export function readConfined(root: string, rel: string, member: string): Buffer {
  bundleReadHooks.afterConfine?.(rel, member);
  const fd = openSync(member, constants.O_RDONLY | (constants.O_NONBLOCK ?? 0));
  try {
    const opened = fstatSync(fd, { bigint: true });
    const again = confineToRoot(root, rel);
    let same = false;
    if (again !== null && opened.isFile()) {
      try {
        const now = statSync(again, { bigint: true });
        same = now.dev === opened.dev && now.ino === opened.ino;
      } catch {
        same = false;
      }
    }
    if (!same) {
      throw changedWhileRead(rel);
    }
    bundleReadHooks.afterCheck?.(rel, member);
    return readFileSync(fd);
  } finally {
    closeSync(fd);
  }
}

function safeMember(root: string, rel: string): string {
  const member = confineToRoot(root, rel);
  if (member === null) {
    throw new InputError(
      "input.bundle_manifest_path",
      `manifest lists an unsafe path ${rel}: it is absolute, contains '..', resolves outside the bundle root (a symlink or junction escapes it), or cannot be safely resolved.`,
      "the manifest must list only paths that stay inside the bundle after symlinks resolve.",
    );
  }
  return member;
}

export function loadBundle(bundleDir: string): Bundle {
  const manifestPath = joinPath(bundleDir, "manifest.json");
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
  if (confineToRoot(bundleDir, "manifest.json") === null) {
    throw new InputError(
      "input.bundle_manifest_path",
      "manifest.json resolves outside the bundle root (a symlink or junction escapes it), so the bundle's list of files does not travel with the bundle.",
      "replace the manifest.json link with the manifest file itself, inside the bundle folder.",
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
    // (18.65), under the JSON profile with a byte-order mark ignored (SPEC 8.7). Number kinds are
    // kept, so a float survives to `digest`'s lazy canonicalize check instead of being folded.
    parsed = parseUntrustedJson(readConfined(bundleDir, "manifest.json", manifestPath), "ignore");
  } catch (err) {
    if (err instanceof InputError) {
      throw err;
    }
    // The profile rule and line, the same in the three engines; never the parser's own text (F3, 18.65).
    throw new InputError(
      "input.bundle_manifest_invalid",
      notValid("manifest.json", err),
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
  const contents = new Map<string, Buffer>();
  const members = new Map<string, Buffer>();
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
    let data: Buffer;
    try {
      data = readConfined(bundleDir, rel, member);
    } catch (err) {
      if (isPermissionError(err)) {
        throw bundleUnreadable(bundleDir, rel);
      }
      throw err;
    }
    if (createHash("sha256").update(data).digest("hex") !== normaliseDigest(entry.sha256)) {
      throw new InputError(
        "input.bundle_manifest_mismatch",
        `${rel} does not match its manifest SHA-256.`,
        "regenerate the bundle so its files match the manifest.",
      );
    }
    contents.set(member, data);
    members.set(rel, data);
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
    contents,
    members,
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
