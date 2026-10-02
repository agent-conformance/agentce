/**
 * The `otel-genai` adapter (SPEC 12.1): OpenTelemetry GenAI / OpenInference spans -> canonical
 * AgentCE evidence events. Ported byte-identical from the hardened Python reference
 * (`engines/python/agentce/records/otel_genai.py`), per contract `P18-18.29` draft 3. A pure function
 * `bytes -> (events, AdapterReport)` over an OTLP/JSON trace export; no network, no learned component.
 */

import { NonCanonicalNumber, parseJson } from "./json";
import { byteCompare, decodeUtf8Strict } from "./util";

export const BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1";

const VALID_SOURCE_CLASSES = new Set(["self_report", "enforcement_point", "independent_system"]);
const MAX_SAFE_BIGINT = BigInt(Number.MAX_SAFE_INTEGER);
const MAX_INT_STR_DIGITS = 4300; // CPython's sys.int_max_str_digits default, enforced the same way

/** The input could not be adapted. `reason` is a stable key, matching the Python reference's. */
export class OtelGenaiAdapterError extends Error {
  readonly reason: string;

  constructor(reason: string, detail = "") {
    super(detail ? `${reason}: ${detail}` : reason);
    this.reason = reason;
    this.name = "OtelGenaiAdapterError";
  }
}

export interface SkippedSpan {
  spanId: string;
  name: string;
  reason: string;
}

export interface AdapterReport {
  adapter: string;
  conventions: string[];
  spansSeen: number;
  eventsEmitted: number;
  skipped: SkippedSpan[];
}

export interface AdaptResult {
  events: Record<string, unknown>[];
  report: AdapterReport;
}

export interface AdaptOptions {
  subject: string;
  sourceClass?: string;
  source?: string;
}

type IntValue = number | NonCanonicalNumber;

interface Span {
  traceId: string;
  spanId: string;
  parentSpanId: string | null;
  name: string;
  start: string | null;
  end: string | null;
  attrs: Record<string, unknown>;
  statusCode: number;
  statusMessage: string | null;
  convention: string;
}

// --- OTLP/JSON decoding helpers. ---------------------------------------------------------------

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    !(value instanceof NonCanonicalNumber)
  );
}

/**
 * The exact grammar CPython's `int(str)` accepts: trim ASCII whitespace, an optional single leading
 * sign, then digits with at most one `_` between any two consecutive digits (never leading, trailing,
 * or doubled), and no more than `sys.int_max_str_digits` digits. Shared by `_anyValue`'s `intValue`
 * string case and `_asInt`'s string case -- the same Python `int()` call in the reference.
 */
function parsePythonIntGrammar(raw: string): IntValue | null {
  const trimmed = raw.replace(/^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g, "");
  let sign = "";
  let rest = trimmed;
  if (rest.startsWith("+") || rest.startsWith("-")) {
    sign = rest[0] === "-" ? "-" : "";
    rest = rest.slice(1);
  }
  if (!/^[0-9](_?[0-9])*$/.test(rest)) {
    return null;
  }
  const digits = rest.replace(/_/g, "");
  if (digits.length > MAX_INT_STR_DIGITS) {
    return null;
  }
  const big = BigInt(`${sign}${digits}`);
  if (big > MAX_SAFE_BIGINT || big < -MAX_SAFE_BIGINT) {
    return new NonCanonicalNumber(`${sign}${digits}`, "integer_out_of_range");
  }
  return Number(big);
}

/** Decode one OTLP `AnyValue` to a plain scalar/list/object (protobuf-JSON encoding). */
function anyValue(value: unknown): unknown {
  if (!isPlainObject(value)) {
    return null;
  }
  if ("stringValue" in value) {
    return value.stringValue;
  }
  if ("intValue" in value) {
    // int64 is JSON-encoded as a string in the OTLP/JSON protobuf mapping; a non-integer string
    // (malformed telemetry) is treated the same as any other unmapped value, never raised. A bare
    // (non-string) number is passed through as-is, full precision preserved via NonCanonicalNumber.
    const raw = value.intValue;
    if (typeof raw !== "string") {
      return raw;
    }
    return parsePythonIntGrammar(raw);
  }
  if ("boolValue" in value) {
    return value.boolValue;
  }
  if ("doubleValue" in value) {
    return value.doubleValue;
  }
  if ("arrayValue" in value) {
    const inner = value.arrayValue;
    const items = isPlainObject(inner) ? inner.values : undefined;
    if (!Array.isArray(items)) {
      return [];
    }
    return items.map(anyValue);
  }
  if ("kvlistValue" in value) {
    const inner = value.kvlistValue;
    const pairs = isPlainObject(inner) ? inner.values : undefined;
    const out: Record<string, unknown> = Object.create(null);
    if (!Array.isArray(pairs)) {
      return out;
    }
    for (const pair of pairs) {
      if (isPlainObject(pair) && typeof pair.key === "string") {
        // Object.create(null) has no `__proto__` accessor to trigger: a span attribute literally
        // named "__proto__" is stored and read back as an ordinary own key, never a prototype
        // assignment (contract C1(f), pinned by the `prototype-pollution` vector).
        out[pair.key] = anyValue(pair.value);
      }
    }
    return out;
  }
  return null;
}

/** Turn an OTLP attribute list `[{key, value}]` into a flat, prototype-safe `{key: scalar}` map. */
function attributesFromList(raw: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = Object.create(null);
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (isPlainObject(item) && typeof item.key === "string") {
        out[item.key] = anyValue(item.value);
      }
    }
  }
  return out;
}

/** `{...a, ...b}` without a plain-object intermediate, so a `__proto__` key never changes shape. */
function mergeAttrs(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = Object.create(null);
  for (const key of Object.keys(a)) {
    out[key] = a[key];
  }
  for (const key of Object.keys(b)) {
    out[key] = b[key];
  }
  return out;
}

function asStr(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asInt(value: unknown): IntValue | null {
  if (typeof value === "boolean") {
    return null;
  }
  if (typeof value === "number") {
    return value;
  }
  if (value instanceof NonCanonicalNumber) {
    // A float (`non_integer_number`) is never an int, matching Python's `isinstance(x, int)` being
    // False for a float even when its value is integral (`5.0`). An out-of-range integer is passed
    // through unchanged so it reaches `canonicalString` and is refused there, the same way an
    // arbitrary-precision Python `int` would be.
    return value.reason === "integer_out_of_range" ? value : null;
  }
  if (typeof value === "string") {
    return parsePythonIntGrammar(value);
  }
  return null;
}

function toBigIntFromInt(value: IntValue): bigint {
  return typeof value === "number" ? BigInt(value) : BigInt(value.source);
}

/** Format a Unix-nanoseconds timestamp as canonical RFC 3339 UTC with millisecond precision. */
function rfc3339Millis(unixNanosRaw: unknown): string | null {
  const parsed = asInt(unixNanosRaw);
  if (parsed === null) {
    return null;
  }
  const nanos = toBigIntFromInt(parsed);
  if (nanos < 0n) {
    return null;
  }
  const millisTotal = nanos / 1_000_000n; // truncate to milliseconds; never round up
  const seconds = millisTotal / 1000n;
  const millis = millisTotal % 1000n;
  if (seconds >= 253402300800n) {
    // A timestamp so far out of range the platform clock cannot represent it (year 10000 and
    // beyond -- Python's `datetime.fromtimestamp` raises `ValueError` above year 9999) is treated as
    // no timestamp at all, never raised.
    return null;
  }
  const date = new Date(Number(seconds) * 1000);
  const pad = (n: number, width: number): string => String(n).padStart(width, "0");
  return (
    `${pad(date.getUTCFullYear(), 4)}-${pad(date.getUTCMonth() + 1, 2)}-${pad(date.getUTCDate(), 2)}` +
    `T${pad(date.getUTCHours(), 2)}:${pad(date.getUTCMinutes(), 2)}:${pad(date.getUTCSeconds(), 2)}` +
    `.${pad(Number(millis), 3)}Z`
  );
}

// --- Convention detection. ----------------------------------------------------------------------

/** Extract `<major.minor>` from the first OTLP schema URL that carries one (ASCII digits only). */
function otelGenaiVersion(...schemaUrls: unknown[]): string | null {
  for (const candidate of schemaUrls) {
    const url = asStr(candidate);
    if (url === null) {
      continue;
    }
    const stripped = url.replace(/\/+$/, "");
    const idx = stripped.lastIndexOf("/");
    const tail = idx === -1 ? stripped : stripped.slice(idx + 1);
    const parts = tail.split(".");
    if (parts.length >= 2 && /^[0-9]+$/.test(parts[0]) && /^[0-9]+$/.test(parts[1])) {
      return `${parts[0]}.${parts[1]}`;
    }
  }
  return null;
}

function conventionFor(
  scopeName: string,
  scopeVersion: string | null,
  scopeSchema: unknown,
  resourceSchema: unknown,
  attrs: Record<string, unknown>,
): string {
  if ("openinference.span.kind" in attrs || scopeName.startsWith("openinference")) {
    return scopeVersion !== null ? `openinference:${scopeVersion}` : "openinference";
  }
  const version = otelGenaiVersion(scopeSchema, resourceSchema);
  return version !== null ? `otel-genai:${version}` : "otel-genai";
}

// --- Span iteration. -----------------------------------------------------------------------------

function iterSpans(document: Record<string, unknown>): Span[] {
  const resourceSpans = document.resourceSpans;
  if (!Array.isArray(resourceSpans)) {
    throw new OtelGenaiAdapterError("not_otlp", "document has no resourceSpans array");
  }
  const spans: Span[] = [];
  for (const resourceSpan of resourceSpans) {
    if (!isPlainObject(resourceSpan)) {
      continue;
    }
    const resource = resourceSpan.resource;
    const resourceAttrs = attributesFromList(
      isPlainObject(resource) ? resource.attributes : undefined,
    );
    const resourceSchema = resourceSpan.schemaUrl;
    const scopeSpans = resourceSpan.scopeSpans;
    if (!Array.isArray(scopeSpans)) {
      continue;
    }
    for (const scopeSpan of scopeSpans) {
      if (!isPlainObject(scopeSpan)) {
        continue;
      }
      const scope = scopeSpan.scope;
      const scopeName = asStr(isPlainObject(scope) ? scope.name : undefined) ?? "";
      const scopeVersion = asStr(isPlainObject(scope) ? scope.version : undefined);
      const scopeSchema = scopeSpan.schemaUrl;
      const spanList = scopeSpan.spans;
      if (!Array.isArray(spanList)) {
        continue;
      }
      for (const span of spanList) {
        if (!isPlainObject(span)) {
          continue;
        }
        const attrs = attributesFromList(span.attributes);
        const convention = conventionFor(
          scopeName,
          scopeVersion,
          scopeSchema,
          resourceSchema,
          attrs,
        );
        const status = span.status;
        let statusCode = 0;
        let statusMessage: string | null = null;
        if (isPlainObject(status)) {
          const codeVal = asInt(status.code);
          statusCode = typeof codeVal === "number" ? codeVal : 0;
          statusMessage = asStr(status.message);
        }
        spans.push({
          traceId: asStr(span.traceId) ?? "",
          spanId: asStr(span.spanId) ?? "",
          parentSpanId: asStr(span.parentSpanId),
          name: asStr(span.name) ?? "",
          start: rfc3339Millis(span.startTimeUnixNano),
          end: rfc3339Millis(span.endTimeUnixNano),
          attrs: mergeAttrs(resourceAttrs, attrs),
          statusCode,
          statusMessage,
          convention,
        });
      }
    }
  }
  return spans;
}

// --- Span mapping. ---------------------------------------------------------------------------------

const OPENINFERENCE_KIND_MAP: Record<string, string> = {
  LLM: "chat",
  EMBEDDING: "embeddings",
  TOOL: "execute_tool",
  AGENT: "invoke_agent",
  RETRIEVER: "retrieve",
};

function openinferenceOperation(attrs: Record<string, unknown>): string | null {
  const kind = asStr(attrs["openinference.span.kind"]);
  if (kind === null) {
    return null;
  }
  return OPENINFERENCE_KIND_MAP[kind] ?? kind;
}

function operationOf(span: Span): string | null {
  if (span.convention.startsWith("openinference")) {
    return openinferenceOperation(span.attrs);
  }
  return asStr(span.attrs["gen_ai.operation.name"]);
}

function agentRef(attrs: Record<string, unknown>): Record<string, unknown> | null {
  const agentId = asStr(attrs["gen_ai.agent.id"]);
  if (agentId === null) {
    return null;
  }
  const agent: Record<string, unknown> = { id: agentId };
  const name = asStr(attrs["gen_ai.agent.name"]);
  if (name !== null) {
    agent.name = name;
  }
  return agent;
}

function firstStr(attrs: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const found = asStr(attrs[key]);
    if (found !== null) {
      return found;
    }
  }
  return null;
}

function firstInt(attrs: Record<string, unknown>, ...keys: string[]): IntValue | null {
  for (const key of keys) {
    if (key in attrs) {
      const found = asInt(attrs[key]);
      if (found !== null) {
        return found;
      }
    }
  }
  return null;
}

function locatorOf(span: Span, fragment: string): string {
  return `otel:${span.traceId}/${span.spanId}#${fragment}`;
}

function endReason(span: Span): string {
  return span.statusCode === 2 ? "error" : "completed";
}

function errorOf(span: Span): string | null {
  return span.statusCode === 2 ? (span.statusMessage ?? "error") : null;
}

function basePayload(span: Span): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  const agent = agentRef(span.attrs);
  if (agent !== null) {
    payload.agent = agent;
  }
  const sessionId = firstStr(span.attrs, "gen_ai.conversation.id", "session.id");
  if (sessionId !== null) {
    payload.session_id = sessionId;
  }
  return payload;
}

function hasInput(span: Span): boolean {
  return ["gen_ai.input.messages", "gen_ai.prompt", "input.value"].some((k) => k in span.attrs);
}

function hasOutput(span: Span): boolean {
  return ["gen_ai.output.messages", "gen_ai.completion", "output.value"].some(
    (k) => k in span.attrs,
  );
}

function modelCall(span: Span, operation: string): Record<string, unknown> {
  const payload = basePayload(span);
  payload.operation = operation;
  const model: Record<string, unknown> = {};
  const provider = firstStr(span.attrs, "gen_ai.system", "gen_ai.provider.name", "llm.provider");
  if (provider !== null) {
    model.provider = provider;
  }
  const name = firstStr(span.attrs, "gen_ai.request.model", "llm.model_name");
  if (name !== null) {
    model.name = name;
  }
  const resolved = firstStr(span.attrs, "gen_ai.response.model");
  if (resolved !== null) {
    model.version_or_digest = resolved;
  }
  if (Object.keys(model).length > 0) {
    payload.model = model;
  }
  const usage: Record<string, unknown> = {};
  const inputTokens = firstInt(
    span.attrs,
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.prompt_tokens",
    "llm.token_count.prompt",
  );
  if (inputTokens !== null) {
    usage.input_tokens = inputTokens;
  }
  const outputTokens = firstInt(
    span.attrs,
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.completion_tokens",
    "llm.token_count.completion",
  );
  if (outputTokens !== null) {
    usage.output_tokens = outputTokens;
  }
  if (Object.keys(usage).length > 0) {
    payload.usage = usage;
  }
  if (hasInput(span)) {
    payload.input_ref = locatorOf(span, "input");
  }
  if (hasOutput(span)) {
    payload.output_ref = locatorOf(span, "output");
  }
  const error = errorOf(span);
  if (error !== null) {
    payload.error = error;
  }
  return payload;
}

function toolCall(span: Span): Record<string, unknown> {
  const payload = basePayload(span);
  const toolName = firstStr(span.attrs, "gen_ai.tool.name", "tool.name") ?? span.name;
  const tool: Record<string, unknown> = { name: toolName };
  const server = firstStr(span.attrs, "gen_ai.tool.server", "server.address");
  if (server !== null) {
    tool.server = server;
  }
  const protocol = firstStr(span.attrs, "gen_ai.tool.protocol");
  if (protocol !== null && ["mcp", "a2a", "http", "native"].includes(protocol)) {
    tool.protocol = protocol;
  }
  payload.tool = tool;
  if (
    ["gen_ai.tool.call.arguments", "tool.parameters", "input.value"].some((k) => k in span.attrs)
  ) {
    payload.args_ref = locatorOf(span, "args");
  }
  if (["gen_ai.tool.call.result", "output.value"].some((k) => k in span.attrs)) {
    payload.result_ref = locatorOf(span, "result");
  }
  const error = errorOf(span);
  if (error !== null) {
    payload.error = error;
  }
  return payload;
}

function resourceAccess(span: Span): Record<string, unknown> {
  const payload = basePayload(span);
  const uri = firstStr(
    span.attrs,
    "gen_ai.data_source.id",
    "retrieval.source",
    "db.collection.name",
  );
  const resource: Record<string, unknown> = { uri: uri ?? span.name };
  const kind = firstStr(span.attrs, "gen_ai.data_source.kind");
  if (kind !== null) {
    resource.kind = kind;
  }
  payload.resource = resource;
  payload.operation = "read";
  const count = firstInt(
    span.attrs,
    "gen_ai.retrieval.document.count",
    "retrieval.documents.count",
  );
  if (count !== null) {
    payload.count = count;
  }
  return payload;
}

function memoryWrite(span: Span): Record<string, unknown> {
  const payload = basePayload(span);
  const store = firstStr(span.attrs, "gen_ai.memory.store");
  if (store !== null) {
    payload.store = store;
  }
  const record = firstStr(span.attrs, "gen_ai.memory.record.id");
  if (record !== null) {
    payload.record_ref = record;
  }
  const trust = firstStr(span.attrs, "gen_ai.memory.trust");
  if (trust !== null && ["trusted", "untrusted", "quarantined"].includes(trust)) {
    payload.trust = trust;
  }
  return payload;
}

function memoryRead(span: Span): Record<string, unknown> {
  const payload = basePayload(span);
  const store = firstStr(span.attrs, "gen_ai.memory.store");
  if (store !== null) {
    payload.store = store;
  }
  const records = span.attrs["gen_ai.memory.record.ids"];
  if (Array.isArray(records)) {
    const recordRefs = records.filter((item): item is string => typeof item === "string");
    if (recordRefs.length > 0) {
      payload.record_refs = recordRefs;
    }
  }
  const trust = firstStr(span.attrs, "gen_ai.memory.trust_min");
  if (trust !== null && ["trusted", "untrusted", "quarantined"].includes(trust)) {
    payload.trust_min = trust;
  }
  return payload;
}

function sessionStart(span: Span): Record<string, unknown> {
  const payload = basePayload(span);
  const environment = firstStr(span.attrs, "deployment.environment.name", "deployment.environment");
  if (environment !== null) {
    payload.environment = environment;
  }
  return payload;
}

function sessionEnd(span: Span): Record<string, unknown> {
  const payload = basePayload(span);
  payload.end_reason = endReason(span);
  return payload;
}

// --- Envelope assembly. -----------------------------------------------------------------------------

function envelope(
  span: Span,
  opts: {
    eventType: string;
    eventId: string;
    time: string;
    payload: Record<string, unknown>;
    subject: string;
    source: string;
    sourceClass: string;
  },
): Record<string, unknown> {
  const event: Record<string, unknown> = {
    specversion: "1.0",
    id: opts.eventId,
    source: opts.source,
    type: `org.agent-conformance.evidence.${opts.eventType}.v1`,
    time: opts.time,
    subject: opts.subject,
    datacontenttype: "application/ld+json",
    agentcesourceclass: opts.sourceClass,
    agentceconv: span.convention,
    data: { "@context": BASE_CONTEXT, "@type": opts.eventType, ...opts.payload },
  };
  if (span.traceId) {
    event.agentcetrace = span.traceId;
  }
  if (span.spanId) {
    event.agentcespan = span.spanId;
  }
  if (span.parentSpanId) {
    event.agentceparent = span.parentSpanId;
  }
  const task = firstStr(span.attrs, "agentce.task", "a2a.task.id", "gen_ai.task.id");
  if (task !== null) {
    event.agentcetask = task;
  }
  return event;
}

function sourceFor(span: Span, override: string | undefined): string {
  if (override !== undefined) {
    return override;
  }
  const service = firstStr(span.attrs, "service.name");
  return service !== null ? `urn:otel:${service}` : "urn:otel:unknown";
}

function mapSpan(
  span: Span,
  operation: string | null,
  opts: { subject: string; sourceClass: string; source: string | undefined },
): Record<string, unknown>[] {
  if (operation === null) {
    return [];
  }
  const resolvedSource = sourceFor(span, opts.source);
  const makeEnvelope = (
    eventType: string,
    eventId: string,
    time: string,
    body: Record<string, unknown>,
  ): Record<string, unknown> =>
    envelope(span, {
      eventType,
      eventId,
      time,
      payload: body,
      subject: opts.subject,
      source: resolvedSource,
      sourceClass: opts.sourceClass,
    });

  const spanTime = span.start ?? span.end;
  if (spanTime === null) {
    return [];
  }
  const baseId = `otel:${span.traceId}/${span.spanId}`;

  if (["chat", "text_completion", "generate_content", "embeddings"].includes(operation)) {
    const modelOp = operation === "embeddings" ? "embeddings" : "chat";
    return [makeEnvelope("ModelCall", baseId, spanTime, modelCall(span, modelOp))];
  }
  if (operation === "execute_tool") {
    return [makeEnvelope("ToolCall", baseId, spanTime, toolCall(span))];
  }
  if (["invoke_agent", "invoke_workflow"].includes(operation)) {
    const endTime = span.end ?? spanTime;
    return [
      makeEnvelope("SessionStart", `${baseId}#session-start`, spanTime, sessionStart(span)),
      makeEnvelope("SessionEnd", `${baseId}#session-end`, endTime, sessionEnd(span)),
    ];
  }
  if (["retrieve", "retrieval"].includes(operation)) {
    return [makeEnvelope("ResourceAccess", baseId, spanTime, resourceAccess(span))];
  }
  if (operation === "memory.write") {
    return [makeEnvelope("MemoryWrite", baseId, spanTime, memoryWrite(span))];
  }
  if (operation === "memory.read") {
    return [makeEnvelope("MemoryRead", baseId, spanTime, memoryRead(span))];
  }
  return [];
}

// --- Public entry point. -----------------------------------------------------------------------------

/**
 * Adapt one OTLP/JSON GenAI trace export into canonical AgentCE evidence events (SPEC 12).
 *
 * `subject` is the assessed subject system and `sourceClass` the adapter's declared trust class
 * (SPEC 6.4) -- both are properties of the deployment, supplied by the collector, never inferred from
 * span contents. `source` overrides the per-span source URI otherwise derived from `service.name`.
 */
export function adapt(payload: Uint8Array, opts: AdaptOptions): AdaptResult {
  const sourceClass = opts.sourceClass ?? "self_report";
  if (!VALID_SOURCE_CLASSES.has(sourceClass)) {
    throw new OtelGenaiAdapterError("bad_source_class", sourceClass);
  }

  let bytes = payload;
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    bytes = bytes.subarray(3); // UTF-8 BOM, matching Python's `utf-8-sig` auto-detection
  }
  let text: string;
  try {
    // `decodeUtf8Strict` keeps a leading BOM rather than stripping it, so a doubled-BOM payload's
    // second BOM survives into `text` as a literal U+FEFF and fails `parseJson` the same way
    // Python's `json.loads` rejects it ("Unexpected UTF-8 BOM") after `utf-8-sig` strips only the
    // first one (18.29 verifier round 1, F2) -- the same decoder `readTextFileStrict` uses.
    text = decodeUtf8Strict(bytes);
  } catch {
    throw new OtelGenaiAdapterError("invalid_encoding", "payload is not valid UTF-8");
  }
  let document: unknown;
  try {
    document = parseJson(text);
  } catch (exc) {
    throw new OtelGenaiAdapterError(
      "invalid_json",
      exc instanceof Error ? exc.message : String(exc),
    );
  }
  if (!isPlainObject(document)) {
    throw new OtelGenaiAdapterError("not_otlp", "top-level value is not a JSON object");
  }

  const events: Record<string, unknown>[] = [];
  const skipped: SkippedSpan[] = [];
  const conventions = new Set<string>();
  let spansSeen = 0;

  for (const span of iterSpans(document)) {
    spansSeen += 1;
    const operation = operationOf(span);
    const emitted = mapSpan(span, operation, {
      subject: opts.subject,
      sourceClass,
      source: opts.source,
    });
    if (emitted.length === 0) {
      const reason = operation === null ? "missing_operation" : "unrecognised_operation";
      skipped.push({ spanId: span.spanId, name: span.name, reason });
      continue;
    }
    conventions.add(span.convention);
    events.push(...emitted);
  }

  events.sort((a, b) => {
    const byTime = byteCompare(a.time as string, b.time as string);
    return byTime !== 0 ? byTime : byteCompare(a.id as string, b.id as string);
  });

  const report: AdapterReport = {
    adapter: "otel-genai",
    conventions: [...conventions].sort(byteCompare),
    spansSeen,
    eventsEmitted: events.length,
    skipped,
  };
  return { events, report };
}
