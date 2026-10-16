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
import { compareInstants } from "./structural";
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
  /**
   * What the strength and signatures alone read (verified, verified_weak or unsigned), kept when the time check has
   * already made the status time_suspect, so a stream's weakness still caps (SPEC §6.6). Not written out.
   */
  strengthStatus: string;
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

/** Any aware date-time; an anchor time comparable with it is a UTC instant (compareInstants). */
const EPOCH = "1970-01-01T00:00:00Z";

/**
 * The latest anchor time, compared as instants (SPEC §6.7); an anchor time with no UTC offset, or none at all, places
 * no record and is left out.
 */
function latestAnchor(anchors: Array<Record<string, unknown>>): string | null {
  let latest: string | null = null;
  for (const anchor of anchors) {
    const at = String(anchor.at ?? "");
    if (
      compareInstants(at, EPOCH) !== null &&
      (latest === null || compareInstants(at, latest) === 1)
    ) {
      latest = at;
    }
  }
  return latest;
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
    strengthStatus: "",
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

  // An anchor whose time cannot be placed (no UTC offset, absent, or not a date-time) anchors nothing (SPEC §8.1).
  const latest = latestAnchor(anchors);
  result.strengthStatus = strengthStatus(strength, blocks, latest !== null, bundleRoot);

  // 3. Timestamp trust: an event later than the anchor that covers it is suspect, and so is one whose time cannot be
  // placed against it (no UTC offset, or not a date-time): it cannot be cross-checked (SPEC §6.6, fail closed).
  // A stream whose anchors are all unplaceable cannot be cross-checked at all, so it is suspect as a whole.
  if (anchors.length > 0 && latest === null) {
    result.status = IntegrityStatus.TIME_SUSPECT;
    return result;
  }
  if (latest !== null) {
    for (let index = 0; index < events.length; index += 1) {
      const order = compareInstants(
        String((events[index] as Record<string, unknown>).time ?? ""),
        latest,
      );
      if (order === 1 || order === null) {
        result.status = IntegrityStatus.TIME_SUSPECT;
        result.firstBadIndex = index;
        return result;
      }
    }
  }

  result.status = result.strengthStatus;
  return result;
}

/**
 * What a stream's strength and signatures read: source_signed is verified only when every record is signed,
 * export_anchored only with a placeable anchor, and export_chained proves only "unchanged since export".
 */
function strengthStatus(
  strength: string,
  blocks: Array<Record<string, unknown> | null>,
  anchored: boolean,
  bundleRoot: string | null,
): string {
  if (strength === IntegrityStrength.SOURCE_SIGNED) {
    return blocks.every((block) => isSigned(block, bundleRoot))
      ? IntegrityStatus.VERIFIED
      : IntegrityStatus.UNSIGNED;
  }
  if (strength === IntegrityStrength.EXPORT_ANCHORED && anchored) {
    return IntegrityStatus.VERIFIED;
  }
  return IntegrityStatus.VERIFIED_WEAK;
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
  return [...verifyStreams(events, manifest, bundleRoot).values()];
}

/** The key of one chain in {@link verifyStreams}' results: its stream id and its source. */
export function chainKey(stream: string, source: string): string {
  return `${stream}\u0000${source}`;
}

/** {@link verifyBundle}'s results keyed by {@link chainKey}, in its order. */
export function verifyStreams(
  events: Array<Record<string, unknown>>,
  manifest: Record<string, unknown>,
  bundleRoot: string | null = null,
): Map<string, IntegrityResult> {
  const anchors = anchorsByStream(manifest);
  const grouped = new Map<string, Map<string, Array<Record<string, unknown>>>>();
  for (const event of events) {
    const stream = streamKey(event);
    const source = String(event.source ?? "");
    let bySource = grouped.get(stream);
    if (bySource === undefined) {
      bySource = new Map();
      grouped.set(stream, bySource);
    }
    const chain = bySource.get(source);
    if (chain === undefined) {
      bySource.set(source, [event]);
    } else {
      chain.push(event);
    }
  }

  const results = new Map<string, IntegrityResult>();
  for (const stream of [...grouped.keys()].sort(byteCompare)) {
    const bySource = grouped.get(stream) as Map<string, Array<Record<string, unknown>>>;
    for (const source of [...bySource.keys()].sort(byteCompare)) {
      const result = verifyStream(
        stream,
        bySource.get(source) as Array<Record<string, unknown>>,
        anchors.get(stream) ?? [],
        bundleRoot,
      );
      // A stream id two sources claim: the manifest's anchors and a sig_ref name no source, so neither chain under
      // that id reads verified, which fails closed rather than let one source's chain borrow another's anchors (18.151).
      if (bySource.size > 1) {
        if (result.status === IntegrityStatus.VERIFIED) {
          result.status = IntegrityStatus.VERIFIED_WEAK;
        }
        if (result.strengthStatus === IntegrityStatus.VERIFIED) {
          result.strengthStatus = IntegrityStatus.VERIFIED_WEAK;
        }
      }
      results.set(chainKey(stream, source), result);
    }
  }
  return results;
}

/** A broken chain: every record on it is read at most as self_report (SPEC §8.1). */
const BROKEN: ReadonlySet<string> = new Set([
  IntegrityStatus.FAILED,
  IntegrityStatus.GAP,
  IntegrityStatus.REORDERED,
]);
/** A chain below verified: an enforcement-point record on it caps a pass at partial (SPEC §6.6, weak_integrity). */
const WEAK: ReadonlySet<string> = new Set([
  IntegrityStatus.VERIFIED_WEAK,
  IntegrityStatus.UNSIGNED,
]);
const CAPPED_CLASSES: ReadonlySet<unknown> = new Set(["enforcement_point", "independent_system"]);

/**
 * Which accepted records an integrity result caps, by event id (SPEC §6.6, §8.1): `broken` holds the
 * enforcement-point and independent-system records on a failed, gap or reordered stream (a record with no integrity
 * member included), `weak` the enforcement-point records on a verified_weak or unsigned stream, and `timeSuspect`
 * every record on a time_suspect stream.
 */
export interface IntegrityCaps {
  broken: ReadonlySet<string>;
  weak: ReadonlySet<string>;
  timeSuspect: ReadonlySet<string>;
}

export const NO_CAPS: IntegrityCaps = {
  broken: new Set(),
  weak: new Set(),
  timeSuspect: new Set(),
};

/**
 * The caps `results` (from {@link verifyStreams} over `rawAccepted`) put on `accepted`, the same records index for
 * index with their trust-corrected class (ingest).
 */
export function integrityCaps(
  rawAccepted: Array<Record<string, unknown>>,
  accepted: Array<Record<string, unknown>>,
  results: Map<string, IntegrityResult>,
): IntegrityCaps {
  const broken = new Set<string>();
  const weak = new Set<string>();
  const suspect = new Set<string>();
  rawAccepted.forEach((raw, index) => {
    const event = accepted[index] as Record<string, unknown>;
    const result = results.get(chainKey(streamKey(raw), String(raw.source ?? "")));
    const status = result !== undefined ? result.status : IntegrityStatus.FAILED;
    const eventId = String(event.id);
    const cls = event.agentcesourceclass;
    if (BROKEN.has(status) && CAPPED_CLASSES.has(cls)) {
      broken.add(eventId);
    } else if (WEAK.has(status) && cls === "enforcement_point") {
      weak.add(eventId);
    } else if (status === IntegrityStatus.TIME_SUSPECT) {
      suspect.add(eventId);
      // The time check reads before the strength: a weak or unsigned stream stays weak whatever its times say.
      if (cls === "enforcement_point" && result !== undefined && WEAK.has(result.strengthStatus)) {
        weak.add(eventId);
      }
    }
  });
  return { broken, weak, timeSuspect: suspect };
}
