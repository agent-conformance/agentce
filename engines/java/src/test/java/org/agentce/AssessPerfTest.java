package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.AbstractList;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

/**
 * {@code assessSubjects} scans the accepted-event list a number of times independent of subject
 * count: the accepted list is indexed by subject once rather than re-filtered per subject.
 */
class AssessPerfTest {
    private static final int EVENT_COUNT = 300;
    private static final int MANY_SUBJECTS = 60;

    /** A list that counts every element touched via indexed access or iteration. */
    private static final class CountingList extends AbstractList<JsonNode> {
        private final List<JsonNode> delegate;
        private final AtomicInteger counter;

        CountingList(List<JsonNode> delegate, AtomicInteger counter) {
            this.delegate = delegate;
            this.counter = counter;
        }

        @Override
        public JsonNode get(int index) {
            counter.incrementAndGet();
            return delegate.get(index);
        }

        @Override
        public int size() {
            return delegate.size();
        }

        @Override
        public Iterator<JsonNode> iterator() {
            Iterator<JsonNode> it = delegate.iterator();
            return new Iterator<>() {
                @Override
                public boolean hasNext() {
                    return it.hasNext();
                }

                @Override
                public JsonNode next() {
                    counter.incrementAndGet();
                    return it.next();
                }
            };
        }
    }

    private static List<JsonNode> events() {
        List<JsonNode> list = new ArrayList<>();
        for (int i = 0; i < EVENT_COUNT; i++) {
            ObjectNode e = Json.nodes().objectNode();
            e.put("id", "e" + i);
            e.put("subject", "ghost");
            e.put("time", "2024-01-01T00:00:00Z");
            e.put("agentcesourceclass", "operator");
            e.putObject("data").put("@type", "ToolCall");
            list.add(e);
        }
        return list;
    }

    private static int scans(int numSubjects) {
        AtomicInteger counter = new AtomicInteger(0);
        StringBuilder subjects = new StringBuilder("[");
        for (int j = 0; j < numSubjects; j++) {
            if (j > 0) {
                subjects.append(",");
            }
            subjects.append("{\"id\":\"s").append(j).append("\"}");
        }
        subjects.append("]");
        Profile profile = Profile.fromDict(Json.parse("{\"subjects\":" + subjects + "}"));
        CountingList accepted = new CountingList(events(), counter);
        Assess.assessSubjects(accepted, profile, List.of(), DomainBinding.empty());
        return counter.get();
    }

    @Test
    void acceptedEventScansDoNotGrowWithSubjectCount() {
        int one = scans(1);
        int many = scans(MANY_SUBJECTS);
        assertTrue(one > 0);
        assertEquals(one, many);
    }
}
