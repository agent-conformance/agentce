/**
 * The `agentce` command-line interface (SPEC §8.5): the same verbs, `--json` envelope, and
 * exit-code scheme as the Python reference. Commands are dispatched here as the engine's modules
 * land; until a verb is implemented it returns a stable `input_error` envelope rather than a guess.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ExitCode } from "./exitCodes";
import { CommandResult } from "./result";

function version(): string {
  const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf-8"));
  return String(pkg.version);
}

function emit(result: CommandResult, json: boolean): void {
  if (json) {
    console.log(JSON.stringify(result.envelope()));
  } else {
    for (const line of result.humanLines) {
      console.log(line);
    }
  }
}

export function main(argv: string[]): number {
  const command = argv[0];
  if (command === "--version" || command === "-V" || command === "version") {
    console.log(`agentce ${version()}`);
    return 0;
  }

  const json = argv.includes("--json");
  const result = new CommandResult(command ?? "");
  result.addCode(ExitCode.INPUT_ERROR);
  result.data.error = {
    message_key: "cli.not_implemented",
    detail: "this command is not yet implemented in the TypeScript engine",
    command: command ?? null,
  };
  result.note("agentce (TypeScript engine) — command not yet implemented.");
  emit(result, json);
  return result.exitCode;
}

if (require.main === module) {
  process.exit(main(process.argv.slice(2)));
}
