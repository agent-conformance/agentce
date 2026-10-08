#!/usr/bin/env bash
# Build gate helper for VG-CLI-OPTIONS (item 18.105, MAINTAINER-INBOX row 153): every command line in
# fixtures/cli_options/cases.json gives its expected exit code and refusal key in the real Python,
# TypeScript and Java CLIs, and a refused run writes nothing under --out. Builds the TypeScript dist and
# the Java jar first so a stale build cannot pass, skipping a build whose inputs hash the same as at its
# last build here (18.107: the seeded-fault demo runs this once per fault, and a fault in one engine
# leaves the other's build untouched).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"

# A content hash of every tracked or new file under the given paths (sources, build files, lockfiles),
# never a modification time: a seeded fault or its restore changes the hash, so the build runs.
inputs_hash() {
  local list
  list="$(mktemp)"
  (cd "$root" && git ls-files --cached --others --exclude-standard -- "$@" | sort -u |
    while IFS= read -r f; do if [ -f "$f" ]; then printf '%s\n' "$f"; fi; done >"$list"
    git hash-object --stdin-paths <"$list" | paste - "$list" | git hash-object --stdin)
  rm -f "$list"
}

# build <stamp file> <command> <input paths...>: runs the command unless the stamp holds the inputs'
# hash, then records the hash. A failed build leaves no stamp.
build() {
  local stamp="$1" cmd="$2" hash
  shift 2
  hash="$(inputs_hash "$@")"
  if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$hash" ]; then
    return 0
  fi
  rm -f "$stamp"
  (cd "$root" && eval "$cmd")
  printf '%s\n' "$hash" >"$stamp"
}

# The two builds are independent; Gradle is waited on by PID so set -e still sees a failure.
build "$root/engines/java/build/libs/.cli-options-inputs" \
  "cd engines/java && ./gradlew --no-daemon --quiet :runnableJar" engines/java & gradle_pid=$!
build "$root/engines/typescript/dist/.cli-options-inputs" \
  "cd engines/typescript && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null" \
  engines/typescript pnpm-lock.yaml pnpm-workspace.yaml
wait "$gradle_pid"
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/verification/gates/cli_options_check.py"
