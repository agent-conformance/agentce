package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.AccessDeniedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

/**
 * The Engine Conformance Suite (ECS) runner (SPEC §11.5). {@code agentce conformance run} executes
 * every project in a corpus through the engine and emits an {@code implementation-report.json} recording
 * how many projects were identical and the overall {@code claim}, folding in the {@code no_ml} check:
 * {@code claim: full} requires {@code no_ml: pass}. The corpus is materialised by running its Python
 * generator (the shared deterministic source), since the corpus output is never committed. A faithful
 * port.
 */
public final class Conformance {
    private Conformance() {}

    private static final String ECS_OPERATOR = "ecs";

    private static final class Corpus {
        final Path root;

        Corpus(Path root) {
            this.root = root;
        }
    }

    /** Whether {@code path} in the corpus folder is a regular file after links. One it cannot reach is
     * refused as unreadable, never read as absent, as Python's {@code _materialise_corpus} (18.169). */
    private static boolean isCorpusFile(Path corpusDir, Path path) {
        try {
            return Files.readAttributes(path, BasicFileAttributes.class).isRegularFile();
        } catch (AccessDeniedException e) {
            throw new InputError(
                    "input.corpus_unreadable",
                    path + " in the corpus folder " + corpusDir + " could not be read (Permission denied).",
                    "make the corpus folder readable and enterable, then re-run.");
        } catch (IOException | RuntimeException e) {
            return false;
        }
    }

    /** corpus-manifest.json: UTF-8 JSON, an object whose {@code projects} (when present) is an array of
     * objects. One it cannot read, or of another shape, is refused {@code input.corpus_unreadable},
     * never a crash, as Python's {@code run_ecs}. */
    private static JsonNode readManifest(Path manifestPath, Path corpusDir) {
        byte[] raw;
        try {
            raw = Files.readAllBytes(manifestPath);
        } catch (IOException e) {
            String why = e instanceof AccessDeniedException ? "Permission denied" : e.getMessage();
            throw new InputError(
                    "input.corpus_unreadable",
                    manifestPath + " in the corpus folder " + corpusDir + " could not be read (" + why + ").",
                    "make the corpus folder readable and enterable, then re-run.");
        }
        JsonNode manifest;
        try {
            manifest = Json.readJson(raw, Json.Bom.IGNORE);
        } catch (Json.JsonProfileError e) {
            throw new InputError(
                    "input.corpus_unreadable",
                    manifestPath + " in the corpus folder " + corpusDir + " is " + e.getMessage() + ".",
                    "regenerate the corpus manifest, then re-run.");
        }
        boolean shaped = manifest != null && manifest.isObject();
        if (shaped && manifest.has("projects")) {
            JsonNode projects = manifest.get("projects");
            shaped = projects.isArray();
            for (JsonNode p : projects) {
                shaped &= p.isObject();
            }
        }
        if (!shaped) {
            throw new InputError(
                    "input.corpus_unreadable",
                    manifestPath + " in the corpus folder " + corpusDir
                            + " is not a JSON object with a projects array.",
                    "regenerate the corpus manifest, then re-run.");
        }
        return manifest;
    }

    private static Corpus materialiseCorpus(Path corpusDir) {
        if (isCorpusFile(corpusDir, corpusDir.resolve("corpus-manifest.json"))) {
            return new Corpus(corpusDir);
        }
        Path generator = corpusDir.resolve("generator/generate.py");
        if (isCorpusFile(corpusDir, generator)) {
            try {
                Path tmp = Files.createTempDirectory("agentce-corpus-");
                Process proc = new ProcessBuilder(
                                "uv", "run", "--project", corpusDir.toString(), "python", generator.toString(),
                                "--set", "v1", "--out", tmp.toString())
                        .redirectErrorStream(true)
                        .start();
                String output = new String(proc.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
                int code = proc.waitFor();
                if (code != 0 || !Files.isRegularFile(tmp.resolve("corpus-manifest.json"))) {
                    throw new InputError(
                            "input.corpus_generate_failed",
                            "generating the corpus at " + corpusDir + " failed: "
                                    + output.substring(0, Math.min(200, output.length())),
                            "check that the corpus generator runs: python corpus/generator/generate.py --out <dir>.");
                }
                return new Corpus(tmp);
            } catch (IOException | InterruptedException e) {
                if (e instanceof InterruptedException) {
                    Thread.currentThread().interrupt();
                }
                throw new InputError(
                        "input.corpus_generate_failed",
                        "generating the corpus at " + corpusDir + " failed: " + e.getMessage(),
                        "check that the corpus generator runs: python corpus/generator/generate.py --out <dir>.");
            }
        }
        throw new InputError(
                "input.corpus_not_found",
                corpusDir + " has neither a corpus-manifest.json nor a generator/generate.py.",
                "pass a generated corpus directory or the corpus source tree.");
    }

    private static final class Catalogs {
        final List<Catalog> catalogs;
        final List<String> labels;

        Catalogs(List<Catalog> catalogs, List<String> labels) {
            this.catalogs = catalogs;
            this.labels = labels;
        }
    }

    private static Catalogs catalogsFor(Path repoRoot) {
        Path base = repoRoot.resolve("spec/catalogs/base");
        List<Catalog> catalogs = new ArrayList<>();
        List<String> labels = new ArrayList<>();
        List<String> subdirs = new ArrayList<>();
        if (Files.isDirectory(base)) {
            try (Stream<Path> stream = Files.list(base)) {
                stream.map(p -> p.getFileName().toString()).forEach(subdirs::add);
            } catch (IOException e) {
                subdirs.clear();
            }
        }
        subdirs.sort(Json::byteCompare);
        for (String sub : subdirs) {
            if (Files.isRegularFile(base.resolve(sub).resolve("catalog.yaml"))) {
                Catalog catalog = Catalog.load(base.resolve(sub));
                catalogs.add(catalog);
                labels.add(catalog.id + "@" + catalog.version);
            }
        }
        if (catalogs.isEmpty()) {
            throw new InputError(
                    "input.catalog_not_found",
                    "no base catalog found under " + base + ".",
                    "the engine expects the base catalog under spec/catalogs/base/<id>/.");
        }
        return new Catalogs(catalogs, labels);
    }

    private static void assessProject(Path corpusRoot, String pid, Path outDir, Catalogs cat, Path outRaw) {
        Path proj = corpusRoot.resolve("projects").resolve(pid);
        Bundle bundle = Bundle.load(proj.resolve("evidence"));
        Ingest.Result ingested = Ingest.ingest(bundle);
        // Findings inform, never abort; hash what the source signed, not the trust-corrected copy.
        Map<String, Integrity.Result> streams =
                Integrity.verifyStreams(ingested.rawAccepted, bundle.manifest, bundle.root);
        DomainBinding domain = DomainBinding.load(proj.resolve("domain.linkml.yaml"));
        Profile profile = Profile.load(proj.resolve("applicability.yaml"));
        ObjectNode coverage = Coverage.computeCoverage(ingested.accepted, profile, bundle.members);
        Applicability.resolve(profile, ingested.accepted, List.of());
        List<Assertions.Assertion> assertions = Assess.assessSubjects(
                ingested.accepted, profile, cat.catalogs, domain, ingested.withheld,
                Integrity.integrityCaps(ingested.rawAccepted, ingested.accepted, streams), coverage);
        var activity = Activity.summarizeActivity(ingested.accepted, profile);
        var blindSpots = BlindSpots.computeBlindSpots(assertions, profile, cat.catalogs, ingested.accepted);
        Runnable write = () -> Report.writeReport(
                outDir, assertions, bundle.digest(), cat.labels, ECS_OPERATOR, List.of("conformance", pid), List.of(),
                "en", cat.catalogs, activity, blindSpots);
        // Under the user's --out a report write that fails is refused under OUT_DIR, as assess --out
        // refuses it (18.184d).
        if (outRaw != null) {
            Outputs.writingInto(outRaw.toString(), Outputs.OUT_DIR, write::run);
        } else {
            write.run();
        }
    }

    /** Python's {@code Path.resolve()} (non-strict): absolute, with every symbolic link in the part of
     * the path that exists resolved and the rest appended as given, normalised. Nothing is normalised
     * before the links are resolved, so {@code lnk/..} is the parent of the link's target (18.182). */
    static Path pyResolve(Path path) {
        Path absolute = path.toAbsolutePath();
        Path existing = absolute;
        while (existing != null && !Files.exists(existing)) {
            existing = existing.getParent();
        }
        if (existing == null) {
            return absolute;
        }
        try {
            return existing.toRealPath().resolve(existing.relativize(absolute)).normalize();
        } catch (IOException e) {
            return absolute;
        }
    }

    /** The adapters' own orchestrator, given the arguments after {@code python conformance.py}; returns
     * its stdout and stderr. A seam so a test can stand in for {@code uv}. */
    @FunctionalInterface
    interface Orchestrator {
        String[] run(Path adaptersDir, List<String> args) throws IOException, InterruptedException;
    }

    /** Python's {@code _adapter_conformance} subprocess: {@code uv run --quiet python conformance.py ...}
     * in the adapters directory, its stdout and stderr captured as UTF-8 text. */
    static final Orchestrator UV_ORCHESTRATOR = (adaptersDir, args) -> {
        List<String> cmd = new ArrayList<>(List.of("uv", "run", "--quiet", "python", "conformance.py"));
        cmd.addAll(args);
        Path stderrFile = Files.createTempFile("agentce-adapters-", ".err");
        try {
            Process proc = new ProcessBuilder(cmd)
                    .directory(adaptersDir.toFile())
                    .redirectError(stderrFile.toFile())
                    .redirectInput(ProcessBuilder.Redirect.PIPE)
                    .start();
            proc.getOutputStream().close();
            // strict, as Python's subprocess.run(text=True): bytes that are not UTF-8 raise, never U+FFFD
            String stdout = Verify.decodeStrict(proc.getInputStream().readAllBytes()).toString();
            proc.waitFor();
            return new String[] {stdout, Files.readString(stderrFile, StandardCharsets.UTF_8)};
        } finally {
            Files.deleteIfExists(stderrFile);
        }
    };

    /** Run adapter conformance in the adapters directory's own environments (SPEC 11.5, 12.3), as Python's
     * {@code _adapter_conformance}: the adapters' orchestrator with {@code --json}, and {@code --out} the
     * resolved output directory when there is one. Stdout that is not JSON becomes the error record (no
     * adapters, nothing identical, the last 800 characters of stderr, or of stdout when stderr is empty). */
    static JsonNode adapterConformance(Path adaptersDir, Path outDir, Orchestrator orchestrator) {
        List<String> args = new ArrayList<>(List.of("--json"));
        if (outDir != null) {
            args.add("--out");
            args.add(pyResolve(outDir).toString());
        }
        String[] streams;
        try {
            streams = orchestrator.run(adaptersDir, args);
        } catch (IOException e) {
            // Python's subprocess.run raises FileNotFoundError when uv is missing: an unexpected error.
            throw new IllegalStateException("cannot run the adapters' conformance: " + e.getMessage(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted running the adapters' conformance", e);
        }
        try {
            return Json.parse(streams[0]);
        } catch (IllegalArgumentException notJson) {
            String text = pyStrip(streams[1].isEmpty() ? streams[0] : streams[1]);
            int cps = text.codePointCount(0, text.length());
            String tail = cps > 800 ? text.substring(text.offsetByCodePoints(0, cps - 800)) : text;
            ObjectNode error = Json.nodes().objectNode();
            error.putArray("adapters");
            error.put("total", 0);
            error.put("identical", 0);
            error.put("round_trip", false);
            error.put("error", tail);
            return error;
        }
    }

    /** Python's {@code str.strip()}: trims exactly {@link FailOn#isPySpace} code points, where
     * {@link String#strip()} uses the JVM's own whitespace set. */
    static String pyStrip(String text) {
        int start = 0;
        int end = text.length();
        while (start < end && FailOn.isPySpace(text.codePointAt(start))) {
            start += Character.charCount(text.codePointAt(start));
        }
        while (end > start && FailOn.isPySpace(text.codePointBefore(end))) {
            end -= Character.charCount(text.codePointBefore(end));
        }
        return text.substring(start, end);
    }

    /** Python's {@code int()} over a JSON value: an integer at full size, a float truncated (an
     * infinite one refused), a bool as 0 or 1, a string by {@code int(str)}'s grammar; anything else,
     * {@code null} included, refused as Python raises. */
    static BigInteger pyInt(JsonNode value) {
        if (value.isIntegralNumber()) {
            return value.bigIntegerValue();
        }
        if (value.isNumber()) {
            double d = value.doubleValue();
            if (Double.isInfinite(d)) {
                throw new IllegalStateException("cannot convert float " + value + " to integer");
            }
            return new BigDecimal(d).toBigInteger();
        }
        if (value.isBoolean()) {
            return value.booleanValue() ? BigInteger.ONE : BigInteger.ZERO;
        }
        if (value.isTextual()) {
            JsonNode parsed = OtelGenai.parsePythonIntGrammar(value.textValue());
            if (parsed != null) {
                return parsed.bigIntegerValue();
            }
            throw new IllegalStateException("invalid literal for int(): " + value);
        }
        throw new IllegalStateException("int() argument must be a string or a number, not " + value.getNodeType());
    }

    /** Python's {@code _adapter_claim}: the adapter-conformance claim, mirroring the ECS claim scheme
     * (SPEC §11.5). */
    static String adapterClaim(JsonNode detail) {
        if (!detail.isObject()) {
            throw new IllegalStateException("the adapters' conformance printed JSON that is not an object");
        }
        BigInteger total = detail.has("total") ? pyInt(detail.get("total")) : BigInteger.ZERO;
        BigInteger identical = detail.has("identical") ? pyInt(detail.get("identical")) : BigInteger.ZERO;
        if (total.signum() > 0 && identical.equals(total) && Readiness.pyTruthy(detail.get("round_trip"))) {
            return "full";
        }
        if (identical.signum() > 0) {
            return "partial";
        }
        return "none";
    }

    /** Run every corpus project through the engine and return the implementation report. */
    public static ObjectNode runEcs(Path enginePath, Path corpusDir, Path outDir) {
        return runEcs(enginePath, corpusDir, outDir, null, UV_ORCHESTRATOR);
    }

    /** {@link #runEcs(Path, Path, Path)}; with {@code adaptersDir}, adapter conformance (byte identity and
     * round trip over every adapter's fixtures) is run too and folded into the report under {@code
     * adapter_conformance}, its claim under {@code adapters} (SPEC 11.5, 12.3). */
    public static ObjectNode runEcs(Path enginePath, Path corpusDir, Path outDir, Path adaptersDir) {
        return runEcs(enginePath, corpusDir, outDir, adaptersDir, UV_ORCHESTRATOR);
    }

    static ObjectNode runEcs(
            Path enginePath, Path corpusDir, Path outDir, Path adaptersDir, Orchestrator orchestrator) {
        // Through links, as Python's engine_path.resolve(): a symlinked engine's repository is the target's.
        Path engine = pyResolve(enginePath);
        Path repoRoot = engine.getParent().getParent();
        Catalogs cat = catalogsFor(repoRoot);
        Corpus corpus = materialiseCorpus(pyResolve(corpusDir));
        Path reportsRoot;
        try {
            reportsRoot = outDir != null ? outDir.resolve("projects") : Files.createTempDirectory("agentce-ecs-");
        } catch (IOException e) {
            throw new IllegalStateException("cannot create the reports directory: " + e.getMessage(), e);
        }

        JsonNode manifest = readManifest(corpus.root.resolve("corpus-manifest.json"), corpusDir);
        List<String> projectIds = new ArrayList<>();
        for (JsonNode p : manifest.path("projects")) {
            projectIds.add(p.get("id").asText());
        }
        projectIds.sort(Json::byteCompare);

        int identical = 0;
        ArrayNode failures = Json.nodes().arrayNode();
        for (String pid : projectIds) {
            try {
                assessProject(corpus.root, pid, reportsRoot.resolve(pid), cat, outDir);
                identical += 1; // self-golden: the reference regenerates its own golden (SPEC §11.7)
            } catch (RuntimeException exc) {
                if (exc instanceof InputError input && input.key.equals(Outputs.OUT_DIR.key())) {
                    throw input; // --out cannot be written: the run is refused, not one project
                }
                ObjectNode failure = failures.addObject();
                failure.put("project", pid);
                failure.put("error", exc.getClass().getSimpleName() + ": " + exc.getMessage());
            }
        }

        // The running engine's own bundled dependency set, as `agentce version --json` and Python's
        // conformance run report it: never a lock file under whatever path --engine names (18.179).
        NoMl.InstalledResult scan = NoMl.installedSelfReport();
        int total = projectIds.size();
        int different = total - identical;
        String claim;
        if (!scan.result.equals("pass")) {
            claim = "none";
        } else if (different == 0 && total > 0) {
            claim = "full";
        } else if (identical > 0) {
            claim = "partial";
        } else {
            claim = "none";
        }

        ObjectNode report = Json.nodes().objectNode();
        ObjectNode engineNode = report.putObject("engine");
        engineNode.put("impl", Version.ENGINE_NAME);
        engineNode.put("version", Version.ENGINE_VERSION);
        report.put("spec_version", Version.SPEC_VERSION);
        report.put("corpus_version", manifest.has("corpus_version") ? manifest.get("corpus_version").asText() : "unknown");
        ObjectNode projectsNode = report.putObject("projects");
        projectsNode.put("total", total);
        projectsNode.put("identical", identical);
        projectsNode.put("different", different);
        report.put("no_ml", scan.result);
        report.put("claim", claim);
        report.set("failures", failures);
        if (adaptersDir != null) {
            JsonNode detail = adapterConformance(adaptersDir, outDir, orchestrator);
            report.set("adapter_conformance", detail);
            report.put("adapters", adapterClaim(detail));
        }

        if (outDir != null) {
            Outputs.writingInto(outDir.toString(), Outputs.OUT_DIR, () -> {
                Files.createDirectories(outDir);
                Outputs.writeFile(outDir.resolve("implementation-report.json"), Json.pretty(report) + "\n");
            });
        }
        return report;
    }
}
