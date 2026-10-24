package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * The six literals REC-03, DAT-01, TRN-02, INT-04, ROB-01 and ROB-04 read (18.37g), three on a Decision and three on
 * a ToolCall, over
 * {@code call-record-cases.json}, the table engines/python/tests/test_graph_call_record.py keeps in sync.
 */
class CallRecordTest {

    @Test
    void everyCaseGivesItsLiteral() {
        JsonNode table = Json.parseFile(TestPaths.testData().resolve("call-record-cases.json"));
        DomainBinding domain = DomainBinding.fromDict(table.get("domain"));
        for (JsonNode row : table.get("cases")) {
            List<JsonNode> events = new ArrayList<>();
            row.get("events").forEach(events::add);
            GraphStore store = Graph.buildGraph(events, domain);
            assertEquals(List.of(row.get("want").asText()),
                    store.literalValues(row.get("node").asText(), "agentce:" + row.get("literal").asText()),
                    row.get("name").asText());
        }
    }
}
