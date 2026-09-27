/**
 * `summarizeActivity`: the counted facts a run's records show (18.4, Hill 1).
 *
 * A faithful port of the Python reference's `tests/test_activity.py`: same event shapes, same
 * assertions, so both engines are proven against the same cases.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { summarizeActivity } from "./activity";
import { type Profile, profileFromDict } from "./profile";

const SUBJECT = "spiffe://corp/agents/a";

function event(eventType: string, data: Record<string, unknown> = {}, sourceClass = "self_report") {
  return {
    id: `e-${eventType}`,
    subject: SUBJECT,
    agentcesourceclass: sourceClass,
    data: { "@type": eventType, agent: { id: SUBJECT }, ...data },
  };
}

function emptyProfile(): Profile {
  return profileFromDict({});
}

function profileWith(declaredTools?: string[]): Profile {
  return profileFromDict({
    catalogs: ["eu-ai-act@2026.09"],
    subjects: [
      {
        id: SUBJECT,
        role: "both",
        ...(declaredTools !== undefined ? { declared_tools: declaredTools } : {}),
      },
    ],
  });
}

test("empty run reports all zero counts", () => {
  const activity = summarizeActivity([], emptyProfile());
  assert.deepEqual(activity.agents, []);
  assert.deepEqual(activity.models, []);
  assert.deepEqual(activity.tools, []);
  assert.equal(
    Object.values(activity.actions_by_effect_class).every((n) => n === 0),
    true,
  );
  assert.equal(
    Object.values(activity.approvals_by_recorder).every((n) => n === 0),
    true,
  );
  assert.equal(
    Object.values(activity.denied_or_blocked).every((n) => n === 0),
    true,
  );
  assert.deepEqual(activity.undeclared, { models: [], tools: [] });
});

test("counts agents, models, and tools", () => {
  const events = [
    event("ModelCall", { model: { provider: "openai", name: "gpt-x", version_or_digest: "1" } }),
    event("ToolCall", {
      tool: { name: "search", server: "mcp://s", protocol: "mcp" },
      effect_class: "read",
    }),
    event("ToolCall", {
      tool: { name: "transfer_funds", server: "mcp://s", protocol: "mcp" },
      effect_class: "irreversible",
    }),
    // A ToolCall naming no effect_class falls into the "unspecified" bucket.
    event("ToolCall", { tool: { name: "search", server: "mcp://s", protocol: "mcp" } }),
  ];
  const activity = summarizeActivity(events, emptyProfile());
  assert.deepEqual(activity.agents, [SUBJECT]);
  assert.deepEqual(
    activity.models.map((m) => m.name),
    ["gpt-x"],
  );
  assert.deepEqual(activity.tools.map((t) => t.name).sort(), ["search", "transfer_funds"]);
  assert.equal(activity.actions_by_effect_class.read, 1);
  assert.equal(activity.actions_by_effect_class.irreversible, 1);
  assert.equal(activity.actions_by_effect_class.unspecified, 1);
  assert.equal(activity.actions_by_effect_class.write, 0);
});

test("approvals are counted by who recorded them", () => {
  const events = [
    event("ApprovalDecided", { outcome: "approve" }, "self_report"),
    event("ApprovalDecided", { outcome: "approve" }, "independent_system"),
    event("ApprovalDecided", { outcome: "reject" }, "independent_system"),
  ];
  const activity = summarizeActivity(events, emptyProfile());
  assert.deepEqual(activity.approvals_by_recorder, {
    enforcement_point: 0,
    independent_system: 2,
    self_report: 1,
  });
  assert.equal(activity.denied_or_blocked.approval_rejected, 1);
});

test("denied or blocked covers the four authority signals", () => {
  const events = [
    event("PolicyDecision", { decision: "deny" }),
    event("PolicyDecision", { decision: "allow" }),
    event("AuthzCheck", { allowed: false }),
    event("AuthzCheck", { allowed: true }),
    event("Refusal", { reason_class: "policy" }),
  ];
  const activity = summarizeActivity(events, emptyProfile());
  assert.deepEqual(activity.denied_or_blocked, {
    approval_rejected: 0,
    authz_denied: 1,
    policy_denied: 1,
    refused: 1,
  });
});

test("undeclared tool and model are honest, not yet declared", () => {
  const events = [
    event("ToolCall", { tool: { name: "search", server: "s", protocol: "mcp" } }),
    event("ModelCall", { model: { provider: "openai", name: "gpt-x", version_or_digest: "1" } }),
  ];
  const activity = summarizeActivity(events, emptyProfile());
  assert.deepEqual(activity.undeclared, { models: ["gpt-x"], tools: ["search"] });
});

test("declaring a tool removes it from undeclared", () => {
  const events = [
    event("ToolCall", { tool: { name: "search", server: "s", protocol: "mcp" } }),
    event("ToolCall", { tool: { name: "transfer_funds", server: "s", protocol: "mcp" } }),
  ];
  const activity = summarizeActivity(events, profileWith(["search"]));
  assert.deepEqual(activity.undeclared.tools, ["transfer_funds"]);
});

test("declaring every tool leaves nothing undeclared", () => {
  const events = [event("ToolCall", { tool: { name: "search", server: "s", protocol: "mcp" } })];
  const activity = summarizeActivity(events, profileWith(["search"]));
  assert.deepEqual(activity.undeclared.tools, []);
});

test("non-dict data and unrelated event types are ignored", () => {
  const events = [{ id: "e1", subject: SUBJECT, data: "not-a-dict" }, event("SessionStart")];
  const activity = summarizeActivity(events, emptyProfile());
  assert.deepEqual(activity.agents, [SUBJECT]);
  assert.deepEqual(activity.tools, []);
  assert.deepEqual(activity.models, []);
});
