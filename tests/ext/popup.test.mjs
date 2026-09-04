/**
 * Popup behaviour tests (ext/popup/popup.js) driven through the same mocked
 * chrome as the background service worker — an end-to-end per-site opt-in
 * flow without a browser: popup click -> background grant/registration ->
 * popup state refresh -> verify call.
 *
 * The active tab is provided by the chrome mock, which is what the real
 * "activeTab" permission grants while the popup is open after a toolbar click.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createChromeMock } from "./chrome_mock.mjs";
import { createDomMock, POPUP_IDS } from "./dom_mock.mjs";
import { loadScript, norm, tick } from "./helpers.mjs";

const ORIGIN = "https://shop.example";
const TAB_URL = "https://shop.example/checkout?utm_source=agent";
const ACTIVE_TAB = { id: 7, url: TAB_URL, active: true, currentWindow: true };
const PAGE_STATE = {
  url: TAB_URL,
  origin: ORIGIN,
  mirrorCookie: "abc123",
  receipt: { rid: "rct_abc", jid: "j_1", aid: "a_1", exp: 1789000000 },
  agentSignals: { userAgentHits: [], webmcp: false, agentLike: false },
  hasWebmcpTool: false,
};

function boot({ permissionDecider, verifyApi, pageState = PAGE_STATE } = {}) {
  const fetchCalls = [];
  const fetchStub = async (url, init = {}) => {
    fetchCalls.push({ url, init });
    return { status: 200, ok: true };
  };
  const mock = createChromeMock({
    tabs: [ACTIVE_TAB],
    permissionDecider,
    onTabMessage: (tabId, msg) => {
      if (msg?.type === "crumbs_collect") {
        return Promise.resolve({ ok: true, state: pageState });
      }
      return Promise.resolve({ ok: false });
    },
  });
  if (verifyApi) {
    mock.chrome.storage.sync.set({ crumbs_verify_api: verifyApi });
  }
  // Load the service worker and the popup into the SAME chrome instance so
  // runtime.sendMessage from the popup reaches the background handlers. Fire
  // the install event (as a real browser would) so enabledSites is seeded.
  const bgCtx = loadScript("background.js", { chrome: mock.chrome });
  mock.chrome.runtime._fireInstalled();
  const dom = createDomMock(POPUP_IDS);
  const popupCtx = loadScript("popup/popup.js", {
    chrome: mock.chrome,
    document: dom.document,
    fetch: fetchStub,
  });
  return { mock, bgCtx, popupCtx, dom, fetchCalls };
}

test("initial render: origin shown, Enable offered, Verify disabled", async () => {
  const { dom } = boot();
  await tick(10);
  assert.equal(dom.element("siteOrigin").textContent, ORIGIN);
  assert.equal(dom.element("toggleSite").textContent, "Enable on this site");
  assert.equal(dom.element("verify").disabled, true);
  assert.equal(dom.element("mirror").textContent, "—");
});

test("grant flow: click Enable -> background grants + registers -> UI shows enabled state and unlocks Verify", async () => {
  const { mock, dom } = boot();
  await tick(10);
  dom.element("toggleSite").click();
  await tick(20);

  assert.equal(dom.element("toggleSite").textContent, "Disable on this site");
  assert.match(dom.element("siteHint").textContent, /Viewing enabled/);
  assert.equal(dom.element("mirror").textContent, "present (6 chars)");
  assert.equal(dom.element("receipt").textContent, "rct_abc (exp 1789000000)");
  assert.equal(dom.element("verify").disabled, false);
  // background state: one granted site, one registered content script
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), [ORIGIN]);
  assert.equal(mock._state.registeredScripts.length, 1);
  assert.ok(mock._state.grantedOrigins.has(ORIGIN + "/*"));
  assert.ok(mock._state.grantedPermissions.has("scripting"));
});

test("denied grant: hint shown and NO phantom-enabled state (regression)", async () => {
  const { mock, dom } = boot({ permissionDecider: () => false });
  await tick(10);
  dom.element("toggleSite").click();
  await tick(20);
  assert.match(dom.element("siteHint").textContent, /Permission not granted/);
  assert.equal(dom.element("toggleSite").textContent, "Enable on this site");
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), []);
  assert.equal(mock._state.registeredScripts.length, 0);
});

test("disable flow: click Disable -> state, script, and permissions all removed; Verify locked again", async () => {
  const { mock, dom } = boot();
  await tick(10);
  dom.element("toggleSite").click(); // enable
  await tick(20);
  dom.element("toggleSite").click(); // disable
  await tick(20);

  assert.equal(dom.element("toggleSite").textContent, "Enable on this site");
  assert.equal(dom.element("mirror").textContent, "—");
  assert.equal(dom.element("receipt").textContent, "—");
  assert.equal(dom.element("verify").disabled, true);
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), []);
  assert.equal(mock._state.registeredScripts.length, 0);
  assert.equal(mock._state.grantedPermissions.has("scripting"), false);
});

test("verify with a configured ledger POSTs ids-only receipt to /v1/verify", async () => {
  const { dom, fetchCalls } = boot({ verifyApi: "https://ledger.example" });
  await tick(10);
  dom.element("toggleSite").click(); // enable -> receipt present
  await tick(20);
  assert.equal(dom.element("verify").disabled, false);
  dom.element("verify").click();
  await tick(20);

  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, "https://ledger.example/v1/verify");
  assert.equal(fetchCalls[0].init.method, "POST");
  assert.equal(fetchCalls[0].init.headers["content-type"], "application/json");
  const body = JSON.parse(fetchCalls[0].init.body);
  assert.match(body.receipt, /rct_abc/);
  assert.match(dom.element("verifyResult").textContent, /ledger responded 200/);
});

test("verify with no configured ledger never fires a network call", async () => {
  const { dom, fetchCalls } = boot();
  await tick(10);
  dom.element("toggleSite").click();
  await tick(20);
  dom.element("verify").click();
  await tick(10);
  assert.equal(fetchCalls.length, 0);
  assert.match(dom.element("verifyResult").textContent, /no ledger configured/);
});

test("page without a receipt keeps Verify disabled even when viewing is enabled", async () => {
  const { dom } = boot({
    pageState: { ...PAGE_STATE, receipt: null, mirrorCookie: null },
  });
  await tick(10);
  dom.element("toggleSite").click();
  await tick(20);
  assert.equal(dom.element("toggleSite").textContent, "Disable on this site");
  assert.equal(dom.element("receipt").textContent, "none");
  assert.equal(dom.element("verify").disabled, true, "nothing to verify without a receipt");
});
