/**
 * Errors that name the fix (SPEC §13.4 AX-6): a stable `key`, a one-sentence `cause`, and a `fix`.
 * The CLI renders these deterministically; unless `--debug` is set, no stack trace reaches the user.
 */

import { ExitCode } from "./exitCodes";

export class AgentceError extends Error {
  readonly key: string;
  readonly cause: string;
  readonly fix: string;
  readonly exitCode: number;

  constructor(key: string, cause: string, fix = "", exitCode: number = ExitCode.INPUT_ERROR) {
    super(`${key}: ${cause}`);
    this.key = key;
    this.cause = cause;
    this.fix = fix;
    this.exitCode = exitCode;
    this.name = "AgentceError";
  }

  toDict(): Record<string, string | number> {
    return { key: this.key, cause: this.cause, fix: this.fix, exit_code: this.exitCode };
  }
}

/** A missing, unreadable, or malformed input (exit code 3). */
export class InputError extends AgentceError {
  constructor(key: string, cause: string, fix = "") {
    super(key, cause, fix, ExitCode.INPUT_ERROR);
    this.name = "InputError";
  }
}
