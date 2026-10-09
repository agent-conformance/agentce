package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * 18.121: SHACL reads a shape's several sh:targetClass values as a union. Both are kept, sorted, in either order,
 * and the focus nodes are the instances of any of them (CND-05 targets ToolCall and Decision).
 */
class TargetsTest {
    private static final String PREFIX = "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
            + "@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .\n";
    private static final String BODY =
            "sh:property [ sh:path agentce:actsOnUntrusted ; sh:hasValue false ; sh:name \"S1\" ] .";

    @Test
    void severalTargetClassesAreAUnion() {
        for (String targets : List.of("agentce:ToolCall, agentce:Decision", "agentce:Decision, agentce:ToolCall")) {
            Map<String, Psp.Shape> shapes =
                    Psp.parseShapesTtl(PREFIX + "agentce:S a sh:NodeShape ; sh:targetClass " + targets + " ; " + BODY);
            Psp.Shape shape = shapes.values().iterator().next();
            assertEquals(List.of("agentce:Decision", "agentce:ToolCall"), shape.targetClasses);
            GraphStore store = new GraphStore();
            store.addSubclassClosure(List.of(
                    new String[] {"agentce:ToolCall", "agentce:ToolCall"},
                    new String[] {"agentce:Decision", "agentce:Decision"},
                    new String[] {"agentce:Outcome", "agentce:Outcome"}));
            String[][] nodes = {
                {"tc1", "agentce:ToolCall", "false"}, {"d1", "agentce:Decision", "true"}, {"o1", "agentce:Outcome", "true"}
            };
            for (String[] n : nodes) {
                store.addType(n[0], n[1]);
                store.addLiteral(n[0], "agentce:actsOnUntrusted", n[2], "xsd:boolean");
            }
            Structural.ShapeResult result = Structural.evaluateShape(store, shape, shapes, "CND-05");
            assertEquals(List.of("d1", "tc1"), result.applicable);
            assertEquals(List.of("d1"), List.copyOf(result.failing));
        }
    }
}
