/**
 * The domain ontology binding (SPEC §6.5): the enterprise's decision and context classes.
 *
 * The binding subclasses `agentce:Decision` and `agentce:ContextItem` for the domain and declares
 * which decision types are consequential and the oversight modality each requires; for the Conduct
 * overlay it declares each task type's effect scope (SPEC §7.7.6 `task_scopes`). A run with no
 * binding is valid (an empty binding).
 */

import { readFileSync } from "node:fs";
import { load } from "js-yaml";

export const DECISION_ROOT = "agentce:Decision";
export const CONTEXT_ROOT = "agentce:ContextItem";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The string members of a declared list; anything else declares none. */
function strings(value: unknown): Set<string> {
  return new Set(
    Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [],
  );
}

/**
 * One task type's declared effect scope (SPEC §7.7.6), as CND-01 reads it: the tools and the effect
 * classes an action of the task may use. `requiresApproval` marks classes for CND-02 and allows
 * none by itself.
 */
export class TaskScope {
  constructor(
    /** null when the scope lists no tools (any tool); otherwise the tools it allows. */
    readonly allowedTools: Set<string> | null,
    /** the effect classes it allows; none when the scope lists none. */
    readonly allowedEffectClasses: Set<string>,
    /** the effect classes that need a human's approval (CND-02's marker; allows none for CND-01). */
    readonly requiresApproval: Set<string> = new Set(),
  ) {}

  allows(tool: unknown, effectClass: unknown): boolean {
    if (this.allowedTools !== null && !(typeof tool === "string" && this.allowedTools.has(tool))) {
      return false;
    }
    return typeof effectClass === "string" && this.allowedEffectClasses.has(effectClass);
  }
}

export class DomainBinding {
  /** child class -> parent class (domain subclasses of Decision / ContextItem). */
  readonly subclasses = new Map<string, string>();
  /** decision-type IRIs declared consequential. */
  readonly consequential = new Set<string>();
  /** decision-type IRI -> the oversight modality it requires. */
  readonly requiredOversight = new Map<string, string>();
  /** task type -> every scope declared for it, in order (an action must stay within all of them). */
  readonly taskScopes = new Map<string, TaskScope[]>();

  static empty(): DomainBinding {
    return new DomainBinding();
  }

  static fromDict(data: Record<string, unknown>): DomainBinding {
    const binding = new DomainBinding();
    const decisionTypes = Array.isArray(data.decision_types) ? data.decision_types : [];
    for (const entry of decisionTypes) {
      if (!isRecord(entry) || !("id" in entry)) {
        continue;
      }
      const id = String(entry.id);
      binding.subclasses.set(
        id,
        String("subclass_of" in entry ? entry.subclass_of : DECISION_ROOT),
      );
      if (entry.consequential) {
        binding.consequential.add(id);
      }
      const modality = entry.required_oversight_modality;
      if (typeof modality === "string") {
        binding.requiredOversight.set(id, modality);
      }
    }
    const contextClasses = Array.isArray(data.context_classes) ? data.context_classes : [];
    for (const entry of contextClasses) {
      if (isRecord(entry) && "id" in entry) {
        binding.subclasses.set(
          String(entry.id),
          String("subclass_of" in entry ? entry.subclass_of : CONTEXT_ROOT),
        );
      }
    }
    const scopes = Array.isArray(data.task_scopes) ? data.task_scopes : [];
    for (const entry of scopes) {
      if (!isRecord(entry) || typeof entry.task_type !== "string") {
        continue;
      }
      const tools = entry.allowed_tools;
      const list = binding.taskScopes.get(entry.task_type) ?? [];
      list.push(
        new TaskScope(
          tools === undefined || tools === null ? null : strings(tools),
          strings(entry.allowed_effect_classes),
          strings(entry.requires_approval),
        ),
      );
      binding.taskScopes.set(entry.task_type, list);
    }
    return binding;
  }

  /**
   * The scopes a task's actions are held to: those declared for the task type the task names,
   * else, when the binding declares a single task type, that type's (every task is of it), else
   * none, so nothing the task does can be shown within a declared scope.
   */
  scopesFor(task: string): TaskScope[] {
    const named = this.taskScopes.get(task);
    if (named !== undefined) {
      return named;
    }
    if (this.taskScopes.size === 1) {
      return [...this.taskScopes.values()][0];
    }
    return [];
  }

  static load(path: string): DomainBinding {
    const data = load(readFileSync(path, "utf-8"));
    if (!isRecord(data)) {
      return DomainBinding.empty();
    }
    return DomainBinding.fromDict(data);
  }
}
