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

import { readFileSync } from "node:fs";
import { confineToRoot, safeIsFile } from "./bundle";
import { type CoverageDenominator, type Profile, denominatorIds } from "./profile";
import { byteCompare } from "./util";

const STATUS_COVERED = "covered";
const STATUS_BELOW = "below_threshold";
const STATUS_UNKNOWN = "unknown";

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

function firstNonEmpty(...candidates: unknown[]): Record<string, unknown> {
  for (const candidate of candidates) {
    if (isRecord(candidate) && Object.keys(candidate).length > 0) {
      return candidate;
    }
  }
  return {};
}

function denominatorCounts(
  denominator: CoverageDenominator,
  bySource: Map<string, Map<string, number>>,
  bundleRoot: string | null,
): Record<string, number> {
  if (denominator.manifest && bundleRoot !== null) {
    // A denominator manifest is a bundle-adjacent reference: confine it to the bundle root so a
    // path that escapes (literally or through a symlink) is not read at all, falling through to
    // the source's own ingested counts exactly as a missing manifest does.
    const confined = confineToRoot(bundleRoot, denominator.manifest);
    if (confined !== null && safeIsFile(confined)) {
      const data = JSON.parse(readFileSync(confined, "utf-8"));
      if (isRecord(data)) {
        const declared = firstNonEmpty(data.counts, data.expected);
        const out: Record<string, number> = {};
        for (const [key, val] of Object.entries(declared)) {
          out[key] = Math.trunc(Number(val));
        }
        return out;
      }
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

/** Return the coverage.json structure for `events` under `profile`. */
export function computeCoverage(
  events: Event[],
  profile: Profile,
  bundleRoot: string | null = null,
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
    const denomIds = denominatorIds(subject);
    const observed = new Map<string, number>();
    const types = new Set<string>();
    for (const event of events) {
      if (String(event.subject ?? "") !== subject.id) {
        continue;
      }
      if (denomIds.has(String(event.source ?? ""))) {
        continue; // a denominator's own events are the yardstick, not captured operations
      }
      const type = eventType(event);
      observed.set(type, (observed.get(type) ?? 0) + 1);
      types.add(type);
    }

    const expected = new Map<string, ExpectedEntry>();
    for (const denominator of subject.coverageDenominators) {
      const counts = denominatorCounts(denominator, bySource, bundleRoot);
      const covered = denominator.covers.length > 0 ? denominator.covers : Object.keys(counts);
      for (const type of covered) {
        if (!(type in counts)) {
          continue;
        }
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
