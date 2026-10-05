/** Shared test-only helpers for exercising unwritable-directory error paths. */

import { constants, accessSync, chmodSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Creates a fresh temp directory, chmods it to 0o555, and runs `body` against it -- unless this
 * process can still write to a mode-555 directory (e.g. running as root), in which case there is
 * nothing to prove and `body` is skipped. Always restores permissions and removes the temp
 * directory afterward, even on failure.
 */
export function withUnwritableDir(prefix: string, body: (dir: string) => void): void {
  const work = mkdtempSync(join(tmpdir(), prefix));
  const dir = join(work, "ro");
  mkdirSync(dir, { mode: 0o555 });
  try {
    accessSync(dir, constants.W_OK);
    return; // this user can write to a mode-555 directory; nothing to prove here
  } catch {
    // expected: not writable, proceed
  }
  try {
    body(dir);
  } finally {
    chmodSync(dir, 0o755);
    rmSync(work, { recursive: true, force: true });
  }
}
