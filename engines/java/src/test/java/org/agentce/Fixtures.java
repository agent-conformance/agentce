package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;

/** Shared fixture helpers for the golden tests. */
final class Fixtures {
    private Fixtures() {}

    static final Path BASE = TestPaths.repoRoot().resolve("spec/catalogs/base/eu-ai-act");

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
        ObjectNode integrity = Json.nodes().objectNode();
        integrity.put("hash", "");
        integrity.put("prev", Integrity.GENESIS_PREV);
        integrity.put("stream", "s1");
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
        event.put("id", "e1");
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
        Files.createDirectories(root.resolve("events"));
        byte[] content = (Json.compact(event) + "\n").getBytes(StandardCharsets.UTF_8);
        Files.write(root.resolve("events/log.jsonl"), content);

        ObjectNode manifest = Json.nodes().objectNode();
        ArrayNode files = manifest.putArray("files");
        ObjectNode fileEntry = files.addObject();
        fileEntry.put("path", "events/log.jsonl");
        fileEntry.put("sha256", "sha256:" + Canonical.sha256Hex(content));
        ObjectNode sourceEntry = manifest.putArray("sources").addObject();
        sourceEntry.put("id", source);
        if (declaredClass != null) {
            sourceEntry.put("class", declaredClass);
        }
        Files.writeString(root.resolve("manifest.json"), Json.compact(manifest), StandardCharsets.UTF_8);
        return Bundle.load(root);
    }
}
