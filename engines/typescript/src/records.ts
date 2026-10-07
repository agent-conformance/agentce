/** Records folders: `agentce assess <folder>` reads the records it is given (SPEC §12, §13.4 AX-1),
 * byte-identical with the Python reference's `agentce.records`. */

import { createHash } from "node:crypto";
import {
  type Stats,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join, posix } from "node:path";
import { summarizeActivity } from "./activity";
import { confineToRoot, resolveLoose, safeIsFile } from "./bundle";
import { CanonicalizationError, canonicalString } from "./canonical";
import { InputError } from "./errors";
import { type AdaptResult, OtelGenaiAdapterError, adapt } from "./otelGenai";
import { type Profile, profileFromDict } from "./profile";
import { safeDump } from "./pyyaml";
import { hasInvisibleCodepoint } from "./report";
import {
  byteCompare,
  isPrintableCodePoint,
  jsonStringifyAscii,
  pyRepr,
  sortKeysDeep,
} from "./util";

type Event = Record<string, unknown>;

/** File suffixes read as trace exports: one OTLP/JSON document per `.json` file, one per line in
 * `.jsonl` / `.ndjson`. */
const RECORD_SUFFIXES = new Set([".json", ".jsonl", ".ndjson"]);
/** File suffixes that are archives or compressed exports: named as not read, never opened. */
const COMPRESSED_SUFFIXES = new Set([".gz", ".tgz", ".zip", ".zst", ".bz2", ".xz"]);
const ADAPTER = "otel-genai";
const SOURCE_CLASS = "self_report";
export const BUNDLE_DIR = "records-bundle";
export const DERIVED_PROFILE_FILE = "applicability.yaml";
/** The subject id a scan uses when the records name no agent, or name exactly one by no real id. */
export const DEFAULT_SUBJECT = "agentce:subject/local";
/** The cross-standard baseline the derived profile names (Python's `bundled.DEFAULT_LENS`). */
const DEFAULT_LENS = "baseline@2026.09";
/** Python's `bundle.DEFAULT_MAX_MANIFEST_FILE_BYTES`: the per-file size limit. */
const MAX_FILE_BYTES = 512 * 1024 * 1024;
/** How many lines of a JSON Lines file that were not trace exports are named in the summary. */
const MAX_LISTED_LINES = 20;
/** The deepest `[`/`{` nesting a record document may have and still be read (Python's
 * `MAX_RECORD_DEPTH`), counted on its raw bytes outside JSON strings. */
export const MAX_RECORD_DEPTH = 256;
const NESTED_TOO_DEEPLY = "nested too deeply to parse safely";
const FIXED_DETAIL = new Map([
  ["invalid_json", "invalid_json: not valid JSON"],
  ["invalid_encoding", "invalid_encoding: not valid UTF-8"],
]);
const LONE_SURROGATE =
  "invalid_encoding: a string holds a lone surrogate, which UTF-8 cannot carry";
/** Matches only an unpaired surrogate: in `u` mode a valid pair is one code point. */
const LONE_SURROGATE_RE = /[\uD800-\uDFFF]/u;
const NO_GENAI_SPANS = "spans found, but none is a GenAI operation the adapter maps";

/** Recorded in the manifest of every records-folder run (SPEC §8.7). */
export const RECORDS_LIMITATION =
  "assessed from trace records only: traces carry no decision records and no declaration says which " +
  "tool calls are consequential, so a control the records give no population for is reported as " +
  "insufficient evidence rather than not applicable.";

const CLASS_JUSTIFICATION =
  "read from the agent's own trace export; the agent could have written anything in it, so it is " +
  "recorded as self-reported.";

/** Python's `os.strerror` text for the errno codes a file read or write can hit. */
const STRERROR: Record<string, string> = {
  EACCES: "Permission denied",
  EPERM: "Operation not permitted",
  ENOENT: "No such file or directory",
  EISDIR: "Is a directory",
  ENOTDIR: "Not a directory",
  ELOOP: "Too many levels of symbolic links",
  ENAMETOOLONG: "File name too long",
  ENOSPC: "No space left on device",
  EROFS: "Read-only file system",
  EIO: "Input/output error",
};

function strerror(err: unknown): string {
  const e = err as NodeJS.ErrnoException;
  return (e.code !== undefined ? STRERROR[e.code] : undefined) ?? e.message;
}

export interface RecordsSummary {
  adapter: string;
  conventions: string[];
  files: Array<{ path: string; spans: number }>;
  unrecognised: Array<{ path: string; reason: string }>;
  unrecognised_lines: Array<{ path: string; line: number; reason: string }>;
  events: number;
  sources: string[];
  spans: number;
  spans_skipped: number;
  duplicates: number;
  lines_unrecognised: number;
}

/** What a records folder held: the adapted events and the summary of how they were found. */
export class ScannedRecords {
  constructor(
    readonly events: Event[],
    readonly subjects: string[],
    readonly summary: RecordsSummary,
    readonly sources: Map<string, string>,
    readonly streams: Map<string, Buffer>,
  ) {}

  /** The default applicability profile (SPEC §6.5), field for field Python's `ScannedRecords.profile`;
   * every subject shares one `evidence_sources` list, as Python's does, so the YAML carries the same
   * anchor. */
  profile(): Record<string, unknown> {
    const times = this.events.map((e) => String(e.time)).sort(byteCompare);
    const evidenceSources = [...this.sources.keys()].sort(byteCompare).map((source) => ({
      adapter: ADAPTER,
      source,
      class: this.sources.get(source),
      class_justification: CLASS_JUSTIFICATION,
    }));
    const empty: Profile = { profileVersion: 1, observationWindow: {}, subjects: [], catalogs: [] };
    const subjects = this.subjects.map((id) => {
      const seen = summarizeActivity(
        this.events.filter((e) => e.subject === id),
        empty,
      );
      return {
        id,
        name: "Agent records",
        role: "deployer",
        evidence_sources: evidenceSources,
        declared_tools: seen.tools.map((t) => t.name),
        declared_models: seen.models.map((m) => m.name),
      };
    });
    return {
      profile_version: 1,
      observation_window: { start: times[0], end: times[times.length - 1] },
      pilot_window: true,
      catalogs: [DEFAULT_LENS],
      subjects,
    };
  }

  /** Write the evidence bundle under `outDir` (replacing a previous run's) and, when `writeProfile`,
   * the default profile beside it; return the bundle directory. */
  write(outDir: string, writeProfile: boolean): string {
    const root = join(outDir, BUNDLE_DIR);
    try {
      try {
        rmSync(root, { recursive: true, force: true });
      } catch {
        // shutil.rmtree(ignore_errors=True)
      }
      mkdirSync(join(root, "events"), { recursive: true });
      const files: Array<{ path: string; sha256: string }> = [];
      for (const source of [...this.streams.keys()].sort(byteCompare)) {
        const stem = source
          .replace(/[^A-Za-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "")
          .slice(0, 60);
        const digest = createHash("sha256").update(source, "utf-8").digest("hex");
        const name = `events/${stem}-${digest.slice(0, 8)}.jsonl`;
        const data = this.streams.get(source) as Buffer;
        writeFileSync(join(root, name), data);
        files.push({ path: name, sha256: createHash("sha256").update(data).digest("hex") });
      }
      const manifest = {
        agentce_bundle_version: 1,
        files,
        sources: [...this.sources.keys()]
          .sort(byteCompare)
          .map((id) => ({ id, class: this.sources.get(id) })),
      };
      writeFileSync(
        join(root, "manifest.json"),
        `${jsonStringifyAscii(sortKeysDeep(manifest), 2)}\n`,
        "utf-8",
      );
      if (writeProfile) {
        writeFileSync(
          join(outDir, DERIVED_PROFILE_FILE),
          `# The default profile \`agentce assess <folder>\` derived from the records it found.\n# Edit it (declare your agents, oversight and catalogs) and pass it back with --profile.\n${safeDump(this.profile())}`,
          "utf-8",
        );
      }
    } catch (err) {
      throw new InputError(
        "input.out_dir_unwritable",
        strerror(err),
        "choose a writable --out directory.",
      );
    }
    return root;
  }
}

/** The deepest `[`/`{` nesting in `payload`'s bytes, not counting brackets inside JSON strings. */
export function rawDepth(payload: Uint8Array): number {
  let depth = 0;
  let deepest = 0;
  let inString = false;
  let escaped = false;
  for (const byte of payload) {
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (byte === 0x5c) {
        escaped = true;
      } else if (byte === 0x22) {
        inString = false;
      }
    } else if (byte === 0x22) {
      inString = true;
    } else if (byte === 0x5b || byte === 0x7b) {
      depth += 1;
      deepest = Math.max(deepest, depth);
    } else if (byte === 0x5d || byte === 0x7d) {
      depth -= 1;
    }
  }
  return deepest;
}

/** Why a document is not a trace export, as Python's `_adapt` raises it. */
class NotRecord extends Error {}

/** Adapt one OTLP/JSON document; throw {@link NotRecord} with the reason when it is not a trace export. */
function adaptDocument(payload: Uint8Array, subject: string): AdaptResult {
  if (rawDepth(payload) > MAX_RECORD_DEPTH) {
    throw new NotRecord(NESTED_TOO_DEEPLY);
  }
  let result: AdaptResult;
  try {
    result = adapt(payload, { subject, sourceClass: SOURCE_CLASS });
    for (const event of result.events) {
      if (LONE_SURROGATE_RE.test(canonicalString(event))) {
        throw new NotRecord(LONE_SURROGATE);
      }
    }
  } catch (exc) {
    if (exc instanceof OtelGenaiAdapterError) {
      throw new NotRecord(FIXED_DETAIL.get(exc.reason) ?? exc.message);
    }
    if (exc instanceof CanonicalizationError) {
      throw new NotRecord(exc.message);
    }
    throw exc;
  }
  if (result.events.length === 0) {
    throw new NotRecord(
      result.report.spansSeen === 0
        ? "no spans: not an OpenTelemetry or OpenInference trace export"
        : NO_GENAI_SPANS,
    );
  }
  return result;
}

/** Python's `Path.suffix`, lower-cased. */
function suffix(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 && i < name.length - 1 ? name.slice(i).toLowerCase() : "";
}

function statOrNull(path: string): Stats | null {
  try {
    return statSync(path);
  } catch {
    return null;
  }
}

/** True when `a` and `b` are the same file on disk, however each is spelled; a path that does not
 * exist is the same as nothing (Python's `os.path.samefile` under `_same`). */
function same(a: string, b: string): boolean {
  const sa = statOrNull(a);
  const sb = statOrNull(b);
  return sa !== null && sb !== null && sa.dev === sb.dev && sa.ino === sb.ino;
}

/** True when `path` is `root` or lies under it, compared by identity on disk. */
function within(path: string, root: string): boolean {
  for (let p = path; ; p = dirname(p)) {
    if (same(p, root)) {
      return true;
    }
    if (dirname(p) === p) {
      return false;
    }
  }
}

function isSymlink(path: string): boolean {
  try {
    return lstatSync(path).isSymbolicLink();
  } catch {
    return false;
  }
}

function isDir(path: string): boolean {
  return statOrNull(path)?.isDirectory() ?? false;
}

/** The record files under `folder` in `os.walk`'s top-down order (hidden names and the `skip` trees
 * are not walked); `null` marks a symlink that leaves the folder. Compressed files and symlinked
 * folders, which are never read, are named in `unread` with why. */
function candidates(
  folder: string,
  skip: string[],
  unread: Array<{ path: string; reason: string }>,
): Array<[string, string | null]> {
  const found: Array<[string, string | null]> = [];
  const rel = (path: string): string => posix.relative(folder, path);
  const walk = (here: string): void => {
    let entries: string[];
    try {
      entries = readdirSync(here);
    } catch {
      return; // os.walk skips a folder it cannot list
    }
    const dirnames: string[] = [];
    const filenames: string[] = [];
    for (const name of entries) {
      (isDir(join(here, name)) ? dirnames : filenames).push(name);
    }
    const kept: string[] = [];
    for (const d of dirnames.sort(byteCompare)) {
      const path = join(here, d);
      if (d.startsWith(".") || skip.some((tree) => same(path, tree))) {
        continue;
      }
      if (isSymlink(path)) {
        unread.push({ path: rel(path), reason: "a symlinked folder is not followed" });
        continue;
      }
      kept.push(d);
    }
    for (const name of filenames.sort(byteCompare)) {
      const path = join(here, name);
      if (name.startsWith(".")) {
        continue;
      }
      if (COMPRESSED_SUFFIXES.has(suffix(name))) {
        unread.push({
          path: rel(path),
          reason: "compressed files are not read; decompress them first",
        });
        continue;
      }
      if (!RECORD_SUFFIXES.has(suffix(name)) || !safeIsFile(path)) {
        continue;
      }
      found.push([rel(path), confineToRoot(folder, rel(path))]);
    }
    for (const d of kept) {
      walk(join(here, d));
    }
  };
  walk(folder);
  return found;
}

/** Python's `bytes.splitlines()`: lines end at `\n`, `\r\n` or `\r`. */
function splitLines(raw: Buffer): Buffer[] {
  const lines: Buffer[] = [];
  let start = 0;
  for (let i = 0; i < raw.length; i++) {
    const byte = raw[i];
    if (byte === 0x0a || byte === 0x0d) {
      lines.push(raw.subarray(start, i));
      if (byte === 0x0d && raw[i + 1] === 0x0a) {
        i++;
      }
      start = i + 1;
    }
  }
  if (start < raw.length) {
    lines.push(raw.subarray(start));
  }
  return lines;
}

/** Python's `bytes.strip()` is non-empty: the line holds a byte other than ASCII whitespace. */
function hasContent(line: Buffer): boolean {
  return line.some((b) => !(b === 0x20 || (b >= 0x09 && b <= 0x0d)));
}

interface FileRead {
  results: AdaptResult[];
  badLines: Array<[number, string]>;
}

/** Read one record file; return why it cannot be read at all instead when it cannot. */
function readRecordFile(path: string, subject: string): FileRead | string {
  let raw: Buffer;
  try {
    const size = statSync(path).size;
    if (size > MAX_FILE_BYTES) {
      return `${size} bytes is over the ${MAX_FILE_BYTES}-byte limit`;
    }
    raw = readFileSync(path);
  } catch (err) {
    return `could not be read: ${strerror(err)}`;
  }
  const documents: Array<[number, Buffer]> =
    suffix(basename(path)) === ".json"
      ? [[1, raw]]
      : splitLines(raw)
          .map((line, i): [number, Buffer] => [i + 1, line])
          .filter(([, line]) => hasContent(line));
  const read: FileRead = { results: [], badLines: [] };
  for (const [number, document] of documents) {
    try {
      read.results.push(adaptDocument(document, subject));
    } catch (exc) {
      if (!(exc instanceof NotRecord)) {
        throw exc;
      }
      read.badLines.push([number, exc.message]);
    }
  }
  return read;
}

const OUT_COLLIDES_FIX =
  "choose an output folder outside the records folder with --out, or an empty one.";

export interface ScanOptions {
  /** The one subject an adopter's `--profile` declares, forced onto every event; `undefined`
   * discovers subjects from each event's own `gen_ai.agent.id`. */
  subject?: string;
  /** The run's `--out`. */
  exclude?: string;
  /** A profile declaring several subjects: keep each event on its own agent id (18.77). */
  perAgent?: boolean;
}

/** The event's own `gen_ai.agent.id`, as the adapter stamped it onto `data.agent`. */
function realId(event: Event): string | undefined {
  const data = event.data as Record<string, unknown> | undefined;
  const agent =
    data !== null && typeof data === "object" && !Array.isArray(data) ? data.agent : null;
  const id =
    agent !== null && typeof agent === "object" && !Array.isArray(agent)
      ? (agent as Record<string, unknown>).id
      : undefined;
  return typeof id === "string" ? id : undefined;
}

/** Read every recognised trace export under `folder`; refuse a folder with none (Python's `scan`). */
export function scanRecords(folderArg: string, options: ScanOptions = {}): ScannedRecords {
  const folder = realpathSync(folderArg);
  const skip: string[] = [];
  if (options.exclude !== undefined) {
    const out = resolveLoose(options.exclude) ?? options.exclude;
    const stale = join(out, BUNDLE_DIR);
    skip.push(stale);
    if (
      within(folder, stale) ||
      isSymlink(stale) ||
      (isDir(stale) && !safeIsFile(join(stale, "manifest.json")))
    ) {
      throw new InputError(
        "input.records_out_collides",
        `${pyRepr(BUNDLE_DIR)} in the output folder is where the run's bundle is rebuilt, and it holds the records or is not a previous run's bundle; writing there would overwrite records.`,
        OUT_COLLIDES_FIX,
      );
    }
    if (within(out, folder)) {
      if (
        same(out, folder) ||
        (statOrNull(out) !== null && !isDir(stale) && readdirSync(out).length > 0)
      ) {
        throw new InputError(
          "input.records_out_collides",
          `the output folder ${pyRepr(basename(out))} is the records folder or lies inside it and holds files that are not a previous run's output; writing there would overwrite records.`,
          OUT_COLLIDES_FIX,
        );
      }
      skip.push(out);
    }
  }
  const byId = new Map<string, Event>();
  const read: Array<{ path: string; spans: number }> = [];
  const unrecognised: Array<{ path: string; reason: string }> = [];
  const badLines: Array<{ path: string; line: number; reason: string }> = [];
  const counts = { spans: 0, spans_skipped: 0, duplicates: 0, lines_unrecognised: 0 };
  const conventions = new Set<string>();

  const found = candidates(folder, skip, unrecognised);
  for (const [rel, path] of found) {
    if (path === null) {
      unrecognised.push({ path: rel, reason: "a symlink that leaves the folder is not read" });
      continue;
    }
    const got = readRecordFile(path, options.subject ?? DEFAULT_SUBJECT);
    if (typeof got === "string") {
      unrecognised.push({ path: rel, reason: got });
      continue;
    }
    if (got.results.length === 0) {
      unrecognised.push({ path: rel, reason: got.badLines[0]?.[1] ?? "empty file" });
      continue;
    }
    let spans = 0;
    for (const result of got.results) {
      spans += result.report.spansSeen;
      counts.spans_skipped += result.report.skipped.length;
      for (const c of result.report.conventions) {
        conventions.add(c);
      }
      for (const event of result.events) {
        const id = String(event.id);
        if (byId.has(id)) {
          counts.duplicates += 1;
        } else {
          byId.set(id, event);
        }
      }
    }
    counts.spans += spans;
    read.push({ path: rel, spans });
    if (suffix(basename(path)) !== ".json") {
      counts.lines_unrecognised += got.badLines.length;
      for (const [line, reason] of got.badLines.slice(0, MAX_LISTED_LINES - badLines.length)) {
        badLines.push({ path: rel, line, reason });
      }
    }
  }

  let subjects: string[];
  if (options.subject !== undefined) {
    subjects = [options.subject];
  } else {
    const realIds = new Set<string>();
    for (const event of byId.values()) {
      const id = realId(event);
      if (id !== undefined) {
        realIds.add(id);
      }
    }
    if (options.perAgent || realIds.size >= 2) {
      // One subject per id, plus the catch-all last only if some event carries no id at all: never
      // guess an id-less event into a named agent.
      subjects = [...realIds].sort(byteCompare);
      if (
        !realIds.has(DEFAULT_SUBJECT) &&
        [...byId.values()].some((e) => realId(e) === undefined)
      ) {
        subjects.push(DEFAULT_SUBJECT);
      }
      for (const event of byId.values()) {
        event.subject = realId(event) ?? DEFAULT_SUBJECT;
      }
    } else {
      const only = realIds.values().next().value ?? DEFAULT_SUBJECT;
      subjects = [only];
      for (const event of byId.values()) {
        event.subject = only;
      }
    }
  }

  if (read.length === 0 && unrecognised.some((item) => item.reason === NO_GENAI_SPANS)) {
    throw new InputError(
      "input.records_no_genai_spans",
      `the folder holds OpenTelemetry traces, but none of their spans is a GenAI operation (${unrecognised.length} file(s) not recognised).`,
      "ask the agent's developer to instrument it with OpenTelemetry GenAI or OpenInference, then export its traces.",
    );
  }
  if (read.length === 0) {
    throw new InputError(
      "input.records_none_recognised",
      `no OpenTelemetry GenAI or OpenInference trace export was found in the folder (${unrecognised.length} candidate file(s) not recognised).`,
      "point assess at a folder of OTLP/JSON trace exports (.json, .jsonl or .ndjson); " +
        "compressed files are not read, so decompress them first.",
    );
  }
  const events = [...byId.values()].sort((a, b) => {
    const byTime = byteCompare(String(a.time), String(b.time));
    return byTime !== 0 ? byTime : byteCompare(String(a.id), String(b.id));
  });
  const sources = new Map<string, string>();
  const lines = new Map<string, string[]>();
  for (const e of events) {
    const source = String(e.source);
    sources.set(source, String(e.agentcesourceclass));
    const chunk = lines.get(source) ?? [];
    chunk.push(`${canonicalString(e)}\n`);
    lines.set(source, chunk);
  }
  const streams = new Map<string, Buffer>();
  for (const [source, chunk] of lines) {
    const data = Buffer.from(chunk.join(""), "utf-8");
    if (data.length > MAX_FILE_BYTES) {
      throw new InputError(
        "input.bundle_manifest_file_too_large",
        `the events of source ${pyRepr(source)} come to ${data.length} bytes, over the ` +
          `${MAX_FILE_BYTES}-byte per-file limit.`,
        "assess the records in smaller folders, or reference bulk content by an opaque " +
          "locator instead of inlining it (SPEC R12).",
      );
    }
    streams.set(source, data);
  }
  const summary: RecordsSummary = {
    adapter: ADAPTER,
    conventions: [...conventions].sort(byteCompare),
    files: read,
    unrecognised,
    unrecognised_lines: badLines,
    events: events.length,
    sources: [...sources.keys()].sort(byteCompare),
    ...counts,
  };
  return new ScannedRecords(events, subjects, summary, sources, streams);
}

/** The subject every record is forced onto: the one subject an adopter's own profile declares, else
 * `undefined` to let {@link scanRecords} put each record on the agent id it carries. */
export function recordsSubject(declared: Profile | undefined): string | undefined {
  if (declared === undefined) {
    return undefined;
  }
  if (declared.subjects.length === 0) {
    throw new InputError(
      "input.profile_invalid",
      "the profile declares no subject, so the records have no agent to be about.",
      "declare one subject in the profile, or leave out --profile to use the default one.",
    );
  }
  const ids = declared.subjects.map((s) => s.id);
  const repeated = [...new Set(ids.filter((id, i) => ids.indexOf(id) !== i))].sort(byteCompare);
  if (repeated.length > 0) {
    throw new InputError(
      "input.profile_invalid",
      `the profile declares the subject ${pyRepr(repeated[0])} more than once.`,
      "declare each subject once in the profile.",
    );
  }
  return ids.length === 1 ? ids[0] : undefined;
}

/** The profile a records run evaluates: the derived one, or the adopter's own plus the derived entry
 * of each agent the scan found that it does not name, with no tools or models declared (18.77). */
export function recordsProfile(declared: Profile | undefined, scanned: ScannedRecords): Profile {
  const derived = profileFromDict(scanned.profile());
  if (declared === undefined) {
    return derived;
  }
  const named = new Set(declared.subjects.map((s) => s.id));
  const extra = derived.subjects
    .filter((s) => !named.has(s.id))
    .map((s) => ({ ...s, declaredTools: [], declaredModels: [] }));
  return { ...declared, subjects: [...declared.subjects, ...extra] };
}

/** Python's `text.encode("unicode_escape").decode("ascii")`. */
function unicodeEscape(text: string): string {
  let out = "";
  for (const ch of text) {
    const cp = ch.codePointAt(0) as number;
    if (ch === "\\") {
      out += "\\\\";
    } else if (ch === "\t") {
      out += "\\t";
    } else if (ch === "\n") {
      out += "\\n";
    } else if (ch === "\r") {
      out += "\\r";
    } else if (cp >= 0x20 && cp < 0x7f) {
      out += ch;
    } else if (cp <= 0xff) {
      out += `\\x${cp.toString(16).padStart(2, "0")}`;
    } else if (cp <= 0xffff) {
      out += `\\u${cp.toString(16).padStart(4, "0")}`;
    } else {
      out += `\\U${cp.toString(16).padStart(8, "0")}`;
    }
  }
  return out;
}

/** `text` with control characters escaped, so a file name cannot drive the terminal (Python's
 * `_printable`). */
function printable(text: string): string {
  const shown = [...text].every((ch) => isPrintableCodePoint(ch.codePointAt(0) as number));
  return !shown || hasInvisibleCodepoint(text) ? unicodeEscape(text) : text;
}

/** What a records-folder run read, what it could not, and where the default profile is (`undefined`
 * when the adopter passed their own). */
export function recordsLines(summary: RecordsSummary, profile: string | undefined): string[] {
  const lines = [
    `read ${summary.events} events from ${summary.files.length} record file(s) ` +
      `(${summary.spans} spans; ${summary.spans_skipped} not a GenAI operation the adapter maps)`,
    ...summary.unrecognised.map(
      (item) => `not read: ${printable(item.path)}: ${printable(item.reason)}`,
    ),
    ...summary.unrecognised_lines.map(
      (item) => `not read: ${printable(item.path)} line ${item.line}: ${printable(item.reason)}`,
    ),
  ];
  if (profile !== undefined) {
    lines.push(
      `default profile: ${printable(profile)} (declare your agents there and pass it back with --profile to refine the run)`,
    );
  }
  return lines;
}
