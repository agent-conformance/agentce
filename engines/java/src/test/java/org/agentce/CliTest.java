package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The CLI verbs {@code validate}, {@code assess}, {@code report}, and {@code quickstart} on the
 * existing ECS/report path (SPEC §8.5), and their honest refusals. Runs the real dispatcher
 * ({@link Cli#run}) end to end against the repository's own vendored quickstart project — no mocks.
 */
class CliTest {
    private static final Path REPO = TestPaths.repoRoot();
    private static final Path QUICKSTART = REPO.resolve("corpus/quickstart");
    private static final Path CATALOG_DIR = REPO.resolve("spec/catalogs/base/eu-ai-act");

    private static JsonNode runJson(String... args) {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        PrintStream original = System.out;
        System.setOut(new PrintStream(buf, true, StandardCharsets.UTF_8));
        int exit;
        try {
            String[] withJson = new String[args.length + 1];
            System.arraycopy(args, 0, withJson, 0, args.length);
            withJson[args.length] = "--json";
            exit = Cli.run(withJson);
        } finally {
            System.setOut(original);
        }
        JsonNode envelope = Json.parse(buf.toString(StandardCharsets.UTF_8));
        assertEquals(exit, envelope.get("exit_code").asInt(), "process exit code must match the envelope");
        return envelope;
    }

    @Test
    void assessRunsTheFullPipelineOverTheVendoredQuickstartProject(@TempDir Path out) {
        JsonNode env = runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out.toString());
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals(49, env.get("assertions").asInt());
        assertEquals("incomplete", env.get("summary").get("verdict").asText());
        assertTrue(Files.isRegularFile(out.resolve("assertions.json")));
        assertTrue(Files.isRegularFile(out.resolve("manifest.json")));
        assertTrue(Files.isRegularFile(out.resolve("coverage.json")));
        assertTrue(Files.isRegularFile(out.resolve("integrity.jsonl")));
        assertTrue(Files.isRegularFile(out.resolve("applicability.jsonl")));
        assertTrue(Files.isRegularFile(out.resolve("quarantine.jsonl")));
        // the bundle path never appears in the manifest's recorded invocation (SPEC §8.4)
        JsonNode manifest = Json.parseFile(out.resolve("manifest.json"));
        for (JsonNode arg : manifest.get("run").get("invocation")) {
            assertTrue(!arg.asText().contains(System.getProperty("user.home")) || arg.asText().startsWith("~/"));
        }
    }

    @Test
    void quickstartAssessesTheBundledProjectOffline(@TempDir Path out) {
        JsonNode env = runJson("quickstart", "--out", out.toString());
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals("ok", env.get("quickstart").asText());
        assertEquals(49, env.get("assertions").asInt());
        assertTrue(Files.isRegularFile(out.resolve("report.md")));
        assertTrue(Files.isRegularFile(out.resolve("report.html")));
    }

    @Test
    void validateReportsAcceptedAndQuarantinedEvents(@TempDir Path out) {
        JsonNode env = runJson(
                "validate", "--bundle", QUICKSTART.resolve("evidence").toString(), "--out", out.toString());
        assertEquals(1, env.get("exit_code").asInt()); // FINDINGS: this bundle has quarantined events
        assertTrue(env.get("accepted").asInt() > 0);
        assertTrue(env.get("quarantined").asInt() > 0);
        assertTrue(Files.isRegularFile(out.resolve("quarantine.jsonl")));
    }

    @Test
    void reportRoundTripsEveryFormatFromACommittedAssertionsFile(@TempDir Path out) {
        runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out.toString());
        Path assertionsFile = out.resolve("assertions.json");
        for (String format : new String[] {"md", "html", "oscal", "sarif", "pack"}) {
            JsonNode env = runJson("report", "--from", assertionsFile.toString(), "--format", format);
            assertEquals(0, env.get("exit_code").asInt(), "format " + format);
            assertTrue(env.get("rendering").asText().length() > 0, "format " + format);
        }
    }

    @Test
    void reportRefusesValidateAsNotYetPorted() {
        JsonNode env = runJson("report", "--from", "assertions.json", "--validate");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.report_validate_unsupported", env.get("error").get("message_key").asText());
    }

    @Test
    void reportRefusesAnUnknownFormatByName() throws IOException {
        Path tmp = Files.createTempFile("assertions", ".json");
        Files.writeString(tmp, "[]");
        JsonNode env = runJson("report", "--from", tmp.toString(), "--format", "xml");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.report_format", env.get("error").get("message_key").asText());
    }

    @Test
    void assessRefusesAnUnresolvedCatalogByName(@TempDir Path out) {
        JsonNode env = runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--catalog", "does-not-exist@9.9",
                "--out", out.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.catalog_unresolved", env.get("error").get("message_key").asText());
    }

    @Test
    void assessResolvesAVendoredCatalogByIdWithNoCatalogDir(@TempDir Path out) {
        JsonNode env = runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--catalog", "eu-ai-act@2026.09",
                "--out", out.toString());
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals(49, env.get("assertions").asInt());
    }

    @Test
    void assessRequiresAnExistingBundleDirectory(@TempDir Path out) {
        JsonNode env = runJson(
                "assess",
                "--bundle", out.resolve("nowhere").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--out", out.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.bundle_not_a_directory", env.get("error").get("message_key").asText());
    }

    @Test
    void assessRequiresAnExistingProfileFile(@TempDir Path out) {
        JsonNode env = runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", out.resolve("nowhere.yaml").toString(),
                "--out", out.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.profile_not_a_file", env.get("error").get("message_key").asText());
    }

    @Test
    void assessWithIncrementalStateSupersedesThePriorReportOnASecondRunWithNewEvidence(@TempDir Path root)
            throws IOException {
        Path state = root.resolve("state");
        Path out1 = root.resolve("out1");
        JsonNode first = runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out1.toString(),
                "--state", state.toString());
        assertEquals(0, first.get("exit_code").asInt());
        assertTrue(first.get("supersedes").isArray());
        assertEquals(0, first.get("supersedes").size());
        assertTrue(Files.isRegularFile(state.resolve("state.json")));

        // Re-running over the identical bundle digest is a no-op (SPEC §8.2 #8): nothing superseded.
        Path out2 = root.resolve("out2");
        JsonNode second = runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out2.toString(),
                "--state", state.toString());
        assertEquals(0, second.get("supersedes").size());

        // A state directory at an incompatible version refuses with a named migration command (exit 3).
        Files.writeString(state.resolve("state.json"), "{\"state_version\": 99}");
        JsonNode third = runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", root.resolve("out3").toString(),
                "--state", state.toString());
        assertEquals(3, third.get("exit_code").asInt());
        assertEquals("input.state_version_incompatible", third.get("error").get("message_key").asText());
    }

    @Test
    void anUnknownVerbReturnsTheStableNotImplementedEnvelope() {
        JsonNode env = runJson("frobnicate");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("cli.not_implemented", env.get("error").get("message_key").asText());
        assertEquals("frobnicate", env.get("error").get("command").asText());
    }
}
