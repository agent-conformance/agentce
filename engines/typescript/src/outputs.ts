/**
 * Checked writes to the paths a command's options name (SPEC §8.7 writes only where asked, §8.5 exit
 * 3), ported from Python's `outputs.py`. Every output a command writes goes through one role: a target
 * that exists as the wrong type, a path that cannot be reached, or a folder that cannot be written is
 * refused under the role's key before anything is written, and a write that fails anyway is refused
 * under the same key. A file is written to a fresh sibling with a bounded name and moved over its
 * target, as in Python and Java: the target is never opened, so a FIFO is never waited on (18.180), a
 * hard link's other name keeps its bytes, and a dangling link is replaced rather than followed out of
 * the folder (18.184c).
 */

import { randomBytes } from "node:crypto";
import {
  constants,
  accessSync,
  chmodSync,
  closeSync,
  lstatSync,
  mkdirSync,
  openSync,
  realpathSync,
  renameSync,
  statSync,
  unlinkSync,
  writeSync,
} from "node:fs";
import type { Stats } from "node:fs";
import { basename, dirname } from "node:path";
import { InputError } from "./errors";
import { joinPath, jsonlText, pyRepr } from "./util";

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

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

/** `path` has a `..` right after a folder that does not exist (`nosub/../name`). The OS cannot
 * resolve it, so it is refused rather than created, the same in every engine (18.184c). */
export function throughMissingFolder(path: string): boolean {
  return [path, ...parents(path)].some((p) => basename(p) === ".." && !isDirectory(dirname(p)));
}

/** A target that does not exist yet: its nearest existing ancestor must be a folder that can be
 * written and entered (missing folders between them are created), and the path must not climb out
 * of a folder that does not exist ({@link throughMissingFolder}). */
function checkCreatable(path: string, role: OutputRole, raw: string): void {
  if (throughMissingFolder(path)) {
    throw refuse(role, raw, UNREACHABLE);
  }
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

/** The file `raw` names, as Python's `Path(raw)` and Java's `Paths.get(raw)` read it: trailing
 * slashes dropped (18.182). Messages keep `raw` as typed. */
function targetOf(raw: string): string {
  return raw.replace(/(?<=.)\/+$/, "");
}

/** Refuse `raw` under `role.key` unless it can be written: an existing target of the role's type that
 * can be written, or a new one whose nearest existing folder can be. Nothing is opened. Returns the path
 * written to ({@link targetOf}). */
export function checkOutput(raw: string, role: OutputRole): string {
  const path = targetOf(raw);
  let info: ReturnType<typeof statSync>;
  try {
    info = statSync(path);
  } catch (err) {
    if (errCode(err) === "ENOENT") {
      if (isLink(path)) {
        throw refuse(role, raw, DANGLING);
      }
      checkCreatable(path, role, raw);
      return path;
    }
    throw refuse(role, raw, UNREACHABLE);
  }
  if (role.directory === true) {
    if (!info.isDirectory()) {
      throw refuse(role, raw, NOT_A_DIRECTORY);
    }
    if (!canAccess(path, constants.W_OK | constants.X_OK)) {
      throw refuse(role, raw, UNWRITABLE);
    }
  } else {
    if (!info.isFile()) {
      throw refuse(role, raw, NOT_A_FILE);
    }
    if (!canAccess(path, constants.W_OK)) {
      throw refuse(role, raw, UNWRITABLE);
    }
  }
  return path;
}

/** Write `data` to `path` without opening it: an existing target must be a regular file that can be
 * written, and the bytes go to a fresh sibling with a bounded name that is then moved over it, keeping
 * the target's mode. A link to a regular file is followed, as an open would; a dangling link is
 * replaced. `exclusive` creates a new file and never replaces one. Throws the OS error
 * (`NotRegularFile` for a target of the wrong type); callers key it under their role. */
export function writeFile(
  path: string,
  data: string | Buffer,
  options: { exclusive?: boolean; mode?: number } = {},
): void {
  const payload = typeof data === "string" ? Buffer.from(data, "utf-8") : data;
  const create = constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL;
  if (options.exclusive === true) {
    writeFd(openSync(path, create, options.mode ?? 0o666), payload);
    return;
  }
  let target = path;
  let keep: number | undefined;
  let info: Stats | undefined;
  try {
    info = statSync(path);
  } catch (err) {
    if (errCode(err) !== "ENOENT") {
      throw err;
    }
  }
  if (info !== undefined) {
    if (!info.isFile()) {
      throw new NotRegularFile(`${path} is not a regular file`);
    }
    accessSync(path, constants.W_OK);
    target = realpathSync.native(path);
    keep = info.mode & 0o7777;
  }
  const sibling = joinPath(dirname(target), `.agentce-${randomBytes(8).toString("hex")}.tmp`);
  const fd = openSync(sibling, create, options.mode ?? 0o666);
  try {
    writeFd(fd, payload);
    if (keep !== undefined) {
      chmodSync(sibling, keep);
    }
    renameSync(sibling, target);
  } catch (err) {
    try {
      unlinkSync(sibling);
    } catch {
      // already moved or never written
    }
    throw err;
  }
}

/** Write all of `payload` to `fd` and close it. */
function writeFd(fd: number, payload: Buffer): void {
  try {
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
  const path = checkOutput(raw, role);
  try {
    mkdirSync(dirname(path), { recursive: true });
    writeFile(path, data, options);
  } catch (err) {
    throw refuse(role, raw, err instanceof NotRegularFile ? NOT_A_FILE : UNWRITABLE);
  }
  return raw;
}

/** Check the directory `raw` names and refuse any write inside `body` that fails anyway; files
 * inside it are written with `writeFile`, so a FIFO at a fixed name is refused too. */
export function writingInto<T>(raw: string, role: OutputRole, body: (dir: string) => T): T {
  const dir = checkOutput(raw, role);
  try {
    return body(dir);
  } catch (err) {
    if (err instanceof InputError || errCode(err) === undefined) {
      throw err;
    }
    throw refuse(role, raw, UNWRITABLE);
  }
}
