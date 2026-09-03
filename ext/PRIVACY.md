# Crumbs Journey Viewer — Privacy Disclosure (v0.1)

Last updated: 2026-08-29. This extension is a LOCAL MVP scaffold — it is not
published to any store.

## 1. What the extension does

* Shows whether the current page carries a Crumbs attribution receipt
  (journey) and any heuristic agent signals.
* Lets you *verify* a receipt against the Crumbs ledger by explicit button
  press.

## 2. Permissions (minimal by design)

| Permission | Why |
|---|---|
| `storage` | remember which sites you opted into; nothing else |
| `activeTab` | read the ACTIVE tab's URL only, and only when you click the extension icon — the popup needs to know which origin you are on to show/opt-in state. `activeTab` is gesture-gated: it grants temporary access to the current tab solely for this click, never background access |
| `scripting` (optional) | inject the viewer content script — requested only when you enable a site |
| `https://*/*` (optional host) | content script can run on a site only AFTER you click "Enable on this site" |

No `cookies`, no `webRequest`, no `tabs` permission, no background network
access. With `activeTab`, the extension reads the active tab's URL only while
the popup is open (triggered by your icon click); it does not watch tabs or
collect browsing history. On tabs where you have not enabled the viewer,
nothing on the page is read at all.

## 3. Data handling

* All state (enabled-site list, collected page state) stays in
  `chrome.storage.local` — per-browser, never uploaded.
* The extension makes **no network requests of its own**. The only network
  call in the entire codebase is the popup's "Verify" button, which sends the
  receipt payload to the ledger endpoint you configure. Nothing else.

## 4. v0.1 viewer limitation (honest note)

The popup stores only receipt *ids* (rid/jid/aid/exp) — the signed wire form
lives in the page's SDK storage, which the content script intentionally does
not copy out. The "Verify" button therefore performs an ids-only check and is
labeled as such in the UI. Full signed-wire verification from the extension is
a post-v0.1 item (see docs/BUILD.md).

## 5. Consent posture

This extension does not set or read any *tracking* state. It only observes
receipts that the Crumbs SDK has already issued **after** the site obtained
ePrivacy-compliant consent (the ledger refuses issuance without a recorded
consent basis). If you see a receipt on a page, it means the site's own
consent flow already ran.

## 6. Store-listing posture (future)

Any Chrome Web Store submission would carry: this disclosure, a store privacy
policy, data-usage declarations (none collected), and per-site opt-in
documentation — per the MV3 minimal-permission requirements of the store
program policies. NOT submitted in v0.1.
