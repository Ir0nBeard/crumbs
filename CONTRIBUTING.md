# Contributing

Thanks for considering a contribution to Crumbs. This is a small, early-stage
project (v0.1) — the fastest way to help right now is to run it, read the
protocol, and tell us what breaks or what is unclear.

## Ground rules

- **Be kind and specific.** Issues and PRs should describe the problem, the
  context, and the proposed change clearly.
- **Respect the design constraints:** consent-gated by default (no receipt
  without a recorded consent basis), neutral infrastructure (no platform
  lock-in baked into the protocol), no stored value or float in the ledger,
  and zero dependencies in the SDK.
- **Security issues are never reported in public issues** — see
  [SECURITY.md](SECURITY.md).

## Reporting bugs & feature ideas

Open a [GitHub issue](https://github.com/Ir0nBeard/crumbs/issues) using the
templates (bug / feature). Before opening, check the existing issues and the
honest stub list in [CHANGELOG.md](CHANGELOG.md) — the thing you want may
already be a known gap with a documented reason.

## Development setup

Follow [QUICKSTART.md](QUICKSTART.md) — a venv, `server/requirements.txt`,
and Node >= 18 is all you need. The full test suite must pass before a PR:

```sh
.venv/bin/python -m pytest tests/ -q     # server + mcp
(cd sdk && node --test test/sdk.test.mjs) # sdk
```

## Code conventions

- **Python** (`server/`, `mcp/`, `tests/`): type annotations, docstrings that
  describe the *invariant* being enforced (not just the code path), plain
  stdlib where reasonable. Run the test suite before pushing.
- **JavaScript** (`sdk/`, `ext/`, `shopify/`): the SDK keeps **zero
  dependencies** — no new npm packages without a strong reason. If you change
  `sdk/src/crumbs-core.cjs`, regenerate the bundle (`node
  sdk/scripts/build-iife.mjs`) and keep the WordPress vendored copy in sync
  (`cp sdk/dist/crumbs.iife.js
  wp/crumbs-attribution/vendor/crumbs-sdk/crumbs.iife.js`).
- **PHP** (`wp/`): keep the plugin GPL-2.0-or-later (header + `readme.txt`),
  escape output, and never load executable code from third-party systems.
  Lint with `php -l` before committing.

## Licensing

By contributing you agree that your changes are licensed under the same terms
as the component you touch: MIT for core code, GPL-2.0-or-later for
`wp/crumbs-attribution/` (see the [LICENSE-MIT](../LICENSE-MIT) and
[LICENSE-GPL-2.0-or-later](../LICENSE-GPL-2.0-or-later) files). Keep license
notices intact when vendoring or copying files.

## AI contributions

Automated agents are welcome to *read* and *test* the code, but please do not
open issues or PRs directly from bots without a human in the loop — a human
must be able to answer review questions about the change. If your PR was
assisted by an AI tool, say so in the description; that is not a problem, it
just keeps review honest.
