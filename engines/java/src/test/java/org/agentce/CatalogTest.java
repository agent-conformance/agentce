package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

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
}
