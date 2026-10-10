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

test("ROB-02: ruled on when the guard ruled on every input, or there were none", () => {
  const write = memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1", trust: "trusted" });
  assert.deepEqual(ruled([write, withInputs(decisionEvent("d1"), ["mem:r1"])]), ["true"]);
  assert.deepEqual(ruled([decisionEvent("d1")]), ["true"]);
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
  assert.deepEqual(ruled([decisionEvent("d1"), other]), ["true"]);
});

test("ROB-02: ruled on is independent of event order", () => {
  const write = selfReport(memoryEvent("w1", "MemoryWrite", { record_ref: "mem:r1" }));
  const events = [write, withInputs(decisionEvent("d1"), ["mem:r1"])];
  assert.deepEqual(ruled(events), ["false"]);
  assert.deepEqual(ruled([...events].reverse()), ["false"]);
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
  const approval = (id: string, actor: unknown) => refEvent(id, "ApprovalDecided", { actor }, "d1");
  const dual = {
    ...decisionEvent("d1"),
    data: { ...decisionEvent("d1").data, oversight_modality: "dual_control" },
  };
  const cover = (events: Record<string, unknown>[], domain = MINOR_DOMAIN) =>
    lit(events, "d1", "agentce:oversightCoverageComplete", domain);
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
    lit([refEvent("x1", ptype, { actor })], "x1", "agentce:interventionByHuman");
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
  const risk = (events: Record<string, unknown>[]) => lit(events, "d1", "agentce:riskReviewed");
  const review = refEvent("a1", "ApprovalDecided", { actor: HUMAN_A }, "d1");
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
