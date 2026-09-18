# 0011 — Placeholder sweep

Status: accepted
Spec refs: —

## Context

A recurring failure mode for a project that ships in stages is that unfinished work reaches the
tree looking finished: a `TODO` left in a shipped file, a command that reports "not yet
implemented", a digest field filled with zeros instead of a real hash, a documentation section that
is a heading with nothing under it. Each of these is a small dishonesty, and there was no automated
check that caught them. The other repository checks look at specific files; nothing scanned the
whole tree for the markers that betray an unfinished edge.

Two kinds of such markers must be told apart. Some are legitimate and permanent: a control catalog
entry honestly labelled `status: placeholder` for a standard that has not been published yet, an
illustrative identifier a user is meant to replace, the conventional all-zero "no predecessor"
pointer at the head of a hash chain. Others are real, known gaps awaiting a fix: an engine command
that is not implemented yet. A blanket ban would force the legitimate ones to be disguised; a
blanket allow would let the real gaps hide.

## Decision

Add `tools/placeholder_sweep.py`, a deterministic scanner that walks every tracked file for a fixed
set of placeholder patterns (TODO, FIXME, XXX, TBD, HACK, "placeholder", the "not (yet)
implemented" family, "coming soon", "under construction", "lorem ipsum", "stub", "dummy",
"changeme", all-zero digests and UUIDs, `example.com`/`example.org` outside fixtures and
documentation, and empty Markdown sections). Every hit must be accounted for in
`tools/placeholder_allowlist.yaml`, or the sweep fails.

Each allowlist entry names a path, a pattern id, the exact number of occurrences, a class, and a
justification:

- `legitimate` — a permanent, honest placeholder that stays.
- `debt` — a known defect awaiting its fix. The entry is removed by the change that fixes the
  defect; the debt total may only fall, and the extended end-to-end gate requires it to reach zero
  (`--fail-on-debt`).

The exact-count rule is deliberate: change the content and the count moves, so the sweep fails
until the allowlist is updated in the same step. That keeps the allowlist honest — it cannot drift
away from the tree, and a new placeholder in an already-listed file is caught rather than absorbed.

`tools/placeholder_sweep.py` runs in CI on every push and pull request
(`.github/workflows/placeholder-sweep.yml`). It excludes its own source, its allowlist, and its
self-test fixtures, which necessarily name every pattern; `example.com`/`example.org` do not fire
inside fixtures, testdata, documentation, or `*.template.*` files, where they are the reserved
illustrative domains (RFC 2606), not placeholders.

## Alternatives considered (with why not)

- **A blanket grep in CI with an inline ignore list.** Rejected: an ad-hoc ignore list carries no
  class or justification, cannot distinguish permanent placeholders from tracked debt, and cannot
  express the "debt must reach zero" gate.
- **Line-anchored allowlist entries.** Rejected: line numbers move with every edit, so the
  allowlist would churn constantly and mask real changes. Counting occurrences per file is robust
  to reformatting while still catching a new hit.
- **Behavioural detection of "returns success after doing nothing".** A vacuous success path is a
  placeholder too, but detecting it reliably across three languages is a static-analysis problem
  with a high false-positive rate. That behaviour is guarded where it is meaningful — by the
  language test suites and by the specific checks that assert a command fails loudly when it has
  nothing to evaluate — rather than by a textual sweep. The sweep stays textual and structural, and
  is extended whenever a real placeholder slips past it.
- **Standard-library only, like the other checks.** Rejected for the same reason as the claims
  register (ADR-0010): the allowlist is YAML, for which the repository already standardises on
  PyYAML.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- The sweep is deterministic: it reads tracked files in sorted order, applies fixed regexes, and
  makes no network call and depends on no clock, locale, or ordering.
- It is language-neutral repository governance; it shares no code with the engines and places no
  requirement on the TypeScript or Java engines.
- Performance is negligible: a single pass over the tracked text files.
- The debt baseline starts at the engines' "not yet implemented" commands; each is removed by the
  work that implements the command, and the count can only fall.
- Introducing a legitimate placeholder (a new draft-standard crosswalk, say) now requires an
  allowlist entry with a justification — a small, deliberate friction that is the point.

## Verification (the test or check that proves the decision holds)

- `uv run --project tools --frozen python -m placeholder_sweep --self-test` exits 0 and prints
  `PLACEHOLDER SWEEP SELF-TEST PASSED`: a good fixture passes and each bad fixture (an unaccounted
  hit, a changed count, a stale entry, an unlisted zero digest, an empty section, an invalid class,
  a missing justification) is caught on the rule it targets.
- `uv run --project tools --frozen python -m placeholder_sweep` scans the repository and exits
  non-zero on any unaccounted hit or stale entry.
- `.github/workflows/placeholder-sweep.yml` runs both on every push and pull request.
