/**
 * The templating port renders every case of python-reference.md section 4 as the Python reference
 * does (18.16e), including its three refusals.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { render } from "./templating";

const CONTEXT = {
  a: "A",
  b: { c: "C" },
  n: null,
  t: true,
  f: false,
  zero: 0,
  s: "",
  d: { k: "K" },
  e: {},
};

test("templating renders each construct as Python's templating.render does", () => {
  const cases: [string, Record<string, unknown>, string][] = [
    ["{{a}}|{{b.c}}|{{b.x}}|{{z}}|{{n}}|{{t}}|{{f}}|{{zero}}", CONTEXT, "A|C||||True|False|0"],
    ["{{#l}}[{{.}}]{{/l}}{{^l}}none{{/l}}", { l: ["x", "y"] }, "[x][y]"],
    ["{{#l}}[{{.}}]{{/l}}{{^l}}none{{/l}}", { l: [] }, "none"],
    ["{{#d}}<{{k}}>{{/d}}{{#e}}E{{/e}}{{^e}}noE{{/e}}", CONTEXT, "<K>noE"],
    ["{{#t}}T{{a}}{{/t}}{{#zero}}Z{{/zero}}{{^zero}}z{{/zero}}{{#s}}S{{/s}}", CONTEXT, "TAz"],
    [
      "{{#l}}{{k}}/{{a}} {{/l}}",
      { a: "outer", l: [{ k: 1 }, { k: 2, a: "inner" }] },
      "1/outer 2/inner ",
    ],
    ["{{a.b.c}} {{#x}}{{x.y}}{{/x}}", { a: "s", x: { y: "Y" } }, " Y"],
    ["{{ a }} {{a-b}} {{}} {{#}}", CONTEXT, "{{ a }} {{a-b}} {{}} {{#}}"],
  ];
  for (const [template, context, want] of cases) {
    assert.equal(render(template, context), want, template);
  }
});

test("templating refuses an unclosed or mismatched section as Python does", () => {
  assert.throws(() => render("{{#a}}x", {}), {
    message: "unclosed section tag(s) in template: ['a']",
  });
  assert.throws(() => render("{{#a}}x{{/b}}", {}), {
    message: "mismatched closing tag {/b} in template",
  });
  assert.throws(() => render("x{{/a}}", {}), {
    message: "mismatched closing tag {/a} in template",
  });
});
