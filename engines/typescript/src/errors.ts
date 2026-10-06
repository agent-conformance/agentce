/**
 * Errors that name the fix (SPEC §13.4 AX-6): a stable `key`, a one-sentence `cause`, and a `fix`.
 * The CLI renders these deterministically; unless `--debug` is set, no stack trace reaches the user.
 */

import { isAbsolute, relative, sep } from "node:path";
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

/** True for a permission error (Node's EACCES/EPERM): the one reason an input is unreadable rather
 * than missing or malformed (18.68). */
export function isPermissionError(err: unknown): boolean {
  const code = (err as { code?: unknown } | null)?.code;
  return code === "EACCES" || code === "EPERM";
}

/** The path a filesystem error names, relative to `root` ("" for `root` itself or no path), as
 * Python's `unreadable_rel` (18.68). */
export function unreadableRel(root: string, err: unknown): string {
  const path = (err as { path?: unknown } | null)?.path;
  if (typeof path !== "string") {
    return "";
  }
  const rel = relative(root, path).split(sep).join("/");
  return rel.startsWith("..") || isAbsolute(rel) ? "" : rel;
}

/** A target, or a file or folder inside it, that cannot be read (Python's `UnreadableError`). */
export function unreadableError(
  key: string,
  what: string,
  target: string,
  rel: string,
  noun: string,
): InputError {
  return new InputError(
    key,
    rel
      ? `${what} ${target} holds a file or folder that cannot be read: ${rel}.`
      : `${what} ${target} cannot be read.`,
    `make every file and folder in the ${noun} readable, then re-run.`,
  );
}
