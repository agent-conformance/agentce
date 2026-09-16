/**
 * Quarantine records: an ingested item the engine refused, with a stable reason (SPEC App. F).
 * Quarantine is an output, never a silent drop. Each record is one line of `quarantine.jsonl`.
 */

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
