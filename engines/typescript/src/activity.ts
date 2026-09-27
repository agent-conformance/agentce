/**
 * What your agents did (Hill 1): counted facts about a run, built only from the accepted events.
 *
 * `summarizeActivity` is the one place the agents, models, tools, actions by effect class, approvals
 * by who recorded them, actions denied or blocked, and tools/models the records show but the profile
 * never declared are computed; `report.md`, `report.html`, the `--json` envelope, and the terminal all
 * render that one dictionary (the pattern `verdict.summarize` established), so they cannot disagree.
 * Nothing here depends on event order, the clock, or the locale. A faithful port of the Python
 * reference (`agentce/activity.py`); field names, key order, and sort order match exactly so
 * `activity.json` is byte-identical across engines.
 */

import type { Profile } from "./profile";
import { byteCompare } from "./util";

/** Every `ToolCall.effect_class` this view counts, plus the bucket for a call that names none. */
export const EFFECT_CLASSES = [
  "external_communication",
  "irreversible",
  "physical",
  "read",
  "spend",
  "unspecified",
  "write",
] as const;
/** The three evidence-source trust classes (SPEC §6.4) an `ApprovalDecided` event was recorded by. */
export const RECORDER_CLASSES = ["enforcement_point", "independent_system", "self_report"] as const;
/** The four ways SPEC's authority events say no to an action. */
export const DENIED_KINDS = [
  "approval_rejected",
  "authz_denied",
  "policy_denied",
  "refused",
] as const;

type Event = Record<string, unknown>;

export interface ActivityModel {
  provider: string;
  name: string;
  version_or_digest: string;
}

export interface ActivityTool {
  name: string;
  server: string;
  protocol: string;
}

export interface Activity {
  agents: string[];
  models: ActivityModel[];
  tools: ActivityTool[];
  actions_by_effect_class: Record<string, number>;
  approvals_by_recorder: Record<string, number>;
  denied_or_blocked: Record<string, number>;
  undeclared: { models: string[]; tools: string[] };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function eventType(event: Event): string {
  const data = event.data;
  return isRecord(data) && typeof data["@type"] === "string" ? data["@type"] : "";
}

function eventData(event: Event): Record<string, unknown> | null {
  return isRecord(event.data) ? event.data : null;
}

function compareTuple(a: string[], b: string[]): number {
  for (let i = 0; i < a.length; i++) {
    const c = byteCompare(a[i] as string, b[i] as string);
    if (c !== 0) return c;
  }
  return 0;
}

/** Return the counted facts `events` show, compared against what `profile` declares.
 *
 * `undeclared` names every distinct tool and model name the events show that no subject's
 * `declaredTools`/`declaredModels` names -- honestly "not declared yet", never "suspicious": a
 * profile that declares neither leaves every tool and model in that list, which is the correct
 * first-run answer, not a false positive. */
export function summarizeActivity(events: Event[], profile: Profile): Activity {
  const agents = new Set<string>();
  const models = new Map<string, string[]>();
  const tools = new Map<string, string[]>();
  const observedTools = new Set<string>();
  const observedModels = new Set<string>();
  const actionsByEffectClass: Record<string, number> = {};
  for (const c of EFFECT_CLASSES) actionsByEffectClass[c] = 0;
  const approvalsByRecorder: Record<string, number> = {};
  for (const c of RECORDER_CLASSES) approvalsByRecorder[c] = 0;
  const deniedOrBlocked: Record<string, number> = {};
  for (const k of DENIED_KINDS) deniedOrBlocked[k] = 0;

  for (const event of events) {
    const data = eventData(event);
    if (data === null) continue;
    const agent = data.agent;
    if (isRecord(agent) && typeof agent.id === "string") {
      agents.add(agent.id);
    }
    const etype = eventType(event);
    if (etype === "ModelCall") {
      const model = data.model;
      if (isRecord(model) && typeof model.name === "string") {
        const name = model.name;
        observedModels.add(name);
        const key = [String(model.provider ?? ""), name, String(model.version_or_digest ?? "")];
        models.set(key.join("\u0000"), key);
      }
    } else if (etype === "ToolCall") {
      const tool = data.tool;
      if (isRecord(tool) && typeof tool.name === "string") {
        const name = tool.name;
        observedTools.add(name);
        const key = [name, String(tool.server ?? ""), String(tool.protocol ?? "")];
        tools.set(key.join("\u0000"), key);
      }
      const effectClass = data.effect_class;
      const key =
        typeof effectClass === "string" && effectClass in actionsByEffectClass
          ? effectClass
          : "unspecified";
      actionsByEffectClass[key] = (actionsByEffectClass[key] ?? 0) + 1;
    } else if (etype === "ApprovalDecided") {
      const sourceClass = String(event.agentcesourceclass ?? "");
      if (sourceClass in approvalsByRecorder) {
        approvalsByRecorder[sourceClass] = (approvalsByRecorder[sourceClass] ?? 0) + 1;
      }
      if (data.outcome === "reject") {
        deniedOrBlocked.approval_rejected = (deniedOrBlocked.approval_rejected ?? 0) + 1;
      }
    } else if (etype === "PolicyDecision" && data.decision === "deny") {
      deniedOrBlocked.policy_denied = (deniedOrBlocked.policy_denied ?? 0) + 1;
    } else if (etype === "AuthzCheck" && data.allowed === false) {
      deniedOrBlocked.authz_denied = (deniedOrBlocked.authz_denied ?? 0) + 1;
    } else if (etype === "Refusal") {
      deniedOrBlocked.refused = (deniedOrBlocked.refused ?? 0) + 1;
    }
  }

  const declaredTools = new Set<string>();
  const declaredModels = new Set<string>();
  for (const subject of profile.subjects) {
    for (const t of subject.declaredTools) declaredTools.add(t);
    for (const m of subject.declaredModels) declaredModels.add(m);
  }

  return {
    agents: [...agents].sort(byteCompare),
    models: [...models.values()].sort(compareTuple).map(([provider, name, versionOrDigest]) => ({
      provider: provider as string,
      name: name as string,
      version_or_digest: versionOrDigest as string,
    })),
    tools: [...tools.values()].sort(compareTuple).map(([name, server, protocol]) => ({
      name: name as string,
      server: server as string,
      protocol: protocol as string,
    })),
    actions_by_effect_class: actionsByEffectClass,
    approvals_by_recorder: approvalsByRecorder,
    denied_or_blocked: deniedOrBlocked,
    undeclared: {
      models: [...observedModels].filter((m) => !declaredModels.has(m)).sort(byteCompare),
      tools: [...observedTools].filter((t) => !declaredTools.has(t)).sort(byteCompare),
    },
  };
}
