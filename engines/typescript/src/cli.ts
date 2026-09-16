/**
 * The `agentce` command-line interface (SPEC §8.5): the same verbs, `--json` envelope, and exit-code
 * scheme as the Python reference. Commands are dispatched here as the engine's modules land; until a
 * verb is implemented it returns a stable `input_error` envelope rather than a guess.
 */

import { statSync } from "node:fs";
import { runEcs } from "./conformance";
import { AgentceError, InputError } from "./errors";
import { ExitCode } from "./exitCodes";
import { CommandResult } from "./result";
import { sortKeysDeep } from "./util";
import { engineVersion } from "./version";

function emit(result: CommandResult, json: boolean): void {
  if (json) {
    // json.dumps(envelope, sort_keys=True, indent=2), matching the Python reference
    console.log(JSON.stringify(sortKeysDeep(result.envelope()), null, 2));
  } else {
    for (const line of result.humanLines) {
      console.log(line);
    }
  }
}

/** The value following `--name` in argv, or undefined. */
function flagValue(argv: string[], name: string): string | undefined {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && index + 1 < argv.length ? argv[index + 1] : undefined;
}

function requireDir(raw: string | undefined, key: string, what: string): string {
  const fix = `pass --${key} <dir>.`;
  if (raw === undefined) {
    throw new InputError(`input.${key}_missing`, `${what} is required.`, fix);
  }
  let isDir = false;
  try {
    isDir = statSync(raw).isDirectory();
  } catch {
    isDir = false;
  }
  if (!isDir) {
    throw new InputError(
      `input.${key}_not_a_directory`,
      `${what} '${raw}' is not an existing directory.`,
      fix,
    );
  }
  return raw;
}

function cmdConformance(argv: string[]): CommandResult {
  const result = new CommandResult("conformance");
  const action = argv[1];
  if (action !== "run") {
    throw new InputError(
      "input.conformance_action",
      "the only conformance action is `run`.",
      "run `agentce conformance run ...`.",
    );
  }
  const engine = requireDir(flagValue(argv, "engine"), "engine", "the engine path");
  const corpus = requireDir(flagValue(argv, "corpus"), "corpus", "the corpus directory");
  const out = flagValue(argv, "out") ?? null;
  const report = runEcs({ enginePath: engine, corpusDir: corpus, outDir: out });

  result.data.action = "run";
  result.data.engine = engine;
  result.data.corpus = corpus;
  if (out !== null) {
    result.data.out = out;
  }
  Object.assign(result.data, report); // report.engine (impl/version) overwrites the engine path, as in the reference
  result.note(
    `ECS: ${report.projects.identical}/${report.projects.total} identical; ` +
      `claim ${report.claim}; no_ml ${report.no_ml}`,
  );
  if (report.claim !== "full") {
    result.addCode(ExitCode.FINDINGS);
  }
  return result;
}

function notImplemented(command: string): CommandResult {
  const result = new CommandResult(command);
  result.addCode(ExitCode.INPUT_ERROR);
  result.data.error = {
    message_key: "cli.not_implemented",
    detail: "this command is not yet implemented in the TypeScript engine",
    command: command || null,
  };
  result.note("agentce (TypeScript engine) — command not yet implemented.");
  return result;
}

function errorResult(command: string, error: AgentceError): CommandResult {
  const result = new CommandResult(command);
  result.addCode(error.exitCode);
  result.data.error = { message_key: error.key, detail: error.cause, fix: error.fix };
  result.note(`${error.key}: ${error.cause}`);
  if (error.fix) {
    result.note(`fix: ${error.fix}`);
  }
  return result;
}

export function main(argv: string[]): number {
  const command = argv[0];
  if (command === "--version" || command === "-V" || command === "version") {
    console.log(`agentce ${engineVersion()}`);
    return 0;
  }

  const json = argv.includes("--json");
  let result: CommandResult;
  try {
    if (command === "conformance") {
      result = cmdConformance(argv);
    } else {
      result = notImplemented(command ?? "");
    }
  } catch (exc) {
    if (exc instanceof AgentceError) {
      result = errorResult(command ?? "", exc);
    } else {
      throw exc;
    }
  }
  emit(result, json);
  return result.exitCode;
}

if (require.main === module) {
  process.exit(main(process.argv.slice(2)));
}
