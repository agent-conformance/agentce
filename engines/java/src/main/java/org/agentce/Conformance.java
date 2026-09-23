package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
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

    private static Corpus materialiseCorpus(Path corpusDir) {
        if (Files.isRegularFile(corpusDir.resolve("corpus-manifest.json"))) {
            return new Corpus(corpusDir);
        }
        Path generator = corpusDir.resolve("generator/generate.py");
        if (Files.isRegularFile(generator)) {
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

    private static void assessProject(Path corpusRoot, String pid, Path outDir, Catalogs cat) {
        Path proj = corpusRoot.resolve("projects").resolve(pid);
        Bundle bundle = Bundle.load(proj.resolve("evidence"));
        Ingest.Result ingested = Ingest.ingest(bundle);
        Integrity.verifyBundle(ingested.accepted, bundle.manifest, bundle.root); // findings inform, never abort
        DomainBinding domain = DomainBinding.load(proj.resolve("domain.linkml.yaml"));
        Profile profile = Profile.load(proj.resolve("applicability.yaml"));
        Coverage.computeCoverage(ingested.accepted, profile, bundle.root);
        Applicability.resolve(profile, ingested.accepted, List.of());
        List<Assertions.Assertion> assertions = Assess.assessSubjects(ingested.accepted, profile, cat.catalogs, domain);
        Report.writeReport(
                outDir, assertions, bundle.digest, cat.labels, ECS_OPERATOR, List.of("conformance", pid), List.of(),
                "en", cat.catalogs);
    }

    /** Run every corpus project through the engine and return the implementation report. */
    public static ObjectNode runEcs(Path enginePath, Path corpusDir, Path outDir) {
        Path engine = enginePath.toAbsolutePath().normalize();
        Path repoRoot = engine.getParent().getParent();
        Catalogs cat = catalogsFor(repoRoot);
        Corpus corpus = materialiseCorpus(corpusDir.toAbsolutePath().normalize());
        Path reportsRoot;
        try {
            reportsRoot = outDir != null ? outDir.resolve("projects") : Files.createTempDirectory("agentce-ecs-");
        } catch (IOException e) {
            throw new IllegalStateException("cannot create the reports directory: " + e.getMessage(), e);
        }

        JsonNode manifest = Json.parseFile(corpus.root.resolve("corpus-manifest.json"));
        List<String> projectIds = new ArrayList<>();
        JsonNode projects = manifest.get("projects");
        if (projects != null && projects.isArray()) {
            for (JsonNode p : projects) {
                projectIds.add(p.get("id").asText());
            }
        }
        projectIds.sort(Json::byteCompare);

        int identical = 0;
        ArrayNode failures = Json.nodes().arrayNode();
        for (String pid : projectIds) {
            try {
                assessProject(corpus.root, pid, reportsRoot.resolve(pid), cat);
                identical += 1; // self-golden: the reference regenerates its own golden (SPEC §11.7)
            } catch (RuntimeException exc) {
                ObjectNode failure = failures.addObject();
                failure.put("project", pid);
                failure.put("error", exc.getClass().getSimpleName() + ": " + exc.getMessage());
            }
        }

        NoMl.Result scan = NoMl.evaluate(repoRoot, engine.resolve("gradle.lockfile"));
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

        if (outDir != null) {
            try {
                Files.createDirectories(outDir);
                Files.write(outDir.resolve("implementation-report.json"),
                        (Json.pretty(report) + "\n").getBytes(StandardCharsets.UTF_8));
            } catch (IOException e) {
                throw new IllegalStateException("cannot write the implementation report: " + e.getMessage(), e);
            }
        }
        return report;
    }
}
