# Chrome Web Store listing — Crumbs Journey Viewer

> Review-ready copy for a Chrome Web Store submission. Keep in sync with the
> manifest description and `PRIVACY.md`.

## Listing fields

**Name:** Crumbs Journey Viewer

**Summary (≤132 chars):**
View the Crumbs attribution journeys and receipts on the pages you visit.
Opt-in per site; nothing leaves your browser unless you verify a receipt.

**Category:** Developer Tools

**Description:**

Crumbs Journey Viewer shows whether the page you are on carries a Crumbs
attribution journey receipt and lets you verify receipts against the Crumbs
ledger you configure.

Crumbs is an open-source, consent-native attribution layer for agentic
commerce: when an AI agent refers you to a merchant, the merchant's site may
issue a signed attribution receipt that records the referral. This extension
is the privacy-preserving viewer for those receipts.

- **Opt-in per site.** Nothing is enabled until you click "Enable on this
  site" for a page — the extension then injects a content script only on that
  origin.
- **Viewer only.** It observes the same JS-visible receipt state any page
  script can read (the SDK mirror cookie and localStorage receipts). It never
  touches the HttpOnly merchant receipt cookie.
- **Verify is explicit and ids-only.** "Verify" sends only the receipt id to
  the ledger URL you configure. No ledger is contacted until you set one and
  click verify.
- **No tracking.** No analytics, no telemetry, no background network calls,
  no remote code.

**Single purpose:** display and verify Crumbs attribution receipt state on
pages the user has opted into.

## Permission justification

| Permission | Type | Why |
|---|---|---|
| `storage` | install | Persist your per-site opt-in and the configured verify API URL |
| `activeTab` | install | Read page state when you open the popup |
| `scripting` | optional | Inject the viewer content script after you enable a site |
| host access (`https://*/*`) | optional | Injected only on the site you enable, on your gesture |

The extension does **not** request `cookies`, does not declare static
`content_scripts`, and loads no remote code. Disabling the last enabled site
also drops the `scripting` permission.

## Privacy

See `ext/PRIVACY.md` (ships with the package). Data use summary:

- Collected: none by the extension itself. Receipt state is read from the
  page you opted into and stored only in that page's context or your local
  `storage`.
- Shared: only the ids-only verify request to the ledger URL you configure.
- Retention: local only; uninstall clears extension storage.

## Support

- Project + issues: https://github.com/Ir0nBeard/crumbs (open source, MIT)
- Privacy policy URL for the store listing: point at `ext/PRIVACY.md` content
  served from the project repository.
