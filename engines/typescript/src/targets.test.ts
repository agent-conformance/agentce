/**
 * 18.121: SHACL reads a shape's several sh:targetClass values as a union. Both are kept, sorted, in
 * either order, and the focus nodes are the instances of any of them (CND-05 targets ToolCall and Decision).
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { parseShapesTtl } from "./psp";
import { GraphStore } from "./store";
import { evaluateShape } from "./structural";

const PREFIX =
  "@prefix sh: <http://www.w3.org/ns/shacl#> .\n" +
  "@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .\n";
const BODY = 'sh:property [ sh:path agentce:actsOnUntrusted ; sh:hasValue false ; sh:name "S1" ] .';

for (const targets of [
  "agentce:ToolCall, agentce:Decision",
  "agentce:Decision, agentce:ToolCall",
]) {
  test(`several target classes are a union (${targets})`, () => {
    const shapes = parseShapesTtl(
      `${PREFIX}agentce:S a sh:NodeShape ; sh:targetClass ${targets} ; ${BODY}\n`,
    );
    const shape = [...shapes.values()][0];
    assert.deepEqual(shape.targetClasses, ["agentce:Decision", "agentce:ToolCall"]);
    const store = new GraphStore();
    store.addSubclassClosure(
      ["agentce:ToolCall", "agentce:Decision", "agentce:Outcome"].map((c): [string, string] => [
        c,
        c,
      ]),
    );
    for (const [node, cls, untrusted] of [
      ["tc1", "agentce:ToolCall", "false"],
      ["d1", "agentce:Decision", "true"],
      ["o1", "agentce:Outcome", "true"],
    ]) {
      store.addType(node, cls);
      store.addLiteral(node, "agentce:actsOnUntrusted", untrusted, "xsd:boolean");
    }
    const [applicable, failing] = evaluateShape(store, shape, shapes, "CND-05");
    assert.deepEqual(applicable, ["d1", "tc1"]);
    assert.deepEqual([...failing], ["d1"]);
  });
}
