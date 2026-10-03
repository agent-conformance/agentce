package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

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

    // --- 18.34: the Portable Shape Profile forbids sh:sparql and sh:js; Java has no `catalog lint`
    // command (confirmed: no `lint` subcommand in Cli.java's dispatch), so this item's Java scope is
    // catalog *loading* only, via Catalog.load -> Psp.parseShapesTtl. ---

    @Test
    void loadRefusesAShapeUsingShSparql(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        mutateShape(tmp, "sh:sparql [] ");
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.sparql_forbidden", err.key);
    }

    @Test
    void loadRefusesAShapeUsingShJs(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        mutateShape(tmp, "sh:js [] ");
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.script_forbidden", err.key);
    }

    @Test
    void loadRefusesAShapeUsingShJavascript(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        mutateShape(tmp, "sh:javascript [] ");
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.script_forbidden", err.key);
    }

    // Contract-critic round 1 (B1): a shape with BOTH predicates must always report sparql first
    // (spec/rules/psp_check.py's PRIORITY_DENY), matching Python/TypeScript's fixed-order check,
    // never whichever one Rdf.Store happened to insert first.
    @Test
    void loadRefusesAShapeCarryingBothPredicatesAsSparqlDeterministically(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        mutateShape(tmp, "sh:js [] ; sh:sparql [] ");
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.sparql_forbidden", err.key);
    }

    @Test
    void loadRefusesAForbiddenShapePredicateRegardlessOfCase(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        mutateShape(tmp, "sh:SPARQL [] ");
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.sparql_forbidden", err.key);
    }

    @Test
    void loadRefusesAForbiddenShapePredicateRegardlessOfCaseJavaScript(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        mutateShape(tmp, "sh:JavaScript [] ");
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.script_forbidden", err.key);
    }

    @Test
    void loadRefusesForbiddenPredicateUnderAnAliasedPrefixOrBareIri(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        Path shape = tmp.resolve("shapes/DAT-01.ttl");
        String original = Files.readString(shape);
        String aliased =
                original
                        .replace(
                                "@prefix sh: <http://www.w3.org/ns/shacl#> .",
                                "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
                                        + "@prefix shacl: <http://www.w3.org/ns/shacl#> .")
                        .replace("sh:name \"S1\" ] .", "sh:name \"S1\" ] ; shacl:sparql [] .");
        Files.writeString(shape, aliased);
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.sparql_forbidden", err.key);

        String bareIri =
                original.replace(
                        "sh:name \"S1\" ] .", "sh:name \"S1\" ] ; <http://www.w3.org/ns/shacl#js> [] .");
        Files.writeString(shape, bareIri);
        InputError err2 = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.script_forbidden", err2.key);
    }

    @Test
    void loadDoesNotRefuseALiteralOrACommentThatMerelyMentionsShSparql(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        Path shape = tmp.resolve("shapes/DAT-01.ttl");
        String original = Files.readString(shape);
        Files.writeString(
                shape,
                original.replace(
                        "sh:name \"S1\" ] .",
                        "sh:name \"S1, not sh:sparql or sh:js (documentation only)\" ] ."));
        Catalog.load(tmp); // must not throw

        Files.writeString(shape, "# this shape must never use sh:sparql or sh:js\n" + original);
        Catalog.load(tmp); // must not throw
    }

    @Test
    void loadRefusesForbiddenPredicateNestedInsideAPropertyShape(@TempDir Path tmp) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        Path shape = tmp.resolve("shapes/DAT-01.ttl");
        Files.writeString(
                shape,
                Files.readString(shape)
                        .replace(
                                "sh:property [ sh:path prov:used ; sh:minCount 1 ; sh:name \"S1\" ] .",
                                "sh:property [ sh:path prov:used ; sh:minCount 1 ; sh:name \"S1\" ; sh:sparql [] ] ."));
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.sparql_forbidden", err.key);
    }

    @Test
    void loadRefusesForbiddenPredicateWrittenAsATurtleUnicodeEscape(@TempDir Path tmp) throws IOException {
        // Turtle's IRIREF grammar allows backslash-u (four hex digits) and backslash-U (eight hex
        // digits) escapes inside <...>; a parser that kept the raw, undecoded IRI text (rather than
        // the resolved IRI) would miss this, since it is the same IRI as sh:sparql once decoded.
        copyTree(Fixtures.BASE, tmp);
        // Built by concatenation, not a literal escape, so javac's own raw unicode-escape
        // preprocessing (which runs before string literals are even recognised) does not decode
        // the backslash-u sequence itself -- the Turtle parser under test must do that decoding.
        String uchar = "<http://www.w3.org/ns/shacl#sp" + "\\" + "u0061" + "rql> [] ";
        mutateShape(tmp, uchar);
        InputError err = assertThrows(InputError.class, () -> Catalog.load(tmp));
        assertEquals("catalog.shape.sparql_forbidden", err.key);
    }

    @Test
    void loadAcceptsTheUnmutatedBaseCatalogClean() {
        Catalog.load(Fixtures.BASE); // must not throw
    }

    private static void mutateShape(Path catalogDir, String triple) throws IOException {
        Path shape = catalogDir.resolve("shapes/DAT-01.ttl");
        String original = Files.readString(shape);
        Files.writeString(shape, original.replace("sh:name \"S1\" ] .", "sh:name \"S1\" ] ; " + triple + " ."));
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
