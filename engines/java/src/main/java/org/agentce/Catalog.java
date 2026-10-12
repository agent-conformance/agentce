package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.FileSystemLoopException;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.HashSet;
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
    private static final String SHAPE_UNRESOLVED = "catalog.shape.unresolved";

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
        // A non-string shape reads as "" (not a covered file), which load refuses; null stays "no shape".
        control.shapePath = shape == null || shape.isNull() ? null : shape.isTextual() ? shape.textValue() : "";
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

    /** The catalog files the digest covers ({@link #coveredFiles}, less the signature): the only files
     * {@link #load} reads. */
    private static Set<String> covered(Path directory) {
        return new HashSet<>(coveredFiles(directory, Set.of("catalog.sig.json")));
    }

    /** The covered {@code controls/<name>.yaml} files, one level deep as the listing always was, sorted. */
    private static List<String> controlFiles(Set<String> files) {
        List<String> rels = new ArrayList<>();
        for (String rel : files) {
            if (rel.startsWith("controls/") && rel.indexOf('/', "controls/".length()) < 0 && rel.endsWith(".yaml")) {
                rels.add(rel);
            }
        }
        rels.sort(Json::byteCompare);
        return rels;
    }

    private static InputError refuseUncovered(String key, ControlSpec control, String path) {
        return new InputError(
                key,
                ErrorCatalogue.errorCause(key).replace("{control}", control.id).replace("{path}", path),
                ErrorCatalogue.errorFix(key));
    }

    /** Load {@code catalog.yaml} and every control and shape in {@code directory}. Only files the catalog
     * digest covers are read, each by its exact name ({@link #coveredFiles}, 18.168): a dot-named control
     * file is never loaded, and a shape path that names anything else is refused, so a signed catalog
     * cannot change what it loads without changing its digest. */
    public static Catalog load(Path directory) {
        JsonNode meta = Yaml.parseFile(directory.resolve("catalog.yaml"));
        JsonNode metaRecord = meta != null && meta.isObject() ? meta : Json.nodes().objectNode();
        List<ControlSpec> controls = new ArrayList<>();
        Map<String, Psp.Shape> shapes = new LinkedHashMap<>();
        Map<String, String> evidenceShapeIris = new LinkedHashMap<>();
        Set<String> files = covered(directory);
        for (String rel : controlFiles(files)) {
            JsonNode data = Yaml.parseFile(directory.resolve(rel));
            if (data == null || !data.isObject()) {
                continue;
            }
            ControlSpec control = controlFromDict(data);
            controls.add(control);
            if (control.shapePath != null) {
                if (!files.contains(control.shapePath)) {
                    throw refuseUncovered(SHAPE_UNRESOLVED, control, control.shapePath);
                }
                shapes.putAll(Psp.loadShapes(directory.resolve(control.shapePath), control.shapePath));
            }
            if (control.evidenceShapePath != null) {
                Map.Entry<String, Map<String, Psp.Shape>> loaded = loadEvidenceShape(directory, control, files);
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
        return !shape.targetClasses.isEmpty() || !shape.targetNodes.isEmpty() || !shape.targetWhere.isEmpty();
    }

    private static List<String> targeted(Map<String, Psp.Shape> shapes) {
        return shapes.entrySet().stream().filter(e -> hasTarget(e.getValue())).map(Map.Entry::getKey).toList();
    }

    /** The shapes in the file a control names as {@code evaluation.evidence_shape}. The file must be inside
     * the catalog folder after symlinks (where the catalog's signature covers it) and hold exactly one node
     * shape with a target; anything else is refused, so a declared evidence shape can never be skipped and
     * read as a pass. Returns the targeted shape's IRI with the file's shapes. */
    private static Map.Entry<String, Map<String, Psp.Shape>> loadEvidenceShape(
            Path directory, ControlSpec control, Set<String> files) {
        String path = control.evidenceShapePath;
        Map<String, Psp.Shape> loaded = Map.of();
        if (files.contains(path)) {
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
            throw refuseUncovered(EVIDENCE_SHAPE_UNRESOLVED, control, path);
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

    /** A folder's identity: its file key (device and inode on Unix), never a path string, which a
     * case-insensitive filesystem spells many ways; its real path where the filesystem gives no key. */
    private static Object folderIdentity(Path dir) {
        try {
            Object key = Files.readAttributes(dir, BasicFileAttributes.class).fileKey();
            return key != null ? key : dir.toRealPath();
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + dir + ": " + e.getMessage(), e);
        }
    }

    /** Every file's POSIX relpath under {@code dir}. As Python's {@code signing._walk_files}: a
     * symlinked folder is descended like a real one and a symlinked file is followed (18.164), a broken
     * link is skipped, and a folder entered a second time (a cycle, or two names for one folder) is
     * refused naming the second name, so each folder is entered once. */
    private static void collectFiles(Path dir, String base, List<String> out, Set<Object> entered) {
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
            if (Files.isDirectory(full)) {
                if (!entered.add(folderIdentity(full))) {
                    throw new IllegalStateException(
                            "folder reached twice: " + full,
                            new FileSystemLoopException(full.toString()));
                }
                collectFiles(full, rel, out, entered);
            } else if (Files.isRegularFile(full)) {
                out.add(rel);
            }
        }
    }

    /** A path part the catalog digest never hashes below: a dot-named file or folder, or {@code __pycache__}. */
    static boolean skippedPart(String part) {
        return part.startsWith(".") || part.equals("__pycache__");
    }

    /** The POSIX relpaths {@link #digestTree} hashes, sorted (byte order): every file {@code collectFiles}
     * reaches, less any path with a {@link #skippedPart} and any name in {@code exclude}. As Python's
     * {@code signing.covered_files}: the catalog loaders read only these, by exact name (18.168), so nothing
     * a loader reads can change without changing the digest. */
    public static List<String> coveredFiles(Path dir, Set<String> exclude) {
        List<String> rels = new ArrayList<>();
        if (Files.isDirectory(dir)) {
            Set<Object> entered = new HashSet<>();
            entered.add(folderIdentity(dir));
            collectFiles(dir, "", rels, entered);
        }
        rels.sort(Json::byteCompare);
        rels.removeIf(rel -> exclude.contains(rel)
                || java.util.Arrays.stream(rel.split("/")).anyMatch(Catalog::skippedPart));
        return rels;
    }

    /** Content-address a directory: {@code sha256:} over the sorted {@code <relpath>\0<filehash>}
     * lines. Ports the Python reference's {@code signing.digest_tree} exactly: sorted by the full
     * POSIX relpath string (byte order, so {@code a-b} sorts before {@code a/x}), {@code exclude}
     * matched against the full relpath (a nested {@code controls/catalog.yaml} is never excluded, only
     * a top-level one), and any path segment that is a dotfile or named {@code __pycache__} is skipped
     * regardless of {@code exclude} ({@link #coveredFiles}). Recomputed from the directory's real bytes
     * every call -- never read from a catalog's stored {@code provenance.digest} field. */
    public static String digestTree(Path dir, Set<String> exclude) {
        List<byte[]> lines = new ArrayList<>();
        for (String rel : coveredFiles(dir, exclude)) {
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
