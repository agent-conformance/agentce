package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.FormatKeyword;
import com.networknt.schema.JsonMetaSchema;
import com.networknt.schema.JsonNodePath;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import javax.xml.stream.XMLInputFactory;
import javax.xml.stream.XMLStreamException;
import javax.xml.stream.XMLStreamReader;

/**
 * {@code report --validate}: schema-validate every artifact in a report directory (SPEC §9), at
 * parity with the Python reference ({@code agentce.report.validate_report}) and the TypeScript port
 * ({@code engines/typescript/src/reportValidate.ts}, item 18.27). Every emitted artifact is checked
 * against its vendored JSON Schema ({@code resources/schemas/*.schema.json}, mirrored from {@code
 * spec/report/}); {@code oscal-ar.json} and {@code results.sarif} are checked twice -- first against
 * AgentCE's own bounded local profile, then, only on success, against the real third-party standard
 * vendored under the same directory (NIST OSCAL 1.1.2, OASIS SARIF 2.1.0) -- so a document that
 * satisfies AgentCE's narrower profile but not the real standard is still caught
 * ({@code harness/remediation/evidence/P18-18.27/python-reference.md}, cases 6 and 8).
 *
 * <p>Unlike the TypeScript port, no keyword override is needed for the vendored NIST schema's
 * Unicode-property patterns: {@code java.util.regex.Pattern} (which networknt's {@code pattern}
 * keyword uses directly) supports {@code \p{L}}/{@code \p{N}} natively, and networknt's draft-04
 * support reads the vendored SARIF schema's legacy {@code id} self-reference without the {@code
 * $id} rename the Ajv-based TypeScript port needs (see the python reference's "Design notes").
 */
public final class ReportValidate {
    private ReportValidate() {}

    /** Always written, regardless of what a run actually emits: the run's structural core. */
    private static final Map<String, String> MANDATORY_ARTIFACT_SCHEMAS = new LinkedHashMap<>();

    static {
        MANDATORY_ARTIFACT_SCHEMAS.put("assertions.json", "assertions");
        MANDATORY_ARTIFACT_SCHEMAS.put("manifest.json", "manifest");
        MANDATORY_ARTIFACT_SCHEMAS.put("activity.json", "activity");
        MANDATORY_ARTIFACT_SCHEMAS.put("blind-spots.json", "blind-spots");
    }

    /** Written only when a run produced them; validated when present, skipped when a narrower run
     * legitimately left them unwritten. */
    private static final Map<String, String> OPTIONAL_ARTIFACT_SCHEMAS = new LinkedHashMap<>();

    static {
        OPTIONAL_ARTIFACT_SCHEMAS.put("oscal-ar.json", "oscal-assessment-results");
        OPTIONAL_ARTIFACT_SCHEMAS.put("results.sarif", "results-sarif");
        OPTIONAL_ARTIFACT_SCHEMAS.put("project.json", "project");
        OPTIONAL_ARTIFACT_SCHEMAS.put("security.json", "security");
        OPTIONAL_ARTIFACT_SCHEMAS.put("auditor.json", "auditor");
        OPTIONAL_ARTIFACT_SCHEMAS.put("buyer.json", "buyer");
    }

    private static final Map<String, String> ARTIFACT_SCHEMAS = new LinkedHashMap<>();

    static {
        ARTIFACT_SCHEMAS.putAll(MANDATORY_ARTIFACT_SCHEMAS);
        ARTIFACT_SCHEMAS.putAll(OPTIONAL_ARTIFACT_SCHEMAS);
    }

    /** The newer optional artifacts (SPEC §9): validated only when a run actually produced them
     * ({@code --emit}, or for {@code runtime_drift.jsonl}, {@code --state}), unlike {@link
     * #ARTIFACT_SCHEMAS}'s entries. */
    private static final List<String> OPTIONAL_ARTIFACTS =
            List.of("report.junit.xml", "oscal-ar.xml", "report.csv", "runtime_drift.jsonl");

    private static final List<String> CSV_COLUMNS =
            List.of("control", "subject", "outcome", "control_version", "rung", "mode");

    private static final Map<String, JsonNode> SCHEMA_CACHE = new ConcurrentHashMap<>();

    private static JsonNode loadSchemaJson(String name) {
        return SCHEMA_CACHE.computeIfAbsent(name, n -> {
            String resource = "/schemas/" + n + ".schema.json";
            try (InputStream in = ReportValidate.class.getResourceAsStream(resource)) {
                if (in == null) {
                    throw new IllegalStateException("vendored report schema not on classpath: " + resource);
                }
                return Json.parse(new String(in.readAllBytes(), StandardCharsets.UTF_8));
            } catch (IOException e) {
                throw new IllegalStateException("cannot read vendored report schema " + resource, e);
            }
        });
    }

    /** A factory whose meta-schema knows no format, so {@code format} is an annotation, never an
     * assertion -- as in the other two engines: Python's validators run without a FormatChecker and
     * the TypeScript port sets {@code validateFormats: false}. networknt asserts known formats for
     * draft-04/07 whatever {@code formatAssertionsEnabled} says, and refuses a plain override of the
     * {@code format} keyword (P18-18.27 verifier round 2, D4). */
    private static JsonSchemaFactory formatsAsAnnotations(SpecVersion.VersionFlag version, JsonMetaSchema metaSchema) {
        JsonMetaSchema withoutFormats = JsonMetaSchema.builder(metaSchema)
                .formatKeywordFactory(formats -> new FormatKeyword(Map.of()))
                .build();
        return JsonSchemaFactory.builder(JsonSchemaFactory.getInstance(version)).metaSchema(withoutFormats).build();
    }

    private static final JsonSchemaFactory LOCAL_FACTORY =
            formatsAsAnnotations(SpecVersion.VersionFlag.V202012, JsonMetaSchema.getV202012());
    private static final Map<String, JsonSchema> LOCAL_VALIDATORS = new ConcurrentHashMap<>();

    private static JsonSchema localValidatorFor(String schemaName) {
        return LOCAL_VALIDATORS.computeIfAbsent(schemaName, n -> LOCAL_FACTORY.getSchema(loadSchemaJson(n)));
    }

    /** A problem's location as typed steps, as networknt records them: an object key is a {@code
     * String}, an array index an {@code Integer}. Never re-split from a joined string, so a key
     * holding {@code /} or digits stays one key (verifier round 2, D5/D6). */
    private static List<Object> segments(JsonNodePath path) {
        List<Object> out = new ArrayList<>();
        for (int i = 0; i < path.getNameCount(); i++) {
            out.add(path.getElement(i));
        }
        return out;
    }

    /** Python's list ordering over {@code absolute_path}: array indices compare as numbers, object
     * keys as strings by code point (UTF-8 byte order is code-point order), and a path sorts before
     * any longer path it is a prefix of. */
    private static int compareSegments(List<Object> a, List<Object> b) {
        int len = Math.min(a.size(), b.size());
        for (int i = 0; i < len; i++) {
            Object segA = a.get(i);
            Object segB = b.get(i);
            int diff = segA instanceof Integer x && segB instanceof Integer y
                    ? Integer.compare(x, y)
                    : Json.byteCompare(String.valueOf(segA), String.valueOf(segB));
            if (diff != 0) {
                return diff;
            }
        }
        return Integer.compare(a.size(), b.size());
    }

    /** The index of the outermost {@code anyOf}/{@code oneOf} step in an evaluation path, or -1.
     * The evaluation path keeps every {@code $ref} hop ({@code /anyOf/0/$ref/pattern}), where the
     * schema location names only the {@code $ref} target -- so a branch reached through a {@code
     * $ref} is still found (verifier round 2, D1). */
    private static int outermostCombinator(JsonNodePath evaluationPath) {
        for (int i = 0; i < evaluationPath.getNameCount(); i++) {
            Object step = evaluationPath.getElement(i);
            if ("anyOf".equals(step) || "oneOf".equals(step)) {
                return i;
            }
        }
        return -1;
    }

    /** How many steps into the instance an evaluation path takes after {@code from}, ignoring its
     * last step (the keyword that failed): {@code properties/<name>}, {@code items} and {@code
     * additionalProperties} each step one level down. These are the only applicators the validated
     * schemas use besides {@code $ref}, {@code allOf}, {@code anyOf} and {@code oneOf}, which stay at
     * the same instance; {@code tools/report_validate_keyword_census.py} fails on any other. */
    private static int instanceSteps(JsonNodePath evaluationPath, int from) {
        int steps = 0;
        for (int i = from; i < evaluationPath.getNameCount() - 1; i++) {
            Object step = evaluationPath.getElement(i);
            if ("properties".equals(step)) {
                steps++;
                i++; // the property name
            } else if ("items".equals(step) || "additionalProperties".equals(step)) {
                steps++;
            }
        }
        return steps;
    }

    private static String display(List<Object> location) {
        if (location.isEmpty()) {
            return "<root>";
        }
        StringBuilder out = new StringBuilder();
        for (Object step : location) {
            if (out.length() > 0) {
                out.append('/');
            }
            out.append(step);
        }
        return out.toString();
    }

    private record LocatedProblem(List<Object> location, String keyword, String message) {}

    /** Every schema error as {@code "<prefix><location>: <message>"}, following the Python reference's
     * model (P18-18.27; {@code python-reference.md}'s Design notes): one problem per failing {@code
     * anyOf}/{@code oneOf} at the combinator's own instance location, grouped by evaluation path
     * (networknt's own {@code oneOf} message when it gives one; it gives none for {@code anyOf}); one
     * {@code additionalProperties} problem per object naming its unexpected keys in code-point order,
     * where networknt reports one per key; sorted by location, then keyword, keeping validator order
     * for ties. */
    private static List<String> schemaProblems(String prefix, Set<ValidationMessage> errors) {
        Map<String, LocatedProblem> combinators = new LinkedHashMap<>();
        Map<String, List<Object>> extraKeyLocations = new LinkedHashMap<>();
        Map<String, List<String>> extraKeys = new LinkedHashMap<>();
        List<LocatedProblem> problems = new ArrayList<>();
        for (ValidationMessage m : errors) {
            List<Object> location = segments(m.getInstanceLocation());
            JsonNodePath evaluationPath = m.getEvaluationPath();
            int combinator = outermostCombinator(evaluationPath);
            if (combinator >= 0) {
                List<Object> at = location.subList(0, location.size() - instanceSteps(evaluationPath, combinator + 1));
                String keyword = (String) evaluationPath.getElement(combinator);
                String group = at + "::" + segments(evaluationPath).subList(0, combinator + 1);
                boolean own = combinator == evaluationPath.getNameCount() - 1;
                if (own || !combinators.containsKey(group)) {
                    String message = own ? m.getMessage() : "does not match any of the required alternatives";
                    combinators.put(group, new LocatedProblem(new ArrayList<>(at), keyword, message));
                }
                continue;
            }
            if ("additionalProperties".equals(m.getType())) {
                String group = location + "::" + evaluationPath;
                extraKeyLocations.putIfAbsent(group, location);
                extraKeys.computeIfAbsent(group, g -> new ArrayList<>()).add(m.getProperty());
                continue;
            }
            problems.add(new LocatedProblem(location, m.getType(), m.getMessage()));
        }
        problems.addAll(combinators.values());
        for (Map.Entry<String, List<String>> entry : extraKeys.entrySet()) {
            List<String> keys = entry.getValue();
            keys.sort(Json::byteCompare);
            problems.add(new LocatedProblem(extraKeyLocations.get(entry.getKey()), "additionalProperties",
                    "properties '" + String.join("', '", keys) + "' are not allowed (additional properties are not allowed)"));
        }
        problems.sort((a, b) -> {
            int diff = compareSegments(a.location(), b.location());
            return diff != 0 ? diff : Json.byteCompare(a.keyword(), b.keyword());
        });
        List<String> out = new ArrayList<>();
        for (LocatedProblem problem : problems) {
            out.add(prefix + display(problem.location()) + ": " + problem.message());
        }
        return out;
    }

    private static Map<String, String> recordedOutputs(Path outDir) {
        Path manifestPath = outDir.resolve("manifest.json");
        if (!Files.isRegularFile(manifestPath)) {
            return Map.of();
        }
        JsonNode manifest;
        try {
            manifest = Json.parse(Files.readString(manifestPath, StandardCharsets.UTF_8));
        } catch (IOException | IllegalArgumentException e) {
            return Map.of();
        }
        JsonNode outputs = manifest.isObject() ? manifest.get("outputs") : null;
        if (outputs == null || !outputs.isObject()) {
            return Map.of();
        }
        Map<String, String> out = new LinkedHashMap<>();
        var fields = outputs.fields();
        while (fields.hasNext()) {
            var entry = fields.next();
            out.put(entry.getKey(), entry.getValue().asText());
        }
        return out;
    }

    private static List<String> validateXmlWellformed(Path path, String filename) {
        XMLInputFactory factory = XMLInputFactory.newInstance();
        factory.setProperty(XMLInputFactory.SUPPORT_DTD, false);
        try (var in = Files.newInputStream(path)) {
            XMLStreamReader reader = factory.createXMLStreamReader(in);
            while (reader.hasNext()) {
                reader.next();
            }
            return List.of();
        } catch (XMLStreamException | IOException e) {
            return List.of(filename + ": invalid XML (" + e.getMessage() + ")");
        }
    }

    private static List<String> validateJsonl(Path path, String filename) {
        List<String> problems = new ArrayList<>();
        String text;
        try {
            text = Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            return List.of(filename + ": cannot read (" + e.getMessage() + ")");
        }
        String[] lines = text.split("\r?\n", -1);
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            if (line.strip().isEmpty()) {
                continue;
            }
            try {
                Json.parse(line);
            } catch (IllegalArgumentException e) {
                problems.add(filename + ": line " + (i + 1) + " is not valid JSON (" + originalMessage(e) + ")");
            }
        }
        return problems;
    }

    private static String originalMessage(IllegalArgumentException e) {
        String message = e.getMessage();
        return message != null && message.startsWith("invalid JSON: ") ? message.substring("invalid JSON: ".length()) : message;
    }

    /** A minimal RFC 4180 field splitter: exactly what {@code report.csv}'s own writer produces (no
     * embedded newlines are ever written into a field), enough to accept a genuine run's output and
     * reject garbage, matching Python's {@code csv.reader} behaviour for this bounded shape. */
    private static List<String> splitCsvLine(String line) {
        List<String> fields = new ArrayList<>();
        int i = 0;
        int n = line.length();
        while (i <= n) {
            StringBuilder field = new StringBuilder();
            if (i < n && line.charAt(i) == '"') {
                i++;
                while (i < n) {
                    if (line.charAt(i) == '"' && i + 1 < n && line.charAt(i + 1) == '"') {
                        field.append('"');
                        i += 2;
                    } else if (line.charAt(i) == '"') {
                        i++;
                        break;
                    } else {
                        field.append(line.charAt(i));
                        i++;
                    }
                }
            } else {
                while (i < n && line.charAt(i) != ',') {
                    field.append(line.charAt(i));
                    i++;
                }
            }
            fields.add(field.toString());
            if (i < n && line.charAt(i) == ',') {
                i++;
            } else {
                break;
            }
        }
        return fields;
    }

    private static List<String> validateCsv(Path path, String filename) {
        String text;
        try {
            text = Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            return List.of(filename + ": invalid CSV (" + e.getMessage() + ")");
        }
        String[] rawLines = text.split("\r?\n", -1);
        List<String> lines = new ArrayList<>();
        for (int i = 0; i < rawLines.length; i++) {
            if (i == rawLines.length - 1 && rawLines[i].isEmpty()) {
                continue; // trailing newline, not an empty final row
            }
            lines.add(rawLines[i]);
        }
        if (lines.isEmpty()) {
            return List.of(filename + ": empty CSV (no header row)");
        }
        List<List<String>> rows = new ArrayList<>();
        for (String line : lines) {
            rows.add(splitCsvLine(line));
        }
        List<String> header = rows.get(0);
        List<String> problems = new ArrayList<>();
        for (String column : CSV_COLUMNS) {
            if (!header.contains(column)) {
                problems.add(filename + ": missing column '" + column + "'");
            }
        }
        int width = header.size();
        for (int i = 1; i < rows.size(); i++) {
            List<String> row = rows.get(i);
            if (row.size() != width) {
                problems.add(filename + ": row " + (i + 1) + " has " + row.size() + " field(s), expected " + width);
            }
        }
        return problems;
    }

    /** Schema-validation problems for every artifact in {@code outDir} (empty when it is entirely
     * valid). */
    public static List<String> validateReport(Path outDir) {
        List<String> problems = new ArrayList<>();
        for (String filename : MANDATORY_ARTIFACT_SCHEMAS.keySet()) {
            if (!Files.isRegularFile(outDir.resolve(filename))) {
                problems.add(filename + ": missing");
            }
        }
        Map<String, String> recorded = recordedOutputs(outDir);
        Set<String> trackedNames = new LinkedHashSet<>(ARTIFACT_SCHEMAS.keySet());
        trackedNames.add("report.md");
        trackedNames.add("report.html");
        trackedNames.addAll(OPTIONAL_ARTIFACTS);
        for (String filename : trackedNames) {
            if (recorded.containsKey(filename) && !Files.isRegularFile(outDir.resolve(filename))) {
                problems.add(filename + ": missing (recorded in manifest.json's outputs but not on disk)");
            }
        }
        for (Map.Entry<String, String> entry : ARTIFACT_SCHEMAS.entrySet()) {
            String filename = entry.getKey();
            String schemaName = entry.getValue();
            Path path = outDir.resolve(filename);
            if (!Files.isRegularFile(path)) {
                continue; // mandatory absence was already reported above; optional absence is not a problem
            }
            JsonNode instance;
            try {
                instance = Json.parse(Files.readString(path, StandardCharsets.UTF_8));
            } catch (IOException e) {
                problems.add(filename + ": cannot read (" + e.getMessage() + ")");
                continue;
            } catch (IllegalArgumentException e) {
                problems.add(filename + ": invalid JSON (" + originalMessage(e) + ")");
                continue;
            }
            Set<ValidationMessage> errors = localValidatorFor(schemaName).validate(instance);
            if (!errors.isEmpty()) {
                problems.addAll(schemaProblems(filename + ": ", errors));
                continue;
            }
            if (filename.equals("oscal-ar.json")) {
                problems.addAll(validateOscalArNist(instance));
            }
            if (filename.equals("results.sarif")) {
                problems.addAll(validateSarif210(instance));
            }
        }
        for (String filename : List.of("report.md", "report.html")) {
            Path path = outDir.resolve(filename);
            try {
                if (Files.isRegularFile(path) && Files.readString(path, StandardCharsets.UTF_8).strip().isEmpty()) {
                    problems.add(filename + ": empty");
                }
            } catch (IOException e) {
                problems.add(filename + ": cannot read (" + e.getMessage() + ")");
            }
        }
        for (String filename : OPTIONAL_ARTIFACTS) {
            Path path = outDir.resolve(filename);
            if (!Files.isRegularFile(path)) {
                continue; // optional: only present, and only validated, when a run actually produced it
            }
            if (filename.equals("report.csv")) {
                problems.addAll(validateCsv(path, filename));
            } else if (filename.equals("runtime_drift.jsonl")) {
                problems.addAll(validateJsonl(path, filename));
            } else {
                problems.addAll(validateXmlWellformed(path, filename));
            }
        }
        return problems;
    }

    // --- Real third-party standards: validated only once AgentCE's own bounded profile has already
    // accepted the document (see the class docstring). --------------------------------------------

    private static final JsonSchemaFactory NIST_FACTORY =
            formatsAsAnnotations(SpecVersion.VersionFlag.V7, JsonMetaSchema.getV7());
    private static volatile JsonSchema nistValidator;

    private static JsonSchema compiledOscalNistValidator() {
        JsonSchema local = nistValidator;
        if (local == null) {
            local = NIST_FACTORY.getSchema(loadSchemaJson("oscal-assessment-results-nist-1.1.2"));
            nistValidator = local;
        }
        return local;
    }

    private static List<String> validateOscalArNist(JsonNode document) {
        Set<ValidationMessage> errors = compiledOscalNistValidator().validate(document);
        if (errors.isEmpty()) {
            return List.of();
        }
        return schemaProblems("oscal-ar.json (NIST OSCAL 1.1.2): ", errors);
    }

    private static final JsonSchemaFactory SARIF_FACTORY =
            formatsAsAnnotations(SpecVersion.VersionFlag.V4, JsonMetaSchema.getV4());
    private static volatile JsonSchema sarifValidator;

    private static JsonSchema compiledSarifValidator() {
        JsonSchema local = sarifValidator;
        if (local == null) {
            local = SARIF_FACTORY.getSchema(loadSchemaJson("sarif-2.1.0"));
            sarifValidator = local;
        }
        return local;
    }

    private static List<String> validateSarif210(JsonNode document) {
        Set<ValidationMessage> errors = compiledSarifValidator().validate(document);
        if (errors.isEmpty()) {
            return List.of();
        }
        return schemaProblems("results.sarif (OASIS SARIF 2.1.0): ", errors);
    }
}
