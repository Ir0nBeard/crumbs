# Security

Crumbs is in early development (v0.1). We take the security of the receipt
protocol, the ledger, and the SDK seriously — and we want to hear about
problems **before** they become public.

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Please report it
privately:

- Email **security@exo-trust.com**, or
- Use GitHub's private vulnerability reporting (Security → *Report a
  vulnerability*) once this repository has it enabled.

Include, if you have it: the affected component (`server/`, `sdk/`, `mcp/`,
`wp/`, `ext/`, `shopify/`), the version or commit, a minimal reproduction, and
your suggested fix. Anything you send is treated as confidential until a fix is
released.

## What we'll do

- Acknowledge your report within **5 business days**.
- Work with you on a fix and a coordinated disclosure timeline.
- Credit you in the changelog/advisory if you want it (no-pressure).

## Scope & expectations (v0.1)

- The ledger is a **self-hosted reference implementation**. Operators are
  responsible for deployment hardening: TLS, secret management for
  `CRUMBS_SIGNING_KEYS`, Postgres + Redis in production, and per-deployment
  rate/budget tuning. See [ARCHITECTURE.md](ARCHITECTURE.md) for the threat
  model and its honest limits.
- Out of scope for private reporting: feature requests, general questions, and
  bugs without a security impact (use [issues](https://github.com/Ir0nBeard/crumbs/issues)
  for those).
