/**
 * Ingest and validate an evidence bundle (SPEC §8.1 first module).
 *
 * Parse every event in `events/*.jsonl`, validate it against the JSON Schema, and quarantine anything
 * refused with a stable reason: an oversize line, malformed JSON or a schema violation
 * (`schema_invalid`), an unrecognised type (`unknown_type`), an undeclared source (`unknown_source`),
 * an event whose class differs from the declared one (`class_mismatch`), a repeated id
 * (`duplicate_id`), or an out-of-order timestamp within a source's stream (`time_order`). Quarantine is an
 * output, never a silent drop. This is a faithful port of the Python reference.
 *
 * A source the manifest declares with no trust class (or a bundle with no manifest classes at all) is
 * "undeclared": its events keep flowing, but `accepted` never trusts the event's own self-asserted
 * `agentcesourceclass` at face value -- it is corrected down to the weakest class, `self_report`, for
 * every downstream reader (SPEC §6.4). That correction never reaches `rawAccepted`, which stays
 * byte-identical to what the source emitted, because SPEC §6.6 defines each event's `integrity.hash`
 * over the whole CloudEvent (`agentcesourceclass` included) as received -- callers verifying integrity
 * must hash what was actually signed, not the engine's trust-corrected copy.
 */

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import type { Bundle } from "./bundle";
import { CanonicalizationError, canonicalString } from "./canonical";
import { InputError } from "./errors";
import { parseJson } from "./json";
import { QuarantineReason, type QuarantineRecord } from "./quarantine";
import { eventTypes, validateEvent } from "./schema";
import { SOURCE_CLASS_RANK } from "./sourceClass";
import { compareInstants } from "./structural";
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
export function splitLines(raw: Uint8Array): Uint8Array[] {
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

export function maxNesting(line: string): number {
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

/** A record that passed every per-record check, waiting for the id and time-order checks. */
interface Candidate {
  event: Record<string, unknown>;
  rawEvent: Record<string, unknown>;
  source: string;
  typeValue: string;
  rank: number;
}

/**
 * A quarantined record that may hold evidence a control needs (18.119): quarantine never gives a better outcome.
 * `event` is the record as readers would see it (class corrected as for an accepted event) when ingest could read it
 * but refused it for where it sits (time_order, a duplicate_id that is not an identical redelivery, class_mismatch,
 * unknown_source); it is null when ingest could not read it at all (schema_invalid, oversize). `subject` is the
 * record's own subject, null when unknown (then the record may belong to any subject). An unknown_type record is not
 * withheld: the engine reads nothing from a type it does not model.
 */
export interface Withheld {
  record: QuarantineRecord;
  event: Record<string, unknown> | null;
  subject: string | null;
  digest: string | null;
  /** Set on an unreadable record whose raw type names MemoryWrite, or whose type cannot be read at all (18.172). */
  write?: UnreadableWrite;
}

/** What ingest can still see of an unreadable record that may be a MemoryWrite (SPEC §7.4, 18.172): its raw
 * `data.record_ref` and `data.store` when each is a string, else null. */
export interface UnreadableWrite {
  recordRef: string | null;
  store: string | null;
}

export interface IngestResult {
  /** What assessment, activity, and graph building read; an undeclared source's event carries
   * `agentcesourceclass: "self_report"` here, never its own unproven self-assertion. */
  accepted: Array<Record<string, unknown>>;
  /** The same events exactly as received. Pass this, never `accepted`, to integrity verification
   * (SPEC §6.6), which must hash what the source actually signed. */
  rawAccepted: Array<Record<string, unknown>>;
  quarantined: QuarantineRecord[];
  withheld: Withheld[];
}

/** The record's canonical digest, null when it has no canonical form (a schema-invalid record may hold a number the
 * canonical form refuses). */
/** Matches only an unpaired surrogate: in `u` mode a valid pair is one code point. */
const LONE_SURROGATE_RE = /[\uD800-\uDFFF]/u;

/** The record's canonical digest, `null` when it has no canonical form: a schema-invalid record may hold a number the
 * canonical form refuses, or a lone surrogate, which UTF-8 cannot carry (Python's `_digest` refuses it as well). */
export function digestOf(event: Record<string, unknown>): string | null {
  try {
    const text = canonicalString(event);
    return LONE_SURROGATE_RE.test(text)
      ? null
      : createHash("sha256").update(text, "utf8").digest("hex");
  } catch (exc) {
    if (exc instanceof CanonicalizationError) {
      return null;
    }
    throw exc;
  }
}

function readable(record: QuarantineRecord, event: Record<string, unknown>): Withheld {
  return { record, event, subject: optStr(event, "subject") ?? null, digest: digestOf(event) };
}

function unreadable(record: QuarantineRecord, event?: Record<string, unknown>): Withheld {
  return {
    record,
    event: null,
    subject: event !== undefined ? (optStr(event, "subject") ?? null) : null,
    digest: event !== undefined ? digestOf(event) : null,
    ...(event !== undefined ? unreadableWrite(event) : {}),
  };
}

/** The raw MemoryWrite facts of an unreadable `event`: a MemoryWrite unless its envelope or data names another
 * type, so a record whose type cannot be read counts as one (fail closed, 18.172). */
function unreadableWrite(event: Record<string, unknown>): { write?: UnreadableWrite } {
  const name = typeof event.type === "string" ? eventTypeName(event.type) : null;
  const data = isRecord(event.data) ? event.data : {};
  const declared = data["@type"];
  if (
    name !== "MemoryWrite" &&
    declared !== "MemoryWrite" &&
    (name !== null || typeof declared === "string")
  ) {
    return {};
  }
  const record = data.record_ref;
  const store = data.store;
  return {
    write: {
      recordRef: typeof record === "string" ? record : null,
      store: typeof store === "string" ? store : null,
    },
  };
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

/**
 * Whether `time` is before `previous`, compared as instants (SPEC §6.7), so a UTC offset or a
 * fraction of a second never puts a later record before an earlier one (18.145). Two times that are
 * not comparable instants (not date-times, or one naive and one aware) keep the byte order ingest
 * has always used.
 */
function precedes(time: string, previous: string): boolean {
  const order = compareInstants(time, previous);
  return order === null ? byteCompare(time, previous) < 0 : order < 0;
}

function optStr(event: Record<string, unknown>, key: string): string | undefined {
  const value = event[key];
  return typeof value === "string" ? value : undefined;
}

export function ingest(bundle: Bundle, maxEventBytes = DEFAULT_MAX_EVENT_BYTES): IngestResult {
  const result: IngestResult = { accepted: [], rawAccepted: [], quarantined: [], withheld: [] };
  const validTypes = eventTypes();
  // Pass 1 checks each record on its own; `records` keeps, in file order, each quarantine record and each candidate
  // (the event as readers see it, as received, its source, type and class rank). `ownerRank` is the highest trusted
  // class among the candidates that carry each id: a record of a lower class never displaces it (18.143).
  const records: Array<QuarantineRecord | Withheld | Candidate> = [];
  const ownerRank = new Map<string, number>();

  for (const path of bundle.eventFiles) {
    for (const rawLine of splitLines(readFileSync(path))) {
      let raw: string;
      try {
        raw = decodeUtf8Strict(rawLine);
      } catch {
        records.push(
          unreadable({ reason: QuarantineReason.SCHEMA_INVALID, detail: "invalid UTF-8" }),
        );
        continue;
      }
      const line = trimJsonWhitespace(raw);
      if (!line) {
        continue;
      }
      if (Buffer.byteLength(line, "utf-8") > maxEventBytes) {
        records.push(
          unreadable({
            reason: QuarantineReason.OVERSIZE,
            detail: `event exceeds ${maxEventBytes} bytes`,
          }),
        );
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
        records.push(
          unreadable({ reason: QuarantineReason.SCHEMA_INVALID, detail: `invalid JSON: ${exc}` }),
        );
        continue;
      }
      if (!isRecord(event)) {
        records.push(
          unreadable({
            reason: QuarantineReason.SCHEMA_INVALID,
            detail: "event is not a JSON object",
          }),
        );
        continue;
      }

      const errors = validateEvent(event);
      if (errors.length > 0) {
        records.push(
          unreadable(
            {
              reason: QuarantineReason.SCHEMA_INVALID,
              eventId: optStr(event, "id"),
              source: optStr(event, "source"),
              type: optStr(event, "type"),
              detail: errors[0],
            },
            event,
          ),
        );
        continue;
      }

      const typeValue = String(event.type);
      const name = eventTypeName(typeValue);
      if (name === null || !validTypes.has(name)) {
        records.push({
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
        records.push(
          readable(
            {
              reason: QuarantineReason.UNKNOWN_SOURCE,
              eventId: String(event.id),
              source,
              type: typeValue,
              detail: "source is not declared in the bundle manifest",
            },
            { ...event, agentcesourceclass: "self_report" },
          ),
        );
        continue;
      }

      const rawEvent = event;
      let effectiveEvent = event;
      const declaredClass =
        bundle.sourceClasses !== null ? bundle.sourceClasses.get(source) : undefined;
      if (declaredClass !== undefined) {
        const eventClass = String(event.agentcesourceclass);
        if (eventClass !== declaredClass) {
          records.push(
            readable(
              {
                reason: QuarantineReason.CLASS_MISMATCH,
                eventId: String(event.id),
                source,
                type: typeValue,
                detail: `event class '${eventClass}' differs from the declared class '${declaredClass}' for this source`,
              },
              { ...event, agentcesourceclass: declaredClass },
            ),
          );
          continue;
        }
      } else if (String(event.agentcesourceclass) !== "self_report") {
        // SPEC §6.4: the manifest declares no trust class for this source, so the event's own
        // self-assertion is never trusted as-is. `rawEvent` (pushed below, unmodified) keeps the
        // original for integrity hashing (SPEC §6.6); this shallow copy is what every assessment,
        // activity, and graph reader sees.
        effectiveEvent = { ...event, agentcesourceclass: "self_report" };
      }

      const eventId = String(effectiveEvent.id);
      // The evidence ladder (SPEC §6.4): of two records with one id, the lower class is quarantined.
      const rank = SOURCE_CLASS_RANK.get(String(effectiveEvent.agentcesourceclass)) as number;
      ownerRank.set(eventId, Math.max(rank, ownerRank.get(eventId) ?? rank));
      records.push({ event: effectiveEvent, rawEvent, source, typeValue, rank });
    }
  }

  const seenIds = new Set<string>();
  // Time order is checked per (source, stream): the stream a record claims names a stream of its own source, so a record
  // never joins another source's ordering group by naming that source's stream (18.147).
  const lastTime = new Map<string, string>();
  // Each duplicate_id record with the raw record it was read from: withheld unless it is an identical redelivery of
  // the record held under its id, which is known only once every record is placed.
  const duplicates: Array<[Withheld, Record<string, unknown>]> = [];
  for (const record of records) {
    if ("digest" in record) {
      result.quarantined.push(record.record);
      result.withheld.push(record);
      continue;
    }
    if (!("event" in record)) {
      result.quarantined.push(record);
      continue;
    }
    const { event, rawEvent, source, typeValue, rank } = record;
    const eventId = String(event.id);
    if (seenIds.has(eventId) || rank < (ownerRank.get(eventId) as number)) {
      const duplicate = readable(
        {
          reason: QuarantineReason.DUPLICATE_ID,
          eventId,
          source,
          type: typeValue,
          detail: seenIds.has(eventId)
            ? "event id already seen in this bundle"
            : "a record of a higher source class has this event id",
        },
        event,
      );
      result.quarantined.push(duplicate.record);
      duplicates.push([duplicate, rawEvent]);
      continue;
    }

    const stream = streamOf(event);
    const orderKey = JSON.stringify([source, stream]);
    const timeValue = String(event.time);
    const previous = lastTime.get(orderKey);
    if (previous !== undefined && precedes(timeValue, previous)) {
      const late = readable(
        {
          reason: QuarantineReason.TIME_ORDER,
          eventId,
          source,
          stream,
          type: typeValue,
          detail: `time ${timeValue} precedes ${previous} in the stream`,
        },
        event,
      );
      result.quarantined.push(late.record);
      result.withheld.push(late);
      continue;
    }

    seenIds.add(eventId);
    lastTime.set(orderKey, timeValue);
    result.accepted.push(event);
    result.rawAccepted.push(rawEvent);
  }
  // Only ids that were delivered twice are compared, so only their held records are hashed.
  const ids = new Set(duplicates.map(([, rawEvent]) => String(rawEvent.id)));
  const held = new Map(
    result.rawAccepted.filter((e) => ids.has(String(e.id))).map((e) => [String(e.id), digestOf(e)]),
  );
  for (const [duplicate, rawEvent] of duplicates) {
    const digest = digestOf(rawEvent);
    if (digest === null || held.get(String(rawEvent.id)) !== digest) {
      result.withheld.push(duplicate);
    }
  }
  return result;
}
