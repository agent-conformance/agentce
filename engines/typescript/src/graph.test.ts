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
import { buildGraph, flaggedRecords, unkeyedFlags } from "./graph";

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

function memoryEvent(id: string, ptype: string, data: Record<string, unknown>) {
  return {
    id,
    time: "2026-01-01T00:00:00Z",
    agentcesourceclass: "enforcement_point",
    data: { "@type": ptype, ...data },
  };
}

function robust(events: Record<string, unknown>[], decisionId = "d1"): string[] {
  const store = buildGraph(events, { domain: MINOR_DOMAIN });
  return store.literalValues(`agentce:event/${decisionId}`, "agentce:robustToUntrustedContent");
}

function withInputs(event: ReturnType<typeof decisionEvent>, inputs: string[]) {
  return { ...event, data: { ...event.data, inputs } };
}

test("ROB-02: a decision is robust when it used only trusted content, or none", () => {
  const write = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  assert.deepEqual(robust([write, withInputs(decisionEvent("d1"), ["mem:r1"])]), ["true"]);
  assert.deepEqual(robust([decisionEvent("d1")]), ["true"]);
});

test("ROB-02: guard marks and a consumed read's trust_min taint a decision", () => {
  for (const marks of [
    { trust: "untrusted" },
    { trust: "quarantined" },
    { trust: "trusted", guard_verdict: "quarantine" },
    { guard_verdict: "block" },
  ]) {
    const write = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", ...marks });
    assert.deepEqual(robust([write, withInputs(decisionEvent("d1"), ["mem:r1"])]), ["false"]);
  }
  const sanitized = memoryEvent("w1", "MemoryWrite", {
    record_ref: "mem:r1",
    guard_verdict: "sanitize",
  });
  assert.deepEqual(robust([sanitized, withInputs(decisionEvent("d1"), ["mem:r1"])]), ["true"]);
  const read = memoryEvent("m1", "MemoryRead", {
    trust_min: "untrusted",
    refs: { consumer: "agentce:event/d1" },
  });
  assert.deepEqual(robust([read, decisionEvent("d1")]), ["false"]);
});

test("ROB-02: taint follows content through any number of hops, in any order", () => {
  const events = [
    memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "untrusted" }),
    memoryEvent("tc1", "ToolCall", { used: ["mem:r1"], result_ref: "content:t1" }),
    memoryEvent("mc1", "ModelCall", { input_ref: "content:t1", output_ref: "content:o1" }),
    withInputs(decisionEvent("d0"), ["content:o1"]),
    withInputs(decisionEvent("d1"), ["agentce:event/d0"]),
  ];
  assert.deepEqual(robust(events, "d0"), ["false"]);
  assert.deepEqual(robust(events), ["false"]);
  assert.deepEqual(robust([...events].reverse()), ["false"]);
});

test("ROB-02: origin, a write of a tainted record and a derived instruction taint a decision", () => {
  const read = memoryEvent("m1", "MemoryRead", {
    trust_min: "untrusted",
    refs: { consumer: "agentce:event/mc1" },
  });
  const viaOrigin = {
    ...decisionEvent("d1"),
    data: { ...decisionEvent("d1").data, refs: { origin: "agentce:event/mc1" } },
  };
  assert.deepEqual(robust([read, memoryEvent("mc1", "ModelCall", {}), viaOrigin]), ["false"]);
  const bad = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "untrusted" });
  const good = memoryEvent("w2", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  assert.deepEqual(robust([bad, good, withInputs(decisionEvent("d1"), ["agentce:event/w2"])]), [
    "false",
  ]);
  const origin = memoryEvent("i1", "Instruction", { source_class: "tool_output" });
  const derived = memoryEvent("i2", "Instruction", {
    source_class: "user",
    refs: { parent: "agentce:event/i1" },
  });
  const acting = {
    ...decisionEvent("d1"),
    data: { ...decisionEvent("d1").data, refs: { instruction: "agentce:event/i2" } },
  };
  assert.deepEqual(robust([origin, derived, acting]), ["false"]);
});

test("ROB-02: a cycle of inputs ends and stays trusted without a source", () => {
  const first = withInputs(decisionEvent("d1"), ["agentce:event/d2"]);
  const second = withInputs(decisionEvent("d2"), ["agentce:event/d1"]);
  assert.deepEqual(robust([first, second]), ["true"]);
});

function ruled(events: Record<string, unknown>[], decisionId = "d1"): string[] {
  const store = buildGraph(events, { domain: MINOR_DOMAIN });
  return store.literalValues(`agentce:event/${decisionId}`, "agentce:untrustedContentRuledOn");
}

function selfReport<T extends Record<string, unknown>>(event: T): T {
  return { ...event, agentcesourceclass: "self_report" };
}

test("ROB-02: ruled on when the guard ruled on every input", () => {
  const write = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  assert.deepEqual(ruled([write, withInputs(decisionEvent("d1"), ["mem:r1"])]), ["true"]);
  // A decision nothing recorded flows into used nothing the guard could rule on (ruling 15, 18.127).
  assert.deepEqual(ruled([decisionEvent("d1")]), ["false"]);
});

test("ROB-02: not ruled on through a read only the agent reported", () => {
  const write = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  const read = selfReport(
    memoryEvent("m1", "MemoryRead", {
      record_refs: ["mem:r1"],
      refs: { consumer: "agentce:event/d1" },
    }),
  );
  assert.deepEqual(ruled([write, read, decisionEvent("d1")]), ["false"]);
  const guardRead = memoryEvent("m2", "MemoryRead", { record_refs: ["mem:r1"] });
  assert.deepEqual(ruled([write, guardRead, read, decisionEvent("d1")]), ["false"]);
});

test("ROB-02: not ruled on a record no enforcement point covers", () => {
  const unguarded = selfReport(
    memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" }),
  );
  const used = withInputs(decisionEvent("d1"), ["mem:r1"]);
  assert.deepEqual(ruled([unguarded, used]), ["false"]);
  const silent = memoryEvent("w2", "MemoryWrite", { record_ref: "mem:r1" });
  assert.deepEqual(ruled([silent, used]), ["false"]);
  // An enforcement-point read covers the record only when it filtered by trust.
  const guardRead = memoryEvent("m1", "MemoryRead", { record_refs: ["mem:r1"] });
  assert.deepEqual(ruled([unguarded, guardRead, used]), ["false"]);
  const filtered = memoryEvent("m1", "MemoryRead", {
    record_refs: ["mem:r1"],
    trust_min: "trusted",
  });
  assert.deepEqual(ruled([unguarded, filtered, used]), ["true"]);
});

test("ROB-02: not ruled on a ref the bundle does not hold", () => {
  for (const ref of ["mem:missing", "agentce:event/missing"]) {
    assert.deepEqual(ruled([withInputs(decisionEvent("d1"), [ref])]), ["false"], ref);
  }
  const other = withInputs(decisionEvent("d2"), ["mem:missing"]);
  const write = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  const used = withInputs(decisionEvent("d1"), ["mem:r1"]);
  assert.deepEqual(ruled([write, used, other]), ["true"]);
});

test("ROB-02: ruled on is independent of event order", () => {
  const write = selfReport(memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1" }));
  const events = [write, withInputs(decisionEvent("d1"), ["mem:r1"])];
  assert.deepEqual(ruled(events), ["false"]);
  assert.deepEqual(ruled([...events].reverse()), ["false"]);
});

test("ROB-02: an unruled rewrite of a guarded record is not ruled on (18.120)", () => {
  const guarded = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  const used = withInputs(decisionEvent("d1"), ["mem:r1"]);
  const filtered = memoryEvent("m1", "MemoryRead", {
    record_refs: ["mem:r1"],
    trust_min: "trusted",
  });
  for (const rewrite of [
    selfReport(memoryEvent("w2", "MemoryWrite", { record_ref: "mem:r1" })),
    selfReport(memoryEvent("w2", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" })),
    memoryEvent("w2", "MemoryWrite", { record_ref: "mem:r1" }),
  ]) {
    assert.deepEqual(ruled([guarded, rewrite, used]), ["false"]);
    assert.deepEqual(ruled([rewrite, used, guarded]), ["false"]);
    assert.deepEqual(ruled([guarded, rewrite, filtered, used]), ["true"]);
  }
  const again = memoryEvent("w2", "MemoryWrite", { record_ref: "mem:r1", guard_verdict: "allow" });
  assert.deepEqual(ruled([guarded, again, used]), ["true"]);
});

test("ROB-02: a record another subject flagged is never ruled on (18.120)", () => {
  const write = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  const filtered = memoryEvent("m1", "MemoryRead", {
    record_refs: ["mem:r1"],
    trust_min: "trusted",
    refs: { consumer: "agentce:event/d1" },
  });
  const events = [write, filtered, decisionEvent("d1")];
  assert.deepEqual(ruled(events), ["true"]);
  for (const [foreign, expected] of [
    ["mem:r1", "false"],
    ["mem:r2", "true"],
  ]) {
    const store = buildGraph(events, {
      domain: MINOR_DOMAIN,
      foreignUntrusted: new Set([foreign]),
    });
    assert.deepEqual(store.literalValues("agentce:event/d1", "agentce:untrustedContentRuledOn"), [
      expected,
    ]);
    assert.deepEqual(store.literalValues("agentce:event/d1", "agentce:robustToUntrustedContent"), [
      "true",
    ]);
  }
});

test("ROB-02: a guard read rules on its records only at trust_min trusted (18.172)", () => {
  // A read that let untrusted or quarantined records through, or filtered by nothing, rules on nothing.
  const unguarded = selfReport(
    memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" }),
  );
  const used = withInputs(decisionEvent("d1"), ["mem:r1"]);
  for (const [trustMin, expected] of [
    ["trusted", "true"],
    ["untrusted", "false"],
    ["quarantined", "false"],
    [null, "false"],
  ]) {
    const read = memoryEvent("m1", "MemoryRead", { record_refs: ["mem:r1"], trust_min: trustMin });
    assert.deepEqual(ruled([unguarded, read, used]), [expected], String(trustMin));
    assert.deepEqual(ruled([used, read, unguarded]), [expected], String(trustMin));
  }
  // A ruled enforcement-point write still rules on its record whatever the read let through.
  const ruledWrite = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  const read = memoryEvent("m1", "MemoryRead", { record_refs: ["mem:r1"], trust_min: "untrusted" });
  assert.deepEqual(ruled([ruledWrite, read, used]), ["true"]);
});

test("ROB-02: a record another subject's unreadable write may have changed is not ruled on (18.172)", () => {
  // SPEC §7.4: the record it names; with no readable record_ref, every record of its store, a record no event names
  // a store for counting as one of them; with no readable store either, every record.
  const w = (recordRef: string | null, store: string | null) => ({ recordRef, store });
  const used = withInputs(decisionEvent("d1"), ["mem:r1"]);
  for (const store of ["kb", null]) {
    const write = memoryEvent("w1", "MemoryWrite", {
      record_ref: "mem:r1",
      trust: "trusted",
      store,
    });
    const events = [write, used];
    for (const [unreadable, expected] of [
      [[], "true"],
      [[w("mem:r1", null)], "false"],
      [[w("mem:r1", "other")], "false"],
      [[w("mem:r2", null)], "true"],
      [[w(null, "kb")], "false"],
      [[w(null, "other")], store === null ? "false" : "true"],
      [[w(null, null)], "false"],
      [[w("mem:r2", null), w(null, null)], "false"],
    ] as const) {
      for (const order of [events, [...events].reverse()]) {
        const graph = buildGraph(order, { domain: MINOR_DOMAIN, foreignUnreadable: unreadable });
        const label = JSON.stringify([store, unreadable]);
        assert.deepEqual(
          graph.literalValues("agentce:event/d1", "agentce:untrustedContentRuledOn"),
          [expected],
          label,
        );
        // Nobody could read that content, so it is unruled, never untrusted.
        assert.deepEqual(
          graph.literalValues("agentce:event/d1", "agentce:robustToUntrustedContent"),
          ["true"],
          label,
        );
      }
    }
  }
});

test("ROB-02: flaggedRecords names every subject that flagged a record (18.120)", () => {
  const write = (id: string, subject: string, data: Record<string, unknown>) => ({
    ...memoryEvent(id, "MemoryWrite", { record_ref: "mem:r1", ...data }),
    subject,
  });
  const events = [
    write("w1", "a", { trust: "untrusted" }),
    write("w2", "b", { guard_verdict: "block" }),
    write("w3", "c", { trust: "trusted" }),
    write("w4", "d", { provenance_origin_class: "tool_output" }),
    write("w5", "e", { provenance_origin_class: "tool_output", trust: "trusted" }),
    memoryEvent("m1", "MemoryRead", { record_refs: ["mem:r1"], trust_min: "untrusted" }),
    selfReport(write("w6", "f", { trust: "trusted" })),
  ];
  const sorted = (m: Map<string, Set<string>>) => [...m].map(([r, by]) => [r, [...by].sort()]);
  assert.deepEqual(sorted(flaggedRecords(events)), [["mem:r1", ["a", "b", "d", "f"]]]);
  assert.deepEqual(sorted(flaggedRecords([...events].reverse())), [
    ["mem:r1", ["a", "b", "d", "f"]],
  ]);
});

test("ROB-02: a flagged write with no readable record_ref is unkeyed (18.202)", () => {
  // A flagged write whose record_ref is absent, null or empty names no record; it counts against its store.
  const write = (id: string, subject: string, data: Record<string, unknown>) => ({
    ...memoryEvent(id, "MemoryWrite", { store: "kb", trust: "untrusted", ...data }),
    subject,
  });
  const events = [
    write("w1", "a", {}),
    write("w2", "b", { record_ref: null }),
    write("w3", "c", { record_ref: "", store: "" }),
    write("w4", "d", { record_ref: "mem:r1" }),
    write("w5", "e", { record_ref: "", trust: "trusted" }),
  ];
  assert.deepEqual(
    [...flaggedRecords(events)].map(([r, by]) => [r, [...by]]),
    [["mem:r1", ["d"]]],
  );
  assert.deepEqual(unkeyedFlags(events), [
    { recordRef: null, store: "kb" },
    { recordRef: null, store: "kb" },
    { recordRef: null, store: null },
  ]);
});

test("ROB-02: an untrusted origin class with no ruling taints", () => {
  const used = withInputs(decisionEvent("d1"), ["mem:r1"]);
  for (const [origin, expected] of [
    ["tool_output", "false"],
    ["user", "true"],
  ]) {
    const write = memoryEvent("w1", "MemoryWrite", {
      record_ref: "mem:r1",
      provenance_origin_class: origin,
    });
    assert.deepEqual(robust([write, used]), [expected], origin);
  }
  const marked = memoryEvent("w1", "MemoryWrite", {
    record_ref: "mem:r1",
    provenance_origin_class: "tool_output",
    trust: "trusted",
  });
  assert.deepEqual(robust([marked, used]), ["true"]);
});

function instructionEvent(id: string, sourceClass: string, refs: Record<string, string> = {}) {
  const named = Object.fromEntries(
    Object.entries(refs).map(([key, ref]) => [key, `agentce:event/${ref}`]),
  );
  return memoryEvent(id, "Instruction", {
    source_class: sourceClass,
    ...(Object.keys(named).length > 0 ? { refs: named } : {}),
  });
}

function callEvent(id = "tc1", instruction = "i1") {
  return memoryEvent(id, "ToolCall", { refs: { instruction: `agentce:event/${instruction}` } });
}

/** CND-05's flag on `acting`, a ToolCall or Decision that acts on an instruction. */
function acts(events: Record<string, unknown>[], acting = "tc1"): string[] {
  const store = buildGraph(events, { domain: MINOR_DOMAIN });
  return store.literalValues(`agentce:event/${acting}`, "agentce:actsOnUntrusted");
}

test("CND-05: an action on an instruction reads the instruction's own class", () => {
  assert.deepEqual(acts([instructionEvent("i1", "tool_output"), callEvent()]), ["true"]);
  assert.deepEqual(acts([instructionEvent("i1", "user"), callEvent()]), ["false"]);
});

test("CND-05: the parent chain is followed for any number of hops, in any order", () => {
  for (const [root, expected] of [
    ["retrieved", "true"],
    ["service", "false"],
  ]) {
    const events = [
      instructionEvent("i3", root),
      instructionEvent("i2", "operator", { parent: "i3" }),
      instructionEvent("i1", "user", { parent: "i2" }),
      callEvent(),
    ];
    assert.deepEqual(acts(events), [expected], root);
    assert.deepEqual(acts([...events].reverse()), [expected], root);
  }
});

test("CND-05: an origin tool call or resource access produced untrusted content", () => {
  const call = callEvent("tc0", "i0");
  const access = memoryEvent("ra0", "ResourceAccess", { operation: "read" });
  for (const origin of ["tc0", "ra0"]) {
    const events = [instructionEvent("i0", "user"), call, access];
    events.push(instructionEvent("i1", "user", { origin }), callEvent());
    assert.deepEqual(acts(events), ["true"], origin);
  }
  assert.deepEqual(acts([instructionEvent("i0", "user"), call], "tc0"), ["false"]);
});

test("CND-05: an origin read the guard marked untrusted taints the chain", () => {
  const write = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  for (const [trustMin, expected] of [
    ["untrusted", "true"],
    ["trusted", "false"],
  ]) {
    const read = memoryEvent("m1", "MemoryRead", { record_refs: ["mem:r1"], trust_min: trustMin });
    const events = [write, read, instructionEvent("i1", "memory_trusted", { origin: "m1" })];
    assert.deepEqual(acts([...events, callEvent()]), [expected], trustMin);
  }
  const bad = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "untrusted" });
  const read = memoryEvent("m1", "MemoryRead", { record_refs: ["mem:r1"], trust_min: "trusted" });
  const events = [bad, read, instructionEvent("i1", "memory_trusted", { origin: "m1" })];
  assert.deepEqual(acts([...events, callEvent()]), ["true"]);
});

test("CND-05: the walk ends on cycles and never walks down the chain", () => {
  const cycle = [
    instructionEvent("i1", "user", { parent: "i2" }),
    instructionEvent("i2", "user", { parent: "i1" }),
    callEvent(),
  ];
  assert.deepEqual(acts(cycle), ["false"]);
  const child = [
    instructionEvent("i1", "user"),
    instructionEvent("i2", "tool_output", { parent: "i1" }),
  ];
  assert.deepEqual(acts([...child, callEvent()]), ["false"]);
});

test("CND-05: a decision acting on a derived untrusted instruction is flagged", () => {
  const decision = {
    ...decisionEvent("d1"),
    data: { ...decisionEvent("d1").data, refs: { instruction: "agentce:event/i1" } },
  };
  const events = [
    instructionEvent("i0", "tool_output"),
    instructionEvent("i1", "user", { parent: "i0" }),
  ];
  assert.deepEqual(acts([...events, decision], "d1"), ["true"]);
});

/** CND-05's evidence flag on `acting`: whether the records show which instruction it acted on. */
function judged(events: Record<string, unknown>[], acting = "tc1"): string[] {
  const store = buildGraph(events, { domain: MINOR_DOMAIN });
  return store.literalValues(`agentce:event/${acting}`, "agentce:actsOnJudged");
}

test("cnd05: a tool call naming no instruction is not judged", () => {
  const absent = memoryEvent("tc1", "ToolCall", { refs: {} });
  const nulled = memoryEvent("tc1", "ToolCall", { refs: { instruction: null } });
  for (const call of [absent, nulled]) {
    assert.deepEqual(judged([instructionEvent("i1", "user"), call]), ["false"]);
    assert.deepEqual(acts([instructionEvent("i1", "user"), call]), ["false"]);
  }
});

test("cnd05: a tool call on a held trusted instruction is judged", () => {
  assert.deepEqual(judged([instructionEvent("i1", "user"), callEvent()]), ["true"]);
});

test("cnd05: a decision naming no instruction is judged", () => {
  assert.deepEqual(judged([decisionEvent("d1")], "d1"), ["true"]);
});

test("CND-05: a parent or origin the bundle does not hold fails closed", () => {
  for (const key of ["parent", "origin"]) {
    assert.deepEqual(
      acts([instructionEvent("i1", "user", { [key]: "gone" }), callEvent()]),
      ["true"],
      key,
    );
  }
});

test("CND-05: an instruction that declares no source class fails closed", () => {
  const bare = memoryEvent("i0", "Instruction", {});
  assert.deepEqual(acts([bare, callEvent("tc1", "i0")]), ["true"]);
  assert.deepEqual(acts([bare, instructionEvent("i1", "user", { parent: "i0" }), callEvent()]), [
    "true",
  ]);
});

test("CND-05: an origin read the guard never ruled on fails closed", () => {
  const derived = instructionEvent("i1", "memory_trusted", { origin: "m1" });
  const unruled = {
    ...memoryEvent("m1", "MemoryRead", { record_refs: ["mem:r9"] }),
    agentcesourceclass: "self_report",
  };
  assert.deepEqual(acts([unruled, derived, callEvent()]), ["true"]);
  const ruled = memoryEvent("m1", "MemoryRead", { record_refs: ["mem:r9"], trust_min: "trusted" });
  assert.deepEqual(acts([ruled, derived, callEvent()]), ["false"]);
});

function lit(
  events: Record<string, unknown>[],
  id: string,
  predicate: string,
  domain = MINOR_DOMAIN,
) {
  return buildGraph(events, { domain }).literalValues(`agentce:event/${id}`, predicate);
}

function refEvent(id: string, ptype: string, data: Record<string, unknown>, decision?: string) {
  const refs = decision === undefined ? {} : { refs: { decision: `agentce:event/${decision}` } };
  return memoryEvent(id, ptype, { ...data, ...refs });
}

const HUMAN_A = { kind: "human", id: "alice" };
const HUMAN_B = { kind: "human", id: "bob" };
const IDP = "urn:example:service:identity-provider";

/**
 * An identity-provider SessionStart a session_ref can name, naming the human it authenticated (SPEC §10.4); by
 * default the actor its id names.
 */
function login(
  id: string,
  sourceClass = "independent_system",
  agent = IDP,
  principal: unknown = { kind: "human", id: id.replace(/^login-/, "") },
) {
  const named = principal === null ? {} : { principal };
  return {
    id,
    time: "2026-01-01T00:00:00Z",
    agentcesourceclass: sourceClass,
    data: { "@type": "SessionStart", agent: { id: agent }, ...named },
  };
}

/** The login a test actor's records name: login-<actor id>. */
function sessionOf(actor: unknown): string {
  const id =
    typeof actor === "object" && actor !== null ? (actor as { id?: unknown }).id : undefined;
  return `agentce:event/login-${String(id)}`;
}

const LOGINS = [login("login-alice"), login("login-bob")];

test("INC-03: triggersIncident marks only the decisions an incident names, directly or not", () => {
  const events = [
    decisionEvent("d1"),
    decisionEvent("d2"),
    decisionEvent("d3"),
    refEvent("o1", "Override", { replacement: "deny", original: "allow" }, "d2"),
    refEvent("inc1", "Incident", { related_refs: ["agentce:event/o1"] }, "d1"),
  ];
  assert.deepEqual(lit(events, "d1", "agentce:triggersIncident"), ["true"]);
  assert.deepEqual(lit(events, "d2", "agentce:triggersIncident"), ["true"]);
  assert.deepEqual(lit(events, "d3", "agentce:triggersIncident"), ["false"]);
  // No incident at all: no decision triggers one.
  assert.deepEqual(lit([decisionEvent("d1")], "d1", "agentce:triggersIncident"), ["false"]);
});

test("INC-03: an untraced incident holds every decision to the rule", () => {
  const unnamed = [decisionEvent("d1"), decisionEvent("d2"), refEvent("inc1", "Incident", {})];
  assert.deepEqual(lit(unnamed, "d1", "agentce:triggersIncident"), ["true"]);
  assert.deepEqual(lit(unnamed, "d2", "agentce:triggersIncident"), ["true"]);
  const unresolved = [
    decisionEvent("d1"),
    decisionEvent("d2"),
    refEvent("inc1", "Incident", { related_refs: ["agentce:event/missing"] }, "d1"),
  ];
  assert.deepEqual(lit(unresolved, "d1", "agentce:triggersIncident"), ["true"]);
  assert.deepEqual(lit(unresolved, "d2", "agentce:triggersIncident"), ["true"]);
});

test("OVS-08: coverage counts distinct human reviewers, two under dual control", () => {
  const approval = (id: string, actor: unknown) =>
    refEvent(
      id,
      "ApprovalDecided",
      { outcome: "approve", actor, session_ref: sessionOf(actor) },
      "d1",
    );
  const dual = {
    ...decisionEvent("d1"),
    data: { ...decisionEvent("d1").data, oversight_modality: "dual_control" },
  };
  const cover = (events: Record<string, unknown>[], domain = MINOR_DOMAIN) =>
    lit([...events, ...LOGINS], "d1", "agentce:oversightCoverageComplete", domain);
  assert.deepEqual(cover([decisionEvent("d1")]), ["false"]);
  assert.deepEqual(cover([decisionEvent("d1"), approval("a1", { kind: "service", id: "x" })]), [
    "false",
  ]);
  assert.deepEqual(cover([decisionEvent("d1"), approval("a1", HUMAN_A)]), ["true"]);
  assert.deepEqual(cover([dual, approval("a1", HUMAN_A), approval("a2", HUMAN_A)]), ["false"]);
  assert.deepEqual(cover([dual, approval("a1", HUMAN_A), approval("a2", HUMAN_B)]), ["true"]);
  const declared = DomainBinding.fromDict({
    decision_types: [
      {
        id: "agentce:CreditDecision",
        subclass_of: "agentce:ConsequentialDecision",
        consequential: true,
        required_oversight_modality: "dual_control",
      },
    ],
  });
  assert.deepEqual(cover([decisionEvent("d1"), approval("a1", HUMAN_A)], declared), ["false"]);
  assert.deepEqual(
    cover([decisionEvent("d1"), approval("a1", HUMAN_A), approval("a2", HUMAN_B)], declared),
    ["true"],
  );
});

test("only an approve outcome approves: a reject, an edit or no outcome is a review, never an approval", () => {
  for (const [outcome, approved] of [
    ["approve", true],
    ["reject", false],
    ["edit", false],
    [undefined, false],
  ] as const) {
    const events = [
      decisionEvent("d1"),
      refEvent(
        "a1",
        "ApprovalDecided",
        { outcome, actor: HUMAN_A, session_ref: sessionOf(HUMAN_A) },
        "d1",
      ),
      ...LOGINS,
    ];
    const store = buildGraph(events, { domain: MINOR_DOMAIN });
    assert.deepEqual(store.objects("agentce:event/d1", "agentce:reviewedBy"), ["agentce:event/a1"]);
    assert.deepEqual(
      store.objects("agentce:event/d1", "agentce:approvedBy"),
      approved ? ["agentce:event/a1"] : [],
      String(outcome),
    );
    assert.deepEqual(lit(events, "d1", "agentce:oversightCoverageComplete"), [String(approved)]);
  }
});

/** A verified ApprovalDecided on d1 by `who` with outcome `out`, recorded at `time`. */
function decided(id: string, who: typeof HUMAN_A | typeof HUMAN_B, out: string, time: string) {
  return {
    ...refEvent(
      id,
      "ApprovalDecided",
      { outcome: out, actor: who, session_ref: sessionOf(who) },
      "d1",
    ),
    time,
  };
}

test("a later reject by the same human withdraws the approval: the most recent decision before the action governs", () => {
  for (const [actor, outcome, decidedAt, approved] of [
    [HUMAN_A, "reject", "2026-01-10T02:00:00Z", false],
    [HUMAN_A, "edit", "2026-01-10T02:00:00Z", false],
    [HUMAN_A, "reject", "2026-01-10T01:00:00Z", false],
    [HUMAN_A, "reject", "2026-01-10T03:00:00Z", false],
    [HUMAN_A, "reject", "2026-01-10T04:00:00Z", true],
    [HUMAN_A, "reject", "2026-01-10T02:00:00", false],
    [HUMAN_A, "reject", "2026-01-10T03:30:00+02:00", false],
    [HUMAN_A, "reject", "2026-01-10T00:30:00Z", true],
    [HUMAN_B, "reject", "2026-01-10T02:00:00Z", true],
  ] as const) {
    const events = [
      decisionEvent("d1"),
      { ...refEvent("t0", "ToolCall", {}, "d1"), time: "2026-01-10T03:00:00Z" },
      decided("a1", HUMAN_A, "approve", "2026-01-10T01:00:00Z"),
      decided("a2", actor, outcome, decidedAt),
      ...LOGINS,
    ];
    const store = buildGraph(events, { domain: MINOR_DOMAIN });
    assert.deepEqual(store.objects("agentce:event/d1", "agentce:reviewedBy"), [
      "agentce:event/a1",
      "agentce:event/a2",
    ]);
    assert.deepEqual(
      store.objects("agentce:event/d1", "agentce:approvedBy"),
      approved ? ["agentce:event/a1"] : [],
      `${outcome} ${decidedAt}`,
    );
  }
});

test("the same human's reject withdraws the approval even when it fails the human-actor rule", () => {
  for (const login of [undefined, sessionOf(HUMAN_B)]) {
    const data: Record<string, unknown> = { outcome: "reject", actor: HUMAN_A };
    if (login !== undefined) {
      data.session_ref = login;
    }
    const events = [
      decisionEvent("d1"),
      {
        ...refEvent(
          "a1",
          "ApprovalDecided",
          { outcome: "approve", actor: HUMAN_A, session_ref: sessionOf(HUMAN_A) },
          "d1",
        ),
        time: "2026-01-10T01:00:00Z",
      },
      { ...refEvent("a2", "ApprovalDecided", data, "d1"), time: "2026-01-10T02:00:00Z" },
      ...LOGINS,
    ];
    const store = buildGraph(events, { domain: MINOR_DOMAIN });
    assert.deepEqual(store.objects("agentce:event/d1", "agentce:reviewedBy"), ["agentce:event/a1"]);
    assert.deepEqual(store.objects("agentce:event/d1", "agentce:approvedBy"), [], String(login));
  }
});

test("an approve after the action ran is a review, not an approval: order compares instants", () => {
  for (const [approvedAt, approved] of [
    ["2026-01-10T00:00:00Z", true],
    ["2026-01-10T01:00:00Z", true],
    ["2026-01-10T01:00:00.001Z", false],
    ["2026-01-10T00:30:00-05:00", false],
    ["2026-01-10T05:00:00+04:00", true],
    ["2026-01-10T00:30:00", false],
  ] as const) {
    const events = [
      decisionEvent("d1"),
      { ...refEvent("t0", "ToolCall", {}, "d1"), time: "2026-01-10T01:00:00Z" },
      {
        ...refEvent(
          "a1",
          "ApprovalDecided",
          { outcome: "approve", actor: HUMAN_A, session_ref: sessionOf(HUMAN_A) },
          "d1",
        ),
        time: approvedAt,
      },
      ...LOGINS,
    ];
    const store = buildGraph(events, { domain: MINOR_DOMAIN });
    assert.deepEqual(store.objects("agentce:event/d1", "agentce:reviewedBy"), ["agentce:event/a1"]);
    assert.deepEqual(
      store.objects("agentce:event/d1", "agentce:approvedBy"),
      approved ? ["agentce:event/a1"] : [],
      approvedAt,
    );
    assert.deepEqual(lit(events, "d1", "agentce:oversightCoverageComplete"), [String(approved)]);
  }
});

test("an approve after the outcome is a review, not an approval, wherever the Outcome sits", () => {
  for (const [approvedAt, approved] of [
    ["2026-01-10T01:00:00Z", true],
    ["2026-01-10T01:00:01Z", false],
    ["2026-01-10T00:30:00-05:00", false],
    ["2026-01-10T00:30:00", false],
  ] as const) {
    const events = [
      decisionEvent("d1"),
      {
        ...refEvent(
          "a1",
          "ApprovalDecided",
          { outcome: "approve", actor: HUMAN_A, session_ref: sessionOf(HUMAN_A) },
          "d1",
        ),
        time: approvedAt,
      },
      ...LOGINS,
      { ...refEvent("o1", "Outcome", {}, "d1"), time: "2026-01-10T01:00:00Z" },
    ];
    const store = buildGraph(events, { domain: MINOR_DOMAIN });
    assert.deepEqual(store.objects("agentce:event/d1", "agentce:reviewedBy"), ["agentce:event/a1"]);
    assert.deepEqual(
      store.objects("agentce:event/d1", "agentce:approvedBy"),
      approved ? ["agentce:event/a1"] : [],
      approvedAt,
    );
  }
});

test("a bare id names the decision in the approval bound, and an unresolved effect bounds every decision", () => {
  const d1 = "agentce:event/d1";
  for (const [outcomeRef, approvalRef, approved] of [
    ["d1", d1, false],
    [`${d1} `, d1, false],
    ["\u00a0d1", d1, false],
    ["AGENTCE:EVENT/D1", d1, false],
    ["a1", d1, false],
    [d1, "d1", false],
    [null, "d1", true],
    [null, ` ${d1}`, true],
  ] as const) {
    const events: Record<string, unknown>[] = [
      decisionEvent("d1"),
      {
        ...memoryEvent("a1", "ApprovalDecided", {
          outcome: "approve",
          actor: HUMAN_A,
          session_ref: sessionOf(HUMAN_A),
          refs: { decision: approvalRef },
        }),
        time: "2026-01-10T05:00:00Z",
      },
      ...LOGINS,
    ];
    if (outcomeRef !== null) {
      events.push({
        ...memoryEvent("o1", "Outcome", { refs: { decision: outcomeRef } }),
        time: "2026-01-10T01:00:00Z",
      });
    }
    const store = buildGraph(events, { domain: MINOR_DOMAIN });
    assert.deepEqual(store.objects(d1, "agentce:reviewedBy"), ["agentce:event/a1"]);
    assert.deepEqual(
      store.objects(d1, "agentce:approvedBy"),
      approved ? ["agentce:event/a1"] : [],
      `${outcomeRef} ${approvalRef}`,
    );
  }
});

test("a bare decision ref links every decision edge, read before its Decision (18.156)", () => {
  const d1 = "agentce:event/d1";
  const events: Record<string, unknown>[] = [
    memoryEvent("t0", "ToolCall", { refs: { decision: "d1" } }),
    memoryEvent("o1", "Outcome", { refs: { decision: " d1" } }),
    memoryEvent("v1", "Override", { refs: { decision: `${d1}\t` } }),
    memoryEvent("x1", "Interrupt", { refs: { decision: "d1" } }),
    memoryEvent("n1", "Notice", { refs: { decision: "\u00a0d1 " } }),
    memoryEvent("t1", "ToolCall", { refs: { decision: "nope" } }),
    decisionEvent("d1"),
  ];
  const store = buildGraph(events, { domain: MINOR_DOMAIN });
  assert.deepEqual(store.objects("agentce:event/t0", "agentce:executes"), [d1]);
  for (const [predicate, node] of [
    ["agentce:resultedIn", "o1"],
    ["agentce:overriddenBy", "v1"],
    ["agentce:interruptedBy", "x1"],
    ["agentce:notifiedBy", "n1"],
  ]) {
    assert.deepEqual(store.objects(d1, predicate), [`agentce:event/${node}`], predicate);
  }
  assert.deepEqual(store.literalValues("agentce:event/t0", "agentce:executesConsequential"), [
    "true",
  ]);
  assert.deepEqual(store.literalValues("agentce:event/t1", "agentce:executesConsequential"), [
    "false",
  ]);
  assert.deepEqual(store.objects("agentce:event/t1", "agentce:executes"), ["nope"]);
});

test("a null decision ref bounds every approval, and an absent one bounds none (18.157)", () => {
  const d1 = "agentce:event/d1";
  for (const [data, approved] of [
    [{ refs: { decision: null } }, false],
    [{ refs: {} }, true],
    [{}, true],
  ] as const) {
    const events: Record<string, unknown>[] = [
      decisionEvent("d1"),
      {
        ...memoryEvent("a1", "ApprovalDecided", {
          outcome: "approve",
          actor: HUMAN_A,
          session_ref: sessionOf(HUMAN_A),
          refs: { decision: d1 },
        }),
        time: "2026-01-10T05:00:00Z",
      },
      { ...memoryEvent("o1", "Outcome", { ...data }), time: "2026-01-10T01:00:00Z" },
      ...LOGINS,
    ];
    const store = buildGraph(events, { domain: MINOR_DOMAIN });
    assert.deepEqual(
      store.objects(d1, "agentce:approvedBy"),
      approved ? ["agentce:event/a1"] : [],
      JSON.stringify(data),
    );
  }
});

test("OVS-07: an override is effective when it replaces a held decision's outcome", () => {
  const effective = (data: Record<string, unknown>, decision = "d1") =>
    lit(
      [decisionEvent("d1"), refEvent("o1", "Override", data, decision)],
      "o1",
      "agentce:interventionEffective",
    );
  assert.deepEqual(effective({ original: "allow", replacement: "deny" }), ["true"]);
  assert.deepEqual(effective({ original: "allow", replacement: "allow" }), ["false"]);
  assert.deepEqual(effective({ original: { a: 1, b: [2] }, replacement: { b: [2], a: 1 } }), [
    "false",
  ]);
  assert.deepEqual(effective({ original: "allow" }), ["false"]);
  assert.deepEqual(effective({ original: "allow", replacement: "deny" }, "missing"), ["false"]);
});

test("OVS-07: an interrupt is effective when a named mechanism halted or paused the agent", () => {
  const effective = (data: Record<string, unknown>) =>
    lit([refEvent("x1", "Interrupt", data)], "x1", "agentce:interventionEffective");
  assert.deepEqual(effective({ effect: "halted", mechanism: "kill_switch" }), ["true"]);
  assert.deepEqual(effective({ effect: "paused", mechanism: "manual" }), ["true"]);
  assert.deepEqual(effective({ effect: "ignored", mechanism: "manual" }), ["false"]);
  assert.deepEqual(effective({ effect: "halted", mechanism: "other" }), ["false"]);
});

test("OVS-07: an intervention is by a human only when a human actor is named", () => {
  const byHuman = (ptype: string, actor: unknown) =>
    lit(
      [refEvent("x1", ptype, { actor, session_ref: sessionOf(actor) }), ...LOGINS],
      "x1",
      "agentce:interventionByHuman",
    );
  for (const ptype of ["Override", "Interrupt"]) {
    assert.deepEqual(byHuman(ptype, HUMAN_A), ["true"], ptype);
    assert.deepEqual(byHuman(ptype, { kind: "human", id: "" }), ["false"], ptype);
    assert.deepEqual(byHuman(ptype, { kind: "service", id: "svc" }), ["false"], ptype);
    assert.deepEqual(byHuman(ptype, undefined), ["false"], ptype);
  }
});

test("ROB-07: an incident is responded to when detection and a response are recorded", () => {
  const responded = (data: Record<string, unknown>) =>
    lit([refEvent("inc1", "Incident", data)], "inc1", "agentce:incidentResponded");
  const at = "2026-01-02T00:00:00Z";
  for (const key of ["causal_assessment_at", "provider_notified_at", "reported_at"]) {
    assert.deepEqual(responded({ detected_at: at, [key]: at }), ["true"], key);
    assert.deepEqual(responded({ [key]: at }), ["false"], key);
  }
  assert.deepEqual(responded({ detected_at: at }), ["false"]);
  assert.deepEqual(responded({ detected_at: at, reported_at: "" }), ["false"]);
});

test("RSK-02: a decision is risk-reviewed by a review or a held policy decision its authorization names", () => {
  const policy = refEvent("p1", "PolicyDecision", { decision: "allow" });
  const authorized = (ref: unknown, key = "authorization") => {
    const d = decisionEvent("d1");
    return { ...d, data: { ...d.data, refs: { [key]: ref } } };
  };
  const risk = (events: Record<string, unknown>[]) =>
    lit([...events, ...LOGINS], "d1", "agentce:riskReviewed");
  const review = refEvent(
    "a1",
    "ApprovalDecided",
    { actor: HUMAN_A, session_ref: sessionOf(HUMAN_A) },
    "d1",
  );
  assert.deepEqual(risk([decisionEvent("d1"), review]), ["true"]);
  assert.deepEqual(risk([policy, authorized("agentce:event/p1")]), ["true"]);
  assert.deepEqual(risk([policy, authorized("agentce:event/p1", "request")]), ["true"]);
  assert.deepEqual(risk([decisionEvent("d1")]), ["false"]);
  const outcome = refEvent("o1", "Outcome", {}, "d1");
  assert.deepEqual(risk([policy, authorized("agentce:event/missing")]), ["false"]);
  assert.deepEqual(risk([outcome, authorized("agentce:event/o1")]), ["false"]);
  assert.deepEqual(risk([policy, authorized("p1")]), ["false"]);
  assert.deepEqual(risk([policy, authorized(["agentce:event/p1"])]), ["false"]);
});

test("human-actor rule: an approval or override counts only with a held login outside the agent and its chain", () => {
  const agent = { id: "spiffe://corp/agents/a" };
  const owner = { kind: "human", id: "owner" };
  const decision = (modality?: string) => {
    const d = decisionEvent("d1");
    return { ...d, data: { ...d.data, agent, acted_for: ["owner"], oversight_modality: modality } };
  };
  const approval = (id: string, actor: unknown, session = sessionOf(actor)) =>
    refEvent(id, "ApprovalDecided", { outcome: "approve", actor, session_ref: session }, "d1");
  const reviewed = (...events: Record<string, unknown>[]) =>
    lit([decision(), ...events], "d1", "agentce:oversightCoverageComplete");
  assert.deepEqual(reviewed(approval("a1", HUMAN_A), ...LOGINS), ["true"]);
  assert.deepEqual(reviewed(approval("a1", HUMAN_A)), ["false"]); // the login is not held
  assert.deepEqual(reviewed(approval("a1", HUMAN_A), login("login-alice", "self_report")), [
    "false",
  ]);
  assert.deepEqual(reviewed(approval("a1", HUMAN_A), login("login-alice", undefined, agent.id)), [
    "false",
  ]);
  const toolCall = refEvent("login-alice", "ToolCall", { tool: { name: "x" } });
  assert.deepEqual(reviewed(approval("a1", HUMAN_A), toolCall), ["false"]);
  assert.deepEqual(reviewed(approval("a1", owner), login("login-owner")), ["false"]);
  // The login names someone else, or nobody: it is not the actor's own.
  const named = (principal: unknown) =>
    reviewed(approval("a1", HUMAN_A), login("login-alice", undefined, IDP, principal));
  assert.deepEqual(named(HUMAN_B), ["false"]);
  assert.deepEqual(named(null), ["false"]);
  // Ids compare after trimming one fixed whitespace set from both ends.
  assert.deepEqual(named({ kind: "human", id: "\u00a0alice\ufeff" }), ["true"]);
  const noSession = refEvent("a1", "ApprovalDecided", { outcome: "approve", actor: HUMAN_A }, "d1");
  assert.deepEqual(reviewed(noSession, ...LOGINS), ["false"]);
  const override = refEvent(
    "o1",
    "Override",
    { actor: owner, session_ref: sessionOf(owner) },
    "d1",
  );
  assert.deepEqual(
    lit([decision(), override, login("login-owner")], "o1", "agentce:interventionByHuman"),
    ["false"],
  );
  // Dual control: two ids on one login, or one id on two logins, are one human.
  const dual = (...events: Record<string, unknown>[]) =>
    lit(
      [decision("dual_control"), ...events, ...LOGINS],
      "d1",
      "agentce:oversightCoverageComplete",
    );
  const alias = { kind: "human", id: "alice " };
  assert.deepEqual(dual(approval("a1", HUMAN_A), approval("a2", HUMAN_B)), ["true"]);
  assert.deepEqual(dual(approval("a1", HUMAN_A), approval("a2", alias, sessionOf(HUMAN_A))), [
    "false",
  ]);
  assert.deepEqual(dual(approval("a1", HUMAN_A), approval("a2", HUMAN_A, sessionOf(HUMAN_B))), [
    "false",
  ]);
  // File order never matters: the login after the approval still counts.
  assert.deepEqual(
    lit(
      [...LOGINS, approval("a1", HUMAN_A), decision()],
      "d1",
      "agentce:oversightCoverageComplete",
    ),
    ["true"],
  );
});

test("not waivable: the store lists failing oversight records and the decision they name (18.186)", () => {
  // What the deviation register cannot waive (SPEC §10.4): each failing ApprovalDecided, Override and Interrupt,
  // and the held decision a failing approval names; a passing approval adds nothing.
  const failed = (events: Record<string, unknown>[]) =>
    [...buildGraph(events, { domain: MINOR_DOMAIN }).humanActorFailed].sort();
  const approval = (decision: string) =>
    refEvent(
      "a1",
      "ApprovalDecided",
      { outcome: "approve", actor: HUMAN_A, session_ref: sessionOf(HUMAN_A) },
      decision,
    );
  assert.deepEqual(failed([decisionEvent("d1"), approval("d1")]), [
    "agentce:event/a1",
    "agentce:event/d1",
  ]);
  assert.deepEqual(failed([decisionEvent("d1"), approval("d1"), ...LOGINS]), []);
  const service = { kind: "service", id: "svc" };
  const intervention = (id: string, ptype: string) =>
    refEvent(id, ptype, { actor: service, session_ref: sessionOf(service) }, "d1");
  assert.deepEqual(
    failed([decisionEvent("d1"), intervention("o1", "Override"), intervention("i1", "Interrupt")]),
    ["agentce:event/i1", "agentce:event/o1"],
  );
  // A failing approval naming no held decision adds only itself.
  assert.deepEqual(failed([decisionEvent("d1"), approval("nowhere")]), ["agentce:event/a1"]);
});

test("task scope: a tasked ToolCall is within scope only when every named task's scope allows it", () => {
  const domain = DomainBinding.fromDict({
    task_scopes: [
      { task_type: "review", allowed_tools: ["lookup"], allowed_effect_classes: ["read"] },
      { task_type: "pay", allowed_tools: ["pay"], allowed_effect_classes: ["spend"] },
    ],
  });
  const call = (id: string, tool: string, effect: string, extra: Record<string, unknown> = {}) => {
    const { refs_task: task, ...rest } = extra;
    return {
      id,
      source: "urn:src:gw",
      subject: "spiffe://corp/agents/a",
      type: "org.agent-conformance.evidence.ToolCall.v1",
      data: {
        "@type": "ToolCall",
        tool: { name: tool },
        effect_class: effect,
        ...(task === undefined ? {} : { refs: { task } }),
      },
      ...rest,
    };
  };
  const calls = [
    call("in", "lookup", "read", { agentcetask: "review" }),
    call("effect", "lookup", "irreversible", { agentcetask: "review" }),
    call("tool", "pay", "read", { refs_task: "review" }),
    call("unknown", "lookup", "read", { agentcetask: "task-1" }),
    call("differ", "lookup", "read", { agentcetask: "pay", refs_task: "review" }),
    call("untasked", "ping", "irreversible"),
  ];
  const store = buildGraph(calls, { domain });
  const got = Object.fromEntries(
    calls.map((c) => [
      c.id,
      store.literalValues(`agentce:event/${c.id}`, "agentce:withinScope")[0],
    ]),
  );
  assert.deepEqual(got, {
    in: "true",
    effect: "false",
    tool: "false",
    unknown: "false",
    differ: "false",
    untasked: "true",
  });
});

// --- CND-02: actions are the population, and each one's gate (SPEC §7.7.4; 18.187) ------------------

const GATED_DOMAIN = DomainBinding.fromDict({
  decision_types: [
    {
      id: "agentce:CreditDecision",
      subclass_of: "agentce:ConsequentialDecision",
      consequential: true,
    },
    { id: "agentce:MinorDecision", subclass_of: "agentce:Decision", consequential: false },
  ],
  task_scopes: [
    {
      task_type: "review",
      allowed_effect_classes: ["read", "write"],
      requires_approval: ["irreversible", "write"],
    },
    { task_type: "payout", allowed_effect_classes: ["spend"] },
  ],
});

interface GateOpts {
  approved?: boolean;
  dtype?: string;
  task?: string | null;
  decisionRef?: unknown;
  policyClass?: string;
  request?: string;
  ptype?: string;
}

function gateEvents(
  effect: string | null = "irreversible",
  policy: string | null = "require_approval",
  opts: GateOpts = {},
): Record<string, unknown>[] {
  const events: Record<string, unknown>[] = [
    memoryEvent("d1", "Decision", { decision_type: opts.dtype ?? "agentce:CreditDecision" }),
  ];
  if (opts.approved ?? true) {
    events.push(login("login-alice"), {
      ...refEvent(
        "ap1",
        "ApprovalDecided",
        { outcome: "approve", actor: HUMAN_A, session_ref: sessionOf(HUMAN_A) },
        "d1",
      ),
      time: "2026-01-01T00:00:01Z",
    });
  }
  if (policy !== null) {
    events.push({
      ...memoryEvent("pd1", "PolicyDecision", {
        decision: policy,
        refs: { request: `agentce:event/${opts.request ?? "tc1"}` },
      }),
      agentcesourceclass: opts.policyClass ?? "enforcement_point",
    });
  }
  const data: Record<string, unknown> = {
    tool: { name: "book" },
    refs: { decision: opts.decisionRef === undefined ? "agentce:event/d1" : opts.decisionRef },
  };
  if (effect !== null) {
    data.effect_class = effect;
  }
  const task = opts.task === undefined ? "review" : opts.task;
  events.push({
    ...memoryEvent("tc1", opts.ptype ?? "ToolCall", data),
    time: "2026-01-01T00:00:02Z",
    ...(task === null ? {} : { agentcetask: task }),
  });
  return events;
}

function gate(events: Record<string, unknown>[], node = "tc1"): [boolean, unknown, unknown] {
  const store = buildGraph(events, { domain: GATED_DOMAIN });
  const iri = `agentce:event/${node}`;
  return [
    store.isA(iri, "agentce:GatedAction"),
    store.literalValues(iri, "agentce:gateMet")[0] ?? null,
    store.literalValues(iri, "agentce:gateJudged")[0] ?? null,
  ];
}

const SHUT = [true, "false", "true"];
const OPEN = [true, "true", "true"];
const OUTSIDE = [false, null, null];

test("CND-02: a requires_approval action needs an enforcement-point decision and an approval", () => {
  assert.deepEqual(gate(gateEvents()), OPEN);
  assert.deepEqual(gate(gateEvents("irreversible", "allow")), OPEN);
  assert.deepEqual(gate(gateEvents("irreversible", "require_approval", { approved: false })), SHUT);
  // the agent's own allow, an allow naming another call, and no PolicyDecision at all are no gate
  assert.deepEqual(
    gate(gateEvents("irreversible", "require_approval", { policyClass: "self_report" })),
    SHUT,
  );
  assert.deepEqual(gate(gateEvents("irreversible", "require_approval", { request: "tcx" })), SHUT);
  assert.deepEqual(gate(gateEvents("irreversible", null)), SHUT);
});

test("CND-02: a deny closes the gate whatever was approved", () => {
  assert.deepEqual(gate(gateEvents("irreversible", "deny")), SHUT);
  assert.deepEqual(gate(gateEvents("external_communication", "deny")), SHUT);
});

test("CND-02: the approval is tied to the decision the action names", () => {
  for (const ref of [null, "agentce:event/d-missing", "agentce:event/d2"]) {
    assert.deepEqual(
      gate(gateEvents("irreversible", "require_approval", { decisionRef: ref })),
      SHUT,
    );
  }
  const events = gateEvents();
  const action = events[events.length - 1] as { data: { refs: Record<string, unknown> } };
  action.data.refs = {};
  assert.deepEqual(gate(events), SHUT);
});

test("CND-02: other high-effect classes need an allow or a released require_approval", () => {
  for (const effect of ["external_communication", "spend", "physical"]) {
    assert.deepEqual(gate(gateEvents(effect, "allow", { approved: false })), OPEN);
    assert.deepEqual(gate(gateEvents(effect, "require_approval")), OPEN);
    assert.deepEqual(gate(gateEvents(effect, "require_approval", { approved: false })), SHUT);
    assert.deepEqual(gate(gateEvents(effect, "allow", { policyClass: "self_report" })), SHUT);
  }
});

test("CND-02: the scope marks the population and read stays outside", () => {
  assert.deepEqual(gate(gateEvents("read", null, { approved: false })), OUTSIDE);
  // write is no high-effect class, but this scope marks it requires_approval
  assert.deepEqual(gate(gateEvents("write", "allow", { approved: false })), SHUT);
  assert.deepEqual(gate(gateEvents("write", "allow")), OPEN);
  // the decision's own type does not matter: the action is what CND-02 counts
  assert.deepEqual(
    gate(gateEvents("irreversible", "require_approval", { dtype: "agentce:MinorDecision" })),
    OPEN,
  );
});

test("CND-02: a high-effect action in no declared scope is held to the approval path", () => {
  for (const task of [null, "unknown-task"]) {
    assert.deepEqual(
      gate(gateEvents("external_communication", "allow", { approved: false, task })),
      SHUT,
    );
    assert.deepEqual(gate(gateEvents("write", "allow", { approved: false, task })), OUTSIDE);
  }
});

test("CND-02: an action with no effect class and an unexecuted decision are unjudged", () => {
  assert.deepEqual(gate(gateEvents(null)), [true, "true", "false"]);
  assert.deepEqual(gate(gateEvents().slice(0, -1), "d1"), [true, "true", "false"]);
  // a minor decision no action executes is not in the population
  const minor = gateEvents("irreversible", "require_approval", { dtype: "agentce:MinorDecision" });
  assert.deepEqual(gate(minor.slice(0, -1), "d1"), OUTSIDE);
});

test("CND-02: a resource access counts only with an effect class", () => {
  assert.deepEqual(
    gate(gateEvents("irreversible", "require_approval", { ptype: "ResourceAccess" })),
    OPEN,
  );
  assert.deepEqual(
    gate(gateEvents(null, "require_approval", { ptype: "ResourceAccess" })),
    OUTSIDE,
  );
});

test("CND-02: releasedBy links the approvals the gate rests on", () => {
  const released = (events: Record<string, unknown>[]) =>
    buildGraph(events, { domain: GATED_DOMAIN }).objects("agentce:event/tc1", "agentce:releasedBy");
  assert.deepEqual(released(gateEvents()), ["agentce:event/ap1"]);
  // an allow-path action passed on the enforcement point's allow rests on no approval
  assert.deepEqual(released(gateEvents("external_communication", "allow")), []);
  assert.deepEqual(released(gateEvents("external_communication", "require_approval")), [
    "agentce:event/ap1",
  ]);
});

// --- CND-07: task budgets (SPEC §7.7.4, §7.7.6; 18.175) ----------------------------------------------------------

function budgetCall(id: string, tool: string, effect: string | null, task?: string) {
  return {
    id,
    source: "urn:src:gw",
    subject: "spiffe://corp/agents/a",
    type: "org.agent-conformance.evidence.ToolCall.v1",
    data: {
      "@type": "ToolCall",
      tool: { name: tool },
      ...(effect === null ? {} : { effect_class: effect }),
    },
    ...(task === undefined ? {} : { agentcetask: task }),
  };
}

function modelCall(id: string, task: string | null, usage: unknown) {
  return {
    id,
    source: "urn:src:agent",
    subject: "spiffe://corp/agents/a",
    type: "org.agent-conformance.evidence.ModelCall.v1",
    data: { "@type": "ModelCall", usage },
    ...(task === null ? {} : { agentcetask: task }),
  };
}

/** Each action's withinBudget and whether the records could tell (no agentce:BudgetUnjudged type). */
function budgetFlags(
  events: { id: string }[],
  budgets: unknown,
  binding: Record<string, unknown> = {},
): Record<string, [unknown, boolean]> {
  const entry: Record<string, unknown> = {
    task_type: "review",
    allowed_effect_classes: ["read", "spend"],
  };
  if (budgets !== null) entry.budgets = budgets;
  const store = buildGraph(events as Record<string, unknown>[], {
    domain: DomainBinding.fromDict({ task_scopes: [entry], ...binding }),
  });
  return Object.fromEntries(
    events.map((e) => [
      e.id,
      [
        store.literalValues(`agentce:event/${e.id}`, "agentce:withinBudget")[0],
        !store.isA(`agentce:event/${e.id}`, "agentce:BudgetUnjudged"),
      ],
    ]),
  );
}

const flagSet = (flags: Record<string, [unknown, boolean]>) => [
  ...new Set(Object.values(flags).map((f) => JSON.stringify(f))),
];

test("task budget tokens: summed per task, every action of the task reads over", () => {
  const under = [
    modelCall("m1", "review", { input_tokens: 400, output_tokens: 100 }),
    budgetCall("t1", "lookup", "read", "review"),
  ];
  assert.deepEqual(budgetFlags(under, { tokens: 500 }), {
    m1: ["true", true],
    t1: ["true", true],
  });
  const over = [...under, modelCall("m2", "review", { input_tokens: 1, output_tokens: 0 })];
  assert.deepEqual(flagSet(budgetFlags(over, { tokens: 500 })), ['["false",true]']);
  // a whole number written 500.0 reads as 500 (JSON and YAML readers give the same number)
  assert.deepEqual(flagSet(budgetFlags(over, { tokens: 500.0 })), ['["false",true]']);
});

test("task budget tokens: an unreadable usage or limit leaves an under-limit task untold", () => {
  const under = [
    modelCall("m1", "review", { input_tokens: 400, output_tokens: 100 }),
    budgetCall("t1", "lookup", "read", "review"),
  ];
  const unread = [...under, modelCall("m2", "review", { input_tokens: 1 })];
  assert.deepEqual(flagSet(budgetFlags(unread, { tokens: 500 })), ['["true",false]']);
  // an overrun the readable usage already shows is told, whatever else is unreadable
  const over = [...unread, modelCall("m3", "review", { input_tokens: 1, output_tokens: 0 })];
  assert.deepEqual(flagSet(budgetFlags(over, { tokens: 500 })), ['["false",true]']);
  for (const limit of [500.5, "500", true, -1]) {
    assert.deepEqual(flagSet(budgetFlags(under, { tokens: limit })), ['["true",false]']);
  }
});

test("task budget tokens: counts are held to 2^53, a sum past it reads over, a limit past it is untold", () => {
  const big = Number.MAX_SAFE_INTEGER;
  const many = Array.from({ length: 513 }, (_, i) =>
    modelCall(`m${i}`, "review", { input_tokens: big, output_tokens: big }),
  );
  assert.deepEqual(flagSet(budgetFlags(many, { tokens: 1000 })), ['["false",true]']);
  const huge = [modelCall("m", "review", { input_tokens: 2 ** 62, output_tokens: 0 })];
  assert.deepEqual(flagSet(budgetFlags(huge, { tokens: big })), ['["false",true]']);
  const past = [modelCall("m", "review", { input_tokens: 2 ** 54, output_tokens: 1 })];
  assert.deepEqual(flagSet(budgetFlags(past, { tokens: 2 ** 54 })), ['["true",false]']);
  const edge = [modelCall("m", "review", { input_tokens: big, output_tokens: 0 })];
  assert.deepEqual(flagSet(budgetFlags(edge, { tokens: big })), ['["true",true]']);
  assert.deepEqual(flagSet(budgetFlags(edge, { tokens: big - 1 })), ['["false",true]']);
});

test("task budget spend and compute: spend told only against zero, compute never", () => {
  const spend = [budgetCall("s1", "pay", "spend", "review")];
  assert.deepEqual(budgetFlags(spend, { spend: 0 }), { s1: ["false", true] });
  assert.deepEqual(budgetFlags(spend, { spend: 10 }), { s1: ["true", false] });
  const read = [budgetCall("r1", "lookup", "read", "review")];
  assert.deepEqual(budgetFlags(read, { spend: 10 }), { r1: ["true", true] });
  assert.deepEqual(budgetFlags([budgetCall("x1", "pay", null, "review")], { spend: 0 }), {
    x1: ["true", false],
  });
  assert.deepEqual(budgetFlags(read, { tokens: 5 }), { r1: ["true", false] });
  const billed = [...read, modelCall("m1", "review", { input_tokens: 1, output_tokens: 1 })];
  assert.deepEqual(flagSet(budgetFlags(billed, { spend: 0 })), ['["true",false]']);
  assert.deepEqual(budgetFlags(read, { compute_seconds: 60 }), { r1: ["true", false] });
  assert.deepEqual(budgetFlags(read, "lots"), { r1: ["true", false] });
  assert.deepEqual(budgetFlags(read, null), { r1: ["true", true] });
});

test("task budget scope: a task held to no scope is untold, an untasked call sets no limit", () => {
  const other = [
    budgetCall("o1", "lookup", "read", "elsewhere"),
    budgetCall("u1", "lookup", "read"),
  ];
  const flags = budgetFlags(other, null, {
    task_scopes: [{ task_type: "review", budgets: { tokens: 5 } }, { task_type: "pay" }],
  });
  assert.deepEqual(flags, { o1: ["true", false], u1: ["true", true] });
  const store = buildGraph(other, { domain: DomainBinding.fromDict({}) });
  assert.equal(store.isA("agentce:event/o1", "agentce:BudgetUnjudged"), false);
  const untasked = modelCall("m0", null, { input_tokens: 9, output_tokens: 9 });
  const scoped = DomainBinding.fromDict({ task_scopes: [{ task_type: "review" }] });
  assert.equal(
    buildGraph([untasked], { domain: scoped }).isA("agentce:event/m0", "agentce:TaskModelCall"),
    false,
  );
});

// --- CND-04: consequential actions, their instruction chain, role and delegation (SPEC §7.7.4; 18.122) ------

type Ev = Record<string, unknown> & { data: Record<string, unknown> };

function rolesDomain(roles: unknown[] | null = ["credit_officer"]): DomainBinding {
  const scope: Record<string, unknown> = { task_type: "review", allowed_effect_classes: ["read"] };
  if (roles !== null) {
    scope.authorised_principal_roles = [...roles];
  }
  return DomainBinding.fromDict({
    decision_types: [
      {
        id: "agentce:CreditDecision",
        subclass_of: "agentce:ConsequentialDecision",
        consequential: true,
      },
      { id: "agentce:MinorDecision", subclass_of: "agentce:Decision", consequential: false },
    ],
    task_scopes: [scope],
  });
}

interface ProvenanceOpts {
  instruction?: unknown;
  sourceClass?: unknown;
  principal?: unknown;
  parent?: Ev;
  origin?: Ev;
  delegation?: string | null;
  dtype?: string;
}

/** A consequential decision d1 executed by tc1, both acting on i1 (CND-04, SPEC §7.7.4). */
function provenanceEvents(opts: ProvenanceOpts = {}): Ev[] {
  const instruction = "instruction" in opts ? opts.instruction : "agentce:event/i1";
  const principal =
    "principal" in opts ? opts.principal : { id: "user:alice", role: "credit_officer" };
  const delegation = opts.delegation === undefined ? "verified" : opts.delegation;
  const data: Record<string, unknown> = {
    source_class: "sourceClass" in opts ? opts.sourceClass : "user",
  };
  if (principal !== null) {
    data.principal = principal;
  }
  const events: Ev[] = [
    memoryEvent("d1", "Decision", {
      decision_type: opts.dtype ?? "agentce:CreditDecision",
      refs: { instruction },
    }),
    memoryEvent("i1", "Instruction", data),
  ];
  const refs: Record<string, unknown> = { decision: "agentce:event/d1", instruction };
  if (delegation !== null) {
    refs.delegation = "agentce:event/g1";
    if (delegation !== "unheld") {
      events.push(memoryEvent("g1", "DelegationIssued", { verification: { status: delegation } }));
    }
  }
  events.push(memoryEvent("tc1", "ToolCall", { tool: { name: "book" }, refs }));
  for (const [key, event] of [
    ["parent", opts.parent],
    ["origin", opts.origin],
  ] as const) {
    if (event !== undefined) {
      events.push(event);
      const i1 = (events[1] as Ev).data;
      i1.refs = {
        ...((i1.refs as Record<string, unknown>) ?? {}),
        [key]: `agentce:event/${event.id}`,
      };
    }
  }
  return events;
}

function provenance(
  events: Ev[],
  node = "tc1",
  domain: DomainBinding = rolesDomain(),
): [boolean, unknown, unknown] {
  const store = buildGraph(events, { domain });
  const iri = `agentce:event/${node}`;
  return [
    store.isA(iri, "agentce:ConsequentialAction"),
    store.literalValues(iri, "agentce:provenanceMet")[0] ?? null,
    store.literalValues(iri, "agentce:provenanceJudged")[0] ?? null,
  ];
}

const FAILS = [true, "false", "true"];
const MEETS = [true, "true", "true"];
const UNJUDGED = [true, "true", "false"];

test("cnd04: the population is consequential actions", () => {
  const events = provenanceEvents();
  assert.deepEqual(provenance(events), MEETS);
  assert.deepEqual(provenance(events, "d1"), MEETS);
  // a call executing a minor decision, and the minor decision, are outside CND-04
  const minor = provenanceEvents({ dtype: "agentce:MinorDecision" });
  assert.deepEqual(provenance(minor), OUTSIDE);
  assert.deepEqual(provenance(minor, "d1"), OUTSIDE);
});

test("cnd04: the chain must end at an authorised principal", () => {
  for (const cls of ["user", "operator", "service", "agent_identified"]) {
    assert.deepEqual(provenance(provenanceEvents({ sourceClass: cls })), MEETS);
  }
  const badClasses = [
    "tool_output",
    "retrieved",
    "memory_untrusted",
    "memory_trusted",
    "agent_unidentified",
    null,
  ];
  for (const bad of badClasses) {
    assert.deepEqual(provenance(provenanceEvents({ sourceClass: bad })), FAILS);
  }
  for (const principal of [null, {}, { id: "" }, "user:alice"]) {
    assert.deepEqual(provenance(provenanceEvents({ principal })), FAILS);
  }
  // every instruction on the parent chain counts, not only the acted-on one
  const badParent = memoryEvent("i0", "Instruction", { source_class: "tool_output" });
  assert.deepEqual(provenance(provenanceEvents({ parent: badParent })), FAILS);
  // the role is read at the chain's end, the parent here
  const goodData = { source_class: "operator", principal: { id: "op:1", role: "credit_officer" } };
  const goodParent = memoryEvent("i0", "Instruction", goodData);
  assert.deepEqual(provenance(provenanceEvents({ parent: goodParent })), MEETS);
  // three hops: a user parent whose own parent is tool_output still fails
  const root = memoryEvent("i9", "Instruction", { source_class: "tool_output" });
  const middle = memoryEvent("i0", "Instruction", {
    ...goodData,
    refs: { parent: "agentce:event/i9" },
  });
  const deep = [...provenanceEvents({ parent: middle }), root];
  assert.deepEqual(provenance(deep), FAILS);
  // arrival order does not change the reading
  assert.deepEqual(provenance([...deep].reverse()), FAILS);
  assert.deepEqual(provenance(provenanceEvents({ parent: goodParent }).reverse()), MEETS);
  // a parent cycle never ends at a principal: unjudged
  const cycle = provenanceEvents({ parent: memoryEvent("i0", "Instruction", { ...goodData }) });
  (cycle[cycle.length - 1] as Ev).data.refs = { parent: "agentce:event/i1" };
  assert.deepEqual(provenance(cycle), UNJUDGED);
  assert.deepEqual(
    provenance(provenanceEvents({ principal: { id: "  ", role: "credit_officer" } })),
    FAILS,
  );
});

test("cnd04: the principal's role must be authorised for the task type", () => {
  const events = provenanceEvents();
  assert.deepEqual(provenance(events, "tc1", rolesDomain(["claims_supervisor"])), FAILS);
  // no roles declared, no role on the principal, or no scope for the task: the records cannot show it
  assert.deepEqual(provenance(events, "tc1", rolesDomain(null)), UNJUDGED);
  assert.deepEqual(provenance(provenanceEvents({ principal: { id: "user:alice" } })), UNJUDGED);
  assert.deepEqual(provenance(events, "tc1", GATED_DOMAIN), UNJUDGED);
  // an empty list authorises no role
  assert.deepEqual(provenance(events, "tc1", rolesDomain([])), FAILS);
  // the Decision takes the task of the call executing it
  const tasked = provenanceEvents();
  (tasked[tasked.length - 1] as Ev).agentcetask = "review";
  assert.deepEqual(provenance(tasked, "d1", rolesDomain(["claims_supervisor"])), FAILS);
});

test("cnd04: an instruction that is not held fails", () => {
  for (const ref of [null, "agentce:event/i-missing", "i1", "agentce:event/g1"]) {
    assert.deepEqual(provenance(provenanceEvents({ instruction: ref })), FAILS);
  }
});

test("cnd04: an origin from a tool call or an untrusted read fails", () => {
  const call = memoryEvent("o1", "ToolCall", { tool: { name: "fetch" } });
  assert.deepEqual(provenance(provenanceEvents({ origin: call })), FAILS);
  const read = memoryEvent("o1", "MemoryRead", { trust_min: "untrusted" });
  assert.deepEqual(provenance(provenanceEvents({ origin: read })), FAILS);
  const trusted = memoryEvent("o1", "MemoryRead", { trust_min: "trusted" });
  assert.deepEqual(provenance(provenanceEvents({ origin: trusted })), MEETS);
  for (const trust of [null, "", "unknown", "Untrusted", ["untrusted"]]) {
    const unread = memoryEvent("o1", "MemoryRead", trust === null ? {} : { trust_min: trust });
    assert.deepEqual(provenance(provenanceEvents({ origin: unread })), UNJUDGED);
  }
  const access = memoryEvent("o1", "ResourceAccess", {});
  assert.deepEqual(provenance(provenanceEvents({ origin: access })), FAILS);
  // an origin of a type §6.2.3 does not allow there cannot be judged
  const other = memoryEvent("o1", "SessionStart", {});
  assert.deepEqual(provenance(provenanceEvents({ origin: other })), UNJUDGED);
});

/** `events` with i1 recorded by the agent (self_report) and the enforcement-point witnesses appended. */
function selfReported(events: Ev[], ...witnesses: Ev[]): Ev[] {
  (events[1] as Ev).agentcesourceclass = "self_report";
  return [...events, ...witnesses];
}

function pdAllow(principal: unknown, request = "agentce:event/tc1"): Ev {
  return memoryEvent("pd1", "PolicyDecision", { decision: "allow", principal, refs: { request } });
}

test("cnd04: a self-reported role must match the enforcement-point record", () => {
  const alice = { id: "user:alice", role: "credit_officer" };
  for (const node of ["tc1", "d1"]) {
    // the policy decision allowing the call records the same role: read as recorded
    let events = selfReported(provenanceEvents(), pdAllow(alice));
    assert.deepEqual(provenance(events, node), MEETS);
    // another role for the same principal: the agent's word is contradicted
    events = selfReported(provenanceEvents(), pdAllow({ id: "user:alice", role: "loan_manager" }));
    assert.deepEqual(provenance(events, node), FAILS);
    // no role on the witness: the records cannot show the role
    events = selfReported(provenanceEvents(), pdAllow({ id: "user:alice" }));
    assert.deepEqual(provenance(events, node), UNJUDGED);
  }
  // an enforcement-point twin of the instruction (same content_ref, task and class) recording another role
  const same = { content_ref: "sha256:aa", refs: { task: "task-1" } };
  const twin = memoryEvent("i1-ep", "Instruction", {
    source_class: "user",
    principal: { id: "user:alice", role: "loan_manager" },
    ...same,
  });
  const events = provenanceEvents();
  Object.assign((events[1] as Ev).data, same);
  assert.deepEqual(provenance(selfReported(events, twin)), FAILS);
  // an instruction with no content_ref has no twin
  assert.deepEqual(provenance(selfReported(provenanceEvents(), twin)), MEETS);
  // a witness for another principal, or allowing another request, is no witness of this role
  const mallory = { id: "user:mallory", role: "loan_manager" };
  assert.deepEqual(provenance(selfReported(provenanceEvents(), pdAllow(mallory))), MEETS);
  const elsewhere = pdAllow({ id: "user:alice" }, "agentce:event/tc9");
  assert.deepEqual(provenance(selfReported(provenanceEvents(), elsewhere)), MEETS);
  // an enforcement-point instruction is read as recorded, whatever a policy decision says
  assert.deepEqual(provenance([...provenanceEvents(), pdAllow({ id: "user:alice" })]), MEETS);
});

test("cnd04: a definite failure beats an unjudged role", () => {
  const events = provenanceEvents({ delegation: "unverified" });
  assert.deepEqual(provenance(events, "tc1", rolesDomain(null)), FAILS);
  assert.deepEqual(provenance(events, "tc1", GATED_DOMAIN), FAILS);
});

test("cnd04: reads authorised_principal_roles strictly", () => {
  const events = provenanceEvents();
  // padding is trimmed on both sides; the comparison is case-sensitive
  assert.deepEqual(provenance(events, "tc1", rolesDomain([" credit_officer "])), MEETS);
  const padded = provenanceEvents({ principal: { id: "user:alice", role: " credit_officer" } });
  assert.deepEqual(provenance(padded), MEETS);
  assert.deepEqual(provenance(events, "tc1", rolesDomain(["Credit_Officer"])), FAILS);
  // a bare string, null or a list with a non-string member is unreadable, never a shorter list
  for (const roles of ["credit_officer", null, ["credit_officer", 7], ["loan_manager", null]]) {
    const domain = DomainBinding.fromDict({
      decision_types: [
        {
          id: "agentce:CreditDecision",
          subclass_of: "agentce:ConsequentialDecision",
          consequential: true,
        },
      ],
      task_scopes: [
        {
          task_type: "review",
          allowed_effect_classes: ["read"],
          authorised_principal_roles: roles,
        },
      ],
    });
    assert.deepEqual(provenance(events, "tc1", domain), UNJUDGED, String(roles));
  }
});

test("cnd04: reads the delegation chain", () => {
  assert.deepEqual(provenance(provenanceEvents({ delegation: "unverified" })), FAILS);
  assert.deepEqual(provenance(provenanceEvents({ delegation: "unverified" }), "d1"), FAILS);
  // no delegation, or one the bundle does not hold: the records cannot judge it (fail closed)
  assert.deepEqual(provenance(provenanceEvents({ delegation: null })), UNJUDGED);
  assert.deepEqual(provenance(provenanceEvents({ delegation: "unheld" })), UNJUDGED);
});
