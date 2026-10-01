# 0024 — Pin Intel macOS to the last `cryptography` release with an Intel wheel

Status: accepted
Spec refs: §13.4 AX-1, AX-5, AX-8

## Context

ADR 0014 pinned `cryptography>=50,<51` everywhere and relied on a source build (Rust plus OpenSSL 3)
for Intel macOS, the one platform that package ships no prebuilt wheel for. In practice that source
build needs two prerequisites a fresh machine does not have, so the one documented install command
(`uv sync`) did not work there without a manual step first — the disclosed prerequisite removed the
surprise, not the friction. A fresh-install run on this Intel Mac
(`verify-agentce/20260930-113643-18899/launch.log`, MAINTAINER-INBOX row 29) hit exactly that: the
source build failed without OpenSSL headers present.

Checked directly against the PyPI index on 2026-09-30 (`pypi.org/pypi/cryptography/json`): every
`cryptography` release from 49.0.0 through the newest (50.0.2) ships `macosx_11_0_arm64` wheels only.
48.0.1 is the newest release that ships a `macosx_10_9_universal2` wheel — a real prebuilt binary that
installs on both Intel and Apple-silicon macOS, not a source build. No release between 48.0.1 and
50.x adds an Intel wheel back; ADR 0014's `!=50.0.2` pin (commit `c2fbeba`) fixed nothing, because the
absence started four releases earlier.

Pinning Intel macOS to 48.0.1 reaches a prebuilt wheel at the cost of three advisories `50.0.x` clears:

| Advisory | Severity | Vulnerable range | Fixed in | Surface |
|---|---|---|---|---|
| GHSA-g6cj-pr64-35w5 (PKCS#7 `EnvelopedData` decryption: Bleichenbacher oracle) | high | `>= 44.0.0, < 50.0.0` | 50.0.0 | `cryptography.hazmat.primitives.serialization.pkcs7` |
| GHSA-jwv3-5hgf-82ww (duplicate self-signed intermediates: exponential path-building) | high | `>= 42.0.0, < 49.0.0` | 49.0.0 | `cryptography.x509` chain verification |
| GHSA-m2h6-j472-rp4c (wildcard DNS name escapes a name-constrained sub-CA) | medium | `>= 45.0.0, < 49.0.0` | 49.0.0 | `cryptography.x509` chain verification |

(Checked via `gh api /advisories/<id>` against the PyPI index on 2026-09-30.) The medium one does not trip
`fail-on-severity: high` on its own, so it needs no allow-list entry; the allow-list (below) covers only the
two high ones. The fourth advisory the 50.x upgrade cleared, GHSA-537c-gmf6-5ccf (vulnerable OpenSSL bundled
in the wheel), is fixed in 48.0.1 itself, so it does not apply here.

Neither vulnerable surface is reachable from this repository: `engines/python/agentce/signing.py`
imports only `cryptography.exceptions.InvalidSignature` and
`cryptography.hazmat.primitives.asymmetric.ed25519`;
`adapters/supply-chain/src/agentce_adapters/supply_chain.py` imports
`cryptography.exceptions`, `cryptography.hazmat.primitives.hashes`,
`cryptography.hazmat.primitives.asymmetric.{ec,ed25519}`, and a serialization module for key
encode/decode. Neither `pkcs7` nor `x509` is imported anywhere in either tree (confirmed by grep). The
engine and adapter sign and verify with Ed25519 (and EC in the adapter); they never decrypt PKCS#7
envelopes or build X.509 certificate chains.

## Decision

1. Split the `cryptography` requirement by platform marker in `engines/python/pyproject.toml` and
   `adapters/supply-chain/pyproject.toml`:
   `cryptography>=50,<51; sys_platform != 'darwin' or platform_machine != 'x86_64'` and
   `cryptography>=48,<49; sys_platform == 'darwin' and platform_machine == 'x86_64'`. Every other
   platform keeps the advisory-clean 50.x line; only Intel macOS drops to 48.x, and only because no
   higher release has a wheel for it.
2. Drop ADR 0014's `!=50.0.2` exclusion from `engines/python/pyproject.toml`: it addressed a premise
   (that some 50.x release has an Intel wheel and some does not) that this ADR's PyPI check disproved.
3. Add `GHSA-g6cj-pr64-35w5` and `GHSA-jwv3-5hgf-82ww` to `allow-ghsas` in
   `.github/workflows/dependency-review.yml`, with an inline comment pointing at this ADR. The
   allow-list is global by advisory ID (the action has no per-path scoping, ADR 0018's "Alternatives
   considered" applies unchanged here): introducing PKCS#7 or X.509 chain-building code anywhere in the
   repository would not be caught by `dependency-review` for these two advisories specifically. The
   reachability argument above, plus a from-source `grep` for `pkcs7`/`x509` importers as part of any
   future review of this pin, is the backstop.
4. `agentce doctor`'s wheel-provenance check (ADR 0014, `engines/python/agentce/environment.py`)
   recognizes the `macosx_10_9_universal2` tag as a prebuilt wheel, not a source build: it is one,
   and telling an Intel-macOS user to install Rust and OpenSSL for a wheel that already installed
   would be wrong advice.
5. Revisit on two triggers: (a) a `cryptography` release resumes shipping an Intel-macOS wheel on the
   50.x line or later — drop the platform split and the two advisories from the allow-list; (b) a
   future change imports `pkcs7` or `x509` chain-building anywhere in `engines/python` or
   `adapters/supply-chain` — re-derive reachability before assuming the allow-list still applies.

## Alternatives considered (with why not)

- **Keep requiring the source build (ADR 0014 as written).** Rejected: it does not reach a working
  install without a manual step a fresh machine never has by default, which is the problem this ADR
  fixes, and the row-29 fresh-install run proved it fails in practice, not just in theory.
- **Drop the platform split and accept 50.x everywhere, telling Intel-macOS users to build from
  source.** Rejected: identical to the status quo ADR 0014 already tried; the prerequisite is real
  friction the project can remove instead of disclosing.
- **Replace `cryptography` with a library that ships an Intel wheel on a current release.** Rejected:
  a new signing dependency is a separate supply-chain decision with its own advisory history and needs
  its own ADR; nothing else is a drop-in replacement for `Ed25519`/`EC` signing plus PEM
  serialization here.
- **`warn-only: true` on the whole `dependency-review` job.** Rejected: disarms `fail-on-severity` for
  every dependency, not just these two advisories — the opposite of the narrowest fix (ADR 0018,
  0021).

## Consequences

- Intel macOS installs a real prebuilt `cryptography` wheel with nothing beyond `uv` and Python, same
  as every other platform in the matrix; the Rust/OpenSSL prerequisite in Getting Started is gone for
  the common case. The `python-install-matrix` CI job's Intel leg now expects `wheel: prebuilt` and no
  longer provisions OpenSSL.
- Intel macOS runs an older `cryptography` release than every other platform, with three known
  unreachable-but-unpatched advisories (above) until upstream ships an Intel wheel again. This is a
  known limitation, not a silent gap: it is recorded here, in Getting Started, and in
  `agentce doctor`'s `environment` section (the installed version and wheel tag are always reported).
- Every tracked `uv.lock` resolving `cryptography` now carries two `cryptography` entries (48.0.1 and
  50.0.1) distinguished by resolution marker, not just the two packages that pin it directly. Every other
  workspace that depends on `agent-conformance` (editable from `engines/python`) picks up the split too,
  because `uv lock` re-resolves the editable dependency's own split specifier (item 18.58). `tools/no_ml_check.py`'s
  denylist scan is unaffected (`cryptography` is not on the no-ML denylist; the scan is not
  platform-aware and checks both entries the same way).
- No effect on determinism: `cryptography` is a signing/verification dependency, not part of the
  canonical form or the evaluation path, and every platform still signs and verifies against the same
  Ed25519 vectors (SPEC §6.7, ADR 0006).

## Verification

- `engines/python/uv.lock` and `adapters/supply-chain/uv.lock` each resolve a
  `macosx_*_(universal2|x86_64)` `cryptography` wheel (`grep -E` in `state show 18.55`'s acceptance).
- `uv run pytest` on this Intel Mac, with `uv sync` resolving `cryptography==48.0.1` here: full suite
  green (1183 passed).
- `uv run agentce doctor --project corpus/quickstart --json` on this Intel Mac reports
  `wheel_source: "prebuilt"` for `cryptography==48.0.1`/`macosx_10_9_universal2`, with no source-build
  note.
- `.github/workflows/dependency-review.yml`'s `review` job, next time it runs against `main` (this
  branch's phase-merge PR): passes with the two-advisory allow-list; `gh run view <run-id> --log`
  shows no other advisory.
- `engines/python/tests/test_environment.py::test_wheel_source_classifies_platform_tags` covers the
  `macosx_10_9_universal2` tag as `prebuilt` and keeps the architecture-specific `macosx_10_12_x86_64`
  tag as `source-build` (no CPython release in the pinned 48.x/50.x lines publishes a bare macOS
  x86_64 wheel; cryptography 46.0.0-46.0.3 shipped one, but tagged `pp*` for PyPy only, not `cp*`).
- Item 18.58 extends this to every other tracked `uv.lock` that resolves `cryptography` transitively
  (the 20 workspaces besides the two above): `tools/cryptography_intel_wheel_check.py` parses every
  tracked lock as TOML and checks its resolved wheels for an Intel-macOS one, in CI, with a seeded fault.
