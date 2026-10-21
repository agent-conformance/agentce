/**
 * Coverage and reconciliation with independent denominators (SPEC §6.5, §8.3 rung 0).
 *
 * For each subject and event type the engine compares the events it captured (`observed`) against the
 * independent denominator's count (`expected`) using integer arithmetic, and reports the ratio and a
 * status: `covered`, `below_threshold` (a capture gap), or `unknown` when no independent denominator
 * covers the type. A source's own counters are never used as its own denominator. This is a faithful
 * port of the Python reference; the ratio is rounded to four places, banker's rounding, with exact
 * integer arithmetic so it is cross-language identical.
 */

import { MAX_COUNT } from "./domain";
import { InputError } from "./errors";
import { NonCanonicalNumber, parseJson } from "./json";
import type { CoverageDenominator, Profile, Subject } from "./profile";
import { byteCompare, decodeUtf8Strict } from "./util";

export const STATUS_COVERED = "covered";
const STATUS_BELOW = "below_threshold";
export const STATUS_UNKNOWN = "unknown";

type Event = Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function eventType(event: Event): string {
  const data = event.data;
  return isRecord(data) && typeof data["@type"] === "string" ? data["@type"] : "Unknown";
}

/** `observed / expected` to four decimals, banker's rounding, or null when `expected <= 0`. */
function ratio(observed: number, expected: number): string | null {
  if (expected <= 0) {
    return null;
  }
  const exp = BigInt(expected);
  const scaled = BigInt(observed) * 10000n;
  let q = scaled / exp; // floor, since both are non-negative
  const twice = 2n * (scaled % exp);
  if (twice > exp) {
    q += 1n;
  } else if (twice === exp && q % 2n === 1n) {
    q += 1n; // half to even
  }
  const frac = (q % 10000n).toString().padStart(4, "0");
  return `${(q / 10000n).toString()}.${frac}`;
}

function invalidManifest(name: string, why: string): InputError {
  return new InputError(
    "input.coverage_denominator_manifest_invalid",
    `the coverage denominator manifest '${name}' ${why}.`,
    'write the manifest as one JSON object whose "counts" maps each event type to a whole number of records, 0 or more.',
  );
}

/** The manifest's JSON, read as every engine reads it: strict UTF-8 and standard JSON only (18.184j). */
function loadManifest(name: string, raw: Buffer): unknown {
  try {
    return parseJson(decodeUtf8Strict(raw));
  } catch (exc) {
    throw invalidManifest(
      name,
      `is not JSON (${exc instanceof Error ? exc.message : String(exc)})`,
    );
  }
}

/** `data[key]` as counts: absent is none, anything else must be an object of whole numbers from 0 to 2^53 - 1. A
 * number with a fraction or exponent, or past that range, reads as a NonCanonicalNumber and is refused. */
function manifestCounts(
  name: string,
  data: Record<string, unknown>,
  key: string,
): Record<string, number> {
  if (!Object.hasOwn(data, key)) {
    return {};
  }
  const declared = data[key];
  if (!isRecord(declared) || declared instanceof NonCanonicalNumber) {
    throw invalidManifest(name, `has a '${key}' that is not a JSON object`);
  }
  for (const [type, count] of Object.entries(declared)) {
    if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) {
      const shown = count instanceof NonCanonicalNumber ? count.source : JSON.stringify(count);
      throw invalidManifest(
        name,
        `counts '${type}' as ${shown}, not a whole number from 0 to ${MAX_COUNT}`,
      );
    }
  }
  return declared as Record<string, number>;
}

/** A denominator counts only when it is independent (SPEC §6.5): never one of the subject's own declared evidence
 * sources, and never an admission attestation, whose signature and policy digest no engine verifies. */
function independent(denominator: CoverageDenominator, subject: Subject): boolean {
  return (
    denominator.kind !== "admission_attestation" &&
    !subject.evidenceSources.some((s) => s.source === denominator.source)
  );
}

/**
 * The denominator's counts for the types it covers (every type it counts when `covers` is empty): both its
 * yardstick and its expected counts (SPEC §6.5).
 */
function denominatorCounts(
  denominator: CoverageDenominator,
  subject: Subject,
  bySource: Map<string, Map<string, number>>,
  members: ReadonlyMap<string, Buffer>,
): Record<string, number> {
  const counts = declaredCounts(denominator, subject, bySource, members);
  const kept: Record<string, number> = {};
  for (const [type, count] of Object.entries(counts)) {
    if (denominator.covers.length === 0 || denominator.covers.includes(type)) {
      kept[type] = count;
    }
  }
  return kept;
}

function declaredCounts(
  denominator: CoverageDenominator,
  subject: Subject,
  bySource: Map<string, Map<string, number>>,
  members: ReadonlyMap<string, Buffer>,
): Record<string, number> {
  if (!independent(denominator, subject)) {
    return {};
  }
  if (denominator.manifest) {
    const raw = members.get(denominator.manifest);
    if (raw === undefined) {
      // A file the bundle manifest does not list is outside the bundle digest: never read (SPEC §6.6).
      return {};
    }
    // The bytes loadBundle checked and hashed, never a second read of the name (18.184f).
    const name = denominator.manifest;
    const data = loadManifest(name, raw);
    // A root that is not an object (a number token included) declares no counts: fall through, as Python and Java do.
    if (isRecord(data) && !(data instanceof NonCanonicalNumber)) {
      const declared = ["counts", "expected"].map((key) => manifestCounts(name, data, key));
      return declared.find((found) => Object.keys(found).length > 0) ?? {};
    }
  }
  // Otherwise count the denominator source's own ingested events, per type.
  const counts: Record<string, number> = {};
  const inner = bySource.get(denominator.source);
  if (inner !== undefined) {
    for (const [type, count] of inner) {
      counts[type] = (counts[type] ?? 0) + count;
    }
  }
  return counts;
}

interface ExpectedEntry {
  count: number;
  denominators: string[];
}

/** Return the coverage.json structure for `events` under `profile`. `members` maps each file the bundle manifest
 * lists to the bytes `loadBundle` checked: a denominator manifest is taken from there and only when listed. */
export function computeCoverage(
  events: Event[],
  profile: Profile,
  members: ReadonlyMap<string, Buffer> = new Map(),
): Record<string, unknown> {
  const bySource = new Map<string, Map<string, number>>();
  for (const event of events) {
    const source = String(event.source ?? "");
    const type = eventType(event);
    let inner = bySource.get(source);
    if (inner === undefined) {
      inner = new Map<string, number>();
      bySource.set(source, inner);
    }
    inner.set(type, (inner.get(type) ?? 0) + 1);
  }

  const subjectsOut: Record<string, unknown> = {};
  for (const subject of profile.subjects) {
    const countsByDenominator = subject.coverageDenominators.map(
      (d) => [d, denominatorCounts(d, subject, bySource, members)] as const,
    );
    // A denominator's own records of a type it covers and counts are the yardstick, not captured operations. One
    // that is not independent, or does not count a type, hides none of that type's events (SPEC §6.5).
    const yardstick = new Set<string>();
    for (const [d, counts] of countsByDenominator) {
      for (const t of Object.keys(counts)) {
        yardstick.add(`${d.source}\u0000${t}`);
      }
    }
    const observed = new Map<string, number>();
    const types = new Set<string>();
    for (const event of events) {
      if (String(event.subject ?? "") !== subject.id) {
        continue;
      }
      const type = eventType(event);
      if (yardstick.has(`${String(event.source ?? "")}\u0000${type}`)) {
        continue;
      }
      observed.set(type, (observed.get(type) ?? 0) + 1);
      types.add(type);
    }

    const expected = new Map<string, ExpectedEntry>();
    for (const [denominator, counts] of countsByDenominator) {
      for (const type of Object.keys(counts)) {
        types.add(type);
        let entry = expected.get(type);
        if (entry === undefined) {
          entry = { count: -1, denominators: [] };
          expected.set(type, entry);
        }
        if ((counts[type] as number) > entry.count) {
          entry.count = counts[type] as number;
          entry.denominators = [denominator.source];
        } else if (counts[type] === entry.count) {
          entry.denominators.push(denominator.source);
        }
      }
    }

    const byType: Record<string, unknown> = {};
    for (const type of [...types].sort(byteCompare)) {
      const obs = observed.get(type) ?? 0;
      const entry = expected.get(type);
      if (entry === undefined) {
        byType[type] = {
          observed: obs,
          expected: null,
          ratio: null,
          status: STATUS_UNKNOWN,
          denominators: [],
        };
      } else {
        byType[type] = {
          observed: obs,
          expected: entry.count,
          ratio: ratio(obs, entry.count),
          status: obs >= entry.count ? STATUS_COVERED : STATUS_BELOW,
          denominators: [...entry.denominators].sort(byteCompare),
        };
      }
    }

    subjectsOut[subject.id] = {
      event_types: byType,
      coverage_status: rollup(subject.coverageDenominators, byType),
      has_independent_denominator: subject.coverageDenominators.length > 0,
    };
  }

  return { subjects: subjectsOut };
}

function rollup(denominators: CoverageDenominator[], byType: Record<string, unknown>): string {
  if (denominators.length === 0) {
    return STATUS_UNKNOWN;
  }
  const statuses = new Set(
    Object.values(byType).map((entry) => (entry as { status: string }).status),
  );
  if (statuses.has(STATUS_UNKNOWN)) {
    return STATUS_UNKNOWN;
  }
  if (statuses.has(STATUS_BELOW)) {
    return "gap";
  }
  return "ok";
}
