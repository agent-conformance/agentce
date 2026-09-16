/**
 * Access to the evidence JSON Schema generated from the LinkML model (SPEC §6, item 0.3).
 *
 * The engine vendors a copy of `spec/model/generated/json-schema/agentce-evidence.schema.json` under
 * `schema/`. Events are validated in two steps, mirroring the model: the CloudEvents envelope against
 * `EvidenceEvent` with `data` emptied (its range is the generic `Payload`), and the JSON-LD payload
 * against the specific `<@type>Payload` definition. The `EventType` enum is read from the same schema.
 * The draft is 2019-09.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import Ajv2019, { type ValidateFunction } from "ajv/dist/2019";

let schemaCache: Record<string, unknown> | null = null;
// `strict: false` and `logger: false` mirror the Python validator: unknown formats (e.g. date-time)
// are annotations, not assertions, and must not print warnings that would corrupt `--json` stdout.
const ajv = new Ajv2019({ allErrors: false, strict: false, logger: false });
const validators = new Map<string, ValidateFunction>();

function evidenceSchema(): Record<string, unknown> {
  if (schemaCache === null) {
    const text = readFileSync(
      join(__dirname, "..", "schema", "agentce-evidence.schema.json"),
      "utf-8",
    );
    schemaCache = JSON.parse(text);
  }
  return schemaCache as Record<string, unknown>;
}

function defs(): Record<string, unknown> {
  return evidenceSchema().$defs as Record<string, unknown>;
}

/** The set of valid `EventType` names (SPEC §6.2.3). */
export function eventTypes(): Set<string> {
  const eventType = defs().EventType as { enum: unknown[] };
  return new Set(eventType.enum.map((name) => String(name)));
}

function validatorForDef(ref: string): ValidateFunction {
  let validate = validators.get(ref);
  if (validate === undefined) {
    validate = ajv.compile({ $defs: defs(), $ref: `#/$defs/${ref}` });
    validators.set(ref, validate);
  }
  return validate;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function messagesOf(validate: ValidateFunction): string[] {
  return (validate.errors ?? []).map(
    (err) => `${err.instancePath || "/"} ${err.message ?? "invalid"}`,
  );
}

/** Schema-validation error messages for `event` (empty when it is valid). */
export function validateEvent(event: unknown): string[] {
  if (!isRecord(event)) {
    return ["event is not a JSON object"];
  }
  const errors: string[] = [];
  const envelope = validatorForDef("EvidenceEvent");
  if (!envelope({ ...event, data: {} })) {
    errors.push(...messagesOf(envelope));
  }

  const data = event.data;
  if (!isRecord(data)) {
    errors.push("data is missing or not a JSON object");
    return errors;
  }
  const payloadType = data["@type"];
  if (typeof payloadType !== "string") {
    errors.push("data.@type is missing or not a string");
    return errors;
  }
  const definition = `${payloadType}Payload`;
  if (!(definition in defs())) {
    errors.push(`unknown payload type '${payloadType}'`);
    return errors;
  }
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    if (key !== "@context" && key !== "@type") {
      payload[key] = value;
    }
  }
  const payloadValidate = validatorForDef(definition);
  if (!payloadValidate(payload)) {
    errors.push(...messagesOf(payloadValidate));
  }
  return errors;
}
