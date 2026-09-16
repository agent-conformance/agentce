package org.agentce;

import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Quarantine records: an ingested item the engine refused, with a stable reason (SPEC App. F).
 * Quarantine is an output, never a silent drop. Each record is one line of {@code quarantine.jsonl}.
 */
public final class Quarantine {
    private Quarantine() {}

    public enum Reason {
        SCHEMA_INVALID("schema_invalid"),
        DUPLICATE_ID("duplicate_id"),
        TIME_ORDER("time_order"),
        UNKNOWN_TYPE("unknown_type"),
        UNKNOWN_SOURCE("unknown_source"),
        CLASS_MISMATCH("class_mismatch"),
        OVERSIZE("oversize"),
        CONTEXT_MISMATCH("context_mismatch");

        public final String value;

        Reason(String value) {
            this.value = value;
        }
    }

    /** One quarantine record; every field but {@code reason} is optional. */
    public static final class Record {
        public final Reason reason;
        public String eventId;
        public String source;
        public String stream;
        public String type;
        public String detail;

        public Record(Reason reason) {
            this.reason = reason;
        }

        public Record eventId(String v) {
            this.eventId = v;
            return this;
        }

        public Record source(String v) {
            this.source = v;
            return this;
        }

        public Record stream(String v) {
            this.stream = v;
            return this;
        }

        public Record type(String v) {
            this.type = v;
            return this;
        }

        public Record detail(String v) {
            this.detail = v;
            return this;
        }

        public ObjectNode toJson() {
            ObjectNode out = Json.nodes().objectNode();
            out.put("reason", reason.value);
            if (eventId != null) {
                out.put("event_id", eventId);
            }
            if (source != null) {
                out.put("source", source);
            }
            if (stream != null) {
                out.put("stream", stream);
            }
            if (type != null) {
                out.put("type", type);
            }
            if (detail != null) {
                out.put("detail", detail);
            }
            return out;
        }
    }

    /** A deterministic {@code {reason: count}} map (sorted by reason). */
    public static Map<String, Integer> countsByReason(Iterable<Record> records) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (Record record : records) {
            counts.merge(record.reason.value, 1, Integer::sum);
        }
        return new TreeMap<>(counts);
    }

    /** The reasons of a run, for a summary. */
    public static List<String> reasons(Iterable<Record> records) {
        List<String> out = new ArrayList<>();
        for (Record record : records) {
            out.add(record.reason.value);
        }
        return out;
    }
}
