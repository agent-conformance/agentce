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
import { InputError } from "./errors";
import { parseJson } from "./json";
import { QuarantineReason, type QuarantineRecord } from "./quarantine";
import { eventTypes, validateEvent } from "./schema";
import { byteCompare, decodeUtf8Strict } from "./util";
import { MAX_JSON_DEPTH } from "./verify";

const DEFAULT_MAX_EVENT_BYTES = 1_048_576;
const TYPE_RE = /^org\.agent-conformance\.evidence\.([A-Za-z0-9]+)\.v1$/;
/** The whitespace JSON itself allows around a value (RFC 8259 §2); nothing else is trimmed. */
const JSON_WHITESPACE_EDGES = /^[ \t\r\n]+|[ \t\r\n]+$/g;

/**
 * One line rule the three engines share (18.65, Python's `bytes.splitlines`): a line ends at \n,
 * \r\n or a lone \r. Split on bytes, before decoding, so each line is decoded (and refused) on its
 * own; \r and \n never occur inside a multi-byte UTF-8 sequence.
 */
function splitLines(raw: Uint8Array): Uint8Array[] {
  const lines: Uint8Array[] = [];
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

function tooDeep(): InputError {
  return new InputError(
    "input.event_structure_too_deep",
    "an evidence event line is nested too deeply to parse safely.",
    "flatten the event's structure; reference deeply nested content by an opaque locator instead (SPEC R12).",
  );
}

/**
 * Mirrors `ingest._max_nesting`: the deepest `[`/`{` nesting in `line`, counted lexically (brackets
 * inside strings ignored) before any parse, so the limit is the same number in all three engines.
 */
function bracketCount(line: string): number {
  let count = 0;
  for (let i = 0; i < line.length; i++) {
    const code = line.charCodeAt(i);
    if (code === 0x5b || code === 0x7b) count++;
  }
  return count;
}

function maxNesting(line: string): number {
  let depth = 0;
  let deepest = 0;
  let inString = false;
  let escaped = false;
  for (const ch of line) {
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === '"') {
        inString = false;
      }
    } else if (ch === '"') {
      inString = true;
    } else if (ch === "[" || ch === "{") {
      depth++;
      deepest = Math.max(deepest, depth);
    } else if (ch === "]" || ch === "}") {
      depth--;
    }
  }
  return deepest;
}

function trimJsonWhitespace(text: string): string {
  return text.replace(JSON_WHITESPACE_EDGES, "");
}

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
    for (const rawLine of splitLines(readFileSync(path))) {
      let raw: string;
      try {
        raw = decodeUtf8Strict(rawLine);
      } catch {
        result.quarantined.push({
          reason: QuarantineReason.SCHEMA_INVALID,
          detail: "invalid UTF-8",
        });
        continue;
      }
      const line = trimJsonWhitespace(raw);
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
      // The bracket count bounds the nesting, so most lines skip the character scan.
      if (bracketCount(line) > MAX_JSON_DEPTH && maxNesting(line) > MAX_JSON_DEPTH) {
        throw tooDeep();
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
