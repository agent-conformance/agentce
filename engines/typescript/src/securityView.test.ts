/**
 * `computeSecurityView`: the security-framed selection of already-computed activity/crosswalk facts
 * (18.16). A faithful port of the Python reference's `tests/test_security_view.py`: same fixtures,
 * same assertions, so both engines are proven against the same cases.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import type { Activity } from "./activity";
import { makeAssertion } from "./assertions";
import { computeSecurityView } from "./securityView";

const WINDOW: [string, string] = ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"];

const EMPTY_ACTIVITY: Activity = {
  agents: [],
  models: [],
  tools: [],
  actions_by_effect_class: {
    external_communication: 0,
    irreversible: 0,
    physical: 0,
    read: 0,
    spend: 0,
    unspecified: 0,
    write: 0,
  },
  approvals_by_recorder: {
    enforcement_point: 0,
    independent_system: 0,
    self_report: 0,
  },
  denied_or_blocked: {
    approval_rejected: 0,
    authz_denied: 0,
    policy_denied: 0,
    refused: 0,
  },
  undeclared: { models: [], tools: [], agents: [] },
};

function assertion(control: string, crosswalk: Array<Record<string, string | boolean>>) {
  return makeAssertion({
    control,
    controlVersion: "2026.09",
    subject: "spiffe://corp/agents/a",
    outcome: "conformant",
    rung: 2,
    mode: "automated",
    window: WINDOW,
    population: [1, 0],
    severity: "high",
    family: control.split("-", 1)[0] as string,
    crosswalk,
  });
}

test("computeSecurityView filters to three frameworks and reuses activity", () => {
  const activity: Activity = {
    ...EMPTY_ACTIVITY,
    tools: [{ name: "shell", server: "local", protocol: "mcp" }],
    actions_by_effect_class: {
      ...EMPTY_ACTIVITY.actions_by_effect_class,
      irreversible: 2,
      read: 5,
    },
  };
  const assertions = [
    assertion("ROB-02", [
      { framework: "mitre-atlas", clause: "AML.T0051", verified: false },
      { framework: "eu-ai-act", clause: "Art. 9", verified: false },
    ]),
    assertion("REC-01", [{ framework: "owasp-acs", clause: "Hook_SubagentStart", verified: true }]),
  ];
  const view = computeSecurityView(activity, assertions);
  assert.equal(view.tool_access, activity.tools);
  assert.equal(view.actions_by_effect_class, activity.actions_by_effect_class);
  assert.deepEqual(view.enforcement_point_evidence, {
    denied_or_blocked: activity.denied_or_blocked,
    approvals_by_recorder: activity.approvals_by_recorder,
  });
  assert.deepEqual(view.drift, { tools: [], models: [] });
  assert.deepEqual(view.standards_citations, [
    { control: "REC-01", framework: "owasp-acs", clause: "Hook_SubagentStart", verified: true },
    { control: "ROB-02", framework: "mitre-atlas", clause: "AML.T0051", verified: false },
  ]);
});

test("computeSecurityView deduplicates and sorts regardless of input order", () => {
  const dup = { framework: "mitre-atlas", clause: "AML.T0101", verified: false };
  const assertionsA = [assertion("INT-01", [dup]), assertion("OVS-03", [dup])];
  const assertionsB = [...assertionsA].reverse();
  const viewA = computeSecurityView(EMPTY_ACTIVITY, assertionsA);
  const viewB = computeSecurityView(EMPTY_ACTIVITY, assertionsB);
  assert.deepEqual(viewA.standards_citations, viewB.standards_citations);
  assert.equal(viewA.standards_citations.length, 2);
});
