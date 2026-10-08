package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
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
            JsonNode data = Json.parseFile(Paths.get(args[1]));
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
            JsonNode data = Json.parseFile(Paths.get(args[1]));
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

        // fail-on-check is the same kind of test-only seam (18.73): it reads a fixture file with
        // {assertions, expressions} and, per expression in order, prints one canonical JSON line:
        // {"error": {key, cause, fix}} on refusal, else {"matched": [one boolean per assertion]} --
        // not part of the public command surface.
        if ("fail-on-check".equals(command)) {
            if (args.length < 2) {
                System.err.println("fail-on-check: a fixture file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            JsonNode data = Json.parseFile(Paths.get(args[1]));
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
                adaptArgs = Json.parseFile(dir.resolve("adapt.json"));
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

        System.err.println("seams: unknown seam '" + (command == null ? "" : command) + "'");
        return ExitCode.INPUT_ERROR.code;
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

}
