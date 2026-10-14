/**
 * A control's min_source_class, enforced per assertion (SPEC §6.4, HR-5, §7.7.5; item 18.127). Mirrors
 * the Python reference's `test_source_class.py`: the judged class of a focus, the attestation of an
 * instruction it acts on (each of conditions (i)-(iv) and both corroboration branches, with hostile twins
 * that differ from an attested one by one field), the refusal of an unknown minimum at catalog load, and
 * the downgrade, which only ever turns a conformant verdict into insufficient_evidence.
 */

import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { type Assertion, assertionToJson } from "./assertions";
import { assessSubjects, missingEvidence } from "./assess";
import { computeBlindSpots } from "./blindSpots";
import { type Catalog, loadCatalog } from "./catalog";
import { DomainBinding } from "./domain";
import { InputError } from "./errors";
import { buildGraph } from "./graph";
import { eventIri } from "./iri";
import { profileFromDict } from "./profile";
import {
  BELOW_MINIMUM_OUTCOME,
  SOURCE_CLASS_RANK,
  SourceClassJudge,
  classRank,
} from "./sourceClass";

type Event = Record<string, unknown>;

const REPO = join(__dirname, "..", "..", "..");
const CATALOGS = join(REPO, "spec", "catalogs");
const SUBJECT = "spiffe://corp/agents/source-class";
const OTHER = "spiffe://corp/agents/other";
const EP = "enforcement_point";
const SR = "self_report";
const IS = "independent_system";
const ALICE = { id: "user:alice", kind: "human" };
const MALLORY = { id: "user:mallory", kind: "human" };
const CONTENT = `sha256:${"a".repeat(64)}`;
const DOMAIN = DomainBinding.fromDict({
  decision_types: [
    { id: "dom:CreditDecision", subclass_of: "agentce:ConsequentialDecision", consequential: true },
  ],
});

function event(
  eid: string,
  ptype: string,
  cls: string,
  data: Record<string, unknown> = {},
  subject = SUBJECT,
): Event {
  const kept = Object.fromEntries(
    Object.entries(data).filter(([, v]) => v !== null && v !== undefined),
  );
  return {
    id: eid,
    source: `urn:src:${cls}`,
    subject,
    type: `org.agent-conformance.evidence.${ptype}.v1`,
    agentcesourceclass: cls,
    time: `2026-01-02T00:00:${String(eid.length).padStart(2, "0")}Z`,
    specversion: "1.0",
    data: { "@type": ptype, ...kept },
  };
}

interface InstructionOpts {
  eid?: string;
  cls?: string;
  sourceClass?: string | null;
  principal?: Record<string, string> | null;
  content?: string;
  subject?: string;
  refs?: Record<string, string>;
}

function instruction(opts: InstructionOpts = {}): Event {
  return event(
    opts.eid ?? "i1",
    "Instruction",
    opts.cls ?? EP,
    {
      source_class: opts.sourceClass === undefined ? "user" : opts.sourceClass,
      principal: opts.principal === undefined ? ALICE : opts.principal,
      content_ref: opts.content ?? CONTENT,
      refs: { task: "task-1", ...(opts.refs ?? {}) },
    },
    opts.subject ?? SUBJECT,
  );
}

function call(cls = EP, refs: Record<string, string> = {}): Event {
  return event("tc1", "ToolCall", cls, {
    agent: { id: SUBJECT },
    tool: { name: "lookup", protocol: "native" },
    effect_class: "read",
    refs: { instruction: eventIri("i1"), ...refs },
  });
}

function policy(
  opts: {
    cls?: string;
    decision?: string;
    principal?: Record<string, string> | null;
    request?: string;
  } = {},
): Event {
  return event("pd1", "PolicyDecision", opts.cls ?? EP, {
    engine: "custom",
    policy_id: "ingress",
    policy_version: "1",
    decision: opts.decision ?? "allow",
    principal: opts.principal === undefined ? ALICE : opts.principal,
    refs: { request: eventIri(opts.request ?? "tc1") },
  });
}

function read(trustMin: string | null = "trusted", cls = EP): Event {
  return event("m1", "MemoryRead", cls, {
    store: "kb",
    record_refs: ["mem:r1"],
    trust_min: trustMin,
  });
}

function dataOf(e: Event): Record<string, unknown> {
  return e.data as Record<string, unknown>;
}

function judge(events: Event[]): SourceClassJudge {
  const own = events.filter((e) => e.subject === SUBJECT);
  const store = buildGraph(own, { domain: DOMAIN });
  return new SourceClassJudge(store, new Map(own.map((e) => [eventIri(String(e.id)), e])));
}

function attested(events: Event[], focus = "tc1"): boolean {
  return judge(events).attested(eventIri(focus), eventIri("i1"));
}

// --- the class order and judgedClass ----------------------------------------------------------------

test("the class order is the SPEC order and anything else ranks lowest", () => {
  assert.deepEqual(
    [...SOURCE_CLASS_RANK.keys()].sort(
      (a, b) => (SOURCE_CLASS_RANK.get(a) ?? 0) - (SOURCE_CLASS_RANK.get(b) ?? 0),
    ),
    [SR, EP, IS],
  );
  for (const odd of ["any", "Enforcement_Point", "", null, 1]) {
    assert.equal(classRank(odd), 0);
  }
});

test("the judged class is the lowest of the focus and what it acts on", () => {
  const focus = eventIri("tc1");
  assert.deepEqual(judge([instruction({ cls: SR }), call()]).judgedClass(focus, ["actsOn"]), {
    rank: 0,
    lowest: ["Instruction"],
  });
  assert.equal(judge([instruction(), call()]).judgedClass(focus, ["actsOn"]).rank, 1);
  assert.deepEqual(judge([instruction(), call(SR)]).judgedClass(focus, ["actsOn"]), {
    rank: 0,
    lowest: ["ToolCall"],
  });
  // Without source_class_from only the focus's own class counts.
  assert.equal(judge([instruction({ cls: SR }), call()]).judgedClass(focus, []).rank, 1);
});

test("a decision counts only what it rests on", () => {
  const decision = event("d1", "Decision", IS, {
    decision_type: "dom:CreditDecision",
    refs: { instruction: eventIri("i1") },
  });
  // Its own independent_system class is not counted.
  assert.equal(
    judge([instruction({ cls: SR }), decision]).judgedClass(eventIri("d1"), ["actsOn"]).rank,
    0,
  );
  assert.equal(judge([instruction(), decision]).judgedClass(eventIri("d1"), ["actsOn"]).rank, 1);
});

test("a decision that reaches nothing is judged through its calls or as self_report", () => {
  const lone = event("d2", "Decision", SR, { decision_type: "dom:CreditDecision" });
  assert.deepEqual(judge([lone]).judgedClass(eventIri("d2"), ["actsOn"]), {
    rank: 0,
    lowest: ["Decision"],
  });
  const executes = call(EP, { decision: eventIri("d2") });
  assert.equal(
    judge([instruction(), lone, executes]).judgedClass(eventIri("d2"), ["actsOn"]).rank,
    1,
  );
  assert.equal(
    judge([instruction({ cls: SR }), lone, executes]).judgedClass(eventIri("d2"), ["actsOn"]).rank,
    0,
  );
  const bare = event("tc1", "ToolCall", EP, {
    agent: { id: SUBJECT },
    tool: { name: "lookup", protocol: "native" },
    effect_class: "read",
    refs: { decision: eventIri("d2") },
  });
  assert.deepEqual(judge([lone, bare]).judgedClass(eventIri("d2"), ["actsOn"]), {
    rank: 0,
    lowest: ["Decision"],
  });
});

test("an unattested independent_system instruction counts as self_report", () => {
  assert.equal(
    judge([instruction({ cls: IS }), call()]).judgedClass(eventIri("tc1"), ["actsOn"]).rank,
    0,
  );
});

// --- attestation (i)-(iv) -----------------------------------------------------------------------------

test("(i) an instruction is attested only when held by its graph IRI", () => {
  assert.ok(attested([instruction(), call()]));
  const bare = call();
  (dataOf(bare).refs as Record<string, string>).instruction = "i1";
  assert.ok(!judge([instruction(), bare]).attested(eventIri("tc1"), "i1"));
  assert.ok(!attested([instruction({ subject: OTHER }), call()]));
});

test("(ii) every parent must be held and attested", () => {
  const parent = instruction({ eid: "i0", cls: EP, content: `sha256:${"0".repeat(64)}` });
  const child = instruction({ refs: { parent: eventIri("i0") } });
  assert.ok(attested([parent, child, call()]));
  const srParent = instruction({ eid: "i0", cls: SR, content: `sha256:${"0".repeat(64)}` });
  assert.ok(!attested([srParent, child, call()]));
  assert.ok(!attested([child, call()])); // the parent is not held
  const cycle = instruction({ eid: "i0", cls: EP, refs: { parent: eventIri("i1") } });
  assert.ok(attested([cycle, child, call()]));
});

test("(iii) an origin must be an enforcement-point read at trust_min trusted", () => {
  const cases: Array<[string, Event, boolean]> = [
    ["ep-trusted", read("trusted"), true],
    ["ep-untrusted", read("untrusted"), false],
    ["ep-no-trust-min", read(null), false],
    ["self-report-read", read("trusted", SR), false],
  ];
  for (const [name, origin, expected] of cases) {
    const inst = instruction({ sourceClass: "memory_trusted", refs: { origin: eventIri("m1") } });
    assert.equal(attested([origin, inst, call()]), expected, name);
  }
  const gone = instruction({ sourceClass: "memory_trusted", refs: { origin: eventIri("gone") } });
  assert.ok(!attested([gone, call()]));
});

test("(iv) a self_report instruction is not attested on its own", () => {
  assert.ok(!attested([instruction({ cls: SR }), call()]));
});

function twin(over: Record<string, unknown> = {}): Event {
  const t = instruction({ eid: "i1-ingress", cls: EP });
  t.time = "2026-01-02T00:00:00Z"; // at ingress, before the call
  const data = dataOf(t);
  for (const [key, value] of Object.entries(over)) {
    if (key === "task" || key === "parent" || key === "origin") {
      (data.refs as Record<string, unknown>)[key] = value;
    } else if (key === "subject") {
      t.subject = value;
    } else if (key === "cls") {
      t.agentcesourceclass = value;
    } else {
      data[key] = value;
    }
  }
  return t;
}

test("(iv) an attested enforcement-point twin witnesses the class", () => {
  assert.ok(attested([instruction({ cls: SR }), twin(), call()]));
  const nobody = instruction({ cls: SR, principal: null, sourceClass: "memory_trusted" });
  const t = twin({ source_class: "memory_trusted" });
  const { principal: _dropped, ...unowned } = dataOf(t);
  t.data = unowned;
  assert.ok(attested([nobody, t, call()]));
});

test("(iv) an enforcement-point policy decision for the focus witnesses the class", () => {
  assert.ok(attested([instruction({ cls: SR }), call(), policy()]));
});

test("hostile corroboration never lifts a self_report instruction", () => {
  const hostile: Array<[string, Event[]]> = [
    ["twin-other-content", [twin({ content_ref: `sha256:${"b".repeat(64)}` })]],
    ["twin-other-task", [twin({ task: "task-2" })]],
    ["twin-other-subject", [twin({ subject: OTHER })]],
    ["twin-other-authorised-class", [twin({ source_class: "operator" })]],
    ["twin-other-principal", [twin({ principal: MALLORY })]],
    [
      "twin-self-report-parent",
      [twin({ parent: eventIri("i0") }), instruction({ eid: "i0", cls: SR, content: "x" })],
    ],
    [
      "twin-tool_output-parent",
      [
        twin({ parent: eventIri("i0") }),
        instruction({ eid: "i0", cls: EP, sourceClass: "tool_output", content: "x" }),
      ],
    ],
    ["twin-untrusted-origin", [twin({ origin: eventIri("m1") }), read("untrusted")]],
    ["pd-other-request", [policy({ request: "tc9" })]],
    ["pd-self-report", [policy({ cls: SR })]],
    ["pd-independent-system", [policy({ cls: IS })]],
    ["pd-deny", [policy({ decision: "deny" })]],
    ["pd-other-principal", [policy({ principal: MALLORY })]],
    ["pd-no-principal", [policy({ principal: null })]],
    ["twin-self-report", [twin({ cls: SR })]],
  ];
  for (const [name, extra] of hostile) {
    assert.ok(!attested([instruction({ cls: SR }), call(), ...extra]), name);
  }
});

test("a policy decision does not lift a self_report parent", () => {
  const parent = instruction({ eid: "i0", cls: SR, content: `sha256:${"0".repeat(64)}` });
  const child = instruction({ cls: SR, refs: { parent: eventIri("i0") } });
  assert.ok(!attested([parent, child, call(), policy()]));
  // Nor does it lift a self-report parent below an enforcement-point child.
  const epChild = instruction({ cls: EP, refs: { parent: eventIri("i0") } });
  assert.ok(!attested([parent, epChild, call(), policy()]));
});

test("a corroborated instruction still needs its origin", () => {
  const inst = instruction({ cls: SR, refs: { origin: eventIri("m1") } });
  assert.ok(!attested([read("untrusted"), inst, call(), policy()]));
  assert.ok(attested([read("trusted"), inst, call(), policy()]));
});

// --- the refusal of an unknown minimum ----------------------------------------------------------------

test("an unknown minimum is refused at catalog load", () => {
  const cases: Array<[string, string | null, string]> = [
    ["independent", "min_source_class: independent", '"independent"'],
    ["missing", null, "(missing)"],
    ["null", "min_source_class: null", "null"],
    ["empty", 'min_source_class: ""', '""'],
    ["number", "min_source_class: 1", "1"],
    ["case", "min_source_class: Enforcement_Point", '"Enforcement_Point"'],
    ["any", "min_source_class: any", '"any"'],
  ];
  const root = mkdtempSync(join(tmpdir(), "agentce-min-class-"));
  try {
    for (const [name, line, shown] of cases) {
      const dir = join(root, name);
      cpSync(join(CATALOGS, "overlays", "conduct"), dir, { recursive: true });
      const path = join(dir, "controls", "CND-05.yaml");
      const text = readFileSync(path, "utf-8");
      const replacement = line === null ? "" : `  ${line}\n`;
      writeFileSync(path, text.replace("  min_source_class: enforcement_point\n", replacement));
      assert.throws(
        () => loadCatalog(dir),
        (err: unknown) =>
          err instanceof InputError &&
          err.key === "catalog.control.min_source_class_unknown" &&
          err.exitCode === 3 &&
          err.cause.includes("CND-05") &&
          err.cause.includes(`min_source_class ${shown},`),
        name,
      );
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("an unknown source_class_from is refused at load", () => {
  const cases: Array<[string, string, string]> = [
    ["case", "source_class_from: [actson]", '["actson"]'],
    ["string", "source_class_from: actsOn", '"actsOn"'],
    ["unknown", "source_class_from: [parent]", '["parent"]'],
    ["null", "source_class_from: null", "null"],
    ["number", "source_class_from: [actsOn, 1]", '["actsOn", 1]'],
  ];
  const root = mkdtempSync(join(tmpdir(), "agentce-class-from-"));
  try {
    for (const [name, line, shown] of cases) {
      const dir = join(root, name);
      cpSync(join(CATALOGS, "overlays", "conduct"), dir, { recursive: true });
      const path = join(dir, "controls", "CND-05.yaml");
      const text = readFileSync(path, "utf-8");
      writeFileSync(path, text.replace("  source_class_from: [actsOn]\n", `  ${line}\n`));
      assert.throws(
        () => loadCatalog(dir),
        (err: unknown) =>
          err instanceof InputError &&
          err.key === "catalog.control.source_class_from_unknown" &&
          err.exitCode === 3 &&
          err.cause.includes("CND-05") &&
          err.cause.includes(`source_class_from ${shown},`),
        name,
      );
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("every bundled and spec catalog declares a known minimum", () => {
  const dirs: string[] = [];
  for (const top of [CATALOGS, join(__dirname, "..", "data", "catalogs")]) {
    for (const group of readdirSync(top, { withFileTypes: true })) {
      if (!group.isDirectory()) continue;
      for (const name of readdirSync(join(top, group.name), { withFileTypes: true })) {
        if (name.isDirectory()) dirs.push(join(top, group.name, name.name));
      }
    }
  }
  assert.ok(dirs.length > 0);
  for (const dir of dirs) {
    try {
      readFileSync(join(dir, "catalog.yaml"));
    } catch {
      continue;
    }
    for (const control of loadCatalog(dir).controls) {
      assert.ok(SOURCE_CLASS_RANK.has(control.minSourceClass), `${dir} ${control.id}`);
    }
  }
});

// --- the downgrade, through assess --------------------------------------------------------------------

function conduct(): Catalog {
  return loadCatalog(join(CATALOGS, "overlays", "conduct"));
}

function assess(events: Event[], control = "CND-05"): Assertion {
  const profile = profileFromDict({
    subjects: [
      { id: SUBJECT, role: "both" },
      { id: OTHER, role: "both" },
    ],
  });
  const assertions = assessSubjects(events, profile, [conduct()], DOMAIN, true, []);
  const found = assertions.find((a) => a.control === control && a.subject === SUBJECT);
  assert.ok(found);
  return found;
}

test("a self_report instruction reads insufficient_evidence naming what is missing", () => {
  const events = [instruction({ cls: SR }), call()];
  const assertion = assess(events);
  assert.equal(assertion.outcome, BELOW_MINIMUM_OUTCOME);
  assert.equal(BELOW_MINIMUM_OUTCOME, "insufficient_evidence");
  assert.equal(assertion.sourceClassSatisfied, false);
  assert.deepEqual(
    assertion.evidence.map((e) => e.ref),
    [eventIri("tc1")],
  );
  assert.deepEqual(assertion.classGap, ["Instruction"]);
  const json = assertionToJson(assertion);
  assert.equal(json.source_class_satisfied, false);
  assert.ok(!("violations" in json) && !("evidence_strength" in json));
  const control = conduct().controls.find((c) => c.id === "CND-05");
  assert.ok(control);
  assert.deepEqual(missingEvidence(control, events, assertion), [
    { event: "Instruction", class: "enforcement_point" },
  ]);
});

test("an attested instruction stays conformant", () => {
  for (const events of [
    [instruction(), call()],
    [instruction({ cls: SR }), twin(), call()],
    [instruction({ cls: SR }), call(), policy()],
  ]) {
    const assertion = assess(events);
    assert.equal(assertion.outcome, "conformant");
    assert.equal(assertion.sourceClassSatisfied, true);
  }
});

test("the other subject's twin never lifts this subject's instruction", () => {
  assert.equal(
    assess([instruction({ cls: SR }), twin({ subject: OTHER }), call()]).outcome,
    "insufficient_evidence",
  );
});

test("the downgrade never touches a non-conformant verdict", () => {
  const assertion = assess([instruction({ cls: SR, sourceClass: "tool_output" }), call()]);
  assert.equal(assertion.outcome, "non-conformant");
  assert.equal(assertion.sourceClassSatisfied, true);
  assert.deepEqual(assertion.classGap ?? [], []);
});

test("a decision acting on nothing is not held to a class", () => {
  const decision = event("d1", "Decision", SR, { decision_type: "dom:CreditDecision" });
  const c = call(EP, { decision: eventIri("d1") });
  assert.equal(assess([instruction(), c, decision]).outcome, "conformant");
});

test("CND-04 reads the delegation it rests on", () => {
  const decision = event("d1", "Decision", SR, { decision_type: "dom:CreditDecision" });
  const delegation = event("dl1", "DelegationIssued", SR, {
    token_ref: "jti-1",
    issuer: "https://idp.example",
    chain: [
      { id: "user:alice", kind: "human" },
      { id: SUBJECT, kind: "agent" },
    ],
    verification: { status: "verified", method: "rfc8693" },
  });
  let c = call(EP, { decision: eventIri("d1") });
  assert.equal(assess([instruction(), c, decision], "CND-04").outcome, "conformant");
  c = call(EP, { decision: eventIri("d1"), delegation: eventIri("dl1") });
  // A delegation the records do not hold counts as self_report.
  assert.equal(assess([instruction(), c, decision], "CND-04").outcome, "insufficient_evidence");
  const downgraded = assess([instruction(), c, decision, delegation], "CND-04");
  assert.equal(downgraded.outcome, "insufficient_evidence");
  assert.deepEqual(downgraded.classGap, ["DelegationIssued"]);
});

test("a blind spot names the record below the minimum", () => {
  const profile = profileFromDict({ subjects: [{ id: SUBJECT, role: "both" }] });
  const events = [instruction({ cls: SR }), call()];
  const catalogs = [conduct()];
  const assertions = assessSubjects(events, profile, catalogs, DOMAIN, true, []);
  const spots = computeBlindSpots(assertions, profile, catalogs, events);
  const spot = spots.blind_spots.find((s) => s.event === "Instruction");
  assert.ok(spot);
  assert.equal(spot.class, "enforcement_point");
  assert.equal(spot.checks_unlocked, 0);
  assert.ok(spot.needed_by >= 1);
});

// --- ROB-02 -------------------------------------------------------------------------------------------

function rob02(events: Event[]): Assertion {
  const profile = profileFromDict({ subjects: [{ id: SUBJECT, role: "both" }] });
  const catalog = loadCatalog(join(CATALOGS, "base", "baseline"));
  const found = assessSubjects(events, profile, [catalog], DOMAIN, true, []).find(
    (a) => a.control === "ROB-02",
  );
  assert.ok(found);
  return found;
}

function memory(): Event[] {
  return [
    event("w0", "MemoryWrite", EP, {
      store: "kb",
      record_ref: "mem:r0",
      trust: "trusted",
      guard_verdict: "allow",
    }),
    event("g0", "MemoryRead", EP, { store: "kb", record_refs: ["mem:r0"], trust_min: "trusted" }),
  ];
}

test("ROB-02: an instruction without a source class taints the decision", () => {
  const decision = event("d1", "Decision", SR, {
    decision_type: "dom:CreditDecision",
    inputs: ["mem:r0"],
    refs: { instruction: eventIri("i1") },
  });
  assert.equal(
    rob02([...memory(), instruction({ sourceClass: "user" }), decision]).outcome,
    "conformant",
  );
  assert.equal(
    rob02([...memory(), instruction({ sourceClass: null }), decision]).outcome,
    "non-conformant",
  );
});

test("ROB-02: a decision that used nothing recorded reads insufficient_evidence", () => {
  const used = event("d1", "Decision", SR, {
    decision_type: "dom:CreditDecision",
    inputs: ["mem:r0"],
  });
  assert.equal(rob02([...memory(), used]).outcome, "conformant");
  const nothing = event("d1", "Decision", SR, { decision_type: "dom:CreditDecision" });
  assert.equal(rob02([...memory(), nothing]).outcome, "insufficient_evidence");
});
