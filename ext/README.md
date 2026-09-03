# Crumbs Journey Viewer — Chrome MV3 extension (scaffold, v0.1)

Local MVP scaffold. **Not published, not submitted to any store** (OPSEC:
store listings are gated on domain + GitHub + explicit go).

## Load it (development)

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select this `ext/` directory
3. Visit a merchant page, click the Crumbs icon, click **Enable on this site**
   (this is the per-site opt-in: optional host permission + content script
   injection happen only on that user gesture)
4. **Refresh** shows the page's receipt state; **Verify** (ids-only in v0.1)
   checks the ledger.

## Design posture (per p5b-distribution.md §A.2)

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

## NOT in v0.1 (stubs / future)

* Full signed-wire verification from the popup (ids-only today — see PRIVACY.md §4)
* Chrome Web Store / Edge Add-ons / Firefox AMO listings
* Enterprise deployment kit (ExtensionInstallForcelist/ExtensionSettings JSON)
* `chrome.cookies`-based HttpOnly-cookie reading (deliberately not requested)
