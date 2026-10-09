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
 *
 * <p>{@code structuralEvaluationOverTheConductOverlayMatchesReference} does the same over the Conduct
 * overlay ({@code spec/catalogs/overlays/conduct}, SPEC §7.7): a real, user-reachable regression guard
 * that {@code withinScope}/{@code actsOnUntrusted} (CND-01/CND-05) are materialised the same way Python
 * does -- the whole overlay was Python-only until item 18.37 found and ported it, and this is the test
 * that would have caught it.
 */
class CatalogTest {
    private static final String[] CASES = {"passed", "failed", "inapplicable"};

    private static ObjectNode evaluateCatalog(Path directory) throws IOException {
        Catalog catalog = Catalog.load(directory);
        DomainBinding domain = DomainBinding.load(directory.resolve("test/domain.yaml"));

        ObjectNode result = Json.nodes().objectNode();
        for (Catalog.ControlSpec control : catalog.controls) {
            Psp.Shape shape = Catalog.shapeFor(catalog, control);
            if (shape == null) {
                continue;
            }
            ObjectNode perCase = Json.nodes().objectNode();
            for (String c : CASES) {
                Path fixture = directory.resolve("test").resolve(control.id).resolve(c + ".jsonl");
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
        return result;
    }

    @Test
    void structuralEvaluationMatchesReference() throws IOException {
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("catalog-golden.json"));
        assertEquals(Canonical.canonicalString(golden), Canonical.canonicalString(evaluateCatalog(Fixtures.BASE)));
    }

    @Test
    void structuralEvaluationOverTheConductOverlayMatchesReference() throws IOException {
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("conduct-golden.json"));
        assertEquals(
                Canonical.canonicalString(golden), Canonical.canonicalString(evaluateCatalog(Fixtures.CONDUCT)));
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

    // --- 18.37k: ROB-02's evidence shape resolves from the file the control declares; a declared
    // evidence shape that is not exactly one targeted node shape inside the catalog is refused. ---

    private static final String EVIDENCE_PREFIXES = "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
            + "@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .\n";
    private static final String EVIDENCE_BODY = " a sh:NodeShape ; sh:targetClass agentce:ConsequentialDecision ;"
            + " sh:property [ sh:path agentce:untrustedContentRuledOn ; sh:hasValue true ] .\n";

    private static Path withEvidenceShape(Path tmp, String text) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        Path shape = tmp.resolve("shapes/ROB-02-evidence.ttl");
        if (text == null) {
            Files.delete(shape);
        } else {
            Files.writeString(shape, text);
        }
        return tmp;
    }

    private static Path withEvidenceShapeValue(Path tmp, String value) throws IOException {
        copyTree(Fixtures.BASE, tmp);
        Path control = tmp.resolve("controls/ROB-02.yaml");
        String declared = "evidence_shape: shapes/ROB-02-evidence.ttl";
        String text = Files.readString(control);
        assertEquals(true, text.contains(declared));
        Files.writeString(control, text.replace(declared, "evidence_shape: " + value));
        return tmp;
    }

    @Test
    void anEvidenceShapeResolvesFromItsOwnFileWhateverItsName(@TempDir Path tmp) throws IOException {
        Catalog catalog = Catalog.load(withEvidenceShape(tmp, EVIDENCE_PREFIXES + "agentce:Anything" + EVIDENCE_BODY));
        Catalog.ControlSpec control = catalog.controls.stream().filter(c -> c.id.equals("ROB-02")).findFirst().get();
        assertEquals("agentce:ConsequentialDecision", Catalog.evidenceShapeFor(catalog, control).targetClass);
    }

    @Test
    void anUnresolvableEvidenceShapeIsRefused(@TempDir Path tmp) throws IOException {
        String noTarget = EVIDENCE_PREFIXES + "agentce:E a sh:NodeShape ; sh:property [ sh:path agentce:x ; sh:minCount 1 ] .\n";
        String twoTargets = EVIDENCE_PREFIXES + "agentce:E1" + EVIDENCE_BODY + "agentce:E2" + EVIDENCE_BODY;
        java.util.Arrays.asList(null, noTarget, twoTargets).forEach(text -> {
            Path dir = tmp.resolve(String.valueOf(text == null ? "missing" : text.length()));
            InputError err = assertThrows(InputError.class, () -> Catalog.load(withEvidenceShape(dir, text)));
            assertEquals("catalog.evidence_shape.unresolved", err.key);
        });
    }

    @Test
    void aDeclaredEvidenceShapeThatIsNotAFileInTheCatalogIsRefused(@TempDir Path tmp) throws IOException {
        Path outside = tmp.resolve("outside.ttl");
        Files.copy(Fixtures.BASE.resolve("shapes/ROB-02-evidence.ttl"), outside);
        int n = 0;
        for (String value : java.util.List.of("\"\"", "null", "5", "../outside.ttl", "linked.ttl")) {
            Path dir = tmp.resolve("cat" + n++).resolve("cat");
            withEvidenceShapeValue(dir, value);
            Files.copy(outside, dir.resolveSibling("outside.ttl"));
            Files.createSymbolicLink(dir.resolve("linked.ttl"), outside);
            InputError err = assertThrows(InputError.class, () -> Catalog.load(dir), value);
            assertEquals("catalog.evidence_shape.unresolved", err.key, value);
        }
    }
}
