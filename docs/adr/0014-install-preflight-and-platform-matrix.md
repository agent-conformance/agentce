# 0014 — Install preflight and a platform matrix for the reference engine

Status: accepted
Spec refs: §13.4 AX-1, AX-5, AX-8

## Context

The reference engine signs and verifies with Ed25519 through `cryptography`, pinned at `>=50,<51`
because every earlier release carries high-severity advisories that the dependency-review gate
refuses. `cryptography` publishes prebuilt wheels for Linux, Windows x86_64, and Apple-silicon macOS
and none for Intel macOS, so the one documented command fails on a clean Intel Mac unless Rust and
OpenSSL 3 are present, and nothing said so: Getting Started named no prerequisite, `agentce doctor`
inspected only a project's declarations, sources, and bundle, and no workflow installed the engine
off Linux.

## Decision

Keep the pin. Lowering it to reach an Intel wheel reintroduces the advisories; replacing the library
is a separate supply-chain decision that needs its own record.

- **State the prerequisites.** Getting Started names Python 3.12 and `uv` before the first command
  and says what Intel macOS additionally needs.
- **Preflight in `doctor`.** `agentce doctor` adds an `environment` section: the running interpreter
  version (read from `sys.version_info`), the platform, and the installed `cryptography` version, its
  wheel tags (read from the distribution's own `WHEEL` file), and whether that is a prebuilt wheel or
  a source build. A source build is a warning-kind note that names the fix (Rust plus OpenSSL 3 with
  `OPENSSL_DIR`), not a failure, because it works. An interpreter below the minimum, or a
  `cryptography` that is absent or does not import, is a problem with a stable key and a fix.
  Provenance is inferred from the platform tag: a tag outside the set `cryptography` publishes
  (`manylinux`, `musllinux`, `macosx_11_0_arm64`, `win_amd64`) can only be a local build.
- **Prove it in CI.** The `python-install-matrix` job in `ci.yml` installs the engine from the frozen
  lockfile on `ubuntu-latest`, `macos-latest` (arm64), and `macos-15-intel` on every push and pull
  request, asserts `agentce doctor` reports the wheel provenance the documentation states for that
  platform, and runs the quickstart to a report. The Intel leg provides OpenSSL 3 from Homebrew and
  compiles `cryptography`.

## Alternatives considered (with why not)

- **Loosen the pin to a release with an Intel wheel.** Rejected: it reintroduces the three advisories
  the dependency-review gate blocks.
- **Replace `cryptography` for Ed25519.** Out of scope here: a new dependency has its own advisory
  history and wheel coverage and needs its own decision.
- **Detect a source build by timing or by probing for `rustc`.** Rejected: the wheel tag is a fact
  recorded in the installed distribution, so nothing is guessed.
- **Only document the requirement.** Rejected: an unverified sentence drifts; the matrix job fails
  when the stated provenance stops being true (for example when an Intel wheel is published).

## Consequences

`doctor`'s JSON gains a section and three message keys; its exit code is unchanged for a healthy
environment. Intel macOS pull-request runs pay a from-source build of `cryptography`. Windows is not
in the matrix and the documentation says so. Determinism is unaffected: `doctor` output is diagnostic
and not a canonical output.

## Verification

`engines/python/tests/test_environment.py` covers the wheel classification, the notes and problems,
and `doctor`'s report of the real interpreter and wheel tag; the `python-install-matrix` job proves
the install and the quickstart on each platform.
