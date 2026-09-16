package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.UUID;

/**
 * Render the report artifacts from assertions (SPEC §9). {@code assertions.json} is written in RFC 8785
 * canonical form (byte-identical across engines); the human report (md/html), OSCAL Assessment Results,
 * SARIF, and role-aware evidence packs are rendered from it, and the reproducibility manifest records
 * the digest of every input and output. DC-5 is enforced before anything is written. A faithful port.
 */
public final class Report {
    private Report() {}

    private static final String ZERO_DIGEST = "sha256:" + "0".repeat(64);
    private static final byte[] NAMESPACE_URL = uuidToBytes(UUID.fromString("6ba7b811-9dad-11d1-80b4-00c04fd430c8"));

    private static final Map<String, String> SARIF_LEVEL = Map.of(
            "non-conformant", "error", "partial", "warning", "insufficient_evidence", "warning");
    private static final Map<String, String> OSCAL_STATE = Map.of(
            "conformant", "satisfied",
            "non-conformant", "not-satisfied",
            "partial", "not-satisfied",
            "not_applicable", "not-satisfied",
            "not_assessed", "not-satisfied",
            "insufficient_evidence", "not-satisfied");

    private static final String NON_DETERMINATION =
            "This statement reports conformance to the named catalog as evaluated by the Agent Conformance "
                    + "Engine over the named evidence and observation window. It is not a legal compliance "
                    + "determination.";

    private static byte[] uuidToBytes(UUID u) {
        ByteBuffer bb = ByteBuffer.allocate(16);
        bb.putLong(u.getMostSignificantBits());
        bb.putLong(u.getLeastSignificantBits());
        return bb.array();
    }

    /** RFC 4122 version-5 (SHA-1) UUID over the URL namespace and {@code name}, matching {@code uuid.uuid5}. */
    static String uuid5(String name) {
        try {
            MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
            sha1.update(NAMESPACE_URL);
            byte[] hash = sha1.digest(name.getBytes(StandardCharsets.UTF_8));
            byte[] b = java.util.Arrays.copyOf(hash, 16);
            b[6] = (byte) ((b[6] & 0x0F) | 0x50); // version 5
            b[8] = (byte) ((b[8] & 0x3F) | 0x80); // IETF variant
            long msb = 0;
            long lsb = 0;
            for (int i = 0; i < 8; i++) {
                msb = (msb << 8) | (b[i] & 0xFF);
            }
            for (int i = 8; i < 16; i++) {
                lsb = (lsb << 8) | (b[i] & 0xFF);
            }
            return new UUID(msb, lsb).toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-1 unavailable", e);
        }
    }

    private static String uuid(String... parts) {
        return uuid5("agentce:" + String.join(":", parts));
    }

    private static String digestBytes(byte[] data) {
        return "sha256:" + Canonical.sha256Hex(data);
    }

    private static String packageDigest() {
        return "sha256:" + Canonical.sha256Hex((Version.ENGINE_NAME + ":" + Version.ENGINE_VERSION).getBytes(StandardCharsets.UTF_8));
    }

    private static String safe(String name) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            out.append(Character.isLetterOrDigit(c) || c == '-' || c == '.' || c == '_' ? c : '_');
        }
        return out.toString();
    }

    private static String outcomeLabel(Map<String, String> cat, String outcome) {
        return cat.getOrDefault("outcome." + outcome, outcome);
    }

    private static String esc(String s) {
        return s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&#x27;");
    }

    private static List<Assertions.Assertion> sortedBySubjectControl(List<Assertions.Assertion> assertions) {
        List<Assertions.Assertion> sorted = new ArrayList<>(assertions);
        sorted.sort((a, b) -> {
            int c = a.subject.compareTo(b.subject);
            return c != 0 ? c : a.control.compareTo(b.control);
        });
        return sorted;
    }

    public static String renderReportMd(List<Assertions.Assertion> assertions, Map<String, Integer> counts, String language) {
        Map<String, String> cat = Messages.catalogue(language);
        List<String> lines = new ArrayList<>();
        lines.add("# " + cat.get("report.title"));
        lines.add("");
        lines.add("## " + cat.get("report.summary_heading"));
        lines.add("");
        for (Map.Entry<String, Integer> e : counts.entrySet()) {
            lines.add("- " + outcomeLabel(cat, e.getKey()) + ": " + e.getValue());
        }
        lines.add("");
        lines.add("## " + cat.get("report.assertions_heading"));
        lines.add("");
        if (assertions.isEmpty()) {
            lines.add("_" + cat.get("report.no_controls") + "_");
        }
        for (Assertions.Assertion a : sortedBySubjectControl(assertions)) {
            lines.add("- `" + a.control + "` @ `" + a.subject + "` -> **" + outcomeLabel(cat, a.outcome) + "** "
                    + "(rung " + a.rung + ", " + a.mode + "; " + a.population[1] + "/" + a.population[0] + " failed)");
        }
        return String.join("\n", lines) + "\n";
    }

    private static final String HTML_STYLE =
            "body{font-family:system-ui,sans-serif;margin:2rem;color:#111;background:#fff;line-height:1.5}"
                    + "h1{font-size:1.5rem}h2{font-size:1.2rem;margin-top:1.5rem}"
                    + "table{border-collapse:collapse;width:100%}"
                    + "th,td{border:1px solid #999;padding:.35rem .5rem;text-align:left}"
                    + "th{background:#f0f0f0}caption{text-align:left;font-weight:bold;margin-bottom:.5rem}"
                    + "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}"
                    + "@media print{@page{size:A4;margin:1.5cm}body{margin:0}@page :first{size:letter}"
                    + "table{page-break-inside:auto}tr{page-break-inside:avoid}}";

    public static String renderReportHtml(List<Assertions.Assertion> assertions, Map<String, Integer> counts, String language) {
        Map<String, String> cat = Messages.catalogue(language);
        String title = esc(cat.get("report.title"));
        StringBuilder summary = new StringBuilder();
        for (Map.Entry<String, Integer> e : counts.entrySet()) {
            summary.append("<li>").append(esc(outcomeLabel(cat, e.getKey()))).append(": ").append(e.getValue()).append("</li>");
        }
        StringBuilder rows = new StringBuilder();
        for (Assertions.Assertion a : sortedBySubjectControl(assertions)) {
            rows.append("<tr><td>").append(esc(a.control)).append("</td><td>").append(esc(a.subject)).append("</td>")
                    .append("<td>").append(esc(outcomeLabel(cat, a.outcome))).append("</td></tr>");
        }
        String bodyRows = rows.length() > 0
                ? rows.toString()
                : "<tr><td colspan=\"3\">" + esc(cat.get("report.no_controls")) + "</td></tr>";
        return "<!doctype html><html lang=\"" + esc(language) + "\"><head><meta charset=\"utf-8\">"
                + "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                + "<meta http-equiv=\"Content-Security-Policy\" "
                + "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'\">"
                + "<title>" + title + "</title><style>" + HTML_STYLE + "</style></head><body>"
                + "<main><h1>" + title + "</h1>"
                + "<section aria-labelledby=\"summary\"><h2 id=\"summary\">"
                + esc(cat.get("report.summary_heading")) + "</h2><ul>" + summary + "</ul></section>"
                + "<section aria-labelledby=\"assertions\"><h2 id=\"assertions\">"
                + esc(cat.get("report.assertions_heading")) + "</h2>"
                + "<table><caption>" + esc(cat.get("report.assertions_heading")) + "</caption>"
                + "<thead><tr><th scope=\"col\">Control</th><th scope=\"col\">Subject</th>"
                + "<th scope=\"col\">Outcome</th></tr></thead>"
                + "<tbody>" + bodyRows + "</tbody></table></section>"
                + "<footer><p>" + esc(cat.get("report.affected_persons")) + "</p></footer>"
                + "</main></body></html>\n";
    }

    public static ObjectNode renderOscal(List<Assertions.Assertion> assertions) {
        ObjectNode root = Json.nodes().objectNode();
        ObjectNode ar = root.putObject("assessment-results");
        ar.put("uuid", uuid("assessment-results"));
        ObjectNode metadata = ar.putObject("metadata");
        metadata.put("title", "AgentCE Assessment Results");
        metadata.put("version", Version.ENGINE_VERSION);
        metadata.put("oscal-version", "1.1.2");
        ArrayNode results = ar.putArray("results");
        ObjectNode result = results.addObject();
        result.put("uuid", uuid("result"));
        result.put("title", "AgentCE structural assessment");
        ArrayNode findings = result.putArray("findings");
        for (Assertions.Assertion a : sortedBySubjectControl(assertions)) {
            ObjectNode finding = findings.addObject();
            finding.put("uuid", uuid("finding", a.control, a.subject));
            finding.put("title", a.control + " for " + a.subject);
            ObjectNode target = finding.putObject("target");
            target.put("type", "objective-id");
            target.put("target-id", a.control);
            ObjectNode status = target.putObject("status");
            status.put("state", OSCAL_STATE.getOrDefault(a.outcome, "not-satisfied"));
            status.put("reason", a.outcome);
        }
        return root;
    }

    public static ObjectNode renderSarif(List<Assertions.Assertion> assertions) {
        ObjectNode root = Json.nodes().objectNode();
        root.put("version", "2.1.0");
        ArrayNode runs = root.putArray("runs");
        ObjectNode run = runs.addObject();
        ObjectNode tool = run.putObject("tool");
        ObjectNode driver = tool.putObject("driver");
        driver.put("name", Version.ENGINE_NAME);
        driver.put("version", Version.ENGINE_VERSION);
        ArrayNode rules = driver.putArray("rules");
        TreeSet<String> controlIds = new TreeSet<>();
        for (Assertions.Assertion a : assertions) {
            controlIds.add(a.control);
        }
        for (String control : controlIds) {
            rules.addObject().put("id", control);
        }
        ArrayNode results = run.putArray("results");
        for (Assertions.Assertion a : sortedBySubjectControl(assertions)) {
            if (SARIF_LEVEL.containsKey(a.outcome)) {
                ObjectNode r = results.addObject();
                r.put("ruleId", a.control);
                r.put("level", SARIF_LEVEL.get(a.outcome));
                r.putObject("message").put("text", a.control + " on " + a.subject + ": " + a.outcome);
            }
        }
        return root;
    }

    public static ObjectNode renderEvidencePack(String subject, List<Assertions.Assertion> assertions, String role) {
        ObjectNode pack = Json.nodes().objectNode();
        pack.put("subject", subject);
        ArrayNode assertionsArr = pack.putArray("assertions");
        TreeSet<String> allEvidence = new TreeSet<>(Json::byteCompare);
        for (Assertions.Assertion a : assertions) {
            ObjectNode row = assertionsArr.addObject();
            row.put("control", a.control);
            row.put("outcome", a.outcome);
            row.put("mode", a.mode);
            TreeSet<String> refs = new TreeSet<>(Json::byteCompare);
            for (Assertions.EvidencePointer e : a.evidence) {
                refs.add(e.ref);
                allEvidence.add(e.ref);
            }
            ArrayNode evidence = row.putArray("evidence");
            refs.forEach(evidence::add);
        }
        ArrayNode packEvidence = pack.putArray("evidence");
        allEvidence.forEach(packEvidence::add);
        if (role != null) {
            pack.put("role", role);
        }
        return pack;
    }

    private static String now() {
        return ZonedDateTime.now(ZoneOffset.UTC).format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'"));
    }

    public static ObjectNode buildManifest(
            String bundleDigest, List<String> catalogs, Map<String, String> outputs, String operator,
            List<String> invocation, List<String> supersedes, String reportLanguage) {
        String packageDigest = packageDigest();
        String host = Canonical.sha256Hex(
                (System.getProperty("os.name") + "|" + System.getProperty("os.arch") + "|" + packageDigest)
                        .getBytes(StandardCharsets.UTF_8));
        ObjectNode manifest = Json.nodes().objectNode();
        manifest.put("agentce_manifest_version", 1);
        ObjectNode engine = manifest.putObject("engine");
        engine.put("impl", Version.ENGINE_NAME);
        engine.put("version", Version.ENGINE_VERSION);
        engine.put("spec_version", Version.SPEC_VERSION);
        engine.put("package_digest", packageDigest);
        ObjectNode inputs = manifest.putObject("inputs");
        inputs.put("bundle_digest", bundleDigest);
        ArrayNode catalogRefs = inputs.putArray("catalogs");
        for (String entry : catalogs) {
            int at = entry.indexOf('@');
            String cid = at >= 0 ? entry.substring(0, at) : entry;
            String version = at >= 0 ? entry.substring(at + 1) : "";
            ObjectNode ref = catalogRefs.addObject();
            ref.put("id", cid);
            ref.put("version", version.isEmpty() ? "0" : version);
            ref.put("digest", ZERO_DIGEST);
        }
        ObjectNode outputsNode = manifest.putObject("outputs");
        List<String> outputKeys = new ArrayList<>(outputs.keySet());
        for (String key : outputKeys) {
            outputsNode.put(key, outputs.get(key));
        }
        ObjectNode runNode = manifest.putObject("run");
        runNode.put("started_at", now());
        runNode.put("operator", operator);
        runNode.put("host_fingerprint", "sha256:" + host);
        ArrayNode inv = runNode.putArray("invocation");
        invocation.forEach(inv::add);
        runNode.put("report_language", reportLanguage);
        if (!supersedes.isEmpty()) {
            ArrayNode sup = manifest.putArray("supersedes");
            supersedes.forEach(sup::add);
        }
        return manifest;
    }

    /** Write every report artifact for {@code assertions} and return the reproducibility manifest. */
    public static ObjectNode writeReport(
            Path outDir, List<Assertions.Assertion> assertions, String bundleDigest, List<String> catalogs,
            String operator, List<String> invocation, List<String> supersedes, String reportLanguage) {
        Assertions.checkDc5(assertions);
        try {
            Files.createDirectories(outDir);
            Map<String, String> outputs = new LinkedHashMap<>();

            ArrayNode assertionsJson = Json.nodes().arrayNode();
            for (Assertions.Assertion a : assertions) {
                assertionsJson.add(a.toJson());
            }
            outputs.put("assertions.json", writeJson(outDir, "assertions.json", assertionsJson));

            Map<String, Integer> counts = Assertions.aggregate(assertions);
            outputs.put("report.md", writeText(outDir, "report.md", renderReportMd(assertions, counts, reportLanguage)));
            outputs.put("report.html", writeText(outDir, "report.html", renderReportHtml(assertions, counts, reportLanguage)));
            outputs.put("oscal-ar.json", writeJson(outDir, "oscal-ar.json", renderOscal(assertions)));
            outputs.put("results.sarif", writeJson(outDir, "results.sarif", renderSarif(assertions)));

            TreeSet<String> subjects = new TreeSet<>(Json::byteCompare);
            for (Assertions.Assertion a : assertions) {
                subjects.add(a.subject);
            }
            for (String subject : subjects) {
                List<Assertions.Assertion> forSubject = new ArrayList<>();
                for (Assertions.Assertion a : assertions) {
                    if (a.subject.equals(subject)) {
                        forSubject.add(a);
                    }
                }
                ObjectNode pack = renderEvidencePack(subject, forSubject, null);
                String rel = "packs/" + safe(subject) + "/pack.json";
                byte[] data = Canonical.canonicalize(pack);
                Path path = outDir.resolve(rel);
                Files.createDirectories(path.getParent());
                Files.write(path, data);
                outputs.put(rel, digestBytes(data));
            }

            ObjectNode manifest = buildManifest(
                    bundleDigest, catalogs, outputs, operator, invocation, supersedes, reportLanguage);
            Files.write(outDir.resolve("manifest.json"), Json.pretty(manifest).getBytes(StandardCharsets.UTF_8));
            return manifest;
        } catch (IOException e) {
            throw new IllegalStateException("cannot write report to " + outDir + ": " + e.getMessage(), e);
        }
    }

    private static String writeJson(Path outDir, String name, JsonNode obj) throws IOException {
        byte[] data = Canonical.canonicalize(obj);
        Files.write(outDir.resolve(name), data);
        return digestBytes(data);
    }

    private static String writeText(Path outDir, String name, String text) throws IOException {
        byte[] data = text.getBytes(StandardCharsets.UTF_8);
        Files.write(outDir.resolve(name), data);
        return digestBytes(data);
    }
}
