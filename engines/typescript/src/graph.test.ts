/**
 * The graph builder is byte-identical to the Python reference (SPEC §6.3).
 *
 * `testdata/graph-golden.txt` is the reference engine's full triple dump for `graph-fixture.json`
 * (edges, literals, and the subclass closure, each line sorted by UTF-8 bytes). The TypeScript
 * builder must reproduce it exactly — every materialised glue edge, principal HMAC, and closure pair.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { DomainBinding } from "./domain";
import { buildGraph } from "./graph";

const TESTDATA = join(__dirname, "..", "testdata");

test("graph triples match the reference golden byte for byte", () => {
  const fixture = JSON.parse(readFileSync(join(TESTDATA, "graph-fixture.json"), "utf-8"));
  const golden = readFileSync(join(TESTDATA, "graph-golden.txt"), "utf-8");
  const store = buildGraph(fixture.events, { domain: DomainBinding.fromDict(fixture.domain) });
  assert.equal(`${store.dumpTriples().join("\n")}\n`, golden);
});

test("graph materialises the human chain terminus and precedence", () => {
  const fixture = JSON.parse(readFileSync(join(TESTDATA, "graph-fixture.json"), "utf-8"));
  const store = buildGraph(fixture.events, { domain: DomainBinding.fromDict(fixture.domain) });
  // dec-1 has a human overseer at its chain terminus, and is reviewed, so dec-2 is precededBy dec-1.
  assert.equal(
    store.objects("agentce:event/dec-2", "agentce:precededBy")[0],
    "agentce:event/dec-1",
  );
  const terminus = store.objects("agentce:event/dec-1", "agentce:chainTerminus")[0] as string;
  assert.equal(store.isA(terminus, "agentce:HumanPrincipal"), true);
});

const MINOR_DOMAIN = DomainBinding.fromDict({
  decision_types: [
    {
      id: "agentce:CreditDecision",
      subclass_of: "agentce:ConsequentialDecision",
      consequential: true,
    },
  ],
});

function decisionEvent(id: string) {
  return {
    id,
    time: "2026-01-01T00:00:00Z",
    agentcesourceclass: "self_report",
    data: { "@type": "Decision", decision_type: "agentce:CreditDecision" },
  };
}

function noticeEvent(id: string, decisionId: string, origin?: string) {
  const refs: Record<string, string> = { decision: `agentce:event/${decisionId}` };
  if (origin !== undefined) {
    refs.origin = `agentce:event/${origin}`;
  }
  return {
    id,
    time: "2026-01-01T00:00:00Z",
    agentcesourceclass: "independent_system",
    data: { "@type": "Notice", refs },
  };
}

test("explanation is not reconstructable when the notice itself dangles", () => {
  // TRN-03 (SPEC §7.6, round-2 critic finding 4): the decision's own evidence chain resolves
  // cleanly, but the Notice that notified it carries a dangling refs.* value of its own (here
  // refs.origin, a real Refs key per spec/model/agentce-evidence.linkml.yaml -- verifier round 1
  // found the prior fixture used refs.explanation_ref, a key Refs does not define, so a real adapter
  // could never produce it) -- `dangling` lands that literal on the NOTICE node, not the Decision
  // node, so `explanationReconstructable` must check the notice's dangling status too.
  const events = [decisionEvent("d1"), noticeEvent("c1", "d1", "missing-origin")];
  const store = buildGraph(events, { domain: MINOR_DOMAIN });
  assert.deepEqual(store.literalValues("agentce:event/c1", "agentce:danglingRef"), [
    "agentce:event/missing-origin",
  ]);
  assert.deepEqual(store.literalValues("agentce:event/d1", "agentce:danglingRef"), []);
  assert.deepEqual(store.literalValues("agentce:event/d1", "agentce:explanationReconstructable"), [
    "false",
  ]);
});

test("explanation reconstructable is independent of notice order", () => {
  // Verifier-found regression: a decision notified by more than one Notice (one clean, one with its
  // own dangling ref) used to read explanationReconstructable from whichever Notice was mapped LAST,
  // so the same evidence gave a different verdict depending only on event order. Reconstructable
  // means at least one notifying Notice is clean -- true in both orderings.
  const decision = decisionEvent("d1");
  const cleanNotice = noticeEvent("c1", "d1");
  const danglingNotice = noticeEvent("c2", "d1", "missing-origin");

  const cleanLast = buildGraph([decision, danglingNotice, cleanNotice], { domain: MINOR_DOMAIN });
  const danglingLast = buildGraph([decision, cleanNotice, danglingNotice], {
    domain: MINOR_DOMAIN,
  });

  assert.deepEqual(
    cleanLast.literalValues("agentce:event/d1", "agentce:explanationReconstructable"),
    ["true"],
  );
  assert.deepEqual(
    danglingLast.literalValues("agentce:event/d1", "agentce:explanationReconstructable"),
    ["true"],
  );
});
