package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

/**
 * Load a control catalog (SPEC §7.1, §7.3-7.4): a directory with {@code catalog.yaml} (metadata and the
 * control list), {@code controls/*.yaml} (one control each), and {@code shapes/*.ttl} (the Portable
 * Shape Profile shapes the rung-2 controls reference). A faithful port of the reference loader.
 */
public final class Catalog {
    public final String id;
    public final String version;
    public final Path directory;
    public final List<ControlSpec> controls;
    public final Map<String, Psp.Shape> shapes;

    private Catalog(String id, String version, Path directory, List<ControlSpec> controls, Map<String, Psp.Shape> shapes) {
        this.id = id;
        this.version = version;
        this.directory = directory;
        this.controls = controls;
        this.shapes = shapes;
    }

    public static final class ControlSpec {
        public String id;
        public String version;
        public String title;
        public List<String> appliesToRoles = new ArrayList<>();
        public String mode;
        public int rung;
        public String severity;
        public String minSourceClass;
        public List<JsonNode> minimumEvidence = new ArrayList<>();
        public String shapePath;
        public JsonNode tolerance;
        public List<JsonNode> testCases = new ArrayList<>();
        public JsonNode raw;
    }

    private static String text(JsonNode node, String field, String dflt) {
        JsonNode v = node.get(field);
        return v != null && !v.isNull() ? v.asText() : dflt;
    }

    private static ControlSpec controlFromDict(JsonNode data) {
        JsonNode evaluation = data.get("evaluation");
        if (evaluation == null || !evaluation.isObject()) {
            evaluation = Json.nodes().objectNode();
        }
        JsonNode applicability = data.get("applicability");
        if (applicability == null || !applicability.isObject()) {
            applicability = Json.nodes().objectNode();
        }
        ControlSpec control = new ControlSpec();
        control.id = text(data, "id", "");
        control.version = text(data, "version", "");
        control.title = text(data, "title", "");
        JsonNode roles = applicability.get("applies_to_roles");
        if (roles != null && roles.isArray()) {
            for (JsonNode r : roles) {
                control.appliesToRoles.add(r.asText());
            }
        }
        control.mode = text(evaluation, "mode", "");
        control.rung = Integer.parseInt(text(evaluation, "rung", "0"));
        control.severity = text(data, "severity", "");
        control.minSourceClass = text(evaluation, "min_source_class", "any");
        JsonNode minEvidence = evaluation.get("minimum_evidence");
        if (minEvidence != null && minEvidence.isArray()) {
            minEvidence.forEach(control.minimumEvidence::add);
        }
        JsonNode shape = evaluation.get("shape");
        control.shapePath = shape != null && shape.isTextual() ? shape.textValue() : null;
        JsonNode tolerance = data.get("tolerance");
        if (tolerance != null && tolerance.isObject()) {
            control.tolerance = tolerance;
        } else {
            ObjectNode dflt = Json.nodes().objectNode();
            dflt.put("kind", "count");
            dflt.put("max", 0);
            control.tolerance = dflt;
        }
        JsonNode testCases = data.get("test_cases");
        if (testCases != null && testCases.isArray()) {
            testCases.forEach(control.testCases::add);
        }
        control.raw = data;
        return control;
    }

    private static List<String> yamlFiles(Path directory) {
        List<String> names = new ArrayList<>();
        if (!Files.isDirectory(directory)) {
            return names;
        }
        try (Stream<Path> stream = Files.list(directory)) {
            stream.map(p -> p.getFileName().toString())
                    .filter(n -> n.endsWith(".yaml"))
                    .forEach(names::add);
        } catch (IOException e) {
            return names;
        }
        names.sort(Json::byteCompare);
        return names;
    }

    /** Load {@code catalog.yaml} and every control and shape in {@code directory}. */
    public static Catalog load(Path directory) {
        JsonNode meta = Yaml.parseFile(directory.resolve("catalog.yaml"));
        JsonNode metaRecord = meta != null && meta.isObject() ? meta : Json.nodes().objectNode();
        List<ControlSpec> controls = new ArrayList<>();
        Map<String, Psp.Shape> shapes = new LinkedHashMap<>();
        for (String controlFile : yamlFiles(directory.resolve("controls"))) {
            JsonNode data = Yaml.parseFile(directory.resolve("controls").resolve(controlFile));
            if (data == null || !data.isObject()) {
                continue;
            }
            ControlSpec control = controlFromDict(data);
            controls.add(control);
            if (control.shapePath != null) {
                shapes.putAll(Psp.loadShapes(directory.resolve(control.shapePath)));
            }
        }
        return new Catalog(
                text(metaRecord, "id", ""), text(metaRecord, "version", ""), directory, controls, shapes);
    }

    /** The PSP shape a control evaluates against (by {@code <id>-Shape} suffix, then any shape with targets). */
    public static Psp.Shape shapeFor(Catalog catalog, ControlSpec control) {
        if (control.shapePath == null) {
            return null;
        }
        String want = control.id + "-Shape";
        for (Map.Entry<String, Psp.Shape> e : catalog.shapes.entrySet()) {
            if (e.getKey().endsWith(want)) {
                return e.getValue();
            }
        }
        for (Psp.Shape shape : catalog.shapes.values()) {
            if (shape.targetClass != null || !shape.targetNodes.isEmpty() || !shape.targetWhere.isEmpty()) {
                return shape;
            }
        }
        return null;
    }
}
