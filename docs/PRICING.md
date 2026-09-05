# Pricing

Crumbs is free software. The v0.1 ledger, JS SDK, MCP server, Chrome
extension, WordPress plugin, and Shopify scaffold are all open source and
cost nothing to download, run, modify, or integrate. There is **no hosted
Crumbs service yet** — v0.1 is self-hosted — and there is **no fee on any
receipt, conversion, or payout record** the ledger creates.

## What you pay

| Item | Cost | Notes |
|---|---|---|
| Ledger server (`server/`) | $0 | MIT; run your own instance (SQLite for development, Postgres for production) |
| JS SDK (`sdk/`) | $0 | MIT; zero runtime dependencies |
| MCP server (`mcp/`) | $0 | MIT; stdio JSON-RPC wrapper over the ledger API |
| Chrome extension (`ext/`) | $0 | MIT; MV3, minimal permissions |
| WordPress plugin (`wp/`) | $0 | GPL-2.0-or-later |
| Shopify scaffold (`shopify/`) | $0 | MIT |
| Receipts / conversions / payout records | $0 | No per-record fees, no subscription |

What running Crumbs actually costs you:

- **Your infrastructure.** The ledger is self-hosted. For a small deployment
  that is a single database; production deployments typically add Postgres
  and optionally Redis. Costs are whatever your existing hosting costs.
- **Payout-rail costs.** When a scheduled payout settles, settlement happens
  on a licensed rail you choose (for example an x402 facilitator on Base,
  with its own network and facilitator fees). Crumbs records the settlement
  proof; the rail moves the money and sets its own fees. The ledger itself
  never holds or moves funds — payouts are records, and amounts are stored in
  integer micro-units to keep them exact.
- **Commissions you set.** Merchant programs define referral commission rates
  in basis points (`commission_rate_bps`). A program's commission is a
  business decision between the merchant and the referring agent: the ledger
  records and enforces the agreed terms and takes nothing off the top.

## Roadmap

A hosted Crumbs ledger (managed instances, no self-hosting required) is under
evaluation. If a hosted offering ships, its prices will be published on this
page before anything is charged, and the open-source core will remain free.

*Last updated for v0.1.1 (2026-09-05).*
