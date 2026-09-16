/**
 * The domain ontology binding (SPEC §6.5): the enterprise's decision and context classes.
 *
 * The binding subclasses `agentce:Decision` and `agentce:ContextItem` for the domain and declares
 * which decision types are consequential and the oversight modality each requires. A run with no
 * binding is valid (an empty binding).
 */

import { readFileSync } from "node:fs";
import { load } from "js-yaml";

export const DECISION_ROOT = "agentce:Decision";
export const CONTEXT_ROOT = "agentce:ContextItem";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export class DomainBinding {
  /** child class -> parent class (domain subclasses of Decision / ContextItem). */
  readonly subclasses = new Map<string, string>();
  /** decision-type IRIs declared consequential. */
  readonly consequential = new Set<string>();
  /** decision-type IRI -> the oversight modality it requires. */
  readonly requiredOversight = new Map<string, string>();

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
    return binding;
  }

  static load(path: string): DomainBinding {
    const data = load(readFileSync(path, "utf-8"));
    if (!isRecord(data)) {
      return DomainBinding.empty();
    }
    return DomainBinding.fromDict(data);
  }
}
