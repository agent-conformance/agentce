/**
 * Checked writes to the paths a command's options name (SPEC §8.7 writes only where asked, §8.5 exit
 * 3), ported from Python's `outputs.py`. Every output a command writes goes through one role: a target
 * that exists as the wrong type, a path that cannot be reached, or a folder that cannot be written is
 * refused under the role's key before anything is written, and a write that fails anyway is refused
 * under the same key. A file is opened non-blocking and checked on the open descriptor, so a FIFO is
 * never waited on (18.180).
 */

import {
  constants,
  accessSync,
  closeSync,
  fstatSync,
  lstatSync,
  mkdirSync,
  openSync,
  statSync,
  writeSync,
} from "node:fs";
import { dirname } from "node:path";
import { InputError } from "./errors";
import { jsonlText, pyRepr } from "./util";

export const NOT_A_FILE = "is not a regular file";
export const NOT_A_DIRECTORY = "is not a directory";
export const DANGLING = "is a link that points nowhere";
export const UNREACHABLE =
  "cannot be reached (a link loop, a folder that cannot be entered, or a name too long)";
export const UNWRITABLE = "cannot be written";

/** One option a command writes to: the key it is refused under, the flag shown, the fix, and
 * whether it names a file or a directory. */
export interface OutputRole {
  readonly key: string;
  readonly flag: string;
  readonly fix: string;
  readonly directory?: boolean;
}

/** The `--out` directory of conformance run. */
export const OUT_DIR: OutputRole = {
  key: "input.out_dir_unwritable",
  flag: "--out",
  fix: "choose a writable --out directory.",
  directory: true,
};

/** A file `writeFile` refused because the open descriptor is not a regular file. */
export class NotRegularFile extends Error {
  readonly code = "ENOTREG";
}

function refuse(role: OutputRole, raw: string, reason: string): InputError {
  return new InputError(role.key, `${role.flag} ${pyRepr(raw)} ${reason}.`, role.fix);
}

function errCode(err: unknown): unknown {
  return (err as { code?: unknown } | null)?.code;
}

function isLink(path: string): boolean {
  try {
    return lstatSync(path).isSymbolicLink();
  } catch {
    return false;
  }
}

function canAccess(path: string, mode: number): boolean {
  try {
    accessSync(path, mode);
    return true;
  } catch {
    return false;
  }
}

/** Python's `Path(raw).parents`, nearest first. */
function parents(path: string): string[] {
  const out: string[] = [];
  let current = path;
  for (;;) {
    const parent = dirname(current);
    if (parent === current) {
      return out;
    }
    out.push(parent);
    current = parent;
  }
}

/** A target that does not exist yet: its nearest existing ancestor must be a folder that can be
 * written and entered (missing folders between them are created). */
function checkCreatable(path: string, role: OutputRole, raw: string): void {
  for (const ancestor of parents(path)) {
    let isDir: boolean;
    try {
      isDir = statSync(ancestor).isDirectory();
    } catch (err) {
      if (errCode(err) === "ENOENT") {
        if (isLink(ancestor)) {
          throw refuse(role, raw, UNREACHABLE);
        }
        continue;
      }
      throw refuse(role, raw, UNREACHABLE);
    }
    if (!isDir) {
      throw refuse(role, raw, UNREACHABLE);
    }
    if (!canAccess(ancestor, constants.W_OK | constants.X_OK)) {
      throw refuse(role, raw, UNWRITABLE);
    }
    return;
  }
}

/** Refuse `raw` under `role.key` unless it can be written: an existing target of the role's type that
 * can be written, or a new one whose nearest existing folder can be. Nothing is opened. */
export function checkOutput(raw: string, role: OutputRole): string {
  let info: ReturnType<typeof statSync>;
  try {
    info = statSync(raw);
  } catch (err) {
    if (errCode(err) === "ENOENT") {
      if (isLink(raw)) {
        throw refuse(role, raw, DANGLING);
      }
      checkCreatable(raw, role, raw);
      return raw;
    }
    throw refuse(role, raw, UNREACHABLE);
  }
  if (role.directory === true) {
    if (!info.isDirectory()) {
      throw refuse(role, raw, NOT_A_DIRECTORY);
    }
    if (!canAccess(raw, constants.W_OK | constants.X_OK)) {
      throw refuse(role, raw, UNWRITABLE);
    }
  } else {
    if (!info.isFile()) {
      throw refuse(role, raw, NOT_A_FILE);
    }
    if (!canAccess(raw, constants.W_OK)) {
      throw refuse(role, raw, UNWRITABLE);
    }
  }
  return raw;
}

/** Write `data` to `path`, opened non-blocking, so a FIFO fails the open instead of waiting for a
 * reader, and checked on the open descriptor before a byte is written. Throws the OS error
 * (`NotRegularFile` for a target of the wrong type); callers key it under their role. */
export function writeFile(
  path: string,
  data: string | Buffer,
  options: { exclusive?: boolean; mode?: number } = {},
): void {
  const payload = typeof data === "string" ? Buffer.from(data, "utf-8") : data;
  const flags =
    constants.O_WRONLY |
    constants.O_CREAT |
    constants.O_NONBLOCK |
    (options.exclusive === true ? constants.O_EXCL : constants.O_TRUNC);
  const fd = openSync(path, flags, options.mode ?? 0o666);
  try {
    if (!fstatSync(fd).isFile()) {
      throw new NotRegularFile(`${path} is not a regular file`);
    }
    let offset = 0;
    while (offset < payload.length) {
      offset += writeSync(fd, payload, offset);
    }
  } finally {
    closeSync(fd);
  }
}

/** Write `records` to `path` as {@link jsonlText} with {@link writeFile} (applicability.jsonl and
 * integrity.jsonl are compared byte for byte by VG-RECORDS-FOLDER-PARITY). */
export function writeJsonl(records: Iterable<unknown>, path: string): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFile(path, jsonlText(records));
}

/** Check `raw` (`checkOutput`), create its missing folders and write `data` to it (`writeFile`). */
export function writeOutput(
  raw: string,
  role: OutputRole,
  data: string | Buffer,
  options: { exclusive?: boolean; mode?: number } = {},
): string {
  checkOutput(raw, role);
  try {
    mkdirSync(dirname(raw), { recursive: true });
    writeFile(raw, data, options);
  } catch (err) {
    throw refuse(role, raw, err instanceof NotRegularFile ? NOT_A_FILE : UNWRITABLE);
  }
  return raw;
}

/** Check the directory `raw` names and refuse any write inside `body` that fails anyway; files
 * inside it are written with `writeFile`, so a FIFO at a fixed name is refused too. */
export function writingInto<T>(raw: string, role: OutputRole, body: (dir: string) => T): T {
  checkOutput(raw, role);
  try {
    return body(raw);
  } catch (err) {
    if (err instanceof InputError || errCode(err) === undefined) {
      throw err;
    }
    throw refuse(role, raw, UNWRITABLE);
  }
}
