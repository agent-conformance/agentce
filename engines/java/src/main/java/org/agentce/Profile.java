package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The applicability profile (SPEC §6.5): what the adopter declares about the assessed system — the
 * observation window, the subjects, each subject's evidence sources and trust classes, the independent
 * coverage denominators, the declared decision types and oversight modalities, and the catalogs to
 * apply. Unknown fields are ignored. A faithful port of the reference.
 */
public final class Profile {
    public int profileVersion = 1;
    public Map<String, String> observationWindow = new LinkedHashMap<>();
    public List<Subject> subjects = new ArrayList<>();
    public List<String> catalogs = new ArrayList<>();

    public static final class EvidenceSource {
        public String adapter;
        public String source;
        public String cls;
        public String manifest;
    }

    public static final class CoverageDenominator {
        public String kind;
        public String source;
        public List<String> covers = new ArrayList<>();
        public String manifest;
        public String statement;
    }

    public static final class Subject {
        public String id;
        public String name;
        public String role;
        public List<EvidenceSource> evidenceSources = new ArrayList<>();
        public List<CoverageDenominator> coverageDenominators = new ArrayList<>();
        public List<String> declaredDecisionTypes = new ArrayList<>();
        public Map<String, String> declaredOversight = new LinkedHashMap<>();
        public List<String> declaredComponents = new ArrayList<>();
    }

    /** The independent coverage-denominator source ids of a subject. */
    public static Set<String> denominatorIds(Subject subject) {
        Set<String> ids = new LinkedHashSet<>();
        for (CoverageDenominator d : subject.coverageDenominators) {
            ids.add(d.source);
        }
        return ids;
    }

    private static String opt(JsonNode node) {
        return node != null && node.isTextual() ? node.textValue() : null;
    }

    private static Map<String, String> strMap(JsonNode node) {
        Map<String, String> out = new LinkedHashMap<>();
        if (node != null && node.isObject()) {
            var it = node.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                out.put(e.getKey(), e.getValue().asText());
            }
        }
        return out;
    }

    private static JsonNode arr(JsonNode node, String field) {
        JsonNode v = node.get(field);
        return v != null && v.isArray() ? v : Json.nodes().arrayNode();
    }

    private static Subject parseSubject(JsonNode raw) {
        Subject subject = new Subject();
        subject.id = raw.get("id").asText();
        subject.name = opt(raw.get("name"));
        subject.role = opt(raw.get("role"));
        for (JsonNode dt : arr(raw, "declared_decision_types")) {
            subject.declaredDecisionTypes.add(dt.asText());
        }
        subject.declaredOversight = strMap(raw.get("declared_oversight"));
        for (JsonNode c : arr(raw, "third_party_components")) {
            if (c.isObject() && c.has("name")) {
                subject.declaredComponents.add(c.get("name").asText());
            }
        }
        for (JsonNode entry : arr(raw, "evidence_sources")) {
            if (entry.isObject() && entry.has("source")) {
                EvidenceSource src = new EvidenceSource();
                src.adapter = entry.has("adapter") ? entry.get("adapter").asText() : "";
                src.source = entry.get("source").asText();
                src.cls = entry.has("class") ? entry.get("class").asText() : "";
                src.manifest = opt(entry.get("manifest"));
                subject.evidenceSources.add(src);
            }
        }
        for (JsonNode entry : arr(raw, "coverage_denominators")) {
            if (entry.isObject() && entry.has("source")) {
                CoverageDenominator d = new CoverageDenominator();
                d.kind = entry.has("kind") ? entry.get("kind").asText() : "";
                d.source = entry.get("source").asText();
                for (JsonNode cov : arr(entry, "covers")) {
                    d.covers.add(cov.asText());
                }
                d.manifest = opt(entry.get("manifest"));
                d.statement = opt(entry.get("statement"));
                subject.coverageDenominators.add(d);
            }
        }
        return subject;
    }

    public static Profile fromDict(JsonNode data) {
        Profile profile = new Profile();
        JsonNode pv = data.get("profile_version");
        profile.profileVersion = pv != null && pv.isNumber() ? pv.asInt() : 1;
        profile.observationWindow = strMap(data.get("observation_window"));
        for (JsonNode cat : arr(data, "catalogs")) {
            profile.catalogs.add(cat.asText());
        }
        for (JsonNode raw : arr(data, "subjects")) {
            if (raw.isObject() && raw.has("id")) {
                profile.subjects.add(parseSubject(raw));
            }
        }
        return profile;
    }

    public static Profile load(Path path) {
        JsonNode data = Yaml.parseFile(path);
        if (data == null || !data.isObject()) {
            return new Profile();
        }
        return fromDict(data);
    }
}
