/**
 * The full assess path (graph → catalog → structural → assertions) is byte-identical to the reference.
 *
 * `testdata/assess-golden.json` is the reference engine's `assess_subjects` output over the base
 * catalog for several event sets (a control's passed and failed fixtures, and an empty stream). The
 * comparison also covers evidence pointers, whose digests canonicalise the real events — so any drift
 * in the canonical form, the graph, the shape parse, or the evaluator would break it.
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { type Assertion, assertionToJson, makeAssertion } from "./assertions";
import {
  COVERAGE_FAMILIES,
  applyDeviations,
  assessSubjects,
  evaluatedNothing,
  reachesHumanActorFailure,
  uncovered,
} from "./assess";
import { loadBundle } from "./bundle";
import { canonicalString } from "./canonical";
import { loadCatalog } from "./catalog";
import { DomainBinding } from "./domain";
import { InputError } from "./errors";
import { ingest } from "./ingest";
import { NO_CAPS } from "./integrity";
import { profileFromDict } from "./profile";
import { parseShapesTtl } from "./psp";
import { loadDeviationRegister } from "./readiness";
import { GraphStore } from "./store";
import { propertyReads } from "./structural";

/** Coverage that reconciles every subject (SPEC §6.5), for tests of rules other than unknown coverage. */
const RECONCILED = { subjects: new Proxy({}, { get: () => ({ coverage_status: "ok" }) }) };

const REPO = join(__dirname, "..", "..", "..");
const BASE = join(REPO, "spec", "catalogs", "base", "eu-ai-act");
const TESTDATA = join(__dirname, "..", "testdata");
const SUBJECT = "spiffe://corp/agents/a";

function eventsOf(controlId: string, kase: string): Array<Record<string, unknown>> {
  return readFileSync(join(BASE, "test", controlId, `${kase}.jsonl`), "utf-8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

test("assess_subjects over the base catalog matches the Python reference", () => {
  const golden = JSON.parse(readFileSync(join(TESTDATA, "assess-golden.json"), "utf-8"));
  const catalog = loadCatalog(BASE);
  const domain = DomainBinding.load(join(BASE, "test", "domain.yaml"));
  const profile = profileFromDict({
    subjects: [{ id: SUBJECT, role: "both" }],
    catalogs: ["base/eu-ai-act"],
  });

  const scenarios: Record<string, Array<Record<string, unknown>>> = {
    "ovs-passed": eventsOf("OVS-03", "passed"),
    "ovs-failed": eventsOf("OVS-03", "failed"),
    "rec-passed": eventsOf("REC-04", "passed"),
    empty: [],
  };

  const result: Record<string, unknown> = {};
  for (const [name, events] of Object.entries(scenarios)) {
    result[name] = assessSubjects(
      events,
      profile,
      [catalog],
      domain,
      true,
      [],
      NO_CAPS,
      RECONCILED,
    ).map(assertionToJson);
  }
  assert.equal(canonicalString(result), canonicalString(golden));
});

function deviationAssertion(control: string, outcome: string): Assertion {
  return makeAssertion({
    control,
    controlVersion: "2026.09",
    subject: SUBJECT,
    outcome,
    rung: 2,
    mode: "automated",
    window: ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"],
    population: [1, 1],
    severity: "high",
    family: "AUV",
  });
}

function deviationEntry(control: string, expiry: string): Record<string, unknown> {
  return {
    control,
    rationale: "r",
    compensating_control: "c",
    owner: "user:a@example.com",
    approver: "user:b@example.com",
    granted: "2025-06-01T00:00:00Z",
    expiry,
  };
}

const AS_OF = "2026-02-01T00:00:00Z";

test("applyDeviations applies an unexpired entry: non-conformant becomes partial and names the control", () => {
  const { assertions, expired } = applyDeviations(
    [deviationAssertion("AUV-01", "non-conformant")],
    [deviationEntry("AUV-01", "2026-06-01T00:00:00Z")],
    AS_OF,
  );
  assert.equal(assertions[0]?.outcome, "partial");
  assert.equal(assertions[0]?.deviation, "AUV-01");
  assert.deepEqual(expired, []);
});

test("applyDeviations ignores an expired entry and returns its control as expired", () => {
  const { assertions, expired } = applyDeviations(
    [deviationAssertion("AUV-02", "non-conformant")],
    [deviationEntry("AUV-02", "2025-11-01T00:00:00Z")],
    AS_OF,
  );
  assert.equal(assertions[0]?.outcome, "non-conformant");
  assert.equal(assertions[0]?.deviation, null);
  assert.deepEqual(expired, ["AUV-02"]);
});

test("applyDeviations leaves every outcome other than non-conformant untouched", () => {
  const inputs = ["conformant", "insufficient_evidence", "not_assessed", "not_applicable"].map(
    (o) => deviationAssertion("AUV-01", o),
  );
  const { assertions, expired } = applyDeviations(
    inputs,
    [deviationEntry("AUV-01", "2026-06-01T00:00:00Z")],
    AS_OF,
  );
  assert.deepEqual(assertions, inputs);
  assert.deepEqual(expired, []);
});

test("applyDeviations never mutates its input assertions or register", () => {
  const inputs = [deviationAssertion("AUV-01", "non-conformant")];
  const register = [deviationEntry("AUV-01", "2026-06-01T00:00:00Z")];
  const before = JSON.stringify([inputs, register]);
  const { assertions } = applyDeviations(inputs, register, AS_OF);
  assert.equal(JSON.stringify([inputs, register]), before);
  assert.notEqual(assertions[0], inputs[0]);
});

test("loadDeviationRegister refuses non-UTF-8 bytes instead of decoding them lossily", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-deviations-utf8-"));
  try {
    const path = join(dir, "reg.yaml");
    writeFileSync(
      path,
      Buffer.concat([
        Buffer.from("deviation_register_version: 1\ndeviations:\n  - control: AUV-"),
        Buffer.from([0xff]),
        Buffer.from("\n"),
      ]),
    );
    assert.throws(
      () => loadDeviationRegister(path),
      (error: unknown) =>
        error instanceof InputError &&
        error.key === "input.deviation_invalid" &&
        error.cause.startsWith(`the deviation register at '${path}' is not valid UTF-8`),
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

const QUARANTINE = join(REPO, "verification", "gates", "fixtures", "quarantine_no_pass");
const ROB02 = join(REPO, "verification", "gates", "fixtures", "rob02_untrusted");

/** ROB-02 over a VG-QUARANTINE-NO-PASS case, ingested from a bundle that declares the gate's three sources. */
function quarantineRob02(kase: string): Assertion {
  const cases = JSON.parse(readFileSync(join(QUARANTINE, "cases.json"), "utf-8")).cases;
  const dir = mkdtempSync(join(tmpdir(), "agentce-assess-"));
  try {
    mkdirSync(join(dir, "events"));
    const lines = cases[kase].events
      .map((e: unknown) => (typeof e === "string" ? e : JSON.stringify(e)))
      .join("\n");
    writeFileSync(join(dir, "events", "subject.jsonl"), `${lines}\n`);
    const hash = createHash("sha256").update(`${lines}\n`).digest("hex");
    const sources = ["enforcement_point", "independent_system", "self_report"].map((cls) => ({
      id: `urn:src:${cls}`,
      class: cls,
    }));
    writeFileSync(
      join(dir, "manifest.json"),
      JSON.stringify({
        bundle_format: "1.0",
        files: [{ path: "events/subject.jsonl", sha256: `sha256:${hash}` }],
        sources,
      }),
    );
    const ingested = ingest(loadBundle(dir));
    const profile = profileFromDict({
      subjects: [{ id: "spiffe://corp/agents/rob02-fixture", role: "both" }],
    });
    const catalog = loadCatalog(join(REPO, "spec", "catalogs", "base", "baseline"));
    const domain = DomainBinding.load(join(ROB02, "domain.linkml.yaml"));
    const assertions = assessSubjects(
      ingested.accepted,
      profile,
      [catalog],
      domain,
      true,
      ingested.withheld,
      NO_CAPS,
      RECONCILED,
    );
    return assertions.find((a) => a.control === "ROB-02") as Assertion;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("a late record that reads non-conformant is marked for the exit code (18.149)", () => {
  const late = quarantineRob02("rob02-untrusted-write-quarantined-time-order");
  assert.equal(late.outcome, "insufficient_evidence");
  assert.equal(late.withheldNonConformant, true);
  // Run state for the exit code only: assertions.json keeps its shape.
  assert.equal("withheld_non_conformant" in assertionToJson(late), false);
  const benign = quarantineRob02("rob02-benign-time-order");
  assert.equal(benign.outcome, "conformant");
  assert.notEqual(benign.withheldNonConformant, true);
});

// --- 18.186: the deviation register cannot waive the SPEC §10.4 human-actor rule. ---

const HUMAN_ACTOR = join(REPO, "verification", "gates", "fixtures", "deviations_human_actor");

/** The case's assertions by control for its fixture subject, and its register's entries. */
function humanActorCase(
  name: string,
  drop: string[] = [],
): { byControl: Map<string, Assertion>; register: Record<string, unknown>[] } {
  const kase = join(HUMAN_ACTOR, name);
  const accepted = ingest(loadBundle(join(kase, "bundle"))).accepted.filter(
    (event) => !drop.includes(String(event.id)),
  );
  const catalogs = [
    loadCatalog(BASE),
    loadCatalog(join(REPO, "spec", "catalogs", "overlays", "conduct")),
  ];
  const profile = profileFromDict({
    subjects: [{ id: "spiffe://corp/agents/cnd05-fixture", role: "both" }],
  });
  const domain = DomainBinding.load(join(kase, "domain.linkml.yaml"));
  const assertions = assessSubjects(
    accepted,
    profile,
    catalogs,
    domain,
    true,
    [],
    NO_CAPS,
    RECONCILED,
  );
  return {
    byControl: new Map(assertions.map((a) => [a.control, a])),
    register: loadDeviationRegister(join(kase, "deviations.yaml")),
  };
}

function pick(byControl: Map<string, Assertion>, control: string): Assertion {
  return byControl.get(control) as Assertion;
}

test("not waivable: a failing approval stays non-conformant on the controls that read it", () => {
  const { byControl, register } = humanActorCase("approval");
  const readers = ["CND-02", "OVS-01", "OVS-08", "RSK-02"];
  const chosen = [...readers, "OVS-05"].map((c) => pick(byControl, c));
  const { assertions, expired, notWaivable } = applyDeviations(
    chosen,
    register,
    chosen[0].window[1],
  );
  assert.deepEqual(expired, []);
  assert.deepEqual(notWaivable, readers);
  for (const a of assertions.slice(0, 4)) {
    assert.deepEqual([a.outcome, a.deviation], ["non-conformant", null], a.control);
  }
  // The decision's missing authorization is no §10.4 failure: the register still waives it.
  assert.deepEqual([assertions[4].outcome, assertions[4].deviation], ["partial", "OVS-05"]);
});

test("not waivable: an expired entry on a failing approval is reported not waivable", () => {
  const { byControl, register } = humanActorCase("approval");
  const cnd02 = pick(byControl, "CND-02");
  const entry = { ...register.find((d) => d.control === "CND-02"), expiry: "2020-01-01" };
  const { assertions, expired, notWaivable } = applyDeviations([cnd02], [entry], cnd02.window[1]);
  assert.deepEqual([expired, notWaivable], [[], ["CND-02"]]);
  assert.equal(assertions[0].outcome, "non-conformant");
});

test("not waivable: a decision with no approval at all is still waivable", () => {
  const { byControl, register } = humanActorCase("approval", ["ap1"]);
  const cnd02 = pick(byControl, "CND-02");
  assert.equal(cnd02.outcome, "non-conformant");
  assert.notEqual(cnd02.humanActorFailed, true);
  const { assertions, notWaivable } = applyDeviations([cnd02], register, cnd02.window[1]);
  assert.deepEqual(notWaivable, []);
  assert.deepEqual([assertions[0].outcome, assertions[0].deviation], ["partial", "CND-02"]);
});

test("not waivable: an override by the agent itself stays non-conformant on OVS-07", () => {
  const { byControl, register } = humanActorCase("override");
  const ovs07 = pick(byControl, "OVS-07");
  const { assertions, notWaivable } = applyDeviations([ovs07], register, ovs07.window[1]);
  assert.deepEqual(notWaivable, ["OVS-07"]);
  assert.deepEqual([assertions[0].outcome, assertions[0].deviation], ["non-conformant", null]);
});

test("not waivable: judged per assertion across subjects", () => {
  const { byControl, register } = humanActorCase("approval");
  const failing = pick(byControl, "CND-02");
  const clean = { ...failing, subject: "spiffe://corp/agents/other", humanActorFailed: false };
  const { assertions, notWaivable } = applyDeviations(
    [failing, clean],
    register,
    failing.window[1],
  );
  assert.deepEqual(notWaivable, ["CND-02"]);
  assert.deepEqual(
    assertions.map((a) => a.outcome),
    ["non-conformant", "partial"],
  );
});

test("not waivable: reads match by predicate", () => {
  const store = new GraphStore();
  store.humanActorFailed.add("d1");
  for (const reads of [
    ["agentce:approvedBy"],
    ["agentce:decidedBy", "^agentce:reviewedBy"],
    ["agentce:x", "agentce:interventionByHuman"],
  ]) {
    assert.equal(reachesHumanActorFailure(store, "d1", reads), true);
  }
  assert.equal(reachesHumanActorFailure(store, "d1", ["agentce:approvedByProxy"]), false);
  assert.equal(reachesHumanActorFailure(store, "d1", ["agentce:authorizedBy"]), false);
  assert.equal(reachesHumanActorFailure(store, "d2", ["agentce:approvedBy"]), false);
});

test("not waivable: the shape reaches the failing decision from another focus", () => {
  const store = new GraphStore();
  store.humanActorFailed.add("d1");
  store.addEdge("tc1", "agentce:executes", "d1");
  store.addEdge("tc2", "agentce:executes", "d2");
  store.addEdge("a1", "agentce:approves", "d1");
  // Two hops on one predicate, given once (propertyReads dedupes): the walk repeats it.
  store.addEdge("i3", "agentce:derivedFrom", "i2");
  store.addEdge("i2", "agentce:derivedFrom", "d1");
  const reaches = (focus: string, reads: string[]): boolean =>
    reachesHumanActorFailure(store, focus, reads);
  assert.equal(reaches("tc1", ["agentce:executes", "agentce:approvedBy"]), true);
  assert.equal(reaches("tc1", ["agentce:executes", "agentce:reviewedBy"]), true);
  assert.equal(
    reaches("a1", ["agentce:approves", "^agentce:executes", "agentce:approvedBy"]),
    true,
  );
  assert.equal(reaches("i3", ["agentce:derivedFrom", "agentce:approvedBy"]), true);
  assert.equal(reaches("tc2", ["agentce:executes", "agentce:approvedBy"]), false);
  assert.equal(reaches("tc1", ["agentce:executes", "agentce:authorizedBy"]), false);
});

test("not waivable: property reads include nested shapes", () => {
  const shapes = parseShapesTtl(`@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .
agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;
  sh:property [ sh:path agentce:executes ; sh:node agentce:N ] ;
  sh:property [ sh:path ( agentce:executes [ sh:inversePath agentce:about ] ) ; sh:equals agentce:x ] ;
  sh:property [ sh:path agentce:calls ; sh:qualifiedValueShape agentce:Q ; sh:qualifiedMinCount 1 ] .
agentce:N a sh:NodeShape ; sh:property [ sh:path agentce:approvedBy ; sh:node agentce:S ] .
agentce:Q a sh:NodeShape ; sh:property [ sh:path agentce:reviewedBy ; sh:minCount 1 ] .
`);
  const shape = [...shapes.values()].find((s) => s.iri.endsWith("#S"));
  assert.ok(shape !== undefined);
  assert.deepEqual(
    propertyReads(shape, shapes),
    new Map([
      [
        "agentce:executes",
        [
          "agentce:executes",
          "agentce:approvedBy",
          "^agentce:about",
          "agentce:x",
          "agentce:calls",
          "agentce:reviewedBy",
        ],
      ],
      ["(agentce:executes ^agentce:about)", ["agentce:executes", "^agentce:about", "agentce:x"]],
      ["agentce:calls", ["agentce:calls", "agentce:reviewedBy"]],
    ]),
  );
});

// --- 18.134: unknown coverage reads INT and REC insufficient_evidence (SPEC §6.5). ---

function assessCoverage(
  events: Array<Record<string, unknown>>,
  coverage: Record<string, unknown>,
  profile = profileFromDict({ subjects: [{ id: SUBJECT, role: "both" }] }),
  catalog = loadCatalog(BASE),
): Map<string, Assertion> {
  const assertions = assessSubjects(
    events,
    profile,
    [catalog],
    DomainBinding.load(join(BASE, "test", "domain.yaml")),
    true,
    [],
    NO_CAPS,
    coverage,
  );
  return new Map(assertions.map((a) => [`${a.subject} ${a.control}`, a]));
}

/** coverage.json's structure with the subject's roll-up `status`, or no entry for it when null. */
function coverageStatus(status: string | null): Record<string, unknown> {
  return { subjects: status === null ? {} : { [SUBJECT]: { coverage_status: status } } };
}

for (const family of ["INT", "REC", "OVS"]) {
  for (const outcome of [
    "conformant",
    "partial",
    "not_applicable",
    "non-conformant",
    "insufficient_evidence",
    "not_assessed",
  ]) {
    test(`coverage unknown on ${family} ${outcome} lowers only a flattering INT or REC outcome`, () => {
      const base = pick(
        assessCoverage(eventsOf("OVS-03", "passed"), RECONCILED),
        `${SUBJECT} OVS-03`,
      );
      const before: Assertion = { ...base, family, outcome };
      const after = uncovered(before);
      if (
        COVERAGE_FAMILIES.has(family) &&
        ["conformant", "partial", "not_applicable"].includes(outcome)
      ) {
        assert.equal(after.outcome, "insufficient_evidence");
        assert.equal(after.coverageUnknown, true);
        assert.deepEqual(after.violations, []);
        assert.deepEqual(after.population, [before.population[0], 0]);
        assert.equal(after.sourceClassSatisfied, null);
        assert.equal(after.evidenceStrength, null);
      } else {
        assert.equal(after, before);
      }
    });
  }
}

for (const [status, lowered] of [
  ["ok", false],
  ["gap", false],
  ["unknown", true],
  [null, true],
] as const) {
  test(`coverage status ${status} decides the INT and REC outcomes`, () => {
    const byControl = assessCoverage(eventsOf("OVS-03", "passed"), coverageStatus(status));
    for (const control of ["INT-01", "REC-04"]) {
      const a = pick(byControl, `${SUBJECT} ${control}`);
      assert.equal(a.outcome, lowered ? "insufficient_evidence" : "conformant");
      assert.equal(a.coverageUnknown === true, lowered);
    }
    const ovs = pick(byControl, `${SUBJECT} OVS-03`);
    assert.equal(ovs.outcome, "conformant");
    assert.ok(!ovs.coverageUnknown);
  });
}

test("coverage unknown keeps a violation non-conformant", () => {
  const a = pick(
    assessCoverage(eventsOf("INT-01", "failed"), coverageStatus("unknown")),
    `${SUBJECT} INT-01`,
  );
  assert.equal(a.outcome, "non-conformant");
  assert.ok(a.violations.length > 0);
  assert.ok(!a.coverageUnknown);
});

test("coverage unknown keeps a role mismatch not_applicable", () => {
  const catalog = loadCatalog(BASE);
  catalog.controls = catalog.controls.map((c) =>
    c.id === "INT-01" ? { ...c, appliesToRoles: ["deployer"] } : c,
  );
  const byControl = assessCoverage(
    eventsOf("OVS-03", "passed"),
    coverageStatus("unknown"),
    profileFromDict({ subjects: [{ id: SUBJECT, role: "provider" }] }),
    catalog,
  );
  const int01 = pick(byControl, `${SUBJECT} INT-01`);
  assert.equal(int01.outcome, "not_applicable");
  assert.ok(!int01.coverageUnknown);
  assert.equal(pick(byControl, `${SUBJECT} REC-04`).outcome, "insufficient_evidence");
});

test("coverage: two subjects each read on their own coverage", () => {
  const other = "spiffe://corp/agents/b";
  const events = eventsOf("OVS-03", "passed");
  const all = [...events, ...events.map((e) => ({ ...e, id: `${e.id}-b`, subject: other }))];
  const byControl = assessCoverage(
    all,
    { subjects: { [SUBJECT]: { coverage_status: "ok" }, [other]: { coverage_status: "unknown" } } },
    profileFromDict({
      subjects: [
        { id: SUBJECT, role: "both" },
        { id: other, role: "both" },
      ],
    }),
  );
  assert.equal(pick(byControl, `${SUBJECT} INT-01`).outcome, "conformant");
  assert.equal(pick(byControl, `${other} INT-01`).outcome, "insufficient_evidence");
});

test("a coverage-lowered check with no population judged nothing", () => {
  const base = pick(assessCoverage(eventsOf("OVS-03", "passed"), RECONCILED), `${SUBJECT} INC-02`);
  const empty = uncovered({ ...base, family: "INT", population: [0, 0] });
  assert.equal(empty.outcome, "insufficient_evidence");
  assert.ok(evaluatedNothing([empty]));
  const judged = uncovered({ ...base, family: "INT", outcome: "conformant", population: [2, 0] });
  assert.ok(!evaluatedNothing([judged]));
});
