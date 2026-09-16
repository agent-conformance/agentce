/**
 * Deterministic IRIs for graph nodes (SPEC §6.3, IR-12).
 *
 * Events become `agentce:event/<id>`. Principals are pseudonymised: `agentce:principal/<mac>` where
 * `mac` is HMAC-SHA-256 of the principal id under a per-subject key. A run without a key uses the
 * documented all-zero key. Agent ids are used verbatim as node IRIs, and `refs.*` values are CURIEs.
 */

import { createHmac } from "node:crypto";

/** The documented default pseudonymisation key (all zero); a real run supplies its own by reference. */
export const ZERO_KEY = Buffer.alloc(32, 0);

export function eventIri(eventId: string): string {
  return `agentce:event/${eventId}`;
}

export function principalIri(rawId: string, key: Buffer = ZERO_KEY): string {
  const mac = createHmac("sha256", key).update(rawId, "utf-8").digest("hex");
  return `agentce:principal/${mac}`;
}

/** True if `value` is a CURIE that refers to an event node. */
export function isEventRef(value: string): boolean {
  return value.startsWith("agentce:event/");
}

/** The event id from an `agentce:event/<id>` CURIE. */
export function eventIdOf(value: string): string {
  if (!isEventRef(value)) {
    return value;
  }
  return value.slice(value.indexOf("/") + 1);
}
