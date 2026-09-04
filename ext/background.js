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
 *
 * Requests the optional "scripting" permission and the per-site host
 * permission in ONE prompt (MV3 groups optional grants), then registers the
 * content script. The site is recorded in storage ONLY after the grant, so a
 * denied prompt can never leave the site listed as enabled while no script or
 * host permission exists. All state lives in chrome.storage.local.
 */
async function enableForSite(origin) {
  const granted = await chrome.permissions.request({
    permissions: ["scripting"],
    origins: [origin + "/*"],
  });
  if (!granted) {
    throw new Error("permission not granted by user");
  }
  const scriptId = "crumbs_viewer_" + origin.replace(/[^a-z0-9]/gi, "_");
  const existing = await chrome.scripting.getRegisteredContentScripts();
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
  const { [STORAGE_KEYS.enabledSites]: enabled = [] } = await chrome.storage.local.get(
    STORAGE_KEYS.enabledSites
  );
  if (!enabled.includes(origin)) {
    await chrome.storage.local.set({ [STORAGE_KEYS.enabledSites]: [...enabled, origin] });
  }
  return true;
}

/**
 * Disable the viewer for `origin`: unregister script + drop the permissions.
 * When the last enabled site is removed, the optional "scripting" permission
 * is dropped too — nothing else in the extension uses it, so least-privilege
 * is restored (the granted host permission is always removed per site).
 */
async function disableForSite(origin) {
  const { [STORAGE_KEYS.enabledSites]: enabled = [] } = await chrome.storage.local.get(
    STORAGE_KEYS.enabledSites
  );
  const remaining = enabled.filter((o) => o !== origin);
  await chrome.storage.local.set({ [STORAGE_KEYS.enabledSites]: remaining });
  const scriptId = "crumbs_viewer_" + origin.replace(/[^a-z0-9]/gi, "_");
  try {
    await chrome.scripting.unregisterContentScripts({ ids: [scriptId] });
  } catch (e) {
    /* not registered — fine */
  }
  await chrome.permissions.remove({ origins: [origin + "/*"] });
  if (remaining.length === 0) {
    try {
      await chrome.permissions.remove({ permissions: ["scripting"] });
    } catch (e) {
      /* not granted — fine */
    }
  }
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
