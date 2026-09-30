package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.networknt.schema.JsonSchema;
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

    private static final JsonSchemaFactory LOCAL_FACTORY =
            JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012);
    private static final Map<String, JsonSchema> LOCAL_VALIDATORS = new ConcurrentHashMap<>();

    private static JsonSchema localValidatorFor(String schemaName) {
        return LOCAL_VALIDATORS.computeIfAbsent(schemaName, n -> LOCAL_FACTORY.getSchema(loadSchemaJson(n)));
    }

    private static String location(com.networknt.schema.JsonNodePath path) {
        int count = path.getNameCount();
        if (count == 0) {
            return "<root>";
        }
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < count; i++) {
            if (i > 0) {
                out.append('/');
            }
            out.append(path.getElement(i));
        }
        return out.toString();
    }

    private static List<String> schemaProblems(String prefix, Set<ValidationMessage> errors) {
        List<Map.Entry<String, ValidationMessage>> entries = new ArrayList<>();
        for (ValidationMessage m : errors) {
            entries.add(Map.entry(location(m.getInstanceLocation()), m));
        }
        entries.sort((a, b) -> Json.byteCompare(a.getKey(), b.getKey()));
        List<String> out = new ArrayList<>();
        for (Map.Entry<String, ValidationMessage> e : entries) {
            out.add(prefix + e.getKey() + ": " + e.getValue().getMessage());
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
        } catch (XMLStreamException e) {
            return List.of(filename + ": invalid XML (" + e.getMessage() + ")");
        } catch (IOException e) {
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

    private static final JsonSchemaFactory NIST_FACTORY = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V7);
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

    private static final JsonSchemaFactory SARIF_FACTORY = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V4);
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
