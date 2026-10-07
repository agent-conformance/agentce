#!/usr/bin/env bash
# Build gate helper for VG-RECORDS-FOLDER-PARITY (item 18.69): `agentce assess <folder>` gives the
# same answer in every engine. tools/records_folder_parity_check.py builds one tree of record folders
# and profiles (the reference set, plus hostile file names, YAML-quoting tool and model names,
# nesting at 256/257, lone surrogates, NaN, bad encodings, CRLF JSON Lines, compressed files and
# symlinks, every refusal, profiles passed back, and a newcomer's relative forms) and runs each
# scenario as text and with --json through Python (the reference) and each engine named below,
# comparing exit codes, error keys, the records summary and lines, the out folder's file list and the
# bytes of its results; it asserts the number of scenarios it ran. Java joins with `java` (18.69b).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root/engines/typescript" && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null)
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python \
  "$root/tools/records_folder_parity_check.py" typescript
