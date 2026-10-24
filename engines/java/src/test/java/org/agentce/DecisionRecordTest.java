package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * The eight decision-record literals DOC-02, TRN-04, OVS-05, RSK-01, REC-05, TRN-01 and TRN-05 read (18.37f), over
 * {@code decision-record-cases.json}, the table engines/python/tests/test_graph_decision_record.py keeps in sync.
 */
class DecisionRecordTest {

    @Test
    void everyCaseGivesItsLiteral() {
        JsonNode table = Json.parseFile(TestPaths.testData().resolve("decision-record-cases.json"));
        DomainBinding domain = DomainBinding.fromDict(table.get("domain"));
        String decision = table.get("decision").asText();
        for (JsonNode row : table.get("cases")) {
            List<JsonNode> events = new ArrayList<>();
            row.get("events").forEach(events::add);
            GraphStore store = Graph.buildGraph(events, domain);
            assertEquals(List.of(row.get("want").asText()),
                    store.literalValues(decision, "agentce:" + row.get("literal").asText()),
                    row.get("name").asText());
        }
    }
}
