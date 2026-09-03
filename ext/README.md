# Crumbs Journey Viewer — Chrome MV3 extension (v0.1)

A developer-facing viewer for Crumbs attribution receipts. Shows whether the
current page carries a Crumbs journey receipt and any heuristic agent signals,
and lets you *verify* a receipt against the ledger you configure.

**Status:** v0.1 scaffold. Not yet submitted to any store.

## Load it (development)

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select this `ext/` directory
3. Visit a merchant page, click the Crumbs icon, click **Enable on this site**
   (this is the per-site opt-in: optional host permission + content script
   injection happen only on that user gesture)
4. **Refresh** shows the page's receipt state; **Verify** (ids-only in v0.1)
   checks the ledger

The ledger URL is **not hard-coded** — configure it in the popup/options
(`crumbs_verify_api`, e.g. the base URL of the ledger instance you run). Verify
is disabled until one is set.

## Design posture

* Minimal permissions: `storage` only at install; `scripting` + host access
  are optional and requested per site by explicit user action.
* No remote code (MV3 requirement), no eval, no background network calls.
* Privacy disclosure ships in `PRIVACY.md` and in the popup.
* Consent-native: the extension never writes tracking state; it only observes
  receipts the SDK issued after site-level consent.

## Files

* `manifest.json` — MV3, minimal/optional permissions
* `background.js` — per-site opt-in (permissions.request + content-script registration)
* `content.js` — reads mirror cookie / SDK storage / agent signals on opt-in sites
* `popup/` — receipt/journey viewer + privacy disclosure
* `PRIVACY.md` — store-ready disclosure text

## Not in v0.1 (stubs / future)

* Full signed-wire verification from the popup (ids-only today — see PRIVACY.md)
* Chrome Web Store / Edge Add-ons / Firefox AMO listings
* Enterprise deployment kit (ExtensionInstallForcelist/ExtensionSettings JSON)
* `chrome.cookies`-based HttpOnly-cookie reading (deliberately not requested)
