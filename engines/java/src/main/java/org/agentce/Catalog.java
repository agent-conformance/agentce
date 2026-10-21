package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AccessDeniedException;
import java.nio.file.FileSystemException;
import java.nio.file.Files;
import java.nio.file.FileSystemLoopException;
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Consumer;
import java.util.function.Predicate;
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
    /** The bytes the catalog was loaded from (18.171): its signature check and provenance digest read
     * these, never the folder again. */
    public final CatalogFiles files;

    /** One read of a catalog folder (18.171): every file the catalog digest covers, by POSIX relpath,
     * read once. The signature check and the loader both read these bytes, never the folder again, so a
     * file changed on disk during a run cannot be loaded unverified or verified unloaded. */
    /** {@code linksOut}: the covered files that are links to a file outside the catalog folder (18.181): a
     * signed load refuses them; other reads follow them. */
    public record CatalogFiles(Path root, Map<String, byte[]> files, Set<String> linksOut) {
        public String digest(Set<String> exclude) {
            return digestFiles(files, exclude);
        }
    }

    /** Test seam for VG-CATALOG-READ-ONCE ({@code Seams race-read}): called with each file's path right
     * after {@link #readFiles} reads it. Null outside that seam. */
    static volatile Consumer<Path> afterRead;

    private static final String EVIDENCE_SHAPE_UNRESOLVED = "catalog.evidence_shape.unresolved";
    private static final String SHAPE_UNRESOLVED = "catalog.shape.unresolved";
    private static final String MIN_SOURCE_CLASS_UNKNOWN = "catalog.control.min_source_class_unknown";
    private static final String SOURCE_CLASS_FROM_UNKNOWN = "catalog.control.source_class_from_unknown";
    private static final String REQUIRES_DENOMINATOR_INVALID = "catalog.control.requires_denominator_invalid";

    /** The coverage-denominator kinds a profile may declare (SPEC §6.5). */
    static final Set<String> DENOMINATOR_KINDS = Set.of(
            "provider_usage_export",
            "egress_proxy_log",
            "firewall_log",
            "system_of_record_export",
            "admission_attestation");

    /** A control that judges only where the subject declares an independent coverage denominator of one of
     * {@code kinds} whose count of {@code covers} records reconciles with what was captured (SPEC §6.5, §7.3). */
    public record RequiredDenominator(Set<String> kinds, String covers) {}

    private Catalog(
            String id,
            String version,
            Path directory,
            List<ControlSpec> controls,
            Map<String, Psp.Shape> shapes,
            Map<String, String> evidenceShapeIris,
            CatalogFiles files) {
        this.id = id;
        this.version = version;
        this.directory = directory;
        this.controls = controls;
        this.shapes = shapes;
        this.evidenceShapeIris = evidenceShapeIris;
        this.files = files;
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
        /** {@code evaluation.source_class_from}: the graph links to the records the judged class also rests on. */
        public List<String> sourceClassFrom = new ArrayList<>();
        /** {@code evaluation.requires_denominator}: the declared denominator a verdict needs (18.37e). */
        public RequiredDenominator requiresDenominator;
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

    /** The control's {@code evaluation.min_source_class}, exactly one of the SPEC §6.4 classes. Anything else
     * (missing, null, empty, not a string, {@code any}, another case or another word) is refused when the catalog
     * loads, never read as no minimum. */
    private static String minSourceClass(String controlId, JsonNode evaluation) {
        JsonNode value = evaluation.get("min_source_class");
        if (value != null && value.isTextual() && SourceClass.RANK.containsKey(value.textValue())) {
            return value.textValue();
        }
        String shown = value == null ? "(missing)" : pyDumps(value);
        throw new InputError(
                MIN_SOURCE_CLASS_UNKNOWN,
                ErrorCatalogue.errorCause(MIN_SOURCE_CLASS_UNKNOWN)
                        .replace("{control}", controlId)
                        .replace("{value}", shown),
                ErrorCatalogue.errorFix(MIN_SOURCE_CLASS_UNKNOWN));
    }

    /** Python's {@code json.dumps(value, ensure_ascii=False)}: ", " and ": " separators, members in their
     * order, non-ASCII text as is. */
    static String pyDumps(JsonNode value) {
        StringBuilder out = new StringBuilder();
        pyDumps(value, out);
        return out.toString();
    }

    private static void pyDumps(JsonNode value, StringBuilder out) {
        if (value == null || value.isNull()) {
            out.append("null");
        } else if (value.isBoolean()) {
            out.append(value.booleanValue() ? "true" : "false");
        } else if (value.isIntegralNumber()) {
            out.append(value.bigIntegerValue());
        } else if (value.isNumber()) {
            out.append(Double.toString(value.doubleValue()).replace("E", "e"));
        } else if (value.isTextual()) {
            pyString(value.textValue(), out);
        } else if (value.isArray()) {
            out.append('[');
            for (int i = 0; i < value.size(); i++) {
                out.append(i > 0 ? ", " : "");
                pyDumps(value.get(i), out);
            }
            out.append(']');
        } else {
            out.append('{');
            boolean first = true;
            for (Map.Entry<String, JsonNode> e : (Iterable<Map.Entry<String, JsonNode>>) value::fields) {
                out.append(first ? "" : ", ");
                first = false;
                pyString(e.getKey(), out);
                out.append(": ");
                pyDumps(e.getValue(), out);
            }
            out.append('}');
        }
    }

    private static void pyString(String text, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            switch (ch) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\t' -> out.append("\\t");
                case '\n' -> out.append("\\n");
                case '\f' -> out.append("\\f");
                case '\r' -> out.append("\\r");
                default -> {
                    if (ch < 0x20) {
                        out.append(String.format("\\u%04x", (int) ch));
                    } else {
                        out.append(ch);
                    }
                }
            }
        }
        out.append('"');
    }

    /** The named {@code source_class_from} links, distinct and byte-sorted. A value that is not a list of known
     * link names is refused when the catalog loads, never dropped: a dropped link would judge the control on the
     * focus record alone. */
    private static List<String> sourceClassFrom(String controlId, JsonNode evaluation) {
        JsonNode value = evaluation.get("source_class_from");
        if (value == null) {
            return new ArrayList<>();
        }
        Set<String> names = new java.util.TreeSet<>(Json::byteCompare);
        boolean known = value.isArray();
        for (JsonNode name : value) {
            known &= name.isTextual() && SourceClass.FROM.containsKey(name.textValue());
            names.add(name.asText());
        }
        if (known) {
            return new ArrayList<>(names);
        }
        throw new InputError(
                SOURCE_CLASS_FROM_UNKNOWN,
                ErrorCatalogue.errorCause(SOURCE_CLASS_FROM_UNKNOWN)
                        .replace("{control}", controlId)
                        .replace("{value}", pyDumps(value)),
                ErrorCatalogue.errorFix(SOURCE_CLASS_FROM_UNKNOWN));
    }

    /** The declared {@code requires_denominator}. Anything but a non-empty list of known denominator kinds,
     * without repeats, and an event type name is refused when the catalog loads, never dropped: a dropped
     * requirement would judge without it. */
    private static RequiredDenominator requiresDenominator(String controlId, JsonNode evaluation) {
        if (!evaluation.has("requires_denominator")) {
            return null;
        }
        JsonNode value = evaluation.get("requires_denominator");
        if (value.isObject() && value.size() == 2 && value.has("kinds") && value.has("covers")) {
            JsonNode kinds = value.get("kinds");
            JsonNode covers = value.get("covers");
            Set<String> named = new HashSet<>();
            boolean known = kinds.isArray() && !kinds.isEmpty();
            for (JsonNode kind : kinds.isArray() ? kinds : Json.nodes().arrayNode()) {
                known &= kind.isTextual() && DENOMINATOR_KINDS.contains(kind.textValue()) && named.add(kind.textValue());
            }
            if (known && covers.isTextual() && !covers.textValue().isEmpty()) {
                return new RequiredDenominator(Set.copyOf(named), covers.textValue());
            }
        }
        throw new InputError(
                REQUIRES_DENOMINATOR_INVALID,
                ErrorCatalogue.errorCause(REQUIRES_DENOMINATOR_INVALID)
                        .replace("{control}", controlId)
                        .replace("{value}", pyDumps(sortedKeys(value))),
                ErrorCatalogue.errorFix(REQUIRES_DENOMINATOR_INVALID));
    }

    /** {@code value} with every object's keys byte-sorted, as Python's {@code json.dumps(sort_keys=True)} writes it. */
    private static JsonNode sortedKeys(JsonNode value) {
        if (value.isArray()) {
            var out = Json.nodes().arrayNode();
            value.forEach(v -> out.add(sortedKeys(v)));
            return out;
        }
        if (value.isObject()) {
            var out = Json.nodes().objectNode();
            List<String> keys = new ArrayList<>();
            value.fieldNames().forEachRemaining(keys::add);
            keys.sort(Json::byteCompare);
            keys.forEach(k -> out.set(k, sortedKeys(value.get(k))));
            return out;
        }
        return value;
    }

    private static ControlSpec controlFromDict(JsonNode data) {
        JsonNode evaluation = data.get("evaluation");
        if (evaluation == null || !evaluation.isObject()) {
            evaluation = Json.nodes().objectNode();
        }
        String minSourceClass = minSourceClass(text(data, "id", ""), evaluation);
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
        control.minSourceClass = minSourceClass;
        control.sourceClassFrom = sourceClassFrom(text(data, "id", ""), evaluation);
        control.requiresDenominator = requiresDenominator(text(data, "id", ""), evaluation);
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

    /** Read every file the catalog digest covers under {@code root} ({@link #coveredFiles}, less the
     * signature) once. {@code first} holds files the caller has already read (the loader reads {@code
     * catalog.yaml} first, so a missing one is refused as before); those bytes are kept, never read a
     * second time. A file that cannot be read throws IllegalStateException, as {@link #digestTree}. */
    public static CatalogFiles readFiles(Path root, Map<String, byte[]> first) {
        Map<String, byte[]> files = new LinkedHashMap<>();
        List<String> rels = coveredFiles(root, Set.of(Verify.CATALOG_SIGNATURE_NAME));
        refuseFolderControls(root);
        Set<String> linksOut = new TreeSet<>(Json::byteCompare);
        for (String rel : rels) {
            if (linksOutside(root, rel)) {
                linksOut.add(rel);
            }
            byte[] content = first.get(rel);
            if (content == null) {
                Path path = root.resolve(rel);
                content = readBytes(path);
                Consumer<Path> hook = afterRead;
                if (hook != null) {
                    hook.accept(path);
                }
            }
            files.put(rel, content);
        }
        return new CatalogFiles(
                root, Collections.unmodifiableMap(files), Collections.unmodifiableSet(linksOut));
    }

    /** A folder named like a control file ({@code controls/<name>.yaml}) is where the loader reads a
     * control: the walk would enter it and the control would vanish, so it is refused naming it (18.181). */
    private static void refuseFolderControls(Path root) {
        Path controls = root.resolve("controls");
        if (!Files.isDirectory(controls)) {
            return;
        }
        List<String> names = new ArrayList<>();
        try (Stream<Path> stream = Files.list(controls)) {
            stream.map(p -> p.getFileName().toString()).forEach(names::add);
        } catch (IOException e) {
            throw new IllegalStateException("cannot list " + controls + ": " + e.getMessage(), e);
        }
        names.sort(Json::byteCompare);
        for (String name : names) {
            Path path = controls.resolve(name);
            if (name.endsWith(".yaml") && !skippedPart(name) && Files.isDirectory(path)) {
                throw notAFile(path, "a folder, not a file");
            }
        }
    }

    /** Whether {@code rel} is itself a link whose target resolves outside {@code root}. */
    private static boolean linksOutside(Path root, String rel) {
        Path path = root.resolve(rel);
        return Files.isSymbolicLink(path) && !insideFolder(root, path);
    }

    /** Whether {@code path}, after links, lies under {@code root}. Judged by folder identity, never by path
     * spelling, which a case-insensitive filesystem or a firmlink allows many of (18.181); mirrors Python's
     * {@code signing.inside_folder}. */
    static boolean insideFolder(Path root, Path path) {
        Object rootId = identity(root);
        for (Path up = realPath(path).getParent(); up != null; up = up.getParent()) {
            if (identity(up).equals(rootId)) {
                return true;
            }
        }
        return false;
    }

    private static Object identity(Path dir) {
        try {
            return folderIdentity(dir, Files.readAttributes(dir, BasicFileAttributes.class));
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + dir + ": " + e.getMessage(), e);
        }
    }

    private static Path realPath(Path path) {
        try {
            return path.toRealPath();
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + path + ": " + e.getMessage(), e);
        }
    }

    /** The refusal for an entry that is there but cannot be read as a file; it names {@code path}. */
    private static IllegalStateException notAFile(Path path, String why) {
        return new IllegalStateException(
                why + ": " + path, new FileSystemException(path.toString(), null, why));
    }

    private static byte[] readBytes(Path path) {
        try {
            return Files.readAllBytes(path);
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + path + ": " + e.getMessage(), e);
        }
    }

    /** A catalog file's bytes as text; bytes that are not UTF-8 are refused naming the file (18.184i). */
    private static String text(CatalogFiles files, String rel) {
        try {
            return Verify.decodeStrict(files.files().get(rel)).toString();
        } catch (CharacterCodingException e) {
            throw fileUnloadable(files.root(), rel, "is not valid UTF-8");
        }
    }

    /** {@code catalog.yaml} or a control file as one YAML mapping (18.184i): checked as Python's unique-key
     * {@code SafeLoader} reads it ({@link Readiness#composeCatalogPyyaml}), so a key written twice, YAML the engine
     * refuses to load, or anything but one mapping (an empty file included) is refused naming the file, never
     * read with its last value, as nothing, or skipped. The values are read from that same tree, so a merge key
     * ({@code <<}) applies as in PyYAML (18.184o). */
    private static JsonNode readMapping(CatalogFiles files, String rel) {
        String text = text(files, rel);
        JsonNode data;
        try {
            data = Readiness.toJsonNode(Readiness.composeCatalogPyyaml(text));
        } catch (StackOverflowError | RuntimeException e) {
            throw fileUnloadable(
                    files.root(),
                    rel,
                    Readiness.nestedTooDeeply(e)
                            ? "is nested too deeply to read safely"
                            : "is not YAML the engine can load: " + e.getMessage());
        }
        if (data == null || !data.isObject()) {
            throw fileUnloadable(files.root(), rel, "does not hold one YAML mapping");
        }
        return data;
    }

    /** input.catalog_unreadable for a catalog file that was read but cannot be loaded (Python's
     * {@code catalog_file_unloadable}, 18.184i). {@code problem} completes "<rel> ...". */
    private static InputError fileUnloadable(Path dir, String rel, String problem) {
        return new InputError(
                "input.catalog_unreadable",
                "the catalog directory " + dir + " holds a file that cannot be loaded: " + rel + " " + problem + ".",
                "fix " + rel + " so it is UTF-8 text holding one YAML mapping with each key written once, or ask the "
                        + "catalog's publisher for a corrected copy, then re-run.");
    }

    /** The covered {@code controls/<name>.yaml} files, one level deep as the listing always was, sorted. */
    private static List<String> controlFiles(Iterable<String> files) {
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
     * cannot change what it loads without changing its digest. The load walks the folder as the digest
     * does, so a folder it cannot read is refused as input.catalog_unreadable, as verify and sign refuse it. */
    public static Catalog load(Path directory) {
        // catalog.yaml first, as before: a folder without one is refused naming it. Every covered file is
        // then read once (18.171) and kept as files, which the signature check and provenance digest read.
        // One that is there but is not a regular file (a FIFO would block the read) is refused unopened (18.181).
        Path metaPath = directory.resolve("catalog.yaml");
        if (!Files.isRegularFile(metaPath) && Files.exists(metaPath, LinkOption.NOFOLLOW_LINKS)) {
            throw Verify.catalogUnreadable(directory, notAFile(metaPath, "not a regular file"));
        }
        byte[] metaBytes;
        try {
            metaBytes = Files.readAllBytes(metaPath);
        } catch (IOException e) {
            throw Verify.catalogUnreadable(directory, e);
        }
        CatalogFiles files;
        try {
            files = readFiles(directory, Map.of("catalog.yaml", metaBytes));
        } catch (IllegalStateException e) {
            throw Verify.catalogUnreadable(directory, e);
        }
        JsonNode metaRecord = readMapping(files, "catalog.yaml");
        List<ControlSpec> controls = new ArrayList<>();
        Map<String, Psp.Shape> shapes = new LinkedHashMap<>();
        Map<String, String> evidenceShapeIris = new LinkedHashMap<>();
        for (String rel : controlFiles(files.files().keySet())) {
            ControlSpec control = controlFromDict(readMapping(files, rel));
            controls.add(control);
            if (control.shapePath != null) {
                if (!files.files().containsKey(control.shapePath)) {
                    throw refuseUncovered(SHAPE_UNRESOLVED, control, control.shapePath);
                }
                shapes.putAll(Psp.parseShapesTtl(text(files, control.shapePath), control.shapePath));
            }
            if (control.evidenceShapePath != null) {
                Map.Entry<String, Map<String, Psp.Shape>> loaded = loadEvidenceShape(files, control);
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
                evidenceShapeIris,
                files);
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
            CatalogFiles files, ControlSpec control) {
        String path = control.evidenceShapePath;
        Path directory = files.root();
        Map<String, Psp.Shape> loaded = Map.of();
        if (files.files().containsKey(path)) {
            try {
                Path named = directory.resolve(path);
                if (insideFolder(directory, named) && Files.isRegularFile(named)) {
                    loaded = Psp.parseShapesTtl(text(files, path), path);
                }
            } catch (IllegalStateException | java.nio.file.InvalidPathException e) {
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
        return forTest(id, version, null, controls);
    }

    /** As {@link #forTest(String, String, List)}, read from {@code directory} for its digest only. */
    static Catalog forTest(String id, String version, Path directory, List<ControlSpec> controls) {
        CatalogFiles files = directory == null ? null : readFiles(directory, Map.of());
        return new Catalog(id, version, directory, controls, Map.of(), Map.of(), files);
    }

    /** Files left out of a catalog's provenance digest: the detached signature and {@code catalog.yaml}
     * itself (which carries the provenance block), so the digest covers the catalog's rules and is
     * non-circular (mirrors the Python reference's {@code catalog._PROVENANCE_EXCLUDE} and the
     * TypeScript port's {@code PROVENANCE_EXCLUDE}). */
    private static final Set<String> PROVENANCE_EXCLUDE = Set.of("catalog.sig.json", "catalog.yaml");

    /** A folder's identity: its file key (device and inode on Unix), never a path string, which a
     * case-insensitive filesystem spells many ways; its real path where the filesystem gives no key. */
    private static Object folderIdentity(Path dir, BasicFileAttributes attrs) {
        Object key = attrs.fileKey();
        if (key != null) {
            return key;
        }
        try {
            return dir.toRealPath();
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + dir + ": " + e.getMessage(), e);
        }
    }

    /** {@code path}'s attributes, following links, or null when there is nothing to walk: a missing entry,
     * or a link that dangles or loops, which Python's {@code signing.is_directory} reads as absent. Any other
     * error (an entry in a folder that can be listed but not entered) is thrown naming the path, as Python
     * and TypeScript raise it, so the walk refuses what it cannot see instead of walking past it (18.170). */
    private static BasicFileAttributes statFollowing(Path path) {
        try {
            return Files.readAttributes(path, BasicFileAttributes.class);
        } catch (NoSuchFileException e) {
            return null;
        } catch (AccessDeniedException e) {
            throw new IllegalStateException("cannot read " + path + ": " + e.getMessage(), e);
        } catch (IOException e) {
            if (Files.isSymbolicLink(path)) {
                return null;
            }
            throw new IllegalStateException("cannot read " + path + ": " + e.getMessage(), e);
        }
    }

    /** Every file's POSIX relpath under {@code dir}. As Python's {@code signing._walk_files}: a
     * symlinked folder is descended like a real one and a symlinked file is followed (18.164), a broken
     * or looping link is skipped, an entry it cannot stat is refused ({@link #statFollowing}), and a
     * folder entered a second time (a cycle, or two names for one folder) is refused naming the second
     * name, so each folder is entered once. */
    private static void collectFiles(
            Path dir, String base, List<String> out, List<String> odd, Set<Object> entered) {
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
            BasicFileAttributes attrs = statFollowing(full);
            if (attrs == null) {
                odd.add(rel);
            } else if (attrs.isDirectory()) {
                if (!entered.add(folderIdentity(full, attrs))) {
                    throw new IllegalStateException(
                            "folder reached twice: " + full,
                            new FileSystemLoopException(full.toString()));
                }
                collectFiles(full, rel, out, odd, entered);
            } else if (attrs.isRegularFile()) {
                out.add(rel);
            } else {
                odd.add(rel);
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
        List<String> odd = new ArrayList<>();
        BasicFileAttributes attrs = statFollowing(dir);
        if (attrs != null && attrs.isDirectory()) {
            Set<Object> entered = new HashSet<>();
            entered.add(folderIdentity(dir, attrs));
            collectFiles(dir, "", rels, odd, entered);
        }
        Predicate<String> uncovered = rel -> exclude.contains(rel)
                || Arrays.stream(rel.split("/")).anyMatch(Catalog::skippedPart);
        odd.sort(Json::byteCompare);
        for (String rel : odd) {
            if (!uncovered.test(rel)) {
                // Refused as unreadable, never dropped from the digest or opened (18.181).
                throw notAFile(dir.resolve(rel), "not a regular file or folder");
            }
        }
        rels.sort(Json::byteCompare);
        rels.removeIf(uncovered);
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
        Map<String, byte[]> files = new LinkedHashMap<>();
        for (String rel : coveredFiles(dir, exclude)) {
            files.put(rel, readBytes(dir.resolve(rel)));
        }
        return digestFiles(files, Set.of());
    }

    /** {@link #digestTree} over bytes already read: the same digest, for the same relpaths and contents. */
    public static String digestFiles(Map<String, byte[]> files, Set<String> exclude) {
        List<String> rels = new ArrayList<>();
        for (String rel : files.keySet()) {
            if (!exclude.contains(rel)) {
                rels.add(rel);
            }
        }
        rels.sort(Json::byteCompare);
        List<byte[]> lines = new ArrayList<>();
        for (String rel : rels) {
            String fileHash = Canonical.sha256Hex(files.get(rel));
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

    /** {@link #provenanceDigest} over the files a load read (18.171). */
    public static String provenanceDigest(CatalogFiles files) {
        return files.digest(PROVENANCE_EXCLUDE);
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
