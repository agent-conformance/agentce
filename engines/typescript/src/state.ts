/**
 * The incremental-assessment state directory (SPEC §5.4 B7, §9.6, HR-10).
 *
 * A faithful port of the Python reference `state.py`: an index of ingested bundle digests and the
 * identity of the last report written. Re-running over the same bundle digest is a no-op (SPEC §8.2
 * #8); a changed bundle — most importantly late-arriving evidence inside an already-assessed window —
 * supersedes the prior report (HR-10), and late events are counted per integrity stream. The directory
 * carries a `state_version`; an incompatible version aborts with an InputError (exit 3) that names the
 * migration command. `state.json` is written in the same shape and bytes as the Python engine, so a
 * state directory is portable across engines.
 */

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { InputError } from "./errors";
import { byteCompare, sortKeysDeep } from "./util";

export const STATE_VERSION = 1;
const STATE_FILE = "state.json";

type Event = Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function streamOf(event: Event): string {
  const data = event.data;
  if (isRecord(data)) {
    const block = data.integrity;
    if (isRecord(block) && typeof block.stream === "string") {
      return block.stream;
    }
  }
  return `${String(event.source ?? "")}|${String(event.subject ?? "")}`;
}

/** The observation window's end: the declared end, else the latest event time, else the epoch. */
export function windowEnd(observationWindow: Record<string, string>, events: Event[]): string {
  if ("end" in observationWindow) {
    return String(observationWindow.end);
  }
  const times = events
    .map((e) => String(e.time ?? ""))
    .filter((t) => t)
    .sort(byteCompare);
  return times.length > 0 ? (times[times.length - 1] as string) : "1970-01-01T00:00:00Z";
}

export class StateDir {
  readonly path: string;
  version = STATE_VERSION;
  bundleDigests: string[] = [];
  lastReportDigest: string | null = null;
  lastWindowEnd: string | null = null;

  private constructor(path: string) {
    this.path = path;
  }

  static load(path: string): StateDir {
    const state = new StateDir(path);
    const file = join(path, STATE_FILE);
    if (!isFile(file)) {
      return state;
    }
    const data = JSON.parse(readFileSync(file, "utf-8"));
    const version = Number(data.state_version ?? 0);
    if (version !== STATE_VERSION) {
      throw new InputError(
        "input.state_version_incompatible",
        `the state directory at ${path} is state_version ${version}, but this engine writes state_version ${STATE_VERSION}.`,
        "there is no migration command: move or delete the state directory and re-run " +
          "with --state pointing at a fresh, empty directory (this discards the prior " +
          "bundle/outcome history recorded there, so late-arriving evidence and drift are " +
          "tracked only from that point forward).",
      );
    }
    state.version = version;
    state.bundleDigests = Array.isArray(data.bundle_digests) ? data.bundle_digests.map(String) : [];
    state.lastReportDigest =
      typeof data.last_report_digest === "string" ? data.last_report_digest : null;
    state.lastWindowEnd = typeof data.last_window_end === "string" ? data.last_window_end : null;
    return state;
  }

  save(): void {
    mkdirSync(this.path, { recursive: true });
    const payload = {
      state_version: STATE_VERSION,
      bundle_digests: [...new Set(this.bundleDigests)].sort(byteCompare),
      last_report_digest: this.lastReportDigest,
      last_window_end: this.lastWindowEnd,
    };
    writeFileSync(
      join(this.path, STATE_FILE),
      `${JSON.stringify(sortKeysDeep(payload), null, 2)}\n`,
    );
  }

  /** Return [supersedes, lateEventsByStream] for a new assessment against this state (HR-10). */
  plan(
    bundleDigest: string,
    accepted: Event[],
    _newWindowEnd: string,
  ): [string[], Record<string, number>] {
    if (this.lastReportDigest === null || this.bundleDigests.includes(bundleDigest)) {
      return [[], {}];
    }
    const late = new Map<string, number>();
    const priorEnd = this.lastWindowEnd;
    if (priorEnd !== null) {
      for (const event of accepted) {
        const time = String(event.time ?? "");
        if (time && time <= priorEnd) {
          const stream = streamOf(event);
          late.set(stream, (late.get(stream) ?? 0) + 1);
        }
      }
    }
    const lateSorted: Record<string, number> = {};
    for (const key of [...late.keys()].sort(byteCompare)) {
      lateSorted[key] = late.get(key) as number;
    }
    return [[this.lastReportDigest], lateSorted];
  }

  /** Record the assessment: index the bundle digest and remember the new report's manifest digest. */
  record(bundleDigest: string, manifestPath: string, newWindowEnd: string): string {
    const digest = `sha256:${createHash("sha256").update(readFileSync(manifestPath)).digest("hex")}`;
    if (!this.bundleDigests.includes(bundleDigest)) {
      this.bundleDigests.push(bundleDigest);
    }
    this.lastReportDigest = digest;
    this.lastWindowEnd = newWindowEnd;
    this.save();
    return digest;
  }
}
