package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
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
    /** Each declared {@code evaluation.evidence_shape} path -> the IRI of the one node shape it holds. */
    public final Map<String, String> evidenceShapeIris;

    private static final String EVIDENCE_SHAPE_UNRESOLVED = "catalog.evidence_shape.unresolved";

    private Catalog(
            String id,
            String version,
            Path directory,
            List<ControlSpec> controls,
            Map<String, Psp.Shape> shapes,
            Map<String, String> evidenceShapeIris) {
        this.id = id;
        this.version = version;
        this.directory = directory;
        this.controls = controls;
        this.shapes = shapes;
        this.evidenceShapeIris = evidenceShapeIris;
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
        public String evidenceShapePath;
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
        // A declared evidence shape is always resolved: a value that is not a path reads as "", which
        // load refuses, so it can never be skipped into a pass.
        if (evaluation.has("evidence_shape")) {
            JsonNode evidenceShape = evaluation.get("evidence_shape");
            control.evidenceShapePath = evidenceShape.isTextual() ? evidenceShape.textValue() : "";
        }
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
        Map<String, String> evidenceShapeIris = new LinkedHashMap<>();
        for (String controlFile : yamlFiles(directory.resolve("controls"))) {
            JsonNode data = Yaml.parseFile(directory.resolve("controls").resolve(controlFile));
            if (data == null || !data.isObject()) {
                continue;
            }
            ControlSpec control = controlFromDict(data);
            controls.add(control);
            if (control.shapePath != null) {
                shapes.putAll(Psp.loadShapes(directory.resolve(control.shapePath), control.shapePath));
            }
            if (control.evidenceShapePath != null) {
                Map.Entry<String, Map<String, Psp.Shape>> loaded = loadEvidenceShape(directory, control);
                evidenceShapeIris.put(control.evidenceShapePath, loaded.getKey());
                shapes.putAll(loaded.getValue());
            }
        }
        return new Catalog(
                text(metaRecord, "id", ""),
                text(metaRecord, "version", ""),
                directory,
                controls,
                shapes,
                evidenceShapeIris);
    }

    private static boolean hasTarget(Psp.Shape shape) {
        return shape.targetClass != null || !shape.targetNodes.isEmpty() || !shape.targetWhere.isEmpty();
    }

    private static List<String> targeted(Map<String, Psp.Shape> shapes) {
        return shapes.entrySet().stream().filter(e -> hasTarget(e.getValue())).map(Map.Entry::getKey).toList();
    }

    /** The shapes in the file a control names as {@code evaluation.evidence_shape}. The file must be inside
     * the catalog folder after symlinks (where the catalog's signature covers it) and hold exactly one node
     * shape with a target; anything else is refused, so a declared evidence shape can never be skipped and
     * read as a pass. Returns the targeted shape's IRI with the file's shapes. */
    private static Map.Entry<String, Map<String, Psp.Shape>> loadEvidenceShape(Path directory, ControlSpec control) {
        String path = control.evidenceShapePath;
        Map<String, Psp.Shape> loaded = Map.of();
        if (!path.isEmpty()) {
            try {
                Path root = directory.toRealPath();
                Path resolved = directory.resolve(path).toRealPath();
                if (resolved.startsWith(root) && Files.isRegularFile(resolved)) {
                    loaded = Psp.loadShapes(resolved, path);
                }
            } catch (IOException | java.nio.file.InvalidPathException e) {
                // A path that does not resolve leaves nothing loaded, which is refused below.
            }
        }
        List<String> targeted = targeted(loaded);
        if (targeted.size() != 1) {
            throw new InputError(
                    EVIDENCE_SHAPE_UNRESOLVED,
                    ErrorCatalogue.errorCause(EVIDENCE_SHAPE_UNRESOLVED)
                            .replace("{control}", control.id)
                            .replace("{path}", path),
                    ErrorCatalogue.errorFix(EVIDENCE_SHAPE_UNRESOLVED));
        }
        return Map.entry(targeted.get(0), loaded);
    }

    /** The shape a focus must meet for the control to judge it ({@code evaluation.evidence_shape}); a focus
     * that violates it lacks the evidence, so the outcome is insufficient_evidence. */
    public static Psp.Shape evidenceShapeFor(Catalog catalog, ControlSpec control) {
        if (control.evidenceShapePath == null) {
            return null;
        }
        return catalog.shapes.get(catalog.evidenceShapeIris.get(control.evidenceShapePath));
    }

    /** A synthetic catalog carrying only {@code controls}, for tests that need specific
     * {@code minimum_evidence} shapes without a fixture directory on disk. */
    static Catalog forTest(String id, String version, List<ControlSpec> controls) {
        return new Catalog(id, version, null, controls, Map.of(), Map.of());
    }

    /** Files left out of a catalog's provenance digest: the detached signature and {@code catalog.yaml}
     * itself (which carries the provenance block), so the digest covers the catalog's rules and is
     * non-circular (mirrors the Python reference's {@code catalog._PROVENANCE_EXCLUDE} and the
     * TypeScript port's {@code PROVENANCE_EXCLUDE}). */
    private static final Set<String> PROVENANCE_EXCLUDE = Set.of("catalog.sig.json", "catalog.yaml");

    private static void collectFiles(Path dir, String base, List<String> out) {
        if (!Files.isDirectory(dir)) {
            return;
        }
        List<String> names = new ArrayList<>();
        try (Stream<Path> stream = Files.list(dir)) {
            stream.map(p -> p.getFileName().toString()).forEach(names::add);
        } catch (IOException e) {
            throw new IllegalStateException("cannot list " + dir + ": " + e.getMessage(), e);
        }
        names.sort(Json::byteCompare);
        for (String name : names) {
            Path full = dir.resolve(name);
            String rel = base.isEmpty() ? name : base + "/" + name;
            // As Python's `rglob`: never descend a symlinked directory, follow a symlinked file, skip
            // a broken link.
            if (Files.isDirectory(full, LinkOption.NOFOLLOW_LINKS)) {
                collectFiles(full, rel, out);
            } else if (Files.isRegularFile(full)) {
                out.add(rel);
            }
        }
    }

    /** Content-address a directory: {@code sha256:} over the sorted {@code <relpath>\0<filehash>}
     * lines. Ports the Python reference's {@code signing.digest_tree} exactly: sorted by the full
     * POSIX relpath string (byte order, so {@code a-b} sorts before {@code a/x}), {@code exclude}
     * matched against the full relpath (a nested {@code controls/catalog.yaml} is never excluded, only
     * a top-level one), and any path segment that is a dotfile or named {@code __pycache__} is skipped
     * regardless of {@code exclude}. Recomputed from the directory's real bytes every call -- never
     * read from a catalog's stored {@code provenance.digest} field. */
    public static String digestTree(Path dir, Set<String> exclude) {
        List<String> rels = new ArrayList<>();
        collectFiles(dir, "", rels);
        rels.sort(Json::byteCompare);
        List<byte[]> lines = new ArrayList<>();
        for (String rel : rels) {
            if (exclude.contains(rel)) {
                continue;
            }
            boolean skip = false;
            for (String part : rel.split("/")) {
                if (part.startsWith(".") || part.equals("__pycache__")) {
                    skip = true;
                    break;
                }
            }
            if (skip) {
                continue;
            }
            byte[] content;
            try {
                content = Files.readAllBytes(dir.resolve(rel));
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + dir.resolve(rel) + ": " + e.getMessage(), e);
            }
            String fileHash = Canonical.sha256Hex(content);
            lines.add((rel + "\0" + fileHash).getBytes(StandardCharsets.UTF_8));
        }
        ByteArrayOutputStream joined = new ByteArrayOutputStream();
        for (int i = 0; i < lines.size(); i++) {
            if (i > 0) {
                joined.write('\n');
            }
            joined.writeBytes(lines.get(i));
        }
        return "sha256:" + Canonical.sha256Hex(joined.toByteArray());
    }

    /** The catalog's real content digest (SPEC §14.5 CP-3): the same recomputation a reader can
     * independently verify against the catalog directory (mirrors the Python reference's
     * {@code catalog.catalog_provenance_digest} and the TypeScript port's
     * {@code catalogProvenanceDigest}). */
    public static String provenanceDigest(Path directory) {
        return digestTree(directory, PROVENANCE_EXCLUDE);
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
            if (hasTarget(shape)) {
                return shape;
            }
        }
        return null;
    }
}
