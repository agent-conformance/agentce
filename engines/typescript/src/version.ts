/**
 * Engine identity constants (SPEC §9.5): the implementation name, spec version, and package version
 * the reports and manifests record. The name is distinct per engine (`agentce-ts`); the spec version
 * and package version match the Python reference so shared artifacts (OSCAL, canonical assertions)
 * stay byte-identical across engines.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

export const ENGINE_NAME = "agentce-ts";
export const SPEC_VERSION = "0.6";

export function engineVersion(): string {
  const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf-8"));
  return String(pkg.version);
}
