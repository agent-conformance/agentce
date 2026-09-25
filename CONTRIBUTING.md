# Contributing

This project is under development and is not yet accepting external contributions. When it opens,
the path below applies; it is documented now so it is ready.

## The external-contributor path

1. Fork the repository and branch from `main`.
2. Every commit requires a Developer Certificate of Origin sign-off (`git commit -s`): the
   `Signed-off-by` trailer's email must exactly match your own commit author email. This is the same
   rule for every author, checked by the `dco` job on every pull request (see
   `docs/adr/0019-external-contributor-lane.md`).
3. Automated dependency-update pull requests get no exemption. A bot's commit passes only when its
   `Signed-off-by` email matches the bot's own author email exactly. Dependabot's default commits
   fail this rule: they are authored from the bot's `users.noreply.github.com` address but signed off
   as `support@github.com`, so the `dco` job refuses them. A wanted update from such a pull request
   reaches `main` as a maintainer's own re-authored, signed-off commit.
4. Open a pull request against `main`. Every required status check must pass before a maintainer
   reviews it.
5. Work happens on feature branches; `main` is merged deliberately.
6. Never commit secrets, credentials, personal data, or customer material. Test data is synthetic by
   construction.

## Running the checks

There is no root Python or Node project. Most packages — `engines/python`, `adapters/`, `corpus/`,
`conformance/`, `spec/`, `skills/`, `tools/`, and `examples/` — are independent `uv` projects, each
with its own lockfile; `engines/typescript` and `website/` are their own `pnpm` workspaces;
`engines/java` is a Gradle project. A bare `pytest` or `ruff` typed at the repository root has
nothing to run against and fails. Run a package's checks from inside it. The reference engine,
`engines/python`:

```bash
cd engines/python && uv run pytest -q
```

```bash
cd engines/python && uv run ruff check . && uv run ruff format --check .
```

Every other Python package follows the same shape (`cd <package> && uv run pytest -q` /
`uv run ruff check .`); Node packages that define their own `lint`/`test` scripts run the
equivalent `pnpm --dir <package> test`. A step that genuinely cannot run offline here (a one-time
`uv sync`/`pnpm install`, or a step needing a live service) is marked with a `no-run` fence-suffix
and a reason rather than left to fail silently.
