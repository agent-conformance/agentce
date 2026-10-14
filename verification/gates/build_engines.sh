#!/usr/bin/env bash
# Sourced by gates that run the built TypeScript and Java CLIs (cli_options.sh, trust_matrix.sh): builds the
# TypeScript dist and the Java jar so a stale build cannot pass, skipping a build whose inputs hash the same as at its
# last build here (18.107: a seeded-fault demo runs a gate once per fault, and a fault in one engine leaves the
# other's build untouched). Expects $root set to the repository root.

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
build_engines() {
  local gradle_pid
  build "$root/engines/java/build/libs/.engine-build-inputs" \
    "cd engines/java && ./gradlew --no-daemon --quiet :runnableJar" engines/java & gradle_pid=$!
  build "$root/engines/typescript/dist/.engine-build-inputs" \
    "cd engines/typescript && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null" \
    engines/typescript pnpm-lock.yaml pnpm-workspace.yaml
  wait "$gradle_pid"
}
