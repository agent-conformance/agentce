/** IRIs, the in-memory triple store, and the domain binding (cross-checked with the Python engine). */

import assert from "node:assert/strict";
import { test } from "node:test";
import { DomainBinding } from "./domain";
import { eventIdOf, eventIri, isEventRef, principalIri } from "./iri";
import { GraphStore } from "./store";

test("event IRIs and refs", () => {
  assert.equal(eventIri("01ABC"), "agentce:event/01ABC");
  assert.equal(isEventRef("agentce:event/01ABC"), true);
  assert.equal(isEventRef("agentce:principal/x"), false);
  assert.equal(eventIdOf("agentce:event/01ABC"), "01ABC");
  assert.equal(eventIdOf("credit:LoanApproval"), "credit:LoanApproval");
});

test("principal IRIs match the Python reference HMAC under the zero key", () => {
  assert.equal(
    principalIri("human:officer"),
    "agentce:principal/4f9618842a3b141eae7bfb7cd81ee43b2ba78b2eef1a7c1d0c13c7ea559ab534",
  );
});

test("store edges and literals are set-valued and byte-ordered", () => {
  const store = new GraphStore();
  store.addEdge("s", "p", "b");
  store.addEdge("s", "p", "a");
  store.addEdge("s", "p", "a"); // idempotent
  assert.deepEqual(store.objects("s", "p"), ["a", "b"]);
  assert.deepEqual(store.subjects("p", "a"), ["s"]);
  assert.equal(store.edgeCount(), 2);

  store.addLiteral("s", "q", "1", "xsd:integer");
  store.addLiteral("s", "q", "0", "xsd:integer");
  assert.deepEqual(store.literalValues("s", "q"), ["0", "1"]);
  assert.deepEqual(store.literalPairs("s", "q"), [
    ["0", "xsd:integer"],
    ["1", "xsd:integer"],
  ]);
});

test("store class membership uses the materialised closure", () => {
  const store = new GraphStore();
  store.addSubclassClosure([
    ["credit:LoanApproval", "credit:LoanApproval"],
    ["credit:LoanApproval", "agentce:Decision"],
    ["agentce:Decision", "agentce:Decision"],
  ]);
  store.addType("agentce:event/d1", "credit:LoanApproval");
  assert.equal(store.isA("agentce:event/d1", "agentce:Decision"), true);
  assert.equal(store.isA("agentce:event/d1", "credit:LoanApproval"), true);
  assert.equal(store.isA("agentce:event/d1", "agentce:ToolCall"), false);
  assert.deepEqual(store.instancesOf("agentce:Decision"), ["agentce:event/d1"]);
});

test("domain binding reads decision and context classes", () => {
  const binding = DomainBinding.fromDict({
    decision_types: [
      { id: "credit:LoanApproval", subclass_of: "credit:CreditDecision", consequential: true },
      { id: "credit:AddressChange" },
    ],
    context_classes: [{ id: "credit:Application" }],
  });
  assert.equal(binding.subclasses.get("credit:LoanApproval"), "credit:CreditDecision");
  assert.equal(binding.subclasses.get("credit:AddressChange"), "agentce:Decision");
  assert.equal(binding.subclasses.get("credit:Application"), "agentce:ContextItem");
  assert.equal(binding.consequential.has("credit:LoanApproval"), true);
  assert.equal(binding.consequential.has("credit:AddressChange"), false);
});
