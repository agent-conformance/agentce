package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;

/**
 * The test seams (18.108): small computations the cross-engine gates and tools drive directly
 * ({@code java -cp <jar> org.agentce.Seams <seam> <path>}). They ship in the jar so the
 * installed-artifact check can run them, but they are not {@code agentce} commands.
 */
public final class Seams {
    private Seams() {}

    public static void main(String[] args) {
        System.exit(run(args));
    }

    static int run(String[] args) {
        String command = args.length > 0 ? args[0] : null;

        // race-read is the VG-CATALOG-READ-ONCE seam (18.171): `race-read <file> <swap-bytes|-> <count-file>
        // <agentce args...>` runs the CLI with a writer racing it. Every read of <file> by the catalog loader
        // is counted and the count written to <count-file>; right after the first, <file> on disk becomes
        // the bytes of <swap-bytes>, so a second read would see other bytes. A run whose hook never fired
        // exits 70, so the gate cannot pass on a race that did not run.
        if ("race-read".equals(command)) {
            if (args.length < 4) {
                System.err.println("race-read: <file> <swap-bytes|-> <count-file> <agentce args...> are required");
                return ExitCode.INPUT_ERROR.code;
            }
            try {
                Path target = Paths.get(args[1]).toRealPath();
                byte[] swap = "-".equals(args[2]) ? null : Files.readAllBytes(Paths.get(args[2]));
                int[] count = {0};
                Catalog.afterRead = path -> {
                    try {
                        if (path.toRealPath().equals(target) && ++count[0] == 1 && swap != null) {
                            Files.write(target, swap);
                        }
                    } catch (IOException e) {
                        throw new java.io.UncheckedIOException(e);
                    }
                };
                int code;
                try {
                    code = Cli.run(java.util.Arrays.copyOfRange(args, 4, args.length));
                } finally {
                    Catalog.afterRead = null;
                    Files.writeString(Paths.get(args[3]), Integer.toString(count[0]));
                }
                return count[0] == 0 ? 70 : code;
            } catch (IOException e) {
                System.err.println("race-read: " + e.getMessage());
                return 70;
            }
        }

        // The numerics verb is a plain computation seam for the cross-engine vector check: it reads a
        // numerics-vectors case file and prints {caseName: result} as plain JSON, not the envelope.
        if ("numerics".equals(command)) {
            if (args.length < 2) {
                System.err.println("numerics: a case file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            System.out.println(Json.pretty(Numerics.computeVectorFile(Paths.get(args[1]))));
            return 0;
        }

        // digest-tree is a plain computation seam (the same pattern as `numerics` above), driven from
        // outside the repo's Java sources by `tools/catalog_digest_check.py` against the built jar: it
        // prints one catalog directory's real content digest, nothing else.
        if ("digest-tree".equals(command)) {
            if (args.length < 2) {
                System.err.println("digest-tree: a directory path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            System.out.println(Catalog.provenanceDigest(Paths.get(args[1])));
            return 0;
        }

        // security-view is a plain computation seam (the same pattern as `numerics`/`digest-tree`
        // above), driven by the security-view build gate's cross-engine diff (18.16, C5): it reads a
        // fixture file with {activity, assertions}, runs SecurityView.compute, and prints the result
        // as JSON -- not part of the public `assess` command surface (no engine here has
        // `--for`/multi-format `write_report` yet, TRADEOFFS row 8).
        if ("security-view".equals(command)) {
            if (args.length < 2) {
                System.err.println("security-view: a fixture file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            JsonNode data = readFixture(Paths.get(args[1]));
            ObjectNode activity = (ObjectNode) data.get("activity");
            List<Assertions.Assertion> assertions = new ArrayList<>();
            for (JsonNode a : data.get("assertions")) {
                assertions.add(Assertions.fromJson(a));
            }
            System.out.println(SecurityView.compute(activity, assertions).toString());
            return 0;
        }

        // auditor-view is the same kind of test-only seam (18.17a, VG-DEVIATIONS-PARITY): it reads a
        // fixture file with {assertions, deviations}, runs AuditorView.computeAuditorView, and prints
        // canonical JSON, the bytes Python's auditor.json holds -- not part of the public command surface.
        if ("auditor-view".equals(command)) {
            if (args.length < 2) {
                System.err.println("auditor-view: a fixture file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            JsonNode data = readFixture(Paths.get(args[1]));
            List<Assertions.Assertion> assertions = new ArrayList<>();
            for (JsonNode a : data.get("assertions")) {
                assertions.add(Assertions.fromJson(a));
            }
            JsonNode deviationsNode = data.get("deviations");
            List<JsonNode> deviations = null;
            if (deviationsNode != null && !deviationsNode.isNull()) {
                deviations = new ArrayList<>();
                deviationsNode.forEach(deviations::add);
            }
            System.out.println(Canonical.canonicalString(AuditorView.computeAuditorView(assertions, deviations)));
            return 0;
        }

        // report-formats is the same kind of test-only seam (18.16c, VG-REPORT-FORMAT-PARITY): for each
        // report directory, it renders report.junit.xml, report.csv, oscal-ar.xml, public-statement.md and
        // report.pdf from that directory's assertions.json, oscal-ar.json and manifest (catalogs, run.started_at,
        // run.report_language), the way Python's write_report does, into <out-root>/<index>/. `assess --emit`
        // reaches them with 18.16b.
        if ("report-formats".equals(command)) {
            if (args.length < 3) {
                System.err.println("report-formats: <out-root> and at least one report directory are required");
                return ExitCode.INPUT_ERROR.code;
            }
            try {
                for (int index = 0; index + 2 < args.length; index++) {
                    Path source = Paths.get(args[index + 2]);
                    List<Assertions.Assertion> assertions = new ArrayList<>();
                    readFixture(source.resolve("assertions.json")).forEach(a -> assertions.add(Assertions.fromJson(a)));
                    JsonNode manifest = readFixture(source.resolve("manifest.json"));
                    List<String> catalogs = new ArrayList<>();
                    manifest.path("inputs").path("catalogs").forEach(
                            c -> catalogs.add(c.path("id").asText() + "@" + c.path("version").asText()));
                    Path out = Paths.get(args[1], String.valueOf(index));
                    Files.createDirectories(out);
                    Files.writeString(out.resolve("report.junit.xml"), Report.renderJunit(assertions));
                    Files.writeString(out.resolve("report.csv"), Report.renderCsv(assertions));
                    Files.writeString(out.resolve("oscal-ar.xml"),
                            Report.renderOscalXml(readFixture(source.resolve("oscal-ar.json"))));
                    Files.writeString(out.resolve("public-statement.md"), Report.renderPublicStatement(
                            assertions, catalogs, Report.appliedDeviationIds(assertions)));
                    JsonNode run = manifest.path("run");
                    Files.write(out.resolve("report.pdf"), Report.renderPdf(assertions, Assertions.aggregate(assertions),
                            run.path("started_at").asText(), run.path("report_language").asText()));
                }
            } catch (IOException e) {
                System.err.println("report-formats: " + e.getMessage());
                return ExitCode.INPUT_ERROR.code;
            }
            return 0;
        }

        // fail-on-check is the same kind of test-only seam (18.73): it reads a fixture file with
        // {assertions, expressions} and, per expression in order, prints one canonical JSON line:
        // {"error": {key, cause, fix}} on refusal, else {"matched": [one boolean per assertion]} --
        // not part of the public command surface.
        if ("fail-on-check".equals(command)) {
            if (args.length < 2) {
                System.err.println("fail-on-check: a fixture file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            JsonNode data = readFixture(Paths.get(args[1]));
            List<Assertions.Assertion> assertions = new ArrayList<>();
            for (JsonNode a : data.get("assertions")) {
                assertions.add(Assertions.fromJson(a));
            }
            for (JsonNode expression : data.get("expressions")) {
                ObjectNode line = Json.nodes().objectNode();
                try {
                    FailOn.Expression parsed = FailOn.parse(expression.textValue());
                    ArrayNode matched = line.putArray("matched");
                    for (Assertions.Assertion a : assertions) {
                        matched.add(parsed.matches(a));
                    }
                } catch (AgentceError exc) {
                    ObjectNode error = line.putObject("error");
                    error.put("key", exc.key);
                    error.put("cause", exc.reason);
                    error.put("fix", exc.fix);
                }
                System.out.println(Canonical.canonicalString(line));
            }
            return 0;
        }

        // otel-genai-fixture is a plain computation seam (the same pattern as `security-view`
        // above), driven by the otel-genai adapter's cross-engine parity check (18.29, C3/C4): it
        // reads `<dir>/input.json` and `<dir>/adapt.json` the same way the Python reference's
        // fixtures module does, calls `adapt`, and for each emitted event (in the adapter's own
        // pinned time/id order) attempts `canonicalString`; on success prints `EVENT <result>`. On
        // a `CanonicalizationError`, nothing further goes to stdout -- `ERROR canonical:<reason>`
        // goes to stderr and the process exits 1, so the check can tell "the adapter accepted this
        // but the event cannot be serialised" apart from "the adapter refused the whole document"
        // (an `OtelGenaiAdapterError`, printed the same way without the `canonical:` prefix). On a
        // clean run, one final `REPORT <json>` line follows every `EVENT` line.
        if ("otel-genai-fixture".equals(command)) {
            if (args.length < 2) {
                System.err.println("otel-genai-fixture: a directory path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            Path dir = Paths.get(args[1]);
            byte[] inputBytes;
            JsonNode adaptArgs;
            try {
                inputBytes = Files.readAllBytes(dir.resolve("input.json"));
                adaptArgs = Json.readBundled(Files.readAllBytes(dir.resolve("adapt.json")));
            } catch (IOException | RuntimeException e) {
                System.err.println("otel-genai-fixture: " + e.getMessage());
                return ExitCode.INPUT_ERROR.code;
            }
            String subject = adaptArgs.has("subject") ? adaptArgs.get("subject").asText() : null;
            String sourceClass = adaptArgs.has("source_class") && !adaptArgs.get("source_class").isNull()
                    ? adaptArgs.get("source_class").asText()
                    : null;
            String source = adaptArgs.has("source") && !adaptArgs.get("source").isNull()
                    ? adaptArgs.get("source").asText()
                    : null;
            try {
                OtelGenai.AdaptResult result = OtelGenai.adapt(inputBytes, subject, sourceClass, source);
                for (JsonNode event : result.events()) {
                    String line;
                    try {
                        line = Canonical.canonicalString(event);
                    } catch (Canonical.CanonicalizationError exc) {
                        System.err.println("ERROR canonical:" + exc.reason);
                        return 1;
                    }
                    System.out.println("EVENT " + line);
                }
                System.out.println("REPORT " + otelGenaiReportLine(result.report()));
                return 0;
            } catch (OtelGenai.OtelGenaiAdapterError exc) {
                System.err.println("ERROR " + exc.reason);
                return 1;
            }
        }

        // json-cases is the JSON profile seam (SPEC 8.7, 18.184x) for tools/json_parity_check.py: every row
        // of the shared case table through this engine's reader, printed as one result per row in table order,
        // the shape Python's agentce.tools.json_cases and TypeScript's json-cases seam print.
        if ("json-cases".equals(command)) {
            if (args.length < 2) {
                System.err.println("json-cases: a case file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            ArrayNode out = Json.nodes().arrayNode();
            for (JsonNode c : Json.readBundled(readAll(Paths.get(args[1]))).get("cases")) {
                out.addArray().add(c.get("id").asText()).add(jsonCaseResult(c));
            }
            System.out.println(Json.compact(out));
            return 0;
        }

        // assess-emit runs this engine's real `assess` with extra formats written (18.16e): until 18.16b wires
        // `assess --emit` here, report_format_parity_check.py compares the remediation output through it.
        if ("assess-emit".equals(command)) {
            List<String> names = args.length < 2 ? List.of() : List.of(args[1].split(",", -1));
            List<String> presets = names.stream().filter(n -> n.startsWith("for=")).map(n -> n.substring(4)).toList();
            List<String> emits = names.stream().filter(n -> !n.startsWith("for=")).toList();
            if (names.isEmpty()
                    || !emits.stream().allMatch(n -> n.equals("remediation") || n.equals("skill"))
                    || presets.size() > 1
                    || !presets.stream().allMatch(Report.AUDIENCE_VIEWS::containsKey)) {
                System.err.println(
                        "assess-emit: the formats are remediation and skill, plus at most one for=<registered view>");
                return ExitCode.INPUT_ERROR.code;
            }
            Report.SEAM_EMIT.addAll(emits);
            Report.SEAM_FOR = presets.isEmpty() ? null : presets.get(0);
            try {
                return Cli.run(java.util.Arrays.copyOfRange(args, 2, args.length));
            } finally {
                Report.SEAM_EMIT.clear();
                Report.SEAM_FOR = null;
            }
        }

        // remediation-md renders every package in a case file ({cases: [{name, package}]}) with
        // renderRemediationMd and writes {name: markdown} as JSON to the output path (18.16e).
        if ("remediation-md".equals(command)) {
            if (args.length < 3) {
                System.err.println("remediation-md: a case file and an output path are required");
                return ExitCode.INPUT_ERROR.code;
            }
            ObjectNode rendered = Json.nodes().objectNode();
            for (JsonNode c : Json.readBundled(readAll(Paths.get(args[1]))).get("cases")) {
                rendered.put(c.get("name").asText(), Remediation.renderRemediationMd(c.get("package")));
            }
            try {
                Files.writeString(Paths.get(args[2]), Json.compact(rendered), StandardCharsets.UTF_8);
            } catch (IOException e) {
                System.err.println("remediation-md: " + e.getMessage());
                return 70;
            }
            return 0;
        }

        // security-cases renders every case of a case file ({cases: [{name, language, view}]}) with the
        // security view's renderers and writes {"<name>.md": text, "<name>.html": text} as JSON (18.16g).
        if ("security-cases".equals(command)) {
            if (args.length < 3) {
                System.err.println("security-cases: a case file and an output path are required");
                return ExitCode.INPUT_ERROR.code;
            }
            ObjectNode rendered = Json.nodes().objectNode();
            for (JsonNode c : Json.readBundled(readAll(Paths.get(args[1]))).get("cases")) {
                String name = c.get("name").asText();
                String language = c.get("language").asText();
                rendered.put(name + ".md", Report.renderSecurityMd(c.get("view"), language));
                rendered.put(name + ".html", Report.renderSecurityHtml(c.get("view"), language));
            }
            try {
                Files.writeString(Paths.get(args[2]), Json.compact(rendered), StandardCharsets.UTF_8);
            } catch (IOException e) {
                System.err.println("security-cases: " + e.getMessage());
                return 70;
            }
            return 0;
        }

        // skill-cases renders every case of a case file ({cases: [{name, kind, ...}]}: skill, finding, reverify,
        // tool_names, filename) with the skill folder's functions and writes {name: text} as JSON (18.16f).
        if ("skill-cases".equals(command)) {
            if (args.length < 3) {
                System.err.println("skill-cases: a case file and an output path are required");
                return ExitCode.INPUT_ERROR.code;
            }
            ObjectNode rendered = Json.nodes().objectNode();
            for (JsonNode c : Json.readBundled(readAll(Paths.get(args[1]))).get("cases")) {
                rendered.put(c.get("name").asText(), skillCase(c));
            }
            try {
                Files.writeString(Paths.get(args[2]), Json.compact(rendered), StandardCharsets.UTF_8);
            } catch (IOException e) {
                System.err.println("skill-cases: " + e.getMessage());
                return 70;
            }
            return 0;
        }

        System.err.println("seams: unknown seam '" + (command == null ? "" : command) + "'");
        return ExitCode.INPUT_ERROR.code;
    }

    /** One skill_cases.json case through the skill folder function its kind names. */
    static String skillCase(JsonNode c) {
        switch (c.get("kind").asText()) {
            case "skill":
                return Skill.renderSkillMd(c.get("spec_version").asText(), c.get("cli_version").asText(),
                        c.get("catalog_version").asText(), c.get("assertions_digest").asText());
            case "finding":
                return Skill.renderSkillFindingMd(c.get("finding"), texts(c.get("tool_calls")));
            case "reverify":
                return Skill.renderReverifyMd(texts(c.get("argv")));
            case "tool_names": {
                List<JsonNode> events = new java.util.ArrayList<>();
                c.get("events").forEach(events::add);
                return String.join("\n", Skill.toolNamesForSubject(events));
            }
            case "filename":
                return Skill.skillFindingFilename(c.get("control").asText(), c.get("index").asInt());
            default:
                throw new IllegalArgumentException("skill-cases: unknown kind " + c.get("kind"));
        }
    }

    private static List<String> texts(JsonNode array) {
        List<String> out = new java.util.ArrayList<>();
        array.forEach(n -> out.add(n.asText()));
        return out;
    }

    private static byte[] readAll(Path path) {
        try {
            return Files.readAllBytes(path);
        } catch (IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    private static JsonNode jsonValue(JsonNode c, String field) {
        return Json.readJson(c.get(field).asText().getBytes(StandardCharsets.UTF_8), Json.Bom.REFUSE);
    }

    private static ObjectNode jsonRefusal(Json.JsonProfileError exc) {
        ObjectNode node = Json.nodes().objectNode();
        node.putArray("refuse").add(exc.rule()).add(exc.line());
        return node;
    }

    private static ObjectNode jsonReaderResult(JsonNode c) {
        byte[] raw = c.has("hex")
                ? HexFormat.of().parseHex(c.get("hex").asText())
                : c.get("text").asText().getBytes(StandardCharsets.UTF_8);
        Json.Bom bom = c.has("bom") && c.get("bom").asText().equals("ignore") ? Json.Bom.IGNORE : Json.Bom.REFUSE;
        ObjectNode node = Json.nodes().objectNode();
        try {
            if (c.get("mode").asText().equals("json")) {
                node.put("canonical", Json.renderValue(Json.readJson(raw, bom)));
                return node;
            }
            String decoded = Json.decodeProfile(raw, bom);
            ArrayNode records = node.putArray("records");
            for (Json.ByteLine line : Json.jsonlByteRecords(decoded.getBytes(StandardCharsets.UTF_8))) {
                Json.readJsonlLine(new String(line.bytes(), StandardCharsets.UTF_8), line.number());
                records.add(line.number());
            }
            return node;
        } catch (Json.JsonProfileError exc) {
            return jsonRefusal(exc);
        }
    }

    private static ObjectNode jsonCaseResult(JsonNode c) {
        if (c.has("mode")) {
            return jsonReaderResult(c);
        }
        ObjectNode node = Json.nodes().objectNode();
        switch (c.get("rule").asText()) {
            case "number_rendering" -> node.put("rendered", Json.renderNumber(jsonValue(c, "input")));
            case "compare_by_value", "compare_across_types" -> {
                JsonNode left = jsonValue(c, "left");
                JsonNode right = jsonValue(c, "right");
                node.putArray("equal").add(Json.jsonEqual(left, right)).add(Json.jsonEqual(right, left));
            }
            case "integer_field_float_form" -> node.put("integer", Json.isJsonInteger(jsonValue(c, "text")));
            case "non_integer_number" -> {
                try {
                    Canonical.canonicalString(jsonValue(c, "text"));
                    node.putNull("refusal");
                } catch (Canonical.CanonicalizationError exc) {
                    node.put("refusal", exc.getMessage());
                }
            }
            default -> node.put("unknown_rule", c.get("rule").asText());
        }
        return node;
    }

    /**
     * The {@code otel-genai-fixture} seam's {@code REPORT} line: compact JSON, field order fixed
     * to match TypeScript's {@code JSON.stringify} output byte for byte (18.29, C3).
     */
    private static String otelGenaiReportLine(OtelGenai.AdapterReport report) {
        ObjectNode node = Json.nodes().objectNode();
        node.put("adapter", report.adapter());
        ArrayNode conventions = node.putArray("conventions");
        report.conventions().forEach(conventions::add);
        node.put("spans_seen", report.spansSeen());
        node.put("events_emitted", report.eventsEmitted());
        ArrayNode skipped = node.putArray("skipped");
        for (OtelGenai.SkippedSpan s : report.skipped()) {
            ObjectNode entry = skipped.addObject();
            entry.put("name", s.name());
            entry.put("reason", s.reason());
            entry.put("span_id", s.spanId());
        }
        return Json.compact(node);
    }

    /** A seam's fixture file, read as bundled data (the TypeScript seams' readBundled). */
    private static JsonNode readFixture(Path file) {
        try {
            return Json.readBundled(Files.readAllBytes(file));
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read JSON " + file + ": " + e.getMessage(), e);
        }
    }
}
