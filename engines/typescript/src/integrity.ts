/**
 * Integrity verification of evidence streams (SPEC §6.6).
 *
 * An integrity stream is `(source, subject)`; each event carries `data.integrity`
 * `{hash, prev, stream, strength, sig_ref?}`. `hash` is the SHA-256 of the RFC 8785 canonical form of
 * the event with `data.integrity` removed, and `prev` links it to the previous event's hash (`"0"*64`
 * at genesis). Verification recomputes each hash (detecting tampering at the exact index), checks the
 * `prev` chain (gap or reordered), cross-checks source timestamps against anchor times (time_suspect),
 * and reports the stream `strength` and one `status` per stream. Broken streams are never fatal: they
 * are findings the evaluator uses to downgrade dependent assertions (SPEC §8.1). This is a faithful
 * port of the Python reference; its results inform findings and do not feed assertions (SPEC §8.1).
 */

import { confineToRoot, safeIsFile } from "./bundle";
import { sha256Hex } from "./canonical";
import { byteCompare } from "./util";

export const GENESIS_PREV = "0".repeat(64);
export const DEFAULT_CLOCK_SKEW_SECONDS = 300; // 5 minutes (SPEC §6.6 timestamp trust)

export enum IntegrityStrength {
  SOURCE_SIGNED = "source_signed",
  EXPORT_ANCHORED = "export_anchored",
  EXPORT_CHAINED = "export_chained",
}

export enum IntegrityStatus {
  VERIFIED = "verified",
  VERIFIED_WEAK = "verified_weak",
  GAP = "gap",
  REORDERED = "reordered",
  UNSIGNED = "unsigned",
  TIME_SUSPECT = "time_suspect",
  FAILED = "failed",
}

/** The verification outcome for one integrity stream (integrity-result.schema.json). */
export interface IntegrityResult {
  stream: string;
  strength: string;
  status: string;
  firstBadIndex: number | null;
  anchors: Array<Record<string, unknown>>;
}

export function integrityResultToJson(result: IntegrityResult): Record<string, unknown> {
  const record: Record<string, unknown> = {
    stream: result.stream,
    strength: result.strength,
    status: result.status,
  };
  if (result.firstBadIndex !== null) {
    record.first_bad_index = result.firstBadIndex;
  }
  if (result.anchors.length > 0) {
    record.anchors = result.anchors;
  }
  return record;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function integrityBlock(event: Record<string, unknown>): Record<string, unknown> | null {
  const data = event.data;
  if (isRecord(data)) {
    const block = data.integrity;
    if (isRecord(block)) {
      return block;
    }
  }
  return null;
}

function streamKey(event: Record<string, unknown>): string {
  const block = integrityBlock(event);
  if (block !== null && typeof block.stream === "string") {
    return block.stream;
  }
  return `${String(event.source ?? "")}|${String(event.subject ?? "")}`;
}

/** Return the SHA-256 of the canonical event with `data.integrity` removed (SPEC §6.6). */
export function recomputeHash(event: Record<string, unknown>): string {
  const without = structuredClone(event);
  const data = without.data;
  if (isRecord(data)) {
    const stripped: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(data)) {
      if (key !== "integrity") {
        stripped[key] = value;
      }
    }
    without.data = stripped;
  }
  return sha256Hex(without);
}

function parseTime(value: string): number | null {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function anchorTimes(anchors: Array<Record<string, unknown>>): number[] {
  const times: number[] = [];
  for (const anchor of anchors) {
    const parsed = parseTime(String(anchor.at ?? ""));
    if (parsed !== null) {
      times.push(parsed);
    }
  }
  return times;
}

function isSigned(block: Record<string, unknown> | null, bundleRoot: string | null): boolean {
  if (block === null) {
    return false;
  }
  const sigRef = block.sig_ref;
  if (typeof sigRef !== "string" || !sigRef) {
    return false;
  }
  if (bundleRoot === null) {
    return true;
  }
  // A sig_ref is evidence content: confine it to the bundle root so a signature file outside it
  // (reached literally or through a symlink) never counts as a verified signature.
  const confined = confineToRoot(bundleRoot, sigRef);
  return confined !== null && safeIsFile(confined);
}

function verifyStream(
  stream: string,
  events: Array<Record<string, unknown>>,
  anchors: Array<Record<string, unknown>>,
  bundleRoot: string | null,
  shared: boolean,
): IntegrityResult {
  const blocks = events.map(integrityBlock);
  let strength: string = IntegrityStrength.EXPORT_CHAINED;
  for (const block of blocks) {
    if (block !== null && typeof block.strength === "string") {
      strength = block.strength;
      break;
    }
  }
  const result: IntegrityResult = {
    stream,
    strength,
    status: "",
    firstBadIndex: null,
    anchors,
  };

  // 1. Tamper: a recomputed hash that does not match the stored one.
  for (let index = 0; index < events.length; index += 1) {
    const block = blocks[index] as Record<string, unknown> | null;
    if (block === null || block.hash !== recomputeHash(events[index] as Record<string, unknown>)) {
      result.status = IntegrityStatus.FAILED;
      result.firstBadIndex = index;
      return result;
    }
  }

  // 2. Chain linkage in arrival order.
  const allHashes = new Set<string>();
  for (const block of blocks) {
    if (block !== null && "hash" in block) {
      allHashes.add(String(block.hash));
    }
  }
  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index] as Record<string, unknown>; // tamper pass guarantees a hash on every event
    const prev = String(block.prev ?? "");
    const want =
      index === 0 ? GENESIS_PREV : String((blocks[index - 1] as Record<string, unknown>).hash);
    if (prev !== want) {
      const broken =
        prev !== GENESIS_PREV && !allHashes.has(prev)
          ? IntegrityStatus.GAP
          : IntegrityStatus.REORDERED;
      result.status = broken;
      result.firstBadIndex = index;
      return result;
    }
  }

  // 3. Timestamp trust: an event later than the anchor that covers it is suspect.
  const times = anchorTimes(anchors);
  if (times.length > 0) {
    const latestAnchor = Math.max(...times);
    for (let index = 0; index < events.length; index += 1) {
      const eventTime = parseTime(String((events[index] as Record<string, unknown>).time ?? ""));
      if (eventTime !== null && eventTime > latestAnchor) {
        result.status = IntegrityStatus.TIME_SUSPECT;
        result.firstBadIndex = index;
        return result;
      }
    }
  }

  // 4. Strength and signatures.
  if (strength === IntegrityStrength.SOURCE_SIGNED) {
    result.status = blocks.every((block) => isSigned(block, bundleRoot))
      ? IntegrityStatus.VERIFIED
      : IntegrityStatus.UNSIGNED;
  } else if (strength === IntegrityStrength.EXPORT_ANCHORED) {
    result.status = anchors.length > 0 ? IntegrityStatus.VERIFIED : IntegrityStatus.VERIFIED_WEAK;
  } else {
    // export_chained: a chain computed after export proves only "unchanged since export".
    result.status = IntegrityStatus.VERIFIED_WEAK;
  }
  // A stream id two sources claim: the manifest's anchors and a sig_ref name no source, so neither chain under that
  // id reads verified, which fails closed rather than let one source's chain borrow another's anchors (18.151).
  if (shared && result.status === IntegrityStatus.VERIFIED) {
    result.status = IntegrityStatus.VERIFIED_WEAK;
  }
  return result;
}

function anchorsByStream(
  manifest: Record<string, unknown>,
): Map<string, Array<Record<string, unknown>>> {
  const out = new Map<string, Array<Record<string, unknown>>>();
  const streams = manifest.streams;
  if (Array.isArray(streams)) {
    for (const entry of streams) {
      if (isRecord(entry) && typeof entry.stream === "string") {
        out.set(entry.stream, Array.isArray(entry.anchors) ? entry.anchors : []);
      }
    }
  }
  return out;
}

/**
 * Verify every integrity stream in `events`; return one result per stream, ordered by stream id, then source.
 *
 * A chain is keyed on (the record's source, the stream it claims): a claimed stream id names a stream of the record's
 * own source (SPEC §6.6), so a record never joins, or breaks, another source's chain by naming it, and no chain under
 * an id two sources claim reads verified (18.151).
 */
export function verifyBundle(
  events: Array<Record<string, unknown>>,
  manifest: Record<string, unknown>,
  bundleRoot: string | null = null,
): IntegrityResult[] {
  const anchors = anchorsByStream(manifest);
  const grouped = new Map<
    string,
    { stream: string; source: string; events: Array<Record<string, unknown>> }
  >();
  for (const event of events) {
    const stream = streamKey(event);
    const source = String(event.source ?? "");
    const key = JSON.stringify([stream, source]);
    const chain = grouped.get(key);
    if (chain === undefined) {
      grouped.set(key, { stream, source, events: [event] });
    } else {
      chain.events.push(event);
    }
  }

  const claimers = new Map<string, number>();
  for (const chain of grouped.values()) {
    claimers.set(chain.stream, (claimers.get(chain.stream) ?? 0) + 1);
  }
  const chains = [...grouped.values()].sort(
    (a, b) => byteCompare(a.stream, b.stream) || byteCompare(a.source, b.source),
  );
  return chains.map((chain) =>
    verifyStream(
      chain.stream,
      chain.events,
      anchors.get(chain.stream) ?? [],
      bundleRoot,
      (claimers.get(chain.stream) ?? 0) > 1,
    ),
  );
}
