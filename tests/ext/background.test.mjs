/**
 * MV3 grant-flow tests for ext/background.js (per-site opt-in).
 *
 * Covers the flow that a real browser drives through the popup: install
 * initialisation, enable (permission request -> content-script registration ->
 * state persistence), the denied-grant path (state must NOT be persisted
 * before the user approves), idempotent re-enable, and disable (state ->
 * unregister -> host permission removal -> scripting permission removal on the
 * last site).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createChromeMock } from "./chrome_mock.mjs";
import { loadScript, norm, tick } from "./helpers.mjs";

const ORIGIN = "https://shop.example";

function boot(opts = {}) {
  const mock = createChromeMock(opts);
  const ctx = loadScript("background.js", { chrome: mock.chrome });
  return { mock, ctx };
}

test("install seeds an empty enabledSites list", async () => {
  const { mock } = boot();
  mock.chrome.runtime._fireInstalled();
  await tick();
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), []);
});

test("enableForSite grants, registers one script, then persists the site", async () => {
  const { mock, ctx } = boot();
  mock.chrome.runtime._fireInstalled();
  await tick();

  const ok = await ctx.enableForSite(ORIGIN);
  assert.equal(ok, true);
  // host + scripting permission granted in one request
  assert.ok(mock._state.grantedOrigins.has(ORIGIN + "/*"));
  assert.ok(mock._state.grantedPermissions.has("scripting"));
  // content script registered for the origin only
  const scripts = mock._state.registeredScripts;
  assert.equal(scripts.length, 1);
  assert.equal(scripts[0].id, "crumbs_viewer_https___shop_example");
  assert.deepEqual(norm(scripts[0].matches), [ORIGIN + "/*"]);
  assert.deepEqual(norm(scripts[0].js), ["content.js"]);
  // site persisted only after the grant
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), [ORIGIN]);
});

test("enableForSite is idempotent: re-enable does not duplicate the script", async () => {
  const { mock, ctx } = boot();
  await ctx.enableForSite(ORIGIN);
  await ctx.enableForSite(ORIGIN);
  assert.equal(mock._state.registeredScripts.length, 1);
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), [ORIGIN]);
});

test("denied permission prompt leaves NO phantom-enabled state (regression)", async () => {
  const { mock, ctx } = boot({
    permissionDecider: () => false,
  });
  mock.chrome.runtime._fireInstalled();
  await tick();
  await assert.rejects(() => ctx.enableForSite(ORIGIN), /permission not granted/);
  // The bug this guards against: the origin used to be persisted BEFORE the
  // prompt, so a denial left the popup showing the site as enabled while no
  // host permission and no content script existed.
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), []);
  assert.equal(mock._state.registeredScripts.length, 0);
  assert.equal(mock._state.grantedPermissions.has("scripting"), false);
});

test("enableForSite re-registers cleanly after a denial on a later attempt", async () => {
  let grant = false;
  const { mock, ctx } = boot({ permissionDecider: () => grant });
  await assert.rejects(() => ctx.enableForSite(ORIGIN), /permission not granted/);
  grant = true;
  const ok = await ctx.enableForSite(ORIGIN);
  assert.equal(ok, true);
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), [ORIGIN]);
  assert.equal(mock._state.registeredScripts.length, 1);
});

test("disableForSite removes state, script, and host permission", async () => {
  const { mock, ctx } = boot();
  await ctx.enableForSite(ORIGIN);
  const ok = await ctx.disableForSite(ORIGIN);
  assert.equal(ok, true);
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), []);
  assert.equal(mock._state.registeredScripts.length, 0);
  assert.equal(mock._state.grantedOrigins.has(ORIGIN + "/*"), false);
  // last site gone -> scripting permission dropped (least privilege restored)
  assert.equal(mock._state.grantedPermissions.has("scripting"), false);
});

test("disableForSite keeps scripting permission while other sites stay enabled", async () => {
  const { mock, ctx } = boot();
  await ctx.enableForSite(ORIGIN);
  await ctx.enableForSite("https://second.example");
  assert.ok(mock._state.grantedPermissions.has("scripting"));
  await ctx.disableForSite(ORIGIN);
  assert.ok(mock._state.grantedPermissions.has("scripting"), "still in use by second site");
  assert.deepEqual(
    norm(await mock._state.localGet("crumbs_enabled_sites")),
    ["https://second.example"]
  );
  await ctx.disableForSite("https://second.example");
  assert.equal(mock._state.grantedPermissions.has("scripting"), false);
});

test("disableForSite on a never-enabled origin is a safe no-op", async () => {
  const { mock, ctx } = boot();
  const ok = await ctx.disableForSite(ORIGIN);
  assert.equal(ok, true);
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), []);
});

test("message routing: enable/disable/status over runtime.onMessage", async () => {
  const { mock, ctx } = boot();
  mock.chrome.runtime._fireInstalled();
  await tick();

  const status = await mock.chrome.runtime.sendMessage({ type: "crumbs_status" });
  assert.deepEqual(norm(status), { enabled: [] });

  const enable = await mock.chrome.runtime.sendMessage({
    type: "crumbs_enable",
    origin: ORIGIN,
  });
  assert.equal(enable.ok, true);

  const status2 = await mock.chrome.runtime.sendMessage({ type: "crumbs_status" });
  assert.deepEqual(norm(status2), { enabled: [ORIGIN] });

  const disable = await mock.chrome.runtime.sendMessage({
    type: "crumbs_disable",
    origin: ORIGIN,
  });
  assert.equal(disable.ok, true);
  assert.deepEqual(norm(await mock._state.localGet("crumbs_enabled_sites")), []);
});

test("denied enable over the message bus reports {ok:false, error} — no crash", async () => {
  const { mock } = boot({ permissionDecider: () => false });
  mock.chrome.runtime._fireInstalled();
  await tick();
  const resp = await mock.chrome.runtime.sendMessage({
    type: "crumbs_enable",
    origin: ORIGIN,
  });
  assert.equal(resp.ok, false);
  assert.match(resp.error, /permission not granted/);
});
