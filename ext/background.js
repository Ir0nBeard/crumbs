/**
 * Crumbs Journey Viewer — background service worker.
 *
 * MV3 minimal-permission posture (see ext/README.md):
 *   - only "storage" is required at install
 *   - host access + content script injection are OPTIONAL and granted per-site
 *     by an explicit user action in the popup ("Enable on this site")
 * No remote code, no eval, no background network calls.
 */

const STORAGE_KEYS = {
  enabledSites: "crumbs_enabled_sites",
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(STORAGE_KEYS.enabledSites, (got) => {
    if (!got[STORAGE_KEYS.enabledSites]) {
      chrome.storage.local.set({ [STORAGE_KEYS.enabledSites]: [] });
    }
  });
});

/**
 * Enable the viewer on `origin` (e.g. "https://shop.example").
 * Requests the optional host permission, then registers the content script.
 * All state lives in chrome.storage.local (per-extension, not shared).
 */
async function enableForSite(origin) {
  const { [STORAGE_KEYS.enabledSites]: enabled = [] } = await chrome.storage.local.get(
    STORAGE_KEYS.enabledSites
  );
  if (!enabled.includes(origin)) {
    await chrome.storage.local.set({ [STORAGE_KEYS.enabledSites]: [...enabled, origin] });
  }
  const granted = await chrome.permissions.request({ origins: [origin + "/*"] });
  if (!granted) {
    throw new Error("permission not granted by user");
  }
  if (!chrome.scripting) {
    // scripting permission not yet granted — request it on the same gesture
    const scriptingGranted = await chrome.permissions.request({ permissions: ["scripting"] });
    if (!scriptingGranted) throw new Error("scripting permission not granted");
  }
  const existing = await chrome.scripting.getRegisteredContentScripts();
  const scriptId = "crumbs_viewer_" + origin.replace(/[^a-z0-9]/gi, "_");
  if (!existing.some((s) => s.id === scriptId)) {
    await chrome.scripting.registerContentScripts([
      {
        id: scriptId,
        matches: [origin + "/*"],
        js: ["content.js"],
        runAt: "document_idle",
      },
    ]);
  }
  return true;
}

/** Disable the viewer for `origin`: unregister script + drop the permission. */
async function disableForSite(origin) {
  const { [STORAGE_KEYS.enabledSites]: enabled = [] } = await chrome.storage.local.get(
    STORAGE_KEYS.enabledSites
  );
  await chrome.storage.local.set({
    [STORAGE_KEYS.enabledSites]: enabled.filter((o) => o !== origin),
  });
  const scriptId = "crumbs_viewer_" + origin.replace(/[^a-z0-9]/gi, "_");
  try {
    await chrome.scripting.unregisterContentScripts({ ids: [scriptId] });
  } catch (e) {
    /* not registered — fine */
  }
  await chrome.permissions.remove({ origins: [origin + "/*"] });
  return true;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "crumbs_enable") {
    enableForSite(msg.origin)
      .then((ok) => sendResponse({ ok }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true; // async response
  }
  if (msg?.type === "crumbs_disable") {
    disableForSite(msg.origin)
      .then((ok) => sendResponse({ ok }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  if (msg?.type === "crumbs_status") {
    chrome.storage.local.get(STORAGE_KEYS.enabledSites, (got) => {
      sendResponse({ enabled: got[STORAGE_KEYS.enabledSites] || [] });
    });
    return true;
  }
  return false;
});
