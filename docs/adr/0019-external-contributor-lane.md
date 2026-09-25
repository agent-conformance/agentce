# 0019 — The commit-identity rail becomes DCO-standard; a real external-contributor lane

Status: accepted
Spec refs: —

## Context

The commit-identity rail enforced one rule for every commit reachable from any ref: author and any
`Signed-off-by` trailer had to be exactly the maintainer's own identity, nothing else. That rule is
right for the maintainer's own history, but it has no lane for a genuine outside contribution —
authored by someone else and correctly signed off under the Developer Certificate of Origin, the
normal open-source convention — nor for an automated dependency-update pull request, whose commit is
authored and signed off by the bot itself. Both are refused outright today. The project's own
`CONTRIBUTING.md` says it is not yet open to external contributions, so this was never exercised in
practice, but the rail as designed cannot accept a pull request from anyone but the maintainer even
once that changes, and it already refuses every automated dependency-update pull request the
repository receives.

## Decision

1. **Two lanes, one rule each, always evaluated together.** A commit whose author presents the
   maintainer's identity — the maintainer's canonical address, any other address recorded as the
   maintainer's, or the maintainer's name — is on the maintainer lane and keeps the exact-identity
   rule: it must be authored from the canonical address exactly, and every `Signed-off-by` on it must
   be that same address too. Presenting the maintainer's name or another of the maintainer's addresses
   never moves a commit onto the external lane. A commit authored by anyone else is DCO-standard: accepted only when at least one `Signed-off-by`
   trailer's email exactly matches the *author's own* email — name alone does not count, which is what
   tells a bot's authoring address apart from a spoofed sign-off carrying the same display name. A
   commit-message denylist applies to every commit on both lanes, always; neither lane can be used to
   bypass it.
2. **Where each lane applies.** The external lane exists for commits that reach `main` through
   someone else's pull request, so it applies only where such commits legitimately appear: the local
   identity rail's scan over every ref, and the `dco` job on a pull request. The maintainer's and the
   project's own automated commits on a working branch — the commits not yet on `main` that the
   automation checks before it records a unit of work complete — are checked under the maintainer
   lane only: authored from the canonical address, at least one `Signed-off-by`, and every one of
   them that same address. The external lane is never consulted for them, so a foreign or bot author
   on that branch is refused even when it carries a sign-off matching its own author. The `dco` job
   is identity-agnostic by design (point 3), so the maintainer-lane rule is enforced by the local
   identity rail, not by the public job.
3. **A real, offline, required check for the external lane.** `tools/dco_check.py` implements the
   DCO half of the rule with no reference to any particular contributor's identity — it is the same
   check for the maintainer, an automated dependency-update pull request, and any outside fork. It
   ships as the `dco` job in `.github/workflows/ci.yml`, run on every push and pull request, needing
   no secret and no private checkout, so it is safe on a fork pull request under the default
   `pull_request` trigger.
4. **`CONTRIBUTING.md` documents the path**: fork, sign off as yourself, open a pull request against
   `main`, pass every required check. The project's decision to stay closed to external contributions
   until general availability is untouched by this decision — the lane is documented and proven ready,
   not opened.
5. **Automated dependency-update pull requests (for example, Dependabot) follow the external lane**,
   not a special case: a bot's commit is accepted only when its `Signed-off-by` email matches the
   bot's own author email exactly, the same DCO rule as any other outside contributor. Dependabot's
   default commits do not meet it: they are authored from the bot's `users.noreply.github.com`
   address but signed off as `support@github.com`, so the rule and the `dco` job refuse them as they
   are. A wanted update from such a pull request reaches `main` as the maintainer's own re-authored,
   signed-off commit. No separate mechanism or exemption is created for bots.
6. **Making the `dco` check a required status check on the protected `main` branch, and deciding how a
   fork's pull request actually merges alongside the maintainer's own per-phase merges, are repository
   administration** — branch protection and merge policy are not something a commit can enact. Both
   are tracked as a human action.

## Alternatives considered (with why not)

- **Keep the closed rule and add a separate allowlist of trusted external identities.** Rejected: an
  allowlist is not a contributor lane, it is a maintainer-curated list of pre-approved people, which
  does not solve "the project cannot accept a genuine outside pull request" and adds an ongoing
  administrative burden with no proportionate benefit.
- **Send every author other than the maintainer's canonical address to the external lane.**
  Rejected: the maintainer's other recorded address, the maintainer's name over any address, and a
  self-signed foreign author on the automation's own working branch would then all pass as
  "external", so the one-fixed-identity rule for the project's own commits would no longer hold.
- **Match a `Signed-off-by` by display name instead of email.** Rejected: a bot's authoring address
  and its sign-off address commonly differ while the display name is identical (the shape this
  decision specifically has to handle), so a name-only match would accept a forged sign-off trivially.
  The match is pinned to the email on purpose.
- **Drop the message denylist on the external lane, since an outside contributor cannot know what it
  contains.** Rejected: the denylist exists to keep specific text out of every public commit
  regardless of who authored it; weakening it on any lane — including the lane the maintainer's own
  automation commits on — defeats its purpose.

## Consequences (including determinism, portability to TS/Java, performance)

- **Determinism / offline.** Both the identity-rail logic and `tools/dco_check.py` are pure git-log
  inspection: no network, clock, or locale dependence. `dco_check.py` uses only the standard library.
- **Portability.** This is a repository-governance mechanism, not part of any engine's evaluation
  path; it has no TypeScript or Java counterpart and touches no engine dependency tree.
- **No weakening.** The exact-identity rule for the maintainer's and the project's own automated
  commits is unchanged: any commit presenting the maintainer's identity, and every commit on the
  automation's own working branch, must use the canonical address; the message denylist still applies to every commit on every lane. Only the
  previously-absent external lane is added.
- **Residual scope.** `dco_check.py` proves a commit carries a matching sign-off; it does not and
  cannot verify a person's real-world identity behind an email address — that is the same limit every
  DCO-based project accepts, and is why merge policy for an accepted external pull request stays a
  human, reviewed decision.

## Verification (the test or check that proves the decision holds)

- The identity-rail logic's own test suite proves, in a throwaway sandbox: a maintainer commit signed
  off by the maintainer is accepted; a maintainer-authored commit signed off by someone else is
  refused; an external commit signed off by its own author is accepted; an external commit that is
  unsigned, signed off by a different person, or signed off with an email that differs from the
  author's own (even when the display name matches) is refused; and a denylisted message is refused
  on both lanes. The same suite proves the lane routing: the maintainer's other recorded address and
  the maintainer's name over a foreign address are refused on the scan over every ref, and on the
  automation's own working branch a foreign or bot author is refused even with a matching sign-off,
  as is an unsigned commit from the canonical address; a Dependabot-default commit (sign-off
  `support@github.com`) is refused on both.
- `tools/dco_check.py --self-test` proves the same DCO half — acceptance and refusal, email-pinned not
  name-pinned — self-contained and offline, run as the first step of the `dco` CI job.
- The `dco` job runs on every push and pull request and is exercised by real commit ranges in CI.
