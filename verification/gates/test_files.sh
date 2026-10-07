#!/usr/bin/env bash
# Shared by gates that name unit test files (18.80): run each named TypeScript test file or Java test
# class and fail when it is missing or runs no tests, which neither runner does by itself. Source it
# after setting `root` to the repository root.

: "${root:?test_files.sh needs root set to the repository root}"

# One node --test run per file: node treats file arguments as globs and skips a missing one, so each
# file is checked to exist and its TAP summary to count at least one test.
ts_tests() {
  local file out count
  for file in "$@"; do
    if [ ! -f "$root/engines/typescript/$file" ]; then
      echo "typescript test file $file is missing" >&2
      return 1
    fi
    if ! out="$(cd "$root/engines/typescript" && node --import tsx --test --test-reporter=tap "$file" 2>&1)"; then
      printf '%s\n' "$out" | tail -40 >&2
      return 1
    fi
    count="$(printf '%s\n' "$out" | sed -n 's/^# tests \([0-9][0-9]*\)$/\1/p' | tail -1)"
    if [ "${count:-0}" -lt 1 ]; then
      echo "typescript test file $file ran no tests" >&2
      return 1
    fi
  done
}

# Gradle passes a --tests filter while any one pattern matches, so each class's own XML report must be
# written by this run and count at least one test.
java_tests() {
  local results="$root/engines/java/build/test-results/test" class filters=() report
  for class in "$@"; do
    rm -f "$results/TEST-$class.xml"
    filters+=(--tests "$class")
  done
  if ! (cd "$root/engines/java" && ./gradlew --no-daemon --quiet test "${filters[@]}"); then
    return 1
  fi
  for class in "$@"; do
    report="$results/TEST-$class.xml"
    if [ ! -f "$report" ] || ! grep -Eq '<testsuite [^>]*tests="[1-9][0-9]*"' "$report"; then
      echo "java test class $class did not run any tests" >&2
      return 1
    fi
  done
}
