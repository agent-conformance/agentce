package org.agentce;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.util.stream.Stream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.Signature;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
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
        return Fixtures.runJson(args);
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
    void assessManifestCarriesTheRealProfileAndDomainDigestsThroughTheRealCli(@TempDir Path out) throws IOException {
        Path profile = QUICKSTART.resolve("applicability.yaml");
        Path domain = QUICKSTART.resolve("domain.linkml.yaml");
        runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", profile.toString(),
                "--domain", domain.toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out.toString());
        JsonNode inputs = Json.parseFile(out.resolve("manifest.json")).get("inputs");
        assertEquals(Report.digestBytes(Files.readAllBytes(profile)), inputs.get("applicability_profile_digest").asText());
        assertEquals(Report.digestBytes(Files.readAllBytes(domain)), inputs.get("domain_binding_digest").asText());
    }

    @Test
    void assessManifestHasNoDomainBindingDigestWhenDomainIsOmitted(@TempDir Path out) {
        runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out.toString());
        JsonNode inputs = Json.parseFile(out.resolve("manifest.json")).get("inputs");
        assertTrue(inputs.has("applicability_profile_digest"));
        assertFalse(inputs.has("domain_binding_digest"));
    }

    @Test
    void assessExitsTwoOnASeverityHighInsufficientEvidenceControl(@TempDir Path out) {
        // verification/gates/fixtures/audience_presets: one control (AUD-01, severity: high) the
        // fixture's evidence bundle never satisfies -- shared with the Python engine's own 18.30 tests
        // and the VG-AUDIENCE-PRESETS gate (SPEC.md:1076, SPEC Sec.8.5).
        Path fixture = REPO.resolve("verification/gates/fixtures/audience_presets");
        JsonNode env = runJson(
                "assess",
                "--bundle", fixture.resolve("evidence").toString(),
                "--profile", fixture.resolve("applicability.yaml").toString(),
                "--domain", fixture.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", fixture.resolve("catalog").toString(),
                "--allow-unverified-catalog",
                "--out", out.toString());
        assertEquals(2, env.get("exit_code").asInt());
        ArrayNode exitStatus = (ArrayNode) env.get("exit_status");
        assertEquals(1, exitStatus.size());
        assertEquals("insufficient_evidence", exitStatus.get(0).asText());
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
        for (String format : new String[] {"md", "html", "oscal", "sarif", "public", "pack"}) {
            JsonNode env = runJson("report", "--from", assertionsFile.toString(), "--format", format);
            assertEquals(0, env.get("exit_code").asInt(), "format " + format);
            assertTrue(env.get("rendering").asText().length() > 0, "format " + format);
        }
    }

    @Test
    void reportValidateRefusesAPathThatIsNotADirectory() {
        JsonNode env = runJson("report", "--validate", "/nonexistent");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.validate_not_a_directory", env.get("error").get("message_key").asText());
    }

    @Test
    void reportValidateAcceptsAGenuineAssessRunAndRejectsACorruptedOne(@TempDir Path out) throws IOException {
        runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out.toString());
        JsonNode clean = runJson("report", "--validate", out.toString());
        assertEquals(0, clean.get("exit_code").asInt(), clean.get("problems").toString());
        assertTrue(clean.get("valid").asBoolean());
        assertEquals(0, clean.get("problems").size());

        Path assertionsPath = out.resolve("assertions.json");
        ArrayNode all = (ArrayNode) Json.parse(Files.readString(assertionsPath));
        ((ObjectNode) all.get(0)).remove("control");
        Files.writeString(assertionsPath, all.toString());
        JsonNode corrupted = runJson("report", "--validate", out.toString());
        assertEquals(3, corrupted.get("exit_code").asInt());
        assertFalse(corrupted.get("valid").asBoolean());
        boolean namesAssertions = false;
        for (JsonNode p : corrupted.get("problems")) {
            if (p.asText().contains("assertions.json")) {
                namesAssertions = true;
            }
        }
        assertTrue(namesAssertions, corrupted.get("problems").toString());
    }

    @Test
    void reportValidateCatchesARealSchemaOnlyOscalViolationPastTheLocalProfile(@TempDir Path out) throws IOException {
        runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out.toString());
        Path oscalPath = out.resolve("oscal-ar.json");
        ObjectNode oscal = (ObjectNode) Json.parse(Files.readString(oscalPath));
        ((ObjectNode) oscal.get("assessment-results")).put("uuid", "not-a-uuid");
        Files.writeString(oscalPath, oscal.toString());
        JsonNode env = runJson("report", "--validate", out.toString());
        assertEquals(3, env.get("exit_code").asInt());
        boolean namesNist = false;
        for (JsonNode p : env.get("problems")) {
            if (p.asText().startsWith("oscal-ar.json (NIST OSCAL 1.1.2): ")) {
                namesNist = true;
            }
        }
        assertTrue(namesNist, env.get("problems").toString());
    }

    @Test
    void reportRefusesAnUnknownFormatByName() throws IOException {
        Path tmp = Files.createTempFile("assertions", ".json");
        Files.writeString(tmp, "[]");
        JsonNode env = runJson("report", "--from", tmp.toString(), "--format", "xml");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.report_format", env.get("error").get("message_key").asText());
        assertEquals("choose one of: md, html, oscal, sarif, public, pack.", env.get("error").get("fix").asText());
    }

    @Test
    void reportReadsRoleAndLanguageAndRefusesAnOptionTheFormatDoesNotRead() throws IOException {
        Path tmp = Files.createTempFile("assertions", ".json");
        Files.writeString(tmp, "[{\"control\":\"DAT-01\",\"control_version\":\"2026.09\",\"subject\":\"a\","
                + "\"outcome\":\"conformant\",\"rung\":2,\"mode\":\"automated\",\"window\":{\"start\":"
                + "\"2026-05-01T00:00:00Z\",\"end\":\"2026-08-29T00:00:00Z\"},\"population\":{\"applicable\":1,"
                + "\"failed\":0},\"severity\":\"high\",\"family\":\"DAT\"}]");
        String from = tmp.toString();
        JsonNode pack = Json.parse(runJson("report", "--from", from, "--format", "pack", "--role", "provider")
                .get("rendering").asText());
        pack.forEach(p -> assertEquals("provider", p.get("role").asText()));
        String de = runJson("report", "--from", from, "--language", "de").get("rendering").asText();
        String en = runJson("report", "--from", from).get("rendering").asText();
        assertFalse(de.lines().findFirst().equals(en.lines().findFirst()), de.lines().findFirst().orElse(""));
        for (String[] argv : new String[][] {
                {"--from", from, "--role", "provider"},
                {"--from", from, "--format", "md", "--catalog", "eu-ai-act"},
                {"--from", from, "--format", "oscal", "--language", "de"},
                {"--validate", tmp.getParent().toString(), "--out", "x"}}) {
            String[] full = new String[argv.length + 1];
            full[0] = "report";
            System.arraycopy(argv, 0, full, 1, argv.length);
            JsonNode env = runJson(full);
            assertEquals(3, env.get("exit_code").asInt(), String.join(" ", argv));
            assertEquals("input.report_flag_unused", env.get("error").get("message_key").asText());
        }
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

    private static final Path AUDITOR_FIXTURE = REPO.resolve("verification/gates/fixtures/auditor_view");
    private static final Path AUDITOR_REGISTER = AUDITOR_FIXTURE.resolve("deviations.yaml");
    /** The fixture register's digest, as Python records it (python-reference.md S1). */
    private static final String AUDITOR_REGISTER_DIGEST =
            "sha256:ec70a21ea9da1881e0c7737a2c760ef7fcee3aed71e92944499871f8f2f7b439";

    private static List<String> auditorAssessArgs(Path out) {
        return new ArrayList<>(List.of(
                "assess",
                "--out", out.toString(),
                "--bundle", AUDITOR_FIXTURE.resolve("evidence").toString(),
                "--profile", AUDITOR_FIXTURE.resolve("applicability.yaml").toString(),
                "--domain", AUDITOR_FIXTURE.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", AUDITOR_FIXTURE.resolve("catalog").toString(),
                "--allow-unverified-catalog"));
    }

    private static JsonNode runAssess(List<String> args, String... extra) {
        List<String> all = new ArrayList<>(args);
        all.addAll(Arrays.asList(extra));
        return runJson(all.toArray(String[]::new));
    }

    private static String outcomeOf(Path out, String control) {
        for (JsonNode a : Json.parseFile(out.resolve("assertions.json"))) {
            if (control.equals(a.get("control").asText())) {
                return a.get("outcome").asText() + (a.hasNonNull("deviation") ? " " + a.get("deviation").asText() : "");
            }
        }
        return null;
    }

    private static String registerDigest(Path out) {
        JsonNode digest = Json.parseFile(out.resolve("manifest.json")).get("inputs").get("deviation_register_digest");
        return digest == null ? null : digest.asText();
    }

    @Test
    void assessDeviationsAppliesTheRegister(@TempDir Path out) {
        JsonNode env = runAssess(auditorAssessArgs(out), "--deviations", AUDITOR_REGISTER.toString());
        assertEquals(1, env.get("exit_code").asInt()); // AUV-02's expired entry leaves it non-conformant
        assertEquals("partial AUV-01", outcomeOf(out, "AUV-01"));
        assertEquals("non-conformant", outcomeOf(out, "AUV-02"));
        assertEquals(AUDITOR_REGISTER_DIGEST, registerDigest(out));
        JsonNode limitations = Json.parseFile(out.resolve("manifest.json")).get("limitations");
        assertTrue(limitations.toString().contains(
                "AUV-02: deviation expired 2025-11-01T00:00:00.000Z; ignored and reported, the control remains non-conformant"));
        JsonNode risks = Json.parseFile(out.resolve("oscal-ar.json"))
                .get("assessment-results").get("results").get(0).get("risks");
        assertEquals(1, risks.size());
        assertEquals("Accepted pending remediation. <script>alert(1)</script>", risks.get(0).get("statement").asText());
    }

    @Test
    void assessDeviationsLintRefusesAControlOutsideTheCatalog(@TempDir Path dir) throws IOException {
        Path register = dir.resolve("reg.yaml");
        Files.writeString(register, "deviation_register_version: 1\ndeviations:\n  - control: XYZ-99\n"
                + "    rationale: r\n    compensating_control: c\n    owner: user:a@example.com\n"
                + "    approver: user:b@example.com\n    granted: \"2026-01-01T00:00:00Z\"\n"
                + "    expiry: \"2026-03-01T00:00:00Z\"\n");
        Path out = dir.resolve("report");
        JsonNode env = runAssess(auditorAssessArgs(out), "--deviations", register.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.deviation_invalid", env.get("error").get("message_key").asText());
        assertFalse(Files.exists(out.resolve("assertions.json")));
    }

    @Test
    void assessDeviationsEqualsFormAppliesLikeTheTwoTokenForm(@TempDir Path out) {
        JsonNode env = runAssess(auditorAssessArgs(out), "--deviations=" + AUDITOR_REGISTER);
        assertEquals(1, env.get("exit_code").asInt());
        assertEquals("partial AUV-01", outcomeOf(out, "AUV-01"));
        assertEquals(AUDITOR_REGISTER_DIGEST, registerDigest(out));
    }

    @Test
    void assessRefusesAnUnknownOrAbbreviatedFlagAndASecondPositional(@TempDir Path out) {
        for (String[] extra : new String[][] {
                {"--em", "md"}, {"--fo", "ci"}, {"--emitt", "md"}, {"--nonsense"}, {"x", "y"}}) {
            JsonNode env = runAssess(auditorAssessArgs(out), extra);
            assertEquals(3, env.get("exit_code").asInt(), String.join(" ", extra));
            assertEquals("input.assess_unrecognized_flag", env.get("error").get("message_key").asText());
            assertFalse(Files.exists(out.resolve("assertions.json")));
        }
    }

    private static final Path RECORDS = REPO.resolve("verification/gates/fixtures/quick_path/records");

    /** The quickstart bundle and profile, then {@code extra}: the assess line every 18.109 row builds on. */
    private static JsonNode assessQuickstart(String... extra) {
        List<String> all = new ArrayList<>(List.of(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString()));
        all.addAll(Arrays.asList(extra));
        return runJson(all.toArray(String[]::new));
    }

    /** Asserts {@code env} is the refusal {@code key} with {@code cause} (when not null), and nothing in {@code out}. */
    private static void assertRefusedNothingWritten(JsonNode env, String key, String cause, Path out) {
        assertEquals(3, env.get("exit_code").asInt(), env.toString());
        assertEquals(key, env.get("error").get("message_key").asText(), env.toString());
        if (cause != null) {
            assertEquals(cause, env.get("error").get("detail").asText());
        }
        assertFalse(Files.exists(out), out.toString());
    }

    @Test
    void assessHelpPrintsTheUsageAnywhereBeforeTheSeparatorAndWritesNothing(@TempDir Path dir) {
        Path out = dir.resolve("o");
        String bundle = QUICKSTART.resolve("evidence").toString();
        String profile = QUICKSTART.resolve("applicability.yaml").toString();
        for (String[] argv : new String[][] {
                {"assess", "-h"},
                {"assess", "--help"},
                {"assess", "--json", "--bundle", bundle, "--profile", profile, "-h", "--out", out.toString()},
                {"assess", "--bundle", bundle, "--profile", profile, "--help", "--out", out.toString()},
                {"assess", "--no-such-flag", "-h"},
                {"assess", "-h", "--no-such-flag"}}) {
            assertEquals(Cli.ASSESS_USAGE, captureStdout(argv), String.join(" ", argv));
            assertEquals(0, Cli.run(argv));
        }
        assertTrue(Cli.ASSESS_USAGE.startsWith("usage: agentce assess [-h] [--json] [--debug] [--quiet]"));
        assertFalse(Files.exists(out));
    }

    @Test
    void assessRefusesManualProbesAClusterAndAValueOnAFlag(@TempDir Path dir) {
        Path out = dir.resolve("o");
        for (String[] extra : new String[][] {
                {"--manual", "/nonexistent-agentce-dir"}, {"--probes", "x"}, {"--records", "x"}, {"--format", "md"},
                {"--ou", "o"}, {"-hx"}, {"--help=x"}, {"--json=1"}, {"--allow-unverified-catalog=1"},
                {"--package-for-sharing=1"}, {"--", "-h"}}) {
            List<String> all = new ArrayList<>(Arrays.asList(extra));
            all.addAll(List.of("--out", out.toString()));
            JsonNode env = assessQuickstart(all.toArray(String[]::new));
            assertRefusedNothingWritten(env, "input.assess_unrecognized_flag", null, out);
        }
    }

    @Test
    void assessRefusesAValueOptionWithNoValue(@TempDir Path dir) {
        Path out = dir.resolve("o");
        boolean defaultOutBefore = Files.exists(Path.of("out")); // the refusal must not create ./out
        JsonNode bare = assessQuickstart("--out");
        assertRefusedNothingWritten(bare, "input.assess_flag_needs_value", "argument --out: expected one argument", out);
        assertEquals("pass --out <value>.", bare.get("error").get("fix").asText());
        assertRefusedNothingWritten(assessQuickstart("--report-language", "--out", out.toString()),
                "input.assess_flag_needs_value", "argument --report-language: expected one argument", out);
        assertRefusedNothingWritten(assessQuickstart("--out", "--", "x"), "input.assess_flag_needs_value",
                "argument --out: expected one argument", out);
        assertEquals(defaultOutBefore, Files.exists(Path.of("out")));
    }

    @Test
    void assessReadsTheEqualsFormAndTheLastOfARepeatedValue(@TempDir Path dir) {
        Path a = dir.resolve("a");
        Path b = dir.resolve("b");
        JsonNode env = runJson("assess",
                "--bundle=" + QUICKSTART.resolve("evidence"), "--profile=" + QUICKSTART.resolve("applicability.yaml"),
                "--out=" + a, "--out", b.toString());
        assertEquals(0, env.get("exit_code").asInt(), env.toString());
        assertTrue(Files.isRegularFile(b.resolve("manifest.json")));
        assertFalse(Files.exists(a));
        JsonNode bad = assessQuickstart("--bundle", dir.resolve("nope").toString(), "--out", a.toString());
        assertRefusedNothingWritten(bad, "input.bundle_not_a_directory", null, a);
        assertRefusedNothingWritten(assessQuickstart("--catalog-dir=" + dir.resolve("nope"), "--out", a.toString()),
                "input.catalog-dir_not_a_directory", null, a);
    }

    @Test
    void assessRefusesAReportLanguageWithNoCatalogue(@TempDir Path dir) {
        Path out = dir.resolve("o");
        for (String language : new String[] {"xx", "", "../x"}) {
            JsonNode env = assessQuickstart("--report-language", language, "--out", out.toString());
            assertRefusedNothingWritten(env, "input.report_language_unknown",
                    "--report-language " + Readiness.pyRepr(language) + " has no report catalogue.", out);
            assertEquals("choose one of: de, en.", env.get("error").get("fix").asText());
        }
    }

    @Test
    void assessAvailableLanguagesAreTheVendoredCatalogues() throws IOException {
        List<String> vendored;
        try (Stream<Path> files = Files.list(REPO.resolve("engines/java/src/main/resources/i18n"))) {
            vendored = files.map(p -> p.getFileName().toString())
                    .filter(n -> n.startsWith("messages.") && n.endsWith(".json"))
                    .map(n -> n.substring("messages.".length(), n.length() - ".json".length()))
                    .sorted()
                    .toList();
        }
        assertEquals(vendored, Messages.AVAILABLE_LANGUAGES);
    }

    @Test
    void assessReportLanguageDeLocalisesTheReportAndTheManifest(@TempDir Path dir) throws IOException {
        Path en = dir.resolve("en");
        Path de = dir.resolve("de");
        assertEquals(0, assessQuickstart("--report-language", "en", "--out", en.toString()).get("exit_code").asInt());
        assertEquals(0, assessQuickstart("--report-language", "de", "--out", de.toString()).get("exit_code").asInt());
        String md = Files.readString(de.resolve("report.md"));
        assertTrue(md.startsWith("# AgentCE-Konformitätsbericht\n"), md.lines().findFirst().orElse(""));
        assertTrue(md.lines().anyMatch("## Ergebnisübersicht"::equals));
        assertTrue(md.lines().anyMatch("## Aussagen"::equals));
        assertFalse(md.equals(Files.readString(en.resolve("report.md"))));
        String html = Files.readString(de.resolve("report.html"));
        assertTrue(html.contains("lang=\"de\"") && html.contains("Konformitätsbericht"));
        assertEquals("de", Json.parseFile(de.resolve("manifest.json")).get("run").get("report_language").asText());
        assertEquals("en", Json.parseFile(en.resolve("manifest.json")).get("run").get("report_language").asText());
    }

    @Test
    void assessRefusesABareAllowUnverifiedCatalogAndPackageForSharing(@TempDir Path dir) {
        Path out = dir.resolve("o");
        assertRefusedNothingWritten(assessQuickstart("--allow-unverified-catalog", "--out", out.toString()),
                "input.allow_unverified_requires_catalog_dir",
                "--allow-unverified-catalog was given without --catalog-dir.", out);
        JsonNode env = assessQuickstart("--package-for-sharing", "--out", out.toString());
        assertRefusedNothingWritten(env, "input.package_unsupported",
                ErrorCatalogue.errorCause("input.package_unsupported"), out);
        assertEquals(ErrorCatalogue.errorFix("input.package_unsupported"), env.get("error").get("fix").asText());
        // packaging comes before the language and the override, as in Python
        assertRefusedNothingWritten(
                assessQuickstart("--allow-unverified-catalog", "--report-language", "xx", "--package-for-sharing",
                        "--out", out.toString()),
                "input.package_unsupported", null, out);
        assertRefusedNothingWritten(
                assessQuickstart("--allow-unverified-catalog", "--report-language", "xx", "--out", out.toString()),
                "input.report_language_unknown", null, out);
        // and all of them before --emit/--for
        assertRefusedNothingWritten(
                assessQuickstart("--allow-unverified-catalog", "--emit", "md", "--out", out.toString()),
                "input.allow_unverified_requires_catalog_dir", null, out);
    }

    @Test
    void assessRefusesARecordsFolderInPythonsOrder(@TempDir Path dir) {
        Path out = dir.resolve("o");
        String bundle = QUICKSTART.resolve("evidence").toString();
        JsonNode env = runJson("assess", RECORDS.toString(), "--out", out.toString());
        assertRefusedNothingWritten(env, "input.records_unsupported",
                ErrorCatalogue.errorCause("input.records_unsupported"), out);
        assertRefusedNothingWritten(
                runJson("assess", RECORDS.toString(), "--fail-on", "severity == 'high'", "--out", out.toString()),
                "input.records_unsupported", null, out);
        String missing = dir.resolve("nope").toString();
        env = runJson("assess", missing, "--out", out.toString());
        assertRefusedNothingWritten(env, "input.records_not_a_directory",
                "the records folder " + Readiness.pyRepr(missing) + " is not an existing directory.", out);
        assertEquals("pass a folder of OpenTelemetry GenAI or OpenInference trace exports.",
                env.get("error").get("fix").asText());
        assertRefusedNothingWritten(runJson("assess", RECORDS.toString(), "--bundle", bundle, "--out", out.toString()),
                "input.records_source_ambiguous", "both a records folder and --bundle were given.", out);
        assertRefusedNothingWritten(
                runJson("assess", RECORDS.toString(), "--package-for-sharing", "--out", out.toString()),
                "input.package_requires_bundle", null, out);
        // `-1` and a token holding a space are positionals, so beside --bundle they are a records folder
        assertRefusedNothingWritten(assessQuickstart("-1", "--out", out.toString()),
                "input.records_source_ambiguous", null, out);
        assertRefusedNothingWritten(assessQuickstart("--x y", "--out", out.toString()),
                "input.records_source_ambiguous", null, out);
    }

    @Test
    void quickstartRefusesAFlagOrArgumentItDoesNotTakeAndHelpPrintsUsage(@TempDir Path out) {
        for (String[] extra : new String[][] {
                {"--ou", "o"}, {"--no-such-flag"}, {"--trust-root", "x"}, {"extra"}, {"--json=1"}, {"--"}}) {
            String[] args = Stream.concat(
                            Stream.of("quickstart", "--json", "--out", out.resolve("q").toString()), Stream.of(extra))
                    .toArray(String[]::new);
            JsonNode env = Json.parse(captureStdout(args));
            assertEquals(3, env.get("exit_code").asInt(), String.join(" ", extra));
            assertEquals("input.quickstart_unrecognized_flag", env.get("error").get("message_key").asText());
            assertFalse(Files.exists(out.resolve("q")));
        }
        String usage = captureStdout("quickstart", "--out", out.resolve("q").toString(), "--no-such-flag", "-h");
        assertTrue(usage.startsWith("usage: agentce quickstart [-h]"));
        assertFalse(Files.exists(out.resolve("q")));
    }

    @Test
    void assessDeviationsBareRefused(@TempDir Path out) {
        JsonNode env = runAssess(auditorAssessArgs(out), "--deviations");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.assess_flag_needs_value", env.get("error").get("message_key").asText());
        assertEquals("argument --deviations: expected one argument", env.get("error").get("detail").asText());
        assertFalse(Files.exists(out.resolve("assertions.json")));
    }

    @Test
    void assessDeviationsMissingValueMidArgv(@TempDir Path out) {
        List<String> args = auditorAssessArgs(out);
        args.add(3, "--deviations"); // directly before --bundle
        JsonNode env = runJson(args.toArray(String[]::new));
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.assess_flag_needs_value", env.get("error").get("message_key").asText());
        assertFalse(Files.exists(out.resolve("assertions.json")));
    }

    @Test
    void assessDeviationsLastWins(@TempDir Path dir) throws IOException {
        Path empty = dir.resolve("empty.yaml");
        Files.writeString(empty, "deviation_register_version: 1\ndeviations: []\n");
        String emptyDigest = Report.digestBytes(Files.readAllBytes(empty));
        Path first = dir.resolve("first");
        runAssess(auditorAssessArgs(first), "--deviations", AUDITOR_REGISTER.toString(), "--deviations", empty.toString());
        assertEquals(emptyDigest, registerDigest(first));
        assertEquals("non-conformant", outcomeOf(first, "AUV-01"));
        Path second = dir.resolve("second");
        runAssess(auditorAssessArgs(second), "--deviations", empty.toString(), "--deviations", AUDITOR_REGISTER.toString());
        assertEquals(AUDITOR_REGISTER_DIGEST, registerDigest(second));
        assertEquals("partial AUV-01", outcomeOf(second, "AUV-01"));
    }

    private static final String FAIL_ON_FIX = "use comparisons of the form field==\"literal\" joined by and/or, over: "
            + "control, family, mode, outcome, rung, severity, subject.";

    /** Asserts {@code env} is the --fail-on refusal with Python's cause, and that nothing was written. */
    private static void assertFailOnRefused(JsonNode env, Path out, String cause) {
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.fail_on_invalid_expression", env.get("error").get("message_key").asText());
        assertEquals(cause, env.get("error").get("detail").asText());
        assertEquals(FAIL_ON_FIX, env.get("error").get("fix").asText());
        assertFalse(env.has("fail_on"));
        assertFalse(Files.exists(out.resolve("assertions.json")));
    }

    /** Asserts {@code env} is assess's missing-value refusal naming {@code flag}, with nothing written. */
    private static void assertNeedsValue(JsonNode env, Path out, String flag, String fix) {
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.assess_flag_needs_value", env.get("error").get("message_key").asText());
        assertEquals("argument " + flag + ": expected one argument", env.get("error").get("detail").asText());
        assertEquals(fix, env.get("error").get("fix").asText());
        assertFalse(Files.exists(out.resolve("assertions.json")));
    }

    @Test
    void assessFailOnGatesExitCode(@TempDir Path dir) {
        // F0: no --fail-on keeps the any-non-conformant rule and adds no fail_on.
        JsonNode env = runAssess(auditorAssessArgs(dir.resolve("f0")));
        assertEquals(1, env.get("exit_code").asInt());
        assertFalse(env.has("fail_on"));
        // F1: the expression names two non-conformant high assertions.
        env = runAssess(auditorAssessArgs(dir.resolve("f1")),
                "--fail-on", "outcome==\"non-conformant\" and severity==\"high\"");
        assertEquals(1, env.get("exit_code").asInt());
        assertEquals("[\"findings\"]", env.get("exit_status").toString());
        assertEquals("outcome==\"non-conformant\" and severity==\"high\"", env.get("fail_on").get("expression").asText());
        assertEquals(2, env.get("fail_on").get("matched").asInt());
        // F2: nothing matches, so the run exits 0 although it holds non-conformant assertions.
        Path f2 = dir.resolve("f2");
        env = runAssess(auditorAssessArgs(f2), "--fail-on", "severity==\"critical\"");
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals("[\"ok\"]", env.get("exit_status").toString());
        assertEquals(0, env.get("fail_on").get("matched").asInt());
        assertTrue(Files.isRegularFile(f2.resolve("assertions.json")));
        // F4b: and binds tighter than or.
        env = runAssess(auditorAssessArgs(dir.resolve("f4b")), "--fail-on",
                "control==\"AUV-01\" and severity==\"low\" or control==\"AUV-02\" and severity==\"low\"");
        assertEquals(0, env.get("exit_code").asInt());
        // F9/F9b: the expression sees the outcomes after deviations are applied.
        env = runAssess(auditorAssessArgs(dir.resolve("f9")), "--deviations", AUDITOR_REGISTER.toString(),
                "--fail-on", "outcome==\"non-conformant\"");
        assertEquals(1, env.get("fail_on").get("matched").asInt());
        env = runAssess(auditorAssessArgs(dir.resolve("f9b")), "--deviations", AUDITOR_REGISTER.toString(),
                "--fail-on", "control==\"AUV-01\" and outcome==\"non-conformant\"");
        assertEquals(0, env.get("exit_code").asInt());
        // D2: a 3000-clause chain evaluates (Python's capture at base crashed with RecursionError).
        String deep = String.join(" or ", java.util.Collections.nCopies(2999, "control==\"x\"")) + " or control==\"AUV-01\"";
        env = runAssess(auditorAssessArgs(dir.resolve("d2")), "--fail-on", deep);
        assertEquals(1, env.get("exit_code").asInt());
        assertEquals(1, env.get("fail_on").get("matched").asInt());
        // R4: a refused expression writes nothing.
        Path r4 = dir.resolve("r4");
        assertFailOnRefused(runAssess(auditorAssessArgs(r4), "--fail-on", "foo==\"x\""), r4,
                "--fail-on 'foo==\"x\"' is not a valid expression: unknown field 'foo'; choose from: "
                        + "control, family, mode, outcome, rung, severity, subject");
        // O1: a missing bundle is refused before the expression is read.
        JsonNode o1 = runJson("assess", "--out", dir.resolve("o1").toString(),
                "--bundle", dir.resolve("nope").toString(),
                "--profile", AUDITOR_FIXTURE.resolve("applicability.yaml").toString(), "--fail-on", "foo");
        assertEquals("input.bundle_not_a_directory", o1.get("error").get("message_key").asText());
        // O3/O4: the expression is refused before an unknown --catalog and a missing --deviations.
        String fooCause = "--fail-on 'foo' is not a valid expression: unknown field 'foo'; choose from: "
                + "control, family, mode, outcome, rung, severity, subject";
        Path o3 = dir.resolve("o3");
        assertFailOnRefused(runAssess(auditorAssessArgs(o3), "--catalog", "nope@1", "--fail-on", "foo"), o3, fooCause);
        Path o4 = dir.resolve("o4");
        assertFailOnRefused(runAssess(auditorAssessArgs(o4),
                "--deviations", dir.resolve("nope.yaml").toString(), "--fail-on", "foo"), o4, fooCause);
    }

    @Test
    void assessFailOnEqualsFormAndLastWins(@TempDir Path dir) {
        // F7: --fail-on=<expression>, split at the first '=' only.
        JsonNode env = runAssess(auditorAssessArgs(dir.resolve("f7")), "--fail-on=severity==\"critical\"");
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals("severity==\"critical\"", env.get("fail_on").get("expression").asText());
        // F8/F8b: the last occurrence wins, whichever spelling it uses.
        env = runAssess(auditorAssessArgs(dir.resolve("f8")),
                "--fail-on", "control==\"AUV-01\"", "--fail-on", "control==\"none\"");
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals("control==\"none\"", env.get("fail_on").get("expression").asText());
        env = runAssess(auditorAssessArgs(dir.resolve("f8b")),
                "--fail-on=control==\"none\"", "--fail-on", "control==\"AUV-01\"");
        assertEquals(1, env.get("exit_code").asInt());
        assertEquals(1, env.get("fail_on").get("matched").asInt());
        // R2: --fail-on= is an empty expression, not a missing value.
        Path r2 = dir.resolve("r2");
        assertFailOnRefused(runAssess(auditorAssessArgs(r2), "--fail-on="), r2,
                "--fail-on '' is not a valid expression: the --fail-on expression is empty");
        // M4: --fail-on=-x is a value.
        Path m4 = dir.resolve("m4");
        assertFailOnRefused(runAssess(auditorAssessArgs(m4), "--fail-on=-x"), m4,
                "--fail-on '-x' is not a valid expression: unexpected character '-' at position 0");
    }

    @Test
    void assessFailOnMissingValueRefused(@TempDir Path dir) {
        String fix = "pass --fail-on <expression>.";
        // M1: trailing.
        Path m1 = dir.resolve("m1");
        assertNeedsValue(runAssess(auditorAssessArgs(m1), "--fail-on"), m1, "--fail-on", fix);
        // M2: directly before another flag.
        Path m2 = dir.resolve("m2");
        List<String> args = auditorAssessArgs(m2);
        args.add(3, "--fail-on");
        assertNeedsValue(runJson(args.toArray(String[]::new)), m2, "--fail-on", fix);
        // M3: -x looks like an option.
        Path m3 = dir.resolve("m3");
        assertNeedsValue(runAssess(auditorAssessArgs(m3), "--fail-on", "-x"), m3, "--fail-on", fix);
        // M7/M8/M9: a negative number (any Unicode decimal digit) or a token holding a space is a value.
        List<String> values = List.of("-1", "-a b", "-١", "-.5");
        for (int i = 0; i < values.size(); i++) {
            String value = values.get(i);
            Path out = dir.resolve("value" + i);
            assertFailOnRefused(runAssess(auditorAssessArgs(out), "--fail-on", value), out,
                    "--fail-on " + Readiness.pyRepr(value)
                            + " is not a valid expression: unexpected character '-' at position 0");
        }
    }

    @Test
    void assessValueFlagsNameFirstMissing(@TempDir Path dir) {
        // M5: --fail-on is first and has no value (--deviations follows it).
        Path m5 = dir.resolve("m5");
        assertNeedsValue(runAssess(auditorAssessArgs(m5), "--fail-on", "--deviations", AUDITOR_REGISTER.toString()),
                m5, "--fail-on", "pass --fail-on <expression>.");
        // M6: --deviations is first and has no value (--fail-on follows it).
        Path m6 = dir.resolve("m6");
        assertNeedsValue(runAssess(auditorAssessArgs(m6), "--deviations", "--fail-on", "control==\"x\""),
                m6, "--deviations", "pass --deviations <file>.");
        // Both given properly, in either order, apply together.
        JsonNode env = runAssess(auditorAssessArgs(dir.resolve("both")),
                "--fail-on", "outcome==\"partial\"", "--deviations", AUDITOR_REGISTER.toString());
        assertEquals(1, env.get("exit_code").asInt());
        assertEquals(1, env.get("fail_on").get("matched").asInt());
        assertEquals("partial AUV-01", outcomeOf(dir.resolve("both"), "AUV-01"));
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

    /** The same capture for the test seams' own entry point (18.108: they are not CLI commands). */
    private static String captureSeamsStdout(String... args) {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        PrintStream original = System.out;
        System.setOut(new PrintStream(buf, true, StandardCharsets.UTF_8));
        try {
            Seams.run(args);
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
        assertEquals(expected + "\n", captureSeamsStdout("digest-tree", fixture.toString()));
    }

    @Test
    void securityViewVerbRunsTheFixtureAndPrintsAStandardsCitationsArray() {
        Path fixture = REPO.resolve("verification/gates/fixtures/security_view/activity_and_assertions.json");
        String out = captureSeamsStdout("security-view", fixture.toString());
        JsonNode citations = Json.parse(out).get("standards_citations");
        assertTrue(citations.isArray());
        Set<String> frameworks = new HashSet<>();
        for (JsonNode citation : citations) {
            frameworks.add(citation.get("framework").asText());
        }
        assertEquals(Set.of("owasp-asi-2026", "mitre-atlas", "owasp-acs"), frameworks);
    }

    @Test
    void anUnknownVerbIsRefusedAndAnUnbuiltCommandIsNotImplemented() {
        // 18.108: an unknown command (a test seam's name included) is input.unknown_command, as in
        // Python; a documented command this engine has not built still answers cli.not_implemented.
        for (String verb : List.of("frobnicate", "digest-tree")) {
            JsonNode env = runJson(verb);
            assertEquals(3, env.get("exit_code").asInt());
            assertEquals("input.unknown_command", env.get("error").get("message_key").asText());
        }
        JsonNode env = runJson("doctor");
        assertEquals("cli.not_implemented", env.get("error").get("message_key").asText());
        assertEquals("doctor", env.get("error").get("command").asText());
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
                "re-run with --debug to see the stack trace, then file an issue for an AgentCE maintainer to investigate.",
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
    void readinessAnUnrecognizedFlagGivesInputReadinessUnrecognizedFlagNeverASilentMisreadOfReportDir(
            @TempDir Path dir) throws IOException {
        Path report = readinessReport(dir);
        JsonNode env = runJson("readiness", "--gasp", report.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.readiness_unrecognized_flag", env.get("error").get("message_key").asText());
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

    // --- `sign` (item 18.26): the Ed25519/DSSE/in-toto signing flow, refusing to sign unless
    // readiness passes. Mirrors cli.test.ts's own sign block (lines 807-1343). ---

    private record EdKeyFile(Path path, byte[] rawPublicKey) {}

    private static EdKeyFile edKeyFile(Path dir, String name) throws Exception {
        KeyPair pair = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        Path path = dir.resolve(name);
        Files.writeString(path, Fixtures.toPem("PRIVATE KEY", pair.getPrivate().getEncoded()));
        byte[] spki = pair.getPublic().getEncoded();
        byte[] rawPublicKey = Arrays.copyOfRange(spki, spki.length - 32, spki.length);
        return new EdKeyFile(path, rawPublicKey);
    }

    private static EdKeyFile edKeyFile(Path dir) throws Exception {
        return edKeyFile(dir, "key.pem");
    }

    /** A READY report directory (reusing {@link #readinessReport(Path)}'s clean shape) with a
     * {@code claim.json} to sign, the minimal shape {@code signSubjects}/{@code cmdSign} need. */
    private static Path signReportDir(Path dir, String claimJson) throws IOException {
        Path reportDir = readinessReport(dir);
        Files.writeString(reportDir.resolve("claim.json"), claimJson);
        return reportDir;
    }

    private static Path signReportDir(Path dir) throws IOException {
        return signReportDir(dir, "{\"claimant\":{\"org\":\"acme\"}}");
    }

    @Test
    void signMalformedClaimGivesClaimMalformedBeforeTheKeyOrADryRun(@TempDir Path dir) throws Exception {
        String notJson = "claim.json is not valid JSON.";
        String notList = "claim.json's signatures field is not a list.";
        Object[][] cases = {
            {new byte[] {'{', '"', 'a', '"', ':', '"', (byte) 0xff, '"', '}'}, notJson},
            {"{\"a\": ".getBytes(StandardCharsets.UTF_8), notJson},
            {"{} GARBAGE".getBytes(StandardCharsets.UTF_8), notJson},
            {"{\"a\": \"\\ud800\"}".getBytes(StandardCharsets.UTF_8), notJson},
            {"[1, 2]".getBytes(StandardCharsets.UTF_8), "claim.json is not an object."},
            {"{\"signatures\": null}".getBytes(StandardCharsets.UTF_8), notList},
            {"{\"signatures\": \"x\"}".getBytes(StandardCharsets.UTF_8), notList},
            {"{\"signatures\": {}}".getBytes(StandardCharsets.UTF_8), notList},
        };
        for (int i = 0; i < cases.length; i++) {
            byte[] raw = (byte[]) cases[i][0];
            Path report = readinessReport(Files.createDirectories(dir.resolve("c" + i)));
            Files.write(report.resolve("claim.json"), raw);
            for (String[] extra : new String[][] {{"--key", dir.resolve("missing.pem").toString()}, {"--dry-run"}}) {
                List<String> argv = new ArrayList<>(List.of(
                        "sign", report.toString(), "--as", "claimant", "--profile", "kms"));
                argv.addAll(List.of(extra));
                JsonNode env = runJson(argv.toArray(String[]::new));
                assertEquals(3, env.get("exit_code").asInt());
                assertEquals("sign.claim_malformed", env.get("error").get("message_key").asText());
                assertEquals(cases[i][1], env.get("error").get("detail").asText());
            }
            assertArrayEquals(raw, Files.readAllBytes(report.resolve("claim.json")));
            assertFalse(Files.exists(report.resolve("signatures")));
        }
    }

    @Test
    void signDryRunWithNoClaimGivesNoClaim(@TempDir Path dir) throws Exception {
        Path report = readinessReport(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "claimant", "--dry-run");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.no_claim", env.get("error").get("message_key").asText());
    }

    @Test
    void signLegacyDsaKeyGivesKeyAlgorithm(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        Path dsa = Path.of("..", "..", "tools", "fixtures", "sign", "dsa-key.pem").toAbsolutePath();
        JsonNode env = runJson(
                "sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", dsa.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.key_algorithm", env.get("error").get("message_key").asText());
    }

    @Test
    void signKmsProfileHappyPathSignsClaimJsonWritesADetachedSignatureExitZero(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        EdKeyFile key = edKeyFile(dir);
        JsonNode env = runJson(
                "sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", key.path().toString());
        assertEquals(0, env.get("exit_code").asInt());
        assertEquals("claimant", env.get("as").asText());
        assertEquals("kms", env.get("profile").asText());
        assertFalse(env.get("dry_run").asBoolean());
        assertEquals("READY", env.get("readiness").asText());
        assertEquals(1, env.get("signatures").asInt());
        String detachedPath = env.get("signature").asText();
        assertTrue(Files.isRegularFile(Path.of(detachedPath)));
        JsonNode detached = Json.parseFile(Path.of(detachedPath));
        assertEquals("claimant", detached.get("role").asText());
        assertEquals("kms", detached.get("profile").asText());
        assertEquals("application/vnd.in-toto+json", detached.get("payloadType").asText());
        assertEquals(env.get("keyid").asText(), detached.get("signatures").get(0).get("keyid").asText());

        JsonNode claim = Json.parseFile(report.resolve("claim.json"));
        assertEquals(1, claim.get("signatures").size());
        assertEquals(detached, claim.get("signatures").get(0));
        assertFalse(env.has("trust_root"));
        assertFalse(Files.exists(report.resolve("trust-root.json")));

        // The signature actually verifies against the key's real derived public key -- a real
        // cryptographic round trip, not a shape-only assertion.
        byte[] payload = Base64.getDecoder().decode(detached.get("payload").asText());
        JsonNode statement = Json.parse(new String(payload, StandardCharsets.UTF_8));
        assertEquals("https://in-toto.io/Statement/v1", statement.get("_type").asText());
        assertEquals("claimant", statement.get("predicate").get("role").asText());
        assertEquals("kms", statement.get("predicate").get("profile").asText());
        byte[] sig = Base64.getDecoder().decode(detached.get("signatures").get(0).get("sig").asText());
        assertTrue(Fixtures.edVerify(Sign.dssePae(Sign.INTOTO_PAYLOAD_TYPE, payload), key.rawPublicKey(), sig));
    }

    @Test
    void signWriteTrustRootWritesATrustRootJsonWhosePublicKeyVerifiesTheSignature(@TempDir Path dir)
            throws Exception {
        Path report = signReportDir(dir, "{\"claimant\":{\"org\":\"acme corp\"}}");
        EdKeyFile key = edKeyFile(dir);
        JsonNode env = runJson(
                "sign",
                report.toString(),
                "--as", "assessor",
                "--profile", "kms",
                "--key", key.path().toString(),
                "--write-trust-root");
        assertEquals(0, env.get("exit_code").asInt());
        String trustRootPath = env.get("trust_root").asText();
        assertTrue(Files.isRegularFile(Path.of(trustRootPath)));
        JsonNode trustRoot = Json.parseFile(Path.of(trustRootPath));
        String keyid = env.get("keyid").asText();
        assertEquals(
                Base64.getEncoder().encodeToString(key.rawPublicKey()),
                trustRoot.get("keys").get(keyid).get("public_key").asText());
        assertEquals("acme corp", trustRoot.get("keys").get(keyid).get("identity").asText());

        JsonNode detached = Json.parseFile(Path.of(env.get("signature").asText()));
        byte[] payload = Base64.getDecoder().decode(detached.get("payload").asText());
        byte[] sig = Base64.getDecoder().decode(detached.get("signatures").get(0).get("sig").asText());
        byte[] rawPublicKey = Base64.getDecoder().decode(trustRoot.get("keys").get(keyid).get("public_key").asText());
        assertTrue(Fixtures.edVerify(Sign.dssePae(Sign.INTOTO_PAYLOAD_TYPE, payload), rawPublicKey, sig));
    }

    @Test
    void signWriteTrustRootDefaultsIdentityToUnsetWhenClaimantOrgIsAbsent(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir, "{}");
        EdKeyFile key = edKeyFile(dir);
        JsonNode env = runJson(
                "sign",
                report.toString(),
                "--as", "claimant",
                "--profile", "kms",
                "--key", key.path().toString(),
                "--write-trust-root");
        JsonNode trustRoot = Json.parseFile(Path.of(env.get("trust_root").asText()));
        String keyid = env.get("keyid").asText();
        assertEquals("unset", trustRoot.get("keys").get(keyid).get("identity").asText());
    }

    @Test
    void signDryRunWritesNothingTouchesNoKeyEvenWithABadKeyValue(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        Path claimPath = report.resolve("claim.json");
        String before = Files.readString(claimPath);
        FileTime beforeMtime = Files.getLastModifiedTime(claimPath);
        JsonNode env = runJson(
                "sign",
                report.toString(),
                "--as", "claimant",
                "--profile", "kms",
                "--key", dir.resolve("does-not-exist.pem").toString(),
                "--dry-run");
        assertEquals(0, env.get("exit_code").asInt());
        assertTrue(env.get("dry_run").asBoolean());
        assertEquals("READY", env.get("readiness").asText());
        assertFalse(env.has("signature"));
        assertEquals(before, Files.readString(claimPath));
        assertEquals(beforeMtime, Files.getLastModifiedTime(claimPath));
        assertFalse(Files.exists(report.resolve("signatures")));
    }

    @Test
    void signASecondSignCallAppendsASecondSignaturesEntryRatherThanReplacingTheFirst(@TempDir Path dir)
            throws Exception {
        Path report = signReportDir(dir);
        EdKeyFile key1 = edKeyFile(dir, "key1.pem");
        EdKeyFile key2 = edKeyFile(dir, "key2.pem");
        runJson("sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", key1.path().toString());
        JsonNode env = runJson(
                "sign", report.toString(), "--as", "assessor", "--profile", "kms", "--key", key2.path().toString());
        assertEquals(2, env.get("signatures").asInt());
        JsonNode claim = Json.parseFile(report.resolve("claim.json"));
        assertEquals(2, claim.get("signatures").size());
        assertEquals("claimant", claim.get("signatures").get(0).get("role").asText());
        assertEquals("assessor", claim.get("signatures").get(1).get("role").asText());
    }

    @Test
    void signANotReadyReportRefusesWithSignNotReadyBeforeTouchingAnyKeyOrFile(@TempDir Path dir) throws Exception {
        Path report = readinessReport(dir, "[]", "{\"status\":\"failed\",\"stream\":\"gw\"}\n");
        Files.writeString(report.resolve("claim.json"), "{}");
        EdKeyFile key = edKeyFile(dir);
        JsonNode env = runJson(
                "sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", key.path().toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.not_ready", env.get("error").get("message_key").asText());
        assertTrue(env.get("error").get("detail").asText().startsWith("the report is NOT READY: "));
        assertEquals(
                "resolve the blocking reasons (agentce readiness <report-dir>) before signing.",
                env.get("error").get("fix").asText());
        assertFalse(Json.parseFile(report.resolve("claim.json")).has("signatures"));
    }

    @Test
    void signAsAbsentGivesInputSignRole(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.sign_role", env.get("error").get("message_key").asText());
        assertEquals("--as must be `claimant` or `assessor`.", env.get("error").get("detail").asText());
        assertEquals("pass --as claimant|assessor.", env.get("error").get("fix").asText());
    }

    @Test
    void signAsBogusGivesInputSignRole(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "bogus");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.sign_role", env.get("error").get("message_key").asText());
    }

    @Test
    void signAnUnknownProfileGivesInputSignProfileWithAPyReprQuotedProfileName(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "claimant", "--profile", "bogus");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.sign_profile", env.get("error").get("message_key").asText());
        assertEquals("unknown signing profile 'bogus'.", env.get("error").get("detail").asText());
        assertEquals("choose one of: sigstore-public, sigstore-private, kms.", env.get("error").get("fix").asText());
    }

    @Test
    void signProfileItsGivesInputSignProfileWithReprsDoubleQuoteSwitch(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "claimant", "--profile", "it's");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.sign_profile", env.get("error").get("message_key").asText());
        assertEquals("unknown signing profile \"it's\".", env.get("error").get("detail").asText());
    }

    @Test
    void signProfileEmptyFallsThroughToTheSigstorePublicDefaultThenRefusesKeylessOffline(@TempDir Path dir)
            throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "claimant", "--profile", "");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.keyless_offline", env.get("error").get("message_key").asText());
    }

    @Test
    void signAnOmittedProfileDefaultsToSigstorePublicWhichRefusesOffline(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "claimant");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.keyless_offline", env.get("error").get("message_key").asText());
        assertEquals(
                "the sigstore-public profile is keyless and obtains a certificate from a Fulcio instance "
                        + "(network); the engine does not sign it offline.",
                env.get("error").get("detail").asText());
        assertEquals(
                "use --profile kms --key <file> offline, or run keyless signing where the Fulcio and Rekor "
                        + "endpoints are reachable.",
                env.get("error").get("fix").asText());
    }

    @Test
    void signProfileSigstorePrivateAlsoRefusesOfflineWithSignKeylessOfflineEvenWithKeyGiven(@TempDir Path dir)
            throws Exception {
        Path report = signReportDir(dir);
        EdKeyFile key = edKeyFile(dir);
        JsonNode env = runJson(
                "sign",
                report.toString(),
                "--as", "claimant",
                "--profile", "sigstore-private",
                "--key", key.path().toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.keyless_offline", env.get("error").get("message_key").asText());
    }

    @Test
    void signWriteTrustRootWithoutProfileKmsGivesSignTrustRootRequiresKmsQuotingTheProfile(@TempDir Path dir)
            throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "claimant", "--write-trust-root");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.trust_root_requires_kms", env.get("error").get("message_key").asText());
        assertEquals(
                "--write-trust-root needs an exportable public key; the 'sigstore-public' profile has none.",
                env.get("error").get("detail").asText());
        assertEquals(
                "pass --profile kms --key <ed25519-private-key.pem> --write-trust-root.",
                env.get("error").get("fix").asText());
    }

    @Test
    void signAMissingClaimJsonGivesSignNoClaim(@TempDir Path dir) throws Exception {
        Path report = readinessReport(dir);
        EdKeyFile key = edKeyFile(dir);
        JsonNode env = runJson(
                "sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", key.path().toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.no_claim", env.get("error").get("message_key").asText());
        assertEquals("the report directory has no claim.json to sign.", env.get("error").get("detail").asText());
        assertEquals(
                "produce the report first: `agentce assess … --out <report-dir>`.",
                env.get("error").get("fix").asText());
    }

    @Test
    void signProfileKmsWithNoKeyGivesSignKmsKeyMissing(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "claimant", "--profile", "kms");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.kms_key_missing", env.get("error").get("message_key").asText());
        assertEquals("the kms profile signs with an operator-held key.", env.get("error").get("detail").asText());
        assertEquals("pass --key <ed25519-private-key.pem>.", env.get("error").get("fix").asText());
    }

    @Test
    void signProfileKmsWithKeyEmptyGivesSignKmsKeyMissingFalsyLikeAnOmittedKey(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", "");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.kms_key_missing", env.get("error").get("message_key").asText());
    }

    @Test
    void signKeyGivenButNotAnExistingFileGivesInputKeyNotAFile(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson(
                "sign",
                report.toString(),
                "--as", "claimant",
                "--profile", "kms",
                "--key", dir.resolve("no-such-key.pem").toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.key_not_a_file", env.get("error").get("message_key").asText());
    }

    @Test
    void signANonEd25519KeyGivesSignKeyAlgorithmWithTheExactCatalogedText(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        Path keyPath = dir.resolve("rsa.pem");
        Files.writeString(keyPath, Fixtures.toPem("PRIVATE KEY", generator.generateKeyPair().getPrivate().getEncoded()));
        JsonNode env = runJson(
                "sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", keyPath.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.key_algorithm", env.get("error").get("message_key").asText());
        assertEquals("the signing key is not an Ed25519 private key.", env.get("error").get("detail").asText());
        assertEquals(
                "supply an Ed25519 key (the algorithm the engine signs with, SPEC §8.7).",
                env.get("error").get("fix").asText());
    }

    @Test
    void signAnUnparseableKeyFileGivesSignKeyUnreadableWithTheExactCatalogedText(@TempDir Path dir) throws Exception {
        Path report = signReportDir(dir);
        Path keyPath = dir.resolve("garbage.pem");
        Files.writeString(keyPath, "not a pem\n");
        JsonNode env = runJson(
                "sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", keyPath.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("sign.key_unreadable", env.get("error").get("message_key").asText());
        assertEquals(
                "the signing key file could not be parsed as an unencrypted PEM private key.",
                env.get("error").get("detail").asText());
        assertEquals(
                "supply an unencrypted Ed25519 private key PEM (`openssl genpkey -algorithm ed25519 "
                        + "-out key.pem`, or `agentce catalog sign --new-key <path>`).",
                env.get("error").get("fix").asText());
    }

    @Test
    void signAnUnrecognizedFlagGivesInputSignUnrecognizedFlagNeverASilentMisreadOfReportDir(@TempDir Path dir)
            throws Exception {
        Path report = signReportDir(dir);
        JsonNode env = runJson("sign", report.toString(), "--role", "claimant");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.sign_unrecognized_flag", env.get("error").get("message_key").asText());
    }

    @Test
    void verifyAUsageErrorIsTheKeyedEnvelopePythonAndTypeScriptGiveAndAnAbbreviatedFlagIsRefused()
            throws Exception {
        String[][] cases = {
            {"unrecognized flag '--bogus'.", "--bogus", "x"},
            {"unrecognized flag '--cat'.", "--cat", "x"},
            {"flag '--catalog' needs a value.", "--catalog"},
            {"unrecognized argument 'extra'.", "--catalog", "x", "extra"},
            {"unrecognized flag '--'.", "--", "--catalog", "x"},
            {"flag '--signer-trust-root' needs a value.", "--catalog", "x", "--signer-trust-root"},
            {"flag '--json' takes no value.", "--json=1", "--catalog", "x"},
        };
        for (String[] c : cases) {
            String[] argv = new String[c.length];
            argv[0] = "verify";
            System.arraycopy(c, 1, argv, 1, c.length - 1);
            JsonNode env = runJson(argv);
            String label = String.join(" ", argv);
            assertEquals(3, env.get("exit_code").asInt(), label);
            assertEquals("input.verify_unrecognized_flag", env.get("error").get("message_key").asText(), label);
            assertEquals(c[0], env.get("error").get("detail").asText(), label);
        }
    }

    // --- `verify` (item 18.28): offline DSSE/certificate verification, dispatched end to end through
    // the real CLI. `VerifyTest` covers the compute seam's own primitives in full (real signed round
    // trips, every malformed-shape vector); these cases prove the CLI wiring, input validation, and
    // exit-code shape -- the vendored dev-root trust root has no CLI override, so a real
    // verified:true --catalog/--release round trip is out of reach from here (covered by `VerifyTest`
    // against test-local trust roots, and by C3's cross-engine parity check against dev-root fixtures).

    @Test
    void verifyWithNoTargetGivesInputVerifyTarget() {
        JsonNode env = runJson("verify");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.verify_target", env.get("error").get("message_key").asText());
        assertEquals(
                "verify needs exactly one of --bundle, --catalog, --release, or --report.",
                env.get("error").get("detail").asText());
    }

    @Test
    void verifyWithTwoTargetsGivesInputVerifyTarget(@TempDir Path dir) {
        JsonNode env = runJson("verify", "--catalog", dir.toString(), "--release", dir.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.verify_target", env.get("error").get("message_key").asText());
    }

    @Test
    void verifySignerTrustRootWithoutReportGivesTheSecondInputVerifyTargetMessage(@TempDir Path dir) {
        JsonNode env = runJson("verify", "--catalog", dir.toString(), "--signer-trust-root", "x.json");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.verify_target", env.get("error").get("message_key").asText());
        assertEquals(
                "--signer-trust-root/--expect-keyid apply only to --report.",
                env.get("error").get("detail").asText());
    }

    @Test
    void verifyCatalogNotADirectoryGivesInputCatalogNotADirectory(@TempDir Path dir) throws Exception {
        Path notADir = dir.resolve("not-a-dir.txt");
        Files.writeString(notADir, "x");
        JsonNode env = runJson("verify", "--catalog", notADir.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.catalog_not_a_directory", env.get("error").get("message_key").asText());
    }

    @Test
    void verifyCatalogUnsignedSoftFailsWithTheExactSentenceAndExitThree(@TempDir Path dir) throws Exception {
        Files.writeString(dir.resolve("rule.yaml"), "x: 1\n");
        JsonNode env = runJson("verify", "--catalog", dir.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertFalse(env.get("verified").asBoolean());
        assertEquals(
                "unsigned: catalog.sig.json is absent, so there is no signature to verify (SPEC §8.7).",
                env.get("reason").asText());
        assertEquals(dir.toString(), env.get("catalog").asText());
    }

    @Test
    void verifyReleaseMissingPathGivesInputReleaseMissing(@TempDir Path dir) {
        Path missing = dir.resolve("nope.dsse.json");
        JsonNode env = runJson("verify", "--release", missing.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.release_missing", env.get("error").get("message_key").asText());
        assertTrue(env.get("error").get("detail").asText().contains(missing.toString()));
    }

    @Test
    void verifyReleaseUnreadableEnvelopeSoftFailsWithTheFixedSentenceAndExitThree(@TempDir Path dir)
            throws Exception {
        Path path = dir.resolve("release.dsse.json");
        Files.writeString(path, "not json");
        JsonNode env = runJson("verify", "--release", path.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertFalse(env.get("verified").asBoolean());
        assertEquals("release envelope is not readable JSON", env.get("reason").asText());
        assertFalse(env.has("error"));
    }

    @Test
    void verifyBundleIsNowReachableFromTheCliWithTheSameShapeValidateEstablishes() {
        JsonNode env = runJson("verify", "--bundle", QUICKSTART.resolve("evidence").toString());
        assertTrue(env.get("stream_count").asInt() > 0);
        assertTrue(env.get("streams").isArray());
        assertEquals(
                env.get("stream_count").asInt(),
                env.get("streams").size());
        assertEquals(QUICKSTART.resolve("evidence").toString(), env.get("bundle").asText());
        // exit_code is FINDINGS (1) when any stream is broken, OK (0) otherwise -- either is a real,
        // non-crashing outcome; the wiring itself (unreachable before this item) is what this proves.
        assertTrue(env.get("exit_code").asInt() == 0 || env.get("exit_code").asInt() == 1);
    }

    @Test
    void verifyReportFallsToCliNotImplemented(@TempDir Path dir) {
        JsonNode env = runJson("verify", "--report", dir.toString());
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("cli.not_implemented", env.get("error").get("message_key").asText());
    }

    // --- assess --catalog-dir signature verification (SPEC §8.7, 18.36): the Python reference
    // capture's scenarios (evidence P18-18.36/python-reference.md), with Python's literal cause/fix
    // text. Scenario 10 (quickstart honours AGENTCE_TRUST_ROOT) needs an environment variable the JVM
    // cannot set for itself; VG-BYO-CATALOG-PARITY runs it against the real Java CLI. ---

    private static final String UNSIGNED_REASON =
            "unsigned: catalog.sig.json is absent, so there is no signature to verify (SPEC §8.7).";
    private static final String TRUST_ROOT_FIX =
            "pass --trust-root <file> (or set AGENTCE_TRUST_ROOT) to a trust root in the form of the "
                    + "engine's vendored data/trust/dev-root.json.";
    private static final String UNVERIFIED_FIX =
            "point --catalog-dir at a catalog whose catalog.sig.json verifies, or pass --trust-root <file> "
                    + "(or set AGENTCE_TRUST_ROOT) for the root that signed it; --allow-unverified-catalog "
                    + "assesses it anyway and records the override as a limitation.";

    /** A throwaway Ed25519 catalog-signing key and a {@code --trust-root} file that trusts only it. */
    private record CatalogKey(String keyid, Path trustRoot, PrivateKey privateKey) {
        static CatalogKey create(Path work, String name) throws Exception {
            KeyPair pair = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
            byte[] spki = pair.getPublic().getEncoded();
            byte[] raw = Arrays.copyOfRange(spki, spki.length - 32, spki.length);
            String keyid = Sign.keyidFor(raw);
            ObjectNode root = Json.nodes().objectNode();
            ObjectNode entry = root.putObject("keys").putObject(keyid);
            entry.put("public_key", Base64.getEncoder().encodeToString(raw));
            entry.put("identity", "test://trusted-signer");
            Path trustRoot = work.resolve(name + "-trust-root.json");
            Files.writeString(trustRoot, Json.pretty(root));
            return new CatalogKey(keyid, trustRoot, pair.getPrivate());
        }

        /** Signs {@code dir} over its current bytes, in the {@code catalog.sig.json} shape. */
        void signDir(Path dir) throws IOException {
            Files.deleteIfExists(dir.resolve("catalog.sig.json"));
            String digest = Catalog.digestTree(dir, Set.of("catalog.sig.json"));
            ObjectNode statement = Json.nodes().objectNode();
            statement.put("_type", "https://in-toto.io/Statement/v1");
            ObjectNode subject = statement.putArray("subject").addObject();
            subject.put("name", "catalog");
            subject.putObject("digest").put("sha256", digest.substring("sha256:".length()));
            statement.put("predicateType", "https://agent-conformance.org/attestation/catalog/v1");
            statement.putObject("predicate");
            ObjectNode envelope = Sign.signStatement(statement, new Sign.Signer() {
                @Override
                public byte[] sign(byte[] data) {
                    try {
                        Signature signature = Signature.getInstance("Ed25519");
                        signature.initSign(privateKey);
                        signature.update(data);
                        return signature.sign();
                    } catch (java.security.GeneralSecurityException e) {
                        throw new IllegalStateException(e);
                    }
                }

                @Override
                public String keyid() {
                    return keyid;
                }
            });
            Files.writeString(dir.resolve("catalog.sig.json"), Json.pretty(envelope));
        }
    }

    /** A copy of the base EU AI Act catalog at {@code work/name}, with its own signature removed. */
    private static Path unsignedCatalogCopy(Path work, String name) throws IOException {
        Path dir = work.resolve(name);
        try (var paths = Files.walk(CATALOG_DIR)) {
            for (Path source : (Iterable<Path>) paths::iterator) {
                Path target = dir.resolve(CATALOG_DIR.relativize(source).toString());
                if (Files.isDirectory(source)) {
                    Files.createDirectories(target);
                } else {
                    Files.copy(source, target);
                }
            }
        }
        Files.delete(dir.resolve("catalog.sig.json"));
        return dir;
    }

    private static JsonNode byoAssess(Path work, String... extra) {
        List<String> args = new ArrayList<>(List.of(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog", "eu-ai-act@2026.09"));
        args.addAll(List.of(extra));
        args.addAll(List.of("--out", work.resolve("out").toString()));
        return runJson(args.toArray(new String[0]));
    }

    private static void assertRefusal(JsonNode env, String key, String cause, String fix) {
        assertEquals(3, env.get("exit_code").asInt(), env.toString());
        JsonNode error = env.get("error");
        assertEquals(key, error.get("message_key").asText());
        if (cause != null) {
            assertEquals(cause, error.get("detail").asText());
        }
        assertEquals(fix, error.get("fix").asText());
    }

    @Test
    void assessRefusesACatalogDirSignedByAKeyTheTrustRootDoesNotKnow(@TempDir Path work) throws Exception {
        CatalogKey key = CatalogKey.create(work, "stranger");
        Path dir = unsignedCatalogCopy(work, "untrusted-cat");
        key.signDir(dir);
        JsonNode env = byoAssess(work, "--catalog-dir", dir.toString());
        assertRefusal(env, "input.catalog_unverified",
                "the catalog directory untrusted-cat did not verify against the effective trust root: no "
                        + "signature verified against the trust root: no trusted key for keyid '" + key.keyid() + "'",
                UNVERIFIED_FIX);
        assertFalse(Files.exists(work.resolve("out")), "a refused run wrote output");
    }

    @Test
    void assessRefusesAnUnsignedCatalogDirWithoutTheOverride(@TempDir Path work) throws Exception {
        JsonNode env = byoAssess(work, "--catalog-dir", unsignedCatalogCopy(work, "unsigned-cat").toString());
        assertRefusal(env, "input.catalog_unverified",
                "the catalog directory unsigned-cat did not verify against the effective trust root: "
                        + UNSIGNED_REASON,
                UNVERIFIED_FIX);
    }

    @Test
    void theOverrideAssessesAnUnsignedCatalogAndRecordsTheLimitation(@TempDir Path work) throws Exception {
        JsonNode env = byoAssess(work,
                "--catalog-dir", unsignedCatalogCopy(work, "unsigned-cat").toString(), "--allow-unverified-catalog");
        assertEquals(0, env.get("exit_code").asInt(), env.toString());
        String expected = "[\"catalog eu-ai-act@2026.09 at unsigned-cat was used unverified "
                + "(--allow-unverified-catalog): " + UNSIGNED_REASON + "\"]";
        assertEquals(Json.parse(expected), env.get("limitations"));
        JsonNode manifest = Json.parseFile(work.resolve("out/manifest.json"));
        assertEquals(Json.parse(expected), manifest.get("limitations"));
        assertTrue(Json.parseFile(work.resolve("out/assertions.json")).size() > 0);
    }

    @Test
    void assessAcceptsACatalogDirSignedByTheTrustRootKeyWithNoLimitation(@TempDir Path work) throws Exception {
        CatalogKey key = CatalogKey.create(work, "signer");
        Path dir = unsignedCatalogCopy(work, "trusted-cat");
        key.signDir(dir);
        JsonNode env = byoAssess(work, "--catalog-dir", dir.toString(), "--trust-root", key.trustRoot().toString());
        assertEquals(0, env.get("exit_code").asInt(), env.toString());
        assertFalse(env.has("limitations"));
        assertFalse(Json.parseFile(work.resolve("out/manifest.json")).has("limitations"));
    }

    @Test
    void assessRefusesATrustRootThatIsNotAFileEvenWithNoCatalogDir(@TempDir Path work) throws Exception {
        String absent = work.resolve("absent.json").toString();
        String dir = unsignedCatalogCopy(work, "unsigned-cat").toString();
        for (String[] extra : List.of(new String[] {"--catalog-dir", dir}, new String[] {})) {
            List<String> args = new ArrayList<>(List.of(extra));
            args.addAll(List.of("--trust-root", absent));
            assertRefusal(byoAssess(work, args.toArray(new String[0])), "input.trust_root_not_a_file",
                    "the trust root '" + absent + "' is not an existing file.", "pass --trust-root <file>.");
        }
    }

    @Test
    void assessRefusesATrustRootThatIsNotReadableJson(@TempDir Path work) throws Exception {
        Path root = work.resolve("bad-root.json");
        Files.writeString(root, "{not json");
        JsonNode env = byoAssess(work, "--trust-root", root.toString());
        assertRefusal(env, "input.trust_root_invalid", null, TRUST_ROOT_FIX);
        // Only the prefix: the suffix is the JSON parser's own message, which differs per engine.
        String prefix = "the trust root '" + root + "' could not be loaded: " + root + " is not readable JSON: ";
        assertTrue(env.get("error").get("detail").asText().startsWith(prefix), env.toString());
    }

    @Test
    void aValidlySignedButRebrandedCatalogIsRefusedOverrideOrNot(@TempDir Path work) throws Exception {
        CatalogKey key = CatalogKey.create(work, "rebrand");
        Path dir = unsignedCatalogCopy(work, "rebrand-cat");
        Path yaml = dir.resolve("catalog.yaml");
        Files.writeString(yaml, Files.readString(yaml).replaceFirst("(?m)^id: eu-ai-act$", "id: eu-ai-act-rebrand"));
        key.signDir(dir);
        for (boolean override : new boolean[] {false, true}) {
            List<String> args = new ArrayList<>(List.of(
                    "--catalog-dir", dir.toString(), "--trust-root", key.trustRoot().toString()));
            if (override) {
                args.add("--allow-unverified-catalog");
            }
            assertRefusal(byoAssess(work, args.toArray(new String[0])), "input.catalog_mismatch",
                    "a --catalog-dir carries 'eu-ai-act-rebrand@2026.09', which --catalog did not request "
                            + "('eu-ai-act@2026.09').",
                    "pass --catalog-dir for the catalog you named, or name the id@version the directory carries.");
        }
    }

    @Test
    void assessRefusesATrustRootWhoseTopLevelIsNotAnObject(@TempDir Path work) throws Exception {
        Path root = work.resolve("array-root.json");
        Files.writeString(root, "[]");
        JsonNode env = byoAssess(work, "--trust-root", root.toString(), "--allow-unverified-catalog",
                "--catalog-dir", unsignedCatalogCopy(work, "unsigned-cat").toString());
        assertRefusal(env, "input.trust_root_invalid",
                "the trust root '" + root + "' could not be loaded: " + root + " does not hold a trust-root object",
                TRUST_ROOT_FIX);
    }

    @Test
    void scanTopLevelReadsTheTokensBeforeTheCommandAsPythonDoes() {
        // 18.108: every branch of Python's _scan_top_level.
        assertTrue(Cli.scanTopLevel(new String[] {}).help());
        assertTrue(Cli.scanTopLevel(new String[] {"--no-such-flag", "-h"}).help());
        assertTrue(Cli.scanTopLevel(new String[] {"-h", "-V"}).help());
        assertTrue(Cli.scanTopLevel(new String[] {"--version"}).version());
        Cli.TopLevel run = Cli.scanTopLevel(new String[] {"--json", "--debug", "version", "--quiet"});
        assertEquals("version", run.command());
        assertArrayEquals(new String[] {"--quiet"}, run.rest());
        assertEquals(Set.of("--json", "--debug"), run.globals());
        assertEquals("version", Cli.scanTopLevel(new String[] {"--", "version"}).command());
        String[][] refusals = {
            {"'-V' takes no other arguments.", "-V", "extra"},
            {"'--version' takes no other arguments.", "--json", "--version"},
            {"unrecognized flag '-hx'.", "-hx", "assess"},
            {"flag '--json' takes no value.", "--json=1", "version"},
            {"unrecognized flag '--no-such-flag'.", "--no-such-flag", "version"},
            {"no command given after '--'.", "--"},
            {"no command given.", "--json"},
            {"unrecognized command 'nope'.", "nope"},
            {"unrecognized command 'digest-tree'.", "digest-tree", "x"},
        };
        for (String[] row : refusals) {
            String[] argv = Arrays.copyOfRange(row, 1, row.length);
            InputError exc = org.junit.jupiter.api.Assertions.assertThrows(
                    InputError.class, () -> Cli.scanTopLevel(argv), String.join(" ", argv));
            assertEquals("input.unknown_command", exc.key);
            assertEquals(row[0], exc.reason);
        }
    }
}
