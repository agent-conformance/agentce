/**
 * The domain ontology binding (SPEC §6.5): the enterprise's decision and context classes.
 *
 * The binding subclasses `agentce:Decision` and `agentce:ContextItem` for the domain and declares
 * which decision types are consequential and the oversight modality each requires; for the Conduct
 * overlay it declares each task type's effect scope and budgets (SPEC §7.7.6 `task_scopes`). A run with no
 * binding is valid (an empty binding).
 */

import { pyStrip } from "./failOn";
import { isPyyamlTimestamp, loadPyyaml, pythonizeTimestamp } from "./readiness";

export const DECISION_ROOT = "agentce:Decision";
export const CONTEXT_ROOT = "agentce:ContextItem";

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    !isPyyamlTimestamp(value)
  );
}

/** Python's `str(value)` for a binding value used as a name: a YAML date renders as `str(date)` does. */
function pyStrValue(value: unknown): string {
  return isPyyamlTimestamp(value) ? pythonizeTimestamp(value.pyyamlTimestamp) : String(value);
}

/** The string members of a declared list; anything else declares none. */
function strings(value: unknown): Set<string> {
  return new Set(
    Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [],
  );
}

/** A scope's authorised_principal_roles, trimmed; null when absent or unreadable (not a list of strings), so a
 * bare string, null or a non-string element never reads as a shorter list (CND-04, inbox row 358). */
function roles(value: unknown): Set<string> | null {
  if (!Array.isArray(value) || !value.every((v) => typeof v === "string")) {
    return null;
  }
  return new Set(value.map((v: string) => pyStrip(v)));
}

/** The resource budgets a task scope declares (SPEC §7.7.6 `budgets`), in the order CND-07 reads them. */
export const BUDGET_DIMENSIONS = ["tokens", "compute_seconds", "spend"] as const;

/** A declared budget: the dimension and its limit; a null limit is unreadable. */
export type Budget = readonly [dimension: string, limit: number | null];

/** A non-negative whole number (`5`, or `5.0` as another engine's reader may give it), else null. */
export function wholeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** The largest count every engine holds exactly (2^53 - 1); a token limit above it is unreadable, and a
 * token usage or sum above it saturates to `TOKEN_CAP`, which exceeds every readable limit. */
export const MAX_COUNT = Number.MAX_SAFE_INTEGER;
export const TOKEN_CAP = MAX_COUNT + 1;

/** A token usage as CND-07 sums it: a whole number, saturated at `TOKEN_CAP`; else null. */
export function tokenCount(value: unknown): number | null {
  const count = wholeNumber(value);
  return count === null ? null : Math.min(count, TOKEN_CAP);
}

/** A declared budget limit: a finite non-negative number, a whole one for tokens; anything else (Infinity
 * and NaN included) is null, a limit no usage can be shown to stay within. */
function limit(dimension: string, value: unknown): number | null {
  if (dimension === "tokens") {
    const tokens = wholeNumber(value);
    return tokens !== null && tokens <= MAX_COUNT ? tokens : null;
  }
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** The budgets a scope declares, as (dimension, limit) pairs; a dimension left out declares no limit.
 * A `budgets` value that is not a mapping declares every dimension unreadable. */
function budgets(value: unknown): Budget[] {
  if (value === undefined || value === null) {
    return [];
  }
  if (!isRecord(value)) {
    return BUDGET_DIMENSIONS.map((d) => [d, null] as const);
  }
  return BUDGET_DIMENSIONS.filter((d) => d in value).map((d) => [d, limit(d, value[d])] as const);
}

/**
 * One task type's declared effect scope (SPEC §7.7.6): the tools and the effect classes an action
 * of the task may use, as CND-01 reads them, and the resource budgets CND-07 reads.
 * `requiresApproval` marks classes for CND-02 and allows none by itself.
 */
export class TaskScope {
  constructor(
    /** null when the scope lists no tools (any tool); otherwise the tools it allows. */
    readonly allowedTools: Set<string> | null,
    /** the effect classes it allows; none when the scope lists none. */
    readonly allowedEffectClasses: Set<string>,
    /** the effect classes that need a human's approval (CND-02's marker; allows none for CND-01). */
    readonly requiresApproval: Set<string> = new Set(),
    /** the declared budgets, in BUDGET_DIMENSIONS order. */
    readonly budgets: readonly Budget[] = [],
    /** the principal roles CND-04 accepts at the end of an instruction chain; null when the scope declares none
     * or declares them unreadably. */
    readonly authorisedPrincipalRoles: Set<string> | null = null,
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
  /** whether a task_scopes entry was unreadable (not a mapping, or no string task_type). */
  taskScopesMalformed = false;

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
      const id = pyStrValue(entry.id);
      binding.subclasses.set(
        id,
        pyStrValue("subclass_of" in entry ? entry.subclass_of : DECISION_ROOT),
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
          pyStrValue(entry.id),
          pyStrValue("subclass_of" in entry ? entry.subclass_of : CONTEXT_ROOT),
        );
      }
    }
    const scopes = Array.isArray(data.task_scopes) ? data.task_scopes : [];
    for (const entry of scopes) {
      if (!isRecord(entry) || typeof entry.task_type !== "string") {
        binding.taskScopesMalformed = true;
        continue;
      }
      const tools = entry.allowed_tools;
      const list = binding.taskScopes.get(entry.task_type) ?? [];
      list.push(
        new TaskScope(
          tools === undefined || tools === null ? null : strings(tools),
          strings(entry.allowed_effect_classes),
          strings(entry.requires_approval),
          budgets(entry.budgets),
          roles(entry.authorised_principal_roles),
        ),
      );
      binding.taskScopes.set(entry.task_type, list);
    }
    return binding;
  }

  /**
   * The scopes a task's actions are held to: those declared for the task type the task names,
   * else, when the binding declares a single task type and no unreadable entry, that type's (every
   * task is of it), else none, so nothing the task does can be shown within a declared scope. An
   * unreadable entry may name another type, so it never lets the remaining one stand for all.
   */
  scopesFor(task: string): TaskScope[] {
    const named = this.taskScopes.get(task);
    if (named !== undefined) {
      return named;
    }
    if (this.taskScopes.size === 1 && !this.taskScopesMalformed) {
      return this.taskScopes.values().next().value as TaskScope[];
    }
    return [];
  }

  /** Reads the binding as Python's PyYAML `SafeLoader` types it, refusing a key written twice and any other YAML
   * it cannot load with `input.domain_binding_invalid`. */
  static load(path: string): DomainBinding {
    const data = loadPyyaml(path, "input.domain_binding_invalid", "the domain binding", true);
    if (!isRecord(data)) {
      return DomainBinding.empty();
    }
    return DomainBinding.fromDict(data);
  }
}
