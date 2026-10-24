package org.agentce;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPairGenerator;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.io.TempDir;

/** The report-file reader contract (SPEC 8.7) against the shared case table {@code
 * spec/report/reader-cases.json}, the rows Python's and TypeScript's tests and {@code
 * tools/report_validate_parity_check.py} read too. Each case edits one file of a fresh report folder this
 * engine wrote itself (with the three formats it cannot emit added, see {@link
 * #writeFormatsThisEngineCannotEmit}) and runs {@code report --validate}, {@code
 * readiness}, {@code sign} or {@code diff} through the real CLI dispatcher. */
class ReaderCasesTest {
    private static final Path REPO = TestPaths.repoRoot();
    private static final Path QUICKSTART = REPO.resolve("corpus/quickstart");
    private static final List<JsonNode> CASES = new ArrayList<>();

    static {
        Json.parseFile(REPO.resolve("spec/report/reader-cases.json")).get("cases").forEach(CASES::add);
    }

    @TempDir
    static Path tmp;

    private static Path base;
    private static Path key;

    @BeforeAll
    static void writeBaseReport() throws Exception {
        base = tmp.resolve("base");
        JsonNode env = Fixtures.runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog", "eu-ai-act@2026.09",
                "--catalog-dir", REPO.resolve("spec/catalogs/base/eu-ai-act").toString(),
                "--out", base.toString());
        assertEquals(0, env.get("exit_code").asInt(), env.toString());
        writeFormatsThisEngineCannotEmit(base);
        key = tmp.resolve("key.pem");
        Files.writeString(key, Fixtures.toPem(
                "PRIVATE KEY", KeyPairGenerator.getInstance("Ed25519").generateKeyPair().getPrivate().getEncoded()));
    }

    /** This engine refuses {@code --emit} ({@code input.emit_unsupported}) and writes only its core
     * outputs, which already hold {@code report.md}, {@code report.html}, {@code oscal-ar.json} and {@code
     * results.sarif}. The three formats it has no writer for are written here from its own {@code
     * assertions.json}, in the shape the Python writer gives them (the case table's anchors: a {@code
     * report.csv} row {@code DAT-01,...}, a {@code testsuite name="agentce"}). */
    private static void writeFormatsThisEngineCannotEmit(Path dir) throws IOException {
        JsonNode assertions = Json.parseFile(dir.resolve("assertions.json"));
        StringBuilder csv = new StringBuilder("control,subject,outcome,control_version,rung,mode\n");
        StringBuilder junit = new StringBuilder("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<testsuite name=\"agentce\" tests=\""
                + assertions.size() + "\">");
        for (JsonNode a : assertions) {
            List<String> row = new ArrayList<>();
            for (String field : List.of("control", "subject", "outcome", "control_version", "rung", "mode")) {
                row.add(a.path(field).asText());
            }
            csv.append(String.join(",", row)).append('\n');
            junit.append("<testcase classname=\"").append(xmlEscape(a.path("subject").asText()))
                    .append("\" name=\"").append(xmlEscape(a.path("control").asText())).append("\"/>");
        }
        junit.append("</testsuite>\n");
        Files.writeString(dir.resolve("report.csv"), csv.toString());
        Files.writeString(dir.resolve("report.junit.xml"), junit.toString());
        Files.writeString(dir.resolve("oscal-ar.xml"), "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                + "<assessment-results xmlns=\"http://csrc.nist.gov/ns/oscal/1.0\"><metadata>"
                + "<title>AgentCE Assessment Results</title><oscal-version>1.1.2</oscal-version></metadata>"
                + "</assessment-results>\n");
    }

    private static String xmlEscape(String text) {
        return text.replace("&", "&amp;").replace("<", "&lt;").replace("\"", "&quot;");
    }

    private static byte[] hex(JsonNode edit, String field) {
        return HexFormat.of().parseHex(edit.path(field).asText(""));
    }

    private static int indexOf(byte[] data, byte[] find, int from) {
        outer:
        for (int i = from; i + find.length <= data.length; i++) {
            for (int k = 0; k < find.length; k++) {
                if (data[i + k] != find[k]) {
                    continue outer;
                }
            }
            return i;
        }
        return -1;
    }

    private static byte[] concat(byte[]... parts) {
        int length = 0;
        for (byte[] part : parts) {
            length += part.length;
        }
        byte[] out = new byte[length];
        int at = 0;
        for (byte[] part : parts) {
            System.arraycopy(part, 0, out, at, part.length);
            at += part.length;
        }
        return out;
    }

    /** One edit of the case table: write, prepend, append, replace (first occurrence) or replace_all. */
    static byte[] applyEdit(byte[] data, JsonNode edit) {
        byte[] content = hex(edit, "hex");
        String op = edit.get("op").asText();
        switch (op) {
            case "write":
                return content;
            case "prepend":
                return concat(content, data);
            case "append":
                return concat(data, content);
            case "replace":
            case "replace_all": {
                byte[] find = hex(edit, "find_hex");
                int at = indexOf(data, find, 0);
                if (at < 0) {
                    throw new AssertionError("find_hex not in the file: " + edit);
                }
                List<byte[]> parts = new ArrayList<>();
                int from = 0;
                while (at >= 0) {
                    parts.add(Arrays.copyOfRange(data, from, at));
                    parts.add(content);
                    from = at + find.length;
                    at = op.equals("replace_all") ? indexOf(data, find, from) : -1;
                }
                parts.add(Arrays.copyOfRange(data, from, data.length));
                return concat(parts.toArray(byte[][]::new));
            }
            default:
                throw new AssertionError("unknown edit op " + op);
        }
    }

    private static List<String> readinessFiles(Path dir) throws IOException {
        try (Stream<Path> listed = Files.list(dir)) {
            return listed.map(p -> p.getFileName().toString()).filter(n -> n.startsWith("report-readiness-")).toList();
        }
    }

    private static void run(JsonNode c) throws Exception {
        String id = c.get("id").asText();
        Path dir = tmp.resolve("case-" + id);
        Fixtures.copyTree(base, dir);
        Path file = dir.resolve(c.get("file").asText());
        byte[] data = Files.exists(file) ? Files.readAllBytes(file) : new byte[0];
        for (JsonNode edit : c.get("edits")) {
            data = applyEdit(data, edit);
        }
        Files.write(file, data);
        byte[] claimBefore = Files.readAllBytes(dir.resolve("claim.json"));
        String command = c.get("command").asText();
        JsonNode env = switch (command) {
            case "report-validate" -> Fixtures.runJson("report", "--validate", dir.toString());
            case "readiness" -> Fixtures.runJson("readiness", dir.toString());
            case "sign" -> Fixtures.runJson(
                    "sign", dir.toString(), "--as", "claimant", "--profile", "kms", "--key", key.toString());
            case "diff" -> Fixtures.runJson(
                    "diff", base.resolve("assertions.json").toString(), dir.resolve("assertions.json").toString());
            default -> throw new AssertionError("unknown command " + command);
        };
        int exit = c.get("exit").asInt();
        assertEquals(exit, env.get("exit_code").asInt(), id + ": " + env);
        if (exit == 0) {
            return;
        }
        String names = c.get("names").asText();
        if (command.equals("report-validate")) {
            assertEquals(c.get("key").asText(), env.path("message_key").asText(), id + ": " + env);
            boolean named = false;
            for (JsonNode problem : env.get("problems")) {
                named |= problem.asText().contains(names);
            }
            assertTrue(named, id + ": no problem line names " + names + ": " + env.get("problems"));
            return;
        }
        assertEquals(c.get("key").asText(), env.path("error").path("message_key").asText(), id + ": " + env);
        String detail = env.path("error").path("detail").asText();
        assertTrue(detail.contains(names), id + ": the error does not name " + names + ": " + detail);
        if (command.equals("readiness") || command.equals("sign")) {
            assertEquals(List.of(), readinessFiles(dir), id);
        }
        if (command.equals("sign")) {
            assertFalse(Files.exists(dir.resolve("signatures")), id + ": a signature was written");
            if (!c.get("file").asText().equals("claim.json")) {
                assertArrayEquals(claimBefore, Files.readAllBytes(dir.resolve("claim.json")), id);
            }
        }
    }

    @TestFactory
    List<DynamicTest> cases() {
        List<DynamicTest> out = new ArrayList<>();
        for (JsonNode c : CASES) {
            out.add(DynamicTest.dynamicTest(c.get("id").asText(), () -> run(c)));
        }
        return out;
    }

    /** The unedited folder is inside the contract for every command the table runs, so each case's
     * refusal comes from its own edit. */
    @Test
    void theUneditedReportPassesEveryCommand() throws Exception {
        Path dir = tmp.resolve("clean");
        Fixtures.copyTree(base, dir);
        assertEquals(0, Fixtures.runJson("report", "--validate", dir.toString()).get("exit_code").asInt());
        JsonNode readiness = Fixtures.runJson("readiness", dir.toString());
        assertEquals(0, readiness.get("exit_code").asInt(), readiness.toString());
        JsonNode signed = Fixtures.runJson(
                "sign", dir.toString(), "--as", "claimant", "--profile", "kms", "--key", key.toString());
        assertEquals(0, signed.get("exit_code").asInt(), signed.toString());
        assertTrue(Files.isDirectory(dir.resolve("signatures")));
        JsonNode diff = Fixtures.runJson(
                "diff", base.resolve("assertions.json").toString(), dir.resolve("assertions.json").toString());
        assertEquals(0, diff.get("exit_code").asInt(), diff.toString());
    }

    @Test
    void theCaseTableIsSortedWithUniqueIds() {
        List<String> ids = CASES.stream().map(c -> c.get("id").asText()).toList();
        assertEquals(ids.stream().sorted(Json::byteCompare).distinct().toList(), ids);
    }
}
