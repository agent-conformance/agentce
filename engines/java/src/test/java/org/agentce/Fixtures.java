package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import org.junit.jupiter.api.Assertions;

/** Shared fixture helpers for the golden tests. */
final class Fixtures {
    private Fixtures() {}

    static final Path BASE = TestPaths.repoRoot().resolve("spec/catalogs/base/eu-ai-act");
    static final Path CONDUCT = TestPaths.repoRoot().resolve("spec/catalogs/overlays/conduct");

    /** Runs the real CLI dispatcher ({@link Cli#run}) with {@code --json} appended, captures
     * stdout, and parses the envelope -- asserting the process exit code matches the envelope's own
     * {@code exit_code}. */
    static JsonNode runJson(String... args) {
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
        Assertions.assertEquals(
                exit, envelope.get("exit_code").asInt(), "process exit code must match the envelope");
        return envelope;
    }

    /** Chmods {@code dir} to {@code r-xr-xr-x} and reports whether that actually removed this JVM's
     * write access (it does not when running as root, where a mode-555 directory is still
     * writable -- nothing to prove there). */
    static boolean makeUnwritable(Path dir) throws IOException {
        Files.setPosixFilePermissions(dir, PosixFilePermissions.fromString("r-xr-xr-x"));
        return !Files.isWritable(dir);
    }

    // --- Ed25519 PEM/PAE test helpers, shared between SignTest and CliTest's sign section
    // (item 18.26): both build and PEM-armor Ed25519/RSA/EC test keys and verify a DSSE signature
    // against a raw public key the same way. ---------------------------------------------------

    private static final byte[] ED25519_SPKI_PREFIX = HexFormat.of().parseHex("302a300506032b6570032100");

    /** PEM-armors {@code der} under {@code label} ({@code -----BEGIN <label>-----}, 64-column body,
     * {@code -----END <label>-----}). */
    static String toPem(String label, byte[] der) {
        String b64 = Base64.getEncoder().encodeToString(der);
        StringBuilder sb = new StringBuilder();
        sb.append("-----BEGIN ").append(label).append("-----\n");
        for (int i = 0; i < b64.length(); i += 64) {
            sb.append(b64, i, Math.min(i + 64, b64.length())).append("\n");
        }
        sb.append("-----END ").append(label).append("-----\n");
        return sb.toString();
    }

    /** Rebuilds a JDK {@link PublicKey} from the 32 raw Ed25519 public-key bytes {@code keyidFor}/
     * {@code publicKeyB64} work with, by prefixing the fixed RFC 8410 SPKI DER header. */
    static PublicKey edPublicKeyObject(byte[] rawPublicKey) throws Exception {
        byte[] spki = new byte[ED25519_SPKI_PREFIX.length + rawPublicKey.length];
        System.arraycopy(ED25519_SPKI_PREFIX, 0, spki, 0, ED25519_SPKI_PREFIX.length);
        System.arraycopy(rawPublicKey, 0, spki, ED25519_SPKI_PREFIX.length, rawPublicKey.length);
        return KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(spki));
    }

    /** Verifies an Ed25519 signature against a raw 32-byte public key -- the real cryptographic
     * round trip {@code KmsSigner.sign}/{@code signStatement}'s own tests assert against. */
    static boolean edVerify(byte[] data, byte[] rawPublicKey, byte[] sig) throws Exception {
        Signature verifier = Signature.getInstance("Ed25519");
        verifier.initVerify(edPublicKeyObject(rawPublicKey));
        verifier.update(data);
        return verifier.verify(sig);
    }

    /** Read a JSON-lines file into a list of nodes (blank lines skipped). */
    static List<JsonNode> readJsonl(Path path) throws IOException {
        List<JsonNode> out = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            if (!line.strip().isEmpty()) {
                out.add(Json.parse(line));
            }
        }
        return out;
    }

    static List<JsonNode> toList(JsonNode array) {
        List<JsonNode> out = new ArrayList<>();
        if (array != null) {
            array.forEach(out::add);
        }
        return out;
    }

    // --- Ingest-bundle builders (item 18.31: a schema-valid event plus the on-disk bundle it
    // belongs to, for tests that need ingest()'s real class-correction/class-mismatch behaviour over
    // a declared or undeclared source, not just the fixed committed ingest-bundle fixture). ----------

    /** A minimal, schema-valid {@code SessionStart} event from {@code source}, claiming {@code
     * agentClass}, with an export-chained integrity block whose hash is already correctly set. */
    static ObjectNode buildIngestEvent(String source, String agentClass) {
        return buildIngestEvent(source, agentClass, "e1", "s1");
    }

    /** As {@link #buildIngestEvent(String, String)}, with an explicit event {@code id} and integrity
     * {@code stream} so a test can build several independent events without id/stream collisions. */
    static ObjectNode buildIngestEvent(String source, String agentClass, String id, String stream) {
        ObjectNode integrity = Json.nodes().objectNode();
        integrity.put("hash", "");
        integrity.put("prev", Integrity.GENESIS_PREV);
        integrity.put("stream", stream);
        integrity.put("strength", "export_chained");
        ObjectNode data = Json.nodes().objectNode();
        data.put("@context", "https://agent-conformance.org/contexts/evidence/v1");
        data.put("@type", "SessionStart");
        data.set("integrity", integrity);
        data.put("session_id", "sess-1");
        ObjectNode event = Json.nodes().objectNode();
        event.put("agentcesourceclass", agentClass);
        event.set("data", data);
        event.put("datacontenttype", "application/ld+json");
        event.put("id", id);
        event.put("source", source);
        event.put("specversion", "1.0");
        event.put("subject", "spiffe://corp/agents/test");
        event.put("time", "2026-05-01T08:00:00.000Z");
        event.put("type", "org.agent-conformance.evidence.SessionStart.v1");
        integrity.put("hash", Integrity.recomputeHash(event));
        return event;
    }

    /**
     * Writes a bundle at {@code root} with one event and a manifest {@code sources} entry for {@code
     * source}: {@code declaredClass} non-null declares that class; {@code null} declares the source
     * with no class at all (undeclared, SPEC §6.4).
     */
    static Bundle writeIngestBundle(Path root, JsonNode event, String source, String declaredClass)
            throws IOException {
        ArrayNode sources = Json.nodes().arrayNode();
        ObjectNode sourceEntry = sources.addObject();
        sourceEntry.put("id", source);
        if (declaredClass != null) {
            sourceEntry.put("class", declaredClass);
        }
        return writeIngestBundle(root, List.of(event), sources);
    }

    /**
     * As {@link #writeIngestBundle(Path, JsonNode, String, String)}, for several events sharing one
     * bundle with a manifest {@code sources} array built by the caller -- letting a test declare a raw
     * JSON {@code class} value (including {@code null} or a non-string) that a single {@code String}
     * parameter can't express.
     */
    static Bundle writeIngestBundle(Path root, List<JsonNode> events, ArrayNode sources) throws IOException {
        Files.createDirectories(root.resolve("events"));
        StringBuilder lines = new StringBuilder();
        for (JsonNode event : events) {
            lines.append(Json.compact(event)).append("\n");
        }
        byte[] content = lines.toString().getBytes(StandardCharsets.UTF_8);
        Files.write(root.resolve("events/log.jsonl"), content);

        ObjectNode manifest = Json.nodes().objectNode();
        ArrayNode files = manifest.putArray("files");
        ObjectNode fileEntry = files.addObject();
        fileEntry.put("path", "events/log.jsonl");
        fileEntry.put("sha256", "sha256:" + Canonical.sha256Hex(content));
        manifest.set("sources", sources);
        Files.writeString(root.resolve("manifest.json"), Json.compact(manifest), StandardCharsets.UTF_8);
        return Bundle.load(root);
    }
}
