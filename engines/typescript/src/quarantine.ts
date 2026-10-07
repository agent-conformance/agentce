/**
 * Quarantine records: an ingested item the engine refused, with a stable reason (SPEC App. F).
 * Quarantine is an output, never a silent drop. Each record is one line of `quarantine.jsonl`.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { InputError } from "./errors";
import { jsonStringifyAscii, sortKeysDeep } from "./util";

export enum QuarantineReason {
  SCHEMA_INVALID = "schema_invalid",
  DUPLICATE_ID = "duplicate_id",
  TIME_ORDER = "time_order",
  UNKNOWN_TYPE = "unknown_type",
  UNKNOWN_SOURCE = "unknown_source",
  CLASS_MISMATCH = "class_mismatch",
  OVERSIZE = "oversize",
  CONTEXT_MISMATCH = "context_mismatch",
}

export interface QuarantineRecord {
  reason: QuarantineReason;
  eventId?: string;
  source?: string;
  stream?: string;
  type?: string;
  detail?: string;
}

export function quarantineToJson(record: QuarantineRecord): Record<string, string> {
  const out: Record<string, string> = { reason: record.reason };
  if (record.eventId !== undefined) {
    out.event_id = record.eventId;
  }
  if (record.source !== undefined) {
    out.source = record.source;
  }
  if (record.stream !== undefined) {
    out.stream = record.stream;
  }
  if (record.type !== undefined) {
    out.type = record.type;
  }
  if (record.detail !== undefined) {
    out.detail = record.detail;
  }
  return out;
}

/** A deterministic `{reason: count}` map (sorted by reason). */
export function countsByReason(records: Iterable<QuarantineRecord>): Record<string, number> {
  const counts = new Map<string, number>();
  for (const record of records) {
    counts.set(record.reason, (counts.get(record.reason) ?? 0) + 1);
  }
  const out: Record<string, number> = {};
  for (const reason of [...counts.keys()].sort()) {
    out[reason] = counts.get(reason) as number;
  }
  return out;
}

/** Write `records` to `path` as one JSON object per line, in input order. Return the count written. */
export function writeQuarantine(records: Iterable<QuarantineRecord>, path: string): number {
  let written = 0;
  const lines: string[] = [];
  for (const record of records) {
    // Python's `json.dumps(sort_keys=True, separators=(",", ":"))`: keys sorted, non-ASCII escaped.
    lines.push(jsonStringifyAscii(sortKeysDeep(quarantineToJson(record))));
    written += 1;
  }
  try {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, lines.length > 0 ? `${lines.join("\n")}\n` : "");
  } catch (err) {
    const e = err as NodeJS.ErrnoException;
    throw new InputError(
      "input.out_dir_unwritable",
      e.message,
      "choose a writable --out directory.",
    );
  }
  return written;
}
