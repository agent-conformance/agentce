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
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
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

    /** The quickstart profile with its {@code catalogs:} list removed, written into {@code dir}. */
    private static Path profileWithoutCatalogs(Path dir) throws IOException {
        List<String> kept = new ArrayList<>();
        boolean inCatalogs = false;
        for (String line : Files.readAllLines(QUICKSTART.resolve("applicability.yaml"))) {
            if (line.equals("catalogs:")) {
                inCatalogs = true;
            } else if (!(inCatalogs && line.startsWith("  - "))) {
                inCatalogs = false;
                kept.add(line);
            }
        }
        Path path = dir.resolve("no-catalog.yaml");
        Files.write(path, kept);
        return path;
    }

    private static List<String> manifestCatalogs(Path out) throws IOException {
        List<String> labels = new ArrayList<>();
        for (JsonNode c : Json.parse(Files.readString(out.resolve("manifest.json"))).get("inputs").get("catalogs")) {
            labels.add(c.get("id").asText() + "@" + c.get("version").asText());
        }
        return labels;
    }

    @Test
    void assessNamingNoCatalogEvaluatesTheBaselineAndAnEmptyCatalogOptionIsRefused(@TempDir Path dir)
            throws IOException {
        Path profile = profileWithoutCatalogs(dir);
        String[] common = {
            "assess",
            "--bundle", QUICKSTART.resolve("evidence").toString(),
            "--profile", profile.toString(),
        };
        JsonNode byDefault = runJson(concat(common, "--out", dir.resolve("default").toString()));
        assertTrue(byDefault.get("exit_code").asInt() <= 1);
        assertEquals(List.of("baseline@2026.09"), manifestCatalogs(dir.resolve("default")));
        JsonNode explicit = runJson(
                concat(common, "--catalog", "eu-ai-act@2026.09", "--out", dir.resolve("eu").toString()));
        assertTrue(explicit.get("exit_code").asInt() <= 1);
        assertEquals(List.of("eu-ai-act@2026.09"), manifestCatalogs(dir.resolve("eu")));
        JsonNode empty = runJson(concat(common, "--catalog", ",", "--out", dir.resolve("none").toString()));
        assertEquals(3, empty.get("exit_code").asInt());
        assertEquals("input.catalog_missing", empty.get("error").get("message_key").asText());
    }

    private static String[] concat(String[] head, String... tail) {
        String[] all = new String[head.length + tail.length];
        System.arraycopy(head, 0, all, 0, head.length);
        System.arraycopy(tail, 0, all, head.length, tail.length);
        return all;
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

    // --- `version` (item 18.22): the same structured `--json` envelope every other command returns,
    // with a real installed-artifact no_ml self-report; distinct from the bare `--version`/`-V` flag,
    // which stays a plain one-line shortcut. ---

    private static String captureStdout(String... args) {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        PrintStream original = System.out;
        System.setOut(new PrintStream(buf, true, StandardCharsets.UTF_8));
        try {
            Cli.run(args);
        } finally {
            System.setOut(original);
        }
        return buf.toString(StandardCharsets.UTF_8);
    }

    @Test
    void versionJsonEnvelopeMatchesTheOtherCommandsShape() {
        JsonNode env = runJson("version");
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals("agentce-java", env.get("engine").asText());
        assertEquals(Version.ENGINE_VERSION, env.get("engine_version").asText());
        assertEquals(Version.SPEC_VERSION, env.get("spec_version").asText());
        assertTrue(env.get("supported_catalogs").isArray());
        assertEquals("pass", env.get("no_ml").asText());
        assertEquals("pass", env.get("no_ml_detail").get("result").asText());
        assertTrue(env.get("no_ml_detail").get("denylisted_present").isEmpty());
    }

    @Test
    void versionPlainTextPrintsTwoLinesDistinctFromTheBareVersionFlag() {
        String out = captureStdout("version");
        assertEquals(
                "agentce-java " + Version.ENGINE_VERSION + " (spec " + Version.SPEC_VERSION + ")\nno_ml: pass\n",
                out);
    }

    @Test
    void bareVersionFlagStaysAOneLinePlainTextShortcutUntouchedByTheVersionSubcommand() {
        assertEquals("agentce " + Version.ENGINE_VERSION + "\n", captureStdout("--version"));
        assertEquals("agentce " + Version.ENGINE_VERSION + "\n", captureStdout("-V"));
    }

    @Test
    void digestTreeVerbPrintsTheDirectorysRealContentDigest() {
        Path fixture = REPO.resolve("spec/model/test-vectors/digest-tree");
        String expected = Catalog.digestTree(fixture, Set.of("catalog.sig.json", "catalog.yaml"));
        assertEquals(expected + "\n", captureStdout("digest-tree", fixture.toString()));
    }

    @Test
    void securityViewVerbRunsTheFixtureAndPrintsAStandardsCitationsArray() {
        Path fixture = REPO.resolve("verification/gates/fixtures/security_view/activity_and_assertions.json");
        String out = captureStdout("security-view", fixture.toString());
        JsonNode citations = Json.parse(out).get("standards_citations");
        assertTrue(citations.isArray());
        Set<String> frameworks = new HashSet<>();
        for (JsonNode citation : citations) {
            frameworks.add(citation.get("framework").asText());
        }
        assertEquals(Set.of("owasp-asi-2026", "mitre-atlas", "owasp-acs"), frameworks);
    }

    @Test
    void anUnknownVerbReturnsTheStableNotImplementedEnvelope() {
        JsonNode env = runJson("frobnicate");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("cli.not_implemented", env.get("error").get("message_key").asText());
        assertEquals("frobnicate", env.get("error").get("command").asText());
    }

    // --- `diff` (item 18.24): a real, deterministic assertion-set diff, matching the Python
    // reference's cmd_diff byte for byte. Mirrors cli.test.ts's diff block. ---

    private static Path diffFixture(Path dir, String name, String recordsJson) {
        try {
            Path path = dir.resolve(name);
            Files.writeString(path, recordsJson);
            return path;
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    @Test
    void diffJsonEnvelopeCarriesReportAReportBChangedDiffWhatChangedExitOneOnARealChange(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[{\"control\":\"C-01\",\"subject\":\"s1\",\"outcome\":\"non-conformant\"}]");
        Path b = diffFixture(dir, "b.json", "[{\"control\":\"C-01\",\"subject\":\"s1\",\"outcome\":\"conformant\"}]");
        JsonNode env = runJson("diff", a.toString(), b.toString());
        assertEquals(1, env.get("exit_code").asInt());
        assertEquals(a.toString(), env.get("report_a").asText());
        assertEquals(b.toString(), env.get("report_b").asText());
        assertEquals(1, env.get("changed").asInt());
        assertEquals(1, env.get("diff").size());
        JsonNode change = env.get("diff").get(0);
        assertEquals("C-01", change.get("control").asText());
        assertEquals("s1", change.get("subject").asText());
        assertEquals("non-conformant", change.get("from").asText());
        assertEquals("conformant", change.get("to").asText());
        assertEquals(1, env.get("what_changed").get("closed").size());
        assertTrue(env.get("what_changed").get("opened").isEmpty());
        assertTrue(env.get("what_changed").get("other").isEmpty());
    }

    @Test
    void diffIdenticalInputsExitZeroTextNoDifferencesNoTrailingPeriod(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[{\"control\":\"C-01\",\"subject\":\"s1\",\"outcome\":\"conformant\"}]");
        String out = captureStdout("diff", a.toString(), a.toString());
        assertEquals("no differences\n", out);
    }

    @Test
    void diffFormatMdIdenticalInputsTheFixedThreeLineSectionNoDifferencesWithAPeriod(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[{\"control\":\"C-01\",\"subject\":\"s1\",\"outcome\":\"conformant\"}]");
        String out = captureStdout("diff", a.toString(), a.toString(), "--format", "md");
        assertEquals("## What changed\n\nno differences.\n", out);
    }

    @Test
    void diffFormatJsonWithoutJsonOneNoteLineByteEqualToTheSortedKeysEnvelopeData(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[]");
        Path b = diffFixture(dir, "b.json", "[{\"control\":\"C-01\",\"subject\":\"s1\",\"outcome\":\"conformant\"}]");
        String out = captureStdout("diff", a.toString(), b.toString(), "--format", "json");
        JsonNode parsed = Json.parse(out); // the whole stdout is one println of a multi-line pretty-printed JSON note
        assertEquals(1, parsed.get("changed").asInt());
        assertEquals(a.toString(), parsed.get("report_a").asText());
        assertTrue(!parsed.has("command"), "note-rendered JSON is result.data only, never the full envelope");
    }

    @Test
    void diffMissingReportBGivesInputReportBMissingWithTheDiffSpecificFixTextExitThree(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[]");
        JsonNode env = runJson("diff", a.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.report_b_missing", env.get("error").get("message_key").asText());
        assertEquals("pass two assertion files: `agentce diff <report-a> <report-b>`.", env.get("error").get("fix").asText());
    }

    @Test
    void diffAMalformedInputFileGivesAKeyedInternalUnexpectedResultNeverACrash(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "not json");
        Path b = diffFixture(dir, "b.json", "[]");
        JsonNode env = runJson("diff", a.toString(), b.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("internal.unexpected", env.get("error").get("message_key").asText());
        assertEquals(
                "re-run with --debug to see the stack trace, then file an issue.",
                env.get("error").get("fix").asText());
    }

    @Test
    void diffDebugOnAMalformedInputFileRethrowsInsteadOfReturningAKeyedEnvelope(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "not json");
        Path b = diffFixture(dir, "b.json", "[]");
        org.junit.jupiter.api.Assertions.assertThrows(
                RuntimeException.class, () -> Cli.run(new String[] {"diff", a.toString(), b.toString(), "--debug"}));
    }

    @Test
    void diffAnExtraPositionalArgumentGivesInputDiffExtraArgument(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[]");
        Path b = diffFixture(dir, "b.json", "[]");
        Path c = diffFixture(dir, "c.json", "[]");
        JsonNode env = runJson("diff", a.toString(), b.toString(), c.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.diff_extra_argument", env.get("error").get("message_key").asText());
    }

    @Test
    void diffAnUnrecognizedFlagGivesInputDiffUnrecognizedFlagNeverASilentPositionalRead(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[]");
        Path b = diffFixture(dir, "b.json", "[]");
        JsonNode env = runJson("diff", a.toString(), b.toString(), "--forma", "text");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.diff_unrecognized_flag", env.get("error").get("message_key").asText());
    }

    @Test
    void diffDebugAndQuietAreAcceptedAndSilentlyIgnoredMatchingEveryOtherCommand(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[]");
        String out = captureStdout("diff", a.toString(), a.toString(), "--debug", "--quiet");
        assertEquals("no differences\n", out);
    }

    @Test
    void diffFormatOutsideTextJsonMdGivesInputDiffFormatWithTheExactPythonFixText(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[]");
        Path b = diffFixture(dir, "b.json", "[]");
        JsonNode env = runJson("diff", a.toString(), b.toString(), "--format", "yaml");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.diff_format", env.get("error").get("message_key").asText());
        assertEquals("pass --format text|json|md.", env.get("error").get("fix").asText());
    }

    @Test
    void diffTheEchoedReportAReportBPathKeepsDotDotUnchanged(@TempDir Path dir) throws IOException {
        diffFixture(dir, "a.json", "[]");
        Files.createDirectory(dir.resolve("sub"));
        String raw = dir + "/sub/../a.json"; // a real, existing file via '..'; the literal segment must survive the echo
        Path b = diffFixture(dir, "b.json", "[]");
        JsonNode env = runJson("diff", raw, b.toString());
        assertTrue(env.get("report_a").asText().contains("sub/../a.json"), env.get("report_a").asText());
    }

    @Test
    void diffJsonEscapesNonAsciiContentExactlyLikePythonsEnsureAsciiTrue(@TempDir Path dir) {
        Path a = diffFixture(dir, "a.json", "[]");
        Path b = diffFixture(dir, "b.json", "[{\"control\":\"cé\",\"subject\":\"😀\",\"outcome\":\"conformant\"}]");
        String raw = captureStdout("diff", a.toString(), b.toString(), "--json");
        assertTrue(raw.contains("c\\u00e9"), raw);
        assertTrue(raw.contains("\\ud83d\\ude00"), raw);
    }

    // --- `readiness` (item 18.25): the report-readiness verdict, matching `readiness.test.ts`'s own
    // CLI-wiring subset. ---

    private static Path readinessReport(Path dir, String assertionsJson, String integrityJsonl) throws IOException {
        Path reportDir = dir.resolve("report");
        Files.createDirectory(reportDir);
        Files.writeString(reportDir.resolve("assertions.json"), assertionsJson);
        Files.writeString(reportDir.resolve("integrity.jsonl"), integrityJsonl);
        Files.writeString(reportDir.resolve("coverage.json"), "{\"subjects\":{}}");
        Files.writeString(reportDir.resolve("applicability.jsonl"), "");
        return reportDir;
    }

    private static Path readinessReport(Path dir) throws IOException {
        return readinessReport(dir, "[]", "");
    }

    @Test
    void readinessACleanReportIsReadyExitZeroAndWritesARealReportReadinessMdFile(@TempDir Path dir) throws IOException {
        Path report = readinessReport(
                dir,
                "[{\"control\":\"OVS-03\",\"outcome\":\"conformant\",\"subject\":\"s\"}]",
                "{\"status\":\"verified\",\"stream\":\"a\"}\n");
        JsonNode env = runJson("readiness", report.toString(), "--catalog-dir", CATALOG_DIR.toString());
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals("READY", env.get("verdict").asText());
        assertEquals(0, env.get("reasons").size());
        String reportPath = env.get("report").asText();
        assertTrue(reportPath.contains("report-readiness-"), reportPath);
        String written = Files.readString(Path.of(reportPath));
        assertTrue(written.startsWith("# Report readiness — READY\n"), written);
    }

    @Test
    void readinessBrokenIntegrityIsNotReadyExitOneBlockingReasonsSectionWritten(@TempDir Path dir) throws IOException {
        Path report = readinessReport(dir, "[]", "{\"status\":\"failed\",\"stream\":\"gw\"}\n");
        JsonNode env = runJson("readiness", report.toString(), "--catalog-dir", CATALOG_DIR.toString());
        assertEquals(1, env.get("exit_code").asInt());
        assertEquals("NOT READY", env.get("verdict").asText());
        String written = Files.readString(Path.of(env.get("report").asText()));
        assertTrue(written.contains("## Blocking reasons"), written);
        assertTrue(written.contains("- integrity failed on stream gw"), written);
    }

    @Test
    void readinessAHighSeverityInsufficientEvidenceRecordedInGapsIsReadyWithLimitations(@TempDir Path dir)
            throws IOException {
        Path report = readinessReport(
                dir, "[{\"control\":\"OVS-03\",\"outcome\":\"insufficient_evidence\",\"subject\":\"s\"}]", "");
        Path gaps = dir.resolve("gaps.md");
        Files.writeString(gaps, "OVS-03 owned by alice on 2026-02-01\n");
        Path deviations = dir.resolve("deviations.yaml");
        Files.writeString(deviations, "deviations: []\n");
        JsonNode env = runJson(
                "readiness",
                report.toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--gaps", gaps.toString(),
                "--deviations", deviations.toString());
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals("READY WITH LIMITATIONS", env.get("verdict").asText());
    }

    @Test
    void readinessMissingReportDirGivesInputReportDirMissingWithTheReadinessSpecificFixText() {
        JsonNode env = runJson("readiness");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.report_dir_missing", env.get("error").get("message_key").asText());
        assertEquals(
                "pass the report directory: `agentce readiness <report-dir>`.",
                env.get("error").get("fix").asText());
    }

    @Test
    void readinessAReportDirThatIsNotADirectoryGivesInputReportDirNotADirectory(@TempDir Path dir) {
        Path notADir = dir.resolve("nope");
        JsonNode env = runJson("readiness", notADir.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.report_dir_not_a_directory", env.get("error").get("message_key").asText());
    }

    @Test
    void readinessAGivenButBadGapsPathIsInputGapsNotAFile(@TempDir Path dir) throws IOException {
        Path report = readinessReport(dir);
        JsonNode env = runJson(
                "readiness",
                report.toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--gaps", dir.resolve("missing.md").toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.gaps_not_a_file", env.get("error").get("message_key").asText());
    }

    @Test
    void readinessAGivenButBadDeviationsPathIsInputDeviationsNotAFile(@TempDir Path dir) throws IOException {
        Path report = readinessReport(dir);
        JsonNode env = runJson(
                "readiness",
                report.toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--deviations", dir.resolve("missing.yaml").toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.deviations_not_a_file", env.get("error").get("message_key").asText());
    }

    @Test
    void readinessABadCatalogDirIsInputCatalogDirNotADirectory(@TempDir Path dir) throws IOException {
        Path report = readinessReport(dir);
        JsonNode env = runJson(
                "readiness", report.toString(), "--catalog-dir", dir.resolve("no-such-catalog").toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.catalog-dir_not_a_directory", env.get("error").get("message_key").asText());
    }

    @Test
    void readinessAMalformedDeviationRegisterGivesInputDeviationInvalidNeverACrash(@TempDir Path dir)
            throws IOException {
        Path report = readinessReport(dir);
        Path deviations = dir.resolve("deviations.yaml");
        Files.writeString(deviations, "deviations: not-a-list\n");
        JsonNode env = runJson(
                "readiness",
                report.toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--deviations", deviations.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.deviation_invalid", env.get("error").get("message_key").asText());
    }

    @Test
    void readinessWithNoCatalogDirEveryVendoredBaseCatalogIsUsed(@TempDir Path dir) throws IOException {
        Path report = readinessReport(dir, "[{\"control\":\"OVS-03\",\"outcome\":\"conformant\",\"subject\":\"s\"}]", "");
        JsonNode env = runJson("readiness", report.toString());
        assertTrue(env.get("exit_code").asInt() == 0 || env.get("exit_code").asInt() == 1);
        assertTrue(env.get("verdict").isTextual());
    }
}
