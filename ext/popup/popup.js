/**
 * Crumbs Journey Viewer — popup logic.
 *
 * Per-site opt-in flow: the popup requests optional host permissions for the
 * CURRENT tab's origin (a user gesture), the background registers the content
 * script, and "Refresh" pulls page state. "Verify" sends the receipt to the
 * configured ledger (user-triggered; the only network call the extension makes).
 *
 * The active tab's URL is read via `activeTab` (manifest permission) — granted
 * only while the popup is open after you click the extension icon.
 */
const DEFAULT_VERIFY_API = "https://api.crumbs.dev"; // placeholder — configurable later
let VERIFY_API = DEFAULT_VERIFY_API;
// v0.1 ships with the placeholder default. A future version (or an advanced
// user) can point at a real ledger by setting chrome.storage.sync
// "crumbs_verify_api" to the base URL, e.g. "https://api.example.com".
chrome.storage.sync.get("crumbs_verify_api", ({ crumbs_verify_api: v }) => {
  if (typeof v === "string" && v) VERIFY_API = v;
});

const $ = (id) => document.getElementById(id);

async function currentTabOrigin() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) return null;
  try {
    return new URL(tab.url).origin;
  } catch (e) {
    return null;
  }
}

async function isEnabled(origin) {
  const { crumbs_enabled_sites: enabled = [] } = await chrome.storage.local.get(
    "crumbs_enabled_sites"
  );
  return enabled.includes(origin);
}

async function refreshState(origin) {
  $("siteOrigin").textContent = origin || "—";
  const enabled = await isEnabled(origin);
  $("toggleSite").textContent = enabled ? "Disable on this site" : "Enable on this site";
  $("siteHint").textContent = enabled
    ? "Viewing enabled — the content script reads receipt state on this site only."
    : "Viewing is opt-in per site. Nothing is read before you enable it.";
  if (!enabled) {
    $("mirror").textContent = "—";
    $("receipt").textContent = "—";
    $("agents").textContent = "—";
    $("webmcp").textContent = "—";
    $("verify").disabled = true;
    return;
  }
  try {
    const resp = await chrome.tabs.sendMessage(
      (await chrome.tabs.query({ active: true, currentWindow: true }))[0].id,
      { type: "crumbs_collect" }
    );
    if (resp?.ok) {
      const s = resp.state;
      $("mirror").textContent = s.mirrorCookie ? "present (" + s.mirrorCookie.length + " chars)" : "none";
      $("receipt").textContent = s.receipt ? s.receipt.rid + " (exp " + s.receipt.exp + ")" : "none";
      $("agents").textContent = s.agentSignals.agentLike
        ? (s.agentSignals.userAgentHits.join(", ") || "UA heuristic") + (s.agentSignals.webmcp ? " + WebMCP" : "")
        : "no agent signals detected";
      $("webmcp").textContent = s.hasWebmcpTool ? "crumbs_conversion registered" : "not present";
      $("verify").disabled = !s.receipt;
    } else {
      $("receipt").textContent = "content script not ready — reload the page";
    }
  } catch (e) {
    $("receipt").textContent = "content script not injected — reload the page";
  }
}

$("toggleSite").addEventListener("click", async () => {
  const origin = await currentTabOrigin();
  if (!origin) return;
  const enabled = await isEnabled(origin);
  const ok = await chrome.runtime.sendMessage({
    type: enabled ? "crumbs_disable" : "crumbs_enable",
    origin,
  });
  if (ok?.ok) await refreshState(origin);
  else $("siteHint").textContent = "Permission not granted — enable from the popup on the site you want to view.";
});

$("refresh").addEventListener("click", async () => {
  await refreshState(await currentTabOrigin());
});

$("verify").addEventListener("click", async () => {
  $("verifyResult").textContent = "verifying…";
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const resp = await chrome.tabs.sendMessage(tab.id, { type: "crumbs_collect" });
  const receipt = resp?.ok && resp.state.receipt;
  if (!receipt) {
    $("verifyResult").textContent = "no receipt on this page";
    return;
  }
  try {
    const wire = JSON.stringify(resp.state.receipt); // NOTE: popup stores only ids;
    // a full verify needs the signed wire — see PRIVACY.md §4 (v0.1 viewer limitation)
    // POST /v1/verify — the payload travels in the BODY, never a query string
    // (bearer-safe pattern, P3 D-M6 / N6).
    const result = await fetch(VERIFY_API + "/v1/verify", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ receipt: wire }),
    });
    $("verifyResult").textContent = "ledger responded " + result.status + " (ids-only verify — see PRIVACY.md)";
  } catch (e) {
    $("verifyResult").textContent = "verify failed: " + e.message;
  }
});

(async () => {
  await refreshState(await currentTabOrigin());
})();
