# Contributing

This project is under development and is not yet accepting external contributions. When it opens,
the path below applies; it is documented now so it is ready.

## The external-contributor path

1. Fork the repository and branch from `main`.
2. Every commit requires a Developer Certificate of Origin sign-off (`git commit -s`): the
   `Signed-off-by` trailer's email must exactly match your own commit author email. This is the same
   rule for every author, checked by the `dco` job on every pull request — including a pull request
   opened by an automated dependency-update bot such as Dependabot, which signs off under the
   identical rule (see `docs/adr/0019-external-contributor-lane.md`).
3. Open a pull request against `main`. Every required status check must pass before a maintainer
   reviews it.
4. Work happens on feature branches; `main` is merged deliberately.
5. Never commit secrets, credentials, personal data, or customer material. Test data is synthetic by
   construction.
