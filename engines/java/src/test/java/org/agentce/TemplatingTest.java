package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.Test;

/** The templating port renders every case of python-reference.md section 4 as the Python reference
 * does (18.16e), including its three refusals. */
class TemplatingTest {
    private static final String CONTEXT =
            "{\"a\":\"A\",\"b\":{\"c\":\"C\"},\"n\":null,\"t\":true,\"f\":false,\"zero\":0,\"s\":\"\","
                    + "\"d\":{\"k\":\"K\"},\"e\":{}}";

    private static String render(String template, String context) throws Exception {
        JsonNode node = Json.readTreeWith(new com.fasterxml.jackson.databind.ObjectMapper(), context);
        return Templating.render(template, node);
    }

    @Test
    void rendersEachConstructAsPython() throws Exception {
        assertEquals("A|C||||True|False|0", render("{{a}}|{{b.c}}|{{b.x}}|{{z}}|{{n}}|{{t}}|{{f}}|{{zero}}", CONTEXT));
        assertEquals("[x][y]", render("{{#l}}[{{.}}]{{/l}}{{^l}}none{{/l}}", "{\"l\":[\"x\",\"y\"]}"));
        assertEquals("none", render("{{#l}}[{{.}}]{{/l}}{{^l}}none{{/l}}", "{\"l\":[]}"));
        assertEquals("<K>noE", render("{{#d}}<{{k}}>{{/d}}{{#e}}E{{/e}}{{^e}}noE{{/e}}", CONTEXT));
        assertEquals("TAz", render("{{#t}}T{{a}}{{/t}}{{#zero}}Z{{/zero}}{{^zero}}z{{/zero}}{{#s}}S{{/s}}", CONTEXT));
        assertEquals("1/outer 2/inner ", render(
                "{{#l}}{{k}}/{{a}} {{/l}}", "{\"a\":\"outer\",\"l\":[{\"k\":1},{\"k\":2,\"a\":\"inner\"}]}"));
        assertEquals(" Y", render("{{a.b.c}} {{#x}}{{x.y}}{{/x}}", "{\"a\":\"s\",\"x\":{\"y\":\"Y\"}}"));
        assertEquals("{{ a }} {{a-b}} {{}} {{#}}", render("{{ a }} {{a-b}} {{}} {{#}}", CONTEXT));
    }

    @Test
    void refusesUnclosedOrMismatchedSectionsAsPython() {
        assertEquals("unclosed section tag(s) in template: ['a']",
                assertThrows(IllegalArgumentException.class, () -> render("{{#a}}x", "{}")).getMessage());
        assertEquals("mismatched closing tag {/b} in template",
                assertThrows(IllegalArgumentException.class, () -> render("{{#a}}x{{/b}}", "{}")).getMessage());
        assertEquals("mismatched closing tag {/a} in template",
                assertThrows(IllegalArgumentException.class, () -> render("x{{/a}}", "{}")).getMessage());
    }
}
