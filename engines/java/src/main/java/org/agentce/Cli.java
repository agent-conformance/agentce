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
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;

/**
 * The {@code agentce} command-line interface (SPEC §8.5): the same {@code --json} envelope and
 * exit-code scheme as the reference. {@code assess}, {@code validate}, {@code report}, and {@code
 * quickstart} are implemented on the existing ECS/report path (the same modules {@code conformance run}
 * already exercises); any other verb returns a stable {@code input_error} envelope rather than a guess.
 */
public final class Cli {
    private Cli() {}

    private static final String DEFAULT_OUT_DIR = "out";
    private static final List<String> REPORT_FORMATS = List.of("md", "html", "oscal", "sarif", "pack");
    private static final Set<String> VERDICT_OUTCOMES = Set.of("conformant", "non-conformant", "insufficient_evidence");

    public static void main(String[] args) {
        System.exit(run(args));
    }

    static int run(String[] args) {
        String command = args.length > 0 ? args[0] : null;
        if ("--version".equals(command) || "-V".equals(command) || "version".equals(command)) {
            System.out.println("agentce " + Version.ENGINE_VERSION);
            return 0;
        }

        // The numerics verb is a plain computation seam for the cross-engine vector check: it reads a
        // numerics-vectors case file and prints {caseName: result} as plain JSON, not the envelope.
        if ("numerics".equals(command)) {
            if (args.length < 2) {
                System.err.println("numerics: a case file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            System.out.println(Json.pretty(Numerics.computeVectorFile(java.nio.file.Path.of(args[1]))));
            return 0;
        }

        boolean json = Arrays.asList(args).contains("--json");
        CommandResult result;
        try {
            if ("conformance".equals(command)) {
                result = cmdConformance(args);
            } else if ("validate".equals(command)) {
                result = cmdValidate(args);
            } else if ("assess".equals(command)) {
                result = cmdAssess(args);
            } else if ("report".equals(command)) {
                result = cmdReport(args);
            } else if ("quickstart".equals(command)) {
                result = cmdQuickstart(args);
            } else {
                result = notImplemented(command == null ? "" : command);
            }
        } catch (AgentceError exc) {
            result = errorResult(command == null ? "" : command, exc);
        }
        emit(result, json);
        return result.exitCode();
    }

    private static void emit(CommandResult result, boolean json) {
        if (json) {
            System.out.println(Json.pretty(result.envelope()));
        } else {
            for (String line : result.humanLines) {
                System.out.println(line);
            }
        }
    }

    private static String flagValue(String[] args, String name) {
        int index = Arrays.asList(args).indexOf("--" + name);
        return index >= 0 && index + 1 < args.length ? args[index + 1] : null;
    }

    /** Every value following a (repeatable) {@code --name} in {@code args}, in order. */
    private static List<String> flagValues(String[] args, String name) {
        List<String> out = new ArrayList<>();
        String flag = "--" + name;
        for (int i = 0; i < args.length; i++) {
            if (flag.equals(args[i]) && i + 1 < args.length) {
                out.add(args[i + 1]);
            }
        }
        return out;
    }

    private static String requireDir(String raw, String key, String what) {
        String fix = "pass --" + key + " <dir>.";
        if (raw == null) {
            throw new InputError("input." + key + "_missing", what + " is required.", fix);
        }
        Path path = Paths.get(raw);
        if (!Files.isDirectory(path)) {
            throw new InputError(
                    "input." + key + "_not_a_directory", what + " '" + raw + "' is not an existing directory.", fix);
        }
        return raw;
    }

    private static String requireFile(String raw, String key, String what) {
        String fix = "pass --" + key + " <file>.";
        if (raw == null) {
            throw new InputError("input." + key + "_missing", what + " is required.", fix);
        }
        Path path = Paths.get(raw);
        if (!Files.isRegularFile(path)) {
            throw new InputError(
                    "input." + key + "_not_a_file", what + " '" + raw + "' is not an existing file.", fix);
        }
        return raw;
    }

    /**
     * A path safe to write into a shared artifact (SPEC §8.4: {@code invocation} records paths, never a
     * person or their local filesystem layout). Under the home directory it becomes {@code ~/…}; else
     * under the current directory it becomes a relative path; anywhere else only the final path
     * component is kept. Mirrors the Python reference's {@code _scrub_path}.
     */
    private static String scrubPath(String raw) {
        Path abs = Paths.get(raw).toAbsolutePath().normalize();
        Path home = Paths.get(System.getProperty("user.home")).toAbsolutePath().normalize();
        if (abs.equals(home) || abs.startsWith(home)) {
            String rel = home.relativize(abs).toString().replace(java.io.File.separatorChar, '/');
            return rel.isEmpty() ? "~/" : "~/" + rel;
        }
        Path cwd = Paths.get("").toAbsolutePath().normalize();
        if (abs.equals(cwd) || abs.startsWith(cwd)) {
            String rel = cwd.relativize(abs).toString().replace(java.io.File.separatorChar, '/');
            return rel.isEmpty() ? "." : rel;
        }
        Path fileName = abs.getFileName();
        return fileName != null ? fileName.toString() : abs.toString();
    }

    /** Write one canonical JSON object per line (no trailing newline when {@code records} is empty). */
    private static void writeJsonl(List<ObjectNode> records, Path path) {
        try {
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }
            StringBuilder out = new StringBuilder();
            for (ObjectNode record : records) {
                out.append(Canonical.canonicalString(record)).append('\n');
            }
            Files.write(path, out.toString().getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException("cannot write " + path + ": " + e.getMessage(), e);
        }
    }

    private static void writeQuarantineJsonl(List<Quarantine.Record> records, Path path) {
        List<ObjectNode> nodes = new ArrayList<>();
        for (Quarantine.Record r : records) {
            nodes.add(r.toJson());
        }
        writeJsonl(nodes, path);
    }

    /** Every catalog the engine ships (base and sector overlays), keyed {@code id@version}. */
    private static Map<String, Path> vendoredCatalogs() {
        Map<String, Path> found = new LinkedHashMap<>();
        Path root = Bundled.catalogsDir();
        for (String kind : new String[] {"base", "overlays"}) {
            Path dir = root.resolve(kind);
            List<String> names = new ArrayList<>();
            if (Files.isDirectory(dir)) {
                try (Stream<Path> stream = Files.list(dir)) {
                    stream.map(p -> p.getFileName().toString()).forEach(names::add);
                } catch (IOException ignored) {
                    // no vendored catalogs of this kind
                }
            }
            names.sort(Json::byteCompare);
            for (String name : names) {
                Path catalogYaml = dir.resolve(name).resolve("catalog.yaml");
                if (!Files.isRegularFile(catalogYaml)) {
                    continue;
                }
                try {
                    JsonNode meta = Yaml.parseFile(catalogYaml);
                    if (meta != null && meta.isObject() && meta.has("id") && meta.has("version")
                            && meta.get("id").isTextual() && meta.get("version").isTextual()) {
                        found.put(meta.get("id").asText() + "@" + meta.get("version").asText(), dir.resolve(name));
                    }
                } catch (RuntimeException ignored) {
                    // a malformed vendored catalog is skipped, never fatal to discovery
                }
            }
        }
        return found;
    }

    private record Resolved(List<Catalog> catalogs, List<String> labels) {}

    /**
     * The catalogs an assessment evaluates, and their {@code id@version} labels (mirrors the Python
     * reference's {@code _resolve_catalogs}). Each {@code --catalog-dir} is loaded as given. Each
     * requested id — the {@code --catalog} list, else the profile's declared {@code catalogs} when no
     * directory was passed — must resolve to a directory that was passed or to a vendored catalog.
     */
    private static Resolved resolveCatalogs(String requested, Profile profile, List<String> catalogDirs) {
        List<Catalog> loaded = new ArrayList<>();
        for (String d : catalogDirs) {
            loaded.add(Catalog.load(Paths.get(requireDir(d, "catalog-dir", "the catalog directory"))));
        }
        Map<String, Catalog> byLabel = new LinkedHashMap<>();
        for (Catalog c : loaded) {
            byLabel.put(c.id + "@" + c.version, c);
        }
        List<String> ids;
        if (requested != null && !requested.isEmpty()) {
            ids = new ArrayList<>();
            for (String s : requested.split(",")) {
                String trimmed = s.strip();
                if (!trimmed.isEmpty()) {
                    ids.add(trimmed);
                }
            }
        } else if (!catalogDirs.isEmpty()) {
            ids = new ArrayList<>();
        } else {
            ids = new ArrayList<>(profile.catalogs);
        }
        if (ids.isEmpty() && loaded.isEmpty()) {
            throw new InputError(
                    "input.catalog_missing",
                    "no catalog to evaluate: --catalog and --catalog-dir were not passed and the profile "
                            + "declares no catalogs.",
                    "pass --catalog <id@version>, or list the catalogs to apply under `catalogs:` in the profile.");
        }
        List<String> uniqueIds = new ArrayList<>(new LinkedHashSet<>(ids));
        List<String> unresolved = new ArrayList<>();
        for (String i : uniqueIds) {
            if (!byLabel.containsKey(i)) {
                unresolved.add(i);
            }
        }
        if (!unresolved.isEmpty()) {
            Map<String, Path> vendored = vendoredCatalogs();
            List<String> stillUnresolved = new ArrayList<>();
            for (String u : unresolved) {
                if (vendored.containsKey(u)) {
                    byLabel.put(u, Catalog.load(vendored.get(u)));
                } else {
                    stillUnresolved.add(u);
                }
            }
            unresolved = stillUnresolved;
        }
        if (!unresolved.isEmpty()) {
            Map<String, Path> vendored = vendoredCatalogs();
            TreeSet<String> available = new TreeSet<>(Json::byteCompare);
            available.addAll(byLabel.keySet());
            available.addAll(vendored.keySet());
            List<String> quoted = new ArrayList<>();
            for (String u : unresolved) {
                quoted.add("'" + u + "'");
            }
            throw new InputError(
                    "input.catalog_unresolved",
                    "no catalog directory resolves " + String.join(", ", quoted)
                            + " (available: " + (available.isEmpty() ? "none" : String.join(", ", available)) + ").",
                    "use an available <id>@<version>, or pass --catalog-dir <dir> for a catalog on disk.");
        }
        LinkedHashSet<String> labelSet = new LinkedHashSet<>(ids);
        for (Catalog c : loaded) {
            labelSet.add(c.id + "@" + c.version);
        }
        List<String> labels = new ArrayList<>(labelSet);
        List<Catalog> catalogs = new ArrayList<>();
        for (String l : labels) {
            catalogs.add(byLabel.get(l));
        }
        return new Resolved(catalogs, labels);
    }

    private static boolean evaluatedNothing(List<Assertions.Assertion> assertions) {
        for (Assertions.Assertion a : assertions) {
            if (VERDICT_OUTCOMES.contains(a.outcome)) {
                return false;
            }
        }
        return true;
    }

    private static String ids(List<String> list, int cap) {
        StringBuilder shown = new StringBuilder();
        int n = Math.min(list.size(), cap);
        for (int i = 0; i < n; i++) {
            if (i > 0) {
                shown.append(", ");
            }
            String item = list.get(i);
            shown.append('\'').append(item.length() > 80 ? item.substring(0, 80) : item).append('\'');
        }
        String result = shown.length() > 0 ? shown.toString() : "none";
        return list.size() > cap ? result + " and " + (list.size() - cap) + " more" : result;
    }

    /** The exit-3 error for a run whose every (control, subject) pair was inapplicable or unassessed. */
    private static InputError nothingEvaluated(Profile profile, List<JsonNode> accepted, int pairs) {
        TreeSet<String> declared = new TreeSet<>(Json::byteCompare);
        for (Profile.Subject s : profile.subjects) {
            declared.add(s.id);
        }
        TreeSet<String> seen = new TreeSet<>(Json::byteCompare);
        for (JsonNode e : accepted) {
            if (e.has("subject")) {
                seen.add(e.get("subject").asText());
            }
        }
        List<String> matched = new ArrayList<>();
        for (String s : declared) {
            if (seen.contains(s)) {
                matched.add(s);
            }
        }
        String why;
        if (accepted.isEmpty()) {
            why = "the bundle has no accepted events";
        } else if (matched.isEmpty()) {
            why = "none of the " + accepted.size() + " accepted events is about a subject the profile declares "
                    + "(profile: " + ids(new ArrayList<>(declared), 3) + "; bundle: " + ids(new ArrayList<>(seen), 3) + ")";
        } else {
            why = "the " + accepted.size() + " accepted events give no control an applicable population "
                    + "(subjects matched: " + ids(matched, 3) + ")";
        }
        return new InputError(
                "input.nothing_evaluated",
                "assessed " + pairs + " (control, subject) pairs and none reached conformant, non-conformant, or "
                        + "insufficient_evidence: " + why + ". The reports were written, but they judge nothing.",
                "emit under the subject and source the profile declares, and record the evidence the "
                        + "catalog's controls apply to.");
    }

    private static CommandResult cmdConformance(String[] args) {
        CommandResult result = new CommandResult("conformance");
        String action = args.length > 1 ? args[1] : null;
        if (!"run".equals(action)) {
            throw new InputError(
                    "input.conformance_action", "the only conformance action is `run`.", "run `agentce conformance run ...`.");
        }
        String engine = requireDir(flagValue(args, "engine"), "engine", "the engine path");
        String corpus = requireDir(flagValue(args, "corpus"), "corpus", "the corpus directory");
        String out = flagValue(args, "out");
        ObjectNode report = Conformance.runEcs(Paths.get(engine), Paths.get(corpus), out == null ? null : Paths.get(out));

        result.data.put("action", "run");
        result.data.put("engine", engine);
        result.data.put("corpus", corpus);
        if (out != null) {
            result.data.put("out", out);
        }
        result.data.setAll(report); // report.engine (impl/version) overwrites the engine path, as in the reference
        result.note("ECS: " + report.get("projects").get("identical").asInt() + "/"
                + report.get("projects").get("total").asInt() + " identical; claim " + report.get("claim").asText()
                + "; no_ml " + report.get("no_ml").asText());
        if (!"full".equals(report.get("claim").asText())) {
            result.addCode(ExitCode.FINDINGS.code);
        }
        return result;
    }

    private static CommandResult cmdValidate(String[] args) {
        CommandResult result = new CommandResult("validate");
        String bundleDir = requireDir(flagValue(args, "bundle"), "bundle", "the evidence bundle");
        Bundle bundle = Bundle.load(Paths.get(bundleDir)); // raises InputError (exit 3) on a missing/mismatching manifest
        Ingest.Result ingested = Ingest.ingest(bundle);
        result.data.put("bundle", bundleDir);
        result.data.put("bundle_digest", bundle.digest);
        result.data.put("accepted", ingested.accepted.size());
        result.data.put("quarantined", ingested.quarantined.size());
        ObjectNode quarantineByReason = result.data.putObject("quarantine_by_reason");
        for (Map.Entry<String, Integer> e : Quarantine.countsByReason(ingested.quarantined).entrySet()) {
            quarantineByReason.put(e.getKey(), e.getValue());
        }
        String out = flagValue(args, "out");
        if (out != null) {
            Path quarantinePath = Paths.get(out, "quarantine.jsonl");
            writeQuarantineJsonl(ingested.quarantined, quarantinePath);
            result.data.put("quarantine_file", quarantinePath.toString());
        }
        result.note("validated " + bundleDir + ": " + ingested.accepted.size() + " accepted, "
                + ingested.quarantined.size() + " quarantined");
        if (!ingested.quarantined.isEmpty()) {
            result.addCode(ExitCode.FINDINGS.code);
        }
        return result;
    }

    private record AssessOptions(
            String bundle,
            String profile,
            String catalog,
            String domain,
            List<String> catalogDirs,
            String out,
            String state,
            String invocationCommand) {}

    /** Run a full assessment (ingest, integrity, graph, coverage, applicability, evaluate, report) — the
     * same pipeline {@code conformance run} exercises per corpus project, generalised to an arbitrary
     * bundle. */
    private static CommandResult runAssess(AssessOptions options) {
        CommandResult result = new CommandResult("assess");
        String bundleDir = requireDir(options.bundle(), "bundle", "the evidence bundle");
        String profilePath = requireFile(options.profile(), "profile", "the applicability profile");
        Path out = Paths.get(options.out());
        Profile profileObj = Profile.load(Paths.get(profilePath));
        Resolved resolved = resolveCatalogs(options.catalog(), profileObj, options.catalogDirs());

        // Stage 1: ingest and validate. A missing/mismatching manifest aborts with exit 3.
        Bundle bundle = Bundle.load(Paths.get(bundleDir));
        Ingest.Result ingested = Ingest.ingest(bundle);
        writeQuarantineJsonl(ingested.quarantined, out.resolve("quarantine.jsonl"));

        // Stage 2: integrity verification, one IntegrityResult per stream.
        List<Integrity.Result> integrityResults = Integrity.verifyBundle(ingested.accepted, bundle.manifest, bundle.root);
        List<ObjectNode> integrityNodes = new ArrayList<>();
        for (Integrity.Result r : integrityResults) {
            integrityNodes.add(r.toJson());
        }
        writeJsonl(integrityNodes, out.resolve("integrity.jsonl"));

        // Stage 3: build the provenance graph (in-memory store).
        DomainBinding domain = options.domain() != null
                ? DomainBinding.load(Paths.get(requireFile(options.domain(), "domain", "the domain binding")))
                : DomainBinding.empty();
        GraphStore store = Graph.buildGraph(ingested.accepted, domain);
        int graphTriples = store.tripleCount();

        // Stage 4: coverage and reconciliation against independent denominators.
        ObjectNode coverage = Coverage.computeCoverage(ingested.accepted, profileObj, Paths.get(bundleDir));
        try {
            Files.createDirectories(out);
            Files.write(out.resolve("coverage.json"), (Json.pretty(coverage) + "\n").getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException("cannot write coverage.json: " + e.getMessage(), e);
        }

        // Stage 5: applicability resolution and drift.
        ArrayNode statements = Applicability.resolve(profileObj, ingested.accepted, List.of());
        List<ObjectNode> statementNodes = new ArrayList<>();
        for (JsonNode s : statements) {
            statementNodes.add((ObjectNode) s);
        }
        writeJsonl(statementNodes, out.resolve("applicability.jsonl"));
        int driftFindings = 0;
        for (JsonNode s : statements) {
            JsonNode drift = s.get("drift");
            if (drift != null && drift.isArray()) {
                driftFindings += drift.size();
            }
        }

        // Stage 6: catalog evaluation and report artifacts.
        List<Assertions.Assertion> evaluated =
                Assess.assessSubjects(ingested.accepted, profileObj, resolved.catalogs(), domain);

        // Stage 6a: incremental state (SPEC §5.4 B7, HR-10).
        String newWindowEnd = StateDir.windowEnd(profileObj.observationWindow, ingested.accepted);
        StateDir state = null;
        List<String> supersedes = List.of();
        if (options.state() != null) {
            state = StateDir.load(Paths.get(options.state())); // incompatible state_version aborts with exit 3
            supersedes = state.plan(bundle.digest, ingested.accepted, newWindowEnd);
        }

        String operatorEnv = System.getenv("AGENTCE_OPERATOR");
        List<String> invocation = List.of(options.invocationCommand(), scrubPath(bundleDir), scrubPath(profilePath));
        Report.writeReport(
                out, evaluated, bundle.digest, resolved.labels(),
                operatorEnv != null ? operatorEnv : "unknown",
                invocation, supersedes, Messages.DEFAULT_LANGUAGE, resolved.catalogs());
        if (state != null) {
            state.record(bundle.digest, out.resolve("manifest.json"), newWindowEnd);
        }

        int nonConformant = 0;
        for (Assertions.Assertion a : evaluated) {
            if ("non-conformant".equals(a.outcome)) {
                nonConformant++;
            }
        }
        Verdict.Summary summary = Verdict.summarize(evaluated);
        result.data.put("bundle", bundleDir);
        result.data.put("bundle_digest", bundle.digest);
        result.data.put("profile", profilePath);
        ArrayNode catalogsArr = result.data.putArray("catalogs");
        resolved.labels().forEach(catalogsArr::add);
        result.data.put("out", out.toString());
        result.data.put("accepted", ingested.accepted.size());
        result.data.put("quarantined", ingested.quarantined.size());
        result.data.put("streams", integrityResults.size());
        result.data.put("graph_triples", graphTriples);
        result.data.put("subjects", ((ObjectNode) coverage.get("subjects")).size());
        result.data.put("drift_findings", driftFindings);
        result.data.put("assertions", evaluated.size());
        ObjectNode summaryNode = result.data.putObject("summary");
        summaryNode.put("verdict", summary.verdict());
        ObjectNode countsNode = summaryNode.putObject("counts");
        for (Map.Entry<String, Integer> e : summary.counts().entrySet()) {
            countsNode.put(e.getKey(), e.getValue());
        }
        ArrayNode topGapsArr = summaryNode.putArray("top_gaps");
        for (Verdict.Gap gap : summary.topGaps()) {
            ObjectNode gapNode = topGapsArr.addObject();
            gapNode.put("outcome", gap.outcome());
            ArrayNode controlsArr = gapNode.putArray("controls");
            gap.controls().forEach(controlsArr::add);
            gapNode.put("more", gap.more());
        }
        if (options.state() != null) {
            ArrayNode supersedesArr = result.data.putArray("supersedes");
            supersedes.forEach(supersedesArr::add);
        }
        if (nonConformant > 0) {
            result.addCode(ExitCode.FINDINGS.code);
        }
        if (evaluatedNothing(evaluated)) {
            throw nothingEvaluated(profileObj, ingested.accepted, evaluated.size());
        }
        result.note("verdict: " + summary.verdict());
        result.note("assessed " + evaluated.size() + " (control, subject) pairs; " + nonConformant + " non-conformant");
        return result;
    }

    private static CommandResult cmdAssess(String[] args) {
        String outArg = flagValue(args, "out");
        return runAssess(new AssessOptions(
                flagValue(args, "bundle"),
                flagValue(args, "profile"),
                flagValue(args, "catalog"),
                flagValue(args, "domain"),
                flagValues(args, "catalog-dir"),
                outArg != null ? outArg : DEFAULT_OUT_DIR,
                flagValue(args, "state"),
                "assess"));
    }

    /** Assess the bundled quickstart project end to end — one command, offline (SPEC §13.4 AX-1). */
    private static CommandResult cmdQuickstart(String[] args) {
        CommandResult result = new CommandResult("quickstart");
        String outArg = flagValue(args, "out");
        String out = outArg != null ? outArg : DEFAULT_OUT_DIR;
        Path quickstart = Bundled.quickstartDir();
        if (!Files.isDirectory(quickstart)) {
            throw new InputError(
                    "input.quickstart_missing",
                    "the quickstart project is missing at " + quickstart + ".",
                    "reinstall the engine: the quickstart project ships inside the package.");
        }
        Path catalogDir = Bundled.catalogsDir().resolve("base").resolve("eu-ai-act");
        CommandResult assess = runAssess(new AssessOptions(
                quickstart.resolve("evidence").toString(),
                quickstart.resolve("applicability.yaml").toString(),
                "eu-ai-act@2026.09",
                quickstart.resolve("domain.linkml.yaml").toString(),
                List.of(catalogDir.toString()),
                out,
                null,
                "quickstart"));
        result.data.setAll(assess.data);
        result.data.put("quickstart", "ok");
        for (int code : assess.applicableCodes()) {
            if (code != ExitCode.OK.code) {
                result.addCode(code);
            }
        }
        for (String line : assess.humanLines) {
            result.note(line);
        }
        int assertionsCount = assess.data.has("assertions") ? assess.data.get("assertions").asInt() : 0;
        result.note("quickstart complete: " + assertionsCount + " assertions; report in " + out);
        return result;
    }

    /** Re-render a report from a committed {@code assertions.json} (SPEC §9.4); {@code --validate} and
     * the {@code public} format are not yet ported and are refused with a named, honest error rather
     * than a silent guess. */
    private static CommandResult cmdReport(String[] args) {
        CommandResult result = new CommandResult("report");
        if (Arrays.asList(args).contains("--validate")) {
            throw new InputError(
                    "input.report_validate_unsupported",
                    "the Java engine has no report --validate support.",
                    "validate the report's artifacts against their vendored schemas with the Python engine.");
        }
        String source = requireFile(flagValue(args, "from"), "from", "the assertions file");
        String formatArg = flagValue(args, "format");
        String format = formatArg != null ? formatArg : "md";
        if (!REPORT_FORMATS.contains(format)) {
            throw new InputError(
                    "input.report_format",
                    "unknown or not-yet-implemented report format '" + format + "'.",
                    "choose one of: " + String.join(", ", REPORT_FORMATS) + ".");
        }
        JsonNode parsed = Json.parseFile(Paths.get(source));
        List<Assertions.Assertion> assertions = new ArrayList<>();
        if (parsed != null && parsed.isArray()) {
            for (JsonNode node : parsed) {
                assertions.add(Assertions.fromJson(node));
            }
        }
        Map<String, Integer> counts = Assertions.aggregate(assertions);
        String rendering;
        switch (format) {
            case "md" -> rendering = Report.renderReportMd(assertions, counts, Messages.DEFAULT_LANGUAGE);
            case "html" -> rendering = Report.renderReportHtml(assertions, counts, Messages.DEFAULT_LANGUAGE);
            case "oscal" -> rendering = Json.pretty(Report.renderOscal(assertions)) + "\n";
            case "sarif" -> rendering = Json.pretty(Report.renderSarif(assertions)) + "\n";
            default -> {
                Map<String, List<Assertions.Assertion>> bySubject = new LinkedHashMap<>();
                for (Assertions.Assertion a : assertions) {
                    bySubject.computeIfAbsent(a.subject, k -> new ArrayList<>()).add(a);
                }
                List<String> subjects = new ArrayList<>(bySubject.keySet());
                subjects.sort(Json::byteCompare);
                ObjectNode packs = Json.nodes().objectNode();
                for (String subject : subjects) {
                    packs.set(subject, Report.renderEvidencePack(subject, bySubject.get(subject), null));
                }
                rendering = Json.pretty(packs) + "\n";
            }
        }
        result.data.put("from", source);
        result.data.put("format", format);
        result.data.put("rendering", rendering);
        String out = flagValue(args, "out");
        if (out != null) {
            try {
                Files.write(Paths.get(out), rendering.getBytes(StandardCharsets.UTF_8));
            } catch (IOException e) {
                throw new IllegalStateException("cannot write " + out + ": " + e.getMessage(), e);
            }
            result.data.put("out", out);
        }
        result.note(rendering);
        return result;
    }

    private static CommandResult notImplemented(String command) {
        CommandResult result = new CommandResult(command);
        result.addCode(ExitCode.INPUT_ERROR.code);
        ObjectNode error = result.data.putObject("error");
        error.put("message_key", "cli.not_implemented");
        error.put("detail", "this command is not yet implemented in the Java engine");
        if (command == null || command.isEmpty()) {
            error.putNull("command");
        } else {
            error.put("command", command);
        }
        result.note("agentce (Java engine) — command not yet implemented.");
        return result;
    }

    private static CommandResult errorResult(String command, AgentceError error) {
        CommandResult result = new CommandResult(command);
        result.addCode(error.exitCode);
        ObjectNode err = result.data.putObject("error");
        err.put("message_key", error.key);
        err.put("detail", error.reason);
        err.put("fix", error.fix);
        result.note(error.key + ": " + error.reason);
        if (error.fix != null && !error.fix.isEmpty()) {
            result.note("fix: " + error.fix);
        }
        return result;
    }
}
