/**
 * Ingest and validate an evidence bundle (SPEC §8.1 first module).
 *
 * Parse every event in `events/*.jsonl`, validate it against the JSON Schema, and quarantine anything
 * refused with a stable reason: an oversize line, malformed JSON or a schema violation
 * (`schema_invalid`), an unrecognised type (`unknown_type`), an undeclared source (`unknown_source`),
 * an event whose class differs from the declared one (`class_mismatch`), a repeated id
 * (`duplicate_id`), or an out-of-order timestamp within a stream (`time_order`). Quarantine is an
 * output, never a silent drop. This is a faithful port of the Python reference.
 */

import { readFileSync } from "node:fs";
import type { Bundle } from "./bundle";
import { parseJson } from "./json";
import { QuarantineReason, type QuarantineRecord } from "./quarantine";
import { eventTypes, validateEvent } from "./schema";
import { byteCompare } from "./util";

const DEFAULT_MAX_EVENT_BYTES = 1_048_576;
const TYPE_RE = /^org\.agent-conformance\.evidence\.([A-Za-z0-9]+)\.v1$/;

export interface IngestResult {
  accepted: Array<Record<string, unknown>>;
  quarantined: QuarantineRecord[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function eventTypeName(typeValue: string): string | null {
  const match = TYPE_RE.exec(typeValue);
  return match ? (match[1] as string) : null;
}

function streamOf(event: Record<string, unknown>): string {
  const data = event.data;
  if (isRecord(data)) {
    const integrity = data.integrity;
    if (isRecord(integrity)) {
      const stream = integrity.stream;
      if (typeof stream === "string" && stream) {
        return stream;
      }
    }
  }
  return String(event.source ?? "");
}

function optStr(event: Record<string, unknown>, key: string): string | undefined {
  const value = event[key];
  return typeof value === "string" ? value : undefined;
}

export function ingest(bundle: Bundle, maxEventBytes = DEFAULT_MAX_EVENT_BYTES): IngestResult {
  const result: IngestResult = { accepted: [], quarantined: [] };
  const validTypes = eventTypes();
  const seenIds = new Set<string>();
  const lastTime = new Map<string, string>();

  for (const path of bundle.eventFiles) {
    for (const raw of readFileSync(path, "utf-8").split("\n")) {
      const line = raw.trim();
      if (!line) {
        continue;
      }
      if (Buffer.byteLength(line, "utf-8") > maxEventBytes) {
        result.quarantined.push({
          reason: QuarantineReason.OVERSIZE,
          detail: `event exceeds ${maxEventBytes} bytes`,
        });
        continue;
      }
      let event: unknown;
      try {
        event = parseJson(line);
      } catch (exc) {
        result.quarantined.push({
          reason: QuarantineReason.SCHEMA_INVALID,
          detail: `invalid JSON: ${exc}`,
        });
        continue;
      }
      if (!isRecord(event)) {
        result.quarantined.push({
          reason: QuarantineReason.SCHEMA_INVALID,
          detail: "event is not a JSON object",
        });
        continue;
      }

      const errors = validateEvent(event);
      if (errors.length > 0) {
        result.quarantined.push({
          reason: QuarantineReason.SCHEMA_INVALID,
          eventId: optStr(event, "id"),
          source: optStr(event, "source"),
          type: optStr(event, "type"),
          detail: errors[0],
        });
        continue;
      }

      const typeValue = String(event.type);
      const name = eventTypeName(typeValue);
      if (name === null || !validTypes.has(name)) {
        result.quarantined.push({
          reason: QuarantineReason.UNKNOWN_TYPE,
          eventId: String(event.id),
          source: String(event.source),
          type: typeValue,
          detail: `unrecognised event type '${typeValue}'`,
        });
        continue;
      }

      const source = String(event.source);
      if (bundle.sources !== null && !bundle.sources.has(source)) {
        result.quarantined.push({
          reason: QuarantineReason.UNKNOWN_SOURCE,
          eventId: String(event.id),
          source,
          type: typeValue,
          detail: "source is not declared in the bundle manifest",
        });
        continue;
      }

      const declaredClass =
        bundle.sourceClasses !== null ? bundle.sourceClasses.get(source) : undefined;
      if (declaredClass !== undefined) {
        const eventClass = String(event.agentcesourceclass);
        if (eventClass !== declaredClass) {
          result.quarantined.push({
            reason: QuarantineReason.CLASS_MISMATCH,
            eventId: String(event.id),
            source,
            type: typeValue,
            detail: `event class '${eventClass}' differs from the declared class '${declaredClass}' for this source`,
          });
          continue;
        }
      }

      const eventId = String(event.id);
      if (seenIds.has(eventId)) {
        result.quarantined.push({
          reason: QuarantineReason.DUPLICATE_ID,
          eventId,
          source,
          type: typeValue,
          detail: "event id already seen in this bundle",
        });
        continue;
      }

      const stream = streamOf(event);
      const timeValue = String(event.time);
      const previous = lastTime.get(stream);
      if (previous !== undefined && byteCompare(timeValue, previous) < 0) {
        result.quarantined.push({
          reason: QuarantineReason.TIME_ORDER,
          eventId,
          source,
          stream,
          type: typeValue,
          detail: `time ${timeValue} precedes ${previous} in the stream`,
        });
        continue;
      }

      seenIds.add(eventId);
      lastTime.set(stream, timeValue);
      result.accepted.push(event);
    }
  }
  return result;
}
