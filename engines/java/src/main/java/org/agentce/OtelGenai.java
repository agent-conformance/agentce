package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.NullNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.math.BigInteger;
import java.nio.charset.CharacterCodingException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;

/**
 * The {@code otel-genai} adapter (SPEC 12.1): OpenTelemetry GenAI / OpenInference spans -> canonical
 * AgentCE evidence events. Ported byte-identical from the hardened Python reference ({@code
 * engines/python/agentce/records/otel_genai.py}), per contract {@code P18-18.29} draft 3. A pure
 * function {@code bytes -> (events, AdapterReport)} over an OTLP/JSON trace export; no network, no
 * learned component.
 */
public final class OtelGenai {
    private OtelGenai() {}

    public static final String BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1";

    private static final Set<String> VALID_SOURCE_CLASSES =
            Set.of("self_report", "enforcement_point", "independent_system");
    private static final int MAX_INT_STR_DIGITS = 4300; // CPython's sys.int_max_str_digits default
    private static final Pattern ALL_DIGITS = Pattern.compile("^[0-9]+$");
    private static final BigInteger NANOS_PER_MILLI = BigInteger.valueOf(1_000_000);
    private static final BigInteger MILLIS_PER_SECOND = BigInteger.valueOf(1000);
    private static final BigInteger YEAR_10000_SECONDS = new BigInteger("253402300800");

    /** The input could not be adapted. {@code reason} is a stable key, matching the Python reference's. */
    public static final class OtelGenaiAdapterError extends RuntimeException {
        private static final long serialVersionUID = 1L;
        public final String reason;

        public OtelGenaiAdapterError(String reason, String detail) {
            super(detail == null || detail.isEmpty() ? reason : reason + ": " + detail);
            this.reason = reason;
        }
    }

    /** A source span the adapter did not map, recorded rather than dropped silently (SPEC 12.1). */
    public record SkippedSpan(String spanId, String name, String reason) {}

    /** What the adapter did with one export: the conventions it mapped and what it could not map. */
    public record AdapterReport(
            String adapter, List<String> conventions, int spansSeen, int eventsEmitted, List<SkippedSpan> skipped) {}

    /** The events an export produced, in canonical time order, and the run report. */
    public record AdaptResult(List<JsonNode> events, AdapterReport report) {}

    /** {@link #envelope}, with a span's {@code subject}/{@code source}/{@code sourceClass} already
     * bound -- {@code mapSpan}'s own branches vary only {@code eventType}/{@code eventId}/{@code
     * time}/{@code payload}, matching the closure {@code makeEnvelope}/{@code envelope} in the
     * TypeScript and Python ports. */
    @FunctionalInterface
    private interface EnvelopeMaker {
        ObjectNode make(String eventType, String eventId, String time, ObjectNode payload);
    }

    /** The decoded fields of one OTLP span the mapper needs. */
    private record Span(
            String traceId,
            String spanId,
            String parentSpanId,
            String name,
            String start,
            String end,
            ObjectNode attrs,
            int statusCode,
            String statusMessage,
            String convention) {}

    // --- OTLP/JSON decoding helpers. -----------------------------------------------------------

    /**
     * The exact grammar CPython's {@code int(str)} accepts: trim ASCII whitespace, an optional
     * single leading sign, then digits with at most one {@code _} between any two consecutive
     * digits (never leading, trailing, or doubled), and no more than {@code
     * sys.int_max_str_digits} digits. Returns a {@code BigIntegerNode} of full precision -- unlike
     * the TypeScript port, no out-of-range wrapper is needed here, since {@link Canonical#canonicalString}
     * already refuses a too-large integer reading the same {@code JsonNode} directly.
     *
     * <p>One linear pass, never a regex: {@code java.util.regex} matches a repeated group by
     * recursing once per character, so a few thousand digits overflowed the stack before the digit
     * cap was ever checked (18.29 verifier round 2, F3).
     */
    private static JsonNode parsePythonIntGrammar(String raw) {
        int start = 0;
        int end = raw.length();
        while (start < end && isAsciiSpace(raw.charAt(start))) {
            start++;
        }
        while (end > start && isAsciiSpace(raw.charAt(end - 1))) {
            end--;
        }
        boolean negative = false;
        if (start < end && (raw.charAt(start) == '+' || raw.charAt(start) == '-')) {
            negative = raw.charAt(start) == '-';
            start++;
        }
        StringBuilder digits = new StringBuilder(end - start + 1);
        if (negative) {
            digits.append('-');
        }
        boolean afterDigit = false;
        for (int i = start; i < end; i++) {
            char c = raw.charAt(i);
            if (c >= '0' && c <= '9') {
                digits.append(c);
                afterDigit = true;
            } else if (c == '_' && afterDigit) {
                afterDigit = false;
            } else {
                return null;
            }
        }
        int digitCount = digits.length() - (negative ? 1 : 0);
        if (!afterDigit || digitCount > MAX_INT_STR_DIGITS) {
            return null;
        }
        return Json.nodes().numberNode(new BigInteger(digits.toString()));
    }

    /** The whitespace {@code int(str)} trims, ASCII only (contract disposition 1): space and \t through \r. */
    private static boolean isAsciiSpace(char c) {
        return c == ' ' || (c >= '\t' && c <= '\r');
    }

    /** Decode one OTLP {@code AnyValue} to a plain scalar/list/object (protobuf-JSON encoding). */
    private static JsonNode anyValue(JsonNode value) {
        if (value == null || !value.isObject()) {
            return NullNode.instance;
        }
        if (value.has("stringValue")) {
            return value.get("stringValue");
        }
        if (value.has("intValue")) {
            // int64 is JSON-encoded as a string in the OTLP/JSON protobuf mapping; a non-integer
            // string (malformed telemetry) is treated the same as any other unmapped value, never
            // raised. A bare (non-string) number is passed through as-is.
            JsonNode raw = value.get("intValue");
            if (!raw.isTextual()) {
                return raw;
            }
            JsonNode parsed = parsePythonIntGrammar(raw.textValue());
            return parsed != null ? parsed : NullNode.instance;
        }
        if (value.has("boolValue")) {
            return value.get("boolValue");
        }
        if (value.has("doubleValue")) {
            return value.get("doubleValue");
        }
        if (value.has("arrayValue")) {
            JsonNode inner = value.get("arrayValue");
            JsonNode items = inner != null && inner.isObject() ? inner.get("values") : null;
            ArrayNode out = Json.nodes().arrayNode();
            if (items == null || !items.isArray()) {
                return out;
            }
            for (JsonNode item : items) {
                out.add(anyValue(item));
            }
            return out;
        }
        if (value.has("kvlistValue")) {
            JsonNode inner = value.get("kvlistValue");
            JsonNode pairs = inner != null && inner.isObject() ? inner.get("values") : null;
            ObjectNode out = Json.nodes().objectNode();
            if (pairs == null || !pairs.isArray()) {
                return out;
            }
            for (JsonNode pair : pairs) {
                JsonNode keyNode = pair.isObject() ? pair.get("key") : null;
                if (keyNode != null && keyNode.isTextual()) {
                    out.set(keyNode.textValue(), anyValue(pair.get("value")));
                }
            }
            return out;
        }
        return NullNode.instance;
    }

    /** Turn an OTLP attribute list {@code [{key, value}]} into a flat {@code {key: scalar}} map. */
    private static ObjectNode attributesFromList(JsonNode raw) {
        ObjectNode out = Json.nodes().objectNode();
        if (raw != null && raw.isArray()) {
            for (JsonNode item : raw) {
                JsonNode keyNode = item.isObject() ? item.get("key") : null;
                if (keyNode != null && keyNode.isTextual()) {
                    out.set(keyNode.textValue(), anyValue(item.get("value")));
                }
            }
        }
        return out;
    }

    private static String asStr(JsonNode value) {
        return value != null && value.isTextual() && !value.textValue().isEmpty() ? value.textValue() : null;
    }

    private static JsonNode asInt(JsonNode value) {
        if (value == null || value.isMissingNode() || value.isNull()) {
            return null;
        }
        if (value.isBoolean()) {
            return null;
        }
        if (value.isIntegralNumber()) {
            return value;
        }
        if (value.isTextual()) {
            return parsePythonIntGrammar(value.textValue());
        }
        return null;
    }

    private static boolean hasAny(ObjectNode attrs, String... keys) {
        for (String key : keys) {
            if (attrs.has(key)) {
                return true;
            }
        }
        return false;
    }

    /** Format a Unix-nanoseconds timestamp as canonical RFC 3339 UTC with millisecond precision. */
    private static String rfc3339Millis(JsonNode unixNanosRaw) {
        JsonNode parsed = asInt(unixNanosRaw);
        if (parsed == null) {
            return null;
        }
        BigInteger nanos = parsed.bigIntegerValue();
        if (nanos.signum() < 0) {
            return null;
        }
        BigInteger millisTotal = nanos.divide(NANOS_PER_MILLI); // truncate; never round up
        BigInteger[] divRem = millisTotal.divideAndRemainder(MILLIS_PER_SECOND);
        BigInteger seconds = divRem[0];
        BigInteger millis = divRem[1];
        if (seconds.compareTo(YEAR_10000_SECONDS) >= 0) {
            // A timestamp so far out of range the platform clock cannot represent it (year 10000
            // and beyond) is treated as no timestamp at all, never raised.
            return null;
        }
        Instant instant = Instant.ofEpochSecond(seconds.longValueExact());
        LocalDateTime dt = LocalDateTime.ofInstant(instant, ZoneOffset.UTC);
        return String.format(
                Locale.ROOT,
                "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                dt.getYear(),
                dt.getMonthValue(),
                dt.getDayOfMonth(),
                dt.getHour(),
                dt.getMinute(),
                dt.getSecond(),
                millis.intValue());
    }

    // --- Convention detection. ------------------------------------------------------------------

    /** Extract {@code <major.minor>} from the first OTLP schema URL that carries one (ASCII digits only). */
    private static String otelGenaiVersion(JsonNode... schemaUrls) {
        for (JsonNode candidate : schemaUrls) {
            String url = asStr(candidate);
            if (url == null) {
                continue;
            }
            int end = url.length();
            while (end > 0 && url.charAt(end - 1) == '/') {
                end--; // Python's url.rstrip("/"): a linear scan, not a per-start-position regex
            }
            String stripped = url.substring(0, end);
            int idx = stripped.lastIndexOf('/');
            String tail = idx == -1 ? stripped : stripped.substring(idx + 1);
            String[] parts = tail.split("\\.", -1);
            if (parts.length >= 2
                    && ALL_DIGITS.matcher(parts[0]).matches()
                    && ALL_DIGITS.matcher(parts[1]).matches()) {
                return parts[0] + "." + parts[1];
            }
        }
        return null;
    }

    private static String conventionFor(
            String scopeName, String scopeVersion, JsonNode scopeSchema, JsonNode resourceSchema, ObjectNode attrs) {
        if (attrs.has("openinference.span.kind") || scopeName.startsWith("openinference")) {
            return scopeVersion != null ? "openinference:" + scopeVersion : "openinference";
        }
        String version = otelGenaiVersion(scopeSchema, resourceSchema);
        return version != null ? "otel-genai:" + version : "otel-genai";
    }

    // --- Span iteration. -------------------------------------------------------------------------

    private static List<Span> iterSpans(ObjectNode document) {
        JsonNode resourceSpansNode = document.get("resourceSpans");
        if (resourceSpansNode == null || !resourceSpansNode.isArray()) {
            throw new OtelGenaiAdapterError("not_otlp", "document has no resourceSpans array");
        }
        List<Span> spans = new ArrayList<>();
        for (JsonNode resourceSpan : resourceSpansNode) {
            if (!resourceSpan.isObject()) {
                continue;
            }
            JsonNode resource = resourceSpan.get("resource");
            ObjectNode resourceAttrs =
                    attributesFromList(resource != null && resource.isObject() ? resource.get("attributes") : null);
            JsonNode resourceSchema = resourceSpan.get("schemaUrl");
            JsonNode scopeSpansNode = resourceSpan.get("scopeSpans");
            if (scopeSpansNode == null || !scopeSpansNode.isArray()) {
                continue;
            }
            for (JsonNode scopeSpan : scopeSpansNode) {
                if (!scopeSpan.isObject()) {
                    continue;
                }
                JsonNode scope = scopeSpan.get("scope");
                String scopeName = asStr(scope != null && scope.isObject() ? scope.get("name") : null);
                if (scopeName == null) {
                    scopeName = "";
                }
                String scopeVersion = asStr(scope != null && scope.isObject() ? scope.get("version") : null);
                JsonNode scopeSchema = scopeSpan.get("schemaUrl");
                JsonNode spanListNode = scopeSpan.get("spans");
                if (spanListNode == null || !spanListNode.isArray()) {
                    continue;
                }
                for (JsonNode span : spanListNode) {
                    if (!span.isObject()) {
                        continue;
                    }
                    ObjectNode attrs = attributesFromList(span.get("attributes"));
                    String convention = conventionFor(scopeName, scopeVersion, scopeSchema, resourceSchema, attrs);
                    JsonNode status = span.get("status");
                    int statusCode = 0;
                    String statusMessage = null;
                    if (status != null && status.isObject()) {
                        JsonNode codeVal = asInt(status.get("code"));
                        statusCode = codeVal != null ? codeVal.intValue() : 0;
                        statusMessage = asStr(status.get("message"));
                    }
                    ObjectNode merged = Json.nodes().objectNode();
                    merged.setAll(resourceAttrs);
                    merged.setAll(attrs);
                    String traceId = asStr(span.get("traceId"));
                    String spanId = asStr(span.get("spanId"));
                    String name = asStr(span.get("name"));
                    spans.add(new Span(
                            traceId != null ? traceId : "",
                            spanId != null ? spanId : "",
                            asStr(span.get("parentSpanId")),
                            name != null ? name : "",
                            rfc3339Millis(span.get("startTimeUnixNano")),
                            rfc3339Millis(span.get("endTimeUnixNano")),
                            merged,
                            statusCode,
                            statusMessage,
                            convention));
                }
            }
        }
        return spans;
    }

    // --- Span mapping. ---------------------------------------------------------------------------

    private static final Map<String, String> OPENINFERENCE_KIND_MAP = Map.of(
            "LLM", "chat",
            "EMBEDDING", "embeddings",
            "TOOL", "execute_tool",
            "AGENT", "invoke_agent",
            "RETRIEVER", "retrieve");
    private static final Set<String> MODEL_OPS = Set.of("chat", "text_completion", "generate_content", "embeddings");
    private static final Set<String> AGENT_OPS = Set.of("invoke_agent", "invoke_workflow");
    private static final Set<String> RETRIEVE_OPS = Set.of("retrieve", "retrieval");
    private static final Set<String> TOOL_PROTOCOLS = Set.of("mcp", "a2a", "http", "native");
    private static final Set<String> TRUST_VALUES = Set.of("trusted", "untrusted", "quarantined");

    private static String openinferenceOperation(ObjectNode attrs) {
        String kind = asStr(attrs.get("openinference.span.kind"));
        if (kind == null) {
            return null;
        }
        return OPENINFERENCE_KIND_MAP.getOrDefault(kind, kind);
    }

    private static String operationOf(Span span) {
        if (span.convention().startsWith("openinference")) {
            return openinferenceOperation(span.attrs());
        }
        return asStr(span.attrs().get("gen_ai.operation.name"));
    }

    private static ObjectNode agentRef(ObjectNode attrs) {
        String agentId = asStr(attrs.get("gen_ai.agent.id"));
        if (agentId == null) {
            return null;
        }
        ObjectNode agent = Json.nodes().objectNode();
        agent.put("id", agentId);
        String name = asStr(attrs.get("gen_ai.agent.name"));
        if (name != null) {
            agent.put("name", name);
        }
        return agent;
    }

    private static String firstStr(ObjectNode attrs, String... keys) {
        for (String key : keys) {
            String found = asStr(attrs.get(key));
            if (found != null) {
                return found;
            }
        }
        return null;
    }

    private static JsonNode firstInt(ObjectNode attrs, String... keys) {
        for (String key : keys) {
            if (attrs.has(key)) {
                JsonNode found = asInt(attrs.get(key));
                if (found != null) {
                    return found;
                }
            }
        }
        return null;
    }

    private static String locatorOf(Span span, String fragment) {
        return "otel:" + span.traceId() + "/" + span.spanId() + "#" + fragment;
    }

    private static String endReason(Span span) {
        return span.statusCode() == 2 ? "error" : "completed";
    }

    private static String errorOf(Span span) {
        if (span.statusCode() != 2) {
            return null;
        }
        return span.statusMessage() != null ? span.statusMessage() : "error";
    }

    private static ObjectNode basePayload(Span span) {
        ObjectNode payload = Json.nodes().objectNode();
        ObjectNode agent = agentRef(span.attrs());
        if (agent != null) {
            payload.set("agent", agent);
        }
        String sessionId = firstStr(span.attrs(), "gen_ai.conversation.id", "session.id");
        if (sessionId != null) {
            payload.put("session_id", sessionId);
        }
        return payload;
    }

    private static ObjectNode modelCall(Span span, String operation) {
        ObjectNode payload = basePayload(span);
        payload.put("operation", operation);
        ObjectNode model = Json.nodes().objectNode();
        String provider = firstStr(span.attrs(), "gen_ai.system", "gen_ai.provider.name", "llm.provider");
        if (provider != null) {
            model.put("provider", provider);
        }
        String name = firstStr(span.attrs(), "gen_ai.request.model", "llm.model_name");
        if (name != null) {
            model.put("name", name);
        }
        String resolved = firstStr(span.attrs(), "gen_ai.response.model");
        if (resolved != null) {
            model.put("version_or_digest", resolved);
        }
        if (model.size() > 0) {
            payload.set("model", model);
        }
        ObjectNode usage = Json.nodes().objectNode();
        JsonNode inputTokens = firstInt(
                span.attrs(), "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens", "llm.token_count.prompt");
        if (inputTokens != null) {
            usage.set("input_tokens", inputTokens);
        }
        JsonNode outputTokens = firstInt(
                span.attrs(),
                "gen_ai.usage.output_tokens",
                "gen_ai.usage.completion_tokens",
                "llm.token_count.completion");
        if (outputTokens != null) {
            usage.set("output_tokens", outputTokens);
        }
        if (usage.size() > 0) {
            payload.set("usage", usage);
        }
        if (hasAny(span.attrs(), "gen_ai.input.messages", "gen_ai.prompt", "input.value")) {
            payload.put("input_ref", locatorOf(span, "input"));
        }
        if (hasAny(span.attrs(), "gen_ai.output.messages", "gen_ai.completion", "output.value")) {
            payload.put("output_ref", locatorOf(span, "output"));
        }
        String error = errorOf(span);
        if (error != null) {
            payload.put("error", error);
        }
        return payload;
    }

    private static ObjectNode toolCall(Span span) {
        ObjectNode payload = basePayload(span);
        String toolName = firstStr(span.attrs(), "gen_ai.tool.name", "tool.name");
        if (toolName == null) {
            toolName = span.name();
        }
        ObjectNode tool = Json.nodes().objectNode();
        tool.put("name", toolName);
        String server = firstStr(span.attrs(), "gen_ai.tool.server", "server.address");
        if (server != null) {
            tool.put("server", server);
        }
        String protocol = firstStr(span.attrs(), "gen_ai.tool.protocol");
        if (protocol != null && TOOL_PROTOCOLS.contains(protocol)) {
            tool.put("protocol", protocol);
        }
        payload.set("tool", tool);
        if (hasAny(span.attrs(), "gen_ai.tool.call.arguments", "tool.parameters", "input.value")) {
            payload.put("args_ref", locatorOf(span, "args"));
        }
        if (hasAny(span.attrs(), "gen_ai.tool.call.result", "output.value")) {
            payload.put("result_ref", locatorOf(span, "result"));
        }
        String error = errorOf(span);
        if (error != null) {
            payload.put("error", error);
        }
        return payload;
    }

    private static ObjectNode resourceAccess(Span span) {
        ObjectNode payload = basePayload(span);
        String uri = firstStr(span.attrs(), "gen_ai.data_source.id", "retrieval.source", "db.collection.name");
        ObjectNode resource = Json.nodes().objectNode();
        resource.put("uri", uri != null ? uri : span.name());
        String kind = firstStr(span.attrs(), "gen_ai.data_source.kind");
        if (kind != null) {
            resource.put("kind", kind);
        }
        payload.set("resource", resource);
        payload.put("operation", "read");
        JsonNode count =
                firstInt(span.attrs(), "gen_ai.retrieval.document.count", "retrieval.documents.count");
        if (count != null) {
            payload.set("count", count);
        }
        return payload;
    }

    private static ObjectNode memoryWrite(Span span) {
        ObjectNode payload = basePayload(span);
        String store = firstStr(span.attrs(), "gen_ai.memory.store");
        if (store != null) {
            payload.put("store", store);
        }
        String record = firstStr(span.attrs(), "gen_ai.memory.record.id");
        if (record != null) {
            payload.put("record_ref", record);
        }
        String trust = firstStr(span.attrs(), "gen_ai.memory.trust");
        if (trust != null && TRUST_VALUES.contains(trust)) {
            payload.put("trust", trust);
        }
        return payload;
    }

    private static ObjectNode memoryRead(Span span) {
        ObjectNode payload = basePayload(span);
        String store = firstStr(span.attrs(), "gen_ai.memory.store");
        if (store != null) {
            payload.put("store", store);
        }
        JsonNode records = span.attrs().get("gen_ai.memory.record.ids");
        if (records != null && records.isArray()) {
            ArrayNode refs = Json.nodes().arrayNode();
            for (JsonNode item : records) {
                if (item.isTextual()) {
                    refs.add(item.textValue());
                }
            }
            if (refs.size() > 0) {
                payload.set("record_refs", refs);
            }
        }
        String trust = firstStr(span.attrs(), "gen_ai.memory.trust_min");
        if (trust != null && TRUST_VALUES.contains(trust)) {
            payload.put("trust_min", trust);
        }
        return payload;
    }

    private static ObjectNode sessionStart(Span span) {
        ObjectNode payload = basePayload(span);
        String environment = firstStr(span.attrs(), "deployment.environment.name", "deployment.environment");
        if (environment != null) {
            payload.put("environment", environment);
        }
        return payload;
    }

    private static ObjectNode sessionEnd(Span span) {
        ObjectNode payload = basePayload(span);
        payload.put("end_reason", endReason(span));
        return payload;
    }

    // --- Envelope assembly. ------------------------------------------------------------------------

    private static ObjectNode envelope(
            Span span,
            String eventType,
            String eventId,
            String time,
            ObjectNode payload,
            String subject,
            String source,
            String sourceClass) {
        ObjectNode event = Json.nodes().objectNode();
        event.put("specversion", "1.0");
        event.put("id", eventId);
        event.put("source", source);
        event.put("type", "org.agent-conformance.evidence." + eventType + ".v1");
        event.put("time", time);
        event.put("subject", subject);
        event.put("datacontenttype", "application/ld+json");
        event.put("agentcesourceclass", sourceClass);
        event.put("agentceconv", span.convention());
        ObjectNode data = Json.nodes().objectNode();
        data.put("@context", BASE_CONTEXT);
        data.put("@type", eventType);
        data.setAll(payload);
        event.set("data", data);
        if (!span.traceId().isEmpty()) {
            event.put("agentcetrace", span.traceId());
        }
        if (!span.spanId().isEmpty()) {
            event.put("agentcespan", span.spanId());
        }
        if (span.parentSpanId() != null) {
            event.put("agentceparent", span.parentSpanId());
        }
        String task = firstStr(span.attrs(), "agentce.task", "a2a.task.id", "gen_ai.task.id");
        if (task != null) {
            event.put("agentcetask", task);
        }
        return event;
    }

    private static String sourceFor(Span span, String override) {
        if (override != null) {
            return override;
        }
        String service = firstStr(span.attrs(), "service.name");
        return service != null ? "urn:otel:" + service : "urn:otel:unknown";
    }

    private static List<JsonNode> mapSpan(Span span, String operation, String subject, String sourceClass, String source) {
        if (operation == null) {
            return List.of();
        }
        String resolvedSource = sourceFor(span, source);
        String spanTime = span.start() != null ? span.start() : span.end();
        if (spanTime == null) {
            return List.of();
        }
        String baseId = "otel:" + span.traceId() + "/" + span.spanId();
        EnvelopeMaker makeEnvelope = (eventType, eventId, time, payload) ->
                envelope(span, eventType, eventId, time, payload, subject, resolvedSource, sourceClass);

        if (MODEL_OPS.contains(operation)) {
            String modelOp = operation.equals("embeddings") ? "embeddings" : "chat";
            return List.of(makeEnvelope.make("ModelCall", baseId, spanTime, modelCall(span, modelOp)));
        }
        if (operation.equals("execute_tool")) {
            return List.of(makeEnvelope.make("ToolCall", baseId, spanTime, toolCall(span)));
        }
        if (AGENT_OPS.contains(operation)) {
            String endTime = span.end() != null ? span.end() : spanTime;
            return List.of(
                    makeEnvelope.make("SessionStart", baseId + "#session-start", spanTime, sessionStart(span)),
                    makeEnvelope.make("SessionEnd", baseId + "#session-end", endTime, sessionEnd(span)));
        }
        if (RETRIEVE_OPS.contains(operation)) {
            return List.of(makeEnvelope.make("ResourceAccess", baseId, spanTime, resourceAccess(span)));
        }
        if (operation.equals("memory.write")) {
            return List.of(makeEnvelope.make("MemoryWrite", baseId, spanTime, memoryWrite(span)));
        }
        if (operation.equals("memory.read")) {
            return List.of(makeEnvelope.make("MemoryRead", baseId, spanTime, memoryRead(span)));
        }
        return List.of();
    }

    // --- Public entry point. -------------------------------------------------------------------------

    /**
     * Adapt one OTLP/JSON GenAI trace export into canonical AgentCE evidence events (SPEC 12).
     *
     * <p>{@code subject} is the assessed subject system and {@code sourceClass} the adapter's
     * declared trust class (SPEC 6.4) -- both are properties of the deployment, supplied by the
     * collector, never inferred from span contents. {@code source} overrides the per-span source
     * URI otherwise derived from {@code service.name}. {@code sourceClass} may be {@code null},
     * defaulting to {@code self_report}.
     */
    public static AdaptResult adapt(byte[] payload, String subject, String sourceClass, String source) {
        String resolvedSourceClass = sourceClass != null ? sourceClass : "self_report";
        if (!VALID_SOURCE_CLASSES.contains(resolvedSourceClass)) {
            throw new OtelGenaiAdapterError("bad_source_class", resolvedSourceClass);
        }

        byte[] bytes = payload;
        if (bytes.length >= 3 && (bytes[0] & 0xFF) == 0xEF && (bytes[1] & 0xFF) == 0xBB && (bytes[2] & 0xFF) == 0xBF) {
            // UTF-8 BOM, matching Python's `utf-8-sig` auto-detection.
            bytes = Arrays.copyOfRange(bytes, 3, bytes.length);
        }
        String text;
        try {
            text = Verify.decodeStrict(bytes).toString();
        } catch (CharacterCodingException e) {
            throw new OtelGenaiAdapterError("invalid_encoding", "payload is not valid UTF-8");
        }
        JsonNode document;
        try {
            document = Json.parse(text);
        } catch (IllegalArgumentException e) {
            throw new OtelGenaiAdapterError("invalid_json", e.getMessage());
        }
        if (!document.isObject()) {
            throw new OtelGenaiAdapterError("not_otlp", "top-level value is not a JSON object");
        }

        List<JsonNode> events = new ArrayList<>();
        List<SkippedSpan> skipped = new ArrayList<>();
        TreeSet<String> conventions = new TreeSet<>(Json::byteCompare);
        int spansSeen = 0;

        for (Span span : iterSpans((ObjectNode) document)) {
            spansSeen += 1;
            String operation = operationOf(span);
            List<JsonNode> emitted = mapSpan(span, operation, subject, resolvedSourceClass, source);
            if (emitted.isEmpty()) {
                String reason = operation == null ? "missing_operation" : "unrecognised_operation";
                skipped.add(new SkippedSpan(span.spanId(), span.name(), reason));
                continue;
            }
            conventions.add(span.convention());
            events.addAll(emitted);
        }

        events.sort((a, b) -> {
            int byTime = Json.byteCompare(a.get("time").asText(), b.get("time").asText());
            return byTime != 0 ? byTime : Json.byteCompare(a.get("id").asText(), b.get("id").asText());
        });

        AdapterReport report =
                new AdapterReport("otel-genai", new ArrayList<>(conventions), spansSeen, events.size(), skipped);
        return new AdaptResult(events, report);
    }
}
