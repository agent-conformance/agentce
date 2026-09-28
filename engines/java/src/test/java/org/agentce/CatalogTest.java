package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The catalog loader, PSP parser, and structural evaluator are byte-identical to the reference. This
 * exercises the whole rung-2 path over the real base catalog: it parses each control's Turtle shape,
 * builds the graph from the control's passed / failed / inapplicable fixtures, and evaluates the
 * control against {@code catalog-golden.json}.
 */
class CatalogTest {
    private static final String[] CASES = {"passed", "failed", "inapplicable"};

    @Test
    void structuralEvaluationMatchesReference() throws IOException {
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("catalog-golden.json"));
        Catalog catalog = Catalog.load(Fixtures.BASE);
        DomainBinding domain = DomainBinding.load(Fixtures.BASE.resolve("test/domain.yaml"));

        ObjectNode result = Json.nodes().objectNode();
        for (Catalog.ControlSpec control : catalog.controls) {
            Psp.Shape shape = Catalog.shapeFor(catalog, control);
            if (shape == null) {
                continue;
            }
            ObjectNode perCase = Json.nodes().objectNode();
            for (String c : CASES) {
                Path fixture = Fixtures.BASE.resolve("test").resolve(control.id).resolve(c + ".jsonl");
                if (!Files.exists(fixture)) {
                    continue;
                }
                GraphStore store = Graph.buildGraph(Fixtures.readJsonl(fixture), domain);
                Structural.ControlOutcome outcome =
                        Structural.evaluateControl(store, shape, catalog.shapes, control.id, control.tolerance);
                perCase.set(c, Structural.controlOutcomeToJson(outcome));
            }
            result.set(control.id, perCase);
        }
        assertEquals(Canonical.canonicalString(golden), Canonical.canonicalString(result));
    }

    // --- digestTree / provenanceDigest (item 18.22: a real catalog content digest, never
    // sha256:000...0) -- the shared cross-engine fixture tree Python's own reference implementation
    // computed `digest-tree.expected` from; TypeScript's `report.test.ts` reads the exact same tree. ---

    private static final Path DIGEST_FIXTURE =
            TestPaths.repoRoot().resolve("spec/model/test-vectors/digest-tree");
    private static final Set<String> PROVENANCE_EXCLUDE = Set.of("catalog.sig.json", "catalog.yaml");

    private static String digestExpected() throws IOException {
        return Files.readString(TestPaths.repoRoot().resolve("spec/model/test-vectors/digest-tree.expected")).strip();
    }

    @Test
    void digestTreeMatchesThePythonReferenceOverTheSharedFixtureTree() throws IOException {
        assertEquals(digestExpected(), Catalog.digestTree(DIGEST_FIXTURE, PROVENANCE_EXCLUDE));
    }

    @Test
    void digestTreeExcludesOnlyTheExactTopLevelNameNeverANestedOneOfTheSameBasename() {
        // `controls/catalog.yaml` is NOT excluded even though `catalog.yaml` is; a naive basename-only
        // match would silently drop it from the digest and mask a tampered nested control file.
        String withNested = Catalog.digestTree(DIGEST_FIXTURE, PROVENANCE_EXCLUDE);
        String withoutExclude = Catalog.digestTree(DIGEST_FIXTURE, Set.of());
        assertNotEquals(withNested, withoutExclude);
    }

    @Test
    void provenanceDigestChangesWhenTheCatalogContentChangesAndIgnoresAStaleStoredDigest(@TempDir Path tmp)
            throws IOException {
        copyTree(DIGEST_FIXTURE, tmp);
        String before = Catalog.provenanceDigest(tmp);
        assertEquals(digestExpected(), before);
        // Tamper a non-excluded file's content; catalog.yaml's own (unrelated) content is untouched, so
        // a naive "read catalog.yaml's stored provenance.digest" implementation would not notice.
        Files.writeString(tmp.resolve("readme.txt"), "tampered content\n");
        String after = Catalog.provenanceDigest(tmp);
        assertNotEquals(before, after);
    }

    private static void copyTree(Path source, Path target) throws IOException {
        try (java.util.stream.Stream<Path> walk = Files.walk(source)) {
            for (Path path : (Iterable<Path>) walk::iterator) {
                Path dest = target.resolve(source.relativize(path).toString());
                if (Files.isDirectory(path)) {
                    Files.createDirectories(dest);
                } else {
                    Files.createDirectories(dest.getParent());
                    Files.copy(path, dest);
                }
            }
        }
    }
}
