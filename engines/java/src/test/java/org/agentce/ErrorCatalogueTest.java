package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The error-key registry, its loader, the trust-root shape causes and the digest-read key (18.80), the
 * same behaviour Python's {@code tests/test_error_catalogue.py} and TypeScript's {@code
 * src/errorCatalogue.test.ts} pin; {@code VG-I18N-ERROR-CATALOGUE} runs all three.
 */
class ErrorCatalogueTest {
    private static List<String> engineSources() throws IOException {
        Path dir = TestPaths.repoRoot().resolve("engines/java/src/main/java/org/agentce");
        List<String> out = new ArrayList<>();
        try (Stream<Path> files = Files.list(dir)) {
            for (Path p : files.filter(f -> f.toString().endsWith(".java")).sorted().toList()) {
                out.add(Files.readString(p));
            }
        }
        return out;
    }

    @Test
    void theRegistryHoldsEveryErrorsPairOfTheSpecCatalogue() throws IOException {
        JsonNode spec = Json.parseFile(TestPaths.repoRoot().resolve("spec/i18n/messages.en.json"));
        for (Map.Entry<String, ErrorCatalogue.Entry> e : ErrorCatalogue.messageKeys().entrySet()) {
            assertEquals(spec.get("errors." + e.getKey() + ".cause").asText(), e.getValue().cause());
            assertEquals(spec.get("errors." + e.getKey() + ".fix").asText(), e.getValue().fix());
        }
        assertTrue(ErrorCatalogue.messageKeys().containsKey("input.digest_unreadable"));
    }

    @Test
    void catalogueGapsIsEmptyOverTheEngineSources() throws IOException {
        assertEquals(List.of(), ErrorCatalogue.catalogueGaps(engineSources()));
    }

    @Test
    void catalogueGapsNamesARaisedKeyTheCatalogueLacks() {
        assertEquals(
                List.of("input.not_a_real_key: raised but not in the catalogue"),
                ErrorCatalogue.catalogueGaps(List.of("throw new InputError(\"input.not_a_real_key\", \"x\", \"y\");")));
    }

    @Test
    void theLoaderIsEmptyForAMissingFileAndThrowsOnACorruptOne() {
        assertEquals(Map.of(), Messages.parseCatalog("missing.json", null));
        assertEquals(Map.of(), Messages.loadCatalog("xx"));
        assertThrows(IllegalArgumentException.class, () -> Messages.parseCatalog("m.json", "{not json"));
        IllegalArgumentException nonObject =
                assertThrows(IllegalArgumentException.class, () -> Messages.parseCatalog("a.json", "[1, 2]"));
        assertTrue(nonObject.getMessage().contains("does not hold a message catalogue object"));
    }

    private static final String[][] SHAPES = {
        {"{\"keys\": \"x\"}", "keys is not a mapping of key id to key entry"},
        {"{\"keys\": {\"abc\": \"s\"}}", "keys entry 'abc' is not a mapping"},
        {"{\"keys\": {\"abc\": {\"identity\": \"x\"}}}", "keys entry 'abc' has no public_key"},
        {"{\"certificate_authorities\": \"x\"}", "certificate_authorities is not a mapping of id to entry"},
        {"{\"certificate_authorities\": {\"ca1\": \"x\"}}", "certificate_authorities entry 'ca1' is not a mapping"},
        {"{\"certificate_authorities\": {\"ca1\": {}}}", "certificate_authorities entry 'ca1' has no public_key"},
        {"{\"keys\": {\"a'b\\nc\": \"x\"}}", "keys entry \"a'b\\nc\" is not a mapping"},
    };

    @Test
    void eachMalformedTrustRootShapeHasOneStableCause(@TempDir Path dir) throws IOException {
        Path path = dir.resolve("root.json");
        for (String[] shape : SHAPES) {
            Files.writeString(path, shape[0]);
            IllegalArgumentException e =
                    assertThrows(IllegalArgumentException.class, () -> Verify.loadTrustRoot(path), shape[0]);
            assertEquals(path + " is not a usable trust root: " + shape[1], e.getMessage());
        }
    }

    @Test
    void aDigestReadThatFailsRaisesDigestUnreadable(@TempDir Path dir) {
        InputError e = assertThrows(InputError.class, () -> Cli.digestOf(dir));
        assertEquals("input.digest_unreadable", e.key);
        assertTrue(e.reason.startsWith(dir + " could not be read to record its digest: "), e.reason);
        assertEquals("make the file readable, then re-run.", e.fix);
    }
}
